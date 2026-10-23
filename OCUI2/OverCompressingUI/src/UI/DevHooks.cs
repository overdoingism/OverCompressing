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
///   --dialog=&lt;name&gt;     open a dialog first: pack, extract, export, rebase, rename, settings, remove, damage
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
        double wait = args.TryGetValue("wait", out var w) && double.TryParse(w, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var ws) ? ws : 2.5;
        await window.ToSignal(window.GetTree().CreateTimer(wait), SceneTreeTimer.SignalName.Timeout);
        if (args.TryGetValue("view", out var view))
            window.DevSetView(view);
        if (args.TryGetValue("select", out var sel) && int.TryParse(sel, out var n))
            window.DevSelectFirst(n);
        if (args.TryGetValue("job", out var job))  // "--job=verify|<pack>" : the tool's arguments, | separated
            window.DevStartJob(job.Split('|'));
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
