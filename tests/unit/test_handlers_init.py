"""Integration checks for command routing through the real aiogram dispatcher."""

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Chat, Message, Update, User

from src.bot.handlers import admin, download_cmd, get_main_router, gif, mp3, voice
from src.bot.handlers import round as round_handler
from src.services.i18n import Translator

from ._helpers import make_db, make_status_message


@pytest.mark.asyncio
async def test_main_router_routes_new_commands_outside_active_fsm():
    # Module-level routers can be attached only once. Exercise the entire routing
    # matrix in this dispatcher instead of calling handlers directly.
    router = get_main_router()
    assert isinstance(router, Router)
    assert len(router.sub_routers) >= 10
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    state = dispatcher.fsm.get_context(bot=bot, chat_id=100, user_id=100)
    db = make_db()
    t = Translator("en")
    commands = {
        "download": (
            download_cmd,
            "_download_and_send",
            download_cmd.DownloadStates.waiting_for_url,
        ),
        "mp3": (mp3, "_download_and_send_mp3", mp3.Mp3States.waiting_for_input),
        "voice": (voice, "_download_and_send_voice", voice.VoiceStates.waiting_for_input),
        "gif": (gif, "_download_and_send_gif", gif.GifStates.waiting_for_input),
        "round": (
            round_handler,
            "_download_and_send_round",
            round_handler.RoundStates.waiting_for_input,
        ),
    }

    async def send(text):
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=100, type="private"),
            from_user=User(id=100, is_bot=False, first_name="Test"),
            text=text,
        )
        await dispatcher.feed_update(bot, Update(update_id=1, message=message), db=db, t=t)

    try:
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(Message, "answer", AsyncMock(return_value=make_status_message()))
            )
            stack.enter_context(patch.object(admin, "is_admin", return_value=True))
            calls = {
                command: stack.enter_context(patch.object(module, helper, AsyncMock()))
                for command, (module, helper, _) in commands.items()
            }
            for previous in (*commands, "adduser"):
                for destination, (_, _, expected_state) in commands.items():
                    for with_url in (False, True):
                        await state.clear()
                        for call in calls.values():
                            call.reset_mock()
                        await send(f"/{previous}")
                        url = " https://youtube.com/watch?v=abc" if with_url else ""
                        await send(f"/{destination}{url}")
                        scenario = (previous, destination, with_url)
                        if with_url:
                            assert await state.get_state() is None, scenario
                            assert calls[destination].await_count == 1, scenario
                        else:
                            assert await state.get_state() == expected_state.state, scenario
                        for command, call in calls.items():
                            if command != destination or not with_url:
                                assert call.await_count == 0, scenario
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
