"""Device recognition event contracts, persistence adapters and polling."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable, Dict, List, Optional, Protocol

from agent.platform.camera_face_api import (
    CameraApiAuthenticationError,
    CameraApiError,
    CameraFaceApiClient,
)
from agent.identity.device_recognition_intake import (
    DeviceRecognitionEvent as IntakeEvent,
    DeviceRecognitionIntake,
    IntakeReceipt,
    source_event_key,
)

logger = logging.getLogger(__name__)

_MIN_FAILURE_BACKOFF_SECONDS = 0.01
_MAX_FAILURE_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True)
class StableTrackEvent:
    camera_id: str
    track_id: int
    bbox: List[int]
    detection_confidence: float
    confirmed_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "bbox": list(self.bbox),
            "detection_confidence": self.detection_confidence,
            "confirmed_at": self.confirmed_at.isoformat(timespec="milliseconds"),
        }


@dataclass
class CustomerSession:
    """One member entry, optionally correlated to a YOLO track."""

    session_id: str
    track_id: Optional[int]
    member_id: str
    member_type: str = "member"
    camera_id: str = ""
    zone_name: str = ""
    confidence: float = 0.0
    entry_time: float = 0.0
    detection_bbox: Optional[List[int]] = None
    match_result: Dict[str, Any] = field(default_factory=dict)
    identity_source: str = "device_api"
    device_event_id: Optional[str] = None
    identity_time: Optional[float] = None
    snapshot_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeviceRecognitionEvent:
    device_id: str
    event_id: str
    member_id: str
    occurred_at: datetime
    similarity: float
    source: str = "poll"
    source_event_id: str = ""
    capture_path: Optional[str] = None
    log_id: Optional[int] = None
    group_id: Optional[int] = None
    person_id: Optional[int] = None
    unique_id: str = ""
    rule_id: Optional[int] = None

    def sanitized_match_result(self) -> Dict[str, Any]:
        return {
            "similarity": self.similarity,
            "source": self.source,
            "source_event_id": self.source_event_id,
            "capture_path": self.capture_path,
            "group_id": self.group_id,
            "person_id": self.person_id,
            "unique_id": self.unique_id,
            "rule_id": self.rule_id,
        }


@dataclass
class DeviceMemberMappingData:
    device_id: str
    member_id: str
    group_id: Optional[int]
    group_uuid: str
    person_id: Optional[int]
    unique_id: str
    face_uuid: str
    sync_status: str = "pending"
    is_enabled: bool = True
    last_sync_at: Optional[datetime] = None
    last_error: Optional[str] = None
    member_active: bool = True


@dataclass
class DeviceRecognitionCursorData:
    device_id: str
    last_log_id: Optional[int] = None
    last_record_time: Optional[datetime] = None
    last_success_at: Optional[datetime] = None


class DeviceIdentityRepository(Protocol):
    def get_mapping(
        self, device_id: str, member_id: str
    ) -> Optional[DeviceMemberMappingData]: ...

    def save_mapping(self, mapping: DeviceMemberMappingData) -> None: ...

    def get_cursor(self, device_id: str) -> Optional[DeviceRecognitionCursorData]: ...

    def save_cursor(self, cursor: DeviceRecognitionCursorData) -> None: ...

    def resolve_mapping(
        self,
        device_id: str,
        group_id: Optional[int],
        person_id: Optional[int],
        unique_id: str,
    ) -> Optional[DeviceMemberMappingData]: ...

class InMemoryDeviceIdentityRepository:
    """Deterministic repository for tests and short-lived local demos."""

    def __init__(self) -> None:
        self._mappings: List[DeviceMemberMappingData] = []
        self._cursors: Dict[str, DeviceRecognitionCursorData] = {}

    def save_mapping(self, mapping: DeviceMemberMappingData) -> None:
        self._mappings = [
            existing
            for existing in self._mappings
            if not (
                existing.device_id == mapping.device_id
                and existing.member_id == mapping.member_id
            )
        ]
        self._mappings.append(mapping)

    def get_mapping(
        self, device_id: str, member_id: str
    ) -> Optional[DeviceMemberMappingData]:
        return next(
            (
                mapping
                for mapping in self._mappings
                if mapping.device_id == device_id and mapping.member_id == member_id
            ),
            None,
        )

    def get_cursor(self, device_id: str) -> Optional[DeviceRecognitionCursorData]:
        return self._cursors.get(device_id)

    def save_cursor(self, cursor: DeviceRecognitionCursorData) -> None:
        self._cursors[cursor.device_id] = cursor

    def resolve_mapping(
        self,
        device_id: str,
        group_id: Optional[int],
        person_id: Optional[int],
        unique_id: str,
    ) -> Optional[DeviceMemberMappingData]:
        for mapping in self._mappings:
            if mapping.device_id != device_id:
                continue
            if mapping.sync_status != "synced" or not mapping.member_active:
                continue
            if unique_id and mapping.unique_id == unique_id:
                return mapping
        if group_id is None or person_id is None:
            return None
        for mapping in self._mappings:
            if (
                mapping.device_id == device_id
                and mapping.sync_status == "synced"
                and mapping.member_active
                and mapping.group_id == group_id
                and mapping.person_id == person_id
            ):
                return mapping
        return None

class SqlAlchemyDeviceIdentityRepository:
    """SQLite-compatible repository that opens one Session per operation."""

    def __init__(
        self, session_factory: Callable[[], Any], *,
        historical_revision_repository: Any = None,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    ):
        self.session_factory = session_factory
        self.historical_revision_repository = historical_revision_repository
        self.utc_clock = utc_clock

    def get_mapping(
        self, device_id: str, member_id: str
    ) -> Optional[DeviceMemberMappingData]:
        from agent.models import DeviceMemberMapping, Member

        session = self.session_factory()
        try:
            row = (
                session.query(DeviceMemberMapping, Member)
                .join(Member, Member.id == DeviceMemberMapping.member_id)
                .filter(
                    DeviceMemberMapping.device_id == device_id,
                    DeviceMemberMapping.member_id == member_id,
                )
                .first()
            )
            if row is None:
                return None
            mapping, member = row
            return DeviceMemberMappingData(
                device_id=mapping.device_id,
                member_id=mapping.member_id,
                group_id=mapping.group_id,
                group_uuid=mapping.group_uuid,
                person_id=mapping.person_id,
                unique_id=mapping.unique_id,
                face_uuid=mapping.face_uuid,
                sync_status=mapping.sync_status,
                is_enabled=bool(mapping.is_enabled),
                last_sync_at=mapping.last_sync_at,
                last_error=mapping.last_error,
                member_active=bool(member.is_active),
            )
        finally:
            session.close()

    def save_mapping(self, mapping: DeviceMemberMappingData) -> None:
        # A new, pending registration has no device identity yet. Once one is
        # known, the existing revision repository owns BOTH writes atomically.
        # last_sync_at is a mutable projection, never a historical start time.
        if self.historical_revision_repository is not None and (
            mapping.unique_id or (mapping.group_id is not None and mapping.person_id is not None)
        ):
            self.historical_revision_repository.apply_mapping_revision(
                mapping, effective_from=self.utc_clock(),
            )
            return
        from agent.models import DeviceMemberMapping

        session = self.session_factory()
        try:
            mapping_id = f"{mapping.device_id}:{mapping.member_id}"
            row = session.query(DeviceMemberMapping).filter_by(id=mapping_id).first()
            if row is None:
                row = DeviceMemberMapping(id=mapping_id)
                session.add(row)
            row.device_id = mapping.device_id
            row.member_id = mapping.member_id
            row.group_id = mapping.group_id
            row.group_uuid = mapping.group_uuid
            row.person_id = mapping.person_id
            row.unique_id = mapping.unique_id
            row.face_uuid = mapping.face_uuid
            row.sync_status = mapping.sync_status
            row.is_enabled = bool(mapping.is_enabled)
            row.last_sync_at = mapping.last_sync_at
            row.last_error = mapping.last_error
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_cursor(self, device_id: str) -> Optional[DeviceRecognitionCursorData]:
        from agent.models import DeviceRecognitionCursor

        session = self.session_factory()
        try:
            row = session.query(DeviceRecognitionCursor).filter_by(device_id=device_id).first()
            if row is None:
                return None
            return DeviceRecognitionCursorData(
                device_id=row.device_id,
                last_log_id=row.last_log_id,
                last_record_time=row.last_record_time,
                last_success_at=row.last_success_at,
            )
        finally:
            session.close()

    def save_cursor(self, cursor: DeviceRecognitionCursorData) -> None:
        from agent.models import DeviceRecognitionCursor

        session = self.session_factory()
        try:
            row = session.query(DeviceRecognitionCursor).filter_by(
                device_id=cursor.device_id
            ).first()
            if row is None:
                row = DeviceRecognitionCursor(device_id=cursor.device_id)
                session.add(row)
            row.last_log_id = cursor.last_log_id
            row.last_record_time = cursor.last_record_time
            row.last_success_at = cursor.last_success_at
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def resolve_mapping(
        self,
        device_id: str,
        group_id: Optional[int],
        person_id: Optional[int],
        unique_id: str,
    ) -> Optional[DeviceMemberMappingData]:
        from agent.models import DeviceMemberMapping, Member

        session = self.session_factory()
        try:
            query = (
                session.query(DeviceMemberMapping, Member)
                .join(Member, Member.id == DeviceMemberMapping.member_id)
                .filter(
                    DeviceMemberMapping.device_id == device_id,
                    DeviceMemberMapping.sync_status == "synced",
                    DeviceMemberMapping.is_enabled.is_(True),
                    Member.is_active.is_(True),
                )
            )
            row = None
            if unique_id:
                row = query.filter(DeviceMemberMapping.unique_id == unique_id).first()
            if row is None and group_id is not None and person_id is not None:
                row = query.filter(
                    DeviceMemberMapping.group_id == group_id,
                    DeviceMemberMapping.person_id == person_id,
                ).first()
            if row is None:
                return None
            mapping, member = row
            return DeviceMemberMappingData(
                device_id=mapping.device_id,
                member_id=mapping.member_id,
                group_id=mapping.group_id,
                group_uuid=mapping.group_uuid,
                person_id=mapping.person_id,
                unique_id=mapping.unique_id,
                face_uuid=mapping.face_uuid,
                sync_status=mapping.sync_status,
                is_enabled=bool(mapping.is_enabled),
                last_sync_at=mapping.last_sync_at,
                last_error=mapping.last_error,
                member_active=bool(member.is_active),
            )
        finally:
            session.close()

class RecognitionRecordPoller:
    """Poll one camera and compensate for missed push recognition events."""

    def __init__(
        self,
        *,
        client: CameraFaceApiClient,
        repository: DeviceIdentityRepository,
        intake: DeviceRecognitionIntake,
        on_health_change: Optional[Callable[[str, bool, str], None]] = None,
        on_warning: Optional[Callable[[str, str], None]] = None,
        poll_interval_seconds: float = 30.0,
        overlap_seconds: float = 5.0,
        page_size: int = 100,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.client = client
        self.repository = repository
        if intake is None:
            raise ValueError("intake is required")
        self.intake = intake
        self.on_health_change = on_health_change
        self.on_warning = on_warning
        self.poll_interval_seconds = poll_interval_seconds
        self.overlap_seconds = overlap_seconds
        self.page_size = page_size
        self.clock = clock
        self.healthy = True
        self.authentication_suspended = False
        self._consecutive_failures = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()

    @property
    def device_id(self) -> str:
        return self.client.device_id

    def _set_health(self, healthy: bool, reason: str) -> None:
        changed = healthy != self.healthy
        self.healthy = healthy
        if (changed or reason == "healthy") and self.on_health_change:
            try:
                self.on_health_change(self.device_id, healthy, reason)
            except Exception as error:
                logger.error(
                    "Device health callback failed for device %s: %s",
                    self.device_id,
                    type(error).__name__,
                )

    def _notify_warning(self, reason: str) -> None:
        if not self.on_warning:
            return
        try:
            self.on_warning(self.device_id, reason)
        except Exception as error:
            logger.error(
                "Device warning callback failed for device %s: %s",
                self.device_id,
                type(error).__name__,
            )

    def _record_failure(self, error: Exception) -> None:
        if isinstance(error, CameraApiAuthenticationError):
            logger.warning(
                "Device recognition polling failed: device_id=%s error_type=%s "
                "message=%s retryable=%s",
                self.device_id,
                type(error).__name__,
                str(error),
                error.retryable,
            )
            self.authentication_suspended = True
            self._consecutive_failures = 3
            self._set_health(False, "authentication_failed")
            return
        self._consecutive_failures += 1
        if self._consecutive_failures in (1, 3):
            if isinstance(error, CameraApiError):
                logger.warning(
                    "Device recognition polling failed: device_id=%s error_type=%s "
                    "message=%s retryable=%s failure_count=%s",
                    self.device_id,
                    type(error).__name__,
                    str(error),
                    error.retryable,
                    self._consecutive_failures,
                )
            else:
                logger.warning(
                    "Device recognition polling failed: device_id=%s error_type=%s "
                    "failure_count=%s",
                    self.device_id,
                    type(error).__name__,
                    self._consecutive_failures,
                )
        if self._consecutive_failures >= 3:
            self._set_health(False, "request_failed")

    def _ingest_records(self, records: List[Dict[str, Any]]) -> List[IntakeReceipt]:
        results = []
        for record in records:
            event = IntakeEvent(
                device_id=self.device_id,
                source="poll",
                source_event_id=source_event_key(
                    str(record["log_id"]), record["occurred_at"]
                ),
                occurred_at=record["occurred_at"],
                group_id=record.get("group_id"),
                person_id=record.get("person_id"),
                rule_id=record.get("rule_id"),
                unique_id=record.get("unique_id", ""),
                similarity=record.get("similarity"),
                reco_valid=record.get("reco_valid"),
                pass_code=record.get("pass_code"),
            )
            results.append(self.intake.receive(event))
        return results

    def poll_once(self) -> List[IntakeReceipt]:
        if self.authentication_suspended:
            return []
        now = self.clock()
        cursor = self.repository.get_cursor(self.device_id)
        anchor = cursor.last_record_time if cursor and cursor.last_record_time else now
        start_time = anchor - timedelta(seconds=self.overlap_seconds)
        all_records: List[Dict[str, Any]] = []
        page_num = 1
        try:
            while True:
                page = self.client.query_recognition_records(
                    start_time,
                    now,
                    page_size=self.page_size,
                    page_num=page_num,
                )
                all_records.extend(page)
                if len(page) < self.page_size:
                    break
                page_num += 1
        except CameraApiError as exc:
            self._record_failure(exc)
            return []

        try:
            ingest_results = self._ingest_records(all_records)
        except Exception as error:
            self._record_failure(error)
            return []

        if all_records:
            newest = max(record["occurred_at"] for record in all_records)
            if newest > now + timedelta(seconds=5):
                self._notify_warning("device_clock_ahead")

        latest_record = (
            max(all_records, key=lambda item: (item["occurred_at"], item["log_id"]))
            if all_records
            else None
        )
        self.repository.save_cursor(
            DeviceRecognitionCursorData(
                device_id=self.device_id,
                last_log_id=(
                    latest_record["log_id"]
                    if latest_record is not None
                    else (cursor.last_log_id if cursor else None)
                ),
                last_record_time=now,
                last_success_at=now,
            )
        )
        was_unhealthy = not self.healthy
        self._consecutive_failures = 0
        self._set_health(True, "recovered" if was_unhealthy else "healthy")
        return ingest_results

    def clear_authentication_suspension(self) -> None:
        self.authentication_suspended = False
        self._consecutive_failures = 0

    def _next_poll_delay(self, started: float) -> float:
        interval = max(0.0, self.poll_interval_seconds)
        if self._consecutive_failures:
            failure_interval = min(
                max(interval, _MIN_FAILURE_BACKOFF_SECONDS)
                * (2 ** min(self._consecutive_failures - 1, 10)),
                _MAX_FAILURE_BACKOFF_SECONDS,
            )
            interval = max(interval, failure_interval)
        return max(0.0, interval - (monotonic() - started))

    def run_forever(self) -> None:
        while not self._stop.is_set():
            started = monotonic()
            try:
                self.poll_once()
            except Exception as error:
                logger.error(
                    "Recognition polling failed for device %s: %s",
                    self.device_id,
                    type(error).__name__,
                )
                self._record_failure(error)
            self._stop.wait(self._next_poll_delay(started))

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    raise RuntimeError("Recognition poller is still stopping")
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self.run_forever,
                name=f"recognition-poller-{self.device_id}",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=timeout)
                if self._thread.is_alive():
                    raise RuntimeError(
                        "Recognition poller thread is still running"
                    )
