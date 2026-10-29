"""Composition root for the three-camera week-one behavior runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Callable, Mapping, Optional, Protocol

from sqlalchemy.orm import Session

from agent.behavior_events import BehaviorEventStore
from agent.behavior_monitor import (
    BehaviorMonitor,
    BehaviorMonitorConfig,
    TrackObservation,
)
from agent.behavior_vision import (
    BehaviorCameraConfig,
    BehaviorVisionAdapter,
    draw_behavior_area_overlay,
)
from agent.camera_track_identity import (
    CameraTrackIdentityCache,
    TrackSnapshot,
    TrackSnapshotBuffer,
)
from agent.camera_runtime_registry import CameraRuntimeRegistry
from agent.detector import open_video_capture
from agent.customer_entry_visit import (
    CustomerEntryObservation,
    CustomerEntryVisit,
)
from agent.staff_greeting_evaluation import (
    GreetingShadowConfig,
    StaffGreetingEvaluation,
)


class VisitActivityObservationSink(Protocol):
    def observe_track(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        track_id: int,
        track_token: str,
        observed_at: datetime,
    ) -> bool: ...


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BehaviorCameraRuntime:
    def __init__(
        self,
        adapter: BehaviorVisionAdapter,
        monitor: BehaviorMonitor,
        camera: Optional[BehaviorCameraConfig] = None,
        *,
        staff_greeting: Optional[StaffGreetingEvaluation] = None,
        customer_entry_visit: Optional[CustomerEntryVisit] = None,
        entry_store_id: Optional[str] = None,
        entry_runtime_session_id: Optional[str] = None,
        identity_association_mode: str = "legacy",
        track_snapshot_buffer: Optional[TrackSnapshotBuffer] = None,
        track_identity_cache: Optional[CameraTrackIdentityCache] = None,
        visit_activity_refresher: Optional[VisitActivityObservationSink] = None,
        runtime_instance_id: Optional[str] = None,
        reconnect_interval_seconds: Optional[float] = None,
    ) -> None:
        entry_context = (
            customer_entry_visit,
            entry_store_id,
            entry_runtime_session_id,
        )
        if any(value is not None for value in entry_context) and not all(
            value is not None for value in entry_context
        ):
            raise ValueError(
                "customer_entry_visit, entry_store_id, and "
                "entry_runtime_session_id must be configured together"
            )
        self.adapter = adapter
        self.monitor = monitor
        self.camera = camera
        self.staff_greeting = staff_greeting
        self.customer_entry_visit = customer_entry_visit
        self.entry_store_id = entry_store_id
        self.entry_runtime_session_id = entry_runtime_session_id
        self.identity_association_mode = identity_association_mode
        self.track_snapshot_buffer = track_snapshot_buffer
        self.track_identity_cache = track_identity_cache
        self.visit_activity_refresher = visit_activity_refresher
        self.runtime_instance_id = runtime_instance_id or getattr(self.adapter, "runtime_id", None)

    def process_frame(self, frame, observed_at: datetime):
        observations = self.adapter.process_frame(frame)
        person_tracks = tuple(getattr(self.adapter, "last_person_tracks", ()))
        store_id = self.entry_store_id or getattr(self.adapter, "store_id", None)
        camera_id = getattr(self.adapter, "camera_id", None)
        runtime_id = getattr(self.adapter, "runtime_id", None)
        if self.track_snapshot_buffer is not None:
            roles = {item.track_token: item.role for item in observations}
            if store_id and camera_id and runtime_id:
                frame_height, frame_width = frame.shape[:2]
                for track in person_tracks:
                    self.track_snapshot_buffer.publish(TrackSnapshot(
                        store_id=store_id,
                        camera_id=camera_id,
                        runtime_id=runtime_id,
                        track_id=track.track_id,
                        track_token=track.track_token,
                        observed_at=observed_at,
                        bbox=(
                            track.bbox[0] / frame_width,
                            track.bbox[1] / frame_height,
                            track.bbox[2] / frame_width,
                            track.bbox[3] / frame_height,
                        ),
                        confidence=track.confidence,
                        role=roles.get(track.track_token, "unknown"),
                        stable_frames=1,
                        is_frame_clipped=track.is_frame_clipped,
                        coordinate_space="normalized_full_frame",
                        frame_size=(frame_width, frame_height),
                    ))
        if (
            self.track_identity_cache is not None
            and store_id
            and camera_id
            and runtime_id
        ):
            self.track_identity_cache.observe_active_track_tokens(
                store_id=store_id,
                camera_id=camera_id,
                runtime_instance_id=runtime_id,
                active_track_tokens={track.track_token for track in person_tracks},
                observed_at=observed_at,
            )
        if (
            self.visit_activity_refresher is not None
            and store_id
            and camera_id
            and runtime_id
        ):
            for track in person_tracks:
                self.visit_activity_refresher.observe_track(
                    store_id=store_id,
                    camera_id=camera_id,
                    runtime_instance_id=runtime_id,
                    track_id=track.track_id,
                    track_token=track.track_token,
                    observed_at=observed_at,
                )
        return self.process_observations(observed_at, observations)

    def process_observations(
        self,
        observed_at: datetime,
        observations: list[TrackObservation] | tuple[TrackObservation, ...],
    ):
        """Run existing BehaviorMonitor processing for one observation batch.

        Adapters own frame/model conversion. This method deliberately starts
        after that conversion so replay sources can reuse the same monitor path.
        """
        observations = tuple(observations)
        entries, behaviors = self.monitor.process(observed_at, observations)
        if self.customer_entry_visit is not None:
            for entry in entries:
                self.customer_entry_visit.confirm_entry(
                    CustomerEntryObservation.from_behavior_entry(
                        entry,
                        store_id=self.entry_store_id,
                        runtime_session_id=self.entry_runtime_session_id,
                    )
                )
        if self.staff_greeting is not None:
            behaviors.extend(
                self.staff_greeting.observe(observed_at, observations, entries)
            )
        return entries, behaviors

    def zone_runtime_diagnostics(self):
        """Expose the monitor's latest read-only zone replay diagnostics."""

        return self.monitor.zone_runtime_diagnostics()

    def render_overlay(self, frame):
        if self.camera is None:
            rendered = frame.copy()
        else:
            rendered = draw_behavior_area_overlay(frame, self.camera)
        track_state_lines = {
            observation.track_token: (
                self.monitor.entry_debug_lines(observation.track_token)
                + (
                    self.staff_greeting.debug_lines(observation.track_token)
                    if self.staff_greeting is not None
                    else ()
                )
            )
            for observation in getattr(self.adapter, "last_observations", ())
        }
        return self.adapter.render_debug_overlay(
            rendered,
            track_state_lines=track_state_lines,
        )

    def cancel_greetings(self, observed_at: datetime, cancel_reason: str):
        if self.staff_greeting is None:
            return ()
        return self.staff_greeting.cancel_all(observed_at, cancel_reason)


