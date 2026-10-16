from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

import aiohttp
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from guguwebui.dependencies.auth import get_current_admin, get_current_user
from guguwebui.services.config_service import ConfigService
from guguwebui.utils.request_security import get_client_ip


def get_target_server_id(request: Request) -> str:
    # header 优先，其次 query
    sid = (request.headers.get("X-Target-Server") or "").strip()
    if not sid:
        sid = (request.query_params.get("serverId") or "").strip()
    return sid or "local"


# 本地/代理路由归属语义收敛为单一清单（原 inline 手写排除清单）。
# - 登录/登出/会话/面板合并/审计等：必须由主服本地处理（cookie 建立在主服域、握手不跨服、审计只看主服）；
# - /api/plugins/online 大数据永远走本地，避免无意义传输；
# - /api/pairing/* 配对握手永远走本地（避免跨服代理导致握手混乱）。
# 约束由 tests/test_proxy_local.py 全量路由表扫描守护：当前路由表必须与清单完全一致，新增路由不会“漏配”也不会“误配”。
LOCAL_ONLY_PATHS: Tuple[str, ...] = (
    "/api/login",
    "/api/login/qq_qr/start",
    "/api/login/qq_qr/status",
    "/api/login/options",
    "/api/login/totp",
    "/api/logout",
    "/api/auth/me",
    "/api/servers",
    "/api/panel_merge_config",
    "/api/audit_logs",
    "/api/i18n/languages",
    "/api/plugins/online",
)

# 已下线路由但仍保留“不代理”兜底的旧路径（请求由主服本地 404，避免跨服转发）
LOCAL_ONLY_LEGACY_PATHS: Tuple[str, ...] = (
    "/api/online-plugins",
)

LOCAL_ONLY_PREFIXES: Tuple[str, ...] = (
    "/api/pairing/",
    "/api/auth/totp",  # 两步验证绑定属于主服本地账号
)


def is_proxy_candidate_path(path: str) -> bool:
    # 只代理 /api/*，并排除主服本地必须处理的少量端点
    if not path.startswith("/api/"):
        return False
    if path in LOCAL_ONLY_PATHS or path in LOCAL_ONLY_LEGACY_PATHS:
        return False
    if path.startswith(LOCAL_ONLY_PREFIXES):
        return False
    return True


def iter_api_routes(app) -> List[Tuple[Any, str]]:
    """遍历 app 上经 include_router 挂载的全部 APIRoute，返回 [(route, prefix)]。

    完整路径 = prefix + route.path。兼容两种 include_router 布局：
    - 旧版 fastapi/starlette：include 时即把 APIRoute 平铺进 app.routes，
      route.path 已含 /api 前缀，prefix 为空串；
    - 新版（_IncludedRouter 惰性包含）：app.routes 里是包装对象，真正的
      APIRoute 位于 original_router.routes，路径前缀记录在 include_context.prefix；
      请求匹配时需先把 prefix 从 scope["path"] 中剥掉，再用底层 route 匹配。
    路由级 prefix（如 APIRouter(prefix="/mods")）已烘焙进 route.path，无需处理。
    """
    from fastapi.routing import APIRoute

    def _walk(holder, prefix: str):
        for entry in getattr(holder, "routes", None) or []:
            if isinstance(entry, APIRoute):
                yield entry, prefix
                continue
            original = getattr(entry, "original_router", None)
            context = getattr(entry, "include_context", None)
            if original is not None and context is not None:
                sub_prefix = (prefix or "") + (getattr(context, "prefix", "") or "")
                yield from _walk(original, sub_prefix)

    return list(_walk(app, ""))


def _route_requires_admin(route: Any) -> bool:
    """判断单个 FastAPI 路由是否声明了 get_current_admin 依赖（由路由元数据驱动）。

    函数签名里的 `Depends(get_current_admin)` 记录在 route.dependant 依赖树中
    （route.dependencies 只保存路由级依赖），因此需要沿 dependant 图递归查找。
    """
    try:
        from fastapi.routing import APIRoute
    except Exception:
        return False
    if not isinstance(route, APIRoute):
        return False
    dependant = getattr(route, "dependant", None)
    stack = [dependant] if dependant is not None else []
    seen = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if getattr(node, "call", None) is get_current_admin:
            return True
        stack.extend(getattr(node, "dependencies", None) or [])
    return False


def _route_requires_admin_for_request(request: Request) -> bool | None:
    """
    在本地路由表中按 method+path 精确匹配，返回该路由是否需要管理员。
    - True / False：匹配到本地路由，按其真实依赖判定（替代历史硬编码清单，
      避免 /api/players/*、/api/save_web_config 等新老路由漏配导致越权）；
    - None：本地路由表无匹配（理论上仅出现在插件自注册等动态场景），调用方自行兜底。
    """
    try:
        from starlette.routing import Match
    except Exception:
        return None
    scope = {
        "type": "http",
        "method": request.method,
        "path": request.url.path,
        "headers": [],
        "query_string": b"",
    }
    path = request.url.path
    for route, prefix in iter_api_routes(request.app):
        # 惰性包含布局下 route.path 不含 include 前缀，需先剥掉前缀再匹配
        rel_path = path
        if prefix:
            if not path.startswith(prefix):
                continue
            rel_path = path[len(prefix):]
        sub_scope = dict(scope)
        sub_scope["path"] = rel_path
        try:
            match = route.matches(sub_scope)
        except Exception:
            continue
        if match and match[0] == Match.FULL:
            return _route_requires_admin(route)
    return None


