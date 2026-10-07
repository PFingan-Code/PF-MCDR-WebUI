"""鉴权/存储加固的回归测试。

覆盖：
1. session 密钥不再硬编码；
2. 鉴权从 token 记录取用户名，不再信任 session 中的 username；
3. token 过期在接口层生效（不再只有登录页检查）；
4. 临时码登录同样受 disable_other_admin 约束；配置文件读取仅管理员；
5. 聊天消息读取需要凭证，且受 public_chat_enabled 约束；
6. 命令黑名单归一化比较；登录失败次数限制；
7. 建表脚本只执行一次；user_db 按顶层键分行写入。
"""

from __future__ import annotations

import datetime
import json

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from guguwebui.constant import SECRET_KEY, user_db
from guguwebui.routers.chat_router import router as chat_router
from guguwebui.routers.config_router import router as config_router
from guguwebui.structures import BusinessException
from guguwebui.structures.envelope import error as api_error
from guguwebui.utils import storage as storage_module
from guguwebui.utils.auth_token import is_expired, parse_expire_time, resolve_web_token
from guguwebui.utils.rate_limit import reset_all as reset_rate_limits
from guguwebui.services.server_service import _is_forbidden_command


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


def _add_token(token: str, username: str, *, expired: bool = False) -> None:
    delta = datetime.timedelta(hours=-1 if expired else 1)
    user_db["token"][token] = {
        "user_name": username,
        "expire_time": str(datetime.datetime.now(datetime.timezone.utc) + delta),
    }
    user_db.save()


def _remove_token(token: str) -> None:
    user_db["token"].pop(token, None)
    user_db.save()


# --------------------------------------------------------------------------
# 1. session 密钥
# --------------------------------------------------------------------------


def test_session_secret_is_not_hardcoded():
    assert SECRET_KEY and SECRET_KEY != "guguwebui"
    assert len(SECRET_KEY) >= 32


def test_session_secret_is_stable_across_reloads():
    from guguwebui.constant import _load_session_secret

    assert _load_session_secret() == SECRET_KEY


# --------------------------------------------------------------------------
# 2/3. token 解析、过期与用户名来源
# --------------------------------------------------------------------------


def test_parse_expire_time_handles_known_formats():
    assert parse_expire_time("2026-01-01 00:00:00+00:00") is not None
    assert parse_expire_time("2026-01-01T00:00:00Z") is not None
    assert parse_expire_time(datetime.datetime.now(datetime.timezone.utc)) is not None
    # 无时区信息按 UTC 处理
    parsed = parse_expire_time("2026-01-01 00:00:00")
    assert parsed is not None and parsed.tzinfo is not None
    # 无法解析视为过期
    assert parse_expire_time("not-a-date") is None
    assert is_expired("not-a-date") is True
    assert is_expired("2000-01-01 00:00:00+00:00") is True


def test_resolve_web_token_returns_username_from_token_record():
    token = "unit-token-username"
    _add_token(token, "real_user")
    try:
        user = resolve_web_token(token)
        assert user == {"username": "real_user", "token": token}
    finally:
        _remove_token(token)


def test_resolve_web_token_rejects_and_purges_expired():
    token = "unit-token-expired"
    _add_token(token, "real_user", expired=True)
    assert resolve_web_token(token) is None
    # 过期 token 已从库中清除
    assert token not in user_db["token"]
    _remove_token(token)


def test_resolve_web_token_rejects_unknown_or_broken_records():
    assert resolve_web_token(None) is None
    assert resolve_web_token("") is None
    assert resolve_web_token("no-such-token") is None
    token = "unit-token-broken"
    user_db["token"][token] = {"expire_time": "2030-01-01 00:00:00+00:00"}
    user_db.save()
    try:
        assert resolve_web_token(token) is None
    finally:
        _remove_token(token)


