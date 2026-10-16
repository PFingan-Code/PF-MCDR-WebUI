import asyncio
import datetime
import inspect
import json
import logging
import secrets
import uuid
from pathlib import Path
from typing import Any, Optional

import aiohttp
from fastapi import (Body, Depends, FastAPI, Form, HTTPException, Request,
                     status)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)
from mcdreforged.api.event import MCDRPluginEvents
from mcdreforged.api.types import PluginServerInterface
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

import guguwebui.state as gugu_state
from guguwebui.constant import *
from guguwebui.dependencies.auth import (get_current_admin, get_current_user,
                                         is_super_admin_user)
from guguwebui.panel_merge.proxy import ApiProxyDispatchMiddleware
from guguwebui.panel_merge.routes import router as panel_merge_router
from guguwebui.PIM import initialize_pim
from guguwebui.routers.audit_router import router as audit_router
from guguwebui.routers.backup_router import router as backup_router
from guguwebui.routers.chat_router import router as chat_router
from guguwebui.routers.config_router import router as config_router
from guguwebui.routers.pim_router import router as pim_router
from guguwebui.routers.monitor_router import router as monitor_router
from guguwebui.routers.mod_router import router as mod_router
from guguwebui.routers.pip_router import router as pip_router
from guguwebui.routers.plugin_management_router import \
    router as plugin_management_router
from guguwebui.routers.plugin_proxy_router import router as plugin_proxy_router
from guguwebui.routers.player_router import router as player_router
from guguwebui.routers.server_router import router as server_router
from guguwebui.services.monitor_service import MonitorService
from guguwebui.services.mod_service import ModService
from guguwebui.services.ai_service import AIService
from guguwebui.services.auth_service import AuthService
from guguwebui.services.chat_service import ChatService
from guguwebui.services.backup_service import BackupService
from guguwebui.services.config_service import ConfigService
from guguwebui.services.file_service import FileService
from guguwebui.services.pip_service import PipService
from guguwebui.services.player_service import PlayerService
from guguwebui.services.plugin_service import PluginService
from guguwebui.services.qq_qr_login_service import QQQRCodeLoginService
from guguwebui.services.server_service import ServerService
from guguwebui.state import (RCON_ONLINE_CACHE, PluginApiHandlerParams,
                             bind_plugin_pages_registry_to_server, pip_tasks)
from guguwebui.structures import (BusinessException, ConfigData, DeepseekQuery,
                                  PimInstallRequest, PimUninstallRequest,
                                  PipPackageRequest, PluginInfo, SaveConfig,
                                  SaveContent, ToggleConfig, TotpDisableRequest,
                                  TotpEnableRequest, TotpSetupRequest)
from guguwebui.structures.envelope import ApiSuccessEnvelope
from guguwebui.structures.envelope import error as api_error
from guguwebui.structures.envelope import success as api_success
from guguwebui.services.operation_audit_service import record_operation
from guguwebui.utils import totp
from guguwebui.utils.auth_token import revoke_user_tokens
from guguwebui.utils.auth_util import migrate_old_config, verify_password
from guguwebui.utils.rate_limit import (hit_limited, login_retry_after,
                                        record_login_failure)
from guguwebui.utils.request_security import get_client_ip, origin_allowed
from guguwebui.utils.log_watcher import LogWatcher
from guguwebui.utils.mc_util import get_plugin_version
from guguwebui.utils.server_util import *

