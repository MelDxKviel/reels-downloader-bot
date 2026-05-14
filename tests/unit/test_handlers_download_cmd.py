"""Tests for /download command handler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import download_cmd as dc
from src.services.downloader import DownloadResult
from src.services.i18n import Translator

from ._helpers import make_callback, make_db, make_message, make_state, make_status_message


@pytest.mark.asyncio
async def test_cmd_download_no_url_enters_fsm():
    msg = make_message("/download")
    state = make_state()
    db = make_db()
    await dc.cmd_download(msg, state, db, Translator("en"))
    state.set_state.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_download_with_url_starts_download(tmp_path):
    msg = make_message("/download https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    sent = MagicMock()
    sent.video = MagicMock()
    sent.video.file_id = "fid"
    msg.answer_video.return_value = sent

    result = DownloadResult(success=True, file_path=str(video), title="T")
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc.cmd_download(msg, state, db, Translator("en"))

    msg.answer_video.assert_awaited()


@pytest.mark.asyncio
async def test_download_and_send_download_exception():
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    with patch.object(dc.downloader, "download", AsyncMock(side_effect=RuntimeError("oops"))):
        await dc._download_and_send(msg, db, sm, "https://youtube.com/watch?v=a", Translator("en"))
    sm.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_download_and_send_failed_result():
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    result = DownloadResult(success=False, error="x")
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc._download_and_send(msg, db, sm, "https://youtube.com/watch?v=a", Translator("en"))
    sm.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_download_and_send_photo_single(tmp_path):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc._download_and_send(msg, db, sm, "https://www.instagram.com/p/x/", Translator("en"))
    msg.answer_photo.assert_awaited()


@pytest.mark.asyncio
async def test_download_and_send_photo_carousel(tmp_path):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    p1 = tmp_path / "p1.jpg"
    p1.write_bytes(b"x")
    p2 = tmp_path / "p2.jpg"
    p2.write_bytes(b"x")
    result = DownloadResult(
        success=True, file_path=str(p1), is_photo=True, photo_paths=[str(p1), str(p2)]
    )
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc._download_and_send(msg, db, sm, "https://www.instagram.com/p/x/", Translator("en"))
    msg.answer_media_group.assert_awaited()


@pytest.mark.asyncio
async def test_download_and_send_video_with_cache(tmp_path):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    sent = MagicMock()
    sent.video = MagicMock()
    sent.video.file_id = "fid"
    msg.answer_video.return_value = sent
    result = DownloadResult(success=True, file_path=str(video), from_cache=True)
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc._download_and_send(msg, db, sm, "https://youtube.com/watch?v=a", Translator("en"))
    sm.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_download_and_send_send_exception(tmp_path):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    msg.answer_video.side_effect = RuntimeError("send failed")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc._download_and_send(msg, db, sm, "https://youtube.com/watch?v=a", Translator("en"))
    sm.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_download_wrong_owner():
    cb = make_callback("cancel_download:1", user_id=2)
    state = make_state()
    await dc.cancel_download(cb, state, Translator("en"))
    state.clear.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_download_success():
    cb = make_callback("cancel_download:1", user_id=1)
    state = make_state()
    await dc.cancel_download(cb, state, Translator("en"))
    state.clear.assert_awaited()
    cb.message.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_download_got_url_no_url():
    msg = make_message("not a url")
    state = make_state()
    db = make_db()
    await dc.download_got_url(msg, state, db, Translator("en"))
    state.clear.assert_not_called()


@pytest.mark.asyncio
async def test_download_got_url_with_url(tmp_path):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state({"prompt_message_id": 5})
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    sent = MagicMock()
    sent.video = MagicMock()
    sent.video.file_id = "fid"
    msg.answer_video.return_value = sent
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc.download_got_url(msg, state, db, Translator("en"))
    state.clear.assert_awaited()
    msg.bot.delete_message.assert_awaited()


@pytest.mark.asyncio
async def test_download_got_url_delete_message_exception(tmp_path):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    msg.bot.delete_message.side_effect = RuntimeError("gone")
    state = make_state({"prompt_message_id": 5})
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    sent = MagicMock()
    sent.video = MagicMock()
    sent.video.file_id = "fid"
    msg.answer_video.return_value = sent
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(dc.downloader, "download", AsyncMock(return_value=result)):
        await dc.download_got_url(msg, state, db, Translator("en"))
