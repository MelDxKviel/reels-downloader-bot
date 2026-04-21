"""
Middleware для проверки доступа пользователей.
"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from src.config import ADMIN_USERS
from src.services.database import DatabaseService

logger = logging.getLogger(__name__)


async def _terminate_callback(event: TelegramObject) -> None:
    """Acknowledge a denied CallbackQuery so the client stops showing a spinner.

    Without this, Telegram clients wait ~30s for a callback response and then
    display a generic timeout error. Called on access-denied branches where the
    handler is skipped.
    """
    if isinstance(event, CallbackQuery):
        try:
            await event.answer("⛔ Нет доступа", show_alert=False)
        except Exception:
            # A stale callback can't be answered twice; swallow and move on.
            pass


class DatabaseMiddleware(BaseMiddleware):
    """Middleware для инъекции сервиса базы данных."""

    def __init__(self, db: DatabaseService):
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["db"] = self.db
        return await handler(event, data)


class UserAccessMiddleware(BaseMiddleware):
    """
    Middleware для проверки доступа пользователя.
    Пропускает только пользователей из базы данных или администраторов.
    Применяется к Message, InlineQuery и ChosenInlineResult.
    """

    def __init__(self, db: DatabaseService):
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)

        if user is None:
            logger.warning("Получено обновление без информации о пользователе")
            await _terminate_callback(event)
            return

        # Администраторы всегда имеют доступ
        if user.id in ADMIN_USERS:
            return await handler(event, data)

        # Проверяем, есть ли пользователь в базе данных
        db_user = await self.db.get_user(user.id)
        if db_user and db_user.is_active:
            return await handler(event, data)

        # Пользователь не в списке - логируем и отвечаем
        logger.info(
            f"🚫 Доступ запрещён для пользователя: {user.full_name} "
            f"(ID: {user.id}, username: @{user.username}, event: {type(event).__name__})"
        )
        await _terminate_callback(event)
        return
