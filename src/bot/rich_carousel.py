"""Helpers for Telegram rich-message slideshows (Bot API 10.2)."""

import html
import logging
import os
from collections.abc import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    InputRichMessage,
    InputRichMessageMedia,
)

from src.bot.telegram_retry import TELEGRAM_UPLOAD_TIMEOUT, retry_transient_telegram
from src.services.downloader import CarouselSlide

logger = logging.getLogger(__name__)

_CAROUSEL_CAPTION_MAX = 1024


def build_slideshow_html(
    slides: Sequence[CarouselSlide],
    caption: str | None = None,
    *,
    media_ids: Sequence[str] | None = None,
) -> str:
    """Build the ``<tg-slideshow>`` HTML for URL or attached media slides."""

    if media_ids is not None and len(media_ids) != len(slides):
        raise ValueError("media_ids must match slides")

    parts = ["<tg-slideshow>"]
    for index, slide in enumerate(slides):
        is_video = bool(getattr(slide, "is_video", False))
        if media_ids is None:
            src = html.escape(slide.url, quote=True)
        else:
            media_kind = "video" if is_video else "photo"
            media_id = html.escape(media_ids[index], quote=True)
            src = f"tg://{media_kind}?id={media_id}"
        tag = "video" if is_video else "img"
        parts.append(f'<{tag} src="{src}"/>')

    if caption:
        text = html.escape(caption.strip()[:_CAROUSEL_CAPTION_MAX])
        if text:
            parts.append(f"<figcaption>{text}</figcaption>")
    parts.append("</tg-slideshow>")
    return "".join(parts)


def _attached_media_rich_message(
    slides: Sequence[CarouselSlide],
    caption: str | None,
    media_values: Sequence[FSInputFile | str] | None,
) -> InputRichMessage | None:
    """Build a rich message backed by uploads or reusable Telegram file IDs."""

    if media_values is None or len(media_values) != len(slides) or not media_values:
        return None

    media_ids = [f"slide_{index + 1}" for index in range(len(slides))]
    attachments: list[InputRichMessageMedia] = []
    for slide, media_value, media_id in zip(slides, media_values, media_ids, strict=True):
        if slide.is_video:
            media = InputMediaVideo(media=media_value, supports_streaming=True)
        else:
            media = InputMediaPhoto(media=media_value)
        attachments.append(InputRichMessageMedia(id=media_id, media=media))

    return InputRichMessage(
        html=build_slideshow_html(slides, caption, media_ids=media_ids),
        media=attachments,
    )


def rich_carousel_variants(
    slides: Sequence[CarouselSlide],
    caption: str | None = None,
    *,
    media_paths: Sequence[str] | None = None,
    media_file_ids: Sequence[str] | None = None,
) -> list[InputRichMessage]:
    """Return upload-first and public-URL fallback variants of a slideshow."""

    if media_paths is not None and media_file_ids is not None:
        raise ValueError("media_paths and media_file_ids are mutually exclusive")

    variants: list[InputRichMessage] = []
    media_values: Sequence[FSInputFile | str] | None = media_file_ids
    if (
        media_paths is not None
        and len(media_paths) == len(slides)
        and not any(slide.is_video for slide in slides)
    ):
        if all(isinstance(path, str) and os.path.isfile(path) for path in media_paths):
            media_values = [FSInputFile(path) for path in media_paths]
    attached_variant = _attached_media_rich_message(slides, caption, media_values)
    if attached_variant is not None:
        variants.append(attached_variant)
    variants.append(InputRichMessage(html=build_slideshow_html(slides, caption)))
    return variants


async def send_rich_carousel(
    bot: Bot,
    chat_id: int,
    slides: Sequence[CarouselSlide],
    caption: str | None = None,
    *,
    media_paths: Sequence[str] | None = None,
) -> bool:
    """Send a rich slideshow, falling back from uploads to public media URLs."""

    for index, rich_message in enumerate(
        rich_carousel_variants(slides, caption, media_paths=media_paths)
    ):
        try:
            await retry_transient_telegram(
                lambda: bot.send_rich_message(
                    chat_id=chat_id,
                    rich_message=rich_message,
                    request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
                ),
                "sendRichMessage(user)",
            )
            return True
        except TelegramAPIError as exc:
            logger.warning("Rich-карусель variant %s не отправлена: %s", index + 1, exc)
        except Exception as exc:  # pragma: no cover - defensive transport fallback
            logger.warning("Rich-карусель variant %s завершилась ошибкой: %s", index + 1, exc)
    return False


async def edit_inline_rich_carousel(
    bot: Bot,
    inline_message_id: str,
    slides: Sequence[CarouselSlide],
    caption: str | None = None,
    *,
    media_file_ids: Sequence[str] | None = None,
) -> bool:
    """Replace an inline placeholder with a rich slideshow.

    Inline edits can't upload multipart files, so callers must pre-upload media
    and pass reusable Telegram ``file_id`` values. Explicit URL media is also
    forbidden by the inline edit contract.
    """

    rich_message = _attached_media_rich_message(slides, caption, media_file_ids)
    if rich_message is None:
        return False
    try:
        await retry_transient_telegram(
            lambda: bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=None,
                rich_message=rich_message,
                reply_markup=None,
                request_timeout=TELEGRAM_UPLOAD_TIMEOUT,
            ),
            "editMessageText(rich carousel, inline)",
        )
        return True
    except TelegramAPIError as exc:
        logger.warning("Inline rich-карусель не отправлена: %s", exc)
    except Exception as exc:  # keep the existing single-media fallback alive
        logger.warning("Inline rich-карусель завершилась ошибкой: %s", exc)
    return False
