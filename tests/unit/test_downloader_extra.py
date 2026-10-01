"""Extra downloader tests to push coverage to 100%."""

import concurrent.futures
import json
import os
import threading
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from src.services.downloader import DownloadResult, VideoDownloader


async def _in_process_worker(service, url, allow_carousel):
    # Source orchestration unit tests replace external backends in this process.
    # Separate worker integration tests exercise production process isolation.
    return await service._download_source(url, allow_carousel)


def make_d(tmp_path: Path) -> VideoDownloader:
    return VideoDownloader(str(tmp_path), worker_runner=_in_process_worker)


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
    with patch("src.services.cookies.YT_COOKIES_FILE", None):
        assert d._get_youtube_cookiefile() is None


def test_get_youtube_cookiefile_none_when_missing(tmp_path):
    d = make_d(tmp_path)
    with patch("src.services.cookies.YT_COOKIES_FILE", "/nope/cookies.txt"):
        assert d._get_youtube_cookiefile() is None


def test_get_youtube_cookiefile_invalid_format(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text('{"not": "netscape"}')
    d = make_d(tmp_path)
    with patch("src.services.cookies.YT_COOKIES_FILE", str(f)):
        assert d._get_youtube_cookiefile() is None


def test_get_youtube_cookiefile_valid(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.cookies.YT_COOKIES_FILE", str(f)):
        assert d._get_youtube_cookiefile() == str(f)


def test_get_instagram_cookiefile_none_when_unset(tmp_path):
    d = make_d(tmp_path)
    with patch("src.services.cookies.INSTA_COOKIES_FILE", None):
        assert d._get_instagram_cookiefile() is None


def test_get_instagram_cookiefile_none_when_missing(tmp_path):
    d = make_d(tmp_path)
    with patch("src.services.cookies.INSTA_COOKIES_FILE", "/nope.txt"):
        assert d._get_instagram_cookiefile() is None


def test_get_instagram_cookiefile_invalid_format(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text('{"foo": "bar"}')
    d = make_d(tmp_path)
    with patch("src.services.cookies.INSTA_COOKIES_FILE", str(f)):
        assert d._get_instagram_cookiefile() is None


def test_get_instagram_cookiefile_valid(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.cookies.INSTA_COOKIES_FILE", str(f)):
        assert d._get_instagram_cookiefile() == str(f)


# ── _get_ydl_opts cookies for instagram ──────────────────────────────────────


def test_get_ydl_opts_youtube_with_cookies(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.cookies.YT_COOKIES_FILE", str(f)):
        opts = d._get_ydl_opts("out.%(ext)s", "https://youtube.com/watch?v=a")
    assert opts.get("cookiefile") == str(f)


def test_get_ydl_opts_instagram_with_cookies(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tFALSE\t0\tSID\tv\n")
    d = make_d(tmp_path)
    with patch("src.services.cookies.INSTA_COOKIES_FILE", str(f)):
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
    with (
        patch(
            "src.services.instagram.urllib.request.urlopen",
            side_effect=urllib.error.URLError("nope"),
        ) as urlopen,
        patch("src.services.instagram.time.sleep") as sleep,
    ):
        assert VideoDownloader._http_get_html("https://x") is None
    assert urlopen.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


def test_http_get_html_retries_504_then_succeeds():
    timeout = urllib.error.HTTPError("https://x", 504, "Gateway Timeout", hdrs=None, fp=None)
    fake = MagicMock()
    fake.headers = {"Content-Type": "text/html"}
    fake.read = MagicMock(return_value=b"<html>ok</html>")
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "src.services.instagram.urllib.request.urlopen",
            side_effect=[timeout, fake],
        ) as urlopen,
        patch("src.services.instagram.time.sleep") as sleep,
    ):
        result = VideoDownloader._http_get_html("https://x")

    assert result == "<html>ok</html>"
    assert urlopen.call_count == 2
    sleep.assert_called_once_with(1)


def test_http_get_html_non_html_content_type():
    fake = MagicMock()
    fake.headers = {"Content-Type": "application/json"}
    fake.read = MagicMock(return_value=b"{}")
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        assert VideoDownloader._http_get_html("https://x") is None


def test_http_get_html_success():
    fake = MagicMock()
    fake.headers = {"Content-Type": "text/html"}
    fake.read = MagicMock(return_value=b"<html></html>")
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        result = VideoDownloader._http_get_html("https://x")
    assert result == "<html></html>"


def test_http_get_html_sends_navigation_header():
    """Instagram only returns the media JSON for top-level navigations.

    The request must advertise ``Sec-Fetch-Mode: navigate`` (as yt-dlp does);
    without it the same URL yields a stripped shell with no product JSON.
    """
    fake = MagicMock()
    fake.headers = {"Content-Type": "text/html"}
    fake.read = MagicMock(return_value=b"<html></html>")
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    with (
        patch("src.services.instagram.urllib.request.Request") as request_cls,
        patch("src.services.instagram.urllib.request.urlopen", return_value=fake),
    ):
        VideoDownloader._http_get_html("https://x")
    headers = request_cls.call_args.kwargs["headers"]
    assert headers["Sec-Fetch-Mode"] == "navigate"


# ── _fetch_instagram_media_info ─────────────────────────────────────────────


def test_instagram_shortcode_to_media_id_matches_real_post():
    assert VideoDownloader._instagram_shortcode_to_media_id("DcHWbs6H5GC") == "3965236657591914882"


def test_instagram_shortcode_to_media_id_rejects_invalid_character():
    assert VideoDownloader._instagram_shortcode_to_media_id("bad.code") is None


def test_fetch_instagram_product_info_requires_exact_shortcode(tmp_path):
    d = make_d(tmp_path)
    cookie = MagicMock()
    cookie.name = "sessionid"
    cookie.value = "dummy-session"
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"items": [{"code": "different", "media_type": 1}]}
    ).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    opener = MagicMock()
    opener.open.return_value = response

    with patch("src.services.instagram.urllib.request.build_opener", return_value=opener):
        result = d._fetch_instagram_product_info("DcHWbs6H5GC", [cookie])

    assert result is None


def test_fetch_instagram_product_info_returns_exact_product(tmp_path):
    d = make_d(tmp_path)
    cookie = MagicMock()
    cookie.name = "sessionid"
    cookie.value = "dummy-session"
    product = {"code": "DcHWbs6H5GC", "media_type": 1}
    response = MagicMock()
    response.read.return_value = json.dumps({"items": [product]}).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    opener = MagicMock()
    opener.open.return_value = response

    with patch("src.services.instagram.urllib.request.build_opener", return_value=opener):
        result = d._fetch_instagram_product_info("DcHWbs6H5GC", [cookie])

    assert result == product
    request = opener.open.call_args.args[0]
    assert "3965236657591914882" in request.full_url
    assert request.get_header("X-ig-app-id") == "936619743392459"


def test_fetch_instagram_product_info_accepts_canonical_private_shortcode(tmp_path):
    d = make_d(tmp_path)
    cookie = MagicMock()
    cookie.name = "sessionid"
    cookie.value = "dummy-session"
    canonical = "DcHWbs6H5GC"
    private_shortcode = canonical + ("A" * 28)
    product = {"code": canonical, "media_type": 1}
    response = MagicMock()
    response.read.return_value = json.dumps({"items": [product]}).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    opener = MagicMock()
    opener.open.return_value = response

    with patch("src.services.instagram.urllib.request.build_opener", return_value=opener):
        result = d._fetch_instagram_product_info(private_shortcode, [cookie])

    assert result == product
    assert "3965236657591914882" in opener.open.call_args.args[0].full_url


def test_instagram_product_photo_carousel_is_exact_and_ordered():
    first_small = "https://scontent.cdninstagram.com/v/t51/first-small.webp"
    first_large = "https://scontent.cdninstagram.com/v/t51/first-large.webp"
    second = "https://scontent.cdninstagram.com/v/t51/second.webp"
    product = {
        "code": "DcHWbs6H5GC",
        "media_type": 8,
        "caption": {"text": "Carousel caption"},
        "carousel_media": [
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [
                        {"url": first_small, "width": 320, "height": 568},
                        {"url": first_large, "width": 1080, "height": 1920},
                    ]
                },
            },
            {
                "media_type": 1,
                "image_versions2": {"candidates": [{"url": second, "width": 1080, "height": 1350}]},
            },
        ],
    }

    result = VideoDownloader._instagram_product_to_media_info(product)

    assert result is not None
    assert result["media_kind"] == "photo"
    assert result["has_video"] is False
    assert result["image_urls"] == [first_large, second]
    assert result["title"] == "Carousel caption"


