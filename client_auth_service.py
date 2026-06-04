"""游戏盒子客户端用户：注册、登录、VIP。"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from platform_utils import backup_json, file_lock, validate_password

try:
    from database import DOC_CLIENT, get_store
except ImportError:
    DOC_CLIENT = "client"

    def get_store():
        return None

CLIENT_DB_PATH = Path("./client_db.json")
BOX_SESSION_PATH = Path("./box_session.json")


class ClientAuthService:
    def __init__(self, db_path: Path = CLIENT_DB_PATH):
        from database import ensure_database_bootstrapped

        ensure_database_bootstrapped()
        self.db_path = db_path
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        store = get_store()
        if store:
            self._data = store.get(DOC_CLIENT)
            if not self._data:
                self._data = {}
        elif self.db_path.exists():
            try:
                self._data = json.loads(self.db_path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        else:
            self._data = {}
        if "users" not in self._data:
            self._data = {"users": {}, "sessions": {}, "used_vip_codes": []}
        if "sessions" not in self._data:
            self._data["sessions"] = {}
        if "used_vip_codes" not in self._data:
            self._data["used_vip_codes"] = []
        self.save()

    def save(self) -> None:
        store = get_store()
        if store:
            store.set(DOC_CLIENT, self._data)
            return
        with file_lock(self.db_path):
            backup_json(self.db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return digest.hex()

    def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        name = username.strip().lower()
        for raw in self._data.get("users", {}).values():
            if str(raw.get("username", "")).lower() == name:
                return raw
        return None

    def _user_public(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        vip, expires = self._vip_status(raw)
        return {
            "id": str(raw.get("id", "")),
            "username": str(raw.get("username", "")),
            "display_name": str(raw.get("display_name") or raw.get("username", "")),
            "vip": vip,
            "vip_expires_at": expires,
            "created_at": str(raw.get("created_at", "")),
        }

    def _vip_status(self, raw: Dict[str, Any]) -> tuple[bool, str]:
        if not raw.get("vip"):
            exp = str(raw.get("vip_expires_at", "") or "")
            if exp:
                try:
                    if datetime.now() <= datetime.strptime(exp, "%Y-%m-%d %H:%M:%S"):
                        return True, exp
                except ValueError:
                    pass
            return False, exp
        exp = str(raw.get("vip_expires_at", "") or "")
        if not exp:
            return True, ""
        try:
            if datetime.now() <= datetime.strptime(exp, "%Y-%m-%d %H:%M:%S"):
                return True, exp
        except ValueError:
            return True, exp
        raw["vip"] = False
        return False, exp

    def is_vip(self, user_id: str) -> bool:
        raw = self._data.get("users", {}).get(user_id)
        if not raw or not raw.get("enabled", True):
            return False
        ok, _ = self._vip_status(raw)
        return ok

    def register(self, username: str, password: str, display_name: str = "") -> Dict[str, Any]:
        username = username.strip()
        if len(username) < 3:
            raise ValueError("用户名至少 3 个字符")
        validate_password(password)
        if self.find_by_username(username):
            raise ValueError("用户名已存在")
        uid = str(uuid.uuid4())
        salt = secrets.token_hex(16)
        self._data.setdefault("users", {})[uid] = {
            "id": uid,
            "username": username,
            "display_name": display_name.strip() or username,
            "password_hash": self._hash_password(password, salt),
            "salt": salt,
            "vip": False,
            "vip_expires_at": "",
            "enabled": True,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.save()
        return self._user_public(self._data["users"][uid])

    def login(self, username: str, password: str) -> Dict[str, Any]:
        raw = self.find_by_username(username)
        if not raw:
            raise ValueError("用户名或密码错误")
        if not raw.get("enabled", True):
            raise ValueError("账号已禁用")
        salt = str(raw.get("salt", ""))
        if self._hash_password(password, salt) != raw.get("password_hash"):
            raise ValueError("用户名或密码错误")
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        self._data.setdefault("sessions", {})[token] = {
            "user_id": raw["id"],
            "expires_at": expires,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.save()
        self.save_session_token(token)
        user = self._user_public(raw)
        return {"ok": True, "token": token, "user": user, "expires_at": expires}

    def logout(self, token: str) -> None:
        sessions = self._data.get("sessions", {})
        if token in sessions:
            del sessions[token]
            self.save()
        if BOX_SESSION_PATH.exists():
            try:
                BOX_SESSION_PATH.unlink()
            except OSError:
                pass

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        sess = self._data.get("sessions", {}).get(token)
        if not sess:
            return None
        expires = str(sess.get("expires_at", ""))
        try:
            if datetime.now() > datetime.strptime(expires, "%Y-%m-%d %H:%M:%S"):
                del self._data["sessions"][token]
                self.save()
                return None
        except ValueError:
            pass
        raw = self._data.get("users", {}).get(str(sess.get("user_id", "")))
        if not raw or not raw.get("enabled", True):
            return None
        return self._user_public(raw)

    def activate_vip(self, user_id: str, code: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = self._data.get("users", {}).get(user_id)
        if not raw:
            raise ValueError("用户不存在")
        code = str(code or "").strip().upper()
        if not code:
            raise ValueError("请输入 VIP 激活码")
        used = {str(x).upper() for x in self._data.get("used_vip_codes", [])}
        if code in used:
            raise ValueError("该激活码已被使用")

        cfg = config or {}
        vip_codes = cfg.get("Vip_Codes") or {}
        if isinstance(vip_codes, list):
            vip_codes = {str(c).upper(): int(cfg.get("Vip_Code_Days", 30)) for c in vip_codes}
        else:
            vip_codes = {str(k).upper(): int(v) for k, v in vip_codes.items()}

        if code not in vip_codes:
            raise ValueError("激活码无效")

        days = int(vip_codes[code])
        now = datetime.now()
        base = now
        exp_str = str(raw.get("vip_expires_at", "") or "")
        if exp_str:
            try:
                old_exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                if old_exp > now:
                    base = old_exp
            except ValueError:
                pass
        if days <= 0:
            raw["vip"] = True
            raw["vip_expires_at"] = ""
        else:
            raw["vip"] = False
            raw["vip_expires_at"] = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        self._data.setdefault("used_vip_codes", []).append(code)
        self._data["used_vip_codes"] = self._data["used_vip_codes"][:5000]
        self.save()
        return self._user_public(raw)

    def grant_vip_days(self, user_id: str, days: int) -> Dict[str, Any]:
        """延长 VIP（如 CDK 激活成功后赠送）。"""
        raw = self._data.get("users", {}).get(user_id)
        if not raw:
            raise ValueError("用户不存在")
        days = int(days)
        now = datetime.now()
        base = now
        exp_str = str(raw.get("vip_expires_at", "") or "")
        if exp_str:
            try:
                old_exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                if old_exp > now:
                    base = old_exp
            except ValueError:
                pass
        if days <= 0:
            raw["vip"] = True
            raw["vip_expires_at"] = ""
        else:
            raw["vip"] = False
            raw["vip_expires_at"] = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        self.save()
        return self._user_public(raw)

    def save_session_token(self, token: str) -> None:
        try:
            BOX_SESSION_PATH.write_text(json.dumps({"token": token}), encoding="utf-8")
        except OSError:
            pass

    def load_session_token(self) -> str:
        if not BOX_SESSION_PATH.exists():
            return ""
        try:
            data = json.loads(BOX_SESSION_PATH.read_text(encoding="utf-8"))
            return str(data.get("token", ""))
        except Exception:
            return ""
