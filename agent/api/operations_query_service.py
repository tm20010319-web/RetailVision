"""Read-only operational views over durable recognition state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import func

from agent.member.member_priority import alert_level, normalize_tier, tier_rank
from agent.models import (
    BusinessJob,
    DeviceHealthState,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OperationsQueryService:
    def __init__(
        self,
        session_factory,
        *,
        device_id: str | None,
        health_freshness_seconds: float,
        profile_mode: str,
        profile_lock_timeout_seconds: float = 300.0,
        clock=datetime.now,
        health_clock: Callable[[], datetime] = _utc_naive_now,
    ) -> None:
        self.session_factory = session_factory
        self.device_id = device_id
        self.health_freshness = timedelta(seconds=health_freshness_seconds)
        self.profile_mode = profile_mode
        self.profile_lock_timeout = timedelta(seconds=profile_lock_timeout_seconds)
        self.clock = clock
        self.health_clock = health_clock

    def status(self, now: datetime | None = None) -> dict:
        observed_now = now or self.clock()
        health_now = now or self.health_clock()
        db = self.session_factory()
        try:
            health = db.get(DeviceHealthState, self.device_id) if self.device_id else None
            inbox_counts = dict(
                db.query(DeviceRecognitionInbox.status, func.count())
                .group_by(DeviceRecognitionInbox.status)
                .all()
            )
            pending_statuses = ("received", "processing", "waiting_mapping")
            oldest_pending = (
                db.query(func.min(DeviceRecognitionInbox.received_at))
                .filter(DeviceRecognitionInbox.status.in_(pending_statuses))
                .scalar()
            )
            profile = self._profile_status(db, observed_now)
            push = self._health_channel(
                health.push_healthy if health else False,
                health.last_heartbeat_at if health else None,
                health.last_push_error if health else None,
                health_now,
                "push_failed",
            )
            poll = self._health_channel(
                health.poll_healthy if health else False,
                health.last_poll_success_at if health else None,
                health.last_poll_error if health else None,
                health_now,
                "poll_failed",
            )
            return {
                "generated_at": observed_now.isoformat(),
                "device_id": self.device_id,
                "api": {"state": "online"},
                "push": push,
                "poll": poll,
                "runtime": self._runtime_status(push, poll),
                "inbox": {
                    "received": inbox_counts.get("received", 0),
                    "processing": inbox_counts.get("processing", 0),
                    "waiting_mapping": inbox_counts.get("waiting_mapping", 0),
                    "oldest_pending_at": _iso(oldest_pending),
                },
                "profile_worker": profile,
            }
        finally:
            db.close()

    def recent_recognitions(
        self, limit: int = 20, now: datetime | None = None
    ) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        observed_now = now or self.clock()
        db = self.session_factory()
        try:
            inbox_rows = (
                db.query(DeviceRecognitionInbox)
                .order_by(
                    DeviceRecognitionInbox.occurred_at.desc(),
                    DeviceRecognitionInbox.received_at.desc(),
                    DeviceRecognitionInbox.canonical_event_id,
                )
                .limit(limit)
                .all()
            )
            member_ids = {row.member_id for row in inbox_rows if row.member_id}
            members = {}
            if member_ids:
                members = {
                    member.id: member
                    for member in db.query(Member).filter(Member.id.in_(member_ids)).all()
                }
            event_ids = [row.canonical_event_id for row in inbox_rows]
            member_events = {}
            if event_ids:
                member_events = {
                    member_event.event_id: member_event
                    for member_event in db.query(MemberEntryEvent)
                    .filter(MemberEntryEvent.event_id.in_(event_ids))
                    .all()
                }
            return {
                "generated_at": observed_now.isoformat(),
                "items": [
                    self._recognition_item(row, members.get(row.member_id), member_events.get(row.canonical_event_id))
                    for row in inbox_rows
                ],
            }
        finally:
            db.close()

    def _health_channel(
        self,
        enabled: bool,
        observed_at: datetime | None,
        stored_error: str | None,
        now: datetime,
        error_code: str,
    ) -> dict:
        if observed_at is None:
            state = "unknown"
        elif enabled and timedelta(0) <= now - observed_at <= self.health_freshness:
            state = "healthy"
        else:
            state = "stale"
        return {
            "state": state,
            "last_seen_at": _iso(observed_at),
            "last_error": error_code if stored_error else None,
        }

    @staticmethod
    def _runtime_status(push: dict, poll: dict) -> dict:
        channels = (push, poll)
        observed_at = max(
            (
                channel["last_seen_at"]
                for channel in channels
                if channel["last_seen_at"] is not None
            ),
            default=None,
        )
        if any(channel["state"] == "healthy" for channel in channels):
            state = "running"
        elif observed_at is not None:
            state = "stale"
        else:
            state = "unknown"
        return {
            "state": state,
            "observed_at": observed_at,
            "basis": "push_or_poll",
        }

    def _profile_status(self, db, now: datetime) -> dict:
        counts = dict(
            db.query(BusinessJob.status, func.count())
            .filter(BusinessJob.task_type == "customer_profile")
            .group_by(BusinessJob.status)
            .all()
        )
        due_filters = (
            BusinessJob.task_type == "customer_profile",
            BusinessJob.status.in_(("pending", "failed")),
            BusinessJob.available_at <= now,
            BusinessJob.attempts < BusinessJob.max_attempts,
        )
        oldest_due = db.query(func.min(BusinessJob.available_at)).filter(*due_filters).scalar()
        running_cutoff = now - self.profile_lock_timeout
        fresh_running = (
            db.query(BusinessJob.id)
            .filter(
                BusinessJob.task_type == "customer_profile",
                BusinessJob.status == "running",
                BusinessJob.locked_at > running_cutoff,
            )
            .first()
            is not None
        )
        if self.profile_mode == "disabled":
            state = "disabled"
        elif fresh_running:
            state = "busy"
        elif oldest_due is not None:
            state = "backlogged"
        else:
            state = "idle"
        return {
            "state": state,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "failed": counts.get("failed", 0),
            "oldest_pending_at": _iso(oldest_due),
            "basis": "job_observation",
        }

    @staticmethod
    def _recognition_item(inbox, member, member_event) -> dict:
        member_data = None if member is None else {
            "id": member.id,
            "name": member.name,
            "tier": normalize_tier(member.tier),
            "priority_rank": tier_rank(member.tier),
            "alert_level": alert_level(member.tier),
        }
        return {
            "event_id": inbox.canonical_event_id,
            "device_id": inbox.device_id,
            "source": inbox.source,
            "occurred_at": _iso(inbox.occurred_at),
            "received_at": _iso(inbox.received_at),
            "processed_at": _iso(inbox.processed_at),
            "status": inbox.status,
            "reason": OperationsQueryService._reason(inbox),
            "similarity": inbox.similarity,
            "capture_available": bool(inbox.capture_path),
            "member": member_data,
            # New association mode records the canonical visit on the Inbox;
            # legacy mode keeps it on MemberEntryEvent.  Project the same
            # read-only visit relationship for both modes without mutating
            # either persisted row.
            "visit_id": (
                member_event.visit_id
                if member_event and member_event.visit_id
                else inbox.matched_visit_id
            ),
            "track_id": member_event.track_id if member_event else None,
        }

    @staticmethod
    def _reason(inbox) -> str | None:
        if inbox.status == "waiting_mapping":
            return "waiting_mapping"
        if inbox.status == "unmapped":
            return "mapping_timeout"
        if inbox.last_error == "member_debounce":
            return "member_debounce"
        if inbox.status == "ignored":
            return "rule_or_similarity_filter"
        if inbox.status in ("received", "processing", "failed") and inbox.last_error:
            return "processing_retry"
        return None
