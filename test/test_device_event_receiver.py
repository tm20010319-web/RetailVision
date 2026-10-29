from test._test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import base64
import asyncio
import json
import shutil
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.schema_evolution import evolve_schema
from agent.device_capture_store import DeviceCaptureStore
from agent.device_recognition_intake import (
    DeviceRecognitionEvent,
    DeviceRecognitionIntake,
)
from agent.device_event_receiver import create_device_event_router
from agent.device_health import DeviceHealthStore
from agent.models import (
    DeviceHealthState,
    DeviceMemberMapping,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
)


OCCURRED_AT = datetime(2026, 7, 20, 5, 24, 59)


def jpeg_bytes(width=4, height=4, value=0):
    image = np.full((height, width, 3), value, dtype=np.uint8)
    encoded, buffer = cv2.imencode(".jpg", image)
    if not encoded:
        raise RuntimeError("failed to encode test JPEG")
    return buffer.tobytes()


JPEG_BYTES = jpeg_bytes()
SECOND_JPEG_BYTES = jpeg_bytes(value=255)
CAPTURE_DATA_URI = (
    "data:image/jpeg;base64," + base64.b64encode(JPEG_BYTES).decode("ascii")
)
HEARTBEAT = {
    "operator": "heartbeat",
    "deviceInfo": {"serialNumber": "P6S-001"},
    "info": {"eventId": 7},
}
FACE_RECO = {
    "operator": "FaceReco",
    "deviceInfo": {
        "serialNumber": "P6S-001",
        "ip": "192.0.2.10",
        "credential": "SENTINEL_DEVICE_CREDENTIAL",
    },
    "info": {
        "eventId": 8,
        "eventTime": "2026-07-20T13:24:59+08:00",
        "matchNumber": 1,
        "personInfo": {
            "uniqueId": "uid-001",
            "personId": "42",
            "groupId": "7",
            "match": 88.083099,
            "faceFeature": "SENTINEL_FACE_FEATURE",
            "name": "Sensitive Name",
        },
        "CaptureImage": {
            "pictureMd5": "abc123",
            "picture": CAPTURE_DATA_URI,
        },
    },
}

FACE_RECO_WITH_SPATIAL_EVIDENCE = {
    **FACE_RECO,
    "info": {
        **FACE_RECO["info"],
        "FaceInfo": {
            "leftTop": {"x": 900, "y": 412},
            "rightBottom": {"x": 1028, "y": 540},
            "BackgroundImage": {"width": 1920, "height": 1088},
            "quality": 86.5,
            "yaw": -2.0,
            "pitch": 1.5,
            "roll": 0.25,
        },
    },
}

FACE_RECO_WITH_VENDOR_SPATIAL_EVIDENCE = {
    **FACE_RECO,
    "info": {
        **FACE_RECO["info"],
        "faceFeature": {
            "age": 23,
            "sex": "SENTINEL_FACE_ATTRIBUTE",
            "quality": "70",
            "yaw": "7.055371",
            "roll": "11.756012",
            "pitch": "9.877519",
        },
        "CaptureImage": {
            **FACE_RECO["info"]["CaptureImage"],
            "leftTop": "[900,412]",
            "rightBottom": "[1028,540]",
        },
        "BackgroundImage": {
            "width": 1920,
            "height": 1088,
            "picture": "data:image/jpeg;base64,SENTINEL_BACKGROUND_IMAGE",
        },
    },
}


FACE_RECO_WITH_BACKGROUND_CAPTURE = {
    **FACE_RECO_WITH_VENDOR_SPATIAL_EVIDENCE,
    "info": {
        **FACE_RECO_WITH_VENDOR_SPATIAL_EVIDENCE["info"],
        "CaptureImage": {
            **FACE_RECO_WITH_VENDOR_SPATIAL_EVIDENCE["info"]["CaptureImage"],
            "leftTop": "[1,1]",
            "rightBottom": "[3,3]",
        },
        "BackgroundImage": {
            "width": 4,
            "height": 4,
            "picture": CAPTURE_DATA_URI,
        },
    },
}


class DeviceEventReceiverTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.capture_root = Path.cwd() / ".task4-tests" / uuid4().hex
        self.capture_root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.capture_root, True)
        self._seed_mapping()

        self.health_store = DeviceHealthStore(self.session_factory)
        self.capture_store = DeviceCaptureStore(self.capture_root, max_bytes=1024)
        self.intake = DeviceRecognitionIntake(
            self.session_factory,
            capture_store=self.capture_store,
            similarity_threshold=0.75,
        )
        app = FastAPI()
        app.include_router(
            create_device_event_router(
                self.intake,
                self.health_store,
                {"P6S-001": "entrance-1"},
            )
        )
        self.client = TestClient(app)

    def _seed_mapping(self):
        db = self.session_factory()
        try:
            db.add(Member(id="member-42", name="Member 42", is_active=True))
            db.add(
                DeviceMemberMapping(
                    id="entrance-1:member-42",
                    device_id="entrance-1",
                    member_id="member-42",
                    group_id=7,
                    group_uuid="group-7",
                    person_id=42,
                    unique_id="uid-001",
                    face_uuid="face-42",
                    sync_status="synced",
                    is_enabled=True,
                )
            )
            db.commit()
        finally:
            db.close()

    def _count(self, model):
        db = self.session_factory()
        try:
            return db.query(model).count()
        finally:
            db.close()

    def test_heartbeat_ack_marks_push_healthy(self):
        response = self.client.post("/api/v1/event/pull", json=HEARTBEAT)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["operator"], "heartbeat-Ack")
        state = self.health_store.get("entrance-1")
        self.assertIsNotNone(state)
        self.assertTrue(state.push_healthy)
        self.assertIsNotNone(state.last_heartbeat_at)

    def test_heartbeat_uses_utc8_for_ack_and_utc_naive_for_health(self):
        utc_now = datetime(2026, 7, 20, 5, 25, 0, tzinfo=timezone.utc)
        app = FastAPI()
        app.include_router(
            create_device_event_router(
                self.intake,
                self.health_store,
                {"P6S-001": "entrance-1"},
                clock=lambda: utc_now,
            )
        )

        response = TestClient(app).post("/api/v1/event/pull", json=HEARTBEAT)

        self.assertEqual(response.json()["info"]["time"], "20260720T132500+08")
        self.assertEqual(
            self.health_store.get("entrance-1").last_heartbeat_at,
            datetime(2026, 7, 20, 5, 25, 0),
        )

    def test_mapped_heartbeat_persistence_failure_returns_503_without_success_ack(self):
        class FailingHealthStore:
            def mark_heartbeat(self, device_id, occurred_at):
                raise RuntimeError("SENTINEL_HEALTH_FAILURE")

        app = FastAPI()
        app.include_router(
            create_device_event_router(
                self.intake,
                FailingHealthStore(),
                {"P6S-001": "entrance-1"},
            )
        )

        with self.assertLogs("agent.device_event_receiver", level="INFO") as logs:
            response = TestClient(app).post("/api/v1/event/pull", json=HEARTBEAT)

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("operator", response.json())
        self.assertNotIn("SENTINEL_HEALTH_FAILURE", "\n".join(logs.output))

    def test_blocking_heartbeat_work_runs_outside_event_loop_thread(self):
        observations = []

        class ObservedHealthStore:
            def mark_heartbeat(self, device_id, occurred_at):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    observations.append((threading.get_ident(), False))
                else:
                    observations.append((threading.get_ident(), True))

        app = FastAPI()
        app.include_router(
            create_device_event_router(
                self.intake,
                ObservedHealthStore(),
                {"P6S-001": "entrance-1"},
            )
        )

        response = TestClient(app).post("/api/v1/event/pull", json=HEARTBEAT)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(observations), 1)
        self.assertFalse(observations[0][1])

    def test_face_reco_is_persisted_before_ack(self):
        response = self.client.post("/api/v1/event/pull", json=FACE_RECO)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["operator"], "FaceReco-Ack")
        self.assertEqual(self._count(DeviceRecognitionInbox), 1)
        self.assertEqual(self._count(MemberEntryEvent), 0)
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual(inbox.device_id, "entrance-1")
            self.assertEqual(inbox.source_event_id, "8:2026-07-20T05:24:59")
            self.assertEqual(inbox.occurred_at, OCCURRED_AT)
            self.assertIsNotNone(inbox.receiver_arrived_at)
            self.assertIsNone(inbox.future_skew_seconds)
            self.assertEqual(inbox.unique_id, "uid-001")
            self.assertEqual(inbox.group_id, 7)
            self.assertEqual(inbox.person_id, 42)
            self.assertAlmostEqual(inbox.similarity, 0.88083099)
            self.assertEqual(Path(inbox.capture_path).read_bytes(), JPEG_BYTES)
        finally:
            db.close()

    def test_face_reco_maps_by_unique_id_when_push_identifiers_are_non_numeric(self):
        payload = {
            **FACE_RECO,
            "info": {
                **FACE_RECO["info"],
                "eventId": 10,
                "personInfo": {
                    **FACE_RECO["info"]["personInfo"],
                    "groupId": "a9668cb02b4f4418a441100881ecf496",
                    "personId": "device-person-42",
                },
            },
        }

        response = self.client.post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["operator"], "FaceReco-Ack")
        self.assertEqual(self._count(DeviceRecognitionInbox), 1)
        self.assertEqual(self._count(MemberEntryEvent), 0)
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual(inbox.member_id, "member-42")
            self.assertIsNone(inbox.group_id)
            self.assertIsNone(inbox.person_id)
        finally:
            db.close()

    def test_duplicate_face_reco_returns_ack_without_duplicate_event(self):
        first = self.client.post("/api/v1/event/pull", json=FACE_RECO)
        second = self.client.post("/api/v1/event/pull", json=FACE_RECO)

        self.assertEqual(first.json()["operator"], "FaceReco-Ack")
        self.assertEqual(second.json()["operator"], "FaceReco-Ack")
        self.assertEqual(self._count(DeviceRecognitionInbox), 1)
        self.assertEqual(self._count(MemberEntryEvent), 0)

    def test_reused_push_event_id_at_new_time_creates_new_event(self):
        mutated = {
            **FACE_RECO,
            "info": {
                **FACE_RECO["info"],
                "eventTime": "2026-07-20T13:25:30+08:00",
                "personInfo": {
                    **FACE_RECO["info"]["personInfo"],
                    "match": 91.5,
                },
            },
        }

        first = self.client.post("/api/v1/event/pull", json=FACE_RECO)
        second = self.client.post("/api/v1/event/pull", json=mutated)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self._count(DeviceRecognitionInbox), 2)
        self.assertEqual(self._count(MemberEntryEvent), 0)

    def test_mutated_retry_with_same_event_id_and_time_creates_one_event(self):
        mutated = {
            **FACE_RECO,
            "info": {
                **FACE_RECO["info"],
                "personInfo": {
                    **FACE_RECO["info"]["personInfo"],
                    "match": 91.5,
                },
            },
        }

        first = self.client.post("/api/v1/event/pull", json=FACE_RECO)
        second = self.client.post("/api/v1/event/pull", json=mutated)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self._count(DeviceRecognitionInbox), 1)
        self.assertEqual(self._count(MemberEntryEvent), 0)

    def test_duplicate_retry_preserves_preexisting_capture_bytes(self):
        first = self.client.post("/api/v1/event/pull", json=FACE_RECO)
        replacement_uri = "data:image/jpeg;base64," + base64.b64encode(
            SECOND_JPEG_BYTES
        ).decode("ascii")
        retry = {
            **FACE_RECO,
            "info": {
                **FACE_RECO["info"],
                "CaptureImage": {
                    **FACE_RECO["info"]["CaptureImage"],
                    "picture": replacement_uri,
                },
            },
        }

        second = self.client.post("/api/v1/event/pull", json=retry)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        captures = list(self.capture_root.glob("*.jpg"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), JPEG_BYTES)

    def test_duplicate_result_removes_only_capture_created_by_retry(self):
        without_capture = {
            **FACE_RECO,
            "info": {**FACE_RECO["info"], "CaptureImage": {}},
        }
        self.client.post("/api/v1/event/pull", json=without_capture)

        response = self.client.post("/api/v1/event/pull", json=FACE_RECO)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(self.capture_root.glob("*.jpg")), [])

    def test_concurrent_same_source_event_keeps_only_accepted_attempt_capture(self):
        database_path = self.capture_root / "concurrent.sqlite"
        engine = create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        session_factory = sessionmaker(bind=engine)
        db = session_factory()
        try:
            db.add(Member(id="member-42", name="Member 42", is_active=True))
            db.add(
                DeviceMemberMapping(
                    id="entrance-1:member-42",
                    device_id="entrance-1",
                    member_id="member-42",
                    group_id=7,
                    group_uuid="group-7",
                    person_id=42,
                    unique_id="uid-001",
                    face_uuid="face-42",
                    sync_status="synced",
                    is_enabled=True,
                )
            )
            db.commit()
        finally:
            db.close()

        capture_barrier = threading.Barrier(2)
        capture_dir = self.capture_root / "concurrent-captures"

        class BarrierCaptureStore(DeviceCaptureStore):
            def save_bytes(self, device_id, event_id, capture_bytes):
                result = super().save_bytes(device_id, event_id, capture_bytes)
                capture_barrier.wait(timeout=5)
                return result

        real_intake = DeviceRecognitionIntake(
            session_factory,
            capture_store=BarrierCaptureStore(
                capture_dir,
                max_bytes=2048,
                max_pixels=100,
            ),
        )
        outcomes = []
        outcomes_lock = threading.Lock()

        class RecordingIntake:
            def receive(self, event, *, capture=None):
                result = real_intake.receive(event, capture=capture)
                with outcomes_lock:
                    outcomes.append(result.status)
                return result

        app = FastAPI()
        app.include_router(
            create_device_event_router(
                RecordingIntake(),
                DeviceHealthStore(session_factory),
                {"P6S-001": "entrance-1"},
            )
        )
        second_uri = "data:image/jpeg;base64," + base64.b64encode(
            SECOND_JPEG_BYTES
        ).decode("ascii")
        second_payload = {
            **FACE_RECO,
            "info": {
                **FACE_RECO["info"],
                "CaptureImage": {
                    **FACE_RECO["info"]["CaptureImage"],
                    "picture": second_uri,
                },
            },
        }
        responses = []
        errors = []

        def post(payload):
            try:
                responses.append(TestClient(app).post("/api/v1/event/pull", json=payload))
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=post, args=(FACE_RECO,)),
            threading.Thread(target=post, args=(second_payload,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertCountEqual(["received", "duplicate"], outcomes)
        db = session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual(0, db.query(MemberEntryEvent).count())
        finally:
            db.close()
        captures = list(capture_dir.glob("*.jpg"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(str(captures[0]), inbox.capture_path)
        self.assertIn(captures[0].read_bytes(), {JPEG_BYTES, SECOND_JPEG_BYTES})

    def test_push_duplicate_adopts_capture_for_poll_event_without_snapshot(self):
        poll_result = self.intake.receive(
            DeviceRecognitionEvent(
                device_id="entrance-1",
                source="poll",
                source_event_id="log-8",
                occurred_at=OCCURRED_AT,
                group_id=7,
                person_id=42,
                rule_id=1,
                unique_id="",
                similarity=0.88083099,
            )
        )
        self.assertEqual(poll_result.status, "received")

        response = self.client.post("/api/v1/event/pull", json=FACE_RECO)

        self.assertEqual(response.status_code, 200)
        captures = list(self.capture_root.glob("*.jpg"))
        self.assertEqual(len(captures), 1)
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual(inbox.source, "poll")
            self.assertEqual(inbox.capture_path, str(captures[0]))
            self.assertEqual(0, db.query(MemberEntryEvent).count())
        finally:
            db.close()

    def test_face_reco_persists_device_fingerprint_metadata_from_push(self):
        payload = {
            **FACE_RECO,
            "deviceInfo": {
                **FACE_RECO["deviceInfo"],
                "model": "P6S-X",
                "version": "3.2.1",
                "apiVersion": "p6s-http-v1",
                "protocolVersion": "p6s-http-v1",
                "metadataSource": "deviceInfo",
            },
            "info": {**FACE_RECO["info"], "eventId": 81},
        }

        response = self.client.post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual("P6S-X", inbox.device_metadata["device_model"]["normalized_value"])
            self.assertEqual("3.2.1", inbox.device_metadata["firmware_version"]["raw_value"])
            self.assertEqual("p6s-http-v1", inbox.device_metadata["api_version"]["normalized_value"])
            self.assertEqual("deviceInfo", inbox.device_metadata["metadata_source"]["raw_value"])
        finally:
            db.close()

    def test_push_device_type_and_version_are_fingerprint_fields(self):
        payload = {
            **FACE_RECO,
            "deviceInfo": {
                **FACE_RECO["deviceInfo"],
                "deviceType": "T5AI-W/IPG-5950PCS-AI",
                "deviceVersion": "3.4.0-20260430-EN",
            },
            "info": {**FACE_RECO["info"], "eventId": 82},
        }
        self.client.post("/api/v1/event/pull", json=payload)
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual(
                "T5AI-W/IPG-5950PCS-AI",
                inbox.device_metadata["device_model"]["normalized_value"],
            )
            self.assertEqual(
                "3.4.0-20260430-EN",
                inbox.device_metadata["firmware_version"]["normalized_value"],
            )
        finally:
            db.close()

    def test_face_reco_background_image_is_retained_as_separate_restricted_evidence(self):
        response = self.client.post(
            "/api/v1/event/pull", json=FACE_RECO_WITH_BACKGROUND_CAPTURE
        )

        self.assertEqual(200, response.status_code)
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertIsNotNone(inbox.capture_path)
            self.assertIsNotNone(inbox.background_capture_path)
            self.assertNotEqual(inbox.capture_path, inbox.background_capture_path)
            self.assertEqual((4, 4), (
                inbox.background_capture_width, inbox.background_capture_height
            ))
            self.assertEqual(
                __import__("hashlib").sha256(JPEG_BYTES).hexdigest(),
                inbox.background_capture_sha256,
            )
            self.assertEqual(JPEG_BYTES, Path(inbox.background_capture_path).read_bytes())
        finally:
            db.close()

    def test_background_image_dimension_mismatch_is_not_retained(self):
        payload = {
            **FACE_RECO_WITH_BACKGROUND_CAPTURE,
            "info": {
                **FACE_RECO_WITH_BACKGROUND_CAPTURE["info"],
                "BackgroundImage": {
                    **FACE_RECO_WITH_BACKGROUND_CAPTURE["info"]["BackgroundImage"],
                    "width": 8,
                    "height": 8,
                },
            },
        }

        response = self.client.post("/api/v1/event/pull", json=payload)

        self.assertEqual(200, response.status_code)
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertIsNone(inbox.background_capture_path)
            self.assertIsNone(inbox.background_capture_sha256)
        finally:
            db.close()

    def test_push_persists_sanitized_normalized_face_spatial_evidence(self):
        response = self.client.post(
            "/api/v1/event/pull",
            json=FACE_RECO_WITH_SPATIAL_EVIDENCE,
        )

        self.assertEqual(200, response.status_code)
        db = self.session_factory()
        try:
            evidence = db.query(DeviceRecognitionInbox).one().face_spatial_evidence
        finally:
            db.close()

        self.assertEqual("normalized_full_frame", evidence["coordinate_space"])
        self.assertEqual([900.0, 412.0, 1028.0, 540.0], evidence["raw_bbox"])
        self.assertEqual([1920, 1088], evidence["source_frame_size"])
        for actual, expected in zip(
            evidence["normalized_bbox"],
            (0.46875, 0.378676, 0.535417, 0.496324),
        ):
            self.assertAlmostEqual(expected, actual, places=6)
        self.assertEqual(86.5, evidence["quality"])
        self.assertEqual(-2.0, evidence["yaw"])
        self.assertEqual(1.5, evidence["pitch"])
        self.assertEqual(0.25, evidence["roll"])
        self.assertEqual("2026-07-20T05:24:59", evidence["event_time"])
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("SENTINEL_FACE_FEATURE", serialized)
        self.assertNotIn("data:image", serialized)
        self.assertNotIn("Sensitive Name", serialized)

    def test_push_accepts_background_dimensions_next_to_face_info(self):
        face_info = dict(FACE_RECO_WITH_SPATIAL_EVIDENCE["info"]["FaceInfo"])
        background = face_info.pop("BackgroundImage")
        payload = {
            **FACE_RECO_WITH_SPATIAL_EVIDENCE,
            "info": {
                **FACE_RECO_WITH_SPATIAL_EVIDENCE["info"],
                "eventId": 9,
                "FaceInfo": face_info,
                "BackgroundImage": background,
            },
        }

        response = self.client.post("/api/v1/event/pull", json=payload)

        self.assertEqual(200, response.status_code)
        db = self.session_factory()
        try:
            evidence = db.query(DeviceRecognitionInbox).one().face_spatial_evidence
        finally:
            db.close()
        self.assertEqual([1920, 1088], evidence["source_frame_size"])

    def test_push_accepts_vendor_capture_bbox_and_face_feature_shape(self):
        response = self.client.post(
            "/api/v1/event/pull",
            json=FACE_RECO_WITH_VENDOR_SPATIAL_EVIDENCE,
        )

        self.assertEqual(200, response.status_code)
        db = self.session_factory()
        try:
            evidence = db.query(DeviceRecognitionInbox).one().face_spatial_evidence
        finally:
            db.close()

        self.assertEqual([900.0, 412.0, 1028.0, 540.0], evidence["raw_bbox"])
        self.assertEqual([1920, 1088], evidence["source_frame_size"])
        self.assertEqual(70.0, evidence["quality"])
        self.assertEqual(7.055371, evidence["yaw"])
        self.assertEqual(9.877519, evidence["pitch"])
        self.assertEqual(11.756012, evidence["roll"])
        for actual, expected in zip(
            evidence["normalized_bbox"],
            (0.46875, 0.378676, 0.535417, 0.496324),
        ):
            self.assertAlmostEqual(expected, actual, places=6)
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("SENTINEL_FACE_ATTRIBUTE", serialized)
        self.assertNotIn("SENTINEL_BACKGROUND_IMAGE", serialized)
        self.assertNotIn("SENTINEL_FACE_FEATURE", serialized)
        self.assertNotIn("Sensitive Name", serialized)

    def test_intake_failure_does_not_leave_a_capture(self):
        class FailingIntake:
            def receive(self, event, *, capture=None):
                raise RuntimeError("SENTINEL_DATABASE_FAILURE")

        app = FastAPI()
        app.include_router(
            create_device_event_router(
                FailingIntake(),
                self.health_store,
                {"P6S-001": "entrance-1"},
            )
        )

        with self.assertLogs("agent.device_event_receiver", level="INFO") as logs:
            response = TestClient(app).post("/api/v1/event/pull", json=FACE_RECO)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(list(self.capture_root.glob("*.jpg")), [])
        self.assertNotIn("SENTINEL_DATABASE_FAILURE", "\n".join(logs.output))

    def test_match_number_zero_acknowledges_without_member_event(self):
        payload = {
            **FACE_RECO,
            "info": {**FACE_RECO["info"], "matchNumber": 0},
        }

        response = self.client.post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["operator"], "FaceReco-Ack")
        self.assertEqual(self._count(DeviceRecognitionInbox), 0)
        self.assertEqual(self._count(MemberEntryEvent), 0)

    def test_recognized_event_requires_unique_id_or_group_and_person_ids(self):
        for missing_field in ("groupId", "personId"):
            with self.subTest(missing_field=missing_field):
                person_info = dict(FACE_RECO["info"]["personInfo"])
                person_info.pop(missing_field)
                person_info["uniqueId"] = ""
                payload = {
                    **FACE_RECO,
                    "info": {
                        **FACE_RECO["info"],
                        "eventId": f"missing-{missing_field}",
                        "personInfo": person_info,
                    },
                }

                response = self.client.post("/api/v1/event/pull", json=payload)

                self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count(DeviceRecognitionInbox), 0)

    def test_low_similarity_is_persisted_as_ignored_without_capture(self):
        payload = {
            **FACE_RECO,
            "info": {
                **FACE_RECO["info"],
                "eventId": 9,
                "personInfo": {**FACE_RECO["info"]["personInfo"], "match": 74.999},
            },
        }

        response = self.client.post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.json()["operator"], "FaceReco-Ack")
        db = self.session_factory()
        try:
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual("ignored", inbox.status)
            self.assertIsNone(inbox.capture_path)
            self.assertEqual(0, db.query(MemberEntryEvent).count())
        finally:
            db.close()
        self.assertEqual([], list(self.capture_root.glob("*.jpg")))

    def test_malformed_and_unknown_events_do_not_persist_or_log_payload(self):
        malformed = {
            **FACE_RECO,
            "info": {**FACE_RECO["info"], "eventTime": "not-a-time"},
        }
        unknown = {
            "operator": "snapshot",
            "credential": "SENTINEL_RAW_PAYLOAD",
            "info": {"eventId": {"secret": "SENTINEL_NESTED_SECRET"}},
        }

        with self.assertLogs("agent.device_event_receiver", level="INFO") as logs:
            malformed_response = self.client.post("/api/v1/event/pull", json=malformed)
            unknown_response = self.client.post("/api/v1/event/pull", json=unknown)

        self.assertEqual(malformed_response.json()["operator"], "FaceReco-Ack")
        self.assertEqual(unknown_response.json()["operator"], "error-Ack")
        self.assertEqual(self._count(DeviceRecognitionInbox), 0)
        log_text = "\n".join(logs.output)
        self.assertNotIn("SENTINEL_RAW_PAYLOAD", log_text)
        self.assertNotIn("SENTINEL_NESTED_SECRET", log_text)
        self.assertNotIn("SENTINEL_FACE_FEATURE", log_text)
        self.assertNotIn(CAPTURE_DATA_URI, log_text)

    def test_capture_store_rejects_invalid_oversized_and_unsafe_inputs(self):
        store = DeviceCaptureStore(
            self.capture_root,
            max_bytes=len(JPEG_BYTES),
            max_pixels=100,
        )

        saved = store.save_data_uri("../entrance", "event/1", CAPTURE_DATA_URI)

        self.assertIsNotNone(saved)
        self.assertTrue(saved.created)
        resolved_path = Path(saved.path).resolve()
        self.assertEqual(resolved_path.read_bytes(), JPEG_BYTES)
        self.assertEqual(resolved_path.parent, self.capture_root.resolve())
        self.assertEqual(list(self.capture_root.glob("*.tmp")), [])
        self.assertIsNone(store.save_data_uri("d", "bad-prefix", "data:image/png;base64,AA=="))
        self.assertIsNone(store.save_data_uri("d", "bad-base64", "data:image/jpeg;base64,%%%"))
        not_jpeg = base64.b64encode(b"not-jpeg").decode("ascii")
        self.assertIsNone(store.save_data_uri("d", "bad-jpeg", f"data:image/jpeg;base64,{not_jpeg}"))
        oversized = base64.b64encode(JPEG_BYTES + b"extra-bytes").decode("ascii")
        self.assertIsNone(store.save_data_uri("d", "too-large", f"data:image/jpeg;base64,{oversized}"))

    def test_capture_store_accepts_jpg_data_uri_alias(self):
        store = DeviceCaptureStore(self.capture_root, max_bytes=1024, max_pixels=100)
        encoded = base64.b64encode(JPEG_BYTES).decode("ascii")

        saved = store.save_data_uri(
            "entrance-1",
            "jpg-alias",
            f"data:image/jpg;base64,{encoded}",
        )

        self.assertIsNotNone(saved)
        self.assertEqual(Path(saved.path).read_bytes(), JPEG_BYTES)

    def test_capture_store_releases_only_files_inside_its_root(self):
        store = DeviceCaptureStore(self.capture_root, max_bytes=1024, max_pixels=100)
        encoded = base64.b64encode(JPEG_BYTES).decode("ascii")
        saved = store.save_data_uri(
            "entrance-1",
            "release",
            f"data:image/jpeg;base64,{encoded}",
        )
        outside = self.capture_root.parent / f"outside-{uuid4().hex}.jpg"
        outside.write_bytes(JPEG_BYTES)
        self.addCleanup(outside.unlink, missing_ok=True)

        self.assertTrue(store.remove_path(saved.path))
        self.assertFalse(Path(saved.path).exists())
        self.assertFalse(store.remove_path(str(outside)))
        self.assertTrue(outside.exists())

    def test_capture_store_rejects_marker_wrapped_invalid_jpeg(self):
        store = DeviceCaptureStore(self.capture_root, max_bytes=1024, max_pixels=100)
        invalid = base64.b64encode(b"\xff\xd8not-a-decodable-jpeg\xff\xd9").decode("ascii")

        with patch("agent.device_capture_store.cv2.imdecode", return_value=None) as imdecode:
            result = store.save_data_uri(
                "entrance-1",
                "invalid-jpeg",
                f"data:image/jpeg;base64,{invalid}",
            )

        self.assertIsNone(result)
        imdecode.assert_not_called()
        self.assertEqual(list(self.capture_root.glob("*.jpg")), [])

    def test_capture_store_rejects_decoded_image_over_pixel_limit(self):
        large_jpeg = jpeg_bytes(width=20, height=10)
        data_uri = "data:image/jpeg;base64," + base64.b64encode(large_jpeg).decode("ascii")
        store = DeviceCaptureStore(
            self.capture_root,
            max_bytes=len(large_jpeg),
            max_pixels=199,
        )

        with patch(
            "agent.device_capture_store.cv2.imdecode",
            return_value=np.zeros((10, 20, 3), dtype=np.uint8),
        ) as imdecode:
            result = store.save_data_uri("entrance-1", "large", data_uri)

        self.assertIsNone(result)
        imdecode.assert_not_called()

    def test_capture_store_rejects_png_bytes_in_jpeg_data_uri(self):
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        encoded, buffer = cv2.imencode(".png", image)
        self.assertTrue(encoded)
        data_uri = "data:image/jpeg;base64," + base64.b64encode(
            buffer.tobytes()
        ).decode("ascii")
        store = DeviceCaptureStore(self.capture_root, max_bytes=1024, max_pixels=100)

        with patch("agent.device_capture_store.cv2.imdecode", return_value=None) as imdecode:
            result = store.save_data_uri("entrance-1", "png", data_uri)

        self.assertIsNone(result)
        imdecode.assert_not_called()

    def test_capture_store_hash_avoids_sanitized_identifier_collisions(self):
        store = DeviceCaptureStore(self.capture_root, max_bytes=1024, max_pixels=100)

        first = store.save_data_uri("entrance/a", "event-1", CAPTURE_DATA_URI)
        second = store.save_data_uri("entrance?a", "event-1", CAPTURE_DATA_URI)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(first.created)
        self.assertTrue(second.created)

    def test_capture_store_creates_immutable_per_attempt_paths(self):
        store = DeviceCaptureStore(self.capture_root, max_bytes=1024, max_pixels=100)
        replacement_uri = "data:image/jpeg;base64," + base64.b64encode(
            SECOND_JPEG_BYTES
        ).decode("ascii")

        first = store.save_data_uri("entrance-1", "event-1", CAPTURE_DATA_URI)
        second = store.save_data_uri("entrance-1", "event-1", replacement_uri)

        self.assertTrue(first.created)
        self.assertTrue(second.created)
        self.assertNotEqual(first.path, second.path)
        first_prefix = Path(first.path).name.split("-", 1)[0]
        second_prefix = Path(second.path).name.split("-", 1)[0]
        self.assertEqual(first_prefix, second_prefix)
        self.assertEqual(Path(first.path).read_bytes(), JPEG_BYTES)
        self.assertEqual(Path(second.path).read_bytes(), SECOND_JPEG_BYTES)


if __name__ == "__main__":
    unittest.main()
