"""
Общие команды бота: /start, /help, /id, /cache, /clearcache
"""

import os

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from src.bot.handlers.admin import is_admin
from src.services.downloader import downloader
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


@router.message(Command("cache"))
async def cmd_cache(message: Message, t: Translator) -> None:
    """Показывает информацию о кэше."""
    cache_size = len(downloader.cache)
    total_size = 0
    for data in downloader.cache.values():
        file_path = data.get("file_path")
        if file_path and os.path.exists(file_path):
            total_size += os.path.getsize(file_path)

    size_mb = total_size / (1024 * 1024)
    await message.answer(t("cache.text", count=cache_size, size_mb=size_mb))


@router.message(Command("clearcache"))
async def cmd_clearcache(message: Message, t: Translator) -> None:
    """Очищает кэш видео."""
    count = downloader.clear_cache()
    await message.answer(t("cache.cleared", count=count))
