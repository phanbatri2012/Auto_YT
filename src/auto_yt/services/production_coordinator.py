"""Single-thread coordinator for durable production jobs."""

from __future__ import annotations

import threading
import sqlite3
from collections.abc import Callable
from pathlib import Path

from auto_yt.services import database as db


JobHandler = Callable[[dict], None]
ErrorHandler = Callable[[dict, Exception], None]


class ProductionCoordinator:
    def __init__(
        self,
        handlers: dict[str, JobHandler],
        *,
        error_handler: ErrorHandler,
    ) -> None:
        self._handlers = dict(handlers)
        self._job_types = tuple(handlers)
        self._error_handler = error_handler
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._current_job_id = ""

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake_event.set()
                return
            self._stop_event.clear()
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="production-coordinator",
            )
            self._thread.start()
        if not self._ready_event.wait(timeout=5):
            raise RuntimeError("Production coordinator không khởi động kịp thời.")

    def stop(self, timeout: float = 10) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._state_lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def wake(self) -> None:
        self.start()
        self._wake_event.set()

    def status(self) -> dict:
        with self._state_lock:
            thread = self._thread
            current_job_id = self._current_job_id
        running = bool(thread and thread.is_alive() and self._ready_event.is_set())
        return {
            "running": running,
            "ready": running and not self._stop_event.is_set(),
            "current_job_id": current_job_id,
            "registered_job_types": list(self._job_types),
        }

    def _next_wait_seconds(self) -> float | None:
        try:
            delays = [
                delay
                for job_type in self._job_types
                if (delay := db.get_next_system_job_retry_delay(job_type))
                is not None
            ]
        except sqlite3.OperationalError:
            return 0.25
        return max(0.05, min(delays)) if delays else None

    def _claim_next(self) -> dict | None:
        for job_type in self._job_types:
            job = db.claim_next_system_job(job_type)
            if job is not None:
                return job
        return None

    def _run(self) -> None:
        owned_database_path = str(Path(db.DB_PATH).resolve())
        self._ready_event.set()
        try:
            while not self._stop_event.is_set():
                if str(Path(db.DB_PATH).resolve()) != owned_database_path:
                    self._wake_event.wait(timeout=0.25)
                    self._wake_event.clear()
                    continue
                try:
                    job = self._claim_next()
                except sqlite3.OperationalError:
                    self._wake_event.wait(timeout=0.25)
                    self._wake_event.clear()
                    continue
                if job is None:
                    self._wake_event.wait(timeout=self._next_wait_seconds())
                    self._wake_event.clear()
                    continue
                with self._state_lock:
                    self._current_job_id = str(job["id"])
                try:
                    self._handlers[str(job["job_type"])](job)
                except Exception as exc:
                    self._error_handler(job, exc)
                finally:
                    with self._state_lock:
                        self._current_job_id = ""
        finally:
            self._ready_event.clear()
