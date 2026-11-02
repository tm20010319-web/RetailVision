"""Ultralytics ByteTrack, staff-vest, and pose adapter for BehaviorMonitor."""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
import warnings
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence

import cv2
import numpy as np

from agent.behavior.behavior_monitor import (
    BehaviorMonitorConfig,
    Point,
    PolygonArea,
    PosePoint,
    TrackObservation,
)
from agent.platform.overlay_text import (
    OverlayText,
    draw_overlay_texts_bgr,
    measure_overlay_text,
)


logger = logging.getLogger(__name__)


BBox = tuple[float, float, float, float]


_OVERLAY_VALUE_ZH = {
    "ENTRY_TRIGGER": "入店触发区",
    "GREETING_AREA": "迎宾区",
    "OUTSIDE_ROI": "业务区域外",
    "NOT_ELIGIBLE": "不符合迎宾条件",
    "ENTRY_CONFIRMED": "入店已确认",
    "WAITING_FOR_GREETING": "等待迎宾",
    "GREETING_IN_PROGRESS": "迎宾进行中",
    "GREETED": "迎宾已完成",
    "NOT_OBSERVED": "未观察到迎宾",
    "CANCELLED": "已取消",
    "YES": "是",
    "NO": "否",
}


def localize_staff_greeting_overlay_line(line: str) -> str:
    """Translate a stable debug line for the Chinese video overlay only."""

    if not isinstance(line, str):
        raise TypeError("overlay line must be a string")
    prefix, separator, value = line.partition(": ")
    if not separator:
        return line
    prefix_zh = {
        "FOOT": "落脚点",
        "WORLD": "世界坐标",
        "AREA": "所在区域",
        "GREETING STATE": "迎宾状态",
        "WAITING": "顾客等待迎宾",
        "DISTANCE": "人员距离",
        "STAFF TRAVEL": "店员移动距离",
        "DISTANCE REDUCTION": "距离缩短",
        "SUSTAINED": "持续时长",
    }.get(prefix)
    if prefix_zh is None:
        return line
    return f"{prefix_zh}：{_OVERLAY_VALUE_ZH.get(value, value)}"


class CameraRole(str, Enum):
    RECEPTION_ENTRY = "reception_entry"
    CUSTOMER_BEHAVIOR = "customer_behavior"
    SMART_CABIN = "smart_cabin"


GREETING_PROFILES: dict[str, dict[str, float | int]] = {
    "controlled_demo": {
        "greeting_window_seconds": 30.0,
        "greeting_staff_travel_metres": 0.25,
        "greeting_duration_seconds": 1.0,
        "greeting_distance_metres": 1.2,
        "minimum_distance_reduction_metres": 0.35,
        "greeting_area_exit_grace_seconds": 0.8,
        "greeting_area_enter_stable_frames": 3,
        "greeting_area_exit_stable_frames": 5,
    },
    "demo_relaxed": {
        "greeting_window_seconds": 30.0,
        "greeting_staff_travel_metres": 0.10,
        "greeting_staff_toward_customer_metres": 0.05,
        "greeting_duration_seconds": 0.5,
        # Demo intent accepts a wider interaction corridor than the
        # calibrated production greeting area. The monitor still requires
        # a real staff track and sustained movement before confirming.
        "greeting_distance_metres": 2.0,
        "minimum_distance_reduction_metres": 0.05,
        "greeting_trend_window_samples": 5,
        "greeting_trend_minimum_samples": 5,
        "greeting_trend_minimum_reduction_metres": 0.05,
        "greeting_trend_decreasing_ratio": 0.5,
        "confirmed_confidence": 0.5,
        "greeting_area_exit_grace_seconds": 0.8,
        "greeting_area_enter_stable_frames": 3,
        "greeting_area_exit_stable_frames": 5,
    },
    "production_candidate": {
        "greeting_staff_travel_metres": 0.40,
        "greeting_duration_seconds": 1.5,
        "greeting_distance_metres": 1.2,
        "minimum_distance_reduction_metres": 0.40,
        "greeting_area_exit_grace_seconds": 0.5,
    },
}
_GREETING_PROFILE_FIELDS = frozenset(
    {
        "greeting_window_seconds",
        "greeting_staff_travel_metres",
        "greeting_staff_toward_customer_metres",
        "greeting_duration_seconds",
        "greeting_distance_metres",
        "minimum_distance_reduction_metres",
        "greeting_trend_window_samples",
        "greeting_trend_minimum_samples",
        "greeting_trend_minimum_reduction_metres",
        "greeting_trend_decreasing_ratio",
        "confirmed_confidence",
        "greeting_area_exit_grace_seconds",
        "greeting_area_enter_stable_frames",
        "greeting_area_exit_stable_frames",
    }
)


@dataclass(frozen=True)
class VestDetection:
    bbox: BBox
    confidence: float
    class_name: str

    def __post_init__(self) -> None:
        if (
            len(self.bbox) != 4
            or not all(
                isinstance(value, Real)
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in self.bbox
            )
            or self.bbox[2] <= self.bbox[0]
            or self.bbox[3] <= self.bbox[1]
        ):
            raise ValueError("vest bbox must contain four finite coordinates")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, Real)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("vest confidence must be between 0 and 1")
        if not isinstance(self.class_name, str) or not self.class_name.strip():
            raise ValueError("vest class_name is required")


class BlueVestDetector:
    """Run staff-vest inference without creating or updating person tracks."""

    def __init__(
        self,
        *,
        model_path: Optional[str] = None,
        model=None,
        device: str = "cuda:0",
        confidence_threshold: float = 0.68,
    ) -> None:
        if (
            isinstance(confidence_threshold, bool)
            or not isinstance(confidence_threshold, Real)
            or not math.isfinite(confidence_threshold)
            or not 0 <= confidence_threshold <= 1
        ):
            raise ValueError("vest confidence threshold must be between 0 and 1")
        if model is None:
            if not model_path:
                raise ValueError("staff vest model path is required")
            from ultralytics import YOLO

            model = YOLO(model_path)
        self.model = model
        self.device = device
        self.confidence_threshold = float(confidence_threshold)

    def detect(self, frame) -> list[VestDetection]:
        results = self.model.predict(
            frame,
            device=self.device,
            conf=self.confidence_threshold,
            verbose=False,
        )
        if not results:
            return []
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        names = getattr(result, "names", {})
        detections = []
        for bbox, confidence, class_id in zip(
            _values(boxes.xyxy),
            _values(boxes.conf),
            _values(boxes.cls),
        ):
            confidence = float(confidence)
            class_name = _class_name(names, int(class_id))
            if class_name != "staff_vest":
                continue
            detection = VestDetection(
                bbox=tuple(float(value) for value in bbox),
                confidence=confidence,
                class_name=class_name,
            )
            if detection.confidence >= self.confidence_threshold:
                detections.append(detection)
        return detections


@dataclass(frozen=True)
class PersonTrackDetection:
    track_id: int
    track_token: str
    bbox: BBox
    confidence: float
    is_frame_clipped: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.track_id, bool) or self.track_id < 0:
            raise ValueError("track_id must be a non-negative integer")
        if not self.track_token.strip():
            raise ValueError("track_token is required")
        _validate_bbox(self.bbox, "person bbox")
        _validate_confidence(self.confidence, "person confidence")
        if not isinstance(self.is_frame_clipped, bool):
            raise ValueError("is_frame_clipped must be a boolean")


