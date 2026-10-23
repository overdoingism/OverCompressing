using System;
using System.Collections.Generic;
using Godot;

namespace OverCompressingUI.UI;

/// <summary>The window's look: cyber + neon. A near-black ground, cyan and magenta light, glass panels with glowing
/// edges, display type (Orbitron) for the small labels and Exo 2 for the text, Share Tech Mono for the log. Colors
/// above 1.0 are HDR: with 2D HDR and the glow pass (Fx) they bloom, which is what makes a line of light neon rather
/// than a colored line. The theme is built in code, once the display scale is known.</summary>
public static class Ui
{
    // palette
    public static readonly Color Bg = new("#05070f");
    public static readonly Color Panel = new(0.03f, 0.045f, 0.09f, 0.56f);
    public static readonly Color PanelSolid = new("#0a0e1c");
    public static readonly Color PanelBorder = new(0f, 0.94f, 1f, 0.22f);
    public static readonly Color Text = new("#e8f1ff");
    public static readonly Color TextDim = new("#8fa3c7");
    public static readonly Color TextFaint = new("#5a6a8a");
    public static readonly Color Neon = new("#00f0ff");       // cyan
    public static readonly Color Neon2 = new("#ff2bd6");      // magenta
    public static readonly Color Neon3 = new("#8a5cff");      // violet
    public static readonly Color Amber = new("#ff9f1c");      // orange: the view toggles
    public static readonly Color Accent = Neon;
    public static readonly Color AccentSoft = new(0f, 0.94f, 1f, 0.2f);
    public static readonly Color Error = new("#ff3860");
    public static readonly Color Ok = new("#39ff14");
    public static readonly Color Warn = new("#ffe600");
    public static readonly Color Selection = new(0f, 0.75f, 0.9f, 0.28f);
    public static readonly Color SelectionText = new("#ffffff");
    public static readonly Color Hover = new(0f, 0.94f, 1f, 0.06f);
    public static readonly Color LogBg = new(0.02f, 0.035f, 0.08f, 0.8f);
    public static readonly Color LogText = new("#bfe9ff");
    public static readonly Color LogCommand = new("#ff6fe3");
    public static readonly Color Context = new("#5f6f8f");
    public static readonly Color Damaged = new("#ff5c7a");

    /// <summary>Row tints by kind, and the legend's (neon) colors.</summary>
    public static readonly Dictionary<string, Color> KindTint = new()
    {
        ["folder"] = new Color("#ffe600", 0.07f), ["drive"] = new Color("#ffe600", 0.07f), ["pack"] = new Color("#8a5cff", 0.14f),
        ["oczip"] = new Color("#00a6ff", 0.13f), ["model"] = new Color("#39ff14", 0.08f), ["weights"] = new Color("#ff3860", 0.11f),
    };
    public static readonly Dictionary<string, Color> KindColor = new()
    {
        ["folder"] = new Color("#ffe600"), ["drive"] = new Color("#ffe600"), ["pack"] = new Color("#a98bff"),
        ["oczip"] = new Color("#3fc3ff"), ["model"] = new Color("#39ff14"), ["weights"] = new Color("#ff3860"), ["file"] = new Color("#9fb3d9"),
    };
    public static readonly (string kind, string label)[] Legend =
        { ("folder", "Folder"), ("pack", "Pack folder"), ("oczip", "Pack file"), ("model", "Model"), ("weights", "Weights") };

    public static float Scale = 1f;
    public static int FontSize = 14;
    public static int SmallFontSize = 12;
    public static Font Font = null!;
    public static Font Bold = null!;
    public static Font Display = null!;
    public static Font Mono = null!;
    public static Theme Theme = null!;
    public static bool Effects = true;

    public static int Px(float n) => Mathf.RoundToInt(n);

    /// <summary>`c` lifted above 1.0 so that the glow pass makes it shine; alpha kept.</summary>
    public static Color Hdr(Color c, float k = 1.8f) => new(c.R * k, c.G * k, c.B * k, c.A);

    // ------------------------------------------------------------------------------------------ fonts

