using System;
using System.Collections.Generic;
using System.Linq;

namespace OverCompressingUI.Core;

/// <summary>
/// All of the window's text, ported from llm_archive_gui.py (TEXT): Unicode symbols where they say it (BMP only),
/// else English. Keys are the original ones so the two windows can be compared line by line; a few are new to this
/// window (graph view, settings) and are marked so.
/// </summary>
public static class T
{
    public const string GuiVersion = "0.5.5";

    private static readonly Dictionary<string, string> Texts = new()
    {
        ["up"] = "↑",
        ["drives"] = "Drives",
        ["pack_to"] = "+ Pack to",
        ["pack_in"] = "+ Pack in",
        ["add_file"] = "+ Add file",
        ["save"] = "⤓ Save .oczip",
        ["extract"] = "⤒ Extract to",
        ["verify"] = "✔ Verify",
        ["remove"] = "− Remove",
        ["rename"] = "✎ Rename",
        ["refresh"] = "↻ Refresh",
        ["hint_keys"] = "F5 Force refresh · Ctrl+A Select all",
        ["mode_in_file"] = "in pack file",
        ["cancel"] = "✕ Cancel",
        ["stop_now"] = "✕ Stop now",
        ["ok"] = "OK",
        ["browse"] = "Browse",
        ["col_name"] = "Name",
        ["col_files"] = "Files",
        ["col_size"] = "Size",
        ["col_stored"] = "Stored",
        ["col_ratio"] = "Ratio",
        ["col_type"] = "Type",
        ["col_needs"] = "Needs",
        ["col_modified"] = "Modified",
        ["col_packed"] = "Packed",
        ["filter"] = "⌕ Filter:",
        ["filter_clear"] = "✕",
        ["view_list"] = "☰ List",
        ["view_base"] = "⑂ By base",
        ["view_graph"] = "⟡ Graph",  // new: the models as a graph of what needs what
        ["filtered"] = "{shown} of {total} shown",
        ["type_folder"] = "Folder",
        ["type_drive"] = "Drive",
        ["type_file"] = "File",
        ["type_weights"] = "Weights",
        ["type_pack"] = "Pack folder",
        ["type_oczip"] = "Pack file",
        ["against"] = "Δ {base}",
        ["removed"] = "{name} (removed)",
        ["shared"] = "= also in {models}",
        ["where_pack"] = "{kind} · {models} · {size} → {stored} ({ratio}) · {blobs}",
        ["selected"] = "{count} selected: {size} → {stored} ({ratio})",
        ["selected_size"] = "{count} selected: {size}",
        ["selected_count"] = "{count} selected",
        ["listed"] = "   ({count} listed in files)",
        ["where_kept"] = " · {size} kept as bases of {models}",
        ["where_unused"] = " · {size} no model needs",
        ["where_model"] = "{name} · {files} · {size} → {stored} ({ratio})",
        ["where_needs"] = " · needs {names}",
        ["where_shared"] = " · {size} of it also in other models",
        ["read_only"] = "read-only",
        ["cannot_list"] = "⚠ Cannot list it: {problem}",
        ["bad_manifests"] = "⚠ {count} cannot be used: {first}",
        ["no_place"] = "{text} is not a folder or a pack file (.oczip).",
        ["nothing_to_extract"] = "Select models or files to extract first.",
        ["nothing_to_verify"] = "Select models or files to verify first.",
        ["pack_what"] = "Select one folder, or files in this folder, to pack.",
        ["pack_here"] = "Go to a folder, or into a pack folder, to pack.",
        ["pick_source"] = "Choose a folder to pack",
        ["pick_dest"] = "Extract into",
        ["extract_title"] = "⤒ Extract to",
        ["dest_prompt"] = "Extract into:",
        ["layout"] = "Folder layout:",
        ["path_full"] = "Full path",
        ["path_last"] = "Last level",
        ["path_files"] = "Files only",
        ["files_off"] = "Files only: one model at a time.",
        ["last_off"] = "Last level: two of these models end in {name}.",
        ["need_dest"] = "Choose where to extract.",
        ["whole_model"] = "{name}",
        ["some_files"] = "{files} of {name}",
        ["pick_archive"] = "Choose a pack folder (an empty folder starts a new one)",
        ["name_title"] = "+ Pack",
        ["name_what"] = "Pack {files} ({size}) from:",
        ["name_prompt"] = "Model name (e.g. Qwen/Qwen2.5-7B):",
        ["into"] = "Pack into:",
        ["into_folder"] = "A pack folder",
        ["into_file"] = "A new pack file (.oczip)",
        ["into_here"] = "This pack folder",
        ["into_file_here"] = "A new pack file (.oczip)",
        ["file_hint_here"] = "To keep the Δ deltas in one file: pack into this pack folder, then ⤓ Save .oczip.",
        ["pick_file"] = "New pack file",
        ["file_exists"] = "That file already exists. Choose a new name: a pack file is never added to.",
        ["file_in_source"] = "The pack file cannot go inside the folder being packed.",
        ["need_target"] = "Choose where to pack.",
        ["target_oczip"] = "That is a pack file: choose “A new pack file (.oczip)” to make one.",
        ["name_hint"] = "Δ bases are chosen automatically. Other options: llm_archive.py (docs/usage.md).",
        ["replace_q"] = "{name} is already in this pack folder ({files}).\n\nReplace it with these files?",
        ["export_title"] = "⤓ Save .oczip",
        ["export_all"] = "every model",
        ["export_verify"] = "✔ Verify the new file",
        ["export_delete"] = "Then remove them from the pack folder (verifies first)",
        ["export_hint"] = "A pack file is read-only. To change it, unzip it into a pack folder.",
        ["export_from_file"] = "A pack file is read-only: nothing is removed from it.",
        ["export_name"] = "The name must end in .oczip.",
        ["export_replace_q"] = "{path} already exists.\n\nReplace it?",
        ["export_delete_q"] = "Once {path} is written{checked}, {what} will be removed from {pack}.\n\nGo on?",
        ["checked"] = " and verified",
        ["unchecked"] = " (not verified)",
        ["remove_title"] = "− Remove",
        ["trash_what"] = "Select files or folders to move to the Recycle Bin.",
        ["trash_inside"] = "This is inside the pack folder {pack}.\n\nRemove models in the pack's own view.",
        ["trash_q"] = "{what} ({size}){packs}\n\nMove to the Recycle Bin?",
        ["trash_packs"] = "\n{packs} among them: their models go too.",
        ["trash_failed"] = "Not moved to the Recycle Bin: {problem}",
        ["remove_q"] = "This removes {what} from {pack} and frees {freed}.{kept}\n\nRemove?",
        ["remove_kept"] = "\n\n{kept} stays as a base of {models}: ⇄ Rebase lets it go when that makes the pack smaller.",
        ["remove_what"] = "Select models, or files of a model, to remove.",
        ["rename_title"] = "✎ Rename",
        ["rename_prompt"] = "New name for {name}:",
        ["rename_what"] = "Select one model to rename.",
        ["rename_file_prompt"] = "New path for {path} in {model}:",
        ["rename_file_what"] = "Select one file to rename.",
        ["rename_file_taken"] = "{model} already has a file {path}.",
        ["add_title"] = "+ Add file",
        ["add_pick"] = "Files to add to {model}",
        ["add_q"] = "{model} already has {files} of these names:\n{names}\n\nReplace them?",
        ["adding"] = "▶ Adding to {name}",
        ["done_add"] = "✔ Added {files} to {name}.",
        ["read_only_action"] = "A pack file is read-only. To change it, unzip it into a pack folder.",
        ["packing"] = "▶ Packing {name}",
        ["exporting"] = "▶ Exporting into {name}",
        ["extracting"] = "▶ Extracting {name}",
        ["verifying"] = "▶ Verifying {what}",
        ["removing"] = "▶ Removing {what}",
        ["renaming"] = "▶ Renaming {name}",
        ["everything"] = "everything in {pack}",
        ["cancelling"] = "■ Cancelling after the current step…",
        ["force_q"] = "Still finishing the current step. Stopping now may leave .oc-*.part files behind (they can be deleted).\n\nStop now?",
        ["quit_q"] = "Something is still running.\n\nCancel it and quit?",
        ["done_pack"] = "✔ Packed {name}.",
        ["done_export"] = "✔ Saved {path}.",
        ["done_export_deleted"] = "✔ Saved {path}, and removed the models from the pack folder.",
        ["done_extract_q"] = "✔ Extracted into {dest}.\n\nOpen the folder?",
        ["done_verify"] = "✔ Everything checked is intact.",
        ["done_remove"] = "✔ Removed {what}.",
        ["done_rename"] = "✔ Renamed {old} to {new}.",
        ["pack_kind"] = "pack",
        ["overwrite_q"] = "{dest} already has some of these files, with other contents.\n\nReplace them?",
        ["failed"] = "✕ Failed",
        ["cancelled"] = "✕ Cancelled.",
        ["into_folder_note"] = "For upkeep: each file stored once, Δ deltas where smaller",
        ["into_file_note"] = "For cold storage: read-only once written",
        ["into_here_note"] = "Beside its models: each file stored once, Δ deltas where smaller",
        ["into_file_here_note"] = "A separate, independent pack: no Δ deltas against these models",
        ["extract_one"] = "Extract {what}",
        ["extract_some"] = "Extract {count} models:",
        ["save_one"] = "Save {name} as a new pack file",
        ["save_all"] = "Save every model as a new pack file",
        ["save_some"] = "Save {count} models as a new pack file:",
        ["save_note"] = "Still compressed, with everything they need.",
        ["and_more"] = "and {count} more",
        ["see_log"] = "The log below has the whole output.",
        ["damage_title"] = "⚠ Verify failed",
        ["damage_heading"] = "{files} lost: what, why, and what to do",
        ["damage_heading_other"] = "Verify failed: what, why, and what to do",
        ["damage_note"] = "Copy the report to keep it, or to ask someone (or an AI) what to do. Its commands store a lost file again or give it up; verify again afterwards.",
        ["copy"] = "⧉ Copy",
        ["copied"] = "✔ Copied",
        ["clean_up"] = "− Clean up",
        ["close"] = "Close",
        ["clean_read_only"] = "A pack file is read-only: to clean it up, unzip it into a pack folder.",
        ["clean_title"] = "− Clean up",
        ["clean_heading"] = "Remove what verify found lost from {pack}",
        ["erase"] = "Only the lost files: {files} of {models}{emptied}.",
        ["erase_emptied"] = "; {names} {verb} left with none and go",
        ["erase_risk"] = "Models may be left incomplete: a safetensors model that lost a shard cannot be loaded.",
        ["erase_mod"] = "The models with lost files, whole: {names}.",
        ["erase_mod_risk"] = "Their intact files go too. What other models need of them stays as their base.",
        ["erase_full"] = "Those models, and the models that need data of them: {names}.",
        ["erase_full_same"] = "Those models (no other model needs data of them): {names}.",
        ["erase_full_same_risk"] = "Here the same as ERASE MOD.",
        ["erase_full_risk"] = "Models that are intact go too, only because they are coded against those: extract them first to keep them.",
        ["clean_prompt"] = "Type the level to run it:",
        ["clean_typed"] = "Type ERASE, ERASE MOD or ERASE FULL exactly.",
        ["clean_changed"] = "The pack has changed since it was verified. Verify it again first.",
        ["cleaning"] = "▶ Cleaning up {name}",
        ["done_clean"] = "✔ Removed what was lost. Verify the pack again to check it.",
        ["mark_model"] = "⚠ {files} failed verify",
        ["mark_file"] = "⚠ failed verify",
        ["mark_file_base"] = "⚠ failed verify (its base)",
        ["rebase"] = "⇄ Rebase",
        ["rebase_title"] = "⇄ Rebase",
        ["rebase_one"] = "Rebase {name}",
        ["rebase_some"] = "Rebase {count} models:",
        ["rebase_find"] = "Find better bases",
        ["rebase_find_note"] = "Each file is stored against the model it is smallest against; it changes only if the pack gets smaller. A base kept for removed models goes when that saves room.",
        ["rebase_alone"] = "Make independent",
        ["rebase_alone_note"] = "No bases: each file stored on its own, so it no longer needs another model. They may get larger.",
        ["rebase_also"] = "Also {names}, whose bases were set by hand",
        ["rebase_hint"] = "Every file that changes is restored and stored again: about as long as packing it.",
        ["rebasing"] = "▶ Rebasing {what}",
        ["done_rebase"] = "✔ Rebased {what}.\n\n{result}",
        ["hint_rebase"] = "\n\n{models} may store smaller against it: select them and ⇄ Rebase.",
        ["rebase_file_one"] = "Rebase {name} of {model}",
        ["rebase_file_some"] = "Rebase {count} files of {model}:",
        // new in this window
        ["settings"] = "⚙ Settings",
        ["settings_title"] = "⚙ Where OverCompressing is",
        ["settings_heading"] = "This window runs llm_archive.py in a Python environment with numpy and zstandard.",
        ["settings_python"] = "Python (python.exe of the .venv, or of a portable copy):",
        ["settings_tool"] = "The tool (tools/llm_archive.py):",
        ["settings_check"] = "✔ Check",
        ["settings_ok"] = "✔ {version}",
        ["settings_bad"] = "✕ {problem}",
        ["settings_note"] = "Looked for next to this program, in OVERCOMPRESSING_HOME, and in a pack's _kit (a portable copy kept there). The choice is kept.",
        ["settings_effects"] = "✦ Neon effects: glow, the live circuit board, the cursor trail, the frosted veil, the sweep",
        ["settings_effects_note"] = "Off, the window is plain and still: for a weak GPU or a remote desktop.",
        ["settings_sounds"] = "♪ Sounds",
        ["graph_hint"] = "Drag nodes · wheel zooms · drag the space to pan · double-click opens",
        ["graph_legend"] = "Arrow: feeds its bytes to (width: the share read)",
        ["log_title"] = "Output",
        ["legend"] = "◧ Colors",
        ["legend_note"] = "Rows of the list are tinted by what they are:",
        ["legend_selected"] = "Selected",
        ["watch_gone"] = "The place shown is gone: showing the nearest one still there.",
        ["open_folder"] = "Open folder",
    };

