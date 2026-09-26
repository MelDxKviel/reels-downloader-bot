"""
Inline-mode загрузка видео.

Пользователь набирает `@bot_username <ссылка>` в любом чате — бот отвечает
двумя inline-карточками: видео и MP3.
Если результат уже есть в кэше, карточка превращается в готовый файл и
Telegram отправляет его моментально. Иначе карточка отправляется как
текстовая "заглушка", а после выбора (chosen_inline_result) бот скачивает
медиа, конвертирует при необходимости, публикует в storage-чат,
получает оттуда file_id и подменяет текст через editMessageMedia. Instagram-
карусель загружает все фото, затем подменяет заглушку через
editMessageText(rich_message=<tg-slideshow>).

ВАЖНО:
- inline feedback у BotFather (`/setinlinefeedback → 100%`) должен быть включён,
  иначе не приходит chosen_inline_result и «медленный» путь не работает.
- К placeholder-карточке ОБЯЗАТЕЛЬНО прикреплена inline-клавиатура: без неё
  Telegram не присылает `inline_message_id` в chosen_inline_result, и сообщение
  невозможно отредактировать (`Available only if there is an inline keyboard
  attached to the message`).
- Одиночное inline-медиа допускает file_id или публичный URL. Rich-карусель
  строже: при inline-edit она принимает только заранее загруженные file_id
  всех слайдов. Поэтому файлы сначала отправляются в storage-чат
  (`VIDEO_STORAGE_CHAT_ID` или первый `ADMIN_USERS`).
"""

import html
import logging
import os
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

from src.bot.rich_carousel import edit_inline_rich_carousel
from src.bot.telegram_retry import (
    TELEGRAM_UPLOAD_TIMEOUT,
    retry_transient_telegram,
    telegram_duration,
)
from src.config import ADMIN_USERS, VIDEO_STORAGE_CHAT_ID
from src.services.database import DatabaseService
from src.services.downloader import downloader
from src.services.i18n import Translator
from src.services.media import CarouselSlide
from src.services.url_utils import extract_url, get_url_hash
from src.services.youtube_search import (
    build_shorts_url,
    get_cached_video_file_id,
    search_shorts,
)

from .mp3 import _convert_to_mp3

# Имя feature-флага в bot_settings, которым админ включает/выключает поиск
# шортсов в inline-режиме (см. handlers/admin.py: /features).
FEATURE_SHORTS_SEARCH = "youtube_shorts_search"
SHORTS_SEARCH_RESULTS = 10

logger = logging.getLogger(__name__)

router = Router()


def _loading_keyboard(t: Translator) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("inline.loading_button"), callback_data="inline_loading")]
        ]
    )


def _extract_url(text: Optional[str]) -> Optional[str]:
    return extract_url(text)


async def _answer_inline(query: InlineQuery, **kwargs: object) -> None:
    await retry_transient_telegram(
        lambda: query.answer(**kwargs),
        "answerInlineQuery",
    )


