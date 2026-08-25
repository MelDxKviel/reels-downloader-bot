"""
Модуль для скачивания видео с различных платформ.
Поддерживает: YouTube, Instagram Reels, TikTok, X/Twitter
"""

import asyncio
import html as html_lib
import http.cookiejar
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

from src.config import DOWNLOAD_DIR, INSTA_COOKIES_FILE, MAX_FILE_SIZE, YT_COOKIES_FILE
from src.services.url_utils import (
    build_kkinstagram_url,
    get_platform_name,
    get_url_hash,
    is_instagram_photo_candidate_url,
    is_instagram_url,
    is_kkinstagram_url,
    is_supported_url,
    is_twitter_url,
    is_youtube_url,
    should_retry_with_kkinstagram,
)

logger = logging.getLogger(__name__)

TELEGRAM_BOT_USER_AGENT = "TelegramBot (like TwitterBot)"

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})

# Older entries can contain a wrong Instagram thumbnail or a carousel truncated
# to the legacy 10-item album limit. Media cache entries are disposable, so old
# photo/video fields are lazily discarded while unrelated MP3 file IDs survive.
_MEDIA_CACHE_VERSION = 3


def _download_retry_delay(n: int) -> float:
    """Deterministic exponential backoff used by yt-dlp HTTP/extractor retries."""

    # yt-dlp calls retry callbacks as ``sleep_func(n=retry_index)`` with a
    # zero-based index, so the parameter name is part of the callback API.
    return min(2 ** max(n, 0), 8)


# Для HTML-запросов используем десктопный браузерный UA: Instagram на UA
# вида TelegramBot/WhatsApp отдаёт упрощённый link-preview без inline JSON,
# а именно из JSON берётся полноразмерный display_url каждого слайда.
# Без этого мы видим только og:image, который на embed/share-эндпоинтах
# возвращается квадратно обрезанным (stp=dst-jpg_e35_s1080x1080).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Rich-message slideshows support more media than a legacy media group, so keep
# the complete Instagram carousel instead of truncating it to the album limit.
MAX_CAROUSEL_ITEMS = 20

# Суффиксы хостов, с которых приходят настоящие Instagram-ассеты (scontent*).
# Простая проверка "scontent" in host ловила бы произвольный домен вроде
# scontent.evil.com — тут мы явно требуем доменную зону Facebook/Instagram CDN.
_INSTAGRAM_CDN_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")


def _is_instagram_cdn_host(hostname: Optional[str]) -> bool:
    if not hostname:
        return False
    host = hostname.lower()
    return any(host.endswith(suffix) for suffix in _INSTAGRAM_CDN_HOST_SUFFIXES)


@dataclass
class CarouselSlide:
    """Ordered source media for a native Telegram rich-message slideshow.

    The URL is retained for the public-URL fallback. Bot API 10.2 can otherwise
    attach downloaded files (regular messages) or pre-uploaded file IDs (inline).
    """

    url: str
    is_video: bool = False


@dataclass
class DownloadResult:
    """Результат скачивания видео.

    ``error`` — человекочитаемое сообщение (для логов и совместимости).
    ``error_code`` — ключ перевода (например ``"downloader.error.private_video"``);
    если задан, хендлер локализует его через ``i18n``. ``error_args`` — аргументы
    форматирования для ``error_code``.
    """

    success: bool
    file_path: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    error_args: Optional[Dict[str, object]] = None
    from_cache: bool = False
    is_photo: bool = False
    photo_paths: Optional[list] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # True only when target-scoped Instagram embed metadata or yt-dlp positively
    # identified a photo. Main-page markers are not trusted because they can
    # belong to unrelated/recommended posts.
    media_type_confirmed: bool = False
    # Упорядоченные слайды карусели Instagram как публичные URL — для отправки
    # нативной rich-карусели (<tg-slideshow>). photo_paths при этом остаётся
    # локальным фолбэком (альбом), если rich-сообщение отправить не удалось.
    carousel_slides: Optional[list] = None


