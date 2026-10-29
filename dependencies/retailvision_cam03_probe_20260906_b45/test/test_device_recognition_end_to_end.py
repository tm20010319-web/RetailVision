"""End-to-end coverage for unified device recognition ingestion."""

from __future__ import annotations

import base64
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from test._test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.camera_face_api import CameraApiTransportError
from agent.schema_evolution import evolve_schema
from agent.device_capture_store import DeviceCaptureStore
from agent.device_recognition_intake import DeviceRecognitionIntake, source_event_key
from agent.device_event_receiver import create_device_event_router
from agent.device_event_worker import DeviceRecognitionInboxWorker
from agent.device_health import DeviceHealthStore
from agent.device_identity import (
    DeviceMemberMappingData,
    RecognitionRecordPoller,
    SqlAlchemyDeviceIdentityRepository,
)
from agent.entry_event_coordinator import EntryEventCoordinator
from agent.customer_entry_visit import (
    CustomerEntryObservation,
    CustomerEntryVisit,
)
from agent.member_entry_service import MemberEntryService
from agent.models import BusinessJob, DeviceRecognitionInbox, Member, MemberEntryEvent, Visit
from agent.profile_jobs import ProfileJobWorker
from pipelines.member_business_runtime import MemberBusinessRuntime


class FakePollTransport:
    """Deterministic camera transport; production poller owns the workflow."""

    device_id = "entrance-1"

    def __init__(self, records=None, *, fail_requests=False):
        self.records = list(records or [])
        self.fail_requests = fail_requests

    def query_recognition_records(self, *_args, **_kwargs):
        if self.fail_requests:
            raise CameraApiTransportError("planned poll failure", retryable=True)
        records, self.records = self.records, []
        return records


class DeterministicProfilePipeline:
    """The only model fake: a stable successful profile response."""

    def run_from_image_path(self, image_path, track_id, camera_id="", zone_name=""):
        return {
            "profile": {
                "style_tags": ["casual"],
                "source_image": Path(image_path).name,
            },
            "track_id": track_id,
            "camera_id": camera_id,
            "zone_name": zone_name,
            "crop_path": image_path,
        }


