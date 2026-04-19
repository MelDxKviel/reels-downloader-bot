"""
Обработчик загрузки видео по URL.
"""

import logging
import re

from aiogram import F, Router
from aiogram.types import FSInputFile, InputMediaPhoto, Message

from src.services.database import DatabaseService
from src.services.downloader import DownloadResult, downloader

logger = logging.getLogger(__name__)

router = Router()

# Регулярное выражение для поиска URL в тексте
URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com|youtu\.be|instagram\.com|kkinstagram\.com|tiktok\.com|vt\.tiktok\.com|vm\.tiktok\.com|twitter\.com|x\.com)"
    r'[^\s<>"\']*',
    re.IGNORECASE,
)


@router.message(F.text)
async def handle_url(message: Message, db: DatabaseService) -> None:
    """Обработчик текстовых сообщений с URL."""
    text = message.text

    # Ищем URL в тексте
    match = URL_PATTERN.search(text)

    if not match:
        # Проверяем, похоже ли сообщение на ссылку
        if any(
            domain in text.lower()
            for domain in ["youtube", "instagram", "kkinstagram", "tiktok", "twitter", "x.com"]
        ):
            await message.answer(
                "🤔 Похоже, вы хотели отправить ссылку, но она некорректна.\n"
                "Пожалуйста, скопируйте полную ссылку на видео."
            )
        else:
            await message.answer(
                "📎 Отправьте мне ссылку на видео с YouTube, Instagram, TikTok или X/Twitter.\n"
                "Используйте /help для получения справки."
            )
        return

    url = match.group(0)
    platform = downloader.get_platform_name(url)

    # Отправляем сообщение о начале скачивания
    status_message = await message.answer(
        f"⏳ Скачиваю с <b>{platform}</b>...\nЭто может занять некоторое время."
    )

    try:
        # Скачиваем видео или фото
        result: DownloadResult = await downloader.download(url)

        if not result.success:
            await status_message.edit_text(
                f"❌ <b>Не удалось скачать</b>\n\nПричина: {result.error}"
            )
            return

        media_label = "фото" if result.is_photo else "видео"

        # Обновляем статус
        if result.from_cache:
            await status_message.edit_text(f"📤 Отправляю {media_label} из кэша...")
        else:
            await status_message.edit_text(f"📤 Отправляю {media_label}...")

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

        await status_message.edit_text(
            "❌ <b>Произошла ошибка</b>\n\nПопробуйте позже или используйте другую ссылку."
        )
