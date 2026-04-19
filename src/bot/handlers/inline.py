"""
Inline-mode загрузка видео.

Пользователь набирает `@bot_username <ссылка>` в любом чате — бот отвечает
двумя inline-карточками: видео и MP3.
Если результат уже есть в кэше, карточка превращается в готовый файл и
Telegram отправляет его моментально. Иначе карточка отправляется как
текстовая "заглушка", а после выбора (chosen_inline_result) бот скачивает
ролик, конвертирует при необходимости, публикует в storage-чат,
получает оттуда file_id и подменяет текст на медиа через editMessageMedia.

ВАЖНО:
- inline feedback у BotFather (`/setinlinefeedback → 100%`) должен быть включён,
  иначе не приходит chosen_inline_result и «медленный» путь не работает.
- К placeholder-карточке ОБЯЗАТЕЛЬНО прикреплена inline-клавиатура: без неё
  Telegram не присылает `inline_message_id` в chosen_inline_result, и сообщение
  невозможно отредактировать (`Available only if there is an inline keyboard
  attached to the message`).
- Для загрузки новых медиа в inline-сообщения Telegram принимает только file_id
  или URL (multipart запрещён). Поэтому файл сначала отправляется в storage-чат
  (`VIDEO_STORAGE_CHAT_ID` или первый `ADMIN_USERS`), и только затем его file_id
  подставляется в editMessageMedia.
"""

import logging
import os
import re
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    ChosenInlineResult,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InputMediaAudio,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
)

from src.config import ADMIN_USERS, VIDEO_STORAGE_CHAT_ID
from src.services.database import DatabaseService
from src.services.downloader import downloader
from src.services.url_utils import get_url_hash

from .mp3 import _convert_to_mp3

logger = logging.getLogger(__name__)

router = Router()

URL_PATTERN = re.compile(
    r"https?://(?:[\w-]+\.)*"
    r"(?:youtube\.com|youtu\.be|instagram\.com|kkinstagram\.com"
    r"|tiktok\.com|twitter\.com|x\.com)"
    r'[^\s<>"\']*',
    re.IGNORECASE,
)

LOADING_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="⏳ Загружается…", callback_data="inline_loading")]]
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
    """Формирует inline-результаты по введённому тексту (видео, кружок, MP3)."""
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
    results = []

    # --- Видео/фото ---
    # Фактический тип медиа берём из cache entry (is_photo), чтобы
    # залежавшийся file_id другого типа не переключал выдачу.
    cached_media_type = downloader.get_cached_media_type(url)
    cached_photo_id = (
        downloader.get_telegram_photo_file_id(url) if cached_media_type == "photo" else None
    )
    cached_video_id = downloader.get_telegram_file_id(url) if cached_media_type != "photo" else None
    if cached_photo_id:
        results.append(
            InlineQueryResultCachedPhoto(
                id=f"cached_photo:{result_id}",
                photo_file_id=cached_photo_id,
                title=f"🖼 Скачать с {platform}",
                description="Отправить моментально (из кэша)",
            )
        )
    elif cached_video_id:
        results.append(
            InlineQueryResultCachedVideo(
                id=f"cached:{result_id}",
                video_file_id=cached_video_id,
                title=f"🎬 Скачать с {platform}",
                description="Отправить моментально (из кэша)",
            )
        )
    else:
        results.append(
            InlineQueryResultArticle(
                id=f"download:{result_id}",
                title=f"📥 Скачать с {platform}",
                description=url[:128],
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Загружаю с <b>{platform}</b>...",
                    parse_mode="HTML",
                ),
                reply_markup=LOADING_KEYBOARD,
            )
        )

    # --- MP3 ---
    cached_mp3_id = downloader.get_telegram_mp3_file_id(url)
    if cached_mp3_id:
        results.append(
            InlineQueryResultCachedAudio(
                id=f"cached_mp3:{result_id}",
                audio_file_id=cached_mp3_id,
                title=f"🎵 MP3 с {platform}",
            )
        )
    else:
        results.append(
            InlineQueryResultArticle(
                id=f"mp3:{result_id}",
                title=f"🎵 MP3 с {platform}",
                description="Извлечь аудио в формате MP3",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Извлекаю MP3 с <b>{platform}</b>...",
                    parse_mode="HTML",
                ),
                reply_markup=LOADING_KEYBOARD,
            )
        )

    await query.answer(results=results, cache_time=1, is_personal=True)


@router.callback_query(F.data == "inline_loading")
async def inline_loading_callback(callback: CallbackQuery) -> None:
    """Тык по placeholder-кнопке во время загрузки."""
    await callback.answer("⏳ Видео загружается, подождите…")


