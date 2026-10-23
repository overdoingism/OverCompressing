using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace OverCompressingUI.Core;

/// <summary>One line of the tool's --json output: {"event", "text", ...fields}. A line that is not JSON becomes a
/// "note" with the line as its text (the tool prints tracebacks and usage errors that way).</summary>
public sealed class ToolEvent
{
    public string Event { get; init; } = "note";
    public string Text { get; init; } = "";
    public JsonObject? Fields { get; init; }
    public bool FromStderr { get; init; }

    public static ToolEvent Parse(string line, bool stderr)
    {
        var trimmed = line.TrimEnd('\r', '\n');
        if (trimmed.StartsWith('{'))
        {
            try
            {
                if (JsonNode.Parse(trimmed) is JsonObject o && o["event"] is JsonValue ev)
                    return new ToolEvent { Event = ev.ToString(), Text = o["text"]?.ToString() ?? "", Fields = o, FromStderr = stderr };
            }
            catch (JsonException) { }
        }
        return new ToolEvent { Event = "note", Text = trimmed, FromStderr = stderr };
    }

    public long? Long(string name) => Fields?[name] is JsonValue v && v.TryGetValue<long>(out var n) ? n :
        Fields?[name] is JsonValue d && d.TryGetValue<double>(out var f) ? (long)f : null;

    public string? Str(string name) => Fields?[name] is JsonValue v ? v.ToString() : null;

    public List<string> Strings(string name) => Fields?[name] is JsonArray a ? a.Select(x => x?.ToString() ?? "").ToList() : new List<string>();
}

/// <summary>A base a file is coded against (model_summary "bases").</summary>
public sealed record BaseRef(string? Model, string? Path, string Sha256, bool Removed, long Size, long Used);

/// <summary>One file of a model, as list shows it (model_summary "files").</summary>
public sealed class FileSummary
{
    public string Path = "";
    public long Size;
    public string Sha256 = "";
    public string BaseChoice = "auto";
    public long? Stored;
    public string? Method;
    public string? Problem;
    public List<BaseRef> Bases = new();
    public List<string> SharedWith = new();
}

/// <summary>What list shows of one model (model_summary); in a pack listing, Files is the count only.</summary>
public sealed class ModelSummary
{
    public string Name = "";
    public string? Revision;
    public string? Created;
    public string BaseChoice = "auto";
    public List<FileSummary> Files = new();
    public int FileCount;
    public long Size;
    public long? Stored;
    public List<string> Needs = new();
    public List<string> NeedsRemoved = new();
    public string? Base;
    public Dictionary<string, double> Usage = new();
    public List<string> NeededBy = new();
    public Dictionary<string, long> Blobs = new();  // sha256 -> stored size (list --json, since upstream 88f7f13)

    public static ModelSummary From(JsonObject o)
    {
        var m = new ModelSummary
        {
            Name = o["name"]?.ToString() ?? "",
            Revision = o["revision"]?.ToString(),
            Created = o["created"]?.ToString(),
            BaseChoice = o["base_choice"]?.ToString() ?? "auto",
            Size = Json.Long(o["size"]) ?? 0,
            Stored = Json.Long(o["stored"]),
            Needs = Json.Strings(o["needs"]),
            NeedsRemoved = Json.Strings(o["needs_removed"]),
            Base = o["base"]?.ToString(),
            NeededBy = Json.Strings(o["needed_by"]),
        };
        if (o["usage"] is JsonObject usage)
            foreach (var (k, v) in usage)
                m.Usage[k] = Json.Double(v) ?? 0;
        if (o["blobs"] is JsonObject blobs)
            foreach (var (k, v) in blobs)
                m.Blobs[k] = Json.Long(v) ?? 0;
        if (o["files"] is JsonArray files)
        {
            m.FileCount = files.Count;
            foreach (var f in files.OfType<JsonObject>())
            {
                var fs = new FileSummary
                {
                    Path = f["path"]?.ToString() ?? "",
                    Size = Json.Long(f["size"]) ?? 0,
                    Sha256 = f["sha256"]?.ToString() ?? "",
                    BaseChoice = f["base_choice"]?.ToString() ?? "auto",
                    Stored = Json.Long(f["stored"]),
                    Method = f["method"]?.ToString(),
                    Problem = f["problem"]?.ToString(),
                    SharedWith = Json.Strings(f["shared_with"]),
                };
                if (f["bases"] is JsonArray bases)
                    foreach (var b in bases.OfType<JsonObject>())
                        fs.Bases.Add(new BaseRef(b["model"]?.ToString(), b["path"]?.ToString(), b["sha256"]?.ToString() ?? "",
                            b["removed"]?.GetValue<bool>() ?? false, Json.Long(b["size"]) ?? 0, Json.Long(b["used"]) ?? 0));
                m.Files.Add(fs);
            }
        }
        else
            m.FileCount = (int)(Json.Long(o["files"]) ?? 0);
        return m;
    }
}

