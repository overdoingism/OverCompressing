"""A small window for llm_archive.py: pack a folder, or some files of one, into an archive; bundle the archive into
one .oczip file; see what an archive folder or .oczip holds; verify it; extract models or single files. Each action
runs llm_archive.py in a process of its own, just as on the command line (delta bases are chosen automatically);
rebase, --base and the other options are there.

    python -I llm_archive_gui.py [<archive_root> | <file.oczip>]

Run from an offline kit (<archive_root>/_kit/tools), it opens that archive. Its text is Unicode symbols where they
say it, else English: nothing depends on the system's language.
"""

import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import llm_archive as la  # noqa: E402

# Runs llm_archive.py in the child process. When its stdin closes (Cancel, or this window going away), it stops as on
# Ctrl+C, so a pack or restore still removes what it had begun.
RUNNER = """import sys, threading, _thread
sys.path.insert(0, sys.argv[1])
import llm_archive as la
def cancel_on_eof():
    sys.stdin.read()
    _thread.interrupt_main()
threading.Thread(target=cancel_on_eof, daemon=True).start()
sys.argv = ["llm_archive.py", *sys.argv[2:]]
try:
    la.main()
except KeyboardInterrupt:
    sys.exit("cancelled")
"""

TEXT = {  # all of the window's text: symbols (Unicode BMP, which every Tk renders) where they say it, else English
    "archive": "Archive",
    "choose_archive": "Open",
    "open_folder": "Archive folder…",
    "open_oczip": "Single file (.oczip)…",
    "pick_oczip": "Single-file archive (.oczip)",
    "read_only": "read-only .oczip",
    "no_archive": "—",
    "pack_folder": "+ Folder…",
    "pack_files": "+ Files…",
    "bundle": "▣ Bundle…",
    "bundle_title": "Bundle into one .oczip",
    "bundle_what": "All of {archive} (models, _kit and anything else in it) into one file:",
    "browse": "Browse…",
    "bundle_verify": "✔ Verify it: read every file back, verify every model",
    "bundle_delete": "Then delete the bundled files from the folder (✔ Verify comes on with it)",
    "bundle_hint": "✔ Verify and ⇩ Extract read the .oczip as it is. It is read-only: to pack into it again, extract it "
                   "into a folder (it is a ZIP).",
    "bundle_name": "The file name must end with .oczip.",
    "bundle_replace_q": "{path} exists already. Replace it?",
    "bundle_delete_q": "Once {path} is written{checked}, the files of {archive} it holds are deleted. Go on?",
    "checked": " and verified",
    "unchecked": " (without verifying it)",
    "bundling": "▶ Bundling into {name}",
    "done_bundle": "✔ Bundled into {path}.",
    "done_bundle_deleted": "✔ Bundled into {path}, and the folder's files deleted: the .oczip is open now.",
    "extract": "⇩ Extract…",
    "verify": "✔ Verify",
    "refresh": "↻ Refresh",
    "cancel": "✕ Cancel",
    "stop_now": "✕ Stop now",
    "ok": "OK",
    "col_name": "Name",
    "col_size": "Size",
    "col_stored": "Stored",
    "col_ratio": "%",
    "col_how": "Stored as",
    "summary": "{models} · {files} · {size} → {stored} ({ratio})",
    "empty": "Empty archive: + Folder… or + Files… packs into it.",
    "need_archive": "Open… an archive folder first.",
    "how_raw": "as is",
    "how_same": "= {ref}",
    "how_delta": "Δ {base}",
    "how_packed": "{method}",
    "model_needs": "needs {names}",
    "unreadable": "⚠ {problem}",
    "pick_archive": "Archive folder (an empty one starts a new archive)",
    "not_archive": "{folder} holds other things but no models. Use it as an archive anyway? Each model packed goes "
                   "into a folder of its own in it.",
    "pick_folder": "Folder to pack",
    "pick_files": "Files to pack (from one folder)",
    "pick_dest": "Extract into (each model goes into a folder named after it)",
    "select_first": "Select models or files in the list first.",
    "name_title": "Pack",
    "name_what": "{files} · {size} · {folder}",
    "name_prompt": "Name in the archive (e.g. Qwen/Qwen2.5-7B):",
    "name_hint": "Δ bases are chosen automatically; for other options use llm_archive.py (docs/usage.md).",
    "replace_q": "The archive already holds {name} ({files}). Replace it with these files?",
    "packing": "▶ Packing {name}",
    "extracting": "▶ Extracting {name}",
    "verifying": "▶ Verifying {what}",
    "everything": "everything",
    "cancelling": "■ Cancelling: finishing the current step…",
    "force_q": "It is still finishing the current step. Stop it right away? Unfinished temporary files (.oc-*.part) "
               "may be left behind; they can simply be deleted.",
    "quit_q": "Something is still running. Cancel it and quit?",
    "done_pack": "✓ Packed {name}.",
    "done_extract_q": "✓ Extracted into {dest}. Open that folder?",
    "done_verify": "✓ Everything checked is intact.",
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
    if n < 1024:
        return f"{n} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024
        if n < 1024 or unit == "TiB":
            return f"{n:,.1f} {unit}"


def ratio(stored, size):
    return f"{stored / size * 100:.1f}%" if size else ""


def describe(entry):
    if entry["method"] == "same":
        return t("how_same", ref=f"{entry['same_as']['repo']}:{entry['same_as']['path']}")
    if entry["method"] == "raw":
        return t("how_raw")
    if entry.get("delta_base"):
        return t("how_delta", base=entry["delta_base"])
    return t("how_packed", method=entry["method"])


def open_folder(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
    except OSError:
        pass


class App:
    def __init__(self, root, archive):
        self.root = root
        self.archive = archive
        self.manifests = {}
        self.items = {}  # tree item -> (model, file path or None for the model's own row)
        self.jobs, self.job, self.proc, self.output = [], None, None, []
        self.lines = queue.Queue()  # output of the running process; None when it has ended
        self.cancelling = self.quit_after = False
        self.scale = root.winfo_fpixels("1i") / 96
        root.title("OverCompressing")
        root.geometry(f"{self.px(1080)}x{self.px(700)}")
        root.minsize(self.px(720), self.px(460))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.load()
        root.after(100, self.poll)

    def px(self, n):
        return int(n * self.scale)

    # ------------------------------------------------------------------------------------------------------ layout

    def build(self):
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        ttk.Style().configure("Treeview", rowheight=line + self.px(8))
        self.bold = tkfont.nametofont("TkDefaultFont").copy()  # kept: a font object deletes its font when collected
        self.bold.configure(weight="bold")

        top = ttk.Frame(self.root, padding=(self.px(10), self.px(10), self.px(10), self.px(6)))
        top.pack(fill="x")
        ttk.Label(top, text=t("archive")).pack(side="left")
        self.path = tk.StringVar()
        ttk.Entry(top, textvariable=self.path, state="readonly").pack(side="left", fill="x", expand=True, padx=self.px(6))
        opener = ttk.Menubutton(top, text=t("choose_archive"))
        menu = tk.Menu(opener, tearoff=False)
        menu.add_command(label=t("open_folder"), command=self.choose_archive)
        menu.add_command(label=t("open_oczip"), command=self.choose_oczip)
        opener["menu"] = menu
        opener.pack(side="left")
        self.controls = [(opener, "idle")]  # (widget, when it can be used: "idle", "folder" archive, "models" in one)

        bar = ttk.Frame(self.root, padding=(self.px(10), 0, self.px(10), self.px(6)))
        bar.pack(fill="x")
        for key, command, when in (("pack_folder", self.pack_folder, "folder"), ("pack_files", self.pack_files, "folder"),
                                   ("bundle", self.bundle, "models"), (None, None, None),
                                   ("extract", self.extract, "idle"), ("verify", self.verify, "idle"), (None, None, None),
                                   ("refresh", self.load, "idle")):
            if key is None:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=self.px(6))
                continue
            button = ttk.Button(bar, text=t(key), command=command)
            button.pack(side="left", padx=(0, self.px(4)))
            self.controls.append((button, when))

        panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=self.px(10))
        frame = ttk.Frame(panes)
        columns = {"size": (t("col_size"), 100, "e"), "stored": (t("col_stored"), 100, "e"),
                   "ratio": (t("col_ratio"), 80, "center"), "how": (t("col_how"), 300, "w")}
        self.tree = ttk.Treeview(frame, columns=list(columns), selectmode="extended")
        self.tree.heading("#0", text=t("col_name"), anchor="w")
        self.tree.column("#0", width=self.px(380))
        for name, (title, width, anchor) in columns.items():
            self.tree.heading(name, text=title, anchor=anchor)
            self.tree.column(name, width=self.px(width), anchor=anchor, stretch=name == "how")
        self.tree.tag_configure("model", font=self.bold)
        self.tree.tag_configure("problem", foreground="#b3261e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        panes.add(frame, weight=3)

        frame = ttk.Frame(panes)
        mono = next((f for f in ("Consolas", "Cascadia Mono", "Menlo", "DejaVu Sans Mono") if f in tkfont.families()), None)
        self.mono = tkfont.Font(family=mono, size=tkfont.nametofont("TkDefaultFont").actual("size")) if mono else "TkFixedFont"
        self.log = tk.Text(frame, height=9, wrap="none", state="disabled", font=self.mono, relief="flat",
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
        self.progress = ttk.Progressbar(bottom, mode="determinate", length=self.px(160))
        self.progress.pack(side="left", padx=self.px(8))
        self.cancel_button = ttk.Button(bottom, text=t("cancel"), command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left")

        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label=t("extract"), command=self.extract)
        self.menu.add_command(label=t("verify"), command=self.verify)
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
        if self.tree.selection() and not self.proc:
            self.menu.tk_popup(event.x_root, event.y_root)

    # ----------------------------------------------------------------------------------------------- the archive

    def load(self):
        """Show the models of the archive and their files, from their manifests."""
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.path.set(self.archive or t("no_archive"))
        if not self.archive:
            self.manifests = {}
            self.status.configure(text=t("need_archive"))
            return
        problems = []
        try:
            self.manifests = la.all_manifests(self.archive, problems)
        except (OSError, ValueError) as e:
            self.manifests, problems = {}, [(self.archive, getattr(e, "strerror", None) or str(e))]
        finally:
            la.close_single_files()  # an .oczip held open here could not be replaced by a bundle
        needs = la.repo_needs(self.manifests)
        size = stored = files = 0
        for repo, manifest in self.manifests.items():
            entries = manifest["files"]
            total, kept = sum(e["size"] for e in entries), sum(e["stored_size"] for e in entries)
            size, stored, files = size + total, stored + kept, files + len(entries)
            how = count(len(entries), "file")
            if needs[repo]:
                how += "; " + t("model_needs", names=", ".join(sorted(needs[repo])))
            node = self.tree.insert("", "end", text=repo, tags=("model",),
                                    values=(size_text(total), size_text(kept), ratio(kept, total), how))
            self.items[node] = (repo, None)
            for e in entries:
                item = self.tree.insert(node, "end", text=e["path"], values=(
                    size_text(e["size"]), size_text(e["stored_size"]), ratio(e["stored_size"], e["size"]), describe(e)))
                self.items[item] = (repo, e["path"])
        for folder, problem in problems:
            self.tree.insert("", "end", text=folder, values=("", "", "", t("unreadable", problem=problem)), tags=("problem",))
        if self.manifests:
            self.status.configure(text=t("summary", models=count(len(self.manifests), "model"), files=count(files, "file"),
                                         size=size_text(size), stored=size_text(stored), ratio=ratio(stored, size))
                                  + (f" · {t('read_only')}" if la.is_oczip(self.archive) else ""))
        else:
            self.status.configure(text=t("empty"))
        self.update_buttons()

    def update_buttons(self):
        single = bool(self.archive) and la.is_oczip(self.archive)
        for widget, when in self.controls:
            usable = not self.proc and (when == "idle" or (when == "folder" and not single)
                                        or (when == "models" and not single and bool(self.manifests)))
            widget.configure(state="normal" if usable else "disabled")

    def choose_archive(self):
        folder = filedialog.askdirectory(parent=self.root, title=t("pick_archive"), mustexist=False,
                                         initialdir=self.archive or None)
        if not folder:
            return
        folder = os.path.normpath(folder)
        if os.path.isfile(os.path.join(folder, "manifest.json")):  # a model's folder: its archive is one up
            folder = os.path.dirname(folder)
        try:
            others = [n for n in os.listdir(folder) if n not in (la.LOCK_NAME, "_kit")]
        except OSError:
            others = []
        if others and not la.all_manifests(folder, []) \
                and not messagebox.askyesno(t("choose_archive"), t("not_archive", folder=folder), parent=self.root):
            return
        self.archive = folder
        self.load()

    def choose_oczip(self):
        path = filedialog.askopenfilename(parent=self.root, title=t("pick_oczip"),
                                          filetypes=[(t("pick_oczip"), "*.oczip"), ("*", "*")])
        if path:
            self.archive = os.path.normpath(path)
            self.load()

    def need_archive(self):
        if not self.archive:
            messagebox.showinfo("OverCompressing", t("need_archive"), parent=self.root)
        return bool(self.archive)

    def selected(self):
        """{model: None for the whole model, or the set of its selected file paths}."""
        chosen = {}
        for item in self.tree.selection():
            if item not in self.items:
                continue
            repo, path = self.items[item]
            if path is None:
                chosen[repo] = None
            elif chosen.get(repo, ()) is not None:
                chosen.setdefault(repo, set()).add(path)
        for repo, paths in chosen.items():
            if paths is not None and len(paths) == len(self.manifests[repo]["files"]):
                chosen[repo] = None
        return chosen

    # ------------------------------------------------------------------------------------------------- actions

    def pack_folder(self):
        if not self.need_archive():
            return
        folder = filedialog.askdirectory(parent=self.root, title=t("pick_folder"), mustexist=True)
        if folder:
            folder = os.path.normpath(folder)
            self.pack(folder, [], os.path.basename(folder))

    def pack_files(self):
        if not self.need_archive():
            return
        chosen = [os.path.normpath(p) for p in self.root.tk.splitlist(
            filedialog.askopenfilenames(parent=self.root, title=t("pick_files")))]
        if not chosen:
            return
        folder = os.path.dirname(chosen[0])
        names = sorted({os.path.relpath(p, folder) for p in chosen})
        name = os.path.splitext(names[0])[0] if len(names) == 1 else os.path.basename(folder)
        self.pack(folder, names, name)

    def pack(self, folder, files, name):
        self.load()
        found, _ = la.listed_files(folder, files) if files else la.source_files(folder, [])
        size = sum(os.path.getsize(os.path.join(folder, *rel.split("/"))) for rel in found)
        name = self.ask_name(name, t("name_what", files=count(len(found), "file"), size=size_text(size), folder=folder))
        if not name:
            return
        if name in self.manifests and not messagebox.askyesno(
                t("name_title"), t("replace_q", name=name, files=count(len(self.manifests[name]["files"]), "file")), parent=self.root):
            return
        argv = ["pack", folder, self.archive, f"--name={name}", *[f"--file={f}" for f in files]]
        self.start([{"argv": argv, "title": t("packing", name=name)}],
                   lambda: messagebox.showinfo(t("name_title"), t("done_pack", name=name), parent=self.root))

    def ask_name(self, default, what):
        """The model name the user types (checked as llm_archive.py checks it), or None."""
        dialog = tk.Toplevel(self.root)
        dialog.title(t("name_title"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(480)
        ttk.Label(frame, text=what, wraplength=wrap).pack(anchor="w")
        ttk.Label(frame, text=t("name_prompt"), wraplength=wrap).pack(anchor="w", pady=(self.px(12), self.px(4)))
        name = tk.StringVar(value=default)
        entry = ttk.Entry(frame, textvariable=name, width=52)
        entry.pack(fill="x")
        error = ttk.Label(frame, foreground="#b3261e", wraplength=wrap)
        error.pack(anchor="w", pady=(self.px(4), 0))
        ttk.Label(frame, text=t("name_hint"), foreground="#5f6368", wraplength=wrap).pack(anchor="w", pady=(self.px(4), 0))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(self.px(14), 0))
        result = []

        def accept(_=None):
            problem = la.name_problem(name.get().strip())
            if problem:
                error.configure(text=problem)
                return
            result.append(name.get().strip())
            dialog.destroy()

        ttk.Button(buttons, text=t("ok"), command=accept, default="active").pack(side="left", padx=(0, self.px(6)))
        ttk.Button(buttons, text=t("cancel"), command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        entry.select_range(0, "end")
        entry.focus_set()
        self.show_dialog(dialog)
        return result[0] if result else None

    def bundle(self):
        """Ask where to write the .oczip and what to do after, then run llm_archive.py bundle."""
        folder = self.archive
        dialog = tk.Toplevel(self.root)
        dialog.title(t("bundle_title"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=self.px(16))
        frame.pack(fill="both", expand=True)
        wrap = self.px(520)
        ttk.Label(frame, text=t("bundle_what", archive=folder), wraplength=wrap).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(self.px(8), self.px(4)))
        path = tk.StringVar(value=os.path.normpath(folder) + ".oczip")
        entry = ttk.Entry(row, textvariable=path, width=56)
        entry.pack(side="left", fill="x", expand=True)

        def browse():
            chosen = filedialog.asksaveasfilename(
                parent=dialog, title=t("bundle_title"), defaultextension=".oczip", filetypes=[(t("pick_oczip"), "*.oczip")],
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
        if delete.get() and not messagebox.askyesno(t("bundle_title"), t("bundle_delete_q", path=target, archive=folder,
                                                    checked=t("checked" if verify.get() else "unchecked")), parent=self.root):
            return
        argv = ["bundle", folder, target, "--verify" if verify.get() else "--no-verify"]
        argv += ["--delete"] * delete.get() + ["--overwrite"] * overwrite

        def done():
            if delete.get():
                self.archive = target
                self.load()
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

    def extract(self):
        if not self.need_archive():
            return
        chosen = self.selected()
        if not chosen:
            messagebox.showinfo(t("extract"), t("select_first"), parent=self.root)
            return
        dest = filedialog.askdirectory(parent=self.root, title=t("pick_dest"), mustexist=False)
        if not dest:
            return
        dest = os.path.normpath(dest)
        jobs = []
        for repo, paths in chosen.items():
            target = os.path.join(dest, *repo.split("/"))
            argv = ["restore", self.archive, repo, target, *[f"--file={p}" for p in sorted(paths or ())]]
            jobs.append({"argv": argv, "title": t("extracting", name=repo), "retry": self.offer_overwrite})
        self.start(jobs, lambda: messagebox.askyesno(t("extract"), t("done_extract_q", dest=dest), parent=self.root)
                   and open_folder(dest))

    def offer_overwrite(self, job, output):
        """The arguments to run a failed restore again with --overwrite, if that is why it failed and the user agrees."""
        if "with other contents" in output and "--overwrite" not in job["argv"] and messagebox.askyesno(
                t("extract"), t("overwrite_q", dest=job["argv"][3]), parent=self.root):
            return job["argv"] + ["--overwrite"]
        return None

    def verify(self):
        if not self.need_archive():
            return
        repos = sorted(self.selected())
        what = ", ".join(repos) if repos else t("everything")
        self.start([{"argv": ["verify", self.archive, *repos], "title": t("verifying", what=what)}],
                   lambda: messagebox.showinfo(t("verify"), t("done_verify"), parent=self.root))

    # ---------------------------------------------------------------------------------------- running the tool

    def start(self, jobs, done):
        """Run llm_archive.py once per job, one after another, showing what it prints; stop at the first failure.
        `done` is called once all have succeeded."""
        self.jobs, self.done = list(jobs), done
        for widget, _ in self.controls:
            widget.configure(state="disabled")
        self.cancel_button.configure(state="normal", text=t("cancel"))
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.next_job()

    def next_job(self):
        if not self.jobs:
            self.finish(True)
            return
        self.job, self.output = self.jobs.pop(0), []
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
        self.next_job()

    def finish(self, ok):
        self.jobs, self.cancelling = [], False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)  # an idle indeterminate bar still shows its block
        self.cancel_button.configure(state="disabled", text=t("cancel"))
        if self.quit_after:
            self.root.destroy()
            return
        self.load()
        if ok:
            self.done()

    def cancel(self):
        if not self.proc:
            return
        if not self.cancelling:
            self.cancelling = True
            self.jobs.clear()
            self.status.configure(text=t("cancelling"))
            self.cancel_button.configure(text=t("stop_now"))
            try:
                self.proc.stdin.close()  # the runner stops as on Ctrl+C
            except OSError:
                pass
        elif messagebox.askyesno(t("stop_now"), t("force_q"), parent=self.root) and self.proc:
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
        elif messagebox.askyesno("OverCompressing", t("quit_q"), parent=self.root):
            self.quit_after = True
            self.cancel()


def default_archive():
    """The archive this file is in when it runs from an offline kit (<archive>/_kit/tools), else none."""
    kit = os.path.dirname(HERE)
    return os.path.dirname(kit) if os.path.basename(kit) == "_kit" else ""


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root, os.path.normpath(sys.argv[1]) if len(sys.argv) > 1 else default_archive())
    root.mainloop()


if __name__ == "__main__":
    main()
