"""Round-trip tests for llm_archive.py's standalone containers on synthetic files of several formats.

Usage: python -I test_formats.py <scratch_dir>
"""

import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import tracemalloc
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import zstandard  # noqa: E402

import llm_archive as la  # noqa: E402

rng = np.random.default_rng(7)
scratch = sys.argv[1]
os.makedirs(scratch, exist_ok=True)
failures = 0


def weights(n, dtype):
    return (rng.standard_normal(n) * 0.02).astype(dtype)


def bf16_bytes(n):
    return (weights(n, np.float32).view(np.uint32) >> 16).astype(np.uint16).tobytes()


def check(name, path):
    global failures
    size = os.path.getsize(path)
    fmt, _, regions = la.file_regions(path, size, 1 << 20)
    out = path + ".ocz"
    codecs, _, _ = la.write_container(path, out, regions, (19, 9))
    restored = b"".join(la.iter_original_chunks(scratch, out))
    ok = restored == open(path, "rb").read()
    failures += not ok
    kinds = sorted({r.get("dtype", r["kind"]) for r in regions})
    used = {la.CODEC_NAMES[c]: n for c, n in sorted(codecs.items())}
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<34} fmt={fmt:<12} {os.path.getsize(out) / size * 100:6.2f}%  {kinds} {used}")


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


def tool(*argv, env=None, timeout=None):
    """(exit code, output) of llm_archive.py run with these arguments (and extra environment variables)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_archive.py")
    run = subprocess.run([sys.executable, "-I", path, *argv], capture_output=True, text=True,
                         env={**os.environ, **(env or {})}, timeout=timeout)
    return run.returncode, run.stdout + run.stderr


def patched_process(patch, *argv):
    """A running "llm_archive.py *argv", started after the Python statement(s) `patch` (on module la)."""
    script = f"""import sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
import llm_archive as la
{patch}
sys.argv = ["llm_archive.py", *{list(argv)!r}]
la.main()
"""
    return subprocess.Popen([sys.executable, "-I", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


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


def interrupted(when, *argv):
    """(exit code, output) of llm_archive.py stopped as by Ctrl+C just "before" or just "after" it replaces a
    manifest.json."""
    script = f"""import sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
import llm_archive as la
write = la.write_manifest
def stop(repo_path, manifest):
    if {when == "after"}:
        write(repo_path, manifest)
    raise KeyboardInterrupt
la.write_manifest = stop
sys.argv = ["llm_archive.py", *{list(argv)!r}]
la.main()
"""
    run = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True)
    return run.returncode, run.stdout + run.stderr


def patched_pack(patch, *argv):
    """(exit code, output) of "llm_archive.py pack *argv" run after the Python statement `patch` (on module la)."""
    script = f"""import sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
import llm_archive as la
{patch}
sys.argv = ["llm_archive.py", "pack", *{list(argv)!r}]
la.main()
"""
    run = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True)
    return run.returncode, run.stdout + run.stderr


def link_dir(link, target):
    """Make `link` a symlink to the folder `target`, or on Windows without the symlink right a directory junction
    (which redirects the same way). Whether it worked."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", os.path.normpath(link), os.path.normpath(target)], capture_output=True)
    return os.path.isdir(link)


def expect(name, ok, detail=""):
    global failures
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail else ""))


def check_auto_base():
    """Packs a small family into a fresh archive without --base and checks the base each model picks, the cycle
    guard, that re-packing the base A stores it under new names and leaves every model verifiable even if
    interrupted, and read_range() against the original bytes for every kind of stored file."""
    print("automatic base selection")
    archive, models = os.path.join(scratch, "auto_archive"), os.path.join(scratch, "auto_models")
    shutil.rmtree(archive, ignore_errors=True)
    os.makedirs(archive)
    cols = 1000
    a = (rng.standard_normal(2000 * cols) * 0.02).astype(np.float32)
    b = a + (rng.standard_normal(a.size) * 2e-3).astype(np.float32)  # a fine-tune of A
    d = b.copy()  # a light fine-tune of B: closer to B than to A
    touched = rng.random(d.size) < 0.05
    d[touched] += (rng.standard_normal(touched.sum()) * 1e-3).astype(np.float32)
    c = (rng.standard_normal(a.size) * 0.02).astype(np.float32)  # unrelated, same architecture
    bf = lambda v: la.convert_bits(v.view(np.uint32), "F32", "BF16")  # noqa: E731
    e = np.concatenate([la.convert_bits(bf(d), "BF16", "F16"), (rng.standard_normal(128 * cols) * 0.02).astype(np.float16).view(np.uint16)])
    norm = ("F32", [64], a[:64].tobytes())
    family = [  # (name, tensors, expected base)
        ("t/A", {"w": ("BF16", [2000, cols], bf(a).tobytes()), "n": norm}, None),
        ("t/C", {"w": ("BF16", [2000, cols], bf(c).tobytes()), "n": norm}, None),
        ("t/B", {"w": ("BF16", [2000, cols], bf(b).tobytes()), "n": norm}, "t/A"),
        ("t/D", {"w": ("BF16", [2000, cols], bf(d).tobytes()), "n": norm}, "t/B"),
        ("t/E", {"n": norm, "w": ("F16", [2128, cols], e.tobytes())}, "t/D"),  # F16, extended vocabulary
    ]
    originals = {}
    for name, tensors, want in family:
        folder = os.path.join(models, name.replace("/", "_"))
        os.makedirs(folder, exist_ok=True)
        write_safetensors(os.path.join(folder, "model.safetensors"), tensors)
        originals[name] = open(os.path.join(folder, "model.safetensors"), "rb").read()
        if name == "t/A":  # a small file, stored as-is
            with open(os.path.join(folder, "tokenizer_config.json"), "w") as fh:
                json.dump({"version": 1}, fh)
        code, out = pack(folder, archive, name)
        got = la.load_manifest(archive, name)["files"][0].get("delta_base") if code == 0 else "pack failed"
        expect(f"{name} picks {want or 'no base'}", code == 0 and got == want, got if got != want else "")
        if got != want:
            print(out)
    code, out = pack(os.path.join(models, "t_A"), archive, "t/A", "--base", "t/B")
    expect("re-pack t/A --base t/B is refused (t/B needs t/A)", code != 0 and "cannot be its base" in out)
    code, out = pack(os.path.join(models, "t_A"), archive, "t/A")
    got = la.load_manifest(archive, "t/A")["files"][0].get("delta_base") if code == 0 else "pack failed"
    expect("re-pack t/A picks no base (its dependents are excluded)", code == 0 and got is None, got or "")

    stored = lambda: [e["stored"] for e in la.load_manifest(archive, "t/A")["files"]]  # noqa: E731
    listing = lambda: sorted(os.listdir(os.path.join(archive, "t__A")))  # noqa: E731
    want = ["manifest.json", "model.safetensors.1.ocz", "tokenizer_config.json"]
    ok = stored() == want[1:] and listing() == want
    expect("the re-pack stores the container under the other name, removes the old one, keeps the unchanged small file",
           ok, "" if ok else f"{stored()}, {listing()}")
    with open(os.path.join(models, "t_A", "tokenizer_config.json"), "w") as fh:
        json.dump({"version": 2}, fh)
    for when, names in (("before", want[1:]), ("after", ["model.safetensors.ocz", "tokenizer_config.json.1"])):
        code, out = interrupted(when, "pack", os.path.join(models, "t_A"), archive, "--name", "t/A")
        ok = code != 0 and "KeyboardInterrupt" in out and stored() == names
        expect(f"a re-pack stopped {when} the manifest is replaced leaves it naming {', '.join(names)}", ok,
               "" if ok else out[-400:])
        if when == "before":
            expect("  and deletes the new files", listing() == want, "" if listing() == want else listing())
        code, out = tool("verify", archive)
        ok = code == 0 and "ALL VERIFIED" in out
        expect("  and every model verifies", ok, "" if ok else out[-400:])
    code, out = pack(os.path.join(models, "t_D"), archive, "t/D2", "--no-base")
    got = la.load_manifest(archive, "t/D2")["files"][0]
    expect("--no-base stores standalone (or as a same-as reference)", code == 0 and not got.get("delta_base"))
    code, out = pack(os.path.join(models, "t_C"), archive, "t/C2", "--base", "t/A", "--no-dedupe")
    got = la.load_manifest(archive, "t/C2")["files"][0].get("delta_base") if code == 0 else "pack failed"
    expect("--base t/A is used even for an unrelated model", got == "t/A", got if got != "t/A" else "")
    originals.update({"t/D2": originals["t/D"], "t/C2": originals["t/C"]})
    for name, original in originals.items():
        entry = la.load_manifest(archive, name)["files"][0]
        spans = [(0, len(original)), (0, 1), (len(original) - 3, 3), (12345, 4_000_000)]
        spans += [(int(s), int(n)) for s, n in zip(rng.integers(0, len(original), 6), rng.integers(1, 3_000_000, 6))]
        bad = [s for s in spans if la.read_range(archive, name, entry, *s) != original[s[0]:s[0] + s[1]]]
        expect(f"read_range on {name} ({entry['method']})", not bad, f"wrong bytes for {bad}" if bad else "")


