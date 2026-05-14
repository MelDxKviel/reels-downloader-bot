"""Coverage for i18n format-error branch."""

from src.services.i18n import get_text


def test_get_text_format_error_returns_template():
    """Missing required format kwarg should log error and return unformatted template."""
    # admin.adduser.exists uses {user_id} — pass a wrong kwarg to trigger KeyError
    text = get_text("admin.adduser.exists", "ru", wrong_kwarg="x")
    assert "{user_id}" in text
