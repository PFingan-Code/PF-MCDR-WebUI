"""公开聊天 API：验证码、账号、会话与消息。

路径迁移：
- POST /chat/generate_code       → POST   /chat/verifications
- POST /chat/check_verification  → GET    /chat/verifications/{code}
- POST /chat/set_password        → PUT    /chat/accounts/{name}/password
- POST /chat/login               → POST   /chat/sessions
- POST /chat/check_session       → GET    /chat/session/{id}
- POST /chat/logout              → DELETE /chat/session/{id}
- POST /chat/get_messages        → GET    /chat/messages（limit/after_id/before_id query）
- POST /chat/get_new_messages    → GET    /chat/messages/incremental
- POST /chat/clear_messages      → DELETE /chat/messages（仅管理员）
- POST /chat/send_message        → POST   /chat/messages

错误不再按 message 关键字猜状态码（get_status_code_for_result 已删除），
统一抛 BusinessException 显式状态码 + 机器错误码。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from guguwebui.dependencies.auth import (get_chat_reader, get_current_admin,
                                         get_current_user)
from guguwebui.services.chat_service import ChatService
from guguwebui.structures import (
    BusinessException,
    ChatLoginRequest,
    ChatMessageCreateRequest,
    ChatSetPasswordRequest,
)
from guguwebui.structures.envelope import ApiSuccessEnvelope, PageEnvelope, success
from guguwebui.utils.rate_limit import (login_retry_after, record_login_failure,
                                        record_login_success)

router = APIRouter(tags=["chat"])

_CHAT_SCOPE = "chat"


def _get_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _ensure_not_limited(client_ip: str, account: Optional[str] = None) -> None:
    wait = login_retry_after(_CHAT_SCOPE, client_ip, account)
    if wait > 0:
        raise BusinessException(
            f"尝试过于频繁，请 {wait} 秒后再试",
            status_code=429,
            code="too_many_attempts",
            data={"retry_after": wait},
        )


@router.post("/chat/verifications", response_model=ApiSuccessEnvelope)
async def chat_generate_code(request: Request):
    """生成聊天页验证码（公开聊天页未启用 → 403 public_chat_disabled）"""
    code, expire_minutes = _get_service(request).generate_verification_code()
    return JSONResponse(success({"code": code, "expire_minutes": expire_minutes}))


@router.get("/chat/verifications/{code}", response_model=ApiSuccessEnvelope)
async def chat_check_verification(request: Request, code: str):
    """检查验证码验证状态：data.verified 为业务状态（不再复用 status）"""
    result = _get_service(request).check_verification_status(code)
    return JSONResponse(success(result))


@router.put("/chat/accounts/{name}/password", response_model=ApiSuccessEnvelope)
async def chat_set_password(
    request: Request,
    name: str,
    body: ChatSetPasswordRequest,
):
    """设置聊天页用户密码（name 必须与验证码绑定的玩家一致；成功后直接签发会话）"""
    client_ip = _client_ip(request)
    _ensure_not_limited(client_ip)
    try:
        result = await _get_service(request).set_user_password(
            body.code, body.password, player_id=name
        )
    except BusinessException as e:
        # 猜测验证码类失败计入 IP 级限制
        if e.code in ("verification_not_found", "verification_mismatch"):
            record_login_failure(_CHAT_SCOPE, client_ip)
        raise
    return JSONResponse(success(result, message=result.get("message")))


@router.post("/chat/sessions", response_model=ApiSuccessEnvelope)
async def chat_login(request: Request, body: ChatLoginRequest):
    """聊天页用户登录（创建会话；失败次数过多 → 429 too_many_attempts）"""
    client_ip = _client_ip(request)
    _ensure_not_limited(client_ip, body.player_id)
    try:
        result = await _get_service(request).login(
            body.player_id, body.password, client_ip
        )
    except BusinessException as e:
        if e.code in ("user_not_found", "invalid_password"):
            record_login_failure(_CHAT_SCOPE, client_ip, body.player_id)
        raise
    record_login_success(_CHAT_SCOPE, client_ip, body.player_id)
    return JSONResponse(success(result, message=result.get("message")))


@router.get("/chat/session/{session_id}", response_model=ApiSuccessEnvelope)
async def chat_check_session(request: Request, session_id: str):
    """检查聊天页会话状态（不存在 → 404 session_not_found，过期 → 401 session_expired）"""
    result = await _get_service(request).check_session(session_id)
    return JSONResponse(success(result))


@router.delete("/chat/session/{session_id}", response_model=ApiSuccessEnvelope)
async def chat_logout(request: Request, session_id: str):
    """聊天页用户退出登录"""
    result = _get_service(request).logout(session_id)
    return JSONResponse(success(message=result.get("message")))


@router.get("/chat/messages", response_model=PageEnvelope)
async def get_chat_messages(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    after_id: Optional[int] = Query(None, ge=0),
    before_id: Optional[int] = Query(None, ge=0),
    _reader: dict = Depends(get_chat_reader),
):
    """获取聊天消息（新→旧；after_id/before_id 为游标，与 offset 二选一）

    权限：WebUI 登录态；或公开聊天页启用时的有效聊天会话（query session_id）。
    """
    result = await _get_service(request).get_messages(
        limit=limit,
        offset=offset,
        after_id=after_id,
        before_id=before_id,
    )
    return JSONResponse(success(result))


@router.get("/chat/messages/incremental", response_model=ApiSuccessEnvelope)
async def get_new_chat_messages(
    request: Request,
    after_id: int = Query(0, ge=0),
    player_id: Optional[str] = None,
    reader: dict = Depends(get_chat_reader),
):
    """获取新消息与在线状态（轮询接口，基于最后消息 ID；player_id 作心跳）

    聊天会话访客的心跳固定记为会话绑定的玩家，不能替他人上报在线。
    """
    if reader.get("auth_via") == "chat_session":
        player_id = reader.get("username")
    result = await _get_service(request).get_new_messages(
        after_id=after_id, player_id_heartbeat=player_id
    )
    return JSONResponse(success(result))


@router.delete("/chat/messages", response_model=ApiSuccessEnvelope)
async def chat_clear_messages(
    request: Request,
    _admin: dict = Depends(get_current_admin),
):
    """清空聊天消息（仅管理员）"""
    result = _get_service(request).clear_messages()
    return JSONResponse(success(message=result.get("message")))


@router.post("/chat/messages", response_model=ApiSuccessEnvelope)
async def send_chat_message(request: Request, body: ChatMessageCreateRequest):
    """发送聊天消息到游戏（公开接口；WebUI 登录用户且用户名与 player_id 一致时走管理员通道）"""
    chat_service = _get_service(request)
    is_admin = False
    try:
        user = await get_current_user(request)
        is_admin = bool(user and user.get("username") == body.player_id)
    except HTTPException:
        is_admin = False
    result = await chat_service.send_message(
        body.message.strip(),
        body.player_id,
        body.session_id or "",
        is_admin,
    )
    return JSONResponse(success(message=result.get("message")))