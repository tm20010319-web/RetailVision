"""Run staff-vest inference on an independent video and export review evidence."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


HARD_CASE_CATEGORIES = (
    "false_positive",
    "false_negative",
    "low_confidence",
    "occlusion",
    "far_distance",
    "blue_background",
    "blue_clothes",
)


@dataclass(frozen=True)
class Detection:
    bbox: tuple[float, float, float, float]
    confidence: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stats", type=Path)
    parser.add_argument("--person-model", default="data/models/yolov8n.pt")
    parser.add_argument("--device", default="0")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--low-confidence", type=float, default=0.10)
    parser.add_argument("--person-confidence", type=float, default=0.25)
    parser.add_argument("--blue-ratio", type=float, default=0.12)
    parser.add_argument("--far-height-ratio", type=float, default=0.12)
    parser.add_argument(
        "--hard-cases-dir",
        type=Path,
        default=Path("data/staff_vest_hard_cases"),
    )
    parser.add_argument("--hard-case-cooldown-frames", type=int, default=15)
    parser.add_argument("--max-frames", type=int)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.source.is_file():
        raise ValueError(f"source video does not exist: {args.source}")
    if args.output.exists():
        raise ValueError(f"output already exists; refusing to overwrite: {args.output}")
    stats_path = args.stats or args.output.with_suffix(".json")
    if stats_path.exists():
        raise ValueError(f"stats already exist; refusing to overwrite: {stats_path}")
    if args.imgsz < 32:
        raise ValueError("--imgsz must be at least 32")
    if not 0 <= args.low_confidence <= args.confidence <= 1:
        raise ValueError("confidence must satisfy 0 <= low-confidence <= confidence <= 1")
    if not 0 <= args.person_confidence <= 1:
        raise ValueError("--person-confidence must be between 0 and 1")
    if not 0 <= args.blue_ratio <= 1:
        raise ValueError("--blue-ratio must be between 0 and 1")
    if not 0 < args.far_height_ratio <= 1:
        raise ValueError("--far-height-ratio must be in (0, 1]")
    if args.hard_case_cooldown_frames < 0:
        raise ValueError("--hard-case-cooldown-frames cannot be negative")
    if args.max_frames is not None and args.max_frames < 1:
        raise ValueError("--max-frames must be at least one")


def values(value: Any) -> list[Any]:
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def detections(result: Any) -> list[Detection]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    coordinates = values(boxes.xyxy)
    confidences = values(boxes.conf)
    return [
        Detection(tuple(float(item) for item in bbox), float(confidences[index]))
        for index, bbox in enumerate(coordinates)
    ]


def vest_belongs_to_person(vest: Detection, person: Detection) -> bool:
    vx1, vy1, vx2, vy2 = vest.bbox
    px1, py1, px2, py2 = person.bbox
    center_x = (vx1 + vx2) / 2
    center_y = (vy1 + vy2) / 2
    torso_top = py1 + (py2 - py1) * 0.10
    torso_bottom = py1 + (py2 - py1) * 0.78
    return px1 <= center_x <= px2 and torso_top <= center_y <= torso_bottom


def blue_torso_ratio(frame: np.ndarray, person: Detection) -> float:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = person.bbox
    left = max(0, min(width, round(x1)))
    right = max(0, min(width, round(x2)))
    top = max(0, min(height, round(y1 + (y2 - y1) * 0.12)))
    bottom = max(0, min(height, round(y1 + (y2 - y1) * 0.72)))
    if right <= left or bottom <= top:
        return 0.0
    crop = frame[top:bottom, left:right]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array((85, 60, 35)), np.array((135, 255, 255)))
    return float(np.count_nonzero(mask) / mask.size) if mask.size else 0.0


def draw_box(
    frame: np.ndarray,
    detection: Detection,
    *,
    color: tuple[int, int, int],
    label: str,
) -> None:
    x1, y1, x2, y2 = (round(value) for value in detection.bbox)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(
        frame,
        label,
        (x1, max(18, y1 - 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        1,
        cv2.LINE_AA,
    )


def run_prediction(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    from ultralytics import YOLO

    vest_model = YOLO(str(args.model), task="detect")
    person_model = YOLO(str(args.person_model), task="detect")
    capture = cv2.VideoCapture(str(args.source))
    if not capture.isOpened():
        raise ValueError(f"cannot open source video: {args.source}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if source_fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise ValueError("source video has invalid FPS or dimensions")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(args.output),
        cv2.VideoWriter_fourcc(*"mp4v"),
        source_fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise ValueError(f"cannot create output video: {args.output}")
    for category in HARD_CASE_CATEGORIES:
        (args.hard_cases_dir / category).mkdir(parents=True, exist_ok=True)

    total_frames = 0
    detection_frames = 0
    detections_total = 0
    confidence_total = 0.0
    suspected_false_positives = 0
    suspected_false_positive_frames = 0
    suspected_false_negatives = 0
    suspected_false_negative_frames = 0
    vest_inference_seconds = 0.0
    processing_started = time.perf_counter()
    hard_case_counts = {category: 0 for category in HARD_CASE_CATEGORIES}
    last_hard_case_frame = {category: -10**9 for category in HARD_CASE_CATEGORIES}
    detection_flags: list[bool] = []

    def save_hard_case(category: str, frame: np.ndarray, frame_index: int) -> None:
        if frame_index - last_hard_case_frame[category] < args.hard_case_cooldown_frames:
            return
        target = args.hard_cases_dir / category / f"frame_{frame_index:08d}.jpg"
        if cv2.imwrite(str(target), frame):
            hard_case_counts[category] += 1
            last_hard_case_frame[category] = frame_index

    try:
        while True:
            if args.max_frames is not None and total_frames >= args.max_frames:
                break
            ok, frame = capture.read()
            if not ok:
                break
            frame_started = time.perf_counter()
            vest_started = time.perf_counter()
            vest_results = vest_model.predict(
                frame,
                conf=args.low_confidence,
                imgsz=args.imgsz,
                device=args.device,
                verbose=False,
            )
            vest_inference_seconds += time.perf_counter() - vest_started
            person_results = person_model.predict(
                frame,
                conf=args.person_confidence,
                classes=[0],
                imgsz=args.imgsz,
                device=args.device,
                verbose=False,
            )
            vest_items = detections(vest_results[0]) if vest_results else []
            person_items = detections(person_results[0]) if person_results else []
            active = [item for item in vest_items if item.confidence >= args.confidence]
            low_confidence = [
                item for item in vest_items if item.confidence < args.confidence
            ]
            if active:
                detection_frames += 1
            detection_flags.append(bool(active))
            detections_total += len(active)
            confidence_total += sum(item.confidence for item in active)

            frame_false_positives = 0
            assigned_people: set[int] = set()
            for vest in active:
                associations = [
                    index
                    for index, person in enumerate(person_items)
                    if vest_belongs_to_person(vest, person)
                ]
                if associations:
                    assigned_people.add(associations[0])
                    color = (40, 210, 40)
                    label = f"staff_vest {vest.confidence:.2f}"
                else:
                    frame_false_positives += 1
                    color = (40, 40, 230)
                    label = f"review FP? {vest.confidence:.2f}"
                draw_box(frame, vest, color=color, label=label)
                _, y1, _, y2 = vest.bbox
                if (y2 - y1) / height < args.far_height_ratio:
                    save_hard_case("far_distance", frame, total_frames)
                x1, vy1, x2, vy2 = vest.bbox
                if x1 <= 2 or vy1 <= 2 or x2 >= width - 2 or vy2 >= height - 2:
                    save_hard_case("occlusion", frame, total_frames)
            if frame_false_positives:
                suspected_false_positives += frame_false_positives
                suspected_false_positive_frames += 1
                save_hard_case("false_positive", frame, total_frames)
                save_hard_case("blue_background", frame, total_frames)

            frame_false_negatives = 0
            for index, person in enumerate(person_items):
                if index in assigned_people:
                    continue
                ratio = blue_torso_ratio(frame, person)
                if ratio < args.blue_ratio:
                    continue
                frame_false_negatives += 1
                draw_box(
                    frame,
                    person,
                    color=(220, 40, 220),
                    label=f"review FN? blue={ratio:.2f}",
                )
            if frame_false_negatives:
                suspected_false_negatives += frame_false_negatives
                suspected_false_negative_frames += 1
                save_hard_case("false_negative", frame, total_frames)
                save_hard_case("blue_clothes", frame, total_frames)
            if low_confidence:
                for item in low_confidence:
                    draw_box(
                        frame,
                        item,
                        color=(0, 200, 255),
                        label=f"low conf {item.confidence:.2f}",
                    )
                save_hard_case("low_confidence", frame, total_frames)

            total_frames += 1
            elapsed = time.perf_counter() - processing_started
            processing_fps = total_frames / elapsed if elapsed > 0 else 0.0
            cv2.putText(
                frame,
                f"FPS {processing_fps:.1f}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            writer.write(frame)
            _ = frame_started
    finally:
        capture.release()
        writer.release()

    processing_seconds = time.perf_counter() - processing_started
    average_confidence = (
        confidence_total / detections_total if detections_total else 0.0
    )
    longest_detection_run = 0
    current_run = 0
    for detected in detection_flags:
        current_run = current_run + 1 if detected else 0
        longest_detection_run = max(longest_detection_run, current_run)
    continuity_windows = 0
    continuity_windows_passed = 0
    if any(detection_flags):
        first_detection = detection_flags.index(True)
        last_detection = len(detection_flags) - 1 - detection_flags[::-1].index(True)
        active_span = detection_flags[first_detection : last_detection + 1]
        continuity_windows = max(0, len(active_span) - 4)
        continuity_windows_passed = sum(
            sum(active_span[index : index + 5]) >= 3
            for index in range(continuity_windows)
        )
    return {
        "source": str(args.source.resolve()),
        "output": str(args.output.resolve()),
        "model": str(args.model),
        "person_model": str(args.person_model),
        "device": args.device,
        "confidence_threshold": args.confidence,
        "low_confidence_threshold": args.low_confidence,
        "source_fps": round(source_fps, 4),
        "source_frame_count": source_frames,
        "total_frames": total_frames,
        "detection_frames": detection_frames,
        "detections": detections_total,
        "average_confidence": round(average_confidence, 6),
        "suspected_false_positives": suspected_false_positives,
        "suspected_false_positive_frames": suspected_false_positive_frames,
        "suspected_false_negatives": suspected_false_negatives,
        "suspected_false_negative_frames": suspected_false_negative_frames,
        "longest_detection_run_frames": longest_detection_run,
        "five_frame_windows": continuity_windows,
        "five_frame_windows_with_three_hits": continuity_windows_passed,
        "five_frame_three_hit_ratio": round(
            continuity_windows_passed / continuity_windows, 6
        ) if continuity_windows else 0.0,
        "mean_vest_inference_ms": round(
            vest_inference_seconds * 1000 / total_frames, 3
        ) if total_frames else 0.0,
        "processing_seconds": round(processing_seconds, 3),
        "processing_fps": round(
            total_frames / processing_seconds, 3
        ) if processing_seconds > 0 else 0.0,
        "hard_cases": hard_case_counts,
        "review_note": (
            "Suspected false positives and false negatives are person-association and "
            "blue-torso heuristics; manual review is required."
        ),
    }


def main() -> int:
    args = parse_args()
    try:
        report = run_prediction(args)
        stats_path = args.stats or args.output.with_suffix(".json")
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
