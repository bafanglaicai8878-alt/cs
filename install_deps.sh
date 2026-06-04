#!/usr/bin/env bash
# 确保 pip 可用并安装 requirements.txt
ensure_pip() {
  if python3 -m pip --version &>/dev/null 2>&1; then
    return 0
  fi
  echo "[*] 未检测到 pip，正在安装..."
  if command -v apt-get &>/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3-pip
  elif command -v yum &>/dev/null; then
    yum install -y python3-pip
  elif command -v dnf &>/dev/null; then
    dnf install -y python3-pip
  elif python3 -m ensurepip --upgrade &>/dev/null; then
    :
  else
    echo "[错误] 无法自动安装 pip，请手动执行："
    echo "  apt install -y python3-pip    # Debian/Ubuntu"
    echo "  yum install -y python3-pip    # CentOS"
    exit 1
  fi
  if ! python3 -m pip --version &>/dev/null 2>&1; then
    echo "[错误] pip 安装后仍不可用"
    exit 1
  fi
  echo "[OK] pip 已就绪"
}

ensure_pip
if command -v apt-get &>/dev/null; then
  apt-get install -y -qq python3-pip libjpeg-dev zlib1g-dev 2>/dev/null || true
fi
echo "[*] 安装 Python 依赖..."
python3 -m pip install -r requirements.txt
python3 - <<'PY'
mods = ["aiofiles", "httpx", "ujson", "vdf", "colorama", "PIL", "pymysql"]
missing = []
for m in mods:
    try:
        __import__(m)
    except ImportError:
        missing.append(m)
if missing:
    raise SystemExit("缺少模块: " + ", ".join(missing))
print("[OK] 依赖检查通过")
# 模拟 Web 服务启动时的导入链
import box_service  # noqa: F401
print("[OK] Web 服务模块可加载")
PY
