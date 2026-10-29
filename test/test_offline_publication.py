from __future__ import annotations

import tempfile
import unittest
import json
import stat
from datetime import datetime
from pathlib import Path

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.models import Base, BehaviorEvent, EvidenceClip, OfflineBehaviorPublication  # noqa: E402
from agent.offline_artifacts import (  # noqa: E402
    OfflineRunArtifactStore,
    StaticArtifactStorageSecurityAdapter,
)
from agent.offline_behavior import (  # noqa: E402
    OfflineBehaviorEventCandidate,
    PreparedOfflineBehaviorPublication,
)
from agent.offline_publication import (  # noqa: E402
    OfflineBehaviorPublisher,
    OfflineBehaviorTimeAuthority,
    TrustedOfflinePublicationGate,
)
from agent.offline_publication_sql import SqlAlchemyOfflinePublicationRepository  # noqa: E402
from agent.behavior_events import BehaviorEventStore  # noqa: E402
from agent.schema_evolution import evolve_schema  # noqa: E402


class OfflinePublicationContractTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_disposable_schema_evolution_creates_only_the_authorized_offline_tables(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            evolve_schema(engine)
            tables = set(inspect(engine).get_table_names())
            self.assertIn("offline_behavior_publications", tables)
            self.assertIn("device_member_identity_revisions", tables)
            self.assertNotIn("offline_analysis_jobs", tables)
            self.assertNotIn("offline_track_identity_associations", tables)
        finally:
            engine.dispose()

    def test_publish_is_atomic_idempotent_and_does_not_create_evidence_or_visits(self):
        with tempfile.TemporaryDirectory() as root:
            artifacts = self._sealed(Path(root), "job", "run")
            prepared = self._prepared("job", "run", "video", artifacts[0].artifact_manifest_hash)
            publisher = self._publisher(clock=lambda: datetime(2026, 8, 17, 3))

            first = publisher.publish(prepared=prepared, artifacts=artifacts[0], artifact_store=artifacts[1])
            retry = publisher.publish(prepared=prepared, artifacts=artifacts[0], artifact_store=artifacts[1])

            db = self.sessions()
            try:
                event = db.query(BehaviorEvent).one()
                self.assertEqual("published", first.status)
                self.assertEqual("already_published_same_manifest", retry.status)
                self.assertEqual(1, db.query(OfflineBehaviorPublication).count())
                self.assertEqual(1, db.query(BehaviorEvent).count())
                self.assertEqual(0, db.query(EvidenceClip).count())
                self.assertIsNone(event.evidence_clip_id)
                self.assertIsNone(event.visit_id)
                self.assertIsNone(event.entry_event_id)
                self.assertEqual("member-a", event.member_id)
                self.assertEqual("store", event.store_id)
                self.assertIsNone(event.duration_seconds)
                self.assertEqual(prepared.events[0].occurred_at, event.occurred_at)
                self.assertEqual(prepared.events[0].identity_source, event.identity_source)
                self.assertEqual(prepared.events[0].identity_binding_level, event.identity_binding_level)
                self.assertEqual(prepared.events[0].details_for_db(), event.details)
            finally:
                db.close()

    def test_existing_job_with_different_run_is_conflict_and_event_count_does_not_grow(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            first_artifacts = self._sealed(Path(root), "job", "run-a", store=store)
            second_artifacts = self._sealed(Path(root), "job", "run-b", store=store)
            publisher = self._publisher()
            publisher.publish(
                prepared=self._prepared("job", "run-a", "video", first_artifacts[0].artifact_manifest_hash),
                artifacts=first_artifacts[0], artifact_store=store,
            )

            result = publisher.publish(
                prepared=self._prepared("job", "run-b", "video", second_artifacts[0].artifact_manifest_hash),
                artifacts=second_artifacts[0], artifact_store=store,
            )

            db = self.sessions()
            try:
                self.assertEqual("already_published_conflict", result.status)
                self.assertEqual(1, db.query(BehaviorEvent).count())
            finally:
                db.close()

    def test_unsealed_or_unverified_artifact_blocks_publication_before_db_writes(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(writer)
            sealed = writer.seal(source_video_id="video", source_content_sha256="a" * 64, normalized_facts=())
            prepared = self._prepared("job", "run", "video", sealed.artifact_manifest_hash)
            metadata_path = sealed.run_directory / "metadata.json"
            metadata_path.chmod(stat.S_IWRITE | stat.S_IREAD)
            metadata_path.write_text("changed", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "artifact_integrity"):
                self._publisher().publish(
                    prepared=prepared, artifacts=sealed, artifact_store=store
                )

            db = self.sessions()
            try:
                self.assertEqual(0, db.query(OfflineBehaviorPublication).count())
                self.assertEqual(0, db.query(BehaviorEvent).count())
            finally:
                db.close()

    def test_zero_event_run_creates_the_job_fence(self):
        with tempfile.TemporaryDirectory() as root:
            artifacts = self._sealed(Path(root), "job", "run")
            prepared = PreparedOfflineBehaviorPublication.create(
                analysis_job_id="job", analysis_run_id="run", source_video_id="video", events=(),
            )

            result = self._publisher().publish(
                prepared=prepared, artifacts=artifacts[0], artifact_store=artifacts[1]
            )

            self.assertEqual(("published", 0), (result.status, result.event_count))

    def test_failure_after_fence_insert_rolls_back_the_whole_batch(self):
        class FailingStore:
            def bulk_insert_behavior(self, *, session, events):
                raise RuntimeError("simulated_crash")

        with tempfile.TemporaryDirectory() as root:
            artifacts = self._sealed(Path(root), "job", "run")
            prepared = self._prepared("job", "run", "video", artifacts[0].artifact_manifest_hash)
            with self.assertRaisesRegex(RuntimeError, "simulated_crash"):
                self._publisher(behavior_event_store=FailingStore()).publish(
                    prepared=prepared, artifacts=artifacts[0], artifact_store=artifacts[1]
                )

            db = self.sessions()
            try:
                self.assertEqual(0, db.query(OfflineBehaviorPublication).count())
                self.assertEqual(0, db.query(BehaviorEvent).count())
            finally:
                db.close()

    def test_manifest_uses_fully_prepared_immutable_payload(self):
        candidate = OfflineBehaviorEventCandidate(
            event_key="offline:v1:holding", event_type="merchandise_holding",
            assessment="confirmed", store_id="store", camera_id="cam",
            customer_track_id=23, customer_track_token="store:cam:run:23",
            started_at=datetime(2026, 8, 17, 2),
            ended_at=datetime(2026, 8, 17, 2, 0, 4), confidence=0.9,
            member_id="member-a", member_tier=" Gold ", identity_status="confirmed",
            identity_method="spatial", identity_confidence=0.9,
            details={"analysis_job_id": "job", "nested": {"value": 1}},
        )
        prepared = PreparedOfflineBehaviorPublication.create(
            analysis_job_id="job", analysis_run_id="run", source_video_id="video",
            events=(candidate,),
        )
        payload = json.loads(prepared.canonical_manifest_bytes)["events"][0]

        candidate.details["nested"]["value"] = 2

        row = prepared.events[0]
        self.assertEqual("gold", row.member_tier)
        self.assertEqual(4.0, row.duration_seconds)
        self.assertEqual(1, payload["details"]["nested"]["value"])
        self.assertEqual(1, row.details["nested"]["value"])
        self.assertEqual(row.event_key, payload["event_key"])
        self.assertEqual(row.identity_source, payload["identity_source"])

    def test_point_events_persist_null_duration_and_holding_persists_interval(self):
        with tempfile.TemporaryDirectory() as root:
            artifacts, store = self._sealed(Path(root), "job-duration", "run")
            start = datetime(2026, 8, 17, 2)
            end = start.replace(second=4)
            events = []
            for event_type, started_at, ended_at in (
                ("merchandise_pick_up", start, start),
                ("merchandise_holding", start, end),
                ("merchandise_put_down", end, end),
            ):
                events.append(OfflineBehaviorEventCandidate(
                    event_key=f"offline:v1:{event_type}", event_type=event_type,
                    assessment="confirmed", store_id="store", camera_id="cam",
                    customer_track_id=23, customer_track_token="store:cam:run:23",
                    started_at=started_at, ended_at=ended_at, confidence=0.9,
                    member_id=None, member_tier=None, identity_status="unmatched",
                    identity_method=None, identity_confidence=None,
                    details={
                        "analysis_job_id": "job-duration",
                        "analysis_run_id": "run",
                        "source_video_id": "video",
                        "offline_artifact": {
                            "artifact_manifest_hash": artifacts.artifact_manifest_hash,
                        },
                    },
                ))
            prepared = PreparedOfflineBehaviorPublication.create(
                analysis_job_id="job-duration", analysis_run_id="run",
                source_video_id="video", events=events,
            )

            self._publisher().publish(
                prepared=prepared, artifacts=artifacts, artifact_store=store
            )
            db = self.sessions()
            try:
                rows = {
                    row.event_type: row
                    for row in db.query(BehaviorEvent).all()
                }
                self.assertIsNone(rows["merchandise_pick_up"].duration_seconds)
                self.assertIsNone(rows["merchandise_put_down"].duration_seconds)
                self.assertEqual(4.0, rows["merchandise_holding"].duration_seconds)
            finally:
                db.close()

    def test_b28_trusted_gate_requires_every_dynamic_identity_gate_and_confirmed_track(self):
        gate = TrustedOfflinePublicationGate()
        profile = type("ResolvedProfile", (), {
            "production_eligibility_reason": lambda _self, *, device_id: (
                None if device_id == "cam02" else "profile_device_scope_mismatch"
            ),
        })()
        confirmed = {"store-1:cam02:run:1": type("Association", (), {"status": "confirmed"})()}
        values = {
            "profile_resolution": profile,
            "device_id": "cam02",
            "source_status": "verified",
            "registration_status": "available",
            "segment_duration_status": "compatible",
            "presentation_timeline_status": "verified",
            "time_authority": OfflineBehaviorTimeAuthority(
                "available", "available", "authoritative", "confirmed", True, None,
            ),
            "recognition_coverage_status": "complete",
            "historical_identity_status": "resolved",
            "guarded_event_time_status": "verified",
            "runtime_face_geometry_status": "compatible",
            "behavior_track_tokens": ("store-1:cam02:run:1",),
            "associations": confirmed,
        }
        self.assertEqual("verified", gate.assess(**values).status)

        cases = {
            "profile": ({"profile_resolution": None}, "capability_profile_unavailable"),
            "source": ({"source_status": "unavailable"}, "source_integrity_unavailable"),
            "registration": ({"registration_status": "unavailable"}, "registration_scope_unavailable"),
            "pts": ({"presentation_timeline_status": "unavailable"}, "presentation_timeline_unavailable"),
            "coverage": ({"recognition_coverage_status": "unavailable"}, "push_coverage_unavailable"),
            "historical": ({"historical_identity_status": "unavailable"}, "historical_identity_unavailable"),
            "guard": ({"guarded_event_time_status": "unavailable"}, "guarded_event_time_unavailable"),
            "geometry": ({"runtime_face_geometry_status": "unavailable"}, "runtime_face_geometry_unavailable"),
            "probable": ({"associations": {"store-1:cam02:run:1": type("Association", (), {"status": "probable"})()}}, "track_identity_not_confirmed"),
            "conflict": ({"associations": {"store-1:cam02:run:1": type("Association", (), {"status": "conflict"})()}}, "track_identity_not_confirmed"),
        }
        for name, (updates, expected_reason) in cases.items():
            with self.subTest(name=name):
                current = dict(values)
                current.update(updates)
                result = gate.assess(**current)
                self.assertEqual(("unavailable", expected_reason), (result.status, result.reason))

    def _prepared(self, job, run, source, artifact_manifest_hash):
        candidate = OfflineBehaviorEventCandidate(
            event_key=f"offline:v1:{run}", event_type="merchandise_pick_up", assessment="confirmed",
            store_id="store", camera_id="cam", customer_track_id=23,
            customer_track_token=f"store:cam:{run}:23", started_at=datetime(2026, 8, 17, 2),
            ended_at=datetime(2026, 8, 17, 2), confidence=0.9, member_id="member-a",
            member_tier=None, identity_status="confirmed", identity_method="spatial",
            identity_confidence=0.9,
            details={"analysis_job_id": job, "analysis_run_id": run, "source_video_id": source,
                     "offline_artifact": {"artifact_manifest_hash": artifact_manifest_hash}},
        )
        return PreparedOfflineBehaviorPublication.create(
            analysis_job_id=job, analysis_run_id=run, source_video_id=source, events=(candidate,),
        )

    def _publisher(self, *, behavior_event_store=None, clock=None):
        behavior_event_store = behavior_event_store or BehaviorEventStore(self.sessions)
        return OfflineBehaviorPublisher(
            publication_repository=SqlAlchemyOfflinePublicationRepository(
                self.sessions, behavior_event_store
            ),
            **({} if clock is None else {"clock": clock}),
        )

    def _sealed(self, root, job, run, store=None):
        store = store or self._store(root)
        writer = store.begin(analysis_job_id=job, analysis_run_id=run)
        self._write_required(writer)
        return writer.seal(source_video_id="video", source_content_sha256="a" * 64, normalized_facts=()), store

    @staticmethod
    def _store(root):
        return OfflineRunArtifactStore(
            root, security_adapter=StaticArtifactStorageSecurityAdapter(),
            known_public_roots=(),
            media_probe=lambda path: path.stat().st_size > 0,
        )

    @staticmethod
    def _write_required(writer):
        writer.write_json("metadata.json", {
            "analysis_job_id": writer.analysis_job_id,
            "analysis_run_id": writer.analysis_run_id,
            "store_id": "store",
            "camera_id": "cam",
            "event_time_standard": "utc_naive",
            "media_timeline_status": "available",
            "behavior_time_quality": "authoritative",
        })
        writer.write_json("source_manifest.json", {
            "source_video_id": "video",
            "source_content_sha256": "a" * 64,
            "source_video_integrity_status": "verified",
        })
        for name in (
            "person_tracks.jsonl", "product_tracks.jsonl", "identity_evidence.jsonl",
            "track_identity_associations.jsonl", "raw_stage9_events.jsonl",
            "normalized_behavior_facts.jsonl",
        ):
            writer.write_jsonl(name, ())
        writer.write_json("behavior_summary.json", {
            "summary_version": "offline_behavior_summary_v1",
            "event_count": 0,
            "events_by_type": {},
            "events_by_confirmed_member_id": {},
            "publication_eligible": True,
        })
        writer.write_bytes("analyzed_video.mp4", b"finalized-mp4")


if __name__ == "__main__":
    unittest.main()
