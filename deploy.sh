#!/bin/bash
# Deploy AI Project Architect to Hetzner server
#
# Usage:
#   ./deploy.sh                  # Deploy with defaults
#   ./deploy.sh --build-only     # Build without restarting
#
# Prerequisites:
#   1. SSH access to the server: ssh root@95.216.199.47
#   2. Docker and docker-compose installed on the server
#   3. .env.prod file created on the server with OPENAI_API_KEY

set -euo pipefail

SERVER="root@95.216.199.47"
APP_DIR="/opt/ai-project-architect"
REPO_URL="https://github.com/ColaberryIntern/AI_ProjectArchitect.git"

echo "=== Deploying AI Project Architect to Hetzner ==="
echo "Server: $SERVER"
echo "App dir: $APP_DIR"
echo ""

# Step 1: SSH in and pull latest code
echo "[1/4] Pulling latest code..."
ssh "$SERVER" << 'REMOTE_SCRIPT'
set -euo pipefail

APP_DIR="/opt/ai-project-architect"
REPO_URL="https://github.com/ColaberryIntern/AI_ProjectArchitect.git"

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

# Install docker-compose plugin if not present
if ! docker compose version &> /dev/null; then
    echo "Installing Docker Compose plugin..."
    apt-get update && apt-get install -y docker-compose-plugin
fi

# Clone or pull the repo
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR"
    git pull origin main
else
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# Check for .env.prod
if [ ! -f "$APP_DIR/.env.prod" ]; then
    echo ""
    echo "ERROR: .env.prod not found!"
    echo "Create it with: cp .env.prod.example .env.prod"
    echo "Then set your OPENAI_API_KEY in .env.prod"
    exit 1
fi
REMOTE_SCRIPT

echo "[2/4] Building Docker image..."
ssh "$SERVER" "cd $APP_DIR && docker compose build"

if [ "${1:-}" = "--build-only" ]; then
    echo "Build complete. Skipping restart (--build-only)."
    exit 0
fi

echo "[3/4] Restarting service..."
ssh "$SERVER" "cd $APP_DIR && docker compose down && docker compose up -d"

echo "[4/4] Verifying deployment..."
sleep 3
ssh "$SERVER" "docker compose -f $APP_DIR/docker-compose.yml ps"

echo ""
echo "=== Deployment complete ==="
echo "API available at: http://95.216.199.47:8000/api/v1/generate"
echo "API docs at:      http://95.216.199.47:8000/docs"
echo ""
