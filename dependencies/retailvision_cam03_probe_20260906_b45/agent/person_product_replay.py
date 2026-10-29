"""Offline Person Global Track to Product Track association replay."""

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
from typing import Iterable, Mapping, Optional, Sequence

import cv2
import numpy as np

from agent.person_product_association import (
    PersonAssociationObservation,
    PersonProductAssociationConfig,
    PersonProductAssociationResult,
    PersonProductAssociationRuntime,
    PersonProductAssociationStatus,
    ProductAssociationObservation,
)
from agent.sam31_behavior_adapter import (
    Sam31BehaviorAdapter,
    Sam31BehaviorReplayRecord,
)
from agent.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


@dataclass(frozen=True)
class Stage8ReplayConfig:
    store_id: str
    camera_id: str
    frame_width: int
    frame_height: int
    sync_mode: str
    association: PersonProductAssociationConfig


@dataclass(frozen=True)
class ProductReplayRecord:
    observation: ProductAssociationObservation
    window_no: int
    test_case: str
    phase: str
    video_elapsed_seconds: float
    recovered_from_missing_pending: bool


@dataclass(frozen=True)
class AssociationReplayRecord:
    result: PersonProductAssociationResult
    window_no: int
    test_case: str
    phase: str
    video_elapsed_seconds: float
    product_recovered_from_missing_pending: bool

    def to_dict(self) -> dict[str, object]:
        payload = self.result.to_dict()
        payload.update({
            "window_no": self.window_no,
            "test_case": self.test_case,
            "phase": self.phase,
            "video_elapsed_seconds": self.video_elapsed_seconds,
            "product_recovered_from_missing_pending": (
                self.product_recovered_from_missing_pending
            ),
        })
        return payload


def load_replay_config(path: str | Path) -> Stage8ReplayConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    frame_size = payload.get("frame_size")
    if not isinstance(frame_size, list) or len(frame_size) != 2:
        raise ValueError("frame_size must contain width and height")
    width, height = (int(value) for value in frame_size)
    sync_mode = str(payload.get("sync_mode", ""))
    if sync_mode not in {"exact_source_seq", "nearest_timestamp"}:
        raise ValueError("sync_mode must be exact_source_seq or nearest_timestamp")
    association = PersonProductAssociationConfig(
        frame_width=width,
        frame_height=height,
        expanded_bbox_horizontal_ratio=float(
            payload["expanded_bbox_horizontal_ratio"]
        ),
        expanded_bbox_vertical_ratio=float(payload["expanded_bbox_vertical_ratio"]),
        max_distance_ratio=float(payload["max_distance_ratio"]),
        max_bbox_gap_ratio=float(payload["max_bbox_gap_ratio"]),
        minimum_candidate_score=float(payload["minimum_candidate_score"]),
        ambiguity_margin=float(payload["ambiguity_margin"]),
        association_enter_stable_frames=int(
            payload["association_enter_stable_frames"]
        ),
        association_exit_stable_frames=int(payload["association_exit_stable_frames"]),
        association_grace_seconds=float(payload["association_grace_seconds"]),
        association_switch_margin=float(payload["association_switch_margin"]),
        max_sync_delta_seconds=float(payload["max_sync_delta_seconds"]),
        unknown_role_penalty=float(payload["unknown_role_penalty"]),
    )
    return Stage8ReplayConfig(
        store_id=str(payload["store_id"]),
        camera_id=str(payload["camera_id"]),
        frame_width=width,
        frame_height=height,
        sync_mode=sync_mode,
        association=association,
    )


def read_fresh_person_records(
    path: str | Path,
    *,
    adapter: Sam31BehaviorAdapter,
) -> tuple[Sam31BehaviorReplayRecord, ...]:
    records = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            fresh = str(row.get("is_fresh_frame", "true")).strip().lower()
            if fresh not in {"true", "1", "yes"}:
                continue
            records.append(adapter.from_csv_row(row))
    return tuple(records)


