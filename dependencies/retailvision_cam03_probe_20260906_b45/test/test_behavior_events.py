from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.behavior_events import (
    BehaviorEventConflictError,
    BehaviorEventError,
    BehaviorEventInput,
    BehaviorEventStore,
    TrafficQueryService,
)
from agent.schema_evolution import evolve_schema
from agent.customer_entry_visit import (
    CustomerEntryObservation,
    CustomerEntryVisit,
    CustomerVisitResult,
)
from agent.models import (
    BehaviorEvent,
    CustomerEntryEvent,
    EvidenceClip,
    IdentityEvidence,
    Member,
    Visit,
)


class BehaviorEventTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.store = BehaviorEventStore(self.sessions)
        self.entry_visits = CustomerEntryVisit(self.sessions)
        self.query = TrafficQueryService(self.sessions)
        self.now = datetime(2026, 7, 27, 12, 0, 0)

    def tearDown(self):
        self.engine.dispose()

    def _entry(self, key, occurred_at, track_id=1, member_id=None, tier=None):
        return self.entry_visits.confirm_entry(
            CustomerEntryObservation(
                store_id="store-1",
                camera_id="entrance-1",
                tracker_track_id=track_id,
                runtime_session_id="runtime-1",
                direction="outside_to_inside",
                occurred_at=occurred_at,
                confirmed_at=occurred_at,
                staff_status="customer",
                staff_source="behavior_role",
                staff_confidence=None,
                entry_confidence=0.94,
                dedup_key=key,
            )
        )

    def test_schema_contains_behavior_tables(self):
        names = set(inspect(self.engine).get_table_names())
        self.assertTrue(
            {"customer_entry_events", "behavior_events", "evidence_clips"} <= names
        )

    def test_entry_is_idempotent_and_anonymous_has_no_tier(self):
        first = self._entry("entry-1", self.now)
        second = self._entry("entry-1", self.now)

        db = self.sessions()
        try:
            self.assertEqual(first.id, second.id)
            self.assertEqual(1, db.query(CustomerEntryEvent).count())
            row = db.query(CustomerEntryEvent).one()
            self.assertIsNone(row.member_id)
            self.assertIsNone(row.member_tier)
            self.assertEqual(1, row.track_id)
        finally:
            db.close()

    def test_entry_observation_does_not_snapshot_unverified_member_identity(self):
        db = self.sessions()
        try:
            db.add(Member(id="member-1", name="Gold", tier="GOLD"))
            db.commit()
        finally:
            db.close()

        result = self._entry(
            "entry-member",
            self.now,
            member_id="member-1",
            tier="GOLD",
        )

        db = self.sessions()
        try:
            row = db.get(CustomerEntryEvent, result.entry_event_id)
            self.assertIsNone(row.member_id)
            self.assertIsNone(row.member_tier)
        finally:
            db.close()

    def test_behavior_and_evidence_are_created_atomically_and_idempotently(self):
        value = BehaviorEventInput(
            event_key="greeting:customer-1",
            event_type="staff_greeting",
            assessment="confirmed",
            camera_id="entrance-1",
            customer_track_id=1,
            customer_track_token="runtime-1:1",
            staff_track_id=8,
            staff_track_token="runtime-1:8",
            started_at=self.now,
            ended_at=self.now + timedelta(seconds=4),
            confidence=0.91,
            details={"staff_travel_metres": 0.8, "minimum_distance_metres": 1.2},
            evidence_start=self.now - timedelta(seconds=3),
            evidence_end=self.now + timedelta(seconds=7),
        )

        first = self.store.record_behavior(value)
        second = self.store.record_behavior(value)

        db = self.sessions()
        try:
            self.assertEqual(first.id, second.id)
            self.assertEqual(1, db.query(BehaviorEvent).count())
            self.assertEqual(1, db.query(EvidenceClip).count())
            clip = db.get(EvidenceClip, first.evidence_clip_id)
            self.assertEqual(first.id, clip.owner_id)
            self.assertEqual(self.now - timedelta(seconds=3), clip.requested_start)
            self.assertEqual(self.now + timedelta(seconds=7), clip.requested_end)
            self.assertEqual("runtime-1:1", db.get(BehaviorEvent, first.id).track_token)
            self.assertIsNone(db.get(BehaviorEvent, first.id).visit_id)
        finally:
            db.close()

    def test_behavior_can_be_saved_without_a_visit(self):
        behavior = self.store.record_behavior(BehaviorEventInput(
            event_key="dwell:anonymous:no-visit",
            event_type="merchandise_dwell",
            assessment="confirmed",
            camera_id="display-1",
            customer_track_id=21,
            customer_track_token="runtime-1:display-1:21",
            started_at=self.now,
            ended_at=self.now + timedelta(seconds=4),
        ))

        db = self.sessions()
        try:
            stored = db.get(BehaviorEvent, behavior.id)
            self.assertIsNone(stored.entry_event_id)
            self.assertIsNone(stored.visit_id)
            self.assertEqual("runtime-1:display-1:21", stored.track_token)
        finally:
            db.close()

    def test_behavior_visit_can_be_backfilled_by_track_token(self):
        token = "store-1:entrance-1:runtime-1:7"
        behavior = self.store.record_behavior(BehaviorEventInput(
            event_key="reach:entry-track:display-1",
            event_type="merchandise_reach",
            assessment="confirmed",
            camera_id="entrance-1",
            customer_track_id=7,
            customer_track_token=token,
            started_at=self.now,
            ended_at=self.now + timedelta(seconds=1),
        ))
        visit = CustomerEntryVisit(self.sessions).confirm_entry(CustomerEntryObservation(
            store_id="store-1",
            camera_id="entrance-1",
            tracker_track_id=7,
            runtime_session_id="runtime-1",
            direction="outside_to_inside",
            occurred_at=self.now,
            confirmed_at=self.now,
            staff_status="customer",
            staff_source="blue_vest",
            staff_confidence=0.95,
            entry_confidence=0.9,
            dedup_key="entry-backfill-1",
        ))

        updated = self.store.attach_visit(
            track_token=token,
            visit_id=visit.id,
            entry_event_id=visit.entry_event_id,
        )

        db = self.sessions()
        try:
            stored = db.get(BehaviorEvent, behavior.id)
            self.assertEqual(1, updated)
            self.assertEqual(visit.id, stored.visit_id)
            self.assertEqual(visit.entry_event_id, stored.entry_event_id)
        finally:
            db.close()

    def test_behavior_with_same_entry_can_be_backfilled(self):
        token = "store-1:entrance-1:runtime-1:17"
        behavior = self.store.record_behavior(BehaviorEventInput(
            event_key="reach:same-entry",
            event_type="merchandise_reach",
            assessment="confirmed",
            camera_id="entrance-1",
            customer_track_id=17,
            customer_track_token=token,
            started_at=self.now,
            ended_at=self.now + timedelta(seconds=1),
        ))
        visit = self._domain_visit(token, 17, "entry-same-backfill")
        db = self.sessions()
        try:
            db.get(BehaviorEvent, behavior.id).entry_event_id = visit.entry_event_id
            db.commit()
        finally:
            db.close()

        updated = self.store.attach_visit(track_token=token, visit_id=visit.id)

        db = self.sessions()
        try:
            stored = db.get(BehaviorEvent, behavior.id)
            self.assertEqual(1, updated)
            self.assertEqual(visit.id, stored.visit_id)
            self.assertEqual(visit.entry_event_id, stored.entry_event_id)
        finally:
            db.close()

    def test_behavior_with_different_entry_is_not_overwritten(self):
        token = "store-1:entrance-1:runtime-1:18"
        behavior = self.store.record_behavior(BehaviorEventInput(
            event_key="reach:different-entry",
            event_type="merchandise_reach",
            assessment="confirmed",
            camera_id="entrance-1",
            customer_track_id=18,
            customer_track_token=token,
            started_at=self.now,
            ended_at=self.now + timedelta(seconds=1),
        ))
        other_entry = self._entry("legacy-other-entry", self.now, track_id=88)
        visit = self._domain_visit(token, 18, "entry-target-backfill")
        db = self.sessions()
        try:
            db.get(BehaviorEvent, behavior.id).entry_event_id = other_entry.id
            db.commit()
        finally:
            db.close()

        with self.assertRaises(BehaviorEventConflictError):
            self.store.attach_visit(track_token=token, visit_id=visit.id)

        db = self.sessions()
        try:
            stored = db.get(BehaviorEvent, behavior.id)
            self.assertEqual(other_entry.id, stored.entry_event_id)
            self.assertIsNone(stored.visit_id)
        finally:
            db.close()

    def test_recent_entries_uses_visit_as_current_identity_authority(self):
        visit = self._domain_visit(
            "store-1:entrance-1:runtime-1:19", 19, "entry-query-upgrade"
        )
        db = self.sessions()
        try:
            db.add(Member(
                id="member-query",
                name="Query Member",
                tier="gold",
                visit_count=0,
            ))
            db.commit()
            db.add(IdentityEvidence(
                canonical_event_id="evidence-query",
                device_id="entrance-1",
                source="push",
                source_event_id="source-query",
                occurred_at=self.now,
                member_id="member-query",
                unique_id="member-query",
                similarity=0.92,
                status="processed",
                association_status="pending",
            ))
            db.commit()
        finally:
            db.close()
        CustomerEntryVisit(self.sessions).attach_member_identity(
            visit.id, "evidence-query"
        )

        item = self.query.recent_entries(limit=1)[0]

        self.assertEqual("member-query", item["member_id"])
        self.assertEqual("gold", item["member_tier"])
        self.assertEqual("identified_member", item["identity_status"])
        self.assertEqual("member", item["visitor_type"])
        db = self.sessions()
        try:
            self.assertIsNone(
                db.get(CustomerEntryEvent, visit.entry_event_id).member_id
            )
        finally:
            db.close()

    def _domain_visit(
        self, token: str, track_id: int, dedup_key: str
    ) -> CustomerVisitResult:
        _, camera_id, runtime_session_id, _ = token.split(":")
        return CustomerEntryVisit(self.sessions).confirm_entry(CustomerEntryObservation(
            store_id="store-1",
            camera_id=camera_id,
            tracker_track_id=track_id,
            runtime_session_id=runtime_session_id,
            direction="outside_to_inside",
            occurred_at=self.now,
            confirmed_at=self.now,
            staff_status="customer",
            staff_source="blue_vest",
            staff_confidence=0.95,
            entry_confidence=0.9,
            dedup_key=dedup_key,
        ))

    def test_exact_identity_backfill_is_idempotent_and_preserves_entry(self):
        token = "store-1:entrance-1:runtime-1:27"
        scoped_store = BehaviorEventStore(self.sessions, store_id="store-1")
        behavior = scoped_store.record_behavior(BehaviorEventInput(
            event_key="dwell:binding-backfill",
            event_type="merchandise_dwell",
            assessment="confirmed",
            camera_id="entrance-1",
            customer_track_id=27,
            customer_track_token=token,
            started_at=self.now - timedelta(seconds=5),
            ended_at=self.now - timedelta(seconds=4),
        ))
        visit = self._domain_visit(token, 27, "entry-binding-backfill")
        with self.sessions() as db:
            db.add(Member(id="member-backfill", name="Backfill", tier="gold"))
            row = db.get(Visit, visit.id)
            row.member_id = "member-backfill"
            db.commit()

        values = dict(
            store_id="store-1",
            camera_id="entrance-1",
            runtime_instance_id="runtime-1",
            track_id=27,
            track_token=token,
            member_id="member-backfill",
            canonical_visit_id=visit.id,
            member_tier="gold",
            evaluation_id="evaluation-27",
            binding_id="binding-27",
            policy_version_id="policy-1",
            algorithm_version="algorithm-1",
            parameter_version="parameters-1",
            recognized_at=self.now,
            identity_source="verified_face_spatial_evidence",
            identity_confidence=0.95,
        )
        first = scoped_store.attach_identity_to_track(**values)
        second = scoped_store.attach_identity_to_track(**values)

        self.assertEqual(1, first["applied_count"])
        self.assertEqual(1, second["already_applied_count"])
        with self.sessions() as db:
            stored = db.get(BehaviorEvent, behavior.id)
            self.assertEqual("binding-27", stored.binding_id)
            self.assertEqual("evaluation-27", stored.evaluation_id)
            self.assertEqual(visit.id, stored.visit_id)
            self.assertIsNone(stored.entry_event_id)

    def test_identity_backfill_keeps_different_binding_provenance(self):
        token = "store-1:entrance-1:runtime-1:28"
        scoped_store = BehaviorEventStore(self.sessions, store_id="store-1")
        behavior = scoped_store.record_behavior(BehaviorEventInput(
            event_key="reach:binding-conflict",
            event_type="merchandise_reach",
            assessment="confirmed",
            camera_id="entrance-1",
            customer_track_id=28,
            customer_track_token=token,
            started_at=self.now - timedelta(seconds=3),
            ended_at=self.now - timedelta(seconds=2),
            binding_id="binding-old",
            evaluation_id="evaluation-old",
        ))
        visit = self._domain_visit(token, 28, "entry-binding-conflict")
        with self.sessions() as db:
            db.add(Member(id="member-conflict", name="Conflict", tier="silver"))
            db.get(Visit, visit.id).member_id = "member-conflict"
            db.commit()

        stats = scoped_store.attach_identity_to_track(
            store_id="store-1",
            camera_id="entrance-1",
            runtime_instance_id="runtime-1",
            track_id=28,
            track_token=token,
            member_id="member-conflict",
            canonical_visit_id=visit.id,
            member_tier="silver",
            evaluation_id="evaluation-new",
            binding_id="binding-new",
            policy_version_id="policy-1",
            algorithm_version="algorithm-1",
            parameter_version="parameters-1",
            recognized_at=self.now,
            identity_source="verified_face_spatial_evidence",
            identity_confidence=0.9,
        )

        self.assertEqual(1, stats["conflict_count"])
        with self.sessions() as db:
            stored = db.get(BehaviorEvent, behavior.id)
            self.assertEqual("binding-old", stored.binding_id)
            self.assertEqual("evaluation-old", stored.evaluation_id)
            self.assertIsNone(stored.member_id)

    def test_peak_window_is_rolling_and_crosses_fixed_bucket_boundary(self):
        for index, offset in enumerate((0, 119, 121, 240)):
            self._entry(
                f"entry-{index}",
                datetime(2026, 7, 27, 10, 3, 0) + timedelta(seconds=offset),
                track_id=index,
            )

        result = self.query.peak_entry_window(days=3, now=self.now)

        self.assertEqual(4, result["count"])
        self.assertEqual(datetime(2026, 7, 27, 10, 3, 0), result["start"])
        self.assertEqual(datetime(2026, 7, 27, 10, 8, 0), result["end"])

    def test_peak_window_uses_latest_start_when_counts_tie(self):
        for index, at in enumerate(
            (
                self.now - timedelta(hours=2),
                self.now - timedelta(hours=2) + timedelta(minutes=1),
                self.now - timedelta(hours=1),
                self.now - timedelta(hours=1) + timedelta(minutes=1),
            )
        ):
            self._entry(f"tie-{index}", at, track_id=index)

        result = self.query.peak_entry_window(days=7, now=self.now)

        self.assertEqual(2, result["count"])
        self.assertEqual(self.now - timedelta(hours=1), result["start"])

    def test_empty_peak_window_and_invalid_lookback(self):
        result = self.query.peak_entry_window(days=3, now=self.now)
        self.assertEqual(0, result["count"])
        self.assertIsNone(result["start"])
        with self.assertRaisesRegex(BehaviorEventError, "days must be 3 or 7"):
            self.query.peak_entry_window(days=1, now=self.now)

    def test_recent_behaviors_hides_output_path(self):
        row = self.store.record_behavior(
            BehaviorEventInput(
                event_key="reach:customer-1:display-1",
                event_type="merchandise_reach",
                assessment="uncertain",
                camera_id="display-1",
                customer_track_id=2,
                customer_track_token="runtime-1:2",
                area_id="display-a",
                started_at=self.now,
                ended_at=self.now + timedelta(seconds=1),
                confidence=0.55,
            )
        )
        db = self.sessions()
        try:
            clip = db.get(EvidenceClip, row.evidence_clip_id)
            clip.status = "ready"
            clip.output_path = "C:/private/evidence.mp4"
            db.commit()
        finally:
            db.close()

        item = self.query.recent_behaviors(limit=1)[0]

        self.assertTrue(item["evidence"]["available"])
        self.assertNotIn("output_path", item["evidence"])


    def test_confirmed_greeting_attribution_is_idempotent_and_aggregated(self):
        store = BehaviorEventStore(
            self.sessions,
            greeting_mock_staff_name="Alice",
        )
        value = BehaviorEventInput(
            event_key="entry:customer-1:greeting",
            event_type="staff_greeting",
            assessment="confirmed",
            camera_id="entrance-1",
            customer_track_id=1,
            customer_track_token="runtime-1:customer-1",
            staff_track_id=8,
            staff_track_token="runtime-1:staff-8",
            started_at=self.now,
            ended_at=self.now + timedelta(seconds=4),
            confidence=0.91,
        )

        first = store.record_behavior(value)
        replay = store.record_behavior(value)

        db = self.sessions()
        try:
            stored = db.get(BehaviorEvent, first.id)
            self.assertEqual(first.id, replay.id)
            self.assertEqual("Alice", stored.staff_name)
            self.assertEqual("configured_mock", stored.staff_attribution_source)
            self.assertEqual(1, db.query(BehaviorEvent).count())
        finally:
            db.close()

        self.assertEqual(
            [
                {
                    "staff_name": "Alice",
                    "greeting_count": 1,
                    "last_greeted_at": (self.now + timedelta(seconds=4)).isoformat(),
                }
            ],
            self.query.staff_greeting_summary(),
        )

        store.record_behavior(
            BehaviorEventInput(
                event_key="entry:customer-2:greeting",
                event_type="staff_greeting",
                assessment="not_observed",
                camera_id="entrance-1",
                customer_track_id=2,
                customer_track_token="runtime-1:customer-2",
                started_at=self.now + timedelta(seconds=10),
                ended_at=self.now + timedelta(seconds=14),
            )
        )
        self.assertEqual(1, self.query.staff_greeting_summary()[0]["greeting_count"])

    def test_demo_greeting_is_visible_but_excluded_from_official_summary(self):
        store = BehaviorEventStore(
            self.sessions,
            greeting_mock_staff_name="Alice",
        )
        official = store.record_behavior(
            BehaviorEventInput(
                event_key="entry:official:greeting",
                event_type="staff_greeting",
                assessment="confirmed",
                camera_id="entrance-1",
                customer_track_id=1,
                customer_track_token="runtime:customer-1",
                staff_track_id=8,
                staff_track_token="runtime:staff-8",
                started_at=self.now,
                ended_at=self.now + timedelta(seconds=2),
            )
        )
        demo = store.record_behavior(
            BehaviorEventInput(
                event_key="entry:demo:greeting",
                event_type="staff_greeting",
                assessment="confirmed",
                camera_id="entrance-1",
                customer_track_id=2,
                customer_track_token="runtime:customer-2",
                staff_track_id=8,
                staff_track_token="runtime:staff-8",
                started_at=self.now + timedelta(seconds=3),
                ended_at=self.now + timedelta(seconds=5),
                is_demo=True,
                details={"greeting_profile": "demo_relaxed"},
            )
        )

        self.assertFalse(official.is_demo)
        self.assertTrue(demo.is_demo)
        self.assertEqual(
            [{
                "staff_name": "Alice",
                "greeting_count": 1,
                "last_greeted_at": (self.now + timedelta(seconds=2)).isoformat(),
            }],
            self.query.staff_greeting_summary(),
        )
        self.assertEqual(
            [{
                "staff_name": "Alice",
                "greeting_count": 1,
                "last_greeted_at": (self.now + timedelta(seconds=5)).isoformat(),
            }],
            self.query.staff_greeting_summary(scope="demo"),
        )
        self.assertEqual(2, self.query.staff_greeting_summary(scope="all")[0]["greeting_count"])
        recent_demo = next(
            item
            for item in self.query.recent_behaviors(limit=10)
            if item["id"] == demo.id
        )
        self.assertTrue(recent_demo["is_demo"])

if __name__ == "__main__":
    unittest.main()
