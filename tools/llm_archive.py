"""Lossless archiver for model folders. safetensors and GGUF files are split per tensor (float tensors: byte planes,
the exponents entropy-coded; quantized tensors, metadata, text: zstd; other binaries: whichever is smaller). A tensor that
also exists in a base model already in the archive (same name, in any of its files) is stored as a delta against it
(ans16 for 16-bit floats), so different file names, headers, shardings and tensor orders still share data. Each file
picks its base by sampling every model in the archive, unless --base names one or --no-base turns deltas off. Files
larger than 1 MiB that are identical to one already in the archive are stored as a reference. See ARCHIVE_FORMAT.md.

    python -I llm_archive.py pack    <source_dir> <archive_root> [--name NAME] [--base NAME | --no-base] [--exclude GLOB ...]
    python -I llm_archive.py rebase  <archive_root> (<name> ... | --all) [--base NAME | --no-base] [--dry-run]
    python -I llm_archive.py verify  <archive_root> [<name> ...]
    python -I llm_archive.py restore <archive_root> <name> <dest_dir> [--overwrite]

The tool never downloads anything: get the model files however you like, then pack the folder. rebase re-chooses the
bases of models already in the archive (e.g. after a closer ancestor was packed), restoring each file it re-packs.

Damage is detected (SHA-256 / CRC) but not repaired here; see docs/protection.md for ways to add repair data.
ans16 deltas restore their base into a temporary file, and rebase the file it re-packs: OC_TMP picks their folder
(default: the system temp folder). OC_WORKERS caps the worker processes used by ans16 (default: one per CPU).
"""

import argparse
import bisect
import datetime
import fnmatch
import hashlib
import itertools
import json
import os
import shutil
import stat
import struct
import sys
import tempfile
import time
import zlib
from collections import deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import numpy as np
import zstandard

TOOL_VERSION = "llm_archive 4"
FORMAT_VERSION = 3
SKIP_DIRS = {".git", ".cache"}  # bookkeeping of git / download tools, not model files
SKIP_FILES = {".hf_files.json"}  # staging metadata written by earlier versions of this tool
MAGIC = b"OCZ3"
CODEC_RAW, CODEC_PLANES, CODEC_ZSTD, CODEC_ANS16 = 0, 1, 2, 3
CODEC_NAMES = ["raw", "planes", "zstd", "ans16"]
FRAME_HEAD = struct.Struct("<BQQI")  # codec, raw_len, payload_len, crc32 of the original chunk
FLOAT_DTYPES = {"BF16": 2, "F16": 2, "F32": 4, "F8_E4M3": 1, "F8_E5M2": 1}  # float dtypes coded as byte planes
RAW_COPY_LIMIT = 1 << 20  # files at or below this size are stored as-is


def repo_dir_name(repo_id):
    return repo_id.replace("/", "__")


NAME_CHARS = "-_.+@()[] "  # besides letters and digits (any script)
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
RESERVED_FOLDERS = {"_kit"}  # the offline kit lives next to the models


def name_problem(repo):
    """None if `repo` can name a model, else why not. A name is one or more parts separated by "/" (e.g.
    Qwen/Qwen2.5-7B), each made of letters, digits and -_.+@()[] or spaces, not starting or ending with a space, not
    ending with a dot (so never "." or ".."), and not a Windows device name; its folder must not be _kit."""
    if not isinstance(repo, str) or not repo:
        return "a model name cannot be empty"
    for part in repo.split("/"):
        if not part or part != part.strip(" ") or part.endswith("."):
            return f"bad model name {repo!r}: no part may be empty, start or end with a space, or end with a dot"
        if not all(ch.isalnum() or ch in NAME_CHARS for ch in part):
            return f"bad model name {repo!r}: use letters, digits, {NAME_CHARS.strip()} and spaces, with / between parts"
        if part.split(".")[0].upper() in WINDOWS_DEVICES:
            return f"bad model name {repo!r}: {part} is a reserved file name on Windows"
    if repo_dir_name(repo).lower() in RESERVED_FOLDERS or len(repo_dir_name(repo)) > 200:
        return f"bad model name {repo!r}: reserved, or too long"
    return None


def path_problem(path):
    """None if `path`, a file's original path in a manifest, is relative, uses "/" and has no empty, "." or ".." part
    (nor NUL), so that it can only name something inside its folder. Any other character is allowed: a name this
    system does not accept is changed when it is restored (local_parts)."""
    if not isinstance(path, str) or not path:
        return "empty file path"
    for part in path.split("/"):
        if part in ("", ".", "..") or "\0" in part:
            return f"unsafe file path {path!r}"
    return None


def stored_problem(path):
    """None if `path`, the name of a stored file inside a model folder, is safe to open on this system: path_problem,
    and no "\\" (a separator on Windows); on Windows also no ":" and no part ending in a dot or space. pack and rebase
    only write names that pass on every system (StoredNames)."""
    problem = path_problem(path)
    if problem or "\\" in path or (os.name == "nt" and any(":" in p or p[-1] in ". " for p in path.split("/"))):
        return problem or f"stored file name {path!r} cannot be used on this system"
    return None


WINDOWS_BAD_CHARS = set('<>:"\\|?*') | {chr(c) for c in range(32)}


def windows_part(part):
    """One file or folder name made valid on Windows: characters it does not allow, and trailing dots and spaces,
    become "_"; a device name (CON, NUL, COM1, ...) gets "_" after its stem, e.g. CON.txt -> CON_.txt."""
    name = "".join("_" if ch in WINDOWS_BAD_CHARS else ch for ch in part)
    kept = name.rstrip(". ")
    name = kept + "_" * (len(name) - len(kept))
    stem, dot, rest = name.partition(".")
    if stem.rstrip(" ").upper() in WINDOWS_DEVICES:
        name = stem + "_" + dot + rest
    return name


def portable_path(path):
    """A path valid on Windows and on POSIX systems: the base of stored file names, so an archive can be copied
    between systems."""
    return "/".join(windows_part(p) for p in path.split("/"))


def local_parts(path):
    """The parts of an original path as this system can name them: changed by windows_part on Windows, as they are
    elsewhere (POSIX allows every character but "/" and NUL)."""
    parts = path.split("/")
    return [windows_part(p) for p in parts] if os.name == "nt" else parts


DELTA_FORMATS = ("safetensors", "gguf")
METHODS = {"raw", "same", "zstd", "auto", *DELTA_FORMATS, *(f"{f}+{k}" for f in DELTA_FORMATS for k in ("ans16", "xor"))}


def is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def is_size(value):
    return type(value) is int and value >= 0


def entry_problem(e):
    """None if a manifest entry can be used: a safe path, its size and SHA-256, a known method and what that method
    needs (same: the file it refers to; any other: its stored file, with size and SHA-256; a delta: its base files)."""
    if not isinstance(e, dict):
        return "an entry of the manifest is not an object"
    problem = path_problem(e.get("path"))
    if problem:
        return problem
    where, method = repr(e["path"]), e.get("method")
    if not is_size(e.get("size")) or not is_sha256(e.get("sha256")):
        return f"{where}: missing or bad size or sha256"
    if not isinstance(method, str) or method not in METHODS:
        return f"{where}: unknown method {method!r}"
    if method == "same":
        ref = e.get("same_as")
        if not isinstance(ref, dict):
            return f"{where}: same_as is missing"
        return name_problem(ref.get("repo")) or path_problem(ref.get("path"))
    if not isinstance(e.get("stored"), str) or not is_size(e.get("stored_size")) or not is_sha256(e.get("stored_sha256")):
        return f"{where}: missing or bad stored, stored_size or stored_sha256"
    problem = stored_problem(e["stored"])
    if problem or "+" not in method:
        return problem
    files = e.get("delta_files")
    if not isinstance(files, list) or not files or not all(isinstance(f, dict) and is_sha256(f.get("sha256")) for f in files):
        return f"{where}: missing or bad delta_files"
    return name_problem(e.get("delta_base")) or next((p for p in map(path_problem, (f.get("path") for f in files)) if p), None)


def manifest_problem(manifest):
    """None if a manifest can be used: its model name, every entry (entry_problem), and no path listed twice. Any
    JSON value gives an answer, never an exception."""
    try:
        return _manifest_problem(manifest)
    except (TypeError, ValueError, KeyError, AttributeError) as e:  # a shape the checks above did not foresee
        return f"malformed manifest ({type(e).__name__})"


def _manifest_problem(manifest):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return "malformed manifest: no list of files"
    problem = name_problem(manifest.get("repo"))
    if problem:
        return problem
    seen = set()
    for e in manifest["files"]:
        problem = entry_problem(e)
        if problem:
            return problem
        if e["path"] in seen:
            return f"{e['path']!r} is listed twice"
        seen.add(e["path"])
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(16 << 20):
            h.update(block)
    return h.hexdigest()


def human(n):
    return f"{n / 2**20:,.1f} MiB"


# ----------------------------------------------------------------------------------------------- codecs


SAFETENSORS_MAX_HEADER = 100 << 20  # the format's own limit


