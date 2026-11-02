"""Long-running RTSP worker for the isolated Demo greeting trigger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Callable, Optional

import cv2

from agent.behavior.behavior_vision import (
    GroundPlaneProjector,
    load_behavior_camera_configs,
)

from .state import DemoGreetingCandidate, DemoGreetingStateMachine
from .vision import DemoVisionPipeline


logger = logging.getLogger(__name__)


def _project_path(value: str, project_root: Path) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else project_root / path)


def _default_device() -> str:
    configured = os.getenv("DEMO_GREETING_DEVICE") or os.getenv("BEHAVIOR_DEVICE")
    if configured:
        return configured
    try:
        import torch

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _ground_calibration(
    path: str,
    camera_id: str,
) -> tuple[
    Optional[GroundPlaneProjector],
    Optional[tuple[int, int]],
    str,
]:
    try:
        cameras = load_behavior_camera_configs(path)
    except (OSError, ValueError) as exc:
        logger.warning("Demo ground calibration is unavailable: %s", exc)
        return None, None, "unavailable"
    camera = next(
        (item for item in cameras if item.camera_id == camera_id),
        None,
    )
    if camera is None or camera.ground_projector is None:
        return None, None, "unavailable"
    return (
        camera.ground_projector,
        camera.calibration_image_size,
        camera.calibration_status or "unavailable",
    )


@dataclass(frozen=True)
class DemoGreetingConfig:
    source: str
    camera_id: str
    store_id: str
    person_model_path: str
    vest_model_path: str
    device: str
    person_confidence: float = 0.35
    vest_confidence: float = 0.45
    ground_projector: Optional[GroundPlaneProjector] = None
    calibration_image_size: Optional[tuple[int, int]] = None
    calibration_status: str = "unavailable"
    co_presence_seconds: float = 0.5
    staff_motion_pixels: float = 15.0
    rearm_absence_seconds: float = 1.5
    frame_interval_seconds: float = 0.0

    @classmethod
    def from_environment(cls) -> "DemoGreetingConfig":
        project_root = Path(__file__).resolve().parents[2]
        source = os.getenv("DEMO_GREETING_VIDEO_SOURCE") or os.getenv("VIDEO_SOURCE", "")
        camera_id = os.getenv("DEMO_GREETING_CAMERA_ID", "entrance-1").strip()
        camera_config_path = _project_path(
            os.getenv(
                "DEMO_GREETING_CAMERA_CONFIG",
                "configs/examples/behavior_cameras.example.json",
            ),
            project_root,
        )
        projector, calibration_size, calibration_status = _ground_calibration(
            camera_config_path,
            camera_id,
        )
        return cls(
            source=source.strip(),
            camera_id=camera_id,
            store_id=os.getenv("DEMO_GREETING_STORE_ID", "store-1").strip(),
            person_model_path=_project_path(
                os.getenv("DEMO_GREETING_PERSON_MODEL", "data/models/yolo11n.pt"),
                project_root,
            ),
            vest_model_path=_project_path(
                os.getenv(
                    "DEMO_GREETING_VEST_MODEL",
                    "data/models/staff_vest.pt",
                ),
                project_root,
            ),
            device=_default_device(),
            person_confidence=float(os.getenv("DEMO_GREETING_PERSON_CONFIDENCE", "0.35")),
            vest_confidence=float(os.getenv("DEMO_GREETING_VEST_CONFIDENCE", "0.45")),
            ground_projector=projector,
            calibration_image_size=calibration_size,
            calibration_status=calibration_status,
            co_presence_seconds=float(os.getenv("DEMO_GREETING_CO_PRESENCE_SECONDS", "0.5")),
            staff_motion_pixels=float(os.getenv("DEMO_GREETING_STAFF_MOTION_PIXELS", "15")),
            rearm_absence_seconds=float(os.getenv("DEMO_GREETING_REARM_SECONDS", "1.5")),
            frame_interval_seconds=float(os.getenv("DEMO_GREETING_FRAME_INTERVAL_SECONDS", "0")),
        )

    def validate(self) -> None:
        if not self.source:
            raise ValueError("DEMO_GREETING_VIDEO_SOURCE or VIDEO_SOURCE is required")
        if not self.camera_id or not self.store_id:
            raise ValueError("DEMO_GREETING_CAMERA_ID and DEMO_GREETING_STORE_ID are required")
        for label, path in (("person model", self.person_model_path), ("vest model", self.vest_model_path)):
            if not Path(path).is_file():
                raise ValueError(f"{label} file does not exist: {path}")
        if self.co_presence_seconds <= 0 or self.staff_motion_pixels <= 0:
            raise ValueError("Demo greeting thresholds must be positive")
        if self.rearm_absence_seconds <= 0 or self.frame_interval_seconds < 0:
            raise ValueError("Demo greeting intervals are invalid")


@dataclass
class _RuntimeState:
    status: str = "idle"
    session_id: Optional[str] = None
    started_at: Optional[datetime] = None
    last_frame_at: Optional[datetime] = None
    last_error: Optional[str] = None
    session_event_count: int = 0
    total_event_count: int = 0
    person_count: int = 0
    staff_count: int = 0
    vest_count: int = 0
    last_event: Optional[dict[str, object]] = None


class DemoGreetingRuntime:
    """Owns the RTSP worker and durable writes for a single Demo session."""

    def __init__(
        self,
        config: DemoGreetingConfig,
        *,
        vision_factory: Callable[[DemoGreetingConfig], DemoVisionPipeline] = lambda value: DemoVisionPipeline(
            person_model_path=value.person_model_path,
            vest_model_path=value.vest_model_path,
            device=value.device,
            person_confidence=value.person_confidence,
            vest_confidence=value.vest_confidence,
            ground_projector=value.ground_projector,
            calibration_image_size=value.calibration_image_size,
        ),
        event_recorder: Optional[Callable[[DemoGreetingCandidate], object]] = None,
        capture_factory: Callable[[str], object] = cv2.VideoCapture,
    ) -> None:
        self.config = config
        self._vision_factory = vision_factory
        self._event_recorder = event_recorder or self._record_behavior_event
        self._capture_factory = capture_factory
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._vision: Optional[DemoVisionPipeline] = None
        self._machine: Optional[DemoGreetingStateMachine] = None
        self._state = _RuntimeState()

    def start(self) -> dict[str, object]:
        self.config.validate()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._reset_locked()
                return self.status()
            self._start_locked()
            thread = threading.Thread(
                target=self._run,
                name="demo-greeting-rtsp",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return self.status()

    def reset(self) -> dict[str, object]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                raise RuntimeError("Demo greeting worker is not running")
            self._reset_locked()
            return self.status()

    def stop(self) -> dict[str, object]:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._state.status = "stopped" if self._state.session_id else "idle"
                return self.status()
            self._state.status = "stopping"
            self._stop_event.set()
        thread.join(timeout=3.0)
        with self._lock:
            if not thread.is_alive():
                self._state.status = "stopped"
            return self.status()

    def status(self) -> dict[str, object]:
        with self._lock:
            state = self._state
            return {
                "status": state.status,
                "running": state.status in {"starting", "running", "stopping"},
                "session_id": state.session_id,
                "camera_id": self.config.camera_id,
                "store_id": self.config.store_id,
                "source": self._safe_source(),
                "device": self.config.device,
                "started_at": self._iso(state.started_at),
                "last_frame_at": self._iso(state.last_frame_at),
                "last_error": state.last_error,
                "session_event_count": state.session_event_count,
                "total_event_count": state.total_event_count,
                "person_count": state.person_count,
                "staff_count": state.staff_count,
                "vest_count": state.vest_count,
                "last_event": state.last_event,
                "rule": {
                    "co_presence_seconds": self.config.co_presence_seconds,
                    "staff_motion_pixels": self.config.staff_motion_pixels,
                    "rearm_absence_seconds": self.config.rearm_absence_seconds,
                },
                "calibration": {
                    "status": self.config.calibration_status,
                    "image_size": self.config.calibration_image_size,
                    "distance_source": (
                        "ground_homography"
                        if self.config.ground_projector is not None
                        else "unavailable"
                    ),
                },
            }

    def _start_locked(self) -> None:
        self._stop_event.clear()
        self._vision = None
        self._state = _RuntimeState(status="starting")
        self._reset_locked()

    def _reset_locked(self) -> None:
        session_id = self._new_session_id()
        self._machine = DemoGreetingStateMachine(
            session_id=session_id,
            camera_id=self.config.camera_id,
            store_id=self.config.store_id,
            co_presence_seconds=self.config.co_presence_seconds,
            staff_motion_pixels=self.config.staff_motion_pixels,
            rearm_absence_seconds=self.config.rearm_absence_seconds,
            calibration_status=self.config.calibration_status,
        )
        self._state.session_id = session_id
        self._state.started_at = datetime.now()
        self._state.session_event_count = 0
        self._state.last_event = None
        self._state.last_error = None
        if self._state.status != "starting":
            self._state.status = "running"

    def _run(self) -> None:
        capture = None
        try:
            vision = self._vision_factory(self.config)
            vision.load()
            capture = self._capture_factory(self.config.source)
            if not getattr(capture, "isOpened", lambda: False)():
                raise RuntimeError("unable to open configured RTSP source")
            with self._lock:
                self._vision = vision
                self._state.status = "running"
            read_failures = 0
            while not self._stop_event.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    read_failures += 1
                    if read_failures >= 12:
                        raise RuntimeError("RTSP frame read failed repeatedly")
                    time.sleep(0.2)
                    continue
                read_failures = 0
                observed_at = datetime.now()
                observations = vision.detect(frame)
                with self._lock:
                    machine = self._machine
                    session_id = self._state.session_id
                    self._state.last_frame_at = observed_at
                    self._state.person_count = vision.last_detection_count
                    self._state.staff_count = vision.last_staff_count
                    self._state.vest_count = vision.last_vest_count
                    candidates = machine.observe(observed_at, observations) if machine else []
                for candidate in candidates:
                    self._persist_candidate(candidate, session_id)
                if self.config.frame_interval_seconds:
                    self._stop_event.wait(self.config.frame_interval_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Demo greeting worker stopped with an error")
            with self._lock:
                self._state.last_error = str(exc)
                self._state.status = "error"
        finally:
            if capture is not None:
                release = getattr(capture, "release", None)
                if callable(release):
                    release()
            with self._lock:
                if self._state.status not in {"error", "stopped"}:
                    self._state.status = "stopped" if self._stop_event.is_set() else "idle"

    def _persist_candidate(
        self,
        candidate: DemoGreetingCandidate,
        expected_session_id: Optional[str],
    ) -> None:
        with self._lock:
            if candidate.session_id != expected_session_id or candidate.session_id != self._state.session_id:
                return
        try:
            stored = self._event_recorder(candidate)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Demo greeting candidate could not be stored")
            with self._lock:
                self._state.last_error = f"event write failed: {exc}"
            return
        with self._lock:
            if candidate.session_id != self._state.session_id:
                return
            self._state.session_event_count += 1
            self._state.total_event_count += 1
            self._state.last_event = {
                "event_id": getattr(stored, "id", None),
                "event_key": candidate.event_key,
                "occurred_at": candidate.confirmed_at.isoformat(),
                "staff_track_token": candidate.staff_track_token,
                "customer_track_token": candidate.customer_track_token,
                "staff_motion_pixels": round(candidate.staff_motion_pixels, 2),
                "pair_duration_seconds": round(candidate.pair_duration_seconds, 3),
                "evidence_status": "pending",
            }

    def _record_behavior_event(self, candidate: DemoGreetingCandidate):
        from agent.behavior.behavior_events import BehaviorEventInput, BehaviorEventStore
        from agent.db import get_session_factory, init_db

        init_db()
        store = BehaviorEventStore(
            get_session_factory(),
            # A blue vest is a Staff Role, never a named Staff Identity.
            greeting_mock_staff_name=None,
        )
        return store.record_behavior(
            BehaviorEventInput(
                event_key=candidate.event_key,
                event_type="staff_greeting",
                assessment="confirmed",
                camera_id=candidate.camera_id,
                customer_track_id=candidate.customer_track_id,
                customer_track_token=candidate.customer_track_token,
                staff_track_id=candidate.staff_track_id,
                staff_track_token=candidate.staff_track_token,
                started_at=candidate.started_at,
                ended_at=candidate.confirmed_at,
                confidence=candidate.confidence,
                details=candidate.details,
                is_demo=True,
            )
        )

    @staticmethod
    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value is not None else None

    def _safe_source(self) -> str:
        source = self.config.source
        if "://" not in source or "@" not in source:
            return source
        protocol, remainder = source.split("://", 1)
        credentials, location = remainder.rsplit("@", 1)
        username = credentials.split(":", 1)[0]
        return f"{protocol}://{username}:***@{location}"

    @staticmethod
    def _new_session_id() -> str:
        return "demo-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
