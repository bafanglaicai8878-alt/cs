#!/usr/bin/env bash
# 检测本机环境、补齐依赖并启动 PLAYGAME 客户端
set -e
cd "$(dirname "$0")"

echo "══════════════════════════════════════"
echo "  PLAYGAME 客户端 · 环境检测"
echo "══════════════════════════════════════"

fail=0
if ! command -v python3 >/dev/null; then
  echo "[×] 未安装 python3"; fail=1
else
  echo "[√] $(python3 --version)"
fi

if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "[×] 缺少 tkinter"
  if command -v apt-get >/dev/null; then
    echo "    正在安装 python3-tk …"
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -qq python3-tk
  else
    fail=1
  fi
fi
python3 -c "import tkinter" 2>/dev/null && echo "[√] tkinter"

echo "[*] 安装 Python 依赖…"
# shellcheck source=install_deps.sh
source "./install_deps.sh"

python3 -c "import setup_deploy; setup_deploy.apply_deploy(verbose=False)" 2>/dev/null || true

URL="$(python3 -c "import json;print(json.load(open('config.json'))['Box_Server_URL'])" 2>/dev/null || echo '')"
echo "[√] 线上地址: ${URL:-未配置}"

PID_FILE="$(pwd)/box_gui.pid"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "[!] 客户端已在运行 PID=$(cat "$PID_FILE")"
  exit 0
fi

LAUNCH=(python3 frontend_box.py)
if [[ "${1:-}" == "--ui-dev" ]]; then
  LAUNCH+=(--ui-dev)
  shift
fi
LAUNCH+=("$@")

echo ""
echo "启动: ${LAUNCH[*]}"

if [[ -n "${DISPLAY:-}" ]]; then
  echo "[√] 图形环境 DISPLAY=$DISPLAY"
  nohup "${LAUNCH[@]}" >> box_gui.log 2>&1 &
elif command -v xvfb-run >/dev/null; then
  echo "[!] 无桌面 DISPLAY，使用虚拟显示 xvfb（仅进程运行，SSH 里看不见窗口）"
  echo "    要在本机看到界面：请在 Windows/Mac 运行 start_box.bat 或下载 playgame-client.zip"
  nohup xvfb-run -a -s "-screen 0 1400x900x24" "${LAUNCH[@]}" >> box_gui.log 2>&1 &
else
  echo "[×] 无 DISPLAY 且无 xvfb-run"
  echo "    服务器: apt install -y xvfb"
  echo "    或在本机 Windows 双击 start_box.bat"
  exit 1
fi

echo $! > "$PID_FILE"
sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "[√] 已启动 PID=$(cat "$PID_FILE")，日志: tail -f box_gui.log"
else
  echo "[×] 启动失败，查看 box_gui.log"
  tail -20 box_gui.log 2>/dev/null || true
  exit 1
fi
