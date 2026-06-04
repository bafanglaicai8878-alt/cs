"""Web 管理台 + 远程 CDK 服务（搜索游戏、生成 CDK、irm|iex 用户安装）。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from admin_service import AdminService, AdminUser
from box_service import BoxService, ImportOptions
from cdk_service import CdkService
from client_auth_service import ClientAuthService
from database import DOC_CATALOG, DOC_STEAM_CATALOG, ensure_database_bootstrapped, get_database_label, is_database_enabled, read_json_cache
from github_tokens import normalize_github_tokens, parse_github_tokens_input, mask_github_token
from platform_utils import RATE_LIMITER, irm_cmd_base, normalize_public_url

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
ACTIVATE_PS1 = ROOT / "activate.ps1"
HOOK_PS1 = ROOT / "hook.ps1"
FIX_PS1 = ROOT / "fix-client.ps1"
ADMIN_HTML = STATIC_DIR / "admin.html"
ADMIN_LOGIN_HTML = STATIC_DIR / "admin-login.html"
LOGIN_HTML = STATIC_DIR / "login.html"
REGISTER_HTML = STATIC_DIR / "register.html"
PORTAL_HTML = STATIC_DIR / "portal.html"
API_BASE_PLACEHOLDER = "__INJECT_API_BASE__"


class WebCdkServer:
    def __init__(self):
        self.service = BoxService()
        self.cdk = CdkService()
        self.admin = AdminService()
        self.box_auth = ClientAuthService()
        self._ready = False
        self._lock = asyncio.Lock()
        self._plugin_cache: Dict[str, Dict[str, bytes]] = {}

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        await self.service.initialize()
        self._ready = True

    async def search_games(
        self, query: str, manifest_only: bool = False
    ) -> List[Dict[str, Any]]:
        await self.ensure_ready()
        query = query.strip()
        if not query:
            return []

        manifest_set = set(await self.service.get_manifest_appids())
        results = await self.service.search_games(query, manifest_only=manifest_only)

        def _row(aid: str, name: str) -> Dict[str, Any]:
            return {
                "appid": aid,
                "name": name,
                "has_manifest": aid in manifest_set,
                "header": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{aid}/header.jpg",
            }

        if results:
            return [_row(str(r.appid), r.name) for r in results]

        if query.isdigit():
            aid = query
            if manifest_only and aid not in manifest_set:
                return []
            name = await self.service._fetch_app_name(aid)
            return [_row(aid, name or f"AppID {aid}")]

        app_id = self.service.backend.extract_app_id(query)
        if app_id:
            if manifest_only and app_id not in manifest_set:
                return []
            name = await self.service._fetch_app_name(app_id)
            return [_row(app_id, name or f"AppID {app_id}")]

        return []

    async def generate_cdks(
        self,
        appid: str,
        name: str = "",
        count: int = 1,
        note: str = "",
        user: Optional[AdminUser] = None,
        billing_mode: str = "immediate",
        expire_days: int = 0,
    ) -> Dict[str, Any]:
        if not user:
            return {"ok": False, "message": "未登录"}
        appid = str(appid).strip()
        if not appid.isdigit():
            return {"ok": False, "message": "AppID 无效"}
        await self.ensure_ready()
        ok_import, import_msg = await self.service.check_importable(appid, deep_probe=True)
        if not ok_import:
            return {"ok": False, "message": import_msg}
        mode = billing_mode if billing_mode in ("immediate", "on_activate") else "immediate"
        count = max(1, min(int(count), 100))
        if not expire_days:
            expire_days = int(
                self.admin._data.get("settings", {}).get("cdk_default_expire_days", 0)
            )
        charged_immediate = mode == "immediate"
        try:
            agent_id = user.id if user.role == "agent" else ""
            commissions: List[Dict[str, Any]] = []
            if user.role == "agent":
                pending = self.cdk.count_agent_pending_precharge(user.id)
                self.admin.check_agent_generation(user, count, mode, pending)
                if charged_immediate:
                    commissions = self.admin.charge_agent_for_cdks(user.id, count)
            codes = self.cdk.generate_batch(
                appid,
                count,
                name=name,
                note=note,
                created_by=user.username,
                agent_id=agent_id,
                billing_mode=mode,
                expire_days=expire_days,
                charged=charged_immediate,
            )
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        except Exception as e:
            if user.role == "agent" and charged_immediate:
                try:
                    self.admin.refund_agent_cdk_charge(
                        user.id, count, user.id, "生成失败回滚"
                    )
                except Exception:
                    logging.exception("CDK 生成回滚失败")
            return {"ok": False, "message": str(e)}
        result = {
            "ok": True,
            "appid": str(appid),
            "name": name,
            "cdks": codes,
            "billing_mode": mode,
        }
        if commissions:
            result["commission"] = commissions
        if user and user.role == "agent":
            fresh = self.admin.get_raw_user(user.id)
            if fresh:
                result["user"] = self.enrich_user_public(user.id)
        return result

    def enrich_user_public(self, user_id: str) -> Dict[str, Any]:
        self.admin._refresh()
        raw = self.admin.get_raw_user(user_id)
        if not raw:
            user = self.admin.get_user(user_id)
            if not user:
                return {}
            return {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "display_name": user.display_name,
                "enabled": user.enabled,
                "parent_id": user.parent_id,
                "cdk_quota": user.cdk_quota,
                "cdk_generated": user.cdk_generated,
                "cdk_cost_price": round(user.cdk_cost_price, 4),
                "balance": round(user.balance, 4),
                "note": user.note,
                "created_at": user.created_at,
            }
        item = self.admin._user_public(raw)
        if raw.get("role") == "agent":
            item["cdk_pending"] = self.cdk.count_agent_pending_precharge(user_id)
            item["cdk_pending_active"] = self.cdk.count_agent_pending_active(user_id)
            item["available_balance"] = self.admin.available_balance(user_id)
        return item

    def list_cdks(
        self,
        limit: int = 200,
        user: Optional[AdminUser] = None,
        appid: str = "",
        appids: Optional[set] = None,
        name_contains: str = "",
    ) -> List[Dict[str, Any]]:
        self.cdk.refresh()
        agent_filter = ""
        if user and user.role == "agent":
            agent_filter = user.id
        items = []
        for code, record in self.cdk.list_keys(
            limit=limit,
            agent_id=agent_filter,
            appid=appid,
            appids=appids,
            name_contains=name_contains,
        ):
            items.append(
                {
                    "cdk": code,
                    "appid": record.appid,
                    "name": record.name,
                    "used": record.used,
                    "used_at": record.used_at,
                    "used_machine": record.used_machine,
                    "note": record.note,
                    "created_by": record.created_by,
                    "agent_id": record.agent_id,
                    "created_at": record.created_at,
                    "billing_mode": record.billing_mode,
                    "charged": record.charged,
                    "expires_at": record.expires_at,
                    "revoked": record.revoked,
                    "revoked_at": record.revoked_at,
                }
            )
        return items

    def _apply_activation_billing(self, cdk_code: str) -> List[Dict[str, Any]]:
        return self.admin.billing_on_activation(self.cdk, cdk_code)

    def dashboard_stats(self, user: Optional[AdminUser] = None) -> Dict[str, Any]:
        agent_filter = user.id if user and user.role == "agent" else ""
        cdk_stats = self.cdk.stats(agent_id=agent_filter)
        users = self.admin.list_users()
        agents = [u for u in users if u.get("role") == "agent"]
        logs = self.cdk.list_activation_logs(limit=500, agent_id=agent_filter)
        today = datetime.now().strftime("%Y-%m-%d")
        today_used = sum(1 for x in logs if str(x.get("used_at", "")).startswith(today))
        payload = {
            "cdk_total": cdk_stats["total"],
            "cdk_used": cdk_stats["used"],
            "cdk_unused": cdk_stats["unused"],
            "agent_count": len(agents) if user and user.role == "superadmin" else 0,
            "user_count": len(users) if user and user.role == "superadmin" else 0,
            "today_activated": today_used,
            "site_name": self.admin.site_name(),
            "base_cdk_price": self.admin.base_cdk_price(),
        }
        if user and user.role == "agent":
            raw = self.admin.get_raw_user(user.id)
            if raw:
                payload["balance"] = round(float(raw.get("balance", 0)), 4)
                payload["available_balance"] = self.admin.available_balance(user.id)
                payload["pending_withdraw"] = self.admin.pending_withdraw_total(user.id)
                payload["withdrawable_balance"] = self.admin.withdrawable_balance(user.id)
                payload["available_withdrawable_balance"] = self.admin.available_withdrawable_balance(user.id)
                payload["cdk_cost_price"] = round(float(raw.get("cdk_cost_price", 0)), 4)
                payload["sub_agent_count"] = self.admin.count_sub_agents(user.id)
                payload["cdk_pending"] = self.cdk.count_agent_pending_precharge(user.id)
                payload["cdk_pending_active"] = self.cdk.count_agent_pending_active(user.id)
        if user and user.role == "superadmin":
            payload["pending_recharge_count"] = self.admin.count_pending_recharge_requests()
            payload["pending_withdraw_count"] = self.admin.count_pending_withdraw_requests()
            payload["announcement"] = self.admin.get_public_announcement()
            payload["github_token_configured"] = bool(
                normalize_github_tokens(self._load_app_config())
            )
            payload["github_token_count"] = len(normalize_github_tokens(self._load_app_config()))
        return payload

    @staticmethod
    def _config_path() -> Path:
        return ROOT / "config.json"

    def _load_app_config(self) -> Dict[str, Any]:
        path = self._config_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @staticmethod
    def _mask_secret(value: str) -> str:
        return mask_github_token(value)

    def get_github_token_status(self) -> Dict[str, Any]:
        cfg = self._load_app_config()
        tokens = normalize_github_tokens(cfg)
        return {
            "configured": bool(tokens),
            "count": len(tokens),
            "masked_tokens": [self._mask_secret(t) for t in tokens],
            "masked": self._mask_secret(tokens[0]) if tokens else "",
            "github_token_configured": bool(tokens),
            "github_token_count": len(tokens),
        }

    async def update_github_tokens(self, tokens: List[str]) -> Dict[str, Any]:
        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in tokens:
            t = str(raw or "").strip()
            if not t:
                continue
            if len(t) < 10:
                raise ValueError("Token 格式无效，请检查是否完整复制")
            if t in seen:
                continue
            seen.add(t)
            cleaned.append(t)
        path = self._config_path()
        cfg = self._load_app_config()
        cfg["Github_Personal_Tokens"] = cleaned
        cfg["Github_Personal_Token"] = cleaned[0] if cleaned else ""
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        if self._ready:
            loaded = await self.service.backend.load_config()
            if loaded:
                self.service.backend.config = loaded
                self.service.backend._github_token_idx = 0
        return self.get_github_token_status()

    async def update_github_token(self, token: str) -> Dict[str, Any]:
        """兼容单 Token 保存。"""
        cleaned = str(token or "").strip()
        if not cleaned:
            return await self.update_github_tokens([])
        return await self.update_github_tokens([cleaned])

    def get_catalog_sync_settings(self) -> Dict[str, Any]:
        cfg = self._load_app_config()
        return {
            "auto_sync_enabled": bool(cfg.get("Catalog_Auto_Sync_Enabled", True)),
            "auto_sync_hours": max(1, int(cfg.get("Catalog_Auto_Sync_Hours") or 24)),
            "merge_on_sync": bool(cfg.get("Catalog_Merge_On_Sync", True)),
            "full_steam_catalog": bool(cfg.get("Full_Steam_Catalog", True)),
        }

    def update_catalog_sync_settings(
        self,
        auto_sync_enabled: Optional[bool] = None,
        auto_sync_hours: Optional[int] = None,
        merge_on_sync: Optional[bool] = None,
    ) -> Dict[str, Any]:
        path = self._config_path()
        cfg = self._load_app_config()
        if auto_sync_enabled is not None:
            cfg["Catalog_Auto_Sync_Enabled"] = bool(auto_sync_enabled)
        if auto_sync_hours is not None:
            cfg["Catalog_Auto_Sync_Hours"] = max(1, min(int(auto_sync_hours), 168))
        if merge_on_sync is not None:
            cfg["Catalog_Merge_On_Sync"] = bool(merge_on_sync)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        if self._ready and self.service.backend.config is not None:
            self.service.backend.config.update(cfg)
        return self.get_catalog_sync_settings()

    def get_catalog_sync_status(self) -> Dict[str, Any]:
        meta = self.service.get_catalog_sync_meta()
        settings = self.get_catalog_sync_settings()
        manifest_cache = read_json_cache(DOC_CATALOG)
        steam_cache = read_json_cache(DOC_STEAM_CATALOG)
        return {
            **settings,
            "last_sync_at": meta.get("updated_at", ""),
            "last_manifest": meta.get("manifest", {}),
            "last_steam": meta.get("steam", {}),
            "manifest_cached": len(manifest_cache.get("appids") or []),
            "steam_cached": int(steam_cache.get("count") or len(steam_cache.get("apps") or {})),
            "storage": get_database_label() if is_database_enabled() else "local",
        }

    async def sync_catalogs(self) -> Dict[str, Any]:
        await self.ensure_ready()
        return await self.service.sync_all_catalogs()

    async def preview_manual_game(self, app_id: str) -> Dict[str, Any]:
        await self.ensure_ready()
        return await self.service.preview_manual_game(app_id)

    async def probe_game_depot(self, app_id: str) -> Dict[str, Any]:
        await self.ensure_ready()
        return await self.service.probe_game_depot(app_id)

    async def enrich_game_meta(
        self,
        app_id: str = "",
        force: bool = False,
        all_manifest: bool = False,
        limit: int = 0,
    ) -> Dict[str, Any]:
        await self.ensure_ready()
        if all_manifest:
            return await self.service.enrich_manifest_metadata_batch(
                app_ids=None,
                force=force,
                limit=limit,
            )
        app_id = str(app_id).strip()
        if not app_id:
            return {"ok": False, "message": "请提供 appid 或 all_manifest=true"}
        return await self.service.enrich_game_meta(app_id, force=force)

    async def add_manual_manifest_game(
        self,
        app_id: str,
        name: str = "",
        force: bool = False,
        probe: bool = True,
        try_import: bool = False,
        operator: str = "",
    ) -> Dict[str, Any]:
        await self.ensure_ready()
        return await self.service.add_manual_manifest_game(
            app_id,
            name=name,
            force=force,
            probe=probe,
            try_import=try_import,
            operator=operator,
        )

    async def import_game_plugins(self, app_id: str) -> Dict[str, Any]:
        await self.ensure_ready()
        return await self.service.import_game_to_server_plugins(app_id)

    async def list_games(
        self,
        query: str = "",
        page: int = 1,
        page_size: int = 48,
        refresh: bool = False,
        installed_only: bool = False,
        catalog_filter: str = "all",
        manifest_filter: str = "",
        sort: str = "appid_desc",
    ) -> Dict[str, Any]:
        await self.ensure_ready()
        games, total, stats = await self.service.list_catalog_games(
            query=query,
            page=page,
            page_size=page_size,
            refresh=refresh,
            installed_only=installed_only,
            catalog_filter=catalog_filter,
            manifest_filter=manifest_filter,
            sort=sort,
        )
        return {"ok": True, "items": games, "total": total, "stats": stats}

    async def redeem(self, cdk: str, machine: str = "") -> Dict[str, Any]:
        await self.ensure_ready()
        code = self.cdk.normalize_cdk(cdk)
        validation = self.cdk.validate(cdk)
        if not validation.valid:
            return {"ok": False, "message": validation.message}

        billing_code = validation.cdk or code
        try:
            self.admin.check_activation_billing(self.cdk, billing_code)
        except ValueError as e:
            return {"ok": False, "message": str(e)}

        app_id = validation.appid
        async with self._lock:
            cached = self._plugin_cache.get(app_id)
            if not cached:
                sources = self.service.get_manifest_sources()
                if not sources:
                    return {"ok": False, "message": "服务端无可用清单源"}
                source = sources[0]
                options = ImportOptions(
                    auto_update_manifest=False,
                    add_all_dlc=False,
                    patch_workshop_key=False,
                )
                result = await self.service.import_game_with_fallback(
                    app_id, source, options
                )
                if not result.success:
                    return {"ok": False, "message": f"入库失败: {result.message}"}

                steam = self.service.backend.steam_path
                if not steam:
                    return {"ok": False, "message": "服务端未配置 Steam 路径"}

                lua_path = steam / "config" / "stplug-in" / f"{app_id}.lua"
                st_path = steam / "config" / "stplug-in" / f"{app_id}.st"
                if not lua_path.exists():
                    return {"ok": False, "message": "插件生成失败"}

                lua_bytes = lua_path.read_bytes()
                cached = {"lua": lua_bytes}
                if st_path.exists():
                    cached["st"] = st_path.read_bytes()
                manifest_refs = re.findall(
                    r'setManifestid\(\s*(\d+)\s*,\s*"(\d+)"',
                    lua_bytes.decode("utf-8", errors="ignore"),
                )
                manifests: List[Dict[str, bytes]] = []
                for depot_id, manifest_id in manifest_refs:
                    filename = f"{depot_id}_{manifest_id}.manifest"
                    for depot_dir in (
                        steam / "config" / "depotcache",
                        steam / "depotcache",
                    ):
                        manifest_path = depot_dir / filename
                        if manifest_path.exists():
                            manifests.append(
                                {"name": filename, "data": manifest_path.read_bytes()}
                            )
                            break
                if manifests:
                    cached["manifests"] = manifests
                self._plugin_cache[app_id] = cached

        consume_result = self.cdk.consume(cdk)
        if not consume_result.valid:
            return {"ok": False, "message": consume_result.message}

        try:
            commissions = self._apply_activation_billing(billing_code)
        except Exception as e:
            self.cdk.unconsume(billing_code)
            logging.exception("activation billing failed for %s", billing_code)
            msg = str(e) if isinstance(e, ValueError) else "扣费失败，请稍后重试"
            return {"ok": False, "message": msg}

        payload: Dict[str, Any] = {
            "ok": True,
            "appid": app_id,
            "name": validation.name or f"AppID {app_id}",
            "cdk": validation.cdk or cdk,
            "lua_b64": base64.b64encode(cached["lua"]).decode("ascii"),
            "message": "CDK 兑换成功",
        }
        if "st" in cached:
            payload["st_b64"] = base64.b64encode(cached["st"]).decode("ascii")
        if cached.get("manifests"):
            payload["manifests"] = [
                {
                    "name": item["name"],
                    "b64": base64.b64encode(item["data"]).decode("ascii"),
                }
                for item in cached["manifests"]
            ]
        if machine:
            payload["machine"] = machine
        if commissions:
            payload["commission"] = commissions
        try:
            from extensions.routes import log_activation

            log_activation(validation.cdk or cdk, app_id, machine, True)
        except Exception:
            pass
        return payload

    def recycle_cdk(self, cdk: str, user: Optional[AdminUser] = None, note: str = "") -> Dict[str, Any]:
        code = self.cdk.normalize_cdk(cdk)
        raw = self.cdk.get_key_raw(code)
        if not raw:
            return {"ok": False, "message": "CDK 不存在"}
        if user and user.role == "agent" and str(raw.get("agent_id", "")) != user.id:
            return {"ok": False, "message": "无权回收此 CDK"}
        try:
            ok, snapshot = self.cdk.recycle_key(
                cdk,
                operator=user.username if user else "",
                note=note,
            )
            if not ok or not snapshot:
                return {"ok": False, "message": "CDK 不存在"}
            billing = self.admin.recycle_cdk_billing(snapshot, user.id if user else "")
            base = ""  # filled by handler
            return {
                "ok": True,
                "message": "CDK 已回收",
                "cdk": code,
                "appid": snapshot.get("appid"),
                "balance_refund": billing.get("balance_refund", 0),
                "quota_refund": billing.get("quota_refund", 0),
                "user": self.enrich_user_public(billing["agent_id"]) if billing.get("agent_id") else None,
                "client_revoke_hint": (
                    f'已标记回收；客户端若已激活过将每 5 分钟自动禁玩，'
                    f'也可手动: $cdk="{code}"; irm {{base}}/revoke.ps1 | iex'
                ),
                "auto_revoke_enabled": True,
            }
        except ValueError as e:
            return {"ok": False, "message": str(e)}


ensure_database_bootstrapped()
SERVER = WebCdkServer()

import extensions.routes as ext_routes

ext_routes.SERVER = SERVER
LOOP = asyncio.new_event_loop()


def _start_background_loop() -> None:
    asyncio.set_event_loop(LOOP)
    LOOP.run_forever()


threading.Thread(target=_start_background_loop, daemon=True, name="WebCdkLoop").start()


def _start_catalog_auto_sync() -> None:
    def _worker() -> None:
        import time as _time

        from extensions.sync_progress import start_background_catalog_sync

        _time.sleep(30)
        while True:
            hours = 24
            try:
                cfg = SERVER._load_app_config()
                hours = max(1, int(cfg.get("Catalog_Auto_Sync_Hours") or 24))
                if cfg.get("Catalog_Auto_Sync_Enabled", True):
                    result = start_background_catalog_sync(SERVER, source="auto")
                    logging.info("自动同步任务: %s", result.get("message"))
            except Exception as e:
                logging.exception("自动同步游戏清单失败: %s", e)
            _time.sleep(hours * 3600)

    threading.Thread(target=_worker, daemon=True, name="CatalogAutoSync").start()


_start_catalog_auto_sync()


def run_async(coro, timeout: int = 600):
    future = asyncio.run_coroutine_threadsafe(coro, LOOP)
    return future.result(timeout=timeout)


def build_install_script(api_base: str) -> bytes:
    template = ACTIVATE_PS1.read_text(encoding="utf-8")
    content = template.replace(API_BASE_PLACEHOLDER, api_base.rstrip("/"))
    return content.encode("utf-8")


def build_hook_script(api_base: str) -> bytes:
    template = HOOK_PS1.read_text(encoding="utf-8")
    content = template.replace(API_BASE_PLACEHOLDER, api_base.rstrip("/"))
    return content.encode("utf-8")


def build_fix_script(api_base: str) -> bytes:
    template = FIX_PS1.read_text(encoding="utf-8")
    content = template.replace(API_BASE_PLACEHOLDER, api_base.rstrip("/"))
    return content.encode("utf-8")


REVOKE_PS1 = ROOT / "revoke.ps1"
SYNC_REVOKE_PS1 = ROOT / "sync_revoke.ps1"


def build_revoke_script(api_base: str) -> bytes:
    template = REVOKE_PS1.read_text(encoding="utf-8")
    content = template.replace(API_BASE_PLACEHOLDER, api_base.rstrip("/"))
    return content.encode("utf-8")


def build_sync_revoke_script(api_base: str) -> bytes:
    template = SYNC_REVOKE_PS1.read_text(encoding="utf-8")
    content = template.replace(API_BASE_PLACEHOLDER, api_base.rstrip("/"))
    return content.encode("utf-8")


class WebHandler(BaseHTTPRequestHandler):
    public_url: str = ""
    listen_port: int = 8787

    def _api_base_for_request(self) -> str:
        if self.public_url:
            return self.public_url.rstrip("/")
        host = self.headers.get("Host", f"127.0.0.1:{self.listen_port}").strip()
        if not host.startswith("http"):
            scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
            return f"{scheme}://{host}".rstrip("/")
        return host.rstrip("/")

    def _read_json_body(self) -> Dict[str, Any]:
        cached = getattr(self, "_json_body_cache", None)
        if cached is not None:
            return cached
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            parsed = {}
            # 兼容部分第三方系统以 x-www-form-urlencoded 提交参数
            try:
                from urllib.parse import parse_qs

                form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                if isinstance(form, dict) and form:
                    parsed = {k: (v[0] if isinstance(v, list) else v) for k, v in form.items()}
            except Exception:
                parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        self._json_body_cache = parsed
        return parsed

    def _send_json(self, code: int, data: Dict[str, Any], *, auth_cookie: str = "", clear_auth_cookie: bool = False) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        if auth_cookie:
            self.send_header("Set-Cookie", self._make_auth_cookie(auth_cookie))
        if clear_auth_cookie:
            self.send_header("Set-Cookie", self._make_auth_cookie("", max_age=0))
        self.end_headers()
        self.wfile.write(body)

    def _cookie_secure(self) -> bool:
        if str(self.headers.get("X-Forwarded-Proto", "")).lower() == "https":
            return True
        host = str(self.headers.get("Host", "")).lower()
        return "127.0.0.1" not in host and "localhost" not in host

    def _parse_cookies(self) -> Dict[str, str]:
        raw = str(self.headers.get("Cookie", ""))
        out: Dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                key, val = part.split("=", 1)
                out[key.strip()] = val.strip()
        return out

    def _make_auth_cookie(self, token: str, *, max_age: int = 86400) -> str:
        flags = ["Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self._cookie_secure():
            flags.append("Secure")
        return f"admin_session={token}; " + "; ".join(flags)

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
        return str(self.client_address[0] if self.client_address else "unknown")

    def _send_csv(self, filename: str, rows: List[List[str]]) -> None:
        import csv
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in rows:
            writer.writerow(row)
        body = buf.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _get_token(self) -> str:
        auth = self.headers.get("Authorization", "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return self._parse_cookies().get("admin_session", "")

    def _require_auth(self, superadmin_only: bool = False) -> Optional[AdminUser]:
        from extensions.security import check_ip_allowed

        if not check_ip_allowed(self._client_ip()):
            self._send_json(403, {"ok": False, "message": "IP 不在白名单"})
            return None
        user = SERVER.admin.verify_token(self._get_token())
        if not user:
            self._send_json(401, {"ok": False, "message": "请先登录"})
            return None
        if not user.enabled:
            self._send_json(403, {"ok": False, "message": "账号已禁用"})
            return None
        if superadmin_only and user.role != "superadmin":
            self._send_json(403, {"ok": False, "message": "需要超级管理员权限"})
            return None
        return user

    def _load_box_config(self) -> Dict[str, Any]:
        path = ROOT / "config.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _require_box_user(self) -> Optional[Dict[str, Any]]:
        token = self._get_token()
        user = SERVER.box_auth.verify_token(token)
        if not user:
            self._send_json(401, {"ok": False, "message": "请先登录"})
            return None
        return user

    def _irm_cmd_base(self) -> str:
        return irm_cmd_base(self._api_base_for_request(), self.listen_port)

    def _commands_payload(self) -> Dict[str, str]:
        base = self._api_base_for_request()
        cmd = self._irm_cmd_base()
        return {
            "public_url": base,
            "public_cmd_base": cmd,
            "install_cmd": f"irm {cmd} | iex",
            "hook_cmd": f"irm {cmd}/hook | iex",
            "cdk_cmd": f'$cdk="XXXX-XXXX-XXXX-XXXX"; irm {cmd} | iex',
        }

    def _send_redirect(self, location: str, *, permanent: bool = False, vary_accept: bool = False) -> None:
        self.send_response(301 if permanent else 302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        if vary_accept:
            self.send_header("Vary", "Accept")
        self.end_headers()

    def _should_redirect_root_to_steam(self) -> bool:
        """浏览器访问 / 跳转 Steam；PowerShell「irm 域名 | iex」仍取激活脚本。"""
        ua = (self.headers.get("User-Agent") or "").lower()
        if "powershell" in ua or "windowspowershell" in ua or "pwsh" in ua:
            return False
        accept = (self.headers.get("Accept") or "").lower()
        if not accept or "*/*" in accept:
            return False
        if "text/html" in accept and "text/plain" not in accept:
            return True
        return False

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        *,
        no_cache: bool = False,
        vary_accept: bool = False,
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if no_cache:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
        if vary_accept:
            self.send_header("Vary", "Accept")
        self.end_headers()
        self.wfile.write(data)

    def _send_ps1(self, *, vary_accept: bool = False) -> None:
        self._send_bytes(
            build_install_script(self._api_base_for_request()),
            "text/plain; charset=utf-8",
            no_cache=True,
            vary_accept=vary_accept,
        )

    def _send_hook_ps1(self) -> None:
        self._send_bytes(build_hook_script(self._api_base_for_request()), "text/plain; charset=utf-8")

    def _send_html_file(self, path: Path) -> None:
        if not path.exists():
            self.send_error(404, f"{path.name} missing")
            return
        html = path.read_text(encoding="utf-8")
        html = html.replace("__PUBLIC_URL__", self._api_base_for_request())
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", no_cache=True)

    def _send_admin_html(self) -> None:
        self._send_html_file(ADMIN_HTML)

    def _send_admin_login_html(self) -> None:
        self._send_html_file(ADMIN_LOGIN_HTML)

    def _send_login_html(self) -> None:
        self._send_html_file(LOGIN_HTML)

    def _send_register_html(self) -> None:
        self._send_html_file(REGISTER_HTML)

    def _send_portal_html(self) -> None:
        self._send_html_file(PORTAL_HTML)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        qs = parse_qs(urlparse(self.path).query)

        from extensions.routes import handle_get

        if handle_get(self, path, qs):
            return

        if path == "/health":
            db_ok = is_database_enabled()
            payload = {"ok": True, "service": "web-cdk", "database": get_database_label() if db_ok else "off"}
            if db_ok:
                try:
                    from database import get_store
                    store = get_store()
                    payload["db_ping"] = store is not None
                except Exception as e:
                    payload["db_ping"] = False
                    payload["db_error"] = str(e)
            else:
                payload["db_ping"] = True
            self._send_json(200 if payload.get("db_ping", True) else 503, payload)
            return

        if path == "/":
            if self._should_redirect_root_to_steam():
                self._send_redirect("https://store.steampowered.com/", vary_accept=True)
            else:
                self._send_ps1(vary_accept=True)
            return

        if path in ("/install.ps1", "/activate.ps1", "/box.ps1"):
            self._send_ps1()
            return

        if path in ("/hook", "/hook.ps1"):
            self._send_hook_ps1()
            return

        if path in ("/fix", "/fix.ps1", "/fix-client.ps1"):
            self._send_bytes(build_fix_script(self._api_base_for_request()), "text/plain; charset=utf-8")
            return

        if path in ("/revoke", "/revoke.ps1"):
            self._send_bytes(build_revoke_script(self._api_base_for_request()), "text/plain; charset=utf-8")
            return

        if path in ("/sync-revoke", "/sync-revoke.ps1"):
            self._send_bytes(build_sync_revoke_script(self._api_base_for_request()), "text/plain; charset=utf-8")
            return

        if path in ("/admin/login", "/admin-login", "/admin-login.html"):
            self._send_admin_login_html()
            return

        if path in ("/admin", "/admin.html"):
            self._send_admin_html()
            return

        if path in ("/login", "/login.html"):
            self._send_login_html()
            return

        if path in ("/register", "/register.html"):
            self._send_register_html()
            return

        if path in ("/portal", "/portal.html", "/web", "/app", "/user"):
            self._send_portal_html()
            return

        if path == "/api/box/me":
            user = self._require_box_user()
            if user:
                self._send_json(200, {"ok": True, "user": user})
            return

        if path == "/api/public/info":
            payload = {"ok": True, **SERVER.admin.get_settings_public()}
            payload.update(self._commands_payload())
            payload["announcement"] = SERVER.admin.get_public_announcement()
            self._send_json(200, payload)
            return

        if path == "/api/public/announcement":
            self._send_json(200, {"ok": True, **SERVER.admin.get_public_announcement()})
            return

        if path == "/api/public/cdk/status":
            cdk = (qs.get("cdk") or [""])[0]
            info = SERVER.cdk.get_public_status(str(cdk))
            if not info:
                self._send_json(404, {"ok": False, "message": "CDK 不存在"})
                return
            self._send_json(200, {"ok": True, **info})
            return

        if path == "/api/public/revoked":
            limit = int((qs.get("limit") or ["200"])[0])
            items = SERVER.cdk.list_revoked(limit=limit)
            self._send_json(200, {"ok": True, "items": items})
            return

        if path == "/api/public/search":
            q = (qs.get("q") or [""])[0]
            try:
                games = run_async(SERVER.search_games(q))
                self._send_json(200, {"ok": True, "games": games})
            except Exception as e:
                logging.exception("public search failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/public/games":
            q = (qs.get("q") or [""])[0]
            page = max(1, int((qs.get("page") or ["1"])[0]))
            limit = max(1, min(int((qs.get("limit") or ["24"])[0]), 96))
            refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
            catalog_filter = (qs.get("filter") or [""])[0] or "all"
            manifest_filter = (qs.get("manifest") or [""])[0]
            sort = (qs.get("sort") or ["appid_desc"])[0]
            try:
                result = run_async(
                    SERVER.list_games(
                        query=q,
                        page=page,
                        page_size=limit,
                        refresh=refresh,
                        installed_only=False,
                        catalog_filter=catalog_filter,
                        manifest_filter=manifest_filter,
                        sort=sort,
                    )
                )
                self._send_json(200, result)
            except Exception as e:
                logging.exception("public list games failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/public/invite":
            code = (qs.get("code") or qs.get("invite") or [""])[0]
            info = SERVER.admin.get_invite_public(str(code))
            if not info:
                self._send_json(404, {"ok": False, "message": "邀请码无效或已失效"})
                return
            self._send_json(200, {"ok": True, **info})
            return

        if path == "/api/admin/invite":
            user = self._require_auth()
            if not user:
                return
            if user.role != "agent":
                self._send_json(403, {"ok": False, "message": "仅代理可生成邀请链接"})
                return
            try:
                info = SERVER.admin.get_agent_invite_info(user.id)
                base = self._api_base_for_request().rstrip("/")
                self._send_json(200, {
                    "ok": True,
                    "register_url": f"{base}/register?code={info['invite_code']}",
                    **info,
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/me":
            user = self._require_auth()
            if not user:
                return
            payload = {"ok": True, "user": SERVER.enrich_user_public(user.id)}
            payload.update(self._commands_payload())
            self._send_json(200, payload)
            return

        if path == "/api/admin/dashboard":
            user = self._require_auth()
            if not user:
                return
            stats = SERVER.dashboard_stats(user)
            stats["ok"] = True
            stats.update(self._commands_payload())
            stats["user"] = SERVER.enrich_user_public(user.id)
            self._send_json(200, stats)
            return

        if path == "/api/admin/users":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            self._send_json(200, {"ok": True, "items": SERVER.admin.list_admin_accounts()})
            return

        if path == "/api/admin/agents":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            self._send_json(200, {"ok": True, "items": SERVER.admin.list_all_agents()})
            return

        if path == "/api/admin/sub-agents":
            user = self._require_auth()
            if not user:
                return
            if user.role == "superadmin":
                parent = (qs.get("parent_id") or [""])[0]
                items = SERVER.admin.list_users(role="agent", parent_id=parent) if parent else SERVER.admin.list_users(role="agent")
            else:
                items = SERVER.admin.list_sub_agents(user.id)
            self._send_json(200, {"ok": True, "items": items})
            return

        if path == "/api/admin/wallet":
            user = self._require_auth()
            if not user:
                return
            summary = SERVER.admin.wallet_summary(user.id)
            summary["ok"] = True
            self._send_json(200, summary)
            return

        if path == "/api/admin/recharge/requests":
            user = self._require_auth()
            if not user:
                return
            if user.role == "agent":
                items = SERVER.admin.list_recharge_requests(user_id=user.id, limit=50)
            elif user.role == "superadmin":
                items = SERVER.admin.list_recharge_requests(limit=200)
            else:
                items = []
            self._send_json(200, {"ok": True, "items": items})
            return

        if path == "/api/admin/recharge/logs":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            limit = int((qs.get("limit") or ["100"])[0])
            items = SERVER.admin.list_recharge_logs(limit=limit)
            self._send_json(200, {"ok": True, "items": items})
            return

        if path == "/api/admin/reports":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            report = SERVER.admin.operations_report(SERVER.cdk)
            report["ok"] = True
            self._send_json(200, report)
            return

        if path == "/api/admin/agent-tree":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            root = (qs.get("root_id") or [""])[0]
            tree = SERVER.admin.build_agent_tree(str(root))
            self._send_json(200, {"ok": True, "tree": tree})
            return

        if path == "/api/admin/commissions":
            user = self._require_auth()
            if not user:
                return
            limit = int((qs.get("limit") or ["100"])[0])
            if user.role == "superadmin":
                items = SERVER.admin.list_all_commission_logs(limit=limit)
            else:
                items = SERVER.admin.list_commission_logs(user.id, limit=limit)
            self._send_json(200, {"ok": True, "items": items})
            return

        if path == "/api/admin/audit-logs":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            limit = int((qs.get("limit") or ["100"])[0])
            self._send_json(200, {"ok": True, "items": SERVER.admin.list_audit_logs(limit=limit)})
            return

        if path == "/api/admin/withdraw/requests":
            user = self._require_auth()
            if not user:
                return
            if user.role == "agent":
                items = SERVER.admin.list_withdraw_requests(user_id=user.id, limit=50)
            elif user.role == "superadmin":
                items = SERVER.admin.list_withdraw_requests(limit=200)
            else:
                items = []
            self._send_json(200, {"ok": True, "items": items})
            return

        if path == "/api/admin/export/logs.csv":
            user = self._require_auth()
            if not user:
                return
            limit = int((qs.get("limit") or ["500"])[0])
            agent_filter = user.id if user.role == "agent" else ""
            logs = SERVER.cdk.list_activation_logs(limit=limit, agent_id=agent_filter)
            rows = [["CDK", "AppID", "游戏名", "激活时间", "机器码", "代理ID"]]
            for it in logs:
                rows.append([
                    str(it.get("cdk", "")),
                    str(it.get("appid", "")),
                    str(it.get("name", "")),
                    str(it.get("used_at", "")),
                    str(it.get("used_machine", "")),
                    str(it.get("agent_id", "")),
                ])
            self._send_csv("activation_logs.csv", rows)
            return

        if path == "/api/admin/export/recharge.csv":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            items = SERVER.admin.list_recharge_requests(limit=500)
            rows = [["时间", "用户名", "类型", "数量", "状态", "备注", "凭证", "审核说明"]]
            for it in items:
                rows.append([
                    str(it.get("created_at", "")),
                    str(it.get("username", "")),
                    str(it.get("type", "")),
                    str(it.get("amount", "")),
                    str(it.get("status", "")),
                    str(it.get("note", "")),
                    str(it.get("proof", "")),
                    str(it.get("review_note", "")),
                ])
            self._send_csv("recharge_requests.csv", rows)
            return

        if path == "/api/admin/settings":
            user = self._require_auth()
            if not user:
                return
            self._send_json(200, {"ok": True, **SERVER.admin.get_settings_public()})
            return

        if path == "/api/admin/github-token":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            self._send_json(200, {"ok": True, **SERVER.get_github_token_status()})
            return

        if path == "/api/admin/catalog/sync-status":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            self._send_json(200, {"ok": True, **SERVER.get_catalog_sync_status()})
            return

        if path == "/api/admin/logs":
            user = self._require_auth()
            if not user:
                return
            limit = int((qs.get("limit") or ["100"])[0])
            agent_filter = user.id if user.role == "agent" else ""
            logs = SERVER.cdk.list_activation_logs(limit=limit, agent_id=agent_filter)
            self._send_json(200, {"ok": True, "items": logs})
            return

        if path == "/api/admin/search":
            user = self._require_auth()
            if not user:
                return
            q = (qs.get("q") or [""])[0]
            manifest_only = (qs.get("manifest_only") or qs.get("manifest") or [""])[0] in (
                "1",
                "true",
                "yes",
                "has_manifest",
            )
            try:
                games = run_async(SERVER.search_games(q, manifest_only=manifest_only))
                self._send_json(200, {"ok": True, "games": games, "manifest_only": manifest_only})
            except Exception as e:
                logging.exception("search failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/cdk/list":
            user = self._require_auth()
            if not user:
                return
            appid_q = str((qs.get("appid") or [""])[0]).strip()
            game_q = str((qs.get("game") or qs.get("q") or [""])[0]).strip()
            by_game = bool(appid_q or game_q)
            default_limit = "5000" if by_game else "200"
            limit = int((qs.get("limit") or [default_limit])[0])
            limit = min(max(limit, 1), 20000)

            appids: Optional[set] = None
            name_contains = ""
            if appid_q:
                if not appid_q.isdigit():
                    self._send_json(400, {"ok": False, "message": "AppID 须为数字"})
                    return
                appid_filter = appid_q
            elif game_q:
                appid_filter = ""
                if game_q.isdigit():
                    appid_filter = game_q
                else:
                    try:
                        games = run_async(SERVER.search_games(game_q))
                    except Exception as e:
                        self._send_json(500, {"ok": False, "message": str(e)})
                        return
                    appids = {str(g.get("appid", "")) for g in games if g.get("appid")}
                    name_contains = game_q
            else:
                appid_filter = ""

            items = SERVER.list_cdks(
                limit=limit,
                user=user,
                appid=appid_filter,
                appids=appids,
                name_contains=name_contains,
            )
            filt = (qs.get("filter") or [""])[0]
            if filt == "unused":
                items = [x for x in items if not x.get("used") and not x.get("revoked")]
            elif filt == "used":
                items = [x for x in items if x.get("used")]

            payload: Dict[str, Any] = {"ok": True, "items": items, "count": len(items)}
            if by_game:
                resolved_appid = appid_filter or (items[0].get("appid") if items else "")
                game_name = ""
                if items:
                    game_name = str(items[0].get("name") or "")
                elif appid_filter:
                    try:
                        games = run_async(SERVER.search_games(appid_filter))
                        if games:
                            game_name = str(games[0].get("name", ""))
                    except Exception:
                        pass
                payload["appid"] = resolved_appid
                payload["game_name"] = game_name
                payload["game_query"] = game_q or appid_q
            self._send_json(200, payload)
            return

        if path == "/api/admin/games":
            user = self._require_auth()
            if not user:
                return
            q = (qs.get("q") or [""])[0]
            page = int((qs.get("page") or ["1"])[0])
            limit = int((qs.get("limit") or ["48"])[0])
            refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
            installed_only = (qs.get("installed_only") or ["0"])[0] in ("1", "true", "yes")
            catalog_filter = (qs.get("filter") or [""])[0] or "all"
            manifest_filter = (qs.get("manifest") or [""])[0]
            sort = (qs.get("sort") or ["appid_desc"])[0]
            try:
                result = run_async(
                    SERVER.list_games(
                        query=q,
                        page=page,
                        page_size=limit,
                        refresh=refresh,
                        installed_only=installed_only,
                        catalog_filter=catalog_filter,
                        manifest_filter=manifest_filter,
                        sort=sort,
                    )
                )
                self._send_json(200, result)
            except Exception as e:
                logging.exception("list games failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):].lstrip("/")
            if ".." in rel or rel.startswith("/"):
                self.send_error(403)
                return
            file_path = STATIC_DIR / rel
            if file_path.is_file():
                data = file_path.read_bytes()
                ctype = "application/octet-stream"
                if rel.lower().endswith(".dll"):
                    ctype = "application/x-msdownload"
                elif rel.lower().endswith(".html"):
                    ctype = "text/html; charset=utf-8"
                elif rel.lower().endswith(".css"):
                    ctype = "text/css; charset=utf-8"
                elif rel.lower().endswith(".js"):
                    ctype = "application/javascript; charset=utf-8"
                self._send_bytes(data, ctype)
                return
            self.send_error(404)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"

        from extensions.routes import handle_post

        if handle_post(self, path):
            return

        if path == "/api/box/register":
            ok, msg = RATE_LIMITER.allow(f"box_reg:{self._client_ip()}", 10, 3600)
            if not ok:
                self._send_json(429, {"ok": False, "message": msg})
                return
            try:
                payload = self._read_json_body()
                user = SERVER.box_auth.register(
                    str(payload.get("username", "")),
                    str(payload.get("password", "")),
                    str(payload.get("display_name", "")),
                )
                self._send_json(200, {"ok": True, "user": user})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/box/login":
            try:
                payload = self._read_json_body()
                result = SERVER.box_auth.login(
                    str(payload.get("username", "")),
                    str(payload.get("password", "")),
                )
                self._send_json(200 if result.get("ok") else 401, result)
            except ValueError as e:
                self._send_json(401, {"ok": False, "message": str(e)})
            return

        if path == "/api/box/logout":
            SERVER.box_auth.logout(self._get_token())
            self._send_json(200, {"ok": True})
            return

        if path == "/api/box/vip/activate":
            user = self._require_box_user()
            if not user:
                return
            try:
                payload = self._read_json_body()
                updated = SERVER.box_auth.activate_vip(
                    str(user["id"]),
                    str(payload.get("code", "")),
                    self._load_box_config(),
                )
                self._send_json(200, {"ok": True, "user": updated})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/box/vip/cdk-success":
            user = self._require_box_user()
            if not user:
                return
            try:
                payload = self._read_json_body()
                cfg = self._load_box_config()
                days = int(payload.get("days") or cfg.get("Box_Vip_Days_Per_CDK", 30))
                updated = SERVER.box_auth.grant_vip_days(str(user["id"]), days)
                self._send_json(200, {"ok": True, "user": updated})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/login":
            try:
                payload = self._read_json_body()
            except Exception:
                self._send_json(400, {"ok": False, "message": "JSON 格式错误"})
                return
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))
            result = SERVER.admin.login(username, password, client_key=self._client_ip())
            if result.get("ok"):
                uid = str(result.get("user", {}).get("id", ""))
                from extensions.security import is_2fa_enabled, verify_2fa

                if uid and is_2fa_enabled(uid):
                    totp = str(payload.get("totp", "")).strip()
                    tok = str(result.get("token", ""))
                    if not totp:
                        if tok:
                            SERVER.admin.logout(tok)
                        self._send_json(401, {"ok": False, "message": "需要 2FA 验证码", "require_2fa": True})
                        return
                    if not verify_2fa(uid, totp):
                        if tok:
                            SERVER.admin.logout(tok)
                        self._send_json(401, {"ok": False, "message": "2FA 验证码错误"})
                        return
                result.update(self._commands_payload())
            code = 200 if result.get("ok") else 401
            cookie = str(result.get("token", "")) if result.get("ok") else ""
            self._send_json(code, result, auth_cookie=cookie)
            return

        if path == "/api/admin/logout":
            SERVER.admin.logout(self._get_token())
            self._send_json(200, {"ok": True}, clear_auth_cookie=True)
            return

        if path == "/api/admin/users":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                item = SERVER.admin.create_user(
                    username=str(payload.get("username", "")),
                    password=str(payload.get("password", "")),
                    role=str(payload.get("role", "agent")),
                    display_name=str(payload.get("display_name", "")),
                    cdk_quota=int(payload.get("cdk_quota", 100)),
                    cdk_cost_price=float(payload.get("cdk_cost_price", SERVER.admin.base_cdk_price())),
                    note=str(payload.get("note", "")),
                    operator_id=user.id,
                )
                self._send_json(200, {"ok": True, "user": item})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/sub-agents":
            user = self._require_auth()
            if not user:
                return
            if user.role not in ("superadmin", "agent"):
                self._send_json(403, {"ok": False, "message": "无权邀请代理"})
                return
            try:
                payload = self._read_json_body()
                item = SERVER.admin.create_sub_agent(
                    operator_id=user.id,
                    username=str(payload.get("username", "")),
                    password=str(payload.get("password", "")),
                    cdk_cost_price=float(payload.get("cdk_cost_price", 0)),
                    cdk_quota=int(payload.get("cdk_quota", 100)),
                    display_name=str(payload.get("display_name", "")),
                    note=str(payload.get("note", "")),
                )
                self._send_json(200, {"ok": True, "user": item})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/settings":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                settings = SERVER.admin.update_settings(**payload)
                self._send_json(200, {"ok": True, "settings": settings})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            except Exception as e:
                logging.exception("update settings failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/github-token":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                if isinstance(payload.get("tokens"), list):
                    status = run_async(SERVER.update_github_tokens(payload.get("tokens") or []))
                elif "tokens_text" in payload:
                    parsed = parse_github_tokens_input(str(payload.get("tokens_text") or ""))
                    status = run_async(SERVER.update_github_tokens(parsed))
                else:
                    status = run_async(SERVER.update_github_token(str(payload.get("token", ""))))
                msg = f"已保存 {status.get('count', 0)} 个 GitHub Token" if status.get("configured") else "GitHub Token 已清除"
                self._send_json(200, {"ok": True, **status, "message": msg})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            except Exception as e:
                logging.exception("update github token failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/catalog/sync":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                from extensions.sync_progress import start_background_catalog_sync

                result = start_background_catalog_sync(SERVER, source="manual")
                code = 200 if result.get("ok") else 409
                self._send_json(code, result)
            except Exception as e:
                logging.exception("catalog sync failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/catalog/settings":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                settings = SERVER.update_catalog_sync_settings(
                    auto_sync_enabled=payload.get("auto_sync_enabled"),
                    auto_sync_hours=payload.get("auto_sync_hours"),
                    merge_on_sync=payload.get("merge_on_sync"),
                )
                self._send_json(200, {"ok": True, "settings": settings, "message": "清单同步设置已保存"})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            except Exception as e:
                logging.exception("update catalog settings failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path.startswith("/api/admin/users/") and path.endswith("/recharge"):
            user = self._require_auth()
            if not user:
                return
            if user.role not in ("superadmin", "agent"):
                self._send_json(403, {"ok": False, "message": "无权充值"})
                return
            uid = path.rstrip("/").split("/")[-2]
            try:
                payload = self._read_json_body()
                item = SERVER.admin.recharge_user(
                    uid,
                    user.id,
                    balance=float(payload.get("balance", 0) or 0),
                    quota=int(payload.get("quota", 0) or 0),
                    note=str(payload.get("note", "")),
                )
                enriched = SERVER.enrich_user_public(uid)
                result = {"ok": True, "user": enriched or item}
                if user.role == "agent":
                    result["operator"] = SERVER.enrich_user_public(user.id)
                self._send_json(200, result)
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/cdk/generate":
            user = self._require_auth()
            if not user:
                return
            try:
                payload = self._read_json_body()
            except Exception:
                self._send_json(400, {"ok": False, "message": "JSON 格式错误"})
                return
            appid = str(payload.get("appid", "")).strip()
            name = str(payload.get("name", "")).strip()
            count = max(1, min(int(payload.get("count", 1)), 100))
            note = str(payload.get("note", "")).strip()
            billing_mode = str(payload.get("billing_mode", "immediate")).strip()
            expire_days = int(payload.get("expire_days", 0) or 0)
            if not appid.isdigit():
                self._send_json(400, {"ok": False, "message": "AppID 无效"})
                return
            ok, msg = RATE_LIMITER.allow(f"gen:{user.id}", 60, 60)
            if not ok:
                self._send_json(429, {"ok": False, "message": msg})
                return
            result = run_async(
                SERVER.generate_cdks(
                    appid,
                    name,
                    count,
                    note,
                    user=user,
                    billing_mode=billing_mode,
                    expire_days=expire_days,
                )
            )
            if result.get("ok"):
                result.update(self._commands_payload())
                if result.get("cdks"):
                    result["cdk_cmd"] = f'$cdk="{result["cdks"][0]}"; irm {self._irm_cmd_base()} | iex'
            code = 200 if result.get("ok") else 400
            self._send_json(code, result)
            return

        if path == "/api/admin/cdk/delete":
            user = self._require_auth()
            if not user:
                return
            try:
                payload = self._read_json_body()
                cdk = str(payload.get("cdk", "")).strip()
                code = SERVER.cdk.normalize_cdk(cdk)
                raw = SERVER.cdk._data.get("keys", {}).get(code)
                if user.role == "agent":
                    if not raw or str(raw.get("agent_id", "")) != user.id:
                        self._send_json(403, {"ok": False, "message": "无权删除此 CDK"})
                        return
                snapshot = dict(raw) if raw else None
                billing = {"balance_refund": 0.0, "quota_refund": 0, "quota_refunded": False}
                refunded = False
                if snapshot and snapshot.get("charged") and snapshot.get("agent_id"):
                    billing = SERVER.admin.refund_agent_cdk_charge(
                        str(snapshot["agent_id"]),
                        1,
                        user.id,
                        f"删除未用 CDK · {snapshot.get('appid', '')}",
                    )
                    billing["quota_refunded"] = billing.get("quota_refund", 0) > 0
                    refunded = True
                ok, _ = SERVER.cdk.delete_key(cdk)
                if not ok:
                    if refunded and snapshot and snapshot.get("agent_id"):
                        try:
                            SERVER.admin.charge_agent_for_cdks(str(snapshot["agent_id"]), 1)
                        except Exception:
                            logging.exception("delete rollback charge failed")
                    self._send_json(404, {"ok": False, "message": "CDK 不存在"})
                    return
                if user.role == "agent":
                    billing["user"] = SERVER.enrich_user_public(user.id)
                self._send_json(200, {"ok": True, **billing})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/cdk/recycle":
            user = self._require_auth()
            if not user:
                return
            try:
                payload = self._read_json_body()
                cdk = str(payload.get("cdk", "")).strip()
                note = str(payload.get("note", "")).strip()
                result = SERVER.recycle_cdk(cdk, user=user, note=note)
                if result.get("ok"):
                    cmd = self._irm_cmd_base()
                    hint = result.get("client_revoke_hint", "").replace("{base}", cmd)
                    result["client_revoke_cmd"] = f'$cdk="{result.get("cdk", "")}"; irm {cmd}/revoke.ps1 | iex'
                    result["client_revoke_hint"] = hint
                    if result.get("user"):
                        pass
                    elif user.role == "agent":
                        result["user"] = SERVER.enrich_user_public(user.id)
                code = 200 if result.get("ok") else 400
                self._send_json(code, result)
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/password":
            user = self._require_auth()
            if not user:
                return
            try:
                payload = self._read_json_body()
                old = str(payload.get("old_password", ""))
                new = str(payload.get("new_password", ""))
                raw = SERVER.admin.find_by_username(user.username)
                if not raw or SERVER.admin._hash_password(old, str(raw.get("salt"))) != raw.get("password_hash"):
                    self._send_json(400, {"ok": False, "message": "原密码错误"})
                    return
                SERVER.admin.update_user(user.id, password=new)
                self._send_json(200, {"ok": True})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/public/revoked/check":
            try:
                payload = self._read_json_body()
            except Exception:
                self._send_json(400, {"ok": False, "message": "JSON 格式错误"})
                return
            raw_cdks = payload.get("cdks") if isinstance(payload.get("cdks"), list) else []
            cdks = [str(x) for x in raw_cdks[:200]]
            revoked = SERVER.cdk.check_revoked_cdks(cdks)
            self._send_json(200, {"ok": True, "revoked": revoked, "count": len(revoked)})
            return

        if path in ("/api/redeem", "/redeem"):
            try:
                payload = self._read_json_body()
            except Exception:
                self._send_json(400, {"ok": False, "message": "JSON 格式错误"})
                return
            cdk = str(payload.get("cdk", "")).strip()
            machine = str(payload.get("machine", "")).strip()
            if not cdk:
                self._send_json(400, {"ok": False, "message": "缺少 cdk 参数"})
                return
            settings = SERVER.admin._data.get("settings", {})
            limit = int(settings.get("redeem_rate_limit", 30))
            ok, msg = RATE_LIMITER.allow(f"redeem:{self._client_ip()}", limit, 60)
            if not ok:
                self._send_json(429, {"ok": False, "message": msg})
                return
            try:
                result = run_async(SERVER.redeem(cdk, machine))
            except Exception as e:
                logging.exception("redeem failed")
                self._send_json(500, {"ok": False, "message": str(e)})
                return
            code = 200 if result.get("ok") else 400
            self._send_json(code, result)
            return

        if path == "/api/public/recharge/apply":
            ok, msg = RATE_LIMITER.allow(f"recharge_pub:{self._client_ip()}", 5, 3600)
            if not ok:
                self._send_json(429, {"ok": False, "message": msg})
                return
            try:
                payload = self._read_json_body()
                username = str(payload.get("username", "")).strip()
                if not username:
                    self._send_json(400, {"ok": False, "message": "请填写代理账号"})
                    return
                ok2, msg2 = RATE_LIMITER.allow(f"recharge_user:{username.lower()}", 3, 3600)
                if not ok2:
                    self._send_json(429, {"ok": False, "message": msg2})
                    return
                req = SERVER.admin.apply_recharge_request(
                    username=username,
                    req_type=str(payload.get("type", "balance")).strip(),
                    amount=payload.get("amount", 0),
                    note=str(payload.get("note", "")),
                    proof=str(payload.get("proof", "")),
                )
                try:
                    from extensions.notification import notify_recharge_pending
                    notify_recharge_pending(username, float(payload.get("amount") or 0))
                except Exception:
                    pass
                self._send_json(200, {
                    "ok": True,
                    "message": "充值申请已提交，请等待管理员审核到账",
                    "request": req,
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            except Exception as e:
                logging.exception("recharge apply failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/recharge/apply":
            user = self._require_auth()
            if not user:
                return
            if user.role != "agent":
                self._send_json(403, {"ok": False, "message": "仅代理可提交充值申请"})
                return
            ok, msg = RATE_LIMITER.allow(f"recharge_apply:{user.id}", 10, 3600)
            if not ok:
                self._send_json(429, {"ok": False, "message": msg})
                return
            try:
                payload = self._read_json_body()
                req = SERVER.admin.apply_recharge_request(
                    username=user.username,
                    req_type=str(payload.get("type", "balance")).strip(),
                    amount=payload.get("amount", 0),
                    note=str(payload.get("note", "")),
                    proof=str(payload.get("proof", "")),
                )
                try:
                    from extensions.notification import notify_recharge_pending
                    notify_recharge_pending(user.username, float(payload.get("amount") or 0))
                except Exception:
                    pass
                self._send_json(200, {
                    "ok": True,
                    "message": "充值申请已提交，请等待管理员审核到账",
                    "request": req,
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/recharge/review":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                req = SERVER.admin.review_recharge_request(
                    request_id=str(payload.get("request_id", "")).strip(),
                    operator_id=user.id,
                    action=str(payload.get("action", "")).strip(),
                    review_note=str(payload.get("note", "")),
                )
                self._send_json(200, {
                    "ok": True,
                    "message": "已通过审核并到账" if req.get("status") == "approved" else "已拒绝该申请",
                    "request": req,
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            except Exception as e:
                logging.exception("recharge review failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/recharge/batch-review":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                ids = payload.get("request_ids") or []
                result = SERVER.admin.batch_review_recharge_requests(
                    [str(x) for x in ids],
                    user.id,
                    str(payload.get("action", "")).strip(),
                    str(payload.get("note", "")),
                )
                self._send_json(200, {"ok": True, **result})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/withdraw/apply":
            user = self._require_auth()
            if not user:
                return
            if user.role != "agent":
                self._send_json(403, {"ok": False, "message": "仅代理可申请提现"})
                return
            ok, msg = RATE_LIMITER.allow(f"withdraw:{user.id}", 10, 3600)
            if not ok:
                self._send_json(429, {"ok": False, "message": msg})
                return
            try:
                payload = self._read_json_body()
                req = SERVER.admin.apply_withdraw_request(
                    user.id,
                    float(payload.get("amount", 0)),
                    payout_info=str(payload.get("payout_info", "")),
                    note=str(payload.get("note", "")),
                )
                try:
                    from extensions.notification import notify_withdraw_pending
                    notify_withdraw_pending(user.username, float(payload.get("amount") or 0))
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "request": req})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/withdraw/review":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                req = SERVER.admin.review_withdraw_request(
                    str(payload.get("request_id", "")).strip(),
                    user.id,
                    str(payload.get("action", "")).strip(),
                    str(payload.get("note", "")),
                )
                self._send_json(200, {"ok": True, "request": req})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/withdraw/batch-review":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                result = SERVER.admin.batch_review_withdraw_requests(
                    [str(x) for x in (payload.get("request_ids") or [])],
                    user.id,
                    str(payload.get("action", "")).strip(),
                    str(payload.get("note", "")),
                )
                self._send_json(200, {"ok": True, **result})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path.startswith("/api/admin/agents/") and path.endswith("/invite/reset"):
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            agent_id = path.rstrip("/").split("/")[-3]
            try:
                info = SERVER.admin.admin_reset_agent_invite(agent_id, user.id)
                base = self._api_base_for_request().rstrip("/")
                self._send_json(200, {
                    "ok": True,
                    "invite_code": info.get("invite_code"),
                    "register_url": f"{base}/register?code={info.get('invite_code')}",
                    "user": info.get("user"),
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/public/register":
            try:
                payload = self._read_json_body()
                settings = SERVER.admin._data.get("settings", {})
                per_hour = int(settings.get("register_per_ip_hour", 5))
                ok, msg = RATE_LIMITER.allow(f"register:{self._client_ip()}", per_hour, 3600)
                if not ok:
                    self._send_json(429, {"ok": False, "message": msg})
                    return
                invite_code = str(payload.get("invite_code") or payload.get("code") or "").strip()
                user = SERVER.admin.register_by_invite(
                    username=str(payload.get("username", "")).strip(),
                    password=str(payload.get("password", "")),
                    invite_code=invite_code,
                    display_name=str(payload.get("display_name", "")).strip(),
                )
                self._send_json(200, {
                    "ok": True,
                    "message": "注册成功，请登录",
                    "user": user,
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            except Exception as e:
                logging.exception("register failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/invite/refresh":
            user = self._require_auth()
            if not user:
                return
            if user.role != "agent":
                self._send_json(403, {"ok": False, "message": "仅代理可刷新邀请码"})
                return
            try:
                code = SERVER.admin.refresh_invite_code(user.id)
                base = self._api_base_for_request().rstrip("/")
                info = SERVER.admin.get_agent_invite_info(user.id)
                self._send_json(200, {
                    "ok": True,
                    "invite_code": code,
                    "register_url": f"{base}/register?code={code}",
                    **info,
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        if path == "/api/admin/invite/settings":
            user = self._require_auth()
            if not user:
                return
            if user.role != "agent":
                self._send_json(403, {"ok": False, "message": "仅代理可设置邀请成本价"})
                return
            try:
                payload = self._read_json_body()
                info = SERVER.admin.set_invite_cost_price(
                    user.id,
                    float(payload.get("cdk_cost_price", 0)),
                )
                base = self._api_base_for_request().rstrip("/")
                self._send_json(200, {
                    "ok": True,
                    "register_url": f"{base}/register?code={info['invite_code']}",
                    **info,
                })
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return

        self.send_error(404)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/admin/settings":
            user = self._require_auth(superadmin_only=True)
            if not user:
                return
            try:
                payload = self._read_json_body()
                settings = SERVER.admin.update_settings(**payload)
                self._send_json(200, {"ok": True, "settings": settings})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            except Exception as e:
                logging.exception("update settings failed")
                self._send_json(500, {"ok": False, "message": str(e)})
            return
        if path.startswith("/api/admin/users/"):
            user = self._require_auth()
            if not user:
                return
            if user.role not in ("superadmin", "agent"):
                self._send_json(403, {"ok": False, "message": "无权操作"})
                return
            uid = path.split("/")[-1]
            try:
                payload = self._read_json_body()
                item = SERVER.admin.update_user(uid, operator_id=user.id, **payload)
                self._send_json(200, {"ok": True, "user": item})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return
        self.send_error(404)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/admin/users/"):
            operator = self._require_auth()
            if not operator:
                return
            if operator.role not in ("superadmin", "agent"):
                self._send_json(403, {"ok": False, "message": "无权操作"})
                return
            uid = path.split("/")[-1]
            try:
                SERVER.admin.delete_user(uid, operator.id)
                self._send_json(200, {"ok": True})
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Web CDK 管理台 + 远程激活服务")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--public-url", default="", help="对外 URL，如 http://公网IP:8787")
    args = parser.parse_args()

    cfg: Dict[str, Any] = {}
    config_path = ROOT / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    server_cfg = cfg.get("Server") or {}
    host = args.host.strip() or str(server_cfg.get("host") or "0.0.0.0")
    port = args.port or int(server_cfg.get("port") or 8787)
    public_url = (
        args.public_url.strip()
        or str(server_cfg.get("public_url") or "").strip()
        or str(cfg.get("Box_Server_URL") or "").strip()
        or f"http://127.0.0.1:{port}"
    )

    public_url, cmd_base = normalize_public_url(public_url, port)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    WebHandler.public_url = public_url
    WebHandler.listen_port = port

    try:
        httpd = ReusableHTTPServer((host, port), WebHandler)
    except OSError as e:
        if getattr(e, "errno", None) == 98:
            print(f"\n错误: 端口 {port} 已被占用。请先执行: bash stop_web.sh\n")
        raise SystemExit(1) from e
    display = WebHandler.public_url

    print("")
    print("=" * 52)
    print("  Web CDK 管理台已启动")
    print("=" * 52)
    print(f"  代理登录:  {display}/login")
    print(f"  代理注册:  {display}/register?code=邀请码")
    print(f"  代理前台:  {display}/portal")
    print(f"  后台登录:  {display}/admin/login")
    print(f"  管理后台:  {display}/admin")
    print(f"  用户激活:  irm {cmd_base} | iex")
    print(f"  安装 Hook: irm {cmd_base}/hook | iex")
    print(f"  盒子账号:  配置 Box_Server_URL={display} 后 GUI 与线上共用登录/VIP")
    if is_database_enabled():
        print(f"  数据存储:  {get_database_label()}，用户/代理/CDK 不写 JSON")
    else:
        print("  数据存储:  本地 JSON（可在 config.json 启用 Database.enabled）")
    print("  注意: 请勿多进程同时写数据库（当前为单进程）")
    print("=" * 52)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
