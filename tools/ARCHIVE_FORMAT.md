# OverCompressing pack format, version 4

Written by `llm_archive.py` (tool version 5). This spec is enough to restore the data without the tool: you need a
JSON reader, a DEFLATE decoder (zlib, RFC 1951), a zstd decoder, SHA-256, a few bit operations (byte planes), and for
ans16 deltas the rANS decoder described below.

## Layout

A pack is a folder (a *pack folder*) or the same tree in one ZIP file (a *pack file*, `.oczip`, below):

```
<pack>/
  ocpack.json               {"format": "overcompressing-pack", "format_version": 4, "changed": "<time>"}: what
                            makes the folder a pack; rewritten last by every change (pack, remove, rename, rebase,
                            export --delete), with `changed` the UTC time in ISO 8601, so its time stamp tells a
                            watcher once per complete change (readers check only `format` and `format_version`)
  models/
    <Org__Name>.json        one manifest per model; "/" in the model's name becomes "__" (Qwen/Qwen2.5-7B ->
                            models/Qwen__Qwen2.5-7B.json)
  blobs/
    <ab>/<sha256>.ocz       one blob per distinct file: an OCZ4 container named by the SHA-256 of the ORIGINAL
                            file (64 lowercase hex digits; <ab> is its first two), shared by every model that has
                            that file
  _kit/                     optional: a portable copy of the tool (its Python and tools), which the tool never
                            reads; export carries it into a pack file
  .overcompressing.lock     empty; the tools lock it while they run (pack, remove, rename, rebase and
                            export --delete alone; list, verify, restore and export together; readers skip it only
                            on a read-only volume or with --no-lock)
```

To restore a model: read its manifest; for each file, decode `blobs/<ab>/<sha256>.ocz` (its `sha256`), which gives
back the original bytes, and check them against `size` and `sha256`.

A blob refers to nothing but the blobs it is coded against (its bases, by SHA-256), and a manifest to nothing but its
files' blobs. Nothing records which models need which blobs: that follows from the manifests and the blobs' headers.
A blob that no manifest needs, directly or through the bases of blobs that are needed, is garbage; `pack`, `remove`
and `rebase` delete such blobs (never while a manifest or a needed blob cannot be read). A blob a removed model
listed stays as long as another model's blob is coded against it.

Files the tool did not write (your notes, recovery data) may lie in the pack folder; the tool never touches them,
and `export` leaves them out. Temporary files are `.oc-*.part`.

### Pack file (`.oczip`)

A ZIP archive (ZIP64 where needed) whose member names are the paths above, relative to the pack, with `/`
separators and UTF-8 names: `ocpack.json`, `models/...`, `blobs/...` and optionally `_kit/...`. Every blob is stored
with method 0 (no compression, no encryption), so it can be read in place at its offset: the member's local header
offset plus 30 plus the lengths of its name and extra field. A blob's local header names it as the central directory
does, with method 0 and no encryption flag there too (else readers that go by one or the other disagree: refused).
Other members are stored or Deflate-compressed
(method 8). No name appears twice and none has an empty, `.` or `..` part. A pack file is written whole and never
changed; extracting it with any ZIP tool gives a pack folder.

## Model manifest (`models/<Org__Name>.json`)

| field | meaning |
|---|---|
| `format`, `format_version` | `"overcompressing-model"`, `4` |
| `name`, `revision` | the model's name in the pack (`--name`), and an optional free-text note (`--revision`) |
| `tool`, `created` | the tool that wrote the manifest, its version and build (e.g. `llm_archive 0.5 (2026-10-22 1a2b3c4)`); UTC time of the pack |
| `base_choice` | how the bases were chosen: `auto`, `forced` (`--base`) or `none` (`--no-base`); `rebase --all` re-chooses only `auto`; if absent, read as `auto`. Not needed to restore |
| `files[].path` | the file's path inside the model folder it was packed from |
| `files[].size`, `sha256` | size and SHA-256 of the **original** file; the SHA-256 names its blob |
| `files[].base_choice` | optional: a choice made for this file alone (`pack --add`, `rebase --file`), as `base_choice` above; if absent, the model's |
| `files[].format`, `tensors` | for parsed safetensors/GGUF files: `[name, dtype, start, end, shape]` of every tensor (absolute byte offsets; shape outermost dimension first, also for GGUF). Used to choose bases, not to restore |

The manifest's file name must be `repo_dir_name(name) + ".json"`. Two names that would give one file name where case
is ignored are not allowed in one pack.

## OCZ4 container (a blob)

All integers are little-endian.

