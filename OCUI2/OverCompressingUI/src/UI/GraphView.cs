using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using OverCompressingUI.Core;

namespace OverCompressingUI.UI;

/// <summary>The models of a pack as a graph of what feeds what: a node per model (its area by what it takes in the
/// pack), an arrow from each base to the models coded against it, the way the bytes flow at restore (its width by
/// how much of the base is read). Bases sit above what they feed. The layout is settled once (by forces, off
/// screen) and then holds still: each node floats a few pixels around its place and stays where it is dragged.
/// Wheel to zoom, drag the space to pan, click to select (with Ctrl and Shift as in the list), double-click to open
/// the model.</summary>
public partial class GraphView : Control
{
    private sealed class Node
    {
        public string Id = "";
        public Row Row = null!;
        public Vector2 Anchor;  // where it belongs
        public Vector2 Pos;     // where it is drawn: the anchor plus a little float
        public Vector2 Vel;     // only while settling
        public float Radius;
        public int Depth;
        public float Phase;
        public string Label = "";  // the short name, on the ring; cut with … when it will not fit
        public int TextSize;
    }

    private const float Band = 16f;  // the ring of text around a node: from the ring to the outside of the glyphs
    private const float MaxArc = 3.5f;  // radians: the text takes at most 200° of the circle, over the top

    private sealed class Edge
    {
        public Node From = null!, To = null!;
        public float Width;
        public string Label = "";
        public float Phase;
    }

    private static readonly Color Warm = new(1f, 0.90f, 0.70f);  // the lit edge: beige, as the pads' breath

    private readonly List<Node> _nodes = new();
    private readonly List<Edge> _edges = new();
    private readonly Dictionary<string, Node> _byId = new();
    private readonly HashSet<string> _removed = new();  // nodes shown as ghosts: needs of removed models
    private HashSet<string> _selected = new();
    private HashSet<string> _matched = new();
    private Vector2 _pan = Vector2.Zero;
    private float _zoom = 1f;
    private Node? _dragging;
    private bool _panning;
    private Node? _hovered;
    private bool _laidOut;
    private double _time;
    private StyleBoxFlat? _ground;
    private StyleBoxFlat? _strip;

    public event Action<string, bool, bool>? NodeClicked;  // id, ctrl, shift
    public event Action<string>? NodeDoubleClicked;
    public event Action<bool>? EmptyClicked;  // ctrl

    public override void _Ready()
    {
        MouseFilter = MouseFilterEnum.Stop;
        ClipContents = true;
        FocusMode = FocusModeEnum.All;
        Resized += () => { if (_laidOut) Fit(); QueueRedraw(); };
    }

    // ------------------------------------------------------------------------------------------ the graph

