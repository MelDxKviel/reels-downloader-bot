"""
Команды администратора: управление пользователями и статистика.
"""

import asyncio
import html
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

from src.config import (
    ADMIN_USERS,
    CACHE_AUTOCLEAN_DEFAULT,
    CACHE_MAX_AGE_HOURS,
    CACHE_MAX_AGE_PRESETS,
)
from src.services.database import DatabaseService, _utcnow
from src.services.downloader import downloader
from src.services.i18n import Translator

logger = logging.getLogger(__name__)

router = Router()

# Перечень фича-флагов, доступных в /features. Хранятся в bot_settings под
# ключом feature.<name>. При добавлении флага не забудьте локализовать его имя
# через ключ "admin.features.flag.<name>". Второе значение — дефолт, если
# флаг ни разу не переключали (например, белый список включён по умолчанию).
FEATURE_FLAGS: tuple[tuple[str, bool], ...] = (
    ("youtube_shorts_search", False),
    ("whitelist", True),
)
FEATURE_FLAG_NAMES: frozenset[str] = frozenset(name for name, _ in FEATURE_FLAGS)
FEATURE_FLAG_DEFAULTS: dict[str, bool] = {name: default for name, default in FEATURE_FLAGS}


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
        parts.append(f"({html.escape(full_name)})")
    if username:
        parts.append(f"@{html.escape(username)}")
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


async def _build_userstats_text(db: DatabaseService, bot: Bot, t: Translator, user_id: int) -> str:
    user = await db.get_user(user_id)
    if not user and user_id not in ADMIN_USERS:
        return t("admin.removeuser.not_found", user_id=user_id)

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
        user_info_lines.append(t("admin.userstats.name", name=html.escape(full_name)))
    if username:
        user_info_lines.append(t("admin.userstats.username", username=html.escape(username)))
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

    return text


async def _do_userstats(
    message: Message, db: DatabaseService, bot: Bot, t: Translator, user_id: int
) -> None:
    text = await _build_userstats_text(db, bot, t, user_id)
    await message.answer(text)


def _short_user_label(
    idx: int, full_name: Optional[str], username: Optional[str], user_id: int
) -> str:
    name = full_name or (f"@{username}" if username else str(user_id))
    if len(name) > 24:
        name = name[:23] + "…"
    return f"📊 #{idx} {name}"


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
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    idx = 1

    def _add_stats_button(label: str, target_id: int) -> None:
        row.append(InlineKeyboardButton(text=label, callback_data=f"userstats_view:{target_id}"))
        if len(row) == 2:
            buttons.append(row.copy())
            row.clear()

    for admin_id in ADMIN_USERS:
        full_name, username = await get_user_display_info(bot, admin_id)
        user_info = format_user_info(admin_id, full_name, username)
        lines.append(f"{idx}. 👑 {user_info}\n    <i>{t('admin.users.role_admin')}</i>")
        _add_stats_button(_short_user_label(idx, full_name, username, admin_id), admin_id)
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
        _add_stats_button(_short_user_label(idx, full_name, username, user.user_id), user.user_id)
        idx += 1

    if row:
        buttons.append(row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    await message.answer("\n".join(lines), reply_markup=keyboard)


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


async def _build_features_view(
    db: DatabaseService, t: Translator
) -> tuple[str, InlineKeyboardMarkup]:
    """Собирает текст и клавиатуру для /features (текущее состояние флагов)."""
    lines = [t("admin.features.title"), ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for flag, default in FEATURE_FLAGS:
        enabled = await db.is_feature_enabled(flag, default=default)
        flag_label = t(f"admin.features.flag.{flag}")
        state_label = t("admin.features.state_on" if enabled else "admin.features.state_off")
        lines.append(f"• <b>{flag_label}</b>: {state_label}")
        action_key = "admin.features.button_disable" if enabled else "admin.features.button_enable"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=t(action_key, name=flag_label),
                    callback_data=f"feature_toggle:{flag}",
                )
            ]
        )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("features"))
