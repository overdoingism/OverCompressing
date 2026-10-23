"""What the archiver makes of model files, read from their headers: which parser takes each file (or why none does,
so that it is stored as plain bytes), how its bytes divide by dtype and how each part is coded, and, with --against,
how much of it lines up with a base model's tensors for a delta. It uses llm_archive's own parsers and matching, so it
reports what pack would do; nothing is compressed, so it says nothing of ratios.

A file may be only its first bytes (fetched with an HTTP range request): --sizes gives the whole file's size. The
header must then be complete: safetensors 8 bytes plus the length they give, GGUF through its tensor table (often
several MiB, the vocabulary being in it; the script says when the bytes given end too soon).

Usage: python -I coverage.py PATH ... [--sizes FILE] [--against PATH ... [--base-sizes FILE]] [--json]
  PATH          a model's files or folder (walked; links are not followed)
  --sizes       the whole sizes of PATH's files: JSON, {"path": size, ...} or a Hugging Face tree listing
                ([{"path", "size", ...}, ...], from
                https://huggingface.co/api/models/<org>/<name>/tree/<revision>?recursive=True), each path relative
                to the folder given, or the file's name for a file given by itself
  --against     the base model's files or folder
  --base-sizes  the same for the base's files
  --json        a JSON object per file instead of text
"""

import argparse
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm_archive as la  # noqa: E402

CHUNK = 64 << 20  # pack's default --chunk-mib

# ggml type numbers as ggml.h names them (2025). The archiver knows only F32, F16 and BF16; any other type it stores
# as opaque bytes up to the next tensor, whatever its number.
GGML_NAMES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1", 10: "Q2_K",
              11: "Q3_K", 12: "Q4_K", 13: "Q5_K", 14: "Q6_K", 15: "Q8_K", 16: "IQ2_XXS", 17: "IQ2_XS", 18: "IQ3_XXS",
              19: "IQ1_S", 20: "IQ4_NL", 21: "IQ3_S", 22: "IQ2_S", 23: "IQ4_XS", 24: "I8", 25: "I16", 26: "I32",
              27: "I64", 28: "F64", 29: "IQ1_M", 30: "BF16", 34: "TQ1_0", 35: "TQ2_0", 39: "MXFP4"}


def size_text(n):
    return f"{n / 2**30:,.2f} GiB" if n >= 2**30 else f"{n / 2**20:,.1f} MiB"


def dtype_text(dtype):
    if dtype.startswith("ggml_type_"):
        number = int(dtype[len("ggml_type_"):])
        return f"{GGML_NAMES[number]} (ggml {number})" if number in GGML_NAMES else f"ggml {number} (no name here)"
    return dtype


def walk(path):
    """[(file, its key in --sizes)] for a file, or every file below a folder; links are left out."""
    if not os.path.isdir(path):
        return [(path, os.path.basename(path))]
    found = []
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
        for name in sorted(files):
            full = os.path.join(root, name)
            if not os.path.islink(full):
                found.append((full, os.path.relpath(full, path).replace(os.sep, "/")))
    return found


def load_sizes(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):  # a Hugging Face tree listing
        data = {e["path"]: e["size"] for e in data if isinstance(e, dict) and e.get("type", "file") == "file"}
    if not (isinstance(data, dict) and all(isinstance(k, str) and type(v) is int and v >= 0 for k, v in data.items())):
        sys.exit(f"{path}: not a JSON object of paths and sizes, nor a tree listing")
    return data


def whole_size(path, key, sizes):
    """(the whole file's size, whether only its first bytes are here)."""
    actual = os.path.getsize(path)
    size = sizes.get(key, actual)
    if size < actual:
        raise ValueError(f"--sizes gives {size:,} bytes, but the file has {actual:,}")
    return size, size > actual


