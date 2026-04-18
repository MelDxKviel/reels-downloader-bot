"""
Inline-mode загрузка видео.

Пользователь набирает `@bot_username <ссылка>` в любом чате — бот отвечает
одной inline-карточкой. Если видео уже есть в локальном кэше с сохранённым
Telegram file_id, карточка превращается в готовое видео, и Telegram отправляет
его моментально. Иначе карточка отправляется как текстовая "заглушка", а после
выбора (chosen_inline_result) бот скачивает ролик и подменяет текст на видео
через editMessageMedia.

ВАЖНО: для обработки выбора карточки требуется включить inline feedback у
BotFather командой /setinlinefeedback (выставить 100%). Без этого путь
"скачать и прислать" работать не будет.
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

from src.services.database import DatabaseService
from src.services.downloader import downloader
from src.services.url_utils import get_url_hash

logger = logging.getLogger(__name__)

router = Router()

URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com|youtu\.be|instagram\.com|kkinstagram\.com"
    r"|tiktok\.com|vt\.tiktok\.com|vm\.tiktok\.com|twitter\.com|x\.com)"
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

    try:
        edited = await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaVideo(
                media=FSInputFile(result.file_path),
                supports_streaming=True,
                caption=result.title or None,
            ),
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (inline): %s", e, exc_info=True)
        await _safe_edit_text(
            bot,
            inline_message_id,
            "❌ <b>Не удалось отправить видео</b>\n\nВозможно, файл слишком большой.",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    # editMessageMedia с inline_message_id возвращает True — file_id не приходит.
    # Закешируем file_id в следующий раз, когда видео уйдёт через обычный чат.
    _ = edited

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