```
"OCZ4"                       4 bytes magic
u32 header_len
header_len bytes of JSON:    {"original_size", "sha256", "method", "regions": [...], "bases": [...]}
one frame per region, in order:
  u8  codec                  low 7 bits: 0 = raw, 1 = byte planes, 2 = zstd, 3 = ans16; bit 7 (0x80) = STANDALONE
  u64 raw_len                bytes this frame restores to (= region length)
  u64 payload_len
  u32 crc32                  zlib CRC-32 of the restored ORIGINAL bytes of the region
  payload_len bytes
32 bytes                     SHA-256 of every byte of the blob before these 32
```

`original_size` and `sha256` are the original file's (the blob's name). `method` is for people only (`safetensors`,
`gguf`, `zstd` for text, `auto` for other bytes, with `+ans16` / `+xor` for a delta). `bases` lists the blobs this one
is coded against: `[{"sha256", "size", "model"?, "path"?}]`, `sha256` and `size` being the base file's; `model` and
`path` say where that file came from when this blob was written, for people only (that model may have been renamed
or removed since). `regions` covers the file in order without gaps; each is `{"offset", "length", "kind", "dtype"?,
"base"?, "base_dtype"?}`.

### Partial blobs

A blob that no model lists any more but other blobs are coded against (kept for them after its model was removed) may
be **partial**: its header has `"partial": true`, and each region that no blob reads is marked `"dropped": true` and has
no frame (the frames of the other regions follow in order, as they were). A partial blob does not restore its file; it
gives the bytes of its kept regions to the blobs coded against it, which read only those. Restoring it as a base for
one of them, take the dropped regions as zeros (each kept region still checks against its CRC-32). Everything else is
as for any blob: its name is still its file's SHA-256, it ends with the SHA-256 of its bytes. A file a manifest lists
never has a partial blob: storing the file again replaces it with a whole one.

### Standalone coding

| kind | contents | frame codecs |
|---|---|---|
| `float` | a float tensor, `dtype` one of BF16, F16, F32, F8_E4M3, F8_E5M2 | 1 (byte planes of that dtype), else 2 or 0 |
| `zstd` | header, metadata, text; with a `dtype` tag: a quantized/integer tensor | 2, else 0 |
| `auto` | unknown binary | 1 (byte planes as BF16) or 2 or 0, whichever was smallest |

Codec 0: the payload is the data. Codec 2: one standard zstd frame. Codec 1: byte planes, below.

### Byte planes (codec 1)

For a region of dtype `d` (the region's `dtype`; BF16 for kind `auto`) with element size `s` (BF16, F16: 2; F32: 4;
F8_E4M3, F8_E5M2: 1), the `n = raw_len // s` whole elements (little-endian unsigned integers `u`) are split into
byte planes, one byte per element each, so that the exponents, the only part of a weight with little information,
sit together:

| dtype | planes |
|---|---|
| BF16 | `p0 = (u >> 7) & 0xFF` (exponent); `p1 = ((u >> 8) & 0x80) \| (u & 0x7F)` (sign, mantissa) |
| F16 | `p0 = u >> 8` (sign, exponent, 2 mantissa bits); `p1 = u & 0xFF` |
| F32 | `p0 = (u >> 23) & 0xFF` (exponent); `p1 = ((u >> 24) & 0x80) \| ((u >> 16) & 0x7F)`; `p2 = (u >> 8) & 0xFF`; `p3 = u & 0xFF` |
| F8_E4M3, F8_E5M2 | `p0` = the byte |

The `raw_len % s` bytes after the last whole element form a tail stream. The payload is:

```
u8  count                         streams: the planes, plus 1 if there is a tail
count x { u8 coding, u64 length } coding 0 = stored, 1 = raw DEFLATE (RFC 1951, no zlib header), 2 = one zstd frame
the coded streams, in order       each plane decodes to exactly n bytes, the tail (stored) to raw_len % s bytes
```

Rebuild each element from its plane bytes (BF16: `((p1 & 0x80) << 8) | (p0 << 7) | (p1 & 0x7F)`; F16:
`(p0 << 8) | p1`; F32: `((p1 & 0x80) << 24) | (p0 << 23) | ((p1 & 0x7F) << 16) | (p2 << 8) | p3`), write the elements
little-endian, then the tail. A decoder must refuse a payload whose stream count, lengths or decoded sizes do not
fit exactly. The writer codes each plane with whichever is smallest: DEFLATE with Huffman coding only (as zlib's
`Z_HUFFMAN_ONLY` produces; it comes close to the order-0 entropy of the exponent plane), a zstd frame, or the plane
as it is. The byte-grouping idea is ZipNN's (Hershcovitch et al., 2024); this coding of it is this format's own.

