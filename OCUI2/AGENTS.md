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

Not in the repository: `engine/` (downloaded), `dist/` (built), `.testpacks/` (local format-4 packs for testing),
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
`--dialog=pack|extract|export|rebase|rename|remove|verify|settings|busy|damage`, `--job=cmd|arg|arg`, `--cancel=<s>`,
`--menu`, `--trash=<path>`, `--shot2=<png> --wait2=<s>`, `--scale=<f>`, `--go=<place>`, `--close`. Do that after every
change, at 100 % and at a high display scale; drive dialogs through their buttons, not through keys.

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
  `busy`, `no_room`, `damaged`, `cancelled`, `other`) is what the window acts on, never the text.
- Every change runs `python -I -X utf8 -u -c <runner> <tools dir> <command...> --json --progress` in a child process
  at below-normal priority (`CliProcess`); the runner is the Tkinter window's `RUNNER`, verbatim: closing the child's
  stdin cancels as Ctrl+C would, "Stop now" is `taskkill /T /F`. Output is read on threads and drained each frame.
- Where the tool is (`ToolLocator`): the settings (`%APPDATA%\Godot\app_userdata\OverCompressing\settings.json`),
  else `OVERCOMPRESSING_HOME`, else the exe's folder and three parents (`tools\llm_archive.py`, `_kit\tools`, an
  `OverCompressing` folder beside), else `V:\OverCompressing`; the Python is a portable copy's own, the checkout's
  `.venv`, python.org's, or `python` on PATH. ⚙ Settings shows and checks the choice.

## Rules kept from the original window

Text is Unicode symbols where they say it (BMP only), else English; no localization. Dialogs: a bold first line, gray
notes, a red line kept for errors, a path on a line of its own shortened from the middle, lists at most 5 names and
"and N more", facts first and the question last, no "…" on buttons. One rule for button states: Refresh, Pack in and
Add file always usable where the place can be changed; every other button acts on the selection and is off while
nothing is; nothing selected never means everything. The list watches one time stamp every 5 s and on mouse activity
after that long, never while a job runs or a dialog is open; F5 lists again at once. − Remove in a folder goes to the
Recycle Bin, never deletes for good. Verify's damage marks live in memory only and only while the pack's stamp is as
it was verified; cleaning up is typed (`ERASE`, `ERASE MOD`, `ERASE FULL`).

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
