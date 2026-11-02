using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Godot;

namespace OverCompressingUI.UI;

/// <summary>
/// Hooks for checking the window without a hand on the mouse (used while developing, harmless otherwise). User
/// arguments after "++" on the command line:
///   --shot=&lt;png&gt;        save a screenshot of the window after it has settled, then quit
///   --wait=&lt;seconds&gt;    how long to wait before the screenshot (default 2.5)
///   --view=list|base|graph  the pack view to switch to first
///   --select=&lt;n&gt;        select the first n rows first
///   --dialog=&lt;name&gt;     open a dialog first: pack, extract, export, rebase, rename, settings, remove, damage, damage-here,
///                        turn (+ Turn folder into pack), newpack (+ New pack folder)
///   --screen=WxH         pretend the display is this small (the window is fitted to it); --scale=&lt;s&gt; forces the scale
///   --tool=&lt;path&gt;       run this llm_archive.py instead of the one found (a test copy that fakes "does not rebuild here")
///   --answer=yes,no,...  the next dialogs answer themselves, in this order (a question of a job, say); the rest stay
///   --script=&lt;file&gt;     steps, one per line, run one after another before the screenshot: "cmd|arg|arg" (a job of the
///                        tool, waited for), "answer:yes,no" (queued for the dialogs to come), "cancel:&lt;s&gt;" (the next
///                        job is cancelled after s seconds), "flag:on|off" (an OFF file beside the --tool copy, read by
///                        it), "shot:&lt;png&gt;", "wait:&lt;s&gt;", "go:&lt;place&gt;", "dialog:&lt;name&gt;" (as --dialog, not waited
///                        for), "type:&lt;text&gt;" (into the pack name field beside the path), "name:&lt;text&gt;" (typed and
///                        submitted, as Enter would), "esc" (the field back to the pack's name), "info" (the field, the
///                        buttons on, the title and the status line, printed), "bg:cmd|arg" (jobs not waited for),
///                        "until-dialog" (wait for one to open), "select:&lt;n&gt;", "fill:a|b" (the open dialog's text
///                        fields), "accept" / "dismiss" (the open dialog), "idle" (wait until nothing runs); "&lt;br&gt;" in
///                        a text is a line break; "#" starts a comment
///   --stay               do not quit after the screenshot
/// </summary>
public static class DevHooks
{
    public static Dictionary<string, string> Args()
    {
        var d = new Dictionary<string, string>();
        foreach (var a in OS.GetCmdlineUserArgs())
        {
            if (!a.StartsWith("--"))
                continue;
            var eq = a.IndexOf('=');
            if (eq < 0)
                d[a[2..]] = "";
            else
                d[a[2..eq]] = a[(eq + 1)..];
        }
        return d;
    }

    /// <summary>Write icon.svg as a Windows .ico (PNG-compressed entries at the usual sizes).</summary>
    public static void WriteIco(string path)
    {
        var svg = Godot.FileAccess.GetFileAsString("res://icon.svg");
        var sizes = new[] { 256, 128, 64, 48, 32, 16 };
        var pngs = new List<byte[]>();
        foreach (var s in sizes)
        {
            var img = new Image();
            img.LoadSvgFromString(svg, s / 128f);
            pngs.Add(img.SavePngToBuffer());
        }
        using var f = new BinaryWriter(File.Create(path));
        f.Write((ushort)0);
        f.Write((ushort)1);
        f.Write((ushort)sizes.Length);
        int offset = 6 + 16 * sizes.Length;
        for (int i = 0; i < sizes.Length; i++)
        {
            f.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
            f.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
            f.Write((byte)0);
            f.Write((byte)0);
            f.Write((ushort)1);
            f.Write((ushort)32);
            f.Write(pngs[i].Length);
            f.Write(offset);
            offset += pngs[i].Length;
        }
        foreach (var png in pngs)
            f.Write(png);
        GD.Print($"icon: {path}");
    }

