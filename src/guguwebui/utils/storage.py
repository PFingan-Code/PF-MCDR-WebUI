"""Shared SQLite storage for WebUI-owned persistent data."""

from __future__ import annotations

import json
import sqlite3
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

DATA_DB_PATH = Path("config") / "guguwebui" / "guguwebui.sqlite3"

_LOCK = threading.RLock()

# 已完成建表的数据库文件（绝对路径）。建表脚本每个进程每个文件只执行一次；
# 文件被删除后会重新建表。
_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (
    namespace TEXT NOT NULL,
    state_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (namespace, state_key)
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    message TEXT NOT NULL,
    message_type INTEGER NOT NULL DEFAULT 0,
    rtext_json TEXT,
    player_uuid TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_id ON chat_messages(id);
CREATE TABLE IF NOT EXISTS minute_stats (
    ts INTEGER PRIMARY KEY,
    cpu_sys REAL, cpu_mc REAL, mem_percent REAL, mem_mc REAL,
    swap_percent REAL, disk_percent REAL, net_rx REAL, net_tx REAL,
    tps REAL, mspt REAL, load1 REAL, load5 REAL, load15 REAL
);
CREATE TABLE IF NOT EXISTS audit_records (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    operation_type TEXT,
    summary TEXT,
    detail_json TEXT,
    account_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_records_ts ON audit_records(ts);
CREATE TABLE IF NOT EXISTS storage_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL
);
"""


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def connect() -> sqlite3.Connection:
    db_path = DATA_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(db_path.absolute())
    # 必须在 connect 之前判断：sqlite3.connect 会创建空文件
    need_schema = key not in _SCHEMA_READY or not db_path.exists()
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    if need_schema:
        with _SCHEMA_LOCK:
            _apply_schema(conn)
            _SCHEMA_READY.add(key)
    return conn


def get_state(namespace: str, key: str, default: Any = None) -> Any:
    with _LOCK:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE namespace=? AND state_key=?",
                (namespace, key),
            ).fetchone()
            if row is None:
                return deepcopy(default)
            try:
                return json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                return deepcopy(default)
        finally:
            conn.close()


def set_state(namespace: str, key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with _LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO app_state(namespace,state_key,value_json) VALUES(?,?,?) "
                "ON CONFLICT(namespace,state_key) DO UPDATE SET value_json=excluded.value_json",
                (namespace, key, payload),
            )
            conn.commit()
        finally:
            conn.close()


def has_meta(key: str) -> bool:
    with _LOCK:
        conn = connect()
        try:
            return conn.execute("SELECT 1 FROM storage_meta WHERE meta_key=?", (key,)).fetchone() is not None
        finally:
            conn.close()


def set_meta(key: str, value: str = "1") -> None:
    with _LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO storage_meta(meta_key,meta_value) VALUES(?,?) "
                "ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SQLiteTable:
    """Small dict-compatible facade used by the existing account code.

    每个顶层键单独存一行（namespace = "<ns>.keys"，state_key = 顶层键），
    save() 只写入内容发生变化的键、删除已移除的键，不再整表重写。
    旧版布局（namespace = "<ns>"，state_key = "data" 的整块 JSON）在首次加载时
    迁移到新布局；旧行保留不删，便于回退旧版本。
    """

    _LEGACY_KEY = "data"

    def __init__(self, namespace: str, default_content: dict[str, Any] | None = None):
        self.namespace = namespace
        self.rows_namespace = f"{namespace}.keys"
        self.split_meta_key = f"split_layout:{namespace}"
        self.default_content = deepcopy(default_content or {})
        self.data: dict[str, Any] = {}
        # 已落库的各键序列化结果，用于 save() 时计算差异
        self._persisted: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        with _LOCK:
            conn = connect()
            try:
                split_done = conn.execute(
                    "SELECT 1 FROM storage_meta WHERE meta_key=?", (self.split_meta_key,)
                ).fetchone() is not None
                data: dict[str, Any] = {}
                persisted: dict[str, str] = {}
                need_migrate = False
                if split_done:
                    for row in conn.execute(
                        "SELECT state_key, value_json FROM app_state WHERE namespace=?",
                        (self.rows_namespace,),
                    ):
                        try:
                            data[row[0]] = json.loads(row[1])
                        except (TypeError, json.JSONDecodeError):
                            continue
                        persisted[row[0]] = row[1]
                else:
                    need_migrate = True
                    row = conn.execute(
                        "SELECT value_json FROM app_state WHERE namespace=? AND state_key=?",
                        (self.namespace, self._LEGACY_KEY),
                    ).fetchone()
                    legacy = None
                    if row is not None:
                        try:
                            legacy = json.loads(row[0])
                        except (TypeError, json.JSONDecodeError):
                            legacy = None
                    data = legacy if isinstance(legacy, dict) else deepcopy(self.default_content)
            finally:
                conn.close()
            self.data = data
            self._persisted = persisted
            if need_migrate:
                self.save()

    def save(self) -> None:
        with _LOCK:
            encoded = {str(k): _encode(v) for k, v in list(self.data.items())}
            changed = [(k, v) for k, v in encoded.items() if self._persisted.get(k) != v]
            removed = [k for k in self._persisted if k not in encoded]
            conn = connect()
            try:
                split_done = conn.execute(
                    "SELECT 1 FROM storage_meta WHERE meta_key=?", (self.split_meta_key,)
                ).fetchone() is not None
                if not changed and not removed and split_done:
                    return
                if changed:
                    conn.executemany(
                        "INSERT INTO app_state(namespace,state_key,value_json) VALUES(?,?,?) "
                        "ON CONFLICT(namespace,state_key) DO UPDATE SET value_json=excluded.value_json",
                        [(self.rows_namespace, k, v) for k, v in changed],
                    )
                if removed:
                    conn.executemany(
                        "DELETE FROM app_state WHERE namespace=? AND state_key=?",
                        [(self.rows_namespace, k) for k in removed],
                    )
                if not split_done:
                    conn.execute(
                        "INSERT OR IGNORE INTO storage_meta(meta_key,meta_value) VALUES(?, '1')",
                        (self.split_meta_key,),
                    )
                conn.commit()
            finally:
                conn.close()
            for k, v in changed:
                self._persisted[k] = v
            for k in removed:
                self._persisted.pop(k, None)

    async def save_async(self) -> None:
        self.save()

    def __getitem__(self, key: str):
        return self.data[key]

    def __setitem__(self, key: str, value: Any):
        self.data[key] = value
        self.save()

    def __contains__(self, key: str):
        return key in self.data

    def __delitem__(self, key: str):
        if key in self.data:
            del self.data[key]
            self.save()

    def __iter__(self):
        return iter(self.data.keys())

    def __repr__(self) -> str:
        return str(self.data)

    def __len__(self):
        return len(self.data)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def keys(self):
        return self.data.keys()

    def values(self):
        return self.data.values()

    def items(self):
        return self.data.items()
