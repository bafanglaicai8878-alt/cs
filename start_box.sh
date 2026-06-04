#!/usr/bin/env bash
# 启动 PLAYGAME 桌面客户端（游戏盒子 GUI）
set -e
cd "$(dirname "$0")"

PY="${PY:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "未找到 python3"
  exit 1
fi

if ! "$PY" -c "import tkinter" 2>/dev/null; then
  echo "缺少 tkinter，请执行：sudo apt install -y python3-tk"
  exit 1
fi

if [[ -z "${DISPLAY:-}" ]]; then
  echo ""
  echo "  当前环境没有图形界面（DISPLAY 未设置）。"
  echo "  · 请在有桌面的 Windows / Mac / Linux 本机运行此脚本"
  echo "  · Windows：双击 start_box.bat"
  echo "  · Mac：bash start_box_mac.sh"
  echo "  · 仅预览 UI：bash start_box.sh --ui-dev"
  echo ""
  exit 1
fi

"$PY" -c "import setup_deploy; setup_deploy.apply_deploy(verbose=False)" 2>/dev/null || true

echo "启动 PLAYGAME 客户端（Box_Server_URL 见 config.json）"
exec "$PY" frontend_box.py "$@"
