"""
Обработчик загрузки видео по URL.
"""

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    InputRichMessage,
    Message,
)

from src.services.database import DatabaseService
from src.services.downloader import CarouselSlide, DownloadResult, downloader
from src.services.i18n import Translator, translate_download_error
from src.services.url_utils import extract_url

logger = logging.getLogger(__name__)

router = Router()

# Подпись карусели обрезается до разумной длины: og:title Instagram бывает
# длинным ("username on Instagram: <весь текст поста>").
_CAROUSEL_CAPTION_MAX = 1024


def _build_slideshow_html(slides: list[CarouselSlide], caption: str | None = None) -> str:
    """Собирает HTML нативной карусели Telegram (``<tg-slideshow>``, Bot API 10.1).

    В rich-сообщениях медиа задаётся ТОЛЬКО публичным http(s) URL (file_id и
    multipart не поддерживаются), поэтому каждый слайд — это ``<img>``/``<video>``
    с исходным URL Instagram CDN. URL и подпись экранируются как HTML.
    """
    parts = ["<tg-slideshow>"]
    for slide in slides:
        src = html.escape(slide.url, quote=True)
        if getattr(slide, "is_video", False):
            parts.append(f'<video src="{src}"/>')
        else:
            parts.append(f'<img src="{src}"/>')
    if caption:
        text = html.escape(caption.strip()[:_CAROUSEL_CAPTION_MAX])
        if text:
            parts.append(f"<figcaption>{text}</figcaption>")
    parts.append("</tg-slideshow>")
    return "".join(parts)


async def _send_rich_carousel(
    message: Message, slides: list[CarouselSlide], caption: str | None
) -> bool:
    """Отправляет слайды нативной каруселью Telegram (``sendRichMessage``).

    Возвращает ``True`` при успехе. При любой ошибке (Bot API сервер без
    поддержки rich-сообщений, Telegram не смог скачать URL и т.п.) возвращает
    ``False`` — вызывающий код откатывается на отправку альбомом из локальных
    файлов, поэтому в худшем случае поведение совпадает с прежним.
    """
    if message.bot is None:
        return False
    rich_html = _build_slideshow_html(slides, caption)
    try:
        await message.bot.send_rich_message(
            chat_id=message.chat.id,
            rich_message=InputRichMessage(html=rich_html),
        )
        return True
    except TelegramAPIError as e:
        logger.warning("Rich-карусель не отправлена, фолбэк на альбом: %s", e)
        return False
    except Exception as e:  # фолбэк надёжнее падения хендлера
        logger.warning("Rich-карусель: неожиданная ошибка, фолбэк на альбом: %s", e)
        return False


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

        # 1) Нативная карусель Telegram (Bot API 10.1, <tg-slideshow>) — один
        #    свайпаемый пост (фото и/или видео) вместо альбома-сетки. Медиа
        #    задаётся публичными URL Instagram CDN, локальные файлы не нужны.
        slides = result.carousel_slides if isinstance(result.carousel_slides, list) else None
        sent_as_carousel = False
        if slides and len(slides) >= 2:
            sent_as_carousel = await _send_rich_carousel(message, slides, result.title)

        # 2) Фолбэк, если карусель не собрана или Telegram не смог её отправить
        #    (старый Bot API сервер, недоступный URL): отдаём скачанные файлы —
        #    альбом/одиночное фото для фото-постов, иначе видео.
        if not sent_as_carousel:
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