    private static Godot.Collections.Array<Font> SystemFallbacks(bool mono)
    {
        var fallbacks = new Godot.Collections.Array<Font>();
        foreach (var name in mono
                     ? new[] { "Cascadia Mono", "Consolas", "Segoe UI Symbol", "Microsoft JhengHei UI", "Microsoft YaHei UI", "Segoe UI Emoji" }
                     : new[] { "Segoe UI", "Segoe UI Symbol", "Microsoft JhengHei UI", "Microsoft YaHei UI", "Yu Gothic UI", "Malgun Gothic", "Segoe UI Emoji" })
            fallbacks.Add(new SystemFont { FontNames = new[] { name }, AllowSystemFallback = true });
        return fallbacks;
    }

    /// <summary>A bundled OpenType font (variable weight), with the system's fonts behind it for every script and
    /// symbol it lacks. If the file cannot be loaded, the system font takes its place.</summary>
    private static Font Bundled(string file, int weight, bool mono = false)
    {
        var fallbacks = SystemFallbacks(mono);
        var font = GD.Load<FontFile>("res://assets/fonts/" + file);
        if (font is null)
        {
            var sys = new SystemFont { FontNames = new[] { mono ? "Consolas" : "Segoe UI" }, FontWeight = weight, AllowSystemFallback = true, Fallbacks = fallbacks };
            return sys;
        }
        font.Antialiasing = TextServer.FontAntialiasing.Gray;
        font.Hinting = TextServer.Hinting.Light;
        font.SubpixelPositioning = TextServer.SubpixelPositioning.Auto;
        font.Fallbacks = fallbacks;
        var v = new FontVariation { BaseFont = font };
        var tag = TextServerManager.GetPrimaryInterface().NameToTag("wght");
        v.VariationOpentype = new Godot.Collections.Dictionary { { tag, weight } };
        return v;
    }

    public static StyleBoxFlat Flat(Color bg, float radius = 8, Color? border = null, int borderWidth = 1, int pad = 0)
    {
        var sb = new StyleBoxFlat { BgColor = bg };
        sb.SetCornerRadiusAll(Px(radius));
        if (border is not null)
        {
            sb.BorderColor = border.Value;
            sb.SetBorderWidthAll(borderWidth);
        }
        sb.SetContentMarginAll(Px(pad));
        sb.AntiAliasing = true;
        return sb;
    }

    /// <summary>A glass panel: dark, translucent, a thin cyan edge, a soft glow around it.</summary>
    public static StyleBoxFlat Glass(float radius = 12, int pad = 12, Color? edge = null)
    {
        var sb = Flat(Panel, radius, edge ?? PanelBorder, 1, pad);
        sb.ShadowColor = new Color(edge ?? Neon, 0.10f);
        sb.ShadowSize = Px(16);
        sb.ShadowOffset = Vector2.Zero;
        return sb;
    }

