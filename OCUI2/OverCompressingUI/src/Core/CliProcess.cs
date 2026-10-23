using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace OverCompressingUI.Core;

/// <summary>
/// One run of llm_archive.py in a child process, its output read line by line on threads of its own and queued for
/// the main thread. The child runs the same runner as the Tkinter window: when its stdin closes (Cancel, or this
/// window going away) it stops as on Ctrl+C, so a pack or restore still removes what it had begun. On Windows the
/// pipe is polled (PeekNamedPipe), not read: a read left pending on it would stall every process started meanwhile
/// (the ans16 workers) as it starts.
/// </summary>
public sealed class CliProcess
{
    // Verbatim from llm_archive_gui.py (RUNNER), so that both windows cancel the tool the same way.
    public const string Runner = """
import os, sys, threading, time, _thread
if sys.platform != "win32":
    os.nice(10)
sys.path.insert(0, sys.argv[1])
import llm_archive as la
def cancel_on_eof():
    if sys.platform == "win32":
        import msvcrt, _winapi
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        while True:
            try:
                _winapi.PeekNamedPipe(handle, 0)
            except OSError:
                break
            time.sleep(0.25)
    else:
        while os.read(sys.stdin.fileno(), 4096):
            pass
    _thread.interrupt_main()
threading.Thread(target=cancel_on_eof, daemon=True).start()
sys.argv = ["llm_archive.py", *sys.argv[2:]]
try:
    la.main()
except KeyboardInterrupt:
    sys.exit("cancelled")
""";

    public readonly ConcurrentQueue<ToolEvent?> Lines = new();  // null: the process ended
    public Process? Process { get; private set; }
    public int ExitCode { get; private set; } = -1;
    public bool Started => Process is not null;
    public bool Ended { get; private set; }
    private int _open = 2;

    public string Python { get; }
    public string Tool { get; }

    public CliProcess(string python, string tool)
    {
        Python = python;
        Tool = tool;
    }

    /// <summary>The command line as people would type it (for the log).</summary>
    public static string Shown(IEnumerable<string> argv)
    {
        var parts = new List<string>();
        foreach (var a in argv)
            parts.Add(a.Length > 0 && !a.Any(ch => char.IsWhiteSpace(ch) || ch == '"') ? a : "\"" + a.Replace("\"", "\\\"") + "\"");
        return string.Join(" ", parts);
    }

    /// <summary>Start the tool with `argv` (the command and its arguments; --json and --progress are added).</summary>
    public void Start(IEnumerable<string> argv, bool progress = true, bool belowNormal = true)
    {
        var toolsDir = Path.GetDirectoryName(Path.GetFullPath(Tool))!;
        var psi = new ProcessStartInfo(Python)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
            WorkingDirectory = toolsDir,
        };
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.ArgumentList.Add("-I");
        psi.ArgumentList.Add("-X");
        psi.ArgumentList.Add("utf8");
        psi.ArgumentList.Add("-u");
        psi.ArgumentList.Add("-c");
        psi.ArgumentList.Add(Runner);
        psi.ArgumentList.Add(toolsDir);
        foreach (var a in argv)
            psi.ArgumentList.Add(a);
        psi.ArgumentList.Add("--json");
        psi.ArgumentList.Add(progress ? "--progress" : "--no-progress");
        var p = new Process { StartInfo = psi, EnableRaisingEvents = false };
        p.Start();
        if (belowNormal)
        {
            try { p.PriorityClass = ProcessPriorityClass.BelowNormal; } catch (Exception) { }
        }
        Process = p;
        new Thread(() => Read(p.StandardOutput, false)) { IsBackground = true, Name = "oc-stdout" }.Start();
        new Thread(() => Read(p.StandardError, true)) { IsBackground = true, Name = "oc-stderr" }.Start();
    }

    private void Read(StreamReader reader, bool stderr)
    {
        try
        {
            string? line;
            while ((line = reader.ReadLine()) is not null)
                Lines.Enqueue(ToolEvent.Parse(line, stderr));
        }
        catch (Exception) { }
        if (Interlocked.Decrement(ref _open) == 0)
        {
            try { Process!.WaitForExit(); ExitCode = Process.ExitCode; } catch (Exception) { ExitCode = -1; }
            Ended = true;
            Lines.Enqueue(null);
        }
    }

    /// <summary>Ask the tool to stop as Ctrl+C would: the runner stops when its stdin closes.</summary>
    public void Cancel()
    {
        try { Process?.StandardInput.Close(); } catch (Exception) { }
    }

    /// <summary>Stop now, with its worker processes.</summary>
    public void Kill()
    {
        var p = Process;
        if (p is null)
            return;
        try
        {
            if (OperatingSystem.IsWindows())
            {
                var k = Process.Start(new ProcessStartInfo("taskkill", $"/T /F /PID {p.Id}") { CreateNoWindow = true, UseShellExecute = false });
                k?.WaitForExit(5000);
            }
            else
                p.Kill(true);
        }
        catch (Exception) { }
    }

    /// <summary>Run the tool to its end (for reading: list, remove --dry-run, --version) and return every line.</summary>
    public static async Task<(int code, List<ToolEvent> events)> RunAsync(string python, string tool, IEnumerable<string> argv,
        CancellationToken ct = default, bool rawArgs = false)
    {
        var proc = new CliProcess(python, tool);
        if (rawArgs)
            proc.StartRaw(argv);
        else
            proc.Start(argv, progress: false, belowNormal: false);
        var events = new List<ToolEvent>();
        await Task.Run(() =>
        {
            while (true)
            {
                if (proc.Lines.TryDequeue(out var e))
                {
                    if (e is null)
                        break;
                    events.Add(e);
                }
                else if (ct.IsCancellationRequested)
                {
                    proc.Kill();
                    break;
                }
                else
                    Thread.Sleep(10);
            }
        }, CancellationToken.None);
        return (proc.ExitCode, events);
    }

    /// <summary>Start the script itself with these arguments (e.g. --version), without the runner or --json.</summary>
    public void StartRaw(IEnumerable<string> argv)
    {
        var psi = new ProcessStartInfo(Python)
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true,
            RedirectStandardError = true, StandardOutputEncoding = new UTF8Encoding(false), StandardErrorEncoding = new UTF8Encoding(false),
            WorkingDirectory = Path.GetDirectoryName(Path.GetFullPath(Tool)),
        };
        psi.ArgumentList.Add("-I");
        psi.ArgumentList.Add("-X");
        psi.ArgumentList.Add("utf8");
        psi.ArgumentList.Add(Tool);
        foreach (var a in argv)
            psi.ArgumentList.Add(a);
        var p = new Process { StartInfo = psi };
        p.Start();
        Process = p;
        new Thread(() => Read(p.StandardOutput, false)) { IsBackground = true }.Start();
        new Thread(() => Read(p.StandardError, true)) { IsBackground = true }.Start();
    }
}
