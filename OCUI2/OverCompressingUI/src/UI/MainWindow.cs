using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Godot;
using OverCompressingUI.Core;

namespace OverCompressingUI.UI;

/// <summary>
/// The window: a file browser, as in other archivers. Folders and files are listed as they are on disk; a pack (a
/// pack folder holding ocpack.json, or a pack file, .oczip) opens as the list of its models, each with what it needs
/// (or as a tree by base, or as a graph); a model opens as the list of its files. Every action runs llm_archive.py in
/// a process of its own, just as on the command line, and shows its output below. A port of llm_archive_gui.py
/// (Tkinter) to the Godot engine, feature for feature, with the same words.
/// </summary>
public partial class MainWindow : Control
{
    // ------------------------------------------------------------------------------------------ constants
    private static readonly Dictionary<string, (string key, int width, bool right)> Columns = new()
    {
        ["files"] = ("col_files", 50, true), ["size"] = ("col_size", 90, true), ["stored"] = ("col_stored", 90, true),
        ["ratio"] = ("col_ratio", 64, true), ["type"] = ("col_type", 150, false), ["needs"] = ("col_needs", 220, false),
        ["packed"] = ("col_packed", 130, false), ["modified"] = ("col_modified", 130, false),
    };
    private static readonly string[] Fitted = { "type", "needs" };  // as wide as what they hold, as the name is
    private static readonly Dictionary<string, string?[]> Buttons = new()
    {
        ["folder"] = new[] { "pack_to", null, "refresh", "remove" },
        ["pack"] = new[] { "pack_in", "save", null, "extract", "verify", null, "rebase", null, "remove", "rename" },
        ["model"] = new[] { "add_file", null, "extract", "verify", null, "rebase", null, "remove", "rename" },
    };
    private const double WatchSeconds = 5;
    private const string Below = "\0below";

    // ------------------------------------------------------------------------------------------ state
    private Settings _settings = null!;
    private PackReader _reader = null!;
    private JobRunner _runner = null!;
    private string _python = "", _tool = "";
    private string _versions = "";
    private Place? _place;
    private string? _where;
    private List<Row> _listing = new();
    private readonly Dictionary<TreeItem, Row> _rows = new();
    private readonly Dictionary<string, TreeItem> _items = new();
    private HashSet<string> _selected = new();
    private HashSet<string> _matched = new();
    private string? _anchor;
    private bool _quiet;
    private bool _insidePack;
    private string _sortBy = "name";
    private bool _descending;
    private string _viewMode = "list";
    private object? _seen;  // the place's stamp as last seen: (mtime, size), "gone", or null
    private double _looked;
    private DamageMarks? _marks;
    private bool _quitAfter;
    private bool _listingNow;
    private string? _laidOut;
    private readonly Dictionary<(string column, bool byBase), float> _naturals = new();
    private float _fitted = -1;
    private string? _dragFrom;
    private HashSet<string> _dragKeep = new();
    private float _dragY;
    private Action? _done;
    private List<Row> _lastRows = new();

    // ------------------------------------------------------------------------------------------ nodes
    private Backdrop _backdrop = null!;
    private Button _up = null!;
    private LineEdit _path = null!;
    private HBoxContainer _bar = null!;
    private Control _modeSlot = null!;
    private Label _modeNote = null!;
    private readonly Dictionary<string, Button> _buttons = new();
    private readonly List<Control> _barItems = new();
    private LineEdit _filter = null!;
    private Label _hint = null!;
    private HBoxContainer _views = null!;
    private readonly Dictionary<string, Button> _viewButtons = new();
    private VSplitContainer _split = null!;
    private Tree _tree = null!;
    private GraphView _graph = null!;
    private HBoxContainer _graphBox = null!;
    private GraphPanel _graphPanel = null!;
    private Label _status = null!;
    private RichTextLabel _log = null!;
    private readonly List<string> _logLines = new();
    private PopupMenu _menu = null!;
    private readonly List<string> _menuKeys = new();
    private BusyOverlay _busy = null!;
    private Timer _watchTimer = null!;
    private readonly Dictionary<string, Action> _commands = new();
    private readonly Dictionary<string, int> _col = new();  // column name -> tree column index for the view shown

    // ============================================================================================ start

    public override void _Ready()
    {
        GetTree().AutoAcceptQuit = false;
        var window = GetWindow();
        // The display's scale: Windows reports it as a DPI (96 = 100 %, 168 = 175 %); screen_get_scale is 1 there.
        Ui.Scale = Mathf.Max(Mathf.Max(DisplayServer.ScreenGetScale(), DisplayServer.ScreenGetDpi() / 96f), 1f);
        if (DevHooks.Args().TryGetValue("scale", out var forced) && float.TryParse(forced, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var fs))
            Ui.Scale = fs;
        window.ContentScaleFactor = Ui.Scale;  // every canvas unit becomes Scale pixels: layout and fonts scale together
        Ui.BuildTheme();
        window.Theme = Ui.Theme;
        Theme = Ui.Theme;
        _settings = Settings.Load(ProjectSettings.GlobalizePath("user://settings.json"));
        Ui.Effects = _settings.Effects;
        Fx.Sfx.Enabled = _settings.Sounds;
        var size = new Vector2I(_settings.WindowWidth > 0 ? _settings.WindowWidth : (int)(1200 * Ui.Scale), _settings.WindowHeight > 0 ? _settings.WindowHeight : (int)(780 * Ui.Scale));
        window.Size = size;
        window.MinSize = new Vector2I((int)(760 * Ui.Scale), (int)(460 * Ui.Scale));
        var screen = DisplayServer.ScreenGetUsableRect();
        window.Position = screen.Position + (screen.Size - size) / 2;
        if (_settings.WindowMaximized)
            window.Mode = Window.ModeEnum.Maximized;
        window.Title = "OverCompressing";
        _viewMode = _settings.ViewMode is "list" or "base" or "graph" ? _settings.ViewMode : "list";
        _sortBy = Columns.ContainsKey(_settings.SortBy) || _settings.SortBy == "name" ? _settings.SortBy : "name";
        _descending = _settings.SortDescending;
        Build();
        PlayIntro();
        _ = Startup();
    }

    private async Task Startup()
    {
        try
        {
            var found = ToolLocator.Find(_settings, Path.GetDirectoryName(OS.GetExecutablePath()) ?? ".");
            while (found is null)
            {
                var ok = await SettingsDialog(firstRun: true);
                if (!ok)
                {
                    GetTree().Quit();
                    return;
                }
                found = ToolLocator.Find(_settings, Path.GetDirectoryName(OS.GetExecutablePath()) ?? ".");
            }
            await UseTool(found.Python, found.Tool);
            var start = StartPlace();
            Go(start ?? new Place(System.Environment.CurrentDirectory, pack: Place.IsPack(System.Environment.CurrentDirectory)));
            _ = DevHooks.Run(this);
        }
        catch (Exception e)
        {
            GD.PrintErr(e);
        }
    }

    private async Task UseTool(string python, string tool)
    {
        _python = python;
        _tool = tool;
        _reader = new PackReader(python, tool);
        _runner = new JobRunner(python, tool);
        _runner.OnLog = Write;
        _runner.OnLine = line => { _status.Text = $"{_runner.Current?.Title}:  {line}"; _busy.SetLine(line); };
        _runner.OnProgress = e =>
        {
            var step = e.Str("step") ?? "";
            if (!string.IsNullOrEmpty(e.Str("item")))
                step += $"  ·  {e.Str("item")}";
            _busy.SetStep(step);
        };
        _runner.OnJobStarted = job =>
        {
            var counted = _runner.JobTotal > 1 ? $"  ({_runner.JobNumber}/{_runner.JobTotal})" : "";
            if (_runner.JobNumber == 1)
                { _veil.Hold(); _busy.Open(job.Title + counted); }
            else
            {
                _busy.SetTitle(job.Title + counted);
                _busy.SetLine("");
                _busy.SetStep("");
            }
            _status.Text = job.Title + " …";
            UpdateButtons();
        };
        _runner.OnFinished = JobsFinished;
        _runner.OnCancelled = () => Finish();
        var (ok, text) = await _reader.VersionAsync();
        var cli = ok ? text.Replace("OverCompressing CLI", "CLI").Split(", format")[0] : "CLI ?";
        _versions = $"GUI {T.GuiVersion} (Godot {Engine.GetVersionInfo()["string"]}) · {cli}";
        GetWindow().Title = $"OverCompressing {_versions}" + (_place is not null ? $" — {_place.Text()}" : "");
    }

    private Place? StartPlace()
    {
        foreach (var arg in OS.GetCmdlineUserArgs().Concat(OS.GetCmdlineArgs()))
        {
            if (arg.StartsWith("--") || arg.StartsWith("res://"))
                continue;
            var p = Place.FromText(arg);
            if (p is not null)
                return p;
        }
        var kit = Path.GetDirectoryName(Path.GetDirectoryName(_tool));  // run from an offline kit (<pack>/_kit/tools): that pack
        if (kit is not null && Path.GetFileName(kit) == "_kit" && Path.GetDirectoryName(kit) is { } packDir && Place.IsPack(packDir))
            return new Place(packDir, pack: true);
        if (!string.IsNullOrEmpty(_settings.LastPlace))
            return Place.FromText(_settings.LastPlace);
        return null;
    }

    // ============================================================================================ layout

    private void Build()
    {
        Fx.Sfx.Init(this);
        _backdrop = new Backdrop();
        AddChild(_backdrop);
        _glow = Fx.Glow();
        _glow.Environment.GlowEnabled = Ui.Effects;
        AddChild(_glow);
        _circuit = new Circuit { Animated = Ui.Effects, Intensity = Ui.Effects ? 1f : 0.6f };
        AddChild(_circuit);
        Input.SetCustomMouseCursor(Icons.Cursor(), Input.CursorShape.Arrow, new Vector2(4, 3) * Ui.Scale);
        Input.SetCustomMouseCursor(Icons.CursorBeam(), Input.CursorShape.Ibeam, new Vector2(16, 16) * Ui.Scale);
        var margin = _ui = new MarginContainer();
        margin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        margin.AddThemeConstantOverride("margin_left", Ui.Px(12));
        margin.AddThemeConstantOverride("margin_right", Ui.Px(12));
        margin.AddThemeConstantOverride("margin_top", Ui.Px(10));
        margin.AddThemeConstantOverride("margin_bottom", Ui.Px(10));
        AddChild(margin);
        var root = Ui.VBox(8);
        margin.AddChild(root);

        // the path bar
        var top = Ui.HBox(8);
        root.AddChild(top);
        _up = Ui.Button(T.Get("up"), GoUp);
        _up.AddThemeFontOverride("font", Ui.Bold);
        _up.CustomMinimumSize = new Vector2(Ui.Px(52), 0);
        _up.TooltipText = "Up (Backspace, Alt+↑)";
        top.AddChild(_up);
        _path = Ui.Edit("", "A folder, a pack folder, or a pack file (.oczip)");
        _path.TextSubmitted += _ => GoToText();
        top.AddChild(_path);
        var settings = Ui.Button(T.Get("settings"), () => _ = SettingsDialog(false));
        settings.TooltipText = T.Get("settings_title");
        top.AddChild(settings);

        // the buttons of the place, and the legend
        _bar = Ui.HBox(4);
        root.AddChild(_bar);
        _modeSlot = new HBoxContainer();
        _bar.AddChild(_modeSlot);
        _modeNote = Ui.Label("", Ui.TextDim, size: Ui.SmallFontSize);
        _bar.AddChild(_modeNote);
        _bar.AddChild(new Control { CustomMinimumSize = new Vector2(Ui.Px(6), 0), MouseFilter = MouseFilterEnum.Ignore });
        _commands["pack_to"] = () => _ = Pack();
        _commands["pack_in"] = () => _ = Pack();
        _commands["add_file"] = () => _ = AddFile();
        _commands["save"] = () => _ = Export();
        _commands["extract"] = () => _ = Extract();
        _commands["verify"] = () => _ = Verify();
        _commands["remove"] = () => _ = Remove();
        _commands["rename"] = () => _ = Rename();
        _commands["rebase"] = () => _ = Rebase();
        _commands["refresh"] = () => Refresh();

        // the filter, the views, the keys
        var tools = Ui.HBox(6);
        root.AddChild(tools);
        tools.AddChild(Ui.Label(T.Get("filter"), Ui.TextDim));
        _filter = Ui.Edit("", "name holds…");
        _filter.CustomMinimumSize = new Vector2(Ui.Px(300), 0);
        _filter.SizeFlagsHorizontal = SizeFlags.ShrinkBegin;
        _filter.TextChanged += _ => { if (!_quiet) Filtered(); };
        tools.AddChild(_filter);
        var clear = Ui.Button(T.Get("filter_clear"), () => _filter.Text = "");
        clear.Pressed += Filtered;
        tools.AddChild(clear);
        tools.AddChild(new Control { CustomMinimumSize = new Vector2(Ui.Px(18), 0), MouseFilter = MouseFilterEnum.Ignore });  // a little apart from the filter
        _views = Ui.HBox(2);
        var group = new ButtonGroup();
        foreach (var (key, mode) in new[] { ("view_list", "list"), ("view_base", "base"), ("view_graph", "graph") })
        {
            var b = new Button { Text = T.Get(key), ToggleMode = true, ButtonGroup = group, FocusMode = FocusModeEnum.None, ButtonPressed = _viewMode == mode };
            Ui.Tint(b, Ui.Amber);  // the views in their own color, apart from the actions
            b.Pressed += () => { _viewMode = mode; _settings.ViewMode = mode; Filtered(); };
            Ui.Animate(b);
            _viewButtons[mode] = b;
            _views.AddChild(b);
        }
        tools.AddChild(_views);
        tools.AddChild(Ui.Spacer());
        _hint = Ui.Label(T.Get("hint_keys"), Ui.TextDim, size: Ui.SmallFontSize);
        tools.AddChild(_hint);

        // the list and the log
        _split = new VSplitContainer { SizeFlagsVertical = SizeFlags.ExpandFill };
        root.AddChild(_split);
        var listBox = Ui.VBox(4);
        listBox.SizeFlagsVertical = SizeFlags.ExpandFill;
        _split.AddChild(listBox);
        var area = new Control { SizeFlagsVertical = SizeFlags.ExpandFill, MouseFilter = MouseFilterEnum.Ignore };
        listBox.AddChild(area);
        _tree = new Tree
        {
            HideRoot = true, HideFolding = true, ScrollHorizontalEnabled = true, ScrollVerticalEnabled = true,
            ColumnTitlesVisible = true, SelectMode = Tree.SelectModeEnum.Single, FocusMode = FocusModeEnum.All,
            AllowReselect = false, AllowRmbSelect = false, AllowSearch = false,
        };
        _tree.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _tree.GuiInput += TreeInput;
        _tree.ColumnTitleClicked += (column, button) => { if (button == (long)MouseButton.Left) Sort(ColumnName((int)column)); };
        _tree.Resized += () => { if (Mathf.Abs(_tree.Size.X - _fitted) > 1) FitColumns(); };
        area.AddChild(_tree);
        _graphBox = Ui.HBox(8);
        _graphBox.Visible = false;
        _graphBox.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        _graph = new GraphView { SizeFlagsHorizontal = SizeFlags.ExpandFill, SizeFlagsVertical = SizeFlags.ExpandFill };
        _graph.NodeClicked += (id, ctrl, shift) => ClickSelect(id, ctrl, shift);
        _graph.NodeDoubleClicked += id => { if (_place is not null && _items.TryGetValue(id, out var item)) OpenRow(item); };
        _graph.EmptyClicked += ctrl => { if (!ctrl) Select(new HashSet<string>()); };
        _graphBox.AddChild(_graph);
        _graphPanel = new GraphPanel { SizeFlagsVertical = SizeFlags.ExpandFill };
        _graphPanel.NameClicked += id => { if (_items.ContainsKey(id)) { _anchor = id; Select(new HashSet<string> { id }); } };
        _graphBox.AddChild(_graphPanel);
        area.AddChild(_graphBox);
        _sweep = new Fx.Sweep();
        area.AddChild(_sweep);
        _status = Ui.Label("", Ui.TextDim, size: Ui.SmallFontSize);
        _status.ClipText = true;
        _status.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        _status.MouseFilter = MouseFilterEnum.Pass;
        var statusBox = Ui.Flat(new Color(Ui.Bg, 0.75f), 5, null, 0, 0);  // a dark strip: the horizon's light runs behind it
        statusBox.ContentMarginLeft = statusBox.ContentMarginRight = Ui.Px(8);
        statusBox.ContentMarginTop = statusBox.ContentMarginBottom = Ui.Px(3);
        _status.AddThemeStyleboxOverride("normal", statusBox);
        _status.AddThemeColorOverride("font_color", new Color("#b8c7e6"));
        listBox.AddChild(_status);
        _log = new RichTextLabel
        {
            BbcodeEnabled = true, ScrollFollowing = true, SelectionEnabled = true, ContextMenuEnabled = true, FitContent = false,
            AutowrapMode = TextServer.AutowrapMode.Off, SizeFlagsVertical = SizeFlags.ExpandFill, FocusMode = FocusModeEnum.Click,
        };
        _log.CustomMinimumSize = new Vector2(0, Ui.Px(90));
        _logBox = (StyleBoxFlat)Ui.Theme.GetStylebox("normal", "RichTextLabel").Duplicate();
        _log.AddThemeStyleboxOverride("normal", _logBox);
        _split.AddChild(_log);
        _split.Dragged += _ => RememberSplit();

        // the right-click menu, the busy veil, the watch
        _menu = new PopupMenu();
        _menu.IdPressed += id => { if (id >= 0 && id < _menuKeys.Count) _commands[_menuKeys[(int)id]](); };
        AddChild(_menu);
        _veil = new Fx.Veil();
        AddChild(_veil);
        OcDialog.OpenCountChanged += n =>
        {
            if (n > _dialogsOpen) _veil.Hold();
            else if (n < _dialogsOpen) _veil.Release();
            _dialogsOpen = n;
        };
        _busy = new BusyOverlay();
        _busy.OnCancel = Cancel;
        AddChild(_busy);
        _trail = new CursorTrail();
        AddChild(_trail);
        _watchTimer = new Timer { WaitTime = WatchSeconds, Autostart = true, OneShot = false };
        _watchTimer.Timeout += () => Watch(false);
        AddChild(_watchTimer);
        Resized += () => CallDeferred(nameof(ApplySplit));
        CallDeferred(nameof(ApplySplit));
    }