def _filter_query_items(items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    # 移除 serverId，避免子服再处理
    return [(k, v) for (k, v) in items if k != "serverId"]


def _filter_outbound_request_headers(request: Request) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        # origin/referer 属于主服站点，转发给子服会被其同源校验误判；转发头由下方按真实 IP 重写
        if lk in {"host", "content-length", "cookie", "origin", "referer",
                  "x-forwarded-for", "x-real-ip", "x-forwarded-host", "x-forwarded-proto"}:
            continue
        headers[k] = v
    return headers


def _filter_inbound_response_headers(headers: aiohttp.typedefs.LooseHeaders) -> Dict[str, str]:
    out_headers: Dict[str, str] = {}
    for k, v in dict(headers).items():
        lk = str(k).lower()
        if lk in {"set-cookie", "content-length", "transfer-encoding", "connection"}:
            continue
        out_headers[str(k)] = str(v)
    return out_headers


async def proxy_request_to_slave(request: Request, slave: dict, sub_path: str) -> Response:
    """
    将主服请求代理到子服的 /api/{sub_path}
    - 注入 X-Panel-Token
    - 不透传 Cookie / Set-Cookie
    """
    base_url = str(slave.get("base_url", "")).rstrip("/")
    target_url = f"{base_url}/api/{sub_path.lstrip('/')}"

    query = _filter_query_items(list(request.query_params.multi_items()))
    headers = _filter_outbound_request_headers(request)
    headers["X-Panel-Token"] = str(slave.get("token", "")).strip()
    headers["X-Forwarded-For"] = get_client_ip(request)

    verify_tls = bool(slave.get("verify_tls", True))
    session: aiohttp.ClientSession = request.app.state.http_session
    log = getattr(
        getattr(request.app.state, "server_interface", None), "logger", None
    )

    try:
        # 将请求体作为异步迭代器转发，尤其避免上传 JAR 时主服先完整读入内存。
        request_data = request.stream()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)
        async with session.request(
            method=request.method,
            url=target_url,
            params=query,
            data=request_data,
            headers=headers,
            ssl=verify_tls,
            timeout=timeout,
        ) as resp:
            resp_body = await resp.read()
            out_headers = _filter_inbound_response_headers(resp.headers)
            return Response(
                content=resp_body, status_code=resp.status, headers=out_headers
            )
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        # 子服未启动、拒绝连接、超时、DNS 等：预期内情况，不打 ERROR + traceback
        if log:
            log.debug("代理子服不可达 %s: %s", target_url, e)
        return JSONResponse(
            {
                "status": "error",
                "message": "Slave unreachable",
                "code": "slave_offline",
            },
            status_code=502,
        )


class ApiProxyDispatchMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            server_id = get_target_server_id(request)
            if server_id == "local":
                return await call_next(request)

            # 仅主服模式启用代理
            config_service: ConfigService | None = getattr(
                request.app.state, "config_service", None
            )
            if config_service is None:
                return await call_next(request)
            server_config = config_service.get_config()
            if server_config.get("panel_role", "master") != "master":
                return await call_next(request)

            if not is_proxy_candidate_path(request.url.path):
                return await call_next(request)

            # 主服本地先做权限判定（权限在主服判定）：
            # 管理员要求直接取自本地路由的真实 Depends(get_current_admin)，不再维护第二份清单。
            requires_admin = _route_requires_admin_for_request(request)
            if requires_admin is None:
                # 本地路由表无匹配（插件自注册等）：按普通登录处理，子服侧仍会执行自己的权限判定
                requires_admin = False
            if requires_admin:
                current_user = await get_current_user(request)
                await get_current_admin(request, current_user=current_user)
            else:
                await get_current_user(request)

            # 查找子服
            slaves = server_config.get("panel_slaves") or []
            slave = None
            for s in slaves:
                if not isinstance(s, dict):
                    continue
                if not s.get("enabled", True):
                    continue
                if str(s.get("id", "")).strip() == server_id:
                    slave = s
                    break
            if slave is None:
                return JSONResponse(
                    {"status": "error", "message": f"Unknown serverId: {server_id}"},
                    status_code=400,
                )

            sub_path = request.url.path[len("/api/") :]
            return await proxy_request_to_slave(request, slave, sub_path)
        except HTTPException:
            raise
        except Exception as e:
            # 未预料的异常仍记录完整栈，便于排查
            request.app.state.server_interface.logger.error(
                f"代理请求失败: {e}", exc_info=True
            )
            return JSONResponse(
                {"status": "error", "message": "Proxy request failed"},
                status_code=502,
            )

