"""聊天页两条链路的回归测试。

1. 游戏内 `!!webui verify <code>` 必须能被普通玩家执行（此前根节点要求 admin，
   玩家在 `!!webui` 就被“约束未满足”拒绝）；管理类子指令仍需管理员权限。
2. 聊天页消息必须真正广播到游戏：不再以“在线人数探测结果”决定是否广播，
   探测失败（Docker 关闭 enable-status / 端口不可达）时也必须发送。
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Optional

import pytest

from guguwebui.services import chat_service as chat_service_module
from guguwebui.services.chat_service import ChatService
from guguwebui.structures import BusinessException


class _Logger:
    def __init__(self):
        self.messages = []

    def debug(self, message, *args, **kwargs):
        self.messages.append(str(message))

    info = debug
    warning = debug
    error = debug


class _Config:
    def __init__(self, **values):
        self.values = values

    def get_config(self):
        return self.values


class _ChatLoggerStub:
    def __init__(self, data_dir=None):
        self.records = []

    def add_message(self, player_id, message, rtext_data=None, message_type=0, server=None):
        self.records.append({"player_id": player_id, "message": message})


class _Server:
    def __init__(self, *, running=True, rcon_running=False, rcon_reply=None):
        self.logger = _Logger()
        self._running = running
        self._rcon_running = rcon_running
        self._rcon_reply = rcon_reply
        self.broadcasts = []

    def is_server_running(self):
        return self._running

    def is_rcon_running(self):
        return self._rcon_running

    def rcon_query(self, command):
        return self._rcon_reply

    def broadcast(self, text, **kwargs):
        self.broadcasts.append(text)


@pytest.fixture(autouse=True)
def _isolate_chat_service(monkeypatch):
    """隔离文件与网络依赖：不写聊天日志文件，不访问 Mojang API。"""
    monkeypatch.setattr(chat_service_module, "ChatLogger", _ChatLoggerStub)

    async def _fake_uuid(player_name, server_interface=None, use_api=True):
        return "00000000-0000-0000-0000-000000000000"

    monkeypatch.setattr(chat_service_module, "get_player_uuid", _fake_uuid)


def _make_service(server, **config_values):
    return ChatService(server, _Config(**config_values))


def _add_session(session_id: str, player_id: str) -> None:
    from guguwebui.constant import user_db

    expire_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    user_db["chat_sessions"][session_id] = {
        "player_id": player_id,
        "expire_time": str(expire_time),
        "ip": "test",
        "last_sent_ms": 0,
    }
    user_db.save()


def _remove_session(session_id: str) -> None:
    from guguwebui.constant import user_db

    user_db["chat_sessions"].pop(session_id, None)
    user_db.save()


def test_admin_chat_page_reaches_game_when_public_to_game_disabled():
    """WebUI 管理端聊天页不应被「公开聊天页→发送到游戏」开关拦截。"""
    server = _Server()
    service = _make_service(server, public_chat_to_game_enabled=False)

    result = asyncio.run(service.send_message("hello", "admin", "", is_admin=True))

    assert len(server.broadcasts) == 1
    assert result["message"] == "消息发送成功"


def test_public_sender_still_blocked_when_to_game_disabled():
    """公开聊天页仍受开关约束（不能因为放开管理端而失去该限制）。"""
    session_id = "test-session-disabled"
    _add_session(session_id, "Steve")
    try:
        server = _Server()
        service = _make_service(server, public_chat_to_game_enabled=False)

        with pytest.raises(BusinessException) as exc_info:
            asyncio.run(service.send_message("hello", "Steve", session_id, is_admin=False))

        assert exc_info.value.code == "chat_to_game_disabled"
        assert server.broadcasts == []
    finally:
        _remove_session(session_id)


def test_public_sender_allowed_when_to_game_enabled():
    session_id = "test-session-enabled"
    _add_session(session_id, "Steve")
    try:
        server = _Server()
        service = _make_service(server, public_chat_to_game_enabled=True)

        result = asyncio.run(service.send_message("hello", "Steve", session_id, is_admin=False))

        assert len(server.broadcasts) == 1
        assert result["message"] == "消息发送成功"
    finally:
        _remove_session(session_id)


def test_broadcast_happens_when_online_probe_is_unavailable():
    """在线人数探测不可用（Docker 常见）时必须仍然广播。"""
    server = _Server(rcon_running=False)
    service = _make_service(server)

    result = asyncio.run(service.send_message("hello", "Steve", "", is_admin=True))

    assert len(server.broadcasts) == 1
    assert result["message"] == "消息发送成功"
    assert "无在线玩家" not in result["message"]


def test_broadcast_happens_even_when_rcon_reports_zero_players():
    """RCON 明确报告 0 人时也先广播（广播无副作用），仅提示文案不同。"""
    server = _Server(
        rcon_running=True,
        rcon_reply="There are 0 of a max of 20 players online: ",
    )
    service = _make_service(server)

    result = asyncio.run(service.send_message("hello", "Steve", "", is_admin=True))

    assert len(server.broadcasts) == 1
    assert "无在线玩家" in result["message"]


def test_server_not_running_reports_recorded():
    server = _Server(running=False)
    service = _make_service(server)

    result = asyncio.run(service.send_message("hello", "Steve", "", is_admin=True))

    assert "服务器未运行" in result["message"]


# --------------------------------------------------------------------------
# 命令树权限：普通玩家必须能到达 !!webui verify
# --------------------------------------------------------------------------


class _Source:
    def __init__(self, level: int):
        self.level = level

    def has_permission(self, level: int) -> bool:
        return self.level >= level

    def get_permission_level(self) -> int:
        return self.level


class _CommandServerStub:
    def __init__(self):
        self.command_node = None
        self.help_messages = []

    def register_command(self, node):
        self.command_node = node

    def register_help_message(self, prefix, message, permission=None):
        self.help_messages.append((prefix, permission))


def _node_allows(node, level: int) -> bool:
    source = _Source(level)
    return all(requirement.requirement(source) for requirement in node._requirements)


def _literal_child(node, literal: str) -> Optional[object]:
    for child in node.get_children():
        literals = getattr(child, "literals", None)
        if literals and literal in literals:
            return child
    return None


def _build_command_tree():
    from guguwebui import register_command

    server = _CommandServerStub()
    register_command(server, "127.0.0.1", 8000)
    return server


def test_normal_player_can_execute_verify():
    server = _build_command_tree()
    root = server.command_node

    assert _node_allows(root, 1), "根节点 !!webui 不应再要求管理员权限"
    assert _node_allows(root, 0), "根节点不应拦截任何玩家进入子指令"

    verify = _literal_child(root, "verify")
    assert verify is not None, "verify 子指令必须存在"
    assert _node_allows(verify, 1), "普通玩家(user=1)必须能执行 !!webui verify"
    assert _node_allows(verify, 0), "验证码即凭证，verify 不应要求权限等级"


def test_admin_subcommands_keep_admin_permission():
    server = _build_command_tree()
    root = server.command_node

    for name in ("create", "change", "temp"):
        child = _literal_child(root, name)
        assert child is not None, f"{name} 子指令必须存在"
        assert not _node_allows(child, 1), f"{name} 必须保持管理员(3)权限"


def test_verify_help_registered_for_normal_players():
    server = _build_command_tree()

    verify_help = [
        permission
        for prefix, permission in server.help_messages
        if "verify" in prefix
    ]
    assert verify_help, "应注册 !!webui verify 的帮助信息"
    assert all(permission is not None and permission <= 1 for permission in verify_help)