    private void ApplySplit()
    {
        float total = _split.Size.Y;
        if (total <= 0)
            return;
        float share = Mathf.Clamp(_settings.LogShare, 0.1f, 0.7f);
        _split.SplitOffset = (int)(total * (1 - share)) - (int)(total / 2);
    }

    private void RememberSplit()
    {
        float total = _split.Size.Y;
        if (total <= 0)
            return;
        float top = total / 2 + _split.SplitOffset;
        _settings.LogShare = Mathf.Clamp(1 - top / total, 0.05f, 0.8f);
    }

    /// <summary>The buttons, and the right-click menu, of a view (Buttons).</summary>
    private void LayOut(string view)
    {
        if (view == _laidOut)
            return;
        foreach (var w in _barItems)
            w.QueueFree();
        _barItems.Clear();
        _buttons.Clear();
        _laidOut = view;
        _menu.Clear();
        _menuKeys.Clear();
        foreach (var key in Buttons[view])
        {
            Control widget;
            if (key is null)
            {
                widget = Ui.VSep();
                _menu.AddSeparator();
            }
            else
            {
                var b = Ui.Button(T.Get(key), _commands[key]);
                _buttons[key] = b;
                if (_introPlaying)  // the intro: the buttons come in one after another
                {
                    b.Modulate = new Color(1, 1, 1, 0);
                    CreateTween().TweenProperty(b, "modulate", Colors.White, 0.25).SetDelay(0.05 * _buttons.Count);
                }
                widget = b;
                _menu.AddItem(T.Get(key), _menuKeys.Count);
                _menuKeys.Add(key);
            }
            _bar.AddChild(widget);
            _barItems.Add(widget);
        }
        if (_legend is null)
        {
            // the key to the row colors: one quiet word at the right; the rows themselves, as they look, on hover
            _legend = Ui.HBox(4);
            _legend.AddChild(Ui.Spacer());
            _legendWord = Ui.DisplayLabel(T.Get("legend"), Ui.Neon, Ui.SmallFontSize + 1);
            // a quiet frame whose edge cycles slowly through the spectrum (the owner's wish for a touch of RGB)
            _legendBox = Ui.Flat(new Color(Ui.Neon, 0.04f), 6, new Color(Ui.Neon, 0.3f), 1, 0);
            _legendBox.ContentMarginLeft = _legendBox.ContentMarginRight = Ui.Px(9);
            _legendBox.ContentMarginTop = _legendBox.ContentMarginBottom = Ui.Px(3);
            _legendBox.ShadowColor = new Color(Ui.Neon, 0.12f);
            _legendBox.ShadowSize = Ui.Px(6);
            _legendWord.AddThemeStyleboxOverride("normal", _legendBox);
            _legendWord.MouseFilter = MouseFilterEnum.Stop;
            _legendWord.MouseDefaultCursorShape = CursorShape.Help;
            _legendWord.MouseEntered += () => { _legendHot = true; _legendWord.AddThemeColorOverride("font_color", Color.Color8(255, 255, 255)); Fx.Sfx.Tick(); ShowLegend(); };
            _legendWord.MouseExited += () => { _legendHot = false; _legendWord.AddThemeColorOverride("font_color", Ui.Neon); HideLegendSoon(); };
            _legend.AddChild(_legendWord);
            _legend.SizeFlagsHorizontal = SizeFlags.ExpandFill;
        }
        if (_legend.GetParent() is null)
            _bar.AddChild(_legend);
        _bar.MoveChild(_legend, _bar.GetChildCount() - 1);
    }

    private HBoxContainer? _legend;
    private Label _legendWord = null!;
    private PanelContainer? _legendPanel;
    private StyleBoxFlat? _legendBox;
    private bool _legendHot;

    /// <summary>The Colors word's edge: a slow sweep through the hues, brighter under the mouse; still with effects off.</summary>
    private void TickLegend()
    {
        if (_legendBox is null)
            return;
        float hue = Ui.Effects ? (float)(_time * 0.08 % 1.0) : 0.5f;
        var c = Color.FromHsv(hue, 0.75f, 1f);
        _legendBox.BorderColor = new Color(c, _legendHot ? 0.95f : 0.45f);
        _legendBox.BgColor = new Color(c, _legendHot ? 0.14f : 0.04f);
        _legendBox.ShadowColor = new Color(c, _legendHot ? 0.3f : 0.12f);
    }

    /// <summary>The key, under the word: each kind as a row of the list looks, its tint under its word and icon.</summary>
    private void ShowLegend()
    {
        if (_legendPanel is null)
        {
            _legendPanel = Ui.Card(12, 10);
            var solid = Ui.Glass(10, 12);
            solid.BgColor = new Color(Ui.PanelSolid, 0.8f);  // over the board, the key must not blend with it
            _legendPanel.AddThemeStyleboxOverride("panel", solid);
            _legendPanel.MouseFilter = MouseFilterEnum.Stop;
            _legendPanel.MouseExited += HideLegendSoon;
            _legendPanel.MouseEntered += () => _legendPanel.Visible = true;
            var box = Ui.VBox(4);
            _legendPanel.AddChild(box);
            box.AddChild(Ui.Note(T.Get("legend_note")));
            box.AddChild(Ui.Gap(2));
            foreach (var (kind, label) in Ui.Legend.Append(("selected", T.Get("legend_selected"))))
            {
                var row = new PanelContainer();
                var tint = kind == "selected" ? Ui.Selection : Ui.KindTint[kind];
                // the tint as it reads in the list (where the lit board shows through): lifted, so it does not sink into the card
                var over = Ui.PanelSolid.Lerp(new Color(tint.R, tint.G, tint.B), Mathf.Clamp(tint.A * 2.4f, 0.26f, 0.55f));
                var sb = Ui.Flat(over, 4, null, 0, 0);
                sb.ContentMarginLeft = Ui.Px(8);
                sb.ContentMarginRight = Ui.Px(14);
                sb.ContentMarginTop = sb.ContentMarginBottom = Ui.Px(5);
                row.AddThemeStyleboxOverride("panel", sb);
                row.CustomMinimumSize = new Vector2(Ui.Px(220), 0);
                var line = Ui.HBox(8);
                if (kind != "selected")
                    line.AddChild(new TextureRect { Texture = Icons.Kind(kind), StretchMode = TextureRect.StretchModeEnum.KeepCentered, CustomMinimumSize = new Vector2(Ui.Px(18), Ui.Px(18)) });
                else
                    line.AddChild(new Control { CustomMinimumSize = new Vector2(Ui.Px(18), Ui.Px(18)) });
                var text = Ui.Label(label, Ui.Text);
                text.AddThemeColorOverride("font_outline_color", new Color(0.01f, 0.015f, 0.04f, 0.9f));
                text.AddThemeConstantOverride("outline_size", 2);
                line.AddChild(text);
                row.AddChild(line);
                box.AddChild(row);
            }
            AddChild(_legendPanel);
        }
        _legendPanel.Visible = true;
        _legendPanel.ResetSize();
        var at = _legendWord.GlobalPosition + new Vector2(_legendWord.Size.X, _legendWord.Size.Y + Ui.Px(6));
        _legendPanel.GlobalPosition = new Vector2(Mathf.Max(at.X - _legendPanel.Size.X, 0), at.Y);
        _legendPanel.Modulate = new Color(1, 1, 1, 0);
        CreateTween().TweenProperty(_legendPanel, "modulate", Colors.White, 0.15);
    }

    private async void HideLegendSoon()
    {
        await ToSignal(GetTree().CreateTimer(0.25), SceneTreeTimer.SignalName.Timeout);
        if (_legendPanel is null || !_legendPanel.Visible)
            return;
        if (_legendPanel.GetGlobalRect().HasPoint(GetGlobalMousePosition()) || _legendWord.GetGlobalRect().HasPoint(GetGlobalMousePosition()))
            return;
        _legendPanel.Visible = false;
    }

    // ============================================================================================ the log

    private void Write(string text, string? tag)
    {
        foreach (var line in text.Split('\n'))
            _logLines.Add(tag is not null ? "\u0001" + line : line);
        if (_logLines.Count > 6000)
        {
            _logLines.RemoveRange(0, _logLines.Count - 4000);
            _log.Clear();
            foreach (var line in _logLines)
                AppendLogLine(line);
            return;
        }
        var escaped = text.Replace("[", "[lb]");
        _log.AppendText(tag == "command" ? $"[color=#{Ui.LogCommand.ToHtml(false)}]{escaped}[/color]" : escaped);
    }

    private void AppendLogLine(string line)
    {
        bool command = line.StartsWith('\u0001');
        var text = (command ? line[1..] : line).Replace("[", "[lb]") + "\n";
        _log.AppendText(command ? $"[color=#{Ui.LogCommand.ToHtml(false)}]{text}[/color]" : text);
    }

    // ============================================================================================ frames and input

    private WorldEnvironment _glow = null!;
    private Circuit _circuit = null!;
    private CursorTrail _trail = null!;
    private Fx.Veil _veil = null!;
    private Fx.Sweep _sweep = null!;
    private StyleBoxFlat _logBox = null!;
    private int _dialogsOpen;
    private double _time;
    private bool _logLit;

    public override void _Process(double delta)
    {
        _time += delta;
        TickLegend();
        if (_runner is { Running: true })
        {
            _runner.Poll();
            if (_runner.Running)
            {
                _busy.Tick(_runner, delta);
                if (Ui.Effects)
                {
                    float pulse = 0.5f + 0.5f * Mathf.Sin((float)_time * 3f);
                    _logBox.BorderColor = Ui.Hdr(Ui.Neon2, 1f + 0.8f * pulse) with { A = 0.5f + 0.5f * pulse };
                    _logLit = true;
                }
            }
        }
        else if (_logLit)
        {
            _logBox.BorderColor = new Color(Ui.Neon2, 0.25f);
            _logLit = false;
        }
        if (_dragFrom is not null && !(_tree.GetLocalMousePosition().Y >= 0 && _tree.GetLocalMousePosition().Y < _tree.Size.Y))
            DragScroll(delta);
    }

    public override void _Input(InputEvent e)
    {
        if (_introPlaying && (e is InputEventKey { Pressed: true } || e is InputEventMouseButton { Pressed: true }))
        {
            EndIntro();  // any key or click skips the intro
            GetViewport().SetInputAsHandled();
            return;
        }
        if (e is InputEventMouseMotion or InputEventMouseButton { Pressed: true })
            Watch(true);
        if (e is not InputEventKey { Pressed: true, Echo: false } key)
            return;
        var focus = GetViewport().GuiGetFocusOwner();
        if (key.Keycode == Key.F5)
        {
            if (!(_runner?.Running ?? false))
                Refresh();
            GetViewport().SetInputAsHandled();
        }
        else if (key.Keycode == Key.F && key.CtrlPressed)
        {
            _filter.GrabFocus();
            _filter.SelectAll();
            GetViewport().SetInputAsHandled();
        }
        else if (key.Keycode == Key.A && key.CtrlPressed && focus is not LineEdit && focus is not TextEdit && focus is not RichTextLabel)
        {
            SelectAll();
            GetViewport().SetInputAsHandled();
        }
        else if (key.Keycode == Key.Escape && focus == _filter)
        {
            _filter.Text = "";
            Filtered();
            GetViewport().SetInputAsHandled();
        }
    }

