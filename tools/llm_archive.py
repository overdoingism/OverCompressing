"""Lossless archiver for model folders. safetensors and GGUF files are split per tensor (float tensors: byte planes,
the exponents entropy-coded; quantized tensors, metadata, text: zstd; other binaries: whichever is smaller). A tensor
that also exists in another model of the pack (same name, in any of its files) is stored as a delta against it (ans16
for 16-bit floats), so different file names, headers, shardings and tensor orders still share data. Each file picks
its base by sampling every model in the pack, unless --base names one or --no-base turns deltas off. See
ARCHIVE_FORMAT.md.

    python -I llm_archive.py pack    <source_dir> ... <pack_folder | new.oczip> [--name NAME] [--replace | --add]
                                     [--base MODEL | --no-base] [--exclude GLOB ... | --file PATH ...]
    python -I llm_archive.py list    <pack> [MODEL]
    python -I llm_archive.py verify  <pack> [MODEL ...]
    python -I llm_archive.py restore <pack> MODEL <dest_dir> [--overwrite] [--file PATH ...] [--flat]
    python -I llm_archive.py remove  <pack_folder> MODEL ... [--file PATH ...] [--dry-run]
    python -I llm_archive.py rename  <pack_folder> MODEL NEW_NAME | <pack_folder> MODEL --file PATH NEW_PATH
    python -I llm_archive.py rebase  <pack_folder> (MODEL ... | --all) [--base MODEL | --no-base] [--dry-run]
    python -I llm_archive.py export  <pack> <new.oczip> [MODEL ...] [--verify | --no-verify] [--delete]

A pack holds models. It is a folder (a pack folder: ocpack.json, models/, blobs/) or the same tree in one ZIP file (a
pack file, .oczip), which list, verify and restore read where it lies. Every file is stored once, as a blob named by
the SHA-256 of the original file and shared by every model that has it; a model is its manifest, the list of its
files. A blob coded as a delta names the blobs it is coded against, so removing a model deletes only the blobs no
remaining model needs. <pack> is a pack folder or a pack file; MODEL is a model's name (Org/Name).
llm_archive_gui.py is a small window for all of this.

The tool never downloads anything: get the model files however you like, then pack the folder. rebase re-chooses the
bases of models already in the pack (e.g. after a closer ancestor was packed, or a base was removed).

Every command prints one line per result (a file packed, verified or restored, a model listed) and a summary; with
--json each of these lines is a JSON object instead, for programs and agents. --progress (on by default when stderr
is a terminal) reports on stderr, about once a second, how far the current step is.

Damage is detected (SHA-256 / CRC) but not repaired here; see docs/protection.md.
ans16 deltas restore their base into a temporary file, and rebase the file it re-packs: OC_TMP picks their folder
(default: the system temp folder). OC_WORKERS caps the worker processes used by ans16 (default: one per CPU).
"""

import argparse
import bisect
import contextlib
import datetime
import errno
import fnmatch
import hashlib
import itertools
import json
import math
import multiprocessing
import os
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import zlib
from collections import deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import numpy as np
import zstandard

VERSION = "0.5"  # of this command-line tool (the window has its own); raised by hand for changes people see
FORMAT_VERSION = 4
SKIP_DIRS = {".git", ".cache", "__MACOSX"}  # bookkeeping of git / download tools / macOS unzipping: not model files
SKIP_FILES = {".hf_files.json", "thumbs.db", "desktop.ini", ".ds_store"}  # compared ignoring case: staging metadata of
# earlier versions of this tool, and the folder-view caches Windows and macOS leave everywhere
MAGIC = b"OCZ4"
CODEC_RAW, CODEC_PLANES, CODEC_ZSTD, CODEC_ANS16 = 0, 1, 2, 3
CODEC_NAMES = ["raw", "planes", "zstd", "ans16"]
FRAME_HEAD = struct.Struct("<BQQI")  # codec, raw_len, payload_len, crc32 of the original chunk
FLOAT_DTYPES = {"BF16": 2, "F16": 2, "F32": 4, "F8_E4M3": 1, "F8_E5M2": 1}  # float dtypes coded as byte planes


# ----------------------------------------------------------------------------------------------- output
#
# Results go to stdout, one line each; with --json every line is a JSON object {"event", "text", ...}, "text" being
# the line people would see. Progress goes to stderr, so a program reading the results never has to tell them apart.

JSON_OUT = False


def say(text, event="note", **fields):
    """One line of output: `text`, or with --json the object {"event": event, "text": text, **fields}."""
    line = json.dumps({"event": event, "text": text, **fields}) if JSON_OUT else text
    with PROGRESS.lock:
        PROGRESS.clear()
        print(line, flush=True)


# What failed, for programs: the "code" of the error event that ends --json output. The text is for people and may
# change; these do not. Only failures a caller can act on have a code of their own; the rest are "other".
ERROR_CODES = ("exists", "name_taken", "busy", "no_room", "damaged", "cancelled", "other")


class Failure(SystemExit):
    """sys.exit(text) of a failure with a code (ERROR_CODES) for programs."""

    def __init__(self, kind, text):
        super().__init__(text)
        self.kind = kind


def fail(kind, text):
    raise Failure(kind, text)


