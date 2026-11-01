# Benchmarks

> **中文重點**
> - 單一 BF16/F16 模型約壓到 **66.1%**（order-0 熵約 65.8%）；一般的 zstd -19 只有約 75%。量化 GGUF 約 95–97%。
> - 浮點 tensor 用本工具自己的位元平面編碼（NumPy + zlib + zstd），壓縮率比原本使用的 ZipNN 更小，速度也更快（見下方）。
> - 同家族 delta：官方 Instruct 對 Base 為 **44%（0.5B）/ 30%（7B）**；社群輕度微調可到 **0.1–5%**；F16 GGUF 對 BF16 base、詞彙表擴充的模型也能共用。
> - Qwen2.5 測試歸檔：6 個 repo 共 34.3 GiB → **15.0 GiB（43.8%）**（format 4：17.2 GiB）；與 safetensors 相同權重的 GGUF 只占 **0.25%**。
> - FP8 的 MoE 微調（ThinkingCap 對 Qwen3.8-Flash-Next-FP8）：微調沒有動 routed experts，FP8 部分以 XOR 存成幾乎 0；實測一個 6.7 GiB 的 shard 存成 **0.27%**，整個 185.5 GB 模型估計約 1 GB。
> - base 自動挑選：社群微調模型全部選到與手動相同的 base，每個檔案約 1–10 秒。
> - 每一筆數字都經過「從磁碟解回、SHA-256 一致」的驗證。
> - 速度（8 核 16 緒）：pack 7B 約 77 秒、restore 約 33 秒、verify 7B 加 Instruct 約 147 秒；delta 的速度取決於 ans16，它本身已用滿所有核心。

Setup: Windows 10, 8 cores / 16 threads, 92 GB RAM, local disk, CPython 3.13, format 5, measured 2026-10-31 unless a section says otherwise.
Sizes are stored size ÷ original size. Every result was verified by decoding from disk and comparing the SHA-256
with the original (for these downloads also with the hash Hugging Face publishes).

## Single models (no base)

| file | original | OverCompressing | `zstd -19`, whole file |
|---|---|---|---|
| Qwen2.5-0.5B `model.safetensors` (BF16) | 942 MiB | **66.1 %** | 75.9 % |
| Qwen2.5-7B, 4 shards (BF16) | 14.2 GiB | **66.2 %** | — |
| Qwen2.5-0.5B-Instruct GGUF BF16 | 948 MiB | **65.8 %** | 75.4 % |
| … GGUF F16 | 948 MiB | 66.1 % | — |
| … GGUF Q8_0 | 507 MiB | 94.6 % | — |
| … GGUF Q4_K_M | 379 MiB | 96.7 % | 96.7 % |
| `tokenizer.json` | 6.7 MiB | 22.4 % | — |

Order-0 entropy of the Qwen2.5-0.5B's BF16 weights: 10.60 bits (66.2 %) with sign, exponent and mantissa coded
separately, 10.52 bits (65.8 %) with the 16 bits coded together.

## The float codec: byte planes, rotation and the choice of coder

Format 5 changes two things against format 4 (below): BF16 and F32 rotate their top 16 bits left by 3 bits before
grouping, instead of aligning the exponent into a plane of its own, and each plane starts from zstd level 1, with
Huffman-only DEFLATE only where it saves over 0.5 % of the plane. F16 and FP8 keep their bytes. Measured on 13 F16 and
18 F32 files of different families, in memory on regions as the tool cuts them, 8 threads, high priority, every
region restored and compared (speeds in MiB/s, best of two runs):

| data | format 4 | format 5 | compress | decompress |
|---|---|---|---|---|
| SD 1.5 UNet (F32) | 83.303 % | **83.136 %** | 445 → 915 | 1427 → 1445 |
| GPT-2 large (F32) | 79.318 % | **79.137 %** | 443 → 912 | 1364 → 1399 |
| bge-large-en-v1.5 (F32 widened from F16) | 42.329 % | **42.257 %** | 450 → 933 | 1199 → 1264 |
| XOR: SD 1.5 UNet non-EMA against EMA (F32) | 73.293 % | **72.108 %** | 464 → 752 | 1225 → 1342 |
| Qwen2.5-0.5B (BF16) | 66.387 % | **66.068 %** | 404 → 880 | 994 → 1352 |
| MiniMax H3 LoRA (BF16) | 46.995 % | **46.425 %** | 444 → 886 | 1343 → 1369 |
| XOR: 0.5B-Instruct against 0.5B (BF16) | 53.608 % | **51.394 %** | 411 → 508 | 953 → 1175 |
| SDXL base (F16) | 84.534 % | 84.547 % | 486 → 1100 | 1859 → 2360 |
| whisper-large-v3 (F16) | 83.894 % | 83.918 % | 441 → 949 | 1764 → 2205 |
| UMT5 XXL (F16) | 84.500 % | 84.606 % | 419 → 1244 | 1507 → 2340 |
| Qwen3.8 mmproj (F16) | 65.915 % | 65.925 % | 386 → 526 | 1176 → 1401 |
| UMT5 XXL (F8_E4M3) | 81.904 % | 82.186 % | 430 → 2263 | 1087 → 2154 |

