# Development scripts

Experiments and measurement helpers kept for reference. They are not needed to archive or restore anything.

| script | what it does |
|---|---|
| `delta_entropy.py` | entropy of delta representations (XOR / SUB / ULP) with and without contexts |
| `ans16_delta.py` | first ans16 prototype (uses `constriction`; superseded by the coder in `tools/llm_archive.py`) |
| `profile_ans16.py` | time the phases of an ans16 delta for one archived shard |
| `make_resharded.py` | build a re-sharded, reordered copy of a safetensors file to test tensor matching |
| `measure.ps1` | wall time and peak memory of a whole process tree (Windows) |