class BehaviorRuntime:
    """Process synchronized frames from configured cameras without owning capture threads."""

    def __init__(
        self,
        cameras: dict[str, BehaviorCameraRuntime],
        *,
        track_snapshot_buffer: Optional[TrackSnapshotBuffer] = None,
        track_identity_cache: Optional[CameraTrackIdentityCache] = None,
    ) -> None:
        if not cameras:
            raise ValueError("at least one behavior camera is required")
        self.cameras = dict(cameras)
        self.track_snapshot_buffer = track_snapshot_buffer
        self.track_identity_cache = track_identity_cache

    def process_frames(self, frames: dict[str, object], observed_at: datetime):
        results = {}
        for camera_id, frame in frames.items():
            runtime = self.cameras.get(camera_id)
            if runtime is None:
                continue
            results[camera_id] = runtime.process_frame(frame, observed_at)
        return results

    def cancel_camera(
        self, camera_id: str, observed_at: datetime, cancel_reason: str
    ):
        runtime = self.cameras.get(camera_id)
        if runtime is None:
            return ()
        return runtime.cancel_greetings(observed_at, cancel_reason)

    def shutdown(self, observed_at: datetime):
        cancelled = {}
        for camera_id, runtime in self.cameras.items():
            cancelled[camera_id] = runtime.cancel_greetings(
                observed_at, "runtime_shutdown"
            )
        return cancelled


