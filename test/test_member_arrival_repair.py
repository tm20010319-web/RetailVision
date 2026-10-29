from __future__ import annotations

import unittest
from datetime import datetime

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.member_arrival_repair import MemberArrivalRepairService
from agent.models import DeviceRecognitionInbox, Member, MemberArrival, MemberArrivalEffect, Visit
from agent.schema_evolution import evolve_schema


class MemberArrivalRepairTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 5, 6)
        self.engine = configure_sqlite_foreign_keys(create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.sessions()
        db.add(Member(id="member-1", name="Member One"))
        db.flush()
        db.add(DeviceRecognitionInbox(
            canonical_event_id="event-1", device_id="device-1", source="push", source_event_id="event-1",
            occurred_at=self.now, store_id="store-1", member_id="member-1", unique_id="member-1", similarity=0.9,
            recognition_status="accepted", store_resolution_status="resolved",
        ))
        db.flush()
        db.add(Visit(id="visit-1", member_id="member-1", store_id="store-1", device_event_id="event-1", entry_time=self.now))
        db.commit()
        db.close()
        self.service = MemberArrivalRepairService(self.sessions, clock=lambda: self.now)

    def tearDown(self):
        self.engine.dispose()

    def test_dry_run_is_read_only_and_apply_is_idempotent(self):
        self.assertEqual("would_create", self.service.repair_visit("visit-1")["status"])
        db = self.sessions()
        self.assertEqual(0, db.query(MemberArrival).count())
        db.close()

        created = self.service.repair_visit("visit-1", apply=True)
        self.assertEqual("created", created["status"])
        self.assertEqual("no_change", self.service.repair_visit("visit-1", apply=True)["status"])
        db = self.sessions()
        try:
            self.assertEqual(1, db.query(MemberArrival).count())
            self.assertTrue(all(row.status == "suppressed" and row.reason == "repair_mode" for row in db.query(MemberArrivalEffect).all()))
            self.assertEqual(1, db.get(Member, "member-1").visit_count)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
