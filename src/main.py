"""
Точка входа для запуска Telegram бота.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat

from src.bot.handlers import get_main_router
from src.bot.middlewares import DatabaseMiddleware, UserAccessMiddleware
from src.config import ADMIN_USERS, BOT_TOKEN
from src.services.database import DatabaseService

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Главная функция запуска бота."""
    # Проверяем токен
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не указан! Создайте файл .env с токеном бота.")
        sys.exit(1)

    # Проверяем администраторов
    if not ADMIN_USERS:
        logger.warning(
            "⚠️ Список ADMIN_USERS пуст! Добавьте ID администраторов в .env (ADMIN_USERS=123456789)"
        )
    else:
        logger.info(f"✅ Администраторы: {ADMIN_USERS}")

    # Инициализируем базу данных
    db = DatabaseService()
    await db.init_db()

    # Создаём бота
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Создаём диспетчер
    dp = Dispatcher()

    # Получаем главный роутер
    main_router = get_main_router()

    # Регистрируем middleware
    main_router.message.middleware(DatabaseMiddleware(db))
    main_router.message.middleware(UserAccessMiddleware(db))
    main_router.inline_query.middleware(DatabaseMiddleware(db))
    main_router.inline_query.middleware(UserAccessMiddleware(db))
    main_router.chosen_inline_result.middleware(DatabaseMiddleware(db))
    main_router.chosen_inline_result.middleware(UserAccessMiddleware(db))
    main_router.callback_query.middleware(DatabaseMiddleware(db))
    main_router.callback_query.middleware(UserAccessMiddleware(db))

    # Подключаем роутер
    dp.include_router(main_router)

    # Регистрируем команды в меню бота
    user_commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Справка по использованию"),
        BotCommand(command="download", description="Скачать видео по URL"),
        BotCommand(command="mp3", description="Извлечь аудио в MP3"),
        BotCommand(command="voice", description="Конвертировать в голосовое сообщение"),
        BotCommand(command="round", description="Конвертировать в кружок (видео-заметка)"),
        BotCommand(command="gif", description="Конвертировать в GIF"),
        BotCommand(command="id", description="Показать ваш Telegram ID"),
    ]
    admin_commands = user_commands + [
        BotCommand(command="adduser", description="Добавить пользователя"),
        BotCommand(command="removeuser", description="Удалить пользователя"),
        BotCommand(command="users", description="Список пользователей"),
        BotCommand(command="stats", description="Глобальная статистика"),
        BotCommand(command="userstats", description="Статистика пользователя"),
        BotCommand(command="adminhelp", description="Справка для администратора"),
    ]

    await bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())
    for admin_id in ADMIN_USERS:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            pass

    # Запускаем бота
    logger.info("🚀 Бот запущен!")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await db.close()


def run() -> None:
    """Функция запуска для использования в качестве entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
