from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.platform.identity_runtime_lease import IdentityAssociationRuntimeLeaseService
from agent.models import DeviceRecognitionInbox, Member, MemberArrival, Visit
from agent.identity.recognition_association_worker import RecognitionAssociationWorker, TrackAssociationCandidate
from agent.visits.reusable_visit_resolver import ReusableVisitResolver
from agent.schema_evolution import evolve_schema


class RecognitionAssociationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 5, 6)
        self.engine = configure_sqlite_foreign_keys(create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.sessions()
        db.add(Member(id="member-1", name="Member One"))
        db.commit()
        db.close()
        self.lease = IdentityAssociationRuntimeLeaseService(
            self.sessions, clock=lambda: self.now
        ).acquire(store_id="store-1", mode="new", owner_id="runtime-1")
        self.resolver = ReusableVisitResolver(self.sessions, clock=lambda: self.now)

    def tearDown(self):
        self.engine.dispose()

    def _event(self, event_id, deadline):
        db = self.sessions()
        db.add(DeviceRecognitionInbox(
            canonical_event_id=event_id, device_id="device-1", source="push", source_event_id=event_id,
            occurred_at=self.now, store_id="store-1", member_id="member-1", unique_id="member-1",
            similarity=0.9, status="processed", recognition_status="accepted",
            store_resolution_status="resolved", track_association_status="pending",
            association_status="pending", association_deadline_at=deadline,
        ))
        db.commit()
        db.close()

    def test_binder_and_fallback_share_resolver(self):
        self._event("binder-event", self.now + timedelta(seconds=5))
        binder = RecognitionAssociationWorker(
            self.sessions, self.resolver, self.lease, clock=lambda: self.now,
            candidate_provider=lambda _row: TrackAssociationCandidate(
                status="probable", camera_id="camera-1", track_token="store-1:camera-1:runtime-1:1",
                method="unique_customer_temporal",
            ),
        )
        self.assertEqual(1, binder.run_once())

        self._event("fallback-event", self.now - timedelta(seconds=1))
        fallback = RecognitionAssociationWorker(
            self.sessions, self.resolver, self.lease, clock=lambda: self.now, fallback=True,
        )
        self.assertEqual(1, fallback.run_once())

        db = self.sessions()
        try:
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(1, db.query(MemberArrival).count())
            self.assertEqual("probable", db.get(DeviceRecognitionInbox, "binder-event").track_association_status)
            self.assertEqual("no_track_match", db.get(DeviceRecognitionInbox, "fallback-event").track_association_status)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
