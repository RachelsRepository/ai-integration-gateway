"""Scheduled worker runner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ai_gateway.observability.logging import get_logger

logger = get_logger(__name__)

Job = Callable[[], Awaitable[int]]


class WorkerRunner:
    """Runs background jobs on fixed intervals until cancelled."""

    def __init__(self) -> None:
        """Initialise an empty runner."""
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()

    def schedule(self, name: str, job: Job, *, interval_seconds: float) -> None:
        """Schedule a job.

        Args:
            name: Job name for logs.
            job: Async callable returning a processed count.
            interval_seconds: Delay between runs.
        """
        self._tasks.append(asyncio.create_task(self._loop(name, job, interval_seconds)))

    async def start(self) -> None:
        """No-op start hook for interface symmetry."""

    async def stop(self) -> None:
        """Cancel every scheduled job."""
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, name: str, job: Job, interval: float) -> None:
        while not self._stopping.is_set():
            try:
                processed = await job()
                logger.info("worker_tick", worker=name, processed=processed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("worker_failed", worker=name, error=str(exc))
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
            except TimeoutError:
                continue


__all__ = ["WorkerRunner"]
