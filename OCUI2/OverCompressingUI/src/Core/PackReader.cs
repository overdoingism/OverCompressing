using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace OverCompressingUI.Core;

/// <summary>Reading a pack is done through the tool itself (list --json, remove --dry-run --json), never by parsing
/// blobs here: the tool is the one reader of its format. Only manifests (plain JSON) are read directly, for what the
/// pack listing does not carry (which blobs a model lists, to sum a selection counting each once).</summary>
public sealed class PackReader
{
    public string Python { get; }
    public string Tool { get; }

    public PackReader(string python, string tool)
    {
        Python = python;
        Tool = tool;
    }

    public sealed class ToolFailed : Exception
    {
        public ToolFailed(string message) : base(message) { }
    }

    private static string LastError(List<ToolEvent> events, int code)
    {
        var err = events.LastOrDefault(e => e.Event == "error")?.Text;
        if (err is not null)
            return err;
        var last = events.LastOrDefault(e => e.Text.Trim().Length > 0 && e.Event != "progress")?.Text;
        return last ?? $"exit code {code}";
    }

    /// <summary>The pack at `path` as list --json shows it, with the manifests it could not use.</summary>
    public async Task<PackSummary> ReadPackAsync(string path, CancellationToken ct = default)
    {
        var (code, events) = await CliProcess.RunAsync(Python, Tool, new[] { "list", path }, ct);
        var pack = events.LastOrDefault(e => e.Event == "pack");
        if (pack is null)
            throw new ToolFailed(LastError(events, code));
        var s = PackSummary.From(pack.Fields!);
        foreach (var f in events.Where(e => e.Event == "fail"))
            s.Problems.Add((f.Str("path") ?? "?", f.Str("problem") ?? f.Text));
        return s;
    }

    /// <summary>One model of the pack, with its files, as list --json shows it.</summary>
    public async Task<ModelSummary> ReadModelAsync(string path, string model, CancellationToken ct = default)
    {
        var (code, events) = await CliProcess.RunAsync(Python, Tool, new[] { "list", path, model }, ct);
        var m = events.LastOrDefault(e => e.Event == "model");
        if (m is null)
            throw new ToolFailed(LastError(events, code));
        return ModelSummary.From(m.Fields!);
    }

    /// <summary>The names of the models of the pack at `path` (empty if it cannot be read): from the manifests.</summary>
    public static List<string> PackModels(string path)
    {
        try
        {
            return ReadManifests(path).Keys.OrderBy(n => n, StringComparer.Ordinal).ToList();
        }
        catch (Exception) { return new List<string>(); }
    }

    public sealed record Removal(long FreedSize, int FreedBlobs, long KeptSize, int KeptBlobs, List<string> NeededBy);

    /// <summary>What removing these models (or files of the one model) frees and keeps: remove --dry-run.</summary>
    public async Task<Removal> PreviewRemovalAsync(string pack, IEnumerable<string> models, IEnumerable<string> files, CancellationToken ct = default)
    {
        var argv = new List<string> { "remove", pack };
        argv.AddRange(models);
        argv.AddRange(files.Select(f => "--file=" + f));
        argv.Add("--dry-run");
        var (code, events) = await CliProcess.RunAsync(Python, Tool, argv, ct);
        var freed = events.LastOrDefault(e => e.Event == "freed");
        if (freed is null)
            throw new ToolFailed(LastError(events, code));
        var kept = events.LastOrDefault(e => e.Event == "kept");
        return new Removal(freed.Long("size") ?? 0, (int)(freed.Long("blobs") ?? 0), kept?.Long("size") ?? 0,
            (int)(kept?.Long("blobs") ?? 0), kept?.Strings("needed_by") ?? new List<string>());
    }

    /// <summary>The tool's version line (--version), or the problem.</summary>
    public async Task<(bool ok, string text)> VersionAsync(CancellationToken ct = default)
    {
        try
        {
            var (code, events) = await CliProcess.RunAsync(Python, Tool, new[] { "--version" }, ct, rawArgs: true);
            var text = string.Join(" ", events.Select(e => e.Text).Where(t => t.Trim().Length > 0)).Trim();
            return code == 0 && text.Length > 0 ? (true, text) : (false, text.Length > 0 ? text : $"exit code {code}");
        }
        catch (Exception e)
        {
            return (false, e.Message);
        }
    }

    // ------------------------------------------------------------------------------------------ manifests

    public sealed record ManifestFile(string Path, long Size, string Sha256);
    public sealed record Manifest(string Name, List<ManifestFile> Files);

    /// <summary>The manifests of a pack folder or pack file, by model name: models/*.json read as plain JSON (name
    /// and files only). Bad ones are left out, as the tool leaves them out of its listing.</summary>
    public static Dictionary<string, Manifest> ReadManifests(string path)
    {
        var result = new Dictionary<string, Manifest>();
        if (Place.IsOczip(path))
        {
            using var zip = ZipFile.OpenRead(path);
            foreach (var entry in zip.Entries)
            {
                if (!entry.FullName.StartsWith("models/") || !entry.FullName.EndsWith(".json") || entry.FullName.Count(c => c == '/') != 1)
                    continue;
                using var s = entry.Open();
                using var r = new StreamReader(s);
                Add(result, r.ReadToEnd());
            }
        }
        else
        {
            var dir = Path.Combine(path, "models");
            if (!Directory.Exists(dir))
                return result;
            foreach (var file in Directory.EnumerateFiles(dir, "*.json"))
                Add(result, File.ReadAllText(file));
        }
        return result;
    }

    private static void Add(Dictionary<string, Manifest> result, string json)
    {
        try
        {
            if (JsonNode.Parse(json) is not JsonObject o || o["name"] is null || o["files"] is not JsonArray files)
                return;
            var m = new Manifest(o["name"]!.ToString(), new List<ManifestFile>());
            foreach (var f in files.OfType<JsonObject>())
                m.Files.Add(new ManifestFile(f["path"]?.ToString() ?? "", Json.Long(f["size"]) ?? 0, f["sha256"]?.ToString() ?? ""));
            result[m.Name] = m;
        }
        catch (Exception) { }
    }

    /// <summary>The size each blob takes in the pack, by SHA-256: file sizes in blobs/ab/&lt;sha&gt;.ocz, or the members
    /// of a pack file (stored as they are).</summary>
    public static Dictionary<string, long> BlobSizes(string path)
    {
        var sizes = new Dictionary<string, long>();
        try
        {
            if (Place.IsOczip(path))
            {
                using var zip = ZipFile.OpenRead(path);
                foreach (var entry in zip.Entries)
                {
                    var parts = entry.FullName.Split('/');
                    if (parts.Length == 3 && parts[0] == "blobs" && parts[2].EndsWith(".ocz"))
                        sizes[parts[2][..^4]] = entry.Length;
                }
            }
            else
            {
                var dir = Path.Combine(path, "blobs");
                if (Directory.Exists(dir))
                    foreach (var file in Directory.EnumerateFiles(dir, "*.ocz", SearchOption.AllDirectories))
                        sizes[Path.GetFileNameWithoutExtension(file)] = new FileInfo(file).Length;
            }
        }
        catch (Exception) { }
        return sizes;
    }
}
