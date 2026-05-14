"""Tests for src/main.py startup logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.main as main_mod

from ._helpers import make_bot, make_db


@pytest.mark.asyncio
async def test_setup_bot_commands_no_admins():
    bot = make_bot()
    db = make_db()
    with patch("src.main.ADMIN_USERS", []):
        await main_mod._setup_bot_commands(bot, db)
    # default commands set + one per supported language
    assert bot.set_my_commands.await_count >= 1


@pytest.mark.asyncio
async def test_setup_bot_commands_with_admin_stored_lang():
    bot = make_bot()
    db = make_db()
    db.get_user_language.return_value = "en"
    with patch("src.main.ADMIN_USERS", [1]):
        await main_mod._setup_bot_commands(bot, db)
    # Default + per-language + per-admin
    assert bot.set_my_commands.await_count >= 3


@pytest.mark.asyncio
async def test_setup_bot_commands_with_admin_no_stored_lang():
    bot = make_bot()
    db = make_db()
    db.get_user_language.return_value = None
    with patch("src.main.ADMIN_USERS", [1]):
        await main_mod._setup_bot_commands(bot, db)


@pytest.mark.asyncio
async def test_setup_bot_commands_admin_setup_exception():
    bot = make_bot()
    db = make_db()
    db.get_user_language.side_effect = RuntimeError("db down")
    with patch("src.main.ADMIN_USERS", [1]):
        await main_mod._setup_bot_commands(bot, db)


@pytest.mark.asyncio
async def test_setup_bot_commands_outer_exception():
    bot = make_bot()
    bot.set_my_commands.side_effect = RuntimeError("api fail")
    db = make_db()
    with patch("src.main.ADMIN_USERS", []):
        await main_mod._setup_bot_commands(bot, db)


@pytest.mark.asyncio
async def test_main_no_bot_token_exits():
    with patch("src.main.BOT_TOKEN", ""):
        with pytest.raises(SystemExit):
            await main_mod.main()


def _build_main_mocks():
    fake_db_instance = MagicMock()
    fake_db_instance.init_db = AsyncMock()
    fake_db_instance.close = AsyncMock()
    fake_db_instance.get_user_language = AsyncMock(return_value=None)

    fake_bot_instance = MagicMock()
    fake_bot_instance.session = MagicMock()
    fake_bot_instance.session.close = AsyncMock()
    fake_bot_instance.set_my_commands = AsyncMock()

    fake_dp_instance = MagicMock()
    fake_dp_instance.start_polling = AsyncMock()
    fake_dp_instance.include_router = MagicMock()

    fake_router = MagicMock()
    for name in ("message", "inline_query", "chosen_inline_result", "callback_query"):
        observer = MagicMock()
        observer.middleware = MagicMock()
        setattr(fake_router, name, observer)
    return fake_db_instance, fake_bot_instance, fake_dp_instance, fake_router


@pytest.mark.asyncio
async def test_main_starts_polling():
    db_i, bot_i, dp_i, router = _build_main_mocks()
    with patch("src.main.BOT_TOKEN", "abc"):
        with patch("src.main.ADMIN_USERS", [1]):
            with patch("src.main.DatabaseService", return_value=db_i):
                with patch("src.main.Bot", return_value=bot_i):
                    with patch("src.main.Dispatcher", return_value=dp_i):
                        with patch("src.main.get_main_router", return_value=router):
                            await main_mod.main()
    dp_i.start_polling.assert_awaited_once()
    bot_i.session.close.assert_awaited()
    db_i.close.assert_awaited()


@pytest.mark.asyncio
async def test_main_no_admin_users_warning():
    """Branch where ADMIN_USERS is empty — should still start polling."""
    db_i, bot_i, dp_i, router = _build_main_mocks()
    with patch("src.main.BOT_TOKEN", "abc"):
        with patch("src.main.ADMIN_USERS", []):
            with patch("src.main.DatabaseService", return_value=db_i):
                with patch("src.main.Bot", return_value=bot_i):
                    with patch("src.main.Dispatcher", return_value=dp_i):
                        with patch("src.main.get_main_router", return_value=router):
                            await main_mod.main()
    dp_i.start_polling.assert_awaited_once()


def test_run_invokes_asyncio_run():
    with patch("src.main.asyncio.run") as mock_run:
        main_mod.run()
    mock_run.assert_called_once()


def test_module_run_as_script():
    """Cover the `if __name__ == '__main__':` entrypoint."""
    import runpy

    with patch("src.main.asyncio.run"):
        runpy.run_module("src.main", run_name="__main__")
