"""Production composition for YOLO tracks plus camera recognition polling."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

from agent.device_identity import (
    DeviceIdentityRepository,
    DeviceRecognitionEvent,
    RecognitionRecordPoller,
)
from agent.device_recognition_intake import DeviceRecognitionIntake
from agent.device_event_worker import DeviceRecognitionInboxWorker
from agent.member_entry_service import MemberEntryService
from agent.identity_runtime_lease import IdentityAssociationRuntimeLeaseService
from agent.identity_runtime_lease import RuntimeLease, RuntimeLeaseUnavailable
from agent.recognition_association_worker import RecognitionAssociationWorker, TrackAssociationCandidate
from agent.reusable_visit_resolver import ReusableVisitResolver
from agent.association_projection import AssociationOutcomeProjectionWorker
from agent.device_health import DeviceHealthStore
from agent.entry_event_coordinator import EntryEventCoordinator
from pipelines.member_business_runtime import MemberBusinessRuntime
from pipelines.realtime_entry_pipeline import (
    AnonymousVisitCallback,
    IdentityUnknownCallback,
    MemberEntryCallback,
    RealtimeEntryPipeline,
    create_realtime_pipeline,
)


logger = logging.getLogger(__name__)


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _ShutdownRequested(Exception):
    """Internal control flow for cancelling partially completed startup."""


class _LifecycleState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"


class DeviceIdentityRuntime:
    """Own the poller, coordinator sweeper and realtime detection pipeline."""

    def __init__(
        self,
        *,
        pipeline: RealtimeEntryPipeline,
        coordinator: Optional[EntryEventCoordinator],
        poller: Optional[RecognitionRecordPoller] = None,
        inbox_worker: Optional[DeviceRecognitionInboxWorker] = None,
        device_health_store: Optional[DeviceHealthStore] = None,
        device_id: Optional[str] = None,
        member_business_runtime: Optional[MemberBusinessRuntime] = None,
        clock: Callable[[], datetime] = datetime.now,
        health_clock: Callable[[], datetime] = _utc_naive_now,
        sweep_interval_seconds: float = 0.25,
        association_workers: tuple[RecognitionAssociationWorker, ...] = (),
        projection_worker: Optional[AssociationOutcomeProjectionWorker] = None,
        association_lease_service: Optional[IdentityAssociationRuntimeLeaseService] = None,
        association_lease: Optional[RuntimeLease] = None,
        association_heartbeat_interval_seconds: float = 10.0,
        video_source: Any = None,
        on_association_lease_lost: Optional[Callable[[str], None]] = None,
        owns_association_lease: bool = True,
        finalization_worker: Any = None,
        binding_heartbeat: Any = None,
    ) -> None:
        self.pipeline = pipeline
        self.poller = poller
        self.inbox_worker = inbox_worker
        self.device_health_store = device_health_store
        self.coordinator = coordinator
        self.device_id = device_id or getattr(poller, "device_id", None)
        self.member_business_runtime = member_business_runtime
        self.clock = clock
        self.health_clock = health_clock
        self.sweep_interval_seconds = sweep_interval_seconds
        self.association_workers = association_workers
        self.projection_worker = projection_worker
        self.association_lease_service = association_lease_service
        self.association_lease = association_lease
        self.association_heartbeat_interval_seconds = association_heartbeat_interval_seconds
        self.video_source = video_source
        self.on_association_lease_lost = on_association_lease_lost
        self.owns_association_lease = owns_association_lease
        self.finalization_worker = finalization_worker
        self.binding_heartbeat = binding_heartbeat
        self._association_last_heartbeat: Optional[datetime] = None
        self._stop = threading.Event()
        self._shutdown_requested = threading.Event()
        bind_shutdown_event = getattr(self.pipeline, "bind_shutdown_event", None)
        if callable(bind_shutdown_event):
            bind_shutdown_event(self._shutdown_requested)
        self._sweeper: Optional[threading.Thread] = None
        self._pipeline_thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()
        self._lifecycle_state = _LifecycleState.STOPPED
        self._services_started = False
        self._poller_cleanup_pending = False
        self._inbox_cleanup_pending = False
        self._business_cleanup_pending = False

    def process_frame(self, frame: Any) -> Dict[str, Any]:
        result = self.pipeline.process_frame(frame)
        if self.coordinator is not None:
            self.coordinator.flush(self.clock())
        return result

    def poll_once(self) -> list[DeviceRecognitionEvent]:
        events = self.poller.poll_once() if self.poller is not None else []
        if self.inbox_worker is not None:
            self.inbox_worker.run_once()
        self._run_association_workers()
        self._refresh_device_health()
        if self.coordinator is not None:
            self.coordinator.flush(self.clock())
        return events

    def _refresh_device_health(self) -> None:
        if self.device_health_store is None or not self.device_id:
            return
        available = self.device_health_store.identity_available(
            self.device_id, self.health_clock()
        )
        if self.coordinator is not None:
            self.coordinator.set_device_health(self.device_id, available)

    def _run_association_workers(self) -> None:
        if self.association_lease_service is not None and self.association_lease is not None:
            now = self.health_clock()
            if (
                self._association_last_heartbeat is None
                or (now - self._association_last_heartbeat).total_seconds()
                >= self.association_heartbeat_interval_seconds
            ):
                try:
                    self.association_lease = self.association_lease_service.heartbeat(
                        self.association_lease
                    )
                    self._association_last_heartbeat = now
                except Exception as heartbeat_error:
                    self.association_workers = ()
                    self.projection_worker = None
                    logger.error(
                        "Association runtime lease refresh failed; workers stopped: "
                        "error_type=%s",
                        type(heartbeat_error).__name__,
                    )
                    if self.on_association_lease_lost is not None:
                        try:
                            self.on_association_lease_lost("runtime_lease_lost")
                        except Exception:
                            logger.exception(
                                "Association lease-loss callback failed; "
                                "runtime is stopping"
                            )
                            self._stop_after_lease_loss()
                    else:
                        self._stop_after_lease_loss()
                    return
        for worker in self.association_workers:
            worker.run_once()
        if self.projection_worker is not None:
            self.projection_worker.run_once()

    def _sweep_forever(self) -> None:
        while not self._stop.wait(self.sweep_interval_seconds):
            try:
                self._refresh_device_health()
            except Exception as error:
                logger.error(
                    "Device health refresh failed: device_id=%s error_type=%s",
                    self.device_id,
                    type(error).__name__,
                )
                if self.device_id:
                    try:
                        if self.coordinator is not None:
                            self.coordinator.set_device_health(self.device_id, False)
                    except Exception as publish_error:
                        logger.error(
                            "Device health fail-closed publish failed: "
                            "device_id=%s error_type=%s",
                            self.device_id,
                            type(publish_error).__name__,
                        )
            try:
                if self.coordinator is not None:
                    self.coordinator.flush(self.clock())
                self._run_association_workers()
            except Exception as error:
                logger.error(
                    "Identity coordinator sweep failed: error_type=%s",
                    type(error).__name__,
                )

    def _stop_after_lease_loss(self) -> None:
        self.request_stop()

        def finish_stop() -> None:
            try:
                self.stop()
            except Exception:
                logger.exception("Identity runtime cleanup failed after lease loss")

        threading.Thread(
            target=finish_stop,
            name=f"identity-lease-loss-stop-{self.device_id or 'device'}",
            daemon=True,
        ).start()

    def _set_lifecycle_state(self, state: _LifecycleState) -> None:
        self._lifecycle_state = state
        self._services_started = state is not _LifecycleState.STOPPED

    def _cleanup_services(self) -> Optional[Exception]:
        """Attempt every owned cleanup and retain only unfinished ownership."""
        self._stop.set()
        first_error: Optional[Exception] = None

        for worker in (self.finalization_worker, self.binding_heartbeat):
            request_stop = getattr(worker, "request_stop", None)
            if callable(request_stop):
                request_stop()

        if self._sweeper is not None:
            if self._sweeper.is_alive():
                try:
                    self._sweeper.join(timeout=2.0)
                except Exception as error:
                    if first_error is None:
                        first_error = error
            if self._sweeper.is_alive():
                if first_error is None:
                    first_error = RuntimeError(
                        "Identity coordinator sweeper is still running"
                    )
            else:
                self._sweeper = None

        for worker in (self.finalization_worker, self.binding_heartbeat):
            if worker is None:
                continue
            try:
                worker.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error

        if self._pipeline_thread is not None:
            if self._pipeline_thread.is_alive():
                try:
                    self._pipeline_thread.join(timeout=2.0)
                except Exception as error:
                    if first_error is None:
                        first_error = error
            if self._pipeline_thread.is_alive():
                if first_error is None:
                    first_error = RuntimeError(
                        "Identity runtime pipeline is still running"
                    )
            else:
                self._pipeline_thread = None

        if self._poller_cleanup_pending:
            try:
                if self.poller is not None:
                    self.poller.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._poller_cleanup_pending = False

        if self._inbox_cleanup_pending:
            try:
                if self.inbox_worker is not None:
                    self.inbox_worker.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._inbox_cleanup_pending = False

        if self._business_cleanup_pending:
            try:
                if self.member_business_runtime is not None:
                    self.member_business_runtime.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._business_cleanup_pending = False

        if (
            self.owns_association_lease
            and self.association_lease_service is not None
            and self.association_lease is not None
        ):
            try:
                self.association_lease_service.release(self.association_lease)
            except RuntimeLeaseUnavailable:
                self.association_lease = None
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self.association_lease = None

        cleanup_pending = (
            self._poller_cleanup_pending
            or self._inbox_cleanup_pending
            or self._sweeper is not None
            or self._pipeline_thread is not None
            or self._business_cleanup_pending
            or (
                self.owns_association_lease
                and self.association_lease is not None
            )
        )
        self._set_lifecycle_state(
            _LifecycleState.STOPPING
            if cleanup_pending
            else _LifecycleState.STOPPED
        )
        return first_error

    def start_background_services(self) -> bool:
        with self._lifecycle_lock:
            if self._shutdown_requested.is_set():
                return False
            if self._lifecycle_state is _LifecycleState.RUNNING:
                return True
            if self._lifecycle_state is _LifecycleState.STOPPING:
                raise RuntimeError("Device identity services are still stopping")
            if self._sweeper and self._sweeper.is_alive():
                raise RuntimeError("Identity coordinator sweeper is still stopping")

            self._stop.clear()

            def ensure_startup_allowed() -> None:
                if self._shutdown_requested.is_set():
                    raise _ShutdownRequested()

            try:
                ensure_startup_allowed()
                if self.member_business_runtime is not None:
                    self._business_cleanup_pending = True
                    self.member_business_runtime.start()
                    ensure_startup_allowed()
                if self.inbox_worker is not None:
                    self._inbox_cleanup_pending = True
                    self.inbox_worker.start()
                    ensure_startup_allowed()
                if self.poller is not None:
                    self._poller_cleanup_pending = True
                    self.poller.start()
                    ensure_startup_allowed()
                if self.finalization_worker is not None:
                    self.finalization_worker.start()
                    ensure_startup_allowed()
                if self.binding_heartbeat is not None:
                    self.binding_heartbeat.start()
                    ensure_startup_allowed()
                self._refresh_device_health()
                self._sweeper = threading.Thread(
                    target=self._sweep_forever,
                    name=f"identity-coordinator-{self.device_id or 'device'}",
                    daemon=True,
                )
                self._sweeper.start()
                self._set_lifecycle_state(_LifecycleState.RUNNING)
                ensure_startup_allowed()
                return True
            except _ShutdownRequested as startup_error:
                cleanup_error = self._cleanup_services()
                if cleanup_error is not None:
                    raise cleanup_error from startup_error
                return False
            except Exception as startup_error:
                cleanup_error = self._cleanup_services()
                if cleanup_error is not None:
                    raise cleanup_error from startup_error
                raise

    def start(self) -> bool:
        """Start workers and, when configured, the realtime pipeline thread."""
        started = self.start_background_services()
        if not started or self.video_source is None:
            return started
        if self._pipeline_thread is not None and self._pipeline_thread.is_alive():
            return True
        self._pipeline_thread = threading.Thread(
            target=self.pipeline.run,
            args=(self.video_source,),
            name=f"identity-pipeline-{self.device_id or 'device'}",
            daemon=True,
        )
        self._pipeline_thread.start()
        return True

    def request_stop(self) -> None:
        """Persist a shutdown request without taking the lifecycle lock."""
        already_requested = self._shutdown_requested.is_set()
        self._shutdown_requested.set()
        self._stop.set()
        if not already_requested:
            self.pipeline.stop()

    def stop(self) -> None:
        self.request_stop()
        with self._lifecycle_lock:
            if self._lifecycle_state is _LifecycleState.STOPPED:
                if not (
                    self.owns_association_lease
                    and self.association_lease_service is not None
                    and self.association_lease is not None
                ):
                    return
            first_error = self._cleanup_services()
            if first_error is not None:
                raise first_error

    def run(self, video_source: Any) -> Dict[str, Any]:
        if not self.start_background_services():
            return {}
        try:
            if self._shutdown_requested.is_set():
                return {}
            return self.pipeline.run(video_source)
        finally:
            self.stop()


def create_device_identity_runtime(
    *,
    client: Any,
    repository: DeviceIdentityRepository,
    session_factory: Any,
    capture_store: Any = None,
    recognition_similarity_threshold: float = 0.75,
    detector: Any = None,
    model_path: Optional[str] = None,
    device: str = "cpu",
    conf_threshold: float = 0.5,
    stability_threshold: int = 3,
    process_every_seconds: float = 0.5,
    poll_interval_seconds: float = 30.0,
    inbox_poll_interval_seconds: float = 0.25,
    inbox_processing_timeout_seconds: float = 300.0,
    device_health_freshness_seconds: float = 90.0,
    on_member_entry: Optional[MemberEntryCallback] = None,
    on_anonymous_visit: Optional[AnonymousVisitCallback] = None,
    on_identity_unknown: Optional[IdentityUnknownCallback] = None,
    on_device_health_change: Optional[Callable[[str, bool, str], None]] = None,
    on_warning: Optional[Callable[[str, str], None]] = None,
    member_business_runtime: Optional[MemberBusinessRuntime] = None,
    clock: Callable[[], datetime] = datetime.now,
    health_clock: Callable[[], datetime] = _utc_naive_now,
    identity_association_mode: str,
    association_store_id: Optional[str] = None,
    association_runtime_owner_id: Optional[str] = None,
    track_candidate_provider: Optional[Callable[[Any], TrackAssociationCandidate]] = None,
    association_lease_service: Optional[IdentityAssociationRuntimeLeaseService] = None,
    association_lease: Optional[RuntimeLease] = None,
    store_resolution_service: Any = None,
    resolver: Optional[ReusableVisitResolver] = None,
    projection_worker: Optional[AssociationOutcomeProjectionWorker] = None,
    association_audit_service: Any = None,
    runtime_instance_id: Optional[str] = None,
    association_deadline_seconds: float = 5.0,
    inbox_min_received_at: Optional[datetime] = None,
    component_debug: bool = False,
    video_source: Any = None,
    on_association_lease_lost: Optional[Callable[[str], None]] = None,
    finalization_worker: Any = None,
    binding_heartbeat: Any = None,
) -> DeviceIdentityRuntime:
    """Build the production path; no local FaceRecognizer is constructed."""

    if identity_association_mode not in {"new", "legacy"}:
        raise ValueError("identity_association_mode must be explicitly new or legacy")
    if identity_association_mode == "new" and member_business_runtime is not None:
        raise ValueError("new mode must not start legacy member business runtime")
    if identity_association_mode == "new" and not association_store_id:
        raise ValueError("new mode requires association_store_id")
    if identity_association_mode == "new" and not association_runtime_owner_id:
        raise ValueError("new mode requires association_runtime_owner_id")
    if identity_association_mode == "new" and not component_debug:
        if association_lease_service is None or association_lease is None:
            raise ValueError(
                "new production runtime must be built by StoreRuntime"
            )
        if track_candidate_provider is None:
            raise ValueError("new production runtime requires CandidateProvider")
        if any(
            value is None
            for value in (store_resolution_service, resolver, projection_worker)
        ):
            raise ValueError(
                "new production StoreRuntime must inject StoreResolutionService, "
                "ReusableVisitResolver, and Projection Worker"
            )
    if identity_association_mode == "legacy" and any(
        value is not None
        for value in (resolver, projection_worker, track_candidate_provider)
    ):
        raise ValueError("legacy mode must not configure new association workers")

    def member_entered(session: Dict[str, Any]) -> None:
        if member_business_runtime is not None:
            member_business_runtime.handle_member_entry(session)
        if on_member_entry is not None:
            on_member_entry(session)

    coordinator = None
    if identity_association_mode == "legacy":
        coordinator = EntryEventCoordinator(
            on_member_entry=member_entered,
            on_anonymous_visit=on_anonymous_visit,
            on_identity_unknown=on_identity_unknown,
            association_window_seconds=2.0,
            track_wait_seconds=3.0,
        )

    health_store = DeviceHealthStore(
        session_factory,
        freshness_seconds=device_health_freshness_seconds,
    )

    def health_changed(device_id: str, healthy: bool, reason: str) -> None:
        available = healthy
        if health_store is not None:
            observed_at = health_clock()
            if healthy:
                health_store.mark_poll_success(device_id, observed_at)
            else:
                health_store.mark_poll_failure(device_id, reason)
            available = health_store.identity_available(device_id, observed_at)
        if coordinator is not None:
            coordinator.set_device_health(device_id, available)
        if on_device_health_change:
            on_device_health_change(device_id, healthy, reason)

    intake = DeviceRecognitionIntake(
        session_factory,
        capture_store=capture_store,
        similarity_threshold=recognition_similarity_threshold,
    )
    inbox_worker = DeviceRecognitionInboxWorker(
        session_factory=session_factory,
        coordinator=coordinator,
        intake=intake,
        member_arrival=(
            member_business_runtime
            if identity_association_mode == "legacy"
            else MemberEntryService(session_factory, profile_enabled=False, clock=clock)
        ),
        reconcile_waiting=intake._reconcile_waiting,
        poll_interval_seconds=inbox_poll_interval_seconds,
        processing_timeout_seconds=inbox_processing_timeout_seconds,
        clock=clock,
        identity_association_mode=identity_association_mode,
        new_mode_store_id=association_store_id,
        association_deadline_seconds=association_deadline_seconds,
        min_received_at=inbox_min_received_at,
    )
    poller = RecognitionRecordPoller(
        client=client,
        repository=repository,
        intake=intake,
        on_health_change=health_changed,
        on_warning=on_warning,
        poll_interval_seconds=poll_interval_seconds,
        clock=clock,
    )

    if detector is None:
        pipeline = create_realtime_pipeline(
            model_path=model_path,
            device=device,
            conf_threshold=conf_threshold,
            face_recognizer=None,
            stability_threshold=stability_threshold,
            process_every_seconds=process_every_seconds,
            camera_id=client.device_id,
            identity_mode="device_api",
            on_stable_track=(
                coordinator.submit_track if coordinator is not None else None
            ),
            on_stable_track_frame=(
                member_business_runtime.capture_track
                if member_business_runtime is not None
                else None
            ),
            on_identity_unknown=on_identity_unknown,
            identity_clock=clock,
        )
    else:
        pipeline = RealtimeEntryPipeline(
            detector=detector,
            face_recognizer=None,
            stability_threshold=stability_threshold,
            process_every_seconds=process_every_seconds,
            camera_id=client.device_id,
            identity_mode="device_api",
            on_stable_track=(
                coordinator.submit_track if coordinator is not None else None
            ),
            on_stable_track_frame=(
                member_business_runtime.capture_track
                if member_business_runtime is not None
                else None
            ),
            on_identity_unknown=on_identity_unknown,
            identity_clock=clock,
        )

    association_workers = ()
    externally_owned_lease = association_lease is not None
    lease_service = association_lease_service
    lease = association_lease
    if identity_association_mode == "new" or (
        identity_association_mode == "legacy"
        and association_store_id
        and association_runtime_owner_id
    ):
        if lease_service is None:
            lease_service = IdentityAssociationRuntimeLeaseService(
                session_factory, clock=health_clock
            )
        if lease is None:
            lease = lease_service.acquire(
                store_id=association_store_id,
                mode=identity_association_mode,
                owner_id=association_runtime_owner_id,
            )
    if identity_association_mode == "new":
        if resolver is None:
            resolver = ReusableVisitResolver(
                session_factory,
                clock=health_clock,
                store_resolution_service=store_resolution_service,
                runtime_lease_service=lease_service,
            )
        fallback = RecognitionAssociationWorker(
            session_factory, resolver, lease, clock=health_clock, fallback=True,
            candidate_provider=track_candidate_provider,
            audit_service=association_audit_service,
            runtime_instance_id=runtime_instance_id,
        )
        if track_candidate_provider is None:
            association_workers = (fallback,)
        else:
            binder = RecognitionAssociationWorker(
                session_factory, resolver, lease, clock=health_clock,
                candidate_provider=track_candidate_provider,
                audit_service=association_audit_service,
                runtime_instance_id=runtime_instance_id,
            )
            association_workers = (binder, fallback)
        if projection_worker is None:
            projection_worker = AssociationOutcomeProjectionWorker(
                session_factory, clock=health_clock
            )

    return DeviceIdentityRuntime(
        pipeline=pipeline,
        coordinator=coordinator,
        poller=poller,
        inbox_worker=inbox_worker,
        device_health_store=health_store,
        device_id=client.device_id,
        member_business_runtime=member_business_runtime,
        clock=clock,
        health_clock=health_clock,
        association_workers=association_workers,
        projection_worker=projection_worker,
        association_lease_service=lease_service,
        association_lease=lease,
        video_source=video_source,
        on_association_lease_lost=on_association_lease_lost,
        owns_association_lease=not externally_owned_lease,
        finalization_worker=finalization_worker,
        binding_heartbeat=binding_heartbeat,
    )
