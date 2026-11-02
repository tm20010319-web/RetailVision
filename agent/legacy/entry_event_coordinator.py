"""Best-effort correlation between YOLO tracks and camera identity events."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.identity.device_identity import (
    CustomerSession,
    DeviceRecognitionEvent,
    StableTrackEvent,
)


MemberEntryCallback = Callable[[Dict[str, Any]], None]
AnonymousVisitCallback = Callable[[int, Dict[str, Any], str], None]
IdentityUnknownCallback = Callable[[Any, str], None]

logger = logging.getLogger(__name__)


class EntryEventCoordinator:
    """Correlate independent streams without guessing in ambiguous scenes."""

    def __init__(
        self,
        *,
        on_member_entry: Optional[MemberEntryCallback] = None,
        on_anonymous_visit: Optional[AnonymousVisitCallback] = None,
        on_identity_unknown: Optional[IdentityUnknownCallback] = None,
        association_window_seconds: float = 2.0,
        track_wait_seconds: float = 3.0,
        dedupe_ttl_seconds: float = 60.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.on_member_entry = on_member_entry
        self.on_anonymous_visit = on_anonymous_visit
        self.on_identity_unknown = on_identity_unknown
        self.association_window_seconds = association_window_seconds
        self.track_wait_seconds = track_wait_seconds
        self.dedupe_ttl_seconds = dedupe_ttl_seconds
        self._monotonic_clock = monotonic_clock
        self._tracks: Dict[Tuple[str, int], StableTrackEvent] = {}
        self._recognitions: Dict[str, DeviceRecognitionEvent] = {}
        self._device_health: Dict[str, bool] = {}
        self._consumed_tracks: Dict[Tuple[str, int], float] = {}
        self._consumed_recognitions: Dict[str, float] = {}
        self._lock = threading.RLock()

    def set_device_health(self, device_id: str, available: bool) -> None:
        """Publish merged push-or-poll identity availability."""
        with self._lock:
            self._device_health[device_id] = available

    def submit_track(self, event: StableTrackEvent) -> None:
        with self._lock:
            self._prune_consumed_locked()
            key = (event.camera_id, event.track_id)
            if key in self._consumed_tracks:
                return
            self._tracks[key] = event
            actions = self._resolve_locked(event.camera_id)
        self._dispatch(actions)

    def submit_recognition(self, event: DeviceRecognitionEvent) -> None:
        with self._lock:
            self._prune_consumed_locked()
            if event.event_id in self._consumed_recognitions:
                return
            self._recognitions[event.event_id] = event
            actions = self._resolve_locked(event.device_id)
        self._dispatch(actions)

    def _matching_tracks(
        self, recognition: DeviceRecognitionEvent
    ) -> List[StableTrackEvent]:
        return [
            track
            for track in self._tracks.values()
            if track.camera_id == recognition.device_id
            and abs((track.confirmed_at - recognition.occurred_at).total_seconds())
            <= self.association_window_seconds
        ]

    def _matching_recognitions(
        self, track: StableTrackEvent
    ) -> List[DeviceRecognitionEvent]:
        return [
            recognition
            for recognition in self._recognitions.values()
            if recognition.device_id == track.camera_id
            and abs((track.confirmed_at - recognition.occurred_at).total_seconds())
            <= self.association_window_seconds
        ]

    def _consume_track(self, track: StableTrackEvent) -> None:
        key = (track.camera_id, track.track_id)
        self._tracks.pop(key, None)
        self._consumed_tracks[key] = (
            self._monotonic_clock() + self.dedupe_ttl_seconds
        )

    def _consume_recognition(self, event: DeviceRecognitionEvent) -> None:
        self._recognitions.pop(event.event_id, None)
        self._consumed_recognitions[event.event_id] = (
            self._monotonic_clock() + self.dedupe_ttl_seconds
        )

    def _prune_consumed_locked(self) -> None:
        now = self._monotonic_clock()
        for key, expires_at in list(self._consumed_tracks.items()):
            if expires_at <= now:
                self._consumed_tracks.pop(key, None)
        for event_id, expires_at in list(self._consumed_recognitions.items()):
            if expires_at <= now:
                self._consumed_recognitions.pop(event_id, None)

    def _member_session(
        self,
        recognition: DeviceRecognitionEvent,
        track: Optional[StableTrackEvent],
    ) -> CustomerSession:
        timestamp = recognition.occurred_at.timestamp()
        return CustomerSession(
            session_id=f"device-{recognition.event_id[:24]}",
            track_id=track.track_id if track else None,
            member_id=recognition.member_id,
            camera_id=recognition.device_id,
            confidence=recognition.similarity,
            entry_time=timestamp,
            detection_bbox=list(track.bbox) if track else None,
            match_result=recognition.sanitized_match_result(),
            identity_source="device_api",
            device_event_id=recognition.event_id,
            identity_time=timestamp,
            snapshot_path=recognition.capture_path,
        )

    def _resolve_locked(self, device_id: str) -> List[Tuple[str, Any, Any]]:
        actions: List[Tuple[str, Any, Any]] = []
        for track in list(self._tracks.values()):
            if track.camera_id != device_id:
                continue
            recognition_candidates = self._matching_recognitions(track)
            if len(recognition_candidates) <= 1:
                continue
            self._consume_track(track)
            actions.append(("unknown", track, "ambiguous_device_events"))
            for recognition in recognition_candidates:
                self._consume_recognition(recognition)
                actions.append(("member", self._member_session(recognition, None), None))

        for recognition in list(self._recognitions.values()):
            if recognition.device_id != device_id:
                continue
            candidates = self._matching_tracks(recognition)
            if len(candidates) > 1:
                self._consume_recognition(recognition)
                actions.append(("member", self._member_session(recognition, None), None))
                for track in candidates:
                    self._consume_track(track)
                    actions.append(("unknown", track, "ambiguous_member_match"))
                continue
            if len(candidates) != 1:
                continue
            track = candidates[0]
            # A unique match must be unique in both directions among currently
            # buffered events. Otherwise defer instead of selecting arbitrarily.
            if len(self._matching_recognitions(track)) != 1:
                continue
            self._consume_recognition(recognition)
            self._consume_track(track)
            actions.append(("member", self._member_session(recognition, track), None))
        return actions

    @staticmethod
    def _detection_dict(track: StableTrackEvent) -> Dict[str, Any]:
        return {
            "bbox": list(track.bbox),
            "confidence": track.detection_confidence,
            "class_id": 0,
            "class_name": "person",
            "track_id": track.track_id,
        }

    def flush(self, now: datetime) -> None:
        with self._lock:
            self._prune_consumed_locked()
            actions: List[Tuple[str, Any, Any]] = []
            for device_id in {
                *[track.camera_id for track in self._tracks.values()],
                *[event.device_id for event in self._recognitions.values()],
            }:
                actions.extend(self._resolve_locked(device_id))

            for track in list(self._tracks.values()):
                age = (now - track.confirmed_at).total_seconds()
                if age < self.track_wait_seconds:
                    continue
                self._consume_track(track)
                if self._device_health.get(track.camera_id, False):
                    actions.append(("anonymous", track, "no_member_recognition"))
                else:
                    actions.append(("unknown", track, "device_unavailable"))

            for recognition in list(self._recognitions.values()):
                age = (now - recognition.occurred_at).total_seconds()
                if age < self.track_wait_seconds:
                    continue
                self._consume_recognition(recognition)
                actions.append(
                    ("member", self._member_session(recognition, None), None)
                )
        self._dispatch(actions)

    def _dispatch(self, actions: List[Tuple[str, Any, Any]]) -> None:
        for kind, value, reason in actions:
            try:
                if kind == "member" and self.on_member_entry:
                    self.on_member_entry(value.to_dict())
                elif kind == "anonymous" and self.on_anonymous_visit:
                    self.on_anonymous_visit(
                        value.track_id, self._detection_dict(value), reason
                    )
                elif kind == "unknown" and self.on_identity_unknown:
                    self.on_identity_unknown(value, reason)
            except Exception:
                logger.exception("Entry coordinator callback failed: kind=%s", kind)