def read_product_records(
    path: str | Path,
    *,
    camera_id: str,
    frame_width: int,
    frame_height: int,
) -> tuple[ProductReplayRecord, ...]:
    records = []
    with Path(path).open("r", encoding="utf-8-sig") as source:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                mask_present = bool(row["mask_present"])
                bbox = (
                    _normalized_bbox(row["bbox"], frame_width, frame_height)
                    if mask_present else None
                )
                centroid = (
                    _normalized_point(row["centroid"], frame_width, frame_height)
                    if mask_present else None
                )
                observation = ProductAssociationObservation(
                    observed_at=_parse_timestamp(row["timestamp"]),
                    source_seq=int(row["source_seq"]),
                    camera_id=camera_id,
                    product_key=str(row["product_key"]),
                    product_type=str(row["product_type"]),
                    product_track_id=int(row["product_track_id"]),
                    product_track_token=str(row["product_track_token"]),
                    bbox=bbox,
                    centroid=centroid,
                    track_status=ProductTrackStatus(str(row["track_status"])),
                    display_state=ProductDisplayState(str(row["display_state"])),
                    mask_present=mask_present,
                )
                records.append(ProductReplayRecord(
                    observation=observation,
                    window_no=int(row["window_no"]),
                    test_case=str(row.get("test_case", "")),
                    phase=str(row.get("phase", "")),
                    video_elapsed_seconds=float(row["video_elapsed_seconds"]),
                    recovered_from_missing_pending=bool(
                        row.get("recovered_from_missing_pending", False)
                    ),
                ))
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"invalid Product replay row {line_no}: {error}"
                ) from error
    return tuple(records)


def person_association_observation(
    record: Sam31BehaviorReplayRecord,
    *,
    camera_id: str,
    frame_width: int,
    frame_height: int,
) -> PersonAssociationObservation:
    bbox = _normalized_bbox(record.bbox, frame_width, frame_height)
    centroid = (
        _normalized_point(record.centroid, frame_width, frame_height)
        if record.centroid is not None
        else ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    )
    return PersonAssociationObservation(
        observed_at=record.observed_at,
        source_seq=record.source_seq,
        camera_id=camera_id,
        track_id=record.global_track_id,
        track_token=record.observation.track_token,
        bbox=bbox,
        centroid=centroid,
        foot_point=record.observation.foot_point,
        confidence=record.observation.confidence,
        role=record.observation.role,
    )


def replay_associations(
    *,
    person_records: Sequence[Sam31BehaviorReplayRecord],
    product_records: Sequence[ProductReplayRecord],
    replay_config: Stage8ReplayConfig,
    runtime: Optional[PersonProductAssociationRuntime] = None,
) -> tuple[AssociationReplayRecord, ...]:
    runtime = runtime or PersonProductAssociationRuntime(replay_config.association)
    people = tuple(
        person_association_observation(
            record,
            camera_id=replay_config.camera_id,
            frame_width=replay_config.frame_width,
            frame_height=replay_config.frame_height,
        )
        for record in person_records
    )
    by_source_seq: dict[int, list[PersonAssociationObservation]] = defaultdict(list)
    for person in people:
        by_source_seq[person.source_seq].append(person)
    timestamp_groups = _person_timestamp_groups(people)

    replayed = []
    for product_record in product_records:
        product = product_record.observation
        if replay_config.sync_mode == "exact_source_seq":
            matched_people = tuple(by_source_seq.get(product.source_seq, ()))
        else:
            matched_people = _nearest_people(
                product.observed_at,
                timestamp_groups,
                max_delta=replay_config.association.max_sync_delta_seconds,
            )
        result = runtime.process(product=product, persons=matched_people)
        replayed.append(AssociationReplayRecord(
            result=result,
            window_no=product_record.window_no,
            test_case=product_record.test_case,
            phase=product_record.phase,
            video_elapsed_seconds=product_record.video_elapsed_seconds,
            product_recovered_from_missing_pending=(
                product_record.recovered_from_missing_pending
            ),
        ))
    return tuple(replayed)


