using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace OverCompressingUI.Core;

/// <summary>What the list shows: the drives (path null), a folder, a pack (a pack folder or a pack file, shown as its
/// models), or one model of a pack. A port of the Tkinter window's Place.</summary>
public sealed class Place
{
    public const string Oczip = ".oczip";
    public const string Marker = "ocpack.json";
    public const string LockName = ".overcompressing.lock";
    public const string Kit = "_kit";
    public static readonly string[] WeightSuffixes = { ".safetensors", ".sft", ".gguf", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".h5", ".msgpack" };
    /// <summary>What systems leave in folders (compared ignoring case): not content, so a folder holding only these can
    /// become a pack (the tool's LEFT_BY_SYSTEMS; names starting with ".trash-" too).</summary>
    public static readonly HashSet<string> LeftBySystems = new(StringComparer.OrdinalIgnoreCase)
    {
        "thumbs.db", "ehthumbs.db", "desktop.ini", ".ds_store", ".directory", "$recycle.bin", "lost+found",
        "system volume information", ".spotlight-v100", ".fseventsd", ".trashes",
    };

    public string? Path { get; }
    public bool Pack { get; }
    public string? Model { get; }

    public Place(string? path = null, bool pack = false, string? model = null)
    {
        Path = path;
        Model = model;
        Pack = pack || model is not null;
    }

    public string View => Model is not null ? "model" : Pack ? "pack" : "folder";
    public bool ReadOnly => Pack && IsOczip(Path);
    public bool IsDrives => Path is null;

    public string Text() => Path is null ? T.Get("drives") : Model is not null ? $"{Path} › {Model}" : Path;

    public Place? Parent()
    {
        if (Path is null)
            return null;
        if (Model is not null)
            return new Place(Path, pack: true);
        var up = System.IO.Path.GetDirectoryName(Path);
        if (!string.IsNullOrEmpty(up) && up != Path)
            return new Place(up, pack: IsPack(up));
        return OperatingSystem.IsWindows() ? new Place() : null;
    }

    /// <summary>The place a row of this place opens: a folder, drive or pack, or a model of this pack.</summary>
    public Place Child(Row row)
    {
        if (View == "pack")
            return new Place(Path, model: row.Id);
        if (Path is null)
            return new Place(row.Name, pack: false);
        var full = System.IO.Path.Combine(Path, row.Name);
        return new Place(full, pack: IsPack(full));
    }

    public bool SamePlaceAs(Place? other) => other is not null && Text() == other.Text();

    /// <summary>The place a typed path names (a folder, a pack, or "&lt;pack&gt; › &lt;model&gt;"), or null. A path that
    /// exists is that path, whatever is in its name: the mark is read only otherwise, so a folder is never taken for
    /// a model (found by the downstream team on the Tkinter window: "a > b" opened model b of pack a, with that
    /// folder there).</summary>
    public static Place? FromText(string text)
    {
        text = text.Trim().Trim('"');
        if (text.Length == 0)
            return null;
        if (Existing(text) is { } whole)
            return new Place(whole, pack: IsPack(whole));
        int at = text.IndexOf('›');
        if (at < 0)
            return null;
        var path = Existing(text[..at].Trim());
        var model = text[(at + 1)..].Trim();
        if (path is null)
            return null;
        if (model.Length > 0)
            return IsPack(path) ? new Place(path, model: model) : null;
        return new Place(path, pack: IsPack(path));
    }

    /// <summary>`path` as a full path if it is a folder or a pack file there, else null.</summary>
    private static string? Existing(string path)
    {
        if (OperatingSystem.IsWindows() && path.Length == 2 && path[1] == ':')
            path += "\\";
        if (path.StartsWith("~"))
            path = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile) + path[1..];
        try { path = System.IO.Path.GetFullPath(path); } catch (Exception) { return null; }
        return Directory.Exists(path) || (IsOczip(path) && File.Exists(path)) ? path : null;
    }

    public static bool IsOczip(string? path) => path is not null && path.EndsWith(Oczip, StringComparison.OrdinalIgnoreCase);

    /// <summary>Whether `path` is a pack: a folder holding ocpack.json, or an .oczip file.</summary>
    public static bool IsPack(string path)
        => File.Exists(System.IO.Path.Combine(path, Marker)) || (IsOczip(path) && File.Exists(path));

    /// <summary>How a file is shown, by its name: oczip, weights (not compressed) or file.</summary>
    public static string KindOf(string name)
    {
        if (IsOczip(name))
            return "oczip";
        var low = name.ToLowerInvariant();
        return WeightSuffixes.Any(s => low.EndsWith(s)) ? "weights" : "file";
    }

    /// <summary>What changes when a pack changes: the time stamp (and size) of a pack folder's ocpack.json, which
    /// every change rewrites last, or of a pack file; null if it cannot be read.</summary>
    public static (long mtime, long size)? PackStamp(string path)
    {
        try
        {
            var f = new FileInfo(IsOczip(path) ? path : System.IO.Path.Combine(path, Marker));
            return f.Exists ? (f.LastWriteTimeUtc.Ticks, f.Length) : null;
        }
        catch (Exception) { return null; }
    }

    /// <summary>The pack folder `path` is or is inside (the nearest folder on the way up holding ocpack.json), or null.</summary>
    public static string? PackFolderOf(string path)
    {
        string folder;
        try { folder = System.IO.Path.GetFullPath(path); } catch (Exception) { return null; }
        while (true)
        {
            if (File.Exists(System.IO.Path.Combine(folder, Marker)))
                return folder;
            var up = System.IO.Path.GetDirectoryName(folder);
            if (string.IsNullOrEmpty(up) || up == folder)
                return null;
            folder = up;
        }
    }

    /// <summary>Why restore must not write into `path`, pack read from it, nor a pack file be written there: it is a
    /// pack folder or inside one. Null if it is neither.</summary>
    public static string? PlaceProblem(string path)
    {
        var inside = PackFolderOf(path);
        if (inside is null)
            return null;
        string full;
        try { full = System.IO.Path.GetFullPath(path).TrimEnd('\\', '/'); } catch (Exception) { full = path; }
        if (string.Equals(inside.TrimEnd('\\', '/'), full, StringComparison.OrdinalIgnoreCase))
            return $"{path} is a pack folder";
        return $"{path} is inside the pack folder {inside}";
    }

    /// <summary>Why `root` cannot become a pack folder (the tool's new_pack_problem, for the dialogs to say so before a
    /// run; the tool judges last): it is a pack folder already, lies inside one, is a file, or holds anything but _kit,
    /// the lock and what systems leave in folders. Null if it can: it need not exist.</summary>
    public static string? NewPackProblem(string root)
    {
        root = NormPath(root).TrimEnd('\\', '/');
        if (File.Exists(System.IO.Path.Combine(root, Marker)))
            return $"{root} is a pack folder already";
        if (PackFolderOf(root) is { } inside)
            return $"{root} is inside the pack folder {inside}: pack into that pack folder, or one of its own";
        if (File.Exists(root))
            return $"{root} is not a folder";
        if (Directory.Exists(root))
        {
            List<string> others;
            try
            {
                others = Directory.EnumerateFileSystemEntries(root).Select(p => System.IO.Path.GetFileName(p))
                    .Where(n => n != Kit && n != LockName && !LeftBySystems.Contains(n) && !n.StartsWith(".trash-", StringComparison.OrdinalIgnoreCase))
                    .OrderBy(n => n, StringComparer.Ordinal).ToList();
            }
            catch (Exception e) { return $"{root}: {e.Message}"; }
            if (others.Count > 0)
                return $"{root} is not a pack folder (no {Marker} in it) and holds other files, e.g. {others[0]}: give a new or empty folder";
        }
        return null;
    }

    /// <summary>Whether `path` lies inside `folder` (or is it).</summary>
    public static bool Holds(string folder, string path)
    {
        try
        {
            var a = System.IO.Path.GetFullPath(folder).TrimEnd('\\', '/') + System.IO.Path.DirectorySeparatorChar;
            var b = System.IO.Path.GetFullPath(path).TrimEnd('\\', '/') + System.IO.Path.DirectorySeparatorChar;
            return b.StartsWith(a, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception) { return false; }
    }

    /// <summary>Where restore puts a model's files: dest/&lt;its full name&gt; ("full"), dest/&lt;the last part of it&gt;
    /// ("last"), or dest itself ("files", with --flat).</summary>
    public static string ExtractTarget(string dest, string name, string layout)
    {
        if (layout == "full")
            return System.IO.Path.Combine(new[] { dest }.Concat(name.Split('/')).ToArray());
        return layout == "last" ? System.IO.Path.Combine(dest, name.Split('/').Last()) : dest;
    }

    public static string NormPath(string path)
    {
        try { return System.IO.Path.GetFullPath(path.Trim()); } catch (Exception) { return path.Trim(); }
    }

    public static bool SamePath(string a, string b)
        => string.Equals(NormPath(a).TrimEnd('\\', '/'), NormPath(b).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase);
}

