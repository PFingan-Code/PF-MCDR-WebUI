"""SQLite-backed operation audit storage with legacy binary import support."""

from __future__ import annotations

import json
import json
import struct
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from guguwebui.utils.storage import connect
from guguwebui.constant import AUDIT_LOG_PATH as _AUDIT_CONST
from guguwebui.utils.audit_actor import account_snapshot_from_user

AUDIT_LOG_PATH = _AUDIT_CONST

_LOCK = threading.Lock()
# detail 中单字段字符串最大长度，防止异常大对象
_MAX_DETAIL_STR = 8000


def _truncate_detail(detail: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not detail:
        return None
    out: Dict[str, Any] = {}
    for k, v in detail.items():
        if isinstance(v, str) and len(v) > _MAX_DETAIL_STR:
            out[k] = v[:_MAX_DETAIL_STR] + "…"
        else:
            out[k] = v
    return out


def _ensure_parent() -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_record(record: Dict[str, Any]) -> None:
    if "id" not in record:
        record["id"] = str(uuid.uuid4())
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > 4 * 1024 * 1024:
        record = {
            "id": record.get("id"),
            "ts": record.get("ts"),
            "operation_type": record.get("operation_type", "overflow"),
            "summary": (record.get("summary") or "")[:500],
            "detail": {"error": "record too large, omitted"},
            "account": record.get("account"),
        }
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with _LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO audit_records "
                "(id,ts,operation_type,summary,detail_json,account_json) VALUES(?,?,?,?,?,?)",
                (
                    record["id"], float(record.get("ts") or 0), record.get("operation_type"),
                    record.get("summary"), json.dumps(record.get("detail"), ensure_ascii=False),
                    json.dumps(record.get("account"), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def record_operation(
    user: dict,
    *,
    operation_type: str,
    summary: str,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """在业务成功路径调用：写入一条操作审计。"""
    rec = {
        "ts": time.time(),
        "operation_type": operation_type,
        "summary": summary,
        "detail": _truncate_detail(detail),
        "account": account_snapshot_from_user(user),
    }
    append_record(rec)


def _read_all_records_unlocked() -> List[Dict[str, Any]]:
    if not AUDIT_LOG_PATH.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with open(AUDIT_LOG_PATH, "rb") as f:
        data = f.read()
    offset = 0
    n = len(data)
    while offset + 4 <= n:
        (length,) = _UINT32_BE.unpack_from(data, offset)
        offset += 4
        if length > n - offset or length > 32 * 1024 * 1024:
            break
        chunk = data[offset : offset + length]
        offset += length
        try:
            out.append(json.loads(chunk.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out


def list_records(
    *,
    offset: int = 0,
    limit: int = 50,
    newest_first: bool = True,
) -> tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    conn = connect()
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0])
        order = "DESC" if newest_first else "ASC"
        rows = conn.execute(
            f"SELECT id,ts,operation_type,summary,detail_json,account_json "
            f"FROM audit_records ORDER BY ts {order} LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        page = []
        for row in rows:
            try:
                detail = json.loads(row[4]) if row[4] else None
            except json.JSONDecodeError:
                detail = None
            try:
                account = json.loads(row[5]) if row[5] else None
            except json.JSONDecodeError:
                account = None
            page.append({"id": row[0], "ts": row[1], "operation_type": row[2],
                         "summary": row[3], "detail": detail, "account": account})
        return page, total
    finally:
        conn.close()
