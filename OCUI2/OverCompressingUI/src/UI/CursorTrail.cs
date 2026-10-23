using System;
using System.Collections.Generic;
using Godot;

namespace OverCompressingUI.UI;

/// <summary>The cursor's wake: the arrow itself, drawn again along the last 0.3 s of its path and fading, cyan near
/// the mouse to magenta at the end, with a soft light under it. Samples are filled in every few pixels so a fast
/// mouse leaves one smear, not a row of arrows.</summary>
public partial class CursorTrail : Control
{
    private readonly List<(Vector2 at, double t)> _points = new();
    private double _time;
    private Vector2 _last = new(-1, -1);
    private const double Keep = 0.3;
    private Texture2D _ghost = null!;
    private static readonly Vector2 Hotspot = new(4, 3);
    private const float CursorSize = 32f;
    public string Info => $"points={_points.Count} mouse={GetGlobalMousePosition()} effects={Ui.Effects}";

    public override void _Ready()
    {
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        MouseFilter = MouseFilterEnum.Ignore;
        _ghost = Icons.Cursor();
    }

    public override void _Process(double delta)
    {
        _time += delta;
        if (!Ui.Effects)
        {
            if (_points.Count > 0) { _points.Clear(); QueueRedraw(); }
            return;
        }
        var at = GetLocalMousePosition();
        if (at.DistanceTo(_last) > 0.5f && GetGlobalRect().HasPoint(GetGlobalMousePosition()))
        {
            float gap = _last.X < 0 ? 0 : at.DistanceTo(_last);
            int steps = Math.Clamp((int)(gap / 3f), 1, 40);
            for (int i = 1; i <= steps; i++)
                _points.Add((steps == 1 ? at : _last.Lerp(at, i / (float)steps), _time));
            _last = at;
            while (_points.Count > 200)
                _points.RemoveAt(0);
        }
        int drop = 0;
        while (drop < _points.Count && _time - _points[drop].t > Keep)
            drop++;
        if (drop > 0)
            _points.RemoveRange(0, drop);
        if (_points.Count > 0)
            QueueRedraw();
    }

    public override void _Draw()
    {
        // the oldest first, so the newest ghost lies on top; the real cursor (the OS draws it) sits on the newest
        foreach (var (at, t) in _points)
        {
            float a = 1f - (float)((_time - t) / Keep);
            if (a <= 0)
                continue;
            a = a * a;
            var tint = Colors.White.Lerp(new Color(1f, 0.45f, 0.95f), 1f - a);  // toward magenta as it ages
            Fx.DrawGlow(this, at + new Vector2(6, 8), 30f, Ui.Neon.Lerp(Ui.Neon2, 1f - a) with { A = 0.07f * a });
            DrawTextureRect(_ghost, new Rect2(at - Hotspot, new Vector2(CursorSize, CursorSize)), false, tint with { A = 0.22f * a });
        }
    }
}
