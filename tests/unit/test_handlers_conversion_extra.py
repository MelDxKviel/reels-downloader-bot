"""Targeted tests for remaining missing branches in gif/mp3/voice/round handlers.

These cover the rare cleanup paths: ffmpeg cleanup os.remove failure, finally-block
os.remove failure, delete_message exception, upload_path cleanup failure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import gif as gif_h
from src.bot.handlers import mp3 as mp3_h
from src.bot.handlers import round as round_h
from src.bot.handlers import voice as voice_h
from src.services.i18n import Translator

from ._helpers import make_db, make_message, make_state, make_status_message

# ── _convert_to_*: returncode!=0 cleanup with os.remove failure ─────────────


@pytest.mark.parametrize(
    "module, fn",
    [
        (gif_h, "_convert_to_gif"),
        (mp3_h, "_convert_to_mp3"),
        (voice_h, "_convert_to_voice"),
        (round_h, "_convert_to_round"),
    ],
)
@pytest.mark.asyncio
async def test_convert_returncode_nonzero_remove_failure(tmp_path, module, fn):
    mod_name = module.__name__.rsplit(".", 1)[1]
    proc = MagicMock()
    proc.returncode = 1
    proc.wait = AsyncMock()

    async def fake_exec(*args, **kwargs):
        out_path = args[-1]
        with open(out_path, "wb") as f:
            f.write(b"x")
        return proc

    with patch(f"src.bot.handlers.{mod_name}.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch(f"src.bot.handlers.{mod_name}.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                f"src.bot.handlers.{mod_name}.asyncio.create_subprocess_exec",
                side_effect=fake_exec,
            ):
                with patch(f"src.bot.handlers.{mod_name}.os.remove", side_effect=OSError("nope")):
                    result = await getattr(module, fn)("input.mp4")
    assert result is None


# ── _send_*: finally os.remove failure ───────────────────────────────────────


@pytest.mark.parametrize(
    "module, send_fn, answer_attr, convert_fn",
    [
        (gif_h, "_send_gif", "answer_animation", "_convert_to_gif"),
        (mp3_h, "_send_mp3", "answer_audio", "_convert_to_mp3"),
        (voice_h, "_send_voice", "answer_voice", "_convert_to_voice"),
        (round_h, "_send_round", "answer_video_note", "_convert_to_round"),
    ],
)
@pytest.mark.asyncio
async def test_send_finally_remove_failure(tmp_path, module, send_fn, answer_attr, convert_fn):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    out_path = tmp_path / "out.bin"
    out_path.write_bytes(b"x")
    mod_name = module.__name__.rsplit(".", 1)[1]
    with patch.object(module, convert_fn, new=AsyncMock(return_value=str(out_path))):
        with patch(f"src.bot.handlers.{mod_name}.os.remove", side_effect=OSError("nope")):
            await getattr(module, send_fn)(
                msg, db, sm, "in.mp4", "YouTube", "https://youtube.com/x", Translator("en")
            )
    getattr(msg, answer_attr).assert_awaited()


# ── *_got_video: delete_message exception in FSM path ───────────────────────


@pytest.mark.parametrize(
    "module, fn, send_fn",
    [
        (gif_h, "gif_got_video", "_send_gif"),
        (mp3_h, "mp3_got_video", "_send_mp3"),
        (round_h, "round_got_video", "_send_round"),
    ],
)
@pytest.mark.asyncio
async def test_got_video_delete_message_exception(tmp_path, module, fn, send_fn):
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "v"
    msg.bot.get_file.return_value = MagicMock(file_path="/file/p")
    msg.bot.delete_message.side_effect = RuntimeError("gone")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state({"prompt_message_id": 5})
    db = make_db()
    with patch.object(module, send_fn, new=AsyncMock()):
        await getattr(module, fn)(msg, state, db, Translator("en"))


# ── voice_got_file: delete_message exception ────────────────────────────────


@pytest.mark.asyncio
async def test_voice_got_file_delete_message_exception(tmp_path):
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "v"
    msg.bot.get_file.return_value = MagicMock(file_path="/p")
    msg.bot.delete_message.side_effect = RuntimeError("gone")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state({"prompt_message_id": 1})
    db = make_db()
    with patch.object(voice_h, "_send_voice", new=AsyncMock()):
        await voice_h.voice_got_file(msg, state, db, Translator("en"))


# ── *_got_video: upload_path cleanup branch ─────────────────────────────────


@pytest.mark.parametrize(
    "module, fn, send_fn",
    [
        (gif_h, "gif_got_video", "_send_gif"),
        (mp3_h, "mp3_got_video", "_send_mp3"),
        (round_h, "round_got_video", "_send_round"),
    ],
)
@pytest.mark.asyncio
async def test_got_video_upload_path_cleanup(tmp_path, module, fn, send_fn):
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "v"
    msg.bot.get_file.return_value = MagicMock(file_path="/p")

    # Simulate file existing at the upload_path after download_file completes
    captured_path = {}

    async def fake_download(file_path, destination):
        captured_path["path"] = destination
        with open(destination, "wb") as f:
            f.write(b"x")

    msg.bot.download_file = AsyncMock(side_effect=fake_download)
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    mod_name = module.__name__.rsplit(".", 1)[1]
    with patch.object(module, send_fn, new=AsyncMock()):
        with patch(f"src.bot.handlers.{mod_name}.DOWNLOAD_DIR", str(tmp_path)):
            with patch(f"src.bot.handlers.{mod_name}.os.remove", side_effect=OSError("nope")):
                await getattr(module, fn)(msg, state, db, Translator("en"))


@pytest.mark.asyncio
async def test_voice_got_file_upload_path_cleanup(tmp_path):
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "v"
    msg.bot.get_file.return_value = MagicMock(file_path="/p")

    async def fake_download(file_path, destination):
        with open(destination, "wb") as f:
            f.write(b"x")

    msg.bot.download_file = AsyncMock(side_effect=fake_download)
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    with patch.object(voice_h, "_send_voice", new=AsyncMock()):
        with patch("src.bot.handlers.voice.DOWNLOAD_DIR", str(tmp_path)):
            with patch("src.bot.handlers.voice.os.remove", side_effect=OSError("nope")):
                await voice_h.voice_got_file(msg, state, db, Translator("en"))
