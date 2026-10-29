from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.models import (
    BehaviorEvent,
    CustomerEntryEvent,
    DeviceRecognitionInbox,
    Member,
    MemberArrival,
    MemberArrivalEffect,
    MemberEntryEvent,
    PurchaseRecord,
    Visit,
)
from agent.schema_evolution import evolve_schema
from agent.visit_merge_service import VisitMergeService


class VisitMergeServiceTests(unittest.TestCase):
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
        db.add(Member(id="member-1", name="Member One"))
        db.commit()
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_entry_backed_visit_is_canonical_and_associations_move(self):
        db = self.sessions()
        db.add(CustomerEntryEvent(
            id="entry-canonical",
            entry_key="entry-canonical",
            dedup_key="entry-canonical",
            store_id="store-1",
            camera_id="camera-1",
            track_id=1,
            tracker_track_id=1,
            track_token="store-1:camera-1:runtime:1",
            runtime_session_id="runtime",
            direction="outside_to_inside",
            occurred_at=self.now,
            confirmed_at=self.now,
            staff_status="customer",
            staff_source="behavior_role",
            confidence=0.9,
        ))
        db.flush()
        canonical = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_event_id="entry-canonical",
            entry_time=self.now,
            visit_source="entrance_crossing",
            verification_status="confirmed",
        )
        duplicate = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now,
            visit_source="recognition_only",
            verification_status="confirmed",
        )
        db.add_all((canonical, duplicate))
        db.flush()
        db.add(BehaviorEvent(
            event_key="behavior-1",
            event_type="merchandise_dwell",
            assessment="confirmed",
            camera_id="camera-1",
            customer_track_id=1,
            customer_track_token="store-1:camera-1:runtime:1",
            track_token="store-1:camera-1:runtime:1",
            visit_id=duplicate.id,
            member_id="member-1",
            started_at=self.now,
            ended_at=self.now,
            occurred_at=self.now,
            duration_seconds=0,
            details={},
        ))
        db.add(MemberEntryEvent(
            event_id="recognition-1",
            device_id="device-1",
            member_id="member-1",
            occurred_at=self.now,
            similarity=0.9,
            identity_time=self.now,
            visit_id=duplicate.id,
        ))
        db.add(DeviceRecognitionInbox(
            canonical_event_id="recognition-1",
            device_id="device-1",
            source="push",
            source_event_id="source-1",
            occurred_at=self.now,
            member_id="member-1",
            unique_id="member-1",
            similarity=0.9,
            status="processed",
            recognition_status="accepted",
            store_resolution_status="resolved",
            track_association_status="no_track_match",
            matched_visit_id=duplicate.id,
        ))
        db.add(PurchaseRecord(
            member_id="member-1",
            product_name="item",
            total_price=1,
            visit_id=duplicate.id,
        ))
        db.commit()
        duplicate_id = duplicate.id
        canonical_id = canonical.id
        db.close()

        result = VisitMergeService(self.sessions, clock=lambda: self.now).merge_reusable(
            member_id="member-1", store_id="store-1", now=self.now
        )

        db = self.sessions()
        try:
            self.assertEqual(canonical_id, result.canonical_visit_id)
            self.assertIn(duplicate_id, result.superseded_visit_ids)
            duplicate = db.get(Visit, duplicate_id)
            self.assertEqual(canonical_id, duplicate.superseded_by_visit_id)
            self.assertEqual(canonical_id, db.query(BehaviorEvent).one().visit_id)
            self.assertEqual(canonical_id, db.query(MemberEntryEvent).one().visit_id)
            self.assertEqual(canonical_id, db.query(DeviceRecognitionInbox).one().matched_visit_id)
            self.assertEqual(canonical_id, db.query(PurchaseRecord).one().visit_id)
        finally:
            db.close()

    def test_merge_converges_arrivals_effects_and_member_statistics(self):
        db = self.sessions()
        canonical = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now,
            visit_source="entrance_crossing",
            verification_status="confirmed",
        )
        duplicate = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now,
            visit_source="recognition_only",
            verification_status="confirmed",
        )
        db.add_all((canonical, duplicate))
        db.flush()
        later = MemberArrival(
            visit_id=canonical.id,
            member_id="member-1",
            occurred_at=self.now,
            arrival_source="device_recognition",
        )
        earlier = MemberArrival(
            visit_id=duplicate.id,
            member_id="member-1",
            occurred_at=self.now - timedelta(seconds=1),
            arrival_source="device_recognition",
        )
        db.add_all((later, earlier))
        db.flush()
        pending = MemberArrivalEffect(
            member_arrival_id=later.id,
            effect_type="welcome_display",
            status="pending",
        )
        succeeded = MemberArrivalEffect(
            member_arrival_id=earlier.id,
            effect_type="welcome_display",
            status="succeeded",
            executed_at=self.now,
        )
        db.add_all((pending, succeeded))
        db.commit()
        canonical_id = canonical.id
        earlier_id = earlier.id
        succeeded_id = succeeded.id
        db.close()

        VisitMergeService(self.sessions, clock=lambda: self.now).merge_reusable(
            member_id="member-1", store_id="store-1", now=self.now
        )

        db = self.sessions()
        try:
            winner = db.get(MemberArrival, earlier_id)
            effect = db.get(MemberArrivalEffect, succeeded_id)
            member = db.get(Member, "member-1")
            self.assertEqual(canonical_id, winner.visit_id)
            self.assertIsNone(winner.superseded_by_arrival_id)
            self.assertEqual(
                earlier_id,
                db.query(MemberArrival)
                .filter(MemberArrival.id != earlier_id)
                .one()
                .superseded_by_arrival_id,
            )
            self.assertEqual(earlier_id, effect.member_arrival_id)
            self.assertIsNone(effect.superseded_by_effect_id)
            self.assertEqual(
                succeeded_id,
                db.query(MemberArrivalEffect)
                .filter(MemberArrivalEffect.id != succeeded_id)
                .one()
                .superseded_by_effect_id,
            )
            self.assertEqual(1, member.visit_count)
        finally:
            db.close()

    def test_open_visit_outside_reusable_window_is_not_merged(self):
        db = self.sessions()
        recent = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now - timedelta(minutes=5),
            last_seen_at=self.now - timedelta(minutes=5),
        )
        stale = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now - timedelta(hours=2),
            last_seen_at=self.now - timedelta(hours=1),
        )
        db.add_all((recent, stale))
        db.commit()
        stale_id = stale.id
        recent_id = recent.id
        db.close()

        result = VisitMergeService(self.sessions, clock=lambda: self.now).merge_reusable(
            member_id="member-1", store_id="store-1", now=self.now
        )

        self.assertEqual(recent_id, result.canonical_visit_id)
        self.assertEqual((), result.superseded_visit_ids)
        db = self.sessions()
        try:
            self.assertIsNone(db.get(Visit, stale_id).superseded_by_visit_id)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
