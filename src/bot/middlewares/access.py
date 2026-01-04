"""
Middleware для проверки доступа пользователей.
"""
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from aiogram.enums import ParseMode

from src.config import ADMIN_USERS
from src.services.database import DatabaseService

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    """Middleware для инъекции сервиса базы данных."""
    
    def __init__(self, db: DatabaseService):
        self.db = db
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data["db"] = self.db
        return await handler(event, data)


class UserAccessMiddleware(BaseMiddleware):
    """
    Middleware для проверки доступа пользователя.
    Пропускает только пользователей из базы данных или администраторов.
    """
    
    def __init__(self, db: DatabaseService):
        self.db = db
    
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        user = event.from_user
        
        if user is None:
            logger.warning("Получено сообщение без информации о пользователе")
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
            f"(ID: {user.id}, username: @{user.username})"
        )
        return

