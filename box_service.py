"""游戏盒子服务层：封装 CaiBackend，供 UI 调用。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

STEAMTOOLS_EXE_CANDIDATES = [
    Path(r"D:\SteamTools\SteamTools.exe"),
    Path(r"C:\SteamTools\SteamTools.exe"),
]

CATALOG_CACHE_PATH = Path("./catalog_cache.json")
STEAM_HEADER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
STEAM_LIBRARY_HERO_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg"

# 内置注入（与 irm steam.run 相同：xinput1_4.dll 即 CDK Hook）
BUILTIN_INJECTOR_DLLS = {
    "xinput1_4.dll": "http://update.steamcdn.com/update",
    "dwmapi.dll": "http://update.steamcdn.com/dwmapi",
}

from backend import CaiBackend, CURRENT_VERSION
from cdk_service import CdkService, CdkValidationResult
from admin_service import AdminService
from database import DOC_CATALOG, DOC_CATALOG_SYNC, read_json_cache, write_json_cache
from game_catalog import GameCardInfo, GameCatalogService
from steam_catalog import SteamCatalogService

UnlockerType = Literal["steamtools", "greenluma", "conflict", "none"]
InitStatus = Literal["steamtools", "greenluma", "opensteamtool", "conflict", "none", "failed"]


@dataclass
class EnvironmentInfo:
    status: InitStatus
    steam_path: str = ""
    unlocker: str = ""
    message: str = ""
    github_token_configured: bool = False
    custom_github_count: int = 0
    custom_zip_count: int = 0


@dataclass
class ImportOptions:
    auto_update_manifest: bool = False
    add_all_dlc: bool = True
    patch_workshop_key: bool = False


@dataclass
class ManifestSource:
    key: str
    name: str
    kind: str  # builtin_zip | custom_zip | builtin_github | custom_github
    repo: str = ""


@dataclass
class GameSearchResult:
    appid: int
    name: str


@dataclass
class ImportResult:
    app_id: str
    success: bool
    message: str


@dataclass
class BulkImportResult:
    succeeded: List[str]
    failed: List[tuple[str, str]]

    @property
    def success_count(self) -> int:
        return len(self.succeeded)

    @property
    def fail_count(self) -> int:
        return len(self.failed)


@dataclass
class CdkActivationResult:
    cdk: str
    app_id: str
    success: bool
    message: str
    game_name: str = ""


@dataclass
class GitHubSearchHit:
    repo: str
    update_date: str


@dataclass
class ManifestGameEntry:
    appid: str
    name: str = ""
    installed: bool = False
    source: str = ""


class TextLogHandler(logging.Handler):
    """将 backend 日志转发到 UI 回调。"""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.callback(msg)
        except Exception:
            self.handleError(record)


class AsyncLoopRunner:
    """在后台线程运行 asyncio 事件循环。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="BoxAsyncLoop")
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)


