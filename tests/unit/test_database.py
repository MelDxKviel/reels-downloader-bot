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


# ── bot settings / feature flags ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_setting_returns_none_when_unset(db_service):
    assert await db_service.get_setting("missing") is None


@pytest.mark.asyncio
async def test_set_and_get_setting(db_service):
    await db_service.set_setting("foo", "bar")
    assert await db_service.get_setting("foo") == "bar"


@pytest.mark.asyncio
async def test_set_setting_updates_existing(db_service):
    await db_service.set_setting("foo", "bar")
    await db_service.set_setting("foo", "baz")
    assert await db_service.get_setting("foo") == "baz"


@pytest.mark.asyncio
async def test_feature_flag_default_off(db_service):
    assert await db_service.is_feature_enabled("nope") is False


@pytest.mark.asyncio
async def test_feature_flag_default_overridable(db_service):
    assert await db_service.is_feature_enabled("nope", default=True) is True


@pytest.mark.asyncio
async def test_set_feature_enabled_persists(db_service):
    await db_service.set_feature_enabled("shorts", True)
    assert await db_service.is_feature_enabled("shorts") is True
    await db_service.set_feature_enabled("shorts", False)
    assert await db_service.is_feature_enabled("shorts") is False


@pytest.mark.asyncio
async def test_set_setting_recovers_from_concurrent_insert_race(db_service, monkeypatch):
    """Simulate a concurrent writer inserting the row between our SELECT and
    COMMIT. The UNIQUE constraint on key would normally fail the commit with
    IntegrityError; set_setting must catch it, rollback, and fall through to
    an UPDATE so the toggle still succeeds.
    """
    from src.services.database import BotSetting

    real_session_factory = db_service.async_session
    sneaked = {"done": False}

    def factory_with_sneak():
        sess = real_session_factory()
        real_execute = sess.execute

        async def patched_execute(stmt, *args, **kwargs):
            result = await real_execute(stmt, *args, **kwargs)
            if not sneaked["done"]:
                sneaked["done"] = True
                # Insert the row via another session right before set_setting
                # adds its own row → guaranteed unique-constraint clash at commit.
                async with real_session_factory() as other:
                    other.add(BotSetting(key="raced", value="from_other"))
                    await other.commit()
            return result

        sess.execute = patched_execute
        return sess

    monkeypatch.setattr(db_service, "async_session", factory_with_sneak)
    await db_service.set_setting("raced", "mine")
    monkeypatch.setattr(db_service, "async_session", real_session_factory)

    # The UPDATE-after-rollback path won — our value must be present.
    assert await db_service.get_setting("raced") == "mine"


# ── cache auto-clean settings ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_autoclean_default(db_service):
    assert await db_service.get_cache_autoclean() is False
    assert await db_service.get_cache_autoclean(default=True) is True


@pytest.mark.asyncio
async def test_set_and_get_cache_autoclean(db_service):
    await db_service.set_cache_autoclean(True)
    assert await db_service.get_cache_autoclean() is True
    await db_service.set_cache_autoclean(False)
    # An explicit stored "0" overrides the default.
    assert await db_service.get_cache_autoclean(default=True) is False


@pytest.mark.asyncio
async def test_cache_max_age_default_when_unset(db_service):
    assert await db_service.get_cache_max_age_hours(168) == 168


@pytest.mark.asyncio
async def test_set_and_get_cache_max_age(db_service):
    await db_service.set_cache_max_age_hours(72)
    assert await db_service.get_cache_max_age_hours(168) == 72


@pytest.mark.asyncio
async def test_cache_max_age_falls_back_on_garbage(db_service):
    await db_service.set_setting("cache.autoclean.max_age_hours", "not-a-number")
    assert await db_service.get_cache_max_age_hours(168) == 168


@pytest.mark.asyncio
async def test_cache_max_age_falls_back_on_nonpositive(db_service):
    await db_service.set_cache_max_age_hours(0)
    assert await db_service.get_cache_max_age_hours(168) == 168


@pytest.mark.asyncio
async def test_set_setting_reraises_if_row_vanishes_after_rollback(db_service, monkeypatch):
    """Edge case: IntegrityError fires but the row is gone on re-select.
    Nothing sensible to do — re-raise so the caller sees the failure.
    """
    from sqlalchemy.exc import IntegrityError

    real_session_factory = db_service.async_session

    def factory_with_fake_commit():
        sess = real_session_factory()

        async def fake_commit():
            raise IntegrityError("statement", {}, Exception("UNIQUE"))

        sess.commit = fake_commit
        return sess

    monkeypatch.setattr(db_service, "async_session", factory_with_fake_commit)
    with pytest.raises(IntegrityError):
        await db_service.set_setting("ghost", "value")
