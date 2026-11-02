"""Capture and replay Staff Role evidence without changing formal artifacts."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.behavior.behavior_monitor import BehaviorMonitor
from agent.behavior.behavior_vision import (
    BehaviorVisionAdapter,
    PersonRoleResolver,
    PersonTrackDetection,
    VestDetection,
)
from agent.behavior.staff_greeting_evaluation import (
    OfficialGreetingCore,
    _distance_trend_is_decreasing,
)
from agent.behavior.staff_greeting_offline import OpenCVPTSReader, load_reception_camera_config


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bbox_iou(left: list[float], right: list[float]) -> float:
    lx1, ly1, lx2, ly2 = (float(value) for value in left)
    rx1, ry1, rx2, ry2 = (float(value) for value in right)
    intersection_width = max(0.0, min(lx2, rx2) - max(lx1, rx1))
    intersection_height = max(0.0, min(ly2, ry2) - max(ly1, ry1))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, lx2 - lx1) * max(0.0, ly2 - ly1)
    right_area = max(0.0, rx2 - rx1) * max(0.0, ry2 - ry1)
    union = left_area + right_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _center_inside(inner: list[float], outer: list[float]) -> bool:
    ix1, iy1, ix2, iy2 = (float(value) for value in inner)
    ox1, oy1, ox2, oy2 = (float(value) for value in outer)
    center_x = (ix1 + ix2) / 2.0
    center_y = (iy1 + iy2) / 2.0
    return ox1 <= center_x <= ox2 and oy1 <= center_y <= oy2


def _subject_candidates(
    subject_bbox: list[float],
    person_tracks: list[dict[str, Any]],
    *,
    min_iou: float,
) -> list[dict[str, Any]]:
    candidates = []
    for track in person_tracks:
        track_bbox = track["bbox"]
        iou = _bbox_iou(subject_bbox, track_bbox)
        mutual_center_containment = _center_inside(
            subject_bbox, track_bbox
        ) and _center_inside(track_bbox, subject_bbox)
        if iou < min_iou and not mutual_center_containment:
            continue
        candidates.append({"track": track, "iou": iou})
    return sorted(candidates, key=lambda item: item["iou"], reverse=True)


def validate_subject_truth(
    rows: list[dict[str, Any]],
    fixture: dict[str, Any],
    *,
    min_iou: float = 0.30,
    ambiguity_margin: float = 0.10,
    max_pts_delta: float = 0.025,
) -> dict[str, Any]:
    """Validate manual per-frame subjects without treating track IDs as identity."""
    if fixture.get("schema_version") != "staff_greeting_subject_truth_v1":
        raise ValueError("unsupported subject truth schema")
    if fixture.get("coordinate_mode") != "pixel_xyxy":
        raise ValueError("subject truth requires coordinate_mode=pixel_xyxy")
    if not rows:
        raise ValueError("candidate diagnostic rows are empty")

    expected_sha256 = fixture.get("source_sha256")
    candidate_sha256 = {row.get("source_sha256") for row in rows}
    expected_size = fixture.get("source_frame_size")
    candidate_sizes = {
        (row.get("frame_width"), row.get("frame_height")) for row in rows
    }
    provenance_error = None
    if candidate_sha256 != {expected_sha256}:
        provenance_error = "subject_truth_source_mismatch"
    elif (
        not isinstance(expected_size, list)
        or len(expected_size) != 2
        or candidate_sizes != {(expected_size[0], expected_size[1])}
    ):
        provenance_error = "subject_truth_frame_size_mismatch"
    if provenance_error is not None:
        return {
            "schema_version": "staff_greeting_subject_truth_report_v1",
            "truth_schema_version": fixture["schema_version"],
            "source_sha256": expected_sha256,
            "source_provenance_status": "invalid",
            "reason": provenance_error,
            "status": "fail",
            "keyframe_count": 0,
            "subject_tracks": {},
            "frames": [],
        }

    frame_reports: list[dict[str, Any]] = []
    for truth_frame in fixture.get("frames", []):
        truth_pts = float(truth_frame["pts_seconds"])
        candidate_row = min(
            rows, key=lambda row: abs(float(row["pts_seconds"]) - truth_pts)
        )
        candidate_pts = float(candidate_row["pts_seconds"])
        pts_delta = abs(candidate_pts - truth_pts)
        subject_reports: list[dict[str, Any]] = []
        provisional_matches: dict[str, list[dict[str, Any]]] = {}
        if pts_delta <= max_pts_delta:
            for subject in truth_frame.get("subjects", []):
                provisional_matches[subject["subject"]] = _subject_candidates(
                    subject["bbox"],
                    candidate_row.get("person_tracks", []),
                    min_iou=min_iou,
                )

        chosen_tokens: dict[str, list[str]] = {}
        for subject_name, candidates in provisional_matches.items():
            if not candidates:
                continue
            if (
                len(candidates) > 1
                and candidates[0]["iou"] - candidates[1]["iou"]
                < ambiguity_margin
            ):
                continue
            token = candidates[0]["track"]["track_token"]
            chosen_tokens.setdefault(token, []).append(subject_name)

        role_items = candidate_row.get("observations") or candidate_row.get(
            "roles", []
        )
        observations = {item["track_token"]: item for item in role_items}
        vest_assigned_tokens = {
            item.get("assigned_track_token")
            for item in candidate_row.get("vest_associations", [])
            if item.get("status") == "assigned"
            and item.get("assigned_track_token") is not None
        }
        for subject in truth_frame.get("subjects", []):
            subject_name = subject["subject"]
            base = {
                "subject": subject_name,
                "expected_role": subject["expected_role"],
                "truth_bbox": subject["bbox"],
                "match_status": "unresolved",
                "status": "unresolved",
                "reason": None,
                "track_id": None,
                "track_token": None,
                "match_iou": None,
                "observed_role": None,
                "vest_hits": None,
                "required_hits": None,
                "vest_assigned": False,
            }
            if pts_delta > max_pts_delta:
                base["reason"] = "candidate_frame_not_found"
                subject_reports.append(base)
                continue
            candidates = provisional_matches.get(subject_name, [])
            if not candidates:
                base["reason"] = "no_spatial_match"
                subject_reports.append(base)
                continue
            if (
                len(candidates) > 1
                and candidates[0]["iou"] - candidates[1]["iou"]
                < ambiguity_margin
            ):
                base["match_status"] = "ambiguous"
                base["reason"] = "ambiguous_spatial_match"
                subject_reports.append(base)
                continue
            track = candidates[0]["track"]
            token = track["track_token"]
            if len(chosen_tokens.get(token, [])) > 1:
                base["match_status"] = "ambiguous"
                base["reason"] = "track_matches_multiple_subjects"
                subject_reports.append(base)
                continue
            observation = observations.get(token)
            if observation is None:
                base["reason"] = "matched_track_missing_role_observation"
                subject_reports.append(base)
                continue

            observed_role = observation.get("role")
            vest_hits = int(observation.get("vest_hits", 0))
            required_hits = int(observation.get("required_hits", 3))
            vest_assigned = token in vest_assigned_tokens
            base.update(
                {
                    "match_status": "matched",
                    "track_id": track["track_id"],
                    "track_token": token,
                    "match_iou": candidates[0]["iou"],
                    "observed_role": observed_role,
                    "vest_hits": vest_hits,
                    "required_hits": required_hits,
                    "vest_assigned": vest_assigned,
                }
            )
            if subject["expected_role"] == "customer":
                if observed_role == "staff":
                    base.update(
                        status="fail", reason="expected_customer_observed_staff"
                    )
                elif vest_hits >= required_hits:
                    base.update(
                        status="fail", reason="customer_reached_staff_hit_threshold"
                    )
                elif vest_assigned:
                    base.update(
                        status="fail", reason="vest_evidence_assigned_to_customer"
                    )
                else:
                    base.update(status="pass", reason="customer_role_preserved")
            elif subject["expected_role"] == "staff":
                if observed_role != "staff":
                    base.update(status="fail", reason="expected_staff_role_missing")
                elif not vest_assigned:
                    base.update(status="fail", reason="staff_vest_evidence_missing")
                else:
                    base.update(status="pass", reason="staff_role_and_vest_preserved")
            else:
                raise ValueError(
                    f"unsupported expected_role={subject['expected_role']!r}"
                )
            subject_reports.append(base)
        frame_reports.append(
            {
                "truth_pts_seconds": truth_pts,
                "candidate_pts_seconds": candidate_pts,
                "pts_delta_seconds": pts_delta,
                "subjects": subject_reports,
            }
        )

    statuses = [
        subject["status"]
        for frame in frame_reports
        for subject in frame["subjects"]
    ]
    status = (
        "fail"
        if "fail" in statuses
        else "unresolved"
        if "unresolved" in statuses
        else "pass"
    )
    subject_tracks: dict[str, list[int]] = {}
    for frame in frame_reports:
        for subject in frame["subjects"]:
            if subject["track_id"] is not None:
                subject_tracks.setdefault(subject["subject"], []).append(
                    subject["track_id"]
                )
    return {
        "schema_version": "staff_greeting_subject_truth_report_v1",
        "truth_schema_version": fixture["schema_version"],
        "source_sha256": fixture.get("source_sha256"),
        "source_provenance_status": "verified",
        "reason": None,
        "status": status,
        "keyframe_count": len(frame_reports),
        "subject_tracks": {
            subject: sorted(set(track_ids))
            for subject, track_ids in sorted(subject_tracks.items())
        },
        "frames": frame_reports,
    }


def _greeting_snapshot(core: OfficialGreetingCore) -> list[dict[str, Any]]:
    snapshots = []
    for token, state in sorted(core._greetings.items()):
        snapshots.append(
            {
                "customer_track_token": token,
                "greeting_state": state.stage.value,
                "pair_count": len(state.pairs),
                "active_staff_track_token": state.active_staff_track_token,
                "customer_in_greeting_area": state.customer_in_greeting_area,
                "pairs": [
                    {
                        "staff_track_token": staff_token,
                        "staff_in_greeting_area": pair.staff_in_greeting_area,
                    }
                    for staff_token, pair in sorted(state.pairs.items())
                ],
            }
        )
    return snapshots


class _DiagnosticGreetingCore(OfficialGreetingCore):
    """Read-only snapshots around the real Core pair transition."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.last_pair_diagnostics: list[dict[str, Any]] = []

    def observe(self, observed_at, observations, entries):
        self.last_pair_diagnostics = []
        return super().observe(observed_at, observations, entries)

    def _observe_pair(self, state, customer, staff, observed_at):
        event = super()._observe_pair(state, customer, staff, observed_at)
        pair = state.pairs.get(staff.track_token)
        if pair is None:
            return event
        recent = list(pair.observations)[-self.config.greeting_trend_window_samples :]
        distances = [item[3] for item in recent]
        decreases = sum(
            current < previous - 1e-6
            for previous, current in zip(distances, distances[1:])
        )
        trend_comparisons = max(0, len(distances) - 1)
        trend_required_decreases = math.ceil(
            trend_comparisons * self.config.greeting_trend_decreasing_ratio
        )
        trend_reduction = (
            distances[0] - distances[-1] if len(distances) >= 2 else 0.0
        )
        trend_pass = _distance_trend_is_decreasing(
            pair.observations,
            window_samples=self.config.greeting_trend_window_samples,
            minimum_samples=self.config.greeting_trend_minimum_samples,
            minimum_reduction_metres=(
                self.config.greeting_trend_minimum_reduction_metres
            ),
            decreasing_ratio=self.config.greeting_trend_decreasing_ratio,
        )
        distance = pair.observations[-1][3]
        reduction = pair.initial_distance - pair.minimum_distance
        toward_threshold = (
            self.config.greeting_staff_toward_customer_metres
            if self.config.greeting_staff_toward_customer_metres is not None
            else self.config.greeting_staff_travel_metres
        )
        demo_confirmed = self._demo_trajectory_confirmed(pair)
        staff_eligible = pair.staff_in_greeting_area or demo_confirmed
        active_approach = pair.approach_confirmed or demo_confirmed
        sustained_seconds = (
            (observed_at - pair.within_since).total_seconds()
            if pair.within_since is not None
            else 0.0
        )
        terminal_assessment = event.assessment if event is not None else None
        self.last_pair_diagnostics.append(
            {
                "observed_at": observed_at.isoformat(),
                "customer_track_token": customer.track_token,
                "staff_track_token": staff.track_token,
                "greeting_stage": state.stage.value,
                "customer_in_greeting_area": state.customer_in_greeting_area,
                "staff_in_greeting_area": pair.staff_in_greeting_area,
                "customer_foot_point": customer.foot_point,
                "staff_foot_point": staff.foot_point,
                "customer_world_point": customer.world_point,
                "staff_world_point": staff.world_point,
                "distance_metres": distance,
                "initial_distance_metres": pair.initial_distance,
                "minimum_distance_metres": pair.minimum_distance,
                "distance_reduction_metres": reduction,
                "staff_origin": pair.staff_origin,
                "staff_travel_metres": pair.maximum_travel,
                "staff_toward_customer_metres": pair.maximum_toward_customer,
                "distance_trend_sample_count": len(distances),
                "distance_trend_reduction": trend_reduction,
                "distance_trend_decreasing_count": decreases,
                "distance_trend_required_decreases": trend_required_decreases,
                "distance_trend_decreasing_ratio": (
                    decreases / trend_comparisons if trend_comparisons else 0.0
                ),
                "distance_trend_pass": trend_pass,
                "approach_confirmed": pair.approach_confirmed,
                "staff_eligible": staff_eligible,
                "within_since": (
                    pair.within_since.isoformat()
                    if pair.within_since is not None
                    else None
                ),
                "sustained_seconds": sustained_seconds,
                "customer_confidence": customer.confidence,
                "staff_confidence": staff.confidence,
                "staff_role_confidence": staff.role_confidence,
                "pair_confidence": pair.qualifying_confidence,
                "lifetime_confidence": pair.lifetime_confidence,
                "qualifying_confidence": pair.qualifying_confidence,
                "confirmed_confidence_threshold": self.config.confirmed_confidence,
                "terminal_assessment": terminal_assessment,
                "terminal_reason": (
                    (event.details or {}).get("reason") if event is not None else None
                ),
                "gates": {
                    "G1_customer_in_greeting_area": state.customer_in_greeting_area,
                    "G2_staff_eligible": staff_eligible,
                    "G3_staff_travel": (
                        pair.maximum_travel
                        >= self.config.greeting_staff_travel_metres
                    ),
                    "G4_staff_toward_customer": (
                        pair.maximum_toward_customer >= toward_threshold
                    ),
                    "G5_minimum_distance_reduction": (
                        reduction >= self.config.minimum_distance_reduction_metres
                    ),
                    "G6_distance_trend": trend_pass,
                    "G7_approach_confirmed": active_approach,
                    "G8_greeting_distance": (
                        distance <= self.config.greeting_distance_metres
                    ),
                    "G9_sustained_duration": (
                        sustained_seconds >= self.config.greeting_duration_seconds
                    ),
                    "G10_confirmed_confidence": (
                        pair.qualifying_confidence is not None
                        and pair.qualifying_confidence
                        >= self.config.confirmed_confidence
                    ),
                },
            }
        )
        return event


