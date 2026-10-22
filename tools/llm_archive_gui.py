"""A small window for llm_archive.py, laid out like other archivers' windows. Folders and files are listed as they are
on disk; a pack (a pack folder, holding ocpack.json, or a pack file, .oczip) opens as the tree of its models, each
under the model most of its data is coded against, and a model opens as the list of its files. Select what to pack,
extract, verify, remove, rename or export: each action runs llm_archive.py in a process of its own, just as on the
command line (delta bases are chosen automatically); rebase, --base and the other options are there.

    python -I llm_archive_gui.py [<folder> | <pack folder> | <file.oczip>]

Run from an offline kit (<pack folder>/_kit/tools), it opens that pack. Its text is Unicode symbols where they say
it, else English: nothing depends on the system's language.
"""

import datetime
import json
import os
import queue
import shlex
import signal
import string
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.5"  # of this window (llm_archive.py, the command-line tool, has its own); raised by hand
sys.path.insert(0, HERE)
import llm_archive as la  # noqa: E402

# Runs llm_archive.py in the child process. When its stdin closes (Cancel, or this window going away), it stops as on
# Ctrl+C, so a pack or restore still removes what it had begun. On Windows the pipe is polled, not read: a read left
# pending on it would stall every process started meanwhile (the ans16 workers) as it starts. Elsewhere it is read
# from its file descriptor: Python's buffered stdin holds a lock while it waits, and workers forked meanwhile would
# wait on that lock forever when they close stdin (found by the downstream team on Linux).
RUNNER = """import os, sys, threading, time, _thread
if sys.platform != "win32":
    os.nice(10)  # gives way to what the user is doing, as on Windows (BELOW_NORMAL); its workers inherit it
sys.path.insert(0, sys.argv[1])
import llm_archive as la
def cancel_on_eof():
    if sys.platform == "win32":
        import msvcrt, _winapi
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        while True:
            try:
                _winapi.PeekNamedPipe(handle, 0)
            except OSError:  # the window closed its end
                break
            time.sleep(0.25)
    else:
        # A buffered read holds stdin's Python I/O lock across fork; a pool worker then blocks when
        # multiprocessing closes its inherited stdin. Read the pipe without that lock.
        while os.read(sys.stdin.fileno(), 4096):
            pass
    _thread.interrupt_main()
threading.Thread(target=cancel_on_eof, daemon=True).start()
sys.argv = ["llm_archive.py", *sys.argv[2:]]
try:
    la.main()
except KeyboardInterrupt:
    sys.exit("cancelled")
"""

WEIGHT_SUFFIXES = (".safetensors", ".gguf", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".h5", ".msgpack")
COLORS = {"folder": "#fff3c4", "drive": "#fff3c4", "pack": "#e6def8", "oczip": "#d9e8fb", "model": "#dcf1e2",
          "weights": "#fde0dc"}  # row backgrounds by kind; other files keep the list's own
LEGEND = (("folder", "Folder"), ("pack", "Pack folder"), ("oczip", "Pack file"), ("model", "Model"), ("weights", "Weights"))
SPINNER = "◐◓◑◒"
COLUMNS = {  # name: (heading key, width, anchor); the name itself is the tree column; width: a FITTED column's least
    "files": ("col_files", 50, "e"), "size": ("col_size", 90, "e"), "stored": ("col_stored", 90, "e"),
    "ratio": ("col_ratio", 64, "e"), "type": ("col_type", 150, "w"), "needs": ("col_needs", 220, "w"),
    "packed": ("col_packed", 130, "w"), "modified": ("col_modified", 130, "w"),
}
FITTED = ("type", "needs")  # as wide as what they hold, as the name is (fit_columns)
SHOWN = {"folder": ("size", "type", "modified"), "pack": ("packed", "files", "size", "stored", "ratio", "needs"),
         "model": ("size", "stored", "ratio", "type", "needs")}  # columns of each view
SORT_KEYS = {"modified": "mtime", "packed": "packed_ts"}  # columns sorted by another value than the one shown
BUTTONS = {  # the buttons (and right-click menu) of each view, in order; None: a separator
    "folder": ("pack_to", None, "refresh", "remove"),
    "pack": ("pack_in", "save", None, "extract", "verify", None, "rebase", None, "remove", "rename"),
    "model": ("add_file", None, "extract", "verify", None, "rebase", None, "remove", "rename"),
}
WATCH_SECONDS = 5
WRAP = 460  # pixels (at 100%): the width of a dialog's text
ARGS_LIMIT = 8000  # characters: past it, models and files go in list files (--models-from, --files-from); Windows: 32,767
BELOW = "\0below"  # a drag begun under the last row of the list  # the place is looked at (one file's time stamp) at most this often

TEXT = {  # all of the window's text: symbols (Unicode BMP, which every Tk renders) where they say it, else English
    "up": "↑",
    "drives": "Drives",
    "pack_to": "+ Pack to",
    "pack_in": "+ Pack in",
    "add_file": "+ Add file",
    "save": "⤓ Save .oczip",
    "extract": "⤒ Extract to",
    "verify": "✔ Verify",
    "remove": "− Remove",
    "rename": "✎ Rename",
    "refresh": "↻ Refresh",
    "hint_keys": "F5 Force refresh · Ctrl+A Select all",
    "mode_in_file": "in pack file",
    "cancel": "✕ Cancel",
    "stop_now": "✕ Stop now",
    "ok": "OK",
    "browse": "Browse",
    "col_name": "Name",
    "col_files": "Files",
    "col_size": "Size",
    "col_stored": "Stored",
    "col_ratio": "Ratio",
    "col_type": "Type",
    "col_needs": "Needs",
    "col_modified": "Modified",
    "col_packed": "Packed",
    "filter": "⌕ Filter:",
    "filter_clear": "✕",
    "view_list": "☰ List",
    "view_base": "⑂ By base",
    "filtered": "{shown} of {total} shown",
    "type_folder": "Folder",
    "type_drive": "Drive",
    "type_file": "File",
    "type_weights": "Weights",
    "type_pack": "Pack folder",
    "type_oczip": "Pack file",
    "against": "Δ {base}",
    "removed": "{name} (removed)",
    "shared": "= also in {models}",
    "where_pack": "{kind} · {models} · {size} → {stored} ({ratio}) · {blobs}",
    "selected": "{count} selected: {size} → {stored} ({ratio})",
    "selected_size": "{count} selected: {size}",
    "selected_count": "{count} selected",
    "listed": "   ({count} listed in files)",
    "where_kept": " · {size} kept as bases of {models}",
    "where_unused": " · {size} no model needs",
    "where_model": "{name} · {files} · {size} → {stored} ({ratio})",
    "where_needs": " · needs {names}",
    "where_shared": " · {size} of it also in other models",
    "read_only": "read-only",
    "cannot_list": "⚠ Cannot list it: {problem}",
    "bad_manifests": "⚠ {count} cannot be used: {first}",
    "no_place": "{text} is not a folder or a pack file (.oczip).",
    "nothing_to_extract": "Select models or files to extract first.",
    "nothing_to_verify": "Select models or files to verify first.",
    "pack_what": "Select one folder, or files in this folder, to pack.",
    "pack_here": "Go to a folder, or into a pack folder, to pack.",
    "pick_source": "Choose a folder to pack",
    "pick_dest": "Extract into",
    "extract_title": "⤒ Extract to",
    "dest_prompt": "Extract into:",
    "layout": "Folder layout:",
    "path_full": "Full path",
    "path_last": "Last level",
    "path_files": "Files only",
    "files_off": "Files only: one model at a time.",
    "last_off": "Last level: two of these models end in {name}.",
    "need_dest": "Choose where to extract.",
    "whole_model": "{name}",
    "some_files": "{files} of {name}",
    "pick_archive": "Choose a pack folder (an empty folder starts a new one)",
    "name_title": "+ Pack",
    "name_what": "Pack {files} ({size}) from:",
    "name_prompt": "Model name (e.g. Qwen/Qwen2.5-7B):",
    "into": "Pack into:",
    "into_folder": "A pack folder",
    "into_file": "A new pack file (.oczip)",
    "into_here": "This pack folder",
    "into_file_here": "A new pack file (.oczip)",
    "file_hint_here": "To keep the Δ deltas in one file: pack into this pack folder, then ⤓ Save .oczip.",
    "pick_file": "New pack file",
    "file_exists": "That file already exists. Choose a new name: a pack file is never added to.",
    "file_in_source": "The pack file cannot go inside the folder being packed.",
    "need_target": "Choose where to pack.",
    "target_oczip": "That is a pack file: choose “A new pack file (.oczip)” to make one.",
    "name_hint": "Δ bases are chosen automatically. Other options: llm_archive.py (docs/usage.md).",
    "replace_q": "{name} is already in this pack folder ({files}).\n\nReplace it with these files?",
    "export_title": "⤓ Save .oczip",
    "export_all": "every model",
    "export_verify": "✔ Verify the new file",
    "export_delete": "Then remove them from the pack folder (verifies first)",
    "export_hint": "A pack file is read-only. To change it, unzip it into a pack folder.",
    "export_from_file": "A pack file is read-only: nothing is removed from it.",
    "export_name": "The name must end in .oczip.",
    "export_replace_q": "{path} already exists.\n\nReplace it?",
    "export_delete_q": "Once {path} is written{checked}, {what} will be removed from {pack}.\n\nGo on?",
    "checked": " and verified",
    "unchecked": " (not verified)",
    "remove_title": "− Remove",
    "trash_what": "Select files or folders to move to the Recycle Bin.",
    "trash_inside": "This is inside the pack folder {pack}.\n\nRemove models in the pack's own view.",
    "trash_q": "{what} ({size}){packs}\n\nMove to the Recycle Bin?",
    "trash_packs": "\n{packs} among them: their models go too.",
    "trash_failed": "Not moved to the Recycle Bin: {problem}",
    "remove_q": "This removes {what} from {pack} and frees {freed}.{kept}\n\nRemove?",
    "remove_kept": "\n\n{kept} stays as a base of {models}: ⇄ Rebase lets it go when that makes the pack smaller.",
    "remove_what": "Select models, or files of a model, to remove.",
    "rename_title": "✎ Rename",
    "rename_prompt": "New name for {name}:",
    "rename_what": "Select one model to rename.",
    "rename_file_prompt": "New path for {path} in {model}:",
    "rename_file_what": "Select one file to rename.",
    "rename_file_taken": "{model} already has a file {path}.",
    "add_title": "+ Add file",
    "add_pick": "Files to add to {model}",
    "add_q": "{model} already has {files} of these names:\n{names}\n\nReplace them?",
    "adding": "▶ Adding to {name}",
    "done_add": "✔ Added {files} to {name}.",
    "read_only_action": "A pack file is read-only. To change it, unzip it into a pack folder.",
    "packing": "▶ Packing {name}",
    "exporting": "▶ Exporting into {name}",
    "extracting": "▶ Extracting {name}",
    "verifying": "▶ Verifying {what}",
    "removing": "▶ Removing {what}",
    "renaming": "▶ Renaming {name}",
    "everything": "everything in {pack}",
    "cancelling": "■ Cancelling after the current step…",
    "force_q": "Still finishing the current step. Stopping now may leave .oc-*.part files behind (they can be "
               "deleted).\n\nStop now?",
    "quit_q": "Something is still running.\n\nCancel it and quit?",
    "done_pack": "✔ Packed {name}.",
    "done_export": "✔ Saved {path}.",
    "done_export_deleted": "✔ Saved {path}, and removed the models from the pack folder.",
    "done_extract_q": "✔ Extracted into {dest}.\n\nOpen the folder?",
    "done_verify": "✔ Everything checked is intact.",
    "done_remove": "✔ Removed {what}.",
    "done_rename": "✔ Renamed {old} to {new}.",
    "pack_kind": "pack",
    "overwrite_q": "{dest} already has some of these files, with other contents.\n\nReplace them?",
    "failed": "✕ Failed",
    "cancelled": "✕ Cancelled.",
    "into_folder_note": "For upkeep: each file stored once, Δ deltas where smaller",
    "into_file_note": "For cold storage: read-only once written",
    "into_here_note": "Beside its models: each file stored once, Δ deltas where smaller",
    "into_file_here_note": "A separate, independent pack: no Δ deltas against these models",
    "extract_one": "Extract {what}",
    "extract_some": "Extract {count} models:",
    "save_one": "Save {name} as a new pack file",
    "save_all": "Save every model as a new pack file",
    "save_some": "Save {count} models as a new pack file:",
    "save_note": "Still compressed, with everything they need.",
    "and_more": "and {count} more",
    "see_log": "The log below has the whole output.",
    "damage_title": "⚠ Verify failed",
    "damage_heading": "{files} lost: what, why, and what to do",
    "damage_heading_other": "Verify failed: what, why, and what to do",
    "damage_note": "Copy the report to keep it, or to ask someone (or an AI) what to do. Its commands store a lost file again or give it up; verify again afterwards.",
    "copy": "⧉ Copy",
    "copied": "✔ Copied",
    "clean_up": "− Clean up",
    "close": "Close",
    "clean_read_only": "A pack file is read-only: to clean it up, unzip it into a pack folder.",
    "clean_title": "− Clean up",
    "clean_heading": "Remove what verify found lost from {pack}",
    "erase": "Only the lost files: {files} of {models}{emptied}.",
    "erase_emptied": "; {names} {verb} left with none and go",
    "erase_risk": "Models may be left incomplete: a safetensors model that lost a shard cannot be loaded.",
    "erase_mod": "The models with lost files, whole: {names}.",
    "erase_mod_risk": "Their intact files go too. What other models need of them stays as their base.",
    "erase_full": "Those models, and the models that need data of them: {names}.",
    "erase_full_same": "Those models (no other model needs data of them): {names}.",
    "erase_full_same_risk": "Here the same as ERASE MOD.",
    "erase_full_risk": "Models that are intact go too, only because they are coded against those: extract them first to keep them.",
    "clean_prompt": "Type the level to run it:",
    "clean_typed": "Type ERASE, ERASE MOD or ERASE FULL exactly.",
    "clean_changed": "The pack has changed since it was verified. Verify it again first.",
    "cleaning": "▶ Cleaning up {name}",
    "done_clean": "✔ Removed what was lost. Verify the pack again to check it.",
    "mark_model": "⚠ {files} failed verify",
    "mark_file": "⚠ failed verify",
    "mark_file_base": "⚠ failed verify (its base)",
    "rebase": "⇄ Rebase",
    "rebase_title": "⇄ Rebase",
    "rebase_one": "Rebase {name}",
    "rebase_some": "Rebase {count} models:",
    "rebase_find": "Find better bases",
    "rebase_find_note": "Each file is stored against the model it is smallest against; it changes only if the pack gets smaller. A base kept for removed models goes when that saves room.",
    "rebase_alone": "Make independent",
    "rebase_alone_note": "No bases: each file stored on its own, so it no longer needs another model. They may get larger.",
    "rebase_also": "Also {names}, whose bases were set by hand",
    "rebase_hint": "Every file that changes is restored and stored again: about as long as packing it.",
    "rebasing": "▶ Rebasing {what}",
    "done_rebase": "✔ Rebased {what}.\n\n{result}",
    "hint_rebase": "\n\n{models} may store smaller against it: select them and ⇄ Rebase.",
    "rebase_file_one": "Rebase {name} of {model}",
    "rebase_file_some": "Rebase {count} files of {model}:",
}


