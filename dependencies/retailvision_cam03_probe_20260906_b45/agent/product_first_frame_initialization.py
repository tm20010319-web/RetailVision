"""Fail-closed first-frame initialization for dense Product Tracks.

This module owns only the initialization domain rules.  It does not open media,
create UI windows, connect to cameras, or publish behavior facts.  Callers inject
one session-scoped SAM adapter and commit the confirmed proposal through the
existing ``MultiProductTrackRuntime`` interface.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Optional, Protocol, Sequence

import cv2
import numpy as np

from agent.sam31_product_tracking import (
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplaySurface,
    ProductTrackStatus,
    ProductWindowResult,
    evaluate_candidate,
)


BBoxPixels = tuple[int, int, int, int]
PointPixels = tuple[int, int]


class InitializationFailureCode:
    FRAME_MISMATCH = "FRAME_MISMATCH"
    PRODUCT_KEY_INVALID = "PRODUCT_KEY_INVALID"
    PRODUCT_KEY_DUPLICATE = "PRODUCT_KEY_DUPLICATE"
    TARGET_COUNT_OUT_OF_POLICY = "TARGET_COUNT_OUT_OF_POLICY"
    BBOX_OUT_OF_BOUNDS = "BBOX_OUT_OF_BOUNDS"
    BBOX_TOO_LOOSE = "BBOX_TOO_LOOSE"
    POSITIVE_POINT_MISS = "POSITIVE_POINT_MISS"
    NEGATIVE_POINT_HIT = "NEGATIVE_POINT_HIT"
    MASK_EMPTY = "MASK_EMPTY"
    MASK_MULTI_INSTANCE = "MASK_MULTI_INSTANCE"
    MASK_OUTSIDE_SURFACE = "MASK_OUTSIDE_SURFACE"
    PAIRWISE_MASK_CONFLICT = "PAIRWISE_MASK_CONFLICT"
    CANDIDATE_REUSED = "CANDIDATE_REUSED"
    CANDIDATE_AMBIGUOUS = "CANDIDATE_AMBIGUOUS"
    MASK_NOT_CONFIRMED = "MASK_NOT_CONFIRMED"
    MASK_HASH_MISMATCH = "MASK_HASH_MISMATCH"
    TRACK_NOT_LOST = "TRACK_NOT_LOST"
    COORDINATE_SPACE_MISMATCH = "COORDINATE_SPACE_MISMATCH"


class DenseProductInitializationError(ValueError):
    """Stable fail-closed error carrying machine-readable failure codes."""

    def __init__(
        self,
        *codes: str,
        diagnostics: Optional[Mapping[str, object]] = None,
    ) -> None:
        values = tuple(dict.fromkeys(str(code) for code in codes if code))
        if not values:
            raise ValueError("at least one initialization failure code is required")
        self.codes = values
        self.diagnostics = dict(diagnostics or {})
        super().__init__(",".join(values))


@dataclass(frozen=True)
class InitializationFrameRef:
    analysis_run_id: str
    source_seq: int
    source_pts: int
    time_base_num: int
    time_base_den: int
    presentation_seconds: float
    frame_sha256: str
    frame_size: tuple[int, int]
    roi_xyxy: Optional[BBoxPixels]
    coordinate_transform_version: str

    def __post_init__(self) -> None:
        if not self.analysis_run_id:
            raise ValueError("analysis_run_id is required")
        if isinstance(self.source_seq, bool) or not isinstance(self.source_seq, int) or self.source_seq < 0:
            raise ValueError("source_seq must be a non-negative integer")
        if isinstance(self.source_pts, bool) or not isinstance(self.source_pts, int):
            raise ValueError("source_pts must be an integer")
        if self.time_base_num <= 0 or self.time_base_den <= 0:
            raise ValueError("time base must be positive")
        if not math.isfinite(float(self.presentation_seconds)):
            raise ValueError("presentation_seconds must be finite")
        if len(self.frame_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.frame_sha256.lower()
        ):
            raise ValueError("frame_sha256 must be a SHA-256 hex digest")
        width, height = self.frame_size
        if width <= 0 or height <= 0:
            raise ValueError("frame_size must be positive")
        if not self.coordinate_transform_version:
            raise ValueError("coordinate_transform_version is required")
        if self.roi_xyxy is not None:
            _validate_bbox(self.roi_xyxy, width=width, height=height)

    @property
    def authoritative_seconds(self) -> float:
        return self.source_pts * self.time_base_num / self.time_base_den

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_run_id": self.analysis_run_id,
            "source_seq": self.source_seq,
            "source_pts": self.source_pts,
            "time_base": {
                "num": self.time_base_num,
                "den": self.time_base_den,
            },
            "presentation_seconds": self.presentation_seconds,
            "frame_sha256": self.frame_sha256,
            "frame_size": list(self.frame_size),
            "roi_xyxy": None if self.roi_xyxy is None else list(self.roi_xyxy),
            "coordinate_transform_version": self.coordinate_transform_version,
        }


@dataclass(frozen=True)
class TargetSelection:
    product_key: str
    bbox_xyxy: BBoxPixels
    positive_points: tuple[PointPixels, ...]
    negative_points: tuple[PointPixels, ...] = ()
    catalog_reference_image: Optional[str] = None
    reference_slot: Optional[str] = None
    initialization_hint: Optional[str] = None


@dataclass(frozen=True)
class InstancePromptResult:
    candidate_id: int
    masks: tuple[np.ndarray, ...]
    session_id: str
    bbox: Optional[BBoxPixels] = None
    confidence: float = 1.0
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            isinstance(self.candidate_id, bool)
            or not isinstance(self.candidate_id, int)
            or self.candidate_id < 0
        ):
            raise ValueError("candidate_id must be a non-negative integer")
        if not self.session_id:
            raise ValueError("session_id is required")


class InstancePromptAdapter(Protocol):
    """One stateful adapter instance represents one SAM initialization session."""

    def add_instance_prompt(
        self,
        *,
        frame: np.ndarray,
        frame_index: int,
        bbox: BBoxPixels,
        points: tuple[PointPixels, ...],
        point_labels: tuple[int, ...],
        obj_id: int,
    ) -> InstancePromptResult:
        ...


@dataclass(frozen=True)
class DenseInitializationPolicy:
    approved_product_keys: frozenset[str]
    product_display_surface: ProductDisplaySurface
    frame_size: tuple[int, int] = (1280, 720)
    minimum_target_count: int = 3
    maximum_target_count: int = 5
    surface_overlap_min: float = 0.50
    bbox_mask_fill_min: float = 0.10
    bbox_frame_area_max: float = 0.35
    component_noise_area_min: int = 16
    component_significant_ratio_min: float = 0.05
    pairwise_mask_overlap_max: float = 0.10

    def __post_init__(self) -> None:
        if not self.approved_product_keys:
            raise ValueError("approved_product_keys is required")
        if not 1 <= self.minimum_target_count <= self.maximum_target_count:
            raise ValueError("target-count policy is invalid")
        for value, name in (
            (self.surface_overlap_min, "surface_overlap_min"),
            (self.bbox_mask_fill_min, "bbox_mask_fill_min"),
            (self.bbox_frame_area_max, "bbox_frame_area_max"),
            (self.component_significant_ratio_min, "component_significant_ratio_min"),
            (self.pairwise_mask_overlap_max, "pairwise_mask_overlap_max"),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if self.component_noise_area_min < 0:
            raise ValueError("component_noise_area_min must be non-negative")

    def thresholds_dict(self) -> dict[str, object]:
        return {
            "minimum_target_count": self.minimum_target_count,
            "maximum_target_count": self.maximum_target_count,
            "surface_overlap_min": self.surface_overlap_min,
            "bbox_mask_fill_min": self.bbox_mask_fill_min,
            "bbox_frame_area_max": self.bbox_frame_area_max,
            "component_noise_area_min": self.component_noise_area_min,
            "component_significant_ratio_min": self.component_significant_ratio_min,
            "pairwise_mask_overlap_max": self.pairwise_mask_overlap_max,
        }


@dataclass(frozen=True)
class MaskProposal:
    product_key: str
    candidate_id: int
    mask: np.ndarray
    bbox: BBoxPixels
    mask_sha256: str
    diagnostics: Mapping[str, object]
    confirmation_required: bool = True


@dataclass(frozen=True)
class InitializationProposal:
    frame_ref: InitializationFrameRef
    selected_product_keys: tuple[str, ...]
    masks: tuple[MaskProposal, ...]
    thresholds: Mapping[str, object]
    failure_codes: tuple[str, ...]
    diagnostics: Mapping[str, object]
    status: str


@dataclass(frozen=True)
class MaskConfirmation:
    product_key: str
    candidate_id: int
    frame_sha256: str
    mask_sha256: str
    status: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        if self.status not in {"confirmed", "rejected"}:
            raise ValueError("confirmation status must be confirmed or rejected")
        if self.confirmed_at.tzinfo is None:
            raise ValueError("confirmed_at must include a timezone")


@dataclass(frozen=True)
class InitializationResult:
    proposal: InitializationProposal
    confirmations: tuple[MaskConfirmation, ...]
    tracks: tuple[ProductWindowResult, ...]
    status: str = "COMMITTED"


class FakeInstancePromptAdapter:
    """Deterministic session-scoped adapter for unit tests and dry runs."""

    def __init__(
        self,
        results: Mapping[int, InstancePromptResult],
        *,
        session_id: str = "fake-dense-initialization-session",
    ) -> None:
        self.results = dict(results)
        self.session_id = session_id
        self.calls: list[dict[str, object]] = []

    def add_instance_prompt(
        self,
        *,
        frame: np.ndarray,
        frame_index: int,
        bbox: BBoxPixels,
        points: tuple[PointPixels, ...],
        point_labels: tuple[int, ...],
        obj_id: int,
    ) -> InstancePromptResult:
        self.calls.append({
            "frame_shape": tuple(frame.shape),
            "frame_index": frame_index,
            "bbox": bbox,
            "points": points,
            "point_labels": point_labels,
            "obj_id": obj_id,
        })
        if obj_id not in self.results:
            return InstancePromptResult(obj_id, (), self.session_id)
        result = self.results[obj_id]
        return InstancePromptResult(
            candidate_id=result.candidate_id,
            masks=result.masks,
            session_id=result.session_id or self.session_id,
            bbox=result.bbox,
            confidence=result.confidence,
            diagnostics=result.diagnostics,
        )


def frame_sha256(frame: np.ndarray) -> str:
    if not isinstance(frame, np.ndarray) or frame.ndim not in {2, 3}:
        raise ValueError("frame must be a two- or three-dimensional ndarray")
    digest = hashlib.sha256()
    digest.update(str(frame.dtype).encode("ascii"))
    digest.update(np.asarray(frame.shape, dtype=np.int64).tobytes())
    digest.update(np.ascontiguousarray(frame).tobytes())
    return digest.hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    binary = np.ascontiguousarray(mask.astype(bool))
    digest = hashlib.sha256()
    digest.update(np.asarray(binary.shape, dtype=np.int64).tobytes())
    digest.update(np.packbits(binary, bitorder="little").tobytes())
    return digest.hexdigest()


def build_proposal(
    frame_ref: InitializationFrameRef,
    target_selections: Sequence[TargetSelection],
    sam_adapter: InstancePromptAdapter,
    *,
    frame: np.ndarray,
    policy: DenseInitializationPolicy,
) -> InitializationProposal:
    """Build an immutable, uncommitted proposal from one authoritative frame."""

    failures: list[str] = []
    diagnostics: dict[str, object] = {
        "frame": frame_ref.to_dict(),
        "targets": [],
        "pairwise_overlap_diagnostics": [],
    }
    width, height = policy.frame_size
    if frame.shape[:2] != (height, width) or frame_ref.frame_size != policy.frame_size:
        failures.append(InitializationFailureCode.COORDINATE_SPACE_MISMATCH)
    if frame_sha256(frame) != frame_ref.frame_sha256:
        failures.append(InitializationFailureCode.FRAME_MISMATCH)

    selections = tuple(target_selections)
    if not policy.minimum_target_count <= len(selections) <= policy.maximum_target_count:
        failures.append(InitializationFailureCode.TARGET_COUNT_OUT_OF_POLICY)
    keys = tuple(item.product_key for item in selections)
    if len(set(keys)) != len(keys):
        failures.append(InitializationFailureCode.PRODUCT_KEY_DUPLICATE)
    if any(key not in policy.approved_product_keys for key in keys):
        failures.append(InitializationFailureCode.PRODUCT_KEY_INVALID)

    for selection in selections:
        target_failures: list[str] = []
        try:
            _validate_bbox(selection.bbox_xyxy, width=width, height=height)
        except ValueError:
            target_failures.append(InitializationFailureCode.BBOX_OUT_OF_BOUNDS)
        if not selection.positive_points:
            target_failures.append(InitializationFailureCode.POSITIVE_POINT_MISS)
        for point in selection.positive_points + selection.negative_points:
            if not _point_in_frame(point, width=width, height=height):
                target_failures.append(InitializationFailureCode.COORDINATE_SPACE_MISMATCH)
        if selection.positive_points and not all(
            _point_in_bbox(point, selection.bbox_xyxy)
            for point in selection.positive_points
        ):
            target_failures.append(InitializationFailureCode.POSITIVE_POINT_MISS)
        failures.extend(target_failures)

    proposals: list[MaskProposal] = []
    session_ids: set[str] = set()
    candidate_ids: set[int] = set()
    if not failures:
        for obj_id, selection in enumerate(selections, start=1):
            points = selection.positive_points + selection.negative_points
            labels = (1,) * len(selection.positive_points) + (0,) * len(selection.negative_points)
            response = sam_adapter.add_instance_prompt(
                frame=frame,
                # This adapter session is materialized from exactly one selected
                # video frame.  source_seq remains provenance, not a SAM-local
                # frame index.
                frame_index=0,
                bbox=selection.bbox_xyxy,
                points=points,
                point_labels=labels,
                obj_id=obj_id,
            )
            session_ids.add(response.session_id)
            target_diagnostics: dict[str, object] = {
                "product_key": selection.product_key,
                "candidate_id": response.candidate_id,
                "bbox_xyxy": list(selection.bbox_xyxy),
                "positive_points": [list(item) for item in selection.positive_points],
                "negative_points": [list(item) for item in selection.negative_points],
                "catalog_reference_image": selection.catalog_reference_image,
                "reference_slot": selection.reference_slot,
                "initialization_hint": selection.initialization_hint,
                "sam_diagnostics": dict(response.diagnostics),
                "failure_codes": [],
            }
            target_failures = target_diagnostics["failure_codes"]
            assert isinstance(target_failures, list)
            if len(response.masks) == 0:
                target_failures.append(InitializationFailureCode.MASK_EMPTY)
            elif len(response.masks) != 1:
                target_failures.append(InitializationFailureCode.CANDIDATE_AMBIGUOUS)
            if response.candidate_id in candidate_ids:
                target_failures.append(InitializationFailureCode.CANDIDATE_REUSED)
            candidate_ids.add(response.candidate_id)
            if target_failures:
                failures.extend(target_failures)
                diagnostics["targets"].append(target_diagnostics)  # type: ignore[union-attr]
                continue

            mask = np.asarray(response.masks[0]).astype(bool)
            if mask.shape != (height, width):
                target_failures.append(InitializationFailureCode.COORDINATE_SPACE_MISMATCH)
                failures.extend(target_failures)
                diagnostics["targets"].append(target_diagnostics)  # type: ignore[union-attr]
                continue
            area = int(np.count_nonzero(mask))
            if area == 0:
                target_failures.append(InitializationFailureCode.MASK_EMPTY)
            for point in selection.positive_points:
                if not bool(mask[point[1], point[0]]):
                    target_failures.append(InitializationFailureCode.POSITIVE_POINT_MISS)
            for point in selection.negative_points:
                if bool(mask[point[1], point[0]]):
                    target_failures.append(InitializationFailureCode.NEGATIVE_POINT_HIT)

            component_diagnostics = _component_diagnostics(mask, policy)
            target_diagnostics["component_diagnostics"] = component_diagnostics
            if int(component_diagnostics["significant_component_count"]) > 1:
                target_failures.append(InitializationFailureCode.MASK_MULTI_INSTANCE)

            bbox = _mask_bbox(mask) or selection.bbox_xyxy
            bbox_area = _bbox_area(selection.bbox_xyxy)
            frame_area = width * height
            fill_ratio = area / bbox_area if bbox_area else 0.0
            frame_ratio = bbox_area / frame_area
            target_diagnostics.update({
                "mask_area": area,
                "mask_bbox": list(bbox),
                "sam_bbox": None if response.bbox is None else list(response.bbox),
                "bbox_mask_fill_ratio": fill_ratio,
                "bbox_frame_area_ratio": frame_ratio,
                "bbox_mask_fill_min": policy.bbox_mask_fill_min,
                "bbox_frame_area_max": policy.bbox_frame_area_max,
            })
            if (
                fill_ratio < policy.bbox_mask_fill_min
                or frame_ratio > policy.bbox_frame_area_max
            ):
                target_failures.append(InitializationFailureCode.BBOX_TOO_LOOSE)

            centroid = _mask_centroid(mask)
            candidate = ProductCandidate(
                local_obj_id=response.candidate_id,
                bbox=tuple(float(value) for value in bbox),
                centroid=centroid,
                confidence=float(response.confidence),
                mask=mask,
            )
            evidence = evaluate_candidate(
                candidate,
                policy.product_display_surface,
                frame_width=width,
                frame_height=height,
            )
            target_diagnostics.update({
                "surface_overlap": evidence.surface_overlap_ratio,
                "centroid_inside_product_display_surface": (
                    evidence.centroid_inside_product_display_surface
                ),
                "surface_overlap_min": policy.surface_overlap_min,
            })
            if (
                not evidence.centroid_inside_product_display_surface
                or evidence.surface_overlap_ratio < policy.surface_overlap_min
            ):
                target_failures.append(InitializationFailureCode.MASK_OUTSIDE_SURFACE)

            failures.extend(target_failures)
            diagnostics["targets"].append(target_diagnostics)  # type: ignore[union-attr]
            if not target_failures:
                proposals.append(MaskProposal(
                    product_key=selection.product_key,
                    candidate_id=response.candidate_id,
                    mask=mask,
                    bbox=bbox,
                    mask_sha256=mask_sha256(mask),
                    diagnostics=target_diagnostics,
                ))

    if len(session_ids) > 1:
        failures.append(InitializationFailureCode.FRAME_MISMATCH)
        diagnostics["sam_session_ids"] = sorted(session_ids)

    for left_index, left in enumerate(proposals):
        for right in proposals[left_index + 1 :]:
            intersection = int(np.count_nonzero(left.mask & right.mask))
            smaller = min(
                int(np.count_nonzero(left.mask)),
                int(np.count_nonzero(right.mask)),
            )
            overlap = intersection / smaller if smaller else 0.0
            row = {
                "left_product_key": left.product_key,
                "right_product_key": right.product_key,
                "intersection_area": intersection,
                "smaller_mask_overlap_ratio": overlap,
                "maximum_allowed": policy.pairwise_mask_overlap_max,
            }
            diagnostics["pairwise_overlap_diagnostics"].append(row)  # type: ignore[union-attr]
            if overlap > policy.pairwise_mask_overlap_max:
                failures.append(InitializationFailureCode.PAIRWISE_MASK_CONFLICT)

    unique_failures = tuple(dict.fromkeys(failures))
    status = "READY_FOR_CONFIRMATION" if not unique_failures else "REJECTED"
    return InitializationProposal(
        frame_ref=frame_ref,
        selected_product_keys=keys,
        masks=tuple(proposals) if not unique_failures else (),
        thresholds=policy.thresholds_dict(),
        failure_codes=unique_failures,
        diagnostics=diagnostics,
        status=status,
    )


def commit_confirmed(
    proposal: InitializationProposal,
    mask_confirmations: Sequence[MaskConfirmation],
    runtime: MultiProductTrackRuntime,
) -> InitializationResult:
    """Atomically create Product Track segments after exact human confirmation."""

    if proposal.failure_codes or proposal.status != "READY_FOR_CONFIRMATION":
        raise DenseProductInitializationError(*(
            proposal.failure_codes or (InitializationFailureCode.CANDIDATE_AMBIGUOUS,)
        ))
    confirmations = tuple(mask_confirmations)
    by_key = {item.product_key: item for item in confirmations}
    if len(by_key) != len(confirmations) or set(by_key) != set(proposal.selected_product_keys):
        raise DenseProductInitializationError(InitializationFailureCode.MASK_NOT_CONFIRMED)
    proposals = {item.product_key: item for item in proposal.masks}
    if (
        len(proposals) != len(proposal.masks)
        or set(proposals) != set(proposal.selected_product_keys)
    ):
        raise DenseProductInitializationError(
            InitializationFailureCode.CANDIDATE_AMBIGUOUS
        )
    if set(proposal.selected_product_keys) - set(runtime.products):
        raise DenseProductInitializationError(
            InitializationFailureCode.PRODUCT_KEY_INVALID
        )
    for product_key in proposal.selected_product_keys:
        confirmation = by_key[product_key]
        mask = proposals[product_key]
        if confirmation.status != "confirmed":
            raise DenseProductInitializationError(InitializationFailureCode.MASK_NOT_CONFIRMED)
        if (
            mask_sha256(mask.mask) != mask.mask_sha256
            or confirmation.candidate_id != mask.candidate_id
            or confirmation.frame_sha256 != proposal.frame_ref.frame_sha256
            or confirmation.mask_sha256 != mask.mask_sha256
        ):
            raise DenseProductInitializationError(InitializationFailureCode.MASK_HASH_MISMATCH)
        status = runtime.status(product_key)
        if status is not None and status is not ProductTrackStatus.LOST:
            raise DenseProductInitializationError(
                InitializationFailureCode.TRACK_NOT_LOST,
                diagnostics={"product_key": product_key, "track_status": status.value},
            )

    assignments = {
        item.product_key: ProductCandidate(
            local_obj_id=item.candidate_id,
            bbox=tuple(float(value) for value in item.bbox),
            centroid=_mask_centroid(item.mask),
            confidence=1.0,
            mask=item.mask,
        )
        for item in proposal.masks
    }
    tracks = runtime.initialize_assignments(
        assignments=assignments,
        observed_at=datetime.fromtimestamp(
            proposal.frame_ref.authoritative_seconds,
            tz=timezone.utc,
        ),
        source_seq=proposal.frame_ref.source_seq,
        window_no=0,
        grounding_candidate_count=len(assignments),
    )
    return InitializationResult(proposal, confirmations, tracks)


def map_roi_selection_to_full_frame(
    selection: TargetSelection,
    *,
    roi_xyxy: BBoxPixels,
    frame_size: tuple[int, int],
) -> TargetSelection:
    width, height = frame_size
    _validate_bbox(roi_xyxy, width=width, height=height)
    offset_x, offset_y = roi_xyxy[:2]
    roi_width = roi_xyxy[2] - offset_x
    roi_height = roi_xyxy[3] - offset_y
    _validate_bbox(selection.bbox_xyxy, width=roi_width, height=roi_height)

    def point(value: PointPixels) -> PointPixels:
        if not _point_in_frame(value, width=roi_width, height=roi_height):
            raise DenseProductInitializationError(
                InitializationFailureCode.COORDINATE_SPACE_MISMATCH
            )
        return value[0] + offset_x, value[1] + offset_y

    x1, y1, x2, y2 = selection.bbox_xyxy
    return TargetSelection(
        product_key=selection.product_key,
        bbox_xyxy=(x1 + offset_x, y1 + offset_y, x2 + offset_x, y2 + offset_y),
        positive_points=tuple(point(value) for value in selection.positive_points),
        negative_points=tuple(point(value) for value in selection.negative_points),
        catalog_reference_image=selection.catalog_reference_image,
        reference_slot=selection.reference_slot,
        initialization_hint=selection.initialization_hint,
    )


def restore_roi_mask(
    roi_mask: np.ndarray,
    *,
    roi_xyxy: BBoxPixels,
    frame_size: tuple[int, int],
) -> np.ndarray:
    width, height = frame_size
    _validate_bbox(roi_xyxy, width=width, height=height)
    x1, y1, x2, y2 = roi_xyxy
    if roi_mask.shape != (y2 - y1, x2 - x1):
        raise DenseProductInitializationError(
            InitializationFailureCode.COORDINATE_SPACE_MISMATCH
        )
    result = np.zeros((height, width), dtype=bool)
    result[y1:y2, x1:x2] = roi_mask.astype(bool)
    return result


def proposal_diagnostic_payload(
    proposal: InitializationProposal,
    *,
    camera_id: str,
    camera_config_version: str,
) -> dict[str, object]:
    return {
        "schema_version": "dpi-01a.v1",
        **proposal.frame_ref.to_dict(),
        "camera_id": camera_id,
        "camera_config_version": camera_config_version,
        "selected_product_keys": list(proposal.selected_product_keys),
        "assignments": [
            {
                "product_key": item.product_key,
                "candidate_id": item.candidate_id,
                "bbox_xyxy": list(item.bbox),
                "mask_area": int(np.count_nonzero(item.mask)),
                "mask_sha256": item.mask_sha256,
                "diagnostics": dict(item.diagnostics),
                "mask_confirmation": "required",
            }
            for item in proposal.masks
        ],
        "failure_codes": list(proposal.failure_codes),
        "thresholds": dict(proposal.thresholds),
        "status": proposal.status,
        "diagnostics": dict(proposal.diagnostics),
    }


def result_mapping_payload(
    result: InitializationResult,
    *,
    camera_id: str,
    camera_config_version: str,
    mask_references: Optional[Mapping[str, str]] = None,
) -> dict[str, object]:
    tracks = {item.product_key: item for item in result.tracks}
    confirmations = {item.product_key: item for item in result.confirmations}
    masks = {item.product_key: item for item in result.proposal.masks}
    return {
        "schema_version": "dpi-01a.v1",
        **result.proposal.frame_ref.to_dict(),
        "camera_id": camera_id,
        "camera_config_version": camera_config_version,
        "selected_product_keys": list(result.proposal.selected_product_keys),
        "assignments": [
            {
                "product_key": key,
                "candidate_id": masks[key].candidate_id,
                "product_track_token": tracks[key].product_track_token,
                "bbox_xyxy": list(masks[key].bbox),
                "positive_points": masks[key].diagnostics.get("positive_points", []),
                "negative_points": masks[key].diagnostics.get("negative_points", []),
                "mask_area": int(np.count_nonzero(masks[key].mask)),
                "mask_sha256": masks[key].mask_sha256,
                "mask_reference": None if mask_references is None else mask_references.get(key),
                "surface_overlap": masks[key].diagnostics.get("surface_overlap"),
                "component_diagnostics": masks[key].diagnostics.get("component_diagnostics"),
                "pairwise_overlap_diagnostics": [
                    row
                    for row in result.proposal.diagnostics.get(
                        "pairwise_overlap_diagnostics", []
                    )
                    if key in {row.get("left_product_key"), row.get("right_product_key")}
                ],
                "mask_confirmation": {
                    "status": confirmations[key].status,
                    "confirmed_at": confirmations[key].confirmed_at.isoformat(),
                },
            }
            for key in result.proposal.selected_product_keys
        ],
        "failure_codes": [],
        "thresholds": dict(result.proposal.thresholds),
        "status": result.status,
    }


def _component_diagnostics(
    mask: np.ndarray,
    policy: DenseInitializationPolicy,
) -> dict[str, object]:
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8),
        connectivity=8,
    )
    areas = sorted((int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count)), reverse=True)
    total = int(np.count_nonzero(mask))
    threshold = max(
        policy.component_noise_area_min,
        int(math.ceil(total * policy.component_significant_ratio_min)),
    )
    significant = [area for area in areas if area >= threshold]
    return {
        "component_areas": areas,
        "component_area_ratios": [area / total if total else 0.0 for area in areas],
        "significant_area_threshold": threshold,
        "significant_component_count": len(significant),
        "ignored_noise_component_count": len(areas) - len(significant),
    }


def _mask_bbox(mask: np.ndarray) -> Optional[BBoxPixels]:
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _mask_centroid(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.where(mask)
    if not len(xs):
        raise DenseProductInitializationError(InitializationFailureCode.MASK_EMPTY)
    return float(np.mean(xs)), float(np.mean(ys))


def _validate_bbox(bbox: BBoxPixels, *, width: int, height: int) -> None:
    if len(bbox) != 4:
        raise ValueError("bbox must contain four coordinates")
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("bbox must fit the full AI frame")


def _bbox_area(bbox: BBoxPixels) -> int:
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def _point_in_frame(point: PointPixels, *, width: int, height: int) -> bool:
    return 0 <= point[0] < width and 0 <= point[1] < height


def _point_in_bbox(point: PointPixels, bbox: BBoxPixels) -> bool:
    return bbox[0] <= point[0] < bbox[2] and bbox[1] <= point[1] < bbox[3]
