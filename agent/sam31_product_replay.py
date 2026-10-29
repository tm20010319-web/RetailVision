"""Offline replay support for the Stage 7C.6 dual-product tracking probe."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence, TypeVar

import cv2
import numpy as np


PRODUCT_KEYS = ("smart_toy_a", "smart_toy_b")
T = TypeVar("T")


@dataclass(frozen=True)
class ReplaySampledFrame:
    sample_id: int
    source_seq: int
    wall_time: float
    frame: np.ndarray
    video_elapsed_seconds: float


@dataclass(frozen=True)
class TimelinePosition:
    test_case: str
    phase: str


@dataclass(frozen=True)
class CDETimeline:
    test_c_start: float
    test_c_end: float
    test_d_start: float
    test_d_end: float
    test_e_start: float
    test_e_end: float
    hold_seconds: float = 5.0

    def __post_init__(self) -> None:
        values = (
            self.test_c_start,
            self.test_c_end,
            self.test_d_start,
            self.test_d_end,
            self.test_e_start,
            self.test_e_end,
            self.hold_seconds,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("C/D/E timeline values must be finite")
        if self.test_c_start < 0 or self.hold_seconds <= 0:
            raise ValueError("timeline start must be non-negative and hold must be positive")
        if not (
            self.test_c_start < self.test_c_end
            <= self.test_d_start < self.test_d_end
            <= self.test_e_start < self.test_e_end
        ):
            raise ValueError("C/D/E timeline must be ordered and non-overlapping")
        if self.test_d_end - self.test_d_start < self.hold_seconds:
            raise ValueError("Test D must contain the configured handheld duration")
        if self.test_e_end - self.test_e_start < self.hold_seconds:
            raise ValueError("Test E must contain the configured handheld duration")

    def classify(self, video_elapsed_seconds: float) -> TimelinePosition:
        if not math.isfinite(video_elapsed_seconds) or video_elapsed_seconds < 0:
            raise ValueError("video elapsed seconds must be finite and non-negative")
        elapsed = float(video_elapsed_seconds)
        if elapsed < self.test_c_start:
            return TimelinePosition("INITIALIZATION", "stationary_identity_confirmation")
        if elapsed < self.test_c_end:
            return TimelinePosition(
                "C",
                _third_phase(
                    elapsed,
                    self.test_c_start,
                    self.test_c_end,
                    ("toy_b_position_1", "toy_b_position_2", "toy_b_position_3"),
                ),
            )
        if elapsed < self.test_d_start:
            return TimelinePosition("OUTSIDE_TEST", "gap_before_test_d")
        if elapsed < self.test_d_end:
            return TimelinePosition(
                "D",
                _surface_handheld_returned_phase(
                    elapsed,
                    self.test_d_start,
                    self.test_d_end,
                    self.hold_seconds,
                    "toy_a",
                ),
            )
        if elapsed < self.test_e_start:
            return TimelinePosition("OUTSIDE_TEST", "gap_before_test_e")
        if elapsed < self.test_e_end:
            return TimelinePosition(
                "E",
                _surface_handheld_returned_phase(
                    elapsed,
                    self.test_e_start,
                    self.test_e_end,
                    self.hold_seconds,
                    "toy_b",
                ),
            )
        return TimelinePosition("COMPLETE", "after_test_e")


class SingleTextGroundingPerWindow:
    """Fail closed if a probe attempts Text Grounding twice in one window."""

    def __init__(self) -> None:
        self._grounded_windows: set[int] = set()

    def run(self, window_no: int, operation: Callable[[], T]) -> T:
        if isinstance(window_no, bool) or not isinstance(window_no, int) or window_no < 0:
            raise ValueError("window_no must be a non-negative integer")
        if window_no in self._grounded_windows:
            raise RuntimeError("each Rolling Window permits one Text Re-Ground")
        self._grounded_windows.add(window_no)
        return operation()

    @property
    def grounding_count(self) -> int:
        return len(self._grounded_windows)


class OfflineVideoWindowSource:
    """Decode a video on its media timeline and yield frozen rolling windows."""

    def __init__(
        self,
        video_path: Path,
        *,
        target_fps: float,
        window_frames: int,
        overlap_frames: int,
        frame_width: int,
        frame_height: int,
    ) -> None:
        if target_fps <= 0:
            raise ValueError("target_fps must be positive")
        if not 0 < overlap_frames < window_frames:
            raise ValueError("window overlap must be smaller than the window")
        self.video_path = Path(video_path)
        if not self.video_path.is_file():
            raise ValueError(f"video does not exist: {self.video_path}")
        self.target_fps = float(target_fps)
        self.window_frames = int(window_frames)
        self.overlap_frames = int(overlap_frames)
        self.frame_width = int(frame_width)
        self.frame_height = int(frame_height)
        self._capture = cv2.VideoCapture(str(self.video_path))
        if not self._capture.isOpened():
            raise RuntimeError(f"cannot open video: {self.video_path}")
        self.source_fps = float(self._capture.get(cv2.CAP_PROP_FPS))
        self.source_frame_count = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.source_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.source_height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not math.isfinite(self.source_fps) or self.source_fps < self.target_fps:
            self.close()
            raise ValueError("source video FPS must be at least the target AI FPS")
        if self.source_frame_count <= 0:
            self.close()
            raise ValueError("source video must report a positive frame count")
        self.duration_seconds = self.source_frame_count / self.source_fps

    def windows(self) -> Iterable[tuple[ReplaySampledFrame, ...]]:
        iterator = self._sampled_frames()
        window = _take(iterator, self.window_frames)
        if len(window) < self.window_frames:
            return
        yield tuple(window)
        new_count = self.window_frames - self.overlap_frames
        while True:
            new_frames = _take(iterator, new_count)
            if len(new_frames) < new_count:
                return
            window = window[-self.overlap_frames :] + new_frames
            yield tuple(window)

    def close(self) -> None:
        if getattr(self, "_capture", None) is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> "OfflineVideoWindowSource":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _sampled_frames(self) -> Iterable[ReplaySampledFrame]:
        next_sample_seconds = 0.0
        sample_id = 0
        source_seq = 0
        interval = 1.0 / self.target_fps
        while self._capture is not None:
            ok, frame = self._capture.read()
            if not ok:
                return
            video_elapsed = source_seq / self.source_fps
            if video_elapsed + 1e-9 >= next_sample_seconds:
                if frame.shape[:2] != (self.frame_height, self.frame_width):
                    frame = cv2.resize(
                        frame,
                        (self.frame_width, self.frame_height),
                        interpolation=cv2.INTER_AREA,
                    )
                yield ReplaySampledFrame(
                    sample_id=sample_id,
                    source_seq=source_seq,
                    wall_time=video_elapsed,
                    frame=frame,
                    video_elapsed_seconds=video_elapsed,
                )
                sample_id += 1
                next_sample_seconds = sample_id * interval
            source_seq += 1


def sampled_source_indices(
    *, source_fps: float, source_frame_count: int, target_fps: float
) -> tuple[int, ...]:
    """Pure form of the offline sampling schedule used for timestamp tests."""
    if source_fps < target_fps or target_fps <= 0 or source_frame_count < 0:
        raise ValueError("invalid source or target sampling rate")
    result = []
    next_sample_seconds = 0.0
    sample_id = 0
    for source_seq in range(source_frame_count):
        elapsed = source_seq / source_fps
        if elapsed + 1e-9 >= next_sample_seconds:
            result.append(source_seq)
            sample_id += 1
            next_sample_seconds = sample_id / target_fps
    return tuple(result)


def offline_observed_at(video_elapsed_seconds: float) -> datetime:
    if not math.isfinite(video_elapsed_seconds) or video_elapsed_seconds < 0:
        raise ValueError("video elapsed seconds must be finite and non-negative")
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=float(video_elapsed_seconds)
    )


def validate_probe_source(
    *,
    source: str,
    video_path: Optional[Path],
    timeline: Optional[CDETimeline],
) -> None:
    if source not in {"live", "video"}:
        raise ValueError("source must be live or video")
    if source == "live" and (video_path is not None or timeline is not None):
        raise ValueError("live mode cannot use video replay arguments")
    if source == "video" and (video_path is None or timeline is None):
        raise ValueError("video mode requires a video and C/D/E timeline")


def build_cde_summary(
    *,
    rows: Sequence[Mapping[str, object]],
    window_associations: Sequence[Mapping[str, object]],
    initial_tokens: Mapping[str, str],
    visually_verified_id_swap_count: Optional[int] = None,
    active_anchor_corruption_count: Optional[int] = None,
    safe_recovery_success: Optional[bool] = None,
    product_keys: Optional[Sequence[str]] = None,
) -> dict[str, object]:
    visual_review_complete = (
        visually_verified_id_swap_count is not None
        and active_anchor_corruption_count is not None
        and safe_recovery_success is not None
    )
    selected_product_keys = tuple(product_keys or PRODUCT_KEYS)
    if len(set(selected_product_keys)) != len(selected_product_keys):
        raise ValueError("product_keys must be unique")
    tests: dict[str, object] = {}
    for test_case in ("C", "D", "E"):
        products = {}
        for product_key in selected_product_keys:
            product_rows = [
                row
                for row in rows
                if row.get("test_case") == test_case
                and row.get("product_key") == product_key
            ]
            observed = [row for row in product_rows if bool(row.get("mask_present"))]
            associations = [
                item
                for item in window_associations
                if item.get("test_case") == test_case
                and item.get("product_key") == product_key
                and bool(item.get("association_eligible", True))
            ]
            tokens = sorted({
                str(row["product_track_token"])
                for row in product_rows
                if row.get("product_track_token")
            })
            segments = sorted({
                int(row["product_track_id"])
                for row in product_rows
                if row.get("product_track_id") is not None
            })
            initial_token = initial_tokens.get(product_key)
            token_change_count = sum(
                row.get("product_track_token") not in (None, initial_token)
                for row in product_rows
            )
            token_continuity_rate = (
                sum(
                    row.get("product_track_token") == initial_token
                    for row in product_rows
                ) / len(product_rows)
                if product_rows else 0.0
            )
            segment_change_count = max(0, len(segments) - 1)
            lost_windows = {
                int(item["window_no"])
                for item in associations
                if bool(item.get("tracking_lost"))
                or item.get("track_status") == "LOST"
            }
            success_count = sum(bool(item.get("association_success")) for item in associations)
            recoveries = [
                item
                for item in associations
                if bool(item.get("recovered_from_missing_pending"))
            ]
            legacy_continuity = (
                sum(row.get("product_track_token") == initial_token for row in observed)
                / len(product_rows)
                if product_rows else 0.0
            )
            if not visual_review_complete:
                identity_continuity_status = "NEEDS_VISUAL_REVIEW"
            elif (
                visually_verified_id_swap_count == 0
                and active_anchor_corruption_count == 0
                and (not recoveries or safe_recovery_success is True)
            ):
                identity_continuity_status = "PASS"
            else:
                identity_continuity_status = "FAIL"
            products[product_key] = {
                "fresh_frame_count": len(product_rows),
                "fresh_mask_count": len(observed),
                "fresh_mask_coverage": len(observed) / len(product_rows) if product_rows else 0.0,
                "observation_coverage": len(observed) / len(product_rows) if product_rows else 0.0,
                "product_track_continuity_rate": legacy_continuity,
                "legacy_observation_based_continuity": legacy_continuity,
                "deprecated_for_identity_acceptance": True,
                "association_eligible_windows": len(associations),
                "association_success_windows": success_count,
                "association_success_rate": success_count / len(associations) if associations else 0.0,
                "raw_association_success_rate": success_count / len(associations) if associations else 0.0,
                "ambiguous_association_count": sum(
                    bool(item.get("association_ambiguous")) for item in associations
                ),
                "lost_count": len(lost_windows),
                "unexpected_lost_count": len(lost_windows),
                "temporary_missing_recovery_count": len(recoveries),
                "recovered_after_missed_windows": [
                    int(item.get("recovered_after_missed_windows", 0))
                    for item in recoveries
                ],
                "recovery_latency_windows": [
                    int(item.get("recovery_latency_windows", 1))
                    for item in recoveries
                ],
                "safe_recovery_success": (
                    safe_recovery_success if recoveries else None
                ),
                "active_anchor_corruption_count": active_anchor_corruption_count,
                "product_track_tokens": tokens,
                "segment_count": len(segments),
                "segment_change_count": segment_change_count,
                "centroid_motion": centroid_motion(product_rows),
                "token_continuity_rate": token_continuity_rate,
                "token_change_count": token_change_count,
                "id_swap_count": visually_verified_id_swap_count,
                "visual_id_swap_count": visually_verified_id_swap_count,
                "id_swap_verification": (
                    "visually_verified"
                    if visually_verified_id_swap_count is not None
                    else "pending_visual_review"
                ),
                "identity_continuity_status": identity_continuity_status,
            }
        tests[test_case] = products

    product_results = [
        product
        for test_result in tests.values()
        for product in test_result.values()
    ]
    c_d_products = [
        product
        for test_case in ("C", "D")
        for product in tests[test_case].values()
    ]
    c_d_hard_gates_met = bool(c_d_products) and all(
        product["observation_coverage"] >= 0.90
        and product["raw_association_success_rate"] >= 0.90
        and product["token_continuity_rate"] == 1.0
        and product["segment_change_count"] == 0
        and product["unexpected_lost_count"] == 0
        for product in c_d_products
    )
    unexpected_lost_count = sum(
        int(product["unexpected_lost_count"]) for product in product_results
    )
    token_change_count = sum(int(product["token_change_count"]) for product in product_results)
    segment_change_count = sum(
        int(product["segment_change_count"]) for product in product_results
    )
    temporary_missing_recovery_count = sum(
        int(product["temporary_missing_recovery_count"])
        for product in product_results
    )
    expected_motion = {
        "C": ("smart_toy_b", "smart_toy_a"),
        "D": ("smart_toy_a", "smart_toy_b"),
        "E": ("smart_toy_b", "smart_toy_a"),
    }
    scenario_motion_evidence = {}
    test_phase_evidence = {}
    for test_case, (moved_key, stationary_key) in expected_motion.items():
        moved_span = tests[test_case][moved_key]["centroid_motion"]["max_axis_span"]
        stationary_span = tests[test_case][stationary_key]["centroid_motion"]["max_axis_span"]
        scenario_motion_evidence[test_case] = {
            "moved_product_key": moved_key,
            "stationary_product_key": stationary_key,
            "moved_max_axis_span_pixels": moved_span,
            "stationary_max_axis_span_pixels": stationary_span,
            "moved_min_pixels": 20.0,
            "stationary_max_pixels": 25.0,
            "motion_pattern_observed": moved_span >= 20.0 and stationary_span <= 25.0,
            "acceptance_role": (
                "advisory_occlusion_metric"
                if test_case == "E"
                else "quality_gate"
            ),
        }
        phase_frames: dict[str, set[object]] = {}
        for index, row in enumerate(rows):
            if row.get("test_case") != test_case or not row.get("phase"):
                continue
            frame_identity = row.get("source_seq", (row.get("timestamp"), index))
            phase_frames.setdefault(str(row["phase"]), set()).add(frame_identity)
        test_phase_evidence[test_case] = {
            "timeline_phase_frame_counts": {
                phase: len(frames) for phase, frames in sorted(phase_frames.items())
            },
            "visual_sequence_verification": "pending_visual_review",
        }
    motion_scenarios_observed = all(
        item["motion_pattern_observed"] for item in scenario_motion_evidence.values()
    )
    e_products = list(tests["E"].values())
    e_temporary_missing_recovery_count = sum(
        int(product["temporary_missing_recovery_count"])
        for product in e_products
    )
    e_automatic_identity_prerequisites_met = (
        e_temporary_missing_recovery_count >= 1
        and all(
            product["unexpected_lost_count"] == 0
            and product["token_change_count"] == 0
            and product["segment_change_count"] == 0
            for product in e_products
        )
    )
    automatic_hard_gates_met = (
        c_d_hard_gates_met
        and e_automatic_identity_prerequisites_met
        and unexpected_lost_count == 0
        and token_change_count == 0
        and segment_change_count == 0
    )
    e_observation_coverage_soft_target_met = all(
        product["observation_coverage"] >= 0.80 for product in e_products
    )
    visual_identity_gates_met = bool(
        visual_review_complete
        and visually_verified_id_swap_count == 0
        and active_anchor_corruption_count == 0
        and safe_recovery_success is True
    )
    if not automatic_hard_gates_met:
        overall_result = "FAIL"
    elif not visual_review_complete:
        overall_result = "NEEDS_REVIEW"
    elif not visual_identity_gates_met:
        overall_result = "FAIL"
    elif not e_observation_coverage_soft_target_met:
        overall_result = "PASS_WITH_COVERAGE_WARNING"
    else:
        overall_result = "PASS"
    return {
        "tests": tests,
        "identity_verification": {
            "token_continuity_change_count": token_change_count,
            "segment_change_count": segment_change_count,
            "visually_verified_id_swap_count": visually_verified_id_swap_count,
            "active_anchor_corruption_count": active_anchor_corruption_count,
            "safe_recovery_success": safe_recovery_success,
            "visual_review_status": (
                "verified"
                if visually_verified_id_swap_count is not None
                else "pending"
            ),
            "note": (
                "Stable Product Track tokens do not prove that visual A/B identity did not swap."
            ),
        },
        "scenario_motion_evidence": scenario_motion_evidence,
        "test_phase_evidence": test_phase_evidence,
        "acceptance": {
            "target_metrics_met": c_d_hard_gates_met,
            "c_d_hard_gates_met": c_d_hard_gates_met,
            "e_automatic_identity_prerequisites_met": (
                e_automatic_identity_prerequisites_met
            ),
            "automatic_hard_gates_met": automatic_hard_gates_met,
            "e_observation_coverage_soft_target": 0.80,
            "e_observation_coverage_soft_target_met": (
                e_observation_coverage_soft_target_met
            ),
            "motion_scenarios_observed": motion_scenarios_observed,
            "unexpected_lost_count": unexpected_lost_count,
            "token_change_count": token_change_count,
            "segment_change_count": segment_change_count,
            "temporary_missing_recovery_count": temporary_missing_recovery_count,
            "e_temporary_missing_recovery_count": (
                e_temporary_missing_recovery_count
            ),
            "id_swap_count": visually_verified_id_swap_count,
            "visual_id_swap_count": visually_verified_id_swap_count,
            "active_anchor_corruption_count": active_anchor_corruption_count,
            "safe_recovery_success": safe_recovery_success,
            "identity_continuity_status": (
                "NEEDS_VISUAL_REVIEW"
                if not visual_review_complete
                else "PASS" if visual_identity_gates_met else "FAIL"
            ),
            "overall_result": overall_result,
        },
    }


def render_cde_report(summary: Mapping[str, object]) -> str:
    metadata = summary["metadata"]
    tests = summary["tests"]
    acceptance = summary["acceptance"]
    lines = [
        "# Stage 7C.6.2 Occlusion-Safe Recovery Test Report",
        "",
        "## 1. Test Metadata",
        "",
        f"- Runtime ID: `{metadata['runtime_id']}`",
        f"- Source video: `{metadata['source_video']}`",
        f"- Video duration: {metadata['video_duration_seconds']:.3f}s",
        f"- Resolution: {metadata['resolution'][0]}x{metadata['resolution'][1]}",
        f"- Sample FPS: {metadata['sample_fps']}",
        f"- Window / overlap: {metadata['window_frames']} / {metadata['overlap_frames']}",
        f"- Prompt: `{metadata['text_prompt']}`",
        f"- Camera: `{metadata['camera_id']}`",
        f"- GPU: `{metadata['gpu']}`",
        "- Offline replay: `true`",
        "- Realtime fresh lag applicable: `false`",
        "",
        "## 2. Overall Result",
        "",
        f"**{acceptance['overall_result']}**",
        "",
    ]
    for number, test_case in enumerate(("C", "D", "E"), start=3):
        lines.extend([f"## {number}. Test {test_case}", ""])
        if test_case == "C":
            lines.append("Toy B moves through three positions while Toy A is expected to remain stable.")
        elif test_case == "D":
            lines.append("Toy A follows surface -> handheld -> returned while Toy B is expected to remain stable.")
        else:
            lines.append("Toy B follows surface -> handheld -> returned while Toy A is expected to remain stable.")
        motion = summary["scenario_motion_evidence"][test_case]
        phases = summary["test_phase_evidence"][test_case]
        lines.extend([
            "",
            (
                f"Centroid motion: moved `{motion['moved_product_key']}` "
                f"{motion['moved_max_axis_span_pixels']:.2f}px; stationary "
                f"`{motion['stationary_product_key']}` "
                f"{motion['stationary_max_axis_span_pixels']:.2f}px; "
                f"pattern observed: `{str(motion['motion_pattern_observed']).lower()}`."
            ),
            (
                "Timeline phase frames: `"
                + json_like_phase_counts(phases["timeline_phase_frame_counts"])
                + "`; visual sequence verification: `pending_visual_review`."
            ),
            "",
            _test_markdown_table(tests[test_case]),
            "",
        ])

    lines.extend(["## 6. Windows 63-69 Association Evidence", ""])
    critical = [
        item
        for item in summary.get("window_associations", ())
        if 63 <= int(item.get("window_no", -1)) <= 69
    ]
    if critical:
        lines.extend([
            "| Window | Product | Status | Misses | Role | Candidates | Local ID | Locked | Recovery | Rejected reason |",
            "| ---: | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- |",
        ])
        for item in critical:
            locked = ",".join(
                str(value) for value in item.get("anchor_locked_candidate_ids", ())
            ) or "-"
            recovery = (
                f"attempted={str(bool(item.get('recovery_attempted'))).lower()}, "
                f"success={str(bool(item.get('recovery_success'))).lower()}"
            )
            lines.append(
                f"| {item['window_no']} | {item['product_key']} | "
                f"{item.get('track_status')} | "
                f"{item.get('consecutive_missed_windows', 0)} | "
                f"{item.get('association_role', 'normal')} | "
                f"{item.get('grounding_candidate_count', 0)} | "
                f"{item.get('local_obj_id') if item.get('local_obj_id') is not None else '-'} | "
                f"{locked} | {recovery} | "
                f"{item.get('recovery_rejected_reason') or '-'} |"
            )
        lines.append("")
    else:
        lines.extend(["- No Window 63-69 evidence was recorded.", ""])

    lines.extend(["## 7. Failure / Risk Analysis", ""])
    diagnostics = _association_diagnostics(summary.get("window_associations", ()))
    if diagnostics:
        lines.extend(f"- {item}" for item in diagnostics)
    else:
        lines.append("- No ambiguous, missed, LOST, or recovery window was recorded.")
    identity = summary["identity_verification"]
    if identity["visual_review_status"] != "verified":
        lines.append(
            "- Visual A/B ID swap review is pending. Token continuity alone is not accepted as proof of zero visual swaps."
        )
    lines.extend([
        "",
        "## 8. Artifact Paths",
        "",
        f"- Summary: `{summary['artifacts']['summary']}`",
        f"- Observations: `{summary['artifacts']['observations']}`",
        f"- Overlay video: `{summary['artifacts']['overlay_video']}`",
        f"- Keyframes: `{summary['artifacts']['keyframes']}`",
        "",
        "This report evaluates Product Tracking only. It does not emit PICK_UP, HOLDING, PUT_DOWN, or Person/Product association events.",
        "",
    ])
    return "\n".join(lines)


def centroid_motion(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    points = [row.get("centroid_pixel") for row in rows if row.get("centroid_pixel")]
    if not points:
        return {"x_span": 0.0, "y_span": 0.0, "max_axis_span": 0.0}
    x_values = [float(point[0]) for point in points]
    y_values = [float(point[1]) for point in points]
    x_span = max(x_values) - min(x_values)
    y_span = max(y_values) - min(y_values)
    return {"x_span": x_span, "y_span": y_span, "max_axis_span": max(x_span, y_span)}


def json_like_phase_counts(values: Mapping[str, object]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in values.items()) or "none"


def _third_phase(elapsed: float, start: float, end: float, names: tuple[str, str, str]) -> str:
    third = (end - start) / 3.0
    if elapsed < start + third:
        return names[0]
    if elapsed < start + 2 * third:
        return names[1]
    return names[2]


def _surface_handheld_returned_phase(
    elapsed: float,
    start: float,
    end: float,
    hold_seconds: float,
    toy_name: str,
) -> str:
    surface_seconds = (end - start - hold_seconds) / 2.0
    if elapsed < start + surface_seconds:
        return f"{toy_name}_surface"
    if elapsed < start + surface_seconds + hold_seconds:
        return f"{toy_name}_handheld"
    return f"{toy_name}_returned"


def _take(iterator: Iterable[T], count: int) -> list[T]:
    result = []
    iterator = iter(iterator)
    for _ in range(count):
        try:
            result.append(next(iterator))
        except StopIteration:
            break
    return result


def _test_markdown_table(products: Mapping[str, Mapping[str, object]]) -> str:
    lines = [
        "| Product | Obs Coverage | Raw Association | Token Continuity | Segment Changes | Safe Recovery | LOST | Visual ID Swap | Identity Continuity |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for product_key in products:
        item = products[product_key]
        visual_swaps = item["id_swap_count"]
        lines.append(
            f"| {product_key} | {item['observation_coverage']:.1%} | "
            f"{item['raw_association_success_rate']:.1%} | "
            f"{item['token_continuity_rate']:.1%} | "
            f"{item['segment_change_count']} | "
            f"{item['safe_recovery_success'] if item['safe_recovery_success'] is not None else 'N/A_OR_PENDING'} | "
            f"{item['lost_count']} | "
            f"{'PENDING_REVIEW' if visual_swaps is None else visual_swaps} | "
            f"{item['identity_continuity_status']} |"
        )
    return "\n".join(lines)


def _association_diagnostics(
    associations: Sequence[Mapping[str, object]],
) -> list[str]:
    result = []
    previous_success: dict[str, bool] = {}
    for item in sorted(
        associations,
        key=lambda value: (int(value["window_no"]), str(value["product_key"])),
    ):
        key = str(item["product_key"])
        success = bool(item.get("association_success"))
        if bool(item.get("association_ambiguous")):
            result.append(f"Window {item['window_no']}: {key} association ambiguous.")
        elif item.get("track_status") == "LOST" or bool(item.get("tracking_lost")):
            result.append(f"Window {item['window_no']}: {key} LOST.")
        elif not success:
            result.append(f"Window {item['window_no']}: {key} MISSING_PENDING / association miss.")
        elif previous_success.get(key) is False:
            result.append(
                f"Window {item['window_no']}: {key} recovered by "
                f"{item.get('association_method', 'unknown')} after "
                f"{item.get('recovered_after_missed_windows', 'unknown')} missed windows."
            )
        previous_success[key] = success
    return result
