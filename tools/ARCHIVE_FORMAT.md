# OverCompressing archive format, version 3

Written by `llm_archive.py` (tool version 4). This spec is enough to restore the data without the tool: you need a
DEFLATE decoder (zlib, RFC 1951), a zstd decoder, SHA-256, a few bit operations (byte planes), and for ans16 deltas
the rANS decoder described below.

## Layout

```
<pack_folder>/
  <name>/                   one directory per packed model folder ("/" in the name becomes "__"); it must hold that
                            model only: the manifest's `repo` must map to the directory's name
    manifest.json           never the name of a stored file (a source file called manifest.json is stored as
                            manifest.json.1, …); no stored file name ends in .part, the suffix of temporary files
    <path>                  small non-weight files (<= 1 MiB), stored byte-for-byte; after a re-pack that changed
                            one, the name may be <path>.1
    <path>.ocz              every other file, in an OCZ3 container (files identical to one stored elsewhere in the
                            archive are not stored again: their manifest entry points at it); after a re-pack or a
                            rebase the name may be <path>.1.ocz: always use `stored` from the manifest
                            Stored names follow the original folder tree, each name made valid on Windows and
                            POSIX. Where a file and a folder would get one name (also when they differ only in
                            case, or while a re-pack keeps the old version's files), the later one gets .1, .2, ...
                            (a folder too: a_.1/x.txt.ocz). A folder left empty by a re-pack is removed.
  _kit/                     optional offline restore kit (tools, wheels, Python, docs)
  .overcompressing.lock     empty; the tools lock it while they run (pack/rebase alone, verify/restore together;
                            readers skip it only on a read-only volume or with --no-lock)
```

### Pack file (`.oczip`)

The `bundle` command puts a whole `<pack_folder>` into one file: a ZIP archive (ZIP64 where needed) whose member
names are the paths relative to `<pack_folder>`, with `/` separators and UTF-8 names. Every container (`*.ocz`, and
every `stored` name of a container in a manifest) is stored with method 0 (no compression, no encryption), so it can
be read in place at its offset: the member's local header offset plus 30 plus the lengths of its name and extra field.
Other members are stored or Deflate-compressed (method 8). The lock file and `.oc-*.part` files are not put in; no name
appears twice and none has an empty, `.` or `..` part. A model's manifest is `<folder>/manifest.json`, exactly as in
the folder. Extracting the ZIP with any tool gives back the pack folder, byte for byte.

## manifest.json

| field | meaning |
|---|---|
| `format`, `format_version` | `"overcompressing-archive"`, `3` |
| `repo`, `revision` | the model's name in the archive (`--name`), and an optional free-text note (`--revision`) |
| `tool`, `created`, `rebased` | tool version that wrote the manifest; UTC time of the pack, and of the last `rebase` that changed the model (absent if none) |
| `base_choice` | how the bases were chosen: `auto`, `forced` (`--base`) or `none` (`--no-base`); `rebase --all` re-chooses only `auto`; if absent, read as `auto`. Not needed to restore |
| `files[].path` | path inside the repo |
| `files[].size`, `sha256` | size and SHA-256 of the **original** file, computed when it was packed |
| `files[].method` | `raw`; `same` (identical to a file stored elsewhere in the archive); or the detected format (`safetensors`, `gguf`, `zstd` for text, `auto` for unknown binaries), with `+ans16` / `+xor` when it is a delta |
| `files[].stored`, `stored_size`, `stored_sha256` | the file as stored in this repo's directory (`stored` is null for `same`) |
| `files[].same_as` | `{"repo", "path"}` of the identical file (method `same`) |
| `files[].delta_base`, `delta_files` | repo id of the base, and `[{"path", "sha256"}]` of its files this delta reads |
| `files[].matched_bytes` | bytes of this file coded against base tensors |
| `files[].format`, `tensors` | for parsed safetensors/GGUF files: `[name, dtype, start, end, shape]` of every tensor (absolute byte offsets; shape outermost dimension first, also for GGUF) |

## OCZ3 container

All integers are little-endian.

```
"OCZ3"                       4 bytes magic
u32 header_len
header_len bytes of JSON:    {"original_size", "regions": [...], "delta": null | {"repo", "files": [{"path", "sha256"}], "op"}}
one frame per region, in order:
  u8  codec                  low 7 bits: 0 = raw, 1 = byte planes, 2 = zstd, 3 = ans16; bit 7 (0x80) = STANDALONE
  u64 raw_len                bytes this frame restores to (= region length)
  u64 payload_len
  u32 crc32                  zlib CRC-32 of the restored ORIGINAL bytes of the region
  payload_len bytes
```

`regions` covers the file in order without gaps; each is `{"offset", "length", "kind", "dtype"?, "base"?}`.

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

### Delta coding (`delta` is set)

Tensors of the file that also exist in the base repo, in any of its files, become regions with
`"base": [i, offset]`: their base bytes are at `offset` in `delta.files[i]`. A tensor matches a base tensor with the
same name and either the same dtype and byte size, the same element count in another float dtype (see
`base_dtype` below), or, for float tensors, the same shape except the first dimension: then only the leading rows
both have are coded against the base (e.g. an embedding after vocabulary extension), and the target's extra rows
form a following standalone region. Restore each base
file first (it may itself be a delta or a `same` reference) and check its SHA-256 against `delta.files[i].sha256`.
Then, per region:

- no `base` key: decode standalone as above.
- `base` and the STANDALONE flag set: decode standalone as above (storing it without the base was smaller).
- kind `ans16`, codec 3: decode the ans16 payload below against the base bytes (`length / 2` weights at `offset`).
- otherwise (kind `xor`, or an `ans16` region that did not shrink): decode as above with the region's dtype, then XOR
  with the `length` base bytes at `offset`.

Because matching is by tensor, a delta works across different file names, header sizes, shardings, tensor
orders, float dtypes and vocabulary sizes. **A delta file is unrecoverable without the base files it lists.** Protect them well (see `docs/protection.md`).

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

- Every stored file has `stored_sha256` (detects bit rot before decoding).
- Every frame has a CRC-32 of the original bytes (localizes damage); ans16 payloads also self-check (step 4).
- Every original file has its `sha256` (proves the restore is exact).
- `pack` refuses to finish a file unless it decodes back from disk to the original SHA-256; a failed ans16 delta is
  redone as an XOR delta. `verify` and `restore` check the stored hashes of every file a file depends on (deltas,
  `same` references) before decoding, and refuse cyclic references.
- `rebase`, and `pack` when it re-packs a repo, write each new stored file under a name the current manifest does
  not use (`<path>.ocz` and `<path>.1.ocz` alternate; a file stored byte-for-byte keeps `<path>` if its bytes are
  unchanged, else alternates with `<path>.1`), decode it back, write the new manifest to a temporary `.oc-*.part` file and
  rename it over `manifest.json`, and only then delete the old stored files, so the archive is complete at every
  moment. Other repos refer to a file by repo, path and SHA-256, which neither command changes.
- Names and paths in a manifest are checked before they are used. A model name is one or more parts separated by
  `/`, each of letters, digits, `-_.+@()[]` and spaces, not empty, not starting or ending with a space, not ending
  with a dot, not a Windows device name, and not mapping to `_kit`. A file path (`path`, `stored`, `same_as.path`,
  `delta_files[].path`) is relative, uses `/`, and has no empty, `.` or `..` part and no NUL. `path` is the original
  name and may hold any other character; `stored` also has no `\` (on Windows no `:` and no part ending in a dot or
  space either), and new stored names are valid on Windows and POSIX and never differ from one another only in case,
  so a model folder can be copied between systems. `restore` refuses paths that would land outside its destination
  and never writes through a link (a symlink or junction below the destination stops it), writes a name its system does not allow with `_` for each character it does
  not allow (on Windows: `<>:"\|?*`, control characters, trailing dots and spaces; device names get `_` after the
  stem), and keeps two paths that would be one file apart with ` (1)`, ` (2)`, ... before the extension of the later
  one (paths that needed no change keep their names).
- `pack` refuses to re-pack a repo when another repo refers to one of its files (by path and SHA-256) that the
  re-pack would change or drop, since that repo could no longer be restored.
- Every manifest entry is checked before use: `path`, `size` (a whole number of at least 0) and `sha256` (64 lowercase
  hex digits); a known `method` (`raw`, `same`, `zstd`, `auto`, `safetensors`, `gguf`, or one of the last two with
  `+ans16` / `+xor`); for `same`, `same_as` with a valid repo and path; otherwise `stored`, `stored_size` and
  `stored_sha256`; for a delta, `delta_base` and a non-empty `delta_files` of paths and SHA-256s. No path may be
  listed twice. A manifest that fails is reported by `verify` and blocks `pack` and `rebase` until it is fixed.
- Every container is checked before anything in it is decoded: magic; a header of at most 64 MiB that is valid
  UTF-8 JSON without duplicate keys, `NaN` or `Infinity`; at most 4,194,304 regions, each 1 byte to 1 GiB long, with
  whole-number offsets and lengths (not `true`, not `0.0`), covering `original_size` in order without gaps, of a
  known kind, with base references only on delta regions and only into `delta.files`; one frame per region whose
  `raw_len` is the region's length and whose codec fits the region; every payload inside the file; nothing after the
  last frame; and `original_size` equal to the manifest's `size`. A zstd payload must be exactly one frame, its
  declared content size no larger than its region, and it is decoded with output capped at that size. An ans16
  payload's lengths must add up exactly, its header fields stay in range (precision 1–30, 1–65,536 lanes, 1–257
  contexts) and its tables (at most 65,536 symbols per context, frequencies summing to `2^prec`) fit. Manifests and
  safetensors headers with duplicate JSON keys, `NaN` or `Infinity` are refused too; a GGUF file whose sizes
  overflow, whose tensor names are not valid UTF-8 or repeat, is stored as plain bytes.
- Every file a tool writes goes first to a new, randomly named temporary file (`.oc-*.part`) created exclusively, and
  is put in place only after it is synced to disk. A new name is taken only if nothing is there, in one step (a hard
  link, then the temporary name removed; where the file system has no hard links, a rename that refuses an existing
  name; with neither, the tool stops rather than check first and rename after). Links below the archive's model
  folders and the restore destination are never followed (on Windows: checked as a best effort).
- Repair data is not part of the format; add your own (copies, PAR2, a self-healing file system, …) and rebuild
  it after re-packing or rebasing a repo. See `docs/protection.md`.
