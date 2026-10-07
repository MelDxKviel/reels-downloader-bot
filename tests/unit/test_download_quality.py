"""Persistent HD grants, real yt-dlp selection and cross-profile isolation."""

import asyncio
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yt_dlp

from src.bot.handlers import admin, download, download_cmd, inline
from src.config import MAX_FILE_SIZE
from src.services import download_worker
from src.services.database import DatabaseService, User
from src.services.download_jobs import DownloadJobs
from src.services.downloader import CarouselSlide, DownloadResult, VideoDownloader
from src.services.i18n import Translator

from ._helpers import (
    make_bot,
    make_db,
    make_message,
    make_state,
    make_status_message,
)

URL = "https://www.youtube.com/watch?v=quality"


def make_inline_query(text):
    return MagicMock(query=text, from_user=MagicMock(id=100), answer=AsyncMock())


def make_chosen_inline(result_id, *, query):
    return MagicMock(
        result_id=result_id, query=query, from_user=MagicMock(id=100), inline_message_id="inline1"
    )


async def test_quality_survives_restart_and_upgrades_existing_schema(tmp_path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'quality.db').as_posix()}"
    db = DatabaseService(database_url)
    # Simulate an existing installation: create_all must add the new table.
    async with db.engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
    await db.init_db()
    await db.add_user(10)
    await db.set_user_language(10, "en")
    assert await db.get_user_download_quality(10) == "standard"
    await db.set_user_download_quality(10, "1080")
    # Admins need no whitelist record to receive quality settings.
    await db.set_user_download_quality(20, "720")
    assert await db.get_user(20) is None
    await db.close()

    db = DatabaseService(database_url)
    try:
        await db.init_db()
        assert await db.get_user_download_quality(10) == "1080"
        assert await db.get_user_download_quality(20) == "720"
        assert await db.get_user_language(10) == "en"
        assert await db.is_user_allowed(10)
        await db.set_user_download_quality(10, "standard")
        assert await db.get_user_download_quality(10) == "standard"
        assert await db.get_user_download_quality(20) == "720"
        with pytest.raises(ValueError):
            await db.set_user_download_quality(10, "2160")
        assert await db.get_user_download_quality(10) == "standard"
    finally:
        await db.close()


@pytest.mark.parametrize(
    "argument,quality", [("720", "720"), ("1080", "1080"), ("off", "standard")]
)
async def test_admin_assigns_and_revokes_hd(db_service, argument, quality):
    message = make_message(f"/hd 123 {argument}", user_id=1)
    with patch.object(admin, "ADMIN_USERS", [1]):
        await admin.cmd_hd(message, db_service, Translator("en"), make_state())
    assert await db_service.get_user_download_quality(123) == quality
    assert await db_service.get_user(123) is None
    assert "Saved" in message.answer.await_args.args[0]


async def test_non_admin_cannot_assign_hd():
    db = make_db()
    with patch.object(admin, "ADMIN_USERS", [1]):
        await admin.cmd_hd(
            make_message("/hd 123 1080", user_id=2), db, Translator("en"), make_state()
        )
    db.set_user_download_quality.assert_not_awaited()
    db.get_user_download_quality.assert_not_awaited()


@pytest.mark.parametrize(
    "args",
    ["", "abc 1080", "-1 1080", "0 720", "123 2160", "123 1080 extra", "9223372036854775808 720"],
)
async def test_invalid_hd_command_does_not_write(args):
    db = make_db()
    with patch.object(admin, "ADMIN_USERS", [1]):
        await admin.cmd_hd(
            make_message(f"/hd {args}", user_id=1), db, Translator("en"), make_state()
        )
    db.set_user_download_quality.assert_not_awaited()


async def test_hd_status_is_read_only(db_service):
    await db_service.set_user_download_quality(123, "720")
    message = make_message("/hd 123", user_id=1)
    with patch.object(admin, "ADMIN_USERS", [1]):
        await admin.cmd_hd(message, db_service, Translator("en"), make_state())
    assert "720p" in message.answer.await_args.args[0]
    assert await db_service.get_user_download_quality(123) == "720"


def _video(resolution, *, portrait=False, audio=True, **overrides):
    width, height = resolution * 16 // 9, resolution
    if portrait:
        width, height = height, width
    return {
        "format_id": str(resolution),
        "url": f"https://cdn.invalid/{resolution}.mp4",
        "ext": "mp4",
        "vcodec": "avc1.640028",
        "acodec": "mp4a.40.2" if audio else "none",
        "width": width,
        "height": height,
        "filesize": 1024,
        **overrides,
    }


