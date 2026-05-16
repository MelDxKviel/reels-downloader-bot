"""Tests for admin handlers."""

from unittest.mock import patch

import pytest

from src.bot.handlers import admin as adm
from src.services.i18n import Translator

from ._helpers import make_bot, make_callback, make_db, make_message, make_state


@pytest.mark.asyncio
async def test_is_admin_true_false():
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1, 2]):
        assert adm.is_admin(1) is True
        assert adm.is_admin(3) is False


@pytest.mark.asyncio
async def test_format_user_info_with_full_data():
    text = adm.format_user_info(123, "Full Name", "handle")
    assert "123" in text
    assert "Full Name" in text
    assert "handle" in text


def test_format_user_info_minimal():
    text = adm.format_user_info(123, None, None)
    assert "123" in text


def test_short_user_label_truncates():
    long = "a" * 50
    label = adm._short_user_label(1, long, None, 999)
    assert len(label) < 50
    assert "…" in label


def test_short_user_label_username_fallback():
    label = adm._short_user_label(1, None, "tgname", 999)
    assert "@tgname" in label


def test_short_user_label_id_fallback():
    label = adm._short_user_label(1, None, None, 999)
    assert "999" in label


@pytest.mark.asyncio
async def test_get_user_display_info_success():
    bot = make_bot()
    chat = type("c", (), {"full_name": "FN", "first_name": "F", "username": "u"})()
    bot.get_chat.return_value = chat
    full_name, username = await adm.get_user_display_info(bot, 1)
    assert full_name == "FN"
    assert username == "u"


@pytest.mark.asyncio
async def test_get_user_display_info_fallback_to_first_name():
    bot = make_bot()
    chat = type("c", (), {"full_name": None, "first_name": "F", "username": None})()
    bot.get_chat.return_value = chat
    full_name, username = await adm.get_user_display_info(bot, 1)
    assert full_name == "F"
    assert username is None


@pytest.mark.asyncio
async def test_get_user_display_info_exception():
    bot = make_bot()
    bot.get_chat.side_effect = RuntimeError("oops")
    full_name, username = await adm.get_user_display_info(bot, 1)
    assert full_name is None
    assert username is None


