"""将 admin_db / cdk_db / client_db 及 SQLite 数据导入线上数据库。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    config_path = ROOT / "config.json"
    if not config_path.exists():
        print("请先创建 config.json 并设置 Database.enabled = true", file=sys.stderr)
        return 1
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db_cfg = cfg.setdefault("Database", {})
    db_cfg["enabled"] = True
    config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print("已启用 Database.enabled，正在迁移…")

    from database import (
        ensure_database_bootstrapped,
        get_store,
        migrate_file_caches_to_store,
        migrate_json_files_to_store,
        migrate_sqlite_to_mysql,
        _resolve_engine,
    )

    engine = _resolve_engine(db_cfg)
    sqlite_path = Path(str(db_cfg.get("path") or "data/app.db"))
    if not sqlite_path.is_absolute():
        sqlite_path = ROOT / sqlite_path

    if engine == "mysql" and sqlite_path.exists():
        print(f"  从 SQLite 导入: {sqlite_path}")
        try:
            result = migrate_sqlite_to_mysql(sqlite_path, db_cfg)
            for name, ok in result.items():
                print(f"  {name}: {'已从 SQLite 导入' if ok else '导入失败'}")
        except Exception as e:
            print(f"  SQLite 导入失败: {e}", file=sys.stderr)

    ensure_database_bootstrapped()
    store = get_store()
    if not store:
        print("数据库初始化失败", file=sys.stderr)
        return 1
    result = migrate_json_files_to_store(store)
    for name, ok in result.items():
        print(f"  {name}: {'已从 JSON 导入' if ok else '跳过（库中已有或文件不存在）'}")
    cache_result = migrate_file_caches_to_store(store)
    for name, ok in cache_result.items():
        print(f"  {name}: {'已从缓存文件导入' if ok else '跳过（库中已有或文件不存在）'}")
    print(f"\n数据库: {store.label}")
    print("请重启 web_server.py。旧 JSON 可备份后删除，勿再与数据库同时写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
