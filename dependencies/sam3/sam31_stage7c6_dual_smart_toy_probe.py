"""Stage 7C.6.2 occlusion-safe dual smart-toy acceptance probe."""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import threading
import time
import winsound
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from rv_handoff_paths import CODE_ROOT, AGENTS_PYTHON, RUNTIME_OUTPUT, project_config
from typing import Optional

import cv2
import numpy as np
import torch

import sam31_stage5_1_1_demo_latency_finalization as base
import sam31_stage7c_product_tracking as stage7c


RETAILVISION_ROOT = CODE_ROOT
if str(RETAILVISION_ROOT) not in sys.path:
    sys.path.insert(0, str(RETAILVISION_ROOT))

from rv_handoff_product_tracking import (  # noqa: E402
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplayState,
    ProductTrackStatus,
    ProductWindowResult,
    diagnose_candidate_edges,
    initialization_candidates,
)
from rv_handoff_product_replay import (  # noqa: E402
    CDETimeline,
    OfflineVideoWindowSource,
    SingleTextGroundingPerWindow,
    build_cde_summary,
    offline_observed_at,
    render_cde_report,
    validate_probe_source,
)


PRODUCT_KEYS = ("smart_toy_a", "smart_toy_b")
PRODUCT_TYPE = "smart_toy"
TEXT_PROMPT = "plush toy"
OUTPUT_ROOT = RUNTIME_OUTPUT / "stage7c6_2_occlusion_safe_recovery"
TOTAL_SECONDS = 130.0
VIDEO_FPS = base.TARGET_AI_FPS
COLORS = {"smart_toy_a": (0, 220, 255), "smart_toy_b": (255, 190, 0)}

TIMELINE = (
    (0.0, "Test A: keep both toys stationary"),
    (30.0, "Test B: move Toy A to position 1"),
    (40.0, "Test B: move Toy A to position 2"),
    (50.0, "Test B: move Toy A to position 3"),
    (60.0, "Test C: move Toy B to position 1"),
    (70.0, "Test C: move Toy B to position 2"),
    (80.0, "Test C: move Toy B to position 3"),
    (90.0, "Test D: prepare Toy A"),
    (95.0, "Test D: lift and hold Toy A for 5 seconds"),
    (100.0, "Test D: return Toy A to a different Surface position"),
    (110.0, "Test E: prepare Toy B"),
    (115.0, "Test E: lift and hold Toy B for 5 seconds"),
    (120.0, "Test E: return Toy B to a different Surface position"),
    (130.0, "Stage 7C.6 capture complete"),
)


@dataclass
class ProbeState:
    initial_tokens: dict[str, str] = field(default_factory=dict)
    overlap_references: dict[str, ProductCandidate] = field(default_factory=dict)
    window_associations: list[dict[str, object]] = field(default_factory=list)


class LiveProbeClock:
    offline_replay = False
    realtime_fresh_lag_applicable = True

    def __init__(self, started_wall: float) -> None:
        self.started_wall = float(started_wall)

    def elapsed(self, item) -> float:
        return max(0.0, float(item.wall_time) - self.started_wall)

    def observed_at(self, item) -> datetime:
        return datetime.fromtimestamp(item.wall_time, tz=timezone.utc)

    def fresh_lag(self, item, finished_wall: float) -> float:
        return max(0.0, finished_wall - float(item.wall_time))

    def position(self, item) -> tuple[str, str]:
        return phase_for_elapsed(self.elapsed(item))

    def video_elapsed_seconds(self, _item) -> None:
        return None


class OfflineProbeClock:
    offline_replay = True
    realtime_fresh_lag_applicable = False

    def __init__(self, timeline: CDETimeline) -> None:
        self.timeline = timeline

    def elapsed(self, item) -> float:
        return float(item.video_elapsed_seconds)

    def observed_at(self, item) -> datetime:
        return offline_observed_at(self.elapsed(item))

    def fresh_lag(self, _item, _finished_wall: float) -> None:
        return None

    def position(self, item) -> tuple[str, str]:
        position = self.timeline.classify(self.elapsed(item))
        return position.test_case, position.phase

    def video_elapsed_seconds(self, item) -> float:
        return self.elapsed(item)


class LiveWindowSource:
    def __init__(self, sampler, start_after_sample_id: int) -> None:
        self.sampler = sampler
        self.start_after_sample_id = start_after_sample_id

    def windows(self):
        window_items, _skipped, _available = self.sampler.wait_latest_new_frames(
            after_sample_id=self.start_after_sample_id,
            count=base.WINDOW_FRAMES,
        )
        while True:
            yield tuple(window_items)
            previous_overlap = [
                base.SampledFrame(
                    item.sample_id,
                    item.source_seq,
                    item.wall_time,
                    item.frame.copy(),
                )
                for item in window_items[-base.OVERLAP_FRAMES :]
            ]
            build = base.build_next_window(self.sampler, previous_overlap)
            window_items = build["window_items"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("live", "video"), default="live")
    parser.add_argument("--video", type=Path)
    parser.add_argument("--test-c-start", type=float)
    parser.add_argument("--test-c-end", type=float)
    parser.add_argument("--test-d-start", type=float)
    parser.add_argument("--test-d-end", type=float)
    parser.add_argument("--test-e-start", type=float)
    parser.add_argument("--test-e-end", type=float)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    timeline_values = (
        args.test_c_start,
        args.test_c_end,
        args.test_d_start,
        args.test_d_end,
        args.test_e_start,
        args.test_e_end,
    )
    if args.source == "video":
        if args.video is None or any(value is None for value in timeline_values):
            parser.error("video mode requires --video and all C/D/E start/end values")
        args.timeline = CDETimeline(*timeline_values)
    else:
        if args.video is not None or any(value is not None for value in timeline_values):
            parser.error("video and C/D/E timeline arguments require --source video")
        args.timeline = None
    validate_probe_source(
        source=args.source,
        video_path=args.video,
        timeline=args.timeline,
    )
    return args


