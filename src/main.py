"""
Точка входа для запуска Telegram бота.
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.config import BOT_TOKEN, ADMIN_USERS
from src.bot.handlers import get_main_router
from src.bot.middlewares import UserAccessMiddleware, DatabaseMiddleware
from src.services.database import DatabaseService

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
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
            "⚠️ Список ADMIN_USERS пуст! "
            "Добавьте ID администраторов в .env (ADMIN_USERS=123456789)"
        )
    else:
        logger.info(f"✅ Администраторы: {ADMIN_USERS}")
    
    # Инициализируем базу данных
    db = DatabaseService()
    await db.init_db()
    
    # Создаём бота
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Создаём диспетчер
    dp = Dispatcher()
    
    # Получаем главный роутер
    main_router = get_main_router()
    
    # Регистрируем middleware
    main_router.message.middleware(DatabaseMiddleware(db))
    main_router.message.middleware(UserAccessMiddleware(db))
    
    # Подключаем роутер
    dp.include_router(main_router)
    
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

