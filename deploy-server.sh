#!/usr/bin/env bash
# 在已 SSH 登录的服务器上执行：独立目录 + 8787 端口，不修改 wwwroot / Nginx / 宝塔站点
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 安装目录（勿改到 wwwroot 下）
INSTALL_DIR="${INSTALL_DIR:-/opt/cai-install_stloader}"
PUBLIC_URL="${PUBLIC_URL:-}"
SERVICE_NAME="${SERVICE_NAME:-cai-stloader-web}"
WEB_PORT="${WEB_PORT:-8787}"

forbidden_path() {
  case "$1" in
    *wwwroot*|*/www/*|*/html/*) return 0 ;;
    *) return 1 ;;
  esac
}

if forbidden_path "$INSTALL_DIR"; then
  echo "[错误] INSTALL_DIR 不能位于 wwwroot 或网站根目录: $INSTALL_DIR"
  echo "       请使用默认: INSTALL_DIR=/opt/cai-install_stloader"
  exit 1
fi

if [[ "$ROOT" == *wwwroot* ]]; then
  echo "[错误] 请勿在 wwwroot 内运行本项目，请复制到 $INSTALL_DIR 后再部署"
  exit 1
fi

detect_public_url() {
  if [[ -n "$PUBLIC_URL" ]]; then
    echo "$PUBLIC_URL"
    return
  fi
  local ip=""
  if command -v curl &>/dev/null; then
    ip="$(curl -s --max-time 3 ifconfig.me 2>/dev/null || true)"
  fi
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  [[ -z "$ip" ]] && ip="127.0.0.1"
  echo "http://${ip}:${WEB_PORT}"
}

PUBLIC="$(detect_public_url | sed 's|/$||')"

echo "[*] 独立部署（不影响 wwwroot / 80 / 443 站点）"
echo "    安装目录: $INSTALL_DIR"
echo "    服务端口: $WEB_PORT"
echo "    对外地址: $PUBLIC"

if [[ "$ROOT" != "$INSTALL_DIR" ]]; then
  echo "[*] 同步代码到 $INSTALL_DIR ..."
  mkdir -p "$INSTALL_DIR"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'data/app.db' \
    --exclude 'config.json' \
    --exclude 'cdk_db.json' \
    --exclude 'admin_db.json' \
    --exclude 'client_db.json' \
    "$ROOT/" "$INSTALL_DIR/"
  cd "$INSTALL_DIR"
fi

if ! command -v python3 &>/dev/null; then
  echo "[错误] 需要 python3，请先安装: apt install python3 python3-pip"
  exit 1
fi

echo "[*] 安装 Python 依赖..."
python3 -m pip install -r requirements.txt -q

export PUBLIC_URL_OVERRIDE="$PUBLIC"
export WEB_PORT_OVERRIDE="$WEB_PORT"
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
elif cfg_path.exists():
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
else:
    cfg = {}

public = os.environ.get("PUBLIC_URL_OVERRIDE", "").rstrip("/")
port = int(os.environ.get("WEB_PORT_OVERRIDE", "8787"))
cfg.setdefault("Server", {})["public_url"] = public
cfg["Server"]["host"] = "0.0.0.0"
cfg["Server"]["port"] = port
cfg["Box_Server_URL"] = public
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
echo "[*] 注册 systemd 服务: $SERVICE_NAME（仅此服务，不改 Nginx）"
cat > "$UNIT" <<UNIT
[Unit]
Description=CS Steam Web (isolated, port ${WEB_PORT})
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 web_server.py --host 0.0.0.0 --port ${WEB_PORT} --public-url ${PUBLIC}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
  echo ""
  echo "=========================================="
  echo "  部署成功（与 wwwroot 站点完全隔离）"
  echo "  目录:     $INSTALL_DIR"
  echo "  端口:     $WEB_PORT（非 80/443）"
  echo "  管理后台: $PUBLIC/admin/login"
  echo "  默认账号: admin / admin123"
  echo "  安全组:   放行 TCP $WEB_PORT"
  echo "=========================================="
else
  journalctl -u "$SERVICE_NAME" -n 40 --no-pager
  exit 1
fi
