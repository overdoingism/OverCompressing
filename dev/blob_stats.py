"""How the files of models in a pack are stored, by dtype and by how each part was coded: the bytes each part restores
to and the bytes its frames take, read from the blobs' headers and frame heads (nothing is decoded). Where list shows
a file's ratio, this shows what makes it up: e.g. that a fine-tune's BF16 tensors went to ans16 deltas at 1 %, its F8
ones to XOR deltas at 30 %, its header to zstd. Each blob is counted once, however many of the models list it.

Usage: python -I blob_stats.py <pack> [MODEL ...] [--by-file] [--json]
  <pack>     a pack folder or a pack file
  MODEL      models to sum up together (default: each model of the pack on its own)
  --by-file  each file on its own as well
  --json     a JSON object per model (and per file) instead of text
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm_archive as la  # noqa: E402

CODECS = {la.CODEC_RAW: "raw", la.CODEC_PLANES: "byte planes", la.CODEC_ZSTD: "zstd", la.CODEC_ANS16: "ans16"}


def size_text(n):
    return f"{n / 2**30:,.2f} GiB" if n >= 2**30 else f"{n / 2**20:,.1f} MiB"


def how(region, codec):
    """How a region was coded, in words."""
    if codec is None:
        return "dropped (partial blob)"
    name = CODECS.get(codec & ~la.STANDALONE, f"codec {codec}")
    if "base" not in region:
        return name
    if codec & la.STANDALONE:
        return f"{name}, delta not used"
    return "ans16 delta" if codec == la.CODEC_ANS16 else f"{region['kind']} delta, {name}"


def what(region, method):
    """What a region holds: its dtype, else what the untagged bytes of such a file are."""
    if region.get("dtype"):
        return region["dtype"] + (f" (base {region['base_dtype']})" if region.get("base_dtype") else "")
    if region["kind"] == "auto":
        return "(not parsed)"
    return "(text)" if method == "zstd" else "(header, metadata, padding)"


def blob_rows(p, sha):
    """({(what, how): [regions, raw bytes, stored bytes]}, the blob's own bytes beyond its frames' payloads,
    the blob's size) of one stored file."""
    span = p.blob_span(sha)
    header, frames, _ = la.container_index(span)
    rows, payload = {}, 0
    for region, frame in zip(header["regions"], frames):
        codec, stored = (frame[0], frame[3]) if frame else (None, 0)
        row = rows.setdefault((what(region, header.get("method")), how(region, codec)), [0, 0, 0])
        row[0] += 1
        row[1] += region["length"]
        row[2] += stored
        payload += stored
    return rows, span[2] - payload, span[2]


def add(into, rows):
    for key, (n, raw, stored) in rows.items():
        row = into.setdefault(key, [0, 0, 0])
        row[0] += n
        row[1] += raw
        row[2] += stored


def summary(label, files, rows, overhead, size, stored):
    return {"name": label, "files": files, "size": size, "stored": stored, "container_bytes": overhead,
            "rows": [{"dtype": d, "how": h, "regions": n, "raw": raw, "stored": s}
                     for (d, h), (n, raw, s) in sorted(rows.items(), key=lambda kv: -kv[1][1])]}


def percent(part, whole):
    return f"{part / whole * 100:6.2f}%" if whole else "      -"


def print_text(s, indent=""):
    print(f"{indent}{s['name']}: {s['files']} file{'s' if s['files'] != 1 else ''}, {size_text(s['size'])} -> "
          f"{size_text(s['stored'])} ({percent(s['stored'], s['size']).strip()})")
    for r in s["rows"]:
        print(f"{indent}  {r['dtype']:<28}{r['how']:<30}{r['regions']:>8,} regions {size_text(r['raw']):>14} -> "
              f"{size_text(r['stored']):>13} {percent(r['stored'], r['raw'])}")
    print(f"{indent}  {'(blob headers, frame heads, trailers)':<58}{'':>31}{size_text(s['container_bytes']):>13}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack")
    parser.add_argument("models", nargs="*", metavar="MODEL")
    parser.add_argument("--by-file", action="store_true", help="each file on its own as well")
    parser.add_argument("--json", action="store_true", help="a JSON object per model (and per file)")
    args = parser.parse_args()
    try:
        p = la.open_pack(args.pack)
        manifests = la.all_manifests(p, [])
    except (OSError, ValueError) as e:
        sys.exit(f"cannot open {args.pack}: {e}")
    if args.models:
        groups = [(" + ".join(la.model_named(manifests, m, p) for m in args.models), [la.model_named(manifests, m, p) for m in args.models])]
    else:
        groups = [(name, [name]) for name in sorted(manifests)]
    for label, names in groups:
        seen, rows, overhead, size, stored, files, each = set(), {}, 0, 0, 0, 0, []
        for name in names:
            for e in manifests[name]["files"]:
                if e["sha256"] in seen:
                    continue
                seen.add(e["sha256"])
                try:
                    file_rows, extra, blob_size = blob_rows(p, e["sha256"])
                except (OSError, ValueError) as error:
                    print(f"{name}:{e['path']}: its blob cannot be read ({error})", file=sys.stderr)
                    continue
                add(rows, file_rows)
                overhead, size, stored, files = overhead + extra, size + e["size"], stored + blob_size, files + 1
                if args.by_file:
                    each.append(summary(f"{name}:{e['path']}", 1, file_rows, extra, e["size"], blob_size))
        for s, indent in [(summary(label, files, rows, overhead, size, stored), "")] + [(f, "  ") for f in each]:
            print(json.dumps(s, ensure_ascii=False)) if args.json else print_text(s, indent)


if __name__ == "__main__":
    main()
