"""
Tests for VideoDownloader: cache operations, cookie validation, yt-dlp integration.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.downloader import DownloadResult, VideoDownloader, _is_instagram_cdn_host

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

    d.add_to_cache(
        url, DownloadResult(success=True, file_path=str(video), title="T", duration=60.0)
    )

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

    d.add_to_cache(
        base_url, DownloadResult(success=True, file_path=str(video), title="T", duration=10.0)
    )

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
        d.add_to_cache(
            f"https://youtube.com/watch?v={i}",
            DownloadResult(success=True, file_path=str(f), title="T", duration=1.0),
        )

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

    d.add_to_cache(
        url, DownloadResult(success=True, file_path=str(video), title="Cached", duration=5.0)
    )

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


# ── telegram file id cache ────────────────────────────────────────────────────


def test_set_and_get_telegram_file_id(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=abc"
    assert d.get_telegram_file_id(url) is None

    d.set_telegram_file_id(url, "file_id_123")
    assert d.get_telegram_file_id(url) == "file_id_123"


def test_set_telegram_file_id_noop_on_empty(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=abc"
    d.set_telegram_file_id(url, "")
    assert d.get_telegram_file_id(url) is None


def test_set_telegram_file_id_noop_when_same(tmp_path):
    """Setting the same file_id twice should not trigger a second cache save."""
    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=abc"
    d.set_telegram_file_id(url, "file_id_abc")
    with patch.object(d, "_save_cache") as mock_save:
        d.set_telegram_file_id(url, "file_id_abc")
    mock_save.assert_not_called()


def test_set_and_get_telegram_photo_file_id(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://www.instagram.com/p/ABC/"
    assert d.get_telegram_photo_file_id(url) is None

    d.set_telegram_photo_file_id(url, "photo_file_id_xyz")
    assert d.get_telegram_photo_file_id(url) == "photo_file_id_xyz"


def test_set_and_get_telegram_mp3_file_id(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=abc"
    assert d.get_telegram_mp3_file_id(url) is None

    d.set_telegram_mp3_file_id(url, "mp3_file_id_999")
    assert d.get_telegram_mp3_file_id(url) == "mp3_file_id_999"


# ── get_cached_media_type ─────────────────────────────────────────────────────


def test_get_cached_media_type_none_for_unknown(tmp_path):
    d = make_downloader(tmp_path)
    assert d.get_cached_media_type("https://youtube.com/watch?v=miss") is None


def test_get_cached_media_type_video(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=abc"
    video = fake_video(tmp_path)
    d.add_to_cache(url, DownloadResult(success=True, file_path=str(video), title="T"))
    assert d.get_cached_media_type(url) == "video"


def test_get_cached_media_type_photo(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://www.instagram.com/p/ABC/"
    photo = fake_video(tmp_path, "photo.jpg")
    d.add_to_cache(
        url,
        DownloadResult(
            success=True, file_path=str(photo), title="T", is_photo=True, photo_paths=[str(photo)]
        ),
    )
    assert d.get_cached_media_type(url) == "photo"


def test_get_cached_media_type_via_telegram_file_id(tmp_path):
    d = make_downloader(tmp_path)
    url = "https://youtube.com/watch?v=abc"
    d.set_telegram_file_id(url, "file_id_123")
    assert d.get_cached_media_type(url) == "video"


# ── cache: photo entries ──────────────────────────────────────────────────────


def test_add_and_get_photo_from_cache(tmp_path):
    d = make_downloader(tmp_path)
    photo = fake_video(tmp_path, "photo.jpg")
    url = "https://www.instagram.com/p/ABC/"

    d.add_to_cache(
        url,
        DownloadResult(
            success=True, file_path=str(photo), title="P", is_photo=True, photo_paths=[str(photo)]
        ),
    )

    result = d.get_from_cache(url)
    assert result is not None
    assert result.is_photo is True
    assert result.photo_paths == [str(photo)]
    assert result.from_cache is True


def test_photo_cache_invalidated_when_files_deleted(tmp_path):
    d = make_downloader(tmp_path)
    photo = fake_video(tmp_path, "photo.jpg")
    url = "https://www.instagram.com/p/ABC/"

    d.add_to_cache(
        url,
        DownloadResult(
            success=True, file_path=str(photo), title="P", is_photo=True, photo_paths=[str(photo)]
        ),
    )
    photo.unlink()

    assert d.get_from_cache(url) is None


# ── _parse_instagram_html ─────────────────────────────────────────────────────


def test_parse_instagram_html_extracts_og_image():
    html = '<meta property="og:image" content="https://cdn.example.com/photo.jpg">'
    result = VideoDownloader._parse_instagram_html(html)
    assert "https://cdn.example.com/photo.jpg" in result["image_urls"]
    assert result["has_video_marker"] is False


def test_parse_instagram_html_detects_video_url():
    html = '<meta property="og:video" content="https://cdn.example.com/video.mp4">'
    result = VideoDownloader._parse_instagram_html(html)
    assert result["video_url"] == "https://cdn.example.com/video.mp4"
    assert result["has_video_marker"] is True


def test_parse_instagram_html_detects_is_video_marker():
    html = '{"is_video": true}'
    result = VideoDownloader._parse_instagram_html(html)
    assert result["has_video_marker"] is True


def test_parse_instagram_html_extracts_title():
    html = '<meta property="og:title" content="My Post Title">'
    result = VideoDownloader._parse_instagram_html(html)
    assert result["title"] == "My Post Title"


def test_parse_instagram_html_deduplicates_images():
    html = (
        '<meta property="og:image" content="https://cdn.example.com/photo.jpg">'
        '<meta property="og:image" content="https://cdn.example.com/photo.jpg">'
    )
    result = VideoDownloader._parse_instagram_html(html)
    assert result["image_urls"].count("https://cdn.example.com/photo.jpg") == 1


def test_parse_instagram_html_empty():
    result = VideoDownloader._parse_instagram_html("")
    assert result["image_urls"] == []
    assert result["video_url"] is None
    assert result["has_video_marker"] is False
    assert result["title"] is None


@pytest.mark.parametrize(
    "host",
    [
        "scontent.cdninstagram.com",
        "scontent-iad3-1.cdninstagram.com",
        "scontent-iad3-1.fbcdn.net",
    ],
)
def test_is_instagram_cdn_host_accepts_real_cdn(host):
    assert _is_instagram_cdn_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        # Regression: "scontent" substring match would have accepted this.
        "scontent.evil.com",
        "scontent-iad3.malicious.org",
        "notcdninstagram.com",
        "example.com",
        "",
        None,
    ],
)
def test_is_instagram_cdn_host_rejects_look_alikes(host):
    assert _is_instagram_cdn_host(host) is False


def test_is_resized_variant_detects_stp_size_marker():
    assert VideoDownloader._is_resized_variant(
        "https://scontent.cdninstagram.com/v/t51/photo.jpg?stp=dst-jpg_e35_s1080x1080&oh=a"
    )
    assert VideoDownloader._is_resized_variant(
        "https://scontent.cdninstagram.com/v/t51/photo.jpg?stp=dst-jpg_e35_s640x640&oh=a"
    )
    assert not VideoDownloader._is_resized_variant(
        "https://scontent.cdninstagram.com/v/t51/photo.jpg?stp=dst-jpg_e35&oh=a"
    )
    assert not VideoDownloader._is_resized_variant(
        "https://scontent.cdninstagram.com/v/t51/photo.jpg"
    )


def test_parse_instagram_html_prefers_display_url_over_og_image():
    # Embed pages put the uncropped original in display_url and a square-cropped
    # preview in og:image — make sure display_url wins the ordering.
    html = (
        '<meta property="og:image" '
        'content="https://scontent.cdninstagram.com/v/t51/photo.jpg?stp=s1080x1080">'
        '{"display_url": "https://scontent.cdninstagram.com/v/t51/photo.jpg?stp=e35"}'
    )
    result = VideoDownloader._parse_instagram_html(html)
    assert result["image_urls"][0] == "https://scontent.cdninstagram.com/v/t51/photo.jpg?stp=e35"


# ── _extract_ig_shortcode ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/p/ABC123/", "ABC123"),
        ("https://www.instagram.com/reel/XYZ789/", "XYZ789"),
        ("https://www.instagram.com/tv/DEF456/", "DEF456"),
        ("https://www.instagram.com/reels/GHI012/", "GHI012"),
    ],
)
def test_extract_ig_shortcode(url, expected):
    assert VideoDownloader._extract_ig_shortcode(url) == expected


def test_extract_ig_shortcode_returns_none_for_profile():
    assert VideoDownloader._extract_ig_shortcode("https://www.instagram.com/user/") is None


# ── download: cookie retry logic ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_retries_without_cookies_on_invalid_cookiefile(tmp_path):
    """When yt-dlp rejects a cookies file, the download is retried without it."""
    from yt_dlp.utils import DownloadError

    d = make_downloader(tmp_path)
    video = fake_video(tmp_path, "video.mp4")
    url = "https://youtube.com/watch?v=cookietest"

    call_count = 0

    def fake_extract(download_url, download=True):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise DownloadError("does not look like a netscape format cookies file")
        return {"title": "Retried", "duration": 5.0}

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = fake_extract
        mock_ydl.prepare_filename.return_value = str(video)

        # Inject a cookiefile so the retry branch is reachable.
        original_get_ydl_opts = d._get_ydl_opts

        def patched_get_ydl_opts(output_path, u):
            opts = original_get_ydl_opts(output_path, u)
            opts["cookiefile"] = "/fake/cookies.txt"
            return opts

        d._get_ydl_opts = patched_get_ydl_opts
        result = await d.download(url)

    assert result.success
    assert call_count == 2


# ── download: photo frame extraction ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_extracts_photo_frame_for_short_instagram_post(tmp_path):
    """Instagram /p/ posts with duration<=1 should be returned as photos."""
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path, "downloaded.mp4")
    photo = fake_video(tmp_path, "downloaded_photo.jpg")
    url = "https://www.instagram.com/p/ABC123/"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "Photo Post", "duration": 0.0}
        mock_ydl.prepare_filename.return_value = str(video)

        with patch.object(
            d,
            "_extract_photo_frame",
            return_value=DownloadResult(
                success=True,
                file_path=str(photo),
                title="Photo Post",
                is_photo=True,
                photo_paths=[str(photo)],
            ),
        ):
            result = await d.download(url)

    assert result.success
    assert result.is_photo is True


@pytest.mark.asyncio
async def test_download_skips_photo_extraction_for_reel(tmp_path):
    """/reel/ URLs are never treated as photo candidates."""
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path, "reel.mp4")
    url = "https://www.instagram.com/reel/XYZ789/"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "Reel", "duration": 0.0}
        mock_ydl.prepare_filename.return_value = str(video)

        with patch.object(d, "_extract_photo_frame") as mock_extract:
            result = await d.download(url)

    mock_extract.assert_not_called()
    assert result.success
    assert result.is_photo is False


@pytest.mark.asyncio
async def test_download_skips_photo_extraction_when_duration_above_threshold(tmp_path):
    """/p/ posts with duration > 1s are treated as videos."""
    d = make_downloader(tmp_path)
    video = fake_video(tmp_path, "longvideo.mp4")
    url = "https://www.instagram.com/p/VIDEO123/"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "Long", "duration": 30.0}
        mock_ydl.prepare_filename.return_value = str(video)

        with patch.object(d, "_extract_photo_frame") as mock_extract:
            result = await d.download(url)

    mock_extract.assert_not_called()
    assert result.success
