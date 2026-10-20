"""Empirical entropy of different delta representations between two BF16 safetensors files with identical layout.

Usage: python -I delta_entropy.py <base.safetensors> <target.safetensors>
"""

import json
import struct
import sys

import numpy as np


def load_bf16_tensors(path):
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<Q", data[:8])[0]
    header = json.loads(data[8 : 8 + n])
    out = {}
    for name, info in sorted(header.items()):
        if name == "__metadata__" or info["dtype"] != "BF16":
            continue
        s, e = info["data_offsets"]
        out[name] = np.frombuffer(data, np.uint16, (e - s) // 2, 8 + n + s)
    return out


def ordered(u):
    """Bijective uint16 map that preserves float order (-0 sorts just below +0)."""
    return np.where(u & 0x8000, u ^ 0xFFFF, u | 0x8000).astype(np.uint16)


def entropy_bits_total(symbols, contexts=None, num_contexts=1):
    """Total bits of an ideal order-0 coder with one table per context, plus distinct-symbol counts for table cost."""
    keys = symbols.astype(np.int64) if contexts is None else contexts.astype(np.int64) * 65536 + symbols
    counts = np.bincount(keys, minlength=num_contexts * 65536).reshape(num_contexts, 65536).astype(np.float64)
    totals = counts.sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(counts > 0, counts / totals, 1.0)
    bits = float(-(counts * np.log2(p)).sum())
    return bits, int((counts > 0).sum())


def main():
    base, target = load_bf16_tensors(sys.argv[1]), load_bf16_tensors(sys.argv[2])
    assert base.keys() == target.keys()
    total_params = sum(t.size for t in target.values())

    variants = {
        "target alone": lambda b, t: t,
        "XOR": lambda b, t: b ^ t,
        "SUB (raw bits)": lambda b, t: t - b,
        "ULP SUB (order-preserving)": lambda b, t: ordered(t) - ordered(b),
    }
    print(f"{total_params:,} BF16 params; bits/param and % of BF16 (16 bits). table cost ~ 3 bytes per distinct symbol per context")
    print(f"{'representation':<28}{'global':>16}{'per tensor':>16}{'tensor+base exp':>18}{'  table cost':>14}")
    for label, fn in variants.items():
        acc = {"global": [], "tensor": [0.0, 0], "tensor_exp": [0.0, 0]}
        for name in target:
            b, t = base[name], target[name]
            r = fn(b, t)
            acc["global"].append(r)
            bits, distinct = entropy_bits_total(r)
            acc["tensor"][0] += bits
            acc["tensor"][1] += distinct
            bits, distinct = entropy_bits_total(r, (b >> 7) & 0xFF, 256)
            acc["tensor_exp"][0] += bits
            acc["tensor_exp"][1] += distinct
        g_bits, g_distinct = entropy_bits_total(np.concatenate(acc["global"]))
        row = [g_bits / total_params, acc["tensor"][0] / total_params, acc["tensor_exp"][0] / total_params]
        cost = acc["tensor_exp"][1] * 24 / total_params  # bits/param for the richest model's tables
        print(
            f"{label:<28}"
            + "".join(f"{v:7.3f} ({v / 16 * 100:5.2f}%)" for v in row[:2])
            + f"  {row[2]:7.3f} ({row[2] / 16 * 100:5.2f}%)"
            + f"  +{cost:.3f}"
        )


if __name__ == "__main__":
    main()
