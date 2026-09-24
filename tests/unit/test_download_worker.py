"""Exercise actual OS process trees without a network extractor or FFmpeg."""

import asyncio
import contextlib
import ctypes
import importlib.util
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.services import download_worker
from src.services.downloader import VideoDownloader
from src.services.media import CarouselSlide

_LOCAL_WORKER = '''
import json
import os
import subprocess
import sys
import time
from pathlib import Path

result, state, release, heartbeat, mode = sys.argv[1:]
sys.stdin.buffer.read()  # Same launch barrier as the production worker.
if mode == "imports":
    import aiogram
    import sqlalchemy
    import yt_dlp
    Path(result).write_text(json.dumps({
        name: str(Path(module.__file__).resolve())
        for name, module in (("aiogram", aiogram), ("sqlalchemy", sqlalchemy), ("yt_dlp", yt_dlp))
    }))
    sys.exit(0)
child_code = """
import os
import sys
import time
from pathlib import Path
marker = Path(sys.argv[1])
while True:
    marker.write_text(str(os.getpid()))
    time.sleep(0.03)
"""
options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
child = subprocess.Popen(
    [sys.executable, "-c", child_code, heartbeat],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
)
while not Path(heartbeat).exists():
    time.sleep(0.01)
Path(state).write_text(json.dumps({"parent": os.getpid(), "child": child.pid}))
if mode == "wait":
    time.sleep(120)
else:
    while not Path(release).exists():
        time.sleep(0.01)
    if mode == "crash":
        sys.exit(7)
    Path(result).write_text(json.dumps({"ok": True}))
'''


def _is_running(pid: int) -> bool:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.WaitForSingleObject.restype = ctypes.c_ulong
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed grandchild may await reaping by init; a zombie cannot write media anymore.
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        try:
            return not any(
                line.startswith("State:") and "Z" in line
                for line in status.read_text().splitlines()
            )
        except FileNotFoundError:
            return False
    return True


async def _wait_for(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def local_worker(monkeypatch, tmp_path):
    script = tmp_path / "local_worker.py"
    script.write_text(_LOCAL_WORKER, encoding="utf-8")
    state = tmp_path / "pids.json"
    release = tmp_path / "release"
    heartbeat = tmp_path / "heartbeat"
    options_seen = []
    processes = []
    tasks = []
    settings = {"mode": "wait"}
    real_create = asyncio.create_subprocess_exec

    async def create(*args, **kwargs):
        if len(args) > 3 and args[1:3] == ("-m", "src.services.download_worker"):
            options_seen.append(dict(kwargs))
            args = (
                args[0],
                str(script),
                str(args[3]),
                str(state),
                str(release),
                str(heartbeat),
                settings["mode"],
            )
            proc = await real_create(*args, **kwargs)
            processes.append(proc)
            return proc
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(download_worker.asyncio, "create_subprocess_exec", create)

    def start(mode="wait", timeout=10):
        settings["mode"] = mode
        task = asyncio.create_task(download_worker.run_worker({}, tmp_path, timeout=timeout))
        tasks.append(task)
        return task

    async def ready():
        await _wait_for(lambda: state.exists() and state.stat().st_size > 0)
        pids = json.loads(state.read_text())
        assert pids["parent"] == processes[0].pid, "worker must not be a venv launcher process"
        assert _is_running(pids["parent"])
        assert _is_running(pids["child"])
        options = options_seen[0]
        if os.name == "nt":
            assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
        else:
            assert options["start_new_session"] is True
        return pids

    yield SimpleNamespace(
        start=start,
        ready=ready,
        release=release,
        directory=tmp_path,
        processes=processes,
        state=state,
    )

    # A failing regression must still leave no Python workers on the developer's computer.
    for task in tasks:
        if not task.done():
            task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(task, 3)
    known_pids = {proc.pid for proc in processes}
    if state.exists() and state.stat().st_size:
        known_pids.update(json.loads(state.read_text()).values())
    for pid in known_pids:
        if _is_running(pid):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
    for proc in processes:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(proc.wait(), 3)


async def _assert_tree_stopped(pids):
    await _wait_for(lambda: not any(_is_running(pid) for pid in pids.values()), timeout=2)


async def test_deadline_kills_actual_worker_and_its_child(local_worker):
    started = time.monotonic()
    task = local_worker.start(timeout=1.5)
    pids = await local_worker.ready()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, 5)

    await _assert_tree_stopped(pids)
    assert time.monotonic() - started < 5
    assert not (local_worker.directory / "result.json").exists()


async def test_cancellation_kills_actual_worker_and_its_child(local_worker):
    task = local_worker.start()
    pids = await local_worker.ready()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)

    await _assert_tree_stopped(pids)
    assert not (local_worker.directory / "result.json").exists()


async def test_crashed_worker_does_not_leave_a_running_child(local_worker):
    task = local_worker.start(mode="crash")
    pids = await local_worker.ready()
    local_worker.release.touch()

    with pytest.raises(RuntimeError, match="exited with code 7"):
        await asyncio.wait_for(task, 5)

    await _assert_tree_stopped(pids)


