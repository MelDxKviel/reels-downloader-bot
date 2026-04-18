import os
from unittest.mock import patch


def test_max_file_size_is_50mb():
    from src.config import MAX_FILE_SIZE

    assert MAX_FILE_SIZE == 50 * 1024 * 1024


def test_download_timeout_is_300s():
    from src.config import DOWNLOAD_TIMEOUT

    assert DOWNLOAD_TIMEOUT == 300


def test_admin_users_parses_comma_separated():
    with patch.dict(os.environ, {"ADMIN_USERS": "111,222,333"}):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.ADMIN_USERS == [111, 222, 333]


def test_admin_users_empty_when_not_set():
    with patch.dict(os.environ, {"ADMIN_USERS": ""}, clear=False):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.ADMIN_USERS == []


def test_admin_users_strips_whitespace():
    with patch.dict(os.environ, {"ADMIN_USERS": " 111 , 222 "}):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.ADMIN_USERS == [111, 222]


def test_download_dir_default():
    with patch.dict(os.environ, {}, clear=False):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.DOWNLOAD_DIR == os.getenv("DOWNLOAD_DIR", "downloads")
