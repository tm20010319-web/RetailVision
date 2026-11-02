"""Run the controlled-demo Greeting profile against a real video segment."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.behavior.behavior_monitor import BehaviorMonitor
from agent.behavior.staff_greeting_evaluation import StaffGreetingEvaluation
from agent.behavior.behavior_vision import (
    BehaviorVisionAdapter,
    draw_behavior_area_overlay,
    load_behavior_camera_configs,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "configs/examples/behavior_cameras.example.json"
    )
    parser.add_argument(
        "--person-model", type=Path, default=PROJECT_ROOT / "data/models/yolo11n.pt"
    )
    parser.add_argument(
        "--vest-model",
        type=Path,
        default=PROJECT_ROOT / "data/models/staff_vest.pt",
    )
    parser.add_argument(
        "--pose-model",
        type=Path,
        default=PROJECT_ROOT / "data/models/yolo11n-pose.pt",
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--start-seconds", type=float, default=25.0)
    parser.add_argument("--end-seconds", type=float, default=70.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs/controlled_demo_threshold",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    for path in (args.source, args.config, args.person_model, args.vest_model, args.pose_model):
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.end_seconds <= args.start_seconds:
        raise ValueError("end-seconds must be greater than start-seconds")

    camera = load_behavior_camera_configs(args.config)[0]
    if camera.monitor_config.greeting_profile != "controlled_demo":
        raise ValueError("the selected camera must use controlled_demo")

    adapter = BehaviorVisionAdapter(
        store_id=camera.store_id,
        camera_id=camera.camera_id,
        person_model_path=str(args.person_model),
        staff_vest_model_path=str(args.vest_model),
        pose_model_path=str(args.pose_model),
        device=args.device,
        staff_vest_enabled=True,
        ground_projector=camera.ground_projector,
        entry_trigger=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        calibration_status=camera.calibration_status,
        calibration_image_size=camera.calibration_image_size,
        runtime_id="controlled-demo-threshold-video-a",
    )
    monitor = BehaviorMonitor(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        config=camera.monitor_config,
    )
    staff_greeting = StaffGreetingEvaluation(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        config=camera.monitor_config,
        behavior_sink=lambda _event: None,
        calibration_status=camera.calibration_status,
        ground_calibration_available=camera.ground_projector is not None,
    )

    capture = cv2.VideoCapture(str(args.source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open source: {args.source}")
    capture.set(cv2.CAP_PROP_POS_MSEC, args.start_seconds * 1000.0)
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    video_path = args.output_dir / "video_a_controlled_demo.mp4"
    writer = cv2.VideoWriter(
        str(video_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        source_fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"cannot open output video: {video_path}")

    entries = []
    greetings = []
    frame_times_ms = []
    frames_processed = 0
    maximum_debug = {
        "staff_travel_metres": 0.0,
        "staff_toward_customer_metres": 0.0,
        "distance_reduction_metres": 0.0,
        "minimum_distance_metres": None,
        "sustained_seconds": 0.0,
    }
    final_decision = "not_confirmed"
    wall_started = time.perf_counter()
    base_time = datetime(2026, 7, 30, 12, 0, 0)
    try:
        while True:
            available, frame = capture.read()
            if not available:
                break
            position_seconds = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if position_seconds > args.end_seconds:
                break
            observed_at = base_time + timedelta(seconds=position_seconds)
            frame_started = time.perf_counter()
            observations = adapter.process_frame(frame)
            new_entries, behaviors = monitor.process(observed_at, observations)
            behaviors.extend(
                staff_greeting.observe(observed_at, observations, new_entries)
            )
            entries.extend(new_entries)
            greetings.extend(
                event for event in behaviors if event.event_type == "staff_greeting"
            )

            track_state_lines = {}
            for observation in observations:
                track_state_lines[observation.track_token] = (
                    monitor.entry_debug_lines(observation.track_token)
                    + staff_greeting.debug_lines(observation.track_token)
                )
                if observation.role != "customer":
                    continue
                state = staff_greeting._official._debug_state(observation.track_token)
                travel = state.get("staff_travel_metres")
                reduction = state.get("distance_reduction_metres")
                toward = state.get("staff_toward_customer_metres")
                distance = state.get("distance_metres")
                sustained = state.get("sustained_seconds")
                if isinstance(travel, float):
                    maximum_debug["staff_travel_metres"] = max(
                        maximum_debug["staff_travel_metres"], travel
                    )
                if isinstance(reduction, float):
                    maximum_debug["distance_reduction_metres"] = max(
                        maximum_debug["distance_reduction_metres"], reduction
                    )
                if isinstance(toward, float):
                    maximum_debug["staff_toward_customer_metres"] = max(
                        maximum_debug["staff_toward_customer_metres"], toward
                    )
                if isinstance(distance, float):
                    current_minimum = maximum_debug["minimum_distance_metres"]
                    maximum_debug["minimum_distance_metres"] = (
                        distance
                        if current_minimum is None
                        else min(current_minimum, distance)
                    )
                if isinstance(sustained, float):
                    maximum_debug["sustained_seconds"] = max(
                        maximum_debug["sustained_seconds"], sustained
                    )

            if any(event.assessment == "confirmed" for event in greetings):
                final_decision = "confirmed"
            rendered = draw_behavior_area_overlay(frame, camera)
            rendered = adapter.render_debug_overlay(
                rendered, track_state_lines=track_state_lines
            )
            cv2.putText(
                rendered,
                f"PROFILE: controlled_demo | FINAL: {final_decision.upper()}",
                (24, height - 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            writer.write(rendered)
            frame_times_ms.append((time.perf_counter() - frame_started) * 1000.0)
            frames_processed += 1
    finally:
        capture.release()
        writer.release()

    wall_seconds = time.perf_counter() - wall_started
    confirmed = [event for event in greetings if event.assessment == "confirmed"]
    selected = confirmed[0] if confirmed else None
    details = selected.details if selected is not None else {}
    entry_keys = [entry.entry_key for entry in entries]
    after = {
        "staff_travel_metres": details.get(
            "staff_travel_metres", maximum_debug["staff_travel_metres"]
        ),
        "staff_toward_customer_metres": details.get(
            "staff_toward_customer_metres",
            maximum_debug["staff_toward_customer_metres"],
        ),
        "distance_reduction_metres": details.get(
            "distance_reduction_metres",
            maximum_debug["distance_reduction_metres"],
        ),
        "minimum_distance_metres": details.get(
            "minimum_distance_metres", maximum_debug["minimum_distance_metres"]
        ),
        "sustained_seconds": details.get(
            "sustained_seconds", maximum_debug["sustained_seconds"]
        ),
        "final_decision": final_decision,
    }
    summary = {
        "source": str(args.source.resolve()),
        "segment_seconds": [args.start_seconds, args.end_seconds],
        "profile": camera.monitor_config.greeting_profile,
        "source_fps": source_fps,
        "frames_processed": frames_processed,
        "processing_fps": frames_processed / wall_seconds if wall_seconds else 0.0,
        "average_frame_ms": float(np.mean(frame_times_ms)) if frame_times_ms else 0.0,
        "p95_frame_ms": float(np.percentile(frame_times_ms, 95)) if frame_times_ms else 0.0,
        "entry_event_count": len(entries),
        "greeting_confirmed_count": len(confirmed),
        "greeting_not_observed_count": sum(
            event.assessment == "not_observed" for event in greetings
        ),
        "greeting_events": [
            {
                "event_key": event.event_key,
                "assessment": event.assessment,
                "confidence": event.confidence,
                "details": event.details,
            }
            for event in greetings
        ],
        "duplicate_count": len(entry_keys) - len(set(entry_keys)),
        "calibration_status": camera.calibration_status,
        "after": after,
        "known_risk": "ordinary_blue_clothing_not_tested",
        "output_video": str(video_path.resolve()),
    }
    summary_path = args.output_dir / "video_a_controlled_demo_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    before = {
        "staff_travel_metres": 0.312,
        "distance_reduction_metres": 0.60,
        "minimum_distance_metres": 0.61,
        "sustained_seconds": 1.2,
        "final_decision": "not_confirmed",
    }
    comparison_path = args.output_dir / "before_after_comparison.md"
    comparison_path.write_text(
        "\n".join(
            [
                "# Controlled Demo Threshold Comparison",
                "",
                "| Metric | Before | After |",
                "|---|---:|---:|",
                *[
                    f"| {name} | {before[name]} | {after[name]} |"
                    for name in before
                ],
                "",
                "The before values are from the prior run of the same 25s-70s segment.",
                "The after values are produced by this run with `controlled_demo`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if confirmed else 2


if __name__ == "__main__":
    raise SystemExit(main())
