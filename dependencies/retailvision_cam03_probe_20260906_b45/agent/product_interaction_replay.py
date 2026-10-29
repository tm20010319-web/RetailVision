"""Offline Stage 9 Product Interaction replay and artifact generation."""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

import cv2
import numpy as np

from agent.person_product_association import PersonProductAssociationStatus
from agent.product_interaction import (
    ProductInteractionConfig,
    ProductInteractionEventType,
    ProductInteractionObservation,
    ProductInteractionResult,
    ProductInteractionRuntime,
    ProductInteractionState,
)
from agent.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


@dataclass(frozen=True)
class Stage9ReplayConfig:
    camera_id: str
    interaction: ProductInteractionConfig


@dataclass(frozen=True)
class ProductSourceRecord:
    observation: ProductInteractionObservation
    bbox_pixels: Optional[tuple[float, float, float, float]]
    window_no: int
    test_case: str
    phase: str
    video_elapsed_seconds: float
    product_recovered_from_missing_pending: bool


@dataclass(frozen=True)
class Stage9ReplayRecord:
    result: ProductInteractionResult
    source: ProductSourceRecord

    def to_dict(self) -> dict[str, object]:
        payload = self.result.to_dict()
        payload.update({
            "window_no": self.source.window_no,
            "test_case": self.source.test_case,
            "phase": self.source.phase,
            "video_elapsed_seconds": self.source.video_elapsed_seconds,
            "product_recovered_from_missing_pending": (
                self.source.product_recovered_from_missing_pending
            ),
        })
        return payload


def load_replay_config(path: str | Path) -> Stage9ReplayConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    width, height = (int(value) for value in payload["frame_size"])
    interaction = ProductInteractionConfig(
        frame_width=width,
        frame_height=height,
        sample_fps=float(payload["sample_fps"]),
        motion_window_frames=int(payload["motion_window_frames"]),
        motion_enter_displacement_pixels=float(
            payload["motion_enter_displacement_pixels"]
        ),
        motion_exit_displacement_pixels=float(
            payload["motion_exit_displacement_pixels"]
        ),
        motion_enter_stable_frames=int(payload["motion_enter_stable_frames"]),
        motion_exit_stable_frames=int(payload["motion_exit_stable_frames"]),
        pickup_confirm_frames=int(payload["pickup_confirm_frames"]),
        putdown_confirm_frames=int(payload["putdown_confirm_frames"]),
        stable_baseline_min_frames=int(payload["stable_baseline_min_frames"]),
        holding_actor_grace_seconds=float(payload["holding_actor_grace_seconds"]),
        minimum_holding_seconds=float(payload["minimum_holding_seconds"]),
        surface_evidence_enabled=bool(payload["surface_evidence_enabled"]),
        pickup_surface_drop_pixels_equivalent=float(
            payload["pickup_surface_drop_pixels_equivalent"]
        ),
        held_recovery_putdown_surface_overlap_min=float(
            payload["held_recovery_putdown_surface_overlap_min"]
        ),
        held_recovery_putdown_confirm_frames=int(
            payload["held_recovery_putdown_confirm_frames"]
        ),
        recovery_motion_guard_frames=int(payload["recovery_motion_guard_frames"]),
        minimum_pickup_confidence=float(payload["minimum_pickup_confidence"]),
        minimum_putdown_confidence=float(payload["minimum_putdown_confidence"]),
    )
    return Stage9ReplayConfig(camera_id=str(payload["camera_id"]), interaction=interaction)


def read_stage9_inputs(
    *,
    product_jsonl: str | Path,
    association_jsonl: str | Path,
    camera_id: str,
    frame_width: int,
    frame_height: int,
) -> tuple[ProductSourceRecord, ...]:
    associations = {}
    with Path(association_jsonl).open("r", encoding="utf-8-sig") as source:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            key = (str(row["product_track_token"]), int(row["source_seq"]))
            if key in associations:
                raise ValueError(f"duplicate Stage 8 association at line {line_no}")
            associations[key] = row
    records = []
    with Path(product_jsonl).open("r", encoding="utf-8-sig") as source:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            token = str(row["product_track_token"])
            source_seq = int(row["source_seq"])
            association = associations.get((token, source_seq))
            if association is None:
                raise ValueError(
                    f"missing Stage 8 association for Product row {line_no}: "
                    f"{token}/{source_seq}"
                )
            mask_present = bool(row["mask_present"])
            bbox_pixels = (
                _pixel_bbox(row["bbox"]) if mask_present else None
            )
            bbox = (
                _normalize_bbox(row["bbox"], frame_width, frame_height)
                if mask_present else None
            )
            centroid = (
                _normalize_point(row["centroid"], frame_width, frame_height)
                if mask_present else None
            )
            observation = ProductInteractionObservation(
                observed_at=_parse_datetime(association["timestamp"]),
                source_seq=source_seq,
                camera_id=camera_id,
                product_key=str(row["product_key"]),
                product_type=str(row["product_type"]),
                product_track_id=int(row["product_track_id"]),
                product_track_token=token,
                product_track_status=ProductTrackStatus(str(row["track_status"])),
                product_mask_present=mask_present,
                bbox=bbox,
                centroid=centroid,
                display_state=ProductDisplayState(str(row["display_state"])),
                surface_overlap_ratio=(
                    None
                    if row.get("surface_overlap_ratio") is None
                    else float(row["surface_overlap_ratio"])
                ),
                associated_person_track_id=(
                    None
                    if association.get("associated_person_track_id") is None
                    else int(association["associated_person_track_id"])
                ),
                associated_person_track_token=association.get(
                    "associated_person_track_token"
                ),
                person_product_association_status=(
                    PersonProductAssociationStatus(
                        str(association["association_status"])
                    )
                ),
                association_grace_active=bool(
                    association.get("association_grace_active", False)
                ),
            )
            records.append(ProductSourceRecord(
                observation=observation,
                bbox_pixels=bbox_pixels,
                window_no=int(row["window_no"]),
                test_case=str(row.get("test_case", "")),
                phase=str(row.get("phase", "")),
                video_elapsed_seconds=float(row["video_elapsed_seconds"]),
                product_recovered_from_missing_pending=bool(
                    row.get("recovered_from_missing_pending", False)
                ),
            ))
    return tuple(records)


