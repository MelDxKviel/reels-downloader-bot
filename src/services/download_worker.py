"""Isolate network extractors and their FFmpeg children behind a hard deadline."""

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from src.config import DOWNLOAD_TIMEOUT
from src.services.download_quality import DEFAULT_QUALITY, validate_quality
from src.services.media import CarouselSlide, DownloadResult
from src.services.process_tree import WindowsProcessJob

logger = logging.getLogger(__name__)


async def stop_process_tree(
    proc: asyncio.subprocess.Process, job: WindowsProcessJob | None = None
) -> None:
    """Reap the worker and all its subprocesses before deleting their files."""
    try:
        if os.name == "nt":
            if job is not None:
                await job.terminate()
            elif proc.returncode is None:
                # Assignment failed before stdin was delivered: no extractor children exist.
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await proc.wait()
    finally:
        if job is not None:
            job.close()


async def _wait_for_cleanup(task: asyncio.Task) -> bool:
    """Repeated caller cancellation must not interrupt process termination."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return cancelled


async def run_worker(request: dict, directory: Path, *, timeout=DOWNLOAD_TIMEOUT) -> dict:
    """Use stdin for the request and a result file so extractor stdout is harmless."""
    result_path = directory / "result.json"
    executable = sys.executable
    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    if os.name == "nt":
        # A Windows venv python.exe is a launcher which can spawn another interpreter
        # before stdin arrives. Start the actual interpreter directly so job assignment
        # precedes every child, while preserving this environment's installed packages.
        executable = getattr(sys, "_base_executable", sys.executable)
        options["env"] = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    started = asyncio.get_running_loop().time()
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            executable,
            "-m",
            "src.services.download_worker",
            str(result_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            **options,
        )
    )
    job = None
    try:
        proc = await asyncio.shield(spawn)
        if os.name == "nt":
            candidate = WindowsProcessJob()
            try:
                candidate.assign(proc.pid)
            except BaseException:
                candidate.close()
                raise
            job = candidate
        remaining = max(0, timeout - (asyncio.get_running_loop().time() - started))
        await asyncio.wait_for(proc.communicate(json.dumps(request).encode()), remaining)
        if proc.returncode != 0:
            raise RuntimeError(f"Download worker exited with code {proc.returncode}")
        return json.loads(result_path.read_text(encoding="utf-8"))
    finally:
        cancelled = await _wait_for_cleanup(spawn)
        try:
            if not spawn.cancelled() and spawn.exception() is None:
                cleanup = asyncio.create_task(stop_process_tree(spawn.result(), job))
                cancelled |= await _wait_for_cleanup(cleanup)
                cleanup.result()
        finally:
            result_path.unlink(missing_ok=True)
        if cancelled:
            raise asyncio.CancelledError


async def run_download_worker(
    service, url: str, allow_carousel: bool, *, quality: str = DEFAULT_QUALITY
) -> DownloadResult:
    validate_quality(quality)
    directory = Path(tempfile.mkdtemp(prefix="job-", dir=service.download_dir)).resolve()
    keep: set[Path] = set()
    try:
        raw = await run_worker(
            {
                "operation": "download",
                "url": url,
                "allow_carousel": allow_carousel,
                "quality": quality,
            },
            directory,
        )
        if raw.get("carousel_slides"):
            raw["carousel_slides"] = [CarouselSlide(**item) for item in raw["carousel_slides"]]
        result = DownloadResult(**raw)
        if result.success:
            keep = {
                Path(path).resolve()
                for path in service._entry_file_paths(service._result_entry(result))
            }
            if not keep or any(not p.is_relative_to(directory) or not p.is_file() for p in keep):
                keep.clear()
                raise ValueError("Worker returned invalid media paths")
        return result
    except TimeoutError:
        return DownloadResult(
            success=False, error="Download deadline exceeded", error_code="downloader.error.timeout"
        )
    except Exception:
        logger.exception("Download worker failed")
        return DownloadResult(
            success=False,
            error="Download worker failed",
            error_code="downloader.error.worker_failed",
        )
    finally:
        # This directory is created by us for exactly one worker. It cannot contain
        # another job's cached media, even after cancellation or partial downloads.
        if keep:
            for path in directory.rglob("*"):
                if path.is_file() and path.resolve() not in keep:
                    path.unlink(missing_ok=True)
        else:
            shutil.rmtree(directory)


async def run_search_worker(directory: Path, query: str, count: int):
    with tempfile.TemporaryDirectory(prefix="job-search-", dir=directory) as work:
        return await run_worker(
            {"operation": "search", "query": query, "count": count},
            Path(work),
            timeout=min(DOWNLOAD_TIMEOUT, 20),
        )


async def _main(result_path: Path) -> None:
    request = json.loads(sys.stdin.buffer.read())
    if request["operation"] == "search":
        from src.services.youtube_search import _search_shorts_sync

        result = [asdict(item) for item in _search_shorts_sync(request["query"], request["count"])]
    else:
        from src.services.downloader import VideoDownloader

        service = VideoDownloader(str(result_path.parent))
        media = await service._download_source(
            request["url"],
            request["allow_carousel"],
            quality=request.get("quality", DEFAULT_QUALITY),
        )
        # FFmpeg stays inside the isolated worker and its hard process-tree deadline.
        if request["allow_carousel"]:
            service._prepare_video_previews(media)
        result = asdict(media)
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
