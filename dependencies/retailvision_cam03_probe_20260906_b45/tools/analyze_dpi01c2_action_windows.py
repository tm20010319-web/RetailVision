"""Build bounded action-window evidence from a real DPI-01C.2 propagation run.

This is an artifact-only diagnostic.  It reads the existing propagation JSONL
and overlay video, labels display-state transitions using the retained
physical-tabletop reference polygon, and never calls the tracking Runtime.
The physical polygon is evidence for surface review only; it is not a Runtime
Gate and does not replace ``product_display_surface``.
"""

from __future__ import annotations

import argparse
import json
import re
import math
from pathlib import Path
from typing import Mapping, Optional, Sequence

import cv2
import numpy as np


REQUIRED_STATES = (
    "stationary_on_stand",
    "just_leaving_stand",
    "held",
    "returned",
)


def _object_for_key(row: Mapping[str, object], product_key: str) -> Mapping[str, object]:
    for item in row.get("objects", []):
        if isinstance(item, Mapping) and str(item.get("product_key")) == product_key:
            return item
    raise ValueError(f"missing product object {product_key} at source_seq={row.get('source_seq')}")


def _source_ref(row: Mapping[str, object], local_index: int, state: str) -> dict[str, object]:
    return {
        "state": state,
        "local_frame_index": int(local_index),
        "source_seq": int(row["source_seq"]),
        "source_pts": int(row["source_pts"]),
        "presentation_seconds": float(row["presentation_seconds"]),
    }


def _sustained(
    rows: Sequence[Mapping[str, object]],
    start: int,
    predicate: object,
    count: int = 3,
) -> Optional[int]:
    check = predicate
    if not callable(check):
        raise TypeError("predicate must be callable")
    for index in range(max(0, int(start)), len(rows)):
        if all(index + offset < len(rows) and bool(check(index + offset)) for offset in range(count)):
            return index
    return None


