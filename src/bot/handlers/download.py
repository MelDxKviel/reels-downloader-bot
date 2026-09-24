"""
Обработчик загрузки видео по URL.
"""

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from src.bot.rich_carousel import send_rich_carousel
from src.bot.telegram_retry import (
    TELEGRAM_UPLOAD_TIMEOUT,
    retry_transient_telegram,
    telegram_duration,
)
from src.services.database import DatabaseService
from src.services.downloader import CarouselSlide, DownloadResult, downloader
from src.services.i18n import Translator, translate_download_error
from src.services.url_utils import extract_url

logger = logging.getLogger(__name__)

router = Router()

_TELEGRAM_MEDIA_GROUP_MAX = 10


async def _send_photo_paths(
    message: Message, photo_paths: list[str], *, as_documents: bool = False
) -> None:
    """Send every fallback file, splitting legacy albums at Telegram's limit."""

    for start in range(0, len(photo_paths), _TELEGRAM_MEDIA_GROUP_MAX):
        chunk = photo_paths[start : start + _TELEGRAM_MEDIA_GROUP_MAX]
        try:
            if len(chunk) > 1:
                media = (
                    [InputMediaDocument(media=FSInputFile(path)) for path in chunk]
                    if as_documents
                    else [InputMediaPhoto(media=FSInputFile(path)) for path in chunk]
                )
                await retry_transient_telegram(
                    lambda: message.bot.send_media_group(
                        chat_id=message.chat.id,
                        media=media,
                        request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                    ),
                    "sendMediaGroup(documents)" if as_documents else "sendMediaGroup(user)",
                )
            elif as_documents:
                await retry_transient_telegram(
                    lambda: message.bot.send_document(
                        chat_id=message.chat.id,
                        document=FSInputFile(chunk[0]),
                        request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                    ),
                    "sendDocument(user)",
                )
            else:
                await retry_transient_telegram(
                    lambda: message.bot.send_photo(
                        chat_id=message.chat.id,
                        photo=FSInputFile(chunk[0]),
                        request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                    ),
                    "sendPhoto(user)",
                )
        except TelegramBadRequest as exc:
            if as_documents or "IMAGE_PROCESS_FAILED" not in str(exc):
                raise
            # Only retry the failed chunk. Earlier chunks have already been sent
            # successfully and must not be duplicated as documents.
            await _send_photo_paths(message, chunk, as_documents=True)


async def _send_carousel_files(message: Message, slides: list[CarouselSlide]) -> bool:
    """Keep every ordered local slide when rich messages are unavailable."""
    if not all(slide.local_path for slide in slides):
        return False
    for start in range(0, len(slides), _TELEGRAM_MEDIA_GROUP_MAX):
        chunk = slides[start : start + _TELEGRAM_MEDIA_GROUP_MAX]
        if not any(slide.is_video for slide in chunk):
            await _send_photo_paths(message, [slide.local_path for slide in chunk])
            continue
        media = [
            InputMediaVideo(
                media=FSInputFile(slide.local_path),
                width=slide.width,
                height=slide.height,
                duration=telegram_duration(slide.duration),
                supports_streaming=True,
            )
            if slide.is_video
            else InputMediaPhoto(media=FSInputFile(slide.local_path))
            for slide in chunk
        ]
        if len(media) > 1:
            await retry_transient_telegram(
                lambda: message.bot.send_media_group(
                    chat_id=message.chat.id,
                    media=media,
                    message_thread_id=message.message_thread_id,
                    request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                ),
                "sendMediaGroup(carousel fallback)",
            )
        else:
            slide = chunk[0]
            await retry_transient_telegram(
                lambda: message.answer_video(
                    video=FSInputFile(slide.local_path),
                    width=slide.width,
                    height=slide.height,
                    duration=telegram_duration(slide.duration),
                    supports_streaming=True,
                    request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                ),
                "sendVideo(carousel fallback)",
            )
    return True


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

    result = None
    try:
        # Reserve cached media until every Telegram upload finishes.
        result: DownloadResult = await downloader.download(
            url, user_id=message.from_user.id, reserve=True
        )

        if not result.success:
            reason = html.escape(translate_download_error(t, result))
            await status_message.edit_text(t("download.failed", reason=reason))
            await db.record_download(
                user_id=message.from_user.id, platform=platform, url=url, success=False
            )
            return

        is_carousel = bool(
            isinstance(result.carousel_slides, list) and len(result.carousel_slides) >= 2
        )
        if is_carousel:
            media_label = t("download.media_label.carousel")
        elif result.is_photo:
            media_label = t("download.media_label.photo")
        else:
            media_label = t("download.media_label.video")

        # Обновляем статус
        if result.from_cache:
            await status_message.edit_text(t("download.from_cache_status", media_label=media_label))
        else:
            await status_message.edit_text(t("download.send_status", media_label=media_label))

        # 1) Нативная карусель Telegram (Bot API 10.2, <tg-slideshow>) — один
        #    свайпаемый пост вместо альбома-сетки. Скачанные фото прикладываются
        #    напрямую; публичные Instagram CDN URL остаются fallback-вариантом.
        slides = result.carousel_slides if isinstance(result.carousel_slides, list) else None
        sent_as_carousel = False
        if slides and len(slides) >= 2 and message.bot is not None:
            media_paths = result.photo_paths if result.is_photo else None
            sent_as_carousel = await send_rich_carousel(
                message.bot,
                message.chat.id,
                slides,
                result.title,
                media_paths=media_paths,
            )

        # 2) Фолбэк, если карусель не собрана или Telegram не смог её отправить
        #    (старый Bot API сервер, недоступный URL): отдаём скачанные файлы —
        #    альбом/одиночное фото для фото-постов, иначе видео.
        if not sent_as_carousel and slides and len(slides) >= 2:
            sent_as_carousel = await _send_carousel_files(message, slides)
        if not sent_as_carousel:
            if result.is_photo:
                photo_paths = result.photo_paths or [result.file_path]
                await _send_photo_paths(message, photo_paths)
            else:
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
                # Сохраняем Telegram file_id, чтобы inline-режим отдавал видео моментально
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
    finally:
        if result is not None:
            downloader.release_result(result)
