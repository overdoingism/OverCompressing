# Build on Windows

**English** · [中文](#在-windows-上建置)

This sets up a development environment, runs the self-tests and (optionally) assembles the offline kit. If you only
want to restore an archive, run its `_kit/install_windows.ps1` instead.

Tested on Windows 10 22H2 with CPython 3.13.

## 1. Prerequisites

| item | why |
|---|---|
| Windows 10/11 x64 | — |
| [Python 3.13](https://www.python.org/downloads/) x64 | runtime |
| [Git](https://git-scm.com/) | clone the repository |

No compiler is needed: the only dependencies, numpy and zstandard, come as prebuilt wheels.

## 2. Get the code

```powershell
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. Create the environment

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r kit\requirements-win_amd64.lock
```

This installs the exact, hash-checked versions the kit uses (numpy and zstandard).

## 4. Run the self-tests

```powershell
.venv\Scripts\python.exe -I tools\test_ans16.py
.venv\Scripts\python.exe -I tools\test_formats.py $env:TEMP\oc-test
```

Both must end with `ALL PASSED`.

## 5. Assemble the offline kit (optional)

```powershell
.venv\Scripts\python.exe -I kit\build_kit.py archive\_kit
```

Downloads and checks against pinned SHA-256: the numpy and zstandard wheels for Windows and Linux, the python.org
3.13.16 installer and python-build-standalone 3.13.16, then writes `SHA256SUMS` for the kit. Add
`--cache <old_kit>` to reuse files from a previous kit without downloading them again.

Next: [usage](usage.md).

---

# 在 Windows 上建置

[English](#build-on-windows) · **中文**

本頁說明如何建立開發環境、執行自我測試，以及（選用）組裝離線工具包。若只是要還原既有的歸檔，請直接執行歸檔內的
`_kit/install_windows.ps1`。

已在 Windows 10 22H2、CPython 3.13 上實測。

## 1. 事前準備

| 項目 | 用途 |
|---|---|
| Windows 10/11 x64 | — |
| [Python 3.13](https://www.python.org/downloads/) x64 | 執行環境 |
| [Git](https://git-scm.com/) | 取得原始碼 |

不需要任何編譯器：僅有的兩個依賴 numpy 與 zstandard 都有現成的 wheel。

## 2. 取得原始碼

```powershell
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. 建立環境

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r kit\requirements-win_amd64.lock
```

這會安裝與工具包完全相同、並經雜湊檢查的版本（numpy 與 zstandard）。

## 4. 執行自我測試

```powershell
.venv\Scripts\python.exe -I tools\test_ans16.py
.venv\Scripts\python.exe -I tools\test_formats.py $env:TEMP\oc-test
```

兩者都必須以 `ALL PASSED` 結束。

## 5. 組裝離線工具包（選用）

```powershell
.venv\Scripts\python.exe -I kit\build_kit.py archive\_kit
```

會下載並以固定的 SHA-256 核對：Windows 與 Linux 的 numpy、zstandard wheel、python.org 3.13.16 安裝檔、
python-build-standalone 3.13.16，最後為工具包寫出 `SHA256SUMS`。加上 `--cache <舊工具包>` 可重用既有檔案，不必重新下載。

接著請看[使用方式](usage.md#使用方式)。
