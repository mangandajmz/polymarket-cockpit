#!/usr/bin/env bash
# deploy.sh — Pull latest code and restart bot + dashboard services
# Usage: bash deploy.sh
# Assumes services named: polymarket-bot   (systemd)
#                          polymarket-dash  (systemd)
# Edit SERVICE_BOT / SERVICE_DASH below if your names differ.

set -euo pipefail

SERVICE_BOT="polymarket-bot"
SERVICE_DASH="polymarket-dash"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "═══════════════════════════════════════════"
echo "  Polymarket Bot — Deploy"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════"

# ── 1. Pull latest code ───────────────────────────────────────────────────────
echo ""
echo "▶ Pulling latest changes from GitHub..."
cd "$REPO_DIR"
git pull origin "$(git rev-parse --abbrev-ref HEAD)"
echo "  ✓ Code up to date"

# ── 2. Install / upgrade dependencies ────────────────────────────────────────
echo ""
echo "▶ Installing dependencies..."
pip install -q --no-cache-dir --upgrade -r requirements.txt
echo "  ✓ Dependencies OK"

# ── 3. Restart bot service ────────────────────────────────────────────────────
echo ""
echo "▶ Restarting bot service ($SERVICE_BOT)..."
sudo systemctl restart "$SERVICE_BOT"
sleep 2
STATUS=$(systemctl is-active "$SERVICE_BOT" 2>/dev/null || echo "unknown")
if [ "$STATUS" = "active" ]; then
    echo "  ✓ $SERVICE_BOT is running"
else
    echo "  ✗ $SERVICE_BOT status: $STATUS"
    echo "    Run: sudo journalctl -u $SERVICE_BOT -n 30"
    exit 1
fi

# ── 4. Restart dashboard service ─────────────────────────────────────────────
echo ""
echo "▶ Restarting dashboard service ($SERVICE_DASH)..."
sudo systemctl restart "$SERVICE_DASH"
sleep 2
STATUS=$(systemctl is-active "$SERVICE_DASH" 2>/dev/null || echo "unknown")
if [ "$STATUS" = "active" ]; then
    echo "  ✓ $SERVICE_DASH is running"
else
    echo "  ✗ $SERVICE_DASH status: $STATUS"
    echo "    Run: sudo journalctl -u $SERVICE_DASH -n 30"
    exit 1
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  ✓ Deploy complete"
echo "═══════════════════════════════════════════"
echo ""
