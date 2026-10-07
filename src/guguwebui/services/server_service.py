import datetime
import json
import threading
import traceback
from pathlib import Path
from typing import List, Optional

from guguwebui.utils.api_cache import api_cache
from guguwebui.utils.mc_util import get_java_server_info, get_server_port
from guguwebui.utils.mcdr_adapter import MCDRAdapter

# 该插件自身的管理子命令（含 MCDR 别名 plg）；reload/unload/disable 会中断当前请求
_PLUGIN_SELF_SUBCOMMANDS = {"reload", "unload", "disable"}
_MCDR_PLUGIN_LITERALS = {"plugin", "plg"}
_PLUGIN_SELF_ID = "guguwebui"


def _is_forbidden_command(command: str) -> bool:
    """命中禁用命令返回 True。

    归一化后比较：合并空白、去首尾空白、忽略大小写，并兼容
    "!!MCDR"/"!!mcdr"、"plugin"/"plg" 与 reload/unload/disable 等价的写法，
    避免大小写或多余空格绕过黑名单。
    """
    normalized = " ".join(command.split()).lower()
    if normalized.startswith("/"):
        normalized = normalized[1:].strip()
    if not normalized:
        return False

    # 服务器停机命令（应通过 !!MCDR server 或面板控制触发）
    parts = normalized.split(" ")
    if parts[0] == "stop" or parts[0] == "minecraft:stop":
        return True

    # WebUI 自身的插件管理命令
    if len(parts) >= 4 and parts[0] == "!!mcdr" and parts[1] in _MCDR_PLUGIN_LITERALS:
        if parts[2] in _PLUGIN_SELF_SUBCOMMANDS and parts[3] == _PLUGIN_SELF_ID:
            return True
    return False