class VideoDownloader:
    """Класс для скачивания видео с различных платформ."""

    def __init__(self, download_dir: str = DOWNLOAD_DIR):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.download_dir / "cache.json"
        self.cache: Dict[str, dict] = self._load_cache()
        self.has_ffmpeg: bool = shutil.which("ffmpeg") is not None

    def _load_cache(self) -> Dict[str, dict]:
        """Загружает кэш из файла."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                valid_cache = {}
                for url_hash, data in cache.items():
                    if not isinstance(data, dict):
                        continue
                    file_path = data.get("file_path")
                    has_local_file = (
                        isinstance(file_path, str) and bool(file_path) and os.path.exists(file_path)
                    )
                    has_telegram_id = any(
                        isinstance(data.get(key), str) and bool(data.get(key))
                        for key in (
                            "telegram_file_id",
                            "telegram_photo_file_id",
                            "telegram_mp3_file_id",
                        )
                    )
                    if has_local_file or has_telegram_id:
                        valid_cache[url_hash] = data
                return valid_cache
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        """Сохраняет кэш в файл."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def _has_cached_media(entry: dict) -> bool:
        return any(
            entry.get(key)
            for key in (
                "file_path",
                "photo_paths",
                "telegram_file_id",
                "telegram_photo_file_id",
                "is_photo",
                "carousel_slides",
            )
        )

    def _invalidate_legacy_media(self, url_hash: str, entry: dict) -> dict:
        """Drop stale v1 photo/video data but retain an independent MP3 file_id."""

        if entry.get("media_cache_version") == _MEDIA_CACHE_VERSION:
            return entry
        if not self._has_cached_media(entry):
            return entry

        self._delete_entry_files(entry)
        migrated = {"cached_at": time.time()}
        mp3_file_id = entry.get("telegram_mp3_file_id")
        if isinstance(mp3_file_id, str) and mp3_file_id:
            migrated["telegram_mp3_file_id"] = mp3_file_id
        self.cache[url_hash] = migrated
        self._save_cache()
        return migrated

    def _current_media_entry(self, url: str) -> Optional[dict]:
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if not entry:
            return None
        entry = self._invalidate_legacy_media(url_hash, entry)
        return entry if self._has_cached_media(entry) else None

    def _clear_local_media_fields(self, entry: dict, *, delete_files: bool = False) -> None:
        if delete_files:
            self._delete_entry_files(entry)
        for key in (
            "file_path",
            "photo_paths",
            "title",
            "duration",
            "width",
            "height",
            "is_photo",
            "media_type_confirmed",
            "carousel_slides",
        ):
            entry.pop(key, None)

    def _prune_missing_local_media(self, url_hash: str, entry: dict) -> None:
        """Drop stale local fields without losing still-valid Telegram IDs."""

        was_carousel = self._deserialize_carousel_slides(entry.get("carousel_slides")) is not None
        self._clear_local_media_fields(entry, delete_files=True)
        if was_carousel:
            # A first-slide ID is not a valid substitute for the vanished full
            # carousel and would flatten the next inline result permanently.
            entry.pop("telegram_photo_file_id", None)
        has_telegram_id = any(
            isinstance(entry.get(key), str) and bool(entry.get(key))
            for key in (
                "telegram_file_id",
                "telegram_photo_file_id",
                "telegram_mp3_file_id",
            )
        )
        if has_telegram_id:
            self.cache[url_hash] = entry
        else:
            self.cache.pop(url_hash, None)
        self._save_cache()

    def _get_url_hash(self, url: str) -> str:
        return get_url_hash(url)

    def get_from_cache(self, url: str) -> Optional[DownloadResult]:
        """Проверяет наличие видео/фото в кэше."""
        url_hash = get_url_hash(url)
        if url_hash in self.cache:
            cached = self._invalidate_legacy_media(url_hash, self.cache[url_hash])
            if not self._has_cached_media(cached):
                if not cached.get("telegram_mp3_file_id"):
                    self.cache.pop(url_hash, None)
                    self._save_cache()
                return None
            file_path = cached.get("file_path")
            photo_paths_raw = cached.get("photo_paths")
            is_photo = bool(cached.get("is_photo", False))

            if is_photo and isinstance(photo_paths_raw, list) and photo_paths_raw:
                declared = [p for p in photo_paths_raw if isinstance(p, str) and p]
                existing = [p for p in declared if os.path.exists(p)]
                if declared and len(existing) == len(declared) == len(photo_paths_raw):
                    return DownloadResult(
                        success=True,
                        file_path=existing[0],
                        title=cached.get("title"),
                        duration=cached.get("duration"),
                        is_photo=True,
                        photo_paths=existing,
                        width=cached.get("width"),
                        height=cached.get("height"),
                        media_type_confirmed=bool(cached.get("media_type_confirmed", False)),
                        carousel_slides=self._deserialize_carousel_slides(
                            cached.get("carousel_slides")
                        ),
                        from_cache=True,
                    )
                self._prune_missing_local_media(url_hash, cached)
                return None

            if file_path and isinstance(file_path, str) and os.path.exists(file_path):
                return DownloadResult(
                    success=True,
                    file_path=file_path,
                    title=cached.get("title"),
                    duration=cached.get("duration"),
                    is_photo=is_photo,
                    photo_paths=[file_path] if is_photo else None,
                    width=cached.get("width"),
                    height=cached.get("height"),
                    media_type_confirmed=bool(cached.get("media_type_confirmed", False)),
                    carousel_slides=self._deserialize_carousel_slides(
                        cached.get("carousel_slides")
                    ),
                    from_cache=True,
                )
            else:
                self._prune_missing_local_media(url_hash, cached)
        return None

    @staticmethod
    def _deserialize_carousel_slides(raw: object) -> Optional[list]:
        """Восстанавливает список ``CarouselSlide`` из кэша (URL слайдов карусели).

        Возвращает ``None``, если слайдов нет или их меньше двух — нативная
        rich-карусель (<tg-slideshow>) имеет смысл только для ≥2 элементов.
        """
        if not isinstance(raw, list):
            return None
        slides: list[CarouselSlide] = []
        for item in raw:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url:
                    slides.append(CarouselSlide(url=url, is_video=bool(item.get("is_video"))))
        return slides if len(slides) >= 2 else None

    def add_to_cache(self, url: str, result: DownloadResult) -> None:
        """Добавляет результат в кэш."""
        if result.success and result.file_path:
            url_hash = get_url_hash(url)
            entry = self.cache.get(url_hash, {})
            if entry:
                entry = self._invalidate_legacy_media(url_hash, entry)
            new_entry: Dict[str, Any] = {
                "file_path": result.file_path,
                "title": result.title,
                "duration": result.duration,
                "width": result.width,
                "height": result.height,
                "media_cache_version": _MEDIA_CACHE_VERSION,
                "cached_at": time.time(),
            }
            if result.is_photo:
                new_entry["is_photo"] = True
                new_entry["media_type_confirmed"] = bool(result.media_type_confirmed)
                paths = result.photo_paths or [result.file_path]
                new_entry["photo_paths"] = list(paths)
            if result.carousel_slides:
                new_entry["carousel_slides"] = [
                    {"url": s.url, "is_video": bool(s.is_video)} for s in result.carousel_slides
                ]
            telegram_media_key = "telegram_photo_file_id" if result.is_photo else "telegram_file_id"
            telegram_media_id = entry.get(telegram_media_key)
            if telegram_media_id:
                new_entry[telegram_media_key] = telegram_media_id
            telegram_mp3_file_id = entry.get("telegram_mp3_file_id")
            if telegram_mp3_file_id:
                new_entry["telegram_mp3_file_id"] = telegram_mp3_file_id
            self.cache[url_hash] = new_entry
            self._save_cache()

    def discard_result_files(self, result: DownloadResult) -> int:
        """Delete uncached local files belonging to a completed result."""

        return self._delete_entry_files(
            {"file_path": result.file_path, "photo_paths": result.photo_paths}
        )

    def _get_or_create_entry(self, url_hash: str) -> dict:
        """Возвращает запись кэша по хэшу, создавая пустую с меткой времени.

        Метка ``cached_at`` нужна автоочистке: записи только с file_id (без
        файла на диске) иначе не имели бы определяемого возраста.
        """
        entry = self.cache.get(url_hash)
        if entry is None:
            entry = {"cached_at": time.time()}
            self.cache[url_hash] = entry
        return entry

    def get_telegram_file_id(self, url: str) -> Optional[str]:
        """Возвращает сохранённый Telegram file_id для URL, если есть."""
        entry = self._current_media_entry(url)
        if not entry:
            return None
        file_id = entry.get("telegram_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    def set_telegram_file_id(self, url: str, file_id: str) -> None:
        """Сохраняет Telegram file_id для URL (используется для inline-mode)."""
        if not file_id:
            return
        url_hash = get_url_hash(url)
        entry = self._invalidate_legacy_media(url_hash, self._get_or_create_entry(url_hash))
        if (
            entry.get("telegram_file_id") == file_id
            and not entry.get("telegram_photo_file_id")
            and not entry.get("is_photo")
        ):
            return
        if entry.get("is_photo") or entry.get("telegram_photo_file_id"):
            self._clear_local_media_fields(entry, delete_files=True)
        entry.pop("telegram_photo_file_id", None)
        entry.pop("is_photo", None)
        entry["telegram_file_id"] = file_id
        entry["media_cache_version"] = _MEDIA_CACHE_VERSION
        self._save_cache()

    def get_cached_media_type(self, url: str) -> Optional[str]:
        """
        Возвращает фактический тип медиа для URL, как он сохранён в кэше:
        "photo" или "video". None — если запись отсутствует или тип не
        определён. Используется inline-хендлером, чтобы не спутать свежие
        file_id с залежавшимися от предыдущего скачивания другого типа.
        """
        entry = self._current_media_entry(url)
        if not entry:
            return None
        if entry.get("is_photo"):
            return "photo"
        file_path = entry.get("file_path")
        if (isinstance(file_path, str) and file_path) or entry.get("telegram_file_id"):
            return "video"
        if entry.get("telegram_photo_file_id"):
            return "photo"
        return None

    def get_cached_carousel_slides(self, url: str) -> Optional[list]:
        """Return ordered cached rich-carousel slides, if the URL is a carousel."""

        entry = self._current_media_entry(url)
        if not entry:
            return None
        return self._deserialize_carousel_slides(entry.get("carousel_slides"))

    def get_telegram_photo_file_id(self, url: str) -> Optional[str]:
        """Возвращает сохранённый Telegram photo file_id для URL, если есть."""
        entry = self._current_media_entry(url)
        if not entry:
            return None
        file_id = entry.get("telegram_photo_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    def set_telegram_photo_file_id(self, url: str, file_id: str) -> None:
        """Сохраняет Telegram photo file_id для URL (используется для inline-mode)."""
        if not file_id:
            return
        url_hash = get_url_hash(url)
        entry = self._invalidate_legacy_media(url_hash, self._get_or_create_entry(url_hash))
        if (
            entry.get("telegram_photo_file_id") == file_id
            and not entry.get("telegram_file_id")
            and entry.get("is_photo")
        ):
            return
        has_video_local = bool(entry.get("file_path")) and not entry.get("is_photo")
        if has_video_local or entry.get("telegram_file_id"):
            self._clear_local_media_fields(entry, delete_files=True)
        entry.pop("telegram_file_id", None)
        entry["is_photo"] = True
        entry["telegram_photo_file_id"] = file_id
        entry["media_cache_version"] = _MEDIA_CACHE_VERSION
        self._save_cache()

    def get_telegram_mp3_file_id(self, url: str) -> Optional[str]:
        """Возвращает сохранённый Telegram file_id для MP3-аудио, если есть."""
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if not entry:
            return None
        file_id = entry.get("telegram_mp3_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    def set_telegram_mp3_file_id(self, url: str, file_id: str) -> None:
        """Сохраняет Telegram file_id для MP3-аудио."""
        if not file_id:
            return
        url_hash = get_url_hash(url)
        entry = self._get_or_create_entry(url_hash)
        if entry.get("telegram_mp3_file_id") == file_id:
            return
        entry["telegram_mp3_file_id"] = file_id
        self._save_cache()

    def is_supported_url(self, url: str) -> bool:
        return is_supported_url(url)

    def get_platform_name(self, url: str) -> str:
        return get_platform_name(url)

    def _looks_like_netscape_cookies_file(self, path: str) -> bool:
        """
        Быстрая проверка, что файл похож на cookies в Netscape формате (требуется yt-dlp).
        """
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(15):
                    line = f.readline()
                    if not line:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    lower = stripped.lower()
                    if lower.startswith("# netscape"):
                        return True
                    if stripped.startswith("#"):
                        continue
                    if "\t" in stripped:
                        parts = stripped.split("\t")
                        if len(parts) >= 7:
                            return True
                    break
        except OSError:
            return False
        return False

    def _get_youtube_cookiefile(self) -> Optional[str]:
        if not YT_COOKIES_FILE:
            return None
        if not os.path.exists(YT_COOKIES_FILE):
            return None
        if not self._looks_like_netscape_cookies_file(YT_COOKIES_FILE):
            logger.warning(
                "YT_COOKIES_FILE задан, но файл не похож на Netscape cookies формат — игнорирую: %s",
                YT_COOKIES_FILE,
            )
            return None
        return YT_COOKIES_FILE

    def _get_instagram_cookiefile(self) -> Optional[str]:
        if not INSTA_COOKIES_FILE:
            return None
        if not os.path.exists(INSTA_COOKIES_FILE):
            return None
        if not self._looks_like_netscape_cookies_file(INSTA_COOKIES_FILE):
            logger.warning(
                "INSTA_COOKIES_FILE задан, но файл не похож на Netscape cookies формат — игнорирую: %s",
                INSTA_COOKIES_FILE,
            )
            return None
        return INSTA_COOKIES_FILE

    def _load_instagram_cookie_jar(self) -> Optional[http.cookiejar.MozillaCookieJar]:
        cookiefile = self._get_instagram_cookiefile()
        if not cookiefile:
            return None
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(cookiefile, ignore_discard=True, ignore_expires=True)
            return jar
        except Exception as e:
            logger.warning("Не удалось загрузить Instagram cookies для HTML-скрапинга: %s", e)
            return None

    @staticmethod
    def _instagram_image_key(image_url: str) -> str:
        """Group signed/resized variants that point to the same Instagram asset."""

        parsed = urlparse(image_url)
        return f"{parsed.hostname or ''}{parsed.path}"

    @classmethod
    def _limit_instagram_image_variants(cls, image_urls: list[str]) -> list[str]:
        """Keep variants for the first N distinct slides without truncating slides."""

        selected_keys: set[str] = set()
        selected_urls: list[str] = []
        for image_url in image_urls:
            key = cls._instagram_image_key(image_url)
            if key not in selected_keys:
                if len(selected_keys) >= MAX_CAROUSEL_ITEMS:
                    continue
                selected_keys.add(key)
            selected_urls.append(image_url)
        return selected_urls

    @classmethod
    def _instagram_slide_count(cls, image_urls: list[str]) -> int:
        """Count distinct assets instead of signed/resized URL variants."""

        return len({cls._instagram_image_key(image_url) for image_url in image_urls})

    @classmethod
    def _extract_instagram_target_payload(
        cls, html: str, shortcode: Optional[str]
    ) -> Optional[str]:
        """Return the most complete JSON object belonging to ``shortcode``.

        Instagram's main page can contain media for recommendations as well as
        the requested post. Only dictionaries whose *own* ``shortcode``/``code``
        field matches are candidates; enclosing feed objects are never trusted.
        """

        if not shortcode:
            return None
        # Keep JSON escaping intact. In particular, blindly replacing ``\"``
        # corrupts perfectly valid captions containing quotes and can make the
        # complete target script impossible to parse.
        probe = html_lib.unescape(html)
        target_nodes: list[dict] = []

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if value.get("shortcode") == shortcode or value.get("code") == shortcode:
                    target_nodes.append(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str):
                stripped = value.strip()
                if shortcode in stripped and stripped[:1] in {"{", "["}:
                    try:
                        visit(json.loads(stripped))
                    except (ValueError, json.JSONDecodeError):
                        pass

        json_sources = re.findall(r"<script[^>]*>(.*?)</script>", probe, re.IGNORECASE | re.DOTALL)
        stripped_probe = probe.strip()
        if stripped_probe[:1] in {"{", "["}:
            json_sources.append(stripped_probe)
        for source in json_sources:
            try:
                visit(json.loads(source.strip()))
            except (ValueError, json.JSONDecodeError):
                continue

        # Some pages wrap JSON in JavaScript assignments. Fall back to balanced
        # object extraction, but still accept only an object's own exact field.
        marker = re.compile(rf'"(?:shortcode|code)"\s*:\s*"{re.escape(shortcode)}"')
        for match in marker.finditer(probe):
            stack: list[int] = []
            in_string = False
            escaped = False
            for index, char in enumerate(probe):
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    stack.append(index)
                elif char == "}" and stack:
                    start = stack.pop()
                    if start <= match.start() < index:
                        payload = probe[start : index + 1]
                        try:
                            node = json.loads(payload)
                        except (ValueError, json.JSONDecodeError):
                            continue
                        if isinstance(node, dict) and (
                            node.get("shortcode") == shortcode or node.get("code") == shortcode
                        ):
                            target_nodes.append(node)

        if not target_nodes:
            return None

        def completeness(node: dict) -> tuple[int, int]:
            carousel_media = node.get("carousel_media")
            carousel_count = len(carousel_media) if isinstance(carousel_media, list) else 0
            sidecar = node.get("edge_sidecar_to_children")
            edges = sidecar.get("edges") if isinstance(sidecar, dict) else None
            if isinstance(edges, list):
                carousel_count = max(carousel_count, len(edges))
            serialized = json.dumps(node, ensure_ascii=False)
            parsed = cls._parse_instagram_html(serialized)
            image_count = cls._instagram_slide_count(parsed["image_urls"])
            return carousel_count, image_count

        best = max(target_nodes, key=completeness)
        return json.dumps(best, ensure_ascii=False)

    def _fetch_instagram_media_info(self, url: str) -> Optional[dict]:
        """
        Запрашивает Instagram-пост и собирает список изображений (для поддержки
        каруселей), URL видео и заголовок. Пробует несколько эндпоинтов:
        embed-страницу Instagram (там карусели рендерятся с несколькими <img>),
        основную страницу и зеркало kkinstagram.
        Возвращает dict с ключами image_urls (list), video_url, has_video, title
        или None, если ничего полезного не удалось извлечь.
        """
        shortcode = self._extract_ig_shortcode(url)
        cookie_jar = self._load_instagram_cookie_jar()

        candidates: list[str] = []
        if shortcode:
            candidates.append(f"https://www.instagram.com/p/{shortcode}/embed/captioned")
            candidates.append(f"https://www.instagram.com/p/{shortcode}/embed/")
        candidates.append(url)
        kk = build_kkinstagram_url(url)
        if kk and kk not in candidates:
            candidates.append(kk)

        trusted_image_urls: list[str] = []
        target_main_image_urls: list[str] = []
        mirror_image_urls: list[str] = []
        fallback_image_urls: list[str] = []
        video_url: Optional[str] = None
        has_video_marker = False
        has_photo_marker = False
        title: Optional[str] = None

        for candidate in candidates:
            html = self._http_get_html(candidate, cookie_jar=cookie_jar)
            if not html:
                continue

            candidate_path = (urlparse(candidate).path or "").lower()
            is_target_embed = is_instagram_url(candidate) and "/embed" in candidate_path
            target_payload = (
                self._extract_instagram_target_payload(html, shortcode)
                if is_instagram_url(candidate) and not is_target_embed
                else None
            )
            is_target_main = target_payload is not None
            parsed = self._parse_instagram_html(target_payload or html)
            if is_target_embed:
                image_urls = trusted_image_urls
            elif is_target_main:
                image_urls = target_main_image_urls
            elif is_kkinstagram_url(candidate):
                image_urls = mirror_image_urls
            else:
                image_urls = fallback_image_urls
            for img in parsed["image_urls"]:
                if img not in image_urls:
                    image_urls.append(img)
            if parsed["video_url"] and not video_url:
                video_url = parsed["video_url"]
            if parsed.get("has_video_marker") and (is_target_embed or is_target_main):
                has_video_marker = True
            if parsed.get("media_kind") == "photo" and (is_target_embed or is_target_main):
                has_photo_marker = True
            if parsed["title"] and not title:
                title = parsed["title"]

            # Ранний выход: уже точно видео или уже набрали максимум слайдов —
            # дальше ходить по источникам бессмысленно. При 1–9 слайдах
            # продолжаем обходить все источники: один endpoint иногда отдаёт
            # только первый слайд, другой — полную карусель.
            if has_video_marker:
                break
            active_images = max(
                (trusted_image_urls, target_main_image_urls, mirror_image_urls),
                key=lambda urls: (self._instagram_slide_count(urls), len(urls)),
            )
            active_slide_count = self._instagram_slide_count(active_images)
            if has_photo_marker and active_slide_count >= MAX_CAROUSEL_ITEMS:
                break

        # Embed and the target-only mirror are safe carousel sources. Prefer the
        # more complete one; use the main page only if neither yielded media,
        # because its JSON can include recommendation thumbnails.
        trusted_sources = max(
            (trusted_image_urls, target_main_image_urls, mirror_image_urls),
            key=lambda urls: (self._instagram_slide_count(urls), len(urls)),
        )
        image_urls = trusted_sources or fallback_image_urls
        if not image_urls and not video_url:
            return None

        return {
            "image_urls": self._limit_instagram_image_variants(image_urls),
            "video_url": video_url,
            "has_video": has_video_marker,
            "media_kind": (
                "video" if has_video_marker else "photo" if has_photo_marker else "unknown"
            ),
            "title": title,
        }

    @staticmethod
    def _extract_ig_shortcode(url: str) -> Optional[str]:
        path = urlparse(url).path or ""
        m = re.search(r"/(?:p|reel|reels|tv)/([^/?#]+)", path, re.IGNORECASE)
        return m.group(1) if m else None

    @staticmethod
    def _http_get_html(
        candidate: str,
        cookie_jar: Optional[http.cookiejar.CookieJar] = None,
    ) -> Optional[str]:
        for retry_index in range(3):
            try:
                req = urllib.request.Request(
                    candidate,
                    headers={
                        "User-Agent": BROWSER_USER_AGENT,
                        "Accept": (
                            "text/html,application/xhtml+xml,application/xml;q=0.9,"
                            "image/webp,*/*;q=0.8"
                        ),
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                if cookie_jar is not None:
                    opener = urllib.request.build_opener(
                        urllib.request.HTTPCookieProcessor(cookie_jar)
                    )
                    ctx = opener.open(req, timeout=15)
                else:
                    ctx = urllib.request.urlopen(req, timeout=15)
                with ctx as resp:
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if "html" not in content_type:
                        return None
                    raw = resp.read(4 * 1024 * 1024)
                return raw.decode("utf-8", errors="ignore")
            except urllib.error.HTTPError as exc:
                is_transient = exc.code in {408, 425, 429} or 500 <= exc.code < 600
                if is_transient and retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная HTTP %s при HTML probe, повтор через %ss: %s",
                        exc.code,
                        delay,
                        candidate,
                    )
                    time.sleep(delay)
                    continue
                logger.debug("HTML fetch failed for %s: %s", candidate, exc)
                return None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная ошибка HTML probe, повтор через %ss: %s (%s)",
                        delay,
                        candidate,
                        exc,
                    )
                    time.sleep(delay)
                    continue
                logger.debug("HTML fetch failed for %s: %s", candidate, exc)
                return None
        return None

    @classmethod
    def _parse_instagram_html(cls, html: str) -> dict:
        """
        Возвращает dict: image_urls (list, порядок слайдов, дедуп),
        video_url, title.
        """
        image_urls: list[str] = []

        # Instagram's current product payload stores full-size covers/photos in
        # image_versions2.candidates. Keep the largest candidate first; unlike
        # og:image these retain the original portrait aspect ratio. A cover is
        # not proof that the post itself is a photo.
        json_probe = html_lib.unescape(html).replace(r"\"", '"')
        for block_match in re.finditer(
            r'"image_versions2"\s*:\s*\{.*?"candidates"\s*:\s*\[(.*?)\]',
            json_probe,
            re.DOTALL,
        ):
            block = block_match.group(1)
            candidates: list[tuple[int, str]] = []
            for object_match in re.finditer(r"\{([^{}]*)\}", block, re.DOTALL):
                item = object_match.group(1)
                url_match = re.search(r'"url"\s*:\s*"((?:[^"\\]|\\.)*)"', item)
                if not url_match:
                    continue
                image_url = cls._decode_json_str(url_match.group(1))
                if os.path.splitext(urlparse(image_url).path)[1].lower() not in _IMAGE_EXTENSIONS:
                    continue
                width_match = re.search(r'"width"\s*:\s*(\d+)', item)
                height_match = re.search(r'"height"\s*:\s*(\d+)', item)
                width = int(width_match.group(1)) if width_match else 0
                height = int(height_match.group(1)) if height_match else 0
                candidates.append((width * height, image_url))
            if candidates:
                _area, image_url = max(candidates, key=lambda item: item[0])
                cls._append_unique(image_urls, image_url)

        # Older payloads have no image_versions2. Only then fall back to the
        # legacy sources below; mixing them into a modern result can append the
        # square og:image cover as an extra carousel slide.
        if not image_urls:
            # 1) Legacy display_url из встроенного JSON. Это оригинал без кропа и
            # в правильном порядке слайдов карусели; все остальные источники ниже —
            # фолбэки, и часто отдают квадратно обрезанный превью.
            for m in re.finditer(r'"display_url"\s*:\s*"((?:[^"\\]|\\.)*)"', html):
                cls._append_unique(image_urls, cls._decode_json_str(m.group(1)))

            # 2) og:image. На основной странице поста = display_url, но на
            # /embed/ endpoint'ах Instagram подменяет на кроп 1080x1080
            # (параметр stp=dst-jpg_e35_sNxN), поэтому ставим после display_url.
            for raw in cls._find_meta_contents(html, "og:image"):
                cls._append_unique(image_urls, raw)
            for raw in cls._find_meta_contents(html, "og:image:url"):
                cls._append_unique(image_urls, raw)
            for raw in cls._find_meta_contents(html, "og:image:secure_url"):
                cls._append_unique(image_urls, raw)

            # 3) <img class="EmbeddedMediaImage" src="..."> на embed-странице.
            for m in re.finditer(
                r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            ):
                cls._append_unique(image_urls, html_lib.unescape(m.group(1)))
            for m in re.finditer(
                r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\']',
                html,
                re.IGNORECASE,
            ):
                cls._append_unique(image_urls, html_lib.unescape(m.group(1)))

        video_contents = (
            list(cls._find_meta_contents(html, "og:video"))
            or list(cls._find_meta_contents(html, "og:video:url"))
            or list(cls._find_meta_contents(html, "og:video:secure_url"))
        )
        video_url = video_contents[0] if video_contents else None

        # Reels/video posts often have no legacy og:video. Current Instagram
        # product JSON uses media_type=2, video_versions/video_dash_manifest;
        # older payloads use GraphVideo/video_url/is_video=true.
        if not video_url:
            video_match = re.search(r'"video_url"\s*:\s*"((?:[^"\\]|\\.)*)"', json_probe)
            if video_match:
                video_url = cls._decode_json_str(video_match.group(1))
        video_patterns = (
            r'"is_video"\s*:\s*true\b',
            r'"__typename"\s*:\s*"GraphVideo"',
            r'"media_type"\s*:\s*2\b',
            r'"video_versions"\s*:\s*\[\s*\{',
            r'"video_dash_manifest"\s*:\s*"(?!")',
        )
        og_types = list(cls._find_meta_contents(html, "og:type"))
        has_video_marker = bool(video_url) or any(
            re.search(pattern, json_probe, re.IGNORECASE) for pattern in video_patterns
        )
        has_video_marker = has_video_marker or any(
            value.lower().startswith("video") for value in og_types
        )

        photo_patterns = (
            r'"is_video"\s*:\s*false\b',
            r'"__typename"\s*:\s*"GraphImage"',
            r'"media_type"\s*:\s*1\b',
        )
        has_photo_marker = any(
            re.search(pattern, json_probe, re.IGNORECASE) for pattern in photo_patterns
        )
        media_kind = "video" if has_video_marker else "photo" if has_photo_marker else "unknown"

        title_contents = list(cls._find_meta_contents(html, "og:title"))
        title = title_contents[0] if title_contents else None

        return {
            "image_urls": image_urls,
            "video_url": video_url,
            "has_video_marker": has_video_marker,
            "media_kind": media_kind,
            "title": title,
        }

    @staticmethod
    def _find_meta_contents(html: str, property_name: str):
        pattern1 = (
            rf'<meta[^>]*?property=["\']{re.escape(property_name)}["\']'
            r'[^>]*?content=["\']([^"\']*)["\']'
        )
        pattern2 = (
            r'<meta[^>]*?content=["\']([^"\']*)["\']'
            rf'[^>]*?property=["\']{re.escape(property_name)}["\']'
        )
        for m in re.finditer(pattern1, html, re.IGNORECASE):
            yield html_lib.unescape(m.group(1))
        for m in re.finditer(pattern2, html, re.IGNORECASE):
            yield html_lib.unescape(m.group(1))

    @staticmethod
    def _decode_json_str(raw: str) -> str:
        """Декодирует строковое значение из JSON (экранированное \\/ и \\uXXXX)."""
        try:
            return json.loads(f'"{raw}"')
        except (ValueError, json.JSONDecodeError):
            return raw.replace("\\/", "/")

    @staticmethod
    def _is_resized_variant(url: str) -> bool:
        """
        Instagram кодирует обрезанные/уменьшенные варианты изображения в
        параметре stp=...sNxN (либо cNxN) query-string. Оригинальный
        display_url такой разметки не содержит. Используется для сортировки
        вариантов одного и того же ассета: оригиналы вперёд, ресайзы сзади.
        """
        return bool(re.search(r"[?&]stp=[^&]*\d+x\d+", url))

    @staticmethod
    def _append_unique(target: list, value: Optional[str]) -> None:
        if not value:
            return
        value = value.strip()
        if value and value not in target:
            target.append(value)

    @staticmethod
    def _supports_carousel(url: str) -> bool:
        """Платформы, чей пост может быть каруселью из нескольких медиа.

        Instagram (включая зеркало kkinstagram) и X/Twitter: у обоих один пост
        может содержать несколько фото/видео, которые мы отдаём нативной
        каруселью (<tg-slideshow>). У X/Twitter медиа публичны (pbs.twimg.com /
        video.twimg.com) и не требуют cookies — Telegram скачивает их по URL.
        """
        return is_instagram_url(url) or is_kkinstagram_url(url) or is_twitter_url(url)

    @staticmethod
    def _best_entry_media_url(entry: dict, want_video: bool) -> Optional[str]:
        """Выбирает прямой http(s) URL медиа из yt-dlp entry.

        Для видео предпочитает прогрессивный (audio+video) формат наибольшего
        качества, для фото — самый крупный графический формат. Это нужно потому,
        что rich-сообщение (<tg-slideshow>) скачивает медиа ТОЛЬКО по URL —
        отдаём ссылку, а не локальный файл.
        """

        def is_http(value: object) -> bool:
            return isinstance(value, str) and value.startswith("http")

        best_url: Optional[str] = None
        best_score = -1.0
        formats = entry.get("formats")
        if isinstance(formats, list):
            for fmt in formats:
                if not isinstance(fmt, dict) or not is_http(fmt.get("url")):
                    continue
                vcodec = fmt.get("vcodec")
                acodec = fmt.get("acodec")
                has_video = vcodec is not None and vcodec != "none"
                has_audio = acodec is not None and acodec != "none"
                if want_video:
                    if not has_video:
                        continue
                    # Прогрессивные (со звуком) форматы — вперёд, дальше по высоте.
                    score = (1_000_000.0 if has_audio else 0.0) + float(fmt.get("height") or 0)
                else:
                    if has_video:
                        continue
                    score = float(fmt.get("width") or fmt.get("height") or 0)
                if score > best_score:
                    best_score = score
                    best_url = fmt["url"]
        if best_url:
            return best_url

        downloads = entry.get("requested_downloads")
        if isinstance(downloads, list):
            for item in downloads:
                if isinstance(item, dict) and is_http(item.get("url")):
                    return item["url"]
        for key in ("url", "display_url"):
            if is_http(entry.get(key)):
                return entry[key]
        thumbnails = entry.get("thumbnails")
        if isinstance(thumbnails, list):
            for thumb in reversed(thumbnails):
                if isinstance(thumb, dict) and is_http(thumb.get("url")):
                    return thumb["url"]
        return None

    @classmethod
    def _entry_to_slide(cls, entry: dict) -> Optional[CarouselSlide]:
        """Превращает один yt-dlp entry карусели в ``CarouselSlide`` (URL + тип).

        Тип слайда определяется по кодекам / длительности / расширению; URL —
        через :meth:`_best_entry_media_url`. Возвращает ``None``, если ссылку
        извлечь не удалось — такой слайд просто пропускается.
        """
        if not isinstance(entry, dict):
            return None
        ext = str(entry.get("ext") or "").lower()
        vcodec = entry.get("vcodec")
        is_video = (
            (vcodec is not None and vcodec != "none")
            or bool(entry.get("duration"))
            or (f".{ext}" in _VIDEO_EXTENSIONS)
        )
        media_url = cls._best_entry_media_url(entry, want_video=is_video)
        if not media_url:
            return None
        return CarouselSlide(url=media_url, is_video=is_video)

    def _download_image_sync(self, image_url: str, output_base: str) -> Optional[str]:
        """
        Скачивает картинку по прямой ссылке и сохраняет её рядом с output_base,
        выбирая расширение по Content-Type или URL. Возвращает путь к файлу или None.
        """
        for retry_index in range(3):
            output_path: Optional[str] = None
            try:
                req = urllib.request.Request(
                    image_url,
                    headers={
                        "User-Agent": TELEGRAM_BOT_USER_AGENT,
                        "Referer": "https://www.instagram.com/",
                    },
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if not content_type.startswith("image/"):
                        logger.warning(
                            "Отбрасываю фото %s: Content-Type %r не является image/*",
                            image_url,
                            content_type,
                        )
                        return None
                    if "jpeg" in content_type or "jpg" in content_type:
                        ext = "jpg"
                    elif "png" in content_type:
                        ext = "png"
                    elif "webp" in content_type:
                        ext = "webp"
                    elif "heic" in content_type:
                        ext = "heic"
                    else:
                        path = urlparse(image_url).path
                        ext = os.path.splitext(path)[1].lstrip(".").lower() or "jpg"
                        if len(ext) > 5 or not ext.isalnum():
                            ext = "jpg"

                    output_path = f"{output_base}.{ext}"
                    total = 0
                    with open(output_path, "wb") as file:
                        while True:
                            chunk = resp.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > MAX_FILE_SIZE:
                                file.close()
                                try:
                                    os.remove(output_path)
                                except OSError:
                                    pass
                                logger.warning(
                                    "Image exceeds MAX_FILE_SIZE (%s bytes), aborted: %s",
                                    total,
                                    image_url,
                                )
                                return None
                            file.write(chunk)
                    if total < 1024:
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                        logger.warning(
                            "Отбрасываю фото %s: слишком маленький файл (%s байт)",
                            image_url,
                            total,
                        )
                        return None
                    return output_path
            except urllib.error.HTTPError as exc:
                if output_path:
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                is_transient = exc.code in {408, 425, 429} or 500 <= exc.code < 600
                if is_transient and retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная HTTP %s при загрузке фото, повтор через %ss: %s",
                        exc.code,
                        delay,
                        image_url,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("Не удалось скачать фото %s: %s", image_url, exc)
                return None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if output_path:
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                if retry_index < 2:
                    delay = _download_retry_delay(retry_index)
                    logger.warning(
                        "Временная ошибка загрузки фото, повтор через %ss (%s): %s",
                        delay,
                        exc,
                        image_url,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("Не удалось скачать фото %s: %s", image_url, exc)
                return None
        return None

    @staticmethod
    def _video_metadata(
        info: object, file_path: str
    ) -> tuple[Optional[int], Optional[int], Optional[float]]:
        """Return display width, height and duration for Telegram's video fields.

        yt-dlp normally copies the selected video format's dimensions to the
        top-level info dict. ffprobe is a fallback for extractors that omit them;
        rotation metadata is applied so portrait videos stay portrait in the
        Telegram preview.
        """

        def positive_int(value: object) -> Optional[int]:
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return number if number > 0 else None

        def optional_float(value: object) -> Optional[float]:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return number if number >= 0 else None

        def signed_float(value: object) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError, OverflowError):
                return None

        info_dict = info if isinstance(info, dict) else {}
        width = positive_int(info_dict.get("width"))
        height = positive_int(info_dict.get("height"))
        duration = optional_float(info_dict.get("duration"))
        rotation = signed_float(info_dict.get("rotation")) or 0.0

        # Probe every video when possible. yt-dlp may provide the encoded
        # landscape dimensions while rotation exists only in container side
        # data; relying on the top-level width/height would then give Telegram
        # the wrong portrait geometry.
        if shutil.which("ffprobe"):
            try:
                proc = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        (
                            "stream=width,height:stream_tags=rotate:"
                            "stream_side_data=rotation:format=duration"
                        ),
                        "-of",
                        "json",
                        file_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if proc.returncode == 0:
                    probe = json.loads(proc.stdout or "{}")
                    streams = probe.get("streams") if isinstance(probe, dict) else None
                    stream = streams[0] if isinstance(streams, list) and streams else {}
                    if isinstance(stream, dict):
                        width = positive_int(stream.get("width")) or width
                        height = positive_int(stream.get("height")) or height
                        tags = stream.get("tags")
                        if isinstance(tags, dict):
                            parsed_rotation = signed_float(tags.get("rotate"))
                            if parsed_rotation is not None:
                                rotation = parsed_rotation
                        side_data = stream.get("side_data_list")
                        if isinstance(side_data, list):
                            for item in side_data:
                                if isinstance(item, dict) and item.get("rotation") is not None:
                                    parsed_rotation = signed_float(item.get("rotation"))
                                    if parsed_rotation is not None:
                                        rotation = parsed_rotation
                                    break
                    format_info = probe.get("format") if isinstance(probe, dict) else None
                    if duration is None and isinstance(format_info, dict):
                        duration = optional_float(format_info.get("duration"))
            except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as e:
                logger.debug("ffprobe metadata failed for %s: %s", file_path, e)

        if width is not None and height is not None and round(abs(rotation)) % 180 == 90:
            width, height = height, width
        return width, height, duration

    def _get_ydl_opts(self, output_path: str, url: str) -> dict:
        """Возвращает опции для yt-dlp."""
        if self.has_ffmpeg:
            fmt = (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
            )
            merge_format = "mp4"
        else:
            fmt = "best[ext=mp4]/best"
            merge_format = None

        opts = {
            "format": fmt,
            **({"merge_output_format": merge_format} if merge_format else {}),
            "outtmpl": output_path,
            "max_filesize": MAX_FILE_SIZE,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],
                },
            },
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 3,
            "retry_sleep_functions": {
                "http": _download_retry_delay,
                "fragment": _download_retry_delay,
                "extractor": _download_retry_delay,
            },
        }

        if is_youtube_url(url):
            cookiefile = self._get_youtube_cookiefile()
            if cookiefile:
                opts["cookiefile"] = cookiefile
        elif is_instagram_url(url):
            cookiefile = self._get_instagram_cookiefile()
            if cookiefile:
                opts["cookiefile"] = cookiefile

        return opts

    def _extract_photo_frame(self, result: DownloadResult) -> Optional[DownloadResult]:
        """
        Extracts the first frame from a video using FFmpeg and returns it as a photo result.
        Used for Instagram photo posts which yt-dlp downloads as 0-second videos.
        Returns None if extraction fails (caller should fall back to sending as video).
        """
        if not self.has_ffmpeg or not result.file_path:
            return None
        video_path = result.file_path
        output_path = os.path.splitext(video_path)[0] + "_photo.jpg"
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-q:v", "2", output_path],
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return None
            if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
                return None
            try:
                os.remove(video_path)
            except OSError:
                pass
            return DownloadResult(
                success=True,
                file_path=output_path,
                title=result.title,
                duration=result.duration,
                is_photo=True,
                photo_paths=[output_path],
                media_type_confirmed=True,
                # Сохраняем слайды карусели: rich-карусель ещё будет отправлена, а
                # локальный фолбэк теперь валидное фото, а не 0-секундное видео.
                carousel_slides=result.carousel_slides,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("Frame extraction failed: %s", e)
            return None

    async def download(self, url: str, allow_carousel: bool = True) -> DownloadResult:
        """Скачивает видео по URL.

        ``allow_carousel`` — собирать ли rich-карусели и использовать общий
        media-кэш. Хендлеры конвертации и inline отключают это, чтобы не
        перетирать полный результат поста вариантом только с первым слайдом.
        Тип Instagram-медиа определяется независимо: подтверждённый фото-пост
        всё равно возвращается как фото, а неоднозначная обложка проверяется
        через yt-dlp прежде, чем может стать фото-фолбэком.
        """
        if not is_supported_url(url):
            return DownloadResult(
                success=False,
                error="URL не поддерживается. Поддерживаемые платформы: YouTube, Instagram, TikTok, X/Twitter",
                error_code="downloader.error.unsupported_url",
            )

        if allow_carousel:
            cached = self.get_from_cache(url)
            if cached:
                return cached

        loop = asyncio.get_event_loop()

        photo_fallback: Optional[DownloadResult] = None
        photo_fallback_confirmed = False
        if is_instagram_photo_candidate_url(url):
            photo_result = await loop.run_in_executor(None, lambda: self._try_instagram_photo(url))
            if photo_result is not None:
                # image_versions2/display_url/og:image are also present on video
                # posts as covers, and surrounding HTML can contain photo markers
                # from recommended posts. Keep every HTML image as an untrusted
                # fallback until yt-dlp explicitly reports that there is no video.
                # A marker from a target-specific embed is safe to return now;
                # this preserves photo carousels that yt-dlp intentionally omits.
                if photo_result.media_type_confirmed:
                    # A yt-dlp probe can reveal a carousel when the public embed
                    # exposed only its first slide. Keep the scoped photo as a
                    # safe fallback if that probe fails (cookies help but aren't
                    # required for the attempt).
                    should_probe_playlist = not photo_result.carousel_slides
                    if not should_probe_playlist:
                        if allow_carousel:
                            self.add_to_cache(url, photo_result)
                        return photo_result
                    photo_fallback_confirmed = True
                photo_fallback = photo_result

        # X/Twitter: если в твите есть фото, собираем полную карусель через
        # fxtwitter (yt-dlp теряет фото из Twitter-плейлиста). Видео-онли и
        # одиночные твиты вернут None и пойдут обычным yt-dlp-путём ниже.
        if allow_carousel and is_twitter_url(url):
            twitter_result = await loop.run_in_executor(
                None, lambda: self._try_twitter_carousel(url)
            )
            if twitter_result is not None:
                self.add_to_cache(url, twitter_result)
                return twitter_result

        file_id = str(uuid.uuid4())[:8]
        # ``%(id)s`` prevents playlist/carousel entries with the same extension
        # from overwriting one another (e.g. every Instagram slide as uuid.jpg).
        output_path = str(self.download_dir / f"{file_id}_%(id)s.%(ext)s")
        ydl_opts = self._get_ydl_opts(output_path, url)

        try:
            result = await loop.run_in_executor(None, lambda: self._download_sync(url, ydl_opts))
            # A successful local video always wins over an unverified HTML image.
            # Instagram currently omits duration for many real videos, so duration
            # must never be used as a media-type detector.
            if result.success:
                if photo_fallback is not None and result.is_photo:
                    fallback_count = len(
                        photo_fallback.carousel_slides
                        or photo_fallback.photo_paths
                        or [photo_fallback.file_path]
                    )
                    result_count = len(
                        result.carousel_slides or result.photo_paths or [result.file_path]
                    )
                    if fallback_count > result_count:
                        # yt-dlp confirmed that the post is photographic but
                        # exposed fewer slides than the scoped HTML scrape.
                        self.discard_result_files(result)
                        richer_photo = photo_fallback
                        photo_fallback = None
                        richer_photo.media_type_confirmed = True
                        if allow_carousel:
                            self.add_to_cache(url, richer_photo)
                        return richer_photo
                if allow_carousel:
                    self.add_to_cache(url, result)
                return result

            if photo_fallback is not None and photo_fallback_confirmed:
                confirmed_photo = photo_fallback
                photo_fallback = None
                if allow_carousel:
                    self.add_to_cache(url, confirmed_photo)
                return confirmed_photo

            # Only yt-dlp's explicit "there is no video in this post" result
            # confirms that an otherwise ambiguous HTML image is the real media.
            # A 403/timeout/504 remains a failure instead of leaking a square
            # video thumbnail into the chat and cache.
            if (
                photo_fallback is not None
                and result.error_code == "downloader.error.instagram_photo_no_media"
            ):
                confirmed_photo = photo_fallback
                photo_fallback = None
                confirmed_photo.media_type_confirmed = True
                if allow_carousel:
                    self.add_to_cache(url, confirmed_photo)
                return confirmed_photo
            return result
        except Exception as e:
            msg = str(e)
            return DownloadResult(
                success=False,
                error=f"Ошибка при скачивании: {msg}",
                error_code="downloader.error.download_exception",
                error_args={"message": msg},
            )
        finally:
            # An HTML image is downloaded before yt-dlp so it can serve as a
            # confirmed photo fallback. If yt-dlp returns a video or any other
            # error, discard that temporary cover instead of leaking an orphan.
            if photo_fallback is not None:
                self._delete_entry_files(
                    {
                        "file_path": photo_fallback.file_path,
                        "photo_paths": photo_fallback.photo_paths,
                    }
                )

    def _try_instagram_photo(self, url: str) -> Optional[DownloadResult]:
        """
        Если Instagram-пост без видео (фото или карусель фото), скачивает до
        20 фото и возвращает DownloadResult(is_photo=True, photo_paths=[...]).
        Возвращает None, если фото не обнаружено — тогда вызывающий код падает в
        обычный video-flow через yt-dlp.
        """
        meta = self._fetch_instagram_media_info(url)
        if not meta or meta.get("has_video") or not meta.get("image_urls"):
            return None

        # Reject login/consent/error pages: branding assets come from
        # static.cdninstagram.com; actual post media comes from scontent* subdomains.
        # We require the scontent* prefix AND a trusted CDN suffix — the bare
        # "scontent" substring check would accept arbitrary hosts like
        # scontent.evil.com as long as they appeared in the parsed HTML.
        cdn_images = []
        for u in meta["image_urls"]:
            host = (urlparse(u).hostname or "").lower()
            if host.startswith("scontent") and _is_instagram_cdn_host(host):
                cdn_images.append(u)
        if not cdn_images:
            return None

        # Для одной и той же фотографии Instagram может отдать несколько
        # URL — полноразмерный display_url и cropped превью из og:image с
        # одинаковым filename, но разными query-параметрами (stp=...&oh=...).
        # Группируем по hostname+path, затем внутри группы поднимаем наверх
        # варианты без маркера ресайза: display_url обычно приходит раньше
        # og:image, но при смешанных ответах endpoint'ов cropped вариант
        # может оказаться первым — сортировка гарантирует full-size приоритет.
        # Остальные варианты держим как fallback, если подпись oh/oe протухла.
        groups: list[list[str]] = []
        groups_by_key: dict[str, list[str]] = {}
        for u in cdn_images:
            key = self._instagram_image_key(u)
            variants = groups_by_key.get(key)
            if variants is None:
                variants = [u]
                groups_by_key[key] = variants
                groups.append(variants)
            else:
                variants.append(u)

        batch_id = str(uuid.uuid4())[:8]
        downloaded: list[str] = []
        slide_urls: list[str] = []
        for idx, variants in enumerate(groups):
            variants.sort(key=self._is_resized_variant)
            output_base = str(self.download_dir / f"{batch_id}_{idx}")
            downloaded_path: Optional[str] = None
            for variant_url in variants:
                path = self._download_image_sync(variant_url, output_base)
                if path:
                    downloaded_path = path
                    downloaded.append(downloaded_path)
                    # Запоминаем URL варианта, который реально скачался: его же
                    # отдадим Telegram в rich-карусели — раз скачали мы, скорее
                    # всего скачает и он (тот же подписанный CDN-URL).
                    slide_urls.append(variant_url)
                    break

            if downloaded_path is None:
                # Returning a silently truncated carousel is worse than a clean
                # failure: let yt-dlp/other fallbacks try to recover all slides.
                self._delete_entry_files({"photo_paths": downloaded})
                return None

        if not downloaded:
            return None

        title = meta.get("title") or "Photo"
        # Карусель (≥2 слайдов) можно отправить нативным <tg-slideshow>;
        # одиночное фото отправляется обычным sendPhoto, slideshow не нужен.
        carousel_slides = (
            [CarouselSlide(url=u, is_video=False) for u in slide_urls]
            if len(slide_urls) >= 2
            else None
        )
        return DownloadResult(
            success=True,
            file_path=downloaded[0],
            title=title,
            is_photo=True,
            photo_paths=downloaded,
            media_type_confirmed=meta.get("media_kind") == "photo",
            carousel_slides=carousel_slides,
        )

    def _fetch_twitter_media(self, url: str):
        """Запрашивает ВСЕ медиа твита (фото + видео) через публичный fxtwitter API.

        yt-dlp для X/Twitter строит плейлист только из видео (фото отбрасываются
        в ``TwitterIE``), поэтому для каруселей с фото берём полный упорядоченный
        список из ``api.fxtwitter.com`` — публично, без cookies. Возвращает
        кортеж ``(slides, caption)`` или ``None``.
        """
        path = urlparse(url).path or ""
        if "/status/" not in path:
            return None
        api_url = f"https://api.fxtwitter.com{path}"
        raw: Optional[bytes] = None
        for retry_index in range(3):
            try:
                req = urllib.request.Request(
                    api_url,
                    headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read(4 * 1024 * 1024)
                break
            except urllib.error.HTTPError as exc:
                is_transient = exc.code in {408, 425, 429} or 500 <= exc.code < 600
                if is_transient and retry_index < 2:
                    time.sleep(_download_retry_delay(retry_index))
                    continue
                logger.debug("fxtwitter fetch failed for %s: %s", api_url, exc)
                return None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if retry_index < 2:
                    time.sleep(_download_retry_delay(retry_index))
                    continue
                logger.debug("fxtwitter fetch failed for %s: %s", api_url, exc)
                return None
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
        except (ValueError, json.JSONDecodeError):
            return None
        tweet = data.get("tweet") if isinstance(data, dict) else None
        if not isinstance(tweet, dict):
            return None
        media = tweet.get("media")
        all_media = media.get("all") if isinstance(media, dict) else None
        if not isinstance(all_media, list):
            return None
        slides: list[CarouselSlide] = []
        for item in all_media:
            if not isinstance(item, dict):
                continue
            media_url = item.get("url")
            if not isinstance(media_url, str) or not media_url.startswith("http"):
                continue
            is_video = item.get("type") in ("video", "gif")
            slides.append(CarouselSlide(url=media_url, is_video=is_video))
            if len(slides) >= MAX_CAROUSEL_ITEMS:
                break
        if not slides:
            return None
        caption = tweet.get("text")
        caption = caption if isinstance(caption, str) and caption.strip() else None
        return slides, caption

    def _try_twitter_carousel(self, url: str) -> Optional[DownloadResult]:
        """Карусель X/Twitter с фото: собирает ПОЛНЫЙ упорядоченный список слайдов
        через fxtwitter и скачивает фото-слайды для локального фолбэка.

        Видео-онли твиты отдаём обычному yt-dlp-пути: его playlist-entries
        покрывают все видео и дают полноценный локальный фолбэк. Здесь же
        вмешиваемся только когда в твите есть фото (их yt-dlp как раз теряет).
        """
        fetched = self._fetch_twitter_media(url)
        if fetched is None:
            return None
        slides, caption = fetched
        if len(slides) < 2 or all(slide.is_video for slide in slides):
            return None
        # Скачиваем фото-слайды для альбома-фолбэка (видео остаются URL-only:
        # их проигрывает rich-карусель, а фолбэк-альбом покажет хотя бы фото).
        batch_id = str(uuid.uuid4())[:8]
        photo_paths: list[str] = []
        for idx, slide in enumerate(slides):
            if slide.is_video:
                continue
            path = self._download_image_sync(
                slide.url, str(self.download_dir / f"{batch_id}_{idx}")
            )
            if path:
                photo_paths.append(path)
        if not photo_paths:
            return None
        return DownloadResult(
            success=True,
            file_path=photo_paths[0],
            title=(caption or "Tweet"),
            is_photo=True,
            photo_paths=photo_paths,
            carousel_slides=slides,
        )

    def _download_sync(self, url: str, ydl_opts: dict) -> DownloadResult:
        """Синхронная функция скачивания для запуска в executor."""

        attempt_downloads: list[list[str]] = []

        def cleanup_failed_attempts() -> None:
            for paths in attempt_downloads:
                self._delete_entry_files({"photo_paths": paths})

        def attempt_download(download_url: str, opts: dict) -> DownloadResult:
            all_downloaded: list[str] = []
            attempt_downloads.append(all_downloaded)

            def progress_hook(d):
                if d.get("status") == "finished":
                    filename = d.get("filename")
                    if isinstance(filename, str) and filename not in all_downloaded:
                        all_downloaded.append(filename)

            opts = opts.copy()
            opts["progress_hooks"] = [progress_hook]

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(download_url, download=True)

                if info is None:
                    return DownloadResult(
                        success=False,
                        error="Не удалось получить информацию о видео",
                        error_code="downloader.error.no_info",
                    )

                logger.debug(
                    "yt-dlp info type: %s, keys: %s",
                    type(info).__name__,
                    list(info.keys()) if isinstance(info, dict) else "N/A",
                )

                carousel_slides: Optional[list] = None
                # Заголовок поста-плейлиста (подпись карусели) надо взять ДО того,
                # как info схлопнется в первый элемент ниже.
                playlist_title: Optional[str] = (
                    info.get("title") if isinstance(info, dict) else None
                )
                if "entries" in info and info["entries"]:
                    entries = info["entries"]
                    logger.debug(
                        "entries type: %s, len: %s",
                        type(entries).__name__,
                        len(entries) if hasattr(entries, "__len__") else "N/A",
                    )
                    # Карусель Instagram (пост /p/ с несколькими медиа): собираем
                    # упорядоченные слайды (фото И видео) как публичные URL — для
                    # нативной rich-карусели (<tg-slideshow>). Первый слайд всё
                    # равно скачивается ниже как локальный фолбэк на случай, если
                    # rich-сообщение отправить не удастся.
                    if self._supports_carousel(download_url):
                        slides: list[CarouselSlide] = []
                        for entry in entries:
                            if isinstance(entry, dict):
                                slide = self._entry_to_slide(entry)
                                if slide is not None:
                                    slides.append(slide)
                            if len(slides) >= MAX_CAROUSEL_ITEMS:
                                break
                        if len(slides) >= 2:
                            carousel_slides = slides
                    for entry in entries:
                        if entry is not None and isinstance(entry, dict):
                            info = entry
                            break
                    else:
                        return DownloadResult(
                            success=False,
                            error="Не удалось получить видео из плейлиста",
                            error_code="downloader.error.no_playlist_video",
                        )

                title = info.get("title", "Видео") if isinstance(info, dict) else "Видео"
                # Для карусели подпись берём из заголовка поста, а не первого слайда.
                if carousel_slides and playlist_title:
                    title = playlist_title
                duration = info.get("duration") if isinstance(info, dict) else None

                # Multiple images captured via progress hook → photo carousel
                existing = [f for f in all_downloaded if os.path.exists(f)]
                image_files = [
                    f for f in existing if os.path.splitext(f)[1].lower() in _IMAGE_EXTENSIONS
                ]
                if len(image_files) > 1:
                    kept_images = image_files[:MAX_CAROUSEL_ITEMS]
                    self._delete_entry_files(
                        {"photo_paths": [path for path in existing if path not in kept_images]}
                    )
                    return DownloadResult(
                        success=True,
                        file_path=kept_images[0],
                        title=title,
                        duration=duration,
                        is_photo=True,
                        photo_paths=kept_images,
                        carousel_slides=carousel_slides,
                    )

                downloaded_file_path = existing[-1] if existing else None

                if not downloaded_file_path:
                    try:
                        prepared = ydl.prepare_filename(info)
                        if isinstance(prepared, str):
                            downloaded_file_path = prepared
                    except Exception:
                        pass

                    if (
                        downloaded_file_path
                        and isinstance(downloaded_file_path, str)
                        and not os.path.exists(downloaded_file_path)
                    ):
                        base = os.path.splitext(downloaded_file_path)[0]
                        for ext in ["mp4", "webm", "mkv", "mov", "jpg", "jpeg", "png", "webp"]:
                            test_path = f"{base}.{ext}"
                            if os.path.exists(test_path):
                                downloaded_file_path = test_path
                                break

                if (
                    downloaded_file_path
                    and isinstance(downloaded_file_path, str)
                    and os.path.exists(downloaded_file_path)
                ):
                    # A playlist may have produced several files even when the
                    # result below exposes only one.  Remove every unowned file
                    # now so mixed/video playlists cannot leak on disk.
                    self._delete_entry_files(
                        {"photo_paths": [path for path in existing if path != downloaded_file_path]}
                    )
                    actual_size = os.path.getsize(downloaded_file_path)
                    if actual_size > MAX_FILE_SIZE:
                        os.remove(downloaded_file_path)
                        size_mb = actual_size // (1024 * 1024)
                        max_mb = MAX_FILE_SIZE // (1024 * 1024)
                        return DownloadResult(
                            success=False,
                            error=(
                                f"Скачанный файл слишком большой "
                                f"({size_mb}MB). Максимум: {max_mb}MB"
                            ),
                            error_code="downloader.error.file_too_large",
                            error_args={"size_mb": size_mb, "max_mb": max_mb},
                        )
                    file_ext = os.path.splitext(downloaded_file_path)[1].lower()
                    is_photo = file_ext in _IMAGE_EXTENSIONS
                    width: Optional[int] = None
                    height: Optional[int] = None
                    if not is_photo:
                        width, height, probed_duration = self._video_metadata(
                            info, downloaded_file_path
                        )
                        if probed_duration is not None:
                            duration = probed_duration
                    return DownloadResult(
                        success=True,
                        file_path=downloaded_file_path,
                        title=title,
                        duration=duration,
                        is_photo=is_photo,
                        photo_paths=[downloaded_file_path] if is_photo else None,
                        width=width,
                        height=height,
                        carousel_slides=carousel_slides,
                    )
                else:
                    outtmpl = opts.get("outtmpl", "")
                    if isinstance(outtmpl, dict):
                        outtmpl = outtmpl.get("default", "")
                    if isinstance(outtmpl, str) and outtmpl:
                        # Keep only the stable prefix before yt-dlp template
                        # fields, so _find_downloaded_file can scan real names.
                        file_id = os.path.basename(outtmpl).split("%", 1)[0].rstrip("._-")
                    else:
                        file_id = None

                    found_file = self._find_downloaded_file(file_id) if file_id else None
                    if found_file:
                        file_ext = os.path.splitext(found_file)[1].lower()
                        is_photo = file_ext in _IMAGE_EXTENSIONS
                        width = None
                        height = None
                        if not is_photo:
                            width, height, probed_duration = self._video_metadata(info, found_file)
                            if probed_duration is not None:
                                duration = probed_duration
                        return DownloadResult(
                            success=True,
                            file_path=found_file,
                            title=title,
                            duration=duration,
                            is_photo=is_photo,
                            photo_paths=[found_file] if is_photo else None,
                            width=width,
                            height=height,
                            carousel_slides=carousel_slides,
                        )
                    return DownloadResult(
                        success=False,
                        error="Файл не был скачан",
                        error_code="downloader.error.file_not_downloaded",
                    )

        def run_attempt(download_url: str, opts: dict) -> DownloadResult:
            """Run one yt-dlp attempt and atomically own/clean its output files."""

            first_attempt_list = len(attempt_downloads)
            outtmpl = opts.get("outtmpl", "")
            if isinstance(outtmpl, dict):
                outtmpl = outtmpl.get("default", "")
            prefix = (
                os.path.basename(outtmpl).split("%", 1)[0].rstrip("._-")
                if isinstance(outtmpl, str) and outtmpl
                else None
            )

            def produced_paths() -> list[str]:
                paths: list[str] = []
                for tracked in attempt_downloads[first_attempt_list:]:
                    for path in tracked:
                        if path not in paths:
                            paths.append(path)
                if prefix:
                    for path in self.download_dir.iterdir():
                        if path.is_file() and path.name.startswith(prefix):
                            value = str(path)
                            if value not in paths:
                                paths.append(value)
                return paths

            try:
                result = attempt_download(download_url, opts)
            except Exception:
                self._delete_entry_files({"photo_paths": produced_paths()})
                raise

            keep: set[str] = set()
            if result.success:
                if isinstance(result.file_path, str):
                    keep.add(result.file_path)
                if isinstance(result.photo_paths, list):
                    keep.update(path for path in result.photo_paths if isinstance(path, str))
            self._delete_entry_files(
                {"photo_paths": [path for path in produced_paths() if path not in keep]}
            )
            return result

        try:
            return run_attempt(url, ydl_opts)

        except DownloadError as e:
            cleanup_failed_attempts()
            error_msg = str(e)
            error_msg_lower = error_msg.lower()

            if ydl_opts.get("cookiefile") and (
                "does not look like a netscape format cookies file" in error_msg_lower
                or "netscape format cookies file" in error_msg_lower
            ):
                bad_cookiefile = ydl_opts.get("cookiefile")
                logger.warning(
                    "yt-dlp отклонил cookies файл (%s). Повторяю скачивание без cookies.",
                    bad_cookiefile,
                )
                ydl_opts_no_cookies = ydl_opts.copy()
                ydl_opts_no_cookies.pop("cookiefile", None)
                try:
                    return run_attempt(url, ydl_opts_no_cookies)
                except DownloadError as e2:
                    cleanup_failed_attempts()
                    e = e2
                    error_msg = str(e2)
                    error_msg_lower = error_msg.lower()

            if should_retry_with_kkinstagram(url, error_msg_lower):
                kk_url = build_kkinstagram_url(url)
                if kk_url:
                    logger.warning(
                        "Instagram требует авторизации. Пробую fallback через kkinstagram: %s",
                        kk_url,
                    )
                    ydl_opts_kk = ydl_opts.copy()
                    ydl_opts_kk["user_agent"] = TELEGRAM_BOT_USER_AGENT
                    http_headers = ydl_opts_kk.get("http_headers")
                    if not isinstance(http_headers, dict):
                        http_headers = {}
                    ydl_opts_kk["http_headers"] = {
                        **http_headers,
                        "User-Agent": TELEGRAM_BOT_USER_AGENT,
                    }
                    try:
                        return run_attempt(kk_url, ydl_opts_kk)
                    except DownloadError as e2:
                        cleanup_failed_attempts()
                        logger.warning("Fallback через kkinstagram не сработал: %s", e2)
                        e = e2
                        error_msg = str(e2)
                        error_msg_lower = error_msg.lower()

            if "ffmpeg is not installed" in error_msg.lower():
                return DownloadResult(
                    success=False,
                    error=(
                        "Нужен FFmpeg для скачивания этого видео (требуется склейка аудио+видео).\n"
                        "Установите FFmpeg и добавьте его в PATH, затем попробуйте ещё раз."
                    ),
                    error_code="downloader.error.ffmpeg_required",
                )
            if "Video unavailable" in error_msg:
                return DownloadResult(
                    success=False,
                    error="Видео недоступно",
                    error_code="downloader.error.video_unavailable",
                )
            elif "Private video" in error_msg:
                return DownloadResult(
                    success=False,
                    error="Это приватное видео",
                    error_code="downloader.error.private_video",
                )
            elif "Sign in" in error_msg or "login" in error_msg_lower:
                return DownloadResult(
                    success=False,
                    error="Требуется авторизация для просмотра этого видео",
                    error_code="downloader.error.auth_required",
                )
            elif (
                "there is no video in this post" in error_msg_lower
                and is_instagram_photo_candidate_url(url)
            ):
                return DownloadResult(
                    success=False,
                    error="Не удалось скачать Instagram фото-пост: требуется авторизация Instagram",
                    error_code="downloader.error.instagram_photo_no_media",
                )
            else:
                truncated = error_msg[:200]
                return DownloadResult(
                    success=False,
                    error=f"Ошибка скачивания: {truncated}",
                    error_code="downloader.error.download_failed",
                    error_args={"message": truncated},
                )
        except Exception as e:
            cleanup_failed_attempts()
            truncated = str(e)[:200]
            return DownloadResult(
                success=False,
                error=f"Неожиданная ошибка: {truncated}",
                error_code="downloader.error.unexpected",
                error_args={"message": truncated},
            )

    def _find_downloaded_file(self, file_id: str) -> Optional[str]:
        """Находит скачанный файл по ID."""
        for ext in ["mp4", "webm", "mkv", "mov", "avi", "jpg", "jpeg", "png", "webp"]:
            file_path = self.download_dir / f"{file_id}.{ext}"
            if file_path.exists():
                return str(file_path)

        for file in self.download_dir.iterdir():
            if file.stem.startswith(file_id) and file.suffix.lower() in {
                ".mp4",
                ".webm",
                ".mkv",
                ".mov",
                ".avi",
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            }:
                return str(file)

        return None

    @staticmethod
    def _entry_file_paths(data: dict) -> list[str]:
        """Локальные файлы записи кэша (видео и/или фото) без дублей."""
        paths: list[str] = []
        file_path = data.get("file_path")
        if isinstance(file_path, str):
            paths.append(file_path)
        photo_paths = data.get("photo_paths")
        if isinstance(photo_paths, list):
            for p in photo_paths:
                if isinstance(p, str) and p not in paths:
                    paths.append(p)
        return paths

    def _delete_entry_files(self, data: dict) -> int:
        """Удаляет файлы записи кэша с диска. Возвращает число удалённых файлов."""
        count = 0
        for p in self._entry_file_paths(data):
            if os.path.exists(p):
                try:
                    os.remove(p)
                    count += 1
                except OSError:
                    pass
        return count

    def _entry_age_seconds(self, data: dict, now: float) -> Optional[float]:
        """Возраст записи кэша в секундах.

        Берётся из ``cached_at`` (время добавления в кэш); для старых записей
        без этого поля — из mtime файла. ``None``, если возраст определить
        нельзя (нет ни метки, ни существующего файла) — такие записи не трогаем.
        """
        cached_at = data.get("cached_at")
        if isinstance(cached_at, (int, float)):
            return max(0.0, now - float(cached_at))
        for path in self._entry_file_paths(data):
            if os.path.exists(path):
                try:
                    return max(0.0, now - os.path.getmtime(path))
                except OSError:
                    continue
        return None

    def cache_disk_usage(self) -> int:
        """Суммарный размер существующих файлов кэша на диске (байты)."""
        total = 0
        for data in self.cache.values():
            for path in self._entry_file_paths(data):
                if os.path.exists(path):
                    try:
                        total += os.path.getsize(path)
                    except OSError:
                        pass
        return total

    def cleanup_expired(self, max_age_seconds: float) -> tuple[int, int]:
        """Удаляет записи кэша старше ``max_age_seconds`` и их файлы.

        Возвращает кортеж ``(удалено_записей, удалено_файлов)``. Записи с
        неопределимым возрастом пропускаются (см. :meth:`_entry_age_seconds`).
        """
        if max_age_seconds <= 0:
            return 0, 0
        now = time.time()
        removed_entries = 0
        removed_files = 0
        for url_hash, data in list(self.cache.items()):
            age = self._entry_age_seconds(data, now)
            if age is None or age < max_age_seconds:
                continue
            removed_files += self._delete_entry_files(data)
            del self.cache[url_hash]
            removed_entries += 1
        if removed_entries:
            self._save_cache()
        return removed_entries, removed_files

    def clear_cache(self) -> int:
        """Очищает весь кэш и удаляет файлы. Возвращает количество удалённых файлов."""
        count = 0
        for data in list(self.cache.values()):
            count += self._delete_entry_files(data)
        self.cache.clear()
        self._save_cache()
        return count


# Создаём глобальный экземпляр загрузчика
downloader = VideoDownloader()
