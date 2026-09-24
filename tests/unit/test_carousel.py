"""
Tests for the native Instagram → Telegram rich carousel (Bot API 10.2
``<tg-slideshow>``): slide-URL extraction, caching round-trip, and HTML builder.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiogram.types import FSInputFile

from src.bot.rich_carousel import (
    build_slideshow_html as _build_slideshow_html,
)
from src.bot.rich_carousel import (
    rich_carousel_variants,
)
from src.services.downloader import CarouselSlide, DownloadResult, VideoDownloader
from src.services.url_utils import is_twitter_url


async def _in_process_worker(downloader, url, allow_carousel):
    return await downloader._download_source(url, allow_carousel)


# ── _try_instagram_photo: carousel slide URLs ─────────────────────────────────


def _cdn(name: str) -> str:
    return f"https://scontent.cdninstagram.com/v/t51/{name}.jpg?oh=a&oe=b"


def test_try_instagram_photo_collects_carousel_slides(tmp_path: Path):
    """A multi-photo post yields ordered carousel_slides with the source URLs."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    urls = [_cdn("1"), _cdn("2"), _cdn("3")]
    meta = {
        "image_urls": urls,
        "video_url": None,
        "has_video": False,
        "media_kind": "photo",
        "title": "My carousel",
    }

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
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    meta = {
        "image_urls": [_cdn("only")],
        "video_url": None,
        "has_video": False,
        "media_kind": "photo",
        "title": "Solo",
    }

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
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
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
    assert d.get_cached_carousel_slides(url) is not None

    # A fresh instance must reload the slides from the JSON cache file.
    reloaded = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    got2 = reloaded.get_from_cache(url)
    assert got2 is not None
    assert got2.carousel_slides is not None
    assert len(got2.carousel_slides) == 2


def test_cached_carousel_is_atomic_when_one_local_slide_disappears(tmp_path: Path):
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    paths = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    for path in paths:
        path.write_bytes(b"x" * 2048)
    url = "https://www.instagram.com/p/ATOMIC/"
    d.add_to_cache(
        url,
        DownloadResult(
            success=True,
            file_path=str(paths[0]),
            is_photo=True,
            photo_paths=[str(path) for path in paths],
            carousel_slides=[CarouselSlide(_cdn("1")), CarouselSlide(_cdn("2"))],
        ),
    )
    d.set_telegram_photo_file_id(url, "first-only")
    d.set_telegram_mp3_file_id(url, "mp3-still-valid")
    paths[1].unlink()

    assert d.get_from_cache(url) is None
    assert not paths[0].exists()
    assert d.get_cached_carousel_slides(url) is None
    assert d.get_telegram_photo_file_id(url) is None
    assert d.get_telegram_mp3_file_id(url) == "mp3-still-valid"

    restarted = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    assert restarted.get_from_cache(url) is None
    assert restarted.get_telegram_mp3_file_id(url) == "mp3-still-valid"


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


def test_rich_carousel_upload_variant_attaches_all_local_photos(tmp_path: Path):
    paths = [tmp_path / "1.jpg", tmp_path / "2.jpg"]
    for path in paths:
        path.write_bytes(b"x")
    slides = [CarouselSlide(_cdn("1")), CarouselSlide(_cdn("2"))]

    variants = rich_carousel_variants(
        slides,
        "Caption",
        media_paths=[str(path) for path in paths],
    )

    assert len(variants) == 2  # multipart first, public Instagram URLs second
    uploaded = variants[0]
    assert uploaded.media is not None
    assert [item.id for item in uploaded.media] == ["slide_1", "slide_2"]
    assert all(isinstance(item.media.media, FSInputFile) for item in uploaded.media)
    assert 'src="tg://photo?id=slide_1"' in uploaded.html
    assert 'src="tg://photo?id=slide_2"' in uploaded.html


def test_rich_carousel_file_id_variant_is_inline_safe():
    slides = [CarouselSlide(_cdn("1")), CarouselSlide(_cdn("2"))]

    variants = rich_carousel_variants(slides, media_file_ids=["file_1", "file_2"])

    attached = variants[0]
    assert attached.media is not None
    assert [item.media.media for item in attached.media] == ["file_1", "file_2"]


# ── /p/ routing: ambiguous covers are verified by yt-dlp ─────────────────────


