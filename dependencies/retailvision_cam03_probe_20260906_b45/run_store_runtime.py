"""Production entrypoint for one Store's unified identity runtime."""

from __future__ import annotations

import logging
import signal
from datetime import datetime
from typing import Any, Optional

from agent.camera_face_api import CameraFaceApiClient
from agent.config import settings
from agent.db import get_session_factory, init_db
from agent.device_capture_store import DeviceCaptureStore
from agent.device_identity import SqlAlchemyDeviceIdentityRepository
from pipelines.behavior_runtime import BehaviorCaptureRunner, create_behavior_runtime
from pipelines.device_identity_runtime import create_device_identity_runtime
from pipelines.member_business_runtime import create_member_business_runtime
from pipelines.store_runtime import StoreRuntime
from run_behavior_runtime import validate_runtime_settings as validate_behavior_settings
from run_device_identity import (
    parse_video_source,
    validate_runtime_settings as validate_device_settings,
)


logger = logging.getLogger(__name__)


def validate_store_runtime_settings(configured: Any):
    mode = getattr(configured, "IDENTITY_ASSOCIATION_MODE", None)
    if mode not in {"new", "legacy"}:
        raise ValueError("IDENTITY_ASSOCIATION_MODE must be explicitly new or legacy")
    store_id = getattr(configured, "IDENTITY_ASSOCIATION_STORE_ID", None)
    owner_id = getattr(configured, "IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID", None)
    if not isinstance(store_id, str) or not store_id.strip():
        raise ValueError("IDENTITY_ASSOCIATION_STORE_ID is required")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID is required")
    finalization_enabled = getattr(
        configured, "PHASE2B_FINALIZATION_ENABLED", False
    )
    if not isinstance(finalization_enabled, bool):
        raise ValueError("PHASE2B_FINALIZATION_ENABLED must be a boolean")
    if mode == "legacy" and finalization_enabled:
        raise ValueError("PHASE2B_FINALIZATION_ENABLED requires new mode")
    validate_device_settings(configured, allow_store_runtime=True)
    behavior = validate_behavior_settings(configured, allow_store_runtime=True)
    foreign_stores = {
        camera.store_id
        for camera in behavior.cameras
        if camera.store_id != store_id
    }
    if foreign_stores:
        raise ValueError("Behavior cameras must belong to the StoreRuntime store")
    return behavior


