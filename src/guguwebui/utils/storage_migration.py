"""One-time migration from legacy files and the old static directory."""

from __future__ import annotations

import json
import shutil
import sqlite3
import struct
import uuid
from pathlib import Path
from typing import Any

_NEW_ROOT = Path("config") / "guguwebui"
_NEW_STATIC = _NEW_ROOT / "guguwebui_static"
_DB_PATH = _NEW_ROOT / "guguwebui.sqlite3"
_OLD_STATIC = Path("guguwebui_static")

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


def _json_load(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError):
        return default


def _merge_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in list(source.iterdir()):
        destination = target / item.name
        if item.is_dir() and destination.is_dir():
            _merge_tree(item, destination)
        elif not destination.exists():
            shutil.move(str(item), str(destination))


def _merge_static_dir() -> None:
    _NEW_STATIC.parent.mkdir(parents=True, exist_ok=True)
    if not _OLD_STATIC.exists() or _OLD_STATIC.resolve() == _NEW_STATIC.resolve():
        _NEW_STATIC.mkdir(parents=True, exist_ok=True)
        return
    _merge_tree(_OLD_STATIC, _NEW_STATIC)
    try:
        _OLD_STATIC.rmdir()
    except OSError:
        pass


def _set_state(conn: sqlite3.Connection, namespace: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO app_state(namespace,state_key,value_json) VALUES(?,?,?) "
        "ON CONFLICT(namespace,state_key) DO NOTHING",
        (namespace, "data", json.dumps(value, ensure_ascii=False, separators=(",", ":"))),
    )


def _migrate_audit_bin(conn: sqlite3.Connection, path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    try:
        data = path.read_bytes()
        offset = 0
        while offset + 4 <= len(data):
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            offset += 4
            if length > len(data) - offset or length > 32 * 1024 * 1024:
                break
            try:
                record = json.loads(data[offset:offset + length].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                offset += length
                continue
            offset += length
            record_id = str(record.get("id") or uuid.uuid4())
            conn.execute(
                "INSERT OR IGNORE INTO audit_records "
                "(id,ts,operation_type,summary,detail_json,account_json) VALUES(?,?,?,?,?,?)",
                (
                    record_id,
                    float(record.get("ts") or 0),
                    record.get("operation_type"),
                    record.get("summary"),
                    json.dumps(record.get("detail"), ensure_ascii=False),
                    json.dumps(record.get("account"), ensure_ascii=False),
                ),
            )
            count += 1
    except OSError:
        return count
    return count


def ensure_storage_layout() -> None:
    """Create the new layout and import legacy WebUI-owned files once."""
    _merge_static_dir()
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        conn.executescript(_SCHEMA)
        migrated = conn.execute(
            "SELECT 1 FROM storage_meta WHERE meta_key='legacy_files_v1'"
        ).fetchone()
        if migrated:
            return

        db_path = _NEW_STATIC / "db.json"
        legacy_db = _json_load(db_path, None)
        if isinstance(legacy_db, dict):
            _set_state(conn, "user_db", legacy_db)

        stats_path = _NEW_STATIC / "player_stats.json"
        legacy_stats = _json_load(stats_path, None)
        if isinstance(legacy_stats, dict):
            _set_state(conn, "player_stats", legacy_stats)

        monitor_path = _NEW_STATIC / "monitor.db"
        if monitor_path.is_file():
            try:
                old = sqlite3.connect(str(monitor_path))
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS minute_stats (
                        ts INTEGER PRIMARY KEY,
                        cpu_sys REAL, cpu_mc REAL, mem_percent REAL, mem_mc REAL,
                        swap_percent REAL, disk_percent REAL, net_rx REAL, net_tx REAL,
                        tps REAL, mspt REAL, load1 REAL, load5 REAL, load15 REAL
                    )
                """)
                rows = old.execute(
                    "SELECT ts,cpu_sys,cpu_mc,mem_percent,mem_mc,swap_percent,disk_percent,"
                    "net_rx,net_tx,tps,mspt,load1,load5,load15 FROM minute_stats"
                ).fetchall()
                conn.executemany(
                    "INSERT OR IGNORE INTO minute_stats VALUES(" + ",".join("?" * 14) + ")",
                    rows,
                )
                old.close()
            except sqlite3.Error:
                pass

        _migrate_audit_bin(conn, _NEW_STATIC / "audit_log.bin")
        conn.execute(
            "INSERT INTO storage_meta(meta_key,meta_value) VALUES('legacy_files_v1','1')"
        )
        conn.commit()
    finally:
        conn.close()
