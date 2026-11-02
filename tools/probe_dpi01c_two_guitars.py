"""Run a bounded two-guitar DPI-01C real SAM3 probe.

This is an evidence tool for the explicitly deferred-robot batch.  It keeps
the frozen domain checks, uses one frame and one SAM session, and stops before
track creation unless ``--confirm`` is supplied after human Mask review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Mapping, Optional, Sequence

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.behavior.product_first_frame_initialization import (  # noqa: E402
    DenseInitializationPolicy,
    InitializationFrameRef,
    MaskConfirmation,
    TargetSelection,
    build_proposal,
    commit_confirmed,
    frame_sha256,
    proposal_diagnostic_payload,
    result_mapping_payload,
)
from agent.behavior.sam31_product_tracking import MultiProductTrackRuntime, ProductCandidate, ProductDisplaySurface  # noqa: E402
from agent.behavior.sam31_real_adapter import create_external_sam31_adapter  # noqa: E402


def _probe_pts(video: Path, source_seq: int) -> tuple[int, Fraction]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=time_base:frame=best_effort_timestamp",
        "-of", "json", str(video),
    ]
    payload = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    time_base = Fraction(str(payload["streams"][0]["time_base"]))
    return int(payload["frames"][source_seq]["best_effort_timestamp"]), time_base


def _decode(video: Path, source_seq: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {video}")
    try:
        for index in range(source_seq + 1):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"cannot decode source_seq={source_seq}")
            if index == source_seq:
                return cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
    finally:
        capture.release()
    raise AssertionError("unreachable")


def _parse_bbox(value: str) -> tuple[int, int, int, int]:
    values = tuple(int(item) for item in value.split(","))
    if len(values) != 4:
        raise ValueError("bbox must be x1,y1,x2,y2")
    return values


def _parse_point(value: str) -> tuple[int, int]:
    values = tuple(int(item) for item in value.split(","))
    if len(values) != 2:
        raise ValueError("point must be x,y")
    return values


def _overlay(frame: np.ndarray, proposal: object, surface: ProductDisplaySurface) -> np.ndarray:
    result = frame.copy()
    colors = ((0, 220, 255), (255, 120, 60))
    for index, item in enumerate(proposal.masks):
        mask = np.asarray(item.mask).astype(bool)
        color = np.asarray(colors[index % len(colors)], dtype=np.float32)
        result[mask] = np.clip(result[mask] * 0.45 + color * 0.55, 0, 255).astype(np.uint8)
        x1, y1, x2, y2 = item.bbox
        cv2.rectangle(result, (x1, y1), (x2, y2), tuple(int(value) for value in color), 2)
        cv2.putText(result, item.product_key, (x1, max(18, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, tuple(int(value) for value in color), 2)
    width, height = 1280, 720
    polygon = np.asarray([(round(x * width), round(y * height)) for x, y in surface.points], dtype=np.int32)
    cv2.polylines(result, [polygon], True, (0, 255, 0), 2)
    return result


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate_from_mask(mask: np.ndarray, local_obj_id: int) -> ProductCandidate:
    ys, xs = np.where(mask.astype(bool))
    if not len(xs):
        raise ValueError("cannot build continuity candidate from an empty mask")
    bbox = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
    return ProductCandidate(
        local_obj_id=local_obj_id,
        bbox=bbox,
        centroid=(float(np.mean(xs)), float(np.mean(ys))),
        confidence=1.0,
        mask=mask.astype(bool),
    )


def run(args: argparse.Namespace) -> int:
    config_bytes = args.camera_config.read_bytes()
    config = json.loads(config_bytes)
    frame = _decode(args.video, args.source_seq)
    source_pts, time_base = _probe_pts(args.video, args.source_seq)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_ref = InitializationFrameRef(
        analysis_run_id=args.analysis_run_id,
        source_seq=args.source_seq,
        source_pts=source_pts,
        time_base_num=time_base.numerator,
        time_base_den=time_base.denominator,
        presentation_seconds=float(source_pts * time_base),
        frame_sha256=frame_sha256(frame),
        frame_size=(1280, 720),
        roi_xyxy=(0, 0, 1280, 720),
        coordinate_transform_version="full_frame_resize_2560x1440_to_1280x720_v1",
    )
    surface_config = config["product_display_surface"]
    surface = ProductDisplaySurface(tuple(tuple(float(v) for v in point) for point in surface_config["points"]))
    selections = (
        TargetSelection(
            product_key="ai_electric_guitar",
            bbox_xyxy=_parse_bbox(args.electric_bbox),
            positive_points=tuple(
                _parse_point(value)
                for value in [args.electric_positive, *(args.electric_additional_positive or [])]
            ),
            negative_points=tuple(
                _parse_point(value)
                for value in (args.electric_negative or ["858,450"])
            ),
        ),
        TargetSelection(
            product_key="ai_guitar",
            bbox_xyxy=_parse_bbox(args.guitar_bbox),
            positive_points=tuple(
                _parse_point(value)
                for value in [args.guitar_positive, *(args.guitar_additional_positive or [])]
            ),
            negative_points=tuple(
                _parse_point(value)
                for value in (args.guitar_negative or ["842,455", "936,455"])
            ),
        ),
    )
    if args.first_product == "ai_guitar":
        selections = tuple(reversed(selections))
    policy_config = config.get("initialization_policy", {})
    policy = DenseInitializationPolicy(
        approved_product_keys=frozenset(config["products"]),
        product_display_surface=surface,
        frame_size=(1280, 720),
        minimum_target_count=int(policy_config.get("minimum_target_count", 2)),
        maximum_target_count=int(policy_config.get("maximum_target_count", 2)),
    )
    adapter = None
    try:
        adapter = create_external_sam31_adapter(
            frame=frame,
            runtime_root=args.sam_runtime_root,
            checkpoint_path=args.checkpoint_path,
        )
        proposal = build_proposal(frame_ref, selections, adapter, frame=frame, policy=policy)
        diagnostic = proposal_diagnostic_payload(
            proposal,
            camera_id=str(config["camera_id"]),
            camera_config_version=str(config["config_version"]),
        )
        diagnostic["batch_scope"] = policy_config.get("batch_scope", "two_guitars_only_robot_deferred")
        diagnostic["operator_confirmation_required"] = True
        cv2.imwrite(str(output_dir / "initialization_frame.jpg"), frame)
        cv2.imwrite(str(output_dir / "multi_product_mask_overlay.jpg"), _overlay(frame, proposal, surface))
        for item in proposal.masks:
            cv2.imwrite(str(output_dir / f"mask_{item.product_key}.png"), item.mask.astype(np.uint8) * 255)
        _write_json(output_dir / "initialization_diagnostic.json", diagnostic)
        pending_mapping = {
            **diagnostic,
            "status": "PENDING_OPERATOR_MASK_CONFIRMATION" if proposal.status == "READY_FOR_CONFIRMATION" else proposal.status,
            "assignments": [
                {
                    "product_key": item.product_key,
                    "candidate_id": item.candidate_id,
                    "mask_sha256": item.mask_sha256,
                    "mask_reference": f"mask_{item.product_key}.png",
                    "mask_confirmation": "required",
                }
                for item in proposal.masks
            ],
        }
        if proposal.status != "READY_FOR_CONFIRMATION" or not args.confirm:
            _write_json(output_dir / "initialization_mapping.json", pending_mapping)
            return 0 if proposal.status == "READY_FOR_CONFIRMATION" else 2

        confirmations = tuple(
            MaskConfirmation(
                product_key=item.product_key,
                candidate_id=item.candidate_id,
                frame_sha256=proposal.frame_ref.frame_sha256,
                mask_sha256=item.mask_sha256,
                status="confirmed",
                confirmed_at=datetime.now(timezone.utc),
            )
            for item in proposal.masks
        )
        runtime = MultiProductTrackRuntime(
            camera_id=str(config["camera_id"]),
            runtime_instance_id=args.runtime_instance_id,
            # The frozen Stage7C runtime currently accepts its two canonical
            # domain types only.  Preserve the operator-facing catalog
            # product_type in config/diagnostics, while routing both guitar
            # keys through the existing smart_guitar tracking contract.
            products={key: "smart_guitar" for key in config["products"]},
            product_display_surface=surface,
            frame_width=1280,
            frame_height=720,
            product_track_lost_after_windows=3,
        )
        result = commit_confirmed(proposal, confirmations, runtime)
        initial_tokens = {item.product_key: item.product_track_token for item in result.tracks}
        continuity_windows = []
        continuity_candidates = tuple(
            _candidate_from_mask(item.mask, 100 + index)
            for index, item in enumerate(proposal.masks)
        )
        for window_no in (1, 2):
            window_results = runtime.process_window(
                candidates=continuity_candidates,
                observed_at=datetime.fromtimestamp(
                    proposal.frame_ref.authoritative_seconds + window_no / 90000,
                    tz=timezone.utc,
                ),
                source_seq=proposal.frame_ref.source_seq + window_no,
                window_no=window_no,
            )
            continuity_windows.append({
                "window_no": window_no,
                "product_track_tokens": {item.product_key: item.product_track_token for item in window_results},
                "product_candidate_ids": {item.product_key: item.local_obj_id for item in window_results},
                "track_status": {item.product_key: None if item.track_status is None else item.track_status.value for item in window_results},
                "association_success": {item.product_key: item.association_success for item in window_results},
                "association_ambiguous": {item.product_key: item.association_ambiguous for item in window_results},
            })
        continuity_pass = all(
            window["product_track_tokens"] == initial_tokens
            and all(window["association_success"].values())
            and not any(window["association_ambiguous"].values())
            for window in continuity_windows
        )
        _write_json(output_dir / "short_window_continuity.json", {
            "schema_version": "dpi-01c.short-window-continuity.v1",
            "status": "PASS" if continuity_pass else "REJECTED",
            "window_count": len(continuity_windows),
            "candidate_source": "initialized_masks_runtime_association_harness",
            "initial_product_track_tokens": initial_tokens,
            "windows": continuity_windows,
        })
        mapping = result_mapping_payload(
            result,
            camera_id=str(config["camera_id"]),
            camera_config_version=str(config["config_version"]),
            mask_references={item.product_key: f"mask_{item.product_key}.png" for item in proposal.masks},
        )
        _write_json(output_dir / "initialization_mapping.json", mapping)
        diagnostic["status"] = "COMMITTED"
        diagnostic["confirmations"] = [{"product_key": item.product_key, "status": item.status, "confirmed_at": item.confirmed_at.isoformat()} for item in confirmations]
        _write_json(output_dir / "initialization_diagnostic.json", diagnostic)
        return 0
    finally:
        if adapter is not None:
            adapter.close()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--camera-config", type=Path, required=True)
    parser.add_argument("--analysis-run-id", required=True)
    parser.add_argument("--runtime-instance-id", default="dpi01c-cam03-guitars-20260904")
    parser.add_argument("--source-seq", type=int, default=0)
    parser.add_argument("--sam-runtime-root", type=Path, default=Path(r"D:\GJJ\work\sam3"))
    parser.add_argument("--checkpoint-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--first-product", choices=("ai_electric_guitar", "ai_guitar"), default="ai_electric_guitar")
    parser.add_argument("--electric-bbox", default="760,300,845,500")
    parser.add_argument("--electric-positive", default="803,458")
    parser.add_argument("--electric-additional-positive", action="append", default=None)
    parser.add_argument("--electric-negative", action="append", default=None)
    parser.add_argument("--guitar-bbox", default="850,330,925,505")
    parser.add_argument("--guitar-positive", default="888,455")
    parser.add_argument("--guitar-additional-positive", action="append", default=None)
    parser.add_argument("--guitar-negative", action="append", default=None)
    parser.add_argument("--confirm", action="store_true", help="commit only after external human Mask confirmation")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
