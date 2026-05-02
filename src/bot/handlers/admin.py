"""
Команды администратора: управление пользователями и статистика.
"""

import logging
from datetime import timedelta
from typing import Optional, Tuple

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.config import ADMIN_USERS
from src.services.database import DatabaseService, _utcnow
from src.services.i18n import Translator

logger = logging.getLogger(__name__)

router = Router()


class AdminStates(StatesGroup):
    waiting_for_user_id = State()


async def get_user_display_info(bot: Bot, user_id: int) -> Tuple[Optional[str], Optional[str]]:
    try:
        chat = await bot.get_chat(user_id)
        full_name = chat.full_name or chat.first_name or None
        username = chat.username
        return full_name, username
    except Exception:
        return None, None


def format_user_info(user_id: int, full_name: Optional[str], username: Optional[str]) -> str:
    """Форматирует информацию о пользователе для отображения."""
    parts = [f"<code>{user_id}</code>"]
    if full_name:
        parts.append(f"({full_name})")
    if username:
        parts.append(f"@{username}")
    return " ".join(parts)


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user_id in ADMIN_USERS


def _cancel_keyboard(user_id: int, action: str, t: Translator) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("common.cancel_button"),
                    callback_data=f"cancel_admin:{user_id}:{action}",
                )
            ]
        ]
    )


async def _do_adduser(
    message: Message, db: DatabaseService, bot: Bot, t: Translator, user_id: int
) -> None:
    success = await db.add_user(user_id)
    if success:
        full_name, username = await get_user_display_info(bot, user_id)
        user_info = format_user_info(user_id, full_name, username)
        await message.answer(t("admin.adduser.success", info=user_info))
        logger.info(f"Admin {message.from_user.id} added user {user_id}")
    else:
        await message.answer(t("admin.adduser.exists", user_id=user_id))


async def _do_removeuser(
    message: Message, db: DatabaseService, bot: Bot, t: Translator, user_id: int
) -> None:
    success = await db.remove_user(user_id)
    if success:
        full_name, username = await get_user_display_info(bot, user_id)
        user_info = format_user_info(user_id, full_name, username)
        await message.answer(t("admin.removeuser.success", info=user_info))
        logger.info(f"Admin {message.from_user.id} removed user {user_id}")
    else:
        await message.answer(t("admin.removeuser.not_found", user_id=user_id))


async def _do_userstats(
    message: Message, db: DatabaseService, bot: Bot, t: Translator, user_id: int
) -> None:
    user = await db.get_user(user_id)
    if not user and user_id not in ADMIN_USERS:
        await message.answer(t("admin.removeuser.not_found", user_id=user_id))
        return

    stats = await db.get_user_stats(user_id)
    full_name, username = await get_user_display_info(bot, user_id)

    created = (
        user.created_at.strftime("%d.%m.%Y %H:%M")
        if user and user.created_at
        else t("admin.users.date_unknown")
    )
    last_active = stats.get("last_activity")
    last_active_str = (
        last_active.strftime("%d.%m.%Y %H:%M") if last_active else t("admin.users.date_unknown")
    )

    user_info_lines = [t("admin.userstats.id", user_id=user_id)]
    if full_name:
        user_info_lines.append(t("admin.userstats.name", name=full_name))
    if username:
        user_info_lines.append(t("admin.userstats.username", username=username))
    user_info_lines.append(t("admin.userstats.added", date=created))
    user_info_lines.append(t("admin.userstats.last_active", date=last_active_str))

    text = (
        t("admin.userstats.title")
        + "\n\n"
        + "\n".join(user_info_lines)
        + "\n\n"
        + t(
            "admin.userstats.downloads_section",
            total=stats["total_downloads"],
            success=stats["successful_downloads"],
            failed=stats["failed_downloads"],
        )
    )

    for platform, count in stats.get("by_platform", {}).items():
        text += f"• {platform}: {count}\n"

    await message.answer(text)


@router.message(Command("adduser"))
async def cmd_adduser(
    message: Message, db: DatabaseService, bot: Bot, t: Translator, state: FSMContext
) -> None:
    """Добавляет пользователя в список разрешённых."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        prompt = await message.answer(
            t("admin.adduser.prompt"),
            reply_markup=_cancel_keyboard(message.from_user.id, "adduser", t),
        )
        await state.set_state(AdminStates.waiting_for_user_id)
        await state.update_data(action="adduser", prompt_message_id=prompt.message_id)
        return

    try:
        user_id = int(args[1].strip())
    except ValueError:
        await message.answer(t("admin.invalid_id"))
        return

    await state.clear()
    await _do_adduser(message, db, bot, t, user_id)


@router.message(Command("removeuser"))
async def cmd_removeuser(
    message: Message, db: DatabaseService, bot: Bot, t: Translator, state: FSMContext
) -> None:
    """Удаляет пользователя из списка разрешённых."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        prompt = await message.answer(
            t("admin.removeuser.prompt"),
            reply_markup=_cancel_keyboard(message.from_user.id, "removeuser", t),
        )
        await state.set_state(AdminStates.waiting_for_user_id)
        await state.update_data(action="removeuser", prompt_message_id=prompt.message_id)
        return

    try:
        user_id = int(args[1].strip())
    except ValueError:
        await message.answer(t("admin.invalid_id"))
        return

    await state.clear()
    await _do_removeuser(message, db, bot, t, user_id)