    /// <summary>Set the models shown (pack rows). Nodes still there keep their places; new ones are settled in.</summary>
    public void SetRows(IEnumerable<Row> rows, HashSet<string> matched)
    {
        var list = rows.Where(r => r.Kind == "model").ToList();
        var keep = new Dictionary<string, Node>(_byId);
        _nodes.Clear();
        _byId.Clear();
        _edges.Clear();
        _removed.Clear();
        _matched = matched;
        bool fresh = keep.Count == 0;
        foreach (var r in list)
        {
            var n = keep.TryGetValue(r.Id, out var old) ? old : new Node { Id = r.Id, Anchor = new Vector2(GD.Randf() * 200 - 100, GD.Randf() * 200 - 100), Phase = GD.Randf() * Mathf.Tau };
            if (!keep.ContainsKey(r.Id))
                fresh = true;
            n.Row = r;
            double stored = Math.Max(r.Stored ?? r.Size ?? 1, 1);
            n.Radius = Ui.Px(26) + Ui.Px(6) * (float)Math.Log10(stored / (1 << 20) + 1);
            (n.Label, n.TextSize) = FitRing(ShortName(r.Name), n.Radius);
            _nodes.Add(n);
            _byId[n.Id] = n;
        }
        int edgeIndex = 0;
        foreach (var r in list)
        {
            var m = r.Summary;
            if (m is null)
                continue;
            foreach (var need in m.Needs)
            {
                if (!_byId.TryGetValue(need, out var from))
                    continue;
                var usage = m.Usage.TryGetValue(need, out var u) ? u : 0;
                _edges.Add(new Edge { From = from, To = _byId[r.Id], Width = Ui.Px(1.2f) + Ui.Px(3.2f) * (float)usage, Label = $"{usage * 100:F0}%", Phase = edgeIndex++ * 0.37f });
            }
            foreach (var gone in m.NeedsRemoved)
            {
                var ghostId = "\0removed:" + gone;
                if (!_byId.TryGetValue(ghostId, out var ghost))
                {
                    ghost = keep.TryGetValue(ghostId, out var old) ? old : new Node { Id = ghostId, Anchor = _byId[r.Id].Anchor + new Vector2(0, -120), Phase = GD.Randf() * Mathf.Tau };
                    if (!keep.ContainsKey(ghostId))
                        fresh = true;
                    ghost.Row = new Row { Id = ghostId, Name = T.Get("removed", ("name", gone)), Kind = "model" };
                    ghost.Radius = Ui.Px(20);
                    (ghost.Label, ghost.TextSize) = FitRing(ShortName(gone) + " (removed)", ghost.Radius);
                    _nodes.Add(ghost);
                    _byId[ghostId] = ghost;
                    _removed.Add(ghostId);
                }
                _edges.Add(new Edge { From = ghost, To = _byId[r.Id], Width = Ui.Px(1.5f), Label = "", Phase = edgeIndex++ * 0.37f });
            }
        }
        // depth: what is fed sits below what feeds it (longest path from a root, cycles cut)
        foreach (var n in _nodes)
            n.Depth = 0;
        for (int round = 0; round < _nodes.Count; round++)
        {
            bool changed = false;
            foreach (var e in _edges)
                if (e.To.Depth <= e.From.Depth && e.To.Depth < _nodes.Count)
                {
                    e.To.Depth = e.From.Depth + 1;
                    changed = true;
                }
            if (!changed)
                break;
        }
        if (!_laidOut || fresh)
            Reset();
        QueueRedraw();
    }

    /// <summary>The model's own name, without its organisation (the panel has the whole name).</summary>
    private static string ShortName(string name)
    {
        int slash = name.LastIndexOf('/');
        return slash >= 0 && slash < name.Length - 1 ? name[(slash + 1)..] : name;
    }

    /// <summary>The text that fits on the ring of a node of radius `r`: the size comes down from 12 to 8 first,
    /// then the end is cut and marked with …</summary>
    private static (string text, int size) FitRing(string text, float r)
    {
        float arcLen = (r + Band * 0.55f) * MaxArc;
        for (int size = 12; size >= 8; size--)
            if (Ui.Measure(text, Ui.Font, size) <= arcLen)
                return (text, size);
        var cut = text;
        while (cut.Length > 3 && Ui.Measure(cut + "…", Ui.Font, 8) > arcLen)
            cut = cut[..^1];
        return (cut + "…", 8);
    }

    public void SetSelection(HashSet<string> selected)
    {
        _selected = selected;
        QueueRedraw();
    }

    /// <summary>Lay the nodes out: by depth (bases at the top), then settled by forces off screen, then fitted.</summary>
    public void Reset()
    {
        _laidOut = true;
        var byDepth = _nodes.GroupBy(n => n.Depth).OrderBy(g => g.Key).ToList();
        float rowGap = Ui.Px(170), colGap = Ui.Px(200);
        foreach (var g in byDepth)
        {
            var row = g.OrderBy(n => n.Id, StringComparer.Ordinal).ToList();
            for (int i = 0; i < row.Count; i++)
            {
                row[i].Anchor = new Vector2((i - (row.Count - 1) / 2f) * colGap + (g.Key % 2) * colGap * 0.3f, g.Key * rowGap);
                row[i].Vel = Vector2.Zero;
            }
        }
        Settle();
        foreach (var n in _nodes)
            n.Pos = n.Anchor;
        _zoom = 1f;
        Fit();
        QueueRedraw();
    }

