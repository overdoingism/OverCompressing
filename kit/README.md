# Offline restore kit

> **Retired.** This is the README of the offline kit that `kit/build_kit.py` made, which is no longer made: the build
> guides (`docs/build-*.md` in the repository) make a portable copy instead. A `_kit/` built with it still works as
> described below.

Everything needed to verify and restore the models in this pack **without network access**.
The pack folder holding `_kit/` holds `ocpack.json`, one manifest per model in `models/` (e.g.
`models/Qwen__Qwen2.5-0.5B.json`, listing every original file with its SHA-256) and the stored files in `blobs/`,
each named by the SHA-256 of its original file.

## What you need on the machine

- An x86-64 Windows or Linux (glibc) machine. CPython 3.13 is bundled in `python/` and is installed or unpacked
  automatically when the machine has none (see `python/README.md`).
- Nothing else: no internet, no GPU.

## Restore

Windows:

```
powershell -ExecutionPolicy Bypass -File _kit\install_windows.ps1
<venv>\Scripts\python.exe -I _kit\tools\llm_archive.py list <pack_folder>
<venv>\Scripts\python.exe -I _kit\tools\llm_archive.py restore <pack_folder> Qwen/Qwen2.5-0.5B-Instruct <dest_dir>
```

Linux:

```
sh _kit/install_linux.sh
~/overcompressing-venv/bin/python -I _kit/tools/llm_archive.py list <pack_folder>
~/overcompressing-venv/bin/python -I _kit/tools/llm_archive.py restore <pack_folder> Qwen/Qwen2.5-0.5B-Instruct <dest_dir>
```

If the pack is a pack file (`.oczip`, made by `llm_archive.py export`), extract this `_kit/` folder from it with
any ZIP tool first (7-Zip opens it as it is; for Windows Explorer, rename a copy to `.zip`), then give the pack file
in place of `<pack_folder>`: `list`, `verify` and `restore` read it without extracting the rest.

Or open a window to look through the pack and extract models or single files (**⤒ Extract to**):
`<venv>\Scripts\pythonw.exe -I
_kit\tools\llm_archive_gui.py` on Windows, `~/overcompressing-venv/bin/python -I _kit/tools/llm_archive_gui.py` on
Linux (the install scripts print both commands).

`restore` writes byte-identical files and checks each against the manifest's SHA-256. A stored file may be a delta
against other stored files (its header names them by SHA-256): they are restored first, from the same pack. **Change
the pack only through the tool** (and copy it whole): a stored file deleted by hand breaks every model that needs it;
`verify` names what is missing. ans16 deltas first restore the base into a temporary file: set `OC_TMP` to a folder
with room for one weight file if the system temp folder is small. `OC_WORKERS` limits the CPU cores used.

## Check

- `llm_archive.py verify <pack_folder>` decodes everything in memory and checks every SHA-256.
- The kit itself: `SHA256SUMS` lists every kit file (`sha256sum -c SHA256SUMS` inside `_kit/`).
- Damaged files are detected, not repaired: keep other copies, or add repair data with a tool of your choice
  (`docs/protection.md` compares the options).

## Contents

| path | what |
|---|---|
| `requirements.txt` | exact pinned versions installed by the scripts |
| `python/windows/` | python.org CPython 3.13.16 installer (Authenticode + OpenPGP verified) |
| `python/linux/` | python-build-standalone CPython 3.13.16 (SHA256SUMS + GitHub attestation verified) |
| `wheels/win_amd64/` | Windows wheels (numpy, zstandard) |
| `wheels/linux_x86_64/` | Linux wheels (numpy, zstandard) |
| `tools/llm_archive.py` | the archiver: pack / list / verify / restore / remove / rename / rebase / export |
| `tools/llm_archive_gui.py` | a small window for it (Tkinter, part of the bundled Python): the models of a pack as a list (or by base), with a filter; pack, add files, save .oczip, extract, verify, remove, rename |
| `tools/BUILD` | which build of the two tools this is (the date and commit each was last changed in); `llm_archive.py --version` and the window's title show it |
| `tools/ARCHIVE_FORMAT.md` | on-disk format; enough to restore by hand if the tool itself is unusable |
| `tools/test_ans16.py` | self-test of the ans16 delta decoder: `python -I tools/test_ans16.py` (prints ALL PASSED) |
| `tools/test_formats.py` | self-test of the safetensors/GGUF/other-file containers: `python -I tools/test_formats.py <scratch_dir>` |
| `tools/compare_tensors.py` | how a fine-tune lines up with a base: `python -I tools/compare_tensors.py <base_file> <target_file>` |
| `docs/` | how it works, scope, benchmarks, usage and build guides (usage and build pages in English and Chinese) |
| `LICENSE`, `THIRD_PARTY_NOTICES.md` | OverCompressing's MIT license and the licenses of everything bundled here |

Rebuild this kit from the source repository with `kit/build_kit.py` (https://github.com/overdoingism/OverCompressing).

## Provenance (checked 2026-10-20)

The numpy and zstandard wheels were scanned with GuardDog 3.2.0 (no malicious findings), their installed files
matched their RECORD hashes, and Windows Defender found no threats.

---

## 中文摘要

這個資料夾是離線還原工具包，在完全沒有網路的情況下，也能驗證、還原這份歸檔裡的模型。

- 工具包已內附 CPython 3.13.16：Windows 用 python.org 官方安裝檔，Linux 用 python-build-standalone。電腦上沒有 Python 3.13 時，安裝腳本會自動安裝或解開它。
- 先執行 `install_windows.ps1` 或 `install_linux.sh` 建立環境，再用 `tools/llm_archive.py list` 看有哪些模型、用 `restore <pack> <模型名稱> <目的資料夾>` 還原。
- pack 資料夾裡有 `ocpack.json`、`models/`（每個模型一份 manifest）與 `blobs/`（儲存檔，以原始檔的 SHA-256 命名，相同的檔案只存一份）。儲存檔可能是以其他儲存檔為基底的差異檔，還原時會先還原它們。**只透過工具修改 pack**（要複製就整個資料夾一起）：用手刪掉儲存檔，會讓需要它的模型壞掉，`verify` 會指出缺少什麼。
- ans16 delta 會先把 base 還原成暫存檔；如果系統暫存資料夾空間不夠，請用 `OC_TMP` 環境變數指定別的資料夾。
- 用 `verify` 檢查損毀；工具包本身可用 `SHA256SUMS` 核對。本工具只負責偵測、不負責修復：請保留多份副本，或自行選用修復方案（比較見 `docs/protection.md`）。
- 就算工具本身壞了，照著 `tools/ARCHIVE_FORMAT.md` 也能手動還原。
