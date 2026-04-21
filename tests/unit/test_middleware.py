"""
Tests for DatabaseMiddleware and UserAccessMiddleware.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery

from src.bot.middlewares.access import DatabaseMiddleware, UserAccessMiddleware


def make_callback(user_id: int):
    """Build a CallbackQuery spec mock so isinstance(x, CallbackQuery) is True."""
    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.from_user.username = "tester"
    cb.from_user.full_name = "Test User"
    cb.answer = AsyncMock()
    return cb


def make_message(user_id: int, username: str = "tester", full_name: str = "Test User"):
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.full_name = full_name
    return msg


@pytest.fixture
def handler():
    return AsyncMock(return_value=None)


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_user = AsyncMock(return_value=None)
    return db


# ── DatabaseMiddleware ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_database_middleware_injects_db(handler, mock_db):
    middleware = DatabaseMiddleware(mock_db)
    event = MagicMock()
    data = {}

    await middleware(handler, event, data)

    assert data["db"] is mock_db
    handler.assert_called_once_with(event, data)


# ── UserAccessMiddleware ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allows_admin_user(handler, mock_db):
    middleware = UserAccessMiddleware(mock_db)

    with patch("src.bot.middlewares.access.ADMIN_USERS", [12345]):
        await middleware(handler, make_message(12345), {})

    handler.assert_called_once()


@pytest.mark.asyncio
async def test_allows_whitelisted_active_user(handler, mock_db):
    active_user = MagicMock(is_active=True)
    mock_db.get_user = AsyncMock(return_value=active_user)
    middleware = UserAccessMiddleware(mock_db)

    with patch("src.bot.middlewares.access.ADMIN_USERS", []):
        await middleware(handler, make_message(99999), {})

    handler.assert_called_once()


@pytest.mark.asyncio
async def test_blocks_unknown_user(handler, mock_db):
    mock_db.get_user = AsyncMock(return_value=None)
    middleware = UserAccessMiddleware(mock_db)

    with patch("src.bot.middlewares.access.ADMIN_USERS", []):
        result = await middleware(handler, make_message(55555), {})

    handler.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_blocks_inactive_user(handler, mock_db):
    inactive_user = MagicMock(is_active=False)
    mock_db.get_user = AsyncMock(return_value=inactive_user)
    middleware = UserAccessMiddleware(mock_db)

    with patch("src.bot.middlewares.access.ADMIN_USERS", []):
        result = await middleware(handler, make_message(77777), {})

    handler.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_blocks_message_without_user(handler, mock_db):
    middleware = UserAccessMiddleware(mock_db)
    event = MagicMock()
    event.from_user = None

    result = await middleware(handler, event, {})

    handler.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_admin_bypasses_db_check(handler, mock_db):
    """Admin user must be allowed without touching the database."""
    middleware = UserAccessMiddleware(mock_db)

    with patch("src.bot.middlewares.access.ADMIN_USERS", [12345]):
        await middleware(handler, make_message(12345), {})

    mock_db.get_user.assert_not_called()


# ── CallbackQuery denial answers the spinner ──────────────────────────────────


@pytest.mark.asyncio
async def test_denied_callback_query_is_answered(handler, mock_db):
    """Denied CallbackQuery must be answered, otherwise clients show a perpetual
    loading spinner until Telegram's ~30s timeout."""
    mock_db.get_user = AsyncMock(return_value=None)
    middleware = UserAccessMiddleware(mock_db)
    cb = make_callback(55555)

    with patch("src.bot.middlewares.access.ADMIN_USERS", []):
        result = await middleware(handler, cb, {})

    handler.assert_not_called()
    assert result is None
    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_query_without_user_is_answered(handler, mock_db):
    middleware = UserAccessMiddleware(mock_db)
    cb = make_callback(0)
    cb.from_user = None

    result = await middleware(handler, cb, {})

    handler.assert_not_called()
    assert result is None
    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_denied_message_does_not_call_answer(handler, mock_db):
    """Message events don't have .answer() spinner semantics — don't call it."""
    mock_db.get_user = AsyncMock(return_value=None)
    middleware = UserAccessMiddleware(mock_db)
    msg = make_message(55555)
    # make_message uses a bare MagicMock; attribute access would auto-create a
    # Mock, so assert it was never touched by checking for answer attribute calls.
    msg.answer = MagicMock()

    with patch("src.bot.middlewares.access.ADMIN_USERS", []):
        await middleware(handler, msg, {})

    msg.answer.assert_not_called()


@pytest.mark.asyncio
async def test_callback_answer_swallows_exception(handler, mock_db):
    """Answering a stale callback must not crash the middleware."""
    mock_db.get_user = AsyncMock(return_value=None)
    middleware = UserAccessMiddleware(mock_db)
    cb = make_callback(55555)
    cb.answer = AsyncMock(side_effect=RuntimeError("query is too old"))

    with patch("src.bot.middlewares.access.ADMIN_USERS", []):
        result = await middleware(handler, cb, {})

    assert result is None
    cb.answer.assert_awaited_once()
