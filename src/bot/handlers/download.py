"""
Обработчик загрузки видео по URL.
"""

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaDocument, InputMediaPhoto, Message

from src.services.database import DatabaseService
from src.services.downloader import DownloadResult, downloader
from src.services.i18n import Translator, translate_download_error
from src.services.url_utils import extract_url

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text)
async def handle_url(message: Message, db: DatabaseService, t: Translator) -> None:
    """Обработчик текстовых сообщений с URL."""
    text = message.text

    # Ищем URL в тексте
    url = extract_url(text)

    if not url:
        # Проверяем, похоже ли сообщение на ссылку
        if any(
            domain in text.lower()
            for domain in ["youtube", "instagram", "kkinstagram", "tiktok", "twitter", "x.com"]
        ):
            await message.answer(t("download.invalid_link_hint"))
        else:
            await message.answer(t("download.send_link_hint"))
        return

    platform = downloader.get_platform_name(url)

    # Отправляем сообщение о начале скачивания
    status_message = await message.answer(t("download.start_status", platform=platform))

    try:
        # Скачиваем видео или фото
        result: DownloadResult = await downloader.download(url)

        if not result.success:
            reason = html.escape(translate_download_error(t, result))
            await status_message.edit_text(t("download.failed", reason=reason))
            return

        media_label = (
            t("download.media_label.photo") if result.is_photo else t("download.media_label.video")
        )

        # Обновляем статус
        if result.from_cache:
            await status_message.edit_text(t("download.from_cache_status", media_label=media_label))
        else:
            await status_message.edit_text(t("download.send_status", media_label=media_label))

        if result.is_photo:
            photo_paths = result.photo_paths or [result.file_path]
            try:
                if len(photo_paths) > 1:
                    media = [InputMediaPhoto(media=FSInputFile(p)) for p in photo_paths]
                    await message.answer_media_group(media=media)
                else:
                    await message.answer_photo(photo=FSInputFile(photo_paths[0]))
            except TelegramBadRequest as e:
                if "IMAGE_PROCESS_FAILED" not in str(e):
                    raise
                # Unsupported image format (e.g. WebP, HEIC) — send as file
                if len(photo_paths) > 1:
                    docs = [InputMediaDocument(media=FSInputFile(p)) for p in photo_paths]
                    await message.answer_media_group(media=docs)
                else:
                    await message.answer_document(document=FSInputFile(photo_paths[0]))
        else:
            sent = await message.answer_video(
                video=FSInputFile(result.file_path), supports_streaming=True
            )
            # Сохраняем Telegram file_id, чтобы inline-режим мог отдавать видео моментально
            if sent.video and sent.video.file_id:
                downloader.set_telegram_file_id(url, sent.video.file_id)

        # Удаляем сообщение о статусе
        await status_message.delete()

        # Записываем статистику
        user = message.from_user
        await db.record_download(user_id=user.id, platform=platform, url=url, success=True)

        logger.info(
            f"✅ {media_label.capitalize()} успешно отправлено: {result.title} "
            f"(пользователь: {message.from_user.id}, из кэша: {result.from_cache})"
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке видео: {e}", exc_info=True)

        # Записываем неудачную попытку
        user = message.from_user
        await db.record_download(user_id=user.id, platform=platform, url=url, success=False)

        await status_message.edit_text(t("download.generic_error"))
