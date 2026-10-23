# Development scripts

Experiments and measurement helpers kept for reference. They are not needed to archive or restore anything.

| script | what it does |
|---|---|
| `coverage.py` | what pack makes of model files, from their headers alone (whole files or ranged downloads): the parser that takes each file or why none does, bytes by dtype and how each is coded, and with `--against` how much lines up with a base for a delta |
| `blob_stats.py` | how the files of models in a pack are stored, by dtype and by how each part was coded (byte planes, zstd, ans16 or XOR delta, delta not used): the bytes each part restores to and the bytes it takes, from the blobs' headers |
| `delta_entropy.py` | entropy of delta representations (XOR / SUB / ULP) with and without contexts |
| `ans16_delta.py` | first ans16 prototype (uses `constriction`; superseded by the coder in `tools/llm_archive.py`) |
| `profile_ans16.py` | time the phases of an ans16 delta for one archived shard |
| `make_resharded.py` | build a re-sharded, reordered copy of a safetensors file to test tensor matching |
| `measure.ps1` | wall time and peak memory of a whole process tree (Windows) |
