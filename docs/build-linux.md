# Build on Linux

**English** · [中文](#在-linux-上建置)

> **Status: these steps follow the Windows setup that was tested end to end, but have not yet been run on a real
> Linux machine.** Please report what you find.

This sets up a development environment on Linux x86-64 (glibc), runs the self-tests and (optionally) assembles the
offline kit. To restore an existing archive, run its `_kit/install_linux.sh` instead.

## 1. Prerequisites

| item | why |
|---|---|
| CPython 3.13 (e.g. a distribution package, or [python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | runtime |
| git | fetch the code |

No compiler is needed: the only dependencies, numpy and zstandard, come as prebuilt wheels.

## 2. Get the code

```bash
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. Create the environment

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r kit/requirements-linux_x86_64.lock
```

This installs the exact, hash-checked versions the kit uses (numpy and zstandard).

## 4. Run the self-tests

```bash
.venv/bin/python -I tools/test_ans16.py
.venv/bin/python -I tools/test_formats.py /tmp/oc-test
```

Both must end with `ALL PASSED`. On Linux they also cover FIFOs and broken links in a source folder, which Windows
cannot create.

## 5. Assemble the offline kit (optional)

```bash
.venv/bin/python -I kit/build_kit.py archive/_kit
```

The kit holds the wheels for both Windows and Linux, so a kit built on Linux also installs on Windows. Add
`--cache <old_kit>` to reuse files from a previous kit.

Next: [usage](usage.md).

---

# 在 Linux 上建置

[English](#build-on-linux) · **中文**

> **狀態：以下步驟沿用已完整實測的 Windows 流程，但尚未在實體 Linux 機器上執行過。** 歡迎回報結果。

本頁說明如何在 Linux x86-64（glibc）上建立開發環境、執行自我測試，以及（選用）組裝離線工具包。
若只是要還原既有的歸檔，請直接執行歸檔內的 `_kit/install_linux.sh`。

## 1. 事前準備

| 項目 | 用途 |
|---|---|
| CPython 3.13（例如發行版套件，或 [python-build-standalone](https://github.com/astral-sh/python-build-standalone)） | 執行環境 |
| git | 取得原始碼 |

不需要任何編譯器：僅有的兩個依賴 numpy 與 zstandard 都有現成的 wheel。

## 2. 取得原始碼

```bash
git clone https://github.com/overdoingism/OverCompressing.git
cd OverCompressing
```

## 3. 建立環境

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r kit/requirements-linux_x86_64.lock
```

這會安裝與工具包完全相同、並經雜湊檢查的版本（numpy 與 zstandard）。

## 4. 執行自我測試

```bash
.venv/bin/python -I tools/test_ans16.py
.venv/bin/python -I tools/test_formats.py /tmp/oc-test
```

兩者都必須以 `ALL PASSED` 結束。在 Linux 上還會額外測試來源資料夾中的 FIFO 與失效連結（Windows 無法建立這兩種檔案）。

## 5. 組裝離線工具包（選用）

```bash
.venv/bin/python -I kit/build_kit.py archive/_kit
```

工具包同時收錄 Windows 與 Linux 的 wheel，所以在 Linux 上組裝的工具包也能在 Windows 安裝。加上 `--cache <舊工具包>`
可重用既有檔案。

接著請看[使用方式](usage.md#使用方式)。
