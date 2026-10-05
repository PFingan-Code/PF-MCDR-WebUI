import datetime
import ipaddress
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request, status

from guguwebui.constant import user_db
from guguwebui.structures import ForbiddenException

async def get_optional_user(request: Request) -> Optional[dict]:
    """解析 WebUI 登录态（cookie 会话或子服 Panel Token）；未登录返回 None。"""
    # 1) 常规 cookie 登录（保持现有逻辑）
    token = request.cookies.get("token")
    if token and token in user_db.get("token", {}) and request.session.get("logged_in"):
        return {"username": request.session.get("username"), "token": token}

    # 2) 子服模式：允许主服通过 X-Panel-Token 访问（不依赖 session/cookie）
    config_service = getattr(request.app.state, "config_service", None)
    if config_service is not None:
        server_config = config_service.get_config()
        if server_config.get("panel_role", "master") == "slave":
            panel_token = (request.headers.get("X-Panel-Token") or "").strip()
            if panel_token:
                panel_master = server_config.get("panel_master") or {}
                allowed_tokens = panel_master.get("allowed_tokens") or []
                allowed_master_ips = panel_master.get("allowed_master_ips") or []

                # 可选：限制主服来源 IP
                if allowed_master_ips:
                    client_ip = request.client.host if request.client else ""
                    if not _ip_allowed(client_ip, allowed_master_ips):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Panel token source not allowed",
                        )

                if _panel_token_enabled(panel_token, allowed_tokens):
                    return {"username": "__panel__", "token": panel_token, "auth_via": "panel_token"}

    return None


async def get_current_user(request: Request) -> dict:
    """获取当前登录用户，如果未登录则抛出 401 异常"""
    user = await get_optional_user(request)
    if user is not None:
        return user

    # token 不存在或 session 无效：清理 session（避免前端误以为已登录）
    token = request.cookies.get("token")
    if token and token not in user_db.get("token", {}):
        request.session.clear()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="User not logged in",
    )


def _panel_token_enabled(token: str, allowed_tokens: list) -> bool:
    for item in allowed_tokens or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("token", "")).strip() != token:
            continue
        return bool(item.get("enabled", False))
    return False


def _ip_allowed(client_ip: str, allowed_master_ips: list) -> bool:
    """支持精确 IP 或 CIDR 段。"""
    if not client_ip:
        return False
    try:
        ip_obj = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for raw in allowed_master_ips or []:
        if not isinstance(raw, str):
            continue
        raw = raw.strip()
        if not raw:
            continue
        try:
            if "/" in raw:
                net = ipaddress.ip_network(raw, strict=False)
                if ip_obj in net:
                    return True
            else:
                if ip_obj == ipaddress.ip_address(raw):
                    return True
        except ValueError:
            continue
    return False

def is_super_admin_user(request: Request, user: dict) -> bool:
    """判断用户是否为超级管理员（子服 Panel Token 视为放行，权限由主服侧控制）。

    把 mod_router 内联的 _is_super_admin / web_server._is_super_admin_user 收敛到此。
    """
    if user.get("auth_via") == "panel_token":
        return True
    config_service = getattr(request.app.state, "config_service", None)
    if config_service is None:
        return False
    config = config_service.get_config()
    return str(user.get("username")) == str(config.get("super_admin_account"))


async def get_current_admin(request: Request, current_user: dict = Depends(get_current_user)):
    """获取当前管理员用户，如果不是管理员则抛出 403 异常"""
    # 子服模式的面板 token：权限由主服控制，子服只校验 token 有效性
    if current_user.get("auth_via") == "panel_token":
        return current_user

    config_service = request.app.state.config_service
    server_config = config_service.get_config()
    
    super_admin_account = str(server_config.get("super_admin_account"))
    disable_other_admin = server_config.get("disable_other_admin", False)
    
    username = current_user.get("username")
    
    is_admin = True
    if disable_other_admin and str(username) != super_admin_account:
        is_admin = False
    
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
            
    return current_user


async def get_super_admin(request: Request, current_user: dict = Depends(get_current_admin)):
    """超级管理员依赖：非超级管理员（或 Panel Token）抛 403（统一错误体）。"""
    if not is_super_admin_user(request, current_user):
        raise ForbiddenException(
            "只有超级管理员可以执行该操作", code="super_admin_required"
        )
    return current_user


def resolve_chat_session(session_id: str) -> Optional[dict]:
    """校验公开聊天页会话：有效返回会话记录，否则返回 None。

    公开聊天页是匿名入口，只持有 chat_session_id（由 /chat/sessions 签发）。
    """
    if not session_id:
        return None
    sessions = user_db.get("chat_sessions") or {}
    if not isinstance(sessions, dict):
        return None
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        return None
    try:
        expire_time = datetime.datetime.fromisoformat(
            str(session.get("expire_time", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if datetime.datetime.now(datetime.timezone.utc) > expire_time:
        return None
    return session


async def get_current_user_or_chat_session(
    request: Request,
    session_id: Optional[str] = Query(
        None, description="公开聊天页会话 ID（匿名入口的访问凭证）"
    ),
    current_user: Optional[dict] = Depends(get_optional_user),
) -> dict:
    """WebUI 登录态，或公开聊天页的有效聊天会话。

    公开聊天页面向匿名访客，需要读取服务器状态、自身版本等信息；这些只读接口
    因此接受 chat_session_id 作为凭证。管理类接口仍只认 WebUI 登录态。
    """
    if current_user is not None:
        return current_user

    session = resolve_chat_session(session_id or "")
    if session is not None:
        return {
            "username": session.get("player_id"),
            "auth_via": "chat_session",
            "chat_session_id": session_id,
        }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="User not logged in",
    )
