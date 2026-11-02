from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from demo.greeting.service import DemoGreetingConfig, DemoGreetingRuntime
from demo.greeting.state import PersonObservation


class _FakeCapture:
    def __init__(self, _source):
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        return True, object()

    def release(self):
        self.released = True


class _FakeVision:
    def __init__(self):
        self.index = 0
        self.last_detection_count = 0
        self.last_staff_count = 0
        self.last_vest_count = 0

    def load(self):
        return None

    def detect(self, _frame):
        positions = (10.0, 18.0, 29.0, 40.0, 50.0)
        x = positions[min(self.index, len(positions) - 1)]
        self.index += 1
        self.last_detection_count = 2
        self.last_staff_count = 1
        self.last_vest_count = 1
        return [
            PersonObservation(
                track_id=7,
                bbox=(x, 10.0, x + 20.0, 90.0),
                is_staff=True,
                staff_confidence=0.88,
                role_source="staff_vest_yolo",
            ),
            PersonObservation(
                track_id=42,
                bbox=(130.0, 10.0, 150.0, 90.0),
                is_staff=False,
                role_source="person_without_vest",
            ),
        ]


class DemoGreetingRuntimeTests(unittest.TestCase):
    def test_environment_config_loads_entrance_ground_calibration(self):
        with patch.dict(
            "os.environ",
            {
                "DEMO_GREETING_CAMERA_ID": "entrance-1",
                "DEMO_GREETING_CAMERA_CONFIG": "configs/examples/behavior_cameras.example.json",
                "DEMO_GREETING_VIDEO_SOURCE": "fake://camera",
            },
            clear=False,
        ):
            config = DemoGreetingConfig.from_environment()

        self.assertEqual((2560, 1440), config.calibration_image_size)
        self.assertEqual("provisional", config.calibration_status)
        self.assertIsNotNone(config.ground_projector)
        world = config.ground_projector.project((0.06640625, 0.5625))
        self.assertAlmostEqual(0.0, world[0], places=5)
        self.assertAlmostEqual(0.0, world[1], places=5)

    def test_real_worker_loop_persists_only_one_candidate_per_track_pair(self):
        recorded = []
        config = DemoGreetingConfig(
            source="fake://camera",
            camera_id="entrance-1",
            store_id="store-1",
            # The worker only validates that configured model paths exist.
            # FakeVision prevents these test files from being loaded as models.
            person_model_path=__file__,
            vest_model_path=__file__,
            device="cpu",
            co_presence_seconds=0.5,
            staff_motion_pixels=15.0,
            frame_interval_seconds=0.22,
        )
        runtime = DemoGreetingRuntime(
            config,
            capture_factory=_FakeCapture,
            event_recorder=recorded.append,
            vision_factory=lambda _config: _FakeVision(),
        )

        started = runtime.start()
        self.assertTrue(started["running"])
        time.sleep(0.95)
        stopped = runtime.stop()

        self.assertEqual("stopped", stopped["status"])
        self.assertEqual(1, len(recorded))
        self.assertTrue(recorded[0].details["is_demo"])
        self.assertEqual("staff_role_unbound", recorded[0].details["identity_status"])
        self.assertEqual(1, stopped["total_event_count"])
