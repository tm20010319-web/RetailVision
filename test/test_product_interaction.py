from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.person_product_association import PersonProductAssociationStatus
from agent.behavior.product_interaction import (
    ProductInteractionConfig,
    ProductInteractionEventType,
    ProductInteractionObservation,
    ProductInteractionRuntime,
    ProductInteractionState,
    ProductMotionState,
)
from agent.behavior.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


class ProductInteractionTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 15, 10, 0, 0)
        self.config = ProductInteractionConfig(
            motion_window_frames=5,
            motion_enter_displacement_pixels=12.0,
            motion_exit_displacement_pixels=6.0,
            motion_enter_stable_frames=2,
            motion_exit_stable_frames=3,
            pickup_confirm_frames=2,
            putdown_confirm_frames=3,
            stable_baseline_min_frames=5,
            minimum_holding_seconds=1.0,
            surface_evidence_enabled=True,
            minimum_pickup_confidence=0.80,
            minimum_putdown_confidence=0.60,
        )
        self.runtime = ProductInteractionRuntime(self.config)
        self.seq = 0

    def observation(
        self,
        *,
        x=700.0,
        y=430.0,
        seconds=None,
        product_key="smart_toy_a",
        token=None,
        status=ProductTrackStatus.ACTIVE,
        mask=True,
        surface=1.0,
        actor=True,
        association_grace=False,
        display=ProductDisplayState.IN_DISPLAY,
    ):
        if seconds is None:
            seconds = self.seq * 0.2
        source_seq = self.seq
        self.seq += 1
        token = token or f"product:cam02:runtime:{product_key}:1"
        if mask:
            centroid = (x / 1280.0, y / 720.0)
            bbox = (
                (x - 35) / 1280.0,
                (y - 35) / 720.0,
                (x + 35) / 1280.0,
                (y + 35) / 720.0,
            )
        else:
            centroid = None
            bbox = None
        return ProductInteractionObservation(
            observed_at=self.start + timedelta(seconds=seconds),
            source_seq=source_seq,
            camera_id="cam02",
            product_key=product_key,
            product_type="smart_toy",
            product_track_id=int(token.rsplit(":", 1)[-1]),
            product_track_token=token,
            product_track_status=status,
            product_mask_present=mask,
            bbox=bbox,
            centroid=centroid,
            display_state=display,
            surface_overlap_ratio=surface if mask else None,
            associated_person_track_id=23 if actor else None,
            associated_person_track_token=(
                "store-1:cam02:person-runtime:23" if actor else None
            ),
            person_product_association_status=(
                PersonProductAssociationStatus.ASSOCIATED
                if actor else PersonProductAssociationStatus.UNASSOCIATED
            ),
            association_grace_active=association_grace,
        )

    def stable_baseline(self, count=8, **kwargs):
        return [self.runtime.process(self.observation(**kwargs)) for _ in range(count)]

    def pickup(self, product_key="smart_toy_a"):
        self.stable_baseline(product_key=product_key)
        results = []
        for x, y in ((700, 410), (700, 380), (705, 360), (710, 345)):
            results.append(
                self.runtime.process(
                    self.observation(
                        x=x,
                        y=y,
                        product_key=product_key,
                        surface=0.70,
                    )
                )
            )
        return results

    def complete_putdown(self):
        self.pickup()
        for _ in range(8):
            held = self.runtime.process(self.observation(x=710, y=345, surface=0.70))
        self.assertEqual(ProductInteractionState.HELD, held.interaction_state)
        for x, y in ((735, 380), (760, 415), (775, 430), (780, 435)):
            self.runtime.process(self.observation(x=x, y=y, surface=0.90))
        results = []
        for _ in range(8):
            results.append(
                self.runtime.process(self.observation(x=780, y=435, surface=0.99))
            )
        return results

    def test_initial_jitter_is_stable_and_builds_baseline(self):
        results = self.stable_baseline(count=8, x=700, y=430)

        self.assertTrue(all(item.motion.motion_state is ProductMotionState.STABLE for item in results))
        self.assertTrue(results[-1].motion.stable_baseline_ready)
        self.assertEqual(ProductInteractionState.IDLE, results[-1].interaction_state)

    def test_single_frame_motion_spike_does_not_enter_moving(self):
        self.stable_baseline()
        result = self.runtime.process(self.observation(x=750, y=390, surface=0.70))

        self.assertEqual(ProductMotionState.STABLE, result.motion.motion_state)
        self.assertEqual(ProductInteractionState.IDLE, result.interaction_state)
        self.assertFalse(result.events)

    def test_continuous_displacement_enters_moving(self):
        self.stable_baseline()
        self.runtime.process(self.observation(x=750, y=390, surface=0.70))
        result = self.runtime.process(self.observation(x=780, y=350, surface=0.70))

        self.assertEqual(ProductMotionState.MOVING, result.motion.motion_state)
        self.assertGreater(result.motion.rolling_displacement_pixels, 12.0)

    def test_motion_hysteresis_returns_to_stable(self):
        self.stable_baseline()
        self.runtime.process(self.observation(x=750, y=390, surface=1.0))
        self.runtime.process(self.observation(x=780, y=350, surface=1.0))
        results = [
            self.runtime.process(self.observation(x=780, y=350, surface=1.0))
            for _ in range(7)
        ]

        self.assertEqual(ProductMotionState.STABLE, results[-1].motion.motion_state)
        self.assertEqual(ProductInteractionState.IDLE, results[-1].interaction_state)

    def test_missing_is_explicit_motion_state(self):
        self.stable_baseline()
        result = self.runtime.process(
            self.observation(status=ProductTrackStatus.MISSING_PENDING, mask=False)
        )

        self.assertEqual(ProductMotionState.MISSING, result.motion.motion_state)
        self.assertEqual(ProductInteractionState.IDLE, result.interaction_state)

    def test_associated_stable_product_does_not_pick_up(self):
        results = self.stable_baseline()

        self.assertEqual(ProductInteractionState.IDLE, results[-1].interaction_state)
        self.assertFalse(any(result.events for result in results))

    def test_no_actor_association_does_not_pick_up(self):
        self.stable_baseline(actor=False)
        results = [
            self.runtime.process(
                self.observation(x=x, y=y, surface=0.70, actor=False)
            )
            for x, y in ((700, 410), (700, 380), (705, 360), (710, 345))
        ]

        self.assertFalse(any(result.events for result in results))
        self.assertNotEqual(ProductInteractionState.HELD, results[-1].interaction_state)

    def test_pickup_requires_temporal_confirmation(self):
        results = self.pickup()
        pickup_events = [
            event
            for result in results
            for event in result.events
            if event.event_type is ProductInteractionEventType.PICK_UP
        ]

        self.assertEqual(1, len(pickup_events))
        self.assertEqual(ProductInteractionState.HELD, results[-1].interaction_state)

    def test_pickup_emits_one_pickup_and_one_holding_event(self):
        results = self.pickup()
        events = [event for result in results for event in result.events]

        self.assertEqual(
            [
                ProductInteractionEventType.PICK_UP,
                ProductInteractionEventType.HOLDING,
            ],
            [event.event_type for event in events],
        )
        self.assertEqual(events[0].interaction_session_id, events[1].interaction_session_id)
        self.assertEqual(1, events[0].event_sequence)
        self.assertEqual(2, events[1].event_sequence)

    def test_surface_evidence_suppresses_c_like_in_display_reposition(self):
        self.stable_baseline()
        results = [
            self.runtime.process(
                self.observation(x=x, y=y, surface=0.90)
            )
            for x, y in ((700, 410), (700, 380), (705, 360), (710, 345))
        ]

        self.assertFalse(any(result.events for result in results))

    def test_surface_evidence_is_optional_when_disabled(self):
        runtime = ProductInteractionRuntime(
            ProductInteractionConfig(
                surface_evidence_enabled=False,
                minimum_pickup_confidence=0.70,
            )
        )
        for _ in range(8):
            runtime.process(self.observation())
        results = []
        for x, y in ((700, 410), (700, 380), (705, 360), (710, 345)):
            results.append(
                runtime.process(
                    self.observation(x=x, y=y, surface=None)
                )
            )

        self.assertTrue(any(
            event.event_type is ProductInteractionEventType.PICK_UP
            for result in results for event in result.events
        ))

    def test_product_recovery_does_not_create_pickup(self):
        self.stable_baseline()
        self.runtime.process(self.observation(status=ProductTrackStatus.MISSING_PENDING, mask=False))
        results = [self.runtime.process(self.observation(x=700, y=430)) for _ in range(4)]

        self.assertFalse(any(result.events for result in results))
        self.assertTrue(results[0].motion.recovered_from_missing)

    def test_pickup_duplicate_is_zero_while_held(self):
        results = self.pickup()
        for _ in range(12):
            results.append(self.runtime.process(self.observation(x=710, y=345, surface=0.70)))

        self.assertEqual(
            1,
            sum(
                event.event_type is ProductInteractionEventType.PICK_UP
                for result in results for event in result.events
            ),
        )

    def test_held_duration_accumulates(self):
        results = self.pickup()
        results.append(self.runtime.process(self.observation(x=710, y=345, surface=0.70)))
        results.append(self.runtime.process(self.observation(x=710, y=345, surface=0.70)))

        self.assertGreater(results[-1].holding_duration_seconds, 0.0)
        self.assertEqual(ProductInteractionState.HELD, results[-1].interaction_state)

    def test_held_product_missing_uses_grace_without_putdown(self):
        self.pickup()
        results = [
            self.runtime.process(
                self.observation(status=ProductTrackStatus.MISSING_PENDING, mask=False)
            )
            for _ in range(3)
        ]

        self.assertTrue(all(result.held_missing_grace for result in results))
        self.assertTrue(all(not result.events for result in results))
        self.assertTrue(all(result.interaction_session_id for result in results))

    def test_held_same_token_recovery_continues_session(self):
        self.pickup()
        self.runtime.process(self.observation(status=ProductTrackStatus.MISSING_PENDING, mask=False))
        recovered = self.runtime.process(self.observation(x=710, y=345, surface=0.70))

        self.assertEqual(ProductInteractionState.HELD, recovered.interaction_state)
        self.assertTrue(recovered.motion.recovered_from_missing)
        self.assertTrue(recovered.interaction_session_id)

    def test_product_lost_terminates_without_putdown(self):
        self.pickup()
        result = self.runtime.process(
            self.observation(status=ProductTrackStatus.LOST, mask=False)
        )

        self.assertTrue(result.interaction_terminated)
        self.assertEqual("product_lost", result.interaction_terminated_reason)
        self.assertFalse(result.events)
        self.assertIsNone(result.interaction_session_id)

    def test_actor_grace_then_termination_without_putdown(self):
        self.pickup()
        self.runtime.process(self.observation(actor=False, association_grace=True))
        self.runtime.process(self.observation(actor=False, association_grace=True))
        terminated = []
        for _ in range(10):
            result = self.runtime.process(
                self.observation(actor=False, association_grace=False)
            )
            terminated.append(result)

        self.assertTrue(any(item.interaction_terminated for item in terminated))
        termination = next(item for item in terminated if item.interaction_terminated)
        self.assertEqual("actor_lost", termination.interaction_terminated_reason)
        self.assertFalse(termination.events)

    def test_held_motion_stable_then_motion_then_stable_puts_down_once(self):
        results = self.complete_putdown()
        putdowns = [
            event
            for result in results
            for event in result.events
            if event.event_type is ProductInteractionEventType.PUT_DOWN
        ]

        self.assertEqual(1, len(putdowns))
        self.assertEqual(ProductInteractionState.IDLE, results[-1].interaction_state)
        self.assertGreater(putdowns[0].holding_duration_seconds, 1.0)

    def test_putdown_pending_motion_cancels_without_event(self):
        self.pickup()
        for _ in range(8):
            self.runtime.process(self.observation(x=710, y=345, surface=0.70))
        for x, y in ((735, 380), (760, 415), (775, 430), (780, 435)):
            self.runtime.process(self.observation(x=x, y=y, surface=0.90))
        stable = [
            self.runtime.process(self.observation(x=780, y=435, surface=0.99))
        for _ in range(6)
        ]
        self.runtime.process(self.observation(x=810, y=450, surface=0.90))
        moving = self.runtime.process(self.observation(x=840, y=470, surface=0.90))

        self.assertTrue(any(
            result.interaction_state is ProductInteractionState.PUTDOWN_PENDING
            for result in stable
        ))
        self.assertEqual(ProductInteractionState.HELD, moving.interaction_state)
        self.assertFalse(moving.events)

    def test_idle_stable_cannot_putdown(self):
        results = self.stable_baseline()

        self.assertFalse(any(
            event.event_type is ProductInteractionEventType.PUT_DOWN
            for result in results for event in result.events
        ))

    def test_missing_cannot_putdown(self):
        self.pickup()
        results = [
            self.runtime.process(
                self.observation(status=ProductTrackStatus.MISSING_PENDING, mask=False)
            )
            for _ in range(8)
        ]

        self.assertFalse(any(
            event.event_type is ProductInteractionEventType.PUT_DOWN
            for result in results for event in result.events
        ))

    def test_new_product_segment_does_not_inherit_held_state(self):
        self.pickup()
        fresh = self.runtime.process(
            self.observation(
                token="product:cam02:runtime:smart_toy_a:2",
                x=700,
                y=430,
            )
        )

        self.assertEqual(ProductInteractionState.IDLE, fresh.interaction_state)
        self.assertIsNone(fresh.interaction_session_id)
        self.assertFalse(fresh.events)

    def test_two_products_have_independent_sessions(self):
        self.stable_baseline(product_key="smart_toy_a")
        toy_a = self.pickup(product_key="smart_toy_a")[-1]
        self.seq = 0
        toy_b_runtime = ProductInteractionRuntime(self.config)
        for _ in range(8):
            toy_b_runtime.process(self.observation(product_key="smart_toy_b"))
        for x, y in ((600, 400), (560, 360), (540, 340), (530, 330)):
            toy_b = toy_b_runtime.process(
                self.observation(product_key="smart_toy_b", x=x, y=y, surface=0.70)
            )

        self.assertEqual(ProductInteractionState.HELD, toy_a.interaction_state)
        self.assertEqual(ProductInteractionState.HELD, toy_b.interaction_state)
        self.assertNotEqual(toy_a.interaction_session_id, toy_b.interaction_session_id)

    def test_event_id_is_deterministic(self):
        first = ProductInteractionRuntime(self.config)
        second = ProductInteractionRuntime(self.config)
        observations = []
        for _ in range(8):
            observations.append(self.observation())
        for x, y in ((700, 410), (700, 380), (705, 360), (710, 345)):
            observations.append(self.observation(x=x, y=y, surface=0.70))

        first_events = [
            event.event_id
            for item in observations
            for event in first.process(item).events
        ]
        second_events = [
            event.event_id
            for item in observations
            for event in second.process(item).events
        ]

        self.assertEqual(first_events, second_events)
        self.assertEqual(len(first_events), len(set(first_events)))

    def test_event_contract_serializes_without_timeline_control_fields(self):
        events = [event for result in self.pickup() for event in result.events]
        payload = events[0].to_dict()
        serialized = json.dumps(payload)

        self.assertIn("event_id", payload)
        self.assertIn("interaction_session_id", payload)
        self.assertNotIn("test_case", serialized)
        self.assertNotIn("phase", serialized)

    def test_outside_display_does_not_alone_create_pickup(self):
        self.stable_baseline(display=ProductDisplayState.OUTSIDE_DISPLAY)
        results = [
            self.runtime.process(
                self.observation(
                    x=x,
                    y=y,
                    surface=1.0,
                    display=ProductDisplayState.OUTSIDE_DISPLAY,
                )
            )
            for x, y in ((700, 410), (700, 380), (705, 360), (710, 345))
        ]

        self.assertFalse(any(result.events for result in results))

    def test_actor_association_candidate_does_not_create_pickup(self):
        self.stable_baseline()
        # Construct a valid observation, then deliberately downgrade only the
        # Stage 8 association status as the state machine input contract allows.
        observations = [
            self.observation(x=x, y=y, surface=0.70)
            for x, y in ((700, 410), (700, 380), (705, 360), (710, 345))
        ]
        observations = [
            ProductInteractionObservation(
                **{
                    **item.__dict__,
                    "person_product_association_status": (
                        PersonProductAssociationStatus.CANDIDATE
                    ),
                }
            )
            for item in observations
        ]
        results = [self.runtime.process(item) for item in observations]

        self.assertFalse(any(result.events for result in results))

    def test_motion_diagnostics_are_normalized_by_frame_diagonal(self):
        self.stable_baseline()
        self.runtime.process(self.observation(x=750, y=390, surface=0.70))
        result = self.runtime.process(self.observation(x=780, y=350, surface=0.70))

        expected = result.motion.rolling_displacement_pixels / (1280**2 + 720**2) ** 0.5
        self.assertAlmostEqual(expected, result.motion.rolling_displacement_ratio)

    def test_repeated_putdown_is_zero_after_session_completion(self):
        results = self.complete_putdown()
        for _ in range(10):
            results.append(self.runtime.process(self.observation(x=780, y=435, surface=0.99)))

        self.assertEqual(
            1,
            sum(
                event.event_type is ProductInteractionEventType.PUT_DOWN
                for result in results for event in result.events
            ),
        )


if __name__ == "__main__":
    unittest.main()
