import os
import platform
import threading
from typing import Any, Callable, Optional

from mcdreforged.api.command import Literal, Text
from mcdreforged.api.event import LiteralEvent
from mcdreforged.api.types import PluginServerInterface

import guguwebui.state as gugu_state
from guguwebui.utils.dependency_checker import check_and_install_dependencies

# 全局变量声明
web_server_interface = None
_mounted_to_fastapi_mcdr = False
chat_logger = None  # 在 _do_startup 中赋值为 ChatLogger 实例
_RUNTIME_ATTR = "_guguwebui_runtime"


def _get_runtime(server: PluginServerInterface) -> dict:
    """Keep lifecycle state on MCDR's stable server object across plugin reloads."""
    runtime = getattr(server, _RUNTIME_ATTR, None)
    if not isinstance(runtime, dict):
        runtime = {
            "lock": threading.RLock(),
            "generation": 0,
            "bootstrap_thread": None,
            "web_server": None,
            "starting": False,
        }
        setattr(server, _RUNTIME_ATTR, runtime)
    return runtime


def _begin_generation(server: PluginServerInterface) -> int:
    runtime = _get_runtime(server)
    with runtime["lock"]:
        runtime["generation"] += 1
        return runtime["generation"]


def _is_current_generation(server: PluginServerInterface, generation: int) -> bool:
    runtime = _get_runtime(server)
    with runtime["lock"]:
        return runtime["generation"] == generation


def _bootstrap(server: PluginServerInterface, generation: int):
    """后台启动流程；插件重载后旧代次不得继续创建监听器。"""
    try:
        check_and_install_dependencies(server)
    except Exception as e:
        server.logger.error(f"依赖检查过程中发生错误: {e}")
        server.logger.warning("将尝试继续启动，但可能会遇到导入错误")
    if not _is_current_generation(server, generation):
        server.logger.info("检测到插件已重载，取消旧的 WebUI 启动流程")
        return
    _do_startup(server, generation)


def _log_plugin_format(server: PluginServerInterface):
    """检测并输出插件运行模式（.mcdr / 文件夹 / 单文件）。"""
    try:
        from .utils.mc_util import detect_plugin_format
        format_map = {
            "mcdr_file": ".mcdr 文件",
            "folder": "文件夹",
            "single_file": "单文件 .py",
            "unknown": "未知"
        }
        name = format_map.get(detect_plugin_format(server), "未知")
        server.logger.info(f"插件运行模式: {name}")
    except Exception as e:
        server.logger.debug(f"检测插件运行模式时出错: {e}")


def _ensure_web_imports(server: PluginServerInterface) -> bool:
    """确保 uvicorn / StaticFiles 可导入，失败时打日志并返回 False。"""
    try:
        import uvicorn
        from fastapi.staticfiles import StaticFiles 
        server.logger.info("所有模块导入成功")
        return True
    except ImportError as e:
        server.logger.error(f"导入模块时发生错误: {e}")
        server.logger.error("请检查依赖包是否正确安装，建议重新启动 MCDR 让依赖自动安装生效")
        return False
    except Exception as e:
        server.logger.error(f"导入模块时发生未知错误: {e}")
        return False


def _validate_plugin_config(
    server: PluginServerInterface,
    plugin_config: dict,
    fastapi_mcdr,
    force_standalone: bool,
) -> dict:
    """
    校验并应用配置。返回最终使用的 plugin_config。
    致命错误且无法忽略时会打日志、尝试卸载插件并抛出 RuntimeError。
    """
    try:
        from .utils.config_validator import ConfigValidator
        validator = ConfigValidator(server.logger)
        is_valid, validated_config, has_critical_error = validator.validate_config(plugin_config)
    except Exception as e:
        server.logger.error(f"配置验证过程中发生错误: {e}")
        server.logger.warning("将使用原始配置继续启动")
        return plugin_config

    if has_critical_error:
        if fastapi_mcdr is not None or force_standalone:
            if fastapi_mcdr is not None:
                server.logger.warning("由于使用 fastapi_mcdr 插件，将忽略IP和端口设置")
            else:
                server.logger.warning("由于强制独立运行模式，将忽略IP和端口设置")
            return validated_config
        server.logger.error("配置验证失败，IP或端口配置错误，拒绝启动Web服务")
        server.logger.error(validator.get_validation_summary())
        server.logger.error("请检查配置文件中的 host 和 port 设置，然后重新启动插件")
        server.logger.info("正在卸载插件...")
        try:
            server.unload_plugin("guguwebui")
        except Exception as e:
            server.logger.error(f"卸载插件时出错: {e}")
        raise RuntimeError("配置验证失败，插件无法启动")

    if not is_valid:
        server.logger.error("配置验证失败，使用默认配置启动")
        server.logger.error(validator.get_validation_summary())
        return validated_config

    server.logger.info("配置验证通过")
    if validator.warnings:
        server.logger.info(validator.get_validation_summary())
    return validated_config


