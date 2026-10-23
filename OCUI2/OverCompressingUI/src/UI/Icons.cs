using System;
using System.Collections.Generic;
using Godot;

namespace OverCompressingUI.UI;

/// <summary>Small icons drawn from SVG at the window's scale (crisp on any display), cached by name.</summary>
public static class Icons
{
    private static readonly Dictionary<string, ImageTexture> Cache = new();

    public static ImageTexture FromSvg(string key, string svg, int size)
    {
        if (Cache.TryGetValue(key, out var cached))
            return cached;
        var img = new Image();
        var err = img.LoadSvgFromString(svg, Ui.Scale);
        if (err != Error.Ok)
        {
            img = Image.CreateEmpty(size, size, false, Image.Format.Rgba8);
            img.Fill(new Color(0, 0, 0, 0));
        }
        var tex = ImageTexture.CreateFromImage(img);
        if (size > 1)
            tex.SetSizeOverride(new Vector2I(size, size));  // rasterized at the display's scale, drawn at its canvas size
        Cache[key] = tex;
        return tex;
    }

    private static string Hex(Color c) => "#" + c.ToHtml(false);

    public static ImageTexture Blank() => FromSvg("blank", "<svg xmlns='http://www.w3.org/2000/svg' width='1' height='1'/>", 1);

    public static ImageTexture Check(bool on)
    {
        var box = $"<rect x='1.5' y='1.5' width='15' height='15' rx='4' fill='{(on ? Hex(Ui.Accent) : "none")}' fill-opacity='{(on ? 0.9 : 0)}' stroke='{(on ? Hex(Ui.Accent) : "#8a93a6")}' stroke-width='1.5'/>";
        var mark = on ? "<path d='M5 9.2l2.4 2.4L13 6.6' fill='none' stroke='#fff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/>" : "";
        return FromSvg("check" + on, $"<svg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 18 18'>{box}{mark}</svg>", 18);
    }

    public static ImageTexture Radio(bool on)
    {
        var ring = $"<circle cx='9' cy='9' r='7' fill='none' stroke='{(on ? Hex(Ui.Accent) : "#8a93a6")}' stroke-width='1.5'/>";
        var dot = on ? $"<circle cx='9' cy='9' r='3.5' fill='{Hex(Ui.Accent)}'/>" : "";
        return FromSvg("radio" + on, $"<svg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 18 18'>{ring}{dot}</svg>", 18);
    }

    public static ImageTexture Arrow(bool collapsed)
    {
        var path = collapsed ? "M6 4l5 4-5 4" : "M4 6l4 5 4-5";
        return FromSvg("arrow" + collapsed, $"<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><path d='{path}' fill='none' stroke='#9aa3b5' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/></svg>", 16);
    }

    public static ImageTexture Close(bool pressed)
    {
        var c = pressed ? "#ffffff" : "#9aa3b5";
        return FromSvg("close" + pressed, $"<svg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 18 18'><path d='M5 5l8 8M13 5l-8 8' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round'/></svg>", 18);
    }

    /// <summary>The mouse cursor: a cyan arrow with a magenta edge, at the display's pixel size (cursors are not scaled
    /// by the canvas).</summary>
    public static ImageTexture Cursor()
    {
        var svg = "<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32' viewBox='0 0 32 32'>" +
                  "<path d='M4 3l0 22 6-5 4 9 4-2-4-8.5 8 0z' fill='#06101c' stroke='" + Hex(Ui.Neon) + "' stroke-width='1.8' stroke-linejoin='round'/>" +
                  "<path d='M6 7l0 13 4.2-3.4 3.4 7.5' fill='none' stroke='" + Hex(Ui.Neon2) + "' stroke-width='1' stroke-opacity='0.8'/>" +
                  "</svg>";
        var img = new Image();
        img.LoadSvgFromString(svg, Ui.Scale);
        var tex = ImageTexture.CreateFromImage(img);
        return tex;
    }

    /// <summary>The text cursor: a neon I-beam.</summary>
    public static ImageTexture CursorBeam()
    {
        var svg = "<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32' viewBox='0 0 32 32'>" +
                  "<path d='M11 5h4M17 5h4M16 5v22M11 27h4M17 27h4' fill='none' stroke='#06101c' stroke-width='4' stroke-linecap='round'/>" +
                  "<path d='M11 5h4M17 5h4M16 5v22M11 27h4M17 27h4' fill='none' stroke='" + Hex(Ui.Neon) + "' stroke-width='1.6' stroke-linecap='round'/>" +
                  "</svg>";
        var img = new Image();
        img.LoadSvgFromString(svg, Ui.Scale);
        return ImageTexture.CreateFromImage(img);
    }

    /// <summary>The icon of a row by its kind: a folder, a drive, a pack (a folder with a seal), a pack file, a model
    /// (a stack), weights, a file.</summary>
    public static ImageTexture Kind(string kind)
    {
        var color = Hex(Ui.KindColor.TryGetValue(kind, out var c) ? c : Ui.KindColor["file"]);
        string body = kind switch
        {
            "folder" => $"<path d='M2 5.5A1.5 1.5 0 0 1 3.5 4H7l1.6 1.6H14.5A1.5 1.5 0 0 1 16 7.1V13a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 2 13z' fill='{color}' fill-opacity='0.9'/>",
            "drive" => $"<rect x='2' y='4' width='14' height='10' rx='2' fill='{color}' fill-opacity='0.9'/><circle cx='13' cy='11' r='1.1' fill='#10131b'/>",
            "pack" => $"<path d='M2 5.5A1.5 1.5 0 0 1 3.5 4H7l1.6 1.6H14.5A1.5 1.5 0 0 1 16 7.1V13a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 2 13z' fill='{color}' fill-opacity='0.9'/><path d='M6.5 11.2l1.8 1.8 3.5-3.6' fill='none' stroke='#10131b' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/>",
            "oczip" => $"<path d='M4 2.5h6.5L14 6v9.5H4z' fill='{color}' fill-opacity='0.9'/><path d='M7.5 4v1.5h1.5V7H7.5v1.5h1.5V10H7.5v1.5h1.5V13H7.5' fill='none' stroke='#10131b' stroke-width='1.1'/>",
            "model" => $"<ellipse cx='9' cy='5' rx='6' ry='2.3' fill='{color}' fill-opacity='0.95'/><path d='M3 5v3.5c0 1.3 2.7 2.3 6 2.3s6-1 6-2.3V5' fill='{color}' fill-opacity='0.7'/><path d='M3 8.5V12c0 1.3 2.7 2.3 6 2.3s6-1 6-2.3V8.5' fill='{color}' fill-opacity='0.5'/>",
            "weights" => $"<rect x='3' y='3' width='12' height='12' rx='2' fill='{color}' fill-opacity='0.9'/><path d='M6 11V7M9 11V5M12 11V8.5' stroke='#10131b' stroke-width='1.6' stroke-linecap='round'/>",
            _ => $"<path d='M4 2.5h6.5L14 6v9.5H4z' fill='{color}' fill-opacity='0.75'/><path d='M10.5 2.5V6H14' fill='none' stroke='#10131b' stroke-width='1'/>",
        };
        return FromSvg("kind:" + kind, $"<svg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 18 18'>{body}</svg>", 18);
    }
}
