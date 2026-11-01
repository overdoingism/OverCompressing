using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace OverCompressingUI.Core;

/// <summary>One run of the tool: its arguments, the title the busy window shows, and (optionally) where in argv the
/// model names sit, so a long list can go into a list file.</summary>
public sealed class Job
{
    public List<string> Argv = new();
    public string Title = "";
    public List<string> Models = new();
    public int ModelsAt;
    public Func<Job, ToolEvent?, Task<List<string>?>>? Retry;  // the arguments to run a failed job again with (given its error event, asking the user), or null

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
    public int ExactRegions { get; private set; }  // "exact" events of the job running: regions worked out the slow way
    public StringBuilder Output { get; } = new();
    public List<string> NotExact { get; } = new();  // the notes beside files written that are not the originals (restore --accept-inexact), this batch

    public Action<string, string?>? OnLog;  // text, tag ("command" or null)
    public Action<string>? OnLine;  // the last line printed (for the busy window and the status line)
    public Action<ToolEvent>? OnProgress;
    public Action<Job>? OnJobStarted;
    public Action<int>? OnExact;  // a region is being worked out exactly (slow): how many so far in this job
    public Action<Job>? OnJobDone;  // a job that ran through (exit 0, or an inexact restore the user asked for): the next one follows
    public Action<bool, Job?, int, string, DamageReport?>? OnFinished;  // ok, the job that failed, its exit code, its output, damage; the window runs its `done` on ok
    public Action? OnCancelled;

    private readonly Queue<Job> _jobs = new();
    private CliProcess? _proc;
    private readonly List<string> _listFiles = new();

    public void Start(IEnumerable<Job> jobs)
    {
        _jobs.Clear();
        foreach (var j in jobs)
            _jobs.Enqueue(j);
        NotExact.Clear();
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
        ExactRegions = 0;
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
            if (e.Event == "exact")
                OnExact?.Invoke(++ExactRegions);
            if (e.Event == "inexact" && Current is { } cur && cur.Argv.Count > 3)  // restore: the note's path in the destination
                NotExact.Add(Path.Combine(new[] { cur.Argv[3] }.Concat((e.Str("note") ?? "").Split('/')).ToArray()));
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
        var job = Current!;
        if (code != 0 && !(Error?.Str("code") == "inexact" && job.Argv.Contains("--accept-inexact")))  // the latter the user
        {   // asked for: it did what it could, and what follows runs (the window says which files are not the originals)
            _ = Retry(job, code, output, Damage);
            return;
        }
        OnJobDone?.Invoke(job);
        NextJob();
    }

    /// <summary>A failed job's second chance: its Retry hook asks the user (a question, on the main thread) and gives
    /// the arguments to run it again with; the job then goes back to the front of the queue, what follows it kept.</summary>
    private async Task Retry(Job job, int code, string output, DamageReport? damage)
    {
        var retry = job.Retry is null ? null : await job.Retry(job, Error);
        if (retry is null)
        {
            Finish(false, job, code, output, damage);
            return;
        }
        var again = new Job(retry, job.Title) { Models = job.Models, ModelsAt = job.ModelsAt, Retry = job.Retry };
        var rest = _jobs.ToList();
        _jobs.Clear();
        _jobs.Enqueue(again);
        foreach (var j in rest)
            _jobs.Enqueue(j);
        JobNumber--;  // the same job, once more
        NextJob();
    }

    private void Finish(bool ok, Job? failed, int code, string output, DamageReport? damage)
    {
        _jobs.Clear();
        Cancelling = false;
        OnFinished?.Invoke(ok, failed, code, output, damage);
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