def classify_product_action_window(
    observations: Sequence[Mapping[str, object]],
    product_key: str,
) -> dict[str, object]:
    """Classify one product's bounded physical contact sequence.

    The thresholds are deliberately diagnostic and conservative.  A state is
    only marked after a short sustained run, and lack of an observed transition
    remains incomplete rather than being inferred from a missing mask.
    """
    rows = list(observations)
    if not rows:
        raise ValueError("observations must not be empty")
    rows.sort(key=lambda item: int(item["source_seq"]))
    objects = [_object_for_key(row, product_key) for row in rows]

    def row_metrics(index: int) -> tuple[float, float, bool, bool]:
        item = objects[index]
        physical = item.get("physical_tabletop", {})
        current = item.get("current_expanded_region", {})
        if not isinstance(physical, Mapping) or not isinstance(current, Mapping):
            raise ValueError("surface metrics must be mappings")
        return (
            float(physical.get("surface_overlap_ratio", 0.0)),
            float(current.get("surface_overlap_ratio", 0.0)),
            bool(physical.get("bbox_bottom_center_inside", False)),
            bool(physical.get("lowest_contact_point_inside", False)),
        )

    initial_physical_overlap = row_metrics(0)[0]

    def is_held(index: int) -> bool:
        physical, _, bottom_inside, contact_inside = row_metrics(index)
        return physical <= 0.05 and not bottom_inside and not contact_inside

    def is_contact(index: int) -> bool:
        physical, _, bottom_inside, contact_inside = row_metrics(index)
        return physical > 0.05 or bottom_inside or contact_inside

    def is_stable_return(index: int) -> bool:
        physical, _, bottom_inside, contact_inside = row_metrics(index)
        return physical >= 0.45 and (bottom_inside or contact_inside)

    leave_start: Optional[int] = None
    for index in range(1, len(rows)):
        physical = row_metrics(index)[0]
        previous_physical = row_metrics(index - 1)[0]
        # Only physical-reference contact loss starts an action transition.
        # Bounding-box jitter alone must not turn a stationary control into a
        # false departure.
        if physical < 0.45 and previous_physical >= 0.45:
            leave_start = index
            break
    held_start = _sustained(rows, (leave_start or 0) + 1, is_held, count=3) if leave_start is not None else None
    return_start = (
        _sustained(rows, held_start + 1, is_contact, count=3)
        if held_start is not None
        else None
    )
    returned_stable = (
        _sustained(rows, return_start, is_stable_return, count=5)
        if return_start is not None
        else None
    )

    state_by_index: list[str] = []
    for index in range(len(rows)):
        if leave_start is None or index < leave_start:
            state = "stationary_on_stand"
        elif held_start is None or index < held_start:
            state = "just_leaving_stand"
        elif return_start is None or index < return_start:
            state = "held"
        else:
            state = "returned"
        state_by_index.append(state)

    intervals: list[dict[str, object]] = []
    interval_start = 0
    for index in range(1, len(state_by_index) + 1):
        if index == len(state_by_index) or state_by_index[index] != state_by_index[interval_start]:
            first = rows[interval_start]
            last = rows[index - 1]
            intervals.append({
                "state": state_by_index[interval_start],
                "start": _source_ref(first, interval_start, state_by_index[interval_start]),
                "end": _source_ref(last, index - 1, state_by_index[interval_start]),
                "frame_count": index - interval_start,
            })
            interval_start = index

    state_indices = {
        "stationary_on_stand": 0,
        "just_leaving_stand": leave_start,
        "held": held_start,
        "returned": return_start,
    }
    if returned_stable is not None:
        state_indices["returned_stable_reference"] = returned_stable
    keyframes: list[dict[str, object]] = []
    seen: set[int] = set()
    for state, index in state_indices.items():
        if index is None or index in seen or index >= len(rows):
            continue
        seen.add(index)
        ref = _source_ref(rows[index], index, state if state in REQUIRED_STATES else "returned")
        item = objects[index]
        current = item.get("current_expanded_region", {})
        physical = item.get("physical_tabletop", {})
        ref.update({
            "product_key": product_key,
            "current_expanded_region": current,
            "physical_tabletop": physical,
            "expanded_region_would_be_in_display": bool(
                isinstance(current, Mapping)
                and bool(current.get("centroid_inside", False))
                and float(current.get("surface_overlap_ratio", 0.0)) >= 0.50
            ),
            "physical_reference_outside": bool(
                isinstance(physical, Mapping)
                and float(physical.get("surface_overlap_ratio", 0.0)) <= 0.05
                and not bool(physical.get("bbox_bottom_center_inside", False))
                and not bool(physical.get("lowest_contact_point_inside", False))
            ),
        })
        keyframes.append(ref)

    observed_states = [state for state in REQUIRED_STATES if state in state_by_index]
    held_indices = [index for index, state in enumerate(state_by_index) if state == "held"]
    expanded_conflict = any(
        bool(keyframes_item["expanded_region_would_be_in_display"])
        and bool(keyframes_item["physical_reference_outside"])
        for keyframes_item in keyframes
        if keyframes_item["state"] == "held"
    )
    if held_indices:
        expanded_conflict = expanded_conflict or any(
            bool(objects[index].get("current_expanded_region", {}).get("centroid_inside", False))
            and float(objects[index].get("current_expanded_region", {}).get("surface_overlap_ratio", 0.0)) >= 0.50
            and float(objects[index].get("physical_tabletop", {}).get("surface_overlap_ratio", 0.0)) <= 0.05
            for index in held_indices
        )

    return {
        "product_key": product_key,
        "initial_physical_overlap_ratio": initial_physical_overlap,
        "observed_states": observed_states,
        "required_states": list(REQUIRED_STATES),
        "transition_complete": all(state in observed_states for state in REQUIRED_STATES),
        "role": "moving_target" if held_start is not None else "stationary_control",
        "state_intervals": intervals,
        "keyframes": keyframes,
        "expanded_region_held_conflict": expanded_conflict,
        "thresholds": {
            "leave_physical_overlap_below": 0.45,
            "held_physical_overlap_at_most": 0.05,
            "returned_stable_physical_overlap_at_least": 0.45,
            "sustained_held_frames": 3,
            "sustained_contact_frames": 3,
            "sustained_returned_stable_frames": 5,
        },
    }


