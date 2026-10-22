# Benchmarks

> **中文重點**
> - 單一 BF16/F16 模型約壓到 **66.4%**（理論下限 66.2%）；一般的 zstd -19 只有約 75%。量化 GGUF 約 95–97%。
> - 浮點 tensor 用本工具自己的位元平面編碼（NumPy + zlib + zstd），壓縮率比原本使用的 ZipNN 還小 0.1–0.9 個百分點。
> - 同家族 delta：官方 Instruct 對 Base 為 **44%（0.5B）/ 30%（7B）**；社群輕度微調可到 **0.1–5%**；F16 GGUF 對 BF16 base、詞彙表擴充的模型也能共用。
> - Qwen2.5 測試歸檔：6 個 repo 共 34.3 GiB → **17.2 GiB（50.1%）**。
> - base 自動挑選：社群微調模型全部選到與手動相同的 base，每個檔案約 1–10 秒。
> - 每一筆數字都經過「從磁碟解回、SHA-256 一致」的驗證。
> - 速度（8 核 16 緒）：pack 7B 約 97 秒、restore 約 31 秒、verify 7B 加 Instruct 約 155 秒；delta 的速度取決於 ans16，它本身已用滿所有核心。

Setup: Windows 10, 8 cores / 16 threads, 92 GB RAM, local disk, CPython 3.13, tool version 4, measured 2026-10-21.
Sizes are stored size ÷ original size. Every result was verified by decoding from disk and comparing the SHA-256
with the original (for these downloads also with the hash Hugging Face publishes).

## Single models (no base)

| file | original | OverCompressing | `zstd -19`, whole file |
|---|---|---|---|
| Qwen2.5-0.5B `model.safetensors` (BF16) | 942 MiB | **66.4 %** | 75.9 % |
| Qwen2.5-7B, 4 shards (BF16) | 14.2 GiB | **66.5 %** | — |
| Qwen2.5-0.5B-Instruct GGUF BF16 | 948 MiB | **66.1 %** | 75.4 % |
| … GGUF F16 | 948 MiB | 66.1 % | — |
| … GGUF Q8_0 | 507 MiB | 94.6 % | — |
| … GGUF Q4_K_M | 379 MiB | 96.7 % | 96.7 % |
| `tokenizer.json` | 6.7 MiB | 22.4 % | — |

Entropy floor of the BF16 weights (sign + exponent + mantissa, coded separately): 10.6 bits = 66.2 %.

## The float codec: byte planes vs ZipNN

Float tensors used to be compressed with ZipNN 0.5.4. They now use the tool's own byte planes: the same byte-grouping
idea, done with NumPy bit operations, the exponent plane coded with zlib's Huffman-only DEFLATE and the other planes
with zstd or as they are ([ARCHIVE_FORMAT.md](../tools/ARCHIVE_FORMAT.md)). This removed ZipNN and PyTorch from the
dependencies. Trial run before the switch, region by region on the float tensors, every region decoded back and
compared (exponent plane by Huffman-only DEFLATE, other planes by zstd level 1; the shipped codec takes the smallest
of DEFLATE, zstd and stored for every plane and gains a little more, e.g. 66.1 % instead of 66.9 % on the F16 GGUF):

| data | ZipNN 0.5.4 | byte planes | difference |
|---|---|---|---|
| Qwen2.5-0.5B (BF16, 942 MiB) | 66.61 % | 66.41 % | −0.19 pp |
| Qwen2.5-7B shard 1 (BF16, 3.7 GiB) | 67.28 % | 67.07 % | −0.21 pp |
| Qwen2.5-0.5B-Instruct GGUF F16 | 67.06 % | 66.94 % | −0.12 pp |
| F32: 400 MiB of the 0.5B's BF16 weights with random low 16 bits (like master weights) | 83.26 % | 83.02 % | −0.24 pp |
| FP8 E4M3: the same weights, scaled per tensor | 82.60 % | 81.73 % | −0.88 pp |
| FP8 E5M2: the same weights, scaled per tensor | 70.10 % | 69.34 % | −0.75 pp |

