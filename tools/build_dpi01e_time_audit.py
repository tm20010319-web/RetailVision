"""Build the fail-closed DPI-01E time and identity audit artifact.

This tool deliberately does not query Recognition or change the frozen DPI-01D
behavior result. It records the conflicting time provenance and preserves the
12 Stage9 events as anonymous, relative-time facts until an authoritative
recording interval is confirmed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SOURCE_VIDEO = Path(r"D:\GJJ\work\video\2026_09_05_20_33_56.mp4")
REPLAY = Path(
    r"D:\GJJ\work\dpi01d_cam03_20260905_203356_review\full_replay_rerun2.json"
)
DIAGNOSTICS = Path(
    r"D:\GJJ\work\dpi01d_cam03_20260905_203356_review\full_diagnostics_rerun2\dpi01d_window_diagnostics.json"
)
OVERLAY_START = "2026-09-04T16:34:52+08:00"
OVERLAY_END_SAMPLE = "2026-09-04T16:36:10+08:00"
TIMEZONE = "Asia/Shanghai"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build(output: Path) -> None:
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    source_sha = _sha256(SOURCE_VIDEO)
    duration_seconds = float(replay["behavior_summary"]["media_duration_seconds"])
    overlay_start = datetime.fromisoformat(OVERLAY_START)
    candidate_end = overlay_start + timedelta(seconds=duration_seconds)

    output.mkdir(parents=True, exist_ok=True)
    source_manifest = {
        "manifest_version": "dpi01e_source_time_manifest_v1",
        "phase": "DPI-01E",
        "logical_camera_id": "cam03",
        "device_scope_status": "pending_operator_confirmation",
        "source_video": str(SOURCE_VIDEO),
        "source_sha256": source_sha,
        "expected_source_sha256_from_dpi01d": replay["source_sha256"],
        "source_sha256_match": source_sha == replay["source_sha256"],
        "duration_seconds": duration_seconds,
        "pts_status": replay["behavior_summary"]["source_pts_status"],
        "time_base": {
            "num": replay["behavior_summary"]["time_base_num"],
            "den": replay["behavior_summary"]["time_base_den"],
        },
        "video_overlay_samples": {
            "first_frame": OVERLAY_START,
            "at_40_seconds": "2026-09-04T16:35:30+08:00",
            "at_80_seconds": "2026-09-04T16:36:09+08:00",
            "last_frame_sample": OVERLAY_END_SAMPLE,
        },
        "candidate_recording_interval": {
            "recording_started_at": OVERLAY_START,
            "recording_ended_at": candidate_end.isoformat(),
            "iana_timezone": TIMEZONE,
            "provenance": "video_timestamp_overlay_only",
            "status": "UNCONFIRMED",
        },
        "conflicting_metadata": {
            "filename_time": "2026-09-05T20:33:56+08:00",
            "container_creation_time_utc": "2026-09-05T12:34:02Z",
            "container_metadata_status": "not_used_as_recording_start",
        },
        "time_provenance_status": "RECORDING_TIME_PROVENANCE_PENDING",
        "absolute_event_time_calculation": "blocked_until_operator_confirms_recording_started_at_and_timezone",
    }
    _write_json(output / "source_time_manifest.json", source_manifest)

    events = replay["raw_stage9_events"]
    track_tokens = sorted({event["person_track_token"] for event in events})
    anonymous = []
    for event in events:
        anonymous.append(
            {
                "record_type": "anonymous_stage9_behavior_fact",
                "identity_status": "unavailable",
                "identity_binding_level": "anonymous",
                "identity_source": None,
                "member_id": None,
                "person_track_token": event["person_track_token"],
                "product_key": event["product_key"],
                "product_name": event.get("product_name"),
                "event_type": event["event_type"],
                "occurred_at": None,
                "source_seq": event["source_seq"],
                "source_pts_seconds": event["source_pts_seconds"],
                "duration_seconds": event.get("holding_duration_seconds"),
                "analysis_run_id": event["analysis_run_id"],
                "source_sha256": source_sha,
            }
        )
    _write_jsonl(output / "anonymous_behavior_facts.jsonl", anonymous)
    _write_jsonl(output / "member_bound_behavior_facts.jsonl", [])
    _write_jsonl(output / "identity_evidence.jsonl", [])
    _write_jsonl(output / "track_identity_associations.jsonl", [
        {
            "analysis_run_id": events[0]["analysis_run_id"],
            "camera_id": "cam03",
            "person_track_token": token,
            "identity_status": "unavailable",
            "member_id": None,
            "reason": "RECORDING_TIME_PROVENANCE_PENDING",
        }
        for token in track_tokens
    ])
    _write_jsonl(output / "recognition_page_audit.jsonl", [])
    _write_json(
        output / "recognition_query_audit.json",
        {
            "status": "not_started",
            "identity_status": "unavailable",
            "reason": "RECORDING_TIME_PROVENANCE_PENDING",
            "query_camera_id": "cam03",
            "requested_interval": None,
            "pages_fetched": 0,
            "coverage_complete": False,
            "records_returned": 0,
            "recognition_query_performed": False,
        },
    )
    _write_json(
        output / "identity_gate_report.json",
        {
            "phase": "DPI-01E",
            "camera_id": "cam03",
            "time_provenance_status": "RECORDING_TIME_PROVENANCE_PENDING",
            "recognition_query_status": "blocked",
            "identity_status": "unavailable",
            "member_projection_status": "blocked",
            "frozen_stage9_event_count": len(events),
            "anonymous_fact_count": len(anonymous),
            "gate_results": {
                "recording_interval_confirmed": False,
                "logical_camera_id_confirmed": True,
                "source_sha256_verified": source_sha == replay["source_sha256"],
                "recognition_query_complete": False,
                "acceptance_policy_passed": False,
                "historical_mapping_resolved": False,
                "same_camera_track_association": False,
            },
            "result_codes": [
                "DPI_01E_IDENTITY_BINDING_BLOCKED",
                "IDENTITY_EVIDENCE_UNAVAILABLE",
                "BEHAVIOR_REPLAY_REMAINS_VALID",
                "RECORDING_TIME_PROVENANCE_PENDING",
            ],
        },
    )
    _write_json(
        output / "member_behavior_summary.json",
        {
            "identity_status": "unavailable",
            "member_bound_event_count": 0,
            "anonymous_event_count": len(anonymous),
            "frozen_stage9_event_count": len(events),
            "member_behavior_event_write": "blocked",
            "reason": "RECORDING_TIME_PROVENANCE_PENDING",
            "products": sorted({event["product_key"] for event in events}),
            "person_track_tokens": track_tokens,
        },
    )

    artifact_hashes = {}
    for path in sorted(output.iterdir()):
        if path.name == "artifact_sha256.json":
            continue
        artifact_hashes[path.name] = _sha256(path)
    _write_json(
        output / "artifact_sha256.json",
        {
            "artifact_hash_version": "sha256",
            "source_video_sha256": source_sha,
            "artifacts": artifact_hashes,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/dpi01e_cam03_offline_member_binding"),
    )
    args = parser.parse_args()
    build(args.output)
