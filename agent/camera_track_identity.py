"""Same-camera candidate assessment and Runtime-local identity projection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Optional

from agent.camera_runtime_registry import CameraRuntimeRegistry
from agent.device_camera_registration import DeviceCameraRegistrationService
from agent.face_spatial_evidence import FaceSpatialEvidence


@dataclass(frozen=True)
class TrackSnapshot:
    store_id: str
    camera_id: str
    runtime_id: str
    track_id: int
    track_token: str
    observed_at: datetime
    bbox: tuple[float, float, float, float]
    confidence: float
    role: str
    stable_frames: int
    is_frame_clipped: bool
    coordinate_space: str = "normalized_full_frame"
    frame_size: Optional[tuple[int, int]] = None

    @property
    def runtime_instance_id(self) -> str:
        """Compatibility alias; runtime_id is now the instance identity."""
        return self.runtime_id


@dataclass(frozen=True)
class TrackIdentityBinding:
    store_id: str
    camera_id: str
    runtime_id: str
    track_id: int
    track_token: str
    member_id: str
    member_tier: Optional[str]
    visit_id: str
    recognition_event_id: str
    status: str
    method: str
    confidence: float
    recognized_at: datetime
    confirmed_at: Optional[datetime]
    expires_at: datetime
    binding_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    canonical_visit_id: Optional[str] = None
    superseded_by_recognition_id: Optional[str] = None
    superseded_reason: Optional[str] = None


class CacheProjectionInvariantViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class TrackAssociationDecision:
    status: str
    track_token: Optional[str]
    reason: Optional[str]
    candidate_count: int
    candidates: tuple[TrackSnapshot, ...]
    should_continue_wait: bool = False
    window_complete: bool = True
    watermark_at: Optional[datetime] = None
    spatial_diagnostics: tuple[dict, ...] = ()


class TrackSnapshotBuffer:
    """Thread-safe five-second time-series buffer for one StoreRuntime."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 5.0,
        track_lost_seconds: float = 2.0,
        max_snapshots_per_track: int = 180,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        if ttl_seconds <= 0 or track_lost_seconds <= 0 or max_snapshots_per_track < 1:
            raise ValueError("invalid TrackSnapshotBuffer limits")
        self.ttl = timedelta(seconds=ttl_seconds)
        self.track_lost = timedelta(seconds=track_lost_seconds)
        self.max_snapshots_per_track = max_snapshots_per_track
        self.clock = clock
        self._history: dict[str, deque[TrackSnapshot]] = {}
        self._watermarks: dict[tuple[str, str, str], datetime] = {}
        self._lock = RLock()

    def publish(self, snapshot: TrackSnapshot) -> TrackSnapshot:
        with self._lock:
            history = self._history.setdefault(snapshot.track_token, deque())
            previous = history[-1] if history else None
            if previous is not None and snapshot.observed_at > previous.observed_at:
                gap = snapshot.observed_at - previous.observed_at
                stable_frames = previous.stable_frames + 1 if gap <= self.track_lost else 1
            else:
                stable_frames = previous.stable_frames if previous is not None else 1
            stored = TrackSnapshot(
                store_id=snapshot.store_id,
                camera_id=snapshot.camera_id,
                runtime_id=snapshot.runtime_id,
                track_id=snapshot.track_id,
                track_token=snapshot.track_token,
                observed_at=snapshot.observed_at,
                bbox=snapshot.bbox,
                confidence=snapshot.confidence,
                role=snapshot.role,
                stable_frames=stable_frames,
                is_frame_clipped=snapshot.is_frame_clipped,
                coordinate_space=snapshot.coordinate_space,
                frame_size=snapshot.frame_size,
            )
            if history and stored.observed_at < history[-1].observed_at:
                history.append(stored)
                history = deque(sorted(history, key=lambda item: item.observed_at))
                self._history[stored.track_token] = history
            else:
                history.append(stored)
            while len(history) > self.max_snapshots_per_track:
                history.popleft()
            scope = (stored.store_id, stored.camera_id, stored.runtime_id)
            prior_watermark = self._watermarks.get(scope)
            if prior_watermark is None or stored.observed_at > prior_watermark:
                self._watermarks[scope] = stored.observed_at
            self._prune(max(self.clock(), stored.observed_at))
            return stored

    def snapshots(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_id: str,
        occurred_at: datetime,
        window_seconds: float = 0.5,
        include_history: bool = False,
    ) -> tuple[TrackSnapshot, ...]:
        if window_seconds < 0:
            raise ValueError("window_seconds must be non-negative")
        window = timedelta(seconds=window_seconds)
        with self._lock:
            self._prune(max(self.clock(), occurred_at))
            result: list[TrackSnapshot] = []
            for history in self._history.values():
                result.extend(
                    snapshot
                    for snapshot in history
                    if snapshot.store_id == store_id
                    and snapshot.camera_id == camera_id
                    and snapshot.runtime_id == runtime_id
                    and abs(snapshot.observed_at - occurred_at) <= window
                )
            ordered = tuple(sorted(result, key=lambda item: (item.observed_at, item.track_token)))
            if include_history:
                return ordered
            latest: dict[str, TrackSnapshot] = {}
            for snapshot in ordered:
                latest[snapshot.track_token] = snapshot
            return tuple(sorted(latest.values(), key=lambda item: item.track_token))

    def watermark(
        self, *, store_id: str, camera_id: str, runtime_id: str
    ) -> Optional[datetime]:
        with self._lock:
            return self._watermarks.get((store_id, camera_id, runtime_id))

    def latest_exact(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        track_token: str,
    ) -> Optional[TrackSnapshot]:
        with self._lock:
            history = self._history.get(track_token)
            if not history:
                return None
            latest = max(history, key=lambda item: item.observed_at)
            expected_token = (
                f"{store_id}:{camera_id}:{runtime_instance_id}:{latest.track_id}"
            )
            if (
                latest.store_id != store_id
                or latest.camera_id != camera_id
                or latest.runtime_id != runtime_instance_id
                or latest.track_token != track_token
                or track_token != expected_token
            ):
                return None
            return latest

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._watermarks.clear()

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.ttl
        for token, history in list(self._history.items()):
            while history and history[0].observed_at < cutoff:
                history.popleft()
            if not history:
                self._history.pop(token, None)


