import sys
import time

import pytest

from scripts import publish_instagram_cookies
from scripts.publish_instagram_cookies import _filter_and_validate, _run_gh


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


@pytest.mark.parametrize("returncode", [0, 1])
def test_gh_output_cannot_echo_cookie_input(capsys, returncode):
    command = [
        sys.executable,
        "-c",
        "import sys; value = sys.stdin.read(); "
        f"print(value); print(value, file=sys.stderr); sys.exit({returncode})",
    ]
    if returncode:
        with pytest.raises(SystemExit) as exc_info:
            _run_gh(command, input_text="private-cookie-payload")
        assert "private-cookie-payload" not in str(exc_info.value)
    else:
        _run_gh(command, input_text="private-cookie-payload")

    captured = capsys.readouterr()
    assert "private-cookie-payload" not in captured.out + captured.err


def test_cookie_publisher_reports_success_without_secret_identifiers(monkeypatch, tmp_path, capsys):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(_cookie(".instagram.com", "sessionid", "private-session-value"))
    monkeypatch.setattr(sys, "argv", ["publish", "--cookie-file", str(cookie_file), "--no-trigger"])
    monkeypatch.setattr(publish_instagram_cookies, "_gh_command", lambda *args: list(args))
    calls = []
    monkeypatch.setattr(
        publish_instagram_cookies,
        "_run_gh",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    publish_instagram_cookies.main()

    assert len(calls) == 1
    assert calls[0][0] == ["secret", "set", publish_instagram_cookies.SECRET_NAME]
    assert calls[0][1]["input_text"]
    captured = capsys.readouterr()
    assert "private-session-value" not in captured.out + captured.err
    assert publish_instagram_cookies.SECRET_NAME not in captured.out + captured.err


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