def _auth_app() -> FastAPI:
    """最小应用：只挂鉴权依赖所依赖的路由，并注册统一错误体。"""
    from guguwebui.dependencies.auth import get_current_user

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.exception_handler(BusinessException)
    async def _business(request, exc):  # pragma: no cover - 仅注册
        return api_error(exc.message, code=exc.code)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            api_error(str(exc.detail), code=f"http_{exc.status_code}"),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request, exc):  # pragma: no cover - 仅注册
        from fastapi.responses import JSONResponse

        return JSONResponse(api_error("invalid", code="validation_error"), status_code=422)

    @app.get("/api/whoami")
    async def whoami(user: dict = Depends(get_current_user)):
        return {"status": "success", "data": user}

    return app


def test_api_rejects_expired_token(monkeypatch):
    token = "api-token-expired"
    _add_token(token, "admin", expired=True)
    try:
        client = TestClient(_auth_app())
        resp = client.get("/api/whoami", cookies={"token": token})
        assert resp.status_code == 401
    finally:
        _remove_token(token)


def test_forged_session_username_is_ignored():
    """伪造 session 中的 username 不能提权：用户名只来自 token 记录。"""
    token = "api-token-low-priv"
    _add_token(token, "normal_user")
    try:
        client = TestClient(_auth_app())
        # 通过 session 中间件的签名机制写入一个“管理员”身份
        with client:
            client.get("/api/whoami", cookies={"token": token})
            resp = client.get("/api/whoami", cookies={"token": token})
        assert resp.status_code == 200
        assert resp.json()["data"]["username"] == "normal_user"

        # 直接把伪造内容塞进 session cookie（模拟持有旧密钥的攻击者）
        from itsdangerous import TimestampSigner

        forged = TimestampSigner("guguwebui").sign(
            json.dumps({"logged_in": True, "username": "super_admin"})
        ).decode()
        resp = client.get(
            "/api/whoami",
            cookies={"token": token, "session": forged},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["username"] == "normal_user"
    finally:
        _remove_token(token)


# --------------------------------------------------------------------------
# 4. 临时码登录 + 配置文件读取权限
# --------------------------------------------------------------------------


class _ConfigStub:
    def __init__(self, **overrides):
        self.values = {
            "super_admin_account": "123456",
            "disable_other_admin": True,
            "allow_temp_password": True,
            "public_chat_enabled": False,
        }
        self.values.update(overrides)

    def get_config(self):
        return dict(self.values)


class _ServerStub:
    class _Logger:
        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    def __init__(self, config: dict):
        self.logger = self._Logger()
        self._config = config

    def load_config_simple(self, name, default, echo_in_console=False):
        merged = dict(default)
        merged.update(self._config)
        return merged


def _login_service(config: dict):
    """构造 AuthService，并返回可直接调用的 login（绕过 HTTP 表单层）。"""
    from guguwebui.services.auth_service import AuthService

    return AuthService(_ServerStub(config))


def _login_call(service, *, account: str = "", password: str = "", temp_code: str = "", ip: str = "10.0.0.1"):
    """在装有 SessionMiddleware 的最小应用内调用登录（与真实链路一致）。

    返回 (status_code, body_dict, response_headers)。
    """
    from starlette.testclient import TestClient as StarletteTestClient

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.post("/api/login")
    async def _login_route(request: Request):
        return await service.login(request, account, password, temp_code, False)

    with StarletteTestClient(app, client=(ip, 12345)) as client:
        response = client.post("/api/login")
    try:
        body = json.loads(response.content.decode("utf-8"))
    except ValueError:
        body = {}
    return response.status_code, body, dict(response.headers)


def _cookie_token(headers: dict) -> str:
    raw = headers.get("set-cookie", "")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "token":
            return value
    return ""


def test_temp_login_respects_disable_other_admin():
    from guguwebui.utils.auth_util import create_temp_password

    code = create_temp_password()  # 无 QQ 号 → 用户名 tempuser
    try:
        service = _login_service({"disable_other_admin": True, "super_admin_account": "123456"})
        status, body, _headers = _login_call(service, temp_code=code)
        assert status == 403, body
        # 被拒绝时不消耗临时码
        assert code in user_db["temp"]
    finally:
        user_db["temp"].pop(code, None)
        user_db.save()


def test_temp_login_allowed_when_other_admin_enabled():
    from guguwebui.utils.auth_util import create_temp_password

    token = None
    code = create_temp_password()
    try:
        service = _login_service({"disable_other_admin": False, "super_admin_account": "123456"})
        status, body, headers = _login_call(service, temp_code=code)
        assert status == 200, body
        token = _cookie_token(headers) or None
        assert token
        assert token in user_db["token"]
    finally:
        user_db["temp"].pop(code, None)
        user_db.save()
        if token:
            _remove_token(token)


def test_temp_login_is_rate_limited():
    service = _login_service({"disable_other_admin": False})
    for _ in range(20):
        status, _body, _headers = _login_call(service, temp_code="BADCODE")
        assert status == 401
    status, body, _headers = _login_call(service, temp_code="BADCODE")
    assert status == 429
    assert body["code"] == "too_many_attempts"
    assert body["data"]["retry_after"] > 0


def test_config_file_read_requires_admin_metadata():
    """路由元数据必须声明管理员依赖（主服代理据此判权）。"""
    from guguwebui.panel_merge.proxy import _route_requires_admin, iter_api_routes
    from guguwebui.routers.plugin_management_router import (
        router as plugin_management_router,
    )

    app = FastAPI()
    app.include_router(config_router, prefix="/api")
    app.include_router(plugin_management_router, prefix="/api")
    checked = {}
    for route, prefix in iter_api_routes(app):
        full = (prefix or "") + (route.path or "")
        if full in ("/api/config-files", "/api/plugins/{plugin_id}/config-files"):
            for method in route.methods or []:
                if method in ("GET", "PUT"):
                    checked[(method, full)] = _route_requires_admin(route)
    assert checked.get(("GET", "/api/config-files")) is True
    assert checked.get(("GET", "/api/plugins/{plugin_id}/config-files")) is True


def test_config_file_read_rejects_anonymous():
    from guguwebui.routers.plugin_management_router import (
        router as plugin_management_router,
    )

    app = FastAPI()
    app.include_router(config_router, prefix="/api")
    app.include_router(plugin_management_router, prefix="/api")
    app.state.config_service = _ConfigStub()
    client = TestClient(app)
    assert client.get("/api/config-files", params={"path": "config.yml"}).status_code == 401
    assert (
        client.get("/api/plugins/guguwebui/config-files").status_code == 401
    )


# --------------------------------------------------------------------------
# 5. 聊天消息读取鉴权
# --------------------------------------------------------------------------


class _ChatServiceStub:
    def __init__(self):
        self.seen_heartbeat = "unset"

    async def get_messages(self, **kwargs):
        return {"items": [], "total": 0, "offset": 0, "limit": 50, "has_more": False}

    async def get_new_messages(self, after_id=0, player_id_heartbeat=None):
        self.seen_heartbeat = player_id_heartbeat
        return {"messages": [], "last_message_id": 0, "online": {"web": [], "game": [], "bot": []}}


def _chat_client(config: dict, chat_service=None):
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.exception_handler(BusinessException)
    async def _business(request, exc):
        return JSONResponse(
            api_error(exc.message, code=exc.code, data=exc.data),
            status_code=exc.status_code,
        )

    app.include_router(chat_router, prefix="/api")
    app.state.config_service = _ConfigStub(**config)
    app.state.chat_service = chat_service or _ChatServiceStub()
    return TestClient(app)


def _add_chat_session(session_id: str, player_id: str = "Shusao") -> None:
    expire_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
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


def test_chat_messages_rejects_anonymous_when_public_disabled():
    client = _chat_client({"public_chat_enabled": False})
    resp = client.get("/api/chat/messages")
    assert resp.status_code == 403
    assert resp.json()["code"] == "public_chat_disabled"


def test_chat_messages_rejects_anonymous_when_public_enabled_without_session():
    client = _chat_client({"public_chat_enabled": True})
    assert client.get("/api/chat/messages").status_code == 401
    assert client.get("/api/chat/messages/incremental").status_code == 401


def test_chat_messages_accepts_valid_chat_session():
    session_id = "api-chat-session-ok"
    _add_chat_session(session_id)
    try:
        client = _chat_client({"public_chat_enabled": True})
        resp = client.get("/api/chat/messages", params={"session_id": session_id})
        assert resp.status_code == 200
    finally:
        _remove_chat_session(session_id)


def test_chat_messages_rejects_chat_session_when_public_disabled():
    session_id = "api-chat-session-disabled"
    _add_chat_session(session_id)
    try:
        client = _chat_client({"public_chat_enabled": False})
        resp = client.get("/api/chat/messages", params={"session_id": session_id})
        assert resp.status_code == 403
    finally:
        _remove_chat_session(session_id)


def test_chat_heartbeat_cannot_impersonate_other_player():
    session_id = "api-chat-session-heartbeat"
    _add_chat_session(session_id, player_id="Shusao")
    service = _ChatServiceStub()
    try:
        client = _chat_client({"public_chat_enabled": True}, chat_service=service)
        resp = client.get(
            "/api/chat/messages/incremental",
            params={"session_id": session_id, "player_id": "SomeoneElse"},
        )
        assert resp.status_code == 200
        assert service.seen_heartbeat == "Shusao"
    finally:
        _remove_chat_session(session_id)


# --------------------------------------------------------------------------
# 6. 命令黑名单 + 登录失败限制
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "stop",
        "STOP",
        "  stop  ",
        "/stop",
        "!!MCDR plugin reload guguwebui",
        "!!mcdr plugin reload guguwebui",
        "!!MCDR plugin  RELOAD  guguwebui",
        "!!MCDR plg unload guguwebui",
        "!!MCDR plugin disable guguwebui",
    ],
)
def test_forbidden_commands_detected(command):
    assert _is_forbidden_command(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "!!MCDR plugin reload other_plugin",
        "!!MCDR plugin list",
        "list",
        "say stop",
        "!!MCDR server stop",
        "",
        "   ",
    ],
)
def test_allowed_commands_not_blocked(command):
    assert _is_forbidden_command(command) is False