# 获取插件真实版本号
app = FastAPI(
    title="GUGU WebUI",
    description="MCDR WebUI 管理界面",
    version=get_plugin_version(),
    # 文档端点改为登录后可见（见下方 /docs、/redoc、/openapi.json），避免向公网暴露完整接口清单
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# 导出 get_plugins_info 供 __init__.py 使用
__all__ = [
    "app",
    "init_app",
    "log_watcher",
    "DEFALUT_CONFIG",
    "STATIC_PATH",
    "ThreadedUvicorn",
]

# URL路径处理函数已移至 utils.py

def _get_static_index_path() -> Path:
    server = getattr(app.state, "server_interface", None)
    return get_static_path(server) / "static" / "index.html"


def serve_spa_index(request: Request) -> HTMLResponse:
    """返回 SPA 的 index.html 文件，并注入配置"""
    static_index_path = _get_static_index_path()
    if not static_index_path.exists():
        return HTMLResponse(
            content="<h1>前端文件未找到</h1><p>请确保已构建前端项目（运行 npm run build）</p>",
            status_code=404,
        )
    with open(static_index_path, "r", encoding="utf-8") as f:
        content = f.read()
    root_path = request.scope.get("root_path", "")
    if not root_path.endswith("/"):
        root_path += "/"
    config_script = f'<script>window.__GUGU_CONFIG__ = {json.dumps({"root_path": root_path.rstrip("/")})};</script>'
    overall_css_link = f'<link rel="stylesheet" href="{root_path}custom/overall.css">'
    overall_js_script = f'<script defer src="{root_path}custom/overall.js"></script>'
    content = content.replace(
        "<head>",
        f'<head><base href="{root_path}">{config_script}{overall_css_link}{overall_js_script}',
    )
    return HTMLResponse(content=content)


# 全局 LogWatcher 实例
log_watcher = None

# ============================================================#
# HTTP client session (for proxy)
@app.on_event("startup")
async def _startup_http_session():
    if not hasattr(app.state, "http_session") or app.state.http_session is None:
        timeout = aiohttp.ClientTimeout(total=30)
        app.state.http_session = aiohttp.ClientSession(timeout=timeout)


@app.on_event("shutdown")
async def _shutdown_http_session():
    session = getattr(app.state, "http_session", None)
    if session is not None:
        try:
            await session.close()
        except Exception:
            pass
        app.state.http_session = None


# Multi-server panel merge logic has been moved to guguwebui.panel_merge.*

# 尝试迁移旧配置
migrate_old_config()


# 初始化函数，在应用程序启动时调用
def init_app(server_instance):
    """初始化应用程序，注册事件监听器"""
    global log_watcher

    # 注入 FastAPI app 到 state，供 PIM installer 等模块调度异步任务
    import guguwebui.state as state_module
    state_module.app = app

    # 存储服务器接口
    app.state.server_interface = server_instance

    # 插件网页注册表挂在 MCDR server 上，避免仅重载 guguwebui 时丢失其它插件的注册
    bind_plugin_pages_registry_to_server(server_instance)

    # 初始化自更新信息
    app.state.self_update_info = {"available": False}

    # 确保user_db包含所有必要的键
    try:
        from guguwebui.constant import DEFALUT_DB, user_db

        # 检查并添加缺失的键
        for key in DEFALUT_DB:
            if key not in user_db:
                user_db[key] = DEFALUT_DB[key]
                server_instance.logger.debug(f"添加缺失的数据库键: {key}")
        user_db.save()
        server_instance.logger.debug("数据库结构已更新")
    except Exception as e:
        server_instance.logger.error(f"更新数据库结构时出错: {e}")

    # 清理现有监听器，避免重复注册
    if log_watcher:
        log_watcher.stop()

    server_config = server_instance.load_config_simple(
        "config.json", DEFALUT_CONFIG, echo_in_console=False
    )
    log_capture_compat_mode = server_config.get("log_capture_compat_mode", True)

    # 初始化 LogWatcher，根据配置选择捕获模式
    log_watcher = LogWatcher(
        server_interface=server_instance,
        compat_mode=log_capture_compat_mode,
    )

    if not log_capture_compat_mode:
        # 标准模式：连接 MCDR 内部日志系统
        log_watcher._setup_log_capture()

    # 仅标准模式注册事件监听，兼容模式通过读文件获取日志
    # 双事件统一交给 on_server_output，由 LogWatcher 做去重与内容兜底提取：
    # - GENERAL_INFO 覆盖面更广（含服务端输出）
    # - USER_INFO 在部分环境下可补充用户来源信息
    if not log_capture_compat_mode:
        server_instance.register_event_listener(
            MCDRPluginEvents.GENERAL_INFO, on_server_output
        )
        server_instance.register_event_listener(
            MCDRPluginEvents.USER_INFO, on_server_output
        )
    # 注册玩家进出事件，刷新RCON在线缓存
    server_instance.register_event_listener(
        MCDRPluginEvents.PLAYER_JOINED, on_player_joined
    )
    server_instance.register_event_listener(
        MCDRPluginEvents.PLAYER_LEFT, on_player_left
    )
    server_instance.register_event_listener(
        MCDRPluginEvents.SERVER_STOP, on_server_stop
    )
    server_instance.register_event_listener(
        MCDRPluginEvents.SERVER_START_PRE, on_server_start_pre
    )

    # 初始化服务
    app.state.auth_service = AuthService(server_instance)
    app.state.config_service = ConfigService(server_instance)
    app.state.server_service = ServerService(server_instance, log_watcher)
    app.state.backup_service = BackupService(server_instance, app.state.server_service)
    app.state.ai_service = AIService(server_instance, app.state.config_service)
    app.state.pip_service = PipService(server_instance)
    app.state.file_service = FileService(server_instance)
    app.state.chat_service = ChatService(server_instance, app.state.config_service)
    app.state.player_service = PlayerService(server_instance, app.state.config_service)
    app.state.mod_service = ModService(server_instance, app.state.config_service)
    app.state.mod_service.install_start_guard()

    # 初始化服务器状态监控（先停止旧实例，避免重复启动）
    try:
        old_monitor = getattr(app.state, "monitor_service", None)
        if old_monitor is not None:
            old_monitor.stop()
        app.state.monitor_service = MonitorService(server_instance)
        app.state.monitor_service.start()
        server_instance.logger.debug("服务器状态监控已启动")
    except Exception as e:
        server_instance.logger.error(f"服务器状态监控初始化失败: {e}")

    # 初始化PIM模块
    try:
        server_instance.logger.debug("正在初始化内置PIM模块...")
        pim_helper, plugin_installer = initialize_pim(server_instance)
        # 将初始化后的PIM实例存储到app.state中，供API调用
        app.state.pim_helper = pim_helper
        app.state.plugin_installer = plugin_installer

        # 初始化插件服务
        app.state.plugin_service = PluginService(
            server_instance, pim_helper, plugin_installer
        )

        # 注入服务依赖
        app.state.auth_service.config_service = app.state.config_service
        app.state.plugin_service.config_service = app.state.config_service
        app.state.server_service.config_service = app.state.config_service

        if pim_helper and plugin_installer:
            server_instance.logger.info("内置PIM模块初始化成功")
        else:
            server_instance.logger.warning(
                "内置PIM模块初始化部分失败，某些功能可能不可用"
            )

        # 在后台线程预热插件仓库缓存（P3：避免阻塞 MCDR 插件加载/服务启动流程）
        from .utils.file_util import check_repository_cache

        try:
            import threading

            threading.Thread(
                target=check_repository_cache, args=(server_instance,), daemon=True
            ).start()
        except Exception as e:
            server_instance.logger.error(f"启动仓库缓存预热线程失败: {e}")
    except Exception as e:
        server_instance.logger.error(f"内置PIM模块初始化失败: {e}")

    if log_capture_compat_mode:
        server_instance.logger.info("WebUI 日志捕获兼容模式已启用，将通过读取日志文件获取日志")
    else:
        server_instance.logger.debug("WebUI 日志捕获器已初始化，将直接从 MCDR 捕获日志")


# check_repository_cache 函数已移至 utils.py


# 事件处理函数
def on_player_joined(server, player: str, info=None):
    """处理玩家加入事件"""
    try:
        RCON_ONLINE_CACHE["dirty"] = True
    except Exception:
        pass
    try:
        player_service = getattr(app.state, "player_service", None)
        if player_service is not None:
            player_service.on_player_joined(server, player, info)
    except Exception as e:
        server.logger.debug(f"记录玩家上线数据失败: {e}")


def on_player_left(server, player: str):
    """处理玩家离开事件"""
    try:
        RCON_ONLINE_CACHE["dirty"] = True
    except Exception:
        pass
    try:
        player_service = getattr(app.state, "player_service", None)
        if player_service is not None:
            player_service.on_player_left(server, player)
    except Exception as e:
        server.logger.debug(f"记录玩家下线数据失败: {e}")


def on_server_output(server, info):
    """处理服务器输出事件"""
    global log_watcher
    if log_watcher:
        log_watcher.on_server_output(server, info)


def on_server_stop(server, return_code=None):
    """服务器停止后执行运行期间排队的模组禁用操作。

    MCDR 的 SERVER_STOP 事件会额外传入服务端进程返回码；保留默认值以
    兼容旧版 MCDR 或测试代码直接调用该回调的情况。
    """
    try:
        mod_service = getattr(app.state, "mod_service", None)
        if mod_service is None:
            return
        result = mod_service.apply_pending_operations()
        if result.get("applied"):
            server.logger.info(f"已完成待处理模组操作: {', '.join(result['applied'])}")
        for error in result.get("errors", []):
            server.logger.error(f"待处理模组操作失败 ({error.get('filename')}): {error.get('message')}")
    except Exception as exc:
        server.logger.error(f"处理待禁用模组时发生错误: {exc}")


def on_server_start_pre(server):
    """启动前同步兜底处理队列，覆盖重启和其它插件直接启动的场景。"""
    try:
        mod_service = getattr(app.state, "mod_service", None)
        if mod_service is None or not mod_service.has_pending_operations():
            return
        result = mod_service.apply_pending_operations()
        if result.get("remaining"):
            server.logger.error("待处理模组操作尚未完成，本次启动将被拦截")
    except Exception as exc:
        server.logger.error(f"启动前处理待操作模组时发生错误: {exc}")


def on_mcdr_info(server, info):
    """处理MCDR信息事件"""
    global log_watcher
    # 增加调试日志
    server.logger.debug(f"收到 MCDR 日志事件: {getattr(info, 'content', '')[:50]}...")
    if log_watcher:
        log_watcher.on_mcdr_info(server, info)
    else:
        server.logger.warning("LogWatcher 尚未初始化，无法记录日志")


# ============================================================#
#
# 认证与页面路由


# redirect to log in
@app.get("/", name="root")
def read_root(request: Request):
    return RedirectResponse(url=get_redirect_url(request, "/login"))


# login page - 使用 React SPA
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    auth_service: AuthService = request.app.state.auth_service
    if await auth_service.check_session_valid(request):
        return RedirectResponse(
            url=get_redirect_url(request, "/index"), status_code=status.HTTP_302_FOUND
        )
    return serve_spa_index(request)


# login request
@app.post("/api/login")
async def login(
    request: Request,
    account: str = Form(""),
    password: str = Form(""),
    temp_code: str = Form(""),
    remember: bool = Form(False),
):
    return await request.app.state.auth_service.login(
        request, account, password, temp_code, remember
    )


@app.post("/api/login/qq_qr/start")
async def qq_qr_login_start(request: Request):
    """
    启动 QQ 扫码登录。
    返回：
      - code：轮询用的 code
      - qrUrl：二维码内容地址（前端据此生成二维码）
    """
    if not _qq_qr_enabled(request):
        return _qq_qr_disabled_response()
    wait = hit_limited("qq_qr_start", get_client_ip(request), 10, 300)
    if wait > 0:
        return JSONResponse(
            api_error(f"操作过于频繁，请 {wait} 秒后再试。", code="too_many_attempts", data={"retry_after": wait}),
            status_code=429,
            headers={"Retry-After": str(wait)},
        )
    qr = await asyncio.to_thread(QQQRCodeLoginService.request_login_code)
    code = qr.get("code", "")
    # 扫码 code 绑定到发起它的浏览器会话（签名 session cookie）：
    # 其他地方取得的 code（含直接向 q.qq.com 申请的）不能在本站兑换登录态
    codes = [c for c in (request.session.get(_QQ_QR_SESSION_KEY) or []) if isinstance(c, str)]
    codes = (codes + [code])[-_QQ_QR_MAX_PENDING:]
    request.session[_QQ_QR_SESSION_KEY] = codes
    return JSONResponse(
        {
            "status": "success",
            "code": code,
            "qrUrl": qr.get("qrUrl", ""),
        }
    )


@app.get("/api/login/qq_qr/status")
async def qq_qr_login_status(request: Request, code: str = ""):
    """
    轮询 QQ 扫码登录状态；成功后会直接在本次响应中写入 token cookie。
    """
    if not _qq_qr_enabled(request):
        return _qq_qr_disabled_response()
    if not code:
        return JSONResponse(
            {"status": "error", "message": "Missing qq login code。"},
            status_code=400,
        )
    pending_codes = request.session.get(_QQ_QR_SESSION_KEY) or []
    if code not in pending_codes:
        return JSONResponse(
            api_error("扫码会话无效，请在本页面重新开始扫码登录。", code="qq_qr_session_mismatch"),
            status_code=403,
        )

    result = await asyncio.to_thread(QQQRCodeLoginService.query_status, code)
    state = str(result.get("state", "error"))
    if state != "wait":
        request.session[_QQ_QR_SESSION_KEY] = [c for c in pending_codes if c != code]

    if state == "wait":
        return JSONResponse(
            {
                "status": "success",
                "state": "wait",
                "message": "正在等待扫码登录...",
            }
        )

    if state == "used":
        return JSONResponse(
            {
                "status": "success",
                "state": "used",
                "message": "二维码已过期，请重新开始扫码登录。",
            }
        )

    if state == "ok":
        uin = str(result.get("uin", "") or "")
        if not uin:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "扫码成功，但未获取到 QQ uin。",
                },
                status_code=500,
            )
        return await request.app.state.auth_service.login_with_account(
            request=request,
            account=uin,
            remember=False,
            state="ok",
        )

    return JSONResponse(
        {
            "status": "success",
            "state": "error",
            "message": f"扫码状态查询失败：{result.get('msg', '')}",
        },
        status_code=200,
    )