def _setup_fastapi_mcdr_and_events(
    server: PluginServerInterface,
    fastapi_mcdr,
    force_standalone: bool,
) -> bool:
    """
    根据是否使用 fastapi_mcdr / 强制独立 挂载或注册事件。
    返回 True 表示使用 fastapi_mcdr（不启动独立服务器），False 表示独立模式。
    """
    if fastapi_mcdr is not None and not force_standalone:
        server.logger.info("检测到 fastapi_mcdr 插件，将挂载为子应用")
        if fastapi_mcdr.is_ready():
            mount_to_fastapi_mcdr(server, fastapi_mcdr)
        else:
            server.register_event_listener(
                fastapi_mcdr.COLLECT_EVENT,
                lambda: mount_to_fastapi_mcdr(server, fastapi_mcdr)
            )
            server.logger.info("fastapi_mcdr 尚未准备好，已注册事件监听器")
        server.register_event_listener(
            LiteralEvent("mcdreforged.plugin_manager.plugin_unloaded"),
            lambda plugin_id: on_plugin_unloaded(server, plugin_id)
        )
        server.register_event_listener(
            LiteralEvent("mcdreforged.plugin_manager.plugin_loaded"),
            lambda plugin_id: on_plugin_loaded(server, plugin_id)
        )
        return True

    if force_standalone:
        server.logger.info("强制独立运行模式，已忽略 fastapi_mcdr 插件")
    else:
        server.logger.info("未检测到 fastapi_mcdr 插件，将使用独立服务器模式")
    server.register_event_listener(
        LiteralEvent("mcdreforged.plugin_manager.plugin_loaded"),
        lambda plugin_id: on_plugin_loaded(server, plugin_id)
    )
    server.register_event_listener(
        LiteralEvent("mcdreforged.plugin_manager.plugin_unloaded"),
        lambda plugin_id: on_plugin_unloaded(server, plugin_id)
    )
    return False


def _apply_ssl_config(
    server: PluginServerInterface,
    plugin_config: dict,
    config_params: dict,
) -> tuple[dict, bool]:
    """根据 plugin_config 填充 config_params 的 SSL 相关项。返回 (config_params, ssl_enabled)。"""
    ssl_enabled = plugin_config.get('ssl_enabled', False)
    if not ssl_enabled:
        return config_params, False
    try:
        ssl_keyfile = plugin_config.get('ssl_keyfile', '')
        ssl_certfile = plugin_config.get('ssl_certfile', '')
        ssl_keyfile_password = plugin_config.get('ssl_keyfile_password', '')
        if not ssl_keyfile or not ssl_certfile:
            server.logger.warning("SSL 配置不完整，将使用 HTTP 模式启动")
            return config_params, False
        if not os.path.exists(ssl_certfile):
            server.logger.error(f"SSL 证书文件不存在: {ssl_certfile}")
            server.logger.warning("将回退至 HTTP 模式")
            return config_params, False
        if not os.path.exists(ssl_keyfile):
            server.logger.error(f"SSL 密钥文件不存在: {ssl_keyfile}")
            server.logger.warning("将回退至 HTTP 模式")
            return config_params, False
        config_params['ssl_keyfile'] = ssl_keyfile
        config_params['ssl_certfile'] = ssl_certfile
        if ssl_keyfile_password:
            config_params['ssl_keyfile_password'] = ssl_keyfile_password
        server.logger.info("已启用 HTTPS 模式")
        return config_params, True
    except Exception as e:
        server.logger.error(f"处理 SSL 配置时发生错误: {e}")
        server.logger.warning("将回退到 HTTP 模式")
        return config_params, False


