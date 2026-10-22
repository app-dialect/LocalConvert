#!/bin/bash
# Double-click to run LocalConvert from source on macOS.
# The first run downloads the Python libraries it needs (one time, ~150 MB); after that it works fully offline.
cd "$(dirname "$0")" || exit 1
PY=python3
if ! command -v $PY >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close." _; exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "First run: setting up (one time only)..."
  $PY -m venv .venv && .venv/bin/python -m pip install --upgrade pip -q && \
    .venv/bin/python -m pip install -r requirements-app.txt -q || { read -r -p "Setup failed. Press Enter to close." _; exit 1; }
fi
exec .venv/bin/python run.py "$@"