    public static async Task Run(MainWindow window)
    {
        var args = Args();
        if (args.TryGetValue("ico", out var ico))
        {
            WriteIco(ico);
            window.GetTree().Quit();
            return;
        }
        if (!args.ContainsKey("shot"))
            return;
        if (args.TryGetValue("answer", out var answers))
            Answers(answers);
        double wait = args.TryGetValue("wait", out var w) && double.TryParse(w, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var ws) ? ws : 2.5;
        await window.ToSignal(window.GetTree().CreateTimer(wait), SceneTreeTimer.SignalName.Timeout);
        if (args.TryGetValue("view", out var view))
            window.DevSetView(view);
        if (args.TryGetValue("select", out var sel) && int.TryParse(sel, out var n))
            window.DevSelectFirst(n);
        if (args.TryGetValue("job", out var job))  // "--job=verify|<pack>" : the tool's arguments, | separated
            window.DevStartJob(job.Split('|'));
        if (args.TryGetValue("script", out var script))
            await RunScript(window, script, args.GetValueOrDefault("tool", ""));
        if (args.TryGetValue("go", out var go))  // go to another place after the wait (the board slides in)
        {
            window.DevGo(go);
            await window.ToSignal(window.GetTree().CreateTimer(0.35), SceneTreeTimer.SignalName.Timeout);
        }
        if (args.ContainsKey("wiggle"))  // move the mouse in a loop so the trail shows
        {
            var c = window.Size / 2;
            for (int i = 0; i < 40; i++)
            {
                float ang = i / 40f * Mathf.Tau;
                var pos = c + new Vector2(Mathf.Cos(ang) * 160, Mathf.Sin(ang * 2) * 70);
                DisplayServer.WarpMouse((Vector2I)(pos * Ui.Scale));  // the real cursor: the trail follows real motion only
                await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
            }
            GD.Print("trail: " + window.DevTrailInfo());
            var img0 = window.GetViewport().GetTexture().GetImage();  // the frame just rendered, trail and all
            img0.SavePng(args["shot"].Replace(".png", "-live.png"));
        }
        if (args.ContainsKey("board"))  // the board alone, bright, for checking the routing
            window.DevBoardOnly();
        if (args.ContainsKey("legend"))
            window.DevLegend();
        if (args.ContainsKey("menu"))
            window.DevMenu();
        if (args.TryGetValue("trash", out var trash))
            window.DevTrash(trash);
        if (args.TryGetValue("cancel", out var cancel) && double.TryParse(cancel, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var cs))
            _ = CancelLater(window, cs);
        if (args.TryGetValue("dialog", out var dialog))
        {
            window.DevOpenDialog(dialog);
            await window.ToSignal(window.GetTree().CreateTimer(0.8), SceneTreeTimer.SignalName.Timeout);
        }
        else
            await window.ToSignal(window.GetTree().CreateTimer(0.4), SceneTreeTimer.SignalName.Timeout);
        await Shot(window, args["shot"]);
        if (args.TryGetValue("shot2", out var shot2))
        {
            double wait2 = args.TryGetValue("wait2", out var w2) && double.TryParse(w2, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var ws2) ? ws2 : 5;
            await window.ToSignal(window.GetTree().CreateTimer(wait2), SceneTreeTimer.SignalName.Timeout);
            await Shot(window, shot2);
        }
        if (args.ContainsKey("close"))
        {
            window.DevClose();  // as the window's close button would: the outro, then settings saved
            if (args.TryGetValue("outro-shot", out var outroShot))
            {
                await window.ToSignal(window.GetTree().CreateTimer(0.3), SceneTreeTimer.SignalName.Timeout);
                var img1 = window.GetViewport().GetTexture().GetImage();
                img1.SavePng(outroShot);
                GD.Print($"screenshot: {outroShot}");
            }
        }
        else if (!args.ContainsKey("stay"))
        {
            Input.SetCustomMouseCursor(null, Input.CursorShape.Arrow);
            Input.SetCustomMouseCursor(null, Input.CursorShape.Ibeam);
            window.GetTree().Quit();
        }
    }

    private static readonly HashSet<string> Ops = new()
    {
        "answer", "cancel", "flag", "shot", "wait", "go", "dialog", "type", "name", "esc", "info", "bg", "idle", "until-dialog",
        "select", "pick", "fill", "accept", "dismiss", "rescale",
    };

