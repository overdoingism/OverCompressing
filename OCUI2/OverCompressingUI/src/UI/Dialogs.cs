using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Godot;
using OverCompressingUI.Core;

namespace OverCompressingUI.UI;

/// <summary>A dialog of this window: a card with a bold first line (what it is about), its content, a red line kept
/// for an error, and OK / Cancel. Shown over the window (embedded) and awaited: Enter accepts, Escape cancels.
/// Dialogs state the facts, then ask last; buttons carry no "…".</summary>
public partial class OcDialog : Window
{
    public readonly VBoxContainer Body = Ui.VBox(6);
    protected Label ErrorLine = null!;
    public Button OkButton = null!;
    public Button CancelButton = null!;
    private HBoxContainer _buttons = null!;
    private readonly TaskCompletionSource<bool> _result = new();
    public Func<bool>? Accepting;  // false keeps the dialog open (an error shown)
    public Func<Task<bool>>? AcceptingAsync;  // the same, for a check that asks something first
    public Control? FirstFocus;
    private bool _accepting;

    public OcDialog(string title, bool resizable = false)
    {
        Title = title;
        Transient = true;
        Exclusive = true;
        Transparent = true;  // the content is see-through: the frame the embedder draws (edge, corners, glow) shows all round
        TransparentBg = true;
        Unresizable = !resizable;
        WrapControls = true;
        Theme = Ui.Theme;
        var margin = _margin = new MarginContainer();
        margin.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.FullRect);
        foreach (var side in new[] { "margin_left", "margin_right", "margin_top", "margin_bottom" })
            margin.AddThemeConstantOverride(side, Ui.Px(18));
        AddChild(margin);
        var box = Ui.VBox(6);
        margin.AddChild(box);
        box.AddChild(Body);
        ErrorLine = Ui.Label("", Ui.Error, wrap: true);
        ErrorLine.Visible = false;
        box.AddChild(ErrorLine);
        _buttons = Ui.HBox(8);
        _buttons.Alignment = BoxContainer.AlignmentMode.End;
        box.AddChild(Ui.Gap(8));
        box.AddChild(_buttons);
        OkButton = Ui.Button(T.Get("ok"), Accept, primary: true);
        OkButton.CustomMinimumSize = new Vector2(Ui.Px(88), 0);
        CancelButton = Ui.Button(T.Get("cancel"), () => Finish(false));
        _buttons.AddChild(OkButton);
        _buttons.AddChild(CancelButton);
        CloseRequested += () => Finish(false);
    }

    /// <summary>Put another button left of OK (Copy, Clean up, Close for reports).</summary>
    public Button AddButton(string text, Action pressed, bool atLeft = false)
    {
        var b = Ui.Button(text, pressed);
        _buttons.AddChild(b);
        if (atLeft)
            _buttons.MoveChild(b, 0);
        return b;
    }

    public void HideOk() => OkButton.Visible = false;
    public void HideCancel() => CancelButton.Visible = false;

    public void ShowError(string? text)
    {
        ErrorLine.Text = T.Sentence(text ?? "");
        ErrorLine.Visible = !string.IsNullOrEmpty(text);
        ResetSize();
    }

    protected async void Accept()
    {
        if (_accepting)
            return;
        try
        {
            _accepting = true;
            if (Accepting is not null && !Accepting())
                return;
            if (AcceptingAsync is not null && !await AcceptingAsync())
                return;
            Finish(true);
        }
        catch (Exception e)
        {
            GD.PrintErr(e);
            ShowError(e.Message);
        }
        finally
        {
            _accepting = false;
        }
    }

    /// <summary>How many dialogs are open: the window keeps its frosted veil up while any is.</summary>
    public static event Action<int>? OpenCountChanged;
    private static int _open;
    private MarginContainer _margin = null!;

    private void Finish(bool ok)
    {
        if (_result.Task.IsCompleted)
            return;
        _result.SetResult(ok);
        _open = Math.Max(0, _open - 1);
        OpenCountChanged?.Invoke(_open);
        QueueFree();
    }

    public override void _Input(InputEvent e)
    {
        if (e is InputEventKey { Pressed: true, Echo: false } key)
        {
            if (key.Keycode == Key.Escape)
            {
                Finish(false);
                GetViewport().SetInputAsHandled();
            }
            else if (key.Keycode is Key.Enter or Key.KpEnter && OkButton.Visible && GetViewport().GuiGetFocusOwner() is not TextEdit)
            {
                Accept();
                GetViewport().SetInputAsHandled();
            }
        }
    }

    /// <summary>Show the dialog over `parent`, centered, and wait until it is closed: true if accepted.</summary>
    public async Task<bool> ShowAsync(Node parent)
    {
        _open++;
        OpenCountChanged?.Invoke(_open);
        Fx.Sfx.Open();
        parent.AddChild(this);
        PopupCentered();
        await ToSignal(GetTree(), SceneTree.SignalName.ProcessFrame);
        ResetSize();
        PopupCentered();
        var area = GetParent().GetViewport().GetVisibleRect();  // canvas units, as embedded windows are placed
        Position = new Vector2I(Position.X, Mathf.Max(Position.Y - (int)(area.Size.Y / 10), 0));
        // the entrance: the content rises into place and lights up
        _margin.PivotOffset = _margin.Size / 2;
        _margin.Scale = new Vector2(0.96f, 0.96f);
        _margin.Modulate = new Color(1, 1, 1, 0);
        var tw = CreateTween().SetParallel();
        tw.TweenProperty(_margin, "scale", Vector2.One, 0.24).SetTrans(Tween.TransitionType.Back).SetEase(Tween.EaseType.Out);
        tw.TweenProperty(_margin, "modulate", Colors.White, 0.18);
        FirstFocus?.GrabFocus();
        return await _result.Task;
    }

    // ------------------------------------------------------------------------------------------ message boxes

    public static Task<bool> Ask(Node parent, string title, string text)
    {
        var d = new OcDialog(title);
        d.Body.AddChild(Ui.Label(text, Ui.Text, wrap: true));
        return d.ShowAsync(parent);
    }

    public static Task Info(Node parent, string title, string text)
    {
        var d = new OcDialog(title);
        d.Body.AddChild(Ui.Label(text, Ui.Text, wrap: true));
        d.HideCancel();
        return d.ShowAsync(parent);
    }

    public static Task Error(Node parent, string title, string text)
    {
        var d = new OcDialog(title);
        var l = Ui.Label(text, Ui.Text, wrap: true);
        d.Body.AddChild(l);
        d.HideCancel();
        return d.ShowAsync(parent);
    }

    /// <summary>A path on a line of its own, folders from its middle left out to fit; the whole of it on hover.</summary>
    public static Label PathLine(string path)
    {
        float wrap = Ui.Px(460);
        var shown = T.Shorten(path, s => Ui.Measure(s) <= wrap);
        var l = Ui.Label(shown, Ui.Text);
        if (shown != path)
            l.TooltipText = path;
        return l;
    }
}

