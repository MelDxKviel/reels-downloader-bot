"""
Команды администратора: управление пользователями и статистика.
"""
import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from src.config import ADMIN_USERS
from src.services.database import DatabaseService

logger = logging.getLogger(__name__)

router = Router()


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user_id in ADMIN_USERS


@router.message(Command("adduser"))
async def cmd_adduser(message: Message, db: DatabaseService) -> None:
    """Добавляет пользователя в список разрешённых."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "📝 <b>Использование:</b>\n"
            "<code>/adduser USER_ID</code>\n\n"
            "Пример: <code>/adduser 123456789</code>"
        )
        return
    
    try:
        user_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ Некорректный ID пользователя. Укажите числовой ID.")
        return
    
    success = await db.add_user(user_id)
    if success:
        await message.answer(f"✅ Пользователь <code>{user_id}</code> добавлен!")
        logger.info(f"Admin {message.from_user.id} added user {user_id}")
    else:
        await message.answer(f"ℹ️ Пользователь <code>{user_id}</code> уже существует.")


@router.message(Command("removeuser"))
async def cmd_removeuser(message: Message, db: DatabaseService) -> None:
    """Удаляет пользователя из списка разрешённых."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "📝 <b>Использование:</b>\n"
            "<code>/removeuser USER_ID</code>\n\n"
            "Пример: <code>/removeuser 123456789</code>"
        )
        return
    
    try:
        user_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ Некорректный ID пользователя. Укажите числовой ID.")
        return
    
    success = await db.remove_user(user_id)
    if success:
        await message.answer(f"✅ Пользователь <code>{user_id}</code> удалён!")
        logger.info(f"Admin {message.from_user.id} removed user {user_id}")
    else:
        await message.answer(f"❌ Пользователь <code>{user_id}</code> не найден.")


@router.message(Command("users"))
async def cmd_users(message: Message, db: DatabaseService) -> None:
    """Показывает список разрешённых пользователей."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    users = await db.get_all_users()
    
    if not users:
        await message.answer("📝 Список пользователей пуст.")
        return
    
    lines = ["👥 <b>Разрешённые пользователи:</b>\n"]
    for i, user in enumerate(users, 1):
        status = "✅" if user.is_active else "❌"
        created = user.created_at.strftime("%d.%m.%Y") if user.created_at else "—"
        lines.append(f"{i}. {status} <code>{user.user_id}</code> (добавлен: {created})")
    
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: DatabaseService) -> None:
    """Показывает общую статистику использования бота."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    stats = await db.get_global_stats()
    
    # Статистика за последние 24 часа
    since_24h = datetime.utcnow() - timedelta(hours=24)
    stats_24h = await db.get_global_stats(since=since_24h)
    
    # Статистика за последние 7 дней
    since_7d = datetime.utcnow() - timedelta(days=7)
    stats_7d = await db.get_global_stats(since=since_7d)
    
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"<b>Всего:</b>\n"
        f"• Загрузок: {stats['total_downloads']}\n"
        f"• Успешных: {stats['successful_downloads']}\n"
        f"• Неудачных: {stats['failed_downloads']}\n"
        f"• Активных пользователей: {stats['active_users']}\n\n"
        f"<b>За 24 часа:</b>\n"
        f"• Загрузок: {stats_24h['total_downloads']}\n"
        f"• Успешных: {stats_24h['successful_downloads']}\n\n"
        f"<b>За 7 дней:</b>\n"
        f"• Загрузок: {stats_7d['total_downloads']}\n"
        f"• Успешных: {stats_7d['successful_downloads']}\n\n"
        f"<b>По платформам (всего):</b>\n"
    )
    
    for platform, count in stats.get('by_platform', {}).items():
        text += f"• {platform}: {count}\n"
    
    await message.answer(text)


@router.message(Command("userstats"))
async def cmd_userstats(message: Message, db: DatabaseService) -> None:
    """Показывает статистику по конкретному пользователю."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "📝 <b>Использование:</b>\n"
            "<code>/userstats USER_ID</code>\n\n"
            "Пример: <code>/userstats 123456789</code>"
        )
        return
    
    try:
        user_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ Некорректный ID пользователя. Укажите числовой ID.")
        return
    
    user = await db.get_user(user_id)
    if not user:
        await message.answer(f"❌ Пользователь <code>{user_id}</code> не найден.")
        return
    
    stats = await db.get_user_stats(user_id)
    
    created = user.created_at.strftime("%d.%m.%Y %H:%M") if user.created_at else "—"
    last_active = stats.get('last_activity')
    last_active_str = last_active.strftime("%d.%m.%Y %H:%M") if last_active else "—"
    
    text = (
        f"📊 <b>Статистика пользователя</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📅 Добавлен: {created}\n"
        f"🕐 Последняя активность: {last_active_str}\n\n"
        f"<b>Загрузки:</b>\n"
        f"• Всего: {stats['total_downloads']}\n"
        f"• Успешных: {stats['successful_downloads']}\n"
        f"• Неудачных: {stats['failed_downloads']}\n\n"
        f"<b>По платформам:</b>\n"
    )
    
    for platform, count in stats.get('by_platform', {}).items():
        text += f"• {platform}: {count}\n"
    
    await message.answer(text)


@router.message(Command("adminhelp"))
async def cmd_adminhelp(message: Message) -> None:
    """Показывает справку по командам администратора."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    await message.answer(
        "🔐 <b>Команды администратора:</b>\n\n"
        "👥 <b>Управление пользователями:</b>\n"
        "/adduser <code>USER_ID</code> — добавить пользователя\n"
        "/removeuser <code>USER_ID</code> — удалить пользователя\n"
        "/users — список всех пользователей\n\n"
        "📊 <b>Статистика:</b>\n"
        "/stats — общая статистика бота\n"
        "/userstats <code>USER_ID</code> — статистика пользователя\n\n"
        "💡 <b>Совет:</b> Чтобы узнать ID пользователя, попросите его отправить боту команду /id"
    )

