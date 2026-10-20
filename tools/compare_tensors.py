"""Compare the tensors of two safetensors or GGUF files (e.g. a fine-tune against its base) with the same matching
rules llm_archive.py uses for deltas: which bytes line up (exactly, through a dtype conversion, or by leading rows
when the first dimension differs), why the rest do not, and how many matched weights are bit-identical.

Usage: python -I compare_tensors.py <base_file> <target_file>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import llm_archive as la  # noqa: E402


def main():
    base_path, target_path = sys.argv[1:3]
    _, base = la.file_tensors(base_path, os.path.getsize(base_path))
    fmt, target = la.file_tensors(target_path, os.path.getsize(target_path))
    match = la.match_against_base(target, [{"tensors": base}], "ans16")
    base_by_name = {t[0]: t for t in base}
    total = sum(t[3] - t[2] for t in target)
    matched = identical = converted = partial = 0
    problems = []
    for tensor in target:
        name, dtype, start, end = tensor[:4]
        m = match(tensor) if end > start else None
        if m is None:
            b = base_by_name.get(name)
            why = "not in base" if b is None else f"{dtype} {tensor[4]} vs base {b[1]} {b[4]}"
            problems.append((end - start, name, why))
            continue
        _, base_start, _, base_dtype, covered = m
        matched += covered
        converted += covered if base_dtype else 0
        if covered < end - start:
            partial += covered
            problems.append((end - start - covered, name, f"only the first {covered:,} of {end - start:,} bytes have rows in the base"))
        if dtype in la.ANS16_EXPONENT:
            pred = la.base_prediction(base_path, base_start, covered // 2, base_dtype, dtype)
            got = np.fromfile(target_path, "<u2", covered // 2, offset=start)
            identical += int(np.count_nonzero(pred == got)) * 2
        else:
            with open(base_path, "rb") as fb, open(target_path, "rb") as ft:
                fb.seek(base_start)
                ft.seek(start)
                identical += int(np.count_nonzero(np.frombuffer(fb.read(covered), np.uint8) == np.frombuffer(ft.read(covered), np.uint8)))
    print(f"{fmt}: {len(target)} tensors, {total / 2**20:,.1f} MiB; base has {len(base)} tensors")
    print(f"matched for delta: {matched / total * 100:.1f}% of bytes (via dtype conversion {converted / total * 100:.1f}%, "
          f"leading rows only {partial / total * 100:.1f}%); bit-identical among matched: {identical / max(matched, 1) * 100:.1f}%")
    for size, name, why in sorted(problems, reverse=True)[:12]:
        print(f"  unmatched {size / 2**20:8.1f} MiB  {name}: {why}")
    if len(problems) > 12:
        print(f"  ... and {len(problems) - 12} more")


if __name__ == "__main__":
    main()
