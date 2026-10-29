"""Historical Push Recognition coverage observations and deterministic query."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Literal, Optional

from sqlalchemy.exc import IntegrityError

from agent.models import PushCoverageObservation


OBSERVATION_TYPES = frozenset(
    {
        "heartbeat_accepted",
        "receiver_started",
        "receiver_graceful_stopped",
        "receiver_unclean_detected",
        "persistence_success",
        "persistence_failure",
    }
)
_STOP_TYPES = frozenset({"receiver_graceful_stopped", "receiver_unclean_detected"})


def _utc_naive(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("coverage time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=None)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class PushCoveragePolicy:
    """Versioned timing contract for historical heartbeat continuity."""

    policy_id: str = "p6s_push_coverage"
    policy_version: str = "p6s_push_coverage_v1"
    expected_heartbeat_interval_seconds: float = 30.0
    transport_jitter_budget_seconds: float = 5.0
    persistence_jitter_budget_seconds: float = 5.0
    maximum_heartbeat_gap_seconds: float = 40.0

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.policy_version.strip():
            raise ValueError("coverage policy identity is required")
        values = (
            self.expected_heartbeat_interval_seconds,
            self.transport_jitter_budget_seconds,
            self.persistence_jitter_budget_seconds,
            self.maximum_heartbeat_gap_seconds,
        )
        if any(value < 0 for value in values):
            raise ValueError("coverage policy values must be non-negative")
        if self.expected_heartbeat_interval_seconds <= 0:
            raise ValueError("expected heartbeat interval must be positive")
        if self.maximum_heartbeat_gap_seconds < self.expected_heartbeat_interval_seconds:
            raise ValueError("maximum heartbeat gap must cover expected interval")


DEFAULT_PUSH_COVERAGE_POLICY = PushCoveragePolicy()


@dataclass(frozen=True)
class PushCoverageResult:
    status: Literal["complete", "unavailable"]
    device_id: str
    coverage_start: datetime
    coverage_end: datetime
    policy_version: str
    reason: Optional[str]
    material_hash: str
    supporting_observation_ids: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.status == "complete"


class PushCoverageStore:
    """Append-only observation writer and fail-closed coverage query."""

    def __init__(
        self,
        session_factory: Callable[[], object],
        *,
        policy: PushCoveragePolicy = DEFAULT_PUSH_COVERAGE_POLICY,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.clock = clock

    def record(
        self,
        *,
        device_id: str,
        observation_type: str,
        observed_at: datetime,
        receiver_instance_id: str,
        boot_id: str,
        outcome: str,
        observation_key: str,
        device_event_at: Optional[datetime] = None,
        source_event_id: Optional[str] = None,
        canonical_event_id: Optional[str] = None,
        failure_class: Optional[str] = None,
        policy_version: Optional[str] = None,
        device_metadata: Optional[dict] = None,
    ) -> PushCoverageObservation:
        if observation_type not in OBSERVATION_TYPES:
            raise ValueError("unknown coverage observation type")
        if not device_id or not receiver_instance_id or not boot_id:
            raise ValueError("device, receiver instance and boot identifiers are required")
        if not observation_key:
            raise ValueError("observation_key is required")
        policy_version = policy_version or self.policy.policy_version
        observed_at = _utc_naive(observed_at)
        device_event_at = (
            None if device_event_at is None else _utc_naive(device_event_at)
        )
        session = self.session_factory()
        try:
            existing = session.query(PushCoverageObservation).filter_by(
                observation_key=observation_key
            ).one_or_none()
            if existing is not None:
                return existing
            row = PushCoverageObservation(
                id=uuid.uuid4().hex,
                observation_key=observation_key,
                device_id=device_id,
                observation_type=observation_type,
                observed_at=observed_at,
                device_event_at=device_event_at,
                receiver_instance_id=receiver_instance_id,
                boot_id=boot_id,
                source_event_id=source_event_id,
                canonical_event_id=canonical_event_id,
                outcome=outcome,
                failure_class=failure_class,
                policy_version=policy_version,
                device_metadata=device_metadata,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row
        except IntegrityError:
            session.rollback()
            existing = session.query(PushCoverageObservation).filter_by(
                observation_key=observation_key
            ).one_or_none()
            if existing is None:
                raise
            return existing
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_heartbeat(
        self,
        *,
        device_id: str,
        receiver_instance_id: str,
        boot_id: str,
        source_event_id: str,
        observed_at: datetime,
        device_event_at: Optional[datetime],
        device_metadata: Optional[dict] = None,
    ) -> tuple[PushCoverageObservation, PushCoverageObservation]:
        prefix = f"{device_id}:{receiver_instance_id}:{source_event_id}"
        heartbeat = self.record(
            device_id=device_id,
            observation_type="heartbeat_accepted",
            observed_at=observed_at,
            device_event_at=device_event_at,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            source_event_id=source_event_id,
            outcome="accepted",
            observation_key=f"heartbeat:{prefix}",
            device_metadata=device_metadata,
        )
        persistence = self.record(
            device_id=device_id,
            observation_type="persistence_success",
            observed_at=observed_at,
            device_event_at=device_event_at,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            source_event_id=source_event_id,
            outcome="committed",
            observation_key=f"persistence-success:{prefix}",
            device_metadata=device_metadata,
        )
        return heartbeat, persistence

    def record_persistence_failure(
        self,
        *,
        device_id: str,
        receiver_instance_id: str,
        boot_id: str,
        source_event_id: Optional[str],
        observed_at: datetime,
        failure_class: str,
    ) -> PushCoverageObservation:
        source = source_event_id or uuid.uuid4().hex
        return self.record(
            device_id=device_id,
            observation_type="persistence_failure",
            observed_at=observed_at,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            source_event_id=source_event_id,
            outcome="failed",
            failure_class=failure_class,
            observation_key=(
                f"persistence-failure:{device_id}:{receiver_instance_id}:"
                f"{source}:{failure_class}"
            ),
        )

    def start_receiver(
        self,
        *,
        device_id: str,
        receiver_instance_id: str,
        boot_id: str,
        observed_at: Optional[datetime] = None,
    ) -> PushCoverageObservation:
        observed_at = _utc_naive(observed_at or self.clock())
        session = self.session_factory()
        try:
            previous_starts = session.query(PushCoverageObservation).filter(
                PushCoverageObservation.device_id == device_id,
                PushCoverageObservation.observation_type == "receiver_started",
                PushCoverageObservation.observed_at < observed_at,
            ).order_by(PushCoverageObservation.observed_at.desc()).all()
            for previous in previous_starts:
                stopped = session.query(PushCoverageObservation).filter(
                    PushCoverageObservation.receiver_instance_id == previous.receiver_instance_id,
                    PushCoverageObservation.observation_type.in_(_STOP_TYPES),
                ).first()
                if stopped is None:
                    key = f"receiver-unclean:{previous.receiver_instance_id}:{receiver_instance_id}"
                    if session.query(PushCoverageObservation).filter_by(observation_key=key).one_or_none() is None:
                        session.add(PushCoverageObservation(
                            id=uuid.uuid4().hex,
                            observation_key=key,
                            device_id=device_id,
                            observation_type="receiver_unclean_detected",
                            observed_at=observed_at,
                            receiver_instance_id=previous.receiver_instance_id,
                            boot_id=previous.boot_id,
                            outcome="fail_closed",
                            failure_class="missing_graceful_stop",
                            policy_version=self.policy.policy_version,
                        ))
                    break
            row = PushCoverageObservation(
                id=uuid.uuid4().hex,
                observation_key=f"receiver-started:{receiver_instance_id}",
                device_id=device_id,
                observation_type="receiver_started",
                observed_at=observed_at,
                receiver_instance_id=receiver_instance_id,
                boot_id=boot_id,
                outcome="ready",
                policy_version=self.policy.policy_version,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def stop_receiver(
        self,
        *,
        device_id: str,
        receiver_instance_id: str,
        boot_id: str,
        observed_at: Optional[datetime] = None,
    ) -> PushCoverageObservation:
        observed_at = _utc_naive(observed_at or self.clock())
        return self.record(
            device_id=device_id,
            observation_type="receiver_graceful_stopped",
            observed_at=observed_at,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            outcome="graceful",
            observation_key=f"receiver-stopped:{receiver_instance_id}",
        )

    def coverage(
        self,
        *,
        device_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> PushCoverageResult:
        start_at = _utc_naive(start_at)
        end_at = _utc_naive(end_at)
        if end_at < start_at:
            raise ValueError("coverage end must be on or after start")
        # The first persisted heartbeat after ``end_at`` proves the final
        # heartbeat interval. A request must not require a heartbeat at the
        # exact RecordingFile boundary, but it also cannot use arbitrarily
        # later activity to bridge an unknown gap.
        proof_deadline = end_at + timedelta(
            seconds=self.policy.maximum_heartbeat_gap_seconds
        )
        session = self.session_factory()
        try:
            rows = session.query(PushCoverageObservation).filter(
                PushCoverageObservation.device_id == device_id,
                PushCoverageObservation.observed_at <= proof_deadline,
            ).order_by(
                PushCoverageObservation.observed_at,
                PushCoverageObservation.id,
            ).all()
        finally:
            session.close()

        starts = [
            row for row in rows
            if row.observation_type == "receiver_started"
            and row.observed_at <= start_at
        ]
        if not starts:
            return self._unavailable(device_id, start_at, end_at, "receiver_start_missing")
        start_row = starts[-1]
        instance_id = start_row.receiver_instance_id
        lifecycle = [
            row for row in rows
            if row.receiver_instance_id == instance_id
        ]
        if any(
            row.observation_type == "receiver_started"
            and start_at < row.observed_at <= end_at
            for row in lifecycle
        ):
            return self._unavailable(device_id, start_at, end_at, "receiver_restart")
        if any(
            row.observation_type in _STOP_TYPES
            and row.observed_at <= start_at
            for row in lifecycle
            if row.observation_type in _STOP_TYPES
        ):
            return self._unavailable(device_id, start_at, end_at, "receiver_not_active_at_start")
        terminal = [
            row for row in lifecycle
            if row.observation_type in _STOP_TYPES
            and start_at <= row.observed_at <= end_at
        ]
        if terminal:
            return self._unavailable(
                device_id, start_at, end_at,
                "receiver_unclean_or_stopped",
                (start_row, *terminal),
            )

        heartbeats = [
            row for row in lifecycle
            if row.observation_type == "heartbeat_accepted"
            and row.policy_version == self.policy.policy_version
        ]
        anchor = [row for row in heartbeats if row.observed_at <= start_at]
        if not anchor:
            return self._unavailable(device_id, start_at, end_at, "heartbeat_start_missing")
        selected = [anchor[-1]]
        for heartbeat in heartbeats:
            if heartbeat.observed_at <= anchor[-1].observed_at:
                continue
            if heartbeat.observed_at > proof_deadline:
                break
            selected.append(heartbeat)
            if heartbeat.observed_at >= end_at:
                break
        if selected[-1].observed_at < end_at:
            return self._unavailable(
                device_id, start_at, end_at, "heartbeat_end_missing",
                selected,
            )
        gaps = [
            (right.observed_at - left.observed_at).total_seconds()
            for left, right in zip(selected, selected[1:])
        ]
        if any(gap > self.policy.maximum_heartbeat_gap_seconds for gap in gaps):
            return self._unavailable(
                device_id, start_at, end_at, "heartbeat_gap_exceeded",
                selected,
            )
        failures = [
            row for row in lifecycle
            if (
            row.observation_type == "persistence_failure"
            and start_at <= row.observed_at <= end_at
            )
        ]
        if failures:
            return self._unavailable(
                device_id, start_at, end_at, "persistence_failure",
                (*selected, *failures),
            )

        successes = [
            row for row in lifecycle
            if row.observation_type == "persistence_success"
            and row.policy_version == self.policy.policy_version
        ]
        supporting = [start_row]
        for heartbeat in selected:
            if not heartbeat.source_event_id:
                return self._unavailable(
                    device_id, start_at, end_at, "heartbeat_source_missing",
                    supporting,
                )
            matched = [
                row for row in successes
                if row.source_event_id == heartbeat.source_event_id
                and abs((row.observed_at - heartbeat.observed_at).total_seconds())
                <= self.policy.persistence_jitter_budget_seconds
            ]
            if not matched:
                return self._unavailable(
                    device_id, start_at, end_at, "persistence_success_missing",
                    (*supporting, heartbeat),
                )
            supporting.extend((heartbeat, matched[0]))
        return self._result(
            status="complete",
            device_id=device_id,
            start_at=start_at,
            end_at=end_at,
            reason=None,
            observations=supporting,
        )

    def _unavailable(
        self,
        device_id: str,
        start_at: datetime,
        end_at: datetime,
        reason: str,
        observations: Iterable[PushCoverageObservation] = (),
    ) -> PushCoverageResult:
        return self._result(
            status="unavailable",
            device_id=device_id,
            start_at=start_at,
            end_at=end_at,
            reason=reason,
            observations=observations,
        )

    def _result(
        self,
        *,
        status: Literal["complete", "unavailable"],
        device_id: str,
        start_at: datetime,
        end_at: datetime,
        reason: Optional[str],
        observations: Iterable[PushCoverageObservation],
    ) -> PushCoverageResult:
        rows = tuple(row for row in observations if row is not None)
        supporting = tuple(dict.fromkeys(row.id for row in rows))
        by_id = {
            row.id: row for row in rows if row.id in supporting
        }
        material = {
            "device_id": device_id,
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "policy_version": self.policy.policy_version,
            "supporting_observations": [
                _canonical_observation_material(by_id[row_id])
                for row_id in supporting
            ],
            "status": status,
            "reason": reason,
        }
        encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return PushCoverageResult(
            status=status,
            device_id=device_id,
            coverage_start=start_at,
            coverage_end=end_at,
            policy_version=self.policy.policy_version,
            reason=reason,
            material_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            supporting_observation_ids=supporting,
        )


def _canonical_observation_material(
    observation: PushCoverageObservation,
) -> dict[str, object]:
    """Return stable fact fields, excluding DB and process-local identifiers."""
    return {
        "observation_type": observation.observation_type,
        "device_id": observation.device_id,
        "observed_at": observation.observed_at.isoformat(),
        "device_event_at": (
            observation.device_event_at.isoformat()
            if observation.device_event_at is not None
            else None
        ),
        "source_event_id": observation.source_event_id,
        "canonical_event_id": observation.canonical_event_id,
        "outcome": observation.outcome,
        "failure_class": observation.failure_class,
        "policy_version": observation.policy_version,
    }