@dataclass(frozen=True)
class ResolvedTrackRole:
    track_token: str
    role: Literal["staff", "customer", "unknown", "uncertain"]
    role_source: str
    role_confidence: float
    vest_confidence: Optional[float]
    vest_hits: int
    window_size: int
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.track_token.strip():
            raise ValueError("track_token is required")
        _validate_confidence(self.role_confidence, "role confidence")
        if self.vest_confidence is not None:
            _validate_confidence(self.vest_confidence, "vest confidence")
        if self.vest_hits < 0 or self.window_size < 1:
            raise ValueError("invalid role evidence window")


@dataclass(frozen=True)
class VestAssociationCandidateDiagnostic:
    track_token: str
    score: float


@dataclass(frozen=True)
class VestAssociationDiagnostic:
    vest_index: int
    vest: VestDetection
    candidates: tuple[VestAssociationCandidateDiagnostic, ...]
    status: Literal["assigned", "ambiguous", "no_candidate", "unassigned"]
    assigned_track_token: Optional[str] = None


class _TrackRoleState:
    def __init__(self, window_size: int) -> None:
        self.evidence: deque[Optional[bool]] = deque(maxlen=window_size)
        self.staff_confirmed = False
        self.last_vest_confidence: Optional[float] = None
        self.last_seen_frame = 0


class PersonRoleResolver:
    """Associate vest detections one-to-one and stabilize Person Track roles."""

    def __init__(
        self,
        *,
        role_window_frames: int = 5,
        staff_hit_frames: int = 3,
        maximum_missing_frames: int = 8,
        vest_confidence_threshold: float = 0.68,
        ambiguity_margin: float = 0.08,
    ) -> None:
        if role_window_frames < 1 or not 1 <= staff_hit_frames <= role_window_frames:
            raise ValueError("invalid staff role window")
        if maximum_missing_frames < 0:
            raise ValueError("maximum_missing_frames must be non-negative")
        _validate_confidence(
            vest_confidence_threshold, "vest confidence threshold"
        )
        if not math.isfinite(ambiguity_margin) or not 0 <= ambiguity_margin <= 1:
            raise ValueError("ambiguity_margin must be between 0 and 1")
        self.role_window_frames = role_window_frames
        self.staff_hit_frames = staff_hit_frames
        self.maximum_missing_frames = maximum_missing_frames
        self.vest_confidence_threshold = float(vest_confidence_threshold)
        self.ambiguity_margin = ambiguity_margin
        self._frame_index = 0
        self._states: dict[str, _TrackRoleState] = {}
        self.last_association_diagnostics: tuple[VestAssociationDiagnostic, ...] = ()

    def resolve(
        self,
        person_tracks: Sequence[PersonTrackDetection],
        vest_detections: Sequence[VestDetection],
    ) -> list[ResolvedTrackRole]:
        self._frame_index += 1
        tokens = [track.track_token for track in person_tracks]
        if len(tokens) != len(set(tokens)):
            raise ValueError("person track tokens must be unique")
        matches, ambiguous = self._match(person_tracks, vest_detections)
        resolved = []
        for track in person_tracks:
            state = self._states.setdefault(
                track.track_token, _TrackRoleState(self.role_window_frames)
            )
            state.last_seen_frame = self._frame_index
            matched_vest = matches.get(track.track_token)
            is_ambiguous = track.track_token in ambiguous
            state.evidence.append(None if is_ambiguous else matched_vest is not None)
            if matched_vest is not None:
                state.last_vest_confidence = matched_vest.confidence
            hits = sum(value is True for value in state.evidence)
            if hits >= self.staff_hit_frames:
                state.staff_confirmed = True
            resolved.append(
                self._resolved_role(
                    track.track_token,
                    state,
                    is_ambiguous=is_ambiguous,
                )
            )
        self._expire_missing(set(tokens))
        return resolved

    def _resolved_role(
        self,
        track_token: str,
        state: _TrackRoleState,
        *,
        is_ambiguous: bool,
    ) -> ResolvedTrackRole:
        hits = sum(value is True for value in state.evidence)
        observed = len(state.evidence)
        if state.staff_confirmed:
            role = "staff"
            source = "staff_vest_yolo"
            confidence = max(hits / self.staff_hit_frames, 1.0)
            reason = None
        elif is_ambiguous or (
            observed == self.role_window_frames and None in state.evidence
        ):
            role = "uncertain"
            source = "vest_person_association"
            confidence = hits / self.role_window_frames
            reason = "overlapping_candidates"
        elif observed == self.role_window_frames:
            role = "customer"
            source = "multi_frame_no_vest"
            confidence = 1 - hits / self.role_window_frames
            reason = None
        else:
            role = "unknown"
            source = "role_window_pending"
            confidence = observed / self.role_window_frames
            reason = None
        return ResolvedTrackRole(
            track_token=track_token,
            role=role,
            role_source=source,
            role_confidence=min(1.0, confidence),
            vest_confidence=state.last_vest_confidence,
            vest_hits=hits,
            window_size=self.role_window_frames,
            reason=reason,
        )

    def _match(
        self,
        person_tracks: Sequence[PersonTrackDetection],
        vest_detections: Sequence[VestDetection],
    ) -> tuple[dict[str, VestDetection], set[str]]:
        candidates = []
        for vest_index, vest in enumerate(vest_detections):
            if (
                vest.class_name != "staff_vest"
                or vest.confidence < self.vest_confidence_threshold
            ):
                continue
            for track in person_tracks:
                score = _vest_person_association_score(vest, track)
                if score is not None:
                    candidates.append(
                        (score, track.track_token, vest_index, vest)
                    )

        ambiguous_tokens: set[str] = set()
        ambiguous_vests: set[int] = set()
        for vest_index in {item[2] for item in candidates}:
            options = sorted(
                (item for item in candidates if item[2] == vest_index),
                reverse=True,
            )
            if len(options) > 1 and options[0][0] - options[1][0] < self.ambiguity_margin:
                ambiguous_vests.add(vest_index)
                ambiguous_tokens.update((options[0][1], options[1][1]))
        for token in {item[1] for item in candidates}:
            options = sorted(
                (item for item in candidates if item[1] == token),
                reverse=True,
            )
            if len(options) > 1 and options[0][0] - options[1][0] < self.ambiguity_margin:
                ambiguous_tokens.add(token)
                ambiguous_vests.update((options[0][2], options[1][2]))

        matches: dict[str, VestDetection] = {}
        used_vests = set()
        for score, token, vest_index, vest in sorted(
            candidates, key=lambda item: (-item[0], item[1], item[2])
        ):
            if (
                token in ambiguous_tokens
                or vest_index in ambiguous_vests
                or token in matches
                or vest_index in used_vests
            ):
                continue
            matches[token] = vest
            used_vests.add(vest_index)
        assigned_by_vest = {
            vest_index: token
            for score, token, vest_index, vest in candidates
            if matches.get(token) is vest
        }
        diagnostics = []
        for vest_index, vest in enumerate(vest_detections):
            vest_candidates = tuple(
                VestAssociationCandidateDiagnostic(track_token=token, score=score)
                for score, token, candidate_vest_index, _candidate_vest in sorted(
                    candidates, key=lambda item: (-item[0], item[1], item[2])
                )
                if candidate_vest_index == vest_index
            )
            assigned_token = assigned_by_vest.get(vest_index)
            if vest_index in ambiguous_vests:
                status = "ambiguous"
            elif assigned_token is not None:
                status = "assigned"
            elif not vest_candidates:
                status = "no_candidate"
            else:
                status = "unassigned"
            diagnostics.append(
                VestAssociationDiagnostic(
                    vest_index=vest_index,
                    vest=vest,
                    candidates=vest_candidates,
                    status=status,
                    assigned_track_token=assigned_token,
                )
            )
        self.last_association_diagnostics = tuple(diagnostics)
        return matches, ambiguous_tokens

    def _expire_missing(self, active_tokens: set[str]) -> None:
        for token, state in list(self._states.items()):
            if token in active_tokens:
                continue
            if self._frame_index - state.last_seen_frame > self.maximum_missing_frames:
                del self._states[token]


