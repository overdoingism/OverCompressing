using System;
using System.Collections.Generic;
using Godot;

namespace OverCompressingUI.UI;

/// <summary>
/// The ground behind the window: a circuit board. Traces are laid out once per window size on a grid as a board
/// is routed: 8 directions, bends of 45° only (a 90° change of course is two 45° bends with a short diagonal between,
/// a mitered corner), each trace ending at a pad; a trace may be a bus of two or three parallel lines whose heads
/// fan out of the node at 45° and whose tails end a little apart (trimmed, run on, or bent away). Now and then a signal leaves a node and runs along a
/// trace; when it reaches the pad at the end, the pad breathes once in warm white and goes dark again. When the
/// window goes to another place, a new board is laid out and slides in from a random side. Everything is drawn here
/// (no particles), advanced by _Process only while the window has the focus and effects are on. Signals and the
/// breathing pads are HDR, so the glow pass makes them shine through the glass panels above.
/// </summary>
public partial class Circuit : Control
{
    private const float Cell = 34f;

    private sealed class Trace
    {
        public List<Vector2> Points = new();
        public List<float> Cum = new();  // distance along the trace at each point
        public float Length;
        public Color Color;
        public bool ToNode;  // ends at a node (a big pad), else at a via
        public bool FromNode = true;  // starts at a node; a bundle's sibling may start at a pin beside it instead
        public float Stagger;  // when it starts to draw itself at the intro (0..0.4 of the reveal)
        public int Bundle;  // traces of one bus share a number (0: alone)
    }

    private sealed class Node
    {
        public Vector2 Pos;
        public float Radius;
        public float Stagger;
        public bool Lit;
    }

    private sealed class Signal
    {
        public Trace Trace = null!;
        public float Dist;
        public float Speed;
        public double Start;  // a word on a bus: the siblings leave a few hundredths of a second apart
    }

    private sealed class Breath
    {
        public Vector2 At;
        public double Age;
    }

    private readonly List<Trace> _traces = new();
    private readonly List<Node> _nodes = new();
    private readonly List<Signal> _signals = new();
    private readonly List<Breath> _breaths = new();
    private Vector2 _offset;
    private float _alpha = 1f;
    private int _seed = 7;
    private Random _rng = new(7);
    private static readonly Color Warm = new(1f, 0.93f, 0.78f);
    private Vector2 _laidOutFor;
    private double _time;
    private double _nextSignal;
    private bool _focused = true;
    public bool Animated = true;
    public float Intensity = 1f;
    private float _reveal = 1f;

    /// <summary>How much of the board is there: 0 nothing, 1 all of it. Rising (the intro), each trace draws itself
    /// from its start after its own delay and each node lights up with one breath; falling (the outro), the traces
    /// draw back into their starts. Signals run only at 1.</summary>
    public float Reveal
    {
        get => _reveal;
        set
        {
            float v = Mathf.Clamp(value, 0, 1);
            bool rising = v > _reveal;
            _reveal = v;
            foreach (var n in _nodes)
            {
                float a = NodeAlpha(n);
                if (rising && !n.Lit && a >= 0.99f)
                {
                    n.Lit = true;
                    _breaths.Add(new Breath { At = n.Pos });
                }
                else if (!rising && n.Lit && a < 0.5f)
                    n.Lit = false;
            }
            if (v < 1f)
                _signals.Clear();
            QueueRedraw();
        }
    }

    private float TraceProgress(Trace t) => Mathf.Clamp((_reveal - t.Stagger) / 0.6f, 0, 1);
    private float NodeAlpha(Node n) => Mathf.Clamp((_reveal - n.Stagger) / 0.3f, 0, 1);

    /// <summary>The first `frac` of a trace, as points.</summary>
    private static Vector2[] Partial(Trace t, float frac)
    {
        float upTo = t.Length * frac;
        var pts = new List<Vector2> { t.Points[0] };
        for (int i = 1; i < t.Points.Count && t.Cum[i] <= upTo; i++)
            pts.Add(t.Points[i]);
        var tip = Along(t, upTo);
        if (tip.DistanceTo(pts[^1]) > 0.5f)
            pts.Add(tip);
        return pts.ToArray();
    }