def _select(service, formats, quality):
    opts = service._get_ydl_opts(
        "unused.%(ext)s", "https://instagram.com/reel/example/", quality=quality
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.process_ie_result(
            {"id": "example", "title": "Example", "formats": formats}, download=False
        )


@pytest.mark.parametrize("ffmpeg", [True, False])
@pytest.mark.parametrize("portrait", [True, False])
@pytest.mark.parametrize(
    "quality,expected", [("standard", "480"), ("720", "720"), ("1080", "1080")]
)
def test_real_format_selection_respects_profile_and_orientation(
    tmp_path, ffmpeg, portrait, quality, expected
):
    service = VideoDownloader(str(tmp_path))
    service.has_ffmpeg = ffmpeg
    formats = [_video(res, portrait=portrait) for res in [360, 480, 720, 1080, 2160]]
    assert _select(service, formats, quality)["format_id"] == expected


@pytest.mark.parametrize("ffmpeg", [True, False])
def test_instagram_silent_video_keeps_high_resolution(tmp_path, ffmpeg):
    service = VideoDownloader(str(tmp_path))
    service.has_ffmpeg = ffmpeg
    formats = [_video(res, portrait=True, audio=False) for res in [480, 720, 1080]]
    assert _select(service, formats, "1080")["format_id"] == "1080"


def test_format_selection_uses_lower_resolution_for_known_oversized_video(tmp_path):
    service = VideoDownloader(str(tmp_path))
    service.has_ffmpeg = True
    formats = [_video(480), _video(720), _video(1080, filesize=MAX_FILE_SIZE + 1)]
    assert _select(service, formats, "1080")["format_id"] == "720"


def test_resolution_beats_extractor_preference_and_uses_smallest_when_needed(tmp_path):
    service = VideoDownloader(str(tmp_path))
    service.has_ffmpeg = True
    formats = [_video(720, quality=100), _video(1080, quality=0), _video(2160, quality=200)]
    assert _select(service, formats, "1080")["format_id"] == "1080"
    assert _select(service, formats, "standard")["format_id"] == "720"


def test_hd_merges_high_resolution_video_and_audio_instead_of_low_progressive(tmp_path):
    service = VideoDownloader(str(tmp_path))
    service.has_ffmpeg = True
    formats = [
        _video(360),
        _video(720, audio=False),
        _video(1080, audio=False),
        {
            "format_id": "audio",
            "url": "https://cdn.invalid/audio.m4a",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "abr": 128,
        },
    ]
    selected = _select(service, formats, "1080")
    assert [item["format_id"] for item in selected["requested_formats"]] == ["1080", "audio"]


def test_carousel_url_fallback_retains_selected_profile():
    formats = [_video(480), _video(720), _video(1080)]
    entry = {**formats[0], "formats": formats}
    assert VideoDownloader._entry_to_slide(entry).url == formats[0]["url"]
    entry.pop("url")
    entry["requested_formats"] = [_video(480, audio=False)]
    assert VideoDownloader._entry_to_slide(entry).url == formats[0]["url"]


async def test_profiles_have_separate_workers_cache_ids_and_delivery_leases(tmp_path, monkeypatch):
    calls = []
    ready = asyncio.Event()

    async def runner(service, url, allow_carousel, *, quality):
        calls.append(quality)
        if len(calls) == 3:
            ready.set()
        await ready.wait()
        path = tmp_path / f"{quality}.mp4"
        path.write_bytes(quality.encode())
        return DownloadResult(success=True, file_path=str(path))

    monkeypatch.setattr(import_module("src.services.downloader"), "jobs", DownloadJobs())
    service = VideoDownloader(str(tmp_path), worker_runner=runner)
    async with asyncio.timeout(5):
        results = await asyncio.gather(
            *[
                service.download(URL, user_id=i, reserve=True, quality=quality)
                for i, quality in enumerate(["standard", "720", "1080", "1080"])
            ]
        )
    assert sorted(calls) == ["1080", "720", "standard"]
    assert results[2].file_path == results[3].file_path
    assert results[0].file_path != results[1].file_path != results[2].file_path
    for quality in ["standard", "720", "1080"]:
        service.set_telegram_file_id(URL, f"id-{quality}", quality=quality)
    restarted = VideoDownloader(str(tmp_path))
    for quality in ["standard", "720", "1080"]:
        assert restarted.get_from_cache(URL, quality=quality).from_cache
        assert restarted.get_telegram_file_id(URL, quality=quality) == f"id-{quality}"
    service.clear_cache()
    assert all((tmp_path / f"{quality}.mp4").exists() for quality in calls)
    for result in results:
        service.release_result(result)
    assert not list(tmp_path.glob("*.mp4"))


def test_carousel_and_photo_ids_are_isolated_by_profile(tmp_path):
    service = VideoDownloader(str(tmp_path))
    for quality in ["standard", "720", "1080"]:
        paths = [tmp_path / f"{quality}-{i}.jpg" for i in range(2)]
        for path in paths:
            path.write_bytes(b"photo")
        service.add_to_cache(
            URL,
            DownloadResult(
                success=True,
                file_path=str(paths[0]),
                is_photo=True,
                photo_paths=[str(path) for path in paths],
                carousel_slides=[CarouselSlide("", local_path=str(path)) for path in paths],
            ),
            quality=quality,
        )
        service.set_telegram_photo_file_id(URL, f"photo-{quality}", quality=quality)
    for quality in ["standard", "720", "1080"]:
        assert service.get_cached_media_type(URL, quality=quality) == "photo"
        assert service.get_telegram_photo_file_id(URL, quality=quality) == f"photo-{quality}"
        slides = service.get_cached_carousel_slides(URL, quality=quality)
        assert len(slides) == 2
        assert quality in slides[0].local_path


async def test_worker_receives_quality_in_its_request(tmp_path, monkeypatch):
    service = VideoDownloader(str(tmp_path))
    requests = []

    async def worker(request, directory):
        requests.append(request)
        return {"success": False}

    monkeypatch.setattr(download_worker, "run_worker", worker)
    await download_worker.run_download_worker(service, URL, True, quality="1080")
    assert requests[0]["quality"] == "1080"


@pytest.mark.parametrize(
    "url", [URL, "https://instagram.com/reel/example/", "https://tiktok.com/@user/video/123"]
)
async def test_source_options_receive_quality(tmp_path, url):
    service = VideoDownloader(str(tmp_path))
    with patch.object(
        service, "_download_sync", return_value=DownloadResult(success=False)
    ) as backend:
        await service._download_source(url, quality="720")
    assert backend.call_args.args[1]["format_sort"][0] == "res:720"


@pytest.mark.parametrize("handler", ["link", "command"])
async def test_regular_delivery_uses_granted_quality(tmp_path, handler):
    db = make_db()
    db.get_user_download_quality.return_value = "1080"
    message = make_message(URL)
    status = make_status_message()
    message.answer.return_value = status
    module = download if handler == "link" else download_cmd
    result = DownloadResult(success=False, error_code="downloader.error.busy")
    with patch.object(module.downloader, "download", AsyncMock(return_value=result)) as call:
        if handler == "link":
            await module.handle_url(message, db, Translator("en"))
        else:
            await module._download_and_send(message, db, status, URL, Translator("en"))
    call.assert_awaited_once_with(URL, user_id=100, reserve=True, quality="1080")


async def test_inline_query_reads_only_granted_quality_cache(tmp_path, monkeypatch):
    service = VideoDownloader(str(tmp_path))
    service.set_telegram_file_id(URL, "standard-id")
    service.set_telegram_file_id(URL, "hd-id", quality="1080")
    monkeypatch.setattr(inline, "downloader", service)
    db = make_db()
    for quality, expected in [("1080", "hd-id"), ("standard", "standard-id")]:
        db.get_user_download_quality.return_value = quality
        query = make_inline_query(URL)
        await inline.inline_query_handler(query, db, Translator("en"))
        result = query.answer.await_args.kwargs["results"][0]
        assert result.video_file_id == expected
        assert query.answer.await_args.kwargs["is_personal"]


@pytest.mark.parametrize("operation,quality", [("download", "1080"), ("mp3", "standard")])
async def test_inline_selection_resolves_current_grant_and_keeps_conversions_economical(
    operation, quality
):
    db = make_db()
    db.get_user_download_quality.return_value = "1080"
    chosen = make_chosen_inline(f"{operation}:example", query=URL)
    with patch.object(
        inline.downloader, "download", AsyncMock(return_value=DownloadResult(success=False))
    ) as call:
        await inline.chosen_inline_handler(chosen, make_bot(), db, Translator("en"))
    assert call.await_args.kwargs["quality"] == quality
    if operation == "mp3":
        db.get_user_download_quality.assert_not_awaited()
