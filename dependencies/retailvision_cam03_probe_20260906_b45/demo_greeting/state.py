"""Small, explicit state machine for the camera-only greeting demo.

This module intentionally does not import or reuse the production greeting
state machine.  It records a clearly labelled demo signal when a staff-role
track and a non-staff person remain visible together while the staff track
moves in the image.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable, Optional


BBox = tuple[float, float, float, float]
Point = tuple[float, float]


def _bbox(value: Iterable[float]) -> BBox:
    values = tuple(float(item) for item in value)
    if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
        raise ValueError("bbox must contain x1, y1, x2, y2")
    return values  # type: ignore[return-value]


@dataclass(frozen=True)
class PersonObservation:
    """One tracked person in a single frame."""

    track_id: int
    bbox: BBox
    is_staff: bool
    staff_confidence: Optional[float] = None
    role_source: str = "unknown"
    world_point: Optional[Point] = None

    def __post_init__(self) -> None:
        if isinstance(self.track_id, bool) or self.track_id < 0:
            raise ValueError("track_id must be a non-negative integer")
        object.__setattr__(self, "bbox", _bbox(self.bbox))
        if self.staff_confidence is not None and not 0 <= self.staff_confidence <= 1:
            raise ValueError("staff_confidence must be between 0 and 1")
        if self.world_point is not None and (
            len(self.world_point) != 2
            or not all(math.isfinite(value) for value in self.world_point)
        ):
            raise ValueError("world_point must contain two finite metre values")

    @property
    def center(self) -> Point:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @property
    def foot_point(self) -> Point:
        x1, _, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y2)


@dataclass(frozen=True)
class DemoGreetingCandidate:
    """A confirmed demo candidate returned by the state machine."""

    session_id: str
    camera_id: str
    store_id: str
    staff_track_id: int
    customer_track_id: int
    staff_track_token: str
    customer_track_token: str
    started_at: datetime
    confirmed_at: datetime
    pair_duration_seconds: float
    staff_motion_pixels: float
    minimum_distance_metres: Optional[float]
    staff_travel_metres: Optional[float]
    staff_role_confidence: Optional[float]
    staff_role_source: str
    calibration_status: str

    @property
    def event_key(self) -> str:
        return (
            f"demo-greeting:{self.session_id}:"
            f"staff-{self.staff_track_id}:customer-{self.customer_track_id}"
        )

    @property
    def confidence(self) -> float:
        # This is a demo detector confidence, not a calibrated business score.
        return 0.82

    @property
    def details(self) -> dict[str, object]:
        return {
            "demo_detector": "co_presence_motion_v1",
            "demo_rule": "staff_vest_and_person_0.5s_plus_15px_motion",
            "greeting_profile": "demo_camera_motion",
            "is_demo": True,
            "store_id": self.store_id,
            "pair_duration_seconds": round(self.pair_duration_seconds, 3),
            "demo_response_seconds": round(self.pair_duration_seconds, 3),
            "staff_motion_pixels": round(self.staff_motion_pixels, 2),
            "minimum_distance_metres": (
                round(self.minimum_distance_metres, 3)
                if self.minimum_distance_metres is not None
                else None
            ),
            "staff_travel_metres": (
                round(self.staff_travel_metres, 3)
                if self.staff_travel_metres is not None
                else None
            ),
            "distance_source": (
                "ground_homography"
                if self.minimum_distance_metres is not None
                and self.staff_travel_metres is not None
                else "unavailable"
            ),
            "calibration_status": self.calibration_status,
            "staff_track_token": self.staff_track_token,
            "customer_track_token": self.customer_track_token,
            "staff_role_source": self.staff_role_source,
            "staff_role_confidence": self.staff_role_confidence,
            "identity_status": "staff_role_unbound",
        }


@dataclass
class _PairState:
    started_at: datetime
    last_seen_at: datetime
    last_staff_center: Point
    motion_pixels: float = 0.0
    staff_origin_world: Optional[Point] = None
    minimum_distance_metres: Optional[float] = None
    maximum_staff_travel_metres: Optional[float] = None
    triggered: bool = False


class DemoGreetingStateMachine:
    """Trigger one event per observed staff/customer track pair.

    The pair is re-armed only after at least one member is absent for the
    configured rearm interval.  This keeps a person standing in front of the
    camera from producing a new event every frame while allowing a new demo
    after the first interaction leaves the view.
    """

    def __init__(
        self,
        *,
        session_id: str,
        camera_id: str,
        store_id: str,
        co_presence_seconds: float = 0.5,
        staff_motion_pixels: float = 15.0,
        rearm_absence_seconds: float = 1.5,
        calibration_status: str = "provisional",
    ) -> None:
        if not session_id.strip() or not camera_id.strip() or not store_id.strip():
            raise ValueError("session_id, camera_id, and store_id are required")
        if co_presence_seconds <= 0 or staff_motion_pixels <= 0:
            raise ValueError("demo thresholds must be positive")
        if rearm_absence_seconds <= 0:
            raise ValueError("rearm_absence_seconds must be positive")
        self.session_id = session_id
        self.camera_id = camera_id
        self.store_id = store_id
        self.co_presence_seconds = float(co_presence_seconds)
        self.staff_motion_pixels = float(staff_motion_pixels)
        self.rearm_absence_seconds = float(rearm_absence_seconds)
        self.calibration_status = calibration_status
        self._pairs: dict[tuple[int, int], _PairState] = {}

    def observe(
        self,
        observed_at: datetime,
        tracks: Iterable[PersonObservation],
    ) -> list[DemoGreetingCandidate]:
        observations = list(tracks)
        track_ids = [track.track_id for track in observations]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("track IDs must be unique within a frame")

        staff_tracks = [track for track in observations if track.is_staff]
        customer_tracks = [track for track in observations if not track.is_staff]
        visible_pairs: set[tuple[int, int]] = set()
        candidates: list[DemoGreetingCandidate] = []

        for staff in staff_tracks:
            if not customer_tracks:
                continue
            # A public shop floor can contain background detections. Use the
            # nearest non-staff person for each staff track so one approach
            # cannot produce an event for every other person in the frame.
            customer = min(
                customer_tracks,
                key=lambda item: math.dist(staff.center, item.center),
            )
            key = (staff.track_id, customer.track_id)
            visible_pairs.add(key)
            pair = self._pairs.get(key)
            if pair is None:
                pair = _PairState(
                    started_at=observed_at,
                    last_seen_at=observed_at,
                    last_staff_center=staff.center,
                    staff_origin_world=staff.world_point,
                )
                self._pairs[key] = pair
            else:
                movement = math.dist(pair.last_staff_center, staff.center)
                # Ignore sub-pixel detector noise but accumulate actual
                # image motion so no world-coordinate calibration is used.
                pair.motion_pixels += max(0.0, movement - 1.0)
                pair.last_staff_center = staff.center
                pair.last_seen_at = observed_at

            self._update_world_metrics(pair, staff, customer)

            if pair.triggered:
                continue
            duration = max(0.0, (observed_at - pair.started_at).total_seconds())
            if (
                duration >= self.co_presence_seconds
                and pair.motion_pixels >= self.staff_motion_pixels
            ):
                pair.triggered = True
                candidates.append(
                    DemoGreetingCandidate(
                        session_id=self.session_id,
                        camera_id=self.camera_id,
                        store_id=self.store_id,
                        staff_track_id=staff.track_id,
                        customer_track_id=customer.track_id,
                        staff_track_token=self._track_token(staff.track_id),
                        customer_track_token=self._track_token(customer.track_id),
                        started_at=pair.started_at,
                        confirmed_at=observed_at,
                        pair_duration_seconds=duration,
                        staff_motion_pixels=pair.motion_pixels,
                        minimum_distance_metres=pair.minimum_distance_metres,
                        staff_travel_metres=pair.maximum_staff_travel_metres,
                        staff_role_confidence=staff.staff_confidence,
                        staff_role_source=staff.role_source,
                        calibration_status=self.calibration_status,
                    )
                )

        self._expire_absent_pairs(observed_at, visible_pairs, set(track_ids))
        return candidates

    @staticmethod
    def _update_world_metrics(
        pair: _PairState,
        staff: PersonObservation,
        customer: PersonObservation,
    ) -> None:
        if staff.world_point is not None:
            if pair.staff_origin_world is None:
                pair.staff_origin_world = staff.world_point
            travel = math.dist(pair.staff_origin_world, staff.world_point)
            pair.maximum_staff_travel_metres = max(
                pair.maximum_staff_travel_metres or 0.0,
                travel,
            )
        if staff.world_point is None or customer.world_point is None:
            return
        distance = math.dist(staff.world_point, customer.world_point)
        pair.minimum_distance_metres = min(
            pair.minimum_distance_metres
            if pair.minimum_distance_metres is not None
            else distance,
            distance,
        )

    def _track_token(self, track_id: int) -> str:
        return f"demo-runtime:{self.session_id}:track-{track_id}"

    def _expire_absent_pairs(
        self,
        observed_at: datetime,
        visible_pairs: set[tuple[int, int]],
        visible_track_ids: set[int],
    ) -> None:
        for key, pair in list(self._pairs.items()):
            if key in visible_pairs:
                continue
            # Keep the completed pair locked until both original tracks have
            # left. A staff member standing in view cannot repeatedly greet
            # the same visible customer because of detector jitter.
            if key[0] in visible_track_ids or key[1] in visible_track_ids:
                continue
            absent_for = max(0.0, (observed_at - pair.last_seen_at).total_seconds())
            if absent_for >= self.rearm_absence_seconds:
                del self._pairs[key]
