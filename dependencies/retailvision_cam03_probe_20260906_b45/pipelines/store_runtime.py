"""Single-Store composition root for the new identity association runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable, Optional
from uuid import uuid4

from agent.association_projection import AssociationOutcomeProjectionWorker
from agent.camera_track_identity import (
    CameraTrackIdentityCache,
    SameCameraRecognitionBinder,
    StoreTrackCandidateProvider,
    TrackSnapshotBuffer,
)
from agent.camera_runtime_registry import CameraRuntimeRegistry
from agent.device_camera_registration import DeviceCameraRegistrationService
from agent.identity_runtime_lease import (
    IdentityAssociationRuntimeLeaseService,
    RuntimeLeaseUnavailable,
)
from agent.reusable_visit_resolver import ReusableVisitResolver
from agent.store_resolution import StoreResolutionService
from agent.track_association_policy import TrackAssociationAuditService
from agent.track_association_finalization import (
    BindingLifecycleHeartbeat,
    TrackAssociationFinalizationWorker,
)
from agent.visit_activity import VisitActivityRefresher, VisitActivityService


logger = logging.getLogger(__name__)

_MODES = frozenset({"new", "legacy"})


class _CompositionLeaseKeeper:
    def __init__(self, service: Any, lease: Any, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("composition heartbeat interval must be positive")
        self.service = service
        self.lease = lease
        self.interval_seconds = interval_seconds
        self._lock = RLock()
        self._stop = Event()
        self._error: Optional[BaseException] = None
        self._thread: Optional[Thread] = None

    def start(self) -> None:
        if not callable(getattr(self.service, "heartbeat", None)):
            return
        self._thread = Thread(
            target=self._run,
            name="store-runtime-composition-lease",
            daemon=True,
        )
        self._thread.start()

    def refresh(self) -> Any:
        with self._lock:
            self._raise_if_failed()
            heartbeat = getattr(self.service, "heartbeat", None)
            if callable(heartbeat):
                self.lease = heartbeat(self.lease)
            return self.lease

    def stop(self) -> Any:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
            if self._thread.is_alive():
                raise RuntimeError("composition lease keeper did not stop")
        self._raise_if_failed()
        return self.lease

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.refresh()
            except BaseException as error:
                self._error = error
                self._stop.set()
                return

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise RuntimeError("Store runtime lease refresh failed") from self._error


@dataclass
class StoreRuntimeDependencies:
    store_id: str
    mode: str
    owner_id: str
    runtime_instance_id: str
    runtime_started_at: Any
    lease_service: Any
    lease: Any
    track_snapshot_buffer: TrackSnapshotBuffer
    track_identity_cache: CameraTrackIdentityCache
    camera_runtime_registry: CameraRuntimeRegistry
    candidate_provider: Any = None
    device_camera_registration: Any = None
    association_audit: Any = None
    store_resolution: Any = None
    resolver: Any = None
    projection_worker: Any = None
    finalization_worker: Any = None
    binding_heartbeat: Any = None
    visit_activity_refresher: Any = None

    @property
    def runtime_owner_id(self) -> str:
        return self.owner_id


class StoreRuntime:
    """Own every component for one Store and its fenced lifecycle."""

    def __init__(
        self,
        *,
        dependencies: StoreRuntimeDependencies,
        behavior_runtime: Any,
        identity_runtime: Any,
    ) -> None:
        self.dependencies = dependencies
        self.behavior_runtime = behavior_runtime
        self.identity_runtime = identity_runtime
        self.mode = dependencies.mode
        self.store_id = dependencies.store_id
        self.owner_id = dependencies.owner_id
        self._state = "stopped"
        self._lease_released = False
        self._lifecycle_lock = RLock()
        self._stopped = Event()
        self._stopped.set()
        self._stop_requested = Event()
        self._fatal_event = Event()
        self._fatal_supervisor_stop = Event()
        self._fatal_kind: Optional[str] = None
        self._fatal_reason: Optional[str] = None
        self._fatal_supervisor: Optional[Thread] = None

    @classmethod
    def build(
        cls,
        *,
        session_factory: Callable[[], Any],
        store_id: str,
        mode: str,
        owner_id: str,
        lease_service: Any = None,
        behavior_builder: Callable[[StoreRuntimeDependencies], Any],
        identity_builder: Callable[[StoreRuntimeDependencies, Callable[[str], None]], Any],
        track_snapshot_buffer_factory: Callable[..., TrackSnapshotBuffer] = TrackSnapshotBuffer,
        track_identity_cache_factory: Callable[[], CameraTrackIdentityCache] = CameraTrackIdentityCache,
        candidate_provider_factory: Optional[
            Callable[[TrackSnapshotBuffer, CameraTrackIdentityCache], Any]
        ] = None,
        store_resolution_factory: Callable[[Callable[[], Any]], Any] = StoreResolutionService,
        resolver_factory: Callable[[Callable[[], Any], Any, Any], Any] = (
            lambda sessions, store_resolution, lease_service: ReusableVisitResolver(
                sessions,
                store_resolution_service=store_resolution,
                runtime_lease_service=lease_service,
            )
        ),
        projection_worker_factory: Callable[[Callable[[], Any]], Any] = (
            lambda sessions: AssociationOutcomeProjectionWorker(sessions)
        ),
        lease_clock: Optional[Callable[[], Any]] = None,
        composition_heartbeat_interval_seconds: float = 10.0,
        runtime_instance_id_factory: Callable[[], str] = lambda: uuid4().hex,
        camera_runtime_registry_factory: Callable[[], CameraRuntimeRegistry] = CameraRuntimeRegistry,
        device_camera_registration_factory: Callable[[Callable[[], Any]], Any] = DeviceCameraRegistrationService,
        association_audit_factory: Callable[[Callable[[], Any]], Any] = TrackAssociationAuditService,
        runtime_started_at_override: Optional[datetime] = None,
        snapshot_buffer_ttl_seconds: float = 5.0,
        track_lost_seconds: float = 2.0,
        snapshot_buffer_max_per_track: int = 180,
        association_window_seconds: float = 1.0,
        min_track_confidence: float = 0.55,
        min_stable_frames: int = 3,
        face_spatial_min_containment: float = 0.8,
        face_spatial_upper_body_ratio: float = 0.45,
        phase2b_finalization_enabled: bool = False,
        transform_status_provider: Callable[[str], str] = lambda _camera_id: "unverified",
        finalization_poll_interval_seconds: float = 0.25,
        finalization_claim_lease_seconds: float = 30.0,
        finalization_retry_base_seconds: float = 0.5,
        finalization_retry_max_seconds: float = 30.0,
        binding_heartbeat_interval_seconds: Optional[float] = None,
        finalization_worker_factory: Callable[..., Any] = TrackAssociationFinalizationWorker,
        binding_heartbeat_factory: Callable[..., Any] = BindingLifecycleHeartbeat,
        visit_activity_refresh_interval_seconds: float = 5.0,
        visit_activity_future_tolerance_seconds: float = 30.0,
        visit_activity_service_factory: Callable[..., Any] = VisitActivityService,
        visit_activity_refresher_factory: Callable[..., Any] = VisitActivityRefresher,
    ) -> "StoreRuntime":
        if mode not in _MODES:
            raise ValueError("IDENTITY_ASSOCIATION_MODE must be explicitly new or legacy")
        if not isinstance(store_id, str) or not store_id.strip():
            raise ValueError("association_store_id is required")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("association_runtime_owner_id is required")
        if not isinstance(phase2b_finalization_enabled, bool):
            raise ValueError("phase2b_finalization_enabled must be a boolean")
        if mode == "legacy" and phase2b_finalization_enabled:
            raise ValueError("Phase 2B Finalization requires new mode")
        if (
            snapshot_buffer_ttl_seconds <= 0
            or track_lost_seconds <= 0
            or snapshot_buffer_max_per_track < 1
            or association_window_seconds <= 0
            or not 0 <= min_track_confidence <= 1
            or min_stable_frames < 1
            or not 0 < face_spatial_min_containment <= 1
            or not 0 < face_spatial_upper_body_ratio <= 1
            or visit_activity_refresh_interval_seconds <= 0
            or visit_activity_future_tolerance_seconds < 0
        ):
            raise ValueError("invalid Track Association runtime configuration")

        if lease_service is None:
            lease_kwargs = {} if lease_clock is None else {"clock": lease_clock}
            lease_service = IdentityAssociationRuntimeLeaseService(
                session_factory, **lease_kwargs
            )
        lease = lease_service.acquire(
            store_id=store_id,
            mode=mode,
            owner_id=owner_id,
        )
        lease_keeper = _CompositionLeaseKeeper(
            lease_service,
            lease,
            composition_heartbeat_interval_seconds,
        )
        lease_keeper.start()
        behavior_runtime = None
        identity_runtime = None
        try:
            runtime_instance_id = runtime_instance_id_factory()
            runtime_started_at = runtime_started_at_override or datetime.utcnow().replace(tzinfo=None)
            if not isinstance(runtime_instance_id, str) or not runtime_instance_id.strip():
                raise ValueError("runtime_instance_id_factory must return a non-empty string")
            try:
                snapshot_buffer = track_snapshot_buffer_factory(
                    ttl_seconds=snapshot_buffer_ttl_seconds,
                    track_lost_seconds=track_lost_seconds,
                    max_snapshots_per_track=snapshot_buffer_max_per_track,
                )
            except TypeError as error:
                if not any(name in str(error) for name in ("ttl_seconds", "track_lost_seconds", "max_snapshots_per_track")):
                    raise
                snapshot_buffer = track_snapshot_buffer_factory()
            identity_cache = track_identity_cache_factory()
            camera_runtime_registry = camera_runtime_registry_factory()
            candidate_provider = None
            device_camera_registration = None
            association_audit = None
            store_resolution = None
            resolver = None
            projection_worker = None
            finalization_worker = None
            binding_heartbeat = None
            visit_activity_refresher = None
            if mode == "new":
                registration_service = device_camera_registration_factory(session_factory)
                device_camera_registration = registration_service
                association_audit = association_audit_factory(session_factory)
                candidate_provider = (
                    candidate_provider_factory(snapshot_buffer, identity_cache)
                    if candidate_provider_factory is not None
                    else StoreTrackCandidateProvider(
                        snapshot_buffer,
                        identity_cache,
                        registration_service=registration_service,
                        runtime_registry=camera_runtime_registry,
                        runtime_instance_id=runtime_instance_id,
                        store_id=store_id,
                        policy_service=association_audit.policies,
                        binder=SameCameraRecognitionBinder(
                            association_window_seconds=association_window_seconds,
                            min_track_confidence=min_track_confidence,
                            min_stable_frames=min_stable_frames,
                            min_face_containment=face_spatial_min_containment,
                            upper_body_ratio=face_spatial_upper_body_ratio,
                        ),
                    )
                )
                configure_provider = getattr(candidate_provider, "configure_runtime", None)
                if callable(configure_provider):
                    try:
                        configure_provider(
                            registration_service=registration_service,
                            runtime_registry=camera_runtime_registry,
                            runtime_instance_id=runtime_instance_id,
                            store_id=store_id,
                            policy_service=association_audit.policies,
                        )
                    except TypeError as error:
                        if "policy_service" not in str(error):
                            raise
                        configure_provider(
                            registration_service=registration_service,
                            runtime_registry=camera_runtime_registry,
                            runtime_instance_id=runtime_instance_id,
                            store_id=store_id,
                        )
                store_resolution = store_resolution_factory(session_factory)
                resolver = resolver_factory(
                    session_factory, store_resolution, lease_service
                )
                resolver.runtime_instance_id = runtime_instance_id
                resolver.phase2b_finalization_enabled = phase2b_finalization_enabled
                resolver.transform_status_provider = transform_status_provider
                projection_worker = projection_worker_factory(session_factory)
                if phase2b_finalization_enabled:
                    finalization_worker = finalization_worker_factory(
                        session_factory,
                        lease_service=lease_service,
                        lease=lease,
                        runtime_instance_id=runtime_instance_id,
                        track_snapshot_buffer=snapshot_buffer,
                        track_identity_cache=identity_cache,
                        transform_status_provider=transform_status_provider,
                        track_lost_seconds=track_lost_seconds,
                        poll_interval_seconds=finalization_poll_interval_seconds,
                        claim_lease_seconds=finalization_claim_lease_seconds,
                        retry_base_seconds=finalization_retry_base_seconds,
                        retry_max_seconds=finalization_retry_max_seconds,
                    )
                    binding_heartbeat = binding_heartbeat_factory(
                        session_factory,
                        lease_service=lease_service,
                        lease=lease,
                        runtime_instance_id=runtime_instance_id,
                        track_snapshot_buffer=snapshot_buffer,
                        track_identity_cache=identity_cache,
                        track_lost_seconds=track_lost_seconds,
                        heartbeat_interval_seconds=binding_heartbeat_interval_seconds,
                    )
                    visit_activity_service = visit_activity_service_factory(
                        session_factory,
                        future_clock_skew_tolerance_seconds=(
                            visit_activity_future_tolerance_seconds
                        ),
                    )
                    visit_activity_refresher = visit_activity_refresher_factory(
                        visit_activity_service,
                        track_identity_cache=identity_cache,
                        refresh_interval_seconds=(
                            visit_activity_refresh_interval_seconds
                        ),
                    )

            dependencies = StoreRuntimeDependencies(
                store_id=store_id,
                mode=mode,
                owner_id=owner_id,
                runtime_instance_id=runtime_instance_id,
                runtime_started_at=runtime_started_at,
                lease_service=lease_service,
                lease=lease,
                track_snapshot_buffer=snapshot_buffer,
                track_identity_cache=identity_cache,
                camera_runtime_registry=camera_runtime_registry,
                candidate_provider=candidate_provider,
                device_camera_registration=device_camera_registration,
                association_audit=association_audit,
                store_resolution=store_resolution,
                resolver=resolver,
                projection_worker=projection_worker,
                finalization_worker=finalization_worker,
                binding_heartbeat=binding_heartbeat,
                visit_activity_refresher=visit_activity_refresher,
            )
            runtime: Optional[StoreRuntime] = None

            def on_lease_lost(reason: str = "runtime_lease_lost") -> None:
                if runtime is not None:
                    runtime.signal_fatal("runtime_lease_lost", reason)

            def on_identity_integrity_fatal(error: BaseException) -> None:
                if runtime is not None:
                    runtime.signal_fatal(
                        "identity_integrity_violation", type(error).__name__
                    )

            if finalization_worker is not None:
                finalization_worker.lease_lost_callback = on_lease_lost
                finalization_worker.integrity_fatal_callback = on_identity_integrity_fatal
            if binding_heartbeat is not None:
                binding_heartbeat.lease_lost_callback = on_lease_lost
                binding_heartbeat.integrity_fatal_callback = on_identity_integrity_fatal

            behavior_runtime = behavior_builder(dependencies)
            dependencies.lease = lease_keeper.refresh()
            identity_runtime = identity_builder(dependencies, on_lease_lost)
            dependencies.lease = lease_keeper.refresh()
            if hasattr(identity_runtime, "association_lease"):
                identity_runtime.association_lease = dependencies.lease
            dependencies.lease = lease_keeper.stop()
            runtime = cls(
                dependencies=dependencies,
                behavior_runtime=behavior_runtime,
                identity_runtime=identity_runtime,
            )
            return runtime
        except Exception:
            try:
                lease_keeper.stop()
            except Exception:
                logger.exception("Composition lease keeper cleanup failed")
            for component in (
                visit_activity_refresher,
                identity_runtime,
                behavior_runtime,
            ):
                if component is None:
                    continue
                try:
                    cls._stop_component(component)
                except Exception:
                    logger.exception(
                        "Runtime cleanup failed during Store composition"
                    )
            try:
                cls._release_with_retries(lease_service, lease_keeper.lease)
            except Exception as release_error:
                raise RuntimeError(
                    "StoreRuntime construction failed and lease release failed"
                ) from release_error
            raise

    @property
    def state(self) -> str:
        return self._state

    @property
    def runtime_owner_id(self) -> str:
        return self.owner_id

    @property
    def runtime_instance_id(self) -> str:
        return self.dependencies.runtime_instance_id

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._lease_released:
                raise RuntimeError("StoreRuntime lease has already been released")
            if self._state == "running":
                return
            if self._state == "failed":
                raise RuntimeError(
                    "StoreRuntime is failed; cleanup must succeed before restart"
                )
            if self._state in {"starting", "stopping"}:
                raise RuntimeError(f"StoreRuntime is {self._state}")
            self._state = "starting"
            self._stopped.clear()
            try:
                self._start_fatal_supervisor()
                self._ensure_startup_allowed()
                self._refresh_lease()
                if self.dependencies.visit_activity_refresher is not None:
                    self._start_component(
                        self.dependencies.visit_activity_refresher
                    )
                self._ensure_startup_allowed()
                self._start_component(self.behavior_runtime)
                self._ensure_startup_allowed()
                self._refresh_lease()
                self._start_component(self.identity_runtime)
                self._ensure_startup_allowed()
                self._refresh_lease()
                self._ensure_startup_allowed()
                self._state = "running"
            except Exception:
                cleanup_failed = False
                for component in (
                    self.dependencies.visit_activity_refresher,
                    self.identity_runtime,
                    self.behavior_runtime,
                ):
                    if component is None:
                        continue
                    try:
                        self._stop_component(component)
                    except Exception:
                        cleanup_failed = True
                        logger.exception(
                            "Store component cleanup failed during startup: store_id=%s",
                            self.store_id,
                        )
                if cleanup_failed:
                    self._state = "failed"
                else:
                    try:
                        self._release_lease()
                    except Exception:
                        logger.exception(
                            "Store runtime lease release failed during startup rollback"
                        )
                        self._state = "failed"
                    else:
                        self._state = "stopped"
                self._stopped.set()
                self._stop_fatal_supervisor()
                raise

    def run(self) -> None:
        self.start()
        self._stopped.wait()

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._state == "starting":
                self._stop_requested.set()
                return
            if self._state == "stopped":
                self._release_lease()
                return
            self._state = "stopping"
            first_error: Optional[Exception] = None
            for component in (
                self.dependencies.visit_activity_refresher,
                self.identity_runtime,
                self.behavior_runtime,
            ):
                if component is None:
                    continue
                try:
                    self._stop_component(component)
                except Exception as error:
                    if first_error is None:
                        first_error = error
            self._clear_runtime_cache("runtime_stop")
            if first_error is None:
                try:
                    self._release_lease()
                except Exception:
                    self._state = "failed"
                    self._stopped.set()
                    raise
                else:
                    self._state = "stopped"
            else:
                self._state = "failed"
            self._stopped.set()
            self._stop_fatal_supervisor()
            if first_error is not None:
                raise first_error

    def _ensure_startup_allowed(self) -> None:
        if self._stop_requested.is_set():
            raise RuntimeError("StoreRuntime startup was stopped")

    def handle_lease_lost(self, reason: str = "runtime_lease_lost") -> None:
        self._handle_fatal("runtime_lease_lost", reason)

    def signal_fatal(self, kind: str, reason: str) -> None:
        if kind not in {"runtime_lease_lost", "identity_integrity_violation"}:
            raise ValueError("unsupported StoreRuntime fatal kind")
        if self._fatal_supervisor_stop.is_set():
            return
        self._fatal_kind = kind
        self._fatal_reason = reason
        self._fatal_event.set()

    def _handle_fatal(self, kind: str, reason: str) -> None:
        """Fail closed for one Store without touching other Store runtimes."""
        with self._lifecycle_lock:
            if self._state == "failed":
                return
            self._state = "failed"
            logger.error(
                "Store identity runtime fatal: store_id=%s kind=%s reason=%s",
                self.store_id,
                kind,
                reason,
            )
            try:
                if self.dependencies.visit_activity_refresher is not None:
                    try:
                        self._stop_component(
                            self.dependencies.visit_activity_refresher
                        )
                    except Exception:
                        logger.exception(
                            "Visit activity refresher failed to stop after fatal: "
                            "store_id=%s",
                            self.store_id,
                        )
                try:
                    self._stop_component(self.identity_runtime)
                except Exception:
                    logger.exception(
                        "Identity runtime failed to stop after fatal: store_id=%s",
                        self.store_id,
                    )
                try:
                    self._stop_component(self.behavior_runtime)
                except Exception:
                    logger.exception(
                        "Behavior runtime failed to stop after fatal: store_id=%s",
                        self.store_id,
                    )
            finally:
                self._clear_runtime_cache(kind)
                release_error: Optional[Exception] = None
                if kind == "identity_integrity_violation":
                    try:
                        self._release_lease()
                    except Exception as error:
                        release_error = error
                        logger.exception(
                            "Store runtime lease release failed after integrity fatal: "
                            "store_id=%s",
                            self.store_id,
                        )
                else:
                    self._lease_released = True
                self._stopped.set()
                if release_error is not None:
                    raise release_error

    def _start_fatal_supervisor(self) -> None:
        if self._fatal_supervisor is not None and self._fatal_supervisor.is_alive():
            return
        self._fatal_supervisor_stop.clear()
        self._fatal_event.clear()
        self._fatal_supervisor = Thread(
            target=self._supervise_fatal,
            name=f"store-runtime-supervisor-{self.store_id}",
            daemon=True,
        )
        self._fatal_supervisor.start()

    def _supervise_fatal(self) -> None:
        self._fatal_event.wait()
        if self._fatal_supervisor_stop.is_set():
            return
        kind = self._fatal_kind or "identity_integrity_violation"
        reason = self._fatal_reason or "runtime_fatal"
        try:
            self._handle_fatal(kind, reason)
        except Exception:
            logger.exception("StoreRuntime fatal teardown failed")

    def _stop_fatal_supervisor(self) -> None:
        self._fatal_supervisor_stop.set()
        self._fatal_event.set()
        thread = self._fatal_supervisor
        if thread is not None and thread is not current_thread():
            thread.join(timeout=2.0)
        self._fatal_supervisor = None

    def _clear_runtime_cache(self, reason: str) -> None:
        self.dependencies.track_identity_cache.clear_runtime_scope(
            store_id=self.store_id,
            runtime_instance_id=self.runtime_instance_id,
            reason=reason,
        )

    def _release_lease(self) -> None:
        if self._lease_released:
            return
        try:
            self.dependencies.lease_service.release(self.dependencies.lease)
        except RuntimeLeaseUnavailable:
            self._lease_released = True
        else:
            self._lease_released = True

    def _refresh_lease(self) -> None:
        lease = self._heartbeat_lease(
            self.dependencies.lease_service,
            self.dependencies.lease,
        )
        self.dependencies.lease = lease
        if hasattr(self.identity_runtime, "association_lease"):
            self.identity_runtime.association_lease = lease

    @staticmethod
    def _heartbeat_lease(service: Any, lease: Any) -> Any:
        heartbeat = getattr(service, "heartbeat", None)
        return heartbeat(lease) if callable(heartbeat) else lease

    @staticmethod
    def _release_with_retries(service: Any, lease: Any, attempts: int = 3) -> None:
        last_error: Optional[Exception] = None
        for _attempt in range(attempts):
            try:
                service.release(lease)
                return
            except RuntimeLeaseUnavailable:
                return
            except Exception as error:
                last_error = error
        assert last_error is not None
        raise last_error

    @staticmethod
    def _start_component(component: Any) -> None:
        start = getattr(component, "start", None)
        if callable(start):
            result = start()
            if result is False:
                raise RuntimeError("StoreRuntime component reported startup failure")
            return
        start_background = getattr(component, "start_background_services", None)
        if callable(start_background):
            result = start_background()
            if result is False:
                raise RuntimeError("StoreRuntime component reported startup failure")
            return
        raise TypeError("StoreRuntime component must expose start()")

    @staticmethod
    def _stop_component(component: Any) -> None:
        stop = getattr(component, "stop", None)
        if callable(stop):
            stop()
            return
        request_stop = getattr(component, "request_stop", None)
        if callable(request_stop):
            request_stop()
            return
        raise TypeError("StoreRuntime component must expose stop()")

    @staticmethod
    def _request_stop(component: Any) -> None:
        request_stop = getattr(component, "request_stop", None)
        if callable(request_stop):
            request_stop()
            return
        StoreRuntime._stop_component(component)
