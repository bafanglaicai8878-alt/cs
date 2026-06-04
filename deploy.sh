#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" &>/dev/null; then
  echo "[错误] 未找到 python3"
  exit 1
fi

echo "[*] 安装依赖..."
"$PY" -m pip install -r requirements.txt -q

echo "[*] 初始化配置与数据库..."
"$PY" setup_deploy.py

PUBLIC="$("$PY" -c "import json; c=json.load(open('config.json',encoding='utf-8')); print(c.get('Server',{}).get('public_url','http://127.0.0.1:8787'))")"
PORT="$("$PY" -c "import json; c=json.load(open('config.json',encoding='utf-8')); print(int(c.get('Server',{}).get('port',8787)))")"

echo ""
echo "[*] 启动 Web 服务: $PUBLIC"
echo "    管理后台: $PUBLIC/admin/login  (admin / admin123)"
echo ""

exec "$PY" web_server.py --host 0.0.0.0 --port "$PORT" --public-url "$PUBLIC"