def gguf_problem(path, size, header_only):
    """Why the archiver takes a GGUF file as plain bytes, found by walking its header as gguf_tensors does (no length
    is read past the bytes there are) and then checking what file_tensors and tensor_regions check."""
    actual = os.path.getsize(path)
    parsed = la.gguf_tensors(path)
    if parsed is None:
        with open(path, "rb") as f:
            def take(n):
                if n > actual - f.tell():
                    raise EOFError
                return f.read(n)

            def number(fmt):
                st = struct.Struct(fmt)
                return st.unpack(take(st.size))[0]

            try:
                take(4)
                version = number("<I")
                if version not in (2, 3):
                    return f"GGUF version {version} (the archiver reads 2 and 3)"
                n_tensors, n_kv = number("<Q"), number("<Q")
                alignment = 32
                for _ in range(n_kv):
                    key = take(number("<Q")).decode("utf-8", "backslashreplace")
                    vtype = number("<I")
                    if vtype in la.GGUF_SCALARS:
                        value = number(la.GGUF_SCALARS[vtype])
                        if key == "general.alignment":
                            alignment = int(value)
                    elif vtype == la.GGUF_STRING:
                        take(number("<Q"))
                    elif vtype == la.GGUF_ARRAY:
                        etype, count = number("<I"), number("<Q")
                        if etype in la.GGUF_SCALARS:
                            take(struct.calcsize(la.GGUF_SCALARS[etype]) * count)
                        elif etype == la.GGUF_STRING:
                            for _ in range(count):
                                take(number("<Q"))
                        elif etype == la.GGUF_ARRAY:
                            return f"metadata {key!r} is an array of arrays (not read)"
                        else:
                            return f"metadata {key!r} is an array of value type {etype}, not one GGUF defines"
                    else:
                        return f"metadata {key!r} has value type {vtype}, not one GGUF defines"
                names = set()
                for _ in range(n_tensors):
                    raw = take(number("<Q"))
                    try:
                        name = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        return f"a tensor name is not UTF-8: {raw[:60]!r}"
                    n_dims = number("<I")
                    if n_dims > la.GGUF_MAX_DIMS:
                        return f"tensor {name!r} has {n_dims} dimensions (at most {la.GGUF_MAX_DIMS} are read)"
                    take(8 * n_dims + 12)
                    if name in names:
                        return f"tensor name {name!r} appears twice"
                    names.add(name)
                if alignment < 1:
                    return f"general.alignment is {alignment}"
            except EOFError:
                return f"the header goes on past the {actual:,} bytes given" if header_only else "the file ends inside its header"
        return "refused by the archiver's GGUF reader (this script did not find why)"
    data_start, raw = parsed
    starts = sorted(data_start + offset for *_, offset in raw) + [size]
    for name, ggml_type, dims, offset in raw:
        if ggml_type in la.GGUF_FLOAT_TYPES:
            start = data_start + offset
            room = next((s for s in starts if s > start), size) - start
            dtype, element = la.GGUF_FLOAT_TYPES[ggml_type]
            if element * math.prod(dims) > room:
                return f"tensor {name!r} ({dtype}, {dims}) needs more bytes than lie before the next tensor or the end"
    return table_problem(la.file_tensors(path, size)[1], size)


def safetensors_problem(path, size, header_only):
    """Why the archiver takes a safetensors file (by name or content) as plain bytes; the checks of
    safetensors_layout and tensor_regions, one at a time."""
    actual = os.path.getsize(path)
    if actual < 8:
        return "shorter than 8 bytes"
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > la.SAFETENSORS_MAX_HEADER:
            return f"a header of {n:,} bytes (the format allows {la.SAFETENSORS_MAX_HEADER:,})"
        if n > size - 8:
            return f"a header of {n:,} bytes, longer than the file"
        if actual < 8 + n:
            return f"the header needs the first {8 + n:,} bytes; {actual:,} given" if header_only else "the file ends inside its header"
        try:
            header = la.strict_json(f.read(n))
        except (ValueError, RecursionError) as e:
            return f"the header is not strict JSON ({e})"
    if not isinstance(header, dict):
        return "the header is not a JSON object"
    for name, v in header.items():
        if name == "__metadata__":
            continue
        if not (isinstance(v, dict) and {"dtype", "shape", "data_offsets"} <= v.keys()):
            return f"tensor {name!r} is not an object with dtype, shape and data_offsets"
        dtype, shape, offsets = v["dtype"], v["shape"], v["data_offsets"]
        if not isinstance(dtype, str):
            return f"tensor {name!r}: dtype {dtype!r} is not a string"
        if not (isinstance(shape, list) and all(type(i) is int and i >= 0 for i in shape)):
            return f"tensor {name!r}: shape {shape!r} is not a list of non-negative integers"
        if not (isinstance(offsets, list) and len(offsets) == 2 and all(type(i) is int and i >= 0 for i in offsets)):
            return f"tensor {name!r}: data_offsets {offsets!r} are not two non-negative integers"
        if not offsets[0] <= offsets[1] <= size - 8 - n:
            return f"tensor {name!r}: data_offsets {offsets} lie outside the data ({size - 8 - n:,} bytes)"
    return table_problem(la.file_tensors(path, size)[1], size)


