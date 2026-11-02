from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from agent.behavior.behavior_events import BehaviorEventStore, TrafficQueryService  # noqa: E402
from agent.models import Base, BehaviorEvent, OfflineBehaviorPublication  # noqa: E402
from agent.offline.offline_artifacts import (  # noqa: E402
    OfflineArtifactError,
    OfflineRunArtifactStore,
    StaticArtifactStorageSecurityAdapter,
)
from agent.offline.offline_behavior import (  # noqa: E402
    MemberProfileEnrichment,
    OfflineBehaviorCanonicalizerV1,
    OfflineBehaviorIdentityProjector,
    OfflineDisplayIdentity,
    OfflinePersonDisplayIdentityResolver,
    PreparedOfflineBehaviorPublication,
    PreparedOfflineBehaviorRow,
    Stage9BehaviorFactAdapter,
)
from agent.offline.offline_identity import OfflineTrackIdentityAssociation  # noqa: E402
from agent.offline.offline_publication import OfflineBehaviorPublisher  # noqa: E402
from agent.offline.offline_publication_sql import SqlAlchemyOfflinePublicationRepository  # noqa: E402
from agent.offline.offline_scope import OfflineRunScopeValidator  # noqa: E402
from pipelines.offline_behavior_pipeline import _json_value, _safe_behavior_summary  # noqa: E402


class OfflinePostFixAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.start = datetime(2026, 8, 17, 2)

    def tearDown(self):
        self.engine.dispose()

    def test_phase1_scope_matrix_and_identity_projection_matrix(self):
        validator = OfflineRunScopeValidator()
        event = self._raw("PICK_UP", self.start)
        cases = (
            replace_namespace(event, camera_id="cam02"),
            replace_namespace(event, person_track_token="wrong-store:cam01:run-a:23"),
            replace_namespace(event, person_track_token="store01:cam01:run-b:23"),
            replace_namespace(event, product_track_token="product:cam01:run-b:1"),
        )
        for invalid in cases:
            with self.subTest(invalid=vars(invalid)):
                result = validator.validate_behavior(
                    store_id="store01",
                    camera_id="cam01",
                    analysis_run_id="run-a",
                    raw_stage9_events=(invalid,),
                    person_tracks=(),
                    product_tracks=(),
                )
                self.assertEqual(("conflict", "normalization_contract_conflict"), (
                    result.status, result.reason,
                ))

        wrong_token = "store01:cam01:run-b:23"
        wrong_association = self._association(
            status="confirmed", member_id="member-a", candidate_member_id=None,
            run_id="run-b", track_token=wrong_token,
        )
        result = validator.validate_associations(
            store_id="store01",
            camera_id="cam01",
            analysis_run_id="run-a",
            associations={wrong_token: wrong_association},
        )
        self.assertEqual(("conflict", "identity_contract_conflict"), (
            result.status, result.reason,
        ))

        fact = self._facts()[0]
        projector = OfflineBehaviorIdentityProjector()
        expected = {
            "confirmed": "member-a",
            "probable": None,
            "ambiguous": None,
            "conflict": None,
            "unmatched": None,
            "unavailable": None,
        }
        for status, expected_member in expected.items():
            association = self._association(
                status=status,
                member_id="member-a" if status == "confirmed" else None,
                candidate_member_id="member-b" if status == "probable" else None,
            )
            event_candidate = projector.project(
                fact=fact,
                association=association,
                enrichment=MemberProfileEnrichment(
                    "available", "Member A", "gold", None
                ),
                artifact_manifest_hash="v1:sha256:artifact",
                association_record_ref=f"track:{fact.customer_track_token}",
                store_id="store01",
            )
            with self.subTest(status=status):
                self.assertEqual(expected_member, event_candidate.member_id)
                if status != "confirmed":
                    self.assertIsNone(event_candidate.member_tier)

    def test_phase2_and_phase3_db_payload_recanonicalizes_to_identical_manifest(self):
        facts = self._facts()
        with tempfile.TemporaryDirectory() as root:
            artifact_store = self._store(Path(root))
            writer = artifact_store.begin(analysis_job_id="job-1", analysis_run_id="run-a")
            association = self._association(
                status="confirmed", member_id="member_001", candidate_member_id=None
            )
            self._write_required(
                writer, facts=facts, associations={association.track_token: association},
                include_keyframes=True,
            )
            sealed = writer.seal(
                source_video_id="video-1",
                source_content_sha256="a" * 64,
                normalized_facts=facts,
            )
            projector = OfflineBehaviorIdentityProjector()
            enrichment = MemberProfileEnrichment(
                "available", "Member 001", " Gold ", None
            )
            candidates = tuple(
                projector.project(
                    fact=fact,
                    association=association,
                    enrichment=enrichment,
                    artifact_manifest_hash=sealed.artifact_manifest_hash,
                    association_record_ref=f"track:{fact.customer_track_token}",
                    store_id="store01",
                )
                for fact in facts
            )
            prepared = PreparedOfflineBehaviorPublication.create(
                analysis_job_id="job-1",
                analysis_run_id="run-a",
                source_video_id="video-1",
                events=candidates,
            )
            publisher = OfflineBehaviorPublisher(
                publication_repository=SqlAlchemyOfflinePublicationRepository(
                    self.sessions, BehaviorEventStore(self.sessions)
                ),
                clock=lambda: datetime(2026, 8, 17, 3),
            )

            result = publisher.publish(
                prepared=prepared,
                artifacts=sealed,
                artifact_store=artifact_store,
            )

            db = self.sessions()
            try:
                durable_rows = tuple(
                    sorted(db.query(BehaviorEvent).all(), key=lambda item: item.event_key)
                )
                publication = db.get(OfflineBehaviorPublication, "job-1")
                db_prepared_rows = tuple(_prepared_from_db(row) for row in durable_rows)
            finally:
                db.close()

            canonicalizer = OfflineBehaviorCanonicalizerV1()
            db_body = {
                "manifest_version": "offline_behavior_event_manifest_v1",
                "canonicalization_version": canonicalizer.version,
                "analysis_job_id": "job-1",
                "analysis_run_id": "run-a",
                "source_video_id": "video-1",
                "event_count": len(db_prepared_rows),
                "events": [
                    canonicalizer.event_payload(row) for row in db_prepared_rows
                ],
            }
            db_manifest_bytes = canonicalizer.canonical_bytes(db_body)
            db_manifest_hash = "v1:sha256:" + hashlib.sha256(
                db_manifest_bytes
            ).hexdigest()

            self.assertEqual("published", result.status)
            self.assertEqual(3, len(durable_rows))
            self.assertEqual(prepared.canonical_manifest_bytes, db_manifest_bytes)
            self.assertEqual(prepared.event_manifest_hash, db_manifest_hash)
            self.assertEqual("store01", durable_rows[0].store_id)
            self.assertEqual("gold", durable_rows[0].member_tier)
            durations = {row.event_type: row.duration_seconds for row in durable_rows}
            self.assertIsNone(durations["merchandise_pick_up"])
            self.assertIsNone(durations["merchandise_put_down"])
            self.assertEqual(4.0, durations["merchandise_holding"])
            self.assertEqual("job-1", publication.analysis_job_id)
            self.assertEqual("run-a", publication.published_analysis_run_id)
            self.assertEqual("video-1", publication.source_video_id)
            self.assertEqual(3, publication.event_count)
            self.assertEqual(prepared.event_manifest_hash, publication.event_manifest_hash)
            for row in durable_rows:
                self.assertIsNone(row.visit_id)
                self.assertIsNone(row.entry_event_id)
                self.assertIsNone(row.evidence_clip_id)

    def test_phase4_artifact_fault_matrix_fails_closed(self):
        facts = self._facts()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)

            missing_store = self._store(root_path / "missing")
            missing_writer = missing_store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(
                missing_writer, facts=(), associations={},
                omit="normalized_behavior_facts.jsonl",
            )
            with self.assertRaisesRegex(OfflineArtifactError, "required_artifact_missing"):
                missing_writer.seal(
                    source_video_id="video", source_content_sha256="a" * 64,
                    normalized_facts=(),
                )

            corrupt_store = OfflineRunArtifactStore(
                root_path / "corrupt",
                security_adapter=StaticArtifactStorageSecurityAdapter(),
                known_public_roots=(),
                media_probe=lambda path: path.read_bytes().startswith(b"valid-mp4"),
            )
            corrupt_writer = corrupt_store.begin(analysis_job_id="job", analysis_run_id="run")
            self._write_required(
                corrupt_writer, facts=(), associations={}, analyzed_video=b"corrupt"
            )
            with self.assertRaisesRegex(OfflineArtifactError, "analyzed_video_invalid"):
                corrupt_writer.seal(
                    source_video_id="video", source_content_sha256="a" * 64,
                    normalized_facts=(),
                )

            point_facts = tuple(
                fact for fact in facts
                if fact.event_type in {"merchandise_pick_up", "merchandise_put_down"}
            )
            for point_fact in point_facts:
                keyframe_store = self._store(
                    root_path / f"keyframe-{point_fact.event_type}"
                )
                keyframe_writer = keyframe_store.begin(
                    analysis_job_id="job", analysis_run_id="run"
                )
                self._write_required(
                    keyframe_writer, facts=(point_fact,), associations={},
                    include_keyframes=False,
                )
                with self.subTest(missing_keyframe=point_fact.event_type):
                    with self.assertRaisesRegex(
                        OfflineArtifactError, "required_keyframe_missing"
                    ):
                        keyframe_writer.seal(
                            source_video_id="video",
                            source_content_sha256="a" * 64,
                            normalized_facts=(point_fact,),
                        )

            unavailable_acl = OfflineRunArtifactStore(
                root_path / "acl",
                security_adapter=StaticArtifactStorageSecurityAdapter(status="unavailable"),
                known_public_roots=(),
                media_probe=lambda path: True,
            )
            with self.assertRaisesRegex(OfflineArtifactError, "restricted_access_unverifiable"):
                unavailable_acl.begin(analysis_job_id="job", analysis_run_id="run")

            public_root = root_path / "public"
            public_store = OfflineRunArtifactStore(
                public_root / "results",
                security_adapter=StaticArtifactStorageSecurityAdapter(),
                known_public_roots=(public_root,),
                media_probe=lambda path: True,
            )
            with self.assertRaisesRegex(OfflineArtifactError, "artifact_root_is_public"):
                public_store.begin(analysis_job_id="job", analysis_run_id="run")

        db = self.sessions()
        try:
            self.assertEqual(0, db.query(OfflineBehaviorPublication).count())
            self.assertEqual(0, db.query(BehaviorEvent).count())
        finally:
            db.close()

    def test_phase5_formal_outputs_do_not_leak_candidate_or_spatial_identity(self):
        base_fact = self._facts()[0]
        projector = OfflineBehaviorIdentityProjector()
        statuses = (
            ("confirmed", "member-a", None),
            ("probable", None, "member-b"),
            ("ambiguous", None, None),
            ("conflict", None, None),
            ("unmatched", None, None),
        )
        candidates = []
        associations = []
        for index, (status, member_id, candidate_member_id) in enumerate(statuses, 1):
            token = f"store01:cam01:run-a:{index}"
            fact = replace(
                base_fact,
                customer_track_id=index,
                customer_track_token=token,
                normalized_fact_ref=f"fact:{index}",
            )
            association = self._association(
                status=status,
                member_id=member_id,
                candidate_member_id=candidate_member_id,
                track_token=token,
            )
            if status == "conflict":
                association = replace(
                    association,
                    candidate_member_ids=("member-c", "member-d"),
                )
            associations.append(association)
            candidates.append(projector.project(
                fact=fact,
                association=association,
                enrichment=MemberProfileEnrichment(
                    "available", "Private Name", "gold", None
                ),
                artifact_manifest_hash="v1:sha256:artifact",
                association_record_ref=f"track:{token}",
                store_id="store01",
            ))
        prepared = PreparedOfflineBehaviorPublication.create(
            analysis_job_id="job-1", analysis_run_id="run-a",
            source_video_id="video-1", events=candidates,
        )
        session = self.sessions()
        try:
            BehaviorEventStore(self.sessions).bulk_insert_behavior(
                session=session, events=prepared.events
            )
            session.commit()
        finally:
            session.close()

        summary = _safe_behavior_summary(
            prepared.events,
            publication_eligible=True,
            media_timeline_status="available",
            absolute_event_time_status="available",
            behavior_time_quality="authoritative",
            identity_status="available",
            identity_failure_reason=None,
        )
        business_api = TrafficQueryService(self.sessions).recent_behaviors(limit=10)
        formal_output = json.dumps(
            {
                "manifest": json.loads(prepared.canonical_manifest_bytes),
                "summary": summary,
                "api": business_api,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for forbidden in (
            "candidate_member_id", "candidate_member_name",
            "conflicting_member_ids", "unique_id", "face_bbox", "device_id",
        ):
            self.assertNotIn(forbidden, formal_output)
        self.assertEqual({"member-a": 1}, summary["events_by_confirmed_member_id"])

        restricted_identity_artifact = json.dumps(
            [_json_value(value) for value in associations], sort_keys=True
        )
        self.assertIn("candidate_member_id", restricted_identity_artifact)
        self.assertIn("member-c", restricted_identity_artifact)
        self.assertIn("member-d", restricted_identity_artifact)
        resolver = OfflinePersonDisplayIdentityResolver()
        labels = {
            status: resolver.resolve_label(OfflineDisplayIdentity(
                analysis_run_id="run-a",
                track_token=f"store01:cam01:run-a:{index}",
                customer_ordinal=index,
                identity_status=status,
                member_id=member_id,
                member_name="Member A" if status == "confirmed" else None,
                artifact_only=False,
            ))
            for index, (status, member_id, _) in enumerate(statuses, 1)
        }
        self.assertEqual("会员：Member A", labels["confirmed"])
        self.assertEqual("顾客2 · 疑似会员", labels["probable"])
        self.assertEqual("顾客3", labels["ambiguous"])
        self.assertEqual("顾客4", labels["conflict"])
        self.assertEqual("顾客5", labels["unmatched"])

    def test_phase7_tamper_no_overwrite_and_new_run_namespace(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job", analysis_run_id="run-1")
            self._write_required(writer, facts=(), associations={})
            sealed = writer.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )
            original_hash = sealed.artifact_manifest_hash
            person_tracks = sealed.run_directory / "person_tracks.jsonl"
            person_tracks.chmod(stat.S_IWRITE | stat.S_IREAD)
            person_tracks.write_text('{"track_token":"tampered"}\n', encoding="utf-8")

            verification = store.verify(sealed)

            self.assertTrue(original_hash.startswith("v1:sha256:"))
            self.assertNotEqual("verified", verification.artifact_integrity_status)
            with self.assertRaisesRegex(OfflineArtifactError, "run_artifact_collision"):
                store.begin(analysis_job_id="job", analysis_run_id="run-1")
            retry = store.begin(analysis_job_id="job", analysis_run_id="run-2")
            self._write_required(retry, facts=(), associations={})
            retry_sealed = retry.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )
            self.assertNotEqual(sealed.run_directory, retry_sealed.run_directory)

    def test_phase8_exactly_once_same_manifest_and_conflicting_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            store = self._store(Path(root))
            writer = store.begin(analysis_job_id="job-once", analysis_run_id="run-1")
            self._write_required(writer, facts=(), associations={})
            sealed = writer.seal(
                source_video_id="video", source_content_sha256="a" * 64,
                normalized_facts=(),
            )
            token = "store01:cam01:run-1:23"
            fact = replace(
                self._facts()[0],
                analysis_job_id="job-once",
                analysis_run_id="run-1",
                source_video_id="video",
                customer_track_token=token,
                product_track_token="product:cam01:run-1:1",
                normalized_fact_ref="fact:job-once:run-1",
            )
            association = self._association(
                status="unmatched", member_id=None, candidate_member_id=None,
                run_id="run-1", track_token=token,
            )
            candidate = OfflineBehaviorIdentityProjector().project(
                fact=fact,
                association=association,
                enrichment=None,
                artifact_manifest_hash=sealed.artifact_manifest_hash,
                association_record_ref=f"track:{fact.customer_track_token}",
                store_id="store01",
            )
            prepared = PreparedOfflineBehaviorPublication.create(
                analysis_job_id="job-once", analysis_run_id="run-1",
                source_video_id="video", events=(candidate,),
            )
            conflicting = PreparedOfflineBehaviorPublication.create(
                analysis_job_id="job-once", analysis_run_id="run-1",
                source_video_id="video", events=(replace(candidate, confidence=0.5),),
            )
            publisher = OfflineBehaviorPublisher(
                publication_repository=SqlAlchemyOfflinePublicationRepository(
                    self.sessions, BehaviorEventStore(self.sessions)
                )
            )

            first = publisher.publish(
                prepared=prepared, artifacts=sealed, artifact_store=store
            )
            same = publisher.publish(
                prepared=prepared, artifacts=sealed, artifact_store=store
            )
            conflict = publisher.publish(
                prepared=conflicting, artifacts=sealed, artifact_store=store
            )

            db = self.sessions()
            try:
                publication = db.get(OfflineBehaviorPublication, "job-once")
                event_count = db.query(BehaviorEvent).count()
            finally:
                db.close()
            self.assertEqual("published", first.status)
            self.assertEqual("already_published_same_manifest", same.status)
            self.assertEqual("already_published_conflict", conflict.status)
            self.assertEqual(1, event_count)
            self.assertEqual(prepared.event_manifest_hash, publication.event_manifest_hash)
            self.assertNotEqual(
                prepared.event_manifest_hash, conflicting.event_manifest_hash
            )

    def _facts(self):
        return Stage9BehaviorFactAdapter().normalize(
            analysis_job_id="job-1",
            analysis_run_id="run-a",
            source_video_id="video-1",
            raw_events=(
                self._raw("PICK_UP", self.start),
                self._raw("PUT_DOWN", self.start + timedelta(seconds=4)),
            ),
        ).facts

    def _raw(self, event_type, occurred_at):
        return SimpleNamespace(
            event_id=f"raw-{event_type.lower()}",
            event_type=event_type,
            observed_at=occurred_at,
            source_seq=1,
            camera_id="cam01",
            product_key="bear",
            product_name="Bear",
            product_track_token="product:cam01:run-a:1",
            person_track_id=23,
            person_track_token="store01:cam01:run-a:23",
            interaction_session_id="session-bear",
            confidence=0.9,
            holding_duration_seconds=None,
        )

    def _association(
        self,
        *,
        status,
        member_id,
        candidate_member_id,
        run_id="run-a",
        track_token="store01:cam01:run-a:23",
    ):
        return OfflineTrackIdentityAssociation(
            analysis_run_id=run_id,
            track_token=track_token,
            status=status,
            member_id=member_id,
            candidate_member_id=candidate_member_id,
            valid_from=self.start,
            valid_to=self.start + timedelta(seconds=4),
            evidence_ids=("evidence-1",),
            method="same_camera_spatial" if status == "confirmed" else "same_camera_temporal",
            confidence=0.9,
            candidate_member_ids=(
                (candidate_member_id,) if candidate_member_id else ()
            ),
        )

    @staticmethod
    def _store(root):
        return OfflineRunArtifactStore(
            root,
            security_adapter=StaticArtifactStorageSecurityAdapter(),
            known_public_roots=(),
            media_probe=lambda path: path.stat().st_size > 0,
        )

    @staticmethod
    def _write_required(
        writer,
        *,
        facts,
        associations,
        omit=None,
        include_keyframes=False,
        analyzed_video=b"valid-mp4",
    ):
        values = {
            "metadata.json": {
                "analysis_job_id": writer.analysis_job_id,
                "analysis_run_id": writer.analysis_run_id,
                "store_id": "store01",
                "camera_id": "cam01",
                "event_time_standard": "utc_naive",
                "media_timeline_status": "available",
                "behavior_time_quality": "authoritative",
            },
            "source_manifest.json": {
                "source_video_id": "video-1" if writer.analysis_job_id == "job-1" else "video",
                "source_content_sha256": "a" * 64,
                "source_video_integrity_status": "verified",
            },
            "behavior_summary.json": {
                "summary_version": "offline_behavior_summary_v1",
                "event_count": len(facts),
                "events_by_type": {},
                "events_by_confirmed_member_id": {},
                "publication_eligible": True,
            },
        }
        for name, value in values.items():
            if name != omit:
                writer.write_json(name, value)
        jsonl_values = {
            "person_tracks.jsonl": (),
            "product_tracks.jsonl": (),
            "identity_evidence.jsonl": (),
            "track_identity_associations.jsonl": (
                _json_value(associations[key]) for key in sorted(associations)
            ),
            "raw_stage9_events.jsonl": (),
            "normalized_behavior_facts.jsonl": (
                _json_value(fact) for fact in facts
            ),
        }
        for name, records in jsonl_values.items():
            if name != omit:
                writer.write_jsonl(name, records)
        if "analyzed_video.mp4" != omit:
            writer.write_bytes("analyzed_video.mp4", analyzed_video)
        if include_keyframes:
            for fact in facts:
                if fact.keyframe_ref:
                    writer.write_bytes(fact.keyframe_ref, b"keyframe")


def _prepared_from_db(row: BehaviorEvent) -> PreparedOfflineBehaviorRow:
    values = {
        field.name: (
            row.details if field.name == "details" else getattr(row, field.name)
        )
        for field in fields(PreparedOfflineBehaviorRow)
    }
    return PreparedOfflineBehaviorRow(**values)


def replace_namespace(value: SimpleNamespace, **updates) -> SimpleNamespace:
    values = vars(value).copy()
    values.update(updates)
    return SimpleNamespace(**values)


if __name__ == "__main__":
    unittest.main()