    /// <summary>Build the theme once the scale is known.</summary>
    public static Theme BuildTheme()
    {
        Font = Bundled("Exo2[wght].ttf", 500);
        Bold = Bundled("Exo2[wght].ttf", 700);
        Display = Bundled("Orbitron[wght].ttf", 600);
        Mono = Bundled("ShareTechMono-Regular.ttf", 400, mono: true);
        var t = new Theme { DefaultFont = Font, DefaultFontSize = FontSize };

        // buttons: glass, a neon edge that lights up under the mouse
        var normal = Flat(new Color(0f, 0.94f, 1f, 0.05f), 6, new Color(Neon, 0.22f), 1, 0);
        normal.ContentMarginLeft = normal.ContentMarginRight = Px(10);
        normal.ContentMarginTop = normal.ContentMarginBottom = Px(5);
        var hover = (StyleBoxFlat)normal.Duplicate();
        hover.BgColor = new Color(Neon, 0.12f);
        hover.BorderColor = Hdr(Neon, 1.2f);
        hover.ShadowColor = new Color(Neon, 0.35f);
        hover.ShadowSize = Px(10);
        var pressed = (StyleBoxFlat)normal.Duplicate();
        pressed.BgColor = new Color(Neon, 0.3f);
        pressed.BorderColor = Hdr(Neon, 1.6f);
        pressed.ShadowColor = new Color(Neon, 0.5f);
        pressed.ShadowSize = Px(14);
        var disabled = (StyleBoxFlat)normal.Duplicate();
        disabled.BgColor = new Color(1, 1, 1, 0.02f);
        disabled.BorderColor = new Color(1, 1, 1, 0.06f);
        var focus = Flat(new Color(0, 0, 0, 0), 6, new Color(Neon2, 0.7f), 1, 0);
        focus.ContentMarginLeft = focus.ContentMarginRight = Px(10);
        focus.ContentMarginTop = focus.ContentMarginBottom = Px(5);
        foreach (var type in new[] { "Button", "CheckBox", "OptionButton", "MenuButton" })
        {
            t.SetStylebox("normal", type, normal);
            t.SetStylebox("hover", type, hover);
            t.SetStylebox("pressed", type, pressed);
            t.SetStylebox("hover_pressed", type, pressed);
            t.SetStylebox("disabled", type, disabled);
            t.SetStylebox("focus", type, focus);
            t.SetColor("font_color", type, Text);
            t.SetColor("font_hover_color", type, Color.Color8(255, 255, 255));
            t.SetColor("font_pressed_color", type, Color.Color8(255, 255, 255));
            t.SetColor("font_disabled_color", type, new Color(Text, 0.3f));
            t.SetColor("font_focus_color", type, Text);
            t.SetColor("icon_disabled_color", type, new Color(1, 1, 1, 0.3f));
        }
        var empty = new StyleBoxEmpty();
        empty.SetContentMarginAll(Px(2));
        foreach (var s in new[] { "normal", "hover", "pressed", "hover_pressed", "disabled", "focus" })
            t.SetStylebox(s, "CheckBox", empty);
        t.SetIcon("checked", "CheckBox", Icons.Check(true));
        t.SetIcon("unchecked", "CheckBox", Icons.Check(false));
        t.SetIcon("checked_disabled", "CheckBox", Icons.Check(true));
        t.SetIcon("unchecked_disabled", "CheckBox", Icons.Check(false));
        t.SetIcon("radio_checked", "CheckBox", Icons.Radio(true));
        t.SetIcon("radio_unchecked", "CheckBox", Icons.Radio(false));
        t.SetIcon("radio_checked_disabled", "CheckBox", Icons.Radio(true));
        t.SetIcon("radio_unchecked_disabled", "CheckBox", Icons.Radio(false));
        t.SetConstant("h_separation", "CheckBox", Px(7));

        // line edits
        var edit = Flat(new Color(0, 0, 0, 0.35f), 6, new Color(Neon, 0.18f), 1, 0);
        edit.ContentMarginLeft = edit.ContentMarginRight = Px(10);
        edit.ContentMarginTop = edit.ContentMarginBottom = Px(6);
        var editFocus = (StyleBoxFlat)edit.Duplicate();
        editFocus.BorderColor = Hdr(Neon, 1.3f);
        editFocus.ShadowColor = new Color(Neon, 0.3f);
        editFocus.ShadowSize = Px(10);
        var editRo = (StyleBoxFlat)edit.Duplicate();
        editRo.BgColor = new Color(1, 1, 1, 0.02f);
        editRo.BorderColor = new Color(1, 1, 1, 0.06f);
        t.SetStylebox("normal", "LineEdit", edit);
        t.SetStylebox("focus", "LineEdit", editFocus);
        t.SetStylebox("read_only", "LineEdit", editRo);
        t.SetColor("font_color", "LineEdit", Text);
        t.SetColor("font_uneditable_color", "LineEdit", TextDim);
        t.SetColor("font_placeholder_color", "LineEdit", TextFaint);
        t.SetColor("caret_color", "LineEdit", Hdr(Neon));
        t.SetColor("selection_color", "LineEdit", new Color(Neon, 0.35f));
        t.SetColor("font_selected_color", "LineEdit", Color.Color8(255, 255, 255));
        t.SetConstant("caret_width", "LineEdit", 2);

        // labels, rich text (the log)
        t.SetColor("font_color", "Label", Text);
        t.SetColor("default_color", "RichTextLabel", LogText);
        t.SetColor("selection_color", "RichTextLabel", new Color(Neon, 0.3f));
        t.SetColor("font_selected_color", "RichTextLabel", Color.Color8(255, 255, 255));
        t.SetStylebox("normal", "RichTextLabel", Flat(LogBg, 10, new Color(Neon2, 0.25f), 1, 10));
        t.SetStylebox("focus", "RichTextLabel", Flat(LogBg, 10, new Color(Neon2, 0.6f), 1, 10));
        t.SetFont("normal_font", "RichTextLabel", Mono);
        t.SetFontSize("normal_font_size", "RichTextLabel", SmallFontSize + 1);
        t.SetFont("mono_font", "RichTextLabel", Mono);

        // tree
        t.SetStylebox("panel", "Tree", Glass(10, 4));
        t.SetStylebox("focus", "Tree", Glass(10, 4, Hdr(Neon, 1.1f)));
        t.SetStylebox("selected", "Tree", Flat(new Color(0, 0, 0, 0), 4));  // selection is painted by the window
        t.SetStylebox("selected_focus", "Tree", Flat(new Color(0, 0, 0, 0), 4));
        t.SetStylebox("hovered", "Tree", Flat(Hover, 4));
        t.SetStylebox("hovered_dimmed", "Tree", Flat(new Color(1, 1, 1, 0.02f), 4));
        t.SetStylebox("hovered_selected", "Tree", Flat(Hover, 4));
        t.SetStylebox("hovered_selected_focus", "Tree", Flat(Hover, 4));
        t.SetStylebox("cursor", "Tree", new StyleBoxEmpty());
        t.SetStylebox("cursor_unfocused", "Tree", new StyleBoxEmpty());
        t.SetStylebox("button_pressed", "Tree", new StyleBoxEmpty());
        var title = Flat(new Color(0f, 0.94f, 1f, 0.04f), 0, null, 0, 0);
        title.ContentMarginLeft = title.ContentMarginRight = Px(8);
        title.ContentMarginTop = title.ContentMarginBottom = Px(7);
        title.BorderColor = new Color(Neon, 0.35f);
        title.BorderWidthBottom = 1;
        var titleHover = (StyleBoxFlat)title.Duplicate();
        titleHover.BgColor = new Color(Neon, 0.1f);
        var titlePressed = (StyleBoxFlat)title.Duplicate();
        titlePressed.BgColor = new Color(Neon, 0.25f);
        t.SetStylebox("title_button_normal", "Tree", title);
        t.SetStylebox("title_button_hover", "Tree", titleHover);
        t.SetStylebox("title_button_pressed", "Tree", titlePressed);
        t.SetColor("title_button_color", "Tree", new Color(Neon, 0.85f));
        t.SetFont("title_button_font", "Tree", Display);
        t.SetFontSize("title_button_font_size", "Tree", SmallFontSize - 2);
        t.SetColor("font_color", "Tree", Text);
        t.SetColor("font_hovered_color", "Tree", Color.Color8(255, 255, 255));
        t.SetColor("font_selected_color", "Tree", Text);
        t.SetColor("guide_color", "Tree", new Color(Neon, 0.05f));
        t.SetColor("relationship_line_color", "Tree", new Color(Neon, 0.3f));
        t.SetColor("parent_hl_line_color", "Tree", new Color(Neon2, 0.6f));
        t.SetColor("children_hl_line_color", "Tree", new Color(Neon, 0.3f));
        t.SetColor("drop_position_color", "Tree", Neon);
        t.SetConstant("v_separation", "Tree", Px(7));
        t.SetConstant("h_separation", "Tree", Px(6));
        t.SetConstant("item_margin", "Tree", Px(20));
        t.SetConstant("inner_item_margin_left", "Tree", Px(6));
        t.SetConstant("inner_item_margin_right", "Tree", Px(6));
        t.SetConstant("draw_relationship_lines", "Tree", 1);
        t.SetConstant("draw_guides", "Tree", 0);
        t.SetConstant("relationship_line_width", "Tree", 1);
        t.SetConstant("parent_hl_line_width", "Tree", 1);
        t.SetConstant("children_hl_line_width", "Tree", 1);
        t.SetConstant("scroll_border", "Tree", Px(4));
        t.SetConstant("scroll_speed", "Tree", 12);
        t.SetIcon("arrow", "Tree", Icons.Arrow(false));
        t.SetIcon("arrow_collapsed", "Tree", Icons.Arrow(true));
        t.SetIcon("arrow_collapsed_mirrored", "Tree", Icons.Arrow(true));

        // scrollbars
        var track = Flat(new Color(1, 1, 1, 0.03f), 3);
        var grabber = Flat(new Color(Neon, 0.35f), 3);
        var grabberHl = Flat(Hdr(Neon, 1.0f) with { A = 0.7f }, 3);
        foreach (var type in new[] { "VScrollBar", "HScrollBar" })
        {
            t.SetStylebox("scroll", type, track);
            t.SetStylebox("scroll_focus", type, track);
            t.SetStylebox("grabber", type, grabber);
            t.SetStylebox("grabber_highlight", type, grabberHl);
            t.SetStylebox("grabber_pressed", type, grabberHl);
            foreach (var icon in new[] { "increment", "decrement", "increment_highlight", "decrement_highlight", "increment_pressed", "decrement_pressed" })
                t.SetIcon(icon, type, Icons.Blank());
        }

        // split container
        t.SetConstant("separation", "VSplitContainer", Px(8));
        t.SetConstant("minimum_grab_thickness", "VSplitContainer", Px(8));
        t.SetIcon("v_grabber", "VSplitContainer", Icons.Blank());
        t.SetIcon("grabber", "VSplitContainer", Icons.Blank());
        t.SetStylebox("split_bar_background", "VSplitContainer", Flat(new Color(1, 1, 1, 0.0f), 2));

        // popup menu
        var menu = Flat(new Color(0.02f, 0.035f, 0.08f, 0.96f), 8, new Color(Neon, 0.45f), 1, 6);
        menu.ShadowColor = new Color(Neon, 0.25f);
        menu.ShadowSize = Px(14);
        t.SetStylebox("panel", "PopupMenu", menu);
        t.SetStylebox("hover", "PopupMenu", Flat(new Color(Neon, 0.22f), 5));
        t.SetStylebox("separator", "PopupMenu", Flat(new Color(Neon, 0.2f), 0));
        t.SetColor("font_color", "PopupMenu", Text);
        t.SetColor("font_hover_color", "PopupMenu", Color.Color8(255, 255, 255));
        t.SetColor("font_disabled_color", "PopupMenu", new Color(Text, 0.3f));
        t.SetConstant("v_separation", "PopupMenu", Px(6));
        t.SetConstant("item_start_padding", "PopupMenu", Px(10));
        t.SetConstant("item_end_padding", "PopupMenu", Px(14));

        // embedded windows (dialogs): a neon frame
        var frame = Flat(PanelSolid, 12, Hdr(Neon, 1.1f) with { A = 0.9f }, 1, 0);
        frame.ShadowColor = new Color(Neon, 0.28f);
        frame.ShadowSize = Px(28);
        frame.ShadowOffset = Vector2.Zero;
        frame.ExpandMarginTop = Px(38);
        frame.ExpandMarginLeft = frame.ExpandMarginRight = frame.ExpandMarginBottom = 1;
        t.SetStylebox("embedded_border", "Window", frame);
        var frameUnfocused = (StyleBoxFlat)frame.Duplicate();
        frameUnfocused.BorderColor = new Color(Neon, 0.35f);
        frameUnfocused.ShadowColor = new Color(Neon, 0.12f);
        t.SetStylebox("embedded_unfocused_border", "Window", frameUnfocused);
        t.SetColor("title_color", "Window", Hdr(Neon, 1.0f));
        t.SetFont("title_font", "Window", Display);
        t.SetFontSize("title_font_size", "Window", SmallFontSize);
        t.SetConstant("title_height", "Window", Px(38));
        t.SetConstant("close_h_offset", "Window", Px(26));
        t.SetConstant("close_v_offset", "Window", Px(25));
        t.SetIcon("close", "Window", Icons.Close(false));
        t.SetIcon("close_pressed", "Window", Icons.Close(true));

        // tooltips
        var tip = Flat(new Color(0.02f, 0.035f, 0.08f, 0.97f), 6, new Color(Neon, 0.5f), 1, 6);
        t.SetStylebox("panel", "TooltipPanel", tip);
        t.SetColor("font_color", "TooltipLabel", Text);
        t.SetFontSize("font_size", "TooltipLabel", SmallFontSize);

        // text edit (the damage report)
        t.SetColor("font_color", "TextEdit", LogText);
        t.SetColor("font_readonly_color", "TextEdit", LogText);
        t.SetColor("selection_color", "TextEdit", new Color(Neon, 0.3f));

        // the list's text alone gets a dark rim (the board shows through the list panel); elsewhere plain glyphs
        // keep the neon look
        t.SetColor("font_outline_color", "Tree", new Color(0.01f, 0.015f, 0.04f, 0.9f));
        t.SetConstant("outline_size", "Tree", 2);
        t.SetStylebox("panel", "PanelContainer", Glass());
        t.SetStylebox("panel", "Panel", new StyleBoxEmpty());
        Theme = t;
        return t;
    }

