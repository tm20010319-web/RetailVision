from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.models import (
    DeviceRecognitionInbox,
    Member,
    MemberArrival,
    MemberArrivalEffect,
    TrackAssociationEvaluation,
    Visit,
)
from agent.identity_runtime_lease import IdentityAssociationRuntimeLeaseService
from agent.identity_runtime_lease import RuntimeLeaseUnavailable
from agent.reusable_visit_resolver import (
    RecognitionAssociationRequest,
    ReusableVisitResolver,
)
from agent.schema_evolution import evolve_schema


class ReusableVisitResolverTests(unittest.TestCase):
    def setUp(self):
        self.engine = configure_sqlite_foreign_keys(create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self.now = datetime(2026, 8, 5, 6, 0, 0)
        db = self.sessions()
        db.add(Member(id="member-1", name="Member One", tier="gold"))
        db.commit()
        db.close()
        self.resolver = ReusableVisitResolver(
            self.sessions,
            clock=lambda: self.now,
        )
        self.lease = IdentityAssociationRuntimeLeaseService(
            self.sessions, clock=lambda: self.now
        ).acquire(store_id="store-1", mode="new", owner_id="test-runtime")

    def tearDown(self):
        self.engine.dispose()

    def _inbox(self, event_id: str, occurred_at=None):
        db = self.sessions()
        db.add(DeviceRecognitionInbox(
            canonical_event_id=event_id,
            device_id="device-1",
            source="push",
            source_event_id=event_id,
            occurred_at=occurred_at or self.now,
            store_id="store-1",
            member_id="member-1",
            unique_id="member-1",
            similarity=0.95,
            status="processed",
            recognition_status="accepted",
            store_resolution_status="resolved",
            track_association_status="no_track_match",
        ))
        db.commit()
        db.close()

    def _request(self, event_id: str, occurred_at=None):
        return RecognitionAssociationRequest(
            recognition_event_id=event_id,
            member_id="member-1",
            member_tier="gold",
            device_id="device-1",
            store_id="store-1",
            camera_id="camera-1",
            occurred_at=occurred_at or self.now,
            similarity=0.95,
            track_association_status="no_track_match",
            mode="new",
            runtime_id="test-runtime",
            runtime_owner_id="test-runtime",
            lease_token=self.lease.lease_token,
            lease_generation=self.lease.lease_generation,
        )

    def test_accepted_recognition_creates_one_visit_arrival_and_effects(self):
        self._inbox("recognition-1")

        result = self.resolver.resolve(self._request("recognition-1"))

        db = self.sessions()
        try:
            self.assertEqual("new_member_arrival", result.association_outcome)
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(1, db.query(MemberArrival).count())
            self.assertEqual(4, db.query(MemberArrivalEffect).count())
            visit = db.query(Visit).one()
            arrival = db.query(MemberArrival).one()
            self.assertEqual("store-1", visit.store_id)
            self.assertEqual("member-1", visit.member_id)
            self.assertEqual(visit.id, arrival.visit_id)
        finally:
            db.close()

    def test_second_recognition_reuses_visit_without_second_arrival(self):
        self._inbox("recognition-1")
        first = self.resolver.resolve(self._request("recognition-1"))
        self._inbox("recognition-2", self.now + timedelta(seconds=4))

        second = self.resolver.resolve(
            self._request("recognition-2", self.now + timedelta(seconds=4))
        )

        db = self.sessions()
        try:
            self.assertEqual("new_member_arrival", first.association_outcome)
            self.assertEqual("existing_visit_observation", second.association_outcome)
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(1, db.query(MemberArrival).count())
            self.assertEqual(first.visit_id, second.visit_id)
        finally:
            db.close()

    def test_delayed_arrival_suppresses_realtime_effects_but_keeps_profile(self):
        occurred_at = self.now - timedelta(seconds=40)
        self._inbox("recognition-delayed", occurred_at)

        result = self.resolver.resolve(
            self._request("recognition-delayed", occurred_at)
        )

        db = self.sessions()
        try:
            effects = {
                row.effect_type: (row.status, row.reason)
                for row in db.query(MemberArrivalEffect).all()
            }
            self.assertEqual("new_member_arrival", result.association_outcome)
            self.assertEqual(
                {
                    "welcome_display": ("suppressed", "arrival_effect_delay_exceeded"),
                    "staff_alert": ("suppressed", "arrival_effect_delay_exceeded"),
                    "coupon_issuance": ("suppressed", "arrival_effect_delay_exceeded"),
                    "customer_profile": ("pending", None),
                },
                effects,
            )
        finally:
            db.close()

    def test_expired_recognition_creates_no_visit_or_arrival(self):
        occurred_at = self.now - timedelta(seconds=601)
        self._inbox("recognition-expired", occurred_at)

        result = self.resolver.resolve(
            self._request("recognition-expired", occurred_at)
        )

        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, "recognition-expired")
            self.assertEqual("no_business_outcome", result.association_outcome)
            self.assertIsNone(result.visit_id)
            self.assertEqual("expired", inbox.recognition_status)
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(MemberArrival).count())
        finally:
            db.close()

    def test_future_clock_anomaly_is_terminal_without_business_facts(self):
        occurred_at = self.now + timedelta(seconds=31)
        self._inbox("recognition-future", occurred_at)

        result = self.resolver.resolve(
            self._request("recognition-future", occurred_at)
        )

        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, "recognition-future")
            self.assertEqual("no_business_outcome", result.association_outcome)
            self.assertEqual("time_anomaly", inbox.recognition_status)
            self.assertEqual("future_device_clock_skew", inbox.association_last_error)
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(MemberArrival).count())
        finally:
            db.close()

    def test_recent_activity_keeps_visit_reusable_past_inactivity_duration(self):
        db = self.sessions()
        visit = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now - timedelta(hours=1),
            last_seen_at=self.now - timedelta(seconds=10),
            is_member=True,
            visitor_type="member",
            identity_status="identified_member",
        )
        db.add(visit)
        db.commit()
        visit_id = visit.id
        db.close()
        self._inbox("recognition-active")

        result = self.resolver.resolve(self._request("recognition-active"))

        self.assertEqual(visit_id, result.visit_id)

    def test_inactive_visit_is_closed_and_new_visit_is_created(self):
        db = self.sessions()
        old_visit = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now - timedelta(hours=1),
            last_seen_at=self.now - timedelta(seconds=1801),
            is_member=True,
            visitor_type="member",
            identity_status="identified_member",
        )
        db.add(old_visit)
        db.commit()
        old_visit_id = old_visit.id
        db.close()
        self._inbox("recognition-return")

        result = self.resolver.resolve(self._request("recognition-return"))

        db = self.sessions()
        try:
            old_visit = db.get(Visit, old_visit_id)
            self.assertNotEqual(old_visit_id, result.visit_id)
            self.assertEqual("inactivity_timeout", old_visit.closed_reason)
            self.assertIsNotNone(old_visit.exit_time)
        finally:
            db.close()

    def test_hard_duration_closes_active_visit(self):
        db = self.sessions()
        old_visit = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now - timedelta(seconds=14401),
            last_seen_at=self.now - timedelta(seconds=1),
            is_member=True,
            visitor_type="member",
            identity_status="identified_member",
        )
        db.add(old_visit)
        db.commit()
        old_visit_id = old_visit.id
        db.close()
        self._inbox("recognition-hard-cap")

        result = self.resolver.resolve(self._request("recognition-hard-cap"))

        db = self.sessions()
        try:
            old_visit = db.get(Visit, old_visit_id)
            self.assertNotEqual(old_visit_id, result.visit_id)
            self.assertEqual("max_visit_duration", old_visit.closed_reason)
        finally:
            db.close()

    def test_missing_store_mapping_waits_without_creating_business_facts(self):
        self._inbox("recognition-store-missing")
        db = self.sessions()
        row = db.get(DeviceRecognitionInbox, "recognition-store-missing")
        row.store_id = None
        db.commit()
        db.close()

        result = self.resolver.resolve(self._request("recognition-store-missing"))

        db = self.sessions()
        try:
            row = db.get(DeviceRecognitionInbox, "recognition-store-missing")
            self.assertEqual("pending", result.association_outcome)
            self.assertEqual("waiting_store_mapping", row.status)
            self.assertEqual("missing", row.store_resolution_status)
            self.assertEqual(0, db.query(Visit).count())
        finally:
            db.close()

    def test_store_conflict_is_terminal_without_visit(self):
        self._inbox("recognition-store-conflict")
        db = self.sessions()
        row = db.get(DeviceRecognitionInbox, "recognition-store-conflict")
        row.store_id = "store-2"
        db.commit()
        db.close()

        result = self.resolver.resolve(self._request("recognition-store-conflict"))

        db = self.sessions()
        try:
            row = db.get(DeviceRecognitionInbox, "recognition-store-conflict")
            self.assertEqual("no_business_outcome", result.association_outcome)
            self.assertEqual("conflict", row.store_resolution_status)
            self.assertEqual(0, db.query(Visit).count())
        finally:
            db.close()

    def test_stale_runtime_generation_cannot_write(self):
        self._inbox("recognition-stale-runtime")
        stale_request = self._request("recognition-stale-runtime")
        self.now += timedelta(seconds=31)
        IdentityAssociationRuntimeLeaseService(
            self.sessions, clock=lambda: self.now
        ).acquire(store_id="store-1", mode="new", owner_id="replacement")

        with self.assertRaises(RuntimeLeaseUnavailable):
            self.resolver.resolve(stale_request)

        db = self.sessions()
        try:
            self.assertEqual(0, db.query(Visit).count())
            self.assertIsNone(
                db.get(DeviceRecognitionInbox, "recognition-stale-runtime").matched_visit_id
            )
        finally:
            db.close()

    def test_confirmed_shadow_assessment_is_preserved_without_binding(self):
        self._inbox("recognition-shadow-confirmed")
        track_token = "store-1:camera-1:runtime:1"
        db = self.sessions()
        db.add(TrackAssociationEvaluation(
            id="evaluation-shadow",
            recognition_event_id="recognition-shadow-confirmed",
            assessment_fingerprint="evaluation-shadow",
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="test-runtime",
            runtime_instance_id="test-runtime",
            mode="shadow",
            result="confirmed",
            candidates=[{"track_token": track_token}],
            excluded_candidates=[],
            candidate_count=1,
            observed_at=self.now,
            spatial_evidence={
                "quality_status": "verified",
                "tracks": [{"track_token": track_token, "matched": True}],
            },
        ))
        db.commit()
        db.close()
        request = self._request("recognition-shadow-confirmed")
        request = RecognitionAssociationRequest(**{
            **request.__dict__,
            "track_association_status": "confirmed",
            "track_token": track_token,
            "evaluation_id": "evaluation-shadow",
        })

        result = self.resolver.resolve(request)

        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, "recognition-shadow-confirmed")
            self.assertEqual("new_member_arrival", result.association_outcome)
            self.assertEqual("confirmed", inbox.track_association_status)
            self.assertEqual(request.track_token, inbox.matched_track_token)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()


