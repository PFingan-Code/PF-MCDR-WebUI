"""从已认证用户上下文构造操作审计中的 account 快照（与鉴权语义一致）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from guguwebui.constant import user_db
from guguwebui.utils.auth_token import resolve_web_token


def account_snapshot_from_user(user: dict) -> Dict[str, Any]:
    """
    user 为 get_current_user / get_current_admin 的返回值。
    """
    if user.get("auth_via") == "panel_token":
        return {
            "username": "__panel__",
            "nickname": None,
            "auth_via": "panel_token",
        }
    username = user.get("username")
    nickname = None
    if username is not None:
        nickname = user_db.get("qq_nicknames", {}).get(str(username))
    return {
        "username": str(username) if username is not None else None,
        "nickname": nickname,
        "auth_via": "session",
    }


def account_snapshot_from_request_session(request) -> Optional[Dict[str, Any]]:
    """
    在无 Depends 上下文中仅从 session/cookie 尝试解析用户（同步）。
    若无法识别则返回 None。主要用于少数必须从 Request 取 session 的场景。
    """
    user = resolve_web_token(request.cookies.get("token"), purge_expired=False)
    if user is not None:
        username = user["username"]
        nickname = None
        if username is not None:
            nickname = user_db.get("qq_nicknames", {}).get(str(username))
        return {
            "username": str(username) if username is not None else None,
            "nickname": nickname,
            "auth_via": "session",
        }
    return None
