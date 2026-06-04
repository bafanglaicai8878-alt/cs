"""Steam 全量 AppID 目录（约 11 万+），用于浏览与名称搜索。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from database import DOC_STEAM_CATALOG, read_json_cache, write_json_cache

STEAM_CATALOG_CACHE_PATH = Path("./steam_catalog_cache.json")
STEAM_CATALOG_SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/woctezuma/steam-store-snapshots/main/data/ISteamApps.json"
)
STEAM_CATALOG_TTL = 7 * 24 * 3600


class SteamCatalogService:
    """拉取并缓存 Steam 全库 AppID + 名称。"""

    def __init__(self, http_client, log: Optional[Callable[[str], None]] = None):
        self.client = http_client
        self._log = log or (lambda _msg: None)
        self._memory_cache: Optional[tuple[float, Dict[str, str]]] = None
        self._last_merge_stats: Dict[str, int] = {}

    def invalidate(self) -> None:
        self._memory_cache = None

    def upsert_apps(self, apps: Dict[str, str]) -> int:
        """将搜索到的游戏名称合并进全库缓存（便于后续本地按名搜索）。"""
        if not apps:
            return 0
        existing = self._read_raw_cache()
        updated = 0
        for appid, name in apps.items():
            aid = str(appid).strip()
            nm = str(name or "").strip()
            if not aid.isdigit() or not nm:
                continue
            if existing.get(aid) != nm:
                existing[aid] = nm
                updated += 1
        if not updated:
            return 0
        self._save_disk_cache(
            existing,
            "search_discover",
            {"newly_added": updated, "previous_local": len(existing) - updated},
        )
        self._memory_cache = (time.time(), dict(existing))
        return updated

    def _read_raw_cache(self) -> Dict[str, str]:
        try:
            raw = read_json_cache(DOC_STEAM_CATALOG, STEAM_CATALOG_CACHE_PATH)
            if not raw:
                return {}
            apps = raw.get("apps") or {}
            return {str(k): str(v) for k, v in apps.items() if str(k).isdigit()}
        except Exception:
            return {}

    def _load_disk_cache(self) -> Optional[Dict[str, str]]:
        try:
            raw = read_json_cache(DOC_STEAM_CATALOG, STEAM_CATALOG_CACHE_PATH)
            if not raw:
                return None
            age = time.time() - float(raw.get("timestamp", 0))
            if age > STEAM_CATALOG_TTL:
                return None
            catalog = self._read_raw_cache()
            return catalog or None
        except Exception:
            return None

    def _save_disk_cache(self, catalog: Dict[str, str], source: str, merge_stats: Optional[Dict[str, int]] = None) -> None:
        try:
            payload: Dict[str, object] = {
                "timestamp": time.time(),
                "source": source,
                "count": len(catalog),
                "apps": catalog,
            }
            if merge_stats:
                payload["merge_stats"] = merge_stats
            write_json_cache(DOC_STEAM_CATALOG, payload, STEAM_CATALOG_CACHE_PATH)
        except Exception as e:
            self._log(f"写入 Steam 全库缓存失败: {e}")

    @staticmethod
    def _merge_catalogs(existing: Dict[str, str], remote: Dict[str, str]) -> tuple[Dict[str, str], Dict[str, int]]:
        merged = dict(existing)
        newly_added = 0
        for appid, name in remote.items():
            if appid not in merged:
                merged[appid] = name
                newly_added += 1
        return merged, {
            "previous_local": len(existing),
            "fetched_from_remote": len(remote),
            "newly_added": newly_added,
            "kept_previous": len(existing),
            "total_after_sync": len(merged),
        }

    async def get_catalog(
        self,
        refresh: bool = False,
        steam_api_key: str = "",
        merge_on_sync: bool = True,
    ) -> Dict[str, str]:
        now = time.time()
        if (
            not refresh
            and self._memory_cache
            and now - self._memory_cache[0] < 3600
        ):
            return dict(self._memory_cache[1])

        if not refresh:
            disk = self._load_disk_cache()
            if disk:
                self._memory_cache = (now, disk)
                self._log(f"使用本地 Steam 全库缓存，共 {len(disk)} 款")
                return dict(disk)

        existing = self._read_raw_cache() if (refresh and merge_on_sync) else {}
        catalog: Dict[str, str] = {}
        source = "snapshot"
        api_key = str(steam_api_key or "").strip()
        if api_key:
            try:
                catalog = await self._fetch_from_steam_api(api_key)
                if catalog:
                    source = "steam_api"
            except Exception as e:
                self._log(f"Steam API 拉取全库失败，改用 GitHub 快照: {e}")

        if not catalog:
            catalog = await self._fetch_from_snapshot()
            source = "snapshot"

        merge_stats: Dict[str, int] = {}
        if refresh and merge_on_sync and existing:
            catalog, merge_stats = self._merge_catalogs(existing, catalog)
            self._last_merge_stats = {**merge_stats, "merge_enabled": 1}
            self._log(
                f"Steam 全库合并完成：远程 {merge_stats['fetched_from_remote']} 款，"
                f"新增 {merge_stats['newly_added']} 款，保留本地 {merge_stats['kept_previous']} 款，"
                f"合计 {merge_stats['total_after_sync']} 款"
            )
        elif catalog:
            self._last_merge_stats = {
                "previous_local": len(existing),
                "fetched_from_remote": len(catalog),
                "newly_added": len(catalog),
                "kept_previous": 0,
                "total_after_sync": len(catalog),
                "merge_enabled": 0,
            }

        if catalog:
            await asyncio.to_thread(
                self._save_disk_cache,
                catalog,
                source,
                merge_stats or self._last_merge_stats,
            )
            self._memory_cache = (now, catalog)
            if not merge_stats:
                self._log(f"Steam 全库已更新（{source}），共 {len(catalog)} 款")
        return dict(catalog)

    async def _fetch_from_snapshot(self) -> Dict[str, str]:
        self._log("正在从 GitHub 快照下载 Steam 全库（约 13MB，首次需等待）…")
        resp = await self.client.get(STEAM_CATALOG_SNAPSHOT_URL, timeout=180)
        resp.raise_for_status()
        payload = resp.json()
        apps = payload.get("applist", {}).get("apps", [])
        catalog: Dict[str, str] = {}
        for item in apps:
            appid = str(item.get("appid", "")).strip()
            name = str(item.get("name", "")).strip()
            if appid.isdigit():
                catalog[appid] = name or f"AppID {appid}"
        return catalog

    async def _fetch_from_steam_api(self, api_key: str) -> Dict[str, str]:
        catalog: Dict[str, str] = {}
        last_appid = 0
        page = 0
        while page < 10:
            page += 1
            params: Dict[str, str | int] = {
                "key": api_key,
                "max_results": 50000,
                "include_games": "true",
                "include_dlc": "true",
                "include_software": "true",
                "include_videos": "true",
                "include_hardware": "true",
            }
            if last_appid:
                params["last_appid"] = last_appid
            resp = await self.client.get(
                "https://api.steampowered.com/IStoreService/GetAppList/v1/",
                params=params,
                timeout=120,
            )
            resp.raise_for_status()
            body = resp.json()
            apps = body.get("response", {}).get("apps") or []
            if not apps:
                break
            for item in apps:
                appid = str(item.get("appid", "")).strip()
                name = str(item.get("name", "")).strip()
                if appid.isdigit():
                    catalog[appid] = name or f"AppID {appid}"
            last_appid = int(apps[-1]["appid"])
            if len(apps) < 50000:
                break
        return catalog
