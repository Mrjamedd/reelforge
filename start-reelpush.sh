#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "Starting ReelPush local services..."
docker compose up -d db redis backend >/dev/null

echo "Applying migrations..."
docker compose exec backend alembic upgrade head >/dev/null

echo "Ensuring admin account exists..."
docker compose exec backend python scripts/seed_admin.py >/dev/null

echo ""
echo "ReelPush is ready:"
echo "  Desktop app: Open ReelPush.command"
echo "  API docs:    http://localhost:8100/api/docs"
