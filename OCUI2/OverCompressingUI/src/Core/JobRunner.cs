using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace OverCompressingUI.Core;

/// <summary>One run of the tool: its arguments, the title the busy window shows, and (optionally) where in argv the
/// model names sit, so a long list can go into a list file.</summary>
public sealed class Job
{
    public List<string> Argv = new();
    public string Title = "";
    public List<string> Models = new();
    public int ModelsAt;
    public Func<Job, ToolEvent?, List<string>?>? Retry;  // the arguments to run a failed job again with (given its error event), or null

    public Job(IEnumerable<string> argv, string title)
    {
        Argv = argv.ToList();
        Title = title;
    }
}

/// <summary>Runs jobs one after another in child processes, stopping at the first failure, and tells the window what
/// happens through callbacks (all on the main thread, from Poll).</summary>
public sealed class JobRunner
{
    public const int ArgsLimit = 8000;  // characters: past it, models and files go in list files; Windows: 32,767

    public string Python { get; }
    public string Tool { get; }
    public JobRunner(string python, string tool)
    {
        Python = python;
        Tool = tool;
    }

    public bool Running => _proc is not null;
    public bool Cancelling { get; private set; }
    public Job? Current { get; private set; }
    public int JobNumber { get; private set; }
    public int JobTotal { get; private set; }
    public ToolEvent? Progress { get; private set; }
    public DateTime StartedAt { get; private set; }
    public List<ToolEvent> Events { get; } = new();  // the hints and summaries of the job running (or that ran last)
    public DamageReport? Damage { get; private set; }
    public ToolEvent? Error { get; private set; }  // the error event that ended the job, with its code
    public StringBuilder Output { get; } = new();

    public Action<string, string?>? OnLog;  // text, tag ("command" or null)
    public Action<string>? OnLine;  // the last line printed (for the busy window and the status line)
    public Action<ToolEvent>? OnProgress;
    public Action<Job>? OnJobStarted;
    public Action<bool, Job?, int, string, DamageReport?>? OnFinished;  // ok, the job that failed, its exit code, its output, damage
    public Action? OnCancelled;

    private readonly Queue<Job> _jobs = new();
    private CliProcess? _proc;
    private readonly List<string> _listFiles = new();
    private Action? _done;

    public void Start(IEnumerable<Job> jobs, Action done)
    {
        _jobs.Clear();
        foreach (var j in jobs)
            _jobs.Enqueue(j);
        _done = done;
        JobNumber = 0;
        JobTotal = _jobs.Count;
        Cancelling = false;
        NextJob();
    }

    private void NextJob()
    {
        if (_jobs.Count == 0)
        {
            Finish(true, null, 0, "", null);
            return;
        }
        Current = _jobs.Dequeue();
        Output.Clear();
        Events.Clear();
        Damage = null;
        Error = null;
        Progress = null;
        JobNumber++;
        StartedAt = DateTime.Now;
        var (argv, listed) = Command(Current);
        OnLog?.Invoke($"\n> llm_archive.py {CliProcess.Shown(argv)}" + (listed > 0 ? T.Get("listed", ("count", listed)) : "") + "\n", "command");
        OnJobStarted?.Invoke(Current);
        _proc = new CliProcess(Python, Tool);
        try
        {
            _proc.Start(argv);
        }
        catch (Exception e)
        {
            _proc = null;
            OnLog?.Invoke($"cannot start {Python}: {e.Message}\n", null);
            Finish(false, Current, -1, e.Message, null);
        }
    }

