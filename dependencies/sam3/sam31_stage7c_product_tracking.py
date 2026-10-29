"""Stage 7C live SAM 3.1 product observation and rolling-window tracking."""

from __future__ import annotations

import argparse
import gc
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from rv_handoff_paths import CODE_ROOT, AGENTS_PYTHON, RUNTIME_OUTPUT, project_config
from typing import Optional

import cv2
import numpy as np
import torch

import sam31_stage5_1_1_demo_latency_finalization as base


RETAILVISION_ROOT = CODE_ROOT
if str(RETAILVISION_ROOT) not in sys.path:
    sys.path.insert(0, str(RETAILVISION_ROOT))

from rv_handoff_product_tracking import (  # noqa: E402
    GroundingDecision,
    ManualInitializationAttemptTracker,
    ProductCandidate,
    ProductTrackRuntime,
    ProductTrackStatus,
    ProductDisplaySurface,
    grounding_decision,
    pixel_xyxy_to_normalized_xywh,
)


CONFIG_PATH = project_config("product_cameras.cam02.stage7c.json")
OUTPUT_PATH = RUNTIME_OUTPUT / "stage7c_cam02_product_live_test.jsonl"
RUN_MINUTES = 8.0
RUNTIME_INSTANCE_ID = datetime.now().strftime("stage7c-%Y%m%d-%H%M%S")


@dataclass
class ProductSessionState:
    product_type: str
    local_obj_id: Optional[int] = None
    old_overlap_bank: Optional[dict[int, dict[int, np.ndarray]]] = None
    last_bbox: Optional[tuple[float, float, float, float]] = None
    initialization_method: Optional[str] = None
    last_confidence: float = 1.0
    manual_init_bbox: Optional[tuple[int, int, int, int]] = None


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manual-bbox",
        action="append",
        nargs=5,
        metavar=("PRODUCT_TYPE", "X1", "Y1", "X2", "Y2"),
        help=(
            "Runtime-local 1280x720 AI-frame bbox used when that product's "
            "Text Grounding is unstable. Historical bboxes are not reused."
        ),
    )
    parser.add_argument(
        "--interactive-manual-bbox",
        action="append",
        choices=("smart_toy", "smart_guitar"),
        metavar="PRODUCT_TYPE",
        help="Prompt for a current-frame bbox when Text Grounding is unstable.",
    )
    return parser.parse_args()


def parse_manual_bboxes(values):
    result = {}
    for value in values or ():
        product_type, *coordinates = value
        if product_type not in {"smart_toy", "smart_guitar"}:
            raise ValueError(f"unsupported manual bbox product_type: {product_type}")
        if product_type in result:
            raise ValueError(f"duplicate manual bbox for {product_type}")
        try:
            bbox = tuple(int(coordinate) for coordinate in coordinates)
        except ValueError as exc:
            raise ValueError("manual bbox coordinates must be integers") from exc
        pixel_xyxy_to_normalized_xywh(
            bbox,
            frame_width=base.AI_WIDTH,
            frame_height=base.AI_HEIGHT,
        )
        result[product_type] = bbox
    return result


def load_config():
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if payload.get("frame_size") != [base.AI_WIDTH, base.AI_HEIGHT]:
        raise RuntimeError("Stage 7C config must match the 1280x720 AI frame")
    area = payload.get("product_display_surface")
    if not isinstance(area, dict) or area.get("area_type") != "display":
        raise RuntimeError("product_display_surface must be configured as display")
    if area.get("calibration_image_size") != [base.AI_WIDTH, base.AI_HEIGHT]:
        raise RuntimeError("product_display_surface calibration must be 1280x720")
    surface = ProductDisplaySurface(
        tuple((float(x), float(y)) for x, y in area["points"])
    )
    return payload, surface


def probabilities(outputs) -> dict[int, float]:
    ids = np.asarray(base.to_numpy(outputs.get("out_obj_ids", []))).reshape(-1)
    probs = np.asarray(base.to_numpy(outputs.get("out_probs", []))).reshape(-1)
    return {
        int(obj_id): float(probs[index]) if index < len(probs) else 1.0
        for index, obj_id in enumerate(ids)
    }


