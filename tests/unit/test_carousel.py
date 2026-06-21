"""
Tests for the native Instagram → Telegram rich carousel (Bot API 10.1
``<tg-slideshow>``): slide-URL extraction, caching round-trip, and HTML builder.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.bot.handlers.download import _build_slideshow_html
from src.services.downloader import CarouselSlide, DownloadResult, VideoDownloader
from src.services.url_utils import is_twitter_url

# ── _try_instagram_photo: carousel slide URLs ─────────────────────────────────


def _cdn(name: str) -> str:
    return f"https://scontent.cdninstagram.com/v/t51/{name}.jpg?oh=a&oe=b"


def test_try_instagram_photo_collects_carousel_slides(tmp_path: Path):
    """A multi-photo post yields ordered carousel_slides with the source URLs."""
    d = VideoDownloader(str(tmp_path))
    urls = [_cdn("1"), _cdn("2"), _cdn("3")]
    meta = {"image_urls": urls, "video_url": None, "has_video": False, "title": "My carousel"}

    def fake_download(image_url: str, output_base: str):
        path = f"{output_base}.jpg"
        with open(path, "wb") as f:
            f.write(b"x" * 2048)
        return path

    with (
        patch.object(d, "_fetch_instagram_media_info", return_value=meta),
        patch.object(d, "_download_image_sync", side_effect=fake_download),
    ):
        result = d._try_instagram_photo("https://www.instagram.com/p/ABC/")

    assert result is not None
    assert result.is_photo is True
    assert len(result.photo_paths) == 3
    assert result.carousel_slides is not None
    assert [s.url for s in result.carousel_slides] == urls
    assert all(s.is_video is False for s in result.carousel_slides)


def test_try_instagram_photo_single_photo_has_no_carousel(tmp_path: Path):
    """A single-photo post must not produce carousel_slides (needs >= 2)."""
    d = VideoDownloader(str(tmp_path))
    meta = {"image_urls": [_cdn("only")], "video_url": None, "has_video": False, "title": "Solo"}

    def fake_download(image_url: str, output_base: str):
        path = f"{output_base}.jpg"
        with open(path, "wb") as f:
            f.write(b"x" * 2048)
        return path

    with (
        patch.object(d, "_fetch_instagram_media_info", return_value=meta),
        patch.object(d, "_download_image_sync", side_effect=fake_download),
    ):
        result = d._try_instagram_photo("https://www.instagram.com/p/ABC/")

    assert result is not None
    assert result.is_photo is True
    assert result.photo_paths == [result.file_path]
    assert result.carousel_slides is None


# ── cache round-trip of carousel slides ───────────────────────────────────────


def test_carousel_slides_survive_cache_roundtrip(tmp_path: Path):
    d = VideoDownloader(str(tmp_path))
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    p1.write_bytes(b"x" * 2048)
    p2.write_bytes(b"x" * 2048)
    url = "https://www.instagram.com/p/XYZ/"
    slides = [CarouselSlide(url=_cdn("1")), CarouselSlide(url=_cdn("2"))]

    d.add_to_cache(
        url,
        DownloadResult(
            success=True,
            file_path=str(p1),
            title="T",
            is_photo=True,
            photo_paths=[str(p1), str(p2)],
            carousel_slides=slides,
        ),
    )

    got = d.get_from_cache(url)
    assert got is not None
    assert got.carousel_slides is not None
    assert [s.url for s in got.carousel_slides] == [_cdn("1"), _cdn("2")]

    # A fresh instance must reload the slides from the JSON cache file.
    reloaded = VideoDownloader(str(tmp_path))
    got2 = reloaded.get_from_cache(url)
    assert got2 is not None
    assert got2.carousel_slides is not None
    assert len(got2.carousel_slides) == 2


def test_deserialize_carousel_slides_needs_two():
    assert VideoDownloader._deserialize_carousel_slides(None) is None
    assert VideoDownloader._deserialize_carousel_slides([{"url": "u1"}]) is None
    slides = VideoDownloader._deserialize_carousel_slides(
        [{"url": "u1"}, {"url": "u2", "is_video": True}]
    )
    assert slides is not None
    assert [s.url for s in slides] == ["u1", "u2"]
    assert slides[0].is_video is False
    assert slides[1].is_video is True


# ── <tg-slideshow> HTML builder ───────────────────────────────────────────────


def test_build_slideshow_html_escapes_urls_and_caption():
    slides = [
        CarouselSlide(url="https://cdn/1.jpg?a=1&b=2"),
        CarouselSlide(url="https://cdn/2.jpg"),
    ]
    out = _build_slideshow_html(slides, "Cap & <tag>")
    assert out.startswith("<tg-slideshow>")
    assert out.endswith("</tg-slideshow>")
    # & inside the URL query must be escaped to &amp; for valid HTML attributes.
    assert '<img src="https://cdn/1.jpg?a=1&amp;b=2"/>' in out
    assert '<img src="https://cdn/2.jpg"/>' in out
    assert "<figcaption>Cap &amp; &lt;tag&gt;</figcaption>" in out


def test_build_slideshow_html_video_slide_and_no_caption():
    slides = [
        CarouselSlide(url="https://cdn/1.jpg"),
        CarouselSlide(url="https://cdn/v.mp4", is_video=True),
    ]
    out = _build_slideshow_html(slides)
    assert '<img src="https://cdn/1.jpg"/>' in out
    assert '<video src="https://cdn/v.mp4"/>' in out
    assert "<figcaption>" not in out


# ── /p/ routing: cookie-gated fall-through to yt-dlp ──────────────────────────


@pytest.mark.asyncio
async def test_pp_single_photo_without_cookies_does_not_call_ytdlp(tmp_path: Path):
    """No IG cookies → a single scraped photo is returned as-is (no yt-dlp, no regression)."""
    d = VideoDownloader(str(tmp_path))
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    single = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)], title="P"
    )
    with (
        patch.object(d, "_try_instagram_photo", return_value=single),
        patch.object(d, "_get_instagram_cookiefile", return_value=None),
        patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls,
    ):
        res = await d.download("https://www.instagram.com/p/ABC/")

    mock_cls.assert_not_called()
    assert res.is_photo is True
    assert res.carousel_slides is None


@pytest.mark.asyncio
async def test_pp_single_photo_with_cookies_recovers_carousel_via_ytdlp(tmp_path: Path):
    """With cookies, a single-image scrape falls through to yt-dlp, which enumerates
    the full carousel the public scrape hid."""
    d = VideoDownloader(str(tmp_path))
    d.has_ffmpeg = False  # keep the 0s-frame extraction out of this test
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    first = tmp_path / "first.jpg"
    first.write_bytes(b"y" * 2048)
    single = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)], title="P"
    )
    info = {
        "title": "Hidden carousel",
        "entries": [
            {
                "ext": "jpg",
                "vcodec": "none",
                "formats": [{"url": "https://cdn/1.jpg", "width": 1080}],
            },
            {
                "ext": "jpg",
                "vcodec": "none",
                "formats": [{"url": "https://cdn/2.jpg", "width": 1080}],
            },
        ],
    }
    with (
        patch.object(d, "_try_instagram_photo", return_value=single),
        patch.object(d, "_get_instagram_cookiefile", return_value="/fake/cookies.txt"),
        patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = info
        mock_ydl.prepare_filename.return_value = str(first)
        res = await d.download("https://www.instagram.com/p/ABC/")

    assert res.success
    assert res.carousel_slides is not None
    assert [s.url for s in res.carousel_slides] == ["https://cdn/1.jpg", "https://cdn/2.jpg"]


@pytest.mark.asyncio
async def test_pp_single_photo_with_cookies_falls_back_when_ytdlp_fails(tmp_path: Path):
    """With cookies but a yt-dlp 403, the scraped single photo is still returned."""
    from yt_dlp.utils import DownloadError

    d = VideoDownloader(str(tmp_path))
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    single = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)], title="P"
    )
    with (
        patch.object(d, "_try_instagram_photo", return_value=single),
        patch.object(d, "_get_instagram_cookiefile", return_value="/fake/cookies.txt"),
        patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("HTTP Error 403: Forbidden")
        res = await d.download("https://www.instagram.com/p/ABC/")

    assert res.success
    assert res.is_photo is True
    assert res.file_path == str(photo)
    # A transient failure must NOT be cached, or it would pin the URL to one photo
    # and defeat carousel recovery on a later request with working cookies.
    assert d.get_from_cache("https://www.instagram.com/p/ABC/") is None


@pytest.mark.asyncio
async def test_pp_single_photo_with_cookies_prefers_scraped_photo_over_bare_video(tmp_path: Path):
    """A genuine single photo that yt-dlp returns as a 0s video (no FFmpeg to convert)
    must yield the scraped photo, not the bare video — and stay uncached (retryable)."""
    d = VideoDownloader(str(tmp_path))
    d.has_ffmpeg = False  # frame extraction can't rescue the 0s video
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    zero_video = tmp_path / "zero.mp4"
    zero_video.write_bytes(b"y" * 2048)
    single = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)], title="P"
    )
    url = "https://www.instagram.com/p/SOLO/"
    with (
        patch.object(d, "_try_instagram_photo", return_value=single),
        patch.object(d, "_get_instagram_cookiefile", return_value="/fake/cookies.txt"),
        patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "Solo", "duration": 0.0}
        mock_ydl.prepare_filename.return_value = str(zero_video)
        res = await d.download(url)

    assert res.is_photo is True
    assert res.file_path == str(photo)  # scraped photo, not the bare 0s video
    assert d.get_from_cache(url) is None


# ── yt-dlp entry → CarouselSlide (mixed photo/video carousels) ─────────────────


def test_entry_to_slide_image():
    entry = {
        "ext": "jpg",
        "vcodec": "none",
        "formats": [
            {"url": "https://cdn/p.jpg", "vcodec": "none", "acodec": "none", "width": 1080}
        ],
    }
    slide = VideoDownloader._entry_to_slide(entry)
    assert slide is not None
    assert slide.is_video is False
    assert slide.url == "https://cdn/p.jpg"


def test_entry_to_slide_video_prefers_progressive_format():
    entry = {
        "ext": "mp4",
        "duration": 7,
        "vcodec": "h264",
        "acodec": "aac",
        "formats": [
            # video-only (no audio) — should be skipped in favour of progressive
            {
                "url": "https://cdn/vonly_1080.mp4",
                "vcodec": "h264",
                "acodec": "none",
                "height": 1080,
            },
            # progressive (audio+video) — preferred even at lower height
            {"url": "https://cdn/prog_720.mp4", "vcodec": "h264", "acodec": "aac", "height": 720},
        ],
    }
    slide = VideoDownloader._entry_to_slide(entry)
    assert slide is not None
    assert slide.is_video is True
    assert slide.url == "https://cdn/prog_720.mp4"


def test_entry_to_slide_returns_none_without_url():
    assert VideoDownloader._entry_to_slide({"ext": "mp4", "duration": 3, "formats": []}) is None
    assert VideoDownloader._entry_to_slide("not-a-dict") is None


def test_best_entry_media_url_image_picks_largest():
    entry = {
        "formats": [
            {"url": "https://cdn/small.jpg", "vcodec": "none", "width": 320},
            {"url": "https://cdn/large.jpg", "vcodec": "none", "width": 1440},
        ]
    }
    assert VideoDownloader._best_entry_media_url(entry, want_video=False) == "https://cdn/large.jpg"


def test_extract_photo_frame_preserves_carousel_slides(tmp_path: Path):
    """Frame extraction (0s-video → photo) must keep carousel_slides so the rich
    carousel is still attempted and the local fallback is a valid photo."""
    d = VideoDownloader(str(tmp_path))
    d.has_ffmpeg = True
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 2048)
    slides = [
        CarouselSlide(url="https://cdn/1.jpg"),
        CarouselSlide(url="https://cdn/2.mp4", is_video=True),
    ]
    incoming = DownloadResult(
        success=True,
        file_path=str(video),
        title="T",
        duration=0.0,
        is_photo=False,
        carousel_slides=slides,
    )
    frame_path = str(tmp_path / "v_photo.jpg")

    def fake_ffmpeg(cmd, capture_output=True, timeout=30):
        with open(frame_path, "wb") as f:
            f.write(b"y" * 4096)
        proc = MagicMock()
        proc.returncode = 0
        return proc

    with patch("src.services.downloader.subprocess.run", side_effect=fake_ffmpeg):
        result = d._extract_photo_frame(incoming)

    assert result is not None
    assert result.is_photo is True
    assert result.photo_paths == [frame_path]
    assert result.carousel_slides == slides


@pytest.mark.asyncio
async def test_download_instagram_carousel_harvests_video_slides(tmp_path: Path):
    """A mixed Instagram carousel yields ordered carousel_slides (video + photo)."""
    d = VideoDownloader(str(tmp_path))
    url = "https://www.instagram.com/p/MIXED/"
    first_file = tmp_path / "first.mp4"
    first_file.write_bytes(b"x" * 2048)

    info = {
        "title": "My mixed carousel",
        "entries": [
            {
                "ext": "mp4",
                "duration": 8,
                "vcodec": "h264",
                "acodec": "aac",
                "formats": [
                    {
                        "url": "https://cdn/v1.mp4",
                        "vcodec": "h264",
                        "acodec": "aac",
                        "height": 720,
                    }
                ],
            },
            {
                "ext": "jpg",
                "vcodec": "none",
                "formats": [{"url": "https://cdn/p2.jpg", "vcodec": "none", "width": 1080}],
            },
        ],
    }

    with patch.object(d, "_try_instagram_photo", return_value=None):
        with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
            mock_ydl = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = info
            mock_ydl.prepare_filename.return_value = str(first_file)
            result = await d.download(url)

    assert result.success
    assert result.carousel_slides is not None
    assert [(s.url, s.is_video) for s in result.carousel_slides] == [
        ("https://cdn/v1.mp4", True),
        ("https://cdn/p2.jpg", False),
    ]
    # The post caption (playlist title), not the first slide's, is kept.
    assert result.title == "My mixed carousel"
    # A concrete local file remains as the album/video fallback.
    assert result.file_path == str(first_file)


# ── X/Twitter carousels ───────────────────────────────────────────────────────


def test_is_twitter_url():
    assert is_twitter_url("https://x.com/u/status/1")
    assert is_twitter_url("https://twitter.com/u/status/1")
    assert is_twitter_url("https://mobile.twitter.com/u/status/1")
    assert not is_twitter_url("https://www.instagram.com/p/A/")
    assert not is_twitter_url("https://example.com/")


@pytest.mark.asyncio
async def test_download_twitter_carousel_harvests_slides(tmp_path: Path):
    """A multi-media X/Twitter post yields a native carousel (carousel_slides) built
    from the public twimg URLs — no cookies required."""
    d = VideoDownloader(str(tmp_path))
    d.has_ffmpeg = False
    url = "https://x.com/user/status/123456"
    first = tmp_path / "first.jpg"
    first.write_bytes(b"x" * 2048)
    info = {
        "title": "A tweet",
        "entries": [
            {
                "ext": "jpg",
                "vcodec": "none",
                "formats": [{"url": "https://pbs.twimg.com/media/1.jpg", "width": 1200}],
            },
            {
                "ext": "mp4",
                "duration": 6,
                "vcodec": "h264",
                "acodec": "aac",
                "formats": [
                    {
                        "url": "https://video.twimg.com/2.mp4",
                        "vcodec": "h264",
                        "acodec": "aac",
                        "height": 720,
                    }
                ],
            },
        ],
    }
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = info
        mock_ydl.prepare_filename.return_value = str(first)
        res = await d.download(url)

    assert res.success
    assert res.carousel_slides is not None
    assert [(s.url, s.is_video) for s in res.carousel_slides] == [
        ("https://pbs.twimg.com/media/1.jpg", False),
        ("https://video.twimg.com/2.mp4", True),
    ]