@pytest.mark.asyncio
async def test_pp_target_mirror_photo_carousel_skips_ytdlp(tmp_path: Path):
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    url = "https://www.instagram.com/p/MIRRORONLY/"
    mirror_urls = [_cdn("mirror-1"), _cdn("mirror-2")]
    mirror_html = (
        "".join(f'<meta property="og:image" content="{image_url}">' for image_url in mirror_urls)
        + '{"media_type":1}'
    )

    def fake_get(candidate: str, **_kwargs):
        return mirror_html if "kkinstagram" in candidate else None

    def fake_download(_image_url: str, output_base: str):
        path = f"{output_base}.jpg"
        Path(path).write_bytes(b"x" * 2048)
        return path

    with (
        patch.object(d, "_http_get_html", side_effect=fake_get),
        patch.object(d, "_download_image_sync", side_effect=fake_download),
        patch.object(d, "_download_sync") as download_sync,
    ):
        result = await d.download(url)

    assert result.success
    assert result.media_type_confirmed is True
    assert result.carousel_slides is not None
    assert [slide.url for slide in result.carousel_slides] == mirror_urls
    download_sync.assert_not_called()


@pytest.mark.asyncio
async def test_pp_ambiguous_cover_without_cookies_ytdlp_video_wins(tmp_path: Path):
    """A bare og:image can be a video cover, so yt-dlp must verify it even without cookies."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v" * 2048)
    single = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)], title="P"
    )
    with (
        patch.object(d, "_try_instagram_photo", return_value=single),
        patch.object(d, "_get_instagram_cookiefile", return_value=None),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "title": "Actual video",
            "duration": None,
            "width": 720,
            "height": 1280,
        }
        mock_ydl.prepare_filename.return_value = str(video)
        res = await d.download("https://www.instagram.com/p/ABC/")

    mock_cls.assert_called_once()
    assert res.is_photo is False
    assert res.file_path == str(video)
    assert (res.width, res.height) == (720, 1280)


@pytest.mark.asyncio
async def test_pp_single_photo_with_cookies_recovers_carousel_via_ytdlp(tmp_path: Path):
    """With cookies, a single-image scrape falls through to yt-dlp, which enumerates
    the full carousel the public scrape hid."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    d.has_ffmpeg = False  # keep the 0s-frame extraction out of this test
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    first = tmp_path / "first.jpg"
    first.write_bytes(b"y" * 2048)
    second = tmp_path / "second.jpg"
    second.write_bytes(b"z" * 2048)
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
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = info
        mock_ydl.prepare_filename.side_effect = [str(first), str(second)]
        res = await d.download("https://www.instagram.com/p/ABC/")

    assert res.success
    assert res.carousel_slides is not None
    assert [s.url for s in res.carousel_slides] == ["https://cdn/1.jpg", "https://cdn/2.jpg"]


