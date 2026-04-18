"""
Обработчик команды /mp3 — конвертация видео в аудио MP3.
"""

import asyncio
import logging
import os
import re
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

logger = logging.getLogger(__name__)

router = Router()

URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com|youtu\.be|instagram\.com|kkinstagram\.com"
    r"|tiktok\.com|vt\.tiktok\.com|vm\.tiktok\.com|twitter\.com|x\.com)"
    r'[^\s<>"\']*',
    re.IGNORECASE,
)


class Mp3States(StatesGroup):
    waiting_for_input = State()


def _cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_mp3:{user_id}")]
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
) -> None:
    """Конвертирует файл и отправляет как аудио MP3."""
    await status_msg.edit_text("🔄 Конвертирую в MP3...")

    mp3_path = await _convert_to_mp3(file_path)

    if mp3_path is None:
        await status_msg.edit_text(
            "❌ <b>Ошибка конвертации</b>\n\nFFmpeg не найден или произошла ошибка обработки."
        )
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
        await status_msg.edit_text("❌ <b>Ошибка при отправке</b>\n\nПопробуйте позже.")
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
) -> None:
    """Скачивает видео по URL и отправляет аудио как MP3."""
    platform = downloader.get_platform_name(url)
    await status_msg.edit_text(f"⏳ Скачиваю видео с <b>{platform}</b>...")

    try:
        result = await downloader.download(url)
    except Exception as e:
        logger.error("Ошибка скачивания: %s", e, exc_info=True)
        await status_msg.edit_text("❌ <b>Ошибка при скачивании</b>\n\nПопробуйте позже.")
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    if not result.success:
        await status_msg.edit_text(f"❌ <b>Не удалось скачать видео</b>\n\nПричина: {result.error}")
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    await _send_mp3(message, db, status_msg, result.file_path, platform, url)


@router.message(Command("mp3"))
async def cmd_mp3(message: Message, state: FSMContext, db: DatabaseService) -> None:
    """Обработчик /mp3 — принимает URL в команде или переходит в режим ожидания."""
    text = message.text or ""
    parts = text.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""

    match = URL_PATTERN.search(rest) if rest else None

    if match:
        url = match.group(0)
        await state.clear()
        status_msg = await message.answer("⏳ Обрабатываю...")
        await _download_and_send_mp3(message, db, status_msg, url)
        return

    prompt = await message.answer(
        "🎵 Отправьте ссылку на видео или mp4-файл.\n\n"
        "Я извлеку аудио и пришлю его в формате <b>MP3</b>.",
        reply_markup=_cancel_keyboard(message.from_user.id),
    )
    await state.set_state(Mp3States.waiting_for_input)
    await state.update_data(prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("cancel_mp3:"))
async def cancel_mp3(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена ожидания — только инициатор может отменить."""
    owner_id = int(callback.data.split(":")[1])
    if callback.from_user.id != owner_id:
        await callback.answer("Это не ваша операция.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ Конвертация в MP3 отменена.")
    await callback.answer()


@router.message(Mp3States.waiting_for_input, F.text)
async def mp3_got_url(message: Message, state: FSMContext, db: DatabaseService) -> None:
    """Получена ссылка в режиме ожидания."""
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
    await _download_and_send_mp3(message, db, status_msg, url)


@router.message(Mp3States.waiting_for_input, F.video | F.document)
async def mp3_got_video(message: Message, state: FSMContext, db: DatabaseService) -> None:
    """Получен видеофайл в режиме ожидания."""
    if message.video:
        file_id = message.video.file_id
    elif message.document:
        mime = message.document.mime_type or ""
        if not mime.startswith("video/"):
            await message.answer("⚠️ Это не видеофайл. Отправьте mp4 или ссылку на видео.")
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

    status_msg = await message.answer("⏳ Загружаю видео...")
    upload_path = str(Path(DOWNLOAD_DIR) / f"upload_{uuid.uuid4().hex[:8]}.mp4")

    try:
        try:
            tg_file = await message.bot.get_file(file_id)
            await message.bot.download_file(tg_file.file_path, destination=upload_path)
        except Exception as e:
            logger.error("Ошибка загрузки файла из Telegram: %s", e, exc_info=True)
            await status_msg.edit_text("❌ <b>Не удалось загрузить файл</b>")
            return
        await _send_mp3(
            message,
            db,
            status_msg,
            upload_path,
            "Upload",
            f"upload:{message.from_user.id}",
        )
    finally:
        if os.path.exists(upload_path):
            try:
                os.remove(upload_path)
            except Exception:
                pass