class RecordingRecognitionIntake:
    """Record normalized intake results at the HTTP boundary."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.results = []

    def receive(self, event, *, capture=None):
        result = self.delegate.receive(event, capture=capture)
        self.results.append((event, result))
        return result

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class DeviceRecognitionEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now().replace(microsecond=0)
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

        self.capture_root = Path.cwd() / ".task7-device-recognition" / uuid4().hex
        self.capture_root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.capture_root, True)
        self._seed_member_and_mapping()

        self.repository = SqlAlchemyDeviceIdentityRepository(self.sessions)
        self.intake = DeviceRecognitionIntake(
            self.sessions,
            capture_store=DeviceCaptureStore(self.capture_root, max_bytes=4096),
        )
        self.receiver_intake = RecordingRecognitionIntake(self.intake)
        member_arrival = MemberBusinessRuntime(
            snapshot_store=SimpleNamespace(),
            service=MemberEntryService(self.sessions, clock=lambda: self.now),
            profile_worker=None,
            recovery_interval_seconds=30.0,
            snapshot_ttl_seconds=3600.0,
            customer_entry_visit=CustomerEntryVisit(
                self.sessions,
                clock=lambda: self.now,
            ),
        )
        self.coordinator = EntryEventCoordinator(
            on_member_entry=member_arrival.handle_member_entry,
            track_wait_seconds=1.0,
        )
        self.inbox_worker = DeviceRecognitionInboxWorker(
            session_factory=self.sessions,
            coordinator=self.coordinator,
            intake=self.intake,
            member_arrival=member_arrival,
            reconcile_waiting=self.intake._reconcile_waiting,
            clock=lambda: self.now,
        )
        self.member_arrival = member_arrival
        self.profile_worker = ProfileJobWorker(
            self.sessions,
            DeterministicProfilePipeline(),
            clock=lambda: self.now + timedelta(seconds=10),
        )
        app = FastAPI()
        app.include_router(
            create_device_event_router(
                self.receiver_intake,
                DeviceHealthStore(self.sessions),
                {"P6S-001": "entrance-1"},
            )
        )
        self.client = TestClient(app)

    def _seed_member_and_mapping(self):
        db = self.sessions()
        try:
            db.add(Member(id="member-1", name="Member One", is_active=True))
            db.commit()
        finally:
            db.close()
        SqlAlchemyDeviceIdentityRepository(self.sessions).save_mapping(
            DeviceMemberMappingData(
                device_id="entrance-1",
                member_id="member-1",
                group_id=0,
                group_uuid="group-zero",
                person_id=7,
                unique_id="member-one",
                face_uuid="face-one",
                sync_status="synced",
            )
        )

    def _face_reco_payload(self, event_id):
        encoded, image = cv2.imencode(
            ".jpg", np.full((4, 4, 3), 127, dtype=np.uint8)
        )
        self.assertTrue(encoded)
        picture = base64.b64encode(image.tobytes()).decode("ascii")
        return {
            "operator": "FaceReco",
            "deviceInfo": {"serialNumber": "P6S-001"},
            "info": {
                "eventId": event_id,
                "eventTime": self.now.isoformat() + "+08:00",
                "matchNumber": 1,
                "personInfo": {
                    "uniqueId": "member-one",
                    "groupId": 0,
                    "personId": 7,
                    "match": 91.0,
                },
                "CaptureImage": {"picture": "data:image/jpeg;base64," + picture},
            },
        }

    def _poller(self, records=None, *, fail_requests=False):
        return RecognitionRecordPoller(
            client=FakePollTransport(records, fail_requests=fail_requests),
            repository=self.repository,
            intake=self.intake,
            clock=lambda: self.now,
        )

    def _matching_poll_record(self, log_id):
        return {
            "log_id": log_id,
            "occurred_at": self.now,
            "person_id": 7,
            "group_id": 0,
            "unique_id": "",
            "similarity": 0.91,
            "reco_valid": True,
            "pass_code": 0,
            "rule_id": 1,
        }

    def _drain_pipeline(self):
        self.assertEqual(1, self.inbox_worker.run_once())
        self.coordinator.flush(self.now + timedelta(seconds=2))
        self.assertTrue(self.profile_worker.run_once())

    def _push_result(self, event_id):
        source_event_id = source_event_key(event_id, self.now)
        matches = [
            result
            for event, result in self.receiver_intake.results
            if event.source_event_id == source_event_id
        ]
        self.assertEqual(1, len(matches))
        return matches[0]

    def _assert_one_succeeded_flow(self, canonical_event_id):
        db = self.sessions()
        try:
            self.assertEqual(1, db.query(DeviceRecognitionInbox).count())
            inbox = db.query(DeviceRecognitionInbox).one()
            self.assertEqual(canonical_event_id, inbox.canonical_event_id)
            self.assertEqual("member-1", inbox.member_id)
            self.assertEqual("processed", inbox.status)
            self.assertEqual(1, db.query(MemberEntryEvent).count())
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(1, db.query(BusinessJob).count())
            member_event = db.query(MemberEntryEvent).one()
            visit = db.query(Visit).one()
            job = db.query(BusinessJob).one()
            self.assertEqual("member-1", member_event.member_id)
            self.assertEqual("member-1", visit.member_id)
            self.assertEqual("succeeded", job.status)
            self.assertEqual("member-1", job.member_id)
            self.assertEqual(canonical_event_id, member_event.event_id)
            self.assertEqual(canonical_event_id, visit.device_event_id)
            self.assertEqual(canonical_event_id, job.event_id)
            self.assertIsNotNone(inbox.capture_path)
            capture_path = Path(inbox.capture_path).resolve()
            self.assertTrue(capture_path.is_file())
            self.assertTrue(capture_path.is_relative_to(self.capture_root.resolve()))
            self.assertEqual(str(capture_path), member_event.snapshot_path)
            self.assertEqual(str(capture_path), job.payload["crop_path"])
            self.assertTrue(
                Path(job.payload["crop_path"]).resolve().is_relative_to(
                    self.capture_root.resolve()
                )
            )
            self.assertEqual(str(capture_path), job.result["crop_path"])
            self.assertEqual(capture_path.name, job.result["profile"]["source_image"])
            member = db.get(Member, "member-1")
            self.assertEqual(canonical_event_id, member.profile["latest_customer_profile_event_id"])
            self.assertIn("latest_customer_profile", member.profile)
        finally:
            db.close()

    def test_smoke_profile_status_verifies_receiver_capture_provenance(self):
        from scripts.device_recognition_smoke import profile_status_for_job

        capture_path = self.capture_root / "receiver-capture.jpg"
        capture_path.write_bytes(b"receiver-capture")
        job = SimpleNamespace(
            status="succeeded",
            payload={"crop_path": str(capture_path)},
            result={
                "crop_path": str(capture_path),
                "profile": {"source_image": capture_path.name},
            },
        )

        self.assertEqual(
            "succeeded_capture_verified",
            profile_status_for_job(job, str(capture_path), self.capture_root),
        )

    def test_smoke_profile_status_rejects_capture_outside_receiver_root(self):
        from scripts.device_recognition_smoke import profile_status_for_job

        outside_capture = Path.cwd() / (uuid4().hex + ".jpg")
        outside_capture.write_bytes(b"untrusted-capture")
        self.addCleanup(outside_capture.unlink, missing_ok=True)
        job = SimpleNamespace(
            status="succeeded",
            payload={"crop_path": str(outside_capture)},
            result={"crop_path": str(outside_capture)},
        )

        self.assertEqual(
            "succeeded_capture_mismatch",
            profile_status_for_job(job, str(outside_capture), self.capture_root),
        )

    def test_smoke_synthetic_capture_is_accepted_by_receiver_store(self):
        from scripts.device_recognition_smoke import SYNTHETIC_CAPTURE_DATA_URI

        capture = DeviceCaptureStore(self.capture_root, max_bytes=4096).save_data_uri(
            "entrance-1", "smoke-event", SYNTHETIC_CAPTURE_DATA_URI
        )

        self.assertIsNotNone(capture)
        self.assertTrue(Path(capture.path).is_file())

    def test_smoke_wait_stops_when_inbox_visit_and_profile_are_complete(self):
        from scripts.device_recognition_smoke import EventState, wait_for_event_state

        states = iter(
            [
                EventState("received", 0, "not_started", False),
                EventState("processed", 1, "succeeded_capture_verified", True),
            ]
        )
        elapsed = [0.0]
        sleeps = []

        state, timed_out = wait_for_event_state(
            lambda: next(states),
            timeout_seconds=2.0,
            interval_seconds=0.5,
            clock=lambda: elapsed[0],
            sleep=lambda seconds: (sleeps.append(seconds), elapsed.__setitem__(0, elapsed[0] + seconds)),
        )

        self.assertFalse(timed_out)
        self.assertEqual("processed", state.canonical_event_status)
        self.assertEqual(1, state.visit_count)
        self.assertEqual("succeeded_capture_verified", state.profile_status)
        self.assertEqual([0.5], sleeps)

    def test_smoke_wait_times_out_with_injected_clock(self):
        from scripts.device_recognition_smoke import EventState, wait_for_event_state

        elapsed = [0.0]
        sleeps = []

        state, timed_out = wait_for_event_state(
            lambda: EventState("received", 0, "not_started", False),
            timeout_seconds=1.0,
            interval_seconds=0.25,
            clock=lambda: elapsed[0],
            sleep=lambda seconds: (sleeps.append(seconds), elapsed.__setitem__(0, elapsed[0] + seconds)),
        )

        self.assertTrue(timed_out)
        self.assertEqual("received", state.canonical_event_status)
        self.assertEqual(0, state.visit_count)
        self.assertEqual("not_started", state.profile_status)
        self.assertEqual([0.25, 0.25, 0.25, 0.25], sleeps)

    def test_smoke_event_lookup_ignores_other_devices_and_poll_rows(self):
        from scripts.device_recognition_smoke import event_state_for_source

        db = self.sessions()
        try:
            db.add_all(
                [
                    DeviceRecognitionInbox(
                        canonical_event_id="target-push",
                        device_id="entrance-1",
                        source="push",
                        source_event_id="shared-event-id",
                        occurred_at=self.now,
                        member_id="member-1",
                        unique_id="member-one",
                        status="processed",
                        received_at=self.now,
                    ),
                    DeviceRecognitionInbox(
                        canonical_event_id="other-device-push",
                        device_id="entrance-2",
                        source="push",
                        source_event_id="shared-event-id",
                        occurred_at=self.now,
                        member_id="member-1",
                        unique_id="member-one",
                        status="received",
                        received_at=self.now + timedelta(seconds=2),
                    ),
                    DeviceRecognitionInbox(
                        canonical_event_id="target-poll",
                        device_id="entrance-1",
                        source="poll",
                        source_event_id="shared-event-id",
                        occurred_at=self.now,
                        member_id="member-1",
                        unique_id="member-one",
                        status="received",
                        received_at=self.now + timedelta(seconds=3),
                    ),
                ]
            )
            db.commit()
        finally:
            db.close()

        state = event_state_for_source(
            self.sessions,
            "entrance-1",
            "shared-event-id",
            "member-1",
            self.capture_root,
            DeviceRecognitionInbox,
            Visit,
            BusinessJob,
        )

        self.assertEqual("processed", state.canonical_event_status)
        self.assertEqual(0, state.visit_count)
        self.assertEqual("not_started", state.profile_status)

    def test_smoke_capture_mismatch_is_not_complete(self):
        from scripts.device_recognition_smoke import event_state_for_source

        db = self.sessions()
        try:
            db.add(
                DeviceRecognitionInbox(
                    canonical_event_id="capture-mismatch",
                    device_id="entrance-1",
                    source="push",
                    source_event_id="capture-mismatch-source",
                    occurred_at=self.now,
                    member_id="member-1",
                    unique_id="member-one",
                    capture_path=str(self.capture_root / "missing.jpg"),
                    status="processed",
                    received_at=self.now,
                )
            )
            db.add(
                Visit(
                    member_id="member-1",
                    device_event_id="capture-mismatch",
                    is_member=True,
                )
            )
            db.add(
                BusinessJob(
                    event_id="capture-mismatch",
                    member_id="member-1",
                    task_type="customer_profile",
                    status="succeeded",
                    payload={"crop_path": "captures/other.jpg"},
                    result={"crop_path": "captures/other.jpg"},
                )
            )
            db.commit()
        finally:
            db.close()

        state = event_state_for_source(
            self.sessions,
            "entrance-1",
            "capture-mismatch-source",
            "member-1",
            self.capture_root,
            DeviceRecognitionInbox,
            Visit,
            BusinessJob,
        )

        self.assertEqual("succeeded_capture_mismatch", state.profile_status)
        self.assertFalse(state.complete)

    def test_push_then_poll_creates_one_canonical_visit_and_profile_job(self):
        response = self.client.post(
            "/api/v1/event/pull", json=self._face_reco_payload("push-1")
        )

        self.assertEqual(200, response.status_code)
        push_result = self._push_result("push-1")
        self.assertEqual("received", push_result.status)
        results = self._poller([self._matching_poll_record(77)]).poll_once()
        self.assertEqual(["duplicate"], [result.status for result in results])
        self.assertEqual(push_result.canonical_event_id, results[0].canonical_event_id)

        self._drain_pipeline()
        self._assert_one_succeeded_flow(push_result.canonical_event_id)

    def test_recognition_upgrades_unique_entry_visit_without_duplicate(self):
        entry_visit = CustomerEntryVisit(
            self.sessions,
            clock=lambda: self.now,
        ).confirm_entry(
            CustomerEntryObservation(
                store_id="store-1",
                camera_id="entrance-1",
                tracker_track_id=7,
                runtime_session_id="runtime-1",
                direction="outside_to_inside",
                occurred_at=self.now,
                confirmed_at=self.now,
                staff_status="customer",
                staff_source="behavior_role",
                staff_confidence=None,
                entry_confidence=0.95,
                dedup_key="entry-before-recognition",
            )
        )
        response = self.client.post(
            "/api/v1/event/pull",
            json=self._face_reco_payload("push-entry-visit"),
        )
        self.assertEqual(200, response.status_code)
        canonical_event_id = self._push_result(
            "push-entry-visit"
        ).canonical_event_id

        self._drain_pipeline()

        db = self.sessions()
        try:
            self.assertEqual(1, db.query(Visit).count())
            visit = db.query(Visit).one()
            self.assertEqual(entry_visit.id, visit.id)
            self.assertEqual("member-1", visit.member_id)
            self.assertIsNotNone(visit.entry_event_id)
            self.assertIsNone(visit.device_event_id)
            member_event = db.get(MemberEntryEvent, canonical_event_id)
            self.assertEqual(visit.id, member_event.visit_id)
            self.assertEqual("finalized", member_event.status)
            evidence = db.get(DeviceRecognitionInbox, canonical_event_id)
            self.assertEqual("matched", evidence.association_status)
            self.assertEqual(visit.id, evidence.matched_visit_id)
            self.assertEqual(1, db.get(Member, "member-1").visit_count)
        finally:
            db.close()

    def test_poll_then_push_creates_one_canonical_visit_and_profile_job(self):
        results = self._poller([self._matching_poll_record(78)]).poll_once()
        self.assertEqual(["received"], [result.status for result in results])

        response = self.client.post(
            "/api/v1/event/pull", json=self._face_reco_payload("push-2")
        )
        self.assertEqual(200, response.status_code)
        push_result = self._push_result("push-2")
        self.assertEqual("duplicate", push_result.status)
        self.assertEqual(results[0].canonical_event_id, push_result.canonical_event_id)

        self._drain_pipeline()
        self._assert_one_succeeded_flow(results[0].canonical_event_id)

    def test_poll_failure_does_not_stop_the_push_flow(self):
        self.assertEqual([], self._poller(fail_requests=True).poll_once())

        response = self.client.post(
            "/api/v1/event/pull", json=self._face_reco_payload("push-3")
        )
        self.assertEqual(200, response.status_code)
        push_result = self._push_result("push-3")
        self.assertEqual("received", push_result.status)

        self._drain_pipeline()
        self._assert_one_succeeded_flow(push_result.canonical_event_id)


if __name__ == "__main__":
    unittest.main()
