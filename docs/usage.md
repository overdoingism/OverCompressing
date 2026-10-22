# Usage

**English** · [中文](#使用方式)

Everything goes through one script, `tools/llm_archive.py`, with eight commands: `pack`, `list`, `verify`,
`restore`, `remove`, `rename`, `rebase` and `export` (or a small window for most of them: see below). It never
downloads anything: get the model files however you like (Hugging Face, ModelScope, git, a mirror, a USB drive), then
pack the folder. Run it with the Python of a portable copy ([Windows](build-windows.md) / [Linux](build-linux.md)),
or any Python 3.13 with numpy and zstandard. Below, `python` means that interpreter; `-I`
(isolated mode) keeps it from importing stray files from the current folder.

## Two forms of a pack

A pack holds models. Every file is stored once, as a *blob* named by the SHA-256 of the original file and shared by
every model that has that file; a model is its *manifest*, the list of its files. A blob coded as a delta names the
blobs it is coded against. Nothing else ties models together: removing a model deletes only the blobs no remaining
model needs. A pack takes one of two forms, with the same tree inside:

- **Pack folder: for upkeep.** A folder (`packs` below) holding `ocpack.json` (what makes it a pack), `models/` (one
  manifest per model, e.g. `models/Qwen__Qwen2.5-7B.json`) and `blobs/`. Add, re-pack, remove, rename and rebase
  models there, one change at a time. Leave its insides to the tool: copy or move the whole folder, never parts of it.
- **Pack file (`.oczip`): for cold storage.** The same tree in one ZIP file, made by `pack` (straight from model
  folders) or by `export` (from a pack folder). One file to copy, checksum and shelve on optical media, tape or an
  offline drive. `list`, `verify` and `restore` read it as it is, without extracting anything. It is read-only: to
  change it, extract it into a pack folder (any ZIP tool), change that, and export again.

Commands name a pack by its path (the folder, or the `.oczip`) and a model by its name in the pack
(`Qwen/Qwen2.5-7B`; its manifest's file name, `Qwen__Qwen2.5-7B`, works too).

## Typical workflow

```bash
python -I tools/llm_archive.py pack models/Qwen2.5-7B packs --name Qwen/Qwen2.5-7B
python -I tools/llm_archive.py pack models/Qwen2.5-7B-Instruct packs --name Qwen/Qwen2.5-7B-Instruct
python -I tools/llm_archive.py list packs
python -I tools/llm_archive.py verify packs
python -I tools/llm_archive.py restore packs Qwen/Qwen2.5-7B-Instruct restored/Qwen2.5-7B-Instruct
```

Pack the models, look at what needs what, verify, restore when needed. Each file is stored as a delta against the
packed model that compresses it best (here the Instruct model picks Qwen2.5-7B by itself), so pack bases before the
models derived from them; if a base arrives later, `rebase` re-chooses the bases afterwards (section 7). Just want
one model in one file?

```bash
python -I tools/llm_archive.py pack models/Qwen2.5-7B Qwen2.5-7B.oczip
```

## For programs and agents

Every command prints one line per result (a file packed, verified or restored, a model listed) and a summary, and
exits with code 0 only if it did what it was asked. `--json` (any command) makes each of these lines a JSON object
`{"event": ..., "text": <the line people would see>, ...fields}`: `file` (with `path`, `size`, `stored` (its blob),
`added` (new in the pack: 0 if stored already), `method`, `bases`), `ok` / `fail` (verify), `model`, `freed` / `kept` (remove), `summary`, `note`; an error ends the output with
`{"event": "error", "code": ..., "text": ...}` and exit code 1 (an invalid `OC_WORKERS` too; the codes are below). A
command line the tool cannot parse
(an argument missing, an unknown option, a value out of range) is refused before anything runs, the usual way: usage
and the error on stderr, exit code 2, with `--json` too. `list --json` prints the whole pack as one object (models
with what they need and what needs them, blobs kept for others, blobs no model needs); each model has `blobs`, the
SHA-256 of each of its files' blobs with the blob's stored size, so that a sum over several models can count a blob
they share once.

The `code` of an error tells programs what failed; the text is for people and may change, the codes do not:

| code | when | what a program can do |
|---|---|---|
| `exists` | the destination holds something already: files with other contents (restore), the pack file (export, pack) | restore, export: run again with `--overwrite`; pack never writes over a pack file |
| `name_taken` | the pack holds a model of that name (or of the same manifest file, case ignored); rename: the new name or path is taken | pack: `--replace` or `--add`; else another name |
| `busy` | the pack folder is in use by another command | run again when it has finished |
| `no_room` | not enough free space, found before writing or while writing | free space, or point `OC_TMP` elsewhere |
| `damaged` | a blob is damaged or missing: verify found it, or another command met it (restore, export, rebase, remove) | run `verify` for the full report; verify itself gives it first, as a `damage` event (the others do not) |
| `cancelled` | stopped by Ctrl+C, or by the window's Cancel | — |
| `other` | anything else | read the text |

`--progress` reports on stderr, about once a second, how far the current step is: the step (`hashing <model>`,
`packing <model>`, `verifying`, `restoring <model>`, `exporting`, …), bytes done of its total, the file it is on and
the time so far; with `--json` each report is `{"event": "progress", "step", "item", "done", "total", "seconds"}`.
Where nothing can be counted (choosing a base, restoring one), the report still comes and says what it is doing. It is
on by default when stderr is a terminal (one line, rewritten in place). Results never go to stderr, so a program
reading stdout never has to tell the two apart.

To tell whether a pack folder has changed (another program, another agent), look at `ocpack.json`: every command that
changes the pack rewrites it last, with the time in `changed`, so its time stamp moves once per change, when the change
is complete.

Lists of models or files longer than a command line takes (about 32,000 characters on Windows) go in list files:
`--models-from FILE` (verify, remove, rebase, export) adds the models named in FILE, `--files-from FILE` (pack, verify,
restore, remove) the files, as `--file` would; one name per line, as written, UTF-8 (a BOM, CRLF and blank lines do
not matter), e.g. `verify packs --models-from models.txt`. A list that names nothing is refused (no models would
otherwise mean all of them).

## A window instead of commands

```bash
python -I tools/llm_archive_gui.py [<folder> | <pack folder> | <file.oczip>]
```

(On Windows, `pythonw` instead of `python` opens it without a console window; a portable copy starts it with
`OverCompressing.cmd` or `./overcompressing`. It needs tkinter and, on Linux, a graphical desktop: the README's "The
window" says how to get it running.) The window is a file browser, as in
other archivers: it lists the folders and files of a place as they are on disk (**↑** goes up; type a path into the
bar to go there). Double-click a folder to go into it. A pack (a pack folder, or a pack file) opens as the **list of
its models**: when each was packed, its files, size, stored size, ratio and the models it needs, each with how much
of it is read (`Qwen/Qwen2.5-7B (99%)`; a removed model whose files it still needs shows as `(removed)`); click a column heading to sort by it. **⑂ By base** shows them as a tree
instead, each model under the model most of its data is coded against: that is how it is stored, not its family (a
model sits under whatever compresses it best, related or not); **☰ List** goes back. The line under the list sums the
place up: for a pack, what its models would take on disk, what the pack takes and the ratio (`11 models · 60.4 GiB →
37.9 GiB (62.8%) · 75 blobs`), and what is kept for whom; with rows selected, it begins with them (`4 selected:
30.3 GiB → 14.7 GiB (48.7%)`, a blob they share counted once; in a folder, the size of the files selected). Double-click a model to see its files: size, stored size, ratio,
how each is stored (`safetensors+ans16`, `zstd`, …), what it is coded against (`Δ <model>`) and which other models
have the same file (`= also in <model>`). Stored size and ratio are those of the file's blob, the same in every model
that has it, since it is stored once: the line under the list says how much of the model other models have too (`· 1.0 GiB of
it also in other models`), and − Remove says what removing it would free. In the same way, each model of a pack
counts all it needs, so the models add up to more than the pack (the line under the list has the pack's real total). Rows are
tinted by kind: folder, pack folder, pack file, model, weights not compressed. Columns are as wide as what they hold,
as far as the window allows; a text cut short shows whole when the mouse rests on it, and a list wider than the window
scrolls sideways.

**⌕ Filter** (Ctrl+F; Esc or ✕ empties it) keeps, in any list, the rows whose names hold its text, ignoring case; with
⑂ By base it also keeps, in gray, the chain above each, so the tree holds together. Going to another place empties
it.

Select rows with a click, Ctrl+click or Shift+click, by dragging over them (also up from the empty space under the last row; with Ctrl, as well as what is selected), or
all at once with **Ctrl+A**: every row the filter lets through, without the gray ones. A click on empty space, or Esc
in the list, selects nothing. In the path bar or the filter, Ctrl+A selects their text instead.

The place shown is named at the left of the buttons, in the legend's word and color: **Folder**, **Pack folder**,
**Pack file** or **Model** (a model in a pack file adds `in pack file`). The buttons (and the right-click menu) are
those of that place. **↻ Refresh**, **+ Pack in** and **+ Add file** can always be used (where the place can be
changed); every other button acts on what is selected, so it is off while nothing is (and Rename wants one row):

- **In a folder:** **+ Pack to** packs the selected folder, or the selected files of this folder (they then make up
  the model on their own; one file is named after itself by default), into a pack folder you choose (remembered for
  the next pack) or into a new pack file (`.oczip`); a name the pack folder holds already is replaced only after you
  agree. **↻ Refresh**. **− Remove** moves the selection to the Recycle Bin (on Linux, the desktop trash), after
  asking, and never deletes anything for good: where the bin would not take it (a network or removable drive, a bin
  set to delete at once, or one too small for it) it is refused, and you delete it yourself if you mean to. Inside a
  pack folder's own folders (`blobs/`, `models/`), Pack to and Remove are off: a pack changes only through its own
  view.
- **In a pack:** **+ Pack in** asks for a folder and packs it into this pack folder, or into a new pack file, a
  separate, independent pack with no Δ deltas against the models here (to keep those and still have one file, pack
  into the pack folder and Save .oczip the model). **⤓ Save .oczip** copies the selected models (all of them: the
  whole pack), still compressed and with every file they need, into a new pack file, for cold storage or to move them; optionally
  verified, and then removed from the pack folder (section 8); from a pack file, it makes a smaller one. **⤒ Extract
  to** writes the original files of what is selected (models, or files of a model) back out into a folder, to use
  them: the dialog asks where, and which folders to make there: **Full path** (`<dest>/Qwen/Qwen2.5-7B/…`), **Last
  level** (`<dest>/Qwen2.5-7B/…`) or **Files only** (straight into `<dest>`, one model at a time); the choice is kept
  until the window closes. Files with other contents already there are replaced only after you agree, and a
  destination in a pack folder is refused before anything runs (section 5). **✔ Verify** the same choice of models.
  **− Remove** the selected models: it first says what that frees and what stays because other models still need it
  (section 6). **✎ Rename** the selected model. **⇄ Rebase** the selected models (section 7): **Find better bases**
  (each file against the model it is smallest against, changed only where the pack gets smaller; a base kept for
  removed models goes when that saves room; models whose bases were set by hand are left as they are unless ticked)
  or **Make independent** (no bases: they may get larger).
- **In a model:** **+ Add file** adds files from disk to the model, each under its own name at the top of the model
  (one the model has already is replaced, after asking; to put one in a subfolder of the model, use `pack --add` with
  a folder above it). **⤒ Extract to** and **✔ Verify** the selected files (all of them: the whole model).
  **⇄ Rebase** the selected files, as for models (a choice made, Make independent, is then theirs). **− Remove** the
  selected files. **✎ Rename** the selected file (its path in the model).

In a pack file, which is read-only, Pack in, Add file, Remove and Rename are off, so with nothing selected no button
is on.

When ✔ Verify finds damage, its **damage report** opens (section 4): **⧉ Copy** puts it on the clipboard, to keep or
to ask someone (or an AI) what to do; its commands store a lost file again or give it up. Until the pack changes, the
list marks what was lost in Needs (`⚠ failed verify`, `(its base)` for a file lost through what it is coded against;
`⚠ 2 files failed verify` on a model), in red. In a pack folder, **− Clean up** removes what was lost at the level you
type, each shown with what it removes and its risk: **ERASE** only the lost files (a model left with none goes);
**ERASE MOD** the models with lost files, whole; **ERASE FULL** those and the models that need data of them. It runs
only while the pack is as it was verified; verify again afterwards.

The list updates itself: every 5 seconds, and at once when you move the mouse over the window or click in it after
that long, it looks at one time stamp (of the pack folder's `ocpack.json`, of the pack file, or of the folder shown) and
lists the place again only if that changed, keeping what is selected. **F5** (Force refresh) lists it again at once.
Nothing is looked at while the window is minimized or something runs. The keys are named beside the filter.

Each action runs `llm_archive.py` exactly as below and shows its output (a command line too long goes through a
list file, `--models-from` or `--files-from`, deleted after). While it runs, a window in front shows what
runs, its last line, the step and how far it is (`37% · 5.6 GiB / 15.1 GiB`, when the tool can tell) and the time so
far beside a turning ◐, and holds the rest of the window; **✕ Cancel** there, or closing it, stops the run as Ctrl+C
would, so nothing half-done stays in the pack. The run has below-normal priority (`nice` 10 on Linux): it uses every
core left idle and gives way to what you are doing meanwhile. Delta bases are always chosen automatically; `--base`,
`rebase` and the other options are on the command line only. The window's text is English and symbols, whatever the system language.
Started from a portable copy kept in a pack folder as `_kit` (see Portable copy), it opens that pack. Its title shows its own version and that of `llm_archive.py`, each
with its build (the date and commit the file was last changed in; `edited` if it differs from that commit), e.g.
`OverCompressing GUI 0.5 (2026-10-22 1a2b3c4) · CLI 0.5 (2026-10-22 1a2b3c4)`: give both when reporting a problem.
`llm_archive.py --version` prints the second.

## 1. `pack` — compress folders into a pack

```bash
python -I tools/llm_archive.py pack <source_dir> [<source_dir> ...] <pack_folder | new.oczip> [--name NAME] [--replace | --add] [--base MODEL | --no-base] [options]
```

Each `<source_dir>` becomes one model. Every file under it is hashed (SHA-256), parsed (safetensors, GGUF, other) and
compressed per tensor into its blob, unless the pack holds that file already (by its SHA-256): then it is shared, not
stored again. Each new blob is written to a temporary file and decoded back from disk to its SHA-256 before it takes
its name. The model's manifest is written at the end. `.git/` and `.cache/` folders (bookkeeping of git and download
tools), `__MACOSX/` folders (left by macOS when it unzips) and the folder-view caches `Thumbs.db`, `desktop.ini` and
`.DS_Store` are always skipped, unless named with `--file`. The folder must not hold the pack, nor be a pack folder or
inside one: pack original files, from a folder of their own.

A pack folder that does not exist yet, or an empty one (or one holding only `_kit/`), becomes a new pack; any other
folder without `ocpack.json` is refused. Instead of a pack folder, a new pack file can be given, `<name>.oczip` (one
that does not exist yet): it is written as it goes, each blob checked from the unfinished file, and gets its name only
when complete, so it needs no room beyond itself and a stopped pack leaves nothing. Its models can only use each other
as bases: give the related folders in one command (bases first, or name one with `--base`, which then goes first).
A pack file is written whole, never added to: to add a model to one, extract it into a pack folder (it is a ZIP), pack
there, and export again.

| option | meaning |
|---|---|
| `--name NAME` | name of the model in the pack, e.g. `Qwen/Qwen2.5-7B` (default: the folder's name); one folder only. Parts separated by `/`, made of letters, digits, `-_.+@()[]` and spaces; no part may be empty, start or end with a space or end with a dot; not a Windows device name (`CON`, `NUL`, …). Two names that give one manifest file where case is ignored (`A/B`, `a/b`, `A__B`) cannot both be in a pack |
| `--replace` | replace the model of that name in the pack folder with these files (without `--replace` or `--add`, a name the pack folder holds already is refused before anything is written) |
| `--add` | add these files to the model of that name in the pack folder: files of the same path are replaced, the others kept (e.g. to put back one file removed by mistake: `--add --file <path>`); with `--base` or `--no-base`, that choice is these files' own, not the model's |
| `--revision TEXT` | free-text note stored in the manifest, e.g. the upstream commit you downloaded |
| `--exclude GLOB` | skip files whose path or name matches (repeatable), e.g. `--exclude "*.bin"` |
| `--file PATH` | pack only this file of `<source_dir>`, a path relative to it (repeatable): the model then holds just the files named; one folder only, not with `--exclude` |
| `--files-from FILE` | the same for every path listed in FILE, one per line (UTF-8) |
| `--base MODEL` | use this model (of the pack, or packed in the same command) as the base of every file instead of choosing one per file |
| `--no-base` | store every new file without a delta base |
| `--delta-codec ans16\|xor` | for 16-bit float tensors: ans16 (default, smaller) or the XOR with the base in byte planes (faster) |
| `--zstd-level N` | zstd level 1–22 for headers, metadata and text (default 19) |
| `--zstd-tensor-level N` | zstd level 1–22 for quantized tensors and unknown binaries (default 9) |
| `--workers N` | worker processes for ans16, 1–1024, at most 61 on Windows (default: `OC_WORKERS`, else the CPUs it may use) |
| `--chunk-mib N` | maximum region size, 1–1024, default 64 |

Each line of output shows the method, what the file adds to the pack and the ratio of that, and, for deltas, how
much of the file matched the base; a file the pack has already (`stored already`) adds 0.0 MiB. The `total` line is
the whole model (with `--add`, its other files too) and the size of all the blobs it needs, shared ones included; `new
in the pack` sums the file lines. A `base for` line before a file shows the base chosen for it, its estimated ratio,
the runner-up and the standalone estimate:

```
base for model-00002-of-00004.safetensors: Qwen/Qwen2.5-7B (est. 29.2%; standalone 66.2%; 1 candidate(s), 3s)
model-00002-of-00004.safetensors   safetensors+ans16   3,685.7 MiB ->  1,086.0 MiB   29.47%  verified, 100% matched in Qwen/Qwen2.5-7B
tokenizer.json                     zstd                    6.7 MiB ->      0.0 MiB    0.00%  stored already
total 14,525.8 MiB -> 4,380.2 MiB = 30.15%; new in the pack: 4,378.8 MiB
```

The SHA-256 recorded is that of the files as they were in the folder. If your source publishes hashes (Hugging Face
does for weight files), compare them before packing when provenance matters. A file the pack has already is not read
again, so a source that changes during the pack cannot touch it.

**Re-packing** a model under the same name, with `--replace`, replaces it: its new files are stored (those the pack
has already are shared), then the new manifest replaces the old one in one step, then the blobs no model needs any
more are deleted. With `--add`, the model keeps its other files.

After a pack into a pack folder, the models (bases chosen automatically) that may store smaller against what came in
are named: `may store smaller against Qwen/Qwen2.5-7B: org/fine-tune (rebase them to find out)`. The manifests alone
tell it (tensors of the same name and size, at least half of a file, and more than it is coded against now): a model
packed before its base is the usual case. `rebase` finds out, and changes nothing that does not get smaller.
Other models are never affected, even if they shared or were coded against the old version's files: whatever they
need stays. So the pack is complete at every moment, even if `pack` is interrupted. `pack` refuses to change a pack
in which a manifest cannot be read or trusted (`verify` names it): the blobs that model needs would look unused.

If a file the pack has already is damaged there, it is stored again (and every model sharing it is whole again);
`pack` says so.

Only regular files are packed; a link to a file counts as that file. A link to a folder (a symlink, or a junction on
Windows), a FIFO, socket or device, and a broken link stop `pack` before it reads anything, and are listed: skip them
with `--exclude <name>`, or pack the folder a link points to as a folder of its own. Nothing outside the source folder
is packed by accident, and nothing in it is left out without a word. A file may have any name; one only some systems
allow (e.g. a Linux file called `a\b.txt`) is recorded as it is, and `restore` adapts it where needed (section 5).

## 2. How the base is chosen

Without `--base`, every new file picks its own base among the other models of the pack:

1. **Candidates**: every model with tensors that match this file's by name (same format; same dtype and size,
   another float dtype, or the same leading rows; see [How it works](principles.md#4-matching-tensors-not-files)).
   Only manifests are read; nothing is decompressed.
2. **Trial**: 16 windows of the file (512 KiB each, spread over its tensors) are coded against each candidate with
   fast estimates and compared with storing them standalone. Only the parts of a candidate those windows need are
   decoded.
3. **Choice**: the smallest estimate wins if it saves at least 1 % over storing the file standalone; otherwise the
   file gets no base. A delta that, once coded, reads its base for less than 1 % of the file (storing the rest
   without it came out smaller) is stored standalone instead, so that no model needs another for nothing; this also
   holds for `--base`.

Files of one folder may pick different bases (e.g. a BF16 GGUF picks a BF16 GGUF, a Q4_K_M one a Q4_K_M), so a model
may need several models, and several models may need one: what needs what is a graph that grows as you pack, never a
cycle (a blob is never coded against one that needs it) and never deeper than 32 blobs. You do not manage it: `list`
shows it, and `remove` keeps what is still needed.

Measured: every community fine-tune in the [benchmarks](benchmarks.md#automatic-base-selection) picked the base found
by hand, and the estimate was within a few points of the real size; choosing takes a few seconds per file.

**Order matters only through what is already packed.** A model can only use bases packed before it. If a closer
ancestor arrives later (say, the Instruct model after its fine-tune), packed with automatic choice the ancestor picks
the fine-tune as its base, and the fine-tune cannot then use it (that would be a cycle). The pack is about as small
either way round (a delta is about as small in both directions). If you want the ancestor to be restorable on its own,
make it independent first, then rebase the fine-tune (section 7):

```bash
python -I tools/llm_archive.py rebase packs Qwen/Qwen2.5-0.5B-Instruct --no-base
python -I tools/llm_archive.py rebase packs --all
```

`--base MODEL` forces a base (it is used even if it does not help; regions that do not shrink are stored standalone).
`--no-base` keeps a model free of deltas, e.g. a base you want to restore on its own. `tools/compare_tensors.py
<base_file> <target_file>` shows in detail how two files line up (exactly, by dtype conversion, by leading rows,
bit-identical share).

## 3. `list` — what a pack holds

```bash
python -I tools/llm_archive.py list <pack> [MODEL] [--json] [--no-lock]
```

Without a model: every model as a tree (each under the model most of its data is coded against), with its size,
stored size and ratio and the other models it needs; then the blobs kept for models that need them though no model
lists them (files of a removed model) and the blobs no model needs (the next `pack`, `remove` or `rebase` deletes
them):

```
pack folder V:\packs: 3 model(s), 52 blob(s), 31,204.6 MiB stored
  14,525.8 MiB ->  9,638.1 MiB   66.35%  Qwen/Qwen2.5-7B
  14,525.8 MiB ->  4,380.2 MiB   30.15%    Qwen/Qwen2.5-7B-Instruct
  14,525.8 MiB ->  9,640.0 MiB   66.37%  Qwen/Qwen2.5-Coder-7B
```

With a model: its files, each with its stored size, method, what it is coded against and which other models have the
same file. Stored sizes count a shared blob for every model that lists it; the head line's total counts it once.

## 4. `verify`

```bash
python -I tools/llm_archive.py verify <pack> [MODEL ...] [--models-from FILE] [--file PATH ...] [--files-from FILE] [--no-lock]
```

Checks in three passes. First the structure, from the blobs' headers alone: every blob a model needs is there,
names its file, and names each blob it is coded against with the size that one holds; no chain of bases goes round
or deeper than 32. Then the content: every blob is read whole against its own SHA-256 (several at once), and every
file whose chain is sound is decoded in memory and compared with the original SHA-256 (a blob several files share is
decoded once; a file that others being verified are coded against is checked just before the first of them, and what
it restores is kept in `OC_TMP` for them until the last, so it is decoded once too).
Last the structure again, to tell a file whose own blob is at fault from one lost only because of a blob it is coded
against. Without models, every model of
the pack, and it also reports blobs that no model needs. When anything fails, a **damage report** follows: the
blobs at fault; the files lost (`its own blob`, or `through` the file whose blob it is coded against); the models
with lost files, how many of their weight files among them; the models that need data of those; and, for each blob at
fault, its file's SHA-256 and how many lost files storing that original again would heal
(`pack <folder holding it> <pack> --name MODEL --add --file PATH`, which heals everything coded against it too), or
how to give a lost file up (`remove <pack> MODEL --file PATH`). With `--json` it is one `damage` event (`faults`,
`lost`, `models`, `affected`, `heal`, `manifests`). `--file PATH` (repeatable, with one model named) checks only
those files of it, their paths as the pack lists them. Prints `ALL VERIFIED (<n> files)` or each failure with its
cause, and exits with a non-zero code on any failure, and also when it verified nothing (no pack there, or no models
in it). A missing or damaged blob, and a manifest that cannot be read or holds an unsafe path or name, are reported
like any other failure; the rest is still checked. Run it after copying a pack and from time to time on cold storage.

## 5. `restore`

```bash
python -I tools/llm_archive.py restore <pack> MODEL <dest_dir> [--overwrite] [--file PATH ...] [--files-from FILE] [--flat] [--no-lock]
```

Writes the original files of a model, byte-identical, and checks each SHA-256. ans16 deltas first restore the base
files they read into `OC_TMP`. `--file PATH` (repeatable) restores only those files, named by their paths in the pack
(as `verify` lists them after `<name>:`); each lands where a full restore would put it. `--flat` puts every file
straight into `<dest_dir>`, without the model's folders; of two files of one name, the one further down gets ` (1)`
(with `--file`, only the files named count). Restore checks what it writes (each file against its SHA-256, and the
blobs it needs), not the rest of the pack: it can give back the intact files of a pack that `verify` finds faults in,
and a restore that succeeds says nothing of the other models. `verify` checks the pack as a whole.

Before writing anything, every path in the manifest is checked: it must be relative, without `..`, and land inside
`<dest_dir>`; restore never writes through a link, so if a folder on the way below `<dest_dir>` is a symlink or a
junction, it stops. Names this system does not allow are adapted, as other archivers do: on Windows each of
`<>:"\|?*` and control characters becomes `_`, as do trailing dots and spaces, and a device name gets `_` after its
stem (`CON.txt` -> `CON_.txt`). If two files would then be one file (e.g. `a\b.txt` and `a_b.txt`, or `a.txt` and
`A.txt` on Windows), the later one gets ` (1)`, ` (2)`, ... before its extension; files whose names needed no change
keep them. Every renamed file is listed (`ok  a\b.txt  (as a_b (1).txt)`), and the names depend only on the pack, so
a second restore puts every file in the same place. A file already in `<dest_dir>` with the right bytes is kept
(`already there`), so an interrupted restore can simply be run again; a file with other contents stops the restore
unless you add `--overwrite`. Each file is written to a new temporary file with a random name in its folder and moved
into place only once its size and SHA-256 are right, so a failed file never replaces what was there; without
`--overwrite`, a file that appears in the destination meanwhile (another program) is not replaced either, since
checking and placing are one step (a hard link, or where the file system has none, a rename that refuses an existing
name). On the rare file system that has neither (some FUSE or network mounts), restore without `--overwrite` stops
and says so; restore into an empty folder with `--overwrite` there. Restore refuses, even with `--overwrite`, a
destination that is a pack folder or inside one (`_kit` included), where restored files would lie among the pack's
own: restore beside it.

## 6. `remove` and `rename`

```bash
python -I tools/llm_archive.py remove <pack_folder> MODEL [MODEL ...] [--models-from FILE] [--file PATH ...] [--files-from FILE] [--dry-run]
python -I tools/llm_archive.py rename <pack_folder> MODEL NEW_NAME
python -I tools/llm_archive.py rename <pack_folder> MODEL --file PATH NEW_PATH
```

`remove` takes models (or, with `--file`, files of one model) out of the pack folder: their manifests go (or lose
those files), then every blob no remaining model needs is deleted. A blob another model still needs stays: one it
lists as well (the same file), or one its blobs are coded against. `remove` says first what it frees and what it
keeps for whom:

```
removing Qwen/Qwen2.5-7B
  frees 312.4 MiB (9 blob(s))
  keeps 9,325.7 MiB (4 blob(s)) that other models still need: Qwen/Qwen2.5-7B-Instruct (as their base: rebase lets it go when that makes the pack smaller)
removed Qwen/Qwen2.5-7B: 312.4 MiB freed
```

`list` then shows `Qwen/Qwen2.5-7B (removed)` among what the Instruct model needs, and the kept blobs. What is kept
is **trimmed** at once: a blob kept only as a base keeps just the regions (tensors, or pieces of at most 64 MiB) other
files are coded against, and drops the rest (`trimmed … from … blob(s) kept as bases`). It then no longer restores its
file, only what others read of it: a *partial* blob, which only verify (through the files coded against it) ever
reads. Each is written whole, checked, and put in place of the old one in one step; packing its original file again
stores it whole. This happens whenever a pack, remove or rebase cleans up. To free what stays, rebase the models that
need it (section 7): it lets a kept base go when storing them otherwise makes the pack smaller. `--dry-run` only says
what would be freed and kept. Removing every file of a model with `--file` removes the
model.

`rename` gives a model another name (its manifest file is renamed); nothing else changes, since nothing refers to a
model by name. The new name follows the rules of `--name` (section 1) and must not be taken. With `--file`, it gives
a file of the model another path instead (only the manifest changes: the blob is named by the file's content).

A file removed by mistake comes back with `pack <its folder> <pack_folder> --name MODEL --add --file <path>`, from the
original file or a restored copy (e.g. restored from a pack file saved earlier); if another model has the same file, its
blob is still in the pack and nothing needs storing.

## 7. `rebase` — re-choose the bases of packed models

```bash
python -I tools/llm_archive.py rebase <pack_folder> MODEL [MODEL ...] [--models-from FILE] [options]
python -I tools/llm_archive.py rebase <pack_folder> MODEL --file PATH [--file PATH ...] [--files-from FILE] [options]
python -I tools/llm_archive.py rebase <pack_folder> --all [options]
```

Stores the files of models already in the pack again, the way `pack` would store them now: each file with tensors
re-chooses its base among the other models (as in section 2). The original folders are not needed. The base is
chosen from the packed bytes, so only a file whose base changes is restored (into `OC_TMP`), re-packed, decoded back
from disk and checked against its SHA-256. Unless `--base` or `--no-base` is given, a file changes only where the
pack gets smaller: its new blob must be smaller, except where a base kept for removed models is let go (below). It restores the same file as the old one, so it takes the old one's place, under the same name, in one step:
every model that needs it is unaffected, and the pack is complete at every moment, even if `rebase` is interrupted.
Blobs that were bases before and are not needed any more are deleted at the end. A base that would make a cycle (a
model whose blobs are coded, directly or not, against this file) is never chosen, nor one that would make a chain of
bases deeper than 32.

| option | meaning |
|---|---|
| `--all` | every model whose bases were chosen automatically, bases before the models that use them; models packed or rebased with `--base` or `--no-base` are skipped (name them to include them), and so are files that were (name them with `--file`) |
| `--file PATH`, `--files-from FILE` | only these files of the one model named; with `--base` or `--no-base` that choice is then theirs, not the model's: `--all`, and naming the model, leave them as they are; naming them again with `--file` and no option chooses automatically again |
| `--base MODEL` | re-pack every file against this model, even if it gets larger (a file that model needs, directly or not, stays as it is) |
| `--no-base` | re-pack every file without a delta base, even if it gets larger |
| `--dry-run` | show what would change; change nothing |
| `--delta-codec`, `--zstd-level`, `--zstd-tensor-level`, `--workers`, `--chunk-mib` | as for `pack` |

Every file that could change gets a line (unchanged, or how it is stored now and what that replaces), then the
model's stored size before and after (with `--file`, that of the files named: `rebasing b.safetensors of org/model`,
`b.safetensors of org/model: stored …`). Here the Gensyn fine-tune of the 0.5B Instruct model, packed before it, moves
onto it:

```
rebasing mazdaypci/Qwen2.5-0.5B-Instruct-Gensyn-Swarm-wily_padded_mink  (delta base chosen per file)
  base for model.safetensors: Qwen/Qwen2.5-0.5B-Instruct (est. 1.4%; standalone 66.2%; 1 candidate(s), 3s)
  model.safetensors                  safetensors+ans16     942.3 MiB ->     13.7 MiB    1.45%  verified, 100% matched in Qwen/Qwen2.5-0.5B-Instruct  (25s)
    replaces its blob of 627.6 MiB
  13 file(s) without tensors unchanged
  stored 630.6 MiB -> 16.7 MiB  (-97.4%)
```

**Bases kept for removed models.** First, `rebase` weighs each blob kept only as a base whose every user (a file
coded against it) belongs to the models it rebases: what letting it go frees (it, and what only it needs) against how
much those files are estimated to grow, coded against another model or on their own. If that is estimated to save at
least a tenth of its room, it says `letting go of …`, stores those files again however they come out, and the kept
blob goes; otherwise `keeping …`. The last line gives the whole pack before and after (`the pack: 9.4 GiB -> 5.1 GiB
(-45.7%)`), which is what counts: a model stored on its own is larger, the base it no longer needs is gone.

`OC_TMP` needs room for the file being re-packed plus the base files its old or new delta reads (for a 7B shard: two
3.7 GB files), and the pack needs room for the new blob until the old one is replaced (at most one more). `rebase`
checks this before each file.

## 8. `export` — models of a pack into a pack file (`.oczip`)

```bash
python -I tools/llm_archive.py export <pack> <file.oczip> [MODEL ...] [--models-from FILE] [--verify | --no-verify] [--delete] [--overwrite]
```

(The window's **⤓ Save .oczip**.) Puts models of a pack folder (default: all) into one new pack file, still compressed,
with every blob they need: also the blobs of
their bases, so the pack file is complete without the folder (the bases themselves are not listed as models in it
unless named). The `_kit/` goes in too; other files you keep in the pack folder do not. The file is a ZIP (ZIP64),
named `.oczip` on purpose so that it is not taken for an ordinary ZIP; blobs are stored as they are (and checked
against their own SHA-256 while copied), the rest is Deflate-compressed when that makes it smaller. It is written to a
temporary file next to it and put in place only when complete, so a stopped export leaves nothing behind.

| option | meaning |
|---|---|
| `--verify` | read it back before it is put in place: every member compared with what was put in (SHA-256), and every model exported verified from the file as `verify` does. A pack file that fails is not put in place (nor does it replace a file of that name, with `--overwrite`). Off by default |
| `--delete` | then remove those models from the pack folder (as `remove` does: what other models still need stays); exporting every model removes the pack folder (what you keep there yourself stays). Verifies first unless `--no-verify`; if anything fails, nothing is removed |
| `--no-verify` | with `--delete`: remove without verifying first |
| `--overwrite` | replace an existing file of that name |

The pack file must lie outside the pack folder (and outside any other). `export` holds the pack's lock while it runs
(alone with `--delete`), so nothing changes the folder meanwhile. `verify`, `restore` and `list` (and the window) take
the pack file in place of the folder and read it as it is: blobs lie uncompressed in it and are read at their
offsets. To change it, extract it into a folder with any ZIP tool (rename a copy to `.zip` for tools that go by the
extension), work on that folder, and export again.

From a pack file, `export` makes a new, smaller pack file of the models named (with all they need); `--delete` is not
allowed there, since a pack file is read-only.

## 9. Protection and repair

OverCompressing detects damage (every blob's own SHA-256, a CRC per region, every file's SHA-256) but does not repair
it. Bit-level damage is the job of the hardware and the file system (RAID, ECC, ZFS/Btrfs/ReFS); keep copies on more
than one medium: see [protection.md](protection.md).

## Environment variables

| variable | effect |
|---|---|
| `OC_TMP` | folder for restored base files (needs room for the base files a delta reads) and for the file `rebase` re-packs |
| `OC_WORKERS` | cap on ans16 worker processes, a whole number of at least 1 (lower it on small-RAM machines); it also caps the threads that code byte planes and zstd (at most 8) and that hash files (at most 4); at most 61 processes on Windows; any other value (only the digits 0–9 count) stops every command with an error |

## Rules for a healthy pack

- Change a pack folder through the tool only. Deleting or moving blobs or manifests by hand breaks the models that
  need them; `verify` names what is missing. Copy or move the whole pack folder at once.
- One change at a time: `pack`, `remove`, `rename`, `rebase` and `export --delete` lock the whole pack while they run;
  `list`, `verify`, `restore` and `export` can run together but not during a change. Whatever finds the pack in use
  stops at once and says so; run it again later. The lock is the system's lock on the empty file
  `.overcompressing.lock` in the pack folder, released when the program ends, however it ends. Readers open an
  existing lock file read-only, so its owner and permissions do not matter. If they cannot lock at all, they go on
  only when the system reports the pack's volume read-only (a CD/DVD/BD, a write-protected drive, a read-only mount),
  since nothing can change it then; otherwise they stop. `--no-lock` makes them go on anyway: use it only when you
  know nothing changes the pack meanwhile (e.g. a share that is read-only for you but not for others).
- Keep a portable copy of the tool with the pack (in the pack folder as `_kit/`, see below), and keep more than one
  copy on different media.
- Links are never followed below the pack folder or a restore destination: if another program swaps a folder there
  for a symlink or junction while the tool runs, the write is refused instead of landing elsewhere. On Linux and macOS
  this holds at every step (folders are opened one by one without following links); on Windows it is a best effort
  (each folder is checked just before use, and every new file's real location through its own handle before it is
  put in place). Do not let untrusted users write into the pack or the destination while the tool runs.

## Portable copy

The tool needs Python 3.13 with numpy and zstandard. The build guides ([Windows](build-windows.md) /
[Linux](build-linux.md)) make a **portable copy**: one folder with its own Python, the two packages and the tools,
which runs wherever it is copied, without installing anything and without network. Make it once, with network;
afterwards it works offline.

So that a pack can be restored years later on a machine without network, keep a portable copy with it: copy the
folder into the pack folder as `_kit/`. The tool reads nothing in `_kit/` but keeps it with the pack: a folder holding
only `_kit/` can become a pack, restore never writes into it, and `export` puts it into the pack file, so a pack file
carries it too. Started from there (`_kit\OverCompressing.cmd`, `_kit/overcompressing`), the window opens that pack.
With a pack file, first extract its `_kit/` folder with any ZIP tool (7-Zip opens it as it is; for Windows Explorer,
rename a copy to `.zip`), then run the tool from it and give it the pack file.

An earlier version made an "offline kit" with install scripts instead; it is no longer made, and its scripts are gone
from the repository. A `_kit/` of that kind in an older pack still works as its own `README.md` says.

## Troubleshooting

| symptom | fix |
|---|---|
| `its blob is damaged` / `… is missing from the pack` | replace the pack from another copy; if you still have the original files, packing them again stores a damaged file again |
| out of disk during restore/verify of a delta, or `not enough room to rebase` | set `OC_TMP` to a folder with more space |
| out of memory | set `OC_WORKERS` to 4 or less |
| leftover `oc-base-*.bin` or `oc-rebase-*` in the temp folder after a killed run | delete it |
| leftover `.oc-*.part` after a killed run | delete it, or let the next `pack`, `remove` or `rebase` do it |
| `already exist … with other contents` (restore) | restore into an empty folder, or add `--overwrite` |
| `models/<name>.json in …: …` when changing a pack | restore that manifest from another copy, or move it out of `models/` |
| `… is not a pack folder (no ocpack.json in it) and holds other files` | pack into a new or empty folder, or into an existing pack folder |
| `… is a link or not a folder: not followed` | a folder in the pack or the destination is a symlink or junction: replace it with a real folder (or restore elsewhere) |
| `a file of that name appeared in the destination while restoring` | another program wrote there: run restore again, or add `--overwrite` |
| `cannot put … in place … without the risk of replacing a file` | the file system has no hard links and no rename that refuses an existing name: restore into an empty folder with `--overwrite`; keep the pack itself on another file system |
| `cannot pack … is a pack folder` / `… is inside the pack folder …` (pack) | pack the original files, from a folder of their own |
| `refusing to restore there: …` | restore into a folder of its own, outside any pack folder |
| `… is a pack file: it is read-only` | extract it into a pack folder (it is a ZIP), change it there, then `export` again |
| `… exists already: a pack file is written whole, never added to` (pack) | pack into a new pack file, or extract it into a pack folder, pack there and `export` again |
| `… is compressed or encrypted in the pack file: blobs must be stored as they are` | the pack file was rewritten by another ZIP tool: make it again with `export` from the folder |
| `the pack folder is in use …` or `… is being changed …` | another command runs on the pack: run this when it has finished |
| `… is a link to a folder`, `… is not a regular file`, `… cannot be read` (pack) | skip it with `--exclude`, or pack the link's target as a folder of its own |
| `the name … is taken` | choose another `--name`: that name and an existing one give one manifest file where case is ignored |
| `… holds a model named … already` (pack) | add `--replace` to replace that model, or `--add` to add the files to it |

---

# 使用方式

[English](#usage) · **中文**

所有功能都在 `tools/llm_archive.py` 這支程式裡，共八個指令：`pack`、`list`、`verify`、`restore`、`remove`、`rename`、
`rebase`、`export`（大部分也可以用圖形介面，見下文）。本工具不會下載任何東西：請用你習慣的方式取得模型檔（Hugging Face、
ModelScope、git、鏡像站、隨身碟皆可），再打包整個資料夾。請用可攜版的 Python（依建置頁面
[Windows](build-windows.md) / [Linux](build-linux.md) 做出），或任何裝有 numpy 與 zstandard 的 Python 3.13 來執行。以下的
`python` 指的都是那個直譯器；
`-I`（隔離模式）可避免它載入目前資料夾裡來路不明的檔案。

## Pack 的兩種形態

pack 用來存放模型。每個檔案只存一份，稱為 *blob*，以原始檔的 SHA-256 命名，所有擁有這個檔案的模型共用它；一個模型就是它的
*manifest*，也就是它的檔案清單。以差異方式存放的 blob 會記下它以哪些 blob 為基底。模型之間沒有其他牽連：移除一個模型時，
只會刪掉其餘模型都用不到的 blob。pack 有兩種形態，裡面的結構相同：

- **pack 資料夾：便於高效能維護。** 一個資料夾（以下為 `packs`），裡面有 `ocpack.json`（有它才是 pack）、`models/`（每個
  模型一份 manifest，例如 `models/Qwen__Qwen2.5-7B.json`）與 `blobs/`。模型的新增、重新打包、移除、改名與 rebase 都在這裡
  進行，一次一項。內部請交給工具管理：要複製或搬移就整個資料夾一起，不要只動其中一部分。
- **pack 檔（`.oczip`）：便於冷儲存管理。** 同樣的結構裝進一個 ZIP 檔，可以用 `pack` 直接從模型資料夾產生，或用 `export`
  從 pack 資料夾匯出。只有一個檔案，方便複製、計算校驗、放到光碟、磁帶或離線硬碟保存。`list`、`verify` 與 `restore` 可以
  直接讀取，不必解開。它是唯讀的：要修改時，用任何 ZIP 工具解開成 pack 資料夾，在其中修改後再 export。

指令以路徑指定 pack（資料夾或 `.oczip`），以 pack 中的名稱指定模型（`Qwen/Qwen2.5-7B`；用它 manifest 的檔名
`Qwen__Qwen2.5-7B` 也可以）。

## 典型流程

```bash
python -I tools/llm_archive.py pack models/Qwen2.5-7B packs --name Qwen/Qwen2.5-7B
python -I tools/llm_archive.py pack models/Qwen2.5-7B-Instruct packs --name Qwen/Qwen2.5-7B-Instruct
python -I tools/llm_archive.py list packs
python -I tools/llm_archive.py verify packs
python -I tools/llm_archive.py restore packs Qwen/Qwen2.5-7B-Instruct restored/Qwen2.5-7B-Instruct
```

依序打包模型、看看誰需要誰、驗證、需要時還原。每個檔案會自動以 pack 中「壓得最小」的模型為 base 存成差異檔（這裡 Instruct
會自己選到 Qwen2.5-7B），所以 base 要比衍生自它的模型先打包；若 base 較晚才加入，可事後用 `rebase` 重新挑選（第 7 節）。
只想把一個模型收成一個檔案？

```bash
python -I tools/llm_archive.py pack models/Qwen2.5-7B Qwen2.5-7B.oczip
```

## 給程式與 AI 代理使用

每個指令對每項結果（打包、驗證或還原的一個檔案、列出的一個模型）印一行，最後印一行總結；只有完成了要求的工作，結束碼
才是 0。`--json`（任何指令都可用）把這些行各自改成一個 JSON 物件 `{"event": ..., "text": <給人看的那一行>, ...欄位}`：
`file`（含 `path`、`size`、`stored`（它的 blob）、`added`（新存入 pack 的量：已存在時為 0）、`method`、`bases`）、`ok` / `fail`（verify）、`model`、`freed` / `kept`（remove）、
`summary`、`note`；發生錯誤時，最後一行是 `{"event": "error", "code": ..., "text": ...}`，結束碼為 1（`OC_WORKERS` 無效時
也是；code 見下文）。
無法解析的命令列（缺少參數、未知選項、超出範圍的值）在執行任何動作之前就被拒絕，照一般方式處理：stderr 印出用法與錯誤，
結束碼 2，加上 `--json` 也一樣。`list --json` 把整個 pack 印成一個物件（各模型需要誰、被誰需要，為其他模型保留的 blob，
以及沒有模型需要的 blob）；每個模型都有 `blobs`：它每個檔案的 blob 的 SHA-256 與該 blob 的儲存大小，加總多個模型時，
它們共用的 blob 就能只算一次。

錯誤事件的 `code` 告訴程式是什麼失敗；文字是給人看的，可能會改，code 不會改：

| code | 什麼時候 | 程式可以 |
|---|---|---|
| `exists` | 目的地已有東西：內容不同的檔案（restore）、該 pack 檔（export、pack） | restore、export：加 `--overwrite` 重跑；pack 絕不覆寫 pack 檔 |
| `name_taken` | pack 裡已有同名模型（或大小寫不計時同一個 manifest 檔名）；rename：新名稱或新路徑已被使用 | pack：改用 `--replace` 或 `--add`；其他：換個名稱 |
| `busy` | pack 資料夾正被另一個指令使用 | 等它結束再跑 |
| `no_room` | 空間不足，寫入前或寫入途中發現 | 清出空間，或把 `OC_TMP` 指到別處 |
| `damaged` | 有 blob 損毀或遺失：verify 發現，或其他指令（restore、export、rebase、remove）途中遇到 | 執行 `verify` 取得完整報告；只有 verify 自己會先輸出它（`damage` 事件），其他指令不會 |
| `cancelled` | 被 Ctrl+C 或視窗的 Cancel 中止 | — |
| `other` | 其他所有情況 | 看文字 |

`--progress` 會在 stderr 上大約每秒報告一次目前步驟的進度：步驟名稱（`hashing <模型>`、`packing <模型>`、`verifying`、
`restoring <模型>`、`exporting` 等）、已完成與總量的位元組、正在處理的檔案、已經過的時間；搭配 `--json` 時，每次報告是
`{"event": "progress", "step", "item", "done", "total", "seconds"}`。無法計量的步驟（挑選 base、還原 base）也照樣定時報告，
說明正在做什麼。stderr 是終端機時預設開啟（同一行原地更新）。結果一律不會寫到 stderr，讀 stdout 的程式不必分辨兩者。

要判斷 pack 資料夾有沒有被改動（其他程式、其他 AI 代理），看 `ocpack.json` 即可：每個會改動 pack 的指令都會在最後一步重寫它，
`changed` 欄位記錄時間，所以每次改動完成時，它的時間戳會變一次。

模型或檔案清單長到超過命令列上限時（Windows 約 32,000 個字元），改用清單檔：`--models-from 檔案`（verify、remove、rebase、
export）加入檔案裡列出的模型，`--files-from 檔案`（pack、verify、restore、remove）加入檔案裡列出的檔案，效果同 `--file`；
一行一個名稱，照原樣讀取，UTF-8（BOM、CRLF 換行與空行都不影響），例如 `verify packs --models-from models.txt`。清單裡一個名稱
都沒有時會拒絕執行（否則「沒有指定模型」會變成「全部模型」）。

## 圖形介面

```bash
python -I tools/llm_archive_gui.py [<資料夾> | <pack 資料夾> | <檔名.oczip>]
```

（Windows 上把 `python` 換成 `pythonw` 就不會多開一個主控台視窗；可攜版則用 `OverCompressing.cmd` 或 `./overcompressing`
啟動。視窗需要 tkinter，Linux 上還需要圖形桌面：怎麼讓它能執行，見 README 的「The window / 視窗」。）視窗是一個檔案瀏覽器，和其他壓縮軟體一樣：照磁碟上的
樣子列出某個位置的資料夾與檔案（**↑** 回上一層；也可以在路徑列直接輸入路徑前往）。雙擊資料夾進入。pack（pack 資料夾或
pack 檔）會開成**模型清單**：列出每個模型的打包時間、檔案數、大小、儲存大小、比例，以及它需要的其他模型，各附用到其中多少
（`Qwen/Qwen2.5-7B (99%)`；已移除、但仍需要其檔案的模型會標成 `(removed)`）；按欄位標題即可依該欄排序。**⑂ By base**（依存放基底）改成樹狀顯示，每個模型掛在它大部分資料
所依據的模型底下：這是它的存放方式，不是血緣（模型會掛在壓得最小的那個底下，不論是否同一家族）；**☰ List** 切回清單。清單
下方那一行是目前位置的總覽：在 pack 裡，是各模型在磁碟上原本會佔的大小、pack 實際佔用的大小與比例
（`11 models · 60.4 GiB → 37.9 GiB (62.8%) · 75 blobs`），以及為誰保留了多少；有選取時，開頭先列出選取部分
（`4 selected: 30.3 GiB → 14.7 GiB (48.7%)`，選取的模型之間共用的 blob 只算一次；在一般資料夾裡，則是選取檔案的大小）。雙擊模型可看它的檔案：大小、儲存大小、比例、儲存方式
（`safetensors+ans16`、`zstd` 等）、以誰為基底（`Δ <模型>`），以及哪些其他模型有相同的檔案（`= also in <模型>`）。儲存大小與
比例是該檔案 blob 的大小與比例；blob 只存一份，所以每個有這個檔案的模型都顯示相同的數字：清單下方那一行會說明這個模型有多少也在其他
模型裡（`· 1.0 GiB of it also in other models`），− Remove 則會說明移除它實際能釋放多少。同樣地，pack 中每個模型都計入它需要
的全部內容，所以各模型加總會大於整個 pack（實際總量見清單下方那一行）。每一列依種類上底色：資料夾、pack 資料夾、pack 檔、模型、未壓縮
的權重檔。欄寬依內容而定，在視窗寬度內盡量完整顯示；被截斷的文字，滑鼠停在上面時會顯示全文，清單比視窗寬時可以左右捲動。

**⌕ Filter**（過濾；Ctrl+F，按 Esc 或 ✕ 清空）在任何清單中只留下名稱含有該文字的列，不分大小寫；在 ⑂ By base 模式下，還會以灰字
保留每一列往上的整條鏈，讓樹不會斷掉。前往其他位置時，過濾條件會清空。

選取方式：點擊、Ctrl+點擊、Shift+點擊、在列上拖動（也可以從最後一列下方的空白處往上拖；按住 Ctrl 拖動是加選），或按 **Ctrl+A** 一次全選：過濾後留下的所有列，不含
灰色列。點清單的空白處，或在清單上按 Esc，就取消選取。在路徑列或過濾框裡按 Ctrl+A，則是全選該欄的文字。

按鈕列最左邊會標出目前所在的位置，用的是右上色標的字與顏色：**Folder**、**Pack folder**、**Pack file** 或 **Model**（pack 檔
裡的模型會再加註 `in pack file`）。按鈕（與右鍵選單）依所在位置而不同。**↻ Refresh**、**+ Pack in** 與 **+ Add file** 隨時可按
（限於可以修改的位置）；其他按鈕都作用在選取的項目上，所以沒有選取時會停用（Rename 要剛好選一列）：

- **在一般資料夾：** **+ Pack to** 把選取的資料夾，或這個資料夾中選取的檔案（這些檔案自成一個模型；只選一個檔案時，預設以檔名
  命名），壓縮進你指定的 pack 資料夾（下次壓縮會記住），或壓成一個新的 pack 檔（`.oczip`）；pack 資料夾裡已有同名模型時，經你
  同意才會取代。**↻ Refresh** 重新整理。**− Remove** 先詢問，再把選取的項目移到資源回收筒（Linux 上是桌面的垃圾桶），絕不會
  永久刪除：資源回收筒收不下的（網路磁碟或卸除式磁碟、設定成直接刪除的資源回收筒，或空間不夠時）一律拒絕，真要刪除請自己動手。
  在 pack 資料夾內部的資料夾（`blobs/`、`models/`）裡，Pack to 與 Remove 會停用：pack 只能在它自己的畫面裡修改。
- **在 pack 裡：** **+ Pack in** 詢問要打包哪個資料夾，打包進這個 pack 資料夾，或存成一個新的 pack 檔：另存成一個獨立的 pack，
  不會以這裡的模型為 Δ 基底（想保留差異、又要單一檔案，請打包進 pack 資料夾，再對該模型按 Save .oczip）。**⤓ Save .oczip** 把
  選取的模型（全選時即整個 pack）維持壓縮狀態，連同它們需要的所有檔案，複製成一個新的 pack 檔，用來冷儲存或搬移；可選擇驗證，以及之後從
  pack 資料夾移除（第 8 節）；在 pack 檔裡按，則是另存成一個較小的 pack 檔。**⤒ Extract to** 把選取項目（模型，或模型中的檔案）
  的原始檔寫回一個資料夾，拿去使用：對話框會問放到哪裡、以及要建立哪些資料夾：**Full path**（`<目的地>/Qwen/Qwen2.5-7B/…`）、
  **Last level**（`<目的地>/Qwen2.5-7B/…`）或 **Files only**（直接放進 `<目的地>`，一次限一個模型）；選擇會保留到關閉視窗為止。
  目的地已有內容不同的同名檔案時，經你同意才會取代；目的地若在 pack 資料夾裡，會在執行前就拒絕（第 5 節）。**✔ Verify** 驗證同樣
  方式選出的模型。**− Remove** 移除選取的模型：會先說明能釋放多少、又有多少因為其他模型仍需要而保留（第 6 節）。**✎ Rename** 為
  選取的模型改名。**⇄ Rebase** 對選取的模型重新挑選 base（第 7 節）：**Find better bases**（每個檔案改以壓得最小的模型為 base，
  只在 pack 變小時才改；為已刪除模型保留的 base，在能省下空間時一併釋放；base 是手動指定的模型除非勾選，否則保持不動），或
  **Make independent**（不用 base：可能變大）。
- **在模型裡：** **+ Add file** 把磁碟上的檔案加入這個模型，各以自己的檔名放在模型最上層（模型裡已有同名檔案時，詢問後取代；要放進
  模型的子資料夾，請用 `pack --add`，以它的上層資料夾為來源）。**⤒ Extract to** 與 **✔ Verify** 針對選取的檔案（全選時即
  整個模型）。**⇄ Rebase** 對選取的檔案重新挑選 base，方式同模型（此時做的選擇，例如 Make independent，只屬於這些檔案）。**− Remove** 移除
  選取的檔案。**✎ Rename** 改選取檔案在模型中的路徑。

在 pack 檔裡（唯讀），Pack in、Add file、Remove 與 Rename 會停用，所以沒有選取時，沒有任何按鈕可按。

✔ Verify 發現損毀時，會開啟它的**損毀報告**（第 4 節）：**⧉ Copy** 把整份報告複製到剪貼簿，可以留存，或拿去問人（或 AI）
該怎麼處理；報告裡的指令可以把遺失的檔案重新存回，或放棄它。在 pack 改變之前，清單會在 Needs 欄以紅字標出遺失的項目
（檔案標 `⚠ failed verify`，因所依據的檔案壞掉而遺失的再加註 `(its base)`；模型標 `⚠ 2 files failed verify`）。在 pack 資料夾中，
**− Clean up** 依你輸入的等級移除遺失的部分，每一級都會先列出會移除什麼與風險：**ERASE** 只移除遺失的檔案（檔案全被移除的模型
也一併移除）；**ERASE MOD** 把有遺失檔案的模型整個移除；**ERASE FULL** 再加上需要這些模型資料的其他模型。只有在 pack 與驗證時
相同時才會執行；完成後請再驗證一次。

清單會自己更新：每 5 秒，以及滑鼠在視窗上移動或點擊時（距上次超過 5 秒），檢查一個時間戳（pack 資料夾的 `ocpack.json`、pack 檔，
或目前資料夾本身），有變才重新列出，並保留選取。按 **F5**（Force refresh）立即重新整理。視窗最小化或有工作正在執行時，不做檢查。快捷鍵說明在過濾框
那一列的右側。

每個動作都是照下文的方式執行 `llm_archive.py`，並顯示它的輸出（命令列太長時，改用清單檔 `--models-from` 或 `--files-from` 傳遞，結束後刪除）。執行期間會有一個置前的視窗，顯示正在做什麼、最後一行輸出、
目前步驟與進度（工具能計量時，例如 `37% · 5.6 GiB / 15.1 GiB`），以及轉動的 ◐ 旁的已執行時間，主視窗則暫時無法操作；在那個
視窗按 **✕ Cancel** 或關掉它，效果等同按 Ctrl+C，pack 裡不會留下做一半的東西。執行的優先權是「低於標準」（Linux 上為
`nice` 10）：閒置的核心都會用上，你同時在用電腦時則讓給你的程式。差異基底一律自動選擇；`--base`、`rebase`
等其他選項只能用命令列。介面文字只有英文與符號，與系統語系無關。從放在 pack 資料夾裡的可攜版（`_kit`，見「可攜版」）啟動時，會直接開啟那個 pack。視窗標題會顯示它自己和 `llm_archive.py` 的版本，各附 build（該檔案最後一次變更的
commit 日期與編號；與該 commit 不同時加註 `edited`），例如 `OverCompressing GUI 0.5 (2026-10-22 1a2b3c4) · CLI 0.5 (2026-10-22 1a2b3c4)`。
回報問題時請附上這兩個版本。`llm_archive.py --version` 會印出後者。

## 1. `pack`：把資料夾打包進 pack

```bash
python -I tools/llm_archive.py pack <來源資料夾> [<來源資料夾> ...] <pack_folder | 新檔名.oczip> [--name 名稱] [--replace | --add] [--base 模型 | --no-base] [選項]
```

每個 `<來源資料夾>` 成為一個模型。資料夾底下的每個檔案都會先計算 SHA-256、解析格式（safetensors、GGUF、其他）、逐 tensor
壓縮成它的 blob；但 pack 裡已經有這個檔案（依 SHA-256）時，就直接共用，不再存一次。每個新的 blob 都先寫到暫存檔，從磁碟
解回並核對 SHA-256，確認無誤才換上正式名稱。最後寫出該模型的 manifest。`.git/` 與 `.cache/` 資料夾（git 與下載工具的管理
資料）、`__MACOSX/` 資料夾（macOS 解壓縮時留下的），以及資料夾檢視快取檔 `Thumbs.db`、`desktop.ini`、`.DS_Store` 一律略過，
除非用 `--file` 明確指定。來源資料夾不可包含 pack，也不可本身就是 pack 資料夾或在其中：請從原始檔案自己的資料夾打包。

尚不存在的資料夾，或空資料夾（或只有 `_kit/` 的資料夾），會成為新的 pack；其他沒有 `ocpack.json` 的資料夾一律拒絕。也可以
不給 pack 資料夾，而給一個新的 pack 檔 `<名稱>.oczip`（尚不存在的）：它是邊壓邊寫的，每個 blob 都從尚未完成的檔案讀回檢查，
完整寫完才換上正式名稱，所以除了它本身不需要額外空間，中途停止也不會留下任何東西。檔案裡的模型只能彼此當基底：請把相關的
資料夾放在同一個指令裡（base 放前面，或用 `--base` 指定，指定的那個會先打包）。pack 檔是一次寫完的，不能追加：要加入模型，
請把它解開成 pack 資料夾（它是 ZIP），在其中打包，再重新 export。

| 選項 | 說明 |
|---|---|
| `--name 名稱` | 模型在 pack 中的名稱，例如 `Qwen/Qwen2.5-7B`（預設為資料夾名稱）；限一個資料夾。以 `/` 分段，每段只能用文字、數字、`-_.+@()[]` 與空白；不可為空、不可以空白開頭或結尾、不可以句點結尾；不可為 Windows 裝置名稱（`CON`、`NUL` 等）。在不分大小寫的系統上會對應到同一個 manifest 檔的兩個名稱（`A/B`、`a/b`、`A__B`）不能同時存在 |
| `--replace` | 用這些檔案取代 pack 資料夾中同名的模型（沒有 `--replace` 或 `--add` 時，pack 資料夾中已有的名稱會在寫入任何東西之前被拒絕） |
| `--add` | 把這些檔案加入 pack 資料夾中同名的模型：同路徑的檔案被取代，其餘保留（例如補回誤刪的一個檔：`--add --file <路徑>`）；搭配 `--base` 或 `--no-base` 時，這個選擇只屬於這些檔案，不屬於整個模型 |
| `--revision 文字` | 寫進 manifest 的備註，例如你下載的上游 commit |
| `--exclude 樣式` | 略過路徑或檔名符合的檔案（可重複指定），例如 `--exclude "*.bin"` |
| `--file 路徑` | 只打包 `<來源資料夾>` 裡的這個檔案，路徑相對於該資料夾（可重複指定）：模型就只包含指定的檔案；限一個資料夾，不可與 `--exclude` 併用 |
| `--files-from 檔案` | 同上，對檔案裡列出的每個路徑，一行一個（UTF-8） |
| `--base 模型` | 所有檔案都以這個模型（pack 中已有的，或同一個指令中打包的）為 base，不再逐檔自動挑選 |
| `--no-base` | 所有新檔案都不使用 base |
| `--delta-codec ans16\|xor` | 16-bit 浮點 tensor 的差異方式：ans16（預設，較小）或與 base 的 XOR 以位元平面壓縮（較快） |
| `--zstd-level N` | 標頭、metadata、文字檔的 zstd 等級，1–22（預設 19） |
| `--zstd-tensor-level N` | 量化 tensor 與未知二進位檔的 zstd 等級，1–22（預設 9） |
| `--workers N` | ans16 平行程序數，1–1024，Windows 上最多 61（預設為 `OC_WORKERS`，未設定則為可使用的 CPU 數） |
| `--chunk-mib N` | 單一區塊上限，1–1024，預設 64 MiB |

輸出的每一行會顯示壓縮方式、這個檔案新增到 pack 的大小與其比例，以及差異檔有多少比例在 base 裡找到對應；pack 裡已有的檔案
（`stored already`）新增 0.0 MiB。`total` 行是整個模型（用 `--add` 時也包含它原有的檔案）與它需要的所有 blob 的大小，共用的
也算在內；`new in the pack` 是各檔案行的加總。檔案前的 `base for` 行顯示為它挑選的 base、估算比例、次佳候選，以及單獨壓縮的
估算：

```
base for model-00002-of-00004.safetensors: Qwen/Qwen2.5-7B (est. 29.2%; standalone 66.2%; 1 candidate(s), 3s)
model-00002-of-00004.safetensors   safetensors+ans16   3,685.7 MiB ->  1,086.0 MiB   29.47%  verified, 100% matched in Qwen/Qwen2.5-7B
tokenizer.json                     zstd                    6.7 MiB ->      0.0 MiB    0.00%  stored already
total 14,525.8 MiB -> 4,380.2 MiB = 30.15%; new in the pack: 4,378.8 MiB
```

記錄的 SHA-256 是檔案在資料夾裡當下的雜湊值。若來源有公布雜湊值（Hugging Face 對權重檔有公布），在意來源正確性時，請在
打包前自行比對。pack 裡已有的檔案不會再讀一次，所以打包途中來源被改動也碰不到它。

以相同名稱、加上 `--replace` **重新打包**會取代該模型：先存入它的新檔案（pack 裡已有的就共用），新 manifest 再一次取代舊
manifest，之後才刪除沒有任何模型還需要的 blob。改用 `--add` 時，模型的其他檔案會保留。

打包進 pack 資料夾後，會列出可能以新模型為 base 而變小的現有模型（base 為自動挑選者）：`may store smaller against
Qwen/Qwen2.5-7B: org/fine-tune (rebase them to find out)`。這只比對 manifest（名稱與大小相同的 tensor 佔檔案至少一半，而且多於
它目前以 base 編碼的部分），典型情況是模型比它的 base 先打包。實際能否變小由 `rebase` 判定，沒有變小的不會更動。其他模型完全不受影響，即使它們共用過、或以舊版的檔案為基底：它們需要的都會保留。所以即使 `pack`
中途被打斷，pack 在任何時刻都是完整的。pack 中若有無法讀取或不可信的 manifest（`verify` 會指出是哪個），`pack` 會拒絕修改
這個 pack：否則那個模型需要的 blob 會看起來像沒人用。

若 pack 裡已有的某個檔案在 pack 中已經損毀，會重新儲存一份（共用它的所有模型也就一起恢復完整）；`pack` 會提醒。

只有一般檔案會被打包；指向檔案的連結視同該檔案。指向資料夾的連結（symlink，或 Windows 的 junction）、FIFO、socket、裝置檔，
以及失效的連結，都會讓 `pack` 在讀取任何東西之前停止並列出：請用 `--exclude <名稱>` 略過，或把連結指向的資料夾另外打包。
這樣不會意外收進來源資料夾以外的東西，也不會不聲不響地漏掉資料夾裡的東西。檔名可以是任何名稱；只有某些系統允許的檔名
（例如 Linux 上名為 `a\b.txt` 的檔案）會照原名記錄，由 `restore` 視需要調整（第 5 節）。

## 2. 如何挑選 base

不指定 `--base` 時，每個新檔案會各自從 pack 中的其他模型挑選 base：

1. **候選**：所有 tensor 能依名稱與此檔配對的模型（同格式；dtype 與大小相同、其他浮點 dtype，或前段列相同，詳見
   [原理](principles.md#4-matching-tensors-not-files)）。只讀 manifest，不解壓任何東西。
2. **試算**：從檔案的 tensor 中平均取 16 段（每段 512 KiB），用快速估算對每個候選試壓，並與單獨壓縮比較。候選模型只解開
   這些片段需要的部分。
3. **決定**：估算最小者勝出，但必須比單獨壓縮至少省下 1%，否則不用 base。實際編碼後，若 delta 只有不到檔案 1% 的部分
   真正用到 base（其餘不用 base 反而更小），就改成單獨存放，免得某個模型平白依賴另一個；指定 `--base` 時也一樣。

同一個資料夾裡的不同檔案可以選不同的 base（例如 BF16 GGUF 選 BF16 GGUF，Q4_K_M 選 Q4_K_M），所以一個模型可能需要好幾個模型，
好幾個模型也可能需要同一個：誰需要誰是一張隨著打包自然長出來的圖，不會有循環（blob 絕不以需要它的 blob 為基底），深度也不超過
32 層。你不必管理它：`list` 會顯示，`remove` 會保留仍被需要的部分。

實測：[benchmarks](benchmarks.md#automatic-base-selection) 中的社群微調模型全部選到與手動挑選相同的 base，估算值與實際大小
只差幾個百分點；每個檔案挑選約需數秒。

**順序只透過「pack 裡已有什麼」產生影響。** 模型只能用比它先打包的模型當 base。若之後才加入更近的祖先（例如先收了微調模型、
後收 Instruct），以自動挑選打包時，祖先會選該微調模型當 base，而微調模型就不能再以祖先為 base（那會形成循環）。兩種方向的
pack 大小差不多（差異檔正反方向一樣小）。若希望祖先能單獨還原，先讓它獨立，再對微調模型 rebase（第 7 節）：

```bash
python -I tools/llm_archive.py rebase packs Qwen/Qwen2.5-0.5B-Instruct --no-base
python -I tools/llm_archive.py rebase packs --all
```

`--base 模型` 可強制指定 base（即使沒有幫助也照用；壓不小的區塊仍會單獨儲存）。`--no-base` 讓模型不使用差異，例如希望某個
base 能單獨還原時。`tools/compare_tensors.py <base 檔案> <目標檔案>` 可詳細列出兩個檔案的對應情形（完全相同、dtype 轉換、
前段列、權重完全相同的比例）。

## 3. `list`：pack 裡有什麼

```bash
python -I tools/llm_archive.py list <pack> [模型] [--json] [--no-lock]
```

不指定模型時：所有模型排成樹狀（每個模型掛在它大部分資料所依據的模型底下），列出大小、儲存大小、比例，以及它需要的其他模型；
接著是「為需要它們的模型保留、但沒有任何模型列出」的 blob（已移除模型的檔案），以及沒有任何模型需要的 blob（下一次 `pack`、
`remove` 或 `rebase` 會刪除）：

```
pack folder V:\packs: 3 model(s), 52 blob(s), 31,204.6 MiB stored
  14,525.8 MiB ->  9,638.1 MiB   66.35%  Qwen/Qwen2.5-7B
  14,525.8 MiB ->  4,380.2 MiB   30.15%    Qwen/Qwen2.5-7B-Instruct
  14,525.8 MiB ->  9,640.0 MiB   66.37%  Qwen/Qwen2.5-Coder-7B
```

指定模型時：列出它的每個檔案，以及儲存大小、儲存方式、以誰為基底、哪些其他模型有相同的檔案。共用的 blob 在每個列出它的模型
各算一次；第一行的總計只算一次。

## 4. `verify`：驗證

```bash
python -I tools/llm_archive.py verify <pack> [模型 ...] [--models-from 檔案] [--file 路徑 ...] [--files-from 檔案] [--no-lock]
```

分三輪檢查。先檢查結構，只讀 blob 的標頭：模型需要的每個 blob 都在、標頭指向正確的檔案、它所依據的每個 blob 大小相符，
而且基底鏈沒有繞成一圈、也沒有超過 32 層。再檢查內容：每個 blob 整個讀過、核對它自己的 SHA-256（多個同時進行），基底鏈完好的
檔案在記憶體中解碼並比對原始 SHA-256（多個檔案共用的 blob 只解碼一次；其他受驗檔案所依據的檔案，會排在第一個依據它的檔案之前
驗證，還原結果暫存在 `OC_TMP` 給它們用到最後一個為止，所以也只解碼一次）。最後再檢查一次結構，分辨「自己的 blob 壞了」和「只因為所依據的
blob 壞了而遺失」。不指定模型時驗證 pack 中的所有模型，並列出沒有任何模型需要的 blob。有任何失敗時，後面會接一份**損毀報告**：出問題的 blob；
遺失的檔案（`its own blob` 是自己的 blob 壞了，`through` 是經由所依據的檔案）；有遺失檔案的模型，以及其中有幾個權重檔；需要
這些模型資料的其他模型；以及每個出問題的 blob 對應檔案的 SHA-256、把那個原檔重新存回去能救回幾個遺失的檔案
（`pack <放原檔的資料夾> <pack> --name 模型 --add --file 路徑`，以它為基礎的檔案也會一起恢復），或如何放棄遺失的檔案
（`remove <pack> 模型 --file 路徑`）。加上 `--json` 時，報告是一個 `damage` 事件（`faults`、`lost`、`models`、`affected`、
`heal`、`manifests`）。`--file 路徑`（可重複，須指定一個模型）只驗證該模型的這些
檔案，路徑照 pack 列出的寫法。結果為 `ALL VERIFIED (<n> files)`，
或逐一列出失敗原因；有任何失敗時結束碼不為 0；什麼都沒驗證到時（那裡沒有 pack，或裡面沒有模型）結束碼也不為 0。缺少或損毀的
blob，以及無法讀取、或含有不安全路徑或名稱的 manifest，都會像其他失敗一樣列出，其餘項目照常檢查。建議在複製 pack 後、以及
冷儲存定期執行。

## 5. `restore`：還原

```bash
python -I tools/llm_archive.py restore <pack> 模型 <目的資料夾> [--overwrite] [--file 路徑 ...] [--files-from 檔案] [--flat] [--no-lock]
```

寫回模型的原始檔案，逐 byte 相同，並核對 SHA-256。ans16 差異檔會先把用到的 base 檔還原到 `OC_TMP`。`--file 路徑`（可重複
指定）只還原這些檔案，路徑以 pack 中的為準（即 `verify` 列出的 `<名稱>:` 之後的部分）；每個檔案會放在完整還原時的同一個位置。
`--flat` 把所有檔案直接放進 `<目的資料夾>`，不建立模型裡的資料夾；同名的兩個檔案，位置較深的那個加上 ` (1)`（搭配 `--file`
時只考慮指定的檔案）。還原只檢查它寫出的東西（每個檔案的 SHA-256，以及它用到的 blob），不檢查 pack 的其他部分：`verify`
找出問題的 pack，仍可能還原出完好的檔案；還原成功也不代表其他模型沒有問題。整個 pack 是否健全，要用 `verify` 檢查。

寫入任何東西之前，會先檢查 manifest 裡的每個路徑：必須是相對路徑、不含 `..`，而且必須落在 `<目的資料夾>` 之內；還原絕不經過
連結寫入，所以 `<目的資料夾>` 底下途經的資料夾若是符號連結或 junction，就會停止。這個系統不允許的檔名會像其他解壓縮工具一樣
調整：在 Windows 上，`<>:"\|?*` 與控制字元都換成 `_`，結尾的句點與空白也換成 `_`，裝置名稱則在主檔名後加 `_`
（`CON.txt` -> `CON_.txt`）。若因此有兩個檔案變成同一個檔案（例如 `a\b.txt` 與 `a_b.txt`，或 Windows 上的 `a.txt` 與
`A.txt`），後者會在副檔名前加上 ` (1)`、` (2)`……；檔名不需調整的檔案優先保留原名。每個改名的檔案都會列出
（`ok  a\b.txt  (as a_b (1).txt)`），而且名稱只取決於 pack 內容，所以重跑還原時每個檔案都會落在同一個位置。目的資料夾裡已有
內容正確的檔案會保留（顯示 `already there`），所以中斷的還原可以直接重跑；已有但內容不同的檔案會讓還原停止，除非加上
`--overwrite`。每個檔案都先寫到同資料夾裡以隨機名稱新建的暫存檔，確認大小與 SHA-256 正確後才移到定位，所以失敗的檔案不會
蓋掉原本的檔案；沒有 `--overwrite` 時，就算還原期間有其他程式在目的地建立了同名檔案，也不會被覆蓋，因為「確認不存在」與
「放上檔案」是同一個步驟（用 hard link；檔案系統不支援時，改用遇到同名就失敗的改名）。少數兩者都不支援的檔案系統（某些 FUSE
或網路掛載）上，沒加 `--overwrite` 的還原會停止並說明原因；這時請還原到空資料夾並加上 `--overwrite`。目的地若本身是 pack
資料夾或在其中（包括 `_kit`），還原出的檔案會和 pack 自己的檔案混在一起，即使加上 `--overwrite` 也會拒絕：請還原到它旁邊。

## 6. `remove` 與 `rename`

```bash
python -I tools/llm_archive.py remove <pack_folder> 模型 [模型 ...] [--models-from 檔案] [--file 路徑 ...] [--files-from 檔案] [--dry-run]
python -I tools/llm_archive.py rename <pack_folder> 模型 新名稱
python -I tools/llm_archive.py rename <pack_folder> 模型 --file 路徑 新路徑
```

`remove` 把模型（或搭配 `--file`，某個模型中的檔案）從 pack 資料夾移除：刪掉它們的 manifest（或從 manifest 中拿掉那些檔案），
然後刪除其餘模型都不需要的 blob。仍被其他模型需要的 blob 會保留：其他模型也列有它（相同的檔案），或其他模型的 blob 以它為
基底。`remove` 會先說明會釋放多少、為誰保留了多少：

```
removing Qwen/Qwen2.5-7B
  frees 312.4 MiB (9 blob(s))
  keeps 9,325.7 MiB (4 blob(s)) that other models still need: Qwen/Qwen2.5-7B-Instruct (as their base: rebase lets it go when that makes the pack smaller)
removed Qwen/Qwen2.5-7B: 312.4 MiB freed
```

之後 `list` 會在 Instruct 模型需要的項目中顯示 `Qwen/Qwen2.5-7B (removed)`，以及保留的 blob。保留下來的部分會立刻**瘦身**：
只當作 base 而保留的 blob，只留下其他檔案以它為基底的區塊（tensor，或最多 64 MiB 的一段），其餘丟掉（`trimmed … from …
blob(s) kept as bases`）。之後它無法再還原成原檔，只能提供其他檔案要讀的部分：這叫*部分保留*的 blob，只有 verify（透過以它
為基底的檔案）會讀它。每個都先完整寫好、檢查過，再一步取代舊的；之後再打包同一個原檔，就會存回完整的一份。每次 pack、remove
或 rebase 清理時都會這樣做。要釋放留下的部分，請對需要它的模型 rebase（第 7 節）：當這些模型改用其他方式存放能讓 pack 變小時，
rebase 會釋放保留的 base。`--dry-run` 只說明會釋放與保留什麼。用 `--file` 移除某個模型的全部檔案時，就是移除該模型。

`rename` 為模型改名（重新命名它的 manifest 檔）；其他一切不變，因為沒有任何東西以名稱指向模型。新名稱的規則同 `--name`
（第 1 節），而且不能已被使用。加上 `--file` 時，改的是模型中某個檔案的路徑（只改 manifest：blob 是以檔案內容命名的）。

誤刪的檔案可以用 `pack <它所在的資料夾> <pack_folder> --name 模型 --add --file <路徑>` 補回，來源是原始檔或還原出來的副本（例如從
之前存下的 pack 檔還原）；若其他模型也有同一個檔案，它的 blob 仍在 pack 裡，不必再存一次。

## 7. `rebase`：重新挑選已打包模型的 base

```bash
python -I tools/llm_archive.py rebase <pack_folder> 模型 [模型 ...] [--models-from 檔案] [選項]
python -I tools/llm_archive.py rebase <pack_folder> 模型 --file 路徑 [--file 路徑 ...] [--files-from 檔案] [選項]
python -I tools/llm_archive.py rebase <pack_folder> --all [選項]
```

把已在 pack 中的模型，依 `pack` 現在會採用的方式重新儲存：每個含 tensor 的檔案從其他模型中重新挑選 base（同第 2 節）。不需要
原資料夾。base 是從 pack 內容挑選的，所以只有 base 改變的檔案才會被還原（到 `OC_TMP`）、重新打包、從磁碟解回並核對 SHA-256。
未指定 `--base` 或 `--no-base` 時，只有讓 pack 變小的檔案才會更動：新的 blob 必須比較小，但釋放為已刪除模型保留的 base 時例外
（見下）。它還原出的檔案與舊的相同，所以以同一個名稱一次取代舊的：需要它的
每個模型都不受影響，即使 `rebase` 中途被打斷，pack 在任何時刻都是完整的。不再被需要的舊基底 blob 會在最後刪除。會造成循環的
base（其 blob 直接或間接以這個檔案為基底的模型）絕不會被選上，會讓基底鏈超過 32 層的也不會。

| 選項 | 說明 |
|---|---|
| `--all` | 所有 base 為自動挑選的模型，base 先於使用它的模型處理；以 `--base` 或 `--no-base` 打包或 rebase 的模型會略過（指名即可納入），這樣處理過的檔案也會略過（用 `--file` 指名即可納入） |
| `--file 路徑`、`--files-from 檔案` | 只處理所指名的那一個模型的這些檔案；搭配 `--base` 或 `--no-base` 時，這個選擇屬於這些檔案，而不是整個模型：`--all` 以及指名該模型時都不會動它們；不加這兩個選項、再用 `--file` 指名它們，就會改回自動挑選 |
| `--base 模型` | 所有檔案都以這個模型為 base 重新打包，即使變大也照做（該模型直接或間接需要的檔案維持原樣） |
| `--no-base` | 所有檔案都不使用 base 重新打包，即使變大也照做 |
| `--dry-run` | 只顯示會改變什麼，不做任何更動 |
| `--delta-codec`、`--zstd-level`、`--zstd-tensor-level`、`--workers`、`--chunk-mib` | 同 `pack` |

每個可能改變的檔案各有一行（未改變，或現在的儲存方式與它取代了什麼），最後是該模型儲存大小的前後對照（用 `--file` 時，是所指名檔案的：`rebasing b.safetensors of org/model`、
`b.safetensors of org/model: stored …`）。以下是比 0.5B Instruct
先打包的 Gensyn 微調模型移到 Instruct 上：

```
rebasing mazdaypci/Qwen2.5-0.5B-Instruct-Gensyn-Swarm-wily_padded_mink  (delta base chosen per file)
  base for model.safetensors: Qwen/Qwen2.5-0.5B-Instruct (est. 1.4%; standalone 66.2%; 1 candidate(s), 3s)
  model.safetensors                  safetensors+ans16     942.3 MiB ->     13.7 MiB    1.45%  verified, 100% matched in Qwen/Qwen2.5-0.5B-Instruct  (25s)
    replaces its blob of 627.6 MiB
  13 file(s) without tensors unchanged
  stored 630.6 MiB -> 16.7 MiB  (-97.4%)
```

**為已刪除模型保留的 base。** rebase 會先權衡每個只當作 base 而保留、且使用者（以它為基底的檔案）全都屬於這次要 rebase 的
模型的 blob：釋放它能空出多少（它本身，以及只有它需要的部分），對照那些檔案改以其他模型為 base、或單獨存放，估計會變大多少。
估計能省下至少它所佔空間的十分之一時，會顯示 `letting go of …`，不論那些檔案重新存放後大小如何都照存，保留的 blob 隨之刪除；
否則顯示 `keeping …`。最後一行是整個 pack 的前後對照（`the pack: 9.4 GiB -> 5.1 GiB (-45.7%)`），這才是重點：單獨存放的模型
雖然變大，但它不再需要的 base 已經刪除了。

`OC_TMP` 需要容納正在重新打包的檔案，以及它新舊差異所讀取的 base 檔（7B 的一個分片：兩個 3.7 GB 的檔案）；pack 所在的磁碟則需要
容納新的 blob，直到取代舊的為止（最多再一個）。`rebase` 會在處理每個檔案前檢查空間。

## 8. `export`：把 pack 的模型存成 pack 檔（`.oczip`）

```bash
python -I tools/llm_archive.py export <pack> <檔名.oczip> [模型 ...] [--models-from 檔案] [--verify | --no-verify] [--delete] [--overwrite]
```

（即視窗中的 **⤓ Save .oczip**。）把 pack 資料夾中的模型（預設全部）維持壓縮狀態，放進一個新的 pack 檔，連同它們需要的所有 blob：包括它們 base 的 blob，所以 pack 檔不靠原資料
夾也是完整的（base 本身除非指名，否則不會以模型的身分列在其中）。`_kit/` 也會一起放入；你自己放在 pack 資料夾的其他檔案則不會。
檔案格式是 ZIP（ZIP64），副檔名刻意取為 `.oczip`，以免被當成一般 ZIP；blob 原樣存放（複製時同時核對它自己的 SHA-256），其他
檔案若用 Deflate 壓得更小就壓縮。檔案先寫到旁邊的暫存檔，完整寫完才放到定位，所以中途停止不會留下任何東西。

| 選項 | 說明 |
|---|---|
| `--verify` | 寫完後、放到定位之前讀回驗證：每個成員都與放進去的內容比對（SHA-256），並像 `verify` 一樣從檔案驗證每個匯出的模型。驗證失敗的 pack 檔不會放到定位（加了 `--overwrite` 時，也不會取代原有的同名檔案）。預設不做 |
| `--delete` | 之後從 pack 資料夾移除這些模型（同 `remove`：其他模型仍需要的會保留）；匯出全部模型時，連 pack 資料夾一起移除（你自己放的檔案會保留）。除非加上 `--no-verify`，否則會先驗證；任何一項失敗就什麼都不移除 |
| `--no-verify` | 與 `--delete` 併用：不驗證就移除 |
| `--overwrite` | 取代同名的既有檔案 |

pack 檔必須放在 pack 資料夾之外（也不能在其他 pack 資料夾裡）。`export` 執行期間會鎖住 pack（加 `--delete` 時為獨占），所以這段
期間沒有人能改動資料夾。`verify`、`restore`、`list`（以及圖形介面）都可以用 pack 檔取代資料夾，直接讀取：blob 在裡面是未壓縮的，
直接依位移讀取。要修改時，請用任何 ZIP 工具解開成資料夾（只認副檔名的工具，可把副本改名為 `.zip`），在該資料夾上作業後再 export。

從 pack 檔執行 `export`，會另存成一個只含指定模型（連同它們需要的一切）的較小 pack 檔；pack 檔是唯讀的，所以不能加 `--delete`。

## 9. 防護與修復

本工具負責偵測損毀（每個 blob 自己的 SHA-256、每個區塊的 CRC、每個檔案的 SHA-256），不負責修復。位元層級的損毀交給硬體與
檔案系統（RAID、ECC、ZFS/Btrfs/ReFS）；並在不只一種媒體上保留副本：詳見 [protection.md](protection.md)。

## 環境變數

| 變數 | 作用 |
|---|---|
| `OC_TMP` | 暫存還原 base 檔的資料夾，需容納差異檔用到的 base 檔；`rebase` 重新打包的檔案也放在這裡 |
| `OC_WORKERS` | ans16 平行程序數上限，須為 1 以上的整數（記憶體小的機器請調低）；同時也是位元平面與 zstd 編碼執行緒（最多 8 個）和計算雜湊執行緒（最多 4 個）的上限；Windows 上程序最多 61 個；其他值（只認 0–9 的數字）會讓所有指令直接報錯 |

## pack 注意事項

- pack 資料夾只透過工具修改。用手刪除或搬移 blob 或 manifest，會讓需要它們的模型壞掉；`verify` 會指出缺少什麼。要複製或搬移
  就整個 pack 資料夾一起。
- 一次只做一項變更：`pack`、`remove`、`rename`、`rebase` 與 `export --delete` 執行期間會鎖住整個 pack；`list`、`verify`、
  `restore` 與 `export` 可以同時執行，但不能在變更進行中執行。發現 pack 正在使用時會立刻停止並說明，請稍後再執行。鎖是系統對
  pack 資料夾中空檔 `.overcompressing.lock` 的鎖，程式結束時（不論如何結束）自動釋放。讀取的指令會以唯讀方式開啟既有的鎖檔，
  所以鎖檔屬於誰、權限如何都不影響。若完全無法加鎖，只有在作業系統回報 pack 所在的磁碟是唯讀時（光碟、防寫磁碟、唯讀掛載）
  才照常執行，因為這時不可能有人寫入；其他情況會停止。加上 `--no-lock` 可強制執行：請只在確定這段期間沒有人會改動 pack 時使用
  （例如對你唯讀、但別人可寫的共用資料夾）。
- 讓 pack 帶著一份可攜版工具（放在 pack 資料夾裡，命名為 `_kit/`，見下文），並在不同媒體保留多份副本。
- 在 pack 資料夾與還原目的資料夾底下，絕不跟隨連結：若工具執行期間有其他程式把某個資料夾換成符號連結或 junction，寫入會被
  拒絕，而不會寫到別處。在 Linux 與 macOS 上每一步都有保障（逐層開啟資料夾、不跟隨連結）；在 Windows 上則是盡力而為（每層
  資料夾使用前檢查，每個新檔案放上前再透過它自己的 handle 確認實際位置）。工具執行期間，請不要讓不受信任的使用者能寫入 pack
  或目的資料夾。

## 可攜版

本工具需要裝有 numpy 與 zstandard 的 Python 3.13。建置頁面（[Windows](build-windows.md) / [Linux](build-linux.md)）會做出
**可攜版**：一個資料夾，裡面有它自己的 Python、這兩個套件和工具，複製到哪裡就能在哪裡執行，不必安裝任何東西，也不需要網路。
在有網路時做一次，之後完全離線可用。

為了讓 pack 多年後也能在沒有網路的機器上還原，請讓它帶著一份可攜版：把整個資料夾複製到 pack 資料夾裡，命名為 `_kit/`。
本工具不會讀取 `_kit/` 的內容，但把它當成 pack 的一部分：只有 `_kit/` 的資料夾可以成為新的 pack，還原絕不寫進它，`export`
也會把它放進 pack 檔，所以 pack 檔同樣帶著它。從那裡啟動（`_kit\OverCompressing.cmd`、`_kit/overcompressing`）時，視窗會
直接開啟那個 pack。若是 pack 檔，請先用任何 ZIP 工具解出其中的 `_kit/`（7-Zip 可直接開啟；Windows 檔案總管則需把副本改名為
`.zip`），再從解出的資料夾執行工具，把 pack 檔交給它。

先前的版本改用安裝腳本組裝「離線工具包」，現已不再製作，腳本也已從 repo 刪除。舊 pack 裡那種 `_kit/` 仍可照它自己的
`README.md` 使用。

## 疑難排解

| 狀況 | 解法 |
|---|---|
| `its blob is damaged` / `… is missing from the pack` | 從其他副本換回 pack；若還有原始檔案，重新打包它們即可把損毀的檔案重新存入 |
| 還原或驗證差異檔時磁碟空間不足，或出現 `not enough room to rebase` | 將 `OC_TMP` 指到空間較大的資料夾 |
| 記憶體不足 | 將 `OC_WORKERS` 設為 4 以下 |
| 程式被強制中斷後，暫存資料夾留下 `oc-base-*.bin` 或 `oc-rebase-*` | 直接刪除 |
| 程式被強制中斷後留下 `.oc-*.part` | 直接刪除，或讓下一次 `pack`、`remove`、`rebase` 處理 |
| 還原時出現 `already exist … with other contents` | 還原到空資料夾，或加上 `--overwrite` |
| 修改 pack 時出現 `models/<名稱>.json in …: …` | 從其他副本換回該 manifest，或把它移出 `models/` |
| `… is not a pack folder (no ocpack.json in it) and holds other files` | 打包進新的或空的資料夾，或既有的 pack 資料夾 |
| `… is a link or not a folder: not followed` | pack 或目的資料夾中有資料夾是符號連結或 junction：改成實際的資料夾（或還原到別處） |
| `a file of that name appeared in the destination while restoring` | 有其他程式寫入了目的地：重跑還原，或加上 `--overwrite` |
| `cannot put … in place … without the risk of replacing a file` | 檔案系統既不支援 hard link，也不支援遇到同名就失敗的改名：還原到空資料夾並加上 `--overwrite`；pack 本身請放在其他檔案系統上 |
| 打包時出現 `cannot pack … is a pack folder` 或 `… is inside the pack folder …` | 從原始檔案自己的資料夾打包 |
| `refusing to restore there: …` | 還原到一個自己的資料夾，不要在任何 pack 資料夾裡 |
| `… is a pack file: it is read-only` | 把它解開成 pack 資料夾（它是 ZIP），在其中修改，再重新 `export` |
| 打包時出現 `… exists already: a pack file is written whole, never added to` | 改壓成新的 pack 檔，或把它解開成 pack 資料夾、在其中打包，再重新 `export` |
| `… is compressed or encrypted in the pack file: blobs must be stored as they are` | 這個 pack 檔被其他 ZIP 工具重寫過：請從資料夾用 `export` 重新製作 |
| `the pack folder is in use …` 或 `… is being changed …` | 另一個指令正在使用這個 pack：等它結束後再執行 |
| 打包時出現 `… is a link to a folder`、`… is not a regular file`、`… cannot be read` | 用 `--exclude` 略過，或把連結指向的資料夾另外打包 |
| `the name … is taken` | 換一個 `--name`：在不分大小寫的系統上，這個名稱與既有的名稱會對應到同一個 manifest 檔 |
| 打包時出現 `… holds a model named … already` | 加上 `--replace` 取代那個模型，或 `--add` 把檔案加入它 |