    /// <summary>A job's command line, and how many names went into list files: when the line would pass ArgsLimit,
    /// its models and files (--file=) go into list files of their own, deleted when the job ends, unless a name
    /// holds a line break.</summary>
    private (List<string> argv, int listed) Command(Job job)
    {
        var argv = job.Argv;
        if (CliProcess.Shown(argv).Length <= ArgsLimit)
            return (argv, 0);
        var models = job.Models;
        int at = job.ModelsAt;
        if (at + models.Count > argv.Count || !argv.Skip(at).Take(models.Count).SequenceEqual(models))
            models = new List<string>();
        var rest = models.Count > 0 ? argv.Take(at).Concat(argv.Skip(at + models.Count)).ToList() : argv.ToList();
        var files = rest.Where(a => a.StartsWith("--file=")).Select(a => a["--file=".Length..]).ToList();
        if (models.Concat(files).Any(n => n.Contains('\n') || n.Contains('\r')))
            return (argv, 0);
        rest = rest.Where(a => !a.StartsWith("--file=")).ToList();
        foreach (var (option, names) in new[] { ("--models-from", models), ("--files-from", files) })
        {
            if (names.Count == 0)
                continue;
            var path = Path.Combine(Path.GetTempPath(), $"oc-list-{Guid.NewGuid():N}.txt");
            File.WriteAllText(path, string.Concat(names.Select(n => n + "\n")), new UTF8Encoding(false));
            _listFiles.Add(path);
            rest.Add(option);
            rest.Add(path);
        }
        return (rest, models.Count + files.Count);
    }

    /// <summary>Called by the window every frame: hands the lines the process printed to the window.</summary>
    public void Poll()
    {
        var proc = _proc;
        if (proc is null)
            return;
        while (proc.Lines.TryDequeue(out var e))
        {
            if (e is null)
            {
                JobEnded(proc);
                return;
            }
            if (e.Event == "damage" && e.Fields is not null)
                Damage = DamageReport.From(e);
            if (e.Event == "error")
                Error = e;
            if (e.Event is "hint" or "summary")
                Events.Add(e);
            if (e.Event == "progress")
            {
                Progress = e;
                OnProgress?.Invoke(e);
                continue;
            }
            var text = e.Text;
            Output.Append(text).Append('\n');
            OnLog?.Invoke(text + "\n", null);
            if (text.Trim().Length > 0 && !Cancelling)
                OnLine?.Invoke(text.Trim().Split('\n')[0]);
        }
    }

    private void JobEnded(CliProcess proc)
    {
        proc.Cancel();
        foreach (var f in _listFiles)
            try { File.Delete(f); } catch (Exception) { }
        _listFiles.Clear();
        _proc = null;
        int code = proc.ExitCode;
        var output = Output.ToString();
        if (Cancelling)
        {
            OnLog?.Invoke(T.Get("cancelled") + "\n", null);
            _jobs.Clear();
            Cancelling = false;
            OnCancelled?.Invoke();
            return;
        }
        if (code != 0)
        {
            var retry = Current?.Retry?.Invoke(Current, Error);
            if (retry is null)
            {
                Finish(false, Current, code, output, Damage);
                return;
            }
            var again = new Job(retry, Current!.Title) { Models = Current.Models, ModelsAt = Current.ModelsAt, Retry = Current.Retry };
            var rest = _jobs.ToList();
            _jobs.Clear();
            _jobs.Enqueue(again);
            foreach (var j in rest)
                _jobs.Enqueue(j);
            JobNumber--;
        }
        NextJob();
    }

    private void Finish(bool ok, Job? failed, int code, string output, DamageReport? damage)
    {
        _jobs.Clear();
        Cancelling = false;
        var done = _done;
        OnFinished?.Invoke(ok, failed, code, output, damage);
        if (ok)
            done?.Invoke();
    }

    /// <summary>Cancel after the current step (the runner stops as on Ctrl+C); the second time, stop now.</summary>
    public void Cancel()
    {
        if (_proc is null)
            return;
        if (!Cancelling)
        {
            Cancelling = true;
            _jobs.Clear();
            _proc.Cancel();
        }
    }

    public void Kill()
    {
        _proc?.Kill();
    }

    /// <summary>The time run so far and how far the step is, for the busy window.</summary>
    public string SpinnerText(int step)
    {
        const string spinner = "◐◓◑◒";
        var seconds = (int)(DateTime.Now - StartedAt).TotalSeconds;
        var text = $"{spinner[step % spinner.Length]}   {seconds / 60}:{seconds % 60:D2}";
        var p = Progress;
        if (p is not null && p.Long("total") is > 0)
        {
            long done = p.Long("done") ?? 0, total = p.Long("total")!.Value;
            text += $"   ·   {Math.Min((double)done / total, 1):P0}   ·   {T.SizeText(Math.Min(done, total))} / {T.SizeText(total)}";
        }
        return text;
    }
}
