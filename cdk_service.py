"""CDK 激活码服务：校验、绑定 AppID、记录使用。"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from platform_utils import backup_json, file_lock

try:
    from database import DOC_CDK, get_store
except ImportError:
    DOC_CDK = "cdk"

    def get_store():
        return None

CDK_DB_PATH = Path("./cdk_db.json")
CDK_PATTERN = re.compile(r"^[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$")


@dataclass
class CdkRecord:
    appid: str
    name: str = ""
    used: bool = False
    used_at: Optional[str] = None
    used_machine: Optional[str] = None
    note: str = ""
    created_by: str = ""
    agent_id: str = ""
    created_at: Optional[str] = None
    billing_mode: str = "immediate"
    charged: bool = True
    expires_at: Optional[str] = None
    revoked: bool = False
    revoked_at: Optional[str] = None


@dataclass
class CdkValidationResult:
    valid: bool
    appid: str = ""
    name: str = ""
    message: str = ""
    cdk: str = ""


class CdkService:
    def __init__(self, db_path: Path = CDK_DB_PATH):
        from database import ensure_database_bootstrapped

        self.db_path = db_path
        self._data: Dict[str, Any] = {}
        self._last_load_at: float = 0.0
        self.load()

    def refresh(self, *, force: bool = False) -> None:
        """从存储重新加载，避免多请求/多线程下内存数据落后。"""
        import time

        if not force and self._data and (time.time() - self._last_load_at) < 5:
            return
        self.load()

    def load(self) -> None:
        store = get_store()
        if store:
            self._data = store.get(DOC_CDK)
            if not self._data:
                self._data = {}
        elif self.db_path.exists():
            try:
                self._data = json.loads(self.db_path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        else:
            self._data = {}
        if "keys" not in self._data:
            self._data = {"settings": self._default_settings(), "keys": {}}
        if "settings" not in self._data:
            self._data["settings"] = self._default_settings()
        import time

        self._last_load_at = time.time()
        settings = self._data.setdefault("settings", self._default_settings())
        default_secret = "cai-box-cdk-secret-change-me"
        if str(settings.get("secret", "")) in ("", default_secret):
            settings["secret"] = secrets.token_hex(16)
        self.save()

    def save(self) -> None:
        store = get_store()
        if store:
            store.set(DOC_CDK, self._data)
            return
        with file_lock(self.db_path):
            backup_json(self.db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    @staticmethod
    def _default_settings() -> Dict[str, Any]:
        return {
            "one_time_use": True,
            "allow_reuse_on_same_machine": False,
            "secret": "cai-box-cdk-secret-change-me",
            "allow_signed_cdk_online": False,
        }

    def signed_cdk_online_allowed(self) -> bool:
        return bool(self.settings().get("allow_signed_cdk_online", False))

    @staticmethod
    def normalize_cdk(code: str) -> str:
        return code.strip().upper().replace(" ", "")

    @staticmethod
    def generate_cdk() -> str:
        raw = secrets.token_hex(8).upper()
        return "-".join(raw[i : i + 4] for i in range(0, 16, 4))

    @staticmethod
    def machine_fingerprint() -> str:
        host = socket.gethostname()
        node = platform.node()
        raw = f"{host}|{node}|{platform.system()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def settings(self) -> Dict[str, Any]:
        return dict(self._data.get("settings", self._default_settings()))

    def list_keys(
        self,
        limit: int = 100,
        agent_id: str = "",
        appid: str = "",
        appids: Optional[set] = None,
        name_contains: str = "",
    ) -> List[tuple[str, CdkRecord]]:
        self.refresh()
        name_q = str(name_contains or "").strip().lower()
        items: List[tuple[str, CdkRecord]] = []
        for cdk, raw in self._data.get("keys", {}).items():
            if agent_id and str(raw.get("agent_id", "")) != agent_id:
                continue
            aid = str(raw.get("appid", ""))
            if appid and aid != str(appid):
                continue
            if appids is not None and aid not in appids:
                continue
            if name_q and name_q not in str(raw.get("name", "")).lower():
                continue
            items.append((cdk, self._parse_record(raw)))
        items.sort(key=lambda x: x[1].created_at or "", reverse=True)
        return items[:limit] if limit > 0 else items

    def list_activation_logs(self, limit: int = 100, agent_id: str = "") -> List[Dict[str, Any]]:
        logs: List[Dict[str, Any]] = []
        for cdk, raw in self._data.get("keys", {}).items():
            if not raw.get("used"):
                continue
            if agent_id and str(raw.get("agent_id", "")) != agent_id:
                continue
            logs.append({
                "cdk": cdk,
                "appid": str(raw.get("appid", "")),
                "name": str(raw.get("name", "")),
                "used_at": raw.get("used_at"),
                "used_machine": raw.get("used_machine"),
                "agent_id": str(raw.get("agent_id", "")),
                "created_by": str(raw.get("created_by", "")),
            })
        logs.sort(key=lambda x: str(x.get("used_at") or ""), reverse=True)
        return logs[:limit]

    def delete_key(self, cdk: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        keys = self._data.get("keys", {})
        code = self.normalize_cdk(cdk)
        if code not in keys:
            return False, None
        if keys[code].get("used"):
            raise ValueError("已使用的 CDK 不能删除")
        snapshot = dict(keys[code])
        del keys[code]
        self.save()
        return True, snapshot

    def stats(self, agent_id: str = "") -> Dict[str, int]:
        self.refresh()
        total = used = unused = 0
        for _, raw in self._data.get("keys", {}).items():
            if agent_id and str(raw.get("agent_id", "")) != agent_id:
                continue
            total += 1
            if raw.get("used"):
                used += 1
            else:
                unused += 1
        return {"total": total, "used": used, "unused": unused}

    def _parse_record(self, raw: Dict[str, Any]) -> CdkRecord:
        billing_mode = str(raw.get("billing_mode", "immediate"))
        charged = bool(raw.get("charged", billing_mode != "on_activate"))
        return CdkRecord(
            appid=str(raw.get("appid", "")),
            name=str(raw.get("name", "")),
            used=bool(raw.get("used", False)),
            used_at=raw.get("used_at"),
            used_machine=raw.get("used_machine"),
            note=str(raw.get("note", "")),
            created_by=str(raw.get("created_by", "")),
            agent_id=str(raw.get("agent_id", "")),
            created_at=raw.get("created_at"),
            billing_mode=billing_mode,
            charged=charged,
            expires_at=raw.get("expires_at"),
            revoked=bool(raw.get("revoked", False)),
            revoked_at=raw.get("revoked_at"),
        )

    def _is_expired(self, raw: Dict[str, Any]) -> bool:
        exp = raw.get("expires_at")
        if not exp:
            return False
        try:
            return datetime.now() > datetime.strptime(str(exp), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False

    def get_key_raw(self, cdk: str) -> Optional[Dict[str, Any]]:
        code = self.normalize_cdk(cdk)
        if not code:
            return None
        raw = self._data.get("keys", {}).get(code)
        return dict(raw) if raw else None

    def count_agent_pending_precharge(self, agent_id: str) -> int:
        """未扣费的 on_activate 卡（含已过期未激活，占用配额/余额预留）。"""
        total = 0
        for raw in self._data.get("keys", {}).values():
            if str(raw.get("agent_id", "")) != agent_id:
                continue
            if str(raw.get("billing_mode", "immediate")) != "on_activate":
                continue
            if raw.get("used") or raw.get("charged"):
                continue
            total += 1
        return total

    def count_agent_pending_active(self, agent_id: str) -> int:
        """未过期、可激活的 on_activate 待扣费卡。"""
        total = 0
        for raw in self._data.get("keys", {}).values():
            if str(raw.get("agent_id", "")) != agent_id:
                continue
            if str(raw.get("billing_mode", "immediate")) != "on_activate":
                continue
            if raw.get("used") or raw.get("charged"):
                continue
            if self._is_expired(raw):
                continue
            total += 1
        return total

    def mark_charged(self, cdk: str) -> bool:
        code = self.normalize_cdk(cdk)
        raw = self._data.get("keys", {}).get(code)
        if not raw:
            return False
        raw["charged"] = True
        self.save()
        return True

    def add_key(
        self,
        appid: str,
        name: str = "",
        cdk: Optional[str] = None,
        note: str = "",
        created_by: str = "",
        agent_id: str = "",
        billing_mode: str = "immediate",
        expire_days: int = 0,
        charged: Optional[bool] = None,
        defer_save: bool = False,
    ) -> str:
        appid = str(appid).strip()
        if not appid.isdigit():
            raise ValueError("AppID 必须是数字")
        code = self.normalize_cdk(cdk) if cdk else self.generate_cdk()
        if not CDK_PATTERN.match(code):
            raise ValueError(f"CDK 格式无效: {code}")
        keys = self._data.setdefault("keys", {})
        if code in keys:
            raise ValueError(f"CDK 已存在: {code}")
        mode = billing_mode if billing_mode in ("immediate", "on_activate") else "immediate"
        if charged is None:
            charged = mode != "on_activate"
        expires_at = None
        if expire_days and int(expire_days) > 0:
            expires_at = (datetime.now() + timedelta(days=int(expire_days))).strftime("%Y-%m-%d %H:%M:%S")
        keys[code] = {
            "appid": appid,
            "name": name,
            "used": False,
            "used_at": None,
            "used_machine": None,
            "note": note,
            "created_by": created_by,
            "agent_id": agent_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "billing_mode": mode,
            "charged": bool(charged),
            "expires_at": expires_at,
        }
        if not defer_save:
            self.save()
        return code

    def generate_batch(
        self,
        appid: str,
        count: int = 1,
        name: str = "",
        note: str = "",
        created_by: str = "",
        agent_id: str = "",
        billing_mode: str = "immediate",
        expire_days: int = 0,
        charged: Optional[bool] = None,
    ) -> List[str]:
        count = max(1, min(int(count), 100))
        created: List[str] = []
        keys = self._data.setdefault("keys", {})
        try:
            for _ in range(count):
                created.append(
                    self.add_key(
                        appid,
                        name=name,
                        note=note,
                        created_by=created_by,
                        agent_id=agent_id,
                        billing_mode=billing_mode,
                        expire_days=expire_days,
                        charged=charged,
                        defer_save=True,
                    )
                )
            self.save()
        except Exception:
            for code in created:
                keys.pop(code, None)
            raise
        return created

    def _validate_signed_cdk(self, cdk: str) -> Optional[CdkValidationResult]:
        """支持 APPID 签名码：APPID-730-XXXX-XXXX（无需预录入数据库）。"""
        parts = cdk.split("-")
        if len(parts) != 4 or parts[0] != "APPID" or not parts[1].isdigit():
            return None
        appid = parts[1]
        sig = (parts[2] + parts[3]).upper()
        secret = str(self.settings().get("secret", ""))
        expected = hashlib.sha256(f"{secret}:{appid}".encode("utf-8")).hexdigest()[:8].upper()
        if sig != expected:
            return None
        return CdkValidationResult(
            valid=True,
            appid=appid,
            name=f"AppID {appid}",
            message="签名 CDK 校验通过",
            cdk=cdk,
        )

    @staticmethod
    def make_signed_cdk(appid: str, secret: str) -> str:
        appid = str(appid).strip()
        sig = hashlib.sha256(f"{secret}:{appid}".encode("utf-8")).hexdigest()[:8].upper()
        return f"APPID-{appid}-{sig[:4]}-{sig[4:8]}"

    def validate(self, cdk: str) -> CdkValidationResult:
        code = self.normalize_cdk(cdk)
        if not code:
            return CdkValidationResult(valid=False, message="请输入 CDK 激活码")

        signed = self._validate_signed_cdk(code)
        if signed:
            if not self.signed_cdk_online_allowed():
                return CdkValidationResult(
                    valid=False,
                    message="签名 CDK 不支持在线兑换，请使用代理发放的卡密",
                )
            return signed

        if not CDK_PATTERN.match(code):
            return CdkValidationResult(valid=False, message="CDK 格式错误，应为 XXXX-XXXX-XXXX-XXXX")

        raw = self._data.get("keys", {}).get(code)
        if not raw:
            return CdkValidationResult(valid=False, message="无效的 CDK，请检查激活码是否正确")

        if raw.get("revoked"):
            return CdkValidationResult(valid=False, message="此 CDK 已被回收，无法使用")

        if self._is_expired(raw):
            return CdkValidationResult(valid=False, message="此 CDK 已过期")

        record = self._parse_record(raw)
        settings = self.settings()
        machine = self.machine_fingerprint()

        if record.used:
            if settings.get("allow_reuse_on_same_machine") and record.used_machine == machine:
                return CdkValidationResult(
                    valid=True,
                    appid=record.appid,
                    name=record.name,
                    message="本机已激活过此 CDK，允许重复使用",
                    cdk=code,
                )
            if settings.get("one_time_use", True):
                return CdkValidationResult(valid=False, message="此 CDK 已被使用")

        if not record.appid.isdigit():
            return CdkValidationResult(valid=False, message="CDK 绑定的 AppID 无效")

        return CdkValidationResult(
            valid=True,
            appid=record.appid,
            name=record.name or f"AppID {record.appid}",
            message="CDK 校验通过",
            cdk=code,
        )

    def consume(self, cdk: str) -> CdkValidationResult:
        result = self.validate(cdk)
        if not result.valid:
            return result

        code = self.normalize_cdk(cdk)
        signed = self._validate_signed_cdk(code)
        if signed:
            if not self.signed_cdk_online_allowed():
                return CdkValidationResult(
                    valid=False,
                    message="签名 CDK 不支持在线兑换，请使用代理发放的卡密",
                )
            return signed

        keys = self._data.setdefault("keys", {})
        raw = keys.get(code)
        if not raw:
            return result

        raw["used"] = True
        raw["used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw["used_machine"] = self.machine_fingerprint()
        self.save()
        result.message = "CDK 已激活并标记为已使用"
        return result

    def unmark_charged(self, cdk: str) -> bool:
        code = self.normalize_cdk(cdk)
        raw = self._data.get("keys", {}).get(code)
        if not raw:
            return False
        raw["charged"] = False
        self.save()
        return True

    def unconsume(self, cdk: str) -> bool:
        code = self.normalize_cdk(cdk)
        raw = self._data.get("keys", {}).get(code)
        if not raw or not raw.get("used"):
            return False
        raw["used"] = False
        raw["used_at"] = None
        raw["used_machine"] = None
        self.save()
        return True

    def recycle_key(
        self,
        cdk: str,
        operator: str = "",
        note: str = "",
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """回收已使用的 CDK：标记 revoked，保留记录供审计与客户端同步。"""
        keys = self._data.get("keys", {})
        code = self.normalize_cdk(cdk)
        raw = keys.get(code)
        if not raw:
            return False, None
        if raw.get("revoked"):
            raise ValueError("该 CDK 已回收")
        if not raw.get("used"):
            raise ValueError("仅已激活的 CDK 可回收，未使用的请使用删除")
        raw["revoked"] = True
        raw["revoked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw["revoked_by"] = str(operator or "")
        if note:
            raw["revoke_note"] = str(note)
        self.save()
        return True, dict(raw)

    def list_revoked(self, limit: int = 500) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for cdk, raw in self._data.get("keys", {}).items():
            if not raw.get("revoked"):
                continue
            items.append({
                "cdk": cdk,
                "appid": str(raw.get("appid", "")),
                "name": str(raw.get("name", "")),
                "revoked_at": raw.get("revoked_at"),
                "agent_id": str(raw.get("agent_id", "")),
            })
        items.sort(key=lambda x: str(x.get("revoked_at") or ""), reverse=True)
        return items[:limit]

    def check_revoked_cdks(self, cdks: List[str]) -> List[Dict[str, Any]]:
        """批量查询哪些 CDK 已被回收（供客户端自动禁玩同步）。"""
        self.refresh()
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for raw_cdk in cdks:
            code = self.normalize_cdk(str(raw_cdk or ""))
            if not code or code in seen:
                continue
            seen.add(code)
            raw = self._data.get("keys", {}).get(code)
            if not raw or not raw.get("revoked"):
                continue
            out.append(
                {
                    "cdk": code,
                    "appid": str(raw.get("appid", "")),
                    "name": str(raw.get("name", "")),
                    "revoked_at": raw.get("revoked_at"),
                }
            )
        return out

    def get_public_status(self, cdk: str) -> Optional[Dict[str, Any]]:
        code = self.normalize_cdk(cdk)
        raw = self._data.get("keys", {}).get(code)
        if not raw:
            return None
        return {
            "cdk": code,
            "appid": str(raw.get("appid", "")),
            "name": str(raw.get("name", "")),
            "used": bool(raw.get("used")),
            "revoked": bool(raw.get("revoked")),
            "revoked_at": raw.get("revoked_at"),
            "expires_at": raw.get("expires_at"),
        }