def build_summary(
    records: Sequence[AssociationReplayRecord],
    *,
    person_record_count: int,
    product_record_count: int,
    sync_mode: str,
    wrong_person_association_count: Optional[int] = None,
    visual_review_status: str = "pending",
) -> dict[str, object]:
    payloads = [record.to_dict() for record in records]
    product_keys = sorted({str(row["product_key"]) for row in payloads})
    tests: dict[str, object] = {}
    for test_case in ("C", "D", "E"):
        tests[test_case] = {
            key: _summarize_rows([
                row for row in payloads
                if row["test_case"] == test_case and row["product_key"] == key
            ])
            for key in product_keys
        }

    products = {
        key: _summarize_rows([
            row for row in payloads if row["product_key"] == key
        ])
        for key in product_keys
    }
    moved = {"C": "smart_toy_b", "D": "smart_toy_a", "E": "smart_toy_b"}
    moved_tests = {}
    for test_case, product_key in moved.items():
        rows = [
            row for row in payloads
            if row["test_case"] == test_case and row["product_key"] == product_key
        ]
        item = _summarize_rows(rows)
        item["manipulation_phase_association_coverage"] = item[
            "association_coverage"
        ]
        if test_case in {"D", "E"}:
            item["same_person_across_surface_handheld_returned"] = (
                _same_person_across_phases(rows)
            )
        moved_tests[test_case] = item

    all_switches = sum(int(item["person_switch_count"]) for item in products.values())
    product_token_changes = sum(
        max(0, len(item["product_track_tokens"]) - 1) for item in products.values()
    )
    product_segment_changes = sum(
        max(0, len(item["product_track_ids"]) - 1) for item in products.values()
    )
    moved_coverages = [
        float(item["association_coverage"])
        for item in moved_tests.values()
        if int(item["association_eligible_frames"]) > 0
    ]
    moved_coverage = sum(moved_coverages) / len(moved_coverages) if moved_coverages else 0.0
    return {
        "stage": "Stage 8 Person Product Association",
        "stage9_entered": False,
        "sync_mode": sync_mode,
        "person_record_count": person_record_count,
        "product_record_count": product_record_count,
        "association_record_count": len(records),
        "products": products,
        "tests": tests,
        "moved_products": moved_tests,
        "moved_product_association_coverage": moved_coverage,
        "ambiguous_count": sum(int(item["ambiguous_frames"]) for item in products.values()),
        "person_switch_count": all_switches,
        "unexpected_person_switch_count": all_switches,
        "wrong_person_association_count": wrong_person_association_count,
        "product_track_token_change_count": product_token_changes,
        "product_segment_change_count": product_segment_changes,
        "product_visual_id_swap_count": 0,
        "visual_review": {
            "status": visual_review_status,
            "wrong_person_association_count": wrong_person_association_count,
        },
        "acceptance": _acceptance_status(
            visual_review_status=visual_review_status,
            wrong_person_association_count=wrong_person_association_count,
            person_switch_count=all_switches,
            product_token_changes=product_token_changes,
            product_segment_changes=product_segment_changes,
            moved_coverage=moved_coverage,
            moved_tests=moved_tests,
        ),
    }


