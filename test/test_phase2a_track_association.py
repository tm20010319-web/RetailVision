from test._test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

from agent.camera_runtime_registry import CameraRuntimeRegistry
from agent.camera_track_identity import FaceSpatialEvidence, TrackSnapshot, TrackSnapshotBuffer, StoreTrackCandidateProvider
from agent.device_camera_registration import DeviceCameraRegistrationService
from agent.models import DeviceCameraRegistration, DeviceRecognitionInbox, Member, TrackAssociationEvaluation
from agent.recognition_association_worker import RecognitionAssociationWorker, TrackAssociationCandidate
from agent.schema_evolution import evolve_schema
from agent.track_association_policy import TrackAssociationAuditService


class Phase2ATrackAssociationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 6, 10, 0, 0)
        self.engine = create_engine("sqlite:///:memory:")
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _inbox(
        self,
        *,
        event_id="event-1",
        occurred_at=None,
        deadline=None,
        face_spatial_evidence=None,
    ):
        db = self.sessions()
        db.add(DeviceRecognitionInbox(
            canonical_event_id=event_id,
            device_id="device-1",
            source="push",
            source_event_id=event_id,
            occurred_at=occurred_at or self.now,
            store_id="store-1",
            member_id="member-1",
            association_deadline_at=deadline or (self.now + timedelta(seconds=5)),
            face_spatial_evidence=face_spatial_evidence,
        ))
        db.commit()
        row = db.get(DeviceRecognitionInbox, event_id)
        db.close()
        return row

    def _provider(self, *, face=None):
        db = self.sessions()
        db.add(DeviceCameraRegistration(
            device_id="device-1",
            camera_id="camera-1",
            effective_from=self.now - timedelta(minutes=1),
        ))
        db.commit()
        db.close()
        buffer = TrackSnapshotBuffer(clock=lambda: self.now)
        for index in range(2):
            buffer.publish(TrackSnapshot(
                store_id="store-1",
                camera_id="camera-1",
                runtime_id="runtime-instance-1",
                track_id=7,
                track_token="store-1:camera-1:runtime-instance-1:7",
                observed_at=self.now + timedelta(milliseconds=index * 250),
                bbox=(0.1, 0.1, 0.5, 0.9),
                confidence=0.9,
                role="customer",
                stable_frames=1,
                is_frame_clipped=False,
                frame_size=(2560, 1440),
            ))
        registry = CameraRuntimeRegistry(clock=lambda: self.now)
        registry.activate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-instance-1",
        )
        provider = StoreTrackCandidateProvider(
            buffer,
            registration_service=DeviceCameraRegistrationService(self.sessions),
            runtime_registry=registry,
            runtime_instance_id="runtime-instance-1",
            store_id="store-1",
            clock=lambda: self.now + timedelta(seconds=1),
            evidence_provider=face,
        )
        return provider

    def test_history_buffer_and_lost_reset(self):
        buffer = TrackSnapshotBuffer(clock=lambda: self.now)
        token = "store-1:camera-1:runtime-instance-1:1"
        first = buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 1, token,
            self.now, (0, 0, 1, 1), .9, "customer", 1, False,
        ))
        second = buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 1, token,
            self.now + timedelta(seconds=1), (0, 0, 1, 1), .9, "customer", 1, False,
        ))
        reset = buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 1, token,
            self.now + timedelta(seconds=4), (0, 0, 1, 1), .9, "customer", 1, False,
        ))
        self.assertEqual(1, first.stable_frames)
        self.assertEqual(2, second.stable_frames)
        self.assertEqual(1, reset.stable_frames)
        self.assertEqual(1, len(buffer.snapshots(
            store_id="store-1", camera_id="camera-1", runtime_id="runtime-instance-1",
            occurred_at=self.now + timedelta(seconds=1), include_history=True,
        )))

    def test_provider_waits_for_watermark_then_returns_probable(self):
        row = self._inbox()
        provider = self._provider()
        waiting = provider(row, now=self.now + timedelta(seconds=1))
        self.assertTrue(waiting.should_continue_wait)

        provider.track_snapshot_buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 7,
            "store-1:camera-1:runtime-instance-1:7",
            self.now + timedelta(seconds=0.5), (0.1, 0.1, 0.5, 0.9), .9,
            "customer", 3, False,
        ))
        result = provider(row, now=self.now + timedelta(seconds=1))
        self.assertEqual("probable", result.status)
        self.assertFalse(result.should_continue_wait)

    def test_multiple_eligible_tracks_are_ambiguous_without_forced_selection(self):
        row = self._inbox()
        provider = self._provider()
        provider.track_snapshot_buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 7,
            "store-1:camera-1:runtime-instance-1:7",
            self.now + timedelta(seconds=0.5), (0.1, 0.1, 0.5, 0.9), .9,
            "customer", 3, False,
        ))
        provider.track_snapshot_buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 8,
            "store-1:camera-1:runtime-instance-1:8",
            self.now, (0.6, 0.1, 0.9, 0.9), .99,
            "customer", 3, False,
        ))
        provider.track_snapshot_buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 8,
            "store-1:camera-1:runtime-instance-1:8",
            self.now + timedelta(seconds=0.25), (0.6, 0.1, 0.9, 0.9), .99,
            "customer", 3, False,
        ))
        provider.track_snapshot_buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 8,
            "store-1:camera-1:runtime-instance-1:8",
            self.now + timedelta(seconds=0.5), (0.6, 0.1, 0.9, 0.9), .99,
            "customer", 3, False,
        ))
        result = provider(row, now=self.now + timedelta(seconds=1))
        self.assertEqual("ambiguous", result.status)
        self.assertIsNone(result.track_token)
        self.assertEqual(2, result.candidate_count)

    def test_confirmed_requires_injected_verified_spatial_evidence(self):
        row = self._inbox()
        provider = self._provider(face=lambda *_args: FaceSpatialEvidence(
            bbox=(0.2, 0.15, 0.3, 0.25),
            normalized=True,
            quality_status="verified",
        ))
        provider.track_snapshot_buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 7,
            "store-1:camera-1:runtime-instance-1:7",
            self.now + timedelta(seconds=0.5), (0.1, 0.1, 0.5, 0.9), .9,
            "customer", 3, False,
        ))
        result = provider(row, now=self.now + timedelta(seconds=1))
        self.assertEqual("confirmed", result.status)
        self.assertEqual("verified", result.spatial_evidence["quality_status"])

    def test_provider_uses_sanitized_inbox_face_spatial_evidence(self):
        row = self._inbox(face_spatial_evidence={
            "coordinate_space": "normalized_full_frame",
            "raw_bbox": [900.0, 412.0, 1028.0, 540.0],
            "normalized_bbox": [900 / 1920, 412 / 1080, 1028 / 1920, 540 / 1080],
            "source_frame_size": [1920, 1080],
            "quality_status": "parsed",
            "quality": 86.5,
            "yaw": -2.0,
            "pitch": 1.5,
            "roll": 0.25,
            "event_time": self.now.isoformat(),
        })
        provider = self._provider()
        provider.track_snapshot_buffer.publish(TrackSnapshot(
            "store-1", "camera-1", "runtime-instance-1", 7,
            "store-1:camera-1:runtime-instance-1:7",
            self.now + timedelta(seconds=0.5), (0.4, 0.2, 0.65, 0.9), .9,
            "customer", 3, False,
            frame_size=(2560, 1440),
        ))

        result = provider(row, now=self.now + timedelta(seconds=1))

        self.assertEqual("confirmed", result.status)
        self.assertEqual(
            "store-1:camera-1:runtime-instance-1:7",
            result.track_token,
        )
        self.assertEqual("normalized_full_frame", result.spatial_evidence["coordinate_space"])
        self.assertEqual("parsed", result.spatial_evidence["quality_status"])
        self.assertEqual([900.0, 412.0, 1028.0, 540.0], result.spatial_evidence["raw_face_bbox"])
        self.assertEqual(1, result.spatial_evidence["final_candidate_count"])
        self.assertEqual("confirmed", result.spatial_evidence["assessment_result"])
        self.assertTrue(result.spatial_evidence["tracks"][0]["matched"])
        self.assertEqual(
            0.5,
            result.spatial_evidence["tracks"][0]["track_time_delta_seconds"],
        )
        self.assertEqual(self.now.isoformat(), result.spatial_evidence["reference_time"])
        self.assertEqual(
            "face_spatial_evidence",
            result.spatial_evidence["reference_time_source"],
        )
        self.assertEqual(
            (self.now + timedelta(seconds=0.5)).isoformat(),
            result.spatial_evidence["primary_snapshot_observed_at"],
        )
        self.assertEqual(0.5, result.spatial_evidence["primary_time_delta_seconds"])
        self.assertEqual(0.5, result.spatial_evidence["primary_time_offset_seconds"])
        self.assertEqual(result.time_delta_seconds, 0.5)
        self.assertEqual(
            result.track_token, result.spatial_evidence["primary_track_token"]
        )

    def test_provider_returns_snapshot_window_incomplete_at_deadline(self):
        row = self._inbox(deadline=self.now + timedelta(seconds=1))
        provider = self._provider()
        result = provider(row, now=self.now + timedelta(seconds=2))
        self.assertEqual("no_track_match", result.status)
        self.assertEqual("snapshot_window_incomplete", result.reason)

    def test_device_camera_resolution_uses_event_time_boundary(self):
        db = self.sessions()
        db.add_all([
            DeviceCameraRegistration(
                device_id="device-1", camera_id="camera-old",
                effective_from=self.now - timedelta(minutes=2),
                effective_to=self.now,
                status="closed",
            ),
            DeviceCameraRegistration(
                device_id="device-1", camera_id="camera-new",
                effective_from=self.now, status="active",
            ),
        ])
        db.commit()
        db.close()
        service = DeviceCameraRegistrationService(self.sessions)
        self.assertEqual(
            "camera-old",
            service.resolve_at(
                device_id="device-1",
                occurred_at=self.now - timedelta(microseconds=1),
            ).camera_id,
        )
        self.assertEqual(
            "camera-new",
            service.resolve_at(device_id="device-1", occurred_at=self.now).camera_id,
        )

    def test_phase2_schema_evolution_is_idempotent_without_jobs(self):
        evolve_schema(self.engine)
        evolve_schema(self.engine)
        inspector = inspect(self.engine)
        self.assertTrue(inspector.has_table("track_identity_bindings"))
        self.assertTrue(inspector.has_table("track_association_finalization_jobs"))
        db = self.sessions()
        try:
            count = db.execute(text(
                "SELECT COUNT(*) FROM track_association_finalization_jobs"
            )).scalar_one()
            self.assertEqual(0, count)
        finally:
            db.close()

    def test_shadow_confirmed_evaluation_is_immutable_and_idempotent(self):
        audit = TrackAssociationAuditService(self.sessions)
        kwargs = dict(
            recognition_event_id="event-1",
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-owner",
            runtime_instance_id="runtime-instance-1",
            result="confirmed",
            method="injected_face_spatial_evidence",
            reason="verified_face_spatial_evidence",
            candidates=[{"track_token": "store-1:camera-1:runtime-instance-1:7"}],
            observed_at=self.now,
            algorithm_version="association-v1",
            parameter_version="defaults-v1",
            policy_version_id=None,
            mode="shadow",
        )
        first = audit.record_evaluation(**kwargs)
        second = audit.record_evaluation(**kwargs)
        self.assertEqual(first.id, second.id)
        db = self.sessions()
        try:
            rows = db.query(TrackAssociationEvaluation).all()
            self.assertEqual(1, len(rows))
            self.assertEqual("confirmed", rows[0].result)
            self.assertFalse(rows[0].applied_binding)
        finally:
            db.close()

    def test_worker_persists_evaluation_before_resolver(self):
        db = self.sessions()
        db.add(Member(id="member-1", name="Member", gender="unknown", tier="gold", is_active=True))
        db.add(DeviceRecognitionInbox(
            canonical_event_id="event-worker",
            device_id="device-1",
            source="push",
            source_event_id="event-worker",
            occurred_at=self.now,
            store_id="store-1",
            member_id="member-1",
            similarity=.95,
            recognition_status="accepted",
            store_resolution_status="resolved",
            association_status="pending",
            track_association_status="pending",
            association_deadline_at=self.now + timedelta(seconds=5),
        ))
        db.commit()
        db.close()
        requests = []
        resolver = SimpleNamespace(resolve=lambda request: requests.append(request))
        lease = SimpleNamespace(
            store_id="store-1", mode="new", owner_id="runtime-owner",
            lease_token="token", lease_generation=1,
        )
        worker = RecognitionAssociationWorker(
            self.sessions,
            resolver,
            lease,
            clock=lambda: self.now,
            runtime_instance_id="runtime-instance-1",
            audit_service=TrackAssociationAuditService(self.sessions),
            candidate_provider=lambda _row, **_kwargs: TrackAssociationCandidate(
                status="confirmed",
                camera_id="camera-1",
                track_token="store-1:camera-1:runtime-instance-1:7",
                method="verified_face_spatial_evidence",
                reason="verified_face_spatial_evidence",
                algorithm_version="association-v1",
                parameter_version="defaults-v1",
                spatial_evidence={
                    "coordinate_space": "normalized_full_frame",
                    "raw_face_bbox": [900.0, 412.0, 1028.0, 540.0],
                    "normalized_face_bbox": [0.46875, 0.381481, 0.535417, 0.5],
                    "tracks": [{
                        "track_token": "store-1:camera-1:runtime-instance-1:7",
                        "person_bbox": [0.4, 0.2, 0.65, 0.9],
                        "containment": True,
                        "face_center": [0.502083, 0.440741],
                        "upper_body_check": True,
                        "matched": True,
                    }],
                    "final_candidate_count": 1,
                    "assessment_result": "confirmed",
                },
            ),
        )
        self.assertEqual(1, worker.run_once())
        self.assertEqual(1, len(requests))
        self.assertIsNotNone(requests[0].evaluation_id)
        db = self.sessions()
        try:
            evaluation = db.get(TrackAssociationEvaluation, requests[0].evaluation_id)
            self.assertIsNotNone(evaluation)
            self.assertEqual("confirmed", evaluation.result)
            self.assertFalse(evaluation.applied_binding)
            self.assertEqual(
                "normalized_full_frame",
                evaluation.spatial_evidence["coordinate_space"],
            )
            self.assertEqual(1, evaluation.spatial_evidence["final_candidate_count"])
            self.assertEqual(
                0,
                db.execute(text("SELECT COUNT(*) FROM track_identity_bindings")).scalar_one(),
            )
            self.assertEqual(
                0,
                db.execute(text(
                    "SELECT COUNT(*) FROM track_association_finalization_jobs"
                )).scalar_one(),
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
