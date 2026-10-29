"""Minimal IoU tracker used only by the isolated Demo worker."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from .state import BBox


def _iou(left: BBox, right: BBox) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _center(box: BBox) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


@dataclass(frozen=True)
class RawPerson:
    bbox: BBox
    confidence: float


@dataclass(frozen=True)
class TrackedPerson:
    track_id: int
    bbox: BBox
    confidence: float


@dataclass
class _Track:
    track_id: int
    bbox: BBox
    confidence: float
    missed_frames: int = 0


class SimplePersonTracker:
    """Greedy tracker with deterministic IDs and short occlusion tolerance."""

    def __init__(self, *, iou_threshold: float = 0.15, max_missed_frames: int = 8) -> None:
        if not 0 <= iou_threshold <= 1:
            raise ValueError("iou_threshold must be between 0 and 1")
        if max_missed_frames < 0:
            raise ValueError("max_missed_frames must be non-negative")
        self.iou_threshold = float(iou_threshold)
        self.max_missed_frames = int(max_missed_frames)
        self._next_id = 1
        self._tracks: dict[int, _Track] = {}

    def update(self, detections: Iterable[RawPerson]) -> list[TrackedPerson]:
        values = list(detections)
        for track in self._tracks.values():
            track.missed_frames += 1

        assignments: list[tuple[float, int, int]] = []
        for detection_index, detection in enumerate(values):
            for track_id, track in self._tracks.items():
                score = _iou(detection.bbox, track.bbox)
                if score >= self.iou_threshold:
                    assignments.append((score, detection_index, track_id))

        used_detections: set[int] = set()
        used_tracks: set[int] = set()
        for _score, detection_index, track_id in sorted(assignments, reverse=True):
            if detection_index in used_detections or track_id in used_tracks:
                continue
            detection = values[detection_index]
            track = self._tracks[track_id]
            track.bbox = detection.bbox
            track.confidence = detection.confidence
            track.missed_frames = 0
            used_detections.add(detection_index)
            used_tracks.add(track_id)

        result: list[TrackedPerson] = []
        for detection_index, detection in enumerate(values):
            if detection_index in used_detections:
                continue
            track_id = self._next_id
            self._next_id += 1
            self._tracks[track_id] = _Track(
                track_id=track_id,
                bbox=detection.bbox,
                confidence=detection.confidence,
            )
            used_tracks.add(track_id)

        for track_id in list(self._tracks):
            if self._tracks[track_id].missed_frames > self.max_missed_frames:
                del self._tracks[track_id]

        # Include only detections observed in this frame.  The state machine
        # treats absent tracks as leaving the camera view.
        for track_id in sorted(used_tracks):
            track = self._tracks.get(track_id)
            if track is None or track.missed_frames:
                continue
            result.append(
                TrackedPerson(
                    track_id=track.track_id,
                    bbox=track.bbox,
                    confidence=track.confidence,
                )
            )
        return result

    @property
    def active_count(self) -> int:
        return len(self._tracks)

    def clear(self) -> None:
        self._tracks.clear()
        self._next_id = 1
