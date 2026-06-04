"""游戏运维：卸载、更新检测、DLC 批量、创意工坊。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def uninstall_game(steam_path: Path, app_id: str) -> Dict[str, Any]:
    app_id = str(app_id).strip()
    removed: List[str] = []
    if not steam_path or not steam_path.exists():
        return {"ok": False, "message": "Steam 路径无效", "removed": removed}
    targets = [
        steam_path / "config" / "stplug-in" / f"{app_id}.lua",
        steam_path / "config" / "stplug-in" / f"{app_id}.st",
    ]
    depot = steam_path / "config" / "depotcache"
    if depot.exists():
        for f in depot.glob(f"*{app_id}*"):
            targets.append(f)
    for p in targets:
        if p.exists():
            try:
                p.unlink()
                removed.append(str(p.relative_to(steam_path)))
            except Exception as e:
                return {"ok": False, "message": str(e), "removed": removed}
    return {"ok": True, "message": f"已卸载 AppID {app_id}", "removed": removed}


def check_game_updates(steam_path: Path, app_ids: List[str]) -> List[Dict[str, Any]]:
    results = []
    plugin_dir = steam_path / "config" / "stplug-in" if steam_path else None
    for app_id in app_ids:
        app_id = str(app_id).strip()
        lua = plugin_dir / f"{app_id}.lua" if plugin_dir else None
        installed = lua.exists() if lua else False
        results.append({
            "appid": app_id,
            "installed": installed,
            "needs_update": not installed,
            "message": "已安装，可重新入库更新" if installed else "未安装",
        })
    return results


async def import_dlc_batch(box_service, app_id: str, dlc_ids: List[str], source_key: str = "manifesthub2") -> Dict[str, Any]:
    from box_service import ImportOptions

    app_id = str(app_id).strip()
    opts = ImportOptions(add_all_dlc=True)
    results = []
    main = await box_service.import_game_with_fallback(app_id, None, opts)
    results.append({"appid": app_id, "ok": main.ok, "message": main.message})
    for dlc in dlc_ids:
        dlc = str(dlc).strip()
        if not dlc.isdigit() or dlc == app_id:
            continue
        r = await box_service.import_game_with_fallback(dlc, None, opts)
        results.append({"appid": dlc, "ok": r.ok, "message": r.message})
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": ok_count > 0, "total": len(results), "success": ok_count, "results": results}


def enable_workshop_stub(steam_path: Path, app_id: str) -> Dict[str, Any]:
    """创意工坊：写入 workshop 启用标记到 lua（需 SteamTools）。"""
    app_id = str(app_id).strip()
    lua = steam_path / "config" / "stplug-in" / f"{app_id}.lua"
    if not lua.exists():
        return {"ok": False, "message": "请先入库主游戏"}
    content = lua.read_text(encoding="utf-8", errors="ignore")
    marker = f"-- workshop_enabled_{app_id}"
    if marker in content:
        return {"ok": True, "message": "创意工坊已启用"}
    extra = f"\n{marker}\n-- 创意工坊支持标记（需 SteamTools 客户端配合）\n"
    lua.write_text(content.rstrip() + extra, encoding="utf-8")
    return {"ok": True, "message": "已写入创意工坊支持标记"}
