# AGENTS.md

Notes for agents working on OverCompressing: what it is, how it is built, the decisions behind it, and how its owner
likes the work done. Read this before changing anything.

## What it is

A lossless archiver for open-weight LLM files (safetensors, GGUF, anything else in a model folder). Every file is
restored bit for bit and checked against its SHA-256. Float tensors are coded as byte planes (zlib Huffman-only or
zstd, whichever is smaller); quantized tensors, metadata and text go to zstd; a fine-tune is stored as a delta
against its base model (`ans16`, a context-modelled rANS coder in NumPy, or an XOR delta). Python 3.13, NumPy and
zstandard only: no compiled code of our own, no PyTorch. The project began on ZipNN and migrated to its own
byte-plane codec; nothing of ZipNN is left.

## Layout

| path | what |
|---|---|
| `tools/llm_archive.py` | the whole tool: CLI (`pack`, `list`, `verify`, `restore`, `remove`, `rename`, `rebase`, `export`), codecs, format, safety |
| `tools/llm_archive_gui.py` | Tkinter window (a file browser; a pack opens as the list of its models) that runs `llm_archive.py` as a child process |
| `tools/ARCHIVE_FORMAT.md` | the on-disk format, byte by byte (enough to restore by hand) |
| `tools/test_ans16.py`, `tools/test_formats.py` | the test suites |
| `tools/compare_tensors.py` | how a fine-tune lines up with a base |
| `kit/` | the hash-locked requirements a portable copy is built from; the rest (`build_kit.py`, install scripts, keys, provenance) is the retired offline kit, kept for reference |
| `docs/` | usage (English then Chinese, one file), principles, scope, benchmarks, build guides, protection |
| `dev/` | experiments and measurement scripts |
| `archive/` | gitignored leftovers of format 3 (old per-model archives, test folders): **not a pack**, no test data for format 4. To test, pack folders of `models/` (gitignored downloads of real models) into a scratch pack; the owner's own packs elsewhere are read-only for agents |

## Concepts and terms

Format version 4. The CLI (`llm_archive.py`) and the window each have a `VERSION` ("0.5"), raised by hand; their
build is the date and commit each file was last changed in (`build_of`: a portable copy's `tools/BUILD`, written by
the build guide, else `git log`/`rev-parse` only, nothing that runs hooks), ", edited" when the file differs. The window's
title shows both, separately (owner's wish: easier debugging); `--version` the CLI's; manifests' `tool` the CLI's. User-
facing text uses these words:

- **Pack**: holds models. Two forms with the same tree inside: a **pack folder** (`ocpack.json`, `models/`, `blobs/`,
  optional `_kit/`; for upkeep) and a **pack file** (`.oczip`, a ZIP of that tree; for cold storage, read-only,
  written whole, read in place). `ocpack.json` is the marker: a folder without it is not a pack.
- **Blob**: one stored file, `blobs/<ab>/<sha256>.ocz`, an OCZ4 container named by the SHA-256 of the original file and
  shared by every model that has that file (dedup is automatic and total). Its header lists its **bases** (blobs it is
  coded against, by SHA-256, plus informational `model`/`path`); it ends with the SHA-256 of all its bytes before.
- **Manifest**: `models/<Org__Name>.json`, a model's list of files (path, size, sha256, and for parsed files format and
  tensor table). A model is its manifest; nothing refers to a model by name, so `rename` touches only the manifest.
- **The graph**: what needs what follows from manifests plus blob headers; it grows as models are packed and is never
  stored (no index, no SQLite: decided). A model may need several models and be needed by several (a DAG at blob
  level; at model level it can even go round). It is walked when needed: `Graph` (sweep, list, rebase), never kept.
- **Sweep**: after `pack`, `remove`, `rebase` (and `export --delete`), blobs that no manifest needs, directly or as a
  base down the chain, are deleted; nothing is deleted if any manifest or needed blob cannot be read. A removed
  model's blobs that others still need stay ("kept"), trimmed to what they read; `rebase` of those others lets them go
  when that makes the pack smaller.
- Base choice: per file, among the other models (not the model itself); a blob is never coded against one that
  needs it (`Graph.above`, also when a damaged or partial blob is stored again), and chains stay at most 32 deep
  (`MAX_REFERENCE_DEPTH`). References are per region (a tensor, or up to 64 MiB of one), into the base file's bytes.