    public override void _Notification(int what)
    {
        if (what == NotificationWMCloseRequest)
            Close();
    }

    // ============================================================================================ the tree

    private string ColumnName(int column)
    {
        foreach (var (name, index) in _col)
            if (index == column)
                return name;
        return "name";
    }

    private void ConfigureColumns(string view)
    {
        var shown = Lister.Shown[view];
        _col.Clear();
        _tree.Columns = shown.Length + 1;
        _col["name"] = 0;
        _tree.SetColumnTitle(0, T.Get("col_name"));
        _tree.SetColumnTitleAlignment(0, HorizontalAlignment.Left);
        _tree.SetColumnClipContent(0, true);
        for (int i = 0; i < shown.Length; i++)
        {
            var (key, _, right) = Columns[shown[i]];
            _col[shown[i]] = i + 1;
            _tree.SetColumnTitle(i + 1, T.Get(key));
            _tree.SetColumnTitleAlignment(i + 1, right ? HorizontalAlignment.Right : HorizontalAlignment.Left);
            _tree.SetColumnClipContent(i + 1, true);
        }
    }

    private bool ByBase => _place is { View: "pack" } && _viewMode == "base";

    /// <summary>Show the place's rows again, after the filter or the view changed.</summary>
    private void Filtered()
    {
        if (_place is null)
            return;
        Show();
        FitColumns();
        SayWhere();
        UpdateButtons();
    }