def check_rebase():
    """Packs a fine-tune F before its base P, as happens in practice: P then picks F as its base and refers to F's
    tokenizer. rebase --all leaves that alone (F cannot use P while P needs F); once P is made independent with
    rebase --no-base --no-dedupe, rebase --all moves F onto P. Every model (G is a delta against F) must stay
    restorable, the old containers must be gone and nothing may be left in OC_TMP."""
    print("rebase")
    archive, models = os.path.join(scratch, "rebase_archive"), os.path.join(scratch, "rebase_models")
    tmp = os.path.join(scratch, "rebase_tmp")
    for d in (archive, tmp):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    os.environ["OC_TMP"] = tmp  # inherited by the tool's processes
    cols = 1000
    p = (rng.standard_normal(2000 * cols) * 0.02).astype(np.float32)
    f = p + (rng.standard_normal(p.size) * 2e-3).astype(np.float32)  # a fine-tune of P
    g = f.copy()  # a light fine-tune of F
    touched = rng.random(g.size) < 0.05
    g[touched] += (rng.standard_normal(touched.sum()) * 1e-3).astype(np.float32)
    bf = lambda v: la.convert_bits(v.view(np.uint32), "F32", "BF16").tobytes()  # noqa: E731
    tokenizer = json.dumps({f"token_{i}": i for i in range(100_000)}).encode()  # > 1 MiB, so it is deduplicated
    originals = {}
    for name, w in (("t/F", f), ("t/P", p), ("t/G", g)):
        folder = os.path.join(models, name.replace("/", "_"))
        os.makedirs(folder, exist_ok=True)
        write_safetensors(os.path.join(folder, "model.safetensors"), {"w": ("BF16", [2000, cols], bf(w)), "n": ("F32", [64], p[:64].tobytes())})
        for rel, data in (("config.json", json.dumps({"name": name}).encode()), ("tokenizer.json", tokenizer)):
            with open(os.path.join(folder, rel), "wb") as fh:
                fh.write(data)
        originals[name] = {rel: open(os.path.join(folder, rel), "rb").read() for rel in os.listdir(folder)}
        code, out = pack(folder, archive, name)
        if code:
            print(out)
    entry = lambda name, path: next(e for e in la.load_manifest(archive, name)["files"] if e["path"] == path)  # noqa: E731
    base = lambda name: entry(name, "model.safetensors").get("delta_base")  # noqa: E731
    stored = lambda name: sum(e["stored_size"] for e in la.load_manifest(archive, name)["files"])  # noqa: E731
    manifests = lambda: {n: open(os.path.join(archive, n.replace("/", "__"), "manifest.json"), "rb").read() for n in originals}  # noqa: E731
    expect("packed in this order, F has no base and P and G are deltas against F",
           (base("t/F"), base("t/P"), base("t/G")) == (None, "t/F", "t/F") and entry("t/P", "tokenizer.json")["method"] == "same")

    before = manifests()
    code, out = tool("rebase", archive, "--all")
    expect("rebase --all changes nothing yet (F cannot use P, which needs F)", code == 0 and manifests() == before, out[-400:] if code else "")
    code, out = tool("rebase", archive, "t/P", "--no-base", "--no-dedupe")
    expect("rebase t/P --no-base --no-dedupe makes P independent, even though it grows", code == 0 and base("t/P") is None
           and entry("t/P", "tokenizer.json")["method"] != "same" and la.repo_needs(la.all_manifests(archive))["t/P"] == set())
    f_before = stored("t/F")

    code, out = tool("rebase", archive, "--all")
    ok = code == 0 and base("t/F") == "t/P"
    expect("rebase --all now moves F onto P", ok, "" if ok else out[-400:])
    expect("F is smaller", stored("t/F") < f_before, f"{f_before} -> {stored('t/F')}")
    expect("F's tokenizer becomes a same-as reference to P's", entry("t/F", "tokenizer.json").get("same_as", {}).get("repo") == "t/P")
    expect("the new container has a new name and the old ones are gone", entry("t/F", "model.safetensors")["stored"] == "model.safetensors.1.ocz"
           and not {"model.safetensors.ocz", "tokenizer.json.ocz"} & set(os.listdir(os.path.join(archive, "t__F"))))
    expect("P is skipped (set with --no-base), G keeps F", "skipping t/P" in out and "base is still t/F" in out
           and base("t/G") == "t/F" and "skipped:" not in out, out if "skipped:" in out else "")
    expect("nothing left in OC_TMP", not os.listdir(tmp), os.listdir(tmp))

    code, out = tool("rebase", archive, "t/P", "--base", "t/F")
    expect("rebase t/P --base t/F is refused (F needs P now)", code != 0 and "cannot be its base" in out)
    before = manifests()
    code, out = tool("rebase", archive, "t/F", "--no-base", "--no-dedupe", "--dry-run")
    expect("--dry-run changes nothing", code == 0 and "would be re-packed without a base" in out
           and "would be stored again" in out and manifests() == before)
    tool("rebase", archive, "t/P")  # nothing to change, but now recorded as chosen automatically
    code, out = tool("rebase", archive, "t/P", "--no-base")
    choice = la.load_manifest(archive, "t/P").get("base_choice")
    expect("--no-base is recorded even when no file changes", code == 0 and choice == "none"
           and "skipping t/P" in tool("rebase", archive, "--all", "--dry-run")[1], choice)

    code, out = tool("verify", archive)
    ok = code == 0 and "ALL VERIFIED" in out
    expect("every model verifies", ok, "" if ok else out[-400:])
    for name, files in originals.items():
        dest = os.path.join(scratch, "rebase_restored", name.replace("/", "_"))
        shutil.rmtree(dest, ignore_errors=True)
        code, out = tool("restore", archive, name, dest)
        same = code == 0 and all(open(os.path.join(dest, rel), "rb").read() == data for rel, data in files.items())
        expect(f"restore {name} is byte-identical", same, out[-400:] if not same else "")
    del os.environ["OC_TMP"]


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


