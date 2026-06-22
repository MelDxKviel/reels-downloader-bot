"""Tests for common handlers: /start, /help, /id."""

from unittest.mock import patch

import pytest

from src.bot.handlers import common as common_h
from src.services.i18n import Translator

from ._helpers import make_message


@pytest.mark.asyncio
async def test_cmd_start_replies_with_greeting():
    msg = make_message("/start")
    await common_h.cmd_start(msg, Translator("en"))
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "Test User" in text


@pytest.mark.asyncio
async def test_cmd_help_non_admin_no_suffix():
    msg = make_message("/help", user_id=999)
    with patch("src.bot.handlers.common.is_admin", return_value=False):
        await common_h.cmd_help(msg, Translator("en"))
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    # The admin-only suffix mentions /adminhelp; it must be absent for non-admins.
    assert "/adminhelp" not in text


@pytest.mark.asyncio
async def test_cmd_help_admin_includes_suffix():
    msg = make_message("/help", user_id=1)
    with patch("src.bot.handlers.common.is_admin", return_value=True):
        await common_h.cmd_help(msg, Translator("en"))
    text = msg.answer.await_args.args[0]
    assert "adminhelp" in text.lower()


@pytest.mark.asyncio
async def test_cmd_id_with_username():
    msg = make_message("/id", user_id=100)
    msg.from_user.username = "myhandle"
    await common_h.cmd_id(msg, Translator("en"))
    text = msg.answer.await_args.args[0]
    assert "100" in text
    assert "myhandle" in text


@pytest.mark.asyncio
async def test_cmd_id_without_username():
    msg = make_message("/id", user_id=100)
    msg.from_user.username = None
    await common_h.cmd_id(msg, Translator("en"))
    text = msg.answer.await_args.args[0]
    assert "100" in text