class StoreTrackCandidateProvider:
    """Assess same-camera candidates without writing identity facts."""

    def __init__(
        self,
        track_snapshot_buffer: TrackSnapshotBuffer,
        track_identity_cache: Optional["CameraTrackIdentityCache"] = None,
        *,
        registration_service: Optional[DeviceCameraRegistrationService] = None,
        runtime_registry: Optional[CameraRuntimeRegistry] = None,
        binder: Optional["SameCameraRecognitionBinder"] = None,
        clock: Callable[[], datetime] = datetime.utcnow,
        runtime_instance_id: Optional[str] = None,
        store_id: Optional[str] = None,
        evidence_provider: Optional[Callable[..., Optional[FaceSpatialEvidence]]] = None,
        policy_service: Optional[object] = None,
    ) -> None:
        self.track_snapshot_buffer = track_snapshot_buffer
        self.track_identity_cache = track_identity_cache
        self.registration_service = registration_service
        self.runtime_registry = runtime_registry
        self.binder = binder or SameCameraRecognitionBinder()
        self.clock = clock
        self.runtime_instance_id = runtime_instance_id
        self.store_id = store_id
        self.evidence_provider = evidence_provider
        self.policy_service = policy_service

    def configure_runtime(
        self,
        *,
        registration_service: DeviceCameraRegistrationService,
        runtime_registry: CameraRuntimeRegistry,
        runtime_instance_id: str,
        store_id: str,
        policy_service: Optional[object] = None,
    ) -> None:
        self.registration_service = registration_service
        self.runtime_registry = runtime_registry
        self.runtime_instance_id = runtime_instance_id
        self.store_id = store_id
        if policy_service is not None:
            self.policy_service = policy_service

    def __call__(self, inbox_row, *, now: Optional[datetime] = None):
        from agent.recognition_association_worker import TrackAssociationAssessment

        TrackAssociationCandidate = TrackAssociationAssessment

        now = (now or self.clock()).replace(tzinfo=None)
        effective = min(inbox_row.occurred_at, now)
        if self.registration_service is None or self.runtime_registry is None:
            return TrackAssociationCandidate(
                status="no_track_match",
                method="candidate_provider_not_configured",
                reason="camera_runtime_registry_missing",
                effective_occurred_at=effective,
            )
        resolution = self.registration_service.resolve_at(
            device_id=inbox_row.device_id,
            occurred_at=inbox_row.occurred_at,
        )
        if resolution.status != "resolved":
            return TrackAssociationCandidate(
                status="no_track_match",
                camera_id=resolution.camera_id,
                method="device_camera_registration",
                reason=resolution.reason or "device_camera_mapping_missing",
                effective_occurred_at=effective,
            )
        camera_id = resolution.camera_id
        if not camera_id or not self.runtime_registry.is_active(
            camera_id,
            store_id=self.store_id or inbox_row.store_id,
            runtime_instance_id=self.runtime_instance_id or "",
        ):
            return TrackAssociationCandidate(
                status="no_track_match",
                camera_id=camera_id,
                method="camera_runtime_registry",
                reason="camera_runtime_inactive",
                effective_occurred_at=effective,
            )
        face = None
        if self.evidence_provider is not None:
            face = self.evidence_provider(inbox_row, camera_id, effective)
        else:
            face = FaceSpatialEvidence.from_safe_dict(
                getattr(inbox_row, "face_spatial_evidence", None)
            )
        if face is not None and face.camera_id is None:
            face = replace(face, camera_id=camera_id)
        reference_time = (
            face.occurred_at
            if face is not None and face.occurred_at is not None
            else effective
        )
        reference_time_source = (
            "face_spatial_evidence"
            if face is not None and face.occurred_at is not None
            else "recognition_effective_time"
        )
        watermark = self.track_snapshot_buffer.watermark(
            store_id=self.store_id or inbox_row.store_id,
            camera_id=camera_id,
            runtime_id=self.runtime_instance_id or "",
        )
        window_complete = (
            watermark is not None
            and watermark >= reference_time + timedelta(seconds=0.5)
        )
        deadline = getattr(inbox_row, "association_deadline_at", None)
        if not window_complete and (deadline is None or now < deadline):
            return TrackAssociationCandidate(
                status="no_track_match",
                camera_id=camera_id,
                method="watermark_wait",
                reason="snapshot_window_pending",
                should_continue_wait=True,
                window_complete=False,
                watermark_at=watermark,
                effective_occurred_at=effective,
            )
        tracks = self.track_snapshot_buffer.snapshots(
            store_id=self.store_id or inbox_row.store_id,
            camera_id=camera_id,
            runtime_id=self.runtime_instance_id or "",
            occurred_at=reference_time,
            window_seconds=0.5,
            include_history=True,
        )
        decision = self.binder.evaluate(
            store_id=self.store_id or inbox_row.store_id,
            camera_id=camera_id,
            runtime_id=self.runtime_instance_id or "",
            effective_occurred_at=effective,
            reference_time=reference_time,
            tracks=tracks,
            face=face,
            window_complete=window_complete,
            watermark_at=watermark,
        )
        eligible_tokens = {item.track_token for item in decision.candidates}
        excluded = tuple(
            {
                "track_token": item.track_token,
                "track_id": item.track_id,
                "role": item.role,
                "confidence": item.confidence,
                "stable_frames": item.stable_frames,
                "is_frame_clipped": item.is_frame_clipped,
                "reason": self.binder.exclusion_reason(
                    item,
                    store_id=self.store_id or inbox_row.store_id,
                    camera_id=camera_id,
                    runtime_id=self.runtime_instance_id or "",
                    effective_occurred_at=reference_time,
                ),
            }
            for item in tracks
            if item.track_token not in eligible_tokens
        )
        policy = self.policy_service.current(camera_id) if self.policy_service is not None else None
        primary_token = decision.track_token or (
            decision.candidates[0].track_token
            if len(decision.candidates) == 1
            else None
        )
        primary = next(
            (
                item
                for item in decision.candidates
                if item.track_token == primary_token
            ),
            None,
        )
        primary_delta = (
            abs((primary.observed_at - reference_time).total_seconds())
            if primary is not None
            else None
        )
        primary_offset = (
            (primary.observed_at - reference_time).total_seconds()
            if primary is not None
            else None
        )
        support = tuple(
            item
            for item in tracks
            if primary is not None
            and item.track_token == primary.track_token
            and item is not primary
        )
        spatial_evidence = None
        if face is not None:
            spatial_evidence = face.to_safe_dict()
            spatial_evidence = {
                "coordinate_space": spatial_evidence.get("coordinate_space"),
                "raw_face_bbox": spatial_evidence.get("raw_bbox"),
                "normalized_face_bbox": spatial_evidence.get("normalized_bbox"),
                "source_frame_size": spatial_evidence.get("source_frame_size"),
                "quality_status": spatial_evidence.get("quality_status"),
                "quality": spatial_evidence.get("quality"),
                "yaw": spatial_evidence.get("yaw"),
                "pitch": spatial_evidence.get("pitch"),
                "roll": spatial_evidence.get("roll"),
                "event_time": spatial_evidence.get("event_time"),
                "reference_time": reference_time.isoformat(),
                "reference_time_source": reference_time_source,
                "effective_occurred_at": effective.isoformat(),
                "face_vs_effective_delta_seconds": (
                    (face.occurred_at - effective).total_seconds()
                    if face.occurred_at is not None
                    else None
                ),
                "primary_snapshot_observed_at": (
                    primary.observed_at.isoformat() if primary is not None else None
                ),
                "primary_time_delta_seconds": primary_delta,
                "primary_time_offset_seconds": primary_offset,
                "primary_track_token": (
                    primary.track_token if primary is not None else None
                ),
                "support_snapshot_count": len(support),
                "support_time_range": (
                    [
                        min(item.observed_at for item in support).isoformat(),
                        max(item.observed_at for item in support).isoformat(),
                    ]
                    if support
                    else None
                ),
                "tracks": [dict(item) for item in decision.spatial_diagnostics],
                "final_candidate_count": sum(
                    bool(item.get("matched"))
                    for item in decision.spatial_diagnostics
                ),
                "assessment_result": decision.status,
            }
        return TrackAssociationCandidate(
            status=decision.status,
            camera_id=camera_id,
            track_token=decision.track_token,
            method=decision.reason,
            reason=decision.reason,
            candidate_count=decision.candidate_count,
            candidates=decision.candidates,
            excluded_candidates=excluded,
            should_continue_wait=decision.should_continue_wait,
            window_complete=decision.window_complete,
            watermark_at=decision.watermark_at,
            effective_occurred_at=effective,
            time_delta_seconds=primary_delta,
            policy_version_id=policy.id if policy is not None else None,
            policy_mode=policy.mode if policy is not None else "shadow",
            algorithm_version=policy.algorithm_version if policy is not None else "track-association-v1",
            parameter_version=policy.parameter_version if policy is not None else "track-association-v1-defaults",
            spatial_evidence=spatial_evidence,
        )


