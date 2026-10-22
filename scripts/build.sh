#!/bin/bash
# Build a standalone app for the current OS (macOS/Linux). Output in dist/.
set -e
cd "$(dirname "$0")/.."
python3 -m venv .build-venv
.build-venv/bin/python -m pip install -q --upgrade pip
.build-venv/bin/python -m pip install -q -r requirements-dev.txt
.build-venv/bin/python -m pytest -q tests
.build-venv/bin/pyinstaller --noconfirm --clean LocalConvert.spec
echo "Built: $(ls -d dist/*)"
