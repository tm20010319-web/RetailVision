"""Stage 10 runtime readiness, Person profiling, and acceptance diagnostics."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

import numpy as np


class RuntimeState(str, Enum):
    STARTING = "STARTING"
    PRODUCT_WARMUP = "PRODUCT_WARMUP"
    PRODUCT_INITIALIZING = "PRODUCT_INITIALIZING"
    PERSON_PRIMING = "PERSON_PRIMING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class _JsonlWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a", encoding="utf-8")
        self._closed = False

    def write(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise RuntimeError("JSONL writer is closed")
        self._stream.write(json.dumps(dict(payload), ensure_ascii=True, default=str) + "\n")

    def close(self) -> None:
        if self._closed:
            return
        self._stream.flush()
        self._stream.close()
        self._closed = True


def latency_stats(values: Sequence[float]) -> dict[str, float | int]:
    clean = [max(0.0, float(value)) for value in values]
    if not clean:
        return {"count": 0, "average": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(clean),
        "average": float(sum(clean) / len(clean)),
        "p50": float(np.percentile(clean, 50)),
        "p95": float(np.percentile(clean, 95)),
        "max": float(max(clean)),
    }


class RuntimeReadinessController:
    """Own the Stage 10 orchestration lifecycle without touching domain state."""

    _ALLOWED = {
        RuntimeState.STARTING: {RuntimeState.PRODUCT_WARMUP, RuntimeState.PRODUCT_INITIALIZING, RuntimeState.DEGRADED, RuntimeState.STOPPING},
        RuntimeState.PRODUCT_WARMUP: {RuntimeState.PRODUCT_INITIALIZING, RuntimeState.DEGRADED, RuntimeState.STOPPING},
        RuntimeState.PRODUCT_INITIALIZING: {RuntimeState.PERSON_PRIMING, RuntimeState.DEGRADED, RuntimeState.STOPPING},
        RuntimeState.PERSON_PRIMING: {RuntimeState.READY, RuntimeState.DEGRADED, RuntimeState.STOPPING},
        RuntimeState.READY: {RuntimeState.DEGRADED, RuntimeState.STOPPING},
        RuntimeState.DEGRADED: {RuntimeState.READY, RuntimeState.STOPPING},
        RuntimeState.STOPPING: {RuntimeState.STOPPED},
        RuntimeState.STOPPED: set(),
    }

    def __init__(
        self,
        output_dir: str | Path,
        *,
        ready_after_completed_windows: int = 2,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if ready_after_completed_windows < 1:
            raise ValueError("ready_after_completed_windows must be positive")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ready_after_completed_windows = int(ready_after_completed_windows)
        self._monotonic = monotonic
        self._now = now
        self._started_perf = monotonic()
        self.started_at = now()
        self.ready_at: Optional[datetime] = None
        self.state = RuntimeState.STARTING
        self.completed_provider_windows = 0
        self.rtsp_connected = False
        self.product_init_ready = False
        self.product_tokens_valid = False
        self.predictor_ready = False
        self.person_provider_primed = False
        self.fatal_runtime_errors = 0
        self.degraded_reason: Optional[str] = None
        self._writer = _JsonlWriter(self.output_dir / "runtime_state.jsonl")
        self._write_transition(None, RuntimeState.STARTING, "runtime_created")

    @property
    def startup_to_ready_seconds(self) -> Optional[float]:
        if self.ready_at is None:
            return None
        return max(0.0, (self.ready_at - self.started_at).total_seconds())

    def _write_transition(
        self,
        previous: Optional[RuntimeState],
        current: RuntimeState,
        reason: str,
    ) -> None:
        self._writer.write({
            "timestamp": self._now().isoformat(),
            "elapsed_seconds": max(0.0, self._monotonic() - self._started_perf),
            "previous_state": None if previous is None else previous.value,
            "runtime_state": current.value,
            "reason": reason,
            "completed_provider_windows": self.completed_provider_windows,
            "rtsp_connected": self.rtsp_connected,
            "product_init_ready": self.product_init_ready,
            "product_tokens_valid": self.product_tokens_valid,
            "predictor_ready": self.predictor_ready,
            "person_provider_primed": self.person_provider_primed,
            "fatal_runtime_errors": self.fatal_runtime_errors,
        })

    def transition(self, target: RuntimeState, reason: str) -> None:
        if target == self.state:
            return
        if target not in self._ALLOWED[self.state]:
            raise RuntimeError(f"invalid Runtime READY transition: {self.state.value} -> {target.value}")
        previous = self.state
        self.state = target
        if target is RuntimeState.READY and self.ready_at is None:
            self.ready_at = self._now()
        self._write_transition(previous, target, reason)
        self._write_readiness()

    def start(self, *, predictor_ready: bool, warmup_enabled: bool) -> None:
        self.predictor_ready = bool(predictor_ready)
        self.transition(
            RuntimeState.PRODUCT_WARMUP if warmup_enabled else RuntimeState.PRODUCT_INITIALIZING,
            "predictor_ready",
        )

    def mark_warmup_complete(self) -> None:
        if self.state is RuntimeState.PRODUCT_WARMUP:
            self.transition(RuntimeState.PRODUCT_INITIALIZING, "product_warmup_complete")

    def mark_rtsp_connected(self) -> None:
        self.rtsp_connected = True
        self._write_readiness()

    def complete_provider_window(
        self,
        *,
        product_init_ready: bool,
        product_tokens_valid: bool,
        person_provider_primed: bool,
    ) -> None:
        self.completed_provider_windows += 1
        self.product_init_ready = bool(product_init_ready)
        self.product_tokens_valid = bool(product_tokens_valid)
        self.person_provider_primed = bool(person_provider_primed)
        if self.product_init_ready and self.state is RuntimeState.PRODUCT_INITIALIZING:
            self.transition(RuntimeState.PERSON_PRIMING, "product_initialized")
        ready = all((
            self.rtsp_connected,
            self.product_init_ready,
            self.product_tokens_valid,
            self.predictor_ready,
            self.person_provider_primed,
            self.fatal_runtime_errors == 0,
            self.completed_provider_windows >= self.ready_after_completed_windows,
        ))
        if ready and self.state in {RuntimeState.PERSON_PRIMING, RuntimeState.DEGRADED}:
            self.transition(RuntimeState.READY, "readiness_conditions_satisfied")
        self._write_readiness()

    def mark_degraded(self, reason: str, *, fatal: bool = False) -> None:
        self.degraded_reason = str(reason)
        if fatal:
            self.fatal_runtime_errors += 1
        if self.state not in {RuntimeState.STOPPING, RuntimeState.STOPPED, RuntimeState.DEGRADED}:
            self.transition(RuntimeState.DEGRADED, reason)
        else:
            self._write_readiness()

    def _snapshot(self) -> dict[str, object]:
        return {
            "runtime_state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "ready_at": None if self.ready_at is None else self.ready_at.isoformat(),
            "startup_to_ready_seconds": self.startup_to_ready_seconds,
            "ready_after_completed_windows": self.ready_after_completed_windows,
            "completed_provider_windows": self.completed_provider_windows,
            "rtsp_connected": self.rtsp_connected,
            "product_init_ready": self.product_init_ready,
            "product_tokens_valid": self.product_tokens_valid,
            "predictor_ready": self.predictor_ready,
            "person_provider_primed": self.person_provider_primed,
            "fatal_runtime_errors": self.fatal_runtime_errors,
            "degraded_reason": self.degraded_reason,
            "stage11_entered": False,
        }

    def _write_readiness(self) -> None:
        (self.output_dir / "readiness.json").write_text(
            json.dumps(self._snapshot(), ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def snapshot(self) -> dict[str, object]:
        return self._snapshot()

    def close(self) -> None:
        if self.state is not RuntimeState.STOPPED:
            if self.state is not RuntimeState.STOPPING:
                self.transition(RuntimeState.STOPPING, "runtime_close")
            self.transition(RuntimeState.STOPPED, "runtime_closed")
        self._write_readiness()
        self._writer.close()


PERSON_PROFILE_FIELDS = (
    "person_session_start_ms",
    "person_grounding_ms",
    "person_regrounding_ms",
    "person_global_association_ms",
    "person_propagation_ms",
    "person_output_conversion_ms",
    "person_session_close_ms",
    "person_other_ms",
    "person_unattributed_ms",
)


def finalize_person_window_profile(
    payload: Mapping[str, object],
    *,
    person_inference_ms: float,
    external_process_ms: float,
) -> dict[str, object]:
    result = dict(payload)
    result["person_inference_ms"] = max(0.0, float(person_inference_ms))
    external_accounted = sum(
        float(result.get(field, 0.0) or 0.0)
        for field in (
            "person_session_start_ms",
            "person_grounding_ms",
            "person_global_association_ms",
            "person_propagation_ms",
            "person_output_conversion_ms",
        )
    )
    result["person_other_ms"] = max(0.0, float(external_process_ms) - external_accounted)
    accounted = sum(
        float(result.get(field, 0.0) or 0.0)
        for field in PERSON_PROFILE_FIELDS
        if field not in {"person_regrounding_ms", "person_unattributed_ms"}
    )
    result["person_unattributed_ms"] = max(0.0, float(person_inference_ms) - accounted)
    result["person_accounted_ratio"] = (
        min(1.0, accounted / float(person_inference_ms))
        if person_inference_ms > 0.0
        else 0.0
    )
    return result


class PersonLatencyProfileRecorder:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._writer = _JsonlWriter(self.output_dir / "person_window_profile.jsonl")
        self._records: list[dict[str, object]] = []
        self._summary: Optional[dict[str, object]] = None

    def record(self, payload: Mapping[str, object]) -> None:
        row = dict(payload)
        self._records.append(row)
        self._writer.write(row)

    @staticmethod
    def _summarize(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
        return {
            field: latency_stats([float(row.get(field, 0.0) or 0.0) for row in records])
            for field in ("person_inference_ms", *PERSON_PROFILE_FIELDS)
        } | {
            "window_count": len(records),
            "track_count_total": sum(int(row.get("person_track_count", 0) or 0) for row in records),
            "grounding_call_count": sum(int(row.get("new_grounding_count", 0) or 0) for row in records),
        }

    def close(self) -> dict[str, object]:
        if self._summary is not None:
            return dict(self._summary)
        empty = [row for row in self._records if row.get("person_scene_phase") == "EMPTY_SCENE"]
        present = [row for row in self._records if row.get("person_scene_phase") == "PERSON_PRESENT"]
        ready = [row for row in self._records if row.get("runtime_state") == RuntimeState.READY.value]
        self._summary = {
            "all_run": self._summarize(self._records),
            "empty_scene": self._summarize(empty),
            "person_present": self._summarize(present),
            "ready_state": self._summarize(ready),
        }
        self._writer.close()
        (self.output_dir / "person_phase_summary.json").write_text(
            json.dumps(self._summary, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return dict(self._summary)


def monotonic_growth(values: Sequence[float], *, minimum_points: int = 5) -> bool:
    if len(values) < minimum_points:
        return False
    tail = [float(value) for value in values[-minimum_points:]]
    return all(right >= left for left, right in zip(tail, tail[1:])) and any(
        right > left for left, right in zip(tail, tail[1:])
    )


def classify_ready_root_cause(
    *,
    acceptance_passed: bool,
    all_run_e2e_p95_ms: float,
    ready_e2e_p95_ms: float,
    person_p95_ms: float,
    product_p95_ms: float,
    oldest_frame_age_p95_ms: float,
) -> str:
    if acceptance_passed:
        return "NO_LONGER_BLOCKED"
    if ready_e2e_p95_ms <= 5000.0 and all_run_e2e_p95_ms > 5000.0:
        return "STARTUP_ONLY"
    if oldest_frame_age_p95_ms >= max(person_p95_ms, product_p95_ms) and oldest_frame_age_p95_ms >= ready_e2e_p95_ms * 0.40:
        return "FRAME_AGE_DOMINANT"
    if person_p95_ms > product_p95_ms * 1.25:
        return "PERSON_DOMINANT"
    if product_p95_ms > person_p95_ms * 1.25:
        return "PRODUCT_DOMINANT"
    if person_p95_ms + product_p95_ms >= ready_e2e_p95_ms * 0.60:
        return "SERIAL_PROVIDER_DOMINANT"
    return "MIXED"


class Stage1022AcceptanceRecorder:
    """Write compact READY traces and evaluate the no-interaction hard gates."""

    def __init__(self, output_dir: str | Path, *, minimum_person_present_ready_windows: int = 20) -> None:
        if minimum_person_present_ready_windows < 1:
            raise ValueError("minimum_person_present_ready_windows must be positive")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "keyframes").mkdir(parents=True, exist_ok=True)
        self.minimum_person_present_ready_windows = int(minimum_person_present_ready_windows)
        self._writers = {
            name: _JsonlWriter(self.output_dir / f"{name}.jsonl")
            for name in (
                "provider_critical_path",
                "ready_frame_latency",
                "ready_queue_metrics",
                "person_identity_trace",
                "product_identity_trace",
                "association_trace",
                "interaction_trace",
            )
        }
        self._providers: list[dict[str, object]] = []
        self._ready_queues: list[dict[str, object]] = []
        self._ready_frames: list[dict[str, object]] = []
        self._person_frames: list[dict[str, object]] = []
        self._product_frames: list[dict[str, object]] = []
        self._events: list[dict[str, object]] = []

    def record_queue(self, payload: Mapping[str, object]) -> None:
        if payload.get("runtime_state") != RuntimeState.READY.value:
            return
        row = dict(payload)
        self._ready_queues.append(row)
        self._writers["ready_queue_metrics"].write(row)

    def record_provider(self, payload: Mapping[str, object]) -> None:
        row = dict(payload)
        row["serialized_provider_ms"] = (
            float(row.get("person_inference_ms", 0.0) or 0.0)
            + float(row.get("product_inference_ms", 0.0) or 0.0)
        )
        row["downstream_ms"] = sum(
            float(row.get(field, 0.0) or 0.0)
            for field in ("association_ms", "interaction_ms", "overlay_ms", "event_emit_ms", "frame_result_write_ms", "keyframe_write_ms")
        )
        self._providers.append(row)
        self._writers["provider_critical_path"].write(row)

    def record_frame(self, latency: Mapping[str, object], result: Mapping[str, object]) -> None:
        state = str(latency.get("runtime_state", ""))
        if state == RuntimeState.READY.value:
            row = dict(latency)
            self._ready_frames.append(row)
            self._writers["ready_frame_latency"].write(row)
        base = {
            "runtime_state": state,
            "source_seq": result.get("source_seq"),
            "timestamp": result.get("timestamp"),
        }
        persons = list(result.get("persons", ()))
        person_row = {**base, "persons": persons, "person_track_count": len(persons)}
        self._person_frames.append(person_row)
        self._writers["person_identity_trace"].write(person_row)
        products = list(result.get("products", ()))
        product_row = {**base, "products": products, "product_track_count": len(products)}
        self._product_frames.append(product_row)
        self._writers["product_identity_trace"].write(product_row)
        for association in result.get("associations", ()):
            self._writers["association_trace"].write({**base, **dict(association)})
        for interaction in result.get("interactions", ()):
            self._writers["interaction_trace"].write({**base, **dict(interaction)})
        self._events.extend(dict(event) for event in result.get("events", ()))

    @staticmethod
    def _provider_stats(records: Sequence[Mapping[str, object]], field: str) -> dict[str, float | int]:
        return latency_stats([float(row.get(field, 0.0) or 0.0) for row in records])

    def close(
        self,
        *,
        performance_summary: Mapping[str, object],
        runtime_summary: Mapping[str, object],
        readiness: Mapping[str, object],
    ) -> dict[str, object]:
        for writer in self._writers.values():
            writer.close()
        ready_providers = [row for row in self._providers if row.get("runtime_state") == RuntimeState.READY.value]
        person_present_ready = [row for row in ready_providers if int(row.get("person_track_count", 0) or 0) > 0]
        ready_person_frames = [row for row in self._person_frames if row.get("runtime_state") == RuntimeState.READY.value]
        seen_frames = [row for row in ready_person_frames if row["persons"]]
        primary_ids = [int(row["persons"][0]["track_id"]) for row in seen_frames]
        primary_tokens = [str(row["persons"][0]["track_token"]) for row in seen_frames]
        observed_ids = [
            int(person["track_id"])
            for row in seen_frames
            for person in row["persons"]
        ]
        observed_tokens = [
            str(person["track_token"])
            for row in seen_frames
            for person in row["persons"]
        ]
        person_switch_count = sum(left != right for left, right in zip(primary_tokens, primary_tokens[1:]))
        duplicate_person_tracks = sum(
            len(ids) != len(set(ids))
            for ids in ([int(person["track_id"]) for person in row["persons"]] for row in ready_person_frames)
        )
        unexpected_person_lost = 0
        if seen_frames:
            first_seen = ready_person_frames.index(seen_frames[0])
            last_seen = len(ready_person_frames) - 1 - ready_person_frames[::-1].index(seen_frames[-1])
            unexpected_person_lost = sum(not row["persons"] for row in ready_person_frames[first_seen:last_seen + 1])

        product_baseline: dict[str, tuple[int, str]] = {}
        product_id_swap_count = 0
        product_token_change_count = 0
        unexpected_product_lost = 0
        for row in self._product_frames:
            if row.get("runtime_state") != RuntimeState.READY.value:
                continue
            for product in row["products"]:
                key = str(product["product_key"])
                identity = (int(product["track_id"]), str(product["track_token"]))
                baseline = product_baseline.setdefault(key, identity)
                product_id_swap_count += identity[0] != baseline[0]
                product_token_change_count += identity[1] != baseline[1]
                unexpected_product_lost += str(product.get("track_status")) == "LOST"

        false_pick_up = sum(str(event.get("event_type")) == "PICK_UP" for event in self._events)
        false_put_down = sum(str(event.get("event_type")) == "PUT_DOWN" for event in self._events)
        queue_growth = monotonic_growth([float(row.get("queue_depth_after", 0.0) or 0.0) for row in self._ready_queues])
        newest_age_growth = monotonic_growth([float(row.get("newest_frame_age_ms", 0.0) or 0.0) for row in self._ready_queues])
        pipeline_growth = monotonic_growth([float(row.get("pipeline_lag_ms", 0.0) or 0.0) for row in self._ready_frames])

        ready_metrics = performance_summary.get("ready_state_metrics", {})
        all_metrics = performance_summary.get("all_run_metrics", performance_summary)
        ready_metrics = ready_metrics if isinstance(ready_metrics, Mapping) else {}
        all_metrics = all_metrics if isinstance(all_metrics, Mapping) else {}
        ready_e2e = ready_metrics.get("pipeline_lag_ms", {})
        all_e2e = all_metrics.get("pipeline_lag_ms", {})
        ready_e2e = ready_e2e if isinstance(ready_e2e, Mapping) else {}
        all_e2e = all_e2e if isinstance(all_e2e, Mapping) else {}
        ready_person = self._provider_stats(ready_providers, "person_inference_ms")
        ready_product = self._provider_stats(ready_providers, "product_inference_ms")
        ready_serialized = self._provider_stats(ready_providers, "serialized_provider_ms")
        oldest = ready_metrics.get("oldest_frame_age_ms", {})
        newest = ready_metrics.get("newest_frame_age_ms", {})
        oldest = oldest if isinstance(oldest, Mapping) else {}
        newest = newest if isinstance(newest, Mapping) else {}
        runtime_errors = int(runtime_summary.get("runtime_errors", 0) or 0)
        person_scene_valid = len(person_present_ready) >= self.minimum_person_present_ready_windows
        runtime_reached_ready = readiness.get("ready_at") is not None
        ready_gate = (
            runtime_reached_ready
            and readiness.get("runtime_state") in {RuntimeState.READY.value, RuntimeState.STOPPING.value, RuntimeState.STOPPED.value}
            and float(ready_e2e.get("p50", 0.0) or 0.0) <= 4000.0
            and float(ready_e2e.get("p95", 0.0) or 0.0) <= 5000.0
            and runtime_errors == 0
            and not queue_growth
            and not newest_age_growth
            and not pipeline_growth
            and person_switch_count == 0
            and duplicate_person_tracks == 0
            and unexpected_person_lost == 0
            and product_id_swap_count == 0
            and product_token_change_count == 0
            and unexpected_product_lost == 0
            and false_pick_up == 0
            and false_put_down == 0
            and person_scene_valid
        )
        root_cause = classify_ready_root_cause(
            acceptance_passed=ready_gate,
            all_run_e2e_p95_ms=float(all_e2e.get("p95", 0.0) or 0.0),
            ready_e2e_p95_ms=float(ready_e2e.get("p95", 0.0) or 0.0),
            person_p95_ms=float(ready_person["p95"]),
            product_p95_ms=float(ready_product["p95"]),
            oldest_frame_age_p95_ms=float(oldest.get("p95", 0.0) or 0.0),
        )
        summary = {
            "scenario": "PERSON_PRESENT_NO_INTERACTION",
            "person_present_test": "RUN" if person_scene_valid else "NOT RUN",
            "person_present_ready_window_count": len(person_present_ready),
            "minimum_person_present_ready_windows": self.minimum_person_present_ready_windows,
            "operator_person_track_id": primary_ids[0] if len(set(primary_ids)) == 1 and primary_ids else None,
            "operator_person_track_token": primary_tokens[0] if len(set(primary_tokens)) == 1 and primary_tokens else None,
            "first_person_seen_timestamp": None if not seen_frames else seen_frames[0]["timestamp"],
            "person_global_ids": sorted(set(observed_ids)),
            "person_track_tokens": sorted(set(observed_tokens)),
            "person_global_id_switch_count": person_switch_count,
            "duplicate_person_track_count": duplicate_person_tracks,
            "unexpected_person_lost_count": unexpected_person_lost,
            "product_id_swap_count": product_id_swap_count,
            "product_token_change_count": product_token_change_count,
            "unexpected_product_lost_count": unexpected_product_lost,
            "false_pick_up_count": false_pick_up,
            "false_put_down_count": false_put_down,
            "queue_growth": queue_growth,
            "newest_frame_age_growth": newest_age_growth,
            "pipeline_lag_growth": pipeline_growth,
            "ready_person_ms": ready_person,
            "ready_product_ms": ready_product,
            "ready_serialized_provider_ms": ready_serialized,
            "all_run_e2e_ms": dict(all_e2e),
            "ready_e2e_ms": dict(ready_e2e),
            "ready_oldest_frame_age_ms": dict(oldest),
            "ready_newest_frame_age_ms": dict(newest),
            "runtime_errors": runtime_errors,
            "runtime_ready": runtime_reached_ready,
            "startup_to_ready_seconds": readiness.get("startup_to_ready_seconds"),
            "root_cause_classification": root_cause,
            "stage10_2_performance_closure": "PASS" if ready_gate else "FAIL",
            "allow_stage10_3_af": bool(ready_gate),
            "stage11_entered": False,
        }
        (self.output_dir / "stage10_2_2_acceptance_summary.json").write_text(
            json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary
