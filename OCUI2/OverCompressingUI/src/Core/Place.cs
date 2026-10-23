using System;
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
    public static readonly string[] WeightSuffixes = { ".safetensors", ".sft", ".gguf", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".h5", ".msgpack" };

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

    /// <summary>The place a typed path names (a folder, a pack, or "&lt;pack&gt; › &lt;model&gt;"), or null.</summary>
    public static Place? FromText(string text)
    {
        text = text.Trim().Trim('"');
        if (text.Length == 0)
            return null;
        string path = text, model = "";
        int at = text.IndexOf('›');
        if (at >= 0)
        {
            path = text[..at].Trim();
            model = text[(at + 1)..].Trim();
        }
        if (OperatingSystem.IsWindows() && path.Length == 2 && path[1] == ':')
            path += "\\";
        if (path.StartsWith("~"))
            path = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile) + path[1..];
        try { path = System.IO.Path.GetFullPath(path); } catch (Exception) { return null; }
        bool dir = Directory.Exists(path), zip = IsOczip(path) && File.Exists(path);
        if (!dir && !zip)
            return null;
        if (model.Length > 0)
            return IsPack(path) ? new Place(path, model: model) : null;
        return new Place(path, pack: IsPack(path));
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
