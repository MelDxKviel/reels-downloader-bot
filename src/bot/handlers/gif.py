"""
Обработчик команды /gif — конвертация видео в анимированный GIF.
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

MAX_GIF_DURATION = 10


class GifStates(StatesGroup):
    waiting_for_input = State()


def _cancel_keyboard(user_id: int, t: Translator) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("common.cancel_button"), callback_data=f"cancel_gif:{user_id}"
                )
            ]
        ]
    )


async def _convert_to_gif(input_path: str) -> Optional[str]:
    """Конвертирует видео в GIF (макс 10с, 480px, 10fps, палитра palettegen/paletteuse)."""
    if not shutil.which("ffmpeg"):
        return None

    output_path = str(Path(DOWNLOAD_DIR) / f"gif_{uuid.uuid4().hex[:8]}.gif")

    # Однопроходная генерация палитры через split — значительно лучше цветопередача
    # 10 fps: плавно и экономично для соцсетей; 480px ширина — баланс качества и размера;
    # sierra2_4a — лучший дизеринг для компактных GIF
    vf = (
        "fps=10,scale=480:-1:flags=lanczos,"
        "split[s0][s1];"
        "[s0]palettegen=max_colors=256:stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=sierra2_4a"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-t",
        str(MAX_GIF_DURATION),
        "-vf",
        vf,
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


async def _send_gif(
    message: Message,
    db: DatabaseService,
    status_msg: Message,
    file_path: str,
    platform: str,
    url: str,
    t: Translator,
) -> None:
    """Конвертирует файл и отправляет как анимацию (GIF)."""
    await status_msg.edit_text(t("gif.convert_status"))

    gif_path = await _convert_to_gif(file_path)

    if gif_path is None:
        await status_msg.edit_text(t("gif.convert_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    try:
        await message.answer_animation(animation=FSInputFile(gif_path))
        await status_msg.delete()
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=True
        )
        logger.info("✅ GIF отправлен (пользователь: %s)", message.from_user.id)
    except Exception as e:
        logger.error("Ошибка при отправке GIF: %s", e, exc_info=True)
        await status_msg.edit_text(t("gif.send_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
    finally:
        try:
            os.remove(gif_path)
        except Exception:
            pass


async def _download_and_send_gif(
    message: Message,
    db: DatabaseService,
    status_msg: Message,
    url: str,
    t: Translator,
) -> None:
    """Скачивает видео по URL и отправляет как GIF."""
    platform = downloader.get_platform_name(url)
    await status_msg.edit_text(t("gif.download_status", platform=platform))

    try:
        result = await downloader.download(url)
    except Exception as e:
        logger.error("Ошибка скачивания: %s", e, exc_info=True)
        await status_msg.edit_text(t("gif.download_error"))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    if not result.success:
        reason = html.escape(translate_download_error(t, result))
        await status_msg.edit_text(t("gif.failed", reason=reason))
        await db.record_download(
            user_id=message.from_user.id, platform=platform, url=url, success=False
        )
        return

    await _send_gif(message, db, status_msg, result.file_path, platform, url, t)


@router.message(Command("gif"))
async def cmd_gif(message: Message, state: FSMContext, db: DatabaseService, t: Translator) -> None:
    """Обработчик /gif — принимает URL в команде или переходит в режим ожидания."""
    text = message.text or ""
    parts = text.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""

    url = extract_url(rest) if rest else None

    if url:
        await state.clear()
        status_msg = await message.answer(t("common.processing"))
        await _download_and_send_gif(message, db, status_msg, url, t)
        return

    prompt = await message.answer(
        t("gif.prompt"),
        reply_markup=_cancel_keyboard(message.from_user.id, t),
    )
    await state.set_state(GifStates.waiting_for_input)
    await state.update_data(prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("cancel_gif:"))
async def cancel_gif(callback: CallbackQuery, state: FSMContext, t: Translator) -> None:
    """Отмена ожидания — только инициатор может отменить."""
    owner_id = int(callback.data.split(":")[1])
    if callback.from_user.id != owner_id:
        await callback.answer(t("common.not_your_operation"), show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(t("gif.cancelled"))
    await callback.answer()


@router.message(GifStates.waiting_for_input, F.text)
async def gif_got_url(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    """Получена ссылка в режиме ожидания."""
    url = extract_url(message.text)

    if not url:
        await message.answer(t("gif.url_not_found"))
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
    await _download_and_send_gif(message, db, status_msg, url, t)


@router.message(GifStates.waiting_for_input, F.video | F.document)
async def gif_got_video(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    """Получен видеофайл в режиме ожидания."""
    if message.video:
        file_id = message.video.file_id
    elif message.document:
        mime = message.document.mime_type or ""
        if not mime.startswith("video/"):
            await message.answer(t("gif.not_video"))
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

    status_msg = await message.answer(t("gif.upload_video_status"))
    upload_path = str(Path(DOWNLOAD_DIR) / f"upload_{uuid.uuid4().hex[:8]}.mp4")

    try:
        try:
            tg_file = await message.bot.get_file(file_id)
            await message.bot.download_file(tg_file.file_path, destination=upload_path)
        except Exception as e:
            logger.error("Ошибка загрузки файла из Telegram: %s", e, exc_info=True)
            await status_msg.edit_text(t("gif.upload_failed"))
            return
        await _send_gif(
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
