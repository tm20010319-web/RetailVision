"""DPI-01E.2 cam03 offline Recognition evidence gate.

This runner is deliberately artifact-only. It validates the frozen Stage9
replay, computes the recording time window from source PTS, audits the
approved cam03 configuration, and emits anonymous facts when Recognition
configuration or historical Push coverage is unavailable. Its optional
Coverage/Inbox preflight is read-only; it never runs vision or writes business
data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.behavior_vision import load_behavior_camera_configs
from agent.offline_behavior import (
    OfflineBehaviorIdentityProjector,
    OfflineNormalizedBehaviorFact,
)
from agent.offline_identity import OfflineTrackIdentityAggregator
from agent.offline_identity_association import GuardedOfflineIdentityPass, OfflineSpatialGeometry
from agent.offline_media import (
    CapabilityProfileResolution, P6SCapabilityProfile, P6SCapabilityProfileResolver,
)
from agent.offline_recognition_provider import (
    DeviceRecognitionAcceptancePolicy,
    DurablePushInboxRecognitionProvider,
    PaginationCompletionPolicy,
    RecordedCameraRecognitionProvider,
)


CAMERA_ID = "cam03"
STORE_ID = "store-1"
SOURCE_PATH = Path(r"D:\GJJ\work\video\2026_09_05_20_33_56.mp4")
REPLAY_PATH = Path(
    r"D:\GJJ\work\dpi01d_cam03_20260905_203356_review\full_replay_rerun2.json"
)
EXPECTED_SOURCE_SHA256 = "921f6ec3881a8222ee269c767a6a3bab276e95be337b95272a0ad53a9afd2920"
EXPECTED_REPLAY_SHA256 = "a08d40c2203a2148da8c57b1a973aa56120be684932a28d9c311556dfeece10d"
LOCAL_START = datetime(2026, 9, 4, 16, 34, 52, tzinfo=ZoneInfo("Asia/Shanghai"))
TIMEZONE_NAME = "Asia/Shanghai"
TIME_CONVERSION_VERSION = "iana-tz-media-duration-v2"
EXPECTED_EVENT_COUNT = 12


class FrozenBehaviorInputMismatch(RuntimeError):
    """Raised before any identity query when the frozen evidence is invalid."""


class RecognitionQueryIntegrityError(RuntimeError):
    """Raised when a Recognition page or record breaks the query contract."""


@dataclass(frozen=True)
class RecognitionRuntime:
    """Injected execution dependencies for the existing identity chain."""

    provider: Any
    track_supplier: Any
    geometry: Any = None
    presentation_timeline_status: str = "verified"
    config_audit: Mapping[str, Any] | None = None
    identity_pass: Any = None


@dataclass(frozen=True)
class RecognitionConfiguration:
    camera_id: str
    store_id: str | None
    device_id: str | None
    base_url_present: bool
    credentials_present: Mapping[str, bool]
    capability_profile_available: bool
    missing: tuple[str, ...]
    available: bool
    raw_path: str | None = None
    auth_mode: str = "basic"
    evidence_mode: str = "historical_query"
    capability_profile_id: str | None = None
    capability_profile_path: str | None = None
    credentials_required: Mapping[str, bool] | None = None
    base_url: str | None = None
    credential_env_names: Mapping[str, str] | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _redact_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise FrozenBehaviorInputMismatch("frozen_replay_not_object")
    return value


def _event_time(event: Mapping[str, Any], start_utc: datetime) -> datetime:
    return start_utc + timedelta(seconds=float(event["source_pts_seconds"]))


def validate_frozen_input(
    *,
    source_path: Path = SOURCE_PATH,
    replay_path: Path = REPLAY_PATH,
) -> tuple[dict[str, Any], str, str]:
    """Validate the exact frozen replay contract and return parsed evidence."""
    source_sha = sha256_file(source_path)
    replay_sha = sha256_file(replay_path)
    if source_sha != EXPECTED_SOURCE_SHA256:
        raise FrozenBehaviorInputMismatch("source_sha256_mismatch")
    if replay_sha != EXPECTED_REPLAY_SHA256:
        raise FrozenBehaviorInputMismatch("frozen_replay_sha256_mismatch")
    replay = _load_json(replay_path)
    events = replay.get("raw_stage9_events")
    if not isinstance(events, list) or len(events) != EXPECTED_EVENT_COUNT:
        raise FrozenBehaviorInputMismatch("frozen_behavior_event_count_mismatch")
    if replay.get("camera_id") != CAMERA_ID:
        raise FrozenBehaviorInputMismatch("frozen_camera_scope_mismatch")
    mismatch = replay.get("state_mismatch_count")
    if mismatch is None:
        mismatch = replay.get("behavior_summary", {}).get("state_mismatch_count", 0)
    if mismatch != 0:
        raise FrozenBehaviorInputMismatch("replay_state_mismatch")
    expected_products = {"ai_electric_guitar", "ai_guitar"}
    if {event.get("product_key") for event in events} != expected_products:
        raise FrozenBehaviorInputMismatch("frozen_product_scope_mismatch")
    if any(event.get("camera_id") != CAMERA_ID for event in events):
        raise FrozenBehaviorInputMismatch("frozen_event_camera_scope_mismatch")
    expected_persons = {
        "ai_electric_guitar": "store-1:cam03:dpi01d-cam03-windowed-replay:2",
        "ai_guitar": "store-1:cam03:dpi01d-cam03-windowed-replay:1",
    }
    if any(expected_persons.get(event.get("product_key")) != event.get("person_track_token") for event in events):
        raise FrozenBehaviorInputMismatch("frozen_person_product_binding_mismatch")
    return replay, source_sha, replay_sha


def recording_time_manifest(replay: Mapping[str, Any], source_sha: str, replay_sha: str) -> dict[str, Any]:
    summary = replay["behavior_summary"]
    duration_seconds = float(summary["media_duration_seconds"])
    time_base_num = int(summary["time_base_num"])
    time_base_den = int(summary["time_base_den"])
    if duration_seconds <= 0:
        raise FrozenBehaviorInputMismatch("media_duration_invalid")
    end_local = LOCAL_START + timedelta(seconds=duration_seconds)
    start_utc = _utc_naive(LOCAL_START)
    end_utc = _utc_naive(end_local)
    events = replay["raw_stage9_events"]
    out_of_window = [
        event["event_id"]
        for event in events
        if not 0 <= float(event["source_pts_seconds"]) < duration_seconds
    ]
    if out_of_window:
        raise FrozenBehaviorInputMismatch("frozen_event_outside_recording_window")
    return {
        "camera_id": CAMERA_ID,
        "source_video": str(SOURCE_PATH),
        "source_sha256": source_sha,
        "frozen_replay": str(REPLAY_PATH),
        "frozen_replay_sha256": replay_sha,
        "operator_confirmed": {
            "local_start_at": _iso(LOCAL_START),
            "timezone": TIMEZONE_NAME,
            "timezone_database": "IANA",
        },
        "time_conversion": {
            "version": TIME_CONVERSION_VERSION,
            "duration_source": "behavior_summary.media_duration_seconds",
            "media_duration_seconds": duration_seconds,
            "source_pts_seconds": duration_seconds,
            "source_pts_status": summary.get("source_pts_status"),
            "stream_start_pts": summary.get("stream_start_pts"),
            "time_base_num": time_base_num,
            "time_base_den": time_base_den,
            "local_start_at": _iso(LOCAL_START),
            "local_end_at": _iso(end_local),
            "utc_start_at": start_utc.isoformat(timespec="milliseconds").replace("+00:00", ""),
            "utc_end_at": end_utc.isoformat(timespec="milliseconds").replace("+00:00", ""),
            "query_interval": "[start_at, end_at)",
            "events_out_of_window": out_of_window,
            "media_duration_cross_check": {
                "source_pts_status": summary.get("source_pts_status"),
                "max_behavior_event_pts_seconds": max(
                    float(event["source_pts_seconds"]) for event in events
                ),
                "duration_exceeds_max_event_pts": duration_seconds
                > max(float(event["source_pts_seconds"]) for event in events),
            },
        },
        "frozen_behavior_input": {
            "event_count": len(events),
            "state_mismatch_count": 0,
            "event_order": [event["event_id"] for event in events],
            "relative_pts_seconds": [event["source_pts_seconds"] for event in events],
            "product_track_tokens": sorted({event["product_track_token"] for event in events}),
            "person_track_tokens": sorted({event["person_track_token"] for event in events}),
            "product_to_person_track": {
                product: sorted({event["person_track_token"] for event in events if event["product_key"] == product})
                for product in sorted({event["product_key"] for event in events})
            },
        },
    }


def _formal_camera_configuration(
    *, camera_id: str = CAMERA_ID, config_path: Path | None = None
) -> RecognitionConfiguration:
    """Load one explicitly configured camera using the project's loader."""
    missing: list[str] = []
    configured_path = config_path
    if configured_path is None:
        configured_path_value = (
            os.getenv("RETAILVISION_CAMERA_CONFIG_PATH", "").strip()
            or os.getenv("BEHAVIOR_CONFIG_PATH", "").strip()
        )
        configured_path = Path(configured_path_value) if configured_path_value else None
    camera = None
    raw: dict[str, Any] = {}
    if configured_path is None:
        missing.append("RETAILVISION_CAMERA_CONFIG_PATH/BEHAVIOR_CONFIG_PATH")
    elif not configured_path.exists():
        missing.append("BEHAVIOR_CONFIG_PATH_FILE")
    else:
        try:
            cameras = load_behavior_camera_configs(configured_path)
            camera = next((item for item in cameras if item.camera_id == camera_id), None)
            raw_payload = json.loads(configured_path.read_text(encoding="utf-8"))
            raw_cameras = raw_payload.get("cameras", []) if isinstance(raw_payload, dict) else []
            raw = next((item for item in raw_cameras if isinstance(item, dict) and item.get("camera_id") == camera_id), {})
        except (OSError, ValueError, json.JSONDecodeError):
            missing.append("BEHAVIOR_CONFIG_PATH_PARSE")
    if camera is None:
        missing.append(f"camera_id={camera_id}")
    store_id = getattr(camera, "store_id", None)
    if not store_id:
        missing.append("store_id")
    recognition = raw.get("recognition") if isinstance(raw.get("recognition"), dict) else {}
    auth_mode = str(recognition.get("auth_mode", "basic")).strip().lower()
    evidence_mode = str(
        recognition.get("evidence_mode", "historical_query")
    ).strip().lower()
    if auth_mode not in {"basic", "none"}:
        missing.append("recognition.auth_mode")
    if evidence_mode not in {"historical_query", "durable_push_inbox"}:
        missing.append("recognition.evidence_mode")
    device_id = str(recognition.get("device_id", os.getenv("CAMERA_FACE_DEVICE_ID", "").strip())).strip() or None
    base_url = str(recognition.get("base_url", os.getenv("CAMERA_FACE_BASE_URL", "").strip())).strip()
    env_names = {
        "username": str(recognition.get("username_env", "CAMERA_FACE_USERNAME")),
        "password": str(recognition.get("password_env", "CAMERA_FACE_PASSWORD")),
        "unique_code": str(recognition.get("unique_code_env", "CAMERA_FACE_UNIQUE_CODE")),
    }
    # Active Push delivery is explicitly unauthenticated for cam03.  In that
    # mode the client credentials are not part of the query contract and an
    # empty password must not make an otherwise valid mapping unavailable.
    credentials = {key: bool(os.getenv(name, "").strip()) for key, name in env_names.items()}
    credentials_required = {key: auth_mode != "none" for key in env_names}
    if not device_id:
        missing.append("recognition.device_id")
    if not base_url:
        missing.append("recognition.base_url")
    for key, present in credentials.items():
        if not credentials_required[key]:
            continue
        if not present:
            missing.append(f"recognition.{key}_env:{env_names[key]}")
    profile_id = str(recognition.get("capability_profile_id", "")).strip() or None
    profile_path_env = str(
        recognition.get(
            "capability_profile_path_env", "RETAILVISION_CAPABILITY_PROFILE_PATH"
        )
    ).strip()
    profile_path_value = os.getenv(profile_path_env, "").strip()
    profile_path = Path(profile_path_value) if profile_path_value else None
    profile_available = bool(
        profile_id and profile_path is not None and profile_path.is_file()
    )
    if not profile_available:
        missing.append(
            f"recognition.capability_profile:{profile_id or '<missing>'}"
        )
    available = not missing
    return RecognitionConfiguration(
        camera_id=camera_id,
        store_id=store_id,
        device_id=device_id,
        base_url_present=bool(base_url),
        credentials_present=credentials,
        credentials_required=credentials_required,
        capability_profile_available=profile_available,
        missing=tuple(dict.fromkeys(missing)),
        available=available,
        raw_path=str(configured_path) if configured_path else None,
        auth_mode=auth_mode,
        evidence_mode=evidence_mode,
        capability_profile_id=profile_id,
        capability_profile_path=str(profile_path) if profile_path else None,
        base_url=base_url or None,
        credential_env_names=env_names,
    )


