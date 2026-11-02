from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.identity.camera_track_identity import (
    CameraTrackIdentityCache,
    TrackIdentityBinding,
)
from agent.models import Member, MemberArrival, Visit
from agent.schema_evolution import evolve_schema
from agent.visits.visit_activity import (
    VisitActivity,
    VisitActivityRefresher,
    VisitActivityService,
)


class VisitActivityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 5, 6)
        self.engine = configure_sqlite_foreign_keys(
            create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions() as db:
            db.add(Member(
                id="member-1",
                name="Member One",
                visit_count=3,
                last_visit=self.now - timedelta(days=1),
            ))
            db.flush()
            visit = Visit(
                member_id="member-1",
                store_id="store-1",
                entry_time=self.now,
                last_seen_at=self.now,
            )
            db.add(visit)
            db.flush()
            arrival = MemberArrival(
                visit_id=visit.id,
                member_id="member-1",
                occurred_at=self.now,
                arrival_source="device_recognition",
            )
            db.add(arrival)
            db.commit()
            self.visit_id = visit.id
            self.arrival_id = arrival.id
        self.service = VisitActivityService(self.sessions)

    def tearDown(self):
        self.engine.dispose()

    def activity(self, observed_at: datetime, **overrides) -> VisitActivity:
        values = {
            "store_id": "store-1",
            "camera_id": "entrance-1",
            "runtime_instance_id": "runtime-1",
            "track_id": 7,
            "track_token": "store-1:entrance-1:runtime-1:7",
            "binding_id": "binding-1",
            "member_id": "member-1",
            "canonical_visit_id": self.visit_id,
            "observed_at": observed_at,
        }
        values.update(overrides)
        if "track_token" not in overrides:
            values["track_token"] = (
                f"{values['store_id']}:{values['camera_id']}:"
                f"{values['runtime_instance_id']}:{values['track_id']}"
            )
        return VisitActivity(**values)

    def visit(self) -> Visit:
        with self.sessions() as db:
            visit = db.get(Visit, self.visit_id)
            db.expunge(visit)
            return visit

    def test_reusable_visit_advances_monotonically(self):
        result = self.service.refresh_batch(
            (self.activity(self.now + timedelta(seconds=10)),),
            now=self.now + timedelta(seconds=10),
        )
        self.assertEqual({"updated": 1}, result)
        self.assertEqual(self.now + timedelta(seconds=10), self.visit().last_seen_at)

        result = self.service.refresh_batch(
            (
                self.activity(self.now + timedelta(seconds=10)),
                self.activity(self.now + timedelta(seconds=9)),
            ),
            now=self.now + timedelta(seconds=10),
        )
        self.assertEqual({"no_change": 2}, result)
        self.assertEqual(self.now + timedelta(seconds=10), self.visit().last_seen_at)

    def test_superseded_visit_refreshes_final_canonical_visit(self):
        with self.sessions() as db:
            canonical = db.get(Visit, self.visit_id)
            duplicate = Visit(
                member_id="member-1",
                store_id="store-1",
                entry_time=self.now,
                last_seen_at=self.now,
                exit_time=self.now + timedelta(seconds=1),
                closed_reason="superseded",
                superseded_by_visit_id=canonical.id,
                superseded_at=self.now + timedelta(seconds=1),
                superseded_reason="duplicate_reusable_visit",
            )
            db.add(duplicate)
            db.commit()
            duplicate_id = duplicate.id

        result = self.service.refresh_batch(
            (
                self.activity(
                    self.now + timedelta(seconds=10),
                    canonical_visit_id=duplicate_id,
                ),
            ),
            now=self.now + timedelta(seconds=10),
        )
        self.assertEqual({"updated": 1}, result)
        self.assertEqual(self.now + timedelta(seconds=10), self.visit().last_seen_at)

    def test_non_reusable_visit_is_not_closed_or_reactivated(self):
        original = self.visit()
        result = self.service.refresh_batch(
            (self.activity(self.now + timedelta(minutes=31)),),
            now=self.now + timedelta(minutes=31),
        )
        self.assertEqual({"skipped_non_reusable_inactivity": 1}, result)
        current = self.visit()
        self.assertEqual(original.last_seen_at, current.last_seen_at)
        self.assertIsNone(current.exit_time)
        self.assertIsNone(current.closed_reason)

    def test_max_duration_visit_is_not_refreshed(self):
        observed_at = self.now + timedelta(hours=4, seconds=1)
        result = self.service.refresh_batch(
            (self.activity(observed_at),), now=observed_at
        )
        self.assertEqual({"skipped_non_reusable_max_duration": 1}, result)
        self.assertEqual(self.now, self.visit().last_seen_at)

    def test_closed_visit_is_not_reopened(self):
        with self.sessions() as db:
            visit = db.get(Visit, self.visit_id)
            visit.exit_time = self.now + timedelta(seconds=1)
            visit.closed_reason = "operator_closed"
            db.commit()
        result = self.service.refresh_batch(
            (self.activity(self.now + timedelta(seconds=20)),),
            now=self.now + timedelta(seconds=20),
        )
        self.assertEqual({"skipped_closed_visit": 1}, result)
        current = self.visit()
        self.assertEqual(self.now, current.last_seen_at)
        self.assertEqual(self.now + timedelta(seconds=1), current.exit_time)
        self.assertEqual("operator_closed", current.closed_reason)

    def test_store_or_member_mismatch_fails_safe(self):
        result = self.service.refresh_batch(
            (
                self.activity(
                    self.now + timedelta(seconds=10), store_id="store-2"
                ),
                self.activity(
                    self.now + timedelta(seconds=11), member_id="member-2"
                ),
            ),
            now=self.now + timedelta(seconds=11),
        )
        self.assertEqual({"skipped_provenance_mismatch": 2}, result)
        self.assertEqual(self.now, self.visit().last_seen_at)

    def test_missing_canonical_visit_has_distinct_safe_outcome(self):
        result = self.service.refresh_batch(
            (
                self.activity(
                    self.now + timedelta(seconds=10),
                    canonical_visit_id="missing-visit",
                ),
            ),
            now=self.now + timedelta(seconds=10),
        )
        self.assertEqual({"skipped_canonical_unresolved": 1}, result)
        self.assertEqual(self.now, self.visit().last_seen_at)

    def test_future_anomaly_does_not_advance_visit(self):
        result = self.service.refresh_batch(
            (self.activity(self.now + timedelta(seconds=31)),), now=self.now
        )
        self.assertEqual({"skipped_future_anomaly": 1}, result)
        self.assertEqual(self.now, self.visit().last_seen_at)

    def test_out_of_order_multi_camera_activity_keeps_maximum_event_time(self):
        result = self.service.refresh_batch(
            (
                self.activity(
                    self.now + timedelta(seconds=7),
                    camera_id="camera-1",
                    track_id=7,
                ),
                self.activity(
                    self.now + timedelta(seconds=3),
                    camera_id="camera-2",
                    track_id=8,
                ),
            ),
            now=self.now + timedelta(seconds=7),
        )
        self.assertEqual({"updated": 2}, result)
        self.assertEqual(self.now + timedelta(seconds=7), self.visit().last_seen_at)

    def test_multi_camera_batch_is_order_independent_across_inactivity_window(self):
        result = self.service.refresh_batch(
            (
                self.activity(
                    self.now + timedelta(minutes=40),
                    camera_id="camera-2",
                    track_id=8,
                ),
                self.activity(
                    self.now + timedelta(minutes=20),
                    camera_id="camera-1",
                    track_id=7,
                ),
            ),
            now=self.now + timedelta(minutes=40),
        )
        self.assertEqual({"updated": 2}, result)
        self.assertEqual(self.now + timedelta(minutes=40), self.visit().last_seen_at)

    def test_refresh_does_not_modify_arrival_member_or_visit_lifecycle(self):
        with self.sessions() as db:
            visit = db.get(Visit, self.visit_id)
            before_visit = (
                visit.entry_time,
                visit.exit_time,
                visit.closed_reason,
                visit.superseded_by_visit_id,
            )
            arrival = db.get(MemberArrival, self.arrival_id)
            before_arrival = (
                arrival.visit_id,
                arrival.member_id,
                arrival.occurred_at,
                arrival.arrival_source,
            )
            member = db.get(Member, "member-1")
            before_member = (member.visit_count, member.last_visit)

        self.service.refresh_batch(
            (self.activity(self.now + timedelta(seconds=10)),),
            now=self.now + timedelta(seconds=10),
        )

        with self.sessions() as db:
            visit = db.get(Visit, self.visit_id)
            self.assertEqual(before_visit, (
                visit.entry_time,
                visit.exit_time,
                visit.closed_reason,
                visit.superseded_by_visit_id,
            ))
            arrival = db.get(MemberArrival, self.arrival_id)
            self.assertEqual(before_arrival, (
                arrival.visit_id,
                arrival.member_id,
                arrival.occurred_at,
                arrival.arrival_source,
            ))
            member = db.get(Member, "member-1")
            self.assertEqual(before_member, (member.visit_count, member.last_visit))


class RecordingVisitActivityService:
    def __init__(self):
        self.batches = []

    def refresh_batch(self, activities, *, now=None):
        batch = tuple(activities)
        self.batches.append((batch, now))
        return {"updated": len(batch)}


class VisitActivityRefresherTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 5, 6)
        self.clock_value = [self.now]
        self.cache = CameraTrackIdentityCache(clock=lambda: self.clock_value[0])
        self.service = RecordingVisitActivityService()
        self.refresher = VisitActivityRefresher(
            self.service,
            track_identity_cache=self.cache,
            refresh_interval_seconds=5.0,
            clock=lambda: self.clock_value[0],
        )
        self.bind(camera_id="camera-1", track_id=7)

    def bind(
        self,
        *,
        camera_id: str,
        track_id: int,
        runtime_id: str = "runtime-1",
        member_id: str = "member-1",
        visit_id: str = "visit-1",
    ):
        self.cache.bind(
            TrackIdentityBinding(
                binding_id=f"binding-{camera_id}-{track_id}",
                evaluation_id=f"evaluation-{camera_id}-{track_id}",
                store_id="store-1",
                camera_id=camera_id,
                runtime_id=runtime_id,
                track_id=track_id,
                track_token=f"store-1:{camera_id}:{runtime_id}:{track_id}",
                member_id=member_id,
                member_tier="gold",
                visit_id=visit_id,
                recognition_event_id=f"recognition-{camera_id}-{track_id}",
                status="confirmed",
                method="spatial_face_track",
                confidence=0.92,
                recognized_at=self.now,
                confirmed_at=self.now,
                expires_at=self.now + timedelta(minutes=1),
                canonical_visit_id=visit_id,
            )
        )

    def observe(self, *, camera_id="camera-1", runtime_id="runtime-1", track_id=7,
                track_token=None, observed_at=None):
        return self.refresher.observe_track(
            store_id="store-1",
            camera_id=camera_id,
            runtime_instance_id=runtime_id,
            track_id=track_id,
            track_token=track_token
            or f"store-1:{camera_id}:{runtime_id}:{track_id}",
            observed_at=observed_at or self.clock_value[0],
        )

    def test_exact_confirmed_cache_identity_is_accepted(self):
        self.assertTrue(self.observe())
        self.assertEqual(1, self.refresher.run_once())
        activity = self.service.batches[0][0][0]
        self.assertEqual("binding-camera-1-7", activity.binding_id)
        self.assertEqual("member-1", activity.member_id)
        self.assertEqual("visit-1", activity.canonical_visit_id)
        self.assertEqual("store-1:camera-1:runtime-1:7", activity.track_token)

    def test_missing_wrong_runtime_or_wrong_token_is_rejected(self):
        self.assertFalse(self.observe(track_id=8))
        self.assertFalse(self.observe(runtime_id="runtime-2"))
        self.assertFalse(self.observe(track_token="store-1:camera-1:runtime-1:99"))
        self.assertEqual(0, self.refresher.run_once())
        self.assertEqual([], self.service.batches)

    def test_probable_or_non_confirmed_identity_is_rejected(self):
        class ProbableCache:
            def resolve_exact(self, **_scope):
                return {
                    "binding_id": "probable-1",
                    "member_id": "member-1",
                    "visit_id": "visit-1",
                    "identity_status": "probable",
                }

        refresher = VisitActivityRefresher(
            self.service,
            track_identity_cache=ProbableCache(),
            clock=lambda: self.clock_value[0],
        )
        self.assertFalse(refresher.observe_track(
            store_id="store-1", camera_id="camera-1",
            runtime_instance_id="runtime-1", track_id=7,
            track_token="store-1:camera-1:runtime-1:7",
            observed_at=self.now,
        ))
        self.assertEqual(0, refresher.run_once())

    def test_twenty_five_fps_coalesces_to_latest_event_time(self):
        for frame in range(125):
            observed_at = self.now + timedelta(seconds=frame / 25)
            self.assertTrue(self.observe(observed_at=observed_at))
        self.clock_value[0] = self.now + timedelta(seconds=5)
        self.assertEqual(1, self.refresher.run_once())
        self.assertEqual(1, len(self.service.batches))
        activity = self.service.batches[0][0][0]
        self.assertEqual(self.now + timedelta(seconds=124 / 25), activity.observed_at)

        self.assertTrue(self.observe(observed_at=self.now + timedelta(seconds=6)))
        self.clock_value[0] = self.now + timedelta(seconds=10)
        self.assertEqual(1, self.refresher.run_once())
        self.assertEqual(2, len(self.service.batches))

    def test_multiple_cameras_for_one_visit_are_batched_and_keep_event_times(self):
        self.bind(camera_id="camera-2", track_id=8)
        self.assertTrue(self.observe(
            camera_id="camera-1", track_id=7,
            observed_at=self.now + timedelta(seconds=7),
        ))
        self.assertTrue(self.observe(
            camera_id="camera-2", track_id=8,
            observed_at=self.now + timedelta(seconds=3),
        ))
        self.assertEqual(2, self.refresher.run_once())
        event_times = {
            activity.observed_at for activity in self.service.batches[0][0]
        }
        self.assertEqual(
            {
                self.now + timedelta(seconds=3),
                self.now + timedelta(seconds=7),
            },
            event_times,
        )

    def test_transient_flush_failure_restores_latest_activity(self):
        class FailingOnceService(RecordingVisitActivityService):
            def __init__(self):
                super().__init__()
                self.failed = False

            def refresh_batch(self, activities, *, now=None):
                if not self.failed:
                    self.failed = True
                    raise OSError("database unavailable")
                return super().refresh_batch(activities, now=now)

        service = FailingOnceService()
        refresher = VisitActivityRefresher(
            service,
            track_identity_cache=self.cache,
            clock=lambda: self.clock_value[0],
        )
        self.assertTrue(refresher.observe_track(
            store_id="store-1", camera_id="camera-1",
            runtime_instance_id="runtime-1", track_id=7,
            track_token="store-1:camera-1:runtime-1:7",
            observed_at=self.now + timedelta(seconds=1),
        ))
        with self.assertRaises(OSError):
            refresher.run_once()
        self.assertTrue(refresher.observe_track(
            store_id="store-1", camera_id="camera-1",
            runtime_instance_id="runtime-1", track_id=7,
            track_token="store-1:camera-1:runtime-1:7",
            observed_at=self.now + timedelta(seconds=2),
        ))
        self.assertEqual(1, refresher.run_once())
        self.assertEqual(
            self.now + timedelta(seconds=2), service.batches[0][0][0].observed_at
        )

    def test_stop_rejects_late_observation_and_flush(self):
        self.assertTrue(self.observe())
        self.refresher.request_stop()
        self.assertFalse(self.observe(observed_at=self.now + timedelta(seconds=1)))
        self.assertEqual(0, self.refresher.run_once())
        self.assertEqual([], self.service.batches)


if __name__ == "__main__":
    unittest.main()
