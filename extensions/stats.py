"""增强统计与导出。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from extensions.store import load_extensions


def _parse_day(ts: str) -> str:
    return str(ts or "")[:10] or "unknown"


def cdk_daily_stats(cdk_data: Dict[str, Any], days: int = 30) -> List[Dict[str, Any]]:
    keys = cdk_data.get("keys", {})
    by_day: Dict[str, Dict[str, int]] = defaultdict(lambda: {"created": 0, "used": 0})
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for raw in keys.values():
        ca = _parse_day(str(raw.get("created_at", "")))
        if ca >= cutoff:
            by_day[ca]["created"] += 1
        if raw.get("used") and raw.get("used_at"):
            ua = _parse_day(str(raw.get("used_at", "")))
            if ua >= cutoff:
                by_day[ua]["used"] += 1
    return [{"date": d, **by_day[d]} for d in sorted(by_day.keys())]


def cdk_agent_stats(cdk_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    keys = cdk_data.get("keys", {})
    by_agent: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "used": 0})
    for raw in keys.values():
        aid = str(raw.get("agent_id") or "system")
        by_agent[aid]["total"] += 1
        if raw.get("used"):
            by_agent[aid]["used"] += 1
    return [{"agent_id": k, **v} for k, v in sorted(by_agent.items(), key=lambda x: -x[1]["total"])]


def cdk_game_stats(cdk_data: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
    keys = cdk_data.get("keys", {})
    by_app: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "used": 0, "name": ""})
    for raw in keys.values():
        app = str(raw.get("appid", ""))
        by_app[app]["count"] += 1
        by_app[app]["name"] = str(raw.get("name") or by_app[app]["name"])
        if raw.get("used"):
            by_app[app]["used"] += 1
    rows = [{"appid": k, **v} for k, v in by_app.items()]
    rows.sort(key=lambda x: -x["count"])
    return rows[:limit]


def activation_stats(days: int = 30) -> List[Dict[str, Any]]:
    logs = load_extensions().get("activation_logs") or []
    by_day: Dict[str, int] = defaultdict(int)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for item in logs:
        d = _parse_day(str(item.get("at", "")))
        if d >= cutoff:
            by_day[d] += 1
    return [{"date": d, "count": by_day[d]} for d in sorted(by_day.keys())]


def full_dashboard_stats(admin_service, cdk_service) -> Dict[str, Any]:
    return {
        "cdk_daily": cdk_daily_stats(cdk_service._data),
        "cdk_agents": cdk_agent_stats(cdk_service._data),
        "cdk_games": cdk_game_stats(cdk_service._data),
        "activations": activation_stats(),
        "recharge_pending": len([r for r in admin_service.list_recharge_requests() if r.get("status") == "pending"]),
        "withdraw_pending": len([r for r in admin_service.list_withdraw_requests() if r.get("status") == "pending"]),
    }