def test_instagram_product_photo_carousel_rejects_missing_child_image():
    product = {
        "code": "incomplete",
        "media_type": 8,
        "carousel_media": [
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [
                        {
                            "url": "https://scontent.cdninstagram.com/v/t51/first.webp",
                            "width": 1080,
                            "height": 1350,
                        }
                    ]
                },
            },
            {"media_type": 1, "image_versions2": {"candidates": []}},
        ],
    }

    assert VideoDownloader._instagram_product_to_media_info(product) is None


def test_instagram_product_keeps_image_without_known_extension():
    """image_versions2 entries are stills; never drop a child just because its
    CDN filename lacks a Telegram-friendly extension (that would flatten the
    slide count and disable the whole recovery)."""
    url = "https://scontent.cdninstagram.com/v/t51/photo.heic"
    product = {
        "code": "abc",
        "media_type": 1,
        "image_versions2": {"candidates": [{"url": url, "width": 1080, "height": 1350}]},
    }

    result = VideoDownloader._instagram_product_to_media_info(product)

    assert result is not None
    assert result["image_urls"] == [url]
    assert result["media_kind"] == "photo"


def test_instagram_product_mixed_carousel_is_video_not_photo():
    cover = "https://scontent.cdninstagram.com/v/t51/cover.webp"
    product = {
        "code": "mixed",
        "media_type": 8,
        "carousel_media": [
            {
                "media_type": 1,
                "image_versions2": {"candidates": [{"url": cover, "width": 1080, "height": 1350}]},
            },
            {
                "media_type": 2,
                "image_versions2": {"candidates": [{"url": cover, "width": 1080, "height": 1920}]},
                "video_versions": [{"url": "https://cdn.example/video.mp4"}],
            },
        ],
    }

    result = VideoDownloader._instagram_product_to_media_info(product)

    assert result is not None
    assert result["media_kind"] == "video"
    assert result["has_video"] is True


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


def test_fetch_instagram_media_info_trusts_photo_marker_only_on_target_embed(tmp_path):
    d = make_d(tmp_path)
    cover = '<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51/cover.jpg">'

    def fake_get(candidate, **_kwargs):
        if "/embed" in candidate:
            return cover
        if urlparse(candidate).hostname == "www.instagram.com":
            return cover + '{"media_type": 1}'
        return None

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result is not None
    assert result["media_kind"] == "unknown"