@dataclass(frozen=True)
class GroundPlaneProjector:
    matrix: tuple[float, float, float, float, float, float, float, float, float]

    def __post_init__(self) -> None:
        if len(self.matrix) != 9 or not all(math.isfinite(value) for value in self.matrix):
            raise ValueError("ground homography must contain nine finite values")
        matrix = np.array(self.matrix, dtype=float).reshape((3, 3))
        if abs(float(np.linalg.det(matrix))) < 1e-12:
            raise ValueError("ground homography must be invertible")

    def project(self, point: Point) -> Point:
        if (
            len(point) != 2
            or not all(
                isinstance(value, Real)
                and not isinstance(value, bool)
                and math.isfinite(value)
                and 0 <= value <= 1
                for value in point
            )
        ):
            raise ValueError("ground projection input must use normalized coordinates")
        x, y = point
        matrix = self.matrix
        denominator = matrix[6] * x + matrix[7] * y + matrix[8]
        if abs(denominator) < 1e-9:
            raise ValueError("ground homography cannot project this point")
        return (
            (matrix[0] * x + matrix[1] * y + matrix[2]) / denominator,
            (matrix[3] * x + matrix[4] * y + matrix[5]) / denominator,
        )

    def unproject(self, world_point: Point) -> Point:
        if len(world_point) != 2 or not all(
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in world_point
        ):
            raise ValueError("world point must contain two finite metre values")
        inverse = np.linalg.inv(
            np.array(self.matrix, dtype=float).reshape((3, 3))
        )
        x, y = world_point
        projected = inverse @ np.array([x, y, 1.0])
        if abs(float(projected[2])) < 1e-12:
            raise ValueError("ground homography cannot unproject this point")
        return (
            float(projected[0] / projected[2]),
            float(projected[1] / projected[2]),
        )

    def validate_control_points(
        self,
        control_points: Sequence["GroundControlPoint"],
        *,
        tolerance_metres: float = 1e-5,
    ) -> None:
        if not control_points:
            raise ValueError("at least one ground control point is required")
        for control_point in control_points:
            actual = self.project(control_point.image_point)
            error = math.dist(actual, control_point.world_point)
            if error > tolerance_metres:
                raise ValueError(
                    f"ground control point {control_point.label} mapping error "
                    f"{error:.6f}m exceeds tolerance"
                )


@dataclass(frozen=True)
class GroundControlPoint:
    label: str
    image_point: Point
    world_point: Point

    def __post_init__(self) -> None:
        if self.label not in {"A", "B", "C", "D"}:
            raise ValueError("unsupported ground control point label")
        if len(self.image_point) != 2 or not all(
            math.isfinite(value) and 0 <= value <= 1
            for value in self.image_point
        ):
            raise ValueError("ground control image point must be normalized")
        if len(self.world_point) != 2 or not all(
            math.isfinite(value) for value in self.world_point
        ):
            raise ValueError("ground control world point must be finite")


RECEPTION_GROUND_CONTROL_POINTS = (
    GroundControlPoint("A", (0.06640625, 0.5625), (0.0, 0.0)),
    GroundControlPoint("B", (0.546875, 0.5625), (3.7, 0.0)),
    GroundControlPoint("C", (0.06640625, 0.72916667), (0.0, 1.0)),
    GroundControlPoint("D", (0.546875, 0.72916667), (3.7, 1.0)),
)
RECEPTION_ENTRY_TRIGGER_POINTS = (
    RECEPTION_GROUND_CONTROL_POINTS[0].image_point,
    RECEPTION_GROUND_CONTROL_POINTS[1].image_point,
    RECEPTION_GROUND_CONTROL_POINTS[3].image_point,
    RECEPTION_GROUND_CONTROL_POINTS[2].image_point,
)
RECEPTION_CALIBRATION_CENTER = (0.306640625, 0.645833335)


@dataclass(frozen=True)
class BehaviorCameraConfig:
    camera_id: str
    store_id: str
    source: str
    entry_trigger: Optional[PolygonArea]
    greeting_area: Optional[PolygonArea]
    interaction_areas: tuple[PolygonArea, ...]
    display_areas: tuple[PolygonArea, ...]
    ground_projector: Optional[GroundPlaneProjector]
    camera_role: CameraRole = CameraRole.CUSTOMER_BEHAVIOR
    monitor_config: BehaviorMonitorConfig = field(
        default_factory=BehaviorMonitorConfig
    )
    staff_vest_enabled: Optional[bool] = None
    calibration_status: Optional[str] = None
    face_transform_status: str = "unverified"
    calibration_image_size: Optional[tuple[int, int]] = None
    ground_control_points: tuple[GroundControlPoint, ...] = ()

    @property
    def entrance_area(self) -> Optional[PolygonArea]:
        """Deprecated compatibility alias for entry_trigger."""
        return self.entry_trigger


