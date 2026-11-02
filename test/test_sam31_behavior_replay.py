from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.behavior_monitor import BehaviorMonitor, BehaviorMonitorConfig, PolygonArea
from agent.behavior.sam31_behavior_adapter import Sam31BehaviorAdapter, Sam31BehaviorReplayRecord
from pipelines.behavior_runtime import BehaviorCameraRuntime
from scripts.replay_sam31_behavior_csv import replay_records


AREA = PolygonArea(
    "display_island",
    "interaction",
    ((0.2, 0.2), (0.6, 0.2), (0.6, 0.95), (0.2, 0.95)),
)


class Sam31BehaviorReplayTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Sam31BehaviorAdapter(
            store_id="store-1",
            camera_id="cam02",
            runtime_instance_id="runtime-1",
            frame_width=1000,
            frame_height=500,
        )
        self.started_at = datetime(2026, 8, 13, 10, 0, 0)

    def _record(self, source_seq: int, seconds: float, foot_x: float):
        bbox = (foot_x * 1000 - 50, 100.0, foot_x * 1000 + 50, 400.0)
        observation = self.adapter.to_observation(
            global_track_id=42,
            bbox=bbox,
        )
        return Sam31BehaviorReplayRecord(
            observed_at=self.started_at + timedelta(seconds=seconds),
            source_seq=source_seq,
            global_track_id=42,
            bbox=bbox,
            centroid=None,
            observation=observation,
        )

    def _runtime(self, **config):
        monitor = BehaviorMonitor(
            camera_id="cam02",
            interaction_areas=(AREA,),
            config=BehaviorMonitorConfig(**config),
        )
        return BehaviorCameraRuntime(SimpleNamespace(runtime_id="runtime-1"), monitor)

    def test_replay_outputs_zone_state_dwell_and_enter_exit_transitions(self):
        runtime = self._runtime(
            dwell_minimum_seconds=1.0,
            zone_enter_stable_frames=2,
            zone_exit_stable_frames=2,
        )
        records = (
            self._record(1, 0.0, 0.1),
            self._record(2, 1.0, 0.4),
            self._record(3, 2.0, 0.4),
            self._record(4, 4.0, 0.4),
            self._record(5, 5.0, 0.1),
            self._record(6, 6.0, 0.1),
        )

        output = list(replay_records(records, runtime=runtime))

        self.assertEqual("enter_pending", output[1]["zone_states"][0]["zone_state"])
        self.assertEqual("ZONE_ENTER", output[2]["runtime_transitions"][0]["transition"])
        self.assertEqual(3.0, output[3]["zone_states"][0]["dwell_seconds"])
        self.assertEqual("exit_pending", output[4]["zone_states"][0]["zone_state"])
        self.assertEqual("ZONE_EXIT", output[5]["runtime_transitions"][0]["transition"])
        self.assertEqual(3.0, output[5]["runtime_transitions"][0]["dwell_seconds"])
        self.assertEqual("merchandise_dwell", output[5]["behavior_events"][0]["event_type"])

    def test_replay_flush_outputs_track_lost_and_cleans_state(self):
        runtime = self._runtime(
            dwell_minimum_seconds=1.0,
            zone_enter_stable_frames=1,
            zone_track_lost_seconds=2.0,
        )
        records = (
            self._record(1, 0.0, 0.4),
            self._record(2, 3.0, 0.4),
        )

        output = list(replay_records(records, runtime=runtime, flush_seconds=2.1))
        flushed = output[-1]

        self.assertTrue(flushed["flush"])
        self.assertEqual("TRACK_LOST", flushed["runtime_transitions"][0]["transition"])
        self.assertEqual("outside", flushed["zone_states"][0]["zone_state"])
        self.assertEqual(3.0, flushed["runtime_transitions"][0]["dwell_seconds"])
        self.assertEqual("merchandise_dwell", flushed["behavior_events"][0]["event_type"])


if __name__ == "__main__":
    unittest.main()
