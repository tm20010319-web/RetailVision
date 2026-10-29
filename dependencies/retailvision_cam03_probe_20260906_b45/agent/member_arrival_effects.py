"""Claim/ACK seam for durable Member Arrival Effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agent.models import Member, MemberArrival, MemberArrivalEffect, Visit


@dataclass(frozen=True)
class ClaimedArrivalEffect:
    effect_id: str
    member_arrival_id: str
    effect_type: str
    claim_token: str
    claim_expires_at: datetime
    deadline_at: Optional[datetime]
    member_id: str
    visit_id: str


class MemberArrivalEffectService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = datetime.utcnow,
        claim_seconds: float = 30.0,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.claim_duration = timedelta(seconds=claim_seconds)

    def claim(
        self, *, effect_type: str, consumer_id: str
    ) -> Optional[ClaimedArrivalEffect]:
        if not effect_type or not consumer_id:
            raise ValueError("effect_type and consumer_id are required")
        db = self.session_factory()
        try:
            self._begin(db)
            now = self.clock().replace(tzinfo=None)
            self._suppress_expired(db, effect_type, now)
            row = (
                db.query(MemberArrivalEffect)
                .join(MemberArrival, MemberArrival.id == MemberArrivalEffect.member_arrival_id)
                .join(Visit, Visit.id == MemberArrival.visit_id)
                .filter(
                    MemberArrivalEffect.effect_type == effect_type,
                    or_(
                        MemberArrivalEffect.status.in_(("pending", "failed")),
                        MemberArrivalEffect.status == "processing",
                    ),
                    MemberArrivalEffect.superseded_by_effect_id.is_(None),
                    MemberArrival.superseded_by_arrival_id.is_(None),
                    Visit.superseded_by_visit_id.is_(None),
                    or_(
                        MemberArrivalEffect.next_retry_at.is_(None),
                        MemberArrivalEffect.next_retry_at <= now,
                    ),
                    or_(
                        MemberArrivalEffect.deadline_at.is_(None),
                        MemberArrivalEffect.deadline_at > now,
                    ),
                    or_(
                        MemberArrivalEffect.claim_expires_at.is_(None),
                        MemberArrivalEffect.claim_expires_at <= now,
                    ),
                )
                .order_by(MemberArrivalEffect.created_at, MemberArrivalEffect.id)
                .first()
            )
            if row is None:
                db.commit()
                return None
            token = uuid4().hex
            row.status = "processing"
            row.consumer_id = consumer_id
            row.claim_token = token
            row.locked_at = now
            row.claim_expires_at = now + self.claim_duration
            row.attempts = (row.attempts or 0) + 1
            arrival = db.get(MemberArrival, row.member_arrival_id)
            db.commit()
            return ClaimedArrivalEffect(
                row.id,
                row.member_arrival_id,
                row.effect_type,
                token,
                row.claim_expires_at,
                row.deadline_at,
                arrival.member_id,
                arrival.visit_id,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def authorize_execution(
        self, *, effect_id: str, consumer_id: str, claim_token: str
    ) -> bool:
        """Recheck the lease and deadline immediately before external execution."""
        db = self.session_factory()
        try:
            self._begin(db)
            now = self.clock().replace(tzinfo=None)
            row = db.get(MemberArrivalEffect, effect_id)
            if (
                row is None
                or row.status != "processing"
                or row.consumer_id != consumer_id
                or row.claim_token != claim_token
                or row.claim_expires_at is None
                or row.claim_expires_at <= now
            ):
                db.rollback()
                return False
            if row.deadline_at is not None and row.deadline_at <= now:
                row.status = "suppressed"
                row.reason = "arrival_effect_delay_exceeded"
                row.claim_token = None
                row.consumer_id = None
                row.locked_at = None
                row.claim_expires_at = None
                row.updated_at = now
                db.commit()
                return False
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def ack(
        self,
        *,
        effect_id: str,
        consumer_id: str,
        claim_token: str,
        executed_at: datetime,
    ) -> bool:
        db = self.session_factory()
        try:
            self._begin(db)
            now = self.clock().replace(tzinfo=None)
            row = db.get(MemberArrivalEffect, effect_id)
            if row is None:
                return False
            same_claim = row.consumer_id == consumer_id and row.claim_token == claim_token
            if row.status == "succeeded":
                db.commit()
                return bool(same_claim)
            if (
                row.status != "processing"
                or not same_claim
                or row.claim_expires_at is None
                or row.claim_expires_at <= now
                or (row.deadline_at is not None and executed_at > row.deadline_at)
            ):
                db.rollback()
                return False
            row.status = "succeeded"
            row.executed_at = executed_at
            row.updated_at = now
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def fail(
        self,
        *,
        effect_id: str,
        consumer_id: str,
        claim_token: str,
        reason: str,
        retry_after_seconds: float = 1.0,
    ) -> bool:
        db = self.session_factory()
        try:
            self._begin(db)
            now = self.clock().replace(tzinfo=None)
            row = db.get(MemberArrivalEffect, effect_id)
            if (
                row is None
                or row.status != "processing"
                or row.consumer_id != consumer_id
                or row.claim_token != claim_token
            ):
                db.rollback()
                return False
            row.status = "failed"
            row.reason = reason
            row.next_retry_at = now + timedelta(seconds=retry_after_seconds)
            row.claim_token = None
            row.consumer_id = None
            row.locked_at = None
            row.claim_expires_at = None
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _suppress_expired(db: Session, effect_type: str, now: datetime) -> None:
        (
            db.query(MemberArrivalEffect)
            .filter(
                MemberArrivalEffect.effect_type == effect_type,
                MemberArrivalEffect.status.in_(("pending", "failed", "processing")),
                MemberArrivalEffect.superseded_by_effect_id.is_(None),
                MemberArrivalEffect.deadline_at.is_not(None),
                MemberArrivalEffect.deadline_at <= now,
            )
            .update(
                {
                    MemberArrivalEffect.status: "suppressed",
                    MemberArrivalEffect.reason: "arrival_effect_delay_exceeded",
                },
                synchronize_session=False,
            )
        )

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
