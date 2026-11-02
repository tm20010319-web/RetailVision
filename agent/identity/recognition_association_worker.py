"""Leased Binder/fallback delivery into the single Reusable Visit Resolver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agent.platform.identity_runtime_lease import RuntimeLease
from agent.models import DeviceRecognitionInbox, Member
from agent.visits.reusable_visit_resolver import RecognitionAssociationRequest, ReusableVisitResolver


@dataclass(frozen=True)
class TrackAssociationCandidate:
    status: str
    camera_id: Optional[str] = None
    track_token: Optional[str] = None
    method: Optional[str] = None
    reason: Optional[str] = None
    algorithm_version: Optional[str] = None
    parameter_version: Optional[str] = None
    evaluation_id: Optional[str] = None
    policy_version_id: Optional[str] = None
    policy_mode: str = "shadow"
    candidate_count: int = 0
    candidates: tuple = ()
    excluded_candidates: tuple = ()
    should_continue_wait: bool = False
    window_complete: bool = True
    watermark_at: Optional[datetime] = None
    effective_occurred_at: Optional[datetime] = None
    time_delta_seconds: Optional[float] = None
    assessment_fingerprint: Optional[str] = None
    spatial_evidence: Optional[dict] = None


# Phase 2A domain name exposed without breaking existing Candidate callers.
TrackAssociationAssessment = TrackAssociationCandidate


@dataclass(frozen=True)
class _AssociationClaim:
    event_id: str
    token: str


class RecognitionAssociationWorker:
    """Claim pending recognitions before or after their Binder deadline."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        resolver: ReusableVisitResolver,
        lease: RuntimeLease,
        *,
        candidate_provider: Optional[
            Callable[[DeviceRecognitionInbox], TrackAssociationCandidate]
        ] = None,
        fallback: bool = False,
        clock: Callable[[], datetime] = datetime.utcnow,
        claim_seconds: float = 30.0,
        retry_seconds: float = 0.3,
        batch_size: int = 100,
        audit_service: Optional[Any] = None,
        runtime_instance_id: Optional[str] = None,
    ) -> None:
        if lease.mode != "new":
            raise ValueError("association workers require a new-mode runtime lease")
        if not fallback and candidate_provider is None:
            raise ValueError("Binder requires a candidate provider")
        self.session_factory = session_factory
        self.resolver = resolver
        self.lease = lease
        self.candidate_provider = candidate_provider
        self.fallback = fallback
        self.clock = clock
        self.claim_duration = timedelta(seconds=claim_seconds)
        self.retry_delay = timedelta(seconds=retry_seconds)
        self.batch_size = batch_size
        self.audit_service = audit_service
        self.runtime_instance_id = runtime_instance_id

    def run_once(self) -> int:
        completed = 0
        for claim in self._claim_batch():
            db = self.session_factory()
            try:
                row = db.get(DeviceRecognitionInbox, claim.event_id)
                if row is None:
                    self._release(claim, "recognition_event_missing")
                    continue
                candidate = self._evaluate_candidate(row)
                if candidate.should_continue_wait:
                    self._release(claim, candidate.reason or "snapshot_window_pending")
                    continue
                member = db.get(Member, row.member_id)
                if member is None:
                    self._release(claim, "member_missing")
                    continue
                request = RecognitionAssociationRequest(
                    recognition_event_id=row.canonical_event_id,
                    member_id=row.member_id,
                    member_tier=member.tier,
                    device_id=row.device_id,
                    store_id=row.store_id,
                    camera_id=candidate.camera_id,
                    occurred_at=row.occurred_at,
                    similarity=float(row.similarity or 0.0),
                    track_association_status=candidate.status,
                    track_token=candidate.track_token,
                    track_method=candidate.method,
                    mode=self.lease.mode,
                    runtime_id=self.runtime_instance_id or self.lease.owner_id,
                    runtime_instance_id=self.runtime_instance_id or self.lease.owner_id,
                    runtime_owner_id=self.lease.owner_id,
                    lease_token=self.lease.lease_token,
                    lease_generation=self.lease.lease_generation,
                    algorithm_version=candidate.algorithm_version,
                    parameter_version=candidate.parameter_version,
                    evaluation_id=candidate.evaluation_id,
                    policy_version_id=candidate.policy_version_id,
                    association_claim_token=claim.token,
                )
                if self.audit_service is not None:
                    evaluation = self.audit_service.record_evaluation(
                        recognition_event_id=row.canonical_event_id,
                        store_id=row.store_id,
                        camera_id=candidate.camera_id,
                        runtime_id=self.runtime_instance_id or self.lease.owner_id,
                        runtime_instance_id=self.runtime_instance_id or self.lease.owner_id,
                        runtime_owner_id=self.lease.owner_id,
                        result=candidate.status,
                        method=candidate.method,
                        reason=candidate.reason or candidate.method,
                        candidates=[self._candidate_payload(item) for item in candidate.candidates],
                        excluded_candidates=[
                            dict(item) if isinstance(item, dict) else self._candidate_payload(item)
                            for item in candidate.excluded_candidates
                        ],
                        candidate_count=candidate.candidate_count,
                        observed_at=row.occurred_at,
                        effective_occurred_at=candidate.effective_occurred_at or row.occurred_at,
                        algorithm_version=candidate.algorithm_version or "track-association-v1",
                        parameter_version=candidate.parameter_version or "track-association-v1-defaults",
                        policy_version_id=candidate.policy_version_id,
                        mode=candidate.policy_mode,
                        time_delta_seconds=candidate.time_delta_seconds,
                        watermark_at=candidate.watermark_at,
                        window_complete=candidate.window_complete,
                        spatial_evidence=candidate.spatial_evidence,
                    )
                    request = RecognitionAssociationRequest(
                        **{**request.__dict__, "evaluation_id": evaluation.id}
                    )
            finally:
                db.close()
            try:
                result = self.resolver.resolve(request)
            except Exception as error:
                self._release(claim, f"{type(error).__name__}: {error}"[:512])
                continue
            if getattr(result, "track_association_status", None) == "finalization_pending":
                completed += 1
                continue
            if self._complete(claim):
                completed += 1
        return completed

    def _claim_batch(self) -> list[_AssociationClaim]:
        db = self.session_factory()
        now = self.clock().replace(tzinfo=None)
        try:
            self._begin(db)
            filters = [
                DeviceRecognitionInbox.store_id == self.lease.store_id,
                DeviceRecognitionInbox.recognition_status == "accepted",
                DeviceRecognitionInbox.store_resolution_status == "resolved",
                DeviceRecognitionInbox.association_status == "pending",
                DeviceRecognitionInbox.track_association_status != "finalization_pending",
                or_(
                    DeviceRecognitionInbox.association_next_retry_at.is_(None),
                    DeviceRecognitionInbox.association_next_retry_at <= now,
                ),
                or_(
                    DeviceRecognitionInbox.association_claim_expires_at.is_(None),
                    DeviceRecognitionInbox.association_claim_expires_at <= now,
                ),
            ]
            if self.fallback:
                filters.append(DeviceRecognitionInbox.association_deadline_at <= now)
            else:
                filters.append(or_(
                    DeviceRecognitionInbox.association_deadline_at.is_(None),
                    DeviceRecognitionInbox.association_deadline_at > now,
                ))
            rows = (
                db.query(DeviceRecognitionInbox)
                .filter(*filters)
                .order_by(DeviceRecognitionInbox.occurred_at, DeviceRecognitionInbox.canonical_event_id)
                .limit(self.batch_size)
                .all()
            )
            claims = []
            for row in rows:
                token = uuid4().hex
                row.association_claim_token = token
                row.association_locked_at = now
                row.association_claim_expires_at = now + self.claim_duration
                row.association_attempts = (row.association_attempts or 0) + 1
                row.association_last_error = None
                claims.append(_AssociationClaim(row.canonical_event_id, token))
            db.commit()
            return claims
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _complete(self, claim: _AssociationClaim) -> bool:
        db = self.session_factory()
        try:
            updated = db.query(DeviceRecognitionInbox).filter(
                DeviceRecognitionInbox.canonical_event_id == claim.event_id,
                DeviceRecognitionInbox.association_claim_token == claim.token,
            ).update({
                DeviceRecognitionInbox.association_claim_token: None,
                DeviceRecognitionInbox.association_locked_at: None,
                DeviceRecognitionInbox.association_claim_expires_at: None,
                DeviceRecognitionInbox.association_next_retry_at: None,
                DeviceRecognitionInbox.association_last_error: None,
            }, synchronize_session=False)
            db.commit()
            if updated:
                return True
            row = db.get(DeviceRecognitionInbox, claim.event_id)
            return bool(
                row is not None
                and row.association_claim_token is None
                and row.association_status != "pending"
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _release(self, claim: _AssociationClaim, reason: str) -> None:
        db = self.session_factory()
        try:
            now = self.clock().replace(tzinfo=None)
            db.query(DeviceRecognitionInbox).filter(
                DeviceRecognitionInbox.canonical_event_id == claim.event_id,
                DeviceRecognitionInbox.association_claim_token == claim.token,
            ).update({
                DeviceRecognitionInbox.association_claim_token: None,
                DeviceRecognitionInbox.association_locked_at: None,
                DeviceRecognitionInbox.association_claim_expires_at: None,
                DeviceRecognitionInbox.association_next_retry_at: now + self.retry_delay,
                DeviceRecognitionInbox.association_last_error: reason,
            }, synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _candidate_payload(candidate: Any) -> dict:
        if isinstance(candidate, dict):
            return dict(candidate)
        return {
            "track_token": getattr(candidate, "track_token", None),
            "track_id": getattr(candidate, "track_id", None),
            "observed_at": getattr(candidate, "observed_at", None).isoformat()
            if getattr(candidate, "observed_at", None) is not None else None,
            "role": getattr(candidate, "role", None),
            "confidence": getattr(candidate, "confidence", None),
            "stable_frames": getattr(candidate, "stable_frames", None),
            "is_frame_clipped": getattr(candidate, "is_frame_clipped", None),
        }

    def _evaluate_candidate(self, row: DeviceRecognitionInbox) -> TrackAssociationCandidate:
        if self.candidate_provider is None:
            return TrackAssociationCandidate(status="no_track_match", method="deadline_fallback")
        try:
            return self.candidate_provider(
                row,
                now=self.clock().replace(tzinfo=None),
            )
        except TypeError as error:
            if "now" not in str(error):
                raise
            return self.candidate_provider(row)

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
