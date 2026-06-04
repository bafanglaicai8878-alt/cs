"""平台工具：JSON 备份、文件锁、限流、密码策略。"""

from __future__ import annotations

import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

_FILE_LOCKS: Dict[str, threading.Lock] = {}
_BACKUP_DIR = Path("./data_backups")


def file_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    if key not in _FILE_LOCKS:
        _FILE_LOCKS[key] = threading.Lock()
    return _FILE_LOCKS[key]


def backup_json(path: Path, keep: int = 20) -> None:
    if not path.exists():
        return
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = _BACKUP_DIR / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    backups = sorted(_BACKUP_DIR.glob(f"{path.stem}_*{path.suffix}"), reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


class RateLimiter:
    """内存滑动窗口限流（进程内有效）。"""

    def __init__(self) -> None:
        self._hits: Dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_sec: int) -> Tuple[bool, str]:
        if limit <= 0:
            return True, ""
        now = time.time()
        with self._lock:
            arr = [t for t in self._hits.get(key, []) if now - t < window_sec]
            if len(arr) >= limit:
                self._hits[key] = arr
                return False, f"操作过于频繁，请 {window_sec} 秒后再试"
            arr.append(now)
            self._hits[key] = arr
        return True, ""


RATE_LIMITER = RateLimiter()


def normalize_public_url(raw: str, default_port: int = 8787) -> tuple[str, str]:
    """
    解析对外地址，返回 (完整服务 URL, irm 指令用地址)。
    支持：playgame.com / https://playgame.com / http://IP:8787
    标准 80/443 端口时，指令地址为纯域名（不带 http）。
    """
    from urllib.parse import urlparse

    s = str(raw or "").strip().rstrip("/")
    if not s or "你的" in s or "公网IP" in s:
        fallback = f"http://127.0.0.1:{default_port}"
        return fallback, fallback

    def _pair(scheme: str, host: str, port: int | None) -> tuple[str, str]:
        if not host:
            fallback = f"http://127.0.0.1:{default_port}"
            return fallback, fallback
        if port is None:
            port = 443 if scheme == "https" else 80
        if port in (80, 443):
            full = f"{scheme}://{host}"
            return full, host
        full = f"{scheme}://{host}:{port}"
        return full, full

    if "://" not in s:
        if s.count(":") == 1 and not s.startswith("["):
            host, port_s = s.rsplit(":", 1)
            if port_s.isdigit():
                port = int(port_s)
                scheme = "https" if port == 443 else "http"
                return _pair(scheme, host, port)
        host = s.split("/")[0]
        return _pair("https", host, 443)

    parsed = urlparse(s)
    host = parsed.hostname or ""
    scheme = parsed.scheme or "http"
    return _pair(scheme, host, parsed.port)


def irm_cmd_base(service_url: str, default_port: int = 8787) -> str:
    return normalize_public_url(service_url, default_port)[1]


def validate_password(password: str, min_len: int = 8) -> None:
    pwd = str(password or "")
    if len(pwd) < min_len:
        raise ValueError(f"密码至少 {min_len} 位")
    if not re.search(r"[A-Za-z]", pwd) or not re.search(r"\d", pwd):
        raise ValueError("密码需同时包含字母和数字")