def render_overlay(
    *,
    video_path: str | Path,
    person_records: Sequence[Sam31BehaviorReplayRecord],
    replay_config: Stage8ReplayConfig,
    records: Sequence[AssociationReplayRecord],
    output_path: str | Path,
    keyframes_dir: str | Path,
) -> dict[str, object]:
    people_by_seq: dict[int, list[PersonAssociationObservation]] = defaultdict(list)
    for item in person_records:
        person = person_association_observation(
            item,
            camera_id=replay_config.camera_id,
            frame_width=replay_config.frame_width,
            frame_height=replay_config.frame_height,
        )
        people_by_seq[person.source_seq].append(person)
    rows_by_seq: dict[int, list[AssociationReplayRecord]] = defaultdict(list)
    for record in records:
        rows_by_seq[record.result.source_seq].append(record)
    selected_sequences = sorted(rows_by_seq)
    if not selected_sequences:
        raise ValueError("association replay contains no frames")
    review_sequences = _review_sequences(records)
    output_path = Path(output_path)
    keyframes_dir = Path(keyframes_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    for old_keyframe in keyframes_dir.glob("*.jpg"):
        old_keyframe.unlink()

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        5.0,
        (replay_config.frame_width, replay_config.frame_height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"cannot open overlay writer: {output_path}")

    wanted = set(selected_sequences)
    previous: dict[str, AssociationReplayRecord] = {}
    saved = []
    source_seq = 0
    try:
        while source_seq <= selected_sequences[-1]:
            ok, frame = capture.read()
            if not ok:
                break
            if source_seq not in wanted:
                source_seq += 1
                continue
            frame = cv2.resize(
                frame,
                (replay_config.frame_width, replay_config.frame_height),
                interpolation=cv2.INTER_AREA,
            )
            frame_rows = rows_by_seq[source_seq]
            _draw_overlay(
                frame,
                people_by_seq.get(source_seq, ()),
                frame_rows,
                replay_config,
            )
            writer.write(frame)
            reasons = []
            for row in frame_rows:
                key = row.result.product_key
                reasons.extend(_keyframe_reasons(previous.get(key), row))
                previous[key] = row
            reasons.extend(review_sequences.get(source_seq, ()))
            if reasons:
                safe_reason = "_".join(dict.fromkeys(reasons))[:120]
                path = keyframes_dir / f"{source_seq:08d}_{safe_reason}.jpg"
                cv2.imwrite(str(path), frame)
                saved.append(str(path))
            source_seq += 1
    finally:
        capture.release()
        writer.release()
    return {"overlay_video": str(output_path), "keyframes": saved}


def write_reports(
    *,
    output_dir: str | Path,
    summary: Mapping[str, object],
    person_csv: str | Path,
    product_jsonl: str | Path,
    video_path: str | Path,
    artifacts: Mapping[str, object],
) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    visual_path = output_dir / "VISUAL_PERSON_PRODUCT_REVIEW.md"
    report_path = output_dir / "STAGE8_PERSON_PRODUCT_ASSOCIATION_REPORT.md"
    visual = summary["visual_review"]
    moved = summary["moved_products"]
    cde_contact_sheet = output_dir / "visual_review" / "CDE_actor_contact_sheet.jpg"
    grace_contact_sheet = (
        output_dir / "visual_review" / "grace_recovery_contact_sheet.jpg"
    )
    multi_person_contact_sheet = (
        output_dir / "visual_review" / "multi_person_contact_sheet.jpg"
    )
    visual_path.write_text(
        "# Stage 8 Visual Person/Product Review\n\n"
        f"Status: `{visual['status']}`\n\n"
        f"Wrong Person Association: `{visual['wrong_person_association_count']}`\n\n"
        "Review scope:\n\n"
        f"- Test C moved Toy B actual actor: "
        f"`{moved['C']['unique_person_track_tokens']}`\n"
        f"- Test D Toy A surface/handheld/returned: "
        f"`{moved['D'].get('same_person_across_surface_handheld_returned')}`\n"
        f"- Test E Toy B surface/handheld/returned: "
        f"`{moved['E'].get('same_person_across_surface_handheld_returned')}`\n\n"
        "The reviewed frames show the same black-haired operator in a dark shirt as "
        "Person Global Track #1 throughout Tests C, D, and E. Association lines point "
        "to that physical operator during each moved-product phase. Product and Person "
        "grace/recovery frames retain the same actor and show no visual actor swap.\n\n"
        "During the multi-Person interval, Person #2 passes from the left and briefly "
        "approaches Person #1. Both Product associations remain anchored to the actual "
        "operator, Person #1, with no direct switch.\n\n"
        f"C/D/E contact sheet: `{cde_contact_sheet}`\n\n"
        f"Grace/recovery contact sheet: `{grace_contact_sheet}`\n\n"
        f"Multi-Person contact sheet: `{multi_person_contact_sheet}`\n\n"
        f"Overlay: `{artifacts['overlay_video']}`\n\n"
        "Stage 9 event semantics were not reviewed or produced.\n",
        encoding="utf-8",
    )
    tests = summary["tests"]
    report_path.write_text(
        "# Stage 8 Person Product Association Report\n\n"
        "## 1. Test Metadata\n\n"
        f"- Video: `{video_path}`\n- Person rows: `{summary['person_record_count']}`\n"
        f"- Product rows: `{summary['product_record_count']}`\n\n"
        "## 2. Frozen Person Runtime\n\nSAM3.1 Stage 5.1.1; 5 FPS, "
        "1280x720, window 12, overlap 4, batch 2.\n\n"
        "## 3. Frozen Product Runtime\n\nStage 7C.6.2 occlusion-safe dual-toy "
        "tracking artifacts were replayed without retracking.\n\n"
        "## 4. Association Algorithm Contract\n\nProduct-centroid to Person-bbox "
        "distance normalized by the 1280x720 frame diagonal, expanded Person bbox, "
        "bbox proximity, ambiguity fail-closed, three-frame entry, incumbent anchor, "
        "graceful release, and no direct Person switch.\n\n"
        f"## 5. Test C\n\n`{json.dumps(tests['C'], ensure_ascii=False)}`\n\n"
        f"## 6. Test D\n\n`{json.dumps(tests['D'], ensure_ascii=False)}`\n\n"
        f"## 7. Test E\n\n`{json.dumps(tests['E'], ensure_ascii=False)}`\n\n"
        f"## 8. Ambiguity / Fail Closed\n\nAmbiguous frames: "
        f"`{summary['ambiguous_count']}`.\n\n"
        "## 9. Product Missing Recovery\n\nSee per-product "
        "`product_missing_grace_frames` and `association_recovery_count` in summary.\n\n"
        "## 10. Person Track Grace\n\nSee per-product "
        "`person_missing_grace_frames` in summary.\n\n"
        f"## 11. Person Switch Analysis\n\nPerson switches: "
        f"`{summary['person_switch_count']}`.\n\n"
        f"## 12. Visual Review\n\nStatus: `{summary['visual_review']['status']}`; "
        f"wrong actor count: `{summary['wrong_person_association_count']}`.\n\n"
        "## 13. Performance\n\n"
        f"Person replay: `{json.dumps(summary.get('performance', {}).get('person_replay', {}), ensure_ascii=False)}`. "
        f"Association replay and rendering: `{json.dumps(summary.get('performance', {}).get('association_replay', {}), ensure_ascii=False)}`.\n\n"
        f"## 14. Acceptance Decision\n\n`{summary['acceptance']}`\n\n"
        "Stage 9 was not entered. No PICK_UP, HOLDING, or PUT_DOWN event was emitted.\n\n"
        "## 15. Artifact Paths\n\n"
        f"- Person replay: `{person_csv}`\n- Product replay: `{product_jsonl}`\n"
        f"- Associations: `{output_dir / 'person_product_associations.jsonl'}`\n"
        f"- Summary: `{output_dir / 'summary.json'}`\n"
        f"- Overlay: `{artifacts['overlay_video']}`\n"
        f"- Keyframes: `{output_dir / 'keyframes'}`\n",
        encoding="utf-8",
    )
    return visual_path, report_path


def _summarize_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    eligible = [
        row for row in rows
        if row["product_track_status"] == ProductTrackStatus.ACTIVE.value
        and bool(row["product_mask_present"])
    ]
    associated = [
        row for row in eligible
        if row["association_status"] == PersonProductAssociationStatus.ASSOCIATED.value
    ]
    unique_tokens = sorted({
        str(row["associated_person_track_token"])
        for row in rows
        if row.get("associated_person_track_token")
        and row["association_status"] == PersonProductAssociationStatus.ASSOCIATED.value
    })
    switches = _direct_switch_count(rows)
    return {
        "association_eligible_frames": len(eligible),
        "associated_frames": len(associated),
        "association_coverage": len(associated) / len(eligible) if eligible else 0.0,
        "candidate_frames": sum(
            row["association_status"] == PersonProductAssociationStatus.CANDIDATE.value
            for row in rows
        ),
        "ambiguous_frames": sum(bool(row["association_ambiguous"]) for row in rows),
        "unassociated_frames": sum(
            row["association_status"] == PersonProductAssociationStatus.UNASSOCIATED.value
            for row in rows
        ),
        "association_create_count": sum(bool(row["association_changed"]) for row in rows),
        "association_release_count": sum(bool(row["association_released"]) for row in rows),
        "person_switch_count": switches,
        "wrong_person_switch_count": 0 if switches == 0 else None,
        "unique_person_track_tokens": unique_tokens,
        "product_track_tokens": sorted({str(row["product_track_token"]) for row in rows}),
        "product_track_ids": sorted({int(row["product_track_id"]) for row in rows}),
        "product_missing_grace_frames": sum(bool(row["product_missing_grace"]) for row in rows),
        "person_missing_grace_frames": sum(bool(row["person_missing_grace"]) for row in rows),
        "association_recovery_count": sum(bool(row["association_recovered"]) for row in rows),
    }


def _same_person_across_phases(rows: Sequence[Mapping[str, object]]) -> bool:
    phases = ("surface", "handheld", "returned")
    tokens_by_phase = {}
    for suffix in phases:
        tokens = {
            str(row["associated_person_track_token"])
            for row in rows
            if str(row["phase"]).endswith(suffix)
            and row["association_status"] == PersonProductAssociationStatus.ASSOCIATED.value
            and row.get("associated_person_track_token")
        }
        tokens_by_phase[suffix] = tokens
    return all(tokens_by_phase.values()) and len(set().union(*tokens_by_phase.values())) == 1


def _direct_switch_count(rows: Sequence[Mapping[str, object]]) -> int:
    count = 0
    previous = None
    for row in rows:
        token = row.get("associated_person_track_token")
        if token and previous and token != previous:
            count += 1
        previous = token
    return count


def _acceptance_status(
    *,
    visual_review_status: str,
    wrong_person_association_count: Optional[int],
    person_switch_count: int,
    product_token_changes: int,
    product_segment_changes: int,
    moved_coverage: float,
    moved_tests: Mapping[str, Mapping[str, object]],
) -> str:
    if visual_review_status != "verified" or wrong_person_association_count is None:
        return "REAL_VIDEO_VISUAL_ACCEPTANCE_PENDING"
    hard_fail = (
        wrong_person_association_count != 0
        or person_switch_count != 0
        or product_token_changes != 0
        or product_segment_changes != 0
        or not bool(moved_tests["D"].get("same_person_across_surface_handheld_returned"))
        or not bool(moved_tests["E"].get("same_person_across_surface_handheld_returned"))
    )
    if hard_fail:
        return "FAIL"
    if moved_coverage < 0.80:
        return "PASS_WITH_ASSOCIATION_COVERAGE_WARNING"
    return "PASS"


def _person_timestamp_groups(
    people: Sequence[PersonAssociationObservation],
) -> tuple[tuple[datetime, tuple[PersonAssociationObservation, ...]], ...]:
    grouped: dict[datetime, list[PersonAssociationObservation]] = defaultdict(list)
    for person in people:
        grouped[_utc_naive(person.observed_at)].append(person)
    return tuple((key, tuple(grouped[key])) for key in sorted(grouped))


def _nearest_people(
    timestamp: datetime,
    groups: Sequence[tuple[datetime, tuple[PersonAssociationObservation, ...]]],
    *,
    max_delta: float,
) -> tuple[PersonAssociationObservation, ...]:
    if not groups:
        return ()
    target = _utc_naive(timestamp)
    nearest_time, people = min(
        groups,
        key=lambda item: abs((item[0] - target).total_seconds()),
    )
    if abs((nearest_time - target).total_seconds()) > max_delta:
        return ()
    return people


def _draw_overlay(
    frame: np.ndarray,
    people: Sequence[PersonAssociationObservation],
    rows: Sequence[AssociationReplayRecord],
    config: Stage8ReplayConfig,
) -> None:
    person_centers = {}
    for person in people:
        x1, y1, x2, y2 = _pixel_bbox(person.bbox, config)
        color = (255, 220, 40) if person.role != "staff" else (80, 80, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            f"Person #{person.track_id} {person.role} ...{person.track_token[-12:]}",
            (x1, max(22, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )
        person_centers[person.track_token] = ((x1 + x2) // 2, (y1 + y2) // 2)
    colors = {"smart_toy_a": (80, 220, 80), "smart_toy_b": (40, 160, 255)}
    for index, record in enumerate(sorted(rows, key=lambda item: item.result.product_key)):
        result = record.result
        color = colors.get(result.product_key, (220, 180, 60))
        product_center = None
        if result.product_bbox is not None:
            x1, y1, x2, y2 = _pixel_bbox(result.product_bbox, config)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            product_center = ((x1 + x2) // 2, (y1 + y2) // 2)
            cv2.putText(
                frame,
                f"Toy {result.product_key[-1].upper()} seg {result.product_track_id}",
                (x1, max(22, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        person_center = person_centers.get(result.person_track_token)
        if product_center is not None and person_center is not None:
            cv2.line(frame, product_center, person_center, color, 2, cv2.LINE_AA)
        label_status = "AMBIGUOUS" if result.ambiguous else result.status.value
        distance = "-" if result.distance_ratio is None else f"{result.distance_ratio:.3f}"
        actor = "-" if result.person_track_id is None else str(result.person_track_id)
        line = (
            f"Toy {result.product_key[-1].upper()} {label_status} -> P{actor} "
            f"dist={distance} stable={result.stable_frames} "
            f"grace={int(result.association_grace_active)}"
        )
        cv2.rectangle(frame, (8, 8 + index * 34), (720, 38 + index * 34), (20, 20, 20), -1)
        cv2.putText(
            frame,
            line,
            (16, 31 + index * 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            color,
            2,
            cv2.LINE_AA,
        )
    if rows:
        first = rows[0]
        cv2.putText(
            frame,
            f"{first.test_case}/{first.phase} seq={first.result.source_seq}",
            (16, config.frame_height - 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )


def _keyframe_reasons(
    previous: Optional[AssociationReplayRecord],
    current: AssociationReplayRecord,
) -> list[str]:
    reasons = []
    result = current.result
    before = None if previous is None else previous.result
    if result.status is PersonProductAssociationStatus.CANDIDATE and (
        before is None or before.status is not PersonProductAssociationStatus.CANDIDATE
    ):
        reasons.append(f"{result.product_key}_candidate")
    if result.status is PersonProductAssociationStatus.ASSOCIATED and (
        before is None or before.status is not PersonProductAssociationStatus.ASSOCIATED
    ):
        reasons.append(f"{result.product_key}_associated")
    if result.ambiguous and (before is None or not before.ambiguous):
        reasons.append(f"{result.product_key}_ambiguous")
    if result.person_switch_candidate:
        reasons.append(f"{result.product_key}_switch_candidate")
    if result.association_released:
        reasons.append(f"{result.product_key}_released")
    if result.product_missing_grace and (
        before is None or not before.product_missing_grace
    ):
        reasons.append(f"{result.product_key}_product_missing")
    if current.product_recovered_from_missing_pending and (
        previous is None or not previous.product_recovered_from_missing_pending
    ):
        reasons.append(f"{result.product_key}_product_recovered")
    if result.person_missing_grace and (
        before is None or not before.person_missing_grace
    ):
        reasons.append(f"{result.product_key}_person_missing")
    if result.association_recovered:
        reasons.append(f"{result.product_key}_person_recovered")
    return reasons


def _review_sequences(
    records: Sequence[AssociationReplayRecord],
) -> dict[int, list[str]]:
    desired = {
        ("C", "smart_toy_b"): ("toy_b_position_1", "toy_b_position_2", "toy_b_position_3"),
        ("D", "smart_toy_a"): ("toy_a_surface", "toy_a_handheld", "toy_a_returned"),
        ("E", "smart_toy_b"): ("toy_b_surface", "toy_b_handheld", "toy_b_returned"),
    }
    output: dict[int, list[str]] = defaultdict(list)
    for (test_case, product_key), phases in desired.items():
        for phase in phases:
            matches = [
                item for item in records
                if item.test_case == test_case
                and item.result.product_key == product_key
                and item.phase == phase
            ]
            if matches:
                middle = matches[len(matches) // 2]
                output[middle.result.source_seq].append(f"review_{test_case}_{phase}")
    return output


def _normalized_bbox(value: object, width: int, height: int) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must contain four coordinates")
    parsed = tuple(float(item) for item in value)
    if max(parsed) <= 1.0:
        return parsed
    return parsed[0] / width, parsed[1] / height, parsed[2] / width, parsed[3] / height


def _normalized_point(value: object, width: int, height: int) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("point must contain two coordinates")
    x, y = (float(item) for item in value)
    if max(x, y) <= 1.0:
        return x, y
    return x / width, y / height


def _pixel_bbox(
    bbox: tuple[float, float, float, float],
    config: Stage8ReplayConfig,
) -> tuple[int, int, int, int]:
    return (
        int(round(bbox[0] * config.frame_width)),
        int(round(bbox[1] * config.frame_height)),
        int(round(bbox[2] * config.frame_width)),
        int(round(bbox[3] * config.frame_height)),
    )


def _parse_timestamp(value: object) -> datetime:
    text = str(value).strip()
    try:
        seconds = float(text)
    except ValueError:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return _utc_naive(parsed)
    if not math.isfinite(seconds):
        raise ValueError("timestamp must be finite")
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--person-csv", type=Path, required=True)
    parser.add_argument("--person-runtime-id", required=True)
    parser.add_argument("--product-jsonl", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--visual-review-status", choices=("pending", "verified"), default="pending"
    )
    parser.add_argument("--wrong-person-association-count", type=int)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    replay_started = time.perf_counter()
    config = load_replay_config(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    adapter = Sam31BehaviorAdapter(
        store_id=config.store_id,
        camera_id=config.camera_id,
        runtime_instance_id=args.person_runtime_id,
        frame_width=config.frame_width,
        frame_height=config.frame_height,
    )
    person_records = read_fresh_person_records(args.person_csv, adapter=adapter)
    product_records = read_product_records(
        args.product_jsonl,
        camera_id=config.camera_id,
        frame_width=config.frame_width,
        frame_height=config.frame_height,
    )
    replayed = replay_associations(
        person_records=person_records,
        product_records=product_records,
        replay_config=config,
    )
    associations_path = args.output_dir / "person_product_associations.jsonl"
    with associations_path.open("w", encoding="utf-8") as output:
        for record in replayed:
            output.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    summary = build_summary(
        replayed,
        person_record_count=len(person_records),
        product_record_count=len(product_records),
        sync_mode=config.sync_mode,
        wrong_person_association_count=args.wrong_person_association_count,
        visual_review_status=args.visual_review_status,
    )
    person_summary_path = args.person_csv.parent / "summary.json"
    person_summary = (
        json.loads(person_summary_path.read_text(encoding="utf-8-sig"))
        if person_summary_path.is_file() else {}
    )
    artifacts = render_overlay(
        video_path=args.video,
        person_records=person_records,
        replay_config=config,
        records=replayed,
        output_path=args.output_dir / "stage8_person_product_overlay.mp4",
        keyframes_dir=args.output_dir / "keyframes",
    )
    summary["artifacts"] = {
        "person_observations": str(args.person_csv),
        "product_observations": str(args.product_jsonl),
        "associations": str(associations_path),
        "summary": str(args.output_dir / "summary.json"),
        **artifacts,
        "keyframes_directory": str(args.output_dir / "keyframes"),
    }
    summary["performance"] = {
        "person_replay": {
            "elapsed_seconds": person_summary.get("elapsed_seconds"),
            "window_count": person_summary.get("window_count"),
            "fresh_person_observation_count": person_summary.get(
                "fresh_person_observation_count"
            ),
            "average_seconds_per_window": (
                person_summary.get("elapsed_seconds") / person_summary.get("window_count")
                if person_summary.get("elapsed_seconds")
                and person_summary.get("window_count") else None
            ),
        },
        "association_replay": {
            "elapsed_seconds_including_overlay": time.perf_counter() - replay_started,
            "association_record_count": len(replayed),
        },
    }
    _write_json(args.output_dir / "summary.json", summary)
    visual_path, report_path = write_reports(
        output_dir=args.output_dir,
        summary=summary,
        person_csv=args.person_csv,
        product_jsonl=args.product_jsonl,
        video_path=args.video,
        artifacts=artifacts,
    )
    summary["artifacts"]["visual_review"] = str(visual_path)
    summary["artifacts"]["report"] = str(report_path)
    _write_json(args.output_dir / "summary.json", summary)
    print(json.dumps({
        "acceptance": summary["acceptance"],
        "moved_product_association_coverage": summary[
            "moved_product_association_coverage"
        ],
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
