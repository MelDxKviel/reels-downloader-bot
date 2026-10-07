"""Delivery keeps file ownership and every slide across Telegram fallbacks."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers import download, download_cmd
from src.services.downloader import CarouselSlide, DownloadResult, VideoDownloader
from src.services.i18n import Translator

from ._helpers import make_db, make_message, make_status_message


@pytest.mark.asyncio
async def test_mixed_fallback_keeps_order_and_every_slide(tmp_path):
    message = make_message()
    message.message_thread_id = 42
    slides = []
    for index in range(11):
        video = index % 2 == 0
        path = tmp_path / f"{index}.{'mp4' if video else 'jpg'}"
        path.write_bytes(b"media")
        slides.append(CarouselSlide("", is_video=video, local_path=str(path)))
    assert await download._send_carousel_files(message, slides)
    group = message.bot.send_media_group.await_args.kwargs
    assert [item.media.path for item in group["media"]] == [s.local_path for s in slides[:10]]
    assert [item.type for item in group["media"]] == [
        "video" if s.is_video else "photo" for s in slides[:10]
    ]
    assert group["message_thread_id"] == 42
    assert message.answer_video.await_args.kwargs["video"].path == slides[-1].local_path


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [False, True])
@pytest.mark.parametrize("fail_upload", [False, True])
async def test_media_lease_survives_clear_during_upload_and_is_released(
    tmp_path, monkeypatch, command, fail_upload
):
    service = VideoDownloader(str(tmp_path))
    path = tmp_path / "video.mp4"
    path.write_bytes(b"media")
    url = "https://youtube.com/watch?v=example"
    service.add_to_cache(url, DownloadResult(success=True, file_path=str(path)))
    lease = service.get_from_cache(url, reserve=True)
    service.download = AsyncMock(return_value=lease)
    module = download_cmd if command else download
    monkeypatch.setattr(module, "downloader", service)
    message = make_message(url)
    status = make_status_message()
    message.answer.return_value = status

    async def send(**kwargs):
        service.clear_cache()
        assert path.exists()
        if fail_upload:
            raise RuntimeError("upload failed")
        return MagicMock(video=MagicMock(file_id="telegram-id"))

    message.bot.send_video = AsyncMock(side_effect=send)
    if command:
        await module._download_and_send(message, make_db(), status, url, Translator("en"))
    else:
        await module.handle_url(message, make_db(), Translator("en"))
    assert not service._leases
    assert not path.exists()
    service.download.assert_awaited_once_with(
        url, user_id=message.from_user.id, reserve=True, quality="standard"
    )
