from datetime import datetime, timedelta
import unittest

from demo.greeting.state import DemoGreetingStateMachine, PersonObservation


def person(track_id, x, *, staff=False, world_point=None):
    return PersonObservation(
        track_id=track_id,
        bbox=(x, 20.0, x + 20.0, 100.0),
        is_staff=staff,
        staff_confidence=0.86 if staff else None,
        role_source="staff_vest_yolo" if staff else "person_without_vest",
        world_point=world_point,
    )


class DemoGreetingStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.started_at = datetime(2026, 7, 31, 9, 0, 0)
        self.machine = DemoGreetingStateMachine(
            session_id="demo-test",
            camera_id="entrance-1",
            store_id="store-1",
            co_presence_seconds=0.5,
            staff_motion_pixels=15.0,
            rearm_absence_seconds=1.5,
        )

    def test_requires_co_presence_and_staff_motion(self):
        self.assertEqual(
            [],
            self.machine.observe(
                self.started_at,
                [person(7, 10, staff=True), person(42, 120)],
            ),
        )
        self.assertEqual(
            [],
            self.machine.observe(
                self.started_at + timedelta(seconds=0.25),
                [person(7, 18, staff=True), person(42, 120)],
            ),
        )

        candidates = self.machine.observe(
            self.started_at + timedelta(seconds=0.55),
            [person(7, 28, staff=True), person(42, 120)],
        )

        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual("demo-test", candidate.session_id)
        self.assertEqual("demo-runtime:demo-test:track-7", candidate.staff_track_token)
        self.assertEqual("demo-runtime:demo-test:track-42", candidate.customer_track_token)
        self.assertGreaterEqual(candidate.staff_motion_pixels, 15.0)
        self.assertEqual("staff_role_unbound", candidate.details["identity_status"])
        self.assertTrue(candidate.details["is_demo"])

    def test_does_not_repeat_until_the_pair_leaves_and_rearms(self):
        frames = [
            (0.0, 10),
            (0.3, 20),
            (0.6, 30),
        ]
        candidates = []
        for offset, x in frames:
            candidates.extend(
                self.machine.observe(
                    self.started_at + timedelta(seconds=offset),
                    [person(7, x, staff=True), person(42, 120)],
                )
            )
        self.assertEqual(1, len(candidates))

        self.assertEqual(
            [],
            self.machine.observe(
                self.started_at + timedelta(seconds=0.9),
                [person(7, 45, staff=True), person(42, 120)],
            ),
        )
        self.machine.observe(self.started_at + timedelta(seconds=2.5), [])

        self.assertEqual(
            [],
            self.machine.observe(
                self.started_at + timedelta(seconds=2.6),
                [person(7, 10, staff=True), person(42, 120)],
            ),
        )
        candidates = self.machine.observe(
            self.started_at + timedelta(seconds=3.2),
            [person(7, 30, staff=True), person(42, 120)],
        )
        self.assertEqual(1, len(candidates))

    def test_two_non_staff_tracks_never_create_a_greeting(self):
        candidates = self.machine.observe(
            self.started_at + timedelta(seconds=1),
            [person(1, 10), person(2, 100)],
        )
        self.assertEqual([], candidates)

    def test_records_demo_response_and_world_metric_fields(self):
        self.machine.observe(
            self.started_at,
            [
                person(7, 10, staff=True, world_point=(1.0, 1.0)),
                person(42, 120, world_point=(1.0, 2.0)),
            ],
        )
        self.machine.observe(
            self.started_at + timedelta(seconds=0.25),
            [
                person(7, 18, staff=True, world_point=(1.0, 1.2)),
                person(42, 120, world_point=(1.0, 2.0)),
            ],
        )
        candidates = self.machine.observe(
            self.started_at + timedelta(seconds=0.55),
            [
                person(7, 28, staff=True, world_point=(1.0, 1.6)),
                person(42, 120, world_point=(1.0, 2.0)),
            ],
        )

        self.assertEqual(1, len(candidates))
        details = candidates[0].details
        self.assertAlmostEqual(0.55, details["demo_response_seconds"], places=2)
        self.assertAlmostEqual(0.4, details["minimum_distance_metres"], places=2)
        self.assertAlmostEqual(0.6, details["staff_travel_metres"], places=2)
        self.assertEqual("ground_homography", details["distance_source"])
        self.assertEqual("provisional", details["calibration_status"])