_QQ_QR_SESSION_KEY = "qq_qr_codes"
_QQ_QR_MAX_PENDING = 3


def _qq_qr_enabled(request: Request) -> bool:
    return bool(request.app.state.config_service.get_config().get("qq_qr_login_enabled", True))


def _qq_qr_disabled_response() -> JSONResponse:
    return JSONResponse(
        api_error("QQ 扫码登录已关闭。", code="qq_qr_disabled"),
        status_code=403,
    )


@app.get("/api/login/options")
async def login_options(request: Request):
    """登录页可用的登录方式（匿名可读，仅包含开关）。"""
    config = request.app.state.config_service.get_config()
    return JSONResponse(
        api_success(
            {
                "qq_qr_enabled": bool(config.get("qq_qr_login_enabled", True)),
                "temp_code_enabled": bool(config.get("allow_temp_password", True)),
            }
        )
    )


@app.post("/api/login/totp")
async def login_totp(
    request: Request,
    ticket: str = Form(""),
    code: str = Form(""),
):
    """两步验证第二步：账号密码 / 扫码通过后返回的 ticket + 验证器中的 6 位验证码。"""
    return await request.app.state.auth_service.complete_totp_login(request, ticket, code)


# ============================================================#
# 两步验证（TOTP）管理：只能操作当前登录账号自己的绑定


