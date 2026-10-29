"""Production entrypoint for configured behavior-camera processing."""

from __future__ import annotations

import logging
import math
import signal
import warnings
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any, Optional

from agent.behavior_vision import load_behavior_camera_configs
from agent.config import settings
from agent.db import get_session_factory, init_db
from pipelines.behavior_runtime import BehaviorCaptureRunner, create_behavior_runtime
from pipelines.store_runtime import StoreRuntime


logger = logging.getLogger(__name__)


class _IdleRuntimeComponent:
    def start(self) -> bool:
        return True

    def stop(self) -> None:
        return None


@dataclass(frozen=True)
class ValidatedRuntimeSettings:
    cameras: tuple
    person_model_path: str
    staff_vest_model_path: Optional[str]
    pose_model_path: str
    tracker: str
    device: str
    confidence: float
    staff_vest_enabled: bool
    staff_vest_confidence: float
    role_window_frames: int
    staff_hit_frames: int
    maximum_missing_frames: int
    greeting_mock_staff_name: str
    greeting_v2_enabled: bool
    realtime_greeting_enabled: bool
    identity_association_mode: str
    identity_component_debug: bool


def _finite_confidence(configured: Any, name: str, default=None) -> float:
    confidence = getattr(configured, name, default)
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, Real)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise ValueError(f"{name} must be between 0 and 1")
    return float(confidence)


def _required_string(configured: Any, name: str) -> str:
    value = getattr(configured, name, None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _integer(configured: Any, name: str, default: int, minimum: int) -> int:
    value = getattr(configured, name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _greeting_mock_staff_name(configured: Any) -> str:
    value = getattr(configured, "BEHAVIOR_GREETING_MOCK_STAFF_NAME", "演示店员")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("BEHAVIOR_GREETING_MOCK_STAFF_NAME is required")
    normalized = value.strip()
    if len(normalized) > 128:
        raise ValueError("BEHAVIOR_GREETING_MOCK_STAFF_NAME is too long")
    return normalized

def _project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def validate_runtime_settings(
    configured: Any,
    *,
    allow_store_runtime: bool = False,
) -> ValidatedRuntimeSettings:
    identity_mode = getattr(configured, "IDENTITY_ASSOCIATION_MODE", None)
    if identity_mode not in {"new", "legacy"}:
        raise ValueError("IDENTITY_ASSOCIATION_MODE must be explicitly new or legacy")
    identity_component_debug = bool(
        getattr(configured, "IDENTITY_COMPONENT_DEBUG", False)
    )
    if identity_mode == "new" and not allow_store_runtime and not identity_component_debug:
        raise RuntimeError("new production mode must start through StoreRuntime")
    project_root = Path(
        getattr(configured, "BEHAVIOR_PROJECT_ROOT", Path(__file__).resolve().parent)
    ).resolve()
    config_path = _project_path(
        project_root, _required_string(configured, "BEHAVIOR_CONFIG_PATH")
    )
    if not config_path.is_file():
        raise FileNotFoundError(f"behavior camera config does not exist: {config_path}")
    cameras = load_behavior_camera_configs(config_path)

    legacy_path = getattr(configured, "BEHAVIOR_VEST_MODEL_PATH", None)
    person_value = getattr(configured, "BEHAVIOR_PERSON_MODEL_PATH", None)
    legacy_config = bool(getattr(configured, "BEHAVIOR_LEGACY_MODEL_CONFIG", False))
    if not isinstance(person_value, str) or not person_value.strip():
        if not isinstance(legacy_path, str) or not legacy_path.strip():
            raise ValueError("BEHAVIOR_PERSON_MODEL_PATH is required")
        legacy_config = True
        person_value = legacy_path
    if legacy_config or not hasattr(configured, "BEHAVIOR_PERSON_MODEL_PATH"):
        warnings.warn(
            "BEHAVIOR_VEST_MODEL_PATH is deprecated; configure separate "
            "BEHAVIOR_PERSON_MODEL_PATH and BEHAVIOR_STAFF_VEST_MODEL_PATH",
            DeprecationWarning,
            stacklevel=2,
        )

    staff_vest_value = getattr(configured, "BEHAVIOR_STAFF_VEST_MODEL_PATH", None)
    if not isinstance(staff_vest_value, str) or not staff_vest_value.strip():
        staff_vest_value = legacy_path
    pose_value = _required_string(configured, "BEHAVIOR_POSE_MODEL_PATH")
    tracker = _required_string(configured, "BEHAVIOR_TRACKER")
    device = _required_string(configured, "BEHAVIOR_DEVICE")
    confidence = _finite_confidence(configured, "BEHAVIOR_CONFIDENCE")
    staff_vest_confidence = _finite_confidence(
        configured, "BEHAVIOR_STAFF_VEST_CONFIDENCE", 0.68
    )
    staff_vest_enabled = getattr(
        configured, "BEHAVIOR_STAFF_VEST_ENABLED", True
    )
    if not isinstance(staff_vest_enabled, bool):
        raise ValueError("BEHAVIOR_STAFF_VEST_ENABLED must be a boolean")
    role_window_frames = _integer(
        configured, "BEHAVIOR_ROLE_WINDOW_FRAMES", 5, 1
    )
    staff_hit_frames = _integer(
        configured, "BEHAVIOR_STAFF_HIT_FRAMES", 3, 1
    )
    if staff_hit_frames > role_window_frames:
        raise ValueError("BEHAVIOR_STAFF_HIT_FRAMES cannot exceed role window")
    maximum_missing_frames = _integer(
        configured, "BEHAVIOR_MAXIMUM_MISSING_FRAMES", 8, 0
    )
    greeting_mock_staff_name = _greeting_mock_staff_name(configured)
    greeting_v2_enabled = getattr(configured, "BEHAVIOR_GREETING_V2_ENABLED", True)
    if not isinstance(greeting_v2_enabled, bool):
        raise ValueError("BEHAVIOR_GREETING_V2_ENABLED must be a boolean")
    realtime_greeting_enabled = getattr(configured, "BEHAVIOR_REALTIME_GREETING_ENABLED", False)
    if not isinstance(realtime_greeting_enabled, bool):
        raise ValueError("BEHAVIOR_REALTIME_GREETING_ENABLED must be a boolean")

    camera_vest_states = [
        staff_vest_enabled
        if camera.staff_vest_enabled is None
        else camera.staff_vest_enabled
        for camera in cameras
    ]
    for camera, vest_enabled in zip(cameras, camera_vest_states):
        if camera.entry_trigger is not None and camera.greeting_area is None:
            raise ValueError(
                f"entry camera {camera.camera_id} requires greeting_area capability"
            )
        if camera.greeting_area is not None and not vest_enabled:
            raise ValueError(
                f"greeting camera {camera.camera_id} requires staff vest detection"
            )
        if camera.greeting_area is not None and camera.ground_projector is None:
            raise ValueError(
                f"greeting camera {camera.camera_id} requires ground_homography"
            )

    person_path = _project_path(project_root, person_value)
    pose_path = _project_path(project_root, pose_value)
    for label, path in (("person model", person_path), ("pose model", pose_path)):
        if not path.is_file():
            raise FileNotFoundError(f"{label} file does not exist: {path}")

    staff_vest_path = None
    if any(camera_vest_states):
        if not isinstance(staff_vest_value, str) or not staff_vest_value.strip():
            raise ValueError("BEHAVIOR_STAFF_VEST_MODEL_PATH is required")
        staff_vest_path = _project_path(project_root, staff_vest_value)
        if not staff_vest_path.is_file():
            raise FileNotFoundError(
                f"staff vest model file does not exist: {staff_vest_path}"
            )

    return ValidatedRuntimeSettings(
        cameras=cameras,
        person_model_path=str(person_path),
        staff_vest_model_path=(
            str(staff_vest_path) if staff_vest_path is not None else None
        ),
        pose_model_path=str(pose_path),
        tracker=tracker,
        device=device,
        confidence=confidence,
        staff_vest_enabled=staff_vest_enabled,
        staff_vest_confidence=staff_vest_confidence,
        role_window_frames=role_window_frames,
        greeting_mock_staff_name=greeting_mock_staff_name,
        staff_hit_frames=staff_hit_frames,
        maximum_missing_frames=maximum_missing_frames,
        greeting_v2_enabled=greeting_v2_enabled,
        realtime_greeting_enabled=realtime_greeting_enabled,
        identity_association_mode=identity_mode,
        identity_component_debug=identity_component_debug,
    )


def build_runner(
    configured: Any = settings,
    *,
    session_factory: Optional[Any] = None,
    model_loader: Optional[Any] = None,
) -> BehaviorCaptureRunner:
    validated = validate_runtime_settings(configured)
    init_db()
    sessions = session_factory or get_session_factory()
    runtime = create_behavior_runtime(
        cameras=validated.cameras,
        session_factory=sessions,
        person_model_path=validated.person_model_path,
        staff_vest_model_path=validated.staff_vest_model_path,
        pose_model_path=validated.pose_model_path,
        device=validated.device,
        tracker=validated.tracker,
        conf_threshold=validated.confidence,
        staff_vest_enabled=validated.staff_vest_enabled,
        staff_vest_confidence_threshold=validated.staff_vest_confidence,
        role_window_frames=validated.role_window_frames,
        greeting_mock_staff_name=validated.greeting_mock_staff_name,
        staff_hit_frames=validated.staff_hit_frames,
        maximum_missing_frames=validated.maximum_missing_frames,
        greeting_v2_enabled=validated.greeting_v2_enabled,
        realtime_greeting_enabled=getattr(validated, "realtime_greeting_enabled", False),
        identity_association_mode=validated.identity_association_mode,
        model_loader=model_loader,
    )
    return BehaviorCaptureRunner(
        runtime,
        {camera.camera_id: camera.source for camera in validated.cameras},
    )


def build_legacy_store_runtime(
    configured: Any = settings,
    *,
    session_factory: Optional[Any] = None,
    model_loader: Optional[Any] = None,
    lease_service: Optional[Any] = None,
) -> StoreRuntime:
    validated = validate_runtime_settings(configured)
    if validated.identity_association_mode != "legacy":
        raise ValueError("legacy behavior StoreRuntime requires legacy mode")
    store_id = getattr(configured, "IDENTITY_ASSOCIATION_STORE_ID", None)
    owner_id = getattr(configured, "IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID", None)
    if not isinstance(store_id, str) or not store_id.strip():
        raise ValueError("IDENTITY_ASSOCIATION_STORE_ID is required")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID is required")
    foreign_stores = {
        camera.store_id for camera in validated.cameras if camera.store_id != store_id
    }
    if foreign_stores:
        raise ValueError("Behavior cameras must belong to the StoreRuntime store")

    init_db()
    sessions = session_factory or get_session_factory()

    def build_behavior(_dependencies):
        runtime = create_behavior_runtime(
            cameras=validated.cameras,
            session_factory=sessions,
            person_model_path=validated.person_model_path,
            staff_vest_model_path=validated.staff_vest_model_path,
            pose_model_path=validated.pose_model_path,
            device=validated.device,
            tracker=validated.tracker,
            conf_threshold=validated.confidence,
            staff_vest_enabled=validated.staff_vest_enabled,
            staff_vest_confidence_threshold=validated.staff_vest_confidence,
            role_window_frames=validated.role_window_frames,
            greeting_mock_staff_name=validated.greeting_mock_staff_name,
            staff_hit_frames=validated.staff_hit_frames,
            maximum_missing_frames=validated.maximum_missing_frames,
            greeting_v2_enabled=validated.greeting_v2_enabled,
            realtime_greeting_enabled=getattr(validated, "realtime_greeting_enabled", False),
            identity_association_mode="legacy",
            model_loader=model_loader,
        )
        return BehaviorCaptureRunner(
            runtime,
            {camera.camera_id: camera.source for camera in validated.cameras},
        )

    return StoreRuntime.build(
        session_factory=sessions,
        store_id=store_id.strip(),
        mode="legacy",
        owner_id=owner_id.strip(),
        lease_service=lease_service,
        behavior_builder=build_behavior,
        identity_builder=lambda _dependencies, _on_lease_lost: _IdleRuntimeComponent(),
    )


def main(configured: Any = settings) -> dict[str, int]:
    if not getattr(configured, "IDENTITY_COMPONENT_DEBUG", False):
        runtime = build_legacy_store_runtime(configured)

        def stop_runtime(_signum: Any, _frame: Any) -> None:
            runtime.stop()

        signal.signal(signal.SIGINT, stop_runtime)
        signal.signal(signal.SIGTERM, stop_runtime)
        runtime.start()
        try:
            summary = runtime.behavior_runtime.wait()
        finally:
            runtime.stop()
        logger.info("Behavior runtime stopped: %s", summary)
        return summary

    runner = build_runner(configured)

    def stop_runner(_signum: Any, _frame: Any) -> None:
        runner.request_stop()

    signal.signal(signal.SIGINT, stop_runner)
    signal.signal(signal.SIGTERM, stop_runner)
    summary = runner.run()
    logger.info("Behavior runtime stopped: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
