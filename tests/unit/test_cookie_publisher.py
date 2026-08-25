import time

import pytest

from scripts.publish_instagram_cookies import _filter_and_validate


def _cookie(domain: str, name: str, value: str, expires: int = 0) -> str:
    return f"{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}"


def test_filter_keeps_only_instagram_and_active_session():
    cookie_jar = "\n".join(
        [
            "# Netscape HTTP Cookie File",
            _cookie(".youtube.com", "SID", "do-not-publish"),
            _cookie("#HttpOnly_.instagram.com", "sessionid", "active-session"),
            _cookie("www.instagram.com", "csrftoken", "csrf"),
        ]
    )

    payload, count = _filter_and_validate(cookie_jar)
    rendered = payload.decode()

    assert count == 2
    assert "active-session" in rendered
    assert "csrf" in rendered
    assert "do-not-publish" not in rendered


def test_filter_rejects_expired_session():
    cookie_jar = _cookie(".instagram.com", "sessionid", "expired", int(time.time()) - 1)

    with pytest.raises(SystemExit, match="sessionid"):
        _filter_and_validate(cookie_jar)


def test_filter_accepts_any_active_session_when_duplicate_exists():
    cookie_jar = "\n".join(
        [
            _cookie(".instagram.com", "sessionid", "active"),
            _cookie(".instagram.com", "sessionid", "expired", int(time.time()) - 1),
        ]
    )

    _, count = _filter_and_validate(cookie_jar)

    assert count == 2