def _start_standalone_server(
    server: PluginServerInterface,
    plugin_config: dict,
    host: str,
    port: int,
    app,
    ThreadedUvicornCls,
    uvicorn_module,
    generation: int | None = None,
):
    """构建 uvicorn 配置（含 SSL）、启动独立 Web 服务并写全局 web_server_interface。"""
    global web_server_interface
    runtime = _get_runtime(server)
    with runtime["lock"]:
        if generation is not None and runtime["generation"] != generation:
            raise RuntimeError("插件已重载，拒绝启动旧代 Web 服务")
        existing = runtime.get("web_server")
        if existing is not None or runtime["starting"]:
            server.logger.info("Web服务器已在运行或启动中，跳过重复启动")
            return
        runtime["starting"] = True
        interface = None
        try:
            config_params = {
                'app': app,
                'host': host,
                'port': port,
                'log_level': 'warning',
            }
            config_params, ssl_enabled = _apply_ssl_config(
                server, plugin_config, config_params
            )
            config = uvicorn_module.Config(**config_params)
            interface = ThreadedUvicornCls(server, config)
            if generation is not None and runtime["generation"] != generation:
                raise RuntimeError("插件已重载，拒绝启动旧代 Web 服务")
            runtime["web_server"] = interface
            web_server_interface = interface
            protocol = "https" if ssl_enabled else "http"
            from .utils.server_util import format_host_for_url
            host_display = format_host_for_url(host)
            server.logger.info(f"网页地址: {protocol}://{host_display}:{port}")
            interface.start()
        except Exception:
            if interface is not None:
                try:
                    interface.stop()
                except Exception:
                    pass
            if runtime.get("web_server") is interface:
                runtime["web_server"] = None
            if web_server_interface is interface:
                web_server_interface = None
            runtime["starting"] = False
            raise
        runtime["starting"] = False


def _log_fastapi_mcdr_url(server: PluginServerInterface):
    """在已挂载到 fastapi_mcdr 时输出访问地址。"""
    server.logger.info("WebUI 已挂载到 fastapi_mcdr 插件，无需启动独立服务器")
    try:
        fastapi_config = server.load_config_simple(
            "fastapi_mcdr_config.json", {"host": "0.0.0.0", "port": 8080}, echo_in_console=False
        )
        port = fastapi_config.get('port', 8080)
        server.logger.info(f"WebUI 访问地址: http://localhost:{port}/guguwebui")
        server.logger.info(f"API 文档地址: http://localhost:{port}/guguwebui/docs")
    except Exception as e:
        server.logger.debug(f"无法获取 fastapi_mcdr 配置: {e}")
        server.logger.info("WebUI 已挂载到 fastapi_mcdr 插件，访问地址请查看 fastapi_mcdr 插件配置")


def _init_chat_logger(server: PluginServerInterface):
    """初始化聊天消息监听器，返回 ChatLogger 实例或 None。"""
    try:
        from .utils.chat_logger import ChatLogger
        from .utils.mc_util import create_chat_logger_status_rtext
        logger = ChatLogger()
        server.logger.info(create_chat_logger_status_rtext('init', True))
        return logger
    except Exception as e:
        server.logger.error(f"聊天消息监听器初始化失败: {e}")
        return None


def _do_startup(server: PluginServerInterface, generation: int | None = None):
    """依赖就绪后执行完整启动流程（在后台线程中调用）。"""
    if generation is not None and not _is_current_generation(server, generation):
        return
    global web_server_interface, chat_logger

    import uvicorn as uvicorn_module
    from fastapi.staticfiles import StaticFiles

    from guguwebui.utils.file_util import amount_static_files
    from guguwebui.utils.mc_util import get_plugins_info
    from guguwebui.utils.server_util import patch_asyncio
    from guguwebui.constant import get_static_path
    from guguwebui.web_server import (DEFALUT_CONFIG, STATIC_PATH,
                                      ThreadedUvicorn, app, init_app)

    _log_plugin_format(server)
    if not _ensure_web_imports(server):
        return

    if platform.system() == 'Windows':
        server.logger.debug("正在为 Windows 平台应用 asyncio 补丁...")
        patch_asyncio(server)
        server.logger.debug("asyncio 补丁应用完成")

    plugin_config = server.load_config_simple("config.json", DEFALUT_CONFIG, echo_in_console=False)
    fastapi_mcdr = server.get_plugin_instance('fastapi_mcdr')
    force_standalone = plugin_config.get('force_standalone', False)
    if force_standalone:
        server.logger.info("强制独立运行模式已启用，将忽略 fastapi_mcdr 插件")
        fastapi_mcdr = None

    plugin_config = _validate_plugin_config(server, plugin_config, fastapi_mcdr, force_standalone)

    use_fastapi_mcdr = _setup_fastapi_mcdr_and_events(server, fastapi_mcdr, force_standalone)
    host = plugin_config['host']
    port = plugin_config['port']

    static_root = get_static_path(server)
    amount_static_files(server, static_root)
    app.mount("/static", StaticFiles(directory=str(static_root / "static")), name="static")
    app.mount("/assets", StaticFiles(directory=str(static_root / "static" / "assets")), name="assets")
    app.mount("/custom", StaticFiles(directory=str(static_root / "custom")), name="custom")

    init_app(server)
    start_self_update_checker(server)
    chat_logger = _init_chat_logger(server)

    if use_fastapi_mcdr:
        _log_fastapi_mcdr_url(server)
    else:
        _start_standalone_server(
            server, plugin_config, host, port,
            app, ThreadedUvicorn, uvicorn_module, generation,
        )

    get_plugins_info(app.state.server_interface)
    register_gugubot_system(server)


