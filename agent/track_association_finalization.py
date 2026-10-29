"""Durable Track Association application and lifecycle workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from threading import Event, Thread, current_thread
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.behavior_events import BehaviorEventStore
from agent.camera_track_identity import (
    CameraTrackIdentityCache,
    TrackIdentityBinding as CachedTrackIdentityBinding,
    TrackSnapshot,
    TrackSnapshotBuffer,
)
from agent.identity_runtime_lease import (
    IdentityAssociationRuntimeLeaseService,
    RuntimeLease,
    RuntimeLeaseUnavailable,
)
from agent.models import (
    CameraIdentityBindingPolicy,
    DeviceRecognitionInbox,
    Member,
    TrackAssociationEvaluation,
    TrackAssociationFinalizationJob,
    TrackIdentityBinding,
    Visit,
    new_uuid,
)
from agent.track_association_policy import is_confirmed_assessment
from agent.visit_merge_service import resolve_canonical_visit_id


logger = logging.getLogger(__name__)


class PersistentFinalizationInvariantViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class _Claim:
    job_id: str
    token: str


class TrackAssociationFinalizationWorker:
    """Drive one Finalization Job through every post-assessment step."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        lease_service: IdentityAssociationRuntimeLeaseService,
        lease: RuntimeLease,
        runtime_instance_id: str,
        track_snapshot_buffer: TrackSnapshotBuffer,
        track_identity_cache: CameraTrackIdentityCache,
        transform_status_provider: Callable[[str], str],
        track_lost_seconds: float = 2.0,
        poll_interval_seconds: float = 0.25,
        claim_lease_seconds: float = 30.0,
        retry_base_seconds: float = 0.5,
        retry_max_seconds: float = 30.0,
        clock: Callable[[], datetime] = datetime.utcnow,
        integrity_fatal_callback: Optional[Callable[[BaseException], None]] = None,
        lease_lost_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not runtime_instance_id:
            raise ValueError("runtime_instance_id is required")
        if min(
            track_lost_seconds,
            poll_interval_seconds,
            claim_lease_seconds,
            retry_base_seconds,
            retry_max_seconds,
        ) <= 0:
            raise ValueError("Finalizer timing values must be positive")
        self.session_factory = session_factory
        self.lease_service = lease_service
        self.lease = lease
        self.runtime_instance_id = runtime_instance_id
        self.track_snapshot_buffer = track_snapshot_buffer
        self.track_identity_cache = track_identity_cache
        self.transform_status_provider = transform_status_provider
        self.track_lost = timedelta(seconds=track_lost_seconds)
        self.poll_interval_seconds = poll_interval_seconds
        self.claim_lease = timedelta(seconds=claim_lease_seconds)
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.clock = clock
        self.behavior_events = BehaviorEventStore(session_factory)
        self.integrity_fatal_callback = integrity_fatal_callback
        self.lease_lost_callback = lease_lost_callback
        self._stop = Event()
        self._thread: Optional[Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run_forever,
            name="track-association-finalizer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self.request_stop()
        thread = self._thread
        if thread is None:
            return
        if thread is current_thread():
            return
        thread.join(timeout=max(2.0, self.poll_interval_seconds * 4))
        if thread.is_alive():
            raise RuntimeError("Track Association Finalizer did not stop")
        self._thread = None

    def request_stop(self) -> None:
        self._stop.set()

    def run_once(self) -> int:
        claim = self._claim_one()
        if claim is None:
            return 0
        try:
            self._process_claim(claim)
            return 1
        except PersistentFinalizationInvariantViolation as error:
            self._record_invariant(claim, str(error))
            raise
        except RuntimeLeaseUnavailable:
            raise
        except Exception as error:
            try:
                self._record_retry(claim, error)
            except PersistentFinalizationInvariantViolation as invariant:
                self._record_invariant(claim, str(invariant))
                raise
            return 1

    def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except PersistentFinalizationInvariantViolation as error:
                if self.integrity_fatal_callback is not None:
                    self.integrity_fatal_callback(error)
                return
            except RuntimeLeaseUnavailable:
                if self.lease_lost_callback is not None:
                    self.lease_lost_callback("runtime_lease_lost")
                return
            except Exception:
                logger.exception("Track Association Finalizer loop failed")
                processed = 0
            if not processed:
                self._stop.wait(self.poll_interval_seconds)

    def _claim_one(self) -> Optional[_Claim]:
        db = self.session_factory()
        try:
            self._begin(db)
            now = self._now()
            row = (
                db.query(TrackAssociationFinalizationJob)
                .filter(
                    or_(
                        (
                            TrackAssociationFinalizationJob.status.in_(
                                ("pending", "binding_committed")
                            )
                            & or_(
                                TrackAssociationFinalizationJob.next_retry_at.is_(None),
                                TrackAssociationFinalizationJob.next_retry_at <= now,
                            )
                        ),
                        (
                            (TrackAssociationFinalizationJob.status == "processing")
                            & (
                                TrackAssociationFinalizationJob.claim_expires_at
                                <= now
                            )
                        ),
                    )
                )
                .order_by(
                    TrackAssociationFinalizationJob.created_at,
                    TrackAssociationFinalizationJob.id,
                )
                .first()
            )
            if row is None:
                db.commit()
                return None
            self._validate_lease(db)
            token = uuid4().hex
            row.status = "processing"
            row.claim_token = token
            row.locked_at = now
            row.claim_expires_at = now + self.claim_lease
            row.attempts = int(row.attempts or 0) + 1
            row.next_retry_at = None
            self._validate_lease(db)
            job_id = row.id
            db.commit()
            return _Claim(job_id, token)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _process_claim(self, claim: _Claim) -> None:
        state = self._job_state(claim)
        if state.application_status == "pending":
            token_scope = self._parse_track_token(state.track_token)
            snapshot = self.track_snapshot_buffer.latest_exact(
                store_id=token_scope[0],
                camera_id=token_scope[1],
                runtime_instance_id=token_scope[2],
                track_token=state.track_token,
            )
            self._apply_binding(claim, snapshot)
            state = self._job_state(claim, allow_terminal=True)
        if state.status == "succeeded":
            return
        if state.application_status == "conflict":
            self._complete_conflict_cleanup(claim)
            return
        if state.application_status != "applied" or not state.binding_id:
            raise PersistentFinalizationInvariantViolation(
                "finalization_application_state_inconsistent"
            )
        self._validate_applied_binding(claim)
        state = self._job_state(claim)
        if state.cache_projection_status != "succeeded":
            self._renew_claim(claim)
            self._project_cache(claim)
        state = self._job_state(claim)
        if state.backfill_status != "succeeded":
            self._renew_claim(claim)
            self._backfill_behavior(claim)
        self._complete_applied(claim)

    def _apply_binding(
        self, claim: _Claim, latest_snapshot: Optional[TrackSnapshot]
    ) -> None:
        db = self.session_factory()
        try:
            self._begin(db)
            now = self._now()
            job = self._claimed_job(db, claim)
            self._validate_lease(db)
            evaluation, inbox, visit, member, policy, track_id = self._validate_t2_facts(
                db, job, now
            )
            skip_reason = self._application_gate_reason(
                evaluation=evaluation,
                inbox=inbox,
                visit=visit,
                member=member,
                policy=policy,
            )
            if skip_reason is not None:
                self._terminal_skip(job, inbox, evaluation, skip_reason)
                self._validate_lease(db)
                db.commit()
                return
            if (
                latest_snapshot is None
                or latest_snapshot.store_id != evaluation.store_id
                or latest_snapshot.camera_id != evaluation.camera_id
                or latest_snapshot.runtime_id != evaluation.runtime_instance_id
                or latest_snapshot.track_id != track_id
                or latest_snapshot.track_token != job.track_token
                or latest_snapshot.observed_at + self.track_lost <= now
            ):
                self._terminal_skip(
                    job, inbox, evaluation, "track_no_longer_active"
                )
                self._validate_lease(db)
                db.commit()
                return
            exact_application = db.query(TrackIdentityBinding).filter(
                TrackIdentityBinding.evaluation_id == job.evaluation_id,
                TrackIdentityBinding.recognition_event_id == job.recognition_event_id,
            ).all()
            if len(exact_application) > 1:
                raise PersistentFinalizationInvariantViolation(
                    "duplicate_exact_binding_application"
                )
            expected = self._binding_values(
                evaluation=evaluation,
                inbox=inbox,
                visit=visit,
                member=member,
                policy=policy,
                track_id=track_id,
                track_token=job.track_token,
                expires_at=latest_snapshot.observed_at + self.track_lost,
                created_at=now,
            )
            if exact_application:
                binding = exact_application[0]
                self._assert_binding_matches(binding, expected)
                if not self._is_active(binding, now):
                    raise PersistentFinalizationInvariantViolation(
                        "idempotent_binding_lifecycle_terminated"
                    )
                self._mark_binding_committed(job, binding, now)
                self._validate_lease(db)
                db.commit()
                return
            unterminated = db.query(TrackIdentityBinding).filter(
                TrackIdentityBinding.track_token == job.track_token,
                TrackIdentityBinding.status == "confirmed",
                TrackIdentityBinding.valid_to.is_(None),
            ).all()
            if any(not self._is_active(binding, now) for binding in unterminated):
                raise PersistentFinalizationInvariantViolation(
                    "track_token_lifecycle_invariant_violation"
                )
            active_exact = [binding for binding in unterminated if self._is_active(binding, now)]
            if active_exact:
                existing = active_exact[0]
                if existing.member_id != member.id:
                    self._commit_conflict(
                        db,
                        job=job,
                        bindings=active_exact,
                        reason="different_member_same_track",
                        conflict_at=now,
                    )
                    self._validate_lease(db)
                    db.commit()
                    return
                existing_visit_id = self._canonical_visit_id(db, existing.visit_id)
                if existing_visit_id != visit.id:
                    raise PersistentFinalizationInvariantViolation(
                        "same_track_member_visit_invariant_violation"
                    )
                self._terminal_skip(
                    job, inbox, evaluation, "same_identity_already_bound"
                )
                self._validate_lease(db)
                db.commit()
                return
            member_track_conflicts = db.query(TrackIdentityBinding).filter(
                TrackIdentityBinding.store_id == evaluation.store_id,
                TrackIdentityBinding.camera_id == evaluation.camera_id,
                TrackIdentityBinding.runtime_instance_id
                == evaluation.runtime_instance_id,
                TrackIdentityBinding.member_id == member.id,
                TrackIdentityBinding.track_token != job.track_token,
                TrackIdentityBinding.status == "confirmed",
                TrackIdentityBinding.valid_to.is_(None),
                TrackIdentityBinding.expires_at > now,
            ).all()
            if member_track_conflicts:
                self._commit_conflict(
                    db,
                    job=job,
                    bindings=member_track_conflicts,
                    reason="same_member_multiple_tracks",
                    conflict_at=now,
                )
                self._validate_lease(db)
                db.commit()
                return
            binding = TrackIdentityBinding(id=new_uuid(), **expected)
            db.add(binding)
            db.flush()
            self._mark_binding_committed(job, binding, now)
            self._validate_lease(db)
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise PersistentFinalizationInvariantViolation(
                "binding_uniqueness_invariant_violation"
            ) from error
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _validate_t2_facts(
        self,
        db: Session,
        job: TrackAssociationFinalizationJob,
        now: datetime,
    ) -> tuple[
        TrackAssociationEvaluation,
        DeviceRecognitionInbox,
        Visit,
        Member,
        Optional[CameraIdentityBindingPolicy],
        int,
    ]:
        evaluation = db.get(TrackAssociationEvaluation, job.evaluation_id)
        inbox = db.get(DeviceRecognitionInbox, job.recognition_event_id)
        if evaluation is None or inbox is None:
            raise PersistentFinalizationInvariantViolation(
                "job_evaluation_inbox_missing"
            )
        if (
            evaluation.recognition_event_id != job.recognition_event_id
            or inbox.evaluation_id != job.evaluation_id
            or inbox.finalization_job_id != job.id
            or inbox.matched_track_token != job.track_token
        ):
            raise PersistentFinalizationInvariantViolation(
                "job_evaluation_inbox_provenance_mismatch"
            )
        store_id, camera_id, runtime_instance_id, track_id = self._parse_track_token(
            job.track_token
        )
        target_token = self._evaluation_track_token(evaluation)
        if (
            target_token != job.track_token
            or evaluation.store_id != store_id
            or evaluation.camera_id != camera_id
            or evaluation.runtime_instance_id != runtime_instance_id
            or inbox.store_id != store_id
            or inbox.runtime_instance_id != runtime_instance_id
        ):
            raise PersistentFinalizationInvariantViolation(
                "job_evaluation_track_scope_mismatch"
            )
        if not inbox.matched_visit_id:
            raise PersistentFinalizationInvariantViolation(
                "job_inbox_visit_provenance_missing"
            )
        job_canonical = self._canonical_visit_id(db, job.visit_id)
        inbox_canonical = self._canonical_visit_id(db, inbox.matched_visit_id)
        if job_canonical != inbox_canonical:
            raise PersistentFinalizationInvariantViolation(
                "job_inbox_visit_provenance_mismatch"
            )
        visit = db.get(Visit, job.visit_id)
        if visit is None or visit.member_id != inbox.member_id:
            raise PersistentFinalizationInvariantViolation(
                "job_visit_member_provenance_mismatch"
            )
        member = db.get(Member, inbox.member_id)
        if member is None:
            raise PersistentFinalizationInvariantViolation("job_member_missing")
        policies = db.query(CameraIdentityBindingPolicy).filter(
            CameraIdentityBindingPolicy.camera_id == camera_id,
            CameraIdentityBindingPolicy.effective_to.is_(None),
        ).all()
        if len(policies) > 1:
            raise PersistentFinalizationInvariantViolation(
                "multiple_current_camera_policies"
            )
        return evaluation, inbox, visit, member, policies[0] if policies else None, track_id

    def _application_gate_reason(
        self,
        *,
        evaluation: TrackAssociationEvaluation,
        inbox: DeviceRecognitionInbox,
        visit: Visit,
        member: Member,
        policy: Optional[CameraIdentityBindingPolicy],
    ) -> Optional[str]:
        if inbox.recognition_status != "accepted":
            return "recognition_status_changed"
        if inbox.store_resolution_status != "resolved":
            return "store_resolution_changed"
        if (
            evaluation.runtime_instance_id != self.runtime_instance_id
            or evaluation.runtime_owner_id not in (None, self.lease.owner_id)
        ):
            return "stale_runtime_instance"
        if visit.exit_time is not None or visit.superseded_by_visit_id is not None:
            return "visit_state_changed"
        if not bool(member.is_active):
            return "member_state_changed"
        if policy is None or policy.mode != "enabled":
            return "policy_not_enabled"
        if evaluation.mode != "enabled" or evaluation.policy_version_id != policy.id:
            return "policy_version_changed"
        if evaluation.algorithm_version != policy.algorithm_version:
            return "algorithm_version_changed"
        if evaluation.parameter_version != policy.parameter_version:
            return "parameter_version_changed"
        if not is_confirmed_assessment(evaluation.result):
            return (
                "probable_application_policy_contract_missing"
                if evaluation.result == "probable"
                else "assessment_not_finalizable"
            )
        if self.transform_status_provider(evaluation.camera_id or "") != "verified":
            return "transform_unverified"
        if (evaluation.spatial_evidence or {}).get("quality_status") != "verified":
            return "spatial_evidence_unverified"
        return None

    def _binding_values(
        self,
        *,
        evaluation: TrackAssociationEvaluation,
        inbox: DeviceRecognitionInbox,
        visit: Visit,
        member: Member,
        policy: CameraIdentityBindingPolicy,
        track_id: int,
        track_token: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> dict:
        if evaluation.effective_occurred_at is None or not evaluation.method:
            raise PersistentFinalizationInvariantViolation(
                "evaluation_binding_provenance_incomplete"
            )
        return {
            "recognition_event_id": evaluation.recognition_event_id,
            "evaluation_id": evaluation.id,
            "store_id": evaluation.store_id,
            "camera_id": evaluation.camera_id,
            "runtime_owner_id": self.lease.owner_id,
            "runtime_instance_id": evaluation.runtime_instance_id,
            "runtime_key": evaluation.runtime_key,
            "track_id": track_id,
            "track_token": track_token,
            "member_id": member.id,
            "member_tier": member.tier,
            "visit_id": visit.id,
            "binding_level": "confirmed",
            "status": "confirmed",
            "identity_source": evaluation.method,
            "identity_confidence": inbox.similarity,
            "policy_version_id": policy.id,
            "algorithm_version": evaluation.algorithm_version,
            "parameter_version": evaluation.parameter_version,
            "valid_from": evaluation.effective_occurred_at,
            "valid_to": None,
            "expires_at": expires_at,
            "created_at": created_at,
            "updated_at": created_at,
        }

    def _assert_binding_matches(self, binding: TrackIdentityBinding, expected: dict) -> None:
        immutable = (
            "recognition_event_id",
            "evaluation_id",
            "store_id",
            "camera_id",
            "runtime_owner_id",
            "runtime_instance_id",
            "runtime_key",
            "track_id",
            "track_token",
            "member_id",
            "member_tier",
            "visit_id",
            "binding_level",
            "identity_source",
            "identity_confidence",
            "policy_version_id",
            "algorithm_version",
            "parameter_version",
            "valid_from",
        )
        if any(getattr(binding, field) != expected[field] for field in immutable):
            raise PersistentFinalizationInvariantViolation(
                "exact_binding_provenance_mismatch"
            )

    def _mark_binding_committed(
        self,
        job: TrackAssociationFinalizationJob,
        binding: TrackIdentityBinding,
        now: datetime,
    ) -> None:
        job.binding_id = binding.id
        job.application_status = "applied"
        job.application_reason = None
        job.status = "binding_committed"
        job.binding_committed_at = now
        job.cache_projection_status = "pending"
        job.backfill_status = "pending"
        job.last_error = None
        job.next_retry_at = None

    def _commit_conflict(
        self,
        db: Session,
        *,
        job: TrackAssociationFinalizationJob,
        bindings: list[TrackIdentityBinding],
        reason: str,
        conflict_at: datetime,
    ) -> None:
        for binding in bindings:
            binding.status = "track_conflict"
            binding.valid_to = conflict_at
        job.application_status = "conflict"
        job.application_reason = reason
        job.binding_id = None
        job.cache_projection_status = "pending"
        job.backfill_status = "skipped"
        job.status = "processing"
        job.last_error = None

    def _terminal_skip(
        self,
        job: TrackAssociationFinalizationJob,
        inbox: DeviceRecognitionInbox,
        evaluation: TrackAssociationEvaluation,
        reason: str,
    ) -> None:
        job.application_status = "skipped"
        job.application_reason = reason
        job.binding_id = None
        job.cache_projection_status = "skipped"
        job.backfill_status = "skipped"
        job.status = "succeeded"
        job.last_error = None
        job.next_retry_at = None
        self._clear_claim(job)
        inbox.track_association_status = self._canonical_assessment(evaluation.result)
        inbox.binding_id = None

    def _project_cache(self, claim: _Claim) -> None:
        binding = self._binding_for_claim(claim)
        now = self._now()
        if not self._is_active(binding, now):
            raise RuntimeError("durable_binding_not_active_for_projection")
        projection = self._cache_projection(binding)
        self.track_identity_cache.bind(projection)
        self._mark_step(claim, "cache_projection_status", "succeeded")

    def _backfill_behavior(self, claim: _Claim) -> None:
        binding = self._binding_for_claim(claim)
        self.behavior_events.attach_identity_to_track(
            store_id=binding.store_id,
            camera_id=binding.camera_id,
            runtime_instance_id=binding.runtime_instance_id,
            track_id=binding.track_id,
            track_token=binding.track_token,
            member_id=binding.member_id,
            canonical_visit_id=binding.visit_id,
            member_tier=binding.member_tier,
            evaluation_id=binding.evaluation_id,
            binding_id=binding.id,
            policy_version_id=binding.policy_version_id,
            algorithm_version=binding.algorithm_version,
            parameter_version=binding.parameter_version,
            recognized_at=binding.valid_from,
            identity_source=binding.identity_source,
            identity_confidence=binding.identity_confidence,
        )
        self._mark_step(claim, "backfill_status", "succeeded")

    def _complete_applied(self, claim: _Claim) -> None:
        db = self.session_factory()
        try:
            self._begin(db)
            job = self._claimed_job(db, claim)
            self._validate_lease(db)
            if (
                job.application_status != "applied"
                or not job.binding_id
                or job.cache_projection_status != "succeeded"
                or job.backfill_status != "succeeded"
            ):
                raise PersistentFinalizationInvariantViolation(
                    "applied_completion_state_inconsistent"
                )
            inbox = db.get(DeviceRecognitionInbox, job.recognition_event_id)
            if inbox is None or inbox.finalization_job_id != job.id:
                raise PersistentFinalizationInvariantViolation(
                    "completion_inbox_provenance_mismatch"
                )
            job.status = "succeeded"
            job.last_error = None
            job.next_retry_at = None
            self._clear_claim(job)
            inbox.track_association_status = "confirmed"
            inbox.binding_id = job.binding_id
            self._validate_lease(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _complete_conflict_cleanup(self, claim: _Claim) -> None:
        state = self._job_state(claim)
        with self.session_factory() as db:
            evaluation = db.get(TrackAssociationEvaluation, state.evaluation_id)
            inbox = db.get(DeviceRecognitionInbox, state.recognition_event_id)
            if evaluation is None or inbox is None or not inbox.member_id:
                raise PersistentFinalizationInvariantViolation(
                    "conflict_cleanup_provenance_missing"
                )
            tokens = {state.track_token}
            if state.application_reason == "same_member_multiple_tracks":
                tokens.update(
                    row[0]
                    for row in db.query(TrackIdentityBinding.track_token).filter(
                        TrackIdentityBinding.store_id == evaluation.store_id,
                        TrackIdentityBinding.camera_id == evaluation.camera_id,
                        TrackIdentityBinding.runtime_instance_id
                        == evaluation.runtime_instance_id,
                        TrackIdentityBinding.member_id == inbox.member_id,
                        TrackIdentityBinding.status == "track_conflict",
                    )
                )
        for token in sorted(tokens):
            store_id, camera_id, runtime_id, track_id = self._parse_track_token(token)
            self.track_identity_cache.invalidate_exact(
                store_id=store_id,
                camera_id=camera_id,
                runtime_instance_id=runtime_id,
                track_id=track_id,
                track_token=token,
                reason=state.application_reason or "track_conflict",
            )
        db = self.session_factory()
        try:
            self._begin(db)
            job = self._claimed_job(db, claim)
            self._validate_lease(db)
            if job.application_status != "conflict":
                raise PersistentFinalizationInvariantViolation(
                    "conflict_completion_state_inconsistent"
                )
            inbox = db.get(DeviceRecognitionInbox, job.recognition_event_id)
            if inbox is None or inbox.finalization_job_id != job.id:
                raise PersistentFinalizationInvariantViolation(
                    "conflict_completion_inbox_mismatch"
                )
            job.cache_projection_status = "succeeded"
            job.status = "succeeded"
            job.last_error = None
            job.next_retry_at = None
            self._clear_claim(job)
            inbox.track_association_status = "track_conflict"
            inbox.binding_id = None
            self._validate_lease(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _validate_applied_binding(self, claim: _Claim) -> None:
        state = self._job_state(claim)
        with self.session_factory() as db:
            job = db.get(TrackAssociationFinalizationJob, claim.job_id)
            binding = db.get(TrackIdentityBinding, state.binding_id)
            evaluation = db.get(TrackAssociationEvaluation, state.evaluation_id)
            inbox = db.get(DeviceRecognitionInbox, state.recognition_event_id)
            if None in (job, binding, evaluation, inbox):
                raise PersistentFinalizationInvariantViolation(
                    "applied_binding_reference_missing"
                )
            visit_id = self._canonical_visit_id(db, state.visit_id)
            if visit_id != state.visit_id:
                raise PersistentFinalizationInvariantViolation(
                    "applied_binding_visit_changed"
                )
            if (
                binding.id != job.binding_id
                or binding.evaluation_id != evaluation.id
                or binding.recognition_event_id != inbox.canonical_event_id
                or binding.visit_id != visit_id
                or binding.track_token != job.track_token
                or binding.member_id != inbox.member_id
            ):
                raise PersistentFinalizationInvariantViolation(
                    "applied_binding_provenance_mismatch"
                )

    def _renew_claim(self, claim: _Claim) -> None:
        db = self.session_factory()
        try:
            self._begin(db)
            job = self._claimed_job(db, claim)
            self._validate_lease(db)
            now = self._now()
            job.locked_at = now
            job.claim_expires_at = now + self.claim_lease
            self._validate_lease(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_step(self, claim: _Claim, field: str, value: str) -> None:
        db = self.session_factory()
        try:
            self._begin(db)
            job = self._claimed_job(db, claim)
            self._validate_lease(db)
            setattr(job, field, value)
            job.last_error = None
            job.next_retry_at = None
            self._validate_lease(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _record_retry(self, claim: _Claim, error: BaseException) -> None:
        db = self.session_factory()
        try:
            self._begin(db)
            job = db.get(TrackAssociationFinalizationJob, claim.job_id)
            if job is None or job.claim_token != claim.token:
                db.rollback()
                return
            self._validate_lease(db)
            now = self._now()
            delay = min(
                self.retry_max_seconds,
                self.retry_base_seconds * (2 ** max(0, int(job.attempts or 1) - 1)),
            )
            if job.application_status == "pending":
                job.status = "pending"
                job.application_status = "pending"
            elif job.application_status == "applied" and job.binding_id:
                job.status = "binding_committed"
                if job.cache_projection_status != "succeeded":
                    job.cache_projection_status = "failed"
                elif job.backfill_status != "succeeded":
                    job.backfill_status = "failed"
            elif job.application_status == "conflict":
                job.status = "pending"
                job.backfill_status = "skipped"
                job.cache_projection_status = "failed"
            else:
                raise PersistentFinalizationInvariantViolation(
                    "retry_application_state_inconsistent"
                )
            job.next_retry_at = now + timedelta(seconds=delay)
            job.last_error = self._sanitize_error(error)
            self._clear_claim(job)
            self._validate_lease(db)
            db.commit()
        except PersistentFinalizationInvariantViolation:
            db.rollback()
            raise
        except RuntimeLeaseUnavailable:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("Failed to persist Finalization retry state")
        finally:
            db.close()

    def _record_invariant(self, claim: _Claim, reason: str) -> None:
        db = self.session_factory()
        try:
            self._begin(db)
            job = db.get(TrackAssociationFinalizationJob, claim.job_id)
            if job is None or job.claim_token != claim.token:
                db.rollback()
                return
            self._validate_lease(db)
            job.last_error = reason[:512]
            self._validate_lease(db)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist Finalization invariant error")
        finally:
            db.close()

    def _job_state(
        self, claim: _Claim, *, allow_terminal: bool = False
    ) -> TrackAssociationFinalizationJob:
        with self.session_factory() as db:
            job = db.get(TrackAssociationFinalizationJob, claim.job_id)
            if job is None:
                raise PersistentFinalizationInvariantViolation("finalization_job_missing")
            if job.status != "succeeded" and job.claim_token != claim.token:
                raise PersistentFinalizationInvariantViolation(
                    "finalization_claim_ownership_lost"
                )
            if job.status == "succeeded" and not allow_terminal:
                raise PersistentFinalizationInvariantViolation(
                    "unexpected_terminal_finalization_job"
                )
            db.expunge(job)
            return job

    def _binding_for_claim(self, claim: _Claim) -> TrackIdentityBinding:
        with self.session_factory() as db:
            job = db.get(TrackAssociationFinalizationJob, claim.job_id)
            if job is None or job.claim_token != claim.token or not job.binding_id:
                raise PersistentFinalizationInvariantViolation(
                    "binding_claim_reference_missing"
                )
            binding = db.get(TrackIdentityBinding, job.binding_id)
            if binding is None:
                raise PersistentFinalizationInvariantViolation(
                    "durable_binding_missing"
                )
            db.expunge(binding)
            return binding

    def _claimed_job(
        self, db: Session, claim: _Claim
    ) -> TrackAssociationFinalizationJob:
        job = db.get(TrackAssociationFinalizationJob, claim.job_id)
        if (
            job is None
            or job.status == "succeeded"
            or job.claim_token != claim.token
            or job.claim_expires_at is None
            or job.claim_expires_at <= self._now()
        ):
            raise PersistentFinalizationInvariantViolation(
                "finalization_claim_ownership_lost"
            )
        return job

    def _cache_projection(
        self, binding: TrackIdentityBinding
    ) -> CachedTrackIdentityBinding:
        return CachedTrackIdentityBinding(
            binding_id=binding.id,
            evaluation_id=binding.evaluation_id,
            store_id=binding.store_id,
            camera_id=binding.camera_id,
            runtime_id=binding.runtime_instance_id,
            track_id=binding.track_id,
            track_token=binding.track_token,
            member_id=binding.member_id,
            member_tier=binding.member_tier,
            visit_id=binding.visit_id,
            canonical_visit_id=binding.visit_id,
            recognition_event_id=binding.recognition_event_id,
            status=binding.status,
            method=binding.identity_source,
            confidence=binding.identity_confidence,
            recognized_at=binding.valid_from,
            confirmed_at=binding.created_at,
            expires_at=binding.expires_at,
        )

    def _validate_lease(self, db: Session) -> None:
        self.lease_service.validate_fence(
            store_id=self.lease.store_id,
            mode=self.lease.mode,
            owner_id=self.lease.owner_id,
            lease_token=self.lease.lease_token,
            lease_generation=self.lease.lease_generation,
            session=db,
        )

    @staticmethod
    def _evaluation_track_token(evaluation: TrackAssociationEvaluation) -> Optional[str]:
        spatial = evaluation.spatial_evidence or {}
        token = spatial.get("primary_track_token")
        if token:
            return str(token)
        matched = [
            item.get("track_token")
            for item in spatial.get("tracks", [])
            if item.get("matched") and item.get("track_token")
        ]
        if len(set(matched)) == 1:
            return str(matched[0])
        candidates = [
            item.get("track_token")
            for item in (evaluation.candidates or [])
            if item.get("track_token")
        ]
        return str(candidates[0]) if len(set(candidates)) == 1 else None

    @staticmethod
    def _canonical_assessment(result: str) -> str:
        return "confirmed" if is_confirmed_assessment(result) else result

    @staticmethod
    def _parse_track_token(track_token: Optional[str]) -> tuple[str, str, str, int]:
        try:
            store_id, camera_id, runtime_id, raw_track_id = str(track_token).split(":")
            track_id = int(raw_track_id)
        except (TypeError, ValueError):
            raise PersistentFinalizationInvariantViolation(
                "malformed_track_token"
            ) from None
        expected = f"{store_id}:{camera_id}:{runtime_id}:{track_id}"
        if not store_id or not camera_id or not runtime_id or expected != track_token:
            raise PersistentFinalizationInvariantViolation("malformed_track_token")
        return store_id, camera_id, runtime_id, track_id

    @staticmethod
    def _is_active(binding: TrackIdentityBinding, now: datetime) -> bool:
        return bool(
            binding.status == "confirmed"
            and binding.valid_to is None
            and binding.expires_at is not None
            and binding.expires_at > now
        )

    @staticmethod
    def _canonical_visit_id(db: Session, visit_id: str) -> str:
        try:
            return resolve_canonical_visit_id(db, visit_id)
        except ValueError as error:
            raise PersistentFinalizationInvariantViolation(
                "canonical_visit_invariant_violation"
            ) from error

    @staticmethod
    def _clear_claim(job: TrackAssociationFinalizationJob) -> None:
        job.claim_token = None
        job.locked_at = None
        job.claim_expires_at = None

    @staticmethod
    def _sanitize_error(error: BaseException) -> str:
        return type(error).__name__[:128]

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")

    def _now(self) -> datetime:
        return self.clock().replace(tzinfo=None)


class BindingLifecycleHeartbeat:
    """Extend active Bindings from exact in-memory Track activity."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        lease_service: IdentityAssociationRuntimeLeaseService,
        lease: RuntimeLease,
        runtime_instance_id: str,
        track_snapshot_buffer: TrackSnapshotBuffer,
        track_identity_cache: CameraTrackIdentityCache,
        track_lost_seconds: float = 2.0,
        heartbeat_interval_seconds: Optional[float] = None,
        clock: Callable[[], datetime] = datetime.utcnow,
        integrity_fatal_callback: Optional[Callable[[BaseException], None]] = None,
        lease_lost_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        if track_lost_seconds <= 0:
            raise ValueError("track_lost_seconds must be positive")
        interval = (
            min(1.0, track_lost_seconds / 2)
            if heartbeat_interval_seconds is None
            else heartbeat_interval_seconds
        )
        if interval <= 0 or interval > track_lost_seconds / 2:
            raise ValueError(
                "heartbeat_interval_seconds must be positive and no greater "
                "than half track_lost_seconds"
            )
        self.session_factory = session_factory
        self.lease_service = lease_service
        self.lease = lease
        self.runtime_instance_id = runtime_instance_id
        self.track_snapshot_buffer = track_snapshot_buffer
        self.track_identity_cache = track_identity_cache
        self.track_lost = timedelta(seconds=track_lost_seconds)
        self.heartbeat_interval_seconds = interval
        self.clock = clock
        self.integrity_fatal_callback = integrity_fatal_callback
        self.lease_lost_callback = lease_lost_callback
        self._stop = Event()
        self._thread: Optional[Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run_forever,
            name="binding-lifecycle-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self.request_stop()
        thread = self._thread
        if thread is None:
            return
        if thread is current_thread():
            return
        thread.join(timeout=max(2.0, self.heartbeat_interval_seconds * 4))
        if thread.is_alive():
            raise RuntimeError("Binding Lifecycle Heartbeat did not stop")
        self._thread = None

    def request_stop(self) -> None:
        self._stop.set()

    def run_once(self) -> int:
        read_now = self._now()
        candidates = self._active_candidates(read_now)
        desired: dict[str, datetime] = {}
        for binding in candidates:
            snapshot = self.track_snapshot_buffer.latest_exact(
                store_id=binding.store_id,
                camera_id=binding.camera_id,
                runtime_instance_id=binding.runtime_instance_id,
                track_token=binding.track_token,
            )
            if (
                snapshot is None
                or snapshot.observed_at + self.track_lost <= read_now
            ):
                self.track_identity_cache.invalidate_exact(
                    store_id=binding.store_id,
                    camera_id=binding.camera_id,
                    runtime_instance_id=binding.runtime_instance_id,
                    track_id=binding.track_id,
                    track_token=binding.track_token,
                    reason="track_no_longer_active",
                )
                continue
            desired[binding.id] = snapshot.observed_at + self.track_lost
        if not desired:
            return 0
        committed = self._commit_batch(candidates, desired)
        for binding in committed:
            try:
                self.track_identity_cache.bind(
                    _cache_projection_from_binding(binding)
                )
            except Exception as error:
                logger.error(
                    "Binding heartbeat Cache projection failed: error_type=%s",
                    type(error).__name__,
                )
        return len(committed)

    def _run_forever(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            try:
                self.run_once()
            except PersistentFinalizationInvariantViolation as error:
                if self.integrity_fatal_callback is not None:
                    self.integrity_fatal_callback(error)
                return
            except RuntimeLeaseUnavailable:
                if self.lease_lost_callback is not None:
                    self.lease_lost_callback("runtime_lease_lost")
                return
            except Exception as error:
                logger.error(
                    "Binding lifecycle heartbeat failed: error_type=%s",
                    type(error).__name__,
                )

    def _active_candidates(self, now: datetime) -> tuple[TrackIdentityBinding, ...]:
        db = self.session_factory()
        try:
            rows = db.query(TrackIdentityBinding).filter(
                TrackIdentityBinding.store_id == self.lease.store_id,
                TrackIdentityBinding.runtime_instance_id == self.runtime_instance_id,
                TrackIdentityBinding.status == "confirmed",
                TrackIdentityBinding.valid_to.is_(None),
                TrackIdentityBinding.expires_at > now,
            ).all()
            for row in rows:
                db.expunge(row)
            return tuple(rows)
        finally:
            db.close()

    def _commit_batch(
        self,
        candidates: tuple[TrackIdentityBinding, ...],
        desired: dict[str, datetime],
    ) -> tuple[TrackIdentityBinding, ...]:
        scopes = {
            row.id: (
                row.store_id,
                row.camera_id,
                row.runtime_instance_id,
                row.track_token,
            )
            for row in candidates
        }
        db = self.session_factory()
        try:
            TrackAssociationFinalizationWorker._begin(db)
            txn_now = self._now()
            self._validate_lease(db)
            committed: list[TrackIdentityBinding] = []
            for binding_id, desired_expires_at in desired.items():
                binding = db.get(TrackIdentityBinding, binding_id)
                if binding is None:
                    continue
                if (
                    scopes.get(binding_id)
                    != (
                        binding.store_id,
                        binding.camera_id,
                        binding.runtime_instance_id,
                        binding.track_token,
                    )
                ):
                    raise PersistentFinalizationInvariantViolation(
                        "heartbeat_binding_scope_invariant_violation"
                    )
                if (
                    binding.status != "confirmed"
                    or binding.valid_to is not None
                    or binding.expires_at is None
                    or binding.expires_at <= txn_now
                    or desired_expires_at <= txn_now
                ):
                    continue
                if desired_expires_at > binding.expires_at:
                    binding.expires_at = desired_expires_at
                db.flush()
                committed.append(binding)
            self._validate_lease(db)
            for binding in committed:
                db.expunge(binding)
            db.commit()
            return tuple(committed)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _validate_lease(self, db: Session) -> None:
        self.lease_service.validate_fence(
            store_id=self.lease.store_id,
            mode=self.lease.mode,
            owner_id=self.lease.owner_id,
            lease_token=self.lease.lease_token,
            lease_generation=self.lease.lease_generation,
            session=db,
        )

    def _now(self) -> datetime:
        return self.clock().replace(tzinfo=None)


def _cache_projection_from_binding(
    binding: TrackIdentityBinding,
) -> CachedTrackIdentityBinding:
    return CachedTrackIdentityBinding(
        binding_id=binding.id,
        evaluation_id=binding.evaluation_id,
        store_id=binding.store_id,
        camera_id=binding.camera_id,
        runtime_id=binding.runtime_instance_id,
        track_id=binding.track_id,
        track_token=binding.track_token,
        member_id=binding.member_id,
        member_tier=binding.member_tier,
        visit_id=binding.visit_id,
        canonical_visit_id=binding.visit_id,
        recognition_event_id=binding.recognition_event_id,
        status=binding.status,
        method=binding.identity_source,
        confidence=binding.identity_confidence,
        recognized_at=binding.valid_from,
        confirmed_at=binding.created_at,
        expires_at=binding.expires_at,
    )
