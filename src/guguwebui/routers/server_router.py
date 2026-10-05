from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from guguwebui.dependencies.auth import (get_current_admin, get_current_user,
                                         get_current_user_or_chat_session)
from guguwebui.services.operation_audit_service import record_operation
from guguwebui.services.server_service import ServerService
from guguwebui.structures import BusinessException
from guguwebui.structures.envelope import ApiSuccessEnvelope, success

router = APIRouter(prefix="/server", tags=["server"])

SERVER_ACTIONS = {"start", "stop", "restart"}


class ServerControlRequest(BaseModel):
    """服务器控制请求（start / stop / restart）"""

    action: str = Field(..., description="控制动作: start | stop | restart")


class ServerCommandRequest(BaseModel):
    """发送到 MCDR 控制台的命令"""

    command: str = Field(..., max_length=2000, description="命令内容")


@router.get("/status", response_model=ApiSuccessEnvelope)
async def api_server_status(
    request: Request,
    _user: dict = Depends(get_current_user_or_chat_session),
):
    """获取服务器状态（{online, version, players}，status 双义已消除）

    公开聊天页为匿名入口，持有 chat_session_id 时同样可读（见 auth 依赖说明）。
    """
    data = await request.app.state.server_service.get_server_status()
    return success(data)


@router.post("/controls", response_model=ApiSuccessEnvelope)
async def api_control_server(
    request: Request,
    control_info: ServerControlRequest,
    admin: dict = Depends(get_current_admin),
):
    """控制Minecraft服务器（start/stop/restart）"""
    action = (control_info.action or "").strip().lower()
    if action not in SERVER_ACTIONS:
        raise BusinessException(
            f"Invalid action: {control_info.action}",
            status_code=400,
            code="invalid_action",
        )

    server_service: ServerService = request.app.state.server_service
    result = server_service.control_server(action)
    if result.get("status") != "success":
        raise BusinessException(
            result.get("message") or "服务器控制指令执行失败",
            status_code=400,
            code="control_failed",
        )

    record_operation(
        admin,
        operation_type="server.control",
        summary=f"服务器控制: {action}",
        detail={"action": action},
    )
    return success(message=result.get("message") or f"Server {action} command sent")


@router.get("/logs", response_model=ApiSuccessEnvelope)
async def api_server_logs(
    request: Request,
    cursor: int = Query(0, ge=0, description="游标（0 = 尾部快照；>0 = 该 counter 之后的新日志）"),
    max_lines: int = Query(100, ge=1, le=500, description="最大返回行数"),
    _user: dict = Depends(get_current_user),
):
    """获取服务器日志（统一 cursor 分页，替代 server_logs / new_logs）"""
    data = request.app.state.server_service.get_logs(cursor, max_lines)
    return success(data)


@router.get("/command-suggestions", response_model=ApiSuccessEnvelope)
async def api_get_command_suggestions(
    request: Request,
    input: str = Query("", max_length=200),
    _user: dict = Depends(get_current_user),
):
    """获取MCDR命令补全建议"""
    suggestions = await request.app.state.server_service.get_command_suggestions(input)
    return success({"suggestions": suggestions})


@router.post("/commands", response_model=ApiSuccessEnvelope)
async def api_send_command(
    request: Request,
    payload: ServerCommandRequest,
    admin: dict = Depends(get_current_admin),
):
    """发送命令到MCDR终端"""
    server_service: ServerService = request.app.state.server_service
    raw_cmd = (payload.command or "").strip()
    if not raw_cmd:
        raise BusinessException(
            "Command cannot be empty", status_code=400, code="invalid_command"
        )

    result = await server_service.send_command(raw_cmd)
    if result.get("status") != "success":
        message = result.get("message") or "命令执行失败"
        if message == "该命令已被禁止执行":
            raise BusinessException(message, status_code=403, code="forbidden_command")
        raise BusinessException(message, status_code=400, code="command_failed")

    record_operation(
        admin,
        operation_type="mcdr.send_command",
        summary="执行 MCDR 控制台命令",
        detail={"command_preview": raw_cmd[:500], "length": len(raw_cmd)},
    )
    feedback_keys = ("feedback", "capture", "timed_out", "note")
    data = {k: result[k] for k in feedback_keys if k in result}
    return success(
        data=data or None,
        message=result.get("message") or "Command sent",
    )


@router.get("/rcon-status", response_model=ApiSuccessEnvelope)
async def api_get_rcon_status(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """获取RCON连接状态"""
    data = await request.app.state.server_service.get_rcon_status()
    return success(data)


@router.post("/rcon-setup", response_model=ApiSuccessEnvelope)
async def api_setup_rcon(
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    """一键启用RCON配置（密码只写入配置文件，不回传）"""
    result = request.app.state.config_service.setup_rcon()
    if isinstance(result, dict) and result.get("status") == "success":
        record_operation(
            admin,
            operation_type="config.setup_rcon",
            summary="一键配置并启用 RCON",
            detail={},
        )
    return success(data=result.get("config"), message=result.get("message") or "RCON配置已成功启用")