    /// <summary>List the rows (default: the place's, as listed last), sorted by the column chosen, folders first. A
    /// pack shown by base puts each model under the model most of its data is coded against. With a filter, only the
    /// rows whose names hold its text, ignoring case; by base, also the chain above each, in gray.</summary>
    private void Show(List<Row>? rows = null, string? select = null)
    {
        bool fresh = rows is not null;
        if (fresh)
        {
            _listing = rows!;
            _naturals.Clear();
        }
        rows = _listing;
        bool byBase = ByBase;
        var text = _filter.Text.Trim().ToLowerInvariant();
        _matched = rows.Where(r => r.Name.ToLowerInvariant().Contains(text)).Select(r => r.Id).ToHashSet();
        var shown = new HashSet<string>(_matched);
        if (byBase && text.Length > 0)
        {
            var parent = rows.ToDictionary(r => r.Id, r => r.Parent);
            foreach (var id in _matched)
            {
                var up = parent.GetValueOrDefault(id);
                while (up is not null && !shown.Contains(up))
                {
                    shown.Add(up);
                    up = parent.GetValueOrDefault(up);
                }
            }
        }
        rows = rows.Where(r => shown.Contains(r.Id)).ToList();
        var selected = select is not null ? new HashSet<string> { select } : fresh ? new HashSet<string>() : new HashSet<string>(_selected);

        object KeyOf(Row r) => _sortBy switch
        {
            "modified" => r.Modified?.Ticks ?? -1L,
            "packed" => r.Packed?.Ticks ?? -1L,
            "size" => r.Size ?? -1L,
            "stored" => r.Stored ?? -1L,
            "files" => (long)(r.Files ?? -1),
            "ratio" => r.Ratio.Length > 0 && double.TryParse(r.Ratio.TrimEnd('%'), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : -1.0,
            "name" => r.Name.ToLowerInvariant(),
            _ => r.Cell(_sortBy, byBase).ToLowerInvariant(),
        };
        var ordered = rows.OrderBy(r => r.Name.ToLowerInvariant(), StringComparer.Ordinal).ToList();
        var cmp = Comparer<object>.Create((a, b) => a is string sa && b is string sb ? string.CompareOrdinal(sa, sb)
            : a is long la && b is long lb ? la.CompareTo(lb) : a is double da && b is double db ? da.CompareTo(db) : 0);
        ordered = (_descending ? ordered.OrderByDescending(KeyOf, cmp) : ordered.OrderBy(KeyOf, cmp)).ToList();
        ordered = ordered.OrderBy(r => r.Kind is "folder" or "drive" or "pack" ? 0 : 1).ToList();  // folders first, as in a file manager

        _tree.Clear();
        _rows.Clear();
        _items.Clear();
        var root = _tree.CreateItem();
        var children = new Dictionary<string, List<Row>>();
        var tops = new List<Row>();
        foreach (var r in ordered)
        {
            if (byBase && r.Parent is not null && shown.Contains(r.Parent))
                children.GetOrAdd(r.Parent).Add(r);
            else
                tops.Add(r);
        }
        void Insert(Row r, TreeItem parent)
        {
            var item = _tree.CreateItem(parent);
            Fill(item, r, byBase);
            _rows[item] = r;
            _items[r.Id] = item;
            if (children.TryGetValue(r.Id, out var kids))
                foreach (var k in kids)
                    if (!_items.ContainsKey(k.Id))  // (a circle of models is shown once)
                        Insert(k, item);
        }
        foreach (var r in tops)
            Insert(r, root);
        foreach (var r in ordered)  // rows whose parents go round in a circle
            if (!_items.ContainsKey(r.Id))
                Insert(r, root);
        _selected = selected.Where(_items.ContainsKey).ToHashSet();
        if (_anchor is not null && !_items.ContainsKey(_anchor))
            _anchor = null;
        foreach (var item in _rows.Keys)
            Paint(item);
        var first = InOrder().FirstOrDefault(i => _selected.Contains(_rows[i].Id));
        if (first is not null)
            _tree.ScrollToItem(first);
        foreach (var (name, index) in _col)
        {
            var mark = name == _sortBy ? (_descending ? " ▼" : " ▲") : "";
            _tree.SetColumnTitle(index, T.Get(name == "name" ? "col_name" : Columns[name].key) + mark);
        }
        _graphBox.Visible = _place is { View: "pack" } && _viewMode == "graph";
        _tree.Visible = !_graphBox.Visible;
        if (fresh && Ui.Effects)
        {
            _sweep.Run();
            var lit = _tree.Visible ? (CanvasItem)_tree : _graphBox;
            lit.Modulate = new Color(1, 1, 1, 0.35f);
            CreateTween().TweenProperty(lit, "modulate", Colors.White, 0.3);
        }
        if (_graphBox.Visible)
        {
            _graph.SetRows(_listing, text.Length > 0 ? _matched : new HashSet<string>());
            _graph.SetSelection(_selected);
            UpdateGraphPanel();
        }
    }

    private void Fill(TreeItem item, Row r, bool byBase)
    {
        item.SetText(0, r.Name);
        item.SetIcon(0, Icons.Kind(r.Kind));
        item.SetIconMaxWidth(0, Ui.Px(18));
        item.SetTooltipText(0, r.Name);
        item.SetSelectable(0, false);
        foreach (var (name, index) in _col)
        {
            if (name == "name")
                continue;
            var text = r.Cell(name, byBase);
            item.SetText(index, text);
            item.SetSelectable(index, false);
            item.SetTextAlignment(index, Columns[name].right ? HorizontalAlignment.Right : HorizontalAlignment.Left);
            if (name is "type" or "needs" && text.Length > 0)
                item.SetTooltipText(index, text);
        }
        var color = r.Damaged ? Ui.Damaged : !_matched.Contains(r.Id) ? Ui.Context : (Color?)null;
        if (color is not null)
            for (int c = 0; c < _tree.Columns; c++)
                item.SetCustomColor(c, color.Value);
    }

    /// <summary>The row's background: the selection, else its kind's tint.</summary>
    private void Paint(TreeItem item)
    {
        var r = _rows[item];
        bool selected = _selected.Contains(r.Id);
        var tint = selected ? Ui.Selection : Ui.KindTint.TryGetValue(r.Kind, out var k) ? k : new Color(0, 0, 0, 0);
        for (int c = 0; c < _tree.Columns; c++)
            item.SetCustomBgColor(c, tint);
    }

    /// <summary>The list's items from top to bottom as shown.</summary>
    private List<TreeItem> InOrder()
    {
        var outList = new List<TreeItem>();
        var root = _tree.GetRoot();
        if (root is null)
            return outList;
        var stack = new Stack<TreeItem>();
        for (var c = root.GetChildCount() - 1; c >= 0; c--)
            stack.Push(root.GetChild(c));
        while (stack.Count > 0)
        {
            var item = stack.Pop();
            outList.Add(item);
            if (!item.Collapsed)
                for (var c = item.GetChildCount() - 1; c >= 0; c--)
                    stack.Push(item.GetChild(c));
        }
        return outList;
    }

    private void Sort(string column)
    {
        _descending = column == _sortBy && !_descending;
        _sortBy = column;
        _settings.SortBy = column;
        _settings.SortDescending = _descending;
        Show();
    }

    /// <summary>Size the columns of this view to what they hold: the numbers and dates as wide as their texts; the
    /// name, Type and Needs too, as far as the list's width allows. What it lacks is taken from each of those in
    /// proportion to what it wants beyond its least width; what is left over goes to Needs (in a folder, to the
    /// name). Columns wider than the list scroll sideways.</summary>
    private void FitColumns()
    {
        if (_place is null || _tree.Size.X <= 0)
            return;
        _fitted = _tree.Size.X;
        var shown = Lister.Shown[_place.View];
        var flexible = new List<string> { "name" };
        flexible.AddRange(shown.Where(c => Fitted.Contains(c)));
        var fixedW = shown.Where(c => !Fitted.Contains(c)).ToDictionary(c => c, Natural);
        var want = flexible.ToDictionary(c => c, Natural);
        var least = flexible.ToDictionary(c => c, c => Mathf.Min(want[c], Ui.Px(c == "name" ? 200 : Columns[c].width)));
        float room = _fitted - fixedW.Values.Sum() - Ui.Px(22);
        float spare = room - least.Values.Sum(), extra = flexible.Sum(c => want[c] - least[c]);
        Dictionary<string, float> got;
        if (spare >= extra)
        {
            got = new Dictionary<string, float>(want);
            got[want.ContainsKey("needs") ? "needs" : "name"] += spare - extra;
        }
        else
            got = flexible.ToDictionary(c => c, c => least[c] + (want[c] - least[c]) * Mathf.Max(spare, 0) / Mathf.Max(extra, 1));
        foreach (var (name, index) in _col)
        {
            float w = got.TryGetValue(name, out var g) ? g : fixedW[name];
            _tree.SetColumnCustomMinimumWidth(index, (int)w);
            _tree.SetColumnExpand(index, false);
        }
        _tree.SetColumnExpand(_col.TryGetValue("needs", out var needs) ? needs : 0, true);
        _tree.SetColumnExpandRatio(_col.TryGetValue("needs", out var n2) ? n2 : 0, 1);
    }

    /// <summary>The width the column needs for its longest text among all the rows of the place (not only those the
    /// filter shows, so typing in the filter does not move the columns), its heading included.</summary>
    private float Natural(string column)
    {
        var key = (column, ByBase);
        if (_naturals.TryGetValue(key, out var w))
            return w;
        var depth = ByBase && column == "name" ? Depths() : new Dictionary<string, int>();
        var texts = _listing.Select(r => (text: column == "name" ? r.Name : r.Cell(column, ByBase), depth: depth.GetValueOrDefault(r.Id)))
            .OrderByDescending(x => x.text.Length + 3 * x.depth).Take(50).ToList();
        var font = _tree.GetThemeFont("font");
        int size = _tree.GetThemeFontSize("font_size");
        float inner = _tree.GetThemeConstant("inner_item_margin_left") + _tree.GetThemeConstant("inner_item_margin_right") + _tree.GetThemeConstant("h_separation");
        float pad = inner + Ui.Px(14) + (column == "name" ? Ui.Px(18) + _tree.GetThemeConstant("h_separation") + Ui.Px(6) : 0);
        var titleBox = _tree.GetThemeStylebox("title_button_normal");
        float heading = Ui.Measure(T.Get(column == "name" ? "col_name" : Columns[column].key) + " ▼", _tree.GetThemeFont("title_button_font"), _tree.GetThemeFontSize("title_button_font_size"))
                        + titleBox.ContentMarginLeft + titleBox.ContentMarginRight + Ui.Px(8);
        float indent = _tree.GetThemeConstant("item_margin");
        float longest = texts.Count > 0 ? texts.Max(x => font.GetStringSize(x.text, HorizontalAlignment.Left, -1, size).X + indent * x.depth) : 0;
        _naturals[key] = Mathf.Ceil(Mathf.Max(heading, longest + pad));
        return _naturals[key];
    }

    private Dictionary<string, int> Depths()
    {
        var parent = _listing.ToDictionary(r => r.Id, r => r.Parent);
        var depth = new Dictionary<string, int>();
        foreach (var id in parent.Keys)
        {
            int d = 0;
            var up = parent[id];
            var seen = new HashSet<string> { id };
            while (up is not null && parent.ContainsKey(up) && !seen.Contains(up))
            {
                seen.Add(up);
                d++;
                up = parent[up];
            }
            depth[id] = d;
        }
        return depth;
    }

    // ------------------------------------------------------------------------------------------ selection

    private void Select(HashSet<string> ids)
    {
        _selected = ids.Where(_items.ContainsKey).ToHashSet();
        foreach (var item in _rows.Keys)
            Paint(item);
        _graph.SetSelection(_selected);
        if (_graphBox.Visible)
            UpdateGraphPanel();
        UpdateButtons();
        SayWhere();
    }

    /// <summary>The panel beside the graph: the selected model, the selection summed up, or the pack.</summary>
    private void UpdateGraphPanel()
    {
        var rows = Chosen();
        _graphPanel.Show(rows, rows.Count > 1 ? SumUp(rows) : null, _where);
    }

    private void ClickSelect(string id, bool ctrl, bool shift)
    {
        if (shift && _anchor is not null && _items.ContainsKey(_anchor))
        {
            var order = InOrder().Select(i => _rows[i].Id).ToList();
            int a = order.IndexOf(_anchor), b = order.IndexOf(id);
            if (a >= 0 && b >= 0)
            {
                var range = order.Skip(Math.Min(a, b)).Take(Math.Abs(a - b) + 1);
                var set = ctrl ? new HashSet<string>(_selected) : new HashSet<string>();
                set.UnionWith(range);
                Select(set);
                return;
            }
        }
        _anchor = id;
        if (ctrl)
        {
            var set = new HashSet<string>(_selected);
            if (!set.Remove(id))
                set.Add(id);
            Select(set);
        }
        else
            Select(new HashSet<string> { id });
    }

    /// <summary>Ctrl+A: every row the filter lets through (not the gray rows shown only to hold a chain together).</summary>
    private void SelectAll()
    {
        if (_runner?.Running ?? false)
            return;
        Select(_rows.Values.Where(r => _matched.Contains(r.Id)).Select(r => r.Id).ToHashSet());
    }

    private List<Row> Chosen()
    {
        return InOrder().Select(i => _rows[i]).Where(r => _selected.Contains(r.Id)).ToList();
    }

    private void TreeInput(InputEvent e)
    {
        if (_runner?.Running ?? false)
            return;
        if (e is InputEventMouseButton mb)
        {
            if (mb.ButtonIndex is MouseButton.WheelUp or MouseButton.WheelDown or MouseButton.WheelLeft or MouseButton.WheelRight)
                return;
            float titles = TitleHeight();
            if (mb.Position.Y < titles)
                return;  // the headings: the tree sorts (column_title_clicked)
            var item = _tree.GetItemAtPosition(mb.Position);
            if (mb.ButtonIndex == MouseButton.Left)
            {
                if (mb.Pressed)
                {
                    _tree.GrabFocus();
                    if (mb.DoubleClick)
                    {
                        if (item is not null)
                            OpenRow(item);
                        _tree.AcceptEvent();
                        return;
                    }
                    Press(item, mb.CtrlPressed, mb.ShiftPressed);
                }
                else
                    Release();
                _tree.AcceptEvent();
            }
            else if (mb.ButtonIndex == MouseButton.Right && mb.Pressed)
            {
                _tree.GrabFocus();
                Popup(item, mb.GlobalPosition);
                _tree.AcceptEvent();
            }
        }
        else if (e is InputEventMouseMotion mm)
        {
            if (_dragFrom is not null && (mm.ButtonMask & MouseButtonMask.Left) != 0)
            {
                _dragY = mm.Position.Y;
                DragTo();
                _tree.AcceptEvent();
            }
        }
        else if (e is InputEventKey { Pressed: true } key)
        {
            if (HandleKey(key))
                _tree.AcceptEvent();
        }
    }

    private float TitleHeight()
    {
        var sb = _tree.GetThemeStylebox("title_button_normal");
        return Ui.Bold.GetHeight(Ui.SmallFontSize) + sb.ContentMarginTop + sb.ContentMarginBottom + _tree.GetThemeStylebox("panel").ContentMarginTop;
    }

    /// <summary>The left button down in the list: on a row, a drag may start there; on empty space, nothing stays
    /// selected (with Ctrl, what is), and a drag may start there too: from below the last row.</summary>
    private void Press(TreeItem? item, bool ctrl, bool shift)
    {
        _dragKeep = ctrl ? new HashSet<string>(_selected) : new HashSet<string>();
        if (item is not null)
        {
            var id = _rows[item].Id;
            _dragFrom = id;
            ClickSelect(id, ctrl, shift);
        }
        else
        {
            _dragFrom = Below;
            if (!ctrl)
                Select(new HashSet<string>());
        }
    }

    private void Release()
    {
        _dragFrom = null;
    }

    private void DragScroll(double delta)
    {
        _dragAccum += delta;
        if (_dragAccum < 0.06)
            return;
        _dragAccum = 0;
        var y = _tree.GetLocalMousePosition().Y;
        var bar = VScroll();
        if (bar is null)
            return;
        bar.Value += (y < 0 ? -1 : 1) * bar.Page / 6;
        _dragY = Mathf.Clamp(y, TitleHeight(), _tree.Size.Y - 1);
        DragTo();
    }

    private double _dragAccum;

    private VScrollBar? VScroll()
    {
        foreach (var child in _tree.GetChildren(true))
            if (child is VScrollBar v)
                return v;
        return null;
    }

    /// <summary>Dragging from a row selects every row from it to the one under the mouse; from under the last row,
    /// every row from the mouse down.</summary>
    private void DragTo()
    {
        var order = InOrder();
        if (order.Count == 0)
            return;
        var ids = order.Select(i => _rows[i].Id).ToList();
        if (_dragFrom != Below && !ids.Contains(_dragFrom!))
            return;
        float y = Mathf.Clamp(_dragY, TitleHeight(), _tree.Size.Y - 1);
        var item = _tree.GetItemAtPosition(new Vector2(Ui.Px(30), y));
        if (item is null && y < _tree.Size.Y / 2)
            item = order[0];
        HashSet<string> chosen;
        if (_dragFrom == Below)
            chosen = item is not null ? ids.Skip(ids.IndexOf(_rows[item].Id)).ToHashSet() : new HashSet<string>();
        else
        {
            int a = ids.IndexOf(_dragFrom!), b = item is not null ? ids.IndexOf(_rows[item].Id) : ids.Count - 1;
            chosen = ids.Skip(Math.Min(a, b)).Take(Math.Abs(a - b) + 1).ToHashSet();
        }
        chosen.UnionWith(_dragKeep);
        if (!chosen.SetEquals(_selected))
            Select(chosen);
    }

    private bool HandleKey(InputEventKey key)
    {
        var order = InOrder();
        if (key.Keycode == Key.Escape)
        {
            Select(new HashSet<string>());
            return true;
        }
        if (key.Keycode is Key.Enter or Key.KpEnter)
        {
            if (_anchor is not null && _items.TryGetValue(_anchor, out var it))
                OpenRow(it);
            return true;
        }
        if (key.Keycode == Key.Backspace || (key.Keycode == Key.Up && key.AltPressed))
        {
            GoUp();
            return true;
        }
        if (key.Keycode is Key.Up or Key.Down or Key.Home or Key.End or Key.Pageup or Key.Pagedown)
        {
            if (order.Count == 0)
                return true;
            var ids = order.Select(i => _rows[i].Id).ToList();
            int at = _anchor is not null ? ids.IndexOf(_anchor) : -1;
            int page = Math.Max(1, (int)((_tree.Size.Y - TitleHeight()) / RowHeight()) - 1);
            int next = key.Keycode switch
            {
                Key.Up => Math.Max(at - 1, 0), Key.Down => Math.Min(at + 1, ids.Count - 1), Key.Home => 0, Key.End => ids.Count - 1,
                Key.Pageup => Math.Max(at - page, 0), _ => Math.Min(at + page, ids.Count - 1),
            };
            if (at < 0)
                next = key.Keycode is Key.Up or Key.End or Key.Pageup ? ids.Count - 1 : 0;
            var id = ids[next];
            if (key.ShiftPressed && _anchor is not null && at >= 0)
            {
                var set = ids.Skip(Math.Min(at, next)).Take(Math.Abs(at - next) + 1).ToHashSet();
                Select(set);  // the anchor stays where the shift range began
            }
            else
            {
                _anchor = id;
                Select(new HashSet<string> { id });
            }
            _tree.ScrollToItem(order[next]);
            return true;
        }
        return false;
    }

    private float RowHeight() => Ui.Font.GetHeight(Ui.FontSize) + _tree.GetThemeConstant("v_separation") + Ui.Px(4);

    /// <summary>The right-click menu: for the row under the mouse (or what is selected, if it is), or on empty space
    /// for nothing selected.</summary>
    private void Popup(TreeItem? item, Vector2 at)
    {
        if (item is null)
            Select(new HashSet<string>());
        else if (!_selected.Contains(_rows[item].Id))
        {
            _anchor = _rows[item].Id;
            Select(new HashSet<string> { _rows[item].Id });
        }
        UpdateButtons();
        _menu.Position = (Vector2I)at;  // embedded popups are placed in canvas units, as the mouse position is
        _menu.Popup();
    }

    // ============================================================================================ browsing

    /// <summary>Show a place (and select the row named `select`); if it cannot be listed, stay where the list was.</summary>
    private async void Go(Place place, string? select = null, bool keepScroll = false)
    {
        if (_listingNow)
            return;
        _listingNow = true;
        Listing listing;
        try
        {
            listing = await Lister.ListAsync(place, _reader);
        }
        catch (Exception e)
        {
            _listingNow = false;
            var problem = e is PackReader.ToolFailed ? e.Message : e.Message;
            if (_place is null && place.Parent() is { } up)  // at start: show what can be shown
            {
                Go(up);
                return;
            }
            await OcDialog.Error(this, "OverCompressing", T.Get("cannot_list", ("problem", problem)));
            if (_place is not null)
                _path.Text = _place.Text();
            return;
        }
        _listingNow = false;
        Mark(listing.Rows, place);
        if (_place is null || !place.SamePlaceAs(_place))
        {
            _quiet = true;
            _filter.Text = "";
            _quiet = false;
            _anchor = null;
            if (Ui.Effects && _place is not null)
                _circuit.Shift();  // another place, another board
        }
        float scroll = keepScroll && VScroll() is { } bar ? (float)bar.Value : -1;
        _place = place;
        _where = listing.Where;
        _settings.LastPlace = place.Path is null ? null : place.Text();
        _insidePack = place.View == "folder" && place.Path is not null && Place.PackFolderOf(place.Path) is not null;
        LayOut(place.View);
        var kind = place.View == "model" ? "model" : place.View == "pack" ? (place.ReadOnly ? "oczip" : "pack") : "folder";
        foreach (var c in _modeSlot.GetChildren())
            c.QueueFree();
        var chip = Ui.Chip(Ui.Legend.First(l => l.kind == kind).label, Ui.KindColor[kind], bold: true);
        _modeSlot.AddChild(chip);
        if (Ui.Effects)
        {
            chip.Modulate = new Color(2.5f, 2.5f, 2.5f, 1f);  // a flash that settles
            CreateTween().TweenProperty(chip, "modulate", Colors.White, 0.5).SetTrans(Tween.TransitionType.Quad).SetEase(Tween.EaseType.Out);
        }
        _modeNote.Text = place.View == "model" && place.ReadOnly ? T.Get("mode_in_file") : "";
        _seen = Stamp();
        _looked = Time.GetTicksMsec() / 1000.0;
        _path.Text = place.Text();
        GetWindow().Title = $"OverCompressing {_versions} — {place.Text()}";
        _views.Visible = place.View == "pack";
        if (place.View != "pack" && _viewMode == "graph")
        {
            _graphBox.Visible = false;
            _tree.Visible = true;
        }
        ConfigureColumns(place.View);
        Show(listing.Rows, select);
        FitColumns();
        SayWhere();
        UpdateButtons();
        if (scroll >= 0 && VScroll() is { } bar2)
            bar2.Value = scroll;
    }

    /// <summary>List the place again, keeping what is selected and where the list is scrolled.</summary>
    private void Refresh()
    {
        if (_place is null)
            return;
        var chosen = new HashSet<string>(_selected);
        var anchor = _anchor;
        GoKeeping(_place, chosen, anchor);
    }

    private async void GoKeeping(Place place, HashSet<string> chosen, string? anchor)
    {
        await Task.Yield();
        var bar = VScroll();
        float scroll = bar is not null ? (float)bar.Value : -1;
        Go(place, null, keepScroll: true);
        // Go is async: wait for it to finish, then put the selection back
        while (_listingNow)
            await ToSignal(GetTree(), SceneTree.SignalName.ProcessFrame);
        _anchor = anchor;
        Select(chosen);
        if (scroll >= 0 && VScroll() is { } b)
            b.Value = scroll;
    }

    /// <summary>What changes when the place changes: the time stamp (and size) of a pack folder's ocpack.json, of a
    /// pack file, or of a folder itself; null for the drives.</summary>
    private object? Stamp()
    {
        var place = _place;
        if (place?.Path is null)
            return null;
        if (place.Pack)
            return Place.PackStamp(place.Path) is { } s ? s : "gone";
        try
        {
            var info = new DirectoryInfo(place.Path);
            if (!info.Exists)
                return "gone";
            return (info.LastWriteTimeUtc.Ticks, 0L);
        }
        catch (Exception) { return "gone"; }
    }

    /// <summary>Look at the place's stamp, at most every WatchSeconds: on a timer, and at once when the mouse moves
    /// or clicks in the window after that long; list the place again only if the stamp changed. Nothing is looked
    /// at while the window is minimized, a dialog is open or something runs.</summary>
    private void Watch(bool byHand)
    {
        double now = Time.GetTicksMsec() / 1000.0;
        if (now - _looked < WatchSeconds || (_runner?.Running ?? true) || _place is null || _listingNow)
            return;
        if (DisplayServer.WindowGetMode() == DisplayServer.WindowMode.Minimized || GetChildren().OfType<Window>().Any(w => w.Visible))
            return;
        _looked = now;
        var stamp = Stamp();
        if (Equals(stamp, _seen))
            return;
        if (stamp is "gone")
        {
            var up = _place.Parent();
            while (up?.Path is not null && !Directory.Exists(up.Path) && !File.Exists(up.Path))
                up = up.Parent();
            _seen = stamp;
            if (up is not null)
            {
                Write(T.Get("watch_gone") + "\n", null);
                Go(up);
            }
            return;
        }
        Refresh();
    }

    private void GoUp()
    {
        if (_runner?.Running ?? false)
            return;
        var up = _place?.Parent();
        if (up is null || _place is null)
            return;
        if (_place.Model is not null)
            Go(up, _place.Model);
        else
            Go(up, Path.GetFileName(_place.Path!.TrimEnd('\\', '/')) is { Length: > 0 } name ? name : _place.Path);
    }

    private async void GoToText()
    {
        var place = Place.FromText(_path.Text);
        if (place is not null)
            Go(place);
        else
        {
            await OcDialog.Error(this, "OverCompressing", T.Get("no_place", ("text", _path.Text)));
            _path.Text = _place?.Text() ?? "";
        }
    }

    /// <summary>⚠ in Needs (and the row in red) for what the last verify that failed found lost, while the pack is as
    /// it was then: only the window remembers it (verify writes nothing into a pack).</summary>
    private void Mark(List<Row> rows, Place place)
    {
        var m = _marks;
        if (m is null || !place.Pack || !Place.SamePath(place.Path!, m.Pack))
            return;
        if (!Equals(Place.PackStamp(m.Pack), m.Stamp))
        {
            _marks = null;
            return;
        }
        foreach (var r in rows)
        {
            string note;
            if (place.View == "pack" && m.Models.TryGetValue(r.Id, out var lost))
                note = T.Get("mark_model", ("files", T.Count(lost, "file")));
            else if (place.View == "model" && m.Lost.TryGetValue((place.Model!, r.Id), out var own))
                note = T.Get(own ? "mark_file" : "mark_file_base");
            else
                continue;
            r.Needs = note + (r.Needs.Length > 0 ? "  " + r.Needs : "");
            r.NeedsByBase = note + (r.NeedsByBase.Length > 0 ? "  " + r.NeedsByBase : "");
            r.Damaged = true;
        }
    }

    /// <summary>Go into a folder, a drive, a pack or a model.</summary>
    private void OpenRow(TreeItem item)
    {
        if (!_rows.TryGetValue(item, out var row) || _place is null)
            return;
        if (row.Kind is "folder" or "drive" or "pack" or "oczip" or "model")
            Go(_place.Child(row));
    }

    /// <summary>Refresh, Pack in and Add file can always be used (where the place can be changed); the others act on
    /// what is selected (Rename on one row), so they are off while nothing is.</summary>
    private void UpdateButtons()
    {
        var place = _place;
        int count = _selected.Count;
        bool writable = place is not null && place.Pack && !place.ReadOnly;
        bool onDisk = place is { View: "folder", Path: not null } && !_insidePack;
        bool running = _runner?.Running ?? false;
        var usable = new Dictionary<string, bool>
        {
            ["refresh"] = true, ["pack_in"] = writable, ["add_file"] = writable, ["pack_to"] = onDisk && count > 0,
            ["remove"] = (onDisk || writable) && count > 0, ["save"] = count > 0, ["extract"] = count > 0,
            ["verify"] = count > 0, ["rename"] = writable && count == 1, ["rebase"] = writable && count > 0,
        };
        foreach (var (key, b) in _buttons)
            b.Disabled = !usable[key] || running;
        for (int i = 0; i < _menu.ItemCount; i++)
        {
            int id = _menu.GetItemId(i);
            if (!_menu.IsItemSeparator(i) && id >= 0 && id < _menuKeys.Count)
                _menu.SetItemDisabled(i, !usable[_menuKeys[id]] || running);
        }
        _up.Disabled = running || _place?.Parent() is null;
    }

    /// <summary>The line under the list: what is selected, summed up, then the place.</summary>
    private void SayWhere()
    {
        if (_place is null || (_runner?.Running ?? false) || _introPlaying)  // (the intro types the version there first)
            return;
        var text = _where ?? T.Count(_listing.Count, "item");
        if (_filter.Text.Trim().Length > 0)
            text = T.Get("filtered", ("shown", _matched.Count), ("total", _listing.Count)) + " · " + text;
        if (_place.ReadOnly)
            text += $" · {T.Get("read_only")}";
        var rows = Chosen();
        if (rows.Count > 0)
            text = SumUp(rows) + "   │   " + text;
        if (_status.Text != text && Ui.Effects)
        {
            _status.Modulate = new Color(1.8f, 1.8f, 1.8f, 0.6f);
            CreateTween().TweenProperty(_status, "modulate", Colors.White, 0.35);
        }
        _status.Text = text;
        _status.TooltipText = text;
    }

    /// <summary>What the selected rows take: on disk; in a pack, also what they are stored in, each blob counted once.</summary>
    private string SumUp(List<Row> rows)
    {
        long size = rows.Where(r => r.Size is not null).Sum(r => r.Size!.Value);
        if (_place!.View == "folder")
        {
            bool files = rows.Any(r => r.Size is not null);
            return files ? T.Get("selected_size", ("count", rows.Count), ("size", T.SizeText(size))) : T.Get("selected_count", ("count", rows.Count));
        }
        var blobs = new Dictionary<string, long>();
        foreach (var r in rows)
        {
            if (r.Summary is not null)
            {
                if (r.Blobs.Count > 0)
                    foreach (var (sha, s) in r.Blobs)
                        blobs[sha] = s;
                else if (r.Stored is not null)
                    blobs["\0" + r.Id] = r.Stored.Value;
            }
            else if (r.File is not null && r.Stored is not null)
                blobs[r.File.Sha256] = r.Stored.Value;
        }
        long stored = blobs.Values.Sum();
        return T.Get("selected", ("count", rows.Count), ("size", T.SizeText(size)), ("stored", T.SizeText(stored)), ("ratio", T.Percent(stored, size)));
    }

    private Dictionary<(string pack, string model), HashSet<string>?> Targets()
    {
        var place = _place!;
        var rows = Chosen();
        var plan = new Dictionary<(string, string), HashSet<string>?>();
        if (place.View == "model" && rows.Count > 0)
        {
            var paths = rows.Select(r => r.Id).ToHashSet();
            plan[(place.Path!, place.Model!)] = paths.SetEquals(_listing.Select(r => r.Id)) ? null : paths;
        }
        else if (place.View == "pack")
            foreach (var r in rows)
                plan[(place.Path!, r.Id)] = null;
        return plan;
    }

    // ============================================================================================ actions

    private async Task Extract()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var plan = Targets();
        if (plan.Count == 0)
        {
            await OcDialog.Info(this, T.Get("extract"), T.Get("nothing_to_extract"));
            return;
        }
        var answer = await AskExtract(plan);
        if (answer is null)
            return;
        var (dest, layout) = answer.Value;
        var targets = plan.Keys.ToDictionary(k => k, k => Place.ExtractTarget(dest, k.model, layout));
        var problems = targets.Values.Select(Place.PlaceProblem).Where(p => p is not null).Distinct().ToList();
        if (problems.Count > 0)
        {
            await OcDialog.Error(this, T.Get("extract"), string.Join("\n", problems.Select(p => T.Sentence(p))));
            return;
        }
        var jobs = new List<Job>();
        foreach (var ((pack, name), paths) in plan.OrderBy(kv => kv.Key.model, StringComparer.Ordinal))
        {
            var argv = new List<string> { "restore", pack, name, targets[(pack, name)] };
            argv.AddRange((paths ?? new HashSet<string>()).OrderBy(p => p, StringComparer.Ordinal).Select(p => "--file=" + p));
            if (layout == "files")
                argv.Add("--flat");
            jobs.Add(new Job(argv, T.Get("extracting", ("name", name))) { Retry = OfferOverwrite });
        }
        Start(jobs, async () =>
        {
            if (await OcDialog.Ask(this, T.Get("extract"), T.Get("done_extract_q", ("dest", T.Short(dest)))))
                OS.ShellOpen(dest);
        });
    }

