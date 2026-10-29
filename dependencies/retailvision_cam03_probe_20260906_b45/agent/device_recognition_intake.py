"""Durable intake for normalized Device Recognition Events.

Transport adapters normalize vendor payloads before crossing this seam.  The
implementation owns Recognition Inbox state, mapping lookup, idempotency and
capture-file ownership; Member Arrival is deliberately downstream.
"""

from __future__ import annotations

import hashlib
import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Literal, Optional

from sqlalchemy.exc import IntegrityError

from agent.models import DeviceMemberMapping, DeviceRecognitionInbox, Member
from agent.face_spatial_evidence import FaceSpatialEvidence


logger = logging.getLogger(__name__)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _background_size(face_spatial_evidence: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    value = face_spatial_evidence.get("source_frame_size") if isinstance(face_spatial_evidence, dict) else None
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in value
    ):
        return value[0], value[1]
    return None, None


class InvalidDeviceRecognitionEvent(ValueError):
    """A normalized event violates the intake interface invariants."""


class IntakeUnavailable(RuntimeError):
    """The durable outcome could not be determined."""


@dataclass(frozen=True)
class DeviceRecognitionEvent:
    device_id: str
    source: Literal["push", "poll"]
    source_event_id: str
    occurred_at: datetime
    unique_id: str = ""
    group_id: Optional[int] = None
    person_id: Optional[int] = None
    similarity: Optional[float] = None
    rule_id: Optional[int] = None
    reco_valid: Optional[bool] = None
    pass_code: Optional[int] = None
    device_occurred_at_raw: Optional[str] = None
    device_utc_offset_minutes: Optional[int] = None
    receiver_arrived_at: Optional[datetime] = None
    face_spatial_evidence: Optional[dict] = None
    device_metadata: Optional[dict] = None
    background_capture: Optional["FaceCapture"] = None


@dataclass(frozen=True)
class FaceCapture:
    content: bytes
    media_type: Literal["image/jpeg"] = "image/jpeg"


@dataclass(frozen=True)
class IntakeReceipt:
    status: Literal["received", "waiting_mapping", "ignored", "duplicate"]
    canonical_event_id: str
    member_id: Optional[str] = None