@pytest.mark.asyncio
async def test_pp_ambiguous_cover_does_not_mask_transient_ytdlp_failure(tmp_path: Path):
    """403/504-like failures must not turn an unverified video cover into a photo."""
    from yt_dlp.utils import DownloadError

    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    single = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)], title="P"
    )
    with (
        patch.object(d, "_try_instagram_photo", return_value=single),
        patch.object(d, "_get_instagram_cookiefile", return_value="/fake/cookies.txt"),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("HTTP Error 403: Forbidden")
        res = await d.download("https://www.instagram.com/p/ABC/")

    assert res.success is False
    assert res.is_photo is False
    assert not photo.exists()
    assert d.get_from_cache("https://www.instagram.com/p/ABC/") is None


@pytest.mark.asyncio
async def test_pp_ambiguous_cover_falls_back_after_explicit_no_video(tmp_path: Path):
    """yt-dlp's explicit no-video result confirms that the scraped asset is a photo."""
    from yt_dlp.utils import DownloadError

    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    candidate = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    url = "https://www.instagram.com/p/PHOTO/"
    with (
        patch.object(d, "_try_instagram_photo", return_value=candidate),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("There is no video in this post")
        result = await d.download(url)

    assert result.success is True
    assert result.is_photo is True
    assert result.media_type_confirmed is True
    assert d.get_from_cache(url) is not None


@pytest.mark.asyncio
async def test_pp_ambiguous_cover_never_overrides_successful_zero_duration_video(tmp_path: Path):
    """Duration=0 is not a photo signal; successful MP4 output remains a video."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
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
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "Solo", "duration": 0.0}
        mock_ydl.prepare_filename.return_value = str(zero_video)
        res = await d.download(url)

    assert res.is_photo is False
    assert res.file_path == str(zero_video)
    assert not photo.exists()
    assert d.get_from_cache(url) is not None


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
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
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

    with patch("src.services.media_io.subprocess.run", side_effect=fake_ffmpeg):
        result = d._extract_photo_frame(incoming)

    assert result is not None
    assert result.is_photo is True
    assert result.media_type_confirmed is True
    assert result.photo_paths == [frame_path]
    assert result.carousel_slides == slides


@pytest.mark.asyncio
async def test_download_instagram_carousel_harvests_video_slides(tmp_path: Path):
    """A mixed Instagram carousel yields ordered carousel_slides (video + photo)."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    url = "https://www.instagram.com/p/MIXED/"
    first_file = tmp_path / "first.mp4"
    first_file.write_bytes(b"x" * 2048)
    second_file = tmp_path / "second.jpg"
    second_file.write_bytes(b"y" * 2048)

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
        with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
            mock_ydl = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = info
            mock_ydl.prepare_filename.side_effect = [str(first_file), str(second_file)]
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

    assert [slide.local_path for slide in result.carousel_slides] == [
        str(first_file),
        str(second_file),
    ]


@pytest.mark.asyncio
async def test_download_instagram_photo_playlist_keeps_every_local_file(tmp_path: Path):
    """The yt-dlp template must not overwrite same-extension carousel entries."""

    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    url = "https://www.instagram.com/p/PHOTOS/"

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=True):
            assert "%(id)s" in self.opts["outtmpl"]
            for media_id in ("first", "second"):
                path = self.opts["outtmpl"].replace("%(id)s", media_id).replace("%(ext)s", "jpg")
                Path(path).write_bytes(media_id.encode() * 1024)
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "finished", "filename": path})
            return {
                "title": "Photos",
                "entries": [
                    {
                        "id": "first",
                        "ext": "jpg",
                        "vcodec": "none",
                        "formats": [{"url": "https://cdn/first.jpg", "width": 1080}],
                    },
                    {
                        "id": "second",
                        "ext": "jpg",
                        "vcodec": "none",
                        "formats": [{"url": "https://cdn/second.jpg", "width": 1080}],
                    },
                ],
            }

        def prepare_filename(self, info):
            return self.opts["outtmpl"].replace("%(id)s", info["id"]).replace("%(ext)s", "jpg")

    with (
        patch.object(d, "_try_instagram_photo", return_value=None),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL),
    ):
        result = await d.download(url, allow_carousel=False)

    assert result.success
    assert result.is_photo
    assert len(result.photo_paths) == 2
    assert [Path(path).stem.rsplit("_", 1)[-1] for path in result.photo_paths] == [
        "first",
        "second",
    ]
    assert [slide.url for slide in result.carousel_slides] == [
        "https://cdn/first.jpg",
        "https://cdn/second.jpg",
    ]


@pytest.mark.asyncio
async def test_mixed_playlist_cleanup_removes_every_downloaded_file(tmp_path: Path):
    """Discarding a mixed result must not leave non-primary playlist files behind."""

    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    d.has_ffmpeg = False
    url = "https://www.instagram.com/p/MIXED-CLEANUP/"
    photo = tmp_path / "mixed-photo.jpg"
    video = tmp_path / "mixed-video.mp4"

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=True):
            photo.write_bytes(b"p" * 2048)
            video.write_bytes(b"v" * 2048)
            for path in (photo, video):
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "finished", "filename": str(path)})
            return {
                "title": "Mixed",
                "entries": [
                    {
                        "id": "photo",
                        "ext": "jpg",
                        "vcodec": "none",
                        "formats": [
                            {
                                "url": "https://cdn/photo.jpg",
                                "vcodec": "none",
                                "width": 1080,
                            }
                        ],
                    },
                    {
                        "id": "video",
                        "ext": "mp4",
                        "vcodec": "h264",
                        "acodec": "aac",
                        "width": 720,
                        "height": 1280,
                        "duration": 5,
                        "formats": [
                            {
                                "url": "https://cdn/video.mp4",
                                "vcodec": "h264",
                                "acodec": "aac",
                                "height": 1280,
                            }
                        ],
                    },
                ],
            }

        def prepare_filename(self, _info):
            return str(video)

    with (
        patch.object(d, "_try_instagram_photo", return_value=None),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL),
    ):
        result = await d.download(url, allow_carousel=False)

    assert result.success
    assert result.file_path == str(photo)
    assert [s.local_path for s in result.carousel_slides] == [str(photo), str(video)]
    assert photo.exists() and video.exists()
    d.discard_result_files(result)
    assert not photo.exists()
    assert not video.exists()