def build_runtime(
    configured: Any = settings,
    *,
    session_factory: Optional[Any] = None,
    model_loader: Optional[Any] = None,
    lease_service: Optional[Any] = None,
) -> StoreRuntime:
    behavior_settings = validate_store_runtime_settings(configured)
    init_db(
        preserve_existing_recognition_rows=bool(
            getattr(configured, "PRESERVE_EXISTING_RECOGNITION_ROWS", False)
        )
    )
    sessions = session_factory or get_session_factory()
    mode = configured.IDENTITY_ASSOCIATION_MODE
    store_id = configured.IDENTITY_ASSOCIATION_STORE_ID.strip()
    owner_id = configured.IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID.strip()

    client = CameraFaceApiClient(
        device_id=configured.CAMERA_FACE_DEVICE_ID,
        base_url=configured.CAMERA_FACE_BASE_URL,
        username=configured.CAMERA_FACE_USERNAME,
        password=configured.CAMERA_FACE_PASSWORD,
        unique_code=configured.CAMERA_FACE_UNIQUE_CODE,
        connect_timeout=configured.CAMERA_FACE_CONNECT_TIMEOUT,
        read_timeout=configured.CAMERA_FACE_READ_TIMEOUT,
    )
    repository = SqlAlchemyDeviceIdentityRepository(sessions)
    capture_store = DeviceCaptureStore(
        configured.P6S_CAPTURE_DIR,
        max_bytes=configured.P6S_MAX_CAPTURE_BYTES,
        max_pixels=configured.P6S_MAX_CAPTURE_PIXELS,
    )

    def build_behavior(dependencies):
        runtime = create_behavior_runtime(
            cameras=behavior_settings.cameras,
            session_factory=sessions,
            person_model_path=behavior_settings.person_model_path,
            staff_vest_model_path=behavior_settings.staff_vest_model_path,
            pose_model_path=behavior_settings.pose_model_path,
            device=behavior_settings.device,
            tracker=behavior_settings.tracker,
            conf_threshold=behavior_settings.confidence,
            staff_vest_enabled=behavior_settings.staff_vest_enabled,
            staff_vest_confidence_threshold=behavior_settings.staff_vest_confidence,
            role_window_frames=behavior_settings.role_window_frames,
            staff_hit_frames=behavior_settings.staff_hit_frames,
            greeting_mock_staff_name=behavior_settings.greeting_mock_staff_name,
            maximum_missing_frames=behavior_settings.maximum_missing_frames,
            greeting_v2_enabled=behavior_settings.greeting_v2_enabled,
            realtime_greeting_enabled=getattr(behavior_settings, "realtime_greeting_enabled", False),
            model_loader=model_loader,
            identity_association_mode=mode,
            track_snapshot_buffer=dependencies.track_snapshot_buffer,
            track_identity_cache=dependencies.track_identity_cache,
            visit_activity_refresher=dependencies.visit_activity_refresher,
            entry_runtime_lease_validator=lambda db, entry: (
                dependencies.lease_service.validate_fence(
                    store_id=entry.store_id,
                    mode=dependencies.lease.mode,
                    owner_id=dependencies.lease.owner_id,
                    lease_token=dependencies.lease.lease_token,
                    lease_generation=dependencies.lease.lease_generation,
                    session=db,
                )
            ),
            runtime_id=dependencies.runtime_instance_id,
            runtime_instance_id=dependencies.runtime_instance_id,
        )
        return BehaviorCaptureRunner(
            runtime,
            {camera.camera_id: camera.source for camera in behavior_settings.cameras},
            camera_runtime_registry=dependencies.camera_runtime_registry,
            store_id=store_id,
            runtime_instance_id=dependencies.runtime_instance_id,
            reconnect_interval_seconds=1.0,
        )

    def build_identity(dependencies, on_lease_lost):
        business_runtime = None
        if mode == "legacy":
            business_runtime = create_member_business_runtime(
                session_factory=sessions,
                profile_mode=configured.PROFILE_MODE,
                output_dir=configured.TRACK_SNAPSHOT_DIR,
                profile_job_poll_interval=configured.PROFILE_JOB_POLL_INTERVAL,
                profile_job_max_attempts=configured.PROFILE_JOB_MAX_ATTEMPTS,
                ollama_vision_timeout_seconds=configured.OLLAMA_VISION_TIMEOUT_SECONDS,
                ollama_vision_max_retries=configured.OLLAMA_VISION_MAX_RETRIES,
                recovery_interval_seconds=configured.MEMBER_ENTRY_RECOVERY_INTERVAL,
                snapshot_ttl_seconds=configured.TRACK_SNAPSHOT_TTL_SECONDS,
            )
        return create_device_identity_runtime(
            client=client,
            repository=repository,
            session_factory=sessions,
            capture_store=capture_store,
            recognition_similarity_threshold=configured.P6S_SIMILARITY_THRESHOLD,
            model_path=configured.YOLO_MODEL_PATH,
            poll_interval_seconds=configured.CAMERA_FACE_POLL_INTERVAL,
            inbox_poll_interval_seconds=configured.DEVICE_EVENT_WORKER_INTERVAL,
            inbox_processing_timeout_seconds=configured.DEVICE_EVENT_PROCESSING_TIMEOUT,
            device_health_freshness_seconds=configured.DEVICE_HEALTH_FRESHNESS_SECONDS,
            member_business_runtime=business_runtime,
            identity_association_mode=mode,
            association_store_id=store_id,
            association_runtime_owner_id=owner_id,
            track_candidate_provider=dependencies.candidate_provider,
            association_lease_service=dependencies.lease_service,
            association_lease=dependencies.lease,
            store_resolution_service=dependencies.store_resolution,
            resolver=dependencies.resolver,
            projection_worker=dependencies.projection_worker,
            association_audit_service=dependencies.association_audit,
            runtime_instance_id=dependencies.runtime_instance_id,
            association_deadline_seconds=getattr(
                configured, "ASSOCIATION_DEADLINE_SECONDS", 5.0
            ),
            inbox_min_received_at=dependencies.runtime_started_at,
            video_source=(
                parse_video_source(configured.VIDEO_SOURCE)
                if mode == "legacy"
                else None
            ),
            on_association_lease_lost=on_lease_lost,
            finalization_worker=dependencies.finalization_worker,
            binding_heartbeat=dependencies.binding_heartbeat,
        )

    min_received_at = getattr(configured, "INBOX_MIN_RECEIVED_AT", "")
    runtime_started_at_override = None
    if min_received_at:
        try:
            runtime_started_at_override = datetime.fromisoformat(min_received_at)
        except ValueError as error:
            raise ValueError(
                "INBOX_MIN_RECEIVED_AT must be ISO datetime without timezone"
            ) from error

    return StoreRuntime.build(
        session_factory=sessions,
        store_id=store_id,
        mode=mode,
        owner_id=owner_id,
        lease_service=lease_service,
        runtime_started_at_override=runtime_started_at_override,
        snapshot_buffer_ttl_seconds=getattr(configured, "SNAPSHOT_BUFFER_SECONDS", 5.0),
        track_lost_seconds=getattr(configured, "TRACK_LOST_SECONDS", 2.0),
        snapshot_buffer_max_per_track=getattr(configured, "SNAPSHOT_BUFFER_MAX_PER_TRACK", 180),
        association_window_seconds=getattr(configured, "ASSOCIATION_WINDOW_SECONDS", 1.0),
        min_track_confidence=getattr(configured, "MIN_TRACK_CONFIDENCE", 0.55),
        min_stable_frames=getattr(configured, "MIN_STABLE_FRAMES", 3),
        face_spatial_min_containment=getattr(
            configured, "FACE_SPATIAL_MIN_CONTAINMENT", 0.8
        ),
        face_spatial_upper_body_ratio=getattr(
            configured, "FACE_SPATIAL_UPPER_BODY_RATIO", 0.45
        ),
        phase2b_finalization_enabled=getattr(
            configured, "PHASE2B_FINALIZATION_ENABLED", False
        ),
        transform_status_provider=lambda camera_id: next(
            (
                getattr(camera, "face_transform_status", "unverified")
                for camera in behavior_settings.cameras
                if camera.camera_id == camera_id
            ),
            "unverified",
        ),
        finalization_poll_interval_seconds=getattr(
            configured, "FINALIZATION_POLL_INTERVAL_SECONDS", 0.25
        ),
        finalization_claim_lease_seconds=getattr(
            configured, "FINALIZATION_CLAIM_LEASE_SECONDS", 30.0
        ),
        finalization_retry_base_seconds=getattr(
            configured, "FINALIZATION_RETRY_BASE_SECONDS", 0.5
        ),
        finalization_retry_max_seconds=getattr(
            configured, "FINALIZATION_RETRY_MAX_SECONDS", 30.0
        ),
        binding_heartbeat_interval_seconds=getattr(
            configured, "BINDING_HEARTBEAT_INTERVAL_SECONDS", None
        ),
        visit_activity_refresh_interval_seconds=getattr(
            configured, "VISIT_ACTIVITY_REFRESH_INTERVAL_SECONDS", 5.0
        ),
        behavior_builder=build_behavior,
        identity_builder=build_identity,
    )


def main(configured: Any = settings) -> None:
    runtime = build_runtime(configured)

    def stop_runtime(_signum: Any, _frame: Any) -> None:
        runtime.stop()

    signal.signal(signal.SIGINT, stop_runtime)
    signal.signal(signal.SIGTERM, stop_runtime)
    runtime.start()
    try:
        runtime.behavior_runtime.wait()
    finally:
        runtime.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