def _capture(args: argparse.Namespace) -> list[dict[str, Any]]:
    camera = load_reception_camera_config(args.camera_config, args.camera_id)
    reader = OpenCVPTSReader(args.source, camera.calibration_image_size)
    metadata = reader.validate()
    if metadata["presentation_timeline_status"] != "verified":
        raise SystemExit("presentation timeline is not verified")
    source_sha256 = _sha256_file(args.source)
    adapter = BehaviorVisionAdapter(
        store_id=camera.store_id,
        camera_id=camera.camera_id,
        person_model_path=str(args.person_model),
        staff_vest_model_path=str(args.vest_model),
        pose_model_path=str(args.pose_model),
        tracker=args.tracker,
        device=args.device,
        person_inference_imgsz=args.person_imgsz,
        staff_vest_enabled=True,
        ground_projector=camera.ground_projector,
        entry_trigger=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        calibration_status=camera.calibration_status,
        calibration_image_size=camera.calibration_image_size,
        identity_resolver=lambda _track_id: None,
        runtime_id="staff-role-diagnostic",
    )
    monitor = BehaviorMonitor(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        config=camera.monitor_config,
    )
    core = _DiagnosticGreetingCore(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        config=camera.monitor_config,
        calibration_status=camera.calibration_status,
        ground_calibration_available=True,
    )
    epoch = datetime(2000, 1, 1)
    rows = []
    for frame, pts_seconds, source_seq in reader.frames():
        if pts_seconds > args.end_seconds:
            break
        observed_at = epoch + timedelta(seconds=pts_seconds)
        observations = adapter.process_frame(frame)
        entries, _ = monitor.process(observed_at, observations)
        events, cancellations = core.observe(observed_at, observations, entries)
        if pts_seconds < args.start_seconds:
            continue
        roles = {item.track_token: item for item in adapter.last_resolved_roles}
        rows.append(
            {
                "source_seq": source_seq,
                "pts_seconds": pts_seconds,
                "source_sha256": source_sha256,
                "frame_width": int(metadata["width"]),
                "frame_height": int(metadata["height"]),
                "person_tracks": [asdict(item) for item in adapter.last_person_tracks],
                "vest_detections": [asdict(item) for item in adapter.last_vest_detections],
                "vest_associations": [
                    asdict(item)
                    for item in adapter.role_resolver.last_association_diagnostics
                ],
                "roles": [asdict(item) for item in adapter.last_resolved_roles],
                "observations": [
                    {
                        "track_id": item.track_id,
                        "track_token": item.track_token,
                        "role": item.role,
                        "foot_point": item.foot_point,
                        "world_point": item.world_point,
                        "customer_in_greeting_area": (
                            item.role == "customer"
                            and camera.greeting_area.contains(item.foot_point)
                        ),
                        "staff_in_greeting_area": (
                            item.role == "staff"
                            and camera.greeting_area.contains(item.foot_point)
                        ),
                        "role_source": roles[item.track_token].role_source,
                        "role_confidence": roles[item.track_token].role_confidence,
                        "vest_hits": roles[item.track_token].vest_hits,
                        "role_window_size": roles[item.track_token].window_size,
                        "required_hits": adapter.role_resolver.staff_hit_frames,
                    }
                    for item in observations
                ],
                "greeting": _greeting_snapshot(core),
                "greeting_pair_diagnostics": core.last_pair_diagnostics,
                "greeting_profile": asdict(camera.monitor_config),
                "events": [item.event_key for item in events],
                "terminal_events": [
                    {
                        "event_key": item.event_key,
                        "assessment": item.assessment,
                        "customer_track_token": item.customer_track_token,
                        "staff_track_token": item.staff_track_token,
                        "confidence": item.confidence,
                        "details": dict(item.details or {}),
                    }
                    for item in events
                ],
                "cancellations": [
                    {
                        "customer_track_token": item.customer_track_token,
                        "reason": item.cancel_reason,
                    }
                    for item in cancellations
                ],
            }
        )
    return rows


