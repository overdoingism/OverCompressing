"""Time the phases of an ans16 delta for one archived shard: base materialization, parallel encode, parallel decode.

Usage: python -I profile_ans16.py <archive_root> <base_name> <target_file> <shard_path_in_base> [workers]
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm_archive as la  # noqa: E402


def main():
    root, base_repo, target_file, shard = sys.argv[1:5]
    workers = int(sys.argv[5]) if len(sys.argv) > 5 else la.ans16_workers()
    entry = next(e for e in la.load_manifest(root, base_repo)["files"] if e["path"] == shard)
    delta = {"repo": base_repo, "path": shard, "sha256": entry["sha256"]}

    t = time.perf_counter()
    base_tmp = la.materialize_file(root, base_repo, shard, entry["sha256"])
    print(f"materialize base         {time.perf_counter() - t:7.1f}s")
    try:
        size = os.path.getsize(target_file)
        fmt, tensors = la.file_tensors(target_file, size)
        _, base_tensors = la.file_tensors(base_tmp, os.path.getsize(base_tmp))
        match = la.match_against_base(tensors, [{"tensors": base_tensors}], "ans16")
        regions = la.tensor_regions(tensors, size, 64 << 20, match)
        jobs = [(base_tmp, r["base"][1], target_file, r["offset"], r["length"], r["dtype"]) for r in regions if r["kind"] == "ans16"]
        print(f"{len(jobs)} ans16 regions, {workers} workers")
        t = time.perf_counter()
        with ProcessPoolExecutor(workers) as pool:
            list(pool.map(_noop, range(workers)))
            print(f"pool start               {time.perf_counter() - t:7.1f}s")
            t = time.perf_counter()
            payloads = list(pool.map(la._ans16_encode_job, jobs))
            print(f"encode                   {time.perf_counter() - t:7.1f}s")
        tmp = os.path.join(os.environ.get("OC_TMP") or ".", "profile_payloads.bin")
        offsets = []
        with open(tmp, "wb") as f:
            for p in payloads:
                offsets.append((f.tell(), len(p)))
                f.write(p)
        t = time.perf_counter()
        with ProcessPoolExecutor(workers) as pool:
            out = list(pool.map(la._ans16_decode_job, [(tmp, o, n, base_tmp, j[1], j[4], j[5]) for (o, n), j in zip(offsets, jobs)]))
        print(f"decode (incl. pool start){time.perf_counter() - t:7.1f}s")
        ok = True
        with open(target_file, "rb") as f:
            for j, data in zip(jobs, out):
                f.seek(j[3])
                ok &= f.read(j[4]) == data
        print("round trip", "OK" if ok else "MISMATCH")
        os.remove(tmp)
    finally:
        os.remove(base_tmp)


def _noop(_):
    return None


if __name__ == "__main__":
    main()
