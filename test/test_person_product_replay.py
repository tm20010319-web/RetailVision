from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.person_product_association import (
    PersonProductAssociationConfig,
    PersonProductAssociationStatus,
    ProductAssociationObservation,
)
from agent.person_product_replay import (
    ProductReplayRecord,
    Stage8ReplayConfig,
    build_summary,
    read_product_records,
    replay_associations,
)
from agent.sam31_behavior_adapter import Sam31BehaviorAdapter
from agent.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


class PersonProductReplayTests(unittest.TestCase):
    def setUp(self):
        self.started = datetime(1970, 1, 1)
        self.adapter = Sam31BehaviorAdapter(
            store_id="store-1",
            camera_id="cam02",
            runtime_instance_id="person-run",
            frame_width=1280,
            frame_height=720,
        )
        association = PersonProductAssociationConfig(
            frame_width=1280,
            frame_height=720,
            association_enter_stable_frames=3,
        )
        self.config = Stage8ReplayConfig(
            store_id="store-1",
            camera_id="cam02",
            frame_width=1280,
            frame_height=720,
            sync_mode="exact_source_seq",
            association=association,
        )

    def person(self, source_seq: int, seconds: float):
        return self.adapter.to_record(
            observed_at=self.started + timedelta(seconds=seconds),
            source_seq=source_seq,
            global_track_id=23,
            bbox=(448, 130, 614, 662),
            centroid=(531, 396),
        )

    def product(self, source_seq: int, seconds: float) -> ProductReplayRecord:
        observation = ProductAssociationObservation(
            observed_at=self.started + timedelta(seconds=seconds),
            source_seq=source_seq,
            camera_id="cam02",
            product_key="smart_toy_a",
            product_type="smart_toy",
            product_track_id=1,
            product_track_token="product:cam02:product-run:smart_toy_a:1",
            bbox=(0.49, 0.44, 0.57, 0.58),
            centroid=(0.53, 0.51),
            track_status=ProductTrackStatus.ACTIVE,
            display_state=ProductDisplayState.IN_DISPLAY,
            mask_present=True,
        )
        return ProductReplayRecord(
            observation=observation,
            window_no=source_seq // 10,
            test_case="D",
            phase="toy_a_handheld",
            video_elapsed_seconds=seconds,
            recovered_from_missing_pending=False,
        )

    def test_exact_source_seq_never_backfills_a_naturally_missing_person_frame(self):
        products = tuple(self.product(seq, index * 0.2) for index, seq in enumerate((100, 104, 108)))
        people = tuple(self.person(seq + 1, index * 0.2) for index, seq in enumerate((100, 104, 108)))

        results = replay_associations(
            person_records=people,
            product_records=products,
            replay_config=self.config,
        )

        self.assertTrue(all(
            item.result.status is PersonProductAssociationStatus.UNASSOCIATED
            for item in results
        ))
        self.assertTrue(all(item.result.person_track_token is None for item in results))

    def test_exact_source_seq_creates_stable_global_track_association(self):
        sequences = (100, 104, 108)
        products = tuple(self.product(seq, index * 0.2) for index, seq in enumerate(sequences))
        people = tuple(self.person(seq, index * 0.2) for index, seq in enumerate(sequences))

        results = replay_associations(
            person_records=people,
            product_records=products,
            replay_config=self.config,
        )

        self.assertEqual(PersonProductAssociationStatus.ASSOCIATED, results[-1].result.status)
        self.assertEqual("store-1:cam02:person-run:23", results[-1].result.person_track_token)
        self.assertEqual(0.0, results[-1].result.person_product_sync_delta_ms)

    def test_nearest_timestamp_requires_explicit_mode_and_respects_delta(self):
        config = replace(self.config, sync_mode="nearest_timestamp")
        sequences = (100, 104, 108)
        products = tuple(self.product(seq, index * 0.2) for index, seq in enumerate(sequences))
        people = tuple(self.person(seq + 1, index * 0.2 + 0.05) for index, seq in enumerate(sequences))

        results = replay_associations(
            person_records=people,
            product_records=products,
            replay_config=config,
        )

        self.assertEqual(PersonProductAssociationStatus.ASSOCIATED, results[-1].result.status)
        self.assertAlmostEqual(50.0, results[-1].result.person_product_sync_delta_ms)

    def test_product_jsonl_is_converted_to_normalized_full_frame_domain(self):
        row = {
            "timestamp": "1970-01-01T08:00:00+08:00",
            "window_no": 0,
            "source_seq": 0,
            "product_key": "smart_toy_a",
            "product_type": "smart_toy",
            "product_track_id": 1,
            "product_track_token": "product:cam02:run:smart_toy_a:1",
            "bbox": [640, 360, 768, 504],
            "centroid": [0.55, 0.60],
            "display_state": "IN_DISPLAY",
            "track_status": "ACTIVE",
            "video_elapsed_seconds": 0.0,
            "mask_present": True,
            "test_case": "INITIALIZATION",
            "phase": "stationary_identity_confirmation",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "products.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            record = read_product_records(
                path,
                camera_id="cam02",
                frame_width=1280,
                frame_height=720,
            )[0]

        self.assertEqual((0.5, 0.5, 0.6, 0.7), record.observation.bbox)
        self.assertEqual((0.55, 0.60), record.observation.centroid)

    def test_summary_is_association_only(self):
        sequences = (100, 104, 108)
        replayed = replay_associations(
            person_records=tuple(
                self.person(seq, index * 0.2) for index, seq in enumerate(sequences)
            ),
            product_records=tuple(
                self.product(seq, index * 0.2) for index, seq in enumerate(sequences)
            ),
            replay_config=self.config,
        )

        summary = build_summary(
            replayed,
            person_record_count=3,
            product_record_count=3,
            sync_mode="exact_source_seq",
        )

        self.assertFalse(summary["stage9_entered"])
        self.assertEqual("REAL_VIDEO_VISUAL_ACCEPTANCE_PENDING", summary["acceptance"])
        serialized = json.dumps(summary)
        self.assertNotIn("PICK_UP", serialized)
        self.assertNotIn("HOLDING", serialized)
        self.assertNotIn("PUT_DOWN", serialized)


if __name__ == "__main__":
    unittest.main()