# ============================================================#


def _handle_dependencies_and_reload(server: PluginServerInterface):
    """在后台线程中处理依赖安装，然后重载插件"""
    try:
        # 导入依赖检查器
        from guguwebui.utils.dependency_checker import check_and_install_dependencies
        
        server.logger.info("开始自动安装缺失的依赖...")
        check_and_install_dependencies(server)
        
        # 依赖安装成功，执行重载命令（使用MCDR的execute_command API）
        server.logger.info("依赖安装完成，正在重载插件...")
        server.execute_command("!!MCDR plugin reload guguwebui")
        
    except Exception as e:
        # 依赖安装失败
        server.logger.error(f"自动安装依赖失败: {e}")
        server.logger.error("请手动安装以下依赖后重试:")
        server.logger.error("pip install passlib fastapi>=0.68.0 javaproperties mcdreforged>=2.3.0 pydantic requests ruamel.yaml starlette uvicorn>=0.15.0 itsdangerous jinja2 argon2_cffi python-multipart typing-extensions aiohttp mcstatus")
        
        # 执行卸载命令（使用MCDR的execute_command API）
        server.logger.info("正在卸载插件...")
        server.execute_command("!!MCDR plugin unload guguwebui")


def on_load(server: PluginServerInterface, _old):
    """注册命令后，异步启动其余 WebUI 逻辑，避免阻塞 MCDR 看门狗。"""
    generation = _begin_generation(server)
    # 直接使用dependency_checker中的函数检查passlib是否已安装
    try:
        from guguwebui.utils.dependency_checker import is_package_installed
        
        # 检查passlib是否已安装（这是constant.py需要的核心依赖）
        if is_package_installed("passlib"):
            # passlib已安装，正常导入并继续
            from guguwebui.constant import DEFALUT_CONFIG
            
            plugin_config = server.load_config_simple("config.json", DEFALUT_CONFIG, echo_in_console=False)
            host = plugin_config.get("host", DEFALUT_CONFIG["host"])
            port = plugin_config.get("port", DEFALUT_CONFIG["port"])
            register_command(server, host, port)

            server.logger.info("启动 WebUI 中（其余部分在后台线程异步完成）...")
            bootstrap_thread = threading.Thread(
                target=_bootstrap, args=(server, generation), daemon=False
            )
            _get_runtime(server)["bootstrap_thread"] = bootstrap_thread
            bootstrap_thread.start()
        else:
            # passlib未安装，启动异步依赖处理
            server.logger.warning("检测到passlib未安装，将在后台线程中自动安装依赖...")
            server.logger.info("依赖安装完成后会自动重载插件")
            
            # 启动后台线程处理依赖
            threading.Thread(target=_handle_dependencies_and_reload, args=(server,), daemon=False).start()
            
    except Exception as e:
        # 其他异常情况
        server.logger.error(f"插件启动过程中发生错误: {e}")
        server.logger.error("将在后台线程中尝试修复依赖问题...")
        
        # 启动后台线程处理依赖
        threading.Thread(target=_handle_dependencies_and_reload, args=(server,), daemon=False).start()


def mount_to_fastapi_mcdr(server: PluginServerInterface, fastapi_mcdr):
    """挂载 WebUI 到 fastapi_mcdr 插件"""
    global _mounted_to_fastapi_mcdr
    try:
        from .web_server import app
        fastapi_mcdr.mount("guguwebui", app)
        _mounted_to_fastapi_mcdr = True
    except Exception as e:
        server.logger.error(f"挂载到 fastapi_mcdr 时发生错误: {e}")
        server.logger.warning("将回退到独立服务器模式")
        # 这里可以添加回退逻辑，但为了简化，我们只记录错误


