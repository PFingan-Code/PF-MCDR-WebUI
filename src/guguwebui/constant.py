from pathlib import Path

import ruamel.yaml
from passlib.context import CryptContext

from guguwebui.utils.storage import SQLiteTable
from guguwebui.utils.storage_migration import ensure_storage_layout

ALGORITHM = "HS256"
# SECRET_KEY 在文件末尾生成：首次启动随机生成并持久化到 SQLite（见 _load_session_secret）
STATIC_PATH = "./config/guguwebui/guguwebui_static"
DATA_DB_PATH = Path("./config") / "guguwebui" / "guguwebui.sqlite3"
USER_DB_PATH = Path(STATIC_PATH) / "db.json"
AUDIT_LOG_PATH = Path(STATIC_PATH) / "audit_log.bin"
PLAYER_STATS_PATH = Path(STATIC_PATH) / "player_stats.json"

# These are also used when an older/incomplete .mcdr package does not contain
# the optional custom assets. They are intentionally minimal and user-editable.
CUSTOM_FILE_DEFAULTS = {
    "css": "/* 在此处添加全局样式 */\n* {\n    padding: 0;\n    margin: 0;\n}\n",
    "js": "// 在此处添加全局脚本\n",
}


def get_static_path(server=None) -> Path:
    """Return the plugin data directory without depending on process cwd."""
    if server is not None:
        try:
            data_folder = server.get_data_folder()
            if data_folder:
                return Path(data_folder) / "guguwebui_static"
        except Exception:
            pass
    return Path(STATIC_PATH)


def get_custom_file_path(server, file_type: str) -> Path:
    if file_type not in CUSTOM_FILE_DEFAULTS:
        raise ValueError(f"不支持的自定义文件类型: {file_type}")
    return get_static_path(server) / "custom" / (
        "overall.css" if file_type == "css" else "overall.js"
    )
# 玩家会话日志保留天数（在线情况统计的历史窗口）
PLAYER_STATS_SESSION_RETENTION_DAYS = 90
PATH_DB_PATH = Path("./config") / "guguwebui" / "config_path.json"

# 插件网页 api_handler：multipart 单文件字段默认最大字节数（超过则 413）
PLUGIN_API_MAX_UPLOAD_BYTES = 1 * 1024 * 1024  # 1 MiB

CSS_FILE = Path(STATIC_PATH) / "custom" / "overall.css"
JS_FILE = Path(STATIC_PATH) / "custom" / "overall.js"

# SERVER_PATH 读config.yml的 working_directory值
CONFIG_FILE_PATH = Path("./config.yml")
yaml = ruamel.yaml.YAML()
with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as config_file:
    config = yaml.load(config_file)
SERVER_PATH = Path(config.get("working_directory", "server"))

SERVER_PROPERTIES_PATH = SERVER_PATH / "server.properties"

# 仓库与项目链接
MCDR_OFFICIAL_CATALOGUE_URL = "https://api.mcdreforged.com/catalogue/everything_slim.json.xz"
PF_PLUGIN_CATALOGUE_URL = "https://pfingan-code.github.io/PluginCatalogue/plugins.json"
PROJECT_GITHUB_URL = "https://github.com/PFingan-Code/PF-MCDR-WebUI"
MCDR_SITE_URL = "https://mcdreforged.com"
MCDR_PLUGINS_PAGE_URL = "https://mcdreforged.com/zh-CN/plugins"

# 收敛“官方 + PF + 自定义仓库”的组装逻辑（R1），供 PIM / 在线插件接口复用
def collect_repository_entries(config=None):
    """返回仓库条目列表，每项: {url, name, is_official, is_loose}（去重，保序）"""
    config = config or {}
    entries = []
    seen = set()

    def _append(url, name=None, is_official=False, is_loose=False):
        url = (url or "").strip()
        if not url or url in seen:
            return
        seen.add(url)
        entries.append({
            "url": url,
            "name": name,
            "is_official": is_official,
            "is_loose": is_loose,
        })

    official_url = str(config.get("mcdr_plugins_url") or MCDR_OFFICIAL_CATALOGUE_URL)
    _append(official_url, None, is_official=True)
    if PF_PLUGIN_CATALOGUE_URL:
        _append(PF_PLUGIN_CATALOGUE_URL, None, is_loose=True)
    for repo in (config.get("repositories") or []):
        if isinstance(repo, dict) and isinstance(repo.get("url"), str):
            name = repo.get("name")
            if not isinstance(name, str) or not name.strip():
                name = None
            _append(repo["url"], name)
    return entries


