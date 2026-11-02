"""Database-backed fenced ownership for one Store's association Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from agent.models import IdentityAssociationRuntimeLease, IdentityAssociationStoreState


class RuntimeLeaseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeLease:
    store_id: str
    mode: str
    owner_id: str
    lease_token: str
    lease_generation: int
    expires_at: datetime


class IdentityAssociationRuntimeLeaseService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = datetime.utcnow,
        lease_duration_seconds: float = 30.0,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.duration = timedelta(seconds=lease_duration_seconds)

    def acquire(self, *, store_id: str, mode: str, owner_id: str) -> RuntimeLease:
        if mode not in {"new", "legacy"}:
            raise ValueError("mode must be new or legacy")
        db = self.session_factory()
        try:
            self._begin(db)
            now = self.clock().replace(tzinfo=None)
            state = db.get(IdentityAssociationStoreState, store_id)
            if mode == "legacy" and state and state.new_mode_activated_at is not None:
                raise RuntimeLeaseUnavailable("store_already_migrated_to_new")
            row = db.get(IdentityAssociationRuntimeLease, store_id)
            if row is not None and row.expires_at > now:
                raise RuntimeLeaseUnavailable("store_runtime_lease_held")
            generation = (row.lease_generation + 1) if row is not None else 1
            token = uuid4().hex
            expires_at = now + self.duration
            if row is None:
                row = IdentityAssociationRuntimeLease(
                    store_id=store_id,
                    mode=mode,
                    owner_id=owner_id,
                    lease_token=token,
                    lease_generation=generation,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=expires_at,
                )
                db.add(row)
            else:
                row.mode = mode
                row.owner_id = owner_id
                row.lease_token = token
                row.lease_generation = generation
                row.acquired_at = now
                row.heartbeat_at = now
                row.expires_at = expires_at
            db.commit()
            return RuntimeLease(store_id, mode, owner_id, token, generation, expires_at)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def heartbeat(self, lease: RuntimeLease) -> RuntimeLease:
        db = self.session_factory()
        try:
            self._begin(db)
            now = self.clock().replace(tzinfo=None)
            row = db.get(IdentityAssociationRuntimeLease, lease.store_id)
            if not self._matches(row, lease, now):
                raise RuntimeLeaseUnavailable("runtime_lease_lost")
            row.heartbeat_at = now
            row.expires_at = now + self.duration
            db.commit()
            return RuntimeLease(
                lease.store_id, lease.mode, lease.owner_id, lease.lease_token,
                lease.lease_generation, row.expires_at,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def validate(self, lease: RuntimeLease, *, session: Optional[Session] = None) -> None:
        owns_session = session is None
        db = session or self.session_factory()
        try:
            now = self.clock().replace(tzinfo=None)
            row = db.get(IdentityAssociationRuntimeLease, lease.store_id)
            if not self._matches(row, lease, now):
                raise RuntimeLeaseUnavailable("runtime_lease_lost")
        finally:
            if owns_session:
                db.close()

    def validate_fence(
        self,
        *,
        store_id: str,
        mode: str,
        owner_id: str,
        lease_token: str,
        lease_generation: int,
        session: Optional[Session] = None,
    ) -> None:
        owns_session = session is None
        db = session or self.session_factory()
        try:
            now = self.clock().replace(tzinfo=None)
            row = db.get(IdentityAssociationRuntimeLease, store_id)
            if not (
                row is not None
                and row.mode == mode
                and row.owner_id == owner_id
                and row.lease_token == lease_token
                and row.lease_generation == lease_generation
                and row.expires_at > now
            ):
                raise RuntimeLeaseUnavailable("runtime_lease_lost")
        finally:
            if owns_session:
                db.close()

    def release(self, lease: RuntimeLease) -> None:
        db = self.session_factory()
        try:
            self._begin(db)
            row = db.get(IdentityAssociationRuntimeLease, lease.store_id)
            if row is None or not self._matches(row, lease, self.clock().replace(tzinfo=None), allow_expired=True):
                raise RuntimeLeaseUnavailable("runtime_lease_lost")
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def _matches(
        row: Optional[IdentityAssociationRuntimeLease],
        lease: RuntimeLease,
        now: datetime,
        *,
        allow_expired: bool = False,
    ) -> bool:
        return bool(
            row is not None
            and row.mode == lease.mode
            and row.owner_id == lease.owner_id
            and row.lease_token == lease.lease_token
            and row.lease_generation == lease.lease_generation
            and (allow_expired or row.expires_at > now)
        )