/// <summary>Native file and folder dialogs, awaited.</summary>
public static class Pick
{
    private static async Task<string[]?> Run(Node parent, FileDialog d)
    {
        var tcs = new TaskCompletionSource<string[]?>();
        d.UseNativeDialog = true;
        d.Access = FileDialog.AccessEnum.Filesystem;
        d.FileSelected += p => tcs.TrySetResult(new[] { p });
        d.DirSelected += p => tcs.TrySetResult(new[] { p });
        d.FilesSelected += p => tcs.TrySetResult(p);
        d.Canceled += () => tcs.TrySetResult(null);
        parent.AddChild(d);
        d.PopupCentered();
        var result = await tcs.Task;
        d.QueueFree();
        return result;
    }

    public static async Task<string?> Folder(Node parent, string title, string? start = null)
    {
        var d = new FileDialog { Title = title, FileMode = FileDialog.FileModeEnum.OpenDir };
        if (!string.IsNullOrEmpty(start) && System.IO.Directory.Exists(start))
            d.CurrentDir = start;
        var r = await Run(parent, d);
        return r?.Length > 0 ? Place.NormPath(r[0]) : null;
    }

    public static async Task<string?> SaveOczip(Node parent, string title, string? startDir, string? startFile)
    {
        var d = new FileDialog { Title = title, FileMode = FileDialog.FileModeEnum.SaveFile, Filters = new[] { "*.oczip ; Pack file" } };
        if (!string.IsNullOrEmpty(startDir) && System.IO.Directory.Exists(startDir))
            d.CurrentDir = startDir;
        if (!string.IsNullOrEmpty(startFile))
            d.CurrentFile = startFile;
        var r = await Run(parent, d);
        return r?.Length > 0 ? Place.NormPath(r[0]) : null;
    }

    public static async Task<string[]> Files(Node parent, string title, string? startDir = null)
    {
        var d = new FileDialog { Title = title, FileMode = FileDialog.FileModeEnum.OpenFiles };
        if (!string.IsNullOrEmpty(startDir) && System.IO.Directory.Exists(startDir))
            d.CurrentDir = startDir;
        var r = await Run(parent, d);
        return r ?? Array.Empty<string>();
    }

    public static async Task<string?> AnyFile(Node parent, string title, string? startDir, string filter)
    {
        var d = new FileDialog { Title = title, FileMode = FileDialog.FileModeEnum.OpenFile, Filters = new[] { filter } };
        if (!string.IsNullOrEmpty(startDir) && System.IO.Directory.Exists(startDir))
            d.CurrentDir = startDir;
        var r = await Run(parent, d);
        return r?.Length > 0 ? Place.NormPath(r[0]) : null;
    }
}
