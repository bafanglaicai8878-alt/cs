#!/usr/bin/env bash
# 停止 Web 服务并释放 8787 端口（供 后台运行.sh / fix_redeem_linux.sh 调用）
set +e
cd "$(dirname "$0")"

PORT="${WEB_PORT:-8787}"

if [[ -f web.pid ]]; then
  OLD_PID="$(cat web.pid 2>/dev/null)"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null
    sleep 1
    kill -9 "$OLD_PID" 2>/dev/null
  fi
  rm -f web.pid
fi

if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null
  sleep 1
fi