def test_login_rate_limit_locks_after_failures_and_resets_on_success():
    from guguwebui.constant import pwd_context

    user_db["user"]["ratelimit_user"] = pwd_context.hash("correct-password")
    user_db.save()
    try:
        service = _login_service({"disable_other_admin": False, "super_admin_account": "ratelimit_user"})
        for _ in range(5):
            status, _body, _headers = _login_call(
                service, account="ratelimit_user", password="wrong-password"
            )
            assert status == 401

        status, body, _headers = _login_call(
            service, account="ratelimit_user", password="wrong-password"
        )
        assert status == 429
        assert body["code"] == "too_many_attempts"
        assert body["data"]["retry_after"] > 0

        # 正确密码在锁定窗口内同样被拒绝
        status, _body, _headers = _login_call(
            service, account="ratelimit_user", password="correct-password"
        )
        assert status == 429
    finally:
        user_db["user"].pop("ratelimit_user", None)
        user_db.save()

    # 清空计数后成功登录应正常，且成功会清除该账号的失败计数
    reset_rate_limits()
    user_db["user"]["ratelimit_user"] = pwd_context.hash("correct-password")
    user_db.save()
    token = None
    try:
        service = _login_service({"disable_other_admin": False, "super_admin_account": "ratelimit_user"})
        status, body, headers = _login_call(
            service, account="ratelimit_user", password="correct-password"
        )
        assert status == 200
        token = _cookie_token(headers)
        assert token and token in user_db["token"]
    finally:
        user_db["user"].pop("ratelimit_user", None)
        user_db.save()
        if token:
            _remove_token(token)


