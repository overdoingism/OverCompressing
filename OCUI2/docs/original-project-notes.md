# Notes on the original project (V:\OverCompressing)

**Update 2026-10-22, later the same day:** upstream commits fcb475d, cb6ec3e and 88f7f13 addressed items 1, 4 and 5
below (`archive/` is now described as format-3 leftovers, `list --json` carries `blobs` per model, `error` events
carry a `code`) and retired the offline kit in favour of a portable copy. This window uses both new fields since
GUI 0.2.1 and falls back to reading manifests with an older tool. The items are kept here as written.

Found while porting the window to the Godot engine (2026-10-22). Nothing here was changed there; these are
observations and suggestions for its owner. 對原專案的觀察與建議（未改動任何原始碼）。

## Stale or inconsistent

1. **`archive/` is not a format-4 pack.** `AGENTS.md` calls `archive/` "local test pack folder with real Qwen models",
   but it holds the earlier layout (one folder per model with `manifest.json` and `*.ocz` beside it, no `ocpack.json`,
   no `models/`, no `blobs/`). The tool refuses it, as the documentation says it should. The only `.oczip` in it,
   `archive/un_arch/mradermacher/Qwen2.5-0.5B-GGUF.oczip`, is also not a pack (`cannot open …: not a pack (no
   ocpack.json in it)`). So there is no format-4 test pack in the repository's data; one has to be built from
   `models/` first. `archive/_kit/` (the built kit) is fine.
2. **Cross-format deltas work better than the docs say.** `docs/scope.md` and the README's scope table list "GGUF ↔
   safetensors cross-format deltas (not yet)", yet packing `mradermacher/Qwen2.5-0.5B-Instruct-GGUF` after
   `saidutta69/Qwen2.5-0.5B-Instruct-heretic` (safetensors) stored both the BF16 and the F16 GGUF as `gguf+ans16`
   against the safetensors model, "99% matched", 5.3 % of their size. Either the scope text is out of date (the
   benchmarks table already shows "F16 GGUF against a BF16 base · 44 %"), or it means something narrower (quantized
   rows). Worth one sentence either way.
3. A comment in `llm_archive_gui.py` sits on the wrong line: `BELOW = "\0below"  # a drag begun under the last row of
   the list  # the place is looked at (one file's time stamp) at most this often` — the second half belongs to
   `WATCH_SECONDS` above it.

## Suggestions for the `--json` interface (what an external window needs)

4. **`list --json` loses the per-file data of each model.** `pack_summary` replaces each model's `files` list by its
   count, so a program reading the pack listing cannot tell which blobs a model lists, hence cannot sum a selection of
   models counting a shared blob once (what the Tkinter window does in-process with `blob_span`). This window reads
   `models/*.json` itself for that. A `blobs` field per model (its SHA-256 list) or a `--files` flag on `list` would
   let any client do it from the tool's own output and keep the format private.
5. **Errors carry no code.** The `error` event has only `text`; the Tkinter window decides to offer `--overwrite` by
   searching the text for "with other contents", and this window had to do the same. A `code` field (`exists`,
   `locked`, `not_a_pack`, `refused`, …) on `error` events would make such decisions robust, also for agents.
6. `remove --dry-run --json` is the right preview for a client, but it still needs the pack's lock (shared) and a
   Python start-up per preview (~0.4 s here). Fine for a window; a `--json` `list` that already carried `freed`/`kept`
   per model would spare it. Low priority.
7. `pack --json` reports the `hint` (models that may store smaller) only after the whole run; a client that wants to
   offer "select them and rebase" has to keep the event list. Fine as is; noting that the Tkinter window and this one
   both rely on that one event.

## Small things in the Tkinter window noticed in passing

8. `ask_pack`: with files selected, the default new pack file lands *inside* the folder being packed
   (`new_file = os.path.join(source if files else …)`). The tool allows it (only the named files are read), but a user
   who later packs the whole folder will find the `.oczip` among the model files. Defaulting to the parent folder in
   both cases would be safer.
9. `windows_trash` reads `NukeOnDelete` / `MaxCapacity` from `HKCU\…\BitBucket\Volume\<guid>`; on a volume the user
   has never deleted from, the key does not exist, so capacity is unknown and the size check is skipped (by design,
   "capacity = None"). The behaviour is right; the docstring could say so.
10. `natural()` measures at most 50 of the longest texts by character count, so a column of many short-but-wide
    (CJK) names can still come out a little narrow. Rare; a tooltip shows the whole text anyway.

## What the port confirmed works as documented

- `--json` output: `pack`, `model`, `fail`, `freed`, `kept`, `summary`, `hint`, `damage`, `progress` (stderr) and
  `error` events behaved exactly as `docs/usage.md` describes, including `--no-progress` and the exit codes.
- The stdin-EOF cancel runner (`RUNNER`) works from a .NET host too: closing the child's stdin ends a running verify
  with `cancelled` and exit code 1 within a second; `taskkill /T` for "Stop now".
- Below-normal priority set on the child right after start is inherited by its ans16 worker processes.

## Found while following the 0.6 / format 5 handoff (2026-11-01)

11. **The exact rebuild looks like a stall.** When a `quant` or `rank1` region does not rebuild the fast way, the
    tool works it out again without floating point arithmetic (`quant_region`, `rank1_region`): on a 482 MiB Q6_K
    file that took minutes per 64 MiB region here, during which `--progress` stays where it was (progress is counted
    as files are written) and the only word is one `exact` line per region, 100+ characters long, whose "(slow)"
    comes last and is cut by both windows (90 characters). The owner, watching, took it for a hang ("1 % and no
    progress"). This window now shows a notice of its own on the busy card when an `exact` event arrives. For the
    tool: a `progress` event per region of an exact rebuild (`step: "rebuilding region 3 of 8 exactly"`, or `done`
    advanced by the region's length), or the words "slow, exact rebuild" first in the `exact` text, would let any
    client say what is happening. Low cost, and the one place where the tool is silent for minutes.
    **Done the same day at the owner's word** (the one edit outside `OCUI2/`): the line is now `slow: exact rebuild of
    <file>, region at <offset>`, and the Tkinter window's busy window got the same note as this one (`exact_slow`).
12. **`list` names a folder's model after the folder as it is.** `pack <Org__Name folder>` without `--name` makes the
    model `Org__Name`, not `Org/Name` (`name = args.name or os.path.basename(source)`; the help says "the folder's
    name"). Consistent with the documentation; noted because the test packs here were first built with `--name` and
    the difference showed in the window's lists. Not a bug.
