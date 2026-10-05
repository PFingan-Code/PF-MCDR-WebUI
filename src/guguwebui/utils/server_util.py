import asyncio
import ipaddress
import socket
import threading
import time

import uvicorn
from fastapi import Request, status
from fastapi.responses import RedirectResponse
from mcdreforged.api.types import PluginServerInterface as ServerInterface


# 辅助函数：根据当前应用路径生成正确的重定向URL
def get_redirect_url(request, path: str) -> str:
    """根据当前应用路径生成正确的重定向URL"""
    root_path = request.scope.get("root_path", "")
    if root_path:
        return f"{root_path}{path}"
    else:
        return path


def format_host_for_url(host: str) -> str:
    """将 host 格式化为 URL 中可用的形式，IPv6 地址需加方括号。"""
    if not host:
        return host
    try:
        ip = ipaddress.ip_address(host)
        return f"[{host}]" if ip.version == 6 else host
    except ValueError:
        return host


from guguwebui.constant import *


# Github: https://github.com/zauberzeug/nicegui/issues/1956
class ThreadedUvicorn:
    """在独立线程运行 Uvicorn，并提供可验证的启停生命周期。"""

    def __init__(self, server: ServerInterface, config: uvicorn.Config):
        self.mcdr_server = server
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(daemon=True, target=self.server.run)
        self._start_timeout = 10.0
        self._stop_timeout = 10.0

    def start(self):
        if self.thread.is_alive():
            if self.server.started:
                return
            raise RuntimeError("Web服务器正在启动或启动失败")

        try:
            self.thread.start()
            deadline = time.monotonic() + self._start_timeout
            while not self.server.started:
                if not self.thread.is_alive() or self.server.should_exit:
                    raise RuntimeError("Web服务器未能绑定监听地址")
                if time.monotonic() >= deadline:
                    self.server.should_exit = True
                    raise TimeoutError("等待Web服务器启动超时")
                time.sleep(0.05)
        except Exception as e:
            self.mcdr_server.logger.error(f"启动服务器时发生异常: {e}")
            self.stop()
            raise

    async def wait_for_started(self):
        """兼容旧调用方的异步等待接口，启动失败时不会无限等待。"""
        deadline = time.monotonic() + self._start_timeout
        while not self.server.started:
            if not self.thread.is_alive() or self.server.should_exit:
                raise RuntimeError("Web服务器未能绑定监听地址")
            if time.monotonic() >= deadline:
                raise TimeoutError("等待Web服务器启动超时")
            await asyncio.sleep(0.1)

    def stop(self) -> bool:
        """请求停止并等待真实 Uvicorn 线程退出，返回是否已确认退出。"""
        self.mcdr_server.logger.debug("正在停止Web服务器...")
        if not self.thread.is_alive():
            return True

        self.server.should_exit = True
        self.mcdr_server.logger.debug("已设置服务器退出标志，等待线程退出...")
        self.thread.join(timeout=self._stop_timeout)
        stopped = not self.thread.is_alive()
        if not stopped:
            self.mcdr_server.logger.error(
                "Web服务器线程在超时时间内未退出，拒绝启动新的监听实例"
            )
        else:
            self.mcdr_server.logger.debug("Web服务器线程已退出，监听端口已释放")
        return stopped

    def _close_ssl_connections(self):
        """尝试关闭所有SSL连接"""
        try:
            # 检查是否是SSL模式
            if (
                hasattr(self.server.config, "ssl_certfile")
                and self.server.config.ssl_certfile
            ):
                self.mcdr_server.logger.debug("检测到SSL模式，尝试关闭SSL连接...")

                # 尝试通过关闭服务器的socket来释放端口
                try:
                    if hasattr(self.server, "servers"):
                        for server in self.server.servers:
                            if hasattr(server, "sockets") and server.sockets:
                                for sock in server.sockets:
                                    try:
                                        self.mcdr_server.logger.debug(
                                            f"关闭服务器socket: {sock}"
                                        )
                                        sock.close()
                                    except Exception as e:
                                        self.mcdr_server.logger.debug(
                                            f"关闭socket时出错: {e}"
                                        )

                    # 作为最后手段，尝试通过创建新连接来关闭旧连接
                    host = self.server.config.host
                    port = self.server.config.port
                    # 若绑定为 0.0.0.0 / ::，连接本机时使用 127.0.0.1 / ::1
                    connect_host = (
                        "::1"
                        if host == "::"
                        else ("127.0.0.1" if host == "0.0.0.0" else host)
                    )

                    try:
                        import ssl

                        context = ssl._create_unverified_context()
                        s = socket.create_connection((connect_host, port), timeout=1)
                        ssl_sock = context.wrap_socket(s)
                        ssl_sock.close()
                    except Exception as e:
                        self.mcdr_server.logger.debug(f"创建SSL连接时出错: {e}")
                except Exception as e:
                    self.mcdr_server.logger.debug(f"关闭服务器socket时出错: {e}")

                self.mcdr_server.logger.debug("SSL连接关闭尝试完成")
        except Exception as e:
            self.mcdr_server.logger.error(f"关闭SSL连接时发生错误: {e}")

    def _force_thread_termination(self):
        """强制终止服务器线程的最后手段"""
        try:
            # 记录警告信息
            self.mcdr_server.logger.warning("执行强制线程终止操作")

            # 尝试通过socket方式关闭服务器
            try:
                host = self.server.config.host
                port = self.server.config.port
                connect_host = (
                    "::1"
                    if host == "::"
                    else ("127.0.0.1" if host == "0.0.0.0" else host)
                )
                try:
                    ip = ipaddress.ip_address(connect_host)
                    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
                except ValueError:
                    family = socket.AF_INET
                for _ in range(3):
                    try:
                        s = socket.socket(family, socket.SOCK_STREAM)
                        s.settimeout(1)
                        s.connect((connect_host, port))
                        s.close()
                    except Exception:
                        break

                self.mcdr_server.logger.debug("已发送关闭触发连接")
            except Exception as e:
                self.mcdr_server.logger.debug(f"发送关闭触发连接失败: {e}")

            # 尝试直接访问和清理uvicorn服务器内部对象
            try:
                if hasattr(self.server, "servers"):
                    for server in self.server.servers:
                        # 尝试关闭server
                        try:
                            if hasattr(server, "close"):
                                server.close()
                            if hasattr(server, "shutdown"):
                                server.shutdown()
                        except Exception:
                            pass
            except Exception as e:
                self.mcdr_server.logger.debug(f"清理uvicorn服务器对象失败: {e}")

            # 替换线程对象
            self.thread = threading.Thread(daemon=True)
            self.mcdr_server.logger.debug("线程对象已替换")

            # 强制收集垃圾
            import gc

            gc.collect()

        except Exception as e:
            self.mcdr_server.logger.error(f"强制终止线程时发生错误: {e}")


# 添加一个修复ConnectionResetError的工具函数
def patch_asyncio(server: ServerInterface):
    """
    为asyncio添加异常处理补丁，防止ConnectionResetError导致程序崩溃
    """
    # 保存原始的_ProactorBasePipeTransport._call_connection_lost方法
    try:
        import asyncio.proactor_events as proactor_events

        original_call_connection_lost = (
            proactor_events._ProactorBasePipeTransport._call_connection_lost
        )

        def patched_call_connection_lost(self, exc):
            try:
                original_call_connection_lost(self, exc)
            except ConnectionResetError:
                # 忽略连接重置错误
                pass

        # 替换原方法
        proactor_events._ProactorBasePipeTransport._call_connection_lost = (
            patched_call_connection_lost
        )
        server.logger.debug("已应用asyncio连接重置错误修复补丁")
    except Exception as e:
        server.logger.error(f"应用asyncio补丁失败: {e}")
