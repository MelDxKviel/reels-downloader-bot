"""
Модуль для скачивания видео с различных платформ.
Поддерживает: YouTube, Instagram Reels, TikTok, X/Twitter
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict

import yt_dlp

from src.config import DOWNLOAD_DIR, MAX_FILE_SIZE, YT_COOKIES_FILE

logger = logging.getLogger(__name__)


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
    
    # Регулярные выражения для поддерживаемых платформ
    SUPPORTED_PATTERNS = [
        # YouTube
        r'(youtube\.com|youtu\.be)',
        # Instagram Reels
        r'(instagram\.com/reel|instagram\.com/p)',
        # TikTok
        r'(tiktok\.com|vm\.tiktok\.com)',
        # X/Twitter
        r'(twitter\.com|x\.com)',
    ]
    
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
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                # Проверяем, что файлы из кэша существуют
                valid_cache = {}
                for url_hash, data in cache.items():
                    file_path = data.get('file_path')
                    # Убеждаемся, что file_path — строка
                    if file_path and isinstance(file_path, str) and os.path.exists(file_path):
                        valid_cache[url_hash] = data
                return valid_cache
            except Exception:
                return {}
        return {}
    
    def _save_cache(self) -> None:
        """Сохраняет кэш в файл."""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def _get_url_hash(self, url: str) -> str:
        """Создаёт хэш URL для использования как ключ кэша."""
        # Нормализуем URL (убираем параметры отслеживания и т.п.)
        normalized = re.sub(r'[?&](utm_\w+|si|feature|ref)=[^&]*', '', url)
        return hashlib.md5(normalized.encode()).hexdigest()[:16]
    
    def get_from_cache(self, url: str) -> Optional[DownloadResult]:
        """Проверяет наличие видео в кэше."""
        url_hash = self._get_url_hash(url)
        if url_hash in self.cache:
            cached = self.cache[url_hash]
            file_path = cached.get('file_path')
            # Убеждаемся, что file_path — строка
            if file_path and isinstance(file_path, str) and os.path.exists(file_path):
                return DownloadResult(
                    success=True,
                    file_path=file_path,
                    title=cached.get('title'),
                    duration=cached.get('duration'),
                    from_cache=True
                )
            else:
                # Файл удалён или путь невалидный, убираем из кэша
                del self.cache[url_hash]
                self._save_cache()
        return None
    
    def add_to_cache(self, url: str, result: DownloadResult) -> None:
        """Добавляет результат в кэш."""
        if result.success and result.file_path:
            url_hash = self._get_url_hash(url)
            self.cache[url_hash] = {
                'file_path': result.file_path,
                'title': result.title,
                'duration': result.duration
            }
            self._save_cache()
    
    def is_supported_url(self, url: str) -> bool:
        """Проверяет, поддерживается ли URL."""
        for pattern in self.SUPPORTED_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        return False
    
    def get_platform_name(self, url: str) -> str:
        """Определяет название платформы по URL."""
        url_lower = url.lower()
        if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            return 'YouTube'
        elif 'instagram.com' in url_lower:
            return 'Instagram'
        elif 'tiktok.com' in url_lower:
            return 'TikTok'
        elif 'twitter.com' in url_lower or 'x.com' in url_lower:
            return 'X/Twitter'
        return 'Unknown'
    
    def _is_youtube_url(self, url: str) -> bool:
        url_lower = url.lower()
        return 'youtube.com' in url_lower or 'youtu.be' in url_lower

    def _looks_like_netscape_cookies_file(self, path: str) -> bool:
        """
        Быстрая проверка, что файл похож на cookies в Netscape формате (требуется yt-dlp).
        Не гарантирует 100% валидность, но отсекает явно неверные файлы.
        """
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                # Смотрим первые непустые строки
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

                    # Комментарии пропускаем
                    if stripped.startswith("#"):
                        continue

                    # Типичная строка cookies файла: 7+ полей, разделённых табами
                    if "\t" in stripped:
                        parts = stripped.split("\t")
                        if len(parts) >= 7:
                            return True

                    # Любой другой контент в первых строках — скорее всего не Netscape формат
                    break
        except OSError:
            return False

        return False

    def _get_youtube_cookiefile(self) -> Optional[str]:
        """
        Возвращает путь к cookies для YouTube, если файл существует и похож на Netscape формат.
        Если формат неверный — просто игнорируем cookies (чтобы не падало скачивание).
        """
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
        # Если ffmpeg не установлен, нельзя склеивать отдельные потоки (video+audio).
        # В этом случае выбираем "одиночный" best MP4 (или best), чтобы скачивание не падало.
        if self.has_ffmpeg:
            fmt = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
            merge_format = 'mp4'
        else:
            fmt = 'best[ext=mp4]/best'
            merge_format = None

        opts = {
            # Формат: лучшее видео до 1080p + лучший аудио, или лучший комбинированный
            'format': fmt,
            **({'merge_output_format': merge_format} if merge_format else {}),
            'outtmpl': output_path,
            # Ограничение по размеру файла
            'max_filesize': MAX_FILE_SIZE,
            # Тихий режим
            'quiet': True,
            'no_warnings': True,
            # Обработка ошибок
            'ignoreerrors': False,
            # User-agent для обхода ограничений
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            # Дополнительные опции для совместимости
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                },
            },
            # Таймаут
            'socket_timeout': 30,
            # Retry
            'retries': 3,
            'fragment_retries': 3,
        }
        
        # Cookies используем только для YouTube (видео 18+). Для остальных платформ не трогаем cookies вообще.
        if self._is_youtube_url(url):
            cookiefile = self._get_youtube_cookiefile()
            if cookiefile:
                opts['cookiefile'] = cookiefile
        
        return opts
    
    async def download(self, url: str) -> DownloadResult:
        """
        Скачивает видео по URL.
        
        Args:
            url: URL видео
            
        Returns:
            DownloadResult с информацией о результате
        """
        if not self.is_supported_url(url):
            return DownloadResult(
                success=False,
                error=f"URL не поддерживается. Поддерживаемые платформы: YouTube, Instagram, TikTok, X/Twitter"
            )
        
        # Проверяем кэш
        cached = self.get_from_cache(url)
        if cached:
            return cached
        
        # Генерируем уникальное имя файла
        file_id = str(uuid.uuid4())[:8]
        output_path = str(self.download_dir / f"{file_id}.%(ext)s")
        
        ydl_opts = self._get_ydl_opts(output_path, url)
        
        try:
            # Запускаем скачивание в отдельном потоке (yt-dlp синхронный)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._download_sync(url, ydl_opts)
            )
            
            # Сохраняем в кэш при успехе
            if result.success:
                self.add_to_cache(url, result)
            
            return result
            
        except Exception as e:
            return DownloadResult(
                success=False,
                error=f"Ошибка при скачивании: {str(e)}"
            )
    
    def _download_sync(self, url: str, ydl_opts: dict) -> DownloadResult:
        """Синхронная функция скачивания для запуска в executor."""
        try:
            def attempt_download(opts: dict) -> DownloadResult:
                downloaded_file_path = None

                def progress_hook(d):
                    nonlocal downloaded_file_path
                    if d.get('status') == 'finished':
                        # Убеждаемся, что filename — строка, а не dict
                        filename = d.get('filename')
                        if isinstance(filename, str):
                            downloaded_file_path = filename

                # Добавляем хук для получения пути к файлу
                opts = opts.copy()
                opts['progress_hooks'] = [progress_hook]

                with yt_dlp.YoutubeDL(opts) as ydl:
                    # Скачиваем видео и получаем информацию
                    info = ydl.extract_info(url, download=True)

                    if info is None:
                        return DownloadResult(
                            success=False,
                            error="Не удалось получить информацию о видео"
                        )

                    # Логируем структуру для отладки
                    logger.debug("yt-dlp info type: %s, keys: %s", type(info).__name__, 
                                 list(info.keys()) if isinstance(info, dict) else "N/A")

                    # Проверяем, это плейлист или одно видео
                    # Для Instagram/X entries может быть вложенным
                    if 'entries' in info and info['entries']:
                        entries = info['entries']
                        logger.debug("entries type: %s, len: %s", type(entries).__name__,
                                     len(entries) if hasattr(entries, '__len__') else "N/A")
                        # Пропускаем None-записи (бывает у некоторых экстракторов)
                        for entry in entries:
                            if entry is not None and isinstance(entry, dict):
                                info = entry
                                break
                        else:
                            return DownloadResult(
                                success=False,
                                error="Не удалось получить видео из плейлиста"
                            )

                    title = info.get('title', 'Видео') if isinstance(info, dict) else 'Видео'
                    duration = info.get('duration') if isinstance(info, dict) else None

                    # Получаем путь к файлу из info или из хука
                    if not downloaded_file_path:
                        # Пробуем получить путь из prepare_filename
                        try:
                            prepared = ydl.prepare_filename(info)
                            if isinstance(prepared, str):
                                downloaded_file_path = prepared
                        except Exception:
                            pass
                        
                        # Меняем расширение на mp4 если было merge
                        if downloaded_file_path and isinstance(downloaded_file_path, str) and not os.path.exists(downloaded_file_path):
                            base = os.path.splitext(downloaded_file_path)[0]
                            for ext in ['mp4', 'webm', 'mkv', 'mov']:
                                test_path = f"{base}.{ext}"
                                if os.path.exists(test_path):
                                    downloaded_file_path = test_path
                                    break

                    # Проверяем, что путь — строка, перед использованием в os.path
                    if downloaded_file_path and isinstance(downloaded_file_path, str) and os.path.exists(downloaded_file_path):
                        # Проверяем размер скачанного файла
                        actual_size = os.path.getsize(downloaded_file_path)
                        if actual_size > MAX_FILE_SIZE:
                            os.remove(downloaded_file_path)
                            return DownloadResult(
                                success=False,
                                error=f"Скачанный файл слишком большой ({actual_size // (1024*1024)}MB). Максимум: {MAX_FILE_SIZE // (1024*1024)}MB"
                            )

                        return DownloadResult(
                            success=True,
                            file_path=downloaded_file_path,
                            title=title,
                            duration=duration
                        )
                    else:
                        # Fallback: ищем файл вручную
                        outtmpl = opts.get('outtmpl', '')
                        if isinstance(outtmpl, dict):
                            outtmpl = outtmpl.get('default', '')
                        if isinstance(outtmpl, str) and outtmpl:
                            file_id = os.path.basename(outtmpl).split('.')[0]
                        else:
                            file_id = None
                        
                        found_file = self._find_downloaded_file(file_id) if file_id else None
                        if found_file:
                            return DownloadResult(
                                success=True,
                                file_path=found_file,
                                title=title,
                                duration=duration
                            )
                        return DownloadResult(
                            success=False,
                            error="Файл не был скачан"
                        )

            return attempt_download(ydl_opts)
                    
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            error_msg_lower = error_msg.lower()

            # Если cookies файл некорректный — не падаем, а пробуем скачать без cookies.
            if ydl_opts.get('cookiefile') and (
                'does not look like a netscape format cookies file' in error_msg_lower
                or 'netscape format cookies file' in error_msg_lower
            ):
                bad_cookiefile = ydl_opts.get('cookiefile')
                logger.warning(
                    "yt-dlp отклонил cookies файл (%s). Повторяю скачивание без cookies.",
                    bad_cookiefile,
                )
                ydl_opts_no_cookies = ydl_opts.copy()
                ydl_opts_no_cookies.pop('cookiefile', None)
                try:
                    return attempt_download(ydl_opts_no_cookies)
                except yt_dlp.utils.DownloadError as e2:
                    e = e2
                    error_msg = str(e2)
                    error_msg_lower = error_msg.lower()

            if 'ffmpeg is not installed' in error_msg.lower():
                return DownloadResult(
                    success=False,
                    error=(
                        "Нужен FFmpeg для скачивания этого видео (требуется склейка аудио+видео).\n"
                        "Установите FFmpeg и добавьте его в PATH, затем попробуйте ещё раз."
                    ),
                )
            if 'Video unavailable' in error_msg:
                return DownloadResult(success=False, error="Видео недоступно")
            elif 'Private video' in error_msg:
                return DownloadResult(success=False, error="Это приватное видео")
            elif 'Sign in' in error_msg or 'login' in error_msg_lower:
                return DownloadResult(success=False, error="Требуется авторизация для просмотра этого видео")
            else:
                return DownloadResult(success=False, error=f"Ошибка скачивания: {error_msg[:200]}")
        except Exception as e:
            return DownloadResult(success=False, error=f"Неожиданная ошибка: {str(e)[:200]}")
    
    def _find_downloaded_file(self, file_id: str) -> Optional[str]:
        """Находит скачанный файл по ID."""
        # Ищем файлы с этим ID
        for ext in ['mp4', 'webm', 'mkv', 'mov', 'avi']:
            file_path = self.download_dir / f"{file_id}.{ext}"
            if file_path.exists():
                return str(file_path)
        
        # Если не нашли по точному имени, ищем по паттерну
        for file in self.download_dir.iterdir():
            if file.stem.startswith(file_id) and file.suffix in ['.mp4', '.webm', '.mkv', '.mov', '.avi']:
                return str(file)
        
        return None
    
    def clear_cache(self) -> int:
        """Очищает весь кэш и удаляет файлы. Возвращает количество удалённых файлов."""
        count = 0
        for url_hash, data in list(self.cache.items()):
            file_path = data.get('file_path')
            # Убеждаемся, что file_path — строка
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

