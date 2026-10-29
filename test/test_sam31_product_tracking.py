from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.sam31_product_tracking import (
    ManualInitializationAttemptTracker,
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplaySurface,
    ProductDisplayState,
    ProductTrackRuntime,
    ProductTrackStatus,
    associate_candidates_globally,
    grounding_decision,
    initialization_candidates,
    pixel_xyxy_to_normalized_xywh,
)
from agent.sam31_product_replay import (
    CDETimeline,
    OfflineVideoWindowSource,
    SingleTextGroundingPerWindow,
    build_cde_summary,
    offline_observed_at,
    render_cde_report,
    sampled_source_indices,
    validate_probe_source,
)


WIDTH = 1280
HEIGHT = 720


def candidate(local_id=7, bbox=(650, 300, 850, 500), confidence=0.9):
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    x1, y1, x2, y2 = bbox
    mask[y1:y2, x1:x2] = True
    return ProductCandidate(
        local_obj_id=local_id,
        bbox=bbox,
        centroid=((x1 + x2) / 2, (y1 + y2) / 2),
        confidence=confidence,
        mask=mask,
    )


class ProductTrackingTests(unittest.TestCase):
    def setUp(self):
        self.surface = ProductDisplaySurface(
            ((0.48, 0.40), (0.72, 0.40), (0.72, 0.95), (0.48, 0.95)),
        )
        self.runtime = ProductTrackRuntime(
            camera_id="cam02",
            runtime_instance_id="stage7c-test",
            product_display_surface=self.surface,
        )
        self.now = datetime(2026, 8, 13, tzinfo=timezone.utc)

    def test_grounding_gate_requires_exactly_one_inside_candidate_with_overlap(self):
        accepted = grounding_decision(
            product_type="smart_toy",
            candidates=(candidate(),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        no_candidate = grounding_decision(
            product_type="smart_toy",
            candidates=(),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        multiple = grounding_decision(
            product_type="smart_toy",
            candidates=(candidate(1), candidate(2)),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )

        self.assertEqual("accepted", accepted.status)
        self.assertEqual("grounding_unstable", no_candidate.status)
        self.assertEqual("grounding_unstable", multiple.status)

    def test_local_id_and_session_changes_do_not_change_product_segment(self):
        decision = grounding_decision(
            product_type="smart_toy",
            candidates=(candidate(local_id=2),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        first = self.runtime.initialize(
            product_type="smart_toy", decision=decision, observed_at=self.now, source_seq=1
        )
        second = self.runtime.observe(
            product_type="smart_toy",
            observed_at=self.now,
            source_seq=20,
            candidate=candidate(
                local_id=99,
                bbox=(760, 500, 890, 620),
            ),
        )

        self.assertEqual(1, first.product_track_id)
        self.assertEqual(first.product_track_token, second.product_track_token)
        self.assertEqual(ProductDisplayState.IN_DISPLAY, second.display_state)

    def test_two_consecutive_window_misses_are_required_before_new_segment(self):
        decision = grounding_decision(
            product_type="smart_toy",
            candidates=(candidate(),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        first = self.runtime.initialize(
            product_type="smart_toy", decision=decision, observed_at=self.now, source_seq=1
        )
        pending = self.runtime.association_miss("smart_toy")
        self.assertEqual(
            ProductTrackStatus.MISSING_PENDING, self.runtime.status("smart_toy")
        )
        lost = self.runtime.association_miss("smart_toy")

        self.assertEqual("UNOBSERVED", pending["display_state"])
        self.assertFalse(pending["tracking_lost"])
        self.assertTrue(lost["tracking_lost"])
        second = self.runtime.initialize(
            product_type="smart_toy", decision=decision, observed_at=self.now, source_seq=30
        )
        self.assertEqual(2, second.product_track_id)
        self.assertNotEqual(first.product_track_token, second.product_track_token)

    def test_manual_initialization_is_once_per_product_segment(self):
        attempts = ManualInitializationAttemptTracker()

        self.assertTrue(attempts.claim("smart_guitar", 1))
        self.assertFalse(attempts.claim("smart_guitar", 1))
        self.assertTrue(attempts.claim("smart_guitar", 2))
        self.assertTrue(attempts.claim("smart_toy", 1))

    def test_confirmed_lost_exposes_next_segment_for_new_manual_initialization(self):
        decision = grounding_decision(
            product_type="smart_guitar",
            candidates=(candidate(bbox=(620, 500, 850, 650)),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        self.assertEqual(1, self.runtime.next_segment_id("smart_guitar"))
        self.runtime.initialize(
            product_type="smart_guitar",
            decision=decision,
            observed_at=self.now,
            source_seq=1,
        )
        self.runtime.association_miss("smart_guitar")
        self.runtime.association_miss("smart_guitar")

        self.assertEqual(ProductTrackStatus.LOST, self.runtime.status("smart_guitar"))
        self.assertEqual(2, self.runtime.next_segment_id("smart_guitar"))

    def test_product_types_are_isolated(self):
        toy_decision = grounding_decision(
            product_type="smart_toy",
            candidates=(candidate(),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        guitar_candidate = candidate(bbox=(620, 500, 850, 650))
        guitar_decision = grounding_decision(
            product_type="smart_guitar",
            candidates=(guitar_candidate,),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        toy = self.runtime.initialize(
            product_type="smart_toy", decision=toy_decision, observed_at=self.now, source_seq=1
        )
        guitar = self.runtime.initialize(
            product_type="smart_guitar", decision=guitar_decision, observed_at=self.now, source_seq=1
        )
        self.runtime.association_miss("smart_guitar")

        self.assertEqual(ProductTrackStatus.ACTIVE, self.runtime.status("smart_toy"))
        self.assertEqual(ProductTrackStatus.MISSING_PENDING, self.runtime.status("smart_guitar"))
        self.assertNotEqual(toy.product_track_token, guitar.product_track_token)

    def test_products_share_surface_without_fixed_per_product_positions(self):
        toy_candidate = candidate(bbox=(760, 500, 890, 620))
        guitar_candidate = candidate(bbox=(630, 300, 720, 430))
        toy_decision = grounding_decision(
            product_type="smart_toy",
            candidates=(toy_candidate,),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        guitar_decision = grounding_decision(
            product_type="smart_guitar",
            candidates=(guitar_candidate,),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )

        self.assertEqual("accepted", toy_decision.status)
        self.assertEqual("accepted", guitar_decision.status)

    def test_product_is_outside_only_after_leaving_shared_surface(self):
        decision = grounding_decision(
            product_type="smart_toy",
            candidates=(candidate(),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        initial = self.runtime.initialize(
            product_type="smart_toy",
            decision=decision,
            observed_at=self.now,
            source_seq=1,
        )
        outside = self.runtime.observe(
            product_type="smart_toy",
            observed_at=self.now,
            source_seq=2,
            candidate=candidate(bbox=(50, 100, 220, 250)),
        )

        self.assertEqual(initial.product_track_token, outside.product_track_token)
        self.assertEqual(ProductDisplayState.OUTSIDE_DISPLAY, outside.display_state)

    def test_surface_gate_is_initialization_only_not_active_continuation(self):
        initial_decision = grounding_decision(
            product_type="smart_toy",
            candidates=(candidate(),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        outside_candidate = candidate(local_id=99, bbox=(50, 100, 220, 250))
        outside_initialization = grounding_decision(
            product_type="smart_toy",
            candidates=(outside_candidate,),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        initial = self.runtime.initialize(
            product_type="smart_toy",
            decision=initial_decision,
            observed_at=self.now,
            source_seq=1,
        )

        continuation = self.runtime.observe(
            product_type="smart_toy",
            observed_at=self.now,
            source_seq=2,
            candidate=outside_candidate,
        )

        self.assertEqual("grounding_unstable", outside_initialization.status)
        self.assertEqual(
            "centroid_outside_product_display_surface",
            outside_initialization.reason,
        )
        self.assertEqual(initial.product_track_id, continuation.product_track_id)
        self.assertEqual(initial.product_track_token, continuation.product_track_token)
        self.assertEqual(ProductDisplayState.OUTSIDE_DISPLAY, continuation.display_state)

    def test_candidate_outside_shared_surface_is_rejected(self):
        decision = grounding_decision(
            product_type="smart_guitar",
            candidates=(candidate(bbox=(50, 100, 220, 250)),),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )

        self.assertEqual("grounding_unstable", decision.status)
        self.assertEqual(
            "centroid_outside_product_display_surface",
            decision.reason,
        )

    def test_formal_config_has_one_surface_and_no_persisted_manual_bbox(self):
        config = json.loads(
            (Path(__file__).parents[1] / "product_cameras.cam02.stage7c.json")
            .read_text(encoding="utf-8")
        )

        self.assertEqual("display", config["product_display_surface"]["area_type"])
        self.assertNotIn("manual_init_bbox", json.dumps(config))
        for product in config["products"].values():
            self.assertNotIn("display_zone", product)

    def test_manual_bbox_contract_uses_pixel_xyxy_and_exact_ai_size(self):
        self.assertEqual(
            [[672 / 1280, 520 / 720, 190 / 1280, 72 / 720]],
            pixel_xyxy_to_normalized_xywh(
                (672, 520, 862, 592), frame_width=1280, frame_height=720
            ),
        )
        with self.assertRaisesRegex(ValueError, "1280x720"):
            pixel_xyxy_to_normalized_xywh(
                (672, 520, 862, 592), frame_width=2560, frame_height=1440
            )


class DualProductTrackingTests(unittest.TestCase):
    def setUp(self):
        self.surface = ProductDisplaySurface(
            ((0.48, 0.40), (0.72, 0.40), (0.72, 0.95), (0.48, 0.95)),
        )
        self.runtime = MultiProductTrackRuntime(
            camera_id="cam02",
            runtime_instance_id="stage7c6-test",
            products={"smart_toy_a": "smart_toy", "smart_toy_b": "smart_toy"},
            product_display_surface=self.surface,
        )
        self.now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        self.toy_a = candidate(11, (650, 330, 720, 430))
        self.toy_b = candidate(22, (790, 480, 860, 580))

    def initialize(self):
        return self.runtime.initialize_assignments(
            assignments={"smart_toy_a": self.toy_a, "smart_toy_b": self.toy_b},
            observed_at=self.now,
            source_seq=1,
            grounding_candidate_count=2,
        )

    def test_initialization_accepts_multiple_surface_candidates_after_operator_mapping(self):
        credible = initialization_candidates(
            candidates=(self.toy_a, self.toy_b),
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )
        results = self.initialize()

        self.assertEqual(2, len(credible))
        self.assertEqual(
            {"smart_toy_a", "smart_toy_b"}, {item.product_key for item in results}
        )
        self.assertTrue(all(item.product_type == "smart_toy" for item in results))
        self.assertEqual(
            {
                "product:cam02:stage7c6-test:smart_toy_a:1",
                "product:cam02:stage7c6-test:smart_toy_b:1",
            },
            {item.product_track_token for item in results},
        )

    def test_global_overlap_assignment_ignores_candidate_order_and_local_ids(self):
        initialized = {item.product_key: item.product_track_token for item in self.initialize()}
        current_b = candidate(101, (790, 480, 860, 580))
        current_a = candidate(202, (650, 330, 720, 430))

        results = self.runtime.process_window(
            candidates=(current_b, current_a),
            observed_at=self.now,
            source_seq=10,
            window_no=1,
        )
        by_key = {item.product_key: item for item in results}

        self.assertEqual(202, by_key["smart_toy_a"].local_obj_id)
        self.assertEqual(101, by_key["smart_toy_b"].local_obj_id)
        self.assertEqual(initialized["smart_toy_a"], by_key["smart_toy_a"].product_track_token)
        self.assertEqual(initialized["smart_toy_b"], by_key["smart_toy_b"].product_track_token)
        self.assertTrue(all(item.association_method == "overlap" for item in results))

    def test_global_assignment_does_not_give_one_candidate_to_both_tracks(self):
        shared = candidate(99, (790, 480, 860, 580))
        associations = associate_candidates_globally(
            previous_candidates={"smart_toy_a": self.toy_a, "smart_toy_b": self.toy_b},
            candidates=(shared,),
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )

        assigned = [item for item in associations if item.success]
        self.assertEqual(1, len(assigned))
        self.assertEqual("smart_toy_b", assigned[0].product_key)
        self.assertEqual(99, assigned[0].candidate.local_obj_id)

    def test_geometry_fallback_keeps_both_instances_in_one_global_assignment(self):
        self.initialize()
        moved_a = candidate(31, (720, 330, 790, 430))
        moved_b = candidate(32, (840, 480, 910, 580))

        results = self.runtime.process_window(
            candidates=(moved_b, moved_a),
            observed_at=self.now,
            source_seq=20,
            window_no=1,
        )
        by_key = {item.product_key: item for item in results}

        self.assertEqual(31, by_key["smart_toy_a"].local_obj_id)
        self.assertEqual(32, by_key["smart_toy_b"].local_obj_id)
        self.assertEqual("geometry", by_key["smart_toy_a"].association_method)
        self.assertEqual("overlap", by_key["smart_toy_b"].association_method)

    def test_equal_global_assignments_are_ambiguous_and_do_not_guess_identity(self):
        self.initialize()
        duplicate_one = candidate(41, (715, 405, 785, 505))
        duplicate_two = candidate(42, (715, 405, 785, 505))

        results = self.runtime.process_window(
            candidates=(duplicate_one, duplicate_two),
            observed_at=self.now,
            source_seq=30,
            window_no=1,
        )

        self.assertTrue(all(item.association_ambiguous for item in results))
        self.assertTrue(all(not item.association_success for item in results))
        self.assertTrue(all(item.local_obj_id is None for item in results))
        self.assertTrue(all(item.display_state is ProductDisplayState.UNOBSERVED for item in results))
        self.assertTrue(all(item.track_status is ProductTrackStatus.MISSING_PENDING for item in results))

    def test_default_dual_tolerance_requires_three_misses_before_lost(self):
        initialized = {item.product_key: item.product_track_token for item in self.initialize()}
        first = self.runtime.process_window(
            candidates=(), observed_at=self.now, source_seq=40, window_no=1
        )
        second = self.runtime.process_window(
            candidates=(), observed_at=self.now, source_seq=50, window_no=2
        )
        third = self.runtime.process_window(
            candidates=(), observed_at=self.now, source_seq=60, window_no=3
        )

        self.assertTrue(all(item.track_status is ProductTrackStatus.MISSING_PENDING for item in first))
        self.assertTrue(all(item.track_status is ProductTrackStatus.MISSING_PENDING for item in second))
        self.assertTrue(all(item.consecutive_missed_windows == 2 for item in second))
        self.assertTrue(all(item.tracking_lost for item in third))
        self.assertTrue(all(item.consecutive_missed_windows == 3 for item in third))
        self.assertEqual(
            initialized,
            {item.product_key: item.product_track_token for item in third},
        )

    def test_two_misses_recover_to_same_segment_and_token_before_third_miss(self):
        initialized = {item.product_key: item for item in self.initialize()}
        self.runtime.process_window(
            candidates=(), observed_at=self.now, source_seq=40, window_no=1
        )
        pending = self.runtime.process_window(
            candidates=(), observed_at=self.now, source_seq=50, window_no=2
        )
        recovered = self.runtime.process_window(
            candidates=(self.toy_a, self.toy_b),
            observed_at=self.now,
            source_seq=60,
            window_no=3,
        )
        by_key = {item.product_key: item for item in recovered}

        self.assertTrue(all(item.track_status is ProductTrackStatus.MISSING_PENDING for item in pending))
        self.assertTrue(all(item.consecutive_missed_windows == 2 for item in pending))
        for product_key, result in by_key.items():
            self.assertEqual(ProductTrackStatus.ACTIVE, result.track_status)
            self.assertEqual(0, result.consecutive_missed_windows)
            self.assertTrue(result.recovered_from_missing_pending)
            self.assertEqual(2, result.recovered_after_missed_windows)
            self.assertEqual(
                initialized[product_key].product_track_id, result.product_track_id
            )
            self.assertEqual(
                initialized[product_key].product_track_token, result.product_track_token
            )

    def test_ambiguous_second_miss_remains_pending_and_never_forces_a_candidate(self):
        self.initialize()
        duplicate_one = candidate(41, (715, 405, 785, 505))
        duplicate_two = candidate(42, (715, 405, 785, 505))
        self.runtime.process_window(
            candidates=(duplicate_one, duplicate_two),
            observed_at=self.now,
            source_seq=30,
            window_no=1,
        )
        second = self.runtime.process_window(
            candidates=(duplicate_one, duplicate_two),
            observed_at=self.now,
            source_seq=40,
            window_no=2,
        )

        self.assertTrue(all(item.association_ambiguous for item in second))
        self.assertTrue(all(not item.association_success for item in second))
        self.assertTrue(all(item.local_obj_id is None for item in second))
        self.assertTrue(all(item.track_status is ProductTrackStatus.MISSING_PENDING for item in second))
        self.assertTrue(all(item.consecutive_missed_windows == 2 for item in second))
        third = self.runtime.process_window(
            candidates=(duplicate_one, duplicate_two),
            observed_at=self.now,
            source_seq=50,
            window_no=3,
        )
        self.assertTrue(all(item.track_status is ProductTrackStatus.LOST for item in third))
        self.assertTrue(all(item.consecutive_missed_windows == 3 for item in third))

    def _runtime_at_two_miss_recovery_boundary(self):
        runtime = MultiProductTrackRuntime(
            camera_id="cam02",
            runtime_instance_id="stage7c6-anchor-test",
            products={"smart_toy_a": "smart_toy", "smart_toy_b": "smart_toy"},
            product_display_surface=self.surface,
        )
        stale_a = candidate(11, (650, 400, 720, 500))
        active_b = candidate(22, (640, 320, 820, 470))
        initialized = runtime.initialize_assignments(
            assignments={"smart_toy_a": stale_a, "smart_toy_b": active_b},
            observed_at=self.now,
            source_seq=1,
            grounding_candidate_count=2,
        )
        runtime.process_window(
            candidates=(candidate(101, (640, 320, 820, 470)),),
            observed_at=self.now,
            source_seq=10,
            window_no=1,
        )
        second = runtime.process_window(
            candidates=(candidate(202, (640, 320, 820, 470)),),
            observed_at=self.now,
            source_seq=20,
            window_no=2,
        )
        return runtime, {item.product_key: item for item in initialized}, {
            item.product_key: item for item in second
        }

    def test_active_anchor_locks_its_candidate_before_recovery_uses_residual(self):
        runtime, initialized, pending = self._runtime_at_two_miss_recovery_boundary()
        physical_a = candidate(303, (640, 330, 725, 400))
        physical_b = candidate(404, (650, 390, 820, 520))

        recovered = runtime.process_window(
            candidates=(physical_b, physical_a),
            observed_at=self.now,
            source_seq=30,
            window_no=3,
        )
        by_key = {item.product_key: item for item in recovered}

        self.assertEqual(ProductTrackStatus.MISSING_PENDING, pending["smart_toy_a"].track_status)
        self.assertEqual(2, pending["smart_toy_a"].consecutive_missed_windows)
        self.assertEqual("active_anchor", by_key["smart_toy_b"].association_role)
        self.assertEqual(404, by_key["smart_toy_b"].local_obj_id)
        self.assertEqual((404,), by_key["smart_toy_b"].anchor_locked_candidate_ids)
        self.assertEqual("recovery", by_key["smart_toy_a"].association_role)
        self.assertEqual(303, by_key["smart_toy_a"].local_obj_id)
        self.assertEqual((404,), by_key["smart_toy_a"].anchor_locked_candidate_ids)
        self.assertTrue(by_key["smart_toy_a"].recovery_attempted)
        self.assertEqual(1, by_key["smart_toy_a"].recovery_candidate_count)
        self.assertTrue(by_key["smart_toy_a"].recovery_success)
        self.assertIsNone(by_key["smart_toy_a"].recovery_rejected_reason)
        self.assertTrue(by_key["smart_toy_a"].recovered_from_missing_pending)
        self.assertEqual(2, by_key["smart_toy_a"].recovered_after_missed_windows)
        self.assertEqual(0, by_key["smart_toy_a"].consecutive_missed_windows)
        for product_key in ("smart_toy_a", "smart_toy_b"):
            self.assertEqual(
                initialized[product_key].product_track_id,
                by_key[product_key].product_track_id,
            )
            self.assertEqual(
                initialized[product_key].product_track_token,
                by_key[product_key].product_track_token,
            )

    def test_anchor_first_recovery_ignores_candidate_order_and_local_id_changes(self):
        assignments = []
        for reverse, a_id, b_id in ((False, 303, 404), (True, 9003, 9004)):
            runtime, _initialized, _pending = self._runtime_at_two_miss_recovery_boundary()
            physical_a = candidate(a_id, (640, 330, 725, 400))
            physical_b = candidate(b_id, (650, 390, 820, 520))
            current = (physical_a, physical_b) if reverse else (physical_b, physical_a)
            results = runtime.process_window(
                candidates=current,
                observed_at=self.now,
                source_seq=30,
                window_no=3,
            )
            assignments.append({item.product_key: item.bbox for item in results})

        self.assertEqual(assignments[0], assignments[1])
        self.assertEqual((640.0, 330.0, 725.0, 400.0), assignments[0]["smart_toy_a"])
        self.assertEqual((650.0, 390.0, 820.0, 520.0), assignments[0]["smart_toy_b"])

    def test_active_anchor_ambiguity_blocks_recovery_identity_reshuffle(self):
        runtime, _initialized, _pending = self._runtime_at_two_miss_recovery_boundary()
        duplicate_one = candidate(303, (640, 320, 820, 470))
        duplicate_two = candidate(404, (640, 320, 820, 470))

        results = runtime.process_window(
            candidates=(duplicate_one, duplicate_two),
            observed_at=self.now,
            source_seq=30,
            window_no=3,
        )
        by_key = {item.product_key: item for item in results}

        self.assertTrue(by_key["smart_toy_b"].association_ambiguous)
        self.assertFalse(by_key["smart_toy_a"].association_success)
        self.assertEqual("recovery", by_key["smart_toy_a"].association_role)
        self.assertFalse(by_key["smart_toy_a"].recovery_attempted)
        self.assertEqual(
            "active_anchor_ambiguous",
            by_key["smart_toy_a"].recovery_rejected_reason,
        )
        self.assertEqual(ProductTrackStatus.LOST, by_key["smart_toy_a"].track_status)
        self.assertEqual(3, by_key["smart_toy_a"].consecutive_missed_windows)
        self.assertIsNone(by_key["smart_toy_a"].local_obj_id)

    def test_continuous_invalid_recovery_reaches_lost_on_third_miss(self):
        runtime, _initialized, _pending = self._runtime_at_two_miss_recovery_boundary()
        physical_b = candidate(404, (640, 320, 820, 470))
        untrusted = candidate(303, (50, 50, 120, 120))

        results = runtime.process_window(
            candidates=(untrusted, physical_b),
            observed_at=self.now,
            source_seq=30,
            window_no=3,
        )
        by_key = {item.product_key: item for item in results}

        self.assertEqual(404, by_key["smart_toy_b"].local_obj_id)
        self.assertTrue(by_key["smart_toy_a"].recovery_attempted)
        self.assertEqual(1, by_key["smart_toy_a"].recovery_candidate_count)
        self.assertFalse(by_key["smart_toy_a"].recovery_success)
        self.assertEqual(
            "no_valid_recovery_candidate",
            by_key["smart_toy_a"].recovery_rejected_reason,
        )
        self.assertEqual(ProductTrackStatus.LOST, by_key["smart_toy_a"].track_status)
        self.assertEqual(3, by_key["smart_toy_a"].consecutive_missed_windows)

    def test_insufficient_candidate_count_records_real_recovery_miss(self):
        _runtime, _initialized, pending = self._runtime_at_two_miss_recovery_boundary()
        result = pending["smart_toy_a"]

        self.assertEqual("recovery", result.association_role)
        self.assertTrue(result.recovery_attempted)
        self.assertEqual(0, result.recovery_candidate_count)
        self.assertFalse(result.recovery_success)
        self.assertEqual(
            "insufficient_candidates_for_tracks",
            result.recovery_rejected_reason,
        )
        self.assertEqual(2, result.consecutive_missed_windows)

    def test_window_payload_contains_stage7c6_contract_fields(self):
        payload = self.initialize()[0].to_dict()
        required = {
            "timestamp", "window_no", "source_seq", "product_key", "product_type",
            "product_track_id", "product_track_token", "local_obj_id",
            "grounding_candidate_count", "candidate_count", "bbox", "centroid",
            "mask_area", "surface_overlap_ratio", "display_state",
            "association_method", "association_success", "association_ambiguous",
            "track_status", "consecutive_missed_windows", "tracking_lost",
            "processing_seconds", "fresh_lag", "recovered_from_missing_pending",
            "recovered_after_missed_windows", "association_role",
            "anchor_locked_candidate_ids", "recovery_attempted",
            "recovery_candidate_count", "recovery_success",
            "recovery_rejected_reason", "recovery_latency_windows",
            "association_mask_iou", "association_centroid_distance_ratio",
        }
        self.assertTrue(required.issubset(payload))

    def test_formal_config_has_one_text_grounding_group_for_two_toy_instances(self):
        config = json.loads(
            (Path(__file__).parents[1] / "product_cameras.cam02.stage7c.json")
            .read_text(encoding="utf-8")
        )

        self.assertEqual(
            {"smart_toy_a", "smart_toy_b"}, set(config["products"])
        )
        self.assertEqual(
            ["smart_toy_a", "smart_toy_b"],
            config["grounding_groups"]["smart_toy"]["product_keys"],
        )
        self.assertEqual(
            "plush toy", config["grounding_groups"]["smart_toy"]["text_prompt"]
        )
        self.assertTrue(
            all(item["product_type"] == "smart_toy" for item in config["products"].values())
        )
        self.assertEqual(3, config["product_track_lost_after_windows"])


class OfflineProductReplayTests(unittest.TestCase):
    def setUp(self):
        self.timeline = CDETimeline(10, 40, 40, 65, 65, 90)

    def test_offline_timestamp_and_sampling_use_media_time_not_wall_clock(self):
        self.assertEqual(
            tuple(range(0, 40, 2)),
            sampled_source_indices(
                source_fps=10.0,
                source_frame_count=40,
                target_fps=5.0,
            ),
        )
        self.assertEqual(
            datetime(1970, 1, 1, 0, 0, 12, 400000, tzinfo=timezone.utc),
            offline_observed_at(12.4),
        )

    def test_offline_video_source_builds_12_frame_windows_with_four_frame_overlap(self):
        with tempfile.TemporaryDirectory() as root:
            video_path = Path(root) / "replay.avi"
            writer = cv2.VideoWriter(
                str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 36)
            )
            self.assertTrue(writer.isOpened())
            for source_seq in range(40):
                writer.write(np.full((36, 64, 3), source_seq, dtype=np.uint8))
            writer.release()

            with OfflineVideoWindowSource(
                video_path,
                target_fps=5.0,
                window_frames=12,
                overlap_frames=4,
                frame_width=1280,
                frame_height=720,
            ) as source:
                windows = list(source.windows())

        self.assertEqual(2, len(windows))
        self.assertEqual(tuple(range(0, 24, 2)), tuple(item.source_seq for item in windows[0]))
        self.assertEqual(
            (16, 18, 20, 22), tuple(item.source_seq for item in windows[1][:4])
        )
        self.assertEqual((720, 1280), windows[0][0].frame.shape[:2])
        self.assertEqual(3.8, windows[1][-1].video_elapsed_seconds)

    def test_cde_timeline_classifies_positions_surface_handheld_and_returned(self):
        expected = {
            5: ("INITIALIZATION", "stationary_identity_confirmation"),
            10: ("C", "toy_b_position_1"),
            20: ("C", "toy_b_position_2"),
            30: ("C", "toy_b_position_3"),
            40: ("D", "toy_a_surface"),
            50: ("D", "toy_a_handheld"),
            55: ("D", "toy_a_returned"),
            65: ("E", "toy_b_surface"),
            75: ("E", "toy_b_handheld"),
            80: ("E", "toy_b_returned"),
            90: ("COMPLETE", "after_test_e"),
        }
        self.assertEqual(
            expected,
            {
                elapsed: (
                    self.timeline.classify(elapsed).test_case,
                    self.timeline.classify(elapsed).phase,
                )
                for elapsed in expected
            },
        )

    def test_live_source_contract_remains_the_default_without_video_arguments(self):
        validate_probe_source(source="live", video_path=None, timeline=None)
        with self.assertRaisesRegex(ValueError, "live mode"):
            validate_probe_source(
                source="live",
                video_path=Path("recording.mp4"),
                timeline=self.timeline,
            )

    def test_each_window_can_execute_text_grounding_only_once(self):
        guard = SingleTextGroundingPerWindow()
        calls = []

        self.assertEqual("plush toy", guard.run(7, lambda: calls.append(7) or "plush toy"))
        with self.assertRaisesRegex(RuntimeError, "one Text Re-Ground"):
            guard.run(7, lambda: calls.append(7))

        self.assertEqual([7], calls)
        self.assertEqual(1, guard.grounding_count)

    def test_summary_is_split_by_cde_and_does_not_fake_visual_id_swap_zero(self):
        rows, associations, initial_tokens = self._summary_fixture()
        summary = build_cde_summary(
            rows=rows,
            window_associations=associations,
            initial_tokens=initial_tokens,
        )

        self.assertEqual({"C", "D", "E"}, set(summary["tests"]))
        self.assertEqual(1.0, summary["tests"]["D"]["smart_toy_a"]["fresh_mask_coverage"])
        self.assertEqual(1, summary["tests"]["E"]["smart_toy_b"]["segment_count"])
        self.assertIsNone(summary["tests"]["C"]["smart_toy_a"]["id_swap_count"])
        self.assertEqual("pending", summary["identity_verification"]["visual_review_status"])
        self.assertEqual("FAIL", summary["acceptance"]["overall_result"])

    def test_observation_coverage_is_separate_from_token_and_identity_continuity(self):
        rows, associations, initial_tokens = self._occlusion_summary_fixture()
        summary = build_cde_summary(
            rows=rows,
            window_associations=associations,
            initial_tokens=initial_tokens,
        )
        toy_a = summary["tests"]["E"]["smart_toy_a"]

        self.assertAlmostEqual(74 / 90, toy_a["observation_coverage"])
        self.assertAlmostEqual(74 / 90, toy_a["product_track_continuity_rate"])
        self.assertTrue(toy_a["deprecated_for_identity_acceptance"])
        self.assertEqual(1.0, toy_a["token_continuity_rate"])
        self.assertEqual(0, toy_a["token_change_count"])
        self.assertEqual(0, toy_a["segment_change_count"])
        self.assertEqual("NEEDS_VISUAL_REVIEW", toy_a["identity_continuity_status"])
        self.assertIsNone(toy_a["visual_id_swap_count"])
        self.assertIsNone(toy_a["safe_recovery_success"])
        self.assertEqual(
            1,
            summary["acceptance"]["e_temporary_missing_recovery_count"],
        )
        self.assertEqual(
            "advisory_occlusion_metric",
            summary["scenario_motion_evidence"]["E"]["acceptance_role"],
        )
        self.assertEqual("NEEDS_REVIEW", summary["acceptance"]["overall_result"])

    def test_e_coverage_below_ninety_does_not_fail_verified_safe_identity(self):
        rows, associations, initial_tokens = self._occlusion_summary_fixture()
        summary = build_cde_summary(
            rows=rows,
            window_associations=associations,
            initial_tokens=initial_tokens,
            visually_verified_id_swap_count=0,
            active_anchor_corruption_count=0,
            safe_recovery_success=True,
        )

        self.assertAlmostEqual(
            74 / 90,
            summary["tests"]["E"]["smart_toy_a"]["observation_coverage"],
        )
        self.assertEqual("PASS", summary["acceptance"]["overall_result"])
        self.assertEqual(
            "PASS",
            summary["tests"]["E"]["smart_toy_a"]["identity_continuity_status"],
        )

    def test_visual_swap_or_unsafe_recovery_is_always_a_hard_failure(self):
        rows, associations, initial_tokens = self._occlusion_summary_fixture()
        swapped = build_cde_summary(
            rows=rows,
            window_associations=associations,
            initial_tokens=initial_tokens,
            visually_verified_id_swap_count=1,
            active_anchor_corruption_count=1,
            safe_recovery_success=False,
        )
        unsafe = build_cde_summary(
            rows=rows,
            window_associations=associations,
            initial_tokens=initial_tokens,
            visually_verified_id_swap_count=0,
            active_anchor_corruption_count=0,
            safe_recovery_success=False,
        )

        self.assertEqual("FAIL", swapped["acceptance"]["overall_result"])
        self.assertEqual("FAIL", unsafe["acceptance"]["overall_result"])

    def test_report_uses_summary_and_lists_specific_ambiguous_and_recovery_windows(self):
        rows, associations, initial_tokens = self._summary_fixture()
        associations.extend((
            {
                "window_no": 17,
                "test_case": "D",
                "product_key": "smart_toy_a",
                "association_success": False,
                "association_ambiguous": True,
                "track_status": "MISSING_PENDING",
            },
            {
                "window_no": 18,
                "test_case": "D",
                "product_key": "smart_toy_a",
                "association_success": True,
                "association_ambiguous": False,
                "association_method": "geometry",
                "track_status": "ACTIVE",
                "recovered_from_missing_pending": True,
                "recovered_after_missed_windows": 2,
            },
        ))
        summary = build_cde_summary(
            rows=rows,
            window_associations=associations,
            initial_tokens=initial_tokens,
        )
        summary.update({
            "metadata": {
                "runtime_id": "offline-test",
                "source_video": "stage7c6_CDE.mp4",
                "video_duration_seconds": 90.0,
                "resolution": [1280, 720],
                "sample_fps": 5.0,
                "window_frames": 12,
                "overlap_frames": 4,
                "text_prompt": "plush toy",
                "camera_id": "cam02",
                "gpu": "test-gpu",
            },
            "window_associations": associations,
            "artifacts": {
                "summary": "summary.json",
                "observations": "observations.jsonl",
                "overlay_video": "mask_overlay.mp4",
                "keyframes": "keyframes",
            },
        })

        report = render_cde_report(summary)

        self.assertIn("**FAIL**", report)
        self.assertIn("Window 17: smart_toy_a association ambiguous", report)
        self.assertIn("Window 18: smart_toy_a recovered by geometry", report)
        self.assertIn("after 2 missed windows", report)
        self.assertIn("Visual A/B ID swap review is pending", report)
        self.assertNotIn("PICK_UP / HOLDING / PUT_DOWN event", report)

    @staticmethod
    def _summary_fixture():
        initial_tokens = {
            "smart_toy_a": "product:cam02:offline:smart_toy_a:1",
            "smart_toy_b": "product:cam02:offline:smart_toy_b:1",
        }
        rows = []
        associations = []
        for window_no, test_case in enumerate(("C", "D", "E"), start=1):
            for index, product_key in enumerate(("smart_toy_a", "smart_toy_b")):
                rows.append({
                    "test_case": test_case,
                    "product_key": product_key,
                    "mask_present": True,
                    "product_track_token": initial_tokens[product_key],
                    "product_track_id": 1,
                    "centroid_pixel": [700 + index * 50 + window_no * 3, 500],
                })
                associations.append({
                    "window_no": window_no,
                    "test_case": test_case,
                    "product_key": product_key,
                    "association_eligible": True,
                    "association_success": True,
                    "association_ambiguous": False,
                    "association_method": "overlap",
                    "track_status": "ACTIVE",
                    "tracking_lost": False,
                })
        return rows, associations, initial_tokens

    @staticmethod
    def _occlusion_summary_fixture():
        initial_tokens = {
            "smart_toy_a": "product:cam02:offline:smart_toy_a:1",
            "smart_toy_b": "product:cam02:offline:smart_toy_b:1",
        }
        rows = []
        associations = []
        for test_case, frame_count in (("C", 90), ("D", 90), ("E", 90)):
            for product_key in ("smart_toy_a", "smart_toy_b"):
                for frame_index in range(frame_count):
                    occluded = (
                        test_case == "E"
                        and product_key == "smart_toy_a"
                        and 30 <= frame_index < 46
                    )
                    rows.append({
                        "test_case": test_case,
                        "product_key": product_key,
                        "mask_present": not occluded,
                        "product_track_token": initial_tokens[product_key],
                        "product_track_id": 1,
                        "centroid_pixel": [
                            700 + (40 if product_key.endswith("_b") else 0),
                            500,
                        ] if not occluded else None,
                        "source_seq": frame_index,
                    })
                associations.append({
                    "window_no": 1,
                    "test_case": test_case,
                    "product_key": product_key,
                    "association_eligible": True,
                    "association_success": True,
                    "association_ambiguous": False,
                    "association_method": "overlap",
                    "track_status": "ACTIVE",
                    "tracking_lost": False,
                })
        associations.extend((
            {
                "window_no": 64,
                "test_case": "E",
                "product_key": "smart_toy_a",
                "association_eligible": True,
                "association_success": False,
                "association_ambiguous": False,
                "association_method": "none",
                "track_status": "MISSING_PENDING",
                "tracking_lost": False,
            },
            {
                "window_no": 65,
                "test_case": "E",
                "product_key": "smart_toy_a",
                "association_eligible": True,
                "association_success": False,
                "association_ambiguous": False,
                "association_method": "none",
                "track_status": "MISSING_PENDING",
                "tracking_lost": False,
            },
            {
                "window_no": 66,
                "test_case": "E",
                "product_key": "smart_toy_a",
                "association_eligible": True,
                "association_success": True,
                "association_ambiguous": False,
                "association_method": "geometry",
                "track_status": "ACTIVE",
                "tracking_lost": False,
                "recovered_from_missing_pending": True,
                "recovered_after_missed_windows": 2,
                "recovery_latency_windows": 1,
            },
        ))
        return rows, associations, initial_tokens


if __name__ == "__main__":
    unittest.main()
