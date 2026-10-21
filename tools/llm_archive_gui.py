"""A small file browser for llm_archive.py, laid out like other archivers' windows. Folders and files are listed as
they are on disk; a model folder (one with a manifest.json) is recognized when you go into it, and an .oczip opens
like a folder. Select what to pack, extract, verify or bundle: each action runs llm_archive.py in a process of its
own, just as on the command line (delta bases are chosen automatically); rebase, --base and the other options are
there.

    python -I llm_archive_gui.py [<folder> | <file.oczip>]

Run from an offline kit (<archive_root>/_kit/tools), it opens that archive. Its text is Unicode symbols where they
say it, else English: nothing depends on the system's language.
"""

import os
import queue
import shlex
import signal
import string
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import llm_archive as la  # noqa: E402

# Runs llm_archive.py in the child process. When its stdin closes (Cancel, or this window going away), it stops as on
# Ctrl+C, so a pack or restore still removes what it had begun. On Windows the pipe is polled, not read: a read left
# pending on it would stall every process started meanwhile (the ans16 workers) as it starts.
RUNNER = """import sys, threading, time, _thread
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
        sys.stdin.read()
    _thread.interrupt_main()
threading.Thread(target=cancel_on_eof, daemon=True).start()
sys.argv = ["llm_archive.py", *sys.argv[2:]]
try:
    la.main()
except KeyboardInterrupt:
    sys.exit("cancelled")
"""

WEIGHT_SUFFIXES = (".safetensors", ".gguf", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".h5", ".msgpack")
COLORS = {"folder": "#fff3c4", "drive": "#fff3c4", "weights": "#fde0dc", "ocz": "#dcf1e2", "oczip": "#d9e8fb",
          "manifest": "#e2e2e2"}  # row backgrounds by kind; other files keep the list's own
LEGEND = (("folder", "Folder"), ("weights", "Weights"), ("ocz", ".ocz"), ("oczip", ".oczip"), ("manifest", "manifest.json"))
SPINNER = "◐◓◑◒"