def load_configured_profiles(config: RecognitionConfiguration):
    """Deserialize the existing full profile contract from the selected path.

    Never reconstruct a verified profile from an abbreviated report snapshot,
    generate a new hash, scan directories, or substitute another camera.
    """
    if not config.capability_profile_path:
        return (), "profile_path_not_configured"
    path = Path(config.capability_profile_path)
    if not path.is_file():
        return (), "profile_file_not_found"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return (), "profile_file_parse_failed"
    if not isinstance(payload, dict):
        return (), "profile_schema_invalid"
    try:
        # Tuple-valued fields use arrays in JSON; all other fields keep their
        # original values, including the stored status, serial and content hash.
        for key in ("firmware_versions", "api_versions", "face_recording_source_frame_size", "face_recording_target_frame_size"):
            if payload.get(key) is not None:
                if not isinstance(payload[key], list):
                    return (), "profile_schema_invalid"
                payload[key] = tuple(payload[key])
        profile = P6SCapabilityProfile(**payload)
        if not profile.verifies_own_hash():
            return (), "profile_hash_invalid"
    except (TypeError, ValueError, KeyError, AttributeError):
        return (), "profile_schema_invalid"
    if profile.profile_id != config.capability_profile_id:
        return (), "configured_profile_id_mismatch"
    if getattr(config, "evidence_mode", profile.recognition_evidence_mode) != profile.recognition_evidence_mode:
        return (), "configured_profile_evidence_mode_mismatch"
    return (profile,), None


