"""Download orchestration and the shared downloader instance."""

import asyncio
import logging
import shutil
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

from src.config import DOWNLOAD_DIR, DOWNLOAD_TIMEOUT
from src.services.cookies import CookieMixin
from src.services.download_cache import DownloadCacheMixin
from src.services.download_jobs import JobQueueFull, jobs
from src.services.download_worker import run_download_worker
from src.services.instagram import InstagramMixin
from src.services.media import _MEDIA_CACHE_VERSION as _MEDIA_CACHE_VERSION
from src.services.media import MAX_CAROUSEL_ITEMS as MAX_CAROUSEL_ITEMS
from src.services.media import CarouselSlide as CarouselSlide
from src.services.media import DownloadResult as DownloadResult
from src.services.media import _is_instagram_cdn_host as _is_instagram_cdn_host
from src.services.media_io import MediaIOMixin
from src.services.twitter import TwitterMixin
from src.services.url_utils import (
    get_platform_name,
    is_instagram_photo_candidate_url,
    is_instagram_post_url,
    is_supported_url,
    is_twitter_url,
)
from src.services.ytdlp_backend import YtDlpMixin

logger = logging.getLogger(__name__)


class VideoDownloader(
    DownloadCacheMixin, CookieMixin, InstagramMixin, TwitterMixin, MediaIOMixin, YtDlpMixin
):
    def __init__(self, download_dir: str = DOWNLOAD_DIR, *, worker_runner=run_download_worker):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.download_dir / "cache.json"
        self._cache_lock = threading.RLock()
        self._pins: dict[str, int] = {}
        self._leases: dict = {}
        self._pending_deletions: set[str] = set()
        self._inflight: dict = {}
        self._worker_runner = worker_runner
        self.cache: Dict[str, dict] = self._load_cache()
        self.has_ffmpeg: bool = shutil.which("ffmpeg") is not None

    def is_supported_url(self, url: str) -> bool:
        return is_supported_url(url)

    def get_platform_name(self, url: str) -> str:
        return get_platform_name(url)

    async def download(
        self,
        url: str,
        allow_carousel: bool = True,
        *,
        user_id: int | None = None,
        reserve: bool = False,
    ) -> DownloadResult:
        """Bounded downloads; cached requests for the same URL share one worker."""
        if not is_supported_url(url):
            return DownloadResult(success=False, error_code="downloader.error.unsupported_url")
        if not allow_carousel:
            return await self._run_download(url, allow_carousel=False, user_id=user_id)
        cached = self.get_from_cache(url, reserve=reserve)
        if cached is not None:
            return cached
        key = self._get_url_hash(url)
        job = self._inflight.get(key)
        if job is None:
            task = asyncio.create_task(
                self._run_download(url, allow_carousel=True, user_id=user_id)
            )
            job = {"task": task, "waiters": 0}
            self._inflight[key] = job
        job["waiters"] += 1
        try:
            # One disconnected caller must not cancel another caller's download.
            result = await asyncio.shield(job["task"])
            result = replace(result)
            with self._cache_lock:
                if result.success:
                    if reserve:
                        self.reserve_result(result)
                    # A cleanup may have run while the worker was active.
                    self.add_to_cache(url, result)
            return result
        finally:
            job["waiters"] -= 1
            if not job["waiters"]:
                self._inflight.pop(key, None)
                task = job["task"]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                elif not task.cancelled() and task.exception() is None:
                    # If all callers were cancelled just as the worker completed,
                    # retain its successful result rather than leaving orphan files.
                    result = task.result()
                    if result.success and key not in self.cache:
                        self.add_to_cache(url, result)

    async def _run_download(self, url: str, *, allow_carousel: bool, user_id: int | None):
        try:
            return await jobs.run(user_id, lambda: self._worker_runner(self, url, allow_carousel))
        except JobQueueFull:
            return DownloadResult(success=False, error_code="downloader.error.busy")

    async def run_conversion(self, user_id: int | None, operation):
        async def convert():
            async with asyncio.timeout(DOWNLOAD_TIMEOUT):
                return await operation()

        try:
            return await jobs.run(user_id, convert)
        except (JobQueueFull, TimeoutError):
            logger.warning("Conversion rejected or deadline exceeded for user %s", user_id)
            return None

    async def _download_source(self, url: str, allow_carousel: bool = True) -> DownloadResult:
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
                if (
                    photo_fallback is not None
                    and result.is_photo
                    and not any(slide.is_video for slide in result.carousel_slides or [])
                ):
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
                        return richer_photo
                return result

            if photo_fallback is not None and photo_fallback_confirmed:
                confirmed_photo = photo_fallback
                photo_fallback = None
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
                return confirmed_photo

            # Some current Instagram photo posts are shared as /reel/ URLs.
            # Normal reels must not pay for a duplicate authenticated API call,
            # so probe this exceptional case only after yt-dlp reports the exact
            # no-formats failure produced by a photographic product entry.
            if (
                result.error_code == "downloader.error.instagram_no_formats"
                and is_instagram_post_url(url)
                and not is_instagram_photo_candidate_url(url)
            ):
                exact_photo = await loop.run_in_executor(
                    None, lambda: self._try_instagram_photo(url)
                )
                if exact_photo is not None and exact_photo.media_type_confirmed:
                    return exact_photo
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


# Shared service used by handlers.
downloader = VideoDownloader()