    /// <summary>The arguments to run a failed restore again with --overwrite, if that is why it failed and the user
    /// agrees. The runner asks synchronously; the question is asked on the next frame, and the job re-queued then.</summary>
    private List<string>? OfferOverwrite(Job job, ToolEvent? error)
    {
        if (error?.Str("code") != "exists" || job.Argv.Contains("--overwrite"))  // the code, never the text
            return null;
        _pendingOverwrite = job;
        return null;
    }

    private Job? _pendingOverwrite;

    private async Task AskOverwriteAndRetry(Job job)
    {
        if (await OcDialog.Ask(this, T.Get("extract"), T.Get("overwrite_q", ("dest", T.Short(job.Argv[3])))))
        {
            var again = new Job(job.Argv.Append("--overwrite"), job.Title) { Retry = job.Retry };
            Start(new[] { again }, _done ?? (() => { }));
        }
    }

    private async Task<(string dest, string layout)?> AskExtract(Dictionary<(string pack, string model), HashSet<string>?> plan)
    {
        var names = plan.Keys.Select(k => k.model).Distinct().OrderBy(n => n, StringComparer.Ordinal).ToList();
        var lasts = names.Select(n => n.Split('/').Last()).ToList();
        var clash = lasts.FirstOrDefault(n => lasts.Count(x => x == n) > 1);
        var usable = new Dictionary<string, bool> { ["full"] = true, ["last"] = clash is null, ["files"] = plan.Count == 1 };
        var d = new OcDialog(T.Get("extract_title"));
        var what = plan.OrderBy(kv => kv.Key.model, StringComparer.Ordinal)
            .Select(kv => kv.Value is null ? T.Get("whole_model", ("name", kv.Key.model)) : T.Get("some_files", ("name", kv.Key.model), ("files", T.Count(kv.Value.Count, "file")))).ToList();
        if (what.Count == 1)
            d.Body.AddChild(Ui.Heading(T.Get("extract_one", ("what", what[0]))));
        else
        {
            d.Body.AddChild(Ui.Heading(T.Get("extract_some", ("count", what.Count))));
            d.Body.AddChild(Ui.Label(T.Lines(what), Ui.Text));
        }
        d.Body.AddChild(Ui.Gap(6));
        d.Body.AddChild(Ui.Label(T.Get("dest_prompt"), Ui.Text));
        var row = Ui.HBox(6);
        d.Body.AddChild(row);
        var dest = Ui.Edit(_settings.ExtractDest);
        row.AddChild(dest);
        row.AddChild(Ui.Button(T.Get("browse"), async () =>
        {
            var chosen = await Pick.Folder(d, T.Get("pick_dest"), dest.Text);
            if (chosen is not null)
                dest.Text = chosen;
        }));
        d.Body.AddChild(Ui.Gap(6));
        d.Body.AddChild(Ui.Label(T.Get("layout"), Ui.Text));
        var group = new ButtonGroup();
        var start = new[] { _settings.ExtractLayout, "last", "full" }.First(k => usable[k]);
        var named = (names.FirstOrDefault(n => n.Contains('/')) ?? names[0]).Split('/');
        var radios = new Dictionary<string, CheckBox>();
        foreach (var (key, shown) in new[]
                 {
                     ("full", Path.Combine(new[] { "…" }.Concat(named).Append("…").ToArray())),
                     ("last", Path.Combine("…", named.Last(), "…")), ("files", Path.Combine("…", "…")),
                 })
        {
            var r = Ui.Radio($"{T.Get("path_" + key)}     {shown}", group, key == start);
            r.Disabled = !usable[key];
            radios[key] = r;
            d.Body.AddChild(r);
        }
        var notes = new List<string>();
        if (!usable["last"])
            notes.Add(T.Get("last_off", ("name", clash)));
        if (!usable["files"])
            notes.Add(T.Get("files_off"));
        if (notes.Count > 0)
            d.Body.AddChild(Ui.Note(string.Join("  ", notes)));
        (string, string)? result = null;
        d.Accepting = () =>
        {
            if (dest.Text.Trim().Length == 0)
            {
                d.ShowError(T.Get("need_dest"));
                return false;
            }
            var layout = radios.First(kv => kv.Value.ButtonPressed).Key;
            result = (Place.NormPath(dest.Text), layout);
            return true;
        };
        d.FirstFocus = dest;
        dest.CaretColumn = dest.Text.Length;
        if (!await d.ShowAsync(this) || result is null)
            return null;
        (_settings.ExtractDest, _settings.ExtractLayout) = result.Value;
        return result;
    }

