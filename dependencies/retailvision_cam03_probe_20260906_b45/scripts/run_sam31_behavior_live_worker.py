"""Consume live SAM 3.1 frame batches from stdin and run cam02 BehaviorMonitor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from agent.behavior_monitor import BehaviorMonitor
from agent.behavior_vision import load_behavior_camera_configs
from agent.sam31_behavior_adapter import Sam31BehaviorAdapter
from agent.sam31_live_behavior import (
    Sam31GlobalTrack,
    Sam31LiveBehaviorBridge,
    parse_observed_at,
)
from pipelines.behavior_runtime import BehaviorCameraRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--behavior-config", type=Path, required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--runtime-instance-id", required=True)
    parser.add_argument("--frame-width", type=int, required=True)
    parser.add_argument("--frame-height", type=int, required=True)
    parser.add_argument("--log-output", type=Path, required=True)
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
    if camera.calibration_image_size != (args.frame_width, args.frame_height):
        raise SystemExit(
            "live AI frame size must match calibration_image_size: "
            f"{camera.calibration_image_size}"
        )

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
    bridge = Sam31LiveBehaviorBridge(adapter=adapter, runtime=runtime)

    args.log_output.parent.mkdir(parents=True, exist_ok=True)
    with args.log_output.open("w", encoding="utf-8", buffering=1) as log:
        for line_number, line in enumerate(sys.stdin, start=1):
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                _validate_frame_size(message, args.frame_width, args.frame_height)
                result = bridge.process_batch(
                    observed_at=parse_observed_at(message["observed_at"]),
                    source_seq=_source_seq(message),
                    tracks=tuple(
                        Sam31GlobalTrack.from_mapping(item)
                        for item in message.get("tracks", [])
                    ),
                )
            except Exception as error:
                raise RuntimeError(f"invalid live batch on stdin line {line_number}") from error
            payload = result.to_dict()
            encoded = json.dumps(payload, ensure_ascii=False)
            log.write(encoded + "\n")
            _print_debug(payload)


def _source_seq(message: dict[str, object]) -> int:
    value = message.get("source_seq")
    if isinstance(value, bool):
        raise ValueError("source_seq must be an integer")
    try:
        return int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("source_seq must be an integer") from error


def _validate_frame_size(
    message: dict[str, object], expected_width: int, expected_height: int
) -> None:
    if message.get("frame_width") != expected_width or message.get(
        "frame_height"
    ) != expected_height:
        raise ValueError("SAM live batch does not use the configured AI frame size")


def _print_debug(payload: dict[str, object]) -> None:
    transition_by_token: dict[str, list[str]] = {}
    for item in payload["transitions"]:
        transition_by_token.setdefault(item["track_token"], []).append(item["transition"])
    if not payload["tracks"] and payload["transitions"]:
        for item in payload["transitions"]:
            print(
                f"[Behavior] Track #{item['global_track_id']} "
                f"token={item['track_token']} "
                f"Zone={item['area_id']} State=OUTSIDE "
                f"Dwell={item['dwell_seconds']:.1f}s "
                f"Transition={item['transition']}",
                flush=True,
            )
        return
    for track in payload["tracks"]:
        zones = track["zones"] or [{
            "area_id": "-", "zone_state": "outside", "dwell_seconds": 0.0
        }]
        for zone in zones:
            transitions = transition_by_token.get(track["track_token"], [])
            print(
                f"[Behavior] Track #{track['global_track_id']} "
                f"token={track['track_token']} "
                f"foot=({track['foot_point'][0]:.3f}, {track['foot_point'][1]:.3f}) "
                f"Zone={zone['area_id']} State={zone['zone_state'].upper()} "
                f"Dwell={zone['dwell_seconds']:.1f}s "
                f"Transition={','.join(transitions) if transitions else '-'}",
                flush=True,
            )


if __name__ == "__main__":
    main()
