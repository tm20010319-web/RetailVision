"""Interactive Offline-MP4 dense Product first-frame initialization.

The tool intentionally has no RTSP path.  It binds a decoded MP4 frame to the
stream's exact PTS/time-base, collects full-frame prompts through an optional
zoom ROI, delegates every rule to ``product_first_frame_initialization``, and
writes review artifacts outside the source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.product_first_frame_initialization import (  # noqa: E402
    DenseInitializationPolicy,
    DenseProductInitializationError,
    InitializationFrameRef,
    MaskConfirmation,
    TargetSelection,
    build_proposal,
    commit_confirmed,
    frame_sha256,
    proposal_diagnostic_payload,
    result_mapping_payload,
)
from agent.sam31_product_tracking import (  # noqa: E402
    MultiProductTrackRuntime,
    ProductDisplaySurface,
)


WINDOW = "Dense Product Initialization"
COORDINATE_TRANSFORM_VERSION = "roi_to_full_ai_frame_v1"


@dataclass(frozen=True)
class ProbedFrame:
    source_seq: int
    source_pts: int


def _probe_video(video: Path) -> tuple[Fraction, tuple[ProbedFrame, ...]]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=time_base:frame=best_effort_timestamp",
        "-of", "json", str(video),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"ffprobe failed: {completed.stderr.strip()}")
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    if len(streams) != 1 or not streams[0].get("time_base"):
        raise RuntimeError("video stream time_base is unavailable")
    time_base = Fraction(str(streams[0]["time_base"]))
    frames = []
    for source_seq, row in enumerate(payload.get("frames", [])):
        value = row.get("best_effort_timestamp")
        if value is None:
            raise RuntimeError(f"source PTS is unavailable at decoded frame {source_seq}")
        frames.append(ProbedFrame(source_seq, int(value)))
    if not frames:
        raise RuntimeError("video contains no timestamped frames")
    return time_base, tuple(frames)


def _decode_frame(video: Path, source_seq: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open Offline MP4: {video}")
    try:
        for index in range(source_seq + 1):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"cannot decode source_seq={source_seq}")
            if index == source_seq:
                return frame
    finally:
        capture.release()
    raise AssertionError("unreachable")


def _choose_source_seq(video: Path, frame_count: int) -> int:
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open Offline MP4: {video}")
    index = 0
    try:
        while index < frame_count:
            ok, frame = capture.read()
            if not ok:
                break
            preview = frame.copy()
            cv2.putText(
                preview,
                f"source_seq={index}  SPACE=select  Q=cancel",
                (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2,
            )
            cv2.imshow(WINDOW, preview)
            key = cv2.waitKey(0) & 0xFF
            if key == ord("q"):
                raise KeyboardInterrupt("initialization cancelled")
            if key == ord(" "):
                return index
            index += 1
    finally:
        capture.release()
    raise RuntimeError("no initialization frame selected")


def _parse_roi(value: Optional[str], frame_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = frame_size
    if value is None:
        return 0, 0, width, height
    parts = tuple(int(item.strip()) for item in value.split(","))
    if len(parts) != 4:
        raise ValueError("--roi must be x1,y1,x2,y2")
    x1, y1, x2, y2 = parts
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("--roi must fit the configured full AI frame")
    return parts


def _collect_points(image: np.ndarray, bbox: tuple[int, int, int, int]) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    positive: list[tuple[int, int]] = []
    negative: list[tuple[int, int]] = []

    def mouse(event: int, x: int, y: int, _flags: int, _data: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            positive.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN:
            negative.append((x, y))

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, mouse)
    while True:
        preview = image.copy()
        x1, y1, x2, y2 = bbox
        cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 255, 255), 2)
        for point in positive:
            cv2.circle(preview, point, 5, (0, 255, 0), -1)
        for point in negative:
            cv2.circle(preview, point, 5, (0, 0, 255), -1)
        cv2.putText(preview, "L=positive R=negative U=undo C=clear ENTER=done Q=cancel", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        cv2.imshow(WINDOW, preview)
        key = cv2.waitKey(20) & 0xFF
        if key in (10, 13):
            if positive:
                return tuple(positive), tuple(negative)
        elif key == ord("u"):
            if negative:
                negative.pop()
            elif positive:
                positive.pop()
        elif key in (ord("c"), ord("r")):
            positive.clear()
            negative.clear()
        elif key == ord("q"):
            raise KeyboardInterrupt("initialization cancelled")


def _selection_for_key(
    frame: np.ndarray,
    product_key: str,
    product_config: Mapping[str, object],
    roi: tuple[int, int, int, int],
) -> TargetSelection:
    x0, y0, x3, y3 = roi
    view = frame[y0:y3, x0:x3].copy()
    reference = product_config.get("catalog_reference_image")
    if reference:
        reference_path = Path(str(reference))
        reference_image = cv2.imread(str(reference_path))
        if reference_image is not None:
            cv2.imshow("Catalog Reference (human aid only)", reference_image)
    print(f"Select a tight bbox for {product_key}")
    x, y, width, height = (int(value) for value in cv2.selectROI(WINDOW, view, False, False))
    if width <= 0 or height <= 0:
        raise KeyboardInterrupt(f"bbox selection cancelled for {product_key}")
    roi_bbox = (x, y, x + width, y + height)
    positive, negative = _collect_points(view, roi_bbox)
    return TargetSelection(
        product_key=product_key,
        bbox_xyxy=(roi_bbox[0] + x0, roi_bbox[1] + y0, roi_bbox[2] + x0, roi_bbox[3] + y0),
        positive_points=tuple((px + x0, py + y0) for px, py in positive),
        negative_points=tuple((px + x0, py + y0) for px, py in negative),
        catalog_reference_image=None if reference is None else str(reference),
        reference_slot=_optional_text(product_config.get("reference_slot")),
        initialization_hint=_optional_text(product_config.get("initialization_hint")),
    )


def _optional_text(value: object) -> Optional[str]:
    return None if value is None else str(value)


def _load_adapter(spec: str, *, frame: np.ndarray, args: argparse.Namespace) -> object:
    module_name, separator, factory_name = spec.partition(":")
    if not separator:
        raise ValueError("--sam-adapter must use module:factory")
    factory = getattr(importlib.import_module(module_name), factory_name)
    parameters = inspect.signature(factory).parameters
    kwargs: dict[str, object] = {}
    if "frame" in parameters:
        kwargs["frame"] = frame
    if "runtime_root" in parameters:
        kwargs["runtime_root"] = args.sam_runtime_root
    if "checkpoint_path" in parameters:
        kwargs["checkpoint_path"] = args.checkpoint_path
    if "output_prob_thresh" in parameters:
        kwargs["output_prob_thresh"] = 0.5
    return factory(**kwargs)


def _close_adapter(adapter: object) -> None:
    close = getattr(adapter, "close", None)
    if callable(close):
        close()


def _overlay(frame: np.ndarray, masks: Sequence[object], surface: ProductDisplaySurface) -> np.ndarray:
    result = frame.copy()
    colors = ((64, 220, 255), (255, 120, 60), (90, 255, 90), (255, 80, 210), (180, 180, 40))
    for index, proposal in enumerate(masks):
        mask = np.asarray(proposal.mask).astype(bool)
        color = np.asarray(colors[index % len(colors)], dtype=np.float32)
        result[mask] = np.clip(result[mask] * 0.45 + color * 0.55, 0, 255).astype(np.uint8)
        x1, y1, _x2, _y2 = proposal.bbox
        cv2.putText(result, proposal.product_key, (x1, max(18, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, tuple(int(v) for v in color), 2)
    height, width = result.shape[:2]
    polygon = np.asarray([(round(x * width), round(y * height)) for x, y in surface.points], dtype=np.int32)
    cv2.polylines(result, [polygon], True, (0, 255, 255), 2)
    return result


def _confirm_masks(
    frame: np.ndarray,
    proposal: object,
    surface: ProductDisplaySurface,
) -> tuple[MaskConfirmation, ...]:
    confirmations = []
    for item in proposal.masks:
        preview = _overlay(frame, (item,), surface)
        cv2.putText(preview, f"Confirm {item.product_key}? Y=yes N=no", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
        cv2.imshow(WINDOW, preview)
        key = cv2.waitKey(0) & 0xFF
        status = "confirmed" if key == ord("y") else "rejected"
        confirmations.append(MaskConfirmation(
            product_key=item.product_key,
            candidate_id=item.candidate_id,
            frame_sha256=proposal.frame_ref.frame_sha256,
            mask_sha256=item.mask_sha256,
            status=status,
            confirmed_at=datetime.now(timezone.utc),
        ))
        if status == "rejected":
            break
    return tuple(confirmations)


def _outside_source_tree(output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return resolved / "dense_product_initialization"
    raise ValueError("artifact output must be outside the source tree")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    def default(value: object) -> object:
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, np.ndarray):
            raise TypeError("raw mask arrays are forbidden in JSON artifacts")
        raise TypeError(f"not JSON serializable: {type(value).__name__}")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=default) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    config_bytes = args.camera_config.read_bytes()
    config = json.loads(config_bytes)
    frame_size = tuple(int(value) for value in config["frame_size"])
    if frame_size != (1280, 720):
        raise ValueError("DPI-01A requires the configured 1280x720 AI coordinate space")
    selected_keys = tuple(str(value) for value in config.get("selected_product_keys", ()))
    if not 3 <= len(selected_keys) <= 5 or len(set(selected_keys)) != len(selected_keys):
        raise ValueError("dense selected_product_keys must contain three to five unique keys")
    products = config["products"]
    if any(key not in products for key in selected_keys):
        raise ValueError("selected_product_keys must come from the approved products")

    time_base, timeline = _probe_video(args.video)
    source_seq = args.source_seq if args.source_seq is not None else _choose_source_seq(args.video, len(timeline))
    if not 0 <= source_seq < len(timeline):
        raise ValueError("--source-seq is outside the probed video timeline")
    raw = _decode_frame(args.video, source_seq)
    frame = cv2.resize(raw, frame_size, interpolation=cv2.INTER_AREA)
    roi = _parse_roi(args.roi, frame_size)
    probed = timeline[source_seq]
    frame_ref = InitializationFrameRef(
        analysis_run_id=args.analysis_run_id,
        source_seq=source_seq,
        source_pts=probed.source_pts,
        time_base_num=time_base.numerator,
        time_base_den=time_base.denominator,
        presentation_seconds=float(probed.source_pts * time_base),
        frame_sha256=frame_sha256(frame),
        frame_size=frame_size,
        roi_xyxy=roi,
        coordinate_transform_version=COORDINATE_TRANSFORM_VERSION,
    )
    selections = tuple(_selection_for_key(frame, key, products[key], roi) for key in selected_keys)
    surface = ProductDisplaySurface(tuple(tuple(float(v) for v in point) for point in config["product_display_surface"]["points"]))
    dense_config = config.get("dense_initialization", {})
    policy = DenseInitializationPolicy(
        approved_product_keys=frozenset(str(key) for key in products),
        product_display_surface=surface,
        frame_size=frame_size,
        surface_overlap_min=float(dense_config.get("surface_overlap_min", config.get("surface_overlap_min", 0.50))),
        bbox_mask_fill_min=float(dense_config.get("bbox_mask_fill_min", 0.10)),
        bbox_frame_area_max=float(dense_config.get("bbox_frame_area_max", 0.35)),
        component_noise_area_min=int(dense_config.get("component_noise_area_min", 16)),
        component_significant_ratio_min=float(dense_config.get("component_significant_ratio_min", 0.05)),
        pairwise_mask_overlap_max=float(dense_config.get("pairwise_mask_overlap_max", 0.10)),
    )
    sam_adapter = _load_adapter(args.sam_adapter, frame=frame, args=args)
    try:
        proposal = build_proposal(frame_ref, selections, sam_adapter, frame=frame, policy=policy)
    except Exception:
        _close_adapter(sam_adapter)
        raise
    artifact_root = _outside_source_tree(args.output_dir)
    if artifact_root.exists() and any(artifact_root.iterdir()):
        raise ValueError(f"artifact directory is not empty: {artifact_root}")
    artifact_root.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(artifact_root / "initialization_frame.jpg"), frame)
    cv2.imwrite(str(artifact_root / "mask_overlay.jpg"), _overlay(frame, proposal.masks, surface))
    config_version = str(config.get("config_version", f"sha256:{hashlib.sha256(config_bytes).hexdigest()}"))
    diagnostic = proposal_diagnostic_payload(proposal, camera_id=str(config["camera_id"]), camera_config_version=config_version)
    _write_json(artifact_root / "initialization_diagnostic.json", diagnostic)
    if proposal.status != "READY_FOR_CONFIRMATION":
        _write_json(artifact_root / "initialization_mapping.json", {**diagnostic, "assignments": [], "status": "REJECTED"})
        _close_adapter(sam_adapter)
        return 2

    confirmations = _confirm_masks(frame, proposal, surface)
    runtime = MultiProductTrackRuntime(
        camera_id=str(config["camera_id"]),
        runtime_instance_id=args.runtime_instance_id,
        products={key: str(products[key]["product_type"]) for key in selected_keys},
        product_display_surface=surface,
        frame_width=frame_size[0], frame_height=frame_size[1],
        surface_overlap_min=float(config.get("surface_overlap_min", 0.50)),
        product_track_lost_after_windows=int(config.get("product_track_lost_after_windows", 3)),
        overlap_iou_min=float(config.get("association_overlap_iou_min", 0.05)),
        geometry_distance_max=float(config.get("association_geometry_distance_max", 0.20)),
        ambiguity_margin=float(config.get("association_ambiguity_margin", 0.05)),
    )
    try:
        result = commit_confirmed(proposal, confirmations, runtime)
    except DenseProductInitializationError as exc:
        diagnostic["status"] = "REJECTED"
        diagnostic["failure_codes"] = list(exc.codes)
        diagnostic["commit_error"] = str(exc)
        confirmation_by_key = {item.product_key: item for item in confirmations}
        for assignment in diagnostic["assignments"]:
            confirmation = confirmation_by_key.get(assignment["product_key"])
            assignment["mask_confirmation"] = (
                {"status": "missing"}
                if confirmation is None
                else {
                    "status": confirmation.status,
                    "confirmed_at": confirmation.confirmed_at.isoformat(),
                }
            )
        _write_json(artifact_root / "initialization_diagnostic.json", diagnostic)
        _write_json(artifact_root / "initialization_mapping.json", {**diagnostic, "assignments": []})
        _close_adapter(sam_adapter)
        return 2

    mask_references = {}
    for item in proposal.masks:
        filename = f"mask_{item.product_key}.png"
        cv2.imwrite(str(artifact_root / filename), item.mask.astype(np.uint8) * 255)
        mask_references[item.product_key] = filename
    mapping = result_mapping_payload(result, camera_id=str(config["camera_id"]), camera_config_version=config_version, mask_references=mask_references)
    _write_json(artifact_root / "initialization_mapping.json", mapping)
    diagnostic["status"] = "COMMITTED"
    diagnostic["confirmations"] = [
        {"product_key": value.product_key, "status": value.status, "confirmed_at": value.confirmed_at.isoformat()}
        for value in confirmations
    ]
    confirmation_by_key = {item.product_key: item for item in confirmations}
    for assignment in diagnostic["assignments"]:
        confirmation = confirmation_by_key[assignment["product_key"]]
        assignment["mask_confirmation"] = {
            "status": confirmation.status,
            "confirmed_at": confirmation.confirmed_at.isoformat(),
        }
    _write_json(artifact_root / "initialization_diagnostic.json", diagnostic)
    _close_adapter(sam_adapter)
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="formal Offline MP4 used by this analysis run")
    parser.add_argument("--camera-config", type=Path, required=True)
    parser.add_argument("--analysis-run-id", required=True)
    parser.add_argument("--runtime-instance-id", required=True)
    parser.add_argument("--sam-adapter", required=True, help="stateful adapter factory as module:factory")
    parser.add_argument("--sam-runtime-root", type=Path, default=Path(r"D:\GJJ\work\sam3"))
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True, help="artifact parent outside the source tree")
    parser.add_argument("--source-seq", type=int, help="decoded frame ordinal; PTS still comes from ffprobe")
    parser.add_argument("--roi", help="optional AI-frame zoom ROI x1,y1,x2,y2")
    return parser.parse_args(argv)


def main() -> int:
    try:
        return run(parse_args())
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
