"""
Обработчик команды /download — скачивание видео по URL.
"""

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.handlers.download import _send_photo_paths
from src.bot.rich_carousel import send_rich_carousel
from src.bot.telegram_retry import (
    TELEGRAM_UPLOAD_TIMEOUT,
    retry_transient_telegram,
    telegram_duration,
)
from src.services.database import DatabaseService
from src.services.downloader import DownloadResult, downloader
from src.services.i18n import Translator, translate_download_error
from src.services.url_utils import extract_url

logger = logging.getLogger(__name__)

router = Router()


class DownloadStates(StatesGroup):
    waiting_for_url = State()


def _cancel_keyboard(user_id: int, t: Translator) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("common.cancel_button"),
                    callback_data=f"cancel_download:{user_id}",
                )
            ]
        ]
    )


async def _download_and_send(
    message: Message,
    db: DatabaseService,
    status_msg: Message,
    url: str,
    t: Translator,
) -> None:
    platform = downloader.get_platform_name(url)
    await status_msg.edit_text(t("download.start_status", platform=platform))

    try:
        result: DownloadResult = await downloader.download(url)
    except Exception as e:
        logger.error("Ошибка скачивания: %s", e, exc_info=True)
        await status_msg.edit_text(t("download.generic_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    if not result.success:
        reason = html.escape(translate_download_error(t, result))
        await status_msg.edit_text(t("download.failed", reason=reason))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    slides = result.carousel_slides if isinstance(result.carousel_slides, list) else None
    if slides and len(slides) >= 2:
        media_label = t("download.media_label.carousel")
    elif result.is_photo:
        media_label = t("download.media_label.photo")
    else:
        media_label = t("download.media_label.video")
    if result.from_cache:
        await status_msg.edit_text(t("download.from_cache_status", media_label=media_label))
    else:
        await status_msg.edit_text(t("download.send_status", media_label=media_label))

    try:
        sent_as_carousel = False
        if slides and len(slides) >= 2 and message.bot is not None:
            sent_as_carousel = await send_rich_carousel(
                message.bot,
                message.chat.id,
                slides,
                result.title,
                media_paths=result.photo_paths if result.is_photo else None,
            )

        if not sent_as_carousel and result.is_photo:
            photo_paths = result.photo_paths or [result.file_path]
            await _send_photo_paths(message, photo_paths)
        elif not sent_as_carousel:
            sent = await retry_transient_telegram(
                lambda: message.bot.send_video(
                    chat_id=message.chat.id,
                    video=FSInputFile(result.file_path),
                    duration=telegram_duration(result.duration),
                    width=result.width,
                    height=result.height,
                    supports_streaming=True,
                    request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                ),
                "sendVideo(user)",
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
        await status_msg.edit_text(t("download.send_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )


@router.message(Command("download"))
async def cmd_download(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""

    url = extract_url(rest) if rest else None

    if url:
        await state.clear()
        status_msg = await message.answer(t("common.processing"))
        await _download_and_send(message, db, status_msg, url, t)
        return

    prompt = await message.answer(
        t("download.cmd.prompt"),
        reply_markup=_cancel_keyboard(message.from_user.id, t),
    )
    await state.set_state(DownloadStates.waiting_for_url)
    await state.update_data(prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("cancel_download:"))
async def cancel_download(callback: CallbackQuery, state: FSMContext, t: Translator) -> None:
    owner_id = int(callback.data.split(":")[1])
    if callback.from_user.id != owner_id:
        await callback.answer(t("common.not_your_operation"), show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(t("download.cmd.cancelled"))
    await callback.answer()


@router.message(DownloadStates.waiting_for_url, F.text)
async def download_got_url(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    url = extract_url(message.text)

    if not url:
        await message.answer(t("download.cmd.url_not_found"))
        return

    data = await state.get_data()
    await state.clear()

    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass

    status_msg = await message.answer(t("common.processing"))
    await _download_and_send(message, db, status_msg, url, t)