How files are split: safetensors by its JSON header; GGUF (v2/v3) by its tensor table (F32/F16/BF16 tensors are
float, quantized ones are tagged `ggml_type_<n>` and run up to the next tensor); anything else in 64 MiB `auto`
chunks (text files: `zstd`). Regions longer than 64 MiB are cut into 64 MiB pieces.

### Delta coding (`bases` is not empty)

Tensors of the file that also exist in the base model, in any of its files, become regions with
`"base": [i, offset]`: their base bytes are at `offset` in the file of `bases[i]`. A tensor matches a base tensor
with the same name and either the same dtype and byte size, the same element count in another float dtype (see
`base_dtype` below), or, for float tensors, the same shape except the first dimension: then only the leading rows
both have are coded against the base (e.g. an embedding after vocabulary extension), and the target's extra rows
form a following standalone region. Restore each base file first (from its own blob, which may itself be a delta)
and check its SHA-256 against `bases[i].sha256`. Then, per region:

- no `base` key: decode standalone as above.
- `base` and the STANDALONE flag set: decode standalone as above (storing it without the base was smaller).
- kind `ans16`, codec 3: decode the ans16 payload below against the base bytes (`length / 2` weights at `offset`).
- otherwise (kind `xor`, or an `ans16` region that did not shrink): decode as above with the region's dtype, then XOR
  with the `length` base bytes at `offset`.

Because matching is by tensor, a delta works across different file names, header sizes, shardings, tensor
orders, float dtypes and vocabulary sizes. **A delta blob is unrecoverable without the blobs it lists as bases**;
the tool keeps them as long as it is needed. A chain of bases is at most 32 blobs deep and never a cycle.

#### Cross-dtype prediction (`base_dtype`)

A BF16/F16 tensor may be matched to a base tensor of another float dtype (BF16, F16 or F32) with the same name and
element count; the region then has `"base_dtype"`. Its base values (`length / 2` elements of `base_dtype` at
`offset`; an F32 base advances 4 bytes per element when a region is split) are converted to the region's `dtype`
and that converted array takes the place of the base bytes, for ans16 as well as for XOR. The conversion must be
reproduced exactly:

1. To float32 (exact): BF16 `b` -> bits `b << 16`; F16 -> its IEEE binary16 value; F32 as is.
2. To the target: IEEE 754 round-to-nearest-even, overflow to infinity, subnormals as IEEE (to F16: the standard
   binary32 -> binary16 conversion; to BF16: `(u + 0x7FFF + ((u >> 16) & 1)) >> 16` on the float32 bits `u`).
3. Any NaN input becomes `0x7E00` (F16) or `0x7FC0` (BF16).

BF16 -> F16 is exact for values in the F16 normal range, so an F16 export of BF16 weights predicts perfectly.

### ans16 payload (codec 3)

Covers `n = length / 2` 16-bit weights. Read `base[i]` and produce `target[i]` (unsigned 16-bit, little-endian).

```
u32 prec, u32 min_context, u64 n, u32 lanes, u32 K, u32 tables_zlen, u32 counts_zlen, u32 nwords
tables_zlen bytes   zstd of: u16 ctx_id[K], u32 k[K], u16 sym_delta[sum k], u32 freq[sum k]
u64 state[lanes]
counts_zlen bytes   zstd of: u32 words_in_lane[lanes]
u32 words[nwords]   lane 0's words, then lane 1's, ... each in reading order
```

1. **Contexts.** `e = (base >> 7) & 0xFF` for BF16, `(base >> 10) & 0x1F` for F16. Count how often each `e`
   occurs in this region; weights whose `e` occurs fewer than `min_context` times get context 256 instead.
2. **Tables.** Context `ctx_id[j]` (ascending) owns `k[j]` entries. Within a context, the symbols are the running
   sum of `sym_delta` (restarting at each context) and are ascending; `freq` sums to exactly `2^prec` per context.
   `cum` of an entry = sum of the `freq` before it in the same context.
3. **Decode** (rANS, 64-bit state, 32-bit words, `L = 2^31`). Weight `i` belongs to lane `i % lanes`. Each lane
   starts with `x = state[lane]` and a read pointer at the start of its words. For `i = 0 .. n-1` in order, with
   `x` = that lane's state: `slot = x mod 2^prec`; find the entry of this weight's context with
   `cum <= slot < cum + freq`; `x = freq * (x >> prec) + slot - cum`; if `x < L`, `x = (x << 32) | next word of the lane`.
   The entry's symbol `r` gives `target[i] = (base[i] + r) mod 2^16`.