TEXT = {  # all of the window's text: symbols (Unicode BMP, which every Tk renders) where they say it, else English
    "up": "↑",
    "drives": "Drives",
    "pack": "+ Pack…",
    "bundle": "▣ Bundle…",
    "extract": "⇩ Extract…",
    "verify": "✔ Verify",
    "refresh": "↻ Refresh",
    "cancel": "✕ Cancel",
    "stop_now": "✕ Stop now",
    "ok": "OK",
    "browse": "Browse…",
    "col_pack": "In Pack",
    "in_pack": "✔",
    "col_name": "Name",
    "col_size": "Size",
    "col_ratio": "Ratio",
    "col_type": "Type",
    "col_modified": "Modified",
    "type_folder": "Folder",
    "type_drive": "Drive",
    "type_file": "File",
    "type_weights": "Weights",
    "type_manifest": "Manifest",
    "type_oczip": "OCZ pack file",
    "type_ocz": "ocz",
    "type_same": "= {ref}",
    "type_delta": "Δ {base}",
    "type_zip": {zipfile.ZIP_STORED: "Store", zipfile.ZIP_DEFLATED: "Deflate"},
    "items": "{items}",
    "model_here": "{repo} · {files} · {size} → {stored} ({ratio})",
    "bad_manifest": "⚠ the manifest.json here cannot be used: {problem}",
    "model_needs": "needs {names} (in the same folder)",
    "bad_dest": "Extract somewhere else:\n{problems}",
    "bad_source": "Cannot pack {source}: {problem}",
    "cannot_list": "⚠ cannot list it: {problem}",
    "no_place": "{text} is not a folder or a pack file (.oczip).",
    "read_only": "read-only pack file",
    "nothing_to_extract": "Nothing to extract in what is selected: select model folders, .ocz files or other files of "
                          "a model, or a pack file (.oczip).",
    "nothing_to_verify": "Nothing to verify here: select model folders, a pack folder or a pack file, or go into one.",
    "pack_what": "Select one folder, or files of this folder, to pack.",
    "pack_here": "This is inside a pack: go to the folder or files you want to pack.",
    "bundle_what_first": "Select a pack folder (one holding model folders), or go into one, to bundle it.",
    "pick_dest": "Extract into",
    "extract_title": "⇩ Extract",
    "dest_prompt": "Into:",
    "layout": "Folders:",
    "path_full": "Full path",
    "path_last": "Last level",
    "path_files": "Files only",
    "files_off": "Files only: one model at a time.",
    "last_off": "Last level: two of these models end in {name}.",
    "need_dest": "Choose where to extract.",
    "whole_model": "{repo} (the whole model)",
    "some_files": "{repo}: {files}",
    "pick_archive": "Pack folder to pack into (an empty one starts a new pack)",
    "name_title": "Pack",
    "name_what": "{files} · {size} · {folder}",
    "name_prompt": "Name in the pack (e.g. Qwen/Qwen2.5-7B):",
    "into": "Pack into:",
    "into_folder": "a pack folder, for upkeep: models side by side, each a Δ delta against the others where smaller",
    "into_file": "a new pack file (.oczip), for cold storage: this model alone, bundled and verified",
    "pick_file": "New pack file",
    "file_exists": "That pack file exists already: a pack file is written whole, never added to. Choose a new name, "
                   "or pack into a pack folder.",
    "file_in_source": "The pack file would be inside the folder being packed: write it somewhere else.",
    "need_target": "Choose where to pack into.",
    "target_oczip": "An .oczip is a pack file: choose \"a new pack file\" to make one, or give a pack folder.",
    "name_hint": "Δ bases are chosen automatically; for other options use llm_archive.py (docs/usage.md).",
    "replace_q": "{pack} already holds {name} ({files}). Replace it with these files?",
    "bundle_title": "Bundle into a pack file",
    "bundle_what": "All of the pack folder {pack} (models, _kit and anything else in it) into one pack file (.oczip), "
                   "for cold storage:",
    "bundle_verify": "✔ Verify it: read every file back, verify every model",
    "bundle_delete": "Then delete the bundled files from the folder (✔ Verify comes on with it)",
    "bundle_hint": "✔ Verify and ⇩ Extract read a pack file as it is. It is read-only: to pack into it again, extract it "
                   "into a pack folder (it is a ZIP).",
    "bundle_name": "The file name must end with .oczip.",
    "bundle_replace_q": "{path} exists already. Replace it?",
    "bundle_delete_q": "Once {path} is written{checked}, the files of {pack} it holds are deleted. Go on?",
    "checked": " and verified",
    "unchecked": " (without verifying it)",
    "packing": "▶ Packing {name}",
    "bundling": "▶ Bundling into {name}",
    "extracting": "▶ Extracting {name}",
    "verifying": "▶ Verifying {what}",
    "everything": "everything in {pack}",
    "cancelling": "■ Cancelling: finishing the current step…",
    "force_q": "It is still finishing the current step. Stop it right away? Unfinished temporary files (.oc-*.part) "
               "may be left behind; they can simply be deleted.",
    "quit_q": "Something is still running. Cancel it and quit?",
    "done_pack": "✔ Packed {name}.",
    "done_bundle": "✔ Bundled into {path}.",
    "done_bundle_deleted": "✔ Bundled into {path}, and the folder's files deleted: the pack file is open now.",
    "done_extract_q": "✔ Extracted into {dest}. Open that folder?",
    "done_verify": "✔ Everything checked is intact.",
    "overwrite_q": "{dest} already holds some of these files with other contents. Replace them?",
    "failed": "✕ Failed",
    "cancelled": "✕ Cancelled.",
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
    return f"{part / whole * 100:.1f}%" if whole else ""


def extract_target(dest, repo, layout):
    """Where restore puts a model's files: dest/<its full name> ("full"), dest/<the last part of it> ("last"), or dest
    itself ("files", with --flat: no folders at all)."""
    if layout == "full":
        return os.path.join(dest, *repo.split("/"))
    return os.path.join(dest, repo.split("/")[-1]) if layout == "last" else dest


def open_folder(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
    except OSError:
        pass


def kind_of(name):
    """How a file is shown, by its name: manifest, oczip, ocz, weights (not compressed) or file."""
    low = name.lower()
    if low == "manifest.json":
        return "manifest"
    if low.endswith(la.OCZIP):
        return "oczip"
    if low.endswith(".ocz"):
        return "ocz"
    return "weights" if low.endswith(WEIGHT_SUFFIXES) else "file"


# ----------------------------------------------------------------------------------------------- places and models


class Place:
    """What the list shows: the drives (Windows; path None), a folder, or a folder inside an .oczip (inner: "" for
    its top, else a "/"-separated path in it)."""

    def __init__(self, path=None, inner=None):
        self.path, self.inner = path, inner

    @property
    def in_zip(self):
        return self.inner is not None

    def text(self):
        if self.path is None:
            return t("drives")
        return os.path.join(self.path, *self.inner.split("/")) if self.inner else self.path

    def parent(self):
        if self.path is None:
            return None
        if self.in_zip:
            return Place(self.path, self.inner.rpartition("/")[0]) if self.inner else Place(os.path.dirname(self.path))
        up = os.path.dirname(self.path)
        if up != self.path:
            return Place(up)
        return Place() if os.name == "nt" else None

    def child(self, name):
        """The folder, drive or .oczip `name` in this place."""
        if self.path is None:
            return Place(name)
        if self.in_zip:
            return Place(self.path, f"{self.inner}/{name}" if self.inner else name)
        full = os.path.join(self.path, name)
        return Place(full, "") if la.is_oczip(full) and os.path.isfile(full) else Place(full)


def place_from_text(text):
    """The place a typed path names (a folder, an .oczip or a folder inside one), or None."""
    text = text.strip().strip('"')
    if not text:
        return None
    if os.name == "nt" and len(text) == 2 and text[1] == ":":
        text += "\\"
    path = os.path.normpath(os.path.expanduser(text))
    if os.path.isdir(path):
        return Place(path)
    probe, inner = path, []
    while True:
        if os.path.isfile(probe) and la.is_oczip(probe):
            return Place(probe, "/".join(reversed(inner)))
        up = os.path.dirname(probe)
        if up == probe:
            return None
        inner.append(os.path.basename(probe))
        probe = up


class Model:
    """The model whose folder the list is in (or below): its archive (a folder or an .oczip), folder, the path below
    the folder (sub), its manifest, and its entries by stored name."""

    def __init__(self, root, folder, sub, manifest):
        self.root, self.folder, self.sub, self.manifest = root, folder, sub, manifest
        self.repo = manifest["repo"]
        self.by_stored = {e["stored"]: e for e in manifest["files"] if e.get("stored")}


def zip_has(path, name):
    try:
        la.oczip(path).getinfo(name)
        return True
    except KeyError:
        return False


def find_model(place):
    """(the Model whose folder `place` is in or below, or None; why a manifest.json right here cannot be used)."""
    if place.path is None or (place.in_zip and not place.inner):
        return None, None
    problem = None
    if place.in_zip:
        folder, _, sub = place.inner.partition("/")
        if not zip_has(place.path, f"{folder}/manifest.json"):
            return None, None
        root = place.path
    else:
        if os.path.isfile(os.path.join(place.path, "manifest.json")):  # say why one right here is not a model's
            try:
                la.model_manifest(os.path.dirname(place.path), os.path.basename(place.path))
            except (OSError, ValueError) as e:
                problem = str(e)
        found = la.model_folder_of(place.path)
        if not found:
            return None, problem
        root, folder = found
        sub = os.path.relpath(os.path.abspath(place.path), os.path.join(root, folder)).replace(os.sep, "/")
        sub = "" if sub == "." else sub
    try:
        return Model(root, folder, sub, la.model_manifest(root, folder)), problem
    except (OSError, ValueError) as e:
        return None, str(e)


def describe(entry):
    """The Type of a file of a model, from its manifest entry."""
    if entry["method"] == "same":
        return t("type_same", ref=f"{entry['same_as']['repo']}:{entry['same_as']['path']}")
    if entry.get("delta_base"):
        return t("type_delta", base=entry["delta_base"])
    return entry["method"]


def list_place(place, model):
    """The rows of a place: dicts with pack (✔: part of a model, by its manifest, or anything in an .oczip), name,
    kind, size, mtime, ratio, type; files of a model also their entry."""
    rows, mark = [], t("in_pack")
    if place.path is None:
        for letter in string.ascii_uppercase:
            if os.path.exists(f"{letter}:\\"):
                rows.append(dict(pack="", name=f"{letter}:\\", kind="drive", size=None, mtime=None, ratio="",
                                 type=t("type_drive")))
        return rows
    if place.in_zip:
        prefix, folders = f"{place.inner}/" if place.inner else "", set()
        for info in la.oczip(place.path).infolist():
            rest = info.filename[len(prefix):] if info.filename.startswith(prefix) else ""
            if "/" in rest:
                folders.add(rest.split("/")[0])
            elif rest:
                rows.append(dict(pack=mark, name=rest, kind=kind_of(rest), size=info.file_size, ratio="",
                                 mtime=time.mktime((*info.date_time, 0, 0, -1)),
                                 type=TEXT["type_zip"].get(info.compress_type, f"method {info.compress_type}")))
        rows += [dict(pack=mark, name=f, kind="folder", size=None, mtime=None, ratio="", type=t("type_folder"))
                 for f in folders]
    else:
        with os.scandir(place.path) as found:
            for e in found:
                try:
                    folder, st = e.is_dir(), e.stat()
                except OSError:
                    continue
                kind = "folder" if folder else kind_of(e.name)
                packed = folder and os.path.isfile(os.path.join(e.path, "manifest.json"))  # a model folder
                rows.append(dict(pack=mark if packed else "", name=e.name, kind=kind, size=None if folder else st.st_size,
                                 mtime=st.st_mtime, ratio="", type=t(f"type_{kind}")))
    if model:
        lying = {e.get("stored") or la.portable_path(e["path"]) for e in model.manifest["files"]}  # where its files are
        for row in rows:
            rel = f"{model.sub}/{row['name']}" if model.sub else row["name"]
            entry = model.by_stored.get(rel)
            if row["kind"] == "folder":
                if any(p.startswith(rel + "/") for p in lying):
                    row["pack"] = mark
            elif entry:
                row.update(entry=entry, pack=mark)
                if entry["method"] != "raw":
                    row.update(kind="ocz", type=describe(entry), ratio=percent(entry["stored_size"], entry["size"]))
            elif rel == "manifest.json":
                row["pack"] = mark
        for entry in model.manifest["files"]:  # same-as references: no file of their own, listed where theirs would be
            folder, _, name = entry["path"].rpartition("/")
            if entry["method"] == "same" and (la.portable_path(folder) if folder else "") == model.sub:
                rows.append(dict(pack=mark, name=name, kind="ref", size=None, mtime=None, ratio="", type=describe(entry),
                                 entry=entry))
    return rows


# ----------------------------------------------------------------------------------------------- the window


class App:
    def __init__(self, root, start):
        self.root = root
        self.place, self.model, self.rows = None, None, {}  # rows: tree item -> row
        self.sort_by, self.descending = "name", False
        self.pack_mode, self.pack_target = "folder", ""  # how, and into which pack folder, the last pack went
        self.extract_dest, self.extract_layout = "", "last"  # where and how the last extract went
        self.jobs, self.job, self.proc, self.output, self.busy = [], None, None, [], None
        self.lines = queue.Queue()  # output of the running process; None when it has ended
        self.cancelling = self.quit_after = False
        self.scale = root.winfo_fpixels("1i") / 96
        root.title("OverCompressing")
        root.geometry(f"{self.px(1120)}x{self.px(720)}")
        root.minsize(self.px(760), self.px(460))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.go(start or Place(os.getcwd()))
        root.after(100, self.poll)

    def px(self, n):
        return int(n * self.scale)

    # ------------------------------------------------------------------------------------------------------ layout

    def build(self):
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style = ttk.Style()
        style.configure("Treeview", rowheight=line + self.px(8))
        # Tk 8.6.9+: without this, tag backgrounds do not show; selected rows keep the selection's colors
        style.map("Treeview", background=[("selected", "#2f6fd6")], foreground=[("selected", "#ffffff")])

        top = ttk.Frame(self.root, padding=(self.px(10), self.px(10), self.px(10), self.px(6)))
        top.pack(fill="x")
        self.up_button = ttk.Button(top, text=t("up"), width=3, command=self.go_up)
        self.up_button.pack(side="left")
        self.path = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.path)
        entry.pack(side="left", fill="x", expand=True, padx=(self.px(6), 0))
        entry.bind("<Return>", lambda _: self.go_to_text())

        bar = ttk.Frame(self.root, padding=(self.px(10), 0, self.px(10), self.px(6)))
        bar.pack(fill="x")
        self.controls = []  # (widget, when it can be used: "dir" (a folder, not inside a model), "files", "idle")
        for key, command, when in (("pack", self.pack, "dir"), ("bundle", self.bundle, "dir"), (None, None, None),
                                   ("extract", self.extract, "files"), ("verify", self.verify, "files"),
                                   (None, None, None), ("refresh", self.refresh, "idle")):
            if key is None:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=self.px(6))
                continue
            button = ttk.Button(bar, text=t(key), command=command)
            button.pack(side="left", padx=(0, self.px(4)))
            self.controls.append((button, when))
        legend = ttk.Frame(bar)
        legend.pack(side="right")
        for kind, label in LEGEND:
            tk.Label(legend, text=f" {label} ", bg=COLORS[kind], fg="#202124").pack(side="left", padx=(self.px(3), 0))

        panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=self.px(10))
        frame = ttk.Frame(panes)
        self.columns = {"pack": (t("col_pack"), 80, "center"), "name": (t("col_name"), 400, "w"),
                        "size": (t("col_size"), 100, "e"),
                        "ratio": (t("col_ratio"), 80, "center"), "type": (t("col_type"), 280, "w"),
                        "modified": (t("col_modified"), 150, "w")}
        self.tree = ttk.Treeview(frame, columns=list(self.columns), show="headings", selectmode="extended")
        for name, (title, width, anchor) in self.columns.items():
            self.tree.heading(name, text=title, anchor=anchor, command=lambda n=name: self.sort(n))
            self.tree.column(name, width=self.px(width), anchor=anchor, stretch=name in ("name", "type"))
        for kind, color in COLORS.items():
            self.tree.tag_configure(kind, background=color)
        self.tree.tag_configure("ref", foreground="#5f6368")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        panes.add(frame, weight=3)
        self.tree.bind("<Double-1>", lambda e: self.open_row(self.tree.identify_row(e.y)))
        self.tree.bind("<Return>", lambda _: self.open_row(self.tree.focus()))
        self.tree.bind("<BackSpace>", lambda _: self.go_up())
        self.tree.bind("<Alt-Up>", lambda _: self.go_up())

        frame = ttk.Frame(panes)
        mono = next((f for f in ("Consolas", "Cascadia Mono", "Menlo", "DejaVu Sans Mono") if f in tkfont.families()), None)
        self.mono = tkfont.Font(family=mono, size=tkfont.nametofont("TkDefaultFont").actual("size")) if mono else "TkFixedFont"
        self.log = tk.Text(frame, height=8, wrap="none", state="disabled", font=self.mono, relief="flat",
                           padx=self.px(6), pady=self.px(4))
        self.log.tag_configure("command", foreground="#1a5fb4")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        panes.add(frame, weight=1)

        bottom = ttk.Frame(self.root, padding=(self.px(10), self.px(6), self.px(10), self.px(10)))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, anchor="w", width=1)  # width 1: long text never widens the window
        self.status.pack(side="left", fill="x", expand=True)
        self.bold = tkfont.nametofont("TkDefaultFont").copy()  # kept: a font object deletes its font when collected
        self.bold.configure(weight="bold")

        self.menu = tk.Menu(self.root, tearoff=False)
        for key, command in (("extract", self.extract), ("verify", self.verify), ("pack", self.pack), ("bundle", self.bundle)):
            self.menu.add_command(label=t(key), command=command)
        self.tree.bind("<Button-2>" if sys.platform == "darwin" else "<Button-3>", self.popup)

    def write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        if int(self.log.index("end").split(".")[0]) > 5000:
            self.log.delete("1.0", "1000.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def popup(self, event):
        item = self.tree.identify_row(event.y)
        if item and item not in self.tree.selection():
            self.tree.selection_set(item)
        if not self.proc:
            self.menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------------------------------------- browsing

    def go(self, place, select=None):
        """Show a place (and select the row named `select`); if it cannot be listed, stay where the list was."""
        try:
            model, problem = find_model(place)
            rows = list_place(place, model)
        except (OSError, ValueError) as e:
            la.close_single_files()
            if self.place is None and place.parent():  # at start: show what can be shown
                return self.go(place.parent())
            messagebox.showerror("OverCompressing", t("cannot_list", problem=getattr(e, "strerror", None) or e),
                                 parent=self.root)
            return
        la.close_single_files()  # an .oczip held open here could not be replaced by a bundle
        self.place, self.model = place, model
        self.path.set(place.text())
        self.root.title(f"OverCompressing — {place.text()}")
        self.show(rows, select)
        self.say_where(problem)
        self.update_buttons()

    def show(self, rows, select=None):
        def key(row):
            value = row.get({"modified": "mtime"}.get(self.sort_by, self.sort_by))
            if self.sort_by == "ratio":
                value = float(value[:-1]) if value else -1.0
            elif value is None:
                value = -1
            elif isinstance(value, str):
                value = value.casefold()
            return value

        rows = sorted(rows, key=lambda r: r["name"].casefold())
        rows.sort(key=key, reverse=self.descending)
        rows.sort(key=lambda r: r["kind"] not in ("folder", "drive"))  # folders first, as in a file manager
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for row in rows:
            modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["mtime"])) if row["mtime"] else ""
            item = self.tree.insert("", "end", tags=(row["kind"],), values=(
                row["pack"], row["name"], size_text(row["size"]), row["ratio"], row["type"], modified))
            self.rows[item] = row
            if row["name"] == select:
                self.tree.selection_set(item)
                self.tree.see(item)
        for name, (title, _, anchor) in self.columns.items():
            mark = (" ▼" if self.descending else " ▲") if name == self.sort_by else ""
            self.tree.heading(name, text=title + mark)

    def sort(self, column):
        self.descending = not self.descending if column == self.sort_by else False
        self.sort_by = column
        self.show(list(self.rows.values()))

    def say_where(self, problem=None):
        if problem:
            text = t("bad_manifest", problem=problem)
        elif self.model:
            files = self.model.manifest["files"]
            size, stored = sum(e["size"] for e in files), sum(e["stored_size"] for e in files)
            text = t("model_here", repo=self.model.repo, files=count(len(files), "file"), size=size_text(size),
                     stored=size_text(stored), ratio=percent(stored, size))
            needs = la.repo_needs({self.model.repo: self.model.manifest})[self.model.repo]
            if needs:
                text += " · " + t("model_needs", names=", ".join(sorted(needs)))
        else:
            text = count(len(self.rows), "item")
        if self.place.in_zip:
            text += f" · {t('read_only')}"
        self.status.configure(text=text)

    def refresh(self):
        chosen = [self.rows[i]["name"] for i in self.tree.selection() if i in self.rows]
        self.go(self.place, chosen[0] if chosen else None)

    def go_up(self):
        """Go to the parent, with the place just left selected there."""
        up = self.place.parent() if self.place else None
        if up:
            if self.place.inner:
                self.go(up, self.place.inner.rpartition("/")[2])
            else:
                self.go(up, os.path.basename(self.place.path) or self.place.path)  # a drive's row is named "C:\"

    def go_to_text(self):
        place = place_from_text(self.path.get())
        if place:
            self.go(place)
        else:
            messagebox.showerror("OverCompressing", t("no_place", text=self.path.get()), parent=self.root)
            self.path.set(self.place.text())

    def open_row(self, item):
        """Go into a folder, a drive or an .oczip (not one inside an .oczip)."""
        row = self.rows.get(item)
        if row and (row["kind"] in ("folder", "drive") or (row["kind"] == "oczip" and not self.place.in_zip)):
            self.go(self.place.child(row["name"]))

    def update_buttons(self):
        inside = self.place is None or self.place.path is None or self.place.in_zip or self.model is not None
        for widget, when in self.controls:
            usable = not self.proc and (when == "idle" or (when == "dir" and not inside)
                                        or (when == "files" and self.place is not None and self.place.path is not None))
            widget.configure(state="normal" if usable else "disabled")

    # ------------------------------------------------------------------------------------------------- selections

    def chosen(self):
        return [self.rows[i] for i in self.tree.selection() if i in self.rows]

    def full(self, name):
        """The path of `name` in the current folder (not in an .oczip)."""
        return os.path.join(self.place.path, name)

    def models_in(self, root):
        """{repo} of the models of an archive (a folder or an .oczip), from their manifests."""
        repos = set()
        try:
            for folder in la.model_folders(root):
                try:
                    repos.add(la.model_manifest(root, folder)["repo"])
                except (OSError, ValueError):
                    pass
        finally:
            la.close_single_files()
        return repos

    def targets(self, rows):
        """{(archive, model): None for the whole model, or the set of its files' original paths} for these rows;
        a model given whole wins over some of its files."""
        plan = {}

        def whole(root, repo):
            plan[(root, repo)] = None

        def some(root, repo, paths):
            if plan.get((root, repo), set()) is not None:
                plan.setdefault((root, repo), set()).update(paths)

        here = self.place
        for row in rows:
            if self.model and row.get("entry"):
                some(self.model.root, self.model.repo, [row["entry"]["path"]])
            elif self.model and row["kind"] == "folder":  # a folder of stored files in a model
                prefix = f"{self.model.sub}/{row['name']}/" if self.model.sub else f"{row['name']}/"
                some(self.model.root, self.model.repo, [e["path"] for e in self.model.manifest["files"]
                                                        if (e.get("stored") or la.portable_path(e["path"])).startswith(prefix)])
            elif row["kind"] == "oczip" and not here.in_zip:
                for repo in self.models_in(self.full(row["name"])):
                    whole(self.full(row["name"]), repo)
            elif row["kind"] == "folder" and here.in_zip and not here.inner:  # a model folder at the top of an .oczip
                try:
                    whole(here.path, la.model_manifest(here.path, row["name"])["repo"])
                except (OSError, ValueError):
                    pass
                finally:
                    la.close_single_files()
            elif row["kind"] == "folder" and not here.in_zip:
                path = self.full(row["name"])
                if os.path.isfile(os.path.join(path, "manifest.json")):  # a model folder
                    try:
                        whole(here.path, la.model_manifest(here.path, row["name"])["repo"])
                    except (OSError, ValueError):
                        pass
                else:  # an archive folder
                    for repo in self.models_in(path):
                        whole(path, repo)
        return plan

    def here_as_target(self):
        """What extract or verify do with nothing selected: the model the list is in, or every model of the .oczip
        or archive folder it shows."""
        if self.model:
            return {(self.model.root, self.model.repo): None}
        root = self.place.path if self.place.path and (not self.place.in_zip or not self.place.inner) else None
        return {(root, repo): None for repo in self.models_in(root)} if root else {}

    # ------------------------------------------------------------------------------------------------- actions

    def extract(self):
        rows = self.chosen()
        plan = self.targets(rows) if rows else self.here_as_target()
        if not plan:
            messagebox.showinfo(t("extract"), t("nothing_to_extract"), parent=self.root)
            return
        answer = self.ask_extract(plan)
        if not answer:
            return
        dest, layout = answer
        targets = {key: extract_target(dest, key[1], layout) for key in plan}
        problems = [p for p in (la.place_problem(targets[key], key[0]) for key in plan) if p]
        if problems:
            messagebox.showerror(t("extract"), t("bad_dest", problems="\n".join(dict.fromkeys(problems))), parent=self.root)
            return
        jobs = []
        for (root, repo), paths in sorted(plan.items()):
            argv = ["restore", os.path.join(root, la.repo_dir_name(repo)), targets[(root, repo)],
                    *[f"--file={p}" for p in sorted(paths or ())], *(["--flat"] if layout == "files" else [])]
            jobs.append({"argv": argv, "title": t("extracting", name=repo), "retry": self.offer_overwrite})
        self.start(jobs, lambda: messagebox.askyesno(t("extract"), t("done_extract_q", dest=dest), parent=self.root)
                   and open_folder(dest))

    def offer_overwrite(self, job, output):
        """The arguments to run a failed restore again with --overwrite, if that is why it failed and the user agrees."""
        if "with other contents" in output and "--overwrite" not in job["argv"] and messagebox.askyesno(
                t("extract"), t("overwrite_q", dest=job["argv"][2]), parent=self.front()):
            return job["argv"] + ["--overwrite"]
        return None

    def ask_extract(self, plan):
        """(destination folder, which folders to make there: "full", "last" or "files") the user picks, or None."""
        repos = sorted({repo for _, repo in plan})
        lasts = [r.split("/")[-1] for r in repos]
        clash = next((n for n in lasts if lasts.count(n) > 1), None)
        usable = {"full": True, "last": clash is None, "files": len(repos) == 1}
        dialog = tk.Toplevel(self.root)
        dialog.title(t("extract_title"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(520)
        what = [t("whole_model", repo=repo) if paths is None else t("some_files", repo=repo, files=count(len(paths), "file"))
                for (_, repo), paths in sorted(plan.items(), key=lambda kv: kv[0][1])]
        ttk.Label(frame, text="\n".join(what[:6] + (["…"] if len(what) > 6 else [])), wraplength=wrap).pack(anchor="w")
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
        named = next((r for r in repos if "/" in r), repos[0]).split("/")  # one that shows the difference, if any
        for key, shown in (("full", os.path.join("…", *named, "…")), ("last", os.path.join("…", named[-1], "…")),
                           ("files", os.path.join("…", "…"))):
            ttk.Radiobutton(frame, text=f"{t('path_' + key)}     {shown}", value=key, variable=layout,
                            state="normal" if usable[key] else "disabled").pack(anchor="w")
        notes = [t("last_off", name=clash)] * (not usable["last"]) + [t("files_off")] * (not usable["files"])
        if notes:
            ttk.Label(frame, text="  ".join(notes), foreground="#5f6368", wraplength=wrap).pack(anchor="w", pady=(self.px(4), 0))
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(self.px(14), 0))
        result = []

        def accept(_=None):
            if not dest.get().strip():
                error.configure(text=t("need_dest"))
                return
            result.append((os.path.normpath(dest.get().strip()), layout.get()))
            dialog.destroy()

        ttk.Button(buttons, text=t("ok"), command=accept, default="active").pack(side="left", padx=(0, self.px(6)))
        ttk.Button(buttons, text=t("cancel"), command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        entry.icursor("end")
        entry.focus_set()  # keys (Return, Escape) reach a dialog only through the focus
        self.show_dialog(dialog)
        if result:
            self.extract_dest, self.extract_layout = result[0]
        return result[0] if result else None

    def verify(self):
        rows = self.chosen()
        plan = self.targets(rows) if rows else self.here_as_target()
        if not plan:
            messagebox.showinfo(t("verify"), t("nothing_to_verify"), parent=self.root)
            return
        by_root = {}
        for root, repo in plan:
            by_root.setdefault(root, set()).add(repo)
        jobs = []
        for root, repos in sorted(by_root.items()):
            whole = repos == self.models_in(root)
            what = t("everything", pack=os.path.basename(root)) if whole else ", ".join(sorted(repos))
            paths = [root] if whole else [os.path.join(root, la.repo_dir_name(r)) for r in sorted(repos)]
            jobs.append({"argv": ["verify", *paths], "title": t("verifying", what=what)})
        self.start(jobs, lambda: messagebox.showinfo(t("verify"), t("done_verify"), parent=self.root))

    def pack(self):
        if self.place is None or self.place.path is None or self.place.in_zip or self.model:
            messagebox.showinfo(t("name_title"), t("pack_here"), parent=self.root)
            return
        rows = self.chosen()
        folders = [r for r in rows if r["kind"] == "folder"]
        if len(folders) == 1 and len(rows) == 1:
            source, files, name = self.full(folders[0]["name"]), [], folders[0]["name"]
        elif rows and not folders and all(r["kind"] not in ("drive", "ref") for r in rows):
            source, files = self.place.path, sorted(r["name"] for r in rows)
            name = os.path.splitext(files[0])[0] if len(files) == 1 else os.path.basename(self.place.path)
        else:
            messagebox.showinfo(t("name_title"), t("pack_what"), parent=self.root)
            return
        found, _ = la.listed_files(source, files) if files else la.source_files(source, [])
        size = sum(os.path.getsize(os.path.join(source, *rel.split("/"))) for rel in found)
        answer = self.ask_pack(name, t("name_what", files=count(len(found), "file"), size=size_text(size), folder=source),
                               source, files)
        if not answer:
            return
        name, mode, where = answer
        self.pack_mode = mode
        if mode == "folder":
            self.pack_target = where
        argv = ["pack", source, where, f"--name={name}", *[f"--file={f}" for f in files]]

        def done():
            if mode == "file" and self.place.path == os.path.dirname(where) and not self.place.in_zip:
                self.go(self.place, os.path.basename(where))
            messagebox.showinfo(t("name_title"), t("done_pack", name=name), parent=self.root)

        self.start([{"argv": argv, "title": t("packing", name=name)}], done)

    def ask_pack(self, default, what, source, files):
        """(model name, "folder" or "file", the pack folder or new pack file) the user enters, checked (also that
        `source` may be packed there), or None."""
        dialog = tk.Toplevel(self.root)
        dialog.title(t("name_title"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(540)
        ttk.Label(frame, text=what, wraplength=wrap).pack(anchor="w")
        ttk.Label(frame, text=t("name_prompt"), wraplength=wrap).pack(anchor="w", pady=(self.px(12), self.px(4)))
        name = tk.StringVar(value=default)
        entry = ttk.Entry(frame, textvariable=name, width=58)
        entry.pack(fill="x")
        ttk.Label(frame, text=t("into"), wraplength=wrap).pack(anchor="w", pady=(self.px(12), self.px(4)))
        mode = tk.StringVar(value=self.pack_mode)
        new_file = os.path.join(source if files else os.path.dirname(source), default.split("/")[-1] + la.OCZIP)
        paths, shown = {"folder": self.pack_target, "file": new_file}, [mode.get()]
        target = tk.StringVar(value=paths[mode.get()])

        def switch():  # each choice keeps its own path
            paths[shown[0]], shown[0] = target.get(), mode.get()
            target.set(paths[mode.get()])

        for key in ("folder", "file"):
            ttk.Radiobutton(frame, text=t(f"into_{key}"), value=key, variable=mode, command=switch).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(self.px(4), 0))
        ttk.Entry(row, textvariable=target, width=50).pack(side="left", fill="x", expand=True)

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

        ttk.Button(row, text=t("browse"), command=browse).pack(side="left", padx=(self.px(6), 0))
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        ttk.Label(frame, text=t("name_hint"), foreground="#5f6368", wraplength=wrap).pack(anchor="w", pady=(self.px(4), 0))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(self.px(14), 0))
        result = []

        def accept(_=None):
            model, where = name.get().strip(), target.get().strip()
            problem = la.name_problem(model) or (not where and t("need_target"))
            if not problem and mode.get() == "file":
                where = os.path.normpath(where)
                problem = ((not la.is_oczip(where) and t("bundle_name")) or (os.path.lexists(where) and t("file_exists"))
                           or (la.holds(source, where) and not files and t("file_in_source")))
            elif not problem:
                where = os.path.normpath(where)
                if la.is_oczip(where):
                    problem = t("target_oczip")
                else:
                    inside = ((la.holds(source, where) and not files and f"{source} holds the pack folder {where}")
                              or la.place_problem(source, where))
                    problem = inside and t("bad_source", source=source, problem=inside)
            if problem:
                error.configure(text=problem)
                return
            if mode.get() == "folder" and os.path.isfile(os.path.join(where, la.repo_dir_name(model), "manifest.json")):
                try:
                    held = count(len(la.model_manifest(where, la.repo_dir_name(model))["files"]), "file")
                except (OSError, ValueError):
                    held = "?"
                if not messagebox.askyesno(t("name_title"), t("replace_q", pack=where, name=model, files=held),
                                           parent=dialog):
                    return
            result.append((model, mode.get(), where))
            dialog.destroy()

        ttk.Button(buttons, text=t("ok"), command=accept, default="active").pack(side="left", padx=(0, self.px(6)))
        ttk.Button(buttons, text=t("cancel"), command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        entry.select_range(0, "end")
        entry.focus_set()  # keys (Return, Escape) reach a dialog only through the focus
        self.show_dialog(dialog)
        return result[0] if result else None

    def bundle(self):
        """Bundle the selected archive folder (or the one the list shows) into one .oczip: ask where, and what to do
        after, then run llm_archive.py bundle."""
        if self.place is None or self.place.path is None or self.place.in_zip or self.model:
            messagebox.showinfo(t("bundle_title"), t("bundle_what_first"), parent=self.root)
            return
        rows = self.chosen()
        folder = self.full(rows[0]["name"]) if len(rows) == 1 and rows[0]["kind"] == "folder" else self.place.path
        if not self.models_in(folder):
            messagebox.showinfo(t("bundle_title"), t("bundle_what_first"), parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(t("bundle_title"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(520)
        ttk.Label(frame, text=t("bundle_what", pack=folder), wraplength=wrap).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(self.px(8), self.px(4)))
        path = tk.StringVar(value=os.path.normpath(folder) + ".oczip")
        entry = ttk.Entry(row, textvariable=path, width=56)
        entry.pack(side="left", fill="x", expand=True)

        def browse():
            chosen = filedialog.asksaveasfilename(
                parent=dialog, title=t("bundle_title"), defaultextension=".oczip", filetypes=[(".oczip", "*.oczip")],
                initialdir=os.path.dirname(path.get()), initialfile=os.path.basename(path.get()), confirmoverwrite=False)
            if chosen:
                path.set(os.path.normpath(chosen))

        ttk.Button(row, text=t("browse"), command=browse).pack(side="left", padx=(self.px(6), 0))
        verify, delete = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text=t("bundle_verify"), variable=verify).pack(anchor="w", pady=(self.px(6), 0))
        ttk.Checkbutton(frame, text=t("bundle_delete"), variable=delete,
                        command=lambda: delete.get() and verify.set(True)).pack(anchor="w")
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        ttk.Label(frame, text=t("bundle_hint"), foreground="#5f6368", wraplength=wrap).pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(self.px(14), 0))
        result = []

        def accept(_=None):
            target = os.path.normpath(path.get().strip())
            if not target.lower().endswith(".oczip"):
                error.configure(text=t("bundle_name"))
                return
            result.append(target)
            dialog.destroy()

        ttk.Button(buttons, text=t("ok"), command=accept, default="active").pack(side="left", padx=(0, self.px(6)))
        ttk.Button(buttons, text=t("cancel"), command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        entry.icursor("end")
        entry.focus_set()  # keys (Return, Escape) reach a dialog only through the focus
        self.show_dialog(dialog)
        if not result:
            return
        target, overwrite = result[0], False
        if os.path.exists(target):
            if not messagebox.askyesno(t("bundle_title"), t("bundle_replace_q", path=target), parent=self.root):
                return
            overwrite = True
        if delete.get() and not messagebox.askyesno(t("bundle_title"), t("bundle_delete_q", path=target, pack=folder,
                                                    checked=t("checked" if verify.get() else "unchecked")), parent=self.root):
            return
        argv = ["bundle", folder, target, "--verify" if verify.get() else "--no-verify"]
        argv += ["--delete"] * delete.get() + ["--overwrite"] * overwrite

        def done():
            if delete.get():
                self.go(Place(target, ""))
            else:
                self.go(self.place, os.path.basename(target))
            messagebox.showinfo(t("bundle_title"), t("done_bundle_deleted" if delete.get() else "done_bundle", path=target),
                                parent=self.root)

        self.start([{"argv": argv, "title": t("bundling", name=os.path.basename(target))}], done)

    def show_dialog(self, dialog):
        """Center a dialog over the window and wait, modal, until it is closed."""
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dialog.wait_visibility()  # a grab needs a window that is shown (X11)
        dialog.grab_set()
        self.root.wait_window(dialog)

    # ---------------------------------------------------------------------------------------- running the tool

    def start(self, jobs, done):
        """Run llm_archive.py once per job, one after another, showing what it prints; stop at the first failure.
        `done` is called once all have succeeded."""
        self.jobs, self.done = list(jobs), done
        self.job_number, self.job_total = 0, len(self.jobs)
        for widget, _ in self.controls:
            widget.configure(state="disabled")
        self.show_busy()
        self.next_job()
        self.spin(0)

    def show_busy(self):
        """A window in front while llm_archive.py runs, holding all input (the main window waits): what runs, the last
        line it printed, how long it has run, and Cancel. Closing it is Cancel."""
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
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(self.px(16), 0))
        self.spinner = ttk.Label(row, anchor="w")  # turns while something runs: activity, not progress
        self.spinner.pack(side="left")
        self.cancel_button = ttk.Button(row, text=t("cancel"), command=self.cancel)
        self.cancel_button.pack(side="right")
        self.busy_since = time.monotonic()
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
        """Turn the symbol in the window in front, beside the time run so far, while something runs."""
        if self.busy is None:
            return
        seconds = int(time.monotonic() - self.busy_since)
        self.spinner.configure(text=f"{SPINNER[step % len(SPINNER)]}   {seconds // 60}:{seconds % 60:02d}")
        self.root.after(150, self.spin, step + 1)

    def next_job(self):
        if not self.jobs:
            self.finish(True)
            return
        self.job, self.output = self.jobs.pop(0), []
        self.job_number += 1
        counted = f"  ({self.job_number}/{self.job_total})" if self.job_total > 1 else ""
        self.busy_title.configure(text=self.job["title"] + counted)
        self.busy_line.configure(text="")
        argv = self.job["argv"]
        shown = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        self.write(f"\n> llm_archive.py {shown}\n", "command")
        self.status.configure(text=self.job["title"] + " …")
        where = dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name == "nt" else dict(start_new_session=True)
        self.proc = subprocess.Popen([python_exe(), "-I", "-X", "utf8", "-u", "-c", RUNNER, HERE, *argv],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", **where)
        threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()

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
                self.output.append(line)
                self.write(line)
                if line.strip() and not self.cancelling:
                    self.status.configure(text=f"{self.job['title']}:  {line.strip()}")
                    shown = line.strip()
                    self.busy_line.configure(text=shown if len(shown) <= 90 else shown[:89] + "…")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def job_ended(self):
        try:
            self.proc.stdin.close()
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
                self.finish(False)
                tail = "\n".join(output.strip().splitlines()[-8:])
                messagebox.showerror(t("failed"), tail or f"exit code {code}", parent=self.root)
                return
            self.jobs.insert(0, {**self.job, "argv": retry})
            self.job_number -= 1  # the same job, once more
        self.next_job()

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
    """Where the list starts: the folder or .oczip given, else the archive this file is in when it runs from an
    offline kit (<archive>/_kit/tools), else the current folder."""
    if len(sys.argv) > 1:
        return place_from_text(sys.argv[1])
    kit = os.path.dirname(HERE)
    return Place(os.path.dirname(kit)) if os.path.basename(kit) == "_kit" else None


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