def safetensors_layout(path):
    """(header_bytes_len, {name: (dtype, shape, offsets)}) or None if not a well-formed safetensors file: header
    within the format's size limit and the file, a JSON object of tensors with a string dtype, a shape of
    non-negative integers and offsets inside the data (overlaps are caught later, by tensor_regions)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n > min(SAFETENSORS_MAX_HEADER, size - 8):
                return None
            header = json.loads(f.read(n))
        if not isinstance(header, dict):
            return None
        tensors = {}
        for k, v in header.items():
            if k == "__metadata__":
                continue
            dtype, shape, (start, end) = v["dtype"], v["shape"], v["data_offsets"]
            ints = [*shape, start, end]
            if not (isinstance(dtype, str) and isinstance(shape, list) and all(type(i) is int and i >= 0 for i in ints)
                    and start <= end <= size - 8 - n):
                return None
            tensors[k] = (dtype, tuple(shape), (start, end))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error, RecursionError):
        return None
    return 8 + n, tensors


def xor_bytes(a, b):
    return np.bitwise_xor(np.frombuffer(a, np.uint8), np.frombuffer(b, np.uint8)).tobytes()


# ----------------------------------------------------------------------------------------------- byte planes (codec 1)
#
# A float tensor's bytes look like noise to a general compressor because each weight's exponent, the only part with
# little information, is split across bytes and mixed with the mantissa. Codec 1 rearranges the bits of each weight
# into byte planes, the exponent in a plane of its own (byte grouping, the idea of ZipNN), and codes each plane with
# the smallest of: raw DEFLATE Huffman-only (zlib: near the plane's order-0 entropy, without LZ matching that only
# costs on such data), a zstd frame (catches long runs, e.g. a zeroed tensor), or the plane as it is. Byte-level
# spec: ARCHIVE_FORMAT.md.

PLANE_STORED, PLANE_DEFLATE, PLANE_ZSTD = 0, 1, 2
PLANES_HEAD = struct.Struct("<B")  # number of streams
PLANE_HEAD = struct.Struct("<BQ")  # coding, coded length


def byte_planes(data, dtype):
    """(planes, tail): the bytes of whole elements rearranged into byte planes (uint8 arrays of one byte per element),
    and the bytes after the last whole element. BF16: exponent; sign and 7 mantissa bits. F16: high byte (sign,
    exponent, 2 mantissa bits); low byte. F32: exponent; sign and the top 7 mantissa bits; the middle and the low
    mantissa byte. F8: the bytes themselves."""
    size = FLOAT_DTYPES[dtype]
    whole = len(data) - len(data) % size
    tail = bytes(data[whole:])
    if size == 1:
        return [np.frombuffer(data, np.uint8, whole)], tail
    if size == 2:
        b = np.frombuffer(data, "<u2", whole // 2)
        if dtype == "BF16":
            return [((b >> 7) & 0xFF).astype(np.uint8), (((b >> 8) & 0x80) | (b & 0x7F)).astype(np.uint8)], tail
        return [(b >> 8).astype(np.uint8), (b & 0xFF).astype(np.uint8)], tail
    b = np.frombuffer(data, "<u4", whole // 4)
    return [((b >> 23) & 0xFF).astype(np.uint8), (((b >> 24) & 0x80) | ((b >> 16) & 0x7F)).astype(np.uint8),
            ((b >> 8) & 0xFF).astype(np.uint8), (b & 0xFF).astype(np.uint8)], tail


def join_planes(planes, tail, dtype):
    """Inverse of byte_planes."""
    size = FLOAT_DTYPES[dtype]
    if size == 1:
        return planes[0].tobytes() + tail
    if size == 2:
        p0, p1 = (p.astype(np.uint16) for p in planes)
        b = (((p1 & 0x80) << 8) | (p0 << 7) | (p1 & 0x7F)) if dtype == "BF16" else ((p0 << 8) | p1)
        return b.astype("<u2").tobytes() + tail
    e, m, b1, b0 = (p.astype(np.uint32) for p in planes)
    return (((m & 0x80) << 24) | (e << 23) | ((m & 0x7F) << 16) | (b1 << 8) | b0).astype("<u4").tobytes() + tail


def deflate_huffman(data):
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_HUFFMAN_ONLY)
    return c.compress(data) + c.flush()


def planes_encode(data, dtype):
    planes, tail = byte_planes(data, dtype)
    streams = []
    for plane in planes:
        raw = plane.tobytes()
        options = [(PLANE_STORED, raw), (PLANE_ZSTD, zstandard.ZstdCompressor(level=1).compress(raw)),
                   (PLANE_DEFLATE, deflate_huffman(raw))]
        streams.append(min(options, key=lambda o: len(o[1])))
    if tail:
        streams.append((PLANE_STORED, tail))
    return PLANES_HEAD.pack(len(streams)) + b"".join(PLANE_HEAD.pack(c, len(s)) for c, s in streams) + b"".join(s for _, s in streams)


def planes_decode(payload, raw_len, dtype):
    """Inverse of planes_encode, for a region of raw_len bytes; ValueError if the payload does not fit it exactly."""
    size = FLOAT_DTYPES.get(dtype)
    if size is None:
        raise ValueError(f"no byte planes for dtype {dtype!r}")
    n, tail_len = divmod(raw_len, size)
    (count,) = PLANES_HEAD.unpack_from(payload, 0)
    want = (1 if size == 1 else size) + (1 if tail_len else 0)
    if count != want:
        raise ValueError(f"byte planes: {count} streams, expected {want}")
    heads = [PLANE_HEAD.unpack_from(payload, PLANES_HEAD.size + i * PLANE_HEAD.size) for i in range(count)]
    pos = PLANES_HEAD.size + count * PLANE_HEAD.size
    if pos + sum(length for _, length in heads) != len(payload):
        raise ValueError("byte planes: stream lengths do not add up to the payload")
    out = []
    for i, (coding, length) in enumerate(heads):
        body, pos = payload[pos:pos + length], pos + length
        expect = tail_len if i == size else n
        if coding == PLANE_STORED:
            raw = bytes(body)
        elif coding == PLANE_DEFLATE:
            d = zlib.decompressobj(-15)
            raw = d.decompress(body, expect)
            if not d.eof or d.unconsumed_tail or d.unused_data:
                raise ValueError("byte planes: DEFLATE stream does not end where it should")
        elif coding == PLANE_ZSTD:
            raw = zstandard.ZstdDecompressor().decompress(body, max_output_size=expect)
        else:
            raise ValueError(f"byte planes: unknown coding {coding}")
        if len(raw) != expect:
            raise ValueError("byte planes: a stream decodes to the wrong length")
        out.append(raw)
    planes = [np.frombuffer(p, np.uint8) for p in out[:size]]
    return join_planes(planes, out[size] if tail_len else b"", dtype)


def decode_payload(codec, payload, raw_len, dtype):
    """Original bytes of a frame; dtype: the byte-plane layout of codec 1 (region_planes_dtype)."""
    if codec == CODEC_RAW:
        return bytes(payload)
    if codec == CODEC_ZSTD:
        return zstandard.ZstdDecompressor().decompress(payload, max_output_size=raw_len)
    if codec == CODEC_PLANES:
        return planes_decode(payload, raw_len, dtype)
    raise ValueError(f"unknown codec {codec}")


def checked_candidate(codec, make, data, dtype):
    """Payload from make() if it is smaller than data and decodes back to it exactly, else None."""
    try:
        payload = make()
        if len(payload) < len(data) and decode_payload(codec, payload, len(data), dtype) == data:
            return payload
    except Exception as e:  # a codec bug must never cost data: the caller falls back to another codec
        print(f"    {CODEC_NAMES[codec]} failed self-check ({type(e).__name__}); falling back")
    return None


def encode_chunk(data, dtype, zstd_level, try_both=False):
    """Smallest allowed codec whose output decodes back to `data` exactly; raw if nothing helps.

    With a float dtype, byte planes are tried first and zstd only if they fail or do not shrink the data, unless
    try_both is set (content of unknown type): then zstd also runs when a fast level-3 probe beats the planes,
    since the full level (19 by default) compresses at only a few MB/s."""
    best = (CODEC_RAW, data)
    if dtype:
        payload = checked_candidate(CODEC_PLANES, lambda: planes_encode(data, dtype), data, dtype)
        if payload is not None:
            best = (CODEC_PLANES, payload)
    if try_both and best[0] == CODEC_PLANES:
        probe = zstandard.ZstdCompressor(level=3).compress(data)
        try_both = len(probe) < len(best[1])
    if best[0] == CODEC_RAW or try_both:
        make = lambda: zstandard.ZstdCompressor(level=zstd_level).compress(data)  # noqa: E731
        payload = checked_candidate(CODEC_ZSTD, make, data, None)
        if payload is not None and len(payload) < len(best[1]):
            best = (CODEC_ZSTD, payload)
    return best


def ordered_map(fn, items, workers):
    """fn(*item) for each item, on `workers` threads, results in order; items are drawn (in this thread) only a few
    ahead, so memory stays bounded. zlib, zstd and numpy release the GIL, so the threads run in parallel."""
    with ThreadPoolExecutor(workers) as pool:
        pending = deque()
        for item in items:
            pending.append(pool.submit(fn, *item))
            if len(pending) > workers:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()


# ----------------------------------------------------------------------------------------------- file layouts
#
# A layout-aware file is described as regions {"offset", "length", "kind", "dtype"?}: "float" regions are float
# tensors (byte planes of that dtype), "zstd" regions are headers, metadata and quantized/integer tensors, "auto"
# regions are bytes of unknown type (the smaller of BF16 byte planes and zstd wins, chunk by chunk).

GGUF_FLOAT_TYPES = {0: ("F32", 4), 1: ("F16", 2), 30: ("BF16", 2)}  # ggml_type -> (dtype, bytes per element)
GGUF_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
GGUF_STRING, GGUF_ARRAY = 8, 9
TEXT_SUFFIXES = (".json", ".txt", ".md", ".py", ".jinja", ".model", ".tiktoken", ".vocab", ".yaml", ".yml", ".csv")


def gguf_tensors(path):
    """(data_start, [(name, ggml_type, dims, offset)]) for a GGUF v2/v3 file, or None if it does not parse."""
    try:
        with open(path, "rb") as f:
            def read(fmt):
                st = struct.Struct(fmt)
                return st.unpack(f.read(st.size))[0]

            def skip_string():
                f.seek(read("<Q"), 1)

            if f.read(4) != b"GGUF" or read("<I") not in (2, 3):
                return None
            n_tensors, n_kv = read("<Q"), read("<Q")
            alignment = 32
            for _ in range(n_kv):
                key = f.read(read("<Q"))
                vtype = read("<I")
                if vtype in GGUF_SCALARS:
                    value = read(GGUF_SCALARS[vtype])
                    if key == b"general.alignment":
                        alignment = int(value)
                elif vtype == GGUF_STRING:
                    skip_string()
                elif vtype == GGUF_ARRAY:
                    etype, count = read("<I"), read("<Q")
                    if etype in GGUF_SCALARS:
                        f.seek(struct.calcsize(GGUF_SCALARS[etype]) * count, 1)
                    elif etype == GGUF_STRING:
                        for _ in range(count):
                            skip_string()
                    else:
                        return None
                else:
                    return None
            tensors = []
            for _ in range(n_tensors):
                name = f.read(read("<Q")).decode("utf-8", "replace")
                dims = [read("<Q") for _ in range(read("<I"))]
                tensors.append((name, read("<I"), dims, read("<Q")))
            if alignment < 1:
                return None  # not a valid file: stored as plain bytes
            return -(-f.tell() // alignment) * alignment, tensors
    except (OSError, ValueError, struct.error, MemoryError, OverflowError):
        return None


def file_tensors(path, size):
    """(format, tensors) for a parseable safetensors or GGUF file, else (None, None). Each tensor is
    [name, dtype, start, end, shape] with absolute byte offsets and the shape outermost dimension first; GGUF
    quantized types are named "ggml_type_<n>" and run up to the next tensor (or the end of the file)."""
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == b"GGUF":
        parsed = gguf_tensors(path)
        if parsed:
            data_start, raw = parsed
            starts = sorted(data_start + offset for _, _, _, offset in raw) + [size]
            tensors = []
            for name, ggml_type, dims, offset in raw:
                start = data_start + offset
                next_start = next((s for s in starts if s > start), size)
                if ggml_type in GGUF_FLOAT_TYPES:
                    dtype, element = GGUF_FLOAT_TYPES[ggml_type]
                    end = start + element * int(np.prod(dims, dtype=np.int64))
                else:
                    dtype, end = f"ggml_type_{ggml_type}", next_start
                if end > next_start:
                    break
                tensors.append([name, dtype, start, end, [int(d) for d in reversed(dims)]])
            else:
                return "gguf", tensors
    if path.endswith(".safetensors"):
        layout = safetensors_layout(path)
        if layout:
            data_start, items = layout
            return "safetensors", [[name, dtype, data_start + s, data_start + e, list(shape)] for name, (dtype, shape, (s, e)) in items.items()]
    return None, None


def tensor_regions(tensors, file_size, chunk_size, match=None):
    """Regions covering a parsed file. Tensors that match(tensor) -> (base file, base start, kind, base_dtype,
    covered bytes) become delta regions with "base": [file index, offset] over their first `covered` bytes (all of
    them unless only some rows exist in the base); the rest are standalone (float tensors "float", others "zstd"
    tagged with their dtype). Gaps (headers, metadata, padding) become untagged "zstd". None if the tensor table
    overlaps itself or runs past the end of the file."""
    spans = []
    for tensor in tensors:
        _, dtype, start, end = tensor[:4]
        standalone = "float" if dtype in FLOAT_DTYPES else "zstd"
        m = match(tensor) if match and end > start else None
        if m:
            cut = start + m[4]
            spans.append((start, cut, m[2], dtype, [m[0], m[1]], m[3]))
            if cut < end:
                spans.append((cut, end, standalone, dtype, None, None))
        else:
            spans.append((start, end, standalone, dtype, None, None))
    regions, pos = [], 0
    for start, end, kind, dtype, base, base_dtype in sorted(spans, key=lambda s: (s[0], s[1])):
        if start < pos or end > file_size:
            return None
        if start > pos:
            regions.append({"offset": pos, "length": start - pos, "kind": "zstd"})
        if end > start:
            region = {"offset": start, "length": end - start, "kind": kind, "dtype": dtype}
            if base:
                region["base"] = base
            if base_dtype:
                region["base_dtype"] = base_dtype
            regions.append(region)
        pos = end
    if file_size > pos:
        regions.append({"offset": pos, "length": file_size - pos, "kind": "zstd"})
    merged = []
    for r in regions:
        if merged and r["kind"] == "zstd" and merged[-1]["kind"] == "zstd" and "dtype" not in r and "dtype" not in merged[-1]:
            merged[-1]["length"] += r["length"]
        else:
            merged.append(r)
    return split_regions(merged, chunk_size)


def split_regions(regions, chunk_size):
    """Cut long regions into pieces: ans16 regions into blocks of ANS16_BLOCK_ELEMS weights, the rest into
    chunk_size (a multiple of every element size). A piece's base offset advances with it."""
    out = []
    for r in regions:
        piece = 2 * ANS16_BLOCK_ELEMS if r["kind"] == "ans16" else chunk_size
        for off in range(0, r["length"], piece):
            p = {**r, "offset": r["offset"] + off, "length": min(piece, r["length"] - off)}
            if "base" in r:  # a converted base (e.g. F32 for a BF16 target) advances by its own element size
                scale = FLOAT_BYTES[r["base_dtype"]] // FLOAT_BYTES[r["dtype"]] if "base_dtype" in r else 1
                p["base"] = [r["base"][0], r["base"][1] + off * scale]
            out.append(p)
    return out


def file_regions(path, size, chunk_size):
    """(format, tensors or None, standalone regions) describing how to compress a file, chosen from its content."""
    fmt, tensors = file_tensors(path, size)
    if tensors is not None:
        regions = tensor_regions(tensors, size, chunk_size)
        if regions:
            return fmt, tensors, regions
    kind = "zstd" if path.lower().endswith(TEXT_SUFFIXES) else "auto"
    return kind, None, split_regions([{"offset": 0, "length": size, "kind": kind}], chunk_size)


def match_against_base(tensors, base_files, delta_codec):
    """match() for tensor_regions. A target tensor matches a base tensor with the same name and, in order of
    preference: the same dtype and byte size; the same element count in another float dtype (BF16/F16 targets only,
    base values converted: base_dtype); or, for float tensors, the same shape except the first dimension (e.g. an
    embedding after vocabulary extension), covering the leading rows both have. Prefers the base file sharing the
    most bytes with this target file. base_files: [{"tensors": ...}]. match() returns
    (base file, base start, kind, base_dtype or None, covered bytes)."""
    by_name = {}
    for index, base_file in enumerate(base_files):
        for t in base_file["tensors"]:
            by_name.setdefault(t[0], []).append((index, t[1], t[2], t[3] - t[2], t[4] if len(t) > 4 else None))

    def candidates(tensor):
        name, dtype, start, end = tensor[:4]
        shape, size = (tensor[4] if len(tensor) > 4 else None), end - start
        found = by_name.get(name, [])
        exact = [(i, s, None, size) for i, d, s, n, _ in found if d == dtype and n == size]
        if exact or dtype not in FLOAT_BYTES:
            return exact
        out = []
        for i, d, s, n, base_shape in found:
            if d not in FLOAT_BYTES or (d != dtype and dtype not in ANS16_EXPONENT):
                continue  # only 16-bit targets can be predicted from another dtype
            base_dtype = None if d == dtype else d
            if d != dtype and n == size // FLOAT_BYTES[dtype] * FLOAT_BYTES[d]:
                out.append((i, s, base_dtype, size))
            elif shape and base_shape and len(shape) == len(base_shape) > 1 and shape[1:] == base_shape[1:]:
                rows = min(shape[0], base_shape[0])
                covered = rows * int(np.prod(shape[1:], dtype=np.int64)) * FLOAT_BYTES[dtype]
                if 0 < covered <= size and covered // FLOAT_BYTES[dtype] * FLOAT_BYTES[d] <= n:
                    out.append((i, s, base_dtype, covered))
        return out

    shared = {}
    for tensor in tensors:
        for index, _, _, covered in candidates(tensor):
            shared[index] = shared.get(index, 0) + covered

    def match(tensor):
        found = candidates(tensor)
        if not found:
            return None
        index, base_start, base_dtype, covered = max(found, key=lambda c: (c[3], shared[c[0]]))
        kind = "ans16" if delta_codec == "ans16" and tensor[1] in ANS16_EXPONENT else "xor"
        return index, base_start, kind, base_dtype, covered

    return match


# ----------------------------------------------------------------------------------------------- ans16 delta coder
#
# For a 16-bit float tensor present in both base and target, each weight's residual
# r = (target_bits - base_bits) mod 2^16 is entropy-coded with interleaved rANS (64-bit state, 32-bit words).
# The frequency table used for a weight is chosen by a context derived from the base weight alone (its exponent
# bits), so the decoder rebuilds every context from the base. Tables are stored as integers, so decoding does not
# depend on floating-point behaviour or on any library's quantizer. Byte-level spec: ARCHIVE_FORMAT.md.

