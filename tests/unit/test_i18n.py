"""Tests for the i18n module."""

from dataclasses import dataclass
from typing import Optional

from src.services.i18n import (
    Translator,
    get_text,
    is_supported_language,
    normalize_language,
    supported_languages_with_labels,
    translate_download_error,
)


def test_supported_languages_known():
    assert is_supported_language("ru")
    assert is_supported_language("en")


def test_supported_languages_unknown():
    assert not is_supported_language("fr")
    assert not is_supported_language("")


def test_normalize_language_strips_region():
    assert normalize_language("en-US") == "en"
    assert normalize_language("ru_RU") == "ru"


def test_normalize_language_unknown_falls_back_to_default():
    # DEFAULT_LANGUAGE depends on env at import time; just check it's supported.
    assert normalize_language("xx") in {"ru", "en"}


def test_normalize_language_none_falls_back_to_default():
    assert normalize_language(None) in {"ru", "en"}


def test_get_text_returns_translation():
    assert get_text("common.cancel_button", "ru") == "❌ Отменить"
    assert get_text("common.cancel_button", "en") == "❌ Cancel"


def test_get_text_with_format_args():
    text = get_text("admin.adduser.exists", "ru", user_id=123)
    assert "123" in text


def test_get_text_missing_key_returns_key():
    """Missing keys should surface as the key itself, not a silent empty string."""
    assert get_text("nonexistent.key", "ru") == "nonexistent.key"


def test_get_text_unknown_language_falls_back_to_default():
    """Unknown languages should fall back to DEFAULT_LANGUAGE, not crash."""
    text = get_text("common.cancel_button", "fr")
    assert text  # not empty
    assert text != "common.cancel_button"  # actually translated


def test_translator_callable():
    t = Translator("ru")
    assert t("common.cancel_button") == "❌ Отменить"


def test_translator_normalizes_lang():
    t = Translator("en-GB")
    assert t.lang == "en"


def test_supported_languages_with_labels_has_all():
    labels = supported_languages_with_labels()
    assert set(labels.keys()) == {"ru", "en"}
    assert all(labels.values())


@dataclass
class FakeResult:
    error: Optional[str] = None
    error_code: Optional[str] = None
    error_args: Optional[dict] = None


def test_translate_download_error_uses_code():
    t = Translator("en")
    result = FakeResult(error="плохо", error_code="downloader.error.private_video")
    assert translate_download_error(t, result) == "This video is private"


def test_translate_download_error_format_args():
    t = Translator("en")
    result = FakeResult(
        error_code="downloader.error.file_too_large",
        error_args={"size_mb": 60, "max_mb": 50},
    )
    msg = translate_download_error(t, result)
    assert "60" in msg
    assert "50" in msg


def test_translate_download_error_falls_back_to_raw():
    t = Translator("en")
    result = FakeResult(error="raw error message")
    assert translate_download_error(t, result) == "raw error message"


def test_translate_download_error_unknown_falls_back():
    t = Translator("en")
    result = FakeResult()
    # Should return the localized "Unknown error", not crash.
    msg = translate_download_error(t, result)
    assert msg
