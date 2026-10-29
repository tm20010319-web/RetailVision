from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior_monitor import BehaviorMonitor, BehaviorMonitorConfig, PolygonArea
from agent.sam31_behavior_adapter import Sam31BehaviorAdapter
from pipelines.behavior_runtime import BehaviorCameraRuntime


class Sam31BehaviorAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Sam31BehaviorAdapter(
            store_id="store-1",
            camera_id="behavior-1",
            runtime_instance_id="runtime-1",
            frame_width=1000,
            frame_height=500,
        )

    def test_global_track_creates_exact_token_and_bottom_center_foot_point(self):
        observation = self.adapter.to_observation(
            global_track_id=42,
            bbox=(100.0, 50.0, 300.0, 450.0),
            centroid=(200.0, 250.0),
        )

        self.assertEqual(42, observation.track_id)
        self.assertEqual("store-1:behavior-1:runtime-1:42", observation.track_token)
        self.assertEqual((0.2, 0.9), observation.foot_point)
        self.assertEqual("customer", observation.role)

    def test_csv_replay_parses_sam31_output_without_using_local_obj_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track_observations.csv"
            with path.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=(
                    "source_seq", "source_wall_time", "local_obj_id",
                    "global_track_id", "bbox_x1", "bbox_y1", "bbox_x2",
                    "bbox_y2", "centroid_x", "centroid_y",
                ))
                writer.writeheader()
                writer.writerow({
                    "source_seq": 101,
                    "source_wall_time": "1780000000.0",
                    "local_obj_id": 7,
                    "global_track_id": 42,
                    "bbox_x1": 100,
                    "bbox_y1": 50,
                    "bbox_x2": 300,
                    "bbox_y2": 450,
                    "centroid_x": 200,
                    "centroid_y": 250,
                })

            records = tuple(self.adapter.read_csv(path))

        self.assertEqual(1, len(records))
        self.assertEqual(101, records[0].source_seq)
        self.assertEqual(42, records[0].global_track_id)
        self.assertEqual("store-1:behavior-1:runtime-1:42", records[0].observation.track_token)
        self.assertEqual(datetime(2026, 5, 28, 20, 26, 40), records[0].observed_at)

    def test_same_global_track_across_sam_windows_keeps_monitor_state(self):
        monitor = BehaviorMonitor(
            camera_id="behavior-1",
            interaction_areas=(PolygonArea(
                "zone-a", "interaction", ((0.1, 0.1), (0.4, 0.1), (0.4, 1.0), (0.1, 1.0))
            ),),
            config=BehaviorMonitorConfig(
                dwell_minimum_seconds=1.0,
                zone_track_lost_seconds=0.1,
            ),
        )
        runtime = BehaviorCameraRuntime(SimpleNamespace(runtime_id="runtime-1"), monitor)
        first_window = self.adapter.to_observation(
            global_track_id=42,
            bbox=(100.0, 50.0, 300.0, 450.0),
        )
        second_window = self.adapter.to_observation(
            global_track_id=42,
            bbox=(110.0, 55.0, 310.0, 450.0),
        )
        started_at = datetime(2026, 8, 12, 10, 0, 0)

        runtime.process_observations(started_at, (first_window,))
        runtime.process_observations(started_at + timedelta(seconds=1.1), (second_window,))
        _entries, behaviors = runtime.process_observations(
            started_at + timedelta(seconds=1.3), ()
        )

        self.assertEqual(first_window.track_token, second_window.track_token)
        self.assertEqual(1, len(behaviors))
        self.assertEqual("merchandise_dwell", behaviors[0].event_type)
        self.assertEqual(first_window.track_token, behaviors[0].customer_track_token)


if __name__ == "__main__":
    unittest.main()
