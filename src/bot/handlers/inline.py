"""
Inline-mode загрузка видео.

Пользователь набирает `@bot_username <ссылка>` в любом чате — бот отвечает
одной inline-карточкой. Если видео уже есть в локальном кэше с сохранённым
Telegram file_id, карточка превращается в готовое видео, и Telegram отправляет
его моментально. Иначе карточка отправляется как текстовая "заглушка", а после
выбора (chosen_inline_result) бот скачивает ролик, публикует его в storage-чат,
получает оттуда file_id и подменяет текст на видео через editMessageMedia.

ВАЖНО:
- inline feedback у BotFather (`/setinlinefeedback → 100%`) должен быть включён,
  иначе не приходит chosen_inline_result и «медленный» путь не работает.
- Для загрузки новых видео в inline-сообщения Telegram принимает только file_id
  или URL (multipart запрещён). Поэтому видео сначала отправляется в storage-чат
  (`VIDEO_STORAGE_CHAT_ID` или первый `ADMIN_USERS`), и только затем его file_id
  подставляется в editMessageMedia.
"""

import logging
import re
from typing import Optional

from aiogram import Bot, Router
from aiogram.types import (
    ChosenInlineResult,
    FSInputFile,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InputMediaVideo,
    InputTextMessageContent,
)

from src.config import ADMIN_USERS, VIDEO_STORAGE_CHAT_ID
from src.services.database import DatabaseService
from src.services.downloader import downloader
from src.services.url_utils import get_url_hash

logger = logging.getLogger(__name__)

router = Router()

# Любой поддомен (`m.youtube.com`, `vt.tiktok.com`, `music.youtube.com` и т.п.) —
# такой же диапазон, как у downloader.is_supported_url.
URL_PATTERN = re.compile(
    r"https?://(?:[\w-]+\.)*"
    r"(?:youtube\.com|youtu\.be|instagram\.com|kkinstagram\.com"
    r"|tiktok\.com|twitter\.com|x\.com)"
    r'[^\s<>"\']*',
    re.IGNORECASE,
)

HINT_TITLE = "Введите ссылку на видео"
HINT_DESCRIPTION = "YouTube · Instagram · TikTok · X/Twitter"
HINT_MESSAGE = (
    "📎 Чтобы скачать видео через inline-режим, отправьте ссылку "
    "с поддерживаемой платформы (YouTube, Instagram, TikTok, X/Twitter)."
)

INVALID_TITLE = "❌ Ссылка не распознана"
INVALID_DESCRIPTION = "Поддерживаются: YouTube, Instagram, TikTok, X/Twitter"
INVALID_MESSAGE = (
    "❌ Не удалось распознать ссылку.\nПоддерживаются: YouTube, Instagram, TikTok, X/Twitter."
)


def _extract_url(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


@router.inline_query()
async def inline_query_handler(query: InlineQuery) -> None:
    """Формирует inline-результат по введённому тексту."""
    text = (query.query or "").strip()

    if not text:
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id="hint",
                    title=HINT_TITLE,
                    description=HINT_DESCRIPTION,
                    input_message_content=InputTextMessageContent(message_text=HINT_MESSAGE),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return

    url = _extract_url(text)
    if url is None:
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id="invalid",
                    title=INVALID_TITLE,
                    description=INVALID_DESCRIPTION,
                    input_message_content=InputTextMessageContent(message_text=INVALID_MESSAGE),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return

    platform = downloader.get_platform_name(url)
    result_id = get_url_hash(url)

    # Быстрый путь: видео уже знакомо Telegram — возвращаем cached video.
    cached_file_id = downloader.get_telegram_file_id(url)
    if cached_file_id:
        await query.answer(
            results=[
                InlineQueryResultCachedVideo(
                    id=f"cached:{result_id}",
                    video_file_id=cached_file_id,
                    title=f"🎬 Видео с {platform}",
                    description="Отправить моментально (из кэша)",
                )
            ],
            cache_time=60,
            is_personal=True,
        )
        return

    # Медленный путь: отправляем заглушку-статью и докачиваем в chosen_inline_result.
    await query.answer(
        results=[
            InlineQueryResultArticle(
                id=f"download:{result_id}",
                title=f"📥 Скачать видео с {platform}",
                description=url[:128],
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Загружаю видео с <b>{platform}</b>...",
                    parse_mode="HTML",
                ),
            )
        ],
        cache_time=1,
        is_personal=True,
    )