def _require_password_account(user: dict) -> str:
    """两步验证只对有密码的正式账号开放（排除临时码用户与子服 Panel Token）。"""
    username = str(user.get("username") or "")
    if user.get("auth_via") or username not in user_db.get("user", {}):
        raise BusinessException(
            "当前登录方式不支持两步验证，请使用账号密码登录后再设置。",
            status_code=400,
            code="totp_account_unsupported",
        )
    return username


def _check_account_password(request: Request, username: str, password: str) -> None:
    """敏感操作前复核密码；错误返回 400（避免前端把 401 当作登录失效而跳转）。"""
    client_ip = get_client_ip(request)
    wait = login_retry_after("web", client_ip, username)
    if wait > 0:
        raise BusinessException(
            f"尝试过于频繁，请 {wait} 秒后再试。",
            status_code=429,
            code="too_many_attempts",
            data={"retry_after": wait},
        )
    stored = user_db.get("user", {}).get(username)
    if not stored or not verify_password(password, stored):
        record_login_failure("web", client_ip, username)
        raise BusinessException("密码错误。", status_code=400, code="invalid_password")


@app.get("/api/auth/totp", response_model=ApiSuccessEnvelope)
async def api_totp_status(request: Request, user: dict = Depends(get_current_user)):
    username = str(user.get("username") or "")
    supported = not user.get("auth_via") and username in user_db.get("user", {})
    return JSONResponse(
        api_success({"supported": supported, "enabled": supported and totp.is_enabled(username)})
    )