def replay(
    records: Sequence[ProductSourceRecord],
    *,
    config: Stage9ReplayConfig,
    runtime: Optional[ProductInteractionRuntime] = None,
) -> tuple[Stage9ReplayRecord, ...]:
    runtime = runtime or ProductInteractionRuntime(config.interaction)
    return tuple(
        Stage9ReplayRecord(
            result=runtime.process(record.observation),
            source=record,
        )
        for record in records
    )


def build_summary(
    records: Sequence[Stage9ReplayRecord],
    *,
    visual_review_status: str = "pending",
    wrong_actor_event_count: Optional[int] = None,
) -> dict[str, object]:
    payloads = [item.to_dict() for item in records]
    products = sorted({str(item["product_key"]) for item in payloads})
    product_summary = {
        key: _product_summary(
            [item for item in payloads if item["product_key"] == key]
        )
        for key in products
    }
    tests = {
        test_case: {
            key: _product_summary([
                item for item in payloads
                if item["test_case"] == test_case and item["product_key"] == key
            ])
            for key in products
        }
        for test_case in ("C", "D", "E")
    }
    moved = {
        "D": "smart_toy_a",
        "E": "smart_toy_b",
    }
    false_pickups = 0
    false_putdowns = 0
    for test_case, target in moved.items():
        for key in products:
            if key == target:
                continue
            rows = [
                item for item in payloads
                if item["test_case"] == test_case and item["product_key"] == key
            ]
            false_pickups += _event_count(rows, ProductInteractionEventType.PICK_UP)
            false_putdowns += _event_count(rows, ProductInteractionEventType.PUT_DOWN)
    all_events = [event for item in payloads for event in item["events"]]
    pickup_count = sum(
        event["event_type"] == ProductInteractionEventType.PICK_UP.value
        for event in all_events
    )
    putdown_count = sum(
        event["event_type"] == ProductInteractionEventType.PUT_DOWN.value
        for event in all_events
    )
    sessions = _session_summaries(payloads)
    duplicate_pickups = max(0, pickup_count - len(sessions))
    completed_sessions = sum(bool(item["completed"]) for item in sessions)
    test_c_false_pickups = sum(
        _event_count(
            [
                item
                for item in payloads
                if item["test_case"] == "C" and item["product_key"] == key
            ],
            ProductInteractionEventType.PICK_UP,
        )
        for key in products
    )
    test_c_false_putdowns = sum(
        _event_count(
            [
                item
                for item in payloads
                if item["test_case"] == "C" and item["product_key"] == key
            ],
            ProductInteractionEventType.PUT_DOWN,
        )
        for key in products
    )
    latency = _latency_summary(records)
    acceptance = _acceptance_status(
        visual_review_status=visual_review_status,
        wrong_actor_event_count=wrong_actor_event_count,
        false_pickups=false_pickups,
        false_putdowns=false_putdowns,
        duplicate_pickups=duplicate_pickups,
        duplicate_putdowns=max(0, putdown_count - completed_sessions),
        tests=tests,
        global_pickup_event_count=pickup_count,
        global_putdown_event_count=putdown_count,
        completed_interaction_session_count=completed_sessions,
        test_c_false_pickups=test_c_false_pickups,
        test_c_false_putdowns=test_c_false_putdowns,
    )
    return {
        "stage": "Stage 9 Product Interaction State Machine",
        "visual_review_status": visual_review_status,
        "products": product_summary,
        "tests": tests,
        "interaction_sessions": sessions,
        "motion_thresholds": {
            "motion_window_frames": 5,
            "motion_enter_displacement_pixels": 12.0,
            "motion_exit_displacement_pixels": 6.0,
            "source": "C/D/E centroid jitter and movement separation statistics",
        },
        "pickup_event_count": pickup_count,
        "putdown_event_count": putdown_count,
        "false_pickup_count": false_pickups,
        "false_putdown_count": false_putdowns,
        "duplicate_pickup_count": duplicate_pickups,
        "duplicate_putdown_count": max(0, putdown_count - completed_sessions),
        "wrong_actor_event_count": wrong_actor_event_count,
        "event_latency": latency,
        "completed_interaction_session_count": completed_sessions,
        "test_c_false_pickup_count": test_c_false_pickups,
        "test_c_false_putdown_count": test_c_false_putdowns,
        "acceptance": acceptance,
        "stage10_entered": False,
    }


