"""Tests for the GIF encoder: mp4 output, size-guard retry loop, ffmpeg guards."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import gif as gif_h
from src.config import MAX_FILE_SIZE


def _ok_proc():
    proc = MagicMock()
    proc.returncode = 0
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_convert_returns_none_without_ffmpeg():
    with patch("src.bot.handlers.gif.shutil.which", return_value=None):
        assert await gif_h._convert_to_gif("input.mp4") is None


@pytest.mark.asyncio
async def test_convert_single_pass_success(tmp_path):
    calls = []

    async def fake_exec(*args, **kwargs):
        with open(args[-1], "wb") as f:
            f.write(b"x")
        calls.append(args)
        return _ok_proc()

    with patch("src.bot.handlers.gif.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("src.bot.handlers.gif.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                "src.bot.handlers.gif.asyncio.create_subprocess_exec", side_effect=fake_exec
            ):
                result = await gif_h._convert_to_gif("input.mp4")

    assert result is not None
    assert result.endswith(".mp4")
    # Tiny output fits the limit on the first try — no re-encode.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_convert_reencodes_until_under_limit(tmp_path):
    calls = []
    # First encode overshoots Telegram's limit, the second fits.
    sizes = [MAX_FILE_SIZE + 1, 1024]

    async def fake_exec(*args, **kwargs):
        with open(args[-1], "wb") as f:
            f.write(b"x")
        calls.append(args)
        return _ok_proc()

    with patch("src.bot.handlers.gif.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("src.bot.handlers.gif.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                "src.bot.handlers.gif.asyncio.create_subprocess_exec", side_effect=fake_exec
            ):
                with patch(
                    "src.bot.handlers.gif.os.path.getsize", side_effect=lambda _p: sizes.pop(0)
                ):
                    result = await gif_h._convert_to_gif("input.mp4")

    assert result is not None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_convert_gives_up_when_always_too_large(tmp_path):
    calls = []

    async def fake_exec(*args, **kwargs):
        with open(args[-1], "wb") as f:
            f.write(b"x")
        calls.append(args)
        return _ok_proc()

    with patch("src.bot.handlers.gif.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("src.bot.handlers.gif.DOWNLOAD_DIR", str(tmp_path)):
            with patch(
                "src.bot.handlers.gif.asyncio.create_subprocess_exec", side_effect=fake_exec
            ):
                with patch("src.bot.handlers.gif.os.path.getsize", return_value=MAX_FILE_SIZE + 1):
                    result = await gif_h._convert_to_gif("input.mp4")

    # Exhausts every fallback, then gives up and cleans up.
    assert result is None
    assert len(calls) == 3


def test_video_filter_caps_size_and_forces_even_dims():
    vf = gif_h._gif_video_filter(24, 640)
    assert "fps=24" in vf
    assert "min(iw,640)" in vf
    assert "min(ih,640)" in vf
    # Even-dimension safety pass for yuv420p.
    assert "trunc(iw/2)*2" in vf
