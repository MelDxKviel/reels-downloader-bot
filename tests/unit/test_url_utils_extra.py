"""Coverage for url_utils ValueError-from-urlparse branches."""

from unittest.mock import patch

import src.services.url_utils as url_utils


def test_normalize_url_returns_input_when_urlparse_raises():
    with patch("src.services.url_utils.urlparse", side_effect=ValueError("bad url")):
        assert url_utils.normalize_url("https://example.com") == "https://example.com"


def test_is_supported_url_false_when_urlparse_raises():
    with patch("src.services.url_utils.urlparse", side_effect=ValueError("bad url")):
        assert url_utils.is_supported_url("https://youtube.com/abc") is False


def test_get_platform_name_unknown_when_urlparse_raises():
    with patch("src.services.url_utils.urlparse", side_effect=ValueError("bad url")):
        assert url_utils.get_platform_name("https://youtube.com/abc") == "Unknown"


def test_is_youtube_url_false_when_urlparse_raises():
    with patch("src.services.url_utils.urlparse", side_effect=ValueError("bad url")):
        assert url_utils.is_youtube_url("https://youtube.com/abc") is False


def test_is_supported_url_false_when_no_host():
    # urlparse on a bare path returns hostname=None
    assert url_utils.is_supported_url("not-a-url") is False