class CameraTrackIdentityCache:
    def __init__(self, *, clock=datetime.utcnow) -> None:
        self.clock = clock
        self._bindings: dict[str, TrackIdentityBinding] = {}
        self.history: list[TrackIdentityBinding] = []
        self._lock = RLock()

    def bind(self, binding: TrackIdentityBinding) -> str:
        self._validate_projection(binding)
        with self._lock:
            self._prune(self.clock())
            existing = self._bindings.get(binding.track_token)
            if existing is not None:
                if self._immutable_projection(existing) != self._immutable_projection(binding):
                    self.history.append(existing)
                    self._bindings.pop(binding.track_token, None)
                    raise CacheProjectionInvariantViolation(
                        "cache_projection_invariant_violation"
                    )
                self.history.append(existing)
                self._bindings[binding.track_token] = binding
                return "already_projected"
            self._bindings[binding.track_token] = binding
            return "projected"

    def resolve_exact(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        track_id: int,
        track_token: str,
    ) -> Optional[dict]:
        expected = f"{store_id}:{camera_id}:{runtime_instance_id}:{track_id}"
        if track_token != expected:
            return None
        with self._lock:
            self._prune(self.clock())
            binding = self._bindings.get(track_token)
            if (
                binding is None
                or binding.store_id != store_id
                or binding.camera_id != camera_id
                or binding.runtime_id != runtime_instance_id
                or binding.track_id != track_id
                or binding.track_token != track_token
                or binding.status != "confirmed"
            ):
                return None
            return {
                "binding_id": binding.binding_id,
                "evaluation_id": binding.evaluation_id,
                "track_token": binding.track_token,
                "member_id": binding.member_id,
                "member_tier": binding.member_tier,
                "visit_id": binding.canonical_visit_id or binding.visit_id,
                "identity_status": "confirmed",
                "identity_confidence": binding.confidence,
                "identity_source": binding.method,
                "recognized_at": binding.recognized_at,
                "confirmed_at": binding.confirmed_at,
                "expires_at": binding.expires_at,
            }

    def resolve(self, track_id: int, *, store_id: Optional[str] = None, camera_id: Optional[str] = None, runtime_id: Optional[str] = None) -> Optional[dict]:
        if store_id is None or camera_id is None or runtime_id is None:
            return None
        token = f"{store_id}:{camera_id}:{runtime_id}:{track_id}"
        return self.resolve_exact(
            store_id=store_id,
            camera_id=camera_id,
            runtime_instance_id=runtime_id,
            track_id=track_id,
            track_token=token,
        )

    def observe_active_track_tokens(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        active_track_tokens: set[str],
        observed_at: datetime,
    ) -> None:
        with self._lock:
            for token, binding in list(self._bindings.items()):
                in_scope = (
                    binding.store_id == store_id
                    and binding.camera_id == camera_id
                    and binding.runtime_id == runtime_instance_id
                )
                if in_scope and (
                    token not in active_track_tokens
                    or binding.expires_at <= observed_at
                ):
                    self.history.append(binding)
                    self._bindings.pop(token, None)

    def invalidate_exact(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        track_id: int,
        track_token: str,
        reason: str,
    ) -> bool:
        expected = f"{store_id}:{camera_id}:{runtime_instance_id}:{track_id}"
        if track_token != expected:
            raise ValueError("track_token scope mismatch")
        with self._lock:
            binding = self._bindings.get(track_token)
            if binding is None:
                return False
            if (
                binding.store_id != store_id
                or binding.camera_id != camera_id
                or binding.runtime_id != runtime_instance_id
                or binding.track_id != track_id
            ):
                return False
            self.history.append(binding)
            self._bindings.pop(track_token, None)
            return True

    def clear_runtime_scope(
        self,
        *,
        store_id: str,
        runtime_instance_id: str,
        reason: str,
        camera_id: Optional[str] = None,
    ) -> int:
        removed = 0
        with self._lock:
            for token, binding in list(self._bindings.items()):
                if (
                    binding.store_id == store_id
                    and binding.runtime_id == runtime_instance_id
                    and (camera_id is None or binding.camera_id == camera_id)
                ):
                    self.history.append(binding)
                    self._bindings.pop(token, None)
                    removed += 1
        return removed

    def clear(self, reason: str) -> None:
        with self._lock:
            self.history.extend(self._bindings.values())
            self._bindings.clear()

    def _prune(self, now: datetime) -> None:
        for token, binding in list(self._bindings.items()):
            if binding.expires_at <= now:
                self.history.append(binding)
                self._bindings.pop(token, None)

    def _validate_projection(self, binding: TrackIdentityBinding) -> None:
        expected = (
            f"{binding.store_id}:{binding.camera_id}:"
            f"{binding.runtime_id}:{binding.track_id}"
        )
        if binding.track_token != expected:
            raise ValueError("track_token scope mismatch")
        if binding.status != "confirmed":
            raise ValueError("cache only accepts confirmed bindings")
        if not binding.binding_id or not binding.evaluation_id:
            raise ValueError("binding_id and evaluation_id are required")
        if binding.confirmed_at is None:
            raise ValueError("confirmed_at is required")
        if binding.expires_at <= self.clock():
            raise ValueError("binding projection is expired")

    @staticmethod
    def _immutable_projection(binding: TrackIdentityBinding) -> tuple:
        return (
            binding.binding_id,
            binding.evaluation_id,
            binding.store_id,
            binding.camera_id,
            binding.runtime_id,
            binding.track_id,
            binding.track_token,
            binding.member_id,
            binding.canonical_visit_id or binding.visit_id,
            binding.method,
            binding.confidence,
            binding.recognized_at,
            binding.confirmed_at,
        )