def on_plugin_unloaded(server: PluginServerInterface, plugin_id: str):
    """处理插件卸载事件"""
    # 关键修复：卸载时清理插件网页注册表，避免侧边栏/API 仍可用
    try:
        gugu_state.REGISTERED_PLUGIN_PAGES.pop(plugin_id, None)
    except Exception:
        # 清理失败不应阻止后续逻辑（例如 fastapi_mcdr 切换）
        server.logger.debug(f"清理插件页注册失败: {plugin_id}", exc_info=True)

    if plugin_id == "fastapi_mcdr":
        # 检查是否强制独立运行
        from guguwebui.constant import DEFALUT_CONFIG
        plugin_config = server.load_config_simple("config.json", DEFALUT_CONFIG, echo_in_console=False)
        force_standalone = plugin_config.get('force_standalone', False)

        if force_standalone:
            server.logger.info("强制独立运行模式，忽略fastapi_mcdr插件卸载事件")
        else:
            server.logger.warning("检测到 fastapi_mcdr 插件被卸载，WebUI 将切换到独立服务器模式")

            # 启动独立服务器模式
            try:
                start_standalone_server(server)
                server.logger.info("已成功切换到独立服务器模式")
            except Exception as e:
                server.logger.error(f"切换到独立服务器模式失败: {e}")


def on_plugin_loaded(server: PluginServerInterface, plugin_id: str):
    """处理插件加载事件"""
    server.logger.info(f"插件加载事件触发: {plugin_id}")
    if plugin_id == "fastapi_mcdr":
        # 检查是否强制独立运行
        from guguwebui.constant import DEFALUT_CONFIG
        plugin_config = server.load_config_simple("config.json", DEFALUT_CONFIG, echo_in_console=False)
        force_standalone = plugin_config.get('force_standalone', False)

        if force_standalone:
            server.logger.info("强制独立运行模式，忽略fastapi_mcdr插件加载事件")
        else:
            server.logger.info("检测到 fastapi_mcdr 插件被重新加载")
            # 不需要主动切换，fastapi_mcdr 准备好时会自动挂载 WebUI
    elif plugin_id == "gugubot":
        # GUGUBot 插件加载时，尝试注册系统模块
        server.logger.info("检测到 GUGUBot 插件加载，尝试注册 WebUI 系统模块")
        register_gugubot_system(server)


def start_standalone_server(server: PluginServerInterface):
    """启动独立服务器模式，复用统一的生命周期保护。"""
    try:
        import uvicorn

        from .utils.mc_util import get_plugins_info
        from .utils.server_util import ThreadedUvicorn
        from .web_server import DEFALUT_CONFIG, app, init_app

        init_app(server)
        plugin_config = server.load_config_simple(
            "config.json", DEFALUT_CONFIG, echo_in_console=False
        )
        generation = _get_runtime(server)["generation"]
        _start_standalone_server(
            server,
            plugin_config,
            plugin_config["host"],
            int(plugin_config["port"]),
            app,
            ThreadedUvicorn,
            uvicorn,
            generation,
        )
        get_plugins_info(app.state.server_interface)
    except Exception as e:
        server.logger.error(f"启动独立服务器失败: {e}")
        raise


# 全局变量，用于管理检查线程
_checker_thread = None
_checker_running = False


def start_plugin_status_checker(server: PluginServerInterface):
    """启动定期检查插件状态的任务"""
    global _checker_thread, _checker_running
    import threading
    import time

    # 如果已经有检查线程在运行，先停止它
    if _checker_thread is not None and _checker_thread.is_alive():
        _checker_running = False
        _checker_thread.join(timeout=1)
        server.logger.debug("已停止之前的检查线程")

    _checker_running = True

    def check_plugin_status():
        """定期检查 fastapi_mcdr 插件状态"""
        while _checker_running:
            try:
                time.sleep(5)  # 每5秒检查一次

                # 检查 fastapi_mcdr 插件是否还存在
                fastapi_mcdr = server.get_plugin_instance('fastapi_mcdr')
                if fastapi_mcdr is None:
                    # 检查是否强制独立运行
                    from guguwebui.constant import DEFALUT_CONFIG
                    plugin_config = server.load_config_simple("config.json", DEFALUT_CONFIG, echo_in_console=False)
                    force_standalone = plugin_config.get('force_standalone', False)

                    if force_standalone:
                        server.logger.debug("强制独立运行模式，忽略fastapi_mcdr插件状态变化")
                    else:
                        server.logger.warning("定期检查发现 fastapi_mcdr 插件已卸载，切换到独立服务器模式")
                        try:
                            start_standalone_server(server)
                            server.logger.info("已成功切换到独立服务器模式")
                            break  # 退出检查循环
                        except Exception as e:
                            server.logger.error(f"切换到独立服务器模式失败: {e}")
                            break

            except Exception as e:
                server.logger.debug(f"插件状态检查时出错: {e}")
                break

    # 启动检查线程
    _checker_thread = threading.Thread(target=check_plugin_status, daemon=True)
    _checker_thread.start()
    server.logger.info("已启动插件状态定期检查任务")