@pytest.mark.asyncio
async def test_cmd_adduser_denied_for_non_admin():
    msg = make_message("/adduser 123", user_id=999)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_adduser(msg, db, bot, Translator("en"), state)
    msg.answer.assert_awaited_once()
    db.add_user.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_adduser_with_user_id_arg():
    msg = make_message("/adduser 12345", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    chat = type("c", (), {"full_name": "FN", "first_name": "F", "username": "h"})()
    bot.get_chat.return_value = chat
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_adduser(msg, db, bot, Translator("en"), state)
    db.add_user.assert_awaited_with(12345)
    state.clear.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_adduser_invalid_id():
    msg = make_message("/adduser foo", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_adduser(msg, db, bot, Translator("en"), state)
    db.add_user.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_adduser_already_exists():
    msg = make_message("/adduser 9999", user_id=1)
    state = make_state()
    db = make_db()
    db.add_user.return_value = False
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_adduser(msg, db, bot, Translator("en"), state)
    msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_adduser_no_args_enters_fsm():
    msg = make_message("/adduser", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_adduser(msg, db, bot, Translator("en"), state)
    state.set_state.assert_awaited()
    state.update_data.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_removeuser_denied_for_non_admin():
    msg = make_message("/removeuser 1", user_id=999)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_removeuser(msg, db, bot, Translator("en"), state)
    db.remove_user.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_removeuser_with_arg_success():
    msg = make_message("/removeuser 42", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    chat = type("c", (), {"full_name": None, "first_name": None, "username": None})()
    bot.get_chat.return_value = chat
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_removeuser(msg, db, bot, Translator("en"), state)
    db.remove_user.assert_awaited_with(42)


@pytest.mark.asyncio
async def test_cmd_removeuser_not_found():
    msg = make_message("/removeuser 42", user_id=1)
    state = make_state()
    db = make_db()
    db.remove_user.return_value = False
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_removeuser(msg, db, bot, Translator("en"), state)


@pytest.mark.asyncio
async def test_cmd_removeuser_invalid_id():
    msg = make_message("/removeuser xx", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_removeuser(msg, db, bot, Translator("en"), state)
    db.remove_user.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_removeuser_no_args_enters_fsm():
    msg = make_message("/removeuser", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_removeuser(msg, db, bot, Translator("en"), state)
    state.set_state.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_users_denied_for_non_admin():
    msg = make_message("/users", user_id=999)
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_users(msg, db, bot, Translator("en"))
    db.get_all_users.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_users_empty_when_no_admins_or_users():
    msg = make_message("/users", user_id=1)
    db = make_db()
    db.get_all_users.return_value = []
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", []):
        await adm.cmd_users(msg, db, bot, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_users_with_admins_and_users():
    msg = make_message("/users", user_id=1)
    db = make_db()
    from datetime import datetime

    u = type(
        "u", (), {"user_id": 2, "is_active": True, "created_at": datetime(2024, 1, 1, 12, 0)}
    )()
    u2 = type("u", (), {"user_id": 3, "is_active": False, "created_at": None})()
    db.get_all_users.return_value = [u, u2]
    bot = make_bot()
    chat = type("c", (), {"full_name": "FN", "first_name": "F", "username": "h"})()
    bot.get_chat.return_value = chat
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_users(msg, db, bot, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_users_many_users_creates_button_rows():
    msg = make_message("/users", user_id=1)
    db = make_db()
    from datetime import datetime

    users = [
        type("u", (), {"user_id": 100 + i, "is_active": True, "created_at": datetime(2024, 1, 1)})()
        for i in range(5)
    ]
    db.get_all_users.return_value = users
    bot = make_bot()
    chat = type("c", (), {"full_name": "FN", "first_name": "F", "username": "h"})()
    bot.get_chat.return_value = chat
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_users(msg, db, bot, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_stats_denied_for_non_admin():
    msg = make_message("/stats", user_id=999)
    db = make_db()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_stats(msg, db, Translator("en"))
    db.get_global_stats.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_stats_admin():
    msg = make_message("/stats", user_id=1)
    db = make_db()
    db.get_global_stats.return_value = {
        "total_downloads": 5,
        "successful_downloads": 4,
        "failed_downloads": 1,
        "active_users": 2,
        "by_platform": {"YouTube": 5},
    }
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_stats(msg, db, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_userstats_no_args_enters_fsm():
    msg = make_message("/userstats", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_userstats(msg, db, bot, Translator("en"), state)
    state.set_state.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_userstats_with_arg():
    msg = make_message("/userstats 42", user_id=1)
    state = make_state()
    db = make_db()
    from datetime import datetime

    u = type("u", (), {"user_id": 42, "is_active": True, "created_at": datetime(2024, 1, 1)})()
    db.get_user.return_value = u
    db.get_user_stats.return_value = {
        "total_downloads": 1,
        "successful_downloads": 1,
        "failed_downloads": 0,
        "by_platform": {"YouTube": 1},
        "last_activity": datetime(2024, 1, 2),
    }
    bot = make_bot()
    chat = type("c", (), {"full_name": "FN", "first_name": "F", "username": "h"})()
    bot.get_chat.return_value = chat
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_userstats(msg, db, bot, Translator("en"), state)
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_userstats_unknown_user():
    msg = make_message("/userstats 99", user_id=1)
    state = make_state()
    db = make_db()
    db.get_user.return_value = None
    db.get_user_stats.return_value = {
        "total_downloads": 0,
        "successful_downloads": 0,
        "failed_downloads": 0,
        "by_platform": {},
        "last_activity": None,
    }
    bot = make_bot()
    chat = type("c", (), {"full_name": None, "first_name": None, "username": None})()
    bot.get_chat.return_value = chat
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_userstats(msg, db, bot, Translator("en"), state)
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_userstats_invalid_id():
    msg = make_message("/userstats not-int", user_id=1)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_userstats(msg, db, bot, Translator("en"), state)
    db.get_user.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_userstats_denied_non_admin():
    msg = make_message("/userstats 1", user_id=999)
    state = make_state()
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_userstats(msg, db, bot, Translator("en"), state)
    db.get_user.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_adminhelp_denied_non_admin():
    msg = make_message("/adminhelp", user_id=999)
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_adminhelp(msg, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_adminhelp_admin():
    msg = make_message("/adminhelp", user_id=1)
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_adminhelp(msg, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_admin_wrong_owner():
    cb = make_callback("cancel_admin:1:adduser", user_id=2)
    state = make_state({"action": "adduser"})
    await adm.cancel_admin(cb, state, Translator("en"))
    cb.answer.assert_awaited()
    state.clear.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_admin_stale_action():
    cb = make_callback("cancel_admin:1:adduser", user_id=1)
    state = make_state({"action": "removeuser"})
    await adm.cancel_admin(cb, state, Translator("en"))
    cb.answer.assert_awaited()
    state.clear.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_admin_success_adduser():
    cb = make_callback("cancel_admin:1:adduser", user_id=1)
    state = make_state({"action": "adduser"})
    await adm.cancel_admin(cb, state, Translator("en"))
    state.clear.assert_awaited()
    cb.message.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_admin_success_removeuser():
    cb = make_callback("cancel_admin:1:removeuser", user_id=1)
    state = make_state({"action": "removeuser"})
    await adm.cancel_admin(cb, state, Translator("en"))
    state.clear.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_admin_success_userstats():
    cb = make_callback("cancel_admin:1:userstats", user_id=1)
    state = make_state({"action": "userstats"})
    await adm.cancel_admin(cb, state, Translator("en"))
    state.clear.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_admin_no_action_part():
    cb = make_callback("cancel_admin:1", user_id=1)
    state = make_state({"action": ""})
    await adm.cancel_admin(cb, state, Translator("en"))
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cb_userstats_view_denied_non_admin():
    cb = make_callback("userstats_view:42", user_id=999)
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_userstats_view(cb, db, bot, Translator("en"))
    cb.answer.assert_awaited()
    db.get_user.assert_not_called()


@pytest.mark.asyncio
async def test_cb_userstats_view_bad_parts():
    cb = make_callback("userstats_view", user_id=1)
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_userstats_view(cb, db, bot, Translator("en"))
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cb_userstats_view_invalid_id():
    cb = make_callback("userstats_view:bad", user_id=1)
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_userstats_view(cb, db, bot, Translator("en"))
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cb_userstats_view_success():
    cb = make_callback("userstats_view:42", user_id=1)
    db = make_db()
    from datetime import datetime

    u = type("u", (), {"user_id": 42, "is_active": True, "created_at": datetime(2024, 1, 1)})()
    db.get_user.return_value = u
    bot = make_bot()
    chat = type("c", (), {"full_name": "F", "first_name": "f", "username": None})()
    bot.get_chat.return_value = chat
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_userstats_view(cb, db, bot, Translator("en"))
    cb.message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_admin_got_user_id_invalid_text():
    msg = make_message("not-int", user_id=1)
    state = make_state({"action": "adduser"})
    db = make_db()
    bot = make_bot()
    await adm.admin_got_user_id(msg, state, db, bot, Translator("en"))
    msg.answer.assert_awaited()
    state.clear.assert_not_called()


@pytest.mark.asyncio
async def test_admin_got_user_id_adduser_with_prompt_delete():
    msg = make_message("123", user_id=1)
    state = make_state({"action": "adduser", "prompt_message_id": 5})
    db = make_db()
    bot = make_bot()
    chat = type("c", (), {"full_name": None, "first_name": None, "username": None})()
    bot.get_chat.return_value = chat
    await adm.admin_got_user_id(msg, state, db, bot, Translator("en"))
    state.clear.assert_awaited()
    msg.bot.delete_message.assert_awaited()
    db.add_user.assert_awaited_with(123)


@pytest.mark.asyncio
async def test_admin_got_user_id_removeuser():
    msg = make_message("123", user_id=1)
    state = make_state({"action": "removeuser"})
    db = make_db()
    bot = make_bot()
    chat = type("c", (), {"full_name": None, "first_name": None, "username": None})()
    bot.get_chat.return_value = chat
    await adm.admin_got_user_id(msg, state, db, bot, Translator("en"))
    db.remove_user.assert_awaited_with(123)


@pytest.mark.asyncio
async def test_admin_got_user_id_userstats():
    msg = make_message("123", user_id=1)
    state = make_state({"action": "userstats"})
    db = make_db()
    from datetime import datetime

    u = type("u", (), {"user_id": 123, "is_active": True, "created_at": datetime(2024, 1, 1)})()
    db.get_user.return_value = u
    bot = make_bot()
    chat = type("c", (), {"full_name": None, "first_name": None, "username": None})()
    bot.get_chat.return_value = chat
    await adm.admin_got_user_id(msg, state, db, bot, Translator("en"))
    db.get_user.assert_awaited()


@pytest.mark.asyncio
async def test_admin_got_user_id_prompt_delete_error_swallowed():
    msg = make_message("123", user_id=1)
    state = make_state({"action": "adduser", "prompt_message_id": 5})
    msg.bot.delete_message.side_effect = RuntimeError("gone")
    db = make_db()
    bot = make_bot()
    chat = type("c", (), {"full_name": None, "first_name": None, "username": None})()
    bot.get_chat.return_value = chat
    await adm.admin_got_user_id(msg, state, db, bot, Translator("en"))
    db.add_user.assert_awaited_with(123)


@pytest.mark.asyncio
async def test_cmd_users_empty_both_admins_and_users():
    """Branch where neither admins nor regular users exist."""
    msg = make_message("/users", user_id=1)
    db = make_db()
    db.get_all_users.return_value = []
    bot = make_bot()
    # Bypass is_admin check so we reach the empty-users branch
    with patch("src.bot.handlers.admin.is_admin", return_value=True):
        with patch("src.bot.handlers.admin.ADMIN_USERS", []):
            await adm.cmd_users(msg, db, bot, Translator("en"))
    msg.answer.assert_awaited()


# ── /features command + toggle callback ──────────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_features_non_admin_denied():
    msg = make_message("/features", user_id=99)
    db = make_db()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_features(msg, db, Translator("en"))
    msg.answer.assert_awaited()
    db.is_feature_enabled.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_features_shows_current_state():
    msg = make_message("/features", user_id=1)
    db = make_db()
    db.is_feature_enabled.return_value = False
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_features(msg, db, Translator("en"))
    msg.answer.assert_awaited()
    # Keyboard with toggle button is attached.
    kwargs = msg.answer.await_args.kwargs
    assert "reply_markup" in kwargs
    assert kwargs["reply_markup"].inline_keyboard


@pytest.mark.asyncio
async def test_cb_feature_toggle_flips_value():
    cb = make_callback("feature_toggle:youtube_shorts_search", user_id=1)
    db = make_db()
    db.is_feature_enabled.return_value = False
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_feature_toggle(cb, db, Translator("en"))
    db.set_feature_enabled.assert_awaited_with("youtube_shorts_search", True)
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cb_feature_toggle_unknown_flag_ignored():
    cb = make_callback("feature_toggle:unknown_flag", user_id=1)
    db = make_db()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_feature_toggle(cb, db, Translator("en"))
    db.set_feature_enabled.assert_not_called()
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cb_feature_toggle_non_admin_denied():
    cb = make_callback("feature_toggle:youtube_shorts_search", user_id=99)
    db = make_db()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_feature_toggle(cb, db, Translator("en"))
    db.set_feature_enabled.assert_not_called()


@pytest.mark.asyncio
async def test_cb_feature_toggle_swallows_edit_text_exception():
    """If Telegram refuses the edit (e.g. message too old), the toggle still completes."""
    cb = make_callback("feature_toggle:youtube_shorts_search", user_id=1)
    cb.message.edit_text.side_effect = RuntimeError("edit failed")
    db = make_db()
    db.is_feature_enabled.return_value = True
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_feature_toggle(cb, db, Translator("en"))
    db.set_feature_enabled.assert_awaited_with("youtube_shorts_search", False)
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cb_feature_toggle_empty_flag_ignored():
    """callback_data missing the flag name (no colon) is treated as unknown."""
    cb = make_callback("feature_toggle:", user_id=1)
    db = make_db()
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_feature_toggle(cb, db, Translator("en"))
    db.set_feature_enabled.assert_not_called()
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_whitelist_flag_registered_with_default_true():
    """The whitelist flag is part of FEATURE_FLAGS and defaults to enabled."""
    assert "whitelist" in adm.FEATURE_FLAG_NAMES
    assert adm.FEATURE_FLAG_DEFAULTS["whitelist"] is True


@pytest.mark.asyncio
async def test_cmd_features_uses_whitelist_default_when_unset():
    """If the whitelist setting has never been written, /features must read it as ON."""
    msg = make_message("/features", user_id=1)
    db = make_db()
    # Simulate "never toggled" by echoing the default kwarg back to caller.
    db.is_feature_enabled.side_effect = lambda name, default=False: default
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cmd_features(msg, db, Translator("en"))
    db.is_feature_enabled.assert_any_await("whitelist", default=True)


@pytest.mark.asyncio
async def test_cb_feature_toggle_whitelist_off_when_default_on():
    """Toggling the whitelist from the default ON state writes a disabled value."""
    cb = make_callback("feature_toggle:whitelist", user_id=1)
    db = make_db()
    db.is_feature_enabled.side_effect = lambda name, default=False: default
    with patch("src.bot.handlers.admin.ADMIN_USERS", [1]):
        await adm.cb_feature_toggle(cb, db, Translator("en"))
    db.set_feature_enabled.assert_awaited_with("whitelist", False)
    cb.answer.assert_awaited()
