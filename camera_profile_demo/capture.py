from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np


logger = logging.getLogger(__name__)


def save_real_crop(
    event: Any,
    frame: np.ndarray,
    captures_dir: str | Path,
) -> Path | None:
    if not isinstance(frame, np.ndarray) or frame.ndim < 2:
        return None
    try:
        if len(event.bbox) != 4:
            return None
        x1, y1, x2, y2 = (int(value) for value in event.bbox)
    except (AttributeError, TypeError, ValueError):
        return None
    height, width = frame.shape[:2]
    x1 = max(0, min(width, x1))
    y1 = max(0, min(height, y1))
    x2 = max(0, min(width, x2))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2].copy()
    directory = Path(captures_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.jpg"
    try:
        if not cv2.imwrite(str(path), crop):
            path.unlink(missing_ok=True)
            return None
    except Exception:
        logger.exception("Failed to save real camera crop")
        path.unlink(missing_ok=True)
        return None
    return path.resolve()


class CameraCaptureLoop:
    def __init__(
        self,
        *,
        source: str | int,
        pipeline: Any,
        capture_factory: Callable[[str | int], Any] | None = None,
        reconnect_delay_seconds: float = 2.0,
    ) -> None:
        self.source = source
        self.pipeline = pipeline
        self.capture_factory = capture_factory or cv2.VideoCapture
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.connected = False
        self.last_error: str | None = None
        self.last_capture_released = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture: Any | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="camera-profile-capture",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            capture = self._capture
        if capture is not None:
            self._release(capture)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
            if not self._thread.is_alive():
                self._thread = None
        self.connected = False

    def _release(self, capture: Any) -> None:
        try:
            capture.release()
        finally:
            self.last_capture_released = True
            with self._lock:
                if self._capture is capture:
                    self._capture = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                capture = self.capture_factory(self.source)
            except Exception as error:
                self.connected = False
                self.last_error = f"camera_open_failed:{type(error).__name__}"
                self._stop.wait(self.reconnect_delay_seconds)
                continue
            with self._lock:
                self._capture = capture
            if not capture.isOpened():
                self.connected = False
                self.last_error = "camera_open_failed"
                self._release(capture)
                self._stop.wait(self.reconnect_delay_seconds)
                continue
            self.connected = True
            self.last_error = None
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    self.connected = False
                    self.last_error = "camera_read_failed"
                    break
                try:
                    self.pipeline.process_frame(frame)
                except Exception as error:
                    self.last_error = f"pipeline_failed:{type(error).__name__}"
                    logger.exception("Demo pipeline frame processing failed")
            self._release(capture)
            if not self._stop.is_set():
                self._stop.wait(self.reconnect_delay_seconds)
        self.connected = False
