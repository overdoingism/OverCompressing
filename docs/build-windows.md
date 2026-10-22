# Build on Windows

**English** · [中文](#在-windows-上建置)

This makes a **portable copy** of OverCompressing: one folder holding its own Python (with numpy and zstandard) and
the tools. Nothing is installed on the system: copy the folder anywhere (another PC, a USB drive, beside your packs)
and it runs there, without network. Every step is a command with a pinned version or SHA-256 to check, so that an
agent can carry it out and verify each step.

Tested on Windows 10 22H2, in Windows PowerShell 5.1.

## 1. Prerequisites

| item | why |
|---|---|
| Windows 10 (1803 or later) / 11, x64 | `curl.exe` and `tar.exe` come with it |
| [Git](https://git-scm.com/) | get the code, and the build stamp of the tools |
| internet access, once | the Python and two wheels are downloaded |

No Python on the system and no compiler: the copy brings its own Python, and numpy and zstandard come as prebuilt
wheels.

## 2. Get the code

```powershell
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. Download Python and check it

The Python is [python-build-standalone](https://github.com/astral-sh/python-build-standalone): CPython built to run
from any folder, with tkinter (for the window) and pip. (python.org's installer installs into the system; its
embeddable package has neither tkinter nor pip.)

```powershell
$py = "cpython-3.13.16+20261001-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
curl.exe -fLo $py "https://github.com/astral-sh/python-build-standalone/releases/download/20261001/$($py -replace '\+', '%2B')"
if ((Get-FileHash $py -Algorithm SHA256).Hash -ne "C402EB9A35ABA90DE319BBC87D23E6D887ECA1551A05CFA80775F92DCF3EBC08") { throw "wrong SHA-256: do not use $py" }
```

## 4. Make the portable folder

```powershell
$out = "$HOME\OverCompressing"          # a new folder, anywhere
New-Item -ItemType Directory $out | Out-Null
tar -xzf $py -C $out                     # its Python, in $out\python
& "$out\python\python.exe" -I -m pip install --require-hashes --only-binary :all: --no-warn-script-location --disable-pip-version-check -r kit\requirements-win_amd64.lock
New-Item -ItemType Directory "$out\tools" | Out-Null
Copy-Item tools\llm_archive.py, tools\llm_archive_gui.py, tools\ARCHIVE_FORMAT.md, tools\compare_tensors.py, tools\test_ans16.py, tools\test_formats.py "$out\tools"
Copy-Item -Recurse docs "$out\docs"
Copy-Item LICENSE, THIRD_PARTY_NOTICES.md $out
& "$out\python\python.exe" -I -c "import json, sys; sys.path.insert(0, 'tools'); import llm_archive as la; json.dump({n: la.build_of('tools/' + n) for n in ('llm_archive.py', 'llm_archive_gui.py')}, open(sys.argv[1], 'w'), indent=1)" "$out\tools\BUILD"
Set-Content "$out\OverCompressing.cmd" '@start "" "%~dp0python\pythonw.exe" -I "%~dp0tools\llm_archive_gui.py" %*' -Encoding ascii
Set-Content "$out\llm_archive.cmd" '@"%~dp0python\python.exe" -I "%~dp0tools\llm_archive.py" %*' -Encoding ascii
```

pip installs numpy and zstandard into the portable Python itself (`$out\python\Lib\site-packages`), each wheel
checked against the SHA-256 in the lock file. `tools\BUILD` records which commit the tools are, for the window's
title and `--version` (a copy has no git to ask). To build without network, put the two wheels in a folder and add
`--no-index --find-links <that folder>` to the pip line.

## 5. Check it

```powershell
& "$out\llm_archive.cmd" --version
& "$out\python\python.exe" -I "$out\tools\test_ans16.py"
& "$out\python\python.exe" -I "$out\tools\test_formats.py" "$env:TEMP\oc-test"
```

The first prints `OverCompressing CLI 0.5 (<date> <commit>), format 4`; both test suites end with `ALL PASSED` (the
second takes several minutes).

## 6. Use it

| file | what |
|---|---|
| `OverCompressing.cmd` | the window (double-click it, or give it a folder, a pack folder or a pack file) |
| `llm_archive.cmd <command> ...` | the command line: [usage](usage.md) |

The folder needs nothing outside it (restored bases go to the system's temp folder, or to `OC_TMP`): move or copy it
as a whole.

**To have a pack carry it**, copy it into the pack folder as `_kit`:

```powershell
Copy-Item -Recurse $out <pack_folder>\_kit
```

`export` then puts it into the pack file too, and `_kit\OverCompressing.cmd` opens that pack. Keep it with every copy
of the pack ([protection](protection.md)). From a pack file, extract `_kit/` with any ZIP tool (7-Zip opens it as it
is; for Windows Explorer, rename a copy to `.zip`) and run it from there.

**To work on the code**, run the checkout's own files with this Python
(`& "$out\python\python.exe" -I tools\llm_archive.py ...`), and make a new copy for a new version.

Next: [usage](usage.md).

---

# 在 Windows 上建置

[English](#build-on-windows) · **中文**

本頁做出 OverCompressing 的**可攜版**：一個資料夾，裡面有它自己的 Python（含 numpy 與 zstandard）和工具。不在系統上安裝
任何東西：把整個資料夾複製到任何地方（另一台電腦、隨身碟、pack 旁邊），不需網路就能在那裡執行。每一步都是附有固定版本或
SHA-256 可核對的指令，交給 AI 代理照做時，每一步都能驗證。

已在 Windows 10 22H2、Windows PowerShell 5.1 上實測。

## 1. 事前準備

| 項目 | 用途 |
|---|---|
| Windows 10（1803 以後）/ 11，x64 | 內建 `curl.exe` 與 `tar.exe` |
| [Git](https://git-scm.com/) | 取得原始碼，以及工具的 build 標記 |
| 網路（只需一次） | 下載 Python 與兩個 wheel |

系統上不需要 Python，也不需要編譯器：可攜版自帶 Python，numpy 與 zstandard 都有現成的 wheel。

## 2. 取得原始碼

```powershell
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. 下載並核對 Python

使用 [python-build-standalone](https://github.com/astral-sh/python-build-standalone)：可以從任何資料夾直接執行的 CPython，
內含 tkinter（圖形介面需要）與 pip。（python.org 的安裝檔會裝進系統；它的 embeddable 版本沒有 tkinter 也沒有 pip。）

```powershell
$py = "cpython-3.13.16+20261001-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
curl.exe -fLo $py "https://github.com/astral-sh/python-build-standalone/releases/download/20261001/$($py -replace '\+', '%2B')"
if ((Get-FileHash $py -Algorithm SHA256).Hash -ne "C402EB9A35ABA90DE319BBC87D23E6D887ECA1551A05CFA80775F92DCF3EBC08") { throw "wrong SHA-256: do not use $py" }
```

## 4. 做出可攜版資料夾

```powershell
$out = "$HOME\OverCompressing"          # 一個新資料夾，放哪裡都可以
New-Item -ItemType Directory $out | Out-Null
tar -xzf $py -C $out                     # 它的 Python，在 $out\python
& "$out\python\python.exe" -I -m pip install --require-hashes --only-binary :all: --no-warn-script-location --disable-pip-version-check -r kit\requirements-win_amd64.lock
New-Item -ItemType Directory "$out\tools" | Out-Null
Copy-Item tools\llm_archive.py, tools\llm_archive_gui.py, tools\ARCHIVE_FORMAT.md, tools\compare_tensors.py, tools\test_ans16.py, tools\test_formats.py "$out\tools"
Copy-Item -Recurse docs "$out\docs"
Copy-Item LICENSE, THIRD_PARTY_NOTICES.md $out
& "$out\python\python.exe" -I -c "import json, sys; sys.path.insert(0, 'tools'); import llm_archive as la; json.dump({n: la.build_of('tools/' + n) for n in ('llm_archive.py', 'llm_archive_gui.py')}, open(sys.argv[1], 'w'), indent=1)" "$out\tools\BUILD"
Set-Content "$out\OverCompressing.cmd" '@start "" "%~dp0python\pythonw.exe" -I "%~dp0tools\llm_archive_gui.py" %*' -Encoding ascii
Set-Content "$out\llm_archive.cmd" '@"%~dp0python\python.exe" -I "%~dp0tools\llm_archive.py" %*' -Encoding ascii
```

pip 會把 numpy 與 zstandard 裝進可攜版自己的 Python（`$out\python\Lib\site-packages`），每個 wheel 都以鎖定檔裡的 SHA-256
核對。`tools\BUILD` 記錄這份工具是哪個 commit，供視窗標題與 `--version` 顯示（複製出去的可攜版沒有 git 可以查）。要在沒有
網路的情況下建置，請把兩個 wheel 放在一個資料夾裡，並在 pip 那一行加上 `--no-index --find-links <那個資料夾>`。

## 5. 檢查

```powershell
& "$out\llm_archive.cmd" --version
& "$out\python\python.exe" -I "$out\tools\test_ans16.py"
& "$out\python\python.exe" -I "$out\tools\test_formats.py" "$env:TEMP\oc-test"
```

第一行會印出 `OverCompressing CLI 0.5 (<日期> <commit>), format 4`；兩個測試都必須以 `ALL PASSED` 結束（第二個需要幾分鐘）。

## 6. 使用

| 檔案 | 用途 |
|---|---|
| `OverCompressing.cmd` | 圖形介面（雙擊，或給它一個資料夾、pack 資料夾或 pack 檔） |
| `llm_archive.cmd <指令> ...` | 命令列：見[使用方式](usage.md#使用方式) |

這個資料夾不依賴外部任何東西（還原用的 base 會放在系統暫存資料夾，或 `OC_TMP`），可以整個搬移或複製。

**要讓 pack 自帶工具**，把它複製到 pack 資料夾裡，命名為 `_kit`：

```powershell
Copy-Item -Recurse $out <pack_folder>\_kit
```

之後 `export` 也會把它放進 pack 檔，而 `_kit\OverCompressing.cmd` 會直接開啟那個 pack。每一份 pack 副本都要附上它
（見[防護與修復](protection.md#防護與修復建議)）。若是 pack 檔，請用任何 ZIP 工具解出 `_kit/`（7-Zip 可直接開啟；Windows
檔案總管則需把副本改名為 `.zip`），再從解出的資料夾執行。

**要修改程式碼**時，用這份 Python 執行 checkout 裡的檔案（`& "$out\python\python.exe" -I tools\llm_archive.py ...`）；
新版本請重新做一份可攜版。

接著請看[使用方式](usage.md#使用方式)。
