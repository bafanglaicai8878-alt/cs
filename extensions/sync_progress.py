"""清单同步进度与 Webhook。"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from extensions.notification import notify_sync_failed
from extensions.store import load_extensions, save_extensions

_lock = threading.Lock()
_sync_guard = threading.Lock()
_sync_thread: Optional[threading.Thread] = None
_STALE_RUNNING_AFTER = timedelta(hours=3)


def get_progress() -> Dict[str, Any]:
    return dict(load_extensions().get("sync_progress", {}))


def _is_stale_running(prog: Dict[str, Any]) -> bool:
    if not prog.get("running"):
        return False
    raw = str(prog.get("updated_at", "") or "").strip()
    if not raw:
        return False
    try:
        updated_at = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return datetime.now() - updated_at > _STALE_RUNNING_AFTER


def is_sync_running() -> bool:
    prog = get_progress()
    if _is_stale_running(prog):
        _set_progress(
            running=False,
            percent=0,
            message="",
            error="上次清单同步状态已超时，已自动解除锁定",
        )
        return False
    return bool(prog.get("running"))


def clear_stale_sync_lock() -> bool:
    prog = get_progress()
    if not _is_stale_running(prog):
        return False
    _set_progress(
        running=False,
        percent=0,
        message="",
        error="上次清单同步状态已超时，已自动解除锁定",
    )
    return True


def _set_progress(**kwargs: Any) -> None:
    with _lock:
        data = load_extensions()
        prog = data.setdefault("sync_progress", {})
        prog.update(kwargs)
        prog["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_extensions(data)


def start_sync() -> None:
    _set_progress(running=True, percent=0, message="准备同步…", error="")


def update_sync(percent: int, message: str) -> None:
    _set_progress(running=True, percent=max(0, min(100, percent)), message=message)


def finish_sync(success: bool, message: str = "") -> None:
    if success:
        _set_progress(running=False, percent=100, message=message or "同步完成", error="")
    else:
        _set_progress(running=False, percent=0, message="", error=message)
        notify_sync_failed(message)


def start_background_catalog_sync(server, source: str = "manual") -> Dict[str, Any]:
    """后台启动清单同步，避免阻塞 HTTP 请求与前端长时间 loading。"""
    global _sync_thread

    if is_sync_running():
        prog = get_progress()
        return {
            "ok": False,
            "message": "清单同步正在进行中，请稍后再试",
            "progress": prog,
        }

    def _run() -> None:
        try:
            from extensions.routes import sync_catalogs_tracked
            from web_server import run_async

            run_async(sync_catalogs_tracked(server), timeout=7200)
        except Exception as e:
            finish_sync(False, str(e))

    with _sync_guard:
        if is_sync_running():
            return {"ok": False, "message": "清单同步正在进行中，请稍后再试"}
        start_sync()
        update_sync(1, f"{'自动' if source == 'auto' else '手动'}同步已启动…")
        _sync_thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"CatalogSync-{source}",
        )
        _sync_thread.start()

    return {
        "ok": True,
        "message": "已开始同步，请在本页或扩展中心查看进度",
        "started": True,
        "source": source,
    }


def trigger_webhook_sync(server) -> Dict[str, Any]:
    """外部 Webhook 触发同步（异步）。"""
    return start_background_catalog_sync(server, source="webhook")