def test_login_rate_limit_is_isolated_per_account():
    from guguwebui.constant import pwd_context

    user_db["user"]["victim_1"] = pwd_context.hash("pw")
    user_db["user"]["victim_2"] = pwd_context.hash("pw")
    user_db.save()
    token = None
    try:
        service = _login_service({"disable_other_admin": False})
        for _ in range(5):
            _login_call(service, account="victim_1", password="bad")
        # 账号 2 不受账号 1 的失败计数影响
        status, body, headers = _login_call(service, account="victim_2", password="pw")
        assert status == 200, body
        token = _cookie_token(headers)
    finally:
        user_db["user"].pop("victim_1", None)
        user_db["user"].pop("victim_2", None)
        user_db.save()
        if token:
            _remove_token(token)


# --------------------------------------------------------------------------
# 7. 存储层
# --------------------------------------------------------------------------


def test_schema_is_applied_once_per_database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_DB_PATH", tmp_path / "once.sqlite3")
    monkeypatch.setattr(storage_module, "_SCHEMA_READY", set())
    calls = {"n": 0}
    original = storage_module._apply_schema

    def _counted(conn):
        calls["n"] += 1
        return original(conn)

    monkeypatch.setattr(storage_module, "_apply_schema", _counted)
    for _ in range(5):
        conn = storage_module.connect()
        conn.close()
    assert calls["n"] == 1