    public override void _Ready()
    {
        MouseFilter = MouseFilterEnum.Ignore;
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        GetWindow().FocusEntered += () => _focused = true;
        GetWindow().FocusExited += () => _focused = false;
        Resized += () => { if (Size.DistanceTo(_laidOutFor) > Cell) Generate(); };
        Generate();
    }

    // ------------------------------------------------------------------------------------------ the board

    private static readonly (int dx, int dy)[] Dirs = { (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1) };

    /// <summary>Another board, sliding in from a random side (when the window goes to another place).</summary>
    public void Shift()
    {
        _seed = new Random().Next();
        Generate();
        var dir = Dirs[_rng.Next(8)];
        var from = new Vector2(dir.dx, dir.dy).Normalized() * Cell * 4;
        _offset = from;
        _alpha = 0.15f;
        var tw = CreateTween().SetParallel();
        tw.TweenMethod(Callable.From<Vector2>(v => { _offset = v; QueueRedraw(); }), from, Vector2.Zero, 0.7).SetTrans(Tween.TransitionType.Quad).SetEase(Tween.EaseType.Out);
        tw.TweenMethod(Callable.From<float>(v => { _alpha = v; QueueRedraw(); }), 0.15f, 1f, 0.7);
    }

    /// <summary>Lay the board out for the current size: nodes first, then one or two buses walking away from each.</summary>
    private void Generate()
    {
        _laidOutFor = Size;
        _traces.Clear();
        _nodes.Clear();
        _signals.Clear();
        _breaths.Clear();
        _rng = new Random(_seed);
        _cols = Math.Max((int)(Size.X / Cell), 4);
        _rows = Math.Max((int)(Size.Y / Cell), 4);
        _occupied.Clear();
        _nodeCells.Clear();
        _nodeDirs.Clear();
        int wanted = Math.Max(_cols * _rows / 70, 6);
        for (int tries = 0; tries < wanted * 20 && _nodeCells.Count < wanted; tries++)
        {
            var c = (_rng.Next(1, _cols - 1), _rng.Next(1, _rows - 1));
            bool far = true;
            foreach (var n in _nodeCells)
                if (Math.Abs(n.Item1 - c.Item1) < 5 && Math.Abs(n.Item2 - c.Item2) < 5)
                    far = false;
            if (!far)
                continue;
            _nodeCells.Add(c);
            _occupied.Add(c);
            _nodes.Add(new Node { Pos = At(c), Radius = _rng.Next(3, 6), Stagger = (float)_rng.NextDouble() * 0.45f, Lit = _reveal >= 1f });
        }
        int bundle = 0;
        foreach (var start in _nodeCells)
        {
            int branches = _rng.Next(1, 3);
            for (int bch = 0; bch < branches; bch++)
            {
                var route = Walk(start, out int firstDir, out bool toNode);
                if (route is null)
                    continue;
                var color = _rng.Next(10) < 4 ? Ui.Neon2 : Ui.Neon;
                int siblings = _rng.Next(100) switch { < 35 => 0, < 75 => 1, _ => 2 };
                int id = siblings > 0 ? ++bundle : 0;
                AddTrace(route, color, toNode, true, id);
                if (siblings == 0)
                    continue;
                // the bus: parallel lines a cell apart, on alternating sides of the first line
                int side = _rng.Next(2) == 0 ? 1 : -1;
                for (int k = 1; k <= siblings; k++)
                {
                    var sib = Sibling(route, firstDir, side * ((k + 1) / 2), start, out bool sibToNode, out bool sibFromNode);
                    if (sib is not null)
                        AddTrace(sib, color, sibToNode, sibFromNode, id);
                    side = -side;
                }
            }
        }
    }