@pytest.mark.asyncio
async def test_playlist_download_error_removes_finished_partial_files(tmp_path: Path):
    """A later playlist failure must clean entries reported finished by yt-dlp."""

    from yt_dlp.utils import DownloadError

    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    url = "https://www.instagram.com/p/PARTIAL-CLEANUP/"
    partial = tmp_path / "finished-before-504.jpg"

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=True):
            partial.write_bytes(b"p" * 2048)
            for hook in self.opts["progress_hooks"]:
                hook({"status": "finished", "filename": str(partial)})
            raise DownloadError("HTTP Error 504: Gateway Timeout")

    with (
        patch.object(d, "_try_instagram_photo", return_value=None),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL),
    ):
        result = await d.download(url, allow_carousel=False)

    assert not result.success
    assert not partial.exists()


# ── X/Twitter carousels ───────────────────────────────────────────────────────


def test_is_twitter_url():
    assert is_twitter_url("https://x.com/u/status/1")
    assert is_twitter_url("https://twitter.com/u/status/1")
    assert is_twitter_url("https://mobile.twitter.com/u/status/1")
    assert not is_twitter_url("https://www.instagram.com/p/A/")
    assert not is_twitter_url("https://example.com/")


class _FakeHTTPResponse:
    """Minimal urllib response stand-in for mocking the fxtwitter fetch."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def read(self, _n: int = -1) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_fetch_twitter_media_parses_ordered_mixed(tmp_path: Path):
    """fxtwitter's tweet.media.all is parsed into ordered slides (photo + video + gif)."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    payload = json.dumps(
        {
            "code": 200,
            "message": "OK",
            "tweet": {
                "text": "hello world",
                "media": {
                    "all": [
                        {
                            "type": "photo",
                            "url": "https://pbs.twimg.com/media/a.jpg",
                            "width": 1200,
                        },
                        {"type": "video", "url": "https://video.twimg.com/b.mp4", "width": 720},
                        {"type": "gif", "url": "https://video.twimg.com/c.mp4"},
                    ]
                },
            },
        }
    ).encode()
    with patch(
        "src.services.twitter.urllib.request.urlopen",
        return_value=_FakeHTTPResponse(payload),
    ):
        out = d._fetch_twitter_media("https://x.com/user/status/123")

    assert out is not None
    slides, caption = out
    assert [(s.url, s.is_video) for s in slides] == [
        ("https://pbs.twimg.com/media/a.jpg", False),
        ("https://video.twimg.com/b.mp4", True),
        ("https://video.twimg.com/c.mp4", True),
    ]
    assert caption == "hello world"


@pytest.mark.asyncio
async def test_download_twitter_mixed_carousel_via_fxtwitter(tmp_path: Path):
    """A photo+video tweet yields a COMPLETE native carousel (photos included) sourced
    from fxtwitter; every photo/video slide has a local upload file."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    slides = [
        CarouselSlide("https://pbs.twimg.com/media/a.jpg", is_video=False),
        CarouselSlide("https://video.twimg.com/b.mp4", is_video=True),
    ]

    def fake_dl(image_url: str, output_base: str):
        path = f"{output_base}.jpg"
        with open(path, "wb") as f:
            f.write(b"x" * 2048)
        return path

    video = tmp_path / "tweet.mp4"
    video.write_bytes(b"v" * 2048)
    with (
        patch.object(d, "_fetch_twitter_media", return_value=(slides, "a tweet")),
        patch.object(d, "_download_image_sync", side_effect=fake_dl),
        patch.object(
            d, "_download_sync", return_value=DownloadResult(success=True, file_path=str(video))
        ),
    ):
        res = await d.download("https://x.com/user/status/123")

    assert res.success
    assert res.is_photo is True
    assert [(s.url, s.is_video) for s in res.carousel_slides] == [
        ("https://pbs.twimg.com/media/a.jpg", False),
        ("https://video.twimg.com/b.mp4", True),
    ]
    assert len(res.photo_paths) == 1
    assert [Path(s.local_path).suffix for s in res.carousel_slides] == [".jpg", ".mp4"]
    assert res.title == "a tweet"


def test_try_twitter_carousel_skips_video_only(tmp_path: Path):
    """Video-only tweets are left to the yt-dlp entries path (complete + local files)."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    slides = [
        CarouselSlide("https://video.twimg.com/1.mp4", is_video=True),
        CarouselSlide("https://video.twimg.com/2.mp4", is_video=True),
    ]
    with patch.object(d, "_fetch_twitter_media", return_value=(slides, "cap")):
        assert d._try_twitter_carousel("https://x.com/u/status/1") is None


