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

# Для HTML-запросов используем десктопный браузерный UA: Instagram на UA
# вида TelegramBot/WhatsApp отдаёт упрощённый link-preview без inline JSON,
# а именно из JSON берётся полноразмерный display_url каждого слайда.
# Без этого мы видим только og:image, который на embed/share-эндпоинтах
# возвращается квадратно обрезанным (stp=dst-jpg_e35_s1080x1080).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Telegram media groups accept от 2 до 10 элементов — карусели Instagram
# могут содержать до 20 слайдов, остальные отбрасываем.
MAX_CAROUSEL_ITEMS = 10

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
    """Слайд карусели Instagram для нативной rich-карусели (Bot API 10.1).

    Rich-сообщения (``sendRichMessage`` / ``<tg-slideshow>``) принимают медиа
    ТОЛЬКО как публичные http(s) URL — file_id и multipart-загрузка не
    поддерживаются. Поэтому слайд хранит исходный URL ассета Instagram CDN
    (его и подставит Telegram), а не локальный путь к файлу.
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
                    file_path = data.get("file_path")
                    if file_path and isinstance(file_path, str) and os.path.exists(file_path):
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

    def _get_url_hash(self, url: str) -> str:
        return get_url_hash(url)

    def get_from_cache(self, url: str) -> Optional[DownloadResult]:
        """Проверяет наличие видео/фото в кэше."""
        url_hash = get_url_hash(url)
        if url_hash in self.cache:
            cached = self.cache[url_hash]
            file_path = cached.get("file_path")
            photo_paths_raw = cached.get("photo_paths")
            is_photo = bool(cached.get("is_photo", False))

            if is_photo and isinstance(photo_paths_raw, list) and photo_paths_raw:
                existing = [p for p in photo_paths_raw if isinstance(p, str) and os.path.exists(p)]
                if existing:
                    return DownloadResult(
                        success=True,
                        file_path=existing[0],
                        title=cached.get("title"),
                        duration=cached.get("duration"),
                        is_photo=True,
                        photo_paths=existing,
                        carousel_slides=self._deserialize_carousel_slides(
                            cached.get("carousel_slides")
                        ),
                        from_cache=True,
                    )
                del self.cache[url_hash]
                self._save_cache()
                return None

            if file_path and isinstance(file_path, str) and os.path.exists(file_path):
                return DownloadResult(
                    success=True,
                    file_path=file_path,
                    title=cached.get("title"),
                    duration=cached.get("duration"),
                    is_photo=is_photo,
                    photo_paths=[file_path] if is_photo else None,
                    carousel_slides=self._deserialize_carousel_slides(
                        cached.get("carousel_slides")
                    ),
                    from_cache=True,
                )
            else:
                del self.cache[url_hash]
                self._save_cache()
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
            telegram_file_id = entry.get("telegram_file_id")
            new_entry: Dict[str, Any] = {
                "file_path": result.file_path,
                "title": result.title,
                "duration": result.duration,
            }
            if result.is_photo:
                new_entry["is_photo"] = True
                paths = result.photo_paths or [result.file_path]
                new_entry["photo_paths"] = list(paths)
            if result.carousel_slides:
                new_entry["carousel_slides"] = [
                    {"url": s.url, "is_video": bool(s.is_video)} for s in result.carousel_slides
                ]
            if telegram_file_id:
                new_entry["telegram_file_id"] = telegram_file_id
            self.cache[url_hash] = new_entry
            self._save_cache()

    def get_telegram_file_id(self, url: str) -> Optional[str]:
        """Возвращает сохранённый Telegram file_id для URL, если есть."""
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if not entry:
            return None
        file_id = entry.get("telegram_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    def set_telegram_file_id(self, url: str, file_id: str) -> None:
        """Сохраняет Telegram file_id для URL (используется для inline-mode)."""
        if not file_id:
            return
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if entry is None:
            entry = {}
            self.cache[url_hash] = entry
        if entry.get("telegram_file_id") == file_id:
            return
        entry["telegram_file_id"] = file_id
        self._save_cache()

    def get_cached_media_type(self, url: str) -> Optional[str]:
        """
        Возвращает фактический тип медиа для URL, как он сохранён в кэше:
        "photo" или "video". None — если запись отсутствует или тип не
        определён. Используется inline-хендлером, чтобы не спутать свежие
        file_id с залежавшимися от предыдущего скачивания другого типа.
        """
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
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

    def get_telegram_photo_file_id(self, url: str) -> Optional[str]:
        """Возвращает сохранённый Telegram photo file_id для URL, если есть."""
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if not entry:
            return None
        file_id = entry.get("telegram_photo_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    def set_telegram_photo_file_id(self, url: str, file_id: str) -> None:
        """Сохраняет Telegram photo file_id для URL (используется для inline-mode)."""
        if not file_id:
            return
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if entry is None:
            entry = {}
            self.cache[url_hash] = entry
        if entry.get("telegram_photo_file_id") == file_id:
            return
        entry["telegram_photo_file_id"] = file_id
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
        entry = self.cache.get(url_hash)
        if entry is None:
            entry = {}
            self.cache[url_hash] = entry
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

        image_urls: list[str] = []
        video_url: Optional[str] = None
        has_video_marker = False
        title: Optional[str] = None

        for candidate in candidates:
            html = self._http_get_html(candidate, cookie_jar=cookie_jar)
            if not html:
                continue

            parsed = self._parse_instagram_html(html)
            for img in parsed["image_urls"]:
                if img not in image_urls:
                    image_urls.append(img)
            if parsed["video_url"] and not video_url:
                video_url = parsed["video_url"]
            if parsed.get("has_video_marker"):
                has_video_marker = True
            if parsed["title"] and not title:
                title = parsed["title"]

            # Ранний выход: уже точно видео или уже набрали максимум слайдов —
            # дальше ходить по источникам бессмысленно. При 1–9 слайдах
            # продолжаем обходить все источники: один endpoint иногда отдаёт
            # только первый слайд, другой — полную карусель.
            if has_video_marker:
                break
            if len(image_urls) >= MAX_CAROUSEL_ITEMS:
                break

        if not image_urls and not video_url:
            return None

        return {
            "image_urls": image_urls[:MAX_CAROUSEL_ITEMS],
            "video_url": video_url,
            "has_video": has_video_marker,
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
        try:
            req = urllib.request.Request(
                candidate,
                headers={
                    "User-Agent": BROWSER_USER_AGENT,
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if cookie_jar is not None:
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                ctx = opener.open(req, timeout=15)
            else:
                ctx = urllib.request.urlopen(req, timeout=15)
            with ctx as resp:
                content_type = (resp.headers.get("Content-Type") or "").lower()
                if "html" not in content_type:
                    return None
                raw = resp.read(4 * 1024 * 1024)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug("HTML fetch failed for %s: %s", candidate, e)
            return None
        return raw.decode("utf-8", errors="ignore")

    @classmethod
    def _parse_instagram_html(cls, html: str) -> dict:
        """
        Возвращает dict: image_urls (list, порядок слайдов, дедуп),
        video_url, title.
        """
        image_urls: list[str] = []

        # 1) display_url из встроенного JSON. Это всегда оригинал без кропа и
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

        # Реелс/видеопост на embed-странице часто не отдаёт og:video, но в
        # inline JSON присутствуют `"video_url":"..."` и/или `"is_video":true`.
        # Поэтому этих маркеров хватает, чтобы не принять видео за фото.
        if not video_url:
            video_match = re.search(r'"video_url"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
            if video_match:
                video_url = cls._decode_json_str(video_match.group(1))
        has_video_marker = bool(video_url) or bool(re.search(r'"is_video"\s*:\s*true', html))

        title_contents = list(cls._find_meta_contents(html, "og:title"))
        title = title_contents[0] if title_contents else None

        return {
            "image_urls": image_urls,
            "video_url": video_url,
            "has_video_marker": has_video_marker,
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
                with open(output_path, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_FILE_SIZE:
                            f.close()
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
                        f.write(chunk)
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
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.warning("Не удалось скачать фото %s: %s", image_url, e)
            return None

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
                # Сохраняем слайды карусели: rich-карусель ещё будет отправлена, а
                # локальный фолбэк теперь валидное фото, а не 0-секундное видео.
                carousel_slides=result.carousel_slides,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("Frame extraction failed: %s", e)
            return None

    async def download(self, url: str) -> DownloadResult:
        """Скачивает видео по URL."""
        if not is_supported_url(url):
            return DownloadResult(
                success=False,
                error="URL не поддерживается. Поддерживаемые платформы: YouTube, Instagram, TikTok, X/Twitter",
                error_code="downloader.error.unsupported_url",
            )

        cached = self.get_from_cache(url)
        if cached:
            return cached

        loop = asyncio.get_event_loop()

        single_photo_fallback: Optional[DownloadResult] = None
        if is_instagram_photo_candidate_url(url):
            photo_result = await loop.run_in_executor(None, lambda: self._try_instagram_photo(url))
            if photo_result is not None:
                # Скрейп уже собрал полную карусель (>=2 слайдов) — отдаём как есть.
                if photo_result.carousel_slides:
                    self.add_to_cache(url, photo_result)
                    return photo_result
                # Найдено только одно фото. Это может быть и одиночный пост, и
                # карусель, слайды которой Instagram прячет от публичного скрейпа.
                # Если заданы IG-cookies, проваливаемся в yt-dlp — он с куками
                # перечислит все слайды карусели. Без кук yt-dlp всё равно отдаст
                # 403, поэтому оставляем одиночное фото и не тратим время.
                if self._get_instagram_cookiefile() is None:
                    self.add_to_cache(url, photo_result)
                    return photo_result
                single_photo_fallback = photo_result

        file_id = str(uuid.uuid4())[:8]
        output_path = str(self.download_dir / f"{file_id}.%(ext)s")
        ydl_opts = self._get_ydl_opts(output_path, url)

        try:
            result = await loop.run_in_executor(None, lambda: self._download_sync(url, ydl_opts))
            if result.success and is_instagram_photo_candidate_url(url) and result.file_path:
                # Extract frame for photo posts (yt-dlp downloads them as 0-second videos).
                # Also attempt for None duration — missing metadata on a /p/ post likely means photo.
                # Для карусели кадр тоже извлекаем (фолбэк-фото должно быть валидным);
                # carousel_slides переносится в результат, так что rich-карусель отправится.
                if result.duration is None or result.duration <= 1.0:
                    frame_result = await loop.run_in_executor(
                        None, lambda: self._extract_photo_frame(result)
                    )
                    if frame_result is not None:
                        result = frame_result
            # Принимаем результат yt-dlp, только если он реально добавил ценность:
            # собрал карусель или вернул фото. Иначе (одиночный пост, который
            # yt-dlp отдал 0-секундным видео, а FFmpeg не сконвертировал) лучше
            # отдать уже скачанное скрейпом фото, а не битое 0-секундное видео.
            if result.success and (
                result.carousel_slides or result.is_photo or single_photo_fallback is None
            ):
                self.add_to_cache(url, result)
                return result
            # Фолбэк НЕ кэшируем: транзиентный 403/таймаут yt-dlp не должен
            # навсегда запинить URL на одно фото — следующий запрос повторит
            # извлечение и сможет восстановить полную карусель.
            if single_photo_fallback is not None:
                return single_photo_fallback
            return result
        except Exception as e:
            if single_photo_fallback is not None:
                return single_photo_fallback
            msg = str(e)
            return DownloadResult(
                success=False,
                error=f"Ошибка при скачивании: {msg}",
                error_code="downloader.error.download_exception",
                error_args={"message": msg},
            )

    def _try_instagram_photo(self, url: str) -> Optional[DownloadResult]:
        """
        Если Instagram-пост без видео (фото или карусель фото), скачивает первые
        10 фото и возвращает DownloadResult(is_photo=True, photo_paths=[...]).
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
            parsed = urlparse(u)
            key = f"{parsed.hostname}{parsed.path}"
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
            for variant_url in variants:
                path = self._download_image_sync(variant_url, output_base)
                if path:
                    downloaded.append(path)
                    # Запоминаем URL варианта, который реально скачался: его же
                    # отдадим Telegram в rich-карусели — раз скачали мы, скорее
                    # всего скачает и он (тот же подписанный CDN-URL).
                    slide_urls.append(variant_url)
                    break

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
            carousel_slides=carousel_slides,
        )

    def _download_sync(self, url: str, ydl_opts: dict) -> DownloadResult:
        """Синхронная функция скачивания для запуска в executor."""

        def attempt_download(download_url: str, opts: dict) -> DownloadResult:
            all_downloaded: list[str] = []

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
                    return DownloadResult(
                        success=True,
                        file_path=image_files[0],
                        title=title,
                        duration=duration,
                        is_photo=True,
                        photo_paths=image_files[:MAX_CAROUSEL_ITEMS],
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
                    return DownloadResult(
                        success=True,
                        file_path=downloaded_file_path,
                        title=title,
                        duration=duration,
                        is_photo=is_photo,
                        photo_paths=[downloaded_file_path] if is_photo else None,
                        carousel_slides=carousel_slides,
                    )
                else:
                    outtmpl = opts.get("outtmpl", "")
                    if isinstance(outtmpl, dict):
                        outtmpl = outtmpl.get("default", "")
                    if isinstance(outtmpl, str) and outtmpl:
                        file_id = os.path.basename(outtmpl).split(".")[0]
                    else:
                        file_id = None

                    found_file = self._find_downloaded_file(file_id) if file_id else None
                    if found_file:
                        file_ext = os.path.splitext(found_file)[1].lower()
                        is_photo = file_ext in _IMAGE_EXTENSIONS
                        return DownloadResult(
                            success=True,
                            file_path=found_file,
                            title=title,
                            duration=duration,
                            is_photo=is_photo,
                            photo_paths=[found_file] if is_photo else None,
                            carousel_slides=carousel_slides,
                        )
                    return DownloadResult(
                        success=False,
                        error="Файл не был скачан",
                        error_code="downloader.error.file_not_downloaded",
                    )

        try:
            return attempt_download(url, ydl_opts)

        except DownloadError as e:
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
                    return attempt_download(url, ydl_opts_no_cookies)
                except DownloadError as e2:
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
                        return attempt_download(kk_url, ydl_opts_kk)
                    except DownloadError as e2:
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
            elif "there is no video in this post" in error_msg_lower and is_instagram_url(url):
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

    def clear_cache(self) -> int:
        """Очищает весь кэш и удаляет файлы. Возвращает количество удалённых файлов."""
        count = 0
        for data in list(self.cache.values()):
            paths: list[str] = []
            file_path = data.get("file_path")
            if isinstance(file_path, str):
                paths.append(file_path)
            photo_paths = data.get("photo_paths")
            if isinstance(photo_paths, list):
                for p in photo_paths:
                    if isinstance(p, str) and p not in paths:
                        paths.append(p)
            for p in paths:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        count += 1
                    except OSError:
                        pass
        self.cache.clear()
        self._save_cache()
        return count


# Создаём глобальный экземпляр загрузчика
downloader = VideoDownloader()
