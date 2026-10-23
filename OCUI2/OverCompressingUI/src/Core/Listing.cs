using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;

namespace OverCompressingUI.Core;

/// <summary>One row of the list: a drive, folder or file, a model of a pack, or a file of a model.</summary>
public sealed class Row
{
    public string Id = "";
    public string Name = "";
    public string Kind = "";  // drive, folder, pack, oczip, model, weights, file
    public long? Size;
    public long? Stored;
    public string Ratio = "";
    public string Type = "";
    public string Needs = "";
    public string NeedsByBase = "";
    public DateTime? Modified;
    public DateTime? Packed;
    public int? Files;
    public string? Parent;  // by base: the model this one sits under
    public bool Damaged;
    public ModelSummary? Summary;  // a model row
    public FileSummary? File;  // a file row
    public Dictionary<string, long> Blobs = new();  // a model row: sha -> stored, to sum a selection counting each once

    public bool IsContainer => Kind is "folder" or "drive" or "pack" or "oczip" or "model";
    public string BaseChoice => File?.BaseChoice ?? Summary?.BaseChoice ?? "auto";

    public string Cell(string column, bool byBase) => column switch
    {
        "size" => T.SizeText(Size),
        "stored" => T.SizeText(Stored),
        "ratio" => Ratio,
        "type" => Type,
        "needs" => byBase ? NeedsByBase : Needs,
        "files" => Files?.ToString() ?? "",
        "modified" => Modified?.ToString("yyyy-MM-dd HH:mm") ?? "",
        "packed" => Packed?.ToString("yyyy-MM-dd HH:mm") ?? "",
        _ => Name,
    };
}

public sealed record Listing(List<Row> Rows, string? Where);

/// <summary>What a place holds, as rows: a folder from disk, a pack and a model through the tool.</summary>
public static class Lister
{
    public static readonly Dictionary<string, string[]> Shown = new()
    {
        ["folder"] = new[] { "size", "type", "modified" },
        ["pack"] = new[] { "packed", "files", "size", "stored", "ratio", "needs" },
        ["model"] = new[] { "size", "stored", "ratio", "type", "needs" },
    };

    public static async Task<Listing> ListAsync(Place place, PackReader reader)
    {
        return place.View switch
        {
            "folder" => await Task.Run(() => ListFolder(place)),
            "pack" => await ListPackAsync(place, reader),
            _ => await ListModelAsync(place, reader),
        };
    }

    /// <summary>The rows of a folder on disk (or of the drives).</summary>
    public static Listing ListFolder(Place place)
    {
        var rows = new List<Row>();
        if (place.Path is null)
        {
            foreach (var drive in DriveInfo.GetDrives())
            {
                var name = drive.Name;
                string type = T.Get("type_drive");
                try
                {
                    if (drive.IsReady && drive.VolumeLabel.Length > 0)
                        type = $"{T.Get("type_drive")} · {drive.VolumeLabel}";
                }
                catch (Exception) { }
                rows.Add(new Row { Id = name, Name = name, Kind = "drive", Type = type });
            }
            return new Listing(rows, null);
        }
        var dir = new DirectoryInfo(place.Path);
        foreach (var e in dir.EnumerateFileSystemInfos())
        {
            try
            {
                bool folder = (e.Attributes & FileAttributes.Directory) != 0;
                var kind = folder ? (File.Exists(Path.Combine(e.FullName, Place.Marker)) ? "pack" : "folder") : Place.KindOf(e.Name);
                rows.Add(new Row
                {
                    Id = e.Name, Name = e.Name, Kind = kind, Size = folder ? null : ((FileInfo)e).Length,
                    Modified = e.LastWriteTime, Type = T.Get("type_" + kind),
                });
            }
            catch (Exception) { }
        }
        return new Listing(rows, null);
    }

    public static string NeedsText(IEnumerable<string> needs, IEnumerable<string> removed, string? baseName = null, Dictionary<string, double>? usage = null)
    {
        usage ??= new Dictionary<string, double>();
        var shown = needs.Where(n => n != baseName).Select(n => usage.TryGetValue(n, out var u) ? $"{n} ({u * 100:F0}%)" : n).ToList();
        shown.AddRange(removed.Select(n => T.Get("removed", ("name", n))));
        return string.Join(", ", shown);
    }

