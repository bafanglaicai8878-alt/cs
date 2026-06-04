"""线上数据库：admin / cdk / client 数据集中存储，支持 SQLite 与 MySQL。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "app.db"

_store: Optional["DocumentStore"] = None
_lock = threading.RLock()
_bootstrapped = False
_log = logging.getLogger(__name__)

DOC_ADMIN = "admin"
DOC_CDK = "cdk"
DOC_CLIENT = "client"
DOC_CATALOG = "cache_catalog"
DOC_STEAM_CATALOG = "cache_steam_catalog"
DOC_SUDAMA = "cache_sudama"
DOC_CATALOG_SYNC = "catalog_sync_meta"
DOC_EXTENSIONS = "extensions"

CACHE_FILE_MAP = {
    DOC_CATALOG: ROOT / "catalog_cache.json",
    DOC_STEAM_CATALOG: ROOT / "steam_catalog_cache.json",
    DOC_SUDAMA: ROOT / "sudama_cache.json",
}


class DocumentStore(ABC):
    @abstractmethod
    def get(self, name: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def set(self, name: str, data: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def has(self, name: str) -> bool:
        ...

    @property
    @abstractmethod
    def label(self) -> str:
        ...


class SqliteDocumentStore(DocumentStore):
    """以文档块方式存储原 JSON 结构（SQLite）。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.RLock()
        self._init_schema()

    @property
    def label(self) -> str:
        return f"SQLite ({self.db_path})"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._local:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS documents (
                        name TEXT PRIMARY KEY,
                        data TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, name: str) -> Dict[str, Any]:
        with self._local:
            conn = self._connect()
            try:
                row = conn.execute("SELECT data FROM documents WHERE name = ?", (name,)).fetchone()
                if not row:
                    return {}
                return json.loads(row["data"])
            finally:
                conn.close()

    def set(self, name: str, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._local:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO documents (name, data, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                    """,
                    (name, payload, now),
                )
                conn.commit()
            finally:
                conn.close()

    def has(self, name: str) -> bool:
        with self._local:
            conn = self._connect()
            try:
                row = conn.execute("SELECT 1 FROM documents WHERE name = ?", (name,)).fetchone()
                return row is not None
            finally:
                conn.close()


class MySqlDocumentStore(DocumentStore):
    """以文档块方式存储原 JSON 结构（MySQL）。"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ):
        try:
            import pymysql
        except ImportError as e:
            raise RuntimeError("未安装 pymysql，请执行: pip install pymysql") from e

        self._pymysql = pymysql
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._local = threading.RLock()
        self._init_schema()

    @property
    def label(self) -> str:
        return f"MySQL ({self.user}@{self.host}:{self.port}/{self.database})"

    def _connect(self):
        return self._pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=15,
            read_timeout=30,
            write_timeout=30,
        )

    def _init_schema(self) -> None:
        with self._local:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS documents (
                            name VARCHAR(64) PRIMARY KEY,
                            data LONGTEXT NOT NULL,
                            updated_at DATETIME NOT NULL
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                conn.commit()
            finally:
                conn.close()

    def get(self, name: str) -> Dict[str, Any]:
        with self._local:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT data FROM documents WHERE name = %s", (name,))
                    row = cur.fetchone()
                if not row:
                    return {}
                return json.loads(row[0])
            finally:
                conn.close()

    def set(self, name: str, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._local:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO documents (name, data, updated_at) VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE data = VALUES(data), updated_at = VALUES(updated_at)
                        """,
                        (name, payload, now),
                    )
                conn.commit()
            finally:
                conn.close()

    def has(self, name: str) -> bool:
        with self._local:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM documents WHERE name = %s LIMIT 1", (name,))
                    row = cur.fetchone()
                return row is not None
            finally:
                conn.close()


# 兼容旧代码引用
SqlDocumentStore = SqliteDocumentStore


def _resolve_engine(db_cfg: Dict[str, Any]) -> str:
    engine = str(db_cfg.get("engine") or "").strip().lower()
    if engine in ("mysql", "mariadb"):
        return "mysql"
    path = str(db_cfg.get("path") or "").strip().lower()
    if path.startswith("mysql://") or path.startswith("mysql+pymysql://"):
        return "mysql"
    return "sqlite"


def _create_store(db_cfg: Dict[str, Any]) -> DocumentStore:
    if _resolve_engine(db_cfg) == "mysql":
        return MySqlDocumentStore(
            host=str(db_cfg.get("host") or "127.0.0.1"),
            port=int(db_cfg.get("port") or 3306),
            user=str(db_cfg.get("user") or ""),
            password=str(db_cfg.get("password") or ""),
            database=str(db_cfg.get("database") or db_cfg.get("name") or ""),
        )
    path = str(db_cfg.get("path") or DEFAULT_DB_PATH).strip()
    if path.startswith("sqlite:///"):
        path = path.replace("sqlite:///", "", 1)
    db_path = Path(path)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return SqliteDocumentStore(db_path)


def is_database_enabled() -> bool:
    return _store is not None


def get_store() -> Optional[DocumentStore]:
    return _store


def get_database_label() -> str:
    if _store:
        return _store.label
    return "未启用"


def init_database(config: Optional[Dict[str, Any]] = None) -> bool:
    """根据 config.json 的 Database 段初始化；成功返回 True。"""
    global _store
    cfg = config or {}
    db_cfg = cfg.get("Database") or {}
    if not db_cfg.get("enabled"):
        _store = None
        return False
    try:
        store = _create_store(db_cfg)
    except Exception:
        _store = None
        raise
    with _lock:
        _store = store
    return True


def ensure_database_bootstrapped() -> bool:
    """任意入口（Web / 盒子 / 命令行）首次创建 Service 前调用。"""
    global _bootstrapped
    if _bootstrapped:
        return is_database_enabled()
    _bootstrapped = True
    cfg: Dict[str, Any] = {}
    config_path = ROOT / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    db_cfg = cfg.setdefault("Database", {})
    if db_cfg.get("enabled") is not False:
        db_cfg["enabled"] = True
    if _resolve_engine(db_cfg) == "sqlite":
        db_cfg.setdefault("path", "data/app.db")
    if not init_database(cfg):
        return False
    try:
        migrate_json_files_to_store()
        migrate_file_caches_to_store()
    except Exception as e:
        _log.warning("数据库迁移失败（服务仍可能继续运行）: %s", e, exc_info=True)
    return True

def read_json_cache(doc_name: str, file_path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 JSON 缓存：优先 MySQL，否则读本地文件。"""
    store = get_store()
    if store:
        data = store.get(doc_name)
        return data if data else {}
    path = file_path or CACHE_FILE_MAP.get(doc_name)
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_json_cache(doc_name: str, data: Dict[str, Any], file_path: Optional[Path] = None) -> None:
    """写入 JSON 缓存：优先 MySQL，否则写本地文件。"""
    store = get_store()
    if store:
        store.set(doc_name, data)
        return
    path = file_path or CACHE_FILE_MAP.get(doc_name)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def migrate_file_caches_to_store(store: Optional[DocumentStore] = None) -> Dict[str, bool]:
    """将本地缓存 JSON 导入数据库（仅当库中尚无该文档时）。"""
    store = store or get_store()
    if not store:
        return {}
    result: Dict[str, bool] = {}
    for doc_name, json_path in CACHE_FILE_MAP.items():
        if store.has(doc_name):
            result[doc_name] = False
            continue
        if not json_path.exists():
            result[doc_name] = False
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            store.set(doc_name, data)
            result[doc_name] = True
        except Exception:
            result[doc_name] = False
    return result


def migrate_json_files_to_store(store: Optional[DocumentStore] = None) -> Dict[str, bool]:
    """将现有 JSON 库导入数据库（仅当库中尚无该文档时导入）。"""
    store = store or get_store()
    if not store:
        raise RuntimeError("数据库未启用")
    mapping = {
        DOC_ADMIN: ROOT / "admin_db.json",
        DOC_CDK: ROOT / "cdk_db.json",
        DOC_CLIENT: ROOT / "client_db.json",
    }
    result: Dict[str, bool] = {}
    for doc_name, json_path in mapping.items():
        if store.has(doc_name):
            result[doc_name] = False
            continue
        if not json_path.exists():
            result[doc_name] = False
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            store.set(doc_name, data)
            result[doc_name] = True
        except Exception:
            result[doc_name] = False
    return result


# 兼容旧函数名
migrate_json_files_to_sqlite = migrate_json_files_to_store


def migrate_sqlite_to_mysql(
    sqlite_path: Union[str, Path] = DEFAULT_DB_PATH,
    mysql_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """将 SQLite 中的 documents 一次性导入 MySQL。"""
    src_path = Path(sqlite_path)
    if not src_path.is_absolute():
        src_path = ROOT / src_path
    if not src_path.exists():
        raise FileNotFoundError(f"SQLite 文件不存在: {src_path}")

    src = SqliteDocumentStore(src_path)
    cfg = mysql_cfg or {}
    dst = MySqlDocumentStore(
        host=str(cfg.get("host") or "127.0.0.1"),
        port=int(cfg.get("port") or 3306),
        user=str(cfg.get("user") or ""),
        password=str(cfg.get("password") or ""),
        database=str(cfg.get("database") or cfg.get("name") or ""),
    )

    conn = sqlite3.connect(str(src_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT name, data FROM documents").fetchall()
    finally:
        conn.close()

    result: Dict[str, bool] = {}
    for row in rows:
        name = row["name"]
        try:
            data = json.loads(row["data"])
            dst.set(name, data)
            result[name] = True
        except Exception:
            result[name] = False
    return result