def resolve_configured_profile(config: RecognitionConfiguration, metadata: Mapping[str, Any]):
    profiles, reason = load_configured_profiles(config)
    if reason:
        return CapabilityProfileResolution("unavailable", None, reason)
    def field(name):
        value = metadata.get(name, {})
        return value if isinstance(value, dict) else {}
    identity_fields = ("device_serial", "device_model", "firmware_version")
    verified = all(
        field(name).get("source") == "device_push_metadata"
        and field(name).get("normalized_value")
        for name in identity_fields
    )
    return P6SCapabilityProfileResolver(profiles).resolve(
        device_id=config.device_id, device_serial=field("device_serial").get("normalized_value"),
        device_model=field("device_model").get("normalized_value"),
        firmware_version=field("firmware_version").get("normalized_value"),
        api_version=field("api_version").get("normalized_value"),
        fingerprint_source="device_push_metadata", fingerprint_verified=verified,
        fingerprint_retrieved_at=field("device_serial").get("retrieved_at"),
        fingerprint_metadata_endpoint="POST /api/v1/event/pull", runtime_protocol_contract="p6s-http-v1",
    )


def _config_audit(config: RecognitionConfiguration | None = None) -> dict[str, Any]:
    config = config or _formal_camera_configuration()
    return {
        "camera_id": config.camera_id,
        "store_id": config.store_id,
        "device_id_redacted": _redact_identifier(config.device_id),
        "service_address_present": config.base_url_present,
        "credentials_present": dict(config.credentials_present),
        "credentials_required": dict(
            config.credentials_required
            or {key: config.auth_mode != "none" for key in config.credentials_present}
        ),
        "authentication_mode": config.auth_mode,
        "recognition_evidence_mode": config.evidence_mode,
        "capability_profile_available": config.capability_profile_available,
        "capability_profile_id": config.capability_profile_id,
        "capability_profile_path": config.capability_profile_path,
        "approved_mapping_source": config.raw_path,
        "available": config.available,
        "missing_or_invalid_configuration": list(config.missing),
        "query_not_attempted": not config.available,
    }