class SameCameraRecognitionBinder:
    def __init__(self, *, association_window_seconds: float = 1.0, min_track_confidence: float = 0.55, min_stable_frames: int = 3, min_face_containment: float = 0.8, upper_body_ratio: float = 0.45) -> None:
        if not 0 < min_face_containment <= 1:
            raise ValueError("min_face_containment must be between zero and one")
        if not 0 < upper_body_ratio <= 1:
            raise ValueError("upper_body_ratio must be between zero and one")
        self.window = timedelta(seconds=association_window_seconds / 2)
        self.min_track_confidence = min_track_confidence
        self.min_stable_frames = min_stable_frames
        self.min_face_containment = min_face_containment
        self.upper_body_ratio = upper_body_ratio

    def evaluate(self, *, store_id: str, camera_id: str, runtime_id: str, effective_occurred_at: datetime, reference_time: Optional[datetime] = None, tracks: tuple[TrackSnapshot, ...], face: Optional[FaceSpatialEvidence] = None, window_complete: bool = True, watermark_at: Optional[datetime] = None) -> TrackAssociationDecision:
        reference = reference_time or (
            face.occurred_at
            if face is not None and face.occurred_at is not None
            else effective_occurred_at
        )
        eligible = [track for track in tracks if self._eligible(track, store_id, camera_id, runtime_id, reference)]
        by_token: dict[str, TrackSnapshot] = {}
        for track in eligible:
            current = by_token.get(track.track_token)
            if current is None or self._primary_sort_key(track, reference) < self._primary_sort_key(current, reference):
                by_token[track.track_token] = track
        candidates = tuple(sorted(by_token.values(), key=lambda item: item.track_token))
        spatial_diagnostics: tuple[dict, ...] = ()
        geometry_verified = False
        time_window_verified = False
        camera_verified = False
        if face is not None and face.normalized:
            spatial_diagnostics = tuple(
                self._spatial_diagnostic(face, track, effective_occurred_at)
                for track in candidates
            )
            geometry_verified = bool(spatial_diagnostics) and all(
                item["geometry_verified"] for item in spatial_diagnostics
            )
            time_window_verified = bool(spatial_diagnostics) and all(
                item["time_window_match"] for item in spatial_diagnostics
            )
            camera_verified = bool(spatial_diagnostics) and all(
                item["camera_match"] for item in spatial_diagnostics
            )
        if (
            face is not None
            and face.normalized
            and face.quality_status in {"parsed", "verified"}
            and geometry_verified
            and time_window_verified
            and camera_verified
        ):
            spatial_tokens = {
                item["track_token"]
                for item in spatial_diagnostics
                if item["matched"]
            }
            spatial = tuple(
                track for track in candidates
                if track.track_token in spatial_tokens
            )
            if len(spatial) == 1:
                return TrackAssociationDecision("confirmed", spatial[0].track_token, "verified_face_spatial_evidence", len(candidates), candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
            if len(spatial) > 1:
                return TrackAssociationDecision("ambiguous", None, "multiple_spatial_candidates", len(candidates), candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
            return TrackAssociationDecision("no_track_match", None, "face_spatial_no_match", len(candidates), candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
        if face is not None and spatial_diagnostics and not camera_verified:
            if len(candidates) == 1:
                return TrackAssociationDecision("probable", candidates[0].track_token, "face_spatial_camera_mismatch", 1, candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
            if len(candidates) > 1:
                return TrackAssociationDecision("ambiguous", None, "face_spatial_camera_mismatch", len(candidates), candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
        if face is not None and spatial_diagnostics and not time_window_verified:
            if len(candidates) == 1:
                return TrackAssociationDecision("probable", candidates[0].track_token, "face_spatial_time_mismatch", 1, candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
            if len(candidates) > 1:
                return TrackAssociationDecision("ambiguous", None, "face_spatial_time_mismatch", len(candidates), candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
        if face is not None and spatial_diagnostics and not geometry_verified:
            if len(candidates) == 1:
                return TrackAssociationDecision("probable", candidates[0].track_token, "face_spatial_geometry_unverified", 1, candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
            if len(candidates) > 1:
                return TrackAssociationDecision("ambiguous", None, "face_spatial_geometry_unverified", len(candidates), candidates, window_complete=window_complete, watermark_at=watermark_at, spatial_diagnostics=spatial_diagnostics)
        if len(candidates) == 1:
            return TrackAssociationDecision("probable", candidates[0].track_token, "unique_customer_temporal", 1, candidates, window_complete=window_complete, watermark_at=watermark_at)
        if not candidates:
            return TrackAssociationDecision("no_track_match", None, "snapshot_window_incomplete" if not window_complete else "no_eligible_customer_track", 0, (), window_complete=window_complete, watermark_at=watermark_at)
        return TrackAssociationDecision("ambiguous", None, "multiple_customer_tracks_without_face_spatial_evidence", len(candidates), candidates, window_complete=window_complete, watermark_at=watermark_at)

    def exclusion_reason(self, track: TrackSnapshot, *, store_id: str, camera_id: str, runtime_id: str, effective_occurred_at: datetime) -> str:
        if track.store_id != store_id:
            return "store_mismatch"
        if track.camera_id != camera_id:
            return "camera_mismatch"
        if track.runtime_id != runtime_id:
            return "runtime_instance_mismatch"
        if abs(track.observed_at - effective_occurred_at) > self.window:
            return "outside_association_window"
        if track.role != "customer":
            return f"role_excluded:{track.role}"
        if track.confidence < self.min_track_confidence:
            return "track_confidence_below_threshold"
        if track.stable_frames < self.min_stable_frames:
            return "stable_frames_below_threshold"
        if track.is_frame_clipped:
            return "frame_clipped"
        return "not_selected_by_assessment"

    def _eligible(self, track: TrackSnapshot, store_id: str, camera_id: str, runtime_id: str, occurred_at: datetime) -> bool:
        return track.store_id == store_id and track.camera_id == camera_id and track.runtime_id == runtime_id and abs(track.observed_at - occurred_at) <= self.window and track.role == "customer" and track.confidence >= self.min_track_confidence and track.stable_frames >= self.min_stable_frames and not track.is_frame_clipped and track.track_token == f"{store_id}:{camera_id}:{runtime_id}:{track.track_id}"

    @staticmethod
    def _primary_sort_key(
        track: TrackSnapshot, reference_time: datetime
    ) -> tuple[float, int, float, datetime]:
        return (
            abs((track.observed_at - reference_time).total_seconds()),
            -track.stable_frames,
            -track.confidence,
            track.observed_at,
        )

    def _spatial_diagnostic(
        self,
        face: FaceSpatialEvidence,
        track: TrackSnapshot,
        effective_occurred_at: datetime,
    ) -> dict:
        diagnostic = self._geometry_diagnostic(face, track)
        fx1, fy1, fx2, fy2 = face.bbox
        px1, py1, px2, py2 = track.bbox
        center_x = (fx1 + fx2) / 2
        center_y = (fy1 + fy2) / 2
        center_inside = px1 <= center_x <= px2 and py1 <= center_y <= py2
        upper_body_bottom = py1 + (py2 - py1) * self.upper_body_ratio
        upper_body_check = center_inside and py1 <= center_y <= upper_body_bottom
        intersection_width = max(0.0, min(fx2, px2) - max(fx1, px1))
        intersection_height = max(0.0, min(fy2, py2) - max(fy1, py1))
        face_area = max(0.0, fx2 - fx1) * max(0.0, fy2 - fy1)
        intersection_over_face = (
            intersection_width * intersection_height / face_area
            if face_area > 0
            else 0.0
        )
        containment = intersection_over_face >= self.min_face_containment
        face_time_delta = (
            abs((face.occurred_at - effective_occurred_at).total_seconds())
            if face.occurred_at is not None
            else None
        )
        face_event_time_match = (
            face_time_delta <= self.window.total_seconds()
            if face_time_delta is not None
            else face.quality_status == "verified"
        )
        face_reference_time = face.occurred_at or effective_occurred_at
        track_time_delta = abs(
            (track.observed_at - face_reference_time).total_seconds()
        )
        track_time_match = track_time_delta <= self.window.total_seconds()
        time_window_match = face_event_time_match and track_time_match
        camera_match = face.camera_id is None or face.camera_id == track.camera_id
        diagnostic.update({
            "person_bbox": list(track.bbox),
            "face_center": [center_x, center_y],
            "face_center_inside": center_inside,
            "upper_body_check": upper_body_check,
            "intersection_over_face_area": intersection_over_face,
            "containment_threshold": self.min_face_containment,
            "containment": containment,
            "face_time_delta_seconds": face_time_delta,
            "track_time_delta_seconds": track_time_delta,
            "face_event_time_match": face_event_time_match,
            "track_time_match": track_time_match,
            "time_window_match": time_window_match,
            "camera_match": camera_match,
            "matched": bool(
                diagnostic["geometry_verified"]
                and time_window_match
                and camera_match
                and center_inside
                and upper_body_check
                and containment
            ),
        })
        return diagnostic

    @staticmethod
    def _geometry_diagnostic(
        face: FaceSpatialEvidence,
        track: TrackSnapshot,
    ) -> dict:
        source_size = face.source_frame_size
        target_size = track.frame_size
        status = "geometry_metadata_missing"
        verified = face.quality_status == "verified"
        candidate_padding = None
        if source_size is not None and target_size is not None:
            source_width, source_height = source_size
            target_width, target_height = target_size
            source_aspect = source_width / source_height
            target_aspect = target_width / target_height
            if abs(source_aspect - target_aspect) <= 0.001:
                status = "same_aspect_ratio"
                verified = True
            else:
                expected_content_height = source_width / target_aspect
                possible_padding = source_height - expected_content_height
                rounded_padding = round(possible_padding)
                if (
                    0 < rounded_padding <= 16
                    and abs(possible_padding - rounded_padding) <= 0.01
                ):
                    status = "vertical_padding_unverified"
                    candidate_padding = rounded_padding
                    verified = False
                else:
                    status = "aspect_ratio_mismatch"
                    verified = False
        return {
            "track_token": track.track_token,
            "source_frame_size": list(source_size) if source_size is not None else None,
            "target_frame_size": list(target_size) if target_size is not None else None,
            "geometry_status": status,
            "geometry_verified": verified,
            "candidate_vertical_padding_pixels": candidate_padding,
            "matched": False,
        }
