"""游戏卡片元数据与封面缓存。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None  # type: ignore
    ImageTk = None  # type: ignore

CACHE_DIR = Path("./cache/covers")
CARD_W, CARD_H = 220, 124

# 首次启动首页展示的精选游戏（GitHub 清单库常见）
FEATURED_APPIDS = [
    "1145360", "730", "570", "413150", "105600", "1956800",
    "550", "892970", "1245620", "1091500", "1174180", "271590",
    "359550", "578080", "252490", "1172470", "1938090", "2358720",
    "814380", "990080", "1240440", "1086940", "1599340", "1716740",
]


@dataclass
class GameCardInfo:
    appid: str
    name: str = ""
    name_en: str = ""
    genre: str = ""
    header_url: str = ""
    installed: bool = False
    in_manifest: bool = True
    status: str = ""

    @property
    def display_title(self) -> str:
        if self.name and self.name_en and self.name != self.name_en:
            return f"{self.name}/{self.name_en}"
        return self.name or self.name_en or f"AppID {self.appid}"

    @property
    def default_header_url(self) -> str:
        return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{self.appid}/header.jpg"


class GameCatalogService:
    def __init__(self, http_client):
        self.client = http_client
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def fetch_card_info(self, app_id: str, installed: bool = False) -> GameCardInfo:
        card = GameCardInfo(appid=app_id, installed=installed)
        url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=schinese"
        try:
            resp = await self.client.get(url, timeout=12)
            payload = resp.json().get(str(app_id), {})
            if payload.get("success"):
                data = payload.get("data", {})
                card.name = data.get("name", "") or ""
                card.header_url = data.get("header_image", "") or card.default_header_url
                genres = data.get("genres") or []
                if genres:
                    card.genre = genres[0].get("description", "") or ""
                if not card.genre:
                    tags = data.get("categories") or []
                    if tags:
                        card.genre = tags[0].get("description", "")[:8]
        except Exception:
            pass
        if not card.header_url:
            card.header_url = card.default_header_url
        if not card.name:
            card.name = f"AppID {app_id}"
        card.status = "已入库" if installed else "可入库"
        return card

    async def fetch_cards_batch(
        self, app_ids: List[str], installed_ids: Optional[set[str]] = None, limit: int = 24
    ) -> List[GameCardInfo]:
        installed_ids = installed_ids or set()
        ids = app_ids[:limit]
        sem = asyncio.Semaphore(6)

        async def _one(aid: str) -> GameCardInfo:
            async with sem:
                return await self.fetch_card_info(aid, aid in installed_ids)

        return await asyncio.gather(*[_one(a) for a in ids])

    def featured_appids(self, page: int = 1, page_size: int = 24) -> List[str]:
        start = (page - 1) * page_size
        return FEATURED_APPIDS[start : start + page_size]

    def cover_cache_path(self, app_id: str) -> Path:
        return CACHE_DIR / f"{app_id}.jpg"

    async def download_cover_bytes(self, app_id: str, url: Optional[str] = None) -> Optional[bytes]:
        cache = self.cover_cache_path(app_id)
        if cache.exists() and cache.stat().st_size > 1000:
            return cache.read_bytes()
        fetch_url = url or f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg"
        try:
            resp = await self.client.get(fetch_url, timeout=20, follow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 1000:
                cache.write_bytes(resp.content)
                return resp.content
        except Exception:
            pass
        return None

    def bytes_to_photo(self, data: bytes, width: int = CARD_W, height: int = CARD_H):
        if not Image or not ImageTk:
            return None
        img = Image.open(BytesIO(data)).convert("RGB")
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)

    async def load_photo(self, app_id: str, url: Optional[str] = None):
        data = await self.download_cover_bytes(app_id, url)
        if data:
            return self.bytes_to_photo(data)
        return None
