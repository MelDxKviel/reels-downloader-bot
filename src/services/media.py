"""Media results and shared downloader constants."""

from dataclasses import dataclass
from typing import Dict, Optional

TELEGRAM_BOT_USER_AGENT = "TelegramBot (like TwitterBot)"

_INSTAGRAM_APP_ID = "936619743392459"
_INSTAGRAM_ASBD_ID = "198387"
_INSTAGRAM_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})

# Older entries/file IDs lack explicit video previews (or have legacy photo data).
# Discard them lazily so Telegram does not keep serving a broken auto-thumbnail;
# unrelated MP3 file IDs survive.
_MEDIA_CACHE_VERSION = 5


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
    local_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    thumbnail_path: Optional[str] = None
    cover_path: Optional[str] = None


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
    thumbnail_path: Optional[str] = None
    cover_path: Optional[str] = None