F32 weights fall into two groups: true F32 at about 83 %, and weights widened from a 16-bit float, whose low bits are
all zero, at 34–46 %. Rotating all 32 bits costs the second group 1.1–1.4 points; rotating the top 16 bits alone
costs nothing on the first and gains on the second. One small sample grows: the 169 MiB of F32 in a MiniMax GGUF, by
0.18 points. F16 is close to its order-0 entropy already (SDXL: 84.534 % against 84.241 %), and its exponent sits inside
the high byte, so rotating gains at most 0.04 points and slows decoding by 20–30 %.

Chunks of unknown type (`auto`) are not rotated but kept as plain byte columns: rotating costs an F16 `.bin` 5.5–6
points, while plain columns cost a BF16 `.bin`, or an F32 one widened from a 16-bit float, at most 1.7.

### Byte planes against ZipNN (format 4)

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
| Qwen2.5-0.5B-Instruct | Qwen2.5-0.5B | 66.1 % | **44.2 %** (XOR with the base, in byte planes: 51.4 %) |
| Qwen2.5-7B-Instruct (4 shards) | Qwen2.5-7B | 66.2 % | **30.0 %** |
| Instruct GGUF BF16 | Base GGUF BF16 (other uploader layout) | 65.8 % | **44.1 %** |
| Instruct GGUF F16 | Base GGUF **BF16** (converted) | 66.1 % | **44.2 %** |
| Instruct GGUF Q4_K_M | Base GGUF Q4_K_M | 96.7 % | **64.6 %** |
| Instruct GGUF Q8_0 | Base GGUF (no Q8_0 in base) | 94.6 % | 87.5 % |

## Quantized tensors from their parent

Qwen2.5-0.5B-Instruct, quantized by three uploaders (the Q5_K_M and Q6_K by bartowski, an imatrix Q4_K_M by mradermacher's i1,
the Q8_0 by mradermacher), restored and compared byte for byte (2026-10-31):

| file | standalone | against the BF16 GGUF | against the safetensors | against only a Q8_0 |
|---|---|---|---|---|
| Q8_0, 506.5 MiB | 94.6 % | **0.27 %** | **0.29 %** | — |
| Q6_K, 482.3 MiB | 95.8 % (est.) | **1.64 %** | **1.66 %** | **3.53 %** |
| Q5_K_M, 400.6 MiB | 97.1 % (est.) | 46.84 % | 46.86 % | 48.59 % |
| i1-Q4_K_M, 379.4 MiB | 97.6 % (est.) | 45.51 % | 45.53 % | 47.01 % |

The last two are held back in this small model: about 45 % of their bytes are Q5_0 or Q5_1 tensors, which are not rebuilt. The
Q4_K and Q5_K tensors themselves take about 10 % and 9 %, mostly their scales. In six large GGUF files here, Q5_K, Q6_K, Q8_0
and Q4_K make up 97 % of the bytes.

The prediction's arithmetic (binary32 add, multiply, divide, rint, trunc) gave the same as exact fractions on every value compared
(495,360 values of Q8_0, Q4_K and Q5_K), with NumPy's AVX2 paths on and off; and the Q8_0 tensors of the three uploaders are byte for byte the
same. A prediction with integers alone was about 6 times slower (10 MiB/s against 62, one thread), so the format defines the
binary32 results and the exact fractions are only the way back.

## A change of one direction

Abliteration takes one direction out of some matrices (`W - r r^T W`), so their change is one column times one row. Against its
original (2026-10-31), byte for byte restored:

| fine-tune | without | with |
|---|---|---|
| saidutta69/Qwen2.5-0.5B-Instruct-heretic, 942.3 MiB | 5.21 % | **0.62 %** |
| huihui-ai/Qwen2.5-VL-3B-Instruct-abliterated, 7.0 GiB | 546.6 MiB | **57.3 MiB** |
| heretic's F16 GGUF, 948.1 MiB | 5.33 % | 3.42 % |
| Gensyn fine-tune (RL) | 1.45 % | 1.45 % |
| Qwen2.5-0.5B-Instruct against Base | 44.21 % | 44.17 % |

Looking for it adds about 10 % to packing where it finds nothing, and about 25 % on the 7 GiB model above. It is tried only where a
sample of 256 rows has half its change's energy in one direction, and kept only where smaller.

## Across formats

A GGUF against the safetensors model of the same weights, with no name to go by (2026-10-31; 948 MiB, metadata
included):

| file | standalone | against the safetensors |
|---|---|---|
| Instruct GGUF BF16 | 65.8 % | **0.25 %** (2.4 MiB) |
| Instruct GGUF F16 | 66.1 % | **0.25 %** (2.4 MiB) |
| `model.safetensors` against the Instruct GGUF, the other way round | 66.1 % | **0.11 %** (1.0 MiB) |

99.98 % of the BF16 GGUF's elements are the same as in the safetensors. The choice costs 3–10 s per
file; packing such a file takes about as long as any ans16 delta of its size.

## Community fine-tunes of Qwen2.5-0.5B

| model | kind | base | weights bit-identical to base | delta |
|---|---|---|---|---|
| mazdaypci/…-Gensyn-Swarm-wily_padded_mink | RL fine-tune | Instruct | 85.9 % | **1.45 %** (942 → 14 MiB) |
| mlx-community/Qwen2.5-0.5B-Instruct-bf16 | re-upload (MLX) | Instruct | 100 % | **0.10 %** |
| saidutta69/Qwen2.5-0.5B-Instruct-heretic | abliteration | Instruct | 87.5 % | **0.62 %** |
| … its F16 GGUF | abliteration, GGUF | Instruct GGUF | — | **3.4 %** (66.0 % standalone) |
| alamios/DeepSeek-R1-DRAFT-Qwen2.5-0.5B | new vocabulary (+128 rows), trained from Base | Base | 40.4 % | **30.4 %** |
| same Instruct, re-sharded into 2 files, tensor order reversed (synthetic)\* | layout change | Base | — | 44.2 % (identical to the original) |

\* measured with tool version 3; ans16 deltas did not change in formats 4 and 5.

## An FP8 fine-tune of a MoE model (Qwen3.8-Flash-Next)

bottlecapai/ThinkingCap-Qwen3.8-Flash-Next-FP8 (revision `ca9f7a5c`) against Qwen/Qwen3.8-Flash-Next-FP8 (`236dfdf2`):
the fine-tune's last shard (layers 8–9, the embeddings and `lm_head`; 6.71 GiB) and the five base shards its tensors
match (7.23 GiB), measured 2026-10-24 on the same machine, in format 4 (format 5 stores FP8 about 0.3 points larger and
BF16 about 0.3 smaller). The by-dtype figures are from `dev/blob_stats.py`.

| | original | stored |
|---|---|---|
| base, 5 shards | 7.23 GiB | 76.5 % (F8_E4M3 81.9 %, BF16 66.4 %) |
| fine-tune shard, standalone | 6.71 GiB | 76.1 % |
| fine-tune shard, as a delta | 6.71 GiB | **0.27 %** (18.3 MiB) |
| … its F8_E4M3 routed experts, XOR delta | 4.17 GiB | 0.2 MiB: bit-identical to the base |
| … its BF16 tensors, ans16 delta | 2.54 GiB | 0.67 % |

Times: pack the base 50 s; the fine-tune shard as a delta 109 s (standalone 41 s); verify both 46 s; restore the
fine-tune shard 48 s.

The fine-tune left the routed experts as the official FP8 release has them: a 64 KiB slice of one routed expert per
layer, read from both repositories, was identical in all 48 layers, while the BF16 attention (36 of 48 layers) and the
shared experts (all 48) differ. So the FP8 part of such a fine-tune needs nothing beyond XOR, and its changes take the
ans16 path. For the whole 185.5 GB model this suggests about 1 GB stored as a delta (an estimate: at most its 11 GB of
BF16 tensors change, coded at about 10 % as in layers 8–9).

## Automatic base selection

The same community models, packed one after another into the test archive without `--base` (each later one also
sees the earlier ones as candidates). Every file picked the base that had been found best by hand:

| file | candidates | picked | runner-up (estimate) | estimate | stored | time |
|---|---|---|---|---|---|---|
| Gensyn fine-tune | 2 | Qwen2.5-0.5B-Instruct | Qwen2.5-0.5B (43.6 %) | 1.4 % | 1.45 % | 6 s |
| DeepSeek-R1 draft | 3 | Qwen2.5-0.5B | Qwen2.5-0.5B-Instruct (45.4 %) | 30.3 % | 30.4 % | 9 s |
| MLX re-upload | 4 | Qwen2.5-0.5B-Instruct | Gensyn fine-tune (1.3 %) | 0.0 % | 0.10 % | 10 s |
| heretic, safetensors | 5 | Qwen2.5-0.5B-Instruct | MLX re-upload (2.3 %, a tie: longer chain) | 2.3 % | 5.2 %\* | 10 s |
| heretic, F16 GGUF | 2 | Instruct GGUF | Base GGUF (43.8 %) | 2.3 % | 5.3 %\* | 4 s |
| Qwen2.5-7B-Instruct, each of 4 shards (3.3–3.7 GiB) | 1 | Qwen2.5-7B | — | 29.2–31.2 % | 29.4–31.6 % | 6 s |
| Instruct GGUF Q4_K_M | 1 | Base GGUF | — (standalone 97.6 %) | 61.0 % | 64.6 % | 1 s |

\* Stored without a change of one direction (`--no-rank1`), which the estimates do not foresee. Estimates are for the file's tensor data and are meant to rank candidates; they can be a few points off (heretic:
2.3 % estimated, 5.2 % stored). The time is the selection alone, before packing; it grows with the number of
candidates, and with candidates that are deltas themselves (their sampled parts are decoded through the chain).

## Whole test archive

Packed one after another by both versions, then verified, 2026-11-01:

| repo | original | format 4 | format 5 |
|---|---|---|---|
| Qwen/Qwen2.5-0.5B | 0.93 GiB | 65.9 % | 65.6 % |
| Qwen/Qwen2.5-0.5B-Instruct (delta) | 0.93 GiB | 44.0 % | 44.0 % |
| Qwen/Qwen2.5-7B | 14.20 GiB | 66.5 % | 66.2 % |
| Qwen/Qwen2.5-7B-Instruct (delta) | 14.20 GiB | 30.1 % | 30.0 % |
| mradermacher/Qwen2.5-0.5B-GGUF (BF16, Q4_K_M) | 1.30 GiB | 74.8 % | **13.2 %** |
| mradermacher/Qwen2.5-0.5B-Instruct-GGUF (BF16, F16, Q8_0, Q4_K_M) | 2.72 GiB | 54.8 % | **6.4 %** |
| **total** | **34.27 GiB** | **17.18 GiB (50.1 %)** | **15.01 GiB (43.8 %)** |

Shared tokenizers (0.5B and 7B use the same files) are stored once, so the rows add up to more than the total. Pack
all six 598 s → 746 s, verify all six 187 s → 221 s; one run each. [What format 5 changed](format-5.md).

## Speed and memory (Qwen2.5-7B)

Format 4 against format 5, the real command line, 2026-10-31, two rounds in turn (7B base, 66.50 % → 66.19 %):

| operation | format 4 | format 5 |
|---|---|---|
| pack | 110.5, 105.0 s | **80.8, 72.7 s** |
| verify | 24.2, 21.9 s | 22.1, 21.2 s |
| restore | 33.6, 32.2 s | 32.6, 34.9 s |

The rest of the test archive in format 5: pack 7B-Instruct as an ans16 delta 311 s (415 s on 2026-11-01, with the search
for a change of one direction, one run); verify 7B and 7B-Instruct 147 s; restore 7B-Instruct 160 s.

The speed work of 2026-10-22, in format 4, measured on the same files before and after it (byte planes without whole-width
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
| byte planes, every float dtype (lengths 1…1M+1 around element edges and around the plane sizes where the Huffman sample changes; noise, zeros, constant, weights; damaged payloads) | all round trips exact; every damaged payload refused |
| dtype conversion vs references (200 011 values incl. subnormals, overflow, ±inf, NaN) | 0 mismatches vs numpy (F16) and an exact-arithmetic reference (BF16) |
| restored 7B models compared with the originals byte by byte | identical |

## Problems found in third-party code along the way

- PowerShell 5.1 strips double quotes from native-command arguments (broke a `python -c` check in an early install script).