def table_problem(tensors, size):
    """What tensor_regions refuses in a tensor table that parsed: overlapping tensors, or one past the end."""
    if tensors is None:
        return "refused by the archiver's reader (this script did not find why)"
    pos, before = 0, None
    for name, _, start, end, *_ in sorted(tensors, key=lambda t: (t[2], t[3])):
        if end > size:
            return f"tensor {name!r} ends at byte {end:,}, past the end of the file"
        if start < pos:
            return f"tensors {before!r} and {name!r} overlap"
        pos, before = end, name
    return "refused by the archiver's reader (this script did not find why)"


def base_tables(paths, sizes):
    """The base model's parsed files, as match_against_base takes them (pack offers it every parsed file of the
    base)."""
    tables = []
    for arg in paths:
        for path, key in walk(arg):
            size, _ = whole_size(path, key, sizes)
            tensors = la.file_tensors(path, size)[1]
            if tensors:
                tables.append({"tensors": tensors})
    return tables


def describe(path, key, sizes, bases):
    size, header_only = whole_size(path, key, sizes)
    fmt, tensors, regions = la.file_regions(path, size, CHUNK)
    out = {"path": key, "size": size, "header_only": header_only, "format": fmt}
    if tensors is None:
        with open(path, "rb") as f:
            head = f.read(9)
        if head[:4] == b"GGUF":
            out["fallback"] = gguf_problem(path, size, header_only)
        elif path.endswith(".safetensors") or head[8:9] == b"{":
            out["fallback"] = safetensors_problem(path, size, header_only)
        out["coded_as"] = "zstd (text)" if fmt == "zstd" else "auto: per 64 MiB, the smaller of BF16 byte planes and zstd"
        return out
    rows = {}
    for name, dtype, start, end, shape in tensors:
        row = rows.setdefault(dtype, {"dtype": dtype, "tensors": 0, "bytes": 0,
                                      "coded_as": "byte planes" if dtype in la.FLOAT_DTYPES else "zstd"})
        row["tensors"] += 1
        row["bytes"] += end - start
        if dtype in la.FLOAT_DTYPES and end - start != math.prod(shape) * la.FLOAT_DTYPES[dtype]:
            row["unlike_shape"] = row.get("unlike_shape", 0) + 1  # a float label on bytes of another size
    out["dtypes"] = sorted(rows.values(), key=lambda r: -r["bytes"])
    out["header_bytes"] = sum(r["length"] for r in regions if "dtype" not in r)  # headers, metadata, padding: zstd
    if bases is not None:
        match = la.match_against_base(tensors, bases, "ans16")
        d = {"tensor_bytes": 0, "ans16": 0, "xor": 0, "converted": 0, "leading_rows": 0}
        for tensor in tensors:
            length = tensor[3] - tensor[2]
            d["tensor_bytes"] += length
            m = match(tensor) if length else None
            if m:
                _, _, kind, base_dtype, covered = m
                d[kind] += covered
                d["converted"] += covered if base_dtype else 0
                d["leading_rows"] += covered if covered < length else 0
        out["against"] = d
    return out


def percent(part, whole):
    return f"{part / whole * 100:5.1f}%" if whole else "    -"


