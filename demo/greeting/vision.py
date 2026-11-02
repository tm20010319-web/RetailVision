"""RTSP frame detection adapter for the isolated greeting demo."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from agent.behavior.behavior_vision import (
    GroundPlaneProjector,
    PersonRoleResolver,
    PersonTrackDetection,
    VestDetection,
)

from .state import BBox, PersonObservation
from .tracking import RawPerson, SimplePersonTracker


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VestObservation:
    bbox: BBox
    confidence: float


def _as_list(values: Any) -> list[Any]:
    if hasattr(values, "cpu"):
        values = values.cpu()
    if hasattr(values, "tolist"):
        values = values.tolist()
    return list(values or [])


def _model_boxes(result: Any) -> Iterable[tuple[BBox, float, int]]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    xyxy = _as_list(getattr(boxes, "xyxy", []))
    confidence = _as_list(getattr(boxes, "conf", []))
    classes = _as_list(getattr(boxes, "cls", []))
    values = []
    for raw_box, raw_confidence, raw_class in zip(xyxy, confidence, classes):
        box = tuple(float(value) for value in raw_box)
        if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
            continue
        values.append((box, float(raw_confidence), int(raw_class)))
    return values


class DemoVisionPipeline:
    """Person + vest inference using the production role boundary.

    The greeting trigger remains intentionally simple, but Staff/Customer role
    attribution uses the same one-to-one vest association and multi-frame
    stabilization as the production behavior pipeline. This keeps a single
    vest detection from turning every overlapping person into Staff.
    """

    def __init__(
        self,
        *,
        person_model_path: str,
        vest_model_path: str,
        device: str = "cpu",
        person_confidence: float = 0.35,
        vest_confidence: float = 0.45,
        role_window_frames: int = 5,
        staff_hit_frames: int = 3,
        maximum_missing_frames: int = 8,
        ground_projector: Optional[GroundPlaneProjector] = None,
        calibration_image_size: Optional[tuple[int, int]] = None,
        model_loader: Optional[Any] = None,
    ) -> None:
        self.person_model_path = str(Path(person_model_path))
        self.vest_model_path = str(Path(vest_model_path))
        self.device = device
        self.person_confidence = float(person_confidence)
        self.vest_confidence = float(vest_confidence)
        self.ground_projector = ground_projector
        self.calibration_image_size = calibration_image_size
        self.role_resolver = PersonRoleResolver(
            role_window_frames=role_window_frames,
            staff_hit_frames=staff_hit_frames,
            maximum_missing_frames=maximum_missing_frames,
            vest_confidence_threshold=self.vest_confidence,
        )
        self._model_loader = model_loader
        self._person_model = None
        self._vest_model = None
        self.tracker = SimplePersonTracker(iou_threshold=0.12, max_missed_frames=5)
        self.last_detection_count = 0
        self.last_staff_count = 0
        self.last_vest_count = 0

    def load(self) -> None:
        if self._person_model is not None and self._vest_model is not None:
            return
        loader = self._model_loader
        if loader is None:
            from ultralytics import YOLO

            loader = YOLO
        self._person_model = loader(self.person_model_path)
        self._vest_model = loader(self.vest_model_path)
        logger.info("Demo models loaded: person=%s vest=%s", self.person_model_path, self.vest_model_path)

    def detect(self, frame: np.ndarray) -> list[PersonObservation]:
        self.load()
        raw_people = self._detect_people(frame)
        tracked = self.tracker.update(raw_people)
        vests = self._detect_vests(frame)
        person_tracks = [
            PersonTrackDetection(
                track_id=person.track_id,
                track_token=self._role_track_token(person.track_id),
                bbox=person.bbox,
                confidence=person.confidence,
            )
            for person in tracked
        ]
        role_detections = [
            VestDetection(
                bbox=vest.bbox,
                confidence=vest.confidence,
                class_name="staff_vest",
            )
            for vest in vests
        ]
        resolved_roles = {
            item.track_token: item
            for item in self.role_resolver.resolve(person_tracks, role_detections)
        }
        observations: list[PersonObservation] = []
        height, width = frame.shape[:2]
        for person, track in zip(tracked, person_tracks):
            role = resolved_roles[track.track_token]
            if role.role not in {"staff", "customer"}:
                continue
            is_staff = role.role == "staff"
            observations.append(
                PersonObservation(
                    track_id=person.track_id,
                    bbox=person.bbox,
                    is_staff=is_staff,
                    staff_confidence=role.role_confidence if is_staff else None,
                    role_source=role.role_source,
                    world_point=self._project_world_point(
                        person.bbox,
                        width=width,
                        height=height,
                    ),
                )
            )
        self.last_detection_count = len(tracked)
        self.last_staff_count = sum(item.is_staff for item in observations)
        self.last_vest_count = len(vests)
        return observations

    @staticmethod
    def _role_track_token(track_id: int) -> str:
        return f"demo-vision:track-{track_id}"

    def _detect_people(self, frame: np.ndarray) -> list[RawPerson]:
        if self._person_model is None:
            return []
        results = self._person_model.predict(
            frame,
            device=self.device,
            conf=self.person_confidence,
            classes=[0],
            verbose=False,
        )
        if not results:
            return []
        return [
            RawPerson(bbox=bbox, confidence=confidence)
            for bbox, confidence, class_id in _model_boxes(results[0])
            if class_id == 0
        ]

    def _detect_vests(self, frame: np.ndarray) -> list[VestObservation]:
        if self._vest_model is None:
            return []
        results = self._vest_model.predict(
            frame,
            device=self.device,
            conf=self.vest_confidence,
            verbose=False,
        )
        if not results:
            return []
        result = results[0]
        names = getattr(result, "names", {}) or {}
        detections: list[VestObservation] = []
        for bbox, confidence, class_id in _model_boxes(result):
            label = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
            # The configured model is a one-class staff_vest model. Accept
            # class 0 too so an exported model with numeric names still works.
            if label not in {"staff_vest", "blue_vest", "blue-vest", "vest"} and class_id != 0:
                continue
            detections.append(VestObservation(bbox=bbox, confidence=confidence))
        return detections

    def _project_world_point(
        self,
        bbox: BBox,
        *,
        width: int,
        height: int,
    ) -> Optional[tuple[float, float]]:
        if self.ground_projector is None or width <= 0 or height <= 0:
            return None
        if (
            self.calibration_image_size is not None
            and self.calibration_image_size != (width, height)
        ):
            return None
        foot_x = (bbox[0] + bbox[2]) / 2.0
        foot_y = bbox[3]
        normalized = (foot_x / width, foot_y / height)
        try:
            return self.ground_projector.project(normalized)
        except ValueError:
            return None