def _event_fact(event: Mapping[str, Any], start_utc: datetime, identity_status: str = "unavailable") -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "product_key": event["product_key"],
        "product_name": event["product_name"],
        "product_track_token": event["product_track_token"],
        "person_track_token": event["person_track_token"],
        "person_track_id": event["person_track_id"],
        "relative_pts_seconds": event["source_pts_seconds"],
        "occurred_at": _event_time(event, start_utc).isoformat(timespec="milliseconds"),
        "duration_seconds": event.get("holding_duration_seconds", 0.0),
        "confidence": event.get("confidence"),
        "interaction_session_id": event.get("interaction_session_id"),
        "identity_status": identity_status,
        "member_id": None,
        "publication_eligible": False,
        "source_replay_event_ref": event["event_id"],
    }


def validate_recognition_record_scope(
    record: Mapping[str, Any], *, camera_id: str, start_at: datetime, end_at: datetime
) -> None:
    """Apply the logical camera and half-open time fence to one record."""
    if record.get("camera_id") not in (None, camera_id):
        raise RecognitionQueryIntegrityError("recognition_camera_scope_mismatch")
    occurred_at = record.get("occurred_at")
    if not isinstance(occurred_at, datetime) or not start_at <= occurred_at < end_at:
        raise RecognitionQueryIntegrityError("recognition_record_outside_half_open_window")