    public static DateTime? PackedTime(string? created)
    {
        if (created is null)
            return null;
        return DateTime.TryParse(created, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var t)
            ? t.ToLocalTime() : null;
    }

    /// <summary>The rows of a pack: one per model (Parent: the model it sits under by base), and what to say about it.</summary>
    public static async Task<Listing> ListPackAsync(Place place, PackReader reader)
    {
        var summary = await reader.ReadPackAsync(place.Path!);
        // each model's blobs come with the listing (list --json "blobs"); an older tool leaves them out, then the
        // manifests are read here instead
        var (manifests, blobSizes) = summary.Models.Any(m => m.Blobs.Count > 0)
            ? (new Dictionary<string, PackReader.Manifest>(), new Dictionary<string, long>())
            : await Task.Run(() =>
            {
                try { return (PackReader.ReadManifests(place.Path!), PackReader.BlobSizes(place.Path!)); }
                catch (Exception) { return (new Dictionary<string, PackReader.Manifest>(), new Dictionary<string, long>()); }
            });
        var names = summary.Models.Select(m => m.Name).ToHashSet();
        var rows = new List<Row>();
        foreach (var m in summary.Models)
        {
            var baseName = m.Base is not null && names.Contains(m.Base) ? m.Base : null;
            var row = new Row
            {
                Id = m.Name, Name = m.Name, Kind = "model", Files = m.FileCount, Size = m.Size, Stored = m.Stored,
                Ratio = T.Percent(m.Stored, m.Size), Parent = baseName, Packed = PackedTime(m.Created),
                Needs = NeedsText(m.Needs, m.NeedsRemoved, usage: m.Usage),
                NeedsByBase = NeedsText(m.Needs, m.NeedsRemoved, baseName, m.Usage), Summary = m,
            };
            if (m.Blobs.Count > 0)
                row.Blobs = new Dictionary<string, long>(m.Blobs);
            else if (manifests.TryGetValue(m.Name, out var manifest))
                foreach (var f in manifest.Files)
                    row.Blobs[f.Sha256] = blobSizes.TryGetValue(f.Sha256, out var s) ? s : 0;
            rows.Add(row);
        }
        long size = summary.Models.Sum(m => m.Size);
        var where = T.Get("where_pack", ("kind", summary.Kind), ("models", T.Count(rows.Count, "model")), ("size", T.SizeText(size)),
            ("stored", T.SizeText(summary.Stored)), ("ratio", T.Percent(summary.Stored, size)), ("blobs", T.Count(summary.Blobs, "blob")));
        if (summary.Kept.Count > 0)
        {
            var users = summary.Kept.SelectMany(k => k.NeededBy).Distinct().OrderBy(n => n, StringComparer.Ordinal);
            where += T.Get("where_kept", ("size", T.SizeText(summary.Kept.Sum(k => k.Size))), ("models", string.Join(", ", users)));
        }
        if (summary.UnusedBlobs > 0)
            where += T.Get("where_unused", ("size", T.SizeText(summary.UnusedSize)));
        if (summary.Problems.Count > 0)
            where = T.Get("bad_manifests", ("count", T.Count(summary.Problems.Count, "manifest")),
                ("first", $"{summary.Problems[0].path}: {summary.Problems[0].problem}")) + " · " + where;
        return new Listing(rows, where);
    }

