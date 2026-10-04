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


def connect() -> sqlite3.Connection:
    DATA_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DATA_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
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


class SQLiteTable:
    """Small dict-compatible facade used by the existing account code."""

    def __init__(self, namespace: str, default_content: dict[str, Any] | None = None):
        self.namespace = namespace
        self.default_content = deepcopy(default_content or {})
        self.data = get_state(namespace, "data", self.default_content)
        if not isinstance(self.data, dict):
            self.data = deepcopy(self.default_content)
            self.save()

    def load(self) -> None:
        self.data = get_state(self.namespace, "data", self.default_content)

    def save(self) -> None:
        set_state(self.namespace, "data", self.data)

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
