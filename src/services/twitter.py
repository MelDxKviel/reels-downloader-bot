"""Twitter implementation for the downloader."""

import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional
from urllib.parse import urlparse

from src.services.media import (
    BROWSER_USER_AGENT,
    MAX_CAROUSEL_ITEMS,
    CarouselSlide,
    DownloadResult,
    _download_retry_delay,
)

logger = logging.getLogger(__name__)


class TwitterMixin:
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
        except ValueError, json.JSONDecodeError:
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
        """Download every slide of a Twitter carousel containing photos."""
        fetched = self._fetch_twitter_media(url)
        if fetched is None:
            return None
        slides, caption = fetched
        if len(slides) < 2 or all(slide.is_video for slide in slides):
            return None
        batch_id = str(uuid.uuid4())[:8]
        photo_paths: list[str] = []
        downloaded: list[str] = []
        for idx, slide in enumerate(slides):
            if slide.is_video:
                output = str(self.download_dir / f"{batch_id}_{idx}_%(id)s.%(ext)s")
                result = self._download_sync(slide.url, self._get_ydl_opts(output, slide.url))
                path = result.file_path if result.success and not result.is_photo else None
                if path:
                    slide.width, slide.height, slide.duration = (
                        result.width,
                        result.height,
                        result.duration,
                    )
                elif result.success:
                    self.discard_result_files(result)
            else:
                path = self._download_image_sync(
                    slide.url, str(self.download_dir / f"{batch_id}_{idx}")
                )
            if not path:
                self._delete_entry_files({"photo_paths": downloaded})
                return None
            slide.local_path = path
            downloaded.append(path)
            if not slide.is_video:
                photo_paths.append(path)
        first = slides[0]
        return DownloadResult(
            success=True,
            file_path=first.local_path,
            title=(caption or "Tweet"),
            is_photo=not first.is_video,
            photo_paths=photo_paths,
            carousel_slides=slides,
            width=first.width,
            height=first.height,
            duration=first.duration,
        )
