#!/usr/bin/env bash
# Run ONCE on the OCI server to install Docker and create directories.
# Usage: bash server_setup.sh
set -euo pipefail

echo "=== ReelPush server setup ==="

# ── Docker ────────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. You may need to log out and back in for group changes."
else
  echo "Docker already installed: $(docker --version)"
fi

# ── Docker Compose plugin ─────────────────────────────────────────────────────
if ! docker compose version &>/dev/null 2>&1; then
  echo "Installing Docker Compose plugin..."
  DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}
  mkdir -p "$DOCKER_CONFIG/cli-plugins"
  curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
    -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
  chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
else
  echo "Docker Compose already available: $(docker compose version)"
fi

# ── App directories ───────────────────────────────────────────────────────────
DEPLOY_DIR="$HOME/reelpush"
mkdir -p "$DEPLOY_DIR/storage/uploads"
mkdir -p "$DEPLOY_DIR/storage/thumbnails"

echo ""
echo "=== Setup complete ==="
echo "App directory: $DEPLOY_DIR"
echo ""
echo "Next steps:"
echo "  1. Copy .env.production.example to $DEPLOY_DIR/.env and fill in values"
echo "  2. Run deploy/deploy.sh from your Mac to push code and start services"
