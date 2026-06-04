"""将 Steam 恢复为官方干净状态：移除注入 DLL、解锁插件、相关注册表。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

STEAM = Path(r"d:\steam")

INJECT_DLLS = [
    "xinput1_4.dll",
    "dwmapi.dll",
    "hid.dll",
    "version.dll",
    "user32.dll",
]

CONFLICT_PATHS = [
    "opensteamtool.toml",
    "opensteamtool",
    "config/lua",
    "config/stplug-in",
]

REG_KEYS = [
    r"HKCU\Software\Valve\Steamtools",
]


def stop_steam() -> None:
    for proc in ("steam.exe", "steamwebhelper.exe"):
        subprocess.run(
            ["taskkill", "/IM", proc, "/F"],
            capture_output=True,
            check=False,
        )


def main() -> int:
    if not STEAM.exists() or not (STEAM / "steam.exe").exists():
        print(f"未找到 Steam: {STEAM}")
        return 1

    print(f"目标 Steam 路径: {STEAM}")
    print("正在关闭 Steam…")
    stop_steam()

    removed: list[str] = []

    for name in INJECT_DLLS:
        path = STEAM / name
        if path.exists():
            path.unlink()
            removed.append(str(path.relative_to(STEAM)))

    steam_cfg = STEAM / "steam.cfg"
    if steam_cfg.exists():
        steam_cfg.unlink()
        removed.append("steam.cfg")

    for rel in CONFLICT_PATHS:
        path = STEAM / rel
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(rel)

    for key in REG_KEYS:
        r = subprocess.run(
            ["reg", "delete", key, "/f"],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0:
            removed.append(f"registry:{key}")

    print("\n已清理以下项目：")
    if removed:
        for item in removed:
            print(f"  - {item}")
    else:
        print("  （未发现需要清理的项目）")

    stplug = STEAM / "config" / "stplug-in"
    stplug.mkdir(parents=True, exist_ok=True)

    print("\nSteam 已恢复为官方干净状态。")
    print("请重新启动 Steam 客户端。")
    print("说明：已下载的游戏文件仍在 steamapps 目录，仅移除了解锁插件与注入文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
