"""Durable Inbox delivery, separate from member identity association."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import threading
from typing import Any, Callable, List, Optional
from uuid import uuid4

from sqlalchemy import func

from agent.device_identity import DeviceRecognitionEvent
from agent.models import DeviceRecognitionInbox


logger = logging.getLogger(__name__)
_ALLOWED_SUBMISSION_ERROR_TYPES = frozenset(
    {
        "ConnectionError",
        "RuntimeError",
        "TimeoutError",
        "TypeError",
        "ValueError",
    }
)


@dataclass(frozen=True)
class _ClaimedEvent:
    event: DeviceRecognitionEvent
    locked_at: datetime
    claim_token: str


class DeviceRecognitionInboxWorker:
    """Claim persisted recognition events and deliver them at least once."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        coordinator: Any,
        intake: Any,
        member_arrival: Any = None,
        reconcile_waiting: Optional[Callable[..., Any]] = None,
        poll_interval_seconds: float = 0.25,
        processing_timeout_seconds: float = 300.0,
        mapping_retention_seconds: float = 86400.0,
        batch_size: int = 100,
        clock: Callable[[], datetime] = datetime.now,
        identity_association_mode: str = "legacy",
        new_mode_store_id: Optional[str] = None,
        association_deadline_seconds: float = 5.0,
        min_received_at: Optional[datetime] = None,
    ) -> None:
        if identity_association_mode not in {"new", "legacy"}:
            raise ValueError("identity_association_mode must be new or legacy")
        if identity_association_mode == "new" and coordinator is not None:
            raise ValueError("new mode must not configure EntryEventCoordinator")
        if identity_association_mode == "new" and not new_mode_store_id:
            raise ValueError("new mode requires an explicit store_id")
        if identity_association_mode == "legacy" and coordinator is None:
            raise ValueError("legacy mode requires EntryEventCoordinator")
        self.session_factory = session_factory
        self.coordinator = coordinator
        self.intake = intake
        if intake is None:
            raise ValueError("intake is required")
        self.member_arrival = member_arrival
        self.reconcile_waiting = reconcile_waiting
        if self.reconcile_waiting is None:
            raise ValueError("reconcile_waiting is required")
        self.poll_interval_seconds = poll_interval_seconds
        self.processing_timeout_seconds = processing_timeout_seconds
        self.mapping_retention_seconds = mapping_retention_seconds
        self.batch_size = batch_size
        self.clock = clock
        self.identity_association_mode = identity_association_mode
        self.new_mode_store_id = new_mode_store_id
        if association_deadline_seconds <= 0:
            raise ValueError("association_deadline_seconds must be positive")
        self.association_deadline_seconds = association_deadline_seconds
        self.min_received_at = min_received_at
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()

    @staticmethod
    def _event_from_row(row: DeviceRecognitionInbox) -> DeviceRecognitionEvent:
        if not row.member_id:
            raise ValueError("received inbox row has no member_id")
        return DeviceRecognitionEvent(
            device_id=row.device_id,
            event_id=row.canonical_event_id,
            member_id=row.member_id,
            occurred_at=row.occurred_at,
            similarity=float(row.similarity or 0.0),
            source=row.source,
            source_event_id=row.source_event_id,
            capture_path=row.capture_path,
            group_id=row.group_id,
            person_id=row.person_id,
            unique_id=row.unique_id or "",
            rule_id=row.rule_id,
        )

    def _claim_batch(self) -> List[_ClaimedEvent]:
        session = self.session_factory()
        locked_at = self.clock()
        try:
            dialect_name = session.get_bind().dialect.name
            if dialect_name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")

            query = session.query(DeviceRecognitionInbox).filter(
                DeviceRecognitionInbox.status == "received",
            ).order_by(
                DeviceRecognitionInbox.received_at,
                DeviceRecognitionInbox.canonical_event_id,
            )
            if self.min_received_at is not None:
                query = query.filter(
                    func.datetime(DeviceRecognitionInbox.received_at)
                    >= self.min_received_at.replace(microsecond=0).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )
            if dialect_name != "sqlite":
                query = query.with_for_update(skip_locked=True)
            rows = query.limit(self.batch_size).all()

            claimed: List[_ClaimedEvent] = []
            for row in rows:
                claim_token = str(uuid4())
                updated = session.query(DeviceRecognitionInbox).filter(
                    DeviceRecognitionInbox.canonical_event_id
                    == row.canonical_event_id,
                    DeviceRecognitionInbox.status == "received",
                ).update(
                    {
                        DeviceRecognitionInbox.status: "processing",
                        DeviceRecognitionInbox.locked_at: locked_at,
                        DeviceRecognitionInbox.claim_token: claim_token,
                        DeviceRecognitionInbox.last_error: None,
                    },
                    synchronize_session=False,
                )
                if updated:
                    claimed.append(
                        _ClaimedEvent(
                            self._event_from_row(row), locked_at, claim_token
                        )
                    )
            session.commit()
            return claimed
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _complete(
        self,
        claim: _ClaimedEvent,
        *,
        status: str = "processed",
        last_error: Optional[str] = None,
    ) -> bool:
        session = self.session_factory()
        try:
            updated = session.query(DeviceRecognitionInbox).filter(
                DeviceRecognitionInbox.canonical_event_id == claim.event.event_id,
                DeviceRecognitionInbox.status == "processing",
                DeviceRecognitionInbox.claim_token == claim.claim_token,
            ).update(
                {
                    DeviceRecognitionInbox.status: status,
                    DeviceRecognitionInbox.processed_at: self.clock(),
                    DeviceRecognitionInbox.locked_at: None,
                    DeviceRecognitionInbox.claim_token: None,
                    DeviceRecognitionInbox.last_error: last_error,
                },
                synchronize_session=False,
            )
            session.commit()
            return bool(updated)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _release(self, claim: _ClaimedEvent, error: Exception) -> None:
        error_type = type(error).__name__
        if error_type not in _ALLOWED_SUBMISSION_ERROR_TYPES:
            error_type = "Exception"
        error_code = f"submission_failed:{error_type}"
        session = self.session_factory()
        try:
            session.query(DeviceRecognitionInbox).filter(
                DeviceRecognitionInbox.canonical_event_id == claim.event.event_id,
                DeviceRecognitionInbox.status == "processing",
                DeviceRecognitionInbox.claim_token == claim.claim_token,
            ).update(
                {
                    DeviceRecognitionInbox.status: "received",
                    DeviceRecognitionInbox.locked_at: None,
                    DeviceRecognitionInbox.claim_token: None,
                    DeviceRecognitionInbox.last_error: error_code,
                },
                synchronize_session=False,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def run_once(self) -> int:
        self.reconcile_waiting(
            self.clock(),
            limit=self.batch_size,
        )
        processed = 0
        for claim in self._claim_batch():
            try:
                if self.identity_association_mode == "new":
                    if self.member_arrival is None:
                        raise RuntimeError("new mode requires compatibility recorder")
                    self.member_arrival.record_compatibility_event(claim.event)
                    session = self.session_factory()
                    try:
                        row = session.get(DeviceRecognitionInbox, claim.event.event_id)
                        row.store_id = self.new_mode_store_id
                        row.recognition_status = "accepted"
                        row.store_resolution_status = "resolved"
                        row.association_status = "pending"
                        row.association_deadline_at = (
                            row.occurred_at + timedelta(seconds=self.association_deadline_seconds)
                        )
                        session.commit()
                    finally:
                        session.close()
                    if self._complete(claim):
                        processed += 1
                    continue
                if self.member_arrival is not None:
                    prepare = getattr(self.member_arrival, "prepare_member_arrival", None)
                    if callable(prepare):
                        preparation = prepare(claim.event)
                    else:
                        preparation = self.member_arrival.prepare_recognition(
                            {
                                "device_event_id": claim.event.event_id,
                                "member_id": claim.event.member_id,
                                "camera_id": claim.event.device_id,
                                "occurred_at": claim.event.occurred_at,
                                "entry_time": claim.event.occurred_at,
                                "identity_time": claim.event.occurred_at,
                                "confidence": claim.event.similarity,
                                "identity_source": "device_api",
                            }
                        )
                    if preparation == "ignored":
                        if self._complete(
                            claim,
                            status="ignored",
                            last_error="member_debounce",
                        ):
                            processed += 1
                        continue
                self.coordinator.submit_recognition(claim.event)
            except Exception as error:
                self._release(claim, error)
                logger.warning(
                    "Recognition inbox submission failed: event_id=%s error_type=%s",
                    claim.event.event_id,
                    type(error).__name__,
                )
                continue
            if self._complete(claim):
                processed += 1
        return processed

    def recover_stale(self) -> int:
        cutoff = self.clock() - timedelta(seconds=self.processing_timeout_seconds)
        session = self.session_factory()
        try:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            recovered = session.query(DeviceRecognitionInbox).filter(
                DeviceRecognitionInbox.status == "processing",
                DeviceRecognitionInbox.locked_at.is_not(None),
                DeviceRecognitionInbox.locked_at <= cutoff,
            ).update(
                {
                    DeviceRecognitionInbox.status: "received",
                    DeviceRecognitionInbox.locked_at: None,
                    DeviceRecognitionInbox.claim_token: None,
                    DeviceRecognitionInbox.last_error: "stale_processing_recovered",
                },
                synchronize_session=False,
            )
            session.commit()
            return int(recovered)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.recover_stale()
                self.run_once()
            except Exception as error:
                logger.error(
                    "Recognition inbox iteration failed: error_type=%s",
                    type(error).__name__,
                )
            self._stop.wait(max(0.0, self.poll_interval_seconds))

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    raise RuntimeError("Recognition inbox worker is still stopping")
                return
            self.recover_stale()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self.run_forever,
                name="device-recognition-inbox",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                self._stop.set()
                raise

    def stop(self, timeout: float = 3.0) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            if self._thread is None:
                return
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                raise RuntimeError("Recognition inbox worker thread is still running")
            self._thread = None
