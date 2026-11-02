"""Durable background execution for customer profile jobs."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from sqlalchemy import desc

from agent.member.member_priority import sql_tier_rank
from agent.models import BusinessJob, Member, MemberEntryEvent, Visit


logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class JobLease:
    job_id: str
    locked_at: datetime
    attempts: int


@dataclass(frozen=True)
class ClaimedJob:
    lease: JobLease
    payload: Dict[str, Any]


class ProfileJobWorker:
    """Claim and execute persisted customer profile jobs one at a time."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        pipeline: Any,
        *,
        clock: Callable[[], datetime] = datetime.now,
        poll_interval_seconds: float = 1.0,
        lock_timeout_seconds: float = 300.0,
    ) -> None:
        self.session_factory = session_factory
        self.pipeline = pipeline
        self.clock = clock
        self.poll_interval_seconds = poll_interval_seconds
        self.lock_timeout_seconds = lock_timeout_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()

    def run_once(self) -> bool:
        """Claim and execute one due profile job, if one is available."""
        claimed = self._claim_due_job()
        if claimed is None:
            return False

        payload = claimed.payload
        try:
            result = self.pipeline.run_from_image_path(
                payload["crop_path"],
                track_id=payload["track_id"],
                camera_id=payload.get("camera_id", ""),
                zone_name=payload.get("zone_name", ""),
            )
        except Exception as error:
            self._record_failure(claimed.lease, error)
            return True

        self._record_success(claimed.lease, result)
        return True

    def recover_stale(self) -> int:
        """Return profile jobs with expired running locks to the pending queue."""
        now = self.clock()
        cutoff = now - timedelta(seconds=self.lock_timeout_seconds)
        db = self.session_factory()
        try:
            retryable = (
                db.query(BusinessJob)
                .filter(
                    BusinessJob.task_type == "customer_profile",
                    BusinessJob.status == "running",
                    BusinessJob.locked_at <= cutoff,
                    BusinessJob.attempts < BusinessJob.max_attempts,
                )
                .update(
                    {
                        BusinessJob.status: "pending",
                        BusinessJob.locked_at: None,
                        BusinessJob.available_at: now,
                    },
                    synchronize_session=False,
                )
            )
            exhausted = (
                db.query(BusinessJob)
                .filter(
                    BusinessJob.task_type == "customer_profile",
                    BusinessJob.status == "running",
                    BusinessJob.locked_at <= cutoff,
                    BusinessJob.attempts >= BusinessJob.max_attempts,
                )
                .update(
                    {
                        BusinessJob.status: "failed",
                        BusinessJob.locked_at: None,
                        BusinessJob.last_error: (
                            "Stale profile job lease expired after final attempt"[:512]
                        ),
                    },
                    synchronize_session=False,
                )
            )
            db.commit()
            return retryable + exhausted
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def start(self) -> None:
        """Recover abandoned work and start one daemon polling thread."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stop.is_set():
                    raise RuntimeError("Profile job worker is still stopping")
                return
            self.recover_stale()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._poll_forever,
                name="profile-job-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Request polling shutdown and wait at most two seconds."""
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                raise TimeoutError(
                    "Profile job worker did not stop within 2 seconds"
                )

    def _claim_due_job(self) -> Optional[ClaimedJob]:
        now = self.clock()
        db = self.session_factory()
        try:
            candidate = (
                db.query(BusinessJob.id)
                .outerjoin(Member, Member.id == BusinessJob.member_id)
                .filter(
                    BusinessJob.task_type == "customer_profile",
                    BusinessJob.status.in_(("pending", "failed")),
                    BusinessJob.available_at <= now,
                    BusinessJob.attempts < BusinessJob.max_attempts,
                )
                .order_by(
                    desc(sql_tier_rank(Member.tier)),
                    BusinessJob.available_at,
                    BusinessJob.created_at,
                    BusinessJob.id,
                )
                .first()
            )
            if candidate is None:
                return None

            job_id = candidate[0]
            claimed = (
                db.query(BusinessJob)
                .filter(
                    BusinessJob.id == job_id,
                    BusinessJob.task_type == "customer_profile",
                    BusinessJob.status.in_(("pending", "failed")),
                    BusinessJob.available_at <= now,
                    BusinessJob.attempts < BusinessJob.max_attempts,
                )
                .update(
                    {
                        BusinessJob.status: "running",
                        BusinessJob.locked_at: now,
                        BusinessJob.attempts: BusinessJob.attempts + 1,
                    },
                    synchronize_session=False,
                )
            )
            if claimed != 1:
                db.rollback()
                return None

            job = db.get(BusinessJob, job_id)
            claimed_job = ClaimedJob(
                lease=JobLease(
                    job_id=job_id,
                    locked_at=job.locked_at,
                    attempts=job.attempts,
                ),
                payload=dict(job.payload or {}),
            )
            db.commit()
            return claimed_job
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _record_success(self, lease: JobLease, result: Dict[str, Any]) -> None:
        db = self.session_factory()
        try:
            finalized = (
                db.query(BusinessJob)
                .filter(
                    BusinessJob.id == lease.job_id,
                    BusinessJob.status == "running",
                    BusinessJob.locked_at == lease.locked_at,
                    BusinessJob.attempts == lease.attempts,
                )
                .update(
                    {
                        BusinessJob.result: result,
                        BusinessJob.status: "succeeded",
                        BusinessJob.locked_at: None,
                        BusinessJob.last_error: None,
                    },
                    synchronize_session=False,
                )
            )
            if finalized != 1:
                db.rollback()
                return

            job = db.get(BusinessJob, lease.job_id)
            member = (
                db.query(Member)
                .filter(Member.id == job.member_id)
                .with_for_update()
                .one_or_none()
            )
            if member is not None:
                profile = dict(member.profile or {})
                if self._is_newer_profile_event(db, job, profile):
                    profile["latest_customer_profile"] = result["profile"]
                    profile["latest_customer_profile_event_id"] = job.event_id
                    member.profile = profile
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _is_newer_profile_event(
        db: Any,
        job: BusinessJob,
        profile: Dict[str, Any],
    ) -> bool:
        current_event_id = profile.get("latest_customer_profile_event_id")
        if not isinstance(current_event_id, str) or not current_event_id:
            return True
        if current_event_id == job.event_id:
            return False

        candidate_key = ProfileJobWorker._profile_event_order_key(
            db,
            job.member_id,
            job.event_id,
        )
        current_key = ProfileJobWorker._profile_event_order_key(
            db,
            job.member_id,
            current_event_id,
        )
        if candidate_key is None or current_key is None:
            return False
        return candidate_key > current_key

    @staticmethod
    def _profile_event_order_key(
        db: Any,
        member_id: str,
        event_id: str,
    ) -> Optional[tuple[datetime, str]]:
        event_time = (
            db.query(MemberEntryEvent.occurred_at)
            .filter(
                MemberEntryEvent.event_id == event_id,
                MemberEntryEvent.member_id == member_id,
            )
            .scalar()
        )
        if event_time is None:
            event_time = (
                db.query(Visit.entry_time)
                .filter(
                    Visit.device_event_id == event_id,
                    Visit.member_id == member_id,
                )
                .scalar()
            )
        if event_time is None:
            return None
        return event_time, event_id

    def _record_failure(self, lease: JobLease, error: Exception) -> None:
        db = self.session_factory()
        try:
            failed = (
                db.query(BusinessJob)
                .filter(
                    BusinessJob.id == lease.job_id,
                    BusinessJob.status == "running",
                    BusinessJob.locked_at == lease.locked_at,
                    BusinessJob.attempts == lease.attempts,
                )
                .update(
                    {
                        BusinessJob.status: "failed",
                        BusinessJob.locked_at: None,
                        BusinessJob.last_error: (
                            f"{type(error).__name__}: {error}"[:512]
                        ),
                        BusinessJob.available_at: self.clock()
                        + timedelta(
                            seconds=min(
                                300,
                                2 ** max(0, lease.attempts - 1),
                            )
                        ),
                    },
                    synchronize_session=False,
                )
            )
            if failed != 1:
                db.rollback()
                return
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _poll_forever(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception as error:
                logger.error(
                    "Profile job polling failed: %s",
                    type(error).__name__,
                )
                worked = False
            if not worked:
                self._stop.wait(self.poll_interval_seconds)