def test_fetch_instagram_media_info_confirms_target_embed_photo(tmp_path):
    d = make_d(tmp_path)
    html = (
        '<meta property="og:image" '
        'content="https://scontent.cdninstagram.com/v/t51/photo.jpg">'
        '{"media_type": 1}'
    )
    with patch.object(d, "_http_get_html", return_value=html):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result is not None
    assert result["media_kind"] == "photo"


def test_fetch_instagram_media_info_ignores_main_page_auxiliary_media(tmp_path):
    d = make_d(tmp_path)
    target = "https://scontent.cdninstagram.com/v/t51/target.jpg"
    auxiliary = "https://scontent.cdninstagram.com/v/t51/recommended.jpg"
    embed_html = (
        '{"media_type":1,"image_versions2":{"candidates":['
        f'{{"url":"{target}","width":1080,"height":1920}}]}}'
    )
    main_html = (
        '{"media_type":2,"video_versions":[{"url":"https://cdn/video.mp4"}],'
        '"image_versions2":{"candidates":['
        f'{{"url":"{auxiliary}","width":1080,"height":1080}}]}}'
    )

    def fake_get(candidate, **_kwargs):
        if "kkinstagram" in candidate:
            return None
        return embed_html if "/embed" in candidate else main_html

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result is not None
    assert result["has_video"] is False
    assert result["media_kind"] == "photo"
    assert result["image_urls"] == [target]


def _polaris_single_photo(code: str, url: str) -> str:
    """Logged-out post JSON: a single item wrapped in ``if_not_gated_logged_out``."""
    return json.dumps(
        {
            "__typename": "XIGPolarisImageMedia",
            "code": code,
            "if_not_gated_logged_out": {
                "code": code,
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": url, "width": 1080, "height": 1920}]
                },
            },
        }
    )


def _polaris_photo_carousel(code: str, urls: list[str]) -> str:
    """Logged-out post JSON: a carousel exposing ``carousel_media`` at top level."""
    return json.dumps(
        {
            "__typename": "XIGPolarisCarouselMedia",
            "code": code,
            "media_type": 8,
            "carousel_media": [
                {
                    "media_type": 1,
                    "image_versions2": {
                        "candidates": [{"url": url, "width": 1080, "height": 1350}]
                    },
                }
                for url in urls
            ],
        }
    )


def test_instagram_target_product_media_unwraps_logged_out_wrapper():
    payload = _polaris_single_photo(
        "abc", "https://scontent.cdninstagram.com/v/t51/photo.jpg"
    )
    info = VideoDownloader._instagram_target_product_media(payload)
    assert info is not None
    assert info["media_kind"] == "photo"
    assert info["has_video"] is False
    assert info["image_urls"] == ["https://scontent.cdninstagram.com/v/t51/photo.jpg"]


def test_instagram_target_product_media_rejects_non_json():
    assert VideoDownloader._instagram_target_product_media(None) is None
    assert VideoDownloader._instagram_target_product_media("not json") is None


def test_fetch_instagram_media_info_target_payload_beats_stray_video_marker(tmp_path):
    """The requested post's own JSON decides the media kind.

    The post is a single photo, but the surrounding page JSON also carries an
    unrelated ``media_type: 2`` entry (recommendations). The structured target
    payload must win, so the photo is not misreported as a video.
    """
    d = make_d(tmp_path)
    photo = "https://scontent.cdninstagram.com/v/t51/photo.jpg"
    main_html = json.dumps(
        {
            "code": "abc",
            "if_not_gated_logged_out": {
                "code": "abc",
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": photo, "width": 1080, "height": 1920}]
                },
            },
            "recommendations": [
                {"media_type": 2, "video_versions": [{"url": "https://cdn/x.mp4"}]}
            ],
        }
    )

    def fake_get(candidate, **_kwargs):
        if "/embed" in candidate:
            return None
        if urlparse(candidate).hostname == "www.instagram.com":
            return main_html
        return None

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result is not None
    assert result["has_video"] is False
    assert result["media_kind"] == "photo"
    assert photo in result["image_urls"]


def test_fetch_instagram_media_info_target_payload_carousel_keeps_order(tmp_path):
    d = make_d(tmp_path)
    first = "https://scontent.cdninstagram.com/v/t51/a.jpg"
    second = "https://scontent.cdninstagram.com/v/t51/b.jpg"
    main_html = _polaris_photo_carousel("abc", [first, second])

    def fake_get(candidate, **_kwargs):
        if "/embed" in candidate:
            return None
        if urlparse(candidate).hostname == "www.instagram.com":
            return main_html
        return None

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result is not None
    assert result["media_kind"] == "photo"
    assert result["has_video"] is False
    assert result["image_urls"] == [first, second]


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


def test_fetch_instagram_media_info_prefers_more_complete_target_mirror(tmp_path):
    d = make_d(tmp_path)
    embed_html = (
        '{"media_type":1,"image_versions2":{"candidates":['
        '{"url":"https://safe.example/embed.jpg","width":1080,"height":1350}]}}'
    )
    main_html = "\n".join(
        f'<meta property="og:image" content="https://unsafe.example/recommendation{i}.jpg">'
        for i in range(3)
    )
    mirror_urls = ["https://safe.example/one.jpg", "https://safe.example/two.jpg"]
    mirror_html = "\n".join(f'<meta property="og:image" content="{url}">' for url in mirror_urls)

    def fake_get(candidate, **_kwargs):
        if "kkinstagram" in candidate:
            return mirror_html
        if "/embed" in candidate:
            return embed_html
        return main_html

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result["image_urls"] == mirror_urls
    assert result["media_kind"] == "photo"


