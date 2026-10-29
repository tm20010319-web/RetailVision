"""Camera-local state machine for entry, dwell, and reach events."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Iterable, Optional, Sequence

from agent.behavior_events import BehaviorEventInput, EntryEventInput


Point = tuple[float, float]


class EntrySpatialSide(str, Enum):
    UNKNOWN = "unknown"
    OUTSIDE_SIDE = "outside_side"
    ENTRY_TRIGGER = "entry_trigger"
    INSIDE_SIDE = "inside_side"


class EntryStage(str, Enum):
    UNKNOWN = "unknown"
    OUTSIDE_STABLE = "outside_stable"
    CROSSING_IN = "crossing_in"
    INSIDE_STABLE = "inside_stable"
    ENTRY_CONFIRMED = "entry_confirmed"
    CROSSING_OUT = "crossing_out"


class ZoneState(str, Enum):
    OUTSIDE = "outside"
    ENTER_PENDING = "enter_pending"
    INSIDE = "inside"
    EXIT_PENDING = "exit_pending"


class ZoneTransitionType(str, Enum):
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    TRACK_LOST = "TRACK_LOST"


@dataclass(frozen=True)
class PolygonArea:
    area_id: str
    area_type: str
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if not self.area_id.strip():
            raise ValueError("area_id is required")
        if self.area_type not in {"entrance", "greeting", "interaction", "display"}:
            raise ValueError("unsupported area_type")
        if len(self.points) < 3:
            raise ValueError("an area requires at least three points")
        for x, y in self.points:
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in (x, y)):
                raise ValueError("area points must use normalized coordinates")
        if len(set(self.points)) != len(self.points):
            raise ValueError("area points must be unique")
        if _polygon_self_intersects(self.points):
            raise ValueError("area polygon must not self-intersect")
        signed_area = sum(
            self.points[index][0] * self.points[(index + 1) % len(self.points)][1]
            - self.points[(index + 1) % len(self.points)][0] * self.points[index][1]
            for index in range(len(self.points))
        )
        if abs(signed_area) < 1e-4:
            raise ValueError("area polygon is too small")

    def contains(self, point: Point) -> bool:
        x, y = point
        inside = False
        previous = self.points[-1]
        for current in self.points:
            x1, y1 = previous
            x2, y2 = current
            if _point_on_segment(point, previous, current):
                return True
            intersects = (y1 > y) != (y2 > y)
            if intersects:
                crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
                if x < crossing_x:
                    inside = not inside
            previous = current
        return inside


@dataclass(frozen=True)
class PosePoint:
    x: float
    y: float
    confidence: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.confidence)):
            raise ValueError("pose point values must be finite")
        if not 0 <= self.x <= 1 or not 0 <= self.y <= 1:
            raise ValueError("pose points must use normalized coordinates")
        if not 0 <= self.confidence <= 1:
            raise ValueError("pose confidence must be between 0 and 1")


@dataclass(frozen=True)
class TrackObservation:
    track_id: int
    track_token: str
    role: str
    foot_point: Point
    confidence: float
    world_point: Optional[Point] = None
    wrists: tuple[PosePoint, ...] = ()
    member_id: Optional[str] = None
    member_tier: Optional[str] = None
    visit_id: Optional[str] = None
    identity_status: Optional[str] = None
    identity_confidence: Optional[float] = None
    identity_source: Optional[str] = None
    identity_binding_level: Optional[str] = None
    evaluation_id: Optional[str] = None
    policy_version_id: Optional[str] = None
    algorithm_version: Optional[str] = None
    parameter_version: Optional[str] = None
    role_source: Optional[str] = None
    role_confidence: Optional[float] = None
    # Optional normalized person height for V2 image-relative checks.
    person_height: Optional[float] = None

    def __post_init__(self) -> None:
        if isinstance(self.track_id, bool) or self.track_id < 0:
            raise ValueError("track_id must be a non-negative integer")
        if not self.track_token.strip():
            raise ValueError("track_token is required")
        if self.role not in {"customer", "staff", "unknown", "uncertain"}:
            raise ValueError("unsupported track role")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.role_source is not None and not self.role_source.strip():
            raise ValueError("role_source must be non-empty when provided")
        if self.role_confidence is not None and not 0 <= self.role_confidence <= 1:
            raise ValueError("role_confidence must be between 0 and 1")
        if self.person_height is not None and (
            not math.isfinite(self.person_height)
            or not 0 < self.person_height <= 1
        ):
            raise ValueError("person_height must be a normalized positive value")
        if not all(0 <= value <= 1 and math.isfinite(value) for value in self.foot_point):
            raise ValueError("foot_point must use normalized coordinates")
        if self.world_point is not None and not all(
            math.isfinite(value) for value in self.world_point
        ):
            raise ValueError("world_point must be finite")


@dataclass(frozen=True)
class BehaviorMonitorConfig:
    greeting_profile: str = "legacy"
    greeting_window_seconds: float = 30.0
    greeting_distance_metres: float = 1.5
    greeting_duration_seconds: float = 2.0
    greeting_staff_travel_metres: float = 0.5
    greeting_staff_toward_customer_metres: Optional[float] = None
    minimum_distance_reduction_metres: float = 0.1
    greeting_trend_window_samples: int = 5
    greeting_trend_minimum_samples: int = 3
    greeting_trend_minimum_reduction_metres: float = 0.1
    greeting_trend_decreasing_ratio: float = 0.6
    greeting_area_exit_grace_seconds: float = 0.0
    greeting_area_enter_stable_frames: int = 1
    greeting_area_exit_stable_frames: int = 1
    dwell_minimum_seconds: float = 3.0
    tracking_grace_seconds: float = 1.0
    zone_enter_stable_frames: int = 1
    zone_exit_stable_frames: int = 2
    zone_track_lost_seconds: float = 2.0
    entrance_reset_seconds: float = 1.0
    outside_stable_frames: int = 3
    inside_stable_frames: int = 3
    crossing_timeout_seconds: float = 3.0
    rearm_outside_frames: int = 5
    minimum_crossing_displacement: float = 0.15
    reach_minimum_seconds: float = 0.3
    reach_reset_seconds: float = 1.0
    confirmed_confidence: float = 0.7
    minimum_pose_confidence: float = 0.5
    distance_mode: str = "ground_homography"

    def __post_init__(self) -> None:
        durations = (
            self.greeting_window_seconds,
            self.greeting_distance_metres,
            self.greeting_duration_seconds,
            self.greeting_staff_travel_metres,
            self.minimum_distance_reduction_metres,
            self.greeting_trend_minimum_reduction_metres,
            self.dwell_minimum_seconds,
            self.tracking_grace_seconds,
            self.zone_track_lost_seconds,
            self.entrance_reset_seconds,
            self.crossing_timeout_seconds,
            self.minimum_crossing_displacement,
            self.reach_minimum_seconds,
            self.reach_reset_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("behavior thresholds must be positive")
        if (
            not math.isfinite(self.greeting_area_exit_grace_seconds)
            or self.greeting_area_exit_grace_seconds < 0
        ):
            raise ValueError("greeting_area_exit_grace_seconds must be non-negative")
        if self.greeting_staff_toward_customer_metres is not None and (
            not math.isfinite(self.greeting_staff_toward_customer_metres)
            or self.greeting_staff_toward_customer_metres <= 0
        ):
            raise ValueError(
                "greeting_staff_toward_customer_metres must be positive when provided"
            )
        if self.greeting_profile not in {
            "legacy",
            "controlled_demo",
            "demo_relaxed",
            "production_candidate",
        }:
            raise ValueError("unsupported greeting_profile")
        for value, name in (
            (self.outside_stable_frames, "outside_stable_frames"),
            (self.inside_stable_frames, "inside_stable_frames"),
            (self.rearm_outside_frames, "rearm_outside_frames"),
            (
                self.greeting_area_enter_stable_frames,
                "greeting_area_enter_stable_frames",
            ),
            (
                self.greeting_area_exit_stable_frames,
                "greeting_area_exit_stable_frames",
            ),
            (self.zone_enter_stable_frames, "zone_enter_stable_frames"),
            (self.zone_exit_stable_frames, "zone_exit_stable_frames"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.greeting_trend_window_samples, bool)
            or not isinstance(self.greeting_trend_window_samples, int)
            or self.greeting_trend_window_samples < 3
        ):
            raise ValueError("greeting_trend_window_samples must be an integer >= 3")
        if (
            isinstance(self.greeting_trend_minimum_samples, bool)
            or not isinstance(self.greeting_trend_minimum_samples, int)
            or not 3 <= self.greeting_trend_minimum_samples
            <= self.greeting_trend_window_samples
        ):
            raise ValueError(
                "greeting_trend_minimum_samples must be from 3 to greeting_trend_window_samples"
            )
        if not 0 < self.greeting_trend_decreasing_ratio <= 1:
            raise ValueError("greeting_trend_decreasing_ratio must be between 0 and 1")
        if not 0 <= self.confirmed_confidence <= 1:
            raise ValueError("confirmed_confidence must be between 0 and 1")
        if not 0 <= self.minimum_pose_confidence <= 1:
            raise ValueError("minimum_pose_confidence must be between 0 and 1")
        if self.distance_mode != "ground_homography":
            raise ValueError("distance_mode must be ground_homography")


@dataclass
class _EntranceState:
    sequence: int = 0
    stage: EntryStage = EntryStage.UNKNOWN
    spatial_side: EntrySpatialSide = EntrySpatialSide.UNKNOWN
    entry_armed: bool = False
    ever_confirmed: bool = False
    side_frames: int = 0
    crossing_started_at: Optional[datetime] = None
    crossing_origin_progress: Optional[float] = None
    last_seen_at: Optional[datetime] = None
    pending_crossing_at: Optional[datetime] = None
    transition_reason: str = "track_started"
    positions: deque[tuple[datetime, Point]] = field(
        default_factory=lambda: deque(maxlen=64)
    )


@dataclass(frozen=True)
class ZoneRuntimeSnapshot:
    track_id: int
    track_token: str
    area_id: str
    zone_state: ZoneState
    dwell_seconds: float
    entered_at: Optional[datetime]
    last_seen_at: Optional[datetime]


@dataclass(frozen=True)
class ZoneRuntimeTransition:
    transition: ZoneTransitionType
    track_id: int
    track_token: str
    area_id: str
    occurred_at: datetime
    dwell_seconds: float


@dataclass
class _ZoneRuntimeState:
    zone_state: ZoneState
    candidate_entered_at: datetime
    entered_at: Optional[datetime]
    last_inside_at: datetime
    last_seen_at: datetime
    customer: TrackObservation
    inside_frames: int = 1
    outside_frames: int = 0


@dataclass
class _ReachState:
    sequence: int
    started_at: datetime
    last_inside_at: datetime
    customer: TrackObservation
    confidence: float
    emitted: bool = False


class BehaviorMonitor:
    """Translate per-frame observations into durable domain-event inputs."""

    def __init__(
        self,
        *,
        camera_id: str,
        entrance_area: Optional[PolygonArea] = None,
        greeting_area: Optional[PolygonArea] = None,
        interaction_areas: Sequence[PolygonArea] = (),
        display_areas: Sequence[PolygonArea] = (),
        config: Optional[BehaviorMonitorConfig] = None,
        behavior_sink: Optional[Callable[[BehaviorEventInput], object]] = None,
        calibration_status: Optional[str] = None,
        ground_calibration_available: bool = True,
    ) -> None:
        if not camera_id.strip():
            raise ValueError("camera_id is required")
        if entrance_area is not None and entrance_area.area_type != "entrance":
            raise ValueError("entrance_area must have type entrance")
        if greeting_area is not None and greeting_area.area_type != "greeting":
            raise ValueError("greeting_area must have type greeting")
        if any(area.area_type != "interaction" for area in interaction_areas):
            raise ValueError("interaction areas must have type interaction")
        if any(area.area_type != "display" for area in display_areas):
            raise ValueError("display areas must have type display")
        self.camera_id = camera_id
        self.entrance_area = entrance_area
        self.greeting_area = greeting_area
        self.interaction_areas = tuple(interaction_areas)
        self.display_areas = tuple(display_areas)
        self.config = config or BehaviorMonitorConfig()
        self.behavior_sink = behavior_sink
        self.calibration_status = calibration_status
        if not isinstance(ground_calibration_available, bool):
            raise ValueError("ground_calibration_available must be a boolean")
        self.ground_calibration_available = ground_calibration_available
        self._entrances: dict[str, _EntranceState] = {}
        self._zones: dict[tuple[str, str], _ZoneRuntimeState] = {}
        self._zone_snapshots: tuple[ZoneRuntimeSnapshot, ...] = ()
        self._zone_transitions: tuple[ZoneRuntimeTransition, ...] = ()
        self._reaches: dict[tuple[str, str], _ReachState] = {}
        self._reach_sequences: dict[tuple[str, str], int] = {}

    def process(
        self, observed_at: datetime, observations: Iterable[TrackObservation]
    ) -> tuple[list[EntryEventInput], list[BehaviorEventInput]]:
        tracks = list(observations)
        customers = {track.track_token: track for track in tracks if track.role == "customer"}
        entries: list[EntryEventInput] = []
        behaviors: list[BehaviorEventInput] = []

        entrance_tracks = {
            track.track_token: track
            for track in tracks
            if track.role in {"customer", "unknown", "uncertain"}
        }
        for track in tracks:
            if track.role == "staff":
                self._entrances.pop(track.track_token, None)
                continue
            if track.track_token not in entrance_tracks:
                continue
            entry = self._observe_entrance(observed_at, track)
            if entry is not None:
                entries.append(entry)

        zone_snapshots: dict[tuple[str, str], ZoneRuntimeSnapshot] = {}
        zone_transitions: list[ZoneRuntimeTransition] = []
        for customer in customers.values():
            zone_behaviors, snapshots, transitions = self._observe_zones(
                observed_at, customer
            )
            behaviors.extend(zone_behaviors)
            zone_snapshots.update(
                ((snapshot.track_token, snapshot.area_id), snapshot)
                for snapshot in snapshots
            )
            zone_transitions.extend(transitions)
            behaviors.extend(self._observe_reach(observed_at, customer))

        self._expire_entrances(observed_at, entrance_tracks)
        expired_behaviors, expired_snapshots, expired_transitions = self._expire_zones(
            observed_at, customers
        )
        behaviors.extend(expired_behaviors)
        zone_snapshots.update(
            ((snapshot.track_token, snapshot.area_id), snapshot)
            for snapshot in expired_snapshots
        )
        zone_transitions.extend(expired_transitions)
        self._zone_snapshots = tuple(zone_snapshots.values())
        self._zone_transitions = tuple(zone_transitions)
        self._expire_reaches(observed_at)

        for behavior in behaviors:
            if self.behavior_sink is not None:
                self.behavior_sink(behavior)
        return entries, behaviors

    def zone_runtime_diagnostics(
        self,
    ) -> tuple[tuple[ZoneRuntimeSnapshot, ...], tuple[ZoneRuntimeTransition, ...]]:
        """Return zone state and transitions produced by the latest process call."""

        return self._zone_snapshots, self._zone_transitions

    def entry_debug_state(self, track_token: str) -> dict[str, object]:
        state = self._entrances.get(track_token)
        if state is None:
            return {
                "track_token": track_token,
                "timestamp": None,
                "spatial_side": EntrySpatialSide.UNKNOWN.value,
                "entry_state": EntryStage.UNKNOWN.value,
                "entry_armed": False,
                "transition_reason": "no_active_track",
                "entry_event_id": None,
            }
        return {
            "track_token": track_token,
            "timestamp": (
                state.last_seen_at.isoformat() if state.last_seen_at is not None else None
            ),
            "spatial_side": state.spatial_side.value,
            "entry_state": state.stage.value,
            "entry_armed": state.entry_armed,
            "transition_reason": state.transition_reason,
            "entry_event_id": None,
        }

    def entry_debug_lines(self, track_token: str) -> tuple[str, ...]:
        state = self.entry_debug_state(track_token)
        side_labels = {
            EntrySpatialSide.UNKNOWN.value: "UNKNOWN",
            EntrySpatialSide.OUTSIDE_SIDE.value: "OUTSIDE",
            EntrySpatialSide.ENTRY_TRIGGER.value: "TRIGGER",
            EntrySpatialSide.INSIDE_SIDE.value: "INSIDE",
        }
        return (
            f"ENTRY SIDE: {side_labels[str(state['spatial_side'])]}",
            f"ENTRY STATE: {str(state['entry_state']).upper()}",
            f"ARMED: {'YES' if state['entry_armed'] else 'NO'}",
            f"ENTRY REASON: {state['transition_reason']}",
        )

    def _observe_entrance(
        self, observed_at: datetime, customer: TrackObservation
    ) -> Optional[EntryEventInput]:
        if self.entrance_area is None:
            return None
        state = self._entrances.setdefault(customer.track_token, _EntranceState())
        state.last_seen_at = observed_at
        state.positions.append((observed_at, customer.foot_point))
        if (
            customer.role == "customer"
            and state.pending_crossing_at is not None
        ):
            return self._confirm_entry(
                state, customer, state.pending_crossing_at
            )
        side = self._entry_spatial_side(customer.foot_point)
        if side == state.spatial_side:
            state.side_frames += 1
        else:
            state.spatial_side = side
            state.side_frames = 1

        if (
            state.crossing_started_at is not None
            and (observed_at - state.crossing_started_at).total_seconds()
            > self.config.crossing_timeout_seconds
        ):
            state.stage = EntryStage.UNKNOWN
            state.crossing_started_at = None
            state.crossing_origin_progress = None
            state.transition_reason = "crossing_timeout"

        if side == EntrySpatialSide.OUTSIDE_SIDE:
            required_frames = (
                self.config.rearm_outside_frames
                if state.ever_confirmed
                else self.config.outside_stable_frames
            )
            if state.side_frames >= required_frames:
                state.stage = EntryStage.OUTSIDE_STABLE
                state.entry_armed = True
                state.crossing_started_at = None
                state.crossing_origin_progress = None
                state.transition_reason = (
                    "rearmed_outside"
                    if state.ever_confirmed
                    else "outside_stable"
                )
            return None

        if side == EntrySpatialSide.ENTRY_TRIGGER:
            if state.stage == EntryStage.OUTSIDE_STABLE and state.entry_armed:
                state.stage = EntryStage.CROSSING_IN
                state.crossing_started_at = observed_at
                state.crossing_origin_progress = self._entry_progress(
                    customer.foot_point
                )
                state.transition_reason = "entered_trigger_from_outside"
            elif state.stage in {
                EntryStage.INSIDE_STABLE,
                EntryStage.ENTRY_CONFIRMED,
            }:
                state.stage = EntryStage.CROSSING_OUT
                state.crossing_started_at = observed_at
                state.transition_reason = "entered_trigger_from_inside"
            return None

        if side == EntrySpatialSide.INSIDE_SIDE:
            if state.stage == EntryStage.CROSSING_IN:
                if state.side_frames < self.config.inside_stable_frames:
                    return None
                origin = state.crossing_origin_progress
                displacement = (
                    self._entry_progress(customer.foot_point) - origin
                    if origin is not None
                    else 0.0
                )
                if displacement < self.config.minimum_crossing_displacement:
                    state.stage = EntryStage.INSIDE_STABLE
                    state.entry_armed = False
                    state.transition_reason = "crossing_displacement_too_small"
                    return None
                state.stage = EntryStage.ENTRY_CONFIRMED
                state.entry_armed = False
                state.ever_confirmed = True
                state.crossing_started_at = None
                state.crossing_origin_progress = None
                state.transition_reason = "outside_to_inside_confirmed"
                if customer.role != "customer":
                    state.pending_crossing_at = observed_at
                    return None
                return self._confirm_entry(state, customer, observed_at)
            if state.side_frames >= self.config.inside_stable_frames:
                if state.stage == EntryStage.CROSSING_OUT:
                    state.transition_reason = "wrong_direction_returned_inside"
                elif state.stage == EntryStage.UNKNOWN:
                    state.transition_reason = "first_seen_inside"
                state.stage = EntryStage.INSIDE_STABLE
            return None
        return None

    def _confirm_entry(
        self,
        state: _EntranceState,
        customer: TrackObservation,
        occurred_at: datetime,
    ) -> EntryEventInput:
        state.stage = EntryStage.ENTRY_CONFIRMED
        state.entry_armed = False
        state.ever_confirmed = True
        state.sequence += 1
        state.pending_crossing_at = None
        return EntryEventInput(
            entry_key=f"{customer.track_token}:entry:{state.sequence}",
            camera_id=self.camera_id,
            track_id=customer.track_id,
            track_token=customer.track_token,
            occurred_at=occurred_at,
            confidence=customer.confidence,
            member_id=customer.member_id,
            member_tier=customer.member_tier,
        )

    def _expire_entrances(
        self,
        observed_at: datetime,
        customers: dict[str, TrackObservation],
    ) -> None:
        for token, state in list(self._entrances.items()):
            if token in customers or state.last_seen_at is None:
                continue
            if (observed_at - state.last_seen_at).total_seconds() <= (
                self.config.tracking_grace_seconds
            ):
                continue
            del self._entrances[token]

    def _entry_spatial_side(self, point: Point) -> EntrySpatialSide:
        if self.entrance_area is None or len(self.entrance_area.points) != 4:
            return EntrySpatialSide.UNKNOWN
        if self.entrance_area.contains(point):
            return EntrySpatialSide.ENTRY_TRIGGER
        outside_left, outside_right, inside_right, inside_left = (
            self.entrance_area.points
        )
        if not _point_projects_onto_segment_axis(
            point, outside_left, outside_right
        ) and not _point_projects_onto_segment_axis(
            point, inside_left, inside_right
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

    def _entry_progress(self, point: Point) -> float:
        if self.entrance_area is None or len(self.entrance_area.points) != 4:
            return 0.0
        outside_left, outside_right, inside_right, inside_left = (
            self.entrance_area.points
        )
        outside_distance = _signed_line_distance(
            outside_left, outside_right, point
        )
        inside_distance = _signed_line_distance(inside_left, inside_right, point)
        denominator = outside_distance - inside_distance
        return outside_distance / denominator if abs(denominator) > 1e-9 else 0.0

    def _observe_zones(
        self, observed_at: datetime, customer: TrackObservation
    ) -> tuple[
        list[BehaviorEventInput],
        list[ZoneRuntimeSnapshot],
        list[ZoneRuntimeTransition],
    ]:
        emitted: list[BehaviorEventInput] = []
        snapshots: list[ZoneRuntimeSnapshot] = []
        transitions: list[ZoneRuntimeTransition] = []
        for area in self.interaction_areas:
            key = (customer.track_token, area.area_id)
            inside = area.contains(customer.foot_point)
            state = self._zones.get(key)
            if state is None:
                if not inside:
                    snapshots.append(self._outside_snapshot(customer, area.area_id))
                    continue
                state = _ZoneRuntimeState(
                    zone_state=ZoneState.ENTER_PENDING,
                    candidate_entered_at=observed_at,
                    entered_at=None,
                    last_inside_at=observed_at,
                    last_seen_at=observed_at,
                    customer=customer,
                )
                self._zones[key] = state
                if self.config.zone_enter_stable_frames == 1:
                    transitions.append(
                        self._confirm_zone_enter(state, area.area_id, observed_at)
                    )
            else:
                state.last_seen_at = observed_at
                state.customer = customer
                if inside:
                    state.last_inside_at = observed_at
                    state.outside_frames = 0
                    if state.zone_state == ZoneState.ENTER_PENDING:
                        state.inside_frames += 1
                        if state.inside_frames >= self.config.zone_enter_stable_frames:
                            transitions.append(
                                self._confirm_zone_enter(state, area.area_id, observed_at)
                            )
                    elif state.zone_state == ZoneState.EXIT_PENDING:
                        state.zone_state = ZoneState.INSIDE
                    else:
                        state.zone_state = ZoneState.INSIDE
                elif state.zone_state == ZoneState.ENTER_PENDING:
                    self._zones.pop(key, None)
                    snapshots.append(self._outside_snapshot(customer, area.area_id))
                    continue
                elif state.zone_state == ZoneState.INSIDE:
                    state.zone_state = ZoneState.EXIT_PENDING
                    state.outside_frames = 1
                elif state.zone_state == ZoneState.EXIT_PENDING:
                    state.outside_frames += 1

            if (
                not inside
                and state.zone_state == ZoneState.EXIT_PENDING
                and state.outside_frames >= self.config.zone_exit_stable_frames
            ):
                dwell_seconds = self._zone_dwell_seconds(state, state.last_inside_at)
                transitions.append(
                    ZoneRuntimeTransition(
                        transition=ZoneTransitionType.ZONE_EXIT,
                        track_id=state.customer.track_id,
                        track_token=state.customer.track_token,
                        area_id=area.area_id,
                        occurred_at=observed_at,
                        dwell_seconds=dwell_seconds,
                    )
                )
                event = self._finish_zone(key)
                if event is not None:
                    emitted.append(event)
                snapshots.append(self._outside_snapshot(customer, area.area_id))
                continue
            snapshots.append(self._zone_snapshot(state, area.area_id, observed_at))
        return emitted, snapshots, transitions

    def _expire_zones(
        self,
        observed_at: datetime,
        customers: dict[str, TrackObservation],
    ) -> tuple[
        list[BehaviorEventInput],
        list[ZoneRuntimeSnapshot],
        list[ZoneRuntimeTransition],
    ]:
        emitted: list[BehaviorEventInput] = []
        snapshots: list[ZoneRuntimeSnapshot] = []
        transitions: list[ZoneRuntimeTransition] = []
        for key, state in list(self._zones.items()):
            if key[0] in customers:
                continue
            if (observed_at - state.last_seen_at).total_seconds() <= (
                self.config.zone_track_lost_seconds
            ):
                snapshots.append(
                    self._zone_snapshot(
                        state,
                        key[1],
                        observed_at,
                        dwell_at=state.last_inside_at,
                    )
                )
                continue
            dwell_seconds = self._zone_dwell_seconds(state, state.last_inside_at)
            transitions.append(
                ZoneRuntimeTransition(
                    transition=ZoneTransitionType.TRACK_LOST,
                    track_id=state.customer.track_id,
                    track_token=state.customer.track_token,
                    area_id=key[1],
                    occurred_at=observed_at,
                    dwell_seconds=dwell_seconds,
                )
            )
            event = self._finish_zone(key)
            if event is not None:
                emitted.append(event)
            snapshots.append(self._outside_snapshot(state.customer, key[1]))
        return emitted, snapshots, transitions

    def _confirm_zone_enter(
        self, state: _ZoneRuntimeState, area_id: str, observed_at: datetime
    ) -> ZoneRuntimeTransition:
        state.zone_state = ZoneState.INSIDE
        state.entered_at = state.candidate_entered_at
        return ZoneRuntimeTransition(
            transition=ZoneTransitionType.ZONE_ENTER,
            track_id=state.customer.track_id,
            track_token=state.customer.track_token,
            area_id=area_id,
            occurred_at=observed_at,
            dwell_seconds=self._zone_dwell_seconds(state, observed_at),
        )

    @staticmethod
    def _zone_dwell_seconds(state: _ZoneRuntimeState, ended_at: datetime) -> float:
        if state.entered_at is None:
            return 0.0
        return max(0.0, (ended_at - state.entered_at).total_seconds())

    def _zone_snapshot(
        self,
        state: _ZoneRuntimeState,
        area_id: str,
        observed_at: datetime,
        *,
        dwell_at: Optional[datetime] = None,
    ) -> ZoneRuntimeSnapshot:
        if dwell_at is None:
            dwell_at = (
                observed_at
                if state.zone_state == ZoneState.INSIDE
                else state.last_inside_at
            )
        return ZoneRuntimeSnapshot(
            track_id=state.customer.track_id,
            track_token=state.customer.track_token,
            area_id=area_id,
            zone_state=state.zone_state,
            dwell_seconds=self._zone_dwell_seconds(state, dwell_at),
            entered_at=state.entered_at,
            last_seen_at=state.last_seen_at,
        )

    @staticmethod
    def _outside_snapshot(
        customer: TrackObservation, area_id: str
    ) -> ZoneRuntimeSnapshot:
        return ZoneRuntimeSnapshot(
            track_id=customer.track_id,
            track_token=customer.track_token,
            area_id=area_id,
            zone_state=ZoneState.OUTSIDE,
            dwell_seconds=0.0,
            entered_at=None,
            last_seen_at=None,
        )

    def _finish_zone(self, key: tuple[str, str]) -> Optional[BehaviorEventInput]:
        state = self._zones.pop(key, None)
        if state is None:
            return None
        duration = self._zone_dwell_seconds(state, state.last_inside_at)
        if duration < self.config.dwell_minimum_seconds:
            return None
        if state.entered_at is None:
            return None
        customer = state.customer
        assessment = (
            "confirmed"
            if customer.confidence >= self.config.confirmed_confidence
            else "uncertain"
        )
        return BehaviorEventInput(
            event_key=(
                f"{customer.track_token}:dwell:{key[1]}:"
                f"{state.entered_at.isoformat()}"
            ),
            event_type="merchandise_dwell",
            assessment=assessment,
            camera_id=self.camera_id,
            customer_track_id=customer.track_id,
            customer_track_token=customer.track_token,
            member_id=customer.member_id,
            member_tier=customer.member_tier,
            area_id=key[1],
            started_at=state.entered_at,
            ended_at=state.last_inside_at,
            confidence=customer.confidence,
            details={"duration_seconds": duration},
            evidence_start=state.entered_at - timedelta(seconds=3),
            evidence_end=state.last_inside_at + timedelta(seconds=3),
        )

    def _observe_reach(
        self, observed_at: datetime, customer: TrackObservation
    ) -> list[BehaviorEventInput]:
        emitted = []
        for area in self.display_areas:
            key = (customer.track_token, area.area_id)
            inside_points = [
                point for point in customer.wrists if area.contains((point.x, point.y))
            ]
            if not inside_points:
                continue
            confidence = min(
                customer.confidence,
                max(point.confidence for point in inside_points),
            )
            state = self._reaches.get(key)
            if state is None:
                sequence = self._reach_sequences.get(key, 0) + 1
                self._reach_sequences[key] = sequence
                state = _ReachState(
                    sequence=sequence,
                    started_at=observed_at,
                    last_inside_at=observed_at,
                    customer=customer,
                    confidence=confidence,
                )
                self._reaches[key] = state
            else:
                state.last_inside_at = observed_at
                state.customer = customer
                state.confidence = min(state.confidence, confidence)
            if state.emitted or (observed_at - state.started_at).total_seconds() < (
                self.config.reach_minimum_seconds
            ):
                continue
            state.emitted = True
            assessment = (
                "confirmed"
                if state.confidence >= self.config.confirmed_confidence
                and max(point.confidence for point in inside_points)
                >= self.config.minimum_pose_confidence
                else "uncertain"
            )
            emitted.append(
                BehaviorEventInput(
                    event_key=(
                        f"{customer.track_token}:reach:{area.area_id}:"
                        f"{state.sequence}"
                    ),
                    event_type="merchandise_reach",
                    assessment=assessment,
                    camera_id=self.camera_id,
                    customer_track_id=customer.track_id,
                    customer_track_token=customer.track_token,
                    member_id=customer.member_id,
                    member_tier=customer.member_tier,
                    area_id=area.area_id,
                    started_at=state.started_at,
                    ended_at=observed_at,
                    confidence=state.confidence,
                    details={"meaning": "hand_entered_display_area"},
                    evidence_start=state.started_at - timedelta(seconds=3),
                    evidence_end=observed_at + timedelta(seconds=5),
                )
            )
        return emitted

    def _expire_reaches(self, observed_at: datetime) -> None:
        for key, state in list(self._reaches.items()):
            if (observed_at - state.last_inside_at).total_seconds() >= (
                self.config.reach_reset_seconds
            ):
                del self._reaches[key]


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


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    x, y = point
    x1, y1 = start
    x2, y2 = end
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > 1e-9:
        return False
    return (
        min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9
        and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9
    )


def _polygon_self_intersects(points: tuple[Point, ...]) -> bool:
    edge_count = len(points)
    for left_index in range(edge_count):
        left_start = points[left_index]
        left_end = points[(left_index + 1) % edge_count]
        for right_index in range(left_index + 1, edge_count):
            if right_index in {
                left_index,
                (left_index + 1) % edge_count,
                (left_index - 1) % edge_count,
            }:
                continue
            right_start = points[right_index]
            right_end = points[(right_index + 1) % edge_count]
            if _segments_intersect(left_start, left_end, right_start, right_end):
                return True
    return False


def _segments_intersect(
    left_start: Point,
    left_end: Point,
    right_start: Point,
    right_end: Point,
) -> bool:
    def orientation(start: Point, end: Point, point: Point) -> float:
        return (
            (end[0] - start[0]) * (point[1] - start[1])
            - (end[1] - start[1]) * (point[0] - start[0])
        )

    values = (
        orientation(left_start, left_end, right_start),
        orientation(left_start, left_end, right_end),
        orientation(right_start, right_end, left_start),
        orientation(right_start, right_end, left_end),
    )
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    return any(
        abs(value) < 1e-9 and _point_on_segment(point, start, end)
        for value, point, start, end in (
            (values[0], right_start, left_start, left_end),
            (values[1], right_end, left_start, left_end),
            (values[2], left_start, right_start, right_end),
            (values[3], left_end, right_start, right_end),
        )
    )