class Progress:
    """How far the running step is, reported on stderr about once a second by a thread of its own, so that a step
    with nothing to count (choosing a base, preparing one) still shows it is alive: the step, the bytes done of its
    total when that is known, what it is working on, and how long it has run. Silent unless started (--progress)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.tty = False
        self.step = self.item = self.total = None
        self.done, self.since, self.shown = 0, time.monotonic(), 0

    def start(self):
        """Report from now on: on a terminal one line rewritten in place, else a line each time (a JSON object with
        --json)."""
        self.tty = sys.stderr.isatty() and not JSON_OUT
        threading.Thread(target=self.run, daemon=True).start()

    def begin(self, step, total=None, item=None):
        """A new step, and the bytes it will count (None: not known)."""
        with self.lock:
            self.step, self.total, self.item, self.done, self.since = step, total, item, 0, time.monotonic()

    def at(self, item):
        with self.lock:
            self.item = item

    def add(self, n):
        with self.lock:
            self.done += n

    def end(self):
        with self.lock:
            self.step = None
            self.clear()

    def run(self):
        while True:
            time.sleep(1)
            try:
                self.report()
            except (OSError, ValueError):  # stderr went away (the window that ran this closed it)
                return

    def report(self):
        with self.lock:
            if self.step is None:
                return
            seconds = int(time.monotonic() - self.since)
            if JSON_OUT:
                line = json.dumps({"event": "progress", "step": self.step, "item": self.item, "done": self.done,
                                   "total": self.total, "seconds": seconds})
            else:
                parts = [self.step]
                if self.total:
                    parts.append(f"{min(self.done / self.total, 1):.0%} of {human(self.total)}")
                if self.item:
                    parts.append(self.item)
                line = "  ".join(parts + [f"{seconds // 60}:{seconds % 60:02d}"])
            if self.tty:
                line = line[:max(20, shutil.get_terminal_size().columns - 1)]
                sys.stderr.write("\r" + line + " " * max(0, self.shown - len(line)))
                self.shown = len(line)
            else:
                sys.stderr.write(line + "\n")
            sys.stderr.flush()

    def clear(self):
        """Take the progress line off a terminal before other output goes there (the lock is held)."""
        if self.tty and self.shown:
            sys.stderr.write("\r" + " " * self.shown + "\r")
            sys.stderr.flush()
            self.shown = 0


PROGRESS = Progress()


def repo_dir_name(repo_id):
    return repo_id.replace("/", "__")


NAME_CHARS = "-_.+@()[] "  # besides letters and digits (any script)
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def name_problem(repo):
    """None if `repo` can name a model, else why not. A name is one or more parts separated by "/" (e.g.
    Qwen/Qwen2.5-7B), each made of letters, digits and -_.+@()[] or spaces, not starting or ending with a space, not
    ending with a dot (so never "." or ".."), and not a Windows device name, so that its manifest file
    (models/<Org__Name>.json) can be named on every system."""
    if not isinstance(repo, str) or not repo:
        return "a model name cannot be empty"
    for part in repo.split("/"):
        if not part or part != part.strip(" ") or part.endswith("."):
            return f"bad model name {repo!r}: no part may be empty, start or end with a space, or end with a dot"
        if not all(ch.isalnum() or ch in NAME_CHARS for ch in part):
            return f"bad model name {repo!r}: use letters, digits, {NAME_CHARS.strip()} and spaces, with / between parts"
        if part.split(".")[0].upper() in WINDOWS_DEVICES:
            return f"bad model name {repo!r}: {part} is a reserved file name on Windows"
    if len(repo_dir_name(repo)) > 200:
        return f"bad model name {repo!r}: too long"
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


def local_parts(path):
    """The parts of an original path as this system can name them: changed by windows_part on Windows, as they are
    elsewhere (POSIX allows every character but "/" and NUL)."""
    parts = path.split("/")
    return [windows_part(p) for p in parts] if os.name == "nt" else parts


DELTA_FORMATS = ("safetensors", "gguf")
MANIFEST_FORMAT, PACK_FORMAT = "overcompressing-model", "overcompressing-pack"
BASE_CHOICES = ("auto", "forced", "none")


def is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def is_size(value):
    return type(value) is int and value >= 0


def tensors_problem(tensors, size):
    """None if `tensors` is a tensor table base selection can use: [name, dtype, start, end, shape] for each tensor,
    inside a file of `size` bytes; else why not."""
    if not isinstance(tensors, list):
        return "no tensor table"
    for t in tensors:
        if not (isinstance(t, list) and len(t) == 5 and isinstance(t[0], str) and isinstance(t[1], str)
                and is_size(t[2]) and is_size(t[3]) and t[2] <= t[3] <= size
                and isinstance(t[4], list) and all(is_size(d) for d in t[4])):
            return f"bad tensor {str(t)[:80]}"
    return None


def entry_problem(e):
    """None if a manifest entry can be used: a safe path, the original file's size and SHA-256 (which also name the
    blob that stores it) and, for a parsed file, its format and tensor table."""
    if not isinstance(e, dict):
        return "an entry of the manifest is not an object"
    problem = path_problem(e.get("path"))
    if problem:
        return problem
    where = repr(e["path"])
    if not is_size(e.get("size")) or not is_sha256(e.get("sha256")):
        return f"{where}: missing or bad size or sha256"
    if "format" in e or "tensors" in e:
        problem = "unknown format" if e.get("format") not in DELTA_FORMATS else tensors_problem(e.get("tensors"), e["size"])
        if problem:
            return f"{where}: {problem}"
    if e.get("base_choice", "auto") not in BASE_CHOICES:
        return f"{where}: unknown base_choice {e.get('base_choice')!r}"
    return None


def manifest_problem(manifest):
    """None if a manifest can be used: this format, its model name, every entry (entry_problem), and no path listed
    twice. Any JSON value gives an answer, never an exception."""
    try:
        return _manifest_problem(manifest)
    except (TypeError, ValueError, KeyError, AttributeError) as e:  # a shape the checks above did not foresee
        return f"malformed manifest ({type(e).__name__})"


def _manifest_problem(manifest):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return "malformed manifest: no list of files"
    if manifest.get("format") != MANIFEST_FORMAT or manifest.get("format_version") != FORMAT_VERSION:
        return f"not a model manifest of format version {FORMAT_VERSION}"
    problem = name_problem(manifest.get("name"))
    if problem:
        return problem
    if manifest.get("base_choice", "auto") not in BASE_CHOICES:
        return f"unknown base_choice {manifest.get('base_choice')!r}"
    seen = set()
    for e in manifest["files"]:
        problem = entry_problem(e)
        if problem:
            return problem
        if e["path"] in seen:
            return f"{e['path']!r} is listed twice"
        seen.add(e["path"])
    return None


def sha256_file(path, counted=False):
    with open(path, "rb") as f:
        return sha256_stream(f, counted)


def sha256_stream(f, counted=False):
    """counted: the bytes go to the progress report."""
    h = hashlib.sha256()
    for block in blocks_ahead(f):
        h.update(block)
        if counted:
            PROGRESS.add(len(block))
    return h.hexdigest()


def blocks_ahead(f, size=None, block=16 << 20):
    """The bytes of f from where it is (`size` of them, else to its end) in blocks, each read on a thread of its own
    while the caller works on the one before: reading and hashing (which lets other threads run) overlap."""
    left = size

    def read():
        nonlocal left
        n = block if left is None else min(block, left)
        data = f.read(n) if n else b""
        if left is not None:
            left -= len(data)
        return data

    with ThreadPoolExecutor(1) as reader:
        ahead = reader.submit(read)
        while data := ahead.result():
            ahead = reader.submit(read)
            yield data


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
            header = strict_json(f.read(n))
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
    out = bytearray(len(a))
    np.bitwise_xor(np.frombuffer(a, np.uint8), np.frombuffer(b, np.uint8), out=np.frombuffer(out, np.uint8))
    return out


def strict_json(data):
    """json.loads that refuses duplicate keys (two readers could disagree on which value counts), NaN and Infinity
    (not JSON) and, for bytes, anything that is not valid UTF-8 (ValueError either way)."""
    def pairs(items):
        if len({k for k, _ in items}) != len(items):
            raise ValueError("duplicate key in JSON")
        return dict(items)

    def constant(name):
        raise ValueError(f"{name} in JSON")  # not JSON (RFC 8259), though Python's json would accept it

    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data).decode("utf-8")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def zstd_frame_length(frame):
    """Length of the zstd frame that `frame` starts with, from its block headers (RFC 8878 section 3.1)."""
    if bytes(frame[:4]) != ZSTD_MAGIC or len(frame) < 6:
        raise ValueError("not a zstd frame")
    pos, checksum = zstandard.frame_header_size(frame), frame[4] & 0x04
    while True:
        if pos + 3 > len(frame):
            raise ValueError("zstd frame cut short")
        head = int.from_bytes(frame[pos:pos + 3], "little")
        kind = (head >> 1) & 3
        if kind == 3:
            raise ValueError("zstd frame with a reserved block type")
        pos += 3 + (1 if kind == 1 else head >> 3)  # an RLE block holds one byte
        if head & 1:
            return pos + (4 if checksum else 0)


def zstd_bounded(frame, limit):
    """The bytes of a payload that must be exactly one zstd frame (nothing after it) decoding to at most `limit`
    bytes. Never produces more than limit + 1 bytes whatever the frame claims; ValueError otherwise."""
    try:
        if zstd_frame_length(frame) != len(frame):
            raise ValueError("bytes after the zstd frame")
        declared = zstandard.frame_content_size(frame)
        if declared > limit:
            raise ValueError("zstd frame declares more than its region holds")
        reader = zstandard.ZstdDecompressor().stream_reader(bytes(frame), read_across_frames=False)
        parts, got = [], 0
        while got <= limit:
            part = reader.read(min(limit + 1 - got, 16 << 20))
            if not part:
                break
            parts.append(part)
            got += len(part)
    except zstandard.ZstdError as e:
        raise ValueError(f"bad zstd frame ({e})") from e
    if got > limit or declared not in (-1, got):
        raise ValueError("zstd frame decodes to more than it should")
    return b"".join(parts)


def zstd_exact(frame, size):
    """zstd_bounded for a frame that must decode to exactly `size` bytes."""
    out = zstd_bounded(frame, size)
    if len(out) != size:
        raise ValueError("zstd frame decodes to the wrong length")
    return out


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
    n = len(data) // size
    tail = bytes(data[n * size:])
    u = np.frombuffer(data, np.uint8, n * size)
    if size == 1:
        return [u], tail
    u = u.reshape(n, size)  # a row per element, its bytes from the lowest up (little-endian)
    if dtype == "F16":
        return [np.ascontiguousarray(u[:, 1]), np.ascontiguousarray(u[:, 0])], tail
    # BF16 and F32: the top byte holds the sign and the exponent's top 7 bits, the next the exponent's lowest bit and
    # the top 7 mantissa bits; byte operations into arrays made once (whole-width ones cost more memory traffic than
    # the coding itself, and threads doing them at once wait for memory)
    hi, lo = u[:, size - 1], u[:, size - 2]
    exponent, sign, tmp = np.empty(n, np.uint8), np.empty(n, np.uint8), np.empty(n, np.uint8)
    np.left_shift(hi, 1, out=exponent)
    np.bitwise_or(exponent, np.right_shift(lo, 7, out=tmp), out=exponent)
    np.bitwise_and(hi, 0x80, out=sign)
    np.bitwise_or(sign, np.bitwise_and(lo, 0x7F, out=tmp), out=sign)
    return [exponent, sign, *(np.ascontiguousarray(u[:, i]) for i in range(size - 3, -1, -1))], tail


def join_planes(planes, tail, dtype):
    """Inverse of byte_planes, as a bytearray built in place."""
    size = FLOAT_DTYPES[dtype]
    n = len(planes[0])
    out = bytearray(n * size + len(tail))
    out[n * size:] = tail
    u = np.frombuffer(out, np.uint8, n * size).reshape(n, size)
    if size == 1:
        u[:, 0] = planes[0]
    elif dtype == "F16":
        u[:, 1], u[:, 0] = planes
    else:
        exponent, sign, *rest = planes
        hi, lo, tmp = u[:, size - 1], u[:, size - 2], np.empty(n, np.uint8)
        np.right_shift(exponent, 1, out=hi)
        np.bitwise_or(hi, np.bitwise_and(sign, 0x80, out=tmp), out=hi)
        np.left_shift(exponent, 7, out=lo)
        np.bitwise_or(lo, np.bitwise_and(sign, 0x7F, out=tmp), out=lo)
        for i, plane in zip(range(size - 3, -1, -1), rest):
            u[:, i] = plane
    return out


def deflate_huffman(data):
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_HUFFMAN_ONLY)
    return c.compress(data) + c.flush()


def planes_encode(data, dtype):
    planes, tail = byte_planes(data, dtype)
    streams = []
    for plane in planes:  # the arrays go to the coders as they are
        options = [(PLANE_STORED, plane), (PLANE_ZSTD, zstandard.ZstdCompressor(level=1).compress(plane)),
                   (PLANE_DEFLATE, deflate_huffman(plane))]
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
    out, view = [], memoryview(payload)  # streams read where they are, not copied
    for i, (coding, length) in enumerate(heads):
        body, pos = view[pos:pos + length], pos + length
        expect = tail_len if i == size else n
        if coding == PLANE_STORED:
            raw = body
        elif coding == PLANE_DEFLATE:
            d = zlib.decompressobj(-15)
            raw = d.decompress(body, expect)
            if not d.eof or d.unconsumed_tail or d.unused_data:
                raise ValueError("byte planes: DEFLATE stream does not end where it should")
        elif coding == PLANE_ZSTD:
            raw = zstd_exact(body, expect)
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
        return zstd_exact(payload, raw_len)
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
        say(f"    {CODEC_NAMES[codec]} failed self-check ({type(e).__name__}); falling back")
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
GGUF_MAX_DIMS = 8  # GGML uses at most 4
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
                name = f.read(read("<Q")).decode("utf-8")  # strict: an invalid name makes the file plain bytes
                n_dims = read("<I")
                if n_dims > GGUF_MAX_DIMS:
                    return None
                dims = [read("<Q") for _ in range(n_dims)]
                tensors.append((name, read("<I"), dims, read("<Q")))
            if alignment < 1 or len({t[0] for t in tensors}) != len(tensors):
                return None  # not a valid file (or tensor names that do not say which is which): stored as plain bytes
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
                    end = start + element * math.prod(dims)  # Python integers: no wrap-around
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
    if len(payload) < ANS16_HEAD.size:
        raise ValueError("ans16 payload too short")
    prec, min_context, n, lanes, contexts, tables_zlen, counts_zlen, nwords = ANS16_HEAD.unpack_from(payload, 0)
    if n != base.size:
        raise ValueError("ans16 region size does not match its base")
    if not (1 <= prec <= 30 and 1 <= lanes <= ANS16_MAX_LANES and 1 <= contexts <= ANS16_RARE_CONTEXT + 1):
        raise ValueError("ans16 header out of range")
    pos = ANS16_HEAD.size
    if pos + tables_zlen + 8 * lanes + counts_zlen + 4 * nwords != len(payload):
        raise ValueError("ans16 payload lengths do not add up")
    # every table is bounded: at most 257 contexts of at most 65536 symbols, 6 bytes each, plus 6 bytes per context
    tables = zstd_bounded(payload[pos : pos + tables_zlen], 6 * contexts * (1 + 65536))
    pos += tables_zlen
    states = np.frombuffer(payload, "<u8", lanes, pos)
    pos += 8 * lanes
    lane_counts = np.frombuffer(zstd_exact(payload[pos : pos + counts_zlen], 4 * lanes), "<u4")
    pos += counts_zlen
    words = np.frombuffer(payload, "<u4", nwords, pos)
    if len(tables) < 6 * contexts or int(lane_counts.sum()) != nwords:
        raise ValueError("ans16 tables do not fit the header")
    ctx_ids = np.frombuffer(tables, "<u2", contexts, 0).astype(np.int64)
    k = np.frombuffer(tables, "<u4", contexts, 2 * contexts).astype(np.int64)
    total = int(k.sum())
    if np.any(k < 1) or np.any(k > 65536) or len(tables) != 6 * contexts + 6 * total or np.any(np.diff(ctx_ids) <= 0):
        raise ValueError("ans16 tables do not fit the header")
    sym_delta = np.frombuffer(tables, "<u2", total, 6 * contexts).astype(np.int64)
    freq = np.frombuffer(tables, "<u4", total, 6 * contexts + 2 * total).astype(np.int64)
    if np.any(freq < 1) or np.any(np.add.reduceat(freq, group_starts(k)) != 1 << prec):
        raise ValueError("ans16 frequencies do not add up")
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
    """OC_WORKERS, else the CPUs this process may use."""
    value = os.environ.get("OC_WORKERS")
    if not value:
        return os.process_cpu_count() or 1
    digits = value.strip()
    if not (digits.isascii() and digits.isdigit() and len(digits) <= 6) or int(digits) < 1:
        sys.exit(f"OC_WORKERS must be a whole number of at least 1, not {value[:40]!r}")
    return int(digits)


def worker_pool(n):
    """A pool of n worker processes: at most 61 on Windows, where Python's pool refuses more (ValueError); where they
    would be forked from this process (Linux before Python 3.14), forked by a fork server instead. This process has
    threads (progress, the window's stdin watch) that may hold a lock at that moment, and a forked worker inherits
    it held, by no thread: it hangs when it takes it (closing stdin, flushing stderr)."""
    if sys.platform == "win32":
        return ProcessPoolExecutor(min(n, 61))
    context = multiprocessing.get_context()
    if context.get_start_method() == "fork" and "forkserver" in multiprocessing.get_all_start_methods():
        context = multiprocessing.get_context("forkserver")
    return ProcessPoolExecutor(n, mp_context=context)


def frame_workers():
    """Threads that code byte-plane and zstd frames: up to 8 (more gain little), fewer with OC_WORKERS."""
    return max(1, min(ans16_workers(), 8))


def hash_workers():
    """Threads that hash or check files at once (each file's SHA-256 is one sequence): up to 4, past which the disk
    is the limit, fewer with OC_WORKERS."""
    return max(1, min(ans16_workers(), 4))


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


# ----------------------------------------------------------------------------------------------- blobs
#
# Every file a pack stores is a blob: one OCZ4 container, named by the SHA-256 of the original file and shared by
# every model that has that file. Its header says how to restore it, including the blobs it is coded against (its
# bases, by SHA-256); its last 32 bytes are the SHA-256 of all its bytes before them, so a blob shows its own damage
# before any decoder reads it. Byte-level spec: ARCHIVE_FORMAT.md.


MAX_HEADER = 64 << 20  # bytes of container header JSON
MAX_REGION = 1 << 30  # bytes one region restores to (--chunk-mib is at most 1024)
MAX_REGIONS = 1 << 22
REGION_KINDS = {"float", "zstd", "auto", "ans16", "xor"}
TRAILER = 32  # the SHA-256 that ends a blob


def container_header_problem(header):
    """None if a container header can be used: the original file's size and SHA-256; bases, each a blob by the
    SHA-256 and size of its file; regions that cover the file in order without gaps, each within MAX_REGION, of a
    known kind, with base references only on delta regions and only into the bases; else why not."""
    if not isinstance(header, dict) or not is_size(header.get("original_size")) or not is_sha256(header.get("sha256")):
        return "no original_size or sha256"
    regions, bases = header.get("regions"), header.get("bases")
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        return "no list of regions, or too many"
    if not isinstance(bases, list) or not all(isinstance(b, dict) and is_sha256(b.get("sha256")) and is_size(b.get("size"))
                                              and isinstance(b.get("model", ""), str) and isinstance(b.get("path", ""), str)
                                              for b in bases):
        return "malformed bases"
    if not isinstance(header.get("method", ""), str):
        return "malformed method"
    if header.get("partial", True) is not True:
        return "malformed partial"
    pos = 0
    for r in regions:
        if isinstance(r, dict) and "dropped" in r and (r["dropped"] is not True or header.get("partial") is not True):
            return f"region at {pos}: dropped, but not in a partial blob"
        if not isinstance(r, dict) or not is_size(r.get("offset")) or r["offset"] != pos or not is_size(r.get("length")) or not 0 < r["length"] <= MAX_REGION \
                or not isinstance(r.get("kind"), str) or r["kind"] not in REGION_KINDS or not isinstance(r.get("dtype", ""), str):
            return f"region at {pos} malformed, out of order or too long"
        base = r.get("base")
        if (base is not None) != (r["kind"] in ("ans16", "xor")):
            return f"region at {pos}: a delta region needs a base reference, and only a delta region has one"
        if base is not None:
            if not (isinstance(base, list) and len(base) == 2 and type(base[0]) is int and 0 <= base[0] < len(bases) and is_size(base[1])):
                return f"region at {pos}: bad base reference"
            if not isinstance(r.get("base_dtype", "BF16"), str) or r.get("base_dtype", "BF16") not in FLOAT_BYTES \
                    or ("base_dtype" in r and r.get("dtype") not in ANS16_EXPONENT):
                return f"region at {pos}: bad base_dtype"
            if r["kind"] == "ans16" and (r.get("dtype") not in ANS16_EXPONENT or r["length"] % 2):
                return f"region at {pos}: bad ans16 region"
            scale = FLOAT_BYTES[r["base_dtype"]] // FLOAT_BYTES[r["dtype"]] if "base_dtype" in r else 1
            if base[1] + r["length"] * scale > bases[base[0]]["size"]:
                return f"region at {pos}: its base bytes run past the end of the base file"
        pos += r["length"]
    return None if pos == header["original_size"] else "regions do not add up to original_size"


def read_container(f, start=0, size=None):
    """(header, [(codec, raw_len, payload offset, payload_len, crc), or None for a region a partial blob dropped]) of
    an open container (the file f, or `size` bytes of it from `start`, where a blob lies in a pack file; offsets
    count from there), checked before anything is decoded: the header (container_header_problem), one frame per region restoring to exactly its length with a
    codec that fits the region, every payload inside the blob, and nothing after the last one but the blob's
    SHA-256. ValueError if not."""
    size = os.fstat(f.fileno()).st_size - start if size is None else size
    f.seek(start)
    if f.read(4) != MAGIC:
        raise ValueError(f"not an {MAGIC.decode()} container")
    (n,) = struct.unpack("<I", f.read(4).ljust(4, b"\0"))
    if n > min(MAX_HEADER, size - 8 - TRAILER):
        raise ValueError("container header too long for the file")
    header = strict_json(f.read(n))
    problem = container_header_problem(header)
    if problem:
        raise ValueError(f"container header: {problem}")
    frames, pos, end = [], 8 + n, size - TRAILER
    for region in header["regions"]:
        if region.get("dropped"):  # a partial blob keeps no frame for it
            frames.append(None)
            continue
        if pos + FRAME_HEAD.size > end:
            raise ValueError("container cut short")
        f.seek(start + pos)
        codec, raw_len, payload_len, crc = FRAME_HEAD.unpack(f.read(FRAME_HEAD.size))
        pos += FRAME_HEAD.size
        kind = codec & ~STANDALONE
        fits = (kind in (CODEC_RAW, CODEC_ZSTD) or (kind == CODEC_PLANES and region_planes_dtype(region))
                or (codec == CODEC_ANS16 and region["kind"] == "ans16")) and not (codec & STANDALONE and "base" not in region)
        if raw_len != region["length"] or not fits or payload_len > end - pos:
            raise ValueError(f"container frame at {region['offset']} does not fit its region")
        frames.append((codec, raw_len, pos, payload_len, crc))
        pos += payload_len
    if pos != end:
        raise ValueError("bytes after the last frame of the container, other than its SHA-256")
    return header, frames


def blob_damage(span, sha, counted=False):
    """Why the bytes at span (path, start, size) are not an intact blob of the file `sha`, or None: its last 32 bytes
    must be the SHA-256 of the rest, its container valid (read_container) and its header naming that file. counted:
    the bytes go to the progress report."""
    path, start, size = span
    try:
        with open(path, "rb") as f:
            if size < 8 + TRAILER:
                return "too short to be a blob"
            f.seek(start)
            h, left = hashlib.sha256(), size - TRAILER
            for block in blocks_ahead(f, left):
                h.update(block)
                left -= len(block)
                if counted:
                    PROGRESS.add(len(block))
            if left:
                return "cut short"
            if f.read(TRAILER) != h.digest():
                return "damaged (its SHA-256 does not match)"
            header, _ = read_container(f, start, size)
    except OSError as e:
        return f"unreadable ({e.strerror or e})"
    except ValueError as e:
        return f"malformed ({e})"
    if header["sha256"] != sha:
        return "the blob of another file"
    return None


# ----------------------------------------------------------------------------------------------- packs
#
# A pack is a folder (FolderPack) or the same tree in one ZIP file (FilePack, read where it lies; NewFilePack while
# pack writes a new one):
#
#     ocpack.json                   {"format": "overcompressing-pack", "format_version": 4}: what makes it a pack
#     models/<Org__Name>.json       one manifest per model: the paths, sizes and SHA-256s of its files
#     blobs/<ab>/<sha256>.ocz       one blob per distinct file, shared by every model that has that file
#     _kit/                         a portable copy of the tool, if one was put there (never read; export carries it)
#
# In a pack file the blobs are stored as they are (ZIP method 0) and read where they lie; the other members may be
# Deflated. A model refers to nothing but its blobs, and a blob to nothing but the blobs it is coded against: what a
# pack holds is a graph that grows as models are packed. The tool walks it when it needs to (to delete what no model
# needs, to show what needs what) and never stores it.

OCZIP = ".oczip"
MARKER = "ocpack.json"
# _kit was the offline kit kit/build_kit.py made, which is retired (no longer made); a portable copy of the tool may be
# put there instead. It is handled passively: a folder holding only it can become a pack, restore never writes into it,
# export carries it, and the window started from it opens the pack.
MODELS, BLOBS, KIT = "models", "blobs", "_kit"
MARKER_JSON = {"format": PACK_FORMAT, "format_version": FORMAT_VERSION}
MAX_MANIFEST = 256 << 20


def is_oczip(path):
    return path.lower().endswith(OCZIP)


def blob_rel(sha):
    return f"{BLOBS}/{sha[:2]}/{sha}.ocz"


def blob_of(rel):
    """The SHA-256 a pack member's path names as a blob, or None if it is not a blob's path."""
    parts = rel.split("/")
    if len(parts) == 3 and parts[0] == BLOBS and parts[2].endswith(".ocz"):
        sha = parts[2][:-4]
        if is_sha256(sha) and parts[1] == sha[:2]:
            return sha
    return None


def manifest_rel(name):
    return f"{MODELS}/{repo_dir_name(name)}.json"


def name_key(name):
    """Two model names with the same key would have one manifest file on Windows and macOS, which ignore case (and
    "Org/Name" and "Org__Name" one everywhere), so a pack never holds both."""
    return repo_dir_name(name).casefold()


class FolderPack:
    """A pack folder."""

    kind = "pack folder"

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def full(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def read(self, rel, limit=MAX_MANIFEST):
        with open(self.full(rel), "rb") as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"{rel} is larger than {human(limit)}")
        return data

    def blob_span(self, sha):
        path = self.full(blob_rel(sha))
        return path, 0, os.path.getsize(path)

    def blob_ids(self):
        found = set()
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
            with os.scandir(self.full(BLOBS)) as folders:
                for d in folders:
                    if d.is_dir(follow_symlinks=False):
                        with os.scandir(d.path) as files:
                            found.update(blob_of(f"{BLOBS}/{d.name}/{e.name}") for e in files if e.is_file(follow_symlinks=False))
        found.discard(None)
        return found

    def manifest_files(self):
        try:
            return sorted(n for n in os.listdir(self.full(MODELS)) if n.endswith(".json") and not n.startswith(".oc-"))
        except (FileNotFoundError, NotADirectoryError):
            return []

    def leftovers(self):
        """Temporary files (.oc-*.part) of runs that stopped without deleting them: in the pack's own folders only."""
        found = []
        for rel in ("", MODELS, BLOBS, *(f"{BLOBS}/{d}" for d in self._subfolders())):
            with contextlib.suppress(FileNotFoundError, NotADirectoryError):
                with os.scandir(self.full(rel) if rel else self.root) as entries:
                    found += [f"{rel}/{e.name}" if rel else e.name for e in entries
                              if e.name.startswith(".oc-") and e.name.endswith(".part") and e.is_file(follow_symlinks=False)]
        return found

    def _subfolders(self):
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
            with os.scandir(self.full(BLOBS)) as folders:
                return [d.name for d in folders if d.is_dir(follow_symlinks=False)]
        return []


class FilePack:
    """A pack file (.oczip), read where it lies and never written to (opened once per run: open_pack)."""

    kind = "pack file"

    def __init__(self, root):
        self.root = os.path.abspath(root)
        try:
            self.zip = zipfile.ZipFile(self.root)
        except zipfile.BadZipFile as e:
            raise ValueError(f"not a readable pack file ({e})") from None
        names = self.zip.namelist()
        bad = next((n for n in names if path_problem(n.rstrip("/"))), None)
        if bad is not None or len(set(names)) != len(names):
            self.zip.close()
            raise ValueError(f"unsafe file name {bad!r} in it" if bad is not None else "a file name appears twice in it")
        self.names = set(names)

    def read(self, rel, limit=MAX_MANIFEST):
        try:
            with self.zip.open(rel) as f:
                data = f.read(limit + 1)
        except KeyError:
            raise FileNotFoundError(errno.ENOENT, "not in the pack file", rel) from None
        except (RuntimeError, NotImplementedError, zipfile.BadZipFile) as e:  # encrypted, an unknown method, a bad CRC
            raise ValueError(f"{rel}: {e}") from None
        if len(data) > limit:
            raise ValueError(f"{rel} is larger than {human(limit)}")
        return data

    def blob_span(self, sha):
        try:
            info = self.zip.getinfo(blob_rel(sha))
        except KeyError:
            raise FileNotFoundError(errno.ENOENT, "not in the pack file", blob_rel(sha)) from None
        return member_span(self.root, info)

    def blob_ids(self):
        return {sha for n in self.names if (sha := blob_of(n))}

    def manifest_files(self):
        return sorted(n.split("/", 1)[1] for n in self.names
                      if n.startswith(MODELS + "/") and n.count("/") == 1 and n.endswith(".json"))

    def close(self):
        self.zip.close()


def member_span(path, info):
    """(path, start, size): where the bytes of a member stored as it is lie in the ZIP file at `path` (its local
    header's offset plus 30 plus the lengths of its name and extra field); ValueError if it is compressed, or if its
    local header disagrees with the central directory on its name, method or encryption (a standard ZIP reader would
    refuse it, or read it otherwise)."""
    if info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1 or info.compress_size != info.file_size:
        raise ValueError(f"{info.filename} is compressed or encrypted in the pack file: blobs must be stored as they are")
    with open(path, "rb") as f:
        f.seek(info.header_offset)
        head = f.read(30)
        name = f.read(struct.unpack("<H", head[26:28])[0]) if len(head) == 30 else b""
        end = os.fstat(f.fileno()).st_size
    if len(head) != 30 or head[:4] != b"PK\x03\x04":
        raise ValueError(f"{info.filename}: no ZIP local header where the pack file says it is")
    flags, method = struct.unpack("<HH", head[6:10])
    if name.decode("utf-8" if flags & 0x800 else "cp437", "replace") != info.orig_filename or method != zipfile.ZIP_STORED \
            or flags & 1:  # decoded as zipfile does
        raise ValueError(f"{info.filename}: its local header disagrees with the pack file's directory (name, method or "
                         "encryption)")
    start = info.header_offset + 30 + sum(struct.unpack("<HH", head[26:30]))
    if start + info.file_size > end:
        raise ValueError(f"{info.filename} runs past the end of the pack file")
    return path, start, info.file_size


class NewFilePack:
    """A pack file being written (pack into a new .oczip): each blob goes in as it is made and is read back from the
    unfinished file, to check it and as a base for the files after it; each manifest goes in when its model is done.
    The file gets its name only once it is complete (SafeTree.placing)."""

    kind = "pack file"

    def __init__(self, f, root):
        self.f, self.root, self.temp_path = f, root, f.temp_path
        self.zip = zipfile.ZipFile(f, "w", allowZip64=True)
        self.spans, self.members = {}, {}  # blob sha -> (start, size); other member -> its bytes
        self.put(MARKER, json.dumps(MARKER_JSON).encode())

    def put(self, rel, data):
        info = zipfile.ZipInfo(rel, time.localtime()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        self.zip.writestr(info, data)
        self.members[rel] = data

    def read(self, rel, limit=MAX_MANIFEST):
        if rel not in self.members:
            raise FileNotFoundError(errno.ENOENT, "not in the pack file", rel)
        return self.members[rel]

    def blob_span(self, sha):
        if sha not in self.spans:
            raise FileNotFoundError(errno.ENOENT, "not in the pack file", blob_rel(sha))
        return (self.temp_path, *self.spans[sha])

    def blob_ids(self):
        return set(self.spans)

    def manifest_files(self):
        return sorted(rel.split("/", 1)[1] for rel in self.members if rel.startswith(MODELS + "/"))


_OPEN = {}  # pack file path -> its FilePack, opened once for the program's run


def open_pack(path):
    """The pack at `path`: a pack folder (FolderPack) or a pack file (FilePack). ValueError if it is neither, or a
    pack of another format."""
    if is_oczip(path) and os.path.isfile(path):
        key = os.path.normcase(os.path.abspath(path))
        if key not in _OPEN:
            _OPEN[key] = FilePack(path)
        p = _OPEN[key]
    elif os.path.isdir(path):
        p = FolderPack(path)
    else:
        raise ValueError(f"not a pack folder or a pack file ({OCZIP})")
    problem = marker_problem(p)
    if problem:
        raise ValueError(problem)
    return p


def marker_problem(p):
    try:
        marker = strict_json(p.read(MARKER, 1 << 16))
    except FileNotFoundError:
        return f"not a pack (no {MARKER} in it)"
    except (OSError, ValueError) as e:
        return f"{MARKER} cannot be read ({e})"
    if not isinstance(marker, dict) or marker.get("format") != PACK_FORMAT:
        return f"its {MARKER} is not a pack's"
    if marker.get("format_version") != FORMAT_VERSION:
        return f"a pack of format version {marker.get('format_version')!r}: this tool reads version {FORMAT_VERSION}"
    return None


def close_pack_files():
    """Close the pack files opened for reading, so that another program may replace them."""
    while _OPEN:
        _OPEN.popitem()[1].close()


def all_manifests(p, problems=None):
    """{name: manifest} of every model of a pack. A manifest that cannot be read or trusted (manifest_problem; its
    file must also be named after its model) goes to `problems` as (its path, the problem) if given; otherwise the
    program stops, since the blobs that model needs would look unused."""
    manifests, keys = {}, {}
    for n in p.manifest_files():
        rel = f"{MODELS}/{n}"
        try:
            manifest = strict_json(p.read(rel))
            problem = manifest_problem(manifest)
            if not problem and repo_dir_name(manifest["name"]) + ".json" != n:
                problem = f"it is the manifest of {manifest['name']!r}, whose file is {manifest_rel(manifest['name'])}"
            if not problem and name_key(manifest["name"]) in keys:  # e.g. in a pack file, names differing in case only
                problem = f"{keys[name_key(manifest['name'])]} is the manifest of a model of that name already"
        except (OSError, ValueError) as e:
            problem = f"cannot read it ({e})"
        if problem:
            if problems is None:
                sys.exit(f"{rel} in {p.root}: {problem}\nfix it, or move it out of the pack, before changing the pack")
            problems.append((rel, problem))
            continue
        manifests[manifest["name"]] = manifest
        keys[name_key(manifest["name"])] = rel
    return manifests


def model_named(manifests, given, p):
    """The name of the model `given` names: its name (Org/Name), or its manifest's file name (Org__Name, with or
    without .json), ignoring case if that leaves only one; stops if no model is named so."""
    if given in manifests:
        return given
    stem = given[:-5] if given.lower().endswith(".json") else given
    for same in (lambda n: repo_dir_name(n) == stem, lambda n: name_key(n) == name_key(stem)):
        found = [n for n in manifests if same(n)]
        if len(found) == 1:
            return found[0]
    sys.exit(f"no model named {given!r} in {p.root}" + (f" (it holds {', '.join(sorted(manifests)[:6])}"
                                                         f"{', ...' if len(manifests) > 6 else ''})" if manifests else ""))


def pack_folder_of(path):
    """The pack folder `path` is or is inside (the nearest folder on the way up holding ocpack.json), or None."""
    folder = os.path.realpath(path)
    while True:
        if os.path.isfile(os.path.join(folder, MARKER)):
            return folder
        up = os.path.dirname(folder)
        if up == folder:
            return None
        folder = up


def place_problem(path):
    """Why restore must not write into `path`, pack read from it, nor a pack file be written there: it is a pack
    folder or inside one (what is written there would lie among the pack's own files, or be read as the pack's).
    None if it is neither."""
    inside = pack_folder_of(path)
    if inside is None:
        return None
    if os.path.normcase(inside) == os.path.normcase(os.path.realpath(path)):
        return f"{path} is a pack folder"
    return f"{path} is inside the pack folder {inside}"


# ----------------------------------------------------------------------------------------------- reading blobs

MAX_REFERENCE_DEPTH = 32  # longer chains of bases are treated as a cycle
FRAME_CACHE_BYTES = 384 << 20
_HEADERS = {}  # span -> (header, frames, region offsets)
_FRAMES = {}  # (span, region index) -> decoded region, least recently used first
_INTACT = set()  # (pack root, sha) of blobs found intact in this run, with every blob they are coded against
_BASE_CACHE = {}  # (pack root, sha) -> restored base file that pack keeps until it has checked the delta


def forget_cached_reads():
    """Drop what was read and checked; needed once a blob of the pack has changed."""
    _HEADERS.clear()
    _FRAMES.clear()
    _INTACT.clear()


def container_index(span):
    """(header, frames, region offsets) of the container at span (path, start, size), checked (read_container)."""
    if span not in _HEADERS:
        path, start, size = span
        with open(path, "rb") as f:
            header, frames = read_container(f, start, size)
        _HEADERS[span] = header, frames, [r["offset"] for r in header["regions"]]
    return _HEADERS[span]


def blob_header(p, sha):
    header = container_index(p.blob_span(sha))[0]
    if header["sha256"] != sha:
        raise ValueError(f"{blob_rel(sha)} is the blob of another file")
    return header


def base_text(base):
    """A base as its blob's header names it: the model and file it was packed from, else its SHA-256."""
    return f"{base['model']}:{base['path']}" if base.get("model") and base.get("path") else f"blob {base['sha256'][:12]}..."


def blob_problem(p, sha, span=None, trail=()):
    """None if the blob of the file `sha` (at `span`, default where the pack has it) and every blob it is coded
    against are there and intact (blob_damage), without a cycle, checked before any decoder sees their bytes; else
    what is wrong."""
    if sha in trail:
        return "cyclic reference between blobs"
    if len(trail) > MAX_REFERENCE_DEPTH:
        return "reference chain too deep"
    placed = span is None
    if placed:
        if (p.root, sha) in _INTACT:
            return None
        try:
            span = p.blob_span(sha)
        except FileNotFoundError:
            return "its blob is missing from the pack"
        except (OSError, ValueError) as e:
            return f"its blob cannot be read ({e})"
    problem = blob_damage(span, sha)
    if problem:
        return f"its blob is {problem}"
    for base in container_index(span)[0]["bases"]:
        problem = blob_problem(p, base["sha256"], trail=trail + (sha,))
        if problem:
            return f"{base_text(base)}, which it is coded against: {problem}"
    if placed:
        _INTACT.add((p.root, sha))
    return None


STANDALONE = 0x80  # frame codec flag: this region of a delta was stored without using the base


def iter_blob_bytes(p, sha, span=None, depth=0):
    """Yield the original bytes of the file blob `sha` stores (at `span`, default where the pack has it), region by
    region; the blobs it is coded against are restored into temporary files first. ans16 regions are decoded in
    parallel worker processes."""
    if depth > MAX_REFERENCE_DEPTH:
        raise ValueError("reference chain too deep (cyclic references?)")
    path, start, size = span or p.blob_span(sha)
    with open(path, "rb") as f:
        header, frames = read_container(f, start, size)
        if header["sha256"] != sha:
            raise ValueError(f"{path}: the blob of another file")
        regions = header["regions"]
        base_paths, owned = base_paths_for(p, header["bases"], depth)
        jobs = [
            (path, start + off, plen, base_paths[r["base"][0]], r["base"][1], r.get("base_dtype"), r["length"], r["dtype"])
            for r, (codec, _, off, plen, _) in ((r, fr) for r, fr in zip(regions, frames) if fr) if codec == CODEC_ANS16
        ]
        pool = worker_pool(ans16_workers()) if jobs else None
        bases = [open(b, "rb") for b in base_paths]
        plain = None
        try:
            decoded = pool.map(_ans16_decode_job, jobs) if pool else iter(())

            def to_decode():  # read here, in order; decoded on the threads
                for region, (codec, raw_len, off, plen, _) in ((r, fr) for r, fr in zip(regions, frames) if fr):
                    if codec != CODEC_ANS16:
                        f.seek(start + off)
                        payload = f.read(plen)
                        base = base_bytes(base_paths, bases, region) if "base" in region and not codec & STANDALONE else None
                        yield codec, payload, raw_len, region, base

            def decode(codec, payload, raw_len, region, base):  # with its CRC, on the thread too
                chunk = decode_payload(codec & ~STANDALONE, payload, raw_len, region_planes_dtype(region))
                chunk = chunk if base is None else xor_bytes(chunk, base)
                return chunk, zlib.crc32(chunk)

            plain = ordered_map(decode, to_decode(), frame_workers())
            for region, frame in zip(regions, frames):
                if frame is None:  # dropped by a partial blob: no file reads it, its place is kept with zeros
                    for at in range(0, region["length"], 16 << 20):
                        yield bytes(min(16 << 20, region["length"] - at))
                    continue
                codec, raw_len, off, plen, crc = frame
                if codec == CODEC_ANS16:
                    chunk = next(decoded)
                    check = zlib.crc32(chunk)
                else:
                    chunk, check = next(plain)
                if len(chunk) != raw_len or check != crc:
                    raise ValueError(f"{path}: region at {region['offset']} failed its CRC check")
                yield chunk
        finally:
            if plain is not None:
                plain.close()
            for b in bases:
                b.close()
            if pool:
                pool.shutdown()
            for b in owned:
                os.remove(b)


class BaseFailed(ValueError):
    """A blob that another is coded against does not restore its file (materialize_blob): the fault is its own."""

    def __init__(self, sha, problem):
        super().__init__(f"blob {sha[:12]}... does not restore its file: {problem}")
        self.sha, self.problem = sha, problem


def materialize_blob(p, sha, depth=0):
    """Restore the file of the blob `sha` into a temporary file (OC_TMP or the system temp folder) and return its
    path; BaseFailed if its bytes do not restore that file (an error of the system, OSError, is passed on as it is)."""
    fd, tmp = temp_base_file()
    h = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out:
            for chunk in iter_blob_bytes(p, sha, depth=depth + 1):
                out.write(chunk)
                h.update(chunk)
        if h.hexdigest() != sha and not container_index(p.blob_span(sha))[0].get("partial"):  # (each region has its CRC)
            raise ValueError("restored with the wrong sha256")
    except BaseFailed:
        os.remove(tmp)
        raise
    except ValueError as e:
        os.remove(tmp)
        raise BaseFailed(sha, str(e)) from e
    except BaseException:
        os.remove(tmp)
        raise
    return tmp


def temp_base_file():
    """(fd, path) of a new temporary file for a restored base: in OC_TMP, else the system's temp folder."""
    return tempfile.mkstemp(prefix="oc-base-", suffix=".bin", dir=os.environ.get("OC_TMP") or None)


class SideCopy:
    """A file written beside bytes being checked: verify keeps a restored file this way as the base of the files after
    it. If it cannot be written (no room, say), there is no copy, and those files restore their base themselves."""

    def __init__(self):
        self.path = self.f = None
        with contextlib.suppress(OSError):
            fd, self.path = temp_base_file()
            self.f = os.fdopen(fd, "wb")

    def write(self, data):
        if self.f:
            try:
                self.f.write(data)
            except OSError:
                self.discard()

    def done(self, ok):
        """Its path if it was written whole and `ok`; else it is deleted (None)."""
        if self.f and ok:
            try:
                self.f.close()
                self.f = None
                return self.path
            except OSError:
                pass
        self.discard()
        return None

    def discard(self):
        with contextlib.suppress(OSError):
            if self.f:
                self.f.close()
        with contextlib.suppress(OSError):
            if self.path:
                os.remove(self.path)
        self.f = self.path = None


def cached_base(p, sha):
    """A restored file of the blob `sha`, kept (for the check that follows) until release_cached_bases()."""
    key = (p.root, sha)
    if key not in _BASE_CACHE:
        _BASE_CACHE[key] = materialize_blob(p, sha)
    return _BASE_CACHE[key]


def release_cached_bases():
    for path in _BASE_CACHE.values():
        os.remove(path)
    _BASE_CACHE.clear()


def base_paths_for(p, bases, depth=0):
    """Restored files of a blob's bases, as (paths, paths this call made and must delete)."""
    paths, owned = [], []
    try:
        for base in bases:
            path = _BASE_CACHE.get((p.root, base["sha256"]))
            if path is None:
                path = materialize_blob(p, base["sha256"], depth)
                owned.append(path)
            paths.append(path)
    except BaseException:
        for path in owned:
            os.remove(path)
        raise
    return paths, owned


def blob_restores(p, sha, size, span=None, counted=False):
    """None if the blob of the file `sha` (at `span`, default where the pack has it) is intact with every blob it is
    coded against (blob_problem) and restores exactly `size` bytes with that SHA-256; else what is wrong. counted:
    the bytes go to the progress report."""
    problem = blob_problem(p, sha, span)
    if problem:
        return problem
    h, n = hashlib.sha256(), 0
    try:
        for chunk in iter_blob_bytes(p, sha, span):
            h.update(chunk)
            n += len(chunk)
            if counted:
                PROGRESS.add(len(chunk))
    except Exception as e:
        return f"decode failed: {e}"
    if n != size or h.hexdigest() != sha:
        return "restored bytes differ from the original SHA-256"
    return None


def read_range(p, sha, start, length, depth=0):
    """Bytes [start, start + length) of the file blob `sha` stores, without restoring the rest of it."""
    if depth > MAX_REFERENCE_DEPTH:
        raise ValueError("reference chain too deep (cyclic references?)")
    span = p.blob_span(sha)
    header, _, offsets = container_index(span)
    if header["sha256"] != sha:
        raise ValueError(f"{blob_rel(sha)} is the blob of another file")
    parts, end = [], start + length
    for i in range(max(0, bisect.bisect_right(offsets, start) - 1), len(offsets)):
        region = header["regions"][i]
        if region["offset"] >= end:
            break
        a, b = max(start - region["offset"], 0), min(end - region["offset"], region["length"])
        if b > a:
            parts.append(region_bytes(p, span, i, a, b, depth))
    return b"".join(parts)


def region_base(p, header, region, depth):
    """The base bytes (before any dtype conversion) a delta region was coded against."""
    index, offset = region["base"]
    scale = FLOAT_BYTES[region["base_dtype"]] // FLOAT_BYTES[region["dtype"]] if "base_dtype" in region else 1
    return read_range(p, header["bases"][index]["sha256"], offset, region["length"] * scale, depth + 1)


def region_bytes(p, span, i, a, b, depth):
    """Bytes a..b (counted from the region's start) of region i of the container at span."""
    header, frames, _ = container_index(span)
    if frames[i] is None:
        raise ValueError(f"{span[0]}: region at {header['regions'][i]['offset']} is not kept (a partial blob)")
    region, (codec, raw_len, off, plen, crc) = header["regions"][i], frames[i]
    key = (span, i)
    if key in _FRAMES:
        _FRAMES[key] = chunk = _FRAMES.pop(key)  # now the most recently used
        return chunk[a:b]
    with open(span[0], "rb") as f:
        f.seek(span[1] + off)
        payload = f.read(plen)
    if codec == CODEC_ANS16:
        base = prediction_from_bytes(region_base(p, header, region, depth), region.get("base_dtype"), region["dtype"])
        stop = -(-b // 2)
        chunk = ans16_decode(base, payload, region["dtype"], stop if stop < raw_len // 2 else None).tobytes()
        if len(chunk) < raw_len:  # a prefix only: nothing to check it against, so it is not cached either
            return chunk[a:b]
    else:
        chunk = decode_payload(codec & ~STANDALONE, payload, raw_len, region_planes_dtype(region))
        if "base" in region and not codec & STANDALONE:
            base = region_base(p, header, region, depth)
            if "base_dtype" in region:
                base = prediction_from_bytes(base, region["base_dtype"], region["dtype"]).tobytes()
            chunk = xor_bytes(chunk, base)
    if len(chunk) != raw_len or zlib.crc32(chunk) != crc:
        raise ValueError(f"{span[0]}: region at {region['offset']} failed its CRC check")
    _FRAMES[key] = chunk
    total = sum(len(c) for c in _FRAMES.values())
    while total > FRAME_CACHE_BYTES and len(_FRAMES) > 1:
        total -= len(_FRAMES.pop(next(iter(_FRAMES))))
    return chunk[a:b]


def blob_info(p, sha):
    """How the blob `sha` stores its file: {"stored": the blob's size, "method", "bases" (as its header lists them),
    "matched": the bytes coded against them}."""
    span = p.blob_span(sha)
    header = container_index(span)[0]
    matched = sum(r["length"] for r in header["regions"] if "base" in r)
    return {"stored": span[2], "method": header.get("method") or "?", "bases": header["bases"], "matched": matched}


# ----------------------------------------------------------------------------------------------- writing blobs


class BlobFailed(Exception):
    """A blob just written does not restore its file exactly: it is not put in place."""


class NotSmaller(Exception):
    """rebase: the blob just written is not smaller than the one it would replace: it is not put in place."""


def method_of(fmt, regions):
    """How a file is stored, for people: its format (safetensors, gguf, zstd for text, auto for other bytes), with
    +ans16 or +xor for a delta."""
    if not any("base" in r for r in regions):
        return fmt
    return f"{fmt}+{'ans16' if any(r['kind'] == 'ans16' for r in regions) else 'xor'}"


def write_container(src_path, out, header, base_paths=(), levels=(19, 9), workers=1):
    """Write the container of the file at src_path to `out`: `header` (its regions and bases), then one frame per
    region. Standalone regions are coded by kind. Regions with a "base" reference are coded against it (ans16 in
    parallel worker processes, or the original XOR base by byte planes/zstd) unless storing them standalone is
    smaller, in which case the frame's codec carries the STANDALONE flag. Other regions are coded on threads
    (frame_workers), read and written here in order. Returns {frame codec: how many frames use it}."""
    regions = header["regions"]
    hb = json.dumps(header).encode()
    codecs_used = {}
    jobs = [
        (base_paths[r["base"][0]], r["base"][1], r.get("base_dtype"), src_path, r["offset"], r["length"], r["dtype"])
        for r in regions if r["kind"] == "ans16"
    ]
    pool = worker_pool(workers) if jobs else None
    bases = [open(b, "rb") for b in base_paths]

    def code(region, data, base):
        """(length, crc32, codec options) of a region; base: its base bytes, for a delta region."""
        dtype, level = region_planes_dtype(region), region_zstd_level(region, *levels)
        if base is None:
            return len(data), zlib.crc32(data), [encode_chunk(data, dtype, level, try_both=region["kind"] == "auto")]
        codec, payload = encode_chunk(data, dtype, level)
        return len(data), zlib.crc32(data), [encode_chunk(xor_bytes(data, base), dtype, level), (codec | STANDALONE, payload)]

    coded = None
    try:
        with open(src_path, "rb") as src, open(src_path, "rb") as ahead:
            out.write(MAGIC + struct.pack("<I", len(hb)) + hb)
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
                out.write(FRAME_HEAD.pack(codec, length, len(payload), crc))
                out.write(payload)
                PROGRESS.add(length)
    finally:
        if coded is not None:
            coded.close()
        for b in bases:
            b.close()
        if pool:
            pool.shutdown()
    return codecs_used


def store_blob(p, sha, size, fill, replace=False, keep=None):
    """Write the blob of the file `sha` (`size` bytes) with fill(out), end it with its SHA-256, and decode it back
    from disk (blob_restores) before it takes its place, so a blob that does not restore its file exactly is never
    used (BlobFailed). In a pack folder it goes over an existing blob only if `replace`, and with keep(its size)
    false not at all (NotSmaller). Returns its size."""
    if isinstance(p, NewFilePack):
        info = zipfile.ZipInfo(blob_rel(sha), time.localtime()[:6])
        info.compress_type = zipfile.ZIP_STORED
        with p.zip.open(info, "w", force_zip64=True) as dst:
            out = HashingWriter(dst)
            fill(out)
            dst.write(out.hash.digest())
        p.f.flush()
        span = member_span(p.temp_path, info)
        problem = blob_restores(p, sha, size, span, counted=True)
        if problem:
            raise BlobFailed(problem)
        p.spans[sha] = span[1:]
        return span[2]
    with SafeTree(p.root).placing(blob_rel(sha), replace) as f:
        out = HashingWriter(f)
        fill(out)
        f.write(out.hash.digest())
        f.flush()
        stored = out.size + TRAILER
        if keep is not None and not keep(stored):
            raise NotSmaller
        problem = blob_restores(p, sha, size, (f.temp_path, 0, stored), counted=True)
        if problem:
            raise BlobFailed(problem)
    forget_cached_reads()
    return stored


def touch_marker(p):
    """Record in ocpack.json when the pack folder last changed. It is the last step of every change, so whatever
    watches that one file (the window does) sees each change once, when it is complete."""
    with contextlib.suppress(OSError, ValueError):
        with SafeTree(p.root).placing(MARKER, replace=True) as f:
            f.write(json.dumps({**MARKER_JSON, "changed": now()}).encode())


def write_manifest(p, manifest):
    """Put a model's manifest in place in one step: a crash leaves the old manifest or the new one."""
    data = json.dumps(manifest, indent=1).encode("utf-8")
    if isinstance(p, NewFilePack):
        p.put(manifest_rel(manifest["name"]), data)
        return
    with SafeTree(p.root).placing(manifest_rel(manifest["name"]), replace=True) as f:
        f.write(data)


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
RELEASE_GAIN = 0.1  # share of a kept base's room that letting it go must be estimated to save (rebase)


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


def choose_base(p, fmt, tensors, read, manifests, blocked, delta_codec):
    """(best model or None, [(model, estimated share)] of the finalists, standalone share, candidate count) for one
    file of format `fmt` with these tensors, whose bytes read(start, length) returns (from disk, or from the pack),
    among `manifests` (the other models of the pack); files whose blobs are in `blocked` are not used (they would make
    a cycle). Shares are estimated stored size / original size of the file's tensor data."""
    if not tensors:
        return None, [], None, 0
    candidates = []
    for name, manifest in sorted(manifests.items()):
        files = [e for e in manifest["files"] if e.get("format") == fmt and e.get("tensors") and e["sha256"] not in blocked]
        if files:
            match = match_against_base(tensors, files, delta_codec)
            if any(match(t) for t in tensors):
                candidates.append((name, files, match))
    windows = sample_windows(tensors) if candidates else []
    if not windows:
        return None, [], None, len(candidates)
    data = [read(t[2] + rel, length) for t, rel, length, _ in windows]
    alone = [standalone_estimate(d, w[0][1]) / len(d) for w, d in zip(windows, data)]  # stored bytes per byte
    weight = sum(w[3] for w in windows)
    score = {c[0]: 0.0 for c in candidates}

    def window_cost(candidate, k):
        name, files, match = candidate
        t, rel, length, w = windows[k]
        m = match(t)
        covered = min(m[4] - rel, length) if m else 0
        if covered <= 0:
            return alone[k] * w
        index, base_start, kind, base_dtype, _ = m
        scale = FLOAT_BYTES[base_dtype] // FLOAT_BYTES[t[1]] if base_dtype else 1
        base_raw = read_range(p, files[index]["sha256"], base_start + rel * scale, covered * scale)
        coded = min(delta_estimate(data[k][:covered], base_raw, t[1], kind, base_dtype), alone[k] * covered)
        return (coded + alone[k] * (length - covered)) / length * w

    def run(picks, group):
        for k in picks:  # window by window, so candidates that share a base reuse its decoded regions
            for c in group:
                if score[c[0]] is not None:
                    try:
                        score[c[0]] += window_cost(c, k)
                    except (OSError, ValueError, KeyError) as e:
                        say(f"    candidate {c[0]} skipped: {e}")
                        score[c[0]] = None

    first = list(range(0, len(windows), -(-len(windows) // FIRST_ROUND)))
    run(first, candidates)
    finalists = sorted((c for c in candidates if score[c[0]] is not None), key=lambda c: score[c[0]])[:FINALISTS]
    run([k for k in range(len(windows)) if k not in first], finalists)
    ranking = sorted(((c[0], score[c[0]] / weight) for c in finalists if score[c[0]] is not None), key=lambda r: r[1])
    standalone = sum(a * w[3] for a, w in zip(alone, windows)) / weight
    best = ranking[0][0] if ranking and ranking[0][1] <= standalone * (1 - MIN_BASE_GAIN) else None
    return best, ranking, standalone, len(candidates)


def pick_base(args, p, rec, fmt, tensors, read, manifests, blocked=frozenset()):
    t0 = time.perf_counter()
    PROGRESS.at(f"{rec['path']}: choosing a base")
    best, ranking, standalone, n = choose_base(p, fmt, tensors, read, manifests, blocked, args.delta_codec)
    PROGRESS.at(rec["path"])
    if standalone is not None:
        shown = [f"{r} {share:.1%}" for r, share in ranking if r != best][:2] + [f"standalone {standalone:.1%}"]
        head = f"{best} (est. {dict(ranking)[best]:.1%}; " if best else "none ("
        say(f"  base for {rec['path']}: {head}{'; '.join(shown)}; {n} candidate(s), {time.perf_counter() - t0:.0f}s)",
            event="base", path=rec["path"], base=best)
    return best


# ----------------------------------------------------------------------------------------------- the graph of a pack


class Graph:
    """What the blobs of a pack are coded against, read from their headers when first needed (only blobs that models
    need are ever read). strict: a blob that cannot be read stops the walk (OSError / ValueError); otherwise it is
    taken as coded against nothing and noted in `unreadable`."""

    def __init__(self, p, manifests, strict=True):
        self.p, self.manifests, self.strict = p, manifests, strict
        self.edges, self.unreadable = {}, set()
        self.owners = {}  # sha -> names of the models that list it, sorted
        for name in sorted(manifests):
            for e in manifests[name]["files"]:
                users = self.owners.setdefault(e["sha256"], [])
                if name not in users:
                    users.append(name)
        self.reverse, self.depths, self.heights = None, {}, {}

    def bases(self, sha):
        if sha not in self.edges:
            try:
                self.edges[sha] = [b["sha256"] for b in blob_header(self.p, sha)["bases"]]
            except (OSError, ValueError):
                if self.strict:
                    raise
                self.edges[sha] = []
                self.unreadable.add(sha)
        return self.edges[sha]

    def changed(self, sha, bases):
        """The blob `sha` is coded against `bases` now (rebase)."""
        self.edges[sha] = list(bases)
        self.reverse, self.depths, self.heights = None, {}, {}

    def needed(self, names=None):
        """Every blob the models `names` (default: all) need: their files' blobs and, down the chain, every blob
        those are coded against."""
        names = self.manifests if names is None else names
        seen, stack = set(), [e["sha256"] for n in names for e in self.manifests[n]["files"]]
        while stack:
            sha = stack.pop()
            if sha not in seen:
                seen.add(sha)
                stack.extend(self.bases(sha))
        return seen

    def above(self, sha):
        """sha and every blob coded against it, directly or through others."""
        if self.reverse is None:
            self.reverse = {}
            for s in self.needed():
                for b in self.bases(s):
                    self.reverse.setdefault(b, set()).add(s)
        found, stack = {sha}, [sha]
        while stack:
            for s in self.reverse.get(stack.pop(), ()):
                if s not in found:
                    found.add(s)
                    stack.append(s)
        return found

    def depth(self, sha, trail=frozenset()):
        """How many blobs deep the chain below `sha` goes (0: coded against nothing)."""
        if sha not in self.depths:
            if sha in trail:
                return MAX_REFERENCE_DEPTH + 1
            self.depths[sha] = max((1 + self.depth(b, trail | {sha}) for b in self.bases(sha)), default=0)
        return self.depths[sha]

    def height(self, sha):
        """How many blobs deep the longest chain coded (directly or not) against `sha` goes down to it."""
        if sha not in self.heights:
            self.above(sha)  # builds self.reverse
            self.heights[sha] = max((1 + self.height(s) for s in self.reverse.get(sha, ())), default=0)
        return self.heights[sha]

    def model_depth(self, name):
        return max((self.depth(e["sha256"]) for e in self.manifests[name]["files"]), default=0)


def base_reads(header, frames):
    """[(base index, start, end)]: the bytes of its bases' files a blob reads, region by region (a region stored
    without its base, or dropped, reads none)."""
    reads = []
    for r, frame in zip(header["regions"], frames):
        if frame is None or "base" not in r or frame[0] & STANDALONE:
            continue
        i, offset = r["base"]
        scale = FLOAT_BYTES[r["base_dtype"]] // FLOAT_BYTES[r["dtype"]] if "base_dtype" in r else 1
        reads.append((i, offset, offset + r["length"] * scale))
    return reads


def regions_read(header, ranges):
    """The indices of the regions of a blob that any of these (start, end) byte ranges of its file overlaps."""
    starts = [r["offset"] for r in header["regions"]]
    found = set()
    for a, b in ranges:
        i = max(bisect.bisect_right(starts, a) - 1, 0)
        while i < len(starts) and starts[i] < b:
            if starts[i] + header["regions"][i]["length"] > a:
                found.add(i)
            i += 1
    return found


def write_trimmed(p, sha, keep):
    """Rewrite the blob `sha` of a pack folder as a partial blob that keeps only the regions `keep` (their frames
    copied as they are) and replace it in one step, after checking the new one; 0 if the old one is damaged (left for
    verify to report). Returns the bytes it saves."""
    span = p.blob_span(sha)
    if blob_damage(span, sha):
        return 0
    header, frames, _ = container_index(span)
    regions = [r if i in keep and frames[i] else {**r, "dropped": True} for i, r in enumerate(header["regions"])]
    head = json.dumps({**header, "partial": True, "regions": regions}).encode()
    with SafeTree(p.root).placing(blob_rel(sha), replace=True) as f, open(span[0], "rb") as src:
        out = HashingWriter(f)
        out.write(MAGIC + struct.pack("<I", len(head)) + head)
        for i, frame in enumerate(frames):
            if frame is None or i not in keep:
                continue
            codec, raw_len, pos, plen, crc = frame
            out.write(FRAME_HEAD.pack(codec, raw_len, plen, crc))
            src.seek(span[1] + pos)
            for at in range(0, plen, 16 << 20):
                out.write(src.read(min(16 << 20, plen - at)))
        f.write(out.hash.digest())
        f.flush()
        size = out.size + TRAILER
        problem = blob_damage((f.temp_path, 0, size), sha)
        if problem:
            raise ValueError(f"the trimmed blob of {sha[:12]}... is {problem}")
    forget_cached_reads()
    return span[2] - size


def trim_kept(p, manifests):
    """Trim the blobs a pack folder keeps only as bases (needed, but no model lists them): each keeps just the
    regions other blobs are coded against, down the chain (a trimmed blob reads less of its own bases), until
    nothing more can go. Returns (blobs trimmed, bytes saved)."""
    listed = {e["sha256"] for m in manifests.values() for e in m["files"]}
    trimmed = saved = 0
    while True:
        needed = Graph(p, manifests).needed()
        reads = {}
        for sha in needed:
            header, frames, _ = container_index(p.blob_span(sha))
            for i, a, b in base_reads(header, frames):
                reads.setdefault(header["bases"][i]["sha256"], []).append((a, b))
        done = 0
        for sha in sorted(needed - listed):
            header, frames, _ = container_index(p.blob_span(sha))
            keep = regions_read(header, reads.get(sha, []))
            if all(frame is None or i in keep for i, frame in enumerate(frames)):
                continue
            gain = write_trimmed(p, sha, keep)
            if gain:
                trimmed, saved, done = trimmed + 1, saved + gain, done + 1
        if not done:
            return trimmed, saved


def sweep(p):
    """Delete the blobs of a pack folder that no model needs, and the temporary files of runs that stopped (the pack
    is locked for this run alone). Nothing is deleted when a manifest, or a blob that models need, cannot be read:
    what it needs would be unknown. Returns (blobs deleted, their bytes), or None if it could not tell."""
    problems = []
    manifests = all_manifests(p, problems)
    if problems:
        say(f"  not cleaning up the pack: {problems[0][0]} cannot be used ({problems[0][1]}); run verify")
        return None
    try:
        needed = Graph(p, manifests).needed()
    except (OSError, ValueError) as e:
        say(f"  not cleaning up the pack: a blob that models need cannot be read ({e}); run verify")
        return None
    tree, count, freed = SafeTree(p.root), 0, 0
    for sha in sorted(p.blob_ids() - needed):
        rel = blob_rel(sha)
        size = os.path.getsize(p.full(rel))
        if tree.remove(rel):
            tree.prune(rel)
            count, freed = count + 1, freed + size
    for rel in p.leftovers():
        tree.remove(rel)
    forget_cached_reads()
    try:
        trimmed, saved = trim_kept(p, manifests)
    except (OSError, ValueError) as e:
        say(f"  bases kept for other models not trimmed: {e}")
        trimmed = saved = 0
    if trimmed:
        say(f"  trimmed {human(saved)} from {trimmed} blob(s) kept as bases: only what other files are coded against "
            "stays", event="trimmed", blobs=trimmed, size=saved)
    return count, freed + saved


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


# ----------------------------------------------------------------------------------------------- pack


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def base_choice(args):
    """How bases were chosen, kept in a manifest for its model, or for a file of it (pack --add, rebase --file):
    rebase --all re-chooses only those chosen "auto", and naming a model rebases its files but those set by hand."""
    return "forced" if args.base else "none" if args.no_base else "auto"


def with_choice(e, choice):
    """A manifest entry with this choice of its own ("auto": none of its own, the model's)."""
    e = {k: v for k, v in e.items() if k != "base_choice"}
    return e if choice == "auto" else {**e, "base_choice": choice}


def ratio(part, whole):
    return f"{part / whole * 100:.2f}%" if whole else "-"


def print_entry(rec, info, t0, new=True):
    """The line for one file packed (new: its blob was written now) or found stored already. It shows what the file
    adds to the pack (nothing, if stored already); `stored` in the event is the size of its blob either way."""
    note = "verified" if new else "stored already"
    if new and info["bases"]:
        models = sorted({b.get("model") or "?" for b in info["bases"]})
        note += f", {info['matched'] / max(rec['size'], 1) * 100:.0f}% matched in {', '.join(models)}"
    added = info["stored"] if new else 0
    say(f"  {rec['path']:<34} {info['method']:<18} {human(rec['size']):>12} -> {human(added):>12}"
        f"  {added / max(rec['size'], 1) * 100:6.2f}%  {note}  ({time.perf_counter() - t0:.0f}s)",
        event="file", path=rec["path"], size=rec["size"], stored=info["stored"], added=added, method=info["method"],
        new=new, bases=[{k: b.get(k) for k in ("model", "path", "sha256")} for b in info["bases"]])


def pack_blob(args, p, src, rec, fmt, tensors, regions, base, manifests, blocked=frozenset(), replace=False, keep=None):
    """Store the file at src (its record rec) as its blob: a delta against the model `base` if its tensors are found
    there (by name, in any of its files whose blobs are not `blocked`), else standalone (`regions`). It is decoded
    back before it takes its place; an ans16 delta that fails is redone as an XOR delta. Returns blob_info of it."""
    levels, chunk_size = (args.zstd_level, args.zstd_tensor_level), args.chunk_mib << 20
    files = [f for f in manifests[base]["files"] if f["sha256"] not in blocked] if base else []
    for codec in dict.fromkeys([args.delta_codec, "xor"]):
        layout, used = delta_layout(tensors, fmt, rec["size"], chunk_size, files, codec)
        bases = [{"sha256": f["sha256"], "size": f["size"], "model": base, "path": f["path"]} for f in used]
        header = {"original_size": rec["size"], "sha256": rec["sha256"], "method": method_of(fmt, layout or regions),
                  "regions": layout or regions, "bases": bases}
        try:
            base_paths = []
            for b in bases:
                PROGRESS.at(f"{rec['path']}: restoring its base {b['path']}")
                base_paths.append(cached_base(p, b["sha256"]))
            PROGRESS.at(rec["path"])
            fill = lambda out: write_container(src, out, header, base_paths, levels, args.workers)  # noqa: E731
            store_blob(p, rec["sha256"], rec["size"], fill, replace, keep)
            return blob_info(p, rec["sha256"])
        except BlobFailed as e:
            if not bases or codec == "xor" or isinstance(p, NewFilePack):
                hint = "; nothing written: pack again with --delta-codec xor" if bases and codec != "xor" else ""
                sys.exit(f"  VERIFY FAILED {rec['path']}: {e}{hint}")
            say(f"    ans16 failed verification ({e}); redoing this file as an XOR delta")
        finally:
            release_cached_bases()


def pack_model(args, p, source, name, paths, manifests, base):
    """Pack the files `paths` of the folder `source` as the model `name` and put its manifest in `manifests`: a new
    model, or with --replace in place of the model of that name, or with --add into it (files of the same path
    replaced, the others kept). A file already stored in the pack (by its SHA-256) is not stored again."""
    clash = next((n for n in manifests if n != name and name_key(n) == name_key(name)), None)
    if clash:
        fail("name_taken", f"the name {name} is taken: the model {clash} has the same manifest file "
                           f"({manifest_rel(clash)}) where case is ignored")
    if base and base not in manifests:
        sys.exit(f"--base {base}: no model of that name in {p.root}")
    say(f"hashing {len(paths)} files in {source}")
    full = {rel: os.path.join(source, *rel.split("/")) for rel in paths}
    PROGRESS.begin(f"hashing {name}", sum(os.path.getsize(f) for f in full.values()))

    def hashed(rel):
        PROGRESS.at(rel)
        return {"path": rel, "size": os.path.getsize(full[rel]), "sha256": sha256_file(full[rel], counted=True)}

    records = list(ordered_map(hashed, ((rel,) for rel in paths), hash_workers()))  # several files at once
    have = p.blob_ids()
    how = f"delta base: {base}" if base else "no delta base" if args.no_base else "delta base chosen per file"
    say(f"packing {name} -> {p.root}  ({how})")
    PROGRESS.begin(f"packing {name}", 2 * sum(r["size"] for r in records if r["sha256"] not in have))
    others = {n: m for n, m in manifests.items() if n != name}
    known = {e["sha256"]: e for m in manifests.values() for e in m["files"]}  # files the pack has already
    entries, added = [], 0
    for rec in records:
        src, t0 = full[rec["path"]], time.perf_counter()
        PROGRESS.at(rec["path"])
        damaged = partial = None
        if rec["sha256"] in have:
            damaged = blob_problem(p, rec["sha256"])
            partial = not damaged and blob_header(p, rec["sha256"]).get("partial")
            if not damaged and not partial:  # stored already: nothing is read from the source again (it may have changed since)
                same = known.get(rec["sha256"], {})
                entries.append(dict(rec, **{k: same[k] for k in ("format", "tensors") if k in same}))
                print_entry(rec, blob_info(p, rec["sha256"]), t0, new=False)
                continue
            say(f"    the stored copy of {rec['path']} " + (f"is damaged ({damaged})" if damaged else "keeps only the part of it "
                "other files are coded against") + "; storing it again")
        fmt, tensors, regions = file_regions(src, rec["size"], args.chunk_mib << 20)
        entries.append(dict(rec, **({"format": fmt, "tensors": tensors} if tensors is not None else {})))
        chosen = base
        # stored again over a blob others may be coded against: never against those (a cycle)
        blocked = Graph(p, manifests, strict=False).above(rec["sha256"]) if damaged or partial else frozenset()
        if not chosen and not args.no_base and tensors:
            chosen = pick_base(args, p, rec, fmt, tensors, file_reader(src), others, blocked)
        info = pack_blob(args, p, src, rec, fmt, tensors, regions, chosen, others, blocked, replace=bool(damaged or partial))
        have.add(rec["sha256"])
        known[rec["sha256"]] = entries[-1]
        added += info["stored"]
        print_entry(rec, info, t0)
    choice = "forced" if base else "none" if args.no_base else "auto"
    if args.add:  # into the model as it is: same paths replaced, the rest kept, in their order
        old = manifests[name]
        new = {e["path"]: with_choice(e, choice) for e in entries}  # a choice made for these files is theirs
        entries = [new.pop(e["path"], e) for e in old["files"]] + list(new.values())
        manifest = {**old, "tool": tool_version(), "files": entries, "revision": args.revision or old.get("revision")}
    else:
        manifest = {
            "format": MANIFEST_FORMAT,
            "format_version": FORMAT_VERSION,
            "name": name,
            "revision": args.revision,
            "tool": tool_version(),
            "created": now(),
            "base_choice": choice,
            "files": entries,
        }
    write_manifest(p, manifest)
    manifests[name] = manifest
    total = sum(e["size"] for e in entries)
    stored = sum(p.blob_span(sha)[2] for sha in {e["sha256"] for e in entries})
    say(f"  total {human(total)} -> {human(stored)} = {ratio(stored, total)}; new in the pack: {human(added)}",
        event="model", name=name, size=total, stored=stored, added=added)


def folder_pack_for_writing(root):
    """The pack folder `root`, locked for this run alone. A folder that does not exist, is empty or holds only _kit
    becomes one; any other folder that is no pack stops the program, as does one inside a pack folder."""
    root = os.path.abspath(root)
    inside = pack_folder_of(root)
    if inside and os.path.normcase(inside) != os.path.normcase(os.path.realpath(root)):
        sys.exit(f"{root} is inside the pack folder {inside}: pack into that pack folder, or one of its own")
    if not inside:
        if os.path.exists(root):
            if not os.path.isdir(root):
                sys.exit(f"{root} is not a folder")
            others = sorted(set(os.listdir(root)) - {KIT, LOCK_NAME})
            if others:
                sys.exit(f"{root} is not a pack folder (no {MARKER} in it) and holds other files, e.g. {others[0]}: "
                         "give a new or empty folder")
        os.makedirs(root, exist_ok=True)
    lock_archive(root, exclusive=True)
    p = FolderPack(root)
    if not os.path.exists(p.full(MARKER)):
        with contextlib.suppress(FileExistsError), SafeTree(root).placing(MARKER, replace=False) as f:
            f.write(json.dumps({**MARKER_JSON, "changed": now()}).encode())
    problem = marker_problem(p)
    if problem:
        sys.exit(f"{root}: {problem}")
    return p


def pack_into_folder(args, root, plans):
    p = folder_pack_for_writing(root)
    manifests = all_manifests(p)
    planned = {name for _, name, _ in plans}
    base = args.base if not args.base or args.base in planned else model_named(manifests, args.base, p)
    for _, name, _ in plans:  # before anything is packed
        if name in manifests and not (args.replace or args.add):
            fail("name_taken", f"{p.root} holds a model named {name} already: add --replace to replace it with these "
                               "files, or --add to add them to it (files of the same path replaced)")
        if args.add and name not in manifests:
            sys.exit(f"--add: no model named {name} in {p.root} to add the files to")
    try:
        for source, name, paths in plans:
            if name in manifests:
                say(f"adding the files of {source} to {name}" if args.add else
                    f"{name} is in the pack already: it is replaced by the files of {source}")
            pack_model(args, p, source, name, paths, manifests, base if base != name else None)
    finally:  # what no model needs now: an earlier version's blobs, or this run's if it stopped early
        result = sweep(p)
        touch_marker(p)
        PROGRESS.end()
    if result and result[0]:
        say(f"deleted {result[0]} blob(s) ({human(result[1])}) no model needs any more")
    with contextlib.suppress(OSError, ValueError):  # a hint only
        rebase_hint(args, p, all_manifests(p), planned)


def rebase_hint(args, p, manifests, new):
    """Name the models (bases chosen automatically) that may store smaller against the models just packed: those with
    tensor files of which these hold, by name and size, at least half the bytes, and more than they are coded against
    now. Their manifests alone tell it; rebase finds out."""
    graph = Graph(p, manifests, strict=False)
    found = []
    for name in sorted(set(manifests) - set(new)):
        if manifests[name].get("base_choice", "auto") != "auto":
            continue
        for e in manifests[name]["files"]:
            if not e.get("tensors"):
                continue
            above = graph.above(e["sha256"])
            offered = [f for n in new for f in manifests[n]["files"]
                       if f.get("format") == e["format"] and f.get("tensors") and f["sha256"] not in above]
            if not offered:
                continue
            match = match_against_base(e["tensors"], offered, args.delta_codec)
            could = sum(m[4] for m in map(match, e["tensors"]) if m)
            now = blob_info(p, e["sha256"])["matched"]
            if could >= e["size"] / 2 and could > now + e["size"] / 4:
                found.append(name)
                break
    if found:
        say(f"  may store smaller against {', '.join(sorted(new))}: {', '.join(found)} (rebase them to find out)",
            event="hint", models=found)


def pack_into_new_file(args, target, plans):
    """pack into a new pack file, written as it goes (no folder in between): each blob is read back from the
    unfinished file to check it, and the file gets its name only once it is complete."""
    target = os.path.abspath(target)
    folder = os.path.dirname(target)
    problem = place_problem(folder)
    if problem:
        sys.exit(f"a pack file is not written into a pack folder: {problem}")
    os.makedirs(folder, exist_ok=True)
    say(f"packing into a new pack file: {target}")
    with SafeTree(folder).placing(os.path.basename(target), replace=False) as f:
        p, manifests = NewFilePack(f, target), {}
        for source, name, paths in plans:
            pack_model(args, p, source, name, paths, manifests, args.base if args.base != name else None)
        p.zip.close()
    PROGRESS.end()
    say(f"wrote {target}: {human(os.path.getsize(target))}", event="summary", path=target, size=os.path.getsize(target))


def cmd_pack(args):
    if len(args.paths) < 2:
        sys.exit("give the folder(s) to pack, then the pack folder or the new pack file to pack into")
    *sources, target = args.paths
    sources = [os.path.abspath(s) for s in sources]
    if len(sources) > 1 and (args.name or args.files):
        sys.exit("--name and --file are for one folder at a time")
    if args.files and args.exclude:
        sys.exit("use --file or --exclude, not both")
    new_file = is_oczip(target)
    if new_file and os.path.lexists(target):
        fail("exists", f"{target} exists already: a pack file is written whole, never added to. To add a model to it, "
                       "extract it into a pack folder (it is a ZIP), pack into that folder, then export it")
    plans, keys = [], set()
    for source in sources:
        if not os.path.isdir(source):
            sys.exit(f"{source} is not a folder")
        problem = place_problem(source)
        if problem:
            sys.exit(f"cannot pack {problem}: pack the original files, from a folder of their own")
        if holds(source, target) and not args.files:  # with --file, only the files named are read
            sys.exit(f"{source} holds {target}: pack a folder that does not, or put the pack somewhere else")
        name = args.name or os.path.basename(source)
        problem = name_problem(name)
        if problem:
            sys.exit(problem + ("" if args.name else f" (taken from the folder name of {source}: choose one with --name)"))
        if name_key(name) in keys:
            sys.exit(f"two of the folders would be the model {name}: pack them one at a time, each with --name")
        keys.add(name_key(name))
        if args.files:
            paths, problems = listed_files(source, args.files)
            problems += [f"{rel} is inside {target}" for rel in paths if holds(target, os.path.join(source, *rel.split("/")))]
            if problems:
                sys.exit(f"cannot pack these files of {source}:\n  " + "\n  ".join(problems))
        else:
            paths, problems = source_files(source, args.exclude)
        if problems:
            sys.exit(f"cannot pack {source} as it is:\n  " + "\n  ".join(problems)
                     + "\nskip these with --exclude (e.g. --exclude \"" + problems[0].split(" ")[0].rstrip("/")
                     + "\"), or pack what a folder link points to as a folder of its own")
        if not paths:
            sys.exit(f"no files to pack in {source}")
        plans.append((source, name, paths))
    plans.sort(key=lambda plan: plan[1] != args.base)  # a base given with --base and packed now goes first
    if new_file and args.add:
        sys.exit("--add adds files to a model of a pack folder: a new pack file holds none yet")
    if new_file:
        if args.base and plans[0][1] != args.base:
            sys.exit(f"--base {args.base}: a new pack file holds only the models packed into it now")
        pack_into_new_file(args, target, plans)
    else:
        pack_into_folder(args, target, plans)


def pack_for_reading(path, no_lock=False):
    """The pack at `path`, for list, verify, restore and export: a pack folder locked against changes meanwhile
    (shared: other readers may run), or a pack file, which nothing changes."""
    try:
        p = open_pack(path)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot open {path}: {e.strerror if isinstance(e, OSError) and e.strerror else e}")
    if isinstance(p, FolderPack):
        lock_archive(p.root, exclusive=False, skip=no_lock)
    return p


def folder_for_change(path, exclusive=True):
    """The pack folder at `path`, locked (exclusive: for this run alone), for remove, rename and rebase."""
    if is_oczip(path):
        sys.exit(f"{path} is a pack file: it is read-only. Extract it into a pack folder (it is a ZIP), change it "
                 "there, then export it again")
    try:
        p = open_pack(path)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot open {path}: {e.strerror if isinstance(e, OSError) and e.strerror else e}")
    lock_archive(p.root, exclusive=exclusive)
    return p


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
            if n.casefold() in SKIP_FILES or excluded(rel(n), patterns):
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


def listed_files(source, paths):
    """(files, problems) as source_files, for the files named with --file only: paths relative to `source` ("/" or
    the system's separator), each a regular file reached without going through a folder link."""
    found, problems = set(), []
    is_junction = getattr(os.path, "isjunction", lambda p: False)
    for given in paths:
        rel = given.replace(os.sep, "/")
        problem = path_problem(rel)
        if problem:
            problems.append(f"{given}: {problem}")
            continue
        parts, full = rel.split("/"), source
        for i, part in enumerate(parts[:-1]):
            full = os.path.join(full, part)
            if os.path.islink(full) or is_junction(full):
                problems.append(f"{'/'.join(parts[:i + 1])}/ is a link to a folder")
                break
        else:
            full = os.path.join(full, parts[-1])
            try:
                if stat.S_ISREG(os.stat(full).st_mode):
                    found.add(rel)
                else:
                    problems.append(f"{rel} is not a regular file")
            except OSError as e:
                problems.append(f"{rel} cannot be read ({e.strerror or e})")
    return sorted(found), problems


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
    _kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
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
    import ctypes
    import fcntl

    def _rename_call():
        """The C function that renames without replacing, with its flag: (None, 0) where there is none."""
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if sys.platform.startswith("linux"):
                call, flag = libc.renameat2, 1  # RENAME_NOREPLACE
            elif sys.platform == "darwin":
                call, flag = libc.renameatx_np, 4  # RENAME_EXCL
            else:
                return None, 0
        except (OSError, AttributeError):
            return None, 0
        call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        call.restype = ctypes.c_int
        return call, flag

    _RENAME_NO_REPLACE = _rename_call()

    def rename_no_replace(src, dst, dir_fd):
        """Rename src to dst, both in the folder dir_fd, only if nothing is at dst (FileExistsError if something is),
        in one step: renameat2 RENAME_NOREPLACE on Linux, renameatx_np RENAME_EXCL on macOS. False if this system or
        this file system cannot."""
        call, flag = _RENAME_NO_REPLACE
        if call is None:
            return False
        if call(dir_fd, os.fsencode(src), dir_fd, os.fsencode(dst), flag) == 0:
            return True
        error = ctypes.get_errno()
        if error in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            return False
        raise OSError(error, os.strerror(error), dst)  # EEXIST: a FileExistsError

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
        say("not locking the pack folder (--no-lock): nothing may change it while this runs")
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
        sys.exit(f"cannot lock the pack folder ({path}: {e.strerror or e})" + ("" if exclusive else
                 "; if you are sure nothing changes the archive while this runs, add --no-lock"))
    if not locked:
        os.close(fd)
        fail("busy", "the pack folder is in use by another command: run this when it has finished" if exclusive else
             "the pack folder is being changed by another pack, remove, rename or rebase: run this when it has finished")
    _HELD_LOCK.append(fd)


def holds(folder, path):
    """Whether `path` is `folder` or inside it (real paths compared, so through links too)."""
    folder, path = (os.path.normcase(os.path.realpath(p)) for p in (folder, path))
    try:
        return os.path.commonpath([folder, path]) == folder
    except ValueError:  # another drive
        return False


class UnsafePath(OSError):
    """A folder on the way to a file is a link (or not a folder): refused, never followed."""


class NoReplaceUnsupported(OSError):
    """The file system can put a file in place only by replacing whatever is there: no hard links and no rename
    that refuses an existing name, so "only if nothing is there" cannot be one step."""


class SafeTree:
    """Creates, replaces and deletes files inside one folder (`root`, trusted as given) without following any link
    below it, so that another program swapping a folder for a link while this one runs cannot redirect a write or a
    delete outside the tree.

    POSIX: every step goes through directory handles opened with O_NOFOLLOW (os.open/mkdir/rename/unlink with
    dir_fd), so a link anywhere below root is refused, never followed. Windows (Python has no directory handles
    there): each folder on the way is checked not to be a link (reparse point) just before use, and a new file's
    real location is checked through its own handle before it is put in place; a best effort, since a folder can
    still be swapped between a check and its use.

    New files are written to a temporary file with a random name, created exclusively (an existing name, a link
    included, makes the creation fail), and written through the open handle only."""

    POSIX = os.name != "nt"

    def __init__(self, root):
        self.root = os.path.realpath(root)

    @staticmethod
    def _parts(rel):
        parts = rel.split("/")
        if any(p in ("", ".", "..") or "\0" in p or (os.name == "nt" and ("\\" in p or ":" in p)) for p in parts):
            raise ValueError(f"unsafe path {rel!r}")
        return parts

    def _open_dir(self, parts, create):
        """POSIX: a handle on root/parts, each level opened with O_NOFOLLOW (made first if `create`)."""
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for i, name in enumerate(parts):
                if create:
                    try:
                        os.mkdir(name, 0o777, dir_fd=fd)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as e:
                    if e.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise UnsafePath(f"{'/'.join(parts[:i + 1])} in {self.root} is a link or not a folder: not followed") from e
                    raise
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _windows_dir(self, parts, create):
        """Windows: root/parts, each level made if `create` and checked not to be a link (a reparse point)."""
        path = self.root
        for i, name in enumerate(parts):
            path = os.path.join(path, name)
            if create:
                try:
                    os.mkdir(path)
                except FileExistsError:
                    pass
            st = os.lstat(path)
            if not stat.S_ISDIR(st.st_mode) or st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise UnsafePath(f"{'/'.join(parts[:i + 1])} in {self.root} is a link or not a folder: not followed")
        return path

    @contextlib.contextmanager
    def placing(self, rel, replace=True):
        """Write a new file at rel ("/"-separated, inside the tree): yields a binary file object on a fresh temporary
        file next to it. When the block ends without an exception, the file is synced to disk and put in place:
        over whatever is at rel if `replace`, else only if nothing is (FileExistsError otherwise; the check and the
        placing are one step, or NoReplaceUnsupported where the file system cannot do that). On an exception the
        temporary file is deleted and rel is left as it was."""
        parts = self._parts(rel)
        folder, name = parts[:-1], parts[-1]
        temp = f".oc-{secrets.token_hex(8)}.part"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        if self.POSIX:
            dfd = self._open_dir(folder, create=True)
            where = dict(dir_fd=dfd)
            path = lambda n: n  # noqa: E731
            flags |= os.O_NOFOLLOW
        else:
            dfd, where = None, {}
            dirpath = self._windows_dir(folder, create=True)
            path = lambda n: os.path.join(dirpath, n)  # noqa: E731
        placed = False
        try:
            fd = os.open(path(temp), flags, 0o644, **where)
            with os.fdopen(fd, "wb") as f:
                f.temp_path = os.path.join(self.root, *folder, temp)  # to read it back before it is put in place
                yield f
                f.flush()
                os.fsync(f.fileno())
                if not self.POSIX:
                    self._check_location(f.fileno(), dirpath)
            if replace:
                os.replace(path(temp), path(name), **(dict(src_dir_fd=dfd, dst_dir_fd=dfd) if self.POSIX else {}))
            elif self.POSIX:
                self._move_new(temp, name, dfd, rel)
            else:
                os.rename(path(temp), path(name))  # Windows: fails if the name exists
            placed = True
        finally:
            if not placed:
                try:
                    os.unlink(path(temp), **where)
                except FileNotFoundError:
                    pass
            if dfd is not None:
                os.close(dfd)

    def _move_new(self, temp, name, dfd, rel):
        """POSIX: move temp to name, both in the folder dfd, only if nothing is at name (FileExistsError if something
        is, a link included), the check and the move being one step: a hard link at name, then temp's own name
        removed (stopped in between, name is complete and temp a leftover link to it); where the file system has no
        hard links, a rename that never replaces (rename_no_replace). Neither: NoReplaceUnsupported, never a check
        followed by a separate move."""
        try:
            os.link(temp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        except FileExistsError:
            raise
        except OSError as e:
            if not rename_no_replace(temp, name, dfd):
                raise NoReplaceUnsupported(f"cannot put {rel} in place in {self.root} without the risk of replacing a file "
                                           f"that appears there meanwhile: this file system has no hard links ({e.strerror or e}) "
                                           f"and no rename that refuses an existing name") from e
            return
        os.unlink(temp, dir_fd=dfd)

    @staticmethod
    def _check_location(fd, dirpath):
        """Windows: refuse a file whose real folder, read through its own handle, is not dirpath (a folder on the
        way was swapped for a junction or link after it was checked)."""
        buf = ctypes.create_unicode_buffer(32768)
        n = _kernel32.GetFinalPathNameByHandleW(wintypes.HANDLE(msvcrt.get_osfhandle(fd)), buf, len(buf), 0)
        if not n:
            raise ctypes.WinError(ctypes.get_last_error())
        real = buf.value[4:] if buf.value.startswith("\\\\?\\") else buf.value
        if real.startswith("UNC\\"):
            real = "\\" + real[3:]
        # dirpath is root (resolved once, at the start) plus plain folder names: any link on the way shows here
        if os.path.normcase(os.path.dirname(real)) != os.path.normcase(dirpath):
            raise UnsafePath(f"a new file landed in {os.path.dirname(real)}, not {dirpath}: a folder was swapped; refused")

    def remove(self, rel):
        """Delete the file (or link) at rel if it is there, and say whether it was; a link is deleted itself, never
        followed, and a folder is left alone."""
        parts = self._parts(rel)
        try:
            if self.POSIX:
                dfd = self._open_dir(parts[:-1], create=False)
                try:
                    os.unlink(parts[-1], dir_fd=dfd)
                finally:
                    os.close(dfd)
            else:
                os.unlink(os.path.join(self._windows_dir(parts[:-1], create=False), parts[-1]))
            return True
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            return False

    def prune(self, rel):
        """Remove the folders of rel that are now empty, deepest first (never root itself)."""
        parts = self._parts(rel)[:-1]
        while parts:
            try:
                if self.POSIX:
                    dfd = self._open_dir(parts[:-1], create=False)
                    try:
                        os.rmdir(parts[-1], dir_fd=dfd)
                    finally:
                        os.close(dfd)
                else:
                    os.rmdir(self._windows_dir(parts, create=False))
            except OSError:
                return  # not empty, gone, or not a plain folder
            parts.pop()


class HashingWriter:
    """A file object that also counts and hashes what is written through it."""

    def __init__(self, f):
        self.f, self.size, self.hash = f, 0, hashlib.sha256()

    def write(self, data):
        self.f.write(data)
        self.size += len(data)
        self.hash.update(data)


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


class _Mismatch(Exception):
    pass

def restore_target(dest_dir, parts):
    """Where restore writes the file with these path parts: inside dest_dir, with no link (symlink or junction) on
    the way below dest_dir, since restore never writes through one (SafeTree); ValueError otherwise."""
    path = dest_dir
    for i, part in enumerate(parts[:-1]):
        path = os.path.join(path, part)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError(f"{'/'.join(parts[:i + 1])} in {dest_dir} is a link: restore does not write through links")
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


def restore_plan(dest_dir, entries, flat=False):
    """[(entry, destination file, its path in dest_dir if that differs from the original path)]. A name this system
    cannot hold is changed (local_parts: "_" for each character it does not allow). With `flat`, every file goes
    straight into dest_dir, without its folders. A file that would land where another file or a folder of the model
    already goes (the same name after that change, names that differ only in case where case is ignored, or two paths
    that a link inside dest_dir joins) gets " (1)", " (2)", ... before its extension. Files whose paths needed no
    change are placed first, so they keep them. The plan depends only on the manifest and the links in dest_dir, so a
    second restore places every file the same way. ValueError if a path would leave dest_dir."""
    parts = [local_parts(e["path"])[-1:] if flat else local_parts(e["path"]) for e in entries]
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
        plan[i] = (entries[i], dst, rel, rel != entries[i]["path"])
    return plan


def restore_entry(p, entry, tree, rel, replace):
    """Write the original bytes of a model's file to rel in tree (SafeTree.placing): into a new temporary file that
    is put in place only once its size and SHA-256 are right, so on a problem rel is left as it was; over a file
    already there only if `replace` (else the placing fails, in one step, if a file of that name has appeared).
    Returns None, or (the kind of failure, ERROR_CODES; the problem)."""
    problem = blob_problem(p, entry["sha256"])
    if problem:
        return "damaged", problem
    try:
        with tree.placing(rel, replace) as f:
            out = HashingWriter(f)
            for chunk in iter_blob_bytes(p, entry["sha256"]):
                out.write(chunk)
                PROGRESS.add(len(chunk))
            if out.size != entry["size"] or out.hash.hexdigest() != entry["sha256"]:
                raise _Mismatch
    except _Mismatch:
        return "damaged", "restored bytes differ from the original SHA-256"
    except FileExistsError:
        return "exists", "a file of that name appeared in the destination while restoring: not overwritten (--overwrite replaces it)"
    except NoReplaceUnsupported as e:
        return "other", f"{e}; restore into an empty folder with --overwrite instead"
    except UnsafePath as e:
        return "other", str(e)
    except ValueError as e:  # a frame that does not decode or fails its CRC
        return "damaged", f"restore failed: {e}"
    except Exception as e:
        return "no_room" if isinstance(e, OSError) and e.errno == errno.ENOSPC else "other", f"restore failed: {e}"
    return None


def cmd_restore(args):
    """Every path is placed (restore_plan) and checked before anything is written. A destination file that already
    holds the right bytes is kept; one with other contents is replaced only with --overwrite."""
    p = pack_for_reading(args.pack, args.no_lock)
    manifests = all_manifests(p, [])  # only this model's manifest has to be sound
    name = model_named(manifests, args.model, p)
    manifest = manifests[name]
    unknown = sorted(set(args.files) - {e["path"] for e in manifest["files"]})
    if unknown:
        sys.exit(f"{name} has no file {unknown[0]!r} (paths as the pack lists them, with \"/\")")
    chosen = [e for e in manifest["files"] if not args.files or e["path"] in set(args.files)]
    try:  # placed among all the model's files, so that each lands where it always does; with --flat, among those chosen
        plan = restore_plan(args.dest_dir, chosen if args.flat else manifest["files"], flat=args.flat)
    except ValueError as e:
        sys.exit(f"cannot restore {name}: {e}")
    wanted = {e["path"] for e in chosen}
    plan = [p_ for p_ in plan if p_[0]["path"] in wanted]
    for folder in dict.fromkeys([args.dest_dir, *(os.path.dirname(dst) for _, dst, _, _ in plan)]):
        problem = place_problem(folder)
        if problem:
            sys.exit(f"refusing to restore there: {problem}. Choose another folder (--overwrite does not change this)")
    targets, conflicts = [], []
    for entry, dst, rel, renamed in plan:
        shown = entry["path"] + (f"  (as {rel})" if renamed else "")
        if os.path.isdir(dst) and not os.path.islink(dst):
            sys.exit(f"cannot restore {shown}: a folder of that name is in the way in {args.dest_dir}")
        present = False
        if os.path.lexists(dst):
            present = (os.path.isfile(dst) and not os.path.islink(dst) and os.path.getsize(dst) == entry["size"]
                       and sha256_file(dst) == entry["sha256"])
            if not present:
                conflicts.append(shown)
        targets.append((entry, rel, shown, present))
    if conflicts and not args.overwrite:
        fail("exists", f"{len(conflicts)} file(s) already exist in {args.dest_dir} with other contents, e.g. "
                       f"{conflicts[0]}: restore into an empty folder, or add --overwrite to replace them")
    os.makedirs(args.dest_dir, exist_ok=True)
    tree = SafeTree(args.dest_dir)
    PROGRESS.begin("checking blobs")
    shas = dict.fromkeys(e["sha256"] for e, _, _, present in targets if not present)
    for _ in ordered_map(blob_problem, ((p, sha) for sha in shas), hash_workers()):
        pass  # several at once; an intact one is remembered (_INTACT), what is wrong with another restore_entry says
    PROGRESS.begin(f"restoring {name}", sum(e["size"] for e, _, _, present in targets if not present))
    for entry, rel, shown, present in targets:
        if present:
            say(f"  ok    {shown}  (already there)", event="file", path=entry["path"], dest=rel, written=False)
            continue
        PROGRESS.at(entry["path"])
        problem = restore_entry(p, entry, tree, rel, replace=args.overwrite)
        if problem:
            fail(problem[0], f"  cannot restore {shown}: {problem[1]}")
        say(f"  ok    {shown}", event="file", path=entry["path"], dest=rel, written=True)
    PROGRESS.end()
    renamed = sum(1 for e, _, rel, r in plan if r and (not args.flat or rel != e["path"].rsplit("/", 1)[-1]))
    if renamed:
        say(f"  {renamed} file(s) restored under another name: a character this system does not allow in names "
            "became \"_\", or a \" (n)\" keeps two files apart")
    what = f"{len(plan)} of {len(manifest['files'])} files of {name}" if args.files else name
    say(f"restored {what}" + (f" @ {manifest['revision']}" if manifest.get("revision") else "") + f" into {args.dest_dir}",
        event="summary", model=name, files=len(plan), dest=args.dest_dir)


# ----------------------------------------------------------------------------------------------- verify


def scan(p, manifests, names, paths=(), passed=None):
    """Verify the files of the models `names` (only those of `paths`, with one model) in three passes, so that a file
    that fails only because of what it is coded against is told apart from one whose own blob is at fault:
    1. structure, from the headers alone: every blob these files need (their own and, down the chain, every base) is
       there, readable and naming its file; each base is named with the size it holds; no chain goes round or deeper
       than MAX_REFERENCE_DEPTH;
    2. content: every blob found sound is read whole against its SHA-256 (blob_damage), then every file whose chain is
       sound is restored and compared with its own (once per blob; passed(name, entry) is called for each that is);
    3. structure again: a file is lost if its blob, or any blob down its chain, was found at fault.
    Returns (faults {blob: what is wrong with it, as "its blob ..." goes on}, lost [{model, path, size, sha256,
    weights, causes, own, problem}], files checked, graph, headers {blob: its header})."""
    graph = Graph(p, manifests, strict=False)
    chosen = [(n, e) for n in names for e in manifests[n]["files"] if not paths or e["path"] in paths]
    faults, headers, stack = {}, {}, [e["sha256"] for _, e in chosen]
    while stack:  # 1
        sha = stack.pop()
        if sha in graph.edges:
            continue
        graph.edges[sha] = []
        try:
            headers[sha] = blob_header(p, sha)
        except FileNotFoundError:
            faults[sha] = "is missing from the pack"
            continue
        except (OSError, ValueError) as e:
            faults[sha] = f"cannot be read ({e})"
            continue
        graph.edges[sha] = [b["sha256"] for b in headers[sha]["bases"]]
        stack.extend(graph.edges[sha])
    for sha, header in headers.items():
        for b in header["bases"]:
            held = headers.get(b["sha256"], {}).get("original_size")
            if held is not None and held != b["size"] and sha not in faults:
                faults[sha] = f"names blob {b['sha256'][:12]}... as a base of {b['size']} bytes, but that holds {held}"
        if sha not in faults and graph.depth(sha) > MAX_REFERENCE_DEPTH:
            faults[sha] = f"is in a chain of bases that goes round, or is deeper than {MAX_REFERENCE_DEPTH}"
        frames = container_index(p.blob_span(sha))[1]
        for i, a, b in base_reads(header, frames) if sha not in faults else ():
            base = headers.get(header["bases"][i]["sha256"])
            if base and base.get("partial") and any(r.get("dropped") for j, r in enumerate(base["regions"])
                                                    if j in regions_read(base, [(a, b)])):
                faults[sha] = f"is coded against a part of {blob_name(manifests, headers, base['sha256'])} that is not kept"
                break
    for _, e in chosen:
        if headers.get(e["sha256"], {}).get("partial") and e["sha256"] not in faults:
            faults[e["sha256"]] = "keeps only the part of its file other files are coded against"

    def chain(sha):
        found, todo = set(), [sha]
        while todo:
            s = todo.pop()
            if s not in found:
                found.add(s)
                todo.extend(graph.edges.get(s, ()))
        return found

    blobs = [sha for sha in sorted(headers) if sha not in faults]  # 2
    PROGRESS.begin("checking blobs", sum(p.blob_span(sha)[2] for sha in blobs))
    damage = lambda span, sha: (sha, blob_damage(span, sha, counted=True))  # noqa: E731
    for sha, problem in ordered_map(damage, ((p.blob_span(sha), sha) for sha in blobs), hash_workers()):
        if problem:
            faults[sha] = f"is {problem}"
    for sha in headers:
        if not chain(sha) & faults.keys():
            _INTACT.add((p.root, sha))  # checked: decoding it reads it once more, not twice
    entries = {}  # blob -> the (model, entry) pairs that are its file, in the order chosen
    for name, e in chosen:
        entries.setdefault(e["sha256"], []).append((name, e))
    sound = [sha for sha in entries if not chain(sha) & faults.keys()]
    users = {}  # blob -> how many of those still to verify are coded against it
    for sha in sound:
        for b in set(graph.edges[sha]):
            users[b] = users.get(b, 0) + 1
    PROGRESS.begin("verifying", sum(entries[sha][0][1]["size"] for sha in sound))

    def restore(sha):
        """(bytes, whether their SHA-256 is the file's) of the restored file; if blobs still to verify are coded against
        it, it is kept for them as it is restored (instead of being restored again for each)."""
        h, n, ok = hashlib.sha256(), 0, False
        copy = SideCopy() if users.get(sha) else None
        try:
            for chunk in iter_blob_bytes(p, sha):
                h.update(chunk)
                n += len(chunk)
                PROGRESS.add(len(chunk))
                if copy:
                    copy.write(chunk)
            ok = h.hexdigest() == sha
        finally:
            if copy and (path := copy.done(ok)):
                _BASE_CACHE[(p.root, sha)] = path
        return n, ok

    restored, wrong = {}, {}
    try:
        for sha in bases_first(sound, graph.edges):
            name, e = entries[sha][0]
            PROGRESS.at(f"{name}:{e['path']}")
            if not chain(sha) & faults.keys():
                try:
                    restored[sha], ok = restore(sha)
                except BaseFailed as err:
                    faults.setdefault(err.sha, f"does not restore its file ({err.problem})")
                except Exception as err:
                    faults[sha] = f"does not restore its file ({err})"
                else:
                    if not ok:
                        faults[sha] = "restores other bytes than its file's (their SHA-256 differs)"
            for b in set(graph.edges[sha]):
                users[b] -= 1
                if not users[b] and (p.root, b) in _BASE_CACHE:  # nothing more coded against it to verify
                    os.remove(_BASE_CACHE.pop((p.root, b)))
            for name, e in entries[sha]:
                if sha not in faults and not chain(sha) & faults.keys():
                    if restored.get(sha) != e["size"]:
                        wrong[(name, e["path"])] = f"the pack lists it with {e['size']} bytes, its blob holds {restored.get(sha)}"
                    elif passed:
                        passed(name, e)
    finally:
        for sha in users:
            with contextlib.suppress(OSError):
                if (p.root, sha) in _BASE_CACHE:
                    os.remove(_BASE_CACHE.pop((p.root, sha)))
    PROGRESS.end()
    lost = []
    for name, e in chosen:  # 3
        sha = e["sha256"]
        causes = sorted(chain(sha) & faults.keys(), key=lambda s: (s != sha, s))
        if not causes and (name, e["path"]) not in wrong:
            continue
        if not causes:
            problem = wrong[(name, e["path"])]
        elif causes[0] == sha:
            problem = f"its blob {faults[sha]}"
        else:
            problem = f"through {blob_name(manifests, headers, causes[0])}: its blob {faults[causes[0]]}"
        lost.append({"model": name, "path": e["path"], "size": e["size"], "sha256": sha, "weights": bool(e.get("tensors")),
                     "causes": causes, "own": not causes or causes[0] == sha, "problem": problem})
    return faults, lost, len(chosen), graph, headers


def bases_first(blobs, edges):
    """`blobs` in their order, but each that others of them are coded against just before the first of those, after
    its own bases: a restored base kept for what follows is then kept the shortest while (edges: blob -> its bases)."""
    wanted = set(blobs)
    needed = {b for sha in blobs for b in edges.get(sha, ()) if b in wanted}
    order, placed = [], set()

    def place(sha):  # recursion only down a chain of bases, which scan has found sound (no cycle, at most 32 deep)
        if sha not in placed:
            placed.add(sha)
            for b in edges.get(sha, ()):
                if b in wanted:
                    place(b)
            order.append(sha)

    for sha in blobs:
        if sha not in needed:
            place(sha)
    for sha in blobs:
        place(sha)
    return order


def blob_name(manifests, headers, sha):
    """A blob for people: the file (model: path) it is, else the one a blob coded against it names, else its hash."""
    users = [(n, e["path"]) for n in sorted(manifests) for e in manifests[n]["files"] if e["sha256"] == sha]
    if users:
        return f"{users[0][0]}: {users[0][1]}" + (f" (and {len(users) - 1} more)" if len(users) > 1 else "")
    named = next((b for h in headers.values() for b in h["bases"] if b["sha256"] == sha and b.get("path")), None)
    if named:
        return f"{named.get('model') or '?'}: {named['path']} (no model lists it now)"
    return f"blob {sha[:12]}..."


def verify_models(p, manifests, names, paths=()):
    """Verify every file of these models (or only the files of these paths: scan), printing a line for each; returns
    (failures, files checked, what scan found)."""
    def passed(name, e):
        say(f"  ok    {name}:{e['path']}", event="ok", model=name, path=e["path"], problem=None)

    found = scan(p, manifests, names, paths, passed)
    for item in found[1]:
        say(f"  FAIL  {item['model']}:{item['path']}  -- {item['problem']}", event="fail", model=item["model"],
            path=item["path"], problem=item["problem"])
    return len(found[1]), found[2], found


def damage_report(p, manifests, names, found, problems):
    """What verify found wrong, as (lines for people, data for programs): the blobs at fault and the files lost (their
    own blob at fault, or one down their chain); the models with lost files, and the models that need data of these
    (down the chain); what re-supplying each original would heal, and how."""
    faults, lost, _, graph, headers = found
    name_of = lambda sha: blob_name(manifests, headers, sha)  # noqa: E731
    damaged = {}
    for item in lost:
        damaged.setdefault(item["model"], []).append(item)
    gone, affected = set(damaged), []
    while True:  # the models that need, directly or through others, a blob only removed models list
        listed = {e["sha256"] for n in gone for e in manifests[n]["files"]}
        more = sorted(n for n in manifests if n not in gone
                      and (graph.needed([n]) - {e["sha256"] for e in manifests[n]["files"]}) & listed)
        if not more:
            break
        affected += more
        gone |= set(more)
    plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"  # noqa: E731
    lines = ["DAMAGE REPORT", f"  pack: {p.root}"]
    if problems:
        lines.append(f"  manifests that cannot be read ({len(problems)}): their models' files are not known")
        lines += [f"    {rel}: {problem}" for rel, problem in problems]
    if faults:
        lines.append(f"  at fault ({plural(len(faults), 'blob')}):")
        lines += [f"    {name_of(sha)}: its blob {faults[sha]}" for sha in sorted(faults, key=name_of)]
    if lost:
        lines.append(f"  lost ({plural(len(lost), 'file')}):")
        for item in lost:
            how = "its own blob" if item["own"] else "through " + ", ".join(name_of(s) for s in item["causes"])
            lines.append(f"    {item['model']}: {item['path']}  {human(item['size']).strip()}"
                         f"{'  weights' if item['weights'] else ''}  ({how})")
    models = []
    if damaged:
        lines.append(f"  models with lost files ({len(damaged)}):")
        for name in sorted(damaged):
            files = manifests[name]["files"]
            weights = sum(1 for e in files if e.get("tensors"))
            hit = sum(1 for item in damaged[name] if item["weights"])
            models.append({"name": name, "files": len(files), "lost": len(damaged[name]), "weights": weights,
                           "lost_weights": hit})
            lines.append(f"    {name}  {len(damaged[name])} of {plural(len(files), 'file')} lost"
                         + (f" (weight files: {hit} of {weights})" if hit else ""))
    if affected:
        lines.append(f"  models that need data of those ({len(affected)}): {', '.join(affected)}")
    intact = [n for n in names if n not in damaged]
    lines.append(f"  intact: {plural(len(intact), 'model')} verified")
    heal = [{"sha256": sha, "file": name_of(sha), "heals": sum(1 for item in lost if item["causes"] == [sha])}
            for sha in sorted(faults, key=name_of)]
    if heal:
        lines.append("  to heal, store the original file again: pack <folder holding it> <pack> --name MODEL --add --file "
                     "PATH")
        lines.append("    (what is coded against it heals with it; a file no model lists now can be added to any model)")
        lines += [f"    {h['file']}  sha256 {h['sha256']}  heals {plural(h['heals'], 'lost file')}" for h in heal]
    if lost:
        lines.append("  to give a lost file up: remove <pack> MODEL --file PATH (or the whole model: remove <pack> MODEL)")
    data = {"pack": p.root, "faults": [{"sha256": sha, "problem": faults[sha], "file": name_of(sha)} for sha in faults],
            "lost": lost, "models": models, "affected": affected, "heal": heal,
            "manifests": [{"path": rel, "problem": problem} for rel, problem in problems]}
    return lines, data


def unused_blobs(p, manifests):
    """(count, bytes) of the blobs in a pack that no model needs, or None if that cannot be told."""
    graph = Graph(p, manifests, strict=False)
    unused = p.blob_ids() - graph.needed()
    if graph.unreadable:
        return None
    return len(unused), sum(p.blob_span(sha)[2] for sha in unused)


def cmd_verify(args):
    p = pack_for_reading(args.pack, args.no_lock)
    problems = []
    manifests = all_manifests(p, problems)
    names = list(dict.fromkeys(model_named(manifests, n, p) for n in args.models)) or sorted(manifests)
    if not names and not problems:
        sys.exit(f"no models in {p.root}: nothing verified")
    if args.files:
        if len(args.models) != 1:
            sys.exit("--file: name the one model these files are of")
        unknown = sorted(set(args.files) - {e["path"] for e in manifests[names[0]]["files"]})
        if unknown:
            sys.exit(f"{names[0]} has no file {unknown[0]!r} (paths as the pack lists them, with \"/\")")
    failures = 0
    for rel, problem in problems:
        failures += 1
        say(f"  FAIL  {rel}  -- {problem}", event="fail", path=rel, problem=problem)
    found, checked, findings = verify_models(p, manifests, names, set(args.files))
    failures += found
    if not args.models:
        unused = unused_blobs(p, manifests)
        if unused and unused[0]:
            later = ": the next pack, remove or rebase deletes them" if isinstance(p, FolderPack) else ""
            say(f"  {unused[0]} blob(s) ({human(unused[1])}) that no model needs{later}", event="unused",
                blobs=unused[0], size=unused[1])
    if failures:
        lines, data = damage_report(p, manifests, names, findings, problems)
        say("\n".join(lines), event="damage", **data)
        fail("damaged", f"{failures} FAILURE(S): what is lost, why, and what to do are in the damage report above")
    if not checked:
        sys.exit("no files found: nothing verified")
    say(f"ALL VERIFIED ({checked} files)", event="summary", checked=checked)


# ----------------------------------------------------------------------------------------------- list


def owner_of(graph, base):
    """The model a base blob belongs to, for people: the one its header names if that model still lists it, else the
    first model that does; None if no model lists it (a removed model's file, kept for the models that need it)."""
    owners = graph.owners.get(base["sha256"], [])
    return base.get("model") if base.get("model") in owners else owners[0] if owners else None


def covered(ranges):
    """How many bytes (start, end) ranges cover, overlaps counted once."""
    total, end = 0, -1
    for a, b in sorted(ranges):
        if b > end:
            total += b - max(a, end)
            end = b
    return total


def model_summary(p, graph, name):
    """What list shows of one model: its files with how each is stored and what it is coded against, and the models
    it needs: {"name", "revision", "created", "base_choice", "files": [...], "size", "stored", "needs",
    "needs_removed", "base", "usage"}. "base" is the model most of its coded bytes refer to, its place in the tree of
    the pack; "usage" says how much of each model it needs it reads ({model: share of that model's bytes})."""
    manifest = graph.manifests[name]
    files, weight, removed, sizes, reads = [], {}, set(), {}, {}
    for e in manifest["files"]:
        row = {"path": e["path"], "size": e["size"], "sha256": e["sha256"], "base_choice": e.get("base_choice", "auto"),
               "stored": None, "method": None, "bases": [],
               "shared_with": [n for n in graph.owners.get(e["sha256"], []) if n != name]}
        try:
            span = p.blob_span(e["sha256"])
            header, frames, _ = container_index(span)
        except (OSError, ValueError) as error:
            row["problem"] = f"its blob cannot be read ({error})"
            files.append(row)
            continue
        mine = {}  # this file's: base file -> the byte ranges it reads of it
        for i, a, b in base_reads(header, frames):
            mine.setdefault(header["bases"][i]["sha256"], []).append((a, b))
            reads.setdefault(header["bases"][i]["sha256"], []).append((a, b))
        row.update(stored=span[2], method=header.get("method"))
        sizes[e["sha256"]] = span[2]
        owners = [owner_of(graph, b) for b in header["bases"]]
        for b, owner in zip(header["bases"], owners):
            row["bases"].append({"model": owner or b.get("model"), "path": b.get("path"), "sha256": b["sha256"],
                                 "removed": owner is None, "size": b["size"], "used": covered(mine.get(b["sha256"], []))})
            if owner is None:
                removed.add(b.get("model") or f"blob {b['sha256'][:12]}...")
        for r in header["regions"]:
            if "base" in r and owners[r["base"][0]] not in (None, name):
                weight[owners[r["base"][0]]] = weight.get(owners[r["base"][0]], 0) + r["length"]
        files.append(row)
    complete = all(f["stored"] is not None for f in files)
    usage = {}
    for model in weight:
        their = {e["sha256"]: e["size"] for e in graph.manifests[model]["files"]}
        usage[model] = round(sum(covered(reads.get(sha, [])) for sha in their) / max(sum(their.values()), 1), 4)
    return {"name": name, "revision": manifest.get("revision"), "created": manifest.get("created"),
            "base_choice": manifest.get("base_choice", "auto"), "files": files,
            "size": sum(e["size"] for e in manifest["files"]), "stored": sum(sizes.values()) if complete else None,
            "needs": sorted(weight), "needs_removed": sorted(removed), "base": max(weight, key=weight.get) if weight else None,
            "usage": usage}


def pack_summary(p, manifests):
    """What list shows of a pack: every model (model_summary, without its files) with the models that need it; the
    blobs kept for the models that need them though no model lists them (files of removed models); and the blobs no
    model needs."""
    graph = Graph(p, manifests, strict=False)
    models = {}
    for name in sorted(manifests):
        summary = model_summary(p, graph, name)
        summary["blobs"] = {f["sha256"]: f["stored"] for f in summary["files"]}  # so sums over models count each once
        summary["files"] = len(summary["files"])
        models[name] = summary
    for m in models.values():
        m["needed_by"] = sorted(n for n, other in models.items() if m["name"] in other["needs"])
    needed, ids = graph.needed(), p.blob_ids()
    kept = sorted(needed - set(graph.owners) - graph.unreadable)
    users = {sha: [] for sha in kept}
    for name in sorted(manifests):
        for sha in graph.needed([name]) & set(kept):
            users[sha].append(name)
    names = {}
    for sha in needed:
        for b in blob_header(p, sha)["bases"] if sha not in graph.unreadable else ():
            names.setdefault(b["sha256"], (b.get("model"), b.get("path")))
    unused = ids - needed

    def size(sha):
        try:
            return p.blob_span(sha)[2]
        except (OSError, ValueError):
            return 0

    return {"pack": p.root, "kind": p.kind, "models": list(models.values()),
            "kept": [{"sha256": sha, "size": size(sha), "model": names.get(sha, (None, None))[0],
                      "path": names.get(sha, (None, None))[1], "needed_by": users[sha]} for sha in kept],
            "unused": {"blobs": len(unused), "size": sum(size(sha) for sha in unused)},
            "blobs": len(ids), "stored": sum(size(sha) for sha in ids), "unreadable": sorted(graph.unreadable)}


def stored_text(size, stored):
    return f"{human(size):>12} -> {human(stored) if stored is not None else '?':>12}  " + (
        f"{stored / size * 100:6.2f}%" if stored is not None and size else " " * 7)


def cmd_list(args):
    p = pack_for_reading(args.pack, args.no_lock)
    problems = []
    manifests = all_manifests(p, problems)
    for rel, problem in problems:
        say(f"  ! {rel} cannot be used: {problem}", event="fail", path=rel, problem=problem)
    if args.model:
        name = model_named(manifests, args.model, p)
        m = model_summary(p, Graph(p, manifests, strict=False), name)
        head = f"{name}: {len(m['files'])} file{'' if len(m['files']) == 1 else 's'}, {stored_text(m['size'], m['stored']).strip()}" + (
            f", packed {m['created']}" if m.get("created") else "") + (f" @ {m['revision']}" if m.get("revision") else "")
        if JSON_OUT:
            return say(head, event="model", **m)
        say(head)
        for f in m["files"]:
            against = ", ".join(sorted({f"{b['model'] or b['sha256'][:12]}{' (removed)' if b['removed'] else ''}"
                                        for b in f["bases"]}))
            note = f.get("problem") or (f"against {against}" if against else "") + (
                f"  = also in {', '.join(f['shared_with'])}" if f["shared_with"] else "")
            say(f"  {f['path']:<40} {stored_text(f['size'], f['stored'])}  {f['method'] or '?':<18} {note}".rstrip())
        return
    s = pack_summary(p, manifests)
    head = (f"{s['kind']} {s['pack']}: {len(s['models'])} model(s), {s['blobs']} blob(s), {human(s['stored'])} stored")
    if JSON_OUT:
        return say(head, event="pack", **s)
    say(head)
    models = {m["name"]: m for m in s["models"]}
    children, shown = {}, set()
    for m in models.values():
        if m["base"] in models:
            children.setdefault(m["base"], []).append(m["name"])

    def show(name, level):
        if name in shown:
            return
        shown.add(name)
        m = models[name]
        needs = [n for n in m["needs"] if n != m["base"] or level == 0] + [f"{n} (removed)" for n in m["needs_removed"]]
        say(f"  {stored_text(m['size'], m['stored'])}  {'  ' * level}{name}"  # the numbers first: they line up
            + (f"  needs {', '.join(needs)}" if needs else ""))
        for child in sorted(children.get(name, ())):
            show(child, level + 1)

    for name in sorted(n for n, m in models.items() if m["base"] not in models):
        show(name, 0)
    for name in sorted(models):  # models whose bases go round in a circle (each needs a file of the other)
        show(name, 0)
    if s["kept"]:
        by = sorted({n for k in s["kept"] for n in k["needed_by"]})
        of = sorted({k["model"] for k in s["kept"] if k["model"]})
        say(f"  kept: {len(s['kept'])} blob(s), {human(sum(k['size'] for k in s['kept']))}, of removed model(s) "
            f"{', '.join(of) or '?'}, for {', '.join(by)} (rebase those to free them)")
    if s["unused"]["blobs"]:
        say(f"  {s['unused']['blobs']} blob(s) ({human(s['unused']['size'])}) that no model needs"
            + (": the next pack, remove or rebase deletes them" if isinstance(p, FolderPack) else ""))
    if s["unreadable"]:
        say(f"  ! {len(s['unreadable'])} blob(s) cannot be read: run verify")


# ----------------------------------------------------------------------------------------------- remove / rename


def removal(p, manifests, changed):
    """What changing manifests as `changed` says ({name: its new manifest, or None to remove the model}) frees and
    keeps: (blobs freed, blobs of the removed files kept, {kept blob: the models that need it}). Blobs that cannot be
    read are taken as coded against nothing, so that what was lost can be removed; sweep deletes nothing while a blob
    that models still need cannot be read."""
    graph = Graph(p, manifests, strict=False)
    before = graph.needed()
    after_manifests = {n: changed.get(n, m) for n, m in manifests.items() if changed.get(n, m) is not None}
    after_graph = Graph(p, after_manifests, strict=False)
    after_graph.edges = graph.edges  # the blobs have not changed: what was read stays true
    after = after_graph.needed()
    gone = {e["sha256"] for n, m in changed.items() for e in manifests[n]["files"]}
    gone -= {e["sha256"] for m in after_manifests.values() for e in m["files"]}  # still listed: nothing removed
    kept = gone & after
    users = {sha: [] for sha in kept}
    for name in sorted(after_manifests):
        for sha in after_graph.needed([name]) & kept:
            users[sha].append(name)
    return before - after, kept, users


def blobs_size(p, shas):
    total = 0
    for sha in shas:
        with contextlib.suppress(OSError, ValueError):  # (a blob that is missing takes no room)
            total += p.blob_span(sha)[2]
    return total


def cmd_remove(args):
    p = folder_for_change(args.pack, exclusive=not args.dry_run)
    if not args.models:
        sys.exit("name the models to remove (or the one model whose files to remove)")
    if args.files and len(args.models) != 1:
        sys.exit("--file removes files of one model: name exactly one")
    manifests = all_manifests(p)
    names = list(dict.fromkeys(model_named(manifests, n, p) for n in args.models))
    if args.files:
        manifest = manifests[names[0]]
        unknown = sorted(set(args.files) - {e["path"] for e in manifest["files"]})
        if unknown:
            sys.exit(f"{names[0]} has no file {unknown[0]!r} (paths as the pack lists them, with \"/\")")
        left = [e for e in manifest["files"] if e["path"] not in set(args.files)]
        changed = {names[0]: {**manifest, "files": left} if left else None}
        what = f"{len(args.files)} of the {len(manifest['files'])} files of {names[0]}" + (
            "" if left else " (all of them: the model goes)")
    else:
        changed = dict.fromkeys(names)
        what = ", ".join(names)
    try:
        freed, kept, users = removal(p, manifests, changed)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot tell what the models of {p.root} need ({e}): nothing removed; run verify")
    freed_size, kept_size = blobs_size(p, freed), blobs_size(p, kept)
    say(f"{'would remove' if args.dry_run else 'removing'} {what}")
    say(f"  {'would free' if args.dry_run else 'frees'} {human(freed_size)} ({len(freed)} blob(s))", event="freed",
        blobs=len(freed), size=freed_size)
    if kept:
        by = sorted({n for us in users.values() for n in us})
        say(f"  keeps {human(kept_size)} ({len(kept)} blob(s)) that other models still need: {', '.join(by)}"
            f"{' (as their base: rebase lets it go when that makes the pack smaller)' if any(users.values()) else ''}",
            event="kept", blobs=len(kept), size=kept_size,
            needed_by=by)
    if args.dry_run:
        say("dry run: nothing changed")
        return
    tree = SafeTree(p.root)
    for name, manifest in changed.items():
        if manifest is None:
            tree.remove(manifest_rel(name))
        else:
            write_manifest(p, manifest)
    result = sweep(p)
    touch_marker(p)
    if result is not None:
        say(f"removed {what}: {human(result[1])} freed", event="summary", blobs=result[0], size=result[1])


def rename_file(p, manifests, name, old, new):
    """Give a file of a model another path: only the manifest changes (the blob is named by the file's content)."""
    manifest = manifests[name]
    paths = [e["path"] for e in manifest["files"]]
    if old not in paths:
        sys.exit(f"{name} has no file {old!r} (paths as the pack lists them, with \"/\")")
    problem = path_problem(new)
    if problem:
        sys.exit(problem)
    if new in paths:
        fail("name_taken", f"{name} has a file {new!r} already")
    files = [{**e, "path": new} if e["path"] == old else e for e in manifest["files"]]
    write_manifest(p, {**manifest, "files": files})
    touch_marker(p)
    say(f"renamed {name}:{old} to {new}", event="summary", model=name, old=old, new=new)


def cmd_rename(args):
    p = folder_for_change(args.pack)
    manifests = all_manifests(p)
    if args.file is not None:
        return rename_file(p, manifests, model_named(manifests, args.model, p), args.file, args.new_name)
    old, new = model_named(manifests, args.model, p), args.new_name
    problem = name_problem(new)
    if problem:
        sys.exit(problem)
    if new == old:
        sys.exit(f"{old} has that name already")
    clash = next((n for n in manifests if n != old and name_key(n) == name_key(new)), None)
    if clash:
        fail("name_taken", f"the name {new} is taken: the model {clash} has the same manifest file "
                           f"({manifest_rel(clash)})" + (" where case is ignored" if clash != new else ""))
    manifest = {**manifests[old], "name": new}
    tree = SafeTree(p.root)
    if name_key(old) == name_key(new):  # only the case changes: the file is renamed, then rewritten
        os.rename(p.full(manifest_rel(old)), p.full(manifest_rel(new)))
        write_manifest(p, manifest)
    else:
        with tree.placing(manifest_rel(new), replace=False) as f:
            f.write(json.dumps(manifest, indent=1).encode("utf-8"))
        tree.remove(manifest_rel(old))
    touch_marker(p)
    say(f"renamed {old} to {new}", event="summary", old=old, new=new)


# ----------------------------------------------------------------------------------------------- rebase
#
# rebase re-chooses the base of every file of a model already in the pack, as if it were packed again now. The base is
# chosen from the stored bytes (read_range), so a file is restored (into OC_TMP) only if its base changes. Its new blob
# restores the same file, so it replaces the old one under the same name once it is checked, and every model that
# needs that blob (as a file of its own, or as a base) is unaffected. A base never makes a cycle (no blob coded,
# directly or not, against the one being re-packed is used) or a chain longer than MAX_REFERENCE_DEPTH.


def file_share(p, e, others, blocked, delta_codec):
    """The share of its size a file (an entry whose blob is in the pack) is estimated to be stored in, coded against
    the best of `others` (models) not in `blocked`, or standalone."""
    read = lambda start, length: read_range(p, e["sha256"], start, length)  # noqa: E731
    best, ranking, standalone, _ = choose_base(p, e["format"], e["tensors"], read, others, blocked, delta_codec)
    if best:
        return dict(ranking)[best]
    if standalone is not None:
        return standalone
    windows = sample_windows(e["tensors"])  # (no candidate: choose_base did not sample)
    if not windows:
        return 1.0
    alone = [standalone_estimate(read(t[2] + rel, length), t[1]) / length for t, rel, length, _ in windows]
    return sum(a * w[3] for a, w in zip(alone, windows)) / sum(w[3] for w in windows)


def weigh_kept(args, p, graph, todo):
    """The blobs kept only as bases (no model lists them) that rebasing the files of `todo` ({model: entries}) lets
    go: those whose every user (a blob coded against it) is one of these files, and whose room (with what only they
    need) is estimated to be worth more than what those files grow by against another model, or standalone, by
    RELEASE_GAIN of it at least. Says each decision; returns the set."""
    manifests = graph.manifests
    listed = {e["sha256"] for m in manifests.values() for e in m["files"]}
    files = {e["sha256"]: (n, e) for n, entries in todo.items() for e in entries}
    needed = graph.needed()
    users = {}
    for sha in needed:
        for b in graph.bases(sha):
            users.setdefault(b, set()).add(sha)
    release = set()
    for kept in sorted(needed - listed):
        coded = users.get(kept, set())
        if not coded or not coded <= files.keys():
            continue
        edges = {sha: [b for b in graph.bases(sha) if b != kept] if sha in coded else graph.bases(sha) for sha in needed}
        left, todo = set(), list(listed)
        while todo:
            sha = todo.pop()
            if sha not in left:
                left.add(sha)
                todo.extend(edges.get(sha, ()))
        room = blobs_size(p, needed - left)
        growth = 0
        for sha in coded:
            name, e = files[sha]
            others = {n: m for n, m in manifests.items() if n != name}
            share = file_share(p, e, others, graph.above(sha) | {kept}, args.delta_codec)
            growth += share * e["size"] - p.blob_span(sha)[2]
        gain = room - growth
        what = f"{blob_name(manifests, {s: blob_header(p, s) for s in coded}, kept)}, kept as a base of {', '.join(sorted({files[s][0] for s in coded}))}"
        if gain > room * RELEASE_GAIN:
            release.add(kept)
            say(f"letting go of {what}: storing what is coded against it otherwise is estimated to save {human(gain)}",
                event="release", sha256=kept, gain=round(gain))
        else:
            say(f"keeping {what}: it saves more than it takes ({human(room)})", event="keep", sha256=kept)
    return release


def rebase_model(args, p, graph, name, base, release=frozenset(), entries=None):
    """Store the files `entries` of one model (default: all of them) again as pack would now (re-choosing their
    bases, or with `base`), and keep the choice in its manifest: the model's, or with --file the files'. A file coded
    against a blob in `release` (weigh_kept) is stored again however it comes out, never against that blob. Returns
    its stored size before and after."""
    manifest = graph.manifests[name]
    chosen_paths = {e["path"] for e in (manifest["files"] if entries is None else entries)}
    forced = bool(args.base or args.no_base)
    others = {n: m for n, m in graph.manifests.items() if n != name}
    chunk_size = args.chunk_mib << 20
    how = f"delta base: {base}" if base else "no delta base" if args.no_base else "delta base chosen per file"
    named = [e for e in manifest["files"] if e["path"] in chosen_paths] if args.files else manifest["files"]
    what = (f"{named[0]['path']} of {name}" if len(named) == 1 else f"{len(named)} files of {name}") if args.files else name
    say(f"rebasing {what}  ({how}{', dry run' if args.dry_run else ''})")
    own = list(dict.fromkeys(e["sha256"] for e in named))  # what is said to be stored: the files named, or the model
    before = blobs_size(p, own)
    tmp, plain, seen = None, 0, set()
    try:
        for e in manifest["files"]:
            sha = e["sha256"]
            if e["path"] not in chosen_paths:
                if e.get("tensors") and e.get("base_choice", "auto") != "auto" and not args.files:
                    say(f"  {e['path']:<34} unchanged: its base was set by hand (name it with --file to rebase it anyway)")
                continue
            if sha in seen:
                continue
            seen.add(sha)
            if not e.get("tensors"):
                plain += 1  # no base can hold it: nothing to re-choose
                continue
            now_bases = sorted(b["sha256"] for b in blob_header(p, sha)["bases"])
            above = graph.above(sha)  # using any of these as a base would make a cycle
            letting_go = bool(release & set(now_bases))
            PROGRESS.begin(f"rebasing {name}", None, e["path"])
            if forced:
                chosen = base
            else:
                read = lambda start, length, sha=sha: read_range(p, sha, start, length)  # noqa: E731
                chosen = pick_base(args, p, e, e["format"], e["tensors"], read, others, above)
            files = [f for f in others[chosen]["files"] if f["sha256"] not in above] if chosen else []
            used = delta_layout(e["tensors"], e["format"], e["size"], chunk_size, files, args.delta_codec)[1]
            if chosen and not used and any(f["sha256"] in above for f in others[chosen]["files"]):
                say(f"  {e['path']:<34} unchanged: {chosen} needs this file (directly or through others), so it "
                    "cannot be its base")
                continue
            if sorted(f["sha256"] for f in used) == now_bases:
                say(f"  {e['path']:<34} unchanged ({'base is still ' + chosen if used else 'still no base'})")
                continue
            if graph.height(sha) + 1 + max((graph.depth(f["sha256"]) for f in used), default=-1) > MAX_REFERENCE_DEPTH:
                say(f"  {e['path']:<34} unchanged: against {chosen} its chain of bases would be too long")
                continue
            if args.dry_run:
                say(f"  {e['path']:<34} would be re-packed {'against ' + chosen if used else 'without a base'}")
                continue
            old = p.blob_span(sha)[2]
            reads = sum(f["size"] for f in used) + sum(b["size"] for b in blob_header(p, sha)["bases"])
            tmp = tmp or tempfile.mkdtemp(prefix="oc-rebase-", dir=os.environ.get("OC_TMP") or None)
            # OC_TMP holds the restored file plus the base files its old or new delta reads; the new blob is at most
            # about as big as the file
            problem = space_problem({tmp: e["size"] + reads, p.root: e["size"] + (1 << 20)})
            if problem:
                fail("no_room", f"  not enough room to rebase {e['path']}: {problem} (OC_TMP picks the temporary folder)")
            t0 = time.perf_counter()
            PROGRESS.begin(f"rebasing {name}", 3 * e["size"], e["path"])
            local = local_parts(e["path"])[-1]  # keeps the extension file_regions looks at
            try:
                problem = restore_entry(p, e, SafeTree(tmp), local, replace=False)
                if problem:
                    fail(problem[0], f"  cannot restore {e['path']}: {problem[1]}")
                src = os.path.join(tmp, local)
                regions = file_regions(src, e["size"], chunk_size)[2]
                info = pack_blob(args, p, src, e, e["format"], e["tensors"], regions, chosen if used else None, others,
                                 above, replace=True, keep=None if forced or letting_go else lambda size, old=old: size < old)
            except NotSmaller:
                say(f"  {e['path']:<34} not smaller than it is now ({human(old)}): kept that")
                continue
            finally:
                SafeTree(tmp).remove(local)
            graph.changed(sha, [b["sha256"] for b in info["bases"]])
            print_entry(e, info, t0)
            say(f"    replaces its blob of {human(old)}")
        if not args.dry_run:  # the choice is kept, so that --all respects it: the files' with --file, else the model's
            choice = base_choice(args)
            if args.files:
                new = {**manifest, "files": [with_choice(e, choice) if e["path"] in chosen_paths else e for e in manifest["files"]]}
            else:  # a file's own choice stays, unless one is forced on them all
                new = {**manifest, "base_choice": choice,
                       "files": [with_choice(e, "auto") for e in manifest["files"]] if choice != "auto" else manifest["files"]}
            if new != manifest:
                write_manifest(p, new)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if plain:
        say(f"  {plain} file(s) without tensors unchanged")
    after = blobs_size(p, own)
    if args.dry_run:
        say(f"  {'stored' if not args.files else what + ': stored'} {human(before)} (dry run: nothing changed)")
    else:
        change = f"{(after - before) / max(before, 1):+.1%}" if after != before else "unchanged"
        say(f"  {'stored' if not args.files else what + ': stored'} {human(before)} -> {human(after)}  ({change})",
            event="model", name=name, files=[e["path"] for e in named] if args.files else None, before=before, after=after)
    return before, after


def cmd_rebase(args):
    p = folder_for_change(args.pack, exclusive=not args.dry_run)
    manifests = all_manifests(p)
    if args.all == bool(args.models):
        sys.exit("give the models to rebase, or --all for every model whose bases were chosen automatically")
    if args.all and args.base:
        sys.exit("--base needs the models named, not --all")
    if args.files and (args.all or len(args.models) != 1):
        sys.exit("--file rebases files of one model: name exactly one")
    names = list(dict.fromkeys(model_named(manifests, n, p) for n in args.models)) or sorted(manifests)
    if args.files:
        unknown = sorted(set(args.files) - {e["path"] for e in manifests[names[0]]["files"]})
        if unknown:
            sys.exit(f"{names[0]} has no file {unknown[0]!r} (paths as the pack lists them, with \"/\")")
    base = model_named(manifests, args.base, p) if args.base else None
    if base in names:
        sys.exit(f"--base {base}: a model is never rebased against itself")
    graph = Graph(p, manifests)
    try:
        order = sorted(names, key=lambda n: (graph.model_depth(n), n))  # bases before the models that use them
    except (OSError, ValueError) as e:
        sys.exit(f"cannot tell what the models of {p.root} need ({e}): run verify")
    before = after = 0
    pack_before = blobs_size(p, p.blob_ids())
    skipped = {n for n in order if args.all and not args.no_base and manifests[n].get("base_choice", "auto") != "auto"}
    for name in sorted(skipped):
        option = "--no-base" if manifests[name]["base_choice"] == "none" else "--base"
        say(f"skipping {name}: its bases were set with {option} (name it to rebase it anyway)")
    forced = bool(base or args.no_base)
    todo = {n: [e for e in manifests[n]["files"]  # named files; else all but those set by hand, unless forced
                if (e["path"] in set(args.files) if args.files else forced or e.get("base_choice", "auto") == "auto")]
            for n in order if n not in skipped}
    try:
        release = set() if forced else weigh_kept(args, p, graph, todo)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot weigh the bases kept for other models ({e}): run verify")
    try:
        for name in order:
            if name in skipped:
                continue
            b, a = rebase_model(args, p, graph, name, base, release, todo[name])
            before, after = before + b, after + a
            graph.manifests = manifests = all_manifests(p)  # its base_choice may have changed
    finally:
        if not args.dry_run:
            sweep(p)  # blobs that were bases before and are not any more
            touch_marker(p)
        PROGRESS.end()
    if len(order) > 1:
        say(f"total stored {human(before)}" + (" (dry run: nothing changed)" if args.dry_run else f" -> {human(after)}"),
            event="models", before=before, after=after)
    if not args.dry_run:  # what counts: with the bases let go, and blobs trimmed or no longer needed
        pack_after = blobs_size(p, p.blob_ids())
        change = f"{(pack_after - pack_before) / max(pack_before, 1):+.1%}" if pack_after != pack_before else "unchanged"
        say(f"the pack: {human(pack_before)} -> {human(pack_after)}  ({change})", event="summary", before=pack_before,
            after=pack_after)


# ----------------------------------------------------------------------------------------------- export


def kit_files(p):
    """(files, problems) of a pack's _kit for export: every file in it ("/" paths from the pack, sorted); problems:
    links (never followed) and anything that is not a regular file."""
    if isinstance(p, FilePack):
        return sorted(n for n in p.names if n.startswith(KIT + "/") and not n.endswith("/")), []
    found, problems, top = [], [], p.full(KIT)
    if not os.path.isdir(top) or os.path.islink(top):
        return [], ([f"{KIT} is a link"] if os.path.islink(top) else [])
    is_junction = getattr(os.path, "isjunction", lambda path: False)
    for dirpath, dirnames, names in os.walk(top):
        rel = lambda n: os.path.relpath(os.path.join(dirpath, n), p.root).replace(os.sep, "/")  # noqa: E731
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full) or is_junction(full):
                problems.append(f"{rel(d)}/ is a link to a folder")
            else:
                keep.append(d)
        dirnames[:] = keep
        for n in sorted(names):
            mode = os.lstat(os.path.join(dirpath, n)).st_mode
            if stat.S_ISREG(mode):
                found.append(rel(n))
            else:
                problems.append(f"{rel(n)} is {'a link' if stat.S_ISLNK(mode) else 'not a regular file'}")
    return sorted(found), problems


def copy_blob(src, dst, size):
    """Copy a blob of `size` bytes from src to dst (file objects), checking that its last 32 bytes are the SHA-256 of
    the rest; returns the SHA-256 of all of it (what the copy must read back as). ValueError if it is damaged."""
    inner, whole, left = hashlib.sha256(), hashlib.sha256(), size - TRAILER
    while left > 0:
        block = src.read(min(left, 16 << 20))
        if not block:
            raise ValueError("cut short")
        inner.update(block)
        whole.update(block)
        dst.write(block)
        left -= len(block)
        PROGRESS.add(len(block))
    tail = src.read(TRAILER)
    if tail != inner.digest():
        raise ValueError("damaged (its SHA-256 does not match)")
    whole.update(tail)
    dst.write(tail)
    return whole.hexdigest()


def readback_problems(path, digests):
    """What differs between the pack file at path, read back from disk, and the members put in it ({name: sha256})."""
    with zipfile.ZipFile(path) as z:
        names, problems = set(z.namelist()), []
        problems += [f"{n}: in the pack file but not put in" for n in sorted(names - set(digests))]
        problems += [f"{n}: missing from the pack file" for n in sorted(set(digests) - names)]
        for name, digest in sorted(digests.items()):
            if name in names:
                try:
                    with z.open(name) as f:
                        same = sha256_stream(f) == digest
                except (OSError, ValueError, zipfile.BadZipFile) as e:
                    problems.append(f"{name}: cannot be read back ({e})")
                    continue
                if not same:
                    problems.append(f"{name}: differs from what was put in")
    return problems


def unlock_archive(root):
    """Release the pack folder's lock and delete its file (export --delete: the pack folder is going)."""
    while _HELD_LOCK:
        os.close(_HELD_LOCK.pop())
    with contextlib.suppress(OSError):
        os.remove(os.path.join(root, LOCK_NAME))


def cmd_export(args):
    """Put models of a pack (default: all) into a new pack file, with every blob they need and the _kit: the blobs
    stored as they are, the rest Deflated where that helps. From a pack file, this makes a smaller one. --verify
    reads it back (every member compared with what was put in, every model verified from it); --delete then removes
    those models from the pack folder (only the blobs no remaining model needs go), verifying first unless
    --no-verify."""
    try:
        p = open_pack(args.pack)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot open {args.pack}: {e}")
    out = os.path.abspath(args.oczip)
    verify = args.delete if args.verify is None else args.verify
    if not is_oczip(out):
        sys.exit(f"{args.oczip}: the name of a pack file must end with {OCZIP}")
    if isinstance(p, FilePack):
        if args.delete:
            sys.exit(f"--delete: {args.pack} is a pack file, which is read-only")
        if os.path.normcase(p.root) == os.path.normcase(out):
            sys.exit(f"{args.oczip} is the pack file exported from: write another one")
    elif holds(p.root, out):
        sys.exit(f"{args.oczip} would be inside the pack folder: write it somewhere else")
    problem = place_problem(os.path.dirname(out))
    if problem:
        sys.exit(f"a pack file is not written into a pack folder: {problem}")
    if os.path.lexists(out) and not args.overwrite:
        fail("exists", f"{args.oczip} exists already: choose another name, or add --overwrite to replace it")
    if isinstance(p, FolderPack):
        lock_archive(p.root, exclusive=args.delete, skip=args.no_lock and not args.delete)
    manifests = all_manifests(p)
    names = list(dict.fromkeys(model_named(manifests, n, p) for n in args.models)) or sorted(manifests)
    if not names:
        sys.exit(f"no models in {p.root}: nothing to export")
    try:
        blobs = sorted(Graph(p, manifests).needed(names))
        sizes = {sha: p.blob_span(sha)[2] for sha in blobs}
    except (OSError, ValueError) as e:
        sys.exit(f"cannot tell what {', '.join(names)} need ({e}): nothing written; run verify")
    kit, problems = kit_files(p)
    if problems:
        sys.exit(f"cannot export {p.root} as it is:\n  " + "\n  ".join(problems) + "\nmove or delete these first")
    kit_sizes = {rel: p.zip.getinfo(rel).file_size if isinstance(p, FilePack) else os.path.getsize(p.full(rel))
                 for rel in kit}
    total = sum(sizes.values()) + sum(kit_sizes.values())
    os.makedirs(os.path.dirname(out), exist_ok=True)
    problem = space_problem({os.path.dirname(out): total + (len(blobs) + len(kit) << 10) + (16 << 20)})
    if problem:
        fail("no_room", f"not enough room for {args.oczip}: {problem}")
    which = "every model" if len(names) == len(manifests) else ", ".join(names)
    say(f"exporting {which} of {p.root} ({len(blobs)} blobs, {human(sum(sizes.values()))}) into {out}")
    PROGRESS.begin("exporting", total)
    digests, t0 = {}, time.perf_counter()
    stamp = time.localtime()[:6]

    def member(rel, method):
        info = zipfile.ZipInfo(rel, stamp)
        info.compress_type = method
        return info

    with SafeTree(os.path.dirname(out)).placing(os.path.basename(out), replace=args.overwrite) as f, \
            zipfile.ZipFile(f, "w", allowZip64=True) as z:
        for rel in [MARKER] + [manifest_rel(n) for n in names]:
            data = p.read(rel)
            z.writestr(member(rel, zipfile.ZIP_DEFLATED), data)
            digests[rel] = hashlib.sha256(data).hexdigest()
        for sha in blobs:
            rel, (path, start, _) = blob_rel(sha), p.blob_span(sha)
            PROGRESS.at(rel)
            with open(path, "rb") as src, z.open(member(rel, zipfile.ZIP_STORED), "w", force_zip64=True) as dst:
                src.seek(start)
                try:
                    digests[rel] = copy_blob(src, dst, sizes[sha])
                except ValueError as e:
                    sys.exit(f"{rel} is {e}: nothing written; run verify")
        for rel in kit:
            PROGRESS.at(rel)
            if isinstance(p, FilePack):
                info = zipfile.ZipInfo(rel, p.zip.getinfo(rel).date_time)
                opened = lambda rel=rel: p.zip.open(rel)  # noqa: E731
            else:
                info = zipfile.ZipInfo.from_file(p.full(rel), rel, strict_timestamps=False)
                opened = lambda rel=rel: open(p.full(rel), "rb")  # noqa: E731
            with opened() as src:
                info.compress_type = zip_method(rel, kit_sizes[rel], src.read(1 << 20))
            h, n = hashlib.sha256(), 0
            with opened() as src, z.open(info, "w", force_zip64=True) as dst:
                while block := src.read(16 << 20):
                    h.update(block)
                    dst.write(block)
                    n += len(block)
                    PROGRESS.add(len(block))
            if n != kit_sizes[rel]:
                sys.exit(f"{rel} changed while it was exported: nothing written")
            digests[rel] = h.hexdigest()
    PROGRESS.end()
    say(f"wrote {out}: {human(os.path.getsize(out))}  ({time.perf_counter() - t0:.0f}s)", event="written", path=out,
        size=os.path.getsize(out))
    if verify:
        say(f"verifying {out}")
        problems = readback_problems(out, digests)
        for problem in problems:
            say(f"  FAIL  {problem}", event="fail", problem=problem)
        new = FilePack(out)
        try:
            failures, checked, _ = verify_models(new, all_manifests(new, []), names)
        finally:
            new.close()
        if problems or failures:
            fail("damaged", f"{len(problems) + failures} FAILURE(S) in {out}" + (": nothing removed" if args.delete else ""))
        say(f"VERIFIED: all {len(digests)} members read back identical; {checked} model files restored exactly")
    if args.delete:
        tree = SafeTree(p.root)
        for name in names:
            tree.remove(manifest_rel(name))
        result = sweep(p)
        freed = f": {human(result[1])} freed" if result else ""
        if len(names) == len(manifests):  # the whole pack is in the file now: the folder goes
            for rel in kit + [MARKER]:
                tree.remove(rel)
                tree.prune(rel)
            for folder in (MODELS, BLOBS):
                with contextlib.suppress(OSError):
                    os.rmdir(p.full(folder))
            unlock_archive(p.root)
            with contextlib.suppress(OSError):
                os.rmdir(p.root)  # only if nothing else is left in it
            left = [os.path.join(d, n) for d, _, found in os.walk(p.root) for n in found]
            say(f"removed the pack folder {p.root}{freed}" if not os.path.exists(p.root) else
                f"emptied the pack folder {p.root}{freed}; left in it, not part of the pack: {len(left)} file(s)"
                + (f", e.g. {left[0]}" if left else ""))
        else:
            touch_marker(p)
            say(f"removed {', '.join(names)} from {p.root}{freed}")


STORED_SUFFIXES = (".ocz", OCZIP, ".zip", ".whl", ".gz", ".tgz", ".xz", ".zst", ".bz2", ".7z", ".rar", ".exe", ".msi",
                   ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp3", ".mp4", ".mkv", ".par2")  # compressed already

def zip_method(rel, size, sample):
    """ZIP_STORED for a file compressed already (by its name) or one Deflate barely shrinks (judged on `sample`, its
    first MiB), else ZIP_DEFLATED."""
    if not size or rel.lower().endswith(STORED_SUFFIXES):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED if len(zlib.compress(sample, 6)) < 0.9 * len(sample) else zipfile.ZIP_STORED


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


def build_of(path):
    """Which build a tool file is, "<date> <commit>" of the last commit that changed it (", edited" when the file
    differs from that commit): from the BUILD file of a portable copy (docs/build-*.md), else from git when the file sits in a
    checkout; None if neither tells. Git is asked only what reads its objects (log, rev-parse): nothing that runs hooks."""
    folder, name = os.path.split(os.path.abspath(path))
    try:
        with open(os.path.join(folder, "BUILD"), encoding="utf-8") as f:
            return json.load(f).get(name)
    except (OSError, ValueError, AttributeError):
        pass

    def git(*argv):
        return subprocess.run(["git", "-C", folder, *argv], capture_output=True, text=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()

    try:
        stamp, committed = git("log", "-1", "--format=%cs %h", "--", name), git("rev-parse", f"HEAD:./{name}")
        with open(path, "rb") as f:
            data = f.read().replace(b"\r\n", b"\n")  # as git keeps it
    except (OSError, subprocess.SubprocessError):
        return None
    if not stamp:
        return None
    return stamp + ("" if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == committed else ", edited")


def version_text(name, version, path):
    build = build_of(path)
    return f"{name} {version}" + (f" ({build})" if build else "")


TOOL = []  # what goes into the manifests' "tool": worked out once, when first written


def tool_version():
    if not TOOL:
        TOOL.append(version_text("llm_archive", VERSION, __file__))
    return TOOL[0]


class ShowVersion(argparse.Action):
    """--version: the version and build of this tool, and the format it writes."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, *_):
        print(f"{version_text('OverCompressing CLI', VERSION, __file__)}, format {FORMAT_VERSION}")
        parser.exit()


def read_list(path):
    """The names in a list file (--models-from, --files-from): one per line, as written; UTF-8, a BOM, CRLF and blank
    lines do not matter. A list that names nothing stops the program (it would otherwise mean "all")."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            names = [line for line in (raw.removesuffix("\r") for raw in f.read().split("\n")) if line]
    except (OSError, UnicodeDecodeError) as e:
        sys.exit(f"cannot read the list {path} ({e})")
    if not names:
        sys.exit(f"the list {path} names nothing")
    return names


def read_lists(args):
    """Add the names of --models-from and --files-from lists to the models and files named on the command line."""
    for path in getattr(args, "models_from", ()):
        args.models = [*args.models, *read_list(path)]
    for path in getattr(args, "files_from", ()):
        args.files = [*args.files, *read_list(path)]


def main():
    global JSON_OUT
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="print every result line (and progress line) as a JSON object {\"event\", \"text\", ...}")
    common.add_argument("--progress", action=argparse.BooleanOptionalAction, default=None,
                        help="report on stderr, about once a second, how far the current step is (default: when "
                             "stderr is a terminal)")
    no_lock = dict(action="store_true", help="do not lock the pack folder (only when it cannot be locked and nothing "
                                             "can change it while this runs)")
    models_from = dict(action="append", default=[], metavar="FILE", dest="models_from",
                       help="also the models named in FILE, one per line (UTF-8; repeatable)")
    files_from = dict(action="append", default=[], metavar="FILE", dest="files_from",
                      help="also the files listed in FILE, one path per line (UTF-8; repeatable), as with --file")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action=ShowVersion, help="show the version and build of this tool, and exit")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pack", parents=[common], help="compress the files of folders into a pack folder, or into a new "
                                                      "pack file, and verify them")
    s.add_argument("paths", nargs="+", metavar="path", help=f"the folder(s) to pack (one model each), then the pack "
                                                           f"folder to pack into or a new pack file (<name>{OCZIP}) to make")
    s.add_argument("--name", help="name of the model in the pack, e.g. Qwen/Qwen2.5-7B (default: the folder's name)")
    there = s.add_mutually_exclusive_group()
    there.add_argument("--replace", action="store_true",
                       help="replace a model of that name in the pack folder with these files (without it, a name the "
                            "pack folder holds already is refused)")
    there.add_argument("--add", action="store_true",
                       help="add these files to the model of that name in the pack folder: files of the same path are "
                            "replaced, the others kept")
    s.add_argument("--revision", help="free-text note stored in the manifest, e.g. the upstream commit")
    s.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                   help="skip files whose path or name matches (repeatable); .git/, .cache/, __MACOSX/, Thumbs.db, "
                        "desktop.ini and .DS_Store are always skipped")
    s.add_argument("--file", action="append", default=[], metavar="PATH", dest="files",
                   help="pack only this file of the folder, a path relative to it (repeatable): the model then holds "
                        "just the files named")
    s.add_argument("--files-from", **files_from)
    base = s.add_mutually_exclusive_group()
    base.add_argument("--base", metavar="MODEL",
                      help="store tensors as deltas against this model (of the pack, or packed now before the others) "
                           "(default: each file picks the model that compresses it best, by sampling)")
    base.add_argument("--no-base", action="store_true", help="store every file without a delta base")
    add_codec_options(s)
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("list", parents=[common], help="show the models of a pack as a tree of what needs what, or "
                                                      "the files of one model")
    s.add_argument("pack", help=f"a pack folder or a pack file ({OCZIP})")
    s.add_argument("model", nargs="?", help="show this model's files, how each is stored and what it is coded against")
    s.add_argument("--no-lock", **no_lock)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("verify", parents=[common], help="decompress everything in memory and check sha256")
    s.add_argument("pack", help=f"a pack folder or a pack file ({OCZIP})")
    s.add_argument("models", nargs="*", metavar="model", help="only these models (default: all of them)")
    s.add_argument("--file", action="append", default=[], metavar="PATH", dest="files",
                   help="only this file of the one model named, its path as the pack lists it (repeatable)")
    s.add_argument("--models-from", **models_from)
    s.add_argument("--files-from", **files_from)
    s.add_argument("--no-lock", **no_lock)
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("restore", parents=[common], help="write a model back out, byte-identical to the original")
    s.add_argument("pack", help=f"a pack folder or a pack file ({OCZIP})")
    s.add_argument("model", help="the model's name (Org/Name)")
    s.add_argument("dest_dir")
    s.add_argument("--overwrite", action="store_true",
                   help="replace files in dest_dir that have other contents (files already right are always kept)")
    s.add_argument("--file", action="append", default=[], metavar="PATH", dest="files",
                   help="restore only this file of the model, its path as the pack lists it (repeatable)")
    s.add_argument("--files-from", **files_from)
    s.add_argument("--flat", action="store_true",
                   help="put every file straight into dest_dir, without the model's folders (two files of one name: "
                        "the later one gets \" (1)\")")
    s.add_argument("--no-lock", **no_lock)
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("remove", parents=[common], help="remove models (or files of a model) from a pack folder: only "
                                                        "what no remaining model needs is deleted")
    s.add_argument("pack", metavar="pack_folder")
    s.add_argument("models", nargs="*", metavar="model", help="the models to remove")
    s.add_argument("--file", action="append", default=[], metavar="PATH", dest="files",
                   help="remove only this file of the model, its path as the pack lists it (repeatable)")
    s.add_argument("--models-from", **models_from)
    s.add_argument("--files-from", **files_from)
    s.add_argument("--dry-run", action="store_true", help="show what would be freed and kept; change nothing")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("rename", parents=[common], help="give a model of a pack folder another name, or a file of a "
                                                        "model another path")
    s.add_argument("pack", metavar="pack_folder")
    s.add_argument("model")
    s.add_argument("new_name", help="the model's new name; with --file, the file's new path in the model")
    s.add_argument("--file", metavar="PATH", help="rename this file of the model (its path as the pack lists it) instead")
    s.set_defaults(func=cmd_rename)

    s = sub.add_parser("rebase", parents=[common], help="re-choose the delta bases of packed models (restores only "
                                                        "what it re-packs)")
    s.add_argument("pack", metavar="pack_folder")
    s.add_argument("models", nargs="*", metavar="model", help="the models to rebase")
    s.add_argument("--models-from", **models_from)
    s.add_argument("--file", action="append", default=[], metavar="PATH", dest="files",
                   help="only this file of the one model named, its path as the pack lists it (repeatable); a choice "
                        "made with --base or --no-base is then this file's, and --all leaves it as it is")
    s.add_argument("--files-from", **files_from)
    s.add_argument("--all", action="store_true",
                   help="every model whose bases were chosen automatically, bases before the models that use them")
    base = s.add_mutually_exclusive_group()
    base.add_argument("--base", metavar="MODEL", help="re-pack every file against this model, even if it gets larger")
    base.add_argument("--no-base", action="store_true", help="re-pack every file without a delta base, even if it gets larger")
    s.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    add_codec_options(s)
    s.set_defaults(func=cmd_rebase)

    s = sub.add_parser("export", parents=[common], help=f"put models of a pack, with every blob they need, into a new "
                                                        f"pack file ({OCZIP})")
    s.add_argument("pack", help=f"a pack folder, or a pack file ({OCZIP}) to take some of its models from")
    s.add_argument("oczip", metavar="pack_file", help=f"the pack file to write, its name ending in {OCZIP} (outside the "
                                                      "pack folder)")
    s.add_argument("models", nargs="*", metavar="model", help="only these models (default: all of them)")
    s.add_argument("--models-from", **models_from)
    check = s.add_mutually_exclusive_group()
    check.add_argument("--verify", action="store_true", default=None,
                       help="then read it back: every member compared with what was put in, every model verified "
                            "(default: only with --delete)")
    check.add_argument("--no-verify", dest="verify", action="store_false", default=None,
                       help="with --delete: remove without verifying first")
    s.add_argument("--delete", action="store_true",
                   help="then remove those models from the pack folder (verified first unless --no-verify); "
                        "exporting all of them removes the pack folder")
    s.add_argument("--overwrite", action="store_true", help="replace a file of that name")
    s.add_argument("--no-lock", **no_lock)
    s.set_defaults(func=cmd_export)

    args = p.parse_args()
    JSON_OUT = args.json
    for stream in (sys.stdout, sys.stderr):  # a name the console's code page lacks must not stop the program
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="backslashreplace")
    if args.progress or (args.progress is None and sys.stderr.isatty()):
        PROGRESS.start()
    try:
        try:
            workers = ans16_workers()  # checks OC_WORKERS before any command starts
            if getattr(args, "workers", 0) is None:
                args.workers = workers
            read_lists(args)
            args.func(args)
        except OSError as e:  # e.g. a full disk: reported plainly; what a command had begun to write is removed already
            fail("no_room" if e.errno == errno.ENOSPC else "other", f"error: {e}")
        except KeyboardInterrupt:  # Ctrl+C, or the window closing stdin: what had begun is removed already, as above
            fail("cancelled", "cancelled")
    except SystemExit as e:
        PROGRESS.end()
        if JSON_OUT and isinstance(e.code, str):
            print(json.dumps({"event": "error", "code": getattr(e, "kind", "other"), "text": e.code}), flush=True)
            raise SystemExit(1) from None
        raise
    finally:
        PROGRESS.end()


if __name__ == "__main__":
    main()