async def test_completed_worker_does_not_leave_a_running_child(local_worker):
    task = local_worker.start(mode="success")
    pids = await local_worker.ready()
    local_worker.release.touch()

    assert await asyncio.wait_for(task, 5) == {"ok": True}

    await _assert_tree_stopped(pids)
    assert not (local_worker.directory / "result.json").exists()


async def test_worker_interpreter_can_import_current_environment_dependencies(local_worker):
    task = local_worker.start(mode="imports")

    imports = await asyncio.wait_for(task, 10)

    assert imports == {
        name: str(Path(importlib.util.find_spec(name).origin).resolve())
        for name in ("aiogram", "sqlalchemy", "yt_dlp")
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object assignment")
async def test_job_assignment_failure_never_sends_request(local_worker, monkeypatch):
    closed = []

    class RejectedJob:
        def assign(self, pid):
            raise PermissionError("Job assignment refused")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(download_worker, "WindowsProcessJob", RejectedJob)
    task = local_worker.start()

    with pytest.raises(PermissionError, match="assignment refused"):
        await asyncio.wait_for(task, 5)

    assert closed == [True]
    assert not local_worker.state.exists()  # No request -> no extractor/child starts.
    assert all(not _is_running(proc.pid) for proc in local_worker.processes)


async def test_repeated_cancellation_waits_for_process_cleanup(local_worker, monkeypatch):
    stopping = asyncio.Event()
    release_cleanup = asyncio.Event()
    stop = download_worker.stop_process_tree

    async def delayed_stop(*args, **kwargs):
        stopping.set()
        await release_cleanup.wait()
        await stop(*args, **kwargs)

    monkeypatch.setattr(download_worker, "stop_process_tree", delayed_stop)
    task = local_worker.start()
    pids = await local_worker.ready()
    try:
        task.cancel()
        await asyncio.wait_for(stopping.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    await _assert_tree_stopped(pids)


async def test_production_worker_cli_serializes_unsupported_url_without_network(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "unused-default-downloads"))
    monkeypatch.setenv("BOT_TOKEN", "0:test")
    monkeypatch.setenv("ADMIN_USERS", "123456")

    result = await download_worker.run_worker(
        {"operation": "download", "url": "https://example.invalid/video", "allow_carousel": True},
        tmp_path,
        timeout=10,
    )

    assert result["success"] is False
    assert result["error_code"] == "downloader.error.unsupported_url"
    assert result["file_path"] is None
    assert result["carousel_slides"] is None
    assert not (tmp_path / "result.json").exists()


async def test_download_worker_keeps_only_referenced_media(tmp_path, monkeypatch):
    service = VideoDownloader(str(tmp_path))

    async def successful_worker(request, directory):
        assert request["operation"] == "download"
        video, photo = directory / "video.mp4", directory / "photo.jpg"
        for path in (video, photo, directory / "unfinished.part", directory / "cache.json"):
            path.write_bytes(b"local media")
        return {
            "success": True,
            "file_path": str(video),
            "carousel_slides": [
                {"url": "https://cdn.invalid/video", "local_path": str(video), "is_video": True},
                {"url": "https://cdn.invalid/photo", "local_path": str(photo)},
            ],
        }

    monkeypatch.setattr(download_worker, "run_worker", successful_worker)

    result = await download_worker.run_download_worker(service, "https://example.invalid", True)

    assert result.success
    assert all(isinstance(slide, CarouselSlide) for slide in result.carousel_slides)
    assert {path.name for path in tmp_path.rglob("*") if path.is_file()} == {
        "video.mp4",
        "photo.jpg",
    }
    assert Path(result.file_path).is_file()
    assert all(Path(slide.local_path).is_file() for slide in result.carousel_slides)


@pytest.mark.parametrize("failure", ["result", "timeout", "exception", "cancel", "outside-path"])
async def test_download_worker_failure_removes_only_its_job_directory(
    tmp_path, monkeypatch, failure
):
    service = VideoDownloader(str(tmp_path))
    retained = tmp_path / "other-cached-video.mp4"
    retained.write_bytes(b"other job")
    job_dirs = []

    async def failed_worker(request, directory):
        job_dirs.append(directory)
        (directory / "unfinished.part").write_bytes(b"partial media")
        if failure == "timeout":
            raise TimeoutError
        if failure == "exception":
            raise RuntimeError("extractor failed")
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "outside-path":
            return {"success": True, "file_path": str(retained)}
        return {"success": False, "error_code": "downloader.error.download_exception"}

    monkeypatch.setattr(download_worker, "run_worker", failed_worker)

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await download_worker.run_download_worker(service, "https://example.invalid", True)
    else:
        result = await download_worker.run_download_worker(service, "https://example.invalid", True)
        assert not result.success
        if failure == "timeout":
            assert result.error_code == "downloader.error.timeout"

    assert job_dirs and all(not path.exists() for path in job_dirs)
    assert retained.read_bytes() == b"other job"
    assert list(tmp_path.iterdir()) == [retained]
