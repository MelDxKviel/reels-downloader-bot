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

from src.config import (
    DOWNLOAD_DIR,
    GIF_CRF,
    GIF_FPS,
    GIF_MAX_DURATION,
    GIF_MAX_SIZE,
    MAX_FILE_SIZE,
)
from src.services.database import DatabaseService
from src.services.downloader import downloader
from src.services.i18n import Translator, translate_download_error
from src.services.url_utils import extract_url

logger = logging.getLogger(__name__)

router = Router()


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


def _gif_video_filter(fps: int, max_size: int) -> str:
    """Filtergraph: cap fps, fit the long side into ``max_size`` (no upscale), even dims.

    The single quotes around each scale expression protect the inner commas from the
    filtergraph parser. The trailing ``trunc(.../2)*2`` pass guarantees even width and
    height, which ``yuv420p`` H.264 requires.
    """
    return (
        f"fps={fps},"
        f"scale=w='min(iw,{max_size})':h='min(ih,{max_size})':"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        "scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'"
    )


async def _run_ffmpeg(cmd: list[str], timeout: int = 180) -> Optional[int]:
    """Run an ffmpeg command, returning its exit code (or None on timeout)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return None
    return proc.returncode


def _remove_quietly(path: str) -> None:
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


async def _convert_to_gif(input_path: str) -> Optional[str]:
    """Build a compact, smooth looping animation (silent H.264 mp4).

    Telegram renders a soundless H.264 mp4 as a GIF (``sendAnimation``), but it is
    far lighter and smoother than a real palette-based ``.gif`` — which lets us keep
    a high frame rate without the file ballooning. The long side is capped at
    ``GIF_MAX_SIZE``, the duration at ``GIF_MAX_DURATION`` and the frame rate at
    ``GIF_FPS``. If the first pass still exceeds Telegram's upload limit, it is
    re-encoded progressively smaller until it fits.
    """
    if not shutil.which("ffmpeg"):
        return None

    output_path = str(Path(DOWNLOAD_DIR) / f"gif_{uuid.uuid4().hex[:8]}.mp4")

    # (fps, max_size, crf): best quality first, falling back to smaller/cheaper
    # encodes only if the result overshoots Telegram's size limit.
    attempts = [
        (GIF_FPS, GIF_MAX_SIZE, GIF_CRF),
        (min(GIF_FPS, 20), min(GIF_MAX_SIZE, 480), min(51, GIF_CRF + 6)),
        (min(GIF_FPS, 15), min(GIF_MAX_SIZE, 360), min(51, GIF_CRF + 10)),
    ]

    for fps, max_size, crf in attempts:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-t",
            str(GIF_MAX_DURATION),
            "-an",
            "-vf",
            _gif_video_filter(fps, max_size),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "main",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-movflags",
            "+faststart",
            output_path,
        ]

        returncode = await _run_ffmpeg(cmd)

        if returncode != 0 or not os.path.exists(output_path):
            _remove_quietly(output_path)
            return None

        if os.path.getsize(output_path) <= MAX_FILE_SIZE:
            return output_path

        # Too heavy for Telegram — drop quality and retry.
        _remove_quietly(output_path)

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

    if not result.success or not result.file_path:
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
        t("gif.prompt", duration=GIF_MAX_DURATION, size=GIF_MAX_SIZE, fps=GIF_FPS),
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
