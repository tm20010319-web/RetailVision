"""Camera-local Product motion and interaction state machine."""

from __future__ import annotations

import hashlib
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from numbers import Real
from typing import Mapping, Optional

from agent.behavior.person_product_association import PersonProductAssociationStatus
from agent.behavior.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


Point = tuple[float, float]
BBox = tuple[float, float, float, float]


class ProductMotionState(str, Enum):
    STABLE = "STABLE"
    MOVING = "MOVING"
    MISSING = "MISSING"


class ProductInteractionState(str, Enum):
    IDLE = "IDLE"
    PICKUP_PENDING = "PICKUP_PENDING"
    HELD = "HELD"
    PUTDOWN_PENDING = "PUTDOWN_PENDING"


class ProductInteractionEventType(str, Enum):
    PICK_UP = "PICK_UP"
    HOLDING = "HOLDING"
    PUT_DOWN = "PUT_DOWN"


@dataclass(frozen=True)
class ProductInteractionConfig:
    frame_width: int = 1280
    frame_height: int = 720
    sample_fps: float = 5.0
    motion_window_frames: int = 5
    motion_enter_displacement_pixels: float = 12.0
    motion_exit_displacement_pixels: float = 6.0
    motion_enter_stable_frames: int = 2
    motion_exit_stable_frames: int = 3
    pickup_confirm_frames: int = 2
    putdown_confirm_frames: int = 3
    stable_baseline_min_frames: int = 5
    holding_actor_grace_seconds: float = 1.5
    minimum_holding_seconds: float = 1.0
    surface_evidence_enabled: bool = True
    pickup_surface_drop_pixels_equivalent: float = 0.12
    held_recovery_putdown_surface_overlap_min: float = 0.95
    held_recovery_putdown_confirm_frames: int = 3
    recovery_motion_guard_frames: int = 5
    minimum_pickup_confidence: float = 0.80
    minimum_putdown_confidence: float = 0.60

    def __post_init__(self) -> None:
        for value, name in (
            (self.frame_width, "frame_width"),
            (self.frame_height, "frame_height"),
            (self.motion_window_frames, "motion_window_frames"),
            (self.motion_enter_stable_frames, "motion_enter_stable_frames"),
            (self.motion_exit_stable_frames, "motion_exit_stable_frames"),
            (self.pickup_confirm_frames, "pickup_confirm_frames"),
            (self.putdown_confirm_frames, "putdown_confirm_frames"),
            (self.stable_baseline_min_frames, "stable_baseline_min_frames"),
            (
                self.held_recovery_putdown_confirm_frames,
                "held_recovery_putdown_confirm_frames",
            ),
            (self.recovery_motion_guard_frames, "recovery_motion_guard_frames"),
        ):
            _positive_int(value, name)
        for value, name in (
            (self.sample_fps, "sample_fps"),
            (
                self.motion_enter_displacement_pixels,
                "motion_enter_displacement_pixels",
            ),
            (
                self.motion_exit_displacement_pixels,
                "motion_exit_displacement_pixels",
            ),
            (self.holding_actor_grace_seconds, "holding_actor_grace_seconds"),
            (self.minimum_holding_seconds, "minimum_holding_seconds"),
            (
                self.pickup_surface_drop_pixels_equivalent,
                "pickup_surface_drop_pixels_equivalent",
            ),
        ):
            _positive_number(value, name)
        if self.motion_exit_displacement_pixels >= self.motion_enter_displacement_pixels:
            raise ValueError("motion exit displacement must be below enter displacement")
        for value, name in (
            (
                self.held_recovery_putdown_surface_overlap_min,
                "held_recovery_putdown_surface_overlap_min",
            ),
            (self.minimum_pickup_confidence, "minimum_pickup_confidence"),
            (self.minimum_putdown_confidence, "minimum_putdown_confidence"),
        ):
            _unit_interval(value, name)
        if not isinstance(self.surface_evidence_enabled, bool):
            raise ValueError("surface_evidence_enabled must be boolean")


