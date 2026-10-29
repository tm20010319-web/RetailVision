from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime, timedelta

from agent.device_identity import DeviceRecognitionEvent, StableTrackEvent
from agent.entry_event_coordinator import EntryEventCoordinator


BASE = datetime(2026, 7, 14, 12, 0, 0)


class FakeMonotonicClock:
    def __init__(self):
        self.current = 0.0

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


def track(track_id=1, seconds=0):
    return StableTrackEvent(
        camera_id="front-door",
        track_id=track_id,
        bbox=[10, 20, 100, 200],
        detection_confidence=0.91,
        confirmed_at=BASE + timedelta(seconds=seconds),
    )


def recognition(event_id="event-1", member_id="member-1", seconds=1, capture_path=None):
    return DeviceRecognitionEvent(
        device_id="front-door",
        event_id=event_id,
        log_id=7,
        member_id=member_id,
        occurred_at=BASE + timedelta(seconds=seconds),
        similarity=0.88,
        group_id=2,
        person_id=19,
        unique_id="vendor-19",
        rule_id=1,
        capture_path=capture_path,
    )


class EntryEventCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.members = []
        self.anonymous = []
        self.unknown = []
        self.coordinator = EntryEventCoordinator(
            on_member_entry=self.members.append,
            on_anonymous_visit=lambda track_id, detection, reason: self.anonymous.append(
                (track_id, detection, reason)
            ),
            on_identity_unknown=lambda event, reason: self.unknown.append((event, reason)),
            association_window_seconds=2,
            track_wait_seconds=3,
        )

    def test_track_then_device_event_associates_unique_candidate(self):
        self.coordinator.submit_track(track())
        self.coordinator.submit_recognition(recognition(capture_path="device-capture.jpg"))

        self.assertEqual(1, len(self.members))
        session = self.members[0]
        self.assertEqual(1, session["track_id"])
        self.assertEqual([10, 20, 100, 200], session["detection_bbox"])
        self.assertEqual("device_api", session["identity_source"])
        self.assertEqual("event-1", session["device_event_id"])
        self.assertEqual("device-capture.jpg", session["snapshot_path"])

    def test_device_event_then_track_also_associates(self):
        self.coordinator.submit_recognition(recognition())
        self.coordinator.submit_track(track())

        self.assertEqual(1, len(self.members))
        self.assertEqual(1, self.members[0]["track_id"])

    def test_ambiguous_tracks_are_not_guessed(self):
        self.coordinator.submit_track(track(1))
        self.coordinator.submit_track(track(2))

        self.coordinator.submit_recognition(recognition())

        self.assertEqual(1, len(self.members))
        self.assertIsNone(self.members[0]["track_id"])
        self.assertIsNone(self.members[0]["detection_bbox"])
        self.assertEqual([1, 2], sorted(item[0].track_id for item in self.unknown))
        self.assertTrue(all(reason == "ambiguous_member_match" for _, reason in self.unknown))

    def test_multiple_device_events_for_one_track_are_not_guessed(self):
        self.coordinator.submit_recognition(recognition("event-1", "member-1"))
        self.coordinator.submit_recognition(recognition("event-2", "member-2"))

        self.coordinator.submit_track(track(1))

        self.assertEqual(2, len(self.members))
        self.assertTrue(all(session["track_id"] is None for session in self.members))
        self.assertEqual(1, len(self.unknown))
        self.assertEqual("ambiguous_device_events", self.unknown[0][1])
        self.assertEqual([], self.anonymous)

    def test_healthy_device_track_becomes_anonymous_after_wait(self):
        self.coordinator.set_device_health("front-door", True)
        self.coordinator.submit_track(track())

        self.coordinator.flush(BASE + timedelta(seconds=3, milliseconds=1))

        self.assertEqual(1, len(self.anonymous))
        self.assertEqual("no_member_recognition", self.anonymous[0][2])
        self.assertEqual([], self.unknown)

    def test_unavailable_device_never_counts_track_as_anonymous(self):
        self.coordinator.set_device_health("front-door", False)
        self.coordinator.submit_track(track())

        self.coordinator.flush(BASE + timedelta(seconds=4))

        self.assertEqual([], self.anonymous)
        self.assertEqual("device_unavailable", self.unknown[0][1])

    def test_later_merged_health_update_controls_the_next_track(self):
        self.coordinator.set_device_health("front-door", True)
        self.coordinator.submit_track(track(track_id=1))
        self.coordinator.flush(BASE + timedelta(seconds=5))
        self.coordinator.set_device_health("front-door", False)
        self.coordinator.submit_track(track(track_id=2, seconds=10))

        self.coordinator.flush(BASE + timedelta(seconds=15))

        self.assertEqual([1], [entry[0] for entry in self.anonymous])
        self.assertEqual("device_unavailable", self.unknown[0][1])

    def test_late_unmatched_device_event_creates_member_session_without_track(self):
        self.coordinator.submit_recognition(recognition())

        self.coordinator.flush(BASE + timedelta(seconds=5))

        self.assertEqual(1, len(self.members))
        self.assertIsNone(self.members[0]["track_id"])
        self.assertEqual("event-1", self.members[0]["device_event_id"])

    def test_events_outside_association_window_are_not_bound(self):
        self.coordinator.set_device_health("front-door", True)
        self.coordinator.submit_track(track(seconds=0))
        self.coordinator.submit_recognition(recognition(seconds=3))

        self.coordinator.flush(BASE + timedelta(seconds=6))

        self.assertEqual(1, len(self.members))
        self.assertIsNone(self.members[0]["track_id"])
        self.assertEqual(1, len(self.anonymous))

    def test_consumed_ids_suppress_duplicates_until_ttl_then_allow_reuse(self):
        clock = FakeMonotonicClock()
        coordinator = EntryEventCoordinator(
            on_member_entry=self.members.append,
            association_window_seconds=2,
            track_wait_seconds=3,
            dedupe_ttl_seconds=10,
            monotonic_clock=clock,
        )

        coordinator.submit_track(track())
        coordinator.submit_recognition(recognition())
        coordinator.submit_track(track())
        coordinator.submit_recognition(recognition())
        self.assertEqual(1, len(self.members))

        clock.advance(10.1)
        coordinator.submit_track(track(seconds=20))
        coordinator.submit_recognition(recognition(seconds=21))

        self.assertEqual(2, len(self.members))
        self.assertEqual([1, 1], [session["track_id"] for session in self.members])

    def test_submit_and_flush_prune_consumed_state_to_ttl_window(self):
        clock = FakeMonotonicClock()
        coordinator = EntryEventCoordinator(
            association_window_seconds=2,
            track_wait_seconds=3,
            dedupe_ttl_seconds=10,
            monotonic_clock=clock,
        )

        coordinator.submit_track(track())
        coordinator.submit_recognition(recognition())
        self.assertEqual(1, len(coordinator._consumed_tracks))
        self.assertEqual(1, len(coordinator._consumed_recognitions))

        clock.advance(10.1)
        coordinator.flush(BASE + timedelta(seconds=20))
        self.assertEqual(0, len(coordinator._consumed_tracks))
        self.assertEqual(0, len(coordinator._consumed_recognitions))

        coordinator.submit_track(track(track_id=2, seconds=20))
        coordinator.submit_recognition(recognition(event_id="event-2", seconds=21))
        clock.advance(10.1)
        coordinator.submit_track(track(track_id=3, seconds=40))
        self.assertNotIn(("front-door", 2), coordinator._consumed_tracks)
        self.assertNotIn("event-2", coordinator._consumed_recognitions)

    def test_business_callback_failure_does_not_break_coordinator(self):
        coordinator = EntryEventCoordinator(
            on_member_entry=lambda session: (_ for _ in ()).throw(
                RuntimeError("business callback failed")
            )
        )

        coordinator.submit_track(track())
        coordinator.submit_recognition(recognition())

        coordinator.flush(BASE + timedelta(seconds=5))


if __name__ == "__main__":
    unittest.main()
