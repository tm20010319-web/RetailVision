"""Transactional member-entry visit finalization and retry recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.member_service import MemberService
from agent.models import (
    BusinessJob,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
    Visit,
)


class SnapshotResolver(Protocol):
    """Resolves an already captured stable-track crop without performing capture."""

    def get(self, camera_id: str, track_id: int) -> Optional[str]:
        ...


@dataclass(frozen=True)
class MemberEntryResult:
    event_id: str
    visit_id: str
    profile_job_status: Optional[str]


class MemberEntryService:
    """Finalizes durable recognition inbox rows in one database transaction."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        snapshot_resolver: Optional[SnapshotResolver] = None,
        *,
        profile_enabled: bool = True,
        profile_job_max_attempts: int = 5,
        association_wait_seconds: float = 3.0,
        member_debounce_seconds: float = 10.0,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        if (
            isinstance(profile_job_max_attempts, bool)
            or not isinstance(profile_job_max_attempts, int)
            or profile_job_max_attempts <= 0
        ):
            raise ValueError(
                "profile_job_max_attempts must be a positive integer"
            )
        self.session_factory = session_factory
        self.snapshot_resolver = snapshot_resolver
        self.profile_enabled = profile_enabled
        self.profile_job_max_attempts = profile_job_max_attempts
        self.association_wait = timedelta(seconds=association_wait_seconds)
        self.member_debounce = timedelta(seconds=member_debounce_seconds)
        self.clock = clock

    def prepare_recognition(self, session: Mapping[str, Any]) -> str:
        """Create the durable Member Arrival candidate after Inbox intake."""
        event_id = session.get("device_event_id")
        member_id = session.get("member_id")
        device_id = session.get("camera_id") or session.get("device_id")
        occurred_at = session.get("entry_time") or session.get("occurred_at")
        if not all(isinstance(value, str) and value for value in (event_id, member_id, device_id)):
            raise ValueError("recognition requires event, member and device identifiers")
        if not isinstance(occurred_at, datetime):
            raise ValueError("recognition requires occurred_at")

        db = self.session_factory()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = db.get(MemberEntryEvent, event_id)
            if existing is not None:
                return "already_prepared"
            inbox = db.get(DeviceRecognitionInbox, event_id)
            if inbox is None or inbox.member_id != member_id:
                raise ValueError(f"recognition inbox event not found: {event_id}")
            if db.get_bind().dialect.name != "sqlite":
                db.query(Member).filter(Member.id == member_id).with_for_update().one()
            previous = (
                db.query(MemberEntryEvent)
                .filter(
                    MemberEntryEvent.device_id == device_id,
                    MemberEntryEvent.member_id == member_id,
                    MemberEntryEvent.event_id != event_id,
                    MemberEntryEvent.occurred_at >= occurred_at - self.member_debounce,
                    MemberEntryEvent.occurred_at <= occurred_at + self.member_debounce,
                )
                .first()
            )
            if previous is not None:
                db.rollback()
                return "ignored"
            db.add(
                MemberEntryEvent(
                    event_id=event_id,
                    device_id=device_id,
                    member_id=member_id,
                    occurred_at=occurred_at,
                    similarity=float(session.get("confidence") or 0.0),
                    identity_source=session.get("identity_source") or "device_api",
                    identity_time=session.get("identity_time") or occurred_at,
                    status="recognized",
                    snapshot_path=inbox.capture_path,
                )
            )
            db.commit()
            return "prepared"
        except IntegrityError:
            db.rollback()
            if db.get(MemberEntryEvent, event_id) is not None:
                return "already_prepared"
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_compatibility_event(self, event: Any) -> str:
        """Persist one record per device recognition without member-arrival logic."""
        event_id = getattr(event, "event_id", None)
        member_id = getattr(event, "member_id", None)
        device_id = getattr(event, "device_id", None)
        occurred_at = getattr(event, "occurred_at", None)
        if not all(isinstance(value, str) and value for value in (event_id, member_id, device_id)):
            raise ValueError("recognition requires event, member and device identifiers")
        if not isinstance(occurred_at, datetime):
            raise ValueError("recognition requires occurred_at")
        db = self.session_factory()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = db.get(MemberEntryEvent, event_id)
            if existing is not None:
                return "already_recorded"
            inbox = db.get(DeviceRecognitionInbox, event_id)
            if inbox is None or inbox.member_id != member_id:
                raise ValueError(f"recognition inbox event not found: {event_id}")
            db.add(MemberEntryEvent(
                event_id=event_id,
                device_id=device_id,
                member_id=member_id,
                occurred_at=occurred_at,
                similarity=float(getattr(event, "similarity", 0.0)),
                identity_source="device_api",
                identity_time=occurred_at,
                status="recognized",
                snapshot_path=inbox.capture_path,
            ))
            db.commit()
            return "recorded"
        except IntegrityError:
            db.rollback()
            if db.get(MemberEntryEvent, event_id) is not None:
                return "already_recorded"
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def reconcile_captures(self, limit: int = 100) -> int:
        """Adopt a later Inbox capture without touching completed results."""
        db = self.session_factory()
        adopted = 0
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            rows = (
                db.query(MemberEntryEvent, DeviceRecognitionInbox)
                .join(
                    DeviceRecognitionInbox,
                    DeviceRecognitionInbox.canonical_event_id == MemberEntryEvent.event_id,
                )
                .filter(
                    DeviceRecognitionInbox.capture_path.is_not(None),
                    MemberEntryEvent.snapshot_path.is_(None),
                )
                .order_by(MemberEntryEvent.occurred_at)
                .limit(limit)
                .all()
            )
            for event, inbox in rows:
                event.snapshot_path = inbox.capture_path
                self._reconcile_profile_job_for_capture(db, event, inbox.capture_path)
                adopted += 1
            db.commit()
            return adopted
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def finalize(self, session: Mapping[str, Any]) -> MemberEntryResult:
        """Commit Visit, counters, optional profile job, and inbox status together."""
        event_id = session.get("device_event_id")
        member_id = session.get("member_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("session requires device_event_id")
        if not isinstance(member_id, str) or not member_id:
            raise ValueError("session requires member_id")

        db = self.session_factory()
        try:
            event = db.get(MemberEntryEvent, event_id)
            if event is None:
                raise ValueError(f"member entry event not found: {event_id}")
            if event.member_id != member_id:
                raise ValueError("session member_id does not match member entry event")

            if event.status == "finalized":
                result = self._finalized_result(db, event_id, member_id)
                if result is None:
                    raise ValueError(f"finalized event has no visit: {event_id}")
                return result

            if db.get(Member, member_id) is None:
                raise ValueError(f"member not found: {member_id}")

            existing_job = self._profile_job(db, event_id)

            track_id = session.get("track_id")
            if track_id is not None:
                event.track_id = track_id
            if session.get("detection_bbox") is not None:
                event.bbox = list(session["detection_bbox"])
            session_snapshot_path = session.get("snapshot_path")
            if isinstance(session_snapshot_path, str) and session_snapshot_path:
                event.snapshot_path = session_snapshot_path

            camera_id = session.get("camera_id") or event.device_id
            zone_name = session.get("zone_name") or "entrance"
            entry_time = self._as_datetime(session.get("entry_time"), event.occurred_at)
            identity_time = self._as_datetime(session.get("identity_time"), event.identity_time)
            confidence = session.get("confidence", event.similarity)
            prebound_visit_id = session.get("visit_id")
            if isinstance(prebound_visit_id, str) and prebound_visit_id:
                prebound_visit = db.get(Visit, prebound_visit_id)
                if prebound_visit is None or prebound_visit.member_id != member_id:
                    raise ValueError(
                        "prebound visit does not match the member entry"
                    )
                visit_id = prebound_visit.id
            else:
                visit_id = MemberService(db).record_visit(
                    member_id=member_id,
                    zone=zone_name,
                    confidence=confidence,
                    device_event_id=event_id,
                    entry_time=entry_time,
                    camera_id=camera_id,
                    track_id=track_id,
                    detection_bbox=event.bbox,
                    identity_time=identity_time,
                    commit=False,
                )
            if visit_id is None:
                raise RuntimeError(f"could not create visit for event: {event_id}")

            profile_job_status = existing_job.status if existing_job else None
            if self.profile_enabled and existing_job is None:
                snapshot_path = self._first_regular_file(
                    session_snapshot_path,
                    event.snapshot_path,
                )
                crop_path = snapshot_path or self._crop_path(camera_id, track_id)
                if not crop_path:
                    if track_id is None and session.get("identity_source") != "device_api":
                        reason = "skipped_no_track"
                    else:
                        reason = "skipped_no_snapshot"
                    profile_job_status, result = "skipped", {"reason": reason}
                    payload = {}
                else:
                    profile_job_status, result = "pending", None
                    payload = {
                        "crop_path": crop_path,
                        "track_id": track_id,
                        "camera_id": camera_id,
                        "zone_name": zone_name,
                        "member_id": member_id,
                        "event_id": event_id,
                    }
                db.add(BusinessJob(
                    event_id=event_id,
                    member_id=member_id,
                    task_type="customer_profile",
                    status=profile_job_status,
                    max_attempts=self.profile_job_max_attempts,
                    payload=payload,
                    result=result,
                ))

            event.visit_id = visit_id
            event.status = "finalized"
            event.next_attempt_at = None
            event.last_error = None
            db.commit()
            return MemberEntryResult(event_id, visit_id, profile_job_status)
        except IntegrityError:
            db.rollback()
            result = self._result_after_integrity_race(event_id, member_id)
            if result is not None:
                return result
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def recover_due(self, limit: int = 100) -> int:
        """Finalize recognized inbox events that outlived track association wait."""
        now = self.clock()
        db = self.session_factory()
        try:
            due_events = (
                db.query(MemberEntryEvent)
                .filter(
                    MemberEntryEvent.status == "recognized",
                    MemberEntryEvent.occurred_at <= now - self.association_wait,
                    or_(
                        MemberEntryEvent.next_attempt_at.is_(None),
                        MemberEntryEvent.next_attempt_at <= now,
                    ),
                )
                .order_by(MemberEntryEvent.occurred_at)
                .limit(limit)
                .all()
            )
            recoveries = [
                {
                    "device_event_id": event.event_id,
                    "member_id": event.member_id,
                    "camera_id": event.device_id,
                    "track_id": None,
                    "zone_name": "entrance",
                    "confidence": event.similarity,
                    "entry_time": event.occurred_at,
                    "identity_time": event.identity_time,
                    "identity_source": event.identity_source,
                    "snapshot_path": event.snapshot_path,
                }
                for event in due_events
            ]
        finally:
            db.close()

        finalized = 0
        for recovery_session in recoveries:
            try:
                self.finalize(recovery_session)
                finalized += 1
            except Exception as error:
                self._record_recovery_failure(recovery_session["device_event_id"], error, now)
        return finalized

    def _reconcile_profile_job_for_capture(
        self, db: Session, member_event: MemberEntryEvent, capture_path: str
    ) -> None:
        job = (
            db.query(BusinessJob)
            .with_for_update()
            .filter(
                BusinessJob.event_id == member_event.event_id,
                BusinessJob.task_type == "customer_profile",
            )
            .one_or_none()
        )
        if job is None:
            return
        if job.status in {"pending", "failed"}:
            payload = dict(job.payload or {})
            payload["crop_path"] = capture_path
            job.payload = payload
            return
        if job.status == "skipped" and isinstance(job.result, dict) and job.result.get("reason") == "skipped_no_snapshot":
            job.status = "pending"
            job.payload = {
                "crop_path": capture_path,
                "track_id": member_event.track_id,
                "camera_id": member_event.device_id,
                "zone_name": "entrance",
                "member_id": member_event.member_id,
                "event_id": member_event.event_id,
            }
            job.result = None
            job.last_error = None
            job.locked_at = None
            job.available_at = self.clock()

    def _profile_job(self, db: Session, event_id: str) -> Optional[BusinessJob]:
        return (
            db.query(BusinessJob)
            .filter(
                BusinessJob.event_id == event_id,
                BusinessJob.task_type == "customer_profile",
            )
            .first()
        )

    @staticmethod
    def _visit_id(db: Session, event_id: str) -> Optional[str]:
        visit = db.query(Visit).filter(Visit.device_event_id == event_id).first()
        return visit.id if visit else None

    def _result_after_integrity_race(
        self, event_id: str, member_id: str
    ) -> Optional[MemberEntryResult]:
        db = self.session_factory()
        try:
            return self._finalized_result(db, event_id, member_id)
        finally:
            db.close()

    def _finalized_result(
        self, db: Session, event_id: str, member_id: str
    ) -> Optional[MemberEntryResult]:
        event = db.get(MemberEntryEvent, event_id)
        if (
            event is None
            or event.member_id != member_id
            or event.status != "finalized"
        ):
            return None

        visit_id = event.visit_id or self._visit_id(db, event_id)
        if visit_id is None:
            return None
        visit = db.get(Visit, visit_id)
        if visit is None or visit.device_event_id != event_id:
            return None

        job = self._profile_job(db, event_id)
        return MemberEntryResult(
            event_id=event_id,
            visit_id=visit_id,
            profile_job_status=job.status if job else None,
        )

    def _crop_path(self, camera_id: str, track_id: Any) -> Optional[str]:
        if track_id is None or self.snapshot_resolver is None:
            return None
        return self._first_regular_file(self.snapshot_resolver.get(camera_id, track_id))

    @staticmethod
    def _first_regular_file(*paths: Any) -> Optional[str]:
        for path in paths:
            if not isinstance(path, str) or not path:
                continue
            try:
                if Path(path).is_file():
                    return path
            except OSError:
                continue
        return None

    @staticmethod
    def _as_datetime(value: Any, fallback: datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value)
        return fallback

    def _record_recovery_failure(
        self, event_id: str, error: Exception, now: datetime
    ) -> None:
        db = self.session_factory()
        try:
            event = db.get(MemberEntryEvent, event_id)
            if event is None or event.status != "recognized":
                return
            attempts = (event.finalize_attempts or 0) + 1
            event.finalize_attempts = attempts
            event.last_error = f"{type(error).__name__}: {error}"[:512]
            event.next_attempt_at = now + timedelta(
                seconds=min(300, 2 ** max(0, attempts - 1))
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
