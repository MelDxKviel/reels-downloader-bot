"""
Поиск YouTube Shorts по тексту.

Используется inline-режимом, когда пользователь вводит обычный текст
(не ссылку): yt-dlp с ``ytsearch`` запрашивает первые N результатов в режиме
``extract_flat`` (без скачивания), мы фильтруем их по длительности до 60 секунд
и возвращаем 3 наиболее свежих кандидата вместе с превью.

Функция блокирующая, поэтому обёрнута в ``run_in_executor`` для использования
из async-кода.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

import yt_dlp

from src.services.downloader import downloader

logger = logging.getLogger(__name__)

# Telegram официально объявляет Shorts как видео до 60 секунд. Чуть-чуть запаса
# на случай, если yt-dlp вернёт duration округлённо вверх (61).
SHORTS_MAX_DURATION_SECONDS = 65

# yt-dlp по поисковому URL ``ytsearchN:`` отдаёт максимум N entries. Берём
# заметно больше, чем выводим, чтобы после фильтрации по длительности всё-таки
# набрать нужное количество шортсов. Telegram inline-мод принимает до 50
# результатов; нам столько не нужно, но потолок выдачи > запрашиваемого, чтобы
# фильтр по длительности не оставил пустые слоты.
SEARCH_FETCH_MULTIPLIER = 4
MAX_SEARCH_FETCH = 50
MIN_QUERY_LENGTH = 2


@dataclass
class ShortsSearchResult:
    """Описание одного найденного шортса."""

    video_id: str
    title: str
    url: str
    thumbnail: Optional[str]
    duration: Optional[float]
    channel: Optional[str]


def _fallback_thumbnail(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _search_shorts_sync(query: str, count: int) -> List[ShortsSearchResult]:
    fetch = min(MAX_SEARCH_FETCH, max(count, count * SEARCH_FETCH_MULTIPLIER))
    search_url = f"ytsearch{fetch}:{query} #shorts"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": 15,
        # ytsearch по умолчанию обращается к YouTube через web-клиент. Здесь
        # cookies необязательны: поиск публичный.
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
    except Exception as e:
        logger.warning("YouTube Shorts search failed for %r: %s", query, e)
        return []

    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return []

    results: List[ShortsSearchResult] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        video_id = entry.get("id")
        if not video_id or not isinstance(video_id, str):
            continue

        duration = entry.get("duration")
        if isinstance(duration, (int, float)):
            if duration > SHORTS_MAX_DURATION_SECONDS:
                continue
        else:
            # extract_flat иногда не возвращает duration. Не отсекаем такие
            # видео жёстко, ведь #shorts фильтр уже отфильтровал поиск, но
            # ставим в конец очереди — лучше шортсы с известной длиной.
            duration = None

        title = entry.get("title") or "YouTube Shorts"
        thumbnail = entry.get("thumbnail")
        if not isinstance(thumbnail, str) or not thumbnail:
            # entry.thumbnails — список dict с key url/width/height.
            thumbs = entry.get("thumbnails")
            if isinstance(thumbs, list):
                for t in thumbs:
                    if isinstance(t, dict) and isinstance(t.get("url"), str):
                        thumbnail = t["url"]
                        break
            if not thumbnail:
                thumbnail = _fallback_thumbnail(video_id)

        channel = entry.get("channel") or entry.get("uploader") or None

        results.append(
            ShortsSearchResult(
                video_id=video_id,
                title=title,
                url=f"https://www.youtube.com/shorts/{video_id}",
                thumbnail=thumbnail,
                duration=float(duration) if duration is not None else None,
                channel=channel if isinstance(channel, str) else None,
            )
        )

        if len(results) >= count:
            break

    return results


async def search_shorts(query: str, count: int = 3) -> List[ShortsSearchResult]:
    """Возвращает до ``count`` шортсов по поисковому запросу."""
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return []

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _search_shorts_sync(query, count))


def build_shorts_url(video_id: str) -> str:
    """Собирает URL шортса по его video_id."""
    return f"https://www.youtube.com/shorts/{video_id}"


def get_cached_video_file_id(video_id: str) -> Optional[str]:
    """Если для этого шортса уже есть Telegram file_id — возвращает его."""
    return downloader.get_telegram_file_id(build_shorts_url(video_id))
