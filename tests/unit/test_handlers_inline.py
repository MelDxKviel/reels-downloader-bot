"""Tests for inline-mode handler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import inline as inline_h
from src.services.downloader import DownloadResult
from src.services.i18n import Translator

from ._helpers import make_bot, make_callback, make_db


def make_inline_query(text: str = "", user_id: int = 100):
    q = MagicMock()
    q.query = text
    q.from_user = MagicMock()
    q.from_user.id = user_id
    q.answer = AsyncMock()
    return q


def make_chosen_result(
    result_id: str = "", query: str = "", user_id: int = 100, inline_message_id="im1"
):
    c = MagicMock()
    c.result_id = result_id
    c.query = query
    c.from_user = MagicMock()
    c.from_user.id = user_id
    c.inline_message_id = inline_message_id
    return c


# ── inline_query_handler ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_query_empty_text_returns_hint():
    q = make_inline_query("")
    await inline_h.inline_query_handler(q, Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_invalid_url():
    q = make_inline_query("not a link")
    await inline_h.inline_query_handler(q, Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_url_no_cache():
    q = make_inline_query("https://youtube.com/watch?v=abc")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value=None):
        with patch.object(inline_h.downloader, "get_telegram_file_id", return_value=None):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value=None):
                await inline_h.inline_query_handler(q, Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_url_cached_video():
    q = make_inline_query("https://youtube.com/watch?v=abc")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value="video"):
        with patch.object(inline_h.downloader, "get_telegram_file_id", return_value="vid_fid"):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value=None):
                await inline_h.inline_query_handler(q, Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_url_cached_photo():
    q = make_inline_query("https://www.instagram.com/p/ABC/")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value="photo"):
        with patch.object(inline_h.downloader, "get_telegram_photo_file_id", return_value="ph"):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value=None):
                await inline_h.inline_query_handler(q, Translator("en"))
    q.answer.assert_awaited()


@pytest.mark.asyncio
async def test_inline_query_url_cached_mp3():
    q = make_inline_query("https://youtube.com/watch?v=abc")
    with patch.object(inline_h.downloader, "get_cached_media_type", return_value=None):
        with patch.object(inline_h.downloader, "get_telegram_file_id", return_value=None):
            with patch.object(inline_h.downloader, "get_telegram_mp3_file_id", return_value="m"):
                await inline_h.inline_query_handler(q, Translator("en"))


# ── inline_loading_callback ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_loading_callback_answers():
    cb = make_callback("inline_loading")
    await inline_h.inline_loading_callback(cb, Translator("en"))
    cb.answer.assert_awaited()


# ── chosen_inline_handler ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chosen_inline_cached_video_only_stats():
    cr = make_chosen_result("cached:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_cached_no_url_in_query():
    cr = make_chosen_result("cached:abc", "no url here")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_not_called()


@pytest.mark.asyncio
async def test_chosen_inline_unknown_prefix_returns():
    cr = make_chosen_result("weird:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_not_called()


@pytest.mark.asyncio
async def test_chosen_inline_download_no_inline_message_id():
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x", inline_message_id=None)
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_no_url_in_query():
    cr = make_chosen_result("download:abc", "not a url")
    bot = make_bot()
    db = make_db()
    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_download_download_exception():
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    with patch.object(inline_h.downloader, "download", AsyncMock(side_effect=RuntimeError("oops"))):
        await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_download_failed_result():
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    result = DownloadResult(success=False, error="x")
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    db.record_download.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_download_video_success(tmp_path):
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value="fid")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_download_video_publish_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value=None)):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_video_edit_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    bot.edit_message_media.side_effect = RuntimeError("edit failed")
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_video_and_get_file_id", AsyncMock(return_value="fid")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_photo_success(tmp_path):
    cr = make_chosen_result("download:abc", "https://www.instagram.com/p/ABC/")
    bot = make_bot()
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value="pf")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_download_photo_publish_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://www.instagram.com/p/ABC/")
    bot = make_bot()
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value=None)):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_download_photo_edit_failure(tmp_path):
    cr = make_chosen_result("download:abc", "https://www.instagram.com/p/ABC/")
    bot = make_bot()
    bot.edit_message_media.side_effect = RuntimeError("fail")
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_upload_photo_and_get_file_id", AsyncMock(return_value="pf")):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_success(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video), title="Title")
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value="afid")
            ):
                await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()


@pytest.mark.asyncio
async def test_chosen_inline_mp3_convert_exception(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(
            inline_h, "_convert_to_mp3", AsyncMock(side_effect=RuntimeError("conv fail"))
        ):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_convert_returns_none(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=None)):
            await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_upload_failure(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value=None)
            ):
                await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


@pytest.mark.asyncio
async def test_chosen_inline_mp3_edit_failure(tmp_path):
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    bot.edit_message_media.side_effect = RuntimeError("edit fail")
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value="afid")
            ):
                await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))


# ── _safe_edit_text ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_edit_text_swallows_exception():
    bot = make_bot()
    bot.edit_message_text.side_effect = RuntimeError("nope")
    await inline_h._safe_edit_text(bot, "im1", "hello")


# ── _resolve_storage_chat_id ─────────────────────────────────────────────────


def test_resolve_storage_chat_id_video_storage_set():
    with patch("src.bot.handlers.inline.VIDEO_STORAGE_CHAT_ID", -100):
        assert inline_h._resolve_storage_chat_id() == -100


def test_resolve_storage_chat_id_admin_fallback():
    with patch("src.bot.handlers.inline.VIDEO_STORAGE_CHAT_ID", None):
        with patch("src.bot.handlers.inline.ADMIN_USERS", [42]):
            assert inline_h._resolve_storage_chat_id() == 42


def test_resolve_storage_chat_id_none():
    with patch("src.bot.handlers.inline.VIDEO_STORAGE_CHAT_ID", None):
        with patch("src.bot.handlers.inline.ADMIN_USERS", []):
            assert inline_h._resolve_storage_chat_id() is None


# ── _upload_*_and_get_file_id ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_video_no_storage():
    bot = make_bot()
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=None):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result is None


@pytest.mark.asyncio
async def test_upload_video_send_fails():
    bot = make_bot()
    bot.send_video.side_effect = RuntimeError("fail")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result is None


@pytest.mark.asyncio
async def test_upload_video_success():
    bot = make_bot()
    staging = MagicMock()
    staging.video = MagicMock()
    staging.video.file_id = "fid"
    staging.message_id = 1
    bot.send_video.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result == "fid"


@pytest.mark.asyncio
async def test_upload_video_no_video_in_staging():
    bot = make_bot()
    staging = MagicMock()
    staging.video = None
    staging.message_id = 1
    bot.send_video.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result is None


@pytest.mark.asyncio
async def test_upload_video_delete_message_fails():
    bot = make_bot()
    staging = MagicMock()
    staging.video = MagicMock()
    staging.video.file_id = "fid"
    staging.message_id = 1
    bot.send_video.return_value = staging
    bot.delete_message.side_effect = RuntimeError("can't delete")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_video_and_get_file_id(bot, "/tmp/x.mp4")
    assert result == "fid"


@pytest.mark.asyncio
async def test_upload_photo_no_storage():
    bot = make_bot()
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=None):
        assert await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg") is None


@pytest.mark.asyncio
async def test_upload_photo_send_fails():
    bot = make_bot()
    bot.send_photo.side_effect = RuntimeError("fail")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        assert await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg") is None


@pytest.mark.asyncio
async def test_upload_photo_success():
    bot = make_bot()
    staging = MagicMock()
    size = MagicMock()
    size.file_id = "pf"
    staging.photo = [size]
    staging.message_id = 1
    bot.send_photo.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg")
    assert result == "pf"


@pytest.mark.asyncio
async def test_upload_photo_no_photo_in_staging():
    bot = make_bot()
    staging = MagicMock()
    staging.photo = None
    staging.message_id = 1
    bot.send_photo.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg")
    assert result is None


@pytest.mark.asyncio
async def test_upload_photo_delete_fails():
    bot = make_bot()
    staging = MagicMock()
    size = MagicMock()
    size.file_id = "pf"
    staging.photo = [size]
    staging.message_id = 1
    bot.send_photo.return_value = staging
    bot.delete_message.side_effect = RuntimeError("nope")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_photo_and_get_file_id(bot, "/tmp/x.jpg")
    assert result == "pf"


@pytest.mark.asyncio
async def test_upload_audio_no_storage():
    bot = make_bot()
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=None):
        assert await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3") is None


@pytest.mark.asyncio
async def test_upload_audio_send_fails():
    bot = make_bot()
    bot.send_audio.side_effect = RuntimeError("fail")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        assert await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3") is None


@pytest.mark.asyncio
async def test_upload_audio_success():
    bot = make_bot()
    staging = MagicMock()
    staging.audio = MagicMock()
    staging.audio.file_id = "afid"
    staging.message_id = 1
    bot.send_audio.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3")
    assert result == "afid"


@pytest.mark.asyncio
async def test_upload_audio_no_audio_in_staging():
    bot = make_bot()
    staging = MagicMock()
    staging.audio = None
    staging.message_id = 1
    bot.send_audio.return_value = staging
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3")
    assert result is None


@pytest.mark.asyncio
async def test_upload_audio_delete_fails():
    bot = make_bot()
    staging = MagicMock()
    staging.audio = MagicMock()
    staging.audio.file_id = "afid"
    staging.message_id = 1
    bot.send_audio.return_value = staging
    bot.delete_message.side_effect = RuntimeError("nope")
    with patch("src.bot.handlers.inline._resolve_storage_chat_id", return_value=42):
        result = await inline_h._upload_audio_and_get_file_id(bot, "/tmp/x.mp3")
    assert result == "afid"


@pytest.mark.asyncio
async def test_chosen_inline_mp3_remove_temp_file_fails(tmp_path):
    """Cleanup of temp mp3 file may fail — exception must be swallowed."""
    cr = make_chosen_result("mp3:abc", "https://youtube.com/watch?v=x")
    bot = make_bot()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    mp3 = tmp_path / "out.mp3"
    mp3.write_bytes(b"x")
    from src.services.downloader import DownloadResult

    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(inline_h.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(inline_h, "_convert_to_mp3", AsyncMock(return_value=str(mp3))):
            with patch.object(
                inline_h, "_upload_audio_and_get_file_id", AsyncMock(return_value="afid")
            ):
                with patch("src.bot.handlers.inline.os.remove", side_effect=OSError("nope")):
                    await inline_h.chosen_inline_handler(cr, bot, db, Translator("en"))
    bot.edit_message_media.assert_awaited()
