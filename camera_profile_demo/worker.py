from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any

from camera_profile_demo.profiling import ProfileResult, build_fallback_profile
from camera_profile_demo.store import VisitorStore


logger = logging.getLogger(__name__)
_STOP = object()


class ProfileWorker:
    def __init__(self, store: VisitorStore, profiler: Any) -> None:
        self.store = store
        self.profiler = profiler
        self._queue: queue.Queue[object] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._queued: set[str] = set()
        self._busy = False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def busy(self) -> bool:
        with self._state_lock:
            return self._busy

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def start(self, *, resume_pending: bool = False) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="camera-profile-worker",
            daemon=True,
        )
        self._thread.start()
        if resume_pending:
            for record in reversed(self.store.list_recent()):
                if record.profile_status not in {"pending", "running"}:
                    continue
                if Path(record.capture_path).is_file():
                    self.store.update(record.visitor_id, profile_status="pending")
                    self.enqueue(record.visitor_id)
                else:
                    self.store.update(
                        record.visitor_id,
                        profile_status="fallback",
                        profile_source="mock_fallback",
                        profile=build_fallback_profile(record.visitor_id),
                        last_error="profile_failed",
                    )

    def enqueue(self, visitor_id: str) -> None:
        if self.store.get(visitor_id) is None:
            raise KeyError(visitor_id)
        with self._state_lock:
            if visitor_id in self._queued:
                return
            self._queued.add(visitor_id)
            self._queue.put(visitor_id)

    def stop(self, timeout: float = 5.0) -> None:
        if not self._thread:
            return
        self._stop.set()
        self._queue.put(_STOP)
        self._thread.join(timeout=timeout)
        if not self._thread.is_alive():
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                self._process(str(item))
            finally:
                if item is not _STOP:
                    with self._state_lock:
                        self._queued.discard(str(item))
                self._queue.task_done()

    def _process(self, visitor_id: str) -> None:
        record = self.store.get(visitor_id)
        if record is None:
            return
        with self._state_lock:
            self._busy = True
        try:
            self.store.update(visitor_id, profile_status="running", last_error=None)
            try:
                result: ProfileResult = self.profiler.analyze(
                    record.capture_path,
                    {
                        "visitor_id": visitor_id,
                        "camera_id": record.camera_id,
                        "track_id": record.track_id,
                    },
                )
            except Exception:
                logger.exception("Unexpected profile worker failure for %s", visitor_id)
                result = ProfileResult(
                    profile=build_fallback_profile(visitor_id),
                    source="mock_fallback",
                    status="fallback",
                    error_code="profile_failed",
                )
            self.store.update(
                visitor_id,
                profile_status=result.status,
                profile_source=result.source,
                profile=result.profile,
                last_error=result.error_code,
            )
        finally:
            with self._state_lock:
                self._busy = False
