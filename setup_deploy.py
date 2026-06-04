"""一键部署：合并配置、初始化数据库、同步 CDK 密钥。"""

from __future__ import annotations

import json
import secrets
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"
CDK_DB_PATH = ROOT / "cdk_db.json"
CDK_EXAMPLE_PATH = ROOT / "cdk_db.example.json"

WEAK_SECRETS = {"", "请改成随机字符串", "cai-box-cdk-secret-change-me"}


def detect_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def merge_config(write: bool = True) -> Tuple[Dict[str, Any], str]:
    cfg = load_json(CONFIG_PATH)
    if not cfg and EXAMPLE_PATH.exists():
        cfg = load_json(EXAMPLE_PATH)

    server = cfg.setdefault("Server", {})
    port = int(server.get("port") or 8787)
    host = str(server.get("host") or "0.0.0.0")
    lan = detect_lan_ip()
    public = str(server.get("public_url") or cfg.get("Box_Server_URL") or "").strip()
    if not public or "你的公网" in public or "你的IP" in public:
        public = f"http://{lan}:{port}"
    from platform_utils import normalize_public_url

    public, cmd_base = normalize_public_url(public, port)

    server["host"] = host
    server["port"] = port
    server["public_url"] = public

    db = cfg.setdefault("Database", {})
    if db.get("enabled") is not False:
        db["enabled"] = True
    db.setdefault("path", "data/app.db")

    cfg["Box_Server_URL"] = public
    cfg.setdefault("Box_Vip_Days_Per_CDK", 30)
    cfg.setdefault("Vip_Codes", {"BOX-VIP-30": 30, "BOX-VIP-FOREVER": 0})
    cfg.setdefault("Force_Unlocker", cfg.get("Force_Unlocker") or "steamtools")
    cfg.setdefault("Catalog_Merge_On_Sync", True)
    cfg.setdefault("Catalog_Auto_Sync_Enabled", True)
    cfg.setdefault("Catalog_Auto_Sync_Hours", 24)
    cfg.setdefault("Catalog_Github_Max_Pages", 0)

    from github_tokens import normalize_github_tokens

    tokens = normalize_github_tokens(cfg)
    cfg["Github_Personal_Tokens"] = tokens
    if tokens:
        cfg["Github_Personal_Token"] = tokens[0]

    db.setdefault("engine", db.get("engine") or "sqlite")
    if str(db.get("engine", "")).lower() in ("mysql", "mariadb"):
        ex_db = load_json(EXAMPLE_PATH).get("Database", {}) if EXAMPLE_PATH.exists() else {}
        for k in ("host", "port", "user", "password", "database"):
            db.setdefault(k, ex_db.get(k, ""))

    cdk = cfg.setdefault("CDK", {})
    cdk.setdefault("enabled", True)
    cdk.setdefault("one_time_use", True)
    if str(cdk.get("secret", "")).strip() in WEAK_SECRETS:
        cdk["secret"] = secrets.token_hex(16)

    if sys.platform != "win32":
        ensure_server_steam_sandbox(cfg)

    if write:
        save_json(CONFIG_PATH, cfg)
    return cfg, public


def ensure_cdk_db(cfg: Dict[str, Any]) -> None:
    secret = str(cfg.get("CDK", {}).get("secret", ""))
    from database import DOC_CDK, ensure_database_bootstrapped, get_store

    ensure_database_bootstrapped()
    store = get_store()
    if store:
        data = store.get(DOC_CDK)
        if not data:
            data = {"settings": {"one_time_use": True, "secret": secret}, "keys": {}}
        settings = data.setdefault("settings", {})
        if str(settings.get("secret", "")).strip() in WEAK_SECRETS:
            settings["secret"] = secret
        settings.setdefault("one_time_use", cfg.get("CDK", {}).get("one_time_use", True))
        store.set(DOC_CDK, data)
        return
    if not CDK_DB_PATH.exists():
        if CDK_EXAMPLE_PATH.exists():
            shutil.copy(CDK_EXAMPLE_PATH, CDK_DB_PATH)
        else:
            save_json(CDK_DB_PATH, {"settings": {"one_time_use": True, "secret": secret}, "keys": {}})
    data = load_json(CDK_DB_PATH)
    settings = data.setdefault("settings", {})
    if str(settings.get("secret", "")).strip() in WEAK_SECRETS:
        settings["secret"] = secret
    settings.setdefault("one_time_use", cfg.get("CDK", {}).get("one_time_use", True))
    save_json(CDK_DB_PATH, data)


def init_database_store() -> bool:
    from database import ensure_database_bootstrapped, get_store, is_database_enabled, migrate_json_files_to_store

    ensure_database_bootstrapped()
    if not is_database_enabled():
        return False
    store = get_store()
    if store:
        migrate_json_files_to_store(store)
    return True


def ensure_server_steam_sandbox(cfg: Dict[str, Any]) -> Path:
    """Linux 无界面服务器：用本地目录模拟 Steam，供 CDK 兑换生成插件。"""
    sandbox = ROOT / "data" / "steam-sandbox"
    for rel in ("config/stplug-in", "config/depotcache", "depotcache"):
        (sandbox / rel).mkdir(parents=True, exist_ok=True)
    cfg["Custom_Steam_Path"] = str(sandbox.resolve())
    cfg["Force_Unlocker"] = cfg.get("Force_Unlocker") or "steamtools"
    return sandbox


def apply_deploy(verbose: bool = True) -> str:
    (ROOT / "data").mkdir(parents=True, exist_ok=True)

    if not CONFIG_PATH.exists() and EXAMPLE_PATH.exists():
        shutil.copy(EXAMPLE_PATH, CONFIG_PATH)

    cfg, public = merge_config(write=True)
    if verbose and sys.platform != "win32":
        print(f"  插件沙箱:  {cfg.get('Custom_Steam_Path', '')}")
    ensure_cdk_db(cfg)
    db_ok = init_database_store()

    from platform_utils import irm_cmd_base

    db_cfg = cfg.get("Database", {})
    if verbose:
        print("")
        print("=" * 52)
        print("  部署初始化完成")
        print("=" * 52)
        print(f"  对外地址:  {public}")
        if db_ok and str(db_cfg.get("engine", "")).lower() in ("mysql", "mariadb"):
            print(f"  数据库:    MySQL {db_cfg.get('database', '')}")
        elif db_ok:
            print(f"  数据库:    SQLite {db_cfg.get('path', 'data/app.db')}")
        else:
            print("  数据库:    未启用")
        print(f"  盒子连接:  Box_Server_URL = {public}")
        print("")
        print("  管理后台:  {}/admin/login".format(public))
        print("  默认账号:  admin / admin123  （请登录后修改）")
        print("  代理前台:  {}/portal".format(public))
        print("  用户激活:  irm {} | iex".format(irm_cmd_base(public)))
        print("=" * 52)
        print("")
    return public


def main() -> int:
    try:
        apply_deploy(verbose=True)
        return 0
    except Exception as e:
        print(f"部署初始化失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
