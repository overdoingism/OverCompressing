#!/bin/sh
# Offline install of the OverCompressing restore environment on Linux x86-64 (glibc). No network is used.
# Python 3.13: $PYTHON if set, else python3.13 on PATH, else the bundled python-build-standalone build is
# unpacked into $PYDIR (default ~/overcompressing-python313). Only prebuilt wheels are installed: no compiler is needed.
# Usage:  sh install_linux.sh [venv_dir]
set -eu
KIT=$(cd "$(dirname "$0")" && pwd)
VENV=${1:-$HOME/overcompressing-venv}
PYDIR=${PYDIR:-$HOME/overcompressing-python313}
PBS="cpython-3.13.16+20261001-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PBS_SHA256="ffcb50e716789d1a6e1db5e745d4d194ac8ed9b015ccf5eabcaccb179a25e4a8"

is_py313() {
    "$1" -c 'import sys, platform; assert sys.version_info[:2] == (3, 13) and platform.machine() == "x86_64"' 2>/dev/null
}

PY=${PYTHON:-}
if [ -z "$PY" ]; then
    if command -v python3.13 >/dev/null 2>&1 && is_py313 python3.13; then
        PY=python3.13
    elif [ -x "$PYDIR/python/bin/python3.13" ]; then
        PY="$PYDIR/python/bin/python3.13"
    else
        echo "No Python 3.13 found; unpacking the bundled python-build-standalone into $PYDIR"
        echo "$PBS_SHA256  $KIT/python/linux/$PBS" | sha256sum -c -
        mkdir -p "$PYDIR"
        tar xzf "$KIT/python/linux/$PBS" -C "$PYDIR"
        PY="$PYDIR/python/bin/python3.13"
    fi
fi
is_py313 "$PY" || { echo "Need CPython 3.13 x86_64, got: $("$PY" -V 2>&1)"; exit 1; }
echo "Using $PY"

"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --no-index --find-links "$KIT/wheels/linux_x86_64" -r "$KIT/requirements.txt"
"$VENV/bin/python" -I -c 'import numpy, zstandard'
echo "restore environment OK"

ARCHIVE=$(dirname "$KIT")
echo
echo "List the models:   $VENV/bin/python -I $KIT/tools/llm_archive.py list $ARCHIVE"
echo "Restore a model:   $VENV/bin/python -I $KIT/tools/llm_archive.py restore $ARCHIVE <model name> <dest_dir>"
echo "Check the pack:    $VENV/bin/python -I $KIT/tools/llm_archive.py verify $ARCHIVE"
echo "Or use a window:  $VENV/bin/python -I $KIT/tools/llm_archive_gui.py"
