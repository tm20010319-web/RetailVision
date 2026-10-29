from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.stage10_live_acceptance import (  # noqa: E402
    evaluate_stage10_3_artifacts,
    summarize_stage10_3_acceptance,
)
from agent.stage10_showcase import (  # noqa: E402
    apply_recorded_event_banners,
    concatenate_stage10_videos,
    extract_video_keyframes,
)


def _frame(seq: int) -> dict[str, object]:
    return {
        "source_seq": seq,
        "timestamp": f"2026-08-16T10:00:{seq:02d}",
        "persons": [{
            "track_id": 1,
            "track_token": "store-1:cam02:run:1",
            "display_name": "顾客1",
        }],
        "products": [
            {
                "product_key": "smart_toy_a",
                "track_id": 1,
                "track_token": "product:bear:1",
            },
            {
                "product_key": "smart_toy_b",
                "track_id": 1,
                "track_token": "product:elephant:1",
            },
        ],
        "associations": [],
        "interactions": [],
        "events": [],
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_video(
    path: Path,
    colors: list[tuple[int, int, int]],
    *,
    size: tuple[int, int] = (64, 48),
) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        5.0,
        size,
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot open test video writer: {path}")
    try:
        for color in colors:
            writer.write(np.full((size[1], size[0], 3), color, dtype=np.uint8))
    finally:
        writer.release()


class Stage103ArtifactTests(unittest.TestCase):
    def test_artifact_evaluator_reads_recorded_facts_and_discovers_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_jsonl(root / "frame_results.jsonl", [_frame(1), _frame(2)])
            _write_jsonl(root / "event_stream.jsonl", [])
            (root / "runtime_summary.json").write_text(
                json.dumps({"runtime_errors": 0}),
                encoding="utf-8",
            )
            _write_video(root / "overlay.mp4", [(20, 30, 40), (30, 40, 50)])
            keyframes = root / "keyframes"
            keyframes.mkdir()
            cv2.imwrite(str(keyframes / "scene.jpg"), np.zeros((48, 64, 3), dtype=np.uint8))

            result = evaluate_stage10_3_artifacts("A", artifact_dir=root).to_dict()

        self.assertEqual("PASS", result["result"])
        self.assertEqual(str(root / "overlay.mp4"), result["overlay_path"])
        self.assertEqual([str(root / "keyframes" / "scene.jpg")], result["keyframe_paths"])
        self.assertEqual(2, result["diagnostics"]["frame_count"])

    def test_artifact_evaluator_fails_closed_on_recorded_runtime_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_jsonl(root / "frame_results.jsonl", [_frame(1)])
            _write_jsonl(root / "event_stream.jsonl", [])
            (root / "runtime_summary.json").write_text(
                json.dumps({"runtime_errors": 1}),
                encoding="utf-8",
            )
            (root / "overlay.mp4").touch()

            result = evaluate_stage10_3_artifacts("A", artifact_dir=root).to_dict()

        self.assertEqual("FAIL", result["result"])
        self.assertEqual(1, result["diagnostics"]["runtime_errors"])

    def test_acceptance_summary_requires_all_six_scenarios_to_pass(self):
        passing = {
            code: {
                "scenario": code,
                "result": "PASS",
                "false_pick_up_count": 0,
                "false_put_down_count": 0,
            }
            for code in "ABCDEF"
        }
        failed = dict(passing)
        failed["F"] = {
            "scenario": "F",
            "result": "FAIL",
            "false_pick_up_count": 2,
            "false_put_down_count": 1,
        }

        self.assertEqual("PASS", summarize_stage10_3_acceptance(passing)["result"])
        self.assertEqual("FAIL", summarize_stage10_3_acceptance(failed)["result"])
        self.assertEqual(
            ["F"],
            summarize_stage10_3_acceptance(failed)["failed_scenarios"],
        )
        self.assertEqual(2, summarize_stage10_3_acceptance(failed)["false_pick_up_count"])
        self.assertEqual(1, summarize_stage10_3_acceptance(failed)["false_put_down_count"])

    def test_showcase_concatenates_real_frames_and_extracts_keyframes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            output = root / "showcase.mp4"
            _write_video(first, [(10, 20, 30), (20, 30, 40)])
            _write_video(second, [(30, 40, 50), (40, 50, 60), (50, 60, 70)])

            result = concatenate_stage10_videos([first, second], output, fps=5.0)
            keyframes = extract_video_keyframes(output, root / "keyframes")
            capture = cv2.VideoCapture(str(output))
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            capture.release()

        self.assertEqual(5, result.frame_count)
        self.assertEqual(5, frame_count)
        self.assertEqual(3, len(keyframes))
        self.assertTrue(all(path.name.endswith(".jpg") for path in keyframes))

    def test_recorded_event_banner_prefers_pickup_and_expires_from_frame_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            frames = []
            for index, second in enumerate((0, 1, 2, 4)):
                row = {
                    "timestamp": f"2026-08-16T10:00:0{second}",
                    "events": [],
                }
                if index == 0:
                    row["events"] = [
                        {
                            "event_type": "PICK_UP",
                            "display": {"message": "顾客1 拿起了 智能熊"},
                        },
                        {
                            "event_type": "HOLDING",
                            "display": {"message": "顾客1 正在查看 智能熊"},
                        },
                    ]
                frames.append(row)
            _write_video(source, [(255, 255, 255)] * 4, size=(1280, 720))
            _write_jsonl(root / "frame_results.jsonl", frames)

            result = apply_recorded_event_banners(
                source,
                root / "frame_results.jsonl",
                output,
            )
            capture = cv2.VideoCapture(str(output))
            rendered = []
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                rendered.append(frame)
            capture.release()

        self.assertEqual(4, result.frame_count)
        self.assertLess(int(rendered[0][50, 900, 0]), 40)
        self.assertGreater(float(rendered[-1][12:64, 370:925].mean()), 200.0)


if __name__ == "__main__":
    unittest.main()
