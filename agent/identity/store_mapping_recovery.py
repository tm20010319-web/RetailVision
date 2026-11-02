"""Durable recovery for accepted recognitions waiting on Store Resolution."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agent.models import DeviceRecognitionInbox
from agent.identity.association_transition import RecognitionInboxTransitionService
from agent.identity.store_resolution import StoreResolutionService


class StoreMappingRecoveryWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        resolution: StoreResolutionService,
        *,
        clock: Callable[[], datetime] = datetime.utcnow,
        batch_size: int = 100,
        claim_seconds: float = 30.0,
        max_backoff_seconds: float = 60.0,
    ) -> None:
        self.session_factory = session_factory
        self.resolution = resolution
        self.clock = clock
        self.batch_size = batch_size
        self.claim_duration = timedelta(seconds=claim_seconds)
        self.max_backoff_seconds = max_backoff_seconds

    def run_once(self) -> int:
        db = self.session_factory()
        now = self.clock().replace(tzinfo=None)
        recovered = 0
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            rows = (
                db.query(DeviceRecognitionInbox)
                .filter(
                    DeviceRecognitionInbox.status == "waiting_store_mapping",
                    DeviceRecognitionInbox.recognition_status == "accepted",
                    DeviceRecognitionInbox.store_resolution_status == "missing",
                    or_(
                        DeviceRecognitionInbox.store_mapping_next_retry_at.is_(None),
                        DeviceRecognitionInbox.store_mapping_next_retry_at <= now,
                    ),
                    or_(
                        DeviceRecognitionInbox.store_mapping_claim_expires_at.is_(None),
                        DeviceRecognitionInbox.store_mapping_claim_expires_at <= now,
                    ),
                )
                .order_by(
                    DeviceRecognitionInbox.store_mapping_next_retry_at,
                    DeviceRecognitionInbox.occurred_at,
                )
                .limit(self.batch_size)
                .all()
            )
            for row in rows:
                token = uuid4().hex
                row.store_mapping_claim_token = token
                row.store_mapping_locked_at = now
                row.store_mapping_claim_expires_at = now + self.claim_duration
                row.store_mapping_attempts = (row.store_mapping_attempts or 0) + 1
                row.store_mapping_last_attempt_at = now
                deadline = row.store_mapping_wait_deadline_at or (
                    row.occurred_at + timedelta(seconds=600)
                )
                row.store_mapping_wait_deadline_at = deadline
                if now > deadline:
                    RecognitionInboxTransitionService.no_business_outcome(
                        row,
                        recognition_status="expired",
                        store_resolution_status="expired",
                        reason="store_mapping_unavailable",
                    )
                    row.store_mapping_last_error = "store_mapping_unavailable"
                    self._clear_claim(row)
                    continue
                result = self.resolution.resolve(
                    device_id=row.device_id,
                    occurred_at=row.occurred_at,
                    persisted_store_id=row.store_id,
                    session=db,
                )
                if result.status == "resolved":
                    RecognitionInboxTransitionService.store_resolved(row, result.store_id)
                    row.store_mapping_last_error = None
                    row.store_mapping_next_retry_at = None
                    recovered += 1
                elif result.status == "conflict":
                    row.store_resolution_status = "conflict"
                    row.status = "processed"
                    row.association_status = "terminal"
                    row.association_outcome = "no_business_outcome"
                    row.store_mapping_last_error = "store_mapping_conflict"
                else:
                    row.store_mapping_last_error = "store_mapping_missing"
                    delay = min(
                        self.max_backoff_seconds,
                        float(2 ** min(row.store_mapping_attempts - 1, 6)),
                    )
                    row.store_mapping_next_retry_at = now + timedelta(seconds=delay)
                self._clear_claim(row)
            db.commit()
            return recovered
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _clear_claim(row: DeviceRecognitionInbox) -> None:
        row.store_mapping_claim_token = None
        row.store_mapping_locked_at = None
        row.store_mapping_claim_expires_at = None
