"""Media io implementation for the downloader."""

import json
import logging
import math
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from src.config import MAX_FILE_SIZE
from src.services.media import (
    TELEGRAM_BOT_USER_AGENT,
    DownloadResult,
    _download_retry_delay,
)

logger = logging.getLogger(__name__)


class MediaIOMixin:
    def _prepare_video_previews(self, result: DownloadResult) -> None:
        """Decode explicit covers once per video, including every carousel slide."""
        if not result.success or not self.has_ffmpeg:
            return
        previews: dict[str, tuple[Optional[str], Optional[str]]] = {}

        def prepare(path, duration):
            if path not in previews:
                previews[path] = self._extract_video_previews(path, duration)
            return previews[path]

        if result.file_path and not result.is_photo:
            result.thumbnail_path, result.cover_path = prepare(result.file_path, result.duration)
        for slide in result.carousel_slides or []:
            if slide.is_video and slide.local_path:
                slide.thumbnail_path, slide.cover_path = prepare(slide.local_path, slide.duration)

    @staticmethod
    def _extract_video_previews(
        video_path: str, duration: Optional[float]
    ) -> tuple[Optional[str], Optional[str]]:
        """Produce a <=320px JPEG thumbnail and a sharper <=1280px cover.

        FFmpeg decodes the selected frame (with autorotation) instead of trusting
        a platform's often square cover or Telegram's automatic first frame.
        Preview errors are optional: keep the original video playable.
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None, None
        thumbnail = Path(video_path).with_suffix(".thumbnail.jpg")
        cover = Path(video_path).with_suffix(".cover.jpg")
        timestamp = (
            min(1.0, duration / 2) if duration and math.isfinite(duration) and duration > 0 else 1.0
        )

        def scale(size):
            # Normalize non-square pixels before fitting, without cropping or upscaling.
            return (
                "scale=iw*sar:ih,setsar=1,"
                f"scale=w='min({size},iw)':h='min({size},ih)'"
                ":force_original_aspect_ratio=decrease"
            )

        for seek in (timestamp, 0.0):
            try:
                proc = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-y",
                        "-ss",
                        str(seek),
                        "-i",
                        video_path,
                        "-filter_complex",
                        f"[0:v:0]split=2[c][t];[c]{scale(1280)}[cover];[t]{scale(320)}[thumb]",
                        "-map",
                        "[cover]",
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        str(cover),
                        "-map",
                        "[thumb]",
                        "-frames:v",
                        "1",
                        "-q:v",
                        "5",
                        str(thumbnail),
                    ],
                    capture_output=True,
                    timeout=15,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
                if (
                    proc.returncode == 0
                    and cover.is_file()
                    and 0 < cover.stat().st_size < 10 * 1024 * 1024
                    and thumbnail.is_file()
                    and 0 < thumbnail.stat().st_size < 200_000
                ):
                    return str(thumbnail), str(cover)
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("Video preview extraction failed for %s: %s", video_path, exc)
            for path in (thumbnail, cover):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove partial video preview %s", path)
        logger.warning("Could not generate video previews for %s", video_path)
        return None, None

    def _download_image_sync(self, image_url: str, output_base: str) -> Optional[str]:
        """
        Скачивает картинку по прямой ссылке и сохраняет её рядом с output_base,
        выбирая расширение по Content-Type или URL. Возвращает путь к файлу или None.
        """
        # CDN URLs and exception messages can contain signed tokens or session data.
        # Keep diagnostics to status codes, sizes, retry delays and exception types.
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
                        logger.warning("Отбрасываю фото: Content-Type не является image/*")
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
                                    "Image exceeds MAX_FILE_SIZE (%s bytes), aborted",
                                    total,
                                )
                                return None
                            file.write(chunk)
                    if total < 1024:
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                        logger.warning(
                            "Отбрасываю фото: слишком маленький файл (%s байт)",
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
                        "Временная HTTP %s при загрузке фото, повтор через %ss",
                        exc.code,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("Не удалось скачать фото: HTTP %s", exc.code)
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
                        "Временная ошибка загрузки фото, повтор через %ss (%s)",
                        delay,
                        type(exc).__name__,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("Не удалось скачать фото: %s", type(exc).__name__)
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
            except TypeError, ValueError, OverflowError:
                return None
            return number if number > 0 else None

        def optional_float(value: object) -> Optional[float]:
            try:
                number = float(value)
            except TypeError, ValueError, OverflowError:
                return None
            return number if number >= 0 else None

        def signed_float(value: object) -> Optional[float]:
            try:
                return float(value)
            except TypeError, ValueError, OverflowError:
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
