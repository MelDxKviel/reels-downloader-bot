"""Tests for the YouTube Shorts search service."""

from unittest.mock import MagicMock, patch

import pytest

from src.services import youtube_search as ys


def _make_entry(video_id, duration=15, title="t", thumbnail=None, channel=None):
    entry = {
        "id": video_id,
        "title": title,
        "duration": duration,
    }
    if thumbnail is not None:
        entry["thumbnail"] = thumbnail
    if channel is not None:
        entry["channel"] = channel
    return entry


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty():
    assert await ys.search_shorts("") == []
    assert await ys.search_shorts(" ") == []
    assert await ys.search_shorts("a") == []


def test_filters_long_videos():
    info = {
        "entries": [
            _make_entry("aaa", duration=15),
            _make_entry("bbb", duration=120),  # too long
            _make_entry("ccc", duration=30),
        ]
    }
    fake_ydl = MagicMock()
    fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
    fake_ydl.__exit__ = MagicMock(return_value=False)
    fake_ydl.extract_info.return_value = info
    with patch.object(ys.yt_dlp, "YoutubeDL", return_value=fake_ydl):
        results = ys._search_shorts_sync("query", count=3)
    ids = [r.video_id for r in results]
    assert ids == ["aaa", "ccc"]


def test_falls_back_to_thumbnail_url():
    info = {"entries": [_make_entry("vid1", duration=10)]}
    fake_ydl = MagicMock()
    fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
    fake_ydl.__exit__ = MagicMock(return_value=False)
    fake_ydl.extract_info.return_value = info
    with patch.object(ys.yt_dlp, "YoutubeDL", return_value=fake_ydl):
        results = ys._search_shorts_sync("query", count=3)
    assert results[0].thumbnail.startswith("https://i.ytimg.com/vi/vid1/")


def test_search_handles_yt_dlp_exception():
    fake_ydl = MagicMock()
    fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
    fake_ydl.__exit__ = MagicMock(return_value=False)
    fake_ydl.extract_info.side_effect = RuntimeError("boom")
    with patch.object(ys.yt_dlp, "YoutubeDL", return_value=fake_ydl):
        assert ys._search_shorts_sync("query", count=3) == []


def test_build_shorts_url():
    assert ys.build_shorts_url("xyz") == "https://www.youtube.com/shorts/xyz"


def test_get_cached_video_file_id_delegates_to_downloader():
    with patch.object(ys.downloader, "get_telegram_file_id", return_value="fid") as m:
        assert ys.get_cached_video_file_id("abc") == "fid"
    m.assert_called_with("https://www.youtube.com/shorts/abc")
