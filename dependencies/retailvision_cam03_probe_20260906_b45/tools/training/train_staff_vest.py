"""Validate and train the single-class staff-vest detector."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "staff_vest_train.yaml"
VALIDATOR = PROJECT_ROOT / "tools" / "training" / "validate_staff_vest_dataset.py"
REQUIRED_CONFIG = {
    "model": str,
    "task": str,
    "imgsz": int,
    "epochs": int,
    "patience": int,
    "seed": int,
    "project": str,
    "name": str,
}
IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "data" / "staff_vest_dataset",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--name", help="Experiment name override")
    parser.add_argument("--device", default="0", help="Ultralytics device, default: 0")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--min-box-pixels", type=float, default=8.0)
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"training config does not exist: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"cannot read training config: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("training config must contain a mapping")
    for name, expected_type in REQUIRED_CONFIG.items():
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, expected_type):
            raise ValueError(f"training config {name} must be {expected_type.__name__}")
    if payload["task"] != "detect":
        raise ValueError("training config task must be detect")
    for name in ("imgsz", "epochs", "patience"):
        if payload[name] < 1:
            raise ValueError(f"training config {name} must be positive")
    if not payload["model"].strip() or not payload["project"].strip() or not payload["name"].strip():
        raise ValueError("model, project, and name cannot be empty")
    return payload


def validate_dataset(
    dataset: Path, *, imgsz: int, min_box_pixels: float
) -> tuple[bool, dict[str, Any]]:
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            str(dataset),
            "--imgsz",
            str(imgsz),
            "--min-box-pixels",
            str(min_box_pixels),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"dataset validator returned invalid JSON: {error}") from error
    return result.returncode == 0, report


def metric(row: dict[str, str], name: str) -> float:
    try:
        return float(row[name])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"results.csv is missing numeric metric: {name}") from error


def build_training_summary(
    *,
    experiment_dir: Path,
    dataset: Path,
    config: dict[str, Any],
    device: str,
    wall_seconds: float,
) -> dict[str, Any]:
    pr_curve = next(
        (
            path
            for path in (
                experiment_dir / "BoxPR_curve.png",
                experiment_dir / "PR_curve.png",
            )
            if path.is_file()
        ),
        experiment_dir / "BoxPR_curve.png",
    )
    required = {
        "best_pt": experiment_dir / "weights" / "best.pt",
        "last_pt": experiment_dir / "weights" / "last.pt",
        "results_csv": experiment_dir / "results.csv",
        "training_curve": experiment_dir / "results.png",
        "pr_curve": pr_curve,
        "confusion_matrix": experiment_dir / "confusion_matrix.png",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    validation_predictions = sorted(experiment_dir.glob("val_batch*_pred.jpg"))
    if not validation_predictions:
        missing.append(str(experiment_dir / "val_batch*_pred.jpg"))
    if missing:
        raise ValueError(f"training completed without required artifacts: {missing}")

    with required["results_csv"].open(newline="", encoding="utf-8") as stream:
        rows = [
            {str(key).strip(): str(value).strip() for key, value in row.items()}
            for row in csv.DictReader(stream)
        ]
    if not rows:
        raise ValueError("results.csv contains no epoch rows")
    best = max(
        rows,
        key=lambda row: 0.1 * metric(row, "metrics/mAP50(B)")
        + 0.9 * metric(row, "metrics/mAP50-95(B)"),
    )

    from ultralytics import YOLO

    test_images = sorted(
        path
        for path in (dataset / "images" / "test").rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not test_images:
        raise ValueError("test split contains no image for inference benchmark")
    benchmark_model = YOLO(str(required["best_pt"]), task=config["task"])
    benchmark_results = benchmark_model.predict(
        str(test_images[0]),
        imgsz=config["imgsz"],
        device=device,
        verbose=False,
    )
    if not benchmark_results:
        raise ValueError("single-image inference benchmark returned no result")
    inference_ms = float(benchmark_results[0].speed.get("inference", 0.0))

    import torch
    import ultralytics

    return {
        "experiment_dir": str(experiment_dir.resolve()),
        "model": config["model"],
        "task": config["task"],
        "parameters": {
            key: config[key]
            for key in ("imgsz", "epochs", "patience", "seed")
        },
        "environment": {
            "python": sys.version.split()[0],
            "ultralytics": ultralytics.__version__,
            "torch": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "device": device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "metrics": {
            "precision": metric(best, "metrics/precision(B)"),
            "recall": metric(best, "metrics/recall(B)"),
            "map50": metric(best, "metrics/mAP50(B)"),
            "map50_95": metric(best, "metrics/mAP50-95(B)"),
            "best_epoch": int(metric(best, "epoch")) + 1,
        },
        "training": {
            "completed_epochs": len(rows),
            "csv_training_seconds": metric(rows[-1], "time"),
            "wall_seconds": round(wall_seconds, 3),
            "single_image_inference_ms": round(inference_ms, 3),
            "single_image_fps": round(1000 / inference_ms, 3)
            if inference_ms > 0
            else 0.0,
            "benchmark_image": str(test_images[0].resolve()),
        },
        "artifacts": {
            key: str(path.resolve()) for key, path in required.items()
        }
        | {
            "validation_predictions": [
                str(path.resolve()) for path in validation_predictions
            ]
        },
    }


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
        dataset_valid, validation_report = validate_dataset(
            args.dataset,
            imgsz=config["imgsz"],
            min_box_pixels=args.min_box_pixels,
        )
        if not dataset_valid:
            print(json.dumps(validation_report, ensure_ascii=True, indent=2))
            print("error: dataset validation failed; training was not started", file=sys.stderr)
            return 1
        experiment_name = args.name or config["name"]
        if (
            not experiment_name.strip()
            or Path(experiment_name).name != experiment_name
            or experiment_name in {".", ".."}
        ):
            raise ValueError("experiment name must be one non-empty path component")
        project_path = Path(config["project"])
        if not project_path.is_absolute():
            project_path = PROJECT_ROOT / project_path
        experiment_dir = project_path / experiment_name
        if experiment_dir.exists():
            raise ValueError(
                f"experiment directory already exists; refusing to overwrite: "
                f"{experiment_dir}"
            )
        train_args = {
            key: value for key, value in config.items() if key not in {"model", "task"}
        }
        train_args.update(
            {
                "data": str((args.dataset / "data.yaml").resolve()),
                "device": args.device,
                "name": experiment_name,
                "project": str(project_path.resolve()),
                "exist_ok": False,
            }
        )
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "dataset_validation": validation_report,
                        "model": config["model"],
                        "task": config["task"],
                        "train_args": train_args,
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return 0

        import torch

        if args.device.lower() != "cpu" and not torch.cuda.is_available():
            raise ValueError(
                "CUDA is unavailable; use the agents-py311 environment or explicitly pass "
                "--device cpu --allow-cpu"
            )
        if args.device.lower() == "cpu" and not args.allow_cpu:
            raise ValueError("CPU training requires explicit --allow-cpu")

        from ultralytics import YOLO

        training_started = time.perf_counter()
        model = YOLO(config["model"], task=config["task"])
        model.train(**train_args)
        summary = build_training_summary(
            experiment_dir=experiment_dir,
            dataset=args.dataset,
            config=config,
            device=args.device,
            wall_seconds=time.perf_counter() - training_started,
        )
        summary_path = experiment_dir / "training_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
