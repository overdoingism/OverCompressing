# Build on Linux

**English** · [中文](#在-linux-上建置)

> **Status:** these steps mirror the Windows build, which was run end to end; on Linux they are checked by the
> downstream team. Please report what you find.

This makes a **portable copy** of OverCompressing: one folder holding its own Python (with numpy and zstandard) and
the tools. Nothing is installed on the system: copy the folder anywhere (another machine, a USB drive, beside your
packs) and it runs there, without network. Every step is a command with a pinned version or SHA-256 to check, so that
an agent can carry it out and verify each step.

## 1. Prerequisites

| item | why |
|---|---|
| Linux x86-64 with glibc 2.17 or later | what python-build-standalone runs on |
| bash, git, curl, tar, sha256sum | get the code and the downloads, and check them |
| for the window: an X11 desktop (or XWayland) | tkinter comes with the Python and draws through the system's X libraries |
| internet access, once | the Python and two wheels are downloaded |

No Python on the system and no compiler: the copy brings its own Python, and numpy and zstandard come as prebuilt
wheels.

## 2. Get the code

```bash
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. Download Python and check it

The Python is [python-build-standalone](https://github.com/astral-sh/python-build-standalone): CPython built to run
from any folder, with tkinter (for the window) and pip.

```bash
py=cpython-3.13.16+20261001-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
curl -fLo "$py" "https://github.com/astral-sh/python-build-standalone/releases/download/20261001/${py/+/%2B}"
echo "ffcb50e716789d1a6e1db5e745d4d194ac8ed9b015ccf5eabcaccb179a25e4a8  $py" | sha256sum -c -
```

## 4. Make the portable folder

```bash
out=~/OverCompressing                    # a new folder, anywhere
mkdir "$out"
tar -xzf "$py" -C "$out"                 # its Python, in $out/python
"$out/python/bin/python3" -I -m pip install --require-hashes --only-binary :all: --no-warn-script-location --disable-pip-version-check -r requirements-linux_x86_64.lock
mkdir "$out/tools"
cp tools/llm_archive.py tools/llm_archive_gui.py tools/ARCHIVE_FORMAT.md tools/compare_tensors.py tools/test_ans16.py tools/test_formats.py "$out/tools/"
cp -r docs LICENSE THIRD_PARTY_NOTICES.md "$out/"
"$out/python/bin/python3" -I -c "import json, sys; sys.path.insert(0, 'tools'); import llm_archive as la; json.dump({n: la.build_of('tools/' + n) for n in ('llm_archive.py', 'llm_archive_gui.py')}, open(sys.argv[1], 'w'), indent=1)" "$out/tools/BUILD"
printf '#!/bin/sh\nexec "$(dirname "$0")/python/bin/python3" -I "$(dirname "$0")/tools/llm_archive_gui.py" "$@"\n' > "$out/overcompressing"
printf '#!/bin/sh\nexec "$(dirname "$0")/python/bin/python3" -I "$(dirname "$0")/tools/llm_archive.py" "$@"\n' > "$out/llm_archive"
chmod +x "$out/overcompressing" "$out/llm_archive"
```

pip installs numpy and zstandard into the portable Python itself, each wheel checked against the SHA-256 in the lock
file. `tools/BUILD` records which commit the tools are, for the window's title and `--version` (a copy has no git to
ask). To build without network, put the two wheels in a folder and add `--no-index --find-links <that folder>` to the
pip line.

## 5. Check it

```bash
"$out/llm_archive" --version
"$out/python/bin/python3" -I "$out/tools/test_ans16.py"
"$out/python/bin/python3" -I "$out/tools/test_formats.py" /tmp/oc-test
```

The first prints `OverCompressing CLI 0.5 (<date> <commit>), format 4`; both test suites end with `ALL PASSED` (the
second takes several minutes; on Linux it also covers FIFOs and broken links in a source folder, which Windows cannot
create).

## 6. Use it

| file | what |
|---|---|
| `overcompressing` | the window (give it a folder, a pack folder or a pack file, or nothing) |
| `llm_archive <command> ...` | the command line: [usage](usage.md) |

The folder needs nothing outside it (restored bases go to the system's temp folder, or to `OC_TMP`): move or copy it
as a whole.

**To have a pack carry it**, copy it into the pack folder as `_kit`. `export` refuses links inside a pack folder, and
this Python has a few (`bin/python3` and others), so replace them by copies first:

```bash
find "$out" -type l | while read -r link; do target=$(readlink -f "$link"); rm "$link"; cp -r "$target" "$link"; done
cp -r "$out" <pack_folder>/_kit
```

`export` then puts it into the pack file too, and `_kit/overcompressing` opens that pack. Keep it with every copy of
the pack ([protection](protection.md)). From a pack file, extract `_kit/` with any ZIP tool; `unzip` keeps the execute
bits, others may not (then: `chmod +x _kit/overcompressing _kit/llm_archive _kit/python/bin/*`).

**To work on the code**, run the checkout's own files with this Python
(`"$out/python/bin/python3" -I tools/llm_archive.py ...`), and make a new copy for a new version.

Next: [usage](usage.md).

---

# 在 Linux 上建置

[English](#build-on-linux) · **中文**

> **狀態：** 以下步驟比照已完整實測的 Windows 流程；Linux 上由下游團隊驗證。歡迎回報結果。

本頁做出 OverCompressing 的**可攜版**：一個資料夾，裡面有它自己的 Python（含 numpy 與 zstandard）和工具。不在系統上安裝
任何東西：把整個資料夾複製到任何地方（另一台機器、隨身碟、pack 旁邊），不需網路就能在那裡執行。每一步都是附有固定版本或
SHA-256 可核對的指令，交給 AI 代理照做時，每一步都能驗證。

## 1. 事前準備

| 項目 | 用途 |
|---|---|
| Linux x86-64，glibc 2.17 以後 | python-build-standalone 的執行條件 |
| bash、git、curl、tar、sha256sum | 取得原始碼與下載檔，並加以核對 |
| 使用圖形介面時：X11 桌面（或 XWayland） | tkinter 隨 Python 附帶，透過系統的 X 函式庫繪圖 |
| 網路（只需一次） | 下載 Python 與兩個 wheel |

系統上不需要 Python，也不需要編譯器：可攜版自帶 Python，numpy 與 zstandard 都有現成的 wheel。

## 2. 取得原始碼

```bash
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. 下載並核對 Python

使用 [python-build-standalone](https://github.com/astral-sh/python-build-standalone)：可以從任何資料夾直接執行的 CPython，
內含 tkinter（圖形介面需要）與 pip。

```bash
py=cpython-3.13.16+20261001-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
curl -fLo "$py" "https://github.com/astral-sh/python-build-standalone/releases/download/20261001/${py/+/%2B}"
echo "ffcb50e716789d1a6e1db5e745d4d194ac8ed9b015ccf5eabcaccb179a25e4a8  $py" | sha256sum -c -
```

## 4. 做出可攜版資料夾

```bash
out=~/OverCompressing                    # 一個新資料夾，放哪裡都可以
mkdir "$out"
tar -xzf "$py" -C "$out"                 # 它的 Python，在 $out/python
"$out/python/bin/python3" -I -m pip install --require-hashes --only-binary :all: --no-warn-script-location --disable-pip-version-check -r requirements-linux_x86_64.lock
mkdir "$out/tools"
cp tools/llm_archive.py tools/llm_archive_gui.py tools/ARCHIVE_FORMAT.md tools/compare_tensors.py tools/test_ans16.py tools/test_formats.py "$out/tools/"
cp -r docs LICENSE THIRD_PARTY_NOTICES.md "$out/"
"$out/python/bin/python3" -I -c "import json, sys; sys.path.insert(0, 'tools'); import llm_archive as la; json.dump({n: la.build_of('tools/' + n) for n in ('llm_archive.py', 'llm_archive_gui.py')}, open(sys.argv[1], 'w'), indent=1)" "$out/tools/BUILD"
printf '#!/bin/sh\nexec "$(dirname "$0")/python/bin/python3" -I "$(dirname "$0")/tools/llm_archive_gui.py" "$@"\n' > "$out/overcompressing"
printf '#!/bin/sh\nexec "$(dirname "$0")/python/bin/python3" -I "$(dirname "$0")/tools/llm_archive.py" "$@"\n' > "$out/llm_archive"
chmod +x "$out/overcompressing" "$out/llm_archive"
```

pip 會把 numpy 與 zstandard 裝進可攜版自己的 Python，每個 wheel 都以鎖定檔裡的 SHA-256 核對。`tools/BUILD` 記錄這份工具是
哪個 commit，供視窗標題與 `--version` 顯示（複製出去的可攜版沒有 git 可以查）。要在沒有網路的情況下建置，請把兩個 wheel
放在一個資料夾裡，並在 pip 那一行加上 `--no-index --find-links <那個資料夾>`。

## 5. 檢查

```bash
"$out/llm_archive" --version
"$out/python/bin/python3" -I "$out/tools/test_ans16.py"
"$out/python/bin/python3" -I "$out/tools/test_formats.py" /tmp/oc-test
```

第一行會印出 `OverCompressing CLI 0.5 (<日期> <commit>), format 4`；兩個測試都必須以 `ALL PASSED` 結束（第二個需要幾分鐘；
在 Linux 上還會額外測試來源資料夾中的 FIFO 與失效連結，Windows 無法建立這兩種檔案）。

## 6. 使用

| 檔案 | 用途 |
|---|---|
| `overcompressing` | 圖形介面（可給它一個資料夾、pack 資料夾或 pack 檔，也可以不給） |
| `llm_archive <指令> ...` | 命令列：見[使用方式](usage.md#使用方式) |

這個資料夾不依賴外部任何東西（還原用的 base 會放在系統暫存資料夾，或 `OC_TMP`），可以整個搬移或複製。

**要讓 pack 自帶工具**，把它複製到 pack 資料夾裡，命名為 `_kit`。`export` 會拒絕 pack 資料夾裡的連結，而這份 Python 有幾個
（`bin/python3` 等），所以先把它們換成實際的檔案：

```bash
find "$out" -type l | while read -r link; do target=$(readlink -f "$link"); rm "$link"; cp -r "$target" "$link"; done
cp -r "$out" <pack_folder>/_kit
```

之後 `export` 也會把它放進 pack 檔，而 `_kit/overcompressing` 會直接開啟那個 pack。每一份 pack 副本都要附上它
（見[防護與修復](protection.md#防護與修復建議)）。若是 pack 檔，請用任何 ZIP 工具解出 `_kit/`；`unzip` 會保留執行權限，
其他工具不一定（這時請執行 `chmod +x _kit/overcompressing _kit/llm_archive _kit/python/bin/*`）。

**要修改程式碼**時，用這份 Python 執行 checkout 裡的檔案（`"$out/python/bin/python3" -I tools/llm_archive.py ...`）；
新版本請重新做一份可攜版。

接著請看[使用方式](usage.md#使用方式)。
