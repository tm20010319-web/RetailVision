from test._test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import tempfile
import unittest
import json
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.schema_evolution import evolve_schema
from agent.device_capture_store import DeviceCaptureStore
from agent.device_recognition_intake import (
    DeviceRecognitionEvent,
    DeviceRecognitionIntake,
    FaceCapture,
    IntakeUnavailable,
    InvalidDeviceRecognitionEvent,
)
from agent.models import (
    DeviceMemberMapping,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
)


NOW = datetime(2026, 8, 3, 12, 0, 0)


def jpeg_bytes(value=0):
    image = np.full((4, 4, 3), value, dtype=np.uint8)
    encoded, buffer = cv2.imencode(".jpg", image)
    if not encoded:
        raise RuntimeError("failed to encode test JPEG")
    return buffer.tobytes()


class DeviceRecognitionIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        db = self.sessions()
        db.add(Member(id="member-1", name="Member", is_active=True))
        db.add(
            DeviceMemberMapping(
                id="device-1:member-1",
                device_id="device-1",
                member_id="member-1",
                group_id=7,
                group_uuid="group-7",
                person_id=42,
                unique_id="person-1",
                face_uuid="face-1",
                sync_status="synced",
                is_enabled=True,
            )
        )
        db.commit()
        db.close()
        self.capture_store = DeviceCaptureStore(
            Path(self.tempdir.name) / "captures",
            max_bytes=4096,
            max_pixels=100,
        )
        self.intake = DeviceRecognitionIntake(
            self.sessions,
            capture_store=self.capture_store,
        )

    def event(self, **overrides):
        values = {
            "device_id": "device-1",
            "source": "push",
            "source_event_id": "event-1",
            "occurred_at": NOW,
            "unique_id": "person-1",
            "group_id": 7,
            "person_id": 42,
            "similarity": 0.91,
            "rule_id": 1,
        }
        values.update(overrides)
        return DeviceRecognitionEvent(**values)

    def test_receive_stops_at_durable_recognition_inbox(self):
        receipt = self.intake.receive(
            self.event(),
            capture=FaceCapture(jpeg_bytes()),
        )

        self.assertEqual("received", receipt.status)
        self.assertEqual("member-1", receipt.member_id)
        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, receipt.canonical_event_id)
            self.assertEqual("received", inbox.status)
            self.assertTrue(Path(inbox.capture_path).is_file())
            self.assertEqual(0, db.query(MemberEntryEvent).count())
        finally:
            db.close()

    def test_receive_persists_restricted_device_metadata(self):
        receipt = self.intake.receive(self.event(device_metadata={
            "device_model": {"raw_value": "P6S-X", "normalized_value": "P6S-X"},
            "firmware_version": {"raw_value": "3.2.1", "normalized_value": "3.2.1"},
        }))

        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, receipt.canonical_event_id)
            self.assertEqual("P6S-X", inbox.device_metadata["device_model"]["normalized_value"])
        finally:
            db.close()

    def test_receive_persists_receiver_arrival_as_diagnostic_only(self):
        arrived = datetime(2026, 8, 3, 12, 0, 18, 700000)
        receipt = self.intake.receive(self.event(receiver_arrived_at=arrived))

        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, receipt.canonical_event_id)
            self.assertEqual(arrived, inbox.receiver_arrived_at)
            self.assertEqual(NOW, inbox.occurred_at)
        finally:
            db.close()

    def test_intake_rebuilds_face_spatial_evidence_from_safe_whitelist(self):
        receipt = self.intake.receive(self.event(face_spatial_evidence={
            "coordinate_space": "normalized_full_frame",
            "raw_bbox": [900.0, 412.0, 1028.0, 540.0],
            "normalized_bbox": [0.46875, 0.378676, 0.535417, 0.496324],
            "source_frame_size": [1920, 1088],
            "quality_status": "parsed",
            "event_time": NOW.isoformat(),
            "faceFeature": "SENTINEL_FACE_FEATURE",
            "picture": "data:image/jpeg;base64,SENTINEL_IMAGE",
            "personInfo": {"name": "Sensitive Name"},
        }))

        db = self.sessions()
        try:
            evidence = db.get(
                DeviceRecognitionInbox,
                receipt.canonical_event_id,
            ).face_spatial_evidence
        finally:
            db.close()

        serialized = json.dumps(evidence, sort_keys=True)
        self.assertIn("normalized_full_frame", serialized)
        self.assertNotIn("SENTINEL_FACE_FEATURE", serialized)
        self.assertNotIn("SENTINEL_IMAGE", serialized)
        self.assertNotIn("Sensitive Name", serialized)

    def test_intake_rejects_non_normalized_face_coordinate_space(self):
        receipt = self.intake.receive(self.event(face_spatial_evidence={
            "coordinate_space": "pixel_xyxy",
            "normalized_bbox": [0.1, 0.1, 0.2, 0.2],
            "quality_status": "parsed",
            "event_time": NOW.isoformat(),
        }))

        db = self.sessions()
        try:
            evidence = db.get(
                DeviceRecognitionInbox,
                receipt.canonical_event_id,
            ).face_spatial_evidence
        finally:
            db.close()

        self.assertIsNone(evidence)

    def test_duplicate_push_does_not_replace_capture(self):
        first = self.intake.receive(self.event(), capture=FaceCapture(jpeg_bytes()))
        second = self.intake.receive(
            self.event(),
            capture=FaceCapture(jpeg_bytes(value=255)),
        )

        self.assertEqual("received", first.status)
        self.assertEqual("duplicate", second.status)
        captures = list((Path(self.tempdir.name) / "captures").glob("*.jpg"))
        self.assertEqual(1, len(captures))
        self.assertEqual(jpeg_bytes(), captures[0].read_bytes())

    def test_push_adopts_capture_for_existing_poll_receipt(self):
        poll = self.intake.receive(
            self.event(source="poll", source_event_id="log-1")
        )
        push = self.intake.receive(
            self.event(source="push", source_event_id="push-1"),
            capture=FaceCapture(jpeg_bytes()),
        )

        self.assertEqual("received", poll.status)
        self.assertEqual("duplicate", push.status)
        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, poll.canonical_event_id)
            self.assertEqual("poll", inbox.source)
            self.assertTrue(Path(inbox.capture_path).is_file())
            self.assertEqual(0, db.query(MemberEntryEvent).count())
        finally:
            db.close()

    def test_ineligible_event_is_durable_without_capture(self):
        receipt = self.intake.receive(
            self.event(similarity=0.5),
            capture=FaceCapture(jpeg_bytes()),
        )

        self.assertEqual("ignored", receipt.status)
        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, receipt.canonical_event_id)
            self.assertEqual("ignored", inbox.status)
            self.assertIsNone(inbox.capture_path)
        finally:
            db.close()
        self.assertEqual([], list((Path(self.tempdir.name) / "captures").glob("*.jpg")))

    def test_waiting_mapping_recovers_without_creating_member_arrival(self):
        receipt = self.intake.receive(
            self.event(unique_id="later", person_id=99),
            capture=FaceCapture(jpeg_bytes()),
        )
        self.assertEqual("waiting_mapping", receipt.status)
        db = self.sessions()
        db.add(
            DeviceMemberMapping(
                id="device-1:later",
                device_id="device-1",
                member_id="member-1",
                group_id=7,
                group_uuid="group-7",
                person_id=99,
                unique_id="later",
                face_uuid="face-later",
                sync_status="synced",
                is_enabled=True,
            )
        )
        db.commit()
        db.close()

        recovered, expired = self.intake._reconcile_waiting(NOW + timedelta(seconds=1))

        self.assertEqual((1, 0), (recovered, expired))
        db = self.sessions()
        try:
            inbox = db.get(DeviceRecognitionInbox, receipt.canonical_event_id)
            self.assertEqual("received", inbox.status)
            self.assertEqual("member-1", inbox.member_id)
            self.assertEqual(0, db.query(MemberEntryEvent).count())
        finally:
            db.close()

    def test_rejects_invalid_normalized_event(self):
        with self.assertRaises(InvalidDeviceRecognitionEvent):
            self.intake.receive(self.event(unique_id="", group_id=None, person_id=None))

    def test_database_failure_releases_the_attempt_capture(self):
        DeviceRecognitionInbox.__table__.drop(self.engine)

        with self.assertRaises(IntakeUnavailable):
            self.intake.receive(
                self.event(),
                capture=FaceCapture(jpeg_bytes()),
            )

        self.assertEqual(
            [],
            list((Path(self.tempdir.name) / "captures").glob("*.jpg")),
        )


if __name__ == "__main__":
    unittest.main()