def source_event_key(source_event_id: str, occurred_at: datetime) -> str:
    """Return a bounded, retry-safe key for a device event."""
    composite = f"{source_event_id}:{occurred_at.isoformat(timespec='seconds')}"
    if len(composite) <= 128:
        return composite
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def canonical_event_id(event: DeviceRecognitionEvent) -> str:
    """Return the source-independent key for one recognized person event."""
    occurred_at = event.occurred_at.replace(microsecond=0).isoformat()
    person_identity = str(event.person_id) if event.person_id is not None else event.unique_id
    material = "|".join(
        (
            event.device_id,
            occurred_at,
            "" if event.group_id is None else str(event.group_id),
            person_identity,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class DeviceRecognitionIntake:
    """Persist normalized events before any Member Arrival processing."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        capture_store: Any = None,
        similarity_threshold: float = 0.75,
        mapping_retention_seconds: float = 86400.0,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.session_factory = session_factory
        self.capture_store = capture_store
        self.similarity_threshold = similarity_threshold
        self.mapping_retention = timedelta(seconds=mapping_retention_seconds)
        self.clock = clock

    def receive(
        self,
        event: DeviceRecognitionEvent,
        *,
        capture: FaceCapture | None = None,
    ) -> IntakeReceipt:
        self._validate(event)
        face_spatial = FaceSpatialEvidence.from_safe_dict(
            event.face_spatial_evidence
        )
        event = replace(
            event,
            face_spatial_evidence=(
                face_spatial.to_safe_dict()
                if face_spatial is not None
                else None
            ),
        )
        event_id = canonical_event_id(event)
        saved_capture = self._save_capture(event, capture)
        saved_background = self._save_background_capture(event)
        if saved_background is not None and (
            (saved_background.width, saved_background.height)
            != _background_size(event.face_spatial_evidence)
        ):
            self._release_capture(saved_background)
            saved_background = None
        retained = False
        retained_background = False
        session = self.session_factory()
        try:
            with self._transaction(session):
                existing = self._source_duplicate(session, event)
                if existing is None:
                    existing = session.get(DeviceRecognitionInbox, event_id)
                if existing is not None:
                    retained = self._adopt_capture(session, existing, event, saved_capture)
                    retained_background = self._adopt_background_capture(
                        session, existing, event, saved_background
                    )
                    receipt = IntakeReceipt(
                        status="duplicate",
                        canonical_event_id=existing.canonical_event_id,
                        member_id=existing.member_id,
                    )
                else:
                    status = "ignored" if self._is_ignored(event) else "received"
                    mapping = None if status == "ignored" else self._resolve_mapping(session, event)
                    if mapping is None and status == "received":
                        status = "waiting_mapping"
                    retained = bool(saved_capture) and status != "ignored"
                    retained_background = bool(saved_background) and status != "ignored"
                    session.add(
                        DeviceRecognitionInbox(
                            canonical_event_id=event_id,
                            device_id=event.device_id,
                            source=event.source,
                            source_event_id=event.source_event_id,
                            occurred_at=event.occurred_at,
                            member_id=mapping.member_id if mapping is not None else None,
                            group_id=event.group_id,
                            person_id=event.person_id,
                            unique_id=event.unique_id,
                            similarity=event.similarity,
                            rule_id=event.rule_id,
                            capture_path=saved_capture.path if retained else None,
                            background_capture_path=(
                                saved_background.path if retained_background else None
                            ),
                            background_capture_sha256=(
                                saved_background.sha256
                                if retained_background else None
                            ),
                            background_capture_width=(
                                _background_size(event.face_spatial_evidence)[0]
                                if retained_background else None
                            ),
                            background_capture_height=(
                                _background_size(event.face_spatial_evidence)[1]
                                if retained_background else None
                            ),
                            device_occurred_at_raw=event.device_occurred_at_raw,
                            device_utc_offset_minutes=event.device_utc_offset_minutes,
                            receiver_arrived_at=event.receiver_arrived_at,
                            face_spatial_evidence=event.face_spatial_evidence,
                            device_metadata=event.device_metadata,
                            status=status,
                        )
                    )
                    receipt = IntakeReceipt(
                        status=status,
                        canonical_event_id=event_id,
                        member_id=mapping.member_id if mapping is not None else None,
                    )
            return receipt
        except IntegrityError:
            session.rollback()
            try:
                existing = self._source_duplicate(session, event)
                if existing is None:
                    existing = session.get(DeviceRecognitionInbox, event_id)
                if existing is None:
                    raise IntakeUnavailable("recognition intake commit conflict")
                retained = self._adopt_capture(session, existing, event, saved_capture)
                retained_background = self._adopt_background_capture(
                    session, existing, event, saved_background
                )
                session.commit()
                return IntakeReceipt(
                    status="duplicate",
                    canonical_event_id=existing.canonical_event_id,
                    member_id=existing.member_id,
                )
            except IntakeUnavailable:
                raise
            except Exception as error:
                session.rollback()
                raise IntakeUnavailable("recognition intake outcome unavailable") from error
        except IntakeUnavailable:
            raise
        except Exception as error:
            session.rollback()
            raise IntakeUnavailable("recognition intake outcome unavailable") from error
        finally:
            session.close()
            if saved_capture is not None and not retained:
                self._release_capture(saved_capture)
            if saved_background is not None and not retained_background:
                self._release_capture(saved_background)

    def _reconcile_waiting(self, now: datetime | None = None, *, limit: int = 100) -> tuple[int, int]:
        """Recover mapping state; called by the Recognition Inbox worker."""
        observed_at = now or self.clock()
        cutoff = observed_at - self.mapping_retention
        session = self.session_factory()
        released: list[str] = []
        recovered = expired = 0
        try:
            with self._transaction(session):
                query = session.query(DeviceRecognitionInbox).filter(
                    DeviceRecognitionInbox.status == "waiting_mapping"
                ).order_by(
                    DeviceRecognitionInbox.received_at,
                    DeviceRecognitionInbox.canonical_event_id,
                )
                if session.get_bind().dialect.name != "sqlite":
                    query = query.with_for_update(skip_locked=True)
                for row in query.limit(limit).all():
                    event = DeviceRecognitionEvent(
                        device_id=row.device_id,
                        source=row.source,
                        source_event_id=row.source_event_id,
                        occurred_at=row.occurred_at,
                        group_id=row.group_id,
                        person_id=row.person_id,
                        unique_id=row.unique_id or "",
                        similarity=row.similarity,
                        rule_id=row.rule_id,
                        device_occurred_at_raw=row.device_occurred_at_raw,
                        device_utc_offset_minutes=row.device_utc_offset_minutes,
                        receiver_arrived_at=row.receiver_arrived_at,
                        face_spatial_evidence=row.face_spatial_evidence,
                        device_metadata=getattr(row, "device_metadata", None),
                        background_capture=None,
                    )
                    mapping = self._resolve_mapping(session, event)
                    if mapping is not None:
                        row.member_id = mapping.member_id
                        row.status = "received"
                        row.last_error = None
                        recovered += 1
                    elif row.received_at <= cutoff:
                        if row.capture_path:
                            released.append(row.capture_path)
                            row.capture_path = None
                        if row.background_capture_path:
                            released.append(row.background_capture_path)
                            row.background_capture_path = None
                        row.status = "unmapped"
                        row.processed_at = observed_at
                        row.last_error = "mapping_timeout"
                        expired += 1
            for path in released:
                self._release_capture_path(path)
            return recovered, expired
        finally:
            session.close()

    def _save_capture(self, event: DeviceRecognitionEvent, capture: FaceCapture | None):
        if capture is None or self.capture_store is None:
            return None
        try:
            return self.capture_store.save_bytes(
                event.device_id,
                event.source_event_id,
                capture.content,
            )
        except Exception as error:
            logger.warning("Recognition capture save failed: error_type=%s", type(error).__name__)
            return None

    def _save_background_capture(self, event: DeviceRecognitionEvent):
        capture = event.background_capture
        if capture is None or self.capture_store is None:
            return None
        try:
            return self.capture_store.save_bytes(
                event.device_id,
                f"{event.source_event_id}:background",
                capture.content,
            )
        except Exception as error:
            logger.warning(
                "Recognition background capture save failed: error_type=%s",
                type(error).__name__,
            )
            return None

    def _release_capture(self, result: Any) -> None:
        try:
            self.capture_store.remove_if_created(result)
        except Exception as error:
            logger.warning("Recognition capture release failed: error_type=%s", type(error).__name__)

    def _release_capture_path(self, path: str) -> None:
        try:
            self.capture_store.remove_path(path)
        except Exception as error:
            logger.warning("Recognition capture release failed: error_type=%s", type(error).__name__)

    @staticmethod
    def _validate(event: DeviceRecognitionEvent) -> None:
        if (
            not isinstance(event, DeviceRecognitionEvent)
            or not event.device_id
            or len(event.device_id) > 64
            or event.source not in {"push", "poll"}
            or not event.source_event_id
            or len(event.source_event_id) > 128
            or not isinstance(event.occurred_at, datetime)
            or len(event.unique_id) > 64
            or (not event.unique_id and (event.group_id is None or event.person_id is None))
        ):
            raise InvalidDeviceRecognitionEvent("invalid normalized recognition event")
        if (
            event.receiver_arrived_at is not None
            and (
                not isinstance(event.receiver_arrived_at, datetime)
                or event.receiver_arrived_at.tzinfo is not None
            )
        ):
            raise InvalidDeviceRecognitionEvent("receiver_arrived_at_invalid")
        if (
            event.similarity is not None
            and (
                isinstance(event.similarity, bool)
                or not isinstance(event.similarity, (int, float))
                or not math.isfinite(event.similarity)
                or not 0 <= event.similarity <= 1
            )
        ):
            raise InvalidDeviceRecognitionEvent("similarity must be between 0 and 1")

    def _is_ignored(self, event: DeviceRecognitionEvent) -> bool:
        return (
            event.reco_valid is False
            or event.pass_code not in (None, 0)
            or event.rule_id not in (None, 1)
            or event.similarity is not None and event.similarity < self.similarity_threshold
        )

    @staticmethod
    def _resolve_mapping(session: Any, event: DeviceRecognitionEvent) -> Any:
        query = session.query(DeviceMemberMapping).join(
            Member, Member.id == DeviceMemberMapping.member_id
        ).filter(
            DeviceMemberMapping.device_id == event.device_id,
            DeviceMemberMapping.sync_status == "synced",
            DeviceMemberMapping.is_enabled.is_(True),
            Member.is_active.is_(True),
        )
        if event.unique_id:
            mapping = query.filter(DeviceMemberMapping.unique_id == event.unique_id).first()
            if mapping is not None:
                return mapping
        if event.group_id is None or event.person_id is None:
            return None
        return query.filter(
            DeviceMemberMapping.group_id == event.group_id,
            DeviceMemberMapping.person_id == event.person_id,
        ).first()

    @staticmethod
    def _source_duplicate(session: Any, event: DeviceRecognitionEvent) -> Any:
        return session.query(DeviceRecognitionInbox).filter(
            DeviceRecognitionInbox.device_id == event.device_id,
            DeviceRecognitionInbox.source == event.source,
            DeviceRecognitionInbox.source_event_id == event.source_event_id,
        ).first()

    @staticmethod
    def _adopt_capture(session: Any, existing: DeviceRecognitionInbox, event: DeviceRecognitionEvent, saved_capture: Any) -> bool:
        if event.source == "push" and existing.face_spatial_evidence is None:
            existing.face_spatial_evidence = event.face_spatial_evidence
            existing.device_occurred_at_raw = (
                existing.device_occurred_at_raw or event.device_occurred_at_raw
            )
            existing.device_utc_offset_minutes = (
                existing.device_utc_offset_minutes
                if existing.device_utc_offset_minutes is not None
                else event.device_utc_offset_minutes
            )
            existing.receiver_arrived_at = (
                existing.receiver_arrived_at or event.receiver_arrived_at
            )
            if getattr(existing, "device_metadata", None) is None:
                existing.device_metadata = event.device_metadata
        if (
            event.source != "push"
            or existing.source != "poll"
            or saved_capture is None
            or existing.capture_path
        ):
            return False
        existing.capture_path = saved_capture.path
        return True

    @staticmethod
    def _adopt_background_capture(
        session: Any,
        existing: DeviceRecognitionInbox,
        event: DeviceRecognitionEvent,
        saved_background: Any,
    ) -> bool:
        if (
            event.source != "push"
            or saved_background is None
            or existing.background_capture_path
        ):
            return False
        existing.background_capture_path = saved_background.path
        existing.background_capture_sha256 = saved_background.sha256
        size = _background_size(event.face_spatial_evidence)
        existing.background_capture_width, existing.background_capture_height = size
        return True

    @contextmanager
    def _transaction(self, session: Any):
        if session.get_bind().dialect.name != "sqlite":
            with session.begin():
                yield
            return
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            session.rollback()
            raise
        else:
            session.commit()
