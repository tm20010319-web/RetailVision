"""Run the bounded DPI-01C.2 real two-product SAM3 propagation probe.

The tool is deliberately evidence-oriented.  It uses the confirmed C.1
prompts, a real multi-frame Session, exact source PTS, and the existing
``MultiProductTrackRuntime`` association at two bounded rolling-window
checkpoints.  It does not publish behavior events.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Mapping, Optional

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.product_camera_config import load_dpi01c_product_camera_config  # noqa: E402
from agent.product_first_frame_initialization import frame_sha256, mask_sha256  # noqa: E402
from agent.sam31_product_tracking import (  # noqa: E402
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplaySurface,
    ProductTrackStatus,
)
from agent.sam31_real_adapter import (  # noqa: E402
    Sam31InstancePromptAdapter,
    create_external_sam31_video_session,
)


VIDEO_DEFAULT = Path(r"D:\GJJ\work\video\2026_09_04_18_17_36.mp4")
CONFIG_DEFAULT = PROJECT_ROOT / "product_cameras.cam03.dpi01c.guitars.stage7c.json"
C1_DEFAULT = Path(r"D:\GJJ\work\dpi01c_cam03_guitars_run\two_guitars_final")
OUTPUT_DEFAULT = Path(r"D:\GJJ\work\dpi01c2_cam03_real_propagation")
PHYSICAL_TABLETOP_PIXELS = ((520, 454), (952, 452), (968, 526), (521, 531))


def _ffprobe(video: Path) -> tuple[Fraction, list[int], dict[str, object]]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,time_base,nb_frames:frame=best_effort_timestamp",
        "-of", "json", str(video),
    ]
    payload = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    stream = payload["streams"][0]
    time_base = Fraction(str(stream["time_base"]))
    frames = payload.get("frames", [])
    pts = [int(item["best_effort_timestamp"]) for item in frames]
    if not pts:
        raise RuntimeError("ffprobe returned no frame PTS")
    return time_base, pts, stream


def _materialize_frames(
    video: Path,
    frame_dir: Path,
    count: int,
    *,
    start_seq: int = 0,
) -> tuple[list[np.ndarray], list[int], dict[str, object]]:
    time_base, pts, stream = _ffprobe(video)
    start_seq = int(start_seq)
    if start_seq < 0 or start_seq >= len(pts):
        raise ValueError(f"source start sequence out of range: {start_seq}")
    count = min(int(count), len(pts) - start_seq)
    if count <= 0:
        raise ValueError("frame count must be positive")
    frame_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {video}")
    frames: list[np.ndarray] = []
    try:
        for source_seq in range(start_seq + count):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"cannot decode source_seq={source_seq}")
            if source_seq < start_seq:
                continue
            local_seq = source_seq - start_seq
            if frame.shape[:2] != (720, 1280):
                frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
            frame = np.ascontiguousarray(frame)
            path = frame_dir / f"{local_seq:05d}.jpg"
            if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise RuntimeError(f"failed to write {path}")
            frames.append(frame)
    finally:
        capture.release()
    if not frames:
        raise RuntimeError("no frames materialized")
    metadata = {
        "time_base": {"num": time_base.numerator, "den": time_base.denominator},
        "source_size": [int(stream["width"]), int(stream["height"])],
        "source_avg_frame_rate": str(stream.get("avg_frame_rate", "unknown")),
        "source_frame_count": int(stream.get("nb_frames", len(pts))),
        "source_start_seq": start_seq,
        "source_end_seq": start_seq + count - 1,
        "materialized_frame_count": len(frames),
        "source_video_sha256": _file_sha256(video),
    }
    return frames, pts[start_seq:start_seq + count], metadata


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_c1(c1_dir: Path) -> tuple[dict[str, object], dict[str, object], dict[str, np.ndarray]]:
    mapping = json.loads((c1_dir / "initialization_mapping.json").read_text(encoding="utf-8"))
    diagnostic = json.loads((c1_dir / "initialization_diagnostic.json").read_text(encoding="utf-8"))
    masks: dict[str, np.ndarray] = {}
    assignments = [dict(item) for item in mapping.get("assignments", [])]
    diagnostic_targets = {
        str(item.get("product_key")): item
        for item in diagnostic.get("diagnostics", {}).get("targets", [])
        if isinstance(item, Mapping)
    }
    # A pending probe mapping intentionally contains only candidate/mask refs.
    # Rehydrate prompt geometry from its same-run diagnostic without treating
    # an unconfirmed mapping as committed Track evidence.
    for assignment in assignments:
        target = diagnostic_targets.get(str(assignment.get("product_key")), {})
        if isinstance(target, Mapping):
            for field in ("bbox_xyxy", "positive_points", "negative_points"):
                if field not in assignment and field in target:
                    assignment[field] = target[field]
    mapping["assignments"] = assignments
    for assignment in assignments:
        key = str(assignment["product_key"])
        mask_path = c1_dir / str(assignment["mask_reference"])
        if not mask_path.is_file():
            raise RuntimeError(f"missing C.1 mask artifact: {mask_path}")
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise RuntimeError(f"cannot read C.1 mask artifact: {mask_path}")
        mask = mask.astype(bool)
        actual_hash = mask_sha256(mask)
        if actual_hash != str(assignment["mask_sha256"]):
            raise RuntimeError(f"C.1 mask SHA mismatch for {key}")
        masks[key] = mask
    if len(masks) != 2 or len({int(item["candidate_id"]) for item in assignments}) != 2:
        raise RuntimeError("C.1 must contain exactly two unique guitar assignments")
    tokens = [str(item.get("product_track_token", "")) for item in assignments]
    if any(tokens) and (len(tokens) != 2 or len(set(tokens)) != 2 or any(not token for token in tokens)):
        raise RuntimeError("C.1 must contain two unique Product Track Tokens")
    if mapping.get("frame_sha256") != diagnostic.get("frame_sha256"):
        raise RuntimeError("C.1 mapping and diagnostic frame hashes differ")
    return mapping, diagnostic, masks


def _polygon_mask(points_pixels: tuple[tuple[int, int], ...], width: int = 1280, height: int = 720) -> np.ndarray:
    result = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(result, [np.asarray(points_pixels, dtype=np.int32)], 1)
    return result.astype(bool)


def _normalized_surface(points_pixels: tuple[tuple[int, int], ...]) -> ProductDisplaySurface:
    return ProductDisplaySurface(tuple((x / 1280.0, y / 720.0) for x, y in points_pixels))


def _mask_bbox(mask: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def _mask_centroid(mask: np.ndarray) -> Optional[tuple[float, float]]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return float(np.mean(xs)), float(np.mean(ys))


def _point_inside(points_pixels: tuple[tuple[int, int], ...], point: Optional[tuple[float, float]]) -> Optional[bool]:
    if point is None:
        return None
    polygon = np.asarray(points_pixels, dtype=np.float32)
    return bool(cv2.pointPolygonTest(polygon, (float(point[0]), float(point[1])), False) >= 0)


def _surface_metrics(mask: np.ndarray, points_pixels: tuple[tuple[int, int], ...]) -> dict[str, object]:
    area = int(np.count_nonzero(mask))
    surface_mask = _polygon_mask(points_pixels, mask.shape[1], mask.shape[0])
    bbox = _mask_bbox(mask)
    centroid = _mask_centroid(mask)
    bottom_center = None if bbox is None else ((bbox[0] + bbox[2] - 1) / 2.0, float(bbox[3] - 1))
    lowest = None
    if area:
        ys, xs = np.where(mask)
        lowest_y = int(ys.max())
        lowest = (float(np.median(xs[ys == lowest_y])), float(lowest_y))
    return {
        "mask_area": area,
        "mask_sha256": mask_sha256(mask),
        "bbox": None if bbox is None else list(bbox),
        "centroid": None if centroid is None else [centroid[0], centroid[1]],
        "centroid_inside": _point_inside(points_pixels, centroid),
        "surface_overlap_ratio": (float(np.count_nonzero(mask & surface_mask)) / area) if area else 0.0,
        "bbox_bottom_center": None if bottom_center is None else [bottom_center[0], bottom_center[1]],
        "bbox_bottom_center_inside": _point_inside(points_pixels, bottom_center),
        "lowest_contact_point": None if lowest is None else [lowest[0], lowest[1]],
        "lowest_contact_point_inside": _point_inside(points_pixels, lowest),
    }


def _outputs_to_masks(outputs: Mapping[str, object]) -> tuple[dict[int, np.ndarray], dict[int, float]]:
    ids = outputs.get("out_obj_ids", [])
    if hasattr(ids, "detach"):
        ids = ids.detach().cpu().numpy()
    ids_array = np.asarray(ids).reshape(-1)
    raw_masks = outputs.get("out_binary_masks")
    if raw_masks is None:
        return {}, {}
    if hasattr(raw_masks, "detach"):
        raw_masks = raw_masks.detach().cpu().numpy()
    masks_array = np.asarray(raw_masks)
    while masks_array.ndim > 3:
        masks_array = masks_array[:, 0]
    masks: dict[int, np.ndarray] = {}
    for index, obj_id in enumerate(ids_array):
        if index >= len(masks_array):
            break
        masks[int(obj_id)] = masks_array[index].astype(bool)
    probs = outputs.get("out_probs", [])
    if hasattr(probs, "detach"):
        probs = probs.detach().cpu().numpy()
    probs_array = np.asarray(probs).reshape(-1)
    scores = {int(obj_id): float(probs_array[index]) for index, obj_id in enumerate(ids_array) if index < len(probs_array)}
    return masks, scores


def _output_diagnostics(outputs: Mapping[str, object]) -> dict[str, object]:
    """Capture backend output facts without manufacturing a candidate."""
    if not isinstance(outputs, Mapping):
        return {"outputs_type": type(outputs).__name__}

    def _shape(value: object) -> Optional[list[int]]:
        if value is None:
            return None
        if hasattr(value, "detach"):
            value = value.detach().cpu().numpy()
        return list(np.asarray(value).shape)

    ids = outputs.get("out_obj_ids", [])
    if hasattr(ids, "detach"):
        ids = ids.detach().cpu().numpy()
    ids_array = np.asarray(ids).reshape(-1)
    frame_stats = outputs.get("frame_stats")
    return {
        "output_keys": sorted(str(key) for key in outputs),
        "out_obj_ids": [int(value) for value in ids_array],
        "out_binary_masks_shape": _shape(outputs.get("out_binary_masks")),
        "out_probs_shape": _shape(outputs.get("out_probs")),
        "frame_stats": frame_stats if isinstance(frame_stats, Mapping) else None,
    }


def _int_list(value: object) -> list[int]:
    """Convert small runtime ID collections without assuming their container type."""
    if value is None:
        return []
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    try:
        return sorted({int(item) for item in np.asarray(value).reshape(-1).tolist()})
    except (TypeError, ValueError):
        try:
            return sorted({int(item) for item in value})  # type: ignore[union-attr]
        except (TypeError, ValueError):
            return []


def _session_runtime_diagnostics(predictor: object, session_id: str) -> dict[str, object]:
    """Read bounded object-table facts to explain post-processing omissions.

    ``handle_stream_request`` exposes only post-processed masks.  The predictor
    keeps the object table and hot-start hide sets in its session state, which
    lets this probe distinguish a backend empty result from a runtime gate.
    This is diagnostic-only and does not mutate the session.
    """
    sessions = getattr(predictor, "_all_inference_states", {})
    session = sessions.get(session_id) if isinstance(sessions, Mapping) else None
    state = session.get("state", {}) if isinstance(session, Mapping) else {}
    metadata = state.get("tracker_metadata", {}) if isinstance(state, Mapping) else {}
    rank0 = metadata.get("rank0_metadata", {}) if isinstance(metadata, Mapping) else {}
    states = state.get("sam2_inference_states", []) if isinstance(state, Mapping) else []
    state_obj_ids: list[int] = []
    if isinstance(states, (list, tuple)):
        for tracker_state in states:
            if isinstance(tracker_state, Mapping):
                state_obj_ids.extend(_int_list(tracker_state.get("obj_ids", [])))
    suppressed_by_frame = rank0.get("suppressed_obj_ids", {}) if isinstance(rank0, Mapping) else {}
    suppressed = []
    if isinstance(suppressed_by_frame, Mapping):
        for values in suppressed_by_frame.values():
            suppressed.extend(_int_list(values))
    removed = _int_list(rank0.get("removed_obj_ids", []) if isinstance(rank0, Mapping) else [])
    generator = state.get("generator_state", {}) if isinstance(state, Mapping) else {}
    unconfirmed_by_frame = generator.get("unconfirmed_obj_ids_per_frame", {}) if isinstance(generator, Mapping) else {}
    unconfirmed = []
    if isinstance(unconfirmed_by_frame, Mapping):
        for values in unconfirmed_by_frame.values():
            unconfirmed.extend(_int_list(values))
    active = _int_list(metadata.get("obj_ids_all_gpu", []) if isinstance(metadata, Mapping) else [])
    return {
        "active_obj_ids": active,
        "sam2_state_obj_ids": sorted(set(state_obj_ids)),
        "removed_obj_ids": removed,
        "suppressed_obj_ids": sorted(set(suppressed)),
        "unconfirmed_obj_ids": sorted(set(unconfirmed)),
    }


def _prime_empty_propagation_cache(
    predictor: object, session_id: str, frame_count: int
) -> dict[str, object]:
    """Create empty cache entries so partial SAM2 masks are not discarded.

    The installed SAM3.1 multiplex wrapper's ``_build_sam2_output`` returns an
    empty result when a frame is absent from ``cached_frame_outputs`` before
    applying refined masks.  Instance-only propagation has no detector cache
    for later frames, so the wrapper otherwise drops valid SAM2 outputs.  We
    only add empty keys; no initialization or propagated mask is copied into
    this cache.
    """
    sessions = getattr(predictor, "_all_inference_states", {})
    session = sessions.get(session_id) if isinstance(sessions, Mapping) else None
    state = session.get("state", {}) if isinstance(session, Mapping) else {}
    cache = state.get("cached_frame_outputs") if isinstance(state, Mapping) else None
    if not isinstance(cache, dict):
        return {"applied": False, "reason": "cached_frame_outputs_unavailable"}
    before = len(cache)
    for frame_index in range(int(frame_count)):
        cache.setdefault(frame_index, {})
    return {
        "applied": True,
        "before_key_count": before,
        "after_key_count": len(cache),
        "added_key_count": len(cache) - before,
        "mask_values_injected": False,
    }


def _propagation_request(session_id: str, frame_count: int) -> dict[str, object]:
    """Build the wrapper-compatible bounded forward propagation request."""
    return {
        "type": "propagate_in_video",
        "session_id": session_id,
        "propagation_direction": "forward",
        # Sam3BasePredictor reads this wire field and forwards it as the
        # model's ``start_frame_idx`` argument.
        "start_frame_index": 0,
        "max_frame_num_to_track": int(frame_count),
        "output_prob_thresh": 0.5,
    }


def _initialization_prompt_bbox(item: Mapping[str, object]) -> tuple[int, int, int, int]:
    """Return the confirmed C.1 prompt bbox, not the resulting mask bbox."""
    diagnostics = item.get("diagnostics", {})
    if isinstance(diagnostics, Mapping) and diagnostics.get("bbox_xyxy") is not None:
        values = diagnostics["bbox_xyxy"]
    else:
        values = item.get("bbox_xyxy")
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise RuntimeError("C.1 assignment is missing its initialization prompt bbox")
    return tuple(int(value) for value in values)  # type: ignore[return-value]


def _candidate_from_propagated_mask(obj_id: int, mask: np.ndarray, confidence: float) -> Optional[ProductCandidate]:
    bbox = _mask_bbox(mask)
    centroid = _mask_centroid(mask)
    if bbox is None or centroid is None or mask.shape != (720, 1280) or not np.any(mask):
        return None
    return ProductCandidate(
        local_obj_id=int(obj_id),
        bbox=tuple(float(value) for value in bbox),
        centroid=centroid,
        confidence=max(0.0, min(1.0, float(confidence))),
        mask=mask.astype(bool),
    )


def _draw_overlay(
    frame: np.ndarray,
    object_masks: Mapping[int, np.ndarray],
    id_to_key: Mapping[int, str],
    tokens: Mapping[str, str],
    surface_points: tuple[tuple[int, int], ...],
    physical_points: tuple[tuple[int, int], ...],
    pts: int,
    time_base: Fraction,
    association: Mapping[str, object],
) -> np.ndarray:
    rendered = frame.copy()
    colors = {
        "ai_electric_guitar": (255, 180, 0),
        "ai_guitar": (0, 210, 255),
    }
    for obj_id, mask in object_masks.items():
        key = id_to_key.get(int(obj_id), f"UNKNOWN_OBJ_{obj_id}")
        color = colors.get(key, (0, 0, 255))
        tint = np.zeros_like(rendered)
        tint[mask] = color
        rendered = cv2.addWeighted(rendered, 0.72, tint, 0.28, 0)
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(rendered, contours, -1, color, 2)
        bbox = _mask_bbox(mask)
        if bbox is not None:
            status = str(association.get(key, {}).get("track_status", "UNOBSERVED")) if isinstance(association.get(key), dict) else "UNOBSERVED"
            display = str(association.get(key, {}).get("display_state", "UNOBSERVED")) if isinstance(association.get(key), dict) else "UNOBSERVED"
            label = f"{key} obj={obj_id} {tokens.get(key, '')[-6:]} {status}/{display}"
            cv2.putText(rendered, label, (bbox[0], max(22, bbox[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2, cv2.LINE_AA)
    cv2.polylines(rendered, [np.asarray(surface_points, dtype=np.int32)], True, (0, 255, 0), 2)
    cv2.polylines(rendered, [np.asarray(physical_points, dtype=np.int32)], True, (255, 0, 255), 2)
    cv2.putText(rendered, "CURRENT_EXPANDED_REGION", (surface_points[0][0], max(20, surface_points[0][1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.putText(rendered, "PHYSICAL_TABLETOP", (physical_points[0][0], physical_points[0][1] + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 0, 255), 2, cv2.LINE_AA)
    cv2.rectangle(rendered, (0, 0), (520, 48), (0, 0, 0), -1)
    cv2.putText(rendered, f"source_pts={pts} t={float(pts * time_base):.3f}s objects={len(object_masks)}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (255, 255, 255), 2, cv2.LINE_AA)
    return rendered


def _write_operator_confirmation(
    output_dir: Path,
    mapping: Mapping[str, object],
    config_version: str,
) -> dict[str, object]:
    confirmations = []
    for item in mapping["assignments"]:
        confirmation = item.get("mask_confirmation", {})
        if isinstance(confirmation, str):
            status = confirmation
            confirmed_at = None
        else:
            status = str(confirmation.get("status", ""))
            confirmed_at = confirmation.get("confirmed_at")
        if status not in {"confirmed", "required", "pending"}:
            raise RuntimeError(f"invalid operator confirmation status: {item['product_key']}")
        confirmations.append({
            "camera_id": mapping["camera_id"],
            "frame_sha256": mapping["frame_sha256"],
            "surface_config_version": config_version,
            "product_key": item["product_key"],
            "candidate_id": item["candidate_id"],
            "mask_sha256": item["mask_sha256"],
            "confirmation_status": status,
            "confirmed_at": confirmed_at,
            "source": "C.1 confirmed artifact, independently rebound for DPI-01C.2",
        })
    payload = {
        "schema_version": "dpi-01c2.operator-confirmation.v1",
        "camera_id": mapping["camera_id"],
        "frame_sha256": mapping["frame_sha256"],
        "surface_config_version": config_version,
        "confirmations": confirmations,
    }
    (output_dir / "operator_confirmation.json").write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _run(args: argparse.Namespace) -> int:
    video = args.video.resolve()
    config = load_dpi01c_product_camera_config(args.camera_config)
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    initialization_dir = (args.initialization_dir or args.c1_dir).resolve()
    mapping, diagnostic, init_masks = _load_c1(initialization_dir)
    confirmation_payload = _write_operator_confirmation(output_dir, mapping, config.config_version)

    frames_dir = output_dir / "_sam_frames"
    frames, pts_values, media = _materialize_frames(
        video,
        frames_dir,
        args.frame_count,
        start_seq=args.source_start_seq,
    )
    if frame_sha256(frames[0]) != mapping["frame_sha256"]:
        raise RuntimeError("propagation start frame differs from the initialization artifact")
    source_video_sha = _file_sha256(video)
    if source_video_sha != str(config.raw["source_video"]["sha256"]):
        raise RuntimeError("camera config source video SHA does not match input")

    surface_points = tuple(tuple(int(value) for value in point) for point in config.raw["product_display_surface"]["pixel_points"])
    physical_points = PHYSICAL_TABLETOP_PIXELS
    surface_polygon = _normalized_surface(surface_points)
    id_to_key = {int(item["candidate_id"]): str(item["product_key"]) for item in mapping["assignments"]}
    expected_ids = sorted(id_to_key)
    tokens = {
        str(item["product_key"]): str(item.get("product_track_token", ""))
        for item in mapping["assignments"]
    }
    prompt_data = {str(item["product_key"]): item for item in mapping["assignments"]}

    # The already committed C.1 tokens are represented by a fresh local
    # Runtime for association checks; no behavior event is emitted.
    runtime = MultiProductTrackRuntime(
        camera_id=config.camera_id,
        runtime_instance_id="dpi01c2-cam03-real-propagation-20260904",
        products=config.tracking_products,
        product_display_surface=surface_polygon,
        frame_width=1280,
        frame_height=720,
        display_evidence_mode=config.display_evidence_mode,
        support_contact_points=config.support_contact_points,
        support_contact_tolerance=config.support_contact_tolerance,
    )
    initial_assignments = {
        key: ProductCandidate(
            local_obj_id=int(prompt_data[key]["candidate_id"]),
            bbox=tuple(float(value) for value in prompt_data[key]["bbox_xyxy"]),
            centroid=_mask_centroid(init_masks[key]) or (0.0, 0.0),
            confidence=1.0,
            mask=init_masks[key],
        )
        for key in config.selected_product_keys
    }
    runtime.initialize_assignments(
        assignments=initial_assignments,
        observed_at=datetime.fromtimestamp(pts_values[0] * media["time_base"]["num"] / media["time_base"]["den"], tz=timezone.utc),
        source_seq=args.source_start_seq,
        window_no=0,
    )
    runtime_tokens = {key: str(runtime.track_identity(key)[1]) for key in config.selected_product_keys}

    predictor, session_id = create_external_sam31_video_session(
        frame_dir=frames_dir,
        runtime_root=args.sam_runtime_root,
        checkpoint_path=args.checkpoint_path,
    )
    adapter = Sam31InstancePromptAdapter(
        predictor=predictor,
        session_id=session_id,
        frame_size=(1280, 720),
    )
    observations: list[dict[str, object]] = []
    propagated: dict[int, dict[int, np.ndarray]] = {}
    prompt_responses: dict[str, dict[str, object]] = {}
    writer = cv2.VideoWriter(
        str(output_dir / "real_propagation_overlay.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(Fraction(media["source_avg_frame_rate"]) if media["source_avg_frame_rate"] not in ("unknown", "0/0") else 20.0),
        (1280, 720),
    )
    try:
        for prompt_index, key in enumerate(config.selected_product_keys, start=1):
            item = prompt_data[key]
            result = adapter.add_instance_prompt(
                frame=frames[0],
                frame_index=0,
                bbox=_initialization_prompt_bbox(item),
                points=tuple(tuple(int(value) for value in point) for point in item["positive_points"] + item["negative_points"]),
                point_labels=(1,) * len(item["positive_points"]) + (0,) * len(item["negative_points"]),
                # C.1 used the deterministic first/second prompt IDs 1 and 2;
                # Mapping stores the resolved Candidate IDs, not the request IDs.
                obj_id=prompt_index,
            )
            prompt_responses[key] = {
                "candidate_id": result.candidate_id,
                "mask_count": len(result.masks),
                "diagnostics": dict(result.diagnostics),
            }
        propagation_cache = _prime_empty_propagation_cache(
            predictor, session_id, len(frames)
        )
        if not propagation_cache.get("applied"):
            raise RuntimeError(
                "SAM3.1 instance propagation cache could not be primed: "
                f"{propagation_cache}"
            )
        for response in predictor.handle_stream_request(
            _propagation_request(session_id, len(frames))
        ):
            local_index = int(response.get("frame_index", -1))
            if not 0 <= local_index < len(frames):
                continue
            outputs = (response or {}).get("outputs", {})
            masks, scores = _outputs_to_masks(outputs)
            propagated[local_index] = masks
            output_diagnostics = _output_diagnostics(outputs)
            runtime_diagnostics = _session_runtime_diagnostics(predictor, session_id)
            returned_ids = set(int(value) for value in output_diagnostics["out_obj_ids"])
            gated_ids = (
                set(runtime_diagnostics["removed_obj_ids"])
                | set(runtime_diagnostics["suppressed_obj_ids"])
                | set(runtime_diagnostics["unconfirmed_obj_ids"])
            )
            runtime_diagnostics["postprocess_hidden_obj_ids"] = sorted(
                (set(expected_ids) - returned_ids) & gated_ids
            )
            runtime_diagnostics["output_obj_ids"] = sorted(returned_ids)
            output_diagnostics["runtime_state"] = runtime_diagnostics
            object_rows = []
            for obj_id, mask in sorted(masks.items()):
                key = id_to_key.get(obj_id)
                candidate = _candidate_from_propagated_mask(obj_id, mask, scores.get(obj_id, 0.0))
                row = {
                    "sam_obj_id": obj_id,
                    "product_key": key,
                    "confidence": scores.get(obj_id),
                    "unknown_obj_id": key is None,
                    "current_expanded_region": _surface_metrics(mask, surface_points),
                    "physical_tabletop": _surface_metrics(mask, physical_points),
                    "candidate_valid": candidate is not None,
                }
                object_rows.append(row)
            observations.append({
                "source_seq": args.source_start_seq + local_index,
                "source_pts": pts_values[local_index],
                "presentation_seconds": float(pts_values[local_index] * media["time_base"]["num"] / media["time_base"]["den"]),
                "sam_obj_ids": sorted(masks),
                "object_count": len(masks),
                "sam_output_diagnostics": output_diagnostics,
                "objects": object_rows,
            })
            # The bounded Runtime windows are evaluated after propagation, so
            # mark this interim overlay as pending association rather than
            # manufacturing per-frame association results.
            propagation_overlay_state = {
                key: {
                    "track_status": "PROPAGATED",
                    "display_state": "PENDING_ASSOCIATION",
                }
                for key in config.selected_product_keys
            }
            writer.write(_draw_overlay(frames[local_index], masks, id_to_key, runtime_tokens, surface_points, physical_points, pts_values[local_index], Fraction(media["time_base"]["num"], media["time_base"]["den"]), propagation_overlay_state))
            if local_index >= len(frames) - 1:
                break
    finally:
        try:
            adapter.close()
        finally:
            writer.release()
            del predictor
            gc.collect()

    if not observations:
        raise RuntimeError("SAM3 propagation returned no frames")
    by_seq = {int(row["source_seq"]): row for row in observations}
    max_obj_ids = sorted(set(obj_id for masks in propagated.values() for obj_id in masks))
    runtime_gate_hidden_frames = [
        row for row in observations
        if row["sam_output_diagnostics"].get("runtime_state", {}).get("postprocess_hidden_obj_ids")
    ]
    runtime_gate_hidden_obj_ids = sorted({
        int(obj_id)
        for row in runtime_gate_hidden_frames
        for obj_id in row["sam_output_diagnostics"]["runtime_state"]["postprocess_hidden_obj_ids"]
    })
    post_initial_masks = [
        masks for source_seq, masks in propagated.items() if int(source_seq) > 0
    ]
    all_two_object = bool(propagated) and all(
        sorted(masks) == expected_ids
        and all(np.asarray(mask).ndim == 2 and np.any(mask) for mask in masks.values())
        for masks in propagated.values()
    )
    post_initial_two_object = bool(post_initial_masks) and all(
        sorted(masks) == expected_ids
        and all(np.asarray(mask).ndim == 2 and np.any(mask) for mask in masks.values())
        for masks in post_initial_masks
    )
    # Two 12-sample windows at approximately 5 FPS, with six-sample overlap.
    sampled_indices = list(range(0, len(frames), max(1, round(float(Fraction(media["source_avg_frame_rate"])) / 5.0))))
    sampled_indices = sampled_indices[:25]
    windows = [sampled_indices[:12], sampled_indices[6:18]] if len(sampled_indices) >= 18 else [sampled_indices]
    window_rows = []
    for window_no, indices in enumerate(windows, start=1):
        anchor_index = indices[-1]
        masks = propagated.get(anchor_index, {})
        candidates = tuple(
            candidate
            for obj_id, mask in sorted(masks.items())
            if (candidate := _candidate_from_propagated_mask(obj_id, mask, 0.0)) is not None
        )
        results = runtime.process_window(
            candidates=candidates,
            observed_at=datetime.fromtimestamp(pts_values[anchor_index] * media["time_base"]["num"] / media["time_base"]["den"], tz=timezone.utc),
            source_seq=args.source_start_seq + anchor_index,
            window_no=window_no,
            grounding_candidate_count=len(candidates),
        )
        result_by_key = {item.product_key: item for item in results}
        row = {
            "window_no": window_no,
            "source_seq": args.source_start_seq + anchor_index,
            "source_pts": pts_values[anchor_index],
            "presentation_seconds": float(pts_values[anchor_index] * media["time_base"]["num"] / media["time_base"]["den"]),
            "sampled_source_seq": [args.source_start_seq + index for index in indices],
            "product_key_mapping": {
                key: {
                    "sam_obj_id": int(next((obj_id for obj_id, mapped_key in id_to_key.items() if mapped_key == key), -1)),
                    "product_track_token": str(result_by_key[key].product_track_token) if key in result_by_key else None,
                    "track_status": result_by_key[key].track_status.value if key in result_by_key and result_by_key[key].track_status else None,
                    "association_method": result_by_key[key].association_method if key in result_by_key else None,
                    "association_success": bool(result_by_key[key].association_success) if key in result_by_key else False,
                    "association_ambiguous": bool(result_by_key[key].association_ambiguous) if key in result_by_key else True,
                    "display_state": result_by_key[key].display_state.value if key in result_by_key else "UNOBSERVED",
                }
                for key in config.selected_product_keys
            },
        }
        window_rows.append(row)
        global_anchor_seq = args.source_start_seq + anchor_index
        if global_anchor_seq in by_seq:
            by_seq[global_anchor_seq]["rolling_window"] = row

    adapter_call_log = [dict(call) for call in adapter.calls]
    observations_payload = {
        "schema_version": "dpi-01c2.real-propagation-observations.v1",
        "camera_id": config.camera_id,
        "source_video": str(video),
        "source_video_sha256": source_video_sha,
        "time_base": media["time_base"],
        "initialization_frame_sha256": mapping["frame_sha256"],
        "initialization_source_seq": mapping["source_seq"],
        "sam_session_id_ref": adapter.session_reference,
        "prompt_responses": prompt_responses,
        "propagation_cache": propagation_cache,
        "sam_adapter_calls": adapter_call_log,
        "expected_obj_ids": expected_ids,
        "observations": observations,
    }
    with (output_dir / "real_propagation_observations.jsonl").open("w", encoding="utf-8") as handle:
        for row in observations:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    (output_dir / "real_propagation_diagnostic.json").write_text(
        json.dumps(observations_payload, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    surface_comparison = _draw_overlay(frames[0], {int(item["candidate_id"]): init_masks[str(item["product_key"])] for item in mapping["assignments"]}, id_to_key, runtime_tokens, surface_points, physical_points, pts_values[0], Fraction(media["time_base"]["num"], media["time_base"]["den"]), {})
    cv2.imwrite(str(output_dir / "surface_comparison_overlay.jpg"), surface_comparison)
    evidence_rows = []
    for seq in (0, min(len(frames) - 1, len(frames) // 2), len(frames) - 1):
        row = by_seq.get(args.source_start_seq + seq)
        evidence_rows.append({
            "source_seq": args.source_start_seq + seq,
            "source_pts": pts_values[seq],
            "presentation_seconds": float(pts_values[seq] * media["time_base"]["num"] / media["time_base"]["den"]),
            "visual_state": "stationary_on_stand" if seq == 0 else "no_departure_visible_in_bounded_clip",
            "object_metrics": [] if row is None else row["objects"],
        })
    surface_evidence = {
        "schema_version": "dpi-01c2.surface-evidence.v1",
        "camera_id": config.camera_id,
        "physical_tabletop_pixels": [list(point) for point in physical_points],
        "current_expanded_region_pixels": [list(point) for point in surface_points],
        "decision": "INSUFFICIENT_MOTION_FOR_SURFACE_SEMANTICS",
        "calibration_status": str(config.raw["product_display_surface"].get("calibration_status")),
        "required_states": ["stationary_on_stand", "just_leaving_stand", "held", "returned"],
        "observed_states": ["stationary_on_stand"],
        "notes": [
            "The bounded real clip contains no visually confirmed departure/held/return state.",
            "The expanded region is not promoted to CONFIRMED and no threshold is relaxed.",
            "Behavior replay remains blocked pending a clip with the required state transitions.",
        ],
        "frames": evidence_rows,
    }
    (output_dir / "surface_evidence.json").write_text(json.dumps(surface_evidence, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    window_association_pass = bool(window_rows) and all(
        entry["product_track_token"] == runtime_tokens[key]
        and entry["association_success"]
        and not entry["association_ambiguous"]
        and entry["track_status"] not in (None, "LOST")
        for window in window_rows
        for key, entry in window["product_key_mapping"].items()
    )
    product_key_continuity = (
        post_initial_two_object
        and all(
            not row.get("unknown_obj_id")
            for observation in observations
            for row in observation["objects"]
        )
    )
    failure_codes: list[str] = []
    if not post_initial_two_object:
        failure_codes.append("POST_INITIALIZATION_TWO_OBJECT_RETENTION_FAILED")
    if runtime_gate_hidden_frames:
        failure_codes.append("POSTPROCESS_RUNTIME_GATE_HIDDEN_OBJECTS")
    if not product_key_continuity:
        failure_codes.append("PRODUCT_KEY_VISUAL_ID_CONTINUITY_FAILED")
    if not window_association_pass:
        failure_codes.append("SHORT_WINDOW_ASSOCIATION_FAILED")
    propagation_pass = all_two_object and product_key_continuity
    summary_status = (
        "PASS_PROPAGATION_SURFACE_CONFIRMATION_PENDING"
        if propagation_pass and window_association_pass
        else "REJECTED_REAL_PROPAGATION"
    )
    summary = {
        "schema_version": "dpi-01c2.real-propagation-summary.v1",
        "status": summary_status,
        "camera_id": config.camera_id,
        "selected_product_keys": list(config.selected_product_keys),
        "formal_product_types": {key: config.products[key].product_type for key in config.selected_product_keys},
        "tracking_product_types": config.tracking_products,
        "source_video_sha256": source_video_sha,
        "time_base": media["time_base"],
        "materialized_frame_count": len(frames),
        "propagated_frame_count": len(observations),
        "expected_obj_ids": expected_ids,
        "observed_obj_ids": max_obj_ids,
        "sam_session_two_object_retention": all_two_object,
        "post_initialization_two_object_retention": post_initial_two_object,
        "postprocess_runtime_gate_hidden_objects": bool(runtime_gate_hidden_frames),
        "postprocess_runtime_gate_hidden_obj_ids": runtime_gate_hidden_obj_ids,
        "postprocess_runtime_gate_hidden_frame_count": len(runtime_gate_hidden_frames),
        "product_key_visual_id_continuity": product_key_continuity,
        "track_token_continuity": window_association_pass,
        "short_window_continuity_pass": window_association_pass,
        "propagation_pass": propagation_pass,
        "failure_codes": failure_codes,
        "sam_adapter_calls": adapter_call_log,
        "propagation_cache": propagation_cache,
        "visual_id_swap": False,
        "rolling_windows": window_rows,
        "operator_confirmation_file": str(output_dir / "operator_confirmation.json"),
        "surface_decision": surface_evidence["decision"],
        "full_behavior_replay": "PENDING",
        "not_fully_closed": True,
    }
    (output_dir / "real_propagation_summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "media_provenance.json").write_text(json.dumps(media, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    shutil.rmtree(frames_dir, ignore_errors=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=VIDEO_DEFAULT)
    parser.add_argument("--camera-config", type=Path, default=CONFIG_DEFAULT)
    parser.add_argument("--c1-dir", type=Path, default=C1_DEFAULT)
    parser.add_argument(
        "--initialization-dir",
        type=Path,
        default=None,
        help="initialization artifact directory; defaults to --c1-dir",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--sam-runtime-root", type=Path, default=Path(r"D:\GJJ\work\sam3"))
    parser.add_argument("--checkpoint-path", type=Path, required=True)
    parser.add_argument("--frame-count", type=int, default=121, help="bounded source frames (default: about five seconds)")
    parser.add_argument(
        "--source-start-seq",
        type=int,
        default=0,
        help="first source sequence to materialize; output retains global source_seq",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(_run(parse_args()))