    /// <summary>Run the forces to rest, off screen: nodes keep apart, stay on their depth's row and near what they
    /// are joined to. The result is the anchors; nothing drifts afterwards.</summary>
    private void Settle()
    {
        float rowGap = Ui.Px(170);
        const float dt = 0.02f;
        for (int step = 0; step < 400; step++)
        {
            float energy = 0;
            foreach (var a in _nodes)
            {
                var force = Vector2.Zero;
                foreach (var b in _nodes)
                {
                    if (a == b)
                        continue;
                    var d = a.Anchor - b.Anchor;
                    float dist = Math.Max(d.Length(), 1f);
                    float min = a.Radius + b.Radius + 2 * Band + Ui.Px(70);
                    float push = dist < min ? (min - dist) * 2.2f : Ui.Px(3000) / (dist * dist) * 20;
                    force += d / dist * push;
                }
                force += new Vector2(0, a.Depth * rowGap - a.Anchor.Y) * 1.6f;
                force += new Vector2(-a.Anchor.X, 0) * 0.08f;
                foreach (var e in _edges)
                {
                    if (e.From != a && e.To != a)
                        continue;
                    var other = e.From == a ? e.To : e.From;
                    var d = other.Anchor - a.Anchor;
                    float dist = Math.Max(d.Length(), 1f);
                    force += d / dist * (dist - rowGap * 1.05f) * 0.5f;
                }
                a.Vel = (a.Vel + force * dt) * 0.80f;
                energy += a.Vel.LengthSquared();
            }
            foreach (var a in _nodes)
                a.Anchor += a.Vel * dt;
            if (energy / Math.Max(_nodes.Count, 1) < 0.5f && step > 60)
                break;
        }
        foreach (var a in _nodes)
            a.Vel = Vector2.Zero;
    }

    /// <summary>Zoom and pan so that every node, with its label, is in view (never closer than 1:1).</summary>
    public void Fit()
    {
        if (_nodes.Count == 0 || Size.X <= 0 || Size.Y <= 0)
            return;
        float minX = float.MaxValue, minY = float.MaxValue, maxX = float.MinValue, maxY = float.MinValue;
        foreach (var n in _nodes)
        {
            float half = n.Radius + Band + Ui.Px(6);
            minX = Math.Min(minX, n.Anchor.X - half);
            maxX = Math.Max(maxX, n.Anchor.X + half);
            minY = Math.Min(minY, n.Anchor.Y - half);
            maxY = Math.Max(maxY, n.Anchor.Y + half);
        }
        float margin = Ui.Px(24);
        var avail = new Vector2(Size.X - 2 * margin, Size.Y - 2 * margin - Ui.Px(28));
        var need = new Vector2(Math.Max(maxX - minX, 1), Math.Max(maxY - minY, 1));
        _zoom = Mathf.Clamp(Math.Min(avail.X / need.X, avail.Y / need.Y), 0.25f, 1f);
        var center = new Vector2((minX + maxX) / 2, (minY + maxY) / 2);
        _pan = new Vector2(Size.X / 2, (Size.Y - Ui.Px(28)) / 2) - center * _zoom;
    }

    // ------------------------------------------------------------------------------------------ life

    public override void _Process(double delta)
    {
        if (!Visible || _nodes.Count == 0)
            return;
        if (!Ui.Effects)
        {
            foreach (var n in _nodes)
                n.Pos = n.Anchor;
            return;
        }
        _time += delta;
        float t = (float)_time;
        foreach (var n in _nodes)  // a few pixels of float around the anchor, never away from it
            n.Pos = n.Anchor + new Vector2(Mathf.Sin(t * 0.6f + n.Phase) * 2.5f, Mathf.Cos(t * 0.8f + n.Phase * 1.3f) * 2.5f);
        QueueRedraw();
    }

    private Vector2 ToScreen(Vector2 p) => p * _zoom + _pan;
    private Vector2 ToWorld(Vector2 s) => (s - _pan) / _zoom;

    private Node? At(Vector2 screen)
    {
        var w = ToWorld(screen);
        for (int i = _nodes.Count - 1; i >= 0; i--)
            if (_nodes[i].Pos.DistanceTo(w) <= _nodes[i].Radius + Band)
                return _nodes[i];
        return null;
    }

