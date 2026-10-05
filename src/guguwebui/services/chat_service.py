import datetime
import random
import secrets
import string
import time
from typing import Any, Dict, Optional, Tuple

from guguwebui.constant import DEFALUT_CONFIG, user_db
from guguwebui.state import RCON_ONLINE_CACHE, WEB_ONLINE_PLAYERS
from guguwebui.structures import BusinessException
from guguwebui.utils.auth_util import (
    cleanup_chat_verifications,
    hash_password,
    verify_password,
)
from guguwebui.utils.chat_logger import ChatLogger
from guguwebui.utils.mc_util import (
    create_chat_logger_status_rtext,
    create_chat_message_rtext,
    get_bot_list,
    get_player_uuid,
)


class ChatService:
    def __init__(self, server, config_service=None):
        self.server = server
        self.config_service = config_service
        self.chat_logger = ChatLogger()

    def generate_verification_code(self) -> Tuple[str, int]:
        """生成聊天页验证码"""
        config = self.config_service.get_config()
        if not config.get("public_chat_enabled", False):
            raise BusinessException(
                "公开聊天页未启用", status_code=403, code="public_chat_disabled"
            )

        cleanup_chat_verifications()

        code = "".join(random.choices(string.digits + string.ascii_uppercase, k=6))
        expire_minutes = config.get("chat_verification_expire_minutes", 10)
        expire_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=expire_minutes
        )

        user_db["chat_verification"][code] = {
            "player_id": None,
            "expire_time": str(expire_time),
            "used": False,
        }
        user_db.save()

        self.server.logger.debug(f"生成聊天页验证码: {code}")
        return code, expire_minutes

    def check_verification_status(self, code: str) -> Dict[str, Any]:
        """检查验证码验证状态"""
        if not code:
            raise BusinessException("验证码不能为空", code="verification_code_required")

        if code not in user_db["chat_verification"]:
            raise BusinessException(
                "验证码不存在", status_code=404, code="verification_not_found"
            )

        verification = user_db["chat_verification"][code]
        expire_time = datetime.datetime.fromisoformat(
            verification["expire_time"].replace("Z", "+00:00")
        )

        if datetime.datetime.now(datetime.timezone.utc) > expire_time:
            del user_db["chat_verification"][code]
            user_db.save()
            raise BusinessException("验证码已过期", code="verification_expired")

        result = {"verified": False, "message": "验证码尚未在游戏内验证"}
        if verification.get("player_id"):
            result = {"verified": True, "player_id": verification["player_id"]}
        return result

    async def set_user_password(
        self, code: str, password: str, player_id: str = None
    ) -> Dict[str, Any]:
        """设置聊天页用户密码（成功后直接签发会话，前端无需再走登录）"""
        password = password.replace("<", "").replace(">", "")

        if not code or not password:
            raise BusinessException("验证码和密码不能为空", code="invalid_request")

        if len(password) < 6:
            raise BusinessException("密码长度至少6位", code="password_too_short")

        if code not in user_db["chat_verification"]:
            raise BusinessException(
                "验证码不存在", status_code=404, code="verification_not_found"
            )

        verification = user_db["chat_verification"][code]
        expire_time = datetime.datetime.fromisoformat(
            verification["expire_time"].replace("Z", "+00:00")
        )

        if datetime.datetime.now(datetime.timezone.utc) > expire_time:
            del user_db["chat_verification"][code]
            user_db.save()
            raise BusinessException("验证码已过期", code="verification_expired")

        if verification.get("used") and (verification.get("player_id") is None):
            raise BusinessException("验证码已被使用", code="verification_already_used")

        if verification.get("player_id") is None:
            raise BusinessException("验证码尚未在游戏内验证", code="verification_pending")

        bound_player = str(verification["player_id"])
        expected_player = (player_id or "").strip()
        if expected_player and expected_player != bound_player:
            raise BusinessException("验证码与玩家不匹配", code="verification_mismatch")
        player_id = bound_player

        user_db["chat_users"][player_id] = {
            "password": hash_password(password),
            "created_time": str(datetime.datetime.now(datetime.timezone.utc)),
        }
        user_db.save()

        try:
            del user_db["chat_verification"][code]
            user_db.save()
        except Exception:
            pass

        self.server.logger.debug(f"聊天页用户 {player_id} 设置密码成功")

        # 签发会话（自动登录）
        session_id = secrets.token_hex(16)
        config = self.config_service.get_config()
        expire_hours = config.get("chat_session_expire_hours", 24)
        expire_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=expire_hours
        )
        user_db["chat_sessions"][session_id] = {
            "player_id": player_id,
            "expire_time": str(expire_time),
            "ip": "unknown",
            "last_sent_ms": 0,
        }
        user_db.save()

        result = {
            "message": "密码设置成功",
            "player_id": player_id,
            "session_id": session_id,
        }
        try:
            uuid_val = await get_player_uuid(player_id, self.server)
            if uuid_val:
                result["uuid"] = uuid_val
        except Exception:
            pass
        return result

    async def login(
        self, player_id: str, password: str, client_ip: str
    ) -> Dict[str, Any]:
        """聊天页用户登录"""
        player_id = player_id.replace("<", "").replace(">", "")
        password = password.replace("<", "").replace(">", "")

        if not player_id or not password:
            raise BusinessException("玩家ID和密码不能为空", code="credentials_required")

        if player_id not in user_db["chat_users"]:
            raise BusinessException("用户不存在", status_code=404, code="user_not_found")

        if not verify_password(password, user_db["chat_users"][player_id]["password"]):
            raise BusinessException("密码错误", status_code=401, code="invalid_password")

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        active_ips = set()
        sessions_to_delete = []
        for sid, sess in list(user_db["chat_sessions"].items()):
            try:
                expire_time = datetime.datetime.fromisoformat(
                    sess["expire_time"].replace("Z", "+00:00")
                )
                if now_utc > expire_time:
                    sessions_to_delete.append(sid)
                    continue
                if sess.get("player_id") == player_id:
                    active_ips.add(sess.get("ip") or "unknown")
            except Exception:
                sessions_to_delete.append(sid)

        for sid in sessions_to_delete:
            try:
                del user_db["chat_sessions"][sid]
            except KeyError:
                pass
        if sessions_to_delete:
            user_db.save()

        if len(active_ips) >= 2 and client_ip not in active_ips:
            raise BusinessException(
                "该账号登录IP已达上限，请先在其他设备退出或等待会话过期",
                status_code=429,
                code="ip_limit_exceeded",
            )

        session_id = secrets.token_hex(16)
        config = self.config_service.get_config()
        expire_hours = config.get("chat_session_expire_hours", 24)
        expire_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=expire_hours
        )

        user_db["chat_sessions"][session_id] = {
            "player_id": player_id,
            "expire_time": str(expire_time),
            "ip": client_ip,
            "last_sent_ms": 0,
        }
        user_db.save()

        self.server.logger.debug(f"聊天页用户 {player_id} 登录成功")

        result = {
            "message": "登录成功",
            "session_id": session_id,
            "player_id": player_id,
        }
        try:
            uuid_val = await get_player_uuid(player_id, self.server)
            if uuid_val:
                result["uuid"] = uuid_val
        except Exception:
            pass
        return result

    async def check_session(self, session_id: str) -> Dict[str, Any]:
        """检查聊天页会话状态"""
        if not session_id:
            raise BusinessException("会话ID不能为空", code="session_id_required")

        if session_id not in user_db["chat_sessions"]:
            raise BusinessException(
                "会话不存在", status_code=404, code="session_not_found"
            )

        session = user_db["chat_sessions"][session_id]
        expire_time = datetime.datetime.fromisoformat(
            session["expire_time"].replace("Z", "+00:00")
        )

        if datetime.datetime.now(datetime.timezone.utc) > expire_time:
            del user_db["chat_sessions"][session_id]
            user_db.save()
            raise BusinessException(
                "会话已过期", status_code=401, code="session_expired"
            )

        player_id = session["player_id"]
        result = {"valid": True, "player_id": player_id}
        try:
            uuid_val = await get_player_uuid(player_id, self.server)
            if uuid_val:
                result["uuid"] = uuid_val
        except Exception:
            pass
        return result

    def logout(self, session_id: str):
        """聊天页用户退出登录"""
        if session_id in user_db["chat_sessions"]:
            del user_db["chat_sessions"][session_id]
            user_db.save()
        return {"message": "退出登录成功"}

    async def get_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        after_id: Optional[int] = None,
        before_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """获取聊天消息"""
        if after_id is not None:
            messages = self.chat_logger.get_new_messages(after_id)
        elif before_id is not None:
            messages = self.chat_logger.get_messages(limit=limit, before_id=before_id)
        else:
            messages = self.chat_logger.get_messages(limit, offset)

        try:
            uuid_cache = {}
            need_uuid_players = set()
            for m in messages:
                pid = m.get("player_id")
                if pid and not m.get("is_plugin", False) and m.get("uuid") is None:
                    need_uuid_players.add(pid)

            if need_uuid_players:
                for pid in need_uuid_players:
                    uuid_cache[pid] = await get_player_uuid(pid, self.server)

            for m in messages:
                pid = m.get("player_id")
                if pid and m.get("uuid") is None and not m.get("is_plugin", False):
                    if pid in uuid_cache:
                        m["uuid"] = uuid_cache[pid]
        except Exception:
            pass

        # 分页列表统一 items 键 + total/offset/limit（has_more 保留用于前端续页判断）
        return {
            "items": messages,
            "total": self.chat_logger.get_message_count(),
            "offset": offset,
            "limit": limit,
            "has_more": len(messages) == limit,
        }

    async def get_new_messages(
        self, after_id: int = 0, player_id_heartbeat: str = None
    ) -> Dict[str, Any]:
        """获取新消息（基于最后消息ID）"""
        messages = self.chat_logger.get_new_messages(after_id)

        try:
            uuid_cache = {}
            for m in messages:
                pid = m.get("player_id")
                if not pid:
                    continue
                if pid not in uuid_cache:
                    uuid_cache[pid] = await get_player_uuid(pid, self.server)
                m["uuid"] = uuid_cache[pid]
        except Exception:
            pass

        if player_id_heartbeat:
            WEB_ONLINE_PLAYERS[player_id_heartbeat] = int(time.time()) + 5

        online_web = [
            pid
            for pid, until in WEB_ONLINE_PLAYERS.items()
            if until >= int(time.time())
        ]
        online_game = []

        if self.server.is_rcon_running():
            now_sec = int(time.time())
            if RCON_ONLINE_CACHE["dirty"] or (
                now_sec - int(RCON_ONLINE_CACHE["ts"]) >= 300
            ):
                try:
                    feedback = self.server.rcon_query("list")
                    names = set()
                    if isinstance(feedback, str) and ":" in feedback:
                        names_part = feedback.split(":", 1)[1].strip()
                        if names_part:
                            for name in [
                                n.strip() for n in names_part.split(",") if n.strip()
                            ]:
                                names.add(name)
                    RCON_ONLINE_CACHE["names"] = names
                    RCON_ONLINE_CACHE["ts"] = now_sec
                    RCON_ONLINE_CACHE["dirty"] = False
                except Exception:
                    pass
            online_game = list(RCON_ONLINE_CACHE["names"])

        online_bot = get_bot_list(self.server)

        return {
            "messages": messages,
            "last_message_id": self.chat_logger.get_last_message_id(),
            "online": {"web": online_web, "game": online_game, "bot": online_bot},
        }

    def clear_messages(self):
        """清空聊天消息"""
        self.chat_logger.clear_messages()
        status_msg = create_chat_logger_status_rtext("clear", True)
        self.server.logger.info(status_msg)
        return {"message": "聊天消息已清空"}

    async def send_message(
        self, message: str, player_id: str, session_id: str, is_admin: bool = False
    ):
        """发送聊天消息到游戏"""
        if not message:
            raise BusinessException("消息内容不能为空", code="message_required")
        if not player_id:
            raise BusinessException("玩家ID无效", code="player_id_required")

        if not is_admin:
            if not session_id or session_id not in user_db["chat_sessions"]:
                raise BusinessException(
                    "会话无效或已过期，请重新登录",
                    status_code=401,
                    code="invalid_session",
                )

            session = user_db["chat_sessions"][session_id]
            if session["player_id"] != player_id:
                raise BusinessException(
                    "玩家ID与会话不匹配",
                    status_code=401,
                    code="session_player_mismatch",
                )

            expire_time = datetime.datetime.fromisoformat(
                session["expire_time"].replace("Z", "+00:00")
            )
            if datetime.datetime.now(datetime.timezone.utc) > expire_time:
                del user_db["chat_sessions"][session_id]
                user_db.save()
                raise BusinessException(
                    "会话已过期，请重新登录",
                    status_code=401,
                    code="session_expired",
                )

            now_ms = int(time.time() * 1000)
            if now_ms - session.get("last_sent_ms", 0) < 2000:
                raise BusinessException(
                    "发送过于频繁，请稍后再试",
                    status_code=429,
                    code="rate_limited",
                )
            session["last_sent_ms"] = now_ms
            user_db.save()

            # 该开关只约束公开聊天页；WebUI 管理端聊天页（is_admin）已通过登录态鉴权，
            # 且本就有执行服务器指令的权限，不应被公开页开关拦截。
            config = self.config_service.get_config()
            if not config.get("public_chat_to_game_enabled", False):
                raise BusinessException(
                    "聊天到游戏功能未启用",
                    status_code=403,
                    code="chat_to_game_disabled",
                )

        player_uuid = await get_player_uuid(player_id, self.server) or "未知"
        rtext_message = create_chat_message_rtext(player_id, message, player_uuid)

        # 分发事件
        try:
            from mcdreforged.api.event import LiteralEvent

            event_data = (
                "webui",
                player_id,
                player_uuid,
                message,
                session_id,
                int(time.time()),
            )
            self.server.dispatch_event(
                LiteralEvent("webui.chat_message_sent"), event_data
            )
        except Exception as e:
            self.server.logger.error(f"分发WebUI聊天消息事件失败: {e}")

        WEB_ONLINE_PLAYERS[player_id] = int(time.time()) + 5
        self.chat_logger.add_message(
            player_id,
            message,
            rtext_data=rtext_message.to_json_object(),
            message_type=1,
            server=self.server,
        )

        # 始终广播。旧实现先用 mcstatus 探测在线人数，探测失败（服务端关闭
        # enable-status、Docker 内端口不可达等）会被当作“无人在线”而静默丢弃消息，
        # 表现为聊天页提示已记录/发送成功，但游戏内收不到。
        try:
            self.server.broadcast(rtext_message)
        except Exception as e:
            self.server.logger.error(f"广播聊天消息到游戏失败: {e}")

        if not self.server.is_server_running():
            return {"message": "已记录（服务器未运行，未能广播到游戏）"}
        if self._get_online_player_count() == 0:
            return {"message": "已记录（当前无在线玩家）"}
        return {"message": "消息发送成功"}

    def _get_online_player_count(self) -> Optional[int]:
        """通过 RCON 获取在线人数；无法确定时返回 None（不得当作 0 处理）。"""
        try:
            if not self.server.is_rcon_running():
                return None
            feedback = self.server.rcon_query("list")
            if not isinstance(feedback, str) or ":" not in feedback:
                return None
            names_part = feedback.split(":", 1)[1].strip()
            return len([n for n in names_part.split(",") if n.strip()])
        except Exception:
            return None
