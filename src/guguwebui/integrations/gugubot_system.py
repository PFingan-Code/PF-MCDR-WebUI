"""
GUGUBot 系统模块 - 为 GUGUBot 提供 WebUI 管理命令
"""
import logging
from typing import Optional

from guguwebui.constant import user_db
from guguwebui.utils.auth_util import (MIN_PASSWORD_LENGTH,
                                       change_user_account,
                                       create_temp_password,
                                       create_user_account, hash_password,
                                       password_too_short, set_user_password)


class WebUISystem:
    """
    WebUI 系统模块，用于在 GUGUBot 中注册 WebUI 管理命令
    
    注意：这个类设计为可以被 GUGUBot 的 BasicSystem 包装使用
    如果 GUGUBot 存在，应该继承 BasicSystem 并注册到 SystemManager
    """
    """WebUI 系统模块，用于在 GUGUBot 中注册 WebUI 管理命令"""

    def __init__(self, server, gugubot_config: Optional[dict] = None):
        """
        初始化 WebUI 系统模块

        Parameters
        ----------
        server : PluginServerInterface
            MCDR 服务器接口
        gugubot_config : dict, optional
            GUGUBot 配置字典
        """
        self.server = server
        self.logger = logging.getLogger("guguwebui.gugubot")
        self.gugubot_config = gugubot_config or {}
        self.system_name = "webui"

    def initialize(self):
        """初始化系统"""
        self.logger.info("WebUI 系统模块已初始化")
    
    def get_help(self) -> list:
        """
        获取系统帮助信息
        
        Returns
        -------
        list
            帮助信息列表
        """
        return [
            "WebUI - 显示帮助",
            "webui 设置密码 <密码> - 设置或更新密码",
            "webui 临时码 - 获取临时码"
        ]

    def _is_admin(self, sender_id: str) -> bool:
        """
        检查是否是 GUGUBot 管理员

        Parameters
        ----------
        sender_id : str
            发送者ID（QQ号）

        Returns
        -------
        bool
            是否为管理员
        """
        try:
            config = getattr(self, "config", None) or self.gugubot_config
            admin_ids = (
                config.get("connector", {})
                .get("QQ", {})
                .get("permissions", {})
                .get("admin_ids", [])
            )
            return str(sender_id) in [str(id) for id in admin_ids]
        except Exception:
            return False

    def _build_message(self, text: str):
        """
        构建消息（使用 MessageBuilder 如果可用，否则使用字典格式）
        
        Parameters
        ----------
        text : str
            消息文本
        
        Returns
        -------
        list
            消息列表
        """
        try:
            # 尝试使用 MessageBuilder
            from gugubot.builder import MessageBuilder
            return [MessageBuilder.text(text)]
        except ImportError:
            # 如果无法导入，使用字典格式
            return [{"type": "text", "data": {"text": text}}]
    
    def _save_qq_nickname(self, qq_id: str, nickname: str):
        """
        保存 QQ 昵称

        Parameters
        ----------
        qq_id : str
            QQ号
        nickname : str
            昵称
        """
        try:
            if "qq_nicknames" not in user_db:
                user_db["qq_nicknames"] = {}
            user_db["qq_nicknames"][str(qq_id)] = nickname
            user_db.save()
        except Exception:
            pass

    async def process_broadcast_info(self, broadcast_info) -> bool:
        """
        处理接收到的消息

        Parameters
        ----------
        broadcast_info
            GUGUBot 的 BroadcastInfo 对象

        Returns
        -------
        bool
            是否已处理该消息（True表示已处理，不传递给其他系统）
        """
        try:
            # 只处理来自 QQ 的消息
            if broadcast_info.source != "QQ":
                return False

            # 检查消息格式
            message = broadcast_info.message
            if not message or message[0].get("type") != "text":
                return False

            content = message[0].get("data", {}).get("text", "").strip()
            # 转换为小写进行比较（不区分大小写）
            content_lower = content.lower().strip()
            
            # 检查是否是 WebUI 相关命令（不区分大小写）
            # 支持带或不带命令前缀
            config = getattr(self, "config", None) or self.gugubot_config
            command_prefix = config.get("GUGUBot", {}).get("command_prefix", "#")
            
            # 移除命令前缀（如果存在）
            if content_lower.startswith(command_prefix.lower()):
                content_lower = content_lower[len(command_prefix):].strip()
            
            # 检查是否是管理员
            if not self._is_admin(broadcast_info.sender_id):
                return False

            # 保存 QQ 昵称
            if broadcast_info.sender:
                self._save_qq_nickname(broadcast_info.sender_id, broadcast_info.sender)
            
            # 处理命令（不区分大小写）
            # WebUI -> 显示帮助
            if content_lower == "webui":
                await self._handle_help(broadcast_info)
                return True
            
            # webui 设置密码 <密码>
            if content_lower.startswith("webui 设置密码"):
                await self._handle_set_password(broadcast_info, content)
                return True
            
            # webui 临时码
            if content_lower == "webui 临时码" or content_lower.startswith("webui 临时码 "):
                await self._handle_generate_temp_code(broadcast_info)
                return True

            return False
        except Exception as e:
            self.logger.error(f"处理消息时出错: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    async def _handle_help(self, broadcast_info):
        """
        处理帮助命令：WebUI

        Parameters
        ----------
        broadcast_info
            BroadcastInfo 对象
        """
        try:
            help_text = (
                "WebUI 管理系统命令：\n"
                "• webui 设置密码 <密码> - 设置或更新 WebUI 账户密码（用户名使用QQ号）\n"
                "• webui 临时码 - 生成临时登录码（15分钟有效）"
            )
            reply_msg = self._build_message(help_text)
            await self._reply(broadcast_info, reply_msg)
        except Exception as e:
            self.logger.error(f"处理帮助命令时出错: {e}")
            reply_msg = self._build_message("处理命令时发生错误，请稍后重试")
            await self._reply(broadcast_info, reply_msg)

    async def _handle_set_password(self, broadcast_info, content: str):
        """
        处理设置密码命令：设置WebUI密码 <密码>
        如果用户已存在则更新密码，否则创建新账户

        Parameters
        ----------
        broadcast_info
            BroadcastInfo 对象
        content : str
            消息内容
        """
        try:
            # 解析密码（命令格式：webui 设置密码 <密码> 或 #webui 设置密码 <密码>）
            # 移除命令前缀（如果存在）
            config = getattr(self, "config", None) or self.gugubot_config
            command_prefix = config.get("GUGUBot", {}).get("command_prefix", "#")
            content_clean = content.strip()
            if content_clean.startswith(command_prefix):
                content_clean = content_clean[len(command_prefix):].strip()
            
            # 分割命令
            parts = content_clean.split(" ", 2)
            if len(parts) < 3 or parts[0].lower() != "webui" or parts[1].lower() != "设置密码":
                reply_msg = self._build_message("命令格式错误！请使用：webui 设置密码 <密码>")
                await self._reply(broadcast_info, reply_msg)
                return

            password = parts[2].strip()
            if not password:
                reply_msg = self._build_message("密码不能为空！")
                await self._reply(broadcast_info, reply_msg)
                return
            if password_too_short(password):
                reply_msg = self._build_message(f"密码长度至少 {MIN_PASSWORD_LENGTH} 位！")
                await self._reply(broadcast_info, reply_msg)
                return

            # 使用 QQ 号作为用户名
            qq_id = str(broadcast_info.sender_id)
            # 获取昵称（优先使用缓存的昵称，否则使用当前发送者昵称）
            nickname = user_db.get("qq_nicknames", {}).get(qq_id) or broadcast_info.sender or "用户"

            # 检查用户是否已存在
            if qq_id in user_db.get("user", {}):
                # 用户已存在，更新密码
                set_user_password(qq_id, password)
            else:
                # 创建新用户
                success = create_user_account(qq_id, password)
                if not success:
                    reply_msg = self._build_message("创建账户失败，请稍后重试")
                    await self._reply(broadcast_info, reply_msg)
                    return

            # 构建友好的回复消息
            reply_text = (
                "欢迎使用WebUI管理系统\n"
                f"{nickname} 您好，您的用户名为 {qq_id}\n"
                f"密码为 {password}\n"
                "您可通过网页使用以上信息进行登录"
            )
            reply_msg = self._build_message(reply_text)
            await self._reply(broadcast_info, reply_msg)
        except Exception as e:
            self.logger.error(f"处理设置密码命令时出错: {e}")
            reply_msg = self._build_message("处理命令时发生错误，请稍后重试")
            await self._reply(broadcast_info, reply_msg)

    async def _handle_generate_temp_code(self, broadcast_info):
        """
        处理生成临时登录码命令：生成WebUI临时登录码

        Parameters
        ----------
        broadcast_info
            BroadcastInfo 对象
        """
        try:
            import datetime

            # 使用 QQ 号作为用户名
            qq_id = str(broadcast_info.sender_id)
            # 获取昵称（优先使用缓存的昵称，否则使用当前发送者昵称）
            nickname = user_db.get("qq_nicknames", {}).get(qq_id) or broadcast_info.sender or "用户"
            
            # 生成临时登录码，关联QQ号
            temp_code = create_temp_password(qq_id=qq_id)
            
            # 构建友好的回复消息
            reply_text = (
                "欢迎使用WebUI管理系统\n"
                f"{nickname} 您好，您的用户名为 {qq_id}\n"
                f"临时登录码为 {temp_code}，有效期至 15分钟\n"
                "您可通过网页使用以上信息进行登录"
            )
            reply_msg = self._build_message(reply_text)
            await self._reply(broadcast_info, reply_msg)
        except Exception as e:
            self.logger.error(f"处理生成临时登录码命令时出错: {e}")
            reply_msg = self._build_message("处理命令时发生错误，请稍后重试")
            await self._reply(broadcast_info, reply_msg)

    async def _reply(self, broadcast_info, message):
        """
        发送回复消息

        Parameters
        ----------
        broadcast_info
            BroadcastInfo 对象
        message : list
            消息列表（CQ码格式）
        """
        try:
            # 使用 BasicSystem 的 reply 方法（如果可用）
            if hasattr(self, "reply") and callable(getattr(self, "reply", None)):
                await self.reply(broadcast_info, message)
                return

            # 备用方案：通过 connector_manager 发送消息
            from gugubot.utils.types import ProcessedInfo

            processed_info = ProcessedInfo(
                processed_message=message,
                source=broadcast_info.source,
                source_id=broadcast_info.source_id,
                sender=broadcast_info.sender,
                sender_id=broadcast_info.sender_id,
                raw="",
                server=broadcast_info.server,
                logger=broadcast_info.logger,
                event_sub_type=broadcast_info.event_sub_type,
                receiver=broadcast_info.receiver,
            )

            gugubot_module = self.server.get_plugin_instance("gugubot")
            if gugubot_module:
                connector_manager = getattr(gugubot_module, "connector_manager", None)
                if connector_manager:
                    await connector_manager.broadcast_processed_info(processed_info)
                    return

            self.logger.warning("无法发送回复消息")
        except Exception as e:
            self.logger.error(f"发送回复消息时出错: {e}")


def create_webui_system(server, config: Optional[dict] = None, system_manager=None):
    """
    创建 WebUI 系统模块的包装器，使其兼容 GUGUBot 的 BasicSystem
    
    Parameters
    ----------
    server : PluginServerInterface
        MCDR 服务器接口
    config : dict, optional
        GUGUBot 配置字典
    system_manager : optional
        GUGUBot 的 SystemManager 实例
    
    Returns
    -------
    WebUISystem
        WebUI 系统模块实例
    """
    try:
        # 尝试导入 GUGUBot 的 BasicSystem
        from gugubot.logic.system.basic_system import BasicSystem
        
        class WebUISystemWrapper(BasicSystem, WebUISystem):
            """WebUI 系统模块的 BasicSystem 包装器"""
            
            def __init__(self, server, config=None, system_manager=None):
                BasicSystem.__init__(self, "webui", enable=True, config=config)
                if system_manager:
                    self.system_manager = system_manager
                WebUISystem.__init__(self, server, config)
                self.config = config or {}
            
            async def process_broadcast_info(self, broadcast_info) -> bool:
                return await WebUISystem.process_broadcast_info(self, broadcast_info)
            
            def _is_admin(self, sender_id: str) -> bool:
                return WebUISystem._is_admin(self, sender_id)
            
            def get_help(self) -> list:
                """获取系统帮助信息"""
                return WebUISystem.get_help(self)
        
        return WebUISystemWrapper(server, config, system_manager)
    except ImportError:
        # 如果无法导入 BasicSystem，返回普通实例
        # 这种情况下，系统需要通过其他方式注册
        return WebUISystem(server, config)
