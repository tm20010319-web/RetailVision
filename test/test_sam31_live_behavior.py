from __future__ import annotations

import unittest
from datetime import datetime, timezone

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.sam31_behavior_adapter import Sam31BehaviorAdapter
from agent.sam31_live_behavior import Sam31GlobalTrack, Sam31LiveBehaviorBridge


class FakeRuntime:
    def __init__(self):
        self.calls = []

    def process_observations(self, observed_at, observations):
        self.calls.append((observed_at, tuple(observations)))
        return [], []

    def zone_runtime_diagnostics(self):
        return (), ()


class Sam31LiveBehaviorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.adapter = Sam31BehaviorAdapter(
            store_id="store-1",
            camera_id="cam02",
            runtime_instance_id="stage6b-live-1",
            frame_width=1280,
            frame_height=720,
        )
        self.bridge = Sam31LiveBehaviorBridge(
            adapter=self.adapter,
            runtime=self.runtime,
        )
        self.now = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)

    def test_one_multi_person_frame_calls_runtime_once_with_global_ids(self):
        result = self.bridge.process_batch(
            observed_at=self.now,
            source_seq=101,
            tracks=(
                Sam31GlobalTrack(23, (100, 100, 300, 600), (200, 350)),
                Sam31GlobalTrack(24, (400, 120, 600, 540), (500, 330)),
            ),
        )

        self.assertEqual(1, len(self.runtime.calls))
        observations = self.runtime.calls[0][1]
        self.assertEqual([23, 24], [item.track_id for item in observations])
        self.assertEqual(
            [
                "store-1:cam02:stage6b-live-1:23",
                "store-1:cam02:stage6b-live-1:24",
            ],
            [item.track_token for item in observations],
        )
        self.assertEqual((0.15625, 600 / 720), observations[0].foot_point)
        self.assertEqual(2, len(result.tracks))

    def test_ai_frame_bbox_outside_1280x720_is_rejected_before_runtime(self):
        with self.assertRaisesRegex(ValueError, "configured frame"):
            self.bridge.process_batch(
                observed_at=self.now,
                source_seq=1,
                tracks=(Sam31GlobalTrack(23, (100, 100, 1300, 700)),),
            )

        self.assertEqual([], self.runtime.calls)

    def test_source_seq_must_be_strictly_increasing_across_windows(self):
        track = Sam31GlobalTrack(23, (100, 100, 300, 600))
        first = self.bridge.process_batch(
            observed_at=self.now,
            source_seq=101,
            tracks=(track,),
        )
        second = self.bridge.process_batch(
            observed_at=self.now,
            source_seq=109,
            tracks=(track,),
        )

        self.assertEqual(
            first.tracks[0]["track_token"], second.tracks[0]["track_token"]
        )
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            self.bridge.process_batch(
                observed_at=self.now,
                source_seq=109,
                tracks=(track,),
            )

    def test_empty_frame_is_one_runtime_batch_for_track_lost_clock(self):
        result = self.bridge.process_batch(
            observed_at=self.now,
            source_seq=200,
            tracks=(),
        )

        self.assertEqual(1, len(self.runtime.calls))
        self.assertEqual((), self.runtime.calls[0][1])
        self.assertEqual((), result.tracks)


if __name__ == "__main__":
    unittest.main()
