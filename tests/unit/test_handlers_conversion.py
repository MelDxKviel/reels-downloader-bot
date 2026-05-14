"""Tests for conversion handlers: /gif, /mp3, /voice, /round.

These handlers share an almost identical structure: FFmpeg conversion,
URL/file FSM flow, cancel callback. Tests are parameterized where possible.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import gif as gif_h
from src.bot.handlers import mp3 as mp3_h
from src.bot.handlers import round as round_h
from src.bot.handlers import voice as voice_h
from src.services.downloader import DownloadResult
from src.services.i18n import Translator

from ._helpers import make_callback, make_db, make_message, make_state, make_status_message

# ── _convert_to_* FFmpeg wrappers ───────────────────────────────────────────


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
async def test_convert_returns_none_without_ffmpeg(module, fn):
    with patch(
        f"src.bot.handlers.{module.__name__.rsplit('.', 1)[1]}.shutil.which", return_value=None
    ):
        result = await getattr(module, fn)("input.mp4")
    assert result is None


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
async def test_convert_success(tmp_path, module, fn):
    mod_name = module.__name__.rsplit(".", 1)[1]
    proc = MagicMock()
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()

    async def fake_exec(*args, **kwargs):
        # extract the output path (last positional arg before kwargs)
        out_path = args[-1]
        # create the output file so the function returns success
        with open(out_path, "wb") as f:
            f.write(b"x")
        return proc

    with patch(f"src.bot.handlers.{mod_name}.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch(f"src.bot.handlers.{mod_name}.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                f"src.bot.handlers.{mod_name}.asyncio.create_subprocess_exec",
                side_effect=fake_exec,
            ):
                result = await getattr(module, fn)("input.mp4")
    assert result is not None


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
async def test_convert_failure_returncode(tmp_path, module, fn):
    mod_name = module.__name__.rsplit(".", 1)[1]
    proc = MagicMock()
    proc.returncode = 1
    proc.wait = AsyncMock()
    proc.kill = MagicMock()

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
                result = await getattr(module, fn)("input.mp4")
    assert result is None


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
async def test_convert_timeout(tmp_path, module, fn):
    import asyncio

    mod_name = module.__name__.rsplit(".", 1)[1]
    proc = MagicMock()
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()

    async def fake_exec(*args, **kwargs):
        return proc

    with patch(f"src.bot.handlers.{mod_name}.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch(f"src.bot.handlers.{mod_name}.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                f"src.bot.handlers.{mod_name}.asyncio.create_subprocess_exec",
                side_effect=fake_exec,
            ):
                with patch(
                    f"src.bot.handlers.{mod_name}.asyncio.wait_for",
                    side_effect=asyncio.TimeoutError(),
                ):
                    result = await getattr(module, fn)("input.mp4")
    assert result is None


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
async def test_convert_timeout_kill_exception(tmp_path, module, fn):
    import asyncio

    mod_name = module.__name__.rsplit(".", 1)[1]
    proc = MagicMock()
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock(side_effect=RuntimeError("kill failed"))

    async def fake_exec(*args, **kwargs):
        return proc

    with patch(f"src.bot.handlers.{mod_name}.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch(f"src.bot.handlers.{mod_name}.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                f"src.bot.handlers.{mod_name}.asyncio.create_subprocess_exec",
                side_effect=fake_exec,
            ):
                with patch(
                    f"src.bot.handlers.{mod_name}.asyncio.wait_for",
                    side_effect=asyncio.TimeoutError(),
                ):
                    result = await getattr(module, fn)("input.mp4")
    assert result is None


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
async def test_convert_returncode_nonzero_with_output_file_cleanup(tmp_path, module, fn):
    """When returncode != 0 but output exists, file is removed."""
    mod_name = module.__name__.rsplit(".", 1)[1]
    proc = MagicMock()
    proc.returncode = 1
    proc.wait = AsyncMock()

    captured_paths = []

    async def fake_exec(*args, **kwargs):
        out_path = args[-1]
        captured_paths.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"x")
        return proc

    with patch(f"src.bot.handlers.{mod_name}.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch(f"src.bot.handlers.{mod_name}.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                f"src.bot.handlers.{mod_name}.asyncio.create_subprocess_exec",
                side_effect=fake_exec,
            ):
                await getattr(module, fn)("input.mp4")

    import os

    for p in captured_paths:
        assert not os.path.exists(p)


# ── /<cmd>: command handler ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module, cmd",
    [
        (gif_h, "cmd_gif"),
        (mp3_h, "cmd_mp3"),
        (voice_h, "cmd_voice"),
        (round_h, "cmd_round"),
    ],
)
@pytest.mark.asyncio
async def test_cmd_no_url_enters_fsm(module, cmd):
    msg = make_message(f"/{cmd[4:]}")
    state = make_state()
    db = make_db()
    await getattr(module, cmd)(msg, state, db, Translator("en"))
    state.set_state.assert_awaited()


@pytest.mark.parametrize(
    "module, cmd, send_fn_name, success_attr",
    [
        (gif_h, "cmd_gif", "_send_gif", "answer_animation"),
        (mp3_h, "cmd_mp3", "_send_mp3", "answer_audio"),
        (voice_h, "cmd_voice", "_send_voice", "answer_voice"),
        (round_h, "cmd_round", "_send_round", "answer_video_note"),
    ],
)
@pytest.mark.asyncio
async def test_cmd_with_url(tmp_path, module, cmd, send_fn_name, success_attr):
    msg = make_message("/x https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    with patch.object(module.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(module, send_fn_name, new=AsyncMock()) as mock_send:
            await getattr(module, cmd)(msg, state, db, Translator("en"))
            mock_send.assert_awaited()


# ── _download_and_send_* / _send_* ──────────────────────────────────────────


@pytest.mark.parametrize(
    "module, dl_fn",
    [
        (gif_h, "_download_and_send_gif"),
        (mp3_h, "_download_and_send_mp3"),
        (voice_h, "_download_and_send_voice"),
        (round_h, "_download_and_send_round"),
    ],
)
@pytest.mark.asyncio
async def test_download_and_send_exception(module, dl_fn):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    with patch.object(module.downloader, "download", AsyncMock(side_effect=RuntimeError("oops"))):
        await getattr(module, dl_fn)(msg, db, sm, "https://youtube.com/watch?v=a", Translator("en"))
    sm.edit_text.assert_awaited()


@pytest.mark.parametrize(
    "module, dl_fn",
    [
        (gif_h, "_download_and_send_gif"),
        (mp3_h, "_download_and_send_mp3"),
        (voice_h, "_download_and_send_voice"),
        (round_h, "_download_and_send_round"),
    ],
)
@pytest.mark.asyncio
async def test_download_and_send_failed_result(module, dl_fn):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    result = DownloadResult(success=False, error="x")
    with patch.object(module.downloader, "download", AsyncMock(return_value=result)):
        await getattr(module, dl_fn)(msg, db, sm, "https://youtube.com/watch?v=a", Translator("en"))
    sm.edit_text.assert_awaited()


@pytest.mark.parametrize(
    "module, send_fn, answer_attr",
    [
        (gif_h, "_send_gif", "answer_animation"),
        (mp3_h, "_send_mp3", "answer_audio"),
        (voice_h, "_send_voice", "answer_voice"),
        (round_h, "_send_round", "answer_video_note"),
    ],
)
@pytest.mark.asyncio
async def test_send_convert_error(tmp_path, module, send_fn, answer_attr):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    input_path = tmp_path / "in.mp4"
    input_path.write_bytes(b"x")
    mod_name = module.__name__.rsplit(".", 1)[1]
    convert_fn = {
        "gif": "_convert_to_gif",
        "mp3": "_convert_to_mp3",
        "voice": "_convert_to_voice",
        "round": "_convert_to_round",
    }[mod_name]
    with patch.object(module, convert_fn, new=AsyncMock(return_value=None)):
        await getattr(module, send_fn)(
            msg, db, sm, str(input_path), "YouTube", "https://youtube.com/x", Translator("en")
        )
    sm.edit_text.assert_awaited()


@pytest.mark.parametrize(
    "module, send_fn, answer_attr",
    [
        (gif_h, "_send_gif", "answer_animation"),
        (mp3_h, "_send_mp3", "answer_audio"),
        (voice_h, "_send_voice", "answer_voice"),
        (round_h, "_send_round", "answer_video_note"),
    ],
)
@pytest.mark.asyncio
async def test_send_success(tmp_path, module, send_fn, answer_attr):
    msg = make_message()
    sm = make_status_message()
    db = make_db()
    input_path = tmp_path / "in.mp4"
    input_path.write_bytes(b"x")
    out_path = tmp_path / "out.bin"
    out_path.write_bytes(b"x")

    mod_name = module.__name__.rsplit(".", 1)[1]
    convert_fn = {
        "gif": "_convert_to_gif",
        "mp3": "_convert_to_mp3",
        "voice": "_convert_to_voice",
        "round": "_convert_to_round",
    }[mod_name]
    with patch.object(module, convert_fn, new=AsyncMock(return_value=str(out_path))):
        await getattr(module, send_fn)(
            msg, db, sm, str(input_path), "YouTube", "https://youtube.com/x", Translator("en")
        )
    getattr(msg, answer_attr).assert_awaited()


@pytest.mark.parametrize(
    "module, send_fn, answer_attr",
    [
        (gif_h, "_send_gif", "answer_animation"),
        (mp3_h, "_send_mp3", "answer_audio"),
        (voice_h, "_send_voice", "answer_voice"),
        (round_h, "_send_round", "answer_video_note"),
    ],
)
@pytest.mark.asyncio
async def test_send_exception_in_send(tmp_path, module, send_fn, answer_attr):
    msg = make_message()
    setattr(msg, answer_attr, AsyncMock(side_effect=RuntimeError("send fail")))
    sm = make_status_message()
    db = make_db()
    out_path = tmp_path / "out.bin"
    out_path.write_bytes(b"x")
    mod_name = module.__name__.rsplit(".", 1)[1]
    convert_fn = {
        "gif": "_convert_to_gif",
        "mp3": "_convert_to_mp3",
        "voice": "_convert_to_voice",
        "round": "_convert_to_round",
    }[mod_name]
    with patch.object(module, convert_fn, new=AsyncMock(return_value=str(out_path))):
        await getattr(module, send_fn)(
            msg, db, sm, "in.mp4", "YouTube", "https://youtube.com/x", Translator("en")
        )
    sm.edit_text.assert_awaited()


# ── cancel handlers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module, cancel_fn, prefix",
    [
        (gif_h, "cancel_gif", "cancel_gif"),
        (mp3_h, "cancel_mp3", "cancel_mp3"),
        (voice_h, "cancel_voice", "cancel_voice"),
        (round_h, "cancel_round", "cancel_round"),
    ],
)
@pytest.mark.asyncio
async def test_cancel_wrong_owner(module, cancel_fn, prefix):
    cb = make_callback(f"{prefix}:1", user_id=2)
    state = make_state()
    await getattr(module, cancel_fn)(cb, state, Translator("en"))
    state.clear.assert_not_called()


@pytest.mark.parametrize(
    "module, cancel_fn, prefix",
    [
        (gif_h, "cancel_gif", "cancel_gif"),
        (mp3_h, "cancel_mp3", "cancel_mp3"),
        (voice_h, "cancel_voice", "cancel_voice"),
        (round_h, "cancel_round", "cancel_round"),
    ],
)
@pytest.mark.asyncio
async def test_cancel_success(module, cancel_fn, prefix):
    cb = make_callback(f"{prefix}:1", user_id=1)
    state = make_state()
    await getattr(module, cancel_fn)(cb, state, Translator("en"))
    state.clear.assert_awaited()


@pytest.mark.parametrize(
    "module, cancel_fn",
    [
        (voice_h, "cancel_voice"),
        (round_h, "cancel_round"),
    ],
)
@pytest.mark.asyncio
async def test_cancel_voice_round_bad_callback_data(module, cancel_fn):
    cb = make_callback("just_text", user_id=1)
    state = make_state()
    await getattr(module, cancel_fn)(cb, state, Translator("en"))
    cb.answer.assert_awaited()


# ── *_got_url handlers ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module, fn",
    [
        (gif_h, "gif_got_url"),
        (mp3_h, "mp3_got_url"),
        (voice_h, "voice_got_url"),
        (round_h, "round_got_url"),
    ],
)
@pytest.mark.asyncio
async def test_got_url_no_url(module, fn):
    msg = make_message("not a url")
    state = make_state()
    db = make_db()
    await getattr(module, fn)(msg, state, db, Translator("en"))
    state.clear.assert_not_called()


@pytest.mark.parametrize(
    "module, fn",
    [
        (gif_h, "gif_got_url"),
        (mp3_h, "mp3_got_url"),
        (voice_h, "voice_got_url"),
        (round_h, "round_got_url"),
    ],
)
@pytest.mark.asyncio
async def test_got_url_with_url(tmp_path, module, fn):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state({"prompt_message_id": 5})
    db = make_db()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = DownloadResult(success=True, file_path=str(video))
    mod_name = module.__name__.rsplit(".", 1)[1]
    dl_fn = {
        "gif": "_download_and_send_gif",
        "mp3": "_download_and_send_mp3",
        "voice": "_download_and_send_voice",
        "round": "_download_and_send_round",
    }[mod_name]
    with patch.object(module.downloader, "download", AsyncMock(return_value=result)):
        with patch.object(module, dl_fn, new=AsyncMock()):
            await getattr(module, fn)(msg, state, db, Translator("en"))
    state.clear.assert_awaited()
    msg.bot.delete_message.assert_awaited()


@pytest.mark.parametrize(
    "module, fn",
    [
        (gif_h, "gif_got_url"),
        (mp3_h, "mp3_got_url"),
        (voice_h, "voice_got_url"),
        (round_h, "round_got_url"),
    ],
)
@pytest.mark.asyncio
async def test_got_url_delete_message_exception(tmp_path, module, fn):
    msg = make_message("https://youtube.com/watch?v=abc")
    sm = make_status_message()
    msg.answer.return_value = sm
    msg.bot.delete_message.side_effect = RuntimeError("gone")
    state = make_state({"prompt_message_id": 5})
    db = make_db()
    mod_name = module.__name__.rsplit(".", 1)[1]
    dl_fn = {
        "gif": "_download_and_send_gif",
        "mp3": "_download_and_send_mp3",
        "voice": "_download_and_send_voice",
        "round": "_download_and_send_round",
    }[mod_name]
    with patch.object(module, dl_fn, new=AsyncMock()):
        await getattr(module, fn)(msg, state, db, Translator("en"))


# ── *_got_video/file handlers ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module, fn, send_fn",
    [
        (gif_h, "gif_got_video", "_send_gif"),
        (mp3_h, "mp3_got_video", "_send_mp3"),
        (round_h, "round_got_video", "_send_round"),
    ],
)
@pytest.mark.asyncio
async def test_got_video_video_message(tmp_path, module, fn, send_fn):
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "vid_fid"
    msg.bot.get_file.return_value = MagicMock(file_path="/file/path")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state({"prompt_message_id": 1})
    db = make_db()
    with patch.object(module, send_fn, new=AsyncMock()):
        await getattr(module, fn)(msg, state, db, Translator("en"))


@pytest.mark.parametrize(
    "module, fn",
    [
        (gif_h, "gif_got_video"),
        (mp3_h, "mp3_got_video"),
        (round_h, "round_got_video"),
    ],
)
@pytest.mark.asyncio
async def test_got_video_document_non_video_rejected(tmp_path, module, fn):
    msg = make_message()
    msg.document = MagicMock()
    msg.document.mime_type = "image/png"
    msg.document.file_id = "doc_fid"
    state = make_state()
    db = make_db()
    await getattr(module, fn)(msg, state, db, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.parametrize(
    "module, fn, send_fn",
    [
        (gif_h, "gif_got_video", "_send_gif"),
        (mp3_h, "mp3_got_video", "_send_mp3"),
        (round_h, "round_got_video", "_send_round"),
    ],
)
@pytest.mark.asyncio
async def test_got_video_document_video_accepted(tmp_path, module, fn, send_fn):
    msg = make_message()
    msg.document = MagicMock()
    msg.document.mime_type = "video/mp4"
    msg.document.file_id = "doc_fid"
    msg.bot.get_file.return_value = MagicMock(file_path="/file/path")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    with patch.object(module, send_fn, new=AsyncMock()):
        await getattr(module, fn)(msg, state, db, Translator("en"))


@pytest.mark.parametrize(
    "module, fn",
    [
        (gif_h, "gif_got_video"),
        (mp3_h, "mp3_got_video"),
        (round_h, "round_got_video"),
    ],
)
@pytest.mark.asyncio
async def test_got_video_upload_failed(tmp_path, module, fn):
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "vid_fid"
    msg.bot.get_file.side_effect = RuntimeError("upload fail")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    await getattr(module, fn)(msg, state, db, Translator("en"))
    sm.edit_text.assert_awaited()


@pytest.mark.parametrize(
    "module, fn",
    [
        (gif_h, "gif_got_video"),
        (mp3_h, "mp3_got_video"),
        (round_h, "round_got_video"),
    ],
)
@pytest.mark.asyncio
async def test_got_video_no_video_no_doc_returns_early(module, fn):
    msg = make_message()
    msg.video = None
    msg.document = None
    state = make_state()
    db = make_db()
    await getattr(module, fn)(msg, state, db, Translator("en"))


# ── voice has audio + document branches ─────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_got_file_video(tmp_path):
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "v"
    msg.bot.get_file.return_value = MagicMock(file_path="/p")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state({"prompt_message_id": 1})
    db = make_db()
    with patch.object(voice_h, "_send_voice", new=AsyncMock()):
        await voice_h.voice_got_file(msg, state, db, Translator("en"))


@pytest.mark.asyncio
async def test_voice_got_file_audio(tmp_path):
    msg = make_message()
    msg.audio = MagicMock()
    msg.audio.file_id = "a"
    msg.bot.get_file.return_value = MagicMock(file_path="/p")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    with patch.object(voice_h, "_send_voice", new=AsyncMock()):
        await voice_h.voice_got_file(msg, state, db, Translator("en"))


@pytest.mark.asyncio
async def test_voice_got_file_document_video(tmp_path):
    msg = make_message()
    msg.document = MagicMock()
    msg.document.mime_type = "video/mp4"
    msg.document.file_id = "d"
    msg.bot.get_file.return_value = MagicMock(file_path="/p")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    with patch.object(voice_h, "_send_voice", new=AsyncMock()):
        await voice_h.voice_got_file(msg, state, db, Translator("en"))


@pytest.mark.asyncio
async def test_voice_got_file_document_audio(tmp_path):
    msg = make_message()
    msg.document = MagicMock()
    msg.document.mime_type = "audio/mpeg"
    msg.document.file_id = "d"
    msg.bot.get_file.return_value = MagicMock(file_path="/p")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    with patch.object(voice_h, "_send_voice", new=AsyncMock()):
        await voice_h.voice_got_file(msg, state, db, Translator("en"))


@pytest.mark.asyncio
async def test_voice_got_file_document_unsupported_mime():
    msg = make_message()
    msg.document = MagicMock()
    msg.document.mime_type = "text/plain"
    state = make_state()
    db = make_db()
    await voice_h.voice_got_file(msg, state, db, Translator("en"))
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_voice_got_file_unknown_returns_early():
    msg = make_message()
    msg.video = None
    msg.audio = None
    msg.document = None
    state = make_state()
    db = make_db()
    await voice_h.voice_got_file(msg, state, db, Translator("en"))


@pytest.mark.asyncio
async def test_voice_got_file_upload_failure():
    msg = make_message()
    msg.video = MagicMock()
    msg.video.file_id = "v"
    msg.bot.get_file.side_effect = RuntimeError("fail")
    sm = make_status_message()
    msg.answer.return_value = sm
    state = make_state()
    db = make_db()
    await voice_h.voice_got_file(msg, state, db, Translator("en"))
    sm.edit_text.assert_awaited()