def collect_repository_urls(config=None):
    """仅返回仓库 URL 列表（顺序：官方、PF、自定义）"""
    return [e["url"] for e in collect_repository_entries(config)]


pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# token: {token : {expire_time, user_name}}
# user : {username: password}
# temp : {temppassword: expire_time} 或 {temppassword: {expire_time: str, qq_id: str}} (新格式，如果关联了QQ号)
# chat_users: {player_id: {password: hashed_password, created_time: timestamp}}
# chat_verification: {code: {player_id: None, expire_time: timestamp, used: False}}
# chat_sessions: {session_id: {player_id: player_id, expire_time: timestamp}}
# qq_nicknames: {qq_id: nickname} - QQ号到昵称的映射
DEFALUT_DB = {
    "token": {},
    "user": {},
    "temp": {},
    "chat_users": {},
    "chat_verification": {},
    "chat_sessions": {},
    "qq_nicknames": {},
}
DEFALUT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8000,
    "super_admin_account": 123456789123456789,
    "disable_other_admin": False,
    "allow_temp_password": True,
    "force_standalone": False,  # 是否强制独立运行（忽略fastapi_mcdr插件）
    "log_capture_compat_mode": True,  # 日志捕获兼容模式（通过读取日志文件获取）
    "ai_api_key": "",  # AI API密钥
    "ai_model": "deepseek-chat",  # AI模型名称
    "ai_api_url": "https://api.deepseek.com/chat/completions",  # 自定义API链接
    "mcdr_plugins_url": MCDR_OFFICIAL_CATALOGUE_URL,  # MCDR插件目录URL
    "repositories": [],  # 多仓库配置列表
    "ssl_enabled": False,  # 是否启用HTTPS
    "ssl_certfile": "",  # SSL证书文件路径
    "ssl_keyfile": "",  # SSL密钥文件路径
    "ssl_keyfile_password": "",  # SSL密钥文件密码（如果有）
    "public_chat_enabled": False,  # 是否启用公开聊天页
    "public_chat_to_game_enabled": False,  # 公开聊天页发送消息到游戏
    "chat_verification_expire_minutes": 10,  # 聊天页验证码过期时间（分钟）
    "chat_session_expire_hours": 24,  # 聊天页会话过期时间（小时）
    "mod_upload_max_bytes": 10 * 1024 * 1024,  # 单个模组上传上限，默认 10 MiB
    "icp_records": [],  # ICP备案信息，最多两个，每个包含 icp 和 url 字段
    # 示例配置（请在 config.json 中添加）：
    # "icp_records": [
    #     {"icp": "浙ICP备12345678号", "url": "https://beian.miit.gov.cn/"},
    #     {"icp": "浙公网安备33010602000123号", "url": "https://www.beian.gov.cn/"}
    # ]
    
    "panel_role": "master", # "master" (主服模式) | "slave" (子服模式)
    # 主服模式：子服连接信息列表
    # item: {id, name, base_url, token, enabled, verify_tls}
    "panel_slaves": [], # 子服连接信息列表
    # 子服模式：允许的主服调用 token 列表
    # allowed_tokens item: {token, enabled, name, created_at}
    # allowed_master_ips: 可选限制主服来源 IP（空表示不限制）
    "panel_master": {"allowed_tokens": [], "allowed_master_ips": []},
}

ensure_storage_layout()
user_db = SQLiteTable("user_db", default_content=DEFALUT_DB)


def _load_session_secret() -> str:
    """读取会话签名密钥；不存在（或为旧版固定值）时随机生成并持久化。

    密钥只用于签名 Starlette session cookie。轮换后旧 session cookie 失效，
    但鉴权以 token cookie 为准，已登录用户不受影响。
    """
    import secrets as _secrets

    from guguwebui.utils.storage import get_state as _get_state
    from guguwebui.utils.storage import set_state as _set_state

    value = _get_state("secrets", "session_secret_key", None)
    if isinstance(value, str) and len(value) >= 32 and value != "guguwebui":
        return value
    value = _secrets.token_urlsafe(48)
    _set_state("secrets", "session_secret_key", value)
    return value


SECRET_KEY = _load_session_secret()