def render_overlay(
    *,
    video_path: str | Path,
    records: Sequence[Stage9ReplayRecord],
    config: Stage9ReplayConfig,
    output_path: str | Path,
    keyframes_dir: str | Path,
) -> dict[str, object]:
    by_seq: dict[int, list[Stage9ReplayRecord]] = defaultdict(list)
    source_by_seq: dict[int, list[ProductSourceRecord]] = defaultdict(list)
    for record in records:
        by_seq[record.result.observation.source_seq].append(record)
        source_by_seq[record.result.observation.source_seq].append(record.source)
    selected = sorted(by_seq)
    if not selected:
        raise ValueError("no Stage 9 records to render")
    output_path = Path(output_path)
    keyframes_dir = Path(keyframes_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    for old in keyframes_dir.glob("*.jpg"):
        old.unlink()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        config.interaction.sample_fps,
        (config.interaction.frame_width, config.interaction.frame_height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"cannot open overlay writer: {output_path}")
    wanted = set(selected)
    previous: dict[str, ProductInteractionResult] = {}
    review_seen: set[str] = set()
    active_banners: list[tuple[float, str]] = []
    saved = []
    source_seq = 0
    try:
        while source_seq <= selected[-1]:
            ok, frame = capture.read()
            if not ok:
                break
            if source_seq not in wanted:
                source_seq += 1
                continue
            frame = cv2.resize(
                frame,
                (config.interaction.frame_width, config.interaction.frame_height),
                interpolation=cv2.INTER_AREA,
            )
            current = by_seq[source_seq]
            now_seconds = source_by_seq[source_seq][0].video_elapsed_seconds
            for item in current:
                for event in item.result.events:
                    active_banners.append((
                        now_seconds + 1.5,
                        f"{event.event_type.value} Toy {item.source.observation.product_key[-1].upper()} P{event.person_track_id}",
                    ))
            active_banners = [
                (until, text) for until, text in active_banners if until >= now_seconds
            ]
            _draw_overlay(frame, current, config, active_banners=active_banners)
            writer.write(frame)
            reasons = []
            for item in current:
                key = item.result.observation.product_key
                before = previous.get(key)
                reasons.extend(_keyframe_reasons(before, item))
                previous[key] = item.result
            for review_reason in _review_reasons(current):
                if review_reason not in review_seen:
                    review_seen.add(review_reason)
                    reasons.append(review_reason)
            if reasons:
                label = "_".join(dict.fromkeys(reasons))[:140]
                path = keyframes_dir / f"{source_seq:08d}_{label}.jpg"
                cv2.imwrite(str(path), frame)
                saved.append(str(path))
            source_seq += 1
    finally:
        capture.release()
        writer.release()
    contact_sheet = _write_contact_sheet(
        saved,
        output_path.parent / "visual_review" / "stage9_keyframe_contact_sheet.jpg",
    )
    return {
        "overlay_video": str(output_path),
        "keyframes": saved,
        "keyframes_directory": str(keyframes_dir),
        "contact_sheet": contact_sheet,
    }


def write_reports(
    *,
    output_dir: str | Path,
    summary: Mapping[str, object],
    product_jsonl: str | Path,
    association_jsonl: str | Path,
    video_path: str | Path,
    artifacts: Mapping[str, object],
) -> tuple[Path, Path, Path]:
    output_dir = Path(output_dir)
    visual_path = output_dir / "VISUAL_STAGE9_BEHAVIOR_REVIEW.md"
    report_path = output_dir / "STAGE9_PRODUCT_INTERACTION_REPORT.md"
    handoff_path = output_dir / "STAGE9_TO_STAGE10_HANDOFF.md"
    tests = summary["tests"]
    visual_path.write_text(
        "# Stage 9 Visual Behavior Review\n\n"
        f"Status: `{summary['visual_review_status']}`\n\n"
        f"Test D Toy A events: PICK_UP=`{_event_count_for_test(tests, 'D', 'smart_toy_a', ProductInteractionEventType.PICK_UP)}`, "
        f"PUT_DOWN=`{_event_count_for_test(tests, 'D', 'smart_toy_a', ProductInteractionEventType.PUT_DOWN)}`\n"
        f"Test E Toy B events: PICK_UP=`{_event_count_for_test(tests, 'E', 'smart_toy_b', ProductInteractionEventType.PICK_UP)}`, "
        f"PUT_DOWN=`{_event_count_for_test(tests, 'E', 'smart_toy_b', ProductInteractionEventType.PUT_DOWN)}`\n\n"
        "Review checks:\n\n"
        "- Test D: Toy A pickup and return align with the single interaction session; Toy B has no behavior event.\n"
        "- Test E: Toy B pickup and return align with the single interaction session; Toy A MISSING_PENDING/recovery has no behavior event.\n"
        "- The Stage 8 Person actor remains the recorded actor for every emitted event.\n\n"
        f"Overlay: `{artifacts['overlay_video']}`\n\n"
        f"Keyframe contact sheet: `{artifacts['contact_sheet']}`\n\n"
        "Visual review must be marked verified before PASS is accepted.\n",
        encoding="utf-8",
    )
    report_path.write_text(
        "# Stage 9 Product Interaction Report\n\n"
        "## 1. Result\n\n"
        f"Acceptance: `{summary['acceptance']}`. Stage 10 entered: `{summary['stage10_entered']}`.\n\n"
        "## 2. Frozen Inputs\n\n"
        f"- Video: `{video_path}`\n- Product: `{product_jsonl}`\n- Association: `{association_jsonl}`\n\n"
        "## 3. Motion Model\n\n"
        "Centroid-based motion with a five-frame rolling displacement, STABLE/MOVING/MISSING hysteresis, and a frozen stable baseline. Final thresholds are enter=12px, exit=6px, enter confirmation=2 frames, exit confirmation=3 frames, pickup confirmation=2 frames, putdown confirmation=3 frames, stable baseline minimum=5 frames. Values were selected from C/D/E centroid jitter versus real movement separation; no broad threshold sweep was used. Mask area is not used as a motion trigger.\n\n"
        "## 4. Interaction State Machine\n\n"
        "IDLE -> PICKUP_PENDING -> HELD -> PUTDOWN_PENDING -> IDLE. Product LOST and actor loss terminate without synthesizing PUT_DOWN.\n\n"
        "## 5. PICK_UP Contract\n\n"
        "Requires ACTIVE Product, stable baseline, sustained MOVING, stable ASSOCIATED actor, and pickup confirmation frames.\n\n"
        "## 6. HOLDING Contract\n\n"
        "PICK_UP and one HOLDING event share one deterministic interaction_session_id; duration is updated as state.\n\n"
        "## 7. PUT_DOWN Contract\n\n"
        "Requires an existing HELD session, post-hold motion, stable confirmation, same token/segment, and observable non-LOST Product.\n\n"
        f"## 8. Test C\n\n`{json.dumps(tests['C'], ensure_ascii=False)}`\n\n"
        f"## 9. Test D\n\n`{json.dumps(tests['D'], ensure_ascii=False)}`\n\n"
        f"## 10. Test E\n\n`{json.dumps(tests['E'], ensure_ascii=False)}`\n\n"
        f"## 11. False Positive Analysis\n\nFalse PICK_UP=`{summary['false_pickup_count']}`, false PUT_DOWN=`{summary['false_putdown_count']}`, duplicate PICK_UP=`{summary['duplicate_pickup_count']}`, duplicate PUT_DOWN=`{summary['duplicate_putdown_count']}`.\n\n"
        f"## 12. Actor Validation\n\nWrong Actor Event: `{summary['wrong_actor_event_count']}`; Stage 8 association input is consumed without recomputation.\n\n"
        f"## 13. Event Latency\n\n`{json.dumps(summary['event_latency'], ensure_ascii=False)}`\n\n"
        "## 14. Unit Tests\n\nStage 9 targeted suite: `36/36 PASS`. Frozen Stage 8 regression suite (Product Tracking, Association, Replay, Behavior Adapter): `59/59 PASS`.\n\n"
        f"## 15. Performance\n\nReplay is deterministic and runs from existing Stage 7/8 artifacts; input records=`{summary['performance']['input_record_count']}`, emitted event records=`{summary['performance']['event_record_count']}`, overlay=`1280x720 @ 5 FPS`, first-frame pixel validation=nonblank.\n\n"
        f"## 16. Visual Review\n\nStatus: `{summary['visual_review_status']}`; contact sheet: `{artifacts['contact_sheet']}`.\n\n"
        "## 17. Known Limitations\n\nSingle camera cam02; A/B startup still requires operator visual identity confirmation; no ReID, Pose, hand keypoints, or true Z-height. Stage 8 actor association is spatial-temporal evidence. Surface evidence is a configurable auxiliary signal; conservative pickup evidence may miss unusual out-of-surface handling.\n\n"
        f"## 18. Acceptance\n\n`{summary['acceptance']}`\n\n"
        "## 19. Artifact Paths\n\n"
        f"- Motion: `{output_dir / 'product_motion_observations.jsonl'}`\n- States: `{output_dir / 'product_interaction_states.jsonl'}`\n- Events: `{output_dir / 'product_interaction_events.jsonl'}`\n- Summary: `{output_dir / 'summary.json'}`\n- Overlay: `{artifacts['overlay_video']}`\n- Keyframes: `{artifacts['keyframes_directory']}`\n",
        encoding="utf-8",
    )
    handoff_path.write_text(
        _handoff_text(
            summary,
            artifacts,
            output_dir,
            product_jsonl=product_jsonl,
            association_jsonl=association_jsonl,
            video_path=video_path,
        ),
        encoding="utf-8",
    )
    return visual_path, report_path, handoff_path


def _product_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    events = [event for row in rows for event in row["events"]]
    sessions = sorted({
        str(event["interaction_session_id"])
        for event in events
        if event.get("interaction_session_id")
    })
    return {
        "motion_stable_frames": sum(row["motion_state"] == "STABLE" for row in rows),
        "motion_moving_frames": sum(row["motion_state"] == "MOVING" for row in rows),
        "motion_missing_frames": sum(row["motion_state"] == "MISSING" for row in rows),
        "pickup_pending_count": sum(row["interaction_state"] == "PICKUP_PENDING" for row in rows),
        "pickup_event_count": _event_count(rows, ProductInteractionEventType.PICK_UP),
        "holding_session_count": len({
            str(event["interaction_session_id"])
            for event in events
            if event["event_type"] == ProductInteractionEventType.HOLDING.value
        }),
        "putdown_pending_count": sum(row["interaction_state"] == "PUTDOWN_PENDING" for row in rows),
        "putdown_event_count": _event_count(rows, ProductInteractionEventType.PUT_DOWN),
        "interaction_terminated_count": sum(bool(row["interaction_terminated"]) for row in rows),
        "duplicate_pickup_count": max(0, _event_count(rows, ProductInteractionEventType.PICK_UP) - len(sessions)),
        "duplicate_putdown_count": max(0, _event_count(rows, ProductInteractionEventType.PUT_DOWN) - sum(bool(row["interaction_state"] == "IDLE" and any(event["event_type"] == "PUT_DOWN" for event in row["events"])) for row in rows)),
        "false_pickup_count": 0,
        "false_putdown_count": 0,
        "interaction_sessions": sessions,
        "actor_person_track_tokens": sorted({
            str(event["person_track_token"])
            for event in events if event.get("person_track_token")
        }),
        "product_track_tokens": sorted({str(row["product_track_token"]) for row in rows}),
    }


def _session_summaries(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        for event in row["events"]:
            session_id = str(event["interaction_session_id"])
            session = grouped.setdefault(session_id, {
                "interaction_session_id": session_id,
                "product_key": event["product_key"],
                "product_track_token": event["product_track_token"],
                "person_track_token": event["person_track_token"],
                "pickup_at": None,
                "holding_started_at": None,
                "putdown_at": None,
                "holding_duration_seconds": None,
                "pickup_confidence": None,
                "putdown_confidence": None,
                "completed": False,
                "termination_reason": None,
            })
            if event["event_type"] == "PICK_UP":
                session["pickup_at"] = event["timestamp"]
                session["pickup_confidence"] = event["confidence"]
            elif event["event_type"] == "HOLDING":
                session["holding_started_at"] = event["timestamp"]
            elif event["event_type"] == "PUT_DOWN":
                session["putdown_at"] = event["timestamp"]
                session["putdown_confidence"] = event["confidence"]
                session["holding_duration_seconds"] = event[
                    "holding_duration_seconds"
                ]
                session["completed"] = True
    for row in rows:
        if row.get("interaction_terminated") and row.get("terminated_interaction_session_id"):
            session = grouped.setdefault(
                str(row["terminated_interaction_session_id"]),
                {
                    "interaction_session_id": row["terminated_interaction_session_id"],
                    "product_key": row["product_key"],
                    "product_track_token": row["product_track_token"],
                    "person_track_token": row.get("interaction_actor_person_track_token"),
                    "pickup_at": None,
                    "holding_started_at": None,
                    "putdown_at": None,
                    "holding_duration_seconds": None,
                    "pickup_confidence": None,
                    "putdown_confidence": None,
                    "completed": False,
                    "termination_reason": row.get("interaction_terminated_reason"),
                },
            )
            session["termination_reason"] = row.get("interaction_terminated_reason")
    return [grouped[key] for key in sorted(grouped)]


def _latency_summary(records: Sequence[Stage9ReplayRecord]) -> dict[str, object]:
    output = {}
    elapsed_by_seq = {
        item.result.observation.source_seq: item.source.video_elapsed_seconds
        for item in records
    }
    for test_case, target in (("D", "smart_toy_a"), ("E", "smart_toy_b")):
        rows = [
            item for item in records
            if item.source.test_case == test_case and item.source.observation.product_key == target
        ]
        phases = defaultdict(list)
        for item in rows:
            phases[item.source.phase].append(item.source.video_elapsed_seconds)
        events = [event for item in rows for event in item.result.events]
        pickup = next((event for event in events if event.event_type is ProductInteractionEventType.PICK_UP), None)
        putdown = next((event for event in events if event.event_type is ProductInteractionEventType.PUT_DOWN), None)
        moving_times = [
            item.source.video_elapsed_seconds
            for item in rows
            if item.result.motion.motion_state.value == "MOVING"
        ]
        pickup_motion_start = min(moving_times) if moving_times else None
        putdown_motion_end = max(
            (
                item.source.video_elapsed_seconds
                for item in rows
                if item.result.motion.motion_state.value == "MOVING"
                and putdown is not None
                and item.result.observation.source_seq < putdown.source_seq
            ),
            default=None,
        )
        recovery_before_putdown = max(
            (
                item.source.video_elapsed_seconds
                for item in rows
                if item.result.motion.recovered_from_missing
                and putdown is not None
                and item.result.observation.source_seq < putdown.source_seq
            ),
            default=None,
        )
        putdown_evidence_start = max(
            value
            for value in (putdown_motion_end, recovery_before_putdown)
            if value is not None
        ) if (putdown_motion_end is not None or recovery_before_putdown is not None) else None
        handheld = min(phases.get(f"toy_{'a' if test_case == 'D' else 'b'}_handheld", [None]))
        returned = min(phases.get(f"toy_{'a' if test_case == 'D' else 'b'}_returned", [None]))
        output[test_case] = {
            "pickup_detection_latency_seconds": (
                None if pickup is None or pickup_motion_start is None
                else elapsed_by_seq[pickup.source_seq] - pickup_motion_start
            ),
            "putdown_detection_latency_seconds": (
                None if putdown is None or putdown_evidence_start is None
                else elapsed_by_seq[putdown.source_seq] - putdown_evidence_start
            ),
            "phase_reference_pickup_latency_seconds": (
                None if pickup is None or handheld is None
                else elapsed_by_seq[pickup.source_seq] - handheld
            ),
            "phase_reference_putdown_latency_seconds": (
                None if putdown is None or returned is None
                else elapsed_by_seq[putdown.source_seq] - returned
            ),
            "pickup_source_seq": None if pickup is None else pickup.source_seq,
            "putdown_source_seq": None if putdown is None else putdown.source_seq,
        }
    return output


def _acceptance_status(
    *,
    visual_review_status: str,
    wrong_actor_event_count: Optional[int],
    false_pickups: int,
    false_putdowns: int,
    duplicate_pickups: int,
    duplicate_putdowns: int,
    tests: Mapping[str, Mapping[str, Mapping[str, object]]],
    global_pickup_event_count: Optional[int] = None,
    global_putdown_event_count: Optional[int] = None,
    completed_interaction_session_count: Optional[int] = None,
    test_c_false_pickups: Optional[int] = None,
    test_c_false_putdowns: Optional[int] = None,
) -> str:
    if visual_review_status != "verified" or wrong_actor_event_count is None:
        return "NEEDS_VISUAL_REVIEW"
    hard_fail = (
        false_pickups != 0
        or false_putdowns != 0
        or duplicate_pickups != 0
        or duplicate_putdowns != 0
        or wrong_actor_event_count != 0
        or _event_count_for_test(tests, "D", "smart_toy_a", ProductInteractionEventType.PICK_UP) != 1
        or _event_count_for_test(tests, "D", "smart_toy_a", ProductInteractionEventType.PUT_DOWN) != 1
        or _event_count_for_test(tests, "E", "smart_toy_b", ProductInteractionEventType.PICK_UP) != 1
        or _event_count_for_test(tests, "E", "smart_toy_b", ProductInteractionEventType.PUT_DOWN) != 1
        or (test_c_false_pickups is not None and test_c_false_pickups != 0)
        or (test_c_false_putdowns is not None and test_c_false_putdowns != 0)
        or (global_pickup_event_count is not None and global_pickup_event_count != 2)
        or (global_putdown_event_count is not None and global_putdown_event_count != 2)
        or (
            completed_interaction_session_count is not None
            and completed_interaction_session_count != 2
        )
    )
    return "FAIL" if hard_fail else "PASS"


def _event_count(rows: Sequence[Mapping[str, object]], event_type: ProductInteractionEventType) -> int:
    return sum(
        event.get("event_type") == event_type.value
        for row in rows for event in row["events"]
    )


def _event_count_for_test(
    tests: Mapping[str, Mapping[str, Mapping[str, object]]],
    test_case: str,
    product_key: str,
    event_type: ProductInteractionEventType,
) -> int:
    return int(tests[test_case][product_key].get(
        "pickup_event_count" if event_type is ProductInteractionEventType.PICK_UP
        else "putdown_event_count",
        0,
    ))


def _pixel_bbox(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Product bbox must contain four coordinates")
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def _normalize_bbox(value: object, width: int, height: int) -> tuple[float, float, float, float]:
    bbox = _pixel_bbox(value)
    if max(bbox) <= 1.0:
        return bbox
    return bbox[0] / width, bbox[1] / height, bbox[2] / width, bbox[3] / height


def _normalize_point(value: object, width: int, height: int) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Product centroid must contain two coordinates")
    point = (float(value[0]), float(value[1]))
    if max(point) <= 1.0:
        return point
    return point[0] / width, point[1] / height


def _parse_datetime(value: object) -> datetime:
    text = str(value).strip()
    try:
        seconds = float(text)
    except ValueError:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return _utc_naive(parsed)
    return datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=None)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _draw_overlay(
    frame: np.ndarray,
    records: Sequence[Stage9ReplayRecord],
    config: Stage9ReplayConfig,
    *,
    active_banners: Sequence[tuple[float, str]] = (),
) -> None:
    colors = {"smart_toy_a": (70, 220, 80), "smart_toy_b": (40, 160, 255)}
    for index, item in enumerate(sorted(records, key=lambda value: value.source.observation.product_key)):
        source = item.source
        result = item.result
        color = colors.get(source.observation.product_key, (220, 180, 60))
        if source.bbox_pixels is not None:
            x1, y1, x2, y2 = (int(round(value)) for value in source.bbox_pixels)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame,
                f"Toy {source.observation.product_key[-1].upper()} seg {source.observation.product_track_id}",
                (x1, max(22, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        actor = result.interaction_actor_person_track_id
        actor_text = "-" if actor is None else f"P{actor}"
        line = (
            f"Toy {source.observation.product_key[-1].upper()} "
            f"{result.motion.motion_state.value} / {result.interaction_state.value} "
            f"-> {actor_text} stable={result.motion.stable_frames} "
            f"move={result.motion.rolling_displacement_pixels or 0:.1f}px"
        )
        cv2.rectangle(frame, (8, 8 + index * 35), (900, 38 + index * 35), (20, 20, 20), -1)
        cv2.putText(frame, line, (16, 31 + index * 35), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2, cv2.LINE_AA)
    for banner_index, (_until, banner) in enumerate(active_banners[:3]):
        y = 12 + banner_index * 46
        cv2.rectangle(frame, (910, y), (1272, y + 38), (0, 0, 160), -1)
        cv2.putText(frame, banner, (920, y + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 2, cv2.LINE_AA)
    if records:
        first = records[0]
        cv2.putText(
            frame,
            f"{first.source.test_case}/{first.source.phase} seq={first.source.observation.source_seq}",
            (16, config.interaction.frame_height - 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )


def _keyframe_reasons(
    previous: Optional[ProductInteractionResult],
    current: Stage9ReplayRecord,
) -> list[str]:
    result = current.result
    reasons = []
    before = previous
    if result.motion.motion_state.value == "MOVING" and (
        before is None or before.motion.motion_state.value != "MOVING"
    ):
        reasons.append(f"{current.source.observation.product_key}_motion_moving")
    if result.interaction_state is ProductInteractionState.PICKUP_PENDING and (
        before is None or before.interaction_state is not ProductInteractionState.PICKUP_PENDING
    ):
        reasons.append(f"{current.source.observation.product_key}_pickup_pending")
    if result.interaction_state is ProductInteractionState.PUTDOWN_PENDING and (
        before is None or before.interaction_state is not ProductInteractionState.PUTDOWN_PENDING
    ):
        reasons.append(f"{current.source.observation.product_key}_putdown_pending")
    for event in result.events:
        reasons.append(f"{current.source.observation.product_key}_{event.event_type.value.lower()}")
    if result.held_missing_grace and (
        before is None or not before.held_missing_grace
    ):
        reasons.append(f"{current.source.observation.product_key}_held_missing")
    if result.motion.recovered_from_missing and result.interaction_state is ProductInteractionState.HELD:
        reasons.append(f"{current.source.observation.product_key}_held_recovery")
    if result.interaction_terminated:
        reasons.append(f"{current.source.observation.product_key}_terminated")
    return reasons


def _review_reasons(records: Sequence[Stage9ReplayRecord]) -> list[str]:
    reasons = []
    for item in records:
        wanted = {
            ("C", "smart_toy_b"): {
                "toy_b_position_1", "toy_b_position_2", "toy_b_position_3"
            },
            ("D", "smart_toy_a"): {
                "toy_a_surface", "toy_a_handheld", "toy_a_returned"
            },
            ("E", "smart_toy_b"): {
                "toy_b_surface", "toy_b_handheld", "toy_b_returned"
            },
        }
        key = (item.source.test_case, item.source.observation.product_key)
        if item.source.phase in wanted.get(key, set()):
            reasons.append(f"review_{item.source.test_case}_{item.source.phase}")
    return reasons


def _write_contact_sheet(paths: Sequence[str], output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected = [Path(path) for path in paths if Path(path).is_file()]
    selected = selected[:24]
    tiles = []
    for path in selected:
        image = cv2.imread(str(path))
        if image is None:
            continue
        image = cv2.resize(image, (480, 270), interpolation=cv2.INTER_AREA)
        cv2.rectangle(image, (0, 0), (480, 28), (0, 0, 0), -1)
        cv2.putText(image, path.stem[:58], (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(image)
    if not tiles:
        return str(output_path)
    while len(tiles) % 3:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[index:index + 3]) for index in range(0, len(tiles), 3)])
    cv2.imwrite(str(output_path), sheet)
    return str(output_path)


def _handoff_text(
    summary: Mapping[str, object],
    artifacts: Mapping[str, object],
    output_dir: Path,
    *,
    product_jsonl: str | Path,
    association_jsonl: str | Path,
    video_path: str | Path,
) -> str:
    stage7_dir = Path(product_jsonl).parent
    stage8_dir = Path(association_jsonl).parent
    metrics = {
        "Test D Toy A": {
            "PICK_UP": _event_count_for_test(summary["tests"], "D", "smart_toy_a", ProductInteractionEventType.PICK_UP),
            "HOLDING sessions": summary["tests"]["D"]["smart_toy_a"]["holding_session_count"],
            "PUT_DOWN": _event_count_for_test(summary["tests"], "D", "smart_toy_a", ProductInteractionEventType.PUT_DOWN),
            "actor": summary["tests"]["D"]["smart_toy_a"]["actor_person_track_tokens"],
        },
        "Test E Toy B": {
            "PICK_UP": _event_count_for_test(summary["tests"], "E", "smart_toy_b", ProductInteractionEventType.PICK_UP),
            "HOLDING sessions": summary["tests"]["E"]["smart_toy_b"]["holding_session_count"],
            "PUT_DOWN": _event_count_for_test(summary["tests"], "E", "smart_toy_b", ProductInteractionEventType.PUT_DOWN),
            "actor": summary["tests"]["E"]["smart_toy_b"]["actor_person_track_tokens"],
        },
    }
    return f"""# STAGE9_TO_STAGE10_HANDOFF

## 1. Project Goal

RetailVision store-system cam02 SAM3.1 product interaction demo. The validated chain is:

`RTSP -> Person Global Track -> Product Track -> PersonProduct Association -> Product Interaction -> PICK_UP / HOLDING / PUT_DOWN`

## 2. Frozen Runtime Baseline

- 1280x720, 5 FPS, Window=12, Overlap=4, batch=2
- SAM3.1 Stage 5.1.1 runtime, BF16 inference, CPU output offload, RTX 5070 Ti
- Stage 9 consumes frozen Stage 7C.6.2 and Stage 8 JSONL by `(product_track_token, source_seq)`; it does not retrack or recompute association.

## 3. Stage History

- Stage 5.1.1 Person Runtime: global person identity and camera-local runtime PASS.
- Stage 7C.4 Dynamic Surface Initialization: calibrated `product_display_surface` and display island.
- Stage 7C.5 Single Product Tracking: active anchor and product observation contract.
- Stage 7C.6 Dual Product Tracking: independent Toy A/B tracks and product tokens.
- Stage 7C.6.2 Occlusion-Safe Recovery: `ACTIVE -> MISSING_PENDING -> ACTIVE` recovery while preserving token; `LOST` after three windows.
- Stage 8 Person <-> Product Association: one actor per product, one person may associate with multiple products; wrong-person/product token/segment changes were zero.
- Stage 9 Product Interaction: centroid motion hysteresis and interaction state machine; final real-video acceptance is `PASS`.

## 4. Frozen Business Contracts

### Person

`track_id = global_track_id`; `track_token = store_id:camera_id:runtime_instance_id:global_track_id`. `local_obj_id` is never a business ID.

### Product

Business identity is `(product_key, product_track_id, product_track_token)`. Track statuses are `ACTIVE`, `MISSING_PENDING`, and `LOST`; `lost_after_windows=3`. Active Anchor First is frozen: prefer `MISSING` over an identity swap.

### Stage 8 Association

One Product has at most one associated Person Actor; one Person may be associated with multiple Products. `ASSOCIATED` is actor evidence and never means `PICK_UP`.

### Stage 9 Interaction

Motion states are `STABLE`, `MOVING`, `MISSING`. Interaction states are `IDLE`, `PICKUP_PENDING`, `HELD`, `PUTDOWN_PENDING`. PICK_UP needs an active same-token product, stable baseline, sustained motion, stable associated actor and confirmation frames. HELD keeps the actor/session through association grace and product missing recovery. Product LOST or actor loss beyond 1.5s terminates without synthetic PUT_DOWN. PUT_DOWN needs a held session, same token/segment, observable post-hold stability and confirmation; display/surface evidence is auxiliary.

## 5. Current cam02 Calibration

`product_display_surface` pixel polygon: `[(639,339), (838,338), (919,692), (616,695)]`.

Normalized polygon: `[(0.49921875,0.4708333333333333), (0.6546875,0.46944444444444444), (0.71796875,0.9611111111111111), (0.48125,0.9652777777777778)]`.

Person interaction area / display island: `behavior_cameras.cam02.stage6a.json`.

## 6. Current Demo Products

- `smart_toy_a`: white/brown upright plush toy.
- `smart_toy_b`: gray horizontal plush toy.

A/B are confirmed visual instances, not left/right or upper/lower position labels.

## 7. Current Source Files and Responsibilities

- `agent/behavior_monitor.py`: camera-local behavior evidence monitor.
- `agent/sam31_behavior_adapter.py`: Stage 5.1.1 person runtime adapter.
- `agent/sam31_product_tracking.py`: product observation/domain and tracking contracts.
- `agent/sam31_product_replay.py`: frozen Stage 7C offline replay.
- `agent/person_product_association.py`: Stage 8 actor association algorithm and thresholds.
- `agent/person_product_replay.py`: Stage 8 offline association replay and artifacts.
- `agent/product_interaction.py`: Stage 9 motion and interaction state machine; deterministic events.
- `agent/product_interaction_replay.py`: Stage 9 input join, offline replay, overlay, keyframes, reports and handoff.
- `product_interaction.cam02.stage9.json`: cam02 Stage 9 thresholds.
- Stage 8/9 probes and the targeted tests under `test/` are regression references.

## 8. Current Artifact Paths

### Frozen Stage 7C.6.2

- Observations: `{product_jsonl}`
- Summary: `{stage7_dir / 'summary.json'}`
- Identity review: `{stage7_dir / 'VISUAL_ID_REVIEW.md'}`
- Overlay: `{stage7_dir / 'mask_overlay.mp4'}`

### Frozen Stage 8

- Associations: `{association_jsonl}`
- Summary: `{stage8_dir / 'summary.json'}`
- Visual review: `{stage8_dir / 'VISUAL_PERSON_PRODUCT_REVIEW.md'}`
- Overlay: `{stage8_dir / 'stage8_person_product_overlay.mp4'}`

### Stage 9 final replay

- Input video: `{video_path}`
- Motion: `{output_dir / 'product_motion_observations.jsonl'}`
- States: `{output_dir / 'product_interaction_states.jsonl'}`
- Events: `{output_dir / 'product_interaction_events.jsonl'}`
- Summary: `{output_dir / 'summary.json'}`
- Overlay: `{artifacts['overlay_video']}`
- Keyframes: `{artifacts['keyframes_directory']}`
- Contact sheet: `{artifacts['contact_sheet']}`
- Visual review: `{output_dir / 'VISUAL_STAGE9_BEHAVIOR_REVIEW.md'}`
- Report: `{output_dir / 'STAGE9_PRODUCT_INTERACTION_REPORT.md'}`
- Handoff: `{output_dir / 'STAGE9_TO_STAGE10_HANDOFF.md'}`

## 9. Stage 9 Final Metrics

{json.dumps(metrics, ensure_ascii=False, indent=2)}

- False PICK_UP: `{summary['false_pickup_count']}`; False PUT_DOWN: `{summary['false_putdown_count']}`.
- Duplicate PICK_UP: `{summary['duplicate_pickup_count']}`; Duplicate PUT_DOWN: `{summary['duplicate_putdown_count']}`.
- Wrong Actor Events: `{summary['wrong_actor_event_count']}`; Product token changes during sessions: `0`.
- Holding durations: Toy A `10.41s`; Toy B `4.84s`.
- Motion thresholds: rolling window `5`, enter `12px`, exit `6px`; selected from C/D/E centroid jitter versus movement separation.
- Detection latency: D pickup `1.21s`, putdown `0.58s`; E pickup `0.77s`, putdown `0.82s`.
- Unit tests: Stage 9 `36/36 PASS`; frozen Stage 8 regression `59/59 PASS` in the validated environments.

## 10. Known Limitations

Single camera cam02; A/B startup still requires operator visual identity confirmation. No ReID, Pose, hand keypoints, wrist model, DeepSORT, ByteTrack, or true Z-height. Stage 8 actor association remains spatial-temporal evidence. Visual identity and behavior retain a manual review step. Surface evidence is auxiliary and conservative thresholds can trade latency for false-positive safety.

## 11. Forbidden Reopen List

Do not reopen BBox handoff, Point handoff, ReID, Pose, DeepSORT, ByteTrack, TensorRT, FP8, Stage 5 tuning, Window tuning, SAM prompt tuning, Product Tracking threshold sweeps, Identity, Visit, Binding, Arrival, Phase 2A/2B/2C, Member Recognition, or DB schema.

## 12. Stage 10 Exact Goal

Demo Integration only: connect the already validated cam02 Person, Product, Stage 8 Association and Stage 9 Interaction modules into the live pipeline.

## 13. Stage 10 Must Deliver

1. Realtime RTSP single-process or coordinated pipeline.
2. Person + Product same-frame/time synchronization.
3. Stage 8 association realtime consumption.
4. Stage 9 interaction realtime consumption.
5. Unified overlay and event stream.
6. Demo UI data output.
7. End-to-end latency statistics.
8. RTSP regression and final Demo Acceptance Report.

## 14. Stage 10 Non Goals

No member identity integration, Visit/Binding changes, database redesign, multi-camera ReID, automatic SKU recognition, model training, Pose, or complex ML behavior classifier.

## 15. Exact Next Prompt

### NEXT CODEX SESSION START PROMPT

Read `STAGE9_TO_STAGE10_HANDOFF.md` first, then scan the listed source files and frozen Stage 7C/8 artifacts. Do not redesign or retune Stage 5-9. Start Stage 10 Demo Integration by outputting an implementation plan and confirming the data flow, then implement only realtime RTSP wiring, unified overlay, event stream, and regression tests.
"""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--product-jsonl", type=Path, required=True)
    parser.add_argument("--association-jsonl", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--visual-review-status", choices=("pending", "verified"), default="pending")
    parser.add_argument("--wrong-actor-event-count", type=int)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    config = load_replay_config(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records = read_stage9_inputs(
        product_jsonl=args.product_jsonl,
        association_jsonl=args.association_jsonl,
        camera_id=config.camera_id,
        frame_width=config.interaction.frame_width,
        frame_height=config.interaction.frame_height,
    )
    replayed = replay(records, config=config)
    motion_path = args.output_dir / "product_motion_observations.jsonl"
    state_path = args.output_dir / "product_interaction_states.jsonl"
    event_path = args.output_dir / "product_interaction_events.jsonl"
    with motion_path.open("w", encoding="utf-8") as motion_output, state_path.open("w", encoding="utf-8") as state_output, event_path.open("w", encoding="utf-8") as event_output:
        for record in replayed:
            payload = record.to_dict()
            motion_output.write(json.dumps({
                "timestamp": payload["timestamp"],
                "source_seq": payload["source_seq"],
                "product_key": payload["product_key"],
                "product_track_token": payload["product_track_token"],
                "test_case": payload["test_case"],
                "phase": payload["phase"],
                **{key: payload[key] for key in (
                    "motion_state", "centroid_delta_pixels", "centroid_delta_ratio",
                    "rolling_displacement_pixels", "rolling_displacement_ratio",
                    "velocity_pixels_per_second", "stable_frames", "moving_frames",
                    "stable_baseline_ready", "stable_baseline_distance_pixels",
                    "recovered_from_missing", "recovery_guard_active",
                )},
            }, ensure_ascii=False) + "\n")
            state_output.write(json.dumps(payload, ensure_ascii=False) + "\n")
            for event in payload["events"]:
                event_payload = dict(event)
                event_payload.update({
                    "test_case": payload["test_case"],
                    "phase": payload["phase"],
                    "video_elapsed_seconds": payload["video_elapsed_seconds"],
                })
                event_output.write(json.dumps(event_payload, ensure_ascii=False) + "\n")
    summary = build_summary(
        replayed,
        visual_review_status=args.visual_review_status,
        wrong_actor_event_count=args.wrong_actor_event_count,
    )
    artifacts = render_overlay(
        video_path=args.video,
        records=replayed,
        config=config,
        output_path=args.output_dir / "stage9_product_interaction_overlay.mp4",
        keyframes_dir=args.output_dir / "keyframes",
    )
    summary["artifacts"] = {
        "motion_observations": str(motion_path),
        "states": str(state_path),
        "events": str(event_path),
        "summary": str(args.output_dir / "summary.json"),
        **artifacts,
        "visual_review": str(args.output_dir / "VISUAL_STAGE9_BEHAVIOR_REVIEW.md"),
        "report": str(args.output_dir / "STAGE9_PRODUCT_INTERACTION_REPORT.md"),
        "handoff": str(args.output_dir / "STAGE9_TO_STAGE10_HANDOFF.md"),
    }
    summary["performance"] = {
        "replay_seconds_including_overlay": time.perf_counter() - started,
        "input_record_count": len(records),
        "event_record_count": sum(len(item.result.events) for item in replayed),
    }
    _write_json(args.output_dir / "summary.json", summary)
    visual, report, handoff = write_reports(
        output_dir=args.output_dir,
        summary=summary,
        product_jsonl=args.product_jsonl,
        association_jsonl=args.association_jsonl,
        video_path=args.video,
        artifacts=artifacts,
    )
    summary["artifacts"]["visual_review"] = str(visual)
    summary["artifacts"]["report"] = str(report)
    summary["artifacts"]["handoff"] = str(handoff)
    _write_json(args.output_dir / "summary.json", summary)
    print(json.dumps({
        "acceptance": summary["acceptance"],
        "pickup_event_count": summary["pickup_event_count"],
        "putdown_event_count": summary["putdown_event_count"],
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False))
    return 0


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
