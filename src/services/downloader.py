"""
Модуль для скачивания видео с различных платформ.
Поддерживает: YouTube, Instagram Reels, TikTok, X/Twitter
"""

import asyncio
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yt_dlp
from yt_dlp.utils import DownloadError

from src.config import DOWNLOAD_DIR, MAX_FILE_SIZE, YT_COOKIES_FILE
from src.services.url_utils import (
    build_kkinstagram_url,
    get_platform_name,
    get_url_hash,
    is_supported_url,
    is_youtube_url,
    should_retry_with_kkinstagram,
)

logger = logging.getLogger(__name__)

TELEGRAM_BOT_USER_AGENT = "TelegramBot (like TwitterBot)"


@dataclass
class DownloadResult:
    """Результат скачивания видео."""

    success: bool
    file_path: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    from_cache: bool = False


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
        """Проверяет наличие видео в кэше."""
        url_hash = get_url_hash(url)
        if url_hash in self.cache:
            cached = self.cache[url_hash]
            file_path = cached.get("file_path")
            if file_path and isinstance(file_path, str) and os.path.exists(file_path):
                return DownloadResult(
                    success=True,
                    file_path=file_path,
                    title=cached.get("title"),
                    duration=cached.get("duration"),
                    from_cache=True,
                )
            else:
                del self.cache[url_hash]
                self._save_cache()
        return None

    def add_to_cache(self, url: str, result: DownloadResult) -> None:
        """Добавляет результат в кэш."""
        if result.success and result.file_path:
            url_hash = get_url_hash(url)
            entry = self.cache.get(url_hash, {})
            telegram_file_id = entry.get("telegram_file_id")
            new_entry = {
                "file_path": result.file_path,
                "title": result.title,
                "duration": result.duration,
            }
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

    def get_telegram_round_file_id(self, url: str) -> Optional[str]:
        """Возвращает сохранённый Telegram file_id для round-видео, если есть."""
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if not entry:
            return None
        file_id = entry.get("telegram_round_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    def set_telegram_round_file_id(self, url: str, file_id: str) -> None:
        """Сохраняет Telegram file_id для round-видео."""
        if not file_id:
            return
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if entry is None:
            entry = {}
            self.cache[url_hash] = entry
        if entry.get("telegram_round_file_id") == file_id:
            return
        entry["telegram_round_file_id"] = file_id
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

        return opts

    async def download(self, url: str) -> DownloadResult:
        """Скачивает видео по URL."""
        if not is_supported_url(url):
            return DownloadResult(
                success=False,
                error="URL не поддерживается. Поддерживаемые платформы: YouTube, Instagram, TikTok, X/Twitter",
            )

        cached = self.get_from_cache(url)
        if cached:
            return cached

        file_id = str(uuid.uuid4())[:8]
        output_path = str(self.download_dir / f"{file_id}.%(ext)s")
        ydl_opts = self._get_ydl_opts(output_path, url)

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self._download_sync(url, ydl_opts))
            if result.success:
                self.add_to_cache(url, result)
            return result
        except Exception as e:
            return DownloadResult(success=False, error=f"Ошибка при скачивании: {str(e)}")

    def _download_sync(self, url: str, ydl_opts: dict) -> DownloadResult:
        """Синхронная функция скачивания для запуска в executor."""

        def attempt_download(download_url: str, opts: dict) -> DownloadResult:
            downloaded_file_path = None

            def progress_hook(d):
                nonlocal downloaded_file_path
                if d.get("status") == "finished":
                    filename = d.get("filename")
                    if isinstance(filename, str):
                        downloaded_file_path = filename

            opts = opts.copy()
            opts["progress_hooks"] = [progress_hook]

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(download_url, download=True)

                if info is None:
                    return DownloadResult(
                        success=False, error="Не удалось получить информацию о видео"
                    )

                logger.debug(
                    "yt-dlp info type: %s, keys: %s",
                    type(info).__name__,
                    list(info.keys()) if isinstance(info, dict) else "N/A",
                )

                if "entries" in info and info["entries"]:
                    entries = info["entries"]
                    logger.debug(
                        "entries type: %s, len: %s",
                        type(entries).__name__,
                        len(entries) if hasattr(entries, "__len__") else "N/A",
                    )
                    for entry in entries:
                        if entry is not None and isinstance(entry, dict):
                            info = entry
                            break
                    else:
                        return DownloadResult(
                            success=False, error="Не удалось получить видео из плейлиста"
                        )

                title = info.get("title", "Видео") if isinstance(info, dict) else "Видео"
                duration = info.get("duration") if isinstance(info, dict) else None

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
                        for ext in ["mp4", "webm", "mkv", "mov"]:
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
                        return DownloadResult(
                            success=False,
                            error=(
                                f"Скачанный файл слишком большой "
                                f"({actual_size // (1024 * 1024)}MB). "
                                f"Максимум: {MAX_FILE_SIZE // (1024 * 1024)}MB"
                            ),
                        )
                    return DownloadResult(
                        success=True, file_path=downloaded_file_path, title=title, duration=duration
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
                        return DownloadResult(
                            success=True, file_path=found_file, title=title, duration=duration
                        )
                    return DownloadResult(success=False, error="Файл не был скачан")

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
                )
            if "Video unavailable" in error_msg:
                return DownloadResult(success=False, error="Видео недоступно")
            elif "Private video" in error_msg:
                return DownloadResult(success=False, error="Это приватное видео")
            elif "Sign in" in error_msg or "login" in error_msg_lower:
                return DownloadResult(
                    success=False, error="Требуется авторизация для просмотра этого видео"
                )
            else:
                return DownloadResult(success=False, error=f"Ошибка скачивания: {error_msg[:200]}")
        except Exception as e:
            return DownloadResult(success=False, error=f"Неожиданная ошибка: {str(e)[:200]}")

    def _find_downloaded_file(self, file_id: str) -> Optional[str]:
        """Находит скачанный файл по ID."""
        for ext in ["mp4", "webm", "mkv", "mov", "avi"]:
            file_path = self.download_dir / f"{file_id}.{ext}"
            if file_path.exists():
                return str(file_path)

        for file in self.download_dir.iterdir():
            if file.stem.startswith(file_id) and file.suffix in [
                ".mp4",
                ".webm",
                ".mkv",
                ".mov",
                ".avi",
            ]:
                return str(file)

        return None

    def clear_cache(self) -> int:
        """Очищает весь кэш и удаляет файлы. Возвращает количество удалённых файлов."""
        count = 0
        for url_hash, data in list(self.cache.items()):
            file_path = data.get("file_path")
            if file_path and isinstance(file_path, str) and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    count += 1
                except Exception:
                    pass
        self.cache.clear()
        self._save_cache()
        return count


# Создаём глобальный экземпляр загрузчика
downloader = VideoDownloader()
