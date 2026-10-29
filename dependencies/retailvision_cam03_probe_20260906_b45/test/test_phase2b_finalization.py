from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.behavior_events import BehaviorEventInput, BehaviorEventStore
from agent.camera_track_identity import (
    CameraTrackIdentityCache,
    TrackSnapshot,
    TrackSnapshotBuffer,
)
from agent.identity_runtime_lease import (
    IdentityAssociationRuntimeLeaseService,
    RuntimeLeaseUnavailable,
)
from agent.models import (
    CameraIdentityBindingPolicy,
    CustomerEntryConfirmedEvent,
    DeviceRecognitionInbox,
    IdentityAssociationRuntimeLease,
    Member,
    MemberArrival,
    MemberArrivalEffect,
    TrackAssociationEvaluation,
    TrackAssociationFinalizationJob,
    TrackIdentityBinding,
    Visit,
    VisitEntryEvidence,
)
from agent.recognition_association_worker import (
    RecognitionAssociationWorker,
    TrackAssociationCandidate,
)
from agent.reusable_visit_resolver import (
    RecognitionAssociationRequest,
    ReusableVisitResolver,
)
from agent.schema_evolution import evolve_schema
from agent.track_association_policy import TrackAssociationAuditService
from agent.track_association_finalization import (
    BindingLifecycleHeartbeat,
    PersistentFinalizationInvariantViolation,
    TrackAssociationFinalizationWorker,
)
from agent.visit_activity import VisitActivity, VisitActivityService


class _CountingLeaseService:
    def __init__(self, *, fail_on: int) -> None:
        self.calls = 0
        self.fail_on = fail_on

    def validate_fence(self, **_values) -> None:
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeLeaseUnavailable("runtime_lease_lost")


