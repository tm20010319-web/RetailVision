"""Generate the staff-vest baseline report and demo-readiness decision."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


MANUAL_CHECKS = {
    "empty_blue_background_no_persistent_false_positive": "无人蓝色背景无持续误报",
    "ordinary_customer_no_persistent_false_positive": "普通顾客无持续误报",
    "ordinary_blue_clothes_no_persistent_false_positive": "普通蓝色衣服无持续误报",
    "main_vest_poses_stable": "正面、侧面、背面和遮挡等主要姿态稳定检测",
    "five_frame_three_hit_continuity": "检测连续性支持 5 帧窗口至少 3 帧命中",
    "no_persistent_blue_wall_false_positive": "蓝墙、门框、灯带、立柱和柜体无持续误报",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("runs/staff_vest/baseline_v1"),
    )
    parser.add_argument(
        "--dataset-report",
        type=Path,
        default=Path("data/staff_vest_dataset/validation_report.json"),
    )
    parser.add_argument(
        "--video-stats",
        type=Path,
        default=Path("outputs/staff_vest_test_result.json"),
    )
    parser.add_argument(
        "--manual-review",
        type=Path,
        default=Path("reports/staff_vest_baseline_v1_manual_review.json"),
    )
    parser.add_argument("--threshold-scan", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/staff_vest_baseline_v1.md"),
    )
    parser.add_argument("--recommended-threshold", type=float, default=0.25)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def load_json(path: Path, description: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{description} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {description}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{description} must contain a JSON object")
    return payload


def number(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "unavailable"


def make_report(args: argparse.Namespace) -> tuple[str, str]:
    if not 0 <= args.recommended_threshold <= 1:
        raise ValueError("--recommended-threshold must be between 0 and 1")
    summary = load_json(args.run_dir / "training_summary.json", "training summary")
    dataset = load_json(args.dataset_report, "dataset validation report")
    video = load_json(args.video_stats, "independent video stats")
    manual = load_json(args.manual_review, "manual review")
    threshold_scan = (
        load_json(args.threshold_scan, "threshold scan")
        if args.threshold_scan is not None
        else {}
    )

    failures: list[str] = []
    artifact_paths = summary.get("artifacts", {})
    for key in ("best_pt", "last_pt"):
        path = Path(str(artifact_paths.get(key, "")))
        if not path.is_file():
            failures.append(f"required artifact is missing: {key} -> {path}")
    if int(video.get("total_frames", 0)) < 1:
        failures.append("independent video produced no processed frames")
    for key, label in MANUAL_CHECKS.items():
        if manual.get(key) is not True:
            failures.append(f"manual review not passed: {label}")
    status = "READY_FOR_DEMO" if not failures else "NOT_READY_FOR_DEMO"

    metrics = summary.get("metrics", {})
    test_metrics = summary.get("test_metrics", {})
    training = summary.get("training", {})
    environment = summary.get("environment", {})
    parameters = summary.get("parameters", {})
    data_summary = dataset.get("summary", {})
    splits = dataset.get("splits", {})
    hard_cases = video.get("hard_cases", {})
    lines = [
        "# Staff vest baseline v1",
        "",
        f"## Decision: {status}",
        "",
        "The decision requires training metrics, an independent-video run, and every manual review item. Metrics alone never produce READY_FOR_DEMO.",
        "",
        "## Dataset",
        "",
        "| Item | Value |",
        "| --- | ---: |",
        f"| Images | {data_summary.get('images', 'unavailable')} |",
        f"| Staff-vest boxes | {data_summary.get('boxes', 'unavailable')} |",
        f"| Negative images / empty labels | {data_summary.get('empty_labels', 'unavailable')} |",
        f"| Train images | {splits.get('train', {}).get('images', 'unavailable')} |",
        f"| Validation images | {splits.get('val', {}).get('images', 'unavailable')} |",
        f"| Test images | {splits.get('test', {}).get('images', 'unavailable')} |",
        "",
        "## Training",
        "",
        "| Parameter | Value |",
        "| --- | --- |",
        f"| Model task | YOLO11n Detection / staff_vest |",
        f"| Image size | {parameters.get('imgsz', 'unavailable')} |",
        f"| Epochs configured | {parameters.get('epochs', 'unavailable')} |",
        f"| Patience | {parameters.get('patience', 'unavailable')} |",
        f"| Seed | {parameters.get('seed', 'unavailable')} |",
        f"| Completed epochs | {training.get('completed_epochs', 'unavailable')} |",
        f"| Best epoch | {metrics.get('best_epoch', 'unavailable')} |",
        f"| Wall training time (s) | {number(training.get('wall_seconds'), 3)} |",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Precision | {number(metrics.get('precision'))} |",
        f"| Recall | {number(metrics.get('recall'))} |",
        f"| mAP50 | {number(metrics.get('map50'))} |",
        f"| mAP50-95 | {number(metrics.get('map50_95'))} |",
        f"| Test Precision | {number(test_metrics.get('precision'))} |",
        f"| Test Recall | {number(test_metrics.get('recall'))} |",
        f"| Test mAP50 | {number(test_metrics.get('map50'))} |",
        f"| Test mAP50-95 | {number(test_metrics.get('map50_95'))} |",
        f"| Single-image inference (ms) | {number(training.get('single_image_inference_ms'), 3)} |",
        f"| Single-image FPS | {number(training.get('single_image_fps'), 3)} |",
        "",
        "## Environment",
        "",
        f"- Python: `{environment.get('python', 'unavailable')}`",
        f"- Ultralytics: `{environment.get('ultralytics', 'unavailable')}`",
        f"- PyTorch: `{environment.get('torch', 'unavailable')}`",
        f"- CUDA available: `{environment.get('cuda_available', 'unavailable')}`",
        f"- GPU: `{environment.get('gpu', 'unavailable')}`",
        "",
        "## Independent video",
        "",
        f"- Source: `{video.get('source', 'unavailable')}`",
        f"- Rendered output: `{video.get('output', 'unavailable')}`",
        f"- Total frames: {video.get('total_frames', 'unavailable')}",
        f"- Detection frames: {video.get('detection_frames', 'unavailable')}",
        f"- Detections: {video.get('detections', 'unavailable')}",
        f"- Average confidence: {number(video.get('average_confidence'))}",
        f"- Processing FPS: {number(video.get('processing_fps'), 3)}",
        f"- Longest detection run (frames): {video.get('longest_detection_run_frames', 'unavailable')}",
        f"- 5-frame windows with at least 3 hits: {video.get('five_frame_windows_with_three_hits', 'unavailable')} / {video.get('five_frame_windows', 'unavailable')}",
        f"- 5-frame / 3-hit ratio: {number(video.get('five_frame_three_hit_ratio'))}",
        f"- Suspected false positives: {video.get('suspected_false_positives', 'unavailable')}",
        f"- Suspected false negatives: {video.get('suspected_false_negatives', 'unavailable')}",
        "",
        "Heuristic false-positive/false-negative candidates are review queues, not ground truth.",
        "",
        "## Hard cases",
        "",
    ]
    for category in (
        "false_positive",
        "false_negative",
        "low_confidence",
        "occlusion",
        "far_distance",
        "blue_background",
        "blue_clothes",
    ):
        lines.append(f"- {category}: {hard_cases.get(category, 0)} exported frames")
    recommended_scan = threshold_scan.get("thresholds", {}).get(
        f"{args.recommended_threshold:.2f}",
        threshold_scan.get("thresholds", {}).get(str(args.recommended_threshold), {}),
    )
    lines.extend(
        [
            "",
            "## Threshold selection",
            "",
            "- Validation F1 curve optimum: `0.681`.",
            f"- Recommended threshold: `{args.recommended_threshold:.2f}`.",
            f"- Independent-video detection frames at recommended threshold: {recommended_scan.get('detection_frames', 'unavailable')}.",
            f"- Independent-video 5-frame / 3-hit ratio at recommended threshold: {number(recommended_scan.get('five_frame_three_hit_ratio'))}.",
        ]
    )
    lines.extend(["", "## Manual review", ""])
    for key, label in MANUAL_CHECKS.items():
        result = "PASS" if manual.get(key) is True else "FAIL/NOT CHECKED"
        lines.append(f"- {result}: {label}")
    lines.extend(
        [
            f"- Notes: {manual.get('notes', 'None')}",
            "",
            "## Failure cases",
            "",
        ]
    )
    if failures:
        lines.extend(f"- {failure}" for failure in failures)
    else:
        lines.append("- No readiness-blocking failure recorded.")
    lines.extend(["", "## Artifacts", ""])
    for name, value in artifact_paths.items():
        if isinstance(value, list):
            for path in value:
                lines.append(f"- {name}: `{path}`")
        else:
            lines.append(f"- {name}: `{value}`")
    lines.extend(
        [
            "",
            "## Runtime recommendation",
            "",
            f"- Recommended vest confidence threshold: `{args.recommended_threshold:.2f}`",
            f"- Best weights: `{artifact_paths.get('best_pt', 'unavailable')}`",
            "- Keep the person-tracking model separate; this weight supplies vest boxes only.",
            "- Preserve the existing 5-frame / 3-hit role window during the later Runtime integration.",
            "",
            "## Next iteration",
            "",
            "Review exported hard cases before changing epochs. Correct labels and add far-distance, side/back, occlusion, blue-background, and blue-clothing examples where the review shows failures.",
            "",
        ]
    )
    return "\n".join(lines), status


def main() -> int:
    args = parse_args()
    try:
        if args.output.exists() and not args.force:
            raise ValueError(
                f"report already exists; pass --force to replace it: {args.output}"
            )
        report, status = make_report(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(json.dumps({"report": str(args.output.resolve()), "status": status}))
        return 0
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
