import json
import os
import re
import zipfile
from pathlib import Path


def compare_versions(a, b):
    """
    语义化版本比较（B7）：a > b 返回 1，a == b 返回 0，a < b 返回 -1。
    空字符串/非数字视为最低版本；避免 “1.10.0 < 1.9.0” 这类字典序误判。
    """
    def to_key(version):
        if version is None:
            version = ""
        version = str(version).strip()
        if not version:
            return (0,)
        numbers = tuple(int(x) for x in re.split(r"[^0-9]+", version) if x)
        return (1, numbers)

    ka, kb = to_key(a), to_key(b)
    if ka > kb:
        return 1
    if ka < kb:
        return -1
    return 0


def check_repository_cache(server):
    from guguwebui.PIM import PIMHelper
    try:
        pim_helper = PIMHelper(server)
        cache_dir = pim_helper.get_temp_dir()
        cache_file = os.path.join(cache_dir, "everything_slim.json")

        class CacheCheckSource:
            def __init__(self, server): self.server = server

            def reply(self, message): self.server.logger.info(f"[仓库缓存] {message}")

            def get_server(self): return self.server

        source = CacheCheckSource(server)
        if not os.path.exists(cache_file):
            server.logger.info("插件仓库缓存不存在，尝试下载")
            pim_helper.get_cata_meta(source, ignore_ttl=False)
            if os.path.exists(cache_file):
                server.logger.info("插件仓库缓存已成功下载")
            else:
                server.logger.warning("插件仓库缓存下载可能失败，但这是正常的，请参考日志了解详情")
                server.logger.info("WebUI将使用PIM模块的下载失败缓存机制，在15分钟内不会重复尝试下载失败的仓库")
        else:
            server.logger.debug("插件仓库缓存已存在")
    except Exception as e:
        server.logger.error(f"检查仓库缓存时出错: {e}")


def __copyFile(server, path, target_path):
    target_path = Path(target_path)
    if "custom" in target_path.parts and target_path.exists() and target_path.name != "server_lang.json":
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with server.open_bundled_file(path) as file_handler:
        message = file_handler.read()
    with open(target_path, 'wb') as f:
        f.write(message)


from .mcdr_adapter import MCDRAdapter


def __copyFolder(server, folder_path, target_folder):
    target_folder = Path(target_folder)
    target_folder.mkdir(parents=True, exist_ok=True)

    try:
        try:
            with server.open_bundled_file(folder_path) as _:
                __copyFile(server, folder_path, target_folder)
                return True
        except FileNotFoundError:
            pass
        except Exception as e:
            server.logger.debug(f"尝试作为文件处理'{folder_path}'时出错: {e}")
            pass

        items = MCDRAdapter.list_plugin_directory(server, folder_path)

        if not items:
            server.logger.warning(f"无法获取文件夹 '{folder_path}' 的内容列表")
            return False

        for item in items:
            if item == "__pycache__" or item == "utils" or item.endswith(".py"):
                server.logger.debug(f"忽略文件/文件夹: {item}")
                continue

            plugin_item_path = f"{folder_path}/{item}"
            target_item_path = target_folder / item

            try:
                with server.open_bundled_file(plugin_item_path) as _:
                    __copyFile(server, plugin_item_path, target_item_path)
            except Exception:
                __copyFolder(server, plugin_item_path, target_item_path)

        server.logger.debug(f"成功从插件提取文件夹 '{folder_path}' 到 '{target_folder}'")
        return True
    except Exception as e:
        server.logger.error(f"提取插件文件夹 '{folder_path}' 时出错: {e}")
        return False


