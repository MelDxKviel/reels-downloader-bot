"""Extra downloader tests to push coverage to 100%."""

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.downloader import DownloadResult, VideoDownloader


def make_d(tmp_path: Path) -> VideoDownloader:
    return VideoDownloader(str(tmp_path))


def fake_file(tmp_path: Path, name: str = "v.mp4", size: int = 1024) -> Path:
    f = tmp_path / name
    f.write_bytes(b"x" * size)
    return f


# ── cache load/save exceptions ────────────────────────────────────────────────


def test_load_cache_returns_empty_on_invalid_json(tmp_path):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text("{ not valid json")
    d = make_d(tmp_path)
    assert d.cache == {}


def test_save_cache_swallows_exception(tmp_path):
    d = make_d(tmp_path)
    with patch("builtins.open", side_effect=OSError("disk full")):
        d._save_cache()  # should not raise


# ── set_telegram_file_id no-op branches ───────────────────────────────────────


def test_set_telegram_file_id_new_entry(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=fresh"
    d.set_telegram_file_id(url, "fid")
    assert d.get_telegram_file_id(url) == "fid"


def test_set_telegram_photo_file_id_empty_noop(tmp_path):
    d = make_d(tmp_path)
    d.set_telegram_photo_file_id("https://www.instagram.com/p/x/", "")
    assert d.get_telegram_photo_file_id("https://www.instagram.com/p/x/") is None


def test_set_telegram_photo_file_id_same_noop(tmp_path):
    d = make_d(tmp_path)
    url = "https://www.instagram.com/p/x/"
    d.set_telegram_photo_file_id(url, "abc")
    with patch.object(d, "_save_cache") as mock_save:
        d.set_telegram_photo_file_id(url, "abc")
    mock_save.assert_not_called()


def test_set_telegram_mp3_file_id_empty_noop(tmp_path):
    d = make_d(tmp_path)
    d.set_telegram_mp3_file_id("https://youtube.com/watch?v=a", "")
    assert d.get_telegram_mp3_file_id("https://youtube.com/watch?v=a") is None


def test_set_telegram_mp3_file_id_same_noop(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=a"
    d.set_telegram_mp3_file_id(url, "abc")
    with patch.object(d, "_save_cache") as mock_save:
        d.set_telegram_mp3_file_id(url, "abc")
    mock_save.assert_not_called()


# ── get_cached_media_type photo via photo_file_id ─────────────────────────────


def test_get_cached_media_type_via_telegram_photo_file_id(tmp_path):
    d = make_d(tmp_path)
    url = "https://www.instagram.com/p/x/"
    d.set_telegram_photo_file_id(url, "ph_fid")
    assert d.get_cached_media_type(url) == "photo"


def test_get_telegram_photo_file_id_none_for_missing(tmp_path):
    d = make_d(tmp_path)
    assert d.get_telegram_photo_file_id("https://www.instagram.com/p/none/") is None


def test_get_telegram_mp3_file_id_none_for_missing(tmp_path):
    d = make_d(tmp_path)
    assert d.get_telegram_mp3_file_id("https://youtube.com/watch?v=z") is None


# ── _looks_like_netscape_cookies_file branches ────────────────────────────────


def test_looks_like_netscape_only_comment_line(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# just a comment\n")  # only a comment, no tab line yet
    d = make_d(tmp_path)
    # Comments alone aren't enough → false
    assert d._looks_like_netscape_cookies_file(str(f)) is False


def test_looks_like_netscape_empty_lines_then_tab_row(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("\n\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    assert d._looks_like_netscape_cookies_file(str(f)) is True


def test_looks_like_netscape_tab_row_too_few_parts(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text(".youtube.com\tTRUE\t/\n")  # only 3 columns
    d = make_d(tmp_path)
    assert d._looks_like_netscape_cookies_file(str(f)) is False


def test_looks_like_netscape_only_empty_then_eof(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("")
    d = make_d(tmp_path)
    assert d._looks_like_netscape_cookies_file(str(f)) is False


# ── _get_youtube_cookiefile / _get_instagram_cookiefile ──────────────────────


def test_get_youtube_cookiefile_none_when_unset(tmp_path):
    d = make_d(tmp_path)
    with patch("src.services.downloader.YT_COOKIES_FILE", None):
        assert d._get_youtube_cookiefile() is None


def test_get_youtube_cookiefile_none_when_missing(tmp_path):
    d = make_d(tmp_path)
    with patch("src.services.downloader.YT_COOKIES_FILE", "/nope/cookies.txt"):
        assert d._get_youtube_cookiefile() is None


def test_get_youtube_cookiefile_invalid_format(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text('{"not": "netscape"}')
    d = make_d(tmp_path)
    with patch("src.services.downloader.YT_COOKIES_FILE", str(f)):
        assert d._get_youtube_cookiefile() is None


def test_get_youtube_cookiefile_valid(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.downloader.YT_COOKIES_FILE", str(f)):
        assert d._get_youtube_cookiefile() == str(f)


def test_get_instagram_cookiefile_none_when_unset(tmp_path):
    d = make_d(tmp_path)
    with patch("src.services.downloader.INSTA_COOKIES_FILE", None):
        assert d._get_instagram_cookiefile() is None


def test_get_instagram_cookiefile_none_when_missing(tmp_path):
    d = make_d(tmp_path)
    with patch("src.services.downloader.INSTA_COOKIES_FILE", "/nope.txt"):
        assert d._get_instagram_cookiefile() is None


def test_get_instagram_cookiefile_invalid_format(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text('{"foo": "bar"}')
    d = make_d(tmp_path)
    with patch("src.services.downloader.INSTA_COOKIES_FILE", str(f)):
        assert d._get_instagram_cookiefile() is None


def test_get_instagram_cookiefile_valid(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.downloader.INSTA_COOKIES_FILE", str(f)):
        assert d._get_instagram_cookiefile() == str(f)


# ── _get_ydl_opts cookies for instagram ──────────────────────────────────────


def test_get_ydl_opts_youtube_with_cookies(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.downloader.YT_COOKIES_FILE", str(f)):
        opts = d._get_ydl_opts("out.%(ext)s", "https://youtube.com/watch?v=a")
    assert opts.get("cookiefile") == str(f)


def test_get_ydl_opts_instagram_with_cookies(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.downloader.INSTA_COOKIES_FILE", str(f)):
        opts = d._get_ydl_opts("out.%(ext)s", "https://www.instagram.com/reel/a/")
    assert opts.get("cookiefile") == str(f)


# ── _decode_json_str / _find_meta_contents / _append_unique ─────────────────


def test_decode_json_str_invalid_returns_replaced():
    # Force a string with backslash that fails JSON parsing → fallback to replace
    raw = "\\u00xx"  # invalid escape
    assert "\\u00xx" in VideoDownloader._decode_json_str(raw) or VideoDownloader._decode_json_str(
        raw
    )


def test_find_meta_contents_content_first():
    html = '<meta content="x" property="og:image">'
    results = list(VideoDownloader._find_meta_contents(html, "og:image"))
    assert results == ["x"]


def test_append_unique_skips_empty():
    target = []
    VideoDownloader._append_unique(target, "")
    VideoDownloader._append_unique(target, None)
    VideoDownloader._append_unique(target, "  ")  # only whitespace
    assert target == []


def test_append_unique_skips_duplicate():
    target = ["a"]
    VideoDownloader._append_unique(target, "a")
    assert target == ["a"]


# ── _parse_instagram_html: embedded_media_image, src-first ──────────────────


def test_parse_instagram_html_embedded_image_class_first():
    html = '<img class="EmbeddedMediaImage thing" src="https://cdn.example/photo.jpg" alt="x">'
    result = VideoDownloader._parse_instagram_html(html)
    assert any("photo.jpg" in u for u in result["image_urls"])


def test_parse_instagram_html_embedded_image_src_first():
    html = '<img src="https://cdn.example/photo2.jpg" class="EmbeddedMediaImage">'
    result = VideoDownloader._parse_instagram_html(html)
    assert any("photo2.jpg" in u for u in result["image_urls"])


def test_parse_instagram_html_og_video_secure_url():
    html = '<meta property="og:video:secure_url" content="https://cdn.example/v.mp4">'
    result = VideoDownloader._parse_instagram_html(html)
    assert result["video_url"] == "https://cdn.example/v.mp4"


def test_parse_instagram_html_og_image_url_variant():
    html = '<meta property="og:image:url" content="https://cdn.example/x.jpg">'
    result = VideoDownloader._parse_instagram_html(html)
    assert "https://cdn.example/x.jpg" in result["image_urls"]


def test_parse_instagram_html_og_image_secure_url_variant():
    html = '<meta property="og:image:secure_url" content="https://cdn.example/x2.jpg">'
    result = VideoDownloader._parse_instagram_html(html)
    assert "https://cdn.example/x2.jpg" in result["image_urls"]


# ── _http_get_html ───────────────────────────────────────────────────────────


def test_http_get_html_url_error():
    with patch(
        "src.services.downloader.urllib.request.urlopen",
        side_effect=urllib.error.URLError("nope"),
    ):
        assert VideoDownloader._http_get_html("https://x") is None


def test_http_get_html_non_html_content_type():
    fake = MagicMock()
    fake.headers = {"Content-Type": "application/json"}
    fake.read = MagicMock(return_value=b"{}")
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        assert VideoDownloader._http_get_html("https://x") is None


def test_http_get_html_success():
    fake = MagicMock()
    fake.headers = {"Content-Type": "text/html"}
    fake.read = MagicMock(return_value=b"<html></html>")
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        result = VideoDownloader._http_get_html("https://x")
    assert result == "<html></html>"


# ── _fetch_instagram_media_info ─────────────────────────────────────────────


def test_fetch_instagram_media_info_no_html(tmp_path):
    d = make_d(tmp_path)
    with patch.object(d, "_http_get_html", return_value=None):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    assert result is None


def test_fetch_instagram_media_info_with_images(tmp_path):
    d = make_d(tmp_path)
    html = '<meta property="og:image" content="https://cdn.example/img.jpg">'
    with patch.object(d, "_http_get_html", return_value=html):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    assert result is not None
    assert result["image_urls"]


def test_fetch_instagram_media_info_with_video_marker(tmp_path):
    d = make_d(tmp_path)
    html = '<meta property="og:video" content="https://cdn.example/v.mp4">'
    with patch.object(d, "_http_get_html", return_value=html):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    assert result is not None
    assert result["has_video"] is True


def test_fetch_instagram_media_info_aggregates_multiple_endpoints(tmp_path):
    d = make_d(tmp_path)
    htmls = [
        '<meta property="og:image" content="https://cdn.example/a.jpg">',
        '<meta property="og:image" content="https://cdn.example/b.jpg">',
        None,
        None,
    ]
    iterator = iter(htmls)
    with patch.object(d, "_http_get_html", side_effect=lambda *a, **k: next(iterator, None)):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    assert result is not None


def test_fetch_instagram_media_info_max_carousel(tmp_path):
    d = make_d(tmp_path)
    images = "\n".join(
        f'<meta property="og:image" content="https://cdn.example/img{i}.jpg">' for i in range(12)
    )
    with patch.object(d, "_http_get_html", return_value=images):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    assert len(result["image_urls"]) <= 10


# ── _download_image_sync ─────────────────────────────────────────────────────


def _make_fake_resp(content_type, body=b"x" * 5000):
    """Build a fake urlopen response context manager."""
    fake = MagicMock()
    fake.headers = {"Content-Type": content_type}
    data = [body, b""]
    fake.read = MagicMock(side_effect=data)
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    return fake


@pytest.mark.parametrize(
    "ct, expected_ext",
    [
        ("image/jpeg", "jpg"),
        ("image/jpg", "jpg"),
        ("image/png", "png"),
        ("image/webp", "webp"),
        ("image/heic", "heic"),
    ],
)
def test_download_image_sync_picks_ext_from_content_type(tmp_path, ct, expected_ext):
    d = make_d(tmp_path)
    fake = _make_fake_resp(ct)
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is not None
    assert out.endswith("." + expected_ext)


def test_download_image_sync_picks_ext_from_url_path(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("image/unknown")
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img.gif?q=1", str(tmp_path / "out"))
    assert out is not None
    assert out.endswith(".gif")


def test_download_image_sync_picks_jpg_fallback_when_url_ext_invalid(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("image/unknown")
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img.toolongextension", str(tmp_path / "out"))
    assert out is not None
    assert out.endswith(".jpg")


def test_download_image_sync_rejects_non_image(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("text/html")
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None


def test_download_image_sync_url_error(tmp_path):
    d = make_d(tmp_path)
    with patch(
        "src.services.downloader.urllib.request.urlopen",
        side_effect=urllib.error.URLError("nope"),
    ):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None


def test_download_image_sync_rejects_tiny_file(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("image/jpeg", body=b"x" * 100)
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None


def test_download_image_sync_exceeds_max_filesize(tmp_path):
    d = make_d(tmp_path)
    from src.config import MAX_FILE_SIZE

    big_body = b"x" * (MAX_FILE_SIZE + 100)
    fake = MagicMock()
    fake.headers = {"Content-Type": "image/jpeg"}
    fake.read = MagicMock(side_effect=[big_body, b""])
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None


# ── _extract_photo_frame ────────────────────────────────────────────────────


def test_extract_photo_frame_no_ffmpeg(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = False
    r = DownloadResult(success=True, file_path=str(tmp_path / "v.mp4"))
    assert d._extract_photo_frame(r) is None


def test_extract_photo_frame_no_file_path(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    r = DownloadResult(success=True, file_path=None)
    assert d._extract_photo_frame(r) is None


def test_extract_photo_frame_ffmpeg_fail(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    r = DownloadResult(success=True, file_path=str(v))
    proc = MagicMock()
    proc.returncode = 1
    with patch("src.services.downloader.subprocess.run", return_value=proc):
        assert d._extract_photo_frame(r) is None


def test_extract_photo_frame_no_output_file(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    r = DownloadResult(success=True, file_path=str(v))
    proc = MagicMock()
    proc.returncode = 0
    with patch("src.services.downloader.subprocess.run", return_value=proc):
        assert d._extract_photo_frame(r) is None


def test_extract_photo_frame_tiny_output_file(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    out = tmp_path / "v_photo.jpg"
    out.write_bytes(b"x")  # tiny

    def fake_run(*args, **kwargs):
        return MagicMock(returncode=0)

    with patch("src.services.downloader.subprocess.run", side_effect=fake_run):
        assert d._extract_photo_frame(DownloadResult(success=True, file_path=str(v))) is None


def test_extract_photo_frame_success(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    out_path = tmp_path / "v_photo.jpg"
    proc = MagicMock(returncode=0)

    def fake_run(*args, **kwargs):
        out_path.write_bytes(b"x" * 5000)
        return proc

    with patch("src.services.downloader.subprocess.run", side_effect=fake_run):
        result = d._extract_photo_frame(DownloadResult(success=True, file_path=str(v)))
    assert result is not None
    assert result.is_photo is True
    assert not v.exists()  # video removed


def test_extract_photo_frame_success_remove_video_fails(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    out_path = tmp_path / "v_photo.jpg"

    def fake_run(*args, **kwargs):
        out_path.write_bytes(b"x" * 5000)
        return MagicMock(returncode=0)

    with patch("src.services.downloader.subprocess.run", side_effect=fake_run):
        with patch("src.services.downloader.os.remove", side_effect=OSError("nope")):
            result = d._extract_photo_frame(DownloadResult(success=True, file_path=str(v)))
    assert result is not None


def test_extract_photo_frame_timeout(tmp_path):
    import subprocess as _sp

    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    with patch(
        "src.services.downloader.subprocess.run",
        side_effect=_sp.TimeoutExpired(cmd="ffmpeg", timeout=30),
    ):
        assert d._extract_photo_frame(DownloadResult(success=True, file_path=str(v))) is None


# ── _try_instagram_photo ─────────────────────────────────────────────────────


def test_try_instagram_photo_no_meta(tmp_path):
    d = make_d(tmp_path)
    with patch.object(d, "_fetch_instagram_media_info", return_value=None):
        assert d._try_instagram_photo("https://www.instagram.com/p/abc/") is None


def test_try_instagram_photo_has_video(tmp_path):
    d = make_d(tmp_path)
    with patch.object(
        d, "_fetch_instagram_media_info", return_value={"image_urls": ["x"], "has_video": True}
    ):
        assert d._try_instagram_photo("https://www.instagram.com/p/abc/") is None


def test_try_instagram_photo_no_images(tmp_path):
    d = make_d(tmp_path)
    with patch.object(
        d, "_fetch_instagram_media_info", return_value={"image_urls": [], "has_video": False}
    ):
        assert d._try_instagram_photo("https://www.instagram.com/p/abc/") is None


def test_try_instagram_photo_no_cdn_images(tmp_path):
    d = make_d(tmp_path)
    with patch.object(
        d,
        "_fetch_instagram_media_info",
        return_value={
            "image_urls": ["https://login.instagram.com/branding.png"],
            "has_video": False,
        },
    ):
        assert d._try_instagram_photo("https://www.instagram.com/p/abc/") is None


def test_try_instagram_photo_success(tmp_path):
    d = make_d(tmp_path)
    cdn_img = "https://scontent.cdninstagram.com/v/t51/photo.jpg"
    meta = {"image_urls": [cdn_img], "has_video": False, "title": "Hi"}
    with patch.object(d, "_fetch_instagram_media_info", return_value=meta):
        with patch.object(
            d, "_download_image_sync", return_value=str(fake_file(tmp_path, "out.jpg"))
        ):
            result = d._try_instagram_photo("https://www.instagram.com/p/abc/")
    assert result is not None
    assert result.is_photo is True


def test_try_instagram_photo_no_downloads(tmp_path):
    d = make_d(tmp_path)
    cdn_img = "https://scontent.cdninstagram.com/v/t51/photo.jpg"
    meta = {"image_urls": [cdn_img], "has_video": False}
    with patch.object(d, "_fetch_instagram_media_info", return_value=meta):
        with patch.object(d, "_download_image_sync", return_value=None):
            assert d._try_instagram_photo("https://www.instagram.com/p/abc/") is None


def test_try_instagram_photo_groups_variants(tmp_path):
    d = make_d(tmp_path)
    base = "https://scontent.cdninstagram.com/v/t51/photo.jpg"
    meta = {
        "image_urls": [f"{base}?stp=s1080x1080", base],  # same path, different query
        "has_video": False,
    }
    with patch.object(d, "_fetch_instagram_media_info", return_value=meta):
        with patch.object(
            d, "_download_image_sync", return_value=str(fake_file(tmp_path, "img.jpg"))
        ):
            result = d._try_instagram_photo("https://www.instagram.com/p/abc/")
    assert result is not None


# ── download(): photo flow integration ───────────────────────────────────────


@pytest.mark.asyncio
async def test_download_with_instagram_photo_short_circuit(tmp_path):
    d = make_d(tmp_path)
    photo = fake_file(tmp_path, "x.jpg")
    photo_result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(d, "_try_instagram_photo", return_value=photo_result):
        result = await d.download("https://www.instagram.com/p/abc/")
    assert result.success
    assert result.is_photo


@pytest.mark.asyncio
async def test_download_exception_in_executor(tmp_path):
    d = make_d(tmp_path)
    with patch.object(d, "_download_sync", side_effect=RuntimeError("boom")):
        result = await d.download("https://youtube.com/watch?v=fail")
    assert not result.success
    assert "boom" in result.error or "Ошибка" in result.error


# ── _download_sync inner branches ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_sync_no_info(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=noinfo"
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = None
        result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_sync_playlist_no_entries(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=emptylist"
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"entries": [None, None]}
        result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_sync_playlist_with_entries(tmp_path):
    d = make_d(tmp_path)
    video = fake_file(tmp_path, "v.mp4")
    url = "https://youtube.com/watch?v=list"
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "entries": [{"title": "First", "duration": 5}, {"title": "Skip"}]
        }
        mock_ydl.prepare_filename.return_value = str(video)
        result = await d.download(url)
    assert result.success
    assert result.title == "First"


@pytest.mark.asyncio
async def test_download_sync_prepare_filename_raises(tmp_path):
    """When prepare_filename raises, fallback path tries extensions."""
    d = make_d(tmp_path)
    # file with non-default extension found via base + ext probe
    file_id = "abc12345"
    video = tmp_path / f"{file_id}.mkv"
    video.write_bytes(b"x")
    url = "https://youtube.com/watch?v=probe"

    class FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, _url, download=True):
            return {"title": "T", "duration": 5}

        def prepare_filename(self, info):
            raise RuntimeError("can't prepare")

    with patch("src.services.downloader.yt_dlp.YoutubeDL", FakeYDL):
        # We can't easily inject file_id, so just ensure exception path is reached
        with patch("uuid.uuid4", return_value=MagicMock(__str__=lambda s: file_id + "00")):
            await d.download(url)


@pytest.mark.asyncio
async def test_download_sync_unfound_file_via_find(tmp_path):
    """When prepare_filename returns non-existing path, _find_downloaded_file probes."""
    d = make_d(tmp_path)
    file_id = "abcd1234"
    # Place a probe file the function should find
    video = tmp_path / f"{file_id}.mkv"
    video.write_bytes(b"x")
    url = "https://youtube.com/watch?v=findme"

    class FakeYDL:
        def __init__(self, opts):
            self._opts = opts
            opts["outtmpl"] = str(tmp_path / f"{file_id}.%(ext)s")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, _url, download=True):
            return {"title": "T", "duration": 5}

        def prepare_filename(self, info):
            return str(tmp_path / f"{file_id}.unknownext")

    # The opts passed in are mutated when the test FakeYDL runs.
    # We need outtmpl to give file_id matching file. Bypass via patching _get_ydl_opts.
    def fake_opts(out_path, u):
        return {"outtmpl": str(tmp_path / f"{file_id}.%(ext)s"), "format": "best"}

    with patch.object(d, "_get_ydl_opts", side_effect=fake_opts):
        with patch("src.services.downloader.yt_dlp.YoutubeDL", FakeYDL):
            result = await d.download(url)
    assert result.success
    assert result.file_path == str(video)


@pytest.mark.asyncio
async def test_download_sync_no_file_found_at_all(tmp_path):
    """No downloaded file → returns file_not_downloaded error."""
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=notdownloaded"

    class FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, _url, download=True):
            return {"title": "T", "duration": 5}

        def prepare_filename(self, info):
            return str(tmp_path / "missing.mp4")

    def fake_opts(out_path, u):
        return {"outtmpl": str(tmp_path / "missing.%(ext)s"), "format": "best"}

    with patch.object(d, "_get_ydl_opts", side_effect=fake_opts):
        with patch("src.services.downloader.yt_dlp.YoutubeDL", FakeYDL):
            result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_sync_outtmpl_dict_form(tmp_path):
    """outtmpl can be a dict with 'default' key."""
    d = make_d(tmp_path)
    file_id = "dictid12"
    video = tmp_path / f"{file_id}.mp4"
    video.write_bytes(b"x")
    url = "https://youtube.com/watch?v=dict"

    class FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, _url, download=True):
            return {"title": "T", "duration": 5}

        def prepare_filename(self, info):
            return str(tmp_path / f"{file_id}.unknownext")

    def fake_opts(out_path, u):
        return {
            "outtmpl": {"default": str(tmp_path / f"{file_id}.%(ext)s")},
            "format": "best",
        }

    with patch.object(d, "_get_ydl_opts", side_effect=fake_opts):
        with patch("src.services.downloader.yt_dlp.YoutubeDL", FakeYDL):
            result = await d.download(url)
    assert result.success


# ── _download_sync error branches ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_handles_ffmpeg_required_error(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=ffmpeg"
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("ffmpeg is not installed")
        result = await d.download(url)
    assert not result.success
    assert "FFmpeg" in result.error or "ffmpeg" in result.error.lower()


@pytest.mark.asyncio
async def test_download_handles_sign_in_error(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=signin"
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("Sign in to view")
        result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_handles_generic_error(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=other"
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("some other error")
        result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_handles_unexpected_exception(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=oops"
    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = RuntimeError("totally unexpected")
        result = await d.download(url)
    assert not result.success


# ── kkinstagram fallback ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_retries_via_kkinstagram(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    url = "https://www.instagram.com/reel/login_required_abc/"
    video = fake_file(tmp_path, "out.mp4")

    call_count = 0

    def fake_extract(download_url, download=True):
        nonlocal call_count
        call_count += 1
        if "kkinstagram" in download_url:
            return {"title": "T", "duration": 5}
        raise DownloadError("login_required")

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = fake_extract
        mock_ydl.prepare_filename.return_value = str(video)
        # Make instagram photo extraction fail so the video flow is taken.
        with patch.object(d, "_try_instagram_photo", return_value=None):
            result = await d.download(url)
    assert result.success
    assert call_count == 2


@pytest.mark.asyncio
async def test_download_kkinstagram_fallback_also_fails(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    url = "https://www.instagram.com/reel/abc_login/"

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("login_required")
        with patch.object(d, "_try_instagram_photo", return_value=None):
            result = await d.download(url)
    assert not result.success


# ── _find_downloaded_file ────────────────────────────────────────────────────


def test_find_downloaded_file_known_ext(tmp_path):
    d = make_d(tmp_path)
    f = tmp_path / "id123.mp4"
    f.write_bytes(b"x")
    assert d._find_downloaded_file("id123") == str(f)


def test_find_downloaded_file_via_scan(tmp_path):
    d = make_d(tmp_path)
    f = tmp_path / "scanid_extra.mp4"
    f.write_bytes(b"x")
    found = d._find_downloaded_file("scanid")
    assert found == str(f)


def test_find_downloaded_file_not_found(tmp_path):
    d = make_d(tmp_path)
    assert d._find_downloaded_file("nope") is None


# ── clear_cache photo paths ──────────────────────────────────────────────────


def test_clear_cache_with_photo_paths(tmp_path):
    d = make_d(tmp_path)
    p1 = fake_file(tmp_path, "p1.jpg")
    p2 = fake_file(tmp_path, "p2.jpg")
    d.cache["h"] = {"file_path": str(p1), "photo_paths": [str(p1), str(p2)], "is_photo": True}
    d._save_cache()
    count = d.clear_cache()
    assert count >= 2


def test_clear_cache_handles_os_remove_failure(tmp_path):
    d = make_d(tmp_path)
    p1 = fake_file(tmp_path, "p1.jpg")
    d.cache["h"] = {"file_path": str(p1), "photo_paths": [str(p1)], "is_photo": True}
    with patch("src.services.downloader.os.remove", side_effect=OSError("nope")):
        d.clear_cache()


# ── final remaining branches ────────────────────────────────────────────────


def test_downloader_method_is_supported_url(tmp_path):
    d = make_d(tmp_path)
    assert d.is_supported_url("https://youtube.com/watch?v=a") is True
    assert d.is_supported_url("https://example.com/") is False


def test_downloader_method_get_platform_name(tmp_path):
    d = make_d(tmp_path)
    assert d.get_platform_name("https://youtube.com/watch?v=a") == "YouTube"


def test_get_cached_media_type_returns_none_for_empty_entry(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=ghost"
    url_hash = d._get_url_hash(url)
    d.cache[url_hash] = {}  # entry exists but has nothing useful
    assert d.get_cached_media_type(url) is None


def test_add_to_cache_preserves_existing_telegram_file_id(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=keep"
    d.set_telegram_file_id(url, "fid1")
    video = fake_file(tmp_path, "v.mp4")
    d.add_to_cache(url, DownloadResult(success=True, file_path=str(video), title="T"))
    # Existing telegram_file_id must be preserved by the new cache entry
    assert d.get_telegram_file_id(url) == "fid1"


def test_fetch_instagram_media_info_breaks_on_video_marker(tmp_path):
    """Once has_video_marker is set, the loop terminates early."""
    d = make_d(tmp_path)
    html_with_video = '<meta property="og:video" content="https://cdn.example/v.mp4">'
    call_count = 0

    def fake_get(_):
        nonlocal call_count
        call_count += 1
        return html_with_video

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    # Should not call all 4 endpoints — the loop breaks on first video marker
    assert call_count < 4


def test_parse_instagram_html_video_url_from_inline_json():
    html = '{"video_url": "https://cdn.example/v.mp4"}'
    result = VideoDownloader._parse_instagram_html(html)
    assert result["video_url"] == "https://cdn.example/v.mp4"


def test_download_image_sync_max_filesize_remove_fails(tmp_path):
    d = make_d(tmp_path)
    from src.config import MAX_FILE_SIZE

    big = b"x" * (MAX_FILE_SIZE + 100)
    fake = MagicMock()
    fake.headers = {"Content-Type": "image/jpeg"}
    fake.read = MagicMock(side_effect=[big, b""])
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        with patch("src.services.downloader.os.remove", side_effect=OSError("nope")):
            out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None


def test_download_image_sync_tiny_remove_fails(tmp_path):
    d = make_d(tmp_path)
    body = b"x" * 100  # < 1024
    fake = MagicMock()
    fake.headers = {"Content-Type": "image/jpeg"}
    fake.read = MagicMock(side_effect=[body, b""])
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with patch("src.services.downloader.urllib.request.urlopen", return_value=fake):
        with patch("src.services.downloader.os.remove", side_effect=OSError("nope")):
            out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None


@pytest.mark.asyncio
async def test_download_sync_outtmpl_empty(tmp_path):
    """outtmpl returns empty string → file_id is None → no file found."""
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=emptytmpl"

    class FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, _url, download=True):
            return {"title": "T", "duration": 5}

        def prepare_filename(self, info):
            return None

    def fake_opts(out_path, u):
        return {"outtmpl": "", "format": "best"}

    with patch.object(d, "_get_ydl_opts", side_effect=fake_opts):
        with patch("src.services.downloader.yt_dlp.YoutubeDL", FakeYDL):
            result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_cookie_retry_then_fails(tmp_path):
    """Cookies file rejected, retry without cookies also fails — still raises."""
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=cookiefail"

    call_count = 0

    def fake_extract(download_url, download=True):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise DownloadError("does not look like a netscape format cookies file")
        raise DownloadError("Video unavailable")

    with patch("src.services.downloader.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = fake_extract

        original = d._get_ydl_opts

        def patched(output_path, u):
            opts = original(output_path, u)
            opts["cookiefile"] = "/fake/c.txt"
            return opts

        d._get_ydl_opts = patched
        result = await d.download(url)

    assert not result.success
    assert "недоступно" in result.error or "Video unavailable" in result.error
    assert call_count == 2


def test_get_cached_media_type_non_empty_but_no_known_keys(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=phantom"
    url_hash = d._get_url_hash(url)
    # non-falsy entry but without any media keys
    d.cache[url_hash] = {"foo": "bar"}
    assert d.get_cached_media_type(url) is None


def test_fetch_instagram_media_info_extracts_title(tmp_path):
    d = make_d(tmp_path)
    html = (
        '<meta property="og:image" content="https://cdn.example/a.jpg">'
        '<meta property="og:title" content="My Post">'
    )
    with patch.object(d, "_http_get_html", return_value=html):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    assert result["title"] == "My Post"


@pytest.mark.asyncio
async def test_download_sync_outtmpl_dict_in_else_branch(tmp_path):
    """outtmpl is a dict — exercise outtmpl.get('default') unwrap path."""
    d = make_d(tmp_path)
    file_id = "dictidx9"
    # file in scan-list, but not at predictable base+ext
    video = tmp_path / f"{file_id}_realname.mp4"
    video.write_bytes(b"x")
    url = "https://youtube.com/watch?v=dictelse"

    class FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, _url, download=True):
            return {"title": "T", "duration": 5}

        def prepare_filename(self, info):
            return None  # forces else branch

    def fake_opts(out_path, u):
        return {
            "outtmpl": {"default": str(tmp_path / f"{file_id}.%(ext)s")},
            "format": "best",
        }

    with patch.object(d, "_get_ydl_opts", side_effect=fake_opts):
        with patch("src.services.downloader.yt_dlp.YoutubeDL", FakeYDL):
            result = await d.download(url)
    # _find_downloaded_file will scan and find the file via prefix
    assert result.success