class ProbeArtifacts:
    def __init__(self, run_dir: Path, surface) -> None:
        self.run_dir = run_dir
        self.surface = surface
        self.jsonl_path = run_dir / "observations.jsonl"
        self.summary_path = run_dir / "summary.json"
        self.video_path = run_dir / "mask_overlay.mp4"
        self.keyframes_dir = run_dir / "keyframes"
        self.keyframes_dir.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict[str, object]] = []
        self.window_metrics: list[dict[str, object]] = []
        self._last_keyframe_state: Optional[tuple[object, ...]] = None
        self._video = cv2.VideoWriter(
            str(self.video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            VIDEO_FPS,
            (base.AI_WIDTH, base.AI_HEIGHT),
        )
        if not self._video.isOpened():
            raise RuntimeError(f"cannot create overlay video: {self.video_path}")

    def append_frame(
        self,
        *,
        rows: list[dict[str, object]],
        frame: np.ndarray,
        candidates: dict[str, ProductCandidate],
    ) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as target:
            for row in rows:
                target.write(json.dumps(row, ensure_ascii=True) + "\n")
                self.rows.append(row)
        rendered = render_overlay(frame, self.surface, candidates, rows)
        self._video.write(rendered)
        state = tuple(
            (
                row["product_key"], row["track_status"], row["display_state"],
                row["test_case"], row["phase"], row["association_success"],
                row["association_ambiguous"], row.get("association_role"),
                row.get("recovery_rejected_reason"),
            )
            for row in rows
        )
        if state != self._last_keyframe_state:
            first = rows[0]
            name = f"{int(first['source_seq']):08d}_{first['test_case']}_{first['phase']}.jpg"
            cv2.imwrite(str(self.keyframes_dir / name), rendered)
            self._last_keyframe_state = state

    def close(self) -> None:
        self._video.release()


def phase_for_elapsed(elapsed: float) -> tuple[str, str]:
    if elapsed < 30:
        return "A", "stationary"
    if elapsed < 40:
        return "B", "toy_a_position_1"
    if elapsed < 50:
        return "B", "toy_a_position_2"
    if elapsed < 60:
        return "B", "toy_a_position_3"
    if elapsed < 70:
        return "C", "toy_b_position_1"
    if elapsed < 80:
        return "C", "toy_b_position_2"
    if elapsed < 90:
        return "C", "toy_b_position_3"
    if elapsed < 95:
        return "D", "toy_a_surface"
    if elapsed < 100:
        return "D", "toy_a_handheld"
    if elapsed < 110:
        return "D", "toy_a_returned"
    if elapsed < 115:
        return "E", "toy_b_surface"
    if elapsed < 120:
        return "E", "toy_b_handheld"
    return "E", "toy_b_returned"


def surface_polygon(surface) -> np.ndarray:
    return np.asarray(
        [[int(round(x * base.AI_WIDTH)), int(round(y * base.AI_HEIGHT))] for x, y in surface.points],
        dtype=np.int32,
    )


def draw_surface(frame: np.ndarray, surface) -> np.ndarray:
    rendered = frame.copy()
    polygon = surface_polygon(surface)
    fill = rendered.copy()
    cv2.fillPoly(fill, [polygon], (255, 0, 255))
    rendered = cv2.addWeighted(fill, 0.10, rendered, 0.90, 0)
    cv2.polylines(rendered, [polygon], True, (255, 0, 255), 3, cv2.LINE_AA)
    return rendered


def render_overlay(frame, surface, candidates, rows) -> np.ndarray:
    rendered = draw_surface(frame, surface)
    by_key = {str(row["product_key"]): row for row in rows}
    for product_key, candidate in candidates.items():
        color = COLORS[product_key]
        mask = candidate.mask.astype(bool)
        rendered[mask] = (
            rendered[mask].astype(np.float32) * 0.52
            + np.asarray(color, dtype=np.float32) * 0.48
        ).astype(np.uint8)
        x1, y1, x2, y2 = (int(value) for value in candidate.bbox)
        cv2.rectangle(rendered, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            rendered,
            "A" if product_key.endswith("_a") else "B",
            (x1, max(24, y1 - 7)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            color,
            2,
            cv2.LINE_AA,
        )
    first = rows[0]
    lines = [
        f"Stage 7C.6.2 Test {first['test_case']} / {first['phase']}  window={first['window_no']}",
    ]
    for key in PRODUCT_KEYS:
        row = by_key[key]
        short = "A" if key.endswith("_a") else "B"
        token = row.get("product_track_token")
        segment = "-" if not token else str(token).rsplit(":", 1)[-1]
        lines.extend((
            f"{key} / Toy {short}: track={row['product_track_id']} segment={segment}",
            f"  {row['track_status']} {row['display_state']}  "
            f"assoc={row['association_method']}:{row['association_success']}  "
            f"ambiguous={row['association_ambiguous']}",
            f"  role={row.get('association_role', 'normal')} "
            f"locked={row.get('anchor_locked_candidate_ids', [])} "
            f"recovery={row.get('recovery_attempted', False)}:"
            f"{row.get('recovery_success', False)}",
        ))
    cv2.rectangle(rendered, (0, 0), (base.AI_WIDTH, 212), (0, 0, 0), -1)
    for index, line in enumerate(lines):
        cv2.putText(
            rendered, line, (15, 25 + index * 29), cv2.FONT_HERSHEY_SIMPLEX,
            0.54, (255, 255, 255), 2, cv2.LINE_AA,
        )
    return rendered


def select_initial_assignments(frame, surface, candidates):
    credible = initialization_candidates(
        candidates=candidates,
        product_display_surface=surface,
        frame_width=base.AI_WIDTH,
        frame_height=base.AI_HEIGHT,
        surface_overlap_min=0.50,
    )
    if len(credible) < 2:
        raise RuntimeError(f"initialization needs at least two credible candidates, got {len(credible)}")
    selection: list[int] = []
    window_name = "Stage 7C.6.2 identity confirmation"

    def on_mouse(event, x, y, _flags, _parameter):
        if event != cv2.EVENT_LBUTTONDOWN or len(selection) >= 2:
            return
        hits = [
            index for index, item in enumerate(credible)
            if bool(item.mask[min(max(y, 0), base.AI_HEIGHT - 1), min(max(x, 0), base.AI_WIDTH - 1)])
        ]
        if len(hits) == 1 and hits[0] not in selection:
            selection.append(hits[0])

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, on_mouse)
    try:
        while True:
            rendered = draw_surface(frame, surface)
            for index, item in enumerate(credible):
                color = (0, 255, 0) if index in selection else (255, 255, 255)
                mask = item.mask.astype(bool)
                rendered[mask] = (
                    rendered[mask].astype(np.float32) * 0.60
                    + np.asarray(color, dtype=np.float32) * 0.40
                ).astype(np.uint8)
                x1, y1, x2, y2 = (int(value) for value in item.bbox)
                cv2.rectangle(rendered, (x1, y1), (x2, y2), color, 2)
                cv2.putText(rendered, str(index + 1), (x1, max(28, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
            instruction = (
                "Click the WHITE/BROWN Toy A mask"
                if not selection else
                "Click the GRAY Toy B mask" if len(selection) == 1 else
                "Enter = confirm A/B identity    R = reset    Esc = cancel"
            )
            cv2.rectangle(rendered, (0, 0), (base.AI_WIDTH, 48), (0, 0, 0), -1)
            cv2.putText(rendered, instruction, (18, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow(window_name, rendered)
            key = cv2.waitKey(30) & 0xFF
            if key in (10, 13) and len(selection) == 2:
                return {PRODUCT_KEYS[index]: credible[candidate_index] for index, candidate_index in enumerate(selection)}
            if key in (ord("r"), ord("R")):
                selection.clear()
            if key == 27:
                raise KeyboardInterrupt("Stage 7C.6 identity confirmation cancelled")
    finally:
        cv2.destroyWindow(window_name)


def wait_for_operator_start(sampler, surface) -> int:
    name = "Stage 7C.6 - Enter=start, Esc=cancel"
    cv2.namedWindow(name, cv2.WINDOW_NORMAL)
    try:
        while True:
            items = sampler.wait_first_window()
            rendered = draw_surface(items[-1].frame, surface)
            cv2.rectangle(rendered, (0, 0), (base.AI_WIDTH, 76), (0, 0, 0), -1)
            cv2.putText(rendered, "Place both toys inside the Surface and keep them still", (18, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(rendered, "Enter = start 130-second A-E probe    Esc = cancel", (18, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow(name, rendered)
            key = cv2.waitKey(100) & 0xFF
            if key in (10, 13):
                return items[-1].sample_id
            if key == 27:
                raise KeyboardInterrupt("Stage 7C.6 cancelled")
    finally:
        cv2.destroyWindow(name)


def start_timeline_announcer(start_wall: float, stop_event: threading.Event):
    def announce() -> None:
        name = "Stage 7C.6 Operator Timeline"
        next_index = 0
        cv2.namedWindow(name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(name, 1100, 250)
        if hasattr(cv2, "WND_PROP_TOPMOST"):
            cv2.setWindowProperty(name, cv2.WND_PROP_TOPMOST, 1)
        try:
            while not stop_event.is_set():
                elapsed = max(0.0, time.time() - start_wall)
                while next_index < len(TIMELINE) and elapsed >= TIMELINE[next_index][0]:
                    offset, message = TIMELINE[next_index]
                    print(f"STAGE7C6_EVENT t+{offset:.0f}s {message}", flush=True)
                    winsound.Beep(1100, 250)
                    next_index += 1
                current = TIMELINE[max(0, next_index - 1)]
                upcoming = TIMELINE[next_index] if next_index < len(TIMELINE) else None
                board = np.zeros((250, 1100, 3), dtype=np.uint8)
                cv2.putText(board, f"LIVE t+{elapsed:05.1f}s / {TOTAL_SECONDS:.0f}s", (24, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.05, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(board, f"NOW: {current[1]}", (24, 104), cv2.FONT_HERSHEY_SIMPLEX, 0.78, (255, 255, 255), 2, cv2.LINE_AA)
                if upcoming is not None:
                    seconds = max(0.0, upcoming[0] - elapsed)
                    cv2.putText(board, f"NEXT in {seconds:04.1f}s: {upcoming[1]}", (24, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.69, (0, 190, 255) if seconds <= 5 else (180, 180, 180), 2, cv2.LINE_AA)
                cv2.putText(board, "Follow this window; each transition beeps.", (24, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (140, 255, 140), 2, cv2.LINE_AA)
                cv2.imshow(name, board)
                cv2.waitKey(100)
        finally:
            cv2.destroyWindow(name)

    thread = threading.Thread(target=announce, daemon=True, name="stage7c6-timeline")
    thread.start()
    return thread


def missing_payload(*, baseline, item, window_no, clock, candidate_count, grounding_count, processing_seconds, finished_wall, association_edge_diagnostics=()):
    test_case, phase = clock.position(item)
    payload = baseline.to_dict()
    payload.update({
        "timestamp": clock.observed_at(item).isoformat(),
        "video_elapsed_seconds": clock.video_elapsed_seconds(item),
        "window_no": window_no,
        "source_seq": item.source_seq,
        "candidate_count": candidate_count,
        "grounding_candidate_count": grounding_count,
        "local_obj_id": None,
        "bbox": None,
        "centroid": None,
        "centroid_pixel": None,
        "mask_area": None,
        "surface_overlap_ratio": None,
        "display_state": ProductDisplayState.UNOBSERVED.value,
        "mask_present": False,
        "test_case": test_case,
        "phase": phase,
        "processing_seconds": processing_seconds,
        "fresh_lag": clock.fresh_lag(item, finished_wall),
        "offline_replay": clock.offline_replay,
        "realtime_fresh_lag_applicable": clock.realtime_fresh_lag_applicable,
        "bbox_handoff_used": False,
        "association_edge_diagnostics": list(association_edge_diagnostics),
    })
    return payload


def observed_payload(result, candidate, *, item, clock, processing_seconds, finished_wall):
    payload = result.to_dict()
    test_case, phase = clock.position(item)
    payload.update({
        "timestamp": clock.observed_at(item).isoformat(),
        "video_elapsed_seconds": clock.video_elapsed_seconds(item),
        "centroid_pixel": list(candidate.centroid),
        "mask_present": True,
        "test_case": test_case,
        "phase": phase,
        "processing_seconds": processing_seconds,
        "fresh_lag": clock.fresh_lag(item, finished_wall),
        "offline_replay": clock.offline_replay,
        "realtime_fresh_lag_applicable": clock.realtime_fresh_lag_applicable,
        "bbox_handoff_used": False,
    })
    return payload


def process_window(*, predictor, window_items, window_no, clock, grounding_guard, config, surface, runtime, state, artifacts):
    started = time.perf_counter()
    session_id = base.start_session(predictor)
    grounding_candidates: tuple[ProductCandidate, ...] = ()
    local_ids: dict[str, int] = {}
    baseline: dict[str, ProductWindowResult] = {}
    edge_diagnostics_by_key: dict[str, tuple[dict[str, object], ...]] = {}
    frame_results: dict[int, tuple[ProductCandidate, ...]] = {}
    overlap_candidates: dict[str, list[tuple[int, ProductCandidate]]] = {key: [] for key in PRODUCT_KEYS}
    try:
        response = grounding_guard.run(
            window_no,
            lambda: stage7c.add_text_prompt(predictor, session_id, TEXT_PROMPT),
        )
        grounding_candidates = stage7c.candidates_from_outputs((response or {}).get("outputs", {}))
        frame_results[0] = grounding_candidates
        initialized = all(runtime.status(key) is not None for key in PRODUCT_KEYS)
        if not initialized:
            assignments = select_initial_assignments(window_items[0].frame, surface, grounding_candidates)
            results = runtime.initialize_assignments(
                assignments=assignments,
                observed_at=clock.observed_at(window_items[0]),
                source_seq=window_items[0].source_seq,
                window_no=window_no,
                grounding_candidate_count=len(grounding_candidates),
            )
            for result in results:
                baseline[result.product_key] = result
                local_ids[result.product_key] = int(result.local_obj_id)
                state.initial_tokens[result.product_key] = str(result.product_track_token)
                state.overlap_references[result.product_key] = assignments[result.product_key]
        else:
            active_keys = [key for key in PRODUCT_KEYS if runtime.status(key) is not ProductTrackStatus.LOST]
            references = {key: state.overlap_references[key] for key in active_keys}
            results = runtime.process_window(
                candidates=grounding_candidates,
                observed_at=clock.observed_at(window_items[0]),
                source_seq=window_items[0].source_seq,
                window_no=window_no,
                grounding_candidate_count=len(grounding_candidates),
                association_references=references,
            )
            all_edge_diagnostics = diagnose_candidate_edges(
                previous_candidates=references,
                candidates=grounding_candidates,
                frame_width=base.AI_WIDTH,
                frame_height=base.AI_HEIGHT,
                overlap_iou_min=float(config["association_overlap_iou_min"]),
                geometry_distance_max=float(
                    config["association_geometry_distance_max"]
                ),
            )
            edge_diagnostics_by_key = {
                key: tuple(
                    item
                    for item in all_edge_diagnostics
                    if item["product_key"] == key
                )
                for key in references
            }
            for result in results:
                baseline[result.product_key] = result
                if result.association_success and result.local_obj_id is not None:
                    local_ids[result.product_key] = int(result.local_obj_id)
                association_item = window_items[
                    0 if window_no == 0 else base.OVERLAP_FRAMES
                ]
                test_case, phase = clock.position(association_item)
                state.window_associations.append({
                    "window_no": window_no,
                    "test_case": test_case,
                    "phase": phase,
                    "product_key": result.product_key,
                    "association_eligible": True,
                    "association_success": result.association_success,
                    "association_ambiguous": result.association_ambiguous,
                    "association_method": result.association_method,
                    "track_status": None if result.track_status is None else result.track_status.value,
                    "tracking_lost": result.tracking_lost,
                    "consecutive_missed_windows": result.consecutive_missed_windows,
                    "recovered_from_missing_pending": (
                        result.recovered_from_missing_pending
                    ),
                    "recovered_after_missed_windows": (
                        result.recovered_after_missed_windows
                    ),
                    "recovery_latency_windows": result.recovery_latency_windows,
                    "association_role": result.association_role,
                    "anchor_locked_candidate_ids": list(
                        result.anchor_locked_candidate_ids
                    ),
                    "recovery_attempted": result.recovery_attempted,
                    "recovery_candidate_count": result.recovery_candidate_count,
                    "recovery_success": result.recovery_success,
                    "recovery_rejected_reason": result.recovery_rejected_reason,
                    "association_mask_iou": result.association_mask_iou,
                    "association_centroid_distance_ratio": (
                        result.association_centroid_distance_ratio
                    ),
                    "grounding_candidate_count": len(grounding_candidates),
                    "local_obj_id": result.local_obj_id,
                    "association_edge_diagnostics": list(
                        edge_diagnostics_by_key.get(result.product_key, ())
                    ),
                })
        propagation_start = 0 if window_no == 0 else base.OVERLAP_FRAMES
        last_overlap_start = len(window_items) - base.OVERLAP_FRAMES
        for response in base.propagate_forward(predictor, session_id, start_frame_index=propagation_start):
            local_idx = response.get("frame_index")
            if local_idx is None or not 0 <= int(local_idx) < len(window_items):
                continue
            local_idx = int(local_idx)
            candidates = stage7c.candidates_from_outputs((response or {}).get("outputs", {}))
            frame_results[local_idx] = candidates
            by_id = {item.local_obj_id: item for item in candidates}
            if local_idx >= last_overlap_start:
                offset = local_idx - last_overlap_start
                for key, local_id in local_ids.items():
                    if local_id in by_id:
                        overlap_candidates[key].append((offset, by_id[local_id]))
    finally:
        base.close_session(predictor, session_id)

    torch.cuda.synchronize()
    processing_seconds = time.perf_counter() - started
    finished_wall = time.time()
    fresh_start = 0 if window_no == 0 else base.OVERLAP_FRAMES
    mask_counts = {key: 0 for key in PRODUCT_KEYS}
    for local_idx in range(fresh_start, len(window_items)):
        item = window_items[local_idx]
        candidates = frame_results.get(local_idx, ())
        by_id = {candidate.local_obj_id: candidate for candidate in candidates}
        selected = {key: by_id[local_id] for key, local_id in local_ids.items() if local_id in by_id}
        observed_results = runtime.observe_assignments(
            assignments=selected,
            observed_at=clock.observed_at(item),
            source_seq=item.source_seq,
            window_no=window_no,
            grounding_candidate_count=len(grounding_candidates),
            candidate_count=len(candidates),
            association_method="operator_confirmation" if window_no == 0 else "window_assignment",
            processing_seconds=processing_seconds,
            fresh_lag=clock.fresh_lag(item, finished_wall),
        ) if selected else ()
        observed_by_key = {result.product_key: result for result in observed_results}
        rows = []
        for key in PRODUCT_KEYS:
            if key in observed_by_key:
                mask_counts[key] += 1
                payload = observed_payload(
                    observed_by_key[key], selected[key], item=item,
                    clock=clock,
                    processing_seconds=processing_seconds,
                    finished_wall=finished_wall,
                )
                if key in baseline:
                    payload["association_method"] = baseline[key].association_method
                    payload["association_success"] = baseline[key].association_success
                    payload["association_ambiguous"] = baseline[key].association_ambiguous
                    payload["recovered_from_missing_pending"] = (
                        baseline[key].recovered_from_missing_pending
                    )
                    payload["recovered_after_missed_windows"] = (
                        baseline[key].recovered_after_missed_windows
                    )
                    payload["recovery_latency_windows"] = (
                        baseline[key].recovery_latency_windows
                    )
                    payload["association_role"] = baseline[key].association_role
                    payload["anchor_locked_candidate_ids"] = list(
                        baseline[key].anchor_locked_candidate_ids
                    )
                    payload["recovery_attempted"] = (
                        baseline[key].recovery_attempted
                    )
                    payload["recovery_candidate_count"] = (
                        baseline[key].recovery_candidate_count
                    )
                    payload["recovery_success"] = baseline[key].recovery_success
                    payload["recovery_rejected_reason"] = (
                        baseline[key].recovery_rejected_reason
                    )
                    payload["association_mask_iou"] = (
                        baseline[key].association_mask_iou
                    )
                    payload["association_centroid_distance_ratio"] = (
                        baseline[key].association_centroid_distance_ratio
                    )
                    payload["association_edge_diagnostics"] = list(
                        edge_diagnostics_by_key.get(key, ())
                    )
                rows.append(payload)
            else:
                base_result = baseline.get(key)
                if base_result is None:
                    identity = runtime.track_identity(key)
                    status = runtime.status(key)
                    base_result = ProductWindowResult(
                        product_key=key, product_type=PRODUCT_TYPE,
                        product_track_id=None if identity is None else identity[0],
                        product_track_token=None if identity is None else identity[1],
                        local_obj_id=None, grounding_candidate_count=len(grounding_candidates),
                        candidate_count=len(candidates), bbox=None, centroid=None, mask_area=None,
                        surface_overlap_ratio=None, display_state=ProductDisplayState.UNOBSERVED,
                        association_method="none", association_success=False,
                        association_ambiguous=False, track_status=status,
                        consecutive_missed_windows=0, tracking_lost=status is ProductTrackStatus.LOST,
                        observed_at=clock.observed_at(item),
                        source_seq=item.source_seq, window_no=window_no,
                    )
                rows.append(missing_payload(
                    baseline=base_result, item=item, window_no=window_no,
                    clock=clock, candidate_count=len(candidates),
                    grounding_count=len(grounding_candidates), processing_seconds=processing_seconds,
                    finished_wall=finished_wall,
                    association_edge_diagnostics=edge_diagnostics_by_key.get(key, ()),
                ))
        artifacts.append_frame(rows=rows, frame=item.frame, candidates=selected)

    for key in PRODUCT_KEYS:
        if overlap_candidates[key]:
            state.overlap_references[key] = min(overlap_candidates[key], key=lambda item: item[0])[1]
    artifacts.window_metrics.append({
        "window_no": window_no,
        "processing_seconds": processing_seconds,
        "grounding_candidate_count": len(grounding_candidates),
        "fresh_frame_count": len(window_items) - fresh_start,
        "fresh_mask_count": mask_counts,
    })
    association_text = ",".join(
        f"{key[-1].upper()}={baseline[key].association_method}:{baseline[key].association_success}"
        for key in PRODUCT_KEYS if key in baseline
    )
    print(
        f"STAGE7C6_WINDOW window={window_no} candidates={len(grounding_candidates)} "
        f"association={association_text} "
        f"roles={','.join(f'{key[-1].upper()}:{baseline[key].association_role}' for key in PRODUCT_KEYS if key in baseline)} "
        f"locked={sorted({candidate_id for result in baseline.values() for candidate_id in result.anchor_locked_candidate_ids})} "
        f"masks=A:{mask_counts[PRODUCT_KEYS[0]]}/"
        f"{len(window_items)-fresh_start},B:{mask_counts[PRODUCT_KEYS[1]]}/"
        f"{len(window_items)-fresh_start} seconds={processing_seconds:.3f}",
        flush=True,
    )


def percentile(values, percent):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), percent))


def distribution(values):
    return {
        "mean": statistics.fmean(values) if values else None,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


def centroid_motion(rows):
    points = [row["centroid_pixel"] for row in rows if row.get("centroid_pixel")]
    if not points:
        return {"x_span": 0.0, "y_span": 0.0, "max_axis_span": 0.0}
    x_values = [float(point[0]) for point in points]
    y_values = [float(point[1]) for point in points]
    x_span = max(x_values) - min(x_values)
    y_span = max(y_values) - min(y_values)
    return {"x_span": x_span, "y_span": y_span, "max_axis_span": max(x_span, y_span)}


def summarize(artifacts, state):
    summary = {
        "stage": "7C.6.2",
        "product_type": PRODUCT_TYPE,
        "product_keys": list(PRODUCT_KEYS),
        "text_prompt": TEXT_PROMPT,
        "text_groundings_per_window": 1,
        "bbox_handoff_used": False,
        "window_frames": base.WINDOW_FRAMES,
        "overlap_frames": base.OVERLAP_FRAMES,
        "products": {},
        "tests": {},
    }
    for key in PRODUCT_KEYS:
        rows = [row for row in artifacts.rows if row["product_key"] == key]
        observed = [row for row in rows if row["mask_present"]]
        associations = [item for item in state.window_associations if item["product_key"] == key]
        initial_token = state.initial_tokens.get(key)
        stable = [row for row in observed if row["product_track_token"] == initial_token]
        lost_windows = {item["window_no"] for item in associations if item["track_status"] == "LOST"}
        summary["products"][key] = {
            "initial_product_track_token": initial_token,
            "fresh_frame_count": len(rows),
            "fresh_mask_count": len(observed),
            "fresh_mask_coverage": len(observed) / len(rows) if rows else 0.0,
            "product_track_continuity_rate": len(stable) / len(rows) if rows else 0.0,
            "association_eligible_windows": len(associations),
            "association_success_windows": sum(bool(item["association_success"]) for item in associations),
            "association_success_rate": (
                sum(bool(item["association_success"]) for item in associations) / len(associations)
                if associations else 0.0
            ),
            "token_change_count": sum(
                row["product_track_token"] not in (None, initial_token) for row in observed
            ),
            "id_swap_count": None,
            "id_swap_verification": "pending_visual_review",
            "ambiguous_association_count": sum(bool(item["association_ambiguous"]) for item in associations),
            "lost_count": len(lost_windows),
            "product_track_tokens": sorted({str(row["product_track_token"]) for row in rows if row["product_track_token"]}),
        }
    for test_case in ("A", "B", "C", "D", "E"):
        summary["tests"][test_case] = {}
        for key in PRODUCT_KEYS:
            rows = [row for row in artifacts.rows if row["test_case"] == test_case and row["product_key"] == key]
            observed = [row for row in rows if row["mask_present"]]
            initial_token = state.initial_tokens.get(key)
            summary["tests"][test_case][key] = {
                "fresh_frame_count": len(rows),
                "fresh_mask_count": len(observed),
                "fresh_mask_coverage": len(observed) / len(rows) if rows else 0.0,
                "product_track_continuity_rate": (
                    sum(row["product_track_token"] == initial_token for row in observed) / len(rows)
                    if rows else 0.0
                ),
                "token_change_count": sum(row["product_track_token"] not in (None, initial_token) for row in observed),
                "id_swap_count": None,
                "id_swap_verification": "pending_visual_review",
                "ambiguous_frame_count": sum(bool(row["association_ambiguous"]) for row in rows),
                "lost_frame_count": sum(row["track_status"] == "LOST" for row in rows),
                "centroid_motion": centroid_motion(rows),
            }
    scenarios = {
        "B": ("smart_toy_a", "smart_toy_b"),
        "C": ("smart_toy_b", "smart_toy_a"),
        "D": ("smart_toy_a", "smart_toy_b"),
        "E": ("smart_toy_b", "smart_toy_a"),
    }
    motion_evidence = {}
    for test_case, (moved_key, stationary_key) in scenarios.items():
        moved_span = summary["tests"][test_case][moved_key]["centroid_motion"]["max_axis_span"]
        stationary_span = summary["tests"][test_case][stationary_key]["centroid_motion"]["max_axis_span"]
        motion_evidence[test_case] = {
            "moved_product_key": moved_key,
            "stationary_product_key": stationary_key,
            "moved_max_axis_span_pixels": moved_span,
            "stationary_max_axis_span_pixels": stationary_span,
            "moved_min_pixels": 20.0,
            "stationary_max_pixels": 25.0,
            "passed": moved_span >= 20.0 and stationary_span <= 25.0,
        }
    metric_windows = [item for item in artifacts.window_metrics if item["window_no"] > 0]
    times = [float(item["processing_seconds"]) for item in metric_windows]
    lags = [
        float(row["fresh_lag"])
        for row in artifacts.rows
        if int(row["window_no"]) > 0
    ]
    tracking_passed = all(
        item["fresh_mask_coverage"] >= 0.90
        and item["product_track_continuity_rate"] >= 0.90
        and item["association_success_rate"] >= 0.90
        and item["token_change_count"] == 0
        and item["lost_count"] == 0
        for item in summary["products"].values()
    )
    motion_scenarios_passed = all(item["passed"] for item in motion_evidence.values())
    summary.update({
        "window_count": len(artifacts.window_metrics),
        "performance_window_count": len(metric_windows),
        "performance_excludes_operator_confirmation_window": True,
        "processing_seconds": distribution(times),
        "fresh_lag_seconds": distribution(lags),
        "peak_vram_allocated_gb": base.gpu_mem()["peak_allocated_gb"],
        "scenario_motion_evidence": motion_evidence,
        "acceptance": {
            "threshold": 0.90,
            "token_change_count": sum(item["token_change_count"] for item in summary["products"].values()),
            "id_swap_count": None,
            "id_swap_verification": "pending_visual_review",
            "unexpected_lost_count": sum(item["lost_count"] for item in summary["products"].values()),
            "tracking_metrics_passed": tracking_passed,
            "motion_scenarios_passed": motion_scenarios_passed,
            "overall_result": "NEEDS_REVIEW" if tracking_passed else "FAIL",
            "passed": False,
        },
        "jsonl": str(artifacts.jsonl_path),
        "overlay_video": str(artifacts.video_path),
        "keyframes": str(artifacts.keyframes_dir),
    })
    return summary


def summarize_offline(*, artifacts, state, source, args, runtime_id, grounding_guard):
    summary = build_cde_summary(
        rows=artifacts.rows,
        window_associations=state.window_associations,
        initial_tokens=state.initial_tokens,
    )
    metric_windows = [item for item in artifacts.window_metrics if item["window_no"] > 0]
    times = [float(item["processing_seconds"]) for item in metric_windows]
    report_path = artifacts.run_dir / "STAGE7C6_2_OCCLUSION_SAFE_RECOVERY_REPORT.md"
    summary.update({
        "stage": "7C.6.2",
        "mode": "video",
        "offline_replay": True,
        "realtime_fresh_lag_applicable": False,
        "fresh_lag_seconds": None,
        "text_prompt": TEXT_PROMPT,
        "text_groundings_per_window": 1,
        "text_grounding_count": grounding_guard.grounding_count,
        "bbox_handoff_used": False,
        "window_count": len(artifacts.window_metrics),
        "performance_window_count": len(metric_windows),
        "performance_excludes_operator_confirmation_window": True,
        "processing_seconds": distribution(times),
        "peak_vram_allocated_gb": base.gpu_mem()["peak_allocated_gb"],
        "metadata": {
            "runtime_id": runtime_id,
            "source_video": str(source.video_path),
            "video_duration_seconds": source.duration_seconds,
            "resolution": [source.source_width, source.source_height],
            "ai_resolution": [base.AI_WIDTH, base.AI_HEIGHT],
            "source_fps": source.source_fps,
            "sample_fps": base.TARGET_AI_FPS,
            "window_frames": base.WINDOW_FRAMES,
            "overlap_frames": base.OVERLAP_FRAMES,
            "text_prompt": TEXT_PROMPT,
            "camera_id": "cam02",
            "gpu": torch.cuda.get_device_name(0),
            "offline_replay": True,
            "realtime_fresh_lag_applicable": False,
            "timeline": {
                "test_c": [args.timeline.test_c_start, args.timeline.test_c_end],
                "test_d": [args.timeline.test_d_start, args.timeline.test_d_end],
                "test_e": [args.timeline.test_e_start, args.timeline.test_e_end],
            },
        },
        "window_associations": state.window_associations,
        "artifacts": {
            "summary": str(artifacts.summary_path),
            "observations": str(artifacts.jsonl_path),
            "overlay_video": str(artifacts.video_path),
            "keyframes": str(artifacts.keyframes_dir),
            "report": str(report_path),
        },
    })
    return summary, report_path


def _validate_frozen_probe_config() -> None:
    if (
        base.AI_WIDTH,
        base.AI_HEIGHT,
        base.TARGET_AI_FPS,
        base.WINDOW_FRAMES,
        base.OVERLAP_FRAMES,
        base.FIXED_BATCH,
    ) != (1280, 720, 5.0, 12, 4, 2):
        raise RuntimeError("Stage 7C.6.2 frozen SAM input/window parameters changed")


def main(argv=None) -> int:
    args = parse_args(argv)
    config, surface = stage7c.load_config()
    group = config["grounding_groups"][PRODUCT_TYPE]
    if group["text_prompt"] != TEXT_PROMPT or tuple(group["product_keys"]) != PRODUCT_KEYS:
        raise RuntimeError("Stage 7C.6.2 requires the frozen dual plush-toy config")
    runtime_id = datetime.now().strftime(
        f"stage7c6_2-{args.source}-%Y%m%d-%H%M%S"
    )
    run_dir = args.output_root / runtime_id
    run_dir.mkdir(parents=True)
    artifacts = ProbeArtifacts(run_dir, surface)
    runtime = MultiProductTrackRuntime(
        camera_id=config["camera_id"], runtime_instance_id=runtime_id,
        products={key: config["products"][key]["product_type"] for key in PRODUCT_KEYS},
        product_display_surface=surface, frame_width=base.AI_WIDTH, frame_height=base.AI_HEIGHT,
        surface_overlap_min=float(config["surface_overlap_min"]),
        product_track_lost_after_windows=int(config["product_track_lost_after_windows"]),
        overlap_iou_min=float(config["association_overlap_iou_min"]),
        geometry_distance_max=float(config["association_geometry_distance_max"]),
        ambiguity_margin=float(config["association_ambiguity_margin"]),
    )
    state = ProbeState()
    grounding_guard = SingleTextGroundingPerWindow()
    _validate_frozen_probe_config()
    if args.source == "live":
        base.validate_config()
    base.configure_torch()
    torch.cuda.reset_peak_memory_stats()
    sampler = None
    video_source = None
    predictor = None
    original_window_dir = base.WINDOW_DIR
    stop_event = threading.Event()
    announcer = None
    summary = None
    report_path = None
    try:
        if args.source == "live":
            sampler = base.AsyncAISampler().start()
        else:
            video_source = OfflineVideoWindowSource(
                args.video,
                target_fps=base.TARGET_AI_FPS,
                window_frames=base.WINDOW_FRAMES,
                overlap_frames=base.OVERLAP_FRAMES,
                frame_width=base.AI_WIDTH,
                frame_height=base.AI_HEIGHT,
            )
            if args.timeline.test_e_end > video_source.duration_seconds:
                raise ValueError(
                    "Test E end exceeds the source video duration: "
                    f"{args.timeline.test_e_end} > {video_source.duration_seconds:.3f}"
                )
        with torch.inference_mode(), base.autocast_context():
            predictor = base.build_predictor()
            base.tune_runtime(predictor)
            base.WINDOW_DIR = run_dir / "_active_window"
            if args.source == "live":
                start_after_sample_id = wait_for_operator_start(sampler, surface)
                source = LiveWindowSource(sampler, start_after_sample_id)
                windows = iter(source.windows())
                first_clock = None
            else:
                windows = iter(video_source.windows())
                first_clock = OfflineProbeClock(args.timeline)
                print(
                    f"STAGE7C6_VIDEO source={video_source.video_path} "
                    f"duration={video_source.duration_seconds:.3f}s "
                    f"source_fps={video_source.source_fps:.3f} sample_fps={base.TARGET_AI_FPS}",
                    flush=True,
                )
            try:
                window_items = next(windows)
            except StopIteration as error:
                raise RuntimeError("source does not contain one complete Rolling Window") from error
            if first_clock is None:
                first_clock = LiveProbeClock(window_items[0].wall_time)
            base.materialize_window(window_items)
            process_window(
                predictor=predictor, window_items=window_items, window_no=0,
                clock=first_clock, grounding_guard=grounding_guard,
                config=config, surface=surface,
                runtime=runtime, state=state, artifacts=artifacts,
            )
            if args.source == "live":
                run_clock = LiveProbeClock(time.time())
                announcer = start_timeline_announcer(run_clock.started_wall, stop_event)
            else:
                run_clock = first_clock
            print(
                f"STAGE7C6_STARTED runtime={runtime_id} source={args.source} "
                f"output={run_dir} identities_confirmed=true",
                flush=True,
            )
            for window_no, window_items in enumerate(windows, start=1):
                base.materialize_window(window_items)
                process_window(
                    predictor=predictor, window_items=window_items, window_no=window_no,
                    clock=run_clock, grounding_guard=grounding_guard,
                    config=config, surface=surface,
                    runtime=runtime, state=state, artifacts=artifacts,
                )
                elapsed = run_clock.elapsed(window_items[-1])
                end_seconds = (
                    TOTAL_SECONDS
                    if args.source == "live"
                    else args.timeline.test_e_end
                )
                if elapsed >= end_seconds:
                    break
    finally:
        stop_event.set()
        if announcer is not None:
            announcer.join(timeout=1.0)
        base.WINDOW_DIR = original_window_dir
        if sampler is not None:
            sampler.stop()
        if video_source is not None:
            video_source.close()
        artifacts.close()
        del predictor
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    if args.source == "video":
        summary, report_path = summarize_offline(
            artifacts=artifacts,
            state=state,
            source=video_source,
            args=args,
            runtime_id=runtime_id,
            grounding_guard=grounding_guard,
        )
    else:
        summary = summarize(artifacts, state)
        summary.update({
            "mode": "live",
            "offline_replay": False,
            "realtime_fresh_lag_applicable": True,
            "text_grounding_count": grounding_guard.grounding_count,
        })
    artifacts.summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    if report_path is not None:
        report_path.write_text(render_cde_report(summary), encoding="utf-8")
    print(f"STAGE7C6_COMPLETE summary={artifacts.summary_path}", flush=True)
    if report_path is not None:
        print(f"STAGE7C6_REPORT report={report_path}", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    result = summary["acceptance"].get("overall_result")
    return 2 if result == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