def test_sqlite_table_writes_only_changed_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_DB_PATH", tmp_path / "keys.sqlite3")
    monkeypatch.setattr(storage_module, "_SCHEMA_READY", set())
    table = storage_module.SQLiteTable("t_keys", default_content={"a": {}, "b": {}})

    table["a"]["x"] = 1
    table.save()

    conn = storage_module.connect()
    try:
        rows = dict(
            conn.execute(
                "SELECT state_key, value_json FROM app_state WHERE namespace=?",
                ("t_keys.keys",),
            ).fetchall()
        )
    finally:
        conn.close()
    assert set(rows) == {"a", "b"}
    assert "x" in rows["a"]

    # 重新加载后数据可完整恢复
    reloaded = storage_module.SQLiteTable("t_keys", default_content={"a": {}, "b": {}})
    assert reloaded["a"] == {"x": 1}

    # 删除键会同步删除行
    del reloaded["b"]
    conn = storage_module.connect()
    try:
        keys = [
            r[0]
            for r in conn.execute(
                "SELECT state_key FROM app_state WHERE namespace=?", ("t_keys.keys",)
            ).fetchall()
        ]
    finally:
        conn.close()
    assert keys == ["a"]

    # 全部键删除后不会回落到旧版整块行
    del reloaded["a"]
    assert storage_module.SQLiteTable("t_keys", default_content={"a": {}, "b": {}}).data == {}


def test_sqlite_table_migrates_legacy_row(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_DB_PATH", tmp_path / "legacy.sqlite3")
    monkeypatch.setattr(storage_module, "_SCHEMA_READY", set())
    # 写入旧版布局（整块 JSON 放在 <ns>/data）
    storage_module.set_state("t_legacy", "data", {"user": {"a": "hash"}, "token": {}})

    table = storage_module.SQLiteTable("t_legacy", default_content={"user": {}, "token": {}})
    assert table["user"] == {"a": "hash"}
    # 迁移后旧行保留（可回退旧版本），新布局已写入
    conn = storage_module.connect()
    try:
        legacy = conn.execute(
            "SELECT value_json FROM app_state WHERE namespace=? AND state_key='data'",
            ("t_legacy",),
        ).fetchone()
        rows = [
            r[0]
            for r in conn.execute(
                "SELECT state_key FROM app_state WHERE namespace=?", ("t_legacy.keys",)
            ).fetchall()
        ]
    finally:
        conn.close()
    assert legacy is not None
    assert sorted(rows) == ["token", "user"]


def test_user_db_uses_split_rows():
    """实际 user_db 已在新布局上（首次加载即完成迁移）。"""
    conn = storage_module.connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM app_state WHERE namespace='user_db.keys' AND state_key='token'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
