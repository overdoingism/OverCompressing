# How it works

> **中文重點**
> - 一般壓縮器（zip、zstd）壓不動權重，是因為 BF16 的 16 個位元裡，7 位尾數幾乎是純雜訊；真正可壓的只有分佈很集中的指數。
> - 單一模型：逐 tensor 解析檔案，浮點 tensor 拆成位元平面（讓指數集中成一條串流，這是 ZipNN 提出的作法），再用 zlib 的 Huffman 編碼，實測 66.4%，距離理論下限 66.2% 只差 0.2 個百分點。整個工具只用 NumPy、zlib、zstd，沒有自己的編譯程式碼。
> - 同家族模型：把微調模型存成「和 base 的差」。每個權重的殘差用 ans16（自寫的 rANS 熵編碼器）壓縮，並用 base 權重的指數當上下文；base 本身就是預測器，不需要另外訓練網路。
> - 依 tensor 名稱配對，所以檔名、分片、順序、dtype（BF16/F16/F32）、詞彙表大小不同都能共用。
> - base 自動挑選：每個檔案對歸檔中所有可配對的模型抽樣試算，選壓得最小的；也可用 `--base` 指定。
> - 每一層都有 SHA-256 / CRC 驗證；打包時一定從磁碟解回比對才算完成。修復交給外部方案（見 protection.md）。

## 1. Why generic compressors fail on weights

A BF16 weight is 1 sign bit, 8 exponent bits and 7 mantissa bits. Trained weights are small numbers, so the exponent
takes only a few dozen values, while the mantissa is close to random. Measured on Qwen2.5-0.5B (494 M weights):

| field | bits stored | entropy (bits) |
|---|---|---|
| sign | 1 | 1.000 |
| exponent | 8 | 2.639 (only 38 distinct values) |
| mantissa | 7 | 6.957 |
| **total** | **16** | **10.596 → floor ≈ 66.2 %** |

Byte-oriented compressors see the exponent split across two bytes and mixed with mantissa noise: `zstd -19` only
reaches 75.9 %. No coder can beat the entropy of the mantissa, so ~66 % is the practical limit for one BF16 model on
its own.

## 2. One model: per-tensor coding

safetensors headers and GGUF tensor tables are parsed, and every tensor becomes its own region:

- **float tensors** (BF16, F16, F32, FP8) → **byte planes**: the bits of each weight are regrouped so that the
  exponents form a byte stream of their own, the byte grouping proposed by [ZipNN](https://github.com/zipnn/zipnn)
  (IBM). That plane is entropy-coded with DEFLATE in Huffman-only mode (zlib); the sign and mantissa planes, close to
  random, are kept as they are or zstd-coded when that helps. Qwen2.5-0.5B: **66.4 %**, 0.2 points above the floor.
- **quantized and integer tensors** (GGUF Q4_K, Q8_0, …; int8) and metadata → zstd. Quantized data is already near
  its entropy, so expect 95–97 %.
- **unknown binaries** (PyTorch `.bin`, …) → 64 MiB chunks, each stored with whichever of BF16 byte planes and zstd
  is smaller.

Only the entropy coding of the exponents matters: the LZ matching of a general compressor finds nothing to reuse in
them (zstd at fast levels is 3–4 points worse than plain Huffman coding). ZipNN's own code is not used: the byte
planes are a few NumPy bit operations, and zlib and zstd do the coding, so the tool has no compiled code of its own
([benchmarks](benchmarks.md#the-float-codec-byte-planes-vs-zipnn) compare the two). The gains beyond this come from
families of models.

## 3. Families: fine-tunes as deltas (ans16)

A fine-tune starts from a base model and moves each weight a little. For a 16-bit tensor present in both:

1. **Residual.** `r = (target_bits − base_bits) mod 2^16` per weight. Small moves give small residuals; untouched
   weights give 0.
2. **Context.** The residual distribution depends on the base weight's magnitude, so weights are grouped by the base
   weight's exponent (up to 256 groups, rare ones merged). The decoder recomputes these groups from the base alone,
   so nothing about them is stored.
3. **Entropy coding.** Each group has its own integer frequency table (summing to 2^24), and residuals are coded with
   an interleaved rANS coder (64-bit state, 32-bit words, many lanes, vectorized in NumPy). Integer tables make the
   output independent of floating-point behaviour or any library's quantizer.

Entropy of the Qwen2.5-0.5B-Instruct delta against its base, in bits per weight (and % of BF16):

| representation | one table | per tensor | tensor + base exponent |
|---|---|---|---|
| no delta | 10.57 (66.1 %) | 10.54 (65.9 %) | 8.76 (54.8 %) |
| XOR | 8.10 (50.6 %) | 8.09 (50.6 %) | 7.47 (46.7 %) |
| **SUB (what ans16 codes)** | 7.67 (47.9 %) | 7.66 (47.9 %) | **7.02 (43.9 %)** |

ans16 reaches **44.2 %** in practice (the XOR with the base, in byte planes: 53.6 %). Light fine-tunes, where most weights did not move,
shrink to a few percent (see [benchmarks](benchmarks.md)).

This is the "compress a model with a model" idea in its useful form: instead of training a network to predict the
weights, the **base model itself is the predictor**, and only what it fails to predict is stored.

## 4. Matching tensors, not files

Deltas are planned per tensor. The manifest of every archived file records its tensor table, and each tensor of a
new file is looked up in **all** files of the base repo by name:

| match | when | coded as |
|---|---|---|
| exact | same name, dtype and size | ans16 (16-bit floats) or the XOR with the base, in byte planes or zstd |
| converted | same name and element count, other float dtype (BF16 ↔ F16, F32 → BF16/F16) | base values converted with IEEE round-to-nearest-even, then as above |
| leading rows | same name, same shape except the first dimension (vocabulary extended or trimmed) | shared rows as above, extra rows standalone |

So a delta still works across different file names, header sizes, shardings, tensor orders, float dtypes and
vocabulary sizes. A region is also coded standalone whenever its delta is not clearly smaller (every XOR delta, an
ans16 delta above half the original size), and the smaller is kept, so a weak base costs nothing.

### Which base

Unless `--base` names one, each file chooses its own base. The candidates are the packed models whose manifests
list tensors that match this file's by the rules above, so finding them decompresses nothing. Then 16 windows of
512 KiB, spread over the file's tensor bytes, are coded against each candidate with fast estimates (the empirical
entropy of the ans16 residual given its context, or zstd of the XOR) and compared with an estimate of storing them
standalone (for floats, the entropy of each byte plane). Only the regions of a candidate that the windows touch are
decoded, through its own chain of deltas, and an ans16 region only up to the window's end. All candidates are scored
on 4 windows, the best 4 on all 16. The smallest estimate wins if it saves at least 1 % of the standalone size.
A blob is never coded against one that needs it (directly or through others), so no cycle can form.

## 5. Identical files

Every file is stored once, as a blob named by its SHA-256: a file already in the pack (a shared tokenizer, a
re-uploaded GGUF, a config two models have) is not stored again, only listed in the new model's manifest. A delta
blob names the blobs it is coded against, so what needs what is a graph that grows as models are packed; removing a
model deletes only the blobs no remaining model needs, directly or as a base.

## 6. Integrity

- Every file's SHA-256 is recorded when it is packed and checked again after restoring (compare it with the
  hashes your source publishes, e.g. Hugging Face's, if you need provenance).
- `pack` finishes a file only after decoding it back **from disk** to the same SHA-256; a failed ans16 delta is
  redone as an XOR delta.
- Every blob ends with its own SHA-256, every frame has a CRC-32, every ans16 block a self-check. `verify` and
  `restore` check the whole chain of bases before decoding, and refuse cycles.
- Repair is not built in: damage is reported precisely, and [protection.md](protection.md) compares ways to make
  it repairable (copies, PAR2, self-healing file systems, …).

## 7. Why not "AI compression with residuals"

A learned predictor plus stored residuals saves about `log2(σ_weights / σ_error)` bits per weight. Trained weights
behave like noise below the exponent, so no network predicts the low mantissa bits, and saving 2 bits per weight
would need the predictor to explain ~94 % of the variance. The one predictor that does work is a sibling model,
which is exactly what the delta coder uses.

The byte-level format, including how to decode ans16 by hand, is in [ARCHIVE_FORMAT.md](../tools/ARCHIVE_FORMAT.md).
