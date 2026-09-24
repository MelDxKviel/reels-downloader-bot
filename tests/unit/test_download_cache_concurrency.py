"""Deterministic concurrency checks without yt-dlp, FFmpeg or network requests."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module

import pytest

from src.services.download_jobs import DownloadJobs, JobQueueFull
from src.services.downloader import CarouselSlide, DownloadResult, VideoDownloader
from src.services.url_utils import get_url_hash

URL = "https://youtube.com/watch?v=concurrency"


@pytest.mark.parametrize("clear_after_first_publish", [False, True])
@pytest.mark.asyncio
async def test_concurrent_downloads_share_worker_and_keep_both_delivery_leases(
    tmp_path, monkeypatch, clear_after_first_publish
):
    started = asyncio.Event()
    finish = asyncio.Event()
    second_entered = asyncio.Event()
    calls = []
    video = tmp_path / "video.mp4"
    photo = tmp_path / "photo.jpg"

    async def runner(downloader, url, allow_carousel):
        calls.append((url, allow_carousel))
        started.set()
        await finish.wait()
        video.write_bytes(b"video")
        photo.write_bytes(b"photo")
        return DownloadResult(
            success=True,
            file_path=str(video),
            carousel_slides=[
                CarouselSlide(
                    "https://cdn.example/video.mp4", is_video=True, local_path=str(video)
                ),
                CarouselSlide("https://cdn.example/photo.jpg", local_path=str(photo)),
            ],
        )

    monkeypatch.setattr(import_module("src.services.downloader"), "jobs", DownloadJobs())
    downloader = VideoDownloader(str(tmp_path), worker_runner=runner)
    original_add = downloader.add_to_cache
    published = 0

    def publish(url, result):
        nonlocal published
        original_add(url, result)
        published += 1
        if clear_after_first_publish and published == 1:
            # The first delivery owns a lease; the second subscriber still has
            # to publish its copy of the shared worker result.
            downloader.clear_cache()

    monkeypatch.setattr(downloader, "add_to_cache", publish)
    first = asyncio.create_task(downloader.download(URL, user_id=1, reserve=True))

    async def second_request():
        second_entered.set()
        return await downloader.download(URL, user_id=2, reserve=True)

    second = None
    try:
        async with asyncio.timeout(5):
            await started.wait()
            second = asyncio.create_task(second_request())
            # second_request runs through admission before yielding to its worker.
            await second_entered.wait()
            assert calls == [(URL, True)]
            finish.set()
            one, two = await asyncio.gather(first, second)
        assert one is not two
        assert one.file_path == two.file_path == str(video)
        assert calls == [(URL, True)]
        downloader.cache[get_url_hash(URL)]["cached_at"] = 0

        assert downloader.cleanup_expired(1) == (0, 0)
        assert video.exists() and photo.exists()
        downloader.release_result(one)
        assert downloader.cleanup_expired(1) == (0, 0)
        assert video.exists() and photo.exists()
        downloader.release_result(two)
        assert downloader.cleanup_expired(1) == (1, 2)
        assert not video.exists() and not photo.exists()
    finally:
        finish.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first, second) if task is not None), return_exceptions=True
        )


def test_clear_cache_defers_physical_deletion_until_last_delivery_releases(tmp_path):
    downloader = VideoDownloader(str(tmp_path))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    downloader.add_to_cache(URL, DownloadResult(success=True, file_path=str(video)))
    one = downloader.get_from_cache(URL, reserve=True)
    two = downloader.get_from_cache(URL, reserve=True)
    assert one is not None and two is not None

    assert downloader.clear_cache() == 0
    assert downloader.get_from_cache(URL) is None
    assert json.loads(downloader.cache_file.read_text(encoding="utf-8")) == {}
    assert video.exists()
    downloader.release_result(one)
    assert video.exists()
    downloader.release_result(two)
    assert not video.exists()
    downloader.release_result(two)  # A finally block may release an already released result.


def test_republishing_cleared_result_cancels_deferred_deletion_for_all_slides(tmp_path):
    downloader = VideoDownloader(str(tmp_path))
    video = tmp_path / "video.mp4"
    photo = tmp_path / "photo.jpg"
    video.write_bytes(b"video")
    photo.write_bytes(b"photo")
    downloader.add_to_cache(
        URL,
        DownloadResult(
            success=True,
            file_path=str(video),
            carousel_slides=[
                CarouselSlide(
                    "https://cdn.example/video.mp4", is_video=True, local_path=str(video)
                ),
                CarouselSlide("https://cdn.example/photo.jpg", local_path=str(photo)),
            ],
        ),
    )
    one = downloader.get_from_cache(URL, reserve=True)
    two = downloader.get_from_cache(URL, reserve=True)
    assert one is not None and two is not None
    downloader.clear_cache()

    # An in-flight subscriber can republish the same files after clear_cache.
    # The new cache ownership supersedes deletion queued by the old snapshot.
    downloader.add_to_cache(URL, two)
    downloader.release_result(one)
    assert video.exists() and photo.exists()
    downloader.release_result(two)
    assert video.exists() and photo.exists()
    assert downloader.get_from_cache(URL) is not None

    downloader.cache[get_url_hash(URL)]["cached_at"] = 0
    assert downloader.cleanup_expired(1) == (1, 2)
    assert not video.exists() and not photo.exists()


def test_expired_cleanup_and_replacement_serialize_without_evicting_fresh_entry(
    tmp_path, monkeypatch
):
    downloader = VideoDownloader(str(tmp_path))
    old = tmp_path / "old.mp4"
    fresh = tmp_path / "fresh.mp4"
    old.write_bytes(b"old")
    fresh.write_bytes(b"fresh")
    downloader.add_to_cache(URL, DownloadResult(success=True, file_path=str(old)))
    downloader.cache[get_url_hash(URL)]["cached_at"] = 0

    cleanup_examining_old = threading.Event()
    allow_cleanup = threading.Event()
    replacement_attempted_lock = threading.Event()
    replacement_acquired_lock = threading.Event()
    replacement_thread = {}
    original_lock = downloader._cache_lock
    original_age = downloader._entry_age_seconds

    class ObservedLock:
        def __enter__(self):
            replacing = threading.get_ident() == replacement_thread.get("ident")
            if replacing:
                replacement_attempted_lock.set()
            original_lock.acquire()
            if replacing:
                replacement_acquired_lock.set()
            return self

        def __exit__(self, *exc):
            original_lock.release()

    def paused_age(entry, now):
        cleanup_examining_old.set()
        assert allow_cleanup.wait(5), "Cleanup was not allowed to finish"
        return original_age(entry, now)

    def replace_entry():
        replacement_thread["ident"] = threading.get_ident()
        downloader.add_to_cache(URL, DownloadResult(success=True, file_path=str(fresh)))

    monkeypatch.setattr(downloader, "_cache_lock", ObservedLock())
    monkeypatch.setattr(downloader, "_entry_age_seconds", paused_age)
    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(downloader.cleanup_expired, 1)
        try:
            assert cleanup_examining_old.wait(5)
            replacement = executor.submit(replace_entry)
            assert replacement_attempted_lock.wait(5)
            assert not replacement_acquired_lock.is_set()
            allow_cleanup.set()
            assert cleanup.result(timeout=5) == (1, 1)
            replacement.result(timeout=5)
        finally:
            allow_cleanup.set()

    cached = downloader.get_from_cache(URL)
    assert cached is not None and cached.file_path == str(fresh)
    assert fresh.exists() and not old.exists()


def test_failed_atomic_replace_preserves_previous_cache_snapshot(tmp_path, monkeypatch):
    downloader = VideoDownloader(str(tmp_path))
    downloader.set_telegram_mp3_file_id(URL, "audio-before")
    previous = downloader.cache_file.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated atomic rename failure")

    with monkeypatch.context() as context:
        context.setattr(import_module("src.services.download_cache").os, "replace", fail_replace)
        downloader.set_telegram_mp3_file_id(URL, "audio-after")

    assert downloader.cache_file.read_bytes() == previous
    assert list(tmp_path.glob(".cache-*.tmp")) == []
    restarted = VideoDownloader(str(tmp_path))
    assert restarted.get_telegram_mp3_file_id(URL) == "audio-before"


async def _submit(jobs, user_id, operation):
    entered = asyncio.Event()

    async def request():
        entered.set()
        return await jobs.run(user_id, operation)

    task = asyncio.create_task(request())
    await entered.wait()
    return task


@pytest.mark.asyncio
async def test_per_user_waiter_does_not_occupy_global_worker_and_cancellation_frees_limits():
    jobs = DownloadJobs(workers=2, queued=3, per_user=2)
    finish = asyncio.Event()
    started = {
        name: asyncio.Event() for name in ("first", "same-user", "other-user", "replacement")
    }
    active = set()
    peak = 0

    async def operation(name):
        nonlocal peak
        active.add(name)
        peak = max(peak, len(active))
        started[name].set()
        try:
            await finish.wait()
            return name
        finally:
            active.remove(name)

    tasks = []
    try:
        async with asyncio.timeout(5):
            first = await _submit(jobs, 1, lambda: operation("first"))
            tasks.append(first)
            await started["first"].wait()
            same = await _submit(jobs, 1, lambda: operation("same-user"))
            tasks.append(same)
            assert jobs.pending == 2
            assert not started["same-user"].is_set()
            with pytest.raises(JobQueueFull):
                await jobs.run(1, lambda: operation("same-user"))

            other = await _submit(jobs, 2, lambda: operation("other-user"))
            tasks.append(other)
            await started["other-user"].wait()
            assert active == {"first", "other-user"}

            same.cancel()
            with pytest.raises(asyncio.CancelledError):
                await same
            assert jobs.pending == 2
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert jobs.pending == 1

            replacement = await _submit(jobs, 1, lambda: operation("replacement"))
            tasks.append(replacement)
            await started["replacement"].wait()
            finish.set()
            assert await replacement == "replacement"
            assert await other == "other-user"
        assert peak == 2
        assert jobs.pending == 0
        assert not active
    finally:
        finish.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_global_queue_is_bounded_and_cancellation_releases_running_and_queued_slots():
    jobs = DownloadJobs(workers=1, queued=1, per_user=3)
    running = asyncio.Event()
    finish = asyncio.Event()
    cleaned_up = asyncio.Event()
    queued_started = False

    async def blocking():
        running.set()
        try:
            await finish.wait()
        finally:
            cleaned_up.set()

    async def queued_operation():
        nonlocal queued_started
        queued_started = True

    tasks = []
    try:
        async with asyncio.timeout(5):
            first = await _submit(jobs, 1, blocking)
            tasks.append(first)
            await running.wait()
            queued = await _submit(jobs, 2, queued_operation)
            tasks.append(queued)
            assert jobs.pending == 2
            with pytest.raises(JobQueueFull):
                await jobs.run(3, queued_operation)
            assert not queued_started

            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert jobs.pending == 1
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert cleaned_up.is_set()
            assert jobs.pending == 0
            await jobs.run(3, queued_operation)
            assert queued_started
            assert jobs.pending == 0
    finally:
        finish.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
