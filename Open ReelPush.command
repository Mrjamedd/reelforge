#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ -d "/Applications/ReelPush Studio.app" ]; then
  exec open -n "/Applications/ReelPush Studio.app"
fi

exec /usr/bin/arch -arm64 python3 reelpush_desktop.py
