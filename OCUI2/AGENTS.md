# AGENTS.md — OverCompressing UI2

Notes for coding agents (and people) working on the window in this folder: what it is, how it is built and checked,
how it talks to the tool, and the decisions not to undo. What it looks like and how to get it: [README](README.md).
The project it belongs to is the checkout above ([../AGENTS.md](../AGENTS.md)); **nothing outside `OCUI2/` is changed
from here**. What the work here turns up about the tool goes into [docs/original-project-notes.md](docs/original-project-notes.md).

## The project

A second window for OverCompressing, built on **Godot 4.7.2 (.NET / C#, `net8.0`)** instead of a GUI toolkit. It does
what the Tkinter window (`../tools/llm_archive_gui.py`) does, feature for feature and with the same words, and adds
what an engine makes cheap: a live circuit board behind the panels, HDR glow, a graph of what needs what, tweened
dialogs, an intro and an outro, synthesized sounds. All of that is switchable in ⚙ Settings. Target: Windows 10 x64
and up; rendering Forward+ (Vulkan) with fallback to D3D12, then OpenGL 3 (no glow there).

Why Godot: Unity and Unreal need an account and a license activation before the editor runs in batch mode, which an
unattended session cannot do; Godot is MIT, one zip, and drives build, run, export and screenshots from the command
line, so the window could be built and checked without a hand on the mouse.

| path | what |
|---|---|
| `OverCompressingUI/` | the Godot project (`project.godot`, `OverCompressingUI.csproj`, `export_presets.cfg`) |
| `OverCompressingUI/src/Core/` | no engine types: `Text.cs` (every string, ported from the Tkinter `TEXT` dict; `GuiVersion`), `Models.cs`, `Place.cs`, `Listing.cs`, `CliProcess.cs`, `JobRunner.cs`, `PackReader.cs`, `Settings.cs` (settings and where the tool is), `Trash.cs` (Recycle Bin) |
| `OverCompressingUI/src/UI/` | `Ui.cs` (theme, fonts, factories), `MainWindow.cs` (the window), `Dialogs.cs`, `BusyOverlay.cs`, `GraphView.cs` + `GraphPanel.cs`, `Circuit.cs` + `Backdrop.cs` (the board), `Fx.cs` (glow, veil, sweep, sounds), `CursorTrail.cs`, `Icons.cs`, `DevHooks.cs` |
| `OverCompressingUI/assets/` | fonts (Orbitron, Exo 2, Share Tech Mono; OFL, texts beside them), shaders |
| `engine/` | `get_engine.ps1` and `fetch_template.py` fetch the editor and the Windows export templates into it (gitignored, checked against the release's SHA-512 sums) |
| `build.ps1` / `run.ps1` | export `dist\OverCompressingUI.exe` and the release zip / build and run from the project |
| `docs/` | screenshots, findings about the original project, third-party licenses |

Not in the repository: `engine/` (downloaded), `dist/` (built), `.testpacks/` (local format-5 packs for testing),
`.godot/`, `bin/`, `obj/`.

## Build, run, check

```powershell
powershell -ExecutionPolicy Bypass -File engine\get_engine.ps1          # once; needs the .NET SDK 8 or later
powershell -ExecutionPolicy Bypass -File run.ps1 [<folder|pack|.oczip>]
powershell -ExecutionPolicy Bypass -File build.ps1 [-Debug] [-NoZip]    # dist\OverCompressingUI.exe + the release zip
```

Both scripts put the console in UTF-8 (`chcp 65001`): dotnet and Godot print UTF-8, and a GBK/Big5 code page shows
it as garbage. The fonts are imported resources: a first run from the editor binary needs `--import` once.

The window is checked by running it and reading a screenshot. From `OverCompressingUI/`: `dotnet build -c Debug`, then
`engine\godot\...\Godot_v4.7.2-stable_mono_win64_console.exe --path . ++ <place> --shot=<png> --wait=<s>`. Everything
after `++` is the window's (`DevHooks.cs`): a place to start in, then `--view=list|base|graph`, `--select=<n>`,
`--dialog=pack|extract|export|rebase|rename|remove|verify|settings|busy|damage|damage-here|turn|newpack`, `--job=cmd|arg|arg`, `--cancel=<s>`,
`--menu`, `--trash=<path>`, `--shot2=<png> --wait2=<s>`, `--scale=<f>`, `--screen=WxH` (a display pretended that small), `--go=<place>`, `--close`, `--tool=<llm_archive.py>` (a test
copy of the tool, the settings untouched), `--answer=yes,no@shot.png,...` (the next dialogs answer themselves, after a screenshot each), `--script=<file>` (jobs,
answers, cancels and screenshots one per line; the comment at the top of `DevHooks.cs` has the words). Do that after
every change, at 100 % and at a high display scale; drive dialogs through their buttons or `--answer`, not through keys.

There are no unit tests; `Core/` has no engine dependency and could take them.

## Shipping

The repository holds the sources only. The built window is a **GitHub release asset**:
`dist\OverCompressingUI-<version>-win64.zip`, which `build.ps1` writes (the exe and its `data_*` folder; unzip anywhere,
e.g. into `OCUI2\dist\`). It is not committed and not put in Git LFS: the exe alone is over GitHub's 100 MB limit
for a committed file, it only runs with the 200-odd files of the `data_*` folder beside it, and LFS would spend the
free bandwidth on a dozen clones while every rebuild stayed in the history for good. Raise `T.GuiVersion` in
`Text.cs` for anything people see; the zip is named by it.

## How it talks to the tool

- Reading goes through the tool: `list <pack> --json` (one `pack` event, each model with `blobs`: sha256 → stored
  size), `list <pack> MODEL --json`, `remove --dry-run --json`, `--version`. Blobs are never parsed here; with an older
  tool the manifests are read instead. A failed run ends with an `error` event whose `code` (`exists`, `name_taken`,
  `busy`, `no_room`, `damaged`, `inexact`, `cancelled`, `other`) is what the window acts on, never the text: `exists` offers
  a restore again with `--overwrite`, `inexact` (the bytes are intact but a quantized file does not rebuild on this
  machine) with `--accept-inexact`, each flag once; a restore that ends `inexact` with that flag is no failure, and the
  notes it wrote are listed when the batch ends. A failed job's question keeps the jobs after it in the queue.
- An `exact` event (a region worked out the slow, exact way) puts a note on the busy card in the window's words
  (`exact_slow`, with the count so far): the tool's progress stands still for minutes there, and a cut line is not enough.
- Synced with upstream through format 5 (CLI 0.6). The Tkinter window (`../tools/llm_archive_gui.py`) is the
  reference for behaviour and words; `inexact` is `code`, never text.
- The graph's colors (owner, 2026-11-02): a node's fill is a nine-step scale by the nominal bits of the dtype most
  of its weights are in (`Ui.BitsScale`, `Core/Weights.cs`, from the manifests' tensor tables; counted as numbers by
  each tensor's shape, since a small model's 8-bit embedding takes most of a Q4 file's bytes), cold to warm as the
  bits go down and shading the owner's five classes from within: ≥ 16-bit (32 a deeper blue than 16), 6–15-bit
  (8 and up, FP8 included, bluer than 6), 4–5-bit (5 greener than 4), < 4-bit (3 more orange than 2 and 1), other
  gray (no weights, a .bin/.pt without a table, an unknown type). The classes are named by their bits only: the
  words lossless / lossy were the owner's and Claude's while discussing, not for the window (a model trained at that
  precision is not lossy). The class is
  the majority of the weights; the shade is the leading dtype within it (`WeightMix.Bits`). The ring is the
  container: safetensors solid, GGUF a ring of dashes that turns with a brightness running round it, other a faint
  dotted ring (`GraphView.DrawContainerRing`; the owner wanted the kinds told apart by motion, not a dead style). The
  key is the ◧ Colors word's card, which follows the view (`ShowLegend`, `_legendFor`): in the graph a swatch card,
  a bar per class blended between its steps with the bits under them (`Swatch`), and a sample node per container,
  the GGUF one turning (`RingSample`); in a list the row kinds as before. The graph's bottom strip keeps only the
  hint, and the card is no wider than its bars. The Colors word itself takes the hue its frame cycles through
  (owner). A node carries what the model takes on disk, a small arrow down with the ratio beside it, and what it
  takes in the pack (`DrawSizes`; a lone percentage read as dull); the tooltip and the panel name the container and
  the dtype shares (`WeightMix.Shares`), not the class (the color is the hint, the owner said) nor the sizes. GPTQ/AWQ packed weights
  (I32 tensors named `qweight`) count as 4-bit. The manifests are read for every pack listing now.
- Synced with upstream through CLI 0.7 (pack folders made and named; 2026-11-02): `init <folder> [--name]` and
  `name <pack> (--to NAME | --none)` run as jobs; `list --json` carries `name`, `id`, `label` (`PackIdentity`), which the
  window shows and never writes itself. A folder view has **+ Pack to, + Turn folder into pack, + New pack folder**
  (the owner's order), the latter two acting on the place (on wherever Pack to can be used); Turn says why a folder
  cannot become a pack (`Place.NewPackProblem`, the tool's rule: empty but for `_kit`, the lock and what systems leave
  in folders, no pack above) before any dialog, New pack refuses an empty, path-like or taken folder name in the
  dialog. Beside the path, in every place, a field of one width (`NoteWidth`, 28 characters, centered) shows the
  pack's name, or in gray its short id (`unnamed` for a pack without one), editable in a pack folder (and its models),
  read-only in a pack file, empty and off elsewhere; ✎ Rename pack beside it is on while the text is a change
  (`NameChange`; the empty field and the short id are none without a name), Enter or the button runs `name`, Esc puts
  the name back, a name too long for the field shows whole on hover; the window's title ends with ` [label]`. The
  mode chip has one width in every place (`_chipWidth`, the widest word), so the buttons start where they did. The
  pack dialog has "Name of a new pack (optional)" (`--pack-name`, refused in the dialog for a pack folder that is
  there: `pack_name_held`). An empty pack's line reads `where_empty`. `JobRunner.Running` is true from `Start` to the
  batch's end (not only while a child process exists): a button update in the gap before the first process, while a
  failed job's question is up, or between jobs (a selection, the watch, the name field) must turn nothing on (the
  downstream team found that gap on the Tkinter window); a cancel while the question is up ends the batch once it is
  answered. The window's size is kept in canvas units with the scale it was saved at (`WindowScale`; an older file's
  pixels are taken as at this scale) and fitted to the display it opens on (`FitToScreen`); once a second
  (`WatchDisplay`) the display's scale is read again, and a change (another display, or the system's scaling changed
  underneath) redraws everything at the new scale (`Rescale`: the canvas scale, the cursors, every icon re-rendered in
  place by `Icons.Reset`, the board's cells) at the same size to the eye, kept within the display; a display grown
  smaller shrinks the window into it. Dev hooks for all this: `--dialog=turn|newpack`, `--screen=WxH`, and the script
  words `type:`, `name:`, `esc`, `info`, `bg:`, `until-dialog`, `select:`, `pick:`, `fill:`, `accept`, `dismiss`,
  `idle`, `rescale:`.
- Every change runs `python -I -X utf8 -u -c <runner> <tools dir> <command...> --json --progress` in a child process
  at below-normal priority (`CliProcess`); the runner is the Tkinter window's `RUNNER`, verbatim: closing the child's
  stdin cancels as Ctrl+C would, "Stop now" is `taskkill /T /F`. Output is read on threads and drained each frame.
- Where the tool is (`ToolLocator`): the settings (`%APPDATA%\Godot\app_userdata\OverCompressing\settings.json`),
  else `OVERCOMPRESSING_HOME`, else the exe's folder and three parents (`tools\llm_archive.py`, `_kit\tools`, an
  `OverCompressing` folder beside), else `V:\OverCompressing`; the Python is a portable copy's own, the checkout's
  `.venv`, python.org's, or `python` on PATH. ⚙ Settings shows and checks the choice.

## Rules kept from the original window

- Dialogs are wide and flat, not tall (owner, 2026-11-02: at 175 % on a 1080p display the pack dialog ran off the
  window, and once it fitted, its glow was cut at the edges): the pack dialog is two columns (`AskPack`: the model and
  where it goes at the left, the new pack's name and the options at the right), a label and its field share a row
  where that reads well (`Ui.Labelled`), notes are a size down (13 px, `Ui.NoteFontSize`; 12 read as strain), a touch heavier (`Ui.NoteFont`, weight 560:
  the fonts are variable, so any weight goes, not only regular and bold) in `Ui.TextNote` (#cad5ea, a shade under the
  text: the owner found `TextDim` and then #b4c3de too dark there) and wrapped to their column
  (`Ui.Note(text, width)`), and a text field shows its edge without the focus (the theme's LineEdit `normal`: an empty
  field on the dark card was invisible until clicked),
  gaps are 2–6 px, and the damage report's text box asks for 240 px, not 380. As a safety net every `OcDialog` fits
  itself to the window (`Fit`): one that would be taller goes into a scroll box sized to what is left, so OK and
  Cancel stay in reach; and it is kept within the window with its glow whole, below the title bar and the frame's
  shadow all round (`Keep`, `Glow`). Every dialog looked soft, "as if through a filter" (owner, 2026-11-02): an embedded window is a see-through viewport,
  and glyphs drawn onto nothing get their antialiased edges blended twice (with transparent black, then over the frame),
  a dark halo round every letter. `OcDialog` now draws an opaque ground inside (`PanelSolid`, rounded at the bottom as
  the frame is), so text blends once; the viewport stays transparent so the frame's corners and glow show. Measured on
  the same label: edge contrast 30 → 34, the halo gone. Not the cause, tried and dropped: font oversampling (already at
  the display's scale; off made it worse), `Normal` hinting (uneven spacing), whole-pixel positioning, LCD antialiasing,
  a dark rim. Check dialogs with `--scale=1.75 --screen=1920x1040` (`--dialog=packin` opens the
  pack dialog in its + Pack in form without the native folder picker).

Text is Unicode symbols where they say it (BMP only), else English; no localization. Dialogs: a bold first line, gray
notes, a red line kept for errors, a path on a line of its own shortened from the middle, lists at most 5 names and
"and N more", facts first and the question last, no "…" on buttons. One rule for button states: Refresh, Pack in,
Add file, Turn folder into pack and New pack folder act on the place, so they are always usable where the place can be
changed; every other button acts on the selection and is off while
nothing is; nothing selected never means everything. The list watches one time stamp every 5 s and on mouse activity
after that long, never while a job runs or a dialog is open; F5 lists again at once. − Remove in a folder goes to the
Recycle Bin, never deletes for good. Verify's damage marks live in memory only and only while the pack's stamp is as
it was verified: a verify that ran through clears the marks of what it checked (its own argv: models, `--file=`) and
marks what it found lost; a cancel or a failure without a report changes nothing; marks are of one pack at a time.
What is lost only through blobs that are intact but do not rebuild here is marked "not rebuilt here", the report gets
that heading, and − Clean up is off while any such blob is in it (it may restore on another machine). Cleaning up is
typed (`ERASE`, `ERASE MOD`, `ERASE FULL`). + Pack in and ⇄ Rebase have a tick for coding quantized weights against
the weights they came from, on every time (off: `--no-quant`). A typed path that exists is that path, whatever is in
its name; only otherwise is `›` read as "<pack> › <model>".

## The look: decisions not to undo

- Theme "cyber + neon". Only edges, lines and marks are HDR; text stays at or below 1.0 and plain (the list's has a
  dark rim, nothing else); panels are 56 % opaque so the board shows through.
- **Nothing that moves may show its pieces**: glows are one radial falloff texture, a signal's tail is one polyline
  with its alpha fading along it, the cursor's wake fills the gaps between samples. No particles (`GpuParticles2D`
  barely advanced under low-processor mode).
- The board: nodes on a grid, bends of 45° only (a 90° turn is two bends with a mitered corner), buses of 2–3 lines,
  no wedges at a pad, no diagonals crossing through a corner; signals leave the nodes at a steady rate that **never
  follows the job state**; a pad breathes warm white when a signal arrives (lasers and bright blobs were too loud).
- The graph flows from a base to the models it feeds (bytes flow downstream); lit edges are warm beige; the layout is
  settled once and holds, a dragged node stays; the model's name runs along its ring; the side panel has the details.
- Nothing may be "too loud": every effect was toned down at least once on request. The intro and outro are short
  (1.25 s, 0.45 s), any key ends the intro, the outro never plays while a job runs. Everything is off with effects off.
- The view toggles are orange and sit right of the filter; the color key is one word, "◧ Colors", that opens on
  hover (a row of chips there looked like buttons).