class BehaviorCaptureRunner:
    """Own configured video captures and feed their frames to a BehaviorRuntime."""

    def __init__(
        self,
        runtime: BehaviorRuntime,
        sources: Mapping[str, str],
        *,
        capture_factory: Callable[[str | int], object] = open_video_capture,
        clock: Callable[[], datetime] = _utc_naive_now,
        startup_timeout_seconds: float = 10.0,
        camera_runtime_registry: Optional[CameraRuntimeRegistry] = None,
        store_id: Optional[str] = None,
        runtime_instance_id: Optional[str] = None,
        reconnect_interval_seconds: Optional[float] = None,
    ) -> None:
        if not sources:
            raise ValueError("at least one behavior camera source is required")
        unknown = set(sources) - set(runtime.cameras)
        if unknown:
            raise ValueError(f"unconfigured behavior camera sources: {sorted(unknown)}")
        self.runtime = runtime
        self.sources = dict(sources)
        self.capture_factory = capture_factory
        self.clock = clock
        self.startup_timeout_seconds = startup_timeout_seconds
        self.camera_runtime_registry = camera_runtime_registry
        self.store_id = store_id
        self.runtime_instance_id = runtime_instance_id
        if reconnect_interval_seconds is not None and reconnect_interval_seconds <= 0:
            raise ValueError("reconnect_interval_seconds must be positive")
        self.reconnect_interval_seconds = reconnect_interval_seconds
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._startup_complete = threading.Event()
        self.last_result: Optional[dict[str, int]] = None
        self.last_error: Optional[BaseException] = None

    def request_stop(self) -> None:
        self._running = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.last_error = None
        self._startup_complete.clear()
        self._thread = threading.Thread(
            target=self._run_background,
            name="behavior-capture-runner",
            daemon=True,
        )
        self._thread.start()
        if not self._startup_complete.wait(self.startup_timeout_seconds):
            self.request_stop()
            raise RuntimeError("Behavior capture runner startup timed out")
        self._thread.join(timeout=0.01)
        if self.last_error is not None:
            raise self.last_error
        if not self._thread.is_alive():
            self._thread = None
            raise RuntimeError("Behavior capture runner stopped during startup")

    def stop(self) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                raise RuntimeError("Behavior capture runner is still running")
            self._thread = None

    def wait(self) -> dict[str, int]:
        thread = self._thread
        if thread is not None:
            thread.join()
        if self.last_error is not None:
            raise self.last_error
        return self.last_result or {
            "frames_read": 0,
            "frames_processed": 0,
            "entry_events": 0,
            "behavior_events": 0,
        }

    def _run_background(self) -> None:
        try:
            self.last_result = self.run()
        except BaseException as error:
            self.last_error = error
        finally:
            self._startup_complete.set()

    def run(self) -> dict[str, int]:
        captures = {}
        reconnect_at: dict[str, float] = {}
        frames_read = 0
        frames_processed = 0
        entry_events = 0
        behavior_events = 0
        frames_ever_processed = False
        self._running = True
        try:
            for camera_id, source in self.sources.items():
                capture = self.capture_factory(_video_source(source))
                if not capture.isOpened():
                    capture.release()
                    raise RuntimeError(f"cannot open behavior camera source: {camera_id}")
                captures[camera_id] = capture
            if self.camera_runtime_registry is not None:
                if not self.store_id or not self.runtime_instance_id:
                    raise RuntimeError("camera runtime registry requires StoreRuntime identity")
                for camera_id in captures:
                    self.camera_runtime_registry.activate(
                        store_id=self.store_id,
                        camera_id=camera_id,
                        runtime_instance_id=self.runtime_instance_id,
                    )
            self._startup_complete.set()

            while self._running:
                monotonic_now = time.monotonic()
                for camera_id, source in self.sources.items():
                    if self.reconnect_interval_seconds is None:
                        break
                    if camera_id in captures or monotonic_now < reconnect_at.get(camera_id, 0.0):
                        continue
                    capture = self.capture_factory(_video_source(source))
                    if not capture.isOpened():
                        capture.release()
                        reconnect_at[camera_id] = monotonic_now + self.reconnect_interval_seconds
                        continue
                    captures[camera_id] = capture
                    reconnect_at.pop(camera_id, None)
                    if self.camera_runtime_registry is not None:
                        self.camera_runtime_registry.activate(
                            store_id=self.store_id,
                            camera_id=camera_id,
                            runtime_instance_id=self.runtime_instance_id,
                        )
                frames = {}
                for camera_id, capture in list(captures.items()):
                    available, frame = capture.read()
                    if not available:
                        self.runtime.cancel_camera(
                            camera_id, self.clock(), "camera_disconnect"
                        )
                        if self.camera_runtime_registry is not None:
                            self.camera_runtime_registry.deactivate(
                                camera_id,
                                runtime_instance_id=self.runtime_instance_id,
                            )
                        capture.release()
                        captures.pop(camera_id)
                        if self.reconnect_interval_seconds is not None:
                            reconnect_at[camera_id] = (
                                time.monotonic() + self.reconnect_interval_seconds
                            )
                        continue
                    frames[camera_id] = frame
                    frames_read += 1
                if not frames:
                    if not captures and not frames_ever_processed:
                        raise RuntimeError("Behavior capture stopped during startup")
                    if not captures and self.reconnect_interval_seconds is None:
                        break
                    time.sleep(min(0.05, self.reconnect_interval_seconds))
                    continue
                frames_ever_processed = True
                results = self.runtime.process_frames(frames, self.clock())
                frames_processed += len(results)
                for entries, behaviors in results.values():
                    entry_events += len(entries)
                    behavior_events += len(behaviors)
        finally:
            self._startup_complete.set()
            self._running = False
            self.runtime.shutdown(self.clock())
            if self.camera_runtime_registry is not None and self.runtime_instance_id is not None:
                for camera_id in self.sources:
                    self.camera_runtime_registry.deactivate(
                        camera_id,
                        runtime_instance_id=self.runtime_instance_id,
                    )
            for capture in captures.values():
                capture.release()

        return {
            "frames_read": frames_read,
            "frames_processed": frames_processed,
            "entry_events": entry_events,
            "behavior_events": behavior_events,
        }