@dataclass(frozen=True)
class ProductInteractionObservation:
    observed_at: datetime
    source_seq: int
    camera_id: str
    product_key: str
    product_type: str
    product_track_id: int
    product_track_token: str
    product_track_status: ProductTrackStatus
    product_mask_present: bool
    bbox: Optional[BBox]
    centroid: Optional[Point]
    display_state: ProductDisplayState
    surface_overlap_ratio: Optional[float]
    associated_person_track_id: Optional[int]
    associated_person_track_token: Optional[str]
    person_product_association_status: PersonProductAssociationStatus
    association_grace_active: bool

    def __post_init__(self) -> None:
        if not isinstance(self.observed_at, datetime):
            raise ValueError("observed_at must be datetime")
        _nonnegative_int(self.source_seq, "source_seq")
        _positive_int(self.product_track_id, "product_track_id")
        for value, name in (
            (self.camera_id, "camera_id"),
            (self.product_key, "product_key"),
            (self.product_type, "product_type"),
            (self.product_track_token, "product_track_token"),
        ):
            _nonempty(value, name)
        if not isinstance(self.product_track_status, ProductTrackStatus):
            raise ValueError("product_track_status must be ProductTrackStatus")
        if not isinstance(self.display_state, ProductDisplayState):
            raise ValueError("display_state must be ProductDisplayState")
        if not isinstance(
            self.person_product_association_status,
            PersonProductAssociationStatus,
        ):
            raise ValueError(
                "person_product_association_status must be PersonProductAssociationStatus"
            )
        if not isinstance(self.product_mask_present, bool):
            raise ValueError("product_mask_present must be boolean")
        if not isinstance(self.association_grace_active, bool):
            raise ValueError("association_grace_active must be boolean")
        if self.product_mask_present:
            if self.bbox is None or self.centroid is None:
                raise ValueError("observed Product requires bbox and centroid")
            _normalized_bbox(self.bbox, "bbox")
            _normalized_point(self.centroid, "centroid")
        elif self.bbox is not None or self.centroid is not None:
            raise ValueError("missing Product must not carry bbox or centroid")
        if self.surface_overlap_ratio is not None:
            _unit_interval(self.surface_overlap_ratio, "surface_overlap_ratio")
        if (self.associated_person_track_id is None) != (
            self.associated_person_track_token is None
        ):
            raise ValueError("Person track ID and token must be provided together")
        if self.associated_person_track_id is not None:
            _nonnegative_int(
                self.associated_person_track_id,
                "associated_person_track_id",
            )
            _nonempty(
                self.associated_person_track_token,
                "associated_person_track_token",
            )


@dataclass(frozen=True)
class ProductMotionDiagnostics:
    motion_state: ProductMotionState
    centroid_delta_pixels: Optional[float]
    centroid_delta_ratio: Optional[float]
    rolling_displacement_pixels: Optional[float]
    rolling_displacement_ratio: Optional[float]
    velocity_pixels_per_second: Optional[float]
    stable_frames: int
    moving_frames: int
    stable_baseline_ready: bool
    stable_baseline_frames: int
    stable_baseline_centroid: Optional[Point]
    stable_baseline_distance_pixels: Optional[float]
    recovered_from_missing: bool
    recovery_guard_active: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "motion_state": self.motion_state.value,
            "centroid_delta_pixels": self.centroid_delta_pixels,
            "centroid_delta_ratio": self.centroid_delta_ratio,
            "rolling_displacement_pixels": self.rolling_displacement_pixels,
            "rolling_displacement_ratio": self.rolling_displacement_ratio,
            "velocity_pixels_per_second": self.velocity_pixels_per_second,
            "stable_frames": self.stable_frames,
            "moving_frames": self.moving_frames,
            "stable_baseline_ready": self.stable_baseline_ready,
            "stable_baseline_frames": self.stable_baseline_frames,
            "stable_baseline_centroid": (
                None
                if self.stable_baseline_centroid is None
                else list(self.stable_baseline_centroid)
            ),
            "stable_baseline_distance_pixels": (
                self.stable_baseline_distance_pixels
            ),
            "recovered_from_missing": self.recovered_from_missing,
            "recovery_guard_active": self.recovery_guard_active,
        }


