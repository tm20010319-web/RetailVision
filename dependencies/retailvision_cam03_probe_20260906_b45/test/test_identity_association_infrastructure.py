from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.identity_runtime_lease import (
    IdentityAssociationRuntimeLeaseService,
    RuntimeLeaseUnavailable,
)
from agent.store_mapping_recovery import StoreMappingRecoveryWorker
from agent.models import (
    CameraStoreRegistration,
    DeviceCameraRegistration,
    DeviceStoreRegistration,
)
from agent.schema_evolution import evolve_schema
from agent.store_resolution import StoreResolutionService
from agent.device_camera_registration import DeviceCameraRegistrationService


class IdentityAssociationInfrastructureTests(unittest.TestCase):
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

    def tearDown(self):
        self.engine.dispose()

    def _registrations(self, *, device_store="store-1", camera_store="store-1"):
        db = self.sessions()
        db.add_all((
            DeviceStoreRegistration(
                device_id="device-1",
                store_id=device_store,
                effective_from=self.now - timedelta(hours=1),
                status="active",
            ),
            CameraStoreRegistration(
                camera_id="camera-1",
                store_id=camera_store,
                effective_from=self.now - timedelta(hours=1),
                status="active",
            ),
            DeviceCameraRegistration(
                device_id="device-1",
                camera_id="camera-1",
                effective_from=self.now - timedelta(hours=1),
                status="active",
            ),
        ))
        db.commit()
        db.close()

    def test_store_resolution_uses_unique_event_time_sources(self):
        self._registrations()

        result = StoreResolutionService(self.sessions).resolve(
            device_id="device-1",
            camera_id="camera-1",
            occurred_at=self.now,
        )

        self.assertEqual("resolved", result.status)
        self.assertEqual("store-1", result.store_id)
        self.assertEqual("camera-1", result.camera_id)

    def test_store_resolution_reports_conflicting_trusted_sources(self):
        self._registrations(camera_store="store-2")

        result = StoreResolutionService(self.sessions).resolve(
            device_id="device-1",
            camera_id="camera-1",
            occurred_at=self.now,
        )

        self.assertEqual("conflict", result.status)
        self.assertIsNone(result.store_id)
        self.assertEqual("store_mapping_conflict", result.reason)

    def test_runtime_lease_fences_previous_owner_after_takeover(self):
        current = [self.now]
        service = IdentityAssociationRuntimeLeaseService(
            self.sessions,
            clock=lambda: current[0],
            lease_duration_seconds=30,
        )
        first = service.acquire(store_id="store-1", mode="new", owner_id="runtime-1")
        with self.assertRaises(RuntimeLeaseUnavailable):
            service.acquire(store_id="store-1", mode="new", owner_id="runtime-2")

        current[0] += timedelta(seconds=31)
        second = service.acquire(store_id="store-1", mode="new", owner_id="runtime-2")

        self.assertGreater(second.lease_generation, first.lease_generation)
        with self.assertRaises(RuntimeLeaseUnavailable):
            service.validate(first)
        service.validate(second)

    def test_store_mapping_recovery_returns_resolved_event_to_inbox_delivery(self):
        self._registrations()
        from agent.models import DeviceRecognitionInbox, Member

        db = self.sessions()
        db.add(Member(id="member-1", name="Member One"))
        db.commit()
        db.add(DeviceRecognitionInbox(
            canonical_event_id="recognition-1",
            device_id="device-1",
            source="push",
            source_event_id="source-1",
            occurred_at=self.now,
            member_id="member-1",
            unique_id="member-1",
            similarity=0.9,
            status="waiting_store_mapping",
            recognition_status="accepted",
            store_resolution_status="missing",
            track_association_status="pending",
            store_mapping_wait_deadline_at=self.now + timedelta(minutes=10),
        ))
        db.commit()
        db.close()

        recovered = StoreMappingRecoveryWorker(
            self.sessions,
            StoreResolutionService(self.sessions),
            clock=lambda: self.now,
        ).run_once()

        db = self.sessions()
        try:
            row = db.get(DeviceRecognitionInbox, "recognition-1")
            self.assertEqual(1, recovered)
            self.assertEqual("received", row.status)
            self.assertEqual("resolved", row.store_resolution_status)
            self.assertEqual("store-1", row.store_id)
            self.assertIsNone(row.matched_visit_id)
        finally:
            db.close()

    def test_device_camera_switch_preserves_half_open_history(self):
        service = DeviceCameraRegistrationService(self.sessions)
        service.register(
            device_id="device-switch", camera_id="camera-old",
            effective_from=self.now - timedelta(hours=1),
        )
        service.switch(
            device_id="device-switch", camera_id="camera-new",
            effective_from=self.now,
        )
        resolution = StoreResolutionService(self.sessions)

        before = resolution.resolve(
            device_id="device-switch", occurred_at=self.now - timedelta(microseconds=1)
        )
        at_boundary = resolution.resolve(
            device_id="device-switch", occurred_at=self.now
        )

        self.assertEqual("camera-old", before.camera_id)
        self.assertEqual("camera-new", at_boundary.camera_id)

    def test_device_camera_registration_rejects_overlapping_interval(self):
        service = DeviceCameraRegistrationService(self.sessions)
        service.register(
            device_id="device-overlap", camera_id="camera-1",
            effective_from=self.now - timedelta(hours=1), effective_to=self.now + timedelta(hours=1),
            status="closed",
        )
        with self.assertRaisesRegex(ValueError, "overlap"):
            service.register(
                device_id="device-overlap", camera_id="camera-2",
                effective_from=self.now,
            )

    def test_historical_overlapping_store_rows_are_conflict(self):
        db = self.sessions()
        db.add_all((
            DeviceStoreRegistration(device_id="device-conflict", store_id="store-1", effective_from=self.now - timedelta(hours=1), status="active"),
            DeviceStoreRegistration(device_id="device-conflict", store_id="store-2", effective_from=self.now - timedelta(hours=1), status="active"),
        ))
        db.commit()
        db.close()
        result = StoreResolutionService(self.sessions).resolve(
            device_id="device-conflict", occurred_at=self.now, persisted_store_id="store-1"
        )
        self.assertEqual("conflict", result.status)
        self.assertEqual("device_store_mapping_conflict", result.reason)


if __name__ == "__main__":
    unittest.main()
