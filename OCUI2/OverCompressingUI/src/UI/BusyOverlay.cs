using System;
using Godot;
using OverCompressingUI.Core;

namespace OverCompressingUI.UI;

/// <summary>While llm_archive.py runs: a card in front (the frosted veil behind it is the window's) with what runs,
/// the last line it printed, how far the current step is (when the tool can tell), how long it has run beside a
/// neon ring, and Cancel. Closing is Cancel.</summary>
public partial class BusyOverlay : Control
{
    private PanelContainer _card = null!;
    private Label _title = null!;
    private Label _line = null!;
    private Label _step = null!;
    private Label _spinner = null!;
    private Ring _ring = null!;
    private Button _cancel = null!;
    public Action? OnCancel;
    private int _tick;
    private double _accum;
    private StyleBoxFlat _box = null!;
    private double _time;

    public override void _Ready()
    {
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        MouseFilter = MouseFilterEnum.Stop;
        Visible = false;
        var center = new CenterContainer();
        center.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(center);
        _card = new PanelContainer();
        _box = Ui.Glass(16, 24, Ui.Hdr(Ui.Neon, 1.2f) with { A = 0.9f });
        _box.ShadowColor = new Color(Ui.Neon, 0.3f);
        _box.ShadowSize = Ui.Px(30);
        _card.AddThemeStyleboxOverride("panel", _box);
        _card.CustomMinimumSize = new Vector2(Ui.Px(660), 0);
        center.AddChild(_card);
        var box = Ui.VBox(8);
        _card.AddChild(box);
        _title = Ui.Label("", Ui.Text, bold: true, size: Ui.FontSize + 3);
        _title.ClipText = true;
        _title.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        box.AddChild(_title);
        _line = Ui.Label("", Ui.TextDim);
        _line.ClipText = true;
        _line.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        box.AddChild(_line);
        _step = Ui.Label("", Ui.TextDim);
        _step.ClipText = true;
        _step.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        box.AddChild(_step);
        box.AddChild(Ui.Gap(8));
        var row = Ui.HBox(14);
        box.AddChild(row);
        _ring = new Ring { CustomMinimumSize = new Vector2(Ui.Px(34), Ui.Px(34)) };
        row.AddChild(_ring);
        _spinner = Ui.DisplayLabel("", Ui.Hdr(Ui.Neon, 1.0f), Ui.SmallFontSize);
        row.AddChild(_spinner);
        row.AddChild(Ui.Spacer());
        _cancel = Ui.Button(T.Get("cancel"), () => OnCancel?.Invoke());
        row.AddChild(_cancel);
    }

    public void Open(string title)
    {
        _title.Text = title;
        _line.Text = "";
        _step.Text = "";
        _spinner.Text = "";
        _cancel.Text = T.Get("cancel");
        _ring.Progress = null;
        Visible = true;
        Modulate = new Color(1, 1, 1, 0);
        _card.PivotOffset = _card.Size / 2;
        _card.Scale = new Vector2(0.94f, 0.94f);
        var tw = CreateTween().SetParallel();
        tw.TweenProperty(this, "modulate", new Color(1, 1, 1, 1), 0.2);
        tw.TweenProperty(_card, "scale", Vector2.One, 0.28).SetTrans(Tween.TransitionType.Back).SetEase(Tween.EaseType.Out);
        Fx.Sfx.Open();
    }

    public void Close()
    {
        if (!Visible)
            return;
        var tw = CreateTween();
        tw.TweenProperty(this, "modulate", new Color(1, 1, 1, 0), 0.16);
        tw.TweenCallback(Callable.From(() => Visible = false));
    }

    public void SetTitle(string text) => _title.Text = text;
    public void SetLine(string text) => _line.Text = T.Cut(text);
    public void SetStep(string text) => _step.Text = T.Cut(text);

    public void SetCancelling()
    {
        _line.Text = T.Get("cancelling");
        _cancel.Text = T.Get("stop_now");
        _box.BorderColor = Ui.Hdr(Ui.Neon2, 1.2f);
        _box.ShadowColor = new Color(Ui.Neon2, 0.3f);
    }

    /// <summary>Called every frame while a job runs.</summary>
    public void Tick(JobRunner runner, double delta)
    {
        _time += delta;
        _accum += delta;
        if (_accum >= 0.15)
        {
            _accum = 0;
            _tick++;
            _spinner.Text = runner.SpinnerText(_tick);
        }
        var p = runner.Progress;
        if (p is not null && p.Long("total") is > 0)
            _ring.Progress = (float)Math.Min((double)(p.Long("done") ?? 0) / p.Long("total")!.Value, 1);
        else
            _ring.Progress = null;
        _ring.Advance(delta);
        if (Ui.Effects && !runner.Cancelling)
        {
            float pulse = 0.5f + 0.5f * Mathf.Sin((float)_time * 2.4f);
            _box.ShadowColor = new Color(Ui.Neon, 0.18f + 0.22f * pulse);
        }
    }

    /// <summary>A neon ring: an arc running round while nothing can be counted, a filled share when it can.</summary>
    public partial class Ring : Control
    {
        public float? Progress;
        private float _angle;

        public void Advance(double delta)
        {
            _angle += (float)delta * 3.4f;
            QueueRedraw();
        }

        public override void _Draw()
        {
            var c = Size / 2;
            float r = Mathf.Min(Size.X, Size.Y) / 2 - Ui.Px(3);
            DrawArc(c, r, 0, Mathf.Tau, 64, new Color(Ui.Neon, 0.12f), Ui.Px(3), true);
            var lit = Ui.Hdr(Ui.Neon, Ui.Effects ? 1.9f : 1f);
            if (Progress is { } p)
            {
                DrawArc(c, r, -Mathf.Pi / 2, -Mathf.Pi / 2 + Mathf.Tau * Mathf.Max(p, 0.01f), 64, lit, Ui.Px(3), true);
                DrawArc(c, r, -Mathf.Pi / 2 + Mathf.Tau * Mathf.Max(p, 0.01f) - 0.1f, -Mathf.Pi / 2 + Mathf.Tau * Mathf.Max(p, 0.01f), 8, Ui.Hdr(Ui.Neon2, 1.6f), Ui.Px(3), true);
            }
            else
            {
                DrawArc(c, r, _angle, _angle + Mathf.Tau * 0.3f, 32, lit, Ui.Px(3), true);
                DrawArc(c, r, _angle + Mathf.Pi, _angle + Mathf.Pi + Mathf.Tau * 0.12f, 16, Ui.Hdr(Ui.Neon2, 1.5f), Ui.Px(3), true);
            }
        }
    }
}
