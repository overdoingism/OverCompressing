# Scope: what it handles

> **中文重點**
> - 格式：safetensors、GGUF（v2/v3）逐 tensor 處理；PyTorch `.bin`/`.pth` 等其他二進位檔自動在位元平面與 zstd 間擇優；文字檔用 zstd。
> - 能大幅壓縮的是 BF16/F16/F32/FP8 浮點權重（約 66%）；量化檔（GGUF Q4/Q8、GPTQ…）本身已接近極限（95–97%），但同家族之間仍可用 delta 省下不少。
> - 同家族 delta：名稱相同的 tensor 自動配對，可跨檔名、分片、順序、dtype、詞彙表大小；base 也會自動挑選，之後加入更近的祖先時可用 `rebase` 重新挑選（不需要原資料夾）。
> - 尚不支援：GGUF 與 safetensors 之間的跨格式配對、量化 tensor 的部分列配對、隱藏層寬度改變的模型、整個歸檔一起最佳化的 base 規劃（`rebase` 一次處理一個模型）。
> - 環境：CPython 3.13（x86-64）。Windows 已完整實測；Linux 流程尚未實機測試。

## File formats

| format | how it is split | float tensors | other tensors / data |
|---|---|---|---|
| **safetensors** | JSON header | byte planes (BF16, F16, F32, F8_E4M3, F8_E5M2) | zstd (I8, U8, I64, BOOL, …) |
| **GGUF** v2/v3 | tensor table (alignment honoured) | byte planes (F32, F16, BF16) | zstd (all quantized types: Q4_K, Q8_0, IQ…, MXFP4) |
| PyTorch `.bin` / `.pth` / `.ckpt`, ONNX, anything else | 64 MiB chunks (`auto`) | — | per chunk: smaller of BF16 byte planes and zstd |
| text (`.json`, `.txt`, `.md`, `.model`, `.jinja`, …) | 64 MiB chunks | — | zstd -19 |
| files ≤ 1 MiB | — | — | stored as is |

Pickle files are only read as bytes; nothing is ever unpickled or executed. A file whose header does not parse falls
back to `auto`, so malformed or unknown files are still stored losslessly.

Typical ratios on their own (see [benchmarks](benchmarks.md)): BF16/F16 ≈ 66 %, FP8 ≈ 80–85 %,
quantized GGUF ≈ 95–97 %, tokenizer JSON ≈ 15–25 %.

## Deltas between related models

Each file is matched tensor by tensor against every file of its base, which it picks among the models already in
the archive (or `--base` names):

| supported | example | measured |
|---|---|---|
| same layout | official Base → Instruct | 7B: 30 % |
| different file names / headers | GGUF from two uploaders | Instruct BF16 GGUF: 44 % |
| resharded or reordered | 1 file → 2 shards, reversed order | identical to the unsharded delta |
| other float dtype | F16 GGUF vs BF16 base, BF16 vs F32 | F16 Instruct GGUF: 44 % (was 67 %) |
| vocabulary extended or trimmed | DeepSeek-R1 draft model (+128 rows) | 30 % (was 40 %) |
| quantized tensors | Q4_K_M Instruct vs Q4_K_M Base | 65 % (was 97 %) |
| identical files anywhere in the archive | shared tokenizer, re-uploads | stored once |
| base chosen automatically | Gensyn fine-tune → Instruct, DeepSeek draft → Base | same base as picked by hand, every time |
| base re-chosen later (`rebase`), without the original folder | Gensyn fine-tune packed before Instruct | 66 % → 1.45 % (30 s) |

Not supported yet:

- **Cross-format** matching (a GGUF against a safetensors base): tensor names differ (`blk.0.attn_q.weight` vs
  `model.layers.0.self_attn.q_proj.weight`) and some architectures permute weights during conversion.
- **Partial rows of quantized tensors** (row boundaries are not byte-aligned in general).
- Models whose hidden size, layer count or head layout changed (pruned or depth-upscaled models): only tensors with
  matching names and trailing shapes can share data.
- Planning the bases of the whole archive at once: `rebase` re-chooses one model at a time and never lets a model
  use one that needs it. So a fine-tune cannot move onto an ancestor packed after it while that ancestor is a delta
  against the fine-tune (which automatic choice usually makes it); the archive is about as small either way, and
  `rebase <ancestor> --no-base --no-dedupe` first frees the ancestor (see [usage](usage.md#2-how-the-base-is-chosen)).

## Requirements and limits

| item | value |
|---|---|
| Python | CPython 3.13, x86-64 (the offline kit bundles it), with numpy and zstandard: prebuilt wheels, no compiler |
| OS | Windows 10/11 (tested end to end); Linux glibc x86-64 (scripts provided, not yet tested on a real machine) |
| RAM | ≈ 4 GB peak with 16 worker processes on a 7B model; `OC_WORKERS` lowers it |
| temporary disk | the base files a delta reads are restored to `OC_TMP` (default: system temp), e.g. one 3.7 GB shard per 7B shard; `rebase` also restores the file it re-packs there |
| speed (8 cores) | pack 7B base ≈ 3 min, 7B Instruct delta ≈ 5.5 min; restore 7B base ≈ 1 min, 7B Instruct ≈ 3 min |
| GPU | not used |

## What it is not

- Not a quantizer: everything is lossless, bit-for-bit.
- Not a model loader or inference runtime: restore the files, then use them with your usual tools.
- Not a backup or repair system: it detects damage; see [protection.md](protection.md) for copies and repair data.
