from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.models import Base, BehaviorEvent, OfflineBehaviorPublication  # noqa: E402
from agent.offline_artifacts import (  # noqa: E402
    OfflineArtifactManifestCanonicalizerV1,
    OfflineRunArtifactStore,
    StaticArtifactStorageSecurityAdapter,
)
from agent.offline_behavior import OfflineBehaviorIdentityProjector, Stage9BehaviorFactAdapter  # noqa: E402
from agent.offline_publication import OfflineBehaviorTimePublicationGate  # noqa: E402
from agent.offline_publication import (  # noqa: E402
    OfflineBehaviorPublisher,
    TrustedOfflinePublicationGate,
)
from agent.offline_publication_sql import SqlAlchemyOfflinePublicationRepository  # noqa: E402
from agent.behavior_events import BehaviorEventStore  # noqa: E402
from agent.offline_source import (  # noqa: E402
    HistoricalRegistrationInterval,
    P6SRecordingSource,
    RegistrationScopeValidator,
)
from agent.offline_source import TrustedSourceVideoVerifier, TrustedSourceVideoResult  # noqa: E402
from agent.offline_media import (  # noqa: E402
    CapabilityProfileResolution,
    OfflineRecordingTimeNormalizer,
    P6SCapabilityProfile,
    P6SCapabilityProfileResolver,
    SegmentDurationAssessment,
    assess_segment_duration,
)
from agent.offline_video_models import (  # noqa: E402
    OfflineBehaviorPass,
    OfflineIdentityPassResult,
    OfflineVideoAnalysisRequest,
)
from agent.offline_identity import OfflineTrackIdentityAssociation  # noqa: E402
from pipelines.offline_behavior_pipeline import (  # noqa: E402
    OfflineBehaviorPipeline,
    OfflineOverlayRenderResult,
)


class _ProjectionSpy:
    def __init__(self, artifact_root: Path, *, fail: bool = False) -> None:
        self.artifact_root = artifact_root
        self.fail = fail
        self.calls = []
        self.sealed_at_write = False

    def write(self, facts, *, store_id, artifact_manifest_hash=None):
        self.sealed_at_write = any(
            self.artifact_root.rglob("artifact_manifest.json")
        )
        self.calls.append((tuple(facts), store_id, artifact_manifest_hash))
        if self.fail:
            raise RuntimeError("restricted payload must not escape")
        return tuple(facts)


class OfflineBehaviorPipelineTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.raw_start = datetime(2026, 8, 17, 10)
        self.utc_start = datetime(2026, 8, 17, 2)
        self.profile_resolution = self._resolved_profile()

    def tearDown(self):
        self.engine.dispose()

    def test_verified_time_authoritative_run_publishes_anonymous_when_provider_a_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            pipeline = self._pipeline(root)

            result = pipeline.run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            db = self.sessions()
            try:
                event = db.query(BehaviorEvent).one()
                self.assertTrue(result.publication_eligible)
                self.assertEqual("unavailable", result.identity_status)
                self.assertEqual("published", result.publication.status)
                self.assertIsNone(event.member_id)
                self.assertEqual("store", event.store_id)
                self.assertEqual(1, db.query(OfflineBehaviorPublication).count())
                metadata = json.loads(
                    (Path(result.artifact_directory) / "metadata.json").read_text(encoding="utf-8")
                )
                source_manifest = json.loads(
                    (Path(result.artifact_directory) / "source_manifest.json").read_text(encoding="utf-8")
                )
                summary = json.loads(
                    (Path(result.artifact_directory) / "behavior_summary.json").read_text(encoding="utf-8")
                )
                self.assertEqual(self.raw_start, datetime.fromisoformat(metadata["raw_started_at"]))
                self.assertEqual(self.utc_start, datetime.fromisoformat(metadata["normalized_started_at_utc"]))
                self.assertEqual(self.utc_start, datetime.fromisoformat(source_manifest["normalized_started_at_utc"]))
                self.assertEqual("available", summary["absolute_event_time_status"])
                self.assertEqual("unavailable", summary["identity_status"])
            finally:
                db.close()

    def test_product_analytics_projection_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            writer = _ProjectionSpy(root)

            result = self._pipeline(
                root, product_analytics_writer=writer
            ).run(
                request=self._request(video, origin="p6s_recording_file"),
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertEqual("disabled", result.product_analytics_projection_status)
            self.assertIsNone(result.product_analytics_projection_reason)
            self.assertEqual([], writer.calls)

    def test_enabled_product_analytics_writes_only_after_seal_without_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            writer = _ProjectionSpy(root)

            result = self._pipeline(
                root,
                product_analytics_writer=writer,
                product_analytics_enabled=True,
            ).run(
                request=self._request(video, origin="p6s_recording_file"),
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertEqual("completed", result.product_analytics_projection_status)
            self.assertIsNone(result.product_analytics_projection_reason)
            self.assertEqual("unavailable", result.identity_status)
            self.assertTrue(writer.sealed_at_write)
            self.assertEqual(1, len(writer.calls))
            self.assertTrue(writer.calls[0][2].startswith("v1:sha256:"))

    def test_unmatched_identity_does_not_block_anonymous_projection(self):
        def unmatched_identity(request, start, end):
            token = f"store:cam:{request.analysis_run_id}:23"
            return OfflineIdentityPassResult(
                recognition_coverage_status="complete",
                associations={token: OfflineTrackIdentityAssociation(
                    analysis_run_id=request.analysis_run_id,
                    analysis_job_id=request.analysis_job_id,
                    store_id=request.store_id,
                    camera_id=request.camera_id,
                    global_track_id=23,
                    track_token=token,
                    status="unmatched",
                    member_id=None,
                    candidate_member_id=None,
                    valid_from=start,
                    valid_to=end,
                    evidence_ids=(),
                    method="spatial",
                    confidence=None,
                )},
                recognition_acceptance_policy_snapshot={
                    "policy_id": "p6s-acceptance",
                    "policy_version": "v1",
                    "similarity_threshold": "0.75",
                    "policy_hash": "v1:sha256:" + "b" * 64,
                },
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            writer = _ProjectionSpy(root)
            result = self._pipeline(
                root,
                identity_pass=unmatched_identity,
                product_analytics_writer=writer,
                product_analytics_enabled=True,
            ).run(
                request=self._request(video, origin="p6s_recording_file"),
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertEqual("completed", result.product_analytics_projection_status)
            self.assertEqual(1, len(writer.calls))

    def test_projection_scope_and_time_fail_closed_after_seal(self):
        def wrong_camera(request, started_at):
            behavior = self._behavior_pass(request, started_at)
            behavior.raw_stage9_events[0].camera_id = "cam-other"
            return behavior

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            intervals = (HistoricalRegistrationInterval(
                self.utc_start - timedelta(seconds=1),
                self.utc_start + timedelta(minutes=2), "cam",
            ),)
            stores = (HistoricalRegistrationInterval(
                self.utc_start - timedelta(seconds=1),
                self.utc_start + timedelta(minutes=2), "store",
            ),)

            scope_writer = _ProjectionSpy(root / "scope")
            scope_result = self._pipeline(
                root / "scope",
                behavior_analyzer=wrong_camera,
                product_analytics_writer=scope_writer,
                product_analytics_enabled=True,
            ).run(
                request=self._request(video, origin="p6s_recording_file"),
                device_camera_intervals=intervals,
                camera_store_intervals=stores,
            )
            self.assertEqual("not_eligible", scope_result.product_analytics_projection_status)
            self.assertEqual(
                "analytics_scope_contract_invalid",
                scope_result.product_analytics_projection_reason,
            )
            self.assertEqual([], scope_writer.calls)

            time_writer = _ProjectionSpy(root / "time")
            time_result = self._pipeline(
                root / "time",
                recording_time_provider=lambda _request: None,
                product_analytics_writer=time_writer,
                product_analytics_enabled=True,
            ).run(request=self._request(video, origin="p6s_recording_file"))
            self.assertEqual("not_eligible", time_result.product_analytics_projection_status)
            self.assertEqual(
                "analytics_authoritative_time_unavailable",
                time_result.product_analytics_projection_reason,
            )
            self.assertEqual([], time_writer.calls)

    def test_projection_failure_is_distinct_from_formal_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            writer = _ProjectionSpy(root, fail=True)
            result = self._pipeline(
                root,
                product_analytics_writer=writer,
                product_analytics_enabled=True,
            ).run(
                request=self._request(video, origin="p6s_recording_file"),
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1),
                    self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertEqual("failed", result.product_analytics_projection_status)
            self.assertEqual(
                "product_analytics_projection_failed",
                result.product_analytics_projection_reason,
            )
            self.assertEqual("published", result.publication_status)
            db = self.sessions()
            try:
                self.assertEqual(1, db.query(BehaviorEvent).count())
            finally:
                db.close()

    def test_untrusted_import_remains_artifact_only_and_never_publishes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "import.mp4"
            video.write_bytes(b"external")
            request = self._request(video, origin="untrusted_import")

            result = self._pipeline(root).run(request=request)

            db = self.sessions()
            try:
                self.assertFalse(result.publication_eligible)
                self.assertEqual("untrusted", result.source_video_integrity_status)
                self.assertEqual("unavailable", result.identity_status)
                self.assertIsNone(result.publication)
                self.assertEqual(0, db.query(BehaviorEvent).count())
                self.assertEqual(0, db.query(OfflineBehaviorPublication).count())
                self.assertIn(
                    b"ARTIFACT ONLY",
                    (Path(result.artifact_directory) / "analyzed_video.mp4").read_bytes(),
                )
            finally:
                db.close()

    def test_hashed_golden_fixture_seals_artifact_only_without_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "golden.mp4"
            video.write_bytes(b"golden-fixture")
            request = self._request(video, origin="untrusted_import")
            content_hash = hashlib.sha256(video.read_bytes()).hexdigest()

            def fixture_source(_request):
                return TrustedSourceVideoResult(
                    "untrusted_test_fixture",
                    "fixture:v1:sha256:" + content_hash,
                    content_hash,
                    video.stat().st_size,
                    video.stat().st_size,
                    "golden_fixture_not_p6s_recording",
                    {"source_type": "golden_test_fixture"},
                )

            result = self._pipeline(
                root,
                trusted_source_provider=fixture_source,
            ).run(request=request)

            db = self.sessions()
            try:
                self.assertFalse(result.publication_eligible)
                self.assertEqual("untrusted_test_fixture", result.source_video_integrity_status)
                self.assertEqual("not_published", result.publication_status)
                self.assertTrue(result.artifact_manifest_hash.startswith("v1:sha256:"))
                run_dir = Path(result.artifact_directory)
                self.assertTrue((run_dir / "artifact_manifest.json").is_file())
                self.assertTrue((run_dir / "analyzed_video.mp4").is_file())
                self.assertEqual(0, db.query(BehaviorEvent).count())
                self.assertEqual(0, db.query(OfflineBehaviorPublication).count())
                self.assertEqual(
                    "untrusted_test_fixture",
                    json.loads((run_dir / "source_manifest.json").read_text())[
                        "source_video_integrity_status"
                    ],
                )
            finally:
                db.close()

    def test_degraded_fixture_seals_relative_time_and_unknown_segment_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "golden.mp4"
            video.write_bytes(b"golden-fixture")
            request = self._request(video, origin="untrusted_import")
            content_hash = hashlib.sha256(video.read_bytes()).hexdigest()

            def fixture_source(_request):
                return TrustedSourceVideoResult(
                    "untrusted_test_fixture",
                    "fixture:v1:sha256:" + content_hash,
                    content_hash,
                    video.stat().st_size,
                    video.stat().st_size,
                    "golden_fixture_not_p6s_recording",
                    {"source_type": "golden_test_fixture"},
                )

            def degraded_behavior(analysis_request, started_at):
                behavior = self._behavior_pass(analysis_request, started_at)
                event = behavior.raw_stage9_events[0]
                event.source_pts_seconds = 7.6
                return replace(
                    behavior,
                    person_tracks=(SimpleNamespace(
                        track_token=event.person_track_token,
                        global_track_id=event.person_track_id,
                        observed_at=self.utc_start,
                        role="customer",
                        sample_seq=0,
                        source_seq=1,
                    ),),
                )

            result = self._pipeline(
                root,
                trusted_source_provider=fixture_source,
                behavior_analyzer=degraded_behavior,
                segment_duration_assessor=lambda _request, _behavior: SegmentDurationAssessment(
                    "unavailable", None, 23.2, None, None,
                    "fixture_not_device_recording_segment",
                    media_duration_source="last_presentation_frame_end",
                    presentation_timeline_status="verified",
                ),
            ).run(request=request)

            run_dir = Path(result.artifact_directory)
            fact = json.loads(
                (run_dir / "normalized_behavior_facts.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual((None, None, 7.6, 7.6), (
                fact["started_at"], fact["ended_at"],
                fact["relative_started_at_seconds"],
                fact["relative_ended_at_seconds"],
            ))
            metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
            source = json.loads((run_dir / "source_manifest.json").read_text(encoding="utf-8"))
            expected_duration = {
                "duration_delta_seconds": None,
                "index_duration_seconds": None,
                "index_duration_source": None,
                "media_duration_seconds": 23.2,
                "media_duration_source": "last_presentation_frame_end",
                "policy_id": None,
                "policy_version": None,
                "presentation_timeline_status": "verified",
                "profile_content_hash": None,
                "profile_id": None,
                "profile_version": None,
                "reason": "segment_duration_assessment_untrusted",
                "status": "unavailable",
                "tolerance_seconds": None,
            }
            self.assertEqual(expected_duration, metadata["segment_duration_assessment"])
            self.assertEqual(expected_duration, source["segment_duration_assessment"])
            association_text = (
                run_dir / "track_identity_associations.jsonl"
            ).read_text(encoding="utf-8")
            association = json.loads(association_text)
            self.assertEqual((
                request.analysis_job_id,
                request.analysis_run_id,
                request.store_id,
                request.camera_id,
                23,
            ), (
                association["analysis_job_id"],
                association["analysis_run_id"],
                association["store_id"],
                association["camera_id"],
                association["global_track_id"],
            ))
            self.assertEqual((None, None, 7.6, 7.6), (
                association["valid_from"],
                association["valid_to"],
                association["relative_valid_from_seconds"],
                association["relative_valid_to_seconds"],
            ))
            self.assertNotIn("1970-01-01", association_text)
            self.assertNotIn("2000-01-01", association_text)
            manifest = json.loads((run_dir / "artifact_manifest.json").read_text())
            recalculated = "v1:sha256:" + hashlib.sha256(
                OfflineArtifactManifestCanonicalizerV1().canonical_bytes(manifest["body"])
            ).hexdigest()
            self.assertEqual(manifest["artifact_manifest_hash"], recalculated)

    def test_invalid_identity_pass_contract_fails_closed_to_anonymous_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            pipeline = self._pipeline(
                root,
                provider_status="available",
                identity_pass=lambda _request, _start, _end: {"coverage": "complete"},
            )

            result = pipeline.run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertTrue(result.publication_eligible)
            self.assertEqual("unavailable", result.identity_status)
            self.assertEqual("published", result.publication.status)
            association_path = Path(result.artifact_directory) / "track_identity_associations.jsonl"
            association = json.loads(association_path.read_text(encoding="utf-8"))
            self.assertEqual("unavailable", association["status"])
            self.assertEqual("identity_pass_contract_invalid", association["diagnostics"][0]["reason"])

    def test_identity_pass_error_fails_closed_to_anonymous_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            pipeline = self._pipeline(
                root,
                provider_status="available",
                identity_pass=lambda _request, _start, _end: (_ for _ in ()).throw(
                    RuntimeError("provider temporary failure")
                ),
            )

            result = pipeline.run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertTrue(result.publication_eligible)
            self.assertEqual("unavailable", result.identity_status)
            self.assertEqual("published", result.publication.status)
            association = json.loads(
                (Path(result.artifact_directory) / "track_identity_associations.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("identity_pass_failed", association["diagnostics"][0]["reason"])

    def test_b28_trusted_gate_blocks_missing_coverage_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            result = self._pipeline(
                root,
                trusted_publication_gate=TrustedOfflinePublicationGate(),
                identity_pass=lambda _request, _start, _end: OfflineIdentityPassResult(
                    recognition_coverage_status="unavailable",
                    associations={},
                    failure_reason="push_coverage_unavailable",
                    presentation_timeline_status="verified",
                ),
            ).run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertFalse(result.publication_eligible)
            self.assertEqual("push_coverage_unavailable", result.publication_block_reason)
            self.assertEqual("not_published", result.publication_status)
            self.assertIsNotNone(result.artifact_manifest_hash)
            db = self.sessions()
            try:
                self.assertEqual(0, db.query(BehaviorEvent).count())
            finally:
                db.close()

    def test_identity_pass_audit_records_are_written_to_restricted_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            pipeline = self._pipeline(
                root,
                provider_status="available",
                identity_pass=lambda _request, _start, _end: OfflineIdentityPassResult(
                    recognition_coverage_status="complete",
                    associations={},
                    accepted_evidence=({"canonical_event_id": "evt-1"},),
                    record_audits=({"canonical_event_id": "evt-1", "status": "accepted"},),
                    page_audits=({"page_num": 1, "eof_proven": True},),
                    recognition_acceptance_policy_snapshot={
                        "policy_id": "p6s-acceptance",
                        "policy_version": "v1",
                        "similarity_threshold": "0.75",
                        "policy_hash": "v1:sha256:" + "b" * 64,
                    },
                ),
            )

            result = pipeline.run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            records = [
                json.loads(line)
                for line in (Path(result.artifact_directory) / "identity_evidence.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            record_types = {record["record_type"] for record in records}
            self.assertTrue(result.publication_eligible)
            self.assertEqual(
                {
                    "recognition_acceptance_policy_snapshot",
                    "recognition_page_audit",
                    "recognition_record_audit",
                    "accepted_recognition_evidence",
                },
                record_types,
            )

    def test_person_track_without_stage9_fact_gets_unavailable_identity_association(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            pipeline = self._pipeline(root, behavior_analyzer=self._track_only_behavior_pass)

            result = pipeline.run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            association_path = Path(result.artifact_directory) / "track_identity_associations.jsonl"
            association = json.loads(association_path.read_text(encoding="utf-8"))
            self.assertEqual(
                f"store:cam:{request.analysis_run_id}:31", association["track_token"]
            )
            self.assertEqual("unavailable", association["status"])
            self.assertEqual(self.utc_start, datetime.fromisoformat(association["valid_from"]))
            self.assertEqual(self.utc_start + timedelta(seconds=10), datetime.fromisoformat(association["valid_to"]))
            self.assertIsNone(association["relative_valid_from_seconds"])
            self.assertIsNone(association["relative_valid_to_seconds"])

    def test_sealed_eligible_run_without_publisher_is_explicitly_not_published(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            pipeline = self._pipeline(root, publisher=False)

            result = pipeline.run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertTrue(result.publication_eligible)
            self.assertIsNone(result.publication)
            self.assertEqual("not_published", result.publication_status)

    def test_foundation_provider_errors_and_invalid_time_contract_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")

            source_failure = self._pipeline(
                root / "source-failure",
                trusted_source_provider=lambda _request: (_ for _ in ()).throw(
                    RuntimeError("source adapter failed")
                ),
            ).run(request=request)
            invalid_time = self._pipeline(
                root / "invalid-time",
                recording_time_provider=lambda _request: SimpleNamespace(
                    timezone_verified=True,
                    timezone_offset_minutes=480,
                    timezone_source="device",
                    event_time_standard="utc_naive",
                    raw_started_at=self.raw_start,
                    raw_ended_at=self.raw_start + timedelta(minutes=1),
                    started_at_utc="not-a-datetime",
                    ended_at_utc="not-a-datetime",
                ),
            ).run(request=request)

            self.assertEqual("unavailable", source_failure.source_video_integrity_status)
            self.assertFalse(source_failure.publication_eligible)
            self.assertEqual("unavailable", invalid_time.absolute_event_time_status)
            self.assertFalse(invalid_time.publication_eligible)

    def test_caller_cannot_force_segment_duration_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")

            result = self._pipeline(root, provider_status="unavailable").run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertEqual("compatible", request.segment_duration_status)
            self.assertEqual("unavailable", result.segment_duration_status)
            self.assertFalse(result.publication_eligible)

    def test_camera_scope_mismatch_blocks_formal_publication(self):
        def wrong_camera(request, started_at):
            result = self._behavior_pass(request, started_at)
            result.raw_stage9_events[0].camera_id = "cam-other"
            return result

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            result = self._pipeline(root, behavior_analyzer=wrong_camera).run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertFalse(result.publication_eligible)
            self.assertEqual("normalization_contract_conflict", result.publication_block_reason)
            db = self.sessions()
            try:
                self.assertEqual(0, db.query(BehaviorEvent).count())
            finally:
                db.close()

    def test_wrong_run_confirmed_association_cannot_display_or_publish_member(self):
        captured_labels = {}

        def renderer(behavior, labels, *, artifact_only):
            captured_labels.update(labels)
            return OfflineOverlayRenderResult(
                behavior.analyzed_video_bytes + b" ARTIFACT ONLY",
                artifact_only_marked=True,
            )

        def identity_pass(request, start, end):
            wrong_token = f"store:cam:other-run:23"
            return OfflineIdentityPassResult(
                recognition_coverage_status="complete",
                associations={wrong_token: OfflineTrackIdentityAssociation(
                    analysis_run_id="other-run",
                    track_token=wrong_token,
                    status="confirmed",
                    member_id="member-a",
                    candidate_member_id=None,
                    valid_from=start,
                    valid_to=end,
                    evidence_ids=("evidence-1",),
                    method="spatial",
                    confidence=0.9,
                )},
                recognition_acceptance_policy_snapshot={
                    "policy_id": "p6s-acceptance",
                    "policy_version": "v1",
                    "similarity_threshold": "0.75",
                    "policy_hash": "v1:sha256:" + "b" * 64,
                },
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "source.mp4"
            video.write_bytes(b"p6s-original")
            request = self._request(video, origin="p6s_recording_file")
            result = self._pipeline(
                root,
                identity_pass=identity_pass,
                overlay_renderer=renderer,
            ).run(
                request=request,
                device_camera_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "cam",
                ),),
                camera_store_intervals=(HistoricalRegistrationInterval(
                    self.utc_start - timedelta(seconds=1), self.utc_start + timedelta(minutes=2), "store",
                ),),
            )

            self.assertFalse(result.publication_eligible)
            self.assertEqual("identity_contract_conflict", result.publication_block_reason)
            self.assertFalse(any("member-a" in label for label in captured_labels.values()))


    def _pipeline(
        self,
        root,
        *,
        provider_status="available",
        identity_pass=None,
        publisher=True,
        behavior_analyzer=None,
        trusted_source_provider=None,
        recording_time_provider=None,
        overlay_renderer=None,
        segment_duration_assessor=None,
        trusted_publication_gate=None,
        product_analytics_writer=None,
        product_analytics_enabled=False,
    ):
        store = OfflineRunArtifactStore(
            root / "analysis_results",
            security_adapter=StaticArtifactStorageSecurityAdapter(),
            known_public_roots=(), media_probe=lambda path: path.stat().st_size > 0,
        )
        profile_resolution = (
            self.profile_resolution
            if provider_status == "available"
            else CapabilityProfileResolution(
                "unavailable", None, "real_device_not_verified"
            )
        )
        return OfflineBehaviorPipeline(
            artifact_store=store, behavior_analyzer=behavior_analyzer or self._behavior_pass,
            trusted_source_provider=trusted_source_provider or self._trusted_source,
            recording_time_provider=recording_time_provider or self._recording_time,
            provider_a_profile=profile_resolution,
            segment_duration_assessor=segment_duration_assessor or (
                lambda request, _behavior: assess_segment_duration(
                    device_id=request.device_id,
                    index_duration_seconds="60",
                    media_duration_seconds="60",
                    source_frame_starts=("0", "59.8"),
                    profile_resolution=profile_resolution,
                    presentation_timeline_status="verified",
                )
            ),
            fact_normalizer=Stage9BehaviorFactAdapter(),
            identity_projector=OfflineBehaviorIdentityProjector(),
            time_publication_gate=OfflineBehaviorTimePublicationGate(),
            registration_validator=RegistrationScopeValidator(),
            overlay_renderer=overlay_renderer or (
                lambda behavior, labels, *, artifact_only: OfflineOverlayRenderResult(
                    behavior.analyzed_video_bytes + (b" ARTIFACT ONLY" if artifact_only else b""),
                    artifact_only_marked=artifact_only,
                )
            ),
            identity_pass=identity_pass,
            publisher=(
                OfflineBehaviorPublisher(
                    publication_repository=SqlAlchemyOfflinePublicationRepository(
                        self.sessions, BehaviorEventStore(self.sessions)
                    ),
                ) if publisher else None
            ),
            trusted_publication_gate=trusted_publication_gate,
            product_analytics_writer=product_analytics_writer,
            product_analytics_enabled=product_analytics_enabled,
        )

    def _trusted_source(self, request):
        if request.recording is None:
            return TrustedSourceVideoResult(
                "untrusted", None, None, 0, None, "untrusted_test_import", None
            )
        verifier = TrustedSourceVideoVerifier()
        verified = verifier.verify(
            recording=request.recording,
            downloaded_path=request.video_path,
            controlled_download=request.controlled_download,
        )
        return verifier.verify_analysis_input(
            trusted_source=verified, analysis_input_path=request.video_path
        )

    def _recording_time(self, request):
        if request.recording is None:
            return None
        return OfflineRecordingTimeNormalizer().normalize(
            raw_started_at=request.recording.raw_started_at,
            raw_ended_at=request.recording.raw_ended_at,
            timezone_offset_minutes=480,
            timezone_source="test_device_registration",
            timezone_verified=True,
        )

    def _request(self, video, *, origin):
        recording = P6SRecordingSource(
            device_id="device", path_name="records", file_name="source.mp4",
            raw_started_at=self.raw_start, raw_ended_at=self.raw_start + timedelta(minutes=1),
            indexed_size_bytes=video.stat().st_size,
        )
        return OfflineVideoAnalysisRequest(
            analysis_job_id=f"job-{origin}", analysis_run_id=f"run-{origin}",
            video_path=video, store_id="store", camera_id="cam", device_id="device",
            source_origin=origin, recording=recording if origin == "p6s_recording_file" else None,
            controlled_download=origin == "p6s_recording_file", timezone_offset_minutes=480,
            timezone_source="device_registration", timezone_verified=True,
            segment_duration_status="compatible", provider_a_status="unavailable",
        )

    def _behavior_pass(self, request, video_started_at):
        session = "session-1"
        keyframe = "keyframes/" + hashlib.sha256(session.encode()).hexdigest()[:24] + "/pick_up.jpg"
        event = SimpleNamespace(
            event_id="raw-pickup", event_type="PICK_UP", observed_at=self.utc_start,
            source_seq=1, camera_id="cam", product_key="sku", product_name="Product",
            product_track_token=f"product:cam:{request.analysis_run_id}:1", person_track_id=23,
            person_track_token=f"store:cam:{request.analysis_run_id}:23", interaction_session_id=session,
            confidence=0.9, holding_duration_seconds=None,
        )
        return OfflineBehaviorPass(
            media_timeline_status="available", raw_stage9_events=(event,),
            person_tracks=(), product_tracks=(), identity_evidence=(),
            track_identity_associations={}, member_enrichments={}, behavior_summary={},
            analyzed_video_bytes=b"final-video", keyframes={keyframe: b"keyframe"},
        )

    def _track_only_behavior_pass(self, request, video_started_at):
        return OfflineBehaviorPass(
            media_timeline_status="available", raw_stage9_events=(),
            person_tracks=(SimpleNamespace(
                track_token=f"store:cam:{request.analysis_run_id}:31",
                observed_at=self.utc_start,
                valid_to=self.utc_start + timedelta(seconds=10),
                role="customer",
                sample_seq=0,
                source_seq=1,
            ),),
            product_tracks=(), identity_evidence=(),
            track_identity_associations={}, member_enrichments={}, behavior_summary={},
            analyzed_video_bytes=b"final-video", keyframes={},
        )

    @staticmethod
    def _resolved_profile():
        profile = P6SCapabilityProfile.create(
            profile_id="p6s-test-contract",
            profile_version="v1",
            trust_class="production_verified",
            status="verified",
            device_model="P6S-Test",
            firmware_versions=("1.0",),
            api_versions=("v1",),
            pagination_completion_mode="verified_short_page",
            pagination_policy_version="paging-v1",
            pagination_validation_report_ref="test:paging-contract",
            segment_duration_policy_id="duration-p6s-test",
            segment_duration_policy_version="duration-v1",
            segment_duration_tolerance_seconds="0.25",
            segment_duration_validation_report_ref="test:duration-contract",
            historical_mapping_capability_status="available",
            historical_mapping_validation_report_ref="test:history-contract",
            timezone_interpretation_mode="verified_device_offset",
            timezone_validation_report_ref="test:timezone-contract",
        )
        return P6SCapabilityProfileResolver((profile,)).resolve(
            device_id="device",
            device_model="P6S-Test",
            firmware_version="1.0",
            api_version="v1",
            fingerprint_source="test_verified_device_metadata",
            fingerprint_verified=True,
        )


if __name__ == "__main__":
    unittest.main()
