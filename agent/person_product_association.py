"""Camera-local Person Global Track to Product Track association evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from numbers import Real
from typing import Optional, Sequence

from agent.sam31_product_tracking import ProductDisplayState, ProductTrackStatus


Point = tuple[float, float]
BBox = tuple[float, float, float, float]


class PersonProductAssociationStatus(str, Enum):
    UNASSOCIATED = "UNASSOCIATED"
    CANDIDATE = "CANDIDATE"
    ASSOCIATED = "ASSOCIATED"


@dataclass(frozen=True)
class PersonProductAssociationConfig:
    frame_width: int = 1280
    frame_height: int = 720
    expanded_bbox_horizontal_ratio: float = 0.75
    expanded_bbox_vertical_ratio: float = 0.25
    max_distance_ratio: float = 0.18
    max_bbox_gap_ratio: float = 0.12
    minimum_candidate_score: float = 0.35
    ambiguity_margin: float = 0.05
    association_enter_stable_frames: int = 3
    association_exit_stable_frames: int = 3
    association_grace_seconds: float = 1.0
    association_switch_margin: float = 0.10
    max_sync_delta_seconds: float = 0.12
    unknown_role_penalty: float = 0.05

    def __post_init__(self) -> None:
        _positive_int(self.frame_width, "frame_width")
        _positive_int(self.frame_height, "frame_height")
        _positive_int(
            self.association_enter_stable_frames,
            "association_enter_stable_frames",
        )
        _positive_int(
            self.association_exit_stable_frames,
            "association_exit_stable_frames",
        )
        for value, name in (
            (self.expanded_bbox_horizontal_ratio, "expanded_bbox_horizontal_ratio"),
            (self.expanded_bbox_vertical_ratio, "expanded_bbox_vertical_ratio"),
            (self.max_distance_ratio, "max_distance_ratio"),
            (self.max_bbox_gap_ratio, "max_bbox_gap_ratio"),
            (self.association_grace_seconds, "association_grace_seconds"),
            (self.max_sync_delta_seconds, "max_sync_delta_seconds"),
        ):
            if not _finite_number(value) or float(value) <= 0:
                raise ValueError(f"{name} must be positive")
        for value, name in (
            (self.minimum_candidate_score, "minimum_candidate_score"),
            (self.ambiguity_margin, "ambiguity_margin"),
            (self.association_switch_margin, "association_switch_margin"),
            (self.unknown_role_penalty, "unknown_role_penalty"),
        ):
            if not _finite_number(value) or not 0 <= float(value) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class PersonAssociationObservation:
    observed_at: datetime
    source_seq: int
    camera_id: str
    track_id: int
    track_token: str
    bbox: BBox
    centroid: Point
    foot_point: Point
    confidence: float
    role: str

    def __post_init__(self) -> None:
        _datetime(self.observed_at, "observed_at")
        _nonnegative_int(self.source_seq, "source_seq")
        _nonnegative_int(self.track_id, "track_id")
        _nonempty(self.camera_id, "camera_id")
        _nonempty(self.track_token, "track_token")
        _normalized_bbox(self.bbox, "bbox")
        _normalized_point(self.centroid, "centroid")
        _normalized_point(self.foot_point, "foot_point")
        if self.role not in {"customer", "staff", "unknown", "uncertain"}:
            raise ValueError("unsupported Person association role")
        _unit_interval(self.confidence, "confidence")


@dataclass(frozen=True)
class ProductAssociationObservation:
    observed_at: datetime
    source_seq: int
    camera_id: str
    product_key: str
    product_type: str
    product_track_id: int
    product_track_token: str
    bbox: Optional[BBox]
    centroid: Optional[Point]
    track_status: ProductTrackStatus
    display_state: ProductDisplayState
    mask_present: bool

    def __post_init__(self) -> None:
        _datetime(self.observed_at, "observed_at")
        _nonnegative_int(self.source_seq, "source_seq")
        _nonnegative_int(self.product_track_id, "product_track_id")
        _nonempty(self.camera_id, "camera_id")
        _nonempty(self.product_key, "product_key")
        _nonempty(self.product_type, "product_type")
        _nonempty(self.product_track_token, "product_track_token")
        if not isinstance(self.track_status, ProductTrackStatus):
            raise ValueError("track_status must be ProductTrackStatus")
        if not isinstance(self.display_state, ProductDisplayState):
            raise ValueError("display_state must be ProductDisplayState")
        if not isinstance(self.mask_present, bool):
            raise ValueError("mask_present must be boolean")
        if self.mask_present:
            if self.bbox is None or self.centroid is None:
                raise ValueError("fresh Product observation requires bbox and centroid")
            _normalized_bbox(self.bbox, "bbox")
            _normalized_point(self.centroid, "centroid")
        elif self.bbox is not None or self.centroid is not None:
            raise ValueError("unobserved Product must not carry bbox or centroid")


@dataclass(frozen=True)
class PersonAssociationCandidateEvidence:
    person_track_id: int
    person_track_token: str
    role: str
    person_product_sync_delta_ms: float
    person_product_distance_pixels: Optional[float]
    person_product_distance_ratio: Optional[float]
    bbox_gap_pixels: Optional[float]
    bbox_gap_ratio: Optional[float]
    bbox_proximity: Optional[float]
    product_centroid_inside_expanded_person_bbox: bool
    score: Optional[float]
    eligible: bool
    reject_reason: Optional[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "person_track_id": self.person_track_id,
            "person_track_token": self.person_track_token,
            "role": self.role,
            "person_product_sync_delta_ms": self.person_product_sync_delta_ms,
            "person_product_distance_pixels": self.person_product_distance_pixels,
            "person_product_distance_ratio": self.person_product_distance_ratio,
            "bbox_gap_pixels": self.bbox_gap_pixels,
            "bbox_gap_ratio": self.bbox_gap_ratio,
            "bbox_proximity": self.bbox_proximity,
            "product_centroid_inside_expanded_person_bbox": (
                self.product_centroid_inside_expanded_person_bbox
            ),
            "score": self.score,
            "eligible": self.eligible,
            "reject_reason": self.reject_reason,
        }


@dataclass(frozen=True)
class PersonProductAssociationResult:
    observed_at: datetime
    source_seq: int
    camera_id: str
    product_key: str
    product_track_id: int
    product_track_token: str
    product_track_status: ProductTrackStatus
    product_mask_present: bool
    product_centroid: Optional[Point]
    product_bbox: Optional[BBox]
    person_track_id: Optional[int]
    person_track_token: Optional[str]
    status: PersonProductAssociationStatus
    method: str
    person_candidate_count: int
    distance_ratio: Optional[float]
    distance_pixels: Optional[float]
    bbox_proximity: Optional[float]
    product_inside_expanded_person_bbox: bool
    ambiguous: bool
    stable_frames: int
    association_started_at: Optional[datetime]
    last_confirmed_at: Optional[datetime]
    association_age_seconds: float
    association_grace_active: bool
    person_product_sync_delta_ms: Optional[float]
    association_changed: bool
    association_released: bool
    association_release_reason: Optional[str]
    association_recovered: bool = False
    person_switch_candidate: bool = False
    switch_candidate_person_track_token: Optional[str] = None
    product_missing_grace: bool = False
    person_missing_grace: bool = False
    candidate_diagnostics: tuple[PersonAssociationCandidateEvidence, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.observed_at.isoformat(),
            "source_seq": self.source_seq,
            "camera_id": self.camera_id,
            "product_key": self.product_key,
            "product_track_id": self.product_track_id,
            "product_track_token": self.product_track_token,
            "product_track_status": self.product_track_status.value,
            "product_mask_present": self.product_mask_present,
            "product_centroid": (
                None if self.product_centroid is None else list(self.product_centroid)
            ),
            "product_bbox": None if self.product_bbox is None else list(self.product_bbox),
            "person_candidate_count": self.person_candidate_count,
            "associated_person_track_id": self.person_track_id,
            "associated_person_track_token": self.person_track_token,
            "association_status": self.status.value,
            "association_method": self.method,
            "association_distance_ratio": self.distance_ratio,
            "person_product_distance_pixels": self.distance_pixels,
            "bbox_proximity": self.bbox_proximity,
            "product_inside_expanded_person_bbox": (
                self.product_inside_expanded_person_bbox
            ),
            "association_stable_frames": self.stable_frames,
            "association_ambiguous": self.ambiguous,
            "association_started_at": (
                None
                if self.association_started_at is None
                else self.association_started_at.isoformat()
            ),
            "last_confirmed_at": (
                None
                if self.last_confirmed_at is None
                else self.last_confirmed_at.isoformat()
            ),
            "association_age_seconds": self.association_age_seconds,
            "association_grace_active": self.association_grace_active,
            "person_product_sync_delta_ms": self.person_product_sync_delta_ms,
            "association_changed": self.association_changed,
            "association_released": self.association_released,
            "association_release_reason": self.association_release_reason,
            "association_recovered": self.association_recovered,
            "person_switch_candidate": self.person_switch_candidate,
            "switch_candidate_person_track_token": (
                self.switch_candidate_person_track_token
            ),
            "product_missing_grace": self.product_missing_grace,
            "person_missing_grace": self.person_missing_grace,
            "candidate_diagnostics": [
                item.to_dict() for item in self.candidate_diagnostics
            ],
        }


@dataclass
class _AssociationState:
    status: PersonProductAssociationStatus = PersonProductAssociationStatus.UNASSOCIATED
    candidate_person_track_id: Optional[int] = None
    candidate_person_track_token: Optional[str] = None
    candidate_started_at: Optional[datetime] = None
    associated_person_track_id: Optional[int] = None
    associated_person_track_token: Optional[str] = None
    stable_frames: int = 0
    association_started_at: Optional[datetime] = None
    last_confirmed_at: Optional[datetime] = None
    invalid_frames: int = 0
    grace_active: bool = False


class PersonProductAssociationRuntime:
    """Associate each Product Track with at most one Person Global Track."""

    def __init__(self, config: Optional[PersonProductAssociationConfig] = None) -> None:
        self.config = config or PersonProductAssociationConfig()
        self._states: dict[str, _AssociationState] = {}
        self._current_product_tokens: dict[str, str] = {}

    def process(
        self,
        *,
        product: ProductAssociationObservation,
        persons: Sequence[PersonAssociationObservation],
    ) -> PersonProductAssociationResult:
        persons = tuple(persons)
        if any(person.camera_id != product.camera_id for person in persons):
            raise ValueError("Person and Product observations must use one camera")
        if len({person.track_token for person in persons}) != len(persons):
            raise ValueError("one frame cannot contain duplicate Person Global Tracks")

        segment_changed = False
        previous_token = self._current_product_tokens.get(product.product_key)
        if previous_token is not None and previous_token != product.product_track_token:
            self._states.pop(previous_token, None)
            segment_changed = True
        self._current_product_tokens[product.product_key] = product.product_track_token
        state = self._states.setdefault(product.product_track_token, _AssociationState())

        if product.track_status is ProductTrackStatus.LOST:
            was_associated = state.status is PersonProductAssociationStatus.ASSOCIATED
            self._states.pop(product.product_track_token, None)
            return self._result(
                product=product,
                state=_AssociationState(),
                method="release",
                association_released=was_associated,
                release_reason="product_lost" if was_associated else None,
            )

        if (
            product.track_status is ProductTrackStatus.MISSING_PENDING
            or not product.mask_present
        ):
            if state.status is PersonProductAssociationStatus.ASSOCIATED:
                state.grace_active = True
                return self._result(
                    product=product,
                    state=state,
                    method="product_missing_grace",
                    association_grace_active=True,
                    product_missing_grace=True,
                )
            return self._result(
                product=product,
                state=state,
                method="product_unobserved",
                release_reason=("product_segment_changed" if segment_changed else None),
            )

        diagnostics = tuple(
            self._candidate_evidence(product=product, person=person)
            for person in persons
        )
        eligible = sorted(
            (item for item in diagnostics if item.eligible),
            key=lambda item: (
                -(item.score if item.score is not None else -1.0),
                item.person_track_token,
            ),
        )

        if state.status is PersonProductAssociationStatus.ASSOCIATED:
            incumbent = next(
                (
                    item
                    for item in eligible
                    if item.person_track_token == state.associated_person_track_token
                ),
                None,
            )
            if incumbent is not None:
                recovered = state.grace_active
                state.grace_active = False
                state.invalid_frames = 0
                state.last_confirmed_at = _utc_naive(product.observed_at)
                state.stable_frames += 1
                challenger = next(
                    (
                        item
                        for item in eligible
                        if item.person_track_token != state.associated_person_track_token
                    ),
                    None,
                )
                switch_candidate = bool(
                    challenger is not None
                    and challenger.score is not None
                    and incumbent.score is not None
                    and challenger.score
                    >= incumbent.score + self.config.association_switch_margin
                )
                return self._result(
                    product=product,
                    state=state,
                    method="incumbent_anchor",
                    evidence=incumbent,
                    candidate_count=len(eligible),
                    diagnostics=diagnostics,
                    association_recovered=recovered,
                    person_switch_candidate=switch_candidate,
                    switch_candidate_token=(
                        challenger.person_track_token if switch_candidate else None
                    ),
                )

            state.invalid_frames += 1
            state.grace_active = True
            elapsed = (
                math.inf
                if state.last_confirmed_at is None
                else max(
                    0.0,
                    (
                        _utc_naive(product.observed_at) - state.last_confirmed_at
                    ).total_seconds(),
                )
            )
            if (
                state.invalid_frames >= self.config.association_exit_stable_frames
                and elapsed > self.config.association_grace_seconds
            ):
                released_state = _AssociationState()
                self._states[product.product_track_token] = released_state
                return self._result(
                    product=product,
                    state=released_state,
                    method="release",
                    candidate_count=len(eligible),
                    diagnostics=diagnostics,
                    association_released=True,
                    release_reason="incumbent_grace_expired",
                    person_switch_candidate=bool(eligible),
                    switch_candidate_token=(
                        eligible[0].person_track_token if eligible else None
                    ),
                )
            return self._result(
                product=product,
                state=state,
                method="association_grace",
                candidate_count=len(eligible),
                diagnostics=diagnostics,
                association_grace_active=True,
                person_missing_grace=True,
                person_switch_candidate=bool(eligible),
                switch_candidate_token=(
                    eligible[0].person_track_token if eligible else None
                ),
            )

        ambiguous = bool(
            len(eligible) >= 2
            and eligible[0].score is not None
            and eligible[1].score is not None
            and eligible[0].score - eligible[1].score < self.config.ambiguity_margin
        )
        if not eligible or ambiguous:
            self._reset_candidate(state)
            return self._result(
                product=product,
                state=state,
                method="ambiguous" if ambiguous else "none",
                candidate_count=len(eligible),
                diagnostics=diagnostics,
                ambiguous=ambiguous,
                release_reason=("product_segment_changed" if segment_changed else None),
            )

        winner = eligible[0]
        if state.candidate_person_track_token == winner.person_track_token:
            state.stable_frames += 1
        else:
            state.candidate_person_track_id = winner.person_track_id
            state.candidate_person_track_token = winner.person_track_token
            state.candidate_started_at = _utc_naive(product.observed_at)
            state.stable_frames = 1
        state.status = PersonProductAssociationStatus.CANDIDATE
        if state.stable_frames < self.config.association_enter_stable_frames:
            return self._result(
                product=product,
                state=state,
                method="bbox_temporal_candidate",
                evidence=winner,
                candidate_count=len(eligible),
                diagnostics=diagnostics,
                release_reason=("product_segment_changed" if segment_changed else None),
            )

        state.status = PersonProductAssociationStatus.ASSOCIATED
        state.associated_person_track_id = state.candidate_person_track_id
        state.associated_person_track_token = state.candidate_person_track_token
        state.association_started_at = state.candidate_started_at
        state.last_confirmed_at = _utc_naive(product.observed_at)
        state.invalid_frames = 0
        state.grace_active = False
        return self._result(
            product=product,
            state=state,
            method="bbox_temporal_stable",
            evidence=winner,
            candidate_count=len(eligible),
            diagnostics=diagnostics,
            association_changed=True,
            release_reason=("product_segment_changed" if segment_changed else None),
        )

    def _candidate_evidence(
        self,
        *,
        product: ProductAssociationObservation,
        person: PersonAssociationObservation,
    ) -> PersonAssociationCandidateEvidence:
        sync_delta = abs(
            (
                _utc_naive(person.observed_at) - _utc_naive(product.observed_at)
            ).total_seconds()
        )
        if person.role == "staff":
            return self._rejected_evidence(person, sync_delta, "staff_role")
        if sync_delta > self.config.max_sync_delta_seconds:
            return self._rejected_evidence(person, sync_delta, "sync_delta_exceeded")
        assert product.centroid is not None and product.bbox is not None
        expanded = _expand_bbox(
            person.bbox,
            horizontal_ratio=self.config.expanded_bbox_horizontal_ratio,
            vertical_ratio=self.config.expanded_bbox_vertical_ratio,
        )
        distance_pixels = _point_bbox_distance_pixels(
            product.centroid,
            person.bbox,
            frame_width=self.config.frame_width,
            frame_height=self.config.frame_height,
        )
        bbox_gap_pixels = _bbox_gap_pixels(
            product.bbox,
            person.bbox,
            frame_width=self.config.frame_width,
            frame_height=self.config.frame_height,
        )
        diagonal = math.hypot(self.config.frame_width, self.config.frame_height)
        distance_ratio = distance_pixels / diagonal
        bbox_gap_ratio = bbox_gap_pixels / diagonal
        inside = _contains(expanded, product.centroid)
        distance_score = max(
            0.0,
            1.0 - distance_ratio / self.config.max_distance_ratio,
        )
        bbox_proximity = max(
            0.0,
            1.0 - bbox_gap_ratio / self.config.max_bbox_gap_ratio,
        )
        score = (
            0.45 * distance_score
            + 0.35 * bbox_proximity
            + 0.20 * float(inside)
        )
        if person.role in {"unknown", "uncertain"}:
            score -= self.config.unknown_role_penalty
        eligible = bool(
            distance_ratio <= self.config.max_distance_ratio
            and (inside or bbox_gap_ratio <= self.config.max_bbox_gap_ratio)
            and score >= self.config.minimum_candidate_score
        )
        return PersonAssociationCandidateEvidence(
            person_track_id=person.track_id,
            person_track_token=person.track_token,
            role=person.role,
            person_product_sync_delta_ms=sync_delta * 1000.0,
            person_product_distance_pixels=distance_pixels,
            person_product_distance_ratio=distance_ratio,
            bbox_gap_pixels=bbox_gap_pixels,
            bbox_gap_ratio=bbox_gap_ratio,
            bbox_proximity=bbox_proximity,
            product_centroid_inside_expanded_person_bbox=inside,
            score=score,
            eligible=eligible,
            reject_reason=None if eligible else "spatial_evidence_below_threshold",
        )

    @staticmethod
    def _rejected_evidence(
        person: PersonAssociationObservation,
        sync_delta: float,
        reason: str,
    ) -> PersonAssociationCandidateEvidence:
        return PersonAssociationCandidateEvidence(
            person_track_id=person.track_id,
            person_track_token=person.track_token,
            role=person.role,
            person_product_sync_delta_ms=sync_delta * 1000.0,
            person_product_distance_pixels=None,
            person_product_distance_ratio=None,
            bbox_gap_pixels=None,
            bbox_gap_ratio=None,
            bbox_proximity=None,
            product_centroid_inside_expanded_person_bbox=False,
            score=None,
            eligible=False,
            reject_reason=reason,
        )

    @staticmethod
    def _reset_candidate(state: _AssociationState) -> None:
        state.status = PersonProductAssociationStatus.UNASSOCIATED
        state.candidate_person_track_id = None
        state.candidate_person_track_token = None
        state.candidate_started_at = None
        state.stable_frames = 0

    def _result(
        self,
        *,
        product: ProductAssociationObservation,
        state: _AssociationState,
        method: str,
        evidence: Optional[PersonAssociationCandidateEvidence] = None,
        candidate_count: int = 0,
        diagnostics: tuple[PersonAssociationCandidateEvidence, ...] = (),
        ambiguous: bool = False,
        association_changed: bool = False,
        association_released: bool = False,
        release_reason: Optional[str] = None,
        association_grace_active: bool = False,
        association_recovered: bool = False,
        person_switch_candidate: bool = False,
        switch_candidate_token: Optional[str] = None,
        product_missing_grace: bool = False,
        person_missing_grace: bool = False,
    ) -> PersonProductAssociationResult:
        is_associated = state.status is PersonProductAssociationStatus.ASSOCIATED
        person_track_id = (
            state.associated_person_track_id
            if is_associated else state.candidate_person_track_id
        )
        person_track_token = (
            state.associated_person_track_token
            if is_associated else state.candidate_person_track_token
        )
        started_at = state.association_started_at if is_associated else None
        age = (
            max(
                0.0,
                (_utc_naive(product.observed_at) - started_at).total_seconds(),
            )
            if started_at is not None else 0.0
        )
        return PersonProductAssociationResult(
            observed_at=_utc_naive(product.observed_at),
            source_seq=product.source_seq,
            camera_id=product.camera_id,
            product_key=product.product_key,
            product_track_id=product.product_track_id,
            product_track_token=product.product_track_token,
            product_track_status=product.track_status,
            product_mask_present=product.mask_present,
            product_centroid=product.centroid,
            product_bbox=product.bbox,
            person_track_id=person_track_id,
            person_track_token=person_track_token,
            status=state.status,
            method=method,
            person_candidate_count=candidate_count,
            distance_ratio=(
                None if evidence is None else evidence.person_product_distance_ratio
            ),
            distance_pixels=(
                None if evidence is None else evidence.person_product_distance_pixels
            ),
            bbox_proximity=None if evidence is None else evidence.bbox_proximity,
            product_inside_expanded_person_bbox=(
                False
                if evidence is None
                else evidence.product_centroid_inside_expanded_person_bbox
            ),
            ambiguous=ambiguous,
            stable_frames=state.stable_frames,
            association_started_at=started_at,
            last_confirmed_at=state.last_confirmed_at,
            association_age_seconds=age,
            association_grace_active=(
                association_grace_active or state.grace_active
            ),
            person_product_sync_delta_ms=(
                None if evidence is None else evidence.person_product_sync_delta_ms
            ),
            association_changed=association_changed,
            association_released=association_released,
            association_release_reason=release_reason,
            association_recovered=association_recovered,
            person_switch_candidate=person_switch_candidate,
            switch_candidate_person_track_token=switch_candidate_token,
            product_missing_grace=product_missing_grace,
            person_missing_grace=person_missing_grace,
            candidate_diagnostics=diagnostics,
        )


def _point_bbox_distance_pixels(
    point: Point,
    bbox: BBox,
    *,
    frame_width: int,
    frame_height: int,
) -> float:
    x, y = point
    x1, y1, x2, y2 = bbox
    dx = max(x1 - x, 0.0, x - x2) * frame_width
    dy = max(y1 - y, 0.0, y - y2) * frame_height
    return math.hypot(dx, dy)


def _bbox_gap_pixels(
    left: BBox,
    right: BBox,
    *,
    frame_width: int,
    frame_height: int,
) -> float:
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    dx = max(rx1 - lx2, lx1 - rx2, 0.0) * frame_width
    dy = max(ry1 - ly2, ly1 - ry2, 0.0) * frame_height
    return math.hypot(dx, dy)


def _expand_bbox(
    bbox: BBox,
    *,
    horizontal_ratio: float,
    vertical_ratio: float,
) -> BBox:
    x1, y1, x2, y2 = bbox
    width = x2 - x1
    height = y2 - y1
    return (
        max(0.0, x1 - width * horizontal_ratio),
        max(0.0, y1 - height * vertical_ratio),
        min(1.0, x2 + width * horizontal_ratio),
        min(1.0, y2 + height * vertical_ratio),
    )


def _contains(bbox: BBox, point: Point) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _normalized_bbox(value: BBox, name: str) -> None:
    if len(value) != 4 or not all(_finite_number(item) for item in value):
        raise ValueError(f"{name} must contain four finite coordinates")
    x1, y1, x2, y2 = (float(item) for item in value)
    if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
        raise ValueError(f"{name} must use normalized full-frame coordinates")


def _normalized_point(value: Point, name: str) -> None:
    if len(value) != 2 or not all(
        _finite_number(item) and 0 <= float(item) <= 1 for item in value
    ):
        raise ValueError(f"{name} must use normalized full-frame coordinates")


def _datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be datetime")


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _unit_interval(value: object, name: str) -> None:
    if not _finite_number(value) or not 0 <= float(value) <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