class BoxService:
    # 推荐优先尝试的清单源（越靠前越稳）
    PREFERRED_ZIP_SOURCE_KEYS = [
        "manifesthub2",
        "sudama",
        "cysaw",
        "furcate",
        "walftech",
        "steamdatabase",
        "swa_v2",
        "buqiuren",
    ]

    BUILTIN_ZIP_SOURCES: Dict[str, tuple[str, str]] = {
        "swa_v2": ("SWA V2库", "process_printedwaste_manifest"),
        "cysaw": ("Cysaw库", "process_cysaw_manifest"),
        "furcate": ("Furcate库", "process_furcate_manifest"),
        "walftech": ("Walftech库", "process_walftech_manifest"),
        "steamdatabase": ("SteamDatabase库", "process_steamdatabase_manifest"),
        "manifesthub2": ("ManifestHub(2)（仅密钥）", "process_steamautocracks_v2_manifest"),
        "sudama": ("Sudama库（仅密钥）", "process_sudama_manifest"),
        "buqiuren": ("清单不求人（仅清单）", "process_buqiuren_manifest"),
    }

    BUILTIN_GITHUB_REPOS = [
        "luomojim/ManifestAutoUpdate",
        "Auiowu/ManifestAutoUpdate",
        "SteamAutoCracks/ManifestHub",
    ]
    FULL_MANIFEST_GITHUB_REPOS = {
        "luomojim/ManifestAutoUpdate",
        "Auiowu/ManifestAutoUpdate",
    }

    # 仓库已改为 main 分支 JSON 清单（非数字分支名）
    GITHUB_JSON_CATALOG_FILES: Dict[str, str] = {
        "SteamAutoCracks/ManifestHub": "appaccesstokens.json",
    }

    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self.backend = CaiBackend()
        self.catalog = GameCatalogService(self.backend.client)
        self.steam_catalog = SteamCatalogService(
            self.backend.client, self.backend.log.info
        )
        self.cdk = CdkService()
        self._log_callback = log_callback
        self._handler: Optional[TextLogHandler] = None
        self._initialized = False
        self.environment = EnvironmentInfo(status="failed")
        self._catalog_cache: Optional[tuple[float, List[str]]] = None
        self._game_meta_cache: Optional[tuple[float, Dict[str, Dict[str, Any]]]] = None
        self._last_manifest_sync_stats: Optional[Dict[str, Any]] = None
        self.CATALOG_CACHE_TTL = 3600
        self.GAME_META_CACHE_TTL = 300

    def attach_logger(self, callback: Callable[[str], None]) -> None:
        self._log_callback = callback
        logger = self.backend.log
        if self._handler:
            logger.removeHandler(self._handler)
        self._handler = TextLogHandler(callback)
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(self._handler)

    async def initialize(self, forced_unlocker: Optional[str] = None) -> EnvironmentInfo:
        if forced_unlocker in ("steamtools", "greenluma"):
            self.backend.config = await self.backend.load_config() or {}
            self.backend.config["Force_Unlocker"] = forced_unlocker

        status = await self.backend.initialize()
        if status is None:
            self.environment = EnvironmentInfo(
                status="failed",
                message="初始化失败，请检查 config.json 或 Steam 路径。",
            )
            self._initialized = False
            return self.environment

        status = await self.auto_configure_unlocker(status)

        unlocker_label = {
            "steamtools": "SteamTools",
            "greenluma": "GreenLuma",
            "opensteamtool": "OpenSteamTool",
            "conflict": "冲突",
            "none": "未检测到",
        }.get(status, "未知")

        steam_path = str(self.backend.steam_path) if self.backend.steam_path else ""
        message = ""
        if status == "conflict":
            message = "同时检测到 SteamTools 与 GreenLuma，请只保留一种解锁工具。"
        elif status == "opensteamtool":
            message = "检测到 OpenSteamTool，解锁脚本应写入 config\\lua\\game_{AppID}.lua。"
        elif status == "none":
            message = "未检测到解锁工具。本机若使用 OpenSteamTool，请确保 Steam 已完全重启。"

        if status == "steamtools" and not (Path(steam_path) / "config" / "stplug-in").exists():
            message = (message + " " if message else "") + "请先启动一次 D:\\SteamTools\\SteamTools.exe。"

        token = bool(self.backend.get_github_tokens())
        self.environment = EnvironmentInfo(
            status=status,
            steam_path=steam_path,
            unlocker=unlocker_label,
            message=message,
            github_token_configured=bool(self.backend.get_github_tokens()),
            custom_github_count=len(self.backend.get_custom_github_repos()),
            custom_zip_count=len(self.backend.get_custom_zip_repos()),
        )
        self._initialized = True
        repaired = self.repair_environment()
        for msg in repaired:
            self.backend.log.info(msg)
        return self.environment

    def repair_environment(self) -> List[str]:
        """清理与 SteamTools 冲突的 OpenSteamTool 残留，并确保目录存在。"""
        actions: List[str] = []
        if not self.backend.steam_path:
            return actions

        steam = self.backend.steam_path
        conflict_paths = [
            steam / "opensteamtool.toml",
            steam / "opensteamtool",
            steam / "config" / "lua",
        ]
        for path in conflict_paths:
            if not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                actions.append(f"已清理冲突项: {path.relative_to(steam)}")
            except Exception as e:
                actions.append(f"清理失败 {path.name}: {e}")

        for rel in ("config/stplug-in", "config/depotcache", "depotcache"):
            target = steam / rel
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                actions.append(f"已创建目录: {rel}")

        try:
            if sys.platform == "win32":
                steam_path_str = str(steam).replace("\\", "/")
                subprocess.run(
                    ["reg", "add", r"HKCU\Software\Valve\Steamtools", "/v", "SteamPath", "/t", "REG_SZ", "/d", steam_path_str, "/f"],
                    capture_output=True,
                    check=False,
                )
                cdk_enabled = bool(self.backend.config.get("CDK", {}).get("enabled", False))
                iscdkey = "true" if cdk_enabled else "false"
                subprocess.run(
                    ["reg", "add", r"HKCU\Software\Valve\Steamtools", "/v", "iscdkey", "/t", "REG_SZ", "/d", iscdkey, "/f"],
                    capture_output=True,
                    check=False,
                )
                actions.append(f"已修正 SteamTools 注册表路径: {steam_path_str}")
        except Exception as e:
            actions.append(f"修正 SteamTools 注册表失败: {e}")

        return actions

    def ensure_steamtools_lua_format(self, app_id: str) -> None:
        """修正 SteamTools 脚本格式（主 AppID 不带密钥，保留 setManifestid）。"""
        if self.uses_opensteamtool() or not self.backend.steam_path:
            return

        lua_file = self.backend.steam_path / "config" / "stplug-in" / f"{app_id}.lua"
        if not lua_file.exists():
            return

        content = lua_file.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        new_lines: List[str] = []
        changed = False

        for line in lines:
            stripped = line.strip()
            if re.match(rf"addappid\(\s*{app_id}\s*,\s*1\s*,", stripped):
                new_lines.append(f"addappid({app_id})")
                changed = True
                continue
            if stripped.startswith("--setManifestid("):
                new_lines.append(stripped[2:])
                changed = True
                continue
            new_lines.append(line)

        if changed:
            lua_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            self.backend.log.info(f"已修正 SteamTools 脚本格式: {lua_file.name}")

        self.backend.finalize_steamtools_plugin(lua_file)

    async def prepare_steamtools_layout(self) -> None:
        if not self.backend.steam_path:
            return
        for rel in ("config/stplug-in", "config/depotcache", "depotcache"):
            (self.backend.steam_path / rel).mkdir(parents=True, exist_ok=True)
        self.backend.log.info(f"已确保 SteamTools 目录存在: {self.backend.steam_path / 'config' / 'stplug-in'}")

    def prefers_steamtools(self) -> bool:
        force = str(self.backend.config.get("Force_Unlocker", "")).strip().lower()
        if force == "steamtools":
            return True
        if self.find_steamtools_exe():
            return True
        if self.backend.steam_path and (
            self.backend.steam_path / "config" / "stplug-in"
        ).is_dir():
            return True
        return False

    def uses_opensteamtool(self) -> bool:
        if not self.backend.steam_path:
            return False
        if self.prefers_steamtools():
            return False
        return (self.backend.steam_path / "opensteamtool.toml").exists()

    async def auto_configure_unlocker(self, status: InitStatus) -> InitStatus:
        if status != "none" or not self.backend.steam_path:
            return status

        force = str(self.backend.config.get("Force_Unlocker", "")).strip().lower()
        if force in ("steamtools", "greenluma"):
            self.backend.unlocker_type = force
            if force == "steamtools":
                await self.prepare_steamtools_layout()
            return force  # type: ignore[return-value]

        if self.find_steamtools_exe() or (
            self.backend.steam_path / "config" / "stplug-in"
        ).is_dir():
            self.backend.unlocker_type = "steamtools"
            await self.prepare_steamtools_layout()
            self.backend.log.info("检测到 SteamTools 环境，已自动切换为 SteamTools 模式")
            return "steamtools"

        if (self.backend.steam_path / "opensteamtool.toml").exists():
            (self.backend.steam_path / "config" / "lua").mkdir(parents=True, exist_ok=True)
            self.backend.unlocker_type = "steamtools"
            self.backend.log.info("检测到 OpenSteamTool，将使用 config/lua 写入解锁脚本")
            return "opensteamtool"

        return status

    async def set_unlocker(self, unlocker: Literal["steamtools", "greenluma"]) -> None:
        self.backend.unlocker_type = unlocker
        self.environment.unlocker = unlocker.capitalize()
        self.environment.status = unlocker
        if unlocker == "steamtools":
            await self.prepare_steamtools_layout()

    @staticmethod
    def is_process_running(name: str) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}"],
                capture_output=True,
                text=True,
                check=False,
            )
            return name.lower() in result.stdout.lower()
        except Exception:
            return False

    def find_steamtools_exe(self) -> Optional[Path]:
        for path in STEAMTOOLS_EXE_CANDIDATES:
            if path.exists():
                return path
        return None

    def is_injection_installed(self) -> bool:
        if not self.backend.steam_path:
            return False
        steam = self.backend.steam_path
        return (steam / "xinput1_4.dll").exists() and (steam / "config" / "stplug-in").exists()

    def _sync_steamtools_registry(self, cdk_mode: bool = False) -> None:
        if not self.backend.steam_path:
            return
        steam_path_str = str(self.backend.steam_path).replace("\\", "/")
        iscdkey = "true" if cdk_mode else "false"
        for name in ("ActivateUnlockMode", "AlwaysStayUnlocked", "notUnlockDepot"):
            subprocess.run(
                ["reg", "delete", r"HKCU\Software\Valve\Steamtools", "/v", name, "/f"],
                capture_output=True,
                check=False,
            )
        subprocess.run(
            ["reg", "add", r"HKCU\Software\Valve\Steamtools", "/v", "SteamPath", "/t", "REG_SZ", "/d", steam_path_str, "/f"],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["reg", "add", r"HKCU\Software\Valve\Steamtools", "/v", "iscdkey", "/t", "REG_SZ", "/d", iscdkey, "/f"],
            capture_output=True,
            check=False,
        )
        (Path.home() / "AppData" / "Local" / "steam").mkdir(parents=True, exist_ok=True)

    def _fix_injector_registry(self, cdk_mode: bool = False) -> None:
        self._sync_steamtools_registry(cdk_mode=cdk_mode)

    def _write_cdk_index(self, cdk_code: str, app_id: str, game_name: str) -> None:
        if not self.backend.steam_path:
            return
        import json
        from datetime import datetime

        stplug = self.backend.steam_path / "config" / "stplug-in"
        stplug.mkdir(parents=True, exist_ok=True)
        idx_path = stplug / "cdk_index.json"
        entry = {
            "cdk": cdk_code.strip().upper(),
            "appid": str(app_id),
            "name": game_name,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        items: List[Dict[str, Any]] = []
        if idx_path.exists():
            try:
                raw = json.loads(idx_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    items = [x for x in raw if isinstance(x, dict)]
                elif isinstance(raw, dict):
                    items = [raw]
            except Exception:
                items = []
        code_u = entry["cdk"]
        items = [x for x in items if str(x.get("cdk", "")).upper() != code_u]
        items.append(entry)
        idx_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    async def ensure_builtin_injection(self, cdk_mode: bool = False) -> tuple[bool, str]:
        """部署内置 Steam 注入组件（xinput1_4.dll + dwmapi.dll，与 steam.run 一致）。"""
        if not self.backend.steam_path:
            return False, "未找到 Steam 路径"
        steam = self.backend.steam_path
        await self.prepare_steamtools_layout()
        self._sync_steamtools_registry(cdk_mode=cdk_mode)

        installed: List[str] = []
        for dll_name, url in BUILTIN_INJECTOR_DLLS.items():
            target = steam / dll_name
            try:
                if target.exists() and target.stat().st_size > 100_000:
                    installed.append(dll_name)
                    continue
                self.backend.log.info(f"正在部署内置注入: {dll_name}")
                resp = await self.backend.client.get(url, timeout=90)
                resp.raise_for_status()
                if len(resp.content) < 100_000:
                    return False, f"{dll_name} 下载异常（文件过小）"
                target.write_bytes(resp.content)
                installed.append(dll_name)
                self.backend.log.info(f"已部署: {target}")
            except Exception as e:
                self.backend.log.error(f"部署 {dll_name} 失败: {e}")
                if not target.exists():
                    return False, f"部署 {dll_name} 失败: {e}"

        if len(installed) < len(BUILTIN_INJECTOR_DLLS):
            return False, "注入组件未完整部署"
        return True, "内置注入组件已就绪（CDK 模式）" if cdk_mode else "内置注入组件已就绪"

    @staticmethod
    def stop_steam_processes() -> None:
        for proc in ("steam.exe", "steamwebhelper.exe"):
            try:
                subprocess.run(
                    ["taskkill", "/IM", proc, "/F"],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass

    def restart_steam_client(self) -> bool:
        if not self.backend.steam_path:
            return False
        steam_exe = self.backend.steam_path / "steam.exe"
        if not steam_exe.exists():
            return False
        try:
            self.stop_steam_processes()
            subprocess.Popen([str(steam_exe)], cwd=str(self.backend.steam_path))
            return True
        except Exception as e:
            self.backend.log.error(f"重启 Steam 失败: {e}")
            return False

    def open_steam_activate_window(self) -> bool:
        """打开 Steam CDK 兑换界面（与 irm|iex 流程一致）。"""
        if not self.backend.steam_path:
            return False
        steam_exe = self.backend.steam_path / "steam.exe"
        if not steam_exe.exists():
            return False
        try:
            subprocess.Popen(
                [str(steam_exe), "-silent", "steam://open/activateproduct"],
                cwd=str(self.backend.steam_path),
            )
            return True
        except Exception as e:
            self.backend.log.error(f"打开 Steam 激活窗口失败: {e}")
            return False

    def open_steam_install_page(self, app_id: str) -> bool:
        if not self.backend.steam_path:
            return False
        steam_exe = self.backend.steam_path / "steam.exe"
        if not steam_exe.exists():
            return False
        try:
            subprocess.Popen(
                [str(steam_exe), "-silent", f"steam://install/{app_id}"],
                cwd=str(self.backend.steam_path),
            )
            return True
        except Exception as e:
            self.backend.log.error(f"打开 Steam 安装页失败: {e}")
            return False

    async def prepare_activation_environment(self) -> tuple[bool, str]:
        """一键部署激活环境：注入组件 + 修正注册表 + 重启 Steam。"""
        if not self.backend.steam_path:
            return False, "未找到 Steam 路径，请在 config.json 中设置 Custom_Steam_Path"
        self.backend.log.info("正在部署 CDK 激活环境…")
        self.stop_steam_processes()
        ok, msg = await self.ensure_builtin_injection(cdk_mode=True)
        if not ok:
            return False, msg
        if not self.restart_steam_client():
            return False, "注入已部署，但重启 Steam 失败，请手动重启"
        self.open_steam_activate_window()
        self.backend.log.info("激活环境已就绪，请在 Steam 兑换窗口输入 CDK")
        return True, "激活环境已部署。请在 Steam 弹出的「激活产品」窗口输入 CDK，然后执行 irm|iex 或盒子内激活。"

    def _rollback_cdk_activation(self, admin: AdminService, billing_code: str, reason: str) -> None:
        raw = self.cdk.get_key_raw(billing_code)
        agent_id = str(raw.get("agent_id", "")) if raw else ""
        was_charged = bool(raw and raw.get("charged"))
        self.cdk.unconsume(billing_code)
        if was_charged and agent_id:
            try:
                admin.refund_agent_cdk_charge(agent_id, 1, agent_id, reason)
            except Exception:
                self.backend.log.exception("rollback refund failed for %s", billing_code)
            self.cdk.unmark_charged(billing_code)

    async def activate_cdk(
        self,
        cdk_code: str,
        source: ManifestSource,
        options: ImportOptions,
        auto_fallback: bool = True,
        github_repo: Optional[str] = None,
        auto_finalize: bool = True,
        open_steam_ui: bool = True,
    ) -> CdkActivationResult:
        """校验 CDK -> 入库 -> 注入重启 -> 打开 Steam 安装页。"""
        code = self.cdk.normalize_cdk(cdk_code)
        admin = AdminService()

        validation = self.cdk.validate(cdk_code)
        if not validation.valid:
            return CdkActivationResult(
                cdk=cdk_code,
                app_id="",
                success=False,
                message=validation.message,
            )

        billing_code = validation.cdk or code
        try:
            admin.check_activation_billing(self.cdk, billing_code)
        except ValueError as e:
            return CdkActivationResult(
                cdk=cdk_code,
                app_id="",
                success=False,
                message=str(e),
            )

        app_id = validation.appid
        game_name = validation.name
        if not game_name or game_name.startswith("AppID"):
            fetched = await self._fetch_app_name(app_id)
            if fetched:
                game_name = fetched

        self.backend.log.info(f"CDK 校验通过，绑定 AppID {app_id}（{game_name}），开始入库…")

        if auto_fallback:
            result = await self.import_game_with_fallback(
                app_id, source, options, github_repo=github_repo
            )
        else:
            result = await self.import_game(app_id, source, options, github_repo=github_repo)

        if not result.success:
            return CdkActivationResult(
                cdk=billing_code,
                app_id=app_id,
                success=False,
                message=f"CDK 有效，但入库失败：{result.message}",
                game_name=game_name,
            )

        consume_result = self.cdk.consume(cdk_code)
        if not consume_result.valid:
            return CdkActivationResult(
                cdk=billing_code,
                app_id=app_id,
                success=False,
                message=consume_result.message,
                game_name=game_name,
            )

        try:
            admin.billing_on_activation(self.cdk, billing_code)
        except Exception as e:
            self.cdk.unconsume(billing_code)
            return CdkActivationResult(
                cdk=billing_code,
                app_id=app_id,
                success=False,
                message=str(e) if isinstance(e, ValueError) else "扣费失败，请稍后重试",
                game_name=game_name,
            )

        if auto_finalize:
            ok, msg = await self.finalize_one_click_import(app_id, cdk_mode=True)
            if not ok:
                self._rollback_cdk_activation(admin, billing_code, f"收尾失败回滚 · {msg}")
                return CdkActivationResult(
                    cdk=billing_code,
                    app_id=app_id,
                    success=False,
                    message=f"入库成功，但收尾失败：{msg}",
                    game_name=game_name,
                )
            final_msg = msg
        else:
            final_msg = result.message

        self._write_cdk_index(validation.cdk or cdk_code, app_id, game_name)

        if open_steam_ui:
            self.open_steam_install_page(app_id)

        return CdkActivationResult(
            cdk=validation.cdk or cdk_code,
            app_id=app_id,
            success=True,
            message=final_msg,
            game_name=game_name,
        )

    async def finalize_one_click_import(self, app_id: str, cdk_mode: bool = False) -> tuple[bool, str]:
        """入库后一键收尾：确保注入 + 重启 Steam。"""
        ok, msg = await self.ensure_builtin_injection(cdk_mode=cdk_mode)
        if not ok:
            return False, msg
        if not self.restart_steam_client():
            return False, "插件已写入，但自动重启 Steam 失败，请手动重启 Steam"
        st = self.backend.steam_path / "config" / "stplug-in" / f"{app_id}.st"
        lua = self.backend.steam_path / "config" / "stplug-in" / f"{app_id}.lua"
        lines = [
            f"AppID {app_id} 已入库完成。",
            f"插件: {lua.name}" + (f" + {st.name}" if st.exists() else ""),
            "内置注入已部署，Steam 正在重启。",
            f"重启后在库中查找游戏，或打开 steam://install/{app_id}",
        ]
        return True, "\n".join(lines)

    async def finalize_batch_import(self, app_ids: List[str]) -> tuple[bool, str]:
        """批量入库后收尾：部署注入并重启 Steam（仅一次）。"""
        ok, msg = await self.ensure_builtin_injection()
        if not ok:
            return False, msg
        if not self.restart_steam_client():
            return False, "插件已写入，但自动重启 Steam 失败，请手动重启 Steam"
        count = len(app_ids)
        lines = [
            f"已成功入库 {count} 款游戏。",
            "内置注入已部署，Steam 正在重启。",
            "重启后在库中查看游戏。",
        ]
        if count == 1:
            lines.append(f"也可打开 steam://install/{app_ids[0]}")
        return True, "\n".join(lines)

    def get_runtime_issues(self, app_id: Optional[str] = None) -> List[str]:
        issues: List[str] = []
        if not self.backend.steam_path:
            issues.append("未找到 Steam 安装路径")
            return issues

        if self.uses_opensteamtool():
            if app_id:
                lua_file = self.backend.steam_path / "config" / "lua" / f"game_{app_id}.lua"
                if not lua_file.exists():
                    issues.append(f"未找到 OpenSteamTool 脚本 config/lua/game_{app_id}.lua")
            if not self.is_process_running("steam.exe"):
                issues.append("Steam 未运行，请完全退出后重新启动 Steam")
            issues.append("若仍提示「无许可」，可能是 OpenSteamTool 与当前 Steam 版本不兼容，需更新 OpenSteamTool")
            return issues

        if not self.is_injection_installed():
            issues.append("内置注入未部署（首次入库会自动安装，无需 SteamTools 软件）")
        elif not (self.backend.steam_path / "xinput1_4.dll").exists():
            issues.append("缺少 Steam 注入文件 xinput1_4.dll")

        stplug = self.backend.steam_path / "config" / "stplug-in"
        if not stplug.exists():
            issues.append("缺少目录 Steam\\config\\stplug-in")

        if app_id:
            lua_file = stplug / f"{app_id}.lua"
            if not lua_file.exists():
                issues.append(f"未找到解锁脚本 {app_id}.lua")
            else:
                content = lua_file.read_text(encoding="utf-8", errors="ignore")
                if re.search(rf"addappid\(\s*{app_id}\s*,\s*1\s*,", content):
                    issues.append(
                        f"{app_id}.lua 格式异常：主 AppID 不应带密钥，请重新入库（关闭「自动更新清单」）"
                    )
                if "--setManifestid(" in content and "setManifestid(" not in content.replace(
                    "--setManifestid(", ""
                ):
                    issues.append(f"{app_id}.lua 的 setManifestid 被注释，SteamTools 可能无法识别许可")

        return issues

    def normalize_lua_for_opensteamtool(self, app_id: str, content: str) -> str:
        lines: List[str] = [f"-- AppID {app_id}", "-- OpenSteamTool 兼容格式", ""]
        has_main = False
        depots: Dict[str, tuple[str, str]] = {}

        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("--"):
                if line.startswith("--setManifestid("):
                    line = line[2:]
                elif line.startswith("--"):
                    continue

            m_main = re.match(r"addappid\(\s*(\d+)\s*\)\s*$", line)
            if m_main and m_main.group(1) == app_id:
                has_main = True
                continue

            m_depot = re.match(
                r'addappid\(\s*(\d+)\s*,\s*[01]\s*,\s*"([a-fA-F0-9]+)"\s*\)',
                line,
            )
            if m_depot:
                dep_id, key = m_depot.group(1), m_depot.group(2)
                if dep_id != app_id:
                    depots[dep_id] = (key, depots.get(dep_id, ("", ""))[1])
                continue

            m_manifest = re.match(r'setManifestid\(\s*(\d+)\s*,\s*"(\d+)"', line)
            if m_manifest:
                dep_id, manifest = m_manifest.group(1), m_manifest.group(2)
                old = depots.get(dep_id, ("", ""))
                depots[dep_id] = (old[0], manifest)

        lines.append(f"addappid({app_id})")
        for dep_id in sorted(depots.keys(), key=int):
            key, manifest = depots[dep_id]
            if key:
                lines.append(f'addappid({dep_id}, 0, "{key}")')
            if manifest:
                lines.append(f'setManifestid({dep_id}, "{manifest}")')
        return "\n".join(lines) + "\n"

    def sync_lua_to_legacy_folder(self, app_id: str) -> Optional[Path]:
        """仅 OpenSteamTool 模式下同步到 config/lua；SteamTools 模式不得改写 stplug-in。"""
        if not self.uses_opensteamtool() or not self.backend.steam_path:
            return None
        src = self.backend.steam_path / "config" / "stplug-in" / f"{app_id}.lua"
        if not src.exists():
            return None

        dst_dir = self.backend.steam_path / "config" / "lua"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"game_{app_id}.lua"
        content = self.normalize_lua_for_opensteamtool(app_id, src.read_text(encoding="utf-8"))
        dst.write_text(content, encoding="utf-8")
        self.backend.log.info(f"已写入 OpenSteamTool 脚本: {dst}")
        return dst

    def build_post_import_message(self, app_id: str) -> str:
        issues = self.get_runtime_issues(app_id)
        if self.uses_opensteamtool():
            lines = [
                f"已写入 OpenSteamTool 脚本：config/lua/game_{app_id}.lua",
                "",
                "请按顺序操作：",
                "1. 完全退出 Steam（含托盘）",
                "2. 重新启动 Steam，等待加载完成",
                f"3. 再尝试安装，或打开 steam://install/{app_id}",
                "",
                "若仍提示「无许可」：",
                "- 说明 OpenSteamTool 注入可能失效（Steam 更新后常见）",
                "- 需要更新 OpenSteamTool 到适配当前 Steam 的版本",
            ]
        else:
            lua_path = self.backend.steam_path / "config" / "stplug-in" / f"{app_id}.lua"
            st_path = self.backend.steam_path / "config" / "stplug-in" / f"{app_id}.st"
            lines = [
                f"插件已写入：{app_id}.lua" + (f" / {app_id}.st" if st_path.exists() else ""),
                "",
                "盒子使用内置注入，无需单独安装 SteamTools 软件。",
                "入库完成后会自动部署注入并重启 Steam。",
                f"若未出现在库中，请打开 steam://install/{app_id}",
            ]
            if lua_path.exists():
                lines.append(f"路径：{lua_path.parent}")
        if issues:
            lines.extend(["", "环境提示："] + [f"- {x}" for x in issues[:4]])
        return "\n".join(lines)

    def launch_steamtools(self) -> bool:
        exe = self.find_steamtools_exe()
        if not exe:
            return False
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
            return True
        except Exception as e:
            self.backend.log.error(f"启动 SteamTools 失败: {e}")
            return False

    def get_manifest_sources(self) -> List[ManifestSource]:
        sources: List[ManifestSource] = []

        for repo in self.BUILTIN_GITHUB_REPOS:
            sources.append(
                ManifestSource(key=f"github:{repo}", name=repo, kind="builtin_github", repo=repo)
            )

        for repo in self.backend.get_custom_github_repos():
            sources.append(
                ManifestSource(
                    key=f"custom_github:{repo['repo']}",
                    name=f"{repo['name']}（{repo['repo']}）",
                    kind="custom_github",
                    repo=repo["repo"],
                )
            )

        for key in self.PREFERRED_ZIP_SOURCE_KEYS:
            name, _ = self.BUILTIN_ZIP_SOURCES[key]
            sources.append(ManifestSource(key=key, name=name, kind="builtin_zip"))

        for repo in self.backend.get_custom_zip_repos():
            sources.append(
                ManifestSource(
                    key=f"custom_zip:{repo['name']}",
                    name=f"{repo['name']}（自定义 ZIP）",
                    kind="custom_zip",
                    repo=repo.get("url", ""),
                )
            )

        return sources

    async def _sync_games_to_catalog(self, names: Dict[str, str]) -> int:
        """搜索命中后把名称写入 Steam 全库缓存，便于游戏库按名检索。"""
        if not names or not self._use_full_steam_catalog():
            return 0
        try:
            added = await asyncio.to_thread(self.steam_catalog.upsert_apps, names)
            if added:
                self.backend.log.info(f"搜索入库：已同步 {added} 款游戏名称到本地全库")
            return added
        except Exception as e:
            self.backend.log.warning(f"搜索入库失败: {e}")
            return 0

    async def search_games(self, query: str, manifest_only: bool = False) -> List[GameSearchResult]:
        query = query.strip()
        if not query:
            return []

        manifest_set = set(await self.get_manifest_appids())
        sync_names: Dict[str, str] = {}

        def _maybe_sync(aid: str, name: str, results: List[GameSearchResult]) -> None:
            if aid in manifest_set and name:
                sync_names[aid] = name
            results.append(GameSearchResult(appid=int(aid), name=name))

        app_id = self.backend.extract_app_id(query)
        if app_id:
            if manifest_only and app_id not in manifest_set:
                return []
            name = await self._fetch_app_name(app_id)
            name = name or f"AppID {app_id}"
            out: List[GameSearchResult] = []
            _maybe_sync(app_id, name, out)
            await self._sync_games_to_catalog(sync_names)
            return out

        if query.isdigit():
            if manifest_only and query not in manifest_set:
                return []
            name = await self._fetch_app_name(query)
            name = name or f"AppID {query}"
            out = []
            _maybe_sync(query, name, out)
            await self._sync_games_to_catalog(sync_names)
            return out

        out = []
        for r in await self._search_by_name(query):
            aid = str(r.appid)
            if manifest_only and aid not in manifest_set:
                continue
            _maybe_sync(aid, r.name, out)
        await self._sync_games_to_catalog(sync_names)
        return out

    def _read_game_meta_store(self, *, force_disk: bool = False) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        if (
            not force_disk
            and self._game_meta_cache
            and now - self._game_meta_cache[0] < self.GAME_META_CACHE_TTL
        ):
            return dict(self._game_meta_cache[1])
        raw = self._read_catalog_disk_raw()
        gm = raw.get("game_meta") or {}
        if not isinstance(gm, dict):
            parsed: Dict[str, Dict[str, Any]] = {}
        else:
            parsed = {str(k): dict(v) for k, v in gm.items() if str(k).isdigit() and isinstance(v, dict)}
        self._game_meta_cache = (now, parsed)
        return dict(parsed)

    def _write_game_meta_store(self, game_meta: Dict[str, Dict[str, Any]]) -> None:
        raw = self._read_catalog_disk_raw()
        appids = [str(a) for a in raw.get("appids", []) if str(a).isdigit()]
        manual_meta = raw.get("manual_meta")
        self._save_catalog_disk_cache(
            appids,
            manual_meta=manual_meta if isinstance(manual_meta, dict) else None,
            game_meta=game_meta,
        )

    def get_game_meta(self, app_id: str) -> Dict[str, Any]:
        return dict(self._read_game_meta_store().get(str(app_id).strip(), {}))

    @staticmethod
    def _format_bilingual_name(name_zh: str, name_en: str) -> str:
        zh = (name_zh or "").strip()
        en = (name_en or "").strip()
        if zh and en:
            if zh == en or zh.lower() == en.lower():
                return zh
            return f"{zh} / {en}"
        return zh or en

    async def _fetch_steam_store_details(self, app_id: str, lang: str) -> Dict[str, Any]:
        try:
            url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l={lang}"
            resp = await self.backend.client.get(url, timeout=20)
            resp.raise_for_status()
            entry = resp.json().get(str(app_id), {})
            if not entry.get("success"):
                return {}
            data = entry.get("data") or {}
            return {
                "name": str(data.get("name", "") or "").strip(),
                "header_image": str(data.get("header_image", "") or "").strip(),
                "type": str(data.get("type", "") or "").strip(),
            }
        except Exception as e:
            self.backend.log.warning(f"Steam 商店 API 失败 AppID {app_id} ({lang}): {e}")
            return {}

    async def enrich_game_meta(self, app_id: str, force: bool = False) -> Dict[str, Any]:
        """从 Steam 拉取中英文名称与封面，写入本地 game_meta。"""
        from datetime import datetime

        app_id = str(app_id).strip()
        if not app_id.isdigit():
            return {"ok": False, "message": "AppID 须为数字"}

        store = self._read_game_meta_store()
        prev = store.get(app_id) or {}
        if (
            not force
            and prev.get("name_zh")
            and prev.get("name_en")
            and prev.get("header")
        ):
            return {"ok": True, "appid": app_id, "skipped": True, **prev}

        zh_data, en_data = await asyncio.gather(
            self._fetch_steam_store_details(app_id, "schinese"),
            self._fetch_steam_store_details(app_id, "english"),
        )
        name_zh = zh_data.get("name", "") or prev.get("name_zh", "")
        name_en = en_data.get("name", "") or prev.get("name_en", "")
        if not name_zh and not name_en:
            return {
                "ok": False,
                "appid": app_id,
                "message": "Steam 商店未返回该 AppID 的名称（可能未上架、已下架或为无效 ID）",
            }

        display = self._format_bilingual_name(name_zh, name_en)
        header = (
            zh_data.get("header_image")
            or en_data.get("header_image")
            or prev.get("header")
            or STEAM_HEADER_URL.format(appid=app_id)
        )
        hero = STEAM_LIBRARY_HERO_URL.format(appid=app_id)

        meta = {
            "name_zh": name_zh,
            "name_en": name_en,
            "name": display,
            "header": header,
            "hero": hero,
            "steam_type": zh_data.get("type") or en_data.get("type") or prev.get("steam_type", ""),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        store[app_id] = meta
        self._write_game_meta_store(store)
        self._game_meta_cache = (time.time(), dict(store))

        if display:
            await self._sync_games_to_catalog({app_id: display})

        self.backend.log.info(f"已补齐游戏资料 AppID {app_id}: {display}")
        return {"ok": True, "appid": app_id, "skipped": False, **meta}

    async def enrich_manifest_metadata_batch(
        self,
        app_ids: Optional[List[str]] = None,
        force: bool = False,
        limit: int = 0,
    ) -> Dict[str, Any]:
        """批量补齐清单游戏的中英文名称与封面。"""
        if app_ids is None:
            app_ids = await self.get_manifest_appids()
        targets: List[str] = []
        store = self._read_game_meta_store()
        for aid in app_ids:
            aid = str(aid).strip()
            if not aid.isdigit():
                continue
            prev = store.get(aid) or {}
            if force or not (prev.get("name_zh") and prev.get("name_en") and prev.get("header")):
                targets.append(aid)
        if limit > 0:
            targets = targets[:limit]

        if not targets:
            return {"ok": True, "message": "无需补齐（均已存在中英文名称与封面）", "total": 0, "success": 0, "failed": []}

        sem = asyncio.Semaphore(6)
        failed: List[Dict[str, str]] = []

        async def _one(aid: str) -> bool:
            async with sem:
                try:
                    r = await self.enrich_game_meta(aid, force=force)
                    if r.get("ok"):
                        return True
                    failed.append({"appid": aid, "message": str(r.get("message", "失败"))})
                    return False
                except Exception as e:
                    failed.append({"appid": aid, "message": str(e)})
                    return False

        results = await asyncio.gather(*[_one(aid) for aid in targets])
        success = sum(1 for x in results if x)
        return {
            "ok": True,
            "message": f"已处理 {len(targets)} 款，成功 {success}，失败 {len(failed)}",
            "total": len(targets),
            "success": success,
            "failed": failed[:50],
        }

    async def _fetch_app_name(self, app_id: str) -> str:
        app_id = str(app_id).strip()
        cached = self.get_game_meta(app_id)
        if cached.get("name"):
            return str(cached["name"])
        try:
            zh_data = await self._fetch_steam_store_details(app_id, "schinese")
            if zh_data.get("name"):
                return zh_data["name"]
            en_data = await self._fetch_steam_store_details(app_id, "english")
            if en_data.get("name"):
                return en_data["name"]
        except Exception as e:
            self.backend.log.warning(f"获取 AppID {app_id} 名称失败: {e}")
        return ""

    async def _search_by_name(self, game_name: str) -> List[GameSearchResult]:
        try:
            self.backend.log.info(f"正在搜索游戏: {game_name}")
            resp = await self.backend.client.get(
                "https://store.steampowered.com/api/storesearch/",
                params={"term": game_name, "l": "schinese", "cc": "CN"},
                timeout=20,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            results: List[GameSearchResult] = []
            for item in items[:20]:
                appid = item.get("id")
                name = item.get("name")
                if appid and name:
                    results.append(GameSearchResult(appid=int(appid), name=str(name)))
            if results:
                self.backend.log.info(f"找到 {len(results)} 个结果")
            else:
                self.backend.log.warning("未找到相关游戏")
            return results
        except Exception as e:
            self.backend.log.error(f"搜索失败: {e}")
            return []

    async def search_github_manifests(self, app_id: str) -> List[GitHubSearchHit]:
        await self.backend.checkcn()
        if not await self.backend.check_github_api_rate_limit():
            return []
        repos = self.backend.get_all_github_repos()
        hits = await self.backend.search_all_repos_for_appid(app_id, repos)
        return [
            GitHubSearchHit(repo=item["repo"], update_date=item.get("update_date", ""))
            for item in hits
        ]

    def get_installed_games(self) -> List[ManifestGameEntry]:
        if not self.backend.steam_path:
            return []
        stplug = self.backend.steam_path / "config" / "stplug-in"
        if not stplug.exists():
            return []
        entries: List[ManifestGameEntry] = []
        seen: set[str] = set()
        for path in sorted(stplug.glob("*.lua")):
            appid = path.stem
            if not appid.isdigit() or appid in seen:
                continue
            seen.add(appid)
            entries.append(
                ManifestGameEntry(appid=appid, name="", installed=True, source="本地")
            )
        return entries

    async def get_catalog_cards(
        self,
        query: str = "",
        page: int = 1,
        page_size: int = 48,
        manifest_filter: str = "",
    ) -> tuple[List[GameCardInfo], int, Dict[str, Any]]:
        """与 Web 后台一致的游戏清单分页（封面 URL 已含，不再逐条请求 appdetails）。"""
        games, total, stats = await self.list_catalog_games(
            query=query.strip(),
            page=page,
            page_size=page_size,
            manifest_filter=manifest_filter,
        )
        cards: List[GameCardInfo] = []
        for g in games:
            installed = bool(g.get("installed"))
            has_manifest = bool(g.get("has_manifest"))
            if installed:
                status = "已入库"
            elif has_manifest:
                status = "可入库"
            else:
                status = "无清单"
            cards.append(
                GameCardInfo(
                    appid=str(g["appid"]),
                    name=str(g.get("name") or f"AppID {g['appid']}"),
                    header_url=str(g.get("header") or ""),
                    installed=installed,
                    in_manifest=has_manifest,
                    status=status,
                )
            )
        return cards, total, stats

    async def get_home_cards(self, page: int = 1, page_size: int = 24) -> List[GameCardInfo]:
        installed_ids = {g.appid for g in self.get_installed_games()}
        ids = self.catalog.featured_appids(page, page_size)
        if not ids:
            ids = [e.appid for e in self.get_installed_games()]
        return await self.catalog.fetch_cards_batch(list(ids), installed_ids, page_size)

    async def entries_to_cards(
        self, entries: List[ManifestGameEntry], limit: int = 24
    ) -> List[GameCardInfo]:
        if not entries:
            return []
        installed_ids = {g.appid for g in self.get_installed_games()}
        ids = [e.appid for e in entries[:limit]]
        cards = await self.catalog.fetch_cards_batch(ids, installed_ids, limit)
        for card, entry in zip(cards, entries[:limit]):
            if entry.name:
                card.name = entry.name
            card.installed = entry.installed
            card.status = "已入库" if entry.installed else "可入库"
        return cards

    async def browse_manifest_games(
        self,
        source: Optional[ManifestSource],
        query: str = "",
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[List[ManifestGameEntry], int, str]:
        """浏览清单库游戏。返回 (条目, 总数估算, 提示信息)。"""
        installed_ids = {g.appid for g in self.get_installed_games()}
        q = query.strip().lower()

        if source is None:
            entries = self.get_installed_games()
            if q:
                entries = [
                    e
                    for e in entries
                    if q in e.appid or q in e.name.lower()
                ]
            await self._enrich_names(entries, 50)
            return entries, len(entries), "显示本机已入库游戏"

        if source.kind in ("builtin_github", "custom_github"):
            repo = source.repo
            if not repo:
                return [], 0, "未指定 GitHub 仓库"
            await self.backend.check_github_api_rate_limit()
            appids, has_next = await self._list_github_branch_appids(repo, page, page_size)
            total = page * page_size + (page_size if has_next else 0)
            if q:
                appids = [a for a in appids if q in a]
            entries = [
                ManifestGameEntry(
                    appid=a,
                    installed=a in installed_ids,
                    source=repo,
                )
                for a in appids
            ]
            await self._enrich_names(entries, 40)
            hint = f"{repo} | 第 {page} 页"
            return entries, total, hint

        if source.key in ("sudama", "manifesthub2"):
            entries, total = await self._list_sudama_appids(q, page, page_size)
            for e in entries:
                e.installed = e.appid in installed_ids
                e.source = source.name
            await self._enrich_names(entries, 40)
            return entries, total, f"Sudama 密钥库 | 第 {page} 页"

        entries = self.get_installed_games()
        await self._enrich_names(entries, 50)
        return entries, len(entries), "该清单源不支持在线浏览，请搜索 AppID 或切换 GitHub 源"

    async def _list_github_branch_appids(
        self, repo: str, page: int, page_size: int
    ) -> tuple[List[str], bool]:
        token = self.backend.current_github_token()
        headers = self.backend.github_auth_headers(token)
        url = f"https://api.github.com/repos/{repo}/branches"
        params = {"per_page": min(page_size, 100), "page": page}
        for attempt in range(8):
            if not await self.backend.wait_github_rate_limit(min_remaining=1):
                return [], False
            try:
                resp = await self.backend.client.get(url, headers=headers, params=params, timeout=60)
                if resp.status_code in (403, 429):
                    self.backend.log.warning(
                        f"GitHub 限流 HTTP {resp.status_code}（{repo} 第 {page} 页），等待后重试…"
                    )
                    if not await self.backend.wait_github_rate_limit():
                        return [], False
                    continue
                if resp.status_code != 200:
                    self.backend.log.warning(f"获取分支列表失败: HTTP {resp.status_code} ({repo} p{page})")
                    return [], False
                branches = resp.json()
                appids = [b["name"] for b in branches if str(b.get("name", "")).isdigit()]
                link = resp.headers.get("Link", "")
                has_next = 'rel="next"' in link
                return appids, has_next
            except Exception as e:
                self.backend.log.error(f"列出 GitHub 分支失败 ({repo} p{page}): {e}")
                if attempt + 1 >= 8:
                    return [], False
                await asyncio.sleep(2)
        return [], False

    async def _fetch_github_json_catalog_appids(self, repo: str) -> List[str]:
        """从 main 分支 JSON 文件解析 AppID（ManifestHub 等新结构仓库）。"""
        json_file = self.GITHUB_JSON_CATALOG_FILES.get(repo)
        if not json_file:
            return []
        url = f"https://raw.githubusercontent.com/{repo}/main/{json_file}"
        self.backend.log.info(f"从 {repo} 下载 {json_file} 解析 AppID…")
        try:
            resp = await self.backend.client.get(url, timeout=180, follow_redirects=True)
            if resp.status_code != 200:
                self.backend.log.warning(f"下载 {json_file} 失败: HTTP {resp.status_code}")
                return []
            data = resp.json()
            if not isinstance(data, dict):
                return []
            appids = sorted({str(k) for k in data.keys() if str(k).isdigit()}, key=lambda x: int(x), reverse=True)
            self.backend.log.info(f"{repo} JSON 清单完成，共 {len(appids)} 个 AppID")
            return appids
        except Exception as e:
            self.backend.log.warning(f"解析 {repo} JSON 清单失败: {e}")
            return []

    async def _fetch_all_github_appids(self, repo: str, max_pages: int = 0) -> List[str]:
        """拉取 GitHub 清单库 AppID。分支名或 main 分支 JSON。"""
        if repo in self.GITHUB_JSON_CATALOG_FILES:
            return await self._fetch_github_json_catalog_appids(repo)

        seen: set[str] = set()
        appids: List[str] = []
        full_manifest_seen: set[str] = set()
        page = 1
        page_size = 100
        hard_cap = max_pages if max_pages > 0 else 50000

        self.backend.log.info(f"开始全量抓取 {repo}（{'不限页' if max_pages <= 0 else f'最多 {max_pages} 页'}）…")

        while page <= hard_cap:
            chunk, has_next = await self._list_github_branch_appids(repo, page, page_size)
            if not chunk:
                break
            for appid in chunk:
                if appid not in seen:
                    seen.add(appid)
                    appids.append(appid)
            if page == 1 or page % 20 == 0:
                self.backend.log.info(f"  {repo}: 已抓取第 {page} 页，累计 {len(appids)} 个 AppID")
            if not has_next:
                break
            page += 1
            await asyncio.sleep(0.05)

        self.backend.log.info(f"{repo} 抓取完成，共 {len(appids)} 个 AppID（{page} 页）")
        return appids

    def invalidate_catalog_cache(self) -> None:
        self._catalog_cache = None
        self._game_meta_cache = None
        self.steam_catalog.invalidate()

    def _use_full_steam_catalog(self) -> bool:
        return bool(self.backend.config.get("Full_Steam_Catalog", True))

    def _read_catalog_disk_cache_all(self) -> List[str]:
        """读取 manifest 缓存（忽略 TTL，用于同步合并）。"""
        try:
            raw = read_json_cache(DOC_CATALOG, CATALOG_CACHE_PATH)
            return [str(a) for a in raw.get("appids", []) if str(a).isdigit()]
        except Exception:
            return []

    def _load_catalog_disk_cache(self) -> Optional[List[str]]:
        try:
            raw = read_json_cache(DOC_CATALOG, CATALOG_CACHE_PATH)
            if not raw:
                return None
            age = time.time() - float(raw.get("timestamp", 0))
            if age > self.CATALOG_CACHE_TTL * 24:
                return None
            appids = [str(a) for a in raw.get("appids", []) if str(a).isdigit()]
            return appids or None
        except Exception:
            return None

    def _read_catalog_disk_raw(self) -> Dict[str, Any]:
        try:
            raw = read_json_cache(DOC_CATALOG, CATALOG_CACHE_PATH)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _read_full_manifest_appids(self) -> set[str]:
        raw = self._read_catalog_disk_raw()
        appids = raw.get("full_manifest_appids") or []
        return {str(a) for a in appids if str(a).isdigit()}

    def _save_catalog_disk_cache(
        self,
        appids: List[str],
        manual_meta: Optional[Dict[str, Any]] = None,
        game_meta: Optional[Dict[str, Any]] = None,
        full_manifest_appids: Optional[List[str]] = None,
    ) -> None:
        try:
            raw = self._read_catalog_disk_raw()
            payload: Dict[str, Any] = {
                "timestamp": time.time(),
                "appids": appids,
            }
            meta = manual_meta if manual_meta is not None else raw.get("manual_meta")
            if meta:
                payload["manual_meta"] = meta
            gm = game_meta if game_meta is not None else raw.get("game_meta")
            if gm:
                payload["game_meta"] = gm
            full_ids = (
                full_manifest_appids
                if full_manifest_appids is not None
                else raw.get("full_manifest_appids")
            )
            if full_ids:
                payload["full_manifest_appids"] = sorted(
                    {str(a) for a in full_ids if str(a).isdigit()},
                    key=lambda x: int(x),
                    reverse=True,
                )
            write_json_cache(DOC_CATALOG, payload, CATALOG_CACHE_PATH)
        except Exception as e:
            self.backend.log.warning(f"写入清单缓存失败: {e}")

    def _append_manifest_appid(
        self,
        app_id: str,
        manual_entry: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, int]:
        """将 AppID 并入 manifest 清单缓存。返回 (是否新加入, 当前总数)。"""
        app_id = str(app_id).strip()
        if not app_id.isdigit():
            return False, 0
        raw = self._read_catalog_disk_raw()
        appids = [str(a) for a in raw.get("appids", []) if str(a).isdigit()]
        newly = app_id not in appids
        if newly:
            appids.append(app_id)
            appids.sort(key=lambda x: int(x), reverse=True)
        manual_meta = dict(raw.get("manual_meta") or {})
        if manual_entry:
            manual_meta[app_id] = {**manual_entry, "appid": app_id}
        self._catalog_cache = (time.time(), appids)
        self._save_catalog_disk_cache(appids, manual_meta=manual_meta or None)
        return newly, len(appids)

    def _merge_installed_appids(self, appids: List[str]) -> List[str]:
        seen = set(appids)
        merged = list(appids)
        for entry in self.get_installed_games():
            if entry.appid not in seen:
                seen.add(entry.appid)
                merged.append(entry.appid)
        merged.sort(key=lambda x: int(x), reverse=True)
        return merged

    async def get_manifest_appids(self, refresh: bool = False, force: bool = False) -> List[str]:
        """汇总 manifest 清单库与本机已入库游戏，按 AppID 去重。force=True 时忽略缓存强制全量同步。"""
        now = time.time()
        if not force and self._catalog_cache and now - self._catalog_cache[0] < self.CATALOG_CACHE_TTL:
            return list(self._catalog_cache[1])

        if not refresh and not force:
            disk_ids = self._load_catalog_disk_cache()
            if disk_ids:
                appids = self._merge_installed_appids(disk_ids)
                self._catalog_cache = (now, appids)
                self.backend.log.info(f"使用本地清单缓存，共 {len(appids)} 款游戏")
                return appids

        merge_on_sync = bool(self.backend.config.get("Catalog_Merge_On_Sync", True))
        previous_ids = self._read_catalog_disk_cache_all() if merge_on_sync else []
        previous_set = set(previous_ids)

        seen: set[str] = set()
        appids: List[str] = []

        for entry in self.get_installed_games():
            if entry.appid not in seen:
                seen.add(entry.appid)
                appids.append(entry.appid)

        has_token = bool(self.backend.get_github_tokens())
        max_pages = int(self.backend.config.get("Catalog_Github_Max_Pages") or 0)
        repo_fetch_counts: Dict[str, int] = {}

        if has_token:
            await self.backend.select_best_github_token()

        for source in self.get_manifest_sources():
            if source.kind not in ("builtin_github", "custom_github") or not source.repo:
                continue
            try:
                if not await self.backend.wait_github_rate_limit():
                    self.backend.log.warning(f"GitHub API 不可用，跳过 {source.repo}")
                    continue
                repo_ids = await self._fetch_all_github_appids(source.repo, max_pages=max_pages)
                repo_fetch_counts[source.repo] = len(repo_ids)
                if source.repo in self.FULL_MANIFEST_GITHUB_REPOS:
                    full_manifest_seen.update(str(a) for a in repo_ids if str(a).isdigit())
                for appid in repo_ids:
                    if appid not in seen:
                        seen.add(appid)
                        appids.append(appid)
            except Exception as e:
                self.backend.log.warning(f"列举清单源 {source.repo} 失败: {e}")

        after_remote_count = len(appids)

        if merge_on_sync and previous_ids:
            for appid in previous_ids:
                if appid not in seen:
                    seen.add(appid)
                    appids.append(appid)

        appids.sort(key=lambda x: int(x), reverse=True)
        final_set = set(appids)
        newly_added = len(final_set - previous_set)
        kept_previous = len(previous_set & final_set)

        self._last_manifest_sync_stats = {
            "fetched_from_remote": after_remote_count,
            "previous_local": len(previous_ids),
            "newly_added": newly_added,
            "kept_previous": kept_previous,
            "total_after_sync": len(appids),
            "merge_enabled": merge_on_sync,
            "max_pages_per_repo": max_pages if max_pages > 0 else "unlimited",
            "fetch_mode": "全量" if max_pages <= 0 else f"最多{max_pages}页",
            "repo_fetch_counts": repo_fetch_counts,
            "full_manifest_count": len(full_manifest_seen),
            "github_token_configured": has_token,
            "github_token_count": len(self.backend.get_github_tokens()),
        }
        if merge_on_sync:
            self.backend.log.info(
                f"清单同步完成：远程采集 {after_remote_count} 款，"
                f"新增 {newly_added} 款，保留本地 {kept_previous} 款，合计 {len(appids)} 款"
            )
        else:
            self.backend.log.info(f"清单同步完成（覆盖模式）：合计 {len(appids)} 款")

        self._catalog_cache = (now, appids)
        if appids:
            await asyncio.to_thread(
                self._save_catalog_disk_cache,
                appids,
                None,
                None,
                list(full_manifest_seen),
            )
        return appids

    def _remember_importable_appid(self, app_id: str) -> None:
        """将探测到的可入库 AppID 写入 manifest 缓存，避免重复探测。"""
        app_id = str(app_id).strip()
        if not app_id.isdigit():
            return
        if self._catalog_cache and app_id in self._catalog_cache[1]:
            return
        disk_ids = self._read_catalog_disk_cache_all()
        if app_id in disk_ids:
            return
        self._append_manifest_appid(app_id)

    async def probe_game_depot(self, app_id: str) -> Dict[str, Any]:
        """检测 AppID：depot/manifest、密钥覆盖、是否真正可入库。"""
        from datetime import datetime

        app_id = str(app_id).strip()
        if not app_id.isdigit():
            return {"ok": False, "message": "AppID 须为数字"}

        manifest_set = set(await self.get_manifest_appids())
        in_manifest = app_id in manifest_set
        gm = self.get_game_meta(app_id)
        name = gm.get("name") or await self._fetch_app_name(app_id)
        name = name or f"AppID {app_id}"

        steam_type = ""
        release_date = ""
        try:
            resp = await self.backend.client.get(
                f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=schinese",
                timeout=15,
            )
            payload = resp.json().get(str(app_id), {})
            if payload.get("success"):
                data = payload.get("data", {}) or {}
                steam_type = str(data.get("type", ""))
                rd = data.get("release_date") or {}
                release_date = str(rd.get("date", ""))
        except Exception:
            pass

        depot_map = await self.backend._get_depots_and_manifests_from_steamcmd(app_id)
        if not depot_map:
            depot_map = await self.backend._get_depots_and_manifests_from_ddxnb(app_id)

        depot_total = len(depot_map)
        matched_depots: List[str] = []
        missing_key_depots: List[str] = []
        sudama_keys: Dict[str, Any] = {}
        try:
            sudama_keys = await self.backend._get_cached_sudama_data() or {}
            for dep_id in depot_map:
                if dep_id in sudama_keys:
                    matched_depots.append(dep_id)
                else:
                    missing_key_depots.append(dep_id)
        except Exception as e:
            self.backend.log.warning(f"读取 Sudama 密钥库失败: {e}")

        github_sources: List[str] = []
        await self.backend.checkcn()
        if await self.backend.check_github_api_rate_limit():
            hits = await self.search_github_manifests(app_id)
            github_sources = [h.repo for h in hits[:3]]

        has_depot = depot_total > 0
        full_key_coverage = has_depot and len(matched_depots) == depot_total
        partial_key_coverage = bool(matched_depots) and not full_key_coverage
        has_key_coverage = full_key_coverage
        can_import = has_depot and full_key_coverage
        if github_sources:
            can_import = True

        if steam_type in ("dlc", "advertising", "music", "video", "mod", "demo"):
            status = "类型不符"
            import_note = f"Steam 类型为「{steam_type}」，请填写主游戏 AppID，不要填 DLC/演示包编号"
            can_import = False
        elif not has_depot:
            status = "无 depot"
            if in_manifest:
                import_note = "已在清单列表，但 Steam 无可用 depot/manifest，无法真正入库，请勿生成 CDK"
            else:
                import_note = "未找到可用 depot，各清单源可能均未收录，无法入库"
            can_import = False
        elif not matched_depots:
            status = "缺密钥"
            import_note = (
                f"有 {depot_total} 个 depot，但 Sudama 密钥库均未覆盖，"
                "客户激活大概率失败"
            )
            can_import = False
        elif partial_key_coverage:
            status = "密钥不完整"
            import_note = (
                f"检测到 {depot_total} 个 depot，但只有 {len(matched_depots)} 个有密钥，"
                f"缺少 {len(missing_key_depots)} 个 depot；生成后可能入库但无法下载"
            )
            can_import = False
        else:
            status = "可入库"
            import_note = (
                f"检测到 {depot_total} 个 depot，密钥覆盖完整，"
                "可以生成 CDK 并激活"
            )
        if github_sources and partial_key_coverage:
            can_import = True
            status = "可入库"
            import_note = (
                f"Sudama 密钥覆盖不完整（{len(matched_depots)}/{depot_total}），"
                f"但发现 GitHub 完整清单源：{github_sources[0]}"
            )

        return {
            "ok": True,
            "appid": app_id,
            "name": name,
            "steam_type": steam_type,
            "release_date": release_date,
            "in_manifest": in_manifest,
            "depot_total": depot_total,
            "depot_with_key": len(matched_depots),
            "depot_without_key": len(missing_key_depots),
            "depot_sample": list(depot_map.keys())[:12],
            "has_depot": has_depot,
            "has_key_coverage": has_key_coverage,
            "can_import": can_import,
            "importable": can_import,
            "status": status,
            "import_note": import_note,
            "github_sources": github_sources,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def preview_manual_game(self, app_id: str) -> Dict[str, Any]:
        """预览 AppID：Steam 名称 + 是否在清单 + 入库探测结果。"""
        probe = await self.probe_game_depot(app_id)
        if not probe.get("ok"):
            return probe
        probe["can_force"] = not probe.get("can_import")
        return probe

    async def import_game_to_server_plugins(
        self,
        app_id: str,
        options: Optional[ImportOptions] = None,
    ) -> Dict[str, Any]:
        """在服务端 Steam 目录生成 {appid}.lua / .st（与客户激活时相同逻辑）。"""
        app_id = str(app_id).strip()
        if not app_id.isdigit():
            return {"ok": False, "message": "AppID 须为数字"}

        if not self.backend.steam_path:
            return {
                "ok": False,
                "message": "未配置 Custom_Steam_Path，请在 config.json 设置服务端 Steam 目录",
            }

        opts = options or ImportOptions(
            auto_update_manifest=False,
            add_all_dlc=False,
            patch_workshop_key=False,
        )
        result = await self.import_game_with_fallback(app_id, None, opts)
        stplug = self.backend.steam_path / "config" / "stplug-in"
        lua_path = stplug / f"{app_id}.lua"
        st_path = stplug / f"{app_id}.st"
        return {
            "ok": result.success,
            "message": result.message,
            "appid": app_id,
            "lua_path": str(lua_path),
            "lua_exists": lua_path.exists(),
            "st_exists": st_path.exists(),
        }

    async def add_manual_manifest_game(
        self,
        app_id: str,
        name: str = "",
        force: bool = False,
        probe: bool = True,
        try_import: bool = False,
        operator: str = "",
    ) -> Dict[str, Any]:
        """手动将游戏加入可入库清单（manifest 列表 + 全库名称缓存）。"""
        from datetime import datetime

        app_id = str(app_id).strip()
        if not app_id.isdigit():
            return {"ok": False, "message": "AppID 须为数字"}

        if not name or not str(name).strip():
            name = await self._fetch_app_name(app_id)
        name = str(name).strip() or f"AppID {app_id}"

        importable = False
        import_msg = ""
        if probe:
            importable, import_msg = await self.check_importable(app_id, deep_probe=True)
        else:
            manifest_set = set(await self.get_manifest_appids())
            importable = app_id in manifest_set
            import_msg = "清单库已收录" if importable else "未执行入库探测"

        if not importable and not force:
            return {
                "ok": False,
                "message": import_msg,
                "appid": app_id,
                "name": name,
                "importable": False,
                "can_force": True,
            }

        manual_entry = {
            "name": name,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "added_by": str(operator or ""),
            "importable": importable,
            "import_note": import_msg,
            "forced": bool(force and not importable),
        }
        newly, total = self._append_manifest_appid(app_id, manual_entry=manual_entry)
        enrich_meta = await self.enrich_game_meta(app_id, force=True)
        if enrich_meta.get("ok") and enrich_meta.get("name"):
            name = str(enrich_meta["name"])
        await self._sync_games_to_catalog({app_id: name})

        msg = "已加入可入库游戏库"
        if newly:
            msg += f"（当前共 {total} 款）"
        else:
            msg += "（该 AppID 已在清单中，已更新信息）"
        if force and not importable:
            msg += "。警告：未探测到可用清单源，生成 CDK 后客户激活可能失败"
        elif importable:
            msg += f"。{import_msg}"

        payload: Dict[str, Any] = {
            "ok": True,
            "message": msg,
            "appid": app_id,
            "name": name,
            "importable": importable,
            "import_note": import_msg,
            "newly_added": newly,
            "manifest_count": total,
            "forced": bool(force and not importable),
            "game_meta": enrich_meta if enrich_meta.get("ok") else {},
        }
        if try_import:
            imp = await self.import_game_to_server_plugins(app_id)
            payload["server_import"] = imp
            if imp.get("lua_exists"):
                payload["message"] += "；服务端已生成 .lua 插件"
            elif imp.get("ok"):
                payload["message"] += "；入库流程完成但未找到 .lua 文件"
            else:
                payload["message"] += f"；服务端预入库失败：{imp.get('message', '')}"
        return payload

    async def check_importable(self, app_id: str, deep_probe: bool = True) -> tuple[bool, str]:
        """判断 AppID 是否可入库。deep_probe 时会尝试 GitHub / Sudama 探测。"""
        app_id = str(app_id).strip()
        if not app_id.isdigit():
            return False, "AppID 无效"

        manifest_set = set(await self.get_manifest_appids())
        if app_id in manifest_set:
            depot_map = await self.backend._get_depots_and_manifests_from_steamcmd(app_id)
            if depot_map:
                sudama_keys = await self.backend._get_cached_sudama_data() or {}
                matched = [d for d in depot_map if d in sudama_keys]
                if len(matched) == len(depot_map):
                    return True, f"清单库已收录（{len(depot_map)} 个 depot，密钥完整）"
                if deep_probe:
                    await self.backend.checkcn()
                    if await self.backend.check_github_api_rate_limit():
                        hits = await self.search_github_manifests(app_id)
                        if hits:
                            self._remember_importable_appid(app_id)
                            return True, f"GitHub 完整清单源可入库（{hits[0].repo}）"
                return (
                    False,
                    f"清单已收录但 depot 密钥不完整（{len(matched)}/{len(depot_map)}），客户可能入库但无法下载",
                )
            if not deep_probe:
                return False, "清单仅有编号、无 depot，无法入库"

        if not deep_probe:
            return False, "该游戏暂无可用清单，客户激活后无法入库"

        await self.backend.checkcn()
        if await self.backend.check_github_api_rate_limit():
            hits = await self.search_github_manifests(app_id)
            if hits:
                self._remember_importable_appid(app_id)
                return True, f"GitHub 清单源可入库（{hits[0].repo}）"

        try:
            depot_map = await self.backend._get_depots_and_manifests_from_steamui(app_id)
            if depot_map:
                sudama_keys = await self.backend._get_cached_sudama_data()
                if sudama_keys and all(dep_id in sudama_keys for dep_id in depot_map):
                    self._remember_importable_appid(app_id)
                    return True, "Sudama 密钥库可入库"
                matched = [dep_id for dep_id in depot_map if sudama_keys and dep_id in sudama_keys]
                if matched:
                    return (
                        False,
                        f"Sudama 密钥不完整（{len(matched)}/{len(depot_map)}），客户可能入库但无法下载",
                    )
        except Exception as e:
            self.backend.log.warning(f"探测 AppID {app_id} 入库能力失败: {e}")

        return False, "该游戏暂无可用清单，客户激活后无法入库。请配置 GitHub Token 后同步清单，或换有「有清单」标记的游戏"

    async def get_all_catalog_appids(self, refresh: bool = False) -> List[str]:
        """兼容旧接口：全库模式下返回 Steam 全库 AppID，否则返回 manifest 清单。"""
        merge = bool(self.backend.config.get("Catalog_Merge_On_Sync", True))
        if self._use_full_steam_catalog():
            api_key = str(self.backend.config.get("Steam_Web_API_Key", "")).strip()
            catalog = await self.steam_catalog.get_catalog(
                refresh=refresh, steam_api_key=api_key, merge_on_sync=merge
            )
            if catalog:
                return sorted(catalog.keys(), key=lambda x: int(x), reverse=True)
        return await self.get_manifest_appids(refresh=refresh)

    async def sync_all_catalogs(self) -> Dict[str, Any]:
        """同步 manifest 可入库清单 + Steam 全库，合并模式写入数据库。"""
        from datetime import datetime
        from database import get_database_label, is_database_enabled
        try:
            from extensions import sync_progress as _sync_progress
        except Exception:
            _sync_progress = None

        def _progress(percent: int, message: str) -> None:
            if _sync_progress:
                _sync_progress.update_sync(percent, message)

        merge = bool(self.backend.config.get("Catalog_Merge_On_Sync", True))
        storage = get_database_label() if is_database_enabled() else "local"
        result: Dict[str, Any] = {"ok": True, "merge_enabled": merge, "saved_to": storage}

        manifest_ids = await self.get_manifest_appids(refresh=True, force=True)
        result["manifest"] = dict(self._last_manifest_sync_stats or {})
        result["manifest"]["total"] = len(manifest_ids)
        _progress(35, f"可入库清单已完成，共 {len(manifest_ids)} 款")

        if self._use_full_steam_catalog():
            _progress(45, "正在同步 Steam 全库…")
            api_key = str(self.backend.config.get("Steam_Web_API_Key", "")).strip()
            catalog = await self.steam_catalog.get_catalog(
                refresh=True, steam_api_key=api_key, merge_on_sync=merge
            )
            result["steam"] = dict(self.steam_catalog._last_merge_stats or {})
            result["steam"]["total"] = len(catalog)
            _progress(70, f"Steam 全库已完成，共 {len(catalog)} 款")
        else:
            result["steam"] = {"skipped": True, "reason": "Full_Steam_Catalog 未启用"}
            _progress(70, "Steam 全库未启用，已跳过")

        meta = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "merge_enabled": merge,
            "manifest": result["manifest"],
            "steam": result["steam"],
        }
        await asyncio.to_thread(write_json_cache, DOC_CATALOG_SYNC, meta)
        self.backend.log.info(
            f"清单自动同步完成：可入库 {result['manifest'].get('total', 0)} 款，"
            f"Steam 全库 {result['steam'].get('total', 0)} 款（已写入 {storage}）"
        )

        if self.backend.config.get("Catalog_Enrich_Meta_On_Sync", True):
            batch_limit = int(self.backend.config.get("Catalog_Enrich_Meta_Batch_Limit") or 400)
            self.backend.log.info(f"清单同步后自动补齐名称与封面（最多 {batch_limit} 款）…")
            _progress(80, f"正在补齐游戏名称与封面（最多 {batch_limit} 款）…")
            enrich = await self.enrich_manifest_metadata_batch(
                app_ids=manifest_ids,
                force=False,
                limit=batch_limit,
            )
            result["meta_enrich"] = enrich
            _progress(90, "游戏名称与封面补齐完成，正在收尾…")

        return result

    @staticmethod
    def get_catalog_sync_meta() -> Dict[str, Any]:
        return read_json_cache(DOC_CATALOG_SYNC)

    async def list_catalog_games(
        self,
        query: str = "",
        page: int = 1,
        page_size: int = 48,
        refresh: bool = False,
        installed_only: bool = False,
        catalog_filter: str = "all",
        manifest_filter: str = "",
        sort: str = "appid_desc",
    ) -> tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
        """浏览游戏目录，返回 (当前页, 筛选总数, 统计信息)。"""
        if refresh:
            self.steam_catalog.invalidate()

        if installed_only:
            catalog_filter = "installed"

        catalog_filter = catalog_filter if catalog_filter in ("all", "installed", "not_installed") else "all"
        only_importable = bool(self.backend.config.get("Only_Importable_Games", True))
        if not manifest_filter:
            manifest_filter = "has_manifest" if only_importable else "all"
        manifest_filter = manifest_filter if manifest_filter in ("all", "has_manifest", "no_manifest") else "all"
        sort = sort if sort in ("appid_desc", "appid_asc", "name_asc") else "appid_desc"

        installed_ids = {g.appid for g in self.get_installed_games()}
        manifest_ids = await self.get_manifest_appids(refresh=refresh)
        manifest_set = set(manifest_ids)
        full_manifest_set = self._read_full_manifest_appids()
        use_full = self._use_full_steam_catalog()
        name_map: Dict[str, str] = {}
        # 「可入库」列表只需 manifest + game_meta，勿每次加载 11 万条 Steam 全库
        need_steam_catalog = use_full and manifest_filter != "has_manifest"

        if need_steam_catalog:
            api_key = str(self.backend.config.get("Steam_Web_API_Key", "")).strip()
            merge = bool(self.backend.config.get("Catalog_Merge_On_Sync", True))
            name_map = await self.steam_catalog.get_catalog(
                refresh=refresh, steam_api_key=api_key, merge_on_sync=merge
            )
            catalog_total = len(name_map)
        else:
            name_map = {}
            catalog_total = len(manifest_set)

        # 「可入库」以清单库 AppID 为准（7583+），不要求必须出现在 Steam 全库快照里
        if manifest_filter == "has_manifest":
            pool = sorted(manifest_set, key=lambda x: int(x), reverse=True)
        elif use_full and name_map:
            pool = list(name_map.keys())
            if manifest_filter == "no_manifest":
                pool = [a for a in pool if a not in manifest_set]
        else:
            pool = list(manifest_ids)
            if manifest_filter == "no_manifest":
                pool = [a for a in pool if a not in manifest_set]

        if catalog_filter == "installed":
            pool = [a for a in pool if a in installed_ids]
        elif catalog_filter == "not_installed":
            pool = [a for a in pool if a not in installed_ids]

        q = query.strip().lower()
        search_synced = 0
        if q:
            matched: set[str] = set()
            sync_names: Dict[str, str] = {}
            if q.isdigit():
                matched = {a for a in pool if q in a}
                if q in manifest_set:
                    matched.add(q)
                    nm = (name_map.get(q) if use_full and name_map else "") or await self._fetch_app_name(q)
                    if nm and not nm.startswith("AppID "):
                        sync_names[q] = nm
            else:
                if use_full and name_map:
                    matched |= {
                        a
                        for a in pool
                        if q in (name_map.get(a) or "").lower() or q in a
                    }
                for r in await self._search_by_name(q):
                    aid = str(r.appid)
                    if manifest_filter == "has_manifest" and aid not in manifest_set:
                        continue
                    if manifest_filter == "no_manifest" and aid in manifest_set:
                        continue
                    if catalog_filter == "installed" and aid not in installed_ids:
                        continue
                    if catalog_filter == "not_installed" and aid in installed_ids:
                        continue
                    matched.add(aid)
                    if aid in manifest_set and r.name:
                        sync_names[aid] = r.name
            if sync_names:
                search_synced = await self._sync_games_to_catalog(sync_names)
                if use_full and name_map:
                    for aid, nm in sync_names.items():
                        name_map[aid] = nm
            filtered = sorted(matched, key=lambda x: int(x), reverse=True)
        else:
            filtered = list(pool)

        if sort == "name_asc":
            filtered.sort(
                key=lambda a: (
                    0 if a in full_manifest_set else 1,
                    (name_map.get(a) or f"AppID {a}").lower(),
                )
            )
        elif sort == "appid_asc":
            filtered.sort(key=lambda x: (0 if x in full_manifest_set else 1, int(x)))
        else:
            filtered.sort(key=lambda x: (0 if x in full_manifest_set else 1, -int(x)))

        total = len(filtered)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        start = (page - 1) * page_size
        chunk = filtered[start : start + page_size]

        entries = [
            ManifestGameEntry(
                appid=appid,
                installed=appid in installed_ids,
                source="本地" if appid in installed_ids else ("清单库" if appid in manifest_set else "Steam"),
            )
            for appid in chunk
        ]
        game_meta_store = self._read_game_meta_store()
        if use_full and name_map:
            for entry in entries:
                entry.name = name_map.get(entry.appid, "") or entry.name
        for entry in entries:
            gm = game_meta_store.get(entry.appid) or {}
            if gm.get("name"):
                entry.name = str(gm["name"])
            elif not entry.name:
                entry.name = f"AppID {entry.appid}"
        games: List[Dict[str, Any]] = []
        for entry in entries:
            has_manifest = entry.appid in manifest_set
            gm = game_meta_store.get(entry.appid) or {}
            display_name = (
                gm.get("name")
                or entry.name
                or name_map.get(entry.appid)
                or f"AppID {entry.appid}"
            )
            header_url = gm.get("header") or STEAM_HEADER_URL.format(appid=entry.appid)
            games.append(
                {
                    "appid": entry.appid,
                    "name": display_name,
                    "name_zh": gm.get("name_zh", ""),
                    "name_en": gm.get("name_en", ""),
                    "header": header_url,
                    "hero": gm.get("hero") or STEAM_LIBRARY_HERO_URL.format(appid=entry.appid),
                    "installed": entry.installed,
                    "has_manifest": has_manifest,
                    "source": entry.source,
                    "store_url": f"https://store.steampowered.com/app/{entry.appid}",
                }
            )

        manifest_in_catalog = len(manifest_set)
        manifest_in_steam_snapshot = (
            sum(1 for a in name_map if a in manifest_set) if use_full and name_map else manifest_in_catalog
        )

        stats = {
            "total_unique": catalog_total,
            "manifest_count": manifest_in_catalog,
            "full_manifest_count": len(full_manifest_set & manifest_set),
            "manifest_in_steam_snapshot": manifest_in_steam_snapshot,
            "no_manifest_count": max(0, catalog_total - manifest_in_catalog),
            "installed_count": len(installed_ids),
            "not_installed_count": max(0, catalog_total - len(installed_ids)),
            "filtered_count": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "catalog_filter": catalog_filter,
            "manifest_filter": manifest_filter,
            "sort": sort,
            "full_steam_catalog": use_full,
            "only_importable_default": only_importable,
            "github_token_configured": bool(self.backend.get_github_tokens()),
            "github_token_count": len(self.backend.get_github_tokens()),
            "search_synced": search_synced,
        }
        if refresh and self._last_manifest_sync_stats:
            stats["sync"] = dict(self._last_manifest_sync_stats)
        return games, total, stats

    async def _list_sudama_appids(
        self, query: str, page: int, page_size: int
    ) -> tuple[List[ManifestGameEntry], int]:
        data = await self.backend._get_cached_sudama_data()
        if not data:
            return [], 0
        keys = [k for k in data.keys() if str(k).isdigit()]
        if query:
            if query.isdigit():
                keys = [k for k in keys if query in k]
            else:
                keys = []
        keys.sort(key=lambda x: int(x), reverse=True)
        total = len(keys)
        start = (page - 1) * page_size
        chunk = keys[start : start + page_size]
        return [ManifestGameEntry(appid=k) for k in chunk], total

    async def enrich_game_names(self, entries: List[ManifestGameEntry], limit: int = 40) -> None:
        await self._enrich_names(entries, limit)

    async def _enrich_names(self, entries: List[ManifestGameEntry], limit: int) -> None:
        tasks = []
        targets = [e for e in entries if not e.name][:limit]
        for entry in targets:
            tasks.append(self._fetch_app_name(entry.appid))
        if not tasks:
            return
        names = await asyncio.gather(*tasks, return_exceptions=True)
        for entry, name in zip(targets, names):
            if isinstance(name, str) and name:
                entry.name = name

    async def import_game(
        self,
        app_id: str,
        source: ManifestSource,
        options: ImportOptions,
        github_repo: Optional[str] = None,
    ) -> ImportResult:
        if not self._initialized:
            return ImportResult(app_id, False, "服务尚未初始化")

        if self.environment.status == "conflict":
            return ImportResult(app_id, False, "解锁环境冲突，请先清理 SteamTools / GreenLuma")

        if self.environment.status == "none" and not self.backend.unlocker_type:
            return ImportResult(app_id, False, "未选择解锁工具")

        self.backend.use_st_auto_update = options.auto_update_manifest
        add_all_dlc = options.add_all_dlc
        patch_depot_key = options.patch_workshop_key

        await self.backend.checkcn()

        if source.kind in ("builtin_github", "custom_github"):
            if not await self.backend.check_github_api_rate_limit():
                return ImportResult(app_id, False, "GitHub API 速率限制，请配置 Token")
            repo = github_repo or source.repo
            success = await self.backend.process_github_manifest(
                app_id, repo, add_all_dlc, patch_depot_key
            )
        elif source.kind == "builtin_zip":
            method_name = self.BUILTIN_ZIP_SOURCES[source.key][1]
            handler = getattr(self.backend, method_name)
            if source.key == "buqiuren":
                success = await handler(app_id)
            else:
                success = await handler(app_id, add_all_dlc, patch_depot_key)
        elif source.kind == "custom_zip":
            repo_config = next(
                (
                    r
                    for r in self.backend.get_custom_zip_repos()
                    if r.get("url") == source.repo or r.get("name") in source.name
                ),
                None,
            )
            if not repo_config:
                return ImportResult(app_id, False, "未找到自定义 ZIP 清单库配置")
            success = await self.backend.process_custom_zip_manifest(
                app_id, repo_config, add_all_dlc, patch_depot_key
            )
        else:
            return ImportResult(app_id, False, f"未知清单源类型: {source.kind}")

        if success:
            self.ensure_steamtools_lua_format(app_id)
            self.sync_lua_to_legacy_folder(app_id)
            return ImportResult(app_id, True, self.build_post_import_message(app_id))
        return ImportResult(
            app_id,
            False,
            "入库失败：当前清单源没有该游戏，或密钥/API 不可用。建议改用 ManifestHub(2) 或 Sudama，并确保 SteamTools 已启动。",
        )

    async def import_game_with_fallback(
        self,
        app_id: str,
        primary_source: Optional[ManifestSource],
        options: ImportOptions,
        github_repo: Optional[str] = None,
    ) -> ImportResult:
        tried: List[str] = []
        sources = self.get_manifest_sources()
        if not sources:
            return ImportResult(app_id, False, "服务端无可用清单源")

        if primary_source is None:
            primary_source = sources[0]

        async def _try(source: ManifestSource, repo: Optional[str] = None) -> ImportResult:
            tried.append(source.name)
            return await self.import_game(app_id, source, options, github_repo=repo)

        first = await _try(primary_source, github_repo)
        if first.success:
            return first

        async def _try_github_hits() -> Optional[ImportResult]:
            if primary_source.kind in ("builtin_github", "custom_github"):
                return None
            await self.backend.checkcn()
            if not await self.backend.check_github_api_rate_limit():
                return None
            hits = await self.search_github_manifests(app_id)
            for hit in hits[:3]:
                source = ManifestSource(
                    key=f"github:{hit.repo}",
                    name=hit.repo,
                    kind="builtin_github",
                    repo=hit.repo,
                )
                if source.name in tried:
                    continue
                result = await _try(source, hit.repo)
                if result.success:
                    self.sync_lua_to_legacy_folder(app_id)
                    result.message = self.build_post_import_message(app_id)
                    return result
            return None

        github_result = await _try_github_hits()
        if github_result:
            return github_result

        fallback_keys = [k for k in self.PREFERRED_ZIP_SOURCE_KEYS if k != primary_source.key]
        for key in fallback_keys:
            name, _ = self.BUILTIN_ZIP_SOURCES[key]
            source = ManifestSource(key=key, name=name, kind="builtin_zip")
            if source.name in tried:
                continue
            result = await _try(source)
            if result.success:
                self.sync_lua_to_legacy_folder(app_id)
                result.message = self.build_post_import_message(app_id)
                return result

        github_result = await _try_github_hits()
        if github_result:
            return github_result

        steam_path = str(self.backend.steam_path or "")
        on_server = bool(steam_path) and (
            "/opt/" in steam_path.replace("\\", "/")
            or "steam-sandbox" in steam_path
        )
        client_hint = (
            "客户电脑需安装 Steam + 执行过 irm 激活脚本（内置注入，不必单独装 SteamTools 软件）。"
            if on_server
            else "客户电脑请安装 Steam，并执行过 irm 激活脚本；若用 SteamTools 模式，可启动一次 SteamTools.exe。"
        )
        return ImportResult(
            app_id,
            False,
            "所有清单源均失败。\n"
            f"已尝试: {', '.join(tried)}\n"
            "常见原因：\n"
            f"1. 填的是 DLC/升级包 AppID，不是主游戏（请用 Steam 商店页主游戏编号）\n"
            "2. 该游戏在 Steam 上尚无可用 depot（未发售、仅预约、或区服限制）\n"
            "3. 各清单库均未收录该游戏\n"
            "4. 服务端 config.json 未配置 Custom_Steam_Path\n"
            f"5. {client_hint}\n"
            "建议：后台「单独添加游戏」先点「探测/预览」；主游戏 AppID 且探测通过后再勾选「生成 lua」。",
        )

    async def import_games_batch(
        self,
        app_ids: List[str],
        source: ManifestSource,
        options: ImportOptions,
        auto_fallback: bool = True,
        github_repo: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> BulkImportResult:
        """批量入库当前列表中的游戏，跳过已入库项。"""
        installed_ids = {g.appid for g in self.get_installed_games()}
        pending = [aid for aid in app_ids if aid not in installed_ids]
        succeeded: List[str] = []
        failed: List[tuple[str, str]] = []
        total = len(pending)

        if not pending:
            return BulkImportResult(succeeded=[], failed=[])

        repo = github_repo if source.kind in ("builtin_github", "custom_github") else None
        for index, app_id in enumerate(pending, start=1):
            if progress_callback:
                progress_callback(index, total, app_id)
            self.backend.log.info(f"[批量入库 {index}/{total}] 正在处理 AppID {app_id}…")
            try:
                if auto_fallback:
                    result = await self.import_game_with_fallback(app_id, source, options, github_repo=repo)
                else:
                    result = await self.import_game(app_id, source, options, github_repo=repo)
            except Exception as e:
                failed.append((app_id, str(e)))
                self.backend.log.error(f"AppID {app_id} 入库异常: {e}")
                continue

            if result.success:
                succeeded.append(app_id)
                self.backend.log.info(f"AppID {app_id} 入库成功")
            else:
                failed.append((app_id, result.message))
                self.backend.log.warning(f"AppID {app_id} 入库失败")

        return BulkImportResult(succeeded=succeeded, failed=failed)

    async def import_workshop(self, workshop_input: str) -> ImportResult:
        if not self._initialized:
            return ImportResult(workshop_input, False, "服务尚未初始化")
        success = await self.backend.process_workshop_manifest(workshop_input)
        if success:
            return ImportResult(workshop_input, True, "创意工坊清单处理成功")
        return ImportResult(workshop_input, False, "创意工坊清单处理失败")

    async def load_config(self) -> Dict[str, Any]:
        config = await self.backend.load_config()
        return config or {}

    async def save_config(self, config: Dict[str, Any]) -> None:
        import aiofiles
        import ujson as json

        async with aiofiles.open("./config.json", "w", encoding="utf-8") as f:
            await f.write(json.dumps(config, indent=2, ensure_ascii=False))
        self.backend.config = config

    async def shutdown(self) -> None:
        await self.backend.cleanup_temp_files()
        await self.backend.close_resources()

    @staticmethod
    def app_version() -> str:
        return CURRENT_VERSION