@pytest.mark.asyncio
async def test_download_twitter_video_only_uses_ytdlp_entries(tmp_path: Path):
    """A multi-video tweet (no photos) is harvested from yt-dlp playlist entries."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    d.has_ffmpeg = False
    url = "https://x.com/u/status/777"
    first = tmp_path / "first.mp4"
    first.write_bytes(b"x" * 2048)
    second = tmp_path / "second.mp4"
    second.write_bytes(b"y" * 2048)
    info = {
        "title": "Tweet",
        "entries": [
            {
                "ext": "mp4",
                "duration": 5,
                "vcodec": "h264",
                "acodec": "aac",
                "formats": [
                    {
                        "url": "https://video.twimg.com/1.mp4",
                        "vcodec": "h264",
                        "acodec": "aac",
                        "height": 720,
                    }
                ],
            },
            {
                "ext": "mp4",
                "duration": 7,
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
    with (
        patch.object(d, "_fetch_twitter_media", return_value=None),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = info
        mock_ydl.prepare_filename.side_effect = [str(first), str(second)]
        res = await d.download(url)

    assert res.carousel_slides is not None
    assert [s.url for s in res.carousel_slides] == [
        "https://video.twimg.com/1.mp4",
        "https://video.twimg.com/2.mp4",
    ]
    assert all(s.is_video for s in res.carousel_slides)

    assert res.file_path == str(first)
    assert res.duration == 5
    assert [slide.local_path for slide in res.carousel_slides] == [str(first), str(second)]


# ── allow_carousel=False (conversions / inline want the real media file) ──────


@pytest.mark.asyncio
async def test_download_allow_carousel_false_skips_fxtwitter_and_gets_video(tmp_path: Path):
    """Conversions/inline pass allow_carousel=False: the fxtwitter photo path is
    skipped and yt-dlp's actual video file is returned (so FFmpeg gets a video,
    not a JPG). The transient result is not cached."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    video = tmp_path / "vid.mp4"
    video.write_bytes(b"x" * 2048)
    url = "https://x.com/u/status/123"
    fx = MagicMock()  # if the fxtwitter path ran, this would be called
    with (
        patch.object(d, "_fetch_twitter_media", fx),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "vid", "duration": 12.0}
        mock_ydl.prepare_filename.return_value = str(video)
        res = await d.download(url, allow_carousel=False)

    fx.assert_not_called()
    assert res.success
    assert res.is_photo is False
    assert res.file_path == str(video)
    assert d.get_from_cache(url) is None  # not cached → no cross-contamination


@pytest.mark.asyncio
async def test_download_allow_carousel_false_ignores_cached_carousel(tmp_path: Path):
    """A previously cached photo-carousel must NOT be served to an allow_carousel=False
    caller — it re-downloads to get the real video."""
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x" * 2048)
    url = "https://x.com/u/status/9"
    d.add_to_cache(
        url,
        DownloadResult(
            success=True,
            file_path=str(photo),
            is_photo=True,
            photo_paths=[str(photo)],
            carousel_slides=[
                CarouselSlide("https://pbs.twimg.com/1.jpg"),
                CarouselSlide("https://video.twimg.com/2.mp4", is_video=True),
            ],
        ),
    )
    video = tmp_path / "v.mp4"
    video.write_bytes(b"y" * 2048)
    with (
        patch.object(d, "_fetch_twitter_media", return_value=None),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"title": "v", "duration": 10.0}
        mock_ydl.prepare_filename.return_value = str(video)
        res = await d.download(url, allow_carousel=False)

    assert res.is_photo is False
    assert res.file_path == str(video)


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/@channel/videos",
        "https://youtube.com/channel/UC123",
        "https://youtube.com/watch?list=PL123",
        "https://youtube.com/embed/videoseries?list=PL123",
    ],
)
def test_youtube_collection_rejected_before_extraction(tmp_path, url):
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as ydl:
        result = d._download_sync(url, d._get_ydl_opts("out.mp4", url))
    assert not result.success
    assert result.error_code == "downloader.error.single_video_required"
    ydl.assert_not_called()


def test_playlist_limits_are_configured_before_download(tmp_path):
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    youtube = d._get_ydl_opts("out.mp4", "https://youtube.com/watch?v=first&list=PL123")
    assert youtube["noplaylist"] is True
    assert youtube["playlist_items"] == "1"
    instagram = d._get_ydl_opts("out.mp4", "https://instagram.com/p/MIXED/")
    assert instagram["noplaylist"] is False
    assert instagram["playlist_items"] == "1:20"


