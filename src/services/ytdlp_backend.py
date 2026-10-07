"""Ytdlp backend implementation for the downloader."""

import logging
import os
from typing import Optional

import yt_dlp
from yt_dlp.utils import DownloadError

from src.config import MAX_FILE_SIZE
from src.services.download_quality import DEFAULT_QUALITY, QUALITY_RESOLUTIONS, validate_quality
from src.services.media import (
    _IMAGE_EXTENSIONS,
    _VIDEO_EXTENSIONS,
    MAX_CAROUSEL_ITEMS,
    TELEGRAM_BOT_USER_AGENT,
    CarouselSlide,
    DownloadResult,
    _download_retry_delay,
)
from src.services.url_utils import (
    build_kkinstagram_url,
    is_instagram_photo_candidate_url,
    is_instagram_post_url,
    is_instagram_url,
    is_kkinstagram_url,
    is_single_youtube_video_url,
    is_twitter_url,
    is_youtube_url,
    should_retry_with_kkinstagram,
)

logger = logging.getLogger(__name__)


class YtDlpMixin:
    def _entry_local_path(self, ydl, entry: dict, tracked: list[str], by_id: dict, used: set):
        """Resolve this entry's final file, including renamed/merged yt-dlp output."""
        expected_ext = "." + str(entry.get("ext") or "").lower()
        expected_photo = expected_ext in _IMAGE_EXTENSIONS
        known_kind = expected_ext in _IMAGE_EXTENSIONS | _VIDEO_EXTENSIONS
        candidates = [entry.get("filepath"), entry.get("_filename")]
        candidates.extend(reversed(by_id.get(str(entry.get("id")), [])))
        for item in entry.get("requested_downloads") or []:
            if isinstance(item, dict):
                candidates.append(item.get("filepath"))
        try:
            candidates.append(ydl.prepare_filename(entry))
        except Exception:
            pass
        media_id = str(entry.get("id") or "")
        if media_id:
            candidates.extend(
                path for path in tracked if os.path.splitext(path)[0].endswith("_" + media_id)
            )
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            base, ext = os.path.splitext(candidate)
            variants = [candidate] + [base + suffix for suffix in sorted(_VIDEO_EXTENSIONS)]
            for path in variants:
                if (
                    path not in used
                    and os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS | _VIDEO_EXTENSIONS
                    and (
                        not known_kind
                        or (os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS)
                        == expected_photo
                    )
                    and os.path.isfile(path)
                ):
                    return path
        # Some extractors omit IDs/filenames. A unique remaining file of the
        # correct kind is safe; never pair one entry with an arbitrary last file.
        want_photo = "." + str(entry.get("ext") or "").lower() in _IMAGE_EXTENSIONS
        remaining = [
            path
            for path in tracked
            if path not in used
            and os.path.isfile(path)
            and (os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS) == want_photo
            and os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS | _VIDEO_EXTENSIONS
        ]
        return remaining[0] if len(remaining) == 1 else None

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

        dimensions = [entry.get("width"), entry.get("height")]
        selected_resolution = (
            min(dimensions)
            if all(isinstance(value, (int, float)) and value > 0 for value in dimensions)
            else None
        )
        if (
            want_video
            and selected_resolution
            and is_http(entry.get("url"))
            and not entry.get("requested_formats")
        ):
            # yt-dlp already selected this progressive/silent source for the
            # caller's profile. Rich URL fallbacks must retain that selection.
            return entry["url"]

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
                    dimensions = [fmt.get("width"), fmt.get("height")]
                    if (
                        selected_resolution
                        and all(
                            isinstance(value, (int, float)) and value > 0 for value in dimensions
                        )
                        and min(dimensions) > selected_resolution
                    ):
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

    def _get_ydl_opts(self, output_path: str, url: str, *, quality: str = DEFAULT_QUALITY) -> dict:
        """Возвращает опции для yt-dlp."""
        resolution = QUALITY_RESOLUTIONS[validate_quality(quality)]
        size_filter = f"[filesize<=?{MAX_FILE_SIZE}]"
        if self.has_ffmpeg:
            fmt = (
                f"bestvideo*{size_filter}+bestaudio{size_filter}"
                f"/best{size_filter}/bestvideo*{size_filter}"
            )
            merge_format = "mp4"
        else:
            fmt = (
                f"best[ext=mp4]{size_filter}/best{size_filter}"
                f"/bestvideo*[ext=mp4]{size_filter}/bestvideo*{size_filter}"
            )
            merge_format = None

        opts = {
            "format": fmt,
            # res uses the shorter side: 1080x1920 Reels count as 1080p.
            # If the source has no format below the target, choose its smallest
            # available resolution instead of transcoding inside the bot.
            "format_sort": [f"res:{resolution}", "vcodec:h264", "ext:mp4:m4a"],
            "format_sort_force": True,
            **({"merge_output_format": merge_format} if merge_format else {}),
            "outtmpl": output_path,
            "max_filesize": MAX_FILE_SIZE,
            # Bound extraction before yt-dlp starts downloading playlist entries.
            "noplaylist": not self._supports_carousel(url),
            "playlist_items": f"1:{MAX_CAROUSEL_ITEMS}" if self._supports_carousel(url) else "1",
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

    def _download_sync(self, url: str, ydl_opts: dict) -> DownloadResult:
        """Синхронная функция скачивания для запуска в executor."""

        if is_youtube_url(url) and not is_single_youtube_video_url(url):
            return DownloadResult(
                success=False,
                error="A link to a single YouTube video is required",
                error_code="downloader.error.single_video_required",
            )

        cookie_snapshot: Optional[str] = None
        try:
            try:
                prepared_opts, cookie_snapshot = self._prepare_ydl_cookie_snapshot(ydl_opts)
            except OSError as e:
                # Public media can still work without cookies. Keep the
                # technical failure in server logs instead of exposing a temp
                # filesystem error to the user.
                logger.warning(
                    "Не удалось создать рабочую копию cookies; продолжаю без cookies: %s",
                    e,
                )
                prepared_opts = ydl_opts.copy()
                prepared_opts.pop("cookiefile", None)
            return self._download_sync_with_opts(url, prepared_opts)
        finally:
            if cookie_snapshot:
                try:
                    os.remove(cookie_snapshot)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning("Не удалось удалить временную копию cookies: %s", e)

    def _download_sync_with_opts(self, url: str, ydl_opts: dict) -> DownloadResult:
        """Run yt-dlp while the caller owns the cookie snapshot lifecycle."""

        attempt_downloads: list[list[str]] = []

        def cleanup_failed_attempts() -> None:
            for paths in attempt_downloads:
                self._delete_entry_files({"photo_paths": paths})

        def attempt_download(download_url: str, opts: dict) -> DownloadResult:
            all_downloaded: list[str] = []
            paths_by_id: dict[str, list[str]] = {}
            attempt_downloads.append(all_downloaded)

            def progress_hook(d):
                if d.get("status") == "finished":
                    filename = d.get("filename")
                    if isinstance(filename, str) and filename not in all_downloaded:
                        all_downloaded.append(filename)
                    info_dict = d.get("info_dict") or {}
                    if isinstance(filename, str) and info_dict.get("id") is not None:
                        paths_by_id.setdefault(str(info_dict["id"]), []).append(filename)

            def postprocessor_hook(data):
                if data.get("status") == "finished":
                    info_dict = data.get("info_dict") or {}
                    progress_hook(
                        {
                            "status": "finished",
                            "filename": info_dict.get("filepath"),
                            "info_dict": info_dict,
                        }
                    )

            opts = opts.copy()
            opts["progress_hooks"] = [progress_hook]
            opts["postprocessor_hooks"] = [postprocessor_hook]

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

                playlist_title = info.get("title") if isinstance(info, dict) else None
                entries = info.get("entries") if isinstance(info, dict) else None
                if entries:
                    entries = [entry for entry in entries if isinstance(entry, dict)]
                    if not entries:
                        return DownloadResult(
                            success=False,
                            error="No downloadable playlist entries",
                            error_code="downloader.error.no_playlist_video",
                        )
                else:
                    entries = [info] if isinstance(info, dict) else []
                    # A few extractors report ordered photo files only through
                    # progress hooks, without exposing playlist entries. Preserve
                    # those photos and let Telegram use their local upload files.
                    existing = [path for path in all_downloaded if os.path.isfile(path)]
                    if (
                        self._supports_carousel(download_url)
                        and len(existing) > 1
                        and all(
                            os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS
                            for path in existing
                        )
                    ):
                        entries = [
                            {
                                "filepath": path,
                                "ext": os.path.splitext(path)[1].lstrip("."),
                                "vcodec": "none",
                            }
                            for path in existing[:MAX_CAROUSEL_ITEMS]
                        ]
                if not entries:
                    return DownloadResult(
                        success=False,
                        error="No media information",
                        error_code="downloader.error.no_info",
                    )
                is_carousel = self._supports_carousel(download_url) and len(entries) > 1
                entries = entries[:MAX_CAROUSEL_ITEMS] if is_carousel else entries[:1]
                used: set[str] = set()
                slides: list[CarouselSlide] = []
                for entry in entries:
                    path = self._entry_local_path(ydl, entry, all_downloaded, paths_by_id, used)
                    if path is None and not is_carousel:
                        outtmpl = opts.get("outtmpl", "")
                        if isinstance(outtmpl, dict):
                            outtmpl = outtmpl.get("default", "")
                        prefix = (
                            os.path.basename(outtmpl).split("%", 1)[0].rstrip("._-")
                            if isinstance(outtmpl, str) and outtmpl
                            else None
                        )
                        path = self._find_downloaded_file(prefix) if prefix else None
                    if not path:
                        return DownloadResult(
                            success=False,
                            error="A downloaded media file is missing",
                            error_code="downloader.error.file_not_downloaded",
                        )
                    if path not in all_downloaded:
                        all_downloaded.append(path)
                    actual_size = os.path.getsize(path)
                    if actual_size > MAX_FILE_SIZE:
                        return DownloadResult(
                            success=False,
                            error="Downloaded file is too large",
                            error_code="downloader.error.file_too_large",
                            error_args={
                                "size_mb": actual_size // (1024 * 1024),
                                "max_mb": MAX_FILE_SIZE // (1024 * 1024),
                            },
                        )
                    used.add(path)
                    is_video = os.path.splitext(path)[1].lower() not in _IMAGE_EXTENSIONS
                    width = height = duration = None
                    if is_video:
                        width, height, duration = self._video_metadata(entry, path)
                    slide = self._entry_to_slide(entry)
                    slides.append(
                        CarouselSlide(
                            url=slide.url if slide else "",
                            is_video=is_video,
                            local_path=path,
                            width=width,
                            height=height,
                            duration=duration,
                        )
                    )
                first = slides[0]
                title = (
                    playlist_title
                    if is_carousel and playlist_title
                    else entries[0].get("title", "Видео")
                )
                photos = [slide.local_path for slide in slides if not slide.is_video]
                return DownloadResult(
                    success=True,
                    file_path=first.local_path,
                    title=title,
                    duration=first.duration,
                    is_photo=not first.is_video,
                    photo_paths=photos or None,
                    width=first.width,
                    height=first.height,
                    carousel_slides=slides if is_carousel else None,
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
                keep.update(
                    slide.local_path
                    for slide in result.carousel_slides or []
                    if isinstance(slide.local_path, str)
                )
            self._delete_entry_files(
                {"photo_paths": [path for path in produced_paths() if path not in keep]}
            )
            return result

        active_ydl_opts = ydl_opts
        try:
            return run_attempt(url, active_ydl_opts)

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
                active_ydl_opts = ydl_opts_no_cookies
                try:
                    return run_attempt(url, active_ydl_opts)
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
                    ydl_opts_kk = active_ydl_opts.copy()
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
            elif "no video formats found" in error_msg_lower and is_instagram_post_url(url):
                return DownloadResult(
                    success=False,
                    error="Instagram не вернул медиаформаты",
                    error_code="downloader.error.instagram_no_formats",
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
