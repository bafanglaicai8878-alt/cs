#!/usr/bin/env bash
# 服务器上若启动失败，执行: bash fix_linux.sh
set -e
cd "$(dirname "$0")"

if grep -q 'if sys.platform == "win32":' backend.py 2>/dev/null; then
  echo "[OK] backend.py 已是 Linux 兼容版"
else
  echo "[*] 修补 backend.py（去掉 Windows 专用 winreg）..."
  python3 - <<'PY'
from pathlib import Path
p = Path("backend.py")
text = p.read_text(encoding="utf-8")
old = "import httpx\nimport winreg\nimport ujson"
new = "import httpx\nif sys.platform == \"win32\":\n    import winreg\nelse:\n    winreg = None  # type: ignore\nimport ujson"
if old not in text:
    raise SystemExit("backend.py 内容已变，请重新上传部署包")
text = text.replace(old, new, 1)
old2 = """            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\\Valve\\Steam')
            return Path(winreg.QueryValueEx(key, 'SteamPath')[0])"""
new2 = """            if sys.platform != "win32" or winreg is None:
                for candidate in (
                    Path.home() / ".steam" / "steam",
                    Path.home() / ".local" / "share" / "Steam",
                    Path("/usr/games/steam"),
                ):
                    if candidate.exists():
                        return candidate
                return None
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\\Valve\\Steam')
            return Path(winreg.QueryValueEx(key, 'SteamPath')[0])"""
if old2 not in text:
    raise SystemExit("get_steam_path 段落未找到，请重新上传部署包")
text = text.replace(old2, new2, 1)
p.write_text(text, encoding="utf-8")
print("[OK] backend.py 已修补")
PY
fi

bash install_deps.sh 2>/dev/null || {
  python3 -m pip install -r requirements.txt
}
python3 -c "import box_service; print('[OK] 可以启动 Web 服务')"
echo "请执行: bash 后台运行.sh"
