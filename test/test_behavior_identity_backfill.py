from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.behavior_events import BehaviorEventInput, BehaviorEventStore
from agent.db import configure_sqlite_foreign_keys
from agent.models import (
    BehaviorEvent,
    Member,
    TrackAssociationEvaluation,
    TrackIdentityBinding,
    Visit,
)
from agent.schema_evolution import evolve_schema


class BehaviorIdentityBackfillTests(unittest.TestCase):
    def setUp(self):
        self.engine = configure_sqlite_foreign_keys(create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.now = datetime(2026, 8, 5, 6, 0, 0)
        db = self.sessions()
        db.add(Member(id="member-1", name="Member One", tier="gold"))
        db.commit()
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_confirmed_backfill_updates_only_prior_same_track_events(self):
        token = "store-1:camera-1:runtime-1:7"
        store = BehaviorEventStore(self.sessions, store_id="store-1")
        behavior = store.record_behavior(
            BehaviorEventInput(
                event_key="dwell-backfill",
                event_type="merchandise_dwell",
                assessment="confirmed",
                camera_id="camera-1",
                customer_track_id=7,
                customer_track_token=token,
                started_at=self.now - timedelta(seconds=10),
                ended_at=self.now - timedelta(seconds=9),
            )
        )
        db = self.sessions()
        visit = Visit(
            member_id="member-1",
            store_id="store-1",
            entry_time=self.now - timedelta(seconds=20),
            last_seen_at=self.now - timedelta(seconds=20),
            is_member=True,
            visitor_type="member",
        )
        db.add(visit)
        db.commit()
        visit_id = visit.id
        db.add(TrackAssociationEvaluation(
            id="evaluation-1",
            recognition_event_id="recognition-1",
            observed_at=self.now,
            result="confirmed",
            mode="enabled",
        ))
        db.add(TrackIdentityBinding(
            id="binding-1",
            recognition_event_id="recognition-1",
            evaluation_id="evaluation-1",
            store_id="store-1",
            camera_id="camera-1",
            runtime_owner_id="owner-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=token,
            member_id="member-1",
            visit_id=visit_id,
            binding_level="confirmed",
            status="confirmed",
            identity_source="device_api",
            identity_confidence=0.95,
            valid_from=self.now,
            expires_at=self.now + timedelta(seconds=2),
        ))
        db.commit()
        db.close()

        result = store.attach_identity_to_track(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=token,
            member_id="member-1",
            canonical_visit_id=visit_id,
            member_tier="gold",
            evaluation_id="evaluation-1",
            binding_id="binding-1",
            policy_version_id="policy-1",
            algorithm_version="algorithm-1",
            parameter_version="parameters-1",
            recognized_at=self.now,
            identity_source="device_api",
            identity_confidence=0.95,
        )

        db = self.sessions()
        try:
            row = db.get(BehaviorEvent, behavior.id)
            self.assertEqual(1, result["applied_count"])
            self.assertEqual(visit_id, row.visit_id)
            self.assertEqual("member-1", row.member_id)
            self.assertEqual("confirmed", row.identity_binding_level)
            self.assertIsNotNone(row.backfilled_at)
            self.assertEqual(
                self.now - timedelta(seconds=20),
                db.get(Visit, visit_id).last_seen_at,
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