    // ------------------------------------------------------------------------------------------ factories

    /// <summary>A button with the small life of this window: a tick when the mouse comes, a press that dips it, a
    /// click sound.</summary>
    public static Button Button(string text, Action? pressed = null, bool primary = false)
    {
        var b = new Button { Text = text, FocusMode = Control.FocusModeEnum.None };
        if (pressed is not null)
            b.Pressed += pressed;
        if (primary)
        {
            var sb = Flat(new Color(Neon, 0.28f), 6, Hdr(Neon, 1.3f), 1, 0);
            sb.ContentMarginLeft = sb.ContentMarginRight = Px(16);
            sb.ContentMarginTop = sb.ContentMarginBottom = Px(6);
            sb.ShadowColor = new Color(Neon, 0.35f);
            sb.ShadowSize = Px(12);
            var hv = (StyleBoxFlat)sb.Duplicate();
            hv.BgColor = new Color(Neon, 0.45f);
            hv.BorderColor = Hdr(Neon, 1.8f);
            hv.ShadowColor = new Color(Neon, 0.55f);
            b.AddThemeStyleboxOverride("normal", sb);
            b.AddThemeStyleboxOverride("hover", hv);
            b.AddThemeStyleboxOverride("pressed", hv);
            b.AddThemeColorOverride("font_color", Color.Color8(255, 255, 255));
            b.AddThemeFontOverride("font", Bold);
        }
        Animate(b);
        return b;
    }