def _replay(path: Path) -> list[dict[str, Any]]:
    resolver = PersonRoleResolver()
    replayed = []
    for row in _read_jsonl(path):
        tracks = [
            PersonTrackDetection(
                track_id=item["track_id"],
                track_token=item["track_token"],
                bbox=tuple(item["bbox"]),
                confidence=item["confidence"],
                is_frame_clipped=item.get("is_frame_clipped", False),
            )
            for item in row["person_tracks"]
        ]
        vests = [
            VestDetection(
                bbox=tuple(item["bbox"]),
                confidence=item["confidence"],
                class_name=item["class_name"],
            )
            for item in row["vest_detections"]
        ]
        roles = resolver.resolve(tracks, vests)
        replayed.append(
            {
                "source_seq": row["source_seq"],
                "pts_seconds": row["pts_seconds"],
                "source_sha256": row.get("source_sha256"),
                "frame_width": row.get("frame_width"),
                "frame_height": row.get("frame_height"),
                "person_tracks": row["person_tracks"],
                "vest_detections": row["vest_detections"],
                "vest_associations": [
                    asdict(item) for item in resolver.last_association_diagnostics
                ],
                "roles": [asdict(item) for item in roles],
            }
        )
    return replayed


def _assert_manual_truth(
    rows: list[dict[str, Any]], *, customer_track_id: int
) -> None:
    track_suffix = f":{customer_track_id}"
    observed_frames = [
        row
        for row in rows
        if any(
            track["track_token"].endswith(track_suffix)
            for track in row["person_tracks"]
        )
    ]
    if not observed_frames:
        raise SystemExit(
            f"INVALID manual_truth_track_not_observed track_id={customer_track_id}"
        )
    false_staff = [
        (row["source_seq"], row["pts_seconds"])
        for row in rows
        for role in row["roles"]
        if role["track_token"].endswith(track_suffix) and role["role"] == "staff"
    ]
    if false_staff:
        first = false_staff[0]
        raise SystemExit(
            f"RED track{customer_track_id}_customer_became_staff "
            f"first_source_seq={first[0]} first_pts_seconds={first[1]:.6f} "
            f"staff_frames={len(false_staff)}"
        )
    print(
        f"GREEN track{customer_track_id}_remained_customer "
        f"observed_frames={len(observed_frames)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--camera-id", default="entrance-1")
    parser.add_argument("--camera-config", type=Path, default=PROJECT_ROOT / "configs/examples/behavior_cameras.example.json")
    parser.add_argument("--person-model", type=Path, default=PROJECT_ROOT / "data/models/yolo11n.pt")
    parser.add_argument("--vest-model", type=Path, default=PROJECT_ROOT / "data/models/staff_vest.pt")
    parser.add_argument("--pose-model", type=Path, default=PROJECT_ROOT / "data/models/yolo11n-pose.pt")
    parser.add_argument("--tracker", default="bytetrack.yaml")
    parser.add_argument("--person-imgsz", type=int)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--start-seconds", type=float, default=64.0)
    parser.add_argument("--end-seconds", type=float, default=70.0)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assert-manual-truth", action="store_true")
    parser.add_argument("--customer-track-id", type=int, default=23)
    parser.add_argument("--subject-truth", type=Path)
    parser.add_argument("--subject-report", type=Path)
    args = parser.parse_args()
    if args.replay is None:
        if args.source is None:
            parser.error("--source is required unless --replay is used")
        rows = _capture(args)
    else:
        rows = _replay(args.replay)
    _write_jsonl(args.output, rows)
    if args.assert_manual_truth:
        _assert_manual_truth(rows, customer_track_id=args.customer_track_id)
    if args.subject_truth is not None:
        with args.subject_truth.open("r", encoding="utf-8") as handle:
            fixture = json.load(handle)
        report = validate_subject_truth(rows, fixture)
        report_path = args.subject_report or args.output.with_name(
            "staff_subject_truth_report.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"subject truth status={report['status']} report={report_path}")
        if report["status"] != "pass":
            return 2
    print(f"wrote {len(rows)} frames to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