def t(key, **values):
    return TEXT[key].format(**values)


def count(n, what):
    return f"{n} {what}" + ("" if n == 1 else "s")


def python_exe():
    """python.exe rather than pythonw.exe (no console), for the child processes whose output is shown."""
    head, tail = os.path.split(sys.executable)
    console = os.path.join(head, "python.exe")
    return console if tail.lower() == "pythonw.exe" and os.path.exists(console) else sys.executable


def pack_stamp(path):
    """What changes when a pack changes: the time stamp (and size) of a pack folder's ocpack.json, which every change
    rewrites last, or of a pack file; None if it cannot be read."""
    try:
        st = os.stat(path if la.is_oczip(path) else os.path.join(path, la.MARKER))
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def names_text(names, most=5):
    """Names in a sentence: at most `most`, then how many more."""
    names = list(names)
    shown = names if len(names) <= most + 1 else names[:most]
    return ", ".join(shown) + (f" {t('and_more', count=len(names) - len(shown))}" if len(shown) < len(names) else "")


def sentence(text):
    """`text` as a sentence: the tool's messages start in lower case, a dialog's with a capital."""
    text = str(text).strip()
    return text[:1].upper() + text[1:]


def shorten(path, fits):
    """`path` with folders from its middle left out ("…") until fits(text): its start and its end tell it."""
    if fits(path):
        return path
    sep = "\\" if "\\" in path else "/"
    parts = path.split(sep)
    head, tail = parts[:2 if len(parts) > 3 else 1], parts[2 if len(parts) > 3 else 1:]
    while len(tail) > 1:
        tail = tail[1:]
        if fits(sep.join(head + ["…"] + tail)):
            break
    return sep.join(head + ["…"] + tail)


def short(path):
    """A path for a message box: about 70 characters at most."""
    return shorten(path, lambda text: len(text) <= 70)


def lines(names, most=5):
    """Names one to a line: at most `most` of them, then how many more."""
    names = list(names)
    shown = names if len(names) <= most + 1 else names[:most]
    return "\n".join(shown + ([t("and_more", count=len(names) - len(shown))] if len(shown) < len(names) else []))


def size_text(n):
    if n is None:
        return ""
    if n < 1024:
        return f"{n} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024
        if n < 1024 or unit == "TiB":
            return f"{n:,.1f} {unit}"


def percent(part, whole):
    return f"{part / whole * 100:.1f}%" if whole and part is not None else ""


def extract_target(dest, name, layout):
    """Where restore puts a model's files: dest/<its full name> ("full"), dest/<the last part of it> ("last"), or dest
    itself ("files", with --flat: no folders at all)."""
    if layout == "full":
        return os.path.join(dest, *name.split("/"))
    return os.path.join(dest, name.split("/")[-1]) if layout == "last" else dest