@app.post("/api/auth/totp/setup", response_model=ApiSuccessEnvelope)
async def api_totp_setup(
    request: Request, body: TotpSetupRequest, user: dict = Depends(get_current_user)
):
    """生成待确认的密钥（需当前密码），返回 otpauth 链接供前端生成二维码。"""
    username = _require_password_account(user)
    _check_account_password(request, username, body.password)
    secret = totp.begin_setup(username)
    return JSONResponse(
        api_success(
            {
                "secret": secret,
                "otpauth_uri": totp.provisioning_uri(secret, username),
                "expires_in": totp.PENDING_SETUP_SECONDS,
            }
        )
    )


@app.post("/api/auth/totp/enable", response_model=ApiSuccessEnvelope)
async def api_totp_enable(
    request: Request, body: TotpEnableRequest, user: dict = Depends(get_current_user)
):
    """用验证码确认绑定；成功后注销该账号的其它登录会话。"""
    username = _require_password_account(user)
    if not totp.confirm_setup(username, body.code):
        raise BusinessException(
            "验证码错误或绑定已超时，请重新扫码后再试。", status_code=400, code="totp_invalid"
        )
    revoked = revoke_user_tokens(username, keep=user.get("token"))
    record_operation(
        user,
        operation_type="auth.totp_enable",
        summary="启用两步验证",
        detail={"revoked_sessions": revoked},
    )
    return JSONResponse(api_success({"revoked_sessions": revoked}, message="两步验证已启用"))


