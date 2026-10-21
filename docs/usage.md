# Usage

**English** · [中文](#使用方式)

Everything goes through one script, `tools/llm_archive.py`, with five commands: `pack`, `rebase`, `verify`,
`restore` and `bundle` (or, for all but rebase, a small window: see below). It never downloads anything: get the model files however you like (Hugging Face, ModelScope, git, a
mirror, a USB drive), then pack the folder. Run it with the Python environment from the offline kit or from a
development setup ([Windows](build-windows.md) / [Linux](build-linux.md)). Below, `python` means that environment's
interpreter; `-I` (isolated mode) keeps it from importing stray files from the current folder.

## Typical workflow

```bash
python -I tools/llm_archive.py pack models/Qwen2.5-7B archive --name Qwen/Qwen2.5-7B
python -I tools/llm_archive.py pack models/Qwen2.5-7B-Instruct archive --name Qwen/Qwen2.5-7B-Instruct
python -I tools/llm_archive.py verify archive
python -I tools/llm_archive.py restore archive Qwen/Qwen2.5-7B-Instruct restored/Qwen2.5-7B-Instruct
```

Pack the models, verify everything, restore when needed. Each file is stored as a delta against the archived model
that compresses it best (here the Instruct model picks Qwen2.5-7B by itself), so pack bases before the models
derived from them. If a base arrives later, `rebase` re-chooses the bases afterwards (section 3).

## A window instead of commands

```bash
python -I tools/llm_archive_gui.py [<archive_root>]
```

(On Windows, `pythonw` instead of `python` opens it without a console window.) **Open** an archive folder (an empty
one starts a new archive) or a single `.oczip` file, then: **+ Folder…** or **+ Files…** to pack (files picked from
one folder make up the model on their own; one file is named after itself by default), **▣ Bundle…** the folder into
one `.oczip` (section 6), **⇩ Extract…** the models or files selected in the
list (each model goes into a folder named after it, e.g. `<dest>/Qwen/Qwen2.5-7B`; files with other contents already
there are replaced only after you agree), and **✔ Verify** the selection or the whole archive. The list shows every
model and file with its size, stored size, ratio and how it is stored (`Δ` a delta against that model, `=` the same
as that file). Each action runs `llm_archive.py` exactly as below and shows its output; **✕ Cancel** stops it as
Ctrl+C would, so nothing half-done stays in the archive. Delta bases are always chosen automatically; `--base`,
`rebase` and the other options are on the command line only; with an `.oczip` open, packing and bundling are off.
The window's text is English and symbols, whatever the system language. Run from an offline kit, it opens the kit's
archive.

## 1. `pack` — compress a folder into the archive

```bash
python -I tools/llm_archive.py pack <source_dir> <archive_root> [--name NAME] [--base NAME | --no-base] [options]
```

Every file under `<source_dir>` is hashed (SHA-256), parsed (safetensors, GGUF, other), compressed per tensor and
written to `<archive_root>/<NAME>/` (a `/` in the name becomes `__`). Each file is then decoded back from disk and
checked against its SHA-256 before the next one starts. A `manifest.json` is written at the end. `.git/` and
`.cache/` folders (bookkeeping of git and download tools) are always skipped. The folder and the archive must not be
inside one another.

| option | meaning |
|---|---|
| `--name NAME` | name of the model in the archive, e.g. `Qwen/Qwen2.5-7B` (default: the folder name); `--base`, `verify` and `restore` use it. Parts separated by `/`, made of letters, digits, `-_.+@()[]` and spaces; no part may be empty, start or end with a space or end with a dot; not a Windows device name (`CON`, `NUL`, …) or `_kit` |
| `--revision TEXT` | free-text note stored in the manifest, e.g. the upstream commit you downloaded |
| `--exclude GLOB` | skip files whose path or name matches (repeatable), e.g. `--exclude "*.bin"` |
| `--file PATH` | pack only this file of `<source_dir>`, a path relative to it (repeatable): the model then holds just the files named; not with `--exclude` |
| `--base NAME` | use this archived model as the base of every file instead of choosing one per file |
| `--no-base` | store every file without a delta base |
| `--delta-codec ans16\|xor` | for 16-bit float tensors: ans16 (default, smaller) or the XOR with the base in byte planes (faster) |
| `--zstd-level N` | zstd level 1–22 for headers, metadata and text (default 19) |
| `--zstd-tensor-level N` | zstd level 1–22 for quantized tensors and unknown binaries (default 9) |
| `--workers N` | worker processes for ans16, 1–1024 (default: `OC_WORKERS`, else the CPU count) |
| `--no-dedupe` | store files even if an identical one is already archived |
| `--chunk-mib N` | maximum region size, 1–1024, default 64 |

Each line of output shows the method, the ratio and, for deltas, how much of the file matched the base. A
`base for` line before a file shows the base chosen for it, its estimated ratio, the runner-up and the
standalone estimate:

```
base for model-00002-of-00004.safetensors: Qwen/Qwen2.5-7B (est. 29.2%; standalone 66.2%; 1 candidate(s), 3s)
model-00002-of-00004.safetensors   safetensors+ans16   3,685.7 MiB ->  1,086.0 MiB   29.47%  verified, 100% matched in Qwen/Qwen2.5-7B
tokenizer.json                     same                    6.7 MiB ->      0.0 MiB    0.00%  same as Qwen/Qwen2.5-0.5B:tokenizer.json
```

The SHA-256 recorded is that of the files as they were in the folder. If your source publishes hashes (Hugging Face
does for weight files), compare them before packing when provenance matters.

Re-packing a model under the same name stores its files again the way `rebase` does (section 3): each container goes
under a name the current manifest does not use (`<path>.ocz` and `<path>.1.ocz` alternate; a small file stored as-is
keeps `<path>` unless its content changed, then it alternates with `<path>.1`), the new manifest replaces the old one
in one step, and only then are the stored files it no longer uses deleted and listed. So the archive stays complete
and verifiable even if `pack` is interrupted: the manifest on disk describes either the old files or the new ones, and
those are all there. Until the re-pack finishes, the archive needs room for the new stored files beside the old ones.
Other files in the directory (e.g. your own recovery data or notes) are never written over or deleted, but are now
outdated: rebuild them. A small file that is unchanged keeps its stored copy (it is checked, not copied again, so
a source file that changes during the pack cannot touch it); if that copy is damaged, it is stored again.

`pack` refuses, before writing anything, a re-pack that would change or drop a file other models need (a file
they are deltas against, or identical to through a same-as reference): it lists those files and models. Pack the
new version under another name, or first make those models independent of it
(`rebase <archive_root> <their names> --no-base --no-dedupe`, section 3) and then re-pack. It also refuses a name
whose folder already holds another model or other files (e.g. `A__B` when `A/B` is archived, since both map to the
folder `A__B`), and any change to an archive in which a `manifest.json` cannot be read or trusted: `verify` names it.

A file of the folder may have any name, including `manifest.json` or `*.part`: it is stored under another name
(e.g. `manifest.json.1`) and restored under its own. Stored files always get names every system can hold, so an
archive can be copied between Windows and Linux; a name only one system allows (e.g. a Linux file called
`a\b.txt`) is still recorded as it is, and `restore` adapts it where needed (section 5). Stored files keep the
folder layout of the source; where a stored file and a stored folder would get the same name (e.g. a Linux file
`a_` next to a folder `a?/`, or a folder that became a file since the last pack), the later one gets `.1`.

Only regular files are packed; a link to a file counts as that file. A link to a folder (a symlink, or a junction on
Windows), a FIFO, socket or device, and a broken link stop `pack` before it reads anything, and are listed: skip
them with `--exclude <name>`, or pack the folder a link points to as a model folder of its own. Nothing outside the
source folder is packed by accident, and nothing in it is left out without a word.

If a file is identical to one already in the archive but that copy is damaged, the new file is stored in full (not
as a reference) and `pack` says so: run `verify` to find the damaged model.

## 2. How the base is chosen

Without `--base`, every file of the folder picks its own base among the models already in the archive:

1. **Candidates**: every archived model with tensors that match this file's by name (same format; same dtype and
   size, another float dtype, or the same leading rows; see [How it works](principles.md#4-matching-tensors-not-files)).
   Only manifests are read; nothing is decompressed.
2. **Trial**: 16 windows of the file (512 KiB each, spread over its tensors) are coded against each candidate with
   fast estimates and compared with storing them standalone. Only the parts of a candidate those windows need are
   decoded.
3. **Choice**: the smallest estimate wins if it saves at least 1 % over storing the file standalone; otherwise the
   file gets no base. On a tie, the base with the shorter chain of references wins (faster restores).

Models that need the one being packed (directly or through others) are never candidates, so a re-pack cannot create a
cycle. Files of one folder may pick different bases (e.g. a BF16 GGUF picks a BF16 GGUF, a Q4_K_M one a Q4_K_M).

Measured: every community fine-tune in the [benchmarks](benchmarks.md#automatic-base-selection) picked the base
found by hand, and the estimate was within a few points of the real size; choosing takes a few seconds per file.

**Order matters only through what is already archived.** A model can only use bases packed before it. If a closer
ancestor arrives later (say, the Instruct model after its fine-tune), run `rebase` on the fine-tune, or `rebase --all`
(section 3): it re-chooses the bases from what is archived now, without the original folders. Usually one step comes
first: packed with automatic choice, the ancestor itself picks the fine-tune as its base and refers to the identical
tokenizer files, and the fine-tune cannot use a model that needs it. The archive is about as small either way round (a
delta is about as small in both directions), so `rebase` leaves that alone. If you want the ancestor to be restorable
on its own, make it independent first, then rebase the fine-tune:

```bash
python -I tools/llm_archive.py rebase archive Qwen/Qwen2.5-0.5B-Instruct --no-base --no-dedupe
python -I tools/llm_archive.py rebase archive --all
```

`--base NAME` forces a base (it is used even if it does not help; regions that do not shrink are stored standalone).
`--no-base` keeps a model free of deltas, e.g. a base you want to restore on its own; add `--no-dedupe` to make it
depend on no other model at all. `tools/compare_tensors.py <base_file> <target_file>` shows in detail how two files
line up (exactly, by dtype conversion, by leading rows, bit-identical share).

## 3. `rebase` — re-choose the bases of archived models

```bash
python -I tools/llm_archive.py rebase <archive_root> <name> [<name> ...] [options]
python -I tools/llm_archive.py rebase <archive_root> --all [options]
```

Stores the files of models already in the archive again, the way `pack` would store them now: each file re-chooses
its base among the models in the archive (as in section 2), and a file identical to one stored in another model
becomes a same-as reference. The original folders are not needed. The base is chosen from the archived bytes, so only
a file whose base changes is restored (into `OC_TMP`), re-packed, decoded back from disk and checked against its
SHA-256. Unless `--base` or `--no-base` is given, the new stored file is kept only if it is smaller.

The new stored file is written next to the old one (as `<path>.1.ocz` when `<path>.ocz` is taken), and the old one is
deleted only after the new manifest has replaced the old manifest, so the archive is complete at every moment, even if
`rebase` is interrupted. Models that refer to the rebased one keep working: they name its files by path and SHA-256,
which do not change. Models that need the one being rebased are never candidates, as for `pack`.

| option | meaning |
|---|---|
| `--all` | every model whose bases were chosen automatically, bases before the models that use them; models packed or rebased with `--base` or `--no-base` are skipped (name them to include them) |
| `--base NAME` | re-pack every file against this model, even if it gets larger |
| `--no-base` | re-pack every file without a delta base, even if it gets larger |
| `--no-dedupe` | store same-as references to other models as files again (with `--no-base`: the model then needs no other model) |
| `--dry-run` | show what would change; change nothing |
| `--delta-codec`, `--zstd-level`, `--zstd-tensor-level`, `--workers`, `--chunk-mib` | as for `pack` |

Every file that could change gets a line (unchanged, or how it is stored now and what that replaces), then the
model's stored size before and after. Here the Gensyn fine-tune of the 0.5B Instruct model, packed before it, moves
onto it:

```
rebasing mazdaypci/Qwen2.5-0.5B-Instruct-Gensyn-Swarm-wily_padded_mink  (delta base chosen per file)
  base for model.safetensors: Qwen/Qwen2.5-0.5B-Instruct (est. 1.4%; standalone 66.2%; 1 candidate(s), 3s)
  model.safetensors                  safetensors+ans16     942.3 MiB ->     13.7 MiB    1.45%  verified, 100% matched in Qwen/Qwen2.5-0.5B-Instruct  (25s)
    replaces model.safetensors.ocz (627.6 MiB as safetensors)
  vocab.json                         same                    2.6 MiB ->      0.0 MiB    0.00%  same as Qwen/Qwen2.5-0.5B-Instruct:vocab.json  (0s)
    replaces vocab.json.ocz (0.8 MiB as zstd)
  removed model.safetensors.ocz (no longer used by the manifest)
  removed vocab.json.ocz (no longer used by the manifest)
  13 file(s) without tensors unchanged
  stored 630.6 MiB -> 15.8 MiB  (-97.5%)
```

`OC_TMP` needs room for the file being re-packed plus the base files its old or new delta reads (for a 7B shard: two
3.7 GB files), and the archive needs room for the new stored file until the old one is deleted (at most one more).
`rebase` checks this before each file. Rebuild your repair data for every model it changed
([protection.md](protection.md)).

## 4. `verify`

```bash
python -I tools/llm_archive.py verify <archive_root> [<name> ...] [--no-lock]
```

Checks the stored hashes of every file and of everything it depends on, decodes it in memory and compares it with the
original SHA-256. Prints `ALL VERIFIED (<n> files)` or each failure with its cause, and exits with a non-zero code
on any failure, and also when it verified nothing (the folder does not exist, or holds no models). A missing or unreadable stored file, and a `manifest.json` that cannot be read or contains an unsafe path or
name, are reported like any other failure; the rest is still checked. Run it after copying the archive and from time
to time on cold storage. `<archive_root>` may also be an `.oczip` file (section 6).

## 5. `restore`

```bash
python -I tools/llm_archive.py restore <archive_root> <name> <dest_dir> [--overwrite] [--file PATH ...] [--no-lock]
```

Writes the original files, byte-identical, and checks each SHA-256. Deltas need their base models in the same archive
root; ans16 deltas first restore the base files they read into `OC_TMP`. `--file PATH` (repeatable) restores only
those files, named by their paths in the archive (as `verify` lists them after `<name>:`); each lands where a full
restore would put it. `<archive_root>` may also be an `.oczip` file (section 6).

Before writing anything, every path in the manifest is checked: it must be relative, without `..`, and land inside
`<dest_dir>`; restore never writes through a link, so if a folder on the way below `<dest_dir>` is a symlink or a
junction, it stops. Names this system does not allow are adapted, as other
archivers do: on Windows each of `<>:"\|?*` and control characters becomes `_`, as do trailing dots and spaces, and a
device name gets `_` after its stem (`CON.txt` -> `CON_.txt`). If two files would then be one file (e.g. `a\b.txt` and
`a_b.txt`, or `a.txt` and `A.txt` on Windows), the later
one gets ` (1)`, ` (2)`, ... before its extension; files whose names needed no change keep them. Every renamed file
is listed (`ok  a\b.txt  (as a_b (1).txt)`), and the names depend only on the archive, so a second restore puts every
file in the same place. A file already in `<dest_dir>` with the right bytes is kept (`already there`), so an
interrupted restore can simply be run again; a file with other contents stops the restore unless you add
`--overwrite`. Each file is written to a new temporary file with a random name in its folder and moved into place
only once its size and SHA-256 are right, so a failed file never replaces what was there; without `--overwrite`, a
file that appears in the destination meanwhile (another program) is not replaced either, since checking and placing
are one step (a hard link, or where the file system has none, a rename that refuses an existing name). On the rare
file system that has neither (some FUSE or network mounts), restore without `--overwrite` stops and says so; restore
into an empty folder with `--overwrite` there. A destination inside the archive itself is refused, even with
`--overwrite`.

## 6. `bundle` — the whole archive in one `.oczip` file

```bash
python -I tools/llm_archive.py bundle <archive_root> <file.oczip> [--verify | --no-verify] [--delete] [--overwrite]
```

Puts everything in `<archive_root>` (the models, `_kit/` and anything else you keep there) into one file: a ZIP
(ZIP64), named `.oczip` on purpose so that it is not taken for an ordinary ZIP. Containers and files compressed
already (wheels, installers, images, …) are stored as they are; the rest is Deflate-compressed when that makes it
smaller. The lock file and leftover `.oc-*.part` files are left out, and a link anywhere in the folder stops it. The
`.oczip` is written to a temporary file next to it and put in place only when complete, so a stopped bundle leaves
nothing behind.

`verify` and `restore` (and the window) take the `.oczip` in place of the folder and read it as it is, without
extracting anything: the containers lie uncompressed in it and are read at their offsets. It is read-only: to `pack`
into it or `rebase` it, extract it into a folder with any ZIP tool (rename a copy to `.zip` for tools that go by the
extension), work on that folder, and bundle again.

| option | meaning |
|---|---|
| `--verify` | then read it back: every file compared with what was put in (SHA-256), and every model verified from the `.oczip` as `verify` does. Off by default |
| `--delete` | then delete the files put in from `<archive_root>` (and the folder, once empty). Verifies first unless `--no-verify`; if anything fails, nothing is deleted |
| `--no-verify` | with `--delete`: delete without verifying first |
| `--overwrite` | replace an existing file of that name |

The `.oczip` must lie outside `<archive_root>`. `bundle` holds the archive's lock while it runs (alone with
`--delete`), so nothing changes the folder meanwhile. Repair data for an `.oczip` (e.g. PAR2) is made for the whole
file and kept beside it (section 7).

## 7. Protection and repair

OverCompressing detects damage but does not repair it. Keep several copies and/or add repair data with a tool of your
choice (PAR2, RAR recovery records, ZFS/Btrfs/ReFS, …): see [protection.md](protection.md).

## Environment variables

| variable | effect |
|---|---|
| `OC_TMP` | folder for restored base files (needs room for the base files a delta reads) and for the file `rebase` re-packs |
| `OC_WORKERS` | cap on ans16 worker processes, a whole number of at least 1 (lower it on small-RAM machines); any other value stops every command with an error |

## Rules for a healthy archive

- Models can depend on each other (deltas, identical-file references). **Do not delete a model directory others
  depend on**; `verify` names any missing dependency.
- Use the same `--name` every time you re-pack a model, so references from other models keep pointing to it.
- Keep `_kit/` (the offline kit) next to the models, and keep more than one copy on different media.
- One change at a time: `pack` and `rebase` lock the whole archive while they run, and `verify` and `restore` can run
  together but not during a change. Whatever finds the archive in use stops at once and says so; run it again
  later. The lock is the system's lock on the empty file `.overcompressing.lock` in the archive root, released when
  the program ends, however it ends. `verify` and `restore` open an existing lock file read-only, so its owner
  and permissions do not matter. If they cannot lock at all, they go on only when the system reports the archive's
  volume read-only (a CD/DVD/BD, a write-protected drive, a read-only mount), since nothing can change it then;
  otherwise they stop. `--no-lock` makes them go on anyway: use it only when you know nothing changes the archive
  meanwhile (e.g. a share that is read-only for you but not for others).
- Links are never followed below the archive's model folders or a restore destination: if another program swaps a
  folder there for a symlink or junction while `pack`, `rebase` or `restore` runs, the write is refused instead of
  landing elsewhere. On Linux and macOS this holds at every step (folders are opened one by one without following
  links); on Windows it is a best effort (each folder is checked just before use, and every new file's real location
  through its own handle before it is put in place). Do not let untrusted users write into the archive or the
  destination while the tool runs.

## Offline kit

Build it once with network access; afterwards everything works without it:

```bash
python -I kit/build_kit.py <archive_root>/_kit [--cache <old_kit>]
```

On the restore machine:

```powershell
powershell -ExecutionPolicy Bypass -File _kit\install_windows.ps1
```

```bash
sh _kit/install_linux.sh
```

The install scripts create a virtual environment from the bundled wheels (installing the bundled Python 3.13 first if
none is found) and print the exact `restore` / `verify` commands to use. Details: `_kit/README.md`. With an
`.oczip`, first extract its `_kit/` folder with any ZIP tool (7-Zip opens it as it is; for Windows Explorer, rename a
copy to `.zip`), then give the `.oczip` to `verify`, `restore` or the window.

## Troubleshooting

| symptom | fix |
|---|---|
| `stored file corrupted` | replace the file from another copy, or repair it with your recovery data ([protection.md](protection.md)) |
| `... is missing from the archive` | copy the referenced model directory back into the archive root |
| out of disk during restore/verify of a delta, or `not enough room to rebase` | set `OC_TMP` to a folder with more space |
| out of memory | set `OC_WORKERS` to 4 or less |
| leftover `oc-base-*.bin` or `oc-rebase-*` in the temp folder after a killed run | delete it |
| after a killed `rebase`, a `*.ocz` or `*.part` file in a model directory that its manifest does not list | delete it (the manifest still uses the old file) |
| `re-packing X would change or drop files other models need` | pack the new version under another name, or make the listed models independent first (`rebase … --no-base --no-dedupe`) |
| `already exist … with other contents` (restore) | restore into an empty folder, or add `--overwrite` |
| `<folder>/manifest.json: …` when packing or rebasing | restore that manifest from another copy, or move the folder out of the archive |
| leftover `.oc-*.part` after a killed run | delete it (an unfinished temporary file; nothing refers to it) |
| `… is a link or not a folder: not followed` | a folder in the archive or the destination is a symlink or junction: replace it with a real folder (or restore elsewhere) |
| `a file of that name appeared in the destination while restoring` | another program wrote there: run restore again, or add `--overwrite` |
| `cannot put … in place … without the risk of replacing a file` | the file system has no hard links and no rename that refuses an existing name: restore into an empty folder with `--overwrite`; keep the archive itself on another file system |
| `… and the archive … are in one another` (pack) | pack a folder outside the archive, into an archive outside that folder |
| `… is a single-file archive (.oczip): it is read-only` | extract it into a folder (it is a ZIP), pack or rebase there, then `bundle` again |
| `… is compressed or encrypted in the .oczip: containers must be stored as they are` | the `.oczip` was rewritten by another ZIP tool: make it again with `bundle` from the folder |
| `the archive is in use …` or `… is being changed …` | another `pack`, `rebase`, `verify` or `restore` runs on the archive: run this when it has finished |
| `… is a link to a folder`, `… is not a regular file`, `… cannot be read` (pack) | skip it with `--exclude`, or pack the link's target as a folder of its own |

---

# 使用方式

[English](#usage) · **中文**

所有功能都在 `tools/llm_archive.py` 這支程式裡，共五個指令：`pack`、`rebase`、`verify`、`restore`、`bundle`（除了 rebase，
其餘也可以用圖形介面，見下文）。本工具不會下載任何東西：
請用你習慣的方式取得模型檔（Hugging Face、ModelScope、git、鏡像站、隨身碟皆可），再打包整個資料夾。請用離線工具包
安裝出來的 Python 環境，或依建置頁面（[Windows](build-windows.md) / [Linux](build-linux.md)）建立的開發環境來執行。
以下的 `python` 指的都是該環境的直譯器；`-I`（隔離模式）可避免它載入目前資料夾裡來路不明的檔案。

## 典型流程

```bash
python -I tools/llm_archive.py pack models/Qwen2.5-7B archive --name Qwen/Qwen2.5-7B
python -I tools/llm_archive.py pack models/Qwen2.5-7B-Instruct archive --name Qwen/Qwen2.5-7B-Instruct
python -I tools/llm_archive.py verify archive
python -I tools/llm_archive.py restore archive Qwen/Qwen2.5-7B-Instruct restored/Qwen2.5-7B-Instruct
```

依序打包模型、驗證全部、需要時還原。每個檔案會自動以歸檔中「壓得最小」的模型為 base 存成 delta（這裡 Instruct
會自己選到 Qwen2.5-7B），所以 base 要比衍生自它的模型先打包。若 base 較晚才加入，可事後用 `rebase` 重新挑選（第 3 節）。

## 圖形介面

```bash
python -I tools/llm_archive_gui.py [<archive_root>]
```

（Windows 上把 `python` 換成 `pythonw` 就不會多開一個主控台視窗。）用 **Open** 開啟歸檔資料夾（選空資料夾即建立新歸檔）
或單一 `.oczip` 檔後，可以：用 **+ Folder…** 或 **+ Files…** 壓縮（從同一資料夾選取的檔案會自成一個模型；只選一個檔案時，
預設以檔名命名）、用 **▣ Bundle…** 把資料夾打包成一個 `.oczip`（第 6 節）、
用 **⇩ Extract…** 解壓縮清單中選取的模型或檔案（每個模型放在以其名稱命名的資料夾中，例如 `<目的地>/Qwen/Qwen2.5-7B`；
目的地已有內容不同的同名檔案時，經你同意才會取代），以及用 **✔ Verify** 驗證選取的模型或整個歸檔。清單會列出每個模型與
檔案的原始大小、壓縮後大小、比例與儲存方式（`Δ` 表示以該模型為基底的差異，`=` 表示與該檔案相同）。每個動作都是照下文的
方式執行 `llm_archive.py`，並顯示它的輸出；**✕ Cancel** 的效果等同按 Ctrl+C，歸檔裡不會留下做一半的東西。差異基底一律
自動選擇；`--base`、`rebase` 等其他選項只能用命令列；開啟 `.oczip` 時，壓縮與打包的按鈕會停用。介面文字只有英文與符號，
與系統語系無關。從離線工具包執行時，會直接開啟工具包所在的歸檔。

## 1. `pack`：把資料夾打包進歸檔

```bash
python -I tools/llm_archive.py pack <來源資料夾> <archive_root> [--name 名稱] [--base 名稱 | --no-base] [選項]
```

`<來源資料夾>` 底下的每個檔案都會先計算 SHA-256、解析格式（safetensors、GGUF、其他）、逐 tensor 壓縮，寫入
`<archive_root>/<名稱>/`（名稱中的 `/` 會轉成 `__`）。每個檔案都會再從磁碟解回並核對 SHA-256，確認無誤才處理下一個。
最後寫出 `manifest.json`。`.git/` 與 `.cache/` 資料夾（git 與下載工具的管理資料）一律略過。來源資料夾與歸檔不可互相包含。

| 選項 | 說明 |
|---|---|
| `--name 名稱` | 模型在歸檔中的名稱，例如 `Qwen/Qwen2.5-7B`（預設為資料夾名稱）；`--base`、`verify`、`restore` 都用這個名稱。以 `/` 分段，每段只能用文字、數字、`-_.+@()[]` 與空白；不可為空、不可以空白開頭或結尾、不可以句點結尾；不可為 Windows 裝置名稱（`CON`、`NUL` 等）或 `_kit` |
| `--revision 文字` | 寫進 manifest 的備註，例如你下載的上游 commit |
| `--exclude 樣式` | 略過路徑或檔名符合的檔案（可重複指定），例如 `--exclude "*.bin"` |
| `--file 路徑` | 只打包 `<來源資料夾>` 裡的這個檔案，路徑相對於該資料夾（可重複指定）：模型就只包含指定的檔案；不可與 `--exclude` 併用 |
| `--base 名稱` | 所有檔案都以這個已歸檔模型為 base，不再逐檔自動挑選 |
| `--no-base` | 所有檔案都不使用 base |
| `--delta-codec ans16\|xor` | 16-bit 浮點 tensor 的 delta 方式：ans16（預設，較小）或與 base 的 XOR 以位元平面壓縮（較快） |
| `--zstd-level N` | 標頭、metadata、文字檔的 zstd 等級，1–22（預設 19） |
| `--zstd-tensor-level N` | 量化 tensor 與未知二進位檔的 zstd 等級，1–22（預設 9） |
| `--workers N` | ans16 平行程序數，1–1024（預設為 `OC_WORKERS`，未設定則為 CPU 數） |
| `--no-dedupe` | 即使歸檔裡已有相同檔案也照樣儲存 |
| `--chunk-mib N` | 單一區塊上限，1–1024，預設 64 MiB |

輸出的每一行會顯示壓縮方式、比例，以及 delta 檔案有多少比例在 base 裡找到對應。檔案前的 `base for` 行顯示
為它挑選的 base、估算比例、次佳候選，以及單獨壓縮的估算：

```
base for model-00002-of-00004.safetensors: Qwen/Qwen2.5-7B (est. 29.2%; standalone 66.2%; 1 candidate(s), 3s)
model-00002-of-00004.safetensors   safetensors+ans16   3,685.7 MiB ->  1,086.0 MiB   29.47%  verified, 100% matched in Qwen/Qwen2.5-7B
tokenizer.json                     same                    6.7 MiB ->      0.0 MiB    0.00%  same as Qwen/Qwen2.5-0.5B:tokenizer.json
```

記錄的 SHA-256 是檔案在資料夾裡當下的雜湊值。若來源有公布雜湊值（Hugging Face 對權重檔有公布），在意來源正確性時，
請在打包前自行比對。

以相同名稱重新打包時，會像 `rebase`（第 3 節）那樣重新儲存該模型的檔案：每個容器檔都寫成目前 manifest 沒有使用的
名稱（`<path>.ocz` 與 `<path>.1.ocz` 輪流使用；原樣儲存的小檔案若內容沒變就維持 `<path>`，內容改變時則與 `<path>.1`
輪流使用），新 manifest 一次取代舊 manifest，之後才刪除新 manifest 不再使用的儲存檔並列出。所以即使 `pack` 中途被
打斷，歸檔仍然完整、可以通過驗證：磁碟上的 manifest 描述的不是舊檔就是新檔，而那些檔案都在。重新打包完成之前，歸檔
所在的磁碟需要同時容納新舊儲存檔。資料夾裡的其他檔案（例如你自己的修復資料或筆記）
不會被覆寫或刪除，但已經過時，請重建。內容沒變的小檔案會沿用原本的儲存檔（只做檢查、不重新複製，所以打包途中來源檔若被
改動也碰不到它）；若那份儲存檔已損毀，會重新儲存。

若重新打包會改變或移除其他模型需要的檔案（其他模型以它為 delta base，或以 same-as 引用與它相同的檔案），`pack` 會在
寫入任何東西之前拒絕，並列出這些檔案與模型。請把新版本用另一個名稱打包，或先讓那些模型不再依賴它（第 3 節的
`rebase <archive_root> <它們的名稱> --no-base --no-dedupe`）後再重新打包。另外，若名稱對應的資料夾已存放其他模型或
其他檔案（例如已歸檔 `A/B` 時打包 `A__B`，兩者都對應到 `A__B` 資料夾），或歸檔中有無法讀取或不可信的
`manifest.json`（`verify` 會指出是哪個），`pack` 也會拒絕。

來源資料夾裡的檔案可以是任何名稱，包括 `manifest.json` 或 `*.part`：它會以其他名稱儲存（例如 `manifest.json.1`），
還原時恢復原名。儲存檔一律使用各系統都能用的名稱，所以歸檔可以在 Windows 與 Linux 之間複製；只有某個系統允許的
檔名（例如 Linux 上名為 `a\b.txt` 的檔案）仍會照原名記錄，由 `restore` 視需要調整（第 5 節）。儲存檔沿用
來源的資料夾結構；若某個儲存檔與某個儲存資料夾會用到同一個名稱（例如 Linux 上的檔案 `a_` 與資料夾 `a?/`，或上次
打包後由資料夾改成檔案的項目），後者會加上 `.1`。

只有一般檔案會被打包；指向檔案的連結視同該檔案。指向資料夾的連結（symlink，或 Windows 的 junction）、FIFO、socket、
裝置檔，以及失效的連結，都會讓 `pack` 在讀取任何東西之前停止並列出：請用 `--exclude <名稱>` 略過，或把連結指向的
資料夾另外當成一個模型資料夾打包。這樣不會意外收進來源資料夾以外的東西，也不會不聲不響地漏掉資料夾裡的東西。

若某個檔案與歸檔中已有的檔案相同，但那一份已經損毀，新檔案會完整儲存（不用引用），`pack` 也會提醒：請執行 `verify`
找出損毀的模型。

## 2. 如何挑選 base

不指定 `--base` 時，資料夾裡的每個檔案會各自從歸檔中已有的模型挑選 base：

1. **候選**：歸檔中所有 tensor 能依名稱與此檔配對的模型（同格式；dtype 與大小相同、其他浮點 dtype，或前段列相同，
   詳見[原理](principles.md#4-matching-tensors-not-files)）。只讀 manifest，不解壓任何東西。
2. **試算**：從檔案的 tensor 中平均取 16 段（每段 512 KiB），用快速估算對每個候選試壓，並與單獨壓縮比較。
   候選模型只解開這些片段需要的部分。
3. **決定**：估算最小者勝出，但必須比單獨壓縮至少省下 1%，否則不用 base。估算相同時，選引用鏈較短的（還原較快）。

直接或間接依賴「正在打包的模型」的其他模型不會成為候選，所以重新打包不會產生循環引用。同一個資料夾裡的不同檔案
可以選不同的 base（例如 BF16 GGUF 選 BF16 GGUF，Q4_K_M 選 Q4_K_M）。

實測：[benchmarks](benchmarks.md#automatic-base-selection) 中的社群微調模型全部選到與手動挑選相同的 base，
估算值與實際大小只差幾個百分點；每個檔案挑選約需數秒。

**順序只透過「歸檔裡已有什麼」產生影響。** 模型只能用比它先打包的模型當 base。若之後才加入更近的祖先（例如先收了
微調模型、後收 Instruct），對該微調模型執行 `rebase`，或執行 `rebase --all`（第 3 節）：它會依歸檔現有的內容重新
挑選 base，不需要原資料夾。通常還得先做一步：以自動挑選打包時，祖先本身會選該微調模型當 base，並引用相同的
tokenizer 檔，而微調模型不能用依賴它的模型當 base。兩種方向的歸檔大小差不多（delta 正反方向一樣小），所以
`rebase` 不會去動它。若希望祖先能單獨還原，先讓它獨立，再對微調模型 rebase：

```bash
python -I tools/llm_archive.py rebase archive Qwen/Qwen2.5-0.5B-Instruct --no-base --no-dedupe
python -I tools/llm_archive.py rebase archive --all
```

`--base 名稱` 可強制指定 base（即使沒有幫助也照用；壓不小的區塊仍會單獨儲存）。`--no-base` 讓模型不使用 delta，
例如希望某個 base 能單獨還原時；再加上 `--no-dedupe` 就完全不依賴其他模型。`tools/compare_tensors.py <base 檔案>
<目標檔案>` 可詳細列出兩個檔案的對應情形（完全相同、dtype 轉換、前段列、權重完全相同的比例）。

## 3. `rebase`：重新挑選已歸檔模型的 base

```bash
python -I tools/llm_archive.py rebase <archive_root> <名稱> [<名稱> ...] [選項]
python -I tools/llm_archive.py rebase <archive_root> --all [選項]
```

把已在歸檔中的模型，依 `pack` 現在會採用的方式重新儲存：每個檔案從歸檔現有的模型中重新挑選 base（同第 2 節），
與其他模型中已儲存的檔案完全相同者改為 same-as 引用。不需要原資料夾。base 是從歸檔內容挑選的，所以只有 base
改變的檔案才會被還原（到 `OC_TMP`）、重新打包、從磁碟解回並核對 SHA-256。未指定 `--base` 或 `--no-base` 時，
新的儲存檔比較小才會採用。

新的儲存檔寫在舊檔旁邊（若 `<path>.ocz` 已被使用，就寫成 `<path>.1.ocz`），舊檔要等新 manifest 取代舊 manifest
之後才刪除，所以即使 `rebase` 中途被打斷，歸檔在任何時刻都是完整的。引用此模型的其他模型不受影響：它們是以路徑與
SHA-256 指名它的檔案，而這兩者都不會改變。與 `pack` 相同，依賴「正在 rebase 的模型」的其他模型不會成為候選。

| 選項 | 說明 |
|---|---|
| `--all` | 所有 base 為自動挑選的模型，base 先於使用它的模型處理；以 `--base` 或 `--no-base` 打包或 rebase 的模型會略過（指名即可納入） |
| `--base 名稱` | 所有檔案都以這個模型為 base 重新打包，即使變大也照做 |
| `--no-base` | 所有檔案都不使用 base 重新打包，即使變大也照做 |
| `--no-dedupe` | 把指向其他模型的 same-as 引用改回實際儲存（搭配 `--no-base`：模型即不再依賴任何其他模型） |
| `--dry-run` | 只顯示會改變什麼，不做任何更動 |
| `--delta-codec`、`--zstd-level`、`--zstd-tensor-level`、`--workers`、`--chunk-mib` | 同 `pack` |

每個可能改變的檔案各有一行（未改變，或現在的儲存方式與它取代了什麼），最後是該模型儲存大小的前後對照。以下是比
0.5B Instruct 先打包的 Gensyn 微調模型移到 Instruct 上：

```
rebasing mazdaypci/Qwen2.5-0.5B-Instruct-Gensyn-Swarm-wily_padded_mink  (delta base chosen per file)
  base for model.safetensors: Qwen/Qwen2.5-0.5B-Instruct (est. 1.4%; standalone 66.2%; 1 candidate(s), 3s)
  model.safetensors                  safetensors+ans16     942.3 MiB ->     13.7 MiB    1.45%  verified, 100% matched in Qwen/Qwen2.5-0.5B-Instruct  (25s)
    replaces model.safetensors.ocz (627.6 MiB as safetensors)
  vocab.json                         same                    2.6 MiB ->      0.0 MiB    0.00%  same as Qwen/Qwen2.5-0.5B-Instruct:vocab.json  (0s)
    replaces vocab.json.ocz (0.8 MiB as zstd)
  removed model.safetensors.ocz (no longer used by the manifest)
  removed vocab.json.ocz (no longer used by the manifest)
  13 file(s) without tensors unchanged
  stored 630.6 MiB -> 15.8 MiB  (-97.5%)
```

`OC_TMP` 需要容納正在重新打包的檔案，以及它新舊 delta 所讀取的 base 檔（7B 的一個分片：兩個 3.7 GB 的檔案）；
歸檔所在的磁碟則需要容納新的儲存檔，直到舊檔刪除為止（最多再一個）。`rebase` 會在處理每個檔案前檢查空間。
被改變的模型，請重建它的修復資料（見 [protection.md](protection.md)）。

## 4. `verify`：驗證

```bash
python -I tools/llm_archive.py verify <archive_root> [<名稱> ...] [--no-lock]
```

檢查每個檔案與其所有依賴的儲存雜湊，在記憶體中解碼並比對原始 SHA-256。結果為 `ALL VERIFIED (<n> files)`，或逐一列出失敗原因；
有任何失敗時結束碼不為 0；什麼都沒驗證到時（資料夾不存在，或裡面沒有模型）結束碼也不為 0。缺少或無法讀取的儲存檔，以及無法讀取、或含有不安全路徑或名稱的 `manifest.json`，都會像
其他失敗一樣列出，其餘項目照常檢查。建議在複製歸檔後、以及冷儲存定期執行。`<archive_root>` 也可以是 `.oczip` 檔（第 6 節）。

## 5. `restore`：還原

```bash
python -I tools/llm_archive.py restore <archive_root> <名稱> <目的資料夾> [--overwrite] [--file 路徑 ...] [--no-lock]
```

寫回與原檔逐 byte 相同的檔案並核對 SHA-256。delta 需要 base 模型在同一個歸檔根目錄裡；ans16 delta 會先把用到的
base 檔還原到 `OC_TMP`。`--file 路徑`（可重複指定）只還原這些檔案，路徑以歸檔中的為準（即 `verify` 列出的 `<名稱>:` 之後的部分）；
每個檔案會放在完整還原時的同一個位置。`<archive_root>` 也可以是 `.oczip` 檔（第 6 節）。

寫入任何東西之前，會先檢查 manifest 裡的每個路徑：必須是相對路徑、不含 `..`，而且必須落在 `<目的資料夾>` 之內；
還原絕不經過連結寫入，所以 `<目的資料夾>` 底下途經的資料夾若是符號連結或 junction，就會停止。這個系統不允許的檔名會像其他解壓縮工具一樣調整：在 Windows 上，
`<>:"\|?*` 與控制字元都換成 `_`，結尾的句點與空白也換成 `_`，裝置名稱則在主檔名後加 `_`（`CON.txt` -> `CON_.txt`）。
若因此有兩個檔案變成同一個檔案（例如 `a\b.txt` 與 `a_b.txt`，或 Windows 上的 `a.txt` 與 `A.txt`），後者會在副檔名前加上 ` (1)`、` (2)`……；檔名不需調整的檔案優先保留原名。
每個改名的檔案都會列出（`ok  a\b.txt  (as a_b (1).txt)`），而且名稱只取決於歸檔內容，所以重跑還原時每個檔案都會
落在同一個位置。目的資料夾裡已有內容正確的檔案會保留（顯示 `already there`），所以中斷的還原可以直接重跑；已有但內容不同
的檔案會讓還原停止，除非加上 `--overwrite`。每個檔案都先寫到同資料夾裡以隨機名稱新建的暫存檔，確認大小與 SHA-256
正確後才移到定位，所以失敗的檔案不會蓋掉原本的檔案；沒有 `--overwrite` 時，就算還原期間有其他程式在目的地建立了同名
檔案，也不會被覆蓋，因為「確認不存在」與「放上檔案」是同一個步驟（用 hard link；檔案系統不支援時，改用遇到同名就失敗的
改名）。少數兩者都不支援的檔案系統（某些 FUSE 或網路掛載）上，沒加 `--overwrite` 的還原會停止並說明原因；這時請還原到
空資料夾並加上 `--overwrite`。目的資料夾若在歸檔之內，即使加上 `--overwrite` 也會拒絕。

## 6. `bundle`：把整個歸檔打包成一個 `.oczip` 檔

```bash
python -I tools/llm_archive.py bundle <archive_root> <檔名.oczip> [--verify | --no-verify] [--delete] [--overwrite]
```

把 `<archive_root>` 裡的所有東西（各模型、`_kit/`，以及你放在裡面的其他資料）放進一個檔案：格式是 ZIP（ZIP64），
副檔名刻意取為 `.oczip`，以免被當成一般 ZIP。容器檔與本來就壓縮過的檔案（wheel、安裝檔、圖片等）原樣存放（store），
其他檔案若用 Deflate 壓得更小就壓縮。鎖檔與殘留的 `.oc-*.part` 不會放進去；資料夾裡有任何連結時會停止。`.oczip` 先寫到
旁邊的暫存檔，完整寫完才放到定位，所以中途停止不會留下任何東西。

`verify` 與 `restore`（以及圖形介面）都可以用 `.oczip` 取代資料夾，直接讀取、不必先解開：容器檔在裡面是未壓縮的，
直接依位移讀取。`.oczip` 是唯讀的：要再 `pack` 進去或 `rebase`，請用任何 ZIP 工具解開成資料夾（只認副檔名的工具，
可把副本改名為 `.zip`），在該資料夾上作業後再重新打包。

| 選項 | 說明 |
|---|---|
| `--verify` | 寫完後讀回驗證：每個檔案都與放進去的內容比對（SHA-256），並像 `verify` 一樣從 `.oczip` 驗證每個模型。預設不做 |
| `--delete` | 之後刪除 `<archive_root>` 裡已放進去的檔案（資料夾清空後也一併刪除）。除非加上 `--no-verify`，否則會先驗證；任何一項失敗就什麼都不刪 |
| `--no-verify` | 與 `--delete` 併用：不驗證就刪除 |
| `--overwrite` | 取代同名的既有檔案 |

`.oczip` 必須放在 `<archive_root>` 之外。`bundle` 執行期間會鎖住歸檔（加 `--delete` 時為獨占），所以這段期間沒有人能改動
資料夾。`.oczip` 的修復資料（例如 PAR2）請針對整個檔案製作，放在它旁邊（第 7 節）。

## 7. 防護與修復

本工具負責偵測損毀，不負責修復。請保留多份副本，並／或用你選擇的工具加上修復資料（PAR2、RAR 修復記錄、
ZFS/Btrfs/ReFS 等），詳見 [protection.md](protection.md)。

## 環境變數

| 變數 | 作用 |
|---|---|
| `OC_TMP` | 暫存還原 base 檔的資料夾，需容納 delta 用到的 base 檔；`rebase` 重新打包的檔案也放在這裡 |
| `OC_WORKERS` | ans16 平行程序數上限，須為 1 以上的整數（記憶體小的機器請調低）；其他值會讓所有指令直接報錯 |

## 歸檔注意事項

- 模型之間可能互相依賴（delta、相同檔案引用）。**不要刪除被其他模型依賴的資料夾**，`verify` 會指出缺少的依賴。
- 重新打包同一個模型時，請使用相同的 `--name`，其他模型對它的引用才不會失效。
- 把 `_kit/`（離線工具包）和各模型放在一起，並在不同媒體保留多份副本。
- 一次只做一項變更：`pack` 與 `rebase` 執行期間會鎖住整個歸檔；`verify` 與 `restore` 可以同時執行，但不能在變更進行中
  執行。發現歸檔正在使用時會立刻停止並說明，請稍後再執行。鎖是系統對歸檔根目錄中空檔 `.overcompressing.lock` 的鎖，
  程式結束時（不論如何結束）自動釋放。`verify` 與 `restore` 會以唯讀方式開啟既有的鎖檔，所以鎖檔屬於誰、權限如何都不影響。
  若完全無法加鎖，只有在作業系統回報歸檔所在的磁碟是唯讀時（光碟、防寫磁碟、唯讀掛載）才照常執行，因為這時不可能有人
  寫入；其他情況會停止。加上 `--no-lock` 可強制執行：請只在確定這段期間沒有人會改動歸檔時使用（例如對你唯讀、但別人
  可寫的共用資料夾）。
- 在歸檔的模型資料夾與還原目的資料夾底下，絕不跟隨連結：若 `pack`、`rebase` 或 `restore` 執行期間有其他程式把某個資料夾
  換成符號連結或 junction，寫入會被拒絕，而不會寫到別處。在 Linux 與 macOS 上每一步都有保障（逐層開啟資料夾、不跟隨連結）；
  在 Windows 上則是盡力而為（每層資料夾使用前檢查，每個新檔案放上前再透過它自己的 handle 確認實際位置）。工具執行期間，
  請不要讓不受信任的使用者能寫入歸檔或目的資料夾。

## 離線工具包

在有網路時建置一次，之後完全離線可用：

```bash
python -I kit/build_kit.py <archive_root>/_kit [--cache <舊工具包>]
```

在還原用的機器上：

```powershell
powershell -ExecutionPolicy Bypass -File _kit\install_windows.ps1
```

```bash
sh _kit/install_linux.sh
```

安裝腳本會用內附的 wheel 建立虛擬環境（找不到 Python 3.13 時會先安裝內附的版本），並印出可直接使用的
`restore` / `verify` 指令。詳見 `_kit/README.md`。若是 `.oczip`，請先用任何 ZIP 工具解出其中的 `_kit/`
（7-Zip 可直接開啟；Windows 檔案總管則需把副本改名為 `.zip`），再把 `.oczip` 交給 `verify`、`restore` 或圖形介面。

## 疑難排解

| 狀況 | 解法 |
|---|---|
| `stored file corrupted` | 從其他副本換回該檔，或用你的修復資料修復（見 [protection.md](protection.md)） |
| `... is missing from the archive` | 把被引用的模型資料夾放回歸檔根目錄 |
| 還原或驗證 delta 時磁碟空間不足，或出現 `not enough room to rebase` | 將 `OC_TMP` 指到空間較大的資料夾 |
| 記憶體不足 | 將 `OC_WORKERS` 設為 4 以下 |
| 程式被強制中斷後，暫存資料夾留下 `oc-base-*.bin` 或 `oc-rebase-*` | 直接刪除 |
| `rebase` 被強制中斷後，模型資料夾裡有 manifest 未列出的 `*.ocz` 或 `*.part` 檔 | 直接刪除（manifest 仍使用舊檔） |
| `re-packing X would change or drop files other models need` | 把新版本用另一個名稱打包，或先讓列出的模型不再依賴它（`rebase … --no-base --no-dedupe`） |
| 還原時出現 `already exist … with other contents` | 還原到空資料夾，或加上 `--overwrite` |
| 打包或 rebase 時出現 `<資料夾>/manifest.json: …` | 從其他副本換回該 manifest，或把該資料夾移出歸檔 |
| 程式被強制中斷後留下 `.oc-*.part` | 直接刪除（未完成的暫存檔，沒有任何東西引用它） |
| `… is a link or not a folder: not followed` | 歸檔或目的資料夾中有資料夾是符號連結或 junction：改成實際的資料夾（或還原到別處） |
| `a file of that name appeared in the destination while restoring` | 有其他程式寫入了目的地：重跑還原，或加上 `--overwrite` |
| `cannot put … in place … without the risk of replacing a file` | 檔案系統既不支援 hard link，也不支援遇到同名就失敗的改名：還原到空資料夾並加上 `--overwrite`；歸檔本身請放在其他檔案系統上 |
| 打包時出現 `… and the archive … are in one another` | 打包歸檔以外的資料夾，並存到該資料夾以外的歸檔 |
| `… is a single-file archive (.oczip): it is read-only` | 把它解開成資料夾（它是 ZIP），在資料夾上 pack 或 rebase，再重新 `bundle` |
| `… is compressed or encrypted in the .oczip: containers must be stored as they are` | 這個 `.oczip` 被其他 ZIP 工具重寫過：請從資料夾用 `bundle` 重新製作 |
| `the archive is in use …` 或 `… is being changed …` | 另一個 `pack`、`rebase`、`verify` 或 `restore` 正在使用這個歸檔：等它結束後再執行 |
| 打包時出現 `… is a link to a folder`、`… is not a regular file`、`… cannot be read` | 用 `--exclude` 略過，或把連結指向的資料夾另外打包 |
