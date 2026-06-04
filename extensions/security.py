"""安全：2FA / IP 白名单 / 敏感操作确认。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Any, Dict, List, Optional

from extensions.store import load_extensions, save_extensions


def _security() -> Dict[str, Any]:
    return load_extensions().get("security", {})


def update_security_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_extensions()
    sec = data.setdefault("security", {})
    sec.update(patch)
    save_extensions(data)
    return sec


def check_ip_allowed(ip: str) -> bool:
    sec = _security()
    if not sec.get("ip_whitelist_enabled"):
        return True
    whitelist: List[str] = sec.get("ip_whitelist") or []
    if not whitelist:
        return True
    return ip in whitelist or any(ip.startswith(w.rstrip("*")) for w in whitelist if w.endswith("*"))


def _totp_code(secret: str, for_time: Optional[int] = None) -> str:
    """RFC 6238 TOTP（30 秒窗口）。"""
    t = (for_time or int(time.time())) // 30
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    msg = struct.pack(">Q", t)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1000000).zfill(6)


def setup_2fa(user_id: str) -> Dict[str, str]:
    secret = base64.b32encode(secrets.token_bytes(20)).decode("utf-8").replace("=", "")
    data = load_extensions()
    users = data.setdefault("security", {}).setdefault("users_2fa", {})
    users[user_id] = {"secret": secret, "enabled": False, "pending": True}
    save_extensions(data)
    return {"secret": secret, "otpauth": f"otpauth://totp/CSSteam:{user_id}?secret={secret}&issuer=CSSteam"}


def enable_2fa(user_id: str, code: str) -> bool:
    data = load_extensions()
    entry = data.get("security", {}).get("users_2fa", {}).get(user_id)
    if not entry:
        raise ValueError("请先获取 2FA 密钥")
    if not verify_2fa(user_id, code, entry.get("secret", "")):
        raise ValueError("验证码错误")
    entry["enabled"] = True
    entry["pending"] = False
    save_extensions(data)
    return True


def disable_2fa(user_id: str) -> None:
    data = load_extensions()
    users = data.get("security", {}).get("users_2fa", {})
    users.pop(user_id, None)
    save_extensions(data)


def is_2fa_enabled(user_id: str) -> bool:
    entry = _security().get("users_2fa", {}).get(user_id, {})
    return bool(entry.get("enabled"))


def verify_2fa(user_id: str, code: str, secret: str = "") -> bool:
    if not secret:
        entry = _security().get("users_2fa", {}).get(user_id, {})
        secret = str(entry.get("secret", ""))
    if not secret or not code:
        return False
    code = str(code).strip()
    now = int(time.time())
    for drift in (-1, 0, 1):
        if secrets.compare_digest(_totp_code(secret, now + drift * 30), code):
            return True
    return False


def require_confirm_password() -> bool:
    return bool(_security().get("require_confirm_password", True))