@router.chosen_inline_result()
async def chosen_inline_handler(chosen: ChosenInlineResult, bot: Bot, db: DatabaseService) -> None:
    """Докачивает видео и заменяет текстовую заглушку на видео."""
    result_id = chosen.result_id or ""

    # cached:* — видео уже было прикреплено к inline-результату, ничего не делаем,
    # только записываем статистику.
    if result_id.startswith("cached:"):
        url = _extract_url(chosen.query)
        if url:
            await db.record_download(
                user_id=chosen.from_user.id,
                platform=downloader.get_platform_name(url),
                url=url,
                success=True,
            )
        return

    if not result_id.startswith("download:"):
        return

    inline_message_id = chosen.inline_message_id
    if not inline_message_id:
        logger.warning(
            "chosen_inline_result без inline_message_id — включите inline feedback у BotFather"
        )
        return

    url = _extract_url(chosen.query)
    if url is None:
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=INVALID_MESSAGE,
            )
        except Exception:
            pass
        return

    platform = downloader.get_platform_name(url)
    user_id = chosen.from_user.id

    try:
        result = await downloader.download(url)
    except Exception as e:
        logger.error("Ошибка скачивания (inline): %s", e, exc_info=True)
        await _safe_edit_text(
            bot, inline_message_id, "❌ <b>Произошла ошибка</b>\n\nПопробуйте позже."
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    if not result.success or not result.file_path:
        await _safe_edit_text(
            bot,
            inline_message_id,
            f"❌ <b>Не удалось скачать видео</b>\n\nПричина: {result.error or 'неизвестная ошибка'}",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    # Для inline-редактирования Telegram не принимает multipart-загрузку — только
    # file_id/URL. Поэтому сперва заливаем видео в storage-чат и извлекаем file_id.
    file_id = await _upload_and_get_file_id(bot, result.file_path)
    if not file_id:
        await _safe_edit_text(
            bot,
            inline_message_id,
            "❌ <b>Не удалось опубликовать видео</b>\n\n"
            "Inline-storage не настроен или видео не загрузилось. "
            "Попросите администратора настроить <code>VIDEO_STORAGE_CHAT_ID</code>.",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    # Кэшируем file_id сразу — следующий такой же inline-запрос пойдёт мгновенным путём.
    downloader.set_telegram_file_id(url, file_id)

    try:
        await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaVideo(
                media=file_id,
                supports_streaming=True,
                caption=result.title or None,
            ),
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (inline): %s", e, exc_info=True)
        await _safe_edit_text(
            bot,
            inline_message_id,
            "❌ <b>Не удалось отправить видео</b>\n\nПопробуйте позже.",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    await db.record_download(user_id=user_id, platform=platform, url=url, success=True)
    logger.info(
        "✅ Inline-видео отправлено (user: %s, url: %s, из кэша: %s)",
        user_id,
        url,
        result.from_cache,
    )


async def _safe_edit_text(bot: Bot, inline_message_id: str, text: str) -> None:
    try:
        await bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=text,
        )
    except Exception as e:
        logger.debug("Не удалось отредактировать inline-сообщение: %s", e)


def _resolve_storage_chat_id() -> Optional[int]:
    """Определяет чат для промежуточной публикации видео ради file_id."""
    if VIDEO_STORAGE_CHAT_ID is not None:
        return VIDEO_STORAGE_CHAT_ID
    if ADMIN_USERS:
        return ADMIN_USERS[0]
    return None


async def _upload_and_get_file_id(bot: Bot, file_path: str) -> Optional[str]:
    """
    Заливает видео в storage-чат и возвращает его Telegram file_id.
    Промежуточное сообщение удаляется (best-effort), file_id остаётся валидным.
    """
    storage_chat_id = _resolve_storage_chat_id()
    if storage_chat_id is None:
        logger.error("Нет VIDEO_STORAGE_CHAT_ID и ADMIN_USERS — inline не может опубликовать видео")
        return None

    try:
        staging = await bot.send_video(
            chat_id=storage_chat_id,
            video=FSInputFile(file_path),
            supports_streaming=True,
            disable_notification=True,
        )
    except Exception as e:
        logger.error(
            "Не удалось выгрузить видео в storage-чат %s: %s",
            storage_chat_id,
            e,
            exc_info=True,
        )
        return None

    file_id = staging.video.file_id if staging.video else None
    if not file_id:
        logger.error("Сообщение в storage-чате не содержит video — file_id не получен")

    try:
        await bot.delete_message(chat_id=storage_chat_id, message_id=staging.message_id)
    except Exception as e:
        logger.debug("Не удалось удалить промежуточное сообщение в storage: %s", e)

    return file_id
