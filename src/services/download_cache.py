"""Download cache implementation for the downloader."""

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

from src.services.download_quality import DEFAULT_QUALITY, validate_quality
from src.services.media import (
    _MEDIA_CACHE_VERSION,
    CarouselSlide,
    DownloadResult,
)
from src.services.url_utils import (
    get_url_hash,
)

logger = logging.getLogger(__name__)


def cache_locked(method):
    """Serialize cache mutations with cleanup running in a worker thread."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._cache_lock:
            return method(self, *args, **kwargs)

    return wrapped


class DownloadCacheMixin:
    @cache_locked
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

    @cache_locked
    def _save_cache(self) -> None:
        """Atomically replace the snapshot; preserve the previous file on failure."""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.download_dir,
                prefix=".cache-",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = f.name
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.cache_file)
        except OSError, TypeError, ValueError:
            logger.exception("Could not persist media cache")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

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

    @cache_locked
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

    @cache_locked
    def _current_media_entry(self, url: str, *, quality: str = DEFAULT_QUALITY) -> Optional[dict]:
        url_hash = self._get_url_hash(url, quality=quality)
        entry = self.cache.get(url_hash)
        if not entry:
            return None
        entry = self._invalidate_legacy_media(url_hash, entry)
        return entry if self._has_cached_media(entry) else None

    @cache_locked
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
            "thumbnail_path",
            "cover_path",
        ):
            entry.pop(key, None)

    @cache_locked
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

    @cache_locked
    def _get_url_hash(self, url: str, *, quality: str = DEFAULT_QUALITY) -> str:
        validate_quality(quality)
        key = get_url_hash(url)
        return key if quality == DEFAULT_QUALITY else f"{key}:{quality}"

    @cache_locked
    def get_from_cache(
        self, url: str, *, reserve: bool = False, quality: str = DEFAULT_QUALITY
    ) -> Optional[DownloadResult]:
        result = self._get_from_cache(url, quality=quality)
        if result is not None and reserve:
            self.reserve_result(result)
        return result

    @staticmethod
    def _result_entry(result: DownloadResult) -> dict:
        return {
            "file_path": result.file_path,
            "photo_paths": result.photo_paths,
            "thumbnail_path": result.thumbnail_path,
            "cover_path": result.cover_path,
            "carousel_slides": [asdict(slide) for slide in result.carousel_slides or []],
        }

    @cache_locked
    def reserve_result(self, result: DownloadResult) -> DownloadResult:
        """Keep files alive until the caller finishes sending them to Telegram."""
        if id(result) not in self._leases:
            paths = self._entry_file_paths(self._result_entry(result))
            self._leases[id(result)] = (result, paths)
            for path in paths:
                self._pins[path] = self._pins.get(path, 0) + 1
        return result

    @cache_locked
    def release_result(self, result: DownloadResult) -> None:
        lease = self._leases.pop(id(result), None)
        if lease is None:
            return
        for path in lease[1]:
            count = self._pins[path] - 1
            if count:
                self._pins[path] = count
            else:
                del self._pins[path]
                if path in self._pending_deletions:
                    self._pending_deletions.remove(path)
                    self._delete_entry_files({"file_path": path})

    @cache_locked
    def _get_from_cache(
        self, url: str, *, quality: str = DEFAULT_QUALITY
    ) -> Optional[DownloadResult]:
        """Проверяет наличие видео/фото в кэше."""
        url_hash = self._get_url_hash(url, quality=quality)
        if url_hash in self.cache:
            cached = self._invalidate_legacy_media(url_hash, self.cache[url_hash])
            if not self._has_cached_media(cached):
                if not cached.get("telegram_mp3_file_id"):
                    self.cache.pop(url_hash, None)
                    self._save_cache()
                return None
            file_path = cached.get("file_path")
            if any(not os.path.isfile(path) for path in self._entry_file_paths(cached)):
                self._prune_missing_local_media(url_hash, cached)
                return None
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
                    thumbnail_path=cached.get("thumbnail_path"),
                    cover_path=cached.get("cover_path"),
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
                if isinstance(url, str) and (url or item.get("local_path")):
                    slides.append(
                        CarouselSlide(
                            **{
                                key: value
                                for key, value in item.items()
                                if key in CarouselSlide.__dataclass_fields__
                            }
                        )
                    )
        return slides if len(slides) >= 2 else None

    @cache_locked
    def add_to_cache(
        self, url: str, result: DownloadResult, *, quality: str = DEFAULT_QUALITY
    ) -> None:
        """Добавляет результат в кэш."""
        if result.success and result.file_path:
            url_hash = self._get_url_hash(url, quality=quality)
            entry = self.cache.get(url_hash, {})
            if entry:
                entry = self._invalidate_legacy_media(url_hash, entry)
            new_entry: Dict[str, Any] = {
                "file_path": result.file_path,
                "title": result.title,
                "duration": result.duration,
                "width": result.width,
                "height": result.height,
                "thumbnail_path": result.thumbnail_path,
                "cover_path": result.cover_path,
                "media_cache_version": _MEDIA_CACHE_VERSION,
                "cached_at": time.time(),
            }
            if result.is_photo:
                new_entry["is_photo"] = True
                new_entry["media_type_confirmed"] = bool(result.media_type_confirmed)
                paths = result.photo_paths or [result.file_path]
                new_entry["photo_paths"] = list(paths)
            if result.carousel_slides:
                new_entry["carousel_slides"] = [asdict(slide) for slide in result.carousel_slides]
            telegram_media_key = "telegram_photo_file_id" if result.is_photo else "telegram_file_id"
            telegram_media_id = entry.get(telegram_media_key)
            if telegram_media_id:
                new_entry[telegram_media_key] = telegram_media_id
            telegram_mp3_file_id = entry.get("telegram_mp3_file_id")
            if telegram_mp3_file_id:
                new_entry["telegram_mp3_file_id"] = telegram_mp3_file_id
            old_paths = set(self._entry_file_paths(entry))
            new_paths = set(self._entry_file_paths(new_entry))
            self._pending_deletions.difference_update(new_paths)
            self._delete_entry_files({"photo_paths": list(old_paths - new_paths)})
            self.cache[url_hash] = new_entry
            self._save_cache()

    @cache_locked
    def discard_result_files(self, result: DownloadResult) -> int:
        """Delete uncached local files belonging to a completed result."""

        return self._delete_entry_files(self._result_entry(result))

    @cache_locked
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

    @cache_locked
    def get_telegram_file_id(self, url: str, *, quality: str = DEFAULT_QUALITY) -> Optional[str]:
        """Возвращает сохранённый Telegram file_id для URL, если есть."""
        entry = self._current_media_entry(url, quality=quality)
        if not entry:
            return None
        file_id = entry.get("telegram_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    @cache_locked
    def set_telegram_file_id(
        self, url: str, file_id: str, *, quality: str = DEFAULT_QUALITY
    ) -> None:
        """Сохраняет Telegram file_id для URL (используется для inline-mode)."""
        if not file_id:
            return
        url_hash = self._get_url_hash(url, quality=quality)
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

    @cache_locked
    def get_cached_media_type(self, url: str, *, quality: str = DEFAULT_QUALITY) -> Optional[str]:
        """
        Возвращает фактический тип медиа для URL, как он сохранён в кэше:
        "photo" или "video". None — если запись отсутствует или тип не
        определён. Используется inline-хендлером, чтобы не спутать свежие
        file_id с залежавшимися от предыдущего скачивания другого типа.
        """
        entry = self._current_media_entry(url, quality=quality)
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

    @cache_locked
    def get_cached_carousel_slides(
        self, url: str, *, quality: str = DEFAULT_QUALITY
    ) -> Optional[list]:
        """Return ordered cached rich-carousel slides, if the URL is a carousel."""

        entry = self._current_media_entry(url, quality=quality)
        if not entry:
            return None
        return self._deserialize_carousel_slides(entry.get("carousel_slides"))

    @cache_locked
    def get_telegram_photo_file_id(
        self, url: str, *, quality: str = DEFAULT_QUALITY
    ) -> Optional[str]:
        """Возвращает сохранённый Telegram photo file_id для URL, если есть."""
        entry = self._current_media_entry(url, quality=quality)
        if not entry:
            return None
        file_id = entry.get("telegram_photo_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    @cache_locked
    def set_telegram_photo_file_id(
        self, url: str, file_id: str, *, quality: str = DEFAULT_QUALITY
    ) -> None:
        """Сохраняет Telegram photo file_id для URL (используется для inline-mode)."""
        if not file_id:
            return
        url_hash = self._get_url_hash(url, quality=quality)
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

    @cache_locked
    def get_telegram_mp3_file_id(self, url: str) -> Optional[str]:
        """Возвращает сохранённый Telegram file_id для MP3-аудио, если есть."""
        url_hash = get_url_hash(url)
        entry = self.cache.get(url_hash)
        if not entry:
            return None
        file_id = entry.get("telegram_mp3_file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    @cache_locked
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

    @staticmethod
    def _entry_file_paths(data: dict) -> list[str]:
        """Локальные файлы записи кэша (видео и/или фото) без дублей."""
        paths: list[str] = []
        for key in ("file_path", "thumbnail_path", "cover_path"):
            path = data.get(key)
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
        photo_paths = data.get("photo_paths")
        if isinstance(photo_paths, list):
            for p in photo_paths:
                if isinstance(p, str) and p not in paths:
                    paths.append(p)
        for slide in data.get("carousel_slides") or []:
            if isinstance(slide, dict):
                for key in ("local_path", "thumbnail_path", "cover_path"):
                    path = slide.get(key)
                    if isinstance(path, str) and path and path not in paths:
                        paths.append(path)
        return paths

    @cache_locked
    def _delete_entry_files(self, data: dict) -> int:
        """Удаляет файлы записи кэша с диска. Возвращает число удалённых файлов."""
        count = 0
        for p in self._entry_file_paths(data):
            if self._pins.get(p, 0):
                self._pending_deletions.add(p)
                continue
            resolved = Path(p).resolve()
            if not resolved.is_relative_to(self.download_dir.resolve()):
                logger.warning("Refusing to delete media outside download directory")
                continue
            if os.path.exists(p):
                try:
                    os.remove(p)
                    count += 1
                    parent = resolved.parent
                    if (
                        parent.name.startswith("job-")
                        and parent.parent == self.download_dir.resolve()
                    ):
                        try:
                            parent.rmdir()
                        except OSError:
                            pass
                except OSError:
                    pass
        return count

    @cache_locked
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

    @cache_locked
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

    @cache_locked
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
            if any(self._pins.get(path) for path in self._entry_file_paths(data)):
                continue
            age = self._entry_age_seconds(data, now)
            if age is None or age < max_age_seconds:
                continue
            removed_files += self._delete_entry_files(data)
            del self.cache[url_hash]
            removed_entries += 1
        if removed_entries:
            self._save_cache()
        return removed_entries, removed_files

    @cache_locked
    def clear_cache(self) -> int:
        """Очищает весь кэш и удаляет файлы. Возвращает количество удалённых файлов."""
        count = 0
        for data in list(self.cache.values()):
            count += self._delete_entry_files(data)
        self.cache.clear()
        self._save_cache()
        return count
