# AGENTS.md

Notes for coding agents (and people) working on OverCompressing: how to build and test it, the rules the code keeps,
and the decisions not to undo. What the tool does and how to use it: [README](README.md),
[docs/usage.md](docs/usage.md).

## The project

A lossless archiver for open-weight model files (safetensors, GGUF, anything else in a model folder): every file is
restored bit for bit and checked against its SHA-256. Float tensors are coded as byte planes; quantized tensors,
metadata and text go to zstd; a fine-tune is stored as a delta against its base (`ans16`, a context-modelled rANS
coder in NumPy, or XOR). Python 3.13 with NumPy and zstandard only: no compiled code of its own, no PyTorch.

| path | what |
|---|---|
| `tools/llm_archive.py` | the whole tool: CLI (`pack`, `list`, `verify`, `restore`, `remove`, `rename`, `rebase`, `export`), codecs, format, safety |
| `tools/llm_archive_gui.py` | the Tkinter window; reads in-process, runs the CLI in a child process for every change |
| `tools/ARCHIVE_FORMAT.md` | the on-disk format, byte by byte (enough to restore by hand) |
| `tools/test_ans16.py`, `tools/test_formats.py` | the test suites |
| `requirements.txt`, `requirements-*.lock` | numpy and zstandard; pinned and hash-checked per platform for the portable copy |
| `docs/` | usage (English, then Chinese), principles, scope, benchmarks, build guides, protection |
| `dev/` | experiments and measurement scripts |

Terms: a **pack** holds models, as a **pack folder** (`ocpack.json`, `models/`, `blobs/`; for upkeep) or a **pack
file** (`.oczip`, the same tree in one ZIP; written once, read in place). A **blob** is one stored file, named by the
SHA-256 of the original and shared by every model that has it; a **manifest** is a model's list of files.

## Build and test

- Set up with a build guide ([Windows](docs/build-windows.md), [Linux](docs/build-linux.md)): it makes a portable copy
  with pinned, hash-checked downloads. Any Python 3.13 with `pip install -r requirements.txt` works too.
- Run everything with `python -I` (isolated mode).
- Both suites pass before every commit:

```bash
python -I tools/test_ans16.py
python -I tools/test_formats.py <scratch_dir>   # several minutes, about 360 checks
```

- Speed changes are measured on real models before and after, per step, not only in total: `docs/benchmarks.md`.
- A script that drives `llm_archive` in-process and starts its worker processes needs `if __name__ == "__main__"`
  (spawn on Windows, a fork server elsewhere import it again).

## Rules the code keeps

- Every written file goes to a fresh `.oc-*.part`, is synced, then put in place; without `--overwrite`, placing is
  atomic no-clobber (a hard link, else `renameat2`/`renamex_np`, else a refusal; never check-then-rename). A blob is
  decoded back to its SHA-256 before it takes its name; `export --verify` checks a pack file before placing it.
- Order: blobs first, the manifest replaced in one step last, the sweep after: a pack is complete at every moment.
- Links below a pack folder or a restore destination are never followed. Restore never writes into a pack folder;
  pack never reads from one.
- One lock per pack folder: changes exclusive, reads shared, never waits.
- Packs, manifests, blobs and the ZIP structure of pack files are untrusted input, checked before anything is decoded.
  Bad input is a refusal with a clear message, never a traceback; a JSON value is checked for its type before it is
  used as a key.
- Damage is detected (SHA-256 per blob and per file, CRC per region), not repaired: the format carries no repair data.

## Contracts

- Format version 4. Any change to it goes into `tools/ARCHIVE_FORMAT.md`.
- `--json`: every stdout line is a JSON object; a failure ends with `{"event": "error", "code", "text"}` and exit code 1.
  The codes (`exists`, `name_taken`, `busy`, `no_room`, `damaged`, `cancelled`, `other`) are the contract, the text is
  for people; programs, the window included, decide by the code. A command line that does not parse exits 2.
- A user-facing change goes into both halves of `docs/usage.md` (English and Chinese).
- Versions are pinned (python-build-standalone in the build guides, numpy and zstandard in the lock files) and bumped
  on purpose: new hashes, the Windows guide followed as written, both suites run. Bump soon when a security fix touches
  what the tool exposes to outside data (zlib, libzstd, `zipfile`, `json`, `hashlib`, numpy, Tk); skip releases
  younger than two weeks.

## Decisions not to undo

- Bases are chosen per file, never a blob that needs the file (no cycles), chains at most 32 deep. A delta that reads
  its bases for under 1% of the file is stored without them, so no model needs another for nothing.
- What needs what is worked out from manifests and blob headers when needed, never stored (no index, no database).
- Blobs a removed model leaves to others are trimmed to what those read (partial blobs).
- No offline kit: the build guides make a portable copy. `_kit/` in a pack folder is handled passively (export
  carries it, restore never writes into it); the kit's scripts are not to come back.
- List files are one name per line: no `@FILE` arguments, no NUL-separated lists.
- No repair schemes and no guided repair in the window; cleaning up after damage is typed (`ERASE`, `ERASE MOD`,
  `ERASE FULL`).
- An archive of format 3 is not a pack: no migration tool.
- On POSIX, ans16 workers are never forked from the tool's own process (a fork server), and the window's runner reads
  stdin with `os.read`: both keep a lock held by another thread from being inherited by a worker, which then hangs.

## The window

- Text: Unicode symbols (BMP only, for Tk 8.6) where they say it, else English; no localization. Dialogs short, in
  sentence case; no "…" on buttons.
- Reading (a pack, a model) is done in-process; every change runs the CLI in a child process with `--json
  --progress`, at below-normal priority; closing its stdin cancels it.
- Only ↻ Refresh, + Pack in and + Add file act with nothing selected; nothing selected never means "everything".
- In tests, drive dialogs by invoking their buttons rather than sending keys (keys need the system focus).
