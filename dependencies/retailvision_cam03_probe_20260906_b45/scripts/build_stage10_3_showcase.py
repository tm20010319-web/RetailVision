"""Concatenate verified Stage 10.3 scenario videos into the final showcase."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.stage10_showcase import (
    concatenate_stage10_videos,
    extract_video_keyframes,
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = concatenate_stage10_videos(
        args.inputs,
        args.output,
        fps=args.fps,
        frame_size=(args.width, args.height),
    )
    keyframes = extract_video_keyframes(
        args.output,
        args.keyframes_dir or args.output.parent / "final_showcase_keyframes",
    )
    payload = {
        **result.to_dict(),
        "inputs": [str(path) for path in args.inputs],
        "keyframe_paths": [str(path) for path in keyframes],
        "events_synthesized": False,
        "stage11_entered": False,
    }
    manifest = args.manifest or args.output.with_suffix(".json")
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--keyframes-dir", type=Path)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
