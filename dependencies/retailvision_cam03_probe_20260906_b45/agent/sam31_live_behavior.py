"""Live SAM 3.1 Global Track batch seam for the existing Behavior runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Real
from typing import Iterable, Mapping, Optional, Protocol, Sequence

from agent.behavior_monitor import Point, ZoneRuntimeSnapshot, ZoneRuntimeTransition
from agent.sam31_behavior_adapter import BBox, Sam31BehaviorAdapter


@dataclass(frozen=True)
class Sam31GlobalTrack:
    global_track_id: int
    bbox: BBox
    centroid: Optional[Point] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "Sam31GlobalTrack":
        try:
            global_track_id = int(str(value["global_track_id"]))
            bbox_value = value["bbox"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("global_track_id and bbox are required") from error
        if not isinstance(bbox_value, Sequence) or isinstance(bbox_value, (str, bytes)):
            raise ValueError("bbox must contain four coordinates")
        bbox = tuple(bbox_value)
        if len(bbox) != 4:
            raise ValueError("bbox must contain four coordinates")
        centroid_value = value.get("centroid")
        centroid = None
        if centroid_value is not None:
            if not isinstance(centroid_value, Sequence) or isinstance(
                centroid_value, (str, bytes)
            ):
                raise ValueError("centroid must contain two coordinates")
            centroid = tuple(centroid_value)
            if len(centroid) != 2:
                raise ValueError("centroid must contain two coordinates")
        return cls(
            global_track_id=global_track_id,
            bbox=tuple(float(item) for item in bbox),
            centroid=(
                None
                if centroid is None
                else (float(centroid[0]), float(centroid[1]))
            ),
        )


@dataclass(frozen=True)
class Sam31LiveBatchResult:
    observed_at: datetime
    source_seq: int
    tracks: tuple[dict[str, object], ...]
    zone_states: tuple[ZoneRuntimeSnapshot, ...]
    transitions: tuple[ZoneRuntimeTransition, ...]
    behavior_events: tuple[object, ...]

    def to_dict(self) -> dict[str, object]:
        states_by_track: dict[str, list[dict[str, object]]] = {}
        for state in self.zone_states:
            states_by_track.setdefault(state.track_token, []).append({
                "area_id": state.area_id,
                "zone_state": state.zone_state.value,
                "dwell_seconds": state.dwell_seconds,
            })
        return {
            "timestamp": self.observed_at.isoformat(),
            "source_seq": self.source_seq,
            "tracks": [
                {
                    **track,
                    "zones": states_by_track.get(str(track["track_token"]), []),
                }
                for track in self.tracks
            ],
            "zone_states": [
                {
                    "global_track_id": state.track_id,
                    "track_token": state.track_token,
                    "area_id": state.area_id,
                    "zone_state": state.zone_state.value,
                    "dwell_seconds": state.dwell_seconds,
                }
                for state in self.zone_states
            ],
            "transitions": [
                {
                    "transition": transition.transition.value,
                    "global_track_id": transition.track_id,
                    "track_token": transition.track_token,
                    "area_id": transition.area_id,
                    "occurred_at": transition.occurred_at.isoformat(),
                    "dwell_seconds": transition.dwell_seconds,
                }
                for transition in self.transitions
            ],
            "behavior_events": [
                {
                    "event_key": event.event_key,
                    "event_type": event.event_type,
                    "track_token": event.customer_track_token,
                    "area_id": event.area_id,
                    "duration_seconds": event.details.get("duration_seconds"),
                }
                for event in self.behavior_events
            ],
        }


class BehaviorObservationRuntime(Protocol):
    def process_observations(self, observed_at: datetime, observations): ...

    def zone_runtime_diagnostics(
        self,
    ) -> tuple[tuple[ZoneRuntimeSnapshot, ...], tuple[ZoneRuntimeTransition, ...]]: ...


class Sam31LiveBehaviorBridge:
    """Convert and submit exactly one complete SAM frame batch per call."""

    def __init__(
        self,
        *,
        adapter: Sam31BehaviorAdapter,
        runtime: BehaviorObservationRuntime,
    ) -> None:
        self.adapter = adapter
        self.runtime = runtime
        self._last_source_seq: Optional[int] = None

    def process_batch(
        self,
        *,
        observed_at: datetime,
        source_seq: int,
        tracks: Iterable[Sam31GlobalTrack],
    ) -> Sam31LiveBatchResult:
        observed_at = _utc_naive(observed_at)
        if isinstance(source_seq, bool) or not isinstance(source_seq, int) or source_seq < 0:
            raise ValueError("source_seq must be a non-negative integer")
        if self._last_source_seq is not None and source_seq <= self._last_source_seq:
            raise ValueError("source_seq must be strictly increasing")
        batch = tuple(tracks)
        global_ids = [item.global_track_id for item in batch]
        if len(set(global_ids)) != len(global_ids):
            raise ValueError("one frame batch cannot contain duplicate global_track_id")

        records = tuple(
            self.adapter.to_record(
                observed_at=observed_at,
                source_seq=source_seq,
                global_track_id=item.global_track_id,
                bbox=item.bbox,
                centroid=item.centroid,
            )
            for item in batch
        )
        _entries, behaviors = self.runtime.process_observations(
            observed_at,
            tuple(item.observation for item in records),
        )
        zone_states, transitions = self.runtime.zone_runtime_diagnostics()
        self._last_source_seq = source_seq
        return Sam31LiveBatchResult(
            observed_at=observed_at,
            source_seq=source_seq,
            tracks=tuple({
                "global_track_id": item.global_track_id,
                "track_token": item.observation.track_token,
                "bbox": list(item.bbox),
                "centroid": None if item.centroid is None else list(item.centroid),
                "foot_point": list(item.observation.foot_point),
            } for item in records),
            zone_states=tuple(zone_states),
            transitions=tuple(transitions),
            behavior_events=tuple(behaviors),
        )


def parse_observed_at(value: object) -> datetime:
    if isinstance(value, bool):
        raise ValueError("observed_at must be Unix seconds or ISO-8601")
    if isinstance(value, Real):
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError("observed_at must be finite")
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("observed_at must be Unix seconds or ISO-8601") from error
    return _utc_naive(parsed)


def _utc_naive(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("observed_at must be a datetime")
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
