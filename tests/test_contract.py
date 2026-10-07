"""契约测试：路由 response_model 与统一错误体结构断言。

覆盖四类关键示例：
- 插件列表（GET /api/plugins）→ ApiSuccessEnvelope；
- 任务状态（GET /api/pip/tasks/{task_id}、GET /api/pim/tasks/{task_id}）→ ApiSuccessEnvelope；
- 分页（GET /api/audit_logs、GET /api/chat/messages、GET /api/mods/trash）→ PageEnvelope；
- 错误体示例（404 / 401 / 422）→ {status: error, message, code} 统一结构。

路由元数据使用与 tests/test_proxy_local.py 相同的最小挂载应用做静态断言；
错误体/外壳行为使用真实 web_server.app（未初始化 app.state，仅依赖无关请求路径）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guguwebui.panel_merge.routes import router as panel_merge_router
from guguwebui.routers.audit_router import router as audit_router
from guguwebui.routers.chat_router import router as chat_router
from guguwebui.routers.config_router import router as config_router
from guguwebui.routers.monitor_router import router as monitor_router
from guguwebui.routers.mod_router import router as mod_router
from guguwebui.routers.pim_router import router as pim_router
from guguwebui.routers.pip_router import router as pip_router
from guguwebui.routers.player_router import router as player_router
from guguwebui.routers.plugin_management_router import (
    router as plugin_management_router,
)
from guguwebui.routers.plugin_proxy_router import router as plugin_proxy_router
from guguwebui.routers.server_router import router as server_router
from guguwebui.structures.envelope import (
    ApiSuccessEnvelope,
    PageEnvelope,
)

# 镜像 web_server.py 的挂载方式（与 tests/test_proxy_local.py 保持一致）
_ROUTERS = [
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


def _build_app() -> FastAPI:
    app = FastAPI()
    for r in _ROUTERS:
        app.include_router(r, prefix="/api")
    return app


def _model_for(app: FastAPI, method: str, path: str):
    from guguwebui.panel_merge.proxy import iter_api_routes

    for route, prefix in iter_api_routes(app):
        if (prefix or "") + (route.path or "") != path:
            continue
        if method.upper() not in route.methods:
            continue
        return route.response_model
    raise AssertionError(f"route {method} {path} not found")


def test_plugin_list_response_model():
    app = _build_app()
    assert _model_for(app, "GET", "/api/plugins") is ApiSuccessEnvelope


def test_task_status_response_models():
    app = _build_app()
    assert _model_for(app, "GET", "/api/pip/tasks/{task_id}") is ApiSuccessEnvelope
    assert _model_for(app, "GET", "/api/pim/tasks/{task_id}") is ApiSuccessEnvelope


def test_paginated_routes_use_page_envelope():
    app = _build_app()
    assert _model_for(app, "GET", "/api/audit_logs") is PageEnvelope
    assert _model_for(app, "GET", "/api/chat/messages") is PageEnvelope
    assert _model_for(app, "GET", "/api/mods/trash") is PageEnvelope


def test_json_routes_declare_response_model():
    """抽查其余 JSON 数据端点也已声明统一外壳（防再次出现无契约裸端点）。"""
    app = _build_app()
    for path in (
        "/api/plugins/{plugin_id}/versions",
        "/api/plugins/{plugin_id}/install",
        "/api/pip/packages",
        "/api/pip/tasks",
        "/api/server/status",
        "/api/server/logs",
        "/api/server/controls",
        "/api/server/commands",
        "/api/monitor/overview",
        "/api/config-files",
        "/api/web-config",
        "/api/custom-assets/{kind}",
        "/api/players",
        "/api/players/bans",
        "/api/players/stats/overview",
        "/api/players/stats/online-history",
        "/api/players/stats/daily",
        "/api/players/stats/players",
        "/api/chat/verifications/{code}",
        "/api/servers",
        "/api/pairing/status",
        "/api/mods",
        "/api/mods/settings",
        "/api/plugins/web-pages",
        "/api/i18n/languages",
    ):
        model = _model_for(app, "GET" if not path.endswith(("/install", "/tasks", "/commands", "/controls")) else "POST", path)
        assert model is ApiSuccessEnvelope, f"{path} 缺少 ApiSuccessEnvelope response_model"


def test_error_body_envelope_shape_on_real_app():
    """错误体示例（统一错误体 2.3）：404 / 401 / 422 均为 {status, message, code}。"""
    from guguwebui.web_server import app as real_app
    from guguwebui.dependencies.auth import get_chat_reader

    client = TestClient(real_app)

    resp = client.get("/api/definitely_not_exists")
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["code"], str) and body["code"]

    # 未登录访问需要登录的端点 → 401 统一错误体
    resp = client.get("/api/plugins")
    assert resp.status_code == 401
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] == "http_401"

    # 聊天读取接口已要求凭证：依赖先于参数校验执行，未启用公开聊天时先返回 403 外壳
    resp = client.get("/api/chat/messages", params={"limit": 99999})
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] == "public_chat_disabled"

    # 携带凭证后才是参数校验错误 → 422 统一错误体
    real_app.dependency_overrides[get_chat_reader] = lambda: {"username": "admin", "token": "t"}
    try:
        resp = client.get("/api/chat/messages", params={"limit": 99999})
    finally:
        real_app.dependency_overrides.pop(get_chat_reader, None)
    assert resp.status_code == 422
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] == "validation_error"
    assert isinstance(body.get("data", {}).get("errors"), list)
    assert "detail" not in body

    # 业务状态码（404 + 机器码）也走同一外壳
    resp = client.get("/api/pip/tasks/not-exist", headers={"X-Target-Server": "local"})
    # pip 任务接口需要管理员；未登录先 401（外壳一致即可）
    assert resp.status_code in (401, 404)
    body = resp.json()
    assert body["status"] == "error"
    assert isinstance(body["code"], str)


def test_404_api_not_found_is_json_not_spa():
    from guguwebui.web_server import app as real_app

    client = TestClient(real_app)
    resp = client.get("/api/definitely_not_exists")
    assert resp.headers.get("content-type", "").startswith("application/json")
    assert resp.json()["status"] == "error"
