# SPDX-License-Identifier: AGPL-3.0-only
"""In-process scheduler: interval jobs on one daemon thread.

Jobs run one at a time, in registration order. A job is a no-argument callable;
callers close over their settings. The first run of a job is its registration
time plus one interval (or an explicit first-run delay), and each later run is
one interval after the previous run finished.

``start`` takes an exclusive lock on ``scheduler.lock`` in the data directory
(``fcntl.flock``, POSIX only). A second process on the same data directory
cannot take it, so it runs no jobs. The lock is released by ``stop`` or when the
process exits.
"""

import fcntl
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

LOCK_FILE_NAME = "scheduler.lock"
TICK_SECONDS = 1.0

logger = logging.getLogger("larder.scheduler")


@dataclass
class _Job:
    name: str
    interval: float
    func: Callable[[], object]
    next_run: float


class Scheduler:
    """Run registered interval jobs on a background thread."""

    def __init__(
        self, data_dir: Path, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._lock_path = Path(data_dir) / LOCK_FILE_NAME
        self._clock = clock
        self._jobs: list[_Job] = []
        self._jobs_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._lock_file: IO[str] | None = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        """True while the scheduler thread is alive."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def register(
        self,
        name: str,
        interval_seconds: float,
        func: Callable[[], object],
        *,
        first_run_delay: float | None = None,
    ) -> None:
        """Add a job, optionally due after a nonnegative first-run delay.

        Raises ValueError for a duplicate name, interval <= 0 or delay < 0.
        """
        if interval_seconds <= 0:
            raise ValueError(f"interval for job '{name}' must be positive")
        if first_run_delay is not None and first_run_delay < 0:
            raise ValueError(f"first-run delay for job '{name}' must be nonnegative")
        delay = interval_seconds if first_run_delay is None else first_run_delay
        with self._jobs_lock:
            if any(job.name == name for job in self._jobs):
                raise ValueError(f"job '{name}' is already registered")
            self._jobs.append(_Job(name, interval_seconds, func, self._clock() + delay))

    def run_due(self) -> None:
        """Run each due job once, in registration order, one at a time.

        A job that raises is logged and does not stop the others. Its next run
        is one interval after it finishes, failed or not.
        """
        with self._jobs_lock:
            jobs = list(self._jobs)
        for job in jobs:
            if self._clock() < job.next_run:
                continue
            try:
                job.func()
            except Exception:
                logger.exception("scheduled job '%s' failed", job.name)
            job.next_run = self._clock() + job.interval

    def start(self) -> None:
        """Take the lock and start the thread; do nothing if already started.

        If another process holds the lock, log once and return without a thread.
        """
        with self._state_lock:
            if self._thread is not None:
                return
            lock_file = self._acquire_lock()
            if lock_file is None:
                return
            self._lock_file = lock_file
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._loop,
                args=(self._stop_event,),
                name="larder-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the thread, wait for it and release the lock."""
        with self._state_lock:
            thread, lock_file = self._thread, self._lock_file
            self._thread = None
            self._lock_file = None
            self._stop_event.set()
            if thread is not None:
                thread.join()
            if lock_file is not None:
                lock_file.close()  # closing the descriptor releases the flock

    def _acquire_lock(self) -> IO[str] | None:
        # Held open until stop(), so a context manager does not fit.
        lock_file = open(self._lock_path, "a", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            logger.warning(
                "another process holds %s; this process runs no scheduled jobs",
                self._lock_path,
            )
            return None
        return lock_file

    def _loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self.run_due()
            stop_event.wait(TICK_SECONDS)
