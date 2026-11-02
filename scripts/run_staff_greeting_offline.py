"""Run the production V1 Staff Greeting Core against an MP4 artifact-only."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.behavior.behavior_vision import BehaviorVisionAdapter
from agent.behavior.staff_greeting_evaluation import OfficialGreetingCore
from agent.behavior.staff_greeting_offline import (
    OfflineStaffGreetingRunner,
    OpenCVPTSReader,
    load_reception_camera_config,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--camera-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--person-model", type=Path, default=PROJECT_ROOT / "data/models/yolo11n.pt")
    parser.add_argument("--vest-model", type=Path, default=PROJECT_ROOT / "data/models/staff_vest.pt")
    parser.add_argument("--pose-model", type=Path, default=PROJECT_ROOT / "data/models/yolo11n-pose.pt")
    parser.add_argument("--person-imgsz", type=int, default=1280)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--overlay-language",
        choices=("zh-CN", "en"),
        default="zh-CN",
    )
    parser.add_argument("--overlay-brand", default="秒象科技")
    args = parser.parse_args()

    camera = load_reception_camera_config(args.camera_config, args.camera_id)
    for path in (args.source, args.person_model, args.vest_model, args.pose_model):
        if not path.is_file():
            raise SystemExit(f"required file does not exist: {path}")
    reader = OpenCVPTSReader(args.source, camera.calibration_image_size)
    adapter = BehaviorVisionAdapter(
        store_id=camera.store_id,
        camera_id=camera.camera_id,
        person_model_path=str(args.person_model),
        staff_vest_model_path=str(args.vest_model),
        pose_model_path=str(args.pose_model),
        person_inference_imgsz=args.person_imgsz,
        device=args.device,
        staff_vest_enabled=True,
        ground_projector=camera.ground_projector,
        entry_trigger=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        calibration_status=camera.calibration_status,
        calibration_image_size=camera.calibration_image_size,
        identity_resolver=lambda _track_id: None,
        runtime_id="offline-staff-greeting",
        overlay_language=args.overlay_language,
        overlay_brand=args.overlay_brand,
    )
    core = OfficialGreetingCore(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        config=camera.monitor_config,
        calibration_status=camera.calibration_status,
        ground_calibration_available=True,
    )
    result = OfflineStaffGreetingRunner(
        media_reader=reader,
        vision_adapter=adapter,
        greeting_core=core,
        camera=camera,
        output_dir=args.output_dir,
        overlay_language=args.overlay_language,
    ).run()
    print(result.manifest_path)
    return 0 if result.status == "FINALIZED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