    private static async Task RunScript(MainWindow window, string path, string tool)
    {
        foreach (var raw in File.ReadAllLines(path))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith("#"))
                continue;
            GD.Print($"script: {line}");
            var colon = line.IndexOf(':');
            var head = colon > 0 ? line[..colon] : line;
            var (op, arg) = Ops.Contains(head) ? (head, colon > 0 ? line[(colon + 1)..].Trim() : "") : ("", line);
            arg = arg.Replace("<br>", "\n");  // a line break in a text (a backslash escape would break paths)
            switch (op)
            {
                case "bg":  // jobs started and not waited for (then "until-dialog", "select", "info", "idle" look at them running)
                    window.DevStartJobs(arg.Split("&&").Select(j => j.Trim().Split('|')).ToList());
                    break;
                case "idle":
                    await Idle(window);
                    break;
                case "until-dialog":
                    while (OcDialog.Open.Count == 0)
                        await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
                    await window.ToSignal(window.GetTree().CreateTimer(0.3), SceneTreeTimer.SignalName.Timeout);
                    break;
                case "select":
                    window.DevSelectFirst(int.Parse(arg));
                    break;
                case "rescale":  // pretend the display's scale changed (as moving to another display would)
                    window.DevRescale(float.Parse(arg, System.Globalization.CultureInfo.InvariantCulture));
                    await window.ToSignal(window.GetTree().CreateTimer(1.5), SceneTreeTimer.SignalName.Timeout);
                    break;
                case "pick":  // select the row named
                    window.DevPick(arg);
                    break;
                case "fill":  // the open dialog's text fields, top to bottom, "|" apart ("" leaves one as it is)
                {
                    var edits = OcDialog.Open.Last().DevEdits();
                    var texts = arg.Split('|');
                    for (int i = 0; i < texts.Length && i < edits.Count; i++)
                        if (texts[i].Length > 0)
                            edits[i].Text = texts[i];
                    await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
                    break;
                }
                case "accept":
                    OcDialog.Open.Last().DevAccept();
                    await window.ToSignal(window.GetTree().CreateTimer(0.5), SceneTreeTimer.SignalName.Timeout);
                    break;
                case "dismiss":
                    OcDialog.Open.Last().DevDismiss();
                    await window.ToSignal(window.GetTree().CreateTimer(0.3), SceneTreeTimer.SignalName.Timeout);
                    break;
                case "answer":
                    Answers(arg);
                    break;
                case "dialog":
                    window.DevOpenDialog(arg);
                    await window.ToSignal(window.GetTree().CreateTimer(0.8), SceneTreeTimer.SignalName.Timeout);
                    break;
                case "type":
                    window.DevName(arg, submit: false);
                    await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
                    break;
                case "name":
                    window.DevName(arg, submit: true);
                    await window.ToSignal(window.GetTree().CreateTimer(0.5), SceneTreeTimer.SignalName.Timeout);
                    await Idle(window);
                    break;
                case "esc":
                    window.DevNameEscape();
                    await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
                    break;
                case "info":
                    GD.Print("info: " + window.DevNameInfo());
                    GD.Print("window: " + window.DevWindowInfo());
                    break;
                case "cancel":
                    _ = CancelLater(window, double.Parse(arg, System.Globalization.CultureInfo.InvariantCulture));
                    break;
                case "flag":
                {
                    var flag = Path.Combine(Path.GetDirectoryName(tool) ?? ".", "OFF");
                    if (arg == "on")
                        File.WriteAllText(flag, "");
                    else if (File.Exists(flag))
                        File.Delete(flag);
                    break;
                }
                case "shot":
                    await Shot(window, arg);
                    break;
                case "wait":
                    await window.ToSignal(window.GetTree().CreateTimer(double.Parse(arg, System.Globalization.CultureInfo.InvariantCulture)), SceneTreeTimer.SignalName.Timeout);
                    break;
                case "go":
                    window.DevGo(arg);
                    await Idle(window);
                    break;
                default:  // one job, or several in one batch: "cmd|arg && cmd|arg"
                    window.DevStartJobs(arg.Split("&&").Select(j => j.Trim().Split('|')).ToList());
                    await window.ToSignal(window.GetTree().CreateTimer(0.5), SceneTreeTimer.SignalName.Timeout);
                    await Idle(window);
                    break;
            }
        }
    }

    /// <summary>"yes,no@shot.png,...": the answers queued for the dialogs to come, each with a screenshot to take first.</summary>
    private static void Answers(string text)
    {
        foreach (var a in text.Split(',', StringSplitOptions.RemoveEmptyEntries))
        {
            var at = a.IndexOf('@');
            var word = (at >= 0 ? a[..at] : a).Trim().ToLowerInvariant();
            OcDialog.DevAnswers.Enqueue((word is "yes" or "ok" or "y", at >= 0 ? a[(at + 1)..].Trim() : null));
        }
    }

    /// <summary>Wait until nothing runs and no dialog is open, and that has held for a second (a report opens a moment
    /// after a job ends).</summary>
    private static async Task Idle(MainWindow window)
    {
        double quiet = 0;
        while (quiet < 1.0)
        {
            await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
            quiet = window.DevIdle ? quiet + window.GetProcessDeltaTime() : 0;
        }
    }

    private static async Task CancelLater(MainWindow window, double seconds)
    {
        await window.ToSignal(window.GetTree().CreateTimer(seconds), SceneTreeTimer.SignalName.Timeout);
        window.DevCancel();
    }

    private static async Task Shot(MainWindow window, string path)
    {
        await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
        await window.ToSignal(window.GetTree(), SceneTree.SignalName.ProcessFrame);
        var img = window.GetViewport().GetTexture().GetImage();
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        img.SavePng(path);
        GD.Print($"screenshot: {path} ({img.GetWidth()}x{img.GetHeight()})");
    }
}
