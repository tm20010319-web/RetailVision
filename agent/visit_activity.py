"""Event-time refresh of reusable Canonical Visit activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable, Iterable, Optional

from sqlalchemy.orm import Session

from agent.models import Visit
from agent.reusable_visit_resolver import ReusableVisitValidator
from agent.visit_merge_service import resolve_canonical_visit_id


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisitActivity:
    """One confirmed, exact Track observation accepted from the runtime Cache."""

    store_id: str
    camera_id: str
    runtime_instance_id: str
    track_id: int
    track_token: str
    binding_id: str
    member_id: str
    canonical_visit_id: str
    observed_at: datetime

    def __post_init__(self) -> None:
        expected_token = (
            f"{self.store_id}:{self.camera_id}:"
            f"{self.runtime_instance_id}:{self.track_id}"
        )
        if (
            not self.store_id
            or not self.camera_id
            or not self.runtime_instance_id
            or not self.binding_id
            or not self.member_id
            or not self.canonical_visit_id
            or self.track_token != expected_token
        ):
            raise ValueError("VisitActivity exact provenance is required")


class VisitActivityService:
    """Refresh only ``Visit.last_seen_at`` from trusted realtime activity."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        future_clock_skew_tolerance_seconds: float = 30.0,
        validator: Optional[ReusableVisitValidator] = None,
    ) -> None:
        if future_clock_skew_tolerance_seconds < 0:
            raise ValueError("future_clock_skew_tolerance_seconds must be non-negative")
        self.session_factory = session_factory
        self.future_tolerance = timedelta(
            seconds=future_clock_skew_tolerance_seconds
        )
        self.validator = validator or ReusableVisitValidator()

    def refresh_batch(
        self,
        activities: Iterable[VisitActivity],
        *,
        now: Optional[datetime] = None,
    ) -> dict[str, int]:
        """Apply an already-coalesced set of confirmed Track observations."""
        activities = tuple(sorted(
            activities,
            key=lambda activity: (
                activity.observed_at,
                activity.store_id,
                activity.camera_id,
                activity.runtime_instance_id,
                activity.track_token,
                activity.binding_id,
            ),
        ))
        if not activities:
            return {}
        result: dict[str, int] = {}
        transaction_now = (now or datetime.utcnow()).replace(tzinfo=None)
        db = self.session_factory()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            for activity in activities:
                outcome = self._refresh_in_session(db, activity, transaction_now)
                result[outcome] = result.get(outcome, 0) + 1
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _refresh_in_session(
        self,
        db: Session,
        activity: VisitActivity,
        transaction_now: datetime,
    ) -> str:
        return touch_confirmed_visit_activity_in_transaction(
            db,
            visit_id=activity.canonical_visit_id,
            member_id=activity.member_id,
            store_id=activity.store_id,
            observed_at=activity.observed_at,
            transaction_now=transaction_now,
            validator=self.validator,
            future_tolerance=self.future_tolerance,
        )


def touch_confirmed_visit_activity_in_transaction(
    db: Session,
    *,
    visit_id: str,
    member_id: str,
    store_id: str,
    observed_at: datetime,
    transaction_now: datetime,
    validator: Optional[ReusableVisitValidator] = None,
    future_tolerance: timedelta = timedelta(seconds=30),
) -> str:
    """Apply the Step 4 confirmed-activity contract in an existing transaction."""
    observed_at = observed_at.replace(tzinfo=None)
    if observed_at - transaction_now.replace(tzinfo=None) > future_tolerance:
        return "skipped_future_anomaly"
    validator = validator or ReusableVisitValidator()
    try:
        canonical_id = resolve_canonical_visit_id(db, visit_id)
    except ValueError:
        return "skipped_canonical_unresolved"
    visit = db.get(Visit, canonical_id)
    if visit is None:
        return "skipped_canonical_unresolved"
    if visit.member_id != member_id or visit.store_id != store_id:
        return "skipped_provenance_mismatch"
    if visit.exit_time is not None:
        return "skipped_closed_visit"
    if visit.superseded_by_visit_id is not None:
        return "skipped_canonical_unresolved"
    visit = validator.resolve(
        db,
        visit_id=canonical_id,
        member_id=member_id,
        store_id=store_id,
        now=observed_at,
    )
    if visit is None:
        persisted = db.get(Visit, canonical_id)
        if persisted is None:
            return "skipped_canonical_unresolved"
        entry_time = persisted.entry_time or persisted.created_at
        activity_time = (
            persisted.last_seen_at
            or persisted.identified_at
            or persisted.identity_time
            or entry_time
        )
        if (
            entry_time is not None
            and observed_at - entry_time > validator.max_visit_duration
        ):
            return "skipped_non_reusable_max_duration"
        if (
            activity_time is not None
            and observed_at - activity_time > validator.inactivity_timeout
        ):
            return "skipped_non_reusable_inactivity"
        return "skipped_non_reusable"
    if visit.last_seen_at is not None and observed_at <= visit.last_seen_at:
        return "no_change"
    visit.last_seen_at = observed_at
    return "updated"


