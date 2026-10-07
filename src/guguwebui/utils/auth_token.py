"""WebUI 登录 token 的解析与过期判断。

鉴权只信任服务端 user_db["token"] 中的记录：用户名取自 token 记录，
不从可被客户端持有的 session cookie 中读取。
"""

from __future__ import annotations

import datetime
from typing import Any, Optional


def parse_expire_time(value: Any) -> Optional[datetime.datetime]:
    """把存储的过期时间解析为带时区的 datetime；无法解析返回 None。

    存储格式为 str(datetime)（如 "2026-01-01 00:00:00.123456+00:00"），
    兼容 ISO 8601 与 "Z" 结尾；无时区信息时按 UTC 处理。
    """
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def is_expired(value: Any, now: Optional[datetime.datetime] = None) -> bool:
    """过期或无法解析均视为已过期（失败即拒绝）。"""
    expire_time = parse_expire_time(value)
    if expire_time is None:
        return True
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now >= expire_time


def resolve_web_token(token: Optional[str], *, purge_expired: bool = True) -> Optional[dict]:
    """校验 WebUI 登录 token，有效返回 {"username", "token"}，否则返回 None。

    过期 token 默认从 user_db 中清除。
    """
    if not token or not isinstance(token, str):
        return None

    from guguwebui.constant import user_db

    tokens = user_db.get("token") or {}
    if not isinstance(tokens, dict):
        return None
    record = tokens.get(token)
    if not isinstance(record, dict):
        return None
    username = record.get("user_name")
    if username is None or str(username) == "":
        return None
    if is_expired(record.get("expire_time")):
        if purge_expired and tokens.pop(token, None) is not None:
            try:
                user_db.save()
            except Exception:
                pass
        return None
    return {"username": str(username), "token": token}
