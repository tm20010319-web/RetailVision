"""Run the DPI-01C single-product real SAM3 Adapter probe on an Offline MP4."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.product_first_frame_initialization import frame_sha256, mask_sha256  # noqa: E402
from agent.sam31_real_adapter import create_external_sam31_adapter  # noqa: E402


def _probe_pts(video: Path, source_seq: int) -> tuple[int, int, int]:
    import subprocess
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=time_base:frame=best_effort_timestamp", "-of", "json", str(video)]
    payload = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    time_base = str(payload["streams"][0]["time_base"])
    numerator, denominator = (int(value) for value in time_base.split("/"))
    rows = payload.get("frames", [])
    return int(rows[source_seq]["best_effort_timestamp"]), numerator, denominator


def _decode(video: Path, source_seq: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {video}")
    try:
        for index in range(source_seq + 1):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"cannot decode source_seq={source_seq}")
            if index == source_seq:
                if frame.shape[:2] != (720, 1280):
                    frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
                return frame
    finally:
        capture.release()
    raise AssertionError("unreachable")


def _parse_bbox(value: str) -> tuple[int, int, int, int]:
    values = tuple(int(item) for item in value.split(","))
    if len(values) != 4:
        raise ValueError("--bbox must be x1,y1,x2,y2")
    return values


def _parse_points(values: Sequence[str]) -> tuple[tuple[int, int], ...]:
    result = []
    for value in values:
        x, y = (int(item) for item in value.split(","))
        result.append((x, y))
    return tuple(result)


def run(args: argparse.Namespace) -> int:
    frame = _decode(args.video, args.source_seq)
    source_pts, time_base_num, time_base_den = _probe_pts(args.video, args.source_seq)
    output_root = args.output_dir / "dpi01c_real_sam3_initialization"
    output_root.mkdir(parents=True, exist_ok=True)
    frame_digest = frame_sha256(frame)
    adapter = None
    payload: dict[str, object] = {
        "schema_version": "dpi-01c.real-sam3-probe.v1",
        "analysis_run_id": args.analysis_run_id,
        "video_path": args.video.name,
        "video_sha256": hashlib.sha256(args.video.read_bytes()).hexdigest(),
        "source_seq": args.source_seq,
        "source_pts": source_pts,
        "time_base": {"num": time_base_num, "den": time_base_den},
        "frame_sha256": frame_digest,
        "frame_size": [1280, 720],
        "obj_id": args.obj_id,
        "bbox": list(_parse_bbox(args.bbox)),
        "positive_points": [list(value) for value in _parse_points(args.positive_point)],
        "negative_points": [list(value) for value in _parse_points(args.negative_point)],
        "session_created": False,
        "session_closed": False,
        "adapter_status": "NOT_RUN",
        "failure_codes": [],
    }
    cv2.imwrite(str(output_root / "initialization_frame.jpg"), frame)
    try:
        adapter = create_external_sam31_adapter(
            frame=frame,
            runtime_root=args.sam_runtime_root,
            checkpoint_path=args.checkpoint_path,
        )
        payload["session_created"] = True
        result = adapter.add_instance_prompt(
            frame=frame,
            frame_index=0,
            bbox=_parse_bbox(args.bbox),
            points=_parse_points(args.positive_point) + _parse_points(args.negative_point),
            point_labels=(1,) * len(args.positive_point) + (0,) * len(args.negative_point),
            obj_id=args.obj_id,
        )
        payload.update({
            "sam_version": result.diagnostics.get("sam_version"),
            "session_id_ref": result.diagnostics.get("session_id_ref"),
            "raw_output_shape": result.diagnostics.get("raw_output_shape"),
            "candidate_count": result.diagnostics.get("candidate_count"),
            "mask_shape": None if not result.masks else list(result.masks[0].shape),
            "mask_area": None if not result.masks else int(np.count_nonzero(result.masks[0])),
            "mask_sha256": None if not result.masks else mask_sha256(result.masks[0]),
            "call_sequence": result.diagnostics.get("call_sequence"),
            "adapter_status": "PASS" if result.masks else "REJECTED",
            "failure_codes": [] if result.masks else [result.diagnostics.get("failure_code", "CANDIDATE_AMBIGUOUS")],
        })
        if result.masks:
            mask = result.masks[0].astype(np.uint8) * 255
            overlay = frame.copy()
            overlay[mask.astype(bool)] = (overlay[mask.astype(bool)] * 0.45 + np.asarray((0, 220, 255)) * 0.55).astype(np.uint8)
            cv2.rectangle(overlay, _parse_bbox(args.bbox)[:2], _parse_bbox(args.bbox)[2:], (0, 255, 255), 2)
            cv2.imwrite(str(output_root / "single_product_probe_overlay.jpg"), overlay)
    except Exception as error:
        payload["adapter_status"] = "BLOCKED"
        payload["failure_codes"] = ["REAL_SAM3_ADAPTER_ERROR"]
        payload["error_type"] = type(error).__name__
        payload["error"] = str(error)
        return_code = 2
    else:
        return_code = 0 if payload["adapter_status"] == "PASS" else 2
    finally:
        if adapter is not None:
            adapter.close()
            payload["session_closed"] = adapter.closed
        (output_root / "sam_adapter_probe.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return return_code


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--analysis-run-id", required=True)
    parser.add_argument("--source-seq", type=int, required=True)
    parser.add_argument("--bbox", required=True, help="x1,y1,x2,y2 in 1280x720 AI coordinates")
    parser.add_argument("--positive-point", action="append", required=True, help="x,y; repeatable")
    parser.add_argument("--negative-point", action="append", default=[], help="x,y; repeatable")
    parser.add_argument("--obj-id", type=int, default=1)
    parser.add_argument("--sam-runtime-root", type=Path, default=Path(r"D:\GJJ\work\sam3"))
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
