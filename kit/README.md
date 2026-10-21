# Offline restore kit

Everything needed to verify and restore the models in this archive **without network access**.
Each sibling directory of `_kit/` (e.g. `Qwen__Qwen2.5-0.5B/`) is one packed model; its `manifest.json` lists
every original file with its SHA-256.

## What you need on the machine

- An x86-64 Windows or Linux (glibc) machine. CPython 3.13 is bundled in `python/` and is installed or unpacked
  automatically when the machine has none (see `python/README.md`).
- Nothing else: no internet, no GPU.

## Restore

Windows:

```
powershell -ExecutionPolicy Bypass -File _kit\install_windows.ps1
<venv>\Scripts\python.exe -I _kit\tools\llm_archive.py restore <archive_root> Qwen/Qwen2.5-0.5B-Instruct <dest_dir>
```

Linux:

```
sh _kit/install_linux.sh
~/overcompressing-venv/bin/python -I _kit/tools/llm_archive.py restore <archive_root> Qwen/Qwen2.5-0.5B-Instruct <dest_dir>
```

If the archive is a single `.oczip` file (made by `llm_archive.py bundle`), extract this `_kit/` folder from it with
any ZIP tool first (7-Zip opens it as it is; for Windows Explorer, rename a copy to `.zip`), then give the `.oczip`
in place of `<archive_root>`: `verify` and `restore` read it without extracting the rest.

Or open a window to look through the archive and extract models or single files: `<venv>\Scripts\pythonw.exe -I
_kit\tools\llm_archive_gui.py` on Windows, `~/overcompressing-venv/bin/python -I _kit/tools/llm_archive_gui.py` on
Linux (the install scripts print both commands).

`restore` writes byte-identical files and checks each against the manifest's SHA-256. Files can depend on other
repos in the archive: a delta (`delta_base` / `delta_files` in the manifest) needs its base model's files, and a file
identical to one archived elsewhere (`"method": "same"`) is restored from that copy. **Do not delete a repo
directory other repos depend on**; `verify` names any missing dependency. All of these need the directories present in the same archive
root. ans16 deltas first restore the base into a temporary file: set `OC_TMP` to a folder with room for one
weight file if the system temp folder is small. `OC_WORKERS` limits the CPU cores used.

## Check

- `llm_archive.py verify <archive_root>` decodes everything in memory and checks every SHA-256.
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
| `tools/llm_archive.py` | the archiver: pack / verify / restore |
| `tools/llm_archive_gui.py` | a small window for it (Tkinter, part of the bundled Python): pack, bundle, verify, extract; opens a folder or an `.oczip` |
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
- 先執行 `install_windows.ps1` 或 `install_linux.sh` 建立環境，再用 `tools/llm_archive.py restore` 還原。
- 檔案可能依賴歸檔裡的其他 repo：delta 需要 base 模型的檔案；和其他地方完全相同的檔案（`"method": "same"`）只存引用，還原時從那份複製。**不要刪除被其他 repo 依賴的 repo 資料夾**，`verify` 會指出缺少的依賴。
- ans16 delta 會先把 base 還原成暫存檔；如果系統暫存資料夾空間不夠，請用 `OC_TMP` 環境變數指定別的資料夾。
- 用 `verify` 檢查損毀；工具包本身可用 `SHA256SUMS` 核對。本工具只負責偵測、不負責修復：請保留多份副本，或自行選用修復方案（比較見 `docs/protection.md`）。
- 就算工具本身壞了，照著 `tools/ARCHIVE_FORMAT.md` 也能手動還原。
