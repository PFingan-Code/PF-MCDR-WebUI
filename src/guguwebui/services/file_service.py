import os
import tempfile
from pathlib import Path

from guguwebui.constant import CUSTOM_FILE_DEFAULTS, get_custom_file_path
from guguwebui.structures import BusinessException


class FileService:
    def __init__(self, server):
        self.server = server

    def _get_path(self, file_type: str) -> Path:
        try:
            return get_custom_file_path(self.server, file_type)
        except ValueError as e:
            raise BusinessException(str(e), status_code=400)

    def _ensure_default_file(self, file_type: str, path: Path) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CUSTOM_FILE_DEFAULTS[file_type], encoding="utf-8")
        self.server.logger.warning(f"自定义{file_type}文件不存在，已恢复默认文件: {path}")

    def load_custom_file(self, file_type: str):
        """加载并在缺失时恢复 overall.js 或 overall.css。"""
        path = self._get_path(file_type)
        try:
            self._ensure_default_file(file_type, path)
            return path.read_text(encoding="utf-8")
        except Exception as e:
            self.server.logger.error(f"读取文件 {file_type} 失败: {e}")
            raise BusinessException(f"读取文件失败: {str(e)}")

    def save_custom_file(self, file_type: str, content: str):
        """以原子替换方式保存 overall.js 或 overall.css。"""
        path = self._get_path(file_type)
        temporary_path = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False,
                prefix=f".{path.name}.", suffix=".tmp"
            ) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, path)
            return {"status": "success", "message": f"{file_type} 保存成功"}
        except Exception as e:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.server.logger.error(f"保存文件 {file_type} 失败: {e}")
            raise BusinessException(f"保存文件失败: {str(e)}")

    def get_icp_records(self, config_service):
        """获取 ICP 备案信息"""
        config = config_service.get_config()
        return {"status": "success", "icp_records": config.get("icp_records", [])}
