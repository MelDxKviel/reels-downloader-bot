"""Batch delivery uses real queue/cache/statistics with only external I/O replaced."""

import asyncio
import json
import shutil
import subprocess
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramServerError

from src.bot.handlers import batch, download_cmd, inline
from src.bot.middlewares.access import UserAccessMiddleware
from src.services.download_jobs import DownloadJobs
from src.services.downloader import CarouselSlide, DownloadResult, VideoDownloader
from src.services.i18n import Translator

from ._helpers import make_callback, make_db, make_message, make_state, make_status_message

URLS = [
    "https://youtu.be/first",
    "https://tiktok.com/@user/video/2",
    "https://instagram.com/p/third/",
]


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("failure", ["download", "worker", "upload"])
async def test_batch_keeps_order_carousels_quality_and_per_link_statistics(
    tmp_path, monkeypatch, db_service, lang, failure
):
    events = []
    queue = DownloadJobs(workers=1, queued=0, per_user=1)
    monkeypatch.setattr(import_module("src.services.downloader"), "jobs", queue)
    await db_service.set_user_download_quality(100, "1080")

    async def worker(service, url, allow_carousel, *, quality):
        assert allow_carousel is True
        assert quality == "1080"
        assert queue.pending == 1
        assert queue._users[100] == 1
        index = URLS.index(url)
        events.append(f"download:{index}")
        if index == 1:
            if failure == "download":
                return DownloadResult(success=False, error_code="downloader.error.busy")
            if failure == "worker":
                raise RuntimeError("private technical detail")
        video = tmp_path / f"{index}.mp4"
        video.write_bytes(b"video")
        result = DownloadResult(success=True, file_path=str(video), width=720, height=1280)
        if index == 2:
            photo = tmp_path / "last.jpg"
            photo.write_bytes(b"photo")
            result.carousel_slides = [
                CarouselSlide("", is_video=True, local_path=str(video), width=720, height=1280),
                CarouselSlide("", local_path=str(photo)),
            ]
        return result

    service = VideoDownloader(str(tmp_path), worker_runner=worker)
    monkeypatch.setattr(download_cmd, "downloader", service)
    text = "/batch " + "\n".join([URLS[0], URLS[1], URLS[0] + "?si=duplicate", URLS[2]])
    message = make_message(text, chat_id=-100)
    summary = make_status_message()
    statuses = [make_status_message() for _ in URLS]
    message.answer.side_effect = [summary, *statuses]

    async def send_video(**kwargs):
        index = int(Path(kwargs["video"].path).stem)
        events.append(f"send:{index}")
        assert kwargs["chat_id"] == -100
        assert (kwargs["width"], kwargs["height"]) == (720, 1280)
        if index == 1:
            raise RuntimeError("private upload detail")
        return MagicMock(video=MagicMock(file_id="first-file-id"))

    message.bot.send_video.side_effect = send_video

    async def send_carousel(**kwargs):
        events.append("carousel:2")
        assert kwargs["chat_id"] == -100
        rich = kwargs["rich_message"]
        assert '<video src="tg://video?id=slide_1"/>' in rich.html
        assert '<img src="tg://photo?id=slide_2"/>' in rich.html
        assert len(rich.media) == 2

    message.bot.send_rich_message.side_effect = send_carousel
    state = make_state()
    await batch.cmd_batch(message, state, db_service, Translator(lang))

    expected = ["download:0", "send:0", "download:1"]
    if failure == "upload":
        expected.append("send:1")
    assert events == [*expected, "download:2", "carousel:2"]
    stats = await db_service.get_user_stats(100)
    assert (stats["total_downloads"], stats["successful_downloads"], stats["failed_downloads"]) == (
        3,
        2,
        1,
    )
    assert (await db_service.get_user_stats(999))["total_downloads"] == 0
    final = summary.edit_text.await_args.args[0]
    assert Translator(lang)("batch.complete", total=3, successful=2, failed=1) in final
    assert Translator(lang)("batch.failed_positions", positions="2") in final
    assert "private" not in final
    state.clear.assert_awaited_once()
    assert service.get_telegram_file_id(URLS[0], quality="1080") == "first-file-id"
    assert service.get_telegram_file_id(URLS[0]) is None
    assert not service._leases
    assert queue.pending == 0
    assert len(service.get_cached_carousel_slides(URLS[2], quality="1080")) == 2


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("text", ["/batch", "/batch invalid", "/batch https://x.com.evil.test/1"])
async def test_batch_without_supported_links_prompts_with_owned_cancel(lang, text):
    message = make_message(text)
    message.answer.return_value = make_status_message()
    state = make_state()
    await batch.cmd_batch(message, state, make_db(), Translator(lang))
    state.set_state.assert_awaited_once_with(batch.BatchStates.waiting_for_urls)
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == "cancel_batch:100"
    assert "5" in message.answer.await_args.args[0]
    state.update_data.assert_awaited_once_with(prompt_message_id=99)


