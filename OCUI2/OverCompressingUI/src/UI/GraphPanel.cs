using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using OverCompressingUI.Core;

namespace OverCompressingUI.UI;

/// <summary>The panel beside the graph: what is selected, in full. One model: its name, files, sizes, when it was
/// packed, how its bases were chosen, what it needs (with how much of each it reads) and what needs it, each a link
/// that selects that node. Several: the count and the sums. None: the pack, summed up.</summary>
public partial class GraphPanel : PanelContainer
{
    private Label _title = null!;
    private RichTextLabel _body = null!;
    public event Action<string>? NameClicked;

    public override void _Ready()
    {
        CustomMinimumSize = new Vector2(Ui.Px(300), 0);
        AddThemeStyleboxOverride("panel", Ui.Glass(10, 14));
        var box = Ui.VBox(8);
        AddChild(box);
        _title = Ui.DisplayLabel("", Ui.Neon, Ui.SmallFontSize);
        box.AddChild(_title);
        _body = new RichTextLabel
        {
            BbcodeEnabled = true, FitContent = false, SizeFlagsVertical = SizeFlags.ExpandFill, SelectionEnabled = true,
            ScrollActive = true, AutowrapMode = TextServer.AutowrapMode.WordSmart, FocusMode = FocusModeEnum.None,
        };
        _body.AddThemeStyleboxOverride("normal", new StyleBoxEmpty());
        _body.AddThemeStyleboxOverride("focus", new StyleBoxEmpty());
        _body.AddThemeFontOverride("normal_font", Ui.Font);
        _body.AddThemeFontSizeOverride("normal_font_size", Ui.SmallFontSize);
        _body.AddThemeFontOverride("bold_font", Ui.Bold);
        _body.AddThemeFontSizeOverride("bold_font_size", Ui.SmallFontSize);
        _body.AddThemeColorOverride("default_color", Ui.Text);
        _body.MetaClicked += meta => NameClicked?.Invoke(meta.ToString());
        _body.MetaUnderlined = false;
        box.AddChild(_body);
    }

    private static string Esc(string s) => s.Replace("[", "[lb]");
    private static string Dim(string s) => $"[color=#{Ui.TextDim.ToHtml(false)}]{Esc(s)}[/color]";
    private static string Link(string id, string text) => $"[url={Esc(id)}][color=#{Ui.Neon.ToHtml(false)}]{Esc(text)}[/color][/url]";
    private static string Head(string s) => $"\n[font_size={Ui.SmallFontSize - 1}][color=#{Ui.Neon.ToHtml(false)}]{Esc(s.ToUpperInvariant())}[/color][/font_size]\n";

    /// <summary>Show the selection: `rows` (model rows), the sum line for several, the pack's line for none.</summary>
    public void Show(List<Row> rows, string? sumLine, string? packLine)
    {
        if (rows.Count == 1)
        {
            var r = rows[0];
            var m = r.Summary;
            _title.Text = "MODEL";
            var b = new System.Text.StringBuilder();
            b.Append($"[b]{Esc(r.Name)}[/b]\n");
            if (r.Damaged)
                b.Append($"[color=#{Ui.Damaged.ToHtml(false)}]{Esc(T.Get("mark_file"))}[/color]\n");
            b.Append(Head("Stored"));
            b.Append($"{Esc(T.Count(r.Files ?? 0, "file"))}  ·  {Esc(T.SizeText(r.Size))} → {Esc(T.SizeText(r.Stored))}  {Dim("(" + r.Ratio + ")")}\n");
            if (r.Packed is not null)
                b.Append($"{Dim("packed")} {r.Packed.Value:yyyy-MM-dd HH:mm}\n");
            if (m is not null)
            {
                var choice = m.BaseChoice switch { "forced" => "bases set by hand (--base)", "none" => "no bases (--no-base)", _ => "bases chosen automatically" };
                b.Append($"{Dim(choice)}\n");
                if (m.Revision is { Length: > 0 })
                    b.Append($"{Dim("revision")} {Esc(m.Revision)}\n");
                b.Append(Head("Needs"));
                if (m.Needs.Count == 0 && m.NeedsRemoved.Count == 0)
                    b.Append(Dim("nothing: it stands on its own") + "\n");
                foreach (var n in m.Needs)
                {
                    var usage = m.Usage.TryGetValue(n, out var u) ? $"  {Dim($"reads {u * 100:F0}% of it")}" : "";
                    b.Append($"{Link(n, n)}{usage}\n");
                }
                foreach (var n in m.NeedsRemoved)
                    b.Append($"{Dim(T.Get("removed", ("name", n)))}\n");
                b.Append(Head("Needed by"));
                if (m.NeededBy.Count == 0)
                    b.Append(Dim("no other model") + "\n");
                foreach (var n in m.NeededBy)
                    b.Append($"{Link(n, n)}\n");
            }
            _body.Text = b.ToString();
        }
        else if (rows.Count > 1)
        {
            _title.Text = "SELECTION";
            var b = new System.Text.StringBuilder();
            b.Append($"[b]{Esc(sumLine ?? T.Count(rows.Count, "model"))}[/b]\n");
            b.Append(Head("Models"));
            foreach (var r in rows.OrderBy(r => r.Name, StringComparer.Ordinal))
                b.Append($"{Link(r.Id, r.Name)}  {Dim(T.SizeText(r.Stored))}\n");
            _body.Text = b.ToString();
        }
        else
        {
            _title.Text = "PACK";
            _body.Text = Esc(packLine ?? "") + "\n\n" + Dim("Click a node to see the model; Ctrl and Shift select several; double-click opens it.");
        }
    }
}
