"""
Обработчик команды /mp3 — конвертация видео в аудио MP3.
"""

import asyncio
import html
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

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

from src.config import DOWNLOAD_DIR
from src.services.database import DatabaseService
from src.services.downloader import downloader
from src.services.i18n import Translator, translate_download_error
from src.services.url_utils import extract_url

logger = logging.getLogger(__name__)

router = Router()


class Mp3States(StatesGroup):
    waiting_for_input = State()


def _cancel_keyboard(user_id: int, t: Translator) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("common.cancel_button"), callback_data=f"cancel_mp3:{user_id}"
                )
            ]
        ]
    )


async def _convert_to_mp3(input_path: str) -> Optional[str]:
    """Извлекает аудио из видео и конвертирует в MP3 (VBR ~190kbps)."""
    if not shutil.which("ffmpeg"):
        return None

    output_path = str(Path(DOWNLOAD_DIR) / f"mp3_{uuid.uuid4().hex[:8]}.mp3")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",
        output_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return None

    if proc.returncode == 0 and os.path.exists(output_path):
        return output_path
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except Exception:
            pass
    return None


async def _send_mp3(
    message: Message,
    db: DatabaseService,
    status_msg: Message,
    file_path: str,
    platform: str,
    url: str,
    t: Translator,
) -> None:
    """Конвертирует файл и отправляет как аудио MP3."""
    await status_msg.edit_text(t("mp3.convert_status"))

    mp3_path = await _convert_to_mp3(file_path)

    if mp3_path is None:
        await status_msg.edit_text(t("mp3.convert_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    try:
        await message.answer_audio(audio=FSInputFile(mp3_path))
        await status_msg.delete()
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=True
        )
        logger.info("✅ MP3 отправлен (пользователь: %s)", message.from_user.id)
    except Exception as e:
        logger.error("Ошибка при отправке MP3: %s", e, exc_info=True)
        await status_msg.edit_text(t("mp3.send_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
    finally:
        try:
            os.remove(mp3_path)
        except Exception:
            pass


async def _download_and_send_mp3(
    message: Message,
    db: DatabaseService,
    status_msg: Message,
    url: str,
    t: Translator,
) -> None:
    """Скачивает видео по URL и отправляет аудио как MP3."""
    platform = downloader.get_platform_name(url)
    await status_msg.edit_text(t("mp3.download_status", platform=platform))

    try:
        result = await downloader.download(url)
    except Exception as e:
        logger.error("Ошибка скачивания: %s", e, exc_info=True)
        await status_msg.edit_text(t("mp3.download_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    if not result.success:
        reason = html.escape(translate_download_error(t, result))
        await status_msg.edit_text(t("mp3.failed", reason=reason))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    await _send_mp3(message, db, status_msg, result.file_path, platform, url, t)


@router.message(Command("mp3"))
async def cmd_mp3(message: Message, state: FSMContext, db: DatabaseService, t: Translator) -> None:
    """Обработчик /mp3 — принимает URL в команде или переходит в режим ожидания."""
    text = message.text or ""
    parts = text.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""

    url = extract_url(rest) if rest else None

    if url:
        await state.clear()
        status_msg = await message.answer(t("common.processing"))
        await _download_and_send_mp3(message, db, status_msg, url, t)
        return

    prompt = await message.answer(
        t("mp3.prompt"),
        reply_markup=_cancel_keyboard(message.from_user.id, t),
    )
    await state.set_state(Mp3States.waiting_for_input)
    await state.update_data(prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("cancel_mp3:"))
async def cancel_mp3(callback: CallbackQuery, state: FSMContext, t: Translator) -> None:
    """Отмена ожидания — только инициатор может отменить."""
    owner_id = int(callback.data.split(":")[1])
    if callback.from_user.id != owner_id:
        await callback.answer(t("common.not_your_operation"), show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(t("mp3.cancelled"))
    await callback.answer()


@router.message(Mp3States.waiting_for_input, F.text)
async def mp3_got_url(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    """Получена ссылка в режиме ожидания."""
    url = extract_url(message.text)

    if not url:
        await message.answer(t("mp3.url_not_found"))
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
    await _download_and_send_mp3(message, db, status_msg, url, t)


@router.message(Mp3States.waiting_for_input, F.video | F.document)
async def mp3_got_video(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    """Получен видеофайл в режиме ожидания."""
    if message.video:
        file_id = message.video.file_id
    elif message.document:
        mime = message.document.mime_type or ""
        if not mime.startswith("video/"):
            await message.answer(t("mp3.not_video"))
            return
        file_id = message.document.file_id
    else:
        return

    data = await state.get_data()
    await state.clear()

    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass

    status_msg = await message.answer(t("mp3.upload_video_status"))
    upload_path = str(Path(DOWNLOAD_DIR) / f"upload_{uuid.uuid4().hex[:8]}.mp4")

    try:
        try:
            tg_file = await message.bot.get_file(file_id)
            await message.bot.download_file(tg_file.file_path, destination=upload_path)
        except Exception as e:
            logger.error("Ошибка загрузки файла из Telegram: %s", e, exc_info=True)
            await status_msg.edit_text(t("mp3.upload_failed"))
            return
        await _send_mp3(
            message,
            db,
            status_msg,
            upload_path,
            "Upload",
            f"upload:{message.from_user.id}",
            t,
        )
    finally:
        if os.path.exists(upload_path):
            try:
                os.remove(upload_path)
            except Exception:
                pass