- **Kept bases are trimmed** (owner: reclaim aggressively): after every sweep, a blob no model lists but others are
  coded against becomes **partial** (`trim_kept`, `write_trimmed`: header `partial`, regions `dropped`, frames of the
  kept ones copied as they are, written and checked, then put in place in one step); dependents are untouched (they
  address the base file's bytes). A partial blob restores only as a base (dropped regions as zeros); verify checks
  that what reads it is kept; pack stores the original whole again over it.
- **Rebase judges by the whole pack**: a file changes only if its blob gets smaller, except that `weigh_kept` lets go
  of a kept base when re-storing every file coded against it (all among the models rebased) is estimated to save at
  least `RELEASE_GAIN` (10%) of its room; those files are then stored however they come out. The last line is the
  pack before and after. Models with hand-set bases are skipped by `--all` (and by the window unless ticked).
  Decisions are per file (matching per tensor across the base model's files); `--file` (one model) rebases only
  those files, and a choice made then (or by `pack --add --base/--no-base`) is the file's own (`files[].base_choice`,
  `with_choice`): `--all` and naming the model leave such files; naming them with `--file` re-chooses. The window has
  ⇄ Rebase in the pack view (models) and the model view (files).
- After a pack, `rebase_hint` names models that may store smaller against what came in (manifests only).
- **No offline kit** (owner's decision, 2026-10-22: agents do the building anyway). Nothing is built or shipped with a
  pack; `docs/build-windows.md` and `docs/build-linux.md` make a **portable copy** (python-build-standalone CPython
  3.13.16 with numpy and zstandard from `kit/requirements-*.lock`, the tools, `tools/BUILD`, launchers), as exact,
  checkable steps. The tool's `_kit/` handling stays, passive: a folder holding only `_kit` can become a pack, restore
  never writes into it, export carries it, the window started from `_kit/tools` opens the pack. So a portable copy put
  in a pack folder as `_kit` travels with the pack. `kit/build_kit.py` and the install scripts are retired: do not
  revive them.

## Commands

```
pack    <source_dir> ... <pack_folder | new.oczip> [--name NAME] [--replace | --add] [--base MODEL | --no-base] [--exclude GLOB | --file PATH | --files-from FILE]
list    <pack> [MODEL] [--json]
verify  <pack> [MODEL ... | --models-from FILE] [--file PATH | --files-from FILE]
restore <pack> MODEL <dest_dir> [--overwrite] [--file PATH | --files-from FILE] [--flat]
remove  <pack_folder> (MODEL ... | --models-from FILE) [--file PATH | --files-from FILE] [--dry-run]
rename  <pack_folder> MODEL (NEW_NAME | --file PATH NEW_PATH)
rebase  <pack_folder> (MODEL ... | --models-from FILE | --all) [--file PATH | --files-from FILE] [--base MODEL | --no-base] [--dry-run]
export  <pack> <new.oczip> [MODEL ... | --models-from FILE] [--verify | --no-verify] [--delete] [--overwrite]
```

Run with `python -I` (isolated mode). Long lists go in list files, one name per line (`read_list`): `--models-from`
(verify, remove, rebase, export) and `--files-from` (pack, verify, restore, remove); an empty list is refused. The
window uses them when a command line would pass `ARGS_LIMIT` (8,000 characters; Windows takes 32,767). Owner's choice:
no `@FILE` (it would make every value starting with `@` special) and no NUL-separated lists (names with line breaks are
not worth it). A model is named by its name (`Org/Name`) or its manifest's file stem
(`Org__Name`). Every command takes `--json` (every stdout line a JSON object `{"event", "text", ...}`; errors end with
`{"event": "error", "code", "text"}` and exit 1; `code`, given by `fail`, is one of `ERROR_CODES`: exists, name_taken,
busy, no_room, damaged, cancelled, other, the failures a program can act on, the text being for people only, and the
window decides its `--overwrite` retry by it; Ctrl+C is `cancelled`, without a traceback) and `--progress` (stderr, about once a second, also when nothing can be counted; on by
default on a terminal). `pack` into a new `.oczip` writes the ZIP as it goes (no folder in between) and checks each
blob from the unfinished file; several sources in one command can be bases for one another (`--base` goes first).
Pack files are never changed: no rebase, no adding (extract, change, export again); `export` from one makes a new,
smaller one (no `--delete`). A name the pack folder holds already needs `--replace` (the model becomes these files)
or `--add` (these files join it, same paths replaced), checked before anything is written. Every change rewrites
`ocpack.json` last (`touch_marker`, a `changed` time), so a watcher of that one file sees each complete change once.

## Safety rules the code keeps

- Every written file goes to a fresh `.oc-*.part` (created exclusively), is synced, then put in place. A blob is
  decoded back from its temporary file to its SHA-256 before it takes its name (`store_blob`; ans16 failures redone as
  XOR, except in a new pack file, which then stops). `export --verify` likewise reads the pack file back from its
  temporary file before placing it: one that fails is never there, nor replaces a file (owner's choice). Without `--overwrite`, placing is atomic no-clobber: hard link,
  else `renameat2`/`renamex_np`; if neither, fail, never check-then-rename.
- Order: blobs first, manifest replaced in one step last, sweep after. A rebase replaces a blob by one that restores
  the same file, so nothing that needs it changes. The pack is complete at every moment.
- `SafeTree`: links below a pack folder or restore destination are never followed (POSIX: dir fds + `O_NOFOLLOW`;
  Windows: best effort, reparse-point checks and the new file's final path read through its handle).
- One lock per pack folder (`.overcompressing.lock`): pack, remove, rename, rebase and `export --delete` exclusive;
  list, verify, restore and export shared; never waits. Pack files are not locked.
- Untrusted input is checked before decoding: blobs (trailing SHA-256, header, frames, base references inside the
  bases, no trailing bytes, zstd output capped), manifests (schema; strict JSON: no duplicate keys, no NaN/Infinity),
  pack files (no unsafe or duplicate names, blobs stored, each blob's local header agreeing with the central directory
  on name, method and encryption: `member_span`, so the tool and a standard ZIP reader judge a pack file alike). restore
  checks what it writes and the blobs it needs, not the whole pack: verify is the check of a pack (documented). A header value looked up in a set or dict, or collected
  into one, is checked to be a string first: a JSON list or object there is unhashable (a TypeError, not a refusal;
  found by the downstream team). Bad input is a refusal, never a traceback; `OC_WORKERS` too (ASCII digits only).
- Restore never writes into a pack folder or anything inside one (`place_problem`); pack never reads from one, and a
  new pack file is never written into one. Names a system cannot hold become `_`, collisions get ` (1)`.
- A pack folder is only created in a new, empty or `_kit`-only folder; any other folder without `ocpack.json` is
  refused. A pack of the earlier format (version 3) is just not a pack (no `ocpack.json`): no hint, no migration
  tool, by decision.

## Window (GUI) conventions

- Text is Unicode symbols where they say it (BMP only, so Tk 8.6 renders them), else English. No localization.
- Dialogs: sentence case, short; first line bold (`heading`: what it is about), hints gray (`note`), errors red in a
  line kept for them; a path on a line of its own, folders from its middle left out to fit (`path_line`, `shorten`;
  the whole on hover; `short` in message boxes); lists at most 5 names and "and N more" (`lines`); confirmations state
  the facts, then ask last. The tool's messages start in lower case: `sentence()` before a dialog shows one, and a
  failed run shows only its last line (the log has the rest). Buttons carry no "…".
- The log pane is dark, a little blue (owner's choice: what most tools use); the up button is bold and wide, as high
  as the path bar.
- Reading (listing a pack, a model, the remove preview) is done in-process through `llm_archive` (`pack_summary`,
  `model_summary`, `removal`), closing pack files afterwards. Every change runs `llm_archive.py` in a child process
  through `RUNNER` (at below-normal priority), with `--json --progress`; the window parses those lines (progress events go to the busy window,
  the rest to the log). Closing the child's stdin cancels as Ctrl+C would. On Windows the child polls stdin with
  `PeekNamedPipe`: a blocking read would stall every ans16 worker process at start (a real bug once). On POSIX it
  reads the file descriptor (`os.read`), never `sys.stdin.read()`: the buffered reader's lock, held while it waits,
  is inherited held by forked workers, which then hang closing stdin (the same bug on Linux, found downstream).
- While a job runs, a modal window in front holds all input and has Cancel. Activity is a turning ◐; how far it is
  is text (`37% · 5.6 GiB / 15.1 GiB`), never a bar.
- A pack shows first as a flat list of its models (sortable by name, Packed time, size, …), with what each needs in
  the Needs column (removed ones as `(removed)`). **⑂ By base** shows a tree instead, each model under the model most
  of its coded bytes refer to (`model_summary` "base"): storage, not family; the owner chose this name so nobody reads
  it as a pedigree. The text filter works in every view; by base it keeps the chain above a match, in gray. Columns are sized to what they hold
  (`fit_columns`, on every change of view, listing or list width): numbers and dates as wide as their texts; Name,
  Type and Needs too as far as the width allows, then shrunk in proportion down to their least widths (`COLUMNS`;
  the name's 200), what is left over to Needs; beyond that the list scrolls sideways. A cut cell shows its whole text
  on hover (`hover`). The owner did not want the name taking all the room while Needs stayed cut.
- The line under the list (`say_where`, `sum_up`) sums the place up: a pack as what its models would take on disk →
  what the pack takes (ratio), a model likewise; a selection first, each blob counted once.
- Stored and ratio of a file are those of its blob, the same in every model that shares it (never 0 for "the second
  one": that would depend on order and change when another model is removed); sharing shows as `= also in <model>` and
  in the model's status line. `pack`'s file lines show instead what the run added (`stored already`: 0.0 MiB).
- Buttons (and the right-click menu) depend on the place shown (`BUTTONS`, `lay_out`): a folder has + Pack to,
  ↻ Refresh, − Remove; a pack has + Pack in, ⤓ Save .oczip (`export`), ⤒ Extract to (`restore`), ✔ Verify, − Remove,
  ✎ Rename; a model has + Add file (`pack --add --file`, at the model's top level), ⤒ Extract to, ✔ Verify, − Remove,
  ✎ Rename (a file's path). In a pack file, Pack in, Add file, Remove and Rename are off. No "…" on buttons; no
  symbols outside the BMP (no 💾). Left of the buttons, the mode in the legend's word and color (`mode`: Folder,
  Pack folder, Pack file, Model; `in pack file` beside a model of one).
- One rule for button states (`update_buttons`, owner's choice): ↻ Refresh, + Pack in and + Add file can always be
  used where the place can be changed; every other button acts on what is selected and is off while nothing is
  (Rename: one row). Nothing selected never means "everything": Ctrl+A selects every row the filter lets through
  (not the gray chain rows); a selection of every model makes the command name none (the whole pack), of every file
  of a model the whole model. Selecting: click, Ctrl/Shift+click, drag (`press`/`drag`, scrolling past the edges),
  Ctrl+A (`select_all`; text fields keep their own); a click on empty space or Esc selects nothing. The keys are named
  beside the filter: "F5 Force refresh · Ctrl+A Select all".
- − Remove in a folder sends to the Recycle Bin (`to_trash`: `SHFileOperationW` with undo on Windows, the
  freedesktop trash on Linux) and never deletes for good: a drive that is not a fixed local one, a bin set to delete
  at once or one too small is refused, and the user deletes by hand. Pack to and Remove are off inside a pack
  folder's own folders.
- The list updates itself (`watch`): one time stamp (a pack folder's `ocpack.json`, a pack file, or the folder shown)
  is looked at every 5 s (`WATCH_SECONDS`), and on mouse movement or a click if 5 s have passed since the last look;
  the place is listed again only if it changed, keeping the selection. Nothing is looked at while a job runs, a
  dialog is open or the window is minimized. F5 lists again at once (owner's rule: no reading more often than that).
- Driving the window in tests: click dialog buttons instead of sending keys (keys need the system focus); keep test
  windows topmost only for the moment of a screenshot; do not leave windows covering the user's screen.

## Speed

- What runs at once: ans16 regions in worker processes, one per CPU the process may use (`ans16_workers`; at most 61
  on Windows, the most Python's pool takes there: `worker_pool`; on POSIX never forked from the tool itself, whose
  threads may hold locks a fork copies held: a fork server where the default is `fork`, i.e. Linux before 3.14, so a
  script driving `llm_archive` in-process needs `if __name__ == "__main__"` there as on Windows); byte-plane and zstd
  frames on threads (`frame_workers`, up to 8) through `ordered_map`, the main thread reading and writing in order
  (CRCs on the threads too); files hashed and blobs checked several at once (`hash_workers`, up to 4), each read a
  block ahead of its hashing (`blocks_ahead`). `OC_WORKERS` caps all of them. One file's SHA-256 is one sequence
  (about 2 GB/s here) and stays SHA-256: it names the blob and is what Hugging Face publishes.
- Byte planes are byte operations into arrays made once (`byte_planes`; `join_planes` builds a bytearray in place),
  with no copies of a whole region. Measured (2026-10-22, 7B): what held threads back was memory traffic from
  whole-width temporaries, not the GIL; worker processes were no faster, and slower to decode (the decoded bytes
  cross a pipe). Planes and payloads are byte for byte what they were (checked against the earlier code).
- verify restores a base once: `bases_first` puts a file that others are coded against just before the first of
  them, and its restored bytes are kept (`SideCopy`, in `OC_TMP`) until the last one is checked; a copy that cannot be
  written only means they restore it themselves.
- The window runs its jobs at below-normal priority (`BELOW_NORMAL_PRIORITY_CLASS`; `nice` 10 in `RUNNER` elsewhere),
  so they use idle cores and give way to the user; the command line keeps the normal priority.
- Speed work is measured on real models before and after (cores in use per step, not only the total time):
  `docs/benchmarks.md`. ans16 itself (NumPy rANS, about 8 MB/s per process) is the limit for deltas.

## Damage and repair

The tool detects damage (SHA-256 per blob and per file, CRC per region); the format carries no repair data.
Bit-level damage is the job of the hardware and the file system (RAID, ECC, ZFS/Btrfs/ReFS); do not build repair
schemes. What the tool does (owner's design): `verify` checks in three passes (`scan`: structure from headers;
content; structure again, so a file lost through its base is told from one whose own blob is at fault) and ends a
failure with a damage report (`damage_report`; text, and a `damage` event): what is at fault, what is lost and why,
what needs the damaged models, and the commands that heal (`pack --add --file`: a file stored again heals what is coded
against it; packing a file whose blob is damaged stores it again) or give up (`remove`). The window shows the report
(copyable) and marks what was lost (in memory only: verify writes nothing), and − Clean up removes at a level the user
types exactly: ERASE (lost files), ERASE MOD (models with lost files), ERASE FULL (and the models that need their
data), only while the pack's stamp is as it was verified. No guided repair in the window: the owner wants it simple.
`remove` works in a damaged pack (its preview reads blobs leniently; sweep stays strict).

## Working with the owner

- Discuss approach questions before implementing; fix clear bugs directly. External test reports (often from a
  downstream Linux team) are input to weigh, not orders: verify each issue first.
- When offering options, state a recommendation and why.
- The repository is not public: no compatibility burden for formats or command lines unless asked.
- Commit and push only when asked (the owner often says to hold commits while the window is being polished). Normal
  commits, no squashing unless asked. End commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Before a commit: run both suites, then commit and push:

```bash
python -I tools/test_ans16.py
python -I tools/test_formats.py <scratch_dir>
```

  `test_formats.py` takes several minutes (about 360 checks; on Windows two are skipped: a broken link, and the
  POSIX-only no-hard-link cases). Update both halves of `docs/usage.md` (English and Chinese) with any user-facing
  change, and `tools/ARCHIVE_FORMAT.md` with any format change.
- Windows is tested here; Linux paths (atomic placing via `renameat2`, `SafeTree` dir fds, Tk 9.0 in
  python-build-standalone's Python, the Linux build guide) are verified by the downstream team.
- Write patch scripts with the Write tool, not shell heredocs (backticks and quotes in docs break them).

## Open questions

- Base choice is per file against one model; the format already allows one file's regions to use blobs of several
  models (per-tensor choice), if it ever pays.
- Cross-format deltas (GGUF against safetensors) and partial rows of quantized tensors are not supported.
