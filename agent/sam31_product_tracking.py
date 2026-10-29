"""Independent SAM 3.1 product observations and track-segment continuity."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from itertools import product
from math import hypot
from typing import Mapping, Optional, Sequence

import cv2
import numpy as np


BBox = tuple[float, float, float, float]
Point = tuple[float, float]


class ProductDisplayState(str, Enum):
    IN_DISPLAY = "IN_DISPLAY"
    OUTSIDE_DISPLAY = "OUTSIDE_DISPLAY"
    UNOBSERVED = "UNOBSERVED"


class ProductTrackStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MISSING_PENDING = "MISSING_PENDING"
    LOST = "LOST"


class ManualInitializationAttemptTracker:
    """Allow one manual fallback attempt for each Product Track segment."""

    def __init__(self) -> None:
        self._claimed: set[tuple[str, int]] = set()

    def claim(self, product_type: str, segment_id: int) -> bool:
        _product_type(product_type)
        if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id < 1:
            raise ValueError("segment_id must be a positive integer")
        key = (product_type, segment_id)
        if key in self._claimed:
            return False
        self._claimed.add(key)
        return True


@dataclass(frozen=True)
class ProductDisplaySurface:
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("product display surface needs at least three points")
        if any(
            len(point) != 2 or not all(0 <= float(value) <= 1 for value in point)
            for point in self.points
        ):
            raise ValueError("product display surface points must be normalized")

    def contains(self, point: Point) -> bool:
        polygon = np.asarray(self.points, dtype=np.float32)
        return cv2.pointPolygonTest(polygon, point, False) >= 0


@dataclass(frozen=True)
class ProductCandidate:
    local_obj_id: int
    bbox: BBox
    centroid: Point
    confidence: float
    mask: np.ndarray

    def __post_init__(self) -> None:
        if self.local_obj_id < 0:
            raise ValueError("local_obj_id must be non-negative")
        if len(self.bbox) != 4 or self.bbox[0] >= self.bbox[2] or self.bbox[1] >= self.bbox[3]:
            raise ValueError("bbox must contain ordered coordinates")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.mask, np.ndarray) or self.mask.ndim != 2:
            raise ValueError("mask must be a two-dimensional array")


@dataclass(frozen=True)
class ProductSpatialEvidence:
    centroid_inside_product_display_surface: bool
    surface_overlap_ratio: float
    mask_area: int


@dataclass(frozen=True)
class GroundingDecision:
    product_type: str
    status: str
    candidate_count: int
    candidate: Optional[ProductCandidate] = None
    evidence: Optional[ProductSpatialEvidence] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class MerchandiseObservation:
    observed_at: datetime
    source_seq: int
    product_track_id: int
    product_track_token: str
    product_type: str
    bbox: BBox
    centroid: Point
    confidence: float
    mask_area: int
    surface_overlap_ratio: float
    centroid_inside_product_display_surface: bool
    display_state: ProductDisplayState
    tracking_lost: bool = False
    product_key: Optional[str] = None
    local_obj_id: Optional[int] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.observed_at.isoformat(),
            "source_seq": self.source_seq,
            "product_track_id": self.product_track_id,
            "product_track_token": self.product_track_token,
            "product_key": self.product_key or self.product_type,
            "product_type": self.product_type,
            "local_obj_id": self.local_obj_id,
            "bbox": list(self.bbox),
            "centroid": list(self.centroid),
            "confidence": self.confidence,
            "mask_area": self.mask_area,
            "mask_overlap_ratio": self.surface_overlap_ratio,
            "surface_overlap_ratio": self.surface_overlap_ratio,
            "centroid_inside": self.centroid_inside_product_display_surface,
            "centroid_inside_product_display_surface": (
                self.centroid_inside_product_display_surface
            ),
            "display_state": self.display_state.value,
            "tracking_lost": self.tracking_lost,
        }


def evaluate_candidate(
    candidate: ProductCandidate,
    surface: ProductDisplaySurface,
    *,
    frame_width: int,
    frame_height: int,
) -> ProductSpatialEvidence:
    _frame_size(frame_width, frame_height)
    if candidate.mask.shape != (frame_height, frame_width):
        raise ValueError("candidate mask does not match the configured AI frame")
    normalized_centroid = (
        candidate.centroid[0] / frame_width,
        candidate.centroid[1] / frame_height,
    )
    mask_area = int(np.count_nonzero(candidate.mask))
    polygon = np.asarray(
        [(x * frame_width, y * frame_height) for x, y in surface.points],
        dtype=np.int32,
    )
    zone_mask = np.zeros(candidate.mask.shape, dtype=np.uint8)
    cv2.fillPoly(zone_mask, [polygon], 1)
    overlap_area = int(
        np.count_nonzero(candidate.mask.astype(bool) & zone_mask.astype(bool))
    )
    return ProductSpatialEvidence(
        centroid_inside_product_display_surface=surface.contains(normalized_centroid),
        surface_overlap_ratio=overlap_area / mask_area if mask_area else 0.0,
        mask_area=mask_area,
    )


def grounding_decision(
    *,
    product_type: str,
    candidates: tuple[ProductCandidate, ...],
    product_display_surface: ProductDisplaySurface,
    frame_width: int,
    frame_height: int,
    surface_overlap_min: float = 0.50,
    invalid_status: str = "grounding_unstable",
) -> GroundingDecision:
    _product_type(product_type)
    if len(candidates) != 1:
        return GroundingDecision(
            product_type=product_type,
            status=invalid_status,
            candidate_count=len(candidates),
            reason="candidate_count",
        )
    candidate = candidates[0]
    evidence = evaluate_candidate(
        candidate,
        product_display_surface,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    if not evidence.centroid_inside_product_display_surface:
        return GroundingDecision(
            product_type,
            invalid_status,
            1,
            candidate,
            evidence,
            "centroid_outside_product_display_surface",
        )
    if evidence.surface_overlap_ratio < surface_overlap_min:
        return GroundingDecision(
            product_type,
            invalid_status,
            1,
            candidate,
            evidence,
            "product_display_surface_overlap",
        )
    return GroundingDecision(product_type, "accepted", 1, candidate, evidence)


class ProductTrackRuntime:
    """Maintain independent integer segments for each product type."""

    def __init__(
        self,
        *,
        camera_id: str,
        runtime_instance_id: str,
        product_display_surface: ProductDisplaySurface,
        frame_width: int = 1280,
        frame_height: int = 720,
        surface_overlap_min: float = 0.50,
        product_track_lost_after_windows: int = 2,
    ) -> None:
        if not camera_id or not runtime_instance_id:
            raise ValueError("camera_id and runtime_instance_id are required")
        _frame_size(frame_width, frame_height)
        if not 0 <= surface_overlap_min <= 1:
            raise ValueError("surface_overlap_min must be between 0 and 1")
        if product_track_lost_after_windows < 1:
            raise ValueError("product_track_lost_after_windows must be positive")
        self.camera_id = camera_id
        self.runtime_instance_id = runtime_instance_id
        self.product_display_surface = product_display_surface
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.surface_overlap_min = surface_overlap_min
        self.product_track_lost_after_windows = product_track_lost_after_windows
        self._next_segment = {
            product_type: 1 for product_type in ("smart_toy", "smart_guitar")
        }
        self._active: dict[str, dict[str, object]] = {}

    def initialize(
        self,
        *,
        product_type: str,
        decision: GroundingDecision,
        observed_at: datetime,
        source_seq: int,
    ) -> MerchandiseObservation:
        if decision.status != "accepted" or decision.candidate is None:
            raise ValueError("cannot create Product Track before the spatial gate")
        if product_type in self._active and self._active[product_type]["status"] is not ProductTrackStatus.LOST:
            raise ValueError("cannot create a parallel Product Track segment")
        segment = self._next_segment[product_type]
        self._next_segment[product_type] += 1
        self._active[product_type] = {
            "segment": segment,
            "token": (
                f"product:{self.camera_id}:{self.runtime_instance_id}:"
                f"{product_type}:{segment}"
            ),
            "local_obj_id": decision.candidate.local_obj_id,
            "misses": 0,
            "status": ProductTrackStatus.ACTIVE,
        }
        return self.observe(
            product_type=product_type,
            observed_at=observed_at,
            source_seq=source_seq,
            candidate=decision.candidate,
        )

    def observe(
        self,
        *,
        product_type: str,
        observed_at: datetime,
        source_seq: int,
        candidate: ProductCandidate,
    ) -> MerchandiseObservation:
        active = self._active.get(product_type)
        if active is None or active["status"] is ProductTrackStatus.LOST:
            raise ValueError("product track is not active")
        active["local_obj_id"] = candidate.local_obj_id
        active["misses"] = 0
        active["status"] = ProductTrackStatus.ACTIVE
        evidence = evaluate_candidate(
            candidate,
            self.product_display_surface,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
        )
        in_display = bool(
            evidence.centroid_inside_product_display_surface
            and evidence.surface_overlap_ratio >= self.surface_overlap_min
        )
        return MerchandiseObservation(
            observed_at=_utc_naive(observed_at),
            source_seq=source_seq,
            product_track_id=int(active["segment"]),
            product_track_token=str(active["token"]),
            product_type=product_type,
            bbox=tuple(float(value) for value in candidate.bbox),
            centroid=(
                candidate.centroid[0] / self.frame_width,
                candidate.centroid[1] / self.frame_height,
            ),
            confidence=float(candidate.confidence),
            mask_area=evidence.mask_area,
            surface_overlap_ratio=evidence.surface_overlap_ratio,
            centroid_inside_product_display_surface=(
                evidence.centroid_inside_product_display_surface
            ),
            display_state=(
                ProductDisplayState.IN_DISPLAY
                if in_display
                else ProductDisplayState.OUTSIDE_DISPLAY
            ),
            local_obj_id=candidate.local_obj_id,
        )

    def association_miss(self, product_type: str) -> Optional[dict[str, object]]:
        active = self._active.get(product_type)
        if active is None or active["status"] is ProductTrackStatus.LOST:
            return None
        active["misses"] = int(active["misses"]) + 1
        active["status"] = (
            ProductTrackStatus.LOST
            if int(active["misses"]) >= self.product_track_lost_after_windows
            else ProductTrackStatus.MISSING_PENDING
        )
        return {
            "product_type": product_type,
            "product_track_id": int(active["segment"]),
            "product_track_token": str(active["token"]),
            "consecutive_missed_windows": int(active["misses"]),
            "display_state": ProductDisplayState.UNOBSERVED.value,
            "tracking_lost": active["status"] is ProductTrackStatus.LOST,
        }

    def status(self, product_type: str) -> Optional[ProductTrackStatus]:
        active = self._active.get(product_type)
        return None if active is None else active["status"]

    def track_identity(self, product_type: str) -> Optional[tuple[int, str]]:
        active = self._active.get(product_type)
        if active is None:
            return None
        return int(active["segment"]), str(active["token"])

    def next_segment_id(self, product_type: str) -> int:
        _product_type(product_type)
        return int(self._next_segment[product_type])


@dataclass(frozen=True)
class ProductAssociation:
    product_key: str
    candidate: Optional[ProductCandidate]
    method: str
    success: bool
    ambiguous: bool
    mask_iou: Optional[float] = None
    centroid_distance_ratio: Optional[float] = None
    contender_local_obj_ids: tuple[int, ...] = ()
    association_role: str = "normal"
    anchor_locked_candidate_ids: tuple[int, ...] = ()
    recovery_attempted: bool = False
    recovery_candidate_count: int = 0
    recovery_success: bool = False
    recovery_rejected_reason: Optional[str] = None


@dataclass(frozen=True)
class ProductWindowResult:
    product_key: str
    product_type: str
    product_track_id: Optional[int]
    product_track_token: Optional[str]
    local_obj_id: Optional[int]
    grounding_candidate_count: int
    candidate_count: int
    bbox: Optional[BBox]
    centroid: Optional[Point]
    mask_area: Optional[int]
    surface_overlap_ratio: Optional[float]
    display_state: ProductDisplayState
    association_method: str
    association_success: bool
    association_ambiguous: bool
    track_status: Optional[ProductTrackStatus]
    consecutive_missed_windows: int
    tracking_lost: bool
    observed_at: datetime
    source_seq: int
    window_no: int
    processing_seconds: Optional[float] = None
    fresh_lag: Optional[float] = None
    recovered_from_missing_pending: bool = False
    recovered_after_missed_windows: int = 0
    association_role: str = "normal"
    anchor_locked_candidate_ids: tuple[int, ...] = ()
    recovery_attempted: bool = False
    recovery_candidate_count: int = 0
    recovery_success: bool = False
    recovery_rejected_reason: Optional[str] = None
    recovery_latency_windows: int = 0
    association_mask_iou: Optional[float] = None
    association_centroid_distance_ratio: Optional[float] = None
    display_evidence_mode: str = "mask_surface_overlap"
    current_support_contact_point: Optional[Point] = None
    approved_support_contact_point: Optional[Point] = None
    support_contact_distance: Optional[float] = None
    support_contact_tolerance: Optional[float] = None
    display_state_reason: str = "mask_surface_overlap"

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.observed_at.isoformat(),
            "window_no": self.window_no,
            "source_seq": self.source_seq,
            "product_key": self.product_key,
            "product_type": self.product_type,
            "product_track_id": self.product_track_id,
            "product_track_token": self.product_track_token,
            "local_obj_id": self.local_obj_id,
            "grounding_candidate_count": self.grounding_candidate_count,
            "candidate_count": self.candidate_count,
            "bbox": None if self.bbox is None else list(self.bbox),
            "centroid": None if self.centroid is None else list(self.centroid),
            "mask_area": self.mask_area,
            "surface_overlap_ratio": self.surface_overlap_ratio,
            "display_state": self.display_state.value,
            "display_evidence_mode": self.display_evidence_mode,
            "current_support_contact_point": None if self.current_support_contact_point is None else list(self.current_support_contact_point),
            "approved_support_contact_point": None if self.approved_support_contact_point is None else list(self.approved_support_contact_point),
            "support_contact_distance": self.support_contact_distance,
            "support_contact_tolerance": self.support_contact_tolerance,
            "display_state_reason": self.display_state_reason,
            "association_method": self.association_method,
            "association_success": self.association_success,
            "association_ambiguous": self.association_ambiguous,
            "association_mask_iou": self.association_mask_iou,
            "association_centroid_distance_ratio": (
                self.association_centroid_distance_ratio
            ),
            "track_status": (
                None if self.track_status is None else self.track_status.value
            ),
            "consecutive_missed_windows": self.consecutive_missed_windows,
            "tracking_lost": self.tracking_lost,
            "processing_seconds": self.processing_seconds,
            "fresh_lag": self.fresh_lag,
            "recovered_from_missing_pending": self.recovered_from_missing_pending,
            "recovered_after_missed_windows": self.recovered_after_missed_windows,
            "association_role": self.association_role,
            "anchor_locked_candidate_ids": list(self.anchor_locked_candidate_ids),
            "recovery_attempted": self.recovery_attempted,
            "recovery_candidate_count": self.recovery_candidate_count,
            "recovery_success": self.recovery_success,
            "recovery_rejected_reason": self.recovery_rejected_reason,
            "recovery_latency_windows": self.recovery_latency_windows,
        }


@dataclass(frozen=True)
class _AssociationEdge:
    candidate_index: int
    method: str
    score: float
    mask_iou: float
    centroid_distance_ratio: float


def initialization_candidates(
    *,
    candidates: Sequence[ProductCandidate],
    product_display_surface: ProductDisplaySurface,
    frame_width: int,
    frame_height: int,
    surface_overlap_min: float = 0.50,
) -> tuple[ProductCandidate, ...]:
    """Return credible Surface candidates without assigning instance identity."""
    if not 0 <= surface_overlap_min <= 1:
        raise ValueError("surface_overlap_min must be between 0 and 1")
    return tuple(
        candidate
        for candidate in candidates
        if (
            (evidence := evaluate_candidate(
                candidate,
                product_display_surface,
                frame_width=frame_width,
                frame_height=frame_height,
            )).centroid_inside_product_display_surface
            and evidence.surface_overlap_ratio >= surface_overlap_min
        )
    )


def associate_candidates_globally(
    *,
    previous_candidates: Mapping[str, ProductCandidate],
    candidates: Sequence[ProductCandidate],
    frame_width: int,
    frame_height: int,
    overlap_iou_min: float = 0.05,
    geometry_distance_max: float = 0.20,
    ambiguity_margin: float = 0.05,
) -> tuple[ProductAssociation, ...]:
    """Associate all active Product Tracks in one globally unique assignment."""
    _frame_size(frame_width, frame_height)
    if not 0 <= overlap_iou_min <= 1:
        raise ValueError("overlap_iou_min must be between 0 and 1")
    if geometry_distance_max <= 0 or ambiguity_margin < 0:
        raise ValueError("association distance and margin must be valid")
    local_ids = [candidate.local_obj_id for candidate in candidates]
    if len(set(local_ids)) != len(local_ids):
        raise ValueError("candidate local_obj_id must be unique within one window")

    keys = tuple(sorted(previous_candidates))
    edges: dict[tuple[str, int], _AssociationEdge] = {}
    for key in keys:
        previous = previous_candidates[key]
        for index, candidate in enumerate(candidates):
            edge = _association_edge(
                candidate_index=index,
                previous=previous,
                candidate=candidate,
                frame_width=frame_width,
                frame_height=frame_height,
                overlap_iou_min=overlap_iou_min,
                geometry_distance_max=geometry_distance_max,
            )
            if edge is not None:
                edges[(key, index)] = edge

    choices = [
        (None,) + tuple(index for index in range(len(candidates)) if (key, index) in edges)
        for key in keys
    ]
    assignments: list[tuple[int, float, tuple[Optional[int], ...]]] = []
    for assignment in product(*choices):
        selected = [index for index in assignment if index is not None]
        if len(selected) != len(set(selected)):
            continue
        assignments.append((
            len(selected),
            sum(
                edges[(key, index)].score
                for key, index in zip(keys, assignment)
                if index is not None
            ),
            assignment,
        ))
    if not assignments:
        assignments = [(0, 0.0, tuple(None for _ in keys))]
    assignments.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_cardinality, best_score, best_assignment = assignments[0]
    contenders = [
        assignment
        for cardinality, score, assignment in assignments
        if cardinality == best_cardinality and best_score - score <= ambiguity_margin
    ]

    result: list[ProductAssociation] = []
    for key_index, key in enumerate(keys):
        possible = {assignment[key_index] for assignment in contenders}
        assigned_index = best_assignment[key_index]
        ambiguous = len(possible) > 1 and any(index is not None for index in possible)
        contender_ids = tuple(sorted(
            candidates[index].local_obj_id
            for index in possible
            if index is not None
        ))
        if assigned_index is None or ambiguous:
            result.append(ProductAssociation(
                product_key=key,
                candidate=None,
                method=("ambiguous" if ambiguous else "none"),
                success=False,
                ambiguous=ambiguous,
                contender_local_obj_ids=contender_ids,
            ))
            continue
        edge = edges[(key, assigned_index)]
        result.append(ProductAssociation(
            product_key=key,
            candidate=candidates[assigned_index],
            method=edge.method,
            success=True,
            ambiguous=False,
            mask_iou=edge.mask_iou,
            centroid_distance_ratio=edge.centroid_distance_ratio,
            contender_local_obj_ids=contender_ids,
        ))
    return tuple(result)


def associate_candidates_with_active_anchors(
    *,
    previous_candidates: Mapping[str, ProductCandidate],
    candidates: Sequence[ProductCandidate],
    active_anchor_keys: Sequence[str],
    recovering_keys: Sequence[str],
    frame_width: int,
    frame_height: int,
    overlap_iou_min: float = 0.05,
    geometry_distance_max: float = 0.20,
    ambiguity_margin: float = 0.05,
) -> tuple[ProductAssociation, ...]:
    """Protect fresh ACTIVE tracks before associating stale recovery tracks."""
    anchor_keys = tuple(sorted(active_anchor_keys))
    recovery_keys = tuple(sorted(recovering_keys))
    if set(anchor_keys) & set(recovery_keys):
        raise ValueError("active anchor and recovering Product Tracks must be disjoint")
    if set(anchor_keys) | set(recovery_keys) != set(previous_candidates):
        raise ValueError("association roles must cover every active Product Track")
    if not recovery_keys:
        return tuple(
            replace(item, association_role="normal")
            for item in associate_candidates_globally(
                previous_candidates=previous_candidates,
                candidates=candidates,
                frame_width=frame_width,
                frame_height=frame_height,
                overlap_iou_min=overlap_iou_min,
                geometry_distance_max=geometry_distance_max,
                ambiguity_margin=ambiguity_margin,
            )
        )

    anchor_associations = associate_candidates_globally(
        previous_candidates={key: previous_candidates[key] for key in anchor_keys},
        candidates=candidates,
        frame_width=frame_width,
        frame_height=frame_height,
        overlap_iou_min=overlap_iou_min,
        geometry_distance_max=geometry_distance_max,
        ambiguity_margin=ambiguity_margin,
    )
    locked_ids = tuple(sorted(
        int(item.candidate.local_obj_id)
        for item in anchor_associations
        if item.success and item.candidate is not None
    ))
    locked_id_set = set(locked_ids)
    remaining = tuple(
        candidate
        for candidate in candidates
        if candidate.local_obj_id not in locked_id_set
    )
    decorated_anchors = [
        replace(
            item,
            association_role="active_anchor",
            anchor_locked_candidate_ids=locked_ids,
        )
        for item in anchor_associations
    ]

    unresolved_anchors = [item for item in anchor_associations if not item.success]
    if unresolved_anchors:
        reason = (
            "active_anchor_ambiguous"
            if any(item.ambiguous for item in unresolved_anchors)
            else "active_anchor_unresolved"
        )
        recovery_associations = [
            ProductAssociation(
                product_key=key,
                candidate=None,
                method="none",
                success=False,
                ambiguous=False,
                association_role="recovery",
                anchor_locked_candidate_ids=locked_ids,
                recovery_attempted=False,
                recovery_candidate_count=len(remaining),
                recovery_success=False,
                recovery_rejected_reason=reason,
            )
            for key in recovery_keys
        ]
    elif len(candidates) < len(previous_candidates):
        recovery_associations = [
            ProductAssociation(
                product_key=key,
                candidate=None,
                method="none",
                success=False,
                ambiguous=False,
                association_role="recovery",
                anchor_locked_candidate_ids=locked_ids,
                recovery_attempted=True,
                recovery_candidate_count=len(remaining),
                recovery_success=False,
                recovery_rejected_reason="insufficient_candidates_for_tracks",
            )
            for key in recovery_keys
        ]
    else:
        recovery_associations = []
        for item in associate_candidates_globally(
            previous_candidates={key: previous_candidates[key] for key in recovery_keys},
            candidates=remaining,
            frame_width=frame_width,
            frame_height=frame_height,
            overlap_iou_min=overlap_iou_min,
            geometry_distance_max=geometry_distance_max,
            ambiguity_margin=ambiguity_margin,
        ):
            rejected_reason = None
            if not item.success:
                rejected_reason = (
                    "ambiguous_recovery_candidate"
                    if item.ambiguous
                    else "no_valid_recovery_candidate"
                )
            recovery_associations.append(replace(
                item,
                association_role="recovery",
                anchor_locked_candidate_ids=locked_ids,
                recovery_attempted=True,
                recovery_candidate_count=len(remaining),
                recovery_success=item.success,
                recovery_rejected_reason=rejected_reason,
            ))

    return tuple(sorted(
        (*decorated_anchors, *recovery_associations),
        key=lambda item: item.product_key,
    ))


def diagnose_candidate_edges(
    *,
    previous_candidates: Mapping[str, ProductCandidate],
    candidates: Sequence[ProductCandidate],
    frame_width: int,
    frame_height: int,
    overlap_iou_min: float = 0.05,
    geometry_distance_max: float = 0.20,
) -> tuple[dict[str, object], ...]:
    """Expose pairwise continuation evidence without changing assignment policy."""
    _frame_size(frame_width, frame_height)
    result = []
    diagonal = hypot(frame_width, frame_height)
    for product_key in sorted(previous_candidates):
        previous = previous_candidates[product_key]
        for index, candidate in enumerate(candidates):
            mask_iou = _mask_iou(previous.mask, candidate.mask)
            distance = hypot(
                previous.centroid[0] - candidate.centroid[0],
                previous.centroid[1] - candidate.centroid[1],
            ) / diagonal
            edge = _association_edge(
                candidate_index=index,
                previous=previous,
                candidate=candidate,
                frame_width=frame_width,
                frame_height=frame_height,
                overlap_iou_min=overlap_iou_min,
                geometry_distance_max=geometry_distance_max,
            )
            result.append({
                "product_key": product_key,
                "previous_centroid": list(previous.centroid),
                "current_local_obj_id": candidate.local_obj_id,
                "current_centroid": list(candidate.centroid),
                "mask_iou": mask_iou,
                "centroid_distance_ratio": distance,
                "association_edge": edge is not None,
                "association_method": None if edge is None else edge.method,
            })
    return tuple(result)


class MultiProductTrackRuntime:
    """Maintain independent segments for multiple same-type product instances."""

    def __init__(
        self,
        *,
        camera_id: str,
        runtime_instance_id: str,
        products: Mapping[str, str],
        product_display_surface: ProductDisplaySurface,
        frame_width: int = 1280,
        frame_height: int = 720,
        surface_overlap_min: float = 0.50,
        product_track_lost_after_windows: int = 3,
        overlap_iou_min: float = 0.05,
        geometry_distance_max: float = 0.20,
        ambiguity_margin: float = 0.05,
        support_contact_points: Optional[Mapping[str, Point]] = None,
        support_contact_tolerance: float = 0.08,
        display_evidence_mode: str = "mask_surface_overlap",
    ) -> None:
        if not camera_id or not runtime_instance_id or not products:
            raise ValueError("camera_id, runtime_instance_id, and products are required")
        _frame_size(frame_width, frame_height)
        for product_key, product_type in products.items():
            _product_key(product_key)
            _product_type(product_type)
        if len(set(products)) != len(products):
            raise ValueError("product_key must be unique")
        if product_track_lost_after_windows < 1:
            raise ValueError("product_track_lost_after_windows must be positive")
        if not 0 <= support_contact_tolerance <= 1:
            raise ValueError("support_contact_tolerance must be between 0 and 1")
        if display_evidence_mode not in {"mask_surface_overlap", "fixed_support_contact"}:
            raise ValueError("unsupported display_evidence_mode")
        self.camera_id = camera_id
        self.runtime_instance_id = runtime_instance_id
        self.products = dict(products)
        self.product_display_surface = product_display_surface
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.surface_overlap_min = surface_overlap_min
        self.product_track_lost_after_windows = product_track_lost_after_windows
        self.overlap_iou_min = overlap_iou_min
        self.geometry_distance_max = geometry_distance_max
        self.ambiguity_margin = ambiguity_margin
        self.support_contact_points = dict(support_contact_points or {})
        self.support_contact_tolerance = support_contact_tolerance
        self.display_evidence_mode = display_evidence_mode
        for product_key, point in self.support_contact_points.items():
            if product_key not in self.products:
                raise ValueError(f"support contact point has unknown product_key: {product_key}")
            if len(point) != 2 or not all(0 <= float(value) <= 1 for value in point):
                raise ValueError("support contact points must be normalized")
        if self.display_evidence_mode == "fixed_support_contact" and set(self.products) - set(self.support_contact_points):
            raise ValueError("fixed_support_contact requires approved support points")
        self._next_segment = {key: 1 for key in products}
        self._tracks: dict[str, dict[str, object]] = {}

    def initialize_assignments(
        self,
        *,
        assignments: Mapping[str, ProductCandidate],
        observed_at: datetime,
        source_seq: int,
        window_no: int = 0,
        grounding_candidate_count: Optional[int] = None,
    ) -> tuple[ProductWindowResult, ...]:
        if not assignments:
            raise ValueError("initialization assignments must not be empty")
        unknown = set(assignments) - set(self.products)
        if unknown:
            raise ValueError(f"unknown product_key: {sorted(unknown)}")
        local_ids = [candidate.local_obj_id for candidate in assignments.values()]
        if len(set(local_ids)) != len(local_ids):
            raise ValueError("one candidate cannot initialize multiple product_key values")
        credible = initialization_candidates(
            candidates=tuple(assignments.values()),
            product_display_surface=self.product_display_surface,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
            surface_overlap_min=self.surface_overlap_min,
        )
        if len(credible) != len(assignments):
            raise ValueError("all operator-confirmed candidates must pass the Surface gate")
        for product_key, candidate in assignments.items():
            support = self.support_contact_points.get(product_key)
            if self.display_evidence_mode == "fixed_support_contact" and support is not None:
                contact = _mask_support_contact(candidate.mask, self.frame_width, self.frame_height)
                if contact is None or hypot(contact[0] - support[0], contact[1] - support[1]) > self.support_contact_tolerance:
                    raise ValueError(f"candidate does not reach configured support contact point: {product_key}")
        blocked = {
            product_key: state["status"]
            for product_key in assignments
            if (
                (state := self._tracks.get(product_key)) is not None
                and state["status"] is not ProductTrackStatus.LOST
            )
        }
        if blocked:
            raise ValueError(
                "cannot create a parallel Product Track segment: "
                + ", ".join(
                    f"{key}={status.value}"
                    for key, status in sorted(blocked.items())
                )
            )

        # Plan every mutation before touching either runtime dictionary.  This
        # preserves batch atomicity when one target is ineligible.
        planned = {
            product_key: {
                "segment": self._next_segment[product_key],
                "token": (
                    f"product:{self.camera_id}:{self.runtime_instance_id}:"
                    f"{product_key}:{self._next_segment[product_key]}"
                ),
                "status": ProductTrackStatus.ACTIVE,
                "misses": 0,
                "candidate": candidate,
            }
            for product_key, candidate in assignments.items()
        }
        next_segments = {
            product_key: int(state["segment"]) + 1
            for product_key, state in planned.items()
        }
        self._tracks.update(planned)
        self._next_segment.update(next_segments)

        results = []
        count = grounding_candidate_count if grounding_candidate_count is not None else len(assignments)
        for product_key, candidate in assignments.items():
            results.append(self._observed_result(
                product_key=product_key,
                candidate=candidate,
                association=ProductAssociation(
                    product_key, candidate, "operator_confirmation", True, False
                ),
                observed_at=observed_at,
                source_seq=source_seq,
                window_no=window_no,
                grounding_candidate_count=count,
                candidate_count=len(assignments),
            ))
        return tuple(results)

    def process_window(
        self,
        *,
        candidates: Sequence[ProductCandidate],
        observed_at: datetime,
        source_seq: int,
        window_no: int,
        grounding_candidate_count: Optional[int] = None,
        processing_seconds: Optional[float] = None,
        fresh_lag: Optional[float] = None,
        association_references: Optional[Mapping[str, ProductCandidate]] = None,
    ) -> tuple[ProductWindowResult, ...]:
        active_candidates = {
            key: state["candidate"]
            for key, state in self._tracks.items()
            if state["status"] is not ProductTrackStatus.LOST
        }
        if association_references is not None:
            if set(association_references) != set(active_candidates):
                raise ValueError("association references must cover every active Product Track")
            active_candidates = dict(association_references)
        active_anchor_keys = tuple(
            key
            for key, state in self._tracks.items()
            if (
                state["status"] is ProductTrackStatus.ACTIVE
                and int(state["misses"]) == 0
            )
        )
        recovering_keys = tuple(
            key
            for key, state in self._tracks.items()
            if (
                state["status"] is ProductTrackStatus.MISSING_PENDING
                and int(state["misses"]) > 0
            )
        )
        associations = associate_candidates_with_active_anchors(
            previous_candidates=active_candidates,
            candidates=candidates,
            active_anchor_keys=active_anchor_keys,
            recovering_keys=recovering_keys,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
            overlap_iou_min=self.overlap_iou_min,
            geometry_distance_max=self.geometry_distance_max,
            ambiguity_margin=self.ambiguity_margin,
        )
        count = grounding_candidate_count if grounding_candidate_count is not None else len(candidates)
        results = []
        for association in associations:
            if association.success and association.candidate is not None:
                result = self._observed_result(
                    product_key=association.product_key,
                    candidate=association.candidate,
                    association=association,
                    observed_at=observed_at,
                    source_seq=source_seq,
                    window_no=window_no,
                    grounding_candidate_count=count,
                    candidate_count=len(candidates),
                )
            else:
                result = self._missed_result(
                    association=association,
                    observed_at=observed_at,
                    source_seq=source_seq,
                    window_no=window_no,
                    grounding_candidate_count=count,
                    candidate_count=len(candidates),
                )
            results.append(ProductWindowResult(
                **{
                    **result.__dict__,
                    "processing_seconds": processing_seconds,
                    "fresh_lag": fresh_lag,
                }
            ))
        return tuple(results)

    def observe_assignments(
        self,
        *,
        assignments: Mapping[str, ProductCandidate],
        observed_at: datetime,
        source_seq: int,
        window_no: int,
        grounding_candidate_count: int,
        candidate_count: int,
        association_method: str,
        processing_seconds: Optional[float] = None,
        fresh_lag: Optional[float] = None,
    ) -> tuple[ProductWindowResult, ...]:
        """Record propagated masks after the window-level identity assignment."""
        local_ids = [candidate.local_obj_id for candidate in assignments.values()]
        if len(set(local_ids)) != len(local_ids):
            raise ValueError("one candidate cannot be observed for multiple Product Tracks")
        results = []
        for product_key, candidate in assignments.items():
            state = self._tracks.get(product_key)
            if state is None or state["status"] is ProductTrackStatus.LOST:
                raise ValueError("propagated observation requires an active Product Track")
            result = self._observed_result(
                product_key=product_key,
                candidate=candidate,
                association=ProductAssociation(
                    product_key=product_key,
                    candidate=candidate,
                    method=association_method,
                    success=True,
                    ambiguous=False,
                ),
                observed_at=observed_at,
                source_seq=source_seq,
                window_no=window_no,
                grounding_candidate_count=grounding_candidate_count,
                candidate_count=candidate_count,
            )
            results.append(ProductWindowResult(**{
                **result.__dict__,
                "processing_seconds": processing_seconds,
                "fresh_lag": fresh_lag,
            }))
        return tuple(results)

    def status(self, product_key: str) -> Optional[ProductTrackStatus]:
        _product_key(product_key)
        state = self._tracks.get(product_key)
        return None if state is None else state["status"]

    def track_identity(self, product_key: str) -> Optional[tuple[int, str]]:
        _product_key(product_key)
        state = self._tracks.get(product_key)
        if state is None:
            return None
        return int(state["segment"]), str(state["token"])

    def _observed_result(
        self,
        *,
        product_key: str,
        candidate: ProductCandidate,
        association: ProductAssociation,
        observed_at: datetime,
        source_seq: int,
        window_no: int,
        grounding_candidate_count: int,
        candidate_count: int,
    ) -> ProductWindowResult:
        state = self._tracks[product_key]
        missed_windows = int(state["misses"])
        recovered = (
            state["status"] is ProductTrackStatus.MISSING_PENDING
            and missed_windows > 0
        )
        state["candidate"] = candidate
        state["misses"] = 0
        state["status"] = ProductTrackStatus.ACTIVE
        evidence = evaluate_candidate(
            candidate,
            self.product_display_surface,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
        )
        in_display = (
            evidence.centroid_inside_product_display_surface
            and evidence.surface_overlap_ratio >= self.surface_overlap_min
        )
        support = self.support_contact_points.get(product_key)
        support_contact = None
        support_distance = None
        if support is not None:
            support_contact = _mask_support_contact(candidate.mask, self.frame_width, self.frame_height)
            if support_contact is not None:
                support_distance = hypot(support_contact[0] - support[0], support_contact[1] - support[1])
            if self.display_evidence_mode == "fixed_support_contact":
                in_display = in_display and support_distance is not None and support_distance <= self.support_contact_tolerance
        if evidence.centroid_inside_product_display_surface is False or evidence.surface_overlap_ratio < self.surface_overlap_min:
            reason = "MASK_SURFACE_GATE_FAILED"
        elif self.display_evidence_mode == "fixed_support_contact" and support is None:
            reason = "SUPPORT_CONTACT_UNAVAILABLE"
        elif self.display_evidence_mode == "fixed_support_contact" and support_distance is None:
            reason = "SUPPORT_CONTACT_UNAVAILABLE"
        elif self.display_evidence_mode == "fixed_support_contact" and support_distance > self.support_contact_tolerance:
            reason = "SUPPORT_CONTACT_OUTSIDE_TOLERANCE"
        elif self.display_evidence_mode == "fixed_support_contact":
            reason = "FIXED_SUPPORT_CONTACT_CONFIRMED"
        else:
            reason = "MASK_SURFACE_GATE_CONFIRMED" if in_display else "MASK_SURFACE_GATE_FAILED"
        return ProductWindowResult(
            product_key=product_key,
            product_type=self.products[product_key],
            product_track_id=int(state["segment"]),
            product_track_token=str(state["token"]),
            local_obj_id=candidate.local_obj_id,
            grounding_candidate_count=grounding_candidate_count,
            candidate_count=candidate_count,
            bbox=tuple(float(value) for value in candidate.bbox),
            centroid=(
                candidate.centroid[0] / self.frame_width,
                candidate.centroid[1] / self.frame_height,
            ),
            mask_area=evidence.mask_area,
            surface_overlap_ratio=evidence.surface_overlap_ratio,
            display_state=(
                ProductDisplayState.IN_DISPLAY
                if in_display else ProductDisplayState.OUTSIDE_DISPLAY
            ),
            association_method=association.method,
            association_success=True,
            association_ambiguous=False,
            track_status=ProductTrackStatus.ACTIVE,
            consecutive_missed_windows=0,
            tracking_lost=False,
            observed_at=_utc_naive(observed_at),
            source_seq=source_seq,
            window_no=window_no,
            recovered_from_missing_pending=recovered,
            recovered_after_missed_windows=missed_windows if recovered else 0,
            association_role=association.association_role,
            anchor_locked_candidate_ids=association.anchor_locked_candidate_ids,
            recovery_attempted=association.recovery_attempted,
            recovery_candidate_count=association.recovery_candidate_count,
            recovery_success=association.recovery_success,
            recovery_rejected_reason=association.recovery_rejected_reason,
            recovery_latency_windows=1 if recovered else 0,
            association_mask_iou=association.mask_iou,
            association_centroid_distance_ratio=(
                association.centroid_distance_ratio
            ),
            display_evidence_mode=self.display_evidence_mode,
            current_support_contact_point=support_contact,
            approved_support_contact_point=support,
            support_contact_distance=support_distance,
            support_contact_tolerance=self.support_contact_tolerance if support is not None else None,
            display_state_reason=reason,
        )

    def _missed_result(
        self,
        *,
        association: ProductAssociation,
        observed_at: datetime,
        source_seq: int,
        window_no: int,
        grounding_candidate_count: int,
        candidate_count: int,
    ) -> ProductWindowResult:
        state = self._tracks[association.product_key]
        state["misses"] = int(state["misses"]) + 1
        state["status"] = (
            ProductTrackStatus.LOST
            if int(state["misses"]) >= self.product_track_lost_after_windows
            else ProductTrackStatus.MISSING_PENDING
        )
        return ProductWindowResult(
            product_key=association.product_key,
            product_type=self.products[association.product_key],
            product_track_id=int(state["segment"]),
            product_track_token=str(state["token"]),
            local_obj_id=None,
            grounding_candidate_count=grounding_candidate_count,
            candidate_count=candidate_count,
            bbox=None,
            centroid=None,
            mask_area=None,
            surface_overlap_ratio=None,
            display_state=ProductDisplayState.UNOBSERVED,
            association_method=association.method,
            association_success=False,
            association_ambiguous=association.ambiguous,
            track_status=state["status"],
            consecutive_missed_windows=int(state["misses"]),
            tracking_lost=state["status"] is ProductTrackStatus.LOST,
            observed_at=_utc_naive(observed_at),
            source_seq=source_seq,
            window_no=window_no,
            association_role=association.association_role,
            anchor_locked_candidate_ids=association.anchor_locked_candidate_ids,
            recovery_attempted=association.recovery_attempted,
            recovery_candidate_count=association.recovery_candidate_count,
            recovery_success=association.recovery_success,
            recovery_rejected_reason=association.recovery_rejected_reason,
            association_mask_iou=association.mask_iou,
            association_centroid_distance_ratio=(
                association.centroid_distance_ratio
            ),
        )


def _mask_iou(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        raise ValueError("association masks must use the same frame size")
    left_mask = left.astype(bool)
    right_mask = right.astype(bool)
    union = int(np.count_nonzero(left_mask | right_mask))
    if union == 0:
        return 0.0
    return int(np.count_nonzero(left_mask & right_mask)) / union


def _mask_support_contact(mask: np.ndarray, frame_width: int, frame_height: int) -> Optional[Point]:
    """Return the normalized lowest mask contact point for upright products."""
    ys, xs = np.where(np.asarray(mask).astype(bool))
    if len(xs) == 0:
        return None
    lowest_y = int(ys.max())
    lowest_x = float(np.median(xs[ys == lowest_y]))
    return lowest_x / frame_width, lowest_y / frame_height


def _association_edge(
    *,
    candidate_index: int,
    previous: ProductCandidate,
    candidate: ProductCandidate,
    frame_width: int,
    frame_height: int,
    overlap_iou_min: float,
    geometry_distance_max: float,
) -> Optional[_AssociationEdge]:
    iou = _mask_iou(previous.mask, candidate.mask)
    distance = hypot(
        previous.centroid[0] - candidate.centroid[0],
        previous.centroid[1] - candidate.centroid[1],
    ) / hypot(frame_width, frame_height)
    if iou >= overlap_iou_min:
        return _AssociationEdge(
            candidate_index, "overlap", 2.0 + iou, iou, distance
        )
    if distance <= geometry_distance_max:
        return _AssociationEdge(
            candidate_index,
            "geometry",
            1.0 + (1.0 - distance / geometry_distance_max),
            iou,
            distance,
        )
    return None


def pixel_xyxy_to_normalized_xywh(
    bbox: BBox, *, frame_width: int, frame_height: int
) -> list[list[float]]:
    _frame_size(frame_width, frame_height)
    x1, y1, x2, y2 = (float(value) for value in bbox)
    if not 0 <= x1 < x2 <= frame_width or not 0 <= y1 < y2 <= frame_height:
        raise ValueError("manual_init_bbox must fit the configured AI frame")
    return [[x1 / frame_width, y1 / frame_height, (x2 - x1) / frame_width, (y2 - y1) / frame_height]]


def _frame_size(width: int, height: int) -> None:
    if (width, height) != (1280, 720):
        raise ValueError("Stage 7C product frame must be 1280x720")


def _product_type(value: str) -> None:
    if value not in {"smart_toy", "smart_guitar"}:
        raise ValueError("unsupported product_type")


def _product_key(value: str) -> None:
    if not value or ":" in value:
        raise ValueError("product_key must be non-empty and cannot contain ':'")


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
