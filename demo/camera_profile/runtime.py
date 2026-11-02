from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2

from demo.camera_profile.capture import CameraCaptureLoop, save_real_crop
from demo.camera_profile.models import DemoStatus, VisitorRecord, build_mock_basics
from demo.camera_profile.store import VisitorStore


logger = logging.getLogger(__name__)


class DemoRuntime:
    def __init__(
        self,
        *,
        store: VisitorStore,
        worker: Any,
        source: str | int,
        camera_id: str,
        captures_dir: str | Path,
        model_path: str,
        yolo_device: str,
        conf_threshold: float,
        capture_cooldown_seconds: float = 3.0,
        ollama_model: str = "qwen2.5vl:3b",
        pipeline_factory: Callable[..., Any] | None = None,
        capture_factory: Callable[[str | int], Any] | None = None,
    ) -> None:
        if capture_cooldown_seconds < 0:
            raise ValueError("capture_cooldown_seconds must be non-negative")
        if pipeline_factory is None:
            from pipelines.legacy.realtime_entry_pipeline import create_realtime_pipeline

            pipeline_factory = create_realtime_pipeline
        self.store = store
        self.worker = worker
        self.source = source
        self.camera_id = camera_id
        self.captures_dir = Path(captures_dir)
        self.ollama_model = ollama_model
        self.capture_cooldown_seconds = capture_cooldown_seconds
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._capture_gate_lock = threading.Lock()
        self._last_capture_at: datetime | None = None
        self.pipeline = pipeline_factory(
            model_path=model_path,
            device=yolo_device,
            conf_threshold=conf_threshold,
            face_recognizer=None,
            stability_threshold=3,
            process_every_seconds=0.3,
            camera_id=camera_id,
            identity_mode="device_api",
            on_stable_track_frame=self._on_stable_frame,
        )
        self.capture = CameraCaptureLoop(
            source=source,
            pipeline=self.pipeline,
            capture_factory=capture_factory or cv2.VideoCapture,
            reconnect_delay_seconds=2.0,
        )

    def start(self) -> None:
        self.store.load()
        self.store.cleanup_unreferenced_captures()
        self.worker.start(resume_pending=True)
        self.capture.start()

    def stop(self) -> None:
        self.capture.stop(timeout=5)
        self.worker.stop(timeout=5)

    def status(self) -> DemoStatus:
        return DemoStatus(
            camera_connected=self.capture.connected,
            capture_running=self.capture.running,
            worker_running=self.worker.running,
            worker_busy=self.worker.busy,
            queue_depth=self.worker.queue_depth,
            last_camera_error=self.capture.last_error,
            ollama_model=self.ollama_model,
        )

    def _next_visitor_id(self, captured_at: datetime) -> str:
        with self._sequence_lock:
            self._sequence = (self._sequence % 999) + 1
            sequence = self._sequence
        return f"visitor-{captured_at.strftime('%Y%m%d-%H%M%S')}-{sequence:03d}"

    def _on_stable_frame(self, event: Any, frame: Any) -> None:
        captured_at = (
            event.confirmed_at
            if isinstance(getattr(event, "confirmed_at", None), datetime)
            else datetime.now()
        )
        with self._capture_gate_lock:
            if self._last_capture_at is not None:
                elapsed = (captured_at - self._last_capture_at).total_seconds()
                if elapsed < self.capture_cooldown_seconds:
                    return
            path = save_real_crop(event, frame, self.captures_dir)
            if path is None:
                logger.warning("Skipped stable track without a valid crop")
                return
            visitor_id = self._next_visitor_id(captured_at)
            record = VisitorRecord(
                visitor_id=visitor_id,
                track_id=int(event.track_id),
                camera_id=str(event.camera_id),
                captured_at=captured_at,
                capture_url=f"/captures/{path.name}",
                capture_path=str(path),
                detection_confidence=float(event.detection_confidence),
                basics=build_mock_basics(visitor_id),
            )
            try:
                self.store.add(record)
                self.worker.enqueue(visitor_id)
                self._last_capture_at = captured_at
            except Exception:
                path.unlink(missing_ok=True)
                logger.exception("Failed to create demo visitor %s", visitor_id)
