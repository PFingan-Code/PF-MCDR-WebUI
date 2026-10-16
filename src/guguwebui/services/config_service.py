import ipaddress
import json
import logging
import secrets
import socket
import string
from pathlib import Path
from typing import List, Optional

from guguwebui.constant import (DEFALUT_CONFIG, MCDR_OFFICIAL_CATALOGUE_URL,
                                PF_PLUGIN_CATALOGUE_URL,
                                SERVER_PROPERTIES_PATH)
from guguwebui.structures import BusinessException
from guguwebui.utils.api_cache import api_cache
from guguwebui.utils.chat_logger import ChatLogger
from guguwebui.utils.i18n_util import (build_json_i18n_translations,
                                       build_yaml_i18n_translations,
                                       consistent_type_update, get_comment)
from guguwebui.utils.mc_util import find_plugin_config_paths, get_server_port
from guguwebui.utils.path_util import SafePath, get_base_dirs
from guguwebui.utils.request_security import is_valid_origin

logger = logging.getLogger(__name__)


def _clean_str_list(items) -> list:
    return [str(x).strip() for x in (items or []) if str(x).strip()]


class ConfigService:
    def __init__(self, server):
        self.server = server

    @staticmethod
    def find_plugin_config_paths(plugin_id: str) -> list:
        config_dir = Path("./config")
        response = []
        config_suffix = [".json", ".yml", ".yaml"]

        # Check ./config/plugin_id/
        plugin_folder = config_dir / plugin_id
        if plugin_folder.exists():
            response += [
                file
                for file in plugin_folder.rglob("*")
                if file.suffix.lower() in config_suffix
            ]
        else:
            # Case-insensitive check for folder
            if config_dir.exists():
                for item in config_dir.iterdir():
                    if item.is_dir() and item.name.lower() == plugin_id.lower():
                        response += [
                            file
                            for file in item.rglob("*")
                            if file.suffix.lower() in config_suffix
                        ]

        # Check ./config/plugin_id.json etc.
        for suffix in config_suffix:
            file_path = (config_dir / plugin_id).with_suffix(suffix)
            if file_path.exists():
                response.append(file_path)
            else:
                # Case-insensitive check for file
                if config_dir.exists():
                    for item in config_dir.iterdir():
                        if (
                            item.is_file()
                            and item.stem.lower() == plugin_id.lower()
                            and item.suffix.lower() == suffix
                        ):
                            response.append(item)

        return [str(i) for i in response if not Path(i).stem.lower().endswith("_lang")]

    def list_config_files(self, plugin_id: str):
        config_path_list = self.find_plugin_config_paths(plugin_id)
        web_mapping = {}
        if config_path_list:
            try:
                config_dir = Path(config_path_list[0]).parent
                main_json_path = config_dir / "main.json"
                if main_json_path.exists():
                    with open(main_json_path, "r", encoding="UTF-8") as f:
                        main_json = json.load(f)
                        for cfg_name, html_name in main_json.items():
                            if isinstance(html_name, str) and html_name.endswith(
                                ".html"
                            ):
                                web_mapping[cfg_name] = True
            except Exception:
                pass

        files_info = []
        for p in config_path_list:
            path_obj = Path(p)
            if path_obj.name.lower() == "main.json":
                continue
            files_info.append(
                {
                    "path": p,
                    "name": path_obj.name,
                    "has_web": web_mapping.get(path_obj.name, False),
                }
            )
        return files_info

    def get_config(self):
        """获取原始配置字典"""
        return self.server.load_config_simple(
            "config.json", DEFALUT_CONFIG, echo_in_console=False
        )

    async def get_web_config(self):
        config = self.server.load_config_simple(
            "config.json", DEFALUT_CONFIG, echo_in_console=False
        )
        ai_api_key_value = config.get("ai_api_key", "")
        ai_api_key_configured = bool(ai_api_key_value and ai_api_key_value.strip())

        chat_message_count = 0
        try:
            chat_logger = ChatLogger()
            chat_message_count = chat_logger.get_message_count()
        except Exception:
            pass

        return {
            "host": config["host"],
            "port": config["port"],
            "super_admin_account": config["super_admin_account"],
            "disable_admin_login_web": config["disable_other_admin"],
            "enable_temp_login_password": config["allow_temp_password"],
            "qq_qr_login_enabled": config.get("qq_qr_login_enabled", True),
            "trusted_proxies": config.get("trusted_proxies", []) or [],
            "allowed_origins": config.get("allowed_origins", []) or [],
            # 多服面板合并
            "panel_role": config.get("panel_role", "master"),
            "panel_slaves": config.get("panel_slaves", []) or [],
            "panel_master": config.get(
                "panel_master", {"allowed_tokens": [], "allowed_master_ips": []}
            )
            or {"allowed_tokens": [], "allowed_master_ips": []},
            "ai_api_key": "",
            "ai_api_key_configured": ai_api_key_configured,
            "ai_model": config.get("ai_model", "deepseek-chat"),
            "ai_api_url": config.get(
                "ai_api_url", "https://api.deepseek.com/chat/completions"
            ),
            "mcdr_plugins_url": config.get(
                "mcdr_plugins_url",
                MCDR_OFFICIAL_CATALOGUE_URL,
            ),
            "pf_plugin_catalogue_url": PF_PLUGIN_CATALOGUE_URL,
            "repositories": config.get("repositories", []),
            "ssl_enabled": config.get("ssl_enabled", False),
            "ssl_certfile": config.get("ssl_certfile", ""),
            "ssl_keyfile": config.get("ssl_keyfile", ""),
            "ssl_keyfile_password": config.get("ssl_keyfile_password", ""),
            "public_chat_enabled": config.get("public_chat_enabled", False),
            "public_chat_to_game_enabled": config.get(
                "public_chat_to_game_enabled", False
            ),
            "chat_verification_expire_minutes": config.get(
                "chat_verification_expire_minutes", 10
            ),
            "chat_session_expire_hours": config.get("chat_session_expire_hours", 24),
            "force_standalone": config.get("force_standalone", False),
            "log_capture_compat_mode": config.get("log_capture_compat_mode", True),
            "icp_records": config.get("icp_records", []),
            "chat_message_count": chat_message_count,
        }

    def save_web_config(self, config_info):
        """保存 Web 配置（WebConfigSaveRequest：结构化字段，为 None 不修改）。"""
        web_config = self.server.load_config_simple(
            "config.json", DEFALUT_CONFIG, echo_in_console=False
        )

        if config_info.host is not None:
            web_config["host"] = config_info.host
        if config_info.port is not None:
            web_config["port"] = int(config_info.port)
        if config_info.super_account is not None:
            # 支持字符串类型的超级管理员账号（如QQ号）；纯数字字符串转为整数
            super_account_str = str(config_info.super_account).strip()
            if super_account_str.isdigit():
                web_config["super_admin_account"] = int(super_account_str)
            else:
                web_config["super_admin_account"] = super_account_str
        if config_info.disable_admin_login_web is not None:
            web_config["disable_other_admin"] = config_info.disable_admin_login_web
        if config_info.enable_temp_login_password is not None:
            web_config["allow_temp_password"] = config_info.enable_temp_login_password
        if config_info.ai_api_key is not None:
            web_config["ai_api_key"] = config_info.ai_api_key
        if config_info.ai_model is not None:
            web_config["ai_model"] = config_info.ai_model
        if config_info.ai_api_url is not None:
            web_config["ai_api_url"] = config_info.ai_api_url
        if config_info.mcdr_plugins_url is not None:
            web_config["mcdr_plugins_url"] = config_info.mcdr_plugins_url
        if config_info.repositories is not None:
            web_config["repositories"] = config_info.repositories
        if config_info.ssl_enabled is not None:
            web_config["ssl_enabled"] = config_info.ssl_enabled
        if config_info.ssl_certfile is not None:
            web_config["ssl_certfile"] = config_info.ssl_certfile
        if config_info.ssl_keyfile is not None:
            web_config["ssl_keyfile"] = config_info.ssl_keyfile
        if config_info.ssl_keyfile_password is not None:
            web_config["ssl_keyfile_password"] = config_info.ssl_keyfile_password
        if config_info.qq_qr_login_enabled is not None:
            web_config["qq_qr_login_enabled"] = config_info.qq_qr_login_enabled
        if config_info.trusted_proxies is not None:
            web_config["trusted_proxies"] = _clean_str_list(config_info.trusted_proxies)
        if config_info.allowed_origins is not None:
            allowed_origins = _clean_str_list(config_info.allowed_origins)
            invalid_origins = [item for item in allowed_origins if not is_valid_origin(item)]
            if invalid_origins:
                raise BusinessException(
                    f"allowed_origins 包含无效的 HTTP(S) 来源: {', '.join(invalid_origins)}",
                    status_code=400,
                    code="invalid_allowed_origins",
                    data={"invalid": invalid_origins},
                )
            web_config["allowed_origins"] = allowed_origins
        if config_info.public_chat_enabled is not None:
            web_config["public_chat_enabled"] = config_info.public_chat_enabled
        if config_info.public_chat_to_game_enabled is not None:
            web_config["public_chat_to_game_enabled"] = (
                config_info.public_chat_to_game_enabled
            )
        if config_info.chat_verification_expire_minutes is not None:
            web_config["chat_verification_expire_minutes"] = (
                config_info.chat_verification_expire_minutes
            )
        if config_info.chat_session_expire_hours is not None:
            web_config["chat_session_expire_hours"] = (
                config_info.chat_session_expire_hours
            )
        if config_info.force_standalone is not None:
            web_config["force_standalone"] = config_info.force_standalone
        if config_info.log_capture_compat_mode is not None:
            web_config["log_capture_compat_mode"] = config_info.log_capture_compat_mode
        if config_info.icp_records is not None:
            web_config["icp_records"] = config_info.icp_records
        # 多服面板合并
        if config_info.panel_role is not None:
            web_config["panel_role"] = config_info.panel_role
        if config_info.panel_slaves is not None:
            web_config["panel_slaves"] = config_info.panel_slaves
        if config_info.panel_master is not None:
            web_config["panel_master"] = config_info.panel_master

        try:
            config_dir = self.server.get_data_folder()
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            config_path = Path(config_dir) / "config.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(web_config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"保存配置文件时出错: {e}")
            raise BusinessException(
                f"保存配置文件失败: {str(e)}",
                status_code=500,
                code="config_save_failed",
            )
        return {"status": "success", "message": "配置已保存，修改后生效情况请查看页面提示"}

    def load_config(
        self, path: str, translation: bool = False, config_type: str = "auto"
    ):
        """加载配置文件，统一返回 {path, type, content, config_data} 文档。

        文件不存在/越权/读取异常抛 BusinessException（404/403/500），不再返回 {}。
        """
        try:
            path_obj = SafePath.get_safe_path(path, get_base_dirs(self.server))
        except ValueError as e:
            raise BusinessException(
                str(e), status_code=403, code="path_not_allowed"
            )

        config_dir = path_obj.parent
        main_json_path = config_dir / "main.json"

        if config_type == "auto":
            main_config = {}
            if main_json_path.exists():
                try:
                    with open(main_json_path, "r", encoding="UTF-8") as f:
                        main_config = json.load(f)
                except Exception:
                    pass
            config_value = main_config.get(path_obj.name)
            if config_value:
                html_path = config_dir / config_value
                if html_path.exists() and html_path.suffix == ".html":
                    try:
                        with open(html_path, "r", encoding="UTF-8") as f:
                            return {
                                "path": path,
                                "type": "html",
                                "content": f.read(),
                                "config_data": None,
                            }
                    except Exception as e:
                        raise BusinessException(
                            f"Failed to read HTML file: {str(e)}",
                            status_code=500,
                            code="html_read_failed",
                        )

        if translation:
            if path_obj.suffix in [".json", ".properties"]:
                path_obj = path_obj.with_stem(f"{path_obj.stem}_lang")
            if path_obj.suffix == ".properties":
                path_obj = path_obj.with_suffix(f".json")

        if not path_obj.exists():
            raise BusinessException(
                f"Config file not found: {path}",
                status_code=404,
                code="config_file_not_found",
            )

        raw_text = ""
        config = {}
        suffix = path_obj.suffix.lower()
        try:
            with open(path_obj, "r", encoding="UTF-8") as f:
                raw_text = f.read()
                f.seek(0)
                if suffix == ".json":
                    config = json.load(f)
                elif suffix in [".yml", ".yaml"]:
                    from ..utils.table import yaml

                    config = yaml.load(f)
                elif suffix == ".properties":
                    import javaproperties

                    config = javaproperties.load(f)
                    config = {
                        k: (
                            v
                            if v not in ["true", "false"]
                            else True if v == "true" else False
                        )
                        for k, v in config.items()
                    }
                elif suffix == ".html":
                    # 直接加载插件页等 HTML（无 main.json 映射时仍走此分支）
                    return {
                        "path": path,
                        "type": "html",
                        "content": raw_text or "",
                        "config_data": None,
                    }
                else:
                    return {
                        "path": path,
                        "type": "text",
                        "content": raw_text,
                        "config_data": {},
                    }
        except Exception as e:
            raise BusinessException(
                f"读取配置文件失败: {str(e)}",
                status_code=500,
                code="config_read_failed",
            )

        if translation:
            if suffix == ".json":
                try:
                    config = self._maybe_nest_i18n(
                        build_json_i18n_translations(config)
                    )
                    return {
                        "path": path,
                        "type": "i18n",
                        "content": raw_text,
                        "config_data": config,
                    }
                except Exception:
                    pass
            elif suffix in [".yml", ".yaml"]:
                try:
                    config = self._maybe_nest_i18n(
                        build_yaml_i18n_translations(config, raw_text)
                    )
                    return {
                        "path": path,
                        "type": "i18n",
                        "content": raw_text,
                        "config_data": config,
                    }
                except Exception:
                    return {
                        "path": path,
                        "type": "i18n",
                        "content": raw_text,
                        "config_data": get_comment(config),
                    }

        type_map = {
            ".json": "json",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".properties": "properties",
        }
        return {
            "path": path,
            "type": type_map.get(suffix, "text"),
            "content": raw_text,
            "config_data": config,
        }

    def _assert_not_webui_config(self, config_path: Path):
        """阻止通过配置编辑入口改写 guguwebui 自身的 config.json"""
        parts = [p.lower() for p in config_path.parts]
        if config_path.name.lower() == "config.json" and "guguwebui" in parts:
            raise BusinessException(
                "无法在此处修改guguwebui配置文件",
                status_code=403,
                code="protected_file",
            )

    def save_config(self, file_path: str, config_data: dict):
        try:
            config_path = SafePath.get_safe_path(file_path, get_base_dirs(self.server))
        except ValueError as e:
            raise BusinessException(
                str(e), status_code=403, code="path_not_allowed"
            )

        if config_path == Path(self.server.get_data_folder()) / "config.json":
            raise BusinessException(
                "无法在此处修改guguwebui配置文件",
                status_code=403,
                code="protected_file",
            )

        if not config_path.exists():
            raise BusinessException(
                f"Config file not found: {file_path}",
                status_code=404,
                code="config_file_not_found",
            )

        try:
            with open(config_path, "r", encoding="UTF-8") as f:
                if config_path.suffix == ".json":
                    data = json.load(f)
                elif config_path.suffix in [".yml", ".yaml"]:
                    from ..utils.table import yaml

                    data = yaml.load(f)
                elif config_path.suffix == ".properties":
                    import javaproperties

                    data = javaproperties.load(f)
                    config_data = {
                        k: v if not isinstance(v, bool) else "true" if v else "false"
                        for k, v in config_data.items()
                    }

            if config_path.suffix == ".json":
                if config_path.name == "help_msg.json" and isinstance(
                    config_data, dict
                ):
                    for field in ["admin_help_msg", "group_help_msg"]:
                        if field in config_data:
                            data[field] = config_data[field]
                elif (
                    isinstance(config_data, dict)
                    and len(config_data) == 0
                    and len(data) > 0
                ):
                    data.clear()
                else:
                    consistent_type_update(data, config_data, remove_missing=True)
            else:
                consistent_type_update(data, config_data, remove_missing=False)

            with open(config_path, "w", encoding="UTF-8") as f:
                if config_path.suffix == ".json":
                    json.dump(data, f, ensure_ascii=False, indent=4)
                elif config_path.suffix in [".yml", ".yaml"]:
                    from ..utils.table import yaml

                    yaml.dump(data, f)
                elif config_path.suffix == ".properties":
                    import javaproperties

                    javaproperties.dump(data, f)
            return {"status": "success", "message": "配置文件保存成功"}
        except BusinessException:
            raise
        except Exception as e:
            logger.error(f"Error saving config file: {e}")
            raise BusinessException(
                f"Error saving config file: {str(e)}",
                status_code=500,
                code="config_save_failed",
            )

    def setup_rcon(self):
        """一键启用 RCON：密码仅写入配置文件，不随响应返回。"""
        try:
            mc_server_port = get_server_port(self.server)
            rcon_host = "127.0.0.1"
            rcon_port = self._find_available_port(mc_server_port + 1, rcon_host)
            rcon_password = self._generate_random_password(16)

            # Update server.properties
            if not SERVER_PROPERTIES_PATH.exists():
                raise BusinessException(
                    "找不到server.properties文件",
                    status_code=404,
                    code="server_properties_not_found",
                )
            import javaproperties

            with open(SERVER_PROPERTIES_PATH, "r", encoding="UTF-8") as f:
                mc_config = javaproperties.load(f)
            mc_config.update(
                {
                    "enable-rcon": "true",
                    "rcon.port": str(rcon_port),
                    "rcon.password": rcon_password,
                    "broadcast-rcon-to-ops": "false",
                }
            )
            with open(SERVER_PROPERTIES_PATH, "w", encoding="UTF-8") as f:
                javaproperties.dump(mc_config, f)

            # Update MCDR config.yml
            config_path = Path("config.yml")
            if not config_path.exists():
                raise BusinessException(
                    "找不到MCDR config.yml文件",
                    status_code=404,
                    code="mcdr_config_not_found",
                )
            from ..utils.table import yaml

            with open(config_path, "r", encoding="UTF-8") as f:
                mcdr_config = yaml.load(f)
            if "rcon" not in mcdr_config:
                mcdr_config["rcon"] = {}
            mcdr_config["rcon"].update(
                {
                    "enable": True,
                    "address": rcon_host,
                    "port": rcon_port,
                    "password": rcon_password,
                }
            )
            with open(config_path, "w", encoding="UTF-8") as f:
                yaml.dump(mcdr_config, f)

            api_cache.invalidate("rcon_status")
            self.server.execute_command("!!MCDR reload config")
            return {
                "status": "success",
                "message": "RCON配置已成功启用",
                "config": {
                    "rcon_host": rcon_host,
                    "rcon_port": rcon_port,
                },
            }
        except BusinessException:
            raise
        except Exception as e:
            raise BusinessException(
                f"配置RCON时发生错误: {str(e)}",
                status_code=500,
                code="rcon_setup_failed",
            )

    def _maybe_nest_i18n(self, i18n: dict) -> dict:
        try:
            if not isinstance(i18n, dict):
                return i18n
            trans = i18n.get("translations", {})
            if isinstance(trans, dict):
                for lang, mapping in list(trans.items()):
                    if isinstance(mapping, dict) and any(
                        isinstance(k, str) and "." in k for k in mapping.keys()
                    ):
                        trans[lang] = self._nest_translation_map_simple(mapping)
            i18n["translations"] = trans
            return i18n
        except Exception:
            return i18n

    def _nest_translation_map_simple(self, flat_map: dict) -> dict:
        nested = {}
        for full_key, meta in (flat_map or {}).items():
            if not isinstance(full_key, str):
                continue
            parts = [p for p in full_key.split(".") if p]
            cur = nested
            for i, part in enumerate(parts):
                if part not in cur or not isinstance(cur.get(part), dict):
                    cur[part] = {"name": None, "desc": None, "children": {}}
                if i == len(parts) - 1 and isinstance(meta, dict):
                    if "name" in meta:
                        cur[part]["name"] = meta.get("name")
                    if "desc" in meta:
                        cur[part]["desc"] = meta.get("desc")
                cur = cur[part]["children"]
        return nested

    def _find_available_port(self, start_port: int, host: str = "127.0.0.1") -> int:
        port = start_port
        while port <= 65535:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind((host, port))
                    return port
            except socket.error:
                port += 1
        raise RuntimeError(f"无法找到可用端口")

    def _generate_random_password(self, length: int = 16) -> str:
        return "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(length)
        )

    def save_config_file_raw(self, path: str, content: str):
        """以原始文本方式保存配置（SafePath 校验后写入，文件必须存在）。"""
        try:
            config_path = SafePath.get_safe_path(path, get_base_dirs(self.server))
        except ValueError as e:
            raise BusinessException(
                str(e), status_code=403, code="path_not_allowed"
            )

        self._assert_not_webui_config(config_path)
        if not config_path.exists():
            raise BusinessException(
                f"Config file not found: {path}",
                status_code=404,
                code="config_file_not_found",
            )

        try:
            with open(config_path, "w", encoding="utf-8") as file:
                file.write(content)
            return {"status": "success", "message": f"{path} saved successfully"}
        except Exception as e:
            raise BusinessException(
                f"保存文件失败: {str(e)}",
                status_code=500,
                code="config_save_failed",
            )