4. **Check.** Afterwards every lane is back to `x = L` and has consumed exactly its words.

Encoding is the exact inverse (symbols in reverse order; emit the low 32 bits and shift right by 32 while
`x >= freq << (63 - prec)`; then `x = ((x // freq) << prec) + x % freq + cum`).

## Integrity

- Every blob ends with its own SHA-256 (detects damage before decoding, with no manifest needed).
- A partial blob (kept only as a base) is checked through what reads it: every region a blob coded against it reads
  must be kept, and those blobs' files restore exactly.
- Every frame has a CRC-32 of the original bytes (localizes damage); ans16 payloads also self-check (step 4).
- Every original file has its `sha256`, which is its blob's name (proves the restore is exact).
- `pack` and `rebase` write a blob to a temporary `.oc-*.part` file and decode it back from there to the original
  SHA-256 before it takes its name; a failed ans16 delta is redone as an XOR delta. In a pack file, blobs are decoded
  back from the unfinished file the same way. `verify` and `restore` check the blob, and every blob it is coded
  against, before decoding, and refuse cycles and chains deeper than 32.
- A file already in the pack (by SHA-256) is not stored again; if its blob is damaged, the next pack of that file
  stores it again.
- A manifest is replaced in one step (written to a temporary file, then renamed over the old one), last: after its
  blobs are in place and before blobs it no longer needs are deleted, so the pack is complete at every moment.
  `rebase` replaces a blob by a new one that restores the same file, so nothing that needs it changes.
- Names and paths in a manifest are checked before they are used. A model name is one or more parts separated by
  `/`, each of letters, digits, `-_.+@()[]` and spaces, not empty, not starting or ending with a space, not ending
  with a dot and not a Windows device name. A file path (`path`) is relative, uses `/`, and has no empty, `.` or `..`
  part and no NUL; it may hold any other character. `restore` refuses paths that would land outside its destination
  and never writes through a link (a symlink or junction below the destination stops it), writes a name its system
  does not allow with `_` for each character it does not allow (on Windows: `<>:"\|?*`, control characters, trailing
  dots and spaces; device names get `_` after the stem), and keeps two paths that would be one file apart with
  ` (1)`, ` (2)`, ... before the extension of the later one (paths that needed no change keep their names).
- Every manifest is checked before use: this format and version; a valid `name` matching its file name; for every
  entry a `path`, `size` (a whole number of at least 0) and `sha256` (64 lowercase hex digits), and for a parsed file
  a known `format` and a tensor table inside the file; no path listed twice. A manifest that fails is reported by
  `verify` and blocks `pack`, `remove`, `rename` and `rebase` until it is fixed (its model's blobs would otherwise
  look unused).
- Every blob is checked before anything in it is decoded: its trailing SHA-256; magic; a header of at most 64 MiB that
  is valid UTF-8 JSON without duplicate keys, `NaN` or `Infinity`, naming the file the blob is named after; at most
  4,194,304 regions, each 1 byte to 1 GiB long, with whole-number offsets and lengths (not `true`, not `0.0`),
  covering `original_size` in order without gaps, of a known kind, with base references only on delta regions, only
  into `bases` and only to bytes inside the base file; one frame per region whose `raw_len` is the region's length
  and whose codec fits the region; every payload inside the blob; nothing after the last frame but the SHA-256. A
  zstd payload must be exactly one frame, its declared content size no larger than its region, and it is decoded
  with output capped at that size. An ans16 payload's lengths must add up exactly, its header fields stay in range
  (precision 1–30, 1–65,536 lanes, 1–257 contexts) and its tables (at most 65,536 symbols per context, frequencies
  summing to `2^prec`) fit. Manifests and safetensors headers with duplicate JSON keys, `NaN` or `Infinity` are
  refused too; a GGUF file whose sizes overflow, whose tensor names are not valid UTF-8 or repeat, is stored as plain
  bytes.
- Every file a tool writes goes first to a new, randomly named temporary file (`.oc-*.part`) created exclusively, and
  is put in place only after it is synced to disk. A new name is taken only if nothing is there, in one step (a hard
  link, then the temporary name removed; where the file system has no hard links, a rename that refuses an existing
  name; with neither, the tool stops rather than check first and rename after). Links below the pack folder and the
  restore destination are never followed (on Windows: checked as a best effort).
- Repair data is not part of the format: bit-level damage is for the hardware and the file system (RAID, ECC,
  ZFS/Btrfs/ReFS) or copies. See `docs/protection.md`.
