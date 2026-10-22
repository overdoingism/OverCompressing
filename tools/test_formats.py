"""Tests for llm_archive.py: containers of synthetic files of several formats, then packs (pack folders and pack
files) built, changed, damaged and misused through the command line.

Usage: python -I test_formats.py <scratch_dir>
"""

import json
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import tracemalloc
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import zstandard  # noqa: E402

import llm_archive as la  # noqa: E402

rng = np.random.default_rng(7)
scratch = os.path.abspath(sys.argv[1]) if __name__ == "__main__" else None
failures = 0
clean = lambda out: "Traceback" not in out  # noqa: E731


def weights(n, dtype):
    return (rng.standard_normal(n) * 0.02).astype(dtype)


def bf16_bytes(n):
    return (weights(n, np.float32).view(np.uint32) >> 16).astype(np.uint16).tobytes()


def bf(v):
    return la.convert_bits(np.asarray(v, np.float32).view(np.uint32), "F32", "BF16").tobytes()


def expect(name, ok, detail=""):
    global failures
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail else ""))


# ----------------------------------------------------------------------------------------------- containers


def write_blob(path, out, regions, bases=(), base_paths=(), workers=1):
    """Write the blob of the file at path to `out` as pack does (header, frames, its SHA-256 at the end); returns
    (the file's SHA-256, frame codecs used)."""
    size, sha = os.path.getsize(path), la.sha256_file(path)
    header = {"original_size": size, "sha256": sha, "method": "test", "regions": regions, "bases": list(bases)}
    with open(out, "wb") as f:
        w = la.HashingWriter(f)
        codecs = la.write_container(path, w, header, base_paths, (19, 9), workers)
        f.write(w.hash.digest())
    return sha, codecs


def read_blob(sha, out):
    """The original bytes of the blob at `out`, its bases (if any) found in la._BASE_CACHE."""
    p = la.FolderPack(scratch)
    return b"".join(la.iter_blob_bytes(p, sha, span=(out, 0, os.path.getsize(out))))


def check(name, path):
    global failures
    size = os.path.getsize(path)
    fmt, _, regions = la.file_regions(path, size, 1 << 20)
    out = path + ".ocz"
    sha, codecs = write_blob(path, out, regions)
    ok = read_blob(sha, out) == open(path, "rb").read() and la.blob_damage((out, 0, os.path.getsize(out)), sha) is None
    failures += not ok
    kinds = sorted({r.get("dtype", r["kind"]) for r in regions})
    used = {la.CODEC_NAMES[c]: n for c, n in sorted(codecs.items())}
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<34} fmt={fmt:<12} {os.path.getsize(out) / max(size, 1) * 100:6.2f}%  {kinds} {used}")


def write_safetensors(path, tensors):
    header, blobs, off = {}, [], 0
    for name, (dtype, shape, blob) in tensors.items():
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [off, off + len(blob)]}
        blobs.append(blob)
        off += len(blob)
    h = json.dumps(header).encode()
    h += b" " * (-len(h) % 8)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(h)) + h + b"".join(blobs))


# ----------------------------------------------------------------------------------------------- running the tool


TOOL_DIR = os.path.dirname(os.path.abspath(__file__))


def tool(*argv, env=None, timeout=None):
    """(exit code, output) of llm_archive.py run with these arguments (and extra environment variables)."""
    run = subprocess.run([sys.executable, "-I", os.path.join(TOOL_DIR, "llm_archive.py"), *argv], capture_output=True,
                         text=True, env={**os.environ, **(env or {})}, timeout=timeout)
    return run.returncode, run.stdout + run.stderr


def script_for(patch, argv):
    return f"""import sys
sys.path.insert(0, {TOOL_DIR!r})
import llm_archive as la
{patch}
sys.argv = ["llm_archive.py", *{list(argv)!r}]
la.main()
"""


