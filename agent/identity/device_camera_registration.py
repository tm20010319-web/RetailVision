"""Transactional, event-time device-to-camera registration management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agent.models import DeviceCameraRegistration


@dataclass(frozen=True)
class DeviceCameraResolution:
    status: str
    camera_id: Optional[str]
    reason: Optional[str]
    registration_ids: tuple[str, ...] = ()


class DeviceCameraRegistrationService:
    def __init__(self, session_factory: Callable[[], Session], *, clock=datetime.utcnow) -> None:
        self.session_factory = session_factory
        self.clock = clock

    def register(
        self,
        *,
        device_id: str,
        camera_id: str,
        effective_from: datetime,
        effective_to: Optional[datetime] = None,
        status: str = "active",
    ) -> DeviceCameraRegistration:
        if status not in {"active", "closed", "void"}:
            raise ValueError("invalid registration status")
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be after effective_from")
        db = self.session_factory()
        try:
            self._begin(db)
            if status != "void" and self._overlaps(
                db, device_id, effective_from, effective_to
            ):
                raise ValueError("device_camera_registration_overlap")
            row = DeviceCameraRegistration(
                device_id=device_id,
                camera_id=camera_id,
                effective_from=effective_from,
                effective_to=effective_to,
                status=status,
            )
            db.add(row)
            db.commit()
            return row
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def switch(self, *, device_id: str, camera_id: str, effective_from: datetime) -> DeviceCameraRegistration:
        db = self.session_factory()
        try:
            self._begin(db)
            current = db.query(DeviceCameraRegistration).filter(
                DeviceCameraRegistration.device_id == device_id,
                DeviceCameraRegistration.status == "active",
                DeviceCameraRegistration.effective_to.is_(None),
            ).one_or_none()
            if current is not None:
                if effective_from <= current.effective_from:
                    raise ValueError("switch time must be after current effective_from")
                current.effective_to = effective_from
                current.status = "closed"
                db.flush()
            if self._overlaps(db, device_id, effective_from, None):
                raise ValueError("device_camera_registration_overlap")
            row = DeviceCameraRegistration(
                device_id=device_id, camera_id=camera_id,
                effective_from=effective_from, status="active",
            )
            db.add(row)
            db.commit()
            return row
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def resolve_at(
        self,
        *,
        device_id: str,
        occurred_at: datetime,
        session: Optional[Session] = None,
    ) -> DeviceCameraResolution:
        """Resolve the unique historical camera mapping at Event Time."""
        if not isinstance(occurred_at, datetime):
            raise ValueError("occurred_at is required")
        owns_session = session is None
        db = session or self.session_factory()
        try:
            rows = self._rows_at(db, device_id, occurred_at)
            if len(rows) > 1:
                return DeviceCameraResolution(
                    "conflict",
                    None,
                    "device_camera_mapping_conflict",
                    tuple(row.id for row in rows),
                )
            if not rows:
                return DeviceCameraResolution(
                    "missing", None, "device_camera_mapping_missing"
                )
            return DeviceCameraResolution(
                "resolved", rows[0].camera_id, None, (rows[0].id,)
            )
        finally:
            if owns_session:
                db.close()

    @staticmethod
    def _rows_at(db: Session, device_id: str, occurred_at: datetime) -> list[DeviceCameraRegistration]:
        return db.query(DeviceCameraRegistration).filter(
            DeviceCameraRegistration.device_id == device_id,
            DeviceCameraRegistration.status.in_(("active", "closed")),
            DeviceCameraRegistration.effective_from <= occurred_at,
            (
                DeviceCameraRegistration.effective_to.is_(None)
                | (occurred_at < DeviceCameraRegistration.effective_to)
            ),
        ).order_by(
            DeviceCameraRegistration.effective_from,
            DeviceCameraRegistration.id,
        ).all()

    @staticmethod
    def _overlaps(
        db: Session,
        device_id: str,
        start: datetime,
        end: Optional[datetime],
    ) -> bool:
        query = db.query(DeviceCameraRegistration.id).filter(
            DeviceCameraRegistration.device_id == device_id,
            DeviceCameraRegistration.status != "void",
            or_(
                DeviceCameraRegistration.effective_to.is_(None),
                DeviceCameraRegistration.effective_to > start,
            ),
        )
        if end is not None:
            query = query.filter(DeviceCameraRegistration.effective_from < end)
        return query.first() is not None

    @staticmethod
    def _begin(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