def start_self_update_checker(server: PluginServerInterface):
    """启动 WebUI 自身更新检查任务"""
    import threading
    import time

    from .utils.mc_util import check_self_update
    from .web_server import app

    def check_task():
        # 启动后先延迟 10 秒检查一次，确保系统已完全启动
        time.sleep(10)
        while True:
            try:
                result = check_self_update(server)
                if result.get("available"):
                    latest = result.get("latest")
                    server.logger.info(
                        f"§6[WebUI] 发现新版本: §a{latest}§6，请前往 Web 界面或在终端执行 §b!!MCDR plugin install -U -y guguwebui §6进行更新")
                    # 存储到 app.state 供前端查询
                    if hasattr(app, "state"):
                        app.state.self_update_info = result
            except Exception as e:
                server.logger.debug(f"自更新检查任务出错: {e}")

            # 每 12 小时检查一次
            time.sleep(12 * 3600)

    thread = threading.Thread(target=check_task, daemon=True)
    thread.start()
    server.logger.info("已启动 WebUI 自更新检查任务 (每 12 小时)")


def on_unload(server: PluginServerInterface):
    global _mounted_to_fastapi_mcdr, web_server_interface
    server.logger.info("正在卸载 WebUI...")
    
    # 卸载 GUGUBot 系统模块
    unregister_gugubot_system(server)

    # 优先从 fastapi_mcdr 取消挂载（不依赖 config / is_ready，仅根据挂载标志）
    if _mounted_to_fastapi_mcdr:
        try:
            fastapi_mcdr = server.get_plugin_instance('fastapi_mcdr')
            if fastapi_mcdr is not None:
                fastapi_mcdr.unmount("guguwebui")
                server.logger.info("已从 fastapi_mcdr 插件卸载 WebUI")
        except Exception as e:
            server.logger.warning(f"从 fastapi_mcdr 卸载时出错: {e}")
        finally:
            _mounted_to_fastapi_mcdr = False

    # 停止插件状态检查线程
    global _checker_running, _checker_thread
    if _checker_running:
        _checker_running = False
        if _checker_thread is not None and _checker_thread.is_alive():
            _checker_thread.join(timeout=1)
            server.logger.debug("已停止插件状态检查线程")

    # 停止日志捕获
    try:
        from .web_server import log_watcher
        if log_watcher:
            log_watcher.stop()
            server.logger.debug("日志捕获器已停止")
    except (ImportError, AttributeError) as e:
        server.logger.debug(f"日志捕获器未初始化或导入失败: {e}")
    except Exception as e:
        server.logger.warning(f"停止日志捕获器时出错: {e}")

    # 停止服务器状态监控
    try:
        from .web_server import app as gugu_app
        monitor_service = getattr(gugu_app.state, "monitor_service", None)
        if monitor_service is not None:
            monitor_service.stop()
            server.logger.debug("服务器状态监控已停止")
    except Exception as e:
        server.logger.debug(f"停止服务器状态监控时出错: {e}")

    # 使旧启动线程在任何后续阶段都失效，并等待它离开启动流程。
    runtime = _get_runtime(server)
    with runtime["lock"]:
        runtime["generation"] += 1
        bootstrap_thread = runtime.get("bootstrap_thread")
        runtime["bootstrap_thread"] = None
        current_web_server = runtime.get("web_server") or web_server_interface

    if bootstrap_thread is not None and bootstrap_thread is not threading.current_thread():
        bootstrap_thread.join(timeout=10)
        if bootstrap_thread.is_alive():
            server.logger.error("旧 WebUI 启动线程未退出，拒绝继续启动新的 WebUI 实例")

    # 停止Web服务器（仅在独立模式下需要）
    try:
        if current_web_server:
            # 如果使用了SSL，添加特殊处理
            try:
                from .web_server import DEFALUT_CONFIG
                plugin_config = server.load_config_simple("config.json", DEFALUT_CONFIG, echo_in_console=False)
                ssl_enabled = plugin_config.get('ssl_enabled', False)

                if ssl_enabled:
                    server.logger.debug("检测到HTTPS模式，使用特殊卸载流程")

                    # 尝试特殊处理HTTPS相关资源
                    try:
                        import gc
                        import ssl

                        # 尝试关闭所有SSL相关对象
                        for obj in gc.get_objects():
                            try:
                                if isinstance(obj, ssl.SSLSocket) and hasattr(obj, 'close'):
                                    obj.close()
                            except Exception:
                                pass

                        server.logger.debug("HTTPS资源清理完成")
                    except Exception as e:
                        server.logger.warning(f"HTTPS资源清理时出错: {e}")
            except Exception as e:
                server.logger.warning(f"检查SSL配置时出错: {e}")

            # 正常停止Web服务器；未确认线程退出时不允许新实例抢占端口
            if current_web_server.stop():
                with runtime["lock"]:
                    if runtime.get("web_server") is current_web_server:
                        runtime["web_server"] = None
                    if web_server_interface is current_web_server:
                        web_server_interface = None
                server.logger.debug("Web服务器已停止")
            else:
                server.logger.error("Web服务器未完全停止，保留运行句柄以阻止端口冲突")
    except Exception as e:
        server.logger.warning(f"停止Web服务器时出错: {e}")

    # Uvicorn owns its event loop; do not close MCDR's current loop here.

    # 强制清理环境
    try:
        import gc
        gc.collect()  # 强制垃圾回收
        server.logger.debug("垃圾回收已完成")
    except Exception as e:
        server.logger.warning(f"垃圾回收时出错: {e}")

    server.logger.info("WebUI 已卸载")


