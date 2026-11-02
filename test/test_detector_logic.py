from __future__ import annotations

import unittest

from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from agent.legacy.detector import Detection, SimpleTracker, VisionDetector


class SimpleTrackerTests(unittest.TestCase):
    def test_overlapping_detections_keep_same_track_id(self) -> None:
        tracker = SimpleTracker(iou_threshold=0.3, max_lost_frames=2)

        first = tracker.update([
            Detection([10, 10, 60, 80], 0.90, 0, "person"),
        ])
        second = tracker.update([
            Detection([12, 12, 62, 82], 0.88, 0, "person"),
        ])

        self.assertEqual(first[0].track_id, 1)
        self.assertEqual(second[0].track_id, 1)
        self.assertEqual(tracker.next_id, 2)

    def test_far_apart_detection_gets_new_track_id(self) -> None:
        tracker = SimpleTracker(iou_threshold=0.3)

        tracker.update([Detection([10, 10, 60, 80], 0.90, 0, "person")])
        second = tracker.update([
            Detection([200, 200, 260, 280], 0.88, 0, "person"),
        ])

        self.assertEqual(second[0].track_id, 2)
        self.assertEqual(len(tracker.tracks), 2)

    def test_missing_track_is_marked_inactive_after_timeout(self) -> None:
        tracker = SimpleTracker(iou_threshold=0.3, max_lost_frames=1)

        tracker.update([Detection([10, 10, 60, 80], 0.90, 0, "person")])
        tracker.update([])
        tracker.update([])

        self.assertFalse(tracker.tracks[1].is_active)


class VisionDetectorPureLogicTests(unittest.TestCase):
    def test_point_in_polygon_detects_inside_and_outside_points(self) -> None:
        detector = VisionDetector(model_path="stub.pt")
        polygon = [(0, 0), (100, 0), (100, 100), (0, 100)]

        self.assertTrue(detector._point_in_polygon((50, 50), polygon))
        self.assertFalse(detector._point_in_polygon((150, 50), polygon))

    def test_detection_to_dict_preserves_track_id(self) -> None:
        detection = Detection([1, 2, 3, 4], 0.75, 0, "person", track_id=42)

        self.assertEqual(
            detection.to_dict(),
            {
                "bbox": [1, 2, 3, 4],
                "confidence": 0.75,
                "class_id": 0,
                "class_name": "person",
                "track_id": 42,
            },
        )


if __name__ == "__main__":
    unittest.main()