def test_mixed_rich_carousel_attaches_local_video_and_photo(tmp_path):
    photo = tmp_path / "slide.jpg"
    video = tmp_path / "slide.mp4"
    photo.write_bytes(b"p")
    video.write_bytes(b"v")
    slides = [
        CarouselSlide("https://expired/photo.jpg", local_path=str(photo)),
        CarouselSlide("https://expired/video.mp4", True, str(video), 720, 1280, 8.5),
    ]
    variants = rich_carousel_variants(slides)
    assert len(variants) == 2
    media = [attachment.media for attachment in variants[0].media]
    assert [item.type for item in media] == ["photo", "video"]
    assert [item.media.path for item in media] == [str(photo), str(video)]
    assert (media[1].width, media[1].height, media[1].duration) == (720, 1280, 8)


def test_mixed_carousel_cache_keeps_paths_metadata_and_deletes_all_files(tmp_path):
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    paths = [tmp_path / "photo.jpg", tmp_path / "video.mp4"]
    for path in paths:
        path.write_bytes(b"x")
    url = "https://instagram.com/p/MIXED-CACHE/"
    d.add_to_cache(
        url,
        DownloadResult(
            success=True,
            file_path=str(paths[0]),
            is_photo=True,
            photo_paths=[str(paths[0])],
            carousel_slides=[
                CarouselSlide("https://cdn/photo.jpg", local_path=str(paths[0])),
                CarouselSlide("https://cdn/video.mp4", True, str(paths[1]), 720, 1280, 12),
            ],
        ),
    )
    restored = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    result = restored.get_from_cache(url)
    assert [slide.local_path for slide in result.carousel_slides] == [str(p) for p in paths]
    assert result.carousel_slides[1].height == 1280
    restored.clear_cache()
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("url", ["https://x.com/user/status/123", "https://instagram.com/p/ABC/"])
def test_photo_hooks_without_playlist_keep_all_uploadable_slides(tmp_path, url):
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    paths = [tmp_path / "first.jpg", tmp_path / "second.jpg"]
    for path in paths:
        path.write_bytes(b"photo")

    class PhotoHooksYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, url, download=True):
            for path in paths:
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "finished", "filename": str(path)})
            return {"title": "Two photos"}

        def prepare_filename(self, info):
            return str(paths[0])

    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", PhotoHooksYDL):
        result = d._download_sync(url, d._get_ydl_opts(str(tmp_path / "output_%(id)s.jpg"), url))
    assert result.success
    assert result.photo_paths == [str(path) for path in paths]
    assert [slide.local_path for slide in result.carousel_slides] == result.photo_paths
    assert all(path.exists() for path in paths)
    attached = rich_carousel_variants(result.carousel_slides)[0]
    assert [item.media.media.path for item in attached.media] == result.photo_paths


@pytest.mark.asyncio
async def test_html_photos_never_replace_photo_first_mixed_carousel(tmp_path):
    d = VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)
    html_paths = [tmp_path / f"html-{index}.jpg" for index in range(3)]
    photo = tmp_path / "actual.jpg"
    video = tmp_path / "actual.mp4"
    for path in [*html_paths, photo, video]:
        path.write_bytes(b"media")
    html_fallback = DownloadResult(
        success=True,
        file_path=str(html_paths[0]),
        is_photo=True,
        photo_paths=[str(path) for path in html_paths],
    )
    actual = DownloadResult(
        success=True,
        file_path=str(photo),
        is_photo=True,
        photo_paths=[str(photo)],
        carousel_slides=[
            CarouselSlide("https://cdn/actual.jpg", local_path=str(photo)),
            CarouselSlide("https://cdn/actual.mp4", True, str(video)),
        ],
    )
    with (
        patch.object(d, "_try_instagram_photo", return_value=html_fallback),
        patch.object(d, "_download_sync", return_value=actual),
    ):
        result = await d.download("https://instagram.com/p/MIXED-FIRST-PHOTO/")
    assert result.carousel_slides == actual.carousel_slides
    assert photo.exists() and video.exists()
    assert all(not path.exists() for path in html_paths)
    cached = d.get_from_cache("https://instagram.com/p/MIXED-FIRST-PHOTO/")
    assert [slide.is_video for slide in cached.carousel_slides] == [False, True]