    private int _cols, _rows;
    private readonly HashSet<(int, int)> _occupied = new();
    private readonly List<(int, int)> _nodeCells = new();
    private readonly Dictionary<(int, int), List<int>> _nodeDirs = new();  // the directions a node's traces leave and arrive by

    /// <summary>Whether a trace may leave (or arrive at) `node` in direction `dir`: at least 90° from every other
    /// trace of that node, so no two meet at the pad in a 45° wedge.</summary>
    private bool DirFree((int, int) node, int dir)
    {
        if (!_nodeDirs.TryGetValue(node, out var used))
            return true;
        foreach (var u in used)
            if (Math.Min(Math.Abs(u - dir), 8 - Math.Abs(u - dir)) < 2)
                return false;
        return true;
    }

    private void UseDir((int, int) node, int dir)
    {
        if (!_nodeDirs.TryGetValue(node, out var used))
            _nodeDirs[node] = used = new List<int>();
        used.Add(dir);
    }

    /// <summary>Whether a step from `c` to `next` is barred: `next` is taken, or the step is diagonal and the two
    /// cells it passes between are both taken (another diagonal runs through that corner: the lines would cross).</summary>
    private bool Barred((int x, int y) c, (int x, int y) next)
    {
        if (!Inside(next) || _occupied.Contains(next))
            return true;
        int dx = next.x - c.x, dy = next.y - c.y;
        return dx != 0 && dy != 0 && _occupied.Contains((c.x + dx, c.y)) && _occupied.Contains((c.x, c.y + dy));
    }

    private Vector2 At((int x, int y) c) => new((c.x + 0.5f) * Cell, (c.y + 0.5f) * Cell);

    private bool NearNode(Vector2 p)
    {
        foreach (var n in _nodes)
            if (n.Pos.DistanceTo(p) <= Cell * 2.3f)
                return true;
        return false;
    }
    private bool Inside((int x, int y) c) => c.x >= 0 && c.y >= 0 && c.x < _cols && c.y < _rows;
    private static (int x, int y) Step((int x, int y) c, int dir, int n = 1) => (c.x + Dirs[dir].dx * n, c.y + Dirs[dir].dy * n);

    /// <summary>A route out of `start`: segments of 2–6 cells, bends of 45° only; six times in ten a bend is followed
    /// by a second one the same way after 1–2 cells (a mitered 90° corner). Stops at the edge, at an occupied cell or
    /// at another node (then it ends there). The cells are marked occupied. Null if too short.</summary>
    private List<(int x, int y)>? Walk((int x, int y) start, out int firstDir, out bool toNode)
    {
        int dir = -1;
        for (int t = 0; t < 8 && dir < 0; t++)
        {
            int d = _rng.Next(8);
            if (DirFree(start, d) && !Barred(start, Step(start, d)))
                dir = d;
        }
        firstDir = Math.Max(dir, 0);
        toNode = false;
        if (dir < 0)
            return null;
        UseDir(start, dir);
        var cur = start;
        var cells = new List<(int x, int y)> { start };
        int segments = _rng.Next(3, 8);
        int turnAgain = 0;  // +1/-1: the second bend of a mitered corner is due after this short segment
        for (int seg = 0; seg < segments; seg++)
        {
            int len = turnAgain != 0 ? _rng.Next(1, 3) : _rng.Next(2, 7);
            int moved = 0;
            for (int i = 0; i < len; i++)
            {
                var next = Step(cur, dir);
                if (!Inside(next))
                    break;
                if (_nodeCells.Contains(next) && next != start)
                {
                    int arrival = (dir + 4) % 8;  // as the node sees it
                    if (!DirFree(next, arrival) || (Dirs[dir].dx != 0 && Dirs[dir].dy != 0 && Barred(cur, next) && _occupied.Contains((cur.x + Dirs[dir].dx, cur.y))))
                        break;  // it would meet another trace at the pad in a wedge: end short, at a via
                    UseDir(next, arrival);
                    cells.Add(next);
                    cur = next;
                    moved++;
                    toNode = true;
                    break;
                }
                if (Barred(cur, next))
                    break;
                cur = next;
                _occupied.Add(cur);
                cells.Add(cur);
                moved++;
            }
            if (toNode || moved < len)
                break;
            int turn = turnAgain != 0 ? turnAgain : (_rng.Next(2) == 0 ? 1 : -1);
            dir = (dir + turn + 8) % 8;
            turnAgain = turnAgain == 0 && _rng.Next(10) < 6 ? turn : 0;
        }
        if (cells.Count < 3)
        {
            foreach (var c in cells)
                if (c != start)
                    _occupied.Remove(c);
            _nodeDirs[start].Remove(firstDir);
            if (toNode)
                _nodeDirs[cells[^1]].Remove((dir + 4) % 8);
            return null;
        }
        return cells;
    }

