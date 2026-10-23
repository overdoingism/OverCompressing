# Scope: what it handles

> **中文重點**
> - 格式：safetensors、GGUF（v2/v3）逐 tensor 處理；PyTorch `.bin`/`.pth` 等其他二進位檔自動在位元平面與 zstd 間擇優；文字檔用 zstd。
> - 能大幅壓縮的是 BF16/F16/F32/FP8 浮點權重（約 66%）；量化檔（GGUF Q4/Q8、GPTQ…）本身已接近極限（95–97%），但同家族之間仍可用 delta 省下不少。
> - 同家族 delta：名稱相同的 tensor 自動配對，可跨檔名、分片、順序、dtype、詞彙表大小；base 也會自動挑選，之後加入更近的祖先時可用 `rebase` 重新挑選（不需要原資料夾）。
> - 量化格式實測（2026-10-24，列出實測的發布者與 revision，見下方表格）：MXFP4、NVFP4、FP8、EXL3 的 safetensors，以及帶引擎自訂 type 的 GGUF，都能逐 tensor 解析；封包的 4-bit 資料約 90–93%，EXL3 約 99.5%；沒動 FP8 experts 的 FP8 微調存成 delta 幾乎為 0。
> - 尚不支援：GGUF 與 safetensors 之間的跨格式配對、量化 tensor 的部分列配對、隱藏層寬度改變的模型、整個 pack 一起最佳化的 base 規劃（`rebase` 一次處理一個模型）。
> - 環境：CPython 3.13（x86-64）。Windows 已完整實測；Linux 由下游團隊實測。

## File formats

