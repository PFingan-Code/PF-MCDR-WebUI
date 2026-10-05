"""API 级端到端冒烟：用真实路由表 + 最小服务桩走通代表性主流程。

覆盖可自动化部分（真实浏览器/真实 MCDR 依赖登录与游戏内命令的
流程无法在纯测试环境复现，等价地用登录态依赖覆盖 + 服务桩代替）：
- 登录态（/api/auth/me）→ 仪表盘数据（/api/server/status）
- 本地插件开关（PUT /api/plugins/{id}/enabled）
- pip 安装任务（POST /api/pip/tasks → GET /api/pip/tasks/{id}，终态）
- 公开聊天消息分页（GET /api/chat/messages）
- 玩家列表分页（GET /api/players，data.items 契约）
- 模组上传 409 冲突错误体（data.warnings 透传）
- 操作审计分页（GET /api/audit_logs）
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from guguwebui.structures import BusinessException


@pytest.fixture
def client():
    from guguwebui.web_server import app
    from guguwebui.dependencies.auth import (
        get_current_admin,
        get_current_user,
        get_current_user_or_chat_session,
        get_super_admin,
    )

    # 让 TestClient 以“已登录管理员/超管”身份访问，跳过 cookie/会话
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "admin",
        "token": "test-token",
    }
    # /server/status 等接口使用“登录态或公开聊天页会话”的复合依赖
    app.dependency_overrides[get_current_user_or_chat_session] = lambda: {
        "username": "admin",
        "token": "test-token",
    }
    app.dependency_overrides[get_current_admin] = lambda: {
        "username": "admin",
        "token": "test-token",
    }
    app.dependency_overrides[get_super_admin] = lambda: {
        "username": "admin",
        "token": "test-token",
    }

    class _Config:
        def get_config(self):
            return {
                "super_admin_account": "admin",
                "disable_other_admin": False,
                "panel_role": "master",
                "mod_upload_max_bytes": 1024 * 1024,
            }

    class _ServerService:
        async def get_server_status(self):
            return {"online": True, "version": "v1.9.0", "players": "3/20"}

    class _PluginService:
        def get_plugins_list(self):
            return [
                {
                    "id": "guguwebui",
                    "name": "GUGU WebUI",
                    "version": "1.9.0",
                    "status": "loaded",
                },
                {"id": "demo_plugin", "name": "Demo", "version": "1.0", "status": "unloaded"},
            ]

        def toggle_plugin(self, plugin_id, enabled):
            return {"status": "success", "message": f"plugin {plugin_id} toggled"}

        def get_task_status(self, task_id):
            return None

    class _PipService:
        async def start_task(self, action, package):
            assert action in ("install", "uninstall")
            assert package
            return "task-123"

        def get_task(self, task_id):
            if task_id != "task-123":
                return None
            return {
                "id": task_id,
                "package": "example-pkg",
                "action": "install",
                "status": "completed",  # 终态枚举 running|completed|failed
                "progress": 100,
                "message": "安装完成",
                "start_time": 1700000000,
                "end_time": 1700000010,
                "access_time": 1700000010,
                "all_messages": ["Collecting example-pkg", "Successfully installed"],
                "error_messages": [],
            }

    class _ChatService:
        async def get_messages(self, **kwargs):
            return {
                "items": [
                    {
                        "id": 2,
                        "player_id": "Steve",
                        "message": "hi",
                        "timestamp": 1700000001,
                        "is_plugin": False,
                        "is_rtext": False,
                        "message_source": "game",
                    }
                ],
                "total": 1,
                "offset": kwargs.get("offset", 0),
                "limit": kwargs.get("limit", 50),
                "has_more": False,
            }

        async def get_new_messages(self, **kwargs):
            return {
                "messages": [],
                "last_message_id": 2,
                "online": {"web": [], "game": ["Steve"], "bot": []},
            }

    class _PlayerService:
        def get_players(self, search="", filter_="all", offset=0, limit=50, exclude_bots=False):
            return {
                "players": [
                    {
                        "name": "Steve",
                        "uuid": "069a79f4-44e9-4726-a5be-fca90e38aaf5",
                        "online": True,
                        "is_bot": False,
                        "ips": ["127.0.0.1"],
                        "ip": "127.0.0.1",
                        "session_seconds": 3600,
                        "is_op": False,
                        "whitelisted": True,
                        "banned": False,
                    }
                ],
                "total": 1,
                "offset": offset,
                "limit": limit,
                "online_count": 1,
                "bot_count": 0,
                "server_running": True,
            }

        def get_whitelist(self):
            return {
                "status": "success",
                "enabled": True,
                "members": [{"name": "Steve", "uuid": "069a79f4-44e9-4726-a5be-fca90e38aaf5"}],
                "server_running": True,
            }

        def get_stats_overview(self, range_key="1h", exclude_bots=False):
            return {
                "range": range_key,
                "current_online": 1,
                "avg_online": 0.5,
                "peak_online": 1,
                "peak_ts": 1700000000,
                "active_players": 1,
                "total_sessions": 1,
            }

        def get_stats_online_history(self, range_key="1h", exclude_bots=False):
            return {
                "range": range_key,
                "sample": "1m",
                "points": [{"t": 1700000000, "value": 1}],
            }

        def get_stats_daily(self, range_key="7d", exclude_bots=False):
            return {
                "range": range_key,
                "points": [{"date": "2026-09-01", "players": 1, "sessions": 1, "playtime": 3600}],
            }

        def get_stats_players(self, exclude_bots=False, limit=50):
            return {
                "players": [
                    {
                        "name": "Steve",
                        "online": True,
                        "sessions": 1,
                        "total_playtime": 3600,
                        "avg_session": 3600,
                        "last_seen": 1700000000,
                    }
                ],
                "total": 1,
            }

    class _ModService:
        async def list_mods(self):
            return {"status": "success", "mods": [], "server_running": True, "mods_path": "/tmp"}

        def list_trash(self):
            return {"status": "success", "items": []}

        async def upload(self, file, enabled, max_bytes, acknowledge_warnings):
            if not acknowledge_warnings:
                raise BusinessException(
                    "模组存在兼容性警告",
                    status_code=409,
                    code="mod_conflict",
                    data={"warnings": [{"code": "mixins", "message": "包含 mixins，需重启生效"}]},
                )
            return {"status": "success", "message": "ok"}

    app.state.config_service = _Config()
    app.state.server_service = _ServerService()
    app.state.plugin_service = _PluginService()
    app.state.pip_service = _PipService()
    app.state.chat_service = _ChatService()
    app.state.player_service = _PlayerService()
    app.state.mod_service = _ModService()

    yield TestClient(app)

    app.dependency_overrides.clear()


def _unwrap(resp):
    body = resp.json()
    assert body.get("status") == "success", body
    return body.get("data")


def test_smoke_login_and_dashboard_status(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    me = _unwrap(resp)
    assert me["username"] == "admin"
    assert me["is_admin"] is True
    assert me["is_super_admin"] is True

    resp = client.get("/api/server/status")
    assert resp.status_code == 200
    st = _unwrap(resp)
    assert st["online"] is True
    assert st["version"]


def test_smoke_local_plugins_list_and_toggle(client):
    resp = client.get("/api/plugins")
    assert resp.status_code == 200
    plugins = _unwrap(resp)["plugins"]
    assert any(p["id"] == "guguwebui" for p in plugins)

    resp = client.put("/api/plugins/demo_plugin/enabled", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["message"]


def test_smoke_pip_task_lifecycle(client):
    # 创建任务 → task_id
    resp = client.post("/api/pip/tasks", json={"action": "install", "package": "example-pkg"})
    assert resp.status_code == 200
    task_id = _unwrap(resp)["task_id"]
    assert task_id == "task-123"

    # 轮询查询 → 状态必须为终态（completed/failed，而不是成功永不结束）
    resp = client.get(f"/api/pip/tasks/{task_id}")
    assert resp.status_code == 200
    info = _unwrap(resp)["task_info"]
    assert info["status"] in ("running", "completed", "failed")
    assert info["status"] == "completed"
    assert info["all_messages"]

    # 不存在的任务 → 404 task_not_found
    resp = client.get("/api/pip/tasks/no-such-task")
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] == "task_not_found"


def test_smoke_chat_messages_pagination(client):
    resp = client.get("/api/chat/messages", params={"limit": 50, "offset": 0})
    assert resp.status_code == 200
    page = _unwrap(resp)
    assert page["items"][0]["player_id"] == "Steve"
    assert page["total"] == 1
    assert "timestamp" in page["items"][0]


def test_smoke_players_pagination_contract(client):
    resp = client.get("/api/players", params={"limit": 50, "offset": 0})
    assert resp.status_code == 200
    page = _unwrap(resp)
    # 分页统一 items 键
    assert isinstance(page["items"], list)
    assert page["items"][0]["name"] == "Steve"
    assert page["total"] == 1
    assert page["server_running"] is True


def test_smoke_player_stats_endpoints(client):
    """玩家在线统计 4 个端点：统一外壳 + exclude_bots 参数透传。"""
    resp = client.get("/api/players/stats/overview", params={"range": "1h", "exclude_bots": "true"})
    assert resp.status_code == 200
    ov = _unwrap(resp)
    assert ov["range"] == "1h"
    assert ov["current_online"] == 1
    assert ov["peak_ts"] == 1700000000

    resp = client.get("/api/players/stats/online-history", params={"range": "6h"})
    assert resp.status_code == 200
    hist = _unwrap(resp)
    assert hist["sample"] == "1m"
    assert hist["points"][0]["value"] == 1

    resp = client.get("/api/players/stats/daily", params={"range": "7d", "exclude_bots": "true"})
    assert resp.status_code == 200
    daily = _unwrap(resp)
    assert daily["points"][0]["date"] == "2026-09-01"
    assert daily["points"][0]["playtime"] == 3600

    resp = client.get("/api/players/stats/players", params={"exclude_bots": "true", "limit": 50})
    assert resp.status_code == 200
    rank = _unwrap(resp)
    assert rank["players"][0]["name"] == "Steve"
    assert rank["players"][0]["total_playtime"] == 3600

    # 非法 range → 422 统一错误体
    resp = client.get("/api/players/stats/overview", params={"range": "999y"})
    assert resp.status_code == 422
    assert resp.json()["status"] == "error"


def test_smoke_mod_upload_conflict_error_body(client):
    resp = client.post(
        "/api/mods/upload",
        files={"file": ("bad.jar", b"\x50\x4b\x03\x04fake", "application/java-archive")},
        data={"enabled": "true", "acknowledge_warnings": "false"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] == "mod_conflict"
    # 业务上下文 data.warnings 透传（前端据此弹出二次确认）
    warnings = body.get("data", {}).get("warnings", [])
    assert any(w["code"] == "mixins" for w in warnings)


def test_smoke_audit_logs_pagination(client):
    import guguwebui.routers.audit_router as audit_module

    rows = [
        {
            "id": "r1",
            "ts": 1700000001.0,
            "operation_type": "mcdr.send_command",
            "summary": "执行命令",
            "detail": {},
            "account": {"username": "admin"},
        }
    ]
    audit_module.list_records = lambda offset=0, limit=50, newest_first=True: (rows, 1)
    try:
        resp = client.get("/api/audit_logs", params={"limit": 50, "offset": 0})
        assert resp.status_code == 200
        page = _unwrap(resp)
        assert page["total"] == 1
        assert page["items"][0]["operation_type"] == "mcdr.send_command"
        # ts 整型 epoch 秒
        assert page["items"][0]["ts"] == 1700000001
    finally:
        delattr(audit_module, "list_records")