def register_command(server: PluginServerInterface, host: str, port: int):
    from .utils.auth_util import verify_chat_code_command  # 在函数内部导入所有需要的命令函数
    from .utils.auth_util import (change_account_command,
                                  create_account_command,
                                  get_temp_password_command)

    # 注册指令
    server.register_command(
        Literal('!!webui')
        .requires(lambda src: src.has_permission(3))
        .runs(lambda src, ctx: src.reply(__get_help_message()))
        .then(
            Literal('create')
            .requires(lambda src: src.has_permission(3))
            .runs(lambda src, ctx: src.reply(__get_help_message()))
            .then(
                Text('account')
                .then(
                    Text('password')
                    .runs(lambda src, ctx: create_account_command(src, ctx, host, port))
                )
            )
        )
        .then(
            Literal('change')
            .requires(lambda src: src.has_permission(3))
            .runs(lambda src, ctx: src.reply(__get_help_message()))
            .then(
                Text('account')
                .then(
                    Text('old password')
                    .then(
                        Text('new password')
                        .runs(lambda src, ctx: change_account_command(src, ctx, host, port))
                    )
                )
            )
        )
        .then(
            Literal('temp')
            .requires(lambda src: src.has_permission(3))
            .runs(lambda src, ctx: get_temp_password_command(src, ctx, host, port))
        )
        .then(
            Literal('verify')
            .requires(lambda src: src.has_permission(1))
            .runs(lambda src, ctx: src.reply(__get_help_message()))
            .then(
                Text('code')
                .runs(lambda src, ctx: verify_chat_code_command(src, ctx))
            )
        )
    )

    server.register_help_message("!!webui", "GUGUWebUI 相关指令", 3)


def __get_help_message():
    help_message = "!!webui create <account> <password>: 注册 guguwebui 账户\n"
    help_message += "!!webui change <account> <old password> <new password>: 修改 guguwebui 账户密码\n"
    help_message += "!!webui temp: 获取 guguwebui 临时密码\n"
    help_message += "!!webui verify <code>: 验证聊天页验证码\n"
    return help_message


def on_user_info(server: PluginServerInterface, info):
    """监听玩家聊天消息并记录到聊天日志"""
    try:
        # 检查是否有聊天日志记录器
        if 'chat_logger' in globals() and chat_logger is not None:
            # 检查是否是玩家消息（info.is_user 为 True）
            if info.is_user:
                # 获取玩家名称和消息内容
                player_name = info.player
                message_content = info.content

                # 检查玩家名称和消息内容是否有效
                if player_name and message_content and player_name.strip() and message_content.strip():
                    # 记录聊天消息
                    chat_logger.add_message(player_name.strip(), message_content.strip(), server=server)
                    from .utils.mc_util import create_chat_logger_status_rtext
                    status_msg = create_chat_logger_status_rtext('record', True, player_name.strip(),
                                                                 message_content.strip())
                    server.logger.debug(status_msg)
                else:
                    server.logger.debug(f"跳过无效的聊天消息: player={player_name}, content={message_content}")
    except Exception as e:
        server.logger.error(f"记录聊天消息时出错: {e}")


def send_message_to_webui(server_interface, source: str, message, message_type: str = "info",
                          target_players: list = None, metadata: dict = None, is_rtext: bool = False):
    """供其他插件调用的函数，用于发送消息到WebUI并同步到游戏"""
    from .utils.mc_util import send_message_to_webui as _send_message_to_webui
    return _send_message_to_webui(server_interface, source, message, message_type, target_players, metadata, is_rtext)


