"""
Обработчик команды /download — скачивание видео по URL.
"""

import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from src.services.database import DatabaseService
from src.services.downloader import DownloadResult, downloader

logger = logging.getLogger(__name__)

router = Router()

URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com|youtu\.be|instagram\.com|kkinstagram\.com"
    r"|tiktok\.com|vt\.tiktok\.com|vm\.tiktok\.com|twitter\.com|x\.com)"
    r'[^\s<>"\']*',
    re.IGNORECASE,
)


class DownloadStates(StatesGroup):
    waiting_for_url = State()


def _cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_download:{user_id}")]
        ]
    )


async def _download_and_send(
    message: Message,
    db: DatabaseService,
    status_msg: Message,
    url: str,
) -> None:
    platform = downloader.get_platform_name(url)
    await status_msg.edit_text(
        f"⏳ Скачиваю с <b>{platform}</b>...\nЭто может занять некоторое время."
    )

    try:
        result: DownloadResult = await downloader.download(url)
    except Exception as e:
        logger.error("Ошибка скачивания: %s", e, exc_info=True)
        await status_msg.edit_text(
            "❌ <b>Произошла ошибка</b>\n\nПопробуйте позже или используйте другую ссылку."
        )
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    if not result.success:
        reason = html.escape(result.error or "Неизвестная ошибка")
        await status_msg.edit_text(f"❌ <b>Не удалось скачать</b>\n\nПричина: {reason}")
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    media_label = "фото" if result.is_photo else "видео"
    if result.from_cache:
        await status_msg.edit_text(f"📤 Отправляю {media_label} из кэша...")
    else:
        await status_msg.edit_text(f"📤 Отправляю {media_label}...")

    try:
        if result.is_photo:
            photo_paths = result.photo_paths or [result.file_path]
            if len(photo_paths) > 1:
                media = [InputMediaPhoto(media=FSInputFile(p)) for p in photo_paths]
                await message.answer_media_group(media=media)
            else:
                await message.answer_photo(photo=FSInputFile(photo_paths[0]))
        else:
            sent = await message.answer_video(
                video=FSInputFile(result.file_path), supports_streaming=True
            )
            if sent.video and sent.video.file_id:
                downloader.set_telegram_file_id(url, sent.video.file_id)
        await status_msg.delete()
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=True
        )
        logger.info(
            "✅ %s отправлено (пользователь: %s, из кэша: %s)",
            media_label.capitalize(),
            message.from_user.id,
            result.from_cache,
        )
    except Exception as e:
        logger.error("Ошибка при отправке: %s", e, exc_info=True)
        await status_msg.edit_text("❌ <b>Ошибка при отправке</b>\n\nПопробуйте позже.")
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )


@router.message(Command("download"))
async def cmd_download(message: Message, state: FSMContext, db: DatabaseService) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""

    match = URL_PATTERN.search(rest) if rest else None

    if match:
        url = match.group(0)
        await state.clear()
        status_msg = await message.answer("⏳ Обрабатываю...")
        await _download_and_send(message, db, status_msg, url)
        return

    prompt = await message.answer(
        "🔗 Отправьте ссылку на видео с YouTube, Instagram, TikTok или X/Twitter.",
        reply_markup=_cancel_keyboard(message.from_user.id),
    )
    await state.set_state(DownloadStates.waiting_for_url)
    await state.update_data(prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("cancel_download:"))
async def cancel_download(callback: CallbackQuery, state: FSMContext) -> None:
    owner_id = int(callback.data.split(":")[1])
    if callback.from_user.id != owner_id:
        await callback.answer("Это не ваша операция.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Загрузка отменена.")
    await callback.answer()


@router.message(DownloadStates.waiting_for_url, F.text)
async def download_got_url(message: Message, state: FSMContext, db: DatabaseService) -> None:
    text = message.text or ""
    match = URL_PATTERN.search(text)

    if not match:
        await message.answer(
            "🤔 Ссылка не найдена. Отправьте ссылку на видео или нажмите ❌ для отмены."
        )
        return

    url = match.group(0)
    data = await state.get_data()
    await state.clear()

    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass

    status_msg = await message.answer("⏳ Обрабатываю...")
    await _download_and_send(message, db, status_msg, url)
