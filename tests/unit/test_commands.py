"""Tests for bot/commands.py command list builders."""

from aiogram.types import BotCommand

from src.bot.commands import admin_commands, user_commands
from src.services.i18n import Translator


def test_user_commands_returns_bot_commands():
    cmds = user_commands(Translator("en"))
    assert all(isinstance(c, BotCommand) for c in cmds)
    names = [c.command for c in cmds]
    for required in ("start", "help", "download", "mp3", "voice", "round", "gif", "language", "id"):
        assert required in names


def test_admin_commands_extends_user_commands():
    user = user_commands(Translator("en"))
    admin = admin_commands(Translator("en"))
    assert len(admin) > len(user)
    admin_names = [c.command for c in admin]
    for extra in ("adduser", "removeuser", "users", "stats", "userstats", "adminhelp"):
        assert extra in admin_names


def test_user_commands_translated_descriptions():
    en = user_commands(Translator("en"))
    ru = user_commands(Translator("ru"))
    assert [c.description for c in en] != [c.description for c in ru]