| format | how it is split | float tensors | other tensors / data |
|---|---|---|---|
| **safetensors** | JSON header | byte planes (BF16, F16, F32, F8_E4M3, F8_E5M2) | zstd (I8, U8, I16, I32, I64, BOOL, F8_E8M0, F4, …: any other dtype) |
| **GGUF** v2/v3 | tensor table (alignment honoured) | byte planes (F32, F16, BF16) | zstd (all quantized types: Q4_K, Q8_0, IQ…, MXFP4, and types the tool does not know, such as an engine's own) |
| PyTorch `.bin` / `.pth` / `.ckpt`, ONNX, anything else | 64 MiB chunks (`auto`) | — | per chunk: smaller of BF16 byte planes and zstd |
| text (`.json`, `.txt`, `.md`, `.model`, `.jinja`, …) | 64 MiB chunks | — | zstd -19 |

Pickle files are only read as bytes; nothing is ever unpickled or executed. A file whose header does not parse falls
back to `auto`, so malformed or unknown files are still stored losslessly.

Typical ratios on their own (see [benchmarks](benchmarks.md)): BF16/F16 ≈ 66 %, FP8 ≈ 80–85 %,
quantized GGUF ≈ 95–97 %, tokenizer JSON ≈ 15–25 %.

## Quantized formats checked

A quantization's name says little about its files: publishers lay out the same scheme differently. What was checked
(2026-10-24) is these files at these revisions: the headers of 266 files from 11 releases, and whole shards packed,
verified, exported, restored and compared by SHA-256 where a result is given. Every file was parsed tensor by tensor;
none fell back to plain bytes. Not checked: whether an engine loads what was restored (that is the engine's part).

| scheme | what its files hold | checked on (revision) | stored |
|---|---|---|---|
| MXFP4 | U8 packed weights, BF16 | openai/gpt-oss-20b (`6cee5e81`), shard `model-00000-of-00002` | 89.5 % (the packed U8 ≈ 93 %\*) |
| | I8 packed weights, F8_E8M0 scales, F8_E4M3, BF16, F32 | deepseek-ai/DeepSeek-V4.1-Flash-1017 (`1bae0e10`) | headers only |
| NVFP4 | U8 packed weights, F8_E4M3 scales, BF16, F32 | nvidia/Qwen3-8B-NVFP4 (`ccd10a89`), shard 2 of 2 | 69.1 %, mostly BF16 (the packed U8 ≈ 90 %\*) |
| | the same, with F8_E8M0 and I8 | nvidia/DeepSeek-V4.1-Flash-NVFP4 (`c48dca01`) | headers only |
| FP8 (E4M3, 128×128 block scales) | F8_E4M3, BF16 | Qwen/Qwen3.8-Flash-Next-FP8 (`236dfdf2`), 5 shards | 76.5 % (F8_E4M3 81.9 %) |
| | | its fine-tune bottlecapai/ThinkingCap-Qwen3.8-Flash-Next-FP8 (`ca9f7a5c`), last shard, as a delta | **0.27 %** (its FP8 experts unchanged: [benchmarks](benchmarks.md)) |
| | | Qwen/Qwen3-8B-FP8 (`220b46e3`) | headers only |
| EXL3 (3.05 bpw) | I16 trellis, F16, BF16, I32 | doth4580/Qwen3.8-Flash-Next-EXL3-3.05bpw (`2f41a009`), last shard and a sidecar | 96.3 % (the I16 ≈ 99.5 %\*) |
| GGUF with an engine's own types | ggml types 1010–1012 (dsx T16), IQ4_NL, Q8_0, F16, F32 | lxe/DeepSeek-V4.1-Flash-1017-dsx-t16 (`8bf76fe6`) | headers only: the unknown types are kept as opaque regions |
| | IQ2_XXS, Q2_K, Q4_K, Q8_0, MXFP4, BF16 | antirez/Qwen3.8-Flash-Next-GGUF (`d600fe1a`), Q2 | headers only |

\* estimated from the file's ratio and its BF16 part (≈ 66 %).

Packed 4-bit data stores at about 90–93 % with zstd and EXL3's trellis hardly at all, as quantized data does
(GGUF: 95–97 %); a codec of its own would gain too little to be worth one. Deltas pair tensors by name: a fine-tune
quantized by another exporter may name them differently (an llm-compressor NVFP4 fine-tune of Qwen3-8B against
NVIDIA's release: 46 % of its bytes line up), and is better stored against a base from the same exporter.

## Deltas between related models

Each file is matched tensor by tensor against every file of its base, which it picks among the models already in
the pack (or `--base` names):

| supported | example | measured |
|---|---|---|
| same layout | official Base → Instruct | 7B: 30 % |
| different file names / headers | GGUF from two uploaders | Instruct BF16 GGUF: 44 % |
| resharded or reordered | 1 file → 2 shards, reversed order | identical to the unsharded delta |
| other float dtype | F16 GGUF vs BF16 base, BF16 vs F32 | F16 Instruct GGUF: 44 % (was 67 %) |
| vocabulary extended or trimmed | DeepSeek-R1 draft model (+128 rows) | 30 % (was 40 %) |
| quantized tensors | Q4_K_M Instruct vs Q4_K_M Base | 65 % (was 97 %) |
| identical files anywhere in the pack | shared tokenizer, re-uploads | stored once |
| base chosen automatically | Gensyn fine-tune → Instruct, DeepSeek draft → Base | same base as picked by hand, every time |
| base re-chosen later (`rebase`), without the original folder | Gensyn fine-tune packed before Instruct | 66 % → 1.45 % (30 s) |

Not supported yet:

- **Cross-format** matching (a GGUF against a safetensors base): tensor names differ (`blk.0.attn_q.weight` vs
  `model.layers.0.self_attn.q_proj.weight`) and some architectures permute weights during conversion.
- **Partial rows of quantized tensors** (row boundaries are not byte-aligned in general).
- Models whose hidden size, layer count or head layout changed (pruned or depth-upscaled models): only tensors with
  matching names and trailing shapes can share data.
- Planning the bases of the whole pack at once: `rebase` re-chooses one model at a time and never codes a file
  against one that needs it. So a fine-tune cannot move onto an ancestor packed after it while that ancestor is a
  delta against the fine-tune (which automatic choice usually makes it); the pack is about as small either way, and
  `rebase <pack> <ancestor> --no-base` first frees the ancestor (see [usage](usage.md#2-how-the-base-is-chosen)).

## Requirements and limits

| item | value |
|---|---|
| Python | CPython 3.13, x86-64 (a portable copy brings its own: see the build guides), with numpy and zstandard: prebuilt wheels, no compiler |
| OS | Windows 10/11 (tested end to end); Linux x86-64 with glibc 2.27 or later, what the pinned numpy wheel needs (build guide provided; checked by a downstream team) |
| RAM | ≈ 12 GB committed at peak for a 7B delta with 16 ans16 worker processes, ≈ 2.5 GB without deltas; `OC_WORKERS` lowers it |
| temporary disk | the base files a delta reads are restored to `OC_TMP` (default: system temp), e.g. one 3.7 GB shard per 7B shard; `rebase` also restores the file it re-packs there |
| speed (8 cores) | pack 7B base ≈ 1.5 min, 7B Instruct delta ≈ 5.5 min; restore 7B base ≈ 30 s, 7B Instruct ≈ 2.7 min |
| GPU | not used |

## What it is not

- Not a quantizer: everything is lossless, bit-for-bit.
- Not a model loader or inference runtime: restore the files, then use them with your usual tools.
- Not a backup or repair system: it detects damage; see [protection.md](protection.md) for copies and repair data.