async def cmd_features(message: Message, db: DatabaseService, t: Translator) -> None:
    """Показывает текущее состояние фича-флагов и позволяет переключать их."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    text, keyboard = await _build_features_view(db, t)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("feature_toggle:"))
async def cb_feature_toggle(callback: CallbackQuery, db: DatabaseService, t: Translator) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("common.access_denied_callback"), show_alert=True)
        return

    parts = callback.data.split(":", 1)
    flag = parts[1] if len(parts) > 1 else ""
    if flag not in FEATURE_FLAG_NAMES:
        await callback.answer()
        return

    current = await db.is_feature_enabled(flag, default=FEATURE_FLAG_DEFAULTS[flag])
    await db.set_feature_enabled(flag, not current)

    text, keyboard = await _build_features_view(db, t)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.debug("Не удалось обновить /features сообщение: %s", e)

    new_state = t("admin.features.state_on" if not current else "admin.features.state_off")
    await callback.answer(
        t("admin.features.toggle_ack", name=t(f"admin.features.flag.{flag}"), state=new_state)
    )


# ── /cache: информация о кэше + управление автоочисткой ──────────────────────


def _format_max_age(hours: int, t: Translator) -> str:
    """Человекочитаемый срок хранения: дни, если кратно 24, иначе часы."""
    if hours % 24 == 0:
        return t("cache.age.days", value=hours // 24)
    return t("cache.age.hours", value=hours)


def _next_max_age(current: int) -> int:
    """Следующий пресет срока хранения (циклически)."""
    for preset in CACHE_MAX_AGE_PRESETS:
        if preset > current:
            return preset
    return CACHE_MAX_AGE_PRESETS[0]


async def _build_cache_view(db: DatabaseService, t: Translator) -> Tuple[str, InlineKeyboardMarkup]:
    """Собирает текст и клавиатуру для /cache (статистика + настройки автоочистки)."""
    count = len(downloader.cache)
    size_mb = downloader.cache_disk_usage() / (1024 * 1024)
    enabled = await db.get_cache_autoclean(CACHE_AUTOCLEAN_DEFAULT)
    max_age_hours = await db.get_cache_max_age_hours(CACHE_MAX_AGE_HOURS)
    max_age_label = _format_max_age(max_age_hours, t)

    text = t(
        "cache.text",
        count=count,
        size_mb=size_mb,
        autoclean=t("cache.autoclean_on" if enabled else "cache.autoclean_off"),
        max_age=max_age_label,
    )
    toggle_key = "cache.button_autoclean_disable" if enabled else "cache.button_autoclean_enable"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("cache.button_clear"), callback_data="cache_clear")],
            [InlineKeyboardButton(text=t(toggle_key), callback_data="cache_autoclean")],
            [
                InlineKeyboardButton(
                    text=t("cache.button_max_age", age=max_age_label),
                    callback_data="cache_age",
                )
            ],
        ]
    )
    return text, keyboard


async def _refresh_cache_view(callback: CallbackQuery, db: DatabaseService, t: Translator) -> None:
    """Перерисовывает сообщение /cache после действия (молча игнорируя отказ Telegram)."""
    text, keyboard = await _build_cache_view(db, t)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.debug("Не удалось обновить /cache сообщение: %s", e)


@router.message(Command("cache"))
async def cmd_cache(message: Message, db: DatabaseService, t: Translator) -> None:
    """Показывает информацию о кэше и управление автоочисткой."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    text, keyboard = await _build_cache_view(db, t)
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("clearcache"))
async def cmd_clearcache(message: Message, t: Translator) -> None:
    """Очищает кэш видео (только администратор)."""
    if not is_admin(message.from_user.id):
        await message.answer(t("admin.only"))
        return

    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, downloader.clear_cache)
    await message.answer(t("cache.cleared", count=count))


@router.callback_query(F.data == "cache_clear")
async def cb_cache_clear(callback: CallbackQuery, db: DatabaseService, t: Translator) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("common.access_denied_callback"), show_alert=True)
        return

    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, downloader.clear_cache)
    await _refresh_cache_view(callback, db, t)
    await callback.answer(t("cache.cleared_alert", count=count))


@router.callback_query(F.data == "cache_autoclean")
async def cb_cache_autoclean_toggle(
    callback: CallbackQuery, db: DatabaseService, t: Translator
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("common.access_denied_callback"), show_alert=True)
        return

    current = await db.get_cache_autoclean(CACHE_AUTOCLEAN_DEFAULT)
    await db.set_cache_autoclean(not current)
    await _refresh_cache_view(callback, db, t)
    new_label = t("cache.autoclean_on" if not current else "cache.autoclean_off")
    await callback.answer(t("cache.autoclean_toggled", state=new_label))


@router.callback_query(F.data == "cache_age")
async def cb_cache_age_cycle(callback: CallbackQuery, db: DatabaseService, t: Translator) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("common.access_denied_callback"), show_alert=True)
        return

    current = await db.get_cache_max_age_hours(CACHE_MAX_AGE_HOURS)
    next_age = _next_max_age(current)
    await db.set_cache_max_age_hours(next_age)
    await _refresh_cache_view(callback, db, t)
    await callback.answer(t("cache.max_age_changed", age=_format_max_age(next_age, t)))


@router.callback_query(F.data.startswith("cancel_admin:"))
async def cancel_admin(callback: CallbackQuery, state: FSMContext, t: Translator) -> None:
    parts = callback.data.split(":")
    try:
        owner_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
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


@router.callback_query(F.data.startswith("userstats_view:"))
async def cb_userstats_view(
    callback: CallbackQuery, db: DatabaseService, bot: Bot, t: Translator
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("common.access_denied_callback"), show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return

    text = await _build_userstats_text(db, bot, t, target_id)
    await callback.message.answer(text)
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