def print_text(out):
    head = f"{out['path']}  {size_text(out['size'])}" + (", header only" if out["header_only"] else "")
    if "error" in out:
        print(f"{head}\n  cannot be read: {out['error']}")
        return
    if "dtypes" not in out:
        why = f"; not parsed: {out['fallback']}" if out.get("fallback") else ""
        print(f"{head}\n  {out['coded_as']}{why}")
        return
    print(f"{head}\n  {out['format']}: {sum(r['tensors'] for r in out['dtypes']):,} tensors")
    for r in out["dtypes"]:
        note = f"  ({r['unlike_shape']} with bytes unlike their shape)" if r.get("unlike_shape") else ""
        print(f"    {dtype_text(r['dtype']):<22}{r['tensors']:>7,} tensors {size_text(r['bytes']):>14} "
              f"{percent(r['bytes'], out['size'])}  {r['coded_as']}{note}")
    print(f"    {'header and padding':<38}{size_text(out['header_bytes']):>14} {percent(out['header_bytes'], out['size'])}  zstd")
    if "against" in out:
        d, total = out["against"], out["against"]["tensor_bytes"]
        print(f"  against the base: {percent(d['ans16'] + d['xor'], total).strip()} of tensor bytes line up "
              f"(ans16 {percent(d['ans16'], total).strip()}, xor {percent(d['xor'], total).strip()}; through a dtype "
              f"conversion {percent(d['converted'], total).strip()}, leading rows only {percent(d['leading_rows'], total).strip()})")


def print_totals(results, against):
    """All files at once: bytes by how they are coded, and by format."""
    ways, formats, total = {}, {}, 0
    for out in results:
        if "error" in out:
            continue
        total += out["size"]
        formats[out["format"]] = formats.get(out["format"], 0) + 1
        if "dtypes" in out:
            for r in out["dtypes"]:
                way = "zstd (tensors)" if r["coded_as"] == "zstd" else r["coded_as"]
                ways[way] = ways.get(way, 0) + r["bytes"]
            ways["zstd (header and padding)"] = ways.get("zstd (header and padding)", 0) + out["header_bytes"]
        else:
            way = "zstd (text)" if out["format"] == "zstd" else "auto (not parsed)"
            ways[way] = ways.get(way, 0) + out["size"]
    print(f"\nall {len(results)} files, {size_text(total)}: " + ", ".join(f"{n} {f}" for f, n in sorted(formats.items())))
    for way, n in sorted(ways.items(), key=lambda w: -w[1]):
        print(f"    {way:<38}{size_text(n):>14} {percent(n, total)}")
    if against:
        tensor_bytes = sum(o["against"]["tensor_bytes"] for o in results if "against" in o)
        lined_up = sum(o["against"]["ans16"] + o["against"]["xor"] for o in results if "against" in o)
        print(f"  against the base: {percent(lined_up, tensor_bytes).strip()} of all tensor bytes line up")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")  # names from files on a console that cannot show them
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", metavar="PATH")
    parser.add_argument("--sizes", metavar="FILE", help="JSON of whole sizes for files that are only their first bytes")
    parser.add_argument("--against", nargs="+", metavar="PATH", help="the base model's files or folder")
    parser.add_argument("--base-sizes", metavar="FILE", help="the same as --sizes, for the base's files")
    parser.add_argument("--json", action="store_true", help="a JSON object per file")
    args = parser.parse_args()
    if args.base_sizes and not args.against:
        parser.error("--base-sizes goes with --against")
    try:
        sizes = load_sizes(args.sizes) if args.sizes else {}
        bases = base_tables(args.against, load_sizes(args.base_sizes) if args.base_sizes else {}) if args.against else None
    except (OSError, ValueError) as e:
        sys.exit(str(e))
    results = []
    for arg in args.paths:
        for path, key in walk(arg):
            try:
                out = describe(path, key, sizes, bases)
            except (OSError, ValueError) as e:
                out = {"path": key, "size": 0, "header_only": False, "error": str(e)}
            results.append(out)
            if args.json:
                print(json.dumps(out, ensure_ascii=False))
            else:
                print_text(out)
    if not args.json and len(results) > 1:
        print_totals(results, bases is not None)


if __name__ == "__main__":
    main()
