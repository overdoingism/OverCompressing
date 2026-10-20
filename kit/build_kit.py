"""Assemble the offline restore kit (normally <archive_root>/_kit) from this repository plus pinned third-party
downloads. Every downloaded file is checked against the SHA-256 recorded below or in kit/requirements-*.lock.

    python -I kit/build_kit.py <out_dir> [--cache DIR]

--cache DIR   a previous kit (or any folder): files found there with the right SHA-256 are copied instead of
              downloaded, so a kit can be rebuilt offline.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/20261001"
PBS_FILE = "cpython-3.13.16+20261001-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PY_ORG = "https://www.python.org/ftp/python/3.13.16"

# (path inside the kit, URL, SHA-256)
DOWNLOADS = [
    ("python/windows/python-3.13.16-amd64.exe", f"{PY_ORG}/python-3.13.16-amd64.exe",
     "fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b"),
    ("python/windows/python-3.13.16-amd64.exe.asc", f"{PY_ORG}/python-3.13.16-amd64.exe.asc",
     "ccec3865848183975279c0a478d5a2f40956dbffe0e091fb75740747081f74f0"),
    ("python/windows/python-3.13.16-amd64.exe.sigstore", f"{PY_ORG}/python-3.13.16-amd64.exe.sigstore",
     "5924e0951be365ffef494a7ee646baa81eab6a930070eb35903140c5198f315c"),
    (f"python/linux/{PBS_FILE}", f"{PBS}/{PBS_FILE.replace('+', '%2B')}",
     "ffcb50e716789d1a6e1db5e745d4d194ac8ed9b015ccf5eabcaccb179a25e4a8"),
    ("python/linux/SHA256SUMS.release", f"{PBS}/SHA256SUMS",
     "3573468fd1f7973d1f0236e5bb0a3ba654f169a24a3d69252e77a80b70ffe2ab"),
]
# (path in this repository, path inside the kit)
REPO_FILES = [
    ("kit/README.md", "README.md"),
    ("kit/install_windows.ps1", "install_windows.ps1"),
    ("kit/install_linux.sh", "install_linux.sh"),
    ("kit/requirements.txt", "requirements.txt"),
    ("kit/python-README.md", "python/README.md"),
    ("kit/keys/python-windows-steve-dower-FC624643487034E5.asc", "python/windows/steve-dower-FC624643487034E5.asc"),
    (f"kit/provenance/attestation-cpython-3.13.16+20261001-x86_64-linux-install_only_stripped.json",
     "python/linux/attestation-cpython-3.13.16+20261001-x86_64-linux-install_only_stripped.json"),
    ("tools/llm_archive.py", "tools/llm_archive.py"),
    ("tools/ARCHIVE_FORMAT.md", "tools/ARCHIVE_FORMAT.md"),
    ("tools/test_ans16.py", "tools/test_ans16.py"),
    ("tools/test_formats.py", "tools/test_formats.py"),
    ("tools/compare_tensors.py", "tools/compare_tensors.py"),
    ("docs", "docs"),
    ("LICENSE", "LICENSE"),
    ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
]
WHEEL_PLATFORMS = {
    "win_amd64": ["--platform", "win_amd64"],
    "linux_x86_64": ["--platform", "manylinux_2_28_x86_64", "--platform", "manylinux_2_27_x86_64",
                     "--platform", "manylinux_2_17_x86_64", "--platform", "manylinux2014_x86_64"],
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(16 << 20):
            h.update(block)
    return h.hexdigest()


def find_in_cache(cache, name, sha):
    if not cache:
        return None
    for dirpath, _, names in os.walk(cache):
        if name in names and sha256_file(os.path.join(dirpath, name)) == sha:
            return os.path.join(dirpath, name)
    return None


def fetch(out, rel, url, sha, cache):
    dst = os.path.join(out, *rel.split("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cached = find_in_cache(cache, os.path.basename(rel), sha)
    if cached:
        shutil.copyfile(cached, dst)
        source = "cache"
    else:
        with urllib.request.urlopen(url, timeout=120) as r, open(dst + ".part", "wb") as f:
            shutil.copyfileobj(r, f, 16 << 20)
        os.replace(dst + ".part", dst)
        source = "downloaded"
    if sha256_file(dst) != sha:
        os.remove(dst)
        sys.exit(f"SHA-256 mismatch for {rel} ({url}); refusing to build the kit")
    print(f"  {source:<10} {rel}")


def pip_download(out, plat, cache):
    dest = os.path.join(out, "wheels", plat)
    os.makedirs(dest, exist_ok=True)
    base = [sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "--require-hashes",
            "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313", "--abi", "abi3", "--abi", "none",
            *WHEEL_PLATFORMS[plat], "-d", dest, "-r", os.path.join(REPO, "kit", f"requirements-{plat}.lock")]
    cached = os.path.join(cache, "wheels", plat) if cache else None
    if cached and os.path.isdir(cached):
        if subprocess.run(base + ["--no-index", "--find-links", cached]).returncode == 0:
            print(f"  cache      wheels/{plat} (hash-checked by pip)")
            return
    if subprocess.run(base).returncode != 0:
        sys.exit(f"pip could not fetch the pinned wheels for {plat}")
    print(f"  downloaded wheels/{plat} (hash-checked by pip)")


def write_checksums(out):
    files = []
    for dirpath, _, names in os.walk(out):
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), out).replace(os.sep, "/")
            if rel != "SHA256SUMS":
                files.append(rel)
    files.sort()
    with open(os.path.join(out, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as f:
        for rel in files:
            f.write(f"{sha256_file(os.path.join(out, rel))}  {rel}\n")
    print(f"  SHA256SUMS for {len(files)} files")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out_dir")
    p.add_argument("--cache")
    args = p.parse_args()
    out = os.path.abspath(args.out_dir)
    if os.path.exists(out) and os.listdir(out):
        sys.exit(f"{out} is not empty; give an empty or new folder")
    print(f"building the offline kit in {out}")
    for rel_src, rel_dst in REPO_FILES:
        src, dst = os.path.join(REPO, *rel_src.split("/")), os.path.join(out, *rel_dst.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        (shutil.copytree if os.path.isdir(src) else shutil.copyfile)(src, dst)
    print(f"  copied     {len(REPO_FILES)} files/folders from the repository")
    for rel, url, sha in DOWNLOADS:
        fetch(out, rel, url, sha, args.cache)
    for plat in WHEEL_PLATFORMS:
        pip_download(out, plat, args.cache)
    write_checksums(out)
    print("done")


if __name__ == "__main__":
    main()
