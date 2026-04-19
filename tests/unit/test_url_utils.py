import pytest

from src.services.url_utils import (
    INSTAGRAM_AUTH_ERROR_MARKERS,
    build_kkinstagram_url,
    get_platform_name,
    get_url_hash,
    is_instagram_photo_candidate_url,
    is_instagram_post_url,
    is_instagram_url,
    is_kkinstagram_url,
    is_supported_url,
    is_youtube_url,
    normalize_url,
    should_retry_with_kkinstagram,
)

# --- normalize_url ---


def test_normalize_url_removes_utm_source():
    url = "https://youtube.com/watch?v=abc&utm_source=telegram"
    assert "utm_source" not in normalize_url(url)


def test_normalize_url_removes_si_param():
    url = "https://youtu.be/abc?si=xyz123"
    assert "si=" not in normalize_url(url)


def test_normalize_url_removes_feature_param():
    url = "https://youtube.com/watch?v=abc&feature=share"
    assert "feature=" not in normalize_url(url)


def test_normalize_url_removes_ref_param():
    url = "https://youtube.com/watch?v=abc&ref=home"
    assert "ref=" not in normalize_url(url)


def test_normalize_url_keeps_video_id():
    url = "https://youtube.com/watch?v=abc123&utm_source=test"
    normalized = normalize_url(url)
    assert "v=abc123" in normalized


def test_normalize_url_unchanged_when_no_tracking():
    url = "https://youtube.com/watch?v=abc123"
    assert normalize_url(url) == url


# --- get_url_hash ---


def test_get_url_hash_returns_16_chars():
    assert len(get_url_hash("https://youtube.com/watch?v=abc")) == 16


def test_get_url_hash_same_for_url_with_tracking():
    base = "https://youtube.com/watch?v=abc"
    with_utm = "https://youtube.com/watch?v=abc&utm_source=share"
    assert get_url_hash(base) == get_url_hash(with_utm)


def test_get_url_hash_different_for_different_urls():
    url1 = "https://youtube.com/watch?v=aaa"
    url2 = "https://youtube.com/watch?v=bbb"
    assert get_url_hash(url1) != get_url_hash(url2)


def test_get_url_hash_deterministic():
    url = "https://youtube.com/watch?v=abc"
    assert get_url_hash(url) == get_url_hash(url)


# --- is_supported_url ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.instagram.com/reel/ABC123/",
        "https://www.instagram.com/p/ABC123/",
        "https://www.tiktok.com/@user/video/123",
        "https://vm.tiktok.com/ABC/",
        "https://twitter.com/user/status/123",
        "https://x.com/user/status/123",
    ],
)
def test_is_supported_url_true(url):
    assert is_supported_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/video",
        "https://vimeo.com/123",
        "https://dailymotion.com/video/abc",
        "not a url at all",
        "",
    ],
)
def test_is_supported_url_false(url):
    assert is_supported_url(url) is False


# --- get_platform_name ---


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc", "YouTube"),
        ("https://youtu.be/abc", "YouTube"),
        ("https://www.instagram.com/reel/abc/", "Instagram"),
        ("https://www.tiktok.com/@user/video/123", "TikTok"),
        ("https://vm.tiktok.com/abc", "TikTok"),
        ("https://twitter.com/user/status/123", "X/Twitter"),
        ("https://x.com/user/status/123", "X/Twitter"),
        ("https://example.com/video", "Unknown"),
    ],
)
def test_get_platform_name(url, expected):
    assert get_platform_name(url) == expected


# --- is_youtube_url ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://YOUTUBE.COM/watch?v=abc",
    ],
)
def test_is_youtube_url_true(url):
    assert is_youtube_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/abc/",
        "https://www.tiktok.com/@user/video/123",
        "https://twitter.com/user/status/123",
    ],
)
def test_is_youtube_url_false(url):
    assert is_youtube_url(url) is False


# --- is_instagram_url ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/abc/",
        "https://instagram.com/p/abc/",
    ],
)
def test_is_instagram_url_true(url):
    assert is_instagram_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://kkinstagram.com/reel/abc/",
        "https://notinstagram.com/reel/abc/",
    ],
)
def test_is_instagram_url_false(url):
    assert is_instagram_url(url) is False


# --- is_kkinstagram_url ---


def test_is_kkinstagram_url_true():
    assert is_kkinstagram_url("https://kkinstagram.com/reel/abc/") is True


def test_is_kkinstagram_url_false():
    assert is_kkinstagram_url("https://instagram.com/reel/abc/") is False


# --- build_kkinstagram_url ---


def test_build_kkinstagram_url_replaces_host():
    url = "https://www.instagram.com/reel/ABC123/"
    result = build_kkinstagram_url(url)
    assert result is not None
    assert "kkinstagram.com" in result
    assert "/reel/ABC123/" in result


def test_build_kkinstagram_url_returns_none_for_non_instagram():
    assert build_kkinstagram_url("https://www.youtube.com/watch?v=abc") is None


# --- should_retry_with_kkinstagram ---


@pytest.mark.parametrize("marker", INSTAGRAM_AUTH_ERROR_MARKERS)
def test_should_retry_with_kkinstagram_on_auth_error(marker):
    url = "https://www.instagram.com/reel/abc/"
    assert should_retry_with_kkinstagram(url, marker) is True


def test_should_retry_with_kkinstagram_false_for_non_instagram():
    url = "https://www.youtube.com/watch?v=abc"
    assert should_retry_with_kkinstagram(url, "login") is False


def test_should_retry_with_kkinstagram_false_when_already_kk():
    url = "https://kkinstagram.com/reel/abc/"
    assert should_retry_with_kkinstagram(url, "login") is False


def test_should_retry_with_kkinstagram_false_for_unrelated_error():
    url = "https://www.instagram.com/reel/abc/"
    assert should_retry_with_kkinstagram(url, "network timeout") is False


# --- is_instagram_post_url ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/ABC123/",
        "https://www.instagram.com/reel/ABC123/",
        "https://www.instagram.com/reels/ABC123/",
        "https://www.instagram.com/tv/ABC123/",
        "https://kkinstagram.com/p/ABC123/",
        "https://kkinstagram.com/reel/ABC123/",
    ],
)
def test_is_instagram_post_url_true(url):
    assert is_instagram_post_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/",
        "https://www.instagram.com/user/",
        "https://www.youtube.com/watch?v=abc",
        "https://www.tiktok.com/@user/video/123",
    ],
)
def test_is_instagram_post_url_false(url):
    assert is_instagram_post_url(url) is False


# --- is_instagram_photo_candidate_url ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/ABC123/",
        "https://www.instagram.com/p/XYZ/",
        "https://kkinstagram.com/p/ABC123/",
    ],
)
def test_is_instagram_photo_candidate_url_true(url):
    assert is_instagram_photo_candidate_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/ABC123/",
        "https://www.instagram.com/reels/ABC123/",
        "https://www.instagram.com/tv/ABC123/",
        "https://www.youtube.com/watch?v=abc",
    ],
)
def test_is_instagram_photo_candidate_url_false(url):
    assert is_instagram_photo_candidate_url(url) is False
