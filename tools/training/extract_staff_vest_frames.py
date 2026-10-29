"""Extract auditable staff-vest training frames from a video."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Source video path")
    parser.add_argument("output_dir", type=Path, help="Directory for JPEG frames")
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--blur-threshold", type=float, default=80.0)
    parser.add_argument("--similarity-distance", type=int, default=4)
    parser.add_argument("--skip-blurry", action="store_true")
    parser.add_argument("--skip-similar", action="store_true")
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--prefix", help="Output prefix; defaults to the video stem")
    parser.add_argument("--report", type=Path, help="Optional new JSON report path")
    return parser.parse_args()


def difference_hash(frame) -> int:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    value = 0
    for row in resized[:, :-1] > resized[:, 1:]:
        for bit in row:
            value = (value << 1) | int(bit)
    return value


def validate_args(args: argparse.Namespace) -> None:
    if not args.video.is_file():
        raise ValueError(f"video does not exist: {args.video}")
    if args.interval_seconds <= 0:
        raise ValueError("--interval-seconds must be greater than zero")
    if args.max_frames is not None and args.max_frames < 1:
        raise ValueError("--max-frames must be at least one")
    if args.blur_threshold < 0:
        raise ValueError("--blur-threshold cannot be negative")
    if not 0 <= args.similarity_distance <= 64:
        raise ValueError("--similarity-distance must be between 0 and 64")
    if not 1 <= args.jpeg_quality <= 100:
        raise ValueError("--jpeg-quality must be between 1 and 100")
    if args.report is not None and args.report.exists():
        raise ValueError(f"report already exists and will not be overwritten: {args.report}")


def extract(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {args.video}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if fps <= 0:
        capture.release()
        raise ValueError("video does not report a usable FPS")

    interval_frames = max(1, round(fps * args.interval_seconds))
    prefix = args.prefix or args.video.stem
    report: dict[str, Any] = {
        "video": str(args.video.resolve()),
        "output_dir": str(args.output_dir.resolve()),
        "fps": fps,
        "interval_seconds": args.interval_seconds,
        "interval_frames": interval_frames,
        "selected": 0,
        "saved": 0,
        "skipped_existing": 0,
        "skipped_blurry": 0,
        "skipped_similar": 0,
        "blurry_frames": [],
        "similar_pairs": [],
    }
    previous_hash: int | None = None
    previous_name: str | None = None
    frame_index = 0
    next_sample = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index < next_sample:
                frame_index += 1
                continue
            if args.max_frames is not None and report["selected"] >= args.max_frames:
                break
            next_sample += interval_frames
            report["selected"] += 1
            timestamp_ms = round(frame_index * 1000 / fps)
            filename = f"{prefix}_t{timestamp_ms:010d}.jpg"
            target = args.output_dir / filename
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            is_blurry = blur_score < args.blur_threshold
            if is_blurry:
                report["blurry_frames"].append(
                    {"file": filename, "score": round(blur_score, 3)}
                )

            frame_hash = difference_hash(frame)
            is_similar = False
            if previous_hash is not None and previous_name is not None:
                distance = (frame_hash ^ previous_hash).bit_count()
                if distance <= args.similarity_distance:
                    is_similar = True
                    report["similar_pairs"].append(
                        {
                            "left": previous_name,
                            "right": filename,
                            "hamming_distance": distance,
                        }
                    )
            previous_hash = frame_hash
            previous_name = filename

            if target.exists():
                report["skipped_existing"] += 1
            elif is_blurry and args.skip_blurry:
                report["skipped_blurry"] += 1
            elif is_similar and args.skip_similar:
                report["skipped_similar"] += 1
            else:
                saved = cv2.imwrite(
                    str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality]
                )
                if not saved:
                    raise OSError(f"failed to write frame: {target}")
                report["saved"] += 1
            frame_index += 1
    finally:
        capture.release()

    report["blurry_count"] = len(report["blurry_frames"])
    report["similar_pair_count"] = len(report["similar_pairs"])
    return report


def main() -> int:
    try:
        args = parse_args()
        report = extract(args)
        output = json.dumps(report, ensure_ascii=True, indent=2)
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as stream:
                stream.write(output + "\n")
        print(output)
        return 0
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
