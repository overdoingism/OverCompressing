"""A small window for llm_archive.py: pack a folder, or some files of one, into an archive; see what the archive
holds; verify it; extract models or single files. Each action runs llm_archive.py in a process of its own, just as
on the command line (delta bases are chosen automatically); rebase, --base and the other options are there.

    python -I llm_archive_gui.py [<archive_root>]

Run from an offline kit (<archive_root>/_kit/tools), it opens that archive. The window is in Chinese where the
system is, else in English; OC_LANG=zh or OC_LANG=en picks one.
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

TEXT = {  # key: (English, Chinese)
    "archive": ("Archive:", "歸檔："),
    "choose_archive": ("Open or create…", "開啟／建立…"),
    "no_archive": ("(none chosen)", "（尚未選擇）"),
    "pack_folder": ("Pack a folder…", "壓縮資料夾…"),
    "pack_files": ("Pack files…", "壓縮檔案…"),
    "extract": ("Extract…", "解壓縮…"),
    "verify": ("Verify", "驗證"),
    "refresh": ("Refresh", "重新整理"),
    "cancel": ("Cancel", "取消"),
    "stop_now": ("Stop now", "強制停止"),
    "ok": ("OK", "確定"),
    "col_name": ("Name", "名稱"),
    "col_size": ("Size", "原始大小"),
    "col_stored": ("Stored", "壓縮後"),
    "col_ratio": ("Ratio", "比例"),
    "col_how": ("Stored as", "儲存方式"),
    "summary": ("{models} model(s), {files} file(s): {size} stored in {stored} ({ratio})",
                "{models} 個模型、{files} 個檔案：{size} 壓縮為 {stored}（{ratio}）"),
    "empty": ("No models in this archive yet: pack a folder or files into it.",
              "這個歸檔還沒有模型：可以把資料夾或檔案壓縮進來。"),
    "need_archive": ("Choose an archive folder first (Open or create…).", "請先選擇歸檔資料夾（開啟／建立…）。"),
    "how_raw": ("as is", "原樣"),
    "how_same": ("same as {ref}", "與 {ref} 相同"),
    "how_delta": ("delta against {base}", "以 {base} 為基底的差異"),
    "how_packed": ("compressed ({method})", "壓縮（{method}）"),
    "model_files": ("{n} file(s)", "{n} 個檔案"),
    "model_needs": ("needs {names}", "需要 {names}"),
    "unreadable": ("cannot be read: {problem}", "無法讀取：{problem}"),
    "bad_name": ("{problem}", "名稱由一或多段以 / 分隔組成，每段只能用文字、數字、空格與 -_.+@()[]，不能以空格開頭或結尾、"
                 "不能以句點結尾，也不能是 CON、NUL 等 Windows 裝置名稱。\n({problem})"),
    "pick_archive": ("Archive folder (an empty folder starts a new archive)", "歸檔資料夾（選空資料夾即建立新歸檔）"),
    "not_archive": ("{folder} holds other things but no models. Use it as an archive anyway? Each model packed goes "
                    "into a folder of its own in it.",
                    "{folder} 裡有其他東西，但沒有任何模型。仍要把它當作歸檔嗎？之後壓縮的每個模型都會在其中各自建立一個資料夾。"),
    "pick_folder": ("Folder to pack", "要壓縮的資料夾"),
    "pick_files": ("Files to pack (from one folder)", "要壓縮的檔案（同一資料夾內）"),
    "pick_dest": ("Extract into (each model goes into a folder named after it)",
                  "解壓縮到（每個模型放在以其名稱命名的資料夾中）"),
    "select_first": ("Select models or files in the list first.", "請先在清單中選取模型或檔案。"),
    "name_title": ("Pack", "壓縮"),
    "name_what": ("{n} file(s), {size}, from {folder}", "{n} 個檔案，共 {size}，來自 {folder}"),
    "name_prompt": ("Name of this model in the archive (e.g. Qwen/Qwen2.5-7B):",
                    "此模型在歸檔中的名稱（例如 Qwen/Qwen2.5-7B）："),
    "name_hint": ("Delta bases are chosen automatically. For other options, use llm_archive.py (docs/usage.md).",
                  "差異基底會自動選擇。其他選項請用命令列 llm_archive.py（見 docs/usage.md）。"),
    "replace_q": ("The archive already holds a model named {name} ({n} file(s)). Packing replaces it with these "
                  "files. Go on?",
                  "歸檔中已有名為 {name} 的模型（{n} 個檔案）。壓縮後會以這次的檔案取代它。要繼續嗎？"),
    "packing": ("Packing {name}", "正在壓縮 {name}"),
    "extracting": ("Extracting {name}", "正在解壓縮 {name}"),
    "verifying": ("Verifying {what}", "正在驗證 {what}"),
    "everything": ("the whole archive", "整個歸檔"),
    "cancelling": ("Cancelling: finishing the current step…", "正在取消：等目前步驟完成…"),
    "force_q": ("It is still finishing the current step. Stop it right away? Unfinished temporary files "
                "(.oc-*.part) may be left behind; they can simply be deleted.",
                "仍在完成目前步驟。要立刻強制停止嗎？可能會留下未完成的暫存檔（.oc-*.part），直接刪除即可。"),
    "quit_q": ("Something is still running. Cancel it and quit?", "還有作業在執行。要取消並離開嗎？"),
    "done_pack": ("Packed {name}.", "已壓縮 {name}。"),
    "done_extract_q": ("Extracted into {dest}. Open that folder?", "已解壓縮到 {dest}。要開啟該資料夾嗎？"),
    "done_verify": ("Everything checked is intact.", "檢查的內容全部正確無誤。"),
    "overwrite_q": ("{dest} already holds some of these files with other contents. Replace them?",
                    "{dest} 裡已有部分同名但內容不同的檔案。要取代它們嗎？"),
    "failed": ("Failed", "失敗"),
    "cancelled": ("Cancelled.", "已取消。"),
}


def chinese():
    choice = os.environ.get("OC_LANG", "").lower()
    if choice:
        return choice.startswith("zh")
    if os.name == "nt":
        import ctypes
        return ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04  # LANG_CHINESE
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            return os.environ[var].lower().startswith("zh")
    return False


ZH = chinese()


def t(key, **values):
    return TEXT[key][ZH].format(**values)


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
        self.buttons = [ttk.Button(top, text=t("choose_archive"), command=self.choose_archive)]
        self.buttons[-1].pack(side="left")

        bar = ttk.Frame(self.root, padding=(self.px(10), 0, self.px(10), self.px(6)))
        bar.pack(fill="x")
        for key, command in (("pack_folder", self.pack_folder), ("pack_files", self.pack_files), (None, None),
                             ("extract", self.extract), ("verify", self.verify), (None, None), ("refresh", self.load)):
            if key is None:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=self.px(6))
                continue
            self.buttons.append(ttk.Button(bar, text=t(key), command=command))
            self.buttons[-1].pack(side="left", padx=(0, self.px(4)))

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
        except OSError as e:
            self.manifests, problems = {}, [(self.archive, e.strerror or str(e))]
        needs = la.repo_needs(self.manifests)
        size = stored = files = 0
        for repo, manifest in self.manifests.items():
            entries = manifest["files"]
            total, kept = sum(e["size"] for e in entries), sum(e["stored_size"] for e in entries)
            size, stored, files = size + total, stored + kept, files + len(entries)
            how = t("model_files", n=len(entries))
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
            self.status.configure(text=t("summary", models=len(self.manifests), files=files, size=size_text(size),
                                         stored=size_text(stored), ratio=ratio(stored, size)))
        else:
            self.status.configure(text=t("empty"))

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
        name = self.ask_name(name, t("name_what", n=len(found), size=size_text(size), folder=folder))
        if not name:
            return
        if name in self.manifests and not messagebox.askyesno(
                t("name_title"), t("replace_q", name=name, n=len(self.manifests[name]["files"])), parent=self.root):
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
                error.configure(text=t("bad_name", problem=problem))
                return
            result.append(name.get().strip())
            dialog.destroy()

        ttk.Button(buttons, text=t("ok"), command=accept, default="active").pack(side="left", padx=(0, self.px(6)))
        ttk.Button(buttons, text=t("cancel"), command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        entry.select_range(0, "end")
        entry.focus_set()
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dialog.wait_visibility()  # a grab needs a window that is shown (X11)
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result[0] if result else None

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
        for widget in self.buttons:
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
        for widget in self.buttons:
            widget.configure(state="normal")
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
