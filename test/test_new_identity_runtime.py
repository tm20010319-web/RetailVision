from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.models import DeviceRecognitionInbox, Member, MemberArrival, Visit
from agent.schema_evolution import evolve_schema
from pipelines.device_identity_runtime import DeviceIdentityRuntime, create_device_identity_runtime
from agent.recognition_association_worker import TrackAssociationCandidate


class _Client:
    device_id = "device-1"


class _Repository:
    pass


class _Detector:
    def initialize(self):
        pass

    def process_frame(self, _frame):
        return []


class NewIdentityRuntimeTests(unittest.TestCase):
    @patch("pipelines.device_identity_runtime.IdentityAssociationRuntimeLeaseService")
    def test_legacy_store_context_acquires_and_releases_runtime_lease(
        self, lease_service_type
    ):
        lease = SimpleNamespace(mode="legacy")
        lease_service_type.return_value.acquire.return_value = lease
        runtime = create_device_identity_runtime(
            client=_Client(),
            repository=_Repository(),
            session_factory=lambda: None,
            detector=_Detector(),
            identity_association_mode="legacy",
            association_store_id="store-1",
            association_runtime_owner_id="legacy-runtime-1",
        )

        lease_service_type.return_value.acquire.assert_called_once_with(
            store_id="store-1",
            mode="legacy",
            owner_id="legacy-runtime-1",
        )
        runtime.stop()
        lease_service_type.return_value.release.assert_called_once_with(lease)

    def test_owned_lease_loss_fails_closed_without_store_runtime_callback(self):
        class Pipeline:
            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True

        class LeaseService:
            def heartbeat(self, _lease):
                raise RuntimeError("database unavailable")

            def release(self, _lease):
                return None

        pipeline = Pipeline()
        runtime = DeviceIdentityRuntime(
            pipeline=pipeline,
            coordinator=None,
            association_lease_service=LeaseService(),
            association_lease=SimpleNamespace(),
        )

        runtime._run_association_workers()

        self.assertTrue(pipeline.stopped)
        self.assertFalse(runtime.start_background_services())

    def test_new_rejects_legacy_business_runtime(self):
        with self.assertRaisesRegex(ValueError, "legacy member business runtime"):
            create_device_identity_runtime(
                client=_Client(),
                repository=_Repository(),
                session_factory=lambda: None,
                detector=_Detector(),
                identity_association_mode="new",
                association_store_id="store-1",
                association_runtime_owner_id="runtime-1",
                member_business_runtime=object(),
            )

    def test_new_production_requires_store_runtime_dependencies(self):
        with self.assertRaisesRegex(ValueError, "StoreRuntime"):
            create_device_identity_runtime(
                client=_Client(),
                repository=_Repository(),
                session_factory=lambda: None,
                detector=_Detector(),
                identity_association_mode="new",
                association_store_id="store-1",
                association_runtime_owner_id="runtime-1",
            )

    def test_new_production_rejects_partially_injected_store_runtime(self):
        with self.assertRaisesRegex(ValueError, "StoreResolutionService.*Resolver.*Projection"):
            create_device_identity_runtime(
                client=_Client(),
                repository=_Repository(),
                session_factory=lambda: None,
                detector=_Detector(),
                identity_association_mode="new",
                association_store_id="store-1",
                association_runtime_owner_id="runtime-1",
                association_lease_service=object(),
                association_lease=SimpleNamespace(),
                track_candidate_provider=lambda _row: None,
            )

    def test_legacy_rejects_new_association_components(self):
        with self.assertRaisesRegex(ValueError, "new association workers"):
            create_device_identity_runtime(
                client=_Client(),
                repository=_Repository(),
                session_factory=lambda: None,
                detector=_Detector(),
                identity_association_mode="legacy",
                resolver=object(),
            )

    def test_new_runtime_runs_pending_event_through_resolver(self):
        now = datetime(2026, 8, 5, 6)
        engine = configure_sqlite_foreign_keys(create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool))
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        db = sessions()
        db.add(Member(id="member-1", name="Member One"))
        db.flush()
        db.add(DeviceRecognitionInbox(
            canonical_event_id="event-1", device_id="device-1", source="push", source_event_id="event-1",
            occurred_at=now, store_id="store-1", member_id="member-1", unique_id="member-1", similarity=0.9,
            status="processed", recognition_status="accepted", store_resolution_status="resolved",
            association_status="pending",
        ))
        db.commit()
        db.close()

        runtime = create_device_identity_runtime(
            client=_Client(), repository=_Repository(), session_factory=sessions, detector=_Detector(),
            identity_association_mode="new", association_store_id="store-1",
            association_runtime_owner_id="runtime-1", clock=lambda: now, health_clock=lambda: now,
            component_debug=True,
            track_candidate_provider=lambda _row: TrackAssociationCandidate(
                status="no_track_match", method="test_no_track"
            ),
        )
        runtime._run_association_workers()

        db = sessions()
        try:
            self.assertIsNone(runtime.coordinator)
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(1, db.query(MemberArrival).count())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
