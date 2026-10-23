using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace OverCompressingUI.Core;

/// <summary>What the window remembers between runs: where the tool is, the last choices in dialogs, the window's
/// size. Kept as JSON in Godot's user folder (%APPDATA%\Godot\app_userdata\OverCompressing\settings.json).</summary>
public sealed class Settings
{
    public string? Python { get; set; }
    public string? Tool { get; set; }
    public string PackMode { get; set; } = "folder";
    public string PackTarget { get; set; } = "";
    public string ExtractDest { get; set; } = "";
    public string ExtractLayout { get; set; } = "last";
    public string ViewMode { get; set; } = "list";
    public string? LastPlace { get; set; }
    public int WindowWidth { get; set; }
    public int WindowHeight { get; set; }
    public bool WindowMaximized { get; set; }
    public float LogShare { get; set; } = 0.26f;
    public string SortBy { get; set; } = "name";
    public bool SortDescending { get; set; }
    public bool Effects { get; set; } = true;  // glow, motes, the frosted veil, the sweep
    public bool Sounds { get; set; } = true;

    [JsonIgnore] public string FilePath { get; private set; } = "";

    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true, DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull };

    public static Settings Load(string path)
    {
        Settings s;
        try
        {
            s = File.Exists(path) ? JsonSerializer.Deserialize<Settings>(File.ReadAllText(path), Options) ?? new Settings() : new Settings();
        }
        catch (Exception)
        {
            s = new Settings();
        }
        s.FilePath = path;
        return s;
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, Options));
        }
        catch (Exception) { }
    }
}

/// <summary>Where llm_archive.py and a Python that can run it are: the settings, OVERCOMPRESSING_HOME, the folders
/// around this program (a development checkout, or a kit's tools), or a pack's _kit.</summary>
public static class ToolLocator
{
    public sealed record Found(string Python, string Tool, string How);

    public static IEnumerable<string> CandidateHomes(string exeDir)
    {
        var env = System.Environment.GetEnvironmentVariable("OVERCOMPRESSING_HOME");
        if (!string.IsNullOrWhiteSpace(env))
            yield return env;
        string? dir = exeDir;
        for (int i = 0; i < 4 && dir is not null; i++)
        {
            yield return dir;
            yield return Path.Combine(dir, "OverCompressing");
            dir = Path.GetDirectoryName(dir);
        }
        yield return @"V:\OverCompressing";
    }

    /// <summary>The tool script in `home` (a checkout, a kit, or the pack holding a kit), or null.</summary>
    public static string? ToolIn(string home)
    {
        foreach (var rel in new[] { @"tools\llm_archive.py", @"_kit\tools\llm_archive.py", "llm_archive.py" })
        {
            var p = Path.Combine(home, rel);
            if (File.Exists(p))
                return p;
        }
        return null;
    }

    /// <summary>A Python for the tool in `toolPath`: a portable copy's own, the checkout's .venv, python.org's, or `python` on PATH.</summary>
    public static string? PythonFor(string toolPath)
    {
        var toolsDir = Path.GetDirectoryName(toolPath)!;
        var home = Path.GetDirectoryName(toolsDir)!;
        foreach (var candidate in new[]
                 {
                     Path.Combine(home, @"python\python.exe"),  // a portable copy (the build guides' layout)
                     Path.Combine(home, @".venv\Scripts\python.exe"),
                     Path.Combine(Path.GetDirectoryName(home) ?? home, @".venv\Scripts\python.exe"),
                     Path.Combine(home, @"venv\Scripts\python.exe"),
                 })
            if (File.Exists(candidate))
                return candidate;
        var localPy = Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python313\python.exe");
        if (File.Exists(localPy))
            return localPy;
        foreach (var dir in (System.Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
        {
            var p = Path.Combine(dir.Trim(), "python.exe");
            if (dir.Trim().Length > 0 && File.Exists(p) && !dir.Contains("WindowsApps", StringComparison.OrdinalIgnoreCase))
                return p;
        }
        return null;
    }

    public static Found? Find(Settings settings, string exeDir)
    {
        if (!string.IsNullOrEmpty(settings.Tool) && File.Exists(settings.Tool) && !string.IsNullOrEmpty(settings.Python) && File.Exists(settings.Python))
            return new Found(settings.Python, settings.Tool, "settings");
        foreach (var home in CandidateHomes(exeDir))
        {
            var tool = ToolIn(home);
            if (tool is null)
                continue;
            var py = PythonFor(tool);
            if (py is not null)
                return new Found(py, tool, home);
        }
        return null;
    }
}
