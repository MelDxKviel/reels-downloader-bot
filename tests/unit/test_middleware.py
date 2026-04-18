"""
Tests for DatabaseMiddleware and UserAccessMiddleware.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.middlewares.access import DatabaseMiddleware, UserAccessMiddleware


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
