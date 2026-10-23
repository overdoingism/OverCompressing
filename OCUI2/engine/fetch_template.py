"""Fetch single members of a remote ZIP (the Godot export templates .tpz) with HTTP Range requests, so the
1.1 GB archive need not be downloaded whole. Verifies each member's CRC-32. Usage:
    python -I fetch_template.py <url> <out_dir> <member> [<member> ...]
"""
import struct
import sys
import urllib.request
import zlib
import os

url, out_dir, members = sys.argv[1], sys.argv[2], sys.argv[3:]


def fetch(start, end):
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "curl/8"})
    with urllib.request.urlopen(req) as r:
        if r.status not in (206,):
            raise SystemExit(f"server did not honour Range (status {r.status})")
        return r.read()


# total size
req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "curl/8"})
with urllib.request.urlopen(req) as r:
    size = int(r.headers["Content-Length"])
print("archive size", size)

tail = fetch(max(0, size - 65536), size - 1)
eocd = tail.rfind(b"PK\x05\x06")
if eocd < 0:
    raise SystemExit("no EOCD")
(disk, cd_disk, n_disk, n_total, cd_size, cd_offset, _) = struct.unpack("<HHHHIIH", tail[eocd + 4:eocd + 22])
if cd_offset == 0xFFFFFFFF or n_total == 0xFFFF:  # ZIP64
    loc = tail.rfind(b"PK\x06\x07")
    z64_off = struct.unpack("<Q", tail[loc + 8:loc + 16])[0]
    z64 = fetch(z64_off, z64_off + 55)
    (_, _, _, _, _, n_disk, n_total, cd_size, cd_offset) = struct.unpack("<QHHIIQQQQ", z64[4:56])
print("central directory", cd_offset, cd_size, n_total)
cd = fetch(cd_offset, cd_offset + cd_size - 1)
pos, found = 0, {}
while pos + 46 <= len(cd) and cd[pos:pos + 4] == b"PK\x01\x02":
    (_, _, _, method, _, _, crc, csize, usize, nlen, elen, clen, _, _, _, lho) = struct.unpack("<HHHHHHIIIHHHHHII", cd[pos + 4:pos + 46])
    name = cd[pos + 46:pos + 46 + nlen].decode("utf-8")
    extra = cd[pos + 46 + nlen:pos + 46 + nlen + elen]
    if 0xFFFFFFFF in (csize, usize, lho):  # ZIP64 extra
        e = 0
        while e + 4 <= len(extra):
            hid, hlen = struct.unpack("<HH", extra[e:e + 4])
            if hid == 1:
                vals, q = [], e + 4
                for v in (usize, csize, lho):
                    if v == 0xFFFFFFFF:
                        vals.append(struct.unpack("<Q", extra[q:q + 8])[0]); q += 8
                    else:
                        vals.append(v)
                usize, csize, lho = vals
                break
            e += 4 + hlen
    found[name] = (method, crc, csize, usize, lho)
    pos += 46 + nlen + elen + clen
print("members", len(found))
os.makedirs(out_dir, exist_ok=True)
for m in members:
    if m not in found:
        raise SystemExit(f"not in archive: {m}; some names: {list(found)[:20]}")
    method, crc, csize, usize, lho = found[m]
    head = fetch(lho, lho + 29)
    nlen, elen = struct.unpack("<HH", head[26:30])
    start = lho + 30 + nlen + elen
    print(f"fetching {m}: {csize} bytes compressed (method {method})")
    data = fetch(start, start + csize - 1)
    if method == 8:
        data = zlib.decompress(data, -15)
    elif method != 0:
        raise SystemExit(f"unsupported method {method}")
    if zlib.crc32(data) & 0xFFFFFFFF != crc or len(data) != usize:
        raise SystemExit(f"CRC mismatch for {m}")
    out = os.path.join(out_dir, os.path.basename(m))
    with open(out, "wb") as f:
        f.write(data)
    print("ok", out, usize)