@dataclass(frozen=True)
class ProductInteractionEvent:
    event_id: str
    event_type: ProductInteractionEventType
    event_sequence: int
    observed_at: datetime
    source_seq: int
    camera_id: str
    product_key: str
    product_track_id: int
    product_track_token: str
    person_track_id: Optional[int]
    person_track_token: Optional[str]
    interaction_session_id: str
    confidence: float
    motion_state: ProductMotionState
    display_state: ProductDisplayState
    holding_duration_seconds: Optional[float]
    evidence: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "event_sequence": self.event_sequence,
            "timestamp": _utc_naive(self.observed_at).isoformat(),
            "source_seq": self.source_seq,
            "camera_id": self.camera_id,
            "product_key": self.product_key,
            "product_track_id": self.product_track_id,
            "product_track_token": self.product_track_token,
            "person_track_id": self.person_track_id,
            "person_track_token": self.person_track_token,
            "interaction_session_id": self.interaction_session_id,
            "confidence": self.confidence,
            "motion_state": self.motion_state.value,
            "display_state": self.display_state.value,
            "holding_duration_seconds": self.holding_duration_seconds,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class ProductInteractionResult:
    observation: ProductInteractionObservation
    motion: ProductMotionDiagnostics
    interaction_state: ProductInteractionState
    previous_interaction_state: ProductInteractionState
    state_changed: bool
    state_transition_reason: Optional[str]
    interaction_session_id: Optional[str]
    interaction_actor_person_track_id: Optional[int]
    interaction_actor_person_track_token: Optional[str]
    holding_duration_seconds: Optional[float]
    pickup_pending_frames: int
    putdown_pending_frames: int
    held_missing_grace: bool
    held_actor_grace: bool
    putdown_armed: bool
    interaction_terminated: bool
    interaction_terminated_reason: Optional[str]
    terminated_interaction_session_id: Optional[str]
    events: tuple[ProductInteractionEvent, ...] = ()

    def to_dict(self) -> dict[str, object]:
        observation = self.observation
        return {
            "timestamp": _utc_naive(observation.observed_at).isoformat(),
            "source_seq": observation.source_seq,
            "camera_id": observation.camera_id,
            "product_key": observation.product_key,
            "product_type": observation.product_type,
            "product_track_id": observation.product_track_id,
            "product_track_token": observation.product_track_token,
            "product_track_status": observation.product_track_status.value,
            "product_mask_present": observation.product_mask_present,
            "product_bbox": (
                None if observation.bbox is None else list(observation.bbox)
            ),
            "product_centroid": (
                None if observation.centroid is None else list(observation.centroid)
            ),
            "display_state": observation.display_state.value,
            "surface_overlap_ratio": observation.surface_overlap_ratio,
            "associated_person_track_id": observation.associated_person_track_id,
            "associated_person_track_token": (
                observation.associated_person_track_token
            ),
            "person_product_association_status": (
                observation.person_product_association_status.value
            ),
            "association_grace_active": observation.association_grace_active,
            **self.motion.to_dict(),
            "interaction_state": self.interaction_state.value,
            "previous_interaction_state": self.previous_interaction_state.value,
            "state_changed": self.state_changed,
            "state_transition_reason": self.state_transition_reason,
            "interaction_session_id": self.interaction_session_id,
            "interaction_actor_person_track_id": (
                self.interaction_actor_person_track_id
            ),
            "interaction_actor_person_track_token": (
                self.interaction_actor_person_track_token
            ),
            "holding_duration_seconds": self.holding_duration_seconds,
            "pickup_pending_frames": self.pickup_pending_frames,
            "putdown_pending_frames": self.putdown_pending_frames,
            "held_missing_grace": self.held_missing_grace,
            "held_actor_grace": self.held_actor_grace,
            "putdown_armed": self.putdown_armed,
            "interaction_terminated": self.interaction_terminated,
            "interaction_terminated_reason": self.interaction_terminated_reason,
            "terminated_interaction_session_id": (
                self.terminated_interaction_session_id
            ),
            "events": [event.to_dict() for event in self.events],
        }


@dataclass
class _ProductState:
    motion_state: ProductMotionState = ProductMotionState.STABLE
    interaction_state: ProductInteractionState = ProductInteractionState.IDLE
    recent_centroids: deque[tuple[datetime, Point]] = field(default_factory=deque)
    baseline_centroids: deque[Point] = field(default_factory=deque)
    stable_baseline_centroid: Optional[Point] = None
    stable_baseline_surface_overlap: Optional[float] = None
    stable_baseline_frames: int = 0
    stable_frames: int = 0
    moving_frames: int = 0
    motion_enter_frames: int = 0
    motion_exit_frames: int = 0
    was_missing: bool = False
    recovery_guard_frames: int = 0
    pickup_pending_frames: int = 0
    putdown_pending_frames: int = 0
    interaction_sequence: int = 0
    interaction_session_id: Optional[str] = None
    interaction_actor_person_track_id: Optional[int] = None
    interaction_actor_person_track_token: Optional[str] = None
    pickup_at: Optional[datetime] = None
    holding_started_at: Optional[datetime] = None
    actor_invalid_since: Optional[datetime] = None
    held_stable_seen: bool = False
    putdown_armed: bool = False
    recovery_stable_frames: int = 0
    completed_event_types: set[ProductInteractionEventType] = field(
        default_factory=set
    )


