"""A batch keeps distinct supported links in the user's original order."""

import pytest

from src.services import url_utils


@pytest.mark.parametrize(
    "text,expected",
    [
        (None, []),
        ("no links", []),
        (
            "Watch (https://youtu.be/first), then https://x.com/user/status/2!\n"
            "https://instagram.com/p/third/.",
            [
                "https://youtu.be/first",
                "https://x.com/user/status/2",
                "https://instagram.com/p/third/",
            ],
        ),
        (
            "https://youtu.be/one?si=first https://youtu.be/one?utm_source=other "
            "https://youtu.be/two https://youtu.be/one",
            ["https://youtu.be/one?si=first", "https://youtu.be/two"],
        ),
        (
            "https://youtube.com.evil.test/watch?v=bad https://example.com/video "
            "https://tiktok.com/@user/video/123 https://x.com.evil.test/status/1",
            ["https://tiktok.com/@user/video/123"],
        ),
        (
            "https://youtube.com/watch?v=one https://youtube.com/watch?v=two",
            ["https://youtube.com/watch?v=one", "https://youtube.com/watch?v=two"],
        ),
    ],
)
def test_extract_urls_preserves_order_and_deduplicates_only_normalized_matches(text, expected):
    assert url_utils.extract_urls(text) == expected
