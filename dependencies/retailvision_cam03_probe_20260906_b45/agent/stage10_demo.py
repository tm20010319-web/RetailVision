"""Realtime Stage 10 demo wiring for the frozen Person/Product contracts.

This module owns orchestration only.  Person and Product model adapters are
injected at the frame boundary, while association and interaction always use
the existing frozen runtimes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from agent.person_product_association import (
    PersonAssociationObservation,
    PersonProductAssociationConfig,
    PersonProductAssociationResult,
    PersonProductAssociationRuntime,
    PersonProductAssociationStatus,
    ProductAssociationObservation,
)
from agent.product_interaction import (
    ProductInteractionConfig,
    ProductInteractionEvent,
    ProductInteractionObservation,
    ProductInteractionResult,
    ProductInteractionRuntime,
)
from agent.sam31_behavior_adapter import Sam31BehaviorAdapter
from agent.sam31_live_behavior import Sam31GlobalTrack
from agent.sam31_product_tracking import (
    evaluate_candidate,
    initialization_candidates,
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplaySurface,
    ProductDisplayState,
    ProductTrackStatus,
    ProductWindowResult,
)
from agent.stage10_presentation import (
    PersonDisplayIdentity,
    chinese_status,
    event_display_message,
    product_presentation,
    resolve_person_display_identity,
)


Point = tuple[float, float]
BBox = tuple[float, float, float, float]
PersonDetector = Callable[["RealtimeFrameContext"], Iterable[Sam31GlobalTrack]]
PersonIdentityResolver = Callable[
    [PersonAssociationObservation],
    Optional[Mapping[str, object]],
]


LOGGER = logging.getLogger(__name__)


class FatalStage10Error(RuntimeError):
    """A configuration or provider startup failure that cannot fail open."""


class ProviderContractError(FatalStage10Error):
    pass


class ProviderInitializationError(FatalStage10Error):
    pass


class ProductInitializationPending(RuntimeError):
    """The current rolling window is not ready for Product A/B assignment."""


class ProductInitializationTimeout(FatalStage10Error):
    """Product preflight exhausted its bounded wait budget."""


@dataclass(frozen=True)
class TransportPreflightResult:
    opened: bool
    decoded_frames: int
    source_resolution: tuple[int, int]
    ai_resolution: tuple[int, int]
    frame_shape: tuple[int, ...]
    black_frames: int
    duplicate_frames: int
    average_pixel_mean: float
    average_pixel_std: float
    first_frame_timestamp: Optional[str]
    last_frame_timestamp: Optional[str]
    result: str

    def to_dict(self) -> dict[str, object]:
        return {
            "rtsp_source": "REDACTED",
            "opened": self.opened,
            "decoded_frames": self.decoded_frames,
            "source_resolution": list(self.source_resolution),
            "ai_resolution": list(self.ai_resolution),
            "frame_shape": list(self.frame_shape),
            "black_frames": self.black_frames,
            "duplicate_frames": self.duplicate_frames,
            "average_pixel_mean": self.average_pixel_mean,
            "average_pixel_std": self.average_pixel_std,
            "first_frame_timestamp": self.first_frame_timestamp,
            "last_frame_timestamp": self.last_frame_timestamp,
            "result": self.result,
        }


@dataclass(frozen=True)
class ProductWindowPreflightResult:
    window_no: int
    frame_count: int
    source_seq_start: Optional[int]
    source_seq_end: Optional[int]
    ai_frame_shape: tuple[int, ...]
    contact_sheet_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "window_no": self.window_no,
            "frame_count": self.frame_count,
            "source_seq_start": self.source_seq_start,
            "source_seq_end": self.source_seq_end,
            "ai_frame_shape": list(self.ai_frame_shape),
            "contact_sheet_path": self.contact_sheet_path,
        }


class SyncStatus(str, Enum):
    EXACT = "EXACT"
    TEMPORAL_MATCH = "TEMPORAL_MATCH"
    MISSING_PERSON = "MISSING_PERSON"
    MISSING_PRODUCT = "MISSING_PRODUCT"
    DROPPED = "DROPPED"


@dataclass(frozen=True)
class RealtimeFrameContext:
    """The one frame identity shared by all Stage 10 consumers."""

    camera_id: str
    source_seq: int
    captured_at: datetime
    frame_width: int
    frame_height: int
    frame: Optional[np.ndarray] = None
    processed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.camera_id.strip():
            raise ValueError("camera_id is required")
        if isinstance(self.source_seq, bool) or not isinstance(self.source_seq, int):
            raise ValueError("source_seq must be an integer")
        if self.source_seq < 0:
            raise ValueError("source_seq must be non-negative")
        if self.frame_width < 1 or self.frame_height < 1:
            raise ValueError("frame dimensions must be positive")
        if not isinstance(self.captured_at, datetime):
            raise ValueError("captured_at must be datetime")
        if self.processed_at is not None and not isinstance(self.processed_at, datetime):
            raise ValueError("processed_at must be datetime when provided")
        if self.frame is not None:
            if not isinstance(self.frame, np.ndarray) or self.frame.ndim not in {2, 3}:
                raise ValueError("frame must be a 2D or 3D numpy array")

    @property
    def timestamp(self) -> datetime:
        return _utc_naive(self.captured_at)


@dataclass(frozen=True)
class SyncDiagnostics:
    person_source_seq: Optional[int]
    product_source_seq: Optional[int]
    person_timestamp: Optional[datetime]
    product_timestamp: Optional[datetime]
    sync_delta_ms: Optional[float]
    status: SyncStatus

    def to_dict(self) -> dict[str, object]:
        return {
            "person_source_seq": self.person_source_seq,
            "product_source_seq": self.product_source_seq,
            "person_timestamp": _iso(self.person_timestamp),
            "product_timestamp": _iso(self.product_timestamp),
            "sync_delta_ms": self.sync_delta_ms,
            "sync_status": self.status.value,
        }


@dataclass(frozen=True)
class SynchronizedPersons:
    persons: tuple[PersonAssociationObservation, ...]
    diagnostics: SyncDiagnostics


@dataclass(frozen=True)
class Stage10ProductObservation:
    """Product association input plus Stage 7 surface evidence."""

    observation: ProductAssociationObservation
    surface_overlap_ratio: Optional[float]
    recovered_from_missing_pending: bool = False
    # Diagnostic-only evidence carried from Stage 7C. It is intentionally not
    # part of ProductAssociationObservation or the Stage 8/9 contracts.
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    mask: Optional[np.ndarray] = None


@dataclass(frozen=True)
class ProviderFrameObservations:
    """Delayed provider output mapped to one original realtime frame."""

    context: RealtimeFrameContext
    person_tracks: tuple[Sam31GlobalTrack, ...]
    products: tuple[ProductAssociationObservation | Stage10ProductObservation, ...]
    person_ready_at: datetime
    product_ready_at: datetime
    person_inference_ms: float
    product_inference_ms: float
    provider_started_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.person_inference_ms < 0 or self.product_inference_ms < 0:
            raise ValueError("provider inference timings must be non-negative")


@dataclass(frozen=True)
class RuntimeErrorDiagnostic:
    source_seq: Optional[int]
    timestamp: Optional[datetime]
    exception_type: str
    exception_message: str
    stage: str
    provider: Optional[str]
    traceback_text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_seq": self.source_seq,
            "timestamp": _iso(self.timestamp),
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "stage": self.stage,
            "provider": self.provider,
            "traceback": self.traceback_text,
        }


def synchronize_persons(
    *,
    product: ProductAssociationObservation,
    persons: Sequence[PersonAssociationObservation],
    max_delta_seconds: float,
) -> SynchronizedPersons:
    """Select exact source-seq people, then a bounded timestamp fallback."""

    if max_delta_seconds <= 0:
        raise ValueError("max_delta_seconds must be positive")
    candidates = tuple(person for person in persons if person.camera_id == product.camera_id)
    exact = tuple(person for person in candidates if person.source_seq == product.source_seq)
    if exact:
        delta = max(
            abs((_utc_naive(person.observed_at) - _utc_naive(product.observed_at)).total_seconds())
            for person in exact
        )
        return SynchronizedPersons(
            exact,
            SyncDiagnostics(
                person_source_seq=exact[0].source_seq,
                product_source_seq=product.source_seq,
                person_timestamp=exact[0].observed_at,
                product_timestamp=product.observed_at,
                sync_delta_ms=delta * 1000.0,
                status=SyncStatus.EXACT,
            ),
        )
    if not candidates:
        return SynchronizedPersons(
            (),
            SyncDiagnostics(
                person_source_seq=None,
                product_source_seq=product.source_seq,
                person_timestamp=None,
                product_timestamp=product.observed_at,
                sync_delta_ms=None,
                status=SyncStatus.MISSING_PERSON,
            ),
        )
    nearest = min(
        candidates,
        key=lambda person: abs(
            (_utc_naive(person.observed_at) - _utc_naive(product.observed_at)).total_seconds()
        ),
    )
    delta = abs(
        (_utc_naive(nearest.observed_at) - _utc_naive(product.observed_at)).total_seconds()
    )
    if delta > max_delta_seconds:
        return SynchronizedPersons(
            (),
            SyncDiagnostics(
                person_source_seq=nearest.source_seq,
                product_source_seq=product.source_seq,
                person_timestamp=nearest.observed_at,
                product_timestamp=product.observed_at,
                sync_delta_ms=delta * 1000.0,
                status=SyncStatus.MISSING_PERSON,
            ),
        )
    matched_time = _utc_naive(nearest.observed_at)
    matched = tuple(
        person for person in candidates if _utc_naive(person.observed_at) == matched_time
    )
    return SynchronizedPersons(
        matched,
        SyncDiagnostics(
            person_source_seq=nearest.source_seq,
            product_source_seq=product.source_seq,
            person_timestamp=nearest.observed_at,
            product_timestamp=product.observed_at,
            sync_delta_ms=delta * 1000.0,
            status=SyncStatus.TEMPORAL_MATCH,
        ),
    )


class ProductFrameSource(Protocol):
    def process_frame(
        self, context: RealtimeFrameContext
    ) -> Sequence[ProductAssociationObservation | Stage10ProductObservation]: ...


class CandidateDetector(Protocol):
    def __call__(self, context: RealtimeFrameContext) -> Iterable[ProductCandidate]: ...


class WindowedProviderBundle(Protocol):
    def start(self) -> None: ...

    def process_window(
        self, contexts: Sequence[RealtimeFrameContext]
    ) -> Sequence[ProviderFrameObservations]: ...

    def gpu_metrics(self) -> Mapping[str, object]: ...

    def close(self) -> None: ...


class MultiProductTrackFrameSource:
    """Adapt the frozen Product Track runtime to the Stage 10 frame seam."""

    def __init__(
        self,
        *,
        tracker: MultiProductTrackRuntime,
        detector: CandidateDetector,
    ) -> None:
        self.tracker = tracker
        self.detector = detector
        self._window_no = 0
        self._initialized = False

    def initialize(
        self,
        *,
        assignments: Mapping[str, ProductCandidate],
        context: RealtimeFrameContext,
    ) -> tuple[Stage10ProductObservation, ...]:
        results = self.tracker.initialize_assignments(
            assignments=assignments,
            observed_at=context.timestamp,
            source_seq=context.source_seq,
            window_no=self._window_no,
            grounding_candidate_count=len(assignments),
        )
        self._initialized = True
        self._window_no += 1
        return tuple(
            Stage10ProductObservation(
                observation=_product_association_observation(
                    result,
                    context.camera_id,
                    context.frame_width,
                    context.frame_height,
                ),
                surface_overlap_ratio=result.surface_overlap_ratio,
                recovered_from_missing_pending=result.recovered_from_missing_pending,
            )
            for result in results
        )

    def process_frame(self, context: RealtimeFrameContext) -> Sequence[ProductAssociationObservation | Stage10ProductObservation]:
        if not self._initialized:
            raise RuntimeError("Product Track source must be initialized before processing")
        candidates = tuple(self.detector(context))
        results = self.tracker.process_window(
            candidates=candidates,
            observed_at=context.timestamp,
            source_seq=context.source_seq,
            window_no=self._window_no,
            grounding_candidate_count=len(candidates),
        )
        self._window_no += 1
        return tuple(
            Stage10ProductObservation(
                observation=_product_association_observation(
                    result,
                    context.camera_id,
                    context.frame_width,
                    context.frame_height,
                ),
                surface_overlap_ratio=result.surface_overlap_ratio,
                recovered_from_missing_pending=result.recovered_from_missing_pending,
            )
            for result in results
        )


@dataclass(frozen=True)
class Stage10DemoEvent:
    """Unified stream DTO mapped directly from a Stage 9 event."""

    event_id: str
    event_type: str
    timestamp: datetime
    source_seq: int
    camera_id: str
    person: Mapping[str, object]
    product: Mapping[str, object]
    interaction: Mapping[str, object]
    evidence: Mapping[str, object]
    display: Mapping[str, object]

    @classmethod
    def from_event(
        cls,
        event: ProductInteractionEvent,
        *,
        person_identity: Optional[PersonDisplayIdentity] = None,
    ) -> "Stage10DemoEvent":
        identity = person_identity or resolve_person_display_identity(
            int(event.person_track_id or 0)
        )
        product = product_presentation(event.product_key)
        return cls(
            event_id=event.event_id,
            event_type=event.event_type.value,
            timestamp=_utc_naive(event.observed_at),
            source_seq=event.source_seq,
            camera_id=event.camera_id,
            person={
                "track_id": event.person_track_id,
                "track_token": event.person_track_token,
                **identity.to_dict(),
            },
            product={
                "product_key": event.product_key,
                "product_type": event.product_key.rsplit("_", 1)[0]
                if "_" in event.product_key
                else event.product_key,
                "track_id": event.product_track_id,
                "track_token": event.product_track_token,
                "english_name": product.english_name,
                "chinese_name": product.chinese_name,
                "display_name": product.display_name,
            },
            interaction={
                "interaction_session_id": event.interaction_session_id,
                "holding_duration_seconds": event.holding_duration_seconds,
                "confidence": event.confidence,
                "event_sequence": event.event_sequence,
            },
            evidence=dict(event.evidence),
            display={
                "event_name": chinese_status(event.event_type),
                "message": event_display_message(
                    event.event_type,
                    person_name=identity.display_name,
                    product_name=product.chinese_name,
                    holding_duration_seconds=event.holding_duration_seconds,
                ),
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": _utc_naive(self.timestamp).isoformat(),
            "source_seq": self.source_seq,
            "camera_id": self.camera_id,
            "person": dict(self.person),
            "product": dict(self.product),
            "interaction": dict(self.interaction),
            "evidence": dict(self.evidence),
            "display": dict(self.display),
        }


class EventSink(Protocol):
    def emit(self, event: Stage10DemoEvent) -> None: ...


class JsonlEventSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._source = self.path.open("a", encoding="utf-8")

    def emit(self, event: Stage10DemoEvent) -> None:
        self._source.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._source.flush()

    def close(self) -> None:
        self._source.close()


class JsonlMetricsWriter:
    """Small buffered JSONL writer for non-critical runtime diagnostics."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._source = self.path.open("a", encoding="utf-8")
        self._closed = False

    def write(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise RuntimeError("metrics writer is closed")
        self._source.write(json.dumps(dict(payload), ensure_ascii=True, default=str) + "\n")

    def flush(self) -> None:
        if self._closed:
            return
        self._source.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._source.flush()
        self._source.close()
        self._closed = True

    def __enter__(self) -> "JsonlMetricsWriter":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


class SustainedMetricsWriter:
    """Buffered ten-second health samples for a sustained live run."""

    def __init__(self, path: str | Path) -> None:
        self._writer = JsonlMetricsWriter(path)

    def record(self, payload: Mapping[str, object]) -> None:
        self._writer.write(payload)

    def close(self) -> None:
        self._writer.close()


def aggregate_acceptance_summary(
    scenarios: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Aggregate A-F scenario rows without weakening any hard gate."""

    hard_gates = (
        "false_pick_up",
        "false_put_down",
        "duplicate_pick_up",
        "duplicate_put_down",
        "wrong_actor",
        "unexpected_person_switch",
        "product_id_swap",
        "product_token_change",
        "unexpected_lost_false_event",
        "runtime_errors",
    )
    rows = [dict(row) for row in scenarios]
    totals = {
        gate: sum(
            int(row.get(gate, 0) or 0)
            for row in rows
        )
        for gate in hard_gates
    }
    all_pass = bool(rows) and all(
        str(row.get("status", "FAIL")).upper() == "PASS" for row in rows
    ) and all(value == 0 for value in totals.values())
    return {
        "scenario_count": len(rows),
        "scenario_results": rows,
        "hard_gate_totals": totals,
        "all_pass": all_pass,
    }


class Stage10PerformanceRecorder:
    """Collect window/provider/frame timing at the Stage 10 orchestration seam."""

    _WINDOW_FIELDS = (
        "window_wait_ms",
        "oldest_frame_age_ms",
        "newest_frame_age_ms",
    )
    _FRAME_FIELDS = (
        "capture_to_provider_start_ms",
        "capture_to_person_ready_ms",
        "capture_to_product_ready_ms",
        "capture_to_association_ready_ms",
        "capture_to_interaction_ready_ms",
        "capture_to_event_ms",
        "capture_to_overlay_ms",
        "frame_result_write_ms",
        "keyframe_write_ms",
        "pipeline_lag_ms",
    )

    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.output_dir = None if output_dir is None else Path(output_dir)
        self._writers: dict[str, JsonlMetricsWriter] = {}
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "window_timing",
                "frame_latency",
                "queue_metrics",
                "provider_metrics",
            ):
                self._writers[name] = JsonlMetricsWriter(self.output_dir / f"{name}.jsonl")
        self._windows: list[dict[str, object]] = []
        self._frames: list[dict[str, object]] = []
        self._providers: list[dict[str, object]] = []
        self._queues: list[dict[str, object]] = []
        self._closed = False
        self._summary: Optional[dict[str, object]] = None

    def _write(self, stream: str, payload: Mapping[str, object]) -> None:
        writer = self._writers.get(stream)
        if writer is not None:
            writer.write(payload)

    def record_window(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise RuntimeError("performance recorder is closed")
        item = dict(payload)
        self._windows.append(item)
        self._queues.append(item)
        self._write("window_timing", item)
        self._write("queue_metrics", item)

    def record_provider(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise RuntimeError("performance recorder is closed")
        item = dict(payload)
        self._providers.append(item)
        self._write("provider_metrics", item)

    def record_frame(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise RuntimeError("performance recorder is closed")
        item = dict(payload)
        self._frames.append(item)
        self._write("frame_latency", item)

    @staticmethod
    def _stats(records: Sequence[Mapping[str, object]], field: str) -> dict[str, float | int]:
        values: list[float] = []
        for record in records:
            value = record.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(max(0.0, float(value)))
        return _latency_stats(values)

    def _build_segment(
        self,
        windows: Sequence[Mapping[str, object]],
        frames: Sequence[Mapping[str, object]],
        providers: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        summary: dict[str, object] = {
            "windows": len(windows),
            "frames": len(frames),
            "provider_records": len(providers),
            "window_wait_ms": self._stats(windows, "window_wait_ms"),
            "oldest_frame_age_ms": self._stats(windows, "oldest_frame_age_ms"),
            "newest_frame_age_ms": self._stats(windows, "newest_frame_age_ms"),
        }
        for field in self._FRAME_FIELDS:
            summary[field] = self._stats(frames, field)
        provider_fields = (
            "person_inference_ms",
            "person_session_start_ms",
            "person_grounding_ms",
            "person_regrounding_ms",
            "person_global_association_ms",
            "person_propagation_ms",
            "person_output_conversion_ms",
            "person_session_close_ms",
            "person_other_ms",
            "person_unattributed_ms",
            "product_inference_ms",
            "product_session_start_ms",
            "product_grounding_ms",
            "product_propagation_ms",
            "product_output_conversion_ms",
            "product_candidate_evaluation_ms",
            "product_track_association_ms",
            "product_candidate_association_ms",
            "product_artifact_conversion_ms",
            "product_session_close_ms",
            "product_other_ms",
            "product_unattributed_ms",
            "product_materialize_ms",
            "person_input_oldest_age_ms",
            "person_input_newest_age_ms",
            "product_input_oldest_age_ms",
            "product_input_newest_age_ms",
            "association_ms",
            "interaction_ms",
            "overlay_ms",
            "event_emit_ms",
            "frame_result_write_ms",
            "keyframe_write_ms",
            "serialized_provider_ms",
            "provider_wall_ms",
        )
        summary["provider"] = {
            field: self._stats(providers, field) for field in provider_fields
        }
        summary["queue"] = {
            field: self._stats(windows, field)
            for field in ("oldest_frame_age_ms", "newest_frame_age_ms")
        }
        return summary

    def _build_summary(self) -> dict[str, object]:
        summary = self._build_segment(self._windows, self._frames, self._providers)
        ready_windows = [row for row in self._windows if row.get("runtime_state") == "READY"]
        ready_frames = [row for row in self._frames if row.get("runtime_state") == "READY"]
        ready_providers = [row for row in self._providers if row.get("runtime_state") == "READY"]
        summary["all_run_metrics"] = self._build_segment(
            self._windows,
            self._frames,
            self._providers,
        )
        summary["ready_state_metrics"] = self._build_segment(
            ready_windows,
            ready_frames,
            ready_providers,
        )
        return summary

    def close(self) -> dict[str, object]:
        if self._summary is not None:
            return dict(self._summary)
        self._summary = self._build_summary()
        for writer in self._writers.values():
            writer.close()
        if self.output_dir is not None:
            (self.output_dir / "performance_summary.json").write_text(
                json.dumps(self._summary, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            before_after = self.output_dir / "performance_before_after.md"
            if not before_after.exists():
                before_after.write_text(
                    "# Stage 10.2 Performance Before/After\n\n"
                    "Before and after live measurements are filled by the acceptance run.\n",
                    encoding="utf-8",
                )
        self._closed = True
        return dict(self._summary)


class ConsoleEventSink:
    def __init__(self, write: Callable[[str], object] = print) -> None:
        self.write = write

    def emit(self, event: Stage10DemoEvent) -> None:
        self.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))


class EventStream:
    def __init__(self, sinks: Iterable[EventSink] = ()) -> None:
        self.sinks = tuple(sinks)
        self.events: list[Stage10DemoEvent] = []

    def emit(self, event: Stage10DemoEvent) -> None:
        if any(item.event_id == event.event_id for item in self.events):
            return
        self.events.append(event)
        for sink in self.sinks:
            sink.emit(event)


@dataclass
class RuntimeCounters:
    frames_received: int = 0
    frames_sampled: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    read_failures: int = 0
    decode_failures: int = 0
    reconnects: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "frames_received": self.frames_received,
            "frames_sampled": self.frames_sampled,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "read_failures": self.read_failures,
            "decode_failures": self.decode_failures,
            "reconnects": self.reconnects,
        }


@dataclass(frozen=True)
class Stage10FrameResult:
    context: RealtimeFrameContext
    persons: tuple[PersonAssociationObservation, ...]
    products: tuple[ProductAssociationObservation, ...]
    associations: tuple[PersonProductAssociationResult, ...]
    interactions: tuple[ProductInteractionResult, ...]
    events: tuple[Stage10DemoEvent, ...]
    sync: tuple[SyncDiagnostics, ...]
    timings_ms: Mapping[str, float]
    stage_timestamps: Mapping[str, Optional[datetime]] = field(default_factory=dict)
    person_display_identities: Mapping[str, PersonDisplayIdentity] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "camera_id": self.context.camera_id,
            "source_seq": self.context.source_seq,
            "timestamp": self.context.timestamp.isoformat(),
            "processed_at": _iso(self.context.processed_at),
            "persons": [
                _person_dict(
                    item,
                    self.person_display_identities.get(item.track_token),
                )
                for item in self.persons
            ],
            "products": [_product_dict(item) for item in self.products],
            "associations": [item.to_dict() for item in self.associations],
            "interactions": [item.to_dict() for item in self.interactions],
            "events": [item.to_dict() for item in self.events],
            "sync": [item.to_dict() for item in self.sync],
            "timings_ms": dict(self.timings_ms),
            "stage_timestamps": {
                key: _iso(value)
                for key, value in self.stage_timestamps.items()
            },
        }


class Stage10DemoRuntime:
    """Run one shared sampled frame through Stages 8 and 9."""

    def __init__(
        self,
        *,
        camera_id: str,
        person_adapter: Sam31BehaviorAdapter,
        person_detector: PersonDetector,
        product_source: ProductFrameSource,
        association_config: PersonProductAssociationConfig,
        interaction_config: ProductInteractionConfig,
        event_stream: Optional[EventStream] = None,
        person_identity_resolver: Optional[PersonIdentityResolver] = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if camera_id != person_adapter.camera_id:
            raise ValueError("camera_id must match the Person adapter")
        if association_config.frame_width != interaction_config.frame_width:
            raise ValueError("Person/Product/Interaction frame widths must match")
        if association_config.frame_height != interaction_config.frame_height:
            raise ValueError("Person/Product/Interaction frame heights must match")
        self.camera_id = camera_id
        self.person_adapter = person_adapter
        self.person_detector = person_detector
        self.product_source = product_source
        self.association = PersonProductAssociationRuntime(association_config)
        self.interaction = ProductInteractionRuntime(interaction_config)
        self.event_stream = event_stream or EventStream()
        self.person_identity_resolver = person_identity_resolver
        self.clock = clock
        self.counters = RuntimeCounters()
        self._last_source_seq: Optional[int] = None
        self._latest: Optional[Stage10FrameResult] = None
        self._latest_events: deque[Stage10DemoEvent] = deque(maxlen=20)
        self._last_processed_perf: Optional[float] = None
        self._processing_fps = 0.0

    def process_frame(self, context: RealtimeFrameContext) -> Stage10FrameResult:
        if context.camera_id != self.camera_id:
            raise ValueError("frame camera_id does not match runtime")
        if self._last_source_seq is not None and context.source_seq <= self._last_source_seq:
            raise ValueError("source_seq must be strictly increasing")
        started = self.clock()
        person_started = self.clock()
        tracks = tuple(_normalize_person_track(item) for item in self.person_detector(context))
        person_ready = self.clock()
        person_ready_at = datetime.now(timezone.utc)

        product_started = self.clock()
        product_inputs = tuple(self.product_source.process_frame(context))
        product_ready = self.clock()
        product_ready_at = datetime.now(timezone.utc)
        return self.process_observations(
            context=context,
            person_tracks=tracks,
            product_inputs=product_inputs,
            started=started,
            person_ready_at=person_ready_at,
            product_ready_at=product_ready_at,
            person_inference_ms=max(0.0, (person_ready - person_started) * 1000.0),
            product_inference_ms=max(0.0, (product_ready - product_started) * 1000.0),
        )

    def process_observations(
        self,
        *,
        context: RealtimeFrameContext,
        person_tracks: Iterable[Sam31GlobalTrack],
        product_inputs: Sequence[
            ProductAssociationObservation | Stage10ProductObservation
        ],
        started: Optional[float] = None,
        person_ready_at: Optional[datetime] = None,
        product_ready_at: Optional[datetime] = None,
        provider_started_at: Optional[datetime] = None,
        person_inference_ms: float = 0.0,
        product_inference_ms: float = 0.0,
    ) -> Stage10FrameResult:
        if context.camera_id != self.camera_id:
            raise ValueError("frame camera_id does not match runtime")
        if self._last_source_seq is not None and context.source_seq <= self._last_source_seq:
            raise ValueError("source_seq must be strictly increasing")
        started = self.clock() if started is None else started
        provider_started_at = provider_started_at or datetime.now(timezone.utc)
        person_ready_at = person_ready_at or datetime.now(timezone.utc)
        product_ready_at = product_ready_at or datetime.now(timezone.utc)
        tracks = tuple(_normalize_person_track(item) for item in person_tracks)
        persons = tuple(
            _person_association_observation(self.person_adapter, context, track)
            for track in tracks
        )
        person_display_identities = {
            person.track_token: self._resolve_person_display_identity(person)
            for person in persons
        }
        product_inputs = tuple(product_inputs)
        products = tuple(_unwrap_product(item) for item in product_inputs)
        surface_overlap = {
            item.observation.product_key: item.surface_overlap_ratio
            for item in product_inputs
            if isinstance(item, Stage10ProductObservation)
        }

        associations: list[PersonProductAssociationResult] = []
        interactions: list[ProductInteractionResult] = []
        sync_diagnostics: list[SyncDiagnostics] = []
        events: list[Stage10DemoEvent] = []
        event_emitted_at: Optional[datetime] = None
        event_emit_ms = 0.0
        if not products:
            sync_diagnostics.append(
                SyncDiagnostics(
                    person_source_seq=persons[0].source_seq if persons else None,
                    product_source_seq=None,
                    person_timestamp=persons[0].observed_at if persons else None,
                    product_timestamp=None,
                    sync_delta_ms=None,
                    status=SyncStatus.MISSING_PRODUCT,
                )
            )
        association_started = self.clock()
        association_pairs: list[tuple[ProductAssociationObservation, PersonProductAssociationResult]] = []
        for product in products:
            synced = synchronize_persons(
                product=product,
                persons=persons,
                max_delta_seconds=self.association.config.max_sync_delta_seconds,
            )
            sync_diagnostics.append(synced.diagnostics)
            result = self.association.process(product=product, persons=synced.persons)
            associations.append(result)
            association_pairs.append((product, result))
        association_ready = self.clock()
        association_ready_at = datetime.now(timezone.utc)
        interaction_started = self.clock()
        for product, result in association_pairs:
            interaction_observation = _interaction_observation(
                product,
                result,
                surface_overlap_ratio=surface_overlap.get(product.product_key),
            )
            interaction_result = self.interaction.process(interaction_observation)
            interactions.append(interaction_result)
            for emitted in interaction_result.events:
                unified = Stage10DemoEvent.from_event(
                    emitted,
                    person_identity=person_display_identities.get(
                        str(emitted.person_track_token or "")
                    ),
                )
                emit_started = self.clock()
                self.event_stream.emit(unified)
                event_emit_ms += max(0.0, (self.clock() - emit_started) * 1000.0)
                if event_emitted_at is None:
                    event_emitted_at = datetime.now(timezone.utc)
                self._latest_events.append(unified)
                events.append(unified)
        interaction_ready = self.clock()
        interaction_ready_at = datetime.now(timezone.utc)
        self.counters.frames_processed += 1
        self._last_source_seq = context.source_seq
        finished = self.clock()
        if self._last_processed_perf is not None:
            processing_interval = finished - self._last_processed_perf
            if processing_interval > 0:
                self._processing_fps = 1.0 / processing_interval
        self._last_processed_perf = finished
        timings = {
            "person_ms": max(0.0, float(person_inference_ms)),
            "product_ms": max(0.0, float(product_inference_ms)),
            "association_ms": max(0.0, (association_ready - association_started) * 1000.0),
            "interaction_ms": max(0.0, (interaction_ready - interaction_started) * 1000.0),
            "event_emit_ms": event_emit_ms,
            "processing_ms": max(0.0, (finished - started) * 1000.0),
        }
        if events:
            assert event_emitted_at is not None
            timings["event_latency_ms"] = max(
                0.0,
                (_utc_naive(event_emitted_at) - _utc_naive(context.timestamp)).total_seconds()
                * 1000.0,
            )
        now = datetime.now(timezone.utc)
        timings["end_to_end_ms"] = max(
            0.0,
            (_utc_naive(now) - _utc_naive(context.timestamp)).total_seconds() * 1000.0,
        )
        timings["pipeline_lag_ms"] = timings["end_to_end_ms"]
        result = Stage10FrameResult(
            context=context,
            persons=persons,
            products=products,
            associations=tuple(associations),
            interactions=tuple(interactions),
            events=tuple(events),
            sync=tuple(sync_diagnostics),
            timings_ms=timings,
            stage_timestamps={
                "capture_at": context.timestamp,
                "provider_start_at": provider_started_at,
                "person_ready_at": person_ready_at,
                "product_ready_at": product_ready_at,
                "association_ready_at": association_ready_at,
                "interaction_ready_at": interaction_ready_at,
                "event_emitted_at": event_emitted_at,
            },
            person_display_identities=person_display_identities,
        )
        self._latest = result
        return result

    def state(self) -> dict[str, object]:
        latest = self._latest
        if latest is None:
            return {
                "camera_id": self.camera_id,
                "runtime": {"connected": False, "fps": 0.0, "latency_ms": 0.0},
                "persons": [],
                "products": [],
                "associations": [],
                "interactions": [],
                "latest_events": [event.to_dict() for event in self._latest_events],
                "counters": self.counters.to_dict(),
            }
        return {
            "camera_id": self.camera_id,
            "source_seq": latest.context.source_seq,
            "runtime": {
                "connected": True,
                "fps": self._processing_fps,
                "latency_ms": latest.timings_ms["end_to_end_ms"],
            },
            "persons": [
                _person_dict(
                    item,
                    latest.person_display_identities.get(item.track_token),
                )
                for item in latest.persons
            ],
            "products": [_product_dict(item) for item in latest.products],
            "associations": [item.to_dict() for item in latest.associations],
            "interactions": [item.to_dict() for item in latest.interactions],
            "latest_events": [event.to_dict() for event in self._latest_events],
            "sync": [item.to_dict() for item in latest.sync],
            "counters": self.counters.to_dict(),
        }

    def _resolve_person_display_identity(
        self,
        person: PersonAssociationObservation,
    ) -> PersonDisplayIdentity:
        enrichment: Optional[Mapping[str, object]] = None
        if self.person_identity_resolver is not None:
            try:
                enrichment = self.person_identity_resolver(person)
            except Exception:
                LOGGER.warning(
                    "Person display identity resolver failed for track_token=%s",
                    person.track_token,
                    exc_info=True,
                )
        return resolve_person_display_identity(person.track_id, enrichment)


class LatestFrameBuffer:
    """Bounded latest-frame queue; replacing an old frame is observable."""

    def __init__(self, *, maxsize: int = 1, counters: Optional[RuntimeCounters] = None) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self.maxsize = maxsize
        self._frames: deque[RealtimeFrameContext] = deque(maxlen=maxsize)
        self.counters = counters or RuntimeCounters()

    def put(self, frame: RealtimeFrameContext) -> None:
        self.counters.frames_received += 1
        if len(self._frames) == self.maxsize:
            self._frames.popleft()
            self.counters.frames_dropped += 1
        self._frames.append(frame)
        self.counters.frames_sampled += 1

    def get(self) -> Optional[RealtimeFrameContext]:
        if not self._frames:
            return None
        return self._frames.popleft()

    def __len__(self) -> int:
        return len(self._frames)


@dataclass(frozen=True)
class RealtimeWindow:
    window_no: int
    contexts: tuple[RealtimeFrameContext, ...]
    fresh_contexts: tuple[RealtimeFrameContext, ...]
    dropped_frames: int
    window_wait_ms: float = 0.0
    queue_depth_before: int = 0
    queue_depth_after: int = 0
    dropped_before_window: int = 0
    dropped_during_window: int = 0
    oldest_frame_age_ms: float = 0.0
    newest_frame_age_ms: float = 0.0
    fresh_frame_count: int = 0


class AsyncRtspWindowSource:
    """Decode/sample continuously while inference consumes bounded windows."""

    def __init__(
        self,
        reader: "RtspFrameReader",
        *,
        max_queue_size: int = 64,
    ) -> None:
        if max_queue_size < 12:
            raise ValueError("max_queue_size must hold one complete window")
        self.reader = reader
        self.max_queue_size = max_queue_size
        self._condition = threading.Condition()
        self._contexts: deque[RealtimeFrameContext] = deque()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[RuntimeErrorDiagnostic] = None
        self._window_no = 0

    def start(self) -> "AsyncRtspWindowSource":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="stage10-rtsp-sampler",
            daemon=True,
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                context = self.reader.read_sampled()
            except Exception as error:  # pragma: no cover - hardware-specific
                self._error = _runtime_error(
                    error,
                    source_seq=None,
                    timestamp=None,
                    stage="rtsp_reader",
                    provider="RtspFrameReader",
                )
                LOGGER.exception("Stage 10 RTSP sampler failed")
                break
            if context is None:
                if self.reader.counters.read_failures >= self.reader.reconnect_attempts:
                    break
                continue
            with self._condition:
                if self._stop.is_set():
                    self.reader.counters.frames_dropped += 1
                    break
                if len(self._contexts) >= self.max_queue_size:
                    self._contexts.popleft()
                    self.reader.counters.frames_dropped += 1
                self._contexts.append(context)
                self._condition.notify_all()

    def wait_window(
        self,
        *,
        previous_overlap: Sequence[RealtimeFrameContext] = (),
        timeout_seconds: float = 30.0,
    ) -> Optional[RealtimeWindow]:
        import time as _time

        required = 12 if not previous_overlap else 8
        previous_last = previous_overlap[-1].source_seq if previous_overlap else None
        wait_started = _time.monotonic()
        deadline = wait_started + timeout_seconds
        dropped_before = int(getattr(self.reader.counters, "frames_dropped", 0))
        with self._condition:
            while True:
                available = [
                    item
                    for item in self._contexts
                    if previous_last is None or item.source_seq > previous_last
                ]
                if len(available) >= required:
                    queue_depth_before = len(self._contexts)
                    fresh = tuple(available[-required:])
                    dropped = max(0, len(available) - required)
                    self.reader.counters.frames_dropped += dropped
                    last_seq = fresh[-1].source_seq
                    while self._contexts and self._contexts[0].source_seq <= last_seq:
                        self._contexts.popleft()
                    queue_depth_after = len(self._contexts)
                    now = _utc_naive(datetime.now(timezone.utc))
                    oldest_age = max(
                        0.0,
                        (now - _utc_naive(fresh[0].captured_at)).total_seconds() * 1000.0,
                    )
                    newest_age = max(
                        0.0,
                        (now - _utc_naive(fresh[-1].captured_at)).total_seconds() * 1000.0,
                    )
                    dropped_after = int(getattr(self.reader.counters, "frames_dropped", 0))
                    contexts = tuple(previous_overlap[-4:]) + fresh
                    result = RealtimeWindow(
                        window_no=self._window_no,
                        contexts=contexts,
                        fresh_contexts=fresh,
                        dropped_frames=dropped,
                        window_wait_ms=max(0.0, (_time.monotonic() - wait_started) * 1000.0),
                        queue_depth_before=queue_depth_before,
                        queue_depth_after=queue_depth_after,
                        dropped_before_window=dropped_before,
                        dropped_during_window=max(0, dropped_after - dropped_before),
                        oldest_frame_age_ms=oldest_age,
                        newest_frame_age_ms=newest_age,
                        fresh_frame_count=len(fresh),
                    )
                    self._window_no += 1
                    return result
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=min(0.2, remaining))

    @property
    def error(self) -> Optional[RuntimeErrorDiagnostic]:
        return self._error

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self.reader.counters.frames_dropped += len(self._contexts)
            self._contexts.clear()
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.reader.close()


class RtspFrameReader:
    """Single-source RTSP reader with bounded reconnect attempts."""

    def __init__(
        self,
        *,
        source: str,
        camera_id: str,
        sample_fps: float = 5.0,
        frame_width: int = 1280,
        frame_height: int = 720,
        capture_factory: Callable[..., Any] = cv2.VideoCapture,
        reconnect_attempts: int = 3,
        warmup_seconds: float = 0.0,
    ) -> None:
        if not source:
            raise ValueError("source is required")
        if sample_fps <= 0 or frame_width < 1 or frame_height < 1:
            raise ValueError("invalid RTSP reader configuration")
        if reconnect_attempts < 1:
            raise ValueError("reconnect_attempts must be positive")
        if warmup_seconds < 0:
            raise ValueError("warmup_seconds must be non-negative")
        self.source = source
        self.camera_id = camera_id
        self.sample_fps = sample_fps
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.capture_factory = capture_factory
        self.reconnect_attempts = reconnect_attempts
        self.warmup_seconds = warmup_seconds
        self.counters = RuntimeCounters()
        self._capture: Any = None
        self._source_seq = 0
        self._last_sample_at: Optional[float] = None
        self._warmup_until: Optional[float] = None
        self._source_width = 0
        self._source_height = 0

    def open(self) -> bool:
        self.close()
        try:
            self._capture = self.capture_factory(self.source, cv2.CAP_FFMPEG)
        except TypeError:
            self._capture = self.capture_factory(self.source)
        opened = bool(self._capture is not None and self._capture.isOpened())
        if opened:
            self.counters.reconnects += 1
            self._warmup_until = (
                time.monotonic() + self.warmup_seconds
                if self.warmup_seconds > 0
                else None
            )
        else:
            self.counters.read_failures += 1
        return opened

    def read(self, *, captured_at: Optional[datetime] = None) -> Optional[RealtimeFrameContext]:
        if self._capture is None and not self.open():
            return None
        ok, frame = self._capture.read()
        if not ok or frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            self.counters.read_failures += 1
            self.close()
            return None
        self.counters.frames_received += 1
        self._source_height, self._source_width = int(frame.shape[0]), int(frame.shape[1])
        frame = cv2.resize(frame, (self.frame_width, self.frame_height), interpolation=cv2.INTER_AREA)
        context = RealtimeFrameContext(
            camera_id=self.camera_id,
            source_seq=self._source_seq,
            captured_at=captured_at or datetime.now(timezone.utc),
            frame_width=self.frame_width,
            frame_height=self.frame_height,
            frame=frame,
        )
        self._source_seq += 1
        return context

    def read_with_reconnect(self, *, captured_at: Optional[datetime] = None) -> Optional[RealtimeFrameContext]:
        for attempt in range(self.reconnect_attempts):
            context = self.read(captured_at=captured_at)
            if context is not None:
                return context
            if attempt + 1 < self.reconnect_attempts and self.open():
                continue
        return None

    def read_sampled(self, *, captured_at: Optional[datetime] = None) -> Optional[RealtimeFrameContext]:
        """Read source frames until the next bounded 5 FPS-style sample slot."""

        interval = 1.0 / self.sample_fps
        while True:
            context = self.read_with_reconnect(captured_at=captured_at)
            if context is None:
                return None
            now = time.monotonic()
            if self._warmup_until is not None and now < self._warmup_until:
                self.counters.frames_dropped += 1
                continue
            if self._last_sample_at is None or now - self._last_sample_at >= interval:
                self._last_sample_at = now
                self.counters.frames_sampled += 1
                return context
            self.counters.frames_dropped += 1

    def close(self) -> None:
        if self._capture is not None:
            release = getattr(self._capture, "release", None)
            if callable(release):
                release()
        self._capture = None

    @property
    def source_resolution(self) -> tuple[int, int]:
        return self._source_width, self._source_height


def build_contact_sheet(
    frames: Sequence[np.ndarray],
    path: str | Path,
    *,
    columns: int = 3,
) -> Path:
    """Save a compact visual contact sheet for a preflight review."""

    if not frames:
        raise ValueError("contact sheet requires at least one frame")
    if columns < 1:
        raise ValueError("columns must be positive")
    thumbnails = []
    for frame in frames:
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("contact sheet frames must be non-empty numpy arrays")
        thumbnails.append(cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA))
    rows = []
    for start in range(0, len(thumbnails), columns):
        row = thumbnails[start : start + columns]
        while len(row) < columns:
            row.append(np.zeros_like(thumbnails[0]))
        rows.append(cv2.hconcat(row))
    sheet = cv2.vconcat(rows)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), sheet):
        raise OSError(f"cannot write contact sheet: {output}")
    return output


def run_transport_preflight(
    reader: RtspFrameReader,
    output_dir: str | Path,
    *,
    frame_count: int = 30,
    sample_indices: Sequence[int] = (0, 10, 20),
) -> TransportPreflightResult:
    """Verify the actual RTSP decode before loading a SAM predictor."""

    if frame_count < 1:
        raise ValueError("frame_count must be positive")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    opened = bool(reader.open())
    frames: list[np.ndarray] = []
    contexts: list[RealtimeFrameContext] = []
    black_frames = 0
    duplicate_frames = 0
    means: list[float] = []
    stds: list[float] = []
    previous: Optional[np.ndarray] = None
    try:
        if opened:
            for index in range(frame_count):
                context = reader.read()
                if context is None:
                    break
                assert context.frame is not None
                frames.append(context.frame)
                contexts.append(context)
                means.append(float(np.mean(context.frame)))
                stds.append(float(np.std(context.frame)))
                if means[-1] <= 1.0 and stds[-1] <= 1.0:
                    black_frames += 1
                if previous is not None and np.array_equal(previous, context.frame):
                    duplicate_frames += 1
                previous = context.frame.copy()
                if index in sample_indices:
                    cv2.imwrite(str(output / f"frame_{index:03d}.jpg"), context.frame)
                # Give the decoder a small interval so the preflight observes
                # source progression instead of a burst of one decoded packet.
                time.sleep(0.05)
        if frames:
            build_contact_sheet(frames, output / "transport_contact_sheet.jpg")
        source_resolution = reader.source_resolution
        ai_resolution = (reader.frame_width, reader.frame_height)
        frame_shape = tuple(frames[0].shape) if frames else (0, 0, 0)
        result = "PASS" if (
            opened
            and len(frames) == frame_count
            and black_frames == 0
            and duplicate_frames == 0
        ) else "FAIL"
        return TransportPreflightResult(
            opened=opened,
            decoded_frames=len(frames),
            source_resolution=source_resolution,
            ai_resolution=ai_resolution,
            frame_shape=frame_shape,
            black_frames=black_frames,
            duplicate_frames=duplicate_frames,
            average_pixel_mean=float(np.mean(means)) if means else 0.0,
            average_pixel_std=float(np.mean(stds)) if stds else 0.0,
            first_frame_timestamp=_iso(contexts[0].timestamp) if contexts else None,
            last_frame_timestamp=_iso(contexts[-1].timestamp) if contexts else None,
            result=result,
        )
    finally:
        reader.close()


def capture_product_preflight_window(
    reader: RtspFrameReader,
    output_dir: str | Path,
    *,
    frame_count: int = 12,
) -> ProductWindowPreflightResult:
    """Save the first complete AI window before Product/SAM orchestration."""

    if frame_count != 12:
        raise ValueError("Product preflight requires a 12-frame window")
    output = Path(output_dir)
    window_dir = output / "windows" / "window_000"
    window_dir.mkdir(parents=True, exist_ok=True)
    contexts: list[RealtimeFrameContext] = []
    try:
        while len(contexts) < frame_count:
            context = reader.read_sampled()
            if context is None:
                break
            contexts.append(context)
            assert context.frame is not None
            cv2.imwrite(str(window_dir / f"frame_{len(contexts) - 1:02d}.jpg"), context.frame)
        frames = [context.frame for context in contexts if context.frame is not None]
        if frames:
            sheet = build_contact_sheet(frames, output / "window_000_contact_sheet.jpg")
        else:
            sheet = output / "window_000_contact_sheet.jpg"
        return ProductWindowPreflightResult(
            window_no=0,
            frame_count=len(contexts),
            source_seq_start=contexts[0].source_seq if contexts else None,
            source_seq_end=contexts[-1].source_seq if contexts else None,
            ai_frame_shape=tuple(frames[0].shape) if frames else (0, 0, 0),
            contact_sheet_path=str(sheet),
        )
    finally:
        reader.close()


def validate_product_coordinate_space(
    surface: ProductDisplaySurface,
    *,
    frame_size: tuple[int, int],
) -> dict[str, str]:
    if tuple(frame_size) != (1280, 720):
        raise ValueError("Product preflight requires the frozen 1280x720 AI frame")
    if not surface.points:
        raise ValueError("product display surface is empty")
    return {
        "frame_size": "1280x720",
        "surface_coordinate_space": "AI_FRAME",
        "candidate_coordinate_space": "AI_FRAME",
    }


def classify_product_candidates(
    *,
    candidates: Sequence[ProductCandidate],
    surface: ProductDisplaySurface,
    frame_width: int,
    frame_height: int,
    window_no: int,
    source_seq_start: int,
    source_seq_end: int,
    grounding_called: bool = False,
    grounding_call_count: int = 0,
    raw_grounding_object_count: Optional[int] = None,
    final_initialization_candidate_count: Optional[int] = None,
    grounding_prompt: str = "plush toy",
) -> dict[str, object]:
    """Explain every Product candidate before the frozen identity gate."""

    values = tuple(candidates)
    credible = initialization_candidates(
        candidates=values,
        product_display_surface=surface,
        frame_width=frame_width,
        frame_height=frame_height,
        surface_overlap_min=0.50,
    )
    candidate_rows: list[dict[str, object]] = []
    credible_ids = {item.local_obj_id for item in credible}
    for candidate in values:
        reason: Optional[str] = None
        try:
            evidence = evaluate_candidate(
                candidate,
                surface,
                frame_width=frame_width,
                frame_height=frame_height,
            )
            if evidence.mask_area == 0:
                reason = "EMPTY_MASK"
            elif not evidence.centroid_inside_product_display_surface:
                reason = "OUTSIDE_DISPLAY_SURFACE"
            elif evidence.surface_overlap_ratio < 0.50:
                reason = "SURFACE_OVERLAP_TOO_LOW"
            overlap = evidence.surface_overlap_ratio
            inside = evidence.centroid_inside_product_display_surface
            mask_area = evidence.mask_area
        except ValueError as error:
            reason = "OTHER"
            overlap = 0.0
            inside = False
            mask_area = int(np.count_nonzero(candidate.mask))
            error_text = str(error)
        else:
            error_text = None
        candidate_rows.append({
            "local_obj_id": candidate.local_obj_id,
            "bbox": list(candidate.bbox),
            "centroid": list(candidate.centroid),
            "mask_area": mask_area,
            "confidence": candidate.confidence,
            "surface_overlap_ratio": overlap,
            "inside_surface": inside,
            "eligible": candidate.local_obj_id in credible_ids and reason is None,
            "rejected": candidate.local_obj_id not in credible_ids or reason is not None,
            "reject_reason": reason,
            "error": error_text,
        })
    raw_count = len(values) if raw_grounding_object_count is None else int(raw_grounding_object_count)
    if raw_count == 0:
        zero_case = "CASE_A_RAW_GROUNDING_ZERO"
    elif raw_count == 1:
        zero_case = "CASE_C_SINGLE_GROUNDING_OBJECT"
    elif len(credible) == 0:
        zero_case = "CASE_B_FILTER_REJECTED"
    else:
        zero_case = None
    return {
        "diagnostic_type": "PRODUCT_INIT_DIAGNOSTIC",
        "window_no": window_no,
        "source_seq_start": source_seq_start,
        "source_seq_end": source_seq_end,
        "grounding_prompt": grounding_prompt,
        "grounding_called": grounding_called,
        "grounding_call_count": grounding_call_count,
        "grounding_window_no": window_no,
        "grounding_returned_candidate_count": len(values),
        "raw_grounding_object_count": raw_count,
        "raw_candidate_count": len(values),
        "credible_candidate_count": len(credible),
        "final_initialization_candidate_count": (
            len(credible)
            if final_initialization_candidate_count is None
            else int(final_initialization_candidate_count)
        ),
        "zero_candidate_case": zero_case,
        "surface_coordinate_space": "AI_FRAME",
        "candidate_coordinate_space": "AI_FRAME",
        "candidates": candidate_rows,
    }


def redact_rtsp_source(_source: str) -> str:
    return "REDACTED"


def render_product_preflight_surface_overlay(
    frame: np.ndarray,
    surface: ProductDisplaySurface,
    path: str | Path,
    *,
    candidates: Sequence[ProductCandidate] = (),
    assignments: Optional[Mapping[str, ProductCandidate]] = None,
) -> Path:
    """Render frozen display surface and raw Product candidate evidence."""

    if frame.shape[:2] != (720, 1280):
        raise ValueError("Product preflight overlay requires a 1280x720 AI frame")
    canvas = frame.copy()
    polygon = np.asarray(
        [(int(x * 1280), int(y * 720)) for x, y in surface.points],
        dtype=np.int32,
    )
    cv2.polylines(canvas, [polygon], True, (0, 255, 255), 3)
    selected = assignments or {}
    reverse = {id(value): key for key, value in selected.items()}
    for candidate in candidates:
        try:
            evidence = evaluate_candidate(
                candidate, surface, frame_width=1280, frame_height=720
            )
            ratio = evidence.surface_overlap_ratio
            inside = evidence.centroid_inside_product_display_surface
        except ValueError:
            ratio = 0.0
            inside = False
        label = reverse.get(id(candidate), str(candidate.local_obj_id))
        color = (0, 255, 0) if inside and ratio >= 0.50 else (0, 0, 255)
        x1, y1, x2, y2 = (int(value) for value in candidate.bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.circle(canvas, (int(candidate.centroid[0]), int(candidate.centroid[1])), 5, color, -1)
        cv2.putText(
            canvas,
            f"{label} id={candidate.local_obj_id} overlap={ratio:.2f}",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        encoded = cv2.imencode(".jpg", canvas)[1]
        try:
            encoded.tofile(str(output))
        except OSError as error:
            raise OSError(f"cannot write Product preflight overlay: {output}") from error
    return output


@dataclass(frozen=True)
class Stage10RunSummary:
    frames_processed: int
    runtime_errors: int
    dropped_frames: int
    latency_ms: Mapping[str, Mapping[str, float | int]]
    counters: Mapping[str, int]
    overlay_path: Optional[str]
    runtime_diagnostics: tuple[RuntimeErrorDiagnostic, ...] = ()
    window_count: int = 0
    provider_metrics: Mapping[str, object] = field(default_factory=dict)
    run_duration_seconds: float = 0.0
    effective_processed_fps: float = 0.0
    frame_drop_rate: float = 0.0
    performance_summary: Mapping[str, object] = field(default_factory=dict)
    product_init_pending_windows: int = 0
    product_init_ready: bool = False
    runtime_state: str = "STOPPED"
    readiness: Mapping[str, object] = field(default_factory=dict)
    ready_state_metrics: Mapping[str, object] = field(default_factory=dict)
    stage10_2_2_acceptance: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "frames_processed": self.frames_processed,
            "runtime_errors": self.runtime_errors,
            "dropped_frames": self.dropped_frames,
            "latency_ms": {key: dict(value) for key, value in self.latency_ms.items()},
            "counters": dict(self.counters),
            "overlay_path": self.overlay_path,
            "runtime_diagnostics": [item.to_dict() for item in self.runtime_diagnostics],
            "window_count": self.window_count,
            "provider_metrics": dict(self.provider_metrics),
            "run_duration_seconds": self.run_duration_seconds,
            "effective_processed_fps": self.effective_processed_fps,
            "frame_drop_rate": self.frame_drop_rate,
            "performance_summary": dict(self.performance_summary),
            "product_init_pending_windows": self.product_init_pending_windows,
            "product_init_ready": self.product_init_ready,
            "runtime_state": self.runtime_state,
            "readiness": dict(self.readiness),
            "ready_state_metrics": dict(self.ready_state_metrics),
            "stage10_2_2_acceptance": dict(self.stage10_2_2_acceptance),
        }


def run_rtsp_demo(
    *,
    reader: RtspFrameReader,
    runtime: Stage10DemoRuntime,
    overlay_path: str | Path | None = None,
    max_frames: Optional[int] = None,
) -> Stage10RunSummary:
    """Consume one RTSP source and keep processing bounded to fresh frames."""

    if max_frames is not None and max_frames < 1:
        raise ValueError("max_frames must be positive when provided")
    writer = None
    overlay_renderer = Stage10OverlayRenderer()
    latencies: dict[str, list[float]] = {}
    diagnostics: list[RuntimeErrorDiagnostic] = []
    processed = 0
    attempted = 0
    run_started = time.monotonic()
    output = None if overlay_path is None else str(Path(overlay_path))
    try:
        while max_frames is None or attempted < max_frames:
            context = reader.read_sampled()
            if context is None:
                break
            attempted += 1
            try:
                result = runtime.process_frame(context)
            except FatalStage10Error:
                raise
            except Exception as error:
                diagnostics.append(_runtime_error(
                    error,
                    source_seq=context.source_seq,
                    timestamp=context.timestamp,
                    stage="stage10_runtime",
                    provider=type(runtime.product_source).__name__,
                ))
                LOGGER.exception(
                    "Stage 10 frame processing failed: source_seq=%s",
                    context.source_seq,
                )
                continue
            processed += 1
            for name, value in result.timings_ms.items():
                latencies.setdefault(name, []).append(float(value))
            if overlay_path is not None and context.frame is not None:
                if writer is None:
                    path = Path(overlay_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(
                        str(path),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        runtime.interaction.config.sample_fps,
                        (context.frame_width, context.frame_height),
                    )
                    if not writer.isOpened():
                        raise RuntimeError(f"cannot open overlay writer: {path}")
                writer.write(overlay_renderer.render(context.frame, result))
                if isinstance(result.stage_timestamps, dict):
                    result.stage_timestamps["overlay_ready_at"] = datetime.now(timezone.utc)
    finally:
        if writer is not None:
            writer.release()
        reader.close()

    reader_counts = reader.counters.to_dict()
    runtime_counts = runtime.counters.to_dict()
    combined_counts = {
        "frames_received": int(reader_counts.get("frames_received", 0)),
        "frames_sampled": int(reader_counts.get("frames_sampled", 0)),
        "frames_processed": int(runtime_counts.get("frames_processed", 0)),
        "frames_dropped": int(reader_counts.get("frames_dropped", 0))
        + int(runtime_counts.get("frames_dropped", 0)),
        "read_failures": int(reader_counts.get("read_failures", 0)),
        "decode_failures": int(reader_counts.get("decode_failures", 0)),
        "reconnects": int(reader_counts.get("reconnects", 0)),
    }
    run_duration = max(0.0, time.monotonic() - run_started)
    received = combined_counts["frames_received"]
    return Stage10RunSummary(
        frames_processed=processed,
        runtime_errors=len(diagnostics),
        dropped_frames=reader.counters.frames_dropped + runtime.counters.frames_dropped,
        latency_ms={name: _latency_stats(values) for name, values in latencies.items()},
        counters=combined_counts,
        overlay_path=output,
        runtime_diagnostics=tuple(diagnostics),
        run_duration_seconds=run_duration,
        effective_processed_fps=(processed / run_duration if run_duration > 0 else 0.0),
        frame_drop_rate=(combined_counts["frames_dropped"] / received if received else 0.0),
    )


def run_windowed_rtsp_demo(
    *,
    source: AsyncRtspWindowSource,
    runtime: Stage10DemoRuntime,
    provider_bundle: WindowedProviderBundle,
    overlay_path: str | Path | None = None,
    keyframes_dir: str | Path | None = None,
    frame_result_path: str | Path | None = None,
    max_windows: Optional[int] = None,
    max_frames: Optional[int] = None,
    max_duration_seconds: Optional[float] = None,
    window_timeout_seconds: float = 30.0,
    performance_recorder: Optional[Stage10PerformanceRecorder] = None,
    acceptance_recorder: Optional[object] = None,
) -> Stage10RunSummary:
    """Run delayed rolling-window providers against one bounded RTSP source."""

    if max_windows is not None and max_windows < 1:
        raise ValueError("max_windows must be positive when provided")
    if max_frames is not None and max_frames < 1:
        raise ValueError("max_frames must be positive when provided")
    if max_duration_seconds is not None and max_duration_seconds <= 0:
        raise ValueError("max_duration_seconds must be positive when provided")
    if window_timeout_seconds <= 0:
        raise ValueError("window_timeout_seconds must be positive")

    writer = None
    overlay_renderer = Stage10OverlayRenderer()
    output = None if overlay_path is None else str(Path(overlay_path))
    keyframe_root = None if keyframes_dir is None else Path(keyframes_dir)
    if keyframe_root is not None:
        keyframe_root.mkdir(parents=True, exist_ok=True)
    result_stream = None
    if frame_result_path is not None:
        trace_path = Path(frame_result_path)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        result_stream = trace_path.open("a", encoding="utf-8")
    latencies: dict[str, list[float]] = {}
    diagnostics: list[RuntimeErrorDiagnostic] = []
    processed = 0
    window_count = 0
    previous_overlap: tuple[RealtimeFrameContext, ...] = ()
    provider_metrics: Mapping[str, object] = {}
    performance_summary: Mapping[str, object] = {}
    product_init_pending_windows = 0
    product_init_ready = False
    readiness_snapshot: Mapping[str, object] = {}
    started_at = 0.0
    try:
        starter = getattr(provider_bundle, "start", None)
        if callable(starter):
            starter()
        source.start()
        mark_rtsp_connected = getattr(provider_bundle, "mark_rtsp_connected", None)
        if callable(mark_rtsp_connected):
            mark_rtsp_connected()
        started_at = time.monotonic()
        while max_windows is None or window_count < max_windows:
            if max_frames is not None and processed >= max_frames:
                break
            if (
                max_duration_seconds is not None
                and time.monotonic() - started_at >= max_duration_seconds
            ):
                break
            window = source.wait_window(
                previous_overlap=previous_overlap,
                timeout_seconds=window_timeout_seconds,
            )
            if window is None:
                break
            window_count += 1
            previous_overlap = tuple(window.contexts[-4:])
            window_runtime_state = str(
                getattr(provider_bundle, "runtime_state", "STARTING")
            )
            window_payload = _window_metric_payload(
                window,
                runtime_state=window_runtime_state,
            )
            if performance_recorder is not None:
                performance_recorder.record_window(window_payload)
            acceptance_record_queue = getattr(acceptance_recorder, "record_queue", None)
            if callable(acceptance_record_queue):
                acceptance_record_queue(window_payload)
            provider_started_at = datetime.now(timezone.utc)
            try:
                observations = _validated_provider_batch(
                    window,
                    provider_bundle.process_window(window.contexts),
                )
                observations = tuple(
                    item
                    if item.provider_started_at is not None
                    else replace(item, provider_started_at=provider_started_at)
                    for item in observations
                )
                product_init_ready = bool(
                    getattr(provider_bundle, "product_init_ready", True)
                )
                provider_window_metrics = getattr(provider_bundle, "last_window_metrics", {})
                if isinstance(provider_window_metrics, Mapping):
                    window_runtime_state = str(
                        provider_window_metrics.get("window_runtime_state", window_runtime_state)
                    )
            except ProductInitializationPending as error:
                product_init_pending_windows += 1
                LOGGER.info(
                    "WAITING_FOR_PRODUCT_INIT window=%s pending=%s detail=%s",
                    window.window_no,
                    product_init_pending_windows,
                    error,
                )
                if performance_recorder is not None:
                    performance_recorder.record_provider({
                        "window_no": window.window_no,
                        "product_init_status": "WAITING_FOR_PRODUCT_INIT",
                        "product_init_pending_windows": product_init_pending_windows,
                    })
                continue
            except FatalStage10Error:
                mark_degraded = getattr(provider_bundle, "mark_degraded", None)
                if callable(mark_degraded):
                    mark_degraded("fatal_provider_error", fatal=True)
                raise
            except Exception as error:
                context = window.fresh_contexts[-1] if window.fresh_contexts else None
                diagnostics.append(_runtime_error(
                    error,
                    source_seq=None if context is None else context.source_seq,
                    timestamp=None if context is None else context.timestamp,
                    stage="provider_bundle",
                    provider=type(provider_bundle).__name__,
                ))
                LOGGER.exception("Stage 10 provider window failed: window=%s", window.window_no)
                mark_degraded = getattr(provider_bundle, "mark_degraded", None)
                if callable(mark_degraded):
                    mark_degraded("provider_window_error")
                continue

            window_results: list[object] = []
            for item in observations:
                if max_frames is not None and processed >= max_frames:
                    break
                try:
                    result = runtime.process_observations(
                        context=item.context,
                        person_tracks=item.person_tracks,
                        product_inputs=item.products,
                        person_ready_at=item.person_ready_at,
                        product_ready_at=item.product_ready_at,
                        provider_started_at=item.provider_started_at,
                        person_inference_ms=item.person_inference_ms,
                        product_inference_ms=item.product_inference_ms,
                    )
                except FatalStage10Error:
                    raise
                except Exception as error:
                    diagnostics.append(_runtime_error(
                        error,
                        source_seq=item.context.source_seq,
                        timestamp=item.context.timestamp,
                        stage="stage10_runtime",
                        provider=type(provider_bundle).__name__,
                    ))
                    LOGGER.exception(
                        "Stage 10 delayed frame processing failed: source_seq=%s",
                        item.context.source_seq,
                    )
                    continue
                processed += 1
                window_results.append(result)
                for name, value in result.timings_ms.items():
                    latencies.setdefault(name, []).append(float(value))
                if overlay_path is not None and item.context.frame is not None:
                    overlay_started = time.perf_counter()
                    if writer is None:
                        path = Path(overlay_path)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        writer = cv2.VideoWriter(
                            str(path),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            runtime.interaction.config.sample_fps,
                            (item.context.frame_width, item.context.frame_height),
                        )
                        if not writer.isOpened():
                            raise FatalStage10Error(f"cannot open overlay writer: {path}")
                    rendered = overlay_renderer.render(item.context.frame, result)
                    writer.write(rendered)
                    if isinstance(result.stage_timestamps, dict):
                        result.stage_timestamps["overlay_ready_at"] = datetime.now(timezone.utc)
                    if isinstance(result.timings_ms, dict):
                        result.timings_ms["overlay_ms"] = max(
                            0.0,
                            (time.perf_counter() - overlay_started) * 1000.0,
                        )
                    if keyframe_root is not None and result.events:
                        keyframe_started = time.perf_counter()
                        for event in result.events:
                            name = (
                                f"{item.context.source_seq:08d}_"
                                f"{event.product.get('product_key')}_{event.event_type}.jpg"
                            )
                            cv2.imwrite(str(keyframe_root / name), rendered)
                        if isinstance(result.timings_ms, dict):
                            result.timings_ms["keyframe_write_ms"] = max(
                                0.0,
                                (time.perf_counter() - keyframe_started) * 1000.0,
                            )
                elif isinstance(result.timings_ms, dict):
                    result.timings_ms["overlay_ms"] = 0.0
                if result_stream is not None:
                    trace_payload = (
                        result.to_dict()
                        if callable(getattr(result, "to_dict", None))
                        else {
                            "camera_id": item.context.camera_id,
                            "source_seq": item.context.source_seq,
                            "timestamp": item.context.timestamp.isoformat(),
                            "timings_ms": dict(result.timings_ms),
                        }
                    )
                    result_write_started = time.perf_counter()
                    result_stream.write(
                        json.dumps(trace_payload, ensure_ascii=False) + "\n"
                    )
                    if isinstance(result.timings_ms, dict):
                        result.timings_ms["frame_result_write_ms"] = max(
                            0.0,
                            (time.perf_counter() - result_write_started) * 1000.0,
                        )
                if performance_recorder is not None:
                    frame_payload = _frame_latency_payload(
                        window_no=window.window_no,
                        observation=item,
                        result=result,
                        runtime_state=window_runtime_state,
                    )
                    performance_recorder.record_frame(frame_payload)
                else:
                    frame_payload = _frame_latency_payload(
                        window_no=window.window_no,
                        observation=item,
                        result=result,
                        runtime_state=window_runtime_state,
                    )
                acceptance_record_frame = getattr(acceptance_recorder, "record_frame", None)
                if callable(acceptance_record_frame):
                    acceptance_record_frame(frame_payload, result.to_dict())
            provider_payload = _provider_metric_payload(
                window=window,
                observations=observations,
                results=window_results,
                provider_bundle=provider_bundle,
                provider_started_at=provider_started_at,
            )
            if performance_recorder is not None:
                performance_recorder.record_provider(provider_payload)
            acceptance_record_provider = getattr(acceptance_recorder, "record_provider", None)
            if callable(acceptance_record_provider):
                acceptance_record_provider(provider_payload)
        source_error = source.error
        if source_error is not None:
            diagnostics.append(source_error)
    finally:
        if writer is not None:
            writer.release()
        if result_stream is not None:
            result_stream.close()
        try:
            provider_metrics = dict(provider_bundle.gpu_metrics())
        except Exception as error:  # Metrics must not hide completed inference.
            diagnostics.append(_runtime_error(
                error,
                source_seq=None,
                timestamp=None,
                stage="provider_metrics",
                provider=type(provider_bundle).__name__,
            ))
        source.stop()
        provider_bundle.close()
        readiness_getter = getattr(provider_bundle, "readiness_snapshot", None)
        if callable(readiness_getter):
            readiness_snapshot = dict(readiness_getter())
        if performance_recorder is not None:
            try:
                performance_summary = performance_recorder.close()
            except Exception as error:
                diagnostics.append(_runtime_error(
                    error,
                    source_seq=None,
                    timestamp=None,
                    stage="performance_metrics",
                    provider=type(performance_recorder).__name__,
                ))

    reader = getattr(source, "reader", None)
    reader_counters = getattr(reader, "counters", RuntimeCounters()).to_dict()
    runtime_counters = runtime.counters.to_dict()
    combined_counts = {
        "frames_received": int(reader_counters.get("frames_received", 0)),
        "frames_sampled": int(reader_counters.get("frames_sampled", 0)),
        "frames_processed": int(runtime_counters.get("frames_processed", 0)),
        "frames_dropped": int(reader_counters.get("frames_dropped", 0))
        + int(runtime_counters.get("frames_dropped", 0)),
        "read_failures": int(reader_counters.get("read_failures", 0)),
        "decode_failures": int(reader_counters.get("decode_failures", 0)),
        "reconnects": int(reader_counters.get("reconnects", 0)),
    }
    run_duration = max(0.0, time.monotonic() - started_at)
    received = combined_counts["frames_received"]
    summary = Stage10RunSummary(
        frames_processed=processed,
        runtime_errors=len(diagnostics),
        dropped_frames=combined_counts["frames_dropped"],
        latency_ms={name: _latency_stats(values) for name, values in latencies.items()},
        counters=combined_counts,
        overlay_path=output,
        runtime_diagnostics=tuple(diagnostics),
        window_count=window_count,
        provider_metrics=provider_metrics,
        run_duration_seconds=run_duration,
        effective_processed_fps=(processed / run_duration if run_duration > 0 else 0.0),
        frame_drop_rate=(combined_counts["frames_dropped"] / received if received else 0.0),
        performance_summary=performance_summary,
        product_init_pending_windows=product_init_pending_windows,
        product_init_ready=product_init_ready,
        runtime_state=str(readiness_snapshot.get("runtime_state", "STOPPED")),
        readiness=readiness_snapshot,
        ready_state_metrics=(
            dict(performance_summary.get("ready_state_metrics", {}))
            if isinstance(performance_summary.get("ready_state_metrics", {}), Mapping)
            else {}
        ),
    )
    acceptance_close = getattr(acceptance_recorder, "close", None)
    if callable(acceptance_close):
        acceptance = acceptance_close(
            performance_summary=performance_summary,
            runtime_summary=summary.to_dict(),
            readiness=readiness_snapshot,
        )
        summary = replace(summary, stage10_2_2_acceptance=dict(acceptance))
    return summary


def build_demo_overlay_view(
    result: Stage10FrameResult,
    *,
    recent_event: Optional[Stage10DemoEvent] = None,
    rtsp_connected: bool = True,
) -> dict[str, object]:
    persons = []
    person_names: dict[int, str] = {}
    for person in result.persons:
        identity = result.person_display_identities.get(person.track_token)
        identity = identity or resolve_person_display_identity(person.track_id)
        person_names[person.track_id] = identity.display_name
        persons.append({
            "track_id": person.track_id,
            "track_token": person.track_token,
            "bbox": person.bbox,
            **identity.to_dict(),
            "caption": f"顾客轨迹 {person.track_id}",
        })

    associations = {item.product_key: item for item in result.associations}
    interactions = {item.observation.product_key: item for item in result.interactions}
    products = []
    current_interactions = []
    for product in result.products:
        presentation = product_presentation(product.product_key)
        interaction = interactions.get(product.product_key)
        association = associations.get(product.product_key)
        motion = "稳定" if interaction is None else chinese_status(interaction.motion.motion_state)
        interaction_state = "空闲" if interaction is None else chinese_status(interaction.interaction_state)
        stable_frames = 0 if interaction is None else interaction.motion.stable_frames
        displacement = 0.0 if interaction is None else interaction.motion.rolling_displacement_pixels
        displacement_text = (
            "--" if displacement is None else f"{float(displacement):.1f}px"
        )
        products.append({
            "product_key": product.product_key,
            "bbox": product.bbox,
            "track_id": product.product_track_id,
            "track_status": product.track_status.value,
            "chinese_name": presentation.chinese_name,
            "display_name": presentation.display_name,
            "summary": f"{presentation.chinese_name}｜{motion}｜{interaction_state}",
            "detail": f"稳定 {stable_frames} 帧｜位移 {displacement_text}",
            "caption": f"轨迹 {product.product_track_id}",
        })
        actor_id = None if association is None else association.person_track_id
        if actor_id is not None:
            actor_name = person_names.get(actor_id, f"顾客{actor_id}")
            current_interactions.append({
                "person_track_id": actor_id,
                "person_name": actor_name,
                "product_key": product.product_key,
                "product_name": presentation.chinese_name,
                "state": interaction_state,
                "duration_seconds": (
                    None if interaction is None else interaction.holding_duration_seconds
                ),
                "relationship": f"{actor_name} → {presentation.chinese_name}",
            })

    display_event = _overlay_display_event(result.events) if result.events else recent_event
    events = [] if display_event is None else [display_event.to_dict()["display"]]
    return {
        "persons": persons,
        "products": products,
        "current_interactions": current_interactions,
        "events": events,
        "telemetry": {
            "connected": bool(rtsp_connected),
            "source_seq": result.context.source_seq,
            "latency_ms": float(result.timings_ms.get("end_to_end_ms", 0.0)),
        },
    }


def _overlay_display_event(
    events: Sequence[Stage10DemoEvent],
) -> Stage10DemoEvent:
    return next(
        (
            event
            for event in reversed(events)
            if event.event_type in {"PICK_UP", "PUT_DOWN"}
        ),
        events[-1],
    )


@lru_cache(maxsize=32)
def _overlay_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc")
    if not path.exists():
        raise RuntimeError(f"Chinese overlay font is unavailable: {path}")
    return ImageFont.truetype(str(path), size=size)


def _fit_overlay_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    preferred_size: int,
    minimum_size: int,
    bold: bool = False,
) -> ImageFont.FreeTypeFont:
    for size in range(preferred_size, minimum_size - 1, -1):
        font = _overlay_font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _overlay_font(minimum_size, bold)


class Stage10OverlayRenderer:
    """Stateful Chinese overlay with a short-lived event banner."""

    PERSON = (53, 208, 226, 255)
    PRODUCT = (74, 222, 128, 255)
    AMBER = (251, 191, 36, 255)
    EVENT = (255, 90, 95, 255)
    TEXT = (248, 250, 252, 255)
    MUTED = (203, 213, 225, 255)
    PANEL = (17, 24, 39, 210)

    def __init__(self, *, event_hold_seconds: float = 3.0) -> None:
        self.event_hold_seconds = max(0.0, float(event_hold_seconds))
        self._recent_event: Optional[Stage10DemoEvent] = None
        self._event_expires_at: Optional[datetime] = None

    def render(
        self,
        frame: np.ndarray,
        result: Stage10FrameResult,
        *,
        rtsp_connected: bool = True,
    ) -> np.ndarray:
        if not isinstance(frame, np.ndarray) or frame.ndim not in {2, 3}:
            raise ValueError("frame must be a numpy image")
        if result.events:
            self._recent_event = _overlay_display_event(result.events)
            self._event_expires_at = (
                _utc_naive(result.context.timestamp)
                + timedelta(seconds=self.event_hold_seconds)
            )
        elif (
            self._event_expires_at is not None
            and _utc_naive(result.context.timestamp) > self._event_expires_at
        ):
            self._recent_event = None
            self._event_expires_at = None

        if frame.ndim == 2:
            canvas = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            canvas = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        else:
            canvas = frame.copy()
        image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image, "RGBA")
        width, height = image.size
        view = build_demo_overlay_view(
            result,
            recent_event=self._recent_event,
            rtsp_connected=rtsp_connected,
        )

        title_font = _overlay_font(22, True)
        body_font = _overlay_font(17)
        small_font = _overlay_font(14)
        for index, product in enumerate(view["products"]):
            y = 16 + index * 58
            draw.rounded_rectangle((16, y, 350, y + 50), radius=4, fill=self.PANEL)
            draw.text((28, y + 5), str(product["summary"]), font=title_font, fill=self.TEXT)
            draw.text((28, y + 29), str(product["detail"]), font=small_font, fill=self.MUTED)

        for person in view["persons"]:
            x1, y1, x2, y2 = _pixel_bbox(person["bbox"], width, height)
            draw.rectangle((x1, y1, x2, y2), outline=self.PERSON, width=3)
            label = str(person["display_name"])
            font = _fit_overlay_font(
                draw,
                label,
                max_width=max(120, x2 - x1 + 80),
                preferred_size=22,
                minimum_size=16,
                bold=True,
            )
            bbox = draw.textbbox((0, 0), label, font=font)
            label_width = bbox[2] - bbox[0]
            top = max(4, y1 - 34)
            draw.rounded_rectangle(
                (x1, top, min(width - 4, x1 + label_width + 18), top + 30),
                radius=4,
                fill=(17, 24, 39, 225),
            )
            draw.text((x1 + 8, top + 3), label, font=font, fill=self.PERSON)
            draw.text((x1 + 4, min(height - 20, y2 + 4)), str(person["caption"]), font=small_font, fill=self.PERSON)

        for product in view["products"]:
            if product["bbox"] is None:
                continue
            x1, y1, x2, y2 = _pixel_bbox(product["bbox"], width, height)
            color = self.PRODUCT if product["track_status"] == "ACTIVE" else self.AMBER
            draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
            label = str(product["chinese_name"])
            bbox = draw.textbbox((0, 0), label, font=title_font)
            label_width = bbox[2] - bbox[0]
            top = max(4, y1 - 32)
            draw.rounded_rectangle(
                (x1, top, min(width - 4, x1 + label_width + 18), top + 29),
                radius=4,
                fill=(17, 24, 39, 225),
            )
            draw.text((x1 + 8, top + 2), label, font=title_font, fill=color)
            draw.text((x1 + 4, min(height - 20, y2 + 4)), str(product["caption"]), font=small_font, fill=color)

        events = view["events"]
        if events:
            message = str(events[-1]["message"])
            left, right = 378, min(width - 16, 916)
            font = _fit_overlay_font(
                draw,
                message,
                max_width=right - left - 36,
                preferred_size=26,
                minimum_size=17,
                bold=True,
            )
            draw.rounded_rectangle((left, 16, right, 62), radius=4, fill=(127, 29, 29, 225), outline=self.EVENT, width=2)
            draw.text((left + 18, 23), message, font=font, fill=self.TEXT)

        interactions = view["current_interactions"]
        if interactions:
            panel_left = max(16, width - 314)
            panel_height = 42 + min(2, len(interactions)) * 48
            draw.rounded_rectangle(
                (panel_left, 92, width - 16, 92 + panel_height),
                radius=4,
                fill=self.PANEL,
            )
            draw.text((panel_left + 16, 100), "当前交互", font=title_font, fill=self.AMBER)
            for index, interaction in enumerate(interactions[:2]):
                y = 132 + index * 48
                draw.text((panel_left + 16, y), str(interaction["relationship"]), font=body_font, fill=self.TEXT)
                detail = f"状态：{interaction['state']}"
                if interaction["duration_seconds"] is not None:
                    detail += f"｜{float(interaction['duration_seconds']):.1f} 秒"
                draw.text((panel_left + 16, y + 23), detail, font=small_font, fill=self.MUTED)

        telemetry = view["telemetry"]
        status = "连接正常" if telemetry["connected"] else "连接中断"
        line = (
            f"{status}  ·  帧 {telemetry['source_seq']}  ·  "
            f"延迟 {float(telemetry['latency_ms']):.1f} ms"
        )
        bbox = draw.textbbox((0, 0), line, font=small_font)
        draw.rounded_rectangle(
            (12, height - 34, min(width - 12, bbox[2] + 32), height - 8),
            radius=3,
            fill=(17, 24, 39, 190),
        )
        draw.text((20, height - 31), line, font=small_font, fill=self.MUTED)
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def render_unified_overlay(
    frame: np.ndarray,
    result: Stage10FrameResult,
    *,
    rtsp_connected: bool = True,
) -> np.ndarray:
    """Render one Chinese overlay frame through the compatibility seam."""

    return Stage10OverlayRenderer(event_hold_seconds=0.0).render(
        frame,
        result,
        rtsp_connected=rtsp_connected,
    )


def _validated_provider_batch(
    window: RealtimeWindow,
    observations: Sequence[ProviderFrameObservations],
) -> tuple[ProviderFrameObservations, ...]:
    expected = {item.source_seq: item for item in window.fresh_contexts}
    received: dict[int, ProviderFrameObservations] = {}
    for item in observations:
        if not isinstance(item, ProviderFrameObservations):
            raise ProviderContractError(
                "Window provider must return ProviderFrameObservations values"
            )
        context = item.context
        expected_context = expected.get(context.source_seq)
        if expected_context is None:
            raise ProviderContractError(
                f"Provider returned non-fresh source_seq {context.source_seq}"
            )
        if context.source_seq in received:
            raise ProviderContractError(
                f"Provider returned duplicate source_seq {context.source_seq}"
            )
        if (
            context.camera_id != expected_context.camera_id
            or context.frame_width != expected_context.frame_width
            or context.frame_height != expected_context.frame_height
            or _utc_naive(context.timestamp) != _utc_naive(expected_context.timestamp)
        ):
            raise ProviderContractError(
                f"Provider changed frame identity for source_seq {context.source_seq}"
            )
        for product_input in item.products:
            product = _unwrap_product(product_input)
            if (
                product.camera_id != context.camera_id
                or product.source_seq != context.source_seq
                or _utc_naive(product.observed_at) != _utc_naive(context.timestamp)
            ):
                raise ProviderContractError(
                    f"Product observation does not match source_seq {context.source_seq}"
                )
        received[context.source_seq] = item
    missing = sorted(set(expected) - set(received))
    if missing:
        raise ProviderContractError(
            f"Provider omitted fresh source_seq values: {missing}"
        )
    return tuple(received[item.source_seq] for item in window.fresh_contexts)


def _runtime_error(
    error: BaseException,
    *,
    source_seq: Optional[int],
    timestamp: Optional[datetime],
    stage: str,
    provider: Optional[str],
) -> RuntimeErrorDiagnostic:
    return RuntimeErrorDiagnostic(
        source_seq=source_seq,
        timestamp=timestamp,
        exception_type=type(error).__name__,
        exception_message=str(error),
        stage=stage,
        provider=provider,
        traceback_text="".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ),
    )


def _datetime_delta_ms(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return max(0.0, (_utc_naive(end) - _utc_naive(start)).total_seconds() * 1000.0)


def _window_metric_payload(
    window: RealtimeWindow,
    *,
    runtime_state: str = "STARTING",
) -> dict[str, object]:
    contexts = window.fresh_contexts
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "window_no": window.window_no,
        "runtime_state": str(runtime_state),
        "first_context_source_seq": contexts[0].source_seq if contexts else None,
        "last_context_source_seq": contexts[-1].source_seq if contexts else None,
        "window_wait_ms": max(0.0, float(window.window_wait_ms)),
        "queue_depth_before": int(window.queue_depth_before),
        "queue_depth_after": int(window.queue_depth_after),
        "dropped_before_window": int(window.dropped_before_window),
        "dropped_during_window": int(window.dropped_during_window or window.dropped_frames),
        "oldest_frame_age_ms": max(0.0, float(window.oldest_frame_age_ms)),
        "newest_frame_age_ms": max(0.0, float(window.newest_frame_age_ms)),
        "fresh_frame_count": int(window.fresh_frame_count or len(contexts)),
    }


def _provider_metric_payload(
    *,
    window: RealtimeWindow,
    observations: Sequence[ProviderFrameObservations],
    results: Sequence[object],
    provider_bundle: object,
    provider_started_at: datetime,
) -> dict[str, object]:
    provider = getattr(provider_bundle, "last_window_metrics", {})
    provider = dict(provider) if isinstance(provider, Mapping) else {}
    first = observations[0] if observations else None
    now = datetime.now(timezone.utc)
    person_ready = None if first is None else first.person_ready_at
    product_ready = None if first is None else first.product_ready_at
    person_ms = provider.get(
        "person_inference_ms",
        0.0 if first is None else first.person_inference_ms,
    )
    product_ms = provider.get(
        "product_inference_ms",
        0.0 if first is None else first.product_inference_ms,
    )
    downstream = {
        name: sum(
            float(getattr(result, "timings_ms", {}).get(name, 0.0))
            for result in results
        )
        for name in (
            "association_ms",
            "interaction_ms",
            "overlay_ms",
            "event_emit_ms",
            "frame_result_write_ms",
            "keyframe_write_ms",
        )
    }
    product_profile = {
        field: provider.get(field)
        for field in (
            "product_phase",
            "product_session_start_ms",
            "product_grounding_ms",
            "product_propagation_ms",
            "product_output_conversion_ms",
            "product_candidate_evaluation_ms",
            "product_track_association_ms",
            "product_candidate_association_ms",
            "product_artifact_conversion_ms",
            "product_session_close_ms",
            "product_other_ms",
            "product_unattributed_ms",
            "product_materialize_ms",
            "product_accounted_ms",
            "product_accounted_ratio",
            "slow_product_window",
            "grounding_candidate_count",
            "product_track_count",
            "allocated_before_mb",
            "reserved_before_mb",
            "allocated_after_mb",
            "reserved_after_mb",
            "peak_allocated_mb",
            "cuda_memory_delta_mb",
        )
        if field in provider
    }
    person_profile = {
        field: provider.get(field)
        for field in (
            "runtime_state",
            "window_runtime_state",
            "runtime_state_after_window",
            "person_scene_phase",
            "person_session_start_ms",
            "person_grounding_ms",
            "person_regrounding_ms",
            "person_global_association_ms",
            "person_propagation_ms",
            "person_output_conversion_ms",
            "person_session_close_ms",
            "person_other_ms",
            "person_unattributed_ms",
            "person_track_count",
            "expected_old_track_count",
            "new_grounding_count",
            "empty_scene_streak",
            "empty_scene_mode",
            "grounding_skipped_due_to_empty_throttle",
            "association_match_count",
            "serialized_provider_ms",
            "provider_wall_ms",
        )
        if field in provider
    }
    return {
        "recorded_at": now.isoformat(),
        "window_no": window.window_no,
        "person_inference_ms": max(0.0, float(person_ms)),
        "person_session_count": int(provider.get("person_session_count", 0)),
        "person_track_count": int(provider.get("person_track_count", 0)),
        "person_ready_at": _iso(person_ready),
        "person_input_oldest_age_ms": _datetime_delta_ms(
            window.fresh_contexts[0].captured_at if window.fresh_contexts else None,
            person_ready,
        ),
        "person_input_newest_age_ms": _datetime_delta_ms(
            window.fresh_contexts[-1].captured_at if window.fresh_contexts else None,
            person_ready,
        ),
        "product_inference_ms": max(0.0, float(product_ms)),
        "product_session_count": int(provider.get("product_session_count", 0)),
        "grounding_count": int(provider.get("grounding_count", 0)),
        "product_ready_at": _iso(product_ready),
        "product_input_oldest_age_ms": _datetime_delta_ms(
            window.fresh_contexts[0].captured_at if window.fresh_contexts else None,
            product_ready,
        ),
        "product_input_newest_age_ms": _datetime_delta_ms(
            window.fresh_contexts[-1].captured_at if window.fresh_contexts else None,
            product_ready,
        ),
        **person_profile,
        **product_profile,
        **downstream,
    }


def _frame_latency_payload(
    *,
    window_no: int,
    observation: ProviderFrameObservations,
    result: object,
    runtime_state: str = "STARTING",
) -> dict[str, object]:
    context = observation.context
    timestamps = getattr(result, "stage_timestamps", {})
    timestamps = timestamps if isinstance(timestamps, Mapping) else {}
    capture = context.timestamp
    provider_start = timestamps.get("provider_start_at") or observation.provider_started_at
    timings = getattr(result, "timings_ms", {})
    timings = timings if isinstance(timings, Mapping) else {}
    overlay_ready = timestamps.get("overlay_ready_at")
    overlay_delta = _datetime_delta_ms(capture, overlay_ready)
    if overlay_delta is None:
        overlay_delta = float(timings.get("end_to_end_ms", 0.0))
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "window_no": window_no,
        "runtime_state": str(runtime_state),
        "source_seq": context.source_seq,
        "capture_at": _iso(capture),
        "capture_to_provider_start_ms": _datetime_delta_ms(capture, provider_start),
        "capture_to_person_ready_ms": _datetime_delta_ms(capture, timestamps.get("person_ready_at")),
        "capture_to_product_ready_ms": _datetime_delta_ms(capture, timestamps.get("product_ready_at")),
        "capture_to_association_ready_ms": _datetime_delta_ms(capture, timestamps.get("association_ready_at")),
        "capture_to_interaction_ready_ms": _datetime_delta_ms(capture, timestamps.get("interaction_ready_at")),
        "capture_to_event_ms": _datetime_delta_ms(capture, timestamps.get("event_emitted_at")),
        "capture_to_overlay_ms": overlay_delta,
        "person_inference_ms": max(0.0, float(timings.get("person_ms", observation.person_inference_ms))),
        "product_inference_ms": max(0.0, float(timings.get("product_ms", observation.product_inference_ms))),
        "association_ms": max(0.0, float(timings.get("association_ms", 0.0))),
        "interaction_ms": max(0.0, float(timings.get("interaction_ms", 0.0))),
        "event_emit_ms": max(0.0, float(timings.get("event_emit_ms", 0.0))),
        "overlay_ms": max(0.0, float(timings.get("overlay_ms", 0.0))),
        "frame_result_write_ms": max(0.0, float(timings.get("frame_result_write_ms", 0.0))),
        "keyframe_write_ms": max(0.0, float(timings.get("keyframe_write_ms", 0.0))),
        "pipeline_lag_ms": max(0.0, float(timings.get("pipeline_lag_ms", timings.get("end_to_end_ms", 0.0)))),
    }


def _latency_stats(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "average": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "average": float(sum(values) / len(values)),
        "p50": float(np.percentile(ordered, 50)),
        "p95": float(np.percentile(ordered, 95)),
        "max": float(max(values)),
    }


def _person_association_observation(
    adapter: Sam31BehaviorAdapter,
    context: RealtimeFrameContext,
    track: Sam31GlobalTrack,
) -> PersonAssociationObservation:
    record = adapter.to_record(
        observed_at=context.timestamp,
        source_seq=context.source_seq,
        global_track_id=track.global_track_id,
        bbox=track.bbox,
        centroid=track.centroid,
    )
    x1, y1, x2, y2 = record.bbox
    bbox = (x1 / context.frame_width, y1 / context.frame_height, x2 / context.frame_width, y2 / context.frame_height)
    centroid = record.centroid
    if centroid is None:
        centroid = ((x1 + x2) / (2.0 * context.frame_width), (y1 + y2) / (2.0 * context.frame_height))
    else:
        centroid = (centroid[0] / context.frame_width, centroid[1] / context.frame_height)
    return PersonAssociationObservation(
        observed_at=context.timestamp,
        source_seq=context.source_seq,
        camera_id=context.camera_id,
        track_id=record.global_track_id,
        track_token=record.observation.track_token,
        bbox=bbox,
        centroid=centroid,
        foot_point=record.observation.foot_point,
        confidence=record.observation.confidence,
        role=record.observation.role,
    )


def _product_association_observation(
    result: ProductWindowResult,
    camera_id: str,
    frame_width: int,
    frame_height: int,
) -> ProductAssociationObservation:
    status = result.track_status or ProductTrackStatus.LOST
    observed = status is ProductTrackStatus.ACTIVE and result.bbox is not None and result.centroid is not None
    bbox = None
    if observed:
        assert result.bbox is not None
        bbox = (
            result.bbox[0] / frame_width,
            result.bbox[1] / frame_height,
            result.bbox[2] / frame_width,
            result.bbox[3] / frame_height,
        )
    return ProductAssociationObservation(
        observed_at=result.observed_at,
        source_seq=result.source_seq,
        camera_id=camera_id,
        product_key=result.product_key,
        product_type=result.product_type,
        product_track_id=int(result.product_track_id or 0),
        product_track_token=str(result.product_track_token or f"product:{camera_id}:{result.product_key}:0"),
        bbox=bbox,
        centroid=result.centroid if observed else None,
        track_status=status,
        display_state=result.display_state,
        mask_present=observed,
    )


def _interaction_observation(
    product: ProductAssociationObservation,
    association: PersonProductAssociationResult,
    *,
    surface_overlap_ratio: Optional[float],
) -> ProductInteractionObservation:
    return ProductInteractionObservation(
        observed_at=product.observed_at,
        source_seq=product.source_seq,
        camera_id=product.camera_id,
        product_key=product.product_key,
        product_type=product.product_type,
        product_track_id=product.product_track_id,
        product_track_token=product.product_track_token,
        product_track_status=product.track_status,
        product_mask_present=product.mask_present,
        bbox=product.bbox,
        centroid=product.centroid,
        display_state=product.display_state,
        surface_overlap_ratio=surface_overlap_ratio,
        associated_person_track_id=association.person_track_id,
        associated_person_track_token=association.person_track_token,
        person_product_association_status=association.status,
        association_grace_active=association.association_grace_active,
    )


def _unwrap_product(
    value: ProductAssociationObservation | Stage10ProductObservation,
) -> ProductAssociationObservation:
    return value.observation if isinstance(value, Stage10ProductObservation) else value


def _normalize_person_track(value: Sam31GlobalTrack | Mapping[str, object]) -> Sam31GlobalTrack:
    if isinstance(value, Sam31GlobalTrack):
        return value
    if isinstance(value, Mapping):
        return Sam31GlobalTrack.from_mapping(value)
    raise ProviderContractError(
        f"Person provider returned unsupported value: {type(value).__name__}"
    )


def _person_dict(
    value: PersonAssociationObservation,
    identity: Optional[PersonDisplayIdentity] = None,
) -> dict[str, object]:
    resolved = identity or resolve_person_display_identity(value.track_id)
    return {
        "track_id": value.track_id,
        "track_token": value.track_token,
        "camera_id": value.camera_id,
        "source_seq": value.source_seq,
        "timestamp": value.observed_at.isoformat(),
        "bbox": list(value.bbox),
        "centroid": list(value.centroid),
        "foot_point": list(value.foot_point),
        "role": value.role,
        "confidence": value.confidence,
        **resolved.to_dict(),
    }


def _product_dict(value: ProductAssociationObservation) -> dict[str, object]:
    presentation = product_presentation(value.product_key)
    return {
        "product_key": value.product_key,
        "product_type": value.product_type,
        "track_id": value.product_track_id,
        "track_token": value.product_track_token,
        "track_status": value.track_status.value,
        "mask_present": value.mask_present,
        "bbox": None if value.bbox is None else list(value.bbox),
        "centroid": None if value.centroid is None else list(value.centroid),
        "display_state": value.display_state.value,
        "source_seq": value.source_seq,
        "timestamp": value.observed_at.isoformat(),
        "english_name": presentation.english_name,
        "chinese_name": presentation.chinese_name,
        "display_name": presentation.display_name,
    }


def _pixel_bbox(value: BBox, width: int, height: int) -> tuple[int, int, int, int]:
    return tuple(int(round(item)) for item in (value[0] * width, value[1] * height, value[2] * width, value[3] * height))  # type: ignore[return-value]


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else _utc_naive(value).isoformat()
