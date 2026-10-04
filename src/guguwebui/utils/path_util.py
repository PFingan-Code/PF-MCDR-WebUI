from pathlib import Path
from typing import List, Union


class SafePath:
    @staticmethod
    def is_safe(path: Union[str, Path], base_dirs: List[Union[str, Path]]) -> bool:
        """
        Check if a path is safe (within one of the base directories).
        Prevents path traversal attacks.
        """
        try:
            target_path = Path(path).resolve()
            for base_dir in base_dirs:
                resolved_base = Path(base_dir).resolve()
                if resolved_base in target_path.parents or resolved_base == target_path:
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    def get_safe_path(path: Union[str, Path], base_dirs: List[Union[str, Path]]) -> Path:
        """
        Get a resolved Path object if it's safe, otherwise raise ValueError.
        """
        if SafePath.is_safe(path, base_dirs):
            return Path(path).resolve()
        raise ValueError(f"Access to path '{path}' is denied (outside of allowed directories)")


def get_base_dirs(server) -> List[Path]:
    """
    Get allowed base directories for file operations.
    """
    base_dirs = [
        Path("config.yml").resolve(),
        Path("permission.yml").resolve(),
        Path("./config/guguwebui/guguwebui_static").resolve(),
        Path("./guguwebui_static").resolve(),  # legacy path during migration,
        Path("./config").resolve(),
    ]
    if server:
        try:
            # Add MCDR data folder
            data_folder = Path(server.get_data_folder()).resolve()
            if data_folder not in base_dirs:
                base_dirs.append(data_folder)

            # Add MC working directory and server.properties
            from .mc_util import get_minecraft_path
            mc_dir = Path(get_minecraft_path(server)).resolve()
            if mc_dir not in base_dirs:
                base_dirs.append(mc_dir)

            # 明确添加 server.properties
            properties_path = mc_dir / "server.properties"
            if properties_path not in base_dirs:
                base_dirs.append(properties_path)

            # 添加插件目录（通常是 plugins/）
            plugins_dir = Path("./plugins").resolve()
            if plugins_dir not in base_dirs:
                base_dirs.append(plugins_dir)

        except Exception:
            pass
    return base_dirs
