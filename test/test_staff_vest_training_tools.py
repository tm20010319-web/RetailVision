from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_SCRIPT = PROJECT_ROOT / "tools" / "training" / "extract_staff_vest_frames.py"
VALIDATE_SCRIPT = PROJECT_ROOT / "tools" / "training" / "validate_staff_vest_dataset.py"
TRAIN_SCRIPT = PROJECT_ROOT / "tools" / "training" / "train_staff_vest.py"
PREDICT_SCRIPT = PROJECT_ROOT / "tools" / "training" / "predict_staff_vest.py"
REPORT_SCRIPT = PROJECT_ROOT / "tools" / "training" / "report_staff_vest.py"


def run_script(script: Path, *arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(value) for value in arguments)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 80), color).save(path)


def write_dataset_yaml(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "data.yaml").write_text(
        yaml.safe_dump(
            {
                "path": str(root),
                "train": "images/train",
                "val": "images/val",
                "test": "images/test",
                "names": {0: "staff_vest"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def write_valid_dataset(root: Path) -> None:
    write_dataset_yaml(root)
    for split, color in (
        ("train", (20, 40, 80)),
        ("val", (40, 80, 20)),
        ("test", (80, 20, 40)),
    ):
        write_image(root / f"images/{split}/{split}.jpg", color)
        label = root / f"labels/{split}/{split}.txt"
        label.parent.mkdir(parents=True, exist_ok=True)
        label.write_text(
            "0 0.5 0.5 0.4 0.4\n" if split == "train" else "",
            encoding="utf-8",
        )


class StaffVestTrainingToolTests(unittest.TestCase):
    def test_extract_cli_samples_at_interval_and_never_overwrites_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "store camera.avi"
            output = root / "frames"
            writer = cv2.VideoWriter(
                str(video),
                cv2.VideoWriter_fourcc(*"MJPG"),
                10.0,
                (64, 48),
            )
            self.assertTrue(writer.isOpened())
            for index in range(10):
                writer.write(np.full((48, 64, 3), index * 10, dtype=np.uint8))
            writer.release()

            first = run_script(
                EXTRACT_SCRIPT,
                video,
                output,
                "--interval-seconds",
                0.2,
                "--max-frames",
                3,
                "--blur-threshold",
                1,
            )
            before = {path.name: path.read_bytes() for path in output.glob("*.jpg")}
            second = run_script(
                EXTRACT_SCRIPT,
                video,
                output,
                "--interval-seconds",
                0.2,
                "--max-frames",
                3,
                "--blur-threshold",
                1,
            )

            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(3, len(before))
            self.assertEqual(before, {path.name: path.read_bytes() for path in output.glob("*.jpg")})
            self.assertEqual(3, json.loads(second.stdout)["skipped_existing"])

    def test_validate_cli_rejects_missing_positive_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            write_dataset_yaml(root)
            write_image(root / "images/train/positive.jpg", (0, 0, 255))
            for split, color in (("val", (0, 255, 0)), ("test", (255, 0, 0))):
                write_image(root / f"images/{split}/negative.jpg", color)
                label = root / f"labels/{split}/negative.txt"
                label.parent.mkdir(parents=True, exist_ok=True)
                label.write_text("", encoding="utf-8")

            result = run_script(VALIDATE_SCRIPT, root)
            report = json.loads(result.stdout)

            self.assertNotEqual(0, result.returncode)
            self.assertEqual(1, report["summary"]["missing_labels"])
            self.assertTrue(any("positive.jpg" in error for error in report["errors"]))

    def test_validate_cli_accepts_dataset_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            write_valid_dataset(root)

            result = run_script(VALIDATE_SCRIPT, "--dataset", root)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])

    def test_validate_cli_rejects_duplicate_content_across_splits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            write_dataset_yaml(root)
            for split in ("train", "val", "test"):
                image = root / f"images/{split}/{split}.jpg"
                write_image(image, (40, 50, 60))
                label = root / f"labels/{split}/{split}.txt"
                label.parent.mkdir(parents=True, exist_ok=True)
                label.write_text(
                    "0 0.5 0.5 0.4 0.4\n" if split == "train" else "",
                    encoding="utf-8",
                )

            result = run_script(VALIDATE_SCRIPT, root)
            report = json.loads(result.stdout)

            self.assertNotEqual(0, result.returncode)
            self.assertGreaterEqual(report["summary"]["cross_split_leaks"], 1)
            self.assertTrue(any("duplicate" in error.lower() for error in report["errors"]))

    def test_validate_cli_rejects_invalid_boxes_and_damaged_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            write_valid_dataset(root)
            (root / "labels/train/train.txt").write_text(
                "1 0.5 0.5 0.4 0.4\n"
                "0 1.1 0.5 0.4 0.4\n"
                "0 0.5 0.5 0.001 0.001\n",
                encoding="utf-8",
            )
            damaged = root / "images/train/damaged.jpg"
            damaged.write_text("not an image", encoding="utf-8")

            result = run_script(VALIDATE_SCRIPT, root)
            report = json.loads(result.stdout)
            errors = "\n".join(report["errors"])

            self.assertNotEqual(0, result.returncode)
            self.assertEqual(1, report["summary"]["damaged_images"])
            self.assertIn("class id must be 0", errors)
            self.assertIn("normalized to [0, 1]", errors)
            self.assertIn("target is too small", errors)

    def test_validate_cli_rejects_capture_batch_split_leakage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            write_valid_dataset(root)
            entries = []
            for split in ("train", "val", "test"):
                entries.append(
                    {
                        "dataset_image": f"images/{split}/{split}.jpg",
                        "split": split,
                        "batch_id": "shared-batch" if split != "test" else "test-batch",
                    }
                )
            (root / "split_manifest.json").write_text(
                json.dumps({"entries": entries}),
                encoding="utf-8",
            )

            result = run_script(VALIDATE_SCRIPT, root)
            report = json.loads(result.stdout)

            self.assertNotEqual(0, result.returncode)
            self.assertEqual(1, report["summary"]["cross_split_leaks"])
            self.assertTrue(
                any("capture batch crosses" in error for error in report["errors"])
            )

    def test_train_cli_dry_run_requires_valid_dataset_before_loading_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            write_dataset_yaml(dataset)
            write_image(dataset / "images/train/positive.jpg", (20, 40, 80))
            config = root / "train.yaml"
            config.write_text(
                yaml.safe_dump(
                    {
                        "model": "missing-model.pt",
                        "task": "detect",
                        "imgsz": 640,
                        "epochs": 80,
                        "patience": 20,
                        "seed": 42,
                        "project": str(root / "runs" / "staff_vest"),
                        "name": "baseline_v1",
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            result = run_script(
                TRAIN_SCRIPT,
                "--dataset",
                dataset,
                "--config",
                config,
                "--dry-run",
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("dataset validation failed", result.stderr.lower())
            self.assertFalse((root / "runs" / "staff_vest").exists())

    def test_train_cli_dry_run_reports_validated_arguments_as_one_json_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            write_valid_dataset(dataset)
            config = root / "train.yaml"
            config.write_text(
                yaml.safe_dump(
                    {
                        "model": "not-loaded-during-dry-run.pt",
                        "task": "detect",
                        "imgsz": 640,
                        "epochs": 80,
                        "patience": 20,
                        "seed": 42,
                        "project": str(root / "runs" / "staff_vest"),
                        "name": "baseline_v1",
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            result = run_script(
                TRAIN_SCRIPT,
                "--dataset",
                dataset,
                "--config",
                config,
                "--dry-run",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["dataset_validation"]["valid"])
            self.assertEqual(80, report["train_args"]["epochs"])
            self.assertEqual("not-loaded-during-dry-run.pt", report["model"])

    def test_train_cli_name_override_refuses_existing_experiment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            write_valid_dataset(dataset)
            project = root / "runs" / "staff_vest"
            config = root / "train.yaml"
            config.write_text(
                yaml.safe_dump(
                    {
                        "model": "not-loaded-during-dry-run.pt",
                        "task": "detect",
                        "imgsz": 640,
                        "epochs": 80,
                        "patience": 20,
                        "seed": 42,
                        "project": str(project),
                        "name": "from_config",
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            fresh = run_script(
                TRAIN_SCRIPT,
                "--dataset",
                dataset,
                "--config",
                config,
                "--name",
                "baseline_v1",
                "--dry-run",
            )
            project.joinpath("baseline_v1").mkdir(parents=True)
            existing = run_script(
                TRAIN_SCRIPT,
                "--dataset",
                dataset,
                "--config",
                config,
                "--name",
                "baseline_v1",
                "--dry-run",
            )

            self.assertEqual(0, fresh.returncode, fresh.stderr)
            self.assertEqual("baseline_v1", json.loads(fresh.stdout)["train_args"]["name"])
            self.assertNotEqual(0, existing.returncode)
            self.assertIn("already exists", existing.stderr.lower())

    def test_train_cli_accepts_box_prefixed_pr_curve_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            write_valid_dataset(dataset)
            project = root / "runs" / "staff_vest"
            config = root / "train.yaml"
            config.write_text(
                yaml.safe_dump(
                    {
                        "model": "fake-data/models/yolo11n.pt",
                        "task": "detect",
                        "imgsz": 640,
                        "epochs": 1,
                        "patience": 1,
                        "seed": 42,
                        "project": str(project),
                        "name": "artifact_contract",
                        "save": True,
                        "plots": True,
                        "val": True,
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            fake_package = root / "fake_modules" / "ultralytics"
            fake_package.mkdir(parents=True)
            fake_package.joinpath("__init__.py").write_text(
                """
from pathlib import Path
__version__ = '8.4.fake'

class Result:
    speed = {'inference': 2.0}

class YOLO:
    def __init__(self, path, task=None):
        pass
    def train(self, **kwargs):
        run = Path(kwargs['project']) / kwargs['name']
        (run / 'weights').mkdir(parents=True)
        for name in ('weights/best.pt', 'weights/last.pt', 'results.png', 'BoxPR_curve.png', 'confusion_matrix.png', 'val_batch0_pred.jpg'):
            (run / name).write_bytes(b'artifact')
        (run / 'results.csv').write_text(
            'epoch,time,metrics/precision(B),metrics/recall(B),metrics/mAP50(B),metrics/mAP50-95(B)\\n'
            '0,1.5,0.8,0.7,0.75,0.5\\n', encoding='utf-8')
    def predict(self, source, **kwargs):
        return [Result()]
""".strip()
                + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root / "fake_modules")

            result = subprocess.run(
                [
                    sys.executable,
                    str(TRAIN_SCRIPT),
                    "--dataset",
                    str(dataset),
                    "--config",
                    str(config),
                    "--name",
                    "artifact_contract",
                    "--device",
                    "cpu",
                    "--allow-cpu",
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            summary = json.loads(
                project.joinpath("artifact_contract/training_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(summary["artifacts"]["pr_curve"].endswith("BoxPR_curve.png"))

    def test_predict_cli_writes_annotated_video_stats_and_hard_case_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "independent test.avi"
            output = root / "result.mp4"
            stats = root / "result.json"
            hard_cases = root / "hard_cases"
            writer = cv2.VideoWriter(
                str(video),
                cv2.VideoWriter_fourcc(*"MJPG"),
                5.0,
                (64, 48),
            )
            self.assertTrue(writer.isOpened())
            for _ in range(3):
                writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
            writer.release()

            fake_package = root / "fake_modules" / "ultralytics"
            fake_package.mkdir(parents=True)
            fake_package.joinpath("__init__.py").write_text(
                """
import numpy as np

class Boxes:
    def __init__(self, person):
        self.xyxy = np.array([[5, 5, 45, 45]] if person else [[12, 12, 30, 28]], dtype=float)
        self.conf = np.array([0.9 if person else 0.8], dtype=float)
        self.cls = np.array([0], dtype=float)

class Result:
    def __init__(self, person):
        self.boxes = Boxes(person)

class YOLO:
    def __init__(self, path, task=None):
        self.person = 'person' in str(path)
    def predict(self, source, **kwargs):
        return [Result(self.person)]
""".strip()
                + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root / "fake_modules")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PREDICT_SCRIPT),
                    "--model",
                    "fake-vest.pt",
                    "--person-model",
                    "fake-person.pt",
                    "--source",
                    str(video),
                    "--output",
                    str(output),
                    "--stats",
                    str(stats),
                    "--hard-cases-dir",
                    str(hard_cases),
                    "--device",
                    "cpu",
                    "--max-frames",
                    "3",
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(output.is_file())
            report = json.loads(stats.read_text(encoding="utf-8"))
            self.assertEqual(3, report["total_frames"])
            self.assertEqual(3, report["detection_frames"])
            self.assertAlmostEqual(0.8, report["average_confidence"])
            for category in (
                "false_positive",
                "false_negative",
                "low_confidence",
                "occlusion",
                "far_distance",
                "blue_background",
                "blue_clothes",
            ):
                self.assertTrue((hard_cases / category).is_dir())

    def test_report_cli_requires_metrics_video_and_manual_review_for_ready_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "runs" / "staff_vest" / "baseline_v1"
            run_dir.mkdir(parents=True)
            summary = {
                "environment": {
                    "python": "3.11.15",
                    "ultralytics": "8.4.90",
                    "torch": "2.11.0+cu128",
                    "cuda_available": True,
                    "gpu": "Fake GPU",
                },
                "parameters": {
                    "imgsz": 640,
                    "epochs": 80,
                    "patience": 20,
                    "seed": 42,
                },
                "metrics": {
                    "precision": 0.91,
                    "recall": 0.87,
                    "map50": 0.93,
                    "map50_95": 0.66,
                    "best_epoch": 47,
                },
                "training": {
                    "completed_epochs": 67,
                    "wall_seconds": 321.5,
                    "single_image_inference_ms": 4.2,
                    "single_image_fps": 238.1,
                },
                "artifacts": {
                    "best_pt": str(run_dir / "weights/best.pt"),
                    "last_pt": str(run_dir / "weights/last.pt"),
                },
            }
            run_dir.joinpath("weights").mkdir()
            run_dir.joinpath("weights/best.pt").write_bytes(b"best")
            run_dir.joinpath("weights/last.pt").write_bytes(b"last")
            run_dir.joinpath("training_summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            validation = root / "validation.json"
            validation.write_text(
                json.dumps(
                    {
                        "summary": {"images": 158, "boxes": 124, "empty_labels": 50},
                        "splits": {
                            "train": {"images": 110},
                            "val": {"images": 32},
                            "test": {"images": 16},
                        },
                    }
                ),
                encoding="utf-8",
            )
            video = root / "video.json"
            video.write_text(
                json.dumps(
                    {
                        "source": "blue_test.mp4",
                        "output": "staff_vest_test_result.mp4",
                        "total_frames": 100,
                        "detection_frames": 75,
                        "detections": 80,
                        "average_confidence": 0.82,
                        "suspected_false_positives": 0,
                        "suspected_false_negatives": 0,
                        "processing_fps": 45.0,
                        "confidence_threshold": 0.25,
                        "hard_cases": {},
                    }
                ),
                encoding="utf-8",
            )
            manual = root / "manual.json"
            manual.write_text(
                json.dumps(
                    {
                        "empty_blue_background_no_persistent_false_positive": True,
                        "ordinary_customer_no_persistent_false_positive": True,
                        "ordinary_blue_clothes_no_persistent_false_positive": True,
                        "main_vest_poses_stable": True,
                        "five_frame_three_hit_continuity": True,
                        "no_persistent_blue_wall_false_positive": True,
                        "notes": "Reviewed independent video.",
                    }
                ),
                encoding="utf-8",
            )
            output = root / "report.md"

            result = run_script(
                REPORT_SCRIPT,
                "--run-dir",
                run_dir,
                "--dataset-report",
                validation,
                "--video-stats",
                video,
                "--manual-review",
                manual,
                "--output",
                output,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            report = output.read_text(encoding="utf-8")
            self.assertIn("READY_FOR_DEMO", report)
            self.assertIn("0.9300", report)
            self.assertIn("staff_vest_test_result.mp4", report)


if __name__ == "__main__":
    unittest.main()
