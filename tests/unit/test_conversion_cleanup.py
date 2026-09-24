"""Regression checks for ownership of temporary conversion files and FFmpeg."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import download_cmd, gif, mp3, voice
from src.bot.handlers import round as round_handler
from src.services.downloader import DownloadResult, VideoDownloader
from src.services.i18n import Translator

from ._helpers import make_callback, make_db, make_message, make_state, make_status_message

CONVERTERS = [gif, mp3, voice, round_handler]


@pytest.fixture
def conversion_downloader(tmp_path, module, monkeypatch):
    downloader = VideoDownloader(str(tmp_path))
    monkeypatch.setattr(module, "downloader", downloader)
    return downloader


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.parametrize(
    "outcome", ["success", "download_failed", "conversion_failed", "cancelled"]
)
@pytest.mark.asyncio
async def test_url_conversion_removes_every_uncached_source(
    tmp_path, module, outcome, conversion_downloader
):
    source = tmp_path / "source.mp4"
    extra = tmp_path / "extra.jpg"
    source.write_bytes(b"video")
    extra.write_bytes(b"photo")
    result = DownloadResult(
        success=outcome != "download_failed",
        file_path=str(source),
        photo_paths=[str(extra)],
        error="failed" if outcome == "download_failed" else None,
    )
    error = {
        "conversion_failed": RuntimeError("conversion failed"),
        "cancelled": asyncio.CancelledError(),
    }.get(outcome)
    command = module.__name__.rsplit(".", 1)[-1]
    send = AsyncMock(side_effect=error)
    url = "https://youtube.com/watch?v=abc"
    with (
        patch.object(module.downloader, "download", AsyncMock(return_value=result)) as download,
        patch.object(module, f"_send_{command}", send),
    ):
        operation = getattr(module, f"_download_and_send_{command}")(
            make_message(user_id=123), make_db(), make_status_message(), url, Translator("en")
        )
        if error is None:
            await operation
        else:
            with pytest.raises(type(error)):
                await operation
        download.assert_awaited_once_with(url, allow_carousel=False, user_id=123)
    assert not source.exists()
    assert not extra.exists()


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.asyncio
async def test_url_conversion_preserves_cache_owned_source(tmp_path, module, conversion_downloader):
    source = tmp_path / "cached.mp4"
    source.write_bytes(b"video")
    result = DownloadResult(success=True, file_path=str(source), from_cache=True)
    command = module.__name__.rsplit(".", 1)[-1]
    with (
        patch.object(module.downloader, "download", AsyncMock(return_value=result)),
        patch.object(module, f"_send_{command}", AsyncMock()),
    ):
        await getattr(module, f"_download_and_send_{command}")(
            make_message(),
            make_db(),
            make_status_message(),
            "https://youtube.com/watch?v=abc",
            Translator("en"),
        )
    assert source.exists()


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.parametrize("interruption", [RuntimeError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_failed_delivery_cleans_source_and_converted_file(
    tmp_path, module, interruption, conversion_downloader
):
    source = tmp_path / "source.mp4"
    output = tmp_path / "converted.bin"
    source.write_bytes(b"video")
    output.write_bytes(b"converted")
    command = module.__name__.rsplit(".", 1)[-1]
    message = make_message()
    send_method = {
        "gif": "answer_animation",
        "mp3": "answer_audio",
        "voice": "answer_voice",
        "round": "answer_video_note",
    }[command]
    getattr(message, send_method).side_effect = interruption("delivery interrupted")

    async def convert(user_id, operation):
        assert user_id == message.from_user.id
        return await operation()

    result = DownloadResult(success=True, file_path=str(source))
    with (
        patch.object(module.downloader, "download", AsyncMock(return_value=result)),
        patch.object(module.downloader, "run_conversion", side_effect=convert),
        patch.object(module, f"_convert_to_{command}", AsyncMock(return_value=str(output))),
    ):
        operation = getattr(module, f"_download_and_send_{command}")(
            message,
            make_db(),
            make_status_message(),
            "https://youtube.com/watch?v=abc",
            Translator("en"),
        )
        if interruption is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            await operation
    assert not source.exists()
    assert not output.exists()


@pytest.mark.parametrize("module", CONVERTERS)
@pytest.mark.parametrize("interruption", [asyncio.TimeoutError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_ffmpeg_interruption_reaps_process_before_removing_partial_file(
    tmp_path, module, interruption
):
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock()
    outputs = []

    async def start_process(*cmd, **kwargs):
        output = Path(cmd[-1])
        output.write_bytes(b"partial conversion")
        outputs.append(output)
        return process

    async def interrupt_wait(waiter, timeout):
        waiter.close()
        raise interruption()

    async def reap_process():
        process.kill.assert_called_once()
        assert outputs[0].exists(), "Do not unlink before FFmpeg releases the output"
        process.returncode = -9

    process.wait.side_effect = reap_process
    command = module.__name__.rsplit(".", 1)[-1]
    with (
        patch.object(module, "DOWNLOAD_DIR", str(tmp_path)),
        patch.object(module.shutil, "which", return_value="ffmpeg"),
        patch.object(module.asyncio, "create_subprocess_exec", side_effect=start_process),
        patch.object(module.asyncio, "wait_for", side_effect=interrupt_wait),
    ):
        conversion = getattr(module, f"_convert_to_{command}")("input.mp4")
        if interruption is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await conversion
        else:
            assert await conversion is None
    process.wait.assert_awaited_once()
    assert outputs and all(not output.exists() for output in outputs)


@pytest.mark.parametrize("module", [download_cmd, *CONVERTERS])
@pytest.mark.asyncio
async def test_stale_cancel_button_does_not_clear_new_prompt(module):
    command = module.__name__.rsplit(".", 1)[-1].replace("_cmd", "")
    callback = make_callback(f"cancel_{command}:100", user_id=100)
    callback.message.message_id = 41
    state = make_state({"prompt_message_id": 42})
    await getattr(module, f"cancel_{command}")(callback, state, Translator("en"))
    state.clear.assert_not_awaited()
    callback.message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once()
