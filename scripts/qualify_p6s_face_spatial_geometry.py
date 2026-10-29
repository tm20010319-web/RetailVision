"""Restricted diagnostic for comparing FaceReco BackgroundImage to a PTS frame.

This is intentionally a qualification tool, not a production association
pipeline. It never infers a transform from resolution alone. A transform is
reported only as a candidate for human review; the caller must provide the
fixed-scene correspondences and validation decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import av
import cv2


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frame_at_or_after(path: Path, target_pts: float):
    container = av.open(str(path))
    try:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            pts = float(frame.pts * stream.time_base)
            if pts >= target_pts:
                return frame, pts, stream.time_base.numerator, stream.time_base.denominator
    finally:
        container.close()
    raise RuntimeError("no_presentation_frame_at_or_after_target")


def _image_metadata(path: Path) -> dict:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError(f"image_decode_failed:{path}")
    height, width = image.shape[:2]
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "width": width,
        "height": height,
        "channels": 1 if image.ndim == 2 else image.shape[2],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", type=Path)
    parser.add_argument("--recording", required=True, type=Path)
    parser.add_argument("--target-pts", required=True, type=float)
    parser.add_argument("--primary-output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    frame, frame_pts, time_base_num, time_base_den = _frame_at_or_after(
        args.recording, args.target_pts
    )
    args.primary_output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.primary_output), frame.to_ndarray(format="bgr24")):
        raise RuntimeError("primary_frame_write_failed")

    recording_frame = {
        "path": str(args.primary_output),
        "sha256": _sha256(args.primary_output),
        "width": frame.width,
        "height": frame.height,
        "source_pts_target": args.target_pts,
        "source_pts_selected": frame_pts,
        "raw_pts": frame.pts,
        "time_base": f"{time_base_num}/{time_base_den}",
        "recording_path": str(args.recording),
        "recording_sha256": _sha256(args.recording),
    }
    background = None
    if args.background is not None and args.background.is_file():
        background = _image_metadata(args.background)

    result = {
        "qualification": "not_qualified",
        "reason": (
            "background_image_unavailable"
            if background is None
            else "fixed_scene_correspondences_required"
        ),
        "background_image": background,
        "recording_primary_frame": recording_frame,
        "field_of_view_match": "not_verified",
        "transform_type": None,
        "transform_parameters": None,
        "fixed_scene_correspondence_count": 0,
        "transform_reprojection_error": None,
        "face_bbox_mapping": "not_verified",
        "provenance_class": "RESTRICTED_AUDIT",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
