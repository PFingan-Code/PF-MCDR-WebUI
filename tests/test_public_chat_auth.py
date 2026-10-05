"""公开聊天页的接口授权。

公开聊天页是匿名入口，只持有 chat_session_id。它需要读取的受限接口
（/api/server/status、/api/plugins/{plugin_id}）应接受该会话作为凭证；
而其它未列入的接口仍然只认 WebUI 登录态。
"""

from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from guguwebui.constant import user_db
from guguwebui.dependencies.auth import resolve_chat_session
from guguwebui.routers.plugin_management_router import (
    router as plugin_management_router,
)
from guguwebui.routers.server_router import router as server_router


def _add_chat_session(session_id: str, player_id: str = "Shusao", *, expired: bool = False) -> None:
    delta = datetime.timedelta(hours=-1 if expired else 1)
    expire_time = datetime.datetime.now(datetime.timezone.utc) + delta
    user_db["chat_sessions"][session_id] = {
        "player_id": player_id,
        "expire_time": str(expire_time),
        "ip": "test",
        "last_sent_ms": 0,
    }
    user_db.save()


def _remove_chat_session(session_id: str) -> None:
    user_db["chat_sessions"].pop(session_id, None)
    user_db.save()


class _ServerServiceStub:
    async def get_server_status(self):
        return {"online": True, "version": "1.20.1", "players": "1/20"}


class _PluginServiceStub:
    def get_plugins_list(self):
        return [{"id": "guguwebui", "version": "1.10.0", "name": "GUGU WebUI"}]


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(server_router, prefix="/api")
    app.include_router(plugin_management_router, prefix="/api")
    app.state.server_service = _ServerServiceStub()
    app.state.plugin_service = _PluginServiceStub()
    return TestClient(app)


# --------------------------------------------------------------------------
# resolve_chat_session 单元行为
# --------------------------------------------------------------------------


def test_resolve_chat_session_accepts_valid_session():
    session_id = "unit-valid-session"
    _add_chat_session(session_id)
    try:
        session = resolve_chat_session(session_id)
        assert session is not None
        assert session["player_id"] == "Shusao"
    finally:
        _remove_chat_session(session_id)


def test_resolve_chat_session_rejects_unknown_and_expired():
    assert resolve_chat_session("") is None
    assert resolve_chat_session("no-such-session") is None

    session_id = "unit-expired-session"
    _add_chat_session(session_id, expired=True)
    try:
        assert resolve_chat_session(session_id) is None
    finally:
        _remove_chat_session(session_id)


# --------------------------------------------------------------------------
# 接口层：公开聊天页可读，其它接口不受影响
# --------------------------------------------------------------------------


def test_server_status_still_requires_credentials(client):
    assert client.get("/api/server/status").status_code == 401


def test_server_status_accepts_chat_session(client):
    session_id = "api-status-session"
    _add_chat_session(session_id)
    try:
        resp = client.get("/api/server/status", params={"session_id": session_id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["data"]["players"] == "1/20"
    finally:
        _remove_chat_session(session_id)


def test_server_status_rejects_expired_chat_session(client):
    session_id = "api-status-expired"
    _add_chat_session(session_id, expired=True)
    try:
        resp = client.get("/api/server/status", params={"session_id": session_id})
        assert resp.status_code == 401
    finally:
        _remove_chat_session(session_id)


def test_plugin_detail_accepts_chat_session(client):
    """页脚需要读取 WebUI 版本。"""
    session_id = "api-plugin-session"
    _add_chat_session(session_id)
    try:
        resp = client.get("/api/plugins/guguwebui", params={"session_id": session_id})
        assert resp.status_code == 200
        assert resp.json()["data"]["plugin"]["version"] == "1.10.0"
    finally:
        _remove_chat_session(session_id)


def test_chat_session_does_not_grant_other_endpoints(client):
    """授权范围仅限显式声明的只读接口，不能顺带放开其它需要登录的接口。"""
    session_id = "api-scope-session"
    _add_chat_session(session_id)
    try:
        resp = client.get("/api/plugins/web-pages", params={"session_id": session_id})
        assert resp.status_code == 401
    finally:
        _remove_chat_session(session_id)