class ProductInteractionRuntime:
    """Evaluate Product motion and emit deterministic interaction events."""

    def __init__(self, config: Optional[ProductInteractionConfig] = None) -> None:
        self.config = config or ProductInteractionConfig()
        self._states: dict[str, _ProductState] = {}
        self._current_tokens: dict[str, str] = {}

    def process(
        self,
        observation: ProductInteractionObservation,
    ) -> ProductInteractionResult:
        previous_token = self._current_tokens.get(observation.product_key)
        if previous_token is not None and previous_token != observation.product_track_token:
            self._states.pop(previous_token, None)
        self._current_tokens[observation.product_key] = observation.product_track_token
        state = self._states.setdefault(
            observation.product_track_token,
            _ProductState(
                recent_centroids=deque(maxlen=self.config.motion_window_frames),
                baseline_centroids=deque(
                    maxlen=self.config.stable_baseline_min_frames
                ),
            ),
        )
        previous_interaction = state.interaction_state
        terminated = False
        termination_reason = None
        terminated_session_id = None
        transition_reason = None
        events: list[ProductInteractionEvent] = []

        if observation.product_track_status is ProductTrackStatus.LOST:
            motion = self._missing_motion(state)
            if state.interaction_state in {
                ProductInteractionState.HELD,
                ProductInteractionState.PUTDOWN_PENDING,
            }:
                terminated = True
                termination_reason = "product_lost"
                terminated_session_id = state.interaction_session_id
            state.interaction_state = ProductInteractionState.IDLE
            self._clear_interaction(state)
            self._states.pop(observation.product_track_token, None)
            return self._result(
                observation,
                state,
                motion,
                previous_interaction,
                transition_reason="product_lost",
                terminated=terminated,
                termination_reason=termination_reason,
                terminated_session_id=terminated_session_id,
            )

        if (
            observation.product_track_status is ProductTrackStatus.MISSING_PENDING
            or not observation.product_mask_present
        ):
            motion = self._missing_motion(state)
            if state.interaction_state is ProductInteractionState.PICKUP_PENDING:
                state.interaction_state = ProductInteractionState.IDLE
                state.pickup_pending_frames = 0
                transition_reason = "pickup_cancelled_product_missing"
            elif state.interaction_state is ProductInteractionState.PUTDOWN_PENDING:
                state.interaction_state = ProductInteractionState.HELD
                state.putdown_pending_frames = 0
                transition_reason = "putdown_cancelled_product_missing"
            return self._result(
                observation,
                state,
                motion,
                previous_interaction,
                transition_reason=transition_reason,
                held_missing_grace=(
                    state.interaction_state is ProductInteractionState.HELD
                ),
            )

        motion = self._update_motion(observation, state)
        actor_valid = self._actor_valid(observation)
        actor_matches = bool(
            actor_valid
            and observation.associated_person_track_token
            == state.interaction_actor_person_track_token
        )

        if state.interaction_state is ProductInteractionState.IDLE:
            if self._pickup_qualifies(observation, state, motion):
                state.interaction_state = ProductInteractionState.PICKUP_PENDING
                state.pickup_pending_frames = 1
                transition_reason = "pickup_candidate_started"

        elif state.interaction_state is ProductInteractionState.PICKUP_PENDING:
            if not self._pickup_qualifies(observation, state, motion):
                state.interaction_state = ProductInteractionState.IDLE
                state.pickup_pending_frames = 0
                transition_reason = "pickup_candidate_cancelled"
            else:
                state.pickup_pending_frames += 1
                if state.pickup_pending_frames >= self.config.pickup_confirm_frames:
                    confidence = self._pickup_confidence(observation, motion)
                    if confidence >= self.config.minimum_pickup_confidence:
                        self._start_interaction(observation, state)
                        state.interaction_state = ProductInteractionState.HELD
                        transition_reason = "pickup_confirmed"
                        events.extend((
                            self._event(
                                observation,
                                state,
                                ProductInteractionEventType.PICK_UP,
                                1,
                                confidence,
                                motion,
                                holding_duration=0.0,
                            ),
                            self._event(
                                observation,
                                state,
                                ProductInteractionEventType.HOLDING,
                                2,
                                confidence,
                                motion,
                                holding_duration=0.0,
                            ),
                        ))

        elif state.interaction_state in {
            ProductInteractionState.HELD,
            ProductInteractionState.PUTDOWN_PENDING,
        }:
            if actor_matches:
                state.actor_invalid_since = None
            elif observation.association_grace_active:
                if state.actor_invalid_since is None:
                    state.actor_invalid_since = _utc_naive(observation.observed_at)
            else:
                if state.actor_invalid_since is None:
                    state.actor_invalid_since = _utc_naive(observation.observed_at)
                elapsed = (
                    _utc_naive(observation.observed_at) - state.actor_invalid_since
                ).total_seconds()
                if elapsed > self.config.holding_actor_grace_seconds:
                    terminated = True
                    termination_reason = "actor_lost"
                    terminated_session_id = state.interaction_session_id
                    state.interaction_state = ProductInteractionState.IDLE
                    self._clear_interaction(state)
                    transition_reason = "interaction_terminated_actor_lost"

            if not terminated:
                transition_reason = self._advance_held_state(
                    observation,
                    state,
                    motion,
                    transition_reason,
                    events,
                )

        self._update_baseline(observation, state, motion)
        return self._result(
            observation,
            state,
            motion,
            previous_interaction,
            transition_reason=transition_reason,
            terminated=terminated,
            termination_reason=termination_reason,
            terminated_session_id=terminated_session_id,
            events=tuple(events),
            held_actor_grace=bool(state.actor_invalid_since),
        )

    def _update_motion(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
    ) -> ProductMotionDiagnostics:
        assert observation.centroid is not None
        observed_at = _utc_naive(observation.observed_at)
        recovered = state.was_missing
        if recovered:
            state.recent_centroids.clear()
            state.motion_state = ProductMotionState.STABLE
            state.motion_enter_frames = 0
            state.motion_exit_frames = 0
            state.stable_frames = 1
            state.moving_frames = 0
            state.recovery_guard_frames = self.config.recovery_motion_guard_frames
        previous = state.recent_centroids[-1] if state.recent_centroids else None
        state.recent_centroids.append((observed_at, observation.centroid))
        state.was_missing = False
        delta = None
        velocity = None
        if previous is not None:
            delta = self._distance_pixels(previous[1], observation.centroid)
            elapsed = max(1e-9, (observed_at - previous[0]).total_seconds())
            velocity = delta / elapsed
        rolling = (
            self._distance_pixels(
                state.recent_centroids[0][1],
                state.recent_centroids[-1][1],
            )
            if len(state.recent_centroids) >= 2 else 0.0
        )
        if not recovered:
            if rolling >= self.config.motion_enter_displacement_pixels:
                state.motion_enter_frames += 1
                state.motion_exit_frames = 0
            elif rolling <= self.config.motion_exit_displacement_pixels:
                state.motion_exit_frames += 1
                state.motion_enter_frames = 0
            else:
                state.motion_enter_frames = 0
                state.motion_exit_frames = 0
            if (
                state.motion_state is ProductMotionState.STABLE
                and state.motion_enter_frames >= self.config.motion_enter_stable_frames
            ):
                state.motion_state = ProductMotionState.MOVING
                state.moving_frames = 1
                state.stable_frames = 0
            elif (
                state.motion_state is ProductMotionState.MOVING
                and state.motion_exit_frames >= self.config.motion_exit_stable_frames
            ):
                state.motion_state = ProductMotionState.STABLE
                state.stable_frames = 1
                state.moving_frames = 0
            elif state.motion_state is ProductMotionState.MOVING:
                state.moving_frames += 1
                state.stable_frames = 0
            else:
                state.stable_frames += 1
                state.moving_frames = 0
        if state.recovery_guard_frames > 0:
            state.recovery_guard_frames -= 1
        baseline_distance = (
            None
            if state.stable_baseline_centroid is None
            else self._distance_pixels(
                state.stable_baseline_centroid,
                observation.centroid,
            )
        )
        diagonal = math.hypot(self.config.frame_width, self.config.frame_height)
        return ProductMotionDiagnostics(
            motion_state=state.motion_state,
            centroid_delta_pixels=delta,
            centroid_delta_ratio=None if delta is None else delta / diagonal,
            rolling_displacement_pixels=rolling,
            rolling_displacement_ratio=rolling / diagonal,
            velocity_pixels_per_second=velocity,
            stable_frames=state.stable_frames,
            moving_frames=state.moving_frames,
            stable_baseline_ready=(
                state.stable_baseline_frames
                >= self.config.stable_baseline_min_frames
                and state.stable_baseline_centroid is not None
            ),
            stable_baseline_frames=state.stable_baseline_frames,
            stable_baseline_centroid=state.stable_baseline_centroid,
            stable_baseline_distance_pixels=baseline_distance,
            recovered_from_missing=recovered,
            recovery_guard_active=state.recovery_guard_frames > 0,
        )

    def _missing_motion(self, state: _ProductState) -> ProductMotionDiagnostics:
        state.was_missing = True
        state.motion_state = ProductMotionState.MISSING
        state.recent_centroids.clear()
        state.motion_enter_frames = 0
        state.motion_exit_frames = 0
        state.stable_frames = 0
        state.moving_frames = 0
        return ProductMotionDiagnostics(
            motion_state=ProductMotionState.MISSING,
            centroid_delta_pixels=None,
            centroid_delta_ratio=None,
            rolling_displacement_pixels=None,
            rolling_displacement_ratio=None,
            velocity_pixels_per_second=None,
            stable_frames=0,
            moving_frames=0,
            stable_baseline_ready=(
                state.stable_baseline_frames
                >= self.config.stable_baseline_min_frames
                and state.stable_baseline_centroid is not None
            ),
            stable_baseline_frames=state.stable_baseline_frames,
            stable_baseline_centroid=state.stable_baseline_centroid,
            stable_baseline_distance_pixels=None,
            recovered_from_missing=False,
            recovery_guard_active=False,
        )

    def _pickup_qualifies(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
        motion: ProductMotionDiagnostics,
    ) -> bool:
        return bool(
            observation.product_track_status is ProductTrackStatus.ACTIVE
            and observation.product_mask_present
            and motion.motion_state is ProductMotionState.MOVING
            and motion.stable_baseline_ready
            and not motion.recovery_guard_active
            and self._actor_valid(observation)
            and self._pickup_confidence(observation, motion)
            >= self.config.minimum_pickup_confidence
        )

    def _pickup_confidence(
        self,
        observation: ProductInteractionObservation,
        motion: ProductMotionDiagnostics,
    ) -> float:
        rolling = float(motion.rolling_displacement_pixels or 0.0)
        baseline = float(motion.stable_baseline_distance_pixels or 0.0)
        motion_score = min(
            1.0,
            rolling / (self.config.motion_enter_displacement_pixels * 2.0),
        )
        baseline_score = min(
            1.0,
            baseline / (self.config.motion_enter_displacement_pixels * 2.0),
        )
        if (
            not self.config.surface_evidence_enabled
            or observation.surface_overlap_ratio is None
            or motion.stable_baseline_centroid is None
        ):
            surface_score = 1.0
        else:
            state = self._states[observation.product_track_token]
            baseline_surface = state.stable_baseline_surface_overlap
            if baseline_surface is None:
                surface_score = 1.0
            else:
                surface_drop = baseline_surface - observation.surface_overlap_ratio
                if surface_drop < self.config.pickup_surface_drop_pixels_equivalent:
                    surface_score = 0.0
                else:
                    surface_score = max(
                        0.0,
                        min(
                            1.0,
                            surface_drop
                            / self.config.pickup_surface_drop_pixels_equivalent,
                        ),
                    )
        actor_score = 1.0 if self._actor_valid(observation) else 0.0
        return max(
            0.0,
            min(
                1.0,
                0.30 * motion_score
                + 0.20 * baseline_score
                + 0.20 * actor_score
                + 0.30 * surface_score,
            ),
        )

    def _advance_held_state(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
        motion: ProductMotionDiagnostics,
        transition_reason: Optional[str],
        events: list[ProductInteractionEvent],
    ) -> Optional[str]:
        now = _utc_naive(observation.observed_at)
        held_seconds = (
            0.0
            if state.holding_started_at is None
            else max(0.0, (now - state.holding_started_at).total_seconds())
        )
        if state.interaction_state is ProductInteractionState.HELD:
            if motion.recovered_from_missing:
                state.recovery_stable_frames = 1
            elif state.recovery_stable_frames > 0:
                if (
                    motion.motion_state is ProductMotionState.STABLE
                    and observation.surface_overlap_ratio is not None
                    and observation.surface_overlap_ratio
                    >= self.config.held_recovery_putdown_surface_overlap_min
                ):
                    state.recovery_stable_frames += 1
                    if (
                        state.recovery_stable_frames
                        >= self.config.held_recovery_putdown_confirm_frames
                        and held_seconds >= self.config.minimum_holding_seconds
                    ):
                        state.putdown_armed = True
                        state.interaction_state = ProductInteractionState.PUTDOWN_PENDING
                        state.putdown_pending_frames = 1
                        return "putdown_candidate_after_recovery"
                else:
                    state.recovery_stable_frames = 0
            if motion.motion_state is ProductMotionState.STABLE:
                state.held_stable_seen = True
                if state.putdown_armed:
                    state.interaction_state = ProductInteractionState.PUTDOWN_PENDING
                    state.putdown_pending_frames = 1
                    return "putdown_candidate_started"
            elif (
                motion.motion_state is ProductMotionState.MOVING
                and state.held_stable_seen
                and held_seconds >= self.config.minimum_holding_seconds
            ):
                state.putdown_armed = True
        elif state.interaction_state is ProductInteractionState.PUTDOWN_PENDING:
            if motion.motion_state is ProductMotionState.MOVING:
                state.interaction_state = ProductInteractionState.HELD
                state.putdown_pending_frames = 0
                state.putdown_armed = True
                return "putdown_candidate_cancelled_motion"
            if motion.motion_state is ProductMotionState.STABLE:
                state.putdown_pending_frames += 1
                if state.putdown_pending_frames >= self.config.putdown_confirm_frames:
                    confidence = self._putdown_confidence(observation, state, motion)
                    if confidence >= self.config.minimum_putdown_confidence:
                        event = self._event(
                            observation,
                            state,
                            ProductInteractionEventType.PUT_DOWN,
                            3,
                            confidence,
                            motion,
                            holding_duration=held_seconds,
                        )
                        events.append(event)
                        state.interaction_state = ProductInteractionState.IDLE
                        self._clear_interaction(state)
                        state.baseline_centroids.clear()
                        state.stable_baseline_frames = 0
                        return "putdown_confirmed"
        return transition_reason

    def _putdown_confidence(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
        motion: ProductMotionDiagnostics,
    ) -> float:
        stable_score = min(
            1.0,
            max(1, motion.stable_frames) / self.config.putdown_confirm_frames,
        )
        actor_score = (
            1.0
            if observation.associated_person_track_token
            == state.interaction_actor_person_track_token
            else 0.5 if observation.association_grace_active else 0.0
        )
        surface_score = (
            0.5
            if observation.surface_overlap_ratio is None
            else float(observation.surface_overlap_ratio)
        )
        return max(
            0.0,
            min(
                1.0,
                0.45 * stable_score
                + 0.20 * actor_score
                + 0.20 * surface_score
                + 0.15 * float(state.putdown_armed),
            ),
        )

    def _update_baseline(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
        motion: ProductMotionDiagnostics,
    ) -> None:
        if (
            state.interaction_state is not ProductInteractionState.IDLE
            or motion.motion_state is not ProductMotionState.STABLE
            or (motion.rolling_displacement_pixels or 0.0)
            > self.config.motion_exit_displacement_pixels
            or observation.centroid is None
            or motion.recovered_from_missing
        ):
            return
        state.baseline_centroids.append(observation.centroid)
        state.stable_baseline_frames += 1
        state.stable_baseline_centroid = (
            sum(point[0] for point in state.baseline_centroids)
            / len(state.baseline_centroids),
            sum(point[1] for point in state.baseline_centroids)
            / len(state.baseline_centroids),
        )
        if observation.surface_overlap_ratio is not None:
            state.stable_baseline_surface_overlap = observation.surface_overlap_ratio

    def _start_interaction(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
    ) -> None:
        state.interaction_sequence += 1
        state.interaction_session_id = (
            f"interaction:{observation.camera_id}:"
            f"{observation.product_track_token}:{state.interaction_sequence}"
        )
        state.interaction_actor_person_track_id = (
            observation.associated_person_track_id
        )
        state.interaction_actor_person_track_token = (
            observation.associated_person_track_token
        )
        state.pickup_at = _utc_naive(observation.observed_at)
        state.holding_started_at = state.pickup_at
        state.pickup_pending_frames = 0
        state.putdown_pending_frames = 0
        state.actor_invalid_since = None
        state.held_stable_seen = False
        state.putdown_armed = False
        state.recovery_stable_frames = 0
        state.completed_event_types.clear()

    def _clear_interaction(self, state: _ProductState) -> None:
        state.interaction_session_id = None
        state.interaction_actor_person_track_id = None
        state.interaction_actor_person_track_token = None
        state.pickup_at = None
        state.holding_started_at = None
        state.actor_invalid_since = None
        state.pickup_pending_frames = 0
        state.putdown_pending_frames = 0
        state.held_stable_seen = False
        state.putdown_armed = False
        state.recovery_stable_frames = 0
        state.completed_event_types.clear()

    def _event(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
        event_type: ProductInteractionEventType,
        event_sequence: int,
        confidence: float,
        motion: ProductMotionDiagnostics,
        *,
        holding_duration: Optional[float],
    ) -> ProductInteractionEvent:
        assert state.interaction_session_id is not None
        payload = (
            f"{observation.camera_id}|{observation.product_track_token}|"
            f"{state.interaction_session_id}|{event_type.value}|{event_sequence}"
        )
        event_id = "product-interaction:" + hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()[:24]
        state.completed_event_types.add(event_type)
        return ProductInteractionEvent(
            event_id=event_id,
            event_type=event_type,
            event_sequence=event_sequence,
            observed_at=_utc_naive(observation.observed_at),
            source_seq=observation.source_seq,
            camera_id=observation.camera_id,
            product_key=observation.product_key,
            product_track_id=observation.product_track_id,
            product_track_token=observation.product_track_token,
            person_track_id=state.interaction_actor_person_track_id,
            person_track_token=state.interaction_actor_person_track_token,
            interaction_session_id=state.interaction_session_id,
            confidence=confidence,
            motion_state=motion.motion_state,
            display_state=observation.display_state,
            holding_duration_seconds=holding_duration,
            evidence={
                **motion.to_dict(),
                "surface_overlap_ratio": observation.surface_overlap_ratio,
                "association_status": (
                    observation.person_product_association_status.value
                ),
                "association_grace_active": observation.association_grace_active,
            },
        )

    def _result(
        self,
        observation: ProductInteractionObservation,
        state: _ProductState,
        motion: ProductMotionDiagnostics,
        previous_interaction: ProductInteractionState,
        *,
        transition_reason: Optional[str] = None,
        terminated: bool = False,
        termination_reason: Optional[str] = None,
        terminated_session_id: Optional[str] = None,
        events: tuple[ProductInteractionEvent, ...] = (),
        held_missing_grace: bool = False,
        held_actor_grace: bool = False,
    ) -> ProductInteractionResult:
        holding_duration = (
            None
            if state.holding_started_at is None
            else max(
                0.0,
                (
                    _utc_naive(observation.observed_at)
                    - state.holding_started_at
                ).total_seconds(),
            )
        )
        return ProductInteractionResult(
            observation=observation,
            motion=motion,
            interaction_state=state.interaction_state,
            previous_interaction_state=previous_interaction,
            state_changed=state.interaction_state is not previous_interaction,
            state_transition_reason=transition_reason,
            interaction_session_id=state.interaction_session_id,
            interaction_actor_person_track_id=(
                state.interaction_actor_person_track_id
            ),
            interaction_actor_person_track_token=(
                state.interaction_actor_person_track_token
            ),
            holding_duration_seconds=holding_duration,
            pickup_pending_frames=state.pickup_pending_frames,
            putdown_pending_frames=state.putdown_pending_frames,
            held_missing_grace=held_missing_grace,
            held_actor_grace=held_actor_grace,
            putdown_armed=state.putdown_armed,
            interaction_terminated=terminated,
            interaction_terminated_reason=termination_reason,
            terminated_interaction_session_id=terminated_session_id,
            events=events,
        )

    def _actor_valid(self, observation: ProductInteractionObservation) -> bool:
        return bool(
            observation.person_product_association_status
            is PersonProductAssociationStatus.ASSOCIATED
            and observation.associated_person_track_token
            and observation.associated_person_track_id is not None
        )

    def _distance_pixels(self, left: Point, right: Point) -> float:
        return math.hypot(
            (left[0] - right[0]) * self.config.frame_width,
            (left[1] - right[1]) * self.config.frame_height,
        )


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _normalized_bbox(value: BBox, name: str) -> None:
    if len(value) != 4 or not all(_finite_number(item) for item in value):
        raise ValueError(f"{name} must contain four finite coordinates")
    x1, y1, x2, y2 = (float(item) for item in value)
    if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
        raise ValueError(f"{name} must use normalized coordinates")


def _normalized_point(value: Point, name: str) -> None:
    if len(value) != 2 or not all(
        _finite_number(item) and 0 <= float(item) <= 1 for item in value
    ):
        raise ValueError(f"{name} must use normalized coordinates")


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _positive_number(value: object, name: str) -> None:
    if not _finite_number(value) or float(value) <= 0:
        raise ValueError(f"{name} must be positive")


def _unit_interval(value: object, name: str) -> None:
    if not _finite_number(value) or not 0 <= float(value) <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
