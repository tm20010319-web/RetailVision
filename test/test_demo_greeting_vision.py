from __future__ import annotations

import unittest

import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior_vision import GroundPlaneProjector
from demo_greeting.vision import DemoVisionPipeline


class _FakeBoxes:
    def __init__(self, xyxy, confidence, classes):
        self.xyxy = xyxy
        self.conf = confidence
        self.cls = classes


class _FakeResult:
    def __init__(self, boxes, names):
        self.boxes = boxes
        self.names = names


class _FakeModel:
    def __init__(self, result):
        self.result = result

    def predict(self, _frame, **_kwargs):
        return [self.result]


class DemoGreetingVisionTests(unittest.TestCase):
    def test_one_vest_resolves_to_one_staff_and_leaves_the_other_person_as_customer(self):
        person_model = _FakeModel(
            _FakeResult(
                _FakeBoxes(
                    [[0, 0, 100, 200], [40, 0, 140, 100]],
                    [0.96, 0.94],
                    [0, 0],
                ),
                {0: "person"},
            )
        )
        vest_model = _FakeModel(
            _FakeResult(
                _FakeBoxes([[45, 40, 75, 90]], [0.91], [0]),
                {0: "staff_vest"},
            )
        )

        def load_model(path):
            return person_model if path == "person.pt" else vest_model

        pipeline = DemoVisionPipeline(
            person_model_path="person.pt",
            vest_model_path="vest.pt",
            device="cpu",
            person_confidence=0.35,
            vest_confidence=0.45,
            model_loader=load_model,
        )
        frame = np.zeros((220, 160, 3), dtype=np.uint8)

        observations = []
        for _ in range(5):
            observations = pipeline.detect(frame)

        self.assertEqual(2, len(observations))
        self.assertEqual(1, sum(item.is_staff for item in observations))
        self.assertEqual(1, sum(not item.is_staff for item in observations))
        self.assertEqual(
            {"staff_vest_yolo", "multi_frame_no_vest"},
            {item.role_source for item in observations},
        )

    def test_projects_bbox_foot_point_to_world_metres(self):
        person_model = _FakeModel(
            _FakeResult(
                _FakeBoxes([[0, 0, 100, 200]], [0.96], [0]),
                {0: "person"},
            )
        )
        vest_model = _FakeModel(
            _FakeResult(
                _FakeBoxes([[30, 60, 70, 130]], [0.91], [0]),
                {0: "staff_vest"},
            )
        )

        def load_model(path):
            return person_model if path == "person.pt" else vest_model

        pipeline = DemoVisionPipeline(
            person_model_path="person.pt",
            vest_model_path="vest.pt",
            device="cpu",
            ground_projector=GroundPlaneProjector(
                (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
            ),
            calibration_image_size=(100, 200),
            model_loader=load_model,
        )
        frame = np.zeros((200, 100, 3), dtype=np.uint8)

        observations = []
        for _ in range(5):
            observations = pipeline.detect(frame)

        self.assertEqual(1, len(observations))
        self.assertEqual((0.5, 1.0), observations[0].world_point)


if __name__ == "__main__":
    unittest.main()
