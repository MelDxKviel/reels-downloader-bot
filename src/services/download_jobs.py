"""Bounded admission for expensive downloads, searches and conversions."""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TypeVar

from src.config import MAX_CONCURRENT_JOBS, MAX_QUEUED_JOBS, MAX_USER_JOBS

T = TypeVar("T")


class JobQueueFull(Exception):
    """The global queue or this user's outstanding-job limit is reached."""


class DownloadJobs:
    def __init__(self, workers=MAX_CONCURRENT_JOBS, queued=MAX_QUEUED_JOBS, per_user=MAX_USER_JOBS):
        self.workers = workers
        self.capacity = workers + queued
        self.per_user = per_user
        self.pending = 0
        self._users = Counter()
        self._locks = {}
        self._slots = asyncio.Semaphore(workers)

    async def run(self, user_id: int | None, operation: Callable[[], Awaitable[T]]) -> T:
        if self.pending >= self.capacity or self._users[user_id] >= self.per_user:
            raise JobQueueFull
        self.pending += 1
        self._users[user_id] += 1
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        try:
            # A user waiting for their previous job must not occupy a global slot.
            async with lock:
                async with self._slots:
                    return await operation()
        finally:
            self.pending -= 1
            self._users[user_id] -= 1
            if not self._users[user_id]:
                del self._users[user_id]
                del self._locks[user_id]


jobs = DownloadJobs()
