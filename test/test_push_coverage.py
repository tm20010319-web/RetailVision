from __future__ import annotations

import json
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.identity.device_event_receiver import create_device_event_router
from agent.identity.device_health import DeviceHealthStore
from agent.identity.device_recognition_intake import DeviceRecognitionIntake
import agent.api.member_api as member_api
from agent.identity.push_coverage import PushCoveragePolicy, PushCoverageStore
from agent.schema_evolution import evolve_schema
from agent.models import PushCoverageObservation


BASE = datetime(2026, 8, 18, 11, 0, 0)


class PushCoverageStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.store = PushCoverageStore(self.sessions)
        self.addCleanup(self.engine.dispose)

    def add_receiver(self, instance="instance-a", boot="boot-a", when=BASE):
        self.store.start_receiver(
            device_id="cam02",
            receiver_instance_id=instance,
            boot_id=boot,
            observed_at=when,
        )

    def add_heartbeat(self, when, sequence, *, instance="instance-a", boot="boot-a"):
        return self.store.record_heartbeat(
            device_id="cam02",
            receiver_instance_id=instance,
            boot_id=boot,
            source_event_id=f"hb-{sequence}",
            observed_at=when,
            device_event_at=when,
        )

    def healthy_window(self, *, instance="instance-a", boot="boot-a", start=BASE):
        self.add_receiver(instance, boot, start)
        for sequence, offset in enumerate((0, 30, 60, 90)):
            self.add_heartbeat(
                start + timedelta(seconds=offset),
                sequence,
                instance=instance,
                boot=boot,
            )
        return start, start + timedelta(seconds=90)

    def test_continuous_healthy_window_is_complete(self):
        start, end = self.healthy_window()
        result = self.store.coverage(device_id="cam02", start_at=start, end_at=end)
        self.assertEqual("complete", result.status)
        self.assertIsNone(result.reason)
        self.assertEqual(9, len(result.supporting_observation_ids))
        self.assertEqual(result.material_hash, self.store.coverage(
            device_id="cam02", start_at=start, end_at=end
        ).material_hash)

    def test_heartbeat_after_window_proves_the_final_partial_interval(self):
        self.add_receiver()
        self.add_heartbeat(BASE, 0)
        self.add_heartbeat(BASE + timedelta(seconds=30), 1)
        self.add_heartbeat(BASE + timedelta(seconds=60), 2)
        result = self.store.coverage(
            device_id="cam02",
            start_at=BASE + timedelta(seconds=5),
            end_at=BASE + timedelta(seconds=55),
        )
        self.assertEqual("complete", result.status)
        self.assertEqual(7, len(result.supporting_observation_ids))

    def test_missing_post_window_heartbeat_is_unavailable(self):
        self.add_receiver()
        self.add_heartbeat(BASE, 0)
        self.add_heartbeat(BASE + timedelta(seconds=30), 1)
        result = self.store.coverage(
            device_id="cam02",
            start_at=BASE,
            end_at=BASE + timedelta(seconds=35),
        )
        self.assertEqual("unavailable", result.status)
        self.assertEqual("heartbeat_end_missing", result.reason)

    def test_heartbeat_gap_is_unavailable_and_later_heartbeat_does_not_heal_it(self):
        self.add_receiver()
        self.add_heartbeat(BASE, 0)
        self.add_heartbeat(BASE + timedelta(seconds=30), 1)
        self.add_heartbeat(BASE + timedelta(seconds=90), 2)
        result = self.store.coverage(
            device_id="cam02", start_at=BASE, end_at=BASE + timedelta(seconds=90)
        )
        self.assertEqual("unavailable", result.status)
        self.assertEqual("heartbeat_gap_exceeded", result.reason)

    def test_graceful_restart_does_not_join_instances(self):
        self.add_receiver("instance-a", "boot-a", BASE)
        self.add_heartbeat(BASE, 0)
        self.add_heartbeat(BASE + timedelta(seconds=30), 1)
        self.store.stop_receiver(
            device_id="cam02",
            receiver_instance_id="instance-a",
            boot_id="boot-a",
            observed_at=BASE + timedelta(seconds=60),
        )
        self.add_receiver("instance-b", "boot-b", BASE + timedelta(seconds=70))
        self.add_heartbeat(BASE + timedelta(seconds=70), 2, instance="instance-b", boot="boot-b")
        self.add_heartbeat(BASE + timedelta(seconds=100), 3, instance="instance-b", boot="boot-b")
        result = self.store.coverage(
            device_id="cam02", start_at=BASE, end_at=BASE + timedelta(seconds=100)
        )
        self.assertEqual("unavailable", result.status)
        self.assertEqual("receiver_unclean_or_stopped", result.reason)

    def test_crash_is_marked_unclean_on_next_start(self):
        self.add_receiver("instance-a", "boot-a", BASE)
        self.add_heartbeat(BASE, 0)
        self.add_heartbeat(BASE + timedelta(seconds=30), 1)
        self.add_receiver("instance-b", "boot-b", BASE + timedelta(seconds=70))
        self.add_heartbeat(BASE + timedelta(seconds=70), 2, instance="instance-b", boot="boot-b")
        result = self.store.coverage(
            device_id="cam02", start_at=BASE, end_at=BASE + timedelta(seconds=70)
        )
        self.assertEqual("unavailable", result.status)
        self.assertEqual("receiver_unclean_or_stopped", result.reason)

    def test_persistence_failure_is_unavailable(self):
        self.add_receiver()
        self.add_heartbeat(BASE, 0)
        self.add_heartbeat(BASE + timedelta(seconds=30), 1)
        self.store.record_persistence_failure(
            device_id="cam02",
            receiver_instance_id="instance-a",
            boot_id="boot-a",
            source_event_id="failure-1",
            observed_at=BASE + timedelta(seconds=45),
            failure_class="db_timeout",
        )
        self.add_heartbeat(BASE + timedelta(seconds=60), 2)
        result = self.store.coverage(
            device_id="cam02", start_at=BASE, end_at=BASE + timedelta(seconds=60)
        )
        self.assertEqual("unavailable", result.status)
        self.assertEqual("persistence_failure", result.reason)

    def test_partial_recognition_does_not_heal_heartbeat_gap(self):
        self.add_receiver()
        self.add_heartbeat(BASE, 0)
        self.store.record(
            device_id="cam02",
            observation_type="persistence_success",
            observed_at=BASE + timedelta(seconds=60),
            device_event_at=BASE + timedelta(seconds=60),
            receiver_instance_id="instance-a",
            boot_id="boot-a",
            source_event_id="recognition-1",
            canonical_event_id="canonical-1",
            outcome="committed",
            observation_key="recognition-persistence:recognition-1",
        )
        result = self.store.coverage(
            device_id="cam02", start_at=BASE, end_at=BASE + timedelta(seconds=60)
        )
        self.assertEqual("unavailable", result.status)

    def test_zero_recognition_records_can_still_have_complete_coverage(self):
        start, end = self.healthy_window()
        result = self.store.coverage(device_id="cam02", start_at=start, end_at=end)
        self.assertEqual("complete", result.status)

    def test_repeated_heartbeat_is_idempotent(self):
        self.add_receiver()
        first = self.add_heartbeat(BASE, 0)
        second = self.add_heartbeat(BASE, 0)
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(first[1].id, second[1].id)
        session = self.sessions()
        try:
            self.assertEqual(3, session.query(PushCoverageObservation).count())
        finally:
            session.close()

    def test_observations_reject_update_and_delete(self):
        self.add_receiver()
        session = self.sessions()
        try:
            row = session.query(PushCoverageObservation).one()
            row.outcome = "rewritten"
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            row = session.query(PushCoverageObservation).one()
            session.delete(row)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            self.assertEqual(1, session.query(PushCoverageObservation).count())
        finally:
            session.close()

    def test_policy_version_changes_result_without_mutating_observations(self):
        start, end = self.healthy_window()
        before = self.store.coverage(device_id="cam02", start_at=start, end_at=end)
        policy = PushCoveragePolicy(policy_version="p6s_push_coverage_v2")
        other = PushCoverageStore(self.sessions, policy=policy)
        after = other.coverage(device_id="cam02", start_at=start, end_at=end)
        self.assertEqual("complete", before.status)
        self.assertEqual("unavailable", after.status)
        self.assertNotEqual(before.material_hash, after.material_hash)
        session = self.sessions()
        try:
            self.assertEqual(9, session.query(PushCoverageObservation).count())
        finally:
            session.close()

    def test_policy_version_can_recompute_same_facts(self):
        policy = PushCoveragePolicy(policy_version="p6s_push_coverage_v2")
        store = PushCoverageStore(self.sessions, policy=policy)
        store.start_receiver(device_id="cam02", receiver_instance_id="instance-v2", boot_id="boot-v2", observed_at=BASE)
        for sequence, offset in enumerate((0, 30, 60, 90)):
            prefix = f"v2-{sequence}"
            store.record(
                device_id="cam02", observation_type="heartbeat_accepted",
                observed_at=BASE + timedelta(seconds=offset),
                device_event_at=BASE + timedelta(seconds=offset),
                receiver_instance_id="instance-v2", boot_id="boot-v2",
                source_event_id=prefix, outcome="accepted",
                observation_key=f"heartbeat:{prefix}", policy_version=policy.policy_version,
            )
            store.record(
                device_id="cam02", observation_type="persistence_success",
                observed_at=BASE + timedelta(seconds=offset),
                device_event_at=BASE + timedelta(seconds=offset),
                receiver_instance_id="instance-v2", boot_id="boot-v2",
                source_event_id=prefix, outcome="committed",
                observation_key=f"persistence:{prefix}", policy_version=policy.policy_version,
            )
        result = store.coverage(device_id="cam02", start_at=BASE, end_at=BASE + timedelta(seconds=90))
        self.assertEqual("complete", result.status)

    def test_material_hash_excludes_random_database_and_process_ids(self):
        start, end = self.healthy_window()
        first = self.store.coverage(device_id="cam02", start_at=start, end_at=end)

        other_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(other_engine.dispose)
        evolve_schema(other_engine)
        other = PushCoverageStore(sessionmaker(bind=other_engine))
        other.start_receiver(
            device_id="cam02",
            receiver_instance_id="different-process-instance",
            boot_id="different-boot",
            observed_at=BASE,
        )
        for sequence, offset in enumerate((0, 30, 60, 90)):
            other.record_heartbeat(
                device_id="cam02",
                receiver_instance_id="different-process-instance",
                boot_id="different-boot",
                source_event_id=f"hb-{sequence}",
                observed_at=BASE + timedelta(seconds=offset),
                device_event_at=BASE + timedelta(seconds=offset),
            )
        second = other.coverage(device_id="cam02", start_at=start, end_at=end)
        self.assertNotEqual(
            first.supporting_observation_ids,
            second.supporting_observation_ids,
        )
        self.assertEqual(first.material_hash, second.material_hash)


class PushCoverageReceiverIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.coverage = PushCoverageStore(self.sessions)
        self.health = DeviceHealthStore(self.sessions)
        app = FastAPI()
        app.include_router(
            create_device_event_router(
                None,
                self.health,
                {"P6S-001": "cam02"},
                coverage_store=self.coverage,
                receiver_instance_id="receiver-a",
                boot_id="boot-a",
                clock=lambda: datetime(2026, 8, 18, 11, 0, 0, tzinfo=timezone.utc),
            )
        )
        self.client = TestClient(app)
        self.addCleanup(self.engine.dispose)

    def test_heartbeat_writes_historical_observations_after_health_commit(self):
        response = self.client.post(
            "/api/v1/event/pull",
            json={
                "operator": "heartbeat",
                "deviceInfo": {"serialNumber": "P6S-001"},
                "info": {"eventId": 1, "time": "2026-08-18T19:00:00+08:00"},
            },
        )
        self.assertEqual(200, response.status_code)
        session = self.sessions()
        try:
            rows = session.query(PushCoverageObservation).all()
            self.assertCountEqual(
                ["heartbeat_accepted", "persistence_success"],
                [row.observation_type for row in rows],
            )
        finally:
            session.close()


class PushCoverageLifespanTests(unittest.TestCase):
    def test_lifespan_writes_receiver_start_and_graceful_stop(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine)
        coverage = PushCoverageStore(sessions)
        services = member_api.DeviceEventServices(
            intake=DeviceRecognitionIntake(sessions),
            health_store=DeviceHealthStore(sessions),
            coverage_store=coverage,
        )
        member_api.reset_device_event_services()
        with patch.object(member_api.settings, "CAMERA_FACE_DEVICE_ID", "cam02"), patch(
            "agent.api.member_api._build_device_event_services", return_value=services
        ), patch("agent.api.member_api.get_service"):
            with TestClient(member_api.app):
                session = sessions()
                try:
                    self.assertEqual(
                        ["receiver_started"],
                        [row.observation_type for row in session.query(
                            PushCoverageObservation
                        ).all()],
                    )
                finally:
                    session.close()
        session = sessions()
        try:
            self.assertEqual(
                ["receiver_started", "receiver_graceful_stopped"],
                [row.observation_type for row in session.query(
                    PushCoverageObservation
                ).order_by(PushCoverageObservation.observed_at).all()],
            )
        finally:
            session.close()
            member_api.reset_device_event_services()


if __name__ == "__main__":
    unittest.main()