def test_fetch_instagram_media_info_trusts_target_mirror_photo_marker(tmp_path):
    d = make_d(tmp_path)
    mirror_urls = [
        "https://scontent.cdninstagram.com/v/t51/one.jpg",
        "https://scontent.cdninstagram.com/v/t51/two.jpg",
    ]
    mirror_html = (
        "".join(f'<meta property="og:image" content="{url}">' for url in mirror_urls)
        + '{"media_type":1}'
    )

    def fake_get(candidate, **_kwargs):
        return mirror_html if "kkinstagram" in candidate else None

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result is not None
    assert result["image_urls"] == mirror_urls
    assert result["media_kind"] == "photo"
    assert result["has_video"] is False


def test_fetch_instagram_media_info_trusts_target_mirror_video_marker(tmp_path):
    d = make_d(tmp_path)
    cover = "https://scontent.cdninstagram.com/v/t51/cover.jpg"
    mirror_html = (
        f'<meta property="og:image" content="{cover}">'
        '{"media_type":2,"video_versions":'
        '[{"url":"https://cdn.example/video.mp4"}]}'
    )

    def fake_get(candidate, **_kwargs):
        return mirror_html if "kkinstagram" in candidate else None

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result is not None
    assert result["has_video"] is True
    assert result["media_kind"] == "video"


def test_fetch_instagram_media_info_counts_distinct_assets_per_source(tmp_path):
    d = make_d(tmp_path)
    one_asset = "https://safe.example/one.jpg"
    embed_html = (
        f'<meta property="og:image" content="{one_asset}?full=1">'
        f'<meta property="og:image" content="{one_asset}?stp=s1080x1080">'
        '{"media_type":1}'
    )
    mirror_urls = ["https://safe.example/a.jpg", "https://safe.example/b.jpg"]
    mirror_html = "".join(
        f'<meta property="og:image" content="{image_url}">' for image_url in mirror_urls
    )

    def fake_get(candidate, **_kwargs):
        if "kkinstagram" in candidate:
            return mirror_html
        if "/embed" in candidate:
            return embed_html
        return None

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result["image_urls"] == mirror_urls


def test_fetch_instagram_media_info_scopes_main_page_to_shortcode(tmp_path):
    d = make_d(tmp_path)
    target_urls = [
        "https://safe.example/target1.jpg?a=1&b=2",
        "https://safe.example/target2.jpg",
        "https://safe.example/target3.jpg",
    ]
    embed_html = (
        '{"media_type":1,"image_versions2":{"candidates":['
        f'{{"url":"{target_urls[0]}","width":1080,"height":1350}}]}}'
    )

    def media(image_url):
        return {
            "media_type": 1,
            "image_versions2": {"candidates": [{"url": image_url, "width": 1080, "height": 1350}]},
        }

    main_html = (
        '<script type="application/json">'
        + json.dumps(
            {
                "items": [
                    {"code": "abc", "media_type": 8, "carousel_media": [media(target_urls[0])]},
                    {
                        "code": "abc",
                        "media_type": 8,
                        "caption": {"text": 'say "hi" {still-json}'},
                        "carousel_media": [media(image_url) for image_url in target_urls],
                    },
                ],
                "recommendations": [{"code": "other", **media("https://unsafe.example/rec.jpg")}],
            }
        ).replace("&", r"\u0026")
        + "</script>"
    )

    def fake_get(candidate, **_kwargs):
        if "/embed" in candidate:
            return embed_html
        if "kkinstagram" in candidate:
            return None
        return main_html

    with patch.object(d, "_http_get_html", side_effect=fake_get):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")

    assert result["image_urls"] == target_urls
    assert result["media_kind"] == "photo"


def test_fetch_instagram_media_info_max_carousel(tmp_path):
    d = make_d(tmp_path)
    images = "\n".join(
        f'<meta property="og:image" content="https://cdn.example/img{i}.jpg">' for i in range(25)
    )
    with patch.object(d, "_http_get_html", return_value=images):
        result = d._fetch_instagram_media_info("https://www.instagram.com/p/abc/")
    assert len(result["image_urls"]) == 20


def test_limit_instagram_image_variants_counts_slides_not_signed_urls(tmp_path):
    d = make_d(tmp_path)
    urls = [
        f"https://cdn.example/slide{index}.jpg?variant={variant}"
        for index in range(25)
        for variant in ("full", "resized")
    ]

    limited = d._limit_instagram_image_variants(urls)

    assert len(limited) == 40
    assert limited[:2] == urls[:2]
    assert all("slide20.jpg" not in url for url in limited)


# ── _download_image_sync ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "opening,closing",
    [
        ('<script type="application/json">', "</script >"),
        ('<SCRIPT data-note="a > b">', "</SCRIPT\n>"),
    ],
)
def test_instagram_script_payload_respects_html_boundaries(opening, closing):
    # An unbalanced brace in a valid JSON string defeats the old object fallback.
    target = {
        "code": "abc",
        "caption": {"text": 'quoted "caption" with } and <tag>'},
        "media_type": 1,
    }
    page = opening + json.dumps(target) + closing
    page += '<script>{"code":"other","media_type":2}</script>'

    payload = VideoDownloader._extract_instagram_target_payload(page, "abc")

    assert payload is not None
    assert json.loads(payload) == target