    /// <summary>A line of the bus beside `route`, `k` cells to the side (sign: which side): the route moved over,
    /// cut where it meets anything. It never enters a pad (that would make a wedge with the first line): it starts
    /// at a pin beside the node, or runs back past it; its tail is trimmed, runs on, bends 45° away, or ends at a pin
    /// beside the node the first line reached. Null if what is left is too short.</summary>
    private List<(int x, int y)>? Sibling(List<(int x, int y)> route, int firstDir, int k, (int x, int y) node, out bool toNode, out bool fromNode)
    {
        toNode = false;
        fromNode = false;
        int perp = (firstDir + 2) % 8;
        var n = Dirs[perp];
        int sign = Math.Sign(k);
        (int x, int y) Shifted((int x, int y) c) => (c.x + n.dx * k, c.y + n.dy * k);
        var cells = new List<(int x, int y)>();
        // the body: the route from its second cell on, moved over, up to the first barred step
        var prev = Shifted(route[0]);
        for (int i = 1; i < route.Count; i++)
        {
            var c = Shifted(route[i]);
            if (!Inside(c) || _nodeCells.Contains(c) || (i == 1 ? _occupied.Contains(c) : Barred(prev, c)))
                break;
            cells.Add(c);
            prev = c;
        }
        if (cells.Count < 2)
            return null;
        // the head: a pin beside the node, or 1–2 cells back past it as if it came from off the board
        if (_rng.Next(10) < 4)
        {
            int back = (firstDir + 4) % 8;
            var c = cells[0];
            for (int i = 0; i < _rng.Next(1, 3); i++)
            {
                var nx = Step(c, back);
                if (_nodeCells.Contains(nx) || Barred(c, nx))
                    break;
                cells.Insert(0, nx);
                c = nx;
            }
        }
        // the tail: the route's last direction
        var last = cells[^1];
        var before = cells[^2];
        int lastDir = Array.FindIndex(Dirs, d => d.dx == Math.Sign(last.x - before.x) && d.dy == Math.Sign(last.y - before.y));
        if (lastDir < 0)
            lastDir = firstDir;
        int tail = _rng.Next(10);
        var endNode = route[^1];
        bool besideEnd = _nodeCells.Contains(endNode) && Math.Abs(last.x - endNode.x) <= 2 && Math.Abs(last.y - endNode.y) <= 2;
        if (besideEnd && tail < 6)
            toNode = false;  // ends at a pin beside that node, as it is
        else if (tail < 3)
        {
            int trim = Math.Min(_rng.Next(1, 4), cells.Count - 3);  // ending short
            if (trim > 0)
                cells.RemoveRange(cells.Count - trim, trim);
        }
        else if (tail < 6)
        {
            var c = last;  // running on
            for (int i = 0; i < _rng.Next(1, 4); i++)
            {
                var nx = Step(c, lastDir);
                if (_nodeCells.Contains(nx) || Barred(c, nx))
                    break;
                cells.Add(nx);
                c = nx;
            }
        }
        else if (tail < 9)
        {
            // bending 45° away from the first line, 1–3 cells
            int away = (lastDir + 1) % 8;
            if (Dirs[away].dx * n.dx * sign + Dirs[away].dy * n.dy * sign < 0)
                away = (lastDir + 7) % 8;
            var c = last;
            for (int i = 0; i < _rng.Next(1, 4); i++)
            {
                var nx = Step(c, away);
                if (_nodeCells.Contains(nx) || Barred(c, nx))
                    break;
                cells.Add(nx);
                c = nx;
            }
        }
        if (cells.Count < 3)
            return null;
        foreach (var c in cells)
            _occupied.Add(c);
        return cells;
    }

