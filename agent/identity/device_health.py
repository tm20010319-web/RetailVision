"""Persistence and freshness checks for independent identity sources."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from agent.models import DeviceHealthState


def _utc_naive(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("health time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=None)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class DeviceHealthStore:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        freshness_seconds: float = 90.0,
    ) -> None:
        self.session_factory = session_factory
        self.freshness = timedelta(seconds=freshness_seconds)

    def mark_heartbeat(self, device_id: str, occurred_at: datetime) -> DeviceHealthState:
        session = self.session_factory()
        try:
            state = session.get(DeviceHealthState, device_id)
            if state is None:
                state = DeviceHealthState(device_id=device_id)
                session.add(state)
            state.push_healthy = True
            state.last_heartbeat_at = _utc_naive(occurred_at)
            state.last_push_error = None
            session.commit()
            session.refresh(state)
            return state
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get(self, device_id: str) -> Optional[DeviceHealthState]:
        session = self.session_factory()
        try:
            return session.get(DeviceHealthState, device_id)
        finally:
            session.close()

    def mark_poll_success(
        self, device_id: str, occurred_at: datetime
    ) -> DeviceHealthState:
        session = self.session_factory()
        try:
            state = session.get(DeviceHealthState, device_id)
            if state is None:
                state = DeviceHealthState(device_id=device_id)
                session.add(state)
            state.poll_healthy = True
            state.last_poll_success_at = _utc_naive(occurred_at)
            state.last_poll_error = None
            session.commit()
            session.refresh(state)
            return state
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_poll_failure(
        self, device_id: str, reason: str
    ) -> DeviceHealthState:
        session = self.session_factory()
        try:
            state = session.get(DeviceHealthState, device_id)
            if state is None:
                state = DeviceHealthState(device_id=device_id)
                session.add(state)
            state.poll_healthy = False
            state.last_poll_error = reason[:512]
            session.commit()
            session.refresh(state)
            return state
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def identity_available(self, device_id: str, now: datetime) -> bool:
        state = self.get(device_id)
        if state is None:
            return False
        normalized_now = _utc_naive(now)

        def fresh(observed_at: Optional[datetime]) -> bool:
            if observed_at is None:
                return False
            age = normalized_now - _utc_naive(observed_at)
            return timedelta(0) <= age <= self.freshness

        push_available = bool(
            state.push_healthy and fresh(state.last_heartbeat_at)
        )
        poll_available = bool(
            state.poll_healthy and fresh(state.last_poll_success_at)
        )
        return push_available or poll_available
