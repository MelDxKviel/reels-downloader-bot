"""Video previews preserve geometry, survive file leases and reach Telegram."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.bot.handlers import download, download_cmd, inline
from src.bot.rich_carousel import edit_inline_rich_carousel, rich_carousel_variants
from src.services import download_worker, media_io
from src.services.downloader import VideoDownloader
from src.services.i18n import Translator
from src.services.media import CarouselSlide, DownloadResult

from ._helpers import make_bot, make_db, make_message, make_status_message


def preview_result(tmp_path):
    paths = [tmp_path / name for name in ("video.mp4", "video.thumbnail.jpg", "video.cover.jpg")]
    for path in paths:
        path.write_bytes(b"media")
    return DownloadResult(
        success=True,
        file_path=str(paths[0]),
        thumbnail_path=str(paths[1]),
        cover_path=str(paths[2]),
        width=720,
        height=1280,
        duration=5,
    )


def test_preview_cache_roundtrip_and_leases(tmp_path):
    service = VideoDownloader(str(tmp_path))
    result = preview_result(tmp_path)
    second = tmp_path / "second.mp4"
    second.write_bytes(b"media")
    result.carousel_slides = [
        CarouselSlide(
            "",
            True,
            result.file_path,
            thumbnail_path=result.thumbnail_path,
            cover_path=result.cover_path,
        ),
        CarouselSlide("", True, str(second)),
    ]
    url = "https://www.instagram.com/p/previews/"
    service.add_to_cache(url, result)
    service = VideoDownloader(str(tmp_path))
    cached = service.get_from_cache(url, reserve=True)
    assert cached.thumbnail_path == result.thumbnail_path
    assert cached.cover_path == result.cover_path
    assert cached.carousel_slides[0].cover_path == result.cover_path
    paths = service._entry_file_paths(service._result_entry(cached))
    assert len(paths) == 4
    assert service.clear_cache() == 0
    assert all(Path(path).is_file() for path in paths)
    service.release_result(cached)
    assert all(not Path(path).exists() for path in paths)


def test_preview_failure_cleans_partial_jpegs_and_keeps_video(tmp_path, monkeypatch):
    result = preview_result(tmp_path)
    monkeypatch.setattr(media_io.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(
        media_io.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("ffmpeg", 15))
    )
    assert VideoDownloader._extract_video_previews(result.file_path, 5) == (None, None)
    assert Path(result.file_path).exists()
    assert not Path(result.thumbnail_path).exists()
    assert not Path(result.cover_path).exists()


def test_previews_generated_once_per_video_and_skip_photos(tmp_path, monkeypatch):
    service = VideoDownloader(str(tmp_path))
    service.has_ffmpeg = True
    result = preview_result(tmp_path)
    result.carousel_slides = [
        CarouselSlide("", True, result.file_path),
        CarouselSlide("", False, "photo.jpg"),
    ]
    extract = Mock(return_value=(result.thumbnail_path, result.cover_path))
    monkeypatch.setattr(service, "_extract_video_previews", extract)
    service._prepare_video_previews(result)
    extract.assert_called_once_with(result.file_path, result.duration)
    assert result.carousel_slides[0].cover_path == result.cover_path
    assert result.carousel_slides[1].cover_path is None


async def test_worker_retains_preview_files(tmp_path, monkeypatch):
    service = VideoDownloader(str(tmp_path))

    async def worker(request, directory):
        result = preview_result(directory)
        (directory / "partial.part").write_bytes(b"partial")
        return vars(result)

    monkeypatch.setattr(download_worker, "run_worker", worker)
    result = await download_worker.run_download_worker(
        service, "https://youtube.com/watch?v=x", True
    )
    assert result.success
    assert Path(result.cover_path).is_file() and Path(result.thumbnail_path).is_file()
    assert not list(tmp_path.rglob("*.part"))
    assert service.discard_result_files(result) == 3


@pytest.mark.parametrize("command", [False, True])
async def test_regular_video_attaches_both_previews(tmp_path, monkeypatch, command):
    result = preview_result(tmp_path)
    module = download_cmd if command else download
    monkeypatch.setattr(module.downloader, "download", AsyncMock(return_value=result))
    monkeypatch.setattr(module.downloader, "set_telegram_file_id", Mock())
    message = make_message("https://youtube.com/watch?v=x")
    status = make_status_message()
    message.answer.return_value = status
    if command:
        await module._download_and_send(message, make_db(), status, message.text, Translator("en"))
    else:
        await module.handle_url(message, make_db(), Translator("en"))
    kwargs = message.bot.send_video.await_args.kwargs
    assert kwargs["thumbnail"].path == result.thumbnail_path
    assert kwargs["cover"].path == result.cover_path
    assert (kwargs["width"], kwargs["height"]) == (720, 1280)


async def test_carousel_uploads_and_fallback_attach_previews(tmp_path):
    result = preview_result(tmp_path)
    slides = [
        CarouselSlide(
            "",
            True,
            result.file_path,
            thumbnail_path=result.thumbnail_path,
            cover_path=result.cover_path,
        ),
        CarouselSlide("", False, result.cover_path),
    ]
    media = rich_carousel_variants(slides)[0].media[0].media
    assert media.thumbnail.path == result.thumbnail_path and media.cover.path == result.cover_path
    message = make_message()
    await download._send_carousel_files(message, slides)
    media = message.bot.send_media_group.await_args.kwargs["media"][0]
    assert media.thumbnail.path == result.thumbnail_path and media.cover.path == result.cover_path
    await download._send_carousel_files(message, slides[:1])
    assert message.answer_video.await_args.kwargs["cover"].path == result.cover_path


async def test_inline_edit_uses_reusable_cover_id(tmp_path, monkeypatch):
    result = preview_result(tmp_path)
    monkeypatch.setattr(inline.downloader, "download", AsyncMock(return_value=result))
    monkeypatch.setattr(inline.downloader, "set_telegram_file_id", Mock())
    upload_cover = AsyncMock(return_value="cover-id")
    upload_video = AsyncMock(return_value="video-id")
    monkeypatch.setattr(inline, "_upload_photo_and_get_file_id", upload_cover)
    monkeypatch.setattr(inline, "_upload_video_and_get_file_id", upload_video)
    bot = make_bot()
    chosen = MagicMock(
        result_id="download:abc",
        query="https://youtube.com/watch?v=x",
        inline_message_id="inline-id",
    )
    chosen.from_user.id = 100
    await inline.chosen_inline_handler(
        chosen,
        bot,
        make_db(),
        Translator("en"),
    )
    upload_cover.assert_awaited_once_with(bot, result.cover_path)
    assert upload_video.await_args.kwargs["thumbnail_path"] == result.thumbnail_path
    assert upload_video.await_args.kwargs["cover_file_id"] == "cover-id"
    media = bot.edit_message_media.await_args.kwargs["media"]
    assert media.cover == "cover-id" and media.thumbnail is None


async def test_storage_video_attaches_thumbnail_and_cover_id(tmp_path, monkeypatch):
    result = preview_result(tmp_path)
    bot = make_bot()
    bot.send_video.return_value.video.file_id = "video-id"
    monkeypatch.setattr(inline, "_resolve_storage_chat_id", lambda: 42)
    assert (
        await inline._upload_video_and_get_file_id(
            bot, result.file_path, thumbnail_path=result.thumbnail_path, cover_file_id="cover-id"
        )
        == "video-id"
    )
    kwargs = bot.send_video.await_args.kwargs
    assert kwargs["thumbnail"].path == result.thumbnail_path and kwargs["cover"] == "cover-id"


async def test_inline_carousel_never_attaches_local_preview_files(tmp_path):
    result = preview_result(tmp_path)
    slides = [
        CarouselSlide(
            "",
            True,
            result.file_path,
            thumbnail_path=result.thumbnail_path,
            cover_path=result.cover_path,
        ),
        CarouselSlide("", False, result.cover_path),
    ]
    bot = make_bot()
    assert await edit_inline_rich_carousel(
        bot,
        "inline-id",
        slides,
        media_file_ids=["video-id", "photo-id"],
        media_cover_file_ids=["cover-id", None],
    )
    media = bot.edit_message_text.await_args.kwargs["rich_message"].media[0].media
    assert media.cover == "cover-id" and media.thumbnail is None


@pytest.fixture
def real_ffmpeg(monkeypatch):
    binary = os.environ.get("FFMPEG_TEST_BINARY") or shutil.which("ffmpeg")
    if not binary:
        pytest.skip("FFmpeg is required for real video preview tests")
    real_which = shutil.which
    monkeypatch.setattr(
        media_io.shutil, "which", lambda name: binary if name == "ffmpeg" else real_which(name)
    )
    return binary


@pytest.mark.parametrize(
    "size,duration,rotate,sar,expected",
    [
        ("720x1280", 2, False, 1, (180, 320)),
        ("640x360", 0.1, False, 1, (320, 180)),
        ("640x360", 2, True, 1, (180, 320)),
        ("320x360", 2, False, 2, (320, 180)),
    ],
)
def test_real_video_preview_geometry_and_decoded_frame(
    tmp_path, real_ffmpeg, size, duration, rotate, sar, expected
):
    image = pytest.importorskip("PIL.Image")
    source = tmp_path / "source.mp4"
    filters = f"setsar={sar}"
    if duration > 1:
        filters += ",drawbox=color=black:t=fill:enable='lt(t,0.4)'"
    subprocess.run(
        [
            real_ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:s={size}:r=25",
            "-t",
            str(duration),
            "-vf",
            filters,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    if rotate:
        rotated = tmp_path / "rotated.mp4"
        help_text = subprocess.run(
            [real_ffmpeg, "-h", "full"], capture_output=True, timeout=15, check=True
        ).stdout
        # Debian/FFmpeg 5 writes rotate tags; newer FFmpeg uses display matrices.
        rotation_input = ["-display_rotation", "90"] if b"-display_rotation" in help_text else []
        rotation_output = [] if rotation_input else ["-metadata:s:v:0", "rotate=90"]
        subprocess.run(
            [
                real_ffmpeg,
                "-loglevel",
                "error",
                "-y",
                *rotation_input,
                "-i",
                str(source),
                "-c",
                "copy",
                *rotation_output,
                str(rotated),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        source = rotated
    original_bytes = source.read_bytes()
    thumb, cover = VideoDownloader._extract_video_previews(str(source), duration)
    assert thumb and cover
    with image.open(thumb) as picture:
        assert picture.format == "JPEG" and picture.size == expected
        red, green, blue = picture.getpixel((picture.width // 2, picture.height // 2))
        assert red > 200 and green < 30 and blue < 30
    with image.open(cover) as picture:
        assert max(picture.size) <= 1280
        assert picture.width / picture.height == pytest.approx(expected[0] / expected[1], abs=0.01)
    assert Path(thumb).stat().st_size < 200_000
    assert source.read_bytes() == original_bytes


def test_real_short_video_unknown_duration_falls_back_to_first_frame(tmp_path, real_ffmpeg):
    source = tmp_path / "short.mp4"
    subprocess.run(
        [
            real_ffmpeg,
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=25",
            "-t",
            "0.1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    thumb, cover = VideoDownloader._extract_video_previews(str(source), None)
    assert thumb and cover
