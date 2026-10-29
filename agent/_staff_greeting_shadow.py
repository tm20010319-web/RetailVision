"""Internal Shadow evaluation and persistence for Staff Greeting."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy.exc import IntegrityError

from agent.behavior_events import BehaviorEventInput, EntryEventInput
from agent.behavior_monitor import Point, PolygonArea, TrackObservation
from agent.models import (
    BehaviorEvent,
    CustomerEntryEvent,
    EvidenceClip,
    GreetingCycle,
    GreetingEvaluation,
    new_uuid,
)

SHADOW_EVALUATOR_VERSION = "greeting-v2.0"
OFFICIAL_EVALUATOR_VERSION = "greeting-v1"
SHADOW_MODE = "shadow"
OFFICIAL_MODE = "official"
PATHS = {"active_approach", "already_close", "accompanying"}
DECISIONS = {"would_confirm", "uncertain", "not_observed", "cancelled", "confirmed"}
CYCLE_OUTCOMES = {"confirmed", "uncertain", "not_observed", "cancelled"}


@dataclass(frozen=True)
class GreetingShadowConfig:
    """Configurable thresholds; all values are saved with every evaluation."""

    evaluator_version: str = SHADOW_EVALUATOR_VERSION
    greeting_window_seconds: float = 30.0
    tracking_grace_seconds: float = 1.0
    greeting_area_stable_frames: int = 3
    proximity_distance_metres: float = 1.5
    image_proximity_person_heights: float = 3.0
    minimum_proximity_seconds: float = 1.0
    active_staff_travel_metres: float = 0.5
    active_toward_customer_metres: float = 0.3
    active_distance_reduction_metres: float = 0.1
    accompanying_min_travel_metres: float = 0.35
    accompanying_direction_cosine: float = 0.65
    accompanying_distance_stability_metres: float = 0.25
    candidate_threshold: float = 0.45
    confirm_threshold: float = 0.65

    def __post_init__(self) -> None:
        positive = (
            self.greeting_window_seconds, self.tracking_grace_seconds,
            self.proximity_distance_metres, self.image_proximity_person_heights,
            self.minimum_proximity_seconds, self.active_staff_travel_metres,
            self.active_toward_customer_metres, self.active_distance_reduction_metres,
            self.accompanying_min_travel_metres, self.accompanying_distance_stability_metres,
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("Greeting V2 thresholds must be positive")
        if (isinstance(self.greeting_area_stable_frames, bool)
                or not isinstance(self.greeting_area_stable_frames, int)
                or self.greeting_area_stable_frames < 1):
            raise ValueError("greeting_area_stable_frames must be positive")
        if not 0 <= self.accompanying_direction_cosine <= 1:
            raise ValueError("accompanying_direction_cosine must be between 0 and 1")
        if not 0 <= self.candidate_threshold <= 1 or not 0 <= self.confirm_threshold <= 1:
            raise ValueError("score thresholds must be between 0 and 1")
        if self.confirm_threshold < self.candidate_threshold:
            raise ValueError("confirm_threshold must be >= candidate_threshold")


@dataclass(frozen=True)
class GreetingEvaluationInput:
    entry_key: str
    evaluator_version: str
    mode: str
    candidate_path: Optional[str]
    decision: str
    interaction_score: Optional[float]
    track_quality_score: Optional[float]
    raw_metrics: dict[str, Any]
    score_breakdown: dict[str, Any]
    calibration_mode: Optional[str]
    calibration_consistency: Optional[str]
    waiting_started_at: Optional[datetime]
    settled_at: datetime
    reason: Optional[str] = None
    candidate_threshold: Optional[float] = None
    confirm_threshold: Optional[float] = None
    response_seconds: Optional[float] = None
    staff_track_id: Optional[int] = None
    staff_track_token: Optional[str] = None
    cycle_outcome: Optional[str] = None
    official_behavior_event_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.entry_key.strip() or self.mode not in {SHADOW_MODE, OFFICIAL_MODE}:
            raise ValueError("entry_key and a valid mode are required")
        if self.candidate_path is not None and self.candidate_path not in PATHS:
            raise ValueError("unsupported candidate_path")
        if self.decision not in DECISIONS:
            raise ValueError("unsupported evaluation decision")
        if self.cycle_outcome is not None and self.cycle_outcome not in CYCLE_OUTCOMES:
            raise ValueError("unsupported cycle outcome")
        if self.waiting_started_at is not None and self.waiting_started_at > self.settled_at:
            raise ValueError("waiting_started_at cannot be after settled_at")


@dataclass(frozen=True)
class GreetingShadowResult:
    entry_key: str
    camera_id: str
    customer_track_id: int
    customer_track_token: str
    candidate_path: Optional[str]
    decision: str
    cycle_outcome: str
    interaction_score: Optional[float]
    track_quality_score: Optional[float]
    raw_metrics: dict[str, Any]
    score_breakdown: dict[str, Any]
    calibration_mode: Optional[str]
    calibration_consistency: Optional[str]
    waiting_started_at: Optional[datetime]
    settled_at: datetime
    reason: Optional[str]
    candidate_threshold: float
    confirm_threshold: float
    response_seconds: Optional[float] = None
    staff_track_id: Optional[int] = None
    staff_track_token: Optional[str] = None


@dataclass
class _Pair:
    staff: TrackObservation
    first_at: datetime
    initial_world: Optional[float]
    minimum_world: Optional[float]
    initial_image: Optional[float]
    minimum_image: Optional[float]
    consistency: str
    staff_origin: Point
    customer_origin: Point
    last_staff: Point
    last_customer: Point
    staff_travel: float = 0.0
    staff_toward: float = 0.0
    customer_travel: float = 0.0
    direction_cosines: list[float] = field(default_factory=list)
    area_frames: int = 0
    frames: int = 0
    confidence: float = 1.0
    role_confidence: float = 1.0
    proximity_started: Optional[datetime] = None
    last_proximity: Optional[datetime] = None
    raw_proximity: float = 0.0
    last_distance: Optional[float] = None
    reductions: list[float] = field(default_factory=list)
    had_world: bool = False
    had_image: bool = False
    spatial_mode: str = "uninitialized"
    last_spatial_observed_at: Optional[datetime] = None

    @property
    def reduction(self) -> Optional[float]:
        if self.initial_world is not None and self.minimum_world is not None:
            return self.initial_world - self.minimum_world
        if self.initial_image is not None and self.minimum_image is not None:
            return self.initial_image - self.minimum_image
        return None


@dataclass
class _Cycle:
    entry: EntryEventInput
    created_at: datetime
    customer: TrackObservation
    area_frames: int = 0
    waiting_started: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    pairs: dict[str, _Pair] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.last_seen = self.created_at


class _GreetingShadowEvaluator:
    """Stateful deterministic evaluator; persistence is deliberately separate."""

    def __init__(self, *, camera_id: str, greeting_area: Optional[PolygonArea],
                 config: Optional[GreetingShadowConfig] = None,
                 calibration_status: Optional[str] = None) -> None:
        if not camera_id.strip():
            raise ValueError("camera_id is required")
        self.camera_id = camera_id
        self.greeting_area = greeting_area
        self.config = config or GreetingShadowConfig()
        self.calibration_status = calibration_status
        self._cycles: dict[str, _Cycle] = {}

    def observe(self, observed_at: datetime,
                observations: Iterable[TrackObservation],
                new_entries: Iterable[EntryEventInput] = ()) -> tuple[GreetingShadowResult, ...]:
        tracks = list(observations)
        customers = {t.track_token: t for t in tracks if t.role == "customer"}
        staff = {t.track_token: t for t in tracks if t.role == "staff"}
        for entry in new_entries:
            if entry.entry_key not in self._cycles:
                customer = customers.get(entry.track_token) or TrackObservation(
                    track_id=entry.track_id, track_token=entry.track_token, role="customer",
                    foot_point=(0.0, 0.0), confidence=entry.confidence,
                    member_id=entry.member_id, member_tier=entry.member_tier,
                )
                self._cycles[entry.entry_key] = _Cycle(entry, entry.occurred_at, customer)
        settled = []
        for key, cycle in list(self._cycles.items()):
            customer = customers.get(cycle.entry.track_token)
            if customer is None:
                if (observed_at - (cycle.last_seen or cycle.created_at)).total_seconds() > self.config.tracking_grace_seconds:
                    settled.append(self._settle(cycle, observed_at, "customer_track_lost", force_cancel=True))
                    del self._cycles[key]
                continue
            cycle.customer = customer
            cycle.last_seen = observed_at
            inside = self.greeting_area is not None and self.greeting_area.contains(customer.foot_point)
            if inside:
                cycle.area_frames += 1
                if cycle.waiting_started is None and cycle.area_frames >= self.config.greeting_area_stable_frames:
                    cycle.waiting_started = observed_at
            else:
                if cycle.waiting_started is not None:
                    settled.append(self._settle(cycle, observed_at, "customer_left_greeting_area"))
                    del self._cycles[key]
                elif (observed_at - cycle.created_at).total_seconds() >= self.config.greeting_window_seconds:
                    settled.append(self._settle(cycle, observed_at, "customer_never_entered_greeting_area", force_cancel=True))
                    del self._cycles[key]
                continue
            if cycle.waiting_started is not None:
                for item in staff.values():
                    self._observe_pair(cycle, item, observed_at)
                candidate = self._candidate(cycle)
                if candidate and candidate[0] == "active_approach":
                    pair, score, breakdown = candidate[1:]
                    if pair.raw_proximity >= self.config.minimum_proximity_seconds and score >= self.config.confirm_threshold:
                        settled.append(self._settle(cycle, observed_at, "active_approach_confirmed", pair=pair, score=score, breakdown=breakdown, decision="would_confirm"))
                        del self._cycles[key]
                        continue
            if (observed_at - cycle.created_at).total_seconds() >= self.config.greeting_window_seconds:
                settled.append(self._settle(cycle, observed_at, "greeting_window_expired"))
                del self._cycles[key]
        return tuple(settled)

    def cancel_all(self, observed_at: datetime, reason: str) -> tuple[GreetingShadowResult, ...]:
        if not isinstance(observed_at, datetime) or not isinstance(reason, str) or not reason.strip():
            raise ValueError("observed_at and reason are required")
        results = [self._settle(c, observed_at, reason, force_cancel=True) for c in self._cycles.values()]
        self._cycles.clear()
        return tuple(results)

    def _observe_pair(self, cycle: _Cycle, staff: TrackObservation, observed_at: datetime) -> None:
        customer = cycle.customer
        world = _world_distance(customer.world_point, staff.world_point)
        image = _image_distance(customer, staff)
        consistency = _distance_consistency(world, image, self.config)
        available_world = world is not None
        available_image = image is not None
        pair = cycle.pairs.get(staff.track_token)
        if pair is None:
            mode = "world" if available_world else "image"
            staff_point = staff.world_point if mode == "world" else staff.foot_point
            customer_point = customer.world_point if mode == "world" else customer.foot_point
            pair = _Pair(
                staff=staff, first_at=observed_at,
                initial_world=world, minimum_world=world,
                initial_image=image, minimum_image=image,
                consistency=consistency, staff_origin=staff_point,
                customer_origin=customer_point, last_staff=staff_point,
                last_customer=customer_point, confidence=min(customer.confidence, staff.confidence),
                role_confidence=staff.role_confidence if staff.role_confidence is not None else staff.confidence,
                spatial_mode=mode,
                last_spatial_observed_at=observed_at,
            )
            cycle.pairs[staff.track_token] = pair
        else:
            if pair.last_spatial_observed_at is not None and (
                observed_at - pair.last_spatial_observed_at
            ).total_seconds() > self.config.tracking_grace_seconds:
                pair.raw_proximity = 0.0
                pair.proximity_started = None
                pair.last_proximity = None
            next_mode = "world" if available_world else "image"
            if next_mode != pair.spatial_mode and next_mode == "world":
                # Rebase all spatial evidence when upgrading from image to world.
                pair.spatial_mode = "world"
                pair.staff_origin = staff.world_point  # type: ignore[assignment]
                pair.customer_origin = customer.world_point  # type: ignore[assignment]
                pair.last_staff = staff.world_point  # type: ignore[assignment]
                pair.last_customer = customer.world_point  # type: ignore[assignment]
                pair.initial_world = world
                pair.minimum_world = world
                pair.last_distance = world
                pair.reductions.clear()
                pair.staff_travel = 0.0
                pair.customer_travel = 0.0
                pair.staff_toward = 0.0
                pair.direction_cosines.clear()
                pair.raw_proximity = 0.0
                pair.proximity_started = None
                pair.last_proximity = None
            pair.last_spatial_observed_at = observed_at
        if pair.spatial_mode == "world" and not available_world:
            return
        staff_point = staff.world_point if pair.spatial_mode == "world" else staff.foot_point
        customer_point = customer.world_point if pair.spatial_mode == "world" else customer.foot_point
        pair.staff = staff
        pair.frames += 1
        pair.confidence = min(pair.confidence, customer.confidence, staff.confidence)
        pair.role_confidence = min(pair.role_confidence, staff.role_confidence if staff.role_confidence is not None else staff.confidence)
        pair.consistency = _merge_consistency(pair.consistency, consistency)
        if world is not None:
            pair.had_world = True
            pair.minimum_world = min(pair.minimum_world if pair.minimum_world is not None else world, world)
        if image is not None and pair.spatial_mode == "image":
            pair.had_image = True
            pair.minimum_image = min(pair.minimum_image if pair.minimum_image is not None else image, image)
        staff_delta = _sub(staff_point, pair.last_staff)
        customer_delta = _sub(customer_point, pair.last_customer)
        pair.staff_travel = max(pair.staff_travel, _distance(pair.staff_origin, staff_point))
        pair.customer_travel = max(pair.customer_travel, _distance(pair.customer_origin, customer_point))
        pair.staff_toward = max(pair.staff_toward, _toward(pair, staff_delta))
        if _length(staff_delta) > 1e-9 and _length(customer_delta) > 1e-9:
            pair.direction_cosines.append(_cosine(staff_delta, customer_delta))
        pair.last_staff, pair.last_customer = staff_point, customer_point
        staff_inside = self.greeting_area is not None and self.greeting_area.contains(staff.foot_point)
        if staff_inside:
            pair.area_frames += 1
        current = world if world is not None else image
        if current is not None and pair.last_distance is not None:
            pair.reductions.append(pair.last_distance - current)
        pair.last_distance = current
        if staff_inside and _is_proximity(world, image, pair.consistency, self.config):
            if pair.proximity_started is None:
                pair.proximity_started = observed_at
            if pair.last_proximity is not None:
                elapsed = max(0.0, (observed_at - pair.last_proximity).total_seconds())
                if elapsed <= self.config.tracking_grace_seconds:
                    pair.raw_proximity += elapsed
                else:
                    pair.proximity_started = observed_at
            pair.last_proximity = observed_at

    def _candidate(self, cycle: _Cycle):
        options = []
        for pair in cycle.pairs.values():
            path = self._path(pair)
            if path is None:
                continue
            score, breakdown = self._score(pair, path)
            options.append((path, pair, score, breakdown))
        return max(options, key=lambda item: item[2]) if options else None

    def _path(self, pair: _Pair) -> Optional[str]:
        reduction = pair.reduction or 0.0
        initial = pair.initial_world if pair.initial_world is not None else pair.initial_image
        minimum = pair.minimum_world if pair.minimum_world is not None else pair.minimum_image
        threshold = self.config.proximity_distance_metres if pair.initial_world is not None else self.config.image_proximity_person_heights
        active = (
            initial is not None and minimum is not None and initial > threshold and minimum <= threshold
            and pair.staff_travel >= self.config.active_staff_travel_metres
            and pair.staff_toward >= self.config.active_toward_customer_metres
            and reduction >= self.config.active_distance_reduction_metres
            and any(value > 0 for value in pair.reductions)
        )
        if active:
            return "active_approach"
        initial_close = (
            pair.initial_world is not None and pair.initial_world <= self.config.proximity_distance_metres
        ) or (
            pair.initial_world is None and pair.initial_image is not None
            and pair.initial_image <= self.config.image_proximity_person_heights
        )
        direction = sum(pair.direction_cosines) / len(pair.direction_cosines) if pair.direction_cosines else 0.0
        if initial_close and pair.staff_travel >= self.config.accompanying_min_travel_metres and pair.customer_travel >= self.config.accompanying_min_travel_metres and direction >= self.config.accompanying_direction_cosine and abs(reduction) <= self.config.accompanying_distance_stability_metres:
            return "accompanying"
        if initial_close and pair.raw_proximity >= self.config.minimum_proximity_seconds:
            return "already_close"
        return None

    def _score(self, pair: _Pair, path: str) -> tuple[float, dict[str, float]]:
        proximity = min(1.0, pair.raw_proximity / self.config.minimum_proximity_seconds)
        area = min(1.0, pair.area_frames / max(pair.frames, 1))
        track = max(0.0, min(1.0, pair.confidence))
        role = max(0.0, min(1.0, pair.role_confidence))
        motion = 1.0 if path == "active_approach" else (0.75 if path == "accompanying" else 0.5)
        breakdown = {"proximity": proximity, "area_overlap": area, "track_quality": track, "staff_role": role, "path_evidence": motion}
        return 0.35 * proximity + 0.2 * area + 0.2 * track + 0.15 * role + 0.1 * motion, breakdown

    def _settle(self, cycle: _Cycle, settled_at: datetime, reason: str, *, force_cancel: bool = False,
                pair: Optional[_Pair] = None, score: Optional[float] = None,
                breakdown: Optional[dict[str, float]] = None, decision: Optional[str] = None) -> GreetingShadowResult:
        candidate = self._candidate(cycle) if pair is None else (self._path(pair), pair, score, breakdown or {})
        if candidate:
            path, selected, candidate_score, candidate_breakdown = candidate
        else:
            path, selected, candidate_score, candidate_breakdown = None, None, None, {}
        if force_cancel:
            decision, outcome = "cancelled", "cancelled"
        elif decision is None and path is not None and candidate_score is not None and candidate_score >= self.config.candidate_threshold:
            decision, outcome = "uncertain", "uncertain"
        elif decision is None:
            decision, outcome = "not_observed", "not_observed"
        else:
            outcome = "confirmed" if decision == "would_confirm" else "uncertain"
        # A world/image disagreement, or image-only fallback, may retain a
        # Shadow candidate but never simulate a high-precision confirmation.
        if decision == "would_confirm" and selected is not None and (
            selected.consistency == "conflict" or not selected.had_world
        ):
            decision, outcome = "uncertain", "uncertain"
        response = None
        if decision == "would_confirm" and cycle.waiting_started is not None:
            response = max(0.0, (settled_at - cycle.waiting_started).total_seconds())
        return GreetingShadowResult(
            entry_key=cycle.entry.entry_key, camera_id=self.camera_id,
            customer_track_id=cycle.entry.track_id, customer_track_token=cycle.entry.track_token,
            candidate_path=path, decision=decision, cycle_outcome=outcome,
            interaction_score=candidate_score, track_quality_score=selected.confidence if selected else None,
            raw_metrics=_metrics(selected), score_breakdown=candidate_breakdown,
            calibration_mode=_calibration_mode(selected),
            calibration_consistency=selected.consistency if selected else "unavailable",
            waiting_started_at=cycle.waiting_started, settled_at=settled_at,
            reason=reason, candidate_threshold=self.config.candidate_threshold,
            confirm_threshold=self.config.confirm_threshold, response_seconds=response,
            staff_track_id=selected.staff.track_id if selected else None,
            staff_track_token=selected.staff.track_token if selected else None,
        )


class _GreetingEvaluationStore:
    """Persistence adapter with database-backed exactly-once boundaries."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def start_cycle(self, entry: EntryEventInput) -> GreetingCycle:
        db = self.session_factory()
        try:
            entry_row = db.query(CustomerEntryEvent).filter_by(entry_key=entry.entry_key).one_or_none()
            if entry_row is None:
                raise ValueError(f"entry event not found: {entry.entry_key}")
            existing = db.query(GreetingCycle).filter_by(entry_event_id=entry_row.id).one_or_none()
            if existing is not None:
                return existing
            row = GreetingCycle(
                id=new_uuid(), entry_event_id=entry_row.id, entry_key=entry.entry_key,
                store_id=entry_row.store_id, camera_id=entry.camera_id,
                runtime_session_id=entry.track_token.split(":", 1)[0],
                customer_track_id=entry.track_id, customer_track_token=entry.track_token,
                cycle_created_at=entry.occurred_at,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row
        except IntegrityError:
            db.rollback()
            return db.query(GreetingCycle).filter_by(entry_key=entry.entry_key).one()
        finally:
            db.close()

    def settle(self, value: GreetingEvaluationInput) -> GreetingEvaluation:
        db = self.session_factory()
        try:
            cycle = db.query(GreetingCycle).filter_by(entry_key=value.entry_key).one_or_none()
            if cycle is None:
                raise ValueError(f"greeting cycle not found: {value.entry_key}")
            existing = db.query(GreetingEvaluation).filter_by(
                greeting_cycle_id=cycle.id, evaluator_version=value.evaluator_version, mode=value.mode
            ).one_or_none()
            if existing is not None:
                return existing
            owner_id = f"{cycle.id}:{value.evaluator_version}:{value.mode}"
            clip = db.query(EvidenceClip).filter_by(owner_type="greeting_evaluation", owner_id=owner_id).one_or_none()
            if clip is None:
                clip = EvidenceClip(
                    id=new_uuid(), owner_type="greeting_evaluation", owner_id=owner_id,
                    camera_id=cycle.camera_id,
                    requested_start=cycle.cycle_created_at - timedelta(seconds=3),
                    requested_end=value.settled_at + timedelta(seconds=3), status="pending",
                )
                db.add(clip)
                db.flush()
            row = GreetingEvaluation(
                id=new_uuid(), greeting_cycle_id=cycle.id,
                evaluator_version=value.evaluator_version, mode=value.mode,
                candidate_path=value.candidate_path, decision=value.decision,
                interaction_score=value.interaction_score, track_quality_score=value.track_quality_score,
                raw_metrics=dict(value.raw_metrics), score_breakdown=dict(value.score_breakdown),
                calibration_mode=value.calibration_mode, calibration_consistency=value.calibration_consistency,
                waiting_started_at=value.waiting_started_at, response_seconds=value.response_seconds,
                settled_at=value.settled_at, reason=value.reason,
                candidate_threshold=value.candidate_threshold, confirm_threshold=value.confirm_threshold,
                staff_track_id=value.staff_track_id, staff_track_token=value.staff_track_token,
                evidence_clip_id=clip.id, official_behavior_event_id=value.official_behavior_event_id,
            )
            db.add(row)
            if cycle.waiting_started_at is None and value.waiting_started_at is not None:
                cycle.waiting_started_at = value.waiting_started_at
            # Shadow evaluations settle independently and are never the
            # operational outcome. V1 may settle later because its debounce
            # and duration thresholds intentionally differ from Shadow.
            if value.mode == OFFICIAL_MODE:
                cycle.settled_at = value.settled_at
                cycle.outcome = value.cycle_outcome or _cycle_outcome(value.decision)
                cycle.cancel_reason = value.reason if value.decision == "cancelled" else None
                if value.official_behavior_event_id:
                    cycle.official_behavior_event_id = value.official_behavior_event_id
            db.commit()
            db.refresh(row)
            return row
        except IntegrityError:
            db.rollback()
            cycle = db.query(GreetingCycle).filter_by(entry_key=value.entry_key).one()
            return db.query(GreetingEvaluation).filter_by(
                greeting_cycle_id=cycle.id, evaluator_version=value.evaluator_version, mode=value.mode
            ).one()
        finally:
            db.close()

    def settle_official_behavior(self, behavior: BehaviorEventInput) -> GreetingEvaluation:
        entry_key = behavior.event_key[:-len(":greeting")] if behavior.event_key.endswith(":greeting") else behavior.event_key
        self._start_from_behavior(entry_key, behavior)
        db = self.session_factory()
        try:
            event = db.query(BehaviorEvent).filter_by(event_key=behavior.event_key).one_or_none()
            event_id = event.id if event is not None else None
            if event is not None:
                event.entry_event_id = db.query(CustomerEntryEvent).filter_by(entry_key=entry_key).one().id
                db.commit()
        finally:
            db.close()
        details = behavior.details or {}
        return self.settle(GreetingEvaluationInput(
            entry_key=entry_key, evaluator_version=OFFICIAL_EVALUATOR_VERSION, mode=OFFICIAL_MODE,
            candidate_path="active_approach" if details.get("trigger_source") else None,
            decision=behavior.assessment, interaction_score=behavior.confidence,
            track_quality_score=behavior.confidence, raw_metrics=dict(details), score_breakdown={},
            calibration_mode=details.get("distance_mode"), calibration_consistency=details.get("calibration_status"),
            waiting_started_at=_parse_datetime(details.get("waiting_started_at")), settled_at=behavior.ended_at,
            reason=details.get("reason"), response_seconds=details.get("response_seconds"),
            staff_track_id=behavior.staff_track_id, staff_track_token=behavior.staff_track_token,
            cycle_outcome=behavior.assessment, official_behavior_event_id=event_id,
        ))

    def _start_from_behavior(self, entry_key: str, behavior: BehaviorEventInput) -> GreetingCycle:
        db = self.session_factory()
        try:
            entry = db.query(CustomerEntryEvent).filter_by(entry_key=entry_key).one_or_none()
            if entry is None:
                raise ValueError(f"entry event not found: {entry_key}")
        finally:
            db.close()
        return self.start_cycle(EntryEventInput(
            entry_key=entry_key, camera_id=behavior.camera_id, track_id=behavior.customer_track_id,
            track_token=behavior.customer_track_token, occurred_at=behavior.started_at,
            confidence=behavior.confidence or 0.0, member_id=behavior.member_id, member_tier=behavior.member_tier,
        ))

    def settle_official_cancellation(self, entry_key: str, *, camera_id: str, track_id: int,
                                     track_token: str, occurred_at: datetime, reason: str) -> GreetingEvaluation:
        self.start_cycle(EntryEventInput(entry_key=entry_key, camera_id=camera_id, track_id=track_id,
                                         track_token=track_token, occurred_at=occurred_at, confidence=0.0))
        return self.settle(GreetingEvaluationInput(
            entry_key=entry_key, evaluator_version=OFFICIAL_EVALUATOR_VERSION, mode=OFFICIAL_MODE,
            candidate_path=None, decision="cancelled", interaction_score=None, track_quality_score=None,
            raw_metrics={}, score_breakdown={}, calibration_mode=None, calibration_consistency=None,
            waiting_started_at=None, settled_at=occurred_at, reason=reason, cycle_outcome="cancelled",
        ))


def result_to_input(result: GreetingShadowResult, *, evaluator_version: str = SHADOW_EVALUATOR_VERSION) -> GreetingEvaluationInput:
    return GreetingEvaluationInput(
        entry_key=result.entry_key, evaluator_version=evaluator_version, mode=SHADOW_MODE,
        candidate_path=result.candidate_path, decision=result.decision,
        interaction_score=result.interaction_score, track_quality_score=result.track_quality_score,
        raw_metrics=result.raw_metrics, score_breakdown=result.score_breakdown,
        calibration_mode=result.calibration_mode, calibration_consistency=result.calibration_consistency,
        waiting_started_at=result.waiting_started_at, settled_at=result.settled_at,
        reason=result.reason, candidate_threshold=result.candidate_threshold,
        confirm_threshold=result.confirm_threshold, response_seconds=result.response_seconds,
        staff_track_id=result.staff_track_id, staff_track_token=result.staff_track_token,
        cycle_outcome=result.cycle_outcome,
    )


def _metrics(pair: Optional[_Pair]) -> dict[str, Any]:
    if pair is None:
        return {"raw_proximity_seconds": 0.0, "distance_source": "unavailable"}
    return {
        "raw_proximity_seconds": round(pair.raw_proximity, 3),
        "initial_distance_metres": pair.initial_world,
        "minimum_distance_metres": pair.minimum_world,
        "distance_reduction_metres": pair.reduction,
        "initial_image_relative_distance": pair.initial_image,
        "minimum_image_relative_distance": pair.minimum_image,
        "staff_travel_metres": round(pair.staff_travel, 3) if pair.had_world else None,
        "staff_toward_customer_metres": round(pair.staff_toward, 3) if pair.had_world else None,
        "customer_travel_metres": round(pair.customer_travel, 3) if pair.had_world else None,
        "area_frames": pair.area_frames, "observation_frames": pair.frames,
        "distance_source": "world_and_image" if pair.had_world and pair.had_image else ("world" if pair.had_world else ("image_relative" if pair.had_image else "unavailable")),
        "distance_conflict": pair.consistency == "conflict",
    }


def _calibration_mode(pair: Optional[_Pair]) -> str:
    if pair is None:
        return "unavailable"
    if pair.had_world and pair.had_image:
        return "world_and_image"
    if pair.had_world:
        return "world_only"
    return "image_relative_fallback" if pair.had_image else "unavailable"


def _cycle_outcome(decision: str) -> str:
    if decision in {"confirmed", "would_confirm"}:
        return "confirmed"
    if decision == "uncertain":
        return "uncertain"
    return "cancelled" if decision == "cancelled" else "not_observed"


def _world_distance(left: Optional[Point], right: Optional[Point]) -> Optional[float]:
    return _distance(left, right) if left is not None and right is not None else None


def _image_distance(customer: TrackObservation, staff: TrackObservation) -> Optional[float]:
    if customer.person_height is None or staff.person_height is None:
        return None
    average_height = (customer.person_height + staff.person_height) / 2
    return _distance(customer.foot_point, staff.foot_point) / average_height if average_height else None


def _distance_consistency(world: Optional[float], image: Optional[float], config: GreetingShadowConfig) -> str:
    if world is None or image is None:
        return "unavailable"
    world_near = world <= config.proximity_distance_metres
    image_near = image <= config.image_proximity_person_heights
    return "consistent" if world_near == image_near else "conflict"


def _merge_consistency(left: str, right: str) -> str:
    if "conflict" in {left, right}:
        return "conflict"
    if "consistent" in {left, right}:
        return "consistent"
    return "unavailable"


def _is_proximity(world: Optional[float], image: Optional[float], consistency: str, config: GreetingShadowConfig) -> bool:
    if world is not None:
        return world <= config.proximity_distance_metres
    return image is not None and image <= config.image_proximity_person_heights


def _distance(left: Optional[Point], right: Optional[Point]) -> float:
    if left is None or right is None:
        return 0.0
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _sub(left: Point, right: Point) -> Point:
    return left[0] - right[0], left[1] - right[1]


def _length(value: Point) -> float:
    return math.hypot(value[0], value[1])


def _cosine(left: Point, right: Point) -> float:
    divisor = _length(left) * _length(right)
    return (left[0] * right[0] + left[1] * right[1]) / divisor if divisor else 0.0


def _toward(pair: _Pair, delta: Point) -> float:
    axis = _sub(pair.customer_origin, pair.staff_origin)
    length = _length(axis)
    return (delta[0] * axis[0] + delta[1] * axis[1]) / length if length else 0.0


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
