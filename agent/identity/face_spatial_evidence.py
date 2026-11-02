"""Sanitized vendor face geometry for same-camera Track Association."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from typing import Any, Optional


BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class FaceSpatialEvidence:
    bbox: BBox
    normalized: bool
    quality_status: str
    raw_bbox: Optional[BBox] = None
    source_frame_size: Optional[tuple[int, int]] = None
    camera_id: Optional[str] = None
    quality: Optional[float] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    roll: Optional[float] = None
    occurred_at: Optional[datetime] = None
    coordinate_space: str = "normalized_full_frame"

    def to_safe_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "coordinate_space": self.coordinate_space,
            "normalized_bbox": list(self.bbox),
            "quality_status": self.quality_status,
        }
        if self.raw_bbox is not None:
            result["raw_bbox"] = list(self.raw_bbox)
        if self.source_frame_size is not None:
            result["source_frame_size"] = list(self.source_frame_size)
        if self.camera_id is not None:
            result["camera_id"] = self.camera_id
        for name in ("quality", "yaw", "pitch", "roll"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        if self.occurred_at is not None:
            result["event_time"] = self.occurred_at.isoformat()
        return result

    @classmethod
    def from_safe_dict(cls, value: object) -> Optional["FaceSpatialEvidence"]:
        if not isinstance(value, dict):
            return None
        if value.get("coordinate_space", "normalized_full_frame") != "normalized_full_frame":
            return None
        bbox = _bbox(value.get("normalized_bbox"))
        if bbox is None:
            return None
        raw_bbox = _raw_bbox(value.get("raw_bbox"))
        size = value.get("source_frame_size")
        source_size = None
        if (
            isinstance(size, (list, tuple))
            and len(size) == 2
            and all(isinstance(item, int) and item > 0 for item in size)
        ):
            source_size = (size[0], size[1])
        event_time = None
        if isinstance(value.get("event_time"), str):
            try:
                event_time = datetime.fromisoformat(value["event_time"])
            except ValueError:
                return None
        return cls(
            bbox=bbox,
            normalized=True,
            quality_status=str(value.get("quality_status") or "parsed"),
            raw_bbox=raw_bbox,
            source_frame_size=source_size,
            camera_id=(
                str(value["camera_id"])
                if isinstance(value.get("camera_id"), str) and value["camera_id"].strip()
                else None
            ),
            quality=_finite_optional(value.get("quality")),
            yaw=_finite_optional(value.get("yaw")),
            pitch=_finite_optional(value.get("pitch")),
            roll=_finite_optional(value.get("roll")),
            occurred_at=event_time,
            coordinate_space=str(
                value.get("coordinate_space") or "normalized_full_frame"
            ),
        )


class FaceSpatialEvidenceAdapter:
    """Extract only geometry and quality fields from a FaceReco payload."""

    def parse(
        self,
        info: object,
        *,
        occurred_at: datetime,
    ) -> Optional[FaceSpatialEvidence]:
        if not isinstance(info, dict):
            return None
        face = info.get("FaceInfo", info.get("faceInfo"))
        if not isinstance(face, dict):
            face = info.get("CaptureImage", info.get("captureImage"))
        if not isinstance(face, dict):
            return None
        features = info.get("faceFeature")
        if not isinstance(features, dict):
            features = face
        left_top = _point(face.get("leftTop"))
        right_bottom = _point(face.get("rightBottom"))
        background = face.get(
            "BackgroundImage",
            face.get(
                "backgroundImage",
                info.get("BackgroundImage", info.get("backgroundImage")),
            ),
        )
        if left_top is None or right_bottom is None or not isinstance(background, dict):
            return None
        width = _positive_int(background.get("width"))
        height = _positive_int(background.get("height"))
        if width is None or height is None:
            return None
        x1, y1 = left_top
        x2, y2 = right_bottom
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return None
        raw_bbox = (x1, y1, x2, y2)
        return FaceSpatialEvidence(
            bbox=(x1 / width, y1 / height, x2 / width, y2 / height),
            normalized=True,
            quality_status="parsed",
            raw_bbox=raw_bbox,
            source_frame_size=(width, height),
            quality=_finite_optional(features.get("quality")),
            yaw=_finite_optional(features.get("yaw")),
            pitch=_finite_optional(features.get("pitch")),
            roll=_finite_optional(features.get("roll")),
            occurred_at=occurred_at,
        )


def _finite_optional(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _positive_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def _point(value: object) -> Optional[tuple[float, float]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        raw_x = value.get("x")
        raw_y = value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        raw_x, raw_y = value
    else:
        return None
    x = _finite_optional(raw_x)
    y = _finite_optional(raw_y)
    return (x, y) if x is not None and y is not None else None


def _bbox(value: object) -> Optional[BBox]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = tuple(_finite_optional(item) for item in value)
    if any(item is None for item in values):
        return None
    x1, y1, x2, y2 = values
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    return x1, y1, x2, y2


def _raw_bbox(value: object) -> Optional[BBox]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = tuple(_finite_optional(item) for item in value)
    if any(item is None for item in values):
        return None
    x1, y1, x2, y2 = values
    if not (0 <= x1 < x2 and 0 <= y1 < y2):
        return None
    return x1, y1, x2, y2