    public override void _GuiInput(InputEvent e)
    {
        if (e is InputEventMouseButton mb)
        {
            if (mb.ButtonIndex == MouseButton.WheelUp || mb.ButtonIndex == MouseButton.WheelDown)
            {
                if (!mb.Pressed)
                    return;
                float factor = mb.ButtonIndex == MouseButton.WheelUp ? 1.12f : 1 / 1.12f;
                var before = ToWorld(mb.Position);
                _zoom = Mathf.Clamp(_zoom * factor, 0.25f, 3f);
                _pan = mb.Position - before * _zoom;
                QueueRedraw();
                AcceptEvent();
                return;
            }
            if (mb.ButtonIndex == MouseButton.Left)
            {
                if (mb.Pressed)
                {
                    GrabFocus();
                    var n = At(mb.Position);
                    if (mb.DoubleClick)
                    {
                        if (n is not null && !_removed.Contains(n.Id))
                            NodeDoubleClicked?.Invoke(n.Id);
                        AcceptEvent();
                        return;
                    }
                    if (n is not null)
                    {
                        _dragging = n;
                        if (!_removed.Contains(n.Id))
                            NodeClicked?.Invoke(n.Id, mb.CtrlPressed, mb.ShiftPressed);
                    }
                    else
                    {
                        _panning = true;
                        EmptyClicked?.Invoke(mb.CtrlPressed);
                    }
                }
                else
                {
                    _dragging = null;
                    _panning = false;
                }
                AcceptEvent();
            }
            else if (mb.ButtonIndex is MouseButton.Middle or MouseButton.Right)
            {
                _panning = mb.Pressed;
                AcceptEvent();
            }
        }
        else if (e is InputEventMouseMotion mm)
        {
            if (_dragging is not null)
            {
                _dragging.Anchor += mm.Relative / _zoom;  // it stays where it is put
                _dragging.Pos += mm.Relative / _zoom;
                QueueRedraw();
            }
            else if (_panning)
            {
                _pan += mm.Relative;
                QueueRedraw();
            }
            else
            {
                var h = At(mm.Position);
                if (h != _hovered)
                {
                    _hovered = h;
                    TooltipText = h is null || _removed.Contains(h.Id) ? "" : Describe(h.Row);
                    QueueRedraw();
                }
            }
        }
    }

    private static string Describe(Row r)
    {
        var parts = new List<string> { r.Name };
        if (r.Files is not null)
            parts.Add(T.Count(r.Files.Value, "file"));
        if (r.Size is not null)
            parts.Add($"{T.SizeText(r.Size)} → {T.SizeText(r.Stored)} ({r.Ratio})");
        if (r.Needs.Length > 0)
            parts.Add("needs " + r.Needs);
        if (r.Summary?.NeededBy.Count > 0)
            parts.Add("needed by " + string.Join(", ", r.Summary.NeededBy));
        if (r.Packed is not null)
            parts.Add("packed " + r.Packed.Value.ToString("yyyy-MM-dd HH:mm"));
        return string.Join("\n", parts);
    }

    // ------------------------------------------------------------------------------------------ drawing

    private static Vector2 Bezier(Vector2 a, Vector2 c, Vector2 b, float t) => (1 - t) * (1 - t) * a + 2 * (1 - t) * t * c + t * t * b;

