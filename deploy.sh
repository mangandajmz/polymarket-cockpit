#!/usr/bin/env bash
# deploy.sh - Pull latest code and restart bot + dashboard services safely.

set -euo pipefail

SERVICE_BOT="polymarket-bot"
SERVICE_DASH="polymarket-dash"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASH_PORT="${DASH_PORT:-8502}"

cd "$REPO_DIR"

BRANCH_NAME="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
BUILD_VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

echo
echo "==========================================="
echo "  Polymarket Bot Deploy"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "==========================================="

echo
echo "> Pulling latest changes..."
git pull origin "$BRANCH_NAME"
BUILD_VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "  OK: branch $BRANCH_NAME"
echo "  OK: build  $BUILD_VERSION"

echo
echo "> Installing dependencies..."
pip install -q --no-cache-dir --upgrade -r requirements.txt
echo "  OK: dependencies"

echo
echo "> Clearing stale listeners on port $DASH_PORT..."
if command -v fuser >/dev/null 2>&1; then
    sudo fuser -k "${DASH_PORT}/tcp" >/dev/null 2>&1 || true
else
    echo "  WARN: fuser not installed, skipping port cleanup"
fi
echo "  OK: port cleanup"

echo
echo "> Restarting bot service..."
sudo systemctl restart "$SERVICE_BOT"
sleep 2
BOT_STATUS="$(systemctl is-active "$SERVICE_BOT" 2>/dev/null || echo unknown)"
if [[ "$BOT_STATUS" != "active" ]]; then
    echo "  FAIL: $SERVICE_BOT status is $BOT_STATUS"
    sudo journalctl -u "$SERVICE_BOT" -n 30 --no-pager || true
    exit 1
fi
echo "  OK: $SERVICE_BOT is active"

echo
echo "> Restarting dashboard service..."
sudo systemctl restart "$SERVICE_DASH"
sleep 2
DASH_STATUS="$(systemctl is-active "$SERVICE_DASH" 2>/dev/null || echo unknown)"
if [[ "$DASH_STATUS" != "active" ]]; then
    echo "  FAIL: $SERVICE_DASH status is $DASH_STATUS"
    sudo journalctl -u "$SERVICE_DASH" -n 30 --no-pager || true
    exit 1
fi
echo "  OK: $SERVICE_DASH is active"

echo
echo "> Verifying dashboard listener..."
READY=0
for _ in $(seq 1 10); do
    if sudo ss -ltnp | grep -q ":${DASH_PORT}"; then
        READY=1
        break
    fi
    sleep 2
done
if [[ "$READY" -ne 1 ]]; then
    echo "  FAIL: no listener on port $DASH_PORT after waiting"
    sudo journalctl -u "$SERVICE_DASH" -n 30 --no-pager || true
    exit 1
fi
sudo ss -ltnp | grep ":${DASH_PORT}" || true

echo
echo "> Recent bot logs..."
sudo journalctl -u "$SERVICE_BOT" -n 5 --no-pager || true

echo
echo "> Recent dashboard logs..."
sudo journalctl -u "$SERVICE_DASH" -n 5 --no-pager || true

echo
echo "==========================================="
echo "  Deploy complete"
echo "  Branch: $BRANCH_NAME"
echo "  Build : $BUILD_VERSION"
echo "==========================================="
echo
