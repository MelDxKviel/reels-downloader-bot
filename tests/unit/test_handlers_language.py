"""Tests for /language handler."""

from unittest.mock import patch

import pytest

from src.bot.handlers import language as lang_h
from src.services.i18n import Translator

from ._helpers import make_bot, make_callback, make_db, make_message


@pytest.mark.asyncio
async def test_cmd_language_shows_keyboard():
    msg = make_message("/language")
    await lang_h.cmd_language(msg, Translator("en"))
    msg.answer.assert_awaited_once()
    kwargs = msg.answer.await_args.kwargs
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_set_language_bad_callback_data():
    cb = make_callback("set_lang:en", user_id=1)
    db = make_db()
    bot = make_bot()
    await lang_h.set_language_callback(cb, db, bot)
    cb.answer.assert_awaited()
    db.set_user_language.assert_not_called()


@pytest.mark.asyncio
async def test_set_language_bad_owner_id():
    cb = make_callback("set_lang:en:not-an-int", user_id=1)
    db = make_db()
    bot = make_bot()
    await lang_h.set_language_callback(cb, db, bot)
    cb.answer.assert_awaited()
    db.set_user_language.assert_not_called()


@pytest.mark.asyncio
async def test_set_language_wrong_user():
    cb = make_callback("set_lang:en:42", user_id=99)
    db = make_db()
    db.get_user_language.return_value = "ru"
    bot = make_bot()
    await lang_h.set_language_callback(cb, db, bot)
    cb.answer.assert_awaited()
    args = cb.answer.await_args
    assert args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_set_language_wrong_user_no_stored_lang():
    cb = make_callback("set_lang:en:42", user_id=99)
    db = make_db()
    db.get_user_language.return_value = None
    bot = make_bot()
    await lang_h.set_language_callback(cb, db, bot)
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_set_language_unsupported():
    cb = make_callback("set_lang:fr:1", user_id=1)
    db = make_db()
    db.set_user_language.return_value = False
    db.get_user_language.return_value = None
    bot = make_bot()
    await lang_h.set_language_callback(cb, db, bot)
    args = cb.answer.await_args
    assert args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_set_language_unsupported_stored_lang_used():
    cb = make_callback("set_lang:fr:1", user_id=1)
    db = make_db()
    db.set_user_language.return_value = False
    db.get_user_language.return_value = "ru"
    bot = make_bot()
    await lang_h.set_language_callback(cb, db, bot)
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_set_language_success_non_admin():
    cb = make_callback("set_lang:en:42", user_id=42)
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.language.ADMIN_USERS", [1]):
        await lang_h.set_language_callback(cb, db, bot)
    db.set_user_language.assert_awaited_with(42, "en")
    cb.message.edit_text.assert_awaited()
    bot.set_my_commands.assert_not_called()


@pytest.mark.asyncio
async def test_set_language_success_admin_refreshes_menu():
    cb = make_callback("set_lang:en:1", user_id=1)
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.language.ADMIN_USERS", [1]):
        await lang_h.set_language_callback(cb, db, bot)
    bot.set_my_commands.assert_awaited()


@pytest.mark.asyncio
async def test_set_language_edit_text_exception_swallowed():
    cb = make_callback("set_lang:en:42", user_id=42)
    cb.message.edit_text.side_effect = RuntimeError("can't edit")
    db = make_db()
    bot = make_bot()
    with patch("src.bot.handlers.language.ADMIN_USERS", []):
        await lang_h.set_language_callback(cb, db, bot)
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_refresh_admin_commands_exception_swallowed():
    bot = make_bot()
    bot.set_my_commands.side_effect = RuntimeError("api down")
    # Should not raise
    await lang_h._refresh_admin_commands(bot, 1, "en")
