"""Tests for URL download handler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest

from src.bot.handlers import download as dl
from src.services.downloader import DownloadResult
from src.services.i18n import Translator

from ._helpers import make_db, make_message, make_status_message


@pytest.mark.asyncio
async def test_handle_url_no_link_hint():
    msg = make_message("just some text")
    db = make_db()
    await dl.handle_url(msg, db, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_invalid_link_hint():
    msg = make_message("send me youtube something")
    db = make_db()
    await dl.handle_url(msg, db, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_success_video(tmp_path):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    sent = MagicMock()
    sent.video = MagicMock()
    sent.video.file_id = "fileid123"
    msg.answer_video.return_value = sent

    result = DownloadResult(
        success=True, file_path=str(video), title="T", duration=10.0, from_cache=False
    )
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(dl.downloader, "set_telegram_file_id") as mock_set:
            await dl.handle_url(msg, db, Translator("en"))

    msg.answer_video.assert_awaited()
    mock_set.assert_called_with("https://youtube.com/watch?v=abc", "fileid123")
    db.record_download.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_failed_download(tmp_path):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()

    result = DownloadResult(success=False, error="Some error")
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        await dl.handle_url(msg, db, Translator("en"))

    sm.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_from_cache(tmp_path):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    sent = MagicMock()
    sent.video = MagicMock()
    sent.video.file_id = "fid"
    msg.answer_video.return_value = sent

    result = DownloadResult(success=True, file_path=str(video), title="T", from_cache=True)
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        await dl.handle_url(msg, db, Translator("en"))

    sm.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_single_photo(tmp_path):
    msg = make_message("https://www.instagram.com/p/ABC/")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")

    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        await dl.handle_url(msg, db, Translator("en"))

    msg.answer_photo.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_photo_carousel(tmp_path):
    msg = make_message("https://www.instagram.com/p/ABC/")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()
    p1 = tmp_path / "p1.jpg"
    p1.write_bytes(b"x")
    p2 = tmp_path / "p2.jpg"
    p2.write_bytes(b"x")

    result = DownloadResult(
        success=True, file_path=str(p1), is_photo=True, photo_paths=[str(p1), str(p2)]
    )
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        await dl.handle_url(msg, db, Translator("en"))

    msg.answer_media_group.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_single_photo_image_process_failed_fallback_to_document(tmp_path):
    msg = make_message("https://www.instagram.com/p/ABC/")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()
    photo = tmp_path / "p.webp"
    photo.write_bytes(b"x")
    msg.answer_photo.side_effect = TelegramBadRequest(
        method=MagicMock(), message="IMAGE_PROCESS_FAILED"
    )

    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        await dl.handle_url(msg, db, Translator("en"))

    msg.answer_document.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_photo_carousel_image_process_failed_fallback(tmp_path):
    msg = make_message("https://www.instagram.com/p/ABC/")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()
    p1 = tmp_path / "p1.webp"
    p1.write_bytes(b"x")
    p2 = tmp_path / "p2.webp"
    p2.write_bytes(b"x")
    msg.answer_media_group.side_effect = [
        TelegramBadRequest(method=MagicMock(), message="IMAGE_PROCESS_FAILED"),
        None,
    ]

    result = DownloadResult(
        success=True, file_path=str(p1), is_photo=True, photo_paths=[str(p1), str(p2)]
    )
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        await dl.handle_url(msg, db, Translator("en"))


@pytest.mark.asyncio
async def test_handle_url_telegram_bad_request_other_reraises(tmp_path):
    msg = make_message("https://www.instagram.com/p/ABC/")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    msg.answer_photo.side_effect = TelegramBadRequest(method=MagicMock(), message="OTHER_ERROR")

    result = DownloadResult(
        success=True, file_path=str(photo), is_photo=True, photo_paths=[str(photo)]
    )
    with patch.object(dl.downloader, "download", AsyncMock(return_value=result)):
        await dl.handle_url(msg, db, Translator("en"))
    # Generic error path triggered
    sm.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_handle_url_unexpected_exception(tmp_path):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    db = make_db()
    with patch.object(dl.downloader, "download", AsyncMock(side_effect=RuntimeError("oops"))):
        await dl.handle_url(msg, db, Translator("en"))
    sm.edit_text.assert_awaited()
    db.record_download.assert_awaited()