    /// <summary>A button in another neon than cyan: its edge, glow and pressed fill in `c`.</summary>
    public static void Tint(Button b, Color c)
    {
        var normal = Flat(new Color(c, 0.06f), 6, new Color(c, 0.35f), 1, 0);
        normal.ContentMarginLeft = normal.ContentMarginRight = Px(10);
        normal.ContentMarginTop = normal.ContentMarginBottom = Px(5);
        var hover = (StyleBoxFlat)normal.Duplicate();
        hover.BgColor = new Color(c, 0.14f);
        hover.BorderColor = Hdr(c, 1.2f);
        hover.ShadowColor = new Color(c, 0.35f);
        hover.ShadowSize = Px(10);
        var pressed = (StyleBoxFlat)normal.Duplicate();
        pressed.BgColor = new Color(c, 0.32f);
        pressed.BorderColor = Hdr(c, 1.6f);
        pressed.ShadowColor = new Color(c, 0.5f);
        pressed.ShadowSize = Px(14);
        b.AddThemeStyleboxOverride("normal", normal);
        b.AddThemeStyleboxOverride("hover", hover);
        b.AddThemeStyleboxOverride("pressed", pressed);
        b.AddThemeStyleboxOverride("hover_pressed", pressed);
        b.AddThemeColorOverride("font_color", c.Lightened(0.35f));
        b.AddThemeColorOverride("font_hover_color", Color.Color8(255, 255, 255));
        b.AddThemeColorOverride("font_pressed_color", Color.Color8(255, 255, 255));
        b.AddThemeColorOverride("font_hover_pressed_color", Color.Color8(255, 255, 255));
    }

