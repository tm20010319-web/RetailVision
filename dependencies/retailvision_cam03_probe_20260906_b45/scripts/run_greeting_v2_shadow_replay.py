"""Replay a real camera video through V1 and V2 Shadow without writing data."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path

import cv2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.behavior_monitor import BehaviorMonitor
from agent.behavior_events import BehaviorEventStore
from agent.behavior_vision import (
    GREETING_PROFILES,
    BehaviorVisionAdapter,
    load_behavior_camera_configs,
)
from agent.schema_evolution import evolve_schema
from agent.customer_entry_visit import CustomerEntryVisit
from agent.staff_greeting_evaluation import StaffGreetingEvaluation
from agent.staff_greeting_shadow_query import StaffGreetingShadowQuery
from pipelines.behavior_runtime import BehaviorCameraRuntime


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "behavior_cameras.example.json")
    parser.add_argument("--person-model", type=Path, default=PROJECT_ROOT / "outputs/models/yolo11n.pt")
    parser.add_argument("--vest-model", type=Path, default=PROJECT_ROOT / "runs/staff_vest/baseline_v1/weights/best.pt")
    parser.add_argument("--pose-model", type=Path, default=PROJECT_ROOT / "outputs/models/yolo11n-pose.pt")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--end-seconds", type=float, default=None)
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--greeting-profile",
        choices=tuple(GREETING_PROFILES),
        help="Override only the replay monitor profile; the camera config file is unchanged.",
    )
    parser.add_argument("--mock-staff-name", default="演示店员")
    return parser.parse_args()


def _json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main() -> int:
    args = arguments()
    for path in (args.source, args.config, args.person_model, args.vest_model, args.pose_model):
        if not path.is_file():
            raise FileNotFoundError(path)
    camera = load_behavior_camera_configs(args.config)[0]
    monitor_config = camera.monitor_config
    if args.greeting_profile is not None:
        monitor_config = replace(
            monitor_config,
            **GREETING_PROFILES[args.greeting_profile],
            greeting_profile=args.greeting_profile,
        )
    adapter = BehaviorVisionAdapter(
        store_id=camera.store_id, camera_id=camera.camera_id,
        person_model_path=str(args.person_model), staff_vest_model_path=str(args.vest_model),
        pose_model_path=str(args.pose_model), device=args.device, staff_vest_enabled=True,
        ground_projector=camera.ground_projector, entry_trigger=camera.entry_trigger,
        greeting_area=camera.greeting_area, calibration_status=camera.calibration_status,
        calibration_image_size=camera.calibration_image_size, runtime_id="greeting-v2-replay",
    )
    sessions = None
    event_store = None
    staff_greeting = None
    if args.database is not None:
        database = args.database.resolve()
        if database.exists():
            raise FileExistsError(f"refusing to overwrite replay database: {database}")
        database.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{database.as_posix()}")
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        event_store = BehaviorEventStore(
            sessions,
            greeting_mock_staff_name=args.mock_staff_name,
        )
    staff_greeting = StaffGreetingEvaluation(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        config=monitor_config,
        behavior_sink=event_store.record_behavior if event_store is not None else lambda _event: None,
        calibration_status=camera.calibration_status,
        ground_calibration_available=camera.ground_projector is not None,
        comparison_enabled=sessions is not None,
        session_factory=sessions,
    )
    customer_entry_visit = (
        CustomerEntryVisit(sessions) if sessions is not None else None
    )
    monitor = BehaviorMonitor(
        camera_id=camera.camera_id, entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area, config=monitor_config,
        calibration_status=camera.calibration_status,
        ground_calibration_available=camera.ground_projector is not None,
        behavior_sink=event_store.record_behavior if event_store is not None else None,
    )
    runtime = BehaviorCameraRuntime(
        adapter,
        monitor,
        staff_greeting=staff_greeting,
        customer_entry_visit=customer_entry_visit,
        entry_store_id=camera.store_id if customer_entry_visit is not None else None,
        entry_runtime_session_id=(
            adapter.runtime_id if customer_entry_visit is not None else None
        ),
    )
    capture = cv2.VideoCapture(str(args.source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {args.source}")
    capture.set(cv2.CAP_PROP_POS_MSEC, args.start_seconds * 1000)
    base = datetime(2026, 7, 31, 11, 47, 0)
    entries, official, shadow_results = [], [], []
    frames = 0
    try:
        while True:
            available, frame = capture.read()
            if not available:
                break
            seconds = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if args.end_seconds is not None and seconds > args.end_seconds:
                break
            observed_at = base + timedelta(seconds=seconds)
            frame_entries, frame_behaviors = runtime.process_frame(frame, observed_at)
            entries.extend(frame_entries)
            official.extend(event for event in frame_behaviors if event.event_type == "staff_greeting")
            frames += 1
    finally:
        capture.release()
    cancellations = runtime.cancel_greetings(
        base + timedelta(seconds=args.end_seconds or seconds), "replay_finished"
    )
    stored_shadow = (
        StaffGreetingShadowQuery(sessions).recent_shadow(limit=100)
        if sessions is not None
        else None
    )
    serialized_shadow = stored_shadow if stored_shadow is not None else [asdict(item) for item in shadow_results]
    summary = {
        "frames": frames,
        "entries": [entry.entry_key for entry in entries],
        "official_greetings": [
            {
                "event_key": event.event_key,
                "assessment": event.assessment,
                "is_demo": event.is_demo,
                "details": event.details,
            }
            for event in official
        ],
        "official_cancellations": [asdict(item) for item in cancellations],
        "shadow": serialized_shadow,
        "stored_shadow": stored_shadow,
    }
    print(json.dumps(summary, ensure_ascii=False, default=_json, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