ANS16_PREC = 24  # frequencies of every table sum to 2^24
ANS16_MIN_CONTEXT = 4096  # contexts with fewer weights in a region share one table
ANS16_RARE_CONTEXT = 256
ANS16_SYMBOLS_PER_LANE = 4096
ANS16_MAX_LANES = 1 << 16
ANS16_BLOCK_ELEMS = 8 << 20  # big tensors are split into regions of at most this many weights
ANS16_EXPONENT = {"BF16": (7, 0xFF), "F16": (10, 0x1F)}  # dtype -> (shift, mask) of the exponent field
FLOAT_BYTES = {"BF16": 2, "F16": 2, "F32": 4}  # base dtypes a 16-bit target tensor can be predicted from
F16_NAN, BF16_NAN = 0x7E00, 0x7FC0
ANS16_ALONE_CHECK = 0.5  # an ans16 frame bigger than this share of its data is also tried without the base


def to_float32(bits, dtype):
    """Exact float32 values of BF16 / F16 / F32 bit patterns."""
    if dtype == "F32":
        return bits.astype(np.uint32).view(np.float32)
    if dtype == "F16":
        return bits.astype(np.uint16).view(np.float16).astype(np.float32)
    return (bits.astype(np.uint32) << 16).view(np.float32)


def convert_bits(bits, src, dst):
    """Bit patterns of src-typed values converted to dst (BF16 or F16): IEEE 754 round-to-nearest-even, overflow to
    infinity, and every NaN to one canonical quiet NaN, so that any decoder rebuilds exactly the same predictor."""
    values = to_float32(bits, src)
    with np.errstate(over="ignore", invalid="ignore"):
        if dst == "F16":
            out = values.astype(np.float16).view(np.uint16)
        else:
            u = values.view(np.uint32)
            out = ((u + 0x7FFF + ((u >> 16) & 1)) >> 16).astype(np.uint16)
    out[np.isnan(values)] = F16_NAN if dst == "F16" else BF16_NAN
    return out


def base_prediction(base_path, offset, count, base_dtype, dtype):
    """The count 16-bit values a target region is coded against: the base bits as stored, or, for a base tensor of
    another float dtype, those values converted to the target dtype."""
    if base_dtype is None or base_dtype == dtype:
        return np.fromfile(base_path, "<u2", count, offset=offset)
    bits = np.fromfile(base_path, "<u4" if base_dtype == "F32" else "<u2", count, offset=offset)
    return convert_bits(bits, base_dtype, dtype)


def prediction_from_bytes(raw, base_dtype, dtype):
    """base_prediction() for base bytes already in memory."""
    if base_dtype is None or base_dtype == dtype:
        return np.frombuffer(raw, "<u2")
    return convert_bits(np.frombuffer(raw, "<u4" if base_dtype == "F32" else "<u2"), base_dtype, dtype)


