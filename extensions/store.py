"""扩展功能统一存储（MySQL/SQLite 文档或本地 JSON）。"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
EXT_PATH = ROOT / "extensions_data.json"
DOC_EXTENSIONS = "extensions"
_lock = threading.RLock()

DEFAULT_EXTENSIONS: Dict[str, Any] = {
    "payment": {
        "settings": {
            "epay_enabled": False,
            "epay_gateway": "https://pay.maihao.la",
            "epay_pid": "",
            "epay_md5_key": "",
            "epay_pay_type": "alipay",
            "epay_checkout_mode": "cashier",
            "epay_platform_public_key": "",
            "usdt_enabled": False,
            "usdt_address": "",
            "usdt_rate": 7.2,
            "callback_secret": "",
        },
        "orders": {},
    },
    "notifications": {
        "settings": {
            "email_enabled": False,
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "telegram_enabled": False,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "wecom_enabled": False,
            "wecom_webhook": "",
            "notify_recharge": True,
            "notify_withdraw": True,
            "notify_sync_fail": True,
        },
    },
    "security": {
        "ip_whitelist_enabled": False,
        "ip_whitelist": [],
        "require_confirm_password": True,
        "users_2fa": {},
    },
    "api_keys": {},
    "tenants": {
        "enabled": False,
        "sites": [],
    },
    "cdk_packages": [],
    "mall_settings": {
        "enabled": False,
        "title": "CDK 卡密商城",
        "description": "在线购买游戏激活码",
    },
    "activation_logs": [],
    "invoices": [],
    "sync_progress": {
        "running": False,
        "percent": 0,
        "message": "",
        "error": "",
        "updated_at": "",
    },
    "help_tutorial": {},
}


def _get_store():
    try:
        from database import get_store

        return get_store()
    except Exception:
        return None


def load_extensions() -> Dict[str, Any]:
    with _lock:
        store = _get_store()
        if store:
            data = store.get(DOC_EXTENSIONS)
            if data:
                return _merge_defaults(data)
        if EXT_PATH.exists():
            try:
                return _merge_defaults(json.loads(EXT_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        return deepcopy(DEFAULT_EXTENSIONS)


def save_extensions(data: Dict[str, Any]) -> None:
    with _lock:
        store = _get_store()
        if store:
            store.set(DOC_EXTENSIONS, data)
            return
        EXT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(DEFAULT_EXTENSIONS)
    for key, val in data.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            merged = deepcopy(out[key])
            merged.update(val)
            out[key] = merged
        else:
            out[key] = val
    return out


def update_section(section: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_extensions()
    cur = data.setdefault(section, {})
    if isinstance(cur, dict) and isinstance(patch, dict):
        cur.update(patch)
    else:
        data[section] = patch
    save_extensions(data)
    return data
