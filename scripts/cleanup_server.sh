#!/usr/bin/env bash
# Safe cleanup on production server (does not stop web service)
set -e
cd "$(dirname "$0")/.."

echo "[*] Stopping headless GUI test processes (if any)..."
pkill -f "xvfb-run.*frontend_box" 2>/dev/null || true
pkill -f "python3 frontend_box.py" 2>/dev/null || true
rm -f box_gui.pid box_gui.log

echo "[*] Removing Python cache..."
find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true

echo "[*] Truncating web.log (keep service running)..."
if [[ -f web.log ]]; then
  : > web.log
fi

echo "[*] Removing duplicate client zip (keep playgame-client.zip)..."
rm -f static/mac-client.zip

echo "[*] Removing duplicate static/frontend_box.py (use root frontend_box.py)..."
rm -f static/frontend_box.py

echo "[*] Removing unused Node scaffold (no node_modules on server)..."
rm -f package.json package-lock.json

echo "[OK] Cleanup done. Web PID: $(cat web.pid 2>/dev/null || echo n/a)"