    private async Task Verify()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var plan = Targets();
        if (plan.Count == 0)
        {
            await OcDialog.Info(this, T.Get("verify"), T.Get("nothing_to_verify"));
            return;
        }
        var byPack = new Dictionary<string, Dictionary<string, HashSet<string>?>>();
        foreach (var ((pack, name), paths) in plan)
            byPack.GetOrAdd(pack)[name] = paths;
        var jobs = new List<Job>();
        foreach (var (pack, chosen) in byPack.OrderBy(kv => kv.Key, StringComparer.Ordinal))
        {
            var (name, paths) = (chosen.First().Key, chosen.First().Value);
            List<string> argv;
            string what;
            List<string> models;
            if (paths is not null)
            {
                argv = new List<string> { "verify", pack, name };
                argv.AddRange(paths.OrderBy(p => p, StringComparer.Ordinal).Select(p => "--file=" + p));
                what = $"{name}: {T.Count(paths.Count, "file")}";
                models = new List<string>();
            }
            else
            {
                bool whole = chosen.Keys.ToHashSet().SetEquals(PackReader.PackModels(pack));
                what = whole ? T.Get("everything", ("pack", Path.GetFileName(pack.TrimEnd('\\', '/')))) : string.Join(", ", chosen.Keys.OrderBy(n => n, StringComparer.Ordinal));
                models = whole ? new List<string>() : chosen.Keys.OrderBy(n => n, StringComparer.Ordinal).ToList();
                argv = new List<string> { "verify", pack };
                argv.AddRange(models);
            }
            jobs.Add(new Job(argv, T.Get("verifying", ("what", what))) { Models = models, ModelsAt = 2 });
        }
        Start(jobs, (Action)(() => { _ = OcDialog.Info(this, T.Get("verify"), T.Get("done_verify")); }));
    }

    private async Task Pack()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var place = _place;
        string source, name;
        List<string> files;
        string? here;
        if (place.View == "pack" && !place.ReadOnly)  // into this pack folder: from a folder to pick
        {
            var picked = await Pick.Folder(this, T.Get("pick_source"), _settings.PackTarget);
            if (picked is null)
                return;
            source = Place.NormPath(picked);
            files = new List<string>();
            name = Path.GetFileName(source.TrimEnd('\\', '/'));
            here = place.Path;
        }
        else if (place is { View: "folder", Path: not null })
        {
            var rows = Chosen();
            var folders = rows.Where(r => r.Kind == "folder").ToList();
            if (folders.Count == 1 && rows.Count == 1)
            {
                source = Path.Combine(place.Path, folders[0].Name);
                files = new List<string>();
                name = folders[0].Name;
            }
            else if (rows.Count > 0 && rows.All(r => r.Kind is not ("folder" or "drive" or "pack")))
            {
                source = place.Path;
                files = rows.Select(r => r.Name).OrderBy(n => n, StringComparer.Ordinal).ToList();
                name = files.Count == 1 ? Path.GetFileNameWithoutExtension(files[0]) : Path.GetFileName(place.Path.TrimEnd('\\', '/'));
            }
            else
            {
                await OcDialog.Info(this, T.Get("name_title"), T.Get("pack_what"));
                return;
            }
            here = null;
        }
        else
        {
            await OcDialog.Info(this, T.Get("name_title"), T.Get("pack_here"));
            return;
        }
        var (count, size) = await Task.Run(() => Lister.SourceFiles(source, files));
        var answer = await AskPack(name, T.Get("name_what", ("files", T.Count(count, "file")), ("size", T.SizeText(size))), source, files, here);
        if (answer is null)
            return;
        var (model, mode, where, replace) = answer.Value;
        if (here is null)
            _settings.PackMode = mode;
        if (mode == "folder")
            _settings.PackTarget = where;
        var argv = new List<string> { "pack", source, where, "--name=" + model };
        argv.AddRange(files.Select(f => "--file=" + f));
        if (replace)
            argv.Add("--replace");
        Start(new[] { new Job(argv, T.Get("packing", ("name", model))) }, async () =>
        {
            if (mode == "file" && place.View == "folder" && Place.SamePath(place.Path!, Path.GetDirectoryName(where) ?? ""))
                Go(place, Path.GetFileName(where));
            else if (mode == "folder" && place.View == "pack")
                Go(place, model);
            var hint = _runner.Events.FirstOrDefault(e => e.Event == "hint");
            await OcDialog.Info(this, T.Get("name_title"), T.Get("done_pack", ("name", model)) +
                (hint is not null ? T.Get("hint_rebase", ("models", T.NamesText(hint.Strings("models")))) : ""));
        });
    }

    /// <summary>(model name, "folder" or "file", the pack folder or new pack file, replace) the user enters, checked,
    /// or null. here: the pack folder the list shows; the choice is then this pack folder (the default) or a new
    /// pack file, a pack of its own.</summary>
    private async Task<(string name, string mode, string where, bool replace)?> AskPack(string defaultName, string what, string source, List<string> files, string? here)
    {
        var d = new OcDialog(T.Get("name_title"));
        d.Body.AddChild(Ui.Heading(what));
        d.Body.AddChild(OcDialog.PathLine(source));
        d.Body.AddChild(Ui.Gap(6));
        d.Body.AddChild(Ui.Label(T.Get("name_prompt"), Ui.Text));
        var name = Ui.Edit(defaultName);
        d.Body.AddChild(name);
        d.Body.AddChild(Ui.Gap(6));
        d.Body.AddChild(Ui.Label(T.Get("into"), Ui.Text));
        var mode = here is not null ? "folder" : _settings.PackMode;
        var newFile = Path.Combine(files.Count > 0 ? source : Path.GetDirectoryName(source) ?? source, defaultName.Split('/').Last() + Place.Oczip);
        var paths = new Dictionary<string, string> { ["folder"] = here ?? _settings.PackTarget, ["file"] = newFile };
        var shownMode = mode;
        var group = new ButtonGroup();
        var radios = new Dictionary<string, CheckBox>();
        var target = Ui.Edit(paths[mode]);
        Button browse = null!;
        void Lock()
        {
            bool fixedTarget = here is not null && Current() == "folder";
            target.Editable = !fixedTarget;
            browse.Disabled = fixedTarget;
        }
        string Current() => radios.First(kv => kv.Value.ButtonPressed).Key;
        void Switch()
        {
            paths[shownMode] = target.Text;
            shownMode = Current();
            target.Text = paths[shownMode];
            Lock();
        }
        foreach (var key in new[] { "folder", "file" })
        {
            var choice = here is null ? "into_" + key : key == "folder" ? "into_here" : "into_file_here";
            var r = Ui.Radio(T.Get(choice), group, key == mode);
            r.Pressed += Switch;
            radios[key] = r;
            d.Body.AddChild(r);
            var note = Ui.Note(T.Get(choice + "_note"));
            var indent = Ui.HBox(0);
            indent.AddChild(new Control { CustomMinimumSize = new Vector2(Ui.Px(26), 0) });
            indent.AddChild(note);
            d.Body.AddChild(indent);
        }
        var row = Ui.HBox(6);
        d.Body.AddChild(row);
        row.AddChild(target);
        browse = Ui.Button(T.Get("browse"), async () =>
        {
            var where = target.Text.Trim();
            string? chosen;
            if (Current() == "folder")
                chosen = await Pick.Folder(d, T.Get("pick_archive"), where);
            else
                chosen = await Pick.SaveOczip(d, T.Get("pick_file"), Path.GetDirectoryName(where), Path.GetFileName(where));
            if (chosen is not null)
                target.Text = chosen;
        });
        row.AddChild(browse);
        Lock();
        d.Body.AddChild(Ui.Gap(4));
        d.Body.AddChild(Ui.Note(T.Get("name_hint") + (here is not null ? "\n" + T.Get("file_hint_here") : "")));
        (string, string, string, bool)? result = null;
        d.AcceptingAsync = async () =>
        {
            var model = name.Text.Trim();
            var where = target.Text.Trim();
            var m = Current();
            var problem = Rules.NameProblem(model) ?? (where.Length == 0 ? T.Get("need_target") : null);
            if (problem is null && m == "file")
            {
                where = Place.NormPath(where);
                problem = !Place.IsOczip(where) ? T.Get("export_name")
                    : File.Exists(where) || Directory.Exists(where) ? T.Get("file_exists")
                    : Place.Holds(source, where) && files.Count == 0 ? T.Get("file_in_source")
                    : Place.PlaceProblem(Path.GetDirectoryName(where) ?? where);
            }
            else if (problem is null)
            {
                where = Place.NormPath(where);
                problem = Place.IsOczip(where) ? T.Get("target_oczip")
                    : Place.Holds(source, where) && files.Count == 0 ? $"{source} holds {where}" : Place.PlaceProblem(source);
            }
            if (problem is not null)
            {
                d.ShowError(problem);
                return false;
            }
            bool held = false;
            if (m == "folder" && File.Exists(Path.Combine(where, Place.Marker)))
            {
                var manifests = await Task.Run(() => { try { return PackReader.ReadManifests(where); } catch (Exception) { return new Dictionary<string, PackReader.Manifest>(); } });
                if (manifests.TryGetValue(model, out var manifest))
                {
                    held = true;
                    if (!await OcDialog.Ask(d, T.Get("name_title"), T.Get("replace_q", ("name", model), ("files", T.Count(manifest.Files.Count, "file")))))
                        return false;
                }
            }
            result = (model, m, where, m == "folder" && held);
            return true;
        };
        d.FirstFocus = name;
        name.SelectAll();
        if (!await d.ShowAsync(this))
            return null;
        return result;
    }

    /// <summary>Add files from disk to the model shown (those whose names the model has already replace them, after
    /// asking): pack --add, each file under its own name at the top of the model.</summary>
    private async Task AddFile()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var place = _place;
        if (place.View != "model" || place.ReadOnly)
        {
            await OcDialog.Info(this, T.Get("add_title"), T.Get("read_only_action"));
            return;
        }
        var chosen = await Pick.Files(this, T.Get("add_pick", ("model", place.Model)));
        var paths = chosen.Select(Place.NormPath).ToList();
        if (paths.Count == 0)
            return;
        var folder = Path.GetDirectoryName(paths[0]) ?? "";
        var names = paths.Select(p => Path.GetFileName(p)).ToList();
        var have = _listing.Select(r => r.Id).ToHashSet();
        var clash = names.Where(have.Contains).ToList();
        if (clash.Count > 0 && !await OcDialog.Ask(this, T.Get("add_title"), T.Get("add_q", ("model", place.Model), ("files", T.Count(clash.Count, "file")), ("names", T.Lines(clash)))))
            return;
        var argv = new List<string> { "pack", folder, place.Path!, "--name=" + place.Model, "--add" };
        argv.AddRange(names.Select(n => "--file=" + n));
        Start(new[] { new Job(argv, T.Get("adding", ("name", place.Model))) }, async () =>
        {
            Go(place, names[0]);
            await OcDialog.Info(this, T.Get("add_title"), T.Get("done_add", ("files", T.Count(names.Count, "file")), ("name", place.Model)));
        });
    }

    /// <summary>Export the selected models of a pack into a new pack file (all of them: the command names none).</summary>
    private async Task Export()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var place = _place;
        var rows = Chosen();
        if (place.View != "pack" || rows.Count == 0)
            return;
        var pack = place.Path!;
        var names = rows.Select(r => r.Id).OrderBy(n => n, StringComparer.Ordinal).ToList();
        if (names.ToHashSet().SetEquals(PackReader.PackModels(pack)))
            names = new List<string>();
        var d = new OcDialog(T.Get("export_title"));
        var what = names.Count > 0 ? string.Join(", ", names) : T.Get("export_all");
        if (names.Count == 1)
            d.Body.AddChild(Ui.Heading(T.Get("save_one", ("name", names[0]))));
        else
        {
            d.Body.AddChild(Ui.Heading(names.Count > 0 ? T.Get("save_some", ("count", names.Count)) : T.Get("save_all")));
            if (names.Count > 0)
                d.Body.AddChild(Ui.Label(T.Lines(names), Ui.Text));
        }
        d.Body.AddChild(Ui.Note(T.Get("save_note")));
        d.Body.AddChild(Ui.Gap(4));
        var row = Ui.HBox(6);
        d.Body.AddChild(row);
        var stem = names.Count == 1 ? names[0].Split('/').Last() : Path.GetFileName(Place.NormPath(pack).TrimEnd('\\', '/'));
        var path = Ui.Edit(Path.Combine(Path.GetDirectoryName(Place.NormPath(pack).TrimEnd('\\', '/')) ?? "", stem + Place.Oczip));
        row.AddChild(path);
        row.AddChild(Ui.Button(T.Get("browse"), async () =>
        {
            var chosen = await Pick.SaveOczip(d, T.Get("export_title"), Path.GetDirectoryName(path.Text), Path.GetFileName(path.Text));
            if (chosen is not null)
                path.Text = chosen;
        }));
        var verify = Ui.Check(T.Get("export_verify"));
        var delete = Ui.Check(T.Get("export_delete"));
        delete.Disabled = place.ReadOnly;
        delete.Toggled += on => { if (on) verify.ButtonPressed = true; };
        d.Body.AddChild(Ui.Gap(2));
        d.Body.AddChild(verify);
        d.Body.AddChild(delete);
        d.Body.AddChild(Ui.Gap(2));
        d.Body.AddChild(Ui.Note(T.Get(place.ReadOnly ? "export_from_file" : "export_hint")));
        string? target = null;
        d.Accepting = () =>
        {
            var t = Place.NormPath(path.Text);
            var problem = !Place.IsOczip(t) ? T.Get("export_name") : Place.PlaceProblem(Path.GetDirectoryName(t) ?? t) ?? (Place.SamePath(t, pack) ? T.Get("file_exists") : null);
            if (problem is not null)
            {
                d.ShowError(problem);
                return false;
            }
            target = t;
            return true;
        };
        d.FirstFocus = path;
        path.CaretColumn = path.Text.Length;
        if (!await d.ShowAsync(this) || target is null)
            return;
        bool overwrite = false;
        bool doVerify = verify.ButtonPressed, doDelete = delete.ButtonPressed;
        if (File.Exists(target))
        {
            if (!await OcDialog.Ask(this, T.Get("export_title"), T.Get("export_replace_q", ("path", T.Short(target)))))
                return;
            overwrite = true;
        }
        if (doDelete && !await OcDialog.Ask(this, T.Get("export_title"), T.Get("export_delete_q", ("path", T.Short(target)), ("pack", T.Short(pack)), ("what", what),
                ("checked", T.Get(doVerify ? "checked" : "unchecked")))))
            return;
        var argv = new List<string> { "export", pack, target };
        argv.AddRange(names);
        argv.Add(doVerify ? "--verify" : "--no-verify");
        if (doDelete)
            argv.Add("--delete");
        if (overwrite)
            argv.Add("--overwrite");
        Start(new[] { new Job(argv, T.Get("exporting", ("name", Path.GetFileName(target)))) { Models = names, ModelsAt = 3 } }, async () =>
        {
            if (doDelete && !Directory.Exists(pack))
                Go(new Place(target, pack: true));
            else
                Refresh();
            await OcDialog.Info(this, T.Get("export_title"), T.Get(doDelete ? "done_export_deleted" : "done_export", ("path", T.Short(target))));
        });
    }

    /// <summary>Remove the selected models (or files of the model shown) from the pack folder, after saying what that
    /// frees and what stays because other models need it. In a folder: the Recycle Bin.</summary>
    private async Task Remove()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var place = _place;
        var rows = Chosen();
        if (place.View == "folder")
        {
            await TrashSelected();
            return;
        }
        if (place.ReadOnly)
        {
            await OcDialog.Info(this, T.Get("remove_title"), T.Get("read_only_action"));
            return;
        }
        if (rows.Count == 0)
        {
            await OcDialog.Info(this, T.Get("remove_title"), T.Get("remove_what"));
            return;
        }
        List<string> names, files;
        string what;
        bool wholeModelGoes = false;
        if (place.View == "pack")
        {
            names = rows.Select(r => r.Id).ToList();
            files = new List<string>();
            what = names.Count <= 3 ? string.Join(", ", names) : T.Count(names.Count, "model");
        }
        else
        {
            names = new List<string> { place.Model! };
            files = rows.Select(r => r.Id).ToList();
            wholeModelGoes = files.Count == _listing.Count;
            what = $"{T.Count(files.Count, "file")} of {place.Model}";
        }
        PackReader.Removal removal;
        try
        {
            removal = await _reader.PreviewRemovalAsync(place.Path!, names, files);
        }
        catch (Exception e)
        {
            await OcDialog.Error(this, T.Get("remove_title"), T.Sentence(e.Message));
            return;
        }
        var keptText = removal.KeptBlobs > 0 ? T.Get("remove_kept", ("kept", T.SizeText(removal.KeptSize)), ("models", string.Join(", ", removal.NeededBy))) : "";
        if (!await OcDialog.Ask(this, T.Get("remove_title"), T.Get("remove_q", ("what", what), ("pack", T.Short(place.Path!)), ("freed", T.SizeText(removal.FreedSize)), ("kept", keptText))))
            return;
        var argv = new List<string> { "remove", place.Path! };
        argv.AddRange(names);
        argv.AddRange(files.Select(f => "--file=" + f));
        Start(new[] { new Job(argv, T.Get("removing", ("what", what))) { Models = names, ModelsAt = 2 } }, async () =>
        {
            if (place.View == "model" && wholeModelGoes)
                Go(place.Parent()!);
            await OcDialog.Info(this, T.Get("remove_title"), T.Get("done_remove", ("what", what)));
        });
    }

    /// <summary>Move the selected files and folders of a folder on disk to the Recycle Bin, after asking. Never inside
    /// a pack folder, which is changed only through the tool.</summary>
    private async Task TrashSelected()
    {
        var place = _place!;
        var rows = Chosen();
        if (_insidePack)
        {
            await OcDialog.Error(this, T.Get("remove_title"), T.Get("trash_inside", ("pack", T.Short(Place.PackFolderOf(place.Path!) ?? ""))));
            return;
        }
        if (place.Path is null || rows.Count == 0)
        {
            await OcDialog.Info(this, T.Get("remove_title"), T.Get("trash_what"));
            return;
        }
        var paths = rows.Select(r => Path.Combine(place.Path, r.Name)).ToArray();
        long size = await Task.Run(() => paths.Sum(Lister.TreeSize));
        var packs = rows.Where(r => r.Kind is "pack" or "oczip").ToList();
        var what = paths.Length == 1 ? T.Short(paths[0]) : T.Count(paths.Length, "item");
        var note = packs.Count > 0 ? T.Get("trash_packs", ("packs", T.Count(packs.Count, T.Get("pack_kind")))) : "";
        if (!await OcDialog.Ask(this, T.Get("remove_title"), T.Get("trash_q", ("what", what), ("size", T.SizeText(size)), ("packs", note))))
            return;
        var problem = Trash.ToTrash(paths, size);
        if (problem is not null)
            await OcDialog.Error(this, T.Get("remove_title"), T.Get("trash_failed", ("problem", problem)));
        Refresh();
    }

    /// <summary>A line of text the user enters, starting as `value`; check(text) -> a problem shown under it, or null.
    /// Null if cancelled or left unchanged.</summary>
    private async Task<string?> AskText(string title, string prompt, string value, Func<string, string?> check)
    {
        var d = new OcDialog(title);
        d.Body.AddChild(Ui.Heading(prompt));
        var edit = Ui.Edit(value);
        edit.CustomMinimumSize = new Vector2(Ui.Px(440), 0);
        d.Body.AddChild(edit);
        string? result = null;
        d.Accepting = () =>
        {
            var text = edit.Text.Trim();
            var problem = text != value ? check(text) : null;
            if (problem is not null)
            {
                d.ShowError(problem);
                return false;
            }
            if (text != value)
                result = text;
            return true;
        };
        d.FirstFocus = edit;
        edit.SelectAll();
        if (!await d.ShowAsync(this))
            return null;
        return result;
    }

    /// <summary>Rebase the selected models, or the selected files of the model shown: Find better bases, or Make independent.</summary>
    private async Task Rebase()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var place = _place;
        var rows = Chosen();
        if (place.View is not ("pack" or "model") || place.ReadOnly || rows.Count == 0)
            return;
        var names = rows.Select(r => r.Id).OrderBy(n => n, StringComparer.Ordinal).ToList();
        bool files = place.View == "model";
        var fixedNames = rows.Where(r => r.BaseChoice != "auto").Select(r => r.Id).ToList();
        var d = new OcDialog(T.Get("rebase_title"));
        if (names.Count == 1)
            d.Body.AddChild(Ui.Heading(files ? T.Get("rebase_file_one", ("name", names[0]), ("model", place.Model)) : T.Get("rebase_one", ("name", names[0]))));
        else
        {
            d.Body.AddChild(Ui.Heading(files ? T.Get("rebase_file_some", ("count", names.Count), ("model", place.Model)) : T.Get("rebase_some", ("count", names.Count))));
            d.Body.AddChild(Ui.Label(T.Lines(names), Ui.Text));
        }
        var group = new ButtonGroup();
        var find = Ui.Radio(T.Get("rebase_find"), group, true);
        var alone = Ui.Radio(T.Get("rebase_alone"), group);
        CheckBox? also = null;
        d.Body.AddChild(Ui.Gap(4));
        foreach (var (radio, key) in new[] { (find, "find"), (alone, "alone") })
        {
            d.Body.AddChild(radio);
            var indent = Ui.HBox(0);
            indent.AddChild(new Control { CustomMinimumSize = new Vector2(Ui.Px(26), 0) });
            indent.AddChild(Ui.Note(T.Get($"rebase_{key}_note")));
            d.Body.AddChild(indent);
            if (key == "find" && fixedNames.Count > 0)
            {
                also = Ui.Check(T.Get("rebase_also", ("names", T.NamesText(fixedNames))), fixedNames.Count == names.Count);
                var row = Ui.HBox(0);
                row.AddChild(new Control { CustomMinimumSize = new Vector2(Ui.Px(26), 0) });
                row.AddChild(also);
                d.Body.AddChild(row);
            }
        }
        d.Body.AddChild(Ui.Gap(4));
        d.Body.AddChild(Ui.Note(T.Get("rebase_hint")));
        if (!await d.ShowAsync(this))
            return;
        bool makeAlone = alone.ButtonPressed;
        var todo = makeAlone || (also?.ButtonPressed ?? false) ? names : names.Where(n => !fixedNames.Contains(n)).ToList();
        if (todo.Count == 0)
            return;
        List<string> argv;
        string what;
        if (files)
        {
            argv = new List<string> { "rebase", place.Path!, place.Model! };
            argv.AddRange(todo.Select(n => "--file=" + n));
            what = todo.Count == 1 ? $"{todo[0]} of {place.Model}" : $"{T.Count(todo.Count, "file")} of {place.Model}";
        }
        else
        {
            argv = new List<string> { "rebase", place.Path! };
            argv.AddRange(todo);
            what = todo.Count == 1 ? todo[0] : T.Count(todo.Count, "model");
        }
        if (makeAlone)
            argv.Add("--no-base");
        Start(new[] { new Job(argv, T.Get("rebasing", ("what", what))) { Models = files ? new List<string>() : todo, ModelsAt = 2 } }, async () =>
        {
            var result = _runner.Events.LastOrDefault(e => e.Event == "summary")?.Text ?? "";
            await OcDialog.Info(this, T.Get("rebase_title"), T.Get("done_rebase", ("what", what), ("result", T.Sentence(result))));
        });
    }

    /// <summary>Rename the selected model (in a pack) or file (in a model).</summary>
    private async Task Rename()
    {
        if (_place is null || (_runner?.Running ?? true))
            return;
        var place = _place;
        var rows = Chosen();
        if (place.ReadOnly)
        {
            await OcDialog.Info(this, T.Get("rename_title"), T.Get("read_only_action"));
            return;
        }
        if (rows.Count != 1)
        {
            await OcDialog.Info(this, T.Get("rename_title"), T.Get(place.View == "model" ? "rename_file_what" : "rename_what"));
            return;
        }
        var old = rows[0].Id;
        List<string> argv;
        string shown;
        string? newName;
        if (place.View == "model")
        {
            var have = _listing.Select(r => r.Id).ToHashSet();
            newName = await AskText(T.Get("rename_title"), T.Get("rename_file_prompt", ("model", place.Model), ("path", old)), old,
                n => Rules.PathProblem(n) ?? (have.Contains(n) ? T.Get("rename_file_taken", ("model", place.Model), ("path", n)) : null));
            argv = new List<string> { "rename", place.Path!, place.Model!, "--file", old, newName ?? "" };
            shown = $"{place.Model}:{old}";
        }
        else
        {
            newName = await AskText(T.Get("rename_title"), T.Get("rename_prompt", ("name", old)), old, Rules.NameProblem);
            argv = new List<string> { "rename", place.Path!, old, newName ?? "" };
            shown = old;
        }
        if (newName is null)
            return;
        Start(new[] { new Job(argv, T.Get("renaming", ("name", shown))) }, async () =>
        {
            Go(place, newName);
            await OcDialog.Info(this, T.Get("rename_title"), T.Get("done_rename", ("old", shown), ("new", newName)));
        });
    }

    // ============================================================================================ damage

    /// <summary>Keep what a verify found lost, for the ⚠ marks (Mark) and for cleaning up (CleanUp).</summary>
    private void Remember(DamageReport damage)
    {
        var marks = new DamageMarks { Pack = Place.NormPath(damage.Pack), Stamp = Place.PackStamp(damage.Pack) };
        foreach (var (model, path, own, _, _) in damage.Lost)
        {
            marks.Lost[(model, path)] = own;
            marks.Models[model] = marks.Models.GetValueOrDefault(model) + 1;
        }
        _marks = marks;
    }

    /// <summary>The damage report of a verify that failed, to read and copy; for a pack folder, Clean up.</summary>
    private async Task ShowDamage(DamageReport damage)
    {
        var d = new OcDialog(T.Get("damage_title"), resizable: true);
        int lost = damage.Lost.Count;
        d.Body.AddChild(Ui.Heading(lost > 0 ? T.Get("damage_heading", ("files", T.Count(lost, "file"))) : T.Get("damage_heading_other")));
        var note = Ui.Note(T.Get("damage_note"));
        note.CustomMinimumSize = new Vector2(Ui.Px(760), 0);
        d.Body.AddChild(note);
        var text = new TextEdit
        {
            Text = damage.Text, Editable = false, CustomMinimumSize = new Vector2(Ui.Px(820), Ui.Px(380)), SizeFlagsVertical = SizeFlags.ExpandFill,
            ContextMenuEnabled = true, ScrollFitContentHeight = false, WrapMode = TextEdit.LineWrappingMode.None,
        };
        text.AddThemeFontOverride("font", Ui.Mono);
        text.AddThemeFontSizeOverride("font_size", Ui.SmallFontSize);
        text.AddThemeColorOverride("font_color", Ui.LogText);
        text.AddThemeColorOverride("font_readonly_color", Ui.LogText);
        text.AddThemeStyleboxOverride("normal", Ui.Flat(Ui.LogBg, 8, Ui.PanelBorder, 1, 8));
        text.AddThemeStyleboxOverride("read_only", Ui.Flat(Ui.LogBg, 8, Ui.PanelBorder, 1, 8));
        text.AddThemeStyleboxOverride("focus", Ui.Flat(Ui.LogBg, 8, new Color(Ui.Accent, 0.5f), 1, 8));
        d.Body.AddChild(text);
        bool writable = !Place.IsOczip(damage.Pack);
        if (!writable)
            d.Body.AddChild(Ui.Note(T.Get("clean_read_only")));
        d.HideOk();
        d.CancelButton.Text = T.Get("close");
        bool clean = false;
        var cleanButton = d.AddButton(T.Get("clean_up"), () => { clean = true; d.CancelButton.EmitSignal(BaseButton.SignalName.Pressed); }, atLeft: true);
        cleanButton.Disabled = !(writable && lost > 0);
        Button copyButton = null!;
        copyButton = d.AddButton(T.Get("copy"), () => { DisplayServer.ClipboardSet(damage.Text); copyButton.Text = T.Get("copied"); }, atLeft: true);
        await d.ShowAsync(this);
        if (clean)
            await CleanUp(damage);
    }

    /// <summary>Remove what a verify found lost, at the level the user types: ERASE (the lost files; models left with
    /// none go), ERASE MOD (the models with lost files, whole), ERASE FULL (those, and the models that need data of
    /// them); only while the pack is as it was verified.</summary>
    private async Task CleanUp(DamageReport damage)
    {
        var pack = damage.Pack;
        var byModel = new Dictionary<string, List<(string path, bool own)>>();
        foreach (var (model, path, own, _, _) in damage.Lost)
            byModel.GetOrAdd(model).Add((path, own));
        var filesOf = damage.Models.ToDictionary(m => m.name, m => m.files);
        var emptied = byModel.Where(kv => kv.Value.Count >= filesOf.GetValueOrDefault(kv.Key, kv.Value.Count + 1)).Select(kv => kv.Key).OrderBy(n => n, StringComparer.Ordinal).ToList();
        var damaged = byModel.Keys.OrderBy(n => n, StringComparer.Ordinal).ToList();
        var affected = damage.Affected.ToList();
        var packName = Path.GetFileName(pack.TrimEnd('\\', '/'));
        var title = T.Get("cleaning", ("name", packName));
        var jobs = new Dictionary<string, List<Job>>
        {
            ["ERASE"] = new(),
            ["ERASE MOD"] = new() { new Job(new[] { "remove", pack }.Concat(damaged), title) { Models = damaged, ModelsAt = 2 } },
            ["ERASE FULL"] = new() { new Job(new[] { "remove", pack }.Concat(damaged).Concat(affected), title) { Models = damaged.Concat(affected).ToList(), ModelsAt = 2 } },
        };
        foreach (var name in byModel.Keys.OrderBy(n => byModel[n].Any(i => i.own)).ThenBy(n => n, StringComparer.Ordinal))
        {
            if (emptied.Contains(name))
                jobs["ERASE"].Add(new Job(new[] { "remove", pack, name }, title) { Models = new List<string> { name }, ModelsAt = 2 });
            else
                jobs["ERASE"].Add(new Job(new[] { "remove", pack, name }.Concat(byModel[name].Select(i => "--file=" + i.path)), title));
        }
        bool many = emptied.Count != 1;
        var levels = new List<(string level, string what, string risk)>
        {
            ("ERASE", T.Get("erase", ("files", T.Count(damage.Lost.Count, "file")), ("models", T.Count(byModel.Count, "model")),
                ("emptied", emptied.Count > 0 ? T.Get("erase_emptied", ("names", T.NamesText(emptied)), ("verb", many ? "are" : "is")) : "")), T.Get("erase_risk")),
            ("ERASE MOD", T.Get("erase_mod", ("names", T.NamesText(damaged))), T.Get("erase_mod_risk")),
            ("ERASE FULL", affected.Count > 0 ? T.Get("erase_full", ("names", T.NamesText(damaged.Concat(affected)))) : T.Get("erase_full_same", ("names", T.NamesText(damaged))),
                T.Get(affected.Count > 0 ? "erase_full_risk" : "erase_full_same_risk")),
        };
        var d = new OcDialog(T.Get("clean_title"));
        d.Body.AddChild(Ui.Heading(T.Get("clean_heading", ("pack", packName))));
        d.Body.AddChild(OcDialog.PathLine(pack));
        d.Body.AddChild(Ui.Gap(6));
        var grid = new GridContainer { Columns = 2 };
        grid.AddThemeConstantOverride("h_separation", Ui.Px(14));
        grid.AddThemeConstantOverride("v_separation", Ui.Px(2));
        d.Body.AddChild(grid);
        foreach (var (level, what, risk) in levels)
        {
            var l = Ui.Label(level, Ui.Text, bold: true);
            l.SizeFlagsVertical = SizeFlags.ShrinkBegin;
            grid.AddChild(l);
            grid.AddChild(Ui.Label(what, Ui.Text, wrap: true));
            grid.AddChild(new Control());
            grid.AddChild(Ui.Note(risk));
        }
        d.Body.AddChild(Ui.Gap(8));
        d.Body.AddChild(Ui.Label(T.Get("clean_prompt"), Ui.Text));
        var typed = Ui.Edit("");
        typed.CustomMinimumSize = new Vector2(Ui.Px(200), 0);
        typed.SizeFlagsHorizontal = SizeFlags.ShrinkBegin;
        d.Body.AddChild(typed);
        string? chosen = null;
        d.Accepting = () =>
        {
            var level = typed.Text.Trim();
            if (!jobs.ContainsKey(level))
            {
                d.ShowError(T.Get("clean_typed"));
                return false;
            }
            var stamp = Place.PackStamp(pack);
            if (stamp is null || !Equals(stamp, _marks?.Stamp))
            {
                d.ShowError(T.Get("clean_changed"));
                return false;
            }
            chosen = level;
            return true;
        };
        d.FirstFocus = typed;
        if (await d.ShowAsync(this) && chosen is not null)
            Start(jobs[chosen], (Action)(() => { _ = OcDialog.Info(this, T.Get("clean_title"), T.Get("done_clean")); }));
    }

    // ============================================================================================ running the tool

    /// <summary>Run llm_archive.py once per job, one after another, showing what it prints; stop at the first failure.
    /// `done` is called once all have succeeded.</summary>
    private void Start(IEnumerable<Job> jobs, Action done)
    {
        _done = done;
        foreach (var b in _buttons.Values)
            b.Disabled = true;
        _runner.Start(jobs, done);
    }

    private void Start(IEnumerable<Job> jobs, Func<Task> done) => Start(jobs, (Action)(() => { _ = done(); }));

    private async void JobsFinished(bool ok, Job? failed, int code, string output, DamageReport? damage)
    {
        var pending = _pendingOverwrite;
        _pendingOverwrite = null;
        Finish();
        if (ok)
        {
            Fx.Sfx.Done();
            return;
        }
        Fx.Sfx.Error();
        if (pending is not null)
        {
            await AskOverwriteAndRetry(pending);
            return;
        }
        if (damage is not null)
        {
            Remember(damage);
            Refresh();
            await ShowDamage(damage);
            return;
        }
        var last = Enumerable.Reverse(output.Split('\n')).Select(l => l.Trim()).FirstOrDefault(l => l.Length > 0) ?? $"exit code {code}";
        await OcDialog.Error(this, T.Get("failed"), T.Sentence(last) + "\n\n" + T.Get("see_log"));
    }

    private void Finish()
    {
        if (_busy.Visible)
            _veil.Release();
        _busy.Close();
        if (_quitAfter)
        {
            Quit();
            return;
        }
        var gone = _place?.Path is { } path && !Directory.Exists(path) && !File.Exists(path) ? path : null;
        if (gone is not null)
        {
            // the job took the place shown (export --delete of every model: the pack folder), so it is not listed
            // again, which would say it cannot be: its nearest parent instead
            while (!Directory.Exists(gone) && Path.GetDirectoryName(gone) is { } up && up != gone)
                gone = up;
            Go(new Place(gone, pack: Place.IsPack(gone)));
        }
        else
            Refresh();  // also turns the buttons back on
    }

    private async void Cancel()
    {
        if (!(_runner?.Running ?? false))
            return;
        if (!_runner.Cancelling)
        {
            _runner.Cancel();
            _status.Text = T.Get("cancelling");
            _busy.SetCancelling();
        }
        else if (await OcDialog.Ask(this, T.Get("stop_now"), T.Get("force_q")) && _runner.Running)
            _runner.Kill();
    }

    private async void Close()
    {
        if (!(_runner?.Running ?? false))
            Quit();
        else if (await OcDialog.Ask(this, "OverCompressing", T.Get("quit_q")))
        {
            _quitAfter = true;
            Cancel();
        }
    }

    // ============================================================================================ intro and outro

    private MarginContainer _ui = null!;
    private bool _introPlaying;
    private bool _quitting;
    private Tween? _introTween;

    /// <summary>The board powers up: the traces draw themselves out of the nodes, the panels rise into place, the
    /// buttons come one by one, the version types itself into the status line. About 1.2 s; any key or click ends it.</summary>
    private async void PlayIntro()
    {
        if (!Ui.Effects)
            return;
        _introPlaying = true;
        _circuit.Reveal = 0f;
        _ui.Modulate = new Color(1, 1, 1, 0);
        _ui.Position = new Vector2(0, 14);
        Fx.Sfx.Boot();
        var tw = _introTween = CreateTween().SetParallel();
        tw.TweenMethod(Callable.From<float>(v => _circuit.Reveal = v), 0f, 1f, 0.95).SetTrans(Tween.TransitionType.Sine).SetEase(Tween.EaseType.Out);
        tw.TweenProperty(_ui, "modulate", Colors.White, 0.5).SetDelay(0.35);
        tw.TweenProperty(_ui, "position", Vector2.Zero, 0.5).SetDelay(0.35).SetTrans(Tween.TransitionType.Quad).SetEase(Tween.EaseType.Out);
        _ = TypeVersion();
        await ToSignal(GetTree().CreateTimer(1.25), SceneTreeTimer.SignalName.Timeout);
        EndIntro();
    }

    /// <summary>The status line types the window's name and version, each coming character a flicker of glyphs first.</summary>
    private async Task TypeVersion()
    {
        await ToSignal(GetTree().CreateTimer(0.5), SceneTreeTimer.SignalName.Timeout);
        const string glyphs = "01<>/\\|#%&=+-:;";
        var rng = new Random();
        var text = "OverCompressing " + (_versions.Length > 0 ? _versions : "GUI " + T.GuiVersion);
        _status.AddThemeFontOverride("font", Ui.Mono);
        try
        {
            for (int i = 0; i <= text.Length && _introPlaying; i++)
            {
                var flicker = new string(Enumerable.Range(0, Math.Min(3, text.Length - i)).Select(_ => glyphs[rng.Next(glyphs.Length)]).ToArray());
                _status.Text = text[..i] + flicker;
                await ToSignal(GetTree().CreateTimer(0.014), SceneTreeTimer.SignalName.Timeout);
            }
            if (_introPlaying)
                _status.Text = text;
        }
        finally
        {
            if (!_introPlaying)
                _status.RemoveThemeFontOverride("font");
        }
    }

    private void EndIntro()
    {
        if (!_introPlaying)
            return;
        _introPlaying = false;
        _introTween?.Kill();
        _circuit.Reveal = 1f;
        _ui.Modulate = Colors.White;
        _ui.Position = Vector2.Zero;
        foreach (var b in _buttons.Values)
            b.Modulate = Colors.White;
        _status.RemoveThemeFontOverride("font");
        SayWhere();
    }

    /// <summary>Leave: with effects, the panels sink, the traces draw back into their nodes and the picture
    /// collapses to a line of light and a dot (about 0.45 s); then the settings are saved and the window closes.</summary>
    private async void Quit()
    {
        if (_quitting)
            return;
        _quitting = true;
        EndIntro();
        if (Ui.Effects)
        {
            Fx.Sfx.Off();
            var tw = CreateTween().SetParallel();
            tw.TweenProperty(_ui, "modulate", new Color(1, 1, 1, 0), 0.22);
            tw.TweenProperty(_ui, "position", new Vector2(0, 14), 0.22).SetTrans(Tween.TransitionType.Quad).SetEase(Tween.EaseType.In);
            tw.TweenMethod(Callable.From<float>(v => _circuit.Reveal = v), 1f, 0f, 0.3);
            await ToSignal(GetTree().CreateTimer(0.1), SceneTreeTimer.SignalName.Timeout);
            await _veil.CrtOff(0.34);
        }
        SaveSettings();
        GetTree().Quit();
    }

    private void SaveSettings()
    {
        // the custom cursors hold textures the rendering server would otherwise report as leaked at exit
        Input.SetCustomMouseCursor(null, Input.CursorShape.Arrow);
        Input.SetCustomMouseCursor(null, Input.CursorShape.Ibeam);
        var window = GetWindow();
        _settings.WindowMaximized = window.Mode == Window.ModeEnum.Maximized;
        if (!_settings.WindowMaximized)
        {
            _settings.WindowWidth = window.Size.X;
            _settings.WindowHeight = window.Size.Y;
        }
        _settings.Save();
    }

    // ============================================================================================ settings

    /// <summary>Where the tool and its Python are: shown at the first start when they are not found, and from ⚙.</summary>
    private async Task<bool> SettingsDialog(bool firstRun)
    {
        if (_runner?.Running ?? false)
            return false;
        var d = new OcDialog(T.Get("settings_title"));
        d.Body.AddChild(Ui.Heading(T.Get("settings_heading")));
        d.Body.AddChild(Ui.Gap(4));
        d.Body.AddChild(Ui.Label(T.Get("settings_tool"), Ui.Text));
        var toolRow = Ui.HBox(6);
        d.Body.AddChild(toolRow);
        var tool = Ui.Edit(_settings.Tool ?? _tool);
        tool.CustomMinimumSize = new Vector2(Ui.Px(480), 0);
        toolRow.AddChild(tool);
        toolRow.AddChild(Ui.Button(T.Get("browse"), async () =>
        {
            var chosen = await Pick.AnyFile(d, T.Get("settings_tool"), Path.GetDirectoryName(tool.Text), "*.py ; Python script");
            if (chosen is not null)
            {
                tool.Text = chosen;
                if (ToolLocator.PythonFor(chosen) is { } py && (python.Text.Trim().Length == 0 || !File.Exists(python.Text.Trim())))
                    python.Text = py;
            }
        }));
        d.Body.AddChild(Ui.Gap(4));
        d.Body.AddChild(Ui.Label(T.Get("settings_python"), Ui.Text));
        var pyRow = Ui.HBox(6);
        d.Body.AddChild(pyRow);
        python = Ui.Edit(_settings.Python ?? _python);
        pyRow.AddChild(python);
        pyRow.AddChild(Ui.Button(T.Get("browse"), async () =>
        {
            var chosen = await Pick.AnyFile(d, T.Get("settings_python"), Path.GetDirectoryName(python.Text), "*.exe ; python.exe");
            if (chosen is not null)
                python.Text = chosen;
        }));
        var found = ToolLocator.Find(new Settings(), Path.GetDirectoryName(OS.GetExecutablePath()) ?? ".");
        if (found is not null && tool.Text.Trim().Length == 0)
        {
            tool.Text = found.Tool;
            python.Text = found.Python;
        }
        d.Body.AddChild(Ui.Gap(4));
        var result = Ui.Label("", Ui.TextDim, wrap: true);
        var checkRow = Ui.HBox(8);
        d.Body.AddChild(checkRow);
        checkRow.AddChild(Ui.Button(T.Get("settings_check"), async () => await Check()));
        checkRow.AddChild(result);
        d.Body.AddChild(Ui.Gap(4));
        d.Body.AddChild(Ui.Note(T.Get("settings_note")));
        d.Body.AddChild(Ui.Gap(8));
        var effects = Ui.Check(T.Get("settings_effects"), _settings.Effects);
        d.Body.AddChild(effects);
        var effectsNote = Ui.HBox(0);
        effectsNote.AddChild(new Control { CustomMinimumSize = new Vector2(Ui.Px(26), 0) });
        effectsNote.AddChild(Ui.Note(T.Get("settings_effects_note")));
        d.Body.AddChild(effectsNote);
        var sounds = Ui.Check(T.Get("settings_sounds"), _settings.Sounds);
        d.Body.AddChild(sounds);
        async Task<bool> Check()
        {
            var py = python.Text.Trim();
            var tl = tool.Text.Trim();
            if (!File.Exists(py) || !File.Exists(tl))
            {
                result.Text = T.Get("settings_bad", ("problem", !File.Exists(tl) ? "the tool is not there" : "python.exe is not there"));
                result.AddThemeColorOverride("font_color", Ui.Error);
                return false;
            }
            result.Text = "…";
            var (ok, text) = await new PackReader(py, tl).VersionAsync();
            result.Text = ok ? T.Get("settings_ok", ("version", text)) : T.Get("settings_bad", ("problem", text));
            result.AddThemeColorOverride("font_color", ok ? Ui.Ok : Ui.Error);
            return ok;
        }
        d.AcceptingAsync = async () =>
        {
            if (!await Check())
                return false;
            _settings.Python = python.Text.Trim();
            _settings.Tool = tool.Text.Trim();
            _settings.Effects = effects.ButtonPressed;
            _settings.Sounds = sounds.ButtonPressed;
            _settings.Save();
            ApplyEffects();
            return true;
        };
        d.FirstFocus = tool;
        if (!await d.ShowAsync(this))
            return false;
        if (!firstRun)
        {
            await UseTool(_settings.Python!, _settings.Tool!);
            Refresh();
        }
        return true;
    }

    private LineEdit python = null!;

    /// <summary>Turn the effects and sounds on or off as the settings say.</summary>
    private void ApplyEffects()
    {
        Ui.Effects = _settings.Effects;
        Fx.Sfx.Enabled = _settings.Sounds;
        _glow.Environment.GlowEnabled = Ui.Effects;
        _circuit.Animated = Ui.Effects;
        _circuit.Intensity = Ui.Effects ? 1f : 0.6f;
        _circuit.QueueRedraw();
        _backdrop.SetIntensity(Ui.Effects ? 1f : 0.5f);
        _backdrop.Animated = Ui.Effects;
        if (!Ui.Effects)
            _logBox.BorderColor = new Color(Ui.Neon2, 0.25f);
    }

    // ============================================================================================ dev hooks

    public void DevSetView(string mode)
    {
        if (_viewButtons.TryGetValue(mode, out var b))
        {
            b.ButtonPressed = true;
            _viewMode = mode;
            Filtered();
        }
    }

    public void DevSelectFirst(int n)
    {
        var ids = InOrder().Select(i => _rows[i].Id).Take(n).ToList();
        _anchor = ids.FirstOrDefault();
        Select(ids.ToHashSet());
    }

    public void DevCancel() => Cancel();
    public void DevLegend() => ShowLegend();
    public void DevBoardOnly() { _ui.Visible = false; _circuit.Intensity = 2.5f; _circuit.Animated = false; _circuit.QueueRedraw(); }
    public string DevTrailInfo() => _trail.Info;
    public void DevGo(string text) { var p = Place.FromText(text); if (p is not null) Go(p); }
    public void DevClose() => Close();

    public void DevMenu()
    {
        var first = InOrder().FirstOrDefault();
        var rect = first is not null ? _tree.GetItemAreaRect(first) : new Rect2(Vector2.Zero, Vector2.Zero);
        Popup(first, _tree.GlobalPosition + rect.Position + new Vector2(Ui.Px(120), rect.Size.Y / 2));
    }

    public async void DevTrash(string path)
    {
        var problem = Trash.ToTrash(new[] { path }, Lister.TreeSize(path));
        GD.Print(problem is null ? $"trash: ok {path} exists={File.Exists(path)}" : $"trash: refused: {problem}");
        await OcDialog.Info(this, T.Get("remove_title"), problem ?? "✔ Moved to the Recycle Bin.");
    }

    public void DevStartJob(string[] argv)
    {
        Start(new[] { new Job(argv, "▶ " + argv[0]) { Retry = argv[0] == "restore" ? OfferOverwrite : null } }, (Action)(() => { _ = OcDialog.Info(this, argv[0], "✔ Done."); }));
    }

    public void DevOpenDialog(string name)
    {
        switch (name)
        {
            case "verify": _ = Verify(); break;
            case "pack": _ = Pack(); break;
            case "extract": _ = Extract(); break;
            case "export": _ = Export(); break;
            case "rebase": _ = Rebase(); break;
            case "rename": _ = Rename(); break;
            case "remove": _ = Remove(); break;
            case "settings": _ = SettingsDialog(false); break;
            case "busy":
                _busy.Open("▶ Packing Qwen/Qwen2.5-7B  (1/2)");
                _busy.SetLine("model-00002-of-00004.safetensors   safetensors+ans16   3,685.7 MiB ->  1,086.0 MiB   29.47%");
                _busy.SetStep("packing Qwen/Qwen2.5-7B  ·  model-00003-of-00004.safetensors");
                break;
            case "damage":
                _ = ShowDamage(new Core.DamageReport
                {
                    Pack = _place?.Path ?? "", Text = "DAMAGE REPORT\n  pack: " + (_place?.Path ?? "") + "\n  at fault (1 blob):\n    Qwen/Qwen2.5-0.5B: model.safetensors: its blob is missing\n  lost (2 files):\n    Qwen/Qwen2.5-0.5B: model.safetensors  942.3 MiB  weights  (its own blob)\n    mlx-community/Qwen2.5-0.5B-Instruct-bf16: model.safetensors  942.3 MiB  weights  (through Qwen/Qwen2.5-0.5B: model.safetensors)\n  intact: 2 models verified",
                    Lost = { ("Qwen/Qwen2.5-0.5B", "model.safetensors", true, 1, true), ("mlx-community/Qwen2.5-0.5B-Instruct-bf16", "model.safetensors", false, 1, true) },
                    Models = { ("Qwen/Qwen2.5-0.5B", 9, 1), ("mlx-community/Qwen2.5-0.5B-Instruct-bf16", 9, 1) },
                });
                break;
        }
    }
}

internal static class DictExt
{
    public static TValue GetOrAdd<TKey, TValue>(this Dictionary<TKey, TValue> d, TKey key) where TKey : notnull where TValue : new()
    {
        if (!d.TryGetValue(key, out var v))
            d[key] = v = new TValue();
        return v;
    }
}
