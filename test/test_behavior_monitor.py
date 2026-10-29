from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior_monitor import (
    BehaviorMonitor,
    BehaviorMonitorConfig,
    PolygonArea,
    PosePoint,
    TrackObservation,
    ZoneState,
    ZoneTransitionType,
)
from agent.staff_greeting_evaluation import StaffGreetingEvaluation


ENTRANCE = PolygonArea(
    "entrance",
    "entrance",
    ((0.0, 0.2), (0.8, 0.2), (0.8, 0.5), (0.0, 0.5)),
)
GREETING = PolygonArea(
    "reception-greeting",
    "greeting",
    ((0.4, 0.5), (0.8, 0.5), (0.8, 1.0), (0.4, 1.0)),
)
INTERACTION = PolygonArea(
    "display-a-interaction",
    "interaction",
    ((0.4, 0.0), (0.8, 0.0), (0.8, 1.0), (0.4, 1.0)),
)
DISPLAY = PolygonArea(
    "display-a",
    "display",
    ((0.6, 0.1), (0.95, 0.1), (0.95, 0.8), (0.6, 0.8)),
)
DIRECTIONAL_ENTRY = PolygonArea(
    "directional-entry",
    "entrance",
    ((0.1, 0.3), (0.9, 0.3), (0.9, 0.5), (0.1, 0.5)),
)


def customer(
    *,
    track_id=1,
    token="runtime:customer-1",
    foot=(0.2, 0.8),
    world=(2.0, 0.0),
    confidence=0.95,
    wrists=(),
):
    return TrackObservation(
        track_id=track_id,
        track_token=token,
        role="customer",
        foot_point=foot,
        world_point=world,
        confidence=confidence,
        wrists=tuple(wrists),
    )


def staff(
    *,
    foot=(0.9, 0.5),
    world=(0.0, 0.0),
    confidence=0.96,
    role_source="staff_vest_yolo",
    role_confidence=0.91,
):
    return TrackObservation(
        track_id=8,
        track_token="runtime:staff-8",
        role="staff",
        foot_point=foot,
        world_point=world,
        confidence=confidence,
        role_source=role_source,
        role_confidence=role_confidence,
    )


def unresolved(*, role="unknown", foot=(0.2, 0.8)):
    return TrackObservation(
        track_id=3,
        track_token="store-1:entrance-1:runtime-1:3",
        role=role,
        foot_point=foot,
        world_point=(2.0, 0.0),
        confidence=0.94,
    )


class _BehaviorHarness:
    """Exercise the production monitor and Greeting module through one test seam."""

    def __init__(self, **kwargs):
        self._monitor = BehaviorMonitor(**kwargs)
        self._greeting = StaffGreetingEvaluation(
            camera_id=self._monitor.camera_id,
            entrance_area=self._monitor.entrance_area,
            greeting_area=self._monitor.greeting_area,
            config=self._monitor.config,
            behavior_sink=lambda event: None,
            calibration_status=self._monitor.calibration_status,
            ground_calibration_available=self._monitor.ground_calibration_available,
        )

    def process(self, observed_at, observations):
        tracks = list(observations)
        entries, behaviors = self._monitor.process(observed_at, tracks)
        greetings = self._greeting.observe(observed_at, tracks, entries)
        return entries, [*behaviors, *greetings]

    def greeting_debug_state(self, customer_track_token):
        return self._greeting._official._debug_state(customer_track_token)

    def greeting_debug_lines(self, customer_track_token):
        return self._greeting.debug_lines(customer_track_token)

    def greeting_cancellations(self):
        return tuple(self._greeting._cancellation_log)

    def cancel_greetings(self, observed_at, cancel_reason):
        return self._greeting.cancel_all(observed_at, cancel_reason)

    def __getattr__(self, name):
        return getattr(self._monitor, name)


class BehaviorMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 27, 12, 0, 0)
        self.monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            interaction_areas=(INTERACTION,),
            display_areas=(DISPLAY,),
            calibration_status="provisional",
        )

    def _enter(self, *, monitor=None, value=None, staff_tracks=()):
        target = monitor or self.monitor
        inside = value or customer()
        outside = customer(
            track_id=inside.track_id,
            token=inside.track_token,
            foot=(inside.foot_point[0], 0.1),
            world=inside.world_point,
            confidence=inside.confidence,
            wrists=inside.wrists,
        )
        trigger = customer(
            track_id=inside.track_id,
            token=inside.track_token,
            foot=(inside.foot_point[0], 0.35),
            world=inside.world_point,
            confidence=inside.confidence,
            wrists=inside.wrists,
        )
        frames = (
            [outside] * target.config.outside_stable_frames
            + [trigger]
            + [inside] * target.config.inside_stable_frames
        )
        started_at = self.now - timedelta(milliseconds=100 * (len(frames) - 1))
        result = ([], [])
        for offset, observation in enumerate(frames):
            result = target.process(
                started_at + timedelta(milliseconds=offset * 100),
                [observation, *staff_tracks],
            )
        return result

    def _controlled_demo_monitor(self, **overrides):
        values = {
            "greeting_profile": "controlled_demo",
            "greeting_window_seconds": 30.0,
            "greeting_staff_travel_metres": 0.25,
            "greeting_duration_seconds": 1.0,
            "greeting_distance_metres": 1.2,
            "minimum_distance_reduction_metres": 0.35,
            "greeting_area_exit_grace_seconds": 0.8,
            "greeting_area_enter_stable_frames": 3,
            "greeting_area_exit_stable_frames": 5,
        }
        values.update(overrides)
        return _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            config=BehaviorMonitorConfig(**values),
        )

    def _demo_relaxed_monitor(self, **overrides):
        values = {
            "greeting_profile": "demo_relaxed",
            "greeting_window_seconds": 30.0,
            "greeting_staff_travel_metres": 0.10,
            "greeting_staff_toward_customer_metres": 0.05,
            "greeting_duration_seconds": 0.5,
            "greeting_distance_metres": 2.0,
            "minimum_distance_reduction_metres": 0.05,
            "greeting_trend_window_samples": 5,
            "greeting_trend_minimum_samples": 5,
            "greeting_trend_minimum_reduction_metres": 0.05,
            "greeting_trend_decreasing_ratio": 0.5,
            "confirmed_confidence": 0.5,
            "greeting_area_exit_grace_seconds": 0.8,
            "greeting_area_enter_stable_frames": 3,
            "greeting_area_exit_stable_frames": 5,
        }
        values.update(overrides)
        return _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            config=BehaviorMonitorConfig(**values),
        )

    def _directional_monitor(self, **config_overrides):
        return _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=DIRECTIONAL_ENTRY,
            config=BehaviorMonitorConfig(**config_overrides),
        )

    def _process_entry_sides(self, monitor, sides, *, token="runtime:customer-1"):
        points = {
            "outside": (0.5, 0.2),
            "trigger": (0.5, 0.4),
            "inside": (0.5, 0.6),
        }
        emitted = []
        for offset, side in enumerate(sides):
            entries, _ = monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [customer(token=token, foot=points[side])],
            )
            emitted.extend(entries)
        return emitted

    def test_customer_entry_counts_once_and_staff_is_excluded(self):
        first, _ = self._enter(staff_tracks=(staff(),))
        duplicate, _ = self.monitor.process(
            self.now + timedelta(milliseconds=100), [customer(), staff()]
        )

        self.assertEqual(1, len(first))
        self.assertEqual([], duplicate)
        self.assertEqual("runtime:customer-1:entry:1", first[0].entry_key)

    def test_entry_requires_stable_outside_trigger_inside_sequence(self):
        monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=DIRECTIONAL_ENTRY,
            config=BehaviorMonitorConfig(
                outside_stable_frames=3,
                inside_stable_frames=3,
            ),
        )
        observed_entries = []
        frames = [
            customer(foot=(0.5, 0.2)),
            customer(foot=(0.5, 0.2)),
            customer(foot=(0.5, 0.2)),
            customer(foot=(0.5, 0.4)),
            customer(foot=(0.5, 0.6)),
            customer(foot=(0.5, 0.6)),
            customer(foot=(0.5, 0.6)),
        ]
        for offset, observation in enumerate(frames):
            entries, _ = monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [observation],
            )
            observed_entries.extend(entries)

        self.assertEqual(1, len(observed_entries))
        self.assertEqual(
            self.now + timedelta(milliseconds=600),
            observed_entries[0].occurred_at,
        )

    def test_inside_trigger_inside_does_not_create_entry(self):
        entries = self._process_entry_sides(
            self._directional_monitor(),
            ["inside"] * 3 + ["trigger"] + ["inside"] * 3,
        )

        self.assertEqual([], entries)

    def test_inside_trigger_outside_does_not_create_entry(self):
        entries = self._process_entry_sides(
            self._directional_monitor(),
            ["inside"] * 3 + ["trigger"] + ["outside"] * 5,
        )

        self.assertEqual([], entries)

    def test_outside_trigger_outside_does_not_create_entry(self):
        entries = self._process_entry_sides(
            self._directional_monitor(),
            ["outside"] * 3 + ["trigger"] + ["outside"] * 3,
        )

        self.assertEqual([], entries)

    def test_entry_trigger_loitering_does_not_duplicate_entry(self):
        monitor = self._directional_monitor()
        entries = self._process_entry_sides(
            monitor,
            ["outside"] * 3
            + ["trigger", "outside"] * 3
            + ["trigger"]
            + ["inside"] * 3
            + ["trigger", "inside"] * 3,
        )

        self.assertEqual(1, len(entries))

    def test_confirmed_entry_does_not_rearm_from_inside_side(self):
        monitor = self._directional_monitor()
        first = self._process_entry_sides(
            monitor,
            ["outside"] * 3 + ["trigger"] + ["inside"] * 3,
        )
        replay = self._process_entry_sides(
            monitor,
            ["trigger"] + ["inside"] * 3,
        )

        self.assertEqual(1, len(first))
        self.assertEqual([], replay)

    def test_stable_return_outside_rearms_a_second_real_entry(self):
        monitor = self._directional_monitor()
        first = self._process_entry_sides(
            monitor,
            ["outside"] * 3 + ["trigger"] + ["inside"] * 3,
        )
        second = self._process_entry_sides(
            monitor,
            ["trigger"] + ["outside"] * 5 + ["trigger"] + ["inside"] * 3,
        )

        self.assertEqual(1, len(first))
        self.assertEqual(1, len(second))
        self.assertNotEqual(first[0].entry_key, second[0].entry_key)

    def test_entry_debug_lines_expose_side_state_armed_and_reason(self):
        monitor = self._directional_monitor()
        self._process_entry_sides(monitor, ["outside"] * 3)

        lines = monitor.entry_debug_lines("runtime:customer-1")

        self.assertIn("ENTRY SIDE: OUTSIDE", lines)
        self.assertIn("ENTRY STATE: OUTSIDE_STABLE", lines)
        self.assertIn("ARMED: YES", lines)
        self.assertIn("ENTRY REASON: outside_stable", lines)

    def test_crossing_timeout_cancels_without_entry(self):
        monitor = self._directional_monitor(crossing_timeout_seconds=3.0)
        for offset in range(3):
            monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [customer(foot=(0.5, 0.2))],
            )
        monitor.process(
            self.now + timedelta(milliseconds=300),
            [customer(foot=(0.5, 0.4))],
        )
        monitor.process(
            self.now + timedelta(seconds=3.4),
            [customer(foot=(0.5, 0.4))],
        )
        emitted = []
        for offset in range(3):
            entries, _ = monitor.process(
                self.now + timedelta(seconds=3.5 + offset * 0.1),
                [customer(foot=(0.5, 0.6))],
            )
            emitted.extend(entries)

        self.assertEqual([], emitted)

    def test_lost_track_state_is_not_inherited_by_new_track(self):
        monitor = self._directional_monitor(tracking_grace_seconds=1.0)
        self._process_entry_sides(
            monitor,
            ["outside"] * 3 + ["trigger"],
            token="runtime:customer-1",
        )
        monitor.process(self.now + timedelta(seconds=2), [])

        entries = self._process_entry_sides(
            monitor,
            ["inside"] * 3,
            token="runtime:customer-2",
        )

        self.assertEqual([], entries)

    def test_unknown_complete_crossing_is_emitted_after_customer_confirmation(self):
        monitor = self._directional_monitor()
        points = ["outside"] * 3 + ["trigger"] + ["inside"] * 3
        coordinates = {
            "outside": (0.5, 0.2),
            "trigger": (0.5, 0.4),
            "inside": (0.5, 0.6),
        }
        for offset, side in enumerate(points):
            entries, _ = monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [unresolved(foot=coordinates[side])],
            )
            self.assertEqual([], entries)

        entries, _ = monitor.process(
            self.now + timedelta(milliseconds=700),
            [
                customer(
                    track_id=3,
                    token="store-1:entrance-1:runtime-1:3",
                    foot=(0.5, 0.6),
                )
            ],
        )

        self.assertEqual(1, len(entries))

    def test_staff_directional_crossing_does_not_create_entry(self):
        monitor = self._directional_monitor()
        emitted = []
        for offset, foot in enumerate(
            [(0.5, 0.2)] * 3 + [(0.5, 0.4)] + [(0.5, 0.6)] * 3
        ):
            entries, _ = monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [staff(foot=foot)],
            )
            emitted.extend(entries)

        self.assertEqual([], emitted)

    def test_unknown_crossing_is_emitted_when_track_becomes_customer(self):
        crossing_at = self.now + timedelta(milliseconds=600)
        feet = [(0.5, 0.1)] * 3 + [(0.5, 0.35)] + [(0.5, 0.8)] * 3
        for offset, foot in enumerate(feet):
            self.monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [unresolved(foot=foot)],
            )

        entries, _ = self.monitor.process(
            self.now + timedelta(milliseconds=700),
            [unresolved(role="customer", foot=(0.5, 0.8))],
        )

        self.assertEqual(1, len(entries))
        self.assertEqual(crossing_at, entries[0].occurred_at)
        self.assertEqual("store-1:entrance-1:runtime-1:3", entries[0].track_token)

    def test_unknown_crossing_survives_moving_beyond_entry_before_confirmation(self):
        crossing_at = self.now + timedelta(milliseconds=600)
        feet = [(0.5, 0.1)] * 3 + [(0.5, 0.35)] + [(0.5, 0.8)] * 3
        for offset, foot in enumerate(feet):
            self.monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [unresolved(foot=foot)],
            )
        self.monitor.process(
            self.now + timedelta(milliseconds=700),
            [unresolved(foot=(0.5, 0.9))],
        )

        entries, _ = self.monitor.process(
            self.now + timedelta(milliseconds=800),
            [unresolved(role="customer", foot=(0.5, 0.9))],
        )

        self.assertEqual(1, len(entries))
        self.assertEqual(crossing_at, entries[0].occurred_at)

    def test_unknown_crossing_is_discarded_when_track_becomes_staff(self):
        feet = [(0.5, 0.1)] * 3 + [(0.5, 0.35)] + [(0.5, 0.8)] * 3
        for offset, foot in enumerate(feet):
            self.monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [unresolved(foot=foot)],
            )

        entries, _ = self.monitor.process(
            self.now + timedelta(milliseconds=700),
            [unresolved(role="staff", foot=(0.5, 0.8))],
        )

        self.assertEqual([], entries)

    def test_same_track_can_count_again_after_leaving_and_reentering(self):
        first, _ = self._enter()
        emitted = []
        feet = (
            [(0.5, 0.35)]
            + [(0.5, 0.1)] * 5
            + [(0.5, 0.35)]
            + [(0.5, 0.8)] * 3
        )
        for offset, foot in enumerate(feet, start=1):
            entries, _ = self.monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [customer(foot=foot)],
            )
            emitted.extend(entries)

        self.assertEqual(1, len(first))
        self.assertEqual(1, len(emitted))
        self.assertEqual("runtime:customer-1:entry:2", emitted[0].entry_key)

    def test_staff_initiated_approach_confirms_greeting(self):
        self._enter(staff_tracks=(staff(world=(0.0, 0.0)),))
        _, before_staff_enters = self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8)), staff(foot=(0.9, 0.5), world=(0.6, 0.0))],
        )
        _, just_entered = self.monitor.process(
            self.now + timedelta(seconds=3),
            [customer(foot=(0.6, 0.8)), staff(foot=(0.6, 0.5), world=(0.8, 0.0))],
        )
        _, behaviors = self.monitor.process(
            self.now + timedelta(seconds=5),
            [customer(foot=(0.6, 0.8)), staff(foot=(0.6, 0.5), world=(0.9, 0.0))],
        )

        self.assertEqual([], before_staff_enters)
        self.assertEqual([], just_entered)
        self.assertEqual(1, len(behaviors))
        event = behaviors[0]
        self.assertEqual("staff_greeting", event.event_type)
        self.assertEqual("confirmed", event.assessment)
        self.assertEqual(8, event.staff_track_id)
        self.assertGreaterEqual(event.details["staff_travel_metres"], 0.5)
        self.assertLessEqual(event.details["minimum_distance_metres"], 1.5)

    def test_controlled_demo_accepts_measured_staff_approach(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        for milliseconds, customer_world, staff_world in (
            (100, (1.08, 0.0), (0.15, 0.0)),
            (200, (0.922, 0.0), (0.312, 0.0)),
            (1400, (0.922, 0.0), (0.312, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )

        self.assertEqual(
            1,
            len(behaviors),
            monitor.greeting_debug_state("runtime:customer-1"),
        )
        event = behaviors[0]
        self.assertEqual("confirmed", event.assessment)
        self.assertAlmostEqual(0.312, event.details["staff_travel_metres"])
        self.assertAlmostEqual(0.6, event.details["distance_reduction_metres"])
        self.assertAlmostEqual(0.61, event.details["minimum_distance_metres"])
        self.assertAlmostEqual(1.2, event.details["sustained_seconds"])
        self.assertEqual("controlled_demo", event.details["greeting_profile"])
        _, replayed = monitor.process(
            self.now + timedelta(seconds=1.5),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )
        self.assertEqual([], replayed)

    def test_demo_relaxed_accepts_short_active_approach_as_demo(self):
        monitor = self._demo_relaxed_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        behaviors = []
        for milliseconds, customer_world, staff_world in (
            (100, (1.21, 0.0), (0.0, 0.0)),
            (200, (1.21, 0.0), (0.0, 0.0)),
            (300, (1.18, 0.0), (0.03, 0.0)),
            (400, (1.15, 0.0), (0.07, 0.0)),
            (500, (1.12, 0.0), (0.11, 0.0)),
            (600, (1.12, 0.0), (0.11, 0.0)),
            (1100, (1.12, 0.0), (0.11, 0.0)),
        ):
            _, frame_behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )
            behaviors.extend(frame_behaviors)

        self.assertEqual(1, len(behaviors), monitor.greeting_debug_state("runtime:customer-1"))
        event = behaviors[0]
        self.assertEqual("confirmed", event.assessment)
        self.assertTrue(event.is_demo)
        self.assertEqual("demo_relaxed", event.details["greeting_profile"])
        self.assertAlmostEqual(0.11, event.details["staff_travel_metres"])
        self.assertAlmostEqual(0.11, event.details["staff_toward_customer_metres"])
        self.assertAlmostEqual(0.20, event.details["distance_reduction_metres"])
        self.assertAlmostEqual(0.9, event.details["response_seconds"])
        self.assertEqual(
            (self.now + timedelta(milliseconds=200)).isoformat(),
            event.details["waiting_started_at"],
        )

    def test_demo_relaxed_accepts_nearby_staff_trajectory_outside_area(self):
        monitor = self._demo_relaxed_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(2.0, 0.0)),
            staff_tracks=(staff(foot=(0.9, 0.5), world=(1.8, 1.5)),),
        )

        behaviors = []
        for milliseconds, staff_world in (
            (100, (1.8, 1.5)),
            (200, (1.78, 1.5)),
            (300, (1.75, 1.5)),
            (400, (1.72, 1.5)),
            (500, (1.69, 1.5)),
            (600, (1.66, 1.5)),
            (1100, (1.66, 1.5)),
        ):
            _, frame_behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=(2.0, 0.0)),
                    staff(foot=(0.9, 0.5), world=staff_world),
                ],
            )
            behaviors.extend(frame_behaviors)

        self.assertEqual(1, len(behaviors))
        event = behaviors[0]
        self.assertEqual("confirmed", event.assessment)
        self.assertTrue(event.is_demo)
        self.assertEqual("demo_intent", event.details["trajectory_mode"])
        self.assertFalse(event.details["staff_in_greeting_area"])
        self.assertGreaterEqual(event.details["staff_travel_metres"], 0.10)
        self.assertEqual(0.0, event.details["distance_reduction_metres"])

    def test_demo_relaxed_rejects_static_staff_outside_area(self):
        monitor = self._demo_relaxed_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(2.0, 0.0)),
            staff_tracks=(staff(foot=(0.9, 0.5), world=(1.8, 1.5)),),
        )

        behaviors = []
        for milliseconds in (100, 200, 300, 400, 500, 1100):
            _, frame_behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=(2.0, 0.0)),
                    staff(foot=(0.9, 0.5), world=(1.8, 1.5)),
                ],
            )
            behaviors.extend(frame_behaviors)

        self.assertEqual([], behaviors)

    def test_demo_relaxed_keeps_final_confidence_gate(self):
        monitor = self._demo_relaxed_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(
                staff(
                    foot=(0.6, 0.5),
                    world=(0.0, 0.0),
                    role_confidence=0.49,
                ),
            ),
        )

        behaviors = []
        for milliseconds, customer_world, staff_world in (
            (100, (1.21, 0.0), (0.0, 0.0)),
            (200, (1.21, 0.0), (0.0, 0.0)),
            (300, (1.18, 0.0), (0.03, 0.0)),
            (400, (1.15, 0.0), (0.07, 0.0)),
            (500, (1.12, 0.0), (0.11, 0.0)),
            (600, (1.12, 0.0), (0.11, 0.0)),
            (1100, (1.12, 0.0), (0.11, 0.0)),
        ):
            _, frame_behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(
                        foot=(0.6, 0.5),
                        world=staff_world,
                        role_confidence=0.49,
                    ),
                ],
            )
            behaviors.extend(frame_behaviors)

        self.assertEqual(1, len(behaviors))
        self.assertEqual("uncertain", behaviors[0].assessment)
        self.assertTrue(behaviors[0].is_demo)

    def test_controlled_demo_rejects_staff_lateral_pass(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        for milliseconds, customer_world, staff_world in (
            (100, (0.9, 0.156), (0.0, 0.156)),
            (200, (0.61, 0.312), (0.0, 0.312)),
            (1400, (0.61, 0.312), (0.0, 0.312)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )

        self.assertEqual([], behaviors)

    def test_controlled_demo_rejects_insufficient_staff_travel(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        for milliseconds, customer_world, staff_world in (
            (100, (0.9, 0.0), (0.1, 0.0)),
            (200, (0.71, 0.0), (0.2, 0.0)),
            (1400, (0.71, 0.0), (0.2, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )

        self.assertEqual([], behaviors)

    def test_controlled_demo_rejects_short_proximity_duration(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        for milliseconds, customer_world, staff_world in (
            (100, (1.08, 0.0), (0.15, 0.0)),
            (200, (0.922, 0.0), (0.312, 0.0)),
            (900, (0.922, 0.0), (0.312, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )

        self.assertEqual([], behaviors)

    def test_controlled_demo_rejects_insufficient_distance_reduction(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        for milliseconds, customer_world, staff_world in (
            (100, (1.125, 0.0), (0.15, 0.0)),
            (200, (1.212, 0.0), (0.312, 0.0)),
            (1400, (1.212, 0.0), (0.312, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )

        self.assertEqual([], behaviors)

    def test_controlled_demo_rejects_final_distance_above_threshold(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(2.0, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        for milliseconds, customer_world, staff_world in (
            (100, (1.75, 0.0), (0.15, 0.0)),
            (200, (1.612, 0.0), (0.312, 0.0)),
            (1400, (1.612, 0.0), (0.312, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )

        self.assertEqual([], behaviors)

    def test_controlled_demo_rejects_customer_approaching_static_staff(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )

        for milliseconds, customer_world in (
            (100, (0.9, 0.0)),
            (200, (0.61, 0.0)),
            (1400, (0.61, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=(0.0, 0.0)),
                ],
            )

        self.assertEqual([], behaviors)

    def test_controlled_demo_retains_cycle_for_brief_greeting_area_exit(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8)),
            staff_tracks=(staff(foot=(0.6, 0.5)),),
        )
        for milliseconds in (100, 200):
            monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [customer(foot=(0.6, 0.8)), staff(foot=(0.6, 0.5))],
            )

        for milliseconds in (300, 400, 500, 600, 700):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [customer(foot=(0.2, 0.8)), staff(foot=(0.6, 0.5))],
            )
            self.assertEqual([], behaviors)
        monitor.process(
            self.now + timedelta(milliseconds=800),
            [customer(foot=(0.6, 0.8)), staff(foot=(0.6, 0.5))],
        )

        self.assertEqual((), monitor.greeting_cancellations())
        self.assertEqual(
            "greeting_in_progress",
            monitor.greeting_debug_state("runtime:customer-1")["greeting_state"],
        )

    def test_controlled_demo_can_confirm_during_debounced_boundary_exit(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )
        for milliseconds, customer_foot, customer_world, staff_world in (
            (100, (0.6, 0.8), (1.08, 0.0), (0.15, 0.0)),
            (200, (0.6, 0.8), (0.922, 0.0), (0.312, 0.0)),
            (1200, (0.2, 0.8), (0.922, 0.0), (0.312, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=customer_foot, world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )

        self.assertEqual(1, len(behaviors))
        self.assertEqual("confirmed", behaviors[0].assessment)

    def test_controlled_demo_cancels_after_greeting_area_exit_grace(self):
        monitor = self._controlled_demo_monitor()
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8)),
            staff_tracks=(staff(foot=(0.6, 0.5)),),
        )
        for milliseconds in (100, 200):
            monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [customer(foot=(0.6, 0.8)), staff(foot=(0.6, 0.5))],
            )

        for milliseconds in (300, 500, 700, 900, 1200):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [customer(foot=(0.2, 0.8)), staff(foot=(0.6, 0.5))],
            )

        self.assertEqual([], behaviors)
        self.assertEqual(
            "customer_left_greeting_area",
            monitor.greeting_cancellations()[-1].cancel_reason,
        )

    def test_greeting_event_contains_runtime_decision_context(self):
        self._enter(staff_tracks=(staff(world=(0.0, 0.0)),))
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8)), staff(world=(0.6, 0.0))],
        )
        self.monitor.process(
            self.now + timedelta(seconds=3),
            [
                customer(foot=(0.6, 0.8)),
                staff(foot=(0.6, 0.5), world=(0.8, 0.0)),
            ],
        )

        _, behaviors = self.monitor.process(
            self.now + timedelta(seconds=5),
            [
                customer(foot=(0.6, 0.8)),
                staff(foot=(0.6, 0.5), world=(0.9, 0.0)),
            ],
        )

        event = behaviors[0]
        self.assertEqual("staff_vest_yolo", event.details["staff_role_source"])
        self.assertEqual(0.91, event.details["staff_role_confidence"])
        self.assertEqual(event.confidence, event.details["greeting_confidence"])
        self.assertEqual(4.0, event.details["response_seconds"])
        self.assertEqual("entry_trigger", event.details["trigger_source"])
        self.assertEqual("ground_homography", event.details["distance_mode"])
        self.assertEqual("provisional", event.details["calibration_status"])
        self.assertEqual(
            "runtime:customer-1:entry:1:greeting-cycle",
            event.details["greeting_cycle_id"],
        )
        self.assertIsNone(event.entry_event_id)
        self.assertIsNone(event.visit_id)

    def test_single_frame_staff_role_miss_keeps_greeting_in_progress(self):
        self._enter(staff_tracks=(staff(world=(0.0, 0.0)),))
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8)), staff(world=(0.6, 0.0))],
        )
        missed_staff = TrackObservation(
            track_id=8,
            track_token="runtime:staff-8",
            role="unknown",
            foot_point=(0.6, 0.5),
            world_point=(0.7, 0.0),
            confidence=0.96,
        )
        _, during_miss = self.monitor.process(
            self.now + timedelta(seconds=2),
            [customer(foot=(0.6, 0.8)), missed_staff],
        )
        self.monitor.process(
            self.now + timedelta(seconds=3),
            [
                customer(foot=(0.6, 0.8)),
                staff(foot=(0.6, 0.5), world=(0.8, 0.0)),
            ],
        )

        _, completed = self.monitor.process(
            self.now + timedelta(seconds=5),
            [
                customer(foot=(0.6, 0.8)),
                staff(foot=(0.6, 0.5), world=(0.9, 0.0)),
            ],
        )

        self.assertEqual([], during_miss)
        self.assertEqual(1, len(completed))
        self.assertEqual("confirmed", completed[0].assessment)

    def test_customer_approaching_static_staff_is_not_greeting(self):
        short_window = BehaviorMonitorConfig(greeting_window_seconds=4)
        monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            config=short_window,
        )
        self._enter(
            monitor=monitor,
            value=customer(world=(3.0, 0.0)),
            staff_tracks=(staff(),),
        )
        monitor.process(
            self.now + timedelta(seconds=2),
            [customer(foot=(0.6, 0.8), world=(1.0, 0.0)), staff(foot=(0.6, 0.5))],
        )
        _, behaviors = monitor.process(
            self.now + timedelta(seconds=4),
            [customer(foot=(0.6, 0.8), world=(0.5, 0.0)), staff(foot=(0.6, 0.5))],
        )

        self.assertEqual(1, len(behaviors))
        self.assertEqual("staff_greeting", behaviors[0].event_type)
        self.assertEqual("not_observed", behaviors[0].assessment)

    def test_staff_lateral_pass_is_not_greeting(self):
        self._enter(staff_tracks=(staff(world=(-2.0, 0.0)),))
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [
                customer(foot=(0.6, 0.8), world=(0.0, 0.0)),
                staff(foot=(0.6, 0.5), world=(-1.0, 0.0)),
            ],
        )
        self.monitor.process(
            self.now + timedelta(seconds=2),
            [
                customer(foot=(0.6, 0.8), world=(0.0, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.0, 0.2)),
            ],
        )

        _, behaviors = self.monitor.process(
            self.now + timedelta(seconds=3),
            [
                customer(foot=(0.6, 0.8), world=(0.0, 0.0)),
                staff(foot=(0.6, 0.5), world=(1.0, 0.0)),
            ],
        )

        self.assertEqual([], behaviors)

    def test_customer_lost_before_greeting_area_cancels_observation(self):
        short_window = BehaviorMonitorConfig(greeting_window_seconds=4)
        monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            config=short_window,
        )
        self._enter(monitor=monitor)

        _, behaviors = monitor.process(self.now + timedelta(seconds=4), [])

        self.assertEqual([], behaviors)

    def test_customer_never_entering_greeting_area_does_not_emit_timeout(self):
        short_window = BehaviorMonitorConfig(greeting_window_seconds=4)
        monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            config=short_window,
        )
        self._enter(monitor=monitor)

        _, behaviors = monitor.process(
            self.now + timedelta(seconds=4), [customer()]
        )

        self.assertEqual([], behaviors)

    def test_greeting_timeout_emits_one_complete_not_observed_result(self):
        monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            config=BehaviorMonitorConfig(greeting_window_seconds=4),
            calibration_status="provisional",
        )
        self._enter(monitor=monitor)
        monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8))],
        )

        _, timed_out = monitor.process(
            self.now + timedelta(seconds=4),
            [customer(foot=(0.6, 0.8))],
        )
        _, replayed = monitor.process(
            self.now + timedelta(seconds=5),
            [customer(foot=(0.6, 0.8))],
        )

        self.assertEqual(1, len(timed_out))
        event = timed_out[0]
        self.assertEqual("not_observed", event.assessment)
        self.assertEqual([], replayed)
        self.assertEqual(
            "runtime:customer-1:entry:1:greeting-cycle",
            event.details["greeting_cycle_id"],
        )
        self.assertEqual(3.0, event.details["response_seconds"])
        self.assertEqual("entry_trigger", event.details["trigger_source"])
        self.assertEqual("ground_homography", event.details["distance_mode"])
        self.assertEqual("provisional", event.details["calibration_status"])

    def test_missing_ground_calibration_preserves_entry_and_cancels_greeting(self):
        monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
            greeting_area=GREETING,
            ground_calibration_available=False,
        )

        entries, behaviors = self._enter(monitor=monitor)

        self.assertEqual(1, len(entries))
        self.assertEqual([], behaviors)
        self.assertEqual(
            "calibration_unavailable",
            monitor.greeting_cancellations()[-1].cancel_reason,
        )

    def test_missing_greeting_area_preserves_entry_and_records_cancellation(self):
        monitor = _BehaviorHarness(
            camera_id="entrance-1",
            entrance_area=ENTRANCE,
        )

        entries, behaviors = self._enter(monitor=monitor)

        self.assertEqual(1, len(entries))
        self.assertEqual([], behaviors)
        self.assertEqual(
            "greeting_area_missing",
            monitor.greeting_cancellations()[-1].cancel_reason,
        )

    def test_normalized_image_distance_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ground_homography"):
            BehaviorMonitorConfig(distance_mode="normalized_image")

    def test_customer_outside_greeting_area_cannot_confirm_greeting(self):
        self._enter(staff_tracks=(staff(world=(0.0, 0.0)),))
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(), staff(foot=(0.2, 0.5), world=(0.6, 0.0))],
        )
        _, behaviors = self.monitor.process(
            self.now + timedelta(seconds=4),
            [customer(), staff(foot=(0.2, 0.5), world=(0.9, 0.0))],
        )

        self.assertEqual([], behaviors)

    def test_staff_must_finish_inside_greeting_area(self):
        self._enter(staff_tracks=(staff(world=(0.0, 0.0)),))
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8)), staff(world=(0.6, 0.0))],
        )
        _, behaviors = self.monitor.process(
            self.now + timedelta(seconds=4),
            [customer(foot=(0.6, 0.8)), staff(world=(0.9, 0.0))],
        )

        self.assertEqual([], behaviors)

    def test_customer_leaving_greeting_area_cancels_observation(self):
        self._enter(staff_tracks=(staff(world=(0.0, 0.0)),))
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8)), staff(world=(0.3, 0.0))],
        )
        self.monitor.process(
            self.now + timedelta(seconds=2),
            [customer(foot=(0.9, 0.8)), staff(foot=(0.6, 0.5), world=(0.8, 0.0))],
        )
        _, behaviors = self.monitor.process(
            self.now + timedelta(seconds=35),
            [customer(foot=(0.9, 0.8)), staff(foot=(0.6, 0.5), world=(0.9, 0.0))],
        )

        self.assertEqual([], behaviors)
        self.assertEqual(
            "customer_left_greeting_area",
            self.monitor.greeting_cancellations()[-1].cancel_reason,
        )

    def test_customer_disappearing_from_greeting_area_cancels_after_grace(self):
        self._enter(staff_tracks=(staff(world=(0.0, 0.0)),))
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8)), staff(world=(0.3, 0.0))],
        )

        _, after_grace = self.monitor.process(
            self.now + timedelta(seconds=3), [staff(foot=(0.6, 0.5), world=(0.8, 0.0))]
        )
        _, returned = self.monitor.process(
            self.now + timedelta(seconds=4),
            [customer(foot=(0.6, 0.8)), staff(foot=(0.6, 0.5), world=(0.8, 0.0))],
        )
        _, still_returned = self.monitor.process(
            self.now + timedelta(seconds=7),
            [customer(foot=(0.6, 0.8)), staff(foot=(0.6, 0.5), world=(0.9, 0.0))],
        )

        self.assertEqual([], after_grace)
        self.assertEqual([], returned)
        self.assertEqual([], still_returned)
        self.assertEqual(
            "customer_track_lost",
            self.monitor.greeting_cancellations()[-1].cancel_reason,
        )

    def test_customer_role_corrected_to_staff_cancels_greeting(self):
        self._enter()
        self.monitor.process(
            self.now + timedelta(seconds=1),
            [customer(foot=(0.6, 0.8))],
        )
        corrected = TrackObservation(
            track_id=1,
            track_token="runtime:customer-1",
            role="staff",
            foot_point=(0.6, 0.8),
            world_point=(2.0, 0.0),
            confidence=0.98,
        )

        _, corrected_behaviors = self.monitor.process(
            self.now + timedelta(seconds=2), [corrected]
        )
        _, later_behaviors = self.monitor.process(
            self.now + timedelta(seconds=35), [corrected]
        )

        self.assertEqual([], corrected_behaviors)
        self.assertEqual([], later_behaviors)
        cancellations = self.monitor.greeting_cancellations()
        self.assertEqual(1, len(cancellations))
        self.assertEqual(
            "customer_role_corrected_to_staff", cancellations[0].cancel_reason
        )

    def test_dwell_tolerates_short_tracking_gap_and_records_exact_duration(self):
        in_area = customer(foot=(0.5, 0.5))
        outside = customer(foot=(0.9, 0.9))
        self.monitor.process(self.now, [in_area])
        self.monitor.process(self.now + timedelta(seconds=3), [in_area])
        _, during_gap = self.monitor.process(self.now + timedelta(seconds=3.5), [])
        self.monitor.process(self.now + timedelta(seconds=4), [in_area])
        _, just_outside = self.monitor.process(
            self.now + timedelta(seconds=5), [outside]
        )
        _, finished = self.monitor.process(
            self.now + timedelta(seconds=5.1), [outside]
        )

        self.assertEqual([], during_gap)
        self.assertEqual([], just_outside)
        self.assertEqual(1, len(finished))
        self.assertEqual("merchandise_dwell", finished[0].event_type)
        self.assertEqual(4.0, finished[0].details["duration_seconds"])

    def test_short_pass_by_does_not_create_dwell(self):
        in_area = customer(foot=(0.5, 0.5))
        outside = customer(foot=(0.9, 0.9))
        self.monitor.process(self.now, [in_area])
        self.monitor.process(self.now + timedelta(seconds=1), [in_area])
        self.monitor.process(self.now + timedelta(seconds=2), [outside])
        _, behaviors = self.monitor.process(self.now + timedelta(seconds=2.1), [outside])

        self.assertEqual([], behaviors)

    def _controlled_demo_confidence_monitor_at_first_window(self):
        monitor = self._controlled_demo_monitor(confirmed_confidence=0.7)
        self._enter(
            monitor=monitor,
            value=customer(foot=(0.6, 0.8), world=(1.21, 0.0)),
            staff_tracks=(staff(foot=(0.6, 0.5), world=(0.0, 0.0)),),
        )
        for milliseconds, customer_world, staff_world in (
            (100, (1.15, 0.0), (0.05, 0.0)),
            (200, (1.08, 0.0), (0.15, 0.0)),
            (300, (0.98, 0.0), (0.25, 0.0)),
            (400, (0.922, 0.0), (0.312, 0.0)),
        ):
            _, behaviors = monitor.process(
                self.now + timedelta(milliseconds=milliseconds),
                [
                    customer(foot=(0.6, 0.8), world=customer_world),
                    staff(foot=(0.6, 0.5), world=staff_world),
                ],
            )
            self.assertEqual([], behaviors)
        return monitor

    def _assert_controlled_demo_behavior_gates_pass(self, event):
        self.assertGreaterEqual(event.details["staff_travel_metres"], 0.25)
        self.assertGreaterEqual(event.details["staff_toward_customer_metres"], 0.25)
        self.assertGreaterEqual(event.details["distance_reduction_metres"], 0.35)
        self.assertLessEqual(event.details["minimum_distance_metres"], 1.2)
        self.assertGreaterEqual(event.details["sustained_seconds"], 1.0)

    def test_transient_low_confidence_in_broken_window_does_not_poison_final_window(self):
        monitor = self._controlled_demo_confidence_monitor_at_first_window()
        monitor.process(
            self.now + timedelta(milliseconds=500),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(
                    foot=(0.6, 0.5),
                    world=(0.312, 0.0),
                    confidence=0.59,
                ),
            ],
        )
        monitor.process(
            self.now + timedelta(milliseconds=600),
            [
                customer(foot=(0.6, 0.8), world=(1.70, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )
        monitor.process(
            self.now + timedelta(milliseconds=700),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )
        _, behaviors = monitor.process(
            self.now + timedelta(milliseconds=1700),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )

        self.assertEqual(1, len(behaviors))
        self.assertEqual("confirmed", behaviors[0].assessment)
        self.assertGreaterEqual(behaviors[0].confidence, 0.7)
        self._assert_controlled_demo_behavior_gates_pass(behaviors[0])

    def test_low_person_confidence_inside_final_window_remains_uncertain(self):
        monitor = self._controlled_demo_confidence_monitor_at_first_window()
        monitor.process(
            self.now + timedelta(milliseconds=500),
            [
                customer(
                    foot=(0.6, 0.8), world=(0.922, 0.0), confidence=0.59
                ),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )
        _, behaviors = monitor.process(
            self.now + timedelta(milliseconds=1400),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )

        self.assertEqual(1, len(behaviors))
        self.assertEqual("uncertain", behaviors[0].assessment)
        self.assertAlmostEqual(0.59, behaviors[0].confidence)
        self._assert_controlled_demo_behavior_gates_pass(behaviors[0])

    def test_fully_high_confidence_final_window_is_confirmed(self):
        monitor = self._controlled_demo_confidence_monitor_at_first_window()

        _, behaviors = monitor.process(
            self.now + timedelta(milliseconds=1400),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )

        self.assertEqual(1, len(behaviors))
        self.assertEqual("confirmed", behaviors[0].assessment)
        self.assertGreaterEqual(behaviors[0].confidence, 0.7)
        self._assert_controlled_demo_behavior_gates_pass(behaviors[0])

    def test_sustained_reset_also_resets_qualifying_confidence(self):
        monitor = self._controlled_demo_confidence_monitor_at_first_window()
        monitor.process(
            self.now + timedelta(milliseconds=500),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(
                    foot=(0.6, 0.5),
                    world=(0.312, 0.0),
                    confidence=0.59,
                ),
            ],
        )
        monitor.process(
            self.now + timedelta(milliseconds=600),
            [
                customer(foot=(0.6, 0.8), world=(1.70, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )

        state = monitor._greeting._official._greetings["runtime:customer-1"]
        pair = state.pairs["runtime:staff-8"]
        self.assertIsNone(pair.within_since)
        self.assertIsNone(pair.qualifying_confidence)

        monitor.process(
            self.now + timedelta(milliseconds=700),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )
        self.assertIsNotNone(pair.within_since)
        self.assertAlmostEqual(0.91, pair.qualifying_confidence)

    def test_low_staff_role_confidence_inside_final_window_remains_uncertain(self):
        monitor = self._controlled_demo_confidence_monitor_at_first_window()
        monitor.process(
            self.now + timedelta(milliseconds=500),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(
                    foot=(0.6, 0.5),
                    world=(0.312, 0.0),
                    role_confidence=0.59,
                ),
            ],
        )
        _, behaviors = monitor.process(
            self.now + timedelta(milliseconds=1400),
            [
                customer(foot=(0.6, 0.8), world=(0.922, 0.0)),
                staff(foot=(0.6, 0.5), world=(0.312, 0.0)),
            ],
        )

        self.assertEqual(1, len(behaviors))
        self.assertEqual("uncertain", behaviors[0].assessment)
        self.assertAlmostEqual(0.59, behaviors[0].confidence)

    def test_zone_enter_requires_stable_inside_frames(self):
        monitor = BehaviorMonitor(
            camera_id="behavior-1",
            interaction_areas=(INTERACTION,),
            config=BehaviorMonitorConfig(
                zone_enter_stable_frames=2,
                zone_exit_stable_frames=2,
            ),
        )

        monitor.process(self.now, [customer(foot=(0.5, 0.5))])
        pending, transitions = monitor.zone_runtime_diagnostics()
        monitor.process(self.now + timedelta(seconds=0.1), [customer(foot=(0.2, 0.5))])
        outside, cancelled = monitor.zone_runtime_diagnostics()

        self.assertEqual(ZoneState.ENTER_PENDING, pending[0].zone_state)
        self.assertEqual((), transitions)
        self.assertEqual(ZoneState.OUTSIDE, outside[0].zone_state)
        self.assertEqual((), cancelled)

    def test_zone_exit_hysteresis_preserves_live_dwell(self):
        monitor = BehaviorMonitor(
            camera_id="behavior-1",
            interaction_areas=(INTERACTION,),
            config=BehaviorMonitorConfig(
                zone_enter_stable_frames=2,
                zone_exit_stable_frames=2,
            ),
        )

        monitor.process(self.now, [customer(foot=(0.5, 0.5))])
        monitor.process(self.now + timedelta(seconds=1), [customer(foot=(0.5, 0.5))])
        entered, enter_transitions = monitor.zone_runtime_diagnostics()
        monitor.process(self.now + timedelta(seconds=2), [customer(foot=(0.2, 0.5))])
        pending, pending_transitions = monitor.zone_runtime_diagnostics()
        monitor.process(self.now + timedelta(seconds=3), [customer(foot=(0.5, 0.5))])
        returned, returned_transitions = monitor.zone_runtime_diagnostics()

        self.assertEqual(ZoneState.INSIDE, entered[0].zone_state)
        self.assertEqual(1.0, entered[0].dwell_seconds)
        self.assertEqual(ZoneTransitionType.ZONE_ENTER, enter_transitions[0].transition)
        self.assertEqual(ZoneState.EXIT_PENDING, pending[0].zone_state)
        self.assertEqual(1.0, pending[0].dwell_seconds)
        self.assertEqual((), pending_transitions)
        self.assertEqual(ZoneState.INSIDE, returned[0].zone_state)
        self.assertEqual(3.0, returned[0].dwell_seconds)
        self.assertEqual((), returned_transitions)

    def test_zone_exit_emits_transition_and_finishes_dwell(self):
        monitor = BehaviorMonitor(
            camera_id="behavior-1",
            interaction_areas=(INTERACTION,),
            config=BehaviorMonitorConfig(
                dwell_minimum_seconds=1.0,
                zone_enter_stable_frames=1,
                zone_exit_stable_frames=2,
            ),
        )

        monitor.process(self.now, [customer(foot=(0.5, 0.5))])
        monitor.process(self.now + timedelta(seconds=4), [customer(foot=(0.5, 0.5))])
        monitor.process(self.now + timedelta(seconds=5), [customer(foot=(0.2, 0.5))])
        _entries, behaviors = monitor.process(
            self.now + timedelta(seconds=6), [customer(foot=(0.2, 0.5))]
        )
        states, transitions = monitor.zone_runtime_diagnostics()

        self.assertEqual(ZoneState.OUTSIDE, states[0].zone_state)
        self.assertEqual(ZoneTransitionType.ZONE_EXIT, transitions[0].transition)
        self.assertEqual(4.0, transitions[0].dwell_seconds)
        self.assertEqual(1, len(behaviors))
        self.assertEqual(4.0, behaviors[0].details["duration_seconds"])

    def test_track_lost_cleans_zone_state_after_timeout(self):
        monitor = BehaviorMonitor(
            camera_id="behavior-1",
            interaction_areas=(INTERACTION,),
            config=BehaviorMonitorConfig(
                dwell_minimum_seconds=1.0,
                zone_enter_stable_frames=1,
                zone_track_lost_seconds=2.0,
            ),
        )

        monitor.process(self.now, [customer(foot=(0.5, 0.5))])
        monitor.process(self.now + timedelta(seconds=3), [customer(foot=(0.5, 0.5))])
        monitor.process(self.now + timedelta(seconds=4), [])
        during_grace, grace_transitions = monitor.zone_runtime_diagnostics()
        _entries, behaviors = monitor.process(self.now + timedelta(seconds=5.1), [])
        cleared, transitions = monitor.zone_runtime_diagnostics()

        self.assertEqual(ZoneState.INSIDE, during_grace[0].zone_state)
        self.assertEqual((), grace_transitions)
        self.assertEqual(ZoneState.OUTSIDE, cleared[0].zone_state)
        self.assertEqual(ZoneTransitionType.TRACK_LOST, transitions[0].transition)
        self.assertEqual(3.0, transitions[0].dwell_seconds)
        self.assertEqual(1, len(behaviors))

    def test_reach_emits_once_per_hand_entry_and_low_confidence_is_uncertain(self):
        high_wrist = PosePoint(0.7, 0.4, 0.9)
        low_wrist = PosePoint(0.7, 0.4, 0.55)
        self.monitor.process(self.now, [customer(wrists=(high_wrist,))])
        _, first = self.monitor.process(
            self.now + timedelta(seconds=0.4),
            [customer(wrists=(high_wrist,))],
        )
        _, duplicate = self.monitor.process(
            self.now + timedelta(seconds=0.8),
            [customer(wrists=(high_wrist,))],
        )
        self.monitor.process(
            self.now + timedelta(seconds=2), [customer(wrists=())]
        )
        self.monitor.process(
            self.now + timedelta(seconds=3),
            [customer(confidence=0.6, wrists=(low_wrist,))],
        )
        _, second = self.monitor.process(
            self.now + timedelta(seconds=3.4),
            [customer(confidence=0.6, wrists=(low_wrist,))],
        )

        self.assertEqual("confirmed", first[0].assessment)
        self.assertEqual([], duplicate)
        self.assertEqual("uncertain", second[0].assessment)
        self.assertNotEqual(first[0].event_key, second[0].event_key)

    def test_polygon_rejects_self_intersection(self):
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            PolygonArea(
                "invalid",
                "greeting",
                ((0.1, 0.1), (0.9, 0.9), (0.9, 0.1), (0.1, 0.9)),
            )

    def test_polygon_rejects_area_too_small_for_runtime(self):
        with self.assertRaisesRegex(ValueError, "too small"):
            PolygonArea(
                "tiny",
                "greeting",
                (
                    (0.1, 0.1),
                    (0.1001, 0.1),
                    (0.1001, 0.1001),
                    (0.1, 0.1001),
                ),
            )

    def test_polygon_boundary_counts_as_inside(self):
        self.assertTrue(ENTRANCE.contains((0.8, 0.35)))
        self.assertFalse(ENTRANCE.contains((0.9, 0.35)))

    def test_first_detection_inside_is_not_an_entry(self):
        entries, _ = self.monitor.process(self.now, [customer()])

        self.assertEqual([], entries)

    def test_short_tracking_gap_does_not_duplicate_entry(self):
        first, _ = self._enter()
        self.monitor.process(self.now + timedelta(milliseconds=500), [])

        duplicate, _ = self.monitor.process(
            self.now + timedelta(milliseconds=900), [customer()]
        )

        self.assertEqual(1, len(first))
        self.assertEqual([], duplicate)

    def test_reused_track_id_requires_a_new_crossing_after_long_loss(self):
        self._enter()
        self.monitor.process(self.now + timedelta(seconds=2), [])
        new_token = "runtime-2:customer-1"
        first_inside, _ = self.monitor.process(
            self.now + timedelta(seconds=3), [customer(token=new_token)]
        )
        emitted = []
        feet = [(0.5, 0.1)] * 3 + [(0.5, 0.35)] + [(0.5, 0.8)] * 3
        for offset, foot in enumerate(feet):
            entries, _ = self.monitor.process(
                self.now + timedelta(seconds=4, milliseconds=offset * 100),
                [customer(token=new_token, foot=foot)],
            )
            emitted.extend(entries)

        self.assertEqual([], first_inside)
        self.assertEqual(1, len(emitted))
        self.assertEqual("runtime-2:customer-1:entry:1", emitted[0].entry_key)

    def test_multiple_customers_crossing_are_counted_independently(self):
        emitted = []
        feet = [(0.5, 0.1)] * 3 + [(0.5, 0.35)] + [(0.5, 0.8)] * 3
        for offset, foot in enumerate(feet):
            entries, _ = self.monitor.process(
                self.now + timedelta(milliseconds=offset * 100),
                [
                    customer(foot=foot),
                    customer(
                        track_id=2,
                        token="runtime:customer-2",
                        foot=(0.6, foot[1]),
                    ),
                ],
            )
            emitted.extend(entries)

        self.assertEqual({1, 2}, {entry.track_id for entry in emitted})


if __name__ == "__main__":
    unittest.main()