    /// <summary>The text of `key`, with `{name}` placeholders filled from the pairs given.</summary>
    public static string Get(string key, params (string name, object? value)[] values)
    {
        if (!Texts.TryGetValue(key, out var text))
            return key;
        foreach (var (name, value) in values)
            text = text.Replace("{" + name + "}", value?.ToString() ?? "");
        return text;
    }

    public static string Count(long n, string what) => $"{n} {what}" + (n == 1 ? "" : "s");

    /// <summary>`text` as a sentence: the tool's messages start in lower case, a dialog's with a capital.</summary>
    public static string Sentence(string? text)
    {
        var s = (text ?? "").Trim();
        return s.Length == 0 ? s : char.ToUpperInvariant(s[0]) + s[1..];
    }

    public static string SizeText(long? n)
    {
        if (n is null)
            return "";
        double v = n.Value;
        if (v < 1024)
            return $"{n} B";
        foreach (var unit in new[] { "KiB", "MiB", "GiB", "TiB" })
        {
            v /= 1024;
            if (v < 1024 || unit == "TiB")
                return $"{v:N1} {unit}";
        }
        return "";
    }

    public static string Percent(long? part, long? whole)
        => whole is > 0 && part is not null ? $"{(double)part.Value / whole.Value * 100:F1}%" : "";