    public override void _Draw()
    {
        _ground ??= Ui.Flat(new Color(Ui.Bg, 0.9f), 10, new Color(Ui.Neon, 0.22f), 1, 0);
        DrawStyleBox(_ground, new Rect2(Vector2.Zero, Size));
        var font = Ui.Font;
        int size = Ui.SmallFontSize;
        if (_nodes.Count == 0)
        {
            var text = "No models";
            DrawString(font, new Vector2((Size.X - Ui.Measure(text)) / 2, Size.Y / 2), text, HorizontalAlignment.Left, -1, size, Ui.TextFaint);
            return;
        }
        bool Lit(Node n) => _selected.Contains(n.Id) || _hovered == n;

        // edges, from the base to what it feeds, outside the ring of text at both ends
        var tailPts = new List<Vector2>();
        var tailCols = new List<Color>();
        foreach (var e in _edges)
        {
            var a = ToScreen(e.From.Pos);
            var b = ToScreen(e.To.Pos);
            var dir = (b - a).Normalized();
            var start = a + dir * (e.From.Radius + Band + 2) * _zoom;
            var end = b - dir * (e.To.Radius + Band + 4) * _zoom;
            bool lit = Lit(e.From) || Lit(e.To);
            bool ghost = _removed.Contains(e.From.Id);
            var color = lit ? Ui.Hdr(Warm, Ui.Effects ? 1.15f : 1f) with { A = 0.95f } : new Color(Ui.Neon, ghost ? 0.15f : 0.35f);
            float w = e.Width * _zoom;
            var mid = (start + end) / 2 + new Vector2(-dir.Y, dir.X) * Ui.Px(18) * _zoom;
            var points = new Vector2[17];
            for (int i = 0; i < points.Length; i++)
                points[i] = Bezier(start, mid, end, i / (float)(points.Length - 1));
            DrawPolyline(points, color, w, true);
            // the arrow head: a barbed chevron, sized with the line
            var head = end;
            var ad = (points[^1] - points[^3]).Normalized();
            var perp = new Vector2(-ad.Y, ad.X);
            float ah = (Ui.Px(8) + w * 1.1f) * Mathf.Max(_zoom, 0.6f);
            var back = head - ad * ah;
            var notch = head - ad * ah * 0.62f;
            var left = back + perp * ah * 0.48f;
            var right = back - perp * ah * 0.48f;
            DrawColoredPolygon(new[] { head, left, notch, right }, color);
            DrawPolyline(new[] { left, head, right }, color with { A = Math.Min(1f, color.A + 0.2f) }, Math.Max(1f, w * 0.6f), true);
            // the bytes on their way: a short tail of light with a soft head, as the signals on the board
            if (Ui.Effects && !ghost)
            {
                float length = 0;
                for (int i = 1; i < points.Length; i++)
                    length += points[i - 1].DistanceTo(points[i]);
                float tailLen = Mathf.Min(Ui.Px(42) * _zoom, length * 0.4f);
                var sparkColor = lit ? Warm : Ui.Neon;
                for (int k = 0; k < 2; k++)
                {
                    float tt = (float)((_time * 0.18 + k * 0.5 + e.Phase) % 1.0);
                    float t0 = Math.Max(tt - tailLen / Math.Max(length, 1f), 0f);
                    tailPts.Clear();
                    tailCols.Clear();
                    for (float t = t0; t <= tt; t += 0.02f)
                    {
                        float f = (t - t0) / Math.Max(tt - t0, 0.001f);
                        tailPts.Add(Bezier(start, mid, end, t));
                        tailCols.Add(Ui.Hdr(sparkColor, 0.9f + 0.5f * f * f) with { A = 0.7f * f * f });
                    }
                    var sp = Bezier(start, mid, end, tt);
                    if (tailPts.Count >= 2)
                    {
                        tailPts.Add(sp);
                        tailCols.Add(Ui.Hdr(sparkColor, 1.35f) with { A = 0.8f });
                        DrawPolylineColors(tailPts.ToArray(), tailCols.ToArray(), Math.Max(w * 0.8f, 1.5f), true);
                    }
                    Fx.DrawGlow(this, sp, Ui.Px(12) * _zoom + w, Ui.Hdr(sparkColor, 1.3f) with { A = 0.7f });
                }
            }
        }

        // nodes
        foreach (var n in _nodes)
        {
            var p = ToScreen(n.Pos);
            float r = n.Radius * _zoom;
            bool ghost = _removed.Contains(n.Id);
            bool selected = _selected.Contains(n.Id);
            bool dim = _matched.Count > 0 && !ghost && !_matched.Contains(n.Id);
            var fill = ghost ? new Color(1, 1, 1, 0.05f) : new Color(Ui.KindColor["model"], dim ? 0.08f : 0.22f);
            float pulse = Ui.Effects ? 0.5f + 0.5f * Mathf.Sin((float)_time * 2.2f + n.Phase) : 0.5f;
            var ring = ghost ? new Color(1, 1, 1, 0.25f) : n.Row.Damaged ? Ui.Hdr(Ui.Damaged, 1.3f) : selected ? Ui.Hdr(Ui.Neon, 1.4f + 0.6f * pulse)
                : Ui.Hdr(Ui.KindColor["model"], dim ? 0.4f : 0.9f + 0.4f * pulse);
            if (selected && !ghost)
                DrawCircle(p, r + (Ui.Px(6) + Ui.Px(4) * pulse) * _zoom, new Color(Ui.Neon, 0.16f + 0.1f * pulse));
            DrawCircle(p, r, new Color(Ui.Bg, 0.95f));
            DrawCircle(p, r, fill);
            if (ghost)
                DrawArc(p, r, 0, Mathf.Tau, 32, ring, Ui.Px(1.2f), true);
            else
                DrawArc(p, r, 0, Mathf.Tau, 48, ring, (_hovered == n ? Ui.Px(3) : Ui.Px(2)) * _zoom, true);
            if (!ghost && n.Row.Ratio.Length > 0)
            {
                var rs = font.GetStringSize(n.Row.Ratio, HorizontalAlignment.Left, -1, size);
                DrawString(font, p - rs / 2 + new Vector2(0, rs.Y * 0.78f), n.Row.Ratio, HorizontalAlignment.Left, -1, size, new Color(Ui.Text, dim ? 0.4f : 0.9f));
            }
            if (!Lit(n))
                DrawRing(n, font, lit: false);
        }
        foreach (var n in _nodes)  // the selected and the hovered: their text on top of everything
            if (Lit(n))
                DrawRing(n, font, lit: true);

        // the hint, centered along the bottom
        var hint = T.Get("graph_hint") + "   ·   " + T.Get("graph_legend");
        var hs = font.GetStringSize(hint, HorizontalAlignment.Left, -1, size);
        var hp = new Vector2(Math.Max((Size.X - hs.X) / 2, Ui.Px(10)), Size.Y - Ui.Px(10));
        _strip ??= Ui.Flat(new Color(Ui.Bg, 0.85f), 5, new Color(Ui.Neon, 0.25f), 1, 0);
        DrawStyleBox(_strip, new Rect2(hp.X - Ui.Px(8), hp.Y - hs.Y - Ui.Px(2), hs.X + Ui.Px(16), hs.Y + Ui.Px(8)));
        DrawString(font, new Vector2(hp.X, hp.Y - Ui.Px(2)), hint, HorizontalAlignment.Left, -1, size, Ui.TextDim);
    }