    /// <summary>A trace from cells: only the corners are kept as points.</summary>
    private void AddTrace(List<(int x, int y)> cells, Color color, bool toNode, bool fromNode, int bundle)
    {
        var pts = new List<Vector2> { At(cells[0]) };
        for (int i = 1; i < cells.Count - 1; i++)
        {
            var d1 = (Math.Sign(cells[i].x - cells[i - 1].x), Math.Sign(cells[i].y - cells[i - 1].y));
            var d2 = (Math.Sign(cells[i + 1].x - cells[i].x), Math.Sign(cells[i + 1].y - cells[i].y));
            if (d1 != d2)
                pts.Add(At(cells[i]));
        }
        pts.Add(At(cells[^1]));
        var t = new Trace { Points = pts, ToNode = toNode, FromNode = fromNode, Color = color, Stagger = (float)_rng.NextDouble() * 0.4f, Bundle = bundle };
        float cum = 0;
        t.Cum.Add(0);
        for (int i = 1; i < pts.Count; i++)
        {
            cum += pts[i - 1].DistanceTo(pts[i]);
            t.Cum.Add(cum);
        }
        t.Length = cum;
        _traces.Add(t);
    }

    private static Vector2 Along(Trace t, float dist)
    {
        dist = Mathf.Clamp(dist, 0, t.Length);
        for (int i = 1; i < t.Points.Count; i++)
            if (dist <= t.Cum[i])
            {
                float seg = t.Cum[i] - t.Cum[i - 1];
                float f = seg > 0 ? (dist - t.Cum[i - 1]) / seg : 0;
                return t.Points[i - 1].Lerp(t.Points[i], f);
            }
        return t.Points[^1];
    }

    // ------------------------------------------------------------------------------------------ life

    public override void _Process(double delta)
    {
        if (!Animated || !_focused || _traces.Count == 0)
            return;
        _time += delta;
        float dt = (float)Math.Min(delta, 0.05);
        if (_time >= _nextSignal && _reveal >= 1f)
        {
            _nextSignal = _time + 0.22 + _rng.NextDouble() * 0.7;
            var t = _traces[_rng.Next(_traces.Count)];
            float speed = 260 + (float)_rng.NextDouble() * 220;
            _signals.Add(new Signal { Trace = t, Speed = speed, Start = _time });
            if (t.Bundle > 0 && _rng.Next(10) < 7)  // a word on the bus: its other lines follow a little apart
            {
                int j = 0;
                foreach (var other in _traces)
                    if (other.Bundle == t.Bundle && other != t)
                        _signals.Add(new Signal { Trace = other, Speed = speed, Start = _time + 0.035 * ++j });
            }
        }
        for (int i = _signals.Count - 1; i >= 0; i--)
        {
            var s = _signals[i];
            if (_time < s.Start)
                continue;
            s.Dist += s.Speed * dt;
            if (s.Dist >= s.Trace.Length)
            {
                _signals.RemoveAt(i);
                _breaths.Add(new Breath { At = s.Trace.Points[^1] });
            }
        }
        for (int i = _breaths.Count - 1; i >= 0; i--)
        {
            _breaths[i].Age += delta;
            if (_breaths[i].Age > 2.2)
                _breaths.RemoveAt(i);
        }
        QueueRedraw();
    }

