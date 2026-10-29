"""Event-time Store and camera resolution from auditable registrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

from agent.models import (
    CameraStoreRegistration,
    DeviceCameraRegistration,
    DeviceStoreRegistration,
    DeviceRecognitionInbox,
    CustomerEntryEvent,
    Visit,
)


@dataclass(frozen=True)
class StoreResolution:
    status: str
    store_id: Optional[str]
    camera_id: Optional[str]
    reason: Optional[str]
    sources: tuple[tuple[str, str], ...]


class StoreResolutionService:
    """Resolve one Store without hiding disagreement between trusted sources."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        deployment_mode: str = "multi_store",
        default_store_id: Optional[str] = None,
    ) -> None:
        if deployment_mode not in {"single_store", "multi_store"}:
            raise ValueError("deployment_mode must be single_store or multi_store")
        self.session_factory = session_factory
        self.deployment_mode = deployment_mode
        self.default_store_id = default_store_id

    def resolve(
        self,
        *,
        device_id: str,
        occurred_at: datetime,
        camera_id: Optional[str] = None,
        persisted_store_id: Optional[str] = None,
        entry_store_id: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> StoreResolution:
        if not isinstance(occurred_at, datetime):
            raise ValueError("occurred_at is required")
        owns_session = session is None
        db = session or self.session_factory()
        try:
            resolved_camera, camera_reason = self._camera_at(
                db, device_id, occurred_at, camera_id
            )
            device_store, device_reason = self._device_store_at(db, device_id, occurred_at)
            camera_store, camera_store_reason = (
                self._camera_store_at(db, resolved_camera, occurred_at)
                if resolved_camera else (None, None)
            )
            source_conflict = next((reason for reason in (device_reason, camera_store_reason) if reason), None)
            if source_conflict:
                return StoreResolution("conflict", None, resolved_camera, source_conflict, ())
            sources: list[tuple[str, str]] = []
            for name, value in (
                ("persisted", persisted_store_id),
                ("entry", entry_store_id),
                ("device", device_store),
                ("camera", camera_store),
            ):
                if isinstance(value, str) and value.strip():
                    sources.append((name, value.strip()))
            if not sources and self.deployment_mode == "single_store" and self.default_store_id:
                sources.append(("single_store_default", self.default_store_id))
            stores = {value for _name, value in sources}
            if len(stores) > 1:
                return StoreResolution(
                    "conflict", None, resolved_camera,
                    "store_mapping_conflict", tuple(sources),
                )
            if not stores:
                return StoreResolution(
                    "missing", None, resolved_camera,
                    camera_reason or "store_mapping_missing", tuple(sources),
                )
            return StoreResolution(
                "resolved", stores.pop(), resolved_camera,
                camera_reason, tuple(sources),
            )
        finally:
            if owns_session:
                db.close()

    def resolve_visit(self, visit_id: str, *, apply: bool = False) -> StoreResolution:
        db = self.session_factory()
        try:
            if apply and db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            visit = db.get(Visit, visit_id)
            if visit is None:
                raise ValueError("visit not found")
            inbox = db.get(DeviceRecognitionInbox, visit.device_event_id) if visit.device_event_id else None
            entry = db.get(CustomerEntryEvent, visit.entry_event_id) if visit.entry_event_id else None
            result = self.resolve(
                device_id=inbox.device_id if inbox else "historical-unknown-device",
                camera_id=visit.camera_id,
                occurred_at=visit.identity_time or visit.entry_time or visit.created_at,
                persisted_store_id=visit.store_id,
                entry_store_id=entry.store_id if entry else None,
                session=db,
            )
            if apply and result.status == "resolved" and visit.store_id is None:
                visit.store_id = result.store_id
                db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _active_rows(query, model, occurred_at: datetime):
        return query.filter(
            model.status.in_(("active", "closed")),
            model.effective_from <= occurred_at,
            (model.effective_to.is_(None) | (occurred_at < model.effective_to)),
        ).order_by(model.effective_from.desc(), model.id).all()

    def _device_store_at(self, db: Session, device_id: str, occurred_at: datetime) -> tuple[Optional[str], Optional[str]]:
        rows = self._active_rows(
            db.query(DeviceStoreRegistration).filter(
                DeviceStoreRegistration.device_id == device_id
            ),
            DeviceStoreRegistration, occurred_at,
        )
        if len(rows) > 1:
            return None, "device_store_mapping_conflict"
        return (rows[0].store_id, None) if rows else (None, None)

    def _camera_store_at(self, db: Session, camera_id: str, occurred_at: datetime) -> tuple[Optional[str], Optional[str]]:
        rows = self._active_rows(
            db.query(CameraStoreRegistration).filter(
                CameraStoreRegistration.camera_id == camera_id
            ),
            CameraStoreRegistration, occurred_at,
        )
        if len(rows) > 1:
            return None, "camera_store_mapping_conflict"
        return (rows[0].store_id, None) if rows else (None, None)

    def _camera_at(
        self,
        db: Session,
        device_id: str,
        occurred_at: datetime,
        supplied_camera_id: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        rows = self._active_rows(
            db.query(DeviceCameraRegistration).filter(
                DeviceCameraRegistration.device_id == device_id
            ),
            DeviceCameraRegistration, occurred_at,
        )
        values = {row.camera_id for row in rows}
        if len(rows) > 1:
            return None, "device_camera_mapping_conflict"
        mapped = next(iter(values)) if values else None
        if supplied_camera_id and mapped and supplied_camera_id != mapped:
            return None, "device_camera_runtime_conflict"
        return supplied_camera_id or mapped, None if mapped or supplied_camera_id else "device_camera_mapping_missing"