def _video_source(value: str) -> str | int:
    try:
        return int(value)
    except ValueError:
        return value


def create_behavior_camera_runtime(
    *,
    camera: BehaviorCameraConfig,
    session_factory,
    person_model_path: str,
    staff_vest_model_path: Optional[str],
    pose_model_path: str,
    device: str = "cuda:0",
    tracker: str = "bytetrack.yaml",
    conf_threshold: float = 0.5,
    config: Optional[BehaviorMonitorConfig] = None,
    identity_resolver: Optional[Callable[[int], Optional[dict]]] = None,
    person_model=None,
    staff_vest_model=None,
    pose_model=None,
    staff_vest_enabled: bool = True,
    staff_vest_confidence_threshold: float = 0.68,
    role_window_frames: int = 5,
    staff_hit_frames: int = 3,
    maximum_missing_frames: int = 8,
    greeting_mock_staff_name: Optional[str] = None,
    runtime_id: Optional[str] = None,
    runtime_instance_id: Optional[str] = None,
    greeting_v2_enabled: bool = True,
    realtime_greeting_enabled: bool = False,
    greeting_v2_config: Optional[GreetingShadowConfig] = None,
    identity_association_mode: str = "legacy",
    track_snapshot_buffer: Optional[TrackSnapshotBuffer] = None,
    track_identity_cache: Optional[CameraTrackIdentityCache] = None,
    visit_activity_refresher: Optional[VisitActivityObservationSink] = None,
    entry_track_identity_resolver: Optional[Callable[[CustomerEntryObservation], Optional[dict]]] = None,
    entry_runtime_lease_validator: Optional[
        Callable[[Session, CustomerEntryObservation], None]
    ] = None,
) -> BehaviorCameraRuntime:
    store = BehaviorEventStore(
        session_factory,
        greeting_mock_staff_name=greeting_mock_staff_name,
        store_id=camera.store_id,
    )
    adapter = BehaviorVisionAdapter(
        store_id=camera.store_id,
        camera_id=camera.camera_id,
        person_model_path=person_model_path,
        staff_vest_model_path=staff_vest_model_path,
        pose_model_path=pose_model_path,
        person_model=person_model,
        staff_vest_model=staff_vest_model,
        pose_model=pose_model,
        tracker=tracker,
        device=device,
        conf_threshold=conf_threshold,
        staff_vest_enabled=staff_vest_enabled,
        staff_vest_confidence_threshold=staff_vest_confidence_threshold,
        role_window_frames=role_window_frames,
        staff_hit_frames=staff_hit_frames,
        maximum_missing_frames=maximum_missing_frames,
        ground_projector=camera.ground_projector,
        entry_trigger=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        calibration_status=camera.calibration_status,
        calibration_image_size=camera.calibration_image_size,
        identity_resolver=identity_resolver,
        runtime_id=runtime_instance_id or runtime_id,
    )
    effective_config = config or camera.monitor_config
    monitor = BehaviorMonitor(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        interaction_areas=camera.interaction_areas,
        display_areas=camera.display_areas,
        config=effective_config,
        behavior_sink=store.record_behavior,
        calibration_status=camera.calibration_status,
        ground_calibration_available=camera.ground_projector is not None,
    )
    staff_greeting = StaffGreetingEvaluation(
        camera_id=camera.camera_id,
        entrance_area=camera.entry_trigger,
        greeting_area=camera.greeting_area,
        config=effective_config,
        behavior_sink=store.record_behavior,
        calibration_status=camera.calibration_status,
        ground_calibration_available=camera.ground_projector is not None,
        comparison_enabled=greeting_v2_enabled,
        session_factory=session_factory,
        shadow_config=greeting_v2_config,
    ) if realtime_greeting_enabled else None
    customer_entry_visit = CustomerEntryVisit(
        session_factory,
        identity_association_mode=identity_association_mode,
        track_identity_resolver=entry_track_identity_resolver,
        runtime_lease_validator=entry_runtime_lease_validator,
    )
    return BehaviorCameraRuntime(
        adapter,
        monitor,
        camera,
        staff_greeting=staff_greeting,
        customer_entry_visit=customer_entry_visit,
        entry_store_id=camera.store_id,
        entry_runtime_session_id=adapter.runtime_id,
        identity_association_mode=identity_association_mode,
        track_snapshot_buffer=track_snapshot_buffer,
        track_identity_cache=track_identity_cache,
        visit_activity_refresher=visit_activity_refresher,
        runtime_instance_id=runtime_instance_id or runtime_id,
    )


