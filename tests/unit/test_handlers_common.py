"""Tests for common handlers: /start, /help, /id, /cache, /clearcache."""

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
    assert "admin" not in text.lower() or "use /adminhelp" not in text.lower()


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


@pytest.mark.asyncio
async def test_cmd_cache_empty():
    msg = make_message("/cache")
    with patch.object(common_h.downloader, "cache", {}):
        await common_h.cmd_cache(msg, Translator("en"))
    msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_cache_with_existing_files(tmp_path):
    msg = make_message("/cache")
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 1024)
    with patch.object(common_h.downloader, "cache", {"h": {"file_path": str(f)}}):
        await common_h.cmd_cache(msg, Translator("en"))
    text = msg.answer.await_args.args[0]
    assert "1" in text  # 1 file cached


@pytest.mark.asyncio
async def test_cmd_cache_skips_missing_files(tmp_path):
    msg = make_message("/cache")
    missing = str(tmp_path / "nope.mp4")
    with patch.object(common_h.downloader, "cache", {"h": {"file_path": missing}}):
        await common_h.cmd_cache(msg, Translator("en"))
    assert msg.answer.await_args is not None


@pytest.mark.asyncio
async def test_cmd_clearcache_reports_count():
    msg = make_message("/clearcache")
    with patch.object(common_h.downloader, "clear_cache", return_value=5):
        await common_h.cmd_clearcache(msg, Translator("en"))
    text = msg.answer.await_args.args[0]
    assert "5" in text
