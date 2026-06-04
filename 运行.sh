#!/usr/bin/env bash
# 小白用法：改好 public_url.txt 后执行 bash 运行.sh
set -e
cd "$(dirname "$0")"

URL="$(grep -v '^#' public_url.txt 2>/dev/null | grep -v '^[[:space:]]*$' | head -1 | tr -d '\r' | sed 's|/$||')"
if [[ -z "$URL" ]] || [[ "$URL" == *"你的"* ]] || [[ "$URL" == *"公网IP"* ]]; then
  echo ""
  echo "  请先编辑 public_url.txt ，改成你的服务器地址，例如："
  echo "  http://107.151.244.244:8787"
  echo ""
  exit 1
fi

echo "[1/3] 安装依赖..."
# shellcheck source=install_deps.sh
source "$(dirname "$0")/install_deps.sh"

echo "[2/3] 初始化配置..."
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
python3 setup_deploy.py 2>&1 | tail -5

URL="$(python3 -c "import json; print(json.load(open('config.json',encoding='utf-8'))['Server']['public_url'])")"
CMD_BASE="$(cat .public_cmd_base 2>/dev/null || echo "$URL")"

echo "[3/3] 启动服务..."
echo ""
echo "  管理后台: ${URL}/admin/login"
echo "  用户指令: irm ${CMD_BASE} | iex"
echo "  账号密码: admin / admin123"
echo "  关闭窗口即停止；要后台常驻请用: bash 后台运行.sh"
echo ""
exec python3 web_server.py --host 0.0.0.0 --port 8787 --public-url "$URL"
