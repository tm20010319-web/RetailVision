"""Resolve the scoped cam02 P6S capability profile from persisted Push metadata."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.offline.offline_media import P6SCapabilityProfile, P6SCapabilityProfileResolver
from agent.offline.offline_recognition_provider import DeviceRecognitionAcceptancePolicy


DEVICE_ID = "cam02"
DEVICE_SERIAL = "I20F11AB92E67AA9FE29D3D"
DEVICE_MODEL = "T5AI-W/IPG-5950PCS-AI"
FIRMWARE_VERSION = "3.4.0-20260430-EN"
RUNTIME_PROTOCOL = "p6s-http-v1"


def _latest_metadata(database: Path) -> tuple[dict, dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT id, source_event_id, observed_at, device_metadata "
            "FROM push_coverage_observations "
            "WHERE device_id = ? AND observation_type = 'heartbeat_accepted' "
            "AND device_metadata IS NOT NULL "
            "ORDER BY observed_at DESC LIMIT 1",
            (DEVICE_ID,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("persisted_push_device_metadata_unavailable")
    metadata = json.loads(row["device_metadata"])
    provenance = {
        "coverage_observation_id": row["id"],
        "source_event_id": row["source_event_id"],
        "retrieved_at": row["observed_at"],
        "database": str(database.resolve()),
    }
    return metadata, provenance


def _verified_value(metadata: dict, field: str, raw_key: str, expected: str) -> str:
    value = metadata.get(field)
    if not isinstance(value, dict):
        raise RuntimeError(f"{field}_unavailable")
    if value.get("source") != "device_push_metadata":
        raise RuntimeError(f"{field}_provenance_untrusted")
    if value.get("device_info_key") != raw_key:
        raise RuntimeError(f"{field}_raw_key_mismatch")
    if value.get("raw_value") != expected or value.get("normalized_value") != expected:
        raise RuntimeError(f"{field}_value_mismatch")
    return expected


def _profile(acceptance_hash: str) -> P6SCapabilityProfile:
    return P6SCapabilityProfile.create(
        profile_id="p6s_cam02_offline_identity",
        profile_version="v1",
        trust_class="production_verified",
        status="verified",
        device_serial=DEVICE_SERIAL,
        device_model=DEVICE_MODEL,
        firmware_versions=(FIRMWARE_VERSION,),
        api_versions=(),
        runtime_protocol_contract=RUNTIME_PROTOCOL,
        pagination_completion_mode="unsupported",
        pagination_policy_version="unsupported",
        pagination_validation_report_ref="P6S_PAGINATION_REAL_DEVICE_VALIDATION.md",
        segment_duration_policy_id="p6s_segment_duration",
        segment_duration_policy_version="unverified",
        segment_duration_tolerance_seconds="0",
        segment_duration_validation_report_ref="P6S_RECORDING_SEGMENT_BOUNDARY_VALIDATION.md",
        historical_mapping_capability_status="available",
        historical_mapping_validation_report_ref="P6S_DURABLE_PUSH_INBOX_PROVIDER_VALIDATION.md",
        timezone_interpretation_mode="verified_device_offset",
        timezone_validation_report_ref="P6S_RECORDING_TIME_REAL_DEVICE_VALIDATION.md",
        recognition_evidence_mode="durable_push_inbox",
        historical_query_status="unsupported",
        push_coverage_policy_id="p6s_push_coverage",
        push_coverage_policy_version="p6s_push_coverage_v1",
        push_coverage_validation_report_ref="P6S_DURABLE_PUSH_COVERAGE_VALIDATION.md",
        push_coverage_status="verified",
        full_segment_boundary_status="unverified",
        event_time_mode="guarded_interior",
        event_time_policy_id="p6s_guarded_interior_event_time",
        event_time_policy_version="p6s_guarded_interior_event_time_v1",
        boundary_guard_seconds="5.0",
        event_time_validation_report_ref="P6S_GUARDED_INTERIOR_EVENT_TIME_VALIDATION.md",
        timezone_offset_minutes=480,
        face_recording_geometry_policy_id="p6s_face_recording_geometry",
        face_recording_geometry_policy_version="p6s_face_recording_geometry_v1",
        face_recording_geometry_status="verified",
        face_recording_geometry_validation_report_ref="P6S_FACE_RECORDING_SPATIAL_GEOMETRY_VALIDATION.md",
        face_recording_source_frame_size=(1920, 1088),
        face_recording_target_frame_size=(800, 448),
        face_recording_scale_x="0.4166666666666667",
        face_recording_scale_y="0.4117647058823529",
        face_recording_offset_x="0",
        face_recording_offset_y="0",
        face_recording_crop="none",
        face_recording_padding="none",
        face_recording_mirror=False,
        face_recording_rotation=0,
        recognition_acceptance_policy_id="device-recognition",
        recognition_acceptance_policy_version="v1",
        recognition_acceptance_policy_hash=acceptance_hash,
        recognition_similarity_threshold="0.75",
    )


def qualify(database: Path) -> dict:
    metadata, provenance = _latest_metadata(database)
    _verified_value(metadata, "device_serial", "serialNumber", DEVICE_SERIAL)
    _verified_value(metadata, "device_model", "deviceType", DEVICE_MODEL)
    _verified_value(metadata, "firmware_version", "deviceVersion", FIRMWARE_VERSION)
    acceptance = DeviceRecognitionAcceptancePolicy.create(
        policy_id="device-recognition",
        policy_version="v1",
        similarity_threshold="0.75",
        accepted_rule_ids=(1,),
        accepted_pass_codes=(None, 0),
        require_reco_valid=False,
    )
    profile = _profile(acceptance.policy_hash)
    resolver = P6SCapabilityProfileResolver((profile,))
    common = {
        "device_id": DEVICE_ID,
        "device_serial": DEVICE_SERIAL,
        "device_model": DEVICE_MODEL,
        "firmware_version": FIRMWARE_VERSION,
        "api_version": None,
        "runtime_protocol_contract": RUNTIME_PROTOCOL,
        "fingerprint_source": "device_push_metadata",
        "fingerprint_verified": True,
        "fingerprint_retrieved_at": provenance["retrieved_at"] + "Z",
        "fingerprint_metadata_endpoint": "POST /api/v1/event/pull",
    }
    resolution = resolver.resolve(**common)
    if resolution.status != "resolved" or resolution.production_eligibility_reason(device_id=DEVICE_ID):
        raise RuntimeError(resolution.reason or "profile_resolution_failed")

    drift_cases = {}
    for name, updates in {
        "missing_serial": {"device_serial": None},
        "wrong_serial": {"device_serial": "OTHER"},
        "wrong_model": {"device_model": "OTHER"},
        "wrong_firmware": {"firmware_version": "3.4.1"},
        "wrong_runtime_protocol": {"runtime_protocol_contract": "p6s-http-v2"},
        "missing_provenance": {"fingerprint_source": None},
        "unverified_fingerprint": {"fingerprint_verified": False},
    }.items():
        values = dict(common)
        values.update(updates)
        result = resolver.resolve(**values)
        drift_cases[name] = {"status": result.status, "reason": result.reason}
        if result.status != "unavailable":
            raise RuntimeError(f"{name}_did_not_fail_closed")
    tampered = replace(profile, profile_content_hash="v1:sha256:" + "0" * 64)
    tampered_result = P6SCapabilityProfileResolver((tampered,)).resolve(**common)
    drift_cases["tampered_profile_hash"] = {
        "status": tampered_result.status,
        "reason": tampered_result.reason,
    }
    if tampered_result.status != "unavailable":
        raise RuntimeError("tampered_profile_hash_did_not_fail_closed")

    identity = json.loads(
        Path("reports/p6s_face_recording_geometry_20260819/event_701_identity_association.json")
        .read_text(encoding="utf-8")
    )
    b26_pass = (
        identity["result"]["status"] == "confirmed"
        and identity["result"]["member_id"] == "member-gaojunjie"
        and identity["result"]["track_token"] == "store-1:cam02:b26-real-701:1"
    )
    return {
        "qualification": "B2.7B",
        "device_id": DEVICE_ID,
        "device_serial": DEVICE_SERIAL,
        "device_model": DEVICE_MODEL,
        "firmware_version": FIRMWARE_VERSION,
        "device_reported_api_version": None,
        "runtime_protocol_contract": RUNTIME_PROTOCOL,
        "metadata_source": "device_push_metadata",
        "metadata_provenance": provenance,
        "fingerprint_verified": True,
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_content_hash": profile.profile_content_hash,
        "profile_self_hash_verified": profile.verifies_own_hash(),
        "profile_resolution": resolution.status,
        "resolution_attestation": resolution.resolution_attestation,
        "profile_snapshot": resolution.profile_snapshot(),
        "drift_cases": drift_cases,
        "b26_confirmed_regression": "PASS" if b26_pass else "FAIL",
        "b27_capability_profile": "VERIFIED" if b26_pass else "NOT VERIFIED",
        "provider_a_production": "FAIL-CLOSED",
        "b28_trusted_production_publication": "NEXT" if b26_pass else "BLOCKED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("runtime/databases/demo_flow.db"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/p6s_device_fingerprint_20260819/scoped_profile_resolution.json"),
    )
    args = parser.parse_args()
    result = qualify(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
