import ipaddress
import logging
import socket
from typing import Any, Dict, Tuple

from guguwebui.constant import DEFALUT_CONFIG


class ConfigValidator:
    """配置验证器，用于验证配置项的类型、格式和值范围"""

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.validation_errors = []
        self.warnings = []

    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], bool]:
        """
        验证配置并返回验证结果、修正后的配置和是否有关键错误

        Args:
            config: 要验证的配置字典

        Returns:
            (is_valid, corrected_config, has_critical_error):
            - is_valid: 整体验证是否通过
            - corrected_config: 修正后的配置
            - has_critical_error: 是否有关键错误（IP/端口错误）
        """
        self.validation_errors = []
        self.warnings = []

        # 创建配置副本进行验证
        validated_config = config.copy()

        # 先验证所有配置项
        self._validate_other_configs(config, validated_config)

        # 验证IP和端口（关键配置）
        has_critical_error = not self._validate_host_port(config, validated_config)

        # 如果有严重错误，返回False
        if self.validation_errors:
            return False, validated_config, has_critical_error

        # 输出警告信息
        for warning in self.warnings:
            self.logger.warning(warning)

        return True, validated_config, has_critical_error

    def _validate_host_port(self, config: Dict[str, Any], validated_config: Dict[str, Any]) -> bool:
        """验证IP地址和端口配置"""
        host = config.get('host', '')
        port = config.get('port', 0)

        # 验证IP地址
        if not self._is_valid_ip(host):
            self.validation_errors.append(f"无效的IP地址: {host}")
            return False

        # 验证端口并统一为整数。端口是否可用由实际 Uvicorn bind 决定，
        # 避免“预检成功后被抢占”的 TOCTOU 竞态。
        if not self._is_valid_port(port):
            self.validation_errors.append(f"无效的端口号: {port}")
            return False
        port = int(port)
        validated_config["port"] = port

        # 检查是否与Minecraft服务器端口冲突
        if not self._check_minecraft_port_conflict(port):
            self.validation_errors.append(f"端口 {port} 与Minecraft服务器端口冲突，无法启动Web服务")
            return False

        return True

    def _validate_other_configs(self, config: Dict[str, Any], validated_config: Dict[str, Any]):
        """验证其他配置项"""
        # 验证super_admin_account
        super_account = config.get('super_admin_account')
        if not isinstance(super_account, (int, str)):
            self.warnings.append(f"super_admin_account 类型错误，期望 int 或 str，实际: {type(super_account)}")
            validated_config['super_admin_account'] = DEFALUT_CONFIG['super_admin_account']
        elif isinstance(super_account, str) and not super_account.isdigit():
            self.warnings.append(f"super_admin_account 格式错误: {super_account}")
            validated_config['super_admin_account'] = DEFALUT_CONFIG['super_admin_account']

        # 验证布尔值配置
        bool_configs = [
            'disable_other_admin', 'allow_temp_password', 'force_standalone',
            'ssl_enabled', 'public_chat_enabled', 'public_chat_to_game_enabled'
        ]
        for key in bool_configs:
            value = config.get(key)
            if not isinstance(value, bool):
                self.warnings.append(f"{key} 类型错误，期望 bool，实际: {type(value)}")
                validated_config[key] = DEFALUT_CONFIG[key]

        # 验证字符串配置
        string_configs = [
            'ai_api_key', 'ai_model', 'ai_api_url', 'mcdr_plugins_url',
            'ssl_certfile', 'ssl_keyfile', 'ssl_keyfile_password',
            'panel_role'
        ]
        for key in string_configs:
            value = config.get(key)
            if not isinstance(value, str):
                self.warnings.append(f"{key} 类型错误，期望 str，实际: {type(value)}")
                validated_config[key] = DEFALUT_CONFIG[key]
            elif key == "panel_role" and value not in ["master", "slave"]:
                self.warnings.append(f"panel_role 值无效，期望 master/slave，实际: {value}")
                validated_config[key] = DEFALUT_CONFIG["panel_role"]

        # 验证列表配置
        repositories = config.get('repositories')
        if not isinstance(repositories, list):
            self.warnings.append(f"repositories 类型错误，期望 list，实际: {type(repositories)}")
            validated_config['repositories'] = DEFALUT_CONFIG['repositories']

        panel_slaves = config.get('panel_slaves')
        if panel_slaves is None:
            panel_slaves = []
        if not isinstance(panel_slaves, list):
            self.warnings.append(f"panel_slaves 类型错误，期望 list，实际: {type(panel_slaves)}")
            validated_config['panel_slaves'] = DEFALUT_CONFIG['panel_slaves']

        panel_master = config.get('panel_master')
        if panel_master is None:
            panel_master = DEFALUT_CONFIG.get('panel_master', {})
        if not isinstance(panel_master, dict):
            self.warnings.append(f"panel_master 类型错误，期望 dict，实际: {type(panel_master)}")
            validated_config['panel_master'] = DEFALUT_CONFIG.get('panel_master', {"allowed_tokens": [], "allowed_master_ips": []})
        else:
            allowed_tokens = panel_master.get("allowed_tokens", [])
            if not isinstance(allowed_tokens, list):
                self.warnings.append(f"panel_master.allowed_tokens 类型错误，期望 list，实际: {type(allowed_tokens)}")
                panel_master["allowed_tokens"] = []
            allowed_master_ips = panel_master.get("allowed_master_ips", [])
            if not isinstance(allowed_master_ips, list):
                self.warnings.append(f"panel_master.allowed_master_ips 类型错误，期望 list，实际: {type(allowed_master_ips)}")
                panel_master["allowed_master_ips"] = []
            validated_config['panel_master'] = panel_master

        # 验证ICP备案配置
        icp_records = config.get('icp_records')
        if not isinstance(icp_records, list):
            self.warnings.append(f"icp_records 类型错误，期望 list，实际: {type(icp_records)}")
            validated_config['icp_records'] = DEFALUT_CONFIG['icp_records']
        else:
            # 验证ICP备案数量（最多两个）
            if len(icp_records) > 2:
                self.warnings.append(f"icp_records 数量超出限制，最多支持2个备案，当前: {len(icp_records)}")
                validated_config['icp_records'] = icp_records[:2]  # 只保留前两个

            # 验证每个备案的格式
            validated_icp_records = []
            for i, record in enumerate(icp_records):
                if not isinstance(record, dict):
                    self.warnings.append(f"icp_records[{i}] 类型错误，期望 dict，实际: {type(record)}")
                    continue

                icp = record.get('icp', '').strip()
                url = record.get('url', '').strip()

                if not icp:
                    self.warnings.append(f"icp_records[{i}] 缺少 icp 字段或为空")
                    continue

                if not url:
                    self.warnings.append(f"icp_records[{i}] 缺少 url 字段或为空")
                    continue

                # 验证URL格式
                if not url.startswith(('http://', 'https://')):
                    self.warnings.append(f"icp_records[{i}] url 格式错误，期望以 http:// 或 https:// 开头: {url}")
                    continue

                validated_icp_records.append({'icp': icp, 'url': url})

            validated_config['icp_records'] = validated_icp_records

        # 验证整数配置
        int_configs = [
            'chat_verification_expire_minutes', 'chat_session_expire_hours',
            'mod_upload_max_bytes'
        ]
        for key in int_configs:
            value = config.get(key)
            if not isinstance(value, int):
                self.warnings.append(f"{key} 类型错误，期望 int，实际: {type(value)}")
                validated_config[key] = DEFALUT_CONFIG[key]
            elif value <= 0:
                self.warnings.append(f"{key} 值超出范围，期望 > 0，实际: {value}")
                validated_config[key] = DEFALUT_CONFIG[key]

        upload_limit = validated_config.get('mod_upload_max_bytes')
        if isinstance(upload_limit, int) and not (1024 * 1024 <= upload_limit <= 4096 * 1024 * 1024):
            self.warnings.append(
                f"mod_upload_max_bytes 值超出范围，期望 1-4096 MiB，实际: {upload_limit}"
            )
            validated_config['mod_upload_max_bytes'] = DEFALUT_CONFIG['mod_upload_max_bytes']

        # 验证AI模型配置
        ai_model = config.get('ai_model')
        if ai_model and not isinstance(ai_model, str):
            self.warnings.append(f"ai_model 类型错误，期望 str，实际: {type(ai_model)}")
            validated_config['ai_model'] = DEFALUT_CONFIG['ai_model']

        # 验证AI API URL格式
        ai_api_url = config.get('ai_api_url')
        if ai_api_url and isinstance(ai_api_url, str):
            if not ai_api_url.startswith(('http://', 'https://')):
                self.warnings.append(f"ai_api_url 格式错误，期望以 http:// 或 https:// 开头: {ai_api_url}")
                validated_config['ai_api_url'] = DEFALUT_CONFIG['ai_api_url']

        # 验证MCDR插件URL格式
        mcdr_plugins_url = config.get('mcdr_plugins_url')
        if mcdr_plugins_url and isinstance(mcdr_plugins_url, str):
            if not mcdr_plugins_url.startswith(('http://', 'https://')):
                self.warnings.append(f"mcdr_plugins_url 格式错误，期望以 http:// 或 https:// 开头: {mcdr_plugins_url}")
                validated_config['mcdr_plugins_url'] = DEFALUT_CONFIG['mcdr_plugins_url']

    @staticmethod
    def _is_valid_ip(host: str) -> bool:
        """验证IP地址是否有效"""
        if not host:
            return False

        # 检查是否为 localhost / 常用 IPv4
        if host.lower() in ['localhost', '127.0.0.1', '0.0.0.0']:
            return True
        # 检查是否为常用 IPv6 字面量
        if host.lower() in ['::', '::1']:
            return True

        try:
            # 尝试解析IP地址
            ip = ipaddress.ip_address(host)
            # IPv4：检查是否为有效的 IPv4 地址（排除一些特殊地址）
            if ip.version == 4:
                if ip.is_loopback or ip.is_unspecified or ip.is_reserved:
                    return True
                if ip.is_private or ip.is_global:
                    return True
                return False
            # IPv6：接受有效解析的 IPv6 地址
            if ip.version == 6:
                return True
            return False
        except ValueError:
            return False

    @staticmethod
    def _is_valid_port(port) -> bool:
        """验证端口号是否有效"""
        try:
            port_int = int(port)
            return 1 <= port_int <= 65535
        except (ValueError, TypeError):
            return False

    def _is_port_available(self, host: str, port: int) -> bool:
        """检查端口是否可用（未被占用），支持 IPv4 与 IPv6"""
        try:
            # 根据 host 选择 socket 地址族
            try:
                ip = ipaddress.ip_address(host)
                family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            except ValueError:
                family = socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.bind((host, port))
                return True
        except socket.error as e:
            error_msg = str(e)
            error_code = getattr(e, 'winerror', None) or getattr(e, 'errno', None)

            # Windows错误代码
            if error_code == 10048:  # WSAEADDRINUSE - 地址已被使用
                self.logger.error(f"端口 {port} 已被占用，无法启动Web服务")
                return False
            elif error_code == 10049:  # WSAEADDRNOTAVAIL - 请求的地址无效
                self.logger.error(f"IP地址 {host} 无效或无法绑定，无法启动Web服务")
                return False
            elif error_code == 10013:  # WSAEACCES - 权限被拒绝（通常表示端口被占用或需要管理员权限）
                self.logger.error(f"端口 {port} 访问被拒绝，可能已被占用或需要管理员权限，无法启动Web服务")
                return False
            elif error_code == 10047:  # WSAEAFNOSUPPORT - 地址族不支持
                self.logger.error(f"IP地址 {host} 的地址族不支持，无法启动Web服务")
                return False
            elif error_code == 10022:  # WSAEINVAL - 参数无效
                self.logger.error(f"IP地址 {host} 或端口 {port} 参数无效，无法启动Web服务")
                return False
            # Unix/Linux错误代码
            elif error_code == 98:  # EADDRINUSE - 地址已被使用
                self.logger.error(f"端口 {port} 已被占用，无法启动Web服务")
                return False
            elif error_code == 99:  # EADDRNOTAVAIL - 地址不可用
                self.logger.error(f"IP地址 {host} 不可用，无法启动Web服务")
                return False
            elif error_code == 13:  # EACCES - 权限被拒绝
                self.logger.error(f"端口 {port} 访问被拒绝，可能已被占用或需要管理员权限，无法启动Web服务")
                return False
            # 通用错误消息匹配
            elif any(keyword in error_msg.lower() for keyword in [
                "address already in use", "端口已被使用", "address in use",
                "already in use", "已被使用", "in use"
            ]):
                self.logger.error(f"端口 {port} 已被占用，无法启动Web服务")
                return False
            elif any(keyword in error_msg.lower() for keyword in [
                "permission denied", "权限被拒绝", "access denied", "访问被拒绝",
                "not allowed", "不允许", "forbidden", "禁止"
            ]):
                self.logger.error(f"端口 {port} 访问被拒绝，可能已被占用或需要管理员权限，无法启动Web服务")
                return False
            else:
                self.logger.warning(f"检查端口 {port} 可用性时出错: {e}")
                # 如果检查失败，假设端口可用
                return True
        except Exception as e:
            self.logger.warning(f"检查端口 {port} 可用性时发生未知错误: {e}")
            # 如果检查失败，假设端口可用
            return True

    def _check_minecraft_port_conflict(self, port: int) -> bool:
        """检查端口是否与Minecraft服务器端口冲突"""
        try:
            # 获取Minecraft服务器配置
            from guguwebui.constant import SERVER_PROPERTIES_PATH

            if not SERVER_PROPERTIES_PATH.exists():
                self.logger.warning("无法找到server.properties文件，跳过Minecraft端口冲突检查")
                return True

            # 读取server.properties文件
            with open(SERVER_PROPERTIES_PATH, 'r', encoding='utf-8') as f:
                content = f.read()

            # 解析配置项
            config_lines = content.split('\n')
            minecraft_ports = set()

            for line in config_lines:
                line = line.strip()
                if line.startswith('server-port='):
                    try:
                        server_port = int(line.split('=')[1])
                        minecraft_ports.add(server_port)
                        self.logger.debug(f"检测到Minecraft服务器端口: {server_port}")
                    except (ValueError, IndexError):
                        pass

                elif line.startswith('enable-rcon=') and line.split('=')[1].lower() == 'true':
                    # 查找下一行的rcon.port
                    for next_line in config_lines[config_lines.index(line) + 1:]:
                        if next_line.strip().startswith('rcon.port='):
                            try:
                                rcon_port = int(next_line.split('=')[1])
                                minecraft_ports.add(rcon_port)
                                self.logger.debug(f"检测到Minecraft RCON端口: {rcon_port}")
                            except (ValueError, IndexError):
                                pass
                            break

                elif line.startswith('enable-query=') and line.split('=')[1].lower() == 'true':
                    # 查找下一行的query.port
                    for next_line in config_lines[config_lines.index(line) + 1:]:
                        if next_line.strip().startswith('query.port='):
                            try:
                                query_port = int(next_line.split('=')[1])
                                minecraft_ports.add(query_port)
                                self.logger.debug(f"检测到Minecraft Query端口: {query_port}")
                            except (ValueError, IndexError):
                                pass
                            break

            # 检查端口冲突
            if port in minecraft_ports:
                self.logger.error(f"端口 {port} 与Minecraft服务器端口冲突: {minecraft_ports}")
                return False

            if minecraft_ports:
                self.logger.info(f"Minecraft服务器使用的端口: {minecraft_ports}")

            return True

        except Exception as e:
            self.logger.warning(f"检查Minecraft端口冲突时出错: {e}")
            # 如果检查失败，假设没有冲突
            return True

    def get_validation_summary(self) -> str:
        """获取验证结果摘要"""
        if not self.validation_errors and not self.warnings:
            return "配置验证通过，所有配置项都有效"

        summary = []
        if self.validation_errors:
            summary.append("配置验证失败:")
            for error in self.validation_errors:
                summary.append(f"  ❌ {error}")
            summary.append("")
            summary.append("解决方案:")
            summary.append("  1. 检查配置文件中的host和port设置")
            summary.append("  2. 确保端口未被其他服务占用")
            summary.append("  3. 确保端口不与Minecraft服务器端口冲突")
            summary.append("  4. 检查IP地址格式是否正确")

        if self.warnings:
            summary.append("配置警告:")
            for warning in self.warnings:
                summary.append(f"  ⚠️  {warning}")
            summary.append("")
            summary.append("注意: 警告不会阻止插件启动，但建议修复这些问题")

        return "\n".join(summary)