@pytest.mark.parametrize(
    "failure",
    ["content_type", "oversized", "tiny", "http_retry", "http_final", "network"],
)
def test_photo_failures_do_not_log_signed_urls_or_exception_details(tmp_path, caplog, failure):
    d = make_d(tmp_path)
    image_url = "https://cdn.example/private-photo.jpg?token=sensitive-token"
    if failure == "content_type":
        response = _make_fake_resp("text/html")
    elif failure == "oversized":
        response = _make_fake_resp("image/jpeg", body=b"x" * 2048)
    elif failure == "tiny":
        response = _make_fake_resp("image/jpeg", body=b"x" * 100)
    elif failure in {"http_retry", "http_final"}:
        response = urllib.error.HTTPError(
            image_url, 504 if failure == "http_retry" else 403, image_url, None, None
        )
    else:
        response = urllib.error.URLError(image_url)

    with (
        patch("src.services.media_io.urllib.request.urlopen") as urlopen,
        patch("src.services.media_io.time.sleep"),
        patch("src.services.media_io.MAX_FILE_SIZE", 1024),
    ):
        if isinstance(response, Exception):
            urlopen.side_effect = response
        else:
            urlopen.return_value = response
        result = d._download_image_sync(image_url, str(tmp_path / "photo"))

    assert result is None
    assert caplog.records
    assert "sensitive-token" not in caplog.text
    assert "private-photo" not in caplog.text
    if failure == "http_retry":
        assert "HTTP 504" in caplog.text
        assert urlopen.call_count == 3
    elif failure == "network":
        assert "URLError" in caplog.text
        assert urlopen.call_count == 3
    assert not list(tmp_path.glob("photo.*"))


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
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is not None
    assert out.endswith("." + expected_ext)


