"""Eventually consistent Association Outcome projection worker."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agent.models import AssociationOutcomeRebuildJob, DeviceRecognitionInbox, MemberArrival, Visit
from agent.visit_merge_service import resolve_canonical_visit_id


class AssociationOutcomeProjectionWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = datetime.utcnow,
        claim_seconds: float = 30.0,
        batch_size: int = 100,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.claim_duration = timedelta(seconds=claim_seconds)
        self.batch_size = batch_size

    def run_once(self) -> int:
        db = self.session_factory()
        now = self.clock().replace(tzinfo=None)
        processed = 0
        try:
            self._begin(db)
            jobs = (
                db.query(AssociationOutcomeRebuildJob)
                .filter(
                    AssociationOutcomeRebuildJob.status.in_(("pending", "failed")),
                    or_(
                        AssociationOutcomeRebuildJob.next_retry_at.is_(None),
                        AssociationOutcomeRebuildJob.next_retry_at <= now,
                    ),
                    or_(
                        AssociationOutcomeRebuildJob.claim_expires_at.is_(None),
                        AssociationOutcomeRebuildJob.claim_expires_at <= now,
                    ),
                )
                .order_by(AssociationOutcomeRebuildJob.created_at, AssociationOutcomeRebuildJob.id)
                .limit(self.batch_size)
                .all()
            )
            for job in jobs:
                token = uuid4().hex
                job.status = "processing"
                job.claim_token = token
                job.locked_at = now
                job.claim_expires_at = now + self.claim_duration
                job.attempts = (job.attempts or 0) + 1
                try:
                    with db.begin_nested():
                        projected_event_ids = self._project_job(db, job, now)
                    job.status = "succeeded"
                    job.next_retry_at = None
                    job.last_error = None
                    job.projected_at = now
                    for event_id in projected_event_ids:
                        row = db.get(DeviceRecognitionInbox, event_id)
                        if row is not None:
                            row.projection_pending = db.query(
                                AssociationOutcomeRebuildJob.id
                            ).filter(
                                AssociationOutcomeRebuildJob.id != job.id,
                                AssociationOutcomeRebuildJob.recognition_event_id == event_id,
                                AssociationOutcomeRebuildJob.status.in_(("pending", "failed", "processing")),
                            ).first() is not None
                    processed += 1
                except Exception as error:
                    job.status = "failed"
                    job.next_retry_at = now + timedelta(seconds=min(60, 2 ** min(job.attempts, 6)))
                    job.last_error = f"{type(error).__name__}: {error}"[:512]
                job.claim_token = None
                job.locked_at = None
                job.claim_expires_at = None
            db.commit()
            return processed
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _project_job(
        self, db: Session, job: AssociationOutcomeRebuildJob, now: datetime
    ) -> set[str]:
        event_ids = {job.recognition_event_id} if job.recognition_event_id else set()
        if job.arrival_id:
            arrival = db.get(MemberArrival, job.arrival_id)
            if arrival and arrival.recognition_event_id:
                event_ids.add(arrival.recognition_event_id)
        if job.visit_id:
            canonical_id = resolve_canonical_visit_id(db, job.visit_id)
            event_ids.update(
                item[0]
                for item in db.query(DeviceRecognitionInbox.canonical_event_id)
                .filter(DeviceRecognitionInbox.matched_visit_id == canonical_id)
                .all()
            )
        for event_id in event_ids:
            row = db.get(DeviceRecognitionInbox, event_id)
            if row is not None:
                row.association_outcome = self._outcome(db, row)
        return event_ids

    @staticmethod
    def _outcome(db: Session, row: DeviceRecognitionInbox) -> str:
        arrival = (
            db.query(MemberArrival)
            .filter(
                MemberArrival.recognition_event_id == row.canonical_event_id,
                MemberArrival.superseded_by_arrival_id.is_(None),
            )
            .one_or_none()
        )
        if arrival is not None:
            visit = db.get(Visit, arrival.visit_id)
            if (
                visit is not None
                and visit.superseded_by_visit_id is None
                and visit.member_id == arrival.member_id
            ):
                return "new_member_arrival"
        if (
            row.recognition_status == "accepted"
            and row.store_resolution_status == "resolved"
            and row.matched_visit_id
        ):
            visit_id = resolve_canonical_visit_id(db, row.matched_visit_id)
            visit = db.get(Visit, visit_id)
            if visit is not None and visit.superseded_by_visit_id is None:
                return "existing_visit_observation"
        if row.recognition_status in {
            "unmapped", "expired", "time_anomaly", "device_identity_conflict",
        } or row.store_resolution_status in {"conflict", "expired"}:
            return "no_business_outcome"
        return "pending"

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