    /// <summary>The press dip and the sounds, for any button.</summary>
    public static void Animate(BaseButton b)
    {
        b.Resized += () => b.PivotOffset = b.Size / 2;
        b.MouseEntered += () => { if (!b.Disabled) Fx.Sfx.Tick(); };
        b.ButtonDown += () =>
        {
            b.PivotOffset = b.Size / 2;
            b.CreateTween().TweenProperty(b, "scale", new Vector2(0.94f, 0.94f), 0.06);
        };
        b.ButtonUp += () =>
        {
            b.CreateTween().TweenProperty(b, "scale", Vector2.One, 0.16).SetTrans(Tween.TransitionType.Back).SetEase(Tween.EaseType.Out);
        };
        b.Pressed += () => Fx.Sfx.Click();
    }

    public static Label Label(string text, Color? color = null, bool bold = false, bool wrap = false, int? size = null)
    {
        var l = new Label { Text = text };
        if (color is not null)
            l.AddThemeColorOverride("font_color", color.Value);
        if (bold)
            l.AddThemeFontOverride("font", Bold);
        if (size is not null)
            l.AddThemeFontSizeOverride("font_size", size.Value);
        if (wrap)
        {
            l.AutowrapMode = TextServer.AutowrapMode.WordSmart;
            l.CustomMinimumSize = new Vector2(Px(460), 0);
        }
        l.MouseFilter = Control.MouseFilterEnum.Pass;
        return l;
    }

