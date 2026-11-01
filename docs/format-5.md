# Format 5: what changed

CLI 0.6 writes and reads format 5 only. **It cannot read a pack of format 4** (CLI 0.5 and earlier), and there is no
converter: to move a pack, restore it with the old tool and pack it again ([below](#moving-a-pack-from-format-4)).

## How much smaller

The same 6 repositories packed by both versions, 34.27 GiB, every file verified against its SHA-256:

| | format 4 | format 5 |
|---|---|---|
| **whole archive** | 17.18 GiB (50.1 %) | **15.01 GiB (43.8 %)**, 12.7 % smaller |
| Qwen2.5-0.5B | 65.9 % | 65.6 % |
| Qwen2.5-7B | 66.5 % | 66.2 % |
| Qwen2.5-7B-Instruct, a delta | 30.1 % | 30.0 % |
| mradermacher/Qwen2.5-0.5B-GGUF (BF16, Q4_K_M) | 74.8 % | **13.2 %** |
| mradermacher/Qwen2.5-0.5B-Instruct-GGUF (BF16, F16, Q8_0, Q4_K_M) | 54.8 % | **6.4 %** |

The gain depends on what the pack holds. Almost all of it comes from files with a close relative in the pack:

| what | format 4 | format 5 |
|---|---|---|
| a GGUF of the same weights as a safetensors model in the pack (948 MiB, BF16) | 627.1 MiB | **2.4 MiB** |
| a Q8_0 GGUF with its float weights in the pack (507 MiB) | 479.2 MiB | **1.4 MiB** |
| a Q6_K GGUF with its float weights in the pack (482 MiB) | about 462 MiB | **7.9 MiB** |
| a Q4_K_M GGUF with its float weights in the pack (379 MiB) | 366.8 MiB | **172.7 MiB** |
| an abliterated model against its original (942 MiB) | 49.1 MiB | **5.8 MiB** |

On its own, a file gains little or nothing:

| what | format 4 | format 5 |
|---|---|---|
| BF16 weights | 66.39 % | 66.07 % |
| true F32 weights | 83.30 % | 83.14 % |
| F16 weights | 84.53 % | 84.55 % |
| FP8 weights | 81.90 % | 82.19 % |
| a quantized GGUF with no relative in the pack | 95–97 % | 95–97 % |

These gains were measured on a few models. The GGUF and quantized figures all come from one family, Qwen2.5-0.5B: other
architectures may reorder weights when converted, and those weights are not matched.
Q4_K_M and Q5_K_M stop near 46 % here because about 45 % of their bytes are Q5_0 and Q5_1 tensors, which are not rebuilt.

## What it costs

Single runs on one machine (8 cores), in seconds:

| operation | format 4 | format 5 |
|---|---|---|
| pack Qwen2.5-7B | 111 | **70** |
| pack Qwen2.5-7B-Instruct, a delta | 345 | 415 |
| pack mradermacher/Qwen2.5-0.5B-Instruct-GGUF | 80 | 161 |
| pack all 6 repositories | 598 | 746 |
| verify all 6 repositories | 187 | 221 |
| restore Qwen2.5-7B | 35 | 34 |
| restore mradermacher/Qwen2.5-0.5B-Instruct-GGUF | 33 | 81 |

Plain models pack faster and restore as fast. A file rebuilt from its float weights takes longer to restore, as its
weights must be worked out again: a Q8_0 took 8.4 s instead of 1.7 s, a BF16 GGUF against its safetensors 11.5 s instead
of 3.4 s.

Rebuilding relies on the machine's floating point arithmetic. If a machine gets it wrong, the tool works the values
out again without it, which is slow; if the file still does not match, it says so (`inexact`) and writes nothing.
The stored data is intact, and another machine may restore it ([usage](usage.md#5-restore)).
`--no-quant` and `--no-rank1` turn the two new kinds of coding off when packing.

## Moving a pack from format 4

You need room for the restored models, as large as the originals, until the new pack is checked. Keep the old pack
until then.

Get the last tool that reads format 4, [commit 802b0f0](https://github.com/overdoingism/OverCompressing/tree/802b0f0).
From a clone:

```bash
git worktree add ../oc-format4 802b0f0
```

List the models, then restore each one with the old tool:

```bash
python -I ../oc-format4/tools/llm_archive.py list old-pack
python -I ../oc-format4/tools/llm_archive.py restore old-pack Org/Name restored/Org__Name
```

Pack them with the new tool, bases first, so that the others find them, and verify:

```bash
python -I tools/llm_archive.py pack restored/Org__Name new-pack --name Org/Name
python -I tools/llm_archive.py verify new-pack
```

The same goes for a pack file (`.oczip`).
