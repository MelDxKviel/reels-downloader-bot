"""
Точка входа для запуска Telegram бота.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

from src.bot.commands import admin_commands, user_commands
from src.bot.handlers import get_main_router
from src.bot.middlewares import DatabaseMiddleware, LocaleMiddleware, UserAccessMiddleware
from src.config import ADMIN_USERS, BOT_TOKEN, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from src.services.database import DatabaseService
from src.services.i18n import Translator

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def _setup_bot_commands(bot: Bot, db: DatabaseService) -> None:
    """Регистрирует меню команд для каждого поддерживаемого языка.

    Telegram сам выберет нужный набор по ``language_code`` пользователя.
    Команды по умолчанию (``language_code=None``) — на ``DEFAULT_LANGUAGE``.
    Админам команды устанавливаются индивидуально по их сохранённому языку
    (или ``DEFAULT_LANGUAGE``, если не выбрали).
    """
    try:
        # Дефолтный набор — на DEFAULT_LANGUAGE.
        default_t = Translator(DEFAULT_LANGUAGE)
        await bot.set_my_commands(user_commands(default_t), scope=BotCommandScopeAllPrivateChats())

        # Локализованные наборы для каждого поддерживаемого языка.
        for code in SUPPORTED_LANGUAGES:
            await bot.set_my_commands(
                user_commands(Translator(code)),
                scope=BotCommandScopeAllPrivateChats(),
                language_code=code,
            )

        # Админам ставим расширенный набор индивидуально на их языке.
        for admin_id in ADMIN_USERS:
            try:
                stored = await db.get_user_language(admin_id)
                admin_t = Translator(stored) if stored else default_t
                await bot.set_my_commands(
                    admin_commands(admin_t), scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ Не удалось установить команды для администратора {admin_id}: {e}"
                )
    except Exception as e:
        logger.warning(f"⚠️ Не удалось зарегистрировать команды бота: {e}")


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

    logger.info(
        "🌐 Языки: default=%s, поддерживаемые=%s",
        DEFAULT_LANGUAGE,
        ", ".join(SUPPORTED_LANGUAGES),
    )

    # Инициализируем базу данных
    db = DatabaseService()
    await db.init_db()

    # Регистрируем администраторов в таблице пользователей, чтобы их статистика была доступна
    for admin_id in ADMIN_USERS:
        await db.add_user(admin_id)

    # Создаём бота
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Создаём диспетчер
    dp = Dispatcher()

    # Получаем главный роутер
    main_router = get_main_router()

    # Регистрируем middleware. Порядок важен: Database → UserAccess → Locale.
    # LocaleMiddleware читает данные из БД через data["db"], поэтому ставится
    # после DatabaseMiddleware, и после UserAccessMiddleware — чтобы лишний раз
    # не запрашивать язык для отклонённых пользователей.
    for observer in (
        main_router.message,
        main_router.inline_query,
        main_router.chosen_inline_result,
        main_router.callback_query,
    ):
        observer.middleware(DatabaseMiddleware(db))
        observer.middleware(UserAccessMiddleware(db))
        observer.middleware(LocaleMiddleware())

    # Подключаем роутер
    dp.include_router(main_router)

    await _setup_bot_commands(bot, db)

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