@router.chosen_inline_result()
async def chosen_inline_handler(chosen: ChosenInlineResult, bot: Bot, db: DatabaseService) -> None:
    """Докачивает медиа и заменяет текстовую заглушку на видео, кружок или MP3."""
    result_id = chosen.result_id or ""

    # Быстрый путь (cached:*, cached_photo:*, cached_mp3:*) — только статистика.
    for prefix in ("cached:", "cached_photo:", "cached_mp3:"):
        if result_id.startswith(prefix):
            url = _extract_url(chosen.query)
            if url:
                await db.record_download(
                    user_id=chosen.from_user.id,
                    platform=downloader.get_platform_name(url),
                    url=url,
                    success=True,
                )
            return

    # Определяем тип операции по префиксу result_id.
    operation: Optional[str] = None
    for prefix in ("download:", "mp3:"):
        if result_id.startswith(prefix):
            operation = prefix.rstrip(":")
            break

    if operation is None:
        return

    inline_message_id = chosen.inline_message_id
    if not inline_message_id:
        logger.warning(
            "chosen_inline_result без inline_message_id — у результата отсутствует reply_markup"
        )
        return

    url = _extract_url(chosen.query)
    if url is None:
        await _safe_edit_text(bot, inline_message_id, INVALID_MESSAGE)
        return

    platform = downloader.get_platform_name(url)
    user_id = chosen.from_user.id

    # Скачиваем видео (общий шаг для всех операций).
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
            f"❌ <b>Не удалось скачать</b>\n\nПричина: {result.error or 'неизвестная ошибка'}",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    if operation == "download":
        if result.is_photo:
            # В inline Telegram не умеет редактировать сообщение в media group,
            # поэтому для карусели отправляется только первый слайд.
            await _handle_photo(
                bot, db, inline_message_id, url, platform, user_id, result.file_path
            )
        else:
            await _handle_video(
                bot, db, inline_message_id, url, platform, user_id, result.file_path
            )

    elif operation == "mp3":
        await _handle_mp3(
            bot, db, inline_message_id, url, platform, user_id, result.file_path, result.title
        )


