"""Download a bounded list of links through the existing media delivery path."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.bot.handlers.download_cmd import _download_and_send
from src.bot.telegram_retry import retry_transient_telegram
from src.services.database import DatabaseService
from src.services.i18n import Translator
from src.services.url_utils import extract_urls

logger = logging.getLogger(__name__)
router = Router()

MAX_BATCH_URLS = 5


class BatchStates(StatesGroup):
    waiting_for_urls = State()


def _cancel_keyboard(user_id: int, t: Translator) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("common.cancel_button"), callback_data=f"cancel_batch:{user_id}"
                )
            ]
        ]
    )


async def _download_batch(
    message: Message, db: DatabaseService, urls: list[str], t: Translator
) -> None:
    total = len(urls)
    summary = await retry_transient_telegram(
        lambda: message.answer(t("batch.progress", current=1, total=total)),
        "sendMessage(batch summary)",
    )
    failed_positions = []
    for position, url in enumerate(urls, start=1):
        if position > 1:
            try:
                await retry_transient_telegram(
                    lambda: summary.edit_text(t("batch.progress", current=position, total=total)),
                    "editMessageText(batch progress)",
                )
            except Exception:
                logger.warning(
                    "Could not update batch progress at item %s", position, exc_info=True
                )
        try:
            status = await retry_transient_telegram(
                lambda: message.answer(t("common.processing")), "sendMessage(batch item)"
            )
            # Await delivery and release of its cache lease before admitting the next link.
            successful = await _download_and_send(message, db, status, url, t)
        except Exception:
            # Status updates and statistics can fail outside the delivery helper's guards.
            # Catch ordinary failures only: cancellation must still stop the whole batch.
            logger.exception("Batch item %s failed", position)
            successful = False
        if not successful:
            failed_positions.append(str(position))

    failed = len(failed_positions)
    text = t("batch.complete", total=total, successful=total - failed, failed=failed)
    if failed_positions:
        text += "\n" + t("batch.failed_positions", positions=", ".join(failed_positions))
    try:
        await retry_transient_telegram(
            lambda: summary.edit_text(text), "editMessageText(batch complete)"
        )
    except Exception:
        logger.warning("Could not edit batch summary; sending a new message", exc_info=True)
        await retry_transient_telegram(lambda: message.answer(text), "sendMessage(batch complete)")


@router.message(Command("batch"))
async def cmd_batch(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    parts = (message.text or "").split(maxsplit=1)
    urls = extract_urls(parts[1] if len(parts) > 1 else None)
    if urls and len(urls) <= MAX_BATCH_URLS:
        await state.clear()
        await _download_batch(message, db, urls, t)
        return

    prompt_text = (
        t("batch.too_many", limit=MAX_BATCH_URLS)
        if len(urls) > MAX_BATCH_URLS
        else t("batch.prompt", limit=MAX_BATCH_URLS)
    )
    prompt = await message.answer(
        prompt_text, reply_markup=_cancel_keyboard(message.from_user.id, t)
    )
    await state.set_state(BatchStates.waiting_for_urls)
    await state.update_data(prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("cancel_batch:"))
async def cancel_batch(callback: CallbackQuery, state: FSMContext, t: Translator) -> None:
    try:
        owner_id = int(callback.data.split(":", 1)[1])
    except IndexError, ValueError:
        await callback.answer()
        return
    if callback.from_user.id != owner_id:
        await callback.answer(t("common.not_your_operation"), show_alert=True)
        return
    data = await state.get_data()
    if callback.message is None or data.get("prompt_message_id") != callback.message.message_id:
        await callback.answer(t("common.stale_prompt"))
        return
    await state.clear()
    await callback.message.edit_text(t("batch.cancelled"))
    await callback.answer()


@router.message(BatchStates.waiting_for_urls, F.text, ~F.text.startswith("/"))
async def batch_got_urls(
    message: Message, state: FSMContext, db: DatabaseService, t: Translator
) -> None:
    urls = extract_urls(message.text)
    if not urls:
        await message.answer(t("batch.url_not_found"))
        return
    if len(urls) > MAX_BATCH_URLS:
        await message.answer(t("batch.too_many", limit=MAX_BATCH_URLS))
        return

    data = await state.get_data()
    await state.clear()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            logger.debug("Could not remove batch prompt %s", prompt_id, exc_info=True)
    await _download_batch(message, db, urls, t)