    public override void _Draw()
    {
        float k = Intensity * _alpha;
        DrawSetTransform(_offset);
        // the traces, with a faint halo
        foreach (var t in _traces)
        {
            float progress = TraceProgress(t);
            if (progress <= 0f)
                continue;
            var pts = progress >= 1f ? t.Points.ToArray() : Partial(t, progress);
            if (pts.Length < 2)
                continue;
            float ta = t.Color == Ui.Neon2 ? 1.3f : 1f;  // magenta reads darker than cyan at the same alpha
            DrawPolyline(pts, new Color(t.Color, 0.045f * k * ta), 5f, true);
            DrawPolyline(pts, new Color(t.Color, 0.22f * k * ta), 1.4f, true);
            var end = pts[^1];
            if (progress < 1f)
                Fx.DrawGlow(this, end, 10f, Ui.Hdr(t.Color, 1.4f) with { A = 0.8f * k });  // the pen drawing the trace
            else if (!t.ToNode)
            {
                if (t.Bundle > 0 && !t.FromNode && NearNode(end))
                    DrawRect(new Rect2(end - new Vector2(2.6f, 2.6f), new Vector2(5.2f, 5.2f)), new Color(t.Color, 0.45f * k));  // a pin
                else
                {
                    DrawCircle(end, 3.2f, new Color(Ui.Bg, 1f));
                    DrawArc(end, 3.2f, 0, Mathf.Tau, 16, new Color(t.Color, 0.5f * k), 1.4f, true);
                }
            }
            if (!t.FromNode)
                DrawRect(new Rect2(pts[0] - new Vector2(2.6f, 2.6f), new Vector2(5.2f, 5.2f)), new Color(t.Color, 0.45f * k));  // a pin
        }
        // the nodes: a pad with a ring
        foreach (var n in _nodes)
        {
            float na = NodeAlpha(n) * k;
            if (na <= 0f)
                continue;
            DrawCircle(n.Pos, n.Radius + 4, new Color(Ui.Neon, 0.05f * na));
            DrawArc(n.Pos, n.Radius + 2.5f, 0, Mathf.Tau, 24, new Color(Ui.Neon, 0.35f * na), 1.2f, true);
            DrawCircle(n.Pos, n.Radius * 0.6f, new Color(Ui.Neon, 0.5f * na));
        }
        if (!Animated)
            return;
        // the signals: a soft head with a tail that fades along the trace (one gradient line, not beads)
        var sig = new List<Vector2>();
        var cols = new List<Color>();
        foreach (var s in _signals)
        {
            if (_time < s.Start)
                continue;
            const float tail = 54f;
            sig.Clear();
            cols.Clear();
            for (float d = Math.Max(s.Dist - tail, 0); d <= s.Dist; d += 2.5f)
            {
                float a = (d - (s.Dist - tail)) / tail;
                sig.Add(Along(s.Trace, d));
                cols.Add(Ui.Hdr(s.Trace.Color, 0.8f + 0.9f * a * a) with { A = 0.75f * a * a });
            }
            var head = Along(s.Trace, s.Dist);
            if (sig.Count >= 2)
            {
                sig.Add(head);
                cols.Add(Ui.Hdr(s.Trace.Color, 1.7f) with { A = 0.8f });
                DrawPolylineColors(sig.ToArray(), cols.ToArray(), 2.4f, true);
            }
            Fx.DrawGlow(this, head, 16f, Ui.Hdr(s.Trace.Color, 1.5f) with { A = 0.75f });
        }
        // the pads a signal reached: a slow breath of warm white (one soft falloff), and dark again
        foreach (var b in _breaths)
        {
            float age = (float)b.Age;
            float env = age < 0.35f ? Mathf.SmoothStep(0, 1, age / 0.35f) : Mathf.Clamp(1 - (age - 0.35f) / 1.8f, 0, 1);
            env *= env;
            Fx.DrawGlow(this, b.At, 52f, new Color(Warm, 0.5f * env));
            Fx.DrawGlow(this, b.At, 16f, Ui.Hdr(Warm, 1.3f) with { A = 0.85f * env });
        }
    }
}