class ServerService:
    def __init__(self, server, log_watcher=None, config_service=None):
        self.server = server
        self.log_watcher = log_watcher
        self.config_service = config_service
        # 直接执行命令并获取执行结果（无需 RCON）的捕获器，首次使用时才注册监听
        self._command_capture = None
        self._capture_lock = threading.Lock()

    async def get_server_status(self):
        cache_key = "server_status"
        cached_result = api_cache.get(cache_key, ttl=5.0)
        if cached_result is not None:
            return cached_result

        server_status = (
            "online"
            if self.server.is_server_running() or self.server.is_server_startup()
            else "offline"
        )

        # 获取MC服务器端口
        mc_port = get_server_port(self.server)

        server_message = await get_java_server_info(mc_port)
        player_count = server_message.get("server_player_count")
        max_player = server_message.get("server_maxinum_player_count")

        player_string = (
            f"{player_count if player_count is not None else 0}/{max_player}"
            if max_player is not None
            else ""
        )

        result = {
            "online": server_status == "online",
            "version": server_message.get("server_version", "") or "",
            "players": player_string,
        }
        api_cache.set(cache_key, result, ttl=5.0)
        return result

    def execute_action(self, action: str):
        allowed_actions = ["start", "stop", "restart"]
        if action not in allowed_actions:
            return False
        self.server.execute_command(f"!!MCDR server {action}")
        return True

    def control_server(self, action: str):
        """控制Minecraft服务器"""
        if self.execute_action(action):
            return {
                "status": "success",
                "message": f"Server {action} command sent",
            }
        return {"status": "error", "message": "Invalid action"}

    def get_logs(self, cursor: int = 0, max_lines: int = 100):
        """获取日志（REST 统一入口，替代 /server_logs + /new_logs）。

        cursor == 0：返回最新的 max_lines 行（尾部快照，首次进入终端用）；
        cursor > 0：返回 counter 大于 cursor 的新日志（增量轮询用）。
        返回统一结构 {logs, total_lines, next_cursor, new_logs_count}。
        """
        if not self.log_watcher:
            return {
                "logs": [],
                "total_lines": 0,
                "next_cursor": cursor,
                "new_logs_count": 0,
            }

        # 限制最大返回行数
        if max_lines > 500:
            max_lines = 500

        if cursor and cursor > 0:
            result = self.log_watcher.get_logs_since_counter(cursor, max_lines)
            return {
                "logs": self._format_log_entries(result["logs"]),
                "total_lines": result["total_lines"],
                "next_cursor": result["last_counter"],
                "new_logs_count": result["new_logs_count"],
            }

        result = self.log_watcher.get_merged_logs(max_lines)
        formatted_logs = self._format_log_entries(result["logs"])
        return {
            "logs": formatted_logs,
            "total_lines": result["total_lines"],
            "next_cursor": formatted_logs[-1]["counter"] if formatted_logs else 0,
            "new_logs_count": len(formatted_logs),
        }

    @staticmethod
    def _format_log_entries(entries):
        formatted_logs = []
        for log in entries:
            formatted_logs.append(
                {
                    "line_number": log["line_number"],
                    "content": log["content"],
                    "source": log["source"],
                    "counter": log.get("counter", 0),
                }
            )
        return formatted_logs

    async def get_rcon_status(self):
        cache_key = "rcon_status"
        cached_result = api_cache.get(cache_key, ttl=5.0)
        if cached_result is not None:
            return cached_result

        rcon_enabled = False
        rcon_connected = False
        rcon_info = {}

        # 读取MCDR配置检查RCON是否启用
        try:
            import ruamel.yaml

            config_path = Path("config.yml")
            if config_path.exists():
                yaml = ruamel.yaml.YAML()
                with open(config_path, "r", encoding="UTF-8") as f:
                    mcdr_config = yaml.load(f)
                    rcon_config = mcdr_config.get("rcon", {})
                    rcon_enabled = rcon_config.get("enable", False)
        except Exception:
            pass

        # 检查RCON是否正在运行
        if hasattr(self.server, "is_rcon_running") and self.server.is_rcon_running():
            rcon_connected = True
            try:
                feedback = self.server.rcon_query("list")
                rcon_info["list_response"] = feedback
                if isinstance(feedback, str) and ":" in feedback:
                    parts = feedback.split(":", 1)
                    if len(parts) == 2:
                        rcon_info["player_info"] = parts[1].strip()
            except Exception as e:
                rcon_info["error"] = str(e)

        result = {
            "rcon_enabled": rcon_enabled,
            "rcon_connected": rcon_connected,
            "rcon_info": rcon_info,
        }
        api_cache.set(cache_key, result, ttl=5.0)
        return result

    def is_public_chat_enabled(self):
        """检查是否启用公开聊天页"""
        try:
            server_config = self.server.load_config_simple(
                "config.json", DEFALUT_CONFIG, echo_in_console=False
            )
            return server_config.get("public_chat_enabled", False)
        except Exception:
            return False

    async def get_command_suggestions(self, command_input: str):
        root_nodes = MCDRAdapter.get_root_nodes(self.server)
        suggestions = []
        parts = command_input.strip().split()
        input_ends_with_space = command_input.endswith(" ")

        if not parts or (
            len(parts) == 1 and parts[0].startswith("!!") and not input_ends_with_space
        ):
            prefix = parts[0] if parts else ""
            for root_command in root_nodes.keys():
                if root_command.startswith(prefix):
                    suggestions.append(
                        {
                            "command": root_command,
                            "description": f"命令: {root_command}",
                        }
                    )
        elif len(parts) == 1 and parts[0] in root_nodes and input_ends_with_space:
            root_command = parts[0]
            for holder in root_nodes[root_command]:
                for child in holder.node.get_children():
                    if hasattr(child, "literals"):
                        for literal in child.literals:
                            suggestions.append(
                                {
                                    "command": f"{root_command} {literal}",
                                    "description": f"子命令: {literal}",
                                }
                            )
                    elif hasattr(child, "get_name"):
                        param_name = child.get_name()
                        suggestions.append(
                            {
                                "command": f"{root_command} <{param_name}>",
                                "description": f"参数: {param_name}",
                            }
                        )
        else:
            # 简化逻辑，实际实现中可以保留原有的复杂补全逻辑
            # 这里为了篇幅先保留核心逻辑
            root_command = parts[0]
            if root_command in root_nodes:
                for holder in root_nodes[root_command]:
                    node = holder.node
                    current_node = node
                    matched = True
                    process_until = len(parts) - (
                        0 if parts[-1].strip() and input_ends_with_space else 1
                    )
                    path_nodes = []

                    for i in range(1, process_until):
                        part = parts[i]
                        found = False
                        for child in current_node.get_children():
                            if hasattr(child, "literals"):
                                for literal in child.literals:
                                    if literal == part:
                                        current_node = child
                                        found = True
                                        path_nodes.append(
                                            {
                                                "type": "literal",
                                                "node": child,
                                                "value": part,
                                            }
                                        )
                                        break
                                if found:
                                    break
                        if not found:
                            for child in current_node.get_children():
                                if hasattr(child, "get_name"):
                                    current_node = child
                                    found = True
                                    path_nodes.append(
                                        {
                                            "type": "argument",
                                            "node": child,
                                            "name": child.get_name(),
                                            "value": part,
                                        }
                                    )
                                    break
                        if not found:
                            matched = False
                            break

                    if matched:
                        last_part = (
                            parts[-1]
                            if len(parts) > 1 and not input_ends_with_space
                            else ""
                        )
                        prefix = " ".join(parts[:-1]) if last_part else " ".join(parts)
                        if prefix and not prefix.endswith(" "):
                            prefix += " "

                        if input_ends_with_space:
                            for child in current_node.get_children():
                                if hasattr(child, "literals"):
                                    for literal in child.literals:
                                        suggestions.append(
                                            {
                                                "command": prefix + literal,
                                                "description": f"子命令: {literal}",
                                            }
                                        )
                                elif hasattr(child, "get_name"):
                                    param_name = child.get_name()
                                    suggestions.append(
                                        {
                                            "command": prefix + f"<{param_name}>",
                                            "description": f"参数: {param_name}",
                                        }
                                    )
                        else:
                            for child in current_node.get_children():
                                if hasattr(child, "literals"):
                                    for literal in child.literals:
                                        if not last_part or literal.startswith(
                                            last_part
                                        ):
                                            suggestions.append(
                                                {
                                                    "command": prefix + literal,
                                                    "description": f"子命令: {literal}",
                                                }
                                            )
                                elif hasattr(child, "get_name"):
                                    param_name = child.get_name()
                                    if not last_part or last_part.startswith("<"):
                                        suggestions.append(
                                            {
                                                "command": prefix + f"<{param_name}>",
                                                "description": f"参数: {param_name}",
                                            }
                                        )

        suggestions.sort(key=lambda x: x["command"])
        return suggestions[:100]

    async def send_command(self, command: str):
        """
        发送命令到 MCDR 终端并尽可能返回命令执行反馈。

        执行通道（优先级从高到低）：
        1. Minecraft 服务器命令（"/" 开头或普通文本）：RCON 已连接时优先走 RCON 并返回
           直接反馈；RCON 未启用或失败时回退到「直接执行 + 输出捕获」（无需 RCON）；
        2. MCDR 命令（"!" 开头）：经由 MCDR 进程内以捕获源直接执行并收集回复，
           不依赖 RCON。
        """
        command = (command or "").strip()
        if not command:
            return {"status": "error", "message": "Command cannot be empty"}

        if _is_forbidden_command(command):
            return {"status": "error", "message": "该命令已被禁止执行"}

        # 防止通过换行符注入多条命令（会写入服务器标准输入 / RCON）
        if "\n" in command or "\r" in command:
            return {"status": "error", "message": "命令包含非法换行符"}
        if len(command) > 2000:
            return {"status": "error", "message": "命令过长（最多 2000 字符）"}

        self.server.logger.info(f"发送命令: {command}")

        # 以 ! 开头的命令属于 MCDR 命令，RCON 无法执行，直接走进程内捕获通道
        if command.startswith("!"):
            return await self._execute_mcdr_command(command)

        # 其余命令（含 "/" 前缀与普通文本）按 Minecraft 服务器命令处理，RCON 优先级更高
        mc_command = command[1:] if command.startswith("/") else command
        return await self._execute_server_command(command, mc_command)

    def _get_command_capture(self):
        """懒加载命令输出捕获器（首次使用时注册 GENERAL_INFO 监听）。"""
        if self._command_capture is None:
            with self._capture_lock:
                if self._command_capture is None:
                    from guguwebui.utils.command_capture import CommandCapture

                    self._command_capture = CommandCapture(self.server)
        return self._command_capture

    async def _execute_server_command(self, command: str, mc_command: str):
        """执行 Minecraft 服务器命令：RCON 优先，回退到直接执行 + 输出捕获。"""
        # RCON 优先级更高：可用时优先通过 RCON 执行并返回直接反馈
        rcon_available = (
            hasattr(self.server, "is_rcon_running")
            and self.server.is_rcon_running()
        )
        if rcon_available:
            try:
                feedback = self.server.rcon_query(mc_command)
                self.server.logger.info(f"RCON反馈: {feedback}")
                return {
                    "status": "success",
                    "message": f"Command sent via RCON: {command}",
                    "feedback": feedback or "",
                }
            except Exception as e:
                self.server.logger.error(f"RCON执行命令出错: {str(e)}")

        # 服务器未运行时无法通过标准输入执行，RCON 也不可用，直接返回
        if not self.server.is_server_running():
            return {
                "status": "success",
                "message": f"Command sent: {command}",
                "feedback": "",
                "note": "服务器未运行，命令未实际执行（RCON 不可用）",
            }

        # 回退：通过服务器控制台直接执行命令并捕获输出（无需 RCON）
        result = await self._get_command_capture().execute_server_command(mc_command)
        return self._build_capture_response(command, result)

    async def _execute_mcdr_command(self, command: str):
        """直接执行 MCDR 命令（! 开头）并通过捕获源收集回复（无需 RCON）。"""
        first_word = command.split(" ", 1)[0]
        root_nodes = MCDRAdapter.get_root_nodes(self.server)
        if root_nodes and first_word not in root_nodes:
            return {
                "status": "success",
                "message": f"Command sent: {command}（MCDR 未注册命令 {first_word}，未执行）",
                "feedback": "",
            }

        result = await self._get_command_capture().execute_mcdr_command(command)
        return self._build_capture_response(command, result)

    @staticmethod
    def _build_capture_response(command: str, result: dict):
        """把直接执行捕获的结果统一为 send_command 响应格式。"""
        if not result.get("success"):
            return {
                "status": "error",
                "message": f"Command execution failed: {command}",
                "feedback": "",
                "error": result.get("error"),
            }
        if result.get("captured"):
            return {
                "status": "success",
                "message": f"Command executed: {command}",
                "feedback": result.get("output", ""),
                "capture": "direct",
                "timed_out": bool(result.get("timed_out")),
            }
        # 命令已执行，但没有捕获到任何输出
        return {
            "status": "success",
            "message": f"Command executed: {command}（已执行，未捕获到输出）",
            "feedback": "",
            "capture": "direct",
            "timed_out": bool(result.get("timed_out")),
        }