class ReusableVisitResolverConcurrencyTests(unittest.TestCase):
    def test_five_cameras_create_one_visit_and_one_arrival(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "resolver.db"
            engine = configure_sqlite_foreign_keys(create_engine(
                f"sqlite:///{path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 10},
            ))
            evolve_schema(engine)
            sessions = sessionmaker(
                bind=engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
            )
            now = datetime(2026, 8, 5, 6, 0, 0)
            db = sessions()
            db.add(Member(id="member-1", name="Member One", tier="gold"))
            db.commit()
            for index in range(5):
                db.add(DeviceRecognitionInbox(
                    canonical_event_id=f"recognition-{index}",
                    device_id=f"device-{index}",
                    source="push",
                    source_event_id=f"source-{index}",
                    occurred_at=now,
                    store_id="store-1",
                    member_id="member-1",
                    unique_id="member-1",
                    similarity=0.95,
                    status="processed",
                    recognition_status="accepted",
                    store_resolution_status="resolved",
                    track_association_status="no_track_match",
                ))
            db.commit()
            db.close()

            resolver = ReusableVisitResolver(sessions, clock=lambda: now)
            lease = IdentityAssociationRuntimeLeaseService(
                sessions, clock=lambda: now
            ).acquire(store_id="store-1", mode="new", owner_id="test-runtime")

            def resolve(index):
                return resolver.resolve(RecognitionAssociationRequest(
                    recognition_event_id=f"recognition-{index}",
                    member_id="member-1",
                    member_tier="gold",
                    device_id=f"device-{index}",
                    store_id="store-1",
                    camera_id=f"camera-{index}",
                    occurred_at=now,
                    similarity=0.95,
                    mode="new",
                    runtime_id="test-runtime",
                    runtime_owner_id="test-runtime",
                    lease_token=lease.lease_token,
                    lease_generation=lease.lease_generation,
                ))

            with ThreadPoolExecutor(max_workers=5) as executor:
                results = list(executor.map(resolve, range(5)))

            db = sessions()
            try:
                self.assertEqual(1, db.query(Visit).count())
                self.assertEqual(1, db.query(MemberArrival).count())
                self.assertEqual(4, db.query(MemberArrivalEffect).count())
                self.assertEqual(
                    1,
                    sum(
                        result.association_outcome == "new_member_arrival"
                        for result in results
                    ),
                )
            finally:
                db.close()
                engine.dispose()
