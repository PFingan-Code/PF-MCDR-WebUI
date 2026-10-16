"""公网暴露（frp / 反向代理）相关加固的回归测试。

覆盖：
1. 真实客户端 IP：仅采信 trusted_proxies 写入的转发头，客户端伪造的 X-Forwarded-For 无效；
2. Origin 校验与安全响应头；API 文档需登录；
3. 两步验证（TOTP）：RFC 6238 向量、登录票据流程、验证码防重放、失败计数；
4. 密码登录不再向未持有密码者透露超级管理员账号；
5. QQ 扫码 code 绑定发起它的浏览器会话；
6. HTTPS（含可信代理声明的 X-Forwarded-Proto）下 token cookie 带 Secure。
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI, Form, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from guguwebui.constant import user_db
from guguwebui.utils import totp
from guguwebui.utils.auth_util import hash_password
from guguwebui.utils.rate_limit import reset_all as reset_rate_limits
from guguwebui.utils.request_security import get_client_ip, is_valid_origin, origin_allowed

from test_auth_hardening import _ConfigStub, _cookie_token, _ServerStub


@pytest.fixture(autouse=True)
def _clean_state():
    reset_rate_limits()
    totp.clear_login_tickets()
    yield
    reset_rate_limits()
    totp.clear_login_tickets()


# --------------------------------------------------------------------------
# 1. 真实客户端 IP
# --------------------------------------------------------------------------


def _ip_app(config: dict) -> FastAPI:
    app = FastAPI()
    app.state.config_service = _ConfigStub(**config)

    @app.get("/ip")
    async def _ip(request: Request):
        return {"ip": get_client_ip(request)}

    return app


def _ip_of(config: dict, headers: dict, peer: str = "127.0.0.1") -> str:
    with TestClient(_ip_app(config), client=(peer, 50000)) as client:
        return client.get("/ip", headers=headers).json()["ip"]


def test_forwarded_headers_ignored_without_trusted_proxies():
    assert _ip_of({}, {"X-Forwarded-For": "1.2.3.4"}) == "127.0.0.1"
    assert _ip_of({}, {"X-Real-IP": "1.2.3.4"}) == "127.0.0.1"


def test_trusted_proxy_uses_rightmost_untrusted_hop():
    cfg = {"trusted_proxies": ["127.0.0.1"]}
    # 客户端自带伪造值，frp/nginx 在右侧追加真实地址
    assert _ip_of(cfg, {"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}) == "203.0.113.9"
    assert _ip_of(cfg, {"X-Forwarded-For": "203.0.113.9"}) == "203.0.113.9"
    assert _ip_of(cfg, {"X-Real-IP": "203.0.113.7"}) == "203.0.113.7"
    assert _ip_of(cfg, {}) == "127.0.0.1"


def test_untrusted_peer_cannot_spoof_even_when_proxies_configured():
    cfg = {"trusted_proxies": ["127.0.0.1"]}
    assert _ip_of(cfg, {"X-Forwarded-For": "1.2.3.4"}, peer="198.51.100.5") == "198.51.100.5"


def test_trusted_proxy_cidr_and_chain():
    cfg = {"trusted_proxies": ["10.0.0.0/8", "127.0.0.1"]}
    assert _ip_of(cfg, {"X-Forwarded-For": "203.0.113.9, 10.1.2.3"}) == "203.0.113.9"


# --------------------------------------------------------------------------
# 2. Origin 校验、安全响应头、文档鉴权
# --------------------------------------------------------------------------


def _origin_ok(origin: str, host: str, config: dict | None = None, headers: dict | None = None, peer="127.0.0.1"):
    app = FastAPI()
    seen = {}

    @app.get("/o")
    async def _o(request: Request):
        seen["ok"] = origin_allowed(request, origin, config or {})
        return {}

    with TestClient(app, client=(peer, 50000)) as client:
        client.get("/o", headers={"host": host, **(headers or {})})
    return seen["ok"]


def test_origin_allowed_rules():
    assert _origin_ok("http://mc.example.com", "mc.example.com")
    assert _origin_ok(
        "https://mc.example.com",
        "mc.example.com",
        {"trusted_proxies": ["127.0.0.1"]},
        {"x-forwarded-proto": "https"},
    )
    assert _origin_ok("http://1.2.3.4:25580", "1.2.3.4:25580")
    assert _origin_ok(
        "https://mc.example.com",
        "mc.example.com:443",
        {"trusted_proxies": ["127.0.0.1"]},
        {"x-forwarded-proto": "https"},
    )
    assert not _origin_ok("https://evil.frp-host.com", "mc.frp-host.com")
    assert not _origin_ok("null", "mc.example.com")
    assert not _origin_ok("http://mc.example.com:8080", "mc.example.com")
    # 协议也属于来源的一部分，HTTP 不得匹配 HTTPS 站点或白名单。
    https_config = {"trusted_proxies": ["127.0.0.1"], "allowed_origins": ["https://mc.example.com"]}
    https_headers = {"x-forwarded-proto": "https"}
    assert not _origin_ok("http://mc.example.com", "mc.example.com", https_config, https_headers)
    assert not _origin_ok(
        "http://mc.example.com",
        "mc.example.com",
        {"trusted_proxies": ["127.0.0.1"], "allowed_origins": ["http://other.example.com"]},
        https_headers,
    )
    assert is_valid_origin("https://mc.example.com:8443")
    assert not is_valid_origin("mc.example.com/path")
    assert not is_valid_origin("https://mc.example.com/path")
    assert not is_valid_origin("https://user:pass@mc.example.com")
    # 反向代理改写了 Host：用 allowed_origins 放行对外地址
    assert _origin_ok(
        "https://mc.example.com", "127.0.0.1:8000", {"allowed_origins": ["https://mc.example.com"]}
    )
    assert not _origin_ok(
        "http://mc.example.com", "127.0.0.1:8000", {"allowed_origins": ["https://mc.example.com"]}
    )
    # X-Forwarded-Host 只采信可信代理
    fwd = {"x-forwarded-host": "mc.example.com"}
    assert not _origin_ok("https://mc.example.com", "127.0.0.1:8000", {}, fwd)
    assert _origin_ok(
        "https://mc.example.com",
        "127.0.0.1:8000",
        {"trusted_proxies": ["127.0.0.1"]},
        {**fwd, "x-forwarded-proto": "https"},
    )


@pytest.fixture()
def real_app_client():
    from guguwebui.web_server import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_cross_site_write_is_rejected(real_app_client):
    resp = real_app_client.post("/api/__nope__", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "origin_mismatch"

    # 同源与无 Origin 的请求不受影响（落到正常路由处理 → 404）
    assert real_app_client.post("/api/__nope__", headers={"Origin": "http://testserver"}).status_code == 404
    assert real_app_client.post("/api/__nope__").status_code == 404
    # 主服代理子服的请求带 X-Panel-Token，不做 Origin 校验
    assert real_app_client.post(
        "/api/__nope__", headers={"Origin": "https://master.example", "X-Panel-Token": "x"}
    ).status_code == 404
    # GET 不校验
    assert real_app_client.get("/api/__nope__", headers={"Origin": "https://evil.example"}).status_code == 404


def test_security_headers_present(real_app_client):
    resp = real_app_client.get("/api/__nope__")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["Referrer-Policy"] == "same-origin"


def test_api_docs_require_login(real_app_client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        resp = real_app_client.get(path, follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"].endswith("/login")


def test_proxy_does_not_forward_browser_origin():
    from guguwebui.panel_merge.proxy import _filter_outbound_request_headers

    app = FastAPI()
    out = {}

    @app.post("/x")
    async def _x(request: Request):
        out.update(_filter_outbound_request_headers(request))
        return {}

    with TestClient(app) as client:
        client.post("/x", headers={"Origin": "https://m.example", "Referer": "https://m.example/a", "X-Forwarded-For": "6.6.6.6"})
    lowered = {k.lower() for k in out}
    assert not lowered & {"origin", "referer", "x-forwarded-for", "cookie", "host"}


def test_panel_token_compare_requires_enabled_exact_match():
    from guguwebui.dependencies.auth import _panel_token_enabled

    tokens = [{"token": "abc", "enabled": True}, {"token": "off", "enabled": False}, {"token": ""}]
    assert _panel_token_enabled("abc", tokens)
    assert not _panel_token_enabled("ab", tokens)
    assert not _panel_token_enabled("off", tokens)
    assert not _panel_token_enabled("", tokens)


# --------------------------------------------------------------------------
# 3. TOTP
# --------------------------------------------------------------------------

# RFC 6238 附录 B（SHA1）：密钥 "12345678901234567890"，取 8 位结果的后 6 位
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    "ts,expected",
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_totp_rfc6238_vectors(ts, expected):
    assert totp.code_at_step(_RFC_SECRET, ts // 30) == expected
    assert totp.match_step(_RFC_SECRET, expected, now=ts) == ts // 30


def test_totp_rejects_malformed_codes():
    assert totp.match_step(_RFC_SECRET, "", now=59) is None
    assert totp.match_step(_RFC_SECRET, "28708", now=59) is None
    assert totp.match_step(_RFC_SECRET, "abcdef", now=59) is None


def test_provisioning_uri():
    uri = totp.provisioning_uri("ABC", "alice")
    assert uri.startswith("otpauth://totp/GUGU%20WebUI%3Aalice?")
    assert "secret=ABC" in uri and "issuer=GUGU+WebUI" in uri


_USER = "totp_test_user"
_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def totp_user():
    user_db["user"][_USER] = hash_password(_PASSWORD)
    user_db.save()
    secret = totp.begin_setup(_USER)
    assert totp.confirm_setup(_USER, totp.code_at_step(secret, totp.current_step()))
    # 让确认用掉的时间步不影响后续登录：回拨 last_step
    user_db["totp"][_USER]["last_step"] = 0
    user_db.save()
    yield secret
    totp.disable(_USER)
    user_db["user"].pop(_USER, None)
    for tok in [t for t, r in user_db["token"].items() if isinstance(r, dict) and r.get("user_name") == _USER]:
        user_db["token"].pop(tok, None)
    user_db.save()


def _two_step_client(config: dict | None = None):
    from guguwebui.services.auth_service import AuthService

    service = AuthService(_ServerStub({"disable_other_admin": False, **(config or {})}))
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.post("/api/login")
    async def _login(request: Request, account: str = Form(""), password: str = Form("")):
        return await service.login(request, account, password, "", False)

    @app.post("/api/login/totp")
    async def _totp(request: Request, ticket: str = Form(""), code: str = Form("")):
        return await service.complete_totp_login(request, ticket, code)

    return TestClient(app, client=("10.0.0.1", 50000))


def test_password_login_requires_totp_when_enabled(totp_user):
    with _two_step_client() as client:
        resp = client.post("/api/login", data={"account": _USER, "password": _PASSWORD})
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "totp_required"
        assert "token=" not in resp.headers.get("set-cookie", "")
        ticket = body["data"]["ticket"]

        bad = client.post("/api/login/totp", data={"ticket": ticket, "code": "000000"})
        assert bad.status_code == 401 and bad.json()["code"] == "totp_invalid"

        code = totp.code_at_step(totp_user, totp.current_step())
        ok = client.post("/api/login/totp", data={"ticket": ticket, "code": code})
        assert ok.status_code == 200, ok.json()
        token = _cookie_token(dict(ok.headers))
        assert token and user_db["token"][token]["user_name"] == _USER

        # 票据一次性
        again = client.post("/api/login/totp", data={"ticket": ticket, "code": code})
        assert again.json()["code"] == "totp_ticket_invalid"

        # 同一验证码不能在新票据上重放
        ticket2 = client.post("/api/login", data={"account": _USER, "password": _PASSWORD}).json()["data"]["ticket"]
        replay = client.post("/api/login/totp", data={"ticket": ticket2, "code": code})
        assert replay.status_code == 401


def test_totp_ticket_dies_after_max_attempts(totp_user):
    with _two_step_client() as client:
        ticket = client.post("/api/login", data={"account": _USER, "password": _PASSWORD}).json()["data"]["ticket"]
        codes = []
        for _ in range(totp.LOGIN_TICKET_MAX_ATTEMPTS):
            codes.append(client.post("/api/login/totp", data={"ticket": ticket, "code": "000000"}).json()["code"])
        assert codes[-1] == "totp_ticket_invalid"
        good = totp.code_at_step(totp_user, totp.current_step())
        assert client.post("/api/login/totp", data={"ticket": ticket, "code": good}).status_code == 401


def test_totp_setup_confirmation_is_rate_limited():
    from guguwebui.utils import rate_limit

    for _ in range(10):
        rate_limit.record_totp_setup_failure(_USER)
    assert rate_limit.totp_setup_retry_after(_USER) > 0
    rate_limit.reset_totp_setup_failures(_USER)
    assert rate_limit.totp_setup_retry_after(_USER) == 0


def test_wrong_password_does_not_reveal_super_admin():
    """disable_other_admin 下，密码错误一律 401；只有密码正确的非超管才会得到 403。"""
    user_db["user"][_USER] = hash_password(_PASSWORD)
    user_db.save()
    try:
        with _two_step_client({"disable_other_admin": True, "super_admin_account": "someone_else"}) as client:
            wrong = client.post("/api/login", data={"account": _USER, "password": "nope-nope"})
            assert wrong.status_code == 401
            right = client.post("/api/login", data={"account": _USER, "password": _PASSWORD})
            assert right.status_code == 403
    finally:
        user_db["user"].pop(_USER, None)
        user_db.save()


def test_secure_cookie_behind_https_proxy():
    user_db["user"][_USER] = hash_password(_PASSWORD)
    user_db.save()
    try:
        with _two_step_client({"trusted_proxies": ["10.0.0.1"]}) as client:
            resp = client.post(
                "/api/login",
                data={"account": _USER, "password": _PASSWORD},
                headers={"X-Forwarded-Proto": "https"},
            )
            assert resp.status_code == 200
            cookie = resp.headers["set-cookie"].lower()
            assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        with _two_step_client() as client:
            # 未配置可信代理时不采信 X-Forwarded-Proto
            resp = client.post(
                "/api/login",
                data={"account": _USER, "password": _PASSWORD},
                headers={"X-Forwarded-Proto": "https"},
            )
            assert "secure" not in resp.headers["set-cookie"].lower()
    finally:
        user_db["user"].pop(_USER, None)
        for tok in [t for t, r in user_db["token"].items() if isinstance(r, dict) and r.get("user_name") == _USER]:
            user_db["token"].pop(tok, None)
        user_db.save()


def test_temp_code_global_limit_spans_ips():
    from guguwebui.utils import rate_limit

    for _ in range(100):
        rate_limit.record_temp_code_failure()
    assert rate_limit.temp_code_retry_after() > 0


def test_config_validator_discards_invalid_allowed_origins():
    from guguwebui.utils.config_validator import ConfigValidator

    validator = ConfigValidator()
    corrected = {}
    validator._validate_other_configs(
        {
            "trusted_proxies": [],
            "allowed_origins": ["https://valid.example.com", "mc.example.com/path", "not-a-url"],
        },
        corrected,
    )
    assert corrected["allowed_origins"] == ["https://valid.example.com"]
    origin_warnings = [warning for warning in validator.warnings if "allowed_origins" in warning]
    assert len(origin_warnings) == 2


# --------------------------------------------------------------------------
# 5. QQ 扫码 code 绑定会话
# --------------------------------------------------------------------------


@pytest.fixture()
def qr_client(monkeypatch):
    from guguwebui import web_server
    from guguwebui.services.auth_service import AuthService
    from guguwebui.services.qq_qr_login_service import QQQRCodeLoginService

    app = web_server.app
    saved = {k: getattr(app.state, k, None) for k in ("config_service", "auth_service")}
    config = {"disable_other_admin": False, "qq_qr_login_enabled": True}
    app.state.config_service = _ConfigStub(**config)
    app.state.auth_service = AuthService(_ServerStub(config))

    counter = iter(range(1000))
    monkeypatch.setattr(
        QQQRCodeLoginService, "request_login_code",
        staticmethod(lambda: {"code": f"code{next(counter)}", "qrUrl": "https://h5.qzone.qq.com/x"}),
    )
    monkeypatch.setattr(
        QQQRCodeLoginService, "query_status", staticmethod(lambda code: {"state": "ok", "uin": _USER})
    )
    user_db["user"][_USER] = hash_password(_PASSWORD)
    user_db.save()
    try:
        yield app
    finally:
        for k, v in saved.items():
            setattr(app.state, k, v)
        user_db["user"].pop(_USER, None)
        for tok in [t for t, r in user_db["token"].items() if isinstance(r, dict) and r.get("user_name") == _USER]:
            user_db["token"].pop(tok, None)
        user_db.save()


def test_qq_qr_code_must_come_from_same_session(qr_client):
    with TestClient(qr_client) as attacker, TestClient(qr_client) as victim:
        foreign_code = victim.post("/api/login/qq_qr/start").json()["code"]
        resp = attacker.get("/api/login/qq_qr/status", params={"code": foreign_code})
        assert resp.status_code == 403
        assert resp.json()["code"] == "qq_qr_session_mismatch"

        own = attacker.post("/api/login/qq_qr/start").json()["code"]
        ok = attacker.get("/api/login/qq_qr/status", params={"code": own})
        assert ok.status_code == 200, ok.json()
        assert _cookie_token(dict(ok.headers))
        # code 用完即从会话移除
        assert attacker.get("/api/login/qq_qr/status", params={"code": own}).status_code == 403


def test_qq_qr_can_be_disabled(qr_client):
    qr_client.state.config_service = _ConfigStub(qq_qr_login_enabled=False)
    with TestClient(qr_client) as client:
        assert client.post("/api/login/qq_qr/start").json()["code"] == "qq_qr_disabled"
        opts = client.get("/api/login/options").json()["data"]
        assert opts["qq_qr_enabled"] is False


def test_qq_qr_respects_totp(qr_client, totp_user):
    with TestClient(qr_client) as client:
        code = client.post("/api/login/qq_qr/start").json()["code"]
        resp = client.get("/api/login/qq_qr/status", params={"code": code})
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "totp_required" and body["state"] == "totp_required"
        assert "token=" not in resp.headers.get("set-cookie", "")


def test_qq_qr_start_is_rate_limited(qr_client):
    with TestClient(qr_client) as client:
        statuses = [client.post("/api/login/qq_qr/start").status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10 and statuses[10] == 429


def test_qq_qr_status_is_rate_limited(qr_client, monkeypatch):
    from guguwebui.services.qq_qr_login_service import QQQRCodeLoginService

    calls = 0

    def query_status(code):
        nonlocal calls
        calls += 1
        return {"state": "wait"}

    monkeypatch.setattr(QQQRCodeLoginService, "query_status", staticmethod(query_status))
    with TestClient(qr_client) as client:
        code = client.post("/api/login/qq_qr/start").json()["code"]
        statuses = [client.get("/api/login/qq_qr/status", params={"code": code}).status_code for _ in range(101)]
    assert statuses[:100] == [200] * 100
    assert statuses[100] == 429
    assert calls == 100
