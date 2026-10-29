"""Run DPI-01D with the frozen Offline Stage5/7C/8/9 windowed provider."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.product_camera_config import load_dpi01c_product_camera_config, validate_formal_replay_readiness
from agent.sam31_product_tracking import ProductDisplaySurface
from agent.offline_video_models import OfflineVideoAnalysisRequest
from agent.offline_source import P6SRecordingSource
from scripts.run_offline_complete_loop_golden import GoldenMp4Analyzer, _load_runtime_configs


def _config_payload(path: Path, config) -> tuple[dict[str, object], ProductDisplaySurface, dict[str, tuple[float, float]]]:
    raw = dict(config.raw)
    surface_raw = dict(raw["product_display_surface"])
    products = {
        key: {"product_type": config.products[key].tracking_product_type}
        for key in config.selected_product_keys
    }
    product_config = {
        "camera_id": config.camera_id,
        "frame_size": list(config.frame_size),
        "selected_product_keys": list(config.selected_product_keys),
        "products": products,
        "surface_overlap_min": 0.50,
        "product_track_lost_after_windows": config.product_track_lost_after_windows,
        "association_overlap_iou_min": 0.05,
        "association_geometry_distance_max": 0.20,
        "association_ambiguity_margin": 0.05,
        "product_display_surface": surface_raw,
    }
    surface = ProductDisplaySurface(tuple(config.product_display_surface.points))
    support_points = dict(config.support_contact_points)
    return product_config, surface, support_points


def _load_initialization_prompts(path: Path) -> dict[str, dict[str, object]]:
    mapping = json.loads((path / "initialization_mapping.json").read_text(encoding="utf-8"))
    diagnostic = json.loads((path / "initialization_diagnostic.json").read_text(encoding="utf-8"))
    targets = {
        str(item["product_key"]): item
        for item in diagnostic.get("diagnostics", {}).get("targets", [])
    }
    prompts: dict[str, dict[str, object]] = {}
    for item in mapping.get("assignments", []):
        key = str(item["product_key"])
        source = dict(item)
        source.update(targets.get(key, {}))
        positive = [tuple(int(value) for value in point) for point in source["positive_points"]]
        negative = [tuple(int(value) for value in point) for point in source["negative_points"]]
        prompts[key] = {
            "obj_id": int(item["candidate_id"]),
            "bbox_xyxy": tuple(int(value) for value in source["bbox_xyxy"]),
            "points": positive + negative,
            "point_labels": [1] * len(positive) + [0] * len(negative),
        }
    if len(prompts) != 2:
        raise RuntimeError("DPI-01D requires exactly two confirmed initialization prompts")
    return prompts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--camera-config", required=True, type=Path)
    parser.add_argument("--initialization-confirmation", required=True, type=Path)
    parser.add_argument("--initialization-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostics-root", type=Path)
    parser.add_argument("--sam-runtime-root", type=Path, default=Path(r"D:\GJJ\work\sam3"))
    parser.add_argument("--start-source-seq", type=int, default=1700)
    args = parser.parse_args()

    config = load_dpi01c_product_camera_config(args.camera_config)
    confirmation = json.loads(args.initialization_confirmation.read_text(encoding="utf-8"))
    confirmations = confirmation.get("confirmations", confirmation.get("products", {}))
    validate_formal_replay_readiness(config, confirmations)
    product_config, surface, support_points = _config_payload(args.camera_config, config)
    initialization_prompts = _load_initialization_prompts(args.initialization_dir)
    # The confirmed first-frame mapping is the operator's selection.  Passing
    # it into the frozen Stage7C provider keeps initialization non-interactive
    # during Offline replay; otherwise the legacy visual selector opens
    # cv2.waitKey() after the real Prompt has already produced the Masks.
    candidate_assignments = {
        key: int(prompt["obj_id"])
        for key, prompt in initialization_prompts.items()
    }
    _, _, association, interaction = _load_runtime_configs()
    analyzer = GoldenMp4Analyzer(
        store_id="store-1",
        camera_id=config.camera_id,
        association=association,
        interaction=interaction,
        sam_runtime_root=args.sam_runtime_root,
        candidate_assignments=candidate_assignments,
        selected_product_keys=config.selected_product_keys,
        product_config=product_config,
        product_surface=surface,
        display_evidence_mode=config.display_evidence_mode,
        support_contact_points=support_points,
        support_contact_tolerance=config.support_contact_tolerance,
        initialization_prompts=initialization_prompts,
        start_source_seq=args.start_source_seq,
        diagnostics_root=args.diagnostics_root,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    request = OfflineVideoAnalysisRequest(
        analysis_job_id="dpi01d-cam03-dual-guitar",
        analysis_run_id="dpi01d-cam03-windowed-replay",
        video_path=args.video,
        store_id="store-1",
        camera_id=config.camera_id,
        device_id=config.camera_id,
        source_origin="untrusted_import",
        recording=None,
        controlled_download=False,
        timezone_offset_minutes=0,
        timezone_source="offline_replay",
        timezone_verified=False,
        analysis_fps=5.0,
    )
    behavior = analyzer(request, now)
    output = {
        "camera_id": config.camera_id,
        "selected_product_keys": list(config.selected_product_keys),
        "display_evidence_mode": config.display_evidence_mode,
        "start_source_seq": args.start_source_seq,
        "source_video": str(args.video.resolve()),
        "source_sha256": __import__("hashlib").sha256(args.video.read_bytes()).hexdigest(),
        "behavior_summary": behavior.behavior_summary,
        "product_tracks": behavior.product_tracks,
        "raw_stage9_events": behavior.raw_stage9_events,
        "keyframe_paths": sorted(behavior.keyframes),
        "media_timeline_status": behavior.media_timeline_status,
    }
    if args.diagnostics_root is not None and behavior.analyzed_video_bytes:
        args.diagnostics_root.mkdir(parents=True, exist_ok=True)
        overlay_path = args.diagnostics_root / "full_replay_overlay.mp4"
        overlay_path.write_bytes(behavior.analyzed_video_bytes)
        output["full_replay_overlay_path"] = str(overlay_path.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=True, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "product_track_rows": len(behavior.product_tracks),
        "event_count": len(behavior.raw_stage9_events),
        "event_types": [item.get("event_type") for item in behavior.raw_stage9_events],
    }, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
