#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV="$REPO_ROOT/.venv-packaging"
PYTHON="$VENV/bin/python"
SPEC="$REPO_ROOT/packaging/reelpush_studio.spec"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required to build ReelPush Studio." >&2
  exit 1
fi

if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$VENV"
fi

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install pyinstaller
"$PYTHON" "$REPO_ROOT/packaging/macos/make-icns.py"

cd "$REPO_ROOT"
"$PYTHON" -m PyInstaller --clean --noconfirm "$SPEC"

echo "Created: $REPO_ROOT/dist/ReelPush Studio.app"