@pytest.mark.parametrize("waiting", [False, True])
async def test_over_limit_batch_downloads_nothing_and_allows_corrected_input(waiting):
    text = "\n".join(f"https://youtu.be/{index}" for index in range(6))
    message = make_message(text if waiting else f"/batch {text}")
    message.answer.return_value = make_status_message()
    state = make_state({"prompt_message_id": 99})
    with patch.object(download_cmd.downloader, "download", AsyncMock()) as download:
        if waiting:
            await batch.batch_got_urls(message, state, make_db(), Translator("en"))
        else:
            await batch.cmd_batch(message, state, make_db(), Translator("en"))
    download.assert_not_awaited()
    state.clear.assert_not_awaited()
    assert "5" in message.answer.await_args.args[0]
    if not waiting:
        state.set_state.assert_awaited_once_with(batch.BatchStates.waiting_for_urls)


async def test_waiting_for_batch_ignores_bad_input_and_keeps_cancel_prompt():
    state = make_state({"prompt_message_id": 99})
    message = make_message("https://example.com/file")
    await batch.batch_got_urls(message, state, make_db(), Translator("en"))
    state.clear.assert_not_awaited()
    message.answer.assert_awaited_once_with(Translator("en")("batch.url_not_found"))


async def test_waiting_batch_counts_unique_links_and_removes_prompt(monkeypatch):
    message = make_message(" ".join([URLS[0], URLS[0] + "?si=x"] * 3))
    summary = make_status_message()
    status = make_status_message()
    message.answer.side_effect = [summary, status]
    state = make_state({"prompt_message_id": 77})
    db = make_db()
    monkeypatch.setattr(
        download_cmd.downloader,
        "download",
        AsyncMock(return_value=DownloadResult(success=False, error_code="downloader.error.busy")),
    )
    # Failure to remove an old prompt must not discard a valid batch.
    message.bot.delete_message.side_effect = RuntimeError("already deleted")
    await batch.batch_got_urls(message, state, db, Translator("en"))
    state.clear.assert_awaited_once()
    message.bot.delete_message.assert_awaited_once_with(100, 77)
    db.record_download.assert_awaited_once_with(
        user_id=100, platform="YouTube", url=URLS[0], success=False
    )
    assert (
        Translator("en")("batch.complete", total=1, successful=0, failed=1)
        in (summary.edit_text.await_args.args[0])
    )


@pytest.mark.parametrize(
    "data,owner,prompt_id,has_message,expected_key",
    [
        ("cancel_batch:100", 200, 99, True, "common.not_your_operation"),
        ("cancel_batch:100", 100, 98, True, "common.stale_prompt"),
        ("cancel_batch:100", 100, 99, False, "common.stale_prompt"),
        ("cancel_batch:oops", 100, 99, True, None),
        ("cancel_batch:", 100, 99, True, None),
    ],
)
async def test_cancel_rejects_other_users_stale_and_malformed_buttons(
    data, owner, prompt_id, has_message, expected_key
):
    callback = make_callback(data, user_id=owner)
    callback.message = make_status_message() if has_message else None
    state = make_state({"prompt_message_id": prompt_id})
    await batch.cancel_batch(callback, state, Translator("en"))
    state.clear.assert_not_awaited()
    callback.answer.assert_awaited_once()
    if expected_key:
        assert callback.answer.await_args.args[0] == Translator("en")(expected_key)


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_cancel_current_batch_clears_state_and_acknowledges_owner(lang):
    callback = make_callback("cancel_batch:100")
    callback.message = make_status_message()
    state = make_state({"prompt_message_id": 99})
    await batch.cancel_batch(callback, state, Translator(lang))
    state.clear.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once_with(Translator(lang)("batch.cancelled"))
    callback.answer.assert_awaited_once()


async def test_batch_cancellation_releases_current_lease_without_starting_next(
    tmp_path, monkeypatch
):
    service = VideoDownloader(str(tmp_path), worker_runner=AsyncMock())
    paths = [tmp_path / f"{index}.mp4" for index in range(2)]
    for url, path in zip(URLS, paths):
        path.write_bytes(b"cached")
        service.add_to_cache(url, DownloadResult(success=True, file_path=str(path)))
    monkeypatch.setattr(download_cmd, "downloader", service)
    message = make_message("/batch " + " ".join(URLS[:2]))
    message.answer.return_value = make_status_message()

    async def interrupted_upload(**kwargs):
        service.clear_cache()
        assert paths[0].exists(), "The current upload owns a cache lease"
        raise asyncio.CancelledError

    message.bot.send_video.side_effect = interrupted_upload
    with pytest.raises(asyncio.CancelledError):
        await batch.cmd_batch(message, make_state(), make_db(), Translator("en"))
    assert message.bot.send_video.await_count == 1
    service._worker_runner.assert_not_awaited()
    assert not service._leases
    assert not any(path.exists() for path in paths)