def patched_process(patch, *argv):
    """A running "llm_archive.py *argv", started after the Python statement(s) `patch` (on module la)."""
    return subprocess.Popen([sys.executable, "-I", "-c", script_for(patch, argv)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def patched(patch, *argv):
    """(exit code, output) of "llm_archive.py *argv" run after the Python statement(s) `patch`."""
    run = subprocess.run([sys.executable, "-I", "-c", script_for(patch, argv)], capture_output=True, text=True)
    return run.returncode, run.stdout + run.stderr


def interrupted(when, *argv):
    """(exit code, output) of llm_archive.py stopped as by Ctrl+C just "before" or just "after" it puts a manifest in
    place."""
    patch = f"""_write = la.write_manifest
def _stop(p, manifest):
    if {when == "after"}:
        _write(p, manifest)
    raise KeyboardInterrupt
la.write_manifest = _stop"""
    return patched(patch, *argv)


def pause_in(function, ready, go):
    """A patch that makes la.<function> create the file `ready` and wait (up to 2 min) for the file `go` first."""
    return f"""import os, time
_inner = la.{function}
def _paused(*a, **k):
    open({ready!r}, "w").close()
    for _ in range(1200):
        if os.path.exists({go!r}):
            break
        time.sleep(0.1)
    return _inner(*a, **k)
la.{function} = _paused"""


def wait_for(path, seconds=60):
    for _ in range(seconds * 10):
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def pack(folder, archive, name, *options, **run):
    """tool("pack", ...); run: env and timeout, as for tool."""
    return tool("pack", folder, archive, "--name", name, *options, **run)


def link_dir(link, target):
    """Make `link` a symlink to the folder `target`, or on Windows without the symlink right a directory junction
    (which redirects the same way). Whether it worked."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", os.path.normpath(link), os.path.normpath(target)], capture_output=True)
    return os.path.isdir(link)


# ----------------------------------------------------------------------------------------------- looking into a pack


def models(pack_path):
    """{name: manifest} of the pack at pack_path, read now."""
    la.forget_cached_reads()
    try:
        return la.all_manifests(la.open_pack(pack_path), [])
    finally:
        la.close_pack_files()


def entry(pack_path, name, path):
    return next(e for e in models(pack_path)[name]["files"] if e["path"] == path)


def info(pack_path, name, path="model.safetensors"):
    """blob_info of one file of a model: its blob's size, method and bases."""
    sha = entry(pack_path, name, path)["sha256"]
    try:
        return la.blob_info(la.open_pack(pack_path), sha)
    finally:
        la.close_pack_files()


def base_of(pack_path, name, path="model.safetensors"):
    """The model a file's blob was coded against (as its header names it), or None."""
    found = sorted({b.get("model") for b in info(pack_path, name, path)["bases"]})
    return found[0] if len(found) == 1 else found or None


def stored(pack_path, name):
    """Bytes of the distinct blobs a model lists."""
    shas = {e["sha256"] for e in models(pack_path)[name]["files"]}
    return sum(os.path.getsize(blob_path(pack_path, sha)) for sha in shas)


def blob_path(pack_path, sha):
    return os.path.join(pack_path, *la.blob_rel(sha).split("/"))


def blobs(pack_path):
    return la.FolderPack(pack_path).blob_ids()


def leftovers(folder):
    """Temporary files (.oc-*.part) anywhere below folder."""
    return [n for _, _, names in os.walk(folder) for n in names if n.startswith(".oc-") and n.endswith(".part")]


def make_folder(path, files):
    """A source folder holding these {relative path: bytes}."""
    shutil.rmtree(path, ignore_errors=True)
    for rel, data in files.items():
        full = os.path.join(path, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
    return path


def same_tree(folder, files):
    """Whether `folder` holds exactly these {relative path: bytes}."""
    found = {}
    for dirpath, _, names in os.walk(folder):
        for n in names:
            full = os.path.join(dirpath, n)
            found[os.path.relpath(full, folder).replace(os.sep, "/")] = open(full, "rb").read()
    return found == files


def files_of(folder):
    return {rel: open(os.path.join(folder, *rel.split("/")), "rb").read()
            for rel in (os.path.relpath(os.path.join(d, n), folder).replace(os.sep, "/") for d, _, ns in os.walk(folder) for n in ns)}


def manifest_file(pack_path, name):
    return os.path.join(pack_path, *la.manifest_rel(name).split("/"))


def edit_manifest(pack_path, name, change):
    path = manifest_file(pack_path, name)
    manifest = json.load(open(path, encoding="utf-8"))
    change(manifest)
    json.dump(manifest, open(path, "w", encoding="utf-8"), indent=1)


def state(pack_path):
    """What a pack folder holds: every file's path and bytes (blobs by size and CRC only), but for the lock and
    ocpack.json, whose stamp every command that may change the pack renews."""
    found = {}
    for rel, data in files_of(pack_path).items():
        if rel not in (la.LOCK_NAME, la.MARKER):
            found[rel] = (len(data), zlib.crc32(data))
    return found


# ----------------------------------------------------------------------------------------------- packs


def check_auto_base():
    """Packs a small family into a fresh pack without --base and checks the base each model picks; that a re-pack
    of an unchanged model stores nothing again, and one stopped before or after its manifest leaves the pack whole;
    --no-base and --base; and read_range() against the original bytes for every kind of stored file."""
    print("automatic base selection")
    archive, src = os.path.join(scratch, "auto_pack"), os.path.join(scratch, "auto_models")
    shutil.rmtree(archive, ignore_errors=True)
    cols = 1000
    a = (rng.standard_normal(2000 * cols) * 0.02).astype(np.float32)
    b = a + (rng.standard_normal(a.size) * 2e-3).astype(np.float32)  # a fine-tune of A
    d = b.copy()  # a light fine-tune of B: closer to B than to A
    touched = rng.random(d.size) < 0.05
    d[touched] += (rng.standard_normal(touched.sum()) * 1e-3).astype(np.float32)
    c = (rng.standard_normal(a.size) * 0.02).astype(np.float32)  # unrelated, same architecture
    bfd = la.convert_bits(d.view(np.uint32), "F32", "BF16")
    e = np.concatenate([la.convert_bits(bfd, "BF16", "F16"), (rng.standard_normal(128 * cols) * 0.02).astype(np.float16).view(np.uint16)])
    norm = ("F32", [64], a[:64].tobytes())
    family = [  # (name, tensors, expected base)
        ("t/A", {"w": ("BF16", [2000, cols], bf(a)), "n": norm}, None),
        ("t/C", {"w": ("BF16", [2000, cols], bf(c)), "n": norm}, None),
        ("t/B", {"w": ("BF16", [2000, cols], bf(b)), "n": norm}, "t/A"),
        ("t/D", {"w": ("BF16", [2000, cols], bf(d)), "n": norm}, "t/B"),
        ("t/E", {"n": norm, "w": ("F16", [2128, cols], e.tobytes())}, "t/D"),  # F16, extended vocabulary
        ("t/D2", {"w": ("BF16", [2000, cols], bf(d + np.float32(1e-3))), "n": norm}, None),  # --no-base
        ("t/C2", {"w": ("BF16", [2000, cols], bf(c * np.float32(1.01))), "n": norm}, "t/A"),  # --base t/A
    ]
    options = {"t/D2": ["--no-base"], "t/C2": ["--base", "t/A"]}
    originals = {}
    for name, tensors, want in family:
        folder = os.path.join(src, name.replace("/", "_"))
        os.makedirs(folder, exist_ok=True)
        write_safetensors(os.path.join(folder, "model.safetensors"), tensors)
        originals[name] = open(os.path.join(folder, "model.safetensors"), "rb").read()
        if name == "t/A":  # a small file
            with open(os.path.join(folder, "tokenizer_config.json"), "w") as fh:
                json.dump({"version": 1}, fh)
        code, out = pack(folder, archive, name, *options.get(name, []))
        got = base_of(archive, name) if code == 0 else "pack failed"
        what = {"t/D2": " (--no-base)", "t/C2": " (--base t/A, an unrelated model)"}.get(name, "")
        expect(f"{name} picks {want or 'no base'}{what}", code == 0 and got == want, got if got != want else "")
        if got != want:
            print(out)
    before = blobs(archive)
    code, out = pack(os.path.join(src, "t_A"), archive, "t/A", "--base", "t/B", "--replace")
    ok = code == 0 and out.count("stored already") == 2 and blobs(archive) == before and base_of(archive, "t/A") is None
    expect("re-pack of the unchanged t/A (even with --base t/B) stores nothing again", ok, "" if ok else out[-300:])
    lines = [line for line in out.splitlines() if "stored already" in line]
    ok = len(lines) == 2 and all("-> " in line and "0.0 MiB    0.00%" in line for line in lines)
    expect("... and its lines say so: 0.0 MiB, 0.00% (what each file adds)", ok, ascii(lines))

    tok = os.path.join(src, "t_A", "tokenizer_config.json")
    old_tok = entry(archive, "t/A", "tokenizer_config.json")["sha256"]
    with open(tok, "w") as fh:
        json.dump({"version": 2}, fh)
    new_tok = la.sha256_file(tok)
    code, out = interrupted("before", "pack", os.path.join(src, "t_A"), archive, "--name", "t/A", "--replace")
    ok = code != 0 and "KeyboardInterrupt" in out and entry(archive, "t/A", "tokenizer_config.json")["sha256"] == old_tok \
        and blobs(archive) == before and not leftovers(archive)
    expect("a re-pack stopped before its manifest is put in place: the old manifest; the new blob deleted", ok,
           "" if ok else out[-400:])
    code, out = interrupted("after", "pack", os.path.join(src, "t_A"), archive, "--name", "t/A", "--replace")
    ok = code != 0 and entry(archive, "t/A", "tokenizer_config.json")["sha256"] == new_tok \
        and blobs(archive) == before - {old_tok} | {new_tok}
    expect("stopped after: the new manifest; the old version's blob, which no model needs now, deleted", ok,
           "" if ok else out[-400:])
    code, out = tool("verify", archive)
    expect("  and every model verifies", code == 0 and "ALL VERIFIED" in out, "" if code == 0 else out[-400:])
    p = la.open_pack(archive)
    manifests = la.all_manifests(p)
    for name, original in originals.items():
        sha = next(e["sha256"] for e in manifests[name]["files"] if e["path"] == "model.safetensors")
        spans = [(0, len(original)), (0, 1), (len(original) - 3, 3), (12345, 4_000_000)]
        spans += [(int(s), int(n)) for s, n in zip(rng.integers(0, len(original), 6), rng.integers(1, 3_000_000, 6))]
        bad = [s for s in spans if la.read_range(p, sha, *s) != original[s[0]:s[0] + s[1]]]
        expect(f"read_range on {name} ({la.blob_info(p, sha)['method']})", not bad, f"wrong bytes for {bad}" if bad else "")


def check_rebase():
    """Packs a fine-tune F before its base P, as happens in practice: P then picks F as its base. rebase --all
    leaves that alone (F cannot use P while P is coded against F); once P is made independent with rebase --no-base,
    rebase --all moves F onto P. Every model (G is a delta against F) must stay restorable and nothing may be left in
    OC_TMP or the pack."""
    print("rebase")
    archive, src = os.path.join(scratch, "rebase_pack"), os.path.join(scratch, "rebase_models")
    tmp = os.path.join(scratch, "rebase_tmp")
    for folder in (archive, tmp):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(tmp)
    os.environ["OC_TMP"] = tmp  # inherited by the tool's processes
    cols = 1000
    p = (rng.standard_normal(2000 * cols) * 0.02).astype(np.float32)
    f = p + (rng.standard_normal(p.size) * 2e-3).astype(np.float32)  # a fine-tune of P
    g = f.copy()  # a light fine-tune of F
    touched = rng.random(g.size) < 0.05
    g[touched] += (rng.standard_normal(touched.sum()) * 1e-3).astype(np.float32)
    tokenizer = json.dumps({f"token_{i}": i for i in range(100_000)}).encode()
    originals = {}
    for name, w in (("t/F", f), ("t/P", p), ("t/G", g)):
        folder = os.path.join(src, name.replace("/", "_"))
        os.makedirs(folder, exist_ok=True)
        write_safetensors(os.path.join(folder, "model.safetensors"), {"w": ("BF16", [2000, cols], bf(w)), "n": ("F32", [64], p[:64].tobytes())})
        for rel, data in (("config.json", json.dumps({"name": name}).encode()), ("tokenizer.json", tokenizer)):
            with open(os.path.join(folder, rel), "wb") as fh:
                fh.write(data)
        originals[name] = files_of(folder)
        code, out = pack(folder, archive, name)
        if code:
            print(out)
    base = lambda name: base_of(archive, name)  # noqa: E731
    shared = len({entry(archive, n, "tokenizer.json")["sha256"] for n in originals}) == 1
    expect("packed in this order, F has no base and P and G are deltas against F; the tokenizer is stored once",
           (base("t/F"), base("t/P"), base("t/G")) == (None, "t/F", "t/F") and shared)

    before = state(archive)
    code, out = tool("rebase", archive, "--all")
    expect("rebase --all changes nothing yet (F cannot use P, which is coded against F)", code == 0 and state(archive) == before,
           out[-400:] if code else "")
    code, out = tool("rebase", archive, "t/P", "--no-base")
    expect("rebase t/P --no-base makes P independent, even though it grows", code == 0 and base("t/P") is None
           and "replaces its blob" in out, "" if code == 0 else out[-300:])
    f_before = stored(archive, "t/F")
    code, out = tool("rebase", archive, "--all")
    ok = code == 0 and base("t/F") == "t/P"
    expect("rebase --all now moves F onto P", ok, "" if ok else out[-400:])
    expect("F is smaller", stored(archive, "t/F") < f_before, f"{f_before} -> {stored(archive, 't/F')}")
    expect("P is skipped (set with --no-base), G keeps F", "skipping t/P" in out and "base is still t/F" in out
           and base("t/G") == "t/F" and "skipped:" not in out, out if "skipped:" in out else "")
    expect("nothing left in OC_TMP or in the pack", not os.listdir(tmp) and not leftovers(archive), os.listdir(tmp))

    code, out = tool("rebase", archive, "t/P", "--base", "t/F")
    expect("rebase t/P --base t/F leaves P as it is: F is coded against P now", code == 0 and "cannot be its base" in out
           and base("t/P") is None, out[-300:])
    before = state(archive)
    code, out = tool("rebase", archive, "t/F", "--no-base", "--dry-run")
    expect("--dry-run changes nothing", code == 0 and "would be re-packed without a base" in out and state(archive) == before)
    tool("rebase", archive, "t/P")  # nothing to change, but now recorded as chosen automatically
    code, out = tool("rebase", archive, "t/P", "--no-base")
    choice = models(archive)["t/P"].get("base_choice")
    expect("--no-base is recorded even when no file changes", code == 0 and choice == "none"
           and "skipping t/P" in tool("rebase", archive, "--all", "--dry-run")[1], choice)
    for argv, words in ((("rebase", archive), "give the models"), (("rebase", archive, "t/F", "--all"), "give the models"),
                        (("rebase", archive, "--all", "--base", "t/P"), "needs the models named"),
                        (("rebase", archive, "t/nobody"), "no model named")):
        code, out = tool(*argv)
        expect(f"rebase {' '.join(argv[2:])}: refused", code != 0 and words in out and clean(out), out[-200:])

    code, out = tool("verify", archive)
    ok = code == 0 and "ALL VERIFIED" in out
    expect("every model verifies", ok, "" if ok else out[-400:])
    for name, files in originals.items():
        dest = os.path.join(scratch, "rebase_restored", name.replace("/", "_"))
        shutil.rmtree(dest, ignore_errors=True)
        code, out = tool("restore", archive, name, dest)
        expect(f"restore {name} is byte-identical", code == 0 and same_tree(dest, files), out[-400:] if code else "")
    del os.environ["OC_TMP"]


def check_remove():
    """remove (whole models and single files), rename, and list: what is freed, what is kept for whom, and the tree
    the pack shows."""
    print("remove, rename, list")
    root = os.path.join(scratch, "remove")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    archive = os.path.join(root, "pack")
    w = weights(1_000_000, np.float32)
    text = b"a tokenizer line shared by both models\n" * 2000
    files = {}
    for name, v in (("t/Base", w), ("t/Tune", w + weights(w.size, np.float32) * np.float32(0.05)),
                    ("t/Other", weights(w.size, np.float32))):
        st = os.path.join(root, "st.safetensors")
        write_safetensors(st, {"w": ("BF16", [1000, 1000], bf(v))})
        files[name] = {"model.safetensors": open(st, "rb").read(), "tokenizer.json": text, f"{name[2:]}.txt": name.encode()}
        pack(make_folder(os.path.join(root, "src", name[2:]), files[name]), archive, name)
    expect("(setup) Tune is coded against Base", base_of(archive, "t/Tune") == "t/Base")

    code, out = tool("list", archive)
    lines = out.splitlines()
    names = [line.split("%", 1)[1] if "%" in line else "" for line in lines]  # what follows the ratio
    under = next((i for i, rest in enumerate(names) if rest.startswith("  t/Base")), None)
    ok = code == 0 and under is not None and names[under + 1].startswith("    t/Tune") and "3 model(s)" in out
    expect("list shows the models as a tree: Tune under Base", ok, "" if ok else out[-400:])
    code, out = tool("list", archive, "--json")
    try:
        summary = json.loads(out.splitlines()[0])
        by_name = {m["name"]: m for m in summary["models"]}
        ok = code == 0 and summary["event"] == "pack" and by_name["t/Tune"]["base"] == "t/Base" \
            and by_name["t/Base"]["needed_by"] == ["t/Tune"] and summary["unused"]["blobs"] == 0
    except (ValueError, KeyError, IndexError) as e:
        ok, out = False, f"{e}: {out}"
    expect("list --json: needs and needed by, per model", ok, "" if ok else out[-400:])
    code, out = tool("list", archive, "t/Tune")
    ok = code == 0 and "against t/Base" in out and "= also in t/Base, t/Other" in out
    expect("list <model>: each file with what it is coded against, and the models sharing it", ok, "" if ok else out[-400:])

    before = state(archive)
    code, out = tool("remove", archive, "t/Base", "--dry-run")
    ok = code == 0 and "would free" in out and "t/Tune" in out and state(archive) == before
    expect("remove --dry-run: says what would be freed and kept, changes nothing", ok, "" if ok else out[-300:])
    base_blob = entry(archive, "t/Base", "model.safetensors")["sha256"]
    own_text = entry(archive, "t/Base", "Base.txt")["sha256"]
    code, out = tool("remove", archive, "t/Base")
    left = blobs(archive)
    ok = code == 0 and "t/Base" not in models(archive) and base_blob in left and own_text not in left \
        and "still need" in out and "t/Tune" in out
    expect("remove t/Base: its own file goes, its weights stay for t/Tune", ok, "" if ok else out[-300:])
    code, out = tool("list", archive)
    expect("list shows t/Tune needing the removed t/Base, and what is kept for it",
           code == 0 and "t/Base (removed)" in out and "kept:" in out, out[-300:])
    code, out = tool("verify", archive)
    expect("everything left verifies", code == 0 and "ALL VERIFIED" in out, out[-300:])
    code, out = tool("rebase", archive, "t/Tune", "--no-base")
    ok = code == 0 and base_blob not in blobs(archive)
    expect("rebase t/Tune --no-base frees the kept blob", ok, "" if ok else out[-300:])

    code, out = tool("remove", archive, "t/Tune", "--file", "Tune.txt")
    ok = code == 0 and [e["path"] for e in models(archive)["t/Tune"]["files"]] == ["model.safetensors", "tokenizer.json"]
    expect("remove --file: one file of a model", ok, "" if ok else out[-300:])
    shared = entry(archive, "t/Tune", "tokenizer.json")["sha256"]
    code, out = tool("remove", archive, "t/Tune", "--file", "tokenizer.json", "--file", "model.safetensors")
    ok = code == 0 and "the model goes" in out and "t/Tune" not in models(archive) and shared in blobs(archive)
    expect("remove --file of all its files removes the model; t/Other keeps the tokenizer they shared", ok,
           "" if ok else out[-300:])
    for argv, words in ((("remove", archive, "t/Gone"), "no model named"),
                        (("remove", archive, "t/Other", "--file", "nope.txt"), "has no file"),
                        (("remove", archive, "t/Other", "t/Other2", "--file", "x"), "exactly one")):
        code, out = tool(*argv)
        expect(f"remove {' '.join(argv[2:])}: refused", code != 0 and words in out and clean(out), out[-200:])

    code, out = tool("rename", archive, "t/Other", "other/Renamed")
    ok = code == 0 and list(models(archive)) == ["other/Renamed"] and not os.path.exists(manifest_file(archive, "t/Other"))
    expect("rename moves the manifest and changes nothing else", ok, "" if ok else out[-300:])
    code, out = tool("rename", archive, "other/Renamed", "other/RENAMED")
    ok = code == 0 and list(models(archive)) == ["other/RENAMED"]
    expect("rename that changes only the case", ok, "" if ok else out[-300:])
    pack(make_folder(os.path.join(root, "src", "x"), {"x.txt": b"x"}), archive, "t/X")
    for argv, words in ((("rename", archive, "t/X", "other/renamed"), "taken"), (("rename", archive, "t/X", "t/X"), "already"),
                        (("rename", archive, "t/X", "a/../b"), "bad model name"), (("rename", archive, "t/Y", "t/Z"), "no model")):
        code, out = tool(*argv)
        expect(f"rename {' '.join(argv[2:])}: refused", code != 0 and words in out and clean(out), out[-200:])
    code, out = tool("restore", archive, "other/RENAMED", os.path.join(root, "renamed"))
    expect("the renamed model restores", code == 0 and same_tree(os.path.join(root, "renamed"), files["t/Other"]), out[-200:])

    print("a name the pack holds already: --replace, --add")
    marker = lambda: json.load(open(os.path.join(archive, la.MARKER)))  # noqa: E731
    stamp = marker().get("changed")
    more = make_folder(os.path.join(root, "src", "more"), {"extra.txt": b"EXTRA", "Other.txt": b"t/Other, again"})
    before = state(archive)
    code, out = pack(more, archive, "other/RENAMED")
    expect("without --replace or --add: refused before anything is written, saying both", code != 0 and "--replace" in out
           and "--add" in out and state(archive) == before and clean(out), out[-200:])
    code, out = pack(more, archive, "other/RENAMED", "--add", "--file", "extra.txt")
    paths = [e["path"] for e in models(archive)["other/RENAMED"]["files"]]
    expect("--add --file: the file joins the model, the others stay", code == 0
           and paths == ["Other.txt", "model.safetensors", "tokenizer.json", "extra.txt"], str(paths))
    code, out = pack(more, archive, "other/RENAMED", "--add", "--file", "Other.txt")
    dest = os.path.join(root, "added")
    code_r, _ = tool("restore", archive, "other/RENAMED", dest)
    ok = code == 0 and code_r == 0 and same_tree(dest, {**files["t/Other"], "Other.txt": b"t/Other, again", "extra.txt": b"EXTRA"})
    expect("--add of a path the model has: that file replaced, the rest restores as before", ok, out[-200:])
    code, out = pack(more, archive, "t/Nobody", "--add")
    expect("--add to a model the pack does not hold: refused", code != 0 and "no model named" in out, out[-200:])
    code, out = pack(more, os.path.join(root, "new.oczip"), "t/M", "--add")
    expect("--add into a new pack file: refused", code != 0 and "--add" in out and not os.path.exists(os.path.join(root, "new.oczip")),
           out[-200:])
    code, out = tool("rename", archive, "other/RENAMED", "--file", "extra.txt", "notes/extra.txt")
    paths = [e["path"] for e in models(archive)["other/RENAMED"]["files"]]
    expect("rename --file: a file of the model gets another path", code == 0 and "notes/extra.txt" in paths
           and "extra.txt" not in paths, out[-200:])
    for argv, words in ((("rename", archive, "other/RENAMED", "--file", "nope.txt", "x.txt"), "has no file"),
                        (("rename", archive, "other/RENAMED", "--file", "Other.txt", "notes/extra.txt"), "already"),
                        (("rename", archive, "other/RENAMED", "--file", "Other.txt", "../x.txt"), "unsafe")):
        code, out = tool(*argv)
        expect(f"rename --file {' '.join(argv[4:])}: refused", code != 0 and words in out and clean(out), out[-200:])
    expect("every change records itself in ocpack.json", marker().get("changed") and marker()["changed"] != stamp,
           str(marker()))
    code, out = tool("remove", archive, "t__X")
    ok = code == 0 and list(models(archive)) == ["other/RENAMED"]
    expect("a model can be given by its manifest's file name (t__X)", ok, "" if ok else out[-200:])


def check_damage():
    """verify in three passes, and its damage report: a damaged blob and the file coded against it, a missing blob, a
    model that only needs data of a damaged one (affected); storing the original again heals what is coded against
    it; giving up what is lost works in a damaged pack."""
    print("damage: the report, healing, giving up")
    root = os.path.join(scratch, "damage")
    shutil.rmtree(root, ignore_errors=True)
    cols = 500
    a = (rng.standard_normal(400 * cols) * 0.02).astype(np.float32)
    v = (rng.standard_normal(400 * cols) * 0.02).astype(np.float32)
    noise = lambda x: x + (rng.standard_normal(x.size) * 2e-3).astype(np.float32)  # noqa: E731
    family = {  # Tune is coded against Base's a.safetensors, Side against its b.safetensors only
        "t/Base": {"a.safetensors": {"w": ("BF16", [400, cols], bf(a))}, "b.safetensors": {"v": ("BF16", [400, cols], bf(v))},
                   "README.md": b"# base\n"},
        "t/Tune": {"a.safetensors": {"w": ("BF16", [400, cols], bf(noise(a)))}, "README.md": b"# tune\n"},
        "t/Side": {"v.safetensors": {"v": ("BF16", [400, cols], bf(noise(v)))}},
    }
    archive = os.path.join(root, "pack")
    for name, files in family.items():
        folder = make_folder(os.path.join(root, "src", name.split("/")[1]), {k: f for k, f in files.items() if isinstance(f, bytes)})
        os.makedirs(folder, exist_ok=True)
        for rel, tensors in files.items():
            if not isinstance(tensors, bytes):
                write_safetensors(os.path.join(folder, rel), tensors)
        pack(folder, archive, name)
    ok = base_of(archive, "t/Tune", "a.safetensors") == "t/Base" and base_of(archive, "t/Side", "v.safetensors") == "t/Base"
    expect("the family is packed as meant (Tune and Side coded against Base)", ok)
    intact = os.path.join(root, "intact")
    shutil.copytree(archive, intact)
    path = blob_path(archive, entry(archive, "t/Base", "a.safetensors")["sha256"])
    data = bytearray(open(path, "rb").read())
    data[len(data) // 2] ^= 0xFF
    open(path, "wb").write(bytes(data))
    damaged = os.path.join(root, "damaged")
    shutil.copytree(archive, damaged)
    code, out = tool("verify", archive)
    ok = (code != 0 and "FAIL  t/Base:a.safetensors  -- its blob is damaged" in out
          and "FAIL  t/Tune:a.safetensors  -- through t/Base: a.safetensors: its blob is damaged" in out
          and "ok    t/Side:v.safetensors" in out and "DAMAGE REPORT" in out and "heals 2 lost files" in out
          and "models that need data of those (1): t/Side" in out)
    expect("a damaged blob: its file and the file coded against it are lost, told apart; Side needs data of Base",
           ok, "" if ok else out[-900:])
    code, out = tool("verify", archive, "--json")
    try:
        report = next(json.loads(line) for line in out.splitlines() if line.startswith("{") and '"damage"' in line)
        lost = sorted((x["model"], x["path"], x["own"]) for x in report["lost"])
        ok = (lost == [("t/Base", "a.safetensors", True), ("t/Tune", "a.safetensors", False)]
              and report["affected"] == ["t/Side"] and report["heal"][0]["heals"] == 2 and len(report["faults"]) == 1)
    except (StopIteration, ValueError, KeyError) as e:
        ok, out = False, f"{e}: {out[-600:]}"
    expect("verify --json: the damage report as data (lost, own or not, affected, what heals)", ok, "" if ok else str(lost))
    code, out = tool("pack", os.path.join(root, "src", "Base"), archive, "--name", "t/Base", "--add", "--file", "a.safetensors")
    code2, out2 = tool("verify", archive)
    expect("storing the original again heals it and what is coded against it", code == 0 and code2 == 0
           and "ALL VERIFIED" in out2, "" if code == code2 == 0 else (out + out2)[-400:])
    os.remove(blob_path(intact, entry(intact, "t/Side", "v.safetensors")["sha256"]))
    code, out = tool("verify", intact)
    ok = code != 0 and "FAIL  t/Side:v.safetensors  -- its blob is missing from the pack" in out and "t/Base:" not in out.split(
        "DAMAGE REPORT")[1].split("to heal")[0].replace("t/Side", "")
    expect("a missing blob: only its file is lost", ok, "" if ok else out[-600:])
    code, out = tool("remove", damaged, "t/Tune", "--file", "a.safetensors")
    code2, out2 = tool("remove", damaged, "t/Base", "--file", "a.safetensors")
    code3, out3 = tool("verify", damaged)
    expect("giving up the lost files works in a damaged pack: it verifies again", code == code2 == code3 == 0
           and "ALL VERIFIED" in out3, "" if code == code2 == code3 == 0 else (out + out2 + out3)[-500:])
    gone = os.path.join(root, "gone")
    shutil.copytree(archive, gone)
    path = blob_path(gone, entry(gone, "t/Base", "a.safetensors")["sha256"])
    os.remove(path)
    code, out = tool("remove", gone, "t/Base", "t/Tune", "t/Side")
    expect("removing every model of a pack with a missing blob: nothing left over", code == 0 and blobs(gone) == set(),
           "" if code == 0 else out[-300:])


def check_kept_bases():
    """Bases kept for removed models: trimmed to what is coded against them (a partial blob), stored whole again when
    the original is packed, over-trimming found by verify; rebase lets a kept base go when that makes the pack
    smaller; the hint after a pack, and how much of a base a model reads."""
    print("kept bases: trimming, letting go, hints")
    root = os.path.join(scratch, "kept")
    shutil.rmtree(root, ignore_errors=True)
    w = (rng.standard_normal(1 << 19) * 0.02).astype(np.float32)
    v = (rng.standard_normal(1 << 18) * 0.02).astype(np.float32)
    tune = w + (rng.standard_normal(w.size) * 2e-3).astype(np.float32)
    src = {name: os.path.join(root, "src", name) for name in ("base", "tune", "base32")}
    for name, tensors in (("base", {"w": ("BF16", [w.size], bf(w)), "v": ("BF16", [v.size], bf(v))}),
                          ("tune", {"w": ("BF16", [w.size], bf(tune))}),
                          ("base32", {"w": ("F32", [w.size], w.tobytes())})):
        os.makedirs(src[name])
        write_safetensors(os.path.join(src[name], "model.safetensors"), tensors)
    archive = os.path.join(root, "pack")
    pack(src["base"], archive, "t/base")
    pack(src["tune"], archive, "t/tune")
    usage = la.model_summary(la.open_pack(archive), la.Graph(la.open_pack(archive), models(archive)), "t/tune")["usage"]
    la.close_pack_files()
    expect("usage: the tune reads the base's w, not its v (about 2/3 of it)", 0.6 < usage.get("t/base", 0) < 0.72, str(usage))
    sha = entry(archive, "t/base", "model.safetensors")["sha256"]
    path = blob_path(archive, sha)
    before = os.path.getsize(path)
    code, out = tool("remove", archive, "t/base")
    header = la.container_index((path, 0, os.path.getsize(path)))[0]
    la.forget_cached_reads()
    ok = (code == 0 and "trimmed" in out and header.get("partial") is True and os.path.getsize(path) < before * 0.8
          and sum(1 for r in header["regions"] if r.get("dropped")) >= 1)
    expect("removing the base keeps of it only what the tune is coded against (a partial blob)", ok, "" if ok else out[-300:])
    code, out = tool("verify", archive)
    expect("... the tune still verifies", code == 0 and "ALL VERIFIED" in out, "" if code == 0 else out[-300:])
    over = os.path.join(root, "over")
    shutil.copytree(archive, over)
    la.write_trimmed(la.FolderPack(over), sha, set())
    la.forget_cached_reads()
    code, out = tool("verify", over)
    expect("a base trimmed of what is coded against it: verify says so", code != 0 and "that is not kept" in out,
           "" if code else out[-300:])
    again = os.path.join(root, "again")
    shutil.copytree(archive, again)
    code, out = pack(src["base"], again, "t/base")
    header = la.container_index((blob_path(again, sha), 0, os.path.getsize(blob_path(again, sha))))[0]
    la.forget_cached_reads()
    code2, out2 = tool("verify", again)
    ok = code == 0 and "storing it again" in out and not header.get("partial") and code2 == 0
    expect("packing the base again stores it whole, never against the tune (that would go round)", ok,
           "" if ok else (out + out2)[-400:])
    total = lambda folder: sum(os.path.getsize(blob_path(folder, s)) for s in blobs(folder))  # noqa: E731
    was = total(archive)
    code, out = tool("rebase", archive, "t/tune")
    code2, out2 = tool("verify", archive)
    ok = code == 0 and "letting go of" in out and total(archive) < was and len(blobs(archive)) == 1 and code2 == 0
    expect("rebase lets the kept base go: the tune stored on its own, the pack smaller", ok,
           "" if ok else f"{was} -> {total(archive)}: " + (out + out2)[-400:])
    hinted = os.path.join(root, "hinted")
    pack(src["tune"], hinted, "t/tune")
    code, out = pack(src["base32"], hinted, "t/base32")
    ok = code == 0 and "may store smaller against t/base32: t/tune" in out
    expect("after a pack, the models that may store smaller against it are named", ok, "" if ok else out[-300:])


def check_rebase_files():
    """rebase --file: only the files named; a choice made then is theirs (--all and naming the model leave them,
    naming them again re-chooses); pack --add with --no-base: the choice is the added files', not the model's."""
    print("rebase of files")
    root = os.path.join(scratch, "rebase-files")
    shutil.rmtree(root, ignore_errors=True)
    w = (rng.standard_normal(1 << 18) * 0.02).astype(np.float32)
    v = (rng.standard_normal(1 << 18) * 0.02).astype(np.float32)
    near = lambda x: x + (rng.standard_normal(x.size) * 2e-3).astype(np.float32)  # noqa: E731
    src = {name: os.path.join(root, "src", name) for name in ("base", "tune")}
    for name, files in (("base", {"a.safetensors": w, "b.safetensors": v}),
                        ("tune", {"a.safetensors": near(w), "b.safetensors": near(v), "c.safetensors": near(v)})):
        os.makedirs(src[name])
        for rel, x in files.items():
            write_safetensors(os.path.join(src[name], rel), {rel[0]: ("BF16", [x.size], bf(x))})
    archive = os.path.join(root, "pack")
    pack(src["base"], archive, "t/base")
    pack(src["tune"], archive, "t/tune", "--exclude", "c.safetensors")
    expect("(setup) both files of the tune coded against the base", base_of(archive, "t/tune", "a.safetensors") == "t/base"
           and base_of(archive, "t/tune", "b.safetensors") == "t/base")
    code, out = tool("rebase", archive, "t/tune", "--no-base", "--file", "a.safetensors")
    m = models(archive)["t/tune"]
    ok = (code == 0 and base_of(archive, "t/tune", "a.safetensors") is None and base_of(archive, "t/tune", "b.safetensors") == "t/base"
          and entry(archive, "t/tune", "a.safetensors").get("base_choice") == "none" and m.get("base_choice", "auto") == "auto")
    expect("rebase --no-base --file: that file alone stored on its own, the choice its own (not the model's)", ok,
           "" if ok else out[-300:])
    for argv in (("--all",), ("t/tune",)):
        code, out = tool("rebase", archive, *argv)
        ok = code == 0 and "a.safetensors" in out and "set by hand" in out and base_of(archive, "t/tune", "a.safetensors") is None
        expect(f"rebase {argv[0]}: a file whose base was set by hand stays as it is", ok, "" if ok else out[-300:])
    code, out = tool("rebase", archive, "t/tune", "--file", "a.safetensors")  # (--all coded the base's a against it)
    ok = (code == 0 and "base_choice" not in entry(archive, "t/tune", "a.safetensors")
          and (base_of(archive, "t/tune", "a.safetensors") == "t/base" or "still no base" in out))
    expect("rebase --file of it: chosen automatically again", ok, "" if ok else out[-300:])
    for argv, words in ((("t/tune", "--file", "nope"), "has no file"), (("t/tune", "t/base", "--file", "a.safetensors"), "exactly one"),
                        (("--all", "--file", "a.safetensors"), "exactly one")):
        code, out = tool("rebase", archive, *argv)
        expect(f"rebase {' '.join(argv)}: refused", code != 0 and words in out, "" if code else out[-200:])
    code, out = tool("pack", src["tune"], archive, "--name", "t/tune", "--add", "--no-base", "--file", "c.safetensors")
    m = models(archive)["t/tune"]
    ok = (code == 0 and base_of(archive, "t/tune", "c.safetensors") is None and m.get("base_choice", "auto") == "auto"
          and entry(archive, "t/tune", "c.safetensors").get("base_choice") == "none")
    expect("pack --add --no-base: the choice is the added file's, the model's stays", ok, "" if ok else out[-300:])
    code, out = tool("verify", archive)
    expect("... and the pack verifies", code == 0, "" if code == 0 else out[-300:])


def check_output():
    """--json (every result line a JSON object, errors too) and --progress (on stderr, about once a second)."""
    print("output for programs")
    root = os.path.join(scratch, "output")
    shutil.rmtree(root, ignore_errors=True)
    code, out = tool("--version")
    ok = code == 0 and out.startswith("OverCompressing CLI 0.") and out.rstrip().endswith("format 4")
    expect("--version: the tool's version (and build, where it can tell) and the format it writes", ok, out[-200:])
    kit = os.path.join(scratch, "output-kit-tools")
    shutil.rmtree(kit, ignore_errors=True)
    os.makedirs(kit)
    shutil.copyfile(os.path.join(TOOL_DIR, "llm_archive.py"), os.path.join(kit, "llm_archive.py"))
    with open(os.path.join(kit, "BUILD"), "w", encoding="utf-8") as fh:
        json.dump({"llm_archive.py": "2026-01-02 abc1234"}, fh)
    run = subprocess.run([sys.executable, "-I", os.path.join(kit, "llm_archive.py"), "--version"], capture_output=True, text=True)
    ok = run.returncode == 0 and "(2026-01-02 abc1234)" in run.stdout
    expect("... in a kit, the build is read from the BUILD file beside the tools", ok, run.stdout + run.stderr)
    archive = os.path.join(root, "pack")
    st = os.path.join(root, "w.safetensors")
    os.makedirs(root)
    write_safetensors(st, {"w": ("BF16", [3000, 1000], bf16_bytes(3_000_000))})
    folder = make_folder(os.path.join(root, "src"), {"model.safetensors": open(st, "rb").read(), "a.txt": b"a"})
    run = subprocess.run([sys.executable, "-I", os.path.join(TOOL_DIR, "llm_archive.py"), "pack", folder, archive,
                          "--name", "t/J", "--json", "--progress"], capture_output=True, text=True,
                         env={**os.environ, "OC_WORKERS": "1"})
    try:
        events = [json.loads(line) for line in run.stdout.splitlines()]
        progress = [json.loads(line) for line in run.stderr.splitlines()]
        ok = run.returncode == 0 and {e["event"] for e in events} >= {"note", "file", "model"} \
            and all("text" in e for e in events) and all(p["event"] == "progress" for p in progress)
        detail = f"{len(progress)} progress line(s)"
    except ValueError as e:
        ok, detail = False, f"{e}: {run.stdout[-200:]} {run.stderr[-200:]}"
    expect("pack --json: every line on stdout is a JSON object, every line on stderr a progress object", ok, detail)
    code, out = tool("verify", os.path.join(root, "nowhere"), "--json")
    try:
        last = json.loads(out.strip().splitlines()[-1])
        ok = code == 1 and last["event"] == "error" and "nowhere" in last["text"]
    except (ValueError, IndexError, KeyError):
        ok = False
    expect("an error with --json: {\"event\": \"error\"} and exit code 1", ok, out[-200:])
    for value in ("0", "-1", "1.5", "words", "\u00b2", "\u0663", " ", "9" * 5000):  # ² and ٣ count as digits for Python
        run = subprocess.run([sys.executable, "-I", os.path.join(TOOL_DIR, "llm_archive.py"), "list", archive, "--json"],
                             capture_output=True, text=True, env={**os.environ, "OC_WORKERS": value})
        try:
            events = [json.loads(line) for line in run.stdout.splitlines()]
            ok = run.returncode == 1 and len(events) == 1 and events[0]["event"] == "error" \
                and "OC_WORKERS" in events[0]["text"] and "Traceback" not in run.stderr
        except (ValueError, KeyError):
            ok = False
        shown = ascii(value) if len(value) < 80 else "<5000 digits>"  # ascii: a console's code page may lack ²
        expect(f"OC_WORKERS={shown}: refused, a JSON error without a traceback", ok, (run.stdout + run.stderr)[-200:])
    try:
        with la.worker_pool(100) as pool:
            ok = pool.submit(abs, -7).result() == 7
    except ValueError:  # Windows: more than 61
        ok = False
    expect("a pool of 100 worker processes runs (on Windows: 61, the most Python's pool takes there)", ok)
    code, out = tool("verify", archive, "--progress")
    expect("verify --progress: the results as without it", code == 0 and "ALL VERIFIED" in out, out[-200:])


def check_safety():
    """The cases of the external reviews (2026-10-20 and after), in this format: each must be refused or handled
    without losing data."""
    root = os.path.join(scratch, "safety")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)

    print("safety: re-packing a model that others need")
    archive = os.path.join(root, "a1")
    text = b"a line of a tokenizer file shared by several models\n" * 40_000
    w = weights(500_000, np.float32)
    a = os.path.join(root, "src", "A")
    st = os.path.join(root, "tmp.safetensors")
    write_safetensors(st, {"w": ("BF16", [500, 1000], bf(w))})
    a_files = {"data.txt": text, "model.safetensors": open(st, "rb").read()}
    pack(make_folder(a, a_files), archive, "t/A")
    write_safetensors(st, {"w": ("BF16", [500, 1000], bf(w + np.float32(1e-4)))})
    b_files = {"data.txt": text, "model.safetensors": open(st, "rb").read()}
    pack(make_folder(os.path.join(root, "src", "B"), b_files), archive, "t/B")
    expect("t/B shares t/A's data.txt and is a delta against t/A",
           entry(archive, "t/B", "data.txt")["sha256"] == entry(archive, "t/A", "data.txt")["sha256"]
           and base_of(archive, "t/B") == "t/A")
    write_safetensors(st, {"w": ("BF16", [500, 1000], bf(w * np.float32(1.5)))})
    code, out = pack(make_folder(a, {"data.txt": text + b"changed\n", "model.safetensors": open(st, "rb").read()}), archive, "t/A",
                     "--replace")
    code_v, out_v = tool("verify", archive)
    expect("t/A re-packed with other files, though t/B used them: allowed, and both verify", code == code_v == 0
           and clean(out), (out + out_v)[-300:])
    dest = os.path.join(root, "r1")
    code, out = tool("restore", archive, "t/B", dest)
    expect("  t/B restores as it was (what it needs is kept)", code == 0 and same_tree(dest, b_files), out[-200:])
    code, out = tool("pack", a, archive, "--name", "t/A", "--exclude", "data.txt", "--replace")
    expect("  t/A re-packed without data.txt (--exclude): t/B keeps its copy", code == 0
           and entry(archive, "t/B", "data.txt")["sha256"] in blobs(archive), out[-200:])

    print("safety: file names a model folder used to need for itself")
    archive = os.path.join(root, "a2")
    files = {"manifest.json": b'{"own": "manifest"}', "manifest.json.part": b"part", "a": b"a", "a.part": b"a.part",
             "x": rng.integers(0, 256, 1_500_000, dtype=np.uint8).tobytes(), "x.ocz": b"not a container",
             "models/m.json": b"{}", "blobs/b": b"b"}  # (an ocpack.json would make it a pack folder: not packed)
    src = make_folder(os.path.join(root, "src", "names"), files)
    for attempt in ("pack", "re-pack"):
        code, out = pack(src, archive, "t/N", *(["--replace"] if attempt == "re-pack" else []))
        code_v, out_v = tool("verify", archive)
        dest = os.path.join(root, f"r2-{attempt}")
        code_r, out_r = tool("restore", archive, "t/N", dest)
        expect(f"{attempt}: manifest.json, models/, blobs/, *.part and x/x.ocz all survive",
               code == code_v == code_r == 0 and same_tree(dest, files), (out + out_v + out_r)[-300:])

    print("safety: model names")
    archive = os.path.join(root, "a4", "pack")
    src = make_folder(os.path.join(root, "src", "small"), {"one.txt": b"first model"})
    for bad in ("..", ".", "a\\b", "a/../b", "CON", "a//b", " lead", "x:y", "a/"):
        code, out = pack(src, archive, bad)
        expect(f"--name {bad!r} is refused", code != 0 and clean(out) and not os.path.exists(os.path.join(archive, "models")))
    code, out = pack(src, archive, "_kit")
    expect("--name _kit is a name like any other", code == 0 and "_kit" in models(archive), out[-200:])
    code, out = tool("pack", make_folder(os.path.join(root, "src", "bad,name"), {"f": b"f"}), archive)
    expect("a folder name that is not a valid model name needs --name", code != 0 and "--name" in out and clean(out))
    # a file name another system cannot hold, e.g. "a\b.txt" on Linux: restored as it is where the system allows it,
    # else with "_" for the character and " (1)" if that name is already used
    names = os.path.join(root, "a4u")
    if os.name == "nt":  # simulated: no Windows file name holds "\", so a patched listing calls a/b.txt that
        folder = make_folder(os.path.join(root, "src", "odd-names"), {"a/b.txt": b"B", "a_b.txt": b"U", "ok.txt": b"ok"})
        code, out = patched('la.source_files = lambda source, patterns: (["a\\\\b.txt", "a_b.txt", "ok.txt"], [])',
                            "pack", folder, names, "--name", "t/U")
        want = {"a_b.txt": b"U", "a_b (1).txt": b"B", "ok.txt": b"ok"}
    else:  # a real one: "\" is an ordinary character in a Linux file name
        folder = make_folder(os.path.join(root, "src", "odd-names"), {"a\\b.txt": b"B", "a_b.txt": b"U", "ok.txt": b"ok"})
        code, out = pack(folder, names, "t/U")
        want = {"a\\b.txt": b"B", "a_b.txt": b"U", "ok.txt": b"ok"}
    code_v, _ = tool("verify", names)
    code_r, out_r = tool("restore", names, "t/U", os.path.join(root, "r4u"))
    expect('a file named a\\b.txt packs and restores (on Windows as "a_b (1).txt")',
           code == code_v == code_r == 0 and clean(out + out_r) and same_tree(os.path.join(root, "r4u"), want), out[-200:])
    code, out = pack(src, archive, "A/B")
    expect("(setup) A/B packs", code == 0, out[-200:] if code else "")
    for clash in ("A__B", "a/b"):
        code, out = pack(make_folder(os.path.join(root, "src", "other"), {"two.txt": b"second model"}), archive, clash)
        code_r, _ = tool("restore", archive, "A/B", os.path.join(root, f"r4-{clash.replace('/', '_')}"))
        expect(f"{clash} cannot take over the manifest file of A/B", code != 0 and "taken" in out and code_r == 0
               and same_tree(os.path.join(root, f"r4-{clash.replace('/', '_')}"), {"one.txt": b"first model"}), out[-200:])
    stray = make_folder(os.path.join(root, "a4s"), {"notes.txt": b"mine"})
    code, out = pack(src, stray, "t/S")
    expect("a folder that holds other files and no ocpack.json is not packed into", code != 0 and clean(out)
           and "not a pack folder" in out and same_tree(stray, {"notes.txt": b"mine"}))
    kit_only = make_folder(os.path.join(root, "a4k"), {"_kit/README.md": b"kit"})
    code, out = pack(src, kit_only, "t/S")
    expect("... but one holding only _kit is", code == 0 and "t/S" in models(kit_only), out[-200:])

    print("safety: paths in a manifest")
    archive = os.path.join(root, "a3")
    files = {"note.txt": b"hello", "sub/f.txt": b"in a subfolder"}
    pack(make_folder(os.path.join(root, "src", "paths"), files), archive, "t/R")
    original = open(manifest_file(archive, "t/R"), encoding="utf-8").read()
    reset = lambda: open(manifest_file(archive, "t/R"), "w", encoding="utf-8").write(original)  # noqa: E731
    for odd in ("a\\..\\..\\x.txt", "C:/x.txt", "x.txt:s", "CON.txt", "name. "):  # names, not escapes
        reset()
        edit_manifest(archive, "t/R", lambda m: m["files"][0].update(path=odd))
        shutil.rmtree(os.path.join(root, "r3m"), ignore_errors=True)
        dest = os.path.join(root, "r3m", "dest")
        code, out = tool("restore", archive, "t/R", dest)
        where = os.path.join(dest, *la.local_parts(odd))
        expect(f"path {odd!r} restores inside the destination as {'/'.join(la.local_parts(odd))!r}",
               code == 0 and clean(out) and os.path.isfile(where) and open(where, "rb").read() == b"hello"
               and os.listdir(os.path.join(root, "r3m")) == ["dest"], out[-200:] if code else "")
    for bad in ("../outside.txt", "/abs.txt", "sub/../../outside.txt"):
        reset()
        edit_manifest(archive, "t/R", lambda m: m["files"][0].update(path=bad))
        dest = os.path.join(root, "r3", "dest")
        code, out = tool("restore", archive, "t/R", dest)
        code_v, out_v = tool("verify", archive)
        expect(f"path {bad!r}: restore refused, verify reports it", code != 0 and clean(out) and not os.path.exists(os.path.join(root, "r3"))
               and code_v != 0 and "FAIL" in out_v and clean(out_v))
    reset()
    edit_manifest(archive, "t/R", lambda m: m["files"][0].update(sha256="ab" * 32))
    code, out = tool("verify", archive)
    expect("a file whose blob is not in the pack is a FAIL line", code != 0 and "missing" in out and clean(out), out[-200:])
    reset()
    dest, outside = os.path.join(root, "r3s", "dest"), os.path.join(root, "r3s", "outside")
    os.makedirs(dest)
    os.makedirs(outside)
    if not link_dir(os.path.join(dest, "sub"), outside):
        print("  skip symlinked folder in the destination (no symlinks or junctions here)")
    else:
        code, out = tool("restore", archive, "t/R", dest)
        expect("restore does not follow a symlinked folder out of the destination", code != 0 and os.listdir(outside) == [] and clean(out))
    alias = os.path.join(root, "a3l")
    pack(make_folder(os.path.join(root, "src", "alias"), {"one/f.txt": b"ONE", "two/f.txt": b"TWO"}), alias, "t/L")
    dest = os.path.join(root, "r3l")
    os.makedirs(os.path.join(dest, "one"))
    if not link_dir(os.path.join(dest, "two"), os.path.join(dest, "one")):
        print("  skip linked folder inside the destination (no symlinks or junctions here)")
    else:
        code, out = tool("restore", alias, "t/L", dest)  # restore never writes through a link in the destination
        expect("a link inside the destination (two -> one) is refused before anything is written",
               code != 0 and clean(out) and "link" in out and os.listdir(os.path.join(dest, "one")) == [])
    if os.name == "nt":
        edit_manifest(archive, "t/R", lambda m: m["files"][1].update(path="NOTE.txt"))
        code, out = tool("restore", archive, "t/R", os.path.join(root, "r3c"))
        expect("note.txt and NOTE.txt, one file on Windows: the second becomes NOTE (1).txt",
               code == 0 and same_tree(os.path.join(root, "r3c"), {"note.txt": b"hello", "NOTE (1).txt": b"in a subfolder"}))
        reset()

    print("safety: restore over existing files")
    dest = os.path.join(root, "r5")
    os.makedirs(dest)
    open(os.path.join(dest, "note.txt"), "wb").write(b"KEEP")
    code, out = tool("restore", archive, "t/R", dest)
    expect("a different existing file stops restore before anything is written",
           code != 0 and open(os.path.join(dest, "note.txt"), "rb").read() == b"KEEP" and not os.path.exists(os.path.join(dest, "sub")))
    edit_manifest(archive, "t/R", lambda m: m["files"][0].update(sha256="0" * 64))
    code, out = tool("restore", archive, "t/R", dest, "--overwrite")
    expect("a failed restore keeps the existing file and leaves no temporary file",
           code != 0 and open(os.path.join(dest, "note.txt"), "rb").read() == b"KEEP" and not leftovers(dest) and clean(out))
    reset()
    code, out = tool("restore", archive, "t/R", dest, "--overwrite")
    code2, out2 = tool("restore", archive, "t/R", dest)
    expect("--overwrite replaces it; a second restore keeps the files already right",
           code == code2 == 0 and same_tree(dest, files) and "already there" in out2)

    print("safety: verify keeps going")
    archive = os.path.join(root, "a6")
    pack(make_folder(os.path.join(root, "src", "v"), {"a.txt": b"first", "b.txt": b"second"}), archive, "t/V")
    pack(make_folder(os.path.join(root, "src", "w"), {"c.txt": b"third"}), archive, "t/W")
    os.remove(blob_path(archive, entry(archive, "t/V", "a.txt")["sha256"]))
    code, out = tool("verify", archive)
    expect("a missing blob is a FAIL line; the other files are still checked",
           code != 0 and clean(out) and "missing" in out and "ok    t/V:b.txt" in out and "ok    t/W:c.txt" in out)
    open(manifest_file(archive, "t/W"), "w").write("{ not json")
    code, out = tool("verify", archive)
    expect("an unreadable manifest is a FAIL line too", code != 0 and clean(out) and "t__W" in out and "t/V:b.txt" in out)
    for argv in (("pack", os.path.join(root, "src", "small"), archive, "--name", "t/X"), ("remove", archive, "t/V"),
                 ("rebase", archive, "--all"), ("rename", archive, "t/V", "t/V2")):
        code, out = tool(*argv)
        expect(f"{argv[0]} refuses to change a pack with an unreadable manifest", code != 0 and clean(out) and "t__W" in out)
    os.makedirs(os.path.join(root, "a6-empty"))
    open(os.path.join(root, "a6-file"), "w").write("not a pack")
    old = make_folder(os.path.join(root, "a6-old"), {"t__A/manifest.json": b"{}"})
    for what, where, words in (("a missing pack", os.path.join(root, "a6-missing"), "not a pack folder"),
                               ("an empty folder", os.path.join(root, "a6-empty"), "no ocpack.json"),
                               ("a file", os.path.join(root, "a6-file"), "not a pack folder"),
                               ("a pack of the earlier format", old, "no ocpack.json")):
        code, out = tool("verify", where)
        code_r, out_r = tool("rebase", where, "--all")
        expect(f"verify and rebase --all of {what} fail, saying why", code != 0 and code_r != 0 and clean(out + out_r)
               and words in out and "ALL VERIFIED" not in out, out[-200:])

    print("safety: options and empty files")
    archive = os.path.join(root, "a7")
    src = make_folder(os.path.join(root, "src", "empty"), {"empty.txt": b"", "sub/also-empty": b""})
    code, out = pack(src, archive, "t/E")
    code_v, _ = tool("verify", archive)
    code_r, _ = tool("restore", archive, "t/E", os.path.join(root, "r7"))
    expect("a folder of empty files packs, verifies and restores", code == code_v == code_r == 0 and clean(out)
           and same_tree(os.path.join(root, "r7"), {"empty.txt": b"", "sub/also-empty": b""}))
    for option in (["--chunk-mib", "0"], ["--chunk-mib", "-1"], ["--workers", "0"], ["--zstd-level", "23"], ["--zstd-tensor-level", "x"]):
        code, out = pack(os.path.join(root, "src", "small"), archive, "t/O", *option)
        expect(f"{' '.join(option)} is refused before anything is written", code == 2 and clean(out)
               and not os.path.exists(manifest_file(archive, "t/O")))
    for value in ("abc", "0", "-3"):
        code, out = tool("verify", archive, env={"OC_WORKERS": value})
        expect(f"OC_WORKERS={value} gives a clear error", code != 0 and clean(out) and "OC_WORKERS" in out)

    print("safety: malformed safetensors headers are stored as plain files")
    archive = os.path.join(root, "a8")
    bad = {
        "list.safetensors": struct.pack("<Q", 2) + b"[]",
        "huge.safetensors": struct.pack("<Q", 1 << 62) + b"{}",
        "past-end.safetensors": struct.pack("<Q", 64) + b"{}",
        "offsets.safetensors": (lambda h: struct.pack("<Q", len(h)) + h + bytes(8))(
            json.dumps({"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 4096]}}).encode()),
        "negative.safetensors": (lambda h: struct.pack("<Q", len(h)) + h + bytes(8))(
            json.dumps({"w": {"dtype": "F32", "shape": [2], "data_offsets": [-8, 8]}}).encode()),
        "shape.safetensors": (lambda h: struct.pack("<Q", len(h)) + h + bytes(8))(
            json.dumps({"w": {"dtype": "F32", "shape": "2", "data_offsets": [0, 8]}}).encode()),
    }
    code, out = pack(make_folder(os.path.join(root, "src", "bad"), bad), archive, "t/H")
    code_v, out_v = tool("verify", archive)
    methods = {name: info(archive, "t/H", name)["method"] for name in bad} if code == 0 else {}
    expect("each packs as a plain file and verifies", code == code_v == 0 and clean(out)
           and all(m == "auto" for m in methods.values()) and all("tensors" not in e for e in models(archive)["t/H"]["files"]),
           str(methods) if code == 0 else out[-300:])


def check_accidents():
    """Cases of the extended review (2026-10-20, round 6), in this format: sources that change or are not plain
    files, two programs on one pack, files of your own in the pack folder, damaged blobs, malformed manifests."""
    root = os.path.join(scratch, "accidents")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)

    print("accidents: the source changes after it was hashed")
    archive, src = os.path.join(root, "c1"), os.path.join(root, "src1")
    make_folder(src, {"x.txt": b"OLD", "y.txt": b"Y0"})
    pack(src, archive, "t/C")
    x, y = os.path.join(src, "x.txt"), os.path.join(src, "y.txt")
    change_x = f"""_inner = la.blob_problem
def _after_hashing(*a, **k):
    open({x!r}, "wb").write(b"CHANGED AFTER HASHING")
    return _inner(*a, **k)
la.blob_problem = _after_hashing"""
    code, out = patched(change_x, "pack", src, archive, "--name", "t/C", "--replace")
    code_r, _ = tool("restore", archive, "t/C", os.path.join(root, "r1"))
    expect("a file stored already is not read again: the hashed version stays in the pack",
           code == 0 and code_r == 0 and same_tree(os.path.join(root, "r1"), {"x.txt": b"OLD", "y.txt": b"Y0"}), out[-200:])
    make_folder(src, {"x.txt": b"OLD", "y.txt": b"Y1"})
    change_y = f"""_inner = la.write_container
def _changed(src_path, *a, **k):
    open(src_path, "wb").write(b"CHANGED AFTER HASHING")
    return _inner(src_path, *a, **k)
la.write_container = _changed"""
    code, out = patched(change_y, "pack", src, archive, "--name", "t/C", "--replace")
    code_v, _ = tool("verify", archive)
    code_r, _ = tool("restore", archive, "t/C", os.path.join(root, "r1b"))
    expect("a file that changes while it is packed: VERIFY FAILED, the old version intact, nothing left over",
           code != 0 and "VERIFY FAILED" in out and code_v == code_r == 0 and not leftovers(archive)
           and same_tree(os.path.join(root, "r1b"), {"x.txt": b"OLD", "y.txt": b"Y0"}), out[-300:])

    print("accidents: two programs on one pack")
    archive = os.path.join(root, "c2")
    pack(make_folder(os.path.join(root, "src2"), {"a.txt": b"AAA"}), archive, "t/L")
    make_folder(os.path.join(root, "src2a"), {"a.txt": b"AAA2"})
    ready, go = os.path.join(root, "a-ready"), os.path.join(root, "a-go")
    first = patched_process(pause_in("write_manifest", ready, go), "pack", os.path.join(root, "src2a"), archive, "--name", "t/L",
                            "--replace")
    if not wait_for(ready):
        first.kill()
        expect("(setup) the first pack reaches its manifest", False, first.communicate()[0][-300:])
    else:
        code_b, out_b = pack(make_folder(os.path.join(root, "src2b"), {"a.txt": b"BBB"}), archive, "t/L", "--replace")
        code_o, out_o = pack(os.path.join(root, "src2b"), archive, "t/Other")
        code_v, out_v = tool("verify", archive)
        code_m, out_m = tool("remove", archive, "t/L")
        open(go, "w").close()
        out_a = first.communicate(timeout=120)[0]
        code_v2, _ = tool("verify", archive)
        code_r, _ = tool("restore", archive, "t/L", os.path.join(root, "r2"))
        expect("while a pack runs, a second pack (same or other model), a remove and a verify stop at once",
               code_b != 0 and code_o != 0 and code_v != 0 and code_m != 0 and "in use" in out_b and "being changed" in out_v
               and clean(out_b + out_v + out_m))
        expect("  and the first pack finishes; the pack verifies and holds its version",
               first.returncode == 0 and code_v2 == code_r == 0 and same_tree(os.path.join(root, "r2"), {"a.txt": b"AAA2"}),
               out_a[-200:] if first.returncode else "")
    ready2, go2 = os.path.join(root, "v-ready"), os.path.join(root, "v-go")
    reader = patched_process(pause_in("blob_damage", ready2, go2), "verify", archive)
    if wait_for(ready2):
        code_v, out_v = tool("verify", archive)
        code_p, out_p = pack(os.path.join(root, "src2b"), archive, "t/Other")
        open(go2, "w").close()
        reader.communicate(timeout=120)
        expect("two verifies run together, and a pack meanwhile stops at once", code_v == 0 and reader.returncode == 0
               and code_p != 0 and "in use" in out_p)
    else:
        reader.kill()
        expect("(setup) the first verify starts", False)

    print("accidents: files of your own in the pack folder")
    archive, src = os.path.join(root, "c3"), os.path.join(root, "src3")
    pack(make_folder(src, {"a.txt": b"A"}), archive, "t/U")
    own = {"notes.txt": b"KEEP USER NOTE", "extra/readme.txt": b"MINE", "blobs/readme.txt": b"MINE TOO",
           "models/readme.txt": b"AND THIS"}
    for rel, data in own.items():
        os.makedirs(os.path.dirname(os.path.join(archive, *rel.split("/"))), exist_ok=True)
        open(os.path.join(archive, *rel.split("/")), "wb").write(data)
    code, out = pack(make_folder(src, {"a.txt": b"A2"}), archive, "t/U", "--replace")
    code_m, out_m = tool("remove", archive, "t/U")
    kept = all(open(os.path.join(archive, *rel.split("/")), "rb").read() == data for rel, data in own.items())
    expect("your own files in a pack folder are never touched by pack or remove", code == code_m == 0 and kept
           and not blobs(archive), (out + out_m)[-200:])

    print("accidents: restore into the pack")
    archive = os.path.join(root, "c4")
    pack(make_folder(os.path.join(root, "src4"), {"w.txt": b"w"}), archive, "t/M")
    for where in (archive, os.path.join(archive, "models"), os.path.join(archive, "blobs", "x")):
        code, out = tool("restore", archive, "t/M", where, "--overwrite")
        code_v, _ = tool("verify", archive)
        ok = code != 0 and code_v == 0 and "refusing to restore there" in out and clean(out)
        expect(f"restore into {os.path.relpath(where, root)} is refused even with --overwrite", ok, "" if ok else out[-200:])

    print("accidents: a file in the pack is damaged")
    archive = os.path.join(root, "c5")
    big = rng.integers(0, 256, 1_500_000, dtype=np.uint8).tobytes()
    pack(make_folder(os.path.join(root, "src5a"), {"big.bin": big}), archive, "t/A")
    path = blob_path(archive, entry(archive, "t/A", "big.bin")["sha256"])
    damaged = bytearray(open(path, "rb").read())
    damaged[len(damaged) // 2] ^= 0x10
    open(path, "wb").write(damaged)
    code_v, out_v = tool("verify", archive)
    expect("verify finds it (the blob's own SHA-256)", code_v != 0 and "damaged" in out_v and clean(out_v), out_v[-200:])
    code, out = pack(make_folder(os.path.join(root, "src5b"), {"big.bin": big}), archive, "t/B")
    code_v, out_v = tool("verify", archive)
    expect("packing the same file again stores it again, with a warning: both models verify", code == 0 and "damaged" in out
           and code_v == 0, (out + out_v)[-300:])

    print("accidents: sources that are not plain files")
    archive = os.path.join(root, "c6")
    outside = make_folder(os.path.join(root, "outside6"), {"weights.txt": b"elsewhere"})
    src = make_folder(os.path.join(root, "src6"), {"ordinary.txt": b"o"})
    if link_dir(os.path.join(src, "linked"), outside):
        code, out = pack(src, archive, "t/K")
        code_x, out_x = pack(src, archive, "t/K", "--exclude", "linked")
        paths = [e["path"] for e in models(archive)["t/K"]["files"]] if code_x == 0 else []
        expect("a link to a folder stops pack, listed; --exclude skips it", code != 0 and "linked/" in out and "--exclude" in out
               and clean(out) and paths == ["ordinary.txt"])
    else:
        print("  skip link to a folder (no symlinks or junctions here)")
    if hasattr(os, "mkfifo"):
        fifo_src = make_folder(os.path.join(root, "src6f"), {"ok.txt": b"ok"})
        os.mkfifo(os.path.join(fifo_src, "pipe"))
        try:
            code, out = pack(fifo_src, archive, "t/F", timeout=60)
            expect("a FIFO stops pack at once instead of hanging", code != 0 and "pipe" in out and clean(out))
        except subprocess.TimeoutExpired:
            expect("a FIFO stops pack at once instead of hanging", False, "pack hung")
    try:
        broken_src = make_folder(os.path.join(root, "src6b"), {"ok.txt": b"ok"})
        os.symlink(os.path.join(broken_src, "missing.bin"), os.path.join(broken_src, "broken.bin"))
    except OSError:
        print("  skip broken link (no symlinks here)")
    else:
        code, out = pack(broken_src, archive, "t/G")
        expect("a broken link stops pack, listed", code != 0 and "broken.bin" in out and clean(out))

    print("accidents: malformed GGUF and manifests")
    archive = os.path.join(root, "c8")
    key = b"general.alignment"
    gguf = b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", len(key)) + key + struct.pack("<II", 4, 0)
    code, out = pack(make_folder(os.path.join(root, "src8"), {"zero-alignment.gguf": gguf}), archive, "t/Z")
    code_v, _ = tool("verify", archive)
    expect("a GGUF with alignment 0 is stored as a plain file", code == code_v == 0 and clean(out))
    archive = os.path.join(root, "c9")
    pack(make_folder(os.path.join(root, "src9a"), {"a.txt": b"a", "b.txt": b"b"}), archive, "t/S")
    pack(make_folder(os.path.join(root, "src9b"), {"c.txt": b"c"}), archive, "t/T")
    path = manifest_file(archive, "t/S")
    original = open(path, encoding="utf-8").read()
    variants = {
        "no size": lambda m: m["files"][0].pop("size"),
        "no sha256": lambda m: m["files"][0].pop("sha256"),
        "a sha256 in capitals": lambda m: m["files"][0].update(sha256=m["files"][0]["sha256"].upper()),
        "a path twice": lambda m: m["files"].append(dict(m["files"][0])),
        "negative size": lambda m: m["files"][0].update(size=-1),
        "files is null": lambda m: m.update(files=None),
        "an entry is a list": lambda m: m["files"].__setitem__(0, ["a.txt"]),
        "size is text": lambda m: m["files"][0].update(size="3"),
        "path is a number": lambda m: m["files"][0].update(path=7),
        "name is a list": lambda m: m.update(name=["t/S"]),
        "another model's name": lambda m: m.update(name="t/Q"),
        "an unknown format": lambda m: m["files"][0].update(format="pickle", tensors=[]),
        "a tensor past the end": lambda m: m["files"][0].update(format="safetensors", tensors=[["w", "F32", 0, 99, [1]]]),
        "tensors as an object": lambda m: m["files"][0].update(format="gguf", tensors={"w": 1}),
        "format version 3": lambda m: m.update(format_version=3),
        "an unknown base_choice": lambda m: m.update(base_choice="sometimes"),
    }
    for what, change in variants.items():
        open(path, "w", encoding="utf-8").write(original)
        edit_manifest(archive, "t/S", change)
        code, out = tool("verify", archive)
        expect(f"manifest with {what}: a FAIL line, the other model is still checked", code != 0 and clean(out)
               and "FAIL" in out and "ok    t/T:c.txt" in out, out[-200:])
    open(path, "w", encoding="utf-8").write(original)

    print("accidents: when the pack folder cannot be locked")
    archive = os.path.join(root, "c10")
    pack(make_folder(os.path.join(root, "src10"), {"r.txt": b"R1"}), archive, "t/R")
    lock = os.path.join(archive, la.LOCK_NAME)
    ready, go = os.path.join(root, "w-ready"), os.path.join(root, "w-go")
    writer = patched_process(pause_in("write_manifest", ready, go), "pack",
                             make_folder(os.path.join(root, "src10"), {"r.txt": b"R2"}), archive, "--name", "t/R", "--replace")
    if wait_for(ready):
        os.chmod(lock, 0o444)  # a lock file readers cannot open for writing (on Windows: read-only)
        code_v, out_v = tool("verify", archive)
        code_r, out_r = tool("restore", archive, "t/R", os.path.join(root, "r10"))
        os.chmod(lock, 0o644)
        open(go, "w").close()
        writer.communicate(timeout=120)
        expect("a read-only lock file still keeps verify and restore out while a pack runs", code_v != 0 and code_r != 0
               and "being changed" in out_v + out_r and not os.path.exists(os.path.join(root, "r10")))
    else:
        writer.kill()
        expect("(setup) the pack reaches its manifest", False)
    no_lock_file = """import os as _os
_open = _os.open
def _refuse(path, *a, **k):
    if str(path).endswith(la.LOCK_NAME):
        raise PermissionError(13, "Permission denied", path)
    return _open(path, *a, **k)
_os.open = _refuse
la.read_only_volume = lambda path: {read_only}"""
    for read_only, options, ok, what in ((False, [], False, "on a writable volume, a reader that cannot lock stops and names --no-lock"),
                                        (False, ["--no-lock"], True, "  --no-lock lets it go on, and says so"),
                                        (True, [], True, "on a read-only volume it goes on without a lock")):
        out = ""
        for argv in (["verify", archive], ["restore", archive, "t/R", os.path.join(root, f"r10-{read_only}-{len(options)}")],
                     ["list", archive]):
            run = patched_process(no_lock_file.format(read_only=read_only), *argv, *options)
            out_run = run.communicate(timeout=120)[0]
            out += out_run
            ok_run = (run.returncode == 0) == ok and clean(out_run)
            if not ok_run:
                break
        expect(what, ok_run and ("--no-lock" in out if not ok else True) and ("not locking" in out if options else True),
               out[-200:] if not ok_run else "")
    expect("this test machine's scratch folder is not reported read-only", not la.read_only_volume(root))

    print("accidents: a write fails (e.g. a full disk)")
    archive = os.path.join(root, "c11")
    pack(make_folder(os.path.join(root, "src11"), {"a.txt": b"a", "b.txt": b"b"}), archive, "t/S")
    before = blobs(archive)
    full = 'def _full(*a, **k):\n    raise OSError(28, "No space left on device")\nla.write_manifest = _full'
    make_folder(os.path.join(root, "src11"), {"a.txt": b"a2", "b.txt": b"b"})
    code, out = patched(full, "pack", os.path.join(root, "src11"), archive, "--name", "t/S", "--replace")
    code_v, _ = tool("verify", archive)
    ok = code != 0 and "No space" in out and clean(out) and code_v == 0 and blobs(archive) == before and not leftovers(archive)
    expect("the error is reported plainly, the old version is intact, nothing new is left", ok, out[-200:])


def check_hardening():
    """Cases of the security review (2026-10-21), in this format: names planted and folders swapped for links while
    pack or restore runs, malformed blobs (refused before decoding, without large allocations), and ambiguous input
    files."""
    root = os.path.join(scratch, "hardening")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)

    print("hardening: something appears where pack is about to write a blob")
    outside = os.path.join(root, "outside.txt")
    open(outside, "wb").write(b"OUTSIDE")
    plant = f"""_inner = la.SafeTree.placing
def _planted(self, rel, replace=True):
    target = la.os.path.join(self.root, *rel.split("/"))
    if rel.startswith("blobs/") and not la.os.path.lexists(target):
        la.os.makedirs(la.os.path.dirname(target), exist_ok=True)
        try:
            la.os.symlink({outside!r}, target)
        except OSError:
            open(target, "wb").write(b"PLANTED")
    return _inner(self, rel, replace)
la.SafeTree.placing = _planted"""
    code, out = patched(plant, "pack", make_folder(os.path.join(root, "src1"), {"a.txt": b"new data"}), os.path.join(root, "h1"),
                        "--name", "t/P")
    expect("a file or link planted at a new blob's name is neither followed nor written over",
           code != 0 and clean(out) and open(outside, "rb").read() == b"OUTSIDE", out[-200:] if code == 0 else "")

    print("hardening: a folder is swapped for a link while pack or restore runs")
    outside_dir = os.path.join(root, "outside-dir")
    os.makedirs(outside_dir)
    swap = f"""import os as _os, subprocess as _sp
_inner, _done = la.SafeTree.placing, set()
def _swapped(self, rel, replace=True):
    parts = rel.split("/")
    if len(parts) > 1 and self.root not in _done:
        _done.add(self.root)
        folder = _os.path.join(self.root, parts[0])
        if _os.path.isdir(folder):
            _os.rename(folder, folder + "-was")
        try:
            _os.symlink({outside_dir!r}, folder, target_is_directory=True)
        except OSError:
            _sp.run(["cmd", "/c", "mklink", "/J", _os.path.normpath(folder), _os.path.normpath({outside_dir!r})], capture_output=True)
    return _inner(self, rel, replace)
la.SafeTree.placing = _swapped"""
    if not link_dir(os.path.join(root, "probe-link"), outside_dir):
        print("  skip swapped folders (no symlinks or junctions here)")
    else:
        files = {"sub/x.txt": b"X"}
        code, out = patched(swap, "pack", make_folder(os.path.join(root, "src2"), files), os.path.join(root, "h2"), "--name", "t/S")
        ok = code != 0 and clean(out) and "not followed" in out and os.listdir(outside_dir) == []
        expect("pack: the blobs folder swapped for a link is refused, nothing written outside", ok, "" if ok else out[-200:])
        archive = os.path.join(root, "h3")
        pack(os.path.join(root, "src2"), archive, "t/S")
        run = patched_process(swap, "restore", archive, "t/S", os.path.join(root, "r3"))
        out = run.communicate(timeout=300)[0]
        ok = run.returncode != 0 and clean(out) and os.listdir(outside_dir) == []
        expect("restore: a destination folder swapped for a link after the checks is refused, nothing written outside",
               ok, "" if ok else out[-200:])
        if os.name == "nt":  # the check through the new file's own handle, for a swap after the folder was checked
            tree_root = os.path.join(root, "h-check")
            os.makedirs(tree_root)
            link_dir(os.path.join(tree_root, "sub"), outside_dir)
            tree = la.SafeTree(tree_root)
            probe = os.path.join(tree_root, "sub", "probe.bin")
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY)
            try:
                tree._check_location(fd, os.path.join(tree.root, "sub"))
                caught = False
            except la.UnsafePath:
                caught = True
            finally:
                os.close(fd)
                os.remove(probe)
            expect("Windows: a new file whose folder is a junction is caught through the file's own handle", caught)

    print("hardening: a file appears after restore checked the destination")
    appear = """_inner = la.SafeTree.placing
def _appear(self, rel, replace=True):
    target = la.os.path.join(self.root, *rel.split("/"))
    if not la.os.path.lexists(target):
        la.os.makedirs(la.os.path.dirname(target), exist_ok=True)
        open(target, "wb").write(b"KEEP")
    return _inner(self, rel, replace)
la.SafeTree.placing = _appear"""
    archive = os.path.join(root, "h4")
    pack(make_folder(os.path.join(root, "src4"), {"x.txt": b"archived"}), archive, "t/A")
    dest = os.path.join(root, "r4")
    run = patched_process(appear, "restore", archive, "t/A", dest)
    out = run.communicate(timeout=300)[0]
    ok = run.returncode != 0 and clean(out) and "appeared" in out and open(os.path.join(dest, "x.txt"), "rb").read() == b"KEEP"
    expect("without --overwrite it is not overwritten (checked and placed in one step)", ok, "" if ok else out[-200:])

    print("hardening: placing without replacing is one step, also when it fails")
    commit = "rename" if os.name == "nt" else "link"  # how a new file goes in place without replacing
    tidy = lambda folder: not [n for n in os.listdir(folder) if n.startswith(".oc-")]  # noqa: E731
    restored = lambda folder: open(os.path.join(folder, "x.txt"), "rb").read()  # noqa: E731
    rival = f"""_real = la.os.{commit}
def _rival(src, dst, *a, **k):
    where = {{"dir_fd": k["dst_dir_fd"]}} if k.get("dst_dir_fd") is not None else {{}}
    fd = la.os.open(dst, la.os.O_WRONLY | la.os.O_CREAT | getattr(la.os, "O_BINARY", 0), 0o644, **where)
    la.os.write(fd, b"RIVAL")
    la.os.close(fd)
    return _real(src, dst, *a, **k)
la.os.{commit} = _rival"""
    dest = os.path.join(root, "r5")
    run = patched_process(rival, "restore", archive, "t/A", dest)
    out = run.communicate(timeout=300)[0]
    ok = run.returncode != 0 and clean(out) and "appeared" in out and restored(dest) == b"RIVAL" and tidy(dest)
    expect("a file another program writes at the very moment of placing is kept", ok, "" if ok else out[-200:])
    fail = f"""def _fail(*a, **k):
    raise OSError(5, "simulated I/O error")
la.os.{commit} = _fail
la.rename_no_replace = _fail"""
    dest = os.path.join(root, "r6")
    run = patched_process(fail, "restore", archive, "t/A", dest)
    out = run.communicate(timeout=300)[0]
    left = os.listdir(dest)
    code, out2 = tool("restore", archive, "t/A", dest)
    ok = run.returncode != 0 and clean(out) and left == [] and code == 0 and restored(dest) == b"archived" and tidy(dest)
    expect("placing that fails leaves nothing (no empty file in the way); restoring again works", ok,
           "" if ok else f"{left} {out[-150:]} {out2[-150:]}")
    if os.name == "nt":
        print("  skip file systems without hard links (POSIX only)")
    elif la._RENAME_NO_REPLACE[0] is None:
        print("  skip file systems without hard links (no rename that refuses an existing name here)")
    else:
        no_links = """def _no_link(*a, **k):
    raise PermissionError(1, "Operation not permitted")
la.os.link = _no_link"""
        dest = os.path.join(root, "r7")
        run = patched_process(no_links, "restore", archive, "t/A", dest)
        out = run.communicate(timeout=300)[0]
        ok = run.returncode == 0 and restored(dest) == b"archived" and tidy(dest)
        expect("without hard links, a rename that refuses an existing name places the file", ok, "" if ok else out[-200:])
        rival_rename = no_links + """
_real = la.rename_no_replace
def _rival(src, dst, dir_fd):
    fd = la.os.open(dst, la.os.O_WRONLY | la.os.O_CREAT, 0o644, dir_fd=dir_fd)
    la.os.write(fd, b"RIVAL")
    la.os.close(fd)
    return _real(src, dst, dir_fd)
la.rename_no_replace = _rival"""
        dest = os.path.join(root, "r8")
        run = patched_process(rival_rename, "restore", archive, "t/A", dest)
        out = run.communicate(timeout=300)[0]
        ok = run.returncode != 0 and clean(out) and "appeared" in out and restored(dest) == b"RIVAL" and tidy(dest)
        expect("... and keeps a file another program writes at that moment", ok, "" if ok else out[-200:])
        neither = no_links + "\nla.rename_no_replace = lambda *a: False"
        dest = os.path.join(root, "r9")
        run = patched_process(neither, "restore", archive, "t/A", dest)
        out = run.communicate(timeout=300)[0]
        left = os.listdir(dest)
        run2 = patched_process(neither, "restore", archive, "t/A", dest, "--overwrite")
        out2 = run2.communicate(timeout=300)[0]
        ok = run.returncode != 0 and clean(out) and "hard links" in out and "--overwrite" in out and left == [] \
            and run2.returncode == 0 and restored(dest) == b"archived" and tidy(dest)
        expect("with neither, restore stops, says to use an empty folder with --overwrite, and that works", ok,
               "" if ok else f"{left} {out[-200:]} {out2[-200:]}")

    print("hardening: malformed blobs are refused before anything is decoded")
    cdir = os.path.join(root, "containers")
    os.makedirs(cdir)
    data = b"hello world"
    sha = la.hashlib.sha256(data).hexdigest()

    def container(header, frames, extra=b"", trailer=True):
        hb = header if isinstance(header, bytes) else json.dumps(header).encode()
        body = la.MAGIC + struct.pack("<I", len(hb)) + hb
        for codec, raw_len, payload, crc, *declared in frames:
            body += la.FRAME_HEAD.pack(codec, raw_len, declared[0] if declared else len(payload), crc) + payload
        body += extra
        return body + (la.hashlib.sha256(body).digest() if trailer else b"")

    def decode(name, blob):
        path = os.path.join(cdir, name.replace(" ", "_")[:60] + ".ocz")
        open(path, "wb").write(blob)
        span = (path, 0, len(blob))
        problem = la.blob_damage(span, sha)
        if problem:
            raise ValueError(problem)
        return b"".join(la.iter_blob_bytes(la.FolderPack(cdir), sha, span=span))

    one = lambda length, kind="zstd": {"original_size": length, "sha256": sha, "regions": [{"offset": 0, "length": length, "kind": kind}], "bases": []}  # noqa: E731
    good = (la.CODEC_RAW, len(data), data, zlib.crc32(data))
    expect("(setup) a well-formed blob decodes", decode("good", container(one(len(data)), [good])) == data)
    bomb = zstandard.ZstdCompressor().compress(bytes(32 << 20))
    bomb_nosize = zstandard.ZstdCompressor(write_content_size=False).compress(bytes(32 << 20))
    base = {"sha256": "ab" * 32, "size": 4}
    cases = {
        "a zstd frame declaring 32 MiB in a 1-byte region": container(one(1), [(la.CODEC_ZSTD, 1, bomb, 0)]),
        "a zstd frame of 32 MiB without a declared size in a 1-byte region": container(one(1), [(la.CODEC_ZSTD, 1, bomb_nosize, 0)]),
        "a zstd frame followed by other bytes": container(one(len(data)), [(la.CODEC_ZSTD, len(data), zstandard.ZstdCompressor().compress(data) + b"junk", zlib.crc32(data))]),
        "bytes after the last frame": container(one(len(data)), [good], extra=b"junk"),
        "no SHA-256 at the end": container(one(len(data)), [good], trailer=False),
        "a wrong SHA-256 at the end": container(one(len(data)), [good])[:-1] + b"\0",
        "the header of another file": container({**one(len(data)), "sha256": "cd" * 32}, [good]),
        "a gap between regions": container({**one(11), "regions": [{"offset": 0, "length": 5, "kind": "zstd"}, {"offset": 6, "length": 5, "kind": "zstd"}]}, [good]),
        "an original_size the regions do not add up to": container({**one(len(data)), "original_size": 12}, [good]),
        "a frame that restores more than its region": container(one(5), [good]),
        "a payload running past the end of the file": container(one(len(data)), [(la.CODEC_RAW, len(data), b"", 0, 1 << 40)]),
        "a region longer than the limit": container(one(1 << 31), [good]),
        "an ans16 frame in a region that is not a delta": container(one(len(data)), [(la.CODEC_ANS16, len(data), data, 0)]),
        "bases that are not a list": container({**one(len(data)), "bases": {"x": 1}}, [good]),
        "a region kind that is a list": container(one(len(data), []), [good]),
        "a region kind that is an object": container(one(len(data), {}), [good]),
        "a base dtype that is a list": container({**one(len(data)), "regions": [{"offset": 0, "length": len(data), "kind": "xor", "dtype": "BF16", "base": [0, 0], "base_dtype": []}], "bases": [base]}, [good]),
        "a base dtype that is an object": container({**one(len(data)), "regions": [{"offset": 0, "length": len(data), "kind": "xor", "dtype": "BF16", "base": [0, 0], "base_dtype": {}}], "bases": [base]}, [good]),
        "a base's model that is a list": container({**one(len(data)), "bases": [{**base, "model": ["t/B"]}]}, [good]),
        "a base's path that is an object": container({**one(len(data)), "bases": [{**base, "path": {"a": 1}}]}, [good]),
        "a base reference past the list of bases": container({**one(len(data)), "regions": [{"offset": 0, "length": len(data), "kind": "xor", "dtype": "BF16", "base": [1, 0]}], "bases": [base]}, [good]),
        "base bytes past the end of the base": container({**one(len(data)), "regions": [{"offset": 0, "length": len(data), "kind": "xor", "dtype": "BF16", "base": [0, 0]}], "bases": [base]}, [good]),
        "a duplicate key in the header": container(json.dumps(one(len(data))).replace('"bases"', '"original_size": 11, "bases"').encode(), [good]),
        "a header longer than the file": la.MAGIC + struct.pack("<I", 10 ** 9) + b"{}" + bytes(32),
        "an offset of false (for 0)": container({**one(len(data)), "regions": [{"offset": False, "length": len(data), "kind": "zstd"}]}, [good]),
        "an offset of 0.0": container({**one(len(data)), "regions": [{"offset": 0.0, "length": len(data), "kind": "zstd"}]}, [good]),
        "an offset of true (for 1)": container({**one(len(data)), "regions": [{"offset": 0, "length": 1, "kind": "zstd"}, {"offset": True, "length": len(data) - 1, "kind": "zstd"}]},
                                               [(la.CODEC_RAW, 1, data[:1], zlib.crc32(data[:1])), (la.CODEC_RAW, len(data) - 1, data[1:], zlib.crc32(data[1:]))]),
        "NaN in the header": container(json.dumps(one(len(data))).replace('"bases": []', '"bases": [], "x": NaN').encode(), [good]),
        "an earlier format's magic": b"OCZ3" + container(one(len(data)), [good])[4:],
    }
    for name, blob in cases.items():
        tracemalloc.start()
        try:
            decode(name, blob)
            ok, detail = False, "decoded"
        except ValueError:
            ok, detail = True, ""
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        expect(f"blob with {name}: refused", ok and peak < (8 << 20), detail or (f"peak {peak >> 20} MiB" if peak >= 8 << 20 else ""))
    la.forget_cached_reads()

    print("hardening: ambiguous input files are stored as plain bytes")

    def gguf(infos):  # [(name bytes, ggml_type, dims, offset)]
        out = b"GGUF" + struct.pack("<IQQ", 3, len(infos), 0)
        for name, ggml_type, dims, offset in infos:
            out += struct.pack("<Q", len(name)) + name + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
            out += struct.pack("<IQ", ggml_type, offset)
        return out + bytes(-len(out) % 32) + bytes(64)

    tensor = lambda offsets: json.dumps({"dtype": "F32", "shape": [4], "data_offsets": offsets})  # noqa: E731
    header = ('{"w": ' + tensor([0, 16]) + ', "w": ' + tensor([16, 32]) + "}").encode()
    header += b" " * (-len(header) % 8)
    odd = {
        "dims-wrap.gguf": gguf([(b"w", 0, [1 << 62, 4], 0)]),  # 4 * 2^64 bytes: wraps to 0 in int64
        "bad-utf8.gguf": gguf([(b"\xff\xfe", 0, [4], 0)]),
        "same-name.gguf": gguf([(b"w", 0, [4], 0), (b"w", 0, [4], 16)]),
        "duplicate-key.safetensors": struct.pack("<Q", len(header)) + header + bytes(32),
    }
    src = make_folder(os.path.join(root, "src5"), odd)
    kinds = {name: la.file_tensors(os.path.join(src, name), len(blob))[0] for name, blob in odd.items()}
    archive = os.path.join(root, "h5")
    code, out = pack(src, archive, "t/O")
    code_v, _ = tool("verify", archive)
    ok = all(k is None for k in kinds.values()) and code == code_v == 0 and clean(out)
    expect("GGUF sizes past int64, invalid UTF-8 or repeated tensor names, and duplicate JSON keys: plain bytes, lossless",
           ok, "" if ok else str(kinds))
    path = manifest_file(archive, "t/O")
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace('"name": ', '"name": "t/O", "name": ', 1))
    code, out = tool("verify", archive)
    expect("a manifest with a duplicate key is a FAIL line", code != 0 and "FAIL" in out and "duplicate" in out and clean(out))
    loose = []
    for sample in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', b'[NaN]'):
        try:
            la.strict_json(sample)
            loose.append(sample)
        except ValueError:
            pass
    expect("NaN, Infinity and -Infinity are not JSON: refused", not loose, str(loose) if loose else "")
    open(path, "w", encoding="utf-8").write(text.replace('"name": ', '"x": NaN, "name": ', 1))
    code, out = tool("verify", archive)
    ok = code != 0 and "FAIL" in out and "NaN" in out and clean(out)
    expect("a manifest with NaN is a FAIL line", ok, "" if ok else out[-200:])
    open(path, "w", encoding="utf-8").write(text)

    print("hardening: a rebase stopped while it writes a blob")
    archive = os.path.join(scratch, "rebase_pack")  # check_rebase's: deltas to re-pack
    before = state(archive)
    stop = """_inner = la.blob_restores
def _stop(p, sha, size, span=None, counted=False):
    if span is not None:  # the check of a blob just written, before it takes its place
        raise KeyboardInterrupt
    return _inner(p, sha, size, span, counted)
la.blob_restores = _stop"""
    code, out = patched(stop, "rebase", archive, "t/F", "--no-base")
    code_v, out_v = tool("verify", archive)
    ok = code != 0 and "KeyboardInterrupt" in out and state(archive) == before and not leftovers(archive) and code_v == 0
    expect("the pack is as it was, without temporary files, and verifies", ok, "" if ok else (out + out_v)[-300:])


def check_selected_files():
    """pack --file and restore --file (used by the window for single files), --flat, files left out, and which
    folders pack may read and restore may write."""
    print("selected files")
    root = os.path.join(scratch, "selected")
    shutil.rmtree(root, ignore_errors=True)
    files = {"a.txt": b"A", "sub/b.txt": b"B", "c.bin": b"C" * 3000}
    src = make_folder(os.path.join(root, "src"), files)
    os.makedirs(os.path.join(root, "elsewhere"))
    link_dir(os.path.join(src, "folder-link"), os.path.join(root, "elsewhere"))  # would stop a pack of the whole folder
    archive = os.path.join(root, "pack")
    code, out = pack(src, archive, "t/S", "--file", "a.txt", "--file", os.path.join("sub", "b.txt"))
    paths = sorted(e["path"] for e in models(archive)["t/S"]["files"]) if code == 0 else []
    ok = code == 0 and paths == ["a.txt", "sub/b.txt"]
    expect("pack --file stores just the files named (a folder link elsewhere does not matter)", ok,
           "" if ok else out[-300:] if code else str(paths))
    for argv, words in ((("--file", "missing.txt"), "cannot be read"), (("--file", "../a.txt"), "unsafe file path"),
                        (("--file", "a.txt", "--exclude", "*.bin"), "not both")):
        code, out = pack(src, archive, "t/S2", *argv)
        ok = code != 0 and words in out and clean(out)
        expect(f"pack {' '.join(argv)}: refused", ok, "" if ok else out[-200:])
    dest = os.path.join(root, "dest")
    code, out = tool("restore", archive, "t/S", dest, "--file", "sub/b.txt")
    ok = code == 0 and same_tree(dest, {"sub/b.txt": b"B"})
    expect("restore --file writes just that file", ok, "" if ok else out[-200:])
    code, out = tool("restore", archive, "t/S", dest, "--file", "c.bin")
    ok = code != 0 and "has no file" in out
    expect("restore --file of a file the model does not have: refused", ok, "" if ok else out[-200:])
    code, out = tool("verify", archive, "t/S", "--file", "sub/b.txt")
    ok = code == 0 and "t/S:sub/b.txt" in out and "a.txt" not in out and "ALL VERIFIED (1 files)" in out
    expect("verify --file checks just that file", ok, "" if ok else out[-200:])
    for argv, words in (((archive, "t/S", "--file", "c.bin"), "has no file"), ((archive, "--file", "a.txt"), "the one model")):
        code, out = tool("verify", *argv)
        ok = code != 0 and words in out
        expect(f"verify {' '.join(argv[1:])}: refused", ok, "" if ok else out[-200:])
    models_list, files_list = os.path.join(root, "models.txt"), os.path.join(root, "files.txt")
    with open(models_list, "wb") as fh:  # as Notepad may write it: a BOM, CRLF, a blank line
        fh.write("\ufefft/S\r\n\r\n".encode())
    with open(files_list, "w", encoding="utf-8") as fh:
        fh.write("sub/b.txt\n")
    code, out = tool("verify", archive, "--models-from", models_list, "--files-from", files_list)
    ok = code == 0 and "t/S:sub/b.txt" in out and "ALL VERIFIED (1 files)" in out
    expect("--models-from and --files-from: one name per line (a BOM, CRLF and blank lines do not matter)", ok,
           "" if ok else out[-200:])
    with open(files_list, "w", encoding="utf-8") as fh:
        fh.write("@weird.txt\n")
    code, out = tool("verify", archive, "t/S", "--files-from", files_list)
    ok = code != 0 and "has no file '@weird.txt'" in out
    expect("a name in a list is taken as written", ok, "" if ok else out[-200:])
    for name, words in (("no-such-list.txt", "cannot read the list"), ("empty.txt", "names nothing")):
        with open(os.path.join(root, "empty.txt"), "w") as fh:
            fh.write("\n\n")
        code, out = tool("remove", archive, "t/S", "--files-from", os.path.join(root, name))
        ok = code != 0 and words in out and "t/S" in models(archive)
        expect(f"a list that {words}: refused, nothing removed", ok, "" if ok else out[-200:])
    code, out = pack(src, archive, "@t/S3", "--file", "a.txt")
    with open(models_list, "w", encoding="utf-8") as fh:
        fh.write("@t/S3\n")
    code2, out2 = tool("remove", archive, "--models-from", models_list)
    ok = code == 0 and code2 == 0 and "@t/S3" not in models(archive)
    expect("a model name may start with @; remove --models-from removes it", ok, "" if ok else (out + out2)[-300:])
    pack(make_folder(os.path.join(root, "twice"), {"x.txt": b"top", "sub/x.txt": b"in sub", "sub/y.txt": b"y"}), archive, "t/F")
    flat = os.path.join(root, "flat")
    code, out = tool("restore", archive, "t/F", flat, "--flat")
    ok = code == 0 and same_tree(flat, {"x.txt": b"top", "x (1).txt": b"in sub", "y.txt": b"y"})
    expect("restore --flat: no folders; the file further down gets \" (1)\"", ok, "" if ok else out[-200:])
    flat = os.path.join(root, "flat-one")
    code, out = tool("restore", archive, "t/F", flat, "--flat", "--file", "sub/x.txt")
    ok = code == 0 and same_tree(flat, {"x.txt": b"in sub"})
    expect("restore --flat --file: named among the files chosen only", ok, "" if ok else out[-200:])
    junk = make_folder(os.path.join(root, "junk"), {"a.txt": b"A", "Thumbs.db": b"x", "Desktop.ini": b"x",
                                                    ".DS_Store": b"x", "__MACOSX/._a.txt": b"x"})
    code, out = pack(junk, archive, "t/J")
    paths = sorted(e["path"] for e in models(archive)["t/J"]["files"]) if code == 0 else []
    ok = code == 0 and paths == ["a.txt"]
    expect("Thumbs.db, desktop.ini, .DS_Store (any case) and __MACOSX/ are left out", ok, "" if ok else out[-200:] + str(paths))
    code, out = pack(junk, archive, "t/J2", "--file", "Thumbs.db")
    paths = sorted(e["path"] for e in models(archive)["t/J2"]["files"]) if code == 0 else []
    ok = code == 0 and paths == ["Thumbs.db"]
    expect("... unless named with --file", ok, "" if ok else out[-200:] + str(paths))

    print("where pack may read and restore may write")
    make_folder(os.path.join(archive, "_kit"), {"README.md": b"kit"})
    for what, folder, words in (("holding the pack folder", root, "holds"),
                                ("that is the pack folder", archive, "is a pack folder"),
                                ("inside the pack folder", os.path.join(archive, "blobs"), "inside the pack folder"),
                                ("inside its _kit", os.path.join(archive, "_kit"), "inside the pack folder")):
        code, out = pack(folder, archive, "t/X")
        ok = code != 0 and words in out and clean(out)
        expect(f"pack of a folder {what}: refused", ok, "" if ok else out[-200:])
    for what, dest, words in (("the pack folder itself", archive, "is a pack folder"),
                              ("inside it", os.path.join(archive, "un_arch"), "inside the pack folder"),
                              ("inside its _kit", os.path.join(archive, "_kit", "x"), "inside the pack folder")):
        code, out = tool("restore", archive, "t/S", dest)
        ok = code != 0 and words in out and clean(out) and not os.path.exists(os.path.join(dest, "a.txt"))
        expect(f"restore into {what}: refused", ok, "" if ok else out[-200:])
    beside = os.path.join(root, "restored", "t", "S")
    code, out = tool("restore", archive, "t/S", beside)
    ok = code == 0 and same_tree(beside, {"a.txt": b"A", "sub/b.txt": b"B"})
    expect("restore into a folder beside the pack folder", ok, "" if ok else out[-200:])
    code, out = pack(beside, archive, "t/again")
    ok = code == 0 and sorted(e["path"] for e in models(archive)["t/again"]["files"]) == ["a.txt", "sub/b.txt"] \
        and "stored already" in out
    expect("pack from such a folder: nothing stored twice", ok, "" if ok else out[-200:])
    code, out = tool("verify", archive, "t/S", "t/J")
    ok = code == 0 and "t/S:a.txt" in out and "t/J:a.txt" in out and "t/again" not in out
    expect("verify of two models: those two", ok, "" if ok else out[-200:])
    for what, argv, words in (("restore of a model the pack does not have", ("restore", archive, "t/none", os.path.join(root, "r-x")),
                               "no model named"),
                              ("verify of a path that does not exist", ("verify", os.path.join(root, "nowhere")), "not a pack folder"),
                              ("rebase of a folder that is no pack", ("rebase", src, "--all"), "not a pack")):
        code, out = tool(*argv)
        ok = code != 0 and words in out and clean(out)
        expect(f"{what}: refused, saying why", ok, "" if ok else out[-200:])
    code, out = tool("rebase", archive, "t/S", "--base", "t/J", "--dry-run")
    ok = code == 0 and "rebasing t/S" in out and "delta base: t/J" in out
    expect("rebase <model> --base <model> --dry-run", ok, "" if ok else out[-300:])


def check_pack_files():
    """Pack files (.oczip): export from a pack folder (whole, or some models with all they need), verify and restore
    in place, pack straight into a new one, --verify and --delete, what is refused, and damaged files."""
    print("pack files (.oczip)")
    root = os.path.join(scratch, "packfiles")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    archive, src = os.path.join(scratch, "rebase_pack"), os.path.join(scratch, "rebase_models")
    sources = {name: files_of(os.path.join(src, name.replace("/", "_"))) for name in ("t/F", "t/P", "t/G")}
    single = os.path.join(root, "all.oczip")
    code, out = tool("export", archive, single)
    ok = code == 0 and os.path.isfile(single) and "verifying" not in out and len(models(archive)) == 3
    expect("export writes the pack file; by default it neither verifies nor removes", ok, "" if ok else out[-300:])
    with zipfile.ZipFile(single) as z:
        methods = {i.filename: i.compress_type for i in z.infolist()}
    ok = all(m == zipfile.ZIP_STORED for n, m in methods.items() if n.startswith("blobs/")) and la.LOCK_NAME not in methods \
        and all(m == zipfile.ZIP_DEFLATED for n, m in methods.items() if n.startswith("models/")) and la.MARKER in methods
    expect("blobs stored, manifests deflated, the lock file left out", ok, "" if ok else str(methods))
    code, out = tool("verify", single)
    code2, out2 = tool("verify", archive)
    ok = code == 0 and "ALL VERIFIED" in out and out.count("  ok  ") == out2.count("  ok  ")
    expect("verify reads the pack file in place (ans16 deltas and shared files too)", ok, "" if ok else out[-300:])
    for name, files in sources.items():
        dest = os.path.join(root, "restored", name.replace("/", "_"))
        code, out = tool("restore", single, name, dest)
        expect(f"restore {name} from the pack file: byte-identical", code == 0 and same_tree(dest, files), out[-300:])
    code, out = tool("list", single)
    expect("list of the pack file", code == 0 and "pack file" in out and "t/F" in out, out[-200:])
    try:
        import llm_archive_gui as gui  # needs tkinter
    except ImportError:
        print("  skip the window's way of running the tool (no tkinter here)")
    else:  # its child keeps stdin open (closing it cancels): the ans16 workers must still start
        dest = os.path.join(root, "runner")
        run = subprocess.Popen([gui.python_exe(), "-I", "-X", "utf8", "-u", "-c", gui.RUNNER, gui.HERE, "restore", single,
                                "t/G", dest], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        chunks = []
        reader = threading.Thread(target=lambda: chunks.append(run.stdout.read()), daemon=True)
        reader.start()
        try:
            code = run.wait(timeout=300)
        except subprocess.TimeoutExpired:  # stop it with the workers it started
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(run.pid)], capture_output=True)
            else:
                run.kill()
            code = "stalled"
        reader.join(10)
        ok = code == 0 and same_tree(dest, sources["t/G"])
        expect("restore of an ans16 delta run as the window runs it (stdin left open): it does not stall", ok,
               "" if ok else f"{code} {''.join(chunks)[-300:]}")
        run.stdin.close()
    dest = os.path.join(root, "one")
    code, out = tool("restore", single, "t/G", dest, "--file", "config.json")
    ok = code == 0 and os.listdir(dest) == ["config.json"]
    expect("restore --file from the pack file", ok, "" if ok else out[-200:])
    some = os.path.join(root, "g.oczip")
    code, out = tool("export", archive, some, "t/G", "--verify")
    names = list(models(some)) if code == 0 else []
    with zipfile.ZipFile(some) as z:
        held = {la.blob_of(n) for n in z.namelist()} - {None}
    la.forget_cached_reads()
    needed = la.Graph(la.open_pack(archive), models(archive)).needed(["t/G"])
    ok = code == 0 and "VERIFIED" in out and names == ["t/G"] and held == needed
    expect("export of one model: that model, with every blob it needs (its bases' too), verified", ok,
           "" if ok else out[-300:])
    for argv, words in ((("export", archive, single), "exists already"),
                        (("export", archive, os.path.join(root, "all.zip")), "must end with"),
                        (("export", archive, os.path.join(archive, "inside.oczip")), "inside the pack folder"),
                        (("pack", os.path.join(src, "t_F"), single, "--name", "t/X"), "exists already"),
                        (("rebase", single, "--all"), "read-only"), (("remove", single, "t/F"), "read-only"),
                        (("rename", single, "t/F", "t/F2"), "read-only")):
        code, out = tool(*argv)
        ok = code != 0 and words in out and clean(out)
        expect(f"{argv[0]} with {os.path.basename(argv[2])}: refused", ok, "" if ok else out[-200:])
    code, out = tool("export", archive, single, "--overwrite")
    expect("--overwrite replaces it", code == 0, "" if code == 0 else out[-200:])
    smaller = os.path.join(root, "p-only.oczip")
    code, out = tool("export", single, smaller, "t/P", "--verify")
    ok = code == 0 and "VERIFIED" in out and list(models(smaller)) == ["t/P"] and os.path.getsize(smaller) < os.path.getsize(single)
    expect("export from a pack file: a smaller pack file of the models named, verified", ok, out[-300:])
    for argv, words in ((("export", single, os.path.join(root, "x.oczip"), "--delete"), "read-only"),
                        (("export", single, single, "--overwrite"), "exported from")):
        code, out = tool(*argv)
        expect(f"export from a pack file with {argv[-1]}: refused", code != 0 and words in out and clean(out), out[-200:])

    print("pack files: pack straight into a new one")
    new_file = os.path.join(root, "new", "G.oczip")
    code, out = tool("pack", os.path.join(src, "t_G"), new_file, "--name", "t/G")
    code_v, out_v = tool("verify", new_file)
    ok = code == 0 and code_v == 0 and os.listdir(os.path.dirname(new_file)) == ["G.oczip"]
    expect("pack into a new pack file: written as it goes (nothing beside it), verified", ok, "" if ok else (out + out_v)[-300:])
    family = os.path.join(root, "new", "family.oczip")
    code, out = tool("pack", os.path.join(src, "t_G"), os.path.join(src, "t_P"), os.path.join(src, "t_F"), family,
                     "--base", "t_P")
    names = models(family) if code == 0 else {}
    ok = code == 0 and list(names) == ["t_F", "t_G", "t_P"] and base_of(family, "t_G") == "t_P" \
        and base_of(family, "t_F") == "t_P" and base_of(family, "t_P") is None
    expect("several folders into one new pack file: --base t_P is packed first, the others against it", ok,
           "" if ok else out[-400:])
    code, out = tool("pack", os.path.join(src, "t_F"), os.path.join(src, "t_G"), os.path.join(root, "new", "auto.oczip"))
    ok = code == 0 and base_of(os.path.join(root, "new", "auto.oczip"), "t_G") == "t_F"
    expect("... and without --base each file picks among those packed before it", ok, "" if ok else out[-300:])
    for what, argv, words in (("with a --base not packed with it", ("pack", os.path.join(src, "t_G"), os.path.join(root, "new", "H.oczip"),
                                                                     "--name", "t/H", "--base", "t/F"), "only the models packed"),
                              ("inside the folder packed", ("pack", os.path.join(src, "t_G"), os.path.join(src, "t_G", "x.oczip"),
                                                            "--name", "t/H"), "holds"),
                              ("inside a pack folder", ("pack", os.path.join(src, "t_G"), os.path.join(archive, "x.oczip"),
                                                        "--name", "t/H"), "not written into a pack folder")):
        code, out = tool(*argv)
        ok = code != 0 and words in out and clean(out) and not os.path.exists(argv[2])
        expect(f"pack into a new pack file {what}: refused", ok, "" if ok else out[-200:])
    stop = """_inner, _calls = la.store_blob, []
def _stopping(*a, **k):
    _calls.append(1)
    if len(_calls) == 2:
        raise KeyboardInterrupt
    return _inner(*a, **k)
la.store_blob = _stopping"""
    code, out = patched(stop, "pack", os.path.join(src, "t_G"), os.path.join(root, "stopped", "G.oczip"), "--name", "t/G")
    ok = code != 0 and os.listdir(os.path.join(root, "stopped")) == []
    expect("a pack into a new pack file stopped midway leaves nothing", ok, "" if ok else out[-200:])

    print("pack files: damaged or unusable")
    la.forget_cached_reads()
    p = la.open_pack(single)
    span = p.blob_span(entry(single, "t/G", "model.safetensors")["sha256"])
    la.close_pack_files()
    bad = os.path.join(root, "flipped.oczip")
    shutil.copyfile(single, bad)
    with open(bad, "r+b") as f:
        f.seek(span[1] + span[2] // 2)
        byte = f.read(1)
        f.seek(-1, 1)
        f.write(bytes([byte[0] ^ 0x20]))
    deflated = os.path.join(root, "deflated.oczip")
    with zipfile.ZipFile(single) as z, zipfile.ZipFile(deflated, "w", zipfile.ZIP_DEFLATED) as w:
        for i in z.infolist():
            w.writestr(i.filename, z.read(i.filename))
    junk = os.path.join(root, "junk.oczip")
    open(junk, "wb").write(b"not a zip at all")
    twice = os.path.join(root, "twice.oczip")
    with zipfile.ZipFile(single) as z, zipfile.ZipFile(twice, "w") as w:
        for i in z.infolist():
            w.writestr(i, z.read(i.filename))
        w.writestr("models/T__G.json", z.read("models/t__G.json").replace(b'"t/G"', b'"T/G"'))  # another case
    no_marker = os.path.join(root, "nomarker.oczip")
    with zipfile.ZipFile(single) as z, zipfile.ZipFile(no_marker, "w") as w:
        for i in z.infolist():
            if i.filename != la.MARKER:
                w.writestr(i, z.read(i.filename))
    for path, words in ((bad, "damaged"), (deflated, "must be stored"), (junk, "cannot open"), (twice, "already"),
                        (no_marker, "no ocpack.json")):
        code, out = tool("verify", path)
        ok = code != 0 and words in out and clean(out)
        expect(f"verify of {os.path.basename(path)}: a clean failure", ok, "" if ok else out[-300:])

    print("pack files: --verify, --delete")
    work = os.path.join(root, "work")

    def fresh_copy():
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(archive, work, ignore=shutil.ignore_patterns(la.LOCK_NAME))
        make_folder(os.path.join(work, "_kit"), {"fake.whl": os.urandom(5000), "README.md": b"how to restore\n" * 200})
        return state(work)

    listing = fresh_copy()
    if link_dir(os.path.join(work, "_kit", "linked"), os.path.join(root, "restored")):
        code, out = tool("export", work, os.path.join(root, "linked.oczip"))
        ok = code != 0 and "link" in out and not os.path.exists(os.path.join(root, "linked.oczip"))
        expect("a link in the _kit stops export", ok, "" if ok else out[-200:])
        (os.rmdir if os.name == "nt" else os.remove)(os.path.join(work, "_kit", "linked"))  # the link itself
    code, out = tool("export", work, os.path.join(root, "checked.oczip"), "--verify")
    ok = code == 0 and "VERIFIED" in out and state(work) == listing
    expect("--verify alone: verified, the folder kept", ok, "" if ok else out[-300:])
    with zipfile.ZipFile(os.path.join(root, "checked.oczip")) as z:
        methods = {i.filename: i.compress_type for i in z.infolist()}
    ok = methods.get("_kit/README.md") == zipfile.ZIP_DEFLATED and methods.get("_kit/fake.whl") == zipfile.ZIP_STORED
    expect("the _kit goes in too (text deflated, a wheel stored)", ok, "" if ok else str(methods))
    patch = 'la.readback_problems = lambda path, digests: ["models/t__F.json: differs from what was put in"]'
    code, out = patched(patch, "export", work, os.path.join(root, "failing.oczip"), "--delete")
    ok = code != 0 and "nothing removed" in out and state(work) == listing
    expect("--delete after a failed verification: nothing removed", ok, "" if ok else out[-300:])
    stop = """_inner, _calls = la.copy_blob, []
def _stopping(*a):
    _calls.append(1)
    if len(_calls) == 3:
        raise KeyboardInterrupt
    return _inner(*a)
la.copy_blob = _stopping"""
    code, out = patched(stop, "export", work, os.path.join(root, "stopped.oczip"), "--delete")
    left = [n for n in os.listdir(root) if n.startswith(".oc-") or n == "stopped.oczip"]
    expect("export stopped midway: no pack file, no temporary file, the folder kept", not left and state(work) == listing,
           str(left))
    code, out = tool("export", work, os.path.join(root, "f.oczip"), "t/F", "--delete")
    names = list(models(work)) if os.path.isdir(work) else []
    ok = code == 0 and "VERIFIED" in out and names == ["t/G", "t/P"] and tool("verify", work)[0] == 0 \
        and tool("verify", os.path.join(root, "f.oczip"))[0] == 0
    expect("export t/F --delete: verified first; t/F leaves the folder, what t/G needs stays", ok, "" if ok else out[-300:])
    code, out = tool("export", work, os.path.join(root, "work.oczip"), "--delete")
    ok = code == 0 and "VERIFIED" in out and not os.path.exists(work)
    expect("export --delete of every model: verified, and the pack folder goes", ok, "" if ok else out[-300:])
    code, out = tool("verify", os.path.join(root, "work.oczip"))
    expect("the result verifies", code == 0 and "ALL VERIFIED" in out, out[-200:])
    fresh_copy()
    open(os.path.join(work, "notes.txt"), "w").write("mine")
    code, out = tool("export", work, os.path.join(root, "unchecked.oczip"), "--delete", "--no-verify")
    ok = code == 0 and "verifying" not in out and os.listdir(work) == ["notes.txt"]
    expect("--delete --no-verify: removed without verifying; a file of your own stays", ok, "" if ok else out[-300:])


def bf16_reference(value):
    """BF16 bits of a float32 value by round-to-nearest-even, worked out from the two neighbouring BF16 values with
    exact float arithmetic instead of bit tricks: an independent check of convert_bits."""
    if math.isnan(value):
        return la.BF16_NAN
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    sign, magnitude, low = (bits >> 16) & 0x8000, abs(value), (bits >> 16) & 0x7FFF
    if math.isinf(value):
        return sign | 0x7F80
    down = struct.unpack("<f", struct.pack("<I", low << 16))[0]
    up = math.ldexp(1.0, 128) if low + 1 == 0x7F80 else struct.unpack("<f", struct.pack("<I", (low + 1) << 16))[0]
    below, above = magnitude - down, up - magnitude  # both exact: the values differ by less than one BF16 step
    return sign | (low if below < above or (below == above and low % 2 == 0) else low + 1)


def check_planes():
    """Codec 1 (byte planes) on every float dtype: lengths around element and frame edges, content from noise to
    constant, and damaged payloads, which must raise rather than decode to anything else."""
    global failures
    print("byte planes (codec 1)")
    lengths = (1, 2, 3, 4, 5, 7, 63, 64, 65, 4095, 4097, 1_000_001)
    contents = {
        "noise": lambda n: rng.integers(0, 256, n, dtype=np.uint8).tobytes(),
        "zeros": lambda n: bytes(n),
        "one value": lambda n: (b"\x3c\x7a\x01\x80" * (n // 4 + 1))[:n],
        "weights": lambda n: (bf16_bytes(n // 2 + 1) * 2)[:n],
    }
    for dtype in la.FLOAT_DTYPES:
        bad = []
        for n in lengths:
            for what, make in contents.items():
                data = make(n)
                if la.planes_decode(la.planes_encode(data, dtype), n, dtype) != data:
                    bad.append(f"{what}/{n}")
        failures += bool(bad)
        print(f"  {'ok  ' if not bad else 'FAIL'} {dtype:<8} {len(lengths) * len(contents)} round trips" + (f": {bad[:5]}" if bad else ""))
    data = bf16_bytes(1_500_000)
    size = len(la.planes_encode(data, "BF16")) / len(data)
    expect(f"BF16 weights shrink to {size:.1%} (exponents entropy-coded)", size < 0.70)
    payload = la.planes_encode(data, "BF16")
    damaged = {
        "stream count": bytes([3]) + payload[1:],
        "truncated": payload[:-100],
        "one byte longer": payload + b"\0",
        "unknown coding": payload[:1] + bytes([9]) + payload[2:],
        "lengths": payload[:2] + struct.pack("<Q", 5) + payload[10:],
        "wrong region length": None,
    }
    for what, broken in damaged.items():
        try:
            out = la.planes_decode(payload, len(data) + 2, "BF16") if broken is None else la.planes_decode(broken, len(data), "BF16")
            ok = False  # no damaged payload may decode
        except Exception:
            ok = True
        expect(f"damaged payload ({what}) is refused", ok, "" if ok else f"decoded {len(out)} bytes")


def check_delta(name, base_dtype, target_dtype, base_rows=2000, target_rows=None, cols=1000):
    """Blob round trip of a target [target_rows, cols] whose tensor exists in the base [base_rows, cols], possibly in
    another float dtype; rows beyond the base are new, rows beyond the target are dropped."""
    global failures
    target_rows = target_rows or base_rows
    w = (rng.standard_normal(base_rows * cols) * 0.02).astype(np.float32)
    keep = min(base_rows, target_rows) * cols
    upd = w[:keep] + (rng.standard_normal(keep) * 1e-4).astype(np.float32) * (rng.random(keep) < 0.2)
    extra = max(0, target_rows - base_rows) * cols
    upd = np.concatenate([upd, (rng.standard_normal(extra) * 0.02).astype(np.float32)])
    enc = {"F32": lambda v: v.tobytes(), "BF16": bf, "F16": lambda v: v.astype(np.float16).tobytes()}
    tag = f"{base_dtype}_{base_rows}_to_{target_dtype}_{target_rows}"
    base_path, target_path = os.path.join(scratch, f"base_{tag}.safetensors"), os.path.join(scratch, f"target_{tag}.safetensors")
    write_safetensors(base_path, {"w": (base_dtype, [base_rows, cols], enc[base_dtype](w)), "n": ("F32", [64], w[:64].tobytes())})
    write_safetensors(target_path, {"n": ("F32", [64], w[:64].tobytes()), "w": (target_dtype, [target_rows, cols], enc[target_dtype](upd))})
    size = os.path.getsize(target_path)
    _, base_tensors = la.file_tensors(base_path, os.path.getsize(base_path))
    _, tensors = la.file_tensors(target_path, size)
    regions = la.tensor_regions(tensors, size, 1 << 20, la.match_against_base(tensors, [{"tensors": base_tensors}], "ans16"))
    base_sha = la.sha256_file(base_path)
    bases = [{"sha256": base_sha, "size": os.path.getsize(base_path), "model": "test/base", "path": "base.safetensors"}]
    out = target_path + ".ocz"
    sha, _ = write_blob(target_path, out, regions, bases, [base_path], workers=2)
    la._BASE_CACHE[(os.path.abspath(scratch), base_sha)] = base_path  # stands in for a restored base file
    try:
        restored = read_blob(sha, out)
    finally:
        la._BASE_CACHE.clear()
    ok = restored == open(target_path, "rb").read()
    failures += not ok
    delta_bytes = sum(r["length"] for r in regions if "base" in r)
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<38} {os.path.getsize(out) / size * 100:6.2f}%  "
          f"coded against base {delta_bytes / size * 100:5.1f}%")


def main():
    global failures
    os.makedirs(scratch, exist_ok=True)
    print("formats")
    fp8 = rng.integers(0, 256, 3_000_000, dtype=np.uint8)
    fp8[fp8 & 0x7F > 0x60] = 0x38  # skewed like real E4M3 weights
    write_safetensors(os.path.join(scratch, "mixed_fp8.safetensors"), {
        "w": ("F8_E4M3", [3000, 1000], fp8.tobytes()),
        "w_scale": ("F32", [3000], weights(3000, np.float32).tobytes()),
        "ids": ("I64", [1000], np.arange(1000, dtype=np.int64).tobytes()),
        "empty": ("BF16", [0], b""),
    })
    check("safetensors FP8 + F32 + I64 + empty", os.path.join(scratch, "mixed_fp8.safetensors"))
    write_safetensors(os.path.join(scratch, "bf16.safetensors"), {"a": ("BF16", [1_500_000], bf16_bytes(1_500_000))})
    check("safetensors BF16", os.path.join(scratch, "bf16.safetensors"))

    with zipfile.ZipFile(os.path.join(scratch, "pytorch_model.bin"), "w", zipfile.ZIP_STORED) as z:  # PyTorch's layout
        z.writestr("archive/data.pkl", b"\x80\x02}q\x00." * 50)  # stand-in bytes: the tool never unpickles
        z.writestr("archive/data/0", bf16_bytes(2_000_000))
        z.writestr("archive/data/1", weights(300_000, np.float32).tobytes())
        z.writestr("archive/data/2", rng.integers(-128, 128, 500_000, dtype=np.int8).tobytes())
    check("PyTorch-style .bin (auto)", os.path.join(scratch, "pytorch_model.bin"))

    with open(os.path.join(scratch, "random.dat"), "wb") as f:
        f.write(rng.integers(0, 256, 3_000_000, dtype=np.uint8).tobytes())
    check("random bytes (auto -> raw)", os.path.join(scratch, "random.dat"))
    with open(os.path.join(scratch, "vocab.json"), "w", encoding="utf-8") as f:
        json.dump({f"token_{i}": i for i in range(200_000)}, f)
    check("text (zstd)", os.path.join(scratch, "vocab.json"))
    with open(os.path.join(scratch, "broken.gguf"), "wb") as f:
        f.write(b"GGUF" + struct.pack("<IQQ", 3, 10**12, 5) + bf16_bytes(500_000))
    check("corrupt GGUF header (falls back)", os.path.join(scratch, "broken.gguf"))
    with open(os.path.join(scratch, "tiny.bin"), "wb") as f:
        f.write(b"\x01\x02\x03")
    check("3-byte file", os.path.join(scratch, "tiny.bin"))
    open(os.path.join(scratch, "empty.bin"), "wb").close()
    check("empty file", os.path.join(scratch, "empty.bin"))

    print("dtype conversion (predictor for cross-dtype deltas)")
    specials = np.array([0.0, -0.0, 1e-8, 6.1e-5, 65504.0, 65520.0, 1e5, 3.4e38, np.inf, -np.inf, np.nan], np.float32)
    values = np.concatenate([specials, (rng.standard_normal(200_000) * 10.0 ** rng.integers(-9, 6, 200_000)).astype(np.float32)])
    f32_bits = values.view(np.uint32)
    with np.errstate(over="ignore"):  # values beyond F16 become infinity, as they should
        want_f16 = values.astype(np.float16).view(np.uint16).copy()
    want_f16[np.isnan(values)] = la.F16_NAN
    want_bf16 = np.array([bf16_reference(float(v)) for v in values], np.uint16)
    for name, got, want in (("F32 -> F16 (numpy reference)", la.convert_bits(f32_bits, "F32", "F16"), want_f16),
                            ("F32 -> BF16 (exact-arithmetic reference)", la.convert_bits(f32_bits, "F32", "BF16"), want_bf16)):
        ok = np.array_equal(got, want)
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name}: {np.count_nonzero(got != want)} mismatches in {len(values):,} values")
    normal = np.abs(values) > 6.2e-5
    normal &= np.abs(values) < 6.5e4
    bf16 = la.convert_bits(f32_bits[normal], "F32", "BF16")
    back = la.to_float32(la.convert_bits(bf16, "BF16", "F16"), "F16")
    ok = np.array_equal(back, la.to_float32(bf16, "BF16"))
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} BF16 -> F16 is exact in the F16 normal range ({normal.sum():,} values)")

    print("cross-dtype and partial-row deltas")
    check_delta("F32 base -> BF16 target", "F32", "BF16")
    check_delta("BF16 base -> F16 target", "BF16", "F16")
    check_delta("F16 base -> BF16 target", "F16", "BF16")
    check_delta("same dtype (BF16 -> BF16)", "BF16", "BF16")
    check_delta("vocab extended 2000 -> 2128 rows", "BF16", "BF16", 2000, 2128)
    check_delta("vocab trimmed 2000 -> 1900 rows", "BF16", "BF16", 2000, 1900)
    check_delta("extended + F32 base -> BF16", "F32", "BF16", 2000, 2128)
    check_delta("extended, F32 -> F32 (xor rows)", "F32", "F32", 2000, 2128)

    check_planes()
    check_auto_base()
    check_rebase()
    check_remove()
    check_damage()
    check_kept_bases()
    check_rebase_files()
    check_output()
    check_safety()
    check_accidents()
    check_hardening()
    check_selected_files()
    check_pack_files()

    print("ALL PASSED" if not failures else f"{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":  # worker processes (Windows spawn) re-import this file
    main()
