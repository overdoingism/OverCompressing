# Third-party notices

OverCompressing itself is MIT-licensed (see [LICENSE](LICENSE)). It builds on the projects below. Nothing here is
vendored into this repository; the offline kit (`kit/build_kit.py`) downloads the listed artifacts at pinned versions
and SHA-256 hashes and ships them with their own license files.

## Shipped with OverCompressing (offline kit)

| Component | License | How it is used |
|---|---|---|
| [CPython](https://www.python.org/) 3.13.16 Windows installer | PSF-2.0 (+ bundled components' licenses, among them [zlib](https://zlib.net/), which the byte-plane codec uses through Python's `zlib` module) | Shipped unmodified in the kit for machines without Python 3.13. |
| [python-build-standalone](https://github.com/astral-sh/python-build-standalone) 20261001 | MPL-2.0 (build system); the distribution contains CPython and libraries under their own licenses | Shipped unmodified in the kit (Linux). |

## Python runtime dependencies (installed from wheels, shipped in the kit)

| Package | Version | License |
|---|---|---|
| [zstandard](https://github.com/indygreg/python-zstandard) (bundles [Zstandard](https://github.com/facebook/zstd), BSD-3-Clause OR GPL-2.0) | 0.25.0 | BSD-3-Clause |
| [numpy](https://numpy.org/) | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |

## Algorithms and prior work (referenced, no code copied)

| Work | License / status | Relation |
|---|---|---|
| [ZipNN](https://github.com/zipnn/zipnn) — IBM ([arXiv:2411.05239](https://arxiv.org/abs/2411.05239)) | MIT | Byte grouping of float weights (exponents in a byte stream of their own) comes from ZipNN. OverCompressing implements it itself (NumPy bit operations, zlib/zstd for the entropy coding) and does not use ZipNN's code. |
| [ryg_rans](https://github.com/rygorous/ryg_rans) — Fabian Giesen | CC0 | The ans16 coder follows the structure of its 64-bit-state interleaved rANS (`rans64`), reimplemented in NumPy. |
| Asymmetric Numeral Systems — Jarek Duda ([arXiv:0902.0271](https://arxiv.org/abs/0902.0271), [arXiv:1311.2540](https://arxiv.org/abs/1311.2540)) | paper | Entropy coding behind ans16. |
| [DFloat11](https://github.com/LeanModels/DFloat11) | Apache-2.0 | Evaluated, not used (needs a CUDA GPU to decompress). |
| [constriction](https://github.com/bamler-lab/constriction) | MIT OR Apache-2.0 OR BSL-1.0 | Used only by the early experiment `dev/ans16_delta.py`. |