def edit_manifest(archive, folder_name, change):
    path = os.path.join(archive, folder_name, "manifest.json")
    manifest = json.load(open(path, encoding="utf-8"))
    change(manifest)
    json.dump(manifest, open(path, "w", encoding="utf-8"), indent=1)


def check_safety():
    """The cases of an external review (2026-10-20): each must be refused or handled without losing data."""
    root = os.path.join(scratch, "safety")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    clean = lambda out: "Traceback" not in out  # noqa: E731

    print("safety: re-packing a model that others need")
    archive = os.path.join(root, "a1")
    text = b"a line of a tokenizer file shared by several models\n" * 40_000  # above the 1 MiB reference limit
    w = weights(500_000, np.float32)
    bf = lambda v: la.convert_bits(v.view(np.uint32), "F32", "BF16").tobytes()  # noqa: E731
    a = os.path.join(root, "src", "A")
    write = lambda files: make_folder(a, files)  # noqa: E731
    st = os.path.join(root, "tmp.safetensors")
    write_safetensors(st, {"w": ("BF16", [500, 1000], bf(w))})
    a_files = {"data.txt": text, "model.safetensors": open(st, "rb").read()}
    write(a_files)
    pack(a, archive, "t/A")
    write_safetensors(st, {"w": ("BF16", [500, 1000], bf(w + np.float32(1e-4)))})
    pack(make_folder(os.path.join(root, "src", "B"), {"data.txt": text, "model.safetensors": open(st, "rb").read()}),
         archive, "t/B")
    b = {e["path"]: e for e in la.load_manifest(archive, "t/B")["files"]}
    expect("t/B refers to t/A (same-as and delta)", b["data.txt"]["method"] == "same" and b["model.safetensors"].get("delta_base") == "t/A")
    code, out = pack(a, archive, "t/A")
    expect("re-pack of t/A with the same files is allowed", code == 0, out[-300:] if code else "")
    write({**a_files, "data.txt": text + b"changed\n"})
    code, out = pack(a, archive, "t/A")
    expect("re-pack of t/A that changes a file t/B needs is refused", code != 0 and "data.txt" in out and "t/B" in out and clean(out))
    write_safetensors(st, {"w": ("BF16", [500, 1000], bf(w * np.float32(1.5)))})
    write({**a_files, "model.safetensors": open(st, "rb").read()})
    code, out = pack(a, archive, "t/A")
    expect("... or changes t/B's delta base file", code != 0 and "model.safetensors" in out and clean(out))
    write(a_files)
    code, out = pack(a, archive, "t/A", "--exclude", "data.txt")
    expect("... or drops a file t/B needs (--exclude)", code != 0 and "data.txt" in out and clean(out))
    code, out = tool("verify", archive)
    expect("the archive is still intact after the refusals", code == 0, out[-300:] if code else "")
    tool("rebase", archive, "t/B", "--no-base", "--no-dedupe")
    write({**a_files, "data.txt": text + b"changed\n"})
    code, out = pack(a, archive, "t/A")
    code_v, out_v = tool("verify", archive)
    expect("after rebase t/B --no-base --no-dedupe, t/A can change", code == 0 and code_v == 0,
           (out + out_v)[-300:] if code or code_v else "")

    print("safety: file names a model folder needs for itself")
    archive = os.path.join(root, "a2")
    files = {"manifest.json": b'{"own": "manifest"}', "manifest.json.part": b"part", "a": b"a", "a.part": b"a.part",
             "x": rng.integers(0, 256, 1_500_000, dtype=np.uint8).tobytes(), "x.ocz": b"not a container"}
    src = make_folder(os.path.join(root, "src", "names"), files)
    for attempt in ("pack", "re-pack"):
        code, out = pack(src, archive, "t/N")
        code_v, out_v = tool("verify", archive)
        dest = os.path.join(root, f"r2-{attempt}")
        code_r, out_r = tool("restore", archive, "t/N", dest)
        stored = sorted(e["stored"] for e in la.load_manifest(archive, "t/N")["files"])
        expect(f"{attempt}: manifest.json, *.part and x/x.ocz all survive", code == code_v == code_r == 0 and same_tree(dest, files),
               f"stored as {stored}")

    print("safety: model names")
    archive = os.path.join(root, "a4", "archive")
    src = make_folder(os.path.join(root, "src", "small"), {"one.txt": b"first model"})
    for bad in ("..", ".", "_kit", "a\\b", "a/../b", "CON", "a//b", " lead", "x:y"):
        code, out = pack(src, archive, bad)
        expect(f"--name {bad!r} is refused", code != 0 and clean(out) and not os.path.exists(os.path.join(root, "a4", "manifest.json")))
    code, out = tool("pack", make_folder(os.path.join(root, "src", "bad,name"), {"f": b"f"}), archive)
    expect("a folder name that is not a valid model name needs --name", code != 0 and "--name" in out and clean(out))
    # a file name another system cannot hold, e.g. "a\b.txt" on Linux (2nd review round): stored under a portable
    # name, restored as it is where the system allows it, else with "_" for the character and " (1)" if that name is
    # already used
    names = os.path.join(root, "a4u")
    if os.name == "nt":  # simulated: no Windows file name holds "\", so a patched listing calls a/b.txt that
        folder = make_folder(os.path.join(root, "src", "odd-names"), {"a/b.txt": b"B", "a_b.txt": b"U", "ok.txt": b"ok"})
        code, out = patched_pack('la.source_files = lambda source, patterns: (["a\\\\b.txt", "a_b.txt", "ok.txt"], [])',
                                 folder, names, "--name", "t/U")
        want = {"a_b.txt": b"U", "a_b (1).txt": b"B", "ok.txt": b"ok"}
    else:  # a real one: "\" is an ordinary character in a Linux file name
        folder = make_folder(os.path.join(root, "src", "odd-names"), {"a\\b.txt": b"B", "a_b.txt": b"U", "ok.txt": b"ok"})
        code, out = pack(folder, names, "t/U")
        want = {"a\\b.txt": b"B", "a_b.txt": b"U", "ok.txt": b"ok"}
    stored = [e["stored"] for e in la.load_manifest(names, "t/U")["files"]] if code == 0 else []
    code_v, _ = tool("verify", names)
    code_r, out_r = tool("restore", names, "t/U", os.path.join(root, "r4u"))
    expect('a file named a\\b.txt packs under a portable name and restores (on Windows as "a_b (1).txt")',
           code == code_v == code_r == 0 and clean(out + out_r) and stored and all("\\" not in s for s in stored)
           and same_tree(os.path.join(root, "r4u"), want), f"stored as {stored}" if code == 0 else out[-200:])
    names = la.StoredNames(["README.md", "Sub/x.ocz"])
    expect("stored names never differ only in case (README.md, readme.md), so an archive copies to Windows",
           names.fresh("readme.md", "") == "readme.md.1" and names.fresh("sub/x") == "Sub/x.1.ocz" and names.fresh("sub", "") == "sub.1")

    def plan(*paths):  # stored names for these original paths, raw ("") when they end in "|"
        n = la.StoredNames()
        out = []
        for p in paths:
            out.append(n.fresh(p.rstrip("|"), "" if p.endswith("|") else ".ocz"))
            n.add(out[-1])
        return out

    # 4th review round: a file and a folder whose stored names would be one name (a_ and a?/ -> a_/)
    expect("file a_ then folder a?/: the folder becomes a_.1/", plan("a_|", "a?/x.txt") == ["a_", "a_.1/x.txt.ocz"])
    expect("folder a?/ then file a_: the file becomes a_.1", plan("a?/x.txt", "a_|") == ["a_/x.txt.ocz", "a_.1"])
    expect("file a then folder A/ (one name on Windows)", plan("a|", "A/x.txt") == ["a", "A.1/x.txt.ocz"])
    expect("folders a?/ and a_/ share one stored folder, their files kept apart",
           plan("a?/x.txt|", "a_/x.txt|") == ["a_/x.txt", "a_/x.txt.1"])
    expect("a top folder named manifest.json, or a folder ending in .part, is renamed",
           plan("manifest.json/x", "y.part/x") == ["manifest.json.1/x.ocz", "y.part.1/x.ocz"])
    expect("a file is not stored as y when y.part is a folder (y is written through y.part)",
           la.StoredNames(["y.part/x.ocz"]).fresh("y", "") == "y.1")
    # a folder that becomes a file in the next version, and back: the old stored folder is still there while the new
    # version is packed (4th review round)
    turn = os.path.join(root, "a4t")
    for version, files in (("folder", {"sub/x.txt": b"in a folder"}), ("file", {"sub": b"now a file"}),
                           ("folder again", {"sub/x.txt": b"in a folder again"})):
        code, out = pack(make_folder(os.path.join(root, "src", "turn"), files), turn, "t/T")
        code_v, _ = tool("verify", turn)
        shutil.rmtree(os.path.join(root, "r4t"), ignore_errors=True)
        code_r, _ = tool("restore", turn, "t/T", os.path.join(root, "r4t"))
        stored = [e["stored"] for e in la.load_manifest(turn, "t/T")["files"]] if code == 0 else []
        left = sorted(os.listdir(os.path.join(turn, "t__T")))
        expect(f"sub as a {version}: packs, verifies, restores; nothing left over", code == code_v == code_r == 0
               and clean(out) and same_tree(os.path.join(root, "r4t"), files) and left == sorted(["manifest.json", stored[0].split("/")[0]]),
               f"stored as {stored}, folder holds {left}" if code == 0 else out[-200:])
    if os.name != "nt":  # the review's case, real: a file a_ and a folder a?/ (Linux allows "?" in names)
        files = {"a_": b"FILE", "a?/x.txt": b"X"}
        code, out = pack(make_folder(os.path.join(root, "src", "prefix"), files), os.path.join(root, "a4p"), "t/P")
        code_v, _ = tool("verify", os.path.join(root, "a4p"))
        code_r, _ = tool("restore", os.path.join(root, "a4p"), "t/P", os.path.join(root, "r4p"))
        expect("a file a_ next to a folder a?/ packs, verifies and restores", code == code_v == code_r == 0
               and clean(out) and same_tree(os.path.join(root, "r4p"), files), out[-200:] if code else "")
    code, out = pack(src, archive, "A/B")
    expect("(setup) A/B packs", code == 0, out[-200:] if code else "")
    code, out = pack(make_folder(os.path.join(root, "src", "other"), {"two.txt": b"second model"}), archive, "A__B")
    code_r, out_r = tool("restore", archive, "A/B", os.path.join(root, "r4"))
    expect("A__B cannot take over the folder of A/B", code != 0 and code_r == 0 and same_tree(os.path.join(root, "r4"), {"one.txt": b"first model"}))
    code, out = tool("restore", archive, "A__B", os.path.join(root, "r4b"))
    expect("restore A__B does not restore A/B instead", code != 0 and clean(out) and not os.path.exists(os.path.join(root, "r4b")))
    os.makedirs(os.path.join(archive, "stray"), exist_ok=True)
    open(os.path.join(archive, "stray", "notes.txt"), "w").write("mine")
    code, out = pack(src, archive, "stray")
    expect("a folder that holds files but no manifest is not packed into", code != 0 and clean(out)
           and open(os.path.join(archive, "stray", "notes.txt")).read() == "mine")

    print("safety: paths in a manifest")
    archive = os.path.join(root, "a3")
    files = {"note.txt": b"hello", "sub/f.txt": b"in a subfolder"}
    pack(make_folder(os.path.join(root, "src", "paths"), files), archive, "t/R")
    original = open(os.path.join(archive, "t__R", "manifest.json"), encoding="utf-8").read()
    for odd in ("a\\..\\..\\x.txt", "C:/x.txt", "x.txt:s", "CON.txt", "name. "):  # names, not escapes
        open(os.path.join(archive, "t__R", "manifest.json"), "w", encoding="utf-8").write(original)
        edit_manifest(archive, "t__R", lambda m: m["files"][0].update(path=odd))
        shutil.rmtree(os.path.join(root, "r3m"), ignore_errors=True)
        dest = os.path.join(root, "r3m", "dest")
        code, out = tool("restore", archive, "t/R", dest)
        where = os.path.join(dest, *la.local_parts(odd))
        expect(f"path {odd!r} restores inside the destination as {'/'.join(la.local_parts(odd))!r}",
               code == 0 and clean(out) and os.path.isfile(where) and open(where, "rb").read() == b"hello"
               and os.listdir(os.path.join(root, "r3m")) == ["dest"], out[-200:] if code else "")
    for bad in ("../outside.txt", "/abs.txt", "sub/../../outside.txt"):
        open(os.path.join(archive, "t__R", "manifest.json"), "w", encoding="utf-8").write(original)
        edit_manifest(archive, "t__R", lambda m: m["files"][0].update(path=bad))
        dest = os.path.join(root, "r3", "dest")
        code, out = tool("restore", archive, "t/R", dest)
        code_v, out_v = tool("verify", archive)
        expect(f"path {bad!r}: restore refused, verify reports it", code != 0 and clean(out) and not os.path.exists(os.path.join(root, "r3"))
               and code_v != 0 and "FAIL" in out_v and clean(out_v))
    open(os.path.join(archive, "t__R", "manifest.json"), "w", encoding="utf-8").write(original)
    edit_manifest(archive, "t__R", lambda m: m["files"][0].update(stored="../../outside.txt"))
    code, out = tool("verify", archive)
    expect("a stored path outside the model folder is reported", code != 0 and "FAIL" in out and clean(out))
    open(os.path.join(archive, "t__R", "manifest.json"), "w", encoding="utf-8").write(original)
    dest, outside = os.path.join(root, "r3s", "dest"), os.path.join(root, "r3s", "outside")
    os.makedirs(dest)
    os.makedirs(outside)
    if not link_dir(os.path.join(dest, "sub"), outside):
        print("  skip symlinked folder in the destination (no symlinks or junctions here)")
    else:
        code, out = tool("restore", archive, "t/R", dest)
        expect("restore does not follow a symlinked folder out of the destination", code != 0 and os.listdir(outside) == [] and clean(out))
    # two paths of the manifest that a link inside the destination makes one file (review of 2026-10-20, 2nd round)
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
        edit_manifest(archive, "t__R", lambda m: m["files"][1].update(path="NOTE.txt"))
        code, out = tool("restore", archive, "t/R", os.path.join(root, "r3c"))
        expect("note.txt and NOTE.txt, one file on Windows: the second becomes NOTE (1).txt",
               code == 0 and same_tree(os.path.join(root, "r3c"), {"note.txt": b"hello", "NOTE (1).txt": b"in a subfolder"}))
        open(os.path.join(archive, "t__R", "manifest.json"), "w", encoding="utf-8").write(original)

    print("safety: restore over existing files")
    dest = os.path.join(root, "r5")
    os.makedirs(dest)
    open(os.path.join(dest, "note.txt"), "wb").write(b"KEEP")
    code, out = tool("restore", archive, "t/R", dest)
    expect("a different existing file stops restore before anything is written",
           code != 0 and open(os.path.join(dest, "note.txt"), "rb").read() == b"KEEP" and not os.path.exists(os.path.join(dest, "sub")))
    edit_manifest(archive, "t__R", lambda m: m["files"][0].update(sha256="0" * 64))
    code, out = tool("restore", archive, "t/R", dest, "--overwrite")
    leftovers = [n for n in os.listdir(dest) if n.endswith(".part")]
    expect("a failed restore keeps the existing file and leaves no temporary file",
           code != 0 and open(os.path.join(dest, "note.txt"), "rb").read() == b"KEEP" and not leftovers and clean(out))
    open(os.path.join(archive, "t__R", "manifest.json"), "w", encoding="utf-8").write(original)
    code, out = tool("restore", archive, "t/R", dest, "--overwrite")
    code2, out2 = tool("restore", archive, "t/R", dest)
    expect("--overwrite replaces it; a second restore keeps the files already right",
           code == code2 == 0 and same_tree(dest, files) and "already there" in out2)

    print("safety: verify keeps going")
    archive = os.path.join(root, "a6")
    pack(make_folder(os.path.join(root, "src", "v"), {"a.txt": b"first", "b.txt": b"second"}), archive, "t/V")
    pack(make_folder(os.path.join(root, "src", "w"), {"c.txt": b"third"}), archive, "t/W")
    os.remove(os.path.join(archive, "t__V", "a.txt"))
    code, out = tool("verify", archive)
    expect("a missing stored file is a FAIL line; the other files are still checked",
           code != 0 and clean(out) and "missing" in out and "ok    t/V:b.txt" in out and "ok    t/W:c.txt" in out)
    open(os.path.join(archive, "t__W", "manifest.json"), "w").write("{ not json")
    code, out = tool("verify", archive)
    expect("an unreadable manifest is a FAIL line too", code != 0 and clean(out) and "t__W" in out and "t/V:b.txt" in out)
    code, out = pack(os.path.join(root, "src", "small"), archive, "t/X")
    expect("pack refuses to change an archive with an unreadable manifest", code != 0 and clean(out) and "t__W" in out)
    os.makedirs(os.path.join(root, "a6-empty"))
    open(os.path.join(root, "a6-file"), "w").write("not an archive")
    for what, where in (("a missing archive", os.path.join(root, "a6-missing")), ("an empty folder", os.path.join(root, "a6-empty")),
                        ("a file", os.path.join(root, "a6-file"))):
        code, out = tool("verify", where)
        code_r, out_r = tool("rebase", where, "--all")
        expect(f"verify and rebase --all of {what} fail: nothing verified", code != 0 and code_r != 0 and clean(out + out_r)
               and "ALL VERIFIED" not in out)

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
               and not os.path.exists(os.path.join(archive, "t__O")))
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
    methods = {e["path"]: e["method"] for e in la.load_manifest(archive, "t/H")["files"]} if code == 0 else {}
    expect("each packs as a plain file and verifies", code == code_v == 0 and clean(out)
           and all(m in ("raw", "auto") for m in methods.values()), str(methods) if code == 0 else out[-300:])


