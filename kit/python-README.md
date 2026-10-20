# Bundled CPython 3.13.16

Used by `../install_windows.ps1` and `../install_linux.sh` only when the machine has no Python 3.13.

## windows/python-3.13.16-amd64.exe — official python.org installer

- Source: https://www.python.org/ftp/python/3.13.16/ (release 2026-09-30)
- SHA-256 `fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b`, 29,935,584 bytes — matches python.org's
  release-file API.
- Authenticode: Valid, signer `CN=Python Software Foundation`, issued by Microsoft ID Verified CS EOC CA 03,
  timestamped by Microsoft. The install script re-checks this and the SHA-256 before running it.
- OpenPGP: `python-3.13.16-amd64.exe.asc` is a good signature from Steve Dower, key
  `7ED1 0B65 31D7 C8E1 BC29 6021 FC62 4643 4870 34E5`; python.org/downloads/metadata/pgp lists him as the
  Windows-binaries signer with key ID FC62 4643 4870 34E5. Key saved as `steve-dower-FC624643487034E5.asc`.
- `python-3.13.16-amd64.exe.sigstore` (Sigstore bundle) kept but not verified here.
- Silent per-user install (what the script runs):
  `python-3.13.16-amd64.exe /quiet InstallAllUsers=0 TargetDir=<dir> Include_launcher=0 PrependPath=0 Shortcuts=0 AssociateFiles=0`

## linux/cpython-3.13.16+20261001-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz — python-build-standalone

- Source: https://github.com/astral-sh/python-build-standalone/releases/tag/20261001 (relocatable build used by uv;
  third party, not python.org). Unpack anywhere; the interpreter is `python/bin/python3.13`. Needs glibc x86-64.
- SHA-256 `ffcb50e716789d1a6e1db5e745d4d194ac8ed9b015ccf5eabcaccb179a25e4a8` — matches the release's `SHA256SUMS`
  (saved as `SHA256SUMS.release`) and GitHub's asset digest.
- GitHub artifact attestation (SLSA provenance v1) verified with `gh attestation verify`: built by
  `astral-sh/python-build-standalone/.github/workflows/release.yml@refs/heads/main`, commit
  `63249f9a31f23542d5a58754aed0b93cc432e761`, OIDC issuer token.actions.githubusercontent.com.
  Saved as `attestation-*.json`.
- Its sysconfig names `clang` as the compiler; `install_linux.sh` switches to gcc when clang is missing.

## Not tested

The Windows silent install and the whole Linux path have not been executed (no Linux machine was available, and
the installer was not run to avoid changing this PC). Only the "Python 3.13 already present" Windows path was run.