    /// <summary>The rows of one model of a pack: one per file, with how it is stored and what it is coded against.</summary>
    public static async Task<Listing> ListModelAsync(Place place, PackReader reader)
    {
        var m = await reader.ReadModelAsync(place.Path!, place.Model!);
        var rows = new List<Row>();
        foreach (var f in m.Files)
        {
            string Label(BaseRef b) => b.Model ?? b.Sha256[..Math.Min(12, b.Sha256.Length)];
            var bases = f.Bases.Where(b => !b.Removed).Select(Label).Distinct().OrderBy(s => s, StringComparer.Ordinal).ToList();
            var gone = f.Bases.Where(b => b.Removed).Select(Label).Distinct().OrderBy(s => s, StringComparer.Ordinal).ToList();
            var used = new Dictionary<string, long>();
            var sizes = new Dictionary<string, long>();
            foreach (var b in f.Bases.Where(b => !b.Removed && b.Model is not null))
            {
                used[b.Model!] = used.GetValueOrDefault(b.Model!) + b.Used;
                sizes[b.Model!] = sizes.GetValueOrDefault(b.Model!) + b.Size;
            }
            var usage = used.Where(kv => sizes[kv.Key] > 0).ToDictionary(kv => kv.Key, kv => (double)kv.Value / sizes[kv.Key]);
            var needs = f.Bases.Count > 0 ? T.Get("against", ("base", NeedsText(bases, gone, usage: usage))) : "";
            if (f.SharedWith.Count > 0)
                needs += (needs.Length > 0 ? "  " : "") + T.Get("shared", ("models", string.Join(", ", f.SharedWith)));
            rows.Add(new Row
            {
                Id = f.Path, Name = f.Path, Kind = Place.KindOf(f.Path), Size = f.Size, Stored = f.Stored,
                Ratio = T.Percent(f.Stored, f.Size), Type = f.Problem ?? f.Method ?? "", Needs = needs, NeedsByBase = needs, File = f,
            });
        }
        var where = T.Get("where_model", ("name", m.Name), ("files", T.Count(m.Files.Count, "file")), ("size", T.SizeText(m.Size)),
            ("stored", T.SizeText(m.Stored)), ("ratio", T.Percent(m.Stored, m.Size)));
        var shared = m.Files.Where(f => f.SharedWith.Count > 0 && f.Stored is not null).GroupBy(f => f.Sha256).Sum(g => g.First().Stored!.Value);
        if (shared > 0)
            where += T.Get("where_shared", ("size", T.SizeText(shared)));
        if (m.Needs.Count > 0 || m.NeedsRemoved.Count > 0)
            where += T.Get("where_needs", ("names", NeedsText(m.Needs, m.NeedsRemoved)));
        return new Listing(rows, where);
    }

    /// <summary>Bytes of a file, or of every file below a folder (links not followed).</summary>
    public static long TreeSize(string path)
    {
        try
        {
            var info = new FileInfo(path);
            if ((info.Attributes & FileAttributes.Directory) == 0 || (info.Attributes & FileAttributes.ReparsePoint) != 0)
                return info.Length;
            long total = 0;
            var stack = new Stack<DirectoryInfo>();
            stack.Push(new DirectoryInfo(path));
            while (stack.Count > 0)
            {
                var d = stack.Pop();
                foreach (var e in d.EnumerateFileSystemInfos())
                {
                    if ((e.Attributes & FileAttributes.ReparsePoint) != 0)
                        continue;
                    if ((e.Attributes & FileAttributes.Directory) != 0)
                        stack.Push((DirectoryInfo)e);
                    else
                        total += ((FileInfo)e).Length;
                }
            }
            return total;
        }
        catch (Exception) { return 0; }
    }

    /// <summary>The files a pack of `source` would take: every regular file below it (the tool's own rules skip .git,
    /// .cache, __MACOSX, Thumbs.db, desktop.ini, .DS_Store), or just those named.</summary>
    public static (int count, long size) SourceFiles(string source, IEnumerable<string> files)
    {
        var listed = files.ToList();
        if (listed.Count > 0)
        {
            long total = 0;
            foreach (var f in listed)
                try { total += new FileInfo(Path.Combine(source, f)).Length; } catch (Exception) { }
            return (listed.Count, total);
        }
        var skipDirs = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { ".git", ".cache", "__MACOSX" };
        var skipFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { ".hf_files.json", "thumbs.db", "desktop.ini", ".ds_store" };
        int count = 0;
        long size = 0;
        var stack = new Stack<string>();
        stack.Push(source);
        while (stack.Count > 0)
        {
            var dir = stack.Pop();
            IEnumerable<FileSystemInfo> entries;
            try { entries = new DirectoryInfo(dir).EnumerateFileSystemInfos(); } catch (Exception) { continue; }
            foreach (var e in entries)
            {
                if ((e.Attributes & FileAttributes.Directory) != 0)
                {
                    if (!skipDirs.Contains(e.Name) && (e.Attributes & FileAttributes.ReparsePoint) == 0)
                        stack.Push(e.FullName);
                }
                else if (!skipFiles.Contains(e.Name))
                {
                    count++;
                    size += ((FileInfo)e).Length;
                }
            }
        }
        return (count, size);
    }
}