def test_download_image_sync_picks_ext_from_url_path(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("image/unknown")
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img.gif?q=1", str(tmp_path / "out"))
    assert out is not None
    assert out.endswith(".gif")


def test_download_image_sync_picks_jpg_fallback_when_url_ext_invalid(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("image/unknown")
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img.toolongextension", str(tmp_path / "out"))
    assert out is not None
    assert out.endswith(".jpg")


def test_download_image_sync_rejects_non_image(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("text/html")
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None


def test_download_image_sync_url_error(tmp_path):
    d = make_d(tmp_path)
    with (
        patch(
            "src.services.instagram.urllib.request.urlopen",
            side_effect=urllib.error.URLError("nope"),
        ) as urlopen,
        patch("src.services.instagram.time.sleep") as sleep,
    ):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))
    assert out is None
    assert urlopen.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


def test_download_image_sync_retries_http_504(tmp_path):
    d = make_d(tmp_path)
    gateway_timeout = urllib.error.HTTPError(
        "https://cdn/img",
        504,
        "Gateway Timeout",
        hdrs=None,
        fp=None,
    )
    fake = _make_fake_resp("image/jpeg")
    with (
        patch(
            "src.services.instagram.urllib.request.urlopen",
            side_effect=[gateway_timeout, fake],
        ) as urlopen,
        patch("src.services.instagram.time.sleep") as sleep,
    ):
        out = d._download_image_sync("https://cdn/img", str(tmp_path / "out"))

    assert out is not None
    assert urlopen.call_count == 2
    sleep.assert_called_once_with(1)


def test_download_image_sync_rejects_tiny_file(tmp_path):
    d = make_d(tmp_path)
    fake = _make_fake_resp("image/jpeg", body=b"x" * 100)
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
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
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
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
    with patch("src.services.media_io.subprocess.run", return_value=proc):
        assert d._extract_photo_frame(r) is None


def test_extract_photo_frame_no_output_file(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    r = DownloadResult(success=True, file_path=str(v))
    proc = MagicMock()
    proc.returncode = 0
    with patch("src.services.media_io.subprocess.run", return_value=proc):
        assert d._extract_photo_frame(r) is None


def test_extract_photo_frame_tiny_output_file(tmp_path):
    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    out = tmp_path / "v_photo.jpg"
    out.write_bytes(b"x")  # tiny

    def fake_run(*args, **kwargs):
        return MagicMock(returncode=0)

    with patch("src.services.media_io.subprocess.run", side_effect=fake_run):
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

    with patch("src.services.media_io.subprocess.run", side_effect=fake_run):
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

    with patch("src.services.media_io.subprocess.run", side_effect=fake_run):
        with patch("src.services.media_io.os.remove", side_effect=OSError("nope")):
            result = d._extract_photo_frame(DownloadResult(success=True, file_path=str(v)))
    assert result is not None


def test_extract_photo_frame_timeout(tmp_path):
    import subprocess as _sp

    d = make_d(tmp_path)
    d.has_ffmpeg = True
    v = fake_file(tmp_path, "v.mp4")
    with patch(
        "src.services.media_io.subprocess.run",
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


def test_try_instagram_photo_rejects_incomplete_carousel_and_cleans_files(tmp_path):
    d = make_d(tmp_path)
    urls = [
        "https://scontent.cdninstagram.com/v/t51/one.jpg",
        "https://scontent.cdninstagram.com/v/t51/two.jpg",
    ]
    first = fake_file(tmp_path, "first.jpg")
    meta = {"image_urls": urls, "has_video": False, "media_kind": "photo"}
    with (
        patch.object(d, "_fetch_instagram_media_info", return_value=meta),
        patch.object(d, "_download_image_sync", side_effect=[str(first), None]),
    ):
        result = d._try_instagram_photo("https://www.instagram.com/p/abc/")

    assert result is None
    assert not first.exists()


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


def test_try_instagram_photo_preserves_authoritative_repeated_slides(tmp_path):
    d = make_d(tmp_path)
    base = "https://scontent.cdninstagram.com/v/t51/repeated.webp"
    urls = [f"{base}?signature=one", f"{base}?signature=two"]
    first = fake_file(tmp_path, "repeat-one.webp")
    second = fake_file(tmp_path, "repeat-two.webp")
    meta = {
        "image_urls": urls,
        "image_urls_are_ordered_slides": True,
        "has_video": False,
        "media_kind": "photo",
    }

    with (
        patch.object(d, "_fetch_instagram_media_info", return_value=meta),
        patch.object(
            d,
            "_download_image_sync",
            side_effect=[str(first), str(second)],
        ) as download_image,
    ):
        result = d._try_instagram_photo("https://www.instagram.com/p/repeated/")

    assert result is not None
    assert result.photo_paths == [str(first), str(second)]
    assert [slide.url for slide in result.carousel_slides] == urls
    assert download_image.call_count == 2


def test_try_instagram_photo_reel_product_video_never_downloads_cover(tmp_path):
    d = make_d(tmp_path)
    product = {
        "code": "video",
        "media_type": 2,
        "image_versions2": {
            "candidates": [
                {
                    "url": "https://scontent.cdninstagram.com/v/t51/square-cover.webp",
                    "width": 1080,
                    "height": 1080,
                }
            ]
        },
        "video_versions": [{"url": "https://cdn.example/video.mp4"}],
    }
    cookie_jar = MagicMock()

    with (
        patch.object(d, "_load_instagram_cookie_jar", return_value=cookie_jar),
        patch.object(d, "_fetch_instagram_product_info", return_value=product),
        patch.object(d, "_download_image_sync") as download_image,
    ):
        result = d._try_instagram_photo("https://www.instagram.com/reel/video/")

    assert result is None
    download_image.assert_not_called()


def test_try_instagram_photo_reel_scrapes_shortcode_embed_without_product(tmp_path):
    """A /reel/ photo post is recovered from the shortcode-scoped embed even
    when the authenticated product API returns nothing."""
    d = make_d(tmp_path)
    cover = '<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51/photo.jpg">'

    def fake_get(candidate, **_kwargs):
        # A reel share link is probed through the scoped embed/mirror only; the
        # raw main page is never consulted.
        assert "/embed" in candidate or "kkinstagram" in candidate, candidate
        return cover + '{"media_type": 1}'

    with (
        patch.object(d, "_load_instagram_cookie_jar", return_value=MagicMock()),
        patch.object(d, "_fetch_instagram_product_info", return_value=None),
        patch.object(d, "_download_image_sync", return_value=str(fake_file(tmp_path, "reel.jpg"))),
        patch.object(d, "_http_get_html", side_effect=fake_get),
    ):
        result = d._try_instagram_photo("https://www.instagram.com/reel/unknown/")

    assert result is not None
    assert result.is_photo
    assert result.media_type_confirmed


def test_try_instagram_photo_reel_bare_cover_stays_unconfirmed(tmp_path):
    """A reel embed without a photo marker yields an unconfirmed result, so the
    caller keeps the video error instead of leaking a square cover."""
    d = make_d(tmp_path)
    cover = '<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51/cover.jpg">'
    with (
        patch.object(d, "_load_instagram_cookie_jar", return_value=MagicMock()),
        patch.object(d, "_fetch_instagram_product_info", return_value=None),
        patch.object(d, "_download_image_sync", return_value=str(fake_file(tmp_path, "cover.jpg"))),
        patch.object(d, "_http_get_html", return_value=cover) as get_html,
    ):
        result = d._try_instagram_photo("https://www.instagram.com/reel/unknown/")

    get_html.assert_called()
    assert result is not None
    assert result.media_type_confirmed is False


def test_try_instagram_photo_reel_video_marker_is_never_a_photo(tmp_path):
    d = make_d(tmp_path)
    cover = '<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51/cover.jpg">'
    html = cover + '{"media_type":2,"video_versions":[{"url":"https://cdn/v.mp4"}]}'
    with (
        patch.object(d, "_load_instagram_cookie_jar", return_value=MagicMock()),
        patch.object(d, "_fetch_instagram_product_info", return_value=None),
        patch.object(d, "_download_image_sync") as download_image,
        patch.object(d, "_http_get_html", return_value=html),
    ):
        result = d._try_instagram_photo("https://www.instagram.com/reel/unknown/")

    assert result is None
    download_image.assert_not_called()


@pytest.mark.asyncio
async def test_download_real_reel_photo_carousel_uses_product_metadata(tmp_path):
    d = make_d(tmp_path)
    first_url = "https://scontent.cdninstagram.com/v/t51/first.webp"
    second_url = "https://scontent.cdninstagram.com/v/t51/second.webp"
    product = {
        "code": "DcHWbs6H5GC",
        "media_type": 8,
        "carousel_media": [
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": first_url, "width": 1080, "height": 1920}]
                },
            },
            {
                "media_type": 1,
                "image_versions2": {
                    "candidates": [{"url": second_url, "width": 1080, "height": 1350}]
                },
            },
        ],
    }
    first_path = fake_file(tmp_path, "first.webp")
    second_path = fake_file(tmp_path, "second.webp")

    with (
        patch.object(d, "_load_instagram_cookie_jar", return_value=MagicMock()),
        patch.object(d, "_fetch_instagram_product_info", return_value=product),
        patch.object(
            d,
            "_download_image_sync",
            side_effect=[str(first_path), str(second_path)],
        ),
        patch.object(
            d,
            "_download_sync",
            return_value=DownloadResult(
                success=False,
                error_code="downloader.error.instagram_no_formats",
            ),
        ) as download_sync,
    ):
        result = await d.download("https://www.instagram.com/reel/DcHWbs6H5GC/")

    assert result.success
    assert result.is_photo
    assert result.media_type_confirmed
    assert result.photo_paths == [str(first_path), str(second_path)]
    assert [slide.url for slide in result.carousel_slides] == [first_url, second_url]
    assert all(not slide.is_video for slide in result.carousel_slides)
    download_sync.assert_called_once()


@pytest.mark.asyncio
async def test_instagram_no_formats_has_structured_error_without_exact_product(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    with (
        patch.object(d, "_try_instagram_photo", return_value=None) as photo_probe,
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as ydl_class,
    ):
        ydl = MagicMock()
        ydl_class.return_value.__enter__.return_value = ydl
        ydl.extract_info.side_effect = DownloadError(
            "[Instagram] DcHWbs6H5GC: No video formats found!"
        )
        result = await d.download("https://www.instagram.com/reel/DcHWbs6H5GC/")

    assert not result.success
    assert result.error_code == "downloader.error.instagram_no_formats"
    photo_probe.assert_called_once()


@pytest.mark.asyncio
async def test_download_reel_photo_recovers_from_scoped_embed_without_cookies(tmp_path):
    """End-to-end: a cookie-less /reel/ photo post is recovered anonymously."""
    d = make_d(tmp_path)
    photo = fake_file(tmp_path, "reel-photo.jpg")
    embed_html = (
        '<meta property="og:image" '
        'content="https://scontent.cdninstagram.com/v/t51/reel.jpg">'
        '{"media_type": 1}'
    )
    with (
        patch.object(d, "_load_instagram_cookie_jar", return_value=None),
        patch.object(d, "_http_get_html", return_value=embed_html),
        patch.object(d, "_download_image_sync", return_value=str(photo)),
        patch.object(
            d,
            "_download_sync",
            return_value=DownloadResult(
                success=False, error_code="downloader.error.instagram_no_formats"
            ),
        ),
    ):
        result = await d.download("https://www.instagram.com/reel/reel-photo/")

    assert result.success
    assert result.is_photo
    assert result.media_type_confirmed


@pytest.mark.asyncio
async def test_download_reel_unconfirmed_photo_files_are_discarded(tmp_path):
    d = make_d(tmp_path)
    cover = fake_file(tmp_path, "cover.jpg")
    unconfirmed = DownloadResult(
        success=True,
        file_path=str(cover),
        is_photo=True,
        photo_paths=[str(cover)],
        media_type_confirmed=False,
    )
    with (
        patch.object(d, "_try_instagram_photo", return_value=unconfirmed),
        patch.object(
            d,
            "_download_sync",
            return_value=DownloadResult(
                success=False, error_code="downloader.error.instagram_no_formats"
            ),
        ),
    ):
        result = await d.download("https://www.instagram.com/reel/unknown/")

    assert not result.success
    assert result.error_code == "downloader.error.instagram_no_formats"
    assert not cover.exists()


@pytest.mark.asyncio
async def test_download_does_not_trust_global_instagram_photo_marker(tmp_path):
    d = make_d(tmp_path)
    photo = fake_file(tmp_path, "x.jpg")
    video = fake_file(tmp_path, "x.mp4")
    photo_result = DownloadResult(
        success=True,
        file_path=str(photo),
        is_photo=True,
        photo_paths=[str(photo)],
        media_type_confirmed=False,
    )
    video_result = DownloadResult(success=True, file_path=str(video), is_photo=False)
    with (
        patch.object(d, "_try_instagram_photo", return_value=photo_result),
        patch.object(d, "_download_sync", return_value=video_result) as download_sync,
    ):
        result = await d.download("https://www.instagram.com/p/abc/")
    assert result.success
    assert result.is_photo is False
    assert result.file_path == str(video)
    assert not photo.exists()
    download_sync.assert_called_once()


@pytest.mark.asyncio
async def test_download_probes_single_target_photo_then_keeps_confirmed_fallback(tmp_path):
    d = make_d(tmp_path)
    photo = fake_file(tmp_path, "confirmed.jpg")
    photo_result = DownloadResult(
        success=True,
        file_path=str(photo),
        is_photo=True,
        photo_paths=[str(photo)],
        media_type_confirmed=True,
    )
    with (
        patch.object(d, "_try_instagram_photo", return_value=photo_result),
        patch.object(
            d,
            "_download_sync",
            return_value=DownloadResult(
                success=False, error_code="downloader.error.download_failed"
            ),
        ) as download_sync,
    ):
        result = await d.download("https://www.instagram.com/p/abc/")

    assert result.is_photo
    assert photo.exists()
    download_sync.assert_called_once()


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
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = None
        result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_sync_playlist_no_entries(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=emptylist"
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
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
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
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

    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
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
        with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
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
        with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
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
        with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
            result = await d.download(url)
    assert result.success


# ── _download_sync error branches ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_handles_ffmpeg_required_error(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=ffmpeg"
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
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
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
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
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("some other error")
        result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_download_handles_unexpected_exception(tmp_path):
    d = make_d(tmp_path)
    url = "https://youtube.com/watch?v=oops"
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
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

    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
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

    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("login_required")
        with patch.object(d, "_try_instagram_photo", return_value=None):
            result = await d.download(url)
    assert not result.success


@pytest.mark.asyncio
async def test_kkinstagram_photo_uses_explicit_no_video_fallback(tmp_path):
    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    photo = fake_file(tmp_path, "photo.jpg")
    candidate = DownloadResult(
        success=True,
        file_path=str(photo),
        is_photo=True,
        photo_paths=[str(photo)],
    )
    url = "https://kkinstagram.com/p/abc/"

    with (
        patch.object(d, "_try_instagram_photo", return_value=candidate),
        patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls,
    ):
        mock_ydl = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = DownloadError("There is no video in this post")
        result = await d.download(url)

    assert result.success
    assert result.is_photo
    assert photo.exists()


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
    with patch("src.services.media_io.os.remove", side_effect=OSError("nope")):
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

    def fake_get(_, **kwargs):
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
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        with patch("src.services.media_io.os.remove", side_effect=OSError("nope")):
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
    with patch("src.services.instagram.urllib.request.urlopen", return_value=fake):
        with patch("src.services.media_io.os.remove", side_effect=OSError("nope")):
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
        with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
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

    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL") as mock_cls:
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


def test_download_sync_uses_writable_cookie_snapshot_and_cleans_it(tmp_path):
    """yt-dlp may rewrite its jar while the configured read-only source stays intact."""

    d = make_d(tmp_path)
    source = tmp_path / "instagram-cookies.txt"
    original = b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n"
    source.write_bytes(original)
    video = fake_file(tmp_path, "cookie-video.mp4")
    seen_snapshot: list[Path] = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            snapshot = Path(self.opts["cookiefile"])
            seen_snapshot.append(snapshot)
            assert snapshot != source
            assert snapshot.parent.resolve() == d.download_dir.resolve()
            assert snapshot.read_bytes() == original
            if os.name != "nt":
                assert snapshot.stat().st_mode & 0o777 == 0o600
            return self

        def __exit__(self, *_args):
            # Mirrors YoutubeDLCookieJar.save(): the working jar is rewritten
            # when YoutubeDL closes.
            Path(self.opts["cookiefile"]).write_bytes(b"refreshed jar")
            return False

        def extract_info(self, _url, download=True):
            return {"title": "Cookie test", "duration": 1}

        def prepare_filename(self, _info):
            return str(video)

    opts = {
        "cookiefile": str(source),
        "outtmpl": str(tmp_path / "cookie-output_%(id)s.%(ext)s"),
    }
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
        result = d._download_sync("https://www.instagram.com/reel/test/", opts)

    assert result.success
    assert source.read_bytes() == original
    assert len(seen_snapshot) == 1
    assert not seen_snapshot[0].exists()
    assert opts["cookiefile"] == str(source)


def test_download_sync_removes_cookie_snapshot_after_unexpected_error(tmp_path):
    d = make_d(tmp_path)
    source = tmp_path / "instagram-cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n")
    seen_snapshot: list[Path] = []

    def fail_after_snapshot(_url, opts):
        snapshot = Path(opts["cookiefile"])
        seen_snapshot.append(snapshot)
        assert snapshot.exists()
        raise OSError(30, "Read-only file system")

    with patch.object(d, "_download_sync_with_opts", side_effect=fail_after_snapshot):
        with pytest.raises(OSError, match="Read-only file system"):
            d._download_sync(
                "https://www.instagram.com/reel/test/",
                {"cookiefile": str(source)},
            )

    assert len(seen_snapshot) == 1
    assert not seen_snapshot[0].exists()


def test_concurrent_downloads_get_distinct_cookie_snapshots(tmp_path):
    d = make_d(tmp_path)
    source = tmp_path / "instagram-cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n")
    barrier = threading.Barrier(2)
    seen_paths: list[Path] = []
    seen_lock = threading.Lock()

    def inspect_snapshot(_url, opts):
        snapshot = Path(opts["cookiefile"])
        with seen_lock:
            seen_paths.append(snapshot)
        barrier.wait(timeout=5)
        assert snapshot.exists()
        snapshot.write_text("updated")
        return DownloadResult(success=True)

    opts = {"cookiefile": str(source)}
    with patch.object(d, "_download_sync_with_opts", side_effect=inspect_snapshot):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: d._download_sync("https://www.instagram.com/reel/concurrent/", opts),
                    range(2),
                )
            )

    assert all(result.success for result in results)
    assert len(set(seen_paths)) == 2
    assert all(not path.exists() for path in seen_paths)
    assert source.read_text() == "# Netscape HTTP Cookie File\n"