    private static float Width(Font font, string text, int size)
    {
        float w = 0;
        foreach (var ch in text)
            w += font.GetCharSize(ch, size).X;
        return w;
    }

    /// <summary>A node's name along the upper arc of its ring, glyph by glyph, reading left to right over the top
    /// (as the legend of a coin); brighter when lit, faint when the filter leaves the node out.</summary>
    private void DrawRing(Node n, Font font, bool lit)
    {
        if (n.Label.Length == 0)
            return;
        var c = ToScreen(n.Pos);
        bool ghost = _removed.Contains(n.Id);
        bool dim = _matched.Count > 0 && !ghost && !_matched.Contains(n.Id);
        int size = Math.Max((int)Math.Round(n.TextSize * _zoom), 9);  // never smaller than 9 px on screen
        float rText = (n.Radius + Band * 0.55f) * _zoom;
        var label = n.Label;
        float width = Width(font, label, size);
        if (width > MaxArc * rText / 0.78f)  // zoomed out: the text would not fit even squeezed: cut it
        {
            var cut = label.TrimEnd('…');
            while (cut.Length > 2 && Width(font, cut + "…", size) > MaxArc * rText / 0.78f)
                cut = cut[..^1];
            label = cut + "…";
            width = Width(font, label, size);
        }
        float arc = Math.Min(width / rText, MaxArc);
        float startAngle = -Mathf.Pi / 2 - arc / 2;
        float scaleX = width > 0 ? Math.Min(arc * rText / width, 1f) : 1f;  // squeezed a little when the arc is at its most
        var color = ghost ? Ui.TextFaint : dim ? Ui.TextFaint : lit ? Color.Color8(255, 255, 255) : Ui.Text;
        var rim = new Color(Ui.Bg, 0.9f);
        float along = 0;
        foreach (var ch in label)
        {
            float adv = font.GetCharSize(ch, size).X;
            float theta = startAngle + (along + adv / 2) * scaleX / rText;
            var pos = c + new Vector2(Mathf.Cos(theta), Mathf.Sin(theta)) * rText;
            DrawSetTransform(pos, theta + Mathf.Pi / 2, new Vector2(scaleX, 1));
            var at = new Vector2(-adv / 2, 0);
            // a dark rim around the glyph, so it reads over the edges
            foreach (var o in new[] { new Vector2(-1, 0), new Vector2(1, 0), new Vector2(0, -1), new Vector2(0, 1) })
                DrawChar(font, at + o, ch.ToString(), size, rim);
            DrawChar(font, at, ch.ToString(), size, color);
            along += adv;
        }
        DrawSetTransform(Vector2.Zero);
    }
}