def open_folder(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
    except OSError:
        pass


def kind_of(name):
    """How a file is shown, by its name: oczip, weights (not compressed) or file."""
    low = name.lower()
    if low.endswith(la.OCZIP):
        return "oczip"
    return "weights" if low.endswith(WEIGHT_SUFFIXES) else "file"


def is_pack(path):
    """Whether `path` is a pack: a folder holding ocpack.json, or an .oczip file."""
    return os.path.isfile(os.path.join(path, la.MARKER)) or (la.is_oczip(path) and os.path.isfile(path))


# ----------------------------------------------------------------------------------------------- places


class Place:
    """What the list shows: the drives (Windows; path None), a folder, a pack (pack: a pack folder or a pack file,
    shown as the tree of its models), or one model of a pack (model: its name)."""

    def __init__(self, path=None, pack=False, model=None):
        self.path, self.pack, self.model = path, pack or model is not None, model

    @property
    def view(self):
        return "model" if self.model is not None else "pack" if self.pack else "folder"

    @property
    def read_only(self):
        return self.pack and la.is_oczip(self.path)

    def text(self):
        if self.path is None:
            return t("drives")
        return f"{self.path} › {self.model}" if self.model is not None else self.path

    def parent(self):
        if self.path is None:
            return None
        if self.model is not None:
            return Place(self.path, pack=True)
        up = os.path.dirname(self.path)
        if up != self.path:
            return Place(up, pack=is_pack(up))
        return Place() if os.name == "nt" else None

    def child(self, row):
        """The place a row of this place opens: a folder, drive or pack, or a model of this pack."""
        if self.view == "pack":
            return Place(self.path, model=row["id"])
        if self.path is None:
            return Place(row["name"])
        full = os.path.join(self.path, row["name"])
        return Place(full, pack=is_pack(full))


def place_from_text(text):
    """The place a typed path names (a folder, a pack, or "<pack> › <model>"), or None."""
    text = text.strip().strip('"')
    if not text:
        return None
    path, _, model = (part.strip() for part in text.partition("›"))
    if os.name == "nt" and len(path) == 2 and path[1] == ":":
        path += "\\"
    path = os.path.normpath(os.path.expanduser(path))
    if not (os.path.isdir(path) or (la.is_oczip(path) and os.path.isfile(path))):
        return None
    if model:
        return Place(path, model=model) if is_pack(path) else None
    return Place(path, pack=is_pack(path))


# ----------------------------------------------------------------------------------------------- what a place holds


def list_folder(place):
    """The rows of a folder on disk (or of the drives): dicts with id, name, kind, size, mtime, type."""
    rows = []
    if place.path is None:
        for letter in string.ascii_uppercase:
            if os.path.exists(f"{letter}:\\"):
                rows.append(dict(id=f"{letter}:\\", name=f"{letter}:\\", kind="drive", type=t("type_drive")))
        return rows, None
    with os.scandir(place.path) as found:
        for e in found:
            try:
                folder, st = e.is_dir(), e.stat()
            except OSError:
                continue
            kind = ("pack" if os.path.isfile(os.path.join(e.path, la.MARKER)) else "folder") if folder else kind_of(e.name)
            rows.append(dict(id=e.name, name=e.name, kind=kind, size=None if folder else st.st_size, mtime=st.st_mtime,
                             type=t(f"type_{kind}")))
    return rows, None


def read_pack(path):
    """(the pack at path, its manifests, problems with the others), the pack file closed again afterwards by the
    caller (la.close_pack_files)."""
    p = la.open_pack(path)
    problems = []
    return p, la.all_manifests(p, problems), problems


def needs_text(needs, removed, base=None, usage=None):
    """The models needed, each with how much of it is read (usage: {model: share}), then those removed."""
    usage = usage or {}
    shown = [n + (f" ({usage[n] * 100:.0f}%)" if n in usage else "") for n in needs if n != base]
    return ", ".join(shown + [t("removed", name=n) for n in removed])


def packed_time(created):
    """The time a manifest's "created" (ISO 8601) names, as a timestamp, or None."""
    try:
        return datetime.datetime.fromisoformat(created).timestamp()
    except (TypeError, ValueError):
        return None


def list_pack(place):
    """The rows of a pack: one per model (parent: the model it sits under in the tree), and what to say about it."""
    la.forget_cached_reads()
    try:
        p, manifests, problems = read_pack(place.path)
        summary = la.pack_summary(p, manifests)
        stored = {}  # blob -> its size, to sum up a selection of models counting each blob once
        for sha in {e["sha256"] for m in manifests.values() for e in m["files"]}:
            try:
                stored[sha] = p.blob_span(sha)[2]
            except (OSError, ValueError):
                pass
    finally:
        la.close_pack_files()
    names = {m["name"] for m in summary["models"]}
    rows = []
    for m in summary["models"]:
        base = m["base"] if m["base"] in names else None
        packed = packed_time(m.get("created"))
        rows.append(dict(id=m["name"], name=m["name"], kind="model", files=m["files"], size=m["size"], stored=m["stored"],
                         ratio=percent(m["stored"], m["size"]), parent=base, packed_ts=packed,
                         packed=time.strftime("%Y-%m-%d %H:%M", time.localtime(packed)) if packed else "",
                         needs=needs_text(m["needs"], m["needs_removed"], usage=m.get("usage")),
                         needs_by_base=needs_text(m["needs"], m["needs_removed"], base, m.get("usage")), summary=m,
                         blobs={e["sha256"]: stored.get(e["sha256"], 0) for e in manifests[m["name"]]["files"]}))
    size = sum(m["size"] for m in summary["models"])  # what the models would take on disk, each with all its files
    where = t("where_pack", kind=p.kind, models=count(len(rows), "model"), size=size_text(size),
              stored=size_text(summary["stored"]), ratio=percent(summary["stored"], size),
              blobs=count(summary["blobs"], "blob"))
    if summary["kept"]:
        users = sorted({n for k in summary["kept"] for n in k["needed_by"]})
        where += t("where_kept", size=size_text(sum(k["size"] for k in summary["kept"])), models=", ".join(users))
    if summary["unused"]["blobs"]:
        where += t("where_unused", size=size_text(summary["unused"]["size"]))
    if problems:
        where = t("bad_manifests", count=count(len(problems), "manifest"), first=f"{problems[0][0]}: {problems[0][1]}") + \
            " · " + where
    return rows, where


def list_model(place):
    """The rows of one model of a pack: one per file, with how it is stored and what it is coded against."""
    la.forget_cached_reads()
    try:
        p, manifests, _ = read_pack(place.path)
        if place.model not in manifests:
            raise ValueError(f"no model named {place.model!r} in it")
        m = la.model_summary(p, la.Graph(p, manifests, strict=False), place.model)
    finally:
        la.close_pack_files()
    rows = []
    for f in m["files"]:
        bases = sorted({b["model"] or b["sha256"][:12] for b in f["bases"] if not b["removed"]})
        gone = sorted({b["model"] or b["sha256"][:12] for b in f["bases"] if b["removed"]})
        used, sizes = {}, {}  # of each base model: the bytes this file reads, of the files of it it reads
        for b in f["bases"]:
            if not b["removed"]:
                used[b["model"]] = used.get(b["model"], 0) + b.get("used", 0)
                sizes[b["model"]] = sizes.get(b["model"], 0) + b.get("size", 0)
        usage = {n: used[n] / sizes[n] for n in used if sizes[n]}
        needs = t("against", base=needs_text(bases, gone, usage=usage)) if f["bases"] else ""
        if f["shared_with"]:
            needs += ("  " if needs else "") + t("shared", models=", ".join(f["shared_with"]))
        rows.append(dict(id=f["path"], name=f["path"], kind=kind_of(f["path"]), size=f["size"], stored=f["stored"],
                         ratio=percent(f["stored"], f["size"]), type=f.get("problem") or f["method"] or "", needs=needs,
                         file=f))
    where = t("where_model", name=m["name"], files=count(len(m["files"]), "file"), size=size_text(m["size"]),
              stored=size_text(m["stored"]), ratio=percent(m["stored"], m["size"]))
    shared = {f["sha256"]: f["stored"] for f in m["files"] if f["shared_with"] and f["stored"] is not None}
    if shared:  # blobs other models have too: stored once, counted in each
        where += t("where_shared", size=size_text(sum(shared.values())))
    if m["needs"] or m["needs_removed"]:
        where += t("where_needs", names=needs_text(m["needs"], m["needs_removed"]))
    return rows, where


def list_place(place):
    return {"folder": list_folder, "pack": list_pack, "model": list_model}[place.view](place)


def pack_models(path):
    """The names of the models of the pack at `path` (empty if it cannot be read)."""
    try:
        return sorted(read_pack(path)[1])
    except (OSError, ValueError, SystemExit):
        return []
    finally:
        la.close_pack_files()


# ----------------------------------------------------------------------------------------------- the Recycle Bin


def tree_size(path):
    """Bytes of a file, or of every file below a folder (links not followed)."""
    if not os.path.isdir(path) or os.path.islink(path):
        return os.lstat(path).st_size
    return sum(os.lstat(os.path.join(d, n)).st_size for d, _, names in os.walk(path) for n in names)


def to_trash(paths, size):
    """Move these files and folders to the Recycle Bin (Windows) or the desktop trash (Linux, the freedesktop.org
    Trash specification); None, or why not. Nothing is ever deleted for good."""
    if os.name == "nt":
        return windows_trash(paths, size)
    if sys.platform.startswith("linux"):
        return linux_trash(paths)
    return "this system has no Recycle Bin the window knows: use your file manager"


def windows_trash(paths, size):
    """The Recycle Bin of the drive: refused where it would delete for good instead (a network, removable or optical
    drive, a bin set to delete at once, or one too small for them); Windows still asks before it ever would."""
    import ctypes
    import winreg
    from ctypes import wintypes

    root = os.path.splitdrive(os.path.abspath(paths[0]))[0] + "\\"
    if root.startswith("\\\\"):
        return f"{root} is a network folder: it has no Recycle Bin"
    kernel32, shell32 = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("shell32")
    kind = kernel32.GetDriveTypeW(root)
    if kind != 3:  # DRIVE_FIXED: other drives (network, removable, optical, RAM) have no Recycle Bin
        return f"the drive {root} has no Recycle Bin"
    volume = ctypes.create_unicode_buffer(64)
    if kernel32.GetVolumeNameForVolumeMountPointW(root, volume, len(volume)):
        guid = volume.value.rstrip("\\").rsplit("\\", 1)[-1].removeprefix("Volume")
        key = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{guid}"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                if winreg.QueryValueEx(k, "NukeOnDelete")[0]:
                    return f"the Recycle Bin of {root} is set to delete files at once"
                capacity = winreg.QueryValueEx(k, "MaxCapacity")[0] << 20
        except OSError:
            capacity = None

        class Info(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]

        info = Info(ctypes.sizeof(Info), 0, 0)
        used = info.i64Size if shell32.SHQueryRecycleBinW(root, ctypes.byref(info)) == 0 else 0
        if capacity is not None and used + size > capacity:
            return (f"the Recycle Bin of {root} holds {size_text(capacity)} ({size_text(used)} in it now), too little for "
                    f"{size_text(size)}: empty it, give it more room, or delete them yourself")

    class Operation(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]

    names = ctypes.create_unicode_buffer("\0".join(os.path.abspath(p) for p in paths) + "\0")  # ends in two NULs
    # FO_DELETE; FOF_ALLOWUNDO (to the bin), FOF_NOCONFIRMATION (asked already), FOF_WANTNUKEWARNING (Windows asks
    # before it would ever delete one for good)
    op = Operation(None, 3, ctypes.cast(names, ctypes.c_wchar_p), None, 0x40 | 0x10 | 0x4000, False, None, None)
    code = shell32.SHFileOperationW(ctypes.byref(op))
    if op.fAnyOperationsAborted:
        return "cancelled"
    return f"Windows reported error {code:#x}" if code else None


def linux_trash(paths):
    """The desktop trash: the home trash for files on the home's file system, else <mount point>/.Trash-<uid>."""
    from urllib.parse import quote

    home = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash")
    os.makedirs(os.path.dirname(home), exist_ok=True)
    for path in paths:
        path = os.path.abspath(path)
        device = os.lstat(path).st_dev
        if os.stat(os.path.dirname(home)).st_dev == device:
            trash = home
        else:
            top = path
            while os.path.dirname(top) != top and os.stat(os.path.dirname(top)).st_dev == device:
                top = os.path.dirname(top)
            trash = os.path.join(top, f".Trash-{os.getuid()}")
        for sub in ("files", "info"):
            os.makedirs(os.path.join(trash, sub), mode=0o700, exist_ok=True)
        base, n = os.path.basename(path), 1
        while True:
            name = base if n == 1 else f"{base}.{n}"
            try:
                fd = os.open(os.path.join(trash, "info", name + ".trashinfo"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                n += 1
        with os.fdopen(fd, "w") as f:
            f.write(f"[Trash Info]\nPath={quote(path)}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        try:
            os.rename(path, os.path.join(trash, "files", name))
        except OSError as e:
            os.remove(os.path.join(trash, "info", name + ".trashinfo"))
            return f"{path} could not go to the trash ({e.strerror or e})"
    return None


# ----------------------------------------------------------------------------------------------- the window


class App:
    def __init__(self, root, start):
        self.root = root
        self.place, self.rows, self.listing = None, {}, []  # rows: tree item -> row shown; listing: every row
        self.matched, self.quiet = set(), False  # ids the filter lets through; quiet: the filter is being reset
        self.seen, self.looked, self.inside_pack = None, 0.0, False  # the place's stamp, when it was looked at
        self.sort_by, self.descending = "name", False
        self.pack_mode, self.pack_target = "folder", ""  # how, and into which pack folder, the last pack went
        self.extract_dest, self.extract_layout = "", "last"  # where and how the last extract went
        self.jobs, self.job, self.proc, self.output, self.busy = [], None, None, [], None
        self.list_files = []  # the list files of the command running (--models-from, --files-from), if any
        self.job_damage = self.marks = None  # the damage report of the job running; what the last one found lost
        self.events = []  # the hints and summaries of the job running (or that ran last)
        self.lines = queue.Queue()  # output of the running process; None when it has ended
        self.cancelling = self.quit_after = False
        self.scale = root.winfo_fpixels("1i") / 96
        self.versions = " · ".join((la.version_text("GUI", VERSION, __file__), la.version_text("CLI", la.VERSION, la.__file__)))
        root.title(f"OverCompressing {self.versions}")
        root.geometry(f"{self.px(1120)}x{self.px(720)}")
        root.minsize(self.px(760), self.px(460))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.go(start or Place(os.getcwd(), pack=is_pack(os.getcwd())))
        root.after(100, self.poll)
        root.after(WATCH_SECONDS * 1000, self.watch)

    def px(self, n):
        return int(n * self.scale)

    # ------------------------------------------------------------------------------------------------------ layout

    def build(self):
        self.bold = tkfont.nametofont("TkDefaultFont").copy()  # kept: a font object deletes its font when collected
        self.bold.configure(weight="bold")
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style = ttk.Style()
        style.configure("Treeview", rowheight=line + self.px(8))
        # Tk 8.6.9+: without this, tag backgrounds do not show; selected rows keep the selection's colors
        style.map("Treeview", background=[("selected", "#2f6fd6")], foreground=[("selected", "#ffffff")])

        top = ttk.Frame(self.root, padding=(self.px(10), self.px(10), self.px(10), self.px(6)))
        top.pack(fill="x")
        ttk.Style().configure("Up.TButton", font=self.bold)  # bold, of the same size: as high as the path bar
        self.up_button = ttk.Button(top, text=t("up"), width=6, command=self.go_up, style="Up.TButton")
        self.up_button.pack(side="left")
        self.path = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.path)
        entry.pack(side="left", fill="x", expand=True, padx=(self.px(6), 0))
        entry.bind("<Return>", lambda _: self.go_to_text())

        self.bar = ttk.Frame(self.root, padding=(self.px(10), 0, self.px(10), self.px(6)))
        self.bar.pack(fill="x")
        self.commands = {"pack_to": self.pack, "pack_in": self.pack, "add_file": self.add_file, "save": self.export,
                         "extract": self.extract, "verify": self.verify, "remove": self.remove, "rename": self.rename,
                         "rebase": self.rebase, "refresh": self.refresh}
        self.buttons, self.bar_items, self.laid_out = {}, [], None  # laid out: the view the buttons are for
        self.mode = tk.Label(self.bar, font=self.bold, fg="#202124")  # what the place is: the legend's word and color
        self.mode.pack(side="left", padx=(0, self.px(4)))
        self.mode_note = ttk.Label(self.bar, foreground="#5f6368")
        self.mode_note.pack(side="left", padx=(0, self.px(10)))
        legend = ttk.Frame(self.bar)
        legend.pack(side="right")
        for kind, label in LEGEND:
            tk.Label(legend, text=f" {label} ", bg=COLORS[kind], fg="#202124").pack(side="left", padx=(self.px(3), 0))

        tools = ttk.Frame(self.root, padding=(self.px(10), 0, self.px(10), self.px(6)))
        tools.pack(fill="x")
        ttk.Label(tools, text=t("filter")).pack(side="left")
        self.filter = tk.StringVar()
        self.filter_entry = ttk.Entry(tools, textvariable=self.filter, width=36)
        self.filter_entry.pack(side="left", padx=(self.px(6), 0))
        self.filter_entry.bind("<Escape>", lambda _: self.filter.set(""))
        ttk.Button(tools, text=t("filter_clear"), width=3, command=lambda: self.filter.set("")).pack(side="left",
                                                                                                  padx=(self.px(4), 0))
        self.filter.trace_add("write", lambda *_: self.quiet or self.filtered())
        self.root.bind("<Control-f>", lambda _: self.filter_entry.focus_set())
        self.hint = ttk.Label(tools, text=t("hint_keys"), foreground="#5f6368")
        self.hint.pack(side="right")
        self.views = ttk.Frame(tools)  # shown for a pack only, right of the hint
        self.view_mode = tk.StringVar(value="list")
        for value in ("list", "base"):
            ttk.Radiobutton(self.views, text=t(f"view_{value}"), value=value, variable=self.view_mode,
                            command=self.filtered, style="Toolbutton").pack(side="left", padx=(self.px(2), 0))

        panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=self.px(10), pady=(0, self.px(10)))
        frame = ttk.Frame(panes)
        self.tree = ttk.Treeview(frame, columns=list(COLUMNS), show="tree headings", selectmode="extended")
        self.tree.heading("#0", text=t("col_name"), anchor="w", command=lambda: self.sort("name"))
        self.tree.column("#0", width=self.px(320), anchor="w", stretch=False)  # widths: fit_columns
        for name, (key, width, anchor) in COLUMNS.items():
            self.tree.heading(name, text=t(key), anchor=anchor, command=lambda n=name: self.sort(n))
            self.tree.column(name, width=self.px(width), anchor=anchor, stretch=False)
        for kind, color in COLORS.items():
            self.tree.tag_configure(kind, background=color)
        self.tree.tag_configure("context", foreground="#8a8f98")  # shown by the filter only for the chain it is in
        self.tree.tag_configure("damaged", foreground="#b3261e")  # found lost by the last verify
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        across = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)

        def across_set(first, last):  # shown only while the columns are wider than the list
            if float(first) <= 0 and float(last) >= 1:
                across.grid_remove()
            else:
                across.grid()
            across.set(first, last)

        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=across_set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        across.grid(row=1, column=0, sticky="ew")
        across.grid_remove()
        self.status = ttk.Label(frame, anchor="w", width=1)  # the list summed up; width 1: never widens the window
        self.status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(self.px(4), self.px(4)))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        panes.add(frame, weight=3)
        self.fitted, self.naturals = None, {}
        self.tree.bind("<Configure>", lambda e: e.width != self.fitted and self.fit_columns())
        self.tree.bind("<Double-1>", self.double_click)
        self.tree.bind("<Return>", lambda _: self.open_row(self.tree.focus()))
        self.tree.bind("<BackSpace>", lambda _: self.go_up())
        self.tree.bind("<Alt-Up>", lambda _: self.go_up())
        self.tree.bind("<<TreeviewSelect>>", lambda _: (self.update_buttons(), self.say_where()))
        self.tree.bind("<Escape>", lambda _: self.tree.selection_set(()))
        self.tree.bind("<ButtonPress-1>", self.press, add="+")
        self.tree.bind("<B1-Motion>", self.drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self.release, add="+")
        self.drag_from, self.drag_keep, self.drag_y, self.drag_job = None, set(), 0, None
        self.tip, self.tip_at, self.tip_job = None, None, None
        self.tree.bind("<Motion>", self.hover, add="+")
        for sequence in ("<Leave>", "<ButtonPress>", "<MouseWheel>"):
            self.tree.bind(sequence, self.hide_tip, add="+")

        frame = ttk.Frame(panes)
        mono = next((f for f in ("Consolas", "Cascadia Mono", "Menlo", "DejaVu Sans Mono") if f in tkfont.families()), None)
        self.mono = tkfont.Font(family=mono, size=tkfont.nametofont("TkDefaultFont").actual("size")) if mono else "TkFixedFont"
        self.log = tk.Text(frame, height=8, wrap="none", state="disabled", font=self.mono, relief="flat",
                           padx=self.px(6), pady=self.px(4))
        self.log.configure(bg="#1c2130", fg="#d7dbe3", selectbackground="#3b4a66", selectforeground="#ffffff",
                           insertbackground="#d7dbe3")  # a console, a little blue
        self.log.tag_configure("command", foreground="#82b4ff")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        panes.add(frame, weight=1)

        self.menu = tk.Menu(self.root, tearoff=False)
        self.tree.bind("<Button-2>" if sys.platform == "darwin" else "<Button-3>", self.popup)
        self.root.bind("<F5>", self.refresh)
        self.root.bind("<Control-a>", self.select_all)
        self.root.bind("<Control-A>", self.select_all)  # (Caps Lock)
        for event in ("<Motion>", "<ButtonPress>"):  # someone is looking: look at the place (at most every 5 s)
            self.root.bind_all(event, lambda _: self.watch(by_hand=True), add="+")

    def lay_out(self, view):
        """The buttons, and the right-click menu, of a view (BUTTONS)."""
        if view == self.laid_out:
            return
        for widget in self.bar_items:
            widget.destroy()
        self.bar_items, self.buttons, self.laid_out = [], {}, view
        self.menu.delete(0, "end")
        for key in BUTTONS[view]:
            if key is None:
                widget = ttk.Separator(self.bar, orient="vertical")
                widget.pack(side="left", fill="y", padx=self.px(6))
            else:
                widget = self.buttons[key] = ttk.Button(self.bar, text=t(key), command=self.commands[key])
                widget.pack(side="left", padx=(0, self.px(4)))
                self.menu.add_command(label=t(key), command=self.commands[key])
            self.bar_items.append(widget)

    def write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        if int(self.log.index("end").split(".")[0]) > 5000:
            self.log.delete("1.0", "1000.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def popup(self, event):
        """The right-click menu: for the row under the mouse (or what is selected, if it is), or on empty space for
        nothing selected."""
        self.hide_tip()
        region, item = self.tree.identify_region(event.x, event.y), self.tree.identify_row(event.y)
        if region not in ("tree", "cell", "nothing"):
            return
        if not item:
            self.tree.selection_set(())
        elif item not in self.tree.selection():
            self.tree.selection_set(item)
        self.update_buttons()
        if not self.proc:
            self.menu.tk_popup(event.x_root, event.y_root)

    def select_all(self, event=None):
        """Ctrl+A: in a text field, all its text; elsewhere, every row the filter lets through (not the gray rows
        shown only to hold a chain together)."""
        widget = event.widget if event is not None else None
        if isinstance(widget, (tk.Entry, ttk.Entry)):
            widget.select_range(0, "end")
            widget.icursor("end")
        elif isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end")
        elif not self.proc:
            self.tree.selection_set([i for i, r in self.rows.items() if r["id"] in self.matched])
        return "break"

    def press(self, event):
        """The left button down in the list: on a row, a drag may start there; on empty space, nothing stays selected
        (with Ctrl, what is), and a drag may start there too: beside a row (columns narrowed by hand), as from that
        row; under the last one, from below."""
        self.hide_tip()
        region, row = self.tree.identify_region(event.x, event.y), self.tree.identify_row(event.y)
        ctrl = event.state & 0x4
        self.drag_keep = set(self.tree.selection()) if ctrl else set()  # Ctrl: as well as what is selected
        self.drag_from = row if region in ("tree", "cell") else row or BELOW if region == "nothing" else None
        if region == "nothing" and not ctrl:
            self.tree.selection_set(())

    def drag(self, event):
        """Dragging from a row selects every row from it to the one under the mouse; above or below the list, the
        list scrolls on while the button is held."""
        if self.drag_from is None:
            return
        self.drag_y = event.y
        self.drag_to()
        if not self.drag_job and not 0 <= event.y < self.tree.winfo_height():
            self.drag_job = self.root.after(60, self.drag_scroll)

    def drag_scroll(self):
        self.drag_job = None
        if self.drag_from is not None and not 0 <= self.drag_y < self.tree.winfo_height():
            self.tree.yview_scroll(-1 if self.drag_y < 0 else 1, "units")
            self.drag_to()
            self.drag_job = self.root.after(60, self.drag_scroll)

    def drag_to(self):
        order = self.in_order()
        if not order or (self.drag_from != BELOW and self.drag_from not in order):  # (listed again meanwhile)
            return
        y = min(max(self.drag_y, 0), self.tree.winfo_height() - 1)
        item = self.tree.identify_row(y)
        if not item and y < self.tree.winfo_height() // 2:  # over the headings: the first row shown
            item = order[min(int(self.tree.yview()[0] * len(order) + 0.5), len(order) - 1)]
        if self.drag_from == BELOW:  # from under the last row: every row from the mouse down
            chosen = set(order[order.index(item):]) if item else set()
        else:
            a, b = order.index(self.drag_from), order.index(item or order[-1])
            chosen = set(order[min(a, b):max(a, b) + 1])
        chosen |= self.drag_keep
        if chosen != set(self.tree.selection()):
            self.tree.selection_set(list(chosen))

    def release(self, _=None):
        self.drag_from = None
        if self.drag_job:
            self.root.after_cancel(self.drag_job)
            self.drag_job = None

    def in_order(self):
        """The list's items from top to bottom as shown (the rows of a closed row left out)."""
        out, stack = [], list(reversed(self.tree.get_children("")))
        while stack:
            item = stack.pop()
            out.append(item)
            if self.tree.item(item, "open"):
                stack.extend(reversed(self.tree.get_children(item)))
        return out

    # ------------------------------------------------------------------------------------------------- browsing

    def go(self, place, select=None):
        """Show a place (and select the row named `select`); if it cannot be listed, stay where the list was."""
        try:
            rows, where = list_place(place)
            self.mark(rows, place)
        except (OSError, ValueError, SystemExit) as e:
            if self.place is None and place.parent():  # at start: show what can be shown
                return self.go(place.parent())
            messagebox.showerror("OverCompressing", t("cannot_list", problem=getattr(e, "strerror", None) or e),
                                 parent=self.root)
            return
        if self.place is None or place.text() != self.place.text():  # another place: the filter starts empty
            self.quiet = True
            self.filter.set("")
            self.quiet = False
        self.place, self.where = place, where
        self.inside_pack = place.view == "folder" and place.path is not None and la.pack_folder_of(place.path) is not None
        self.lay_out(place.view)
        kind = {"model": "model", "pack": "oczip" if place.read_only else "pack"}.get(place.view, "folder")
        self.mode.configure(text=f" {dict(LEGEND)[kind]} ", bg=COLORS[kind])
        self.mode_note.configure(text=t("mode_in_file") if place.view == "model" and place.read_only else "")
        self.seen, self.looked = self.stamp(), time.monotonic()
        self.path.set(place.text())
        self.root.title(f"OverCompressing {self.versions} — {place.text()}")
        if place.view == "pack":
            self.views.pack(side="right", before=self.hint, padx=(self.px(12), 0))
        else:
            self.views.pack_forget()
        self.tree.configure(displaycolumns=SHOWN[place.view])
        self.show(rows, select)
        self.fit_columns()
        self.say_where()
        self.update_buttons()

    def fit_columns(self):
        """Size the columns of this view to what they hold: the numbers and dates as wide as their texts; the name, Type
        and Needs too, as far as the list's width allows. What it lacks is taken from each of those in proportion to
        what it wants beyond its least width (its width in COLUMNS, the name's 200); what is left over goes to Needs
        (in a folder, to the name). Columns wider than the list scroll sideways. Done again when the view, the listing
        or the list's width changes (a column dragged wider keeps that only until then)."""
        if self.place is None:
            return
        self.tree.update_idletasks()
        self.fitted = self.tree.winfo_width()
        shown = SHOWN[self.place.view]
        flexible = ["#0"] + [c for c in shown if c in FITTED]
        fixed = {c: self.natural(c) for c in shown if c not in FITTED}
        want = {c: self.natural(c) for c in flexible}
        least = {c: min(want[c], self.px(200 if c == "#0" else COLUMNS[c][1])) for c in flexible}
        room = self.fitted - sum(fixed.values()) - self.px(4)
        spare, extra = room - sum(least.values()), sum(want[c] - least[c] for c in flexible)
        if spare >= extra:
            got = dict(want)
            got["needs" if "needs" in want else "#0"] += spare - extra
        else:
            got = {c: least[c] + (want[c] - least[c]) * max(spare, 0) // max(extra, 1) for c in flexible}
        for c in shown:
            self.tree.column(c, width=got[c] if c in got else fixed[c])
        self.tree.column("#0", width=got["#0"])

    def natural(self, column):
        """The width the column needs for its longest text among all the rows of the place (not only those the filter
        shows, so that typing in the filter does not move the columns), its heading included."""
        key = (column, self.by_base)
        if key not in self.naturals:
            depth = self.depths() if self.by_base and column == "#0" else {}
            texts = [(r["name"] if column == "#0" else self.cell(r, column), depth.get(r["id"], 0)) for r in self.listing]
            longest = sorted(texts, key=lambda x: len(x[0]) + 3 * x[1], reverse=True)[:50]  # measuring them all is slow
            font, pad = tkfont.nametofont("TkDefaultFont"), self.px(16 + (24 if column == "#0" else 0))  # + the indicator
            heading = tkfont.nametofont("TkHeadingFont").measure(t("col_name" if column == "#0" else COLUMNS[column][0]) + " ▼")
            self.naturals[key] = max([heading] + [font.measure(s) + self.px(20) * d for s, d in longest]) + pad
        return self.naturals[key]

    def depths(self):
        """How deep each row of the place sits in the tree by base."""
        parent, depth = {r["id"]: r.get("parent") for r in self.listing}, {}
        for rid in parent:
            d, up, seen = 0, parent[rid], {rid}
            while up in parent and up not in seen:  # (a circle of models ends where it began)
                seen.add(up)
                d, up = d + 1, parent[up]
            depth[rid] = d
        return depth

    def cell(self, row, column):
        """The text of a row in a column (Needs differs by base)."""
        if column in ("size", "stored"):
            return size_text(row.get(column))
        if column == "modified":
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(row["mtime"])) if row.get("mtime") else ""
        value = row.get("needs_by_base", row.get("needs")) if column == "needs" and self.by_base else row.get(column)
        return "" if value is None else str(value)

    def hover(self, event):
        """A moment after the mouse comes to a cell whose column cuts its text, show the whole text below it."""
        item, column = self.tree.identify_row(event.y), self.tree.identify_column(event.x)
        if (item, column) == self.tip_at:
            return
        self.hide_tip()
        self.tip_at = (item, column)
        if item not in self.rows:
            return
        if column == "#0":
            text, used, up = self.tree.item(item, "text"), self.px(24), self.tree.parent(item)
            while up:  # indented by depth
                used, up = used + self.px(20), self.tree.parent(up)
        else:
            text, used = self.tree.set(item, self.tree.column(column, "id")), self.px(8)
        if text and tkfont.nametofont("TkDefaultFont").measure(text) + used > self.tree.column(column, "width"):
            x, y = event.x_root + self.px(12), event.y_root + self.px(18)
            self.tip_job = self.root.after(400, lambda: self.show_tip(text, x, y))

    def show_tip(self, text, x, y):
        self.tip_job = None
        self.tip = tk.Toplevel(self.root)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=text, background="#ffffe0", relief="solid", borderwidth=1, padx=self.px(4),
                 pady=self.px(2)).pack()
        self.tip.update_idletasks()
        right = self.root.winfo_rootx() + self.root.winfo_width()  # kept within the window's width where it can be
        self.tip.wm_geometry(f"+{max(min(x, right - self.tip.winfo_reqwidth()), self.root.winfo_rootx())}+{y}")

    def hide_tip(self, _=None):
        if self.tip_job:
            self.root.after_cancel(self.tip_job)
        if self.tip:
            self.tip.destroy()
        self.tip = self.tip_job = self.tip_at = None

    @property
    def by_base(self):
        return self.place is not None and self.place.view == "pack" and self.view_mode.get() == "base"

    def filtered(self):
        """Show the place's rows again, after the filter or the view changed."""
        if self.place is not None:
            self.show()
            self.fit_columns()  # by base, the names are indented and Needs says other things
            self.say_where()
            self.update_buttons()

    def show(self, rows=None, select=None):
        """List the rows (default: the place's, as listed last), sorted by the column chosen, folders first. A pack
        shown by base puts each model under the model most of its data is coded against. With a filter, only the
        rows whose names hold its text, ignoring case; by base, also the chain above each, in gray, so the tree holds
        together."""
        fresh = rows is not None  # a new listing: nothing of the last one stays selected
        self.hide_tip()
        if fresh:
            self.listing, self.naturals = rows, {}
        rows, by_base = self.listing, self.by_base
        text = self.filter.get().strip().casefold()
        self.matched = {r["id"] for r in rows if text in r["name"].casefold()}
        shown = set(self.matched)
        if by_base and text:
            parent = {r["id"]: r.get("parent") for r in rows}
            for rid in self.matched:
                up = parent.get(rid)
                while up is not None and up not in shown:  # (a circle of models ends where it began)
                    shown.add(up)
                    up = parent.get(up)
        rows = [r for r in rows if r["id"] in shown]
        selected = {select} if select is not None else set() if fresh else             {self.rows[i]["id"] for i in self.tree.selection() if i in self.rows}

        def key(row):
            value = row.get(SORT_KEYS.get(self.sort_by, self.sort_by))
            if self.sort_by == "ratio":
                value = float(value[:-1]) if value else -1.0
            elif value is None:
                value = -1
            elif isinstance(value, str):
                value = value.casefold()
            return value

        rows = sorted(rows, key=lambda r: r["name"].casefold())
        rows.sort(key=key, reverse=self.descending)
        rows.sort(key=lambda r: r["kind"] not in ("folder", "drive", "pack"))  # folders first, as in a file manager
        self.tree.delete(*self.tree.get_children())
        self.rows, items = {}, {}
        children = {}
        for row in rows:
            children.setdefault(row.get("parent") if by_base and row.get("parent") in shown else None, []).append(row)

        def insert(row, parent):
            tags = (row["kind"],) + (("context",) if row["id"] not in self.matched else ()) + (("damaged",) * bool(row.get("damaged")))
            item = self.tree.insert(parent, "end", text=row["name"], open=True, tags=tags,
                                    values=[self.cell(row, c) for c in COLUMNS])
            self.rows[item], items[row["id"]] = row, item
            for child in children.get(row["id"], ()):
                if child["id"] not in items:  # (a circle of models is shown once)
                    insert(child, item)

        for row in children.get(None, []):
            insert(row, "")
        for row in rows:  # rows whose parents go round in a circle
            if row["id"] not in items:
                insert(row, "")
        keep = [items[i] for i in selected if i in items]
        if keep:
            self.tree.selection_set(keep)
            self.tree.see(keep[0])
        for name, (key_, _, _) in COLUMNS.items():
            mark = (" ▼" if self.descending else " ▲") if name == self.sort_by else ""
            self.tree.heading(name, text=t(key_) + mark)
        self.tree.heading("#0", text=t("col_name") + ((" ▼" if self.descending else " ▲") if self.sort_by == "name" else ""))

    def sort(self, column):
        self.descending = not self.descending if column == self.sort_by else False
        self.sort_by = column
        self.show()

    def say_where(self):
        """The line under the list: what is selected, summed up, then the place."""
        if self.place is None or self.proc:  # (while something runs, the line says what)
            return
        text = self.where or count(len(self.listing), "item")
        if self.filter.get().strip():
            text = t("filtered", shown=len(self.matched), total=len(self.listing)) + " · " + text
        if self.place.read_only:
            text += f" · {t('read_only')}"
        rows = self.chosen()
        if rows:
            text = self.sum_up(rows) + "   │   " + text
        self.status.configure(text=text)

    def sum_up(self, rows):
        """What the selected rows take: on disk; in a pack, also what they are stored in, each blob counted once."""
        size = sum(r["size"] for r in rows if r.get("size") is not None)
        if self.place.view == "folder":
            files = any(r.get("size") is not None for r in rows)
            return t("selected_size", count=len(rows), size=size_text(size)) if files else t("selected_count", count=len(rows))
        blobs = {}
        for r in rows:
            if "blobs" in r:  # a model
                blobs.update(r["blobs"])
            elif r.get("stored") is not None:  # a file of a model
                blobs[r["file"]["sha256"]] = r["stored"]
        stored = sum(blobs.values())
        return t("selected", count=len(rows), size=size_text(size), stored=size_text(stored), ratio=percent(stored, size))

    def refresh(self, _=None):
        """List the place again, keeping what is selected and where the list is scrolled."""
        chosen, top = {self.rows[i]["id"] for i in self.tree.selection() if i in self.rows}, self.tree.yview()[0]
        self.go(self.place)
        items = [i for i, r in self.rows.items() if r["id"] in chosen]
        if items:
            self.tree.selection_set(items)
        self.tree.yview_moveto(top)

    def stamp(self):
        """What changes when the place changes: the time stamp (and size) of a pack folder's ocpack.json, which every
        change rewrites last, of a pack file, or of a folder itself; None for the drives."""
        place = self.place
        if place is None or place.path is None:
            return None
        if place.pack:
            return pack_stamp(place.path) or "gone"
        try:
            st = os.stat(place.path)
        except OSError:
            return "gone"
        return st.st_mtime_ns, st.st_size

    def watch(self, by_hand=False):
        """Look at the place's stamp, at most every WATCH_SECONDS: on a timer, and at once when the mouse moves or
        clicks in the window after that long; list the place again only if the stamp changed. Nothing is looked at
        while the window is minimized, a dialog is open or something runs (its end lists the place anyway)."""
        if not by_hand:
            self.root.after(WATCH_SECONDS * 1000, self.watch)
        now = time.monotonic()
        if now - self.looked < WATCH_SECONDS or self.proc or self.busy is not None or self.place is None:
            return
        try:
            if self.root.state() == "iconic" or self.root.grab_current() is not None:
                return
        except tk.TclError:  # the window is going
            return
        self.looked = now
        stamp = self.stamp()
        if stamp == self.seen:
            return
        if stamp == "gone":  # it was deleted or moved: show the nearest place that is still there
            up = self.place.parent()
            while up is not None and up.path is not None and not os.path.exists(up.path):
                up = up.parent()
            self.seen = stamp
            if up is not None:
                self.go(up)
            return
        self.refresh()

    def go_up(self):
        """Go to the parent, with the place just left selected there."""
        up = self.place.parent() if self.place else None
        if up:
            if self.place.model is not None:
                self.go(up, self.place.model)
            else:
                self.go(up, os.path.basename(self.place.path) or self.place.path)  # a drive's row is named "C:\"

    def go_to_text(self):
        place = place_from_text(self.path.get())
        if place:
            self.go(place)
        else:
            messagebox.showerror("OverCompressing", t("no_place", text=self.path.get()), parent=self.root)
            self.path.set(self.place.text())

    def double_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "tree" or self.tree.identify_element(event.x, event.y) != "Treeitem.indicator":
            self.open_row(self.tree.identify_row(event.y))

    def mark(self, rows, place):
        """⚠ in Needs (and the row in red) for what the last verify that failed found lost, while the pack is as it
        was then: only the window remembers it (verify writes nothing into a pack)."""
        m = self.marks
        if not m or not place.pack or os.path.normcase(os.path.abspath(place.path)) != os.path.normcase(m["pack"]):
            return
        if pack_stamp(m["pack"]) != m["stamp"]:
            self.marks = None
            return
        for r in rows:
            if place.view == "pack" and r["id"] in m["models"]:
                note = t("mark_model", files=count(m["models"][r["id"]], "file"))
            elif place.view == "model" and (place.model, r["id"]) in m["lost"]:
                note = t("mark_file" if m["lost"][(place.model, r["id"])] else "mark_file_base")
            else:
                continue
            for key in ("needs", "needs_by_base"):
                if key in r:
                    r[key] = note + ("  " + r[key] if r[key] else "")
            r["damaged"] = True

    def open_row(self, item):
        """Go into a folder, a drive, a pack or a model."""
        row = self.rows.get(item)
        if row and row["kind"] in ("folder", "drive", "pack", "oczip", "model"):
            self.go(self.place.child(row))

    def update_buttons(self):
        """Refresh, Pack in and Add file can always be used (where the place can be changed); the others act on what
        is selected (Rename on one row), so they are off while nothing is."""
        place, count = self.place, len(self.tree.selection())
        writable = place is not None and place.pack and not place.read_only  # a pack folder, or a model in one
        on_disk = place is not None and place.view == "folder" and place.path is not None and not self.inside_pack
        usable = {"refresh": True, "pack_in": writable, "add_file": writable, "pack_to": on_disk and count > 0,
                  "remove": (on_disk or writable) and count > 0, "save": count > 0, "extract": count > 0,
                  "verify": count > 0, "rename": writable and count == 1, "rebase": writable and count > 0}
        keys = [k for k in BUTTONS[self.laid_out or "folder"] if k]
        for index, key in enumerate(keys):
            state = "normal" if usable[key] and not self.proc else "disabled"
            self.buttons[key].configure(state=state)
            self.menu.entryconfigure(index, state=state)

    # ------------------------------------------------------------------------------------------------- selections

    def chosen(self):
        return [self.rows[i] for i in self.tree.selection() if i in self.rows]

    def full(self, name):
        """The path of `name` in the current folder."""
        return os.path.join(self.place.path, name)

    def targets(self):
        """{(pack, model): None for the whole model, or the set of its files' paths} for what is selected."""
        place, rows = self.place, self.chosen()
        if place.view == "model" and rows:
            paths = {r["id"] for r in rows}
            return {(place.path, place.model): None if paths == {r["id"] for r in self.listing} else paths}
        if place.view == "pack":
            return {(place.path, r["id"]): None for r in rows}
        return {}

    # ------------------------------------------------------------------------------------------------- actions

    def extract(self):
        plan = self.targets()
        if not plan:
            messagebox.showinfo(t("extract"), t("nothing_to_extract"), parent=self.root)
            return
        answer = self.ask_extract(plan)
        if not answer:
            return
        dest, layout = answer
        targets = {key: extract_target(dest, key[1], layout) for key in plan}
        problems = [p for p in (la.place_problem(target) for target in targets.values()) if p]
        if problems:
            messagebox.showerror(t("extract"), "\n".join(sentence(p) for p in dict.fromkeys(problems)), parent=self.root)
            return
        jobs = []
        for (pack, name), paths in sorted(plan.items()):
            argv = ["restore", pack, name, targets[(pack, name)], *[f"--file={p}" for p in sorted(paths or ())],
                    *(["--flat"] if layout == "files" else [])]
            jobs.append({"argv": argv, "title": t("extracting", name=name), "retry": self.offer_overwrite})
        self.start(jobs, lambda: messagebox.askyesno(t("extract"), t("done_extract_q", dest=short(dest)), parent=self.root)
                   and open_folder(dest))

    def offer_overwrite(self, job, output):
        """The arguments to run a failed restore again with --overwrite, if that is why it failed and the user agrees."""
        if "with other contents" in output and "--overwrite" not in job["argv"] and messagebox.askyesno(
                t("extract"), t("overwrite_q", dest=short(job["argv"][3])), parent=self.front()):
            return job["argv"] + ["--overwrite"]
        return None

    def ask_extract(self, plan):
        """(destination folder, which folders to make there: "full", "last" or "files") the user picks, or None."""
        names = sorted({name for _, name in plan})
        lasts = [n.split("/")[-1] for n in names]
        clash = next((n for n in lasts if lasts.count(n) > 1), None)
        usable = {"full": True, "last": clash is None, "files": len(plan) == 1}
        dialog = self.dialog(t("extract_title"))
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(WRAP)
        what = [t("whole_model", name=name) if paths is None else t("some_files", name=name, files=count(len(paths), "file"))
                for (_, name), paths in sorted(plan.items(), key=lambda kv: kv[0][1])]
        if len(what) == 1:
            self.heading(frame, t("extract_one", what=what[0]))
        else:
            self.heading(frame, t("extract_some", count=len(what)))
            ttk.Label(frame, text=lines(what)).pack(anchor="w", pady=(self.px(2), 0))
        ttk.Label(frame, text=t("dest_prompt")).pack(anchor="w", pady=(self.px(12), self.px(4)))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        dest = tk.StringVar(value=self.extract_dest)
        entry = ttk.Entry(row, textvariable=dest, width=52)
        entry.pack(side="left", fill="x", expand=True)

        def browse():
            chosen = filedialog.askdirectory(parent=dialog, title=t("pick_dest"), mustexist=False,
                                             initialdir=dest.get() or None)
            if chosen:
                dest.set(os.path.normpath(chosen))

        ttk.Button(row, text=t("browse"), command=browse).pack(side="left", padx=(self.px(6), 0))
        ttk.Label(frame, text=t("layout")).pack(anchor="w", pady=(self.px(12), self.px(4)))
        layout = tk.StringVar(value=next(k for k in (self.extract_layout, "last", "full") if usable[k]))
        named = next((n for n in names if "/" in n), names[0]).split("/")  # one that shows the difference, if any
        for key, shown in (("full", os.path.join("…", *named, "…")), ("last", os.path.join("…", named[-1], "…")),
                           ("files", os.path.join("…", "…"))):
            ttk.Radiobutton(frame, text=f"{t('path_' + key)}     {shown}", value=key, variable=layout,
                            state="normal" if usable[key] else "disabled").pack(anchor="w")
        notes = [t("last_off", name=clash)] * (not usable["last"]) + [t("files_off")] * (not usable["files"])
        if notes:
            ttk.Label(frame, text="  ".join(notes), foreground="#5f6368", wraplength=wrap).pack(anchor="w", pady=(self.px(4), 0))
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        result = []

        def accept(_=None):
            if not dest.get().strip():
                error.configure(text=t("need_dest"))
                return
            result.append((os.path.normpath(dest.get().strip()), layout.get()))
            dialog.destroy()

        self.dialog_buttons(dialog, frame, accept)
        entry.icursor("end")
        entry.focus_set()  # keys (Return, Escape) reach a dialog only through the focus
        self.show_dialog(dialog)
        if result:
            self.extract_dest, self.extract_layout = result[0]
        return result[0] if result else None

    def verify(self):
        plan = self.targets()
        if not plan:
            messagebox.showinfo(t("verify"), t("nothing_to_verify"), parent=self.root)
            return
        by_pack = {}
        for (pack, name), paths in plan.items():
            by_pack.setdefault(pack, {})[name] = paths
        jobs = []
        for pack, chosen in sorted(by_pack.items()):
            name, paths = next(iter(chosen.items()))
            if paths:  # files of one model
                argv, what = ["verify", pack, name, *[f"--file={p}" for p in sorted(paths)]], f"{name}: {count(len(paths), 'file')}"
            else:
                whole = set(chosen) == set(pack_models(pack))
                what = t("everything", pack=os.path.basename(pack)) if whole else ", ".join(sorted(chosen))
                argv = ["verify", pack, *([] if whole else sorted(chosen))]
            jobs.append({"argv": argv, "title": t("verifying", what=what), "models": [] if paths else argv[2:],
                         "models_at": 2})
        self.start(jobs, lambda: messagebox.showinfo(t("verify"), t("done_verify"), parent=self.root))

    def pack(self):
        place = self.place
        if place.view == "pack" and not place.read_only:  # into this pack folder: from a folder to pick
            source = filedialog.askdirectory(parent=self.root, title=t("pick_source", pack=place.path), mustexist=True)
            if not source:
                return
            source, files, name, here = os.path.normpath(source), [], os.path.basename(os.path.normpath(source)), place.path
        elif place.view == "folder" and place.path is not None:
            rows = self.chosen()
            folders = [r for r in rows if r["kind"] == "folder"]
            if len(folders) == 1 and len(rows) == 1:
                source, files, name = self.full(folders[0]["name"]), [], folders[0]["name"]
            elif rows and all(r["kind"] not in ("folder", "drive", "pack") for r in rows):
                source, files = place.path, sorted(r["name"] for r in rows)
                name = os.path.splitext(files[0])[0] if len(files) == 1 else os.path.basename(place.path)
            else:
                messagebox.showinfo(t("name_title"), t("pack_what"), parent=self.root)
                return
            here = None
        else:
            messagebox.showinfo(t("name_title"), t("pack_here"), parent=self.root)
            return
        found, _ = la.listed_files(source, files) if files else la.source_files(source, [])
        size = sum(os.path.getsize(os.path.join(source, *rel.split("/"))) for rel in found)
        answer = self.ask_pack(name, t("name_what", files=count(len(found), "file"), size=size_text(size), folder=source),
                               source, files, here)
        if not answer:
            return
        name, mode, where, replace = answer
        self.pack_mode = mode if here is None else self.pack_mode
        if mode == "folder":
            self.pack_target = where
        argv = ["pack", source, where, f"--name={name}", *[f"--file={f}" for f in files], *["--replace"] * replace]

        def done():
            if mode == "file" and place.view == "folder" and place.path == os.path.dirname(where):
                self.go(place, os.path.basename(where))
            elif mode == "folder" and place.view == "pack":
                self.go(place, name)
            hint = next((e for e in self.events if e.get("event") == "hint"), None)
            messagebox.showinfo(t("name_title"), t("done_pack", name=name) + (
                t("hint_rebase", models=names_text(hint.get("models", []))) if hint else ""), parent=self.root)

        self.start([{"argv": argv, "title": t("packing", name=name)}], done)

    def ask_pack(self, default, what, source, files, here=None):
        """(model name, "folder" or "file", the pack folder or new pack file) the user enters, checked (also that
        `source` may be packed there), or None. here: the pack folder the list shows; the choice is then this pack
        folder (the default) or a new pack file, a pack of its own."""
        dialog = self.dialog(t("name_title"))
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(WRAP)
        self.heading(frame, what)
        self.path_line(frame, source, pady=(self.px(2), 0))
        ttk.Label(frame, text=t("name_prompt"), wraplength=wrap).pack(anchor="w", pady=(self.px(12), self.px(4)))
        name = tk.StringVar(value=default)
        entry = ttk.Entry(frame, textvariable=name, width=58)
        entry.pack(fill="x")
        ttk.Label(frame, text=t("into"), wraplength=wrap).pack(anchor="w", pady=(self.px(12), self.px(4)))
        mode = tk.StringVar(value="folder" if here else self.pack_mode)
        new_file = os.path.join(source if files else os.path.dirname(source), default.split("/")[-1] + la.OCZIP)
        paths, shown = {"folder": here or self.pack_target, "file": new_file}, [mode.get()]
        target = tk.StringVar(value=paths[mode.get()])

        def switch():  # each choice keeps its own path
            paths[shown[0]], shown[0] = target.get(), mode.get()
            target.set(paths[mode.get()])
            lock()

        for key in ("folder", "file"):
            choice = f"into_{key}" if not here else "into_here" if key == "folder" else "into_file_here"
            ttk.Radiobutton(frame, text=t(choice), value=key, variable=mode, command=switch).pack(anchor="w",
                                                                                               pady=(self.px(2), 0))
            self.note(frame, t(choice + "_note"), padx=(self.px(24), 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(self.px(4), 0))
        target_entry = ttk.Entry(row, textvariable=target, width=50)
        target_entry.pack(side="left", fill="x", expand=True)

        def browse():
            where = target.get().strip()
            if mode.get() == "folder":
                chosen = filedialog.askdirectory(parent=dialog, title=t("pick_archive"), mustexist=False,
                                                 initialdir=where or None)
            else:
                chosen = filedialog.asksaveasfilename(
                    parent=dialog, title=t("pick_file"), defaultextension=la.OCZIP, filetypes=[(la.OCZIP, "*" + la.OCZIP)],
                    initialdir=os.path.dirname(where) or None, initialfile=os.path.basename(where), confirmoverwrite=False)
            if chosen:
                target.set(os.path.normpath(chosen))

        browse_button = ttk.Button(row, text=t("browse"), command=browse)
        browse_button.pack(side="left", padx=(self.px(6), 0))

        def lock():  # the pack folder shown is chosen, not typed
            fixed = here and mode.get() == "folder"
            target_entry.configure(state="readonly" if fixed else "normal")
            browse_button.configure(state="disabled" if fixed else "normal")

        lock()
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        self.note(frame, "\n".join([t("name_hint")] + [t("file_hint_here")] * bool(here)), pady=(self.px(8), 0))
        result = []

        def accept(_=None):
            model, where = name.get().strip(), target.get().strip()
            problem = la.name_problem(model) or (not where and t("need_target"))
            if not problem and mode.get() == "file":
                where = os.path.normpath(where)
                problem = ((not la.is_oczip(where) and t("export_name")) or (os.path.lexists(where) and t("file_exists"))
                           or (la.holds(source, where) and not files and t("file_in_source"))
                           or la.place_problem(os.path.dirname(where)))
            elif not problem:
                where = os.path.normpath(where)
                if la.is_oczip(where):
                    problem = t("target_oczip")
                else:
                    problem = (la.holds(source, where) and not files and f"{source} holds {where}") or la.place_problem(source)
            if problem:
                error.configure(text=sentence(problem))
                return
            held = None
            if mode.get() == "folder" and os.path.isfile(os.path.join(where, la.MARKER)):
                try:
                    held = read_pack(where)[1].get(model)
                except (OSError, ValueError, SystemExit):
                    held = None
                finally:
                    la.close_pack_files()
                if held and not messagebox.askyesno(t("name_title"), t("replace_q", pack=where, name=model,
                                                                       files=count(len(held["files"]), "file")), parent=dialog):
                    return
            result.append((model, mode.get(), where, bool(held) if mode.get() == "folder" else False))
            dialog.destroy()

        self.dialog_buttons(dialog, frame, accept)
        entry.select_range(0, "end")
        entry.focus_set()  # keys (Return, Escape) reach a dialog only through the focus
        self.show_dialog(dialog)
        return result[0] if result else None

    def add_file(self):
        """Add files from disk to the model shown (those whose names the model has already replace them, after
        asking): pack --add, each file under its own name at the top of the model."""
        place = self.place
        if place.view != "model" or place.read_only:
            messagebox.showinfo(t("add_title"), t("read_only_action"), parent=self.root)
            return
        chosen = filedialog.askopenfilenames(parent=self.root, title=t("add_pick", model=place.model))
        paths = [os.path.normpath(c) for c in self.root.tk.splitlist(chosen)] if chosen else []
        if not paths:
            return
        folder, names = os.path.dirname(paths[0]), [os.path.basename(path) for path in paths]
        clash = [n for n in names if n in {r["id"] for r in self.listing}]
        if clash and not messagebox.askyesno(t("add_title"), t("add_q", model=place.model, files=count(len(clash), "file"),
                                                               names=lines(clash)),
                                             parent=self.root):
            return
        argv = ["pack", folder, place.path, f"--name={place.model}", "--add", *[f"--file={n}" for n in names]]

        def done():
            self.go(place, names[0])
            messagebox.showinfo(t("add_title"), t("done_add", files=count(len(names), "file"), name=place.model),
                                parent=self.root)

        self.start([{"argv": argv, "title": t("adding", name=place.model)}], done)

    def export(self):
        """Export the selected models of a pack into a new pack file (all of them: the command names none): ask where,
        and what to do after, then run llm_archive.py export."""
        place, rows = self.place, self.chosen()
        if place.view != "pack" or not rows:
            return
        pack, names = place.path, sorted(r["id"] for r in rows)
        if set(names) == set(pack_models(pack)):
            names = []
        dialog = self.dialog(t("export_title"))
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(WRAP)
        what = ", ".join(names) if names else t("export_all")
        if len(names) == 1:
            self.heading(frame, t("save_one", name=names[0]))
        else:
            self.heading(frame, t("save_some", count=len(names)) if names else t("save_all"))
            if names:
                ttk.Label(frame, text=lines(names)).pack(anchor="w", pady=(self.px(2), 0))
        self.note(frame, t("save_note"), pady=(self.px(2), 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(self.px(10), self.px(4)))
        stem = names[0].split("/")[-1] if len(names) == 1 else os.path.basename(os.path.normpath(pack))
        path = tk.StringVar(value=os.path.join(os.path.dirname(os.path.normpath(pack)), stem + la.OCZIP))
        entry = ttk.Entry(row, textvariable=path, width=56)
        entry.pack(side="left", fill="x", expand=True)

        def browse():
            chosen = filedialog.asksaveasfilename(
                parent=dialog, title=t("export_title"), defaultextension=la.OCZIP, filetypes=[(la.OCZIP, "*" + la.OCZIP)],
                initialdir=os.path.dirname(path.get()), initialfile=os.path.basename(path.get()), confirmoverwrite=False)
            if chosen:
                path.set(os.path.normpath(chosen))

        ttk.Button(row, text=t("browse"), command=browse).pack(side="left", padx=(self.px(6), 0))
        verify, delete = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text=t("export_verify"), variable=verify).pack(anchor="w", pady=(self.px(6), 0))
        ttk.Checkbutton(frame, text=t("export_delete"), variable=delete, command=lambda: delete.get() and verify.set(True),
                        state="disabled" if place.read_only else "normal").pack(anchor="w")
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        self.note(frame, t("export_from_file" if place.read_only else "export_hint"))
        result = []

        def accept(_=None):
            target = os.path.normpath(path.get().strip())
            problem = ((not la.is_oczip(target) and t("export_name")) or la.place_problem(os.path.dirname(target))
                       or (os.path.normcase(target) == os.path.normcase(pack) and t("file_exists")))
            if problem:
                error.configure(text=sentence(problem))
                return
            result.append(target)
            dialog.destroy()

        self.dialog_buttons(dialog, frame, accept)
        entry.icursor("end")
        entry.focus_set()  # keys (Return, Escape) reach a dialog only through the focus
        self.show_dialog(dialog)
        if not result:
            return
        target, overwrite = result[0], False
        if os.path.exists(target):
            if not messagebox.askyesno(t("export_title"), t("export_replace_q", path=short(target)), parent=self.root):
                return
            overwrite = True
        if delete.get() and not messagebox.askyesno(t("export_title"), t(
                "export_delete_q", path=short(target), pack=short(pack), what=what,
                checked=t("checked" if verify.get() else "unchecked")), parent=self.root):
            return
        argv = ["export", pack, target, *names, "--verify" if verify.get() else "--no-verify"]
        argv += ["--delete"] * delete.get() + ["--overwrite"] * overwrite

        def done():
            if delete.get() and not os.path.exists(pack):
                self.go(Place(target, pack=True))
            else:
                self.refresh()
            messagebox.showinfo(t("export_title"), t("done_export_deleted" if delete.get() else "done_export", path=short(target)),
                                parent=self.root)

        self.start([{"argv": argv, "title": t("exporting", name=os.path.basename(target)), "models": names, "models_at": 3}],
                   done)

    def remove(self):
        """Remove the selected models (or files of the model shown) from the pack folder, after saying what that frees
        and what stays because other models need it."""
        place, rows = self.place, self.chosen()
        if place.view == "folder":
            return self.trash()
        if place.read_only:
            messagebox.showinfo(t("remove_title"), t("read_only_action"), parent=self.root)
            return
        if not rows:
            messagebox.showinfo(t("remove_title"), t("remove_what"), parent=self.root)
            return
        la.forget_cached_reads()
        try:
            p, manifests, _ = read_pack(place.path)
            if place.view == "pack":
                names, files = [r["id"] for r in rows], []
                changed = dict.fromkeys(names)
                what = ", ".join(names) if len(names) <= 3 else count(len(names), "model")
            else:
                names, files = [place.model], [r["id"] for r in rows]
                left = [e for e in manifests[place.model]["files"] if e["path"] not in set(files)]
                changed = {place.model: {**manifests[place.model], "files": left} if left else None}
                what = f"{count(len(files), 'file')} of {place.model}"
            freed, kept, users = la.removal(p, manifests, changed)
            freed_size, kept_size = la.blobs_size(p, freed), la.blobs_size(p, kept)
        except (OSError, ValueError, SystemExit) as e:
            messagebox.showerror(t("remove_title"), sentence(e), parent=self.root)
            return
        finally:
            la.close_pack_files()
        needing = sorted({n for us in users.values() for n in us})
        kept_text = t("remove_kept", kept=size_text(kept_size), models=", ".join(needing)) if kept else ""
        if not messagebox.askyesno(t("remove_title"), t("remove_q", what=what, pack=short(place.path), freed=size_text(freed_size),
                                                        kept=kept_text), parent=self.root):
            return
        argv = ["remove", place.path, *names, *[f"--file={f}" for f in files]]

        def done():
            if place.view == "model" and changed[place.model] is None:
                self.go(place.parent())
            messagebox.showinfo(t("remove_title"), t("done_remove", what=what), parent=self.root)

        self.start([{"argv": argv, "title": t("removing", what=what), "models": names, "models_at": 2}], done)

    def trash(self):
        """Move the selected files and folders of a folder on disk to the Recycle Bin, after asking. Nothing is ever
        deleted for good: what the bin cannot take is refused (to_trash). Never inside a pack folder, which is
        changed only through the tool."""
        place, rows = self.place, self.chosen()
        if self.inside_pack:
            messagebox.showerror(t("remove_title"), t("trash_inside", pack=short(la.pack_folder_of(place.path))), parent=self.root)
            return
        if place.path is None or not rows:
            messagebox.showinfo(t("remove_title"), t("trash_what"), parent=self.root)
            return
        paths = [self.full(r["name"]) for r in rows]
        size = sum(tree_size(path) for path in paths)
        packs = [r for r in rows if r["kind"] in ("pack", "oczip")]
        what = short(paths[0]) if len(paths) == 1 else count(len(paths), "item")
        note = t("trash_packs", packs=count(len(packs), t("pack_kind"))) if packs else ""
        if not messagebox.askyesno(t("remove_title"), t("trash_q", what=what, size=size_text(size), packs=note),
                                   parent=self.root):
            return
        problem = to_trash(paths, size)
        if problem:
            messagebox.showerror(t("remove_title"), t("trash_failed", problem=problem), parent=self.root)
        self.refresh()

    def ask_text(self, title, prompt, value, check):
        """A line of text the user enters, starting as `value`; check(text) -> a problem shown under it, or None.
        None if cancelled or left unchanged."""
        dialog = self.dialog(title)
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(WRAP)
        self.heading(frame, prompt)
        text = tk.StringVar(value=value)
        entry = ttk.Entry(frame, textvariable=text, width=52)
        entry.pack(fill="x", pady=(self.px(6), 0))
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        result = []

        def accept(_=None):
            new = text.get().strip()
            problem = check(new) if new != value else None
            if problem:
                error.configure(text=sentence(problem))
                return
            if new != value:
                result.append(new)
            dialog.destroy()

        self.dialog_buttons(dialog, frame, accept)
        entry.select_range(0, "end")
        entry.focus_set()
        self.show_dialog(dialog)
        return result[0] if result else None

    def rebase(self):
        """Rebase the selected models, or the selected files of the model shown: Find better bases (rebase, which
        changes a file only when the pack gets smaller, and lets go of a base kept for removed models when that saves
        room; those whose bases were set by hand are left as they are unless ticked) or Make independent (rebase
        --no-base: the choice is then the model's, or the files')."""
        place, rows = self.place, self.chosen()
        if place.view not in ("pack", "model") or place.read_only or not rows:
            return
        names = sorted(r["id"] for r in rows)
        files = place.view == "model"
        choice_of = (lambda r: r["file"].get("base_choice", "auto")) if files else (lambda r: r.get("summary", {}).get("base_choice", "auto"))
        fixed = [r["id"] for r in rows if choice_of(r) != "auto"]
        dialog = self.dialog(t("rebase_title"))
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        if len(names) == 1:
            self.heading(frame, t("rebase_file_one", name=names[0], model=place.model) if files else t("rebase_one", name=names[0]))
        else:
            self.heading(frame, t("rebase_file_some", count=len(names), model=place.model) if files
                         else t("rebase_some", count=len(names)))
            ttk.Label(frame, text=lines(names)).pack(anchor="w", pady=(self.px(2), 0))
        mode, also = tk.StringVar(value="find"), tk.BooleanVar(value=len(fixed) == len(names))
        for key in ("find", "alone"):
            ttk.Radiobutton(frame, text=t(f"rebase_{key}"), value=key, variable=mode).pack(
                anchor="w", pady=(self.px(8) if key == "find" else self.px(4), 0))
            self.note(frame, t(f"rebase_{key}_note"), padx=(self.px(24), 0))
            if key == "find" and fixed:  # (otherwise left as they are, as rebase --all does)
                ttk.Checkbutton(frame, text=t("rebase_also", names=names_text(fixed)), variable=also).pack(
                    anchor="w", padx=(self.px(24), 0), pady=(self.px(2), 0))
        self.note(frame, t("rebase_hint"), pady=(self.px(8), 0))
        chosen = []

        def accept(_=None):
            chosen.append(mode.get())
            dialog.destroy()

        self.dialog_buttons(dialog, frame, accept)
        self.show_dialog(dialog)
        if not chosen:
            return
        todo = names if chosen[0] == "alone" or also.get() else [n for n in names if n not in fixed]
        if not todo:
            return
        alone = ["--no-base"] if chosen[0] == "alone" else []
        if files:  # the files named: a choice made is theirs
            argv = ["rebase", place.path, place.model, *[f"--file={n}" for n in todo], *alone]
            what = f"{todo[0]} of {place.model}" if len(todo) == 1 else f"{count(len(todo), 'file')} of {place.model}"
        else:
            argv = ["rebase", place.path, *todo, *alone]
            what = todo[0] if len(todo) == 1 else count(len(todo), "model")

        def done():
            result = next((e.get("text", "") for e in reversed(self.events) if e.get("event") == "summary"), "")
            messagebox.showinfo(t("rebase_title"), t("done_rebase", what=what, result=sentence(result)), parent=self.root)

        self.start([{"argv": argv, "title": t("rebasing", what=what), "models": [] if files else todo, "models_at": 2}], done)

    def rename(self):
        """Rename the selected model (in a pack) or file (in a model)."""
        place, rows = self.place, self.chosen()
        if place.read_only:
            messagebox.showinfo(t("rename_title"), t("read_only_action"), parent=self.root)
            return
        if len(rows) != 1:
            messagebox.showinfo(t("rename_title"), t("rename_file_what" if place.view == "model" else "rename_what"),
                                parent=self.root)
            return
        old = rows[0]["id"]
        if place.view == "model":
            have = {r["id"] for r in self.listing}
            check = lambda new: la.path_problem(new) or (new in have and t("rename_file_taken", model=place.model, path=new))  # noqa: E731
            new = self.ask_text(t("rename_title"), t("rename_file_prompt", model=place.model, path=old), old, check)
            argv = ["rename", place.path, place.model, "--file", old, new]
            shown = f"{place.model}:{old}"
        else:
            new = self.ask_text(t("rename_title"), t("rename_prompt", name=old), old, la.name_problem)
            argv = ["rename", place.path, old, new]
            shown = old
        if new is None:
            return

        def done():
            self.go(place, new)
            messagebox.showinfo(t("rename_title"), t("done_rename", old=shown, new=new), parent=self.root)

        self.start([{"argv": argv, "title": t("renaming", name=shown)}], done)

    # ------------------------------------------------------------------------------------------------- dialogs

    def dialog(self, title):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        return dialog

    def dialog_buttons(self, dialog, frame, accept):
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(self.px(14), 0))
        ttk.Button(buttons, text=t("ok"), command=accept, default="active").pack(side="left", padx=(0, self.px(6)))
        ttk.Button(buttons, text=t("cancel"), command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _: dialog.destroy())

    def heading(self, frame, text):
        """A dialog's first line, what it is about: bold."""
        ttk.Label(frame, text=text, font=self.bold, wraplength=self.px(WRAP)).pack(anchor="w")

    def note(self, frame, text, **pack):
        """A hint in a dialog: gray."""
        label = ttk.Label(frame, text=text, foreground="#5f6368", wraplength=self.px(WRAP))
        label.pack(anchor="w", **pack)
        return label

    def path_line(self, frame, path, **pack):
        """A path on a line of its own, folders from its middle left out to fit; the whole of it on hover."""
        font = tkfont.nametofont("TkDefaultFont")
        shown = shorten(path, lambda text: font.measure(text) <= self.px(WRAP))
        label = ttk.Label(frame, text=shown)
        label.pack(anchor="w", **pack)
        if shown != path:
            label.bind("<Enter>", lambda e: self.show_tip(path, e.x_root + self.px(12), e.y_root + self.px(18)))
            label.bind("<Leave>", self.hide_tip)

    def show_dialog(self, dialog):
        """Center a dialog over the window and wait, modal, until it is closed."""
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dialog.wait_visibility()  # a grab needs a window that is shown (X11)
        dialog.grab_set()
        self.root.wait_window(dialog)
        self.hide_tip()

    # ---------------------------------------------------------------------------------------- running the tool

    def start(self, jobs, done):
        """Run llm_archive.py once per job, one after another, showing what it prints; stop at the first failure.
        `done` is called once all have succeeded."""
        self.jobs, self.done = list(jobs), done
        self.job_number, self.job_total = 0, len(self.jobs)
        for button in self.buttons.values():
            button.configure(state="disabled")
        self.show_busy()
        self.next_job()
        self.spin(0)

    def show_busy(self):
        """A window in front while llm_archive.py runs, holding all input (the main window waits): what runs, the last
        line it printed, how far the current step is (when the tool can tell), how long it has run, and Cancel.
        Closing it is Cancel."""
        busy = self.busy = tk.Toplevel(self.root)
        busy.title("OverCompressing")
        busy.transient(self.root)
        busy.resizable(False, False)
        busy.protocol("WM_DELETE_WINDOW", self.cancel)
        frame = ttk.Frame(busy, padding=self.px(18))
        frame.pack(fill="both", expand=True)
        self.busy_title = ttk.Label(frame, font=self.bold, width=64, anchor="w")
        self.busy_title.pack(fill="x")
        self.busy_line = ttk.Label(frame, foreground="#5f6368", width=78, anchor="w")
        self.busy_line.pack(fill="x", pady=(self.px(8), 0))
        self.busy_step = ttk.Label(frame, foreground="#5f6368", width=78, anchor="w")
        self.busy_step.pack(fill="x", pady=(self.px(4), 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(self.px(16), 0))
        self.spinner = ttk.Label(row, anchor="w")  # turns while something runs: activity; how far it is, in words
        self.spinner.pack(side="left")
        self.cancel_button = ttk.Button(row, text=t("cancel"), command=self.cancel)
        self.cancel_button.pack(side="right")
        self.busy_since, self.progress = time.monotonic(), None
        busy.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - busy.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - busy.winfo_reqheight()) // 3
        busy.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        busy.wait_visibility()  # a grab needs a window that is shown (X11)
        busy.grab_set()
        busy.focus_set()

    def front(self):
        """The window a message goes over: the one in front while something runs, else the main window."""
        return self.busy if self.busy is not None else self.root

    def spin(self, step):
        """Turn the symbol in the window in front, beside the time run so far and how far the step is."""
        if self.busy is None:
            return
        seconds = int(time.monotonic() - self.busy_since)
        text = f"{SPINNER[step % len(SPINNER)]}   {seconds // 60}:{seconds % 60:02d}"
        progress = self.progress
        if progress and progress.get("total"):
            done, total = progress.get("done", 0), progress["total"]
            text += f"   ·   {min(done / total, 1):.0%}   ·   {size_text(min(done, total))} / {size_text(total)}"
        self.spinner.configure(text=text)
        self.root.after(150, self.spin, step + 1)

    def next_job(self):
        if not self.jobs:
            self.finish(True)
            return
        self.job, self.output, self.job_damage, self.events = self.jobs.pop(0), [], None, []
        self.job_number += 1
        counted = f"  ({self.job_number}/{self.job_total})" if self.job_total > 1 else ""
        self.busy_title.configure(text=self.job["title"] + counted)
        self.busy_line.configure(text="")
        self.busy_step.configure(text="")
        self.progress = None
        argv, listed = self.command(self.job)
        shown = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        self.write(f"\n> llm_archive.py {shown}" + (t("listed", count=listed) if listed else "") + "\n", "command")
        self.status.configure(text=self.job["title"] + " …")
        # below normal priority: a job uses every core the user leaves idle, and gives way when the user needs them
        where = (dict(creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt"
                 else dict(start_new_session=True))
        self.proc = subprocess.Popen([python_exe(), "-I", "-X", "utf8", "-u", "-c", RUNNER, HERE, *argv, "--json", "--progress"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", **where)
        threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()

    def command(self, job):
        """A job's command line, and how many names went into list files: when the line would pass ARGS_LIMIT, its
        models (job["models"], at job["models_at"]) and files (--file=) go into list files of their own, deleted when
        the job ends, unless a name holds a line break."""
        argv, models, at = job["argv"], job.get("models", []), job.get("models_at", 0)
        if len(subprocess.list2cmdline(argv)) <= ARGS_LIMIT:
            return argv, 0
        if argv[at:at + len(models)] != models:
            models = []
        rest = argv[:at] + argv[at + len(models):] if models else list(argv)
        files = [a[len("--file="):] for a in rest if a.startswith("--file=")]
        if any("\n" in name or "\r" in name for name in models + files):
            return argv, 0
        rest = [a for a in rest if not a.startswith("--file=")]
        for option, names in (("--models-from", models), ("--files-from", files)):
            if names:
                fd, path = tempfile.mkstemp(prefix="oc-list-", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                    f.write("".join(name + "\n" for name in names))
                self.list_files.append(path)
                rest += [option, path]
        return rest, len(models) + len(files)

    def read(self, proc):
        for line in proc.stdout:
            self.lines.put(line)
        proc.wait()
        self.lines.put(None)

    def poll(self):
        try:
            while True:
                line = self.lines.get_nowait()
                if line is None:
                    self.job_ended()
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    event = None
                if not isinstance(event, dict) or "event" not in event:
                    event = {"event": "note", "text": line.rstrip("\n")}
                if event["event"] == "damage":
                    self.job_damage = event
                if event["event"] in ("hint", "summary"):
                    self.events.append(event)
                if event["event"] == "progress":
                    self.progress = event
                    step = event.get("step") or ""
                    if event.get("item"):
                        step += f"  ·  {event['item']}"
                    self.busy_step.configure(text=step if len(step) <= 90 else step[:89] + "…")
                    continue
                text = str(event.get("text", ""))
                self.output.append(text + "\n")
                self.write(text + "\n")
                if text.strip() and not self.cancelling:
                    shown = text.strip().splitlines()[0]
                    self.status.configure(text=f"{self.job['title']}:  {shown}")
                    self.busy_line.configure(text=shown if len(shown) <= 90 else shown[:89] + "…")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def job_ended(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        while self.list_files:
            try:
                os.remove(self.list_files.pop())
            except OSError:
                pass
        code, self.proc = self.proc.returncode, None
        output = "".join(self.output)
        if self.cancelling:
            self.write(t("cancelled") + "\n")
            self.finish(False)
            return
        if code != 0:
            retry = self.job.get("retry") and self.job["retry"](self.job, output)
            if not retry:
                damage = self.job_damage
                if damage:
                    self.remember(damage)
                self.finish(False)
                if damage:
                    self.show_damage(damage)
                    return
                last = next((line for line in reversed(output.strip().splitlines()) if line.strip()), f"exit code {code}")
                messagebox.showerror(t("failed"), sentence(last) + "\n\n" + t("see_log"), parent=self.root)
                return
            self.jobs.insert(0, {**self.job, "argv": retry})
            self.job_number -= 1  # the same job, once more
        self.next_job()

    def remember(self, damage):
        """Keep what a verify found lost, for the ⚠ marks (mark) and for cleaning up (clean_up)."""
        lost = {(item["model"], item["path"]): item["own"] for item in damage["lost"]}
        models = {}
        for model, _ in lost:
            models[model] = models.get(model, 0) + 1
        self.marks = {"pack": os.path.normcase(os.path.abspath(damage["pack"])), "stamp": pack_stamp(damage["pack"]),
                      "lost": lost, "models": models}

    def show_damage(self, damage):
        """The damage report of a verify that failed, to read and copy; for a pack folder, Clean up (clean_up)."""
        dialog = self.dialog(t("damage_title"))
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        lost = len(damage["lost"])
        self.heading(frame, t("damage_heading", files=count(lost, "file")) if lost else t("damage_heading_other"))
        self.note(frame, t("damage_note"), pady=(self.px(2), self.px(8)))
        box = ttk.Frame(frame)
        box.pack(fill="both", expand=True)
        text = tk.Text(box, width=100, height=22, wrap="none", font=self.mono, relief="flat", padx=self.px(8),
                       pady=self.px(6), bg="#1c2130", fg="#d7dbe3", selectbackground="#3b4a66", insertbackground="#d7dbe3")
        text.insert("1.0", damage["text"])
        text.configure(state="disabled")
        down = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        across = ttk.Scrollbar(box, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=down.set, xscrollcommand=across.set)
        text.grid(row=0, column=0, sticky="nsew")
        down.grid(row=0, column=1, sticky="ns")
        across.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        pack = damage["pack"]
        writable = not la.is_oczip(pack)
        if not writable:
            self.note(frame, t("clean_read_only"), pady=(self.px(8), 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(self.px(12), 0))

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(damage["text"])
            copy_button.configure(text=t("copied"))

        def clean():
            dialog.destroy()
            self.clean_up(damage)

        copy_button = ttk.Button(buttons, text=t("copy"), command=copy)
        copy_button.pack(side="left")
        ttk.Button(buttons, text=t("close"), command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text=t("clean_up"), command=clean,
                   state="normal" if writable and lost else "disabled").pack(side="right", padx=(0, self.px(6)))
        dialog.resizable(True, True)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        self.show_dialog(dialog)

    def clean_up(self, damage):
        """Remove what a verify found lost, at the level the user types: ERASE (the lost files; models left with none
        go), ERASE MOD (the models with lost files, whole), ERASE FULL (those, and the models that need data of them),
        each with what it removes and its risk; only while the pack is as it was verified."""
        pack, lost = damage["pack"], damage["lost"]
        by_model = {}
        for item in lost:
            by_model.setdefault(item["model"], []).append(item)
        files_of = {m["name"]: m["files"] for m in damage["models"]}
        emptied = sorted(n for n, items in by_model.items() if len(items) >= files_of.get(n, len(items) + 1))
        damaged, affected = sorted(by_model), list(damage["affected"])
        jobs = {"ERASE": [], "ERASE MOD": [{"argv": ["remove", pack, *damaged], "models": damaged, "models_at": 2}],
                "ERASE FULL": [{"argv": ["remove", pack, *damaged, *affected], "models": damaged + affected, "models_at": 2}]}
        for name in sorted(by_model, key=lambda n: (any(i["own"] for i in by_model[n]), n)):  # what depends first
            if name in emptied:
                jobs["ERASE"].append({"argv": ["remove", pack, name], "models": [name], "models_at": 2})
            else:
                jobs["ERASE"].append({"argv": ["remove", pack, name, *[f"--file={i['path']}" for i in by_model[name]]]})
        for level in jobs.values():
            for job in level:
                job["title"] = t("cleaning", name=os.path.basename(pack))
        many = len(emptied) != 1
        levels = [
            ("ERASE", t("erase", files=count(len(lost), "file"), models=count(len(by_model), "model"),
                        emptied=t("erase_emptied", names=names_text(emptied), verb="are" if many else "is") if emptied
                        else ""), t("erase_risk")),
            ("ERASE MOD", t("erase_mod", names=names_text(damaged)), t("erase_mod_risk")),
            ("ERASE FULL", t("erase_full", names=names_text(damaged + affected)) if affected
             else t("erase_full_same", names=names_text(damaged)), t("erase_full_risk" if affected else "erase_full_same_risk")),
        ]
        dialog = self.dialog(t("clean_title"))
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        self.heading(frame, t("clean_heading", pack=os.path.basename(pack)))
        self.path_line(frame, pack, pady=(self.px(2), self.px(10)))
        grid = ttk.Frame(frame)
        grid.pack(fill="x")
        for row, (level, what, risk) in enumerate(levels):
            ttk.Label(grid, text=level, font=self.bold).grid(row=2 * row, column=0, sticky="nw", padx=(0, self.px(14)),
                                                            pady=(self.px(6) if row else 0, 0))
            ttk.Label(grid, text=what, wraplength=self.px(WRAP)).grid(row=2 * row, column=1, sticky="w",
                                                                       pady=(self.px(6) if row else 0, 0))
            ttk.Label(grid, text=risk, foreground="#5f6368", wraplength=self.px(WRAP)).grid(row=2 * row + 1, column=1,
                                                                                            sticky="w")
        ttk.Label(frame, text=t("clean_prompt")).pack(anchor="w", pady=(self.px(14), self.px(4)))
        typed = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=typed, width=24)
        entry.pack(anchor="w")
        error = ttk.Label(frame, foreground="#b3261e", wraplength=self.px(WRAP))
        error.pack(anchor="w", pady=(self.px(4), 0))
        chosen = []

        def accept(_=None):
            level = typed.get().strip()
            if level not in jobs:
                error.configure(text=t("clean_typed"))
                return
            if pack_stamp(pack) != (self.marks or {}).get("stamp") or pack_stamp(pack) is None:
                error.configure(text=t("clean_changed"))
                return
            chosen.append(level)
            dialog.destroy()

        self.dialog_buttons(dialog, frame, accept)
        entry.focus_set()
        self.show_dialog(dialog)
        if chosen:
            self.start(jobs[chosen[0]], lambda: messagebox.showinfo(t("clean_title"), t("done_clean"), parent=self.root))

    def finish(self, ok):
        self.jobs, self.cancelling = [], False
        if self.busy is not None:
            self.busy.grab_release()
            self.busy.destroy()
            self.busy = None
        if self.quit_after:
            self.root.destroy()
            return
        self.refresh()  # also turns the buttons back on
        if ok:
            self.done()

    def cancel(self):
        if not self.proc:
            return
        if not self.cancelling:
            self.cancelling = True
            self.jobs.clear()
            self.status.configure(text=t("cancelling"))
            self.busy_line.configure(text=t("cancelling"))
            self.cancel_button.configure(text=t("stop_now"))
            try:
                self.proc.stdin.close()  # the runner stops as on Ctrl+C
            except OSError:
                pass
        elif messagebox.askyesno(t("stop_now"), t("force_q"), parent=self.front()) and self.proc:
            if os.name == "nt":  # with its worker processes
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass

    def close(self):
        if not self.proc:
            self.root.destroy()
        elif messagebox.askyesno("OverCompressing", t("quit_q"), parent=self.front()):
            self.quit_after = True
            self.cancel()


def start_place():
    """Where the list starts: the folder or pack given, else the pack this file is in when it runs from an offline
    kit (<pack folder>/_kit/tools), else the current folder."""
    if len(sys.argv) > 1:
        return place_from_text(sys.argv[1])
    kit = os.path.dirname(HERE)
    if os.path.basename(kit) == "_kit":
        return Place(os.path.dirname(kit), pack=is_pack(os.path.dirname(kit)))
    return None


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root, start_place())
    root.mainloop()


if __name__ == "__main__":
    main()
