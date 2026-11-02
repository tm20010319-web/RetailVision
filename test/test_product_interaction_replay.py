from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.person_product_association import PersonProductAssociationStatus
from agent.behavior.product_interaction import (
    ProductInteractionConfig,
    ProductInteractionEventType,
    ProductInteractionObservation,
    ProductInteractionRuntime,
)
from tools.replay.product_interaction_replay import (
    ProductSourceRecord,
    Stage9ReplayConfig,
    _acceptance_status,
    build_summary,
    load_replay_config,
    read_stage9_inputs,
    replay,
)
from agent.behavior.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


class ProductInteractionReplayTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(1970, 1, 1)
        self.config = Stage9ReplayConfig(
            camera_id="cam02",
            interaction=ProductInteractionConfig(
                surface_evidence_enabled=False,
                minimum_pickup_confidence=0.70,
            ),
        )

    def record(
        self,
        index: int,
        *,
        key="smart_toy_a",
        x=700,
        y=430,
        phase="surface",
        test_case="D",
        token=None,
        surface=1.0,
        mask=True,
    ):
        token = token or f"product:cam02:run:{key}:1"
        observation = ProductInteractionObservation(
            observed_at=self.start + timedelta(seconds=index * 0.2),
            source_seq=index,
            camera_id="cam02",
            product_key=key,
            product_type="smart_toy",
            product_track_id=int(token.rsplit(":", 1)[-1]),
            product_track_token=token,
            product_track_status=(
                ProductTrackStatus.ACTIVE if mask else ProductTrackStatus.MISSING_PENDING
            ),
            product_mask_present=mask,
            bbox=(0.5, 0.5, 0.6, 0.6) if mask else None,
            centroid=(x / 1280.0, y / 720.0) if mask else None,
            display_state=ProductDisplayState.IN_DISPLAY,
            surface_overlap_ratio=surface if mask else None,
            associated_person_track_id=23,
            associated_person_track_token="store-1:cam02:person-run:23",
            person_product_association_status=PersonProductAssociationStatus.ASSOCIATED,
            association_grace_active=False,
        )
        return ProductSourceRecord(
            observation=observation,
            bbox_pixels=(640, 360, 768, 432) if mask else None,
            window_no=index // 8,
            test_case=test_case,
            phase=phase,
            video_elapsed_seconds=index * 0.2,
            product_recovered_from_missing_pending=False,
        )

    def test_config_loader_keeps_frozen_dimensions_and_thresholds(self):
        config = load_replay_config(
            Path(__file__).parents[1] / "configs/cam02/product_interaction.cam02.stage9.json"
        )

        self.assertEqual("cam02", config.camera_id)
        self.assertEqual(1280, config.interaction.frame_width)
        self.assertEqual(12.0, config.interaction.motion_enter_displacement_pixels)
        self.assertEqual(3, config.interaction.putdown_confirm_frames)

    def test_replay_consumes_product_token_and_source_seq(self):
        records = [self.record(index) for index in range(8)]
        replayed = replay(records, config=self.config)

        self.assertEqual(8, len(replayed))
        self.assertEqual(7, replayed[-1].result.observation.source_seq)
        self.assertEqual(records[-1].observation.product_track_token, replayed[-1].result.observation.product_track_token)

    def test_timeline_metadata_is_not_used_by_runtime(self):
        first = replay(
            [self.record(index, phase="surface", test_case="D") for index in range(8)],
            config=self.config,
        )
        second = replay(
            [self.record(index, phase="handheld", test_case="E") for index in range(8)],
            config=self.config,
        )

        self.assertEqual(
            [item.result.interaction_state for item in first],
            [item.result.interaction_state for item in second],
        )
        self.assertNotIn("phase", first[-1].result.observation.__dict__)

    def test_stage9_input_join_requires_every_stage8_source_seq(self):
        product_row = {
            "timestamp": "1970-01-01T00:00:00Z",
            "window_no": 0,
            "source_seq": 5,
            "product_key": "smart_toy_a",
            "product_type": "smart_toy",
            "product_track_id": 1,
            "product_track_token": "product:cam02:run:smart_toy_a:1",
            "bbox": [640, 360, 768, 432],
            "centroid": [0.55, 0.60],
            "display_state": "IN_DISPLAY",
            "surface_overlap_ratio": 1.0,
            "track_status": "ACTIVE",
            "mask_present": True,
            "video_elapsed_seconds": 0.0,
            "test_case": "D",
            "phase": "toy_a_surface",
            "recovered_from_missing_pending": False,
        }
        association_row = {
            "timestamp": "1970-01-01T00:00:00Z",
            "source_seq": 5,
            "product_key": "smart_toy_a",
            "product_track_id": 1,
            "product_track_token": "product:cam02:run:smart_toy_a:1",
            "product_track_status": "ACTIVE",
            "product_mask_present": True,
            "product_bbox": [0.5, 0.5, 0.6, 0.6],
            "product_centroid": [0.55, 0.6],
            "associated_person_track_id": 23,
            "associated_person_track_token": "store-1:cam02:person-run:23",
            "association_status": "ASSOCIATED",
            "association_grace_active": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            product_path = directory / "products.jsonl"
            association_path = directory / "associations.jsonl"
            product_path.write_text(json.dumps(product_row) + "\n", encoding="utf-8")
            association_path.write_text(json.dumps(association_row) + "\n", encoding="utf-8")

            records = read_stage9_inputs(
                product_jsonl=product_path,
                association_jsonl=association_path,
                camera_id="cam02",
                frame_width=1280,
                frame_height=720,
            )

        self.assertEqual(1, len(records))
        self.assertEqual(5, records[0].observation.source_seq)
        self.assertEqual((0.5, 0.5, 0.6, 0.6), records[0].observation.bbox)

    def test_summary_counts_events_and_duplicate_events(self):
        records = [self.record(index, phase="toy_a_surface") for index in range(8)]
        replayed = replay(records, config=self.config)
        summary = build_summary(replayed, visual_review_status="pending")

        self.assertEqual(0, summary["pickup_event_count"])
        self.assertEqual(0, summary["putdown_event_count"])
        self.assertEqual(0, summary["duplicate_pickup_count"])
        self.assertEqual("NEEDS_VISUAL_REVIEW", summary["acceptance"])

    def test_stage9_events_have_deterministic_session_contract(self):
        records = [self.record(index, surface=1.0) for index in range(8)]
        records.extend([
            self.record(8, x=700, y=410, phase="toy_a_handheld", surface=None),
            self.record(9, x=700, y=380, phase="toy_a_handheld", surface=None),
            self.record(10, x=705, y=360, phase="toy_a_handheld", surface=None),
        ])
        replayed = replay(records, config=self.config)
        events = [event for item in replayed for event in item.result.events]

        self.assertTrue(events)
        self.assertEqual(1, events[0].event_sequence)
        self.assertIn("interaction_session_id", events[0].to_dict())
        self.assertEqual(events[0].interaction_session_id, events[1].interaction_session_id)

    def test_acceptance_gate_covers_test_c_and_global_session_counts(self):
        tests = {
            "C": {
                "smart_toy_a": {"pickup_event_count": 0, "putdown_event_count": 0},
                "smart_toy_b": {"pickup_event_count": 0, "putdown_event_count": 0},
            },
            "D": {
                "smart_toy_a": {"pickup_event_count": 1, "putdown_event_count": 1},
                "smart_toy_b": {"pickup_event_count": 0, "putdown_event_count": 0},
            },
            "E": {
                "smart_toy_a": {"pickup_event_count": 0, "putdown_event_count": 0},
                "smart_toy_b": {"pickup_event_count": 1, "putdown_event_count": 1},
            },
        }
        accepted = _acceptance_status(
            visual_review_status="verified",
            wrong_actor_event_count=0,
            false_pickups=0,
            false_putdowns=0,
            duplicate_pickups=0,
            duplicate_putdowns=0,
            tests=tests,
            global_pickup_event_count=2,
            global_putdown_event_count=2,
            completed_interaction_session_count=2,
            test_c_false_pickups=0,
            test_c_false_putdowns=0,
        )
        self.assertEqual("PASS", accepted)

        rejected = _acceptance_status(
            visual_review_status="verified",
            wrong_actor_event_count=0,
            false_pickups=0,
            false_putdowns=0,
            duplicate_pickups=0,
            duplicate_putdowns=0,
            tests=tests,
            global_pickup_event_count=2,
            global_putdown_event_count=2,
            completed_interaction_session_count=2,
            test_c_false_pickups=1,
            test_c_false_putdowns=0,
        )
        self.assertEqual("FAIL", rejected)


if __name__ == "__main__":
    unittest.main()
