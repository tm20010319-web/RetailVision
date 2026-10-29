"""Production startup entrypoint for device-backed member identity."""

from __future__ import annotations

import logging
import math
import signal
from numbers import Real
from typing import Any, Union

from agent.camera_face_api import CameraFaceApiClient
from agent.config import settings
from agent.db import get_session_factory, init_db
from agent.device_capture_store import DeviceCaptureStore
from agent.device_identity import SqlAlchemyDeviceIdentityRepository
from pipelines.device_identity_runtime import (
    DeviceIdentityRuntime,
    create_device_identity_runtime,
)
from pipelines.member_business_runtime import create_member_business_runtime


logger = logging.getLogger(__name__)

_REQUIRED_SETTINGS = (
    "CAMERA_FACE_DEVICE_ID",
    "CAMERA_FACE_BASE_URL",
    "CAMERA_FACE_USERNAME",
    "CAMERA_FACE_UNIQUE_CODE",
)
_PROFILE_MODES = {"mock", "ollama", "disabled"}
_POSITIVE_DURATION_SETTINGS = (
    "CAMERA_FACE_CONNECT_TIMEOUT",
    "CAMERA_FACE_READ_TIMEOUT",
    "CAMERA_FACE_POLL_INTERVAL",
    "DEVICE_EVENT_WORKER_INTERVAL",
    "DEVICE_EVENT_PROCESSING_TIMEOUT",
    "DEVICE_HEALTH_FRESHNESS_SECONDS",
    "PROFILE_JOB_POLL_INTERVAL",
    "OLLAMA_VISION_TIMEOUT_SECONDS",
    "MEMBER_ENTRY_RECOVERY_INTERVAL",
    "TRACK_SNAPSHOT_TTL_SECONDS",
)


def parse_video_source(value: str) -> Union[int, str]:
    """Convert numeric camera indexes to integers and preserve file paths."""
    try:
        return int(value)
    except ValueError:
        return value


def validate_runtime_settings(
    configured: Any,
    *,
    allow_store_runtime: bool = False,
) -> None:
    """Reject invalid credentials, modes, and worker timing settings."""
    mode = getattr(configured, "IDENTITY_ASSOCIATION_MODE", None)
    if mode not in {"new", "legacy"}:
        raise ValueError("IDENTITY_ASSOCIATION_MODE must be explicitly new or legacy")
    if (
        mode == "new"
        and not allow_store_runtime
        and not getattr(configured, "IDENTITY_COMPONENT_DEBUG", False)
    ):
        raise RuntimeError(
            "new production mode must start through StoreRuntime"
        )
    if not getattr(configured, "IDENTITY_COMPONENT_DEBUG", False):
        for name in (
            "IDENTITY_ASSOCIATION_STORE_ID",
            "IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID",
        ):
            value = getattr(configured, name, None)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
    for name in _REQUIRED_SETTINGS:
        value = getattr(configured, name, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
    if not isinstance(getattr(configured, "CAMERA_FACE_PASSWORD", None), str):
        raise ValueError("CAMERA_FACE_PASSWORD must be a string")

    profile_mode = getattr(configured, "PROFILE_MODE", None)
    if profile_mode not in _PROFILE_MODES:
        raise ValueError("PROFILE_MODE must be 'mock', 'ollama', or 'disabled'")

    for name in _POSITIVE_DURATION_SETTINGS:
        value = getattr(configured, name, None)
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive number")

    max_attempts = getattr(configured, "PROFILE_JOB_MAX_ATTEMPTS", None)
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts <= 0
    ):
        raise ValueError("PROFILE_JOB_MAX_ATTEMPTS must be a positive integer")

    vision_retries = getattr(configured, "OLLAMA_VISION_MAX_RETRIES", None)
    if (
        isinstance(vision_retries, bool)
        or not isinstance(vision_retries, int)
        or vision_retries <= 0
    ):
        raise ValueError("OLLAMA_VISION_MAX_RETRIES must be a positive integer")


def build_runtime(configured: Any = settings) -> DeviceIdentityRuntime:
    """Compose the production runtime without local face recognition."""
    validate_runtime_settings(configured)
    init_db()
    session_factory = get_session_factory()
    client = CameraFaceApiClient(
        device_id=configured.CAMERA_FACE_DEVICE_ID,
        base_url=configured.CAMERA_FACE_BASE_URL,
        username=configured.CAMERA_FACE_USERNAME,
        password=configured.CAMERA_FACE_PASSWORD,
        unique_code=configured.CAMERA_FACE_UNIQUE_CODE,
        connect_timeout=configured.CAMERA_FACE_CONNECT_TIMEOUT,
        read_timeout=configured.CAMERA_FACE_READ_TIMEOUT,
    )
    repository = SqlAlchemyDeviceIdentityRepository(session_factory)
    capture_store = DeviceCaptureStore(
        configured.P6S_CAPTURE_DIR,
        max_bytes=configured.P6S_MAX_CAPTURE_BYTES,
        max_pixels=configured.P6S_MAX_CAPTURE_PIXELS,
    )
    mode = configured.IDENTITY_ASSOCIATION_MODE
    business_runtime = None
    if mode == "legacy":
        business_runtime = create_member_business_runtime(
            session_factory=session_factory,
            profile_mode=configured.PROFILE_MODE,
            output_dir=configured.TRACK_SNAPSHOT_DIR,
            profile_job_poll_interval=configured.PROFILE_JOB_POLL_INTERVAL,
            profile_job_max_attempts=configured.PROFILE_JOB_MAX_ATTEMPTS,
            ollama_vision_timeout_seconds=configured.OLLAMA_VISION_TIMEOUT_SECONDS,
            ollama_vision_max_retries=configured.OLLAMA_VISION_MAX_RETRIES,
            recovery_interval_seconds=configured.MEMBER_ENTRY_RECOVERY_INTERVAL,
            snapshot_ttl_seconds=configured.TRACK_SNAPSHOT_TTL_SECONDS,
        )
    runtime = create_device_identity_runtime(
        client=client,
        repository=repository,
        session_factory=session_factory,
        capture_store=capture_store,
        recognition_similarity_threshold=configured.P6S_SIMILARITY_THRESHOLD,
        model_path=configured.YOLO_MODEL_PATH,
        poll_interval_seconds=configured.CAMERA_FACE_POLL_INTERVAL,
        inbox_poll_interval_seconds=configured.DEVICE_EVENT_WORKER_INTERVAL,
        inbox_processing_timeout_seconds=configured.DEVICE_EVENT_PROCESSING_TIMEOUT,
        device_health_freshness_seconds=configured.DEVICE_HEALTH_FRESHNESS_SECONDS,
        member_business_runtime=business_runtime,
        identity_association_mode=mode,
        association_store_id=getattr(configured, "IDENTITY_ASSOCIATION_STORE_ID", None),
        association_runtime_owner_id=getattr(
            configured, "IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID", None
        ),
        component_debug=getattr(configured, "IDENTITY_COMPONENT_DEBUG", False),
    )
    logger.info(
        "Device identity runtime built for device %s in %s profile mode",
        configured.CAMERA_FACE_DEVICE_ID,
        configured.PROFILE_MODE,
    )
    return runtime


def main(configured: Any = settings) -> None:
    """Build the runtime, install shutdown handlers, and process video."""
    runtime = build_runtime(configured)

    def stop_runtime(_signum: Any, _frame: Any) -> None:
        runtime.request_stop()

    signal.signal(signal.SIGINT, stop_runtime)
    signal.signal(signal.SIGTERM, stop_runtime)
    runtime.run(parse_video_source(configured.VIDEO_SOURCE))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