@router.message(Command("users"))
async def cmd_users(message: Message, db: DatabaseService, bot: Bot, t: Translator) -> None:
    """Показывает список разрешённых пользователей."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    users = await db.get_all_users()
    admin_ids = set(ADMIN_USERS)
    regular_users = [u for u in users if u.user_id not in admin_ids]

    if not regular_users and not admin_ids:
        await message.answer(t("admin.users.empty"))
        return

    lines = [t("admin.users.title") + "\n"]
    idx = 1

    for admin_id in ADMIN_USERS:
        full_name, username = await get_user_display_info(bot, admin_id)
        user_info = format_user_info(admin_id, full_name, username)
        lines.append(f"{idx}. 👑 {user_info}\n    <i>{t('admin.users.role_admin')}</i>")
        idx += 1

    for user in regular_users:
        status = "✅" if user.is_active else "❌"
        created = (
            user.created_at.strftime("%d.%m.%Y")
            if user.created_at
            else t("admin.users.date_unknown")
        )
        full_name, username = await get_user_display_info(bot, user.user_id)
        user_info = format_user_info(user.user_id, full_name, username)
        added_label = t("admin.users.added_at", date=created)
        lines.append(f"{idx}. {status} {user_info}\n    <i>{added_label}</i>")
        idx += 1

    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: DatabaseService, t: Translator) -> None:
    """Показывает общую статистику использования бота."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    stats = await db.get_global_stats()

    now = _utcnow()
    since_24h = now - timedelta(hours=24)
    stats_24h = await db.get_global_stats(since=since_24h)

    since_7d = now - timedelta(days=7)
    stats_7d = await db.get_global_stats(since=since_7d)

    text = t(
        "admin.stats.text",
        total=stats["total_downloads"],
        success=stats["successful_downloads"],
        failed=stats["failed_downloads"],
        active=stats["active_users"],
        total_24h=stats_24h["total_downloads"],
        success_24h=stats_24h["successful_downloads"],
        total_7d=stats_7d["total_downloads"],
        success_7d=stats_7d["successful_downloads"],
    )

    for platform, count in stats.get("by_platform", {}).items():
        text += f"• {platform}: {count}\n"

    await message.answer(text)


@router.message(Command("userstats"))
async def cmd_userstats(
    message: Message, db: DatabaseService, bot: Bot, t: Translator, state: FSMContext
) -> None:
    """Показывает статистику по конкретному пользователю."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        prompt = await message.answer(
            t("admin.userstats.prompt"),
            reply_markup=_cancel_keyboard(message.from_user.id, "userstats", t),
        )
        await state.set_state(AdminStates.waiting_for_user_id)
        await state.update_data(action="userstats", prompt_message_id=prompt.message_id)
        return

    try:
        user_id = int(args[1].strip())
    except ValueError:
        await message.answer(t("admin.invalid_id"))
        return

    await state.clear()
    await _do_userstats(message, db, bot, t, user_id)


@router.message(Command("adminhelp"))
async def cmd_adminhelp(message: Message, t: Translator) -> None:
    """Показывает справку по командам администратора."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    await message.answer(t("admin.adminhelp.text"))


@router.callback_query(F.data.startswith("cancel_admin:"))
async def cancel_admin(callback: CallbackQuery, state: FSMContext, t: Translator) -> None:
    parts = callback.data.split(":")
    owner_id = int(parts[1])
    btn_action = parts[2] if len(parts) > 2 else ""

    if callback.from_user.id != owner_id:
        await callback.answer(t("common.not_your_operation"), show_alert=True)
        return

    data = await state.get_data()
    current_action = data.get("action", "")

    if btn_action != current_action:
        await callback.answer(t("admin.stale_prompt"), show_alert=True)
        return

    cancelled_key = {
        "adduser": "admin.adduser.cancelled",
        "removeuser": "admin.removeuser.cancelled",
        "userstats": "admin.userstats.cancelled",
    }.get(current_action, "admin.adduser.cancelled")

    await state.clear()
    await callback.message.edit_text(t(cancelled_key))
    await callback.answer()


@router.message(AdminStates.waiting_for_user_id, F.text)
async def admin_got_user_id(
    message: Message, state: FSMContext, db: DatabaseService, bot: Bot, t: Translator
) -> None:
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer(t("admin.id_not_found"))
        return

    data = await state.get_data()
    action = data.get("action")
    prompt_id = data.get("prompt_message_id")

    await state.clear()

    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass

    if action == "adduser":
        await _do_adduser(message, db, bot, t, user_id)
    elif action == "removeuser":
        await _do_removeuser(message, db, bot, t, user_id)
    elif action == "userstats":
        await _do_userstats(message, db, bot, t, user_id)
