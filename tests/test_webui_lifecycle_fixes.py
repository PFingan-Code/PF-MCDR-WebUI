from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from guguwebui.constant import CUSTOM_FILE_DEFAULTS
from guguwebui.services.file_service import FileService
from guguwebui.utils.config_validator import ConfigValidator
from guguwebui.utils.file_util import amount_static_files


class _Logger:
    def __init__(self):
        self.messages = []

    def debug(self, message, *args, **kwargs):
        self.messages.append(str(message))

    info = debug
    warning = debug
    error = debug


class _Server:
    def __init__(self, data_folder: Path, bundled: dict[str, bytes] | None = None):
        self.data_folder = data_folder
        self.bundled = bundled or {}
        self.logger = _Logger()

    def get_data_folder(self):
        return str(self.data_folder)

    @contextmanager
    def open_bundled_file(self, path: str):
        if path not in self.bundled:
            raise FileNotFoundError(path)
        yield io.BytesIO(self.bundled[path])

    def list_directory(self, path: str):
        return []


def test_missing_custom_assets_are_restored_in_server_data_folder(tmp_path):
    server = _Server(tmp_path / "mcdr-data")

    amount_static_files(server)

    custom = tmp_path / "mcdr-data" / "guguwebui_static" / "custom"
    assert (custom / "overall.css").read_text(encoding="utf-8") == CUSTOM_FILE_DEFAULTS["css"]
    assert (custom / "overall.js").read_text(encoding="utf-8") == CUSTOM_FILE_DEFAULTS["js"]


def test_existing_custom_assets_are_not_overwritten(tmp_path):
    server = _Server(
        tmp_path / "mcdr-data",
        bundled={
            "guguwebui/custom/overall.css": b"bundled css",
            "guguwebui/custom/overall.js": b"bundled js",
        },
    )
    custom = tmp_path / "mcdr-data" / "guguwebui_static" / "custom"
    custom.mkdir(parents=True)
    (custom / "overall.css").write_text("user css", encoding="utf-8")

    amount_static_files(server)

    assert (custom / "overall.css").read_text(encoding="utf-8") == "user css"
    assert (custom / "overall.js").read_bytes() == b"bundled js"


def test_file_service_loads_and_saves_using_server_data_folder(tmp_path):
    server = _Server(tmp_path / "data")
    service = FileService(server)

    assert service.load_custom_file("js") == CUSTOM_FILE_DEFAULTS["js"]
    assert service.save_custom_file("js", "window.ready = true;")['status'] == "success"
    assert service.load_custom_file("js") == "window.ready = true;"
    assert (tmp_path / "data" / "guguwebui_static" / "custom" / "overall.js").exists()


def test_file_service_atomic_save_keeps_old_content_on_replace_failure(tmp_path):
    server = _Server(tmp_path / "data")
    service = FileService(server)
    service.save_custom_file("css", "old")

    with patch("guguwebui.services.file_service.os.replace", side_effect=OSError("replace failed")):
        with pytest.raises(Exception):
            service.save_custom_file("css", "new")

    assert service.load_custom_file("css") == "old"


def test_port_validation_does_not_use_toctou_bind_probe():
    validator = ConfigValidator(_Logger())
    validator._check_minecraft_port_conflict = lambda port: True
    validator._is_port_available = lambda host, port: (_ for _ in ()).throw(
        AssertionError("port availability probe must not run")
    )

    is_valid, config, critical = validator.validate_config(
        {"host": "127.0.0.1", "port": "38001"}
    )

    assert is_valid is True
    assert critical is False
    assert config["port"] == 38001
