from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.person_product_association import (
    PersonAssociationObservation,
    PersonProductAssociationConfig,
    PersonProductAssociationRuntime,
    PersonProductAssociationStatus,
    ProductAssociationObservation,
)
from agent.behavior.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


class PersonProductAssociationTests(unittest.TestCase):
    def setUp(self):
        self.started = datetime(2026, 8, 14, 15, 0, 0)
        self.config = PersonProductAssociationConfig(
            frame_width=1280,
            frame_height=720,
            expanded_bbox_horizontal_ratio=0.75,
            expanded_bbox_vertical_ratio=0.25,
            max_distance_ratio=0.18,
            max_bbox_gap_ratio=0.12,
            minimum_candidate_score=0.35,
            ambiguity_margin=0.05,
            association_enter_stable_frames=3,
            association_exit_stable_frames=3,
            association_grace_seconds=1.0,
            association_switch_margin=0.10,
            max_sync_delta_seconds=0.12,
            unknown_role_penalty=0.05,
        )
        self.runtime = PersonProductAssociationRuntime(self.config)

    def person(
        self,
        track_id: int,
        *,
        seconds: float = 0.0,
        source_seq: int = 100,
        bbox=(0.35, 0.18, 0.48, 0.92),
        role: str = "customer",
    ) -> PersonAssociationObservation:
        x1, y1, x2, y2 = bbox
        return PersonAssociationObservation(
            observed_at=self.started + timedelta(seconds=seconds),
            source_seq=source_seq,
            camera_id="cam02",
            track_id=track_id,
            track_token=f"store-1:cam02:person-runtime:{track_id}",
            bbox=bbox,
            centroid=((x1 + x2) / 2, (y1 + y2) / 2),
            foot_point=((x1 + x2) / 2, y2),
            confidence=0.95,
            role=role,
        )

    def product(
        self,
        *,
        key: str = "smart_toy_a",
        token: str | None = None,
        track_id: int = 1,
        seconds: float = 0.0,
        source_seq: int = 100,
        bbox=(0.49, 0.44, 0.57, 0.58),
        centroid=(0.53, 0.51),
        status: ProductTrackStatus = ProductTrackStatus.ACTIVE,
        mask_present: bool = True,
    ) -> ProductAssociationObservation:
        return ProductAssociationObservation(
            observed_at=self.started + timedelta(seconds=seconds),
            source_seq=source_seq,
            camera_id="cam02",
            product_key=key,
            product_type="smart_toy",
            product_track_id=track_id,
            product_track_token=token or f"product:cam02:product-runtime:{key}:{track_id}",
            bbox=bbox if mask_present else None,
            centroid=centroid if mask_present else None,
            track_status=status,
            display_state=(
                ProductDisplayState.IN_DISPLAY
                if mask_present else ProductDisplayState.UNOBSERVED
            ),
            mask_present=mask_present,
        )

    def observe(self, frame: int, people, **product_kwargs):
        seconds = frame * 0.2
        source_seq = 100 + frame * 4
        product = self.product(
            seconds=seconds,
            source_seq=source_seq,
            **product_kwargs,
        )
        normalized_people = tuple(
            self.person(
                item[0],
                seconds=seconds,
                source_seq=source_seq,
                **item[1],
            )
            for item in people
        )
        return self.runtime.process(product=product, persons=normalized_people)

    def associate_p1(self, *, key="smart_toy_a"):
        results = []
        for frame in range(3):
            results.append(self.observe(frame, ((23, {}),), key=key))
        return results

    def test_global_track_token_and_same_source_seq_create_stable_association(self):
        first, second, third = self.associate_p1()

        self.assertEqual(PersonProductAssociationStatus.CANDIDATE, first.status)
        self.assertEqual(1, first.stable_frames)
        self.assertEqual(PersonProductAssociationStatus.CANDIDATE, second.status)
        self.assertEqual(PersonProductAssociationStatus.ASSOCIATED, third.status)
        self.assertEqual(23, third.person_track_id)
        self.assertEqual("store-1:cam02:person-runtime:23", third.person_track_token)
        self.assertEqual(0.0, third.person_product_sync_delta_ms)
        self.assertTrue(third.association_changed)
        self.assertNotIn("local_obj_id", third.to_dict())

    def test_sync_delta_over_limit_and_staff_are_rejected(self):
        product = self.product()
        late = self.person(23, seconds=0.5)
        staff = self.person(24, role="staff")

        result = self.runtime.process(product=product, persons=(late, staff))

        self.assertEqual(PersonProductAssociationStatus.UNASSOCIATED, result.status)
        self.assertEqual(0, result.person_candidate_count)
        reasons = {item.person_track_id: item.reject_reason for item in result.candidate_diagnostics}
        self.assertEqual("sync_delta_exceeded", reasons[23])
        self.assertEqual("staff_role", reasons[24])

    def test_spatial_evidence_uses_product_to_bbox_not_foot_point(self):
        person = self.person(23)
        product = self.product(centroid=(0.50, 0.50))

        result = self.runtime.process(product=product, persons=(person,))
        evidence = result.candidate_diagnostics[0]

        self.assertTrue(evidence.product_centroid_inside_expanded_person_bbox)
        self.assertGreater(evidence.person_product_distance_pixels, 0.0)
        self.assertLess(evidence.person_product_distance_ratio, 0.05)
        self.assertGreater(evidence.bbox_proximity, 0.0)
        self.assertNotEqual(person.foot_point, product.centroid)

    def test_product_centroid_inside_person_bbox_has_zero_distance_and_gap(self):
        person = self.person(23, bbox=(0.35, 0.18, 0.60, 0.92))
        product = self.product(centroid=(0.50, 0.50), bbox=(0.48, 0.45, 0.54, 0.57))

        result = self.runtime.process(product=product, persons=(person,))
        evidence = result.candidate_diagnostics[0]

        self.assertEqual(0.0, evidence.person_product_distance_pixels)
        self.assertEqual(0.0, evidence.person_product_distance_ratio)
        self.assertEqual(1.0, evidence.bbox_proximity)

    def test_single_frame_never_creates_association(self):
        result = self.observe(0, ((23, {}),))

        self.assertEqual(PersonProductAssociationStatus.CANDIDATE, result.status)
        self.assertFalse(result.association_changed)

    def test_two_nearly_equal_people_are_ambiguous_and_fail_closed(self):
        result = self.observe(0, (
            (23, {"bbox": (0.35, 0.18, 0.48, 0.92)}),
            (24, {"bbox": (0.58, 0.18, 0.71, 0.92)}),
        ))

        self.assertEqual(PersonProductAssociationStatus.UNASSOCIATED, result.status)
        self.assertTrue(result.ambiguous)
        self.assertIsNone(result.person_track_token)

    def test_one_person_can_associate_with_two_products(self):
        for frame in range(3):
            p1 = ((23, {}),)
            result_a = self.observe(frame, p1, key="smart_toy_a")
            result_b = self.observe(
                frame,
                p1,
                key="smart_toy_b",
                bbox=(0.50, 0.58, 0.59, 0.70),
                centroid=(0.545, 0.64),
            )

        self.assertEqual(PersonProductAssociationStatus.ASSOCIATED, result_a.status)
        self.assertEqual(PersonProductAssociationStatus.ASSOCIATED, result_b.status)
        self.assertEqual(result_a.person_track_token, result_b.person_track_token)

    def test_incumbent_anchor_blocks_single_frame_closer_challenger(self):
        self.associate_p1()
        result = self.observe(3, (
            (23, {}),
            (24, {"bbox": (0.47, 0.18, 0.60, 0.92)}),
        ))

        self.assertEqual(PersonProductAssociationStatus.ASSOCIATED, result.status)
        self.assertEqual(23, result.person_track_id)
        self.assertEqual("incumbent_anchor", result.method)
        self.assertTrue(result.person_switch_candidate)
        self.assertFalse(result.association_changed)

    def test_release_then_stable_new_person_never_directly_switches(self):
        self.associate_p1()
        far_p1 = {"bbox": (0.02, 0.10, 0.12, 0.90)}
        near_p2 = {"bbox": (0.47, 0.18, 0.60, 0.92)}
        grace_one = self.observe(3, ((23, far_p1), (24, near_p2)))
        grace_two = self.observe(4, ((23, far_p1), (24, near_p2)))
        released = self.observe(8, ((24, near_p2),))

        self.assertEqual(23, grace_one.person_track_id)
        self.assertTrue(grace_one.association_grace_active)
        self.assertEqual(23, grace_two.person_track_id)
        self.assertTrue(released.association_released)
        self.assertIsNone(released.person_track_id)
        self.assertEqual("incumbent_grace_expired", released.association_release_reason)

        candidate = self.observe(9, ((24, near_p2),))
        self.observe(10, ((24, near_p2),))
        associated = self.observe(11, ((24, near_p2),))
        self.assertEqual(PersonProductAssociationStatus.CANDIDATE, candidate.status)
        self.assertEqual(24, associated.person_track_id)
        self.assertEqual(PersonProductAssociationStatus.ASSOCIATED, associated.status)

    def test_product_missing_pending_and_safe_recovery_keep_same_actor(self):
        associated = self.associate_p1()[-1]
        missing_one = self.observe(
            3,
            (),
            status=ProductTrackStatus.MISSING_PENDING,
            mask_present=False,
        )
        missing_two = self.observe(
            4,
            (),
            status=ProductTrackStatus.MISSING_PENDING,
            mask_present=False,
        )
        recovered = self.observe(5, ((23, {}),))

        self.assertEqual(associated.person_track_token, missing_one.person_track_token)
        self.assertTrue(missing_one.association_grace_active)
        self.assertTrue(missing_two.association_grace_active)
        self.assertEqual(associated.association_started_at, recovered.association_started_at)
        self.assertTrue(recovered.association_recovered)
        self.assertFalse(recovered.association_changed)

    def test_product_lost_releases_and_new_segment_does_not_inherit(self):
        self.associate_p1()
        lost = self.observe(
            3,
            (),
            status=ProductTrackStatus.LOST,
            mask_present=False,
        )
        new_segment = self.observe(
            4,
            ((23, {}),),
            token="product:cam02:product-runtime:smart_toy_a:2",
            track_id=2,
        )

        self.assertTrue(lost.association_released)
        self.assertEqual("product_lost", lost.association_release_reason)
        self.assertEqual(PersonProductAssociationStatus.CANDIDATE, new_segment.status)
        self.assertFalse(new_segment.association_changed)

    def test_person_temporary_miss_uses_grace_and_recovers_same_association(self):
        associated = self.associate_p1()[-1]
        missing = self.observe(3, ())
        recovered = self.observe(4, ((23, {}),))

        self.assertTrue(missing.association_grace_active)
        self.assertEqual(associated.person_track_token, missing.person_track_token)
        self.assertEqual(associated.association_started_at, recovered.association_started_at)
        self.assertTrue(recovered.association_recovered)

    def test_person_missing_beyond_grace_releases(self):
        self.associate_p1()
        self.observe(3, ())
        self.observe(4, ())
        released = self.observe(8, ())

        self.assertTrue(released.association_released)
        self.assertEqual("incumbent_grace_expired", released.association_release_reason)
        self.assertEqual(PersonProductAssociationStatus.UNASSOCIATED, released.status)

    def test_output_is_association_only_and_contains_no_stage9_events(self):
        payload = self.observe(0, ((23, {}),)).to_dict()
        serialized = str(payload)

        self.assertNotIn("PICK_UP", serialized)
        self.assertNotIn("HOLDING", serialized)
        self.assertNotIn("PUT_DOWN", serialized)
        required = {
            "timestamp", "source_seq", "camera_id", "product_key",
            "product_track_id", "product_track_token", "product_track_status",
            "product_mask_present", "product_centroid", "product_bbox",
            "person_candidate_count", "associated_person_track_id",
            "associated_person_track_token", "association_status",
            "association_method", "association_distance_ratio",
            "product_inside_expanded_person_bbox", "association_stable_frames",
            "association_ambiguous", "association_started_at",
            "association_age_seconds", "association_grace_active",
            "person_product_sync_delta_ms", "association_changed",
            "association_released", "association_release_reason",
            "candidate_diagnostics",
        }
        self.assertTrue(required.issubset(payload))


if __name__ == "__main__":
    unittest.main()