zstd alone on the exponent plane is worse (level 3: +3.8 pp, a literals-only setting: +1.2 pp; level 19 matches
DEFLATE's ratio at 2 MB/s): its LZ matching finds nothing to reuse in exponents. In memory, on 64 MiB pieces of the
7B with 8 threads, byte planes run at about 490 MB/s compressing (each plane tried with DEFLATE and zstd) and 1.5 GB/s
decompressing (ZipNN with its own threads, on the 0.5B: about 850 MB/s compress, 1.4 GB/s decompress); whole-model
times are below.

## Official fine-tunes against their base

| target | base | standalone | delta |
|---|---|---|---|
| Qwen2.5-0.5B-Instruct | Qwen2.5-0.5B | 66.4 % | **44.2 %** (XOR with the base, in byte planes: 53.6 %) |
| Qwen2.5-7B-Instruct (4 shards) | Qwen2.5-7B | 66.5 % | **30.0 %** |
| Instruct GGUF BF16 | Base GGUF BF16 (other uploader layout) | 66.1 % | **44.1 %** |
| Instruct GGUF F16 | Base GGUF **BF16** (converted) | 66.1 % | **44.2 %** |
| Instruct GGUF Q4_K_M | Base GGUF Q4_K_M | 96.7 % | **64.6 %** |
| Instruct GGUF Q8_0 | Base GGUF (no Q8_0 in base) | 94.6 % | 87.5 % |

## Community fine-tunes of Qwen2.5-0.5B

| model | kind | base | weights bit-identical to base | delta |
|---|---|---|---|---|
| mazdaypci/…-Gensyn-Swarm-wily_padded_mink | RL fine-tune | Instruct | 85.9 % | **1.45 %** (942 → 14 MiB) |
| mlx-community/Qwen2.5-0.5B-Instruct-bf16 | re-upload (MLX) | Instruct | 100 % | **0.10 %** |
| saidutta69/Qwen2.5-0.5B-Instruct-heretic | abliteration | Instruct | 87.5 % | **5.2 %** |
| … its F16 GGUF | abliteration, GGUF | Instruct GGUF | — | **5.3 %** (66.0 % standalone) |
| alamios/DeepSeek-R1-DRAFT-Qwen2.5-0.5B | new vocabulary (+128 rows), trained from Base | Base | 40.4 % | **30.4 %** |
| same Instruct, re-sharded into 2 files, tensor order reversed (synthetic)\* | layout change | Base | — | 44.2 % (identical to the original) |

\* measured with tool version 3; ans16 deltas did not change in version 4.

## Automatic base selection

The same community models, packed one after another into the test archive without `--base` (each later one also
sees the earlier ones as candidates). Every file picked the base that had been found best by hand:

| file | candidates | picked | runner-up (estimate) | estimate | stored | time |
|---|---|---|---|---|---|---|
| Gensyn fine-tune | 2 | Qwen2.5-0.5B-Instruct | Qwen2.5-0.5B (43.6 %) | 1.4 % | 1.45 % | 6 s |
| DeepSeek-R1 draft | 3 | Qwen2.5-0.5B | Qwen2.5-0.5B-Instruct (45.4 %) | 30.3 % | 30.4 % | 9 s |
| MLX re-upload | 4 | Qwen2.5-0.5B-Instruct | Gensyn fine-tune (1.3 %) | 0.0 % | 0.10 % | 10 s |
| heretic, safetensors | 5 | Qwen2.5-0.5B-Instruct | MLX re-upload (2.3 %, a tie: longer chain) | 2.3 % | 5.2 % | 10 s |
| heretic, F16 GGUF | 2 | Instruct GGUF | Base GGUF (43.8 %) | 2.3 % | 5.3 % | 4 s |
| Qwen2.5-7B-Instruct, each of 4 shards (3.3–3.7 GiB) | 1 | Qwen2.5-7B | — | 29.2–31.2 % | 29.4–31.6 % | 6 s |
| Instruct GGUF Q4_K_M | 1 | Base GGUF | — (standalone 97.6 %) | 61.0 % | 64.6 % | 1 s |

Estimates are for the file's tensor data and are meant to rank candidates; they can be a few points off (heretic:
2.3 % estimated, 5.2 % stored). The time is the selection alone, before packing; it grows with the number of
candidates, and with candidates that are deltas themselves (their sampled parts are decoded through the chain).

## Whole test archive

| repo | original | stored |
|---|---|---|
| Qwen/Qwen2.5-0.5B | 0.93 GiB | 65.9 % |
| Qwen/Qwen2.5-0.5B-Instruct (delta) | 0.93 GiB | 43.7 % |
| Qwen/Qwen2.5-7B | 14.20 GiB | 66.5 % |
| Qwen/Qwen2.5-7B-Instruct (delta) | 14.20 GiB | 30.0 % |
| mradermacher/Qwen2.5-0.5B-GGUF (BF16 + Q4_K_M) | 1.30 GiB | 74.9 % |
| mradermacher/Qwen2.5-0.5B-Instruct-GGUF (BF16, F16, Q8_0, Q4_K_M; delta) | 2.72 GiB | 54.8 % |
| **total** | **34.27 GiB** | **17.18 GiB (50.1 %)** |