def base_bytes(base_paths, base_files, region):
    """Bytes a region's XOR residual is taken against."""
    index, offset = region["base"]
    if "base_dtype" in region:
        return base_prediction(base_paths[index], offset, region["length"] // 2, region["base_dtype"], region["dtype"]).tobytes()
    base_files[index].seek(offset)
    return base_files[index].read(region["length"])
ANS16_HEAD = struct.Struct("<IIQIIIII")  # prec, min_context, n, lanes, contexts, tables_zlen, counts_zlen, words
RANS_L = 1 << 31


def ans16_contexts(base, dtype, min_context, count=None):
    """(context of each of the first `count` weights, or of all; sorted ids of the contexts the whole region uses)."""
    shift, mask = ANS16_EXPONENT[dtype]
    ctx = ((base >> shift) & mask).astype(np.int32)
    counts = np.bincount(ctx, minlength=256)
    rare = (counts > 0) & (counts < min_context)
    ids = np.flatnonzero((counts > 0) & ~rare)
    if rare.any():
        ids = np.append(ids, ANS16_RARE_CONTEXT)
    ctx = ctx[:count]
    return np.where(rare[ctx], np.int32(ANS16_RARE_CONTEXT), ctx), ids


def group_starts(k):
    return np.concatenate(([0], np.cumsum(k)[:-1])).astype(np.int64)


def group_exclusive_cumsum(values, k):
    """Exclusive prefix sum of `values`, restarting at every group boundary (groups of sizes k)."""
    cum = np.cumsum(values) - values
    return cum - np.repeat(cum[group_starts(k)], k)


def rans_encode(freq, cum, prec, lanes):
    """Interleaved rANS; symbol i goes to lane i % lanes. Returns final states, words per lane, words (decode order)."""
    n = freq.size
    x = np.full(lanes, RANS_L, np.uint64)
    out_lane, out_step, out_word = [], [], []
    for s in range((n + lanes - 1) // lanes - 1, -1, -1):
        lo = s * lanes
        f = freq[lo : lo + lanes].astype(np.uint64)
        xs = x[: f.size]
        emit = np.flatnonzero(xs >= (f << (63 - prec)))
        if emit.size:
            out_lane.append(emit.astype(np.int32))
            out_step.append(np.full(emit.size, s, np.int32))
            out_word.append((xs[emit] & 0xFFFFFFFF).astype(np.uint32))
            xs[emit] >>= 32
        q = xs // f
        xs[:] = (q << prec) + (xs - q * f) + cum[lo : lo + lanes].astype(np.uint64)
    if not out_lane:
        return x, np.zeros(lanes, np.uint32), np.zeros(0, np.uint32)
    lane, step, word = np.concatenate(out_lane), np.concatenate(out_step), np.concatenate(out_word)
    order = np.lexsort((step, lane))  # grouped by lane, each lane in the order its decoder reads them
    return x, np.bincount(lane, minlength=lanes).astype(np.uint32), word[order]


def rans_decode(states, lane_counts, words, n, prec, lookup, stop=None):
    """Inverse of rans_encode. lookup(lo, hi, slot) -> (table index, freq, cum) for symbols lo..hi. With stop, only
    the first `stop` symbols are decoded (and the stream's end is not checked)."""
    lanes = states.size
    x = states.astype(np.uint64)
    ptr = group_starts(lane_counts.astype(np.int64))
    end = ptr + lane_counts
    words64 = words.astype(np.uint64)
    mask = np.uint64((1 << prec) - 1)
    m = n if stop is None else min(stop, n)
    out = np.empty(m, np.int64)
    for s in range((m + lanes - 1) // lanes):
        lo, hi = s * lanes, min(s * lanes + lanes, m)
        xs = x[: hi - lo]
        slot = xs & mask
        idx, f, c = lookup(lo, hi, slot)
        out[lo:hi] = idx
        xs[:] = f * (xs >> prec) + slot - c
        need = np.flatnonzero(xs < RANS_L)
        if need.size:
            p = ptr[need]
            if np.any(p >= end[need]):
                raise ValueError("ans16 stream ran out of words (corrupt payload)")
            xs[need] = (xs[need] << 32) | words64[p]
            ptr[need] = p + 1
    if m < n:
        return out
    if np.any(x != RANS_L) or np.any(ptr != end):
        raise ValueError("ans16 stream did not return to its initial state (corrupt payload or wrong base)")
    return out


def ans16_quantize(counts, k, prec):
    """Integer frequencies per group: every symbol >= 1, each group sums to exactly 2^prec."""
    total = 1 << prec
    freq = np.empty(counts.size, np.int64)
    for a, size in zip(group_starts(k), k):
        c = counts[a : a + size].astype(np.int64)
        f = 1 + c * (total - size) // c.sum()
        f[np.argmax(f)] += total - f.sum()
        freq[a : a + size] = f
    return freq


def ans16_encode(base, target, dtype, prec=ANS16_PREC, min_context=ANS16_MIN_CONTEXT):
    if base.size == 0 or base.size != target.size:
        raise ValueError("ans16 codes one or more weights against as many base weights")  # pack never asks otherwise
    ctx, ctx_ids = ans16_contexts(base, dtype, min_context)
    ctxpos = np.searchsorted(ctx_ids, ctx).astype(np.int32)
    residual = (target - base).astype(np.uint16)  # wraps mod 2^16
    # Histogram over (context, residual) keys; keys < 257 * 65536, so a dense count array beats sorting.
    key = ctxpos * np.int32(65536) + residual
    histogram = np.bincount(key, minlength=ctx_ids.size * 65536)
    keys = np.flatnonzero(histogram)
    counts = histogram[keys]
    del histogram
    k = np.bincount(keys >> 16, minlength=ctx_ids.size)
    sym = keys & 0xFFFF
    freq = ans16_quantize(counts, k, prec)
    cum = group_exclusive_cumsum(freq, k)
    position = np.empty(ctx_ids.size * 65536, np.int32)
    position[keys] = np.arange(keys.size, dtype=np.int32)
    inv = position[key]
    del position, key
    lanes = int(min(ANS16_MAX_LANES, max(1, base.size // ANS16_SYMBOLS_PER_LANE)))
    states, lane_counts, words = rans_encode(freq.astype(np.uint32)[inv], cum.astype(np.uint32)[inv], prec, lanes)
    sym_delta = np.diff(sym, prepend=0)
    starts = group_starts(k)
    sym_delta[starts] = sym[starts]
    tables = b"".join(
        a.astype(t).tobytes() for a, t in ((ctx_ids, "<u2"), (k, "<u4"), (sym_delta, "<u2"), (freq, "<u4"))
    )
    tables_z = zstandard.ZstdCompressor(level=19).compress(tables)
    counts_z = zstandard.ZstdCompressor(level=19).compress(lane_counts.astype("<u4").tobytes())
    head = ANS16_HEAD.pack(prec, min_context, base.size, lanes, ctx_ids.size, len(tables_z), len(counts_z), words.size)
    return head + tables_z + states.astype("<u8").tobytes() + counts_z + words.astype("<u4").tobytes()


def ans16_decode(base, payload, dtype, stop=None):
    """The target weights coded in payload; with stop, only the first `stop` of them (cheaper, but unchecked)."""
    prec, min_context, n, lanes, contexts, tables_zlen, counts_zlen, nwords = ANS16_HEAD.unpack_from(payload, 0)
    if n != base.size:
        raise ValueError("ans16 region size does not match its base")
    pos = ANS16_HEAD.size
    tables = zstandard.ZstdDecompressor().decompress(payload[pos : pos + tables_zlen])
    pos += tables_zlen
    states = np.frombuffer(payload, "<u8", lanes, pos)
    pos += 8 * lanes
    lane_counts = np.frombuffer(zstandard.ZstdDecompressor().decompress(payload[pos : pos + counts_zlen]), "<u4")
    pos += counts_zlen
    words = np.frombuffer(payload, "<u4", nwords, pos)
    ctx_ids = np.frombuffer(tables, "<u2", contexts, 0).astype(np.int64)
    k = np.frombuffer(tables, "<u4", contexts, 2 * contexts).astype(np.int64)
    total = int(k.sum())
    sym_delta = np.frombuffer(tables, "<u2", total, 6 * contexts).astype(np.int64)
    freq = np.frombuffer(tables, "<u4", total, 6 * contexts + 2 * total).astype(np.int64)
    sym = (group_exclusive_cumsum(sym_delta, k) + sym_delta).astype(np.uint16)
    cum = group_exclusive_cumsum(freq, k).astype(np.uint64)
    freq = freq.astype(np.uint64)
    table_keys = (np.repeat(np.arange(contexts, dtype=np.uint64), k) << np.uint64(prec)) + cum
    ctx, used = ans16_contexts(base, dtype, min_context, stop)
    if not np.array_equal(used, ctx_ids):
        raise ValueError("ans16 contexts do not match the base (wrong base or corrupt payload)")
    ctx_keys = np.searchsorted(ctx_ids, ctx).astype(np.uint64) << np.uint64(prec)

    def lookup(lo, hi, slot):
        idx = np.searchsorted(table_keys, ctx_keys[lo:hi] + slot, side="right") - 1
        return idx, freq[idx], cum[idx]

    idx = rans_decode(states, lane_counts, words, n, prec, lookup, stop)
    return (base[: idx.size] + sym[idx]).astype(np.uint16)


def region_zstd_level(region, text_level, tensor_level):
    """Headers, metadata and text files get the slow high level; tensor data and unknown binaries the fast one
    (on quantized weights level 19 saves ~0.1% over level 9 at ~1/80 of the speed)."""
    return text_level if region["kind"] == "zstd" and "dtype" not in region else tensor_level


def region_planes_dtype(region):
    """The dtype whose byte planes a region's codec-1 frame uses: BF16 for bytes of unknown type, the tensor's own
    for a float tensor (None: the region never uses byte planes)."""
    return "BF16" if region["kind"] == "auto" else region.get("dtype") if region.get("dtype") in FLOAT_DTYPES else None


def ans16_workers():
    value = os.environ.get("OC_WORKERS")
    if not value:
        return os.cpu_count() or 1
    if not value.strip().isdigit() or int(value) < 1:
        sys.exit(f"OC_WORKERS must be a whole number of at least 1, not {value!r}")
    return int(value)


def frame_workers():
    """Threads that code byte-plane and zstd frames: up to 8 (more gain little), fewer with OC_WORKERS."""
    return max(1, min(ans16_workers(), 8))


def _ans16_encode_job(job):
    base_path, base_offset, base_dtype, target_path, offset, length, dtype = job
    base = base_prediction(base_path, base_offset, length // 2, base_dtype, dtype)
    target = np.fromfile(target_path, "<u2", length // 2, offset=offset)
    return ans16_encode(base, target, dtype)


def _ans16_decode_job(job):
    container_path, payload_offset, payload_len, base_path, base_offset, base_dtype, length, dtype = job
    with open(container_path, "rb") as f:
        f.seek(payload_offset)
        payload = f.read(payload_len)
    base = base_prediction(base_path, base_offset, length // 2, base_dtype, dtype)
    return ans16_decode(base, payload, dtype).tobytes()


# ----------------------------------------------------------------------------------------------- container I/O


def read_container_header(f):
    if f.read(4) != MAGIC:
        raise ValueError(f"not an {MAGIC.decode()} container")
    (n,) = struct.unpack("<I", f.read(4))
    return json.loads(f.read(n))


def find_entry(archive_root, repo, path):
    entry = next((e for e in load_manifest(archive_root, repo)["files"] if e["path"] == path), None)
    if entry is None:
        raise ValueError(f"{repo}:{path} is not in the archive")
    return entry


MAX_REFERENCE_DEPTH = 32  # deeper chains of deltas / same-as references are treated as a cycle


def iter_entry_bytes(archive_root, repo, entry, depth=0):
    """Yield the original bytes of a manifest entry, following same-as references and deltas."""
    if depth > MAX_REFERENCE_DEPTH:
        raise ValueError("reference chain too deep (cyclic references?)")
    if entry["method"] == "same":
        ref = entry["same_as"]
        yield from iter_entry_bytes(archive_root, ref["repo"], find_entry(archive_root, ref["repo"], ref["path"]), depth + 1)
        return
    stored = os.path.join(archive_root, repo_dir_name(repo), entry["stored"])
    if entry["method"] == "raw":
        with open(stored, "rb") as f:
            while block := f.read(16 << 20):
                yield block
    else:
        yield from iter_original_chunks(archive_root, stored, depth)


def materialize_file(archive_root, repo, path, sha256, depth=0):
    """Restore an archived file into a temporary file (OC_TMP or the system temp folder) and return its path."""
    entry = find_entry(archive_root, repo, path)
    if entry["sha256"] != sha256:
        raise ValueError(f"{repo}:{path} has a different sha256 than recorded")
    fd, tmp = tempfile.mkstemp(prefix="oc-base-", suffix=".bin", dir=os.environ.get("OC_TMP") or None)
    h = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out:
            for chunk in iter_entry_bytes(archive_root, repo, entry, depth + 1):
                out.write(chunk)
                h.update(chunk)
        if h.hexdigest() != sha256:
            raise ValueError(f"{repo}:{path} restored with the wrong sha256")
    except BaseException:
        os.remove(tmp)
        raise
    return tmp


_BASE_CACHE = {}  # (repo, path, sha256) -> restored base file that pack keeps until it has verified the delta


def release_cached_bases():
    for path in _BASE_CACHE.values():
        os.remove(path)
    _BASE_CACHE.clear()


def base_paths_for(archive_root, delta, depth=0):
    """Restored base files for a delta header, as (paths, paths this call created and must delete)."""
    paths, owned = [], []
    try:
        for f in delta["files"]:
            key = (delta["repo"], f["path"], f["sha256"])
            path = _BASE_CACHE.get(key)
            if path is None:
                path = materialize_file(archive_root, delta["repo"], f["path"], f["sha256"], depth)
                owned.append(path)
            paths.append(path)
    except BaseException:
        for path in owned:
            os.remove(path)
        raise
    return paths, owned


STANDALONE = 0x80  # frame codec flag: this region of a delta was stored without using the base


def iter_original_chunks(archive_root, container_path, depth=0):
    """Yield the original file's bytes region by region, resolving deltas against base archives recursively.
    ans16 regions are decoded in parallel worker processes."""
    with open(container_path, "rb") as f:
        header = read_container_header(f)
        regions = header["regions"]
        frames = []
        for _ in regions:
            codec, raw_len, payload_len, crc = FRAME_HEAD.unpack(f.read(FRAME_HEAD.size))
            frames.append((codec, raw_len, f.tell(), payload_len, crc))
            f.seek(payload_len, 1)
        delta = header.get("delta")
        base_paths, owned = base_paths_for(archive_root, delta, depth) if delta else ([], [])
        jobs = [
            (container_path, off, plen, base_paths[r["base"][0]], r["base"][1], r.get("base_dtype"), r["length"], r["dtype"])
            for r, (codec, _, off, plen, _) in zip(regions, frames) if codec == CODEC_ANS16
        ]
        pool = ProcessPoolExecutor(ans16_workers()) if jobs else None
        bases = [open(p, "rb") for p in base_paths]
        plain = None
        try:
            decoded = pool.map(_ans16_decode_job, jobs) if pool else iter(())

            def to_decode():  # read here, in order; decoded on the threads
                for region, (codec, raw_len, off, plen, _) in zip(regions, frames):
                    if codec != CODEC_ANS16:
                        f.seek(off)
                        payload = f.read(plen)
                        base = base_bytes(base_paths, bases, region) if "base" in region and not codec & STANDALONE else None
                        yield codec, payload, raw_len, region, base

            def decode(codec, payload, raw_len, region, base):
                chunk = decode_payload(codec & ~STANDALONE, payload, raw_len, region_planes_dtype(region))
                return chunk if base is None else xor_bytes(chunk, base)

            plain = ordered_map(decode, to_decode(), frame_workers())
            for region, (codec, raw_len, off, plen, crc) in zip(regions, frames):
                chunk = next(decoded) if codec == CODEC_ANS16 else next(plain)
                if len(chunk) != raw_len or zlib.crc32(chunk) != crc:
                    raise ValueError(f"{container_path}: region at {region['offset']} failed its CRC check")
                yield chunk
        finally:
            if plain is not None:
                plain.close()
            for b in bases:
                b.close()
            if pool:
                pool.shutdown()
            for path in owned:
                os.remove(path)


def write_container(src_path, dst_path, regions, levels, delta=None, base_paths=(), workers=1):
    """One frame per region. Standalone regions are coded by kind. Regions with a "base" reference are coded
    against it (ans16 in parallel worker processes, or the original XOR base by byte planes/zstd) unless storing
    them standalone is smaller, in which case the frame's codec carries the STANDALONE flag. Other regions are coded
    on threads (frame_workers), read and written here in order."""
    header = {"original_size": os.path.getsize(src_path), "regions": regions, "delta": delta}
    hb = json.dumps(header).encode()
    codecs_used = {}
    jobs = [
        (base_paths[r["base"][0]], r["base"][1], r.get("base_dtype"), src_path, r["offset"], r["length"], r["dtype"])
        for r in regions if r["kind"] == "ans16"
    ]
    pool = ProcessPoolExecutor(workers) if jobs else None
    bases = [open(p, "rb") for p in base_paths]

    def code(region, data, base):
        """(length, crc32, codec options) of a region; base: its base bytes, for a delta region."""
        dtype, level = region_planes_dtype(region), region_zstd_level(region, *levels)
        if base is None:
            return len(data), zlib.crc32(data), [encode_chunk(data, dtype, level, try_both=region["kind"] == "auto")]
        codec, payload = encode_chunk(data, dtype, level)
        return len(data), zlib.crc32(data), [encode_chunk(xor_bytes(data, base), dtype, level), (codec | STANDALONE, payload)]

    coded = None
    try:
        with open(src_path, "rb") as src, open(src_path, "rb") as ahead, open(dst_path + ".part", "wb") as dst:
            dst.write(MAGIC + struct.pack("<I", len(hb)) + hb)
            encoded = pool.map(_ans16_encode_job, jobs) if pool else iter(())

            def to_code():  # read here, in order (from a handle of its own); coded on the threads
                for region in regions:
                    if region["kind"] != "ans16":
                        ahead.seek(region["offset"])
                        data = ahead.read(region["length"])
                        yield region, data, base_bytes(base_paths, bases, region) if "base" in region else None

            coded = ordered_map(code, to_code(), frame_workers())
            for region in regions:
                if region["kind"] != "ans16":
                    length, crc, options = next(coded)
                else:
                    src.seek(region["offset"])
                    data = src.read(region["length"])
                    length, crc, payload = len(data), zlib.crc32(data), next(encoded)
                    if len(payload) >= len(data):  # no gain from ans16: XOR against the base, or standalone
                        options = code(region, data, base_bytes(base_paths, bases, region))[2]
                    else:
                        options = [(CODEC_ANS16, payload)]
                        if len(payload) > len(data) * ANS16_ALONE_CHECK:  # weakly related base
                            codec, alone = encode_chunk(data, region_planes_dtype(region), region_zstd_level(region, *levels))
                            options.append((codec | STANDALONE, alone))
                codec, payload = min(options, key=lambda o: len(o[1]))
                codecs_used[codec] = codecs_used.get(codec, 0) + 1
                dst.write(FRAME_HEAD.pack(codec, length, len(payload), crc))
                dst.write(payload)
            dst.flush()
            os.fsync(dst.fileno())  # on disk before a manifest can point at it (rebase then deletes the old file)
    finally:
        if coded is not None:
            coded.close()
        for b in bases:
            b.close()
        if pool:
            pool.shutdown()
    os.replace(dst_path + ".part", dst_path)
    return codecs_used


# ----------------------------------------------------------------------------------------------- partial reads
#
# Choosing a base samples a few windows of every candidate model. read_range() decodes only the regions that hold a
# window (an ans16 region only up to the window's end) and, for delta regions, only the base bytes those need.

_ENTRIES = {}  # (archive_root, repo) -> {path: manifest entry}
_CONTAINERS = {}  # container path -> (header, frames, region offsets)
_FRAMES = {}  # (container path, region index) -> decoded region, least recently used first
FRAME_CACHE_BYTES = 384 << 20


def forget_cached_reads():
    """Drop what read_range() has cached; needed once a manifest or a stored file in the archive has changed."""
    _ENTRIES.clear()
    _CONTAINERS.clear()
    _FRAMES.clear()


def cached_entry(archive_root, repo, path, sha256):
    key = (archive_root, repo)
    if key not in _ENTRIES:
        _ENTRIES[key] = {e["path"]: e for e in load_manifest(archive_root, repo)["files"]}
    entry = _ENTRIES[key].get(path)
    if entry is None or entry["sha256"] != sha256:
        raise ValueError(f"{repo}:{path} is missing from the archive or has a different sha256 than recorded")
    return entry


def container_index(path):
    """(header, [(codec, raw_len, payload offset, payload_len, crc)], region offsets) of a container."""
    if path not in _CONTAINERS:
        with open(path, "rb") as f:
            header = read_container_header(f)
            frames = []
            for _ in header["regions"]:
                codec, raw_len, payload_len, crc = FRAME_HEAD.unpack(f.read(FRAME_HEAD.size))
                frames.append((codec, raw_len, f.tell(), payload_len, crc))
                f.seek(payload_len, 1)
        _CONTAINERS[path] = header, frames, [r["offset"] for r in header["regions"]]
    return _CONTAINERS[path]


def read_range(archive_root, repo, entry, start, length, depth=0):
    """Bytes [start, start + length) of an archived file, without restoring the rest of it."""
    if depth > MAX_REFERENCE_DEPTH:
        raise ValueError("reference chain too deep (cyclic references?)")
    if entry["method"] == "same":
        ref = entry["same_as"]
        target = cached_entry(archive_root, ref["repo"], ref["path"], entry["sha256"])
        return read_range(archive_root, ref["repo"], target, start, length, depth + 1)
    stored = os.path.join(archive_root, repo_dir_name(repo), entry["stored"])
    if entry["method"] == "raw":
        with open(stored, "rb") as f:
            f.seek(start)
            return f.read(length)
    header, _, offsets = container_index(stored)
    parts, end = [], start + length
    for i in range(max(0, bisect.bisect_right(offsets, start) - 1), len(offsets)):
        region = header["regions"][i]
        if region["offset"] >= end:
            break
        a, b = max(start - region["offset"], 0), min(end - region["offset"], region["length"])
        if b > a:
            parts.append(region_bytes(archive_root, stored, i, a, b, depth))
    return b"".join(parts)


def region_base(archive_root, delta, region, depth):
    """The base bytes (before any dtype conversion) a delta region was coded against."""
    index, offset = region["base"]
    scale = FLOAT_BYTES[region["base_dtype"]] // FLOAT_BYTES[region["dtype"]] if "base_dtype" in region else 1
    f = delta["files"][index]
    entry = cached_entry(archive_root, delta["repo"], f["path"], f["sha256"])
    return read_range(archive_root, delta["repo"], entry, offset, region["length"] * scale, depth + 1)


def region_bytes(archive_root, stored, i, a, b, depth):
    """Bytes a..b (counted from the region's start) of region i of a container."""
    header, frames, _ = container_index(stored)
    region, (codec, raw_len, off, plen, crc) = header["regions"][i], frames[i]
    key = (stored, i)
    if key in _FRAMES:
        _FRAMES[key] = chunk = _FRAMES.pop(key)  # now the most recently used
        return chunk[a:b]
    with open(stored, "rb") as f:
        f.seek(off)
        payload = f.read(plen)
    if codec == CODEC_ANS16:
        base = prediction_from_bytes(region_base(archive_root, header["delta"], region, depth), region.get("base_dtype"), region["dtype"])
        stop = -(-b // 2)
        chunk = ans16_decode(base, payload, region["dtype"], stop if stop < raw_len // 2 else None).tobytes()
        if len(chunk) < raw_len:  # a prefix only: nothing to check it against, so it is not cached either
            return chunk[a:b]
    else:
        chunk = decode_payload(codec & ~STANDALONE, payload, raw_len, region_planes_dtype(region))
        if "base" in region and not codec & STANDALONE:
            base = region_base(archive_root, header["delta"], region, depth)
            if "base_dtype" in region:
                base = prediction_from_bytes(base, region["base_dtype"], region["dtype"]).tobytes()
            chunk = xor_bytes(chunk, base)
    if len(chunk) != raw_len or zlib.crc32(chunk) != crc:
        raise ValueError(f"{stored}: region at {region['offset']} failed its CRC check")
    _FRAMES[key] = chunk
    total = sum(len(c) for c in _FRAMES.values())
    while total > FRAME_CACHE_BYTES and len(_FRAMES) > 1:
        total -= len(_FRAMES.pop(next(iter(_FRAMES))))
    return chunk[a:b]


# ----------------------------------------------------------------------------------------------- base selection
#
# Without --base, each file picks its own base. Every model in the archive with tensors that match this file's (see
# match_against_base) is a candidate. A few windows of the file are coded against each candidate's matching bytes
# with cheap estimates: the entropy of the ans16 residual given its context, or zstd of the XOR. The candidate with
# the smallest estimated size wins if it beats storing the file standalone by at least MIN_BASE_GAIN.

SAMPLE_WINDOWS = 16  # windows per file, spread evenly over its tensor bytes
SAMPLE_BYTES = 512 << 10
FIRST_ROUND = 4  # every candidate is scored on this many windows first ...
FINALISTS = 4  # ... and only this many of the best on all of them
MIN_BASE_GAIN = 0.01  # share of the standalone estimate a base must save


def entropy_bytes(context, symbol):
    """Bytes needed to code `symbol` given `context`: empirical conditional entropy, Miller-Madow corrected."""
    keys, counts = np.unique((context.astype(np.int64) << 32) | symbol.astype(np.int64), return_counts=True)
    _, group = np.unique(keys >> 32, return_inverse=True)
    totals = np.bincount(group, weights=counts)
    bits = np.sum(counts * np.log2(totals[group] / counts)) + (keys.size - totals.size) / (2 * np.log(2))
    return bits / 8


def standalone_estimate(data, dtype):
    """Estimated size of data stored without a base: for floats, the order-0 entropy of each byte plane (about what
    codec 1 reaches); for anything else, zstd's at a fast level."""
    if dtype in FLOAT_DTYPES:
        planes, tail = byte_planes(data, dtype)
        zero = np.zeros(planes[0].size, np.int64)
        return sum(entropy_bytes(zero, plane) for plane in planes) + len(tail)
    return len(zstandard.ZstdCompressor(level=3).compress(data))


def delta_estimate(data, base_raw, dtype, kind, base_dtype):
    """Estimated size of data coded against base_raw (the base tensor's bytes before any dtype conversion)."""
    if kind == "ans16":
        base = prediction_from_bytes(base_raw, base_dtype, dtype)
        shift, mask = ANS16_EXPONENT[dtype]
        return entropy_bytes((base >> shift) & mask, np.frombuffer(data, "<u2") - base)
    if base_dtype:
        base_raw = prediction_from_bytes(base_raw, base_dtype, dtype).tobytes()
    return len(zstandard.ZstdCompressor(level=3).compress(xor_bytes(data, base_raw)))


def sample_windows(tensors):
    """[(tensor, offset in it, length, weight)]: up to SAMPLE_WINDOWS windows spread evenly over the tensor bytes,
    each standing for `weight` of them. A window starts at its tensor's start or at a whole number of ans16 blocks
    into it, where a candidate stored as an ans16 delta usually starts a region, so only a prefix is decoded."""
    sized = [t for t in tensors if t[3] - t[2] >= 4]
    ends = list(itertools.accumulate(t[3] - t[2] for t in sized))
    if not ends:
        return []
    step, block, picked = ends[-1] / SAMPLE_WINDOWS, 2 * ANS16_BLOCK_ELEMS, {}
    for j in range(SAMPLE_WINDOWS):
        pos = (j + 0.5) * step
        i = bisect.bisect_right(ends, pos)
        t = sized[i]
        rel = int(pos - (ends[i] - (t[3] - t[2]))) // block * block
        picked[(i, rel)] = picked.get((i, rel), 0) + step
    windows = []
    for (i, rel), weight in picked.items():
        length = min(SAMPLE_BYTES, sized[i][3] - sized[i][2] - rel) // 4 * 4
        if length:
            windows.append((sized[i], rel, length, weight))
    return windows


def file_reader(path):
    """read(start, length) -> those bytes of a file on disk."""
    def read(start, length):
        with open(path, "rb") as f:
            f.seek(start)
            return f.read(length)

    return read


def choose_base(archive_root, fmt, tensors, read, manifests, blocked, delta_codec):
    """(best repo or None, [(repo, estimated share)] of the finalists, standalone share, candidate count) for one file
    of format `fmt` with these tensors, whose bytes read(start, length) returns (from disk, or from the archive).
    Shares are estimated stored size / original size of the file's tensor data."""
    if not tensors:
        return None, [], None, 0
    candidates = []
    for repo, manifest in manifests.items():
        files = [e for e in manifest["files"] if e.get("format") == fmt and e.get("tensors")]
        if repo not in blocked and files:
            match = match_against_base(tensors, files, delta_codec)
            if any(match(t) for t in tensors):
                candidates.append((repo, files, match))
    windows = sample_windows(tensors) if candidates else []
    if not windows:
        return None, [], None, len(candidates)
    data = [read(t[2] + rel, length) for t, rel, length, _ in windows]
    alone =[standalone_estimate(d, w[0][1]) / len(d) for w, d in zip(windows, data)]  # stored bytes per byte
    weight = sum(w[3] for w in windows)
    score = {c[0]: 0.0 for c in candidates}

    def window_cost(candidate, k):
        repo, files, match = candidate
        t, rel, length, w = windows[k]
        m = match(t)
        covered = min(m[4] - rel, length) if m else 0
        if covered <= 0:
            return alone[k] * w
        index, base_start, kind, base_dtype, _ = m
        scale = FLOAT_BYTES[base_dtype] // FLOAT_BYTES[t[1]] if base_dtype else 1
        base_raw = read_range(archive_root, repo, files[index], base_start + rel * scale, covered * scale)
        coded = min(delta_estimate(data[k][:covered], base_raw, t[1], kind, base_dtype), alone[k] * covered)
        return (coded + alone[k] * (length - covered)) / length * w

    def run(picks, group):
        for k in picks:  # window by window, so candidates that share a base reuse its decoded regions
            for c in group:
                if score[c[0]] is not None:
                    try:
                        score[c[0]] += window_cost(c, k)
                    except (OSError, ValueError, KeyError) as e:
                        print(f"    candidate {c[0]} skipped: {e}")
                        score[c[0]] = None

    first = list(range(0, len(windows), -(-len(windows) // FIRST_ROUND)))
    run(first, candidates)
    finalists = sorted((c for c in candidates if score[c[0]] is not None), key=lambda c: score[c[0]])[:FINALISTS]
    run([k for k in range(len(windows)) if k not in first], finalists)
    depth = chain_depths(manifests)  # on a tie, the base that is quicker to restore
    ranking = sorted(((c[0], score[c[0]] / weight) for c in finalists if score[c[0]] is not None),
                     key=lambda r: (r[1], depth[r[0]]))
    standalone = sum(a * w[3] for a, w in zip(alone, windows)) / weight
    best = ranking[0][0] if ranking and ranking[0][1] <= standalone * (1 - MIN_BASE_GAIN) else None
    return best, ranking, standalone, len(candidates)


def pick_base(args, rec, fmt, tensors, read, manifests, blocked):
    t0 = time.perf_counter()
    best, ranking, standalone, n = choose_base(args.archive_root, fmt, tensors, read, manifests, blocked, args.delta_codec)
    if standalone is not None:
        shown = [f"{r} {share:.1%}" for r, share in ranking if r != best][:2] + [f"standalone {standalone:.1%}"]
        head = f"{best} (est. {dict(ranking)[best]:.1%}; " if best else "none ("
        print(f"  base for {rec['path']}: {head}{'; '.join(shown)}; {n} candidate(s), {time.perf_counter() - t0:.0f}s)")
    return best


def repo_needs(manifests):
    """{repo: the other repos its files refer to}."""
    return {r: {ref[0] for e in m["files"] for ref in references(e)} - {r} for r, m in manifests.items()}


def chain_depths(manifests):
    """{repo: how many repos deep its references go} (0: it needs no other repo)."""
    needs, depth = repo_needs(manifests), {}

    def walk(repo, trail):
        if repo in trail:
            return MAX_REFERENCE_DEPTH
        if repo not in depth:
            depth[repo] = max((1 + walk(r, trail | {repo}) for r in needs[repo] if r in needs), default=0)
        return depth[repo]

    for repo in needs:
        walk(repo, frozenset())
    return depth


def dependents(manifests, repo):
    """Every repo in the archive that needs `repo` to be restored, directly or through other repos."""
    needs = repo_needs(manifests)
    found, frontier = set(), {repo}
    while frontier:
        frontier = {r for r, refs in needs.items() if refs & frontier} - found - {repo}
        found |= frontier
    return found


# ----------------------------------------------------------------------------------------------- pack / verify / restore


def load_manifest(archive_root, repo_id):
    """The manifest of a model, checked (manifest_problem) and confirmed to be that model's: ValueError otherwise."""
    problem = name_problem(repo_id)
    if problem:
        raise ValueError(problem)
    path = os.path.join(archive_root, repo_dir_name(repo_id), "manifest.json")
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    problem = manifest_problem(manifest)
    if not problem and manifest["repo"] != repo_id:
        problem = f"this folder holds the model {manifest['repo']!r}, not {repo_id!r}"
    if problem:
        raise ValueError(f"{path}: {problem}")
    return manifest


def references(entry):
    """(repo, path, sha256) of every archived file this entry needs in order to be restored."""
    if entry["method"] == "same":
        return [(entry["same_as"]["repo"], entry["same_as"]["path"], entry["sha256"])]
    return [(entry["delta_base"], f["path"], f["sha256"]) for f in entry.get("delta_files", [])]


def chain_problem(archive_root, repo, entry, trail=()):
    """Check stored hashes of this entry and of every file it depends on, before any decoder sees the bytes."""
    here = f"{repo}:{entry['path']}"
    if here in trail:
        return "cyclic reference: " + " -> ".join(trail[trail.index(here):] + (here,))
    if len(trail) > MAX_REFERENCE_DEPTH:
        return "reference chain too deep"
    trail += (here,)
    if entry.get("stored"):
        try:
            digest = sha256_file(os.path.join(archive_root, repo_dir_name(repo), *entry["stored"].split("/")))
        except OSError as e:
            return f"stored file {entry['stored']} missing or unreadable ({e.strerror or e})"
        if digest != entry["stored_sha256"]:
            return "stored file corrupted (stored_sha256 mismatch)"
    for ref_repo, ref_path, ref_sha in references(entry):
        try:
            ref_entry = find_entry(archive_root, ref_repo, ref_path)
        except (OSError, ValueError):
            return f"{ref_repo}:{ref_path}, which it depends on, is missing from the archive"
        if ref_entry["sha256"] != ref_sha:
            return f"{ref_repo}:{ref_path}, which it depends on, has a different sha256 than recorded"
        problem = chain_problem(archive_root, ref_repo, ref_entry, trail)
        if problem:
            return problem if problem.startswith("cyclic") else f"{ref_repo}:{ref_path}: {problem}"
    return None


def verify_entry(archive_root, repo, entry):
    try:
        problem = chain_problem(archive_root, repo, entry)
    except Exception as e:  # a malformed entry: report it like any other problem
        return f"cannot check it: {e!r}"
    if problem:
        return problem
    h, size = hashlib.sha256(), 0
    try:
        for chunk in iter_entry_bytes(archive_root, repo, entry):
            h.update(chunk)
            size += len(chunk)
    except Exception as e:
        return f"decode failed: {e}"
    if size != entry.get("size") or h.hexdigest() != entry.get("sha256"):
        return "restored bytes differ from original sha256"
    return None


def physical_index(manifests, blocked):
    """sha256 -> (repo, path) of every file stored in the archive (raw or container), except in blocked repos."""
    index = {}
    for repo, manifest in manifests.items():
        if repo not in blocked:
            for e in manifest["files"]:
                if e["method"] != "same":
                    index.setdefault(e["sha256"], (repo, e["path"]))
    return index


def delta_layout(tensors, fmt, size, chunk_size, base_files, delta_codec):
    """(regions of a parsed file whose "base" references number the base files they use, those base files) for a
    delta against base_files, or (None, []) if none of its tensors is found there."""
    candidates = [b for b in base_files if b.get("format") == fmt and b.get("tensors")] if tensors is not None else []
    regions = tensor_regions(tensors, size, chunk_size, match_against_base(tensors, candidates, delta_codec)) if candidates else None
    used = sorted({r["base"][0] for r in regions or [] if "base" in r})
    if not used:
        return None, []
    renumber = {old: new for new, old in enumerate(used)}
    for r in regions:
        if "base" in r:
            r["base"][0] = renumber[r["base"][0]]
    return regions, [candidates[i] for i in used]


def pack_file(args, src, rec, repo_path, base, base_files, delta_codec, chunk_size, same_index, stored=None, raw_stored=None):
    """Store one file and return its manifest entry, not yet verified. A file already stored elsewhere in the
    archive becomes a same-as reference; a parsed file whose tensors appear in the base repo (by name, in any of its
    files: base_files) becomes a delta against those tensors; anything else is stored standalone. A container is
    written to `stored` (default: <path>.ocz), a small file stored as-is to `raw_stored` (default: <path>)."""
    entry = dict(rec)
    levels = (args.zstd_level, args.zstd_tensor_level)
    fmt, tensors, regions = file_regions(src, rec["size"], chunk_size)
    if tensors is not None:
        entry.update(format=fmt, tensors=tensors)
    if rec["sha256"] in same_index and rec["size"] > RAW_COPY_LIMIT:  # tiny files: a reference is not worth the dependency
        ref_repo, ref_path = same_index[rec["sha256"]]
        entry.update(method="same", same_as={"repo": ref_repo, "path": ref_path}, stored=None, stored_size=0, stored_sha256=None)
        return entry
    if rec["size"] <= RAW_COPY_LIMIT and fmt in ("auto", "zstd"):
        entry.update(stored=raw_stored or portable_path(rec["path"]), method="raw")
        dst = os.path.join(repo_path, *entry["stored"].split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as f, open(dst + ".part", "wb") as out:
            shutil.copyfileobj(f, out)
            out.flush()
            os.fsync(out.fileno())  # on disk before a manifest can point at it, as for a container
        os.replace(dst + ".part", dst)
    else:
        entry["stored"] = stored or portable_path(rec["path"]) + ".ocz"
        dst = os.path.join(repo_path, *entry["stored"].split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        delta_regions, used = delta_layout(tensors, fmt, rec["size"], chunk_size, base_files, delta_codec)
        if used:
            files = [{"path": f["path"], "sha256": f["sha256"]} for f in used]
            delta = {"repo": base, "files": files, "op": delta_codec}
            for f in files:  # kept for the verification that follows; cmd_pack releases them
                key = (base, f["path"], f["sha256"])
                if key not in _BASE_CACHE:
                    _BASE_CACHE[key] = materialize_file(args.archive_root, base, f["path"], f["sha256"])
            base_paths = [_BASE_CACHE[(base, f["path"], f["sha256"])] for f in files]
            codecs_used = write_container(src, dst, delta_regions, levels, delta, base_paths, args.workers)
            matched = sum(r["length"] for r in delta_regions if "base" in r)
            kind = "ans16" if any(r["kind"] == "ans16" for r in delta_regions) else "xor"
            entry.update(method=f"{fmt}+{kind}", delta_base=base, delta_files=files, matched_bytes=matched)
        else:
            codecs_used = write_container(src, dst, regions, levels)
            entry["method"] = fmt
        entry["frame_codecs"] = {
            CODEC_NAMES[c & ~STANDALONE] + ("/standalone" if c & STANDALONE else ""): n for c, n in sorted(codecs_used.items())
        }
    dst = os.path.join(repo_path, *entry["stored"].split("/"))
    entry["stored_size"] = os.path.getsize(dst)
    entry["stored_sha256"] = sha256_file(dst)
    return entry


def remove_orphans(repo_path, old_entries, entries):
    """After a re-pack, delete the files the previous manifest stored that the new one no longer uses (the old
    containers, which were stored again under the other name, or turned into same-as references). Nothing else in
    the directory is touched, so files added by other tools (recovery data, notes) stay, though they may now describe
    outdated contents."""
    keep = {os.path.normcase(os.path.normpath(e["stored"])) for e in entries if e.get("stored")}
    removed = []
    for e in old_entries:
        rel = e.get("stored")
        if rel and os.path.normcase(os.path.normpath(rel)) not in keep and os.path.isfile(os.path.join(repo_path, rel)):
            os.remove(os.path.join(repo_path, rel))
            removed.append(rel)
            print(f"  removed {rel} (no longer used by the manifest)")
    prune_empty_folders(repo_path, removed)


def remove_unreferenced(archive_root, repo, paths):
    """For a pack or rebase that stopped early: delete these new stored files (and their .part files), unless the
    manifest on disk already uses them."""
    repo_path = os.path.join(archive_root, repo_dir_name(repo))
    try:
        in_use = {os.path.normcase(os.path.join(repo_path, *e["stored"].split("/")))
                  for e in load_manifest(archive_root, repo)["files"] if e.get("stored")}
    except FileNotFoundError:
        in_use = set()  # a first pack: no manifest refers to anything yet
    except (OSError, ValueError):
        return  # cannot tell: leave them
    for path in paths:
        for p in (path, path + ".part"):
            if os.path.normcase(p) not in in_use and os.path.exists(p):
                os.remove(p)
    prune_empty_folders(repo_path, [os.path.relpath(p, repo_path).replace(os.sep, "/") for p in paths])


def excluded(rel, patterns):
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1], p) for p in patterns)


def source_files(source, patterns):
    """(files, problems). files: every regular file under `source` (relative POSIX paths, sorted; a link to a file
    counts as that file) except tool bookkeeping and --exclude matches. problems: what cannot be packed as it is: a
    link to a folder (or a junction: never followed, so nothing outside `source` is packed by accident, and nothing
    in it is skipped silently either) unless --exclude names it, and anything that is not a regular file (a FIFO,
    socket or device would block or never end; a broken link has nothing to read)."""
    found, problems = [], []
    is_junction = getattr(os.path, "isjunction", lambda p: False)
    for dirpath, dirnames, names in os.walk(source):
        rel = lambda n: os.path.relpath(os.path.join(dirpath, n), source).replace(os.sep, "/")  # noqa: E731
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if d in SKIP_DIRS:
                continue
            if os.path.islink(full) or is_junction(full):
                if not excluded(rel(d), patterns):
                    problems.append(f"{rel(d)}/ is a link to a folder")
            else:
                keep.append(d)
        dirnames[:] = keep
        for n in sorted(names):
            if n in SKIP_FILES or excluded(rel(n), patterns):
                continue
            try:
                regular = stat.S_ISREG(os.stat(os.path.join(dirpath, n)).st_mode)
            except OSError as e:
                problems.append(f"{rel(n)} cannot be read ({e.strerror or e})")
                continue
            if regular:
                found.append(rel(n))
            else:
                problems.append(f"{rel(n)} is not a regular file")
    return found, problems


LOCK_NAME = ".overcompressing.lock"  # empty file at the archive root; the lock is the OS's, on this file
_HELD_LOCK = []  # the open lock file, kept open (so locked) until the program ends

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _LockFileEx = _kernel32.LockFileEx
    _LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(_Overlapped)]
    _LockFileEx.restype = wintypes.BOOL
    _kernel32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _kernel32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPDWORD,
                                                wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPWSTR, wintypes.DWORD]
    ERROR_LOCK_VIOLATION = 33

    def _try_lock(fd, exclusive):
        """True if locked, False if someone else holds the lock; OSError if this file system cannot lock."""
        flags = 1 | (2 if exclusive else 0)  # LOCKFILE_FAIL_IMMEDIATELY, LOCKFILE_EXCLUSIVE_LOCK
        if _LockFileEx(msvcrt.get_osfhandle(fd), flags, 0, 1, 0, ctypes.byref(_Overlapped())):
            return True
        error = ctypes.get_last_error()
        if error == ERROR_LOCK_VIOLATION:
            return False
        raise ctypes.WinError(error)

    def read_only_volume(path):
        """Whether Windows reports the volume holding `path` as read-only (a CD/DVD/BD, write-protected media)."""
        root, flags = ctypes.create_unicode_buffer(32768), wintypes.DWORD()
        return bool(_kernel32.GetVolumePathNameW(os.path.abspath(path), root, len(root))
                    and _kernel32.GetVolumeInformationW(root, None, 0, None, None, ctypes.byref(flags), None, 0)
                    and flags.value & 0x00080000)  # FILE_READ_ONLY_VOLUME
else:
    import errno
    import fcntl

    def _try_lock(fd, exclusive):
        """True if locked, False if someone else holds the lock; OSError if this file system cannot lock."""
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            return True
        except OSError as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return False
            raise

    def read_only_volume(path):
        """Whether the file system holding `path` is mounted read-only (an optical disc, a read-only mount)."""
        try:
            return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
        except OSError:
            return False


def lock_archive(archive_root, exclusive, skip=False):
    """Lock the whole archive until the program ends: pack and rebase alone (exclusive), so two of them never plan
    the same names or replace each other's manifest; verify and restore together (shared), so they never read a
    file a pack is about to delete. Never waits: if the lock is held, the program stops. A reader opens an existing
    lock file read-only, so whoever owns it and whatever its permissions, the lock still counts. A reader that cannot
    lock at all goes on only if the system reports the archive's volume read-only (nothing can change it then), or
    with --no-lock (skip): its user's word that nothing changes the archive meanwhile."""
    if skip:
        print("not locking the archive (--no-lock): nothing may change it while this runs")
        return
    path = os.path.join(archive_root, LOCK_NAME)
    fd = None
    try:
        if not exclusive:
            try:
                fd = os.open(path, os.O_RDONLY)
            except FileNotFoundError:
                pass
        if fd is None:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        locked = _try_lock(fd, exclusive)
    except OSError as e:
        if fd is not None:
            os.close(fd)
        if not exclusive and read_only_volume(archive_root):
            return  # read-only media: nothing can change the archive
        sys.exit(f"cannot lock the archive ({path}: {e.strerror or e})" + ("" if exclusive else
                 "; if you are sure nothing changes the archive while this runs, add --no-lock"))
    if not locked:
        os.close(fd)
        sys.exit("the archive is in use by another pack, rebase, verify or restore: run this when it has finished"
                 if exclusive else "the archive is being changed by another pack or rebase: run this when it has finished")
    _HELD_LOCK.append(fd)


def cmd_pack(args):
    source = os.path.abspath(args.source_dir)
    if not os.path.isdir(source):
        sys.exit(f"{source} is not a folder")
    name = args.name or os.path.basename(source)
    problem = name_problem(name)
    if problem:
        sys.exit(problem + ("" if args.name else " (taken from the folder name: choose one with --name)"))
    paths, problems = source_files(source, args.exclude)
    if problems:
        sys.exit(f"cannot pack {source} as it is:\n  " + "\n  ".join(problems)
                 + "\nskip these with --exclude (e.g. --exclude \"" + problems[0].split(" ")[0].rstrip("/")
                 + "\"), or pack what a folder link points to as a folder of its own")
    if not paths:
        sys.exit(f"no files to pack in {source}")
    print(f"hashing {len(paths)} files in {source}")
    records = []
    for rel in paths:
        full = os.path.join(source, *rel.split("/"))
        records.append({"path": rel, "size": os.path.getsize(full), "sha256": sha256_file(full)})
    staged = {"repo": name, "revision": args.revision, "files": records}
    repo_path = os.path.join(args.archive_root, repo_dir_name(staged["repo"]))
    os.makedirs(args.archive_root, exist_ok=True)
    lock_archive(args.archive_root, exclusive=True)  # from reading the manifests to the last cleanup
    manifests = all_manifests(args.archive_root)
    if name not in manifests and os.path.isdir(repo_path) and any(files for _, _, files in os.walk(repo_path)):
        sys.exit(f"{repo_path} already holds files but is not the folder of a model named {name!r}: choose another --name")
    lost = files_others_need(manifests, name, records)
    if lost:
        users = sorted({u for _, us in lost for u in us})
        listed = "\n".join(f"  {path}  (needed by {', '.join(us)})" for path, us in lost)
        sys.exit(f"re-packing {name} would change or drop files other models need to be restored:\n{listed}\n"
                 f"pack it under another name, or first store those models without it, e.g.\n"
                 f"  python -I llm_archive.py rebase {quoted(args.archive_root)} {' '.join(map(quoted, users))} "
                 f"--no-base --no-dedupe")
    os.makedirs(repo_path, exist_ok=True)
    old_entries = manifests[name]["files"] if name in manifests else []
    # As in rebase, a re-pack stores every file under a name the current manifest does not use, so the archive stays
    # complete until the new manifest replaces it. Only a file stored as-is may be written over its old copy, and only
    # when the bytes are the same.
    kept_raw = {e["path"]: e for e in old_entries if e["method"] == "raw" and e.get("stored")}
    names = StoredNames([e["stored"] for e in old_entries if e.get("stored")], on_disk=repo_path)
    chunk_size = args.chunk_mib << 20
    blocked = {name} | dependents(manifests, name)  # using any of these as a base or reference would make a cycle
    if args.base and args.base not in manifests:
        sys.exit(f"--base {args.base}: no model of that name in {args.archive_root}")
    if args.base in blocked:
        sys.exit(f"--base {args.base}: it needs {name} to be restored, so it cannot be its base")
    same_index = physical_index(manifests, blocked) if args.dedupe else {}
    entries = []
    how = f"delta base: {args.base}" if args.base else "no delta base" if args.no_base else "delta base chosen per file"
    print(f"packing {name} -> {repo_path}  ({how})")
    pending = []  # names this run may have written that no manifest refers to yet
    try:
        for rec in staged["files"]:
            src = os.path.join(source, *rec["path"].split("/"))
            t0 = time.perf_counter()
            old = kept_raw.get(rec["path"])
            if old and old["sha256"] == rec["sha256"] and old["size"] == rec["size"]:
                # stored as-is before, with the same bytes: keep that stored file instead of copying the source over
                # it (the source may have changed since it was hashed); a damaged copy is stored again below
                entry = {**rec, **{k: old[k] for k in ("method", "stored", "stored_size", "stored_sha256")}}
                problem = verify_entry(args.archive_root, name, entry)
                if not problem:
                    print_entry(rec, entry, t0)
                    entries.append(entry)
                    continue
                print(f"    the stored copy of {rec['path']} is damaged ({problem}); storing it again")
            base = args.base
            if not base and not args.no_base and not (rec["size"] > RAW_COPY_LIMIT and rec["sha256"] in same_index):
                fmt, tensors = file_tensors(src, rec["size"])
                base = pick_base(args, rec, fmt, tensors, file_reader(src), manifests, blocked)
            base_files = manifests[base]["files"] if base else []
            # new files only ever go under names nothing in the folder uses yet
            container = names.fresh(rec["path"])
            raw = names.fresh(rec["path"], "") if rec["size"] <= RAW_COPY_LIMIT else None
            pending += [os.path.join(repo_path, *n.split("/")) for n in (container, raw) if n]
            entry, problem = pack_verified(args, src, rec, repo_path, name, base, base_files, chunk_size, same_index, container, raw)
            if problem:
                sys.exit(f"  VERIFY FAILED {rec['path']}: {problem}")
            print_entry(rec, entry, t0)
            entries.append(entry)
            if entry["stored"]:
                names.add(entry["stored"])  # not for another file of this folder
        manifest = {
            "format": "overcompressing-archive",
            "format_version": FORMAT_VERSION,
            "tool": TOOL_VERSION,
            "created": now(),
            "repo": staged["repo"],
            "revision": staged["revision"],
            "base_choice": base_choice(args),
            "files": entries,
        }
        write_manifest(repo_path, manifest)
        pending.clear()
        remove_orphans(repo_path, old_entries, entries)
    finally:
        if pending:  # stopped early: delete the new files, unless the manifest on disk already uses them
            remove_unreferenced(args.archive_root, name, pending)
    total, stored = sum(e["size"] for e in entries), sum(e["stored_size"] for e in entries)
    print(f"  total {human(total)} -> {human(stored)}" + (f" = {stored / total * 100:.2f}%" if total else ""))


def files_others_need(manifests, repo, records):
    """[(path, [models])] of the files of `repo` that other models refer to (same-as or delta) and that `records`
    (the files about to be packed) would change or drop."""
    have = {(r["path"], r["sha256"]) for r in records}
    lost = {}
    for other, manifest in manifests.items():
        for e in manifest["files"] if other != repo else ():
            for ref_repo, path, sha in references(e):
                if ref_repo == repo and (path, sha) not in have:
                    lost.setdefault(path, set()).add(other)
    return sorted((path, sorted(users)) for path, users in lost.items())


def quoted(text):
    return f'"{text}"' if any(ch in text for ch in " ()[]&;") else text


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def base_choice(args):
    """How a model's bases were chosen, kept in its manifest: rebase --all re-chooses only those chosen "auto"."""
    return "forced" if args.base else "none" if args.no_base else "auto"


def pack_verified(args, src, rec, repo_path, repo, base, base_files, chunk_size, same_index, stored=None, raw_stored=None):
    """pack_file, then decode the stored file back from disk and compare it with the original SHA-256; an ans16
    delta that fails is redone as an XOR delta. Returns (entry, problem or None)."""
    try:
        entry = pack_file(args, src, rec, repo_path, base, base_files, args.delta_codec, chunk_size, same_index, stored, raw_stored)
        if entry["method"] == "same":
            # refer to an identical file only if it is intact: the stored hashes along its chain are checked (decoding
            # it again would take as long as packing); if it is damaged, this file is stored itself instead
            problem = chain_problem(args.archive_root, repo, entry)
            if problem:
                print(f"    {entry['same_as']['repo']}:{entry['same_as']['path']} is damaged ({problem}); storing "
                      f"{rec['path']} itself instead (run verify)")
                entry = pack_file(args, src, rec, repo_path, base, base_files, args.delta_codec, chunk_size, {}, stored, raw_stored)
        problem = verify_entry(args.archive_root, repo, entry) if entry["method"] != "same" else None
        if problem and entry.get("delta_base") and args.delta_codec == "ans16":
            print(f"    ans16 failed verification ({problem}); redoing this file as an XOR delta")
            entry = pack_file(args, src, rec, repo_path, base, base_files, "xor", chunk_size, same_index, stored, raw_stored)
            problem = verify_entry(args.archive_root, repo, entry)
    finally:
        release_cached_bases()
    return entry, problem


def print_entry(rec, entry, t0):
    if entry["method"] == "same":
        note = f"same as {entry['same_as']['repo']}:{entry['same_as']['path']}"
    else:
        note = "verified"
        if entry.get("matched_bytes") is not None:
            note += f", {entry['matched_bytes'] / rec['size'] * 100:.0f}% matched in {entry['delta_base']}"
    print(
        f"  {rec['path']:<34} {entry['method']:<18} {human(rec['size']):>12} -> {human(entry['stored_size']):>12}"
        f"  {entry['stored_size'] / max(rec['size'], 1) * 100:6.2f}%  {note}  ({time.perf_counter() - t0:.0f}s)"
    )


def write_manifest(repo_path, manifest):
    """Replace manifest.json in one step: a crash leaves either the old manifest or the new one. The new one is
    written to a temporary file of its own, so no stored file can be in the way."""
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", suffix=".part", dir=repo_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(repo_path, "manifest.json"))
    except BaseException:
        os.remove(tmp)
        raise


# ----------------------------------------------------------------------------------------------- rebase
#
# rebase re-chooses the base of every file of a model already in the archive, as if it were packed again now. The base
# is chosen from the archived bytes (read_range), so a file is restored (into OC_TMP) only if its base changes. The
# new container is written beside the old one under another name, and the old one is deleted only after the new
# manifest has replaced the old manifest, so the archive is complete at every moment. Models that refer to the rebased
# one are unaffected: they name its files by path and original SHA-256, which do not change.


def name_key(name):
    """Two stored file names with the same key would be one file on Windows, which ignores case, so a model folder
    never holds both, whatever system wrote it."""
    return name.replace("\\", "/").casefold()


RESERVED_NAMES = {name_key("manifest.json")}  # the model folder's own file


class StoredNames:
    """Plans the names of the stored files of one model folder as a tree that follows the original paths, so that
    the folder can be copied to any system: every name valid on Windows and POSIX (windows_part); no two names that
    differ only in case; never a file where a folder of that name is, or the other way round; never manifest.json at
    the top; no name ending in .part, and no file whose <name>.part is used (it is written through that). A file or
    folder name that cannot be used gets .1, .2, ... It starts from the names the current manifest uses, which stay
    on disk until the new manifest replaces it."""

    def __init__(self, held=(), on_disk=None):
        """held: stored names of the current manifest; on_disk: the model folder, whose other files, links and
        folders (e.g. your own notes or recovery data) are never written over either."""
        self.files = set()  # name_key of every stored file
        self.folders = {}  # name_key of every stored folder -> its spelling
        self.folder_of = {}  # original folder path -> stored folder path
        for name in held:
            self.add(name)
        if on_disk and os.path.isdir(on_disk):
            for dirpath, dirnames, filenames in os.walk(on_disk):
                rel = os.path.relpath(dirpath, on_disk).replace(os.sep, "/")
                prefix = "" if rel == "." else rel + "/"
                for d in dirnames:
                    if os.path.islink(os.path.join(dirpath, d)):
                        self.add(prefix + d)  # a link to a folder: occupied like a file
                    else:
                        self.folders.setdefault(name_key(prefix + d), prefix + d)
                for f in filenames:
                    self.add(prefix + f)

    def add(self, name):
        """Reserve a stored file name and its folders."""
        parts = name.split("/")
        for i in range(1, len(parts)):
            folder = "/".join(parts[:i])
            self.folders.setdefault(name_key(folder), folder)
        self.files.add(name_key(name))

    def free(self, name, in_place=False):
        """Whether a file may be stored as `name` (in_place: it may already be one, being rewritten)."""
        key, temp = name_key(name), name_key(name + ".part")
        return (key not in RESERVED_NAMES and not key.endswith(".part") and key not in self.folders
                and temp not in self.files and temp not in self.folders and (in_place or key not in self.files))

    def folder(self, path):
        """The stored folder, ending in "/" (or ""), for the folders of the file at original `path`."""
        parts, stored = path.split("/")[:-1], ""
        for i, part in enumerate(parts):
            original = "/".join(parts[:i + 1])
            if original not in self.folder_of:
                base = stored + windows_part(part)
                for n in itertools.count():
                    name = f"{base}.{n}" if n else base
                    key = name_key(name)
                    if key in self.folders:  # that folder exists already: share it, spelled as it is
                        name = self.folders[key]
                        break
                    if key not in self.files and key not in RESERVED_NAMES and not key.endswith(".part"):
                        self.folders[key] = name
                        break
                self.folder_of[original] = name
            stored = self.folder_of[original] + "/"
        return stored

    def stem(self, path):
        """Where the file at original `path` is stored under its own (portable) name."""
        return self.folder(path) + windows_part(path.split("/")[-1])

    def fresh(self, path, suffix=".ocz"):
        """A name for the file at original `path` that no stored file or folder uses: <stem><suffix> or
        <stem>.<n><suffix> (suffix "" for a file stored as-is)."""
        stem = self.stem(path)
        for n in itertools.count():
            name = f"{stem}.{n}{suffix}" if n else f"{stem}{suffix}"
            if self.free(name):
                return name


def prune_empty_folders(repo_path, stored_paths):
    """Remove the folders of these stored paths that are now empty, deepest first (never repo_path itself)."""
    for rel in stored_paths:
        parts = rel.split("/")[:-1]
        while parts:
            try:
                os.rmdir(os.path.join(repo_path, *parts))
            except OSError:
                break  # not empty, or already gone
            parts.pop()


def space_problem(needs):
    """None, or which folder lacks room. needs: {folder: bytes it must take}; folders on one drive add up."""
    drives = {}
    for folder, n in needs.items():
        drives.setdefault(os.stat(folder).st_dev, [folder, 0])[1] += n
    for folder, n in drives.values():
        free = shutil.disk_usage(folder).free
        if free < n:
            return f"{folder} has {human(free)} free, about {human(n)} needed"
    return None


def restore_entry(archive_root, repo, entry, dst):
    """Write the original bytes of an archived file to dst. They go to a new temporary file in dst's folder first,
    which replaces dst only once its size and SHA-256 are right, so on a problem dst is left as it was. Returns a
    problem, or None."""
    problem = chain_problem(archive_root, repo, entry)
    if problem:
        return problem
    fd, tmp = tempfile.mkstemp(prefix=".oc-restore-", suffix=".part", dir=os.path.dirname(os.path.abspath(dst)))
    h, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in iter_entry_bytes(archive_root, repo, entry):
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
            f.flush()
            os.fsync(f.fileno())
        if size != entry["size"] or h.hexdigest() != entry["sha256"]:
            return "restored bytes differ from original sha256"
        os.replace(tmp, dst)
        tmp = None
        return None
    except Exception as e:
        return f"restore failed: {e}"
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def restore_target(dest_dir, parts):
    """Where restore writes the file with these path parts: inside dest_dir even after following any symlinked or
    junctioned folder on the way; ValueError otherwise."""
    dst = os.path.join(dest_dir, *parts)
    root = os.path.realpath(dest_dir)
    try:
        inside = os.path.commonpath([root, os.path.realpath(os.path.dirname(dst))]) == root
    except ValueError:  # another drive
        inside = False
    if not inside:
        raise ValueError(f"{'/'.join(parts)} would be written outside {dest_dir}")
    return dst


def target_key(path):
    """What a write to `path` really reaches: its folder with every symlink or junction resolved (paths through
    different links may name one file), its own name as is (os.replace replaces a link, it does not follow it), and
    case ignored where the system ignores it."""
    key = os.path.normcase(os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path)))
    return key.casefold() if sys.platform == "darwin" else key


def numbered(name, n):
    """name with " (n)" before its extension: model.safetensors -> model (1).safetensors."""
    stem, ext = os.path.splitext(name)
    return f"{stem} ({n}){ext}"


def restore_plan(dest_dir, entries):
    """[(entry, destination file, its path in dest_dir if that differs from the original path)]. A name this system
    cannot hold is changed (local_parts: "_" for each character it does not allow). A file that would land where
    another file or a folder of the model already goes (the same name after that change, names that differ only in
    case where case is ignored, or two paths that a link inside dest_dir joins) gets " (1)", " (2)", ... before its
    extension. Files whose names needed no change are placed first, so they keep them. The plan depends only on the
    manifest and the links in dest_dir, so a second restore places every file the same way. ValueError if a path
    would leave dest_dir."""
    parts = [local_parts(e["path"]) for e in entries]
    folders = {target_key(os.path.join(dest_dir, *p[:i])) for p in parts for i in range(1, len(p))}
    taken, plan = set(), [None] * len(entries)
    for i in sorted(range(len(entries)), key=lambda i: (parts[i] != entries[i]["path"].split("/"), i)):
        folder, name = parts[i][:-1], parts[i][-1]
        for n in itertools.count():
            where = folder + [numbered(name, n) if n else name]
            dst = restore_target(dest_dir, where)
            key = target_key(dst)
            if key not in taken and key not in folders:
                break
        taken.add(key)
        rel = "/".join(where)
        plan[i] = (entries[i], dst, rel if rel != entries[i]["path"] else None)
    return plan


def stored_description(entry):
    if entry["method"] == "same":
        return f"the same-as reference to {entry['same_as']['repo']}:{entry['same_as']['path']}"
    against = f" against {entry['delta_base']}" if entry.get("delta_base") else ""
    return f"{entry['stored']} ({human(entry['stored_size'])} as {entry['method']}{against})"


def rebase_model(args, repo):
    """Store every file of one model again as pack would now (re-choosing its delta base, and turning it into a
    same-as reference if an identical file is stored elsewhere), from the archive. Returns its stored size before and
    after."""
    forget_cached_reads()  # an earlier model of this run may have been rewritten
    manifests = all_manifests(args.archive_root)
    manifest = manifests[repo]
    old_entries = manifest["files"]
    blocked = {repo} | dependents(manifests, repo)  # using any of these as a base or reference would make a cycle
    if args.base in blocked:
        sys.exit(f"--base {args.base}: it needs {repo} to be restored, so it cannot be its base")
    repo_path = os.path.join(args.archive_root, repo_dir_name(repo))
    chunk_size = args.chunk_mib << 20
    forced = bool(args.base or args.no_base)
    same_index = physical_index(manifests, blocked) if args.dedupe else {}
    names = StoredNames([e["stored"] for e in old_entries if e.get("stored")], on_disk=repo_path)
    how =f"delta base: {args.base}" if args.base else "no delta base" if args.no_base else "delta base chosen per file"
    how += ("" if args.dedupe else ", no same-as references") + (", dry run" if args.dry_run else "")
    print(f"rebasing {repo}  ({how})")
    entries, pending, tmp, plain = [], [], None, 0  # pending: new stored files that no manifest refers to yet
    try:
        for old in old_entries:
            rec = {k: old[k] for k in ("path", "size", "sha256")}
            entries.append(old)
            same = old["method"] == "same"
            if same and args.dedupe:
                print(f"  {rec['path']:<34} unchanged (same-as reference)")
                continue
            if not same and rec["size"] > RAW_COPY_LIMIT and rec["sha256"] in same_index:
                ref_repo, ref_path = same_index[rec["sha256"]]
                if args.dry_run:
                    print(f"  {rec['path']:<34} would become a same-as reference to {ref_repo}:{ref_path}")
                    continue
                entry = {k: old[k] for k in ("path", "size", "sha256", "format", "tensors") if k in old}
                entry.update(method="same", same_as={"repo": ref_repo, "path": ref_path}, stored=None, stored_size=0, stored_sha256=None)
                t0 = time.perf_counter()
                problem = verify_entry(args.archive_root, repo, entry)  # its stored copy is about to be deleted
                if problem:
                    sys.exit(f"  VERIFY FAILED {rec['path']}: {problem}")
                print_entry(rec, entry, t0)
                print(f"    replaces {stored_description(old)}")
                entries[-1] = entry
                continue
            if not same and not old.get("tensors"):
                plain += 1  # no base can hold it: nothing to re-choose
                continue
            if forced or not old.get("tensors"):
                base = args.base
            else:
                read = lambda start, length, old=old: read_range(args.archive_root, repo, old, start, length)  # noqa: E731
                base = pick_base(args, rec, old["format"], old["tensors"], read, manifests, blocked)
            if not same and base == old.get("delta_base"):
                print(f"  {rec['path']:<34} unchanged ({'base is still ' + base if base else 'still no base'})")
                continue
            if args.dry_run:
                print(f"  {rec['path']:<34} would be {'stored again' if same else 're-packed'} "
                      f"{'against ' + base if base else 'without a base'}")
                continue
            base_files = manifests[base]["files"] if base else []
            old_base = {f["path"]: f["size"] for f in manifests.get(old.get("delta_base"), {"files": []})["files"]}
            reads_now = sum(old_base.get(f["path"], 0) for f in old.get("delta_files", []))
            reads_new = sum(f["size"] for f in delta_layout(old.get("tensors"), old.get("format"), rec["size"], chunk_size,
                                                            base_files, args.delta_codec)[1])
            tmp = tmp or tempfile.mkdtemp(prefix="oc-rebase-", dir=os.environ.get("OC_TMP") or None)
            # OC_TMP holds the restored file plus the base files its old or new delta reads; the new container is
            # at most about as big as the file
            problem = space_problem({tmp: rec["size"] + max(reads_now, reads_new), repo_path: rec["size"] + (1 << 20)})
            if problem:
                sys.exit(f"  not enough room to rebase {rec['path']}: {problem} (OC_TMP picks the temporary folder)")
            t0 = time.perf_counter()
            src = os.path.join(tmp, local_parts(rec["path"])[-1])  # keeps the extension file_regions looks at
            try:
                problem = restore_entry(args.archive_root, repo, old, src)
                if problem:
                    sys.exit(f"  cannot restore {rec['path']}: {problem}")
                stored = names.fresh(rec["path"])
                names.add(stored)
                pending.append(os.path.join(repo_path, *stored.split("/")))
                entry, problem = pack_verified(args, src, rec, repo_path, repo, base, base_files, chunk_size, {}, stored)
            finally:
                for path in (src, src + ".part"):
                    if os.path.exists(path):
                        os.remove(path)
            if problem:
                sys.exit(f"  VERIFY FAILED {rec['path']}: {problem}")
            print_entry(rec, entry, t0)
            if forced or same or entry["stored_size"] < old["stored_size"]:
                entries[-1] = entry
                print(f"    replaces {stored_description(old)}")
            else:
                os.remove(pending.pop())
                prune_empty_folders(repo_path, [entry["stored"]])
                print(f"    not smaller than {stored_description(old)}: kept that")
        if any(e is not o for e, o in zip(entries, old_entries)):
            write_manifest(repo_path, {**manifest, "tool": TOOL_VERSION, "base_choice": base_choice(args),
                                       "rebased": now(), "files": entries})
            pending.clear()
            remove_orphans(repo_path, old_entries, entries)
        elif not args.dry_run and manifest.get("base_choice", "auto") != base_choice(args):
            write_manifest(repo_path, {**manifest, "base_choice": base_choice(args)})  # so that --all respects it
    finally:
        if pending:  # stopped early: delete the new files, unless the manifest on disk already uses them
            remove_unreferenced(args.archive_root, repo, pending)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if plain:
        print(f"  {plain} file(s) without tensors unchanged")
    before, after = sum(e["stored_size"] for e in old_entries), sum(e["stored_size"] for e in entries)
    if args.dry_run:
        print(f"  stored {human(before)} (dry run: nothing changed)")
    else:
        change = f"{(after - before) / max(before, 1):+.1%}" if after != before else "unchanged"
        print(f"  stored {human(before)} -> {human(after)}  ({change})")
    return before, after


def cmd_rebase(args):
    if bool(args.repos) == args.all:
        sys.exit("name the models to rebase, or use --all")
    if args.all and args.base:
        sys.exit("--base needs the models named, not --all")
    if not os.path.isdir(args.archive_root):
        sys.exit(f"{args.archive_root} is not a folder")
    lock_archive(args.archive_root, exclusive=not args.dry_run)
    manifests = all_manifests(args.archive_root)
    if not manifests:
        sys.exit(f"no models found in {args.archive_root}")
    for repo in args.repos + ([args.base] if args.base else []):
        if repo not in manifests:
            sys.exit(f"no model named {repo} in {args.archive_root}")
    depth = chain_depths(manifests)
    repos = sorted(args.repos or manifests, key=lambda r: (depth[r], r))  # bases before the models that use them
    before = after = 0
    for repo in repos:
        choice = manifests[repo].get("base_choice", "auto")  # manifests older than rebase do not say: "auto"
        if args.all and not args.no_base and choice != "auto":
            option = "--no-base" if choice == "none" else "--base"
            print(f"skipping {repo}: its bases were set with {option} (name it to rebase it anyway)")
            continue
        b, a = rebase_model(args, repo)
        before, after = before + b, after + a
    if len(repos) > 1:
        print(f"total stored {human(before)}" + (" (dry run: nothing changed)" if args.dry_run else f" -> {human(after)}"))


def all_manifests(archive_root, problems=None):
    """{repo: manifest} of every model in the archive. A folder whose manifest cannot be read or trusted (see
    manifest_problem; its folder must also be named after its model) is added to `problems` as (folder, problem) if
    given; otherwise the program stops, since a model it cannot see may depend on the others."""
    manifests = {}
    for d in sorted(os.listdir(archive_root)) if os.path.isdir(archive_root) else ():
        path = os.path.join(archive_root, d, "manifest.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                manifest = json.load(f)
            problem = manifest_problem(manifest)
            if not problem and os.path.normcase(repo_dir_name(manifest["repo"])) != os.path.normcase(d):
                problem = f"it holds the model {manifest['repo']!r}, whose folder is {repo_dir_name(manifest['repo'])}"
        except (OSError, ValueError) as e:
            problem = f"cannot read it ({e})"
        if problem:
            if problems is None:
                sys.exit(f"{path}: {problem}\nfix it, or move that folder out of the archive, before changing the archive")
            problems.append((d, problem))
            continue
        manifests[manifest["repo"]] = manifest
    return manifests


def cmd_verify(args):
    if not os.path.isdir(args.archive_root):
        sys.exit(f"{args.archive_root} is not a folder: nothing verified")
    lock_archive(args.archive_root, exclusive=False, skip=args.no_lock)
    failures, problems, checked = 0, [], 0
    repos = args.repos or list(all_manifests(args.archive_root, problems))
    if not repos and not problems:
        sys.exit(f"no models found in {args.archive_root}: nothing verified")
    for folder, problem in problems:
        failures += 1
        print(f"  FAIL  {folder}/manifest.json  -- {problem}")
    for repo in repos:
        try:
            entries = load_manifest(args.archive_root, repo)["files"]
        except (OSError, ValueError) as e:
            failures += 1
            print(f"  FAIL  {repo}  -- cannot read its manifest: {e}")
            continue
        for entry in entries:
            problem = verify_entry(args.archive_root, repo, entry)
            failures += bool(problem)
            checked += 1
            print(f"  {'FAIL' if problem else 'ok  '}  {repo}:{entry['path']}" + (f"  -- {problem}" if problem else ""))
    if failures:
        sys.exit(f"{failures} FAILURE(S): replace the damaged files from another copy or repair them (see docs/protection.md)")
    if not checked:
        sys.exit("no files found: nothing verified")
    print(f"ALL VERIFIED ({checked} files)")


def cmd_restore(args):
    """Every path is placed (restore_plan) and checked before anything is written. A destination file that already
    holds the right bytes is kept; one with other contents is replaced only with --overwrite."""
    if os.path.isdir(args.archive_root):
        lock_archive(args.archive_root, exclusive=False, skip=args.no_lock)
    try:
        manifest = load_manifest(args.archive_root, args.repo)
        plan = restore_plan(args.dest_dir, manifest["files"])
    except (OSError, ValueError) as e:
        sys.exit(f"cannot restore {args.repo}: {e}")
    archive = os.path.realpath(args.archive_root)

    def in_archive(folder):
        try:
            return os.path.commonpath([archive, os.path.realpath(folder)]) == archive
        except ValueError:  # another drive
            return False

    if in_archive(args.dest_dir) or any(in_archive(os.path.dirname(dst)) for _, dst, _ in plan):
        sys.exit(f"refusing to restore into the archive itself ({args.archive_root}): choose a folder outside it "
                 "(--overwrite does not change this)")
    targets, conflicts = [], []
    for entry, dst, renamed in plan:
        shown = entry["path"] + (f"  (as {renamed})" if renamed else "")
        if os.path.isdir(dst) and not os.path.islink(dst):
            sys.exit(f"cannot restore {shown}: a folder of that name is in the way in {args.dest_dir}")
        present = False
        if os.path.lexists(dst):
            present = (os.path.isfile(dst) and not os.path.islink(dst) and os.path.getsize(dst) == entry["size"]
                       and sha256_file(dst) == entry["sha256"])
            if not present:
                conflicts.append(shown)
        targets.append((entry, dst, shown, present))
    if conflicts and not args.overwrite:
        sys.exit(f"{len(conflicts)} file(s) already exist in {args.dest_dir} with other contents, e.g. {conflicts[0]}: "
                 "restore into an empty folder, or add --overwrite to replace them")
    for entry, dst, shown, present in targets:
        if present:
            print(f"  ok    {shown}  (already there)")
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
        except OSError as e:
            sys.exit(f"  cannot restore {shown}: {e}")
        problem = restore_entry(args.archive_root, args.repo, entry, dst)
        if problem:
            sys.exit(f"  cannot restore {shown}: {problem}")
        print(f"  ok    {shown}")
    renamed = sum(1 for _, _, r in plan if r)
    if renamed:
        print(f"  {renamed} file(s) restored under another name: a character this system does not allow in names "
              "became \"_\", or a \" (n)\" keeps two files apart")
    print(f"restored {args.repo}" + (f" @ {manifest['revision']}" if manifest.get("revision") else "") + f" into {args.dest_dir}")


def whole_number(lo, hi):
    """argparse type: a whole number from lo to hi."""
    def parse(text):
        try:
            n = int(text)
        except ValueError:
            n = None
        if n is None or not lo <= n <= hi:
            raise argparse.ArgumentTypeError(f"must be a whole number from {lo} to {hi}, not {text!r}")
        return n
    return parse


def add_codec_options(s):
    s.add_argument("--delta-codec", choices=["ans16", "xor"], default="ans16",
                   help="for 16-bit float tensors found in the base: ans16 (smaller) or xor (faster)")
    s.add_argument("--workers", type=whole_number(1, 1024), default=None,
                   help="worker processes for ans16 (default: OC_WORKERS, else one per CPU)")
    s.add_argument("--chunk-mib", type=whole_number(1, 1024), default=64, help="maximum region size, 1-1024 (default 64)")
    s.add_argument("--zstd-level", type=whole_number(1, 22), default=19,
                   help="zstd level 1-22 for headers, metadata and text files (default 19)")
    s.add_argument("--zstd-tensor-level", type=whole_number(1, 22), default=9,
                   help="zstd level 1-22 for quantized tensors and unknown binaries (default 9; 19 is ~80x slower for ~0.1%%)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("pack", help="compress every file of a folder into the archive and verify it")
    s.add_argument("source_dir")
    s.add_argument("archive_root")
    s.add_argument("--name", help="name of this model in the archive, e.g. Qwen/Qwen2.5-7B (default: the folder name)")
    s.add_argument("--revision", help="free-text note stored in the manifest, e.g. the upstream commit")
    s.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                   help="skip files whose path or name matches (repeatable); .git/ and .cache/ are always skipped")
    base = s.add_mutually_exclusive_group()
    base.add_argument("--base", metavar="NAME",
                      help="store tensors as deltas against this model in the archive (default: each file picks the "
                           "model that compresses it best, by sampling)")
    base.add_argument("--no-base", action="store_true", help="store every file without a delta base")
    s.add_argument("--no-dedupe", dest="dedupe", action="store_false",
                   help="store files even if an identical file is already in the archive")
    add_codec_options(s)
    s.set_defaults(func=cmd_pack)
    s = sub.add_parser("rebase", help="re-choose the delta bases of archived models (restores only what it re-packs)")
    s.add_argument("archive_root")
    s.add_argument("repos", nargs="*", metavar="name")
    s.add_argument("--all", action="store_true",
                   help="every model whose bases were chosen automatically, bases before the models that use them")
    base = s.add_mutually_exclusive_group()
    base.add_argument("--base", metavar="NAME", help="re-pack every file against this model, even if it gets larger")
    base.add_argument("--no-base", action="store_true", help="re-pack every file without a delta base, even if it gets larger")
    s.add_argument("--no-dedupe", dest="dedupe", action="store_false",
                   help="store files that are same-as references to another model as files again")
    s.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    add_codec_options(s)
    s.set_defaults(func=cmd_rebase)
    no_lock = "do not lock the archive (only when it cannot be locked and nothing can change it while this runs)"
    s = sub.add_parser("verify", help="decompress everything in memory and check sha256")
    s.add_argument("archive_root")
    s.add_argument("repos", nargs="*")
    s.add_argument("--no-lock", action="store_true", help=no_lock)
    s.set_defaults(func=cmd_verify)
    s = sub.add_parser("restore", help="write a repo back out, byte-identical to the original")
    s.add_argument("archive_root")
    s.add_argument("repo")
    s.add_argument("dest_dir")
    s.add_argument("--overwrite", action="store_true",
                   help="replace files in dest_dir that have other contents (files already right are always kept)")
    s.add_argument("--no-lock", action="store_true", help=no_lock)
    s.set_defaults(func=cmd_restore)
    args = p.parse_args()
    workers = ans16_workers()  # checks OC_WORKERS before any command starts
    if getattr(args, "workers", 0) is None:
        args.workers = workers
    try:
        args.func(args)
    except OSError as e:  # e.g. a full disk: reported plainly; a pack or rebase has already removed its new files
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
