import unittest
from types import SimpleNamespace
from unittest.mock import patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from pipelines.behavior_runtime import create_behavior_camera_runtime


class RealtimeGreetingGateTests(unittest.TestCase):
    def _camera(self):
        return SimpleNamespace(
            camera_id="entrance-1", store_id="store-1", entry_trigger=None,
            greeting_area=None, interaction_areas=(), display_areas=(),
            ground_projector=None, calibration_status="provisional",
            calibration_image_size=(2560, 1440),
            monitor_config=SimpleNamespace(),
        )

    def _build(self, realtime, shadow):
        with patch("pipelines.behavior_runtime.BehaviorEventStore"), \
             patch("pipelines.behavior_runtime.BehaviorVisionAdapter") as vision, \
             patch("pipelines.behavior_runtime.BehaviorMonitor"), \
             patch("pipelines.behavior_runtime.CustomerEntryVisit"), \
             patch("pipelines.behavior_runtime.StaffGreetingEvaluation") as greeting:
            vision.return_value.runtime_id = "runtime-1"
            create_behavior_camera_runtime(
                camera=self._camera(), session_factory=lambda: None,
                person_model_path="person.pt", staff_vest_model_path=None,
                pose_model_path="pose.pt", greeting_v2_enabled=shadow,
                realtime_greeting_enabled=realtime,
            )
            return greeting

    def test_parent_child_matrix(self):
        self.assertEqual(0, self._build(False, False).call_count)
        self.assertEqual(0, self._build(False, True).call_count)
        self.assertEqual(1, self._build(True, False).call_count)
        kwargs = self._build(True, True).call_args.kwargs
        self.assertTrue(kwargs["comparison_enabled"])


if __name__ == "__main__":
    unittest.main()