    /// <summary>A small label in the display face: the window's "machine" voice (headings of columns, chips).</summary>
    public static Label DisplayLabel(string text, Color? color = null, int? size = null)
    {
        var l = Label(text, color ?? Neon, size: size ?? SmallFontSize - 1);
        l.AddThemeFontOverride("font", Display);
        return l;
    }

    public static Label Note(string text) => Label(text, TextDim, wrap: true);

    public static Label Heading(string text) => Label(text, Text, bold: true, wrap: true, size: FontSize + 1);

    public static LineEdit Edit(string text = "", string placeholder = "")
    {
        var e = new LineEdit { Text = text, PlaceholderText = placeholder, SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, ContextMenuEnabled = true };
        e.CustomMinimumSize = new Vector2(Px(320), 0);
        return e;
    }

    public static HBoxContainer HBox(int sep = 6) { var h = new HBoxContainer(); h.AddThemeConstantOverride("separation", Px(sep)); return h; }
    public static VBoxContainer VBox(int sep = 6) { var v = new VBoxContainer(); v.AddThemeConstantOverride("separation", Px(sep)); return v; }
    public static Control Spacer() => new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, MouseFilter = Control.MouseFilterEnum.Ignore };
    public static Control Gap(int h) => new Control { CustomMinimumSize = new Vector2(0, Px(h)), MouseFilter = Control.MouseFilterEnum.Ignore };

    public static VSeparator VSep()
    {
        var s = new VSeparator();
        s.AddThemeStyleboxOverride("separator", Flat(new Color(Neon, 0.25f), 0));
        s.AddThemeConstantOverride("separation", Px(12));
        return s;
    }

    public static PanelContainer Card(int pad = 12, float radius = 12)
    {
        var p = new PanelContainer();
        p.AddThemeStyleboxOverride("panel", Glass(radius, pad));
        return p;
    }

    /// <summary>A legend chip: the kind's word in the display face, in its neon, in a glowing outline.</summary>
    public static Label Chip(string text, Color color, bool bold = false)
    {
        var l = new Label { Text = $" {text} " };
        var sb = Flat(new Color(color, 0.1f), 5, new Color(color, bold ? 0.95f : 0.55f), 1, 0);
        sb.ContentMarginLeft = sb.ContentMarginRight = Px(7);
        sb.ContentMarginTop = sb.ContentMarginBottom = Px(3);
        if (bold)
        {
            sb.ShadowColor = new Color(color, 0.35f);
            sb.ShadowSize = Px(10);
            sb.BorderColor = Hdr(color, 1.3f);
        }
        l.AddThemeStyleboxOverride("normal", sb);
        l.AddThemeColorOverride("font_color", bold ? Hdr(color, 1.1f) : color.Lightened(0.15f));
        l.AddThemeFontSizeOverride("font_size", SmallFontSize - 2);
        l.AddThemeFontOverride("font", Display);
        l.MouseFilter = Control.MouseFilterEnum.Pass;
        return l;
    }

    public static float Measure(string text, Font? font = null, int? size = null)
        => (font ?? Font).GetStringSize(text, HorizontalAlignment.Left, -1, size ?? FontSize).X;

    public static CheckBox Check(string text, bool on = false)
    {
        var c = new CheckBox { Text = text, ButtonPressed = on, FocusMode = Control.FocusModeEnum.None };
        c.Pressed += () => Fx.Sfx.Click();
        return c;
    }

    public static CheckBox Radio(string text, ButtonGroup group, bool on = false)
    {
        var c = new CheckBox { Text = text, ButtonPressed = on, ButtonGroup = group, FocusMode = Control.FocusModeEnum.None };
        c.Pressed += () => Fx.Sfx.Click();
        return c;
    }
}