/// <summary>The tool's rules for names and paths (name_problem, path_problem), so dialogs can refuse before a run.</summary>
public static class Rules
{
    private const string NameChars = "-_.+@()[] ";
    private static readonly string[] WindowsDevices =
        new[] { "CON", "PRN", "AUX", "NUL" }.Concat(Enumerable.Range(1, 9).Select(i => $"COM{i}")).Concat(Enumerable.Range(1, 9).Select(i => $"LPT{i}")).ToArray();

    /// <summary>Null if `repo` can name a model, else why not.</summary>
    public static string? NameProblem(string? repo)
    {
        if (string.IsNullOrEmpty(repo))
            return "a model name cannot be empty";
        foreach (var part in repo.Split('/'))
        {
            if (part.Length == 0 || part != part.Trim(' ') || part.EndsWith('.'))
                return $"bad model name '{repo}': no part may be empty, start or end with a space, or end with a dot";
            if (!part.All(ch => char.IsLetterOrDigit(ch) || NameChars.Contains(ch)))
                return $"bad model name '{repo}': use letters, digits, {NameChars.Trim()} and spaces, with / between parts";
            if (WindowsDevices.Contains(part.Split('.')[0].ToUpperInvariant()))
                return $"bad model name '{repo}': {part} is a reserved file name on Windows";
        }
        if (repo.Replace("/", "__").Length > 200)
            return $"bad model name '{repo}': too long";
        return null;
    }

    public const int PackNameMax = 100;  // characters: of a pack's name (the tool's PACK_NAME_MAX)

    /// <summary>Null if `name` can name a pack (the tool's pack_name_problem), else why not: it is only ever shown, so
    /// any text will do that shows as one line.</summary>
    public static string? PackNameProblem(string? name)
    {
        if (string.IsNullOrEmpty(name))
            return "a pack name cannot be empty";
        if (name != name.Trim())
            return $"bad pack name '{name}': it may not start or end with a space";
        if (name.EnumerateRunes().Count() > PackNameMax)
            return $"bad pack name '{name}': at most {PackNameMax} characters";
        foreach (var r in name.EnumerateRunes())
        {
            var c = System.Text.Rune.GetUnicodeCategory(r);
            if (c is System.Globalization.UnicodeCategory.Control or System.Globalization.UnicodeCategory.Format or System.Globalization.UnicodeCategory.Surrogate
                or System.Globalization.UnicodeCategory.PrivateUse or System.Globalization.UnicodeCategory.OtherNotAssigned
                or System.Globalization.UnicodeCategory.LineSeparator or System.Globalization.UnicodeCategory.ParagraphSeparator)
                return $"bad pack name '{name}': it may not hold control characters or line breaks";
        }
        return null;
    }

    /// <summary>Null if `path`, a file's path in a manifest, is relative, uses "/" and has no empty, "." or ".." part.</summary>
    public static string? PathProblem(string? path)
    {
        if (string.IsNullOrEmpty(path))
            return "empty file path";
        foreach (var part in path.Split('/'))
            if (part is "" or "." or ".." || part.Contains('\0'))
                return $"unsafe file path '{path}'";
        return null;
    }
}
