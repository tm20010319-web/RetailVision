"""Isolated two-item experiment using existing VLX interaction components.

Target bindings are process-local; shared toy/guitar defaults and weights remain
unchanged. Each VLX query is issued separately, as the prior probe showed that a
combined query can omit the smaller eye massager. No manual boxes/events enter.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = "D:/project/RetailVisionDev/tools/ffmpeg/bin" + os.pathsep + os.environ["PATH"]
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from agent import vlx_interaction as domain

HOME_TARGETS = (
    domain.Target("home_monitor", "家庭监控", "small home security camera"),
    domain.Target("ai_eye_massager", "AI眼睛按摩仪", "wearable eye massager"),
)


def bind_targets():
    # The analyzer's default argument was bound at definition time; explicitly
    # supply the new targets instead of relying on changing that default.
    from scripts import run_vlx_interaction_video as video
    from scripts import vlx_interaction_vision as vision

    class HomeAnalyzer(domain.InteractionAnalyzer):
        def __init__(self, config=None, targets=HOME_TARGETS):
            super().__init__(config, targets=targets)

    video.TARGETS = vision.TARGETS = HOME_TARGETS
    video.InteractionAnalyzer = HomeAnalyzer
    return video, vision


def wait_for_gpu():
    while True:
        probe = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True,
        )
        memory, utilization = map(int, probe.stdout.strip().split(","))
        if memory < 2000 and utilization < 10:
            return
        print(f"Waiting for idle GPU: {memory} MiB, {utilization}% utilization", flush=True)
        time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check", "infer", "prepare", "render"))
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--recorded-run", type=Path)
    parser.add_argument("--wait-for-gpu", action="store_true")
    args = parser.parse_args()
    if args.stage != "check" and args.output_dir is None:
        parser.error("--output-dir is required")
    video, vision = bind_targets()
    vision.cv2.setNumThreads(2)
    config, vlx = video.load_config(ROOT / "config/vlx_interaction_video.yaml")
    config["experiment_targets"] = [asdict(t) for t in HOME_TARGETS]
    config["recognition_query_mode"] = "one_query_per_target"
    if args.stage == "check":
        import json
        print(json.dumps(video.preflight(config, vlx), ensure_ascii=False, indent=2))
        assert set(video.InteractionAnalyzer().targets) == {t.key for t in HOME_TARGETS}
        return
    if args.wait_for_gpu and args.stage == "infer":
        wait_for_gpu()
    if args.stage == "infer":
        if args.video is None or not args.video.is_file():
            parser.error("--video must be an existing recording")

        class SeparateQueryVision(vision.VideoVision):
            def recognize(self, frame, sequence, timestamp):
                import cv2
                from PIL import Image
                import torch
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                proposals = self.proposer(image)
                combined = {"result_bbox_list": [], "per_target_outputs": []}
                with torch.inference_mode():
                    for target in HOME_TARGETS:
                        raw = self.worker.run_task(
                            image, "detection", target.phrase, lang="en",
                            bbox_list=proposals, temperature=0.0,
                            max_new_tokens=self.config["max_new_tokens"],
                        )
                        combined["per_target_outputs"].append({"query": target.phrase, "output": raw})
                        combined["result_bbox_list"].extend(raw.get("result_bbox_list", []))
                return combined, len(proposals)

        video.run_analysis(args.video.resolve(), args.output_dir.resolve(), config, vlx,
                           vision_factory=SeparateQueryVision)
    else:
        from scripts import vlx_mask_refinement as refine
        refine.TARGETS = HOME_TARGETS
        if args.stage == "prepare":
            if args.recorded_run is None:
                parser.error("--recorded-run is required")
            refine.prepare(args.recorded_run.resolve(), args.output_dir.resolve(), [0, 1])
        elif args.stage == "render":
            refine.render(args.output_dir.resolve())


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
