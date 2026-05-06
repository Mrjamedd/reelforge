#!/usr/bin/env bash
# Run from your Mac to sync the ReelPush backend to the OCI server and restart it.
# Usage: OCI_HOST=user@1.2.3.4 bash deploy/deploy.sh [--migrate] [--seed]
#
# Flags:
#   --migrate   Run Alembic migrations after deploying (safe to run repeatedly)
#   --seed      Seed the admin account (only needed on first deploy)
set -euo pipefail

OCI_HOST="${OCI_HOST:?Set OCI_HOST=ubuntu@<server-ip> before running}"
REMOTE_DIR="${REMOTE_DIR:-reelpush}"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

MIGRATE=false
SEED=false
for arg in "$@"; do
  case $arg in
    --migrate) MIGRATE=true ;;
    --seed)    SEED=true ;;
  esac
done

echo "=== Syncing ReelPush to $OCI_HOST ==="

# ── Sync source code (exclude local dev artifacts) ───────────────────────────
rsync -avz --delete \
  --exclude='.env' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='*.pyc' \
  --exclude='storage/' \
  --exclude='build/' \
  --exclude='dist/' \
  --exclude='.DS_Store' \
  "$LOCAL_DIR/backend/" \
  "$OCI_HOST:$REMOTE_DIR/backend/"

rsync -avz \
  "$LOCAL_DIR/docker-compose.prod.yml" \
  "$LOCAL_DIR/nginx.conf" \
  "$OCI_HOST:$REMOTE_DIR/"

echo ""
echo "=== Building and starting services on server ==="

ssh "$OCI_HOST" bash -s -- "$REMOTE_DIR" "$MIGRATE" "$SEED" <<'REMOTE'
set -euo pipefail
REMOTE_DIR="$1"
MIGRATE="$2"
SEED="$3"

cd "$REMOTE_DIR"

if [ ! -f .env ]; then
  echo "ERROR: $REMOTE_DIR/.env not found."
  echo "Copy .env.production.example to $REMOTE_DIR/.env and fill in values first."
  exit 1
fi

echo "Building Docker image..."
docker compose -f docker-compose.prod.yml build backend

echo "Pulling base images..."
docker compose -f docker-compose.prod.yml pull db redis

echo "Starting services (db + redis first)..."
docker compose -f docker-compose.prod.yml up -d db redis

echo "Waiting for db to be healthy..."
timeout 60 bash -c 'until docker compose -f docker-compose.prod.yml ps db | grep -q "healthy"; do sleep 2; done'

if [ "$MIGRATE" = "true" ]; then
  echo "Running Alembic migrations..."
  docker compose -f docker-compose.prod.yml run --rm backend alembic upgrade head
fi

if [ "$SEED" = "true" ]; then
  echo "Seeding admin account..."
  docker compose -f docker-compose.prod.yml run --rm backend python scripts/seed_admin.py
fi

echo "Starting all services..."
docker compose -f docker-compose.prod.yml up -d

echo ""
echo "=== Services status ==="
docker compose -f docker-compose.prod.yml ps
REMOTE

echo ""
echo "=== Deploy complete ==="
echo "Health check: curl http://$(echo $OCI_HOST | cut -d@ -f2):8100/api/health"