def create_behavior_runtime(
    *,
    cameras: tuple[BehaviorCameraConfig, ...],
    session_factory,
    person_model_path: str,
    staff_vest_model_path: Optional[str],
    pose_model_path: str,
    device: str = "cuda:0",
    tracker: str = "bytetrack.yaml",
    conf_threshold: float = 0.5,
    config: Optional[BehaviorMonitorConfig] = None,
    identity_resolver: Optional[Callable[[str, int], Optional[dict]]] = None,
    model_loader: Optional[Callable[[str], object]] = None,
    staff_vest_enabled: bool = True,
    staff_vest_confidence_threshold: float = 0.68,
    role_window_frames: int = 5,
    staff_hit_frames: int = 3,
    greeting_mock_staff_name: Optional[str] = None,
    maximum_missing_frames: int = 8,
    greeting_v2_enabled: bool = True,
    realtime_greeting_enabled: bool = False,
    greeting_v2_config: Optional[GreetingShadowConfig] = None,
    identity_association_mode: str = "legacy",
    track_snapshot_buffer: Optional[TrackSnapshotBuffer] = None,
    track_identity_cache: Optional[CameraTrackIdentityCache] = None,
    visit_activity_refresher: Optional[VisitActivityObservationSink] = None,
    runtime_id: Optional[str] = None,
    runtime_instance_id: Optional[str] = None,
    entry_runtime_lease_validator: Optional[
        Callable[[Session, CustomerEntryObservation], None]
    ] = None,
) -> BehaviorRuntime:
    runtimes = {}
    for camera in cameras:
        resolver = (
            None
            if identity_resolver is None
            else lambda track_id, camera_id=camera.camera_id: identity_resolver(
                camera_id, track_id
            )
        )
        if resolver is None and track_identity_cache is not None:
            resolver = lambda track_id, camera=camera: track_identity_cache.resolve_exact(
                store_id=camera.store_id,
                camera_id=camera.camera_id,
                runtime_instance_id=runtime_instance_id or runtime_id,
                track_id=track_id,
                track_token=(
                    f"{camera.store_id}:{camera.camera_id}:"
                    f"{runtime_instance_id or runtime_id}:{track_id}"
                ),
            )
        entry_identity_resolver = None
        if track_identity_cache is not None:
            entry_identity_resolver = lambda entry, camera=camera: track_identity_cache.resolve_exact(
                store_id=camera.store_id,
                camera_id=camera.camera_id,
                runtime_instance_id=runtime_instance_id or runtime_id,
                track_id=entry.tracker_track_id,
                track_token=entry.track_token,
            )
        camera_vest_enabled = (
            staff_vest_enabled
            if camera.staff_vest_enabled is None
            else camera.staff_vest_enabled
        )
        person_model = model_loader(person_model_path) if model_loader else None
        staff_vest_model = (
            model_loader(staff_vest_model_path)
            if model_loader and camera_vest_enabled and staff_vest_model_path
            else None
        )
        pose_model = model_loader(pose_model_path) if model_loader else None
        runtimes[camera.camera_id] = create_behavior_camera_runtime(
            camera=camera,
            session_factory=session_factory,
            person_model_path=person_model_path,
            staff_vest_model_path=staff_vest_model_path,
            pose_model_path=pose_model_path,
            device=device,
            tracker=tracker,
            conf_threshold=conf_threshold,
            config=config,
            identity_resolver=resolver,
            person_model=person_model,
            staff_vest_model=staff_vest_model,
            pose_model=pose_model,
            staff_vest_enabled=camera_vest_enabled,
            staff_vest_confidence_threshold=staff_vest_confidence_threshold,
            role_window_frames=role_window_frames,
            staff_hit_frames=staff_hit_frames,
            greeting_mock_staff_name=greeting_mock_staff_name,
            maximum_missing_frames=maximum_missing_frames,
            greeting_v2_enabled=greeting_v2_enabled,
            realtime_greeting_enabled=realtime_greeting_enabled,
            greeting_v2_config=greeting_v2_config,
            identity_association_mode=identity_association_mode,
            track_snapshot_buffer=track_snapshot_buffer,
            track_identity_cache=track_identity_cache,
            visit_activity_refresher=visit_activity_refresher,
            entry_track_identity_resolver=entry_identity_resolver,
            entry_runtime_lease_validator=entry_runtime_lease_validator,
            runtime_id=runtime_instance_id or runtime_id,
            runtime_instance_id=runtime_instance_id or runtime_id,
        )
    return BehaviorRuntime(
        runtimes,
        track_snapshot_buffer=track_snapshot_buffer,
        track_identity_cache=track_identity_cache,
    )