def amount_static_files(server, static_path=None):
    if static_path is None:
        from guguwebui.constant import get_static_path
        static_path = get_static_path(server)
    static_path = Path(static_path)
    static_path.mkdir(parents=True, exist_ok=True)
    static_target = Path(static_path) / 'static'
    static_target.mkdir(parents=True, exist_ok=True)

    try:
        __copyFile(server, 'guguwebui/static/index.html', static_target / 'index.html')
    except Exception as e:
        server.logger.warning(f"复制 guguwebui/static/index.html 失败: {e}")

    assets_target = static_target / 'assets'
    if assets_target.exists() and assets_target.is_file():
        assets_target.unlink()
    assets_target.mkdir(parents=True, exist_ok=True)
    if not __copyFolder(server, 'guguwebui/static/assets', str(assets_target)):
        server.logger.debug("插件内无 guguwebui/static/assets 或复制未包含文件，已确保目录存在")

    custom_target = Path(static_path) / 'custom'
    custom_target.mkdir(parents=True, exist_ok=True)
    from guguwebui.constant import CUSTOM_FILE_DEFAULTS
    custom_sources = {
        "overall.css": ("css", "guguwebui/custom/overall.css"),
        "overall.js": ("js", "guguwebui/custom/overall.js"),
    }
    for filename, (file_type, bundled_path) in custom_sources.items():
        target = custom_target / filename
        try:
            __copyFile(server, bundled_path, target)
        except Exception as e:
            if target.exists():
                server.logger.warning(f"复制 {bundled_path} 失败，保留现有文件: {e}")
            else:
                try:
                    target.write_text(CUSTOM_FILE_DEFAULTS[file_type], encoding="utf-8")
                    server.logger.warning(
                        f"复制 {bundled_path} 失败，已创建默认自定义文件: {target} ({e})"
                    )
                except Exception as fallback_error:
                    server.logger.error(
                        f"复制 {bundled_path} 且创建默认文件均失败: {fallback_error}"
                    )

    server.logger.debug("成功复制 static 资源（index.html + assets + custom）")


def extract_metadata(plugin_path):
    try:
        if os.path.isdir(plugin_path):
            return extract_folder_plugin_metadata(plugin_path)
        elif zipfile.is_zipfile(plugin_path):
            return extract_zip_plugin_metadata(plugin_path)
        elif os.path.isfile(plugin_path):
            return extract_single_file_plugin_metadata(plugin_path)
    except Exception:
        # 单个插件元数据解析失败不应拖垮整个插件列表接口
        return None
    return None


def extract_single_file_plugin_metadata(plugin_file_path):
    import importlib.util
    import importlib.machinery
    if not plugin_file_path or not os.path.isfile(plugin_file_path):
        return None
    basename = os.path.basename(plugin_file_path)
    # 单文件 .py 插件及其禁用形态 xxx.py.disabled（MCDR 禁用后保留原名并追加后缀）都要解析元数据；
    # 其他无法识别的文件（README、配置文件等）不当作 Python 模块执行
    if basename.endswith('.py.disabled'):
        module_name = basename[:-len('.py.disabled')]
    elif basename.endswith('.py'):
        module_name = basename[:-len('.py')]
    else:
        return None
    # 显式指定 SourceFileLoader：.py.disabled 后缀无法被 spec_from_file_location 自动识别，
    # 不指定 loader 时 spec 会返回 None 导致后续崩溃
    loader = importlib.machinery.SourceFileLoader(module_name, plugin_file_path)
    spec = importlib.util.spec_from_file_location(module_name, plugin_file_path, loader=loader)
    if spec is None or spec.loader is None:
        return None
    try:
        plugin_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin_module)
    except Exception:
        return None
    metadata = getattr(plugin_module, 'PLUGIN_METADATA', None)
    return metadata if metadata else None


def extract_folder_plugin_metadata(plugin_path):
    for root, dirs, files in os.walk(plugin_path):
        for file in files:
            if file == 'mcdreforged.plugin.json':
                with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                    return json.load(f)


def extract_zip_plugin_metadata(zip_path):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file in zip_ref.namelist():
            if file.endswith('mcdreforged.plugin.json'):
                with zip_ref.open(file) as f:
                    return json.load(f)
