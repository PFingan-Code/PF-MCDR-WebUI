"""is_proxy_candidate_path 回归测试：本地/代理归属语义由单一清单收敛。

原实现把“本地仅处理”的路径写成 proxy.py 内联字符串清单，与路由表脱节；
归属语义收敛为 `LOCAL_ONLY_PATHS / LOCAL_ONLY_LEGACY_PATHS / LOCAL_ONLY_PREFIXES`
常量，并由本测试全量路由表扫描守护两个不变量：
1. 当前路由表（/api/*）中，每个路由的“是否可代理”判定必须与清单完全一致——
   新增路由不会因为忘记加入清单而漏配（被误代理），也不会误配（本应代理而不代理）；
2. 精选关键路径（登录/会话/面板合并/配对/在线插件）按预期返回 False。
"""

from __future__ import annotations

from fastapi import FastAPI

from guguwebui.panel_merge.proxy import (
    LOCAL_ONLY_LEGACY_PATHS,
    LOCAL_ONLY_PATHS,
    LOCAL_ONLY_PREFIXES,
    is_proxy_candidate_path,
    iter_api_routes,
)
from guguwebui.panel_merge.routes import router as panel_merge_router
from guguwebui.routers.audit_router import router as audit_router
from guguwebui.routers.chat_router import router as chat_router
from guguwebui.routers.config_router import router as config_router
from guguwebui.routers.monitor_router import router as monitor_router
from guguwebui.routers.mod_router import router as mod_router
from guguwebui.routers.pim_router import router as pim_router
from guguwebui.routers.pip_router import router as pip_router
from guguwebui.routers.player_router import router as player_router
from guguwebui.routers.plugin_management_router import \
    router as plugin_management_router
from guguwebui.routers.plugin_proxy_router import router as plugin_proxy_router
from guguwebui.routers.server_router import router as server_router


def _build_app() -> FastAPI:
    """镜像 web_server.py 的挂载方式（不含 @app 级页面/web_server 本地端点路由）。"""
    app = FastAPI()
    routers = [
        panel_merge_router,
        plugin_management_router,
        config_router,
        server_router,
        monitor_router,
        plugin_proxy_router,
        pim_router,
        pip_router,
        chat_router,
        player_router,
        mod_router,
        audit_router,
    ]
    for r in routers:
        app.include_router(r, prefix="/api")
    return app


def _is_listed_local(path: str) -> bool:
    return (
        path in LOCAL_ONLY_PATHS
        or path in LOCAL_ONLY_LEGACY_PATHS
        or path.startswith(LOCAL_ONLY_PREFIXES)
    )


def _full_path(route, prefix: str) -> str:
    return (prefix or "") + (route.path or "")


def test_proxy_list_matches_route_table():
    """全量路由表扫描：is_proxy_candidate_path 与清单判定完全一致。"""
    app = _build_app()
    checked = 0
    for route, prefix in iter_api_routes(app):
        path = _full_path(route, prefix)
        expected = not _is_listed_local(path)
        got = is_proxy_candidate_path(path)
        assert got is expected, (
            f"route {path} got proxy_candidate={got}, list says {expected}"
        )
        checked += 1
    assert checked > 60, f"expected a broad route sweep, only checked {checked}"


def test_key_local_only_paths_are_never_proxied():
    """精选关键路径（无需路由表存在，纯函数语义断言）。"""
    assert is_proxy_candidate_path("/api/servers") is False
    assert is_proxy_candidate_path("/api/panel_merge_config") is False
    assert is_proxy_candidate_path("/api/audit_logs") is False
    assert is_proxy_candidate_path("/api/i18n/languages") is False
    assert is_proxy_candidate_path("/api/plugins/online") is False
    assert is_proxy_candidate_path("/api/online-plugins") is False  # 遗留兜底
    assert is_proxy_candidate_path("/api/pairing/enable") is False
    assert is_proxy_candidate_path("/api/pairing/status") is False
    # web_server 本地端点
    assert is_proxy_candidate_path("/api/login") is False
    assert is_proxy_candidate_path("/api/login/qq_qr/status") is False
    assert is_proxy_candidate_path("/api/logout") is False
    assert is_proxy_candidate_path("/api/auth/me") is False


def test_proxyable_paths_still_proxied():
    """普通业务端点仍应可代理（授权判定仍在主服、执行在子服）。"""
    for path in [
        "/api/server/status",
        "/api/plugins",
        "/api/pip/packages",
        "/api/chat/messages",
        "/api/players/whitelist",
    ]:
        assert is_proxy_candidate_path(path) is True, path


def test_local_only_paths_refer_to_real_routes():
    """清单中的路径必须在真实路由表中存在（防打字/已下线残留），遗留路径除外。"""
    app = _build_app()
    registered = {_full_path(route, prefix) for route, prefix in iter_api_routes(app)}
    # web_server 本地端点（login/logout/auth/me）不在 routers 应用内，跳过它们
    web_server_local = {
        "/api/login", "/api/login/qq_qr/start", "/api/login/qq_qr/status",
        "/api/login/options", "/api/login/totp", "/api/logout", "/api/auth/me",
    }
    for path in LOCAL_ONLY_PATHS:
        if path in web_server_local:
            continue
        assert path in registered, f"{path} 在清单中但未注册任何路由"
    for path in LOCAL_ONLY_LEGACY_PATHS:
        assert path not in registered, f"{path} 是遗留路径但路由表已注册（应清理）"