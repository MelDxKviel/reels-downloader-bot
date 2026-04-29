"""
Lightweight i18n: maps a translation key + locale → formatted string.

Translation tables live in ``src/locales/<lang>.py`` as flat ``MESSAGES`` dicts.
Handlers receive a ready-to-use ``Translator`` callable from ``LocaleMiddleware``
and don't need to know which language is active.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping

from src.config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from src.locales import LOCALE_MESSAGES

logger = logging.getLogger(__name__)


def is_supported_language(lang: str) -> bool:
    return lang in SUPPORTED_LANGUAGES


def normalize_language(lang: str | None) -> str:
    """Pick the best-matching supported language code, falling back to default."""
    if not lang:
        return DEFAULT_LANGUAGE
    code = lang.lower().split("-", 1)[0].split("_", 1)[0]
    if code in SUPPORTED_LANGUAGES:
        return code
    return DEFAULT_LANGUAGE


def get_text(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs: Any) -> str:
    """Return a translated string for ``key`` in ``lang``.

    Falls back to ``DEFAULT_LANGUAGE`` if the key is missing in the chosen
    language, and to the key itself if it's missing everywhere — which makes
    typos visible in the UI rather than silently empty.
    """
    pack: Mapping[str, str] = LOCALE_MESSAGES.get(lang) or {}
    template = pack.get(key)
    if template is None:
        fallback = LOCALE_MESSAGES.get(DEFAULT_LANGUAGE) or {}
        template = fallback.get(key)
        if template is None:
            logger.warning("Missing translation: key=%s lang=%s", key, lang)
            return key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError) as e:
        logger.error("Translation format error: key=%s lang=%s: %s", key, lang, e)
        return template


class Translator:
    """Bound translator: ``t = Translator("ru"); t("key", **kwargs)``.

    Stored in handler ``data`` by ``LocaleMiddleware`` so handlers don't have
    to thread the language code through every call.
    """

    __slots__ = ("lang",)

    def __init__(self, lang: str) -> None:
        self.lang = normalize_language(lang)

    def __call__(self, key: str, **kwargs: Any) -> str:
        return get_text(key, self.lang, **kwargs)


def translate_download_error(t: "Translator", result: Any) -> str:
    """Return a localized error message from a ``DownloadResult``-like object.

    Prefers ``error_code`` (translation key) and ``error_args``; falls back to
    the raw ``error`` string, and finally to ``common.unknown_error``.
    """
    code = getattr(result, "error_code", None)
    if code:
        args = getattr(result, "error_args", None) or {}
        return t(code, **args)
    raw = getattr(result, "error", None)
    if raw:
        return raw
    return t("common.unknown_error")


def supported_languages_with_labels() -> Dict[str, str]:
    """Mapping of supported language code → human label for keyboards."""
    labels = {
        "ru": "🇷🇺 Русский",
        "en": "🇬🇧 English",
    }
    return {code: labels.get(code, code) for code in SUPPORTED_LANGUAGES}
