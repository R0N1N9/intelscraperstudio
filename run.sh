#!/usr/bin/env bash
# intelscraperstudio launcher for macOS / Linux.
set -euo pipefail
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "FFmpeg is required (macOS: brew install ffmpeg)."; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting things up..."
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
PYTHONPATH="$PWD/engine" exec .venv/bin/python -m iss serve "$@"
