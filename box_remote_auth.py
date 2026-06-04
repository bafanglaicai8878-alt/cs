"""游戏盒子远程认证：与 Web 服务共用 client_db（一套账号 / VIP）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

BOX_SESSION_PATH = Path("./box_session.json")


class RemoteBoxAuthService:
    """通过 Box_Server_URL 调用 web_server 的 /api/box/* 接口。"""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        token: str = "",
    ) -> Dict[str, Any]:
        if not self.base_url:
            raise ValueError("未配置 Box_Server_URL")
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
                payload = json.loads(err_body)
                msg = payload.get("message") or str(e)
            except Exception:
                msg = str(e)
            raise ValueError(msg) from e
        except urllib.error.URLError as e:
            raise ValueError(f"无法连接服务器 {self.base_url}：{e.reason}") from e

    def register(self, username: str, password: str, display_name: str = "") -> Dict[str, Any]:
        r = self._request(
            "POST",
            "/api/box/register",
            {"username": username, "password": password, "display_name": display_name},
        )
        if not r.get("ok"):
            raise ValueError(r.get("message", "注册失败"))
        return r.get("user") or {}

    def login(self, username: str, password: str) -> Dict[str, Any]:
        r = self._request("POST", "/api/box/login", {"username": username, "password": password})
        if not r.get("ok"):
            raise ValueError(r.get("message", "登录失败"))
        token = str(r.get("token", ""))
        if token:
            self.save_session_token(token)
        return r

    def logout(self, token: str) -> None:
        try:
            self._request("POST", "/api/box/logout", {}, token=token)
        except ValueError:
            pass
        if BOX_SESSION_PATH.exists():
            try:
                BOX_SESSION_PATH.unlink()
            except OSError:
                pass

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        try:
            r = self._request("GET", "/api/box/me", token=token)
        except ValueError:
            return None
        if not r.get("ok"):
            return None
        return r.get("user")

    def is_vip(self, user_id: str) -> bool:
        token = self.load_session_token()
        user = self.verify_token(token)
        if not user or str(user.get("id", "")) != str(user_id):
            return False
        return bool(user.get("vip"))

    def activate_vip(self, user_id: str, code: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        token = self.load_session_token()
        if not token:
            raise ValueError("请先登录")
        r = self._request("POST", "/api/box/vip/activate", {"code": code}, token=token)
        if not r.get("ok"):
            raise ValueError(r.get("message", "激活失败"))
        return r.get("user") or {}

    def grant_vip_after_cdk(self, days: int = 0) -> Dict[str, Any]:
        token = self.load_session_token()
        if not token:
            return {}
        body = {}
        if days > 0:
            body["days"] = days
        r = self._request("POST", "/api/box/vip/cdk-success", body, token=token)
        if not r.get("ok"):
            return {}
        return r.get("user") or {}

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
