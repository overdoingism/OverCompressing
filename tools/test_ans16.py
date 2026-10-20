"""Round-trip tests for the ans16 coder in llm_archive.py.

Usage: python -I test_ans16.py [<base.safetensors> <target.safetensors>]
"""

import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import llm_archive as la  # noqa: E402

rng = np.random.default_rng(1234)
failures = 0


def bf16(values):
    return (np.asarray(values, np.float32).view(np.uint32) >> 16).astype(np.uint16)


def check(name, base, target, dtype="BF16", **kw):
    global failures
    payload = la.ans16_encode(base, target, dtype, **kw)
    out = la.ans16_decode(base, payload, dtype)
    ok = out.dtype == np.uint16 and np.array_equal(out, target)
    failures += not ok
    bits = len(payload) * 8 / max(base.size, 1)
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<44} n={base.size:>9,}  {bits:6.2f} bits/weight")
    return payload


print("synthetic cases")
for n in (1, 2, 3, 4095, 4096, 4097, 8193, 100_000):
    w = rng.standard_normal(n) * 0.02
    base = bf16(w)
    check(f"small random update, n={n}", base, bf16(w + rng.standard_normal(n) * 1e-4))
w = rng.standard_normal(300_000) * 0.02
base = bf16(w)
check("identical (all residuals 0)", base, base.copy())
check("uniform random 16-bit target", base, rng.integers(0, 65536, base.size, dtype=np.uint16))
check("sign flips everywhere", base, base ^ np.uint16(0x8000))
check("every context rare (min_context huge)", base, bf16(w * 1.01), min_context=10**9)
check("every context kept (min_context 1)", base, bf16(w * 1.01), min_context=1)
w16 = (rng.standard_normal(200_000) * 0.02).astype(np.float16)
check("F16 small update", w16.view(np.uint16), (w16 + np.float16(1e-3)).view(np.uint16), dtype="F16")

print("prefix decode (used to sample archived models)")
target = bf16(w + rng.standard_normal(w.size) * 1e-4)
payload = la.ans16_encode(base, target, "BF16")
for stop in (1, 73, 4096, 150_001, base.size - 1, base.size):
    out = la.ans16_decode(base, payload, "BF16", stop)
    ok = out.dtype == np.uint16 and np.array_equal(out, target[:stop])
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} first {stop:,} of {base.size:,} weights")

print("empty input")
for b, t in ((np.zeros(0, np.uint16), np.zeros(0, np.uint16)), (base[:3], base[:2])):
    try:
        la.ans16_encode(b, t, "BF16")
        failures += 1
        print(f"  FAIL sizes {b.size}/{t.size} accepted")
    except ValueError:
        print(f"  ok   sizes {b.size}/{t.size} rejected with ValueError")

print("corruption")
payload = bytearray(la.ans16_encode(base, bf16(w * 1.01), "BF16"))
for where in (len(payload) - 5, len(payload) // 2, la.ANS16_HEAD.size + 3):
    bad = bytearray(payload)
    bad[where] ^= 0x40
    try:
        out = la.ans16_decode(base, bytes(bad), "BF16")
        caught = not np.array_equal(out, bf16(w * 1.01))
        how = "output differs (CRC would catch it)" if caught else "UNDETECTED"
    except Exception as e:
        caught, how = True, f"raised {type(e).__name__}"
    failures += not caught
    print(f"  {'ok  ' if caught else 'FAIL'} flipped byte {where:>9,}: {how}")
try:
    la.ans16_decode(base[:-1], bytes(payload), "BF16")
    failures += 1
    print("  FAIL wrong-size base accepted")
except ValueError:
    print("  ok   wrong-size base rejected")

if len(sys.argv) == 3:
    print("real tensors")
    def tensors(path):
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            return 8 + n, json.loads(f.read(n))
    start, hdr = tensors(sys.argv[1])
    for name in ("model.embed_tokens.weight", "model.layers.0.self_attn.q_proj.weight", "model.norm.weight"):
        s, e = hdr[name]["data_offsets"]
        count = min((e - s) // 2, la.ANS16_BLOCK_ELEMS)
        b = np.fromfile(sys.argv[1], "<u2", count, offset=start + s)
        t = np.fromfile(sys.argv[2], "<u2", count, offset=start + s)
        t0 = time.perf_counter()
        check(name, b, t)
        print(f"       encode+decode {time.perf_counter() - t0:.1f}s")

print("ALL PASSED" if not failures else f"{failures} FAILURE(S)")
sys.exit(1 if failures else 0)