@app.post("/api/auth/totp/disable", response_model=ApiSuccessEnvelope)
async def api_totp_disable(
    request: Request, body: TotpDisableRequest, user: dict = Depends(get_current_user)
):
    username = _require_password_account(user)
    _check_account_password(request, username, body.password)
    if not totp.verify_code(username, body.code):
        record_login_failure("web", get_client_ip(request), username)
        raise BusinessException("验证码错误。", status_code=400, code="totp_invalid")
    totp.disable(username)
    record_operation(user, operation_type="auth.totp_disable", summary="关闭两步验证")
    return JSONResponse(api_success(message="两步验证已关闭"))


# logout Endpoints
@app.get("/logout", response_class=RedirectResponse)
async def logout(request: Request):
    """页面级登出"""
    response = RedirectResponse(
        url=get_redirect_url(request, "/login"), status_code=status.HTTP_302_FOUND
    )
    return await request.app.state.auth_service.logout(request, response)


@app.post("/api/logout", response_model=ApiSuccessEnvelope)
async def api_logout(request: Request):
    """API 形式的登出"""
    return await request.app.state.auth_service.logout(
        request, JSONResponse({"status": "success", "message": "Logged out"})
    )


class SessionTokenSyncMiddleware(BaseHTTPMiddleware):
    """确保 session 与 user_db 中的 token 一致：若 cookie 有 token 但 token 已不在 db（如 db 被清空），则清除 session。"""

    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get("token")
        if token and token not in user_db.get("token", {}):
            request.session.clear()
        return await call_next(request)


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """跨站请求防护：带 Origin 的写请求必须与站点同源（或在 allowed_origins 中）。

    SameSite=Lax 只区分“站点”（注册域），frp 等服务常给多个用户分配同一父域下的子域名，
    兄弟子域名之间互为同站，Lax 拦不住。浏览器发起的写请求都会带 Origin，据此校验；
    无 Origin 的请求（非浏览器客户端）放行，仍需正常鉴权。
    主服代理子服时携带 X-Panel-Token（浏览器跨站无法附加自定义头），不做此校验。
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in _UNSAFE_METHODS and not request.headers.get("x-panel-token"):
            origin = request.headers.get("origin")
            if origin is not None and not origin_allowed(request, origin):
                server = getattr(request.app.state, "server_interface", None)
                if server is not None:
                    server.logger.warning(
                        f"已拒绝跨站请求: {request.method} {request.url.path}，Origin={origin}，"
                        f"Host={request.headers.get('host')}。若这是你自己的访问地址（反向代理改写了 Host），"
                        "请把它加入配置项 allowed_origins"
                    )
                return JSONResponse(
                    api_error("请求来源不受信任（Origin 与站点不一致）", code="origin_mismatch"),
                    status_code=403,
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """基础安全响应头：禁止 MIME 嗅探、禁止被其它站点嵌入（点击劫持）、不向外站泄露来源路径。"""

    _HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "same-origin",
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in self._HEADERS.items():
            if name not in response.headers:
                response.headers[name] = value
        return response


app.add_middleware(SessionTokenSyncMiddleware)
app.add_middleware(ApiProxyDispatchMiddleware)
app.add_middleware(OriginCheckMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.include_router(panel_merge_router, prefix="/api") # 合并面板
app.include_router(plugin_management_router, prefix="/api") # 插件管理
app.include_router(config_router, prefix="/api") # 配置
app.include_router(server_router, prefix="/api") # 服务器
app.include_router(monitor_router, prefix="/api") # 服务器状态监控
app.include_router(plugin_proxy_router, prefix="/api") # 插件代理
app.include_router(pim_router, prefix="/api") # PIM
app.include_router(pip_router, prefix="/api") # PIP
app.include_router(chat_router, prefix="/api") # 聊天
app.include_router(player_router, prefix="/api") # 玩家管理
app.include_router(mod_router, prefix="/api") # Minecraft 模组管理
app.include_router(backup_router, prefix="/api") # 备份插件管理
app.include_router(audit_router, prefix="/api") # 操作审计


# ============================================================#
# Pages - 使用 React SPA
# API 文档：登录后可见
@app.get("/openapi.json", include_in_schema=False)
async def openapi_json(request: Request, user: dict = Depends(get_current_user)):
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
async def swagger_docs(request: Request, user: dict = Depends(get_current_user)):
    from fastapi.openapi.docs import get_swagger_ui_html

    root_path = request.scope.get("root_path", "")
    return get_swagger_ui_html(openapi_url=f"{root_path}/openapi.json", title=f"{app.title} - Docs")


@app.get("/redoc", include_in_schema=False)
async def redoc_docs(request: Request, user: dict = Depends(get_current_user)):
    from fastapi.openapi.docs import get_redoc_html

    root_path = request.scope.get("root_path", "")
    return get_redoc_html(openapi_url=f"{root_path}/openapi.json", title=f"{app.title} - ReDoc")


@app.get("/index", response_class=HTMLResponse)
async def read_index(request: Request, user: dict = Depends(get_current_user)):
    return serve_spa_index(request)


@app.get("/home", response_class=HTMLResponse)
async def read_home(request: Request, user: dict = Depends(get_current_user)):
    return serve_spa_index(request)


@app.get("/mc", response_class=HTMLResponse)
async def mc(request: Request, admin: dict = Depends(get_current_admin)):
    return serve_spa_index(request)


@app.get("/mcdr", response_class=HTMLResponse)
async def mcdr(request: Request, admin: dict = Depends(get_current_admin)):
    return serve_spa_index(request)


@app.get("/plugins", response_class=HTMLResponse)
async def plugins(request: Request, admin: dict = Depends(get_current_admin)):
    return serve_spa_index(request)


@app.get("/online-plugins", response_class=HTMLResponse)
async def online_plugins(request: Request, admin: dict = Depends(get_current_admin)):
    return serve_spa_index(request)


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request, admin: dict = Depends(get_current_admin)):
    return serve_spa_index(request)


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request, user: dict = Depends(get_current_user)):
    return serve_spa_index(request)

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    server_service: ServerService = request.app.state.server_service
    if not server_service.is_public_chat_enabled():
        return serve_spa_index(request)  # 返回 SPA，由前端处理 404
    return serve_spa_index(request)

@app.get("/player-chat", response_class=HTMLResponse)
async def player_chat_page(request: Request):
    server_service: ServerService = request.app.state.server_service
    if not server_service.is_public_chat_enabled():
        return serve_spa_index(request)  # 返回 SPA，由前端处理 404
    return serve_spa_index(request)


@app.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request, admin: dict = Depends(get_current_admin)):
    """提供终端日志页面 - 使用 React SPA"""
    return serve_spa_index(request)

@app.get("/operation-logs", response_class=HTMLResponse)
async def operation_logs_page(request: Request, admin: dict = Depends(get_current_admin)):
    """操作记录（只读）"""
    return serve_spa_index(request)


@app.get("/players", response_class=HTMLResponse)
async def players_page(request: Request, admin: dict = Depends(get_current_admin)):
    """玩家管理页面"""
    return serve_spa_index(request)


@app.get("/mods", response_class=HTMLResponse)
async def mods_page(request: Request, admin: dict = Depends(get_current_admin)):
    """Minecraft 模组管理页面"""
    return serve_spa_index(request)

# 404 page - 返回 SPA index.html，由前端处理 404
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            api_error("API endpoint not found", code="not_found"),
            status_code=404,
        )
    if request.url.path.startswith("/static/"):
        return JSONResponse(
            api_error("Static file not found", code="not_found"),
            status_code=404,
        )
    return serve_spa_index(request)


@app.exception_handler(ConnectionResetError)
async def connection_reset_handler(request: Request, exc: ConnectionResetError):
    # 在日志中记录错误，但向客户端返回友好消息
    app.state.server_interface.logger.warning(f"连接重置错误: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content=api_error("连接被重置，请刷新页面重试", code="connection_reset"),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # 如果是 401 或 403 且不是 API 请求，重定向到登录
    if exc.status_code in [401, 403] and "/api/" not in request.url.path:
        return RedirectResponse(
            url=get_redirect_url(request, "/login"), status_code=302
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=api_error(
            str(exc.detail), code=f"http_{exc.status_code}"
        ),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """参数校验错误（422）：统一为 status/code/message/data 错误体。"""
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = " → ".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = first.get("msg", "请求参数校验失败")
    if loc:
        msg = f"{loc}: {msg}"
    return JSONResponse(
        status_code=422,
        content=api_error(
            msg,
            code="validation_error",
            data={
                "errors": [
                    {
                        "loc": [str(x) for x in e.get("loc", [])],
                        "msg": e.get("msg"),
                        "type": e.get("type"),
                    }
                    for e in errors
                ]
            },
        ),
    )


@app.exception_handler(BusinessException)
async def business_exception_handler(request: Request, exc: BusinessException):
    """处理业务异常：统一错误体（status/message/code/data）。"""
    return JSONResponse(
        status_code=exc.status_code,
        content=api_error(exc.message, code=exc.code, data=exc.data),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_msg = f"未处理的异常: {str(exc)}"
    logger = getattr(app.state.server_interface, "logger", logging.getLogger(__name__))
    logger.error(error_msg, exc_info=True)
    return JSONResponse(
        status_code=500, content=api_error("服务器内部错误", code="internal_error")
    )


# ============================================================#


def _is_admin_user(request: Request, user: dict) -> bool:
    """与 get_current_admin 一致，不抛异常。"""
    if user.get("auth_via") == "panel_token":
        return True
    config_service = request.app.state.config_service
    server_config = config_service.get_config()
    super_admin_account = str(server_config.get("super_admin_account"))
    disable_other_admin = server_config.get("disable_other_admin", False)
    username = user.get("username")
    if disable_other_admin and str(username) != super_admin_account:
        return False
    return True


@app.get("/api/auth/me", response_model=ApiSuccessEnvelope)
async def api_auth_me(request: Request, user: dict = Depends(get_current_user)):
    """
    获取当前登录用户信息（/api/checkLogin → GET /api/auth/me）。
    未登录不返回 error 体，而是由依赖直接抛 401（统一错误体）。
    """
    username = user.get("username")
    nickname = None

    # 从 qq_nicknames 中获取昵称
    if username:
        try:
            from guguwebui.constant import user_db
            nickname = user_db.get("qq_nicknames", {}).get(str(username))
        except Exception:
            pass

    return JSONResponse(
        api_success(
            {
                "username": username,
                "nickname": nickname,
                "is_admin": _is_admin_user(request, user),
                "is_super_admin": is_super_admin_user(request, user),
            }
        )
    )

@app.post("/api/deepseek")
async def query_deepseek(
    request: Request,
    query_data: DeepseekQuery,
    admin: dict = Depends(get_current_admin),
):
    """向AI API发送问题并获取回答（上游响应原样放入统一外壳 data，不再叠包）"""
    ai_service: AIService = request.app.state.ai_service
    result = await ai_service.query(
        query_data.query,
        query_data.model,
        query_data.api_key,
        query_data.api_url,
        query_data.system_prompt,
    )
    return JSONResponse(api_success(result))


# ============================================================#
# 聊天页相关API端点
# ============================================================#


@app.post("/api/self_update", response_model=ApiSuccessEnvelope)
async def api_self_update(request: Request, admin: dict = Depends(get_current_admin)):
    """执行 WebUI 自身更新（去 success 双键，消息统一放 message）"""
    result = request.app.state.plugin_service.self_update()
    message = str(result.get("message") or "已发送更新指令到 MCDR")
    return JSONResponse(api_success(message=message))


@app.get("/api/self_update_info", response_model=ApiSuccessEnvelope)
async def api_get_self_update_info(
    request: Request, admin: dict = Depends(get_current_admin)
):
    """获取 WebUI 自身更新信息（统一成功外壳，负载移入 data）"""
    info = getattr(request.app.state, "self_update_info", {"available": False})
    return JSONResponse(api_success(info))

