"""Commands reuse a replied source while delivery and ownership stay with the caller."""

import asyncio
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, User

from src.bot.handlers import gif, mp3, voice
from src.bot.handlers import round as round_handler
from src.bot.middlewares.access import UserAccessMiddleware
from src.services.downloader import DownloadResult, VideoDownloader
from src.services.i18n import Translator

from ._helpers import make_db, make_message, make_state, make_status_message

CONVERTERS = [mp3, voice, gif, round_handler]


def replied_message(kind=None, *, text=None, caption=None, mime="video/mp4"):
    media = {"file_id": "replied-file", "file_unique_id": "unique-file"}
    fields = {}
    if kind == "video":
        fields[kind] = {**media, "width": 160, "height": 120, "duration": 1}
    elif kind == "audio":
        fields[kind] = {**media, "duration": 1}
    elif kind == "document":
        fields[kind] = {**media, "mime_type": mime, "file_name": "source.mp4"}
    return Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=Chat(id=-100, type="group"),
        from_user=User(id=999, is_bot=False, first_name="Original author"),
        text=text,
        caption=caption,
        **fields,
    )


@pytest.fixture(scope="module")
def source_video(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for real reply conversion tests")
    source = tmp_path_factory.mktemp("reply-source") / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x120:r=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "0.3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return source


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_reply_video_produces_real_media_for_caller(
    tmp_path, monkeypatch, module, lang, source_video, db_service
):
    command = module.__name__.rsplit(".", 1)[-1]
    message = make_message(f"/{command}", user_id=100, chat_id=-100)
    # A link in the video's caption must not replace the actual attached video.
    message.reply_to_message = replied_message(
        "video", caption="https://youtube.com/watch?v=caption"
    )
    status = make_status_message()
    message.answer.return_value = status
    state = make_state({"prompt_message_id": 9})
    monkeypatch.setattr(module, "DOWNLOAD_DIR", str(tmp_path))
    message.bot.get_file.return_value = MagicMock(file_path="telegram/source.mp4")

    async def download_file(path, destination):
        assert path == "telegram/source.mp4"
        shutil.copyfile(source_video, destination)

    message.bot.download_file.side_effect = download_file
    method, field = {
        "mp3": ("answer_audio", "audio"),
        "voice": ("answer_voice", "voice"),
        "gif": ("answer_animation", "animation"),
        "round": ("answer_video_note", "video_note"),
    }[command]
    delivered = []

    async def deliver(**kwargs):
        output = Path(kwargs[field].path)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        streams = json.loads(probe.stdout)["streams"]
        codecs = {stream["codec_type"]: stream["codec_name"] for stream in streams}
        if command == "mp3":
            assert codecs == {"audio": "mp3"}
        elif command == "voice":
            assert codecs == {"audio": "opus"}
        elif command == "gif":
            assert codecs == {"video": "h264"}
        else:
            assert codecs["video"] == "h264"
            video = next(stream for stream in streams if stream["codec_type"] == "video")
            assert (video["width"], video["height"]) == (512, 512)
        delivered.append(output)

    getattr(message, method).side_effect = deliver
    with (
        patch.object(module.downloader, "download", AsyncMock()) as network_download,
        patch.object(
            module.downloader, "run_conversion", wraps=module.downloader.run_conversion
        ) as queued_conversion,
    ):
        await getattr(module, f"cmd_{command}")(message, state, db_service, Translator(lang))

    assert len(delivered) == 1
    assert not list(tmp_path.iterdir()), "Source and conversion output must both be removed"
    assert source_video.exists()
    assert queued_conversion.call_args.args[0] == 100
    network_download.assert_not_awaited()
    message.bot.get_file.assert_awaited_once_with("replied-file")
    message.bot.delete_message.assert_awaited_once_with(-100, 9)
    status.delete.assert_awaited_once()
    state.clear.assert_awaited_once()
    assert (await db_service.get_user_stats(100))["successful_downloads"] == 1
    assert (await db_service.get_user_stats(999))["total_downloads"] == 0


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.parametrize("location", ["text", "caption"])
async def test_reply_link_uses_standard_uncached_conversion(
    tmp_path, monkeypatch, module, location
):
    command = module.__name__.rsplit(".", 1)[-1]
    url = "https://youtube.com/watch?v=replied"
    message = make_message(f"/{command}")
    message.reply_to_message = replied_message(**{location: f"Watch {url}!"})
    message.answer.return_value = make_status_message()
    source = tmp_path / "uncached.mp4"
    source.write_bytes(b"video")
    result = DownloadResult(success=True, file_path=str(source))
    state = make_state()
    monkeypatch.setattr(module, "downloader", VideoDownloader(str(tmp_path)))
    with (
        patch.object(module.downloader, "download", AsyncMock(return_value=result)) as download,
        patch.object(module, f"_send_{command}", AsyncMock()) as send,
    ):
        await getattr(module, f"cmd_{command}")(message, state, make_db(), Translator("en"))
    download.assert_awaited_once_with(url, allow_carousel=False, user_id=100)
    assert send.await_args.args[0] is message
    assert send.await_args.args[3] == str(source)
    assert not source.exists()
    state.clear.assert_awaited_once()


@pytest.mark.parametrize("module", CONVERTERS)
async def test_explicit_argument_takes_priority_over_reply(module):
    command = module.__name__.rsplit(".", 1)[-1]
    message = make_message(f"/{command} https://x.com/user/status/123")
    message.reply_to_message = replied_message("video")
    with patch.object(module, f"_download_and_send_{command}", AsyncMock()) as download:
        await getattr(module, f"cmd_{command}")(message, make_state(), make_db(), Translator("en"))
    assert download.await_args.args[3] == "https://x.com/user/status/123"
    message.bot.get_file.assert_not_awaited()


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.parametrize("invalid_argument", [False, True])
async def test_unusable_reply_or_argument_keeps_prompt_and_owner(module, invalid_argument):
    command = module.__name__.rsplit(".", 1)[-1]
    message = make_message(f"/{command}" + (" invalid" if invalid_argument else ""))
    message.reply_to_message = replied_message(
        text="https://youtube.com.evil.test/watch?v=abc"
        if not invalid_argument
        else "https://youtube.com/watch?v=must-not-use"
    )
    message.answer.return_value = make_status_message()
    state = make_state()
    await getattr(module, f"cmd_{command}")(message, state, make_db(), Translator("ru"))
    state.set_state.assert_awaited_once()
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == f"cancel_{command}:100"
    message.bot.get_file.assert_not_awaited()


@pytest.mark.parametrize(
    "module,kind,mime",
    [
        *[(module, "document", "video/mp4") for module in CONVERTERS],
        (voice, "audio", "audio/mpeg"),
        (voice, "document", "audio/mpeg"),
    ],
)
async def test_reply_reuses_supported_file_types(tmp_path, monkeypatch, module, kind, mime):
    command = module.__name__.rsplit(".", 1)[-1]
    message = make_message(f"/{command}")
    message.reply_to_message = replied_message(kind, mime=mime)
    message.answer.return_value = make_status_message()
    monkeypatch.setattr(module, "DOWNLOAD_DIR", str(tmp_path))
    with patch.object(module, f"_send_{command}", AsyncMock()) as send:
        await getattr(module, f"cmd_{command}")(message, make_state(), make_db(), Translator("en"))
    message.bot.get_file.assert_awaited_once_with("replied-file")
    assert send.await_args.args[0] is message
    assert send.await_args.args[4:6] == ("Upload", "upload:100")


@pytest.mark.parametrize("module", CONVERTERS)
async def test_reply_rejects_non_media_document(module):
    command = module.__name__.rsplit(".", 1)[-1]
    message = make_message(f"/{command}")
    message.reply_to_message = replied_message("document", mime="application/pdf")
    await getattr(module, f"cmd_{command}")(message, make_state(), make_db(), Translator("en"))
    key = "voice.unsupported_format" if module is voice else f"{command}.not_video"
    message.answer.assert_awaited_once_with(Translator("en")(key))
    message.bot.get_file.assert_not_awaited()


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_reply_upload_failure_removes_partial_file(tmp_path, monkeypatch, module, failure):
    command = module.__name__.rsplit(".", 1)[-1]
    message = make_message(f"/{command}")
    message.reply_to_message = replied_message("video")
    message.answer.return_value = make_status_message()
    monkeypatch.setattr(module, "DOWNLOAD_DIR", str(tmp_path))

    async def download_file(path, destination):
        Path(destination).write_bytes(b"partial upload")
        raise failure("interrupted")

    message.bot.download_file.side_effect = download_file
    db = make_db()
    operation = getattr(module, f"cmd_{command}")(message, make_state(), db, Translator("en"))
    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await operation
    else:
        await operation
        message.answer.return_value.edit_text.assert_awaited_once_with(
            Translator("en")(f"{command}.upload_failed")
        )
        db.record_download.assert_awaited_once_with(
            user_id=100, platform="Upload", url="upload:100", success=False
        )
    assert message.bot.download_file.await_count == 1
    assert not list(tmp_path.iterdir())


async def test_reply_to_admin_does_not_grant_caller_access():
    message = make_message("/mp3", user_id=100)
    message.reply_to_message = replied_message("video")
    db = make_db()
    db.is_feature_enabled.return_value = True
    handler = AsyncMock()
    with patch("src.bot.middlewares.access.ADMIN_USERS", [999]):
        await UserAccessMiddleware(db)(handler, message, {})
    handler.assert_not_awaited()
    db.get_user.assert_awaited_once_with(100)
