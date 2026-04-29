"""
Команда /language — выбор языка интерфейса.

Пользователь видит инлайн-клавиатуру со списком поддерживаемых языков,
нажимает кнопку, и предпочтение сохраняется в БД (через ``UserPreference``).
В ``callback_data`` зашит ``user_id`` инициатора, чтобы в групповых чатах
кнопку не мог нажать посторонний.

Если пользователь является администратором, после смены языка сразу обновляется
меню команд для его чата (``BotCommandScopeChat``), чтобы описания команд
переключились без перезапуска бота.
"""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.commands import admin_commands
from src.config import ADMIN_USERS
from src.services.database import DatabaseService
from src.services.i18n import Translator, supported_languages_with_labels

logger = logging.getLogger(__name__)

router = Router()


async def _refresh_admin_commands(bot: Bot, admin_id: int, lang: str) -> None:
    """Re-register the per-chat admin command scope in the new language."""
    try:
        await bot.set_my_commands(
            admin_commands(Translator(lang)),
            scope=BotCommandScopeChat(chat_id=admin_id),
        )
    except Exception as e:
        logger.debug("Could not refresh admin commands for %s: %s", admin_id, e)


def _language_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"set_lang:{code}:{user_id}")]
        for code, label in supported_languages_with_labels().items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("language"))
async def cmd_language(message: Message, t: Translator) -> None:
    await message.answer(
        t("language.choose"),
        reply_markup=_language_keyboard(message.from_user.id),
    )


@router.callback_query(F.data.startswith("set_lang:"))
async def set_language_callback(callback: CallbackQuery, db: DatabaseService, bot: Bot) -> None:
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    _, code, owner_str = parts

    try:
        owner_id = int(owner_str)
    except ValueError:
        await callback.answer()
        return

    if callback.from_user.id != owner_id:
        # Используем язык того, кто нажимает: ему и адресовано сообщение об ошибке.
        stored = await db.get_user_language(callback.from_user.id)
        t_caller = (
            Translator(stored) if stored else Translator(callback.from_user.language_code or "")
        )
        await callback.answer(t_caller("common.not_your_operation"), show_alert=True)
        return

    success = await db.set_user_language(owner_id, code)
    if not success:
        # Язык не из списка поддерживаемых — отвечаем на текущем языке инициатора.
        stored = await db.get_user_language(owner_id)
        t_owner = (
            Translator(stored) if stored else Translator(callback.from_user.language_code or "")
        )
        await callback.answer(t_owner("language.unsupported"), show_alert=True)
        return

    # Подтверждение показываем уже на новом языке.
    t_new = Translator(code)
    try:
        await callback.message.edit_text(t_new("language.changed"))
    except Exception as e:
        logger.debug("Не удалось отредактировать сообщение выбора языка: %s", e)
    await callback.answer()

    # Если это администратор — сразу обновляем его меню команд.
    if owner_id in ADMIN_USERS:
        await _refresh_admin_commands(bot, owner_id, code)
