"""Evaluate one recorded Stage 10.3 A-F scenario without synthesizing events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.stage10_live_acceptance import evaluate_stage10_3_artifacts
from agent.stage10_showcase import extract_video_keyframes


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    overlay = args.overlay or args.artifact_dir / "overlay.mp4"
    keyframe_root = args.keyframes_dir or args.artifact_dir / "keyframes"
    keyframe_paths = tuple(sorted(keyframe_root.glob("*.jpg")))
    if not keyframe_paths and not args.skip_keyframe_extraction:
        keyframe_paths = extract_video_keyframes(overlay, keyframe_root)
    result = evaluate_stage10_3_artifacts(
        args.scenario,
        artifact_dir=args.artifact_dir,
        expected_product_key=args.expected_product_key,
        expected_actor_track_token=args.expected_actor_track_token,
        overlay_path=overlay,
        keyframe_paths=keyframe_paths,
    )
    output = args.output or args.artifact_dir / "stage10_3_acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.result == "PASS" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=tuple("ABCDEF"))
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-product-key")
    parser.add_argument("--expected-actor-track-token")
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--keyframes-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-keyframe-extraction",
        action="store_true",
        help="Do not extract review frames when the keyframes directory is empty",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