def audit_recognition_pages(
    pages: list[Mapping[str, Any]], *, camera_id: str, start_at: datetime, end_at: datetime
) -> list[dict[str, Any]]:
    """Audit deterministic page order, completeness, duplicates, and scope."""
    audits: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    expected_page = 1
    for page in pages:
        page_num = page.get("page_num")
        records = page.get("records")
        next_page = page.get("next_page")
        if page_num != expected_page or not isinstance(records, list):
            raise RecognitionQueryIntegrityError("pagination_loop_or_gap")
        fingerprint = hashlib.sha256(
            json.dumps(records, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen_pages:
            raise RecognitionQueryIntegrityError("pagination_duplicate_page")
        seen_pages.add(fingerprint)
        for record in records:
            validate_recognition_record_scope(record, camera_id=camera_id, start_at=start_at, end_at=end_at)
        eof = bool(page.get("eof"))
        audits.append({"page_num": page_num, "records_count": len(records), "eof_proven": eof})
        if eof:
            return audits
        if not isinstance(next_page, int) or next_page <= page_num:
            raise RecognitionQueryIntegrityError("pagination_loop_or_gap")
        expected_page = next_page
    raise RecognitionQueryIntegrityError("pagination_incomplete")


def project_identity_facts(
    events: list[Mapping[str, Any]], identities: Mapping[str, Mapping[str, Any]], start_utc: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project only confirmed identities; every other status stays anonymous."""
    member_facts: list[dict[str, Any]] = []
    anonymous_facts: list[dict[str, Any]] = []
    for event in events:
        identity = identities.get(event["person_track_token"], {})
        status = identity.get("identity_status", "unavailable")
        fact = _event_fact(event, start_utc, status)
        if status == "confirmed" and identity.get("member_id"):
            fact["member_id"] = identity["member_id"]
            fact["publication_eligible"] = True
            member_facts.append(fact)
        else:
            fact["member_id"] = None
            fact["publication_eligible"] = False
            anonymous_facts.append(fact)
    return member_facts, anonymous_facts


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def build_recognition_runtime(
    config: RecognitionConfiguration,
    *,
    profile_resolution: Any,
    historical_identity_resolver: Any,
    track_supplier: Any,
    session_factory: Any = None,
    push_coverage_store: Any = None,
    settings: Any = None,
) -> RecognitionRuntime:
    """Select the existing Provider from an approved capability profile."""
    if not config.available or not config.device_id:
        raise RuntimeError("CAM03_RECOGNITION_CONFIGURATION_UNAVAILABLE")
    profile = getattr(profile_resolution, "profile", None)
    reason = profile_resolution.production_eligibility_reason(device_id=config.device_id)
    if reason or profile is None:
        raise RuntimeError(reason or "provider_capability_profile_unavailable")
    acceptance = DeviceRecognitionAcceptancePolicy.create(
        policy_id=profile.recognition_acceptance_policy_id or "device-recognition",
        policy_version=profile.recognition_acceptance_policy_version or "v1",
        similarity_threshold=profile.recognition_similarity_threshold or "0.75",
        accepted_rule_ids=(1,),
        accepted_pass_codes=(None, 0),
        require_reco_valid=False,
    )
    if profile.recognition_evidence_mode == "historical_query":
        from agent.camera_face_api import CameraFaceApiClient

        configured = settings
        if configured is None:
            from agent.config import settings as configured
        env_names = config.credential_env_names or {
            "username": "CAMERA_FACE_USERNAME",
            "password": "CAMERA_FACE_PASSWORD",
            "unique_code": "CAMERA_FACE_UNIQUE_CODE",
        }
        client = CameraFaceApiClient(
            device_id=config.device_id,
            base_url=config.base_url or configured.CAMERA_FACE_BASE_URL,
            username=os.getenv(env_names["username"], ""),
            password=os.getenv(env_names["password"], ""),
            unique_code=os.getenv(env_names["unique_code"], ""),
            connect_timeout=configured.CAMERA_FACE_CONNECT_TIMEOUT,
            read_timeout=configured.CAMERA_FACE_READ_TIMEOUT,
        )
        provider = RecordedCameraRecognitionProvider(
            client=client,
            device_id=config.device_id,
            pagination_policy=PaginationCompletionPolicy(
                policy_version=profile.pagination_policy_version,
                status="verified",
                trust_class=profile.trust_class,
                completion_mode=profile.pagination_completion_mode,
            ),
            acceptance_policy=acceptance,
            historical_identity_resolver=historical_identity_resolver,
            recognition_timezone_offset_minutes=profile.timezone_offset_minutes,
            profile_resolution=profile_resolution,
        )
    elif profile.recognition_evidence_mode == "durable_push_inbox":
        if session_factory is None or push_coverage_store is None:
            raise RuntimeError("durable_push_dependencies_unavailable")
        provider = DurablePushInboxRecognitionProvider(
            session_factory=session_factory,
            push_coverage_store=push_coverage_store,
            historical_identity_resolver=historical_identity_resolver,
            acceptance_policy=acceptance,
            device_id=config.device_id,
            profile_resolution=profile_resolution,
        )
    else:
        raise RuntimeError("recognition_evidence_mode_unsupported")
    return RecognitionRuntime(
        provider=provider,
        track_supplier=track_supplier,
        geometry=(OfflineSpatialGeometry(
            decoded_source_frame_size=profile.face_recording_source_frame_size,
            analysis_frame_size=profile.face_recording_target_frame_size,
            full_frame_resize_verified=True,
            cropped=profile.face_recording_crop not in {None, "none"},
            mirrored=bool(profile.face_recording_mirror),
            rotated=profile.face_recording_rotation not in {None, 0},
            padded=profile.face_recording_padding not in {None, "none"},
            transform_type="full_frame_resize",
            scale_x=float(profile.face_recording_scale_x),
            scale_y=float(profile.face_recording_scale_y),
            offset_x=float(profile.face_recording_offset_x),
            offset_y=float(profile.face_recording_offset_y),
            policy_id=profile.face_recording_geometry_policy_id,
            policy_version=profile.face_recording_geometry_policy_version,
            validation_report_ref=profile.face_recording_geometry_validation_report_ref,
            device_id=config.device_id,
        ) if profile.face_recording_geometry_status == "verified" else None),
        config_audit={"profile_snapshot": profile_resolution.profile_snapshot()},
    )


def _normalized_fact(event: Mapping[str, Any], start_utc: datetime) -> OfflineNormalizedBehaviorFact:
    occurred_at = _event_time(event, start_utc)
    event_type = {
        "PICK_UP": "merchandise_pick_up",
        "HOLDING": "merchandise_holding",
        "PUT_DOWN": "merchandise_put_down",
    }.get(event["event_type"], "merchandise_holding")
    ended_at = occurred_at + timedelta(seconds=float(event.get("holding_duration_seconds", 0.0)))
    return OfflineNormalizedBehaviorFact(
        analysis_job_id="dpi01e2-cam03",
        analysis_run_id=event["analysis_run_id"],
        source_video_id=str(SOURCE_PATH),
        event_type=event_type,
        camera_id=event["camera_id"],
        customer_track_id=int(event["person_track_id"]),
        customer_track_token=event["person_track_token"],
        interaction_session_id=event["interaction_session_id"],
        product_key=event["product_key"],
        product_name=event.get("product_name"),
        product_track_token=event["product_track_token"],
        started_at=occurred_at,
        ended_at=ended_at,
        relative_started_at_seconds=float(event["source_pts_seconds"]),
        relative_ended_at_seconds=float(event["source_pts_seconds"]) + float(event.get("holding_duration_seconds", 0.0)),
        confidence=event.get("confidence"),
        normalized_fact_ref=event["event_id"],
        raw_event_refs=(event["event_id"],),
        holding_duration_seconds=float(event.get("holding_duration_seconds", 0.0)),
        keyframe_ref=None,
    )


def _serialize_evidence(values: Any) -> list[dict[str, Any]]:
    result = []
    for item in values:
        result.append({
            "evidence_id": item.evidence_id,
            "canonical_event_id": item.canonical_event_id,
            "occurred_at": item.occurred_at.isoformat(timespec="milliseconds"),
            "member_id": item.member_id,
            "similarity": item.similarity,
            "source": item.source,
            "device_id_redacted": _redact_identifier(item.device_id),
            "group_id": item.group_id,
            "person_id": item.person_id,
            "unique_id_present": bool(item.unique_id),
        })
    return result


def _execute_identity_chain(
    *,
    runtime: RecognitionRuntime,
    events: list[Mapping[str, Any]],
    start_at: datetime,
    end_at: datetime,
    start_utc: datetime,
    source_sha: str,
    store_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    request = SimpleNamespace(
        analysis_job_id="dpi01e2-cam03",
        analysis_run_id=events[0]["analysis_run_id"],
        store_id=store_id,
        camera_id=CAMERA_ID,
    )
    identity_pass = runtime.identity_pass or GuardedOfflineIdentityPass(
        provider=runtime.provider,
        track_supplier=runtime.track_supplier,
        geometry=runtime.geometry,
        presentation_timeline_status=runtime.presentation_timeline_status,
    )
    result = identity_pass(request, start_at, end_at)
    associations = result.associations
    if result.recognition_coverage_status != "complete":
        associations = {
            event["person_track_token"]: OfflineTrackIdentityAggregator.unavailable(
                analysis_run_id=event["analysis_run_id"],
                track_token=event["person_track_token"],
                track_started_at=None,
                track_ended_at=None,
                diagnostics=({"reason": result.failure_reason or "recognition_query_incomplete"},),
            )
            for event in events
        }
    track_tokens = sorted({event["person_track_token"] for event in events})
    track_results: list[dict[str, Any]] = []
    identity_map: dict[str, dict[str, Any]] = {}
    for token in track_tokens:
        association = associations.get(token)
        status = getattr(association, "status", "unavailable")
        member_id = getattr(association, "member_id", None) if status == "confirmed" else None
        entry = {
            "person_track_token": token,
            "identity_status": status,
            "member_id": member_id,
            "candidate_member_id": getattr(association, "candidate_member_id", None),
            "evidence_ids": list(getattr(association, "evidence_ids", ())),
            "method": getattr(association, "method", None),
            "confidence": getattr(association, "confidence", None),
            "diagnostics": list(getattr(association, "diagnostics", ())),
        }
        identity_map[token] = entry
        track_results.append(entry)
    projector = OfflineBehaviorIdentityProjector()
    member_facts: list[dict[str, Any]] = []
    anonymous_facts: list[dict[str, Any]] = []
    for event in events:
        token = event["person_track_token"]
        association = associations.get(token)
        if association is None:
            association = OfflineTrackIdentityAggregator.unavailable(
                analysis_run_id=event["analysis_run_id"],
                track_token=token,
                track_started_at=None,
                track_ended_at=None,
                diagnostics=({"reason": "track_association_missing"},),
            )
        # Keep the frozen Stage9 payload unchanged; the existing projector is
        # invoked for the identity decision and publication eligibility only.
        projector.project(
            fact=_normalized_fact(event, start_utc),
            association=association,
            enrichment=None,
            artifact_manifest_hash="v1:sha256:" + source_sha,
            association_record_ref=event["event_id"],
            store_id=store_id,
        )
        fact = _event_fact(event, start_utc, identity_map[token]["identity_status"])
        if identity_map[token]["identity_status"] == "confirmed" and identity_map[token]["member_id"]:
            fact["member_id"] = identity_map[token]["member_id"]
            fact["publication_eligible"] = True
            member_facts.append(fact)
        else:
            anonymous_facts.append(fact)
    query_complete = result.recognition_coverage_status == "complete"
    query_audit = {
        "status": "complete" if query_complete else "unavailable",
        "complete": query_complete,
        "failure_reason": result.failure_reason,
        "raw_record_count": len(result.accepted_evidence) + len(result.record_audits),
        "accepted_evidence_count": len(result.accepted_evidence),
        "pages_fetched": len(result.page_audits),
        "camera_id": CAMERA_ID,
        "requested_start": start_at.isoformat(timespec="milliseconds"),
        "requested_end": end_at.isoformat(timespec="milliseconds"),
        "query_interval": "[start_at, end_at)",
        "query_attempted": True,
    }
    page_audits = [
        item if isinstance(item, dict) else {
            "page_num": item.page_num,
            "requested_page_size": item.requested_page_size,
            "records_count": item.records_count,
            "eof_proven": item.eof_proven,
            "status": item.status,
            "completion_mode": item.completion_mode,
            "metadata": item.metadata,
        }
        for item in result.page_audits
    ]
    return query_audit, _serialize_evidence(result.accepted_evidence), track_results, member_facts, anonymous_facts, page_audits


def build_read_only_push_evidence_auditor() -> Any:
    """Build a database-only Coverage/Inbox auditor for the CLI preflight.

    This is intentionally separate from the identity Provider: when a cam03
    capability profile is absent, the command can still prove whether the
    historical Push interval is covered without treating partial rows as
    usable identity evidence. The session is read-only by convention and no
    schema initialization or commit is performed here.
    """
    from agent.db import get_session_factory
    from agent.models import DeviceRecognitionInbox
    from agent.models import PushCoverageObservation
    from agent.offline_media import P6SCapabilityProfileResolver
    from agent.push_coverage import PushCoverageStore

    session_factory = get_session_factory()
    coverage_store = PushCoverageStore(session_factory)

    def audit(start_at: datetime, end_at: datetime) -> dict[str, Any]:
        coverage = coverage_store.coverage(
            device_id=CAMERA_ID,
            start_at=start_at,
            end_at=end_at,
        )
        session = session_factory()
        try:
            latest = (
                session.query(PushCoverageObservation)
                .filter(
                    PushCoverageObservation.device_id == CAMERA_ID,
                    PushCoverageObservation.observation_type == "heartbeat_accepted",
                    PushCoverageObservation.device_metadata.isnot(None),
                )
                .order_by(PushCoverageObservation.observed_at.desc())
                .first()
            )
            records = (
                session.query(DeviceRecognitionInbox)
                .filter(
                    DeviceRecognitionInbox.device_id == CAMERA_ID,
                    DeviceRecognitionInbox.source == "push",
                    DeviceRecognitionInbox.occurred_at >= start_at,
                    DeviceRecognitionInbox.occurred_at < end_at,
                )
                .count()
            )
        finally:
            session.close()
        metadata = getattr(latest, "device_metadata", None) or {}
        serial = metadata.get("device_serial", {}) if isinstance(metadata, dict) else {}
        model = metadata.get("device_model", {}) if isinstance(metadata, dict) else {}
        firmware = metadata.get("firmware_version", {}) if isinstance(metadata, dict) else {}
        serial_value = serial.get("normalized_value") if isinstance(serial, dict) else None
        model_value = model.get("normalized_value") if isinstance(model, dict) else None
        firmware_value = firmware.get("normalized_value") if isinstance(firmware, dict) else None
        retrieved_at = serial.get("retrieved_at") if isinstance(serial, dict) else None
        profile_resolution = resolve_configured_profile(_formal_camera_configuration(), metadata)
        return {
            "status": coverage.status,
            "reason": coverage.reason,
            "coverage_start": coverage.coverage_start.isoformat(timespec="milliseconds"),
            "coverage_end": coverage.coverage_end.isoformat(timespec="milliseconds"),
            "policy_version": coverage.policy_version,
            "supporting_observation_count": len(coverage.supporting_observation_ids),
            "recognition_record_count": records,
            "device_fingerprint": {
                "device_id_redacted": _redact_identifier(CAMERA_ID),
                "serial_present": bool(serial_value),
                "device_model": model_value,
                "firmware_version": firmware_value,
                "runtime_protocol_contract": "p6s-http-v1" if latest is not None else None,
                "metadata_source": "device_push_metadata" if latest is not None else None,
                "metadata_endpoint": "POST /api/v1/event/pull" if latest is not None else None,
            },
            "capability_profile_resolution": {
                "status": profile_resolution.status,
                "reason": profile_resolution.reason,
                "profile_id": profile_resolution.profile.profile_id if profile_resolution.profile else None,
            },
            "camera_id": CAMERA_ID,
            "query_interval": "[start_at, end_at)",
            "database_write_performed": False,
        }

    return audit


def run(
    output_dir: Path,
    *,
    source_path: Path = SOURCE_PATH,
    replay_path: Path = REPLAY_PATH,
    runtime: RecognitionRuntime | None = None,
    runtime_factory: Any = None,
    config: RecognitionConfiguration | None = None,
    evidence_auditor: Any = None,
) -> dict[str, Any]:
    replay, source_sha, replay_sha = validate_frozen_input(source_path=source_path, replay_path=replay_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    time_manifest = recording_time_manifest(replay, source_sha, replay_sha)
    start_utc = datetime.fromisoformat(time_manifest["time_conversion"]["utc_start_at"])
    end_utc = datetime.fromisoformat(time_manifest["time_conversion"]["utc_end_at"])
    events = replay["raw_stage9_events"]
    config_model = config or _formal_camera_configuration()
    config_audit = _config_audit(config_model)
    tracks = {}
    for event in events:
        tracks[event["person_track_token"]] = event["person_track_id"]
    unavailable_tracks = [
        {
            "person_track_id": track_id,
            "person_track_token": token,
            "identity_status": "unavailable",
            "member_id": None,
            "candidate_member_id": None,
            "evidence_ids": [],
            "reason": "CAM03_RECOGNITION_CONFIGURATION_UNAVAILABLE",
        }
        for token, track_id in sorted(tracks.items(), key=lambda pair: pair[1])
    ]
    query_audit = {
        "camera_id": CAMERA_ID,
        "store_id": config_model.store_id or STORE_ID,
        "requested_start": time_manifest["time_conversion"]["utc_start_at"],
        "requested_end": time_manifest["time_conversion"]["utc_end_at"],
        "query_interval": "[start_at, end_at)",
        "configuration": config_audit,
        "status": "unavailable",
        "complete": False,
        "failure_reason": "CAM03_RECOGNITION_CONFIGURATION_UNAVAILABLE",
        "pages_fetched": 0,
        "raw_record_count": 0,
        "query_attempted": False,
    }
    track_results = unavailable_tracks
    member_facts: list[dict[str, Any]] = []
    anonymous_facts: list[dict[str, Any]] = []
    identity_evidence: list[dict[str, Any]] = []
    page_audits: list[dict[str, Any]] = []
    status_codes = ["DPI_01E_TIME_CONFIRMED", "DPI_01D_BASELINE_12_EVENTS_FROZEN"]
    status = "BLOCKED"
    if runtime is None and config_model.available and runtime_factory is not None:
        try:
            runtime = runtime_factory(config_model)
        except Exception as exc:
            config_audit["runtime_build_failure"] = type(exc).__name__
            config_audit["runtime_build_failure_reason"] = str(exc)
    if runtime is not None:
        try:
            (
                query_audit,
                identity_evidence,
                track_results,
                member_facts,
                anonymous_facts,
                page_audits,
            ) = _execute_identity_chain(
                runtime=runtime,
                events=events,
                start_at=start_utc,
                end_at=end_utc,
                start_utc=start_utc,
                source_sha=source_sha,
                store_id=config_model.store_id or STORE_ID,
            )
            query_audit["configuration"] = config_audit
            if query_audit["complete"]:
                status_codes.append("DPI_01E_RECOGNITION_QUERY_PASS")
                statuses = {item["identity_status"] for item in track_results}
                if "ambiguous" in statuses or "conflict" in statuses:
                    status_codes.extend(["DPI_01E_IDENTITY_AMBIGUOUS", "MEMBER_BEHAVIOR_PUBLICATION_BLOCKED"])
                elif not member_facts:
                    status_codes.extend(["DPI_01E_IDENTITY_UNMATCHED", "BEHAVIOR_FACTS_REMAIN_ANONYMOUS"])
                else:
                    status_codes.extend(["DPI_01E_OFFLINE_MEMBER_ASSOCIATION_PASS", "MEMBER_BOUND_BEHAVIOR_FACTS_READY"])
                    status = "READY"
            else:
                status_codes.append("DPI_01E_IDENTITY_BINDING_BLOCKED")
        except Exception as exc:
            query_audit = {
                **query_audit,
                "status": "unavailable",
                "complete": False,
                "failure_reason": f"identity_chain_failure:{type(exc).__name__}",
                "query_attempted": True,
            }
            track_results = [
                {**item, "reason": "identity_chain_unavailable", "identity_status": "unavailable", "member_id": None}
                for item in unavailable_tracks
            ]
            anonymous_facts = [_event_fact(event, start_utc) for event in events]
            status_codes.append("DPI_01E_IDENTITY_BINDING_BLOCKED")
    else:
        anonymous_facts = [_event_fact(event, start_utc) for event in events]
        if config_model.available:
            query_audit["failure_reason"] = "recognition_runtime_unavailable"
            status_codes.append("DPI_01E_IDENTITY_BINDING_BLOCKED")
        else:
            if evidence_auditor is not None and config_model.evidence_mode == "durable_push_inbox":
                try:
                    evidence_audit = evidence_auditor(start_utc, end_utc)
                    query_audit["historical_evidence_audit"] = evidence_audit
                    query_audit["evidence_audit_attempted"] = True
                    if evidence_audit.get("status") != "complete":
                        query_audit["failure_reason"] = (
                            "HISTORICAL_RECOGNITION_EVIDENCE_UNAVAILABLE:"
                            + str(evidence_audit.get("reason") or "coverage_unavailable")
                        )
                        track_results = [
                            {
                                **item,
                                "reason": "HISTORICAL_RECOGNITION_EVIDENCE_UNAVAILABLE",
                            }
                            for item in track_results
                        ]
                        status_codes.append("HISTORICAL_RECOGNITION_EVIDENCE_UNAVAILABLE")
                except Exception as exc:
                    query_audit["historical_evidence_audit"] = {
                        "status": "unavailable",
                        "reason": f"evidence_audit_failure:{type(exc).__name__}",
                        "camera_id": CAMERA_ID,
                        "query_interval": "[start_at, end_at)",
                        "database_write_performed": False,
                    }
                    query_audit["evidence_audit_attempted"] = True
                    status_codes.append("HISTORICAL_RECOGNITION_EVIDENCE_UNAVAILABLE")
            status_codes.extend(["CAM03_RECOGNITION_CONFIGURATION_UNAVAILABLE", "DPI_01E_IDENTITY_BINDING_BLOCKED"])
    if not anonymous_facts and len(member_facts) != len(events):
        anonymous_facts = [
            _event_fact(event, start_utc, "unavailable")
            for event in events
            if event["event_id"] not in {item["event_id"] for item in member_facts}
        ]
    _write_json(output_dir / "source_time_manifest.json", time_manifest)
    _write_json(output_dir / "recognition_query_audit.json", query_audit)
    _write_json(
        output_dir / "capability_profile_audit.json",
        {
            "camera_id": CAMERA_ID,
            "configuration": config_audit,
            "historical_evidence": query_audit.get("historical_evidence_audit"),
            "profile_resolution": (
                query_audit.get("historical_evidence_audit", {})
                .get("capability_profile_resolution")
            ),
            "status": (
                "UNAVAILABLE"
                if not config_model.capability_profile_available
                else "RESOLVED"
            ),
        },
    )
    _write_jsonl(output_dir / "recognition_page_audit.jsonl", page_audits)
    _write_jsonl(output_dir / "identity_evidence.jsonl", identity_evidence)
    _write_jsonl(output_dir / "track_identity_associations.jsonl", track_results)
    _write_jsonl(output_dir / "member_bound_behavior_facts.jsonl", member_facts)
    _write_jsonl(output_dir / "anonymous_behavior_facts.jsonl", anonymous_facts)
    gate = {
        "camera_id": CAMERA_ID,
        "source_sha256": source_sha,
        "frozen_replay_sha256": replay_sha,
        "frozen_behavior_event_count": len(events),
        "recording_time_confirmed": True,
        "recognition_configuration_available": config_model.available,
        "recognition_query_complete": query_audit["complete"],
        "track_results": track_results,
        "confirmed_member_event_count": len(member_facts),
        "anonymous_event_count": len(anonymous_facts),
        "status_codes": status_codes,
        "status": status,
        "database_write_performed": False,
    }
    _write_json(output_dir / "identity_gate_report.json", gate)
    _write_json(output_dir / "member_behavior_summary.json", {
        "camera_id": CAMERA_ID,
        "status": status,
        "confirmed_member_event_count": len(member_facts),
        "anonymous_event_count": len(anonymous_facts),
        "track_counts": {str(track_id): sum(1 for event in events if event["person_track_id"] == track_id) for track_id in sorted(set(tracks.values()))},
        "publication": "blocked",
    })
    artifacts = {}
    for path in sorted(output_dir.iterdir()):
        if path.name != "artifact_sha256.json" and path.is_file():
            artifacts[path.name] = sha256_file(path)
    _write_json(output_dir / "artifact_sha256.json", {
        "manifest_version": "dpi01e2-artifact-sha256-v1",
        "source_sha256": source_sha,
        "frozen_replay_sha256": replay_sha,
        "artifacts": artifacts,
    })
    return gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("reports/dpi01e_cam03_offline_member_binding"))
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--replay", type=Path, default=REPLAY_PATH)
    parser.add_argument(
        "--audit-durable-push",
        action="store_true",
        help="Run a read-only Coverage/Inbox audit when the capability profile is unavailable.",
    )
    args = parser.parse_args()
    try:
        audit_enabled = args.audit_durable_push or bool(os.getenv("DATABASE_URL", "").strip())
        gate = run(
            args.output_dir,
            source_path=args.source,
            replay_path=args.replay,
            evidence_auditor=(build_read_only_push_evidence_auditor() if audit_enabled else None),
        )
    except FrozenBehaviorInputMismatch as exc:
        print(f"FROZEN_BEHAVIOR_INPUT_MISMATCH: {exc}")
        return 2
    print(json.dumps({"status": gate["status"], "status_codes": gate["status_codes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