@router.inline_query()
async def inline_query_handler(query: InlineQuery, db: DatabaseService, t: Translator) -> None:
    """Формирует inline-результаты по введённому тексту (видео, кружок, MP3)."""
    text = (query.query or "").strip()

    if not text:
        await _answer_inline(
            query,
            results=[
                InlineQueryResultArticle(
                    id="hint",
                    title=t("inline.hint_title"),
                    description=t("inline.hint_description"),
                    input_message_content=InputTextMessageContent(
                        message_text=t("inline.hint_message")
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return

    url = _extract_url(text)
    if url is None:
        # Если введённый текст похож на URL (есть схема ://), но мы его не
        # извлекли — значит, это ссылка на неподдерживаемую платформу. В этом
        # случае оставляем привычное сообщение "ссылка не распознана" и НЕ
        # запускаем поиск шортсов: иначе vimeo/reddit-ссылка стала бы
        # поисковым запросом, что вводит в заблуждение.
        looks_like_url = "://" in text

        shorts_enabled = False
        if not looks_like_url:
            try:
                shorts_enabled = await db.is_feature_enabled(FEATURE_SHORTS_SEARCH)
            except Exception as e:
                logger.warning("Не удалось получить флаг %s: %s", FEATURE_SHORTS_SEARCH, e)

        if shorts_enabled:
            await _answer_shorts_search(query, text, t)
            return

        await _answer_inline(
            query,
            results=[
                InlineQueryResultArticle(
                    id="invalid",
                    title=t("inline.invalid_title"),
                    description=t("inline.invalid_description"),
                    input_message_content=InputTextMessageContent(
                        message_text=t("inline.invalid_message")
                    ),
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
    cached_carousel_slides = downloader.get_cached_carousel_slides(url)
    cached_media_type = downloader.get_cached_media_type(url)
    cached_photo_id = (
        downloader.get_telegram_photo_file_id(url)
        if cached_media_type == "photo" and not cached_carousel_slides
        else None
    )
    cached_video_id = (
        downloader.get_telegram_file_id(url)
        if cached_media_type != "photo" and not cached_carousel_slides
        else None
    )
    if cached_photo_id:
        results.append(
            InlineQueryResultCachedPhoto(
                id=f"cached_photo:{result_id}",
                photo_file_id=cached_photo_id,
                title=t("inline.cached_photo_title", platform=platform),
                description=t("inline.cached_description"),
            )
        )
    elif cached_video_id:
        results.append(
            InlineQueryResultCachedVideo(
                id=f"cached:{result_id}",
                video_file_id=cached_video_id,
                title=t("inline.cached_video_title", platform=platform),
                description=t("inline.cached_description"),
            )
        )
    else:
        results.append(
            InlineQueryResultArticle(
                id=f"download:{result_id}",
                title=t("inline.download_title", platform=platform),
                description=url[:128],
                input_message_content=InputTextMessageContent(
                    message_text=t("inline.download_status", platform=platform),
                    parse_mode="HTML",
                ),
                reply_markup=_loading_keyboard(t),
            )
        )

    # --- MP3 ---
    cached_mp3_id = downloader.get_telegram_mp3_file_id(url)
    if cached_mp3_id:
        results.append(
            InlineQueryResultCachedAudio(
                id=f"cached_mp3:{result_id}",
                audio_file_id=cached_mp3_id,
                title=t("inline.mp3_title", platform=platform),
            )
        )
    else:
        results.append(
            InlineQueryResultArticle(
                id=f"mp3:{result_id}",
                title=t("inline.mp3_title", platform=platform),
                description=t("inline.mp3_description"),
                input_message_content=InputTextMessageContent(
                    message_text=t("inline.mp3_status", platform=platform),
                    parse_mode="HTML",
                ),
                reply_markup=_loading_keyboard(t),
            )
        )

    await _answer_inline(query, results=results, cache_time=1, is_personal=True)


async def _answer_shorts_search(query: InlineQuery, text: str, t: Translator) -> None:
    """Ищет 3 YouTube Shorts по тексту и формирует inline-результаты."""
    try:
        shorts = await search_shorts(text, count=SHORTS_SEARCH_RESULTS, user_id=query.from_user.id)
    except Exception as e:
        logger.warning("Shorts search exception for %r: %s", text, e)
        shorts = []

    if not shorts:
        await _answer_inline(
            query,
            results=[
                InlineQueryResultArticle(
                    id="shorts_empty",
                    title=t("inline.shorts.empty_title"),
                    description=t("inline.shorts.empty_description"),
                    input_message_content=InputTextMessageContent(
                        message_text=t("inline.shorts.empty_message")
                    ),
                )
            ],
            cache_time=10,
            is_personal=True,
        )
        return

    results = []
    for item in shorts:
        description_parts = []
        if item.channel:
            description_parts.append(item.channel)
        if item.duration is not None:
            description_parts.append(_format_duration(item.duration))
        description = " · ".join(description_parts) or t("inline.shorts.default_description")

        cached_file_id = get_cached_video_file_id(item.video_id)
        if cached_file_id:
            results.append(
                InlineQueryResultCachedVideo(
                    id=f"sc:{item.video_id}",
                    video_file_id=cached_file_id,
                    title=item.title,
                    description=description,
                )
            )
            continue

        results.append(
            InlineQueryResultArticle(
                id=f"s:{item.video_id}",
                title=item.title,
                description=description,
                thumbnail_url=item.thumbnail,
                input_message_content=InputTextMessageContent(
                    message_text=t(
                        "inline.shorts.download_status",
                        title=html.escape(item.title),
                    ),
                    parse_mode="HTML",
                ),
                reply_markup=_loading_keyboard(t),
            )
        )

    await _answer_inline(query, results=results, cache_time=30, is_personal=True)


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


@router.callback_query(F.data == "inline_loading")
async def inline_loading_callback(callback: CallbackQuery, t: Translator) -> None:
    """Тык по placeholder-кнопке во время загрузки."""
    await callback.answer(t("inline.loading_callback"))


@router.chosen_inline_result()
async def chosen_inline_handler(
    chosen: ChosenInlineResult, bot: Bot, db: DatabaseService, t: Translator
) -> None:
    """Докачивает медиа и заменяет текстовую заглушку на видео, кружок или MP3."""
    result_id = chosen.result_id or ""

    # Быстрый путь для кэшированного шортса: URL восстанавливается из video_id.
    if result_id.startswith("sc:"):
        video_id = result_id[len("sc:") :]
        if video_id:
            shorts_url = build_shorts_url(video_id)
            await db.record_download(
                user_id=chosen.from_user.id,
                platform=downloader.get_platform_name(shorts_url),
                url=shorts_url,
                success=True,
            )
        return

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
    for prefix in ("download:", "mp3:", "s:"):
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

    # Для шортсов URL не приходит в chosen.query (там пользовательский поисковой
    # запрос). Восстанавливаем URL из video_id, закодированного в result_id.
    if operation == "s":
        video_id = result_id[len("s:") :]
        if not video_id:
            await _safe_edit_text(bot, inline_message_id, t("inline.error.generic"))
            return
        url = build_shorts_url(video_id)
    else:
        url = _extract_url(chosen.query)
        if url is None:
            await _safe_edit_text(bot, inline_message_id, t("inline.invalid_message"))
            return

    platform = downloader.get_platform_name(url)
    user_id = chosen.from_user.id

    cache_media = operation in {"download", "s"}
    try:
        result = await downloader.download(
            url, allow_carousel=cache_media, reserve=cache_media, user_id=user_id
        )
    except Exception as e:
        logger.error("Ошибка скачивания (inline): %s", e, exc_info=True)
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    if not result.success or not result.file_path:
        logger.warning("Inline download failed for %s: %s", url, result.error or result.error_code)
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    try:
        if operation == "download" or operation == "s":
            slides = result.carousel_slides if isinstance(result.carousel_slides, list) else None
            if slides and len(slides) >= 2:
                carousel_cover_ids = None
                if any(slide.is_video and slide.cover_path for slide in slides):
                    carousel_cover_ids = [
                        await _upload_photo_and_get_file_id(bot, slide.cover_path)
                        if slide.is_video and slide.cover_path
                        else None
                        for slide in slides
                    ]
                carousel_file_ids = await _upload_carousel_and_get_file_ids(
                    bot,
                    slides,
                    photo_paths=result.photo_paths,
                    **({"cover_file_ids": carousel_cover_ids} if carousel_cover_ids else {}),
                )
                sent_as_carousel = await edit_inline_rich_carousel(
                    bot,
                    inline_message_id,
                    slides,
                    result.title,
                    media_file_ids=carousel_file_ids,
                    **({"media_cover_file_ids": carousel_cover_ids} if carousel_cover_ids else {}),
                )
                if sent_as_carousel:
                    await db.record_download(
                        user_id=user_id,
                        platform=platform,
                        url=url,
                        success=True,
                    )
                    logger.info(
                        "✅ Inline rich-карусель отправлена (user: %s, url: %s)", user_id, url
                    )
                    return

            if result.is_photo:
                await _handle_photo(
                    bot,
                    db,
                    inline_message_id,
                    url,
                    platform,
                    user_id,
                    result.file_path,
                    t,
                    cache_file_id=not bool(slides and len(slides) >= 2),
                )
            else:
                await _handle_video(
                    bot,
                    db,
                    inline_message_id,
                    url,
                    platform,
                    user_id,
                    result.file_path,
                    t,
                    width=result.width,
                    height=result.height,
                    duration=result.duration,
                    thumbnail_path=result.thumbnail_path,
                    cover_path=result.cover_path,
                    cache_file_id=not bool(slides and len(slides) >= 2),
                )

        elif operation == "mp3":
            await _handle_mp3(
                bot,
                db,
                inline_message_id,
                url,
                platform,
                user_id,
                result.file_path,
                result.title,
                t,
            )
    finally:
        if cache_media:
            downloader.release_result(result)
        else:
            downloader.discard_result_files(result)


async def _handle_video(
    bot: Bot,
    db: DatabaseService,
    inline_message_id: str,
    url: str,
    platform: str,
    user_id: int,
    file_path: str,
    t: Translator,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration: Optional[float] = None,
    cache_file_id: bool = True,
    thumbnail_path: Optional[str] = None,
    cover_path: Optional[str] = None,
) -> None:
    """Загружает видео в storage и подменяет inline-заглушку."""
    # Inline edits cannot attach a local JPEG; upload the cover for a reusable ID.
    cover_id = await _upload_photo_and_get_file_id(bot, cover_path) if cover_path else None
    preview_options = {}
    if thumbnail_path:
        preview_options["thumbnail_path"] = thumbnail_path
    if cover_id:
        preview_options["cover_file_id"] = cover_id
    file_id = await _upload_video_and_get_file_id(
        bot, file_path, width=width, height=height, duration=duration, **preview_options
    )
    if not file_id:
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    if cache_file_id:
        downloader.set_telegram_file_id(url, file_id)

    try:
        await retry_transient_telegram(
            lambda: bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaVideo(
                    media=file_id,
                    supports_streaming=True,
                    width=width,
                    height=height,
                    duration=telegram_duration(duration),
                    cover=cover_id,
                ),
                reply_markup=None,
            ),
            "editMessageMedia(video)",
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (видео, inline): %s", e, exc_info=True)
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
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
    t: Translator,
    *,
    cache_file_id: bool = True,
) -> None:
    """Загружает фото в storage и подменяет inline-заглушку."""
    file_id = await _upload_photo_and_get_file_id(bot, file_path)
    if not file_id:
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    # A temporary rich-message failure must not permanently flatten a carousel
    # into its first photo on the next inline query.  Per-slide file IDs are not
    # cached yet, so only cache the ID for genuine single-photo results.
    if cache_file_id:
        downloader.set_telegram_photo_file_id(url, file_id)

    try:
        await retry_transient_telegram(
            lambda: bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaPhoto(media=file_id),
                reply_markup=None,
            ),
            "editMessageMedia(photo)",
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (фото, inline): %s", e, exc_info=True)
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    await db.record_download(user_id=user_id, platform=platform, url=url, success=True)
    logger.info("✅ Inline-фото отправлено (user: %s, url: %s)", user_id, url)


async def _upload_carousel_photos_and_get_file_ids(
    bot: Bot, photo_paths: list[str]
) -> Optional[list[str]]:
    """Pre-upload every carousel photo for an inline rich-message edit."""

    file_ids: list[str] = []
    for photo_path in photo_paths:
        file_id = await _upload_photo_and_get_file_id(bot, photo_path)
        if not file_id:
            return None
        file_ids.append(file_id)
    return file_ids


async def _upload_carousel_and_get_file_ids(
    bot: Bot,
    slides: list[CarouselSlide],
    *,
    photo_paths: Optional[list[str]] = None,
    cover_file_ids: Optional[list[Optional[str]]] = None,
) -> Optional[list[str]]:
    """Upload ordered photo/video slides before an inline rich-message edit."""
    paths = [slide.local_path for slide in slides]
    all_photos = not any(slide.is_video for slide in slides)
    if all_photos and not all(paths) and photo_paths and len(photo_paths) == len(slides):
        paths = photo_paths
    if not all(isinstance(path, str) and path for path in paths):
        return None
    if all_photos:
        return await _upload_carousel_photos_and_get_file_ids(bot, paths)
    file_ids: list[str] = []
    for index, (slide, path) in enumerate(zip(slides, paths, strict=True)):
        if slide.is_video:
            preview_options = {}
            if slide.thumbnail_path:
                preview_options["thumbnail_path"] = slide.thumbnail_path
            if cover_file_ids and cover_file_ids[index]:
                preview_options["cover_file_id"] = cover_file_ids[index]
            file_id = await _upload_video_and_get_file_id(
                bot,
                path,
                width=slide.width,
                height=slide.height,
                duration=slide.duration,
                **preview_options,
            )
        else:
            file_id = await _upload_photo_and_get_file_id(bot, path)
        if not file_id:
            return None
        file_ids.append(file_id)
    return file_ids


async def _handle_mp3(
    bot: Bot,
    db: DatabaseService,
    inline_message_id: str,
    url: str,
    platform: str,
    user_id: int,
    file_path: str,
    title: Optional[str],
    t: Translator,
) -> None:
    """Конвертирует в MP3, загружает в storage и подменяет inline-заглушку."""
    mp3_path: Optional[str] = None
    try:
        mp3_path = await downloader.run_conversion(user_id, lambda: _convert_to_mp3(file_path))
    except Exception as e:
        logger.error("Ошибка конвертации в MP3 (inline): %s", e, exc_info=True)

    if not mp3_path:
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
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
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    downloader.set_telegram_mp3_file_id(url, file_id)

    try:
        await retry_transient_telegram(
            lambda: bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaAudio(media=file_id),
                reply_markup=None,
            ),
            "editMessageMedia(audio)",
        )
    except Exception as e:
        logger.error("Ошибка при editMessageMedia (MP3, inline): %s", e, exc_info=True)
        await _safe_edit_text(bot, inline_message_id, t("inline.error.failed"))
        await db.record_download(user_id=user_id, platform=platform, url=url, success=False)
        return

    await db.record_download(user_id=user_id, platform=platform, url=url, success=True)
    logger.info("✅ Inline-MP3 отправлен (user: %s, url: %s)", user_id, url)


async def _safe_edit_text(bot: Bot, inline_message_id: str, text: str) -> None:
    try:
        await retry_transient_telegram(
            lambda: bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=None,
            ),
            "editMessageText(inline)",
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


async def _upload_video_and_get_file_id(
    bot: Bot,
    file_path: str,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration: Optional[float] = None,
    thumbnail_path: Optional[str] = None,
    cover_file_id: Optional[str] = None,
) -> Optional[str]:
    """
    Заливает видео в storage-чат и возвращает его Telegram file_id.
    Промежуточное сообщение удаляется (best-effort), file_id остаётся валидным.
    """
    storage_chat_id = _resolve_storage_chat_id()
    if storage_chat_id is None:
        logger.error("Нет VIDEO_STORAGE_CHAT_ID и ADMIN_USERS — inline не может опубликовать видео")
        return None

    try:
        staging = await retry_transient_telegram(
            lambda: bot.send_video(
                chat_id=storage_chat_id,
                video=FSInputFile(file_path),
                duration=telegram_duration(duration),
                width=width,
                height=height,
                thumbnail=FSInputFile(thumbnail_path) if thumbnail_path else None,
                cover=cover_file_id,
                supports_streaming=True,
                disable_notification=True,
                request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
            ),
            "sendVideo(storage)",
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
        staging = await retry_transient_telegram(
            lambda: bot.send_photo(
                chat_id=storage_chat_id,
                photo=FSInputFile(file_path),
                disable_notification=True,
                request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
            ),
            "sendPhoto(storage)",
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
        staging = await retry_transient_telegram(
            lambda: bot.send_audio(
                chat_id=storage_chat_id,
                audio=FSInputFile(file_path),
                title=title,
                disable_notification=True,
                request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
            ),
            "sendAudio(storage)",
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