def build_action_evidence(
    observations: Sequence[Mapping[str, object]],
    *,
    camera_id: str,
    source_video_sha256: str,
    time_base: Mapping[str, object],
    selected_product_keys: Sequence[str],
    product_display_surface_pixels: Sequence[Sequence[int]],
    physical_tabletop_pixels: Sequence[Sequence[int]],
) -> dict[str, object]:
    products = {
        key: classify_product_action_window(observations, key)
        for key in selected_product_keys
    }
    moving_products = [item for item in products.values() if item["role"] == "moving_target"]
    observed_states = sorted({
        state
        for item in products.values()
        for state in item["observed_states"]
    }, key=REQUIRED_STATES.index)
    conflict_products = [
        str(key) for key, item in products.items() if item["expanded_region_held_conflict"]
    ]
    transition_complete = bool(moving_products) and all(
        bool(item["transition_complete"]) for item in moving_products
    )
    return {
        "schema_version": "dpi-01c2.action-window-evidence.v1",
        "camera_id": camera_id,
        "source_video_sha256": source_video_sha256,
        "time_base": dict(time_base),
        "bounded_source_range": {
            "source_seq_start": int(observations[0]["source_seq"]),
            "source_seq_end": int(observations[-1]["source_seq"]),
            "presentation_seconds_start": float(observations[0]["presentation_seconds"]),
            "presentation_seconds_end": float(observations[-1]["presentation_seconds"]),
            "frame_count": len(observations),
        },
        "product_display_surface": {
            "area_id": "product_display_surface",
            "pixel_points": [list(point) for point in product_display_surface_pixels],
            "runtime_gate": True,
        },
        "physical_tabletop_reference": {
            "pixel_points": [list(point) for point in physical_tabletop_pixels],
            "runtime_gate": False,
            "purpose": "diagnostic_reference_only",
        },
        "selected_product_keys": list(selected_product_keys),
        "products": products,
        "observed_states": observed_states,
        "required_states": list(REQUIRED_STATES),
        "transition_complete": transition_complete,
        "expanded_surface_semantics_conflict_products": conflict_products,
        "decision": (
            "ACTION_WINDOWS_OBSERVED_SURFACE_SEMANTICS_REVIEW_REQUIRED"
            if transition_complete and conflict_products
            else "ACTION_WINDOWS_OBSERVED"
            if transition_complete
            else "ACTION_WINDOWS_INCOMPLETE"
        ),
        "behavior_events_emitted": False,
        "runtime_state_machine_modified": False,
        "full_behavior_replay": "PENDING",
        "not_fully_closed": True,
        "notes": [
            "The physical tabletop polygon is evidence only and is not a Runtime Gate.",
            "The current expanded product_display_surface remains the only formal display surface.",
            "Held white-guitar masks overlap the expanded surface, so Surface semantics require operator review.",
            "This artifact does not claim PICK_UP, HOLDING, PUT_DOWN, or production validation.",
        ],
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _annotate_frame(frame: np.ndarray, keyframe: Mapping[str, object], conflict: bool) -> np.ndarray:
    rendered = frame.copy()
    state = str(keyframe["state"])
    key = str(keyframe["product_key"])
    color = {
        "stationary_on_stand": (80, 200, 80),
        "just_leaving_stand": (0, 200, 255),
        "held": (0, 80, 255),
        "returned": (255, 180, 0),
    }.get(state, (255, 255, 255))
    cv2.rectangle(rendered, (0, 0), (920, 72), (0, 0, 0), -1)
    cv2.putText(
        rendered,
        f"ACTION {state}  {key}  seq={keyframe['source_seq']}  t={float(keyframe['presentation_seconds']):.2f}s",
        (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2, cv2.LINE_AA,
    )
    if conflict:
        cv2.putText(
            rendered,
            "SURFACE REVIEW: expanded region says IN_DISPLAY while physical reference is OUTSIDE",
            (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 180, 255), 1, cv2.LINE_AA,
        )
    return rendered


def _read_overlay_frames(video: Path, indices: Sequence[int]) -> dict[int, np.ndarray]:
    wanted = sorted({int(index) for index in indices if int(index) >= 0})
    if not wanted:
        return {}
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open propagation overlay: {video}")
    frames: dict[int, np.ndarray] = {}
    try:
        for index in range(wanted[-1] + 1):
            ok, frame = capture.read()
            if not ok:
                break
            if index in wanted:
                frames[index] = frame
    finally:
        capture.release()
    return frames


def write_action_overlays(
    evidence: Mapping[str, object],
    *,
    propagation_overlay: Path,
    output_dir: Path,
    source_seq_start: int,
) -> dict[str, object]:
    keyframe_rows: list[tuple[str, Mapping[str, object], bool]] = []
    for key, item in dict(evidence["products"]).items():
        if not isinstance(item, Mapping):
            continue
        conflict = bool(item.get("expanded_region_held_conflict", False))
        for keyframe in item.get("keyframes", []):
            if isinstance(keyframe, Mapping):
                keyframe_rows.append((str(key), keyframe, conflict))
    frames = _read_overlay_frames(
        propagation_overlay,
        [int(row[1]["source_seq"]) - int(source_seq_start) for row in keyframe_rows],
    )
    keyframe_dir = output_dir / "action_window_keyframes"
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[np.ndarray] = []
    artifact_rows: list[dict[str, object]] = []
    for key, keyframe, conflict in keyframe_rows:
        local_index = int(keyframe["source_seq"]) - int(source_seq_start)
        frame = frames.get(local_index)
        if frame is None:
            continue
        image = _annotate_frame(frame, keyframe, conflict and str(keyframe["state"]) == "held")
        path = keyframe_dir / f"{_safe_name(key)}_{_safe_name(str(keyframe['state']))}_{int(keyframe['source_seq'])}.jpg"
        if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise RuntimeError(f"failed to write action overlay {path}")
        rendered.append(image)
        artifact_rows.append({
            "product_key": key,
            "state": keyframe["state"],
            "source_seq": keyframe["source_seq"],
            "source_pts": keyframe["source_pts"],
            "path": str(path),
        })
    contact_sheet_path = output_dir / "action_window_keyframes.jpg"
    if rendered:
        tiles = []
        for image in rendered:
            tile = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
            tiles.append(tile)
        while len(tiles) % 2:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [cv2.hconcat(tiles[index:index + 2]) for index in range(0, len(tiles), 2)]
        sheet = cv2.vconcat(rows)
        if not cv2.imwrite(str(contact_sheet_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise RuntimeError(f"failed to write contact sheet {contact_sheet_path}")
    return {
        "keyframes": artifact_rows,
        "contact_sheet": str(contact_sheet_path) if rendered else None,
        "keyframe_directory": str(keyframe_dir),
    }


def run(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve()
    observations_path = output_dir / "real_propagation_observations.jsonl"
    diagnostic_path = output_dir / "real_propagation_diagnostic.json"
    summary_path = output_dir / "real_propagation_summary.json"
    config = json.loads(args.camera_config.resolve().read_text(encoding="utf-8"))
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    propagation_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    observations = [json.loads(line) for line in observations_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected_keys = [str(key) for key in config["selected_product_keys"]]
    surface_points = config["product_display_surface"]["pixel_points"]
    support_points = config["product_display_surface"].get("support_contact_points", {})
    physical_points = [list(point) for point in args.physical_tabletop_pixels]
    evidence = build_action_evidence(
        observations,
        camera_id=str(config["camera_id"]),
        source_video_sha256=str(diagnostic["source_video_sha256"]),
        time_base=diagnostic["time_base"],
        selected_product_keys=selected_keys,
        product_display_surface_pixels=surface_points,
        physical_tabletop_pixels=physical_points,
    )
    first_observation = observations[0]
    sam_object_mapping = {}
    for item in first_observation.get("objects", []):
        if isinstance(item, Mapping) and item.get("product_key") in selected_keys:
            sam_object_mapping[str(item["product_key"])] = int(item["sam_obj_id"])
    first_window = propagation_summary.get("rolling_windows", [{}])[0]
    window_mapping = first_window.get("product_key_mapping", {}) if isinstance(first_window, Mapping) else {}
    track_mapping = {}
    for key in selected_keys:
        window_item = window_mapping.get(key, {}) if isinstance(window_mapping, Mapping) else {}
        if not isinstance(window_item, Mapping):
            window_item = {}
        track_mapping[key] = {
            "sam_obj_id": sam_object_mapping.get(key),
            "diagnostic_runtime_track_token": window_item.get("product_track_token"),
            "track_status": window_item.get("track_status"),
            "association_success": window_item.get("association_success"),
            "mapping_scope": "bounded_propagation_diagnostic",
            "formal_new_segment_confirmed": False,
        }
    confirmation_path = output_dir / "operator_confirmation.json"
    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8")) if confirmation_path.is_file() else {}
    evidence["product_key_mapping"] = track_mapping
    evidence["operator_mask_confirmation"] = {
        "path": str(confirmation_path),
        "statuses": {
            str(item.get("product_key")): str(item.get("confirmation_status"))
            for item in confirmation.get("confirmations", [])
            if isinstance(item, Mapping)
        },
    }
    evidence["upright_support_rule"] = {
        "configured_support_contact_points_pixels": support_points,
        "tolerance_normalized": 0.08,
        "formal_surface_area_id": "product_display_surface",
        "purpose": "cam03_display_state_only",
    }
    for product_key, product in evidence["products"].items():
        approved = support_points.get(product_key)
        for keyframe in product.get("keyframes", []):
            physical = keyframe.get("physical_tabletop", {})
            current = keyframe.get("current_expanded_region", {})
            contact = physical.get("lowest_contact_point") if isinstance(physical, Mapping) else None
            distance = None
            if approved and isinstance(contact, list) and len(contact) == 2:
                distance = math.hypot(float(contact[0]) - float(approved[0]), float(contact[1]) - float(approved[1])) / 1280.0
            keyframe.update({
                "display_evidence_mode": str(surface_points and config["product_display_surface"].get("display_evidence_mode", "mask_surface_overlap")),
                "current_support_contact_point": contact,
                "approved_support_contact_point": approved,
                "support_contact_distance": distance,
                "support_contact_tolerance": float(config["product_display_surface"].get("support_contact_tolerance", 0.08)),
                "display_state": "IN_DISPLAY" if distance is not None and distance <= float(config["product_display_surface"].get("support_contact_tolerance", 0.08)) and float(current.get("surface_overlap_ratio", 0.0)) >= 0.50 else "OUTSIDE_DISPLAY",
                "display_state_reason": "fixed_support_contact_within_tolerance" if distance is not None and distance <= float(config["product_display_surface"].get("support_contact_tolerance", 0.08)) else "support_contact_outside_tolerance",
            })
    overlay_artifacts = write_action_overlays(
        evidence,
        propagation_overlay=output_dir / "real_propagation_overlay.mp4",
        output_dir=output_dir,
        source_seq_start=int(observations[0]["source_seq"]),
    )
    evidence["overlay_artifacts"] = overlay_artifacts
    evidence_path = output_dir / "action_window_diagnostic.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    frames = []
    for item in evidence["products"].values():
        frames.extend(item.get("keyframes", []))
    surface_evidence = {
        "schema_version": "dpi-01c2.surface-evidence.v2",
        "camera_id": evidence["camera_id"],
        "physical_tabletop_pixels": physical_points,
        "current_expanded_region_pixels": surface_points,
        "decision": "SURFACE_SEMANTICS_REVIEW_REQUIRED" if evidence["expanded_surface_semantics_conflict_products"] else evidence["decision"],
        "calibration_status": str(config["product_display_surface"].get("calibration_status")),
        "required_states": list(REQUIRED_STATES),
        "observed_states": evidence["observed_states"],
        "frames": frames,
        "action_window_diagnostic": str(evidence_path),
        "overlay_artifacts": overlay_artifacts,
        "notes": evidence["notes"],
    }
    (output_dir / "surface_evidence.json").write_text(json.dumps(surface_evidence, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update({
        "action_window_diagnostic": str(evidence_path),
        "action_windows_observed": bool(evidence["transition_complete"]),
        "action_window_decision": evidence["decision"],
        "surface_decision": surface_evidence["decision"],
        "full_behavior_replay": "PENDING",
        "not_fully_closed": True,
    })
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "decision": evidence["decision"],
        "observed_states": evidence["observed_states"],
        "expanded_surface_semantics_conflict_products": evidence["expanded_surface_semantics_conflict_products"],
        "action_window_diagnostic": str(evidence_path),
        "overlay_artifacts": overlay_artifacts,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--camera-config", type=Path, required=True)
    parser.add_argument(
        "--physical-tabletop-pixels",
        type=int,
        nargs=8,
        default=(520, 454, 952, 452, 968, 526, 521, 531),
        metavar="P",
        help="four x,y points used for diagnostic evidence only",
    )
    args = parser.parse_args(argv)
    values = list(args.physical_tabletop_pixels)
    args.physical_tabletop_pixels = [values[index:index + 2] for index in range(0, 8, 2)]
    return args


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