    /// <summary>Names in a sentence: at most `most`, then how many more.</summary>
    public static string NamesText(IEnumerable<string> names, int most = 5)
    {
        var list = names.ToList();
        var shown = list.Count <= most + 1 ? list : list.Take(most).ToList();
        var text = string.Join(", ", shown);
        return shown.Count < list.Count ? text + " " + Get("and_more", ("count", list.Count - shown.Count)) : text;
    }

    /// <summary>Names one to a line: at most `most` of them, then how many more.</summary>
    public static string Lines(IEnumerable<string> names, int most = 5)
    {
        var list = names.ToList();
        var shown = list.Count <= most + 1 ? list : list.Take(most).ToList();
        if (shown.Count < list.Count)
            shown = shown.Append(Get("and_more", ("count", list.Count - shown.Count))).ToList();
        return string.Join("\n", shown);
    }

    /// <summary>`path` with folders from its middle left out ("…") until fits(text): its start and its end tell it.</summary>
    public static string Shorten(string path, Func<string, bool> fits)
    {
        if (fits(path))
            return path;
        var sep = path.Contains('\\') ? "\\" : "/";
        var parts = path.Split(sep);
        int keep = parts.Length > 3 ? 2 : 1;
        var head = parts.Take(keep).ToList();
        var tail = parts.Skip(keep).ToList();
        while (tail.Count > 1)
        {
            tail = tail.Skip(1).ToList();
            if (fits(string.Join(sep, head.Append("…").Concat(tail))))
                break;
        }
        return string.Join(sep, head.Append("…").Concat(tail));
    }

    /// <summary>A path for a message box: about 70 characters at most.</summary>
    public static string Short(string path) => Shorten(path, s => s.Length <= 70);

    /// <summary>A line cut to `most` characters, with "…" when it was longer.</summary>
    public static string Cut(string text, int most = 90) => text.Length <= most ? text : text[..(most - 1)] + "…";
}
