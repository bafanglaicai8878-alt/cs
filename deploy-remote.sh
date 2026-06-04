#!/usr/bin/env bash
# 从本机一键同步到 Linux 服务器（独立目录 /opt，不碰 wwwroot、不改 Nginx）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

DEPLOY_HOST="${DEPLOY_HOST:-root@107.151.244.244}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/cai-install_stloader}"

if [[ "$DEPLOY_PATH" == *wwwroot* ]] || [[ "$DEPLOY_PATH" == /www/* ]]; then
  echo "[错误] DEPLOY_PATH 不能设在 wwwroot 或 /www 下: $DEPLOY_PATH"
  exit 1
fi
PUBLIC_URL="${PUBLIC_URL:-http://107.151.244.244:8787}"
SERVICE_NAME="${SERVICE_NAME:-cai-stloader-web}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"

echo "[*] 目标: ${DEPLOY_HOST}:${DEPLOY_PATH}"
echo "[*] 对外地址: ${PUBLIC_URL}"

echo "[*] 同步文件..."
rsync -avz --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'data/app.db' \
  --exclude 'config.json' \
  --exclude 'cdk_db.json' \
  --exclude 'admin_db.json' \
  --exclude 'client_db.json' \
  --exclude 'box_session.json' \
  -e "ssh ${SSH_OPTS}" \
  "$ROOT/" "${DEPLOY_HOST}:${DEPLOY_PATH}/"

echo "[*] 远程安装与初始化..."
ssh ${SSH_OPTS} "${DEPLOY_HOST}" bash -s <<REMOTE
set -euo pipefail
cd "${DEPLOY_PATH}"

if ! command -v python3 &>/dev/null; then
  if command -v apt-get &>/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip python3-venv rsync
  elif command -v yum &>/dev/null; then
    yum install -y python3 python3-pip
  else
    echo "[错误] 未找到 python3，请先在服务器安装 Python 3.10+"
    exit 1
  fi
fi

python3 -m pip install -r requirements.txt -q

export PUBLIC_URL_OVERRIDE="${PUBLIC_URL}"
python3 - <<'PY'
import json
import os
import secrets
from pathlib import Path

root = Path(".")
example = root / "config.example.json"
cfg_path = root / "config.json"
if not cfg_path.exists() and example.exists():
    cfg = json.loads(example.read_text(encoding="utf-8"))
else:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

public = os.environ.get("PUBLIC_URL_OVERRIDE", "").rstrip("/")
if public:
    cfg.setdefault("Server", {})["public_url"] = public
    cfg["Box_Server_URL"] = public
cfg.setdefault("Server", {}).setdefault("host", "0.0.0.0")
cfg.setdefault("Server", {}).setdefault("port", 8787)
cfg.setdefault("Database", {})["enabled"] = True
cfg.setdefault("Database", {}).setdefault("path", "data/app.db")
weak = {"", "请改成随机字符串", "cai-box-cdk-secret-change-me"}
cdk = cfg.setdefault("CDK", {})
if str(cdk.get("secret", "")).strip() in weak:
    cdk["secret"] = secrets.token_hex(16)
cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
PY

python3 setup_deploy.py

UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "\$UNIT" <<UNIT
[Unit]
Description=CS Steam Web (cai-install_stloader)
After=network.target

[Service]
Type=simple
WorkingDirectory=${DEPLOY_PATH}
ExecStart=/usr/bin/python3 web_server.py --host 0.0.0.0 --port 8787 --public-url ${PUBLIC_URL}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
sleep 2
systemctl is-active --quiet "${SERVICE_NAME}" && echo "[OK] 服务已启动" || (journalctl -u "${SERVICE_NAME}" -n 30 --no-pager; exit 1)
REMOTE

echo ""
echo "=========================================="
echo "  部署完成"
echo "  管理后台: ${PUBLIC_URL}/admin/login"
echo "  默认账号: admin / admin123"
echo "  请确认云安全组已放行 TCP 8787"
echo "=========================================="