class BehaviorVisionAdapter:
    def __init__(
        self,
        *,
        store_id: str = "default-store",
        camera_id: str,
        person_model_path: Optional[str] = None,
        staff_vest_model_path: Optional[str] = None,
        detection_model_path: Optional[str] = None,
        pose_model_path: Optional[str] = None,
        person_model=None,
        staff_vest_model=None,
        detection_model=None,
        pose_model=None,
        vest_detector: Optional[BlueVestDetector] = None,
        role_resolver: Optional[PersonRoleResolver] = None,
        tracker: str = "bytetrack.yaml",
        device: str = "cuda:0",
        conf_threshold: float = 0.5,
        staff_vest_enabled: bool = True,
        staff_vest_confidence_threshold: float = 0.68,
        role_window_frames: int = 5,
        staff_hit_frames: int = 3,
        maximum_missing_frames: int = 8,
        person_inference_imgsz: Optional[int] = None,
        ground_projector: Optional[GroundPlaneProjector] = None,
        entry_trigger: Optional[PolygonArea] = None,
        greeting_area: Optional[PolygonArea] = None,
        calibration_status: Optional[str] = None,
        calibration_image_size: Optional[tuple[int, int]] = None,
        identity_resolver: Optional[Callable[[int], Optional[dict]]] = None,
        runtime_id: Optional[str] = None,
        statistics_clock: Callable[[], float] = time.monotonic,
        overlay_language: Literal["en", "zh-CN"] = "en",
        overlay_brand: Optional[str] = None,
    ) -> None:
        if not isinstance(store_id, str) or not store_id.strip():
            raise ValueError("store_id is required")
        if not camera_id.strip():
            raise ValueError("camera_id is required")
        if not tracker.strip():
            raise ValueError("tracker is required")
        if not 0 <= conf_threshold <= 1:
            raise ValueError("conf_threshold must be between 0 and 1")
        if role_window_frames < 1 or not 1 <= staff_hit_frames <= role_window_frames:
            raise ValueError("invalid staff role window")
        if (
            isinstance(maximum_missing_frames, bool)
            or not isinstance(maximum_missing_frames, int)
            or maximum_missing_frames < 0
        ):
            raise ValueError("maximum_missing_frames must be a non-negative integer")
        if person_inference_imgsz is not None and (
            isinstance(person_inference_imgsz, bool)
            or not isinstance(person_inference_imgsz, int)
            or person_inference_imgsz <= 0
        ):
            raise ValueError("person_inference_imgsz must be a positive integer")
        if overlay_language not in {"en", "zh-CN"}:
            raise ValueError("overlay_language must be en or zh-CN")
        if overlay_brand is not None and not overlay_brand.strip():
            raise ValueError("overlay_brand must not be blank")
        if detection_model_path is not None or detection_model is not None:
            warnings.warn(
                "detection_model_path/detection_model are deprecated; use "
                "person_model_path/person_model",
                DeprecationWarning,
                stacklevel=2,
            )
        person_model_path = person_model_path or detection_model_path
        person_model = person_model or detection_model
        if person_model is None:
            if not person_model_path:
                raise ValueError("person model path is required")
            from ultralytics import YOLO

            person_model = YOLO(person_model_path)
        if pose_model is None:
            if not pose_model_path:
                raise ValueError("pose model path is required")
            from ultralytics import YOLO

            pose_model = YOLO(pose_model_path)
        if not isinstance(staff_vest_enabled, bool):
            raise ValueError("staff_vest_enabled must be a boolean")
        if entry_trigger is not None and entry_trigger.area_type != "entrance":
            raise ValueError("entry_trigger must have type entrance")
        if greeting_area is not None and greeting_area.area_type != "greeting":
            raise ValueError("greeting_area must have type greeting")
        if calibration_status not in {None, "provisional"}:
            raise ValueError("calibration_status must be provisional")
        if calibration_image_size is not None and (
            len(calibration_image_size) != 2
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                for value in calibration_image_size
            )
        ):
            raise ValueError("calibration_image_size must contain width and height")
        if staff_vest_enabled and vest_detector is None:
            vest_model_path = staff_vest_model_path or detection_model_path
            vest_model = staff_vest_model or detection_model
            vest_detector = BlueVestDetector(
                model_path=vest_model_path,
                model=vest_model,
                device=device,
                confidence_threshold=staff_vest_confidence_threshold,
            )
        self.store_id = store_id.strip()
        self.camera_id = camera_id
        self.person_model = person_model
        self.pose_model = pose_model
        self.vest_detector = vest_detector
        self.tracker = tracker
        self.device = device
        self.conf_threshold = conf_threshold
        self.staff_vest_enabled = staff_vest_enabled
        self.role_window_frames = role_window_frames
        self.staff_hit_frames = staff_hit_frames
        self.maximum_missing_frames = maximum_missing_frames
        self.person_inference_imgsz = person_inference_imgsz
        self.role_resolver = role_resolver or PersonRoleResolver(
            role_window_frames=role_window_frames,
            staff_hit_frames=staff_hit_frames,
            maximum_missing_frames=maximum_missing_frames,
            vest_confidence_threshold=staff_vest_confidence_threshold,
        )
        self.ground_projector = ground_projector
        self.entry_trigger = entry_trigger
        self.greeting_area = greeting_area
        self.calibration_status = calibration_status
        self.calibration_image_size = calibration_image_size
        self._calibration_size_checked = False
        self.identity_resolver = identity_resolver or (lambda _track_id: None)
        self.runtime_id = runtime_id or uuid.uuid4().hex
        self.overlay_language = overlay_language
        self.overlay_brand = overlay_brand.strip() if overlay_brand else None
        self._statistics_clock = statistics_clock
        self._statistics_started_at = statistics_clock()
        self._statistics_frames = 0
        self._person_inference_ms = 0.0
        self._vest_inference_ms = 0.0
        self._pose_inference_ms = 0.0
        self.last_person_tracks: tuple[PersonTrackDetection, ...] = ()
        self.last_vest_detections: tuple[VestDetection, ...] = ()
        self.last_resolved_roles: tuple[ResolvedTrackRole, ...] = ()
        self.last_observations: tuple[TrackObservation, ...] = ()

    def process_frame(self, frame) -> list[TrackObservation]:
        height, width = frame.shape[:2]
        if not self._calibration_size_checked:
            self._calibration_size_checked = True
            if (
                self.calibration_image_size is not None
                and (width, height) != self.calibration_image_size
            ):
                warnings.warn(
                    f"runtime frame size {width}x{height} does not match "
                    f"calibration size {self.calibration_image_size[0]}x"
                    f"{self.calibration_image_size[1]}",
                    RuntimeWarning,
                    stacklevel=2,
                )
        inference_started = time.perf_counter()
        person_track_options = {
            "persist": True,
            "tracker": self.tracker,
            "device": self.device,
            "conf": self.conf_threshold,
            "classes": [0],
            "verbose": False,
        }
        if self.person_inference_imgsz is not None:
            person_track_options["imgsz"] = self.person_inference_imgsz
        person_results = self.person_model.track(frame, **person_track_options)
        person_inference_ms = (time.perf_counter() - inference_started) * 1000
        if self.staff_vest_enabled and self.vest_detector is not None:
            inference_started = time.perf_counter()
            vests = self.vest_detector.detect(frame)
            vest_inference_ms = (time.perf_counter() - inference_started) * 1000
        else:
            vests = []
            vest_inference_ms = 0.0
        persons = []
        if person_results:
            result = person_results[0]
            names = getattr(result, "names", {})
            boxes = getattr(result, "boxes", None)
            if boxes is not None:
                xyxy_values = _values(boxes.xyxy)
                confidences = _values(boxes.conf)
                class_ids = _values(boxes.cls)
                track_ids = (
                    _values(boxes.id)
                    if getattr(boxes, "id", None) is not None
                    else []
                )
                for index, bbox in enumerate(xyxy_values):
                    class_id = int(class_ids[index])
                    if _class_name(names, class_id) != "person":
                        continue
                    track_id = (
                        int(track_ids[index])
                        if index < len(track_ids) and track_ids[index] is not None
                        else None
                    )
                    if track_id is None:
                        continue
                    persons.append(
                        {
                            "bbox": tuple(float(value) for value in bbox),
                            "confidence": float(confidences[index]),
                            "track_id": track_id,
                            "track_token": (
                                f"{self.store_id}:{self.camera_id}:"
                                f"{self.runtime_id}:{track_id}"
                            ),
                        }
                    )

        person_tracks = tuple(
            PersonTrackDetection(
                track_id=person["track_id"],
                track_token=person["track_token"],
                bbox=person["bbox"],
                confidence=person["confidence"],
                is_frame_clipped=(
                    person["bbox"][0] <= max(2, width * 0.01)
                    or person["bbox"][1] <= max(2, height * 0.01)
                    or person["bbox"][2] >= width - max(2, width * 0.01)
                    or person["bbox"][3] >= height - max(2, height * 0.01)
                ),
            )
            for person in persons
        )
        resolved_roles = tuple(self.role_resolver.resolve(person_tracks, vests))
        roles_by_token = {role.track_token: role for role in resolved_roles}
        self.last_person_tracks = person_tracks
        self.last_vest_detections = tuple(vests)
        self.last_resolved_roles = resolved_roles

        inference_started = time.perf_counter()
        poses = self._pose_assignments(frame, persons)
        pose_inference_ms = (time.perf_counter() - inference_started) * 1000
        observations = []
        for person in persons:
            track_id = person["track_id"]
            identity = self.identity_resolver(track_id)
            resolved_role = roles_by_token[person["track_token"]]
            role = resolved_role.role
            role_source = resolved_role.role_source
            role_confidence = resolved_role.role_confidence
            if identity is not None and role != "staff":
                role = "customer"
                role_source = "member_identity"
                role_confidence = 1.0
            x1, y1, x2, y2 = person["bbox"]
            foot_point = (
                _normalise((x1 + x2) / 2, width),
                _normalise(y2, height),
            )
            world_point = (
                self.ground_projector.project(foot_point)
                if self.ground_projector is not None
                else None
            )
            observations.append(
                TrackObservation(
                    track_id=track_id,
                    track_token=person["track_token"],
                    role=role,
                    foot_point=foot_point,
                    world_point=world_point,
                    confidence=person["confidence"],
                    wrists=tuple(poses.get(track_id, ())),
                    member_id=identity.get("member_id") if identity else None,
                    member_tier=identity.get("member_tier") if identity else None,
                    visit_id=identity.get("visit_id") if identity else None,
                    identity_status=identity.get("identity_status") if identity else None,
                    identity_confidence=(
                        identity.get("identity_confidence") if identity else None
                    ),
                    identity_source=identity.get("identity_source") if identity else None,
                    identity_binding_level=(
                        identity.get("identity_binding_level") if identity else None
                    ),
                    evaluation_id=identity.get("evaluation_id") if identity else None,
                    policy_version_id=(
                        identity.get("policy_version_id") if identity else None
                    ),
                    algorithm_version=(
                        identity.get("algorithm_version") if identity else None
                    ),
                    parameter_version=(
                        identity.get("parameter_version") if identity else None
                    ),
                    role_source=role_source,
                    role_confidence=role_confidence,
                    person_height=(y2 - y1) / height,
                )
            )
        self._record_runtime_statistics(
            person_inference_ms=person_inference_ms,
            vest_inference_ms=vest_inference_ms,
            pose_inference_ms=pose_inference_ms,
            active_tracks=len(persons),
            vest_detections=len(vests),
        )
        self.last_observations = tuple(observations)
        return observations

    def _record_runtime_statistics(
        self,
        *,
        person_inference_ms: float,
        vest_inference_ms: float,
        pose_inference_ms: float,
        active_tracks: int,
        vest_detections: int,
    ) -> None:
        self._statistics_frames += 1
        self._person_inference_ms += person_inference_ms
        self._vest_inference_ms += vest_inference_ms
        self._pose_inference_ms += pose_inference_ms
        now = self._statistics_clock()
        elapsed = now - self._statistics_started_at
        if elapsed < 5.0:
            return
        frames = self._statistics_frames
        logger.info(
            "Behavior vision stats camera_id=%s runtime_session_id=%s fps=%.2f "
            "person_inference_ms=%.2f vest_inference_ms=%.2f "
            "pose_inference_ms=%.2f active_tracks=%d vest_detections=%d",
            self.camera_id,
            self.runtime_id,
            frames / elapsed if elapsed > 0 else 0.0,
            self._person_inference_ms / frames,
            self._vest_inference_ms / frames,
            self._pose_inference_ms / frames,
            active_tracks,
            vest_detections,
        )
        self._statistics_started_at = now
        self._statistics_frames = 0
        self._person_inference_ms = 0.0
        self._vest_inference_ms = 0.0
        self._pose_inference_ms = 0.0

    def role_debug_lines(self, track_token: str) -> tuple[str, ...]:
        track = next(
            (
                item
                for item in self.last_person_tracks
                if item.track_token == track_token
            ),
            None,
        )
        role = next(
            (
                item
                for item in self.last_resolved_roles
                if item.track_token == track_token
            ),
            None,
        )
        if track is None or role is None:
            raise KeyError(f"unknown track token: {track_token}")
        lines = [
            f"Track {track.track_id}",
            f"TOKEN: {track.track_token}",
            f"ROLE: {role.role.upper()}",
        ]
        if role.reason:
            lines.append(f"REASON: {role.reason}")
        else:
            vest_value = (
                f"{role.vest_confidence:.2f}"
                if role.vest_confidence is not None
                else "-"
            )
            lines.append(f"VEST: {vest_value}")
        lines.append(f"HITS: {role.vest_hits}/{role.window_size}")
        return tuple(lines)

    def spatial_debug_lines(self, track_token: str) -> tuple[str, ...]:
        observation = next(
            (
                item
                for item in self.last_observations
                if item.track_token == track_token
            ),
            None,
        )
        if observation is None:
            raise KeyError(f"unknown track token: {track_token}")
        if (
            self.entry_trigger is not None
            and self.entry_trigger.contains(observation.foot_point)
        ):
            area = "ENTRY_TRIGGER"
        elif (
            self.greeting_area is not None
            and self.greeting_area.contains(observation.foot_point)
        ):
            area = "GREETING_AREA"
        else:
            area = "OUTSIDE_ROI"
        world = (
            f"WORLD: ({observation.world_point[0]:.2f},"
            f"{observation.world_point[1]:.2f})m"
            if observation.world_point is not None
            else "WORLD: -"
        )
        return (
            f"FOOT: ({observation.foot_point[0]:.3f},"
            f"{observation.foot_point[1]:.3f})",
            world,
            f"AREA: {area}",
        )

    def render_debug_overlay(
        self,
        frame: np.ndarray,
        track_state_lines: Optional[dict[str, tuple[str, ...]]] = None,
    ) -> np.ndarray:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("frame must be a BGR image")
        rendered = frame.copy()
        roles = {item.track_token: item for item in self.last_resolved_roles}
        observations = {
            item.track_token: item for item in self.last_observations
        }
        colors = {
            "staff": (0, 220, 0),
            "customer": (255, 160, 0),
            "unknown": (0, 220, 255),
            "uncertain": (0, 80, 255),
        }
        role_labels_zh = {
            "staff": "店员",
            "customer": "顾客",
            "unknown": "身份待确认",
            "uncertain": "身份不确定",
        }
        unicode_items: list[OverlayText] = []
        height, width = rendered.shape[:2]
        for track in self.last_person_tracks:
            role = roles.get(track.track_token)
            if role is None:
                continue
            x1, y1, x2, y2 = (
                int(round(value)) for value in track.bbox
            )
            x1 = min(width - 1, max(0, x1))
            y1 = min(height - 1, max(0, y1))
            x2 = min(width - 1, max(0, x2))
            y2 = min(height - 1, max(0, y2))
            color = colors[role.role]
            cv2.rectangle(rendered, (x1, y1), (x2, y2), color, 4)
            vest_value = (
                f"{role.vest_confidence:.2f}"
                if role.vest_confidence is not None
                else "-"
            )
            chinese = self.overlay_language == "zh-CN"
            label_lines = (
                (
                    f"轨迹 {track.track_id}｜{role_labels_zh[role.role]}",
                    f"蓝色马甲 {vest_value}｜命中 {role.vest_hits}/{role.window_size}",
                )
                if chinese
                else (
                    f"TRACK {track.track_id} | {role.role.upper()}",
                    f"VEST {vest_value} | HITS {role.vest_hits}/{role.window_size}",
                )
            )
            label_font_size = 29
            label_scale = 0.78
            label_thickness = 2
            label_line_height = 37 if chinese else 30
            label_width = max(
                (
                    measure_overlay_text(
                        line,
                        font_size=label_font_size,
                        bold=True,
                    )[0]
                    if chinese
                    else cv2.getTextSize(
                        line,
                        cv2.FONT_HERSHEY_SIMPLEX,
                        label_scale,
                        label_thickness,
                    )[0][0]
                )
                for line in label_lines
            ) + 16
            label_height = label_line_height * len(label_lines) + 8
            label_top = max(0, y1 - label_height)
            label_right = min(width - 1, x1 + label_width)
            cv2.rectangle(
                rendered, (x1, label_top), (label_right, y1), (20, 20, 20), -1
            )
            cv2.rectangle(
                rendered, (x1, label_top), (label_right, y1), color, 2
            )
            for line_index, line in enumerate(label_lines):
                if chinese:
                    unicode_items.append(
                        OverlayText(
                            line,
                            (min(width - 1, x1 + 8), label_top + 2 + line_index * label_line_height),
                            label_font_size,
                            color,
                            bold=True,
                        )
                    )
                else:
                    text_y = min(
                        height - 6,
                        label_top + 26 + line_index * label_line_height,
                    )
                    cv2.putText(
                        rendered,
                        line,
                        (min(width - 1, x1 + 8), text_y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        label_scale,
                        color,
                        label_thickness,
                        cv2.LINE_AA,
                    )
            if self.overlay_brand:
                brand_font_size = 30
                brand_width, brand_height = measure_overlay_text(
                    self.overlay_brand,
                    font_size=brand_font_size,
                    bold=True,
                )
                brand_top = (
                    y2 + 5
                    if y2 + brand_height + 13 < height
                    else max(0, y2 - brand_height - 10)
                )
                brand_right = min(width - 1, x1 + brand_width + 16)
                cv2.rectangle(
                    rendered,
                    (x1, brand_top),
                    (brand_right, min(height - 1, brand_top + brand_height + 8)),
                    (20, 20, 20),
                    -1,
                )
                cv2.rectangle(
                    rendered,
                    (x1, brand_top),
                    (brand_right, min(height - 1, brand_top + brand_height + 8)),
                    color,
                    2,
                )
                unicode_items.append(
                    OverlayText(
                        self.overlay_brand,
                        (x1 + 8, brand_top),
                        brand_font_size,
                        color,
                        bold=True,
                    )
                )
            observation = observations.get(track.track_token)
            if observation is None:
                continue
            foot_x = min(
                width - 1, max(0, round(observation.foot_point[0] * width))
            )
            foot_y = min(
                height - 1, max(0, round(observation.foot_point[1] * height))
            )
            cv2.circle(rendered, (foot_x, foot_y), 6, color, -1)
            spatial_lines = self.spatial_debug_lines(track.track_token) + tuple(
                (track_state_lines or {}).get(track.track_token, ())
            )
            if chinese:
                spatial_lines = tuple(
                    localize_staff_greeting_overlay_line(line)
                    for line in spatial_lines
                )
            panel_font_scale = 0.64
            panel_font_size = 25
            panel_line_height = 32 if chinese else 25
            panel_width = min(900, max(520, width // 3))
            panel_x = (
                x2 + 8
                if x2 + panel_width + 8 < width
                else max(0, x1 - panel_width - 8)
            )
            spatial_y = max(
                28,
                min(height - panel_line_height * len(spatial_lines) - 8, y1),
            )
            panel_bottom = min(
                height - 1,
                spatial_y + panel_line_height * len(spatial_lines) + 8,
            )
            panel = rendered.copy()
            cv2.rectangle(
                panel,
                (panel_x, max(0, spatial_y - 22)),
                (min(width - 1, panel_x + panel_width), panel_bottom),
                (0, 0, 0),
                -1,
            )
            cv2.addWeighted(panel, 0.62, rendered, 0.38, 0, rendered)
            for line_index, line in enumerate(spatial_lines):
                if chinese:
                    unicode_items.append(
                        OverlayText(
                            line,
                            (panel_x + 10, max(0, spatial_y - 22) + line_index * panel_line_height),
                            panel_font_size,
                            color,
                        )
                    )
                else:
                    cv2.putText(
                        rendered,
                        line,
                        (panel_x + 10, spatial_y + line_index * panel_line_height),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        panel_font_scale,
                        color,
                        2,
                        cv2.LINE_AA,
                    )
        return draw_overlay_texts_bgr(rendered, unicode_items)

    def _pose_assignments(self, frame, persons: list[dict]) -> dict[int, list[PosePoint]]:
        if not persons:
            return {}
        pose_results = self.pose_model.predict(
            frame,
            device=self.device,
            conf=self.conf_threshold,
            verbose=False,
        )
        if not pose_results:
            return {}
        result = pose_results[0]
        boxes = getattr(result, "boxes", None)
        keypoints = getattr(result, "keypoints", None)
        if boxes is None or keypoints is None:
            return {}
        pose_boxes = _values(boxes.xyxy)
        point_xy = _values(keypoints.xy)
        point_conf = _values(keypoints.conf)
        height, width = frame.shape[:2]
        assignments: dict[int, list[PosePoint]] = {}
        for index, pose_box in enumerate(pose_boxes):
            person = max(
                persons,
                key=lambda item: _iou(tuple(pose_box), item["bbox"]),
            )
            if _iou(tuple(pose_box), person["bbox"]) < 0.2:
                continue
            wrists = []
            for keypoint_index in (9, 10):
                if keypoint_index >= len(point_xy[index]):
                    continue
                x, y = point_xy[index][keypoint_index]
                confidence = point_conf[index][keypoint_index]
                wrists.append(
                    PosePoint(
                        _normalise(float(x), width),
                        _normalise(float(y), height),
                        float(confidence),
                    )
                )
            assignments[person["track_id"]] = wrists
        return assignments


def load_behavior_camera_configs(path: str | Path) -> tuple[BehaviorCameraConfig, ...]:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    cameras = data.get("cameras") if isinstance(data, dict) else None
    if not isinstance(cameras, list) or not cameras:
        raise ValueError("behavior config requires a non-empty cameras list")
    configured = []
    greeting_profiles = _configured_greeting_profiles(data)
    ids = set()
    for camera in cameras:
        if not isinstance(camera, dict):
            raise ValueError("camera config must be an object")
        camera_id = str(camera.get("camera_id", "")).strip()
        store_id = str(camera.get("store_id", "default-store")).strip()
        source_value = str(camera.get("source", "")).strip()
        if not camera_id or not store_id or not source_value or camera_id in ids:
            raise ValueError(
                "camera_id, store_id, and source must be non-empty; camera_id must be unique"
            )
        ids.add(camera_id)
        has_entry_trigger = "entry_trigger" in camera
        entry_value = camera.get("entry_trigger")
        if not has_entry_trigger and "entrance_area" in camera:
            warnings.warn(
                "entrance_area is deprecated; use entry_trigger",
                DeprecationWarning,
                stacklevel=2,
            )
            entry_value = camera.get("entrance_area")
        entry_trigger = _area_or_none(entry_value, "entrance")
        greeting = _area_or_none(camera.get("greeting_area"), "greeting")
        role_value = camera.get("camera_role")
        if role_value is None:
            camera_role = (
                CameraRole.RECEPTION_ENTRY
                if entry_trigger is not None or greeting is not None
                else CameraRole.CUSTOMER_BEHAVIOR
            )
        else:
            try:
                camera_role = CameraRole(str(role_value))
            except ValueError as error:
                raise ValueError(f"unsupported camera_role: {role_value}") from error
        if (
            entry_trigger is not None or greeting is not None
        ) and camera_role != CameraRole.RECEPTION_ENTRY:
            raise ValueError(
                "entry_trigger and greeting_area require camera_role=reception_entry"
            )
        entrance_value = camera.get("entrance", {})
        if not isinstance(entrance_value, dict):
            raise ValueError("entrance configuration must be an object")
        entrance_fields = {
            "outside_stable_frames",
            "inside_stable_frames",
            "crossing_timeout_seconds",
            "rearm_outside_frames",
            "minimum_crossing_displacement",
        }
        zone_runtime_value = camera.get("zone_runtime", {})
        if not isinstance(zone_runtime_value, dict):
            raise ValueError("zone_runtime configuration must be an object")
        zone_runtime_fields = {
            "zone_enter_stable_frames",
            "zone_exit_stable_frames",
            "tracking_grace_seconds",
            "dwell_minimum_seconds",
            "zone_track_lost_seconds",
        }
        unknown_zone_runtime_fields = set(zone_runtime_value) - zone_runtime_fields
        if unknown_zone_runtime_fields:
            raise ValueError(
                "unsupported zone_runtime configuration fields: "
                f"{sorted(unknown_zone_runtime_fields)}"
            )
        unknown_entrance_fields = set(entrance_value) - entrance_fields
        if unknown_entrance_fields:
            raise ValueError(
                "unsupported entrance configuration fields: "
                f"{sorted(unknown_entrance_fields)}"
            )
        greeting_value = camera.get("greeting")
        if greeting_value is None:
            monitor_config = BehaviorMonitorConfig(
                **entrance_value, **zone_runtime_value
            )
        else:
            if not isinstance(greeting_value, dict):
                raise ValueError("greeting configuration must be an object")
            unknown_greeting_fields = set(greeting_value) - (
                _GREETING_PROFILE_FIELDS | {"profile"}
            )
            if unknown_greeting_fields:
                raise ValueError(
                    "unsupported greeting configuration fields: "
                    f"{sorted(unknown_greeting_fields)}"
                )
            profile = greeting_value.get("profile")
            if not isinstance(profile, str) or profile not in greeting_profiles:
                raise ValueError("greeting profile must be a supported profile name")
            monitor_values = dict(entrance_value)
            monitor_values.update(zone_runtime_value)
            monitor_values.update(greeting_profiles[profile])
            monitor_values.update(
                {key: value for key, value in greeting_value.items() if key != "profile"}
            )
            monitor_values["greeting_profile"] = profile
            monitor_config = BehaviorMonitorConfig(**monitor_values)
        interactions = tuple(
            _area(value, "interaction")
            for value in camera.get("interaction_areas", [])
        )
        displays = tuple(
            _area(value, "display") for value in camera.get("display_areas", [])
        )
        homography = camera.get("ground_homography")
        calibration_status = camera.get("calibration_status")
        if calibration_status is not None and calibration_status != "provisional":
            raise ValueError("calibration_status must be provisional")
        calibration_size = camera.get("calibration_image_size")
        if calibration_size is not None:
            if (
                not isinstance(calibration_size, list)
                or len(calibration_size) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                    for value in calibration_size
                )
            ):
                raise ValueError(
                    "calibration_image_size must contain positive width and height"
                )
            calibration_image_size = tuple(calibration_size)
        else:
            calibration_image_size = None
        staff_vest_enabled = camera.get("staff_vest_enabled")
        if staff_vest_enabled is not None and not isinstance(
            staff_vest_enabled, bool
        ):
            raise ValueError("staff_vest_enabled must be a boolean")
        face_transform_status = str(
            camera.get("face_transform_status", "unverified")
        ).strip().lower()
        if face_transform_status not in {"unverified", "verified"}:
            raise ValueError(
                "face_transform_status must be verified or unverified"
            )
        projector = (
            GroundPlaneProjector(tuple(float(value) for value in homography))
            if homography is not None
            else None
        )
        ground_control_points: tuple[GroundControlPoint, ...] = ()
        if has_entry_trigger and entry_trigger is not None:
            if calibration_status != "provisional":
                raise ValueError(
                    "entry_trigger requires calibration_status=provisional"
                )
            if calibration_image_size is None:
                raise ValueError("entry_trigger requires calibration_image_size")
            if projector is None:
                raise ValueError("entry_trigger requires ground_homography")
            if len(entry_trigger.points) != 4:
                raise ValueError("entry_trigger requires four A/B/D/C points")
            ground_control_points = (
                GroundControlPoint("A", entry_trigger.points[0], (0.0, 0.0)),
                GroundControlPoint("B", entry_trigger.points[1], (3.7, 0.0)),
                GroundControlPoint("C", entry_trigger.points[3], (0.0, 1.0)),
                GroundControlPoint("D", entry_trigger.points[2], (3.7, 1.0)),
            )
            projector.validate_control_points(ground_control_points)
            image_center = _line_intersection(
                entry_trigger.points[0],
                entry_trigger.points[2],
                entry_trigger.points[1],
                entry_trigger.points[3],
            )
            center = projector.project(image_center)
            if math.dist(center, (1.85, 0.5)) > 1e-3:
                raise ValueError("ground calibration center mapping is invalid")
        configured.append(
            BehaviorCameraConfig(
                camera_id=camera_id,
                store_id=store_id,
                source=source_value,
                entry_trigger=entry_trigger,
                greeting_area=greeting,
                interaction_areas=interactions,
                display_areas=displays,
                ground_projector=projector,
                camera_role=camera_role,
                monitor_config=monitor_config,
                staff_vest_enabled=staff_vest_enabled,
                calibration_status=calibration_status,
                face_transform_status=face_transform_status,
                calibration_image_size=calibration_image_size,
                ground_control_points=ground_control_points,
            )
        )
    return tuple(configured)


def _configured_greeting_profiles(data: object) -> dict[str, dict[str, float | int]]:
    profiles = {name: dict(values) for name, values in GREETING_PROFILES.items()}
    configured = data.get("greeting_profiles", {}) if isinstance(data, dict) else {}
    if not isinstance(configured, dict):
        raise ValueError("greeting_profiles must be an object")
    for name, values in configured.items():
        if name not in profiles or not isinstance(values, dict):
            raise ValueError("greeting_profiles must define supported profile objects")
        unknown_fields = set(values) - _GREETING_PROFILE_FIELDS
        if unknown_fields:
            raise ValueError(
                "unsupported greeting profile fields: " f"{sorted(unknown_fields)}"
            )
        profiles[name].update(values)
    return profiles


def draw_behavior_area_overlay(
    frame: np.ndarray,
    camera: BehaviorCameraConfig,
    *,
    opacity: float = 0.18,
) -> np.ndarray:
    """Return a copy of a frame with configured camera areas visualized."""
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must be a BGR image")
    if not 0 <= opacity <= 1:
        raise ValueError("opacity must be between 0 and 1")
    rendered = frame.copy()
    fill = rendered.copy()
    areas = [
        (camera.entry_trigger, (0, 255, 0), "ENTRY_TRIGGER"),
        (camera.greeting_area, (255, 140, 0), "GREETING"),
    ]
    areas.extend(
        (area, (0, 190, 255), "INTERACTION")
        for area in camera.interaction_areas
    )
    areas.extend(
        (area, (255, 0, 255), "DISPLAY") for area in camera.display_areas
    )
    height, width = rendered.shape[:2]
    configured = []
    for area, color, label in areas:
        if area is None:
            continue
        points = np.array(
            [
                (
                    min(width - 1, max(0, round(x * width))),
                    min(height - 1, max(0, round(y * height))),
                )
                for x, y in area.points
            ],
            dtype=np.int32,
        )
        cv2.fillPoly(fill, [points], color)
        configured.append((points, color, label))
    cv2.addWeighted(fill, opacity, rendered, 1 - opacity, 0, rendered)
    for points, color, label in configured:
        cv2.polylines(rendered, [points], True, color, 2)
        x, y = points[0]
        cv2.putText(
            rendered,
            label,
            (int(x) + 4, max(14, int(y) + 18)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    _draw_ground_calibration_overlay(rendered, camera)
    if camera.entry_trigger is not None and camera.greeting_area is None:
        cv2.putText(
            rendered,
            "GREETING_AREA: NOT_CONFIGURED",
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    return rendered


def _draw_ground_calibration_overlay(
    rendered: np.ndarray,
    camera: BehaviorCameraConfig,
) -> None:
    if (
        camera.ground_projector is None
        or not camera.ground_control_points
        or camera.calibration_status is None
    ):
        return
    height, width = rendered.shape[:2]
    world_x = [point.world_point[0] for point in camera.ground_control_points]
    world_y = [point.world_point[1] for point in camera.ground_control_points]
    minimum_x, maximum_x = min(world_x), max(world_x)
    minimum_y, maximum_y = min(world_y), max(world_y)
    grid_color = (180, 180, 180)

    x_values = list(np.arange(minimum_x, maximum_x, 0.5)) + [maximum_x]
    y_values = list(np.arange(minimum_y, maximum_y, 0.5)) + [maximum_y]
    for value in x_values:
        start = camera.ground_projector.unproject((float(value), minimum_y))
        end = camera.ground_projector.unproject((float(value), maximum_y))
        start_pixel = _normalized_pixel(start, width, height)
        end_pixel = _normalized_pixel(end, width, height)
        cv2.line(rendered, start_pixel, end_pixel, grid_color, 1, cv2.LINE_AA)
        cv2.putText(
            rendered,
            f"{value:.1f}m",
            (start_pixel[0] + 2, max(12, start_pixel[1] - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.32,
            grid_color,
            1,
            cv2.LINE_AA,
        )
    for value in y_values:
        start = camera.ground_projector.unproject((minimum_x, float(value)))
        end = camera.ground_projector.unproject((maximum_x, float(value)))
        cv2.line(
            rendered,
            _normalized_pixel(start, width, height),
            _normalized_pixel(end, width, height),
            grid_color,
            1,
            cv2.LINE_AA,
        )

    for control_point in camera.ground_control_points:
        pixel = _normalized_pixel(control_point.image_point, width, height)
        cv2.circle(rendered, pixel, 4, (255, 255, 255), -1)
        cv2.putText(
            rendered,
            control_point.label,
            (pixel[0] + 5, max(12, pixel[1] - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        rendered,
        f"CALIBRATION: {camera.calibration_status}",
        (12, max(18, height - 12)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )


def _normalized_pixel(point: Point, width: int, height: int) -> tuple[int, int]:
    return (
        min(width - 1, max(0, round(point[0] * width))),
        min(height - 1, max(0, round(point[1] * height))),
    )


def _line_intersection(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
) -> Point:
    x1, y1 = first_start
    x2, y2 = first_end
    x3, y3 = second_start
    x4, y4 = second_end
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-12:
        raise ValueError("entry_trigger diagonals must intersect")
    determinant_first = x1 * y2 - y1 * x2
    determinant_second = x3 * y4 - y3 * x4
    return (
        (
            determinant_first * (x3 - x4)
            - (x1 - x2) * determinant_second
        )
        / denominator,
        (
            determinant_first * (y3 - y4)
            - (y1 - y2) * determinant_second
        )
        / denominator,
    )


def _area_or_none(value, area_type: str) -> Optional[PolygonArea]:
    return None if value is None else _area(value, area_type)


def _area(value, area_type: str) -> PolygonArea:
    if not isinstance(value, dict):
        raise ValueError("area config must be an object")
    points = value.get("points")
    if not isinstance(points, list):
        raise ValueError("area points must be a list")
    return PolygonArea(
        area_id=str(value.get("area_id", "")),
        area_type=area_type,
        points=tuple((float(point[0]), float(point[1])) for point in points),
    )


def _values(value):
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _class_name(names, class_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(class_id, class_id))
    if isinstance(names, (list, tuple)) and class_id < len(names):
        return str(names[class_id])
    return str(class_id)


def _validate_confidence(value: float, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{label} must be between 0 and 1")


def _validate_bbox(bbox: BBox, label: str) -> None:
    if (
        not isinstance(bbox, tuple)
        or len(bbox) != 4
        or not all(
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in bbox
        )
        or bbox[2] <= bbox[0]
        or bbox[3] <= bbox[1]
    ):
        raise ValueError(f"{label} must contain four finite coordinates")


def _vest_person_association_score(
    vest: VestDetection, person: PersonTrackDetection
) -> Optional[float]:
    vx1, vy1, vx2, vy2 = vest.bbox
    px1, py1, px2, py2 = person.bbox
    center_x = (vx1 + vx2) / 2
    center_y = (vy1 + vy2) / 2
    if not (px1 <= center_x <= px2 and py1 <= center_y <= py2):
        return None
    person_width = px2 - px1
    person_height = py2 - py1
    normalized_y = (center_y - py1) / person_height
    # A lower placement is characteristic of a person box merged across
    # overlapping subjects, not of a vest on that person's torso.
    torso_bottom = 0.60
    if not 0.08 <= normalized_y <= torso_bottom:
        return None
    intersection_width = max(0.0, min(vx2, px2) - max(vx1, px1))
    intersection_height = max(0.0, min(vy2, py2) - max(vy1, py1))
    vest_area = (vx2 - vx1) * (vy2 - vy1)
    person_area = person_width * person_height
    containment_score = intersection_width * intersection_height / vest_area
    area_ratio = vest_area / person_area
    if containment_score < 0.6 or not 0.005 <= area_ratio <= 0.6:
        return None
    center_inside_score = 1.0
    torso_position_score = max(0.0, 1 - abs(normalized_y - 0.38) / 0.38)
    vest_confidence_score = vest.confidence
    return (
        0.30 * center_inside_score
        + 0.30 * torso_position_score
        + 0.25 * containment_score
        + 0.15 * vest_confidence_score
    )


def _vest_belongs_to_person(vest: tuple, person: tuple) -> bool:
    vx1, vy1, vx2, vy2 = vest
    px1, py1, px2, py2 = person
    center_x = (vx1 + vx2) / 2
    center_y = (vy1 + vy2) / 2
    torso_bottom = py1 + (py2 - py1) * 0.72
    return px1 <= center_x <= px2 and py1 <= center_y <= torso_bottom


def _iou(left: tuple, right: tuple) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _normalise(value: float, maximum: int) -> float:
    if maximum <= 0:
        raise ValueError("frame dimensions must be positive")
    return min(1.0, max(0.0, value / maximum))
