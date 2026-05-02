#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "Starting ReelPush services..."
docker compose up -d db redis backend >/dev/null

echo "Applying migrations..."
docker compose exec backend alembic upgrade head >/dev/null

echo "Ensuring admin account exists..."
docker compose exec backend python scripts/seed_admin.py >/dev/null

echo "Publishing the staged ReelPush workspace..."
docker compose exec backend python scripts/publish_staged.py "$@"
