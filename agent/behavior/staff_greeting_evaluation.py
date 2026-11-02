"""Deep module for official Staff Greeting and optional Shadow evaluation."""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Iterable, Optional, Sequence

from agent.behavior.behavior_events import BehaviorEventInput, EntryEventInput
from agent.behavior.behavior_monitor import (
    BehaviorMonitorConfig,
    EntrySpatialSide,
    Point,
    PolygonArea,
    TrackObservation,
)
from agent.behavior._staff_greeting_shadow import (
    GreetingShadowConfig,
    _GreetingEvaluationStore,
    _GreetingShadowEvaluator,
    result_to_input,
)

logger = logging.getLogger(__name__)


class GreetingStage(str, Enum):
    NOT_ELIGIBLE = "not_eligible"
    ENTRY_CONFIRMED = "entry_confirmed"
    WAITING_FOR_GREETING = "waiting_for_greeting"
    GREETING_IN_PROGRESS = "greeting_in_progress"
    GREETED = "greeted"
    NOT_OBSERVED = "not_observed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GreetingCancellation:
    """Terminal lifecycle record; ``started_at`` is the Cycle creation time."""

    camera_id: str
    customer_track_token: str
    greeting_cycle_id: str
    cancelled_at: datetime
    cancel_reason: str
    previous_stage: GreetingStage
    # Optional only for callers constructing legacy cancellation records; Core
    # always supplies the authoritative Cycle creation timestamp.
    started_at: Optional[datetime] = None


@dataclass
class _GreetingPair:
    staff_origin: Point
    customer_origin: Point
    initial_distance: float
    minimum_distance: float
    staff_track_id: int
    staff_track_token: str
    maximum_travel: float = 0.0
    maximum_toward_customer: float = 0.0
    within_since: Optional[datetime] = None
    lifetime_confidence: float = 1.0
    qualifying_confidence: Optional[float] = None
    approach_confirmed: bool = False
    staff_role_source: Optional[str] = None
    staff_role_confidence: Optional[float] = None
    staff_in_greeting_area: bool = False
    staff_enter_frames: int = 0
    staff_exit_frames: int = 0
    staff_exit_started_at: Optional[datetime] = None
    last_observed_at: Optional[datetime] = None
    observations: deque[tuple[datetime, Point, Point, float]] = field(
        default_factory=lambda: deque(maxlen=8)
    )


@dataclass
class _GreetingState:
    entry_key: str
    greeting_cycle_id: str
    customer: TrackObservation
    arrived_at: datetime
    last_seen_at: datetime
    stage: GreetingStage = GreetingStage.ENTRY_CONFIRMED
    pairs: dict[str, _GreetingPair] = field(default_factory=dict)
    had_ground_observation: bool = False
    entered_greeting_area: bool = False
    greeting_area_started_at: Optional[datetime] = None
    customer_in_greeting_area: bool = False
    customer_enter_frames: int = 0
    customer_exit_frames: int = 0
    customer_exit_started_at: Optional[datetime] = None
    active_staff_track_token: Optional[str] = None


class StaffGreetingEvaluation:
    """Evaluate and durably record official Staff Greeting outcomes."""

    def __init__(
        self,
        *,
        camera_id: str,
        entrance_area: Optional[PolygonArea],
        greeting_area: Optional[PolygonArea],
        config: BehaviorMonitorConfig,
        behavior_sink: Callable[[BehaviorEventInput], object],
        calibration_status: Optional[str] = None,
        ground_calibration_available: bool = True,
        comparison_enabled: bool = False,
        session_factory=None,
        shadow_config: Optional[GreetingShadowConfig] = None,
    ) -> None:
        if not camera_id.strip():
            raise ValueError("camera_id is required")
        if entrance_area is not None and entrance_area.area_type != "entrance":
            raise ValueError("entrance_area must have type entrance")
        if greeting_area is not None and greeting_area.area_type != "greeting":
            raise ValueError("greeting_area must have type greeting")
        if not callable(behavior_sink):
            raise ValueError("behavior_sink is required")
        if not isinstance(ground_calibration_available, bool):
            raise ValueError("ground_calibration_available must be a boolean")
        if comparison_enabled and session_factory is None:
            raise ValueError("session_factory is required when comparison is enabled")
        self._official = OfficialGreetingCore(
            camera_id=camera_id,
            entrance_area=entrance_area,
            greeting_area=greeting_area,
            config=config,
            calibration_status=calibration_status,
            ground_calibration_available=ground_calibration_available,
        )
        self._behavior_sink = behavior_sink
        self._cancellation_log = deque(maxlen=256)
        self._shadow = None
        self._store = None
        if comparison_enabled:
            self._shadow = _GreetingShadowEvaluator(
                camera_id=camera_id,
                greeting_area=greeting_area,
                config=shadow_config,
                calibration_status=calibration_status,
            )
            self._store = _GreetingEvaluationStore(session_factory)

    def observe(
        self,
        observed_at: datetime,
        observations: Sequence[TrackObservation],
        entries: Sequence[EntryEventInput] = (),
    ) -> tuple[BehaviorEventInput, ...]:
        events, cancellations = self._official.observe(
            observed_at, observations, entries
        )
        self._cancellation_log.extend(cancellations)
        for event in events:
            self._behavior_sink(event)
        self._persist_comparison(
            observed_at,
            observations,
            entries,
            events,
            cancellations,
        )
        return events

    def cancel_all(
        self, observed_at: datetime, reason: str
    ) -> tuple[GreetingCancellation, ...]:
        cancellations = self._official.cancel_all(observed_at, reason)
        self._cancellation_log.extend(cancellations)
        if self._shadow is None or self._store is None:
            return cancellations
        try:
            self._persist_cancellations(cancellations)
            for result in self._shadow.cancel_all(observed_at, reason):
                self._store.settle(result_to_input(result))
        except Exception as exc:  # Comparison is never authoritative.
            self._log_comparison_failure("cancellation", exc)
        return cancellations

    def debug_lines(self, customer_track_token: str) -> tuple[str, ...]:
        return self._official.debug_lines(customer_track_token)

    def _persist_comparison(
        self,
        observed_at: datetime,
        observations: Sequence[TrackObservation],
        entries: Sequence[EntryEventInput],
        events: Sequence[BehaviorEventInput],
        cancellations: Sequence[GreetingCancellation],
    ) -> None:
        if self._shadow is None or self._store is None:
            return
        try:
            for entry in entries:
                self._store.start_cycle(entry)
            for event in events:
                self._store.settle_official_behavior(event)
            self._persist_cancellations(cancellations)
            for result in self._shadow.observe(observed_at, observations, entries):
                self._store.settle(result_to_input(result))
        except Exception as exc:  # Shadow must never stop official evaluation.
            self._log_comparison_failure("frame", exc)

    def _persist_cancellations(
        self, cancellations: Sequence[GreetingCancellation]
    ) -> None:
        if self._store is None:
            return
        for cancellation in cancellations:
            self._store.settle_official_cancellation(
                cancellation.greeting_cycle_id.removesuffix(":greeting-cycle"),
                camera_id=cancellation.camera_id,
                track_id=0,
                track_token=cancellation.customer_track_token,
                occurred_at=cancellation.cancelled_at,
                reason=cancellation.cancel_reason,
            )

    @staticmethod
    def _log_comparison_failure(operation: str, error: Exception) -> None:
        logger.error(
            "Staff Greeting comparison failed: operation=%s error_type=%s",
            operation,
            type(error).__name__,
        )


class OfficialGreetingCore:
    def __init__(
        self,
        *,
        camera_id: str,
        entrance_area: Optional[PolygonArea],
        greeting_area: Optional[PolygonArea],
        config: BehaviorMonitorConfig,
        calibration_status: Optional[str],
        ground_calibration_available: bool,
    ) -> None:
        self.camera_id = camera_id
        self.entrance_area = entrance_area
        self.greeting_area = greeting_area
        self.config = config
        self.calibration_status = calibration_status
        self.ground_calibration_available = ground_calibration_available
        self._greetings: dict[str, _GreetingState] = {}
        self._finalized_cycles: set[str] = set()
        self._frame_cancellations: list[GreetingCancellation] = []

    def observe(
        self,
        observed_at: datetime,
        observations: Sequence[TrackObservation],
        entries: Sequence[EntryEventInput],
    ) -> tuple[tuple[BehaviorEventInput, ...], tuple[GreetingCancellation, ...]]:
        if not isinstance(observed_at, datetime):
            raise ValueError("observed_at must be a datetime")
        tracks = list(observations)
        customers = {
            track.track_token: track for track in tracks if track.role == "customer"
        }
        staff = [track for track in tracks if track.role == "staff"]
        self._frame_cancellations = []

        for track in staff:
            if track.track_token in self._greetings:
                self._cancel(
                    track.track_token,
                    observed_at,
                    "customer_role_corrected_to_staff",
                )

        entries_by_token = {entry.track_token: entry for entry in entries}
        events: list[BehaviorEventInput] = []
        for customer in customers.values():
            entry = entries_by_token.get(customer.track_token)
            if entry is not None:
                self._start(entry, customer, observed_at)
            event = self._observe_customer(observed_at, customer, staff)
            if event is not None:
                events.append(event)
        events.extend(self._expire(observed_at, customers))
        return tuple(events), tuple(self._frame_cancellations)

    def cancel_all(
        self, observed_at: datetime, reason: str
    ) -> tuple[GreetingCancellation, ...]:
        if not isinstance(observed_at, datetime):
            raise ValueError("observed_at must be a datetime")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        self._frame_cancellations = []
        for token in tuple(self._greetings):
            self._cancel(token, observed_at, reason)
        return tuple(self._frame_cancellations)

    def debug_lines(self, token: str) -> tuple[str, ...]:
        state = self._debug_state(token)
        distance = state["distance_metres"]
        travel = state["staff_travel_metres"]
        reduction = state["distance_reduction_metres"]
        sustained = state["sustained_seconds"]
        return (
            f"GREETING STATE: {str(state['greeting_state']).upper()}",
            f"WAITING: {'YES' if state['customer_in_greeting_area'] else 'NO'}",
            f"DISTANCE: {distance:.2f}m" if isinstance(distance, float) else "DISTANCE: -",
            f"STAFF TRAVEL: {travel:.2f}m" if isinstance(travel, float) else "STAFF TRAVEL: -",
            (
                f"DISTANCE REDUCTION: {reduction:.2f}m"
                if isinstance(reduction, float)
                else "DISTANCE REDUCTION: -"
            ),
            (
                f"SUSTAINED: {sustained:.2f}s"
                if isinstance(sustained, float)
                else "SUSTAINED: -"
            ),
        )

    def _start(
        self,
        entry: EntryEventInput,
        customer: TrackObservation,
        observed_at: datetime,
    ) -> None:
        self._greetings[customer.track_token] = _GreetingState(
            entry_key=entry.entry_key,
            greeting_cycle_id=f"{entry.entry_key}:greeting-cycle",
            customer=customer,
            arrived_at=observed_at,
            last_seen_at=observed_at,
        )
        if self.greeting_area is None:
            self._cancel(customer.track_token, observed_at, "greeting_area_missing")
        elif not self.ground_calibration_available:
            self._cancel(customer.track_token, observed_at, "calibration_unavailable")

    def _cancel(
        self, token: str, observed_at: datetime, reason: str
    ) -> GreetingCancellation:
        state = self._greetings.pop(token)
        previous_stage = state.stage
        state.stage = GreetingStage.CANCELLED
        cancellation = GreetingCancellation(
            camera_id=self.camera_id,
            customer_track_token=token,
            greeting_cycle_id=state.greeting_cycle_id,
            started_at=state.arrived_at,
            cancelled_at=observed_at,
            cancel_reason=reason,
            previous_stage=previous_stage,
        )
        self._frame_cancellations.append(cancellation)
        return cancellation

    def _observe_customer(
        self,
        observed_at: datetime,
        customer: TrackObservation,
        staff_tracks: Iterable[TrackObservation],
    ) -> Optional[BehaviorEventInput]:
        state = self._greetings.get(customer.track_token)
        if state is None:
            return None
        state.customer = customer
        state.last_seen_at = observed_at
        customer_in_area = bool(
            self.greeting_area is not None
            and self.greeting_area.contains(customer.foot_point)
        )
        if customer_in_area:
            state.customer_enter_frames += 1
            state.customer_exit_frames = 0
            state.customer_exit_started_at = None
            if state.customer_enter_frames >= self.config.greeting_area_enter_stable_frames:
                if not state.customer_in_greeting_area:
                    state.greeting_area_started_at = observed_at
                state.customer_in_greeting_area = True
                state.entered_greeting_area = True
                if state.stage == GreetingStage.ENTRY_CONFIRMED:
                    state.stage = GreetingStage.WAITING_FOR_GREETING
        else:
            state.customer_enter_frames = 0
            if state.customer_in_greeting_area:
                state.customer_exit_frames += 1
                if state.customer_exit_started_at is None:
                    state.customer_exit_started_at = observed_at
                exit_elapsed = (
                    observed_at - state.customer_exit_started_at
                ).total_seconds()
                grace_elapsed = (
                    self.config.greeting_area_exit_grace_seconds == 0
                    or exit_elapsed > self.config.greeting_area_exit_grace_seconds
                )
                if (
                    state.customer_exit_frames
                    >= self.config.greeting_area_exit_stable_frames
                    and grace_elapsed
                ):
                    state.customer_in_greeting_area = False
                    self._cancel(
                        customer.track_token,
                        observed_at,
                        "customer_left_greeting_area",
                    )
                    return None
            elif self._entry_spatial_side(customer.foot_point) == EntrySpatialSide.OUTSIDE_SIDE:
                self._cancel(customer.track_token, observed_at, "customer_left_entrance")
                return None
        if customer.world_point is None:
            return None
        if self.config.greeting_profile == "demo_relaxed" and not state.customer_in_greeting_area:
            return None
        state.had_ground_observation = True
        for staff in staff_tracks:
            event = self._observe_pair(state, customer, staff, observed_at)
            if event is not None:
                return event
            if customer.track_token not in self._greetings:
                return None
        return None

    def _observe_pair(
        self,
        state: _GreetingState,
        customer: TrackObservation,
        staff: TrackObservation,
        observed_at: datetime,
    ) -> Optional[BehaviorEventInput]:
        if staff.world_point is None or customer.world_point is None:
            return None
        pair = state.pairs.get(staff.track_token)
        distance = _distance(customer.world_point, staff.world_point)
        if pair is None:
            pair = _GreetingPair(
                staff_origin=staff.world_point,
                customer_origin=customer.world_point,
                initial_distance=distance,
                minimum_distance=distance,
                staff_track_id=staff.track_id,
                staff_track_token=staff.track_token,
                lifetime_confidence=min(
                    customer.confidence,
                    staff.confidence,
                    staff.role_confidence
                    if staff.role_confidence is not None
                    else 1.0,
                ),
                staff_role_source=staff.role_source,
                staff_role_confidence=staff.role_confidence,
            )
            state.pairs[staff.track_token] = pair
        pair.observations.append(
            (observed_at, staff.world_point, customer.world_point, distance)
        )
        pair.last_observed_at = observed_at
        pair.minimum_distance = min(pair.minimum_distance, distance)
        pair.maximum_travel = max(
            pair.maximum_travel,
            _distance(pair.staff_origin, staff.world_point),
        )
        if pair.initial_distance > 1e-9:
            direction = (
                (pair.customer_origin[0] - pair.staff_origin[0])
                / pair.initial_distance,
                (pair.customer_origin[1] - pair.staff_origin[1])
                / pair.initial_distance,
            )
            displacement = (
                staff.world_point[0] - pair.staff_origin[0],
                staff.world_point[1] - pair.staff_origin[1],
            )
            pair.maximum_toward_customer = max(
                pair.maximum_toward_customer,
                displacement[0] * direction[0] + displacement[1] * direction[1],
            )
        current_confidence = min(
            customer.confidence,
            staff.confidence,
            staff.role_confidence if staff.role_confidence is not None else 1.0,
        )
        pair.lifetime_confidence = min(
            pair.lifetime_confidence, current_confidence
        )
        pair.staff_role_source = staff.role_source or pair.staff_role_source
        if staff.role_confidence is not None:
            pair.staff_role_confidence = staff.role_confidence
        reduction = pair.initial_distance - pair.minimum_distance
        toward_threshold = (
            self.config.greeting_staff_toward_customer_metres
            if self.config.greeting_staff_toward_customer_metres is not None
            else self.config.greeting_staff_travel_metres
        )
        if (
            pair.maximum_travel >= self.config.greeting_staff_travel_metres
            and pair.maximum_toward_customer >= toward_threshold
            and reduction >= self.config.minimum_distance_reduction_metres
            and _distance_trend_is_decreasing(
                pair.observations,
                window_samples=self.config.greeting_trend_window_samples,
                minimum_samples=self.config.greeting_trend_minimum_samples,
                minimum_reduction_metres=self.config.greeting_trend_minimum_reduction_metres,
                decreasing_ratio=self.config.greeting_trend_decreasing_ratio,
            )
        ):
            pair.approach_confirmed = True
        demo_confirmed = self._demo_trajectory_confirmed(pair)
        staff_in_area = bool(
            self.greeting_area is not None
            and self.greeting_area.contains(staff.foot_point)
        )
        if staff_in_area:
            pair.staff_enter_frames += 1
            pair.staff_exit_frames = 0
            pair.staff_exit_started_at = None
            if pair.staff_enter_frames >= self.config.greeting_area_enter_stable_frames:
                pair.staff_in_greeting_area = True
        else:
            pair.staff_enter_frames = 0
            if pair.staff_in_greeting_area:
                pair.staff_exit_frames += 1
                if pair.staff_exit_started_at is None:
                    pair.staff_exit_started_at = observed_at
                exit_elapsed = (
                    observed_at - pair.staff_exit_started_at
                ).total_seconds()
                grace_elapsed = (
                    self.config.greeting_area_exit_grace_seconds == 0
                    or exit_elapsed > self.config.greeting_area_exit_grace_seconds
                )
                if (
                    pair.staff_exit_frames
                    >= self.config.greeting_area_exit_stable_frames
                    and grace_elapsed
                ):
                    pair.staff_in_greeting_area = False
                    pair.within_since = None
                    pair.qualifying_confidence = None
                    if (
                        self.config.greeting_profile != "demo_relaxed"
                        and state.active_staff_track_token == staff.track_token
                    ):
                        self._cancel(
                            customer.track_token,
                            observed_at,
                            "staff_left_greeting_area",
                        )
                        return None
        staff_eligible = pair.staff_in_greeting_area or demo_confirmed
        if state.customer_in_greeting_area and staff_eligible:
            state.stage = GreetingStage.GREETING_IN_PROGRESS
            state.active_staff_track_token = staff.track_token
        active_approach = pair.approach_confirmed or demo_confirmed
        if (
            state.customer_in_greeting_area
            and staff_eligible
            and distance <= self.config.greeting_distance_metres
            and active_approach
        ):
            if pair.within_since is None:
                pair.within_since = observed_at
                pair.qualifying_confidence = current_confidence
            else:
                pair.qualifying_confidence = min(
                    pair.qualifying_confidence
                    if pair.qualifying_confidence is not None
                    else current_confidence,
                    current_confidence,
                )
            if (
                observed_at - pair.within_since
            ).total_seconds() >= self.config.greeting_duration_seconds:
                return self._confirmed_event(
                    state,
                    customer,
                    staff,
                    pair,
                    observed_at,
                    reduction,
                    demo_confirmed,
                )
        else:
            pair.within_since = None
            pair.qualifying_confidence = None
        return None

    def _confirmed_event(
        self,
        state: _GreetingState,
        customer: TrackObservation,
        staff: TrackObservation,
        pair: _GreetingPair,
        observed_at: datetime,
        reduction: float,
        demo_confirmed: bool,
    ) -> Optional[BehaviorEventInput]:
        if state.greeting_cycle_id in self._finalized_cycles:
            self._greetings.pop(customer.track_token, None)
            return None
        state.stage = GreetingStage.GREETED
        self._finalized_cycles.add(state.greeting_cycle_id)
        self._greetings.pop(customer.track_token, None)
        if pair.qualifying_confidence is None:
            raise RuntimeError("qualifying_confidence_unavailable")
        terminal_confidence = pair.qualifying_confidence
        assessment = (
            "confirmed"
            if terminal_confidence >= self.config.confirmed_confidence
            else "uncertain"
        )
        response_started_at = state.greeting_area_started_at or state.arrived_at
        return BehaviorEventInput(
            event_key=f"{state.entry_key}:greeting",
            event_type="staff_greeting",
            assessment=assessment,
            camera_id=self.camera_id,
            customer_track_id=customer.track_id,
            customer_track_token=customer.track_token,
            staff_track_id=staff.track_id,
            staff_track_token=staff.track_token,
            member_id=customer.member_id,
            member_tier=customer.member_tier,
            started_at=state.arrived_at,
            ended_at=observed_at,
            confidence=terminal_confidence,
            is_demo=self.config.greeting_profile == "demo_relaxed",
            details={
                "greeting_cycle_id": state.greeting_cycle_id,
                "staff_role_source": pair.staff_role_source,
                "staff_role_confidence": pair.staff_role_confidence,
                "greeting_confidence": terminal_confidence,
                "response_seconds": (
                    observed_at - response_started_at
                ).total_seconds(),
                "waiting_started_at": response_started_at.isoformat(),
                "trigger_source": "entry_trigger",
                "distance_mode": self.config.distance_mode,
                "staff_travel_metres": pair.maximum_travel,
                "staff_toward_customer_metres": pair.maximum_toward_customer,
                "distance_reduction_metres": reduction,
                "minimum_distance_metres": pair.minimum_distance,
                "sustained_seconds": (
                    observed_at - pair.within_since
                ).total_seconds(),
                "greeting_profile": self.config.greeting_profile,
                "trajectory_mode": (
                    "demo_intent" if demo_confirmed else "active_approach"
                ),
                "staff_in_greeting_area": pair.staff_in_greeting_area,
                "calibration_status": self.calibration_status,
            },
            evidence_start=state.arrived_at - timedelta(seconds=3),
            evidence_end=observed_at + timedelta(seconds=3),
        )

    def _expire(
        self,
        observed_at: datetime,
        customers: dict[str, TrackObservation],
    ) -> list[BehaviorEventInput]:
        expired = []
        for token, state in list(self._greetings.items()):
            tracking_fresh = (
                observed_at - state.last_seen_at
            ).total_seconds() <= self.config.tracking_grace_seconds
            if token not in customers and not tracking_fresh:
                self._cancel(token, observed_at, "customer_track_lost")
                continue
            if (
                observed_at - state.arrived_at
            ).total_seconds() < self.config.greeting_window_seconds:
                continue
            if not state.entered_greeting_area:
                self._cancel(
                    token,
                    observed_at,
                    "customer_never_entered_greeting_area",
                )
                continue
            customer = customers.get(token, state.customer)
            assessment = (
                "not_observed"
                if tracking_fresh and state.had_ground_observation
                else "uncertain"
            )
            best_pair = min(
                state.pairs.values(),
                key=lambda pair: pair.minimum_distance,
                default=None,
            )
            state.stage = GreetingStage.NOT_OBSERVED
            self._finalized_cycles.add(state.greeting_cycle_id)
            response_started_at = state.greeting_area_started_at or state.arrived_at
            expired.append(
                BehaviorEventInput(
                    event_key=f"{state.entry_key}:greeting",
                    event_type="staff_greeting",
                    assessment=assessment,
                    camera_id=self.camera_id,
                    customer_track_id=customer.track_id,
                    customer_track_token=customer.track_token,
                    staff_track_id=(
                        best_pair.staff_track_id if best_pair is not None else None
                    ),
                    staff_track_token=(
                        best_pair.staff_track_token if best_pair is not None else None
                    ),
                    member_id=customer.member_id,
                    member_tier=customer.member_tier,
                    started_at=state.arrived_at,
                    ended_at=observed_at,
                    confidence=(
                        best_pair.lifetime_confidence
                        if best_pair is not None
                        else customer.confidence
                    ),
                    is_demo=self.config.greeting_profile == "demo_relaxed",
                    details=self._expiry_details(
                        state, best_pair, customer, observed_at, response_started_at
                    ),
                    evidence_start=state.arrived_at - timedelta(seconds=3),
                    evidence_end=observed_at + timedelta(seconds=3),
                )
            )
            del self._greetings[token]
        return expired

    def _expiry_details(
        self,
        state: _GreetingState,
        pair: Optional[_GreetingPair],
        customer: TrackObservation,
        observed_at: datetime,
        response_started_at: datetime,
    ) -> dict:
        confidence = (
            pair.lifetime_confidence
            if pair is not None
            else customer.confidence
        )
        return {
            "reason": "greeting_window_expired",
            "greeting_cycle_id": state.greeting_cycle_id,
            "staff_role_source": pair.staff_role_source if pair else None,
            "staff_role_confidence": pair.staff_role_confidence if pair else None,
            "greeting_confidence": confidence,
            "response_seconds": (
                observed_at - response_started_at
            ).total_seconds(),
            "waiting_started_at": response_started_at.isoformat(),
            "trigger_source": "entry_trigger",
            "distance_mode": self.config.distance_mode,
            "minimum_distance_metres": pair.minimum_distance if pair else None,
            "distance_reduction_metres": (
                pair.initial_distance - pair.minimum_distance if pair else None
            ),
            "sustained_seconds": (
                (observed_at - pair.within_since).total_seconds()
                if pair is not None and pair.within_since is not None
                else 0.0
            ),
            "greeting_profile": self.config.greeting_profile,
            "staff_travel_metres": pair.maximum_travel if pair else 0.0,
            "staff_toward_customer_metres": (
                pair.maximum_toward_customer if pair else 0.0
            ),
            "calibration_status": self.calibration_status,
        }

    def _demo_trajectory_confirmed(self, pair: _GreetingPair) -> bool:
        return (
            self.config.greeting_profile == "demo_relaxed"
            and len(pair.observations) >= self.config.greeting_trend_minimum_samples
            and pair.maximum_travel >= self.config.greeting_staff_travel_metres
            and pair.initial_distance <= self.config.greeting_distance_metres
            and pair.observations[-1][3] <= self.config.greeting_distance_metres
        )

    def _entry_spatial_side(self, point: Point) -> EntrySpatialSide:
        if self.entrance_area is None or len(self.entrance_area.points) != 4:
            return EntrySpatialSide.UNKNOWN
        outside_left, outside_right, inside_right, inside_left = (
            self.entrance_area.points
        )
        if self.entrance_area.contains(point):
            return EntrySpatialSide.ENTRY_TRIGGER
        if not _point_projects_onto_segment_axis(
            point, outside_left, outside_right
        ):
            return EntrySpatialSide.UNKNOWN
        outside_distance = _signed_line_distance(
            outside_left, outside_right, point
        )
        inside_distance = _signed_line_distance(inside_left, inside_right, point)
        if outside_distance < -1e-9:
            return EntrySpatialSide.OUTSIDE_SIDE
        if inside_distance > 1e-9:
            return EntrySpatialSide.INSIDE_SIDE
        return EntrySpatialSide.UNKNOWN

    def _debug_state(self, token: str) -> dict[str, object]:
        state = self._greetings.get(token)
        if state is None:
            return {
                "customer_in_greeting_area": False,
                "distance_metres": None,
                "staff_travel_metres": None,
                "distance_reduction_metres": None,
                "sustained_seconds": None,
                "greeting_state": GreetingStage.NOT_ELIGIBLE.value,
            }
        pair = min(
            state.pairs.values(),
            key=lambda item: item.minimum_distance,
            default=None,
        )
        return {
            "customer_in_greeting_area": state.customer_in_greeting_area,
            "distance_metres": (
                pair.observations[-1][3]
                if pair is not None and pair.observations
                else None
            ),
            "staff_travel_metres": pair.maximum_travel if pair else None,
            "distance_reduction_metres": (
                pair.initial_distance - pair.minimum_distance if pair else None
            ),
            "sustained_seconds": (
                (state.last_seen_at - pair.within_since).total_seconds()
                if pair is not None and pair.within_since is not None
                else None
            ),
            "greeting_state": state.stage.value,
        }


# Backwards-compatible internal name for existing diagnostics/tests.
_OfficialGreetingEvaluator = OfficialGreetingCore


def _distance(left: Point, right: Point) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _signed_line_distance(start: Point, end: Point, point: Point) -> float:
    length = _distance(start, end)
    if length <= 1e-12:
        return 0.0
    return (
        (end[0] - start[0]) * (point[1] - start[1])
        - (end[1] - start[1]) * (point[0] - start[0])
    ) / length


def _point_projects_onto_segment_axis(
    point: Point, start: Point, end: Point
) -> bool:
    length_squared = (end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2
    if length_squared <= 1e-12:
        return False
    projection = (
        (point[0] - start[0]) * (end[0] - start[0])
        + (point[1] - start[1]) * (end[1] - start[1])
    ) / length_squared
    return -1e-9 <= projection <= 1 + 1e-9


def _distance_trend_is_decreasing(
    observations: deque[tuple[datetime, Point, Point, float]],
    *,
    window_samples: int,
    minimum_samples: int,
    minimum_reduction_metres: float,
    decreasing_ratio: float,
) -> bool:
    recent = list(observations)[-window_samples:]
    if len(recent) < minimum_samples:
        return False
    distances = [item[3] for item in recent]
    decreases = sum(
        current < previous - 1e-6
        for previous, current in zip(distances, distances[1:])
    )
    required = math.ceil((len(distances) - 1) * decreasing_ratio)
    return (
        distances[0] - distances[-1] >= minimum_reduction_metres
        and decreases >= required
    )