class VisitActivityRefresher:
    """Coalesce exact confirmed Track activity outside the video-frame DB path."""

    def __init__(
        self,
        service: VisitActivityService,
        *,
        track_identity_cache: Any,
        refresh_interval_seconds: float = 5.0,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        self.service = service
        self.track_identity_cache = track_identity_cache
        self.refresh_interval_seconds = refresh_interval_seconds
        self.clock = clock
        self._pending: dict[tuple[str, str], VisitActivity] = {}
        self._lock = RLock()
        self._stop = Event()
        self._terminated = Event()
        self._thread: Optional[Thread] = None

    def observe_track(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        track_id: int,
        track_token: str,
        observed_at: datetime,
    ) -> bool:
        if self._terminated.is_set():
            return False
        identity = self.track_identity_cache.resolve_exact(
            store_id=store_id,
            camera_id=camera_id,
            runtime_instance_id=runtime_instance_id,
            track_id=track_id,
            track_token=track_token,
        )
        if identity is None or identity.get("identity_status") != "confirmed":
            return False
        try:
            activity = VisitActivity(
                store_id=store_id,
                camera_id=camera_id,
                runtime_instance_id=runtime_instance_id,
                track_id=track_id,
                track_token=track_token,
                binding_id=identity["binding_id"],
                member_id=identity["member_id"],
                canonical_visit_id=identity["visit_id"],
                observed_at=observed_at.replace(tzinfo=None),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Visit activity Cache projection rejected: store_id=%s "
                "camera_id=%s runtime_instance_id=%s track_token=%s",
                store_id,
                camera_id,
                runtime_instance_id,
                track_token,
            )
            return False
        key = (activity.canonical_visit_id, activity.track_token)
        with self._lock:
            if self._terminated.is_set():
                return False
            existing = self._pending.get(key)
            if existing is None or activity.observed_at > existing.observed_at:
                self._pending[key] = activity
        return True

    def run_once(self) -> int:
        with self._lock:
            if self._terminated.is_set():
                self._pending.clear()
                return 0
            batch = tuple(self._pending.values())
            self._pending.clear()
        if not batch:
            return 0
        try:
            outcomes = self.service.refresh_batch(batch, now=self._now())
            if any(key != "updated" for key in outcomes):
                logger.info(
                    "Visit activity batch outcomes: %s",
                    ",".join(
                        f"{key}={value}" for key, value in sorted(outcomes.items())
                    ),
                )
        except Exception:
            self._restore(batch)
            raise
        return len(batch)

    def start(self) -> None:
        if self._terminated.is_set():
            raise RuntimeError("Visit Activity Refresher is terminated")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run_forever,
            name="visit-activity-refresher",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        self._terminated.set()
        self._stop.set()
        with self._lock:
            self._pending.clear()

    def stop(self) -> None:
        self.request_stop()
        thread = self._thread
        if thread is None or thread is current_thread():
            return
        thread.join(timeout=max(2.0, self.refresh_interval_seconds * 2))
        if thread.is_alive():
            raise RuntimeError("Visit Activity Refresher did not stop")
        self._thread = None

    def _run_forever(self) -> None:
        while not self._stop.wait(self.refresh_interval_seconds):
            try:
                self.run_once()
            except Exception as error:
                logger.error(
                    "Visit activity refresh failed: error_type=%s",
                    type(error).__name__,
                )

    def _restore(self, activities: tuple[VisitActivity, ...]) -> None:
        with self._lock:
            if self._terminated.is_set():
                return
            for activity in activities:
                key = (activity.canonical_visit_id, activity.track_token)
                existing = self._pending.get(key)
                if existing is None or activity.observed_at > existing.observed_at:
                    self._pending[key] = activity

    def _now(self) -> datetime:
        return self.clock().replace(tzinfo=None)
