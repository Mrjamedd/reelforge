#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

docker compose up -d db redis backend >/dev/null
docker compose exec backend alembic upgrade head >/dev/null
docker compose exec backend python scripts/seed_admin.py >/dev/null

status=0
if ! docker compose exec backend python scripts/publish_staged.py "$@"; then
  status=$?
fi

echo ""
if [[ $status -eq 0 ]]; then
  echo "ReelPush publish finished."
else
  echo "ReelPush publish failed."
fi

read -r -n 1 -s -p "Press any key to close this window..."
echo ""
exit $status
