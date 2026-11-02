"""Composition and lifecycle for durable member-entry business processing."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional

from agent.visits.customer_entry_visit import CustomerEntryVisit
from agent.legacy.member_entry_service import MemberEntryResult, MemberEntryService
from agent.profile.profile_jobs import ProfileJobWorker
from agent.profile.profile_snapshot_store import ProfileSnapshotStore
from pipelines.legacy.customer_profile_pipeline import create_customer_profile_pipeline


logger = logging.getLogger(__name__)


class _LifecycleState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"


class MemberBusinessRuntime:
    """Own snapshot capture, durable arrival finalization, and profile work."""

    def __init__(
        self,
        *,
        snapshot_store: ProfileSnapshotStore,
        service: MemberEntryService,
        profile_worker: Optional[ProfileJobWorker],
        recovery_interval_seconds: float,
        snapshot_ttl_seconds: float,
        profile_job_max_attempts: int = 5,
        customer_entry_visit: Optional[CustomerEntryVisit] = None,
    ) -> None:
        self.snapshot_store = snapshot_store
        self.service = service
        self.profile_worker = profile_worker
        self.recovery_interval_seconds = recovery_interval_seconds
        self.snapshot_ttl_seconds = snapshot_ttl_seconds
        self.profile_job_max_attempts = profile_job_max_attempts
        self.customer_entry_visit = customer_entry_visit
        self._stop = threading.Event()
        self._recovery_thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()
        self._lifecycle_state = _LifecycleState.STOPPED
        self._started = False
        self._worker_cleanup_pending = False

    def capture_track(self, event: Any, frame: Any) -> None:
        self.snapshot_store.capture(event, frame)

    def handle_member_entry(
        self, session: Mapping[str, Any]
    ) -> MemberEntryResult:
        prepared = dict(session)
        if self.customer_entry_visit is not None:
            event_id = prepared.get("device_event_id")
            member_id = prepared.get("member_id")
            camera_id = prepared.get("camera_id") or prepared.get("device_id")
            occurred_at = prepared.get("entry_time") or prepared.get("occurred_at")
            if isinstance(occurred_at, (int, float)):
                occurred_at = datetime.fromtimestamp(occurred_at)
            if (
                isinstance(event_id, str)
                and isinstance(member_id, str)
                and isinstance(camera_id, str)
                and isinstance(occurred_at, datetime)
            ):
                attached = self.customer_entry_visit.attach_recognition_identity(
                    event_id,
                    camera_id=camera_id,
                    occurred_at=occurred_at,
                    member_id=member_id,
                    confidence=float(prepared.get("confidence") or 0.0),
                )
                if attached is not None:
                    prepared["visit_id"] = attached.visit_id
        return self.service.finalize(prepared)

    def prepare_member_arrival(self, event: Any) -> str:
        """Create or debounce the Member Arrival candidate after Inbox intake."""
        return self.service.prepare_recognition(
            {
                "device_event_id": event.event_id,
                "member_id": event.member_id,
                "camera_id": event.device_id,
                "occurred_at": event.occurred_at,
                "entry_time": event.occurred_at,
                "identity_time": event.occurred_at,
                "confidence": event.similarity,
                "identity_source": "device_api",
            }
        )

    def _set_lifecycle_state(self, state: _LifecycleState) -> None:
        self._lifecycle_state = state
        self._started = state is not _LifecycleState.STOPPED

    def _cleanup_children(self) -> Optional[Exception]:
        """Attempt every owned cleanup and retain only unfinished ownership."""
        self._stop.set()
        first_error: Optional[Exception] = None

        thread = self._recovery_thread
        if thread is not None:
            if thread.is_alive():
                try:
                    thread.join(timeout=2.0)
                except Exception as error:
                    first_error = error
            if thread.is_alive():
                if first_error is None:
                    first_error = RuntimeError(
                        "Member business recovery thread is still running"
                    )
            else:
                self._recovery_thread = None

        if self._worker_cleanup_pending:
            try:
                if self.profile_worker is not None:
                    self.profile_worker.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._worker_cleanup_pending = False

        cleanup_pending = (
            self._recovery_thread is not None
            or self._worker_cleanup_pending
        )
        self._set_lifecycle_state(
            _LifecycleState.STOPPING
            if cleanup_pending
            else _LifecycleState.STOPPED
        )
        return first_error

    def start(self) -> None:
        """Start profile work, perform recovery, then start periodic recovery."""
        with self._lifecycle_lock:
            if self._lifecycle_state is _LifecycleState.RUNNING:
                return
            if self._lifecycle_state is _LifecycleState.STOPPING:
                if (
                    self._recovery_thread is not None
                    and self._recovery_thread.is_alive()
                ):
                    raise RuntimeError(
                        "Member business recovery is still stopping"
                    )
                cleanup_error = self._cleanup_children()
                if cleanup_error is not None:
                    raise cleanup_error
                if self._lifecycle_state is not _LifecycleState.STOPPED:
                    raise RuntimeError("Member business runtime is still stopping")

            try:
                if self.profile_worker is not None:
                    self._worker_cleanup_pending = True
                    self.profile_worker.start()
                self.service.recover_due()
                self._stop.clear()
                self._recovery_thread = threading.Thread(
                    target=self._recover_forever,
                    name="member-business-recovery",
                    daemon=True,
                )
                self._recovery_thread.start()
                self._set_lifecycle_state(_LifecycleState.RUNNING)
            except Exception as startup_error:
                cleanup_error = self._cleanup_children()
                if cleanup_error is not None:
                    raise cleanup_error from startup_error
                raise

    def stop(self) -> None:
        """Stop recovery before stopping the optional profile worker."""
        with self._lifecycle_lock:
            if self._lifecycle_state is _LifecycleState.STOPPED:
                return
            first_error = self._cleanup_children()
            if first_error is not None:
                raise first_error

    def _recover_forever(self) -> None:
        while not self._stop.wait(self.recovery_interval_seconds):
            try:
                self.service.recover_due()
            except Exception:
                logger.exception("Member entry recovery iteration failed")
            try:
                reconcile_captures = getattr(self.service, "reconcile_captures", None)
                if callable(reconcile_captures):
                    reconcile_captures()
            except Exception:
                logger.exception("Member entry capture reconciliation failed")
            try:
                self.snapshot_store.cleanup_expired(self.snapshot_ttl_seconds)
            except Exception:
                logger.exception("Profile snapshot cleanup iteration failed")


def create_member_business_runtime(
    session_factory: Any,
    profile_mode: str,
    output_dir: str,
    profile_job_poll_interval: float,
    profile_job_max_attempts: int,
    recovery_interval_seconds: float,
    snapshot_ttl_seconds: float,
    ollama_vision_timeout_seconds: Optional[float] = None,
    ollama_vision_max_retries: Optional[int] = None,
) -> MemberBusinessRuntime:
    """Build durable member handling for mock, Ollama, or disabled profiles."""
    normalized_mode = profile_mode.lower()
    if normalized_mode not in {"mock", "ollama", "disabled"}:
        raise ValueError("profile_mode must be 'mock', 'ollama', or 'disabled'")

    snapshot_store = ProfileSnapshotStore(output_dir)
    service = MemberEntryService(
        session_factory,
        snapshot_resolver=snapshot_store,
        profile_enabled=normalized_mode != "disabled",
        profile_job_max_attempts=profile_job_max_attempts,
    )
    customer_entry_visit = CustomerEntryVisit(session_factory)
    profile_worker = None
    if normalized_mode != "disabled":
        pipeline = create_customer_profile_pipeline(
            use_mock_llm=normalized_mode == "mock",
            output_dir=output_dir,
            vision_timeout_seconds=ollama_vision_timeout_seconds,
            vision_max_retries=ollama_vision_max_retries,
        )
        profile_worker = ProfileJobWorker(
            session_factory,
            pipeline,
            poll_interval_seconds=profile_job_poll_interval,
        )

    return MemberBusinessRuntime(
        snapshot_store=snapshot_store,
        service=service,
        profile_worker=profile_worker,
        recovery_interval_seconds=recovery_interval_seconds,
        snapshot_ttl_seconds=snapshot_ttl_seconds,
        profile_job_max_attempts=profile_job_max_attempts,
        customer_entry_visit=customer_entry_visit,
    )
