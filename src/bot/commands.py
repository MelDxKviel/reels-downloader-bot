"""Bot command menu builders — shared between startup and language-switch handler."""

from typing import List

from aiogram.types import BotCommand

from src.services.i18n import Translator


def user_commands(t: Translator) -> List[BotCommand]:
    return [
        BotCommand(command="start", description=t("menu.start")),
        BotCommand(command="help", description=t("menu.help")),
        BotCommand(command="download", description=t("menu.download")),
        BotCommand(command="mp3", description=t("menu.mp3")),
        BotCommand(command="voice", description=t("menu.voice")),
        BotCommand(command="round", description=t("menu.round")),
        BotCommand(command="gif", description=t("menu.gif")),
        BotCommand(command="language", description=t("menu.language")),
        BotCommand(command="id", description=t("menu.id")),
    ]


def admin_commands(t: Translator) -> List[BotCommand]:
    return user_commands(t) + [
        BotCommand(command="adduser", description=t("menu.adduser")),
        BotCommand(command="removeuser", description=t("menu.removeuser")),
        BotCommand(command="users", description=t("menu.users")),
        BotCommand(command="stats", description=t("menu.stats")),
        BotCommand(command="userstats", description=t("menu.userstats")),
        BotCommand(command="adminhelp", description=t("menu.adminhelp")),
    ]
