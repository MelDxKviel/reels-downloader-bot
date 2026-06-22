"""
Общие команды бота: /start, /help, /id
"""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from src.bot.handlers.admin import is_admin
from src.services.i18n import Translator

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, t: Translator) -> None:
    """Обработчик команды /start."""
    user = message.from_user
    await message.answer(t("start.text", name=user.full_name))


@router.message(Command("help"))
async def cmd_help(message: Message, t: Translator) -> None:
    """Обработчик команды /help."""
    text = t("help.text")
    if is_admin(message.from_user.id):
        text += t("help.admin_suffix")
    await message.answer(text, link_preview_options=LinkPreviewOptions(is_disabled=True))


@router.message(Command("id"))
async def cmd_id(message: Message, t: Translator) -> None:
    """Показывает ID пользователя."""
    user = message.from_user
    await message.answer(
        t(
            "id.text",
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or t("id.username_unset"),
        )
    )
