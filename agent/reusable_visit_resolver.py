"""Single transactional seam for accepted recognition to Visit business facts."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agent.association_transition import RecognitionInboxTransitionService
from agent.identity_runtime_lease import IdentityAssociationRuntimeLeaseService
from agent.models import (
    AssociationOutcomeRebuildJob,
    DeviceRecognitionInbox,
    IdentityAssociationStoreState,
    TrackAssociationEvaluation,
    Member,
    MemberArrival,
    MemberArrivalEffect,
    Visit,
    TrackAssociationFinalizationJob,
    TrackIdentityBinding,
    CustomerEntryConfirmedEvent,
    VisitEntryEvidence,
)
from agent.track_association_policy import (
    CameraIdentityBindingPolicyService,
    is_confirmed_assessment,
)
from agent.visit_merge_service import VisitMergeService, resolve_canonical_visit_id
from agent.store_resolution import StoreResolutionService
from agent.member_statistics import MemberStatisticsRecalculator


REALTIME_EFFECTS = ("welcome_display", "staff_alert", "coupon_issuance")
NON_REALTIME_EFFECTS = ("customer_profile",)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecognitionAssociationRequest:
    recognition_event_id: str
    member_id: str
    member_tier: Optional[str]
    device_id: str
    store_id: str
    camera_id: Optional[str]
    occurred_at: datetime
    similarity: float
    track_association_status: str = "no_track_match"
    track_token: Optional[str] = None
    track_method: Optional[str] = None
    identity_source: str = "device_api"
    mode: str = "new"
    runtime_id: Optional[str] = None
    runtime_owner_id: Optional[str] = None
    runtime_instance_id: Optional[str] = None
    lease_token: Optional[str] = None
    lease_generation: Optional[int] = None
    algorithm_version: Optional[str] = None
    parameter_version: Optional[str] = None
    evaluation_id: Optional[str] = None
    policy_version_id: Optional[str] = None
    association_claim_token: Optional[str] = None


@dataclass(frozen=True)
class RecognitionAssociationResult:
    recognition_event_id: str
    visit_id: Optional[str]
    arrival_id: Optional[str]
    association_outcome: str
    track_association_status: str
    realtime_effect_status: Optional[str]
    effective_occurred_at: datetime


@dataclass(frozen=True)
class _ConvergencePlan:
    anonymous_visit: Visit
    existing_member_visits: tuple[Visit, ...]
    planned_canonical_visit_id: str
    planned_duplicate_visit_ids: tuple[str, ...]
    participating_visit_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Phase2CProvenanceDiagnostic:
    anonymous_visit_id: str
    member_visit_ids: tuple[str, ...]
    planned_canonical_visit_id: str
    finalization_job_ids: tuple[str, ...]
    job_statuses: tuple[str, ...]
    application_statuses: tuple[str, ...]
    binding_ids: tuple[str, ...]
    evaluation_id: Optional[str]
    recognition_event_id: str
    track_token: Optional[str]
    runtime_instance_id: Optional[str]


class ReusableVisitValidator:
    """Shared Canonical/Reusable validation for recognition and entry paths."""

    def __init__(self, *, inactivity_timeout_seconds=1800.0, max_visit_duration_seconds=14400.0) -> None:
        self.inactivity_timeout = timedelta(seconds=inactivity_timeout_seconds)
        self.max_visit_duration = timedelta(seconds=max_visit_duration_seconds)

    def resolve(
        self,
        db: Session,
        *,
        visit_id: str,
        member_id: str,
        store_id: str,
        now: datetime,
    ) -> Optional[Visit]:
        canonical_id = resolve_canonical_visit_id(db, visit_id)
        visit = db.get(Visit, canonical_id)
        if (
            visit is None
            or visit.member_id != member_id
            or visit.store_id != store_id
            or visit.exit_time is not None
            or visit.superseded_by_visit_id is not None
        ):
            return None
        entry_time = visit.entry_time or visit.created_at
        activity = visit.last_seen_at or visit.identified_at or visit.identity_time or entry_time
        if entry_time is None or activity is None:
            return None
        if now - entry_time > self.max_visit_duration or now - activity > self.inactivity_timeout:
            return None
        return visit


class ReusableVisitResolver:
    """Own all member Visit/Arrival decisions for accepted recognitions."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = datetime.utcnow,
        inactivity_timeout_seconds: float = 1800.0,
        max_visit_duration_seconds: float = 14400.0,
        recognition_event_max_age_seconds: float = 600.0,
        realtime_arrival_effect_max_delay_seconds: float = 30.0,
        future_clock_skew_tolerance_seconds: float = 30.0,
        effect_types: tuple[str, ...] = REALTIME_EFFECTS + NON_REALTIME_EFFECTS,
        store_resolution_service: Optional[StoreResolutionService] = None,
        runtime_lease_service: Optional[IdentityAssociationRuntimeLeaseService] = None,
        policy_service: Optional[CameraIdentityBindingPolicyService] = None,
        runtime_instance_id: Optional[str] = None,
        phase2b_finalization_enabled: bool = False,
        transform_status_provider: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.inactivity_timeout = timedelta(seconds=inactivity_timeout_seconds)
        self.max_visit_duration = timedelta(seconds=max_visit_duration_seconds)
        self.max_event_age = timedelta(seconds=recognition_event_max_age_seconds)
        self.realtime_delay = timedelta(seconds=realtime_arrival_effect_max_delay_seconds)
        self.future_tolerance = timedelta(seconds=future_clock_skew_tolerance_seconds)
        self.effect_types = tuple(effect_types)
        self.store_resolution = store_resolution_service or StoreResolutionService(
            session_factory
        )
        self.runtime_leases = runtime_lease_service or IdentityAssociationRuntimeLeaseService(
            session_factory, clock=clock
        )
        self.policies = policy_service or CameraIdentityBindingPolicyService(
            session_factory, clock=clock
        )
        self.runtime_instance_id = runtime_instance_id
        self.phase2b_finalization_enabled = phase2b_finalization_enabled
        # This adapter is invoked while SQLite is write-locked. Production
        # composition must provide a preloaded, in-memory lookup only.
        self.transform_status_provider = transform_status_provider or (
            lambda _camera_id: "unverified"
        )
        self.member_statistics = MemberStatisticsRecalculator(session_factory)

    def resolve(self, request: RecognitionAssociationRequest) -> RecognitionAssociationResult:
        self._validate_request(request)
        db = self.session_factory()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            resolver_now = self._normalise_now(self.clock())
            inbox = db.get(DeviceRecognitionInbox, request.recognition_event_id)
            if inbox is None:
                raise ValueError("recognition event not found")
            self._validate_runtime_lease(db, request)
            if (
                request.association_claim_token is not None
                and inbox.association_claim_token != request.association_claim_token
            ):
                raise ValueError("recognition_association_claim_lost")
            evaluation = self._load_evaluation(db, inbox, request)
            if (
                inbox.evaluation_id is not None
                and request.evaluation_id != inbox.evaluation_id
            ):
                raise ValueError("recognition_inbox_evaluation_conflict")
            future_skew = max(
                0.0, (request.occurred_at - resolver_now).total_seconds()
            )
            if request.occurred_at - resolver_now > self.future_tolerance:
                return self._time_anomaly(
                    db, inbox, request, resolver_now, future_skew
                )
            effective_time, age = self._effective_event_time(
                request.occurred_at, resolver_now
            )
            if inbox.member_id and inbox.member_id != request.member_id:
                raise ValueError("recognition member does not match Inbox")
            track_status, track_token, track_reason = self._effective_track_association(
                db, request
            )
            if age > self.max_event_age:
                self._project_inbox_attempt(
                    inbox,
                    request=request,
                    track_status=track_status,
                    track_token=track_token,
                    track_reason=track_reason,
                    resolver_now=resolver_now,
                    future_skew=future_skew,
                )
                return self._expire(db, inbox, request, effective_time)

            resolution = self.store_resolution.resolve(
                device_id=request.device_id,
                camera_id=request.camera_id,
                occurred_at=request.occurred_at,
                persisted_store_id=inbox.store_id,
                session=db,
            )
            if resolution.status != "resolved" or resolution.store_id != request.store_id:
                self._project_inbox_attempt(
                    inbox,
                    request=request,
                    track_status=track_status,
                    track_token=track_token,
                    track_reason=track_reason,
                    resolver_now=resolver_now,
                    future_skew=future_skew,
                )
                return self._block_store_resolution(
                    db, inbox, request, resolver_now, resolution.status
                )

            member = db.get(Member, request.member_id)
            if member is None or not member.is_active:
                raise ValueError("active member is required")

            convergence_plan = self._build_convergence_plan(
                db,
                inbox=inbox,
                request=request,
                evaluation=evaluation,
                resolver_now=resolver_now,
            )
            if isinstance(convergence_plan, _Phase2CProvenanceDiagnostic):
                db.rollback()
                return self._record_convergence_blocked(
                    request,
                    convergence_plan,
                    track_status,
                    track_token,
                    track_reason,
                    request.evaluation_id,
                    resolver_now,
                    future_skew,
                )

            self._project_inbox_attempt(
                inbox,
                request=request,
                track_status=track_status,
                track_token=track_token,
                track_reason=track_reason,
                resolver_now=resolver_now,
                future_skew=future_skew,
            )
            inbox.store_id = resolution.store_id
            inbox.store_resolution_status = "resolved"
            converged = self._converge_exact_anonymous_visit(
                db,
                inbox=inbox,
                request=request,
                evaluation=evaluation,
                effective_time=effective_time,
                resolver_now=resolver_now,
                plan=convergence_plan,
            )
            reusable = converged or self._close_and_find_reusable(
                db, request, effective_time, resolver_now
            )
            visit = reusable or Visit(
                member_id=request.member_id,
                store_id=request.store_id,
                camera_id=request.camera_id or request.device_id,
                entry_time=effective_time,
                identity_time=effective_time,
                identified_at=effective_time,
                last_seen_at=effective_time,
                recognized_confidence=request.similarity,
                identity_confidence=request.similarity,
                identity_source=request.identity_source,
                identity_status="identified_member",
                visitor_type="member",
                is_member=True,
                visit_source="recognition_only",
                verification_status="confirmed",
            )
            if reusable is None:
                db.add(visit)
                db.flush()
            self._touch_visit(visit, effective_time)

            arrival = self._effective_arrival_for_visit(db, visit)
            outcome = "existing_visit_observation"
            realtime_status = None
            if arrival is None:
                arrival = MemberArrival(
                    visit_id=visit.id,
                    member_id=request.member_id,
                    recognition_event_id=request.recognition_event_id,
                    occurred_at=effective_time,
                    arrival_source="device_recognition",
                )
                db.add(arrival)
                db.flush()
                outcome = "new_member_arrival"
                realtime_status = self._create_effects(
                    db, arrival, effective_time, resolver_now, age
                )
                self._activate_store_if_needed(
                    db, request, arrival, resolver_now
                )
            RecognitionInboxTransitionService.associated(
                inbox,
                visit_id=visit.id,
                outcome=outcome,
                associated_at=resolver_now,
            )
            inbox.realtime_effect_status = realtime_status
            if age > self.realtime_delay:
                inbox.realtime_effect_reason = "arrival_effect_delay_exceeded"
            self._rebuild_job(
                db,
                recognition_event_id=request.recognition_event_id,
                visit_id=visit.id,
                arrival_id=arrival.id,
                reason="recognition_resolved",
            )
            self.member_statistics.recalculate(request.member_id, session=db)
            finalization_pending = self._maybe_create_finalization_job(
                db,
                inbox=inbox,
                request=request,
                evaluation=evaluation,
                visit=visit,
            )
            if finalization_pending:
                track_status = "finalization_pending"
            self._clear_association_claim(inbox, request)
            self._validate_runtime_lease(db, request)
            db.commit()
            return RecognitionAssociationResult(
                request.recognition_event_id,
                visit.id,
                arrival.id,
                outcome,
                track_status,
                realtime_status,
                effective_time,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _effective_track_association(
        db: Session, request: RecognitionAssociationRequest
    ) -> tuple[str, Optional[str], Optional[str]]:
        # Phase 2A persists the algorithm result exactly as assessed. Policy
        # eligibility controls Durable Binding in Phase 2B, not Evaluation.
        status = (
            "confirmed"
            if is_confirmed_assessment(request.track_association_status)
            else request.track_association_status
        )
        return status, request.track_token, request.track_method

    def _validate_runtime_lease(
        self, db: Session, request: RecognitionAssociationRequest
    ) -> None:
        if not request.lease_token or request.lease_generation is None:
            raise ValueError("runtime lease token and generation are required")
        if not request.runtime_owner_id:
            raise ValueError("runtime_owner_id is required")
        self.runtime_leases.validate_fence(
            store_id=request.store_id,
            mode=request.mode,
            owner_id=request.runtime_owner_id,
            lease_token=request.lease_token,
            lease_generation=request.lease_generation,
            session=db,
        )

    def _load_evaluation(
        self,
        db: Session,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
    ) -> Optional[TrackAssociationEvaluation]:
        if request.evaluation_id is None:
            return None
        evaluation = db.get(TrackAssociationEvaluation, request.evaluation_id)
        if evaluation is None:
            raise ValueError("track_association_evaluation_not_found")
        if evaluation.recognition_event_id != request.recognition_event_id:
            raise ValueError("track_association_evaluation_event_conflict")
        if evaluation.store_id != request.store_id:
            raise ValueError("track_association_evaluation_store_conflict")
        runtime_instance_id = request.runtime_instance_id or request.runtime_id
        if evaluation.runtime_instance_id != runtime_instance_id:
            raise ValueError("track_association_evaluation_runtime_conflict")
        if evaluation.camera_id != request.camera_id:
            raise ValueError("track_association_evaluation_camera_conflict")
        same_result = evaluation.result == request.track_association_status
        confirmed_result = is_confirmed_assessment(
            evaluation.result
        ) and is_confirmed_assessment(request.track_association_status)
        if not same_result and not confirmed_result:
            raise ValueError("track_association_evaluation_result_conflict")
        if (
            request.policy_version_id is not None
            and evaluation.policy_version_id != request.policy_version_id
        ):
            raise ValueError("track_association_evaluation_policy_conflict")
        target = self._evaluation_track_token(evaluation)
        if request.track_token and target and request.track_token != target:
            raise ValueError("track_association_evaluation_track_conflict")
        if request.track_token and evaluation.candidates:
            candidate_tokens = {
                item.get("track_token")
                for item in evaluation.candidates
                if isinstance(item, dict) and item.get("track_token")
            }
            if request.track_token not in candidate_tokens:
                raise ValueError("track_association_evaluation_track_conflict")
        return evaluation

    @staticmethod
    def _evaluation_track_token(
        evaluation: TrackAssociationEvaluation,
    ) -> Optional[str]:
        spatial = evaluation.spatial_evidence or {}
        matched = {
            item.get("track_token")
            for item in spatial.get("tracks", ())
            if isinstance(item, dict) and item.get("matched") and item.get("track_token")
        }
        if len(matched) == 1:
            return next(iter(matched))
        tokens = {
            item.get("track_token")
            for item in (evaluation.candidates or ())
            if isinstance(item, dict) and item.get("track_token")
        }
        return next(iter(tokens)) if len(tokens) == 1 else None

    def _maybe_create_finalization_job(
        self,
        db: Session,
        *,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
        evaluation: Optional[TrackAssociationEvaluation],
        visit: Visit,
    ) -> bool:
        canonical_visit_id = resolve_canonical_visit_id(db, visit.id)
        track_token = (
            request.track_token
            or (self._evaluation_track_token(evaluation) if evaluation is not None else None)
        )
        job = db.query(TrackAssociationFinalizationJob).filter_by(
            recognition_event_id=request.recognition_event_id
        ).one_or_none()
        if job is not None:
            if evaluation is None or not track_token:
                raise ValueError("track_association_finalization_job_conflict")
            self._validate_existing_finalization_job(
                db,
                job=job,
                request=request,
                evaluation=evaluation,
                canonical_visit_id=canonical_visit_id,
                track_token=track_token,
            )
            self._link_inbox_to_finalization_job(
                inbox,
                job=job,
                evaluation=evaluation,
                canonical_visit_id=canonical_visit_id,
                track_token=track_token,
            )
            return job.status != "succeeded"
        if not self.phase2b_finalization_enabled or evaluation is None:
            return False
        eligible, _reason = self._finalization_eligibility(
            db,
            inbox=inbox,
            request=request,
            evaluation=evaluation,
            store_resolution_status="resolved",
        )
        if not eligible:
            return False
        if not track_token:
            return False
        job = TrackAssociationFinalizationJob(
            recognition_event_id=request.recognition_event_id,
            evaluation_id=evaluation.id,
            visit_id=canonical_visit_id,
            track_token=track_token,
            status="pending",
            application_status="pending",
            cache_projection_status="pending",
            backfill_status="pending",
        )
        db.add(job)
        db.flush()
        self._link_inbox_to_finalization_job(
            inbox,
            job=job,
            evaluation=evaluation,
            canonical_visit_id=canonical_visit_id,
            track_token=track_token,
        )
        return True

    def _converge_exact_anonymous_visit(
        self,
        db: Session,
        *,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
        evaluation: Optional[TrackAssociationEvaluation],
        effective_time: datetime,
        resolver_now: datetime,
        plan: Optional[_ConvergencePlan],
    ) -> Optional[Visit]:
        """Promote one exact entrance Visit before Finalization freezes Visit facts."""
        if plan is None:
            return None
        promoted = plan.anonymous_visit
        promoted.member_id = request.member_id
        promoted.visitor_type = "member"
        promoted.identity_status = "identified_member"
        promoted.is_member = True
        promoted.identity_time = effective_time
        promoted.identified_at = effective_time
        promoted.identity_source = request.identity_source
        promoted.recognized_confidence = request.similarity
        promoted.identity_confidence = request.similarity
        self._touch_visit(promoted, effective_time)

        candidate_ids = list(plan.participating_visit_ids)
        if not plan.existing_member_visits:
            return promoted
        merged = VisitMergeService(self.session_factory, clock=self.clock).merge_in_session(
            db,
            member_id=request.member_id,
            store_id=request.store_id,
            now=resolver_now,
            candidate_visit_ids=candidate_ids,
        )
        if (
            merged.canonical_visit_id != plan.planned_canonical_visit_id
            or set(merged.superseded_visit_ids)
            != set(plan.planned_duplicate_visit_ids)
        ):
            raise ValueError("phase2c_convergence_plan_changed")
        return db.get(Visit, merged.canonical_visit_id)

    def _build_convergence_plan(
        self,
        db: Session,
        *,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
        evaluation: Optional[TrackAssociationEvaluation],
        resolver_now: datetime,
    ) -> Optional[_ConvergencePlan | _Phase2CProvenanceDiagnostic]:
        if not self.phase2b_finalization_enabled or evaluation is None:
            return None
        if not is_confirmed_assessment(evaluation.result):
            return None
        eligible, _reason = self._finalization_eligibility(
            db,
            inbox=inbox,
            request=request,
            evaluation=evaluation,
            store_resolution_status="resolved",
        )
        if not eligible or not request.track_token:
            return None
        anonymous = self._exact_anonymous_entry_visits(db, request)
        if len(anonymous) != 1:
            if len(anonymous) > 1:
                logger.warning(
                    "phase2c_anonymous_visit_ambiguous",
                    extra={"recognition_event_id": request.recognition_event_id},
                )
            return None
        existing = self._read_reusable_member_visits(
            db, request=request, resolver_now=resolver_now
        )
        participating = (anonymous[0], *existing)
        merge = VisitMergeService(self.session_factory, clock=self.clock)
        merge_plan = merge.preview_in_session(
            db,
            candidate_visit_ids=[visit.id for visit in participating],
            member_id=request.member_id,
            store_id=request.store_id,
            anonymous_visit_id=anonymous[0].id,
        )
        diagnostic = self._phase2b_visit_provenance_diagnostic(
            db,
            participating=participating,
            planned_canonical_visit_id=merge_plan.canonical_visit_id,
            request=request,
            evaluation=evaluation,
        )
        if diagnostic is not None:
            return diagnostic
        return _ConvergencePlan(
            anonymous_visit=anonymous[0],
            existing_member_visits=existing,
            planned_canonical_visit_id=merge_plan.canonical_visit_id,
            planned_duplicate_visit_ids=merge_plan.duplicate_visit_ids,
            participating_visit_ids=tuple(visit.id for visit in participating),
        )

    @staticmethod
    def _exact_anonymous_entry_visits(
        db: Session, request: RecognitionAssociationRequest
    ) -> tuple[Visit, ...]:
        if not request.track_token or not ReusableVisitResolver._valid_track_token(
            request, request.track_token
        ):
            return ()
        runtime_id = request.runtime_instance_id or request.runtime_id
        rows = (
            db.query(Visit)
            .join(VisitEntryEvidence, VisitEntryEvidence.visit_id == Visit.id)
            .join(
                CustomerEntryConfirmedEvent,
                CustomerEntryConfirmedEvent.id == VisitEntryEvidence.entry_event_id,
            )
            .filter(
                Visit.member_id.is_(None),
                Visit.store_id == request.store_id,
                Visit.camera_id == request.camera_id,
                Visit.track_id == int(request.track_token.split(":")[3]),
                Visit.superseded_by_visit_id.is_(None),
                Visit.exit_time.is_(None),
                CustomerEntryConfirmedEvent.store_id == request.store_id,
                CustomerEntryConfirmedEvent.camera_id == request.camera_id,
                CustomerEntryConfirmedEvent.runtime_session_id == runtime_id,
                CustomerEntryConfirmedEvent.track_id == int(request.track_token.split(":")[3]),
                CustomerEntryConfirmedEvent.tracker_track_id
                == int(request.track_token.split(":")[3]),
                CustomerEntryConfirmedEvent.track_token == request.track_token,
            )
            .distinct()
            .order_by(Visit.id)
            .all()
        )
        return tuple(rows)

    def _read_reusable_member_visits(
        self,
        db: Session,
        *,
        request: RecognitionAssociationRequest,
        resolver_now: datetime,
    ) -> tuple[Visit, ...]:
        visits = (
            db.query(Visit)
            .filter(
                Visit.member_id == request.member_id,
                Visit.store_id == request.store_id,
                Visit.exit_time.is_(None),
                Visit.superseded_by_visit_id.is_(None),
            )
            .order_by(Visit.entry_time, Visit.created_at, Visit.id)
            .all()
        )
        reusable = []
        for visit in visits:
            if self._reuse_expiration_reason(
                visit, observed_at=resolver_now, resolver_now=resolver_now
            ) is None:
                reusable.append(visit)
        return tuple(reusable)

    @staticmethod
    def _phase2b_visit_provenance_diagnostic(
        db: Session,
        *,
        participating: tuple[Visit, ...],
        planned_canonical_visit_id: str,
        request: RecognitionAssociationRequest,
        evaluation: TrackAssociationEvaluation,
    ) -> Optional[_Phase2CProvenanceDiagnostic]:
        visit_ids = [visit.id for visit in participating]
        if not visit_ids:
            return None
        jobs = db.query(TrackAssociationFinalizationJob).filter(
            TrackAssociationFinalizationJob.visit_id.in_(visit_ids)
        ).order_by(TrackAssociationFinalizationJob.id).all()
        bindings = db.query(TrackIdentityBinding).filter(
            TrackIdentityBinding.visit_id.in_(visit_ids)
        ).order_by(TrackIdentityBinding.id).all()
        if not jobs and not bindings:
            return None
        return _Phase2CProvenanceDiagnostic(
            anonymous_visit_id=participating[0].id,
            member_visit_ids=tuple(visit.id for visit in participating[1:]),
            planned_canonical_visit_id=planned_canonical_visit_id,
            finalization_job_ids=tuple(job.id for job in jobs),
            job_statuses=tuple(job.status for job in jobs),
            application_statuses=tuple(job.application_status for job in jobs),
            binding_ids=tuple(binding.id for binding in bindings),
            evaluation_id=evaluation.id,
            recognition_event_id=request.recognition_event_id,
            track_token=request.track_token,
            runtime_instance_id=request.runtime_instance_id or request.runtime_id,
        )

    @staticmethod
    def _project_inbox_attempt(
        inbox: DeviceRecognitionInbox,
        *,
        request: RecognitionAssociationRequest,
        track_status: str,
        track_token: Optional[str],
        track_reason: Optional[str],
        resolver_now: datetime,
        future_skew: float,
    ) -> None:
        inbox.future_skew_seconds = future_skew
        inbox.member_id = request.member_id
        inbox.recognition_status = "accepted"
        inbox.track_association_status = track_status
        inbox.matched_track_token = track_token
        inbox.match_reason = track_reason
        inbox.track_association_reason = track_reason
        if request.evaluation_id is not None:
            inbox.evaluation_id = request.evaluation_id
        inbox.runtime_instance_id = request.runtime_instance_id or request.runtime_id
        inbox.match_confidence = request.similarity
        inbox.association_attempts = (inbox.association_attempts or 0) + 1
        inbox.associated_at = resolver_now

    def _record_convergence_blocked(
        self,
        request: RecognitionAssociationRequest,
        diagnostic: _Phase2CProvenanceDiagnostic,
        track_status: str,
        track_token: Optional[str],
        track_reason: Optional[str],
        evaluation_id: Optional[str],
        resolver_now: datetime,
        future_skew: float,
    ) -> RecognitionAssociationResult:
        logger.warning(
            "phase2c_convergence_blocked_by_phase2b_provenance",
            extra=diagnostic.__dict__,
        )
        db = self.session_factory()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            self._validate_runtime_lease(db, request)
            inbox = db.get(DeviceRecognitionInbox, request.recognition_event_id)
            if inbox is None:
                raise ValueError("recognition event not found")
            if (
                request.association_claim_token is not None
                and inbox.association_claim_token != request.association_claim_token
            ):
                raise ValueError("recognition_association_claim_lost")
            self._project_inbox_attempt(
                inbox,
                request=request,
                track_status=track_status,
                track_token=track_token,
                track_reason=track_reason,
                resolver_now=resolver_now,
                future_skew=future_skew,
            )
            if evaluation_id is not None:
                inbox.evaluation_id = evaluation_id
            inbox.store_id = request.store_id
            inbox.store_resolution_status = "resolved"
            RecognitionInboxTransitionService.no_business_outcome(
                inbox,
                recognition_status="accepted",
                reason="phase2c_convergence_blocked_by_phase2b_provenance",
            )
            self._clear_association_claim(inbox, request)
            self._validate_runtime_lease(db, request)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return RecognitionAssociationResult(
            request.recognition_event_id,
            None,
            None,
            "no_business_outcome",
            track_status,
            None,
            request.occurred_at,
        )

    def _validate_existing_finalization_job(
        self,
        db: Session,
        *,
        job: TrackAssociationFinalizationJob,
        request: RecognitionAssociationRequest,
        evaluation: TrackAssociationEvaluation,
        canonical_visit_id: str,
        track_token: str,
    ) -> None:
        existing_visit_id = resolve_canonical_visit_id(db, job.visit_id)
        if (
            job.evaluation_id == evaluation.id
            and existing_visit_id == canonical_visit_id
            and job.track_token == track_token
        ):
            return
        logger.error(
            "Finalization Job conflict: recognition_event_hash=%s",
            hashlib.sha256(
                request.recognition_event_id.encode("utf-8")
            ).hexdigest()[:12],
        )
        raise ValueError("track_association_finalization_job_conflict")

    @staticmethod
    def _link_inbox_to_finalization_job(
        inbox: DeviceRecognitionInbox,
        *,
        job: TrackAssociationFinalizationJob,
        evaluation: TrackAssociationEvaluation,
        canonical_visit_id: str,
        track_token: str,
    ) -> None:
        inbox.evaluation_id = evaluation.id
        inbox.finalization_job_id = job.id
        inbox.matched_visit_id = canonical_visit_id
        inbox.matched_track_token = track_token
        if job.status != "succeeded":
            inbox.track_association_status = "finalization_pending"

    @staticmethod
    def _clear_association_claim(
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
    ) -> None:
        if request.association_claim_token is None:
            return
        inbox.association_claim_token = None
        inbox.association_locked_at = None
        inbox.association_claim_expires_at = None

    def _finalization_eligibility(
        self,
        db: Session,
        *,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
        evaluation: TrackAssociationEvaluation,
        store_resolution_status: Optional[str] = None,
    ) -> tuple[bool, str]:
        if inbox.recognition_status != "accepted":
            return False, "recognition_not_accepted"
        if (store_resolution_status or inbox.store_resolution_status) != "resolved":
            return False, "store_not_resolved"
        if request.mode != "new" or self.runtime_instance_id is None:
            return False, "stale_runtime_instance"
        if (request.runtime_instance_id or request.runtime_id) != self.runtime_instance_id:
            return False, "stale_runtime_instance"
        policy = self.policies.current(request.camera_id or "", session=db)
        if policy is None or policy.mode != "enabled":
            return False, "policy_not_enabled"
        if evaluation.mode != "enabled" or evaluation.policy_version_id != policy.id:
            return False, "policy_version_changed"
        spatial = evaluation.spatial_evidence or {}
        target_track_token = self._evaluation_track_token(evaluation)
        if (
            not request.track_token
            or target_track_token != request.track_token
            or not self._valid_track_token(request, request.track_token)
        ):
            return False, "evaluation_target_not_unique"
        transform_status = self.transform_status_provider(request.camera_id or "")
        spatial_verified = spatial.get("quality_status") == "verified"
        if is_confirmed_assessment(evaluation.result):
            if transform_status != "verified":
                return False, "transform_unverified"
            if not spatial_verified:
                return False, "spatial_evidence_unverified"
            if not is_confirmed_assessment(request.track_association_status):
                return False, "track_association_not_confirmed"
            return True, "confirmed"
        if evaluation.result == "probable":
            allow_probable = bool(
                getattr(policy, "allow_probable_binding", False)
                or getattr(policy, "allow_probable_propagation", False)
            )
            if not allow_probable:
                return False, "probable_application_policy_contract_missing"
            if request.track_association_status != "probable":
                return False, "track_association_not_probable"
            return True, "probable"
        return False, "assessment_not_finalizable"

    @staticmethod
    def _valid_track_token(
        request: RecognitionAssociationRequest, track_token: str
    ) -> bool:
        parts = track_token.split(":")
        if len(parts) != 4:
            return False
        store_id, camera_id, runtime_instance_id, track_id = parts
        expected_runtime = request.runtime_instance_id or request.runtime_id
        try:
            parsed_track_id = int(track_id)
        except (TypeError, ValueError):
            return False
        return bool(
            store_id == request.store_id
            and camera_id == request.camera_id
            and runtime_instance_id == expected_runtime
            and parsed_track_id >= 0
        )

    def _block_store_resolution(
        self,
        db: Session,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
        resolver_now: datetime,
        resolution_status: str,
    ) -> RecognitionAssociationResult:
        if resolution_status == "missing":
            RecognitionInboxTransitionService.waiting_store_mapping(
                inbox, "store_mapping_missing"
            )
            inbox.store_mapping_wait_deadline_at = (
                request.occurred_at + timedelta(seconds=600)
            )
            outcome = "pending"
        else:
            RecognitionInboxTransitionService.no_business_outcome(
                inbox,
                recognition_status="accepted",
                store_resolution_status="conflict",
                reason="store_mapping_conflict",
            )
            outcome = "no_business_outcome"
        db.add(AssociationOutcomeRebuildJob(
            recognition_event_id=request.recognition_event_id,
            reason="store_resolution_blocked",
        ))
        inbox.projection_pending = True
        self._clear_association_claim(inbox, request)
        self._validate_runtime_lease(db, request)
        db.commit()
        return RecognitionAssociationResult(
            request.recognition_event_id,
            None,
            None,
            outcome,
            request.track_association_status,
            None,
            min(request.occurred_at, resolver_now),
        )

    def _close_and_find_reusable(
        self,
        db: Session,
        request: RecognitionAssociationRequest,
        observed_at: datetime,
        resolver_now: datetime,
    ) -> Optional[Visit]:
        candidates = (
            db.query(Visit)
            .filter(
                Visit.member_id == request.member_id,
                Visit.store_id == request.store_id,
                Visit.exit_time.is_(None),
                Visit.superseded_by_visit_id.is_(None),
            )
            .order_by(Visit.entry_time, Visit.created_at, Visit.id)
            .all()
        )
        reusable = []
        for visit in candidates:
            expiration_reason = self._reuse_expiration_reason(
                visit, observed_at=observed_at, resolver_now=resolver_now
            )
            if expiration_reason is not None:
                visit.exit_time = resolver_now
                visit.closed_reason = expiration_reason
                continue
            reusable.append(visit)
        if not reusable:
            return None
        if len(reusable) > 1:
            merged = VisitMergeService(
                self.session_factory, clock=self.clock
            ).merge_in_session(
                db,
                member_id=request.member_id,
                store_id=request.store_id,
                now=resolver_now,
                candidate_visit_ids=[visit.id for visit in reusable],
            )
            return db.get(Visit, merged.canonical_visit_id)
        return reusable[0]

    def _reuse_expiration_reason(
        self,
        visit: Visit,
        *,
        observed_at: datetime,
        resolver_now: datetime,
    ) -> Optional[str]:
        entry_time = visit.entry_time or visit.created_at or observed_at
        activity = max(
            value for value in (
                visit.last_seen_at,
                visit.identified_at,
                visit.identity_time,
                entry_time,
            ) if value is not None
        )
        if resolver_now - entry_time > self.max_visit_duration:
            return "max_visit_duration"
        if resolver_now - activity > self.inactivity_timeout:
            return "inactivity_timeout"
        return None

    @staticmethod
    def _effective_arrival_for_visit(db: Session, visit: Visit) -> Optional[MemberArrival]:
        return (
            db.query(MemberArrival)
            .filter(
                MemberArrival.visit_id == visit.id,
                MemberArrival.superseded_by_arrival_id.is_(None),
            )
            .order_by(MemberArrival.occurred_at, MemberArrival.created_at, MemberArrival.id)
            .first()
        )

    def _create_effects(
        self,
        db: Session,
        arrival: MemberArrival,
        effective_time: datetime,
        resolver_now: datetime,
        age: timedelta,
    ) -> str:
        realtime_status = "pending"
        delayed = age > self.realtime_delay
        for effect_type in self.effect_types:
            is_realtime = effect_type in REALTIME_EFFECTS
            status = "suppressed" if is_realtime and delayed else "pending"
            reason = "arrival_effect_delay_exceeded" if status == "suppressed" else None
            deadline = (
                arrival.occurred_at + self.realtime_delay
                if is_realtime
                else resolver_now + timedelta(hours=24)
            )
            db.add(MemberArrivalEffect(
                member_arrival_id=arrival.id,
                effect_type=effect_type,
                status=status,
                deadline_at=deadline,
                reason=reason,
            ))
            if is_realtime and status == "suppressed":
                realtime_status = "suppressed"
        return realtime_status

    @staticmethod
    def _touch_visit(visit: Visit, observed_at: datetime) -> None:
        if visit.last_seen_at is None or observed_at > visit.last_seen_at:
            visit.last_seen_at = observed_at

    @staticmethod
    def _rebuild_job(db: Session, **values) -> None:
        reason = values.pop("reason")
        event_id = values.get("recognition_event_id")
        existing = None
        if event_id:
            inbox = db.get(DeviceRecognitionInbox, event_id)
            if inbox is not None:
                inbox.projection_pending = True
            existing = db.query(AssociationOutcomeRebuildJob).filter(
                AssociationOutcomeRebuildJob.recognition_event_id == event_id,
                AssociationOutcomeRebuildJob.status.in_(("pending", "failed")),
            ).first()
        if existing is None:
            db.add(AssociationOutcomeRebuildJob(reason=reason, **values))
        else:
            existing.visit_id = values.get("visit_id") or existing.visit_id
            existing.arrival_id = values.get("arrival_id") or existing.arrival_id
            existing.reason = reason
            existing.status = "pending"
            existing.next_retry_at = None

    def _activate_store_if_needed(
        self,
        db: Session,
        request: RecognitionAssociationRequest,
        arrival: MemberArrival,
        now: datetime,
    ) -> None:
        if request.mode != "new":
            return
        state = db.get(IdentityAssociationStoreState, request.store_id)
        if state is None:
            state = IdentityAssociationStoreState(
                store_id=request.store_id,
                schema_version="v1",
                activated_mode="new",
                new_mode_activated_at=now,
                first_new_arrival_id=arrival.id,
                activated_by_runtime_id=request.runtime_id,
            )
            db.add(state)
        elif state.new_mode_activated_at is None:
            state.activated_mode = "new"
            state.new_mode_activated_at = now
            state.first_new_arrival_id = arrival.id
            state.activated_by_runtime_id = request.runtime_id

    def _expire(
        self,
        db: Session,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
        effective_time: datetime,
    ) -> RecognitionAssociationResult:
        RecognitionInboxTransitionService.no_business_outcome(
            inbox,
            recognition_status="expired",
            reason="recognition_event_expired",
        )
        db.add(AssociationOutcomeRebuildJob(
            recognition_event_id=request.recognition_event_id,
            reason="recognition_event_expired",
        ))
        inbox.projection_pending = True
        self._clear_association_claim(inbox, request)
        self._validate_runtime_lease(db, request)
        db.commit()
        return RecognitionAssociationResult(
            request.recognition_event_id,
            None,
            None,
            "no_business_outcome",
            request.track_association_status,
            None,
            effective_time,
        )

    def _time_anomaly(
        self,
        db: Session,
        inbox: DeviceRecognitionInbox,
        request: RecognitionAssociationRequest,
        resolver_now: datetime,
        future_skew: float,
    ) -> RecognitionAssociationResult:
        inbox.future_skew_seconds = future_skew
        RecognitionInboxTransitionService.no_business_outcome(
            inbox,
            recognition_status="time_anomaly",
            reason="future_device_clock_skew",
        )
        inbox.track_association_status = "no_track_match"
        db.add(AssociationOutcomeRebuildJob(
            recognition_event_id=request.recognition_event_id,
            reason="future_device_clock_skew",
        ))
        inbox.projection_pending = True
        self._clear_association_claim(inbox, request)
        self._validate_runtime_lease(db, request)
        db.commit()
        return RecognitionAssociationResult(
            request.recognition_event_id,
            None,
            None,
            "no_business_outcome",
            "no_track_match",
            None,
            resolver_now,
        )

    @staticmethod
    def _effective_event_time(
        occurred_at: datetime,
        resolver_now: datetime,
    ) -> tuple[datetime, timedelta]:
        return min(occurred_at, resolver_now), max(timedelta(0), resolver_now - occurred_at)

    @staticmethod
    def _normalise_now(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise ValueError("clock must return datetime")
        return value.replace(tzinfo=None)

    @staticmethod
    def _validate_request(request: RecognitionAssociationRequest) -> None:
        for name in ("recognition_event_id", "member_id", "device_id", "store_id"):
            if not isinstance(getattr(request, name), str) or not getattr(request, name).strip():
                raise ValueError(f"{name} is required")
        if request.mode not in {"new", "legacy"}:
            raise ValueError("mode must be new or legacy")
        if not 0 <= request.similarity <= 1:
            raise ValueError("similarity must be between 0 and 1")
