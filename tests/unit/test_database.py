"""
Tests for DatabaseService using an in-memory SQLite database.
"""

from datetime import datetime, timedelta

import pytest

# ── user management ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_new_user(db_service):
    added = await db_service.add_user(1001)
    assert added is True


@pytest.mark.asyncio
async def test_add_user_returns_false_when_already_active(db_service):
    await db_service.add_user(1001)
    added_again = await db_service.add_user(1001)
    assert added_again is False


@pytest.mark.asyncio
async def test_add_user_reactivates_inactive(db_service):
    await db_service.add_user(1001)
    await db_service.remove_user(1001)

    user = await db_service.get_user(1001)
    assert user.is_active is False

    reactivated = await db_service.add_user(1001)
    assert reactivated is True

    user = await db_service.get_user(1001)
    assert user.is_active is True


@pytest.mark.asyncio
async def test_remove_user_deactivates(db_service):
    await db_service.add_user(1001)
    removed = await db_service.remove_user(1001)

    assert removed is True
    user = await db_service.get_user(1001)
    assert user.is_active is False


@pytest.mark.asyncio
async def test_remove_nonexistent_user_returns_false(db_service):
    removed = await db_service.remove_user(9999)
    assert removed is False


@pytest.mark.asyncio
async def test_get_user_returns_none_for_unknown(db_service):
    user = await db_service.get_user(9999)
    assert user is None


@pytest.mark.asyncio
async def test_get_all_users_empty(db_service):
    users = await db_service.get_all_users()
    assert users == []


@pytest.mark.asyncio
async def test_get_all_users_returns_all(db_service):
    await db_service.add_user(101)
    await db_service.add_user(102)
    await db_service.add_user(103)

    users = await db_service.get_all_users()
    user_ids = {u.user_id for u in users}
    assert user_ids == {101, 102, 103}


@pytest.mark.asyncio
async def test_is_user_allowed_active(db_service):
    await db_service.add_user(1001)
    assert await db_service.is_user_allowed(1001) is True


@pytest.mark.asyncio
async def test_is_user_allowed_inactive(db_service):
    await db_service.add_user(1001)
    await db_service.remove_user(1001)
    assert await db_service.is_user_allowed(1001) is False


@pytest.mark.asyncio
async def test_is_user_allowed_nonexistent(db_service):
    assert await db_service.is_user_allowed(9999) is False


# ── statistics ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_download_stores_entry(db_service):
    await db_service.record_download(
        1001, "YouTube", "https://youtube.com/watch?v=abc", success=True
    )
    stats = await db_service.get_user_stats(1001)
    assert stats["total_downloads"] == 1
    assert stats["successful_downloads"] == 1


@pytest.mark.asyncio
async def test_record_download_failed(db_service):
    await db_service.record_download(
        1001, "YouTube", "https://youtube.com/watch?v=abc", success=False
    )
    stats = await db_service.get_user_stats(1001)
    assert stats["total_downloads"] == 1
    assert stats["successful_downloads"] == 0
    assert stats["failed_downloads"] == 1


@pytest.mark.asyncio
async def test_get_user_stats_by_platform(db_service):
    await db_service.record_download(1001, "YouTube", "https://youtube.com/1", success=True)
    await db_service.record_download(1001, "YouTube", "https://youtube.com/2", success=True)
    await db_service.record_download(1001, "TikTok", "https://tiktok.com/1", success=True)

    stats = await db_service.get_user_stats(1001)
    assert stats["by_platform"]["YouTube"] == 2
    assert stats["by_platform"]["TikTok"] == 1


@pytest.mark.asyncio
async def test_get_user_stats_empty(db_service):
    stats = await db_service.get_user_stats(9999)
    assert stats["total_downloads"] == 0
    assert stats["successful_downloads"] == 0
    assert stats["failed_downloads"] == 0
    assert stats["by_platform"] == {}
    assert stats["last_activity"] is None


@pytest.mark.asyncio
async def test_get_global_stats_totals(db_service):
    await db_service.record_download(101, "YouTube", "https://youtube.com/1", success=True)
    await db_service.record_download(102, "Instagram", "https://instagram.com/1", success=True)
    await db_service.record_download(101, "TikTok", "https://tiktok.com/1", success=False)

    stats = await db_service.get_global_stats()
    assert stats["total_downloads"] == 3
    assert stats["successful_downloads"] == 2
    assert stats["failed_downloads"] == 1
    assert stats["active_users"] == 2


@pytest.mark.asyncio
async def test_get_global_stats_by_platform(db_service):
    await db_service.record_download(101, "YouTube", "https://youtube.com/1", success=True)
    await db_service.record_download(101, "YouTube", "https://youtube.com/2", success=True)
    await db_service.record_download(101, "TikTok", "https://tiktok.com/1", success=True)

    stats = await db_service.get_global_stats()
    assert stats["by_platform"]["YouTube"] == 2
    assert stats["by_platform"]["TikTok"] == 1


@pytest.mark.asyncio
async def test_get_global_stats_empty(db_service):
    stats = await db_service.get_global_stats()
    assert stats["total_downloads"] == 0
    assert stats["active_users"] == 0


@pytest.mark.asyncio
async def test_get_global_stats_with_since_filter(db_service):
    await db_service.record_download(101, "YouTube", "https://youtube.com/old", success=True)

    future_since = datetime.utcnow() + timedelta(hours=1)
    stats = await db_service.get_global_stats(since=future_since)
    assert stats["total_downloads"] == 0


# ── language preferences ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_language_unset_returns_none(db_service):
    assert await db_service.get_user_language(1001) is None


@pytest.mark.asyncio
async def test_set_and_get_user_language(db_service):
    success = await db_service.set_user_language(1001, "en")
    assert success is True
    assert await db_service.get_user_language(1001) == "en"


@pytest.mark.asyncio
async def test_set_user_language_updates_existing(db_service):
    await db_service.set_user_language(1001, "ru")
    await db_service.set_user_language(1001, "en")
    assert await db_service.get_user_language(1001) == "en"


@pytest.mark.asyncio
async def test_set_user_language_rejects_unsupported(db_service):
    success = await db_service.set_user_language(1001, "fr")
    assert success is False
    assert await db_service.get_user_language(1001) is None


@pytest.mark.asyncio
async def test_user_language_independent_of_user_table(db_service):
    """Saving a language must not auto-create a row in the access table."""
    await db_service.set_user_language(1001, "en")
    user = await db_service.get_user(1001)
    assert user is None  # not added to whitelist
    assert await db_service.is_user_allowed(1001) is False
