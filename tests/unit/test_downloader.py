"""
Tests for VideoDownloader: cache operations, cookie validation, yt-dlp integration.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.downloader import DownloadResult, VideoDownloader


# ── helpers ──────────────────────────────────────────────────────────────────

def make_downloader(tmp_path: Path) -> VideoDownloader:
    return VideoDownloader(str(tmp_path))


def fake_video(tmp_path: Path, name: str = "video.mp4", size: int = 1024) -> Path:
    f = tmp_path / name
    f.write_bytes(b"x" * size)
    return f


# ── cache: get / add / invalidation ──────────────────────────────────────────

def test_get_from_cache_miss(tmp_path):
    d = make_downloader(tmp_path)
    assert d.get_from_cache("https://youtube.com/watch?v=miss") is None


def test_add_and_get_from_cache(tmp_path):
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path)
    url = "https://youtube.com/watch?v=abc"

    d.add_to_cache(url, DownloadResult(success=True, file_path=str(video), title="T", duration=60.0))

    result = d.get_from_cache(url)
    assert result is not None
    assert result.success
    assert result.from_cache
    assert result.title == "T"
    assert result.duration == 60.0


def test_cache_entry_normalizes_url(tmp_path):
    """URLs that differ only in tracking params share one cache entry."""
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path)

    base_url = "https://youtube.com/watch?v=abc"
    utm_url = "https://youtube.com/watch?v=abc&utm_source=telegram"

    d.add_to_cache(base_url, DownloadResult(success=True, file_path=str(video), title="T", duration=10.0))

    assert d.get_from_cache(utm_url) is not None


def test_get_from_cache_invalidates_deleted_file(tmp_path):
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path)
    url = "https://youtube.com/watch?v=abc"
    url_hash = d._get_url_hash(url)

    d.cache[url_hash] = {"file_path": str(video), "title": "T", "duration": 10.0}
    video.unlink()  # delete the file

    assert d.get_from_cache(url) is None
    assert url_hash not in d.cache  # cleaned up


def test_add_to_cache_does_not_store_failed_result(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=fail"
    d.add_to_cache(url, DownloadResult(success=False, error="oops"))
    assert d.get_from_cache(url) is None


def test_cache_persists_to_json(tmp_path):
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path)
    url = "https://youtube.com/watch?v=abc"

    d.add_to_cache(url, DownloadResult(success=True, file_path=str(video), title="T", duration=5.0))

    # New instance loads the saved JSON
    d2 = make_downloader(tmp_path)
    assert d2.get_from_cache(url) is not None


# ── cache: clear ──────────────────────────────────────────────────────────────

def test_clear_cache_removes_files_and_entries(tmp_path):
    d = make_downloader(tmp_path)
    files = [fake_video(tmp_path, f"v{i}.mp4") for i in range(3)]
    for i, f in enumerate(files):
        d.add_to_cache(f"https://youtube.com/watch?v={i}",
                       DownloadResult(success=True, file_path=str(f), title="T", duration=1.0))

    assert len(d.cache) == 3
    count = d.clear_cache()

    assert count == 3
    assert len(d.cache) == 0
    for f in files:
        assert not f.exists()


def test_clear_cache_returns_0_when_empty(tmp_path):
    d = make_downloader(tmp_path)
    assert d.clear_cache() == 0


# ── netscape cookie validation ────────────────────────────────────────────────

def test_looks_like_netscape_cookies_with_header(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_downloader(tmp_path)
    assert d._looks_like_netscape_cookies_file(str(f)) is True


def test_looks_like_netscape_cookies_with_tab_row(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(".youtube.com\tTRUE\t/\tFALSE\t0\tSID\tvalue\n")
    d = make_downloader(tmp_path)
    assert d._looks_like_netscape_cookies_file(str(f)) is True


def test_looks_like_netscape_cookies_rejects_json(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text('{"not": "netscape"}')
    d = make_downloader(tmp_path)
    assert d._looks_like_netscape_cookies_file(str(f)) is False


def test_looks_like_netscape_cookies_missing_file(tmp_path):
    d = make_downloader(tmp_path)
    assert d._looks_like_netscape_cookies_file(str(tmp_path / "nonexistent.txt")) is False


# ── ydl opts ──────────────────────────────────────────────────────────────────

def test_get_ydl_opts_no_cookiefile_for_non_youtube(tmp_path):
    d = make_downloader(tmp_path)
    opts = d._get_ydl_opts("out.%(ext)s", "https://www.instagram.com/reel/abc/")
    assert "cookiefile" not in opts


def test_get_ydl_opts_format_without_ffmpeg(tmp_path):
    d = make_downloader(tmp_path)
    d.has_ffmpeg = False
    opts = d._get_ydl_opts("out.%(ext)s", "https://youtube.com/watch?v=abc")
    assert "best[ext=mp4]/best" in opts["format"]
    assert "merge_output_format" not in opts


def test_get_ydl_opts_format_with_ffmpeg(tmp_path):
    d = make_downloader(tmp_path)
    d.has_ffmpeg = True
    opts = d._get_ydl_opts("out.%(ext)s", "https://youtube.com/watch?v=abc")
    assert "bestvideo" in opts["format"]
    assert opts.get("merge_output_format") == "mp4"


# ── download: unsupported URL ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_rejects_unsupported_url(tmp_path):
    d = make_downloader(tmp_path)
    result = await d.download("https://example.com/video")
    assert not result.success
    assert "не поддерживается" in result.error.lower() or "поддерживаемые" in result.error.lower()


# ── download: cache hit ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_returns_cached_result(tmp_path):
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path)
    url = "https://youtube.com/watch?v=cached"

    d.add_to_cache(url, DownloadResult(success=True, file_path=str(video), title="Cached", duration=5.0))

    result = await d.download(url)
    assert result.success
    assert result.from_cache
    assert result.title == "Cached"


# ── download: successful yt-dlp call ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_success_via_yt_dlp(tmp_path):
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path, "downloaded.mp4")
    url = "https://youtube.com/watch?v=realvideo"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "My Video", "duration": 120.0}
        mock_ydl.prepare_filename.return_value = str(video)

        result = await d.download(url)

    assert result.success
    assert result.title == "My Video"
    assert result.duration == 120.0
    assert not result.from_cache


@pytest.mark.asyncio
async def test_download_caches_result_after_success(tmp_path):
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path, "downloaded.mp4")
    url = "https://youtube.com/watch?v=newvideo"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "V", "duration": 10.0}
        mock_ydl.prepare_filename.return_value = str(video)

        await d.download(url)

    # Second call should be a cache hit without calling yt-dlp again
    result = await d.download(url)
    assert result.from_cache


# ── download: yt-dlp error handling ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_handles_unavailable_video(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=unavailable"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("Video unavailable")

        result = await d.download(url)

    assert not result.success
    assert "недоступно" in result.error


@pytest.mark.asyncio
async def test_download_handles_private_video(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=private"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("Private video")

        result = await d.download(url)

    assert not result.success
    assert "приватное" in result.error


@pytest.mark.asyncio
async def test_download_handles_file_too_large(tmp_path):
    from src.config import MAX_FILE_SIZE

    d = make_downloader(tmp_path)
    # Create file larger than MAX_FILE_SIZE
    big_video = fake_video(tmp_path, "big.mp4", size=MAX_FILE_SIZE + 1)
    url = "https://youtube.com/watch?v=toobig"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "Big", "duration": 600.0}
        mock_ydl.prepare_filename.return_value = str(big_video)

        result = await d.download(url)

    assert not result.success
    assert "большой" in result.error or "MB" in result.error
    assert not big_video.exists()  # file should be deleted
