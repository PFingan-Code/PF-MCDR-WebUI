from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class BusinessException(Exception):
    """业务异常基类（统一错误体见 structures/envelope.py）"""
    def __init__(self, message: str, status_code: int = 400, data: Any = None, code: str = "error"):
        self.message = message
        self.status_code = status_code
        self.data = data
        self.code = code
        super().__init__(message)


class AuthenticationException(BusinessException):
    """认证异常"""
    def __init__(self, message: str = "未登录或会话已过期", data: Any = None, code: str = "unauthorized"):
        super().__init__(message, status_code=401, data=data, code=code)


class ForbiddenException(BusinessException):
    """权限异常"""
    def __init__(self, message: str = "权限不足", data: Any = None, code: str = "forbidden"):
        super().__init__(message, status_code=403, data=data, code=code)


class NotFoundException(BusinessException):
    """资源未找到异常"""
    def __init__(self, message: str = "资源未找到", data: Any = None, code: str = "not_found"):
        super().__init__(message, status_code=404, data=data, code=code)


class LoginData(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    remember: Optional[bool] = False


class SaveConfig(BaseModel):
    action: str
    host: Optional[str] = None
    port: Optional[str] = None
    super_account: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None
    ai_api_url: Optional[str] = None
    mcdr_plugins_url: Optional[str] = None
    repositories: Optional[list] = None
    ssl_enabled: Optional[bool] = None
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None
    ssl_keyfile_password: Optional[str] = None
    public_chat_enabled: Optional[bool] = None
    public_chat_to_game_enabled: Optional[bool] = None
    chat_verification_expire_minutes: Optional[int] = None
    chat_session_expire_hours: Optional[int] = None
    force_standalone: Optional[bool] = None
    log_capture_compat_mode: Optional[bool] = None
    icp_records: Optional[list] = None
    # 多服面板合并
    panel_role: Optional[str] = None  # "master" | "slave"
    panel_slaves: Optional[list] = None
    panel_master: Optional[dict] = None


class WebConfigSaveRequest(BaseModel):
    """Web 配置保存（REST 结构化字段，无 action 分派；为 None 的字段不修改）"""
    host: Optional[str] = None
    port: Optional[str] = None
    super_account: Optional[str] = None
    disable_admin_login_web: Optional[bool] = None
    enable_temp_login_password: Optional[bool] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None
    ai_api_url: Optional[str] = None
    mcdr_plugins_url: Optional[str] = None
    repositories: Optional[list] = None
    ssl_enabled: Optional[bool] = None
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None
    ssl_keyfile_password: Optional[str] = None
    public_chat_enabled: Optional[bool] = None
    public_chat_to_game_enabled: Optional[bool] = None
    chat_verification_expire_minutes: Optional[int] = None
    chat_session_expire_hours: Optional[int] = None
    force_standalone: Optional[bool] = None
    log_capture_compat_mode: Optional[bool] = None
    icp_records: Optional[list] = None
    # 多服面板合并
    panel_role: Optional[str] = None  # "master" | "slave"
    panel_slaves: Optional[list] = None
    panel_master: Optional[dict] = None


# Pip包管理相关模型
class PipTaskCreateRequest(BaseModel):
    """发起 pip 安装/卸载任务（install/uninstall 合并为任务创建资源）"""
    action: str  # "install" | "uninstall"
    package: str


class PipPackageRequest(BaseModel):
    package: str


class ToggleConfig(BaseModel):
    plugin_id: str
    status: bool


class ModToggleRequest(BaseModel):
    """启用/禁用模组（目标文件名移入路径参数 PUT /mods/{filename}/enabled）"""
    enabled: bool
    acknowledge_warnings: bool = False


class ModFileRequest(BaseModel):
    filename: str


class ModConfigSaveRequest(BaseModel):
    path: str
    content: Optional[str] = None
    config_data: Optional[Any] = None


class ModSettingsRequest(BaseModel):
    upload_max_mib: int


class SaveContent(BaseModel):
    action: str
    content: str


class PluginInfo(BaseModel):
    plugin_id: str


class PimInstallRequest(BaseModel):
    plugin_id: str
    version: Optional[str] = None
    repo_url: Optional[str] = None


class PimUninstallRequest(BaseModel):
    plugin_id: str


class ConfigData(BaseModel):
    file_path: str
    config_data: dict


class DeepseekQuery(BaseModel):
    query: str
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None


# 玩家管理相关模型（目标名/IP 移入路径参数，body 只留类型与原因）
class PlayerBanRequest(BaseModel):
    """封禁玩家 / IP：type 为 "player" 或 "ip"（未知值 → 400 invalid_type）"""
    type: str = "player"
    reason: Optional[str] = None


class PlayerUnbanRequest(BaseModel):
    """解封玩家 / IP：type 为 "player" 或 "ip"（未知值 → 400 invalid_type）"""
    type: str = "player"


class PlayerKickRequest(BaseModel):
    reason: Optional[str] = None


class WhitelistSetRequest(BaseModel):
    enabled: bool


# 公开聊天相关模型（Pydantic 入参，类型/长度校验 422 走统一错误体）
class ChatLoginRequest(BaseModel):
    """聊天页用户登录"""
    player_id: str
    password: str


class ChatSetPasswordRequest(BaseModel):
    """设置聊天页用户密码（code 为游戏内验证码，路径 name 须与其绑定玩家一致）"""
    code: str
    password: str


class ChatMessageCreateRequest(BaseModel):
    """发送聊天消息（管理员通道可省略 session_id）"""
    message: str
    player_id: str
    session_id: Optional[str] = None


# 面板合并 / 配对模型（Pydantic 入参，枚举/长度校验 422 走统一错误体）
class PanelMergeConfigRequest(BaseModel):
    """面板合并配置（PUT /api/panel_merge_config 结构化 body，替代原 POST 手写 json）"""
    panel_role: Literal["master", "slave"] = "master"
    panel_slaves: Optional[list] = None
    panel_master: Optional[dict] = None


class PairingRequest(BaseModel):
    """主服 → 子服连接请求（无需登录；master_name 可选）"""
    master_name: Optional[str] = Field(default="", max_length=64)


class PairingDecisionRequest(BaseModel):
    """确认/拒绝待处理配对请求"""
    request_id: str = Field(..., min_length=1)


class PairingConnectRequest(BaseModel):
    """主服向目标子服发起配对连接"""
    slave_name: str = Field(..., min_length=1, max_length=64)
    base_url: str = Field(..., min_length=1, max_length=2048)
