"""Superseded by the ans16 coder in llm_archive.py (integer tables, no constriction). Experimental delta coder: 16-bit SUB residual (target - base mod 2^16) per BF16 weight, entropy-coded with rANS
using one frequency table per (tensor, base-weight exponent) context. The decoder derives the grouping from the base
alone, so only the tables and the ANS streams are stored. Measures size and verifies a bit-exact restore.

Usage: python -I ans16_delta.py <base.safetensors> <target.safetensors>
"""

import hashlib
import json
import struct
import sys
import time

import constriction
import numpy as np
import zstandard

MIN_CONTEXT = 1 << 16  # contexts with fewer weights than this share one "rare" table per tensor
RARE = 256


def read_safetensors(path):
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<Q", data[:8])[0]
    header = json.loads(data[8 : 8 + n])
    return data, 8 + n, {k: v for k, v in header.items() if k != "__metadata__"}


def contexts_and_order(b):
    """Context id per weight (base exponent, rare ones merged) and the stable order that groups them."""
    ctx = ((b >> 7) & 0xFF).astype(np.int32)
    counts = np.bincount(ctx, minlength=256)
    ctx = np.where(counts[ctx] < MIN_CONTEXT, RARE, ctx)
    order = np.argsort(ctx, kind="stable")
    ids, starts = np.unique(ctx[order], return_index=True)
    bounds = list(zip(ids, starts, list(starts[1:]) + [len(order)]))
    return order, bounds


def model_from_counts(counts):
    return constriction.stream.model.Categorical(counts / counts.sum(), perfect=False)


def encode(base_t, target_t):
    """Return (list of ANS streams as bytes, table bytes) for one tensor pair."""
    r = (target_t - base_t).astype(np.uint16)
    order, bounds = contexts_and_order(base_t)
    r_sorted = r[order]
    streams, tables = [], bytearray()
    for _, s, e in bounds:
        uniq, inv, counts = np.unique(r_sorted[s:e], return_inverse=True, return_counts=True)
        coder = constriction.stream.stack.AnsCoder()
        coder.encode_reverse(inv.astype(np.int32), model_from_counts(counts.astype(np.float64)))
        streams.append(coder.get_compressed().tobytes())
        tables += struct.pack("<I", len(uniq)) + np.diff(uniq, prepend=np.uint16(0)).astype(np.uint16).tobytes()
        tables += counts.astype(np.uint32).tobytes()
    return streams, bytes(tables)


def decode(base_t, streams, tables, table_pos):
    order, bounds = contexts_and_order(base_t)
    r_sorted = np.empty(base_t.size, np.uint16)
    for (_, s, e), stream in zip(bounds, streams):
        (k,) = struct.unpack_from("<I", tables, table_pos)
        table_pos += 4
        uniq = np.cumsum(np.frombuffer(tables, np.uint16, k, table_pos), dtype=np.uint16)
        table_pos += 2 * k
        counts = np.frombuffer(tables, np.uint32, k, table_pos).astype(np.float64)
        table_pos += 4 * k
        coder = constriction.stream.stack.AnsCoder(np.frombuffer(stream, np.uint32).copy())
        r_sorted[s:e] = uniq[coder.decode(model_from_counts(counts), e - s)]
    r = np.empty_like(r_sorted)
    r[order] = r_sorted
    return (base_t + r).astype(np.uint16), table_pos


def main():
    base, base_start, base_hdr = read_safetensors(sys.argv[1])
    target, target_start, target_hdr = read_safetensors(sys.argv[2])
    assert base_start == target_start and base[:base_start] == target[:target_start], "headers must match"
    names = sorted(target_hdr, key=lambda k: target_hdr[k]["data_offsets"][0])
    assert all(target_hdr[k]["dtype"] == "BF16" for k in names), "experiment handles all-BF16 files only"

    def view(data, name):
        s, e = target_hdr[name]["data_offsets"]
        return np.frombuffer(data, np.uint16, (e - s) // 2, base_start + s)

    t0 = time.perf_counter()
    all_streams, all_tables = [], bytearray()
    for name in names:
        streams, tables = encode(view(base, name), view(target, name))
        all_streams.append(streams)
        all_tables += tables
    t_enc = time.perf_counter() - t0
    tables_z = zstandard.ZstdCompressor(level=19).compress(bytes(all_tables))
    stream_bytes = sum(len(s) for streams in all_streams for s in streams)
    framing = 8 * sum(len(streams) for streams in all_streams)  # a u64 length per stream
    total = stream_bytes + len(tables_z) + framing
    print(f"ANS streams {stream_bytes:>13,} B   tables {len(tables_z):>11,} B (zstd)   framing {framing:,} B")
    print(f"ans16 SUB delta total {total:>13,} B = {total / len(target) * 100:.2f}% of target   (encode {t_enc:.0f}s)")

    t0 = time.perf_counter()
    out = bytearray(target[:target_start])  # header is identical to base
    tables = zstandard.ZstdDecompressor().decompress(tables_z)
    pos = 0
    for name, streams in zip(names, all_streams):
        restored, pos = decode(view(base, name), streams, tables, pos)
        out += restored.tobytes()
    t_dec = time.perf_counter() - t0
    ok = hashlib.sha256(out).digest() == hashlib.sha256(target).digest()
    print(f"restore: {'BIT-EXACT' if ok else 'MISMATCH'}   (decode {t_dec:.0f}s)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
