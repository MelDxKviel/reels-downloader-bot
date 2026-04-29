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


def test_video_storage_chat_id_parsed():
    with patch.dict(os.environ, {"VIDEO_STORAGE_CHAT_ID": "-1001234567890"}):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.VIDEO_STORAGE_CHAT_ID == -1001234567890


def test_video_storage_chat_id_none_when_not_set():
    env = {k: v for k, v in os.environ.items() if k != "VIDEO_STORAGE_CHAT_ID"}
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.VIDEO_STORAGE_CHAT_ID is None


def test_default_language_from_env():
    with patch.dict(os.environ, {"DEFAULT_LANGUAGE": "en"}):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.DEFAULT_LANGUAGE == "en"


def test_default_language_normalizes_case():
    with patch.dict(os.environ, {"DEFAULT_LANGUAGE": "EN"}):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.DEFAULT_LANGUAGE == "en"


def test_default_language_falls_back_when_unsupported():
    with patch.dict(os.environ, {"DEFAULT_LANGUAGE": "xx"}):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.DEFAULT_LANGUAGE == "ru"


def test_default_language_default_is_ru():
    env = {k: v for k, v in os.environ.items() if k != "DEFAULT_LANGUAGE"}
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.DEFAULT_LANGUAGE == "ru"


def test_supported_languages_includes_ru_and_en():
    import src.config as cfg

    assert "ru" in cfg.SUPPORTED_LANGUAGES
    assert "en" in cfg.SUPPORTED_LANGUAGES
