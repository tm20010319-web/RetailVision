"""Replay SAM 3.1 track_observations.csv through the existing BehaviorMonitor."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Optional

from agent.behavior_monitor import BehaviorMonitor
from agent.behavior_vision import load_behavior_camera_configs
from agent.sam31_behavior_adapter import Sam31BehaviorAdapter, Sam31BehaviorReplayRecord
from pipelines.behavior_runtime import BehaviorCameraRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--behavior-config", type=Path, required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--runtime-instance-id", required=True)
    parser.add_argument("--frame-width", type=int, required=True)
    parser.add_argument("--frame-height", type=int, required=True)
    parser.add_argument(
        "--flush-seconds",
        type=float,
        help="process one empty batch after EOF to exercise TRACK_LOST cleanup",
    )
    args = parser.parse_args()

    camera = next(
        (
            item
            for item in load_behavior_camera_configs(args.behavior_config)
            if item.camera_id == args.camera_id
        ),
        None,
    )
    if camera is None:
        raise SystemExit(f"camera is not configured: {args.camera_id}")

    monitor = BehaviorMonitor(
        camera_id=camera.camera_id,
        interaction_areas=camera.interaction_areas,
        config=camera.monitor_config,
    )
    runtime = BehaviorCameraRuntime(
        SimpleNamespace(runtime_id=args.runtime_instance_id),
        monitor,
    )
    adapter = Sam31BehaviorAdapter(
        store_id=camera.store_id,
        camera_id=camera.camera_id,
        runtime_instance_id=args.runtime_instance_id,
        frame_width=args.frame_width,
        frame_height=args.frame_height,
    )
    records = sorted(adapter.read_csv(args.csv_path), key=lambda item: item.source_seq)
    for output in replay_records(
        records,
        runtime=runtime,
        flush_seconds=args.flush_seconds,
    ):
        print(json.dumps(output, ensure_ascii=False))


def replay_records(
    records: Iterable[Sam31BehaviorReplayRecord],
    *,
    runtime: BehaviorCameraRuntime,
    flush_seconds: Optional[float] = None,
) -> Iterable[dict[str, object]]:
    records = sorted(records, key=lambda item: item.source_seq)
    last_source_seq: Optional[int] = None
    last_observed_at = None
    for _source_seq, frame_records in groupby(records, key=lambda item: item.source_seq):
        batch = tuple(frame_records)
        observed_at = batch[0].observed_at
        if any(item.observed_at != observed_at for item in batch[1:]):
            raise SystemExit("one source_seq must have one timestamp")
        entries, behaviors = runtime.process_observations(
            observed_at,
            tuple(item.observation for item in batch),
        )
        last_source_seq = batch[0].source_seq
        last_observed_at = observed_at
        yield _replay_output(
            source_seq=last_source_seq,
            observed_at=observed_at,
            track_tokens=[item.observation.track_token for item in batch],
            entries=entries,
            behaviors=behaviors,
            runtime=runtime,
        )
    if flush_seconds is not None:
        if flush_seconds <= 0:
            raise ValueError("flush_seconds must be positive")
        if last_observed_at is None:
            return
        flushed_at = last_observed_at + timedelta(seconds=flush_seconds)
        entries, behaviors = runtime.process_observations(flushed_at, ())
        output = _replay_output(
            source_seq=last_source_seq,
            observed_at=flushed_at,
            track_tokens=[],
            entries=entries,
            behaviors=behaviors,
            runtime=runtime,
        )
        output["flush"] = True
        yield output


def _replay_output(
    *,
    source_seq: Optional[int],
    observed_at: datetime,
    track_tokens: list[str],
    entries,
    behaviors,
    runtime: BehaviorCameraRuntime,
) -> dict[str, object]:
    zone_states, transitions = runtime.zone_runtime_diagnostics()
    return {
        "source_seq": source_seq,
        "timestamp": observed_at.isoformat(),
        "track_tokens": track_tokens,
        "entry_count": len(entries),
        "zone_states": [
            {
                "track_token": item.track_token,
                "area_id": item.area_id,
                "zone_state": item.zone_state.value,
                "dwell_seconds": item.dwell_seconds,
            }
            for item in zone_states
        ],
        "runtime_transitions": [
            {
                "transition": item.transition.value,
                "track_token": item.track_token,
                "area_id": item.area_id,
                "occurred_at": item.occurred_at.isoformat(),
                "dwell_seconds": item.dwell_seconds,
            }
            for item in transitions
        ],
        "behavior_events": [
            {
                "event_key": item.event_key,
                "event_type": item.event_type,
                "assessment": item.assessment,
                "track_token": item.customer_track_token,
                "area_id": item.area_id,
            }
            for item in behaviors
        ],
    }


if __name__ == "__main__":
    main()
