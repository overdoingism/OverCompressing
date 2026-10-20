"""Make a test model folder that looks like a community re-upload: the tensors of a safetensors file split into N
shards, in reversed order, with a fresh header. Pack the folder with llm_archive.py to test tensor matching.

Usage: python -I make_resharded.py <source.safetensors> <out_dir> [shards]
"""

import json
import os
import struct
import sys


def main():
    src, out = sys.argv[1:3]
    shards = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    with open(src, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
        data_start = 8 + n
        tensors = [(k, v) for k, v in header.items() if k != "__metadata__"]
        tensors.sort(key=lambda kv: kv[1]["data_offsets"][0], reverse=True)
        os.makedirs(out, exist_ok=True)
        per = -(-len(tensors) // shards)
        for i in range(shards):
            part = tensors[i * per:(i + 1) * per]
            name = f"model-{i + 1:05d}-of-{shards:05d}.safetensors"
            new_header, off, blobs = {"__metadata__": {"format": "pt"}}, 0, []
            for key, info in part:
                s, e = info["data_offsets"]
                f.seek(data_start + s)
                blobs.append(f.read(e - s))
                new_header[key] = {"dtype": info["dtype"], "shape": info["shape"], "data_offsets": [off, off + e - s]}
                off += e - s
            h = json.dumps(new_header).encode()
            h += b" " * (-len(h) % 8)
            path = os.path.join(out, name)
            with open(path, "wb") as w:
                w.write(struct.pack("<Q", len(h)) + h)
                for b in blobs:
                    w.write(b)
            print(f"{name}: {len(part)} tensors, {os.path.getsize(path):,} bytes")


if __name__ == "__main__":
    main()
