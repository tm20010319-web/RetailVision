from __future__ import annotations

import unittest
from datetime import datetime

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.association_projection import AssociationOutcomeProjectionWorker
from agent.db import configure_sqlite_foreign_keys
from agent.models import AssociationOutcomeRebuildJob, DeviceRecognitionInbox, Member, MemberArrival, Visit
from agent.schema_evolution import evolve_schema


class AssociationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 5, 6)
        self.engine = configure_sqlite_foreign_keys(create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.sessions()
        db.add(Member(id="member-1", name="Member One"))
        db.flush()
        visit = Visit(id="visit-1", member_id="member-1", store_id="store-1", entry_time=self.now)
        db.add(visit)
        db.flush()
        db.add(DeviceRecognitionInbox(
            canonical_event_id="event-1", device_id="device-1", source="push", source_event_id="source-1",
            occurred_at=self.now, member_id="member-1", unique_id="member-1", similarity=0.9,
            recognition_status="accepted", store_resolution_status="resolved", matched_visit_id="visit-1",
            projection_pending=True,
        ))
        db.add(MemberArrival(visit_id="visit-1", member_id="member-1", recognition_event_id="event-1", occurred_at=self.now))
        db.add(AssociationOutcomeRebuildJob(recognition_event_id="event-1", visit_id="visit-1", reason="test"))
        db.commit()
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_rebuilds_outcome_from_effective_arrival_facts(self):
        self.assertEqual(1, AssociationOutcomeProjectionWorker(self.sessions, clock=lambda: self.now).run_once())
        db = self.sessions()
        try:
            self.assertEqual("new_member_arrival", db.get(DeviceRecognitionInbox, "event-1").association_outcome)
            self.assertFalse(db.get(DeviceRecognitionInbox, "event-1").projection_pending)
            self.assertEqual("succeeded", db.query(AssociationOutcomeRebuildJob).one().status)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