async def test_batch_retries_transient_summary_error(monkeypatch):
    message = make_message("/batch " + " ".join(URLS[:2]))
    summary = make_status_message()
    summary.edit_text.side_effect = [
        TelegramServerError(method=MagicMock(), message="Gateway Timeout (504)"),
        None,
        None,
    ]
    message.answer.side_effect = [summary, make_status_message(), make_status_message()]
    monkeypatch.setattr(
        download_cmd.downloader, "download", AsyncMock(return_value=DownloadResult(success=False))
    )
    with patch("src.bot.telegram_retry.asyncio.sleep", AsyncMock()) as sleep:
        await batch.cmd_batch(message, make_state(), make_db(), Translator("en"))
    sleep.assert_awaited_once_with(1.0)
    assert "2" in summary.edit_text.await_args.args[0]


async def test_batch_does_not_bypass_whitelist(db_service, monkeypatch):
    message = make_message("/batch " + " ".join(URLS))
    monkeypatch.setattr("src.bot.middlewares.access.ADMIN_USERS", [999])

    async def dispatch(event, data):
        await batch.cmd_batch(event, make_state(), db_service, Translator("en"))

    await UserAccessMiddleware(db_service)(dispatch, message, {})
    message.answer.assert_not_awaited()
    assert (await db_service.get_user_stats(100))["total_downloads"] == 0


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_cached_batch_delivers_real_media_previews_and_reusable_inline_video(
    tmp_path, monkeypatch, lang
):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for real batch media checks")
    from PIL import Image

    video = tmp_path / "portrait.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x240:r=25",
            "-t",
            "0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (120, 180), "red").save(photo)
    result = DownloadResult(success=True, file_path=str(video), width=160, height=240, duration=0.2)
    worker = AsyncMock()
    service = VideoDownloader(str(tmp_path), worker_runner=worker)
    service._prepare_video_previews(result)
    assert result.thumbnail_path and result.cover_path
    for url, item in (
        (URLS[0], result),
        (URLS[2], DownloadResult(success=True, file_path=str(photo), is_photo=True)),
    ):
        service.add_to_cache(url, item, quality="720")
    monkeypatch.setattr(download_cmd, "downloader", service)
    monkeypatch.setattr(inline, "downloader", service)
    message = make_message(f"/batch {URLS[0]} {URLS[2]}")
    summary = make_status_message()
    message.answer.side_effect = [summary, make_status_message(), make_status_message()]
    deliveries = []

    async def send_video(**kwargs):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", kwargs["video"].path],
            check=True,
            capture_output=True,
            timeout=15,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        assert (stream["codec_name"], stream["width"], stream["height"]) == ("h264", 160, 240)
        for field in ("thumbnail", "cover"):
            with Image.open(kwargs[field].path) as image:
                assert image.format == "JPEG"
                assert image.width * 3 == image.height * 2
        deliveries.append("video")
        return MagicMock(video=MagicMock(file_id="batch-real-video-id"))

    async def send_photo(**kwargs):
        with Image.open(kwargs["photo"].path) as image:
            assert image.size == (120, 180)
        deliveries.append("photo")

    message.bot.send_video.side_effect = send_video
    message.bot.send_photo.side_effect = send_photo
    db = make_db()
    db.get_user_download_quality.return_value = "720"
    await batch.cmd_batch(message, make_state(), db, Translator(lang))
    assert deliveries == ["video", "photo"]
    assert summary.edit_text.await_args.args[0] == Translator(lang)(
        "batch.complete", total=2, successful=2, failed=0
    )
    assert all(call.kwargs["success"] for call in db.record_download.await_args_list)
    assert not service._leases
    assert all(
        Path(path).exists() for path in (video, photo, result.thumbnail_path, result.cover_path)
    )
    worker.assert_not_awaited()

    query = MagicMock(query=URLS[0], answer=AsyncMock())
    query.from_user.id = 100
    await inline.inline_query_handler(query, db, Translator(lang))
    cached = query.answer.await_args.kwargs["results"][0]
    assert cached.video_file_id == "batch-real-video-id"
    assert query.answer.await_args.kwargs["is_personal"] is True
