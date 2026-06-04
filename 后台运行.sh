#!/usr/bin/env bash
# 改好 public_url.txt 后执行一次，服务在后台跑（不影响 wwwroot）
set -e
cd "$(dirname "$0")"

URL="$(grep -v '^#' public_url.txt 2>/dev/null | grep -v '^[[:space:]]*$' | head -1 | tr -d '\r' | sed 's|/$||')"
if [[ -z "$URL" ]] || [[ "$URL" == *"你的"* ]]; then
  echo "请先编辑 public_url.txt"
  exit 1
fi

DIR="$(pwd)"
# shellcheck source=install_deps.sh
source "$(dirname "$0")/install_deps.sh"
export PUBLIC_URL_OVERRIDE="$URL"
python3 - <<'PY'
import json, os, secrets
from pathlib import Path
from github_tokens import normalize_github_tokens
from platform_utils import normalize_public_url

p = Path("config.json")
ex = Path("config.example.json")
cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else json.loads(ex.read_text(encoding="utf-8"))
raw = os.environ["PUBLIC_URL_OVERRIDE"].rstrip("/")
port = int(cfg.get("Server", {}).get("port") or 8787)
url, cmd_base = normalize_public_url(raw, port)
cfg.setdefault("Server", {})["public_url"] = url
cfg["Server"]["host"] = "0.0.0.0"
cfg["Server"]["port"] = port
cfg["Box_Server_URL"] = url
Path(".public_cmd_base").write_text(cmd_base, encoding="utf-8")
db = cfg.setdefault("Database", {})
db["enabled"] = True
db.setdefault("path", "data/app.db")
ex_db = json.loads(ex.read_text(encoding="utf-8")).get("Database", {}) if ex.exists() else {}
if str(db.get("engine", "")).lower() in ("mysql", "mariadb") or str(ex_db.get("engine", "")).lower() in ("mysql", "mariadb"):
    db.setdefault("engine", ex_db.get("engine", "mysql"))
    for k in ("host", "port", "user", "password", "database"):
        db.setdefault(k, ex_db.get(k, ""))
tokens = normalize_github_tokens(cfg)
cfg["Github_Personal_Tokens"] = tokens
cfg["Github_Personal_Token"] = tokens[0] if tokens else ""
w = {"", "请改成随机字符串", "cai-box-cdk-secret-change-me"}
cdk = cfg.setdefault("CDK", {})
if str(cdk.get("secret", "")).strip() in w:
    cdk["secret"] = secrets.token_hex(16)
Path("config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
PY
python3 setup_deploy.py 2>&1 | tail -3 || true

URL="$(python3 -c "import json; print(json.load(open('config.json',encoding='utf-8'))['Server']['public_url'])")"
CMD_BASE="$(cat .public_cmd_base 2>/dev/null || echo "$URL")"

bash "$(dirname "$0")/stop_web.sh"

nohup python3 web_server.py --host 0.0.0.0 --port 8787 --public-url "$URL" >> web.log 2>&1 &
echo $! > web.pid
CMD_BASE="$(cat .public_cmd_base 2>/dev/null || echo "$URL")"
sleep 2
if kill -0 "$(cat web.pid)" 2>/dev/null; then
  echo "已后台启动，PID=$(cat web.pid)"
  echo "管理后台: ${URL}/admin/login  （admin / admin123）"
  echo "用户指令: irm ${CMD_BASE} | iex"
  echo "日志: tail -f ${DIR}/web.log"
  echo "停止: kill \$(cat ${DIR}/web.pid)"
else
  echo "启动失败，最近日志："
  tail -40 web.log 2>/dev/null || echo "(无 web.log)"
  echo ""
  echo "可前台调试: python3 web_server.py --host 0.0.0.0 --port 8787 --public-url \"$URL\""
  exit 1
fi
