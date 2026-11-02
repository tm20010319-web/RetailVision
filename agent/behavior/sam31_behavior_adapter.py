"""Translate SAM 3.1 global-track CSV observations for BehaviorMonitor."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Iterable, Mapping, Optional

from agent.behavior.behavior_monitor import Point, TrackObservation


BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Sam31BehaviorReplayRecord:
    """One replayable SAM observation and its source sequencing metadata."""

    observed_at: datetime
    source_seq: int
    global_track_id: int
    bbox: BBox
    centroid: Optional[Point]
    observation: TrackObservation


class Sam31BehaviorAdapter:
    """Build existing TrackObservation values from SAM 3.1 global tracks."""

    def __init__(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        frame_width: int,
        frame_height: int,
        role: str = "customer",
        confidence: float = 1.0,
    ) -> None:
        _nonempty(store_id, "store_id")
        _nonempty(camera_id, "camera_id")
        _nonempty(runtime_instance_id, "runtime_instance_id")
        _positive_int(frame_width, "frame_width")
        _positive_int(frame_height, "frame_height")
        if role not in {"customer", "staff", "unknown", "uncertain"}:
            raise ValueError("unsupported track role")
        _confidence(confidence)
        self.store_id = store_id
        self.camera_id = camera_id
        self.runtime_instance_id = runtime_instance_id
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.role = role
        self.confidence = confidence

    def to_observation(
        self,
        *,
        global_track_id: int,
        bbox: BBox,
        centroid: Optional[Point] = None,
    ) -> TrackObservation:
        _nonnegative_int(global_track_id, "global_track_id")
        normalized_bbox = _bbox(
            bbox=bbox,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
        )
        if centroid is not None:
            _point(
                centroid,
                frame_width=self.frame_width,
                frame_height=self.frame_height,
                name="centroid",
            )
        x1, _y1, x2, y2 = normalized_bbox
        foot_point = (
            ((x1 + x2) / 2) / self.frame_width,
            y2 / self.frame_height,
        )
        return TrackObservation(
            track_id=global_track_id,
            track_token=(
                f"{self.store_id}:{self.camera_id}:"
                f"{self.runtime_instance_id}:{global_track_id}"
            ),
            role=self.role,
            foot_point=foot_point,
            confidence=self.confidence,
        )

    def from_csv_row(self, row: Mapping[str, object]) -> Sam31BehaviorReplayRecord:
        global_track_id = _csv_int(row, "global_track_id")
        source_seq = _csv_int(row, "source_seq")
        bbox = (
            _csv_float(row, "bbox_x1"),
            _csv_float(row, "bbox_y1"),
            _csv_float(row, "bbox_x2"),
            _csv_float(row, "bbox_y2"),
        )
        centroid = _optional_centroid(row)
        observed_at = _timestamp(row)
        observation = self.to_observation(
            global_track_id=global_track_id,
            bbox=bbox,
            centroid=centroid,
        )
        return Sam31BehaviorReplayRecord(
            observed_at=observed_at,
            source_seq=source_seq,
            global_track_id=global_track_id,
            bbox=bbox,
            centroid=centroid,
            observation=observation,
        )

    def to_record(
        self,
        *,
        observed_at: datetime,
        source_seq: int,
        global_track_id: int,
        bbox: BBox,
        centroid: Optional[Point] = None,
    ) -> Sam31BehaviorReplayRecord:
        if not isinstance(observed_at, datetime):
            raise ValueError("observed_at must be a datetime")
        _nonnegative_int(source_seq, "source_seq")
        observation = self.to_observation(
            global_track_id=global_track_id,
            bbox=bbox,
            centroid=centroid,
        )
        return Sam31BehaviorReplayRecord(
            observed_at=observed_at,
            source_seq=source_seq,
            global_track_id=global_track_id,
            bbox=tuple(float(value) for value in bbox),
            centroid=centroid,
            observation=observation,
        )

    def read_csv(self, path: str | Path) -> Iterable[Sam31BehaviorReplayRecord]:
        with Path(path).open("r", encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                yield self.from_csv_row(row)


def _timestamp(row: Mapping[str, object]) -> datetime:
    value = row.get("timestamp", row.get("source_wall_time"))
    if value is None or not str(value).strip():
        raise ValueError("timestamp or source_wall_time is required")
    text = str(value).strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("timestamp must be Unix seconds or ISO-8601") from error
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    if not math.isfinite(seconds):
        raise ValueError("timestamp must be finite")
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


def _optional_centroid(row: Mapping[str, object]) -> Optional[Point]:
    x = row.get("centroid_x")
    y = row.get("centroid_y")
    if x in {None, ""} and y in {None, ""}:
        return None
    if x in {None, ""} or y in {None, ""}:
        raise ValueError("centroid_x and centroid_y must be provided together")
    return float(x), float(y)


def _csv_int(row: Mapping[str, object], name: str) -> int:
    value = row.get(name)
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    _nonnegative_int(parsed, name)
    return parsed


def _csv_float(row: Mapping[str, object], name: str) -> float:
    value = row.get(name)
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _bbox(*, frame_width: int, frame_height: int, bbox: BBox) -> BBox:
    if len(bbox) != 4 or not all(
        isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
        for value in bbox
    ):
        raise ValueError("bbox must contain four finite coordinates")
    x1, y1, x2, y2 = (float(value) for value in bbox)
    if not 0 <= x1 < x2 <= frame_width or not 0 <= y1 < y2 <= frame_height:
        raise ValueError("bbox must fit inside the configured frame")
    return x1, y1, x2, y2


def _point(
    point: Point,
    *,
    frame_width: int,
    frame_height: int,
    name: str,
) -> None:
    if len(point) != 2 or not all(
        isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
        for value in point
    ):
        raise ValueError(f"{name} must contain two finite coordinates")
    x, y = point
    if not 0 <= x <= frame_width or not 0 <= y <= frame_height:
        raise ValueError(f"{name} must fit inside the configured frame")


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _confidence(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("confidence must be between 0 and 1")