def register_plugin_page(
    plugin_id: str,
    html_path: str,
    *,
    name: Optional[str] = None,
    icon: Optional[str] = None,
    api_handler: Optional[Callable[..., Any]] = None,
    upload_max_bytes: Optional[int] = None,
):
    """
    供其他插件调用的函数，用于注册其自定义网页，并可选择注册后端 API 处理器。

    Args:
        plugin_id: 插件ID
        html_path: 网页 HTML 文件的完整路径或相对于 config 目录的路径
        name: 可选。侧边栏与页面标题的友好显示名称；不传则回退为 plugin_id
        icon: 可选。Lucide 图标名、Emoji，或相对于 html_path 所在目录的图片路径。
        api_handler: 可选。接收 (url_path, params) 的处理函数；params 含 method、query、body。
            前端请求 ``GET/POST ... /api/plugin/{plugin_id}/{子路径}`` 时由 WebUI 转发至此。
            ``body`` 在 ``multipart/form-data`` 下可含上传文件字段，解析为含 ``data: bytes`` 的字典（见文档）。
        upload_max_bytes: 可选。该插件单文件上传大小上限（字节）。不传则使用全局默认上限。
    """
    from guguwebui.state import REGISTERED_PLUGIN_PAGES, PluginPageEntry

    if name is not None and not isinstance(name, str):
        raise ValueError("name must be str or None")
    if icon is not None and not isinstance(icon, str):
        raise ValueError("icon must be str or None")

    if upload_max_bytes is not None:
        if not isinstance(upload_max_bytes, int) or isinstance(upload_max_bytes, bool):
            raise ValueError("upload_max_bytes must be int or None")
        if upload_max_bytes <= 0:
            raise ValueError("upload_max_bytes must be > 0")

    REGISTERED_PLUGIN_PAGES[plugin_id] = PluginPageEntry(
        html_path=html_path,
        name=name,
        icon=icon,
        api_handler=api_handler,
        upload_max_bytes=upload_max_bytes,
    )


def register_gugubot_system(server: PluginServerInterface):
    """
    注册 WebUI 系统模块到 GUGUBot
    
    Parameters
    ----------
    server : PluginServerInterface
        MCDR 服务器接口
    """
    try:
        # 检测 GUGUBot 插件是否存在
        gugubot_module = server.get_plugin_instance("gugubot")
        if not gugubot_module:
            return

        # 通过 connector_manager 获取 SystemManager
        connector_manager = getattr(gugubot_module, "connector_manager", None)
        if not connector_manager:
            return
        
        system_manager = getattr(connector_manager, "system_manager", None)
        if not system_manager:
            return

        # 检查系统是否已注册
        systems = getattr(system_manager, "systems", [])
        for system in systems:
            if hasattr(system, "name") and system.name == "webui":
                # 如果已注册但 system_manager 为 None，更新它
                if hasattr(system, "system_manager") and system.system_manager is None:
                    system.system_manager = system_manager
                return

        # 获取 GUGUBot 配置
        gugubot_config = {}
        config_paths = ["GUGUbot/config.json", "GUGUBot/config.json", "gugubot/config.json"]
        
        for config_path in config_paths:
            try:
                gugubot_config = server.load_config_simple(config_path, {}, echo_in_console=False)
                break
            except Exception:
                continue
        
        # 如果无法加载配置，尝试从模块获取
        if not gugubot_config:
            gugubot_config = getattr(gugubot_module, "gugubot_config", None) or getattr(gugubot_module, "config", {})

        # 创建并注册系统模块
        from .integrations.gugubot_system import create_webui_system
        
        webui_system = create_webui_system(server, gugubot_config, system_manager)
        
        # 初始化系统
        if hasattr(webui_system, "initialize"):
            webui_system.initialize()
        
        # 注册到 SystemManager
        if hasattr(system_manager, "systems"):
            system_manager.systems.append(webui_system)
            server.logger.info("WebUI 系统模块已成功注册到 GUGUBot")

    except Exception as e:
        server.logger.debug(f"注册 GUGUBot 系统模块时出错: {e}")


def unregister_gugubot_system(server: PluginServerInterface):
    """
    从 GUGUBot 卸载 WebUI 系统模块
    
    Parameters
    ----------
    server : PluginServerInterface
        MCDR 服务器接口
    """
    try:
        gugubot_module = server.get_plugin_instance("gugubot")
        if not gugubot_module:
            return

        connector_manager = getattr(gugubot_module, "connector_manager", None)
        if not connector_manager:
            return
        
        system_manager = getattr(connector_manager, "system_manager", None)
        if not system_manager:
            return

        # 查找并移除 WebUI 系统
        systems = getattr(system_manager, "systems", [])
        for i, system in enumerate(systems):
            if hasattr(system, "name") and system.name == "webui":
                systems.pop(i)
                server.logger.info("WebUI 系统模块已从 GUGUBot 卸载")
                break

    except Exception:
        pass
