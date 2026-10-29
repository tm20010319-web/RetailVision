"""Immutable Track Association evaluations, reviews, and camera policies."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from agent.models import CameraIdentityBindingPolicy, EvidenceClip, TrackAssociationEvaluation, TrackAssociationReview, new_uuid


def is_confirmed_assessment(result: Optional[str]) -> bool:
    """Return whether an immutable assessment has confirmed semantics."""
    return result in {"confirmed", "spatial_confirmed"}


class CameraIdentityBindingPolicyService:
    def __init__(self, session_factory: Callable[[], Session], *, clock: Callable[[], datetime] = datetime.utcnow) -> None:
        self.session_factory = session_factory
        self.clock = clock

    def current(self, camera_id: str, *, session: Optional[Session] = None) -> Optional[CameraIdentityBindingPolicy]:
        owns = session is None
        db = session or self.session_factory()
        try:
            rows = db.query(CameraIdentityBindingPolicy).filter(
                CameraIdentityBindingPolicy.camera_id == camera_id,
                CameraIdentityBindingPolicy.effective_to.is_(None),
            ).all()
            return rows[0] if len(rows) == 1 else None
        finally:
            if owns:
                db.close()

    def set_policy(self, *, camera_id: str, mode: str, algorithm_version: str, parameter_version: str, reason: str, qualification_snapshot: Optional[dict] = None, rollback_evaluation_id: Optional[str] = None) -> CameraIdentityBindingPolicy:
        if mode not in {"shadow", "enabled", "rolled_back"}:
            raise ValueError("invalid camera identity policy mode")
        db = self.session_factory()
        try:
            self._begin(db)
            if mode == "enabled":
                qualification = TrackAssociationAuditService._qualification_in_session(
                    db, camera_id
                )
                if not qualification["qualified"]:
                    raise ValueError("camera has not met Shadow qualification")
            now = self.clock().replace(tzinfo=None)
            old = self.current(camera_id, session=db)
            if old is not None:
                old.effective_to = now
            policy = CameraIdentityBindingPolicy(
                camera_id=camera_id, mode=mode, algorithm_version=algorithm_version,
                parameter_version=parameter_version, effective_from=now,
                change_reason=reason, qualification_snapshot=qualification_snapshot or {},
                rollback_evaluation_id=rollback_evaluation_id,
            )
            db.add(policy)
            db.commit()
            return policy
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")


class TrackAssociationAuditService:
    def __init__(self, session_factory: Callable[[], Session], *, clock: Callable[[], datetime] = datetime.utcnow, evidence_window_seconds: float = 3.0) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.evidence_window = timedelta(seconds=evidence_window_seconds)
        self.policies = CameraIdentityBindingPolicyService(session_factory, clock=clock)

    def record_evaluation(
        self,
        *,
        recognition_event_id: str,
        camera_id: Optional[str],
        runtime_id: Optional[str],
        result: str,
        method: Optional[str],
        reason: Optional[str],
        candidates: list[dict],
        observed_at: datetime,
        algorithm_version: str,
        parameter_version: str,
        policy_version_id: Optional[str],
        mode: str,
        store_id: Optional[str] = None,
        runtime_instance_id: Optional[str] = None,
        runtime_owner_id: Optional[str] = None,
        runtime_key: Optional[str] = None,
        excluded_candidates: Optional[list[dict]] = None,
        candidate_count: Optional[int] = None,
        effective_occurred_at: Optional[datetime] = None,
        time_delta_seconds: Optional[float] = None,
        watermark_at: Optional[datetime] = None,
        window_complete: bool = True,
        policy_snapshot: Optional[dict] = None,
        spatial_evidence: Optional[dict] = None,
        applied_binding: bool = False,
    ) -> TrackAssociationEvaluation:
        if mode not in {"shadow", "enabled", "rolled_back"}:
            raise ValueError("invalid evaluation mode")
        # Compatibility-only input. Durable Binding becomes the sole
        # application fact in Phase 2B; Evaluation always records False.
        fingerprint = self._assessment_fingerprint(
            recognition_event_id=recognition_event_id,
            camera_id=camera_id,
            runtime_instance_id=runtime_instance_id or runtime_id,
            result=result,
            reason=reason,
            candidates=candidates,
            spatial_evidence=spatial_evidence,
            effective_occurred_at=effective_occurred_at or observed_at,
            algorithm_version=algorithm_version,
            parameter_version=parameter_version,
            policy_version_id=policy_version_id,
        )
        db = self.session_factory()
        try:
            self._begin(db)
            existing = db.query(TrackAssociationEvaluation).filter(
                TrackAssociationEvaluation.recognition_event_id == recognition_event_id,
                TrackAssociationEvaluation.assessment_fingerprint == fingerprint,
            ).one_or_none()
            if existing is not None:
                db.commit()
                db.refresh(existing)
                db.expunge(existing)
                return existing
            snapshot = dict(policy_snapshot or {})
            if camera_id and not snapshot:
                current = self.policies.current(camera_id, session=db)
                if current is not None:
                    snapshot = {
                        "mode": current.mode,
                        "algorithm_version": current.algorithm_version,
                        "parameter_version": current.parameter_version,
                        "effective_from": current.effective_from.isoformat(),
                    }
                    policy_version_id = policy_version_id or current.id
            evaluation = TrackAssociationEvaluation(
                recognition_event_id=recognition_event_id,
                assessment_fingerprint=fingerprint,
                store_id=store_id,
                camera_id=camera_id,
                runtime_id=runtime_id,
                runtime_owner_id=runtime_owner_id,
                runtime_instance_id=runtime_instance_id or runtime_id,
                runtime_key=runtime_key,
                result=result, method=method, reason=reason, candidates=candidates,
                excluded_candidates=excluded_candidates or [],
                candidate_count=(len(candidates) if candidate_count is None else candidate_count),
                effective_occurred_at=effective_occurred_at or observed_at,
                observed_at=observed_at, time_delta_seconds=time_delta_seconds,
                watermark_at=watermark_at, window_complete=window_complete,
                algorithm_version=algorithm_version, parameter_version=parameter_version,
                policy_version_id=policy_version_id, policy_snapshot=snapshot,
                spatial_evidence=spatial_evidence,
                applied_binding=False, mode=mode,
                evidence_status=(
                    "pending" if is_confirmed_assessment(result) else "unavailable"
                ),
            )
            db.add(evaluation)
            db.flush()
            if is_confirmed_assessment(result) and camera_id:
                evidence_at = effective_occurred_at or observed_at
                clip = EvidenceClip(
                    owner_type="track_association_evaluation", owner_id=evaluation.id,
                    camera_id=camera_id, requested_start=evidence_at - self.evidence_window,
                    requested_end=evidence_at + self.evidence_window, status="pending",
                )
                db.add(clip)
                db.flush()
                evaluation.evidence_clip_id = clip.id
            db.commit()
            db.refresh(evaluation)
            db.expunge(evaluation)
            return evaluation
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _assessment_fingerprint(**values) -> str:
        def normalize(value):
            if isinstance(value, datetime):
                return value.isoformat()
            return value
        payload = {
            key: normalize(value)
            for key, value in values.items()
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def review(self, *, evaluation_id: str, verdict: str, reviewer: str, reason: Optional[str] = None) -> TrackAssociationReview:
        if verdict not in {"correct", "incorrect", "uncertain"}:
            raise ValueError("invalid review verdict")
        db = self.session_factory()
        try:
            self._begin(db)
            now = self.clock().replace(tzinfo=None)
            evaluation = db.get(TrackAssociationEvaluation, evaluation_id)
            if evaluation is None:
                raise ValueError("evaluation not found")
            if is_confirmed_assessment(evaluation.result) and evaluation.evidence_status != "ready" and verdict != "uncertain":
                raise ValueError("confirmed evaluation without playable evidence requires uncertain review")
            prior = db.query(TrackAssociationReview).filter(
                TrackAssociationReview.evaluation_id == evaluation_id,
                TrackAssociationReview.superseded_by_review_id.is_(None),
            ).one_or_none()
            review = TrackAssociationReview(
                id=new_uuid(), evaluation_id=evaluation_id, verdict=verdict,
                reason=reason, reviewer=reviewer, reviewed_at=now,
            )
            db.add(review)
            if prior is not None:
                prior.superseded_by_review_id = review.id
            if verdict == "incorrect" and is_confirmed_assessment(evaluation.result):
                current = self.policies.current(evaluation.camera_id, session=db)
                if current and current.mode == "enabled" and current.id == evaluation.policy_version_id:
                    current.effective_to = now
                    db.add(CameraIdentityBindingPolicy(
                        camera_id=current.camera_id, mode="rolled_back",
                        algorithm_version=current.algorithm_version, parameter_version=current.parameter_version,
                        effective_from=now, change_reason=reason or "confirmed_review_incorrect",
                        qualification_snapshot=current.qualification_snapshot or {}, rollback_evaluation_id=evaluation.id,
                    ))
            db.commit()
            return review
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def qualification(self, camera_id: str) -> dict:
        db = self.session_factory()
        try:
            return self._qualification_in_session(db, camera_id)
        finally:
            db.close()

    @staticmethod
    def _qualification_in_session(db: Session, camera_id: str) -> dict:
        evaluations = db.query(TrackAssociationEvaluation).filter(
            TrackAssociationEvaluation.camera_id == camera_id,
        ).all()
        accepted = len({row.recognition_event_id for row in evaluations})
        confirmed = [
            row for row in evaluations if is_confirmed_assessment(row.result)
        ]
        reviews = {
            row.evaluation_id: row.verdict
            for row in db.query(TrackAssociationReview).filter(
                TrackAssociationReview.superseded_by_review_id.is_(None)
            ).all()
        }
        correct = sum(reviews.get(row.id) == "correct" for row in confirmed)
        incorrect = sum(reviews.get(row.id) == "incorrect" for row in confirmed)
        days = len({row.observed_at.date() for row in evaluations})
        all_confirmed_reviewed_correct = bool(confirmed) and correct == len(confirmed)
        return {
            "accepted": accepted, "confirmed": len(confirmed), "confirmed_correct": correct,
            "confirmed_incorrect": incorrect, "operating_days": days,
            "qualified": (
                accepted >= 100
                and len(confirmed) >= 50
                and correct >= 50
                and all_confirmed_reviewed_correct
                and incorrect == 0
                and days >= 3
            ),
        }

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