public sealed record KeptBlob(string Sha256, long Size, string? Model, string? Path, List<string> NeededBy);

/// <summary>What list shows of a pack (pack_summary).</summary>
public sealed class PackSummary
{
    public string Pack = "";
    public string Kind = "";
    public List<ModelSummary> Models = new();
    public List<KeptBlob> Kept = new();
    public int UnusedBlobs;
    public long UnusedSize;
    public int Blobs;
    public long Stored;
    public List<string> Unreadable = new();
    public List<(string path, string problem)> Problems = new();  // manifests that cannot be used ("fail" events)

    public static PackSummary From(JsonObject o)
    {
        var s = new PackSummary
        {
            Pack = o["pack"]?.ToString() ?? "",
            Kind = o["kind"]?.ToString() ?? "",
            Blobs = (int)(Json.Long(o["blobs"]) ?? 0),
            Stored = Json.Long(o["stored"]) ?? 0,
            Unreadable = Json.Strings(o["unreadable"]),
        };
        if (o["models"] is JsonArray models)
            s.Models = models.OfType<JsonObject>().Select(ModelSummary.From).ToList();
        if (o["kept"] is JsonArray kept)
            foreach (var k in kept.OfType<JsonObject>())
                s.Kept.Add(new KeptBlob(k["sha256"]?.ToString() ?? "", Json.Long(k["size"]) ?? 0, k["model"]?.ToString(),
                    k["path"]?.ToString(), Json.Strings(k["needed_by"])));
        if (o["unused"] is JsonObject unused)
        {
            s.UnusedBlobs = (int)(Json.Long(unused["blobs"]) ?? 0);
            s.UnusedSize = Json.Long(unused["size"]) ?? 0;
        }
        return s;
    }
}

/// <summary>A verify's damage report (the "damage" event): the text for people, and the data the window uses.</summary>
public sealed class DamageReport
{
    public string Text = "";
    public string Pack = "";
    public List<(string model, string path, bool own, long size, bool weights)> Lost = new();
    public List<(string name, int files, int lost)> Models = new();
    public List<string> Affected = new();

    public static DamageReport From(ToolEvent e)
    {
        var o = e.Fields!;
        var d = new DamageReport { Text = e.Text, Pack = o["pack"]?.ToString() ?? "", Affected = Json.Strings(o["affected"]) };
        if (o["lost"] is JsonArray lost)
            foreach (var item in lost.OfType<JsonObject>())
                d.Lost.Add((item["model"]?.ToString() ?? "", item["path"]?.ToString() ?? "", item["own"]?.GetValue<bool>() ?? false,
                    Json.Long(item["size"]) ?? 0, item["weights"]?.GetValue<bool>() ?? false));
        if (o["models"] is JsonArray models)
            foreach (var m in models.OfType<JsonObject>())
                d.Models.Add((m["name"]?.ToString() ?? "", (int)(Json.Long(m["files"]) ?? 0), (int)(Json.Long(m["lost"]) ?? 0)));
        return d;
    }
}

/// <summary>What the last verify that failed found lost, kept for the ⚠ marks and for Clean up while the pack's stamp
/// is as it was then.</summary>
public sealed class DamageMarks
{
    public string Pack = "";  // normalized full path
    public (long mtime, long size)? Stamp;
    public Dictionary<(string model, string path), bool> Lost = new();  // -> own blob at fault
    public Dictionary<string, int> Models = new();  // model -> lost files
}

internal static class Json
{
    public static long? Long(JsonNode? n)
    {
        if (n is not JsonValue v) return null;
        if (v.TryGetValue<long>(out var l)) return l;
        if (v.TryGetValue<double>(out var d)) return (long)d;
        return null;
    }

    public static double? Double(JsonNode? n)
    {
        if (n is not JsonValue v) return null;
        if (v.TryGetValue<double>(out var d)) return d;
        if (v.TryGetValue<long>(out var l)) return l;
        return null;
    }

    public static List<string> Strings(JsonNode? n) => n is JsonArray a ? a.Select(x => x?.ToString() ?? "").ToList() : new List<string>();
}