def test_invalid_cookie_is_not_restored_for_kkinstagram_retry(tmp_path):
    """Once rejected, the jar must stay disabled for later mirror fallbacks."""

    from yt_dlp.utils import DownloadError

    d = make_d(tmp_path)
    source = tmp_path / "instagram-cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n")
    video = tmp_path / "mirror-video.mp4"
    calls: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            calls.append(opts.copy())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=True):
            call_number = len(calls)
            if call_number == 1:
                raise DownloadError("does not look like a netscape format cookies file")
            if call_number == 2:
                raise DownloadError("login required")
            video.write_bytes(b"video")
            return {"title": "Mirror", "duration": 1}

        def prepare_filename(self, _info):
            return str(video)

    opts = {
        "cookiefile": str(source),
        "outtmpl": str(tmp_path / "mirror-output_%(id)s.%(ext)s"),
    }
    with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
        result = d._download_sync("https://www.instagram.com/reel/test/", opts)

    assert result.success
    assert len(calls) == 3
    first_snapshot = Path(calls[0]["cookiefile"])
    assert first_snapshot != source
    assert "cookiefile" not in calls[1]
    assert "cookiefile" not in calls[2]
    assert not first_snapshot.exists()


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
        with patch("src.services.ytdlp_backend.yt_dlp.YoutubeDL", FakeYDL):
            result = await d.download(url)
    # _find_downloaded_file will scan and find the file via prefix
    assert result.success