Shared tokenizers (0.5B and 7B use the same files) are stored once.

## Speed and memory (Qwen2.5-7B)

Measured 2026-10-22 on the same files before and after that day's speed work (byte planes without whole-width
temporaries, files hashed and blobs checked several at once, verify restoring a base once); models read from one
NVMe SSD, pack and temporary files on another.

| operation | before | after | cores in use (after) | peak memory (after) |
|---|---|---|---|---|
| pack 7B base (incl. hashing and verification) | 143 s | **97 s** | 4.2 | 2.4 GiB |
| pack 7B-Instruct as ans16 delta (incl. base selection and verification) | 331 s | 336 s | 9.5 | 12.5 GiB |
| verify both models | 194 s | **155 s** | 9.8 | 11.4 GiB |
| restore 7B base to disk | 49 s | **31 s** (≈ 500 MB/s) | 3.0 | 1.9 GiB |
| restore 7B-Instruct to disk | 165 s | 164 s | 9.2 | 11.5 GiB |
| ans16 on one 3.3 GB shard\* | encode 29 s, decode 27 s | — | — | — |

Cores in use: CPU time of all the tool's processes over the time taken (16 threads here). Peak memory: their
committed memory together, the peak of a job object holding them. Deltas are bound by ans16 (NumPy rANS, about
8 MB/s per process), which the speed work did not touch: verifying one Instruct shard took 45.1 s before and 39.9 s
after, its ans16 part 31.6 s both times (two runs each, alternating); the delta rows vary by about ±10% from run to
run. ans16 runs in one worker process per CPU thread (16 here), byte planes and zstd on up to 8 threads, hashing on
up to 4; `OC_WORKERS` lowers all of them, and with them the memory. With 16 byte-plane threads the 7B base packed in
89 s instead of 97 s but took 4.1 GiB instead of 2.4 GiB, so 8 stays. \* measured with tool version 3; the ans16 code
did not change.

### What held the byte-plane threads back (64 MiB pieces of the 7B, in memory)

| code | compress, 8 / 16 threads | decompress, 8 / 16 threads |
|---|---|---|
| before: whole-width (16-bit) temporaries and copies, on threads | 342 / 433 MB/s | 615 / 687 MB/s |
| the same code in worker processes | 327 / 361 MB/s | 413 / 365 MB/s |
| now: byte operations into arrays made once, on threads | 490 / 672 MB/s | 1541 / 1582 MB/s |

Processes were no faster, so the GIL was not the limit; memory traffic was. The output is byte for byte the same.
One thread: 79 → 83 MB/s compressing, 242 → 362 MB/s decompressing.

### zstd level on quantized GGUF tensor data (Q4_K_M, 64 MiB pieces)

| level | size | speed |
|---|---|---|
| 3 | 97.89 % | 867 MiB/s |
| 9 (default for tensors) | 97.83 % | 496 MiB/s |
| 15 | 97.82 % | 92 MiB/s |
| 19 (default for text/metadata) | 97.76 % | 6.3 MiB/s |

## Integrity and failure tests

| test | result |
|---|---|
| flip 1 byte in a base container | `verify` reports it and every delta / reference depending on it; `restore` refuses |
| external repair with par2cmdline, 5 % (see [protection.md](protection.md)) | 1990/1991 blocks found, repaired, re-verified, SHA-256 restored |
| synthetic cyclic reference | refused with the cycle printed (`A -> B -> A`) |
| portable copy made by the Windows build guide, run as written (PowerShell 5.1) | its Python passes both self-test suites and the window checks; the window starts from its launcher |
| ans16 unit tests (sizes 1…8M, all-zero, random, sign flips, rare contexts, F16, corrupted payloads, empty input) | all pass; corruption always detected |
| byte planes, every float dtype (lengths 1…1M+1 around element edges; noise, zeros, constant, weights; damaged payloads) | all round trips exact; every damaged payload refused |
| dtype conversion vs references (200 011 values incl. subnormals, overflow, ±inf, NaN) | 0 mismatches vs numpy (F16) and an exact-arithmetic reference (BF16) |
| restored 7B models compared with the originals byte by byte | identical |

## Problems found in third-party code along the way

- PowerShell 5.1 strips double quotes from native-command arguments (broke a `python -c` check in an early install script).
