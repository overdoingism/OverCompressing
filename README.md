# OverCompressing

**Lossless archiving for open-weight LLMs — every file restored bit for bit, at half the size or less.**
**開源大型語言模型的無損歸檔工具：每個檔案都能逐位元還原，體積減半甚至更小。**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)
![Windows tested](https://img.shields.io/badge/Windows-tested-brightgreen.svg)
![Linux untested](https://img.shields.io/badge/Linux-not%20yet%20tested-lightgrey.svg)

Model weights hardly compress with zip or zstd, and quantizing them loses information. OverCompressing keeps the
original files exactly, compresses each tensor with a codec suited to it, and stores fine-tunes as deltas against
their base model. An offline kit (Python, wheels, tools, docs) is stored next to the archive so it can be restored
without internet access.

模型權重用 zip、zstd 幾乎壓不動，量化又會失真。OverCompressing 完整保留原檔，對每個 tensor 用最合適的方式壓縮，
並把微調模型存成「和 base 的差異」。歸檔旁附有離線工具包（Python、套件、工具、文件），沒有網路也能還原。

---

## Results at a glance / 實測摘要

| what | result |
|---|---|
| one BF16 model (Qwen2.5-0.5B / 7B) | **66.4 %** of the original (zstd -19: 75.9 %; entropy floor ≈ 66.2 %) |
| official Instruct as a delta of its Base | **44 %** (0.5B), **30 %** (7B) |
| community fine-tunes (RL, abliteration, re-uploads) | **0.1 – 5 %** |
| F16 GGUF against a BF16 base · vocab-extended draft model | **44 %** · **30 %** |
| test archive: 6 repos, 34.3 GiB | **17.2 GiB (50.1 %)** |

All results are verified by decoding from disk and matching the original SHA-256 (for these tests, also the
hashes Hugging Face publishes).

→ **[Benchmarks](docs/benchmarks.md)**

## How it works / 原理

- **Per tensor.** safetensors and GGUF files are parsed. Float tensors are split into byte planes so that the
  exponents sit together (the byte-grouping idea of [ZipNN](https://github.com/zipnn/zipnn)) and are entropy-coded
  with zlib's Huffman coding; quantized tensors and metadata go to zstd. The whole tool is Python on NumPy, zlib and
  zstd: no compiled code of its own, no PyTorch.
- **Deltas with the base as predictor.** A fine-tune is stored as residuals against its base, coded by **ans16**, a
  context-modelled rANS coder written for this project. No neural network is trained: the base model *is* the
  predictor.
- **Matched by tensor name** across file names, shardings, orders, float dtypes (BF16/F16/F32) and vocabulary sizes.
- **Base chosen automatically.** Each file is trial-coded against every matching model already in the archive and
  uses the one that compresses it best, or none; `--base` overrides.
- **Verified at every step**; identical files are stored once. Damage is detected; for repair, pick a protection
  scheme ([docs/protection.md](docs/protection.md)).

→ **[How it works](docs/principles.md)**

## Scope / 適用範圍

| works well | limited |
|---|---|
| BF16 / F16 / F32 / FP8 safetensors and GGUF | quantized files on their own (95–97 %) |
| families: Base → Instruct → community fine-tunes | GGUF ↔ safetensors cross-format deltas (not yet) |
| PyTorch `.bin` and other binaries (auto codec) | Linux (scripts provided, untested) |

→ **[Scope](docs/scope.md)**

## Quick start / 快速開始

```bash
python -I tools/llm_archive.py pack    models/Qwen2.5-0.5B archive --name Qwen/Qwen2.5-0.5B
python -I tools/llm_archive.py pack    models/Qwen2.5-0.5B-Instruct archive --name Qwen/Qwen2.5-0.5B-Instruct
python -I tools/llm_archive.py verify  archive
python -I tools/llm_archive.py restore archive Qwen/Qwen2.5-0.5B-Instruct restored
```

Get the model folders with any tool you like (OverCompressing never downloads) → pack the base → pack the fine-tune
(it finds its base in the archive by itself and is stored as a delta) → verify → restore. Prefer a window?
`python -I tools/llm_archive_gui.py archive` does pack, verify and extract with a few clicks. Want one file?
`python -I tools/llm_archive.py bundle archive models.oczip --delete` puts the whole archive into one `.oczip` (a ZIP),
verifies it and removes the folder's files; `verify` and `restore` read the `.oczip` as it is.

→ **[Usage / 使用方式（中英對照）](docs/usage.md)**

## Build / 建置

| platform | guide |
|---|---|
| Windows 10/11 x64 | **[docs/build-windows.md](docs/build-windows.md)** (中英對照) |
| Linux x86-64 | **[docs/build-linux.md](docs/build-linux.md)** (中英對照) |

Needs CPython 3.13 with numpy and zstandard, both available as prebuilt wheels: no compiler on any platform.
`kit/build_kit.py` assembles the offline kit from pinned, hash-checked downloads.

## Protecting the archive / 防護與修復

OverCompressing detects damage but leaves repair to you: several copies, PAR2, RAR recovery records, ZFS/Btrfs/ReFS,
optical ECC or tape all work. **[docs/protection.md](docs/protection.md)** (中英對照) compares them and lists what matters for
an archive with deltas (protect bases most, rebuild repair data after a re-pack or rebase).

## Archive format / 歸檔格式

The on-disk format is documented byte by byte, including how to decode ans16 by hand, so archives stay readable
without this tool: **[tools/ARCHIVE_FORMAT.md](tools/ARCHIVE_FORMAT.md)**.

## Repository layout / 目錄結構

```
tools/llm_archive.py       the archiver (pack / rebase / verify / restore / bundle)
tools/llm_archive_gui.py   a small window (Tkinter) for pack, bundle, verify and restore
tools/ARCHIVE_FORMAT.md    format specification
tools/compare_tensors.py   how well a fine-tune lines up with a base
tools/test_*.py            self-tests (ans16 coder, formats, conversions)
kit/                       offline kit: install scripts, pinned locks, keys, build_kit.py
docs/                      principles, scope, benchmarks, usage, build guides, protection advice
dev/                       experiments and measurement scripts used during development
```

## References / 參考文獻

- M. Hershcovitch et al., *Lossless and Near-Lossless Compression for Foundation Models*, 2024 —
  [arXiv:2404.15198](https://arxiv.org/abs/2404.15198) (the earliest of the IBM papers this work starts from).
- M. Hershcovitch et al., *ZipNN: Lossless Compression for AI Models*, 2024 —
  [arXiv:2411.05239](https://arxiv.org/abs/2411.05239) · code: [github.com/zipnn/zipnn](https://github.com/zipnn/zipnn)
  (byte grouping of float weights: the idea behind the byte-plane codec, implemented here independently).
- J. Duda, *Asymmetric numeral systems*, 2009 — [arXiv:0902.0271](https://arxiv.org/abs/0902.0271); and
  *ANS: entropy coding combining speed of Huffman coding with compression rate of arithmetic coding*, 2013 —
  [arXiv:1311.2540](https://arxiv.org/abs/1311.2540).
- F. Giesen, [ryg_rans](https://github.com/rygorous/ryg_rans) (64-bit interleaved rANS; public domain/CC0).
- T. Zhang et al., *70% Size, 100% Accuracy: Lossless LLM Compression for Efficient GPU Inference via Dynamic-Length
  Float (DFloat11)*, 2025 — [arXiv:2504.11651](https://arxiv.org/abs/2504.11651) (evaluated; needs a GPU to decode).
- DEFLATE, [RFC 1951](https://www.rfc-editor.org/rfc/rfc1951) (zlib); Zstandard, [RFC 8878](https://www.rfc-editor.org/rfc/rfc8878).

## License / 授權

MIT — see [LICENSE](LICENSE). Third-party components and their licenses (numpy BSD-3, zstd BSD-3/GPL-2, CPython
PSF with zlib, python-build-standalone MPL-2.0) are listed in
**[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)**.