def candidates_from_outputs(
    outputs, *, fallback_confidence: float = 1.0
) -> tuple[ProductCandidate, ...]:
    masks = base.masks_from_outputs(outputs)
    scores = probabilities(outputs)
    result = []
    for local_obj_id, mask in sorted(masks.items()):
        geo = base.geometry_from_mask(mask)
        if geo["bbox"] is None or geo["centroid"] is None:
            continue
        confidence = scores.get(int(local_obj_id), fallback_confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            confidence = fallback_confidence
        result.append(
            ProductCandidate(
                local_obj_id=int(local_obj_id),
                bbox=tuple(float(value) for value in geo["bbox"]),
                centroid=tuple(float(value) for value in geo["centroid"]),
                confidence=confidence,
                mask=mask.astype(bool),
            )
        )
    return tuple(result)


def add_text_prompt(predictor, session_id, prompt):
    return predictor.handle_request(
        {
            "type": "add_prompt",
            "session_id": session_id,
            "frame_index": 0,
            "text": prompt,
            "output_prob_thresh": base.OUTPUT_PROB_THRESH,
        }
    )


def add_box_prompt(predictor, session_id, bbox):
    return predictor.handle_request(
        {
            "type": "add_prompt",
            "session_id": session_id,
            "frame_index": 0,
            "bounding_boxes": pixel_xyxy_to_normalized_xywh(
                bbox,
                frame_width=base.AI_WIDTH,
                frame_height=base.AI_HEIGHT,
            ),
            "bounding_box_labels": [1],
            "output_prob_thresh": base.OUTPUT_PROB_THRESH,
        }
    )


def select_runtime_manual_bbox(frame, product_type):
    if frame.shape[:2] != (base.AI_HEIGHT, base.AI_WIDTH):
        raise RuntimeError("manual bbox frame must be the 1280x720 AI input")
    window_name = f"Stage 7C manual init: {product_type}"
    x, y, width, height = cv2.selectROI(
        window_name,
        frame,
        showCrosshair=True,
        fromCenter=False,
    )
    cv2.destroyWindow(window_name)
    if width <= 0 or height <= 0:
        return None
    bbox = (int(x), int(y), int(x + width), int(y + height))
    pixel_xyxy_to_normalized_xywh(
        bbox,
        frame_width=base.AI_WIDTH,
        frame_height=base.AI_HEIGHT,
    )
    confirm_name = f"Confirm Stage 7C manual init: {product_type}"
    preview = frame.copy()
    cv2.rectangle(
        preview,
        (bbox[0], bbox[1]),
        (bbox[2], bbox[3]),
        (0, 255, 255),
        2,
    )
    cv2.rectangle(preview, (0, 0), (base.AI_WIDTH, 48), (0, 0, 0), -1)
    cv2.putText(
        preview,
        "Enter/Space = confirm bbox    Esc = cancel",
        (18, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.namedWindow(confirm_name, cv2.WINDOW_NORMAL)
    try:
        while True:
            cv2.imshow(confirm_name, preview)
            key = cv2.waitKey(30) & 0xFF
            if key in (10, 13, 32):
                return bbox
            if key == 27:
                return None
    finally:
        cv2.destroyWindow(confirm_name)


def choose_initial_candidate(
    predictor,
    session_id,
    product_type,
    product_config,
    product_display_surface,
    overlap_min,
    manual_init_bbox=None,
    manual_bbox_provider=None,
    manual_attempt_tracker=None,
    segment_id=None,
) -> tuple[GroundingDecision, str]:
    response = add_text_prompt(predictor, session_id, product_config["text_prompt"])
    decision = grounding_decision(
        product_type=product_type,
        candidates=candidates_from_outputs(response.get("outputs", {})),
        product_display_surface=product_display_surface,
        frame_width=base.AI_WIDTH,
        frame_height=base.AI_HEIGHT,
        surface_overlap_min=overlap_min,
    )
    if decision.status == "accepted":
        return decision, "text"
    manual_available = manual_init_bbox is not None or manual_bbox_provider is not None
    if manual_available and manual_attempt_tracker is not None and segment_id is not None:
        if not manual_attempt_tracker.claim(product_type, segment_id):
            return GroundingDecision(
                product_type,
                "manual_init_unavailable",
                decision.candidate_count,
                decision.candidate,
                decision.evidence,
                "manual_init_already_attempted_for_segment",
            ), "manual_unavailable"
    if manual_init_bbox is None and manual_bbox_provider is not None:
        try:
            manual_init_bbox = manual_bbox_provider()
        except (KeyboardInterrupt, cv2.error):
            return GroundingDecision(
                product_type,
                "manual_init_cancelled",
                decision.candidate_count,
                decision.candidate,
                decision.evidence,
                "manual_init_cancelled",
            ), "manual_cancelled"
        if manual_init_bbox is None:
            return GroundingDecision(
                product_type,
                "manual_init_cancelled",
                decision.candidate_count,
                decision.candidate,
                decision.evidence,
                "manual_init_cancelled",
            ), "manual_cancelled"
    if manual_init_bbox is None:
        return decision, "text_unstable"
    try:
        response = add_box_prompt(predictor, session_id, tuple(manual_init_bbox))
    except ValueError as exc:
        return GroundingDecision(
            product_type,
            "manual_init_invalid",
            decision.candidate_count,
            decision.candidate,
            decision.evidence,
            f"manual_init_invalid:{exc}",
        ), "manual_invalid"
    manual = grounding_decision(
        product_type=product_type,
        candidates=candidates_from_outputs(response.get("outputs", {})),
        product_display_surface=product_display_surface,
        frame_width=base.AI_WIDTH,
        frame_height=base.AI_HEIGHT,
        surface_overlap_min=overlap_min,
        invalid_status="manual_init_invalid",
    )
    return manual, "manual_bbox"


def handoff_bbox(state: ProductSessionState):
    if state.old_overlap_bank:
        first_frame = state.old_overlap_bank.get(0, {})
        if first_frame:
            mask = next(iter(first_frame.values()))
            bbox = base.mask_bbox(mask)
            if bbox is not None:
                return tuple(float(value) for value in bbox)
    return state.last_bbox


def same_track_across_gap(old_bbox, candidate) -> bool:
    if old_bbox is None:
        return False
    biou = base.bbox_iou(old_bbox, candidate.bbox)
    old_centroid = (
        (old_bbox[0] + old_bbox[2]) / 2,
        (old_bbox[1] + old_bbox[3]) / 2,
    )
    distance = base.centroid_distance_norm(
        old_centroid,
        candidate.centroid,
        base.AI_WIDTH,
        base.AI_HEIGHT,
    )
    return biou >= base.ASSOC_MIN_BBOX_IOU or distance <= base.ASSOC_MAX_CENTROID_DISTANCE_NORM


def append_jsonl(payload):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("a", encoding="utf-8") as target:
        target.write(json.dumps(payload, ensure_ascii=True) + "\n")


def log_observation(observation, *, window_no, local_obj_id, method):
    payload = observation.to_dict()
    payload.update(
        {
            "window_no": window_no,
            "local_obj_id": local_obj_id,
            "track_status": ProductTrackStatus.ACTIVE.value,
            "association_method": method,
        }
    )
    append_jsonl(payload)
    print(
        f"[{observation.product_type}] id={observation.product_track_id} "
        f"token={observation.product_track_token} "
        f"state={observation.display_state.value} "
        f"centroid=({observation.centroid[0]:.3f},{observation.centroid[1]:.3f}) "
        f"surface_overlap={observation.surface_overlap_ratio:.3f}"
    )


def log_unobserved(
    item,
    product_type,
    runtime,
    window_no,
    *,
    lost,
    misses,
    reason,
    track_status=None,
):
    identity = runtime.track_identity(product_type)
    append_jsonl(
        {
            "timestamp": datetime.fromtimestamp(
                item.wall_time, tz=timezone.utc
            ).replace(tzinfo=None).isoformat(),
            "source_seq": item.source_seq,
            "window_no": window_no,
            "product_track_id": None if identity is None else identity[0],
            "product_track_token": None if identity is None else identity[1],
            "product_type": product_type,
            "bbox": None,
            "centroid": None,
            "confidence": None,
            "mask_area": None,
            "mask_overlap_ratio": None,
            "surface_overlap_ratio": None,
            "centroid_inside": None,
            "centroid_inside_product_display_surface": None,
            "display_state": "UNOBSERVED",
            "tracking_lost": lost,
            "consecutive_missed_windows": misses,
            "track_status": track_status
            or ("LOST" if lost else "MISSING_PENDING"),
            "reason": reason,
        }
    )


def process_product_window(
    *,
    predictor,
    window_items,
    window_no,
    product_type,
    product_config,
    product_display_surface,
    runtime,
    state,
    overlap_min,
    interactive_manual_bbox,
    manual_attempt_tracker,
):
    session_id = base.start_session(predictor)
    response_candidates = ()
    associated = False
    method = None
    local_obj_id = None
    first_overlap: dict[int, dict[int, np.ndarray]] = {}
    last_overlap: dict[int, dict[int, np.ndarray]] = {}
    last_overlap_start = len(window_items) - base.OVERLAP_FRAMES
    try:
        status = runtime.status(product_type)
        if status is None or status is ProductTrackStatus.LOST:
            segment_id = runtime.next_segment_id(product_type)
            manual_init_available = bool(
                state.manual_init_bbox is not None
                or product_type in interactive_manual_bbox
            )
            decision, method = choose_initial_candidate(
                predictor,
                session_id,
                product_type,
                product_config,
                product_display_surface,
                overlap_min,
                state.manual_init_bbox,
                (
                    lambda: select_runtime_manual_bbox(
                        window_items[0].frame,
                        product_type,
                    )
                    if manual_init_available and state.manual_init_bbox is None
                    else None
                ),
                manual_attempt_tracker,
                segment_id,
            )
            state.manual_init_bbox = None
            if decision.status != "accepted" or decision.candidate is None:
                append_jsonl(
                    {
                        "timestamp": datetime.fromtimestamp(
                            window_items[0].wall_time, tz=timezone.utc
                        ).replace(tzinfo=None).isoformat(),
                        "source_seq": window_items[0].source_seq,
                        "window_no": window_no,
                        "product_type": product_type,
                        "display_state": "UNOBSERVED",
                        "tracking_lost": status is ProductTrackStatus.LOST,
                        "initialization_status": decision.status,
                        "initialization_reason": decision.reason,
                        "initialization_method": method,
                    }
                )
                return
            initial = runtime.initialize(
                product_type=product_type,
                decision=decision,
                observed_at=datetime.fromtimestamp(
                    window_items[0].wall_time, tz=timezone.utc
                ),
                source_seq=window_items[0].source_seq,
            )
            local_obj_id = decision.candidate.local_obj_id
            response_candidates = (decision.candidate,)
            associated = True
            state.initialization_method = method
            state.last_confidence = decision.candidate.confidence
            log_observation(
                initial,
                window_no=window_no,
                local_obj_id=local_obj_id,
                method=f"initialize:{method}",
            )
        else:
            bbox = handoff_bbox(state)
            if bbox is None:
                miss = runtime.association_miss(product_type)
                log_unobserved(
                    window_items[0],
                    product_type,
                    runtime,
                    window_no,
                    lost=bool(miss and miss["tracking_lost"]),
                    misses=0 if miss is None else miss["consecutive_missed_windows"],
                    reason="missing_handoff_bbox",
                )
                return
            response = add_box_prompt(predictor, session_id, bbox)
            response_candidates = candidates_from_outputs(
                response.get("outputs", {}),
                fallback_confidence=state.last_confidence,
            )
            if len(response_candidates) == 1:
                local_obj_id = response_candidates[0].local_obj_id

        if local_obj_id is not None and response_candidates:
            first_overlap[0] = {
                local_obj_id: response_candidates[0].mask.copy()
            }

        buffered_fresh = []
        propagation_start_frame_index = (
            0 if window_no == 0 else base.OVERLAP_FRAMES
        )
        for response in base.propagate_forward(
            predictor,
            session_id,
            start_frame_index=propagation_start_frame_index,
        ):
            local_idx = response.get("frame_index")
            if local_idx is None:
                continue
            local_idx = int(local_idx)
            if not 0 <= local_idx < len(window_items):
                continue
            frame_candidates = candidates_from_outputs(
                response.get("outputs", {}),
                fallback_confidence=state.last_confidence,
            )
            selected = None
            if local_obj_id is not None:
                selected = next(
                    (item for item in frame_candidates if item.local_obj_id == local_obj_id),
                    None,
                )
            if local_idx < base.OVERLAP_FRAMES and selected is not None:
                first_overlap[local_idx] = {local_obj_id: selected.mask.copy()}
            if local_idx >= last_overlap_start and selected is not None:
                last_overlap[local_idx - last_overlap_start] = {
                    local_obj_id: selected.mask.copy()
                }
            if window_no == 0 or local_idx >= base.OVERLAP_FRAMES:
                buffered_fresh.append((local_idx, selected))

        if status is not None and status is not ProductTrackStatus.LOST:
            if state.old_overlap_bank and local_obj_id is not None:
                association = base.associate_global_tracks(
                    state.old_overlap_bank,
                    first_overlap,
                )
                associated = bool(association["matches"])
                method = "overlap"
            elif len(response_candidates) == 1:
                associated = same_track_across_gap(
                    state.last_bbox, response_candidates[0]
                )
                method = "gap_geometry"

        if not associated:
            miss = runtime.association_miss(product_type)
            for local_idx, _candidate in buffered_fresh[:1] or [(0, None)]:
                log_unobserved(
                    window_items[local_idx],
                    product_type,
                    runtime,
                    window_no,
                    lost=bool(miss and miss["tracking_lost"]),
                    misses=0 if miss is None else miss["consecutive_missed_windows"],
                    reason="association_miss",
                )
            state.old_overlap_bank = None
            return

        for local_idx, candidate in buffered_fresh:
            item = window_items[local_idx]
            if candidate is None:
                log_unobserved(
                    item,
                    product_type,
                    runtime,
                    window_no,
                    lost=False,
                    misses=0,
                    reason="frame_mask_missing",
                    track_status="ACTIVE",
                )
                continue
            observation = runtime.observe(
                product_type=product_type,
                observed_at=datetime.fromtimestamp(item.wall_time, tz=timezone.utc),
                source_seq=item.source_seq,
                candidate=candidate,
            )
            state.last_bbox = candidate.bbox
            log_observation(
                observation,
                window_no=window_no,
                local_obj_id=candidate.local_obj_id,
                method=method or state.initialization_method,
            )

        next_bank = {}
        for offset in range(base.OVERLAP_FRAMES):
            local = last_overlap.get(offset)
            if local and local_obj_id in local:
                identity = runtime.track_identity(product_type)
                next_bank[offset] = {identity[0]: local[local_obj_id].copy()}
        state.old_overlap_bank = next_bank or None
        state.local_obj_id = local_obj_id
    finally:
        base.close_session(predictor, session_id)


def main():
    args = parse_args()
    config, product_display_surface = load_config()
    manual_bboxes = parse_manual_bboxes(args.manual_bbox)
    interactive_manual_bbox = set(args.interactive_manual_bbox or ())
    OUTPUT_PATH.unlink(missing_ok=True)
    base.validate_config()
    base.configure_torch()
    sampler = base.AsyncAISampler().start()
    predictor = None
    runtime = ProductTrackRuntime(
        camera_id=config["camera_id"],
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        product_display_surface=product_display_surface,
        frame_width=base.AI_WIDTH,
        frame_height=base.AI_HEIGHT,
        surface_overlap_min=float(config["surface_overlap_min"]),
        product_track_lost_after_windows=int(
            config["product_track_lost_after_windows"]
        ),
    )
    states = {
        product_type: ProductSessionState(
            product_type,
            manual_init_bbox=manual_bboxes.get(product_type),
        )
        for product_type in config["products"]
    }
    manual_attempt_tracker = ManualInitializationAttemptTracker()
    previous_overlap_items = []
    try:
        with torch.inference_mode(), base.autocast_context():
            predictor = base.build_predictor()
            base.tune_runtime(predictor)
            end_time = time.perf_counter() + RUN_MINUTES * 60
            window_no = 0
            print(f"Stage 7C runtime={RUNTIME_INSTANCE_ID} log={OUTPUT_PATH}")
            while time.perf_counter() < end_time:
                build = base.build_next_window(sampler, previous_overlap_items)
                window_items = build["window_items"]
                previous_overlap_items = [
                    base.SampledFrame(
                        sample_id=item.sample_id,
                        source_seq=item.source_seq,
                        wall_time=item.wall_time,
                        frame=item.frame.copy(),
                    )
                    for item in window_items[-base.OVERLAP_FRAMES :]
                ]
                base.materialize_window(window_items)
                for product_type, product_config in config["products"].items():
                    process_product_window(
                        predictor=predictor,
                        window_items=window_items,
                        window_no=window_no,
                        product_type=product_type,
                        product_config=product_config,
                        product_display_surface=product_display_surface,
                        runtime=runtime,
                        state=states[product_type],
                        overlap_min=float(config["grounding_surface_overlap_min"]),
                        interactive_manual_bbox=interactive_manual_bbox,
                        manual_attempt_tracker=manual_attempt_tracker,
                    )
                window_no += 1
    finally:
        sampler.stop()
        del predictor
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    print(f"Stage 7C complete: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
