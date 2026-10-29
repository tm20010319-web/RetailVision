from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from unittest.mock import patch

import numpy as np

from agent.detector import Detection
from agent.device_identity import CustomerSession as DeviceCustomerSession
from agent.device_identity import StableTrackEvent
from pipelines import CustomerSession as PublicCustomerSession
from pipelines.realtime_entry_pipeline import (
    CustomerSession as PipelineCustomerSession,
    RealtimeEntryPipeline,
    create_realtime_pipeline,
)


class Detector:
    def __init__(self):
        self.detections = [
            Detection([10, 20, 100, 200], 0.91, 0, "person", track_id=3)
        ]

    def process_frame(self, frame):
        return self.detections

    def reset(self):
        pass


class ExplodingFaceRecognizer:
    def __init__(self):
        self.calls = 0

    def recognize_face(self, image, **kwargs):
        self.calls += 1
        raise AssertionError("local recognition must not run in device_api mode")


class RealtimeDeviceIdentityModeTests(unittest.TestCase):
    def test_customer_session_has_one_canonical_class(self):
        self.assertIs(DeviceCustomerSession, PipelineCustomerSession)
        self.assertIs(DeviceCustomerSession, PublicCustomerSession)

    def test_stable_track_is_emitted_without_crop_or_local_recognition(self):
        recognizer = ExplodingFaceRecognizer()
        stable_events = []
        anonymous = []
        pipeline = RealtimeEntryPipeline(
            detector=Detector(),
            face_recognizer=recognizer,
            identity_mode="device_api",
            stability_threshold=1,
            camera_id="front-door",
            on_stable_track=stable_events.append,
            on_anonymous_visit=lambda *args: anonymous.append(args),
        )

        result = pipeline.process_frame(np.zeros((240, 320, 3), dtype=np.uint8))

        self.assertEqual(0, recognizer.calls)
        self.assertEqual([], anonymous)
        self.assertEqual(1, len(stable_events))
        self.assertIsInstance(stable_events[0], StableTrackEvent)
        self.assertEqual("front-door", stable_events[0].camera_id)
        self.assertEqual(3, stable_events[0].track_id)
        self.assertEqual("identity_pending", result["confirmed_entries"][0]["entry_type"])
        self.assertEqual("awaiting_device_identity", result["confirmed_entries"][0]["reason"])
        self.assertEqual(1, len(result["stable_track_events"]))

    def test_factory_defaults_to_legacy_mode_without_legacy_dependencies(self):
        with patch("pipelines.realtime_entry_pipeline.VisionDetector.initialize"):
            pipeline = create_realtime_pipeline(
                model_path="stub.pt",
                camera_id="front-door",
            )

        self.assertEqual("legacy", pipeline.identity_mode)
        self.assertIsNone(pipeline.face_recognizer)

    def test_factory_explicit_device_api_mode_remains_available(self):
        with patch("pipelines.realtime_entry_pipeline.VisionDetector.initialize"):
            pipeline = create_realtime_pipeline(
                model_path="stub.pt",
                camera_id="front-door",
                identity_mode="device_api",
            )

        self.assertEqual("device_api", pipeline.identity_mode)

    def test_factory_with_legacy_recognizer_keeps_compatibility_mode(self):
        recognizer = ExplodingFaceRecognizer()
        with patch("pipelines.realtime_entry_pipeline.VisionDetector.initialize"):
            pipeline = create_realtime_pipeline(
                model_path="stub.pt",
                face_recognizer=recognizer,
            )

        self.assertEqual("legacy", pipeline.identity_mode)

    def test_factory_with_old_new_customer_callback_keeps_legacy_mode(self):
        with patch("pipelines.realtime_entry_pipeline.VisionDetector.initialize"):
            pipeline = create_realtime_pipeline(
                model_path="stub.pt",
                on_new_customer=lambda *args: None,
            )

        self.assertEqual("legacy", pipeline.identity_mode)

    def test_invalid_identity_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            RealtimeEntryPipeline(detector=Detector(), identity_mode="other")


if __name__ == "__main__":
    unittest.main()