async def _handle_video(
    bot: Bot,
    db: DatabaseService,
    inline_message_id: str,
    url: str,
    platform: str,
    user_id: int,
    file_path: str,
) -> None:
    """Загружает видео в storage и подменяет inline-заглушку."""
    file_id = await _upload_video_and_get_file_id(bot, file_path)
    if not file_id:
        await _safe_edit_text(
            bot,
            inline_message_id,
            "❌ <b>Не удалось опубликовать</b>\n\n"
            "Inline-storage не настроен или файл не загрузился. "
            "Попросите администратора настроить <code>VIDEO_STORAGE_CHAT_ID</code>.",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    downloader.set_telegram_file_id(url, file_id)

    try:
        await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaVideo(media=file_id, supports_streaming=True),
            reply_markup=None,
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (видео, inline): %s", e, exc_info=True)
        await _safe_edit_text(
            bot, inline_message_id, "❌ <b>Не удалось отправить</b>\n\nПопробуйте позже."
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    await db.record_download(user_id=user_id, platform=platform, url=url, success=True)


async def _handle_photo(
    bot: Bot,
    db: DatabaseService,
    inline_message_id: str,
    url: str,
    platform: str,
    user_id: int,
    file_path: str,
) -> None:
    """Загружает фото в storage и подменяет inline-заглушку."""
    file_id = await _upload_photo_and_get_file_id(bot, file_path)
    if not file_id:
        await _safe_edit_text(
            bot,
            inline_message_id,
            "❌ <b>Не удалось опубликовать</b>\n\n"
            "Inline-storage не настроен или файл не загрузился. "
            "Попросите администратора настроить <code>VIDEO_STORAGE_CHAT_ID</code>.",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    downloader.set_telegram_photo_file_id(url, file_id)

    try:
        await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaPhoto(media=file_id),
            reply_markup=None,
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (фото, inline): %s", e, exc_info=True)
        await _safe_edit_text(
            bot, inline_message_id, "❌ <b>Не удалось отправить</b>\n\nПопробуйте позже."
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    await db.record_download(user_id=user_id, platform=platform, url=url, success=True)
    logger.info("✅ Inline-фото отправлено (user: %s, url: %s)", user_id, url)


async def _handle_mp3(
    bot: Bot,
    db: DatabaseService,
    inline_message_id: str,
    url: str,
    platform: str,
    user_id: int,
    file_path: str,
    title: Optional[str],
) -> None:
    """Конвертирует в MP3, загружает в storage и подменяет inline-заглушку."""
    mp3_path: Optional[str] = None
    try:
        mp3_path = await _convert_to_mp3(file_path)
    except Exception as e:
        logger.error("Ошибка конвертации в MP3 (inline): %s", e, exc_info=True)

    if not mp3_path:
        await _safe_edit_text(
            bot,
            inline_message_id,
            "❌ <b>Не удалось конвертировать в MP3</b>\n\nFFmpeg не найден или ошибка обработки.",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    try:
        file_id = await _upload_audio_and_get_file_id(bot, mp3_path, title=title or "Audio")
    finally:
        try:
            os.remove(mp3_path)
        except Exception:
            pass

    if not file_id:
        await _safe_edit_text(
            bot,
            inline_message_id,
            "❌ <b>Не удалось опубликовать MP3</b>\n\n"
            "Inline-storage не настроен. Попросите администратора настроить <code>VIDEO_STORAGE_CHAT_ID</code>.",
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    downloader.set_telegram_mp3_file_id(url, file_id)

    try:
        await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaAudio(media=file_id),
            reply_markup=None,
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (MP3, inline): %s", e, exc_info=True)
        await _safe_edit_text(
            bot, inline_message_id, "❌ <b>Не удалось отправить MP3</b>\n\nПопробуйте позже."
        )
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    await db.record_download(user_id=user_id, platform=platform, url=url, success=True)
    logger.info("✅ Inline-MP3 отправлен (user: %s, url: %s)", user_id, url)


async def _safe_edit_text(bot: Bot, inline_message_id: str, text: str) -> None:
    try:
        await bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception as e:
        logger.debug("Не удалось отредактировать inline-сообщение: %s", e)


def _resolve_storage_chat_id() -> Optional[int]:
    """Определяет чат для промежуточной публикации файлов ради file_id."""
    if VIDEO_STORAGE_CHAT_ID is not None:
        return VIDEO_STORAGE_CHAT_ID
    if ADMIN_USERS:
        return ADMIN_USERS[0]
    return None


async def _upload_video_and_get_file_id(bot: Bot, file_path: str) -> Optional[str]:
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
            "Не удалось выгрузить видео в storage-чат %s: %s", storage_chat_id, e, exc_info=True
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


async def _upload_photo_and_get_file_id(bot: Bot, file_path: str) -> Optional[str]:
    """
    Заливает фото в storage-чат и возвращает его Telegram file_id (наибольший размер).
    Промежуточное сообщение удаляется (best-effort), file_id остаётся валидным.
    """
    storage_chat_id = _resolve_storage_chat_id()
    if storage_chat_id is None:
        logger.error("Нет VIDEO_STORAGE_CHAT_ID и ADMIN_USERS — inline не может опубликовать фото")
        return None

    try:
        staging = await bot.send_photo(
            chat_id=storage_chat_id,
            photo=FSInputFile(file_path),
            disable_notification=True,
        )
    except Exception as e:
        logger.error(
            "Не удалось выгрузить фото в storage-чат %s: %s", storage_chat_id, e, exc_info=True
        )
        return None

    file_id: Optional[str] = None
    if staging.photo:
        # photo — массив размеров, берём самый большой (последний).
        file_id = staging.photo[-1].file_id
    if not file_id:
        logger.error("Сообщение в storage-чате не содержит photo — file_id не получен")

    try:
        await bot.delete_message(chat_id=storage_chat_id, message_id=staging.message_id)
    except Exception as e:
        logger.debug("Не удалось удалить промежуточное сообщение (фото) из storage: %s", e)

    return file_id


async def _upload_audio_and_get_file_id(
    bot: Bot, file_path: str, title: str = "Audio"
) -> Optional[str]:
    """
    Заливает аудио в storage-чат и возвращает его Telegram file_id.
    Промежуточное сообщение удаляется (best-effort), file_id остаётся валидным.
    """
    storage_chat_id = _resolve_storage_chat_id()
    if storage_chat_id is None:
        logger.error("Нет VIDEO_STORAGE_CHAT_ID и ADMIN_USERS — inline не может опубликовать аудио")
        return None

    try:
        staging = await bot.send_audio(
            chat_id=storage_chat_id,
            audio=FSInputFile(file_path),
            title=title,
            disable_notification=True,
        )
    except Exception as e:
        logger.error(
            "Не удалось выгрузить аудио в storage-чат %s: %s", storage_chat_id, e, exc_info=True
        )
        return None

    file_id = staging.audio.file_id if staging.audio else None
    if not file_id:
        logger.error("Сообщение в storage-чате не содержит audio — file_id не получен")

    try:
        await bot.delete_message(chat_id=storage_chat_id, message_id=staging.message_id)
    except Exception as e:
        logger.debug("Не удалось удалить промежуточное сообщение (аудио) из storage: %s", e)

    return file_id
