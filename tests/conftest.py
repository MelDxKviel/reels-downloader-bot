import pytest_asyncio

from src.services.database import DatabaseService


@pytest_asyncio.fixture
async def db_service():
    """In-memory SQLite database service for tests."""
    service = DatabaseService("sqlite+aiosqlite:///:memory:")
    await service.init_db()
    yield service
    await service.close()
