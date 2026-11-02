"""Run the frozen Stage5/7C/8/9 analyzer on one real B2.8 RecordingFile."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.offline.offline_video_models import OfflineVideoAnalysisRequest
from agent.offline.offline_source import P6SRecordingSource
from scripts.run_offline_complete_loop_golden import (
    GoldenMp4Analyzer,
    _load_runtime_configs,
)


def _json_value(value):
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--job-id", default="b28-real-qualification")
    parser.add_argument("--run-id", default="b28-real-run")
    parser.add_argument("--started-at-utc", required=True)
    parser.add_argument("--ended-at-utc", required=True)
    parser.add_argument("--recording-start-local", required=True)
    parser.add_argument("--recording-end-local", required=True)
    parser.add_argument("--target-product", default="smart_toy_a")
    parser.add_argument("--sam-runtime-root", type=Path, default=Path(r"D:\GJJ\work\sam3"))
    args = parser.parse_args()

    started_at = datetime.fromisoformat(args.started_at_utc)
    ended_at = datetime.fromisoformat(args.ended_at_utc)
    recording_start_local = datetime.fromisoformat(args.recording_start_local)
    recording_end_local = datetime.fromisoformat(args.recording_end_local)
    request = OfflineVideoAnalysisRequest(
        analysis_job_id=args.job_id,
        analysis_run_id=args.run_id,
        video_path=args.video,
        store_id="store-1",
        camera_id="cam02",
        device_id="cam02",
        source_origin="p6s_recording_file",
        recording=P6SRecordingSource(
            device_id="cam02",
            path_name="/download/video/20260819",
            file_name=args.video.name,
            raw_started_at=recording_start_local,
            raw_ended_at=recording_end_local,
            indexed_size_bytes=args.video.stat().st_size,
        ),
        controlled_download=True,
        timezone_offset_minutes=480,
        timezone_source="p6s_cam02_verified_offset",
        timezone_verified=True,
        analysis_fps=5.0,
    )
    _store, _camera, association, interaction = _load_runtime_configs()
    analyzer = GoldenMp4Analyzer(
        store_id="store-1",
        camera_id="cam02",
        association=association,
        interaction=interaction,
        sam_runtime_root=args.sam_runtime_root,
        # The frozen SAM3 product runtime requires both logical product keys
        # during initialization.  Qualification still targets one product;
        # the output is checked below for exactly one complete target session
        # and no non-target behavior.
        candidate_assignments={"smart_toy_a": 0, "smart_toy_b": 1},
    )
    behavior = analyzer(request, started_at)
    output = {
        "analysis_job_id": request.analysis_job_id,
        "analysis_run_id": request.analysis_run_id,
        "video_path": str(args.video.resolve()),
        "video_started_at_utc": started_at,
        "video_ended_at_utc": ended_at,
        "media_timeline_status": behavior.media_timeline_status,
        "person_tracks": behavior.person_tracks,
        "product_tracks": behavior.product_tracks,
        "raw_stage9_events": behavior.raw_stage9_events,
        "behavior_summary": behavior.behavior_summary,
        "keyframe_paths": sorted(behavior.keyframes),
        "analyzed_video_bytes": len(behavior.analyzed_video_bytes),
        "target_product": args.target_product,
    }
    target_events = [
        item for item in behavior.raw_stage9_events
        if item.get("product_key") == args.target_product
    ]
    sessions = {}
    for item in target_events:
        sessions.setdefault(item.get("interaction_session_id"), []).append(
            item.get("event_type")
        )
    output["behavior_qualification"] = {
        "target_product": args.target_product,
        "session_count": len(sessions),
        "session_event_types": {
            str(key): list(value) for key, value in sessions.items()
        },
        "all_sessions_closed": all(
            tuple(value) == ("PICK_UP", "HOLDING", "PUT_DOWN")
            for value in sessions.values()
        ),
        "at_least_one_session": bool(sessions),
        "status": (
            "qualified"
            if sessions and all(
                tuple(value) == ("PICK_UP", "HOLDING", "PUT_DOWN")
                for value in sessions.values()
            )
            else "not_qualified"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_json_value(output), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    event_types = [item["event_type"] for item in behavior.raw_stage9_events]
    print(json.dumps({
        "person_track_rows": len(behavior.person_tracks),
        "product_track_rows": len(behavior.product_tracks),
        "event_types": event_types,
        "output": str(args.output.resolve()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