def check_accidents():
    """Cases of the extended review (2026-10-20, round 6): sources that change or are not plain files, two programs
    on one archive, files next to the stored ones, damaged references, malformed manifests and headers."""
    root = os.path.join(scratch, "accidents")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    clean = lambda out: "Traceback" not in out  # noqa: E731

    print("accidents: the source changes after it was hashed")
    archive, src = os.path.join(root, "c1"), os.path.join(root, "src1")
    make_folder(src, {"x.txt": b"OLD", "y.txt": b"Y0"})
    pack(src, archive, "t/C")
    x = os.path.join(src, "x.txt")
    change_x = f"""_inner = la.all_manifests
def _after_hashing(*a, **k):
    open({x!r}, "wb").write(b"CHANGED AFTER HASHING")
    return _inner(*a, **k)
la.all_manifests = _after_hashing"""
    make_folder(src, {"x.txt": b"OLD", "y.txt": b"Y0"})
    code, out = patched_pack(change_x, src, archive, "--name", "t/C")
    code_r, _ = tool("restore", archive, "t/C", os.path.join(root, "r1"))
    expect("an unchanged small file keeps its stored copy: the hashed version is archived, nothing is overwritten",
           code == 0 and code_r == 0 and same_tree(os.path.join(root, "r1"), {"x.txt": b"OLD", "y.txt": b"Y0"}), out[-200:] if code else "")
    make_folder(src, {"x.txt": b"OLD", "y.txt": b"Y1"})
    change_y = f"""_inner = la.pack_verified
def _changed(args, source_file, *a, **k):
    open(source_file, "wb").write(b"CHANGED AFTER HASHING")
    return _inner(args, source_file, *a, **k)
la.pack_verified = _changed"""
    code, out = patched_pack(change_y, src, archive, "--name", "t/C")
    code_v, _ = tool("verify", archive)
    code_r, _ = tool("restore", archive, "t/C", os.path.join(root, "r1b"))
    expect("a changed file that changes again while packed: VERIFY FAILED, the old version is intact",
           code != 0 and "VERIFY FAILED" in out and code_v == code_r == 0
           and same_tree(os.path.join(root, "r1b"), {"x.txt": b"OLD", "y.txt": b"Y0"}))

    print("accidents: two programs on one archive")
    archive = os.path.join(root, "c2")
    pack(make_folder(os.path.join(root, "src2"), {"a.txt": b"AAA"}), archive, "t/L")
    make_folder(os.path.join(root, "src2a"), {"a.txt": b"AAA2"})
    ready, go = os.path.join(root, "a-ready"), os.path.join(root, "a-go")
    first = patched_process(pause_in("write_manifest", ready, go), "pack", os.path.join(root, "src2a"), archive, "--name", "t/L")
    if not wait_for(ready):
        first.kill()
        expect("(setup) the first pack reaches its manifest", False, first.communicate()[0][-300:])
    else:
        code_b, out_b = pack(make_folder(os.path.join(root, "src2b"), {"a.txt": b"BBB"}), archive, "t/L")
        code_o, out_o = pack(os.path.join(root, "src2b"), archive, "t/Other")
        code_v, out_v = tool("verify", archive)
        open(go, "w").close()
        out_a = first.communicate(timeout=120)[0]
        code_v2, _ = tool("verify", archive)
        code_r, _ = tool("restore", archive, "t/L", os.path.join(root, "r2"))
        expect("while a pack runs, a second pack (same or other model) and a verify stop at once",
               code_b != 0 and code_o != 0 and code_v != 0 and "in use" in out_b and "being changed" in out_v and clean(out_b + out_v))
        expect("  and the first pack finishes; the archive verifies and holds its version",
               first.returncode == 0 and code_v2 == code_r == 0 and same_tree(os.path.join(root, "r2"), {"a.txt": b"AAA2"}),
               out_a[-200:] if first.returncode else "")
    ready2, go2 = os.path.join(root, "v-ready"), os.path.join(root, "v-go")
    reader = patched_process(pause_in("verify_entry", ready2, go2), "verify", archive)
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

    print("accidents: files next to the stored ones")
    archive, src = os.path.join(root, "c3"), os.path.join(root, "src3")
    pack(make_folder(src, {"a.txt": b"A"}), archive, "t/U")
    own = {"notes.txt": b"KEEP USER NOTE", "extra/readme.txt": b"MINE"}
    for rel, data in own.items():
        os.makedirs(os.path.dirname(os.path.join(archive, "t__U", *rel.split("/"))), exist_ok=True)
        open(os.path.join(archive, "t__U", *rel.split("/")), "wb").write(data)
    files = {"a.txt": b"A", "notes.txt": b"NEW SOURCE", "extra/readme.txt": b"NEW TOO"}
    code, out = pack(make_folder(src, files), archive, "t/U")
    code_v, _ = tool("verify", archive)
    code_r, _ = tool("restore", archive, "t/U", os.path.join(root, "r3"))
    kept = all(open(os.path.join(archive, "t__U", *rel.split("/")), "rb").read() == data for rel, data in own.items())
    expect("your own files in a model folder are never written over by a pack", code == code_v == code_r == 0 and kept
           and same_tree(os.path.join(root, "r3"), files))

    print("accidents: restore into the archive")
    archive = os.path.join(root, "c4")
    pack(make_folder(os.path.join(root, "src4"), {"manifest.json": b'{"own": 1}', "w.txt": b"w"}), archive, "t/M")
    for where in (os.path.join(archive, "t__M"), archive, os.path.join(archive, "t__M", "sub")):
        code, out = tool("restore", archive, "t/M", where, "--overwrite")
        code_v, _ = tool("verify", archive)
        expect(f"restore into {os.path.relpath(where, root)} is refused even with --overwrite", code != 0 and code_v == 0
               and "archive itself" in out and clean(out))

    print("accidents: an identical file in the archive is damaged")
    archive = os.path.join(root, "c5")
    big = rng.integers(0, 256, 1_500_000, dtype=np.uint8).tobytes()
    pack(make_folder(os.path.join(root, "src5a"), {"big.bin": big}), archive, "t/A")
    stored = os.path.join(archive, "t__A", la.load_manifest(archive, "t/A")["files"][0]["stored"])
    damaged = bytearray(open(stored, "rb").read())
    damaged[len(damaged) // 2] ^= 0x10
    open(stored, "wb").write(damaged)
    code, out = pack(make_folder(os.path.join(root, "src5b"), {"big.bin": big}), archive, "t/B")
    entry = la.load_manifest(archive, "t/B")["files"][0] if code == 0 else {}
    code_v, _ = tool("verify", archive, "t/B")
    expect("the new file is stored itself, with a warning, and verifies", code == 0 and "damaged" in out
           and entry.get("method") != "same" and code_v == 0)

    print("accidents: sources that are not plain files")
    archive = os.path.join(root, "c6")
    outside = make_folder(os.path.join(root, "outside6"), {"weights.txt": b"elsewhere"})
    src = make_folder(os.path.join(root, "src6"), {"ordinary.txt": b"o"})
    if link_dir(os.path.join(src, "linked"), outside):
        code, out = pack(src, archive, "t/K")
        code_x, out_x = pack(src, archive, "t/K", "--exclude", "linked")
        paths = [e["path"] for e in la.load_manifest(archive, "t/K")["files"]] if code_x == 0 else []
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
    path = os.path.join(archive, "t__S", "manifest.json")
    original = open(path, encoding="utf-8").read()
    variants = {
        "no size": lambda m: m["files"][0].pop("size"),
        "no sha256": lambda m: m["files"][0].pop("sha256"),
        "unknown method": lambda m: m["files"][0].update(method="bogus"),
        "a path twice": lambda m: m["files"].append(dict(m["files"][0])),
        "bad stored_sha256": lambda m: m["files"][0].update(stored_sha256="xyz"),
        "negative size": lambda m: m["files"][0].update(size=-1),
        "files is null": lambda m: m.update(files=None),
        "an entry is a list": lambda m: m["files"].__setitem__(0, ["a.txt"]),
        # review round 7: other JSON types where a string or object belongs
        "method is a list": lambda m: m["files"][0].update(method=[]),
        "method is an object": lambda m: m["files"][0].update(method={}),
        "size is text": lambda m: m["files"][0].update(size="3"),
        "path is a number": lambda m: m["files"][0].update(path=7),
        "same_as is a list": lambda m: m["files"][0].update(method="same", same_as=["t/T", "c.txt"]),
        "delta_files is an object": lambda m: m["files"][0].update(method="safetensors+ans16", delta_base="t/T",
                                                                    delta_files={"path": "c.txt"}),
        "repo is a list": lambda m: m.update(repo=["t/S"]),
    }
    for what, change in variants.items():
        open(path, "w", encoding="utf-8").write(original)
        edit_manifest(archive, "t__S", change)
        code, out = tool("verify", archive)
        expect(f"manifest with {what}: a FAIL line, the other model is still checked", code != 0 and clean(out)
               and "FAIL" in out and "ok    t/T:c.txt" in out)
    open(path, "w", encoding="utf-8").write(original)

    print("accidents: when the archive cannot be locked")
    archive = os.path.join(root, "c10")
    pack(make_folder(os.path.join(root, "src10"), {"r.txt": b"R1"}), archive, "t/R")
    lock = os.path.join(archive, la.LOCK_NAME)
    ready, go = os.path.join(root, "w-ready"), os.path.join(root, "w-go")
    writer = patched_process(pause_in("write_manifest", ready, go), "pack",
                             make_folder(os.path.join(root, "src10"), {"r.txt": b"R2"}), archive, "--name", "t/R")
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
        for argv in (["verify", archive], ["restore", archive, "t/R", os.path.join(root, f"r10-{read_only}-{len(options)}")]):
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
    full = 'def _full(*a, **k):\n    raise OSError(28, "No space left on device")\nla.write_manifest = _full'
    make_folder(os.path.join(root, "src9a"), {"a.txt": b"a2", "b.txt": b"b"})
    code, out = patched_pack(full, os.path.join(root, "src9a"), archive, "--name", "t/S")
    code_v, _ = tool("verify", archive)
    leftovers = [n for n in os.listdir(os.path.join(archive, "t__S")) if n.endswith(".part") or n not in ("manifest.json", "a.txt", "b.txt")]
    expect("the error is reported plainly, the old version is intact, nothing new is left", code != 0 and "No space" in out
           and clean(out) and code_v == 0 and not leftovers, f"left: {leftovers}" if leftovers else "")


def check_hardening():
    """Cases of the security review (2026-10-21): names planted and folders swapped for links while pack or restore
    runs, malformed containers (refused before decoding, without large allocations), and ambiguous input files."""
    root = os.path.join(scratch, "hardening")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    clean = lambda out: "Traceback" not in out  # noqa: E731

    print("hardening: something appears where pack is about to write")
    outside = os.path.join(root, "outside.txt")
    open(outside, "wb").write(b"OUTSIDE")
    plant = f"""_inner = la.SafeTree.placing
def _planted(self, rel, replace=True):
    target = la.os.path.join(self.root, *rel.split("/"))
    if rel != "manifest.json" and not la.os.path.lexists(target):
        try:
            la.os.symlink({outside!r}, target)
        except OSError:
            open(target, "wb").write(b"PLANTED")
    return _inner(self, rel, replace)
la.SafeTree.placing = _planted"""
    code, out = patched_pack(plant, make_folder(os.path.join(root, "src1"), {"a.txt": b"new data"}), os.path.join(root, "h1"),
                             "--name", "t/P")
    expect("a file or link planted at a new stored name is neither followed nor written over",
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
        code, out = patched_pack(swap, make_folder(os.path.join(root, "src2"), files), os.path.join(root, "h2"), "--name", "t/S")
        ok = code != 0 and clean(out) and "not followed" in out and os.listdir(outside_dir) == []
        expect("pack: a stored folder swapped for a link is refused, nothing written outside", ok, "" if ok else out[-200:])
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

    print("hardening: malformed containers are refused before anything is decoded")
    cdir = os.path.join(root, "containers")
    os.makedirs(cdir)
    data = b"hello world"

    def container(header, frames, extra=b""):
        hb = header if isinstance(header, bytes) else json.dumps(header).encode()
        body = la.MAGIC + struct.pack("<I", len(hb)) + hb
        for codec, raw_len, payload, crc, *declared in frames:
            body += la.FRAME_HEAD.pack(codec, raw_len, declared[0] if declared else len(payload), crc) + payload
        return body + extra

    def decode(name, blob):
        path = os.path.join(cdir, name.replace(" ", "_") + ".ocz")
        open(path, "wb").write(blob)
        return b"".join(la.iter_original_chunks(cdir, path))

    one = lambda length, kind="zstd": {"original_size": length, "regions": [{"offset": 0, "length": length, "kind": kind}], "delta": None}  # noqa: E731
    good = (la.CODEC_RAW, len(data), data, zlib.crc32(data))
    expect("(setup) a well-formed container decodes", decode("good", container(one(len(data)), [good])) == data)
    bomb = zstandard.ZstdCompressor().compress(bytes(32 << 20))
    bomb_nosize = zstandard.ZstdCompressor(write_content_size=False).compress(bytes(32 << 20))
    cases = {
        "a zstd frame declaring 32 MiB in a 1-byte region": container(one(1), [(la.CODEC_ZSTD, 1, bomb, 0)]),
        "a zstd frame of 32 MiB without a declared size in a 1-byte region": container(one(1), [(la.CODEC_ZSTD, 1, bomb_nosize, 0)]),
        "a zstd frame followed by other bytes": container(one(len(data)), [(la.CODEC_ZSTD, len(data), zstandard.ZstdCompressor().compress(data) + b"junk", zlib.crc32(data))]),
        "bytes after the last frame": container(one(len(data)), [good], extra=b"junk"),
        "a gap between regions": container({"original_size": 11, "regions": [{"offset": 0, "length": 5, "kind": "zstd"}, {"offset": 6, "length": 5, "kind": "zstd"}], "delta": None}, [good]),
        "an original_size the regions do not add up to": container({**one(len(data)), "original_size": 12}, [good]),
        "a frame that restores more than its region": container(one(5), [good]),
        "a payload running past the end of the file": container(one(len(data)), [(la.CODEC_RAW, len(data), b"", 0, 1 << 40)]),
        "a region longer than the limit": container(one(1 << 31), [good]),
        "an ans16 frame in a region that is not a delta": container(one(len(data)), [(la.CODEC_ANS16, len(data), data, 0)]),
        "a duplicate key in the header": container(b'{"original_size": 11, "original_size": 11, "regions": [{"offset": 0, "length": 11, "kind": "zstd"}], "delta": null}', [good]),
        "a header longer than the file": la.MAGIC + struct.pack("<I", 10 ** 9) + b"{}",
        "an offset of false (for 0)": container({**one(len(data)), "regions": [{"offset": False, "length": len(data), "kind": "zstd"}]}, [good]),
        "an offset of 0.0": container({**one(len(data)), "regions": [{"offset": 0.0, "length": len(data), "kind": "zstd"}]}, [good]),
        "an offset of true (for 1)": container({**one(len(data)), "regions": [{"offset": 0, "length": 1, "kind": "zstd"}, {"offset": True, "length": len(data) - 1, "kind": "zstd"}]},
                                               [(la.CODEC_RAW, 1, data[:1], zlib.crc32(data[:1])), (la.CODEC_RAW, len(data) - 1, data[1:], zlib.crc32(data[1:]))]),
        "NaN in the header": container(json.dumps(one(len(data))).replace('"delta": null', '"delta": null, "x": NaN').encode(), [good]),
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
        expect(f"container with {name}: refused", ok and peak < (8 << 20), detail or (f"peak {peak >> 20} MiB" if peak >= 8 << 20 else ""))

    print("hardening: ambiguous input files are stored as plain bytes")

    def gguf(infos):  # [(name bytes, ggml_type, dims, offset)]
        out = b"GGUF" + struct.pack("<IQQ", 3, len(infos), 0)
        for name, ggml_type, dims, offset in infos:
            out += struct.pack("<Q", len(name)) + name + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
            out += struct.pack("<IQ", ggml_type, offset)
        return out + bytes(-len(out) % 32) + bytes(64)

    entry = lambda name, offsets: json.dumps({"dtype": "F32", "shape": [4], "data_offsets": offsets})  # noqa: E731
    header = ('{"w": ' + entry("w", [0, 16]) + ', "w": ' + entry("w", [16, 32]) + "}").encode()
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
    path = os.path.join(archive, "t__O", "manifest.json")
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace('"repo": ', '"repo": "t/O", "repo": ', 1))
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
    open(path, "w", encoding="utf-8").write(text.replace('"repo": ', '"x": NaN, "repo": ', 1))
    code, out = tool("verify", archive)
    ok = code != 0 and "FAIL" in out and "NaN" in out and clean(out)
    expect("a manifest with NaN is a FAIL line", ok, "" if ok else out[-200:])

    print("hardening: a rebase stopped before its manifest leaves no new file behind")
    archive = os.path.join(root, "h6")
    src = make_folder(os.path.join(root, "src6"), {"small.txt": b"a small file"})
    pack(src, archive, "t/A")
    pack(src, archive, "t/B")

    def to_same(manifest):  # pack refers to a file only above RAW_COPY_LIMIT, but a manifest may hold a small one
        manifest["files"][0].update(method="same", same_as={"repo": "t/A", "path": "small.txt"}, stored=None,
                                    stored_size=0, stored_sha256=None)
    edit_manifest(archive, "t__B", to_same)
    os.remove(os.path.join(archive, "t__B", "small.txt"))
    before = sorted(os.listdir(os.path.join(archive, "t__B")))
    code, out = interrupted("before", "rebase", archive, "t/B", "--no-dedupe")
    after = sorted(os.listdir(os.path.join(archive, "t__B")))
    code2, out2 = tool("rebase", archive, "t/B", "--no-dedupe")
    code3, out3 = tool("verify", archive)
    ok = code != 0 and after == before and code2 == 0 and la.load_manifest(archive, "t/B")["files"][0]["method"] == "raw" and code3 == 0
    expect("a small same-as file stored again (as-is) by rebase is removed when it stops; run again it works", ok,
           "" if ok else f"{before} -> {after} {out2[-200:]} {out3[-200:]}")


def check_selected_files():
    """pack --file and restore --file (used by the window for single files), and a source folder and archive that
    hold one another."""
    print("selected files")
    root = os.path.join(scratch, "selected")
    shutil.rmtree(root, ignore_errors=True)
    files = {"a.txt": b"A", "sub/b.txt": b"B", "c.bin": b"C" * 3000}
    src = make_folder(os.path.join(root, "src"), files)
    os.makedirs(os.path.join(root, "elsewhere"))
    link_dir(os.path.join(src, "folder-link"), os.path.join(root, "elsewhere"))  # would stop a pack of the whole folder
    archive = os.path.join(root, "archive")
    code, out = pack(src, archive, "t/S", "--file", "a.txt", "--file", os.path.join("sub", "b.txt"))
    stored = sorted(e["path"] for e in la.load_manifest(archive, "t/S")["files"]) if code == 0 else []
    ok = code == 0 and stored == ["a.txt", "sub/b.txt"]
    expect("pack --file stores just the files named (a folder link elsewhere does not matter)", ok,
           "" if ok else out[-300:] if code else str(stored))
    for argv, words in ((("--file", "missing.txt"), "cannot be read"), (("--file", "../a.txt"), "unsafe file path"),
                        (("--file", "a.txt", "--exclude", "*.bin"), "not both")):
        code, out = pack(src, archive, "t/S2", *argv)
        ok = code != 0 and words in out and "Traceback" not in out
        expect(f"pack {' '.join(argv)}: refused", ok, "" if ok else out[-200:])
    dest = os.path.join(root, "dest")
    code, out = tool("restore", archive, "t/S", dest, "--file", "sub/b.txt")
    ok = code == 0 and same_tree(dest, {"sub/b.txt": b"B"})
    expect("restore --file writes just that file", ok, "" if ok else out[-200:])
    code, out = tool("restore", archive, "t/S", dest, "--file", "c.bin")
    ok = code != 0 and "has no file" in out
    expect("restore --file of a file the model does not have: refused", ok, "" if ok else out[-200:])
    for what, folder in (("holding the archive", root), ("inside the archive", os.path.join(archive, "t__S"))):
        code, out = pack(folder, archive, "t/X")
        ok = code != 0 and "in one another" in out
        expect(f"pack of a folder {what}: refused", ok, "" if ok else out[-200:])


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


def main():
    global failures
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


    def check_delta(name, base_dtype, target_dtype, base_rows=2000, target_rows=None, cols=1000):
        """Container round trip of a target [target_rows, cols] whose tensor exists in the base [base_rows, cols],
        possibly in another float dtype; rows beyond the base are new, rows beyond the target are dropped."""
        global failures
        target_rows = target_rows or base_rows
        w = (rng.standard_normal(base_rows * cols) * 0.02).astype(np.float32)
        keep = min(base_rows, target_rows) * cols
        upd = w[:keep] + (rng.standard_normal(keep) * 1e-4).astype(np.float32) * (rng.random(keep) < 0.2)
        extra = max(0, target_rows - base_rows) * cols
        upd = np.concatenate([upd, (rng.standard_normal(extra) * 0.02).astype(np.float32)])
        enc = {"F32": lambda v: v.tobytes(), "BF16": lambda v: la.convert_bits(v.view(np.uint32), "F32", "BF16").tobytes(),
               "F16": lambda v: v.astype(np.float16).tobytes()}
        tag = f"{base_dtype}_{base_rows}_to_{target_dtype}_{target_rows}"
        base_path, target_path = os.path.join(scratch, f"base_{tag}.safetensors"), os.path.join(scratch, f"target_{tag}.safetensors")
        write_safetensors(base_path, {"w": (base_dtype, [base_rows, cols], enc[base_dtype](w)), "n": ("F32", [64], w[:64].tobytes())})
        write_safetensors(target_path, {"n": ("F32", [64], w[:64].tobytes()), "w": (target_dtype, [target_rows, cols], enc[target_dtype](upd))})
        size = os.path.getsize(target_path)
        _, base_tensors = la.file_tensors(base_path, os.path.getsize(base_path))
        _, tensors = la.file_tensors(target_path, size)
        regions = la.tensor_regions(tensors, size, 1 << 20, la.match_against_base(tensors, [{"tensors": base_tensors}], "ans16"))
        delta = {"repo": "test/base", "files": [{"path": "base.safetensors", "sha256": "0" * 64}], "op": "ans16"}
        out = target_path + ".ocz"
        la.write_container(target_path, out, regions, (19, 9), delta, [base_path], workers=2)
        la._BASE_CACHE[("test/base", "base.safetensors", "0" * 64)] = base_path  # stands in for a restored base file
        try:
            restored = b"".join(la.iter_original_chunks(scratch, out))
        finally:
            la._BASE_CACHE.clear()
        ok = restored == open(target_path, "rb").read()
        failures += not ok
        delta_bytes = sum(r["length"] for r in regions if "base" in r)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<38} {os.path.getsize(out) / size * 100:6.2f}%  "
              f"coded against base {delta_bytes / size * 100:5.1f}%")

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
    check_safety()
    check_accidents()
    check_hardening()
    check_selected_files()

    print("ALL PASSED" if not failures else f"{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":  # worker processes (Windows spawn) re-import this file
    main()