class Phase2BFinalizationProducerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 9, 20, 0, 0)
        self.engine = configure_sqlite_foreign_keys(create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions() as db:
            db.add(Member(id="member-1", name="Member One", tier="gold"))
            db.add(CameraIdentityBindingPolicy(
                id="policy-1",
                camera_id="camera-1",
                mode="enabled",
                algorithm_version="track-association-v1",
                parameter_version="track-association-v1-defaults",
                effective_from=self.now,
                qualification_snapshot={},
            ))
            db.commit()
        self.lease_service = IdentityAssociationRuntimeLeaseService(
            self.sessions, clock=lambda: self.now
        )
        self.lease = self.lease_service.acquire(
            store_id="store-1", mode="new", owner_id="owner-1"
        )

    def tearDown(self):
        self.engine.dispose()

    @property
    def track_token(self) -> str:
        return "store-1:camera-1:runtime-1:7"

    def _inbox(self, event_id: str, *, status: str = "pending") -> None:
        with self.sessions() as db:
            db.add(DeviceRecognitionInbox(
                canonical_event_id=event_id,
                device_id="device-1",
                source="push",
                source_event_id=event_id,
                occurred_at=self.now,
                store_id="store-1",
                member_id="member-1",
                unique_id="member-1",
                similarity=0.95,
                status="processed",
                recognition_status="accepted",
                store_resolution_status="resolved",
                track_association_status=status,
                association_status="pending",
                association_deadline_at=self.now + timedelta(seconds=5),
                association_claim_token="claim-1",
                association_locked_at=self.now,
                association_claim_expires_at=self.now + timedelta(seconds=30),
            ))
            db.commit()

    def _evaluation(
        self,
        event_id: str,
        *,
        evaluation_id: str | None = None,
        result: str = "confirmed",
        mode: str = "enabled",
        quality_status: str = "verified",
        recognition_event_id: str | None = None,
        track_token: str | None = None,
        camera_id: str = "camera-1",
    ) -> TrackAssociationEvaluation:
        token = track_token or self.track_token
        row = TrackAssociationEvaluation(
            id=evaluation_id or f"evaluation-{event_id}",
            recognition_event_id=recognition_event_id or event_id,
            assessment_fingerprint=f"fingerprint-{evaluation_id or event_id}",
            store_id="store-1",
            camera_id=camera_id,
            runtime_id="runtime-1",
            runtime_owner_id="owner-1",
            runtime_instance_id="runtime-1",
            mode=mode,
            result=result,
            method="verified_face_spatial_evidence",
            reason="verified_face_spatial_evidence",
            candidates=[{"track_token": token}],
            excluded_candidates=[],
            candidate_count=1,
            effective_occurred_at=self.now,
            observed_at=self.now,
            algorithm_version="track-association-v1",
            parameter_version="track-association-v1-defaults",
            policy_version_id=("policy-1" if camera_id == "camera-1" else f"policy-{camera_id}"),
            policy_snapshot={"mode": mode},
            spatial_evidence={
                "quality_status": quality_status,
                "tracks": [{"track_token": token, "matched": True}],
            },
            applied_binding=False,
        )
        with self.sessions() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
        return row

    def _clear_claim(self, event_id: str) -> None:
        with self.sessions() as db:
            row = db.get(DeviceRecognitionInbox, event_id)
            row.association_claim_token = None
            row.association_locked_at = None
            row.association_claim_expires_at = None
            db.commit()

    def _add_anonymous_entry(self, *, visit_id: str, event_id: str) -> None:
        with self.sessions() as db:
            db.add(CustomerEntryConfirmedEvent(
                id=f"entry-{event_id}",
                entry_key=f"entry-key-{event_id}",
                dedup_key=f"entry-dedup-{event_id}",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                tracker_track_id=7,
                track_token=self.track_token,
                runtime_session_id="runtime-1",
                occurred_at=self.now - timedelta(seconds=2),
                confirmed_at=self.now - timedelta(seconds=2),
                confidence=0.95,
            ))
            db.add(VisitEntryEvidence(
                visit_id=visit_id,
                entry_event_id=f"entry-{event_id}",
                evidence_role="initial",
            ))
            db.commit()

    def _request(
        self,
        event_id: str,
        evaluation_id: str,
        *,
        result: str = "confirmed",
        runtime_instance_id: str = "runtime-1",
        track_token: str | None = None,
        camera_id: str = "camera-1",
    ) -> RecognitionAssociationRequest:
        token = track_token or self.track_token
        return RecognitionAssociationRequest(
            recognition_event_id=event_id,
            member_id="member-1",
            member_tier="gold",
            device_id="device-1",
            store_id="store-1",
            camera_id=camera_id,
            occurred_at=self.now,
            similarity=0.95,
            track_association_status=result,
            track_token=token,
            track_method="verified_face_spatial_evidence",
            mode="new",
            runtime_id=runtime_instance_id,
            runtime_owner_id="owner-1",
            runtime_instance_id=runtime_instance_id,
            lease_token=self.lease.lease_token,
            lease_generation=self.lease.lease_generation,
            evaluation_id=evaluation_id,
            policy_version_id=("policy-1" if camera_id == "camera-1" else f"policy-{camera_id}"),
            association_claim_token="claim-1",
        )

    def _resolver(
        self,
        *,
        enabled: bool = True,
        transform_status: str = "verified",
        lease_service=None,
        runtime_instance_id: str = "runtime-1",
    ) -> ReusableVisitResolver:
        return ReusableVisitResolver(
            self.sessions,
            clock=lambda: self.now,
            runtime_lease_service=lease_service or self.lease_service,
            runtime_instance_id=runtime_instance_id,
            phase2b_finalization_enabled=enabled,
            transform_status_provider=lambda _camera_id: transform_status,
        )

    def test_worker_passes_exact_evaluation_id_to_resolver(self):
        self._inbox("worker-event")
        self._clear_claim("worker-event")
        captured = []
        resolver = SimpleNamespace(resolve=lambda request: (
            captured.append(request) or SimpleNamespace(
                track_association_status=request.track_association_status
            )
        ))
        audit = SimpleNamespace(record_evaluation=lambda **_values: SimpleNamespace(
            id="evaluation-exact"
        ))
        worker = RecognitionAssociationWorker(
            self.sessions,
            resolver,
            self.lease,
            clock=lambda: self.now,
            runtime_instance_id="runtime-1",
            audit_service=audit,
            candidate_provider=lambda _row: TrackAssociationCandidate(
                status="confirmed",
                camera_id="camera-1",
                track_token=self.track_token,
                method="verified_face_spatial_evidence",
                policy_version_id="policy-1",
                policy_mode="enabled",
                candidates=({"track_token": self.track_token},),
            ),
        )

        self.assertEqual(1, worker.run_once())
        self.assertEqual("evaluation-exact", captured[0].evaluation_id)

    def test_exact_anonymous_entry_promotes_before_finalization_job(self):
        with self.sessions() as db:
            anonymous = Visit(
                id="anonymous-phase2c",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            )
            db.add(anonymous)
            db.commit()
        self._add_anonymous_entry(visit_id="anonymous-phase2c", event_id="phase2c")
        self._inbox("phase2c")
        evaluation = self._evaluation("phase2c")

        result = self._resolver().resolve(self._request("phase2c", evaluation.id))

        self.assertEqual("anonymous-phase2c", result.visit_id)
        with self.sessions() as db:
            visit = db.get(Visit, "anonymous-phase2c")
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("member-1", visit.member_id)
            self.assertEqual("entrance_crossing", visit.visit_source)
            self.assertEqual("member", visit.visitor_type)
            self.assertEqual(0.95, float(visit.recognized_confidence))
            self.assertEqual(0.95, visit.identity_confidence)
            self.assertEqual(visit.id, job.visit_id)
            self.assertEqual(1, db.query(VisitEntryEvidence).count())

    def test_phase2b_job_fence_blocks_anonymous_promotion_before_write(self):
        self._create_pending_job("existing-phase2b")
        with self.sessions() as db:
            anonymous = Visit(
                id="anonymous-fenced",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            )
            db.add(anonymous)
            db.commit()
        self._add_anonymous_entry(visit_id="anonymous-fenced", event_id="fenced")
        self._inbox("fenced")
        evaluation = self._evaluation("fenced")

        with self.sessions() as db:
            before_visits = db.query(Visit).count()
            before_arrivals = db.query(MemberArrival).count()
            before_effects = db.query(MemberArrivalEffect).count()
            before_jobs = db.query(TrackAssociationFinalizationJob).count()

        with patch("agent.reusable_visit_resolver.logger.warning") as warning:
            result = self._resolver().resolve(self._request("fenced", evaluation.id))

        with self.sessions() as db:
            anonymous = db.get(Visit, "anonymous-fenced")
            existing_job = db.query(TrackAssociationFinalizationJob).filter_by(
                recognition_event_id="existing-phase2b"
            ).one()
            self.assertIsNone(anonymous.member_id)
            self.assertIsNone(anonymous.superseded_by_visit_id)
            self.assertEqual("pending", existing_job.status)
            self.assertEqual(before_visits, db.query(Visit).count())
            self.assertEqual(before_arrivals, db.query(MemberArrival).count())
            self.assertEqual(before_effects, db.query(MemberArrivalEffect).count())
            self.assertEqual(before_jobs, db.query(TrackAssociationFinalizationJob).count())
            incoming = db.get(DeviceRecognitionInbox, "fenced")
            self.assertEqual("terminal", incoming.association_status)
            self.assertEqual("no_business_outcome", incoming.association_outcome)
            self.assertIsNone(incoming.matched_visit_id)
            self.assertIsNone(incoming.finalization_job_id)
        self.assertIsNone(result.visit_id)
        self.assertEqual("no_business_outcome", result.association_outcome)
        diagnostic = warning.call_args.kwargs["extra"]
        self.assertEqual("anonymous-fenced", diagnostic["anonymous_visit_id"])
        self.assertEqual(evaluation.id, diagnostic["evaluation_id"])
        self.assertEqual("fenced", diagnostic["recognition_event_id"])
        self.assertEqual(self.track_token, diagnostic["track_token"])
        self.assertEqual("runtime-1", diagnostic["runtime_instance_id"])
        self.assertTrue(diagnostic["finalization_job_ids"])
        self.assertEqual(("pending",), diagnostic["job_statuses"])
        self.assertEqual(("pending",), diagnostic["application_statuses"])
        self.assertEqual((), diagnostic["binding_ids"])

    def test_phase2c_ambiguous_exact_entries_preserve_all_anonymous_visits(self):
        for visit_id, event_id in (
            ("anonymous-ambiguous-one", "ambiguous-one"),
            ("anonymous-ambiguous-two", "ambiguous-two"),
        ):
            with self.sessions() as db:
                db.add(Visit(
                    id=visit_id,
                    store_id="store-1",
                    camera_id="camera-1",
                    track_id=7,
                    entry_time=self.now - timedelta(seconds=2),
                    last_seen_at=self.now - timedelta(seconds=2),
                    visitor_type="anonymous",
                    identity_status="anonymous",
                    visit_source="entrance_crossing",
                    verification_status="confirmed",
                ))
                db.commit()
            self._add_anonymous_entry(visit_id=visit_id, event_id=event_id)
        self._inbox("ambiguous-recognition")
        evaluation = self._evaluation("ambiguous-recognition")

        result = self._resolver().resolve(
            self._request("ambiguous-recognition", evaluation.id)
        )

        with self.sessions() as db:
            anonymous = db.query(Visit).filter(
                Visit.id.in_(("anonymous-ambiguous-one", "anonymous-ambiguous-two"))
            ).all()
            self.assertEqual(2, len(anonymous))
            self.assertTrue(all(visit.member_id is None for visit in anonymous))
            self.assertNotIn(result.visit_id, {visit.id for visit in anonymous})

    def test_phase2c_requires_exact_entry_tracker_track_id(self):
        with self.sessions() as db:
            db.add(Visit(
                id="anonymous-tracker-mismatch",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            ))
            db.add(CustomerEntryConfirmedEvent(
                id="entry-tracker-mismatch",
                entry_key="entry-key-tracker-mismatch",
                dedup_key="entry-dedup-tracker-mismatch",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                tracker_track_id=8,
                track_token=self.track_token,
                runtime_session_id="runtime-1",
                occurred_at=self.now - timedelta(seconds=2),
                confirmed_at=self.now - timedelta(seconds=2),
                confidence=0.95,
            ))
            db.add(VisitEntryEvidence(
                visit_id="anonymous-tracker-mismatch",
                entry_event_id="entry-tracker-mismatch",
                evidence_role="initial",
            ))
            db.commit()
        self._inbox("tracker-mismatch-recognition")
        evaluation = self._evaluation("tracker-mismatch-recognition")

        result = self._resolver().resolve(
            self._request("tracker-mismatch-recognition", evaluation.id)
        )

        with self.sessions() as db:
            anonymous = db.get(Visit, "anonymous-tracker-mismatch")
            self.assertIsNone(anonymous.member_id)
            self.assertNotEqual(anonymous.id, result.visit_id)

    def test_phase2c_requires_exact_anonymous_visit_scope(self):
        mismatches = (
            ("anonymous-visit-store-mismatch", "store-2", "camera-1", 7),
            ("anonymous-visit-camera-mismatch", "store-1", "camera-2", 7),
            ("anonymous-visit-track-mismatch", "store-1", "camera-1", 8),
        )
        with self.sessions() as db:
            for index, (visit_id, store_id, camera_id, track_id) in enumerate(mismatches):
                db.add(Visit(
                    id=visit_id,
                    store_id=store_id,
                    camera_id=camera_id,
                    track_id=track_id,
                    entry_time=self.now - timedelta(seconds=2),
                    last_seen_at=self.now - timedelta(seconds=2),
                    visitor_type="anonymous",
                    identity_status="anonymous",
                    visit_source="entrance_crossing",
                    verification_status="confirmed",
                ))
                db.add(CustomerEntryConfirmedEvent(
                    id=f"entry-visit-scope-{index}",
                    entry_key=f"entry-key-visit-scope-{index}",
                    dedup_key=f"entry-dedup-visit-scope-{index}",
                    store_id="store-1",
                    camera_id="camera-1",
                    track_id=7,
                    tracker_track_id=7,
                    track_token=self.track_token,
                    runtime_session_id="runtime-1",
                    occurred_at=self.now - timedelta(seconds=2),
                    confirmed_at=self.now - timedelta(seconds=2),
                    confidence=0.95,
                ))
                db.add(VisitEntryEvidence(
                    visit_id=visit_id,
                    entry_event_id=f"entry-visit-scope-{index}",
                    evidence_role="initial",
                ))
            db.commit()
        self._inbox("visit-scope-mismatch")
        evaluation = self._evaluation("visit-scope-mismatch")

        result = self._resolver().resolve(
            self._request("visit-scope-mismatch", evaluation.id)
        )

        with self.sessions() as db:
            for visit_id, *_values in mismatches:
                self.assertIsNone(db.get(Visit, visit_id).member_id)
            self.assertNotIn(result.visit_id, {row[0] for row in mismatches})

    def test_phase2c_binding_fence_blocks_promotion_before_write(self):
        self._evaluation(
            "historical-binding-event",
            evaluation_id="historical-binding-evaluation",
        )
        with self.sessions() as db:
            anonymous = Visit(
                id="anonymous-binding-fenced",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            )
            db.add(anonymous)
            db.flush()
            db.add(TrackIdentityBinding(
                recognition_event_id="historical-binding-event",
                evaluation_id="historical-binding-evaluation",
                store_id="store-1",
                camera_id="camera-1",
                runtime_owner_id="owner-1",
                runtime_instance_id="runtime-1",
                track_id=7,
                track_token=self.track_token,
                member_id="member-1",
                visit_id=anonymous.id,
                binding_level="confirmed",
                status="confirmed",
                identity_source="verified_face_spatial_evidence",
                identity_confidence=0.95,
                valid_from=self.now - timedelta(seconds=2),
                expires_at=self.now + timedelta(minutes=1),
            ))
            db.commit()
        self._add_anonymous_entry(
            visit_id="anonymous-binding-fenced", event_id="binding-fenced"
        )
        self._inbox("binding-fenced")
        evaluation = self._evaluation("binding-fenced")

        self._resolver().resolve(self._request("binding-fenced", evaluation.id))

        with self.sessions() as db:
            anonymous = db.get(Visit, "anonymous-binding-fenced")
            binding = db.query(TrackIdentityBinding).one()
            self.assertIsNone(anonymous.member_id)
            self.assertEqual("confirmed", binding.status)
            self.assertEqual("anonymous-binding-fenced", binding.visit_id)

    def test_phase2c_non_finalizable_assessment_does_not_promote(self):
        with self.sessions() as db:
            db.add(Visit(
                id="anonymous-probable",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            ))
            db.commit()
        self._add_anonymous_entry(visit_id="anonymous-probable", event_id="probable")
        self._inbox("probable")
        evaluation = self._evaluation("probable", result="probable")

        self._resolver().resolve(self._request("probable", evaluation.id, result="probable"))

        with self.sessions() as db:
            self.assertIsNone(db.get(Visit, "anonymous-probable").member_id)

    def test_phase2c_any_existing_finalization_job_status_fences_promotion(self):
        self._evaluation(
            "frozen-history-event", evaluation_id="frozen-history-evaluation"
        )
        visit_id = "anonymous-frozen-history"
        with self.sessions() as db:
            db.add(Visit(
                id=visit_id,
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            ))
            db.flush()
            db.add(TrackAssociationFinalizationJob(
                recognition_event_id="frozen-history-event",
                evaluation_id="frozen-history-evaluation",
                visit_id=visit_id,
                track_token=self.track_token,
                status="pending",
                application_status="pending",
                cache_projection_status="skipped",
                backfill_status="skipped",
            ))
            db.commit()
        self._add_anonymous_entry(visit_id=visit_id, event_id="frozen-history")

        for status, application_status in (
            ("pending", "pending"),
            ("processing", "pending"),
            ("binding_committed", "applied"),
            ("succeeded", "applied"),
            ("succeeded", "skipped"),
            ("succeeded", "conflict"),
        ):
            with self.subTest(status=status, application_status=application_status):
                with self.sessions() as db:
                    job = db.query(TrackAssociationFinalizationJob).filter_by(
                        recognition_event_id="frozen-history-event"
                    ).one()
                    job.status = status
                    job.application_status = application_status
                    db.commit()
                event_id = f"incoming-{status}-{application_status}"
                self._inbox(event_id)
                incoming = self._evaluation(event_id)

                self._resolver().resolve(
                    self._request(event_id, incoming.id)
                )

                with self.sessions() as db:
                    self.assertIsNone(db.get(Visit, visit_id).member_id)

    def test_phase2c_merges_existing_member_visit_before_creating_job(self):
        with self.sessions() as db:
            anonymous = Visit(
                id="anonymous-to-merge",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=4),
                last_seen_at=self.now - timedelta(seconds=4),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            )
            existing = Visit(
                id="member-to-merge",
                member_id="member-1",
                store_id="store-1",
                camera_id="camera-1",
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="member",
                identity_status="identified_member",
                is_member=True,
                visit_source="recognition_only",
                verification_status="confirmed",
            )
            db.add_all((anonymous, existing))
            db.commit()
        self._add_anonymous_entry(visit_id="anonymous-to-merge", event_id="to-merge")
        self._inbox("merge-recognition")
        evaluation = self._evaluation("merge-recognition")

        result = self._resolver().resolve(
            self._request("merge-recognition", evaluation.id)
        )

        with self.sessions() as db:
            canonical = db.get(Visit, result.visit_id)
            duplicate = db.get(Visit, "member-to-merge")
            job = db.query(TrackAssociationFinalizationJob).filter_by(
                recognition_event_id="merge-recognition"
            ).one()
            self.assertEqual("anonymous-to-merge", canonical.id)
            self.assertEqual("member-1", canonical.member_id)
            self.assertEqual("entrance_crossing", canonical.visit_source)
            self.assertEqual(canonical.id, duplicate.superseded_by_visit_id)
            self.assertEqual(canonical.id, job.visit_id)

    def test_phase2c_lease_loss_rolls_back_promotion_and_finalization_job(self):
        with self.sessions() as db:
            db.add(Visit(
                id="anonymous-lease-lost",
                store_id="store-1",
                camera_id="camera-1",
                track_id=7,
                entry_time=self.now - timedelta(seconds=2),
                last_seen_at=self.now - timedelta(seconds=2),
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            ))
            db.commit()
        self._add_anonymous_entry(visit_id="anonymous-lease-lost", event_id="lease-lost")
        self._inbox("lease-lost")
        evaluation = self._evaluation("lease-lost")

        with self.assertRaisesRegex(RuntimeLeaseUnavailable, "runtime_lease_lost"):
            self._resolver(lease_service=_CountingLeaseService(fail_on=2)).resolve(
                self._request("lease-lost", evaluation.id)
            )

        with self.sessions() as db:
            anonymous = db.get(Visit, "anonymous-lease-lost")
            self.assertIsNone(anonymous.member_id)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_worker_audit_and_real_resolver_share_one_evaluation(self):
        self._inbox("integrated-event")
        self._clear_claim("integrated-event")
        worker = RecognitionAssociationWorker(
            self.sessions,
            self._resolver(),
            self.lease,
            clock=lambda: self.now,
            runtime_instance_id="runtime-1",
            audit_service=TrackAssociationAuditService(
                self.sessions, clock=lambda: self.now
            ),
            candidate_provider=lambda _row: TrackAssociationCandidate(
                status="confirmed",
                camera_id="camera-1",
                track_token=self.track_token,
                method="verified_face_spatial_evidence",
                reason="verified_face_spatial_evidence",
                policy_version_id="policy-1",
                policy_mode="enabled",
                candidates=({"track_token": self.track_token},),
                effective_occurred_at=self.now,
                spatial_evidence={
                    "quality_status": "verified",
                    "tracks": [{"track_token": self.track_token, "matched": True}],
                },
            ),
        )

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            evaluation = db.query(TrackAssociationEvaluation).one()
            job = db.query(TrackAssociationFinalizationJob).one()
            inbox = db.get(DeviceRecognitionInbox, "integrated-event")
            self.assertEqual(evaluation.id, job.evaluation_id)
            self.assertEqual(evaluation.id, inbox.evaluation_id)

    def test_confirmed_verified_assessment_creates_atomic_pending_job(self):
        self._inbox("confirmed-event")
        evaluation = self._evaluation("confirmed-event")

        result = self._resolver().resolve(
            self._request("confirmed-event", evaluation.id)
        )

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            inbox = db.get(DeviceRecognitionInbox, "confirmed-event")
            self.assertEqual("finalization_pending", result.track_association_status)
            self.assertEqual("finalization_pending", inbox.track_association_status)
            self.assertEqual(evaluation.id, job.evaluation_id)
            self.assertEqual(job.id, inbox.finalization_job_id)
            self.assertEqual(inbox.matched_visit_id, job.visit_id)
            self.assertEqual(self.track_token, job.track_token)
            self.assertEqual("pending", job.application_status)
            self.assertIsNone(inbox.association_claim_token)

    def test_resolver_uses_exact_evaluation_instead_of_latest_for_event(self):
        self._inbox("exact-event")
        exact = self._evaluation("exact-event", evaluation_id="evaluation-exact")
        self._evaluation(
            "exact-event", evaluation_id="evaluation-later", quality_status="parsed"
        )

        self._resolver().resolve(self._request("exact-event", exact.id))

        with self.sessions() as db:
            self.assertEqual(
                exact.id, db.query(TrackAssociationFinalizationJob).one().evaluation_id
            )

    def test_missing_evaluation_rolls_back_t1(self):
        self._inbox("missing-evaluation")

        with self.assertRaisesRegex(ValueError, "evaluation_not_found"):
            self._resolver().resolve(
                self._request("missing-evaluation", "evaluation-missing")
            )

        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())
            self.assertEqual(
                "claim-1",
                db.get(DeviceRecognitionInbox, "missing-evaluation").association_claim_token,
            )

    def test_evaluation_for_other_recognition_rolls_back_t1(self):
        self._inbox("event-a")
        evaluation = self._evaluation(
            "event-a", recognition_event_id="event-b"
        )

        with self.assertRaisesRegex(ValueError, "evaluation_event_conflict"):
            self._resolver().resolve(self._request("event-a", evaluation.id))

        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_same_recognition_reuses_identical_job(self):
        self._inbox("retry-event")
        evaluation = self._evaluation("retry-event")
        request = self._request("retry-event", evaluation.id)

        first = self._resolver().resolve(request)
        replay = RecognitionAssociationRequest(**{
            **request.__dict__, "association_claim_token": None
        })
        second = self._resolver().resolve(replay)

        with self.sessions() as db:
            jobs = db.query(TrackAssociationFinalizationJob).all()
            self.assertEqual(1, len(jobs))
            self.assertEqual(first.visit_id, second.visit_id)

    def test_existing_job_with_different_evaluation_is_domain_conflict(self):
        self._inbox("conflict-event")
        first = self._evaluation("conflict-event", evaluation_id="evaluation-one")
        second = self._evaluation("conflict-event", evaluation_id="evaluation-two")
        resolver = self._resolver()
        resolver.resolve(self._request("conflict-event", first.id))
        with self.sessions() as db:
            db.get(DeviceRecognitionInbox, "conflict-event").evaluation_id = None
            db.commit()

        conflicting_request = self._request("conflict-event", second.id)
        conflicting_request = RecognitionAssociationRequest(**{
            **conflicting_request.__dict__, "association_claim_token": None
        })
        with self.assertRaisesRegex(ValueError, "finalization_job_conflict"):
            resolver.resolve(conflicting_request)

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual(first.id, job.evaluation_id)

    def test_existing_inbox_evaluation_cannot_be_replaced(self):
        self._inbox("inbox-evaluation-conflict")
        first = self._evaluation(
            "inbox-evaluation-conflict", evaluation_id="evaluation-original"
        )
        second = self._evaluation(
            "inbox-evaluation-conflict", evaluation_id="evaluation-replacement"
        )
        with self.sessions() as db:
            db.get(
                DeviceRecognitionInbox, "inbox-evaluation-conflict"
            ).evaluation_id = first.id
            db.commit()

        with self.assertRaisesRegex(ValueError, "inbox_evaluation_conflict"):
            self._resolver().resolve(
                self._request("inbox-evaluation-conflict", second.id)
            )

        with self.sessions() as db:
            inbox = db.get(DeviceRecognitionInbox, "inbox-evaluation-conflict")
            self.assertEqual(first.id, inbox.evaluation_id)
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_existing_unfinished_job_survives_capability_gate_change(self):
        self._inbox("existing-pending-job")
        evaluation = self._evaluation("existing-pending-job")
        request = self._request("existing-pending-job", evaluation.id)
        self._resolver().resolve(request)
        replay = RecognitionAssociationRequest(**{
            **request.__dict__, "association_claim_token": None
        })

        result = self._resolver(enabled=False).resolve(replay)

        with self.sessions() as db:
            inbox = db.get(DeviceRecognitionInbox, "existing-pending-job")
            self.assertEqual("finalization_pending", result.track_association_status)
            self.assertEqual("finalization_pending", inbox.track_association_status)
            self.assertEqual(1, db.query(TrackAssociationFinalizationJob).count())

    def test_shadow_confirmed_preserves_assessment_without_job(self):
        self._inbox("shadow-event")
        evaluation = self._evaluation("shadow-event", mode="shadow")

        result = self._resolver().resolve(
            self._request("shadow-event", evaluation.id)
        )

        with self.sessions() as db:
            inbox = db.get(DeviceRecognitionInbox, "shadow-event")
            self.assertEqual("confirmed", result.track_association_status)
            self.assertEqual("confirmed", inbox.track_association_status)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())
            self.assertEqual("confirmed", db.get(TrackAssociationEvaluation, evaluation.id).result)

    def test_spatial_confirmed_evaluation_uses_canonical_inbox_status(self):
        self._inbox("spatial-confirmed")
        evaluation = self._evaluation("spatial-confirmed", result="spatial_confirmed")

        result = self._resolver().resolve(
            self._request(
                "spatial-confirmed", evaluation.id, result="spatial_confirmed"
            )
        )

        with self.sessions() as db:
            inbox = db.get(DeviceRecognitionInbox, "spatial-confirmed")
            self.assertEqual("finalization_pending", result.track_association_status)
            self.assertEqual("finalization_pending", inbox.track_association_status)
            self.assertEqual(
                "spatial_confirmed",
                db.get(TrackAssociationEvaluation, evaluation.id).result,
            )

    def test_policy_version_change_preserves_evaluation_without_job(self):
        self._inbox("policy-changed")
        evaluation = self._evaluation("policy-changed")
        with self.sessions() as db:
            current = db.get(CameraIdentityBindingPolicy, "policy-1")
            current.effective_to = self.now + timedelta(seconds=1)
            db.add(CameraIdentityBindingPolicy(
                id="policy-2",
                camera_id="camera-1",
                mode="enabled",
                algorithm_version="track-association-v1",
                parameter_version="track-association-v1-defaults",
                effective_from=self.now + timedelta(seconds=1),
                qualification_snapshot={},
            ))
            db.commit()

        result = self._resolver().resolve(
            self._request("policy-changed", evaluation.id)
        )

        with self.sessions() as db:
            self.assertEqual("confirmed", result.track_association_status)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())
            self.assertEqual("confirmed", db.get(TrackAssociationEvaluation, evaluation.id).result)

    def test_evaluation_scope_mismatch_rolls_back(self):
        cases = (
            ("store", "store_id", "store-2", "evaluation_store_conflict"),
            ("camera", "camera_id", "camera-2", "evaluation_camera_conflict"),
            ("runtime", "runtime_instance_id", "runtime-2", "evaluation_runtime_conflict"),
        )
        for suffix, field, value, error in cases:
            with self.subTest(scope=suffix):
                event_id = f"scope-{suffix}"
                self._inbox(event_id)
                evaluation = self._evaluation(event_id)
                with self.sessions() as db:
                    setattr(db.get(TrackAssociationEvaluation, evaluation.id), field, value)
                    db.commit()
                with self.assertRaisesRegex(ValueError, error):
                    self._resolver().resolve(
                        self._request(event_id, evaluation.id)
                    )
        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_evaluation_track_target_mismatch_rolls_back(self):
        self._inbox("track-mismatch")
        evaluation = self._evaluation("track-mismatch")
        request = RecognitionAssociationRequest(**{
            **self._request("track-mismatch", evaluation.id).__dict__,
            "track_token": "store-1:camera-1:runtime-1:99",
        })

        with self.assertRaisesRegex(ValueError, "evaluation_track_conflict"):
            self._resolver().resolve(request)

        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_incomplete_track_token_is_not_finalizable(self):
        self._inbox("invalid-token")
        evaluation = self._evaluation("invalid-token")
        invalid = "camera-1:runtime-1:7"
        with self.sessions() as db:
            row = db.get(TrackAssociationEvaluation, evaluation.id)
            row.candidates = [{"track_token": invalid}]
            row.spatial_evidence = {
                "quality_status": "verified",
                "tracks": [{"track_token": invalid, "matched": True}],
            }
            db.commit()
        request = RecognitionAssociationRequest(**{
            **self._request("invalid-token", evaluation.id).__dict__,
            "track_token": invalid,
        })

        result = self._resolver().resolve(request)

        with self.sessions() as db:
            self.assertEqual("confirmed", result.track_association_status)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_unverified_transform_or_spatial_evidence_creates_no_job(self):
        cases = (("transform", "verified", "unverified"), ("spatial", "parsed", "verified"))
        for suffix, quality, transform in cases:
            with self.subTest(suffix=suffix):
                event_id = f"unverified-{suffix}"
                self._inbox(event_id)
                evaluation = self._evaluation(event_id, quality_status=quality)
                self._resolver(transform_status=transform).resolve(
                    self._request(event_id, evaluation.id)
                )
        with self.sessions() as db:
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_non_finalizable_assessments_create_no_job(self):
        for result in ("ambiguous", "no_track_match", "track_conflict"):
            with self.subTest(result=result):
                event_id = f"event-{result}"
                self._inbox(event_id)
                evaluation = self._evaluation(event_id, result=result)
                self._resolver().resolve(
                    self._request(event_id, evaluation.id, result=result)
                )
        with self.sessions() as db:
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_capability_gate_defaults_to_disabled(self):
        self._inbox("capability-event")
        evaluation = self._evaluation("capability-event")
        resolver = ReusableVisitResolver(
            self.sessions,
            clock=lambda: self.now,
            runtime_lease_service=self.lease_service,
            runtime_instance_id="runtime-1",
            transform_status_provider=lambda _camera_id: "verified",
        )

        result = resolver.resolve(self._request("capability-event", evaluation.id))

        with self.sessions() as db:
            self.assertEqual("confirmed", result.track_association_status)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_binder_and_fallback_never_claim_finalization_pending(self):
        for fallback in (False, True):
            event_id = "fallback-pending" if fallback else "binder-pending"
            deadline = self.now - timedelta(seconds=1) if fallback else self.now + timedelta(seconds=5)
            self._inbox(event_id, status="finalization_pending")
            with self.sessions() as db:
                row = db.get(DeviceRecognitionInbox, event_id)
                row.association_deadline_at = deadline
                row.association_claim_token = None
                row.association_claim_expires_at = None
                db.commit()
            worker = RecognitionAssociationWorker(
                self.sessions,
                SimpleNamespace(resolve=lambda _request: None),
                self.lease,
                clock=lambda: self.now,
                fallback=fallback,
                candidate_provider=(None if fallback else lambda _row: TrackAssociationCandidate(
                    status="probable"
                )),
            )
            self.assertEqual(0, worker.run_once())

    def test_normal_pending_remains_claimable(self):
        self._inbox("normal-pending")
        self._clear_claim("normal-pending")
        resolver = SimpleNamespace(resolve=lambda request: SimpleNamespace(
            track_association_status=request.track_association_status
        ))
        worker = RecognitionAssociationWorker(
            self.sessions,
            resolver,
            self.lease,
            clock=lambda: self.now,
            candidate_provider=lambda _row: TrackAssociationCandidate(
                status="no_track_match"
            ),
        )
        self.assertEqual(1, worker.run_once())

    def test_non_finalizable_worker_claim_is_cleared_by_t1(self):
        self._inbox("shadow-worker")
        self._clear_claim("shadow-worker")
        worker = RecognitionAssociationWorker(
            self.sessions,
            self._resolver(),
            self.lease,
            clock=lambda: self.now,
            runtime_instance_id="runtime-1",
            audit_service=TrackAssociationAuditService(
                self.sessions, clock=lambda: self.now
            ),
            candidate_provider=lambda _row: TrackAssociationCandidate(
                status="confirmed",
                camera_id="camera-1",
                track_token=self.track_token,
                method="verified_face_spatial_evidence",
                policy_version_id="policy-1",
                policy_mode="shadow",
                candidates=({"track_token": self.track_token},),
                spatial_evidence={
                    "quality_status": "verified",
                    "tracks": [{"track_token": self.track_token, "matched": True}],
                },
            ),
        )

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            inbox = db.get(DeviceRecognitionInbox, "shadow-worker")
            self.assertIsNone(inbox.association_claim_token)
            self.assertEqual("confirmed", inbox.track_association_status)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_lease_failure_at_start_rolls_back_all_t1_facts(self):
        self._inbox("lease-start")
        evaluation = self._evaluation("lease-start")

        with self.assertRaises(RuntimeLeaseUnavailable):
            self._resolver(
                lease_service=_CountingLeaseService(fail_on=1)
            ).resolve(self._request("lease-start", evaluation.id))

        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_lease_owner_mismatch_rolls_back_t1(self):
        self._inbox("lease-owner")
        evaluation = self._evaluation("lease-owner")
        request = RecognitionAssociationRequest(**{
            **self._request("lease-owner", evaluation.id).__dict__,
            "runtime_owner_id": "other-owner",
        })

        with self.assertRaises(RuntimeLeaseUnavailable):
            self._resolver().resolve(request)

        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_lost_association_claim_rolls_back_before_business_facts(self):
        self._inbox("claim-lost")
        evaluation = self._evaluation("claim-lost")
        request = RecognitionAssociationRequest(**{
            **self._request("claim-lost", evaluation.id).__dict__,
            "association_claim_token": "stale-claim",
        })

        with self.assertRaisesRegex(ValueError, "association_claim_lost"):
            self._resolver().resolve(request)

        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())
            self.assertEqual(
                "claim-1", db.get(DeviceRecognitionInbox, "claim-lost").association_claim_token
            )

    def test_lease_failure_before_commit_rolls_back_job_and_visit(self):
        self._inbox("lease-commit")
        evaluation = self._evaluation("lease-commit")

        with self.assertRaises(RuntimeLeaseUnavailable):
            self._resolver(
                lease_service=_CountingLeaseService(fail_on=2)
            ).resolve(self._request("lease-commit", evaluation.id))

        with self.sessions() as db:
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())
            self.assertEqual(
                "claim-1", db.get(DeviceRecognitionInbox, "lease-commit").association_claim_token
            )

    def test_stale_runtime_instance_creates_no_job(self):
        self._inbox("stale-runtime")
        evaluation = self._evaluation("stale-runtime")

        result = self._resolver(runtime_instance_id="runtime-2").resolve(
            self._request("stale-runtime", evaluation.id)
        )

        with self.sessions() as db:
            self.assertEqual("confirmed", result.track_association_status)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())

    def test_probable_has_safe_default_without_formal_policy_contract(self):
        self._inbox("probable-event")
        evaluation = self._evaluation("probable-event", result="probable")

        result = self._resolver().resolve(
            self._request("probable-event", evaluation.id, result="probable")
        )

        with self.sessions() as db:
            self.assertEqual("probable", result.track_association_status)
            self.assertEqual(0, db.query(TrackAssociationFinalizationJob).count())
            self.assertEqual(0, db.query(TrackIdentityBinding).count())

    def _finalizer(
        self,
        *,
        transform_status: str = "verified",
        buffer: TrackSnapshotBuffer | None = None,
        cache: CameraTrackIdentityCache | None = None,
        runtime_instance_id: str = "runtime-1",
        lease_service=None,
    ):
        buffer = buffer or TrackSnapshotBuffer(clock=lambda: self.now)
        cache = cache or CameraTrackIdentityCache(clock=lambda: self.now)
        worker = TrackAssociationFinalizationWorker(
            self.sessions,
            lease_service=lease_service or self.lease_service,
            lease=self.lease,
            runtime_instance_id=runtime_instance_id,
            track_snapshot_buffer=buffer,
            track_identity_cache=cache,
            transform_status_provider=lambda _camera_id: transform_status,
            clock=lambda: self.now,
        )
        return worker, buffer, cache

    def _publish_track(
        self,
        buffer: TrackSnapshotBuffer,
        *,
        track_id: int = 7,
        camera_id: str = "camera-1",
    ) -> None:
        buffer.publish(TrackSnapshot(
            store_id="store-1",
            camera_id=camera_id,
            runtime_id="runtime-1",
            track_id=track_id,
            track_token=f"store-1:{camera_id}:runtime-1:{track_id}",
            observed_at=self.now,
            bbox=(0.2, 0.1, 0.6, 0.9),
            confidence=0.9,
            role="customer",
            stable_frames=3,
            is_frame_clipped=False,
        ))

    def _create_pending_job(self, event_id: str) -> TrackAssociationEvaluation:
        self._inbox(event_id)
        evaluation = self._evaluation(event_id)
        self._resolver().resolve(self._request(event_id, evaluation.id))
        return evaluation

    def test_finalizer_applies_binding_projects_cache_and_completes(self):
        evaluation = self._create_pending_job("finalize-success")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            binding = db.query(TrackIdentityBinding).one()
            inbox = db.get(DeviceRecognitionInbox, "finalize-success")
            self.assertEqual("succeeded", job.status)
            self.assertEqual("applied", job.application_status)
            self.assertEqual("succeeded", job.cache_projection_status)
            self.assertEqual("succeeded", job.backfill_status)
            self.assertEqual(binding.id, job.binding_id)
            self.assertEqual(binding.id, inbox.binding_id)
            self.assertEqual("confirmed", inbox.track_association_status)
            self.assertEqual(evaluation.id, binding.evaluation_id)
            self.assertEqual(self.now, binding.valid_from)
        resolved = cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=self.track_token,
        )
        self.assertEqual("member-1", resolved["member_id"])

    def test_finalizer_skips_when_exact_track_is_no_longer_active(self):
        self._create_pending_job("finalize-track-lost")
        worker, _buffer, _cache = self._finalizer()

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            inbox = db.get(DeviceRecognitionInbox, "finalize-track-lost")
            self.assertEqual("succeeded", job.status)
            self.assertEqual("skipped", job.application_status)
            self.assertEqual("track_no_longer_active", job.application_reason)
            self.assertEqual("confirmed", inbox.track_association_status)
            self.assertEqual(0, db.query(TrackIdentityBinding).count())

    def test_finalizer_revalidates_policy_before_binding(self):
        self._create_pending_job("finalize-policy-change")
        with self.sessions() as db:
            db.get(CameraIdentityBindingPolicy, "policy-1").mode = "rolled_back"
            db.commit()
        worker, buffer, _cache = self._finalizer()
        self._publish_track(buffer)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("skipped", job.application_status)
            self.assertEqual("policy_not_enabled", job.application_reason)
            self.assertEqual(0, db.query(TrackIdentityBinding).count())

    def test_finalizer_revalidates_transform_and_spatial_quality(self):
        cases = (
            ("transform", "verified", "unverified", "transform_unverified"),
            ("spatial", "parsed", "verified", "spatial_evidence_unverified"),
        )
        for suffix, quality, transform, expected in cases:
            with self.subTest(suffix=suffix):
                event_id = f"t2-{suffix}"
                evaluation = self._create_pending_job(event_id)
                if quality != "verified":
                    with self.sessions() as db:
                        row = db.get(TrackAssociationEvaluation, evaluation.id)
                        row.spatial_evidence = {
                            **row.spatial_evidence,
                            "quality_status": quality,
                        }
                        db.commit()
                worker, buffer, _cache = self._finalizer(
                    transform_status=transform
                )
                self._publish_track(buffer)
                worker.run_once()
                with self.sessions() as db:
                    job = db.query(TrackAssociationFinalizationJob).filter_by(
                        recognition_event_id=event_id
                    ).one()
                    self.assertEqual("skipped", job.application_status)
                    self.assertEqual(expected, job.application_reason)
                    self.assertEqual(0, db.query(TrackIdentityBinding).count())

    def test_finalizer_skips_job_from_stale_runtime_instance(self):
        self._create_pending_job("t2-stale-runtime")
        worker, buffer, _cache = self._finalizer(runtime_instance_id="runtime-2")
        self._publish_track(buffer)

        worker.run_once()

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("skipped", job.application_status)
            self.assertEqual("stale_runtime_instance", job.application_reason)
            self.assertEqual(0, db.query(TrackIdentityBinding).count())

    def test_finalizer_lease_fence_loss_creates_no_binding(self):
        self._create_pending_job("t2-lease-lost")
        with self.sessions() as db:
            row = db.get(IdentityAssociationRuntimeLease, "store-1")
            row.lease_token = "replacement-token"
            row.lease_generation += 1
            db.commit()
        worker, buffer, _cache = self._finalizer()
        self._publish_track(buffer)

        with self.assertRaises(RuntimeLeaseUnavailable):
            worker.run_once()

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("pending", job.status)
            self.assertEqual(0, db.query(TrackIdentityBinding).count())

    def test_different_member_same_track_conflicts_and_invalidates_cache(self):
        self._create_pending_job("member-a-event")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer)
        worker.run_once()
        with self.sessions() as db:
            first_binding_id = db.query(TrackIdentityBinding).one().id
            db.add(Member(id="member-2", name="Member Two", tier="silver"))
            db.commit()
        self._inbox("member-b-event")
        with self.sessions() as db:
            db.get(DeviceRecognitionInbox, "member-b-event").member_id = "member-2"
            db.commit()
        evaluation = self._evaluation("member-b-event")
        request = RecognitionAssociationRequest(**{
            **self._request("member-b-event", evaluation.id).__dict__,
            "member_id": "member-2",
            "member_tier": "silver",
        })
        self._resolver().resolve(request)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            conflict_job = db.query(TrackAssociationFinalizationJob).filter_by(
                recognition_event_id="member-b-event"
            ).one()
            first_binding = db.get(TrackIdentityBinding, first_binding_id)
            self.assertEqual("conflict", conflict_job.application_status)
            self.assertEqual("different_member_same_track", conflict_job.application_reason)
            self.assertEqual("succeeded", conflict_job.cache_projection_status)
            self.assertEqual("track_conflict", first_binding.status)
            self.assertIsNotNone(first_binding.valid_to)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())
        self.assertIsNone(cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=self.track_token,
        ))

    def test_conflict_cache_failure_retries_cleanup_only(self):
        class FlakyInvalidationCache(CameraTrackIdentityCache):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.failures = 1

            def invalidate_exact(self, **kwargs):
                if self.failures:
                    self.failures -= 1
                    raise OSError("temporary invalidation failure")
                return super().invalidate_exact(**kwargs)

        cache = FlakyInvalidationCache(clock=lambda: self.now)
        self._create_pending_job("conflict-cleanup-a")
        worker, buffer, _ = self._finalizer(cache=cache)
        self._publish_track(buffer)
        worker.run_once()
        with self.sessions() as db:
            db.add(Member(id="member-cleanup-b", name="Cleanup B", tier="silver"))
            db.commit()
        self._inbox("conflict-cleanup-b")
        with self.sessions() as db:
            db.get(DeviceRecognitionInbox, "conflict-cleanup-b").member_id = (
                "member-cleanup-b"
            )
            db.commit()
        evaluation = self._evaluation("conflict-cleanup-b")
        request = RecognitionAssociationRequest(**{
            **self._request("conflict-cleanup-b", evaluation.id).__dict__,
            "member_id": "member-cleanup-b",
            "member_tier": "silver",
        })
        self._resolver().resolve(request)

        self.assertEqual(1, worker.run_once())
        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).filter_by(
                recognition_event_id="conflict-cleanup-b"
            ).one()
            binding = db.query(TrackIdentityBinding).one()
            self.assertEqual("pending", job.status)
            self.assertEqual("conflict", job.application_status)
            self.assertEqual("failed", job.cache_projection_status)
            self.assertEqual("track_conflict", binding.status)
            self.assertIsNone(job.binding_id)
        self.now += timedelta(seconds=0.5)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).filter_by(
                recognition_event_id="conflict-cleanup-b"
            ).one()
            self.assertEqual("succeeded", job.status)
            self.assertEqual("conflict", job.application_status)
            self.assertEqual("succeeded", job.cache_projection_status)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())

    def test_same_identity_new_evaluation_is_compatible_noop(self):
        self._create_pending_job("identity-first")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer)
        worker.run_once()
        with self.sessions() as db:
            original = db.query(TrackIdentityBinding).one()
            original_binding_id = original.id
            original_expiry = original.expires_at
        self._create_pending_job("identity-reconfirmed")

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).filter_by(
                recognition_event_id="identity-reconfirmed"
            ).one()
            binding = db.query(TrackIdentityBinding).one()
            inbox = db.get(DeviceRecognitionInbox, "identity-reconfirmed")
            self.assertEqual("skipped", job.application_status)
            self.assertEqual("same_identity_already_bound", job.application_reason)
            self.assertIsNone(job.binding_id)
            self.assertEqual("skipped", job.cache_projection_status)
            self.assertEqual("skipped", job.backfill_status)
            self.assertEqual(original_binding_id, binding.id)
            self.assertEqual(original_expiry, binding.expires_at)
            self.assertEqual("confirmed", inbox.track_association_status)
            self.assertIsNone(inbox.binding_id)
        self.assertEqual(0, len(cache.history))

    def test_same_member_same_camera_runtime_two_tracks_conflicts(self):
        self._create_pending_job("track-one")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer, track_id=7)
        worker.run_once()
        second_token = "store-1:camera-1:runtime-1:8"
        self._inbox("track-two")
        evaluation = self._evaluation("track-two", track_token=second_token)
        self._resolver().resolve(self._request(
            "track-two", evaluation.id, track_token=second_token
        ))
        self._publish_track(buffer, track_id=8)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).filter_by(
                recognition_event_id="track-two"
            ).one()
            binding = db.query(TrackIdentityBinding).one()
            self.assertEqual("conflict", job.application_status)
            self.assertEqual("same_member_multiple_tracks", job.application_reason)
            self.assertEqual("track_conflict", binding.status)
            self.assertEqual("track_conflict", db.get(
                DeviceRecognitionInbox, "track-two"
            ).track_association_status)
        self.assertIsNone(cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=self.track_token,
        ))

    def test_cache_projection_failure_retries_without_reapplying_binding(self):
        class FlakyCache(CameraTrackIdentityCache):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.failures = 1

            def bind(self, binding):
                if self.failures:
                    self.failures -= 1
                    raise OSError("temporary cache failure")
                return super().bind(binding)

        self._create_pending_job("cache-retry")
        cache = FlakyCache(clock=lambda: self.now)
        worker, buffer, _ = self._finalizer(cache=cache)
        self._publish_track(buffer)

        self.assertEqual(1, worker.run_once())
        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            binding_id = job.binding_id
            self.assertEqual("binding_committed", job.status)
            self.assertEqual("applied", job.application_status)
            self.assertEqual("failed", job.cache_projection_status)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())
        self.now += timedelta(seconds=0.5)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("succeeded", job.status)
            self.assertEqual(binding_id, job.binding_id)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())

    def test_backfill_failure_retries_after_binding_and_cache_commit(self):
        self._create_pending_job("backfill-retry")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer)
        real_store = worker.behavior_events
        worker.behavior_events = SimpleNamespace(
            attach_identity_to_track=lambda **_kwargs: (_ for _ in ()).throw(
                OSError("temporary backfill failure")
            )
        )

        self.assertEqual(1, worker.run_once())
        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            binding_id = job.binding_id
            self.assertEqual("binding_committed", job.status)
            self.assertEqual("succeeded", job.cache_projection_status)
            self.assertEqual("failed", job.backfill_status)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())
        self.assertEqual("member-1", cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=self.track_token,
        )["member_id"])
        worker.behavior_events = real_store
        self.now += timedelta(seconds=0.5)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("succeeded", job.status)
            self.assertEqual(binding_id, job.binding_id)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())

    def test_same_member_bindings_on_different_cameras_are_allowed(self):
        self._create_pending_job("camera-one")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer)
        worker.run_once()
        with self.sessions() as db:
            db.add(CameraIdentityBindingPolicy(
                id="policy-camera-2",
                camera_id="camera-2",
                mode="enabled",
                algorithm_version="track-association-v1",
                parameter_version="track-association-v1-defaults",
                effective_from=self.now,
                qualification_snapshot={},
            ))
            db.commit()
        second_token = "store-1:camera-2:runtime-1:7"
        self._inbox("camera-two")
        evaluation = self._evaluation(
            "camera-two", track_token=second_token, camera_id="camera-2"
        )
        self._resolver().resolve(self._request(
            "camera-two",
            evaluation.id,
            track_token=second_token,
            camera_id="camera-2",
        ))
        self._publish_track(buffer, camera_id="camera-2")

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            bindings = db.query(TrackIdentityBinding).order_by(
                TrackIdentityBinding.camera_id
            ).all()
            self.assertEqual(2, len(bindings))
            self.assertTrue(all(row.status == "confirmed" for row in bindings))
        self.assertEqual("member-1", cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-2",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=second_token,
        )["member_id"])

    def test_pending_job_defensively_reuses_exact_persisted_application(self):
        evaluation = self._create_pending_job("defensive-recovery")
        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            visit = db.get(Visit, job.visit_id)
            db.add(TrackIdentityBinding(
                id="binding-defensive",
                recognition_event_id=job.recognition_event_id,
                evaluation_id=evaluation.id,
                store_id="store-1",
                camera_id="camera-1",
                runtime_owner_id="owner-1",
                runtime_instance_id="runtime-1",
                track_id=7,
                track_token=self.track_token,
                member_id="member-1",
                member_tier="gold",
                visit_id=visit.id,
                binding_level="confirmed",
                status="confirmed",
                identity_source="verified_face_spatial_evidence",
                identity_confidence=0.95,
                policy_version_id="policy-1",
                algorithm_version="track-association-v1",
                parameter_version="track-association-v1-defaults",
                valid_from=self.now,
                expires_at=self.now + timedelta(seconds=2),
                created_at=self.now,
                updated_at=self.now,
            ))
            db.commit()
        worker, buffer, _cache = self._finalizer()
        self._publish_track(buffer)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("succeeded", job.status)
            self.assertEqual("applied", job.application_status)
            self.assertEqual("binding-defensive", job.binding_id)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())

    def test_applied_job_missing_binding_reference_fail_stops_with_claim(self):
        self._create_pending_job("broken-applied")
        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            job.application_status = "applied"
            job.status = "binding_committed"
            job.binding_id = None
            db.commit()
        worker, _buffer, _cache = self._finalizer()

        with self.assertRaises(PersistentFinalizationInvariantViolation):
            worker.run_once()

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("processing", job.status)
            self.assertEqual("applied", job.application_status)
            self.assertIsNotNone(job.claim_token)
            self.assertIsNotNone(job.claim_expires_at)
            self.assertIn(
                "finalization_application_state_inconsistent", job.last_error
            )
            self.assertEqual(
                "finalization_pending",
                db.get(DeviceRecognitionInbox, "broken-applied").track_association_status,
            )

    def test_expired_processing_claim_is_taken_over(self):
        self._create_pending_job("claim-takeover")
        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            job.status = "processing"
            job.claim_token = "crashed-worker"
            job.locked_at = self.now - timedelta(seconds=31)
            job.claim_expires_at = self.now - timedelta(seconds=1)
            db.commit()
        worker, buffer, _cache = self._finalizer()
        self._publish_track(buffer)

        self.assertEqual(1, worker.run_once())

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            self.assertEqual("succeeded", job.status)
            self.assertEqual(1, db.query(TrackIdentityBinding).count())

    def test_second_finalizer_cannot_take_live_claim(self):
        self._create_pending_job("exclusive-claim")
        first, buffer, cache = self._finalizer()
        second, _same_buffer, _same_cache = self._finalizer(
            buffer=buffer, cache=cache
        )
        self._publish_track(buffer)

        claim = first._claim_one()
        self.assertIsNotNone(claim)
        self.assertEqual(0, second.run_once())
        first._process_claim(claim)

        with self.sessions() as db:
            self.assertEqual(1, db.query(TrackIdentityBinding).count())
            self.assertEqual(
                "succeeded", db.query(TrackAssociationFinalizationJob).one().status
            )

    def test_finalizer_backfills_exact_behavior_provenance(self):
        token = self.track_token
        behavior_store = BehaviorEventStore(self.sessions, store_id="store-1")
        behavior = behavior_store.record_behavior(BehaviorEventInput(
            event_key="dwell:finalizer-backfill",
            event_type="merchandise_dwell",
            assessment="confirmed",
            camera_id="camera-1",
            customer_track_id=7,
            customer_track_token=token,
            started_at=self.now - timedelta(seconds=5),
            ended_at=self.now - timedelta(seconds=4),
        ))
        evaluation = self._create_pending_job("backfill-finalizer")
        worker, buffer, _cache = self._finalizer()
        self._publish_track(buffer)

        worker.run_once()

        with self.sessions() as db:
            job = db.query(TrackAssociationFinalizationJob).one()
            stored = db.get(type(behavior), behavior.id)
            self.assertEqual(job.binding_id, stored.binding_id)
            self.assertEqual(evaluation.id, stored.evaluation_id)
            self.assertEqual("member-1", stored.member_id)
            self.assertEqual(job.visit_id, stored.visit_id)
            self.assertIsNone(stored.entry_event_id)

    def test_heartbeat_extends_binding_and_cache_without_visit_activity(self):
        self._create_pending_job("heartbeat-event")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer)
        worker.run_once()
        with self.sessions() as db:
            binding = db.query(TrackIdentityBinding).one()
            original_expiry = binding.expires_at
            visit = db.get(Visit, binding.visit_id)
            original_last_seen = visit.last_seen_at
        self.now += timedelta(seconds=1)
        self._publish_track(buffer)
        heartbeat = BindingLifecycleHeartbeat(
            self.sessions,
            lease_service=self.lease_service,
            lease=self.lease,
            runtime_instance_id="runtime-1",
            track_snapshot_buffer=buffer,
            track_identity_cache=cache,
            track_lost_seconds=2.0,
            heartbeat_interval_seconds=1.0,
            clock=lambda: self.now,
        )

        self.assertEqual(1, heartbeat.run_once())

        with self.sessions() as db:
            binding = db.query(TrackIdentityBinding).one()
            visit = db.get(Visit, binding.visit_id)
            self.assertGreater(binding.expires_at, original_expiry)
            self.assertEqual(original_last_seen, visit.last_seen_at)
            durable_expiry = binding.expires_at
        resolved = cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=self.track_token,
        )
        self.assertEqual(durable_expiry, resolved["expires_at"])

    def test_visit_activity_refresh_does_not_extend_durable_binding(self):
        self._create_pending_job("visit-activity-binding")
        worker, buffer, _cache = self._finalizer()
        self._publish_track(buffer)
        worker.run_once()
        with self.sessions() as db:
            binding = db.query(TrackIdentityBinding).one()
            original_expiry = binding.expires_at
            activity = VisitActivity(
                store_id=binding.store_id,
                camera_id=binding.camera_id,
                runtime_instance_id=binding.runtime_instance_id,
                track_id=binding.track_id,
                track_token=binding.track_token,
                binding_id=binding.id,
                member_id=binding.member_id,
                canonical_visit_id=binding.visit_id,
                observed_at=self.now + timedelta(seconds=1),
            )

        result = VisitActivityService(self.sessions).refresh_batch(
            (activity,), now=activity.observed_at
        )

        self.assertEqual({"updated": 1}, result)
        with self.sessions() as db:
            binding = db.query(TrackIdentityBinding).one()
            self.assertEqual(original_expiry, binding.expires_at)

    def test_heartbeat_never_revives_expired_binding(self):
        self._create_pending_job("heartbeat-expired")
        worker, buffer, cache = self._finalizer()
        self._publish_track(buffer)
        worker.run_once()
        with self.sessions() as db:
            original_expiry = db.query(TrackIdentityBinding).one().expires_at
        self.now += timedelta(seconds=3)
        self._publish_track(buffer)
        heartbeat = BindingLifecycleHeartbeat(
            self.sessions,
            lease_service=self.lease_service,
            lease=self.lease,
            runtime_instance_id="runtime-1",
            track_snapshot_buffer=buffer,
            track_identity_cache=cache,
            track_lost_seconds=2.0,
            heartbeat_interval_seconds=1.0,
            clock=lambda: self.now,
        )

        self.assertEqual(0, heartbeat.run_once())

        with self.sessions() as db:
            self.assertEqual(
                original_expiry, db.query(TrackIdentityBinding).one().expires_at
            )
        self.assertIsNone(cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=self.track_token,
        ))


if __name__ == "__main__":
    unittest.main()
