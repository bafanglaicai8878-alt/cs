"""开放 REST API Key（与 steamfn 模式一致：密钥绑定代理账号）。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from extensions.store import load_extensions, save_extensions


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _admin():
    from admin_service import AdminService

    return AdminService()


def build_api_docs(api_base: str, api_key: str = "你的API密钥") -> Dict[str, str]:
    """生成与 steamfn 一致的请求示例（api_key 放在 JSON 请求体）。"""
    base = api_base.rstrip("/")
    endpoint = f"{base}/api2/cdkeys/generate"
    key = api_key or "你的API密钥"
    curl = (
        f'curl -X POST "{endpoint}" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f"  -d '{{\n"
        f'    "api_key": "{key}",\n'
        f'    "appid": 730,\n'
        f'    "quantity": 1,\n'
        f'    "generation_mode": 0,\n'
        f'    "notes": "API测试"\n'
        f"  }}'"
    )
    return {
        "auth_mode": "api_key_in_body",
        "generate_endpoint": endpoint,
        "generate_curl": curl,
        "list_curl": (
            f'curl "{base}/api/v1/cdk/list?limit=20&filter=unused" \\\n'
            f'  -H "Content-Type: application/json" \\\n'
            f"  -d '{{\"api_key\": \"{key}\"}}'"
        ),
        "status_curl": (
            f'curl "{base}/api/v1/cdk/status?cdk=XXXX-XXXX-XXXX-XXXX&api_key={key}"'
        ),
        "games_curl": (
            f'curl "{base}/api/v1/games?q=cs2&api_key={key}"'
        ),
    }


def get_agent_key_info(owner_id: str) -> Optional[Dict[str, Any]]:
    adm = _admin()
    key = adm.get_agent_api_key(owner_id)
    if not key:
        return None
    raw = adm.get_raw_user(owner_id) or {}
    return {
        "id": owner_id,
        "owner_id": owner_id,
        "api_key": key,
        "prefix": key[:8] + "…" + key[-4:] if len(key) > 12 else key,
        "created_at": raw.get("api_key_created_at", ""),
        "enabled": True,
    }


def get_or_create_agent_api_key(owner_id: str, username: str = "") -> Dict[str, Any]:
    del username
    info = get_agent_key_info(owner_id)
    if info:
        return {**info, "key": info.get("api_key")}
    return {"key": None, "api_key": None}


def regenerate_agent_api_key(owner_id: str, username: str = "") -> Dict[str, Any]:
    del username
    key = _admin().generate_agent_api_key(owner_id)
    return {"key": key, "api_key": key}


def clear_agent_api_key(owner_id: str) -> None:
    _admin().clear_agent_api_key(owner_id)


def create_api_key(name: str, owner_id: str, scopes: Optional[List[str]] = None) -> Dict[str, Any]:
    del name, scopes
    key = _admin().generate_agent_api_key(owner_id)
    return {"id": owner_id, "key": key, "api_key": key}


def list_api_keys(owner_id: str = "") -> List[Dict[str, Any]]:
    info = get_agent_key_info(owner_id) if owner_id else None
    return [info] if info else []


def revoke_api_key(key_id: str, owner_id: str = "") -> bool:
    del key_id
    if owner_id:
        clear_agent_api_key(owner_id)
        return True
    return False


def touch_api_key(_key_id: str) -> None:
    return


def verify_api_key(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    token = raw.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    raw_user = _admin().find_by_api_key(token)
    if raw_user:
        return {
            "id": str(raw_user.get("id", "")),
            "owner_id": str(raw_user.get("id", "")),
            "name": str(raw_user.get("username", "")),
            "enabled": True,
            "scopes": ["read", "cdk", "games"],
        }

    if not token.startswith("csk_"):
        return None
    h = _hash_key(token)
    for entry in load_extensions().get("api_keys", {}).values():
        if entry.get("hash") == h and entry.get("enabled"):
            return dict(entry)
    return None
