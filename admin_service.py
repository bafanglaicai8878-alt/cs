"""管理后台：用户、代理、登录会话、多级分润。"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from platform_utils import RATE_LIMITER, backup_json, file_lock, validate_password

try:
    from database import DOC_ADMIN, get_store, is_database_enabled
except ImportError:
    DOC_ADMIN = "admin"

    def get_store():
        return None

    def is_database_enabled():
        return False

ADMIN_DB_PATH = Path("./admin_db.json")
ROLES = ("superadmin", "agent")
MIN_WITHDRAW_AMOUNT = 500.0


@dataclass
class AdminUser:
    id: str
    username: str
    role: str
    display_name: str = ""
    enabled: bool = True
    parent_id: str = ""
    cdk_quota: int = 0
    cdk_generated: int = 0
    cdk_cost_price: float = 0.0
    balance: float = 0.0
    note: str = ""
    created_at: str = ""


class AdminService:
    def __init__(self, db_path: Path = ADMIN_DB_PATH):
        from database import ensure_database_bootstrapped

        ensure_database_bootstrapped()
        self.db_path = db_path
        self._data: Dict[str, Any] = {}
        self._billing_lock = threading.RLock()
        self.load()

    def _read_from_store(self) -> None:
        store = get_store()
        if store:
            data = store.get(DOC_ADMIN)
            self._data = data if isinstance(data, dict) and data else {}
        elif self.db_path.exists():
            try:
                self._data = json.loads(self.db_path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _ensure_structure(self, *, seed_if_empty: bool = False) -> bool:
        dirty = False
        if not isinstance(self._data, dict):
            self._data = {}
            dirty = True
        if "users" not in self._data:
            self._data["users"] = {}
            dirty = True
        if "sessions" not in self._data:
            self._data["sessions"] = {}
            dirty = True
        if "settings" not in self._data:
            self._data["settings"] = self._default_settings()
            dirty = True
        if "commission_logs" not in self._data:
            self._data["commission_logs"] = []
            dirty = True
        if "recharge_logs" not in self._data:
            self._data["recharge_logs"] = []
            dirty = True
        if "recharge_requests" not in self._data:
            self._data["recharge_requests"] = []
            dirty = True
        if "withdraw_requests" not in self._data:
            self._data["withdraw_requests"] = []
            dirty = True
        if "audit_logs" not in self._data:
            self._data["audit_logs"] = []
            dirty = True
        if "login_attempts" not in self._data:
            self._data["login_attempts"] = {}
            dirty = True
        if self._migrate_users():
            dirty = True
        if seed_if_empty and not self._data.get("users"):
            self._seed_default_admin()
            dirty = True
        return dirty

    def _refresh(self) -> None:
        """从存储读取最新数据（不写入，避免覆盖并发修改）。"""
        with self._billing_lock:
            self._read_from_store()
            self._ensure_structure(seed_if_empty=False)

    def load(self) -> None:
        with self._billing_lock:
            self._read_from_store()
            if self._ensure_structure(seed_if_empty=True):
                self._save_unsafe()

    def _migrate_users(self) -> bool:
        dirty = False
        base = float(self._data.get("settings", {}).get("base_cdk_price", 0.1))
        for raw in self._data.get("users", {}).values():
            if "balance" not in raw:
                raw["balance"] = 0.0
                dirty = True
            if "withdrawable_balance" not in raw:
                raw["withdrawable_balance"] = self._estimate_withdrawable_balance(str(raw.get("id", "")), raw)
                dirty = True
            if "cdk_cost_price" not in raw:
                raw["cdk_cost_price"] = base if raw.get("role") == "agent" else 0.0
                dirty = True
            else:
                price = float(raw.get("cdk_cost_price", base))
                if raw.get("cdk_cost_price") != price:
                    raw["cdk_cost_price"] = price
                    dirty = True
            if raw.get("role") == "agent" and not raw.get("invite_code"):
                raw["invite_code"] = self._generate_unique_invite_code()
                dirty = True
        return dirty

    def _estimate_withdrawable_balance(self, user_id: str, raw: Dict[str, Any]) -> float:
        if raw.get("role") != "agent":
            return 0.0
        earned = sum(
            round(float(x.get("amount", 0) or 0), 4)
            for x in self._data.get("commission_logs", [])
            if str(x.get("user_id", "")) == user_id
        )
        withdrawn = sum(
            round(float(x.get("amount", 0) or 0), 4)
            for x in self._data.get("withdraw_requests", [])
            if str(x.get("user_id", "")) == user_id and x.get("status") == "approved"
        )
        bal = round(float(raw.get("balance", 0) or 0), 4)
        return round(min(bal, max(0.0, earned - withdrawn)), 4)

    def _save_unsafe(self) -> None:
        store = get_store()
        if store:
            store.set(DOC_ADMIN, self._data)
            return
        with file_lock(self.db_path):
            backup_json(self.db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _merge_sessions_from_store(self, local: Dict[str, Any]) -> None:
        """写入前合并 DB 中的 session，避免 stale save 覆盖刚登录的 token。"""
        store = get_store()
        remote_sessions: Dict[str, Any] = {}
        if store:
            remote = store.get(DOC_ADMIN)
            if isinstance(remote, dict):
                remote_sessions = remote.get("sessions") or {}
        elif self.db_path.exists():
            try:
                remote = json.loads(self.db_path.read_text(encoding="utf-8"))
                if isinstance(remote, dict):
                    remote_sessions = remote.get("sessions") or {}
            except Exception:
                remote_sessions = {}
        if not remote_sessions:
            return
        merged = dict(local.get("sessions") or {})
        merged.update(remote_sessions)
        local["sessions"] = merged

    def save(self) -> None:
        with self._billing_lock:
            self._merge_sessions_from_store(self._data)
            self._save_unsafe()

    @contextmanager
    def _write_lock(self):
        """读-改-写同一锁内完成，避免并发覆盖或恢复已删数据。"""
        self._billing_lock.acquire()
        try:
            self._read_from_store()
            self._ensure_structure(seed_if_empty=False)
            yield
            self._save_unsafe()
        finally:
            self._billing_lock.release()

    @staticmethod
    def _default_settings() -> Dict[str, Any]:
        return {
            "site_name": "CS Steam 管理台",
            "session_hours": 24,
            "base_cdk_price": 0.1,
            "register_default_quota": 100,
            "cdk_default_expire_days": 0,
            "commission_max_levels": 0,
            "login_max_attempts": 8,
            "login_lock_minutes": 15,
            "register_per_ip_hour": 5,
            "redeem_rate_limit": 30,
            "announcement": "",
            "announcement_enabled": False,
        }

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return digest.hex()

    def _seed_default_admin(self) -> None:
        uid = str(uuid.uuid4())
        salt = secrets.token_hex(16)
        self._data["users"][uid] = {
            "id": uid,
            "username": "admin",
            "password_hash": self._hash_password("admin123", salt),
            "salt": salt,
            "role": "superadmin",
            "display_name": "超级管理员",
            "enabled": True,
            "parent_id": "",
            "cdk_generated": 0,
            "cdk_cost_price": 0.0,
            "balance": 0.0,
            "note": "默认账号，请尽快修改密码",
            "must_change_password": True,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _parse_user(self, raw: Dict[str, Any]) -> AdminUser:
        return AdminUser(
            id=str(raw.get("id", "")),
            username=str(raw.get("username", "")),
            role=str(raw.get("role", "agent")),
            display_name=str(raw.get("display_name", "")),
            enabled=bool(raw.get("enabled", True)),
            parent_id=str(raw.get("parent_id", "")),
            cdk_quota=int(raw.get("cdk_quota", 0)),
            cdk_generated=int(raw.get("cdk_generated", 0)),
            cdk_cost_price=float(raw.get("cdk_cost_price", 0)),
            balance=float(raw.get("balance", 0)),
            note=str(raw.get("note", "")),
            created_at=str(raw.get("created_at", "")),
        )

    def base_cdk_price(self) -> float:
        return float(self._data.get("settings", {}).get("base_cdk_price", 0.1))

    def get_effective_cost(self, user_id: str) -> float:
        raw = self._data.get("users", {}).get(user_id)
        if not raw:
            return self.base_cdk_price()
        if raw.get("role") == "superadmin":
            return self.base_cdk_price()
        return float(raw.get("cdk_cost_price", self.base_cdk_price()))

    def _user_public(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        u = self._parse_user(raw)
        item = {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "display_name": u.display_name,
            "enabled": u.enabled,
            "parent_id": u.parent_id,
            "cdk_generated": u.cdk_generated,
            "cdk_cost_price": round(u.cdk_cost_price, 4),
            "balance": round(u.balance, 4),
            "note": u.note,
            "created_at": u.created_at,
            "must_change_password": bool(raw.get("must_change_password", False)),
        }
        if u.role == "agent":
            item["sub_agent_count"] = self.count_sub_agents(u.id)
            item["pending_withdraw"] = round(self.pending_withdraw_total(u.id), 4)
            item["available_balance"] = round(self.available_balance(u.id), 4)
            item["withdrawable_balance"] = round(self.withdrawable_balance(u.id), 4)
            item["available_withdrawable_balance"] = round(self.available_withdrawable_balance(u.id), 4)
            item["credit_limit"] = round(float(raw.get("credit_limit") or 0), 2)
            item["available_with_credit"] = round(float(raw.get("balance") or 0) + float(raw.get("credit_limit") or 0), 4)
            api_key = str(raw.get("api_key", "") or "").strip()
            if api_key:
                item["api_key"] = api_key
                item["has_api_key"] = True
            else:
                item["has_api_key"] = False
            code = str(raw.get("invite_code", "") or "").upper()
            if code:
                item["invite_code"] = code
            icp = raw.get("invite_cost_price")
            if icp is not None:
                item["invite_cost_price"] = round(float(icp), 4)
        if u.parent_id:
            parent = self.get_raw_user(u.parent_id)
            if parent:
                item["parent_username"] = str(parent.get("username", ""))
                item["parent_display_name"] = str(parent.get("display_name", ""))
        item["is_sub_agent"] = bool(u.parent_id)
        return item

    def count_sub_agents(self, parent_id: str) -> int:
        return sum(
            1
            for u in self._data.get("users", {}).values()
            if u.get("role") == "agent" and u.get("parent_id") == parent_id
        )

    def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        username = username.strip().lower()
        for raw in self._data.get("users", {}).values():
            if str(raw.get("username", "")).lower() == username:
                return raw
        return None

    def find_by_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        key = str(api_key or "").strip()
        if not key:
            return None
        self._refresh()
        for raw in self._data.get("users", {}).values():
            if raw.get("role") != "agent" or not raw.get("enabled", True):
                continue
            if str(raw.get("api_key", "") or "").strip() == key:
                return raw
        return None

    def get_agent_api_key(self, user_id: str) -> str:
        raw = self.get_raw_user(user_id)
        if not raw:
            return ""
        return str(raw.get("api_key", "") or "").strip()

    def generate_agent_api_key(self, user_id: str) -> str:
        with self._write_lock():
            raw = self._data.get("users", {}).get(user_id)
            if not raw or raw.get("role") != "agent":
                raise ValueError("仅代理可生成 API 密钥")
            key = secrets.token_hex(16)
            raw["api_key"] = key
            raw["api_key_created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return key

    def clear_agent_api_key(self, user_id: str) -> None:
        with self._write_lock():
            raw = self._data.get("users", {}).get(user_id)
            if not raw or raw.get("role") != "agent":
                raise ValueError("仅代理可清除 API 密钥")
            raw.pop("api_key", None)
            raw.pop("api_key_created_at", None)

    def get_user(self, user_id: str) -> Optional[AdminUser]:
        raw = self._data.get("users", {}).get(user_id)
        return self._parse_user(raw) if raw else None

    def get_raw_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get("users", {}).get(user_id)

    def login(self, username: str, password: str, client_key: str = "") -> Dict[str, Any]:
        self._refresh()
        settings = self._data.get("settings", self._default_settings())
        lock_key = f"login:{client_key or username.strip().lower()}"
        max_attempts = int(settings.get("login_max_attempts", 8))
        lock_min = int(settings.get("login_lock_minutes", 15))
        ok, msg = RATE_LIMITER.allow(lock_key, max_attempts, lock_min * 60)
        if not ok:
            return {"ok": False, "message": msg}

        self._billing_lock.acquire()
        try:
            self._read_from_store()
            self._ensure_structure(seed_if_empty=False)
            raw = self.find_by_username(username)
            if not raw:
                return {"ok": False, "message": "用户名或密码错误"}
            if not raw.get("enabled", True):
                return {"ok": False, "message": "账号已禁用"}
            salt = str(raw.get("salt", ""))
            if self._hash_password(password, salt) != raw.get("password_hash"):
                return {"ok": False, "message": "用户名或密码错误"}

            hours = int(settings.get("session_hours", 24))
            token = secrets.token_urlsafe(32)
            expires = (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            self._data.setdefault("sessions", {})[token] = {
                "user_id": raw["id"],
                "expires_at": expires,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.audit_log(str(raw.get("id", "")), str(raw.get("username", "")), "login", "用户登录")
            self._save_unsafe()
            payload = {
                "ok": True,
                "token": token,
                "user": self._user_public(raw),
                "expires_at": expires,
                "must_change_password": bool(raw.get("must_change_password", False)),
            }
            payload["base_cdk_price"] = self.base_cdk_price()
            return payload
        finally:
            self._billing_lock.release()

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._write_lock():
            sessions = self._data.get("sessions", {})
            if token in sessions:
                del sessions[token]

    def verify_token(self, token: str) -> Optional[AdminUser]:
        if not token:
            return None
        self._refresh()
        sess = self._data.get("sessions", {}).get(token)
        if not sess:
            return None
        expires = str(sess.get("expires_at", ""))
        try:
            if datetime.now() > datetime.strptime(expires, "%Y-%m-%d %H:%M:%S"):
                with self._write_lock():
                    sessions = self._data.get("sessions", {})
                    if token in sessions:
                        del sessions[token]
                return None
        except ValueError:
            pass
        return self.get_user(str(sess.get("user_id", "")))

    def list_users(
        self,
        role: str = "",
        parent_id: str = "",
        *,
        top_level_only: bool = False,
    ) -> List[Dict[str, Any]]:
        self._refresh()
        items = []
        for raw in self._data.get("users", {}).values():
            if role and raw.get("role") != role:
                continue
            if top_level_only and raw.get("parent_id"):
                continue
            if parent_id and raw.get("parent_id") != parent_id:
                continue
            items.append(self._user_public(raw))
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return items

    def list_admin_accounts(self) -> List[Dict[str, Any]]:
        return self.list_users(role="superadmin")

    def list_top_agents(self) -> List[Dict[str, Any]]:
        return self.list_users(role="agent", top_level_only=True)

    def list_all_agents(self) -> List[Dict[str, Any]]:
        return self.list_users(role="agent")

    def list_sub_agents(self, parent_id: str) -> List[Dict[str, Any]]:
        return self.list_users(role="agent", parent_id=parent_id)

    def _generate_unique_invite_code(self) -> str:
        existing = {
            str(u.get("invite_code", "")).upper()
            for u in self._data.get("users", {}).values()
            if u.get("invite_code")
        }
        for _ in range(200):
            code = secrets.token_hex(4).upper()
            if code not in existing:
                return code
        return secrets.token_hex(8).upper()

    def find_by_invite_code(self, invite_code: str) -> Optional[Dict[str, Any]]:
        code = str(invite_code or "").strip().upper()
        if not code:
            return None
        for raw in self._data.get("users", {}).values():
            if str(raw.get("invite_code", "")).upper() == code:
                return raw
        return None

    def ensure_invite_code(self, user_id: str) -> str:
        raw = self._data.get("users", {}).get(user_id)
        if not raw:
            raise ValueError("用户不存在")
        if raw.get("role") != "agent":
            raise ValueError("仅代理可生成邀请码")
        if not raw.get("invite_code"):
            raw["invite_code"] = self._generate_unique_invite_code()
            self.save()
        return str(raw["invite_code"])

    def refresh_invite_code(self, user_id: str) -> str:
        raw = self._data.get("users", {}).get(user_id)
        if not raw or raw.get("role") != "agent":
            raise ValueError("仅代理可刷新邀请码")
        raw["invite_code"] = self._generate_unique_invite_code()
        self.save()
        return str(raw["invite_code"])

    def get_invite_public(self, invite_code: str) -> Optional[Dict[str, Any]]:
        raw = self.find_by_invite_code(invite_code)
        if not raw or raw.get("role") != "agent" or not raw.get("enabled", True):
            return None
        settings = self._data.get("settings", self._default_settings())
        inviter_id = str(raw.get("id", ""))
        min_cost = round(self.get_effective_cost(inviter_id) + 0.01, 4)
        if raw.get("invite_cost_price") not in (None, ""):
            register_cost = self._validate_sub_price(inviter_id, float(raw.get("invite_cost_price")))
        else:
            register_cost = min_cost
        return {
            "invite_code": str(raw.get("invite_code", "")).upper(),
            "inviter_username": str(raw.get("username", "")),
            "inviter_name": str(raw.get("display_name") or raw.get("username", "")),
            "default_quota": int(settings.get("register_default_quota", 100)),
            "register_cost_price": register_cost,
            "min_cost_price": min_cost,
        }

    def get_agent_invite_info(self, user_id: str) -> Dict[str, Any]:
        raw = self.get_raw_user(user_id)
        if not raw or raw.get("role") != "agent":
            raise ValueError("仅代理可查看邀请信息")
        code = self.ensure_invite_code(user_id)
        min_cost = round(self.get_effective_cost(user_id) + 0.01, 4)
        if raw.get("invite_cost_price") not in (None, ""):
            register_cost = self._validate_sub_price(user_id, float(raw.get("invite_cost_price")))
        else:
            register_cost = min_cost
        return {
            "invite_code": code,
            "inviter_name": str(raw.get("display_name") or raw.get("username", "")),
            "invite_cost_price": register_cost,
            "min_cost_price": min_cost,
            "my_cost_price": round(float(raw.get("cdk_cost_price", 0)), 4),
            "default_quota": int(self._data.get("settings", {}).get("register_default_quota", 100)),
        }

    def set_invite_cost_price(self, user_id: str, cdk_cost_price: float) -> Dict[str, Any]:
        raw = self.get_raw_user(user_id)
        if not raw or raw.get("role") != "agent":
            raise ValueError("仅代理可设置邀请成本价")
        raw["invite_cost_price"] = self._validate_sub_price(user_id, cdk_cost_price)
        self.save()
        return self.get_agent_invite_info(user_id)

    def register_by_invite(
        self,
        username: str,
        password: str,
        invite_code: str,
        display_name: str = "",
    ) -> Dict[str, Any]:
        code = str(invite_code or "").strip().upper()
        if not code:
            raise ValueError("请填写邀请码")
        inviter = self.find_by_invite_code(code)
        if not inviter or inviter.get("role") != "agent":
            raise ValueError("邀请码无效")
        if not inviter.get("enabled", True):
            raise ValueError("邀请人账号已禁用，无法注册")
        settings = self._data.get("settings", self._default_settings())
        default_quota = int(settings.get("register_default_quota", 100))
        inviter_id = str(inviter.get("id", ""))
        if inviter.get("invite_cost_price") not in (None, ""):
            default_price = self._validate_sub_price(inviter_id, float(inviter.get("invite_cost_price")))
        else:
            default_price = round(self.get_effective_cost(inviter_id) + 0.01, 4)
        return self.create_user(
            username=username,
            password=password,
            role="agent",
            display_name=display_name,
            parent_id=str(inviter.get("id", "")),
            cdk_cost_price=default_price,
            note=f"邀请码注册 · 上级 {inviter.get('username', '')}",
            operator_id=str(inviter.get("id", "")),
        )

    def _validate_sub_price(self, parent_id: str, sub_price: float) -> float:
        sub_price = round(float(sub_price), 4)
        if sub_price <= 0:
            raise ValueError("下级 CDK 单价必须大于 0")
        parent_cost = self.get_effective_cost(parent_id) if parent_id else self.base_cdk_price()
        if sub_price < parent_cost:
            raise ValueError(f"下级单价不能低于您的成本价 {parent_cost}")
        return sub_price

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "agent",
        display_name: str = "",
        parent_id: str = "",
        cdk_quota: int = 100,
        cdk_cost_price: Optional[float] = None,
        note: str = "",
        operator_id: str = "",
    ) -> Dict[str, Any]:
        with self._write_lock():
            return self._create_user_unsafe(
                username=username,
                password=password,
                role=role,
                display_name=display_name,
                parent_id=parent_id,
                cdk_quota=cdk_quota,
                cdk_cost_price=cdk_cost_price,
                note=note,
                operator_id=operator_id,
            )

    def _create_user_unsafe(
        self,
        username: str,
        password: str,
        role: str = "agent",
        display_name: str = "",
        parent_id: str = "",
        cdk_quota: int = 100,
        cdk_cost_price: Optional[float] = None,
        note: str = "",
        operator_id: str = "",
    ) -> Dict[str, Any]:
        username = username.strip()
        if len(username) < 3:
            raise ValueError("用户名至少 3 个字符")
        validate_password(password)
        if role not in ROLES:
            raise ValueError("角色无效")
        if self.find_by_username(username):
            raise ValueError("用户名已存在")

        if role == "agent":
            if parent_id:
                parent = self.get_raw_user(parent_id)
                if not parent or parent.get("role") not in ROLES:
                    raise ValueError("上级代理不存在")
                if operator_id and operator_id != parent_id and parent.get("role") != "superadmin":
                    if parent_id != operator_id:
                        raise ValueError("只能在自己的团队下创建下级")
                price = self._validate_sub_price(
                    parent_id,
                    cdk_cost_price if cdk_cost_price is not None else self.get_effective_cost(parent_id) + 0.01,
                )
            else:
                price = round(float(cdk_cost_price if cdk_cost_price is not None else self.base_cdk_price()), 4)
                if price < self.base_cdk_price():
                    raise ValueError(f"一级代理单价不能低于基础价 {self.base_cdk_price()}")
        else:
            price = 0.0
            parent_id = ""

        uid = str(uuid.uuid4())
        salt = secrets.token_hex(16)
        user_row: Dict[str, Any] = {
            "id": uid,
            "username": username,
            "password_hash": self._hash_password(password, salt),
            "salt": salt,
            "role": role,
            "display_name": display_name or username,
            "enabled": True,
            "parent_id": parent_id if role == "agent" else "",
            "cdk_generated": 0,
            "cdk_cost_price": price,
            "balance": 0.0,
            "withdrawable_balance": 0.0,
            "note": note,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if role == "agent":
            user_row["invite_code"] = self._generate_unique_invite_code()
        self._data.setdefault("users", {})[uid] = user_row
        return self._user_public(self._data["users"][uid])

    def create_sub_agent(
        self,
        operator_id: str,
        username: str,
        password: str,
        cdk_cost_price: float,
        cdk_quota: int = 100,
        display_name: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        operator = self.get_raw_user(operator_id)
        if not operator:
            raise ValueError("操作者不存在")
        if operator.get("role") not in ROLES:
            raise ValueError("无权邀请代理")
        parent_id = operator_id if operator.get("role") == "agent" else ""
        return self.create_user(
            username=username,
            password=password,
            role="agent",
            display_name=display_name,
            parent_id=parent_id,
            cdk_quota=cdk_quota,
            cdk_cost_price=cdk_cost_price,
            note=note,
            operator_id=operator_id,
        )

    def update_user(self, user_id: str, operator_id: str = "", **fields: Any) -> Dict[str, Any]:
        with self._write_lock():
            raw = self._data.get("users", {}).get(user_id)
            if not raw:
                raise ValueError("用户不存在")
            operator = self._data.get("users", {}).get(operator_id) if operator_id else None

            if operator and operator.get("role") == "agent":
                if raw.get("parent_id") != operator_id and user_id != operator_id:
                    raise ValueError("只能管理自己的下级代理")

            if "display_name" in fields:
                raw["display_name"] = str(fields["display_name"])
            if "enabled" in fields:
                raw["enabled"] = bool(fields["enabled"])
            if "note" in fields:
                raw["note"] = str(fields["note"])
            if "cdk_quota" in fields and raw.get("role") == "agent":
                raw["cdk_quota"] = 0
            if "cdk_cost_price" in fields and raw.get("role") == "agent":
                parent_id = str(raw.get("parent_id", ""))
                if operator and operator.get("role") == "agent" and parent_id != operator_id:
                    raise ValueError("只能修改直属下级的单价")
                old_price = round(float(raw.get("cdk_cost_price", 0) or 0), 4)
                new_price = round(float(fields["cdk_cost_price"]), 4)
                if new_price != old_price:
                    raw["cdk_cost_price"] = self._validate_sub_price(parent_id, new_price)
            if "password" in fields and fields["password"]:
                pwd = str(fields["password"])
                validate_password(pwd)
                salt = secrets.token_hex(16)
                raw["salt"] = salt
                raw["password_hash"] = self._hash_password(pwd, salt)
                raw["must_change_password"] = False
                self.audit_log(operator_id or user_id, str(raw.get("username", "")), "change_password", "修改密码")
            return self._user_public(raw)

    def update_settings(self, **fields: Any) -> Dict[str, Any]:
        with self._write_lock():
            settings = self._data.setdefault("settings", self._default_settings())
            if "base_cdk_price" in fields:
                try:
                    price = round(float(fields["base_cdk_price"]), 4)
                except (TypeError, ValueError):
                    raise ValueError("基础单价格式无效")
                if price <= 0:
                    raise ValueError("基础单价必须大于 0")
                settings["base_cdk_price"] = price
            if "site_name" in fields:
                settings["site_name"] = str(fields["site_name"])
            if "register_default_quota" in fields:
                settings["register_default_quota"] = 0
            if "cdk_default_expire_days" in fields:
                settings["cdk_default_expire_days"] = max(0, int(fields["cdk_default_expire_days"]))
            if "commission_max_levels" in fields:
                settings["commission_max_levels"] = max(0, int(fields["commission_max_levels"]))
            if "login_max_attempts" in fields:
                settings["login_max_attempts"] = max(1, int(fields["login_max_attempts"]))
            if "login_lock_minutes" in fields:
                settings["login_lock_minutes"] = max(1, int(fields["login_lock_minutes"]))
            if "register_per_ip_hour" in fields:
                settings["register_per_ip_hour"] = max(0, int(fields["register_per_ip_hour"]))
            if "announcement" in fields:
                settings["announcement"] = str(fields["announcement"] or "")
            if "announcement_enabled" in fields:
                settings["announcement_enabled"] = bool(fields["announcement_enabled"])
            return dict(settings)

    def delete_user(self, user_id: str, operator_id: str) -> None:
        with self._write_lock():
            if user_id == operator_id:
                raise ValueError("不能删除当前登录账号")
            raw = self._data.get("users", {}).get(user_id)
            if not raw:
                raise ValueError("用户不存在")
            operator = self._data.get("users", {}).get(operator_id)
            if operator and operator.get("role") == "agent":
                if raw.get("parent_id") != operator_id:
                    raise ValueError("只能删除自己的下级代理")
            subs = [
                u for u in self._data.get("users", {}).values()
                if u.get("role") == "agent" and u.get("parent_id") == user_id
            ]
            if subs:
                raise ValueError("请先删除或转移该代理的下级")
            if raw.get("role") == "superadmin":
                admins = [
                    u for u in self._data.get("users", {}).values()
                    if u.get("role") == "superadmin" and u.get("enabled")
                ]
                if len(admins) <= 1:
                    raise ValueError("至少保留一个超级管理员")
            del self._data["users"][user_id]
            sessions = self._data.get("sessions", {})
            for tok, sess in list(sessions.items()):
                if str(sess.get("user_id", "")) == user_id:
                    del sessions[tok]

    def check_cdk_quota(self, user: AdminUser, count: int = 1) -> None:
        return

    def check_pre_generate_quota(self, user: AdminUser, count: int = 1, pending_count: int = 0) -> None:
        if user.role != "agent":
            return
        if count <= 0:
            return
        unit_cost = self.cdk_generation_cost(user, 1)
        if unit_cost <= 0:
            return
        available = self.available_balance(user.id)
        max_pre_generate = int(available / unit_cost) * 10
        needed = max(0, int(pending_count)) + int(count)
        if needed > max_pre_generate:
            raise ValueError(
                f"预生成额度不足（余额可预生成 {max_pre_generate} 张，待激活 {int(pending_count)} 张，本次 {int(count)} 张）"
            )

    def cdk_generation_cost(self, user: AdminUser, count: int = 1) -> float:
        if user.role != "agent" or count <= 0:
            return 0.0
        return round(float(user.cdk_cost_price) * count, 4)

    def check_cdk_balance(self, user: AdminUser, count: int = 1, reserve_pending: int = 0) -> None:
        if user.role != "agent":
            return
        total = max(0, int(count)) + max(0, int(reserve_pending))
        if total <= 0:
            return
        cost = self.cdk_generation_cost(user, total)
        if cost <= 0:
            return
        available = self.available_balance(user.id)
        if available < cost:
            pending_wd = self.pending_withdraw_total(user.id)
            bal = round(float(user.balance), 4)
            raise ValueError(
                f"可用余额不足（账面 {bal:.2f}，待审提现 {pending_wd:.2f}，可用 {available:.2f}，需要 {cost:.2f}）"
            )

    def check_cdk_quota_for_pending(self, user: AdminUser, count: int = 1, pending_count: int = 0) -> None:
        return

    def check_agent_generation(
        self,
        user: AdminUser,
        count: int = 1,
        billing_mode: str = "immediate",
        pending_count: int = 0,
    ) -> None:
        if user.role != "agent":
            return
        mode = billing_mode if billing_mode in ("immediate", "on_activate") else "immediate"
        if mode == "on_activate":
            self.check_pre_generate_quota(user, count, pending_count)
        else:
            self.check_cdk_balance(user, count, 0)

    def pending_withdraw_total(self, user_id: str) -> float:
        total = 0.0
        for req in self._data.get("withdraw_requests", []):
            if str(req.get("user_id", "")) != user_id:
                continue
            if req.get("status") != "pending":
                continue
            total += round(float(req.get("amount", 0)), 4)
        return round(total, 4)

    def available_balance(self, user_id: str) -> float:
        raw = self.get_raw_user(user_id)
        if not raw:
            return 0.0
        bal = round(float(raw.get("balance", 0)), 4)
        frozen = self.pending_withdraw_total(user_id)
        return round(max(0.0, bal - frozen), 4)

    def withdrawable_balance(self, user_id: str) -> float:
        raw = self.get_raw_user(user_id)
        if not raw or raw.get("role") != "agent":
            return 0.0
        bal = round(float(raw.get("balance", 0) or 0), 4)
        withdrawable = round(float(raw.get("withdrawable_balance", 0) or 0), 4)
        return round(max(0.0, min(bal, withdrawable)), 4)

    def available_withdrawable_balance(self, user_id: str) -> float:
        pending = self.pending_withdraw_total(user_id)
        return round(max(0.0, self.withdrawable_balance(user_id) - pending), 4)

    def _spend_agent_balance(self, raw: Dict[str, Any], amount: float) -> None:
        """余额消费优先扣充值余额，不足时再扣可提现分润余额。"""
        amount = round(float(amount or 0), 4)
        if amount <= 0:
            return
        bal = round(float(raw.get("balance", 0) or 0), 4)
        withdrawable = round(min(bal, float(raw.get("withdrawable_balance", 0) or 0)), 4)
        locked = round(max(0.0, bal - withdrawable), 4)
        withdrawable_spend = max(0.0, amount - locked)
        raw["balance"] = round(bal - amount, 4)
        if withdrawable_spend > 0:
            raw["withdrawable_balance"] = round(max(0.0, withdrawable - withdrawable_spend), 4)

    def recharge_user(
        self,
        user_id: str,
        operator_id: str,
        balance: float = 0,
        quota: int = 0,
        note: str = "",
    ) -> Dict[str, Any]:
        with self._billing_lock:
            raw = self.get_raw_user(user_id)
            if not raw or raw.get("role") != "agent":
                raise ValueError("只能给代理账号充值")
            operator = self.get_raw_user(operator_id)
            if not operator:
                raise ValueError("操作者无效")
            if operator.get("role") == "agent":
                if raw.get("parent_id") != operator_id:
                    raise ValueError("只能给自己的下级代理充值")
            elif operator.get("role") != "superadmin":
                raise ValueError("无权充值")

            bal_delta = round(float(balance or 0), 4)
            quota_delta = 0
            if bal_delta == 0:
                raise ValueError("请填写充值金额")

            if bal_delta < 0:
                raise ValueError("充值金额不能为负数")

            if bal_delta and operator.get("role") == "agent":
                op_bal = round(float(operator.get("balance", 0)), 4)
                if op_bal < bal_delta:
                    raise ValueError(f"您的余额不足（当前 {op_bal:.2f}，需要 {bal_delta:.2f}）")
                self._spend_agent_balance(operator, bal_delta)

            if bal_delta:
                raw["balance"] = round(float(raw.get("balance", 0)) + bal_delta, 4)

            log = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "operator_id": operator_id,
                "operator_username": str(operator.get("username", "")),
                "balance_delta": bal_delta,
                "quota_delta": quota_delta,
                "note": str(note or "").strip(),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            if operator.get("role") == "agent" and bal_delta:
                log["operator_balance_delta"] = round(-bal_delta, 4)
            self._data.setdefault("recharge_logs", []).insert(0, log)
            self._data["recharge_logs"] = self._data["recharge_logs"][:2000]
            self.save()
            return self._user_public(raw)

    def apply_recharge_request(
        self,
        username: str,
        req_type: str,
        amount: float,
        note: str = "",
        proof: str = "",
    ) -> Dict[str, Any]:
        """登录页预充值：提交待审核充值申请（暂不自动到账）。"""
        raw = self.find_by_username(username)
        if not raw or raw.get("role") != "agent":
            raise ValueError("代理账号不存在")
        if not raw.get("enabled", True):
            raise ValueError("账号已禁用")
        kind = "balance"
        val = round(float(amount), 4)
        if val <= 0:
            raise ValueError("充值金额必须大于 0")
        req = {
            "id": str(uuid.uuid4()),
            "user_id": str(raw.get("id", "")),
            "username": str(raw.get("username", "")),
            "display_name": str(raw.get("display_name", "")),
            "type": kind,
            "amount": val,
            "note": str(note or "").strip(),
            "proof": str(proof or "").strip(),
            "status": "pending",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._data.setdefault("recharge_requests", []).insert(0, req)
        self._data["recharge_requests"] = self._data["recharge_requests"][:5000]
        self.save()
        return req

    def list_recharge_requests(self, user_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._data.get("recharge_requests", []))
        if user_id:
            items = [x for x in items if x.get("user_id") == user_id]
        return items[:limit]

    def count_pending_recharge_requests(self) -> int:
        return sum(
            1
            for x in self._data.get("recharge_requests", [])
            if x.get("status") == "pending"
        )

    def review_recharge_request(
        self,
        request_id: str,
        operator_id: str,
        action: str,
        review_note: str = "",
    ) -> Dict[str, Any]:
        operator = self.get_raw_user(operator_id)
        if not operator or operator.get("role") != "superadmin":
            raise ValueError("仅超级管理员可审核充值申请")

        req = None
        for item in self._data.get("recharge_requests", []):
            if str(item.get("id", "")) == str(request_id):
                req = item
                break
        if not req:
            raise ValueError("充值申请不存在")
        if req.get("status") != "pending":
            raise ValueError("该申请已处理，请勿重复操作")

        act = str(action or "").strip().lower()
        if act not in ("approve", "reject"):
            raise ValueError("无效操作，请使用 approve 或 reject")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        req["reviewed_at"] = now
        req["reviewer_id"] = operator_id
        req["reviewer_username"] = str(operator.get("username", ""))
        req["review_note"] = str(review_note or "").strip()

        if act == "reject":
            if not str(review_note or "").strip():
                raise ValueError("拒绝时请填写审核说明")
            req["status"] = "rejected"
            self.audit_log(operator_id, str(operator.get("username", "")), "recharge_reject", f"拒绝充值 {req.get('username')}")
            self.save()
            return req

        user_id = str(req.get("user_id", ""))
        req_type = str(req.get("type", "balance"))
        amount = req.get("amount", 0)
        apply_note = f"审核通过 · {req.get('note', '')}".strip(" ·")
        self.recharge_user(
            user_id,
            operator_id,
            balance=float(amount),
            quota=0,
            note=apply_note,
        )
        req["status"] = "approved"
        req["recharge_log_note"] = apply_note
        self.audit_log(operator_id, str(operator.get("username", "")), "recharge_approve", f"通过充值 {req.get('username')} {amount}")
        self.save()
        return req

    def batch_review_recharge_requests(
        self,
        request_ids: List[str],
        operator_id: str,
        action: str,
        review_note: str = "",
    ) -> Dict[str, Any]:
        ok_items: List[Dict[str, Any]] = []
        errors: List[str] = []
        for rid in request_ids:
            try:
                ok_items.append(self.review_recharge_request(rid, operator_id, action, review_note))
            except ValueError as e:
                errors.append(f"{rid[:8]}: {e}")
        return {"ok_count": len(ok_items), "errors": errors, "items": ok_items}

    def list_recharge_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        return list(self._data.get("recharge_logs", []))[:limit]

    def charge_agent_for_cdks(self, user_id: str, count: int = 1) -> List[Dict[str, Any]]:
        """扣减余额并触发上级分润（即时生成或激活扣费时调用）。"""
        with self._billing_lock:
            raw = self.get_raw_user(user_id)
            if not raw or raw.get("role") != "agent":
                return []
            user = self._parse_user(raw)
            self.check_cdk_quota(user, count)
            self.check_cdk_balance(user, count, 0)
            cost = self.cdk_generation_cost(user, count)
            if cost > 0:
                self._spend_agent_balance(raw, cost)
                log = {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "operator_id": user_id,
                    "operator_username": str(raw.get("username", "")),
                    "balance_delta": round(-cost, 4),
                    "quota_delta": 0,
                    "note": f"生成 CDK 扣费 ×{count}",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._data.setdefault("recharge_logs", []).insert(0, log)
                self._data["recharge_logs"] = self._data["recharge_logs"][:2000]
            raw["cdk_generated"] = int(raw.get("cdk_generated", 0)) + count
            commissions = self.apply_generation_commission(user_id, count)
            self.save()
            return commissions

    def refund_agent_cdk_charge(
        self,
        user_id: str,
        count: int = 1,
        operator_id: str = "",
        note: str = "CDK 扣费退回",
    ) -> Dict[str, Any]:
        """撤销已扣费的 CDK（删除未用卡或生成失败回滚）。"""
        with self._billing_lock:
            raw = self.get_raw_user(user_id)
            if not raw or raw.get("role") != "agent" or count <= 0:
                return {"balance_refund": 0, "quota_refund": 0}

            user = self._parse_user(raw)
            refund_bal = 0.0
            cost = self.cdk_generation_cost(user, count)
            if cost > 0:
                raw["balance"] = round(float(raw.get("balance", 0)) + cost, 4)
                refund_bal = cost
            raw["cdk_generated"] = max(0, int(raw.get("cdk_generated", 0)) - count)
            self.reverse_generation_commission(user_id, count)

            operator = self.get_raw_user(operator_id) if operator_id else None
            log = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "operator_id": operator_id,
                "operator_username": str(operator.get("username", "")) if operator else "",
                "balance_delta": refund_bal,
                "quota_delta": -count,
                "note": note,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._data.setdefault("recharge_logs", []).insert(0, log)
            self._data["recharge_logs"] = self._data["recharge_logs"][:2000]
            self.save()
            return {
                "balance_refund": refund_bal,
                "quota_refund": 0,
                "agent_id": user_id,
            }

    def check_activation_billing(self, cdk_service, cdk_code: str) -> None:
        raw = cdk_service.get_key_raw(cdk_code)
        if not raw or str(raw.get("billing_mode", "immediate")) != "on_activate":
            return
        if raw.get("charged"):
            return
        agent_id = str(raw.get("agent_id", ""))
        if not agent_id:
            return
        agent_raw = self.get_raw_user(agent_id)
        if agent_raw:
            user = self._parse_user(agent_raw)
            self.check_cdk_quota(user, 1)
            self.check_cdk_balance(user, 1, 0)

    def check_activation_quota(self, cdk_service, cdk_code: str) -> None:
        """兼容旧调用，等同 check_activation_billing。"""
        self.check_activation_billing(cdk_service, cdk_code)

    def billing_on_activation(self, cdk_service, cdk_code: str) -> List[Dict[str, Any]]:
        with self._billing_lock:
            raw = cdk_service.get_key_raw(cdk_code)
            if not raw or str(raw.get("billing_mode", "immediate")) != "on_activate":
                return []
            if raw.get("charged"):
                return []
            agent_id = str(raw.get("agent_id", ""))
            if not agent_id:
                cdk_service.mark_charged(cdk_code)
                return []
            self.check_activation_billing(cdk_service, cdk_code)
            commissions = self.charge_agent_for_cdks(agent_id, 1)
            if not cdk_service.mark_charged(cdk_code):
                self.refund_agent_cdk_charge(agent_id, 1, agent_id, "激活扣费标记失败回滚")
                raise ValueError("扣费标记失败，请重试")
            return commissions

    def add_cdk_generated(self, user_id: str, count: int = 1) -> None:
        raw = self._data.get("users", {}).get(user_id)
        if not raw:
            return
        raw["cdk_generated"] = int(raw.get("cdk_generated", 0)) + count
        self.save()

    def subtract_cdk_generated(self, user_id: str, count: int = 1) -> None:
        raw = self._data.get("users", {}).get(user_id)
        if not raw:
            return
        raw["cdk_generated"] = max(0, int(raw.get("cdk_generated", 0)) - count)
        self.save()

    def apply_generation_commission(self, generator_id: str, count: int = 1) -> List[Dict[str, Any]]:
        """沿上级链逐级分润（每级拿与下级的差价）。"""
        if count <= 0:
            return []
        child = self.get_raw_user(generator_id)
        if not child or child.get("role") != "agent":
            return []

        settings = self._data.get("settings", self._default_settings())
        max_levels = int(settings.get("commission_max_levels", 0))
        logs: List[Dict[str, Any]] = []
        level = 0

        while child:
            parent_id = str(child.get("parent_id", ""))
            if not parent_id:
                break
            if max_levels and level >= max_levels:
                break
            parent = self.get_raw_user(parent_id)
            if not parent:
                break

            child_price = float(child.get("cdk_cost_price", 0))
            if parent.get("role") == "superadmin":
                parent_cost = self.base_cdk_price()
            else:
                parent_cost = float(parent.get("cdk_cost_price", self.base_cdk_price()))

            profit = round((child_price - parent_cost) * count, 4)
            if profit > 0:
                parent["balance"] = round(float(parent.get("balance", 0)) + profit, 4)
                parent["withdrawable_balance"] = round(float(parent.get("withdrawable_balance", 0) or 0) + profit, 4)
                log = {
                    "id": str(uuid.uuid4()),
                    "user_id": parent_id,
                    "from_user_id": str(child.get("id", "")),
                    "from_username": str(child.get("username", "")),
                    "amount": profit,
                    "cdk_count": count,
                    "unit_profit": round(child_price - parent_cost, 4),
                    "reason": "下级 CDK 分润",
                    "level": level + 1,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._data.setdefault("commission_logs", []).insert(0, log)
                logs.append(log)

            if parent.get("role") == "superadmin":
                break
            child = parent
            level += 1

        if logs:
            self._data["commission_logs"] = self._data["commission_logs"][:5000]
            self.save()
        return logs

    def reverse_generation_commission(self, generator_id: str, count: int = 1) -> List[Dict[str, Any]]:
        """回收 CDK 时向上级链扣回已发分润。"""
        if count <= 0:
            return []
        child = self.get_raw_user(generator_id)
        if not child or child.get("role") != "agent":
            return []

        settings = self._data.get("settings", self._default_settings())
        max_levels = int(settings.get("commission_max_levels", 0))
        logs: List[Dict[str, Any]] = []
        level = 0

        while child:
            parent_id = str(child.get("parent_id", ""))
            if not parent_id:
                break
            if max_levels and level >= max_levels:
                break
            parent = self.get_raw_user(parent_id)
            if not parent:
                break

            child_price = float(child.get("cdk_cost_price", 0))
            if parent.get("role") == "superadmin":
                parent_cost = self.base_cdk_price()
            else:
                parent_cost = float(parent.get("cdk_cost_price", self.base_cdk_price()))

            profit = round((child_price - parent_cost) * count, 4)
            if profit > 0:
                bal = round(float(parent.get("balance", 0)), 4)
                deduct = min(profit, bal) if bal > 0 else 0.0
                parent["balance"] = round(bal - deduct, 4)
                withdrawable = round(float(parent.get("withdrawable_balance", 0) or 0), 4)
                parent["withdrawable_balance"] = round(max(0.0, withdrawable - profit), 4)
                log = {
                    "id": str(uuid.uuid4()),
                    "user_id": parent_id,
                    "from_user_id": str(child.get("id", "")),
                    "from_username": str(child.get("username", "")),
                    "amount": -profit,
                    "cdk_count": count,
                    "unit_profit": round(child_price - parent_cost, 4),
                    "reason": "CDK 回收扣回分润",
                    "level": level + 1,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if deduct < profit:
                    log["uncollected"] = round(profit - deduct, 4)
                self._data.setdefault("commission_logs", []).insert(0, log)
                logs.append(log)

            if parent.get("role") == "superadmin":
                break
            child = parent
            level += 1

        if logs:
            self._data["commission_logs"] = self._data["commission_logs"][:5000]
            self.save()
        return logs

    def recycle_cdk_billing(self, cdk_raw: Dict[str, Any], operator_id: str = "") -> Dict[str, Any]:
        """回收已激活 CDK：退代理成本到余额、扣回上级分润。"""
        if not cdk_raw.get("charged"):
            return {"balance_refund": 0, "quota_refund": 0}
        agent_id = str(cdk_raw.get("agent_id", ""))
        if not agent_id:
            return {"balance_refund": 0, "quota_refund": 0}
        note = f"CDK 回收退款 · {cdk_raw.get('appid', '')}"
        return self.refund_agent_cdk_charge(agent_id, 1, operator_id, note)

    def list_commission_logs(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        logs = [
            x
            for x in self._data.get("commission_logs", [])
            if x.get("user_id") == user_id
        ]
        return logs[:limit]

    def wallet_summary(self, user_id: str) -> Dict[str, Any]:
        raw = self.get_raw_user(user_id)
        if not raw:
            return {}
        u = self._parse_user(raw)
        return {
            "balance": round(u.balance, 4),
            "available_balance": round(self.available_balance(user_id), 4),
            "withdrawable_balance": round(self.withdrawable_balance(user_id), 4),
            "available_withdrawable_balance": round(self.available_withdrawable_balance(user_id), 4),
            "pending_withdraw": round(self.pending_withdraw_total(user_id), 4),
            "cdk_cost_price": round(u.cdk_cost_price, 4),
            "base_cdk_price": self.base_cdk_price(),
            "sub_agent_count": self.count_sub_agents(user_id) if u.role == "agent" else 0,
            "recent_logs": self.list_commission_logs(user_id, 20),
        }

    def site_name(self) -> str:
        return str(self._data.get("settings", {}).get("site_name", "CS Steam 管理台"))

    def site_name(self) -> str:
        return str(self._data.get("settings", {}).get("site_name", "CS Steam 管理台"))

    def audit_log(self, operator_id: str, operator_name: str, action: str, detail: str = "") -> None:
        entry = {
            "id": str(uuid.uuid4()),
            "operator_id": operator_id,
            "operator_name": operator_name,
            "action": action,
            "detail": str(detail or "")[:500],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._data.setdefault("audit_logs", []).insert(0, entry)
        self._data["audit_logs"] = self._data["audit_logs"][:3000]

    def list_audit_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        return list(self._data.get("audit_logs", []))[:limit]

    def get_public_announcement(self) -> Dict[str, Any]:
        s = self._data.get("settings", self._default_settings())
        if not s.get("announcement_enabled"):
            return {"enabled": False, "content": ""}
        return {"enabled": True, "content": str(s.get("announcement", ""))}

    def admin_reset_agent_invite(self, agent_id: str, operator_id: str) -> Dict[str, Any]:
        operator = self.get_raw_user(operator_id)
        if not operator or operator.get("role") != "superadmin":
            raise ValueError("仅超级管理员可重置邀请码")
        code = self.refresh_invite_code(agent_id)
        self.audit_log(operator_id, str(operator.get("username", "")), "reset_invite", f"重置代理 {agent_id} 邀请码")
        return {"invite_code": code, "user": self._user_public(self.get_raw_user(agent_id) or {})}

    def admin_set_agent_invite_price(self, agent_id: str, operator_id: str, price: float) -> Dict[str, Any]:
        operator = self.get_raw_user(operator_id)
        if not operator or operator.get("role") != "superadmin":
            raise ValueError("仅超级管理员可操作")
        raw = self.get_raw_user(agent_id)
        if not raw or raw.get("role") != "agent":
            raise ValueError("代理不存在")
        raw["invite_cost_price"] = self._validate_sub_price(str(raw.get("parent_id", "")), float(price))
        self.save()
        self.audit_log(operator_id, str(operator.get("username", "")), "set_invite_price", f"设置 {raw.get('username')} 邀请价")
        return self._user_public(raw)

    def apply_withdraw_request(self, user_id: str, amount: float, payout_info: str = "", note: str = "") -> Dict[str, Any]:
        raw = self.get_raw_user(user_id)
        if not raw or raw.get("role") != "agent":
            raise ValueError("仅代理可申请提现")
        val = round(float(amount), 4)
        if val <= 0:
            raise ValueError("提现金额必须大于 0")
        if val < MIN_WITHDRAW_AMOUNT:
            raise ValueError(f"提现金额满 {MIN_WITHDRAW_AMOUNT:.0f} 元才可以提交")
        with self._billing_lock:
            bal = round(float(raw.get("balance", 0)), 4)
            pending = self.pending_withdraw_total(user_id)
            withdrawable = self.withdrawable_balance(user_id)
            available = round(withdrawable - pending, 4)
            if available < MIN_WITHDRAW_AMOUNT:
                raise ValueError(f"可提现分润满 {MIN_WITHDRAW_AMOUNT:.0f} 元才可以提现（当前可提现 {available:.2f}）")
            if available < val:
                raise ValueError(f"可提现分润不足（账户余额 {bal:.2f}，待审核提现 {pending:.2f}，可提现 {available:.2f}）")
            req = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "username": str(raw.get("username", "")),
                "amount": val,
                "payout_info": str(payout_info or "").strip(),
                "note": str(note or "").strip(),
                "status": "pending",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._data.setdefault("withdraw_requests", []).insert(0, req)
            self._data["withdraw_requests"] = self._data["withdraw_requests"][:5000]
            self.save()
            return req

    def list_withdraw_requests(self, user_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._data.get("withdraw_requests", []))
        if user_id:
            items = [x for x in items if x.get("user_id") == user_id]
        return items[:limit]

    def count_pending_withdraw_requests(self) -> int:
        return sum(1 for x in self._data.get("withdraw_requests", []) if x.get("status") == "pending")

    def review_withdraw_request(
        self,
        request_id: str,
        operator_id: str,
        action: str,
        review_note: str = "",
    ) -> Dict[str, Any]:
        operator = self.get_raw_user(operator_id)
        if not operator or operator.get("role") != "superadmin":
            raise ValueError("仅超级管理员可审核提现")
        req = None
        for item in self._data.get("withdraw_requests", []):
            if str(item.get("id", "")) == str(request_id):
                req = item
                break
        if not req or req.get("status") != "pending":
            raise ValueError("提现申请不存在或已处理")
        act = str(action or "").strip().lower()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        req["reviewed_at"] = now
        req["reviewer_username"] = str(operator.get("username", ""))
        req["review_note"] = str(review_note or "").strip()
        if act == "reject":
            if not req["review_note"]:
                raise ValueError("拒绝时请填写审核说明")
            req["status"] = "rejected"
            self.save()
            return req
        if act != "approve":
            raise ValueError("无效操作")
        user_id = str(req.get("user_id", ""))
        with self._billing_lock:
            raw = self.get_raw_user(user_id)
            if not raw:
                raise ValueError("用户不存在")
            amount = round(float(req.get("amount", 0)), 4)
            bal = round(float(raw.get("balance", 0)), 4)
            if bal < amount:
                raise ValueError("用户余额不足，无法完成提现")
            withdrawable = self.withdrawable_balance(user_id)
            if withdrawable < amount:
                raise ValueError("用户可提现分润不足，无法完成提现")
            raw["balance"] = round(bal - amount, 4)
            raw["withdrawable_balance"] = round(max(0.0, withdrawable - amount), 4)
            req["status"] = "approved"
            self.audit_log(operator_id, str(operator.get("username", "")), "withdraw_approve", f"{req.get('username')} -{amount}")
            self.save()
            return req

    def batch_review_withdraw_requests(
        self,
        request_ids: List[str],
        operator_id: str,
        action: str,
        review_note: str = "",
    ) -> Dict[str, Any]:
        ok_items: List[Dict[str, Any]] = []
        errors: List[str] = []
        for rid in request_ids:
            try:
                ok_items.append(self.review_withdraw_request(rid, operator_id, action, review_note))
            except ValueError as e:
                errors.append(f"{rid[:8]}: {e}")
        return {"ok_count": len(ok_items), "errors": errors, "items": ok_items}

    def list_all_commission_logs(self, limit: int = 200) -> List[Dict[str, Any]]:
        return list(self._data.get("commission_logs", []))[:limit]

    def build_agent_tree(self, root_id: str = "") -> List[Dict[str, Any]]:
        users = self._data.get("users", {})
        by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for raw in users.values():
            if raw.get("role") != "agent":
                continue
            pid = str(raw.get("parent_id", ""))
            by_parent.setdefault(pid, []).append(self._user_public(raw))

        def nest(parent: str) -> List[Dict[str, Any]]:
            nodes = []
            for u in sorted(by_parent.get(parent, []), key=lambda x: x.get("username", "")):
                nodes.append({**u, "children": nest(u["id"])})
            return nodes

        return nest(root_id)

    def operations_report(self, cdk_service) -> Dict[str, Any]:
        today = datetime.now().strftime("%Y-%m-%d")
        agents = [u for u in self._data.get("users", {}).values() if u.get("role") == "agent"]
        activation_logs = cdk_service.list_activation_logs(limit=5000)
        today_activations = [x for x in activation_logs if str(x.get("used_at", "")).startswith(today)]

        agent_stats: Dict[str, Dict[str, Any]] = {}
        for a in agents:
            aid = str(a.get("id", ""))
            agent_stats[aid] = {
                "id": aid,
                "username": a.get("username"),
                "display_name": a.get("display_name", ""),
                "cdk_generated": int(a.get("cdk_generated", 0)),
                "balance": round(float(a.get("balance", 0)), 4),
                "today_activations": 0,
            }
        game_stats: Dict[str, int] = {}
        for log in today_activations:
            aid = str(log.get("agent_id", ""))
            if aid in agent_stats:
                agent_stats[aid]["today_activations"] += 1
            appid = str(log.get("appid", ""))
            game_stats[appid] = game_stats.get(appid, 0) + 1

        top_agents = sorted(agent_stats.values(), key=lambda x: x["today_activations"], reverse=True)[:10]
        top_games = sorted(
            [{"appid": k, "name": next((x.get("name") for x in today_activations if str(x.get("appid")) == k), ""), "count": v} for k, v in game_stats.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        recharge_today = sum(
            1 for x in self._data.get("recharge_requests", [])
            if str(x.get("created_at", "")).startswith(today)
        )
        approved_today = sum(
            1 for x in self._data.get("recharge_requests", [])
            if x.get("status") == "approved" and str(x.get("reviewed_at", "")).startswith(today)
        )

        return {
            "today_activations": len(today_activations),
            "today_recharge_requests": recharge_today,
            "today_recharge_approved": approved_today,
            "pending_recharge": self.count_pending_recharge_requests(),
            "pending_withdraw": self.count_pending_withdraw_requests(),
            "agent_count": len(agents),
            "top_agents_today": top_agents,
            "top_games_today": top_games,
        }

    def get_settings_public(self) -> Dict[str, Any]:
        s = self._data.get("settings", self._default_settings())
        return {
            "site_name": s.get("site_name", "CS Steam 管理台"),
            "base_cdk_price": self.base_cdk_price(),
            "register_default_quota": int(s.get("register_default_quota", 100)),
            "cdk_default_expire_days": int(s.get("cdk_default_expire_days", 0)),
            "commission_max_levels": int(s.get("commission_max_levels", 0)),
            "announcement_enabled": bool(s.get("announcement_enabled", False)),
            "announcement": str(s.get("announcement", "")),
        }
