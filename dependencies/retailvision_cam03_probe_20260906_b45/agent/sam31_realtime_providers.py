"""Stage 10 adapters for the frozen external SAM3.1 rolling-window runtimes."""

from __future__ import annotations

import gc
import importlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from agent.person_product_association import ProductAssociationObservation
from agent.sam31_live_behavior import Sam31GlobalTrack
from agent.sam31_product_tracking import (
    evaluate_candidate,
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplayState,
    ProductDisplaySurface,
    ProductTrackStatus,
)
from agent.stage10_demo import (
    JsonlMetricsWriter,
    ProviderContractError,
    ProviderFrameObservations,
    ProviderInitializationError,
    ProductInitializationPending,
    ProductInitializationTimeout,
    RealtimeFrameContext,
    Stage10ProductObservation,
    classify_product_candidates,
    render_product_preflight_surface_overlay,
)
from agent.stage10_product_profile import (
    PRODUCT_PROFILE_FIELDS,
    ProductLatencyProfileRecorder,
    cuda_memory_snapshot,
    finalize_product_window_profile,
)
from agent.stage10_runtime_readiness import (
    PersonLatencyProfileRecorder,
    RuntimeReadinessController,
    RuntimeState,
    finalize_person_window_profile,
)


WINDOW_FRAMES = 12
OVERLAP_FRAMES = 4
FRESH_FRAMES = WINDOW_FRAMES - OVERLAP_FRAMES
PRODUCT_KEYS = ("smart_toy_a", "smart_toy_b")


def _prime_sam_propagation_cache(
    predictor: object,
    session_id: object,
    frame_count: int,
) -> None:
    """Keep instance-only SAM2 outputs visible through Multiplex postprocess.

    The external SAM3.1 wrapper emits an empty result when a frame has no
    ``cached_frame_outputs`` entry before refined instance masks are applied.
    Rolling-window instance prompts therefore need empty cache keys; no masks
    or detections are injected here.
    """
    sessions = getattr(predictor, "_all_inference_states", {})
    session = sessions.get(str(session_id)) if isinstance(sessions, Mapping) else None
    state = session.get("state", {}) if isinstance(session, Mapping) else None
    cache = state.get("cached_frame_outputs") if isinstance(state, Mapping) else None
    if not isinstance(cache, dict):
        return
    for frame_index in range(int(frame_count)):
        cache.setdefault(frame_index, {})


class _PersonCapturePublisher:
    def __init__(self) -> None:
        self.frames: dict[int, tuple[Sam31GlobalTrack, ...]] = {}

    def publish(self, item: Any, tracks: Sequence[object]) -> None:
        source_seq = int(item.source_seq)
        if source_seq in self.frames:
            raise ProviderContractError(
                f"Person runtime published duplicate source_seq {source_seq}"
            )
        normalized = []
        for track in tracks:
            if isinstance(track, Sam31GlobalTrack):
                normalized.append(track)
            elif isinstance(track, Mapping):
                normalized.append(Sam31GlobalTrack.from_mapping(track))
            else:
                raise ProviderContractError(
                    f"Person runtime returned unsupported track {type(track).__name__}"
                )
        self.frames[source_seq] = tuple(normalized)


class Sam31RealtimePersonProvider:
    """Capture fresh Global Track batches from the frozen Stage 5.1.1 runtime."""

    def __init__(
        self,
        *,
        base_module: Any,
        predictor: object,
        profile_recorder: Optional[PersonLatencyProfileRecorder] = None,
    ) -> None:
        self.base = base_module
        self.predictor = predictor
        self.window_no = 0
        self.old_overlap_bank: dict[int, dict[int, np.ndarray]] = {}
        self.global_tracks: dict[int, object] = {}
        self.next_global_id = 1
        self.empty_scene_streak = 0
        self.empty_scene_last_poll_wall_time: Optional[float] = None
        self.last_window_metrics: dict[str, object] = {}
        self.profile_recorder = profile_recorder
        self._pending_profile: Optional[dict[str, object]] = None

    def process_window(
        self,
        contexts: Sequence[RealtimeFrameContext],
        *,
        terminal: bool = False,
    ) -> Mapping[int, tuple[Sam31GlobalTrack, ...]]:
        process_started = time.perf_counter()
        current_window_no = self.window_no
        contexts = _validate_contexts(contexts, window_no=current_window_no, terminal=terminal)
        items = _sampled_frames(self.base, contexts)
        publisher = _PersonCapturePublisher()
        session_ids: list[object] = []
        original_start = self.base.start_session
        original_masks = getattr(self.base, "masks_from_outputs", None)
        original_association = getattr(self.base, "associate_global_tracks", None)
        original_propagate = getattr(self.base, "propagate_forward", None)
        output_conversion_ms = 0.0
        global_association_ms = 0.0
        propagation_ms = 0.0
        propagation_response_count = 0
        expected_old_track_ids = {
            int(track_id)
            for frame_tracks in self.old_overlap_bank.values()
            for track_id in frame_tracks
        }
        input_empty_scene_streak = self.empty_scene_streak

        def recording_start(predictor: object) -> object:
            session_id = original_start(predictor)
            session_ids.append(session_id)
            return session_id

        def recording_masks(*args: object, **kwargs: object) -> object:
            nonlocal output_conversion_ms
            if not callable(original_masks):
                raise ProviderContractError("Person output converter is unavailable")
            started = time.perf_counter()
            try:
                return original_masks(*args, **kwargs)
            finally:
                output_conversion_ms += max(0.0, (time.perf_counter() - started) * 1000.0)

        def recording_association(*args: object, **kwargs: object) -> object:
            nonlocal global_association_ms
            if not callable(original_association):
                raise ProviderContractError("Person Global Association helper is unavailable")
            started = time.perf_counter()
            try:
                return original_association(*args, **kwargs)
            finally:
                global_association_ms += max(0.0, (time.perf_counter() - started) * 1000.0)

        def recording_propagate(*args: object, **kwargs: object):
            nonlocal propagation_ms, propagation_response_count
            if not callable(original_propagate):
                raise ProviderContractError("Person propagation helper is unavailable")
            iterator = iter(original_propagate(*args, **kwargs))
            while True:
                started = time.perf_counter()
                try:
                    response = next(iterator)
                except StopIteration:
                    propagation_ms += max(0.0, (time.perf_counter() - started) * 1000.0)
                    return
                propagation_ms += max(0.0, (time.perf_counter() - started) * 1000.0)
                propagation_response_count += 1
                yield response

        self.base.start_session = recording_start
        if callable(original_masks):
            self.base.masks_from_outputs = recording_masks
        if callable(original_association):
            self.base.associate_global_tracks = recording_association
        if callable(original_propagate):
            self.base.propagate_forward = recording_propagate
        external_started = time.perf_counter()
        external_process_ms = 0.0
        session_close_ms = 0.0
        try:
            result = self.base.run_window(
                predictor=self.predictor,
                window_items=list(items),
                old_overlap_bank=self.old_overlap_bank,
                global_tracks=self.global_tracks,
                next_global_id=self.next_global_id,
                window_no=self.window_no,
                empty_scene_streak=self.empty_scene_streak,
                empty_scene_last_poll_wall_time=self.empty_scene_last_poll_wall_time,
                observation_rows=[],
                association_rows=[],
                behavior_publisher=publisher,
            )
        finally:
            external_process_ms = max(0.0, (time.perf_counter() - external_started) * 1000.0)
            self.base.start_session = original_start
            if callable(original_masks):
                self.base.masks_from_outputs = original_masks
            if callable(original_association):
                self.base.associate_global_tracks = original_association
            if callable(original_propagate):
                self.base.propagate_forward = original_propagate
            for session_id in session_ids:
                close_started = time.perf_counter()
                self.base.close_session(self.predictor, session_id)
                session_close_ms += max(0.0, (time.perf_counter() - close_started) * 1000.0)

        self.old_overlap_bank = dict(result["new_overlap_bank"])
        self.next_global_id = int(result["next_global_id"])
        self.empty_scene_streak = int(result["next_empty_scene_streak"])
        self.empty_scene_last_poll_wall_time = result[
            "next_empty_scene_last_poll_wall_time"
        ]
        fresh = contexts if current_window_no == 0 else contexts[OVERLAP_FRAMES:]
        expected = {item.source_seq for item in fresh}
        if set(publisher.frames) != expected:
            raise ProviderContractError(
                "Person runtime fresh-frame mapping mismatch: "
                f"expected={sorted(expected)} actual={sorted(publisher.frames)}"
            )
        unique_track_ids = {
            int(track.global_track_id)
            for item in fresh
            for track in publisher.frames[item.source_seq]
        }
        grounding = result.get("grounding", {})
        grounding = grounding if isinstance(grounding, Mapping) else {}
        attempts = grounding.get("attempts", ())
        attempts = attempts if isinstance(attempts, Sequence) else ()
        association = result.get("association", {})
        association = association if isinstance(association, Mapping) else {}
        person_inference_ms = max(
            0.0,
            (time.perf_counter() - process_started) * 1000.0,
        )
        self._pending_profile = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "window_no": current_window_no,
            "timestamp": contexts[-1].captured_at.isoformat(),
            "source_seq_start": contexts[0].source_seq,
            "source_seq_end": contexts[-1].source_seq,
            "person_scene_phase": "PERSON_PRESENT" if unique_track_ids else "EMPTY_SCENE",
            "person_track_count": len(unique_track_ids),
            "person_global_track_ids": sorted(unique_track_ids),
            "expected_old_track_count": len(expected_old_track_ids),
            "new_grounding_count": int(grounding.get("text_calls", 0) or 0),
            "empty_scene_streak": input_empty_scene_streak,
            "next_empty_scene_streak": self.empty_scene_streak,
            "empty_scene_mode": (
                len(expected_old_track_ids) == 0
                and input_empty_scene_streak >= int(getattr(self.base, "EMPTY_SCENE_ENTER_AFTER_WINDOWS", 2))
            ),
            "grounding_skipped_due_to_empty_throttle": bool(grounding.get("throttled", False)),
            "association_match_count": len(tuple(association.get("matches", ()))),
            "person_session_start_ms": max(0.0, float(result.get("session_init_sec", 0.0) or 0.0) * 1000.0),
            "person_grounding_ms": max(0.0, float(grounding.get("text_sec", 0.0) or 0.0) * 1000.0),
            "person_regrounding_ms": sum(
                max(0.0, float(attempt.get("sec", 0.0) or 0.0) * 1000.0)
                for attempt in attempts[1:]
                if isinstance(attempt, Mapping)
            ),
            "person_global_association_ms": global_association_ms,
            "person_propagation_ms": propagation_ms,
            "person_propagation_response_count": propagation_response_count,
            "person_output_conversion_ms": output_conversion_ms,
            "person_session_close_ms": session_close_ms,
            "external_process_ms": external_process_ms,
        }
        self.last_window_metrics = {
            "person_session_count": len(session_ids),
            "person_track_count": len(unique_track_ids),
            "person_inference_ms": person_inference_ms,
            **self._pending_profile,
        }
        self.window_no += 1
        return {item.source_seq: publisher.frames[item.source_seq] for item in fresh}

    def finalize_window_profile(
        self,
        *,
        person_inference_ms: float,
        runtime_state: str,
    ) -> dict[str, object]:
        if self._pending_profile is None:
            return {}
        pending = dict(self._pending_profile)
        pending["runtime_state"] = str(runtime_state)
        finalized = finalize_person_window_profile(
            pending,
            person_inference_ms=person_inference_ms,
            external_process_ms=float(pending.get("external_process_ms", 0.0) or 0.0),
        )
        self._pending_profile = None
        self.last_window_metrics.update(finalized)
        if self.profile_recorder is not None:
            self.profile_recorder.record(finalized)
        return finalized

    def close(self) -> None:
        if self.profile_recorder is not None:
            self.profile_recorder.close()


class _ProductCaptureArtifacts:
    def __init__(self) -> None:
        self.frames: dict[int, tuple[Mapping[str, object], ...]] = {}
        self.rows: list[Mapping[str, object]] = []
        self.window_metrics: list[Mapping[str, object]] = []

    def append_frame(
        self,
        *,
        rows: Sequence[Mapping[str, object]],
        frame: np.ndarray,
        candidates: Mapping[str, ProductCandidate],
    ) -> None:
        del frame
        if not rows:
            raise ProviderContractError("Product runtime emitted an empty frame batch")
        source_seq = int(rows[0]["source_seq"])
        if source_seq in self.frames:
            raise ProviderContractError(
                f"Product runtime published duplicate source_seq {source_seq}"
            )
        if any(int(row["source_seq"]) != source_seq for row in rows):
            raise ProviderContractError("Product frame batch mixes source_seq values")
        copied_rows = []
        for row in rows:
            copied_row = dict(row)
            candidate = candidates.get(str(row["product_key"]))
            if candidate is not None:
                # Keep the actual SAM mask in the in-process diagnostic path;
                # normal runtime rows remain JSON-safe and unchanged.
                copied_row["_diagnostic_mask"] = np.asarray(candidate.mask).astype(bool)
                copied_row["_diagnostic_local_obj_id"] = int(candidate.local_obj_id)
            copied_rows.append(copied_row)
        copied = tuple(copied_rows)
        self.frames[source_seq] = copied
        self.rows.extend(copied)


class _RealtimeProductClock:
    offline_replay = False
    realtime_fresh_lag_applicable = True

    def __init__(self, started_wall: float) -> None:
        self.started_wall = float(started_wall)

    def elapsed(self, item: Any) -> float:
        return max(0.0, float(item.wall_time) - self.started_wall)

    def observed_at(self, item: Any) -> datetime:
        return datetime.fromtimestamp(float(item.wall_time), tz=timezone.utc)

    def fresh_lag(self, item: Any, finished_wall: float) -> float:
        return max(0.0, float(finished_wall) - float(item.wall_time))

    def position(self, _item: Any) -> tuple[str, str]:
        return "LIVE", "realtime"

    def video_elapsed_seconds(self, _item: Any) -> None:
        return None


class Sam31RealtimeProductProvider:
    """Capture tracked Product observations from the frozen Stage 7C.6.2 probe."""

    def __init__(
        self,
        *,
        base_module: Any,
        product_module: Any,
        predictor: object,
        config: Mapping[str, object],
        surface: ProductDisplaySurface,
        runtime: MultiProductTrackRuntime | object,
        candidate_assignments: Optional[Mapping[str, int]] = None,
        initialization_prompts: Optional[Mapping[str, Mapping[str, object]]] = None,
        selected_product_keys: Optional[Sequence[str]] = None,
        diagnostics_root: Optional[str | Path] = None,
        profile_recorder: Optional[ProductLatencyProfileRecorder] = None,
        gpu_timing_probe: bool = False,
    ) -> None:
        self.base = base_module
        self.product_module = product_module
        self.predictor = predictor
        self.config = dict(config)
        self.surface = surface
        self.runtime = runtime
        self.window_no = 0
        configured_keys = tuple(str(value) for value in config.get(
            "selected_product_keys",
            getattr(product_module, "PRODUCT_KEYS", PRODUCT_KEYS),
        ))
        self.product_keys = tuple(
            str(value) for value in (selected_product_keys or configured_keys)
        )
        configured_products = config.get("products")
        approved_keys = (
            set(str(value) for value in configured_products)
            if isinstance(configured_products, Mapping) and configured_products
            else set(str(value) for value in getattr(product_module, "PRODUCT_KEYS", ()))
        )
        if len(set(self.product_keys)) != len(self.product_keys):
            raise ProviderInitializationError("Product runtime keys must be unique")
        if set(self.product_keys) - approved_keys:
            raise ProviderInitializationError(
                "selected_product_keys must come from the approved camera products"
            )
        if len(self.product_keys) not in {2, 3, 4, 5}:
            raise ProviderInitializationError(
                "Product runtime requires the legacy two targets or three to five dense targets"
            )
        module_keys = tuple(getattr(product_module, "PRODUCT_KEYS", self.product_keys))
        if module_keys != self.product_keys:
            raise ProviderInitializationError(
                "Product SAM adapter keys do not match selected_product_keys"
            )
        self.candidate_assignments = self._validate_assignment_config(
            candidate_assignments
        )
        self.initialization_prompts = None if initialization_prompts is None else dict(initialization_prompts)
        self.state = product_module.ProbeState()
        self.grounding_guard = product_module.SingleTextGroundingPerWindow()
        self.clock: Optional[_RealtimeProductClock] = None
        self.last_window_metrics: dict[str, object] = {}
        self.initialized = False
        self.last_initialization_diagnostic: dict[str, object] = {}
        self.profile_recorder = profile_recorder
        self.gpu_timing_probe = bool(gpu_timing_probe)
        self._pending_profile: Optional[dict[str, object]] = None
        self._diagnostic_writers: dict[str, JsonlMetricsWriter] = {}
        self.preflight_root: Optional[Path] = None
        if diagnostics_root is not None:
            root = Path(diagnostics_root)
            root.mkdir(parents=True, exist_ok=True)
            self.preflight_root = root.parent
            self._diagnostic_writers = {
                name: JsonlMetricsWriter(root / f"{name}.jsonl")
                for name in (
                    "product_init_windows",
                    "grounding_diagnostics",
                    "candidate_diagnostics",
                )
            }

    def _validate_assignment_config(
        self,
        assignments: Optional[Mapping[str, int]],
    ) -> Optional[dict[str, int]]:
        if assignments is None:
            return None
        values = {str(key): int(value) for key, value in assignments.items()}
        if set(values) != set(self.product_keys):
            if self.product_keys == PRODUCT_KEYS:
                raise ProviderInitializationError(
                    "Product initialization requires both smart_toy_a and smart_toy_b"
                )
            raise ProviderInitializationError(
                "Product initialization assignments must cover every selected_product_key"
            )
        if len(set(values.values())) != len(values):
            raise ProviderInitializationError(
                "The same candidate cannot initialize multiple product_key values"
            )
        return values

    def _select_initial_assignments(
        self,
        frame: np.ndarray,
        surface: ProductDisplaySurface,
        candidates: Sequence[ProductCandidate],
        visual_initializer: Any,
    ) -> Mapping[str, ProductCandidate]:
        if self.candidate_assignments is None:
            if not callable(visual_initializer):
                raise ProviderInitializationError(
                    "Product visual initializer is unavailable"
                )
            return visual_initializer(frame, surface, candidates)
        by_id = {candidate.local_obj_id: candidate for candidate in candidates}
        missing = {
            key: candidate_id
            for key, candidate_id in self.candidate_assignments.items()
            if candidate_id not in by_id
        }
        if missing:
            raise ProductInitializationPending(
                "Configured Product candidates are missing: "
                f"{missing}; available_local_ids={sorted(by_id)}"
            )
        return {
            key: by_id[candidate_id]
            for key, candidate_id in self.candidate_assignments.items()
        }

    def process_window(
        self,
        contexts: Sequence[RealtimeFrameContext],
        *,
        terminal: bool = False,
    ) -> Mapping[int, tuple[Stage10ProductObservation, ...]]:
        process_started = time.perf_counter()
        current_window_no = self.window_no
        contexts = _validate_contexts(contexts, window_no=current_window_no, terminal=terminal)
        items = _sampled_frames(self.base, contexts)
        if self.clock is None:
            self.clock = _RealtimeProductClock(float(items[0].wall_time))
        artifacts = _ProductCaptureArtifacts()
        memory_before = cuda_memory_snapshot(self.base)
        stage_ms = {field: 0.0 for field in PRODUCT_PROFILE_FIELDS}
        external_artifact_conversion_ms = 0.0
        propagation_response_count = 0
        grounding_count = 0
        raw_grounding_object_count = 0
        observed_candidates: tuple[ProductCandidate, ...] = ()
        diagnostic_payload: Optional[dict[str, object]] = None
        grounding_pending_conversion = False
        original_grounding_run = getattr(self.grounding_guard, "run", None)
        stage7c = getattr(self.product_module, "stage7c", None)
        original_candidates_from_outputs = getattr(stage7c, "candidates_from_outputs", None)

        def _sync_for_probe() -> None:
            if not self.gpu_timing_probe:
                return
            torch = getattr(self.base, "torch", None)
            cuda = getattr(torch, "cuda", None)
            if cuda is not None and cuda.is_available():
                cuda.synchronize()

        def _timed(field: str, operation: Any) -> object:
            _sync_for_probe()
            started = time.perf_counter()
            try:
                return operation()
            finally:
                _sync_for_probe()
                stage_ms[field] += max(0.0, (time.perf_counter() - started) * 1000.0)

        def _raw_count(outputs: object) -> int:
            if not isinstance(outputs, Mapping):
                return 0
            values = outputs.get("out_obj_ids", ())
            try:
                return int(np.asarray(self.base.to_numpy(values)).reshape(-1).size)
            except Exception:
                try:
                    return len(values)  # type: ignore[arg-type]
                except Exception:
                    return 0

        def recording_candidates_from_outputs(outputs: object, *args: object, **kwargs: object):
            nonlocal raw_grounding_object_count, observed_candidates, grounding_pending_conversion, diagnostic_payload
            if not callable(original_candidates_from_outputs):
                raise ProviderContractError("Product candidate converter is unavailable")
            converted = _timed(
                "product_output_conversion_ms",
                lambda: tuple(original_candidates_from_outputs(outputs, *args, **kwargs)),
            )
            assert isinstance(converted, tuple)
            if grounding_pending_conversion:
                def evaluate_grounding_candidates() -> dict[str, object]:
                    nonlocal raw_grounding_object_count, observed_candidates
                    raw_grounding_object_count = _raw_count(outputs)
                    observed_candidates = converted
                    return classify_product_candidates(
                        candidates=converted,
                        surface=self.surface,
                        frame_width=contexts[0].frame_width,
                        frame_height=contexts[0].frame_height,
                        window_no=current_window_no,
                        source_seq_start=contexts[0].source_seq,
                        source_seq_end=contexts[-1].source_seq,
                        grounding_called=grounding_count > 0,
                        grounding_call_count=grounding_count,
                        raw_grounding_object_count=raw_grounding_object_count,
                        grounding_prompt=str(getattr(self.product_module, "TEXT_PROMPT", "plush toy")),
                    )

                evaluated = _timed(
                    "product_candidate_evaluation_ms",
                    evaluate_grounding_candidates,
                )
                assert isinstance(evaluated, dict)
                diagnostic_payload = evaluated
                grounding_pending_conversion = False
            return converted

        def recording_grounding_run(*args: object, **kwargs: object) -> object:
            nonlocal grounding_count, grounding_pending_conversion
            grounding_count += 1
            if not callable(original_grounding_run):
                raise ProviderContractError("Product grounding guard has no run method")
            grounding_pending_conversion = True
            if current_window_no == 0 and self.initialization_prompts:
                def run_confirmed_prompts() -> object:
                    from agent.sam31_real_adapter import Sam31InstancePromptAdapter

                    sessions = getattr(self.predictor, "_all_inference_states", {})
                    if not isinstance(sessions, Mapping) or not sessions:
                        raise ProviderInitializationError("SAM3 session state unavailable for confirmed initialization")
                    session_id = next(reversed(sessions))
                    adapter = Sam31InstancePromptAdapter(
                        predictor=self.predictor,
                        session_id=str(session_id),
                        frame_size=(int(contexts[0].frame_width), int(contexts[0].frame_height)),
                    )
                    obj_ids: list[int] = []
                    masks: list[np.ndarray] = []
                    probs: list[float] = []
                    for key, prompt in self.initialization_prompts.items():
                        result = adapter.add_instance_prompt(
                            frame=contexts[0].frame,
                            frame_index=0,
                            bbox=tuple(int(value) for value in prompt["bbox_xyxy"]),
                            points=tuple(tuple(int(value) for value in point) for point in prompt["points"]),
                            point_labels=tuple(int(value) for value in prompt["point_labels"]),
                            obj_id=int(prompt["obj_id"]),
                        )
                        if not result.masks:
                            raise ProductInitializationPending(
                                f"confirmed initialization returned empty Mask: {key}"
                            )
                        obj_ids.append(int(result.candidate_id))
                        masks.append(np.asarray(result.masks[0]).astype(bool))
                        probs.append(float(result.confidence))
                    return {
                        "outputs": {
                            "out_obj_ids": np.asarray(obj_ids, dtype=np.int64),
                            "out_binary_masks": np.stack(masks, axis=0),
                            "out_probs": np.asarray(probs, dtype=np.float32),
                        }
                    }

                return _timed("product_grounding_ms", run_confirmed_prompts)
            if (
                current_window_no > 0
                and self.initialization_prompts
                and self.state.overlap_references
            ):
                def run_overlap_handoff() -> object:
                    sessions = getattr(self.predictor, "_all_inference_states", {})
                    if not isinstance(sessions, Mapping) or not sessions:
                        raise ProviderInitializationError(
                            "SAM3 session state unavailable for overlap handoff"
                        )
                    session_id = str(next(reversed(sessions)))
                    from agent.sam31_real_adapter import Sam31InstancePromptAdapter

                    active_references = []
                    for key, candidate in self.state.overlap_references.items():
                        status = self.runtime.status(key)
                        if status is not None and getattr(status, "value", status) == "LOST":
                            continue
                        active_references.append((str(key), candidate))
                    if not active_references:
                        return original_grounding_run(*args, **kwargs)
                    adapter = Sam31InstancePromptAdapter(
                        predictor=self.predictor,
                        session_id=session_id,
                        frame_size=(int(contexts[0].frame_width), int(contexts[0].frame_height)),
                    )
                    obj_ids: list[int] = []
                    masks: list[np.ndarray] = []
                    probs: list[float] = []
                    for index, (_key, candidate) in enumerate(active_references):
                        cx, cy = (int(round(value)) for value in candidate.centroid)
                        result = adapter.add_instance_prompt(
                            frame=contexts[0].frame,
                            frame_index=0,
                            bbox=tuple(int(round(value)) for value in candidate.bbox),
                            points=((cx, cy),),
                            point_labels=(1,),
                            obj_id=int(candidate.local_obj_id),
                        )
                        if not result.masks:
                            continue
                        obj_ids.append(int(result.candidate_id))
                        masks.append(np.asarray(result.masks[0]).astype(bool))
                        probs.append(float(result.confidence))
                    if not obj_ids:
                        return {"outputs": {}}
                    return {"outputs": {
                        "out_obj_ids": np.asarray(obj_ids, dtype=np.int64),
                        "out_binary_masks": np.stack(masks, axis=0),
                        "out_probs": np.asarray(probs, dtype=np.float32),
                    }}

                return _timed("product_grounding_ms", run_overlap_handoff)
            return _timed(
                "product_grounding_ms",
                lambda: original_grounding_run(*args, **kwargs),
            )

        original_initializer = getattr(
            self.product_module,
            "select_initial_assignments",
            None,
        )
        session_count = 0
        original_start = getattr(self.base, "start_session", None)
        original_close = getattr(self.base, "close_session", None)
        original_propagate = getattr(self.base, "propagate_forward", None)

        def recording_start(predictor: object) -> object:
            nonlocal session_count
            assert callable(original_start)
            session_count += 1
            return _timed(
                "product_session_start_ms",
                lambda: original_start(predictor),
            )

        def recording_close(predictor: object, session_id: object) -> object:
            if not callable(original_close):
                return None
            return _timed(
                "product_session_close_ms",
                lambda: original_close(predictor, session_id),
            )

        def recording_propagate(*args: object, **kwargs: object):
            nonlocal propagation_response_count
            if not callable(original_propagate):
                raise ProviderContractError("Product propagation helper is unavailable")
            if len(args) >= 2:
                _prime_sam_propagation_cache(
                    self.predictor,
                    args[1],
                    len(items),
                )
            iterator = iter(original_propagate(*args, **kwargs))
            while True:
                _sync_for_probe()
                started = time.perf_counter()
                try:
                    response = next(iterator)
                except StopIteration:
                    _sync_for_probe()
                    stage_ms["product_propagation_ms"] += max(
                        0.0,
                        (time.perf_counter() - started) * 1000.0,
                    )
                    return
                _sync_for_probe()
                stage_ms["product_propagation_ms"] += max(
                    0.0,
                    (time.perf_counter() - started) * 1000.0,
                )
                propagation_response_count += 1
                yield response

        def initializer(frame: np.ndarray, surface: ProductDisplaySurface, candidates: Sequence[ProductCandidate]):
            nonlocal diagnostic_payload
            try:
                assignments = _timed(
                    "product_candidate_evaluation_ms",
                    lambda: self._select_initial_assignments(
                        frame,
                        surface,
                        candidates,
                        original_initializer,
                    ),
                )
                assert isinstance(assignments, Mapping)
            except ProductInitializationPending:
                if diagnostic_payload is not None:
                    diagnostic_payload["final_initialization_candidate_count"] = 0
                raise
            except RuntimeError as error:
                if diagnostic_payload is not None:
                    diagnostic_payload["final_initialization_candidate_count"] = 0
                if "at least two credible candidates" in str(error):
                    raise ProductInitializationPending(
                        f"WAITING_FOR_PRODUCT_INIT: {error}"
                    ) from error
                raise
            if diagnostic_payload is not None:
                diagnostic_payload["final_initialization_candidate_count"] = len(assignments)
            if self.preflight_root is not None:
                def write_initialization_artifacts() -> None:
                    success: dict[str, object] = {"window_no": current_window_no}
                    for key, candidate in assignments.items():
                        evidence = evaluate_candidate(
                            candidate,
                            self.surface,
                            frame_width=frame.shape[1],
                            frame_height=frame.shape[0],
                        )
                        success[key] = {
                            "local_obj_id": candidate.local_obj_id,
                            "bbox": list(candidate.bbox),
                            "centroid": list(candidate.centroid),
                            "surface_overlap_ratio": None if evidence is None else evidence.surface_overlap_ratio,
                        }
                    (self.preflight_root / "product_init_success.json").write_text(
                        json.dumps(success, ensure_ascii=True, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    try:
                        render_product_preflight_surface_overlay(
                            frame,
                            self.surface,
                            self.preflight_root / "overlays" / "product_init_success.jpg",
                            candidates=tuple(assignments.values()),
                            assignments=assignments,
                        )
                    except Exception as error:  # Diagnostics must not fail inference.
                        success["overlay_error"] = str(error)
                        (self.preflight_root / "product_init_success.json").write_text(
                            json.dumps(success, ensure_ascii=True, indent=2, sort_keys=True),
                            encoding="utf-8",
                        )

                _timed("product_artifact_conversion_ms", write_initialization_artifacts)
            return assignments

        runtime_overrides: list[tuple[str, bool, object]] = []
        runtime_dict = getattr(self.runtime, "__dict__", None)
        if isinstance(runtime_dict, dict):
            for method_name in ("initialize_assignments", "process_window", "observe_assignments"):
                original_method = getattr(self.runtime, method_name, None)
                if not callable(original_method):
                    continue
                had_instance_value = method_name in runtime_dict
                old_instance_value = runtime_dict.get(method_name)

                def timed_runtime_call(
                    *args: object,
                    _operation: Any = original_method,
                    **kwargs: object,
                ) -> object:
                    return _timed(
                        "product_track_association_ms",
                        lambda: _operation(*args, **kwargs),
                    )

                setattr(self.runtime, method_name, timed_runtime_call)
                runtime_overrides.append((method_name, had_instance_value, old_instance_value))

        original_append_frame = artifacts.append_frame

        def recording_append_frame(*args: object, **kwargs: object) -> object:
            nonlocal external_artifact_conversion_ms
            started = time.perf_counter()
            try:
                return original_append_frame(*args, **kwargs)
            finally:
                elapsed = max(0.0, (time.perf_counter() - started) * 1000.0)
                external_artifact_conversion_ms += elapsed
                stage_ms["product_artifact_conversion_ms"] += elapsed

        artifacts.append_frame = recording_append_frame  # type: ignore[method-assign]

        self.product_module.select_initial_assignments = initializer
        if stage7c is not None and callable(original_candidates_from_outputs):
            stage7c.candidates_from_outputs = recording_candidates_from_outputs
        if callable(original_grounding_run):
            self.grounding_guard.run = recording_grounding_run
        if callable(original_start):
            self.base.start_session = recording_start
        if callable(original_close):
            self.base.close_session = recording_close
        if callable(original_propagate):
            self.base.propagate_forward = recording_propagate
        external_process_started = time.perf_counter()
        external_process_ms = 0.0
        try:
            self.product_module.process_window(
                predictor=self.predictor,
                window_items=list(items),
                window_no=self.window_no,
                clock=self.clock,
                grounding_guard=self.grounding_guard,
                config=self.config,
                surface=self.surface,
                runtime=self.runtime,
                state=self.state,
                artifacts=artifacts,
            )
        except ProductInitializationPending:
            self.window_no += 1
            raise
        except ProviderInitializationError:
            raise
        except (RuntimeError, ValueError) as error:
            if self.window_no == 0:
                raise ProviderInitializationError(
                    f"Product initialization failed: {error}"
                ) from error
            self.window_no += 1
            raise
        finally:
            external_process_ms = max(
                0.0,
                (time.perf_counter() - external_process_started) * 1000.0,
            )
            if callable(original_grounding_run):
                self.grounding_guard.run = original_grounding_run
            if stage7c is not None and callable(original_candidates_from_outputs):
                stage7c.candidates_from_outputs = original_candidates_from_outputs
            if callable(original_start):
                self.base.start_session = original_start
            if callable(original_close):
                self.base.close_session = original_close
            if callable(original_propagate):
                self.base.propagate_forward = original_propagate
            artifacts.append_frame = original_append_frame  # type: ignore[method-assign]
            for method_name, had_instance_value, old_instance_value in runtime_overrides:
                if had_instance_value:
                    setattr(self.runtime, method_name, old_instance_value)
                else:
                    delattr(self.runtime, method_name)
            if original_initializer is None:
                delattr(self.product_module, "select_initial_assignments")
            else:
                self.product_module.select_initial_assignments = original_initializer
            if diagnostic_payload is None:
                diagnostic_payload = classify_product_candidates(
                    candidates=observed_candidates,
                    surface=self.surface,
                    frame_width=contexts[0].frame_width,
                    frame_height=contexts[0].frame_height,
                    window_no=current_window_no,
                    source_seq_start=contexts[0].source_seq,
                    source_seq_end=contexts[-1].source_seq,
                    grounding_called=grounding_count > 0,
                    grounding_call_count=grounding_count,
                    raw_grounding_object_count=raw_grounding_object_count,
                    final_initialization_candidate_count=0,
                    grounding_prompt=str(getattr(self.product_module, "TEXT_PROMPT", "plush toy")),
                )
            diagnostic_payload["grounding_returned_candidate_count"] = len(observed_candidates)
            diagnostic_payload["grounding_call_count"] = grounding_count
            self.last_initialization_diagnostic = dict(diagnostic_payload)
            if self.preflight_root is not None and contexts[0].frame is not None:
                try:
                    render_product_preflight_surface_overlay(
                        contexts[0].frame,
                        self.surface,
                        self.preflight_root / "overlays" / "product_preflight_surface_overlay.jpg",
                        candidates=observed_candidates,
                    )
                except Exception as error:  # Diagnostics must not fail inference.
                    self.last_initialization_diagnostic["overlay_error"] = str(error)
            for writer_name, writer in self._diagnostic_writers.items():
                if writer_name == "candidate_diagnostics":
                    for candidate in diagnostic_payload.get("candidates", ()):
                        writer.write({
                            "window_no": diagnostic_payload["window_no"],
                            "source_seq_start": diagnostic_payload["source_seq_start"],
                            "source_seq_end": diagnostic_payload["source_seq_end"],
                            **dict(candidate),
                        })
                else:
                    writer.write(diagnostic_payload)

        fresh = contexts if current_window_no == 0 else contexts[OVERLAP_FRAMES:]
        expected = {item.source_seq for item in fresh}
        if set(artifacts.frames) != expected:
            raise ProviderContractError(
                "Product runtime fresh-frame mapping mismatch: "
                f"expected={sorted(expected)} actual={sorted(artifacts.frames)}"
            )
        conversion_started = time.perf_counter()
        converted = {
            context.source_seq: _product_rows(
                artifacts.frames[context.source_seq],
                context=context,
                product_keys=self.product_keys,
            )
            for context in fresh
        }
        stage_ms["product_artifact_conversion_ms"] += max(
            0.0,
            (time.perf_counter() - conversion_started) * 1000.0,
        )
        memory_after = cuda_memory_snapshot(self.base)
        product_inference_ms = max(
            0.0,
            (time.perf_counter() - process_started) * 1000.0,
        )
        unique_tracks = {
            item.observation.product_key
            for values in converted.values()
            for item in values
        }
        self._pending_profile = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "window_no": current_window_no,
            "timestamp": contexts[-1].captured_at.isoformat(),
            "source_seq_start": contexts[0].source_seq,
            "source_seq_end": contexts[-1].source_seq,
            "product_phase": "INITIALIZATION" if current_window_no == 0 else "STEADY_STATE",
            "fresh_frame_count": len(fresh),
            "overlap_frame_count": 0 if current_window_no == 0 else OVERLAP_FRAMES,
            "propagation_response_count": propagation_response_count,
            "propagation_per_frame_ms": (
                stage_ms["product_propagation_ms"] / propagation_response_count
                if propagation_response_count
                else 0.0
            ),
            "grounding_candidate_count": len(observed_candidates),
            "candidate_count": len(observed_candidates),
            "product_track_count": len(unique_tracks),
            "allocated_before_mb": memory_before["allocated_mb"],
            "reserved_before_mb": memory_before["reserved_mb"],
            "allocated_after_mb": memory_after["allocated_mb"],
            "reserved_after_mb": memory_after["reserved_mb"],
            "peak_allocated_mb": memory_after["peak_allocated_mb"],
            "cuda_memory_delta_mb": memory_after["allocated_mb"] - memory_before["allocated_mb"],
            "gpu_timing_synchronized": self.gpu_timing_probe,
            "external_process_ms": external_process_ms,
            "external_artifact_conversion_ms": external_artifact_conversion_ms,
            **stage_ms,
        }
        self.last_window_metrics = {
            "product_session_count": session_count,
            "grounding_count": grounding_count,
            "product_track_count": len(unique_tracks),
            "product_inference_ms": product_inference_ms,
            **stage_ms,
        }
        self.initialized = True
        self.window_no += 1
        return converted

    def finalize_window_profile(
        self,
        *,
        product_inference_ms: float,
        product_materialize_ms: float,
        person_inference_ms: float,
    ) -> dict[str, object]:
        if self._pending_profile is None:
            return {}
        pending = dict(self._pending_profile)
        pending["product_materialize_ms"] = max(0.0, float(product_materialize_ms))
        pending["person_inference_ms"] = max(0.0, float(person_inference_ms))
        finalized = finalize_product_window_profile(
            pending,
            product_inference_ms=product_inference_ms,
            external_process_ms=float(pending.get("external_process_ms", 0.0) or 0.0),
            external_artifact_conversion_ms=float(
                pending.get("external_artifact_conversion_ms", 0.0) or 0.0
            ),
        )
        finalized.pop("external_artifact_conversion_ms", None)
        self._pending_profile = None
        self.last_window_metrics.update(finalized)
        if self.profile_recorder is not None:
            self.profile_recorder.record(finalized)
        return finalized

    def set_warmup_profile(self, payload: Mapping[str, object]) -> None:
        if self.profile_recorder is not None:
            self.profile_recorder.set_warmup(payload)

    def close(self) -> None:
        for writer in self._diagnostic_writers.values():
            writer.close()
        if self.profile_recorder is not None:
            self.profile_recorder.close()


class Sam31RealtimeProviderBundle:
    """Run Person and Product SAM sessions sequentially over one shared window."""

    def __init__(
        self,
        *,
        runtime_instance_id: str,
        output_dir: str | Path,
        sam_runtime_root: str | Path = r"D:\GJJ\work\sam3",
        candidate_assignments: Optional[Mapping[str, int]] = None,
        selected_product_keys: Optional[Sequence[str]] = None,
        product_config: Optional[Mapping[str, object]] = None,
        product_surface: Optional[ProductDisplaySurface] = None,
        display_evidence_mode: str = "mask_surface_overlap",
        support_contact_points: Optional[Mapping[str, tuple[float, float]]] = None,
        support_contact_tolerance: float = 0.08,
        base_module: Optional[ModuleType] = None,
        product_module: Optional[ModuleType] = None,
        product_tracking_module: Optional[ModuleType] = None,
        predictor: Optional[object] = None,
        person_provider: Optional[Sam31RealtimePersonProvider] = None,
        product_provider: Optional[Sam31RealtimeProductProvider] = None,
        initialization_prompts: Optional[Mapping[str, Mapping[str, object]]] = None,
        product_init_max_windows: int = 10,
        sam_warmup_probe: bool = False,
        gpu_timing_probe: bool = False,
        dual_predictor_vram_probe: bool = False,
        ready_after_completed_windows: int = 2,
        diagnostics_root: Optional[str | Path] = None,
    ) -> None:
        if not runtime_instance_id.strip():
            raise ValueError("runtime_instance_id is required")
        self.runtime_instance_id = runtime_instance_id
        self.output_dir = Path(output_dir)
        self.sam_runtime_root = Path(sam_runtime_root)
        self.candidate_assignments = candidate_assignments
        self.initialization_prompts = None if initialization_prompts is None else dict(initialization_prompts)
        self.selected_product_keys = (
            None
            if selected_product_keys is None
            else tuple(str(value) for value in selected_product_keys)
        )
        self.product_config = None if product_config is None else dict(product_config)
        self.product_surface = product_surface
        self.display_evidence_mode = str(display_evidence_mode)
        self.support_contact_points = dict(support_contact_points or {})
        self.support_contact_tolerance = float(support_contact_tolerance)
        self.base = base_module
        self.product_module = product_module
        self.product_tracking_module = product_tracking_module
        self.predictor = predictor
        self.person_provider = person_provider
        self.product_provider = product_provider
        self.sam_warmup_probe = bool(sam_warmup_probe)
        self.gpu_timing_probe = bool(gpu_timing_probe)
        self.dual_predictor_vram_probe = bool(dual_predictor_vram_probe)
        if ready_after_completed_windows < 1:
            raise ValueError("ready_after_completed_windows must be positive")
        self.ready_after_completed_windows = int(ready_after_completed_windows)
        self.diagnostics_root = None if diagnostics_root is None else Path(diagnostics_root)
        if product_init_max_windows < 1:
            raise ValueError("product_init_max_windows must be positive")
        self.product_init_max_windows = int(product_init_max_windows)
        self.product_init_pending_windows = 0
        self.product_init_ready = False
        self.window_no = 0
        self._initialized = False
        self._owns_predictor = predictor is None
        self._original_window_dir: Optional[Path] = None
        self._metrics: dict[str, object] = {}
        self.last_window_metrics: dict[str, object] = {}
        self._warmup_done = False
        self._warmup_profile: dict[str, object] = {
            "enabled": self.sam_warmup_probe,
            "completed": False,
        }
        self._profile_recorder: Optional[ProductLatencyProfileRecorder] = None
        self._person_profile_recorder: Optional[PersonLatencyProfileRecorder] = None
        self._readiness: Optional[RuntimeReadinessController] = None

    def _ensure_readiness(self) -> RuntimeReadinessController:
        if self._readiness is None:
            self._readiness = RuntimeReadinessController(
                self.output_dir,
                ready_after_completed_windows=self.ready_after_completed_windows,
            )
        return self._readiness

    @property
    def runtime_state(self) -> str:
        return (
            RuntimeState.STARTING.value
            if self._readiness is None
            else self._readiness.state.value
        )

    def readiness_snapshot(self) -> Mapping[str, object]:
        return (
            {"runtime_state": RuntimeState.STARTING.value, "stage11_entered": False}
            if self._readiness is None
            else self._readiness.snapshot()
        )

    def mark_rtsp_connected(self) -> None:
        self._ensure_readiness().mark_rtsp_connected()

    def mark_degraded(self, reason: str, *, fatal: bool = False) -> None:
        self._ensure_readiness().mark_degraded(reason, fatal=fatal)

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        readiness = self._ensure_readiness()
        try:
            if self.base is None or self.product_module is None or self.product_tracking_module is None:
                root = str(self.sam_runtime_root.resolve())
                if root not in sys.path:
                    sys.path.insert(0, root)
                self.base = importlib.import_module(
                    "sam31_stage5_1_1_demo_latency_finalization"
                )
                self.product_module = importlib.import_module(
                    "sam31_stage7c6_dual_smart_toy_probe"
                )
                self.product_tracking_module = importlib.import_module(
                    "sam31_stage7c_product_tracking"
                )
            if self.selected_product_keys and tuple(self.product_module.PRODUCT_KEYS) != tuple(self.selected_product_keys):
                # The frozen Stage7C provider is deliberately reused for another
                # approved product catalog. Its module-level product key table
                # is the only legacy catalog binding; all runtime associations
                # remain keyed by the caller-provided configuration.
                self.product_module.PRODUCT_KEYS = tuple(self.selected_product_keys)
                self.product_module.PRODUCT_TYPE = "smart_guitar"
                # The frozen Stage7C rolling-window contract re-groundings
                # each new window before overlap association.  Use the
                # concrete catalog noun so SAM3 can recover both guitars;
                # the confirmed first-frame bbox/point prompts remain the
                # only initialization source.
                self.product_module.TEXT_PROMPT = "guitar"
            config = None
            surface = None
            selected_keys = self.selected_product_keys
            if self.product_provider is None:
                if self.product_config is not None:
                    config = dict(self.product_config)
                    surface = self.product_surface
                else:
                    config, surface = self.product_tracking_module.load_config()
                selected_keys = tuple(
                    self.selected_product_keys
                    or config.get(
                        "selected_product_keys",
                        getattr(self.product_module, "PRODUCT_KEYS", PRODUCT_KEYS),
                    )
                )
            _validate_frozen_runtime(
                self.base,
                self.product_module,
                selected_product_keys=selected_keys,
            )
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._profile_recorder = ProductLatencyProfileRecorder(
                self.output_dir / "stage10_2_1_product_profile"
            )
            self._person_profile_recorder = PersonLatencyProfileRecorder(self.output_dir)
            self._original_window_dir = Path(self.base.WINDOW_DIR)
            self.base.WINDOW_DIR = self.output_dir / "_active_window"
            if self.predictor is None:
                self.base.configure_torch()
                self.base.torch.cuda.reset_peak_memory_stats()
                self.predictor = self.base.build_predictor()
                self.base.tune_runtime(self.predictor)
            if self.dual_predictor_vram_probe:
                self._run_dual_predictor_vram_probe()
            if self.person_provider is None:
                self.person_provider = Sam31RealtimePersonProvider(
                    base_module=self.base,
                    predictor=self.predictor,
                    profile_recorder=self._person_profile_recorder,
                )
            elif getattr(self.person_provider, "profile_recorder", None) is None:
                self.person_provider.profile_recorder = self._person_profile_recorder
            if self.product_provider is None:
                assert config is not None and surface is not None
                assert selected_keys is not None
                runtime = MultiProductTrackRuntime(
                    camera_id=str(config["camera_id"]),
                    runtime_instance_id=self.runtime_instance_id,
                    products={
                        key: str(config["products"][key]["product_type"])
                        for key in selected_keys
                    },
                    product_display_surface=surface,
                    frame_width=int(self.base.AI_WIDTH),
                    frame_height=int(self.base.AI_HEIGHT),
                    surface_overlap_min=float(config["surface_overlap_min"]),
                    product_track_lost_after_windows=int(
                        config["product_track_lost_after_windows"]
                    ),
                    overlap_iou_min=float(config["association_overlap_iou_min"]),
                    geometry_distance_max=float(
                        config["association_geometry_distance_max"]
                    ),
                    ambiguity_margin=float(config["association_ambiguity_margin"]),
                    support_contact_points=self.support_contact_points,
                    support_contact_tolerance=self.support_contact_tolerance,
                    display_evidence_mode=self.display_evidence_mode,
                )
                self.product_provider = Sam31RealtimeProductProvider(
                    base_module=self.base,
                    product_module=self.product_module,
                    predictor=self.predictor,
                    config=config,
                    surface=surface,
                    runtime=runtime,
                    candidate_assignments=self.candidate_assignments,
                    initialization_prompts=self.initialization_prompts,
                    selected_product_keys=selected_keys,
                    diagnostics_root=(
                        self.diagnostics_root
                        if self.diagnostics_root is not None
                        else self.output_dir
                        / "stage10_2_product_preflight"
                        / "diagnostics"
                    ),
                    profile_recorder=self._profile_recorder,
                    gpu_timing_probe=self.gpu_timing_probe,
                )
            elif getattr(self.product_provider, "profile_recorder", None) is None:
                self.product_provider.profile_recorder = self._profile_recorder
            self._initialized = True
            if readiness.state is RuntimeState.STARTING:
                readiness.start(
                    predictor_ready=self.predictor is not None,
                    warmup_enabled=self.sam_warmup_probe,
                )
        except ProviderInitializationError:
            readiness.mark_degraded("provider_initialization_error", fatal=True)
            raise
        except Exception as error:
            readiness.mark_degraded("provider_initialization_error", fatal=True)
            raise ProviderInitializationError(
                f"SAM3.1 provider initialization failed: {error}"
            ) from error

    def _run_dual_predictor_vram_probe(self) -> None:
        assert self.base is not None
        assert self.predictor is not None
        root = self.output_dir / "stage10_2_1_product_profile"
        root.mkdir(parents=True, exist_ok=True)
        first = cuda_memory_snapshot(self.base)
        projected_peak_mb = max(
            first["allocated_mb"],
            first["peak_allocated_mb"],
        ) * 2.0
        payload: dict[str, object] = {
            "enabled": True,
            "predictor_a": first,
            "projected_peak_mb": projected_peak_mb,
            "safety_limit_mb": 14500.0,
            "concurrent_inference": False,
        }
        path = root / "dual_predictor_vram_probe.json"
        if projected_peak_mb > 14500.0:
            payload["result"] = "FAIL_FEASIBILITY"
            payload["predictor_b_loaded"] = False
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            raise ProviderInitializationError(
                "dual Predictor projected VRAM exceeds the 14.5GB safety gate"
            )
        predictor_b = self.base.build_predictor()
        self.base.tune_runtime(predictor_b)
        try:
            second = cuda_memory_snapshot(self.base)
            payload["predictor_b"] = second
            payload["predictor_b_loaded"] = True
            payload["result"] = (
                "PASS_FEASIBILITY"
                if second["peak_allocated_mb"] <= 14500.0
                else "FAIL_FEASIBILITY"
            )
        finally:
            predictor_b = None
            gc.collect()
            torch = getattr(self.base, "torch", None)
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if payload["result"] != "PASS_FEASIBILITY":
            raise ProviderInitializationError(
                "dual Predictor measured VRAM exceeds the 14.5GB safety gate"
            )

    def _run_sam_warmup_probe(self) -> None:
        if self._warmup_done or not self.sam_warmup_probe:
            return
        assert self.base is not None
        assert self.product_module is not None
        assert self.predictor is not None
        assert self.product_provider is not None
        assert self.person_provider is not None
        stage7c = getattr(self.product_module, "stage7c", None)
        add_text_prompt = getattr(stage7c, "add_text_prompt", None)
        candidates_from_outputs = getattr(stage7c, "candidates_from_outputs", None)
        if not callable(add_text_prompt) or not callable(candidates_from_outputs):
            raise ProviderInitializationError("SAM warmup helpers are unavailable")

        product_state_before = {
            "window_no": self.product_provider.window_no,
            "initialized": self.product_provider.initialized,
            "grounding_count": self.product_provider.grounding_guard.grounding_count,
        }
        person_state_before = {
            "window_no": self.person_provider.window_no,
            "next_global_id": self.person_provider.next_global_id,
            "global_track_count": len(self.person_provider.global_tracks),
        }
        total_started = time.perf_counter()
        session_started = time.perf_counter()
        session_id = self.base.start_session(self.predictor)
        session_start_ms = max(0.0, (time.perf_counter() - session_started) * 1000.0)
        grounding_ms = 0.0
        propagation_ms = 0.0
        session_close_ms = 0.0
        candidate_count = 0
        propagated_frames = 0
        try:
            grounding_started = time.perf_counter()
            response = add_text_prompt(
                self.predictor,
                session_id,
                str(getattr(self.product_module, "TEXT_PROMPT", "plush toy")),
            )
            grounding_ms = max(0.0, (time.perf_counter() - grounding_started) * 1000.0)
            candidates = tuple(candidates_from_outputs((response or {}).get("outputs", {})))
            candidate_count = len(candidates)
            propagation_started = time.perf_counter()
            for _response in self.base.propagate_forward(
                self.predictor,
                session_id,
                start_frame_index=0,
            ):
                propagated_frames += 1
            propagation_ms = max(0.0, (time.perf_counter() - propagation_started) * 1000.0)
        finally:
            close_started = time.perf_counter()
            self.base.close_session(self.predictor, session_id)
            session_close_ms = max(0.0, (time.perf_counter() - close_started) * 1000.0)
        total_ms = max(0.0, (time.perf_counter() - total_started) * 1000.0)
        product_state_after = {
            "window_no": self.product_provider.window_no,
            "initialized": self.product_provider.initialized,
            "grounding_count": self.product_provider.grounding_guard.grounding_count,
        }
        person_state_after = {
            "window_no": self.person_provider.window_no,
            "next_global_id": self.person_provider.next_global_id,
            "global_track_count": len(self.person_provider.global_tracks),
        }
        self._warmup_profile = {
            "enabled": True,
            "completed": True,
            "cold_probe_ms": total_ms,
            "session_start_ms": session_start_ms,
            "grounding_ms": grounding_ms,
            "propagation_ms": propagation_ms,
            "session_close_ms": session_close_ms,
            "candidate_count": candidate_count,
            "propagated_frames": propagated_frames,
            "product_state_unchanged": product_state_before == product_state_after,
            "person_state_unchanged": person_state_before == person_state_after,
            "stage8_stage9_invoked": False,
            "event_count": 0,
        }
        self._warmup_done = True
        if self._readiness is not None:
            self._readiness.mark_warmup_complete()
        set_warmup_profile = getattr(self.product_provider, "set_warmup_profile", None)
        if callable(set_warmup_profile):
            set_warmup_profile(self._warmup_profile)
        (self.output_dir / "stage10_2_1_product_profile" / "sam_warmup_probe.json").write_text(
            json.dumps(self._warmup_profile, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def process_window(
        self,
        contexts: Sequence[RealtimeFrameContext],
        *,
        terminal: bool = False,
    ) -> Sequence[ProviderFrameObservations]:
        self._ensure_initialized()
        assert self.base is not None
        assert self.person_provider is not None
        assert self.product_provider is not None
        readiness = self._ensure_readiness()
        window_runtime_state = readiness.state.value
        contexts = _validate_contexts(contexts, window_no=self.window_no, terminal=terminal)
        materialize_started = time.perf_counter()
        self.base.materialize_window(list(_sampled_frames(self.base, contexts)))
        product_materialize_ms = max(
            0.0,
            (time.perf_counter() - materialize_started) * 1000.0,
        )
        provider_started_at = datetime.now(timezone.utc)
        torch = self.base.torch
        with torch.inference_mode(), self.base.autocast_context():
            self._run_sam_warmup_probe()
            product_started = time.perf_counter()
            try:
                products = (
                    self.product_provider.process_window(contexts, terminal=True)
                    if terminal
                    else self.product_provider.process_window(contexts)
                )
            except ProductInitializationPending as error:
                self.product_init_pending_windows += 1
                diagnostic = dict(self.product_provider.last_initialization_diagnostic)
                self.last_window_metrics = {
                    "window_no": self.window_no,
                    "product_init_status": "WAITING_FOR_PRODUCT_INIT",
                    "product_init_pending_windows": self.product_init_pending_windows,
                    "product_init_diagnostic": diagnostic,
                    "product_inference_ms": max(
                        0.0,
                        (time.perf_counter() - product_started) * 1000.0,
                    ),
                }
                if self.product_init_pending_windows > self.product_init_max_windows:
                    raise ProductInitializationTimeout(
                        "PRODUCT_INIT_TIMEOUT: "
                        f"waited {self.product_init_pending_windows} windows; "
                        f"max={self.product_init_max_windows}; "
                        f"last_diagnostic={diagnostic}"
                    ) from error
                self.window_no += 1
                raise
            product_finished = time.perf_counter()
            product_ready_at = datetime.now(timezone.utc)
            person_started = time.perf_counter()
            if self.person_provider.window_no == 0 and self.window_no > 0:
                self.person_provider.window_no = self.window_no
            persons = (
                self.person_provider.process_window(contexts, terminal=True)
                if terminal
                else self.person_provider.process_window(contexts)
            )
            person_finished = time.perf_counter()
            person_ready_at = datetime.now(timezone.utc)
        self.product_init_ready = bool(self.product_provider.initialized)
        fresh = contexts if self.window_no == 0 else contexts[OVERLAP_FRAMES:]
        expected = {item.source_seq for item in fresh}
        if set(persons) != expected or set(products) != expected:
            raise ProviderContractError(
                "Person/Product providers did not return the same fresh frame identities"
            )
        person_ms = max(0.0, (person_finished - person_started) * 1000.0)
        product_ms = max(0.0, (product_finished - product_started) * 1000.0)
        provider_wall_ms = max(0.0, (person_finished - product_started) * 1000.0)
        if (
            self.sam_warmup_probe
            and self._warmup_profile.get("completed")
            and "warm_start_product_init_ms" not in self._warmup_profile
        ):
            self._warmup_profile["warm_start_product_init_ms"] = product_ms
            self._warmup_profile["warmup_effect_ms"] = (
                float(self._warmup_profile.get("cold_probe_ms", 0.0)) - product_ms
            )
            set_warmup_profile = getattr(self.product_provider, "set_warmup_profile", None)
            if callable(set_warmup_profile):
                set_warmup_profile(self._warmup_profile)
            (self.output_dir / "stage10_2_1_product_profile" / "sam_warmup_probe.json").write_text(
                json.dumps(self._warmup_profile, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        finalize_profile = getattr(self.product_provider, "finalize_window_profile", None)
        profile = (
            finalize_profile(
                product_inference_ms=product_ms,
                product_materialize_ms=product_materialize_ms,
                person_inference_ms=person_ms,
            )
            if callable(finalize_profile)
            else {}
        )
        finalize_person_profile = getattr(self.person_provider, "finalize_window_profile", None)
        person_profile = (
            finalize_person_profile(
                person_inference_ms=person_ms,
                runtime_state=window_runtime_state,
            )
            if callable(finalize_person_profile)
            else {}
        )
        product_tokens_valid = all(
            bool(getattr(getattr(item, "observation", item), "product_track_token", ""))
            for values in products.values()
            for item in values
        )
        readiness.complete_provider_window(
            product_init_ready=self.product_init_ready,
            product_tokens_valid=product_tokens_valid,
            person_provider_primed=self.person_provider.window_no > 0,
        )
        self.last_window_metrics = {
            "window_no": self.window_no,
            "window_runtime_state": window_runtime_state,
            "runtime_state": window_runtime_state,
            "runtime_state_after_window": readiness.state.value,
            "person_inference_ms": person_ms,
            "product_inference_ms": product_ms,
            "serialized_provider_ms": person_ms + product_ms,
            "provider_wall_ms": provider_wall_ms,
            **dict(self.person_provider.last_window_metrics),
            **dict(self.product_provider.last_window_metrics),
            **profile,
            **person_profile,
            "product_init_status": "READY" if self.product_init_ready else "WAITING_FOR_PRODUCT_INIT",
            "product_init_pending_windows": self.product_init_pending_windows,
        }
        self.last_window_metrics["person_inference_ms"] = person_ms
        self.last_window_metrics["product_inference_ms"] = product_ms
        self.window_no += 1
        return tuple(
            ProviderFrameObservations(
                context=context,
                person_tracks=tuple(persons[context.source_seq]),
                products=tuple(products[context.source_seq]),
                person_ready_at=person_ready_at,
                product_ready_at=product_ready_at,
                person_inference_ms=person_ms,
                product_inference_ms=product_ms,
                provider_started_at=provider_started_at,
            )
            for context in fresh
        )

    def start(self) -> None:
        self._ensure_initialized()
        if self.sam_warmup_probe and not self._warmup_done:
            assert self.base is not None
            warmup_window = (
                self.output_dir
                / "stage10_2_product_preflight"
                / "windows"
                / "window_000"
            )
            if len(tuple(warmup_window.glob("*.jpg"))) != WINDOW_FRAMES:
                raise ProviderInitializationError(
                    "SAM warmup probe requires the reviewed 12-frame Product preflight Window"
                )
            active_window = self.base.WINDOW_DIR
            self.base.WINDOW_DIR = warmup_window
            try:
                torch = self.base.torch
                with torch.inference_mode(), self.base.autocast_context():
                    self._run_sam_warmup_probe()
            finally:
                self.base.WINDOW_DIR = active_window

    def gpu_metrics(self) -> Mapping[str, object]:
        if self.base is None or not hasattr(self.base, "gpu_mem"):
            return dict(self._metrics)
        values = self.base.gpu_mem()
        self._metrics = {
            "allocated_mb": float(values.get("allocated_gb", 0.0)) * 1024.0,
            "reserved_mb": float(values.get("reserved_gb", 0.0)) * 1024.0,
            "peak_vram_mb": float(values.get("peak_allocated_gb", 0.0)) * 1024.0,
            "peak_reserved_mb": float(values.get("peak_reserved_gb", 0.0)) * 1024.0,
        }
        return dict(self._metrics)

    def close(self) -> None:
        if self.person_provider is not None:
            self.person_provider.close()
        if self.product_provider is not None:
            self.product_provider.close()
        if self._profile_recorder is not None:
            self._profile_recorder.close()
        if self._person_profile_recorder is not None:
            self._person_profile_recorder.close()
        if self.base is not None and self._original_window_dir is not None:
            self.base.WINDOW_DIR = self._original_window_dir
        if self._owns_predictor:
            self.predictor = None
            gc.collect()
            torch = getattr(self.base, "torch", None)
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        if self._readiness is not None:
            self._readiness.close()


def _validate_frozen_runtime(
    base: Any,
    product_module: Any,
    *,
    selected_product_keys: Optional[Sequence[str]] = None,
) -> None:
    actual = (
        int(base.AI_WIDTH),
        int(base.AI_HEIGHT),
        float(base.TARGET_AI_FPS),
        int(base.WINDOW_FRAMES),
        int(base.OVERLAP_FRAMES),
        int(base.FIXED_BATCH),
        bool(base.ENABLE_BF16),
        bool(base.ENABLE_CPU_OUTPUT_OFFLOAD),
    )
    expected = (1280, 720, 5.0, 12, 4, 2, True, True)
    if actual != expected:
        raise ProviderInitializationError(
            f"Frozen SAM3.1 runtime configuration changed: {actual}"
        )
    module_keys = tuple(product_module.PRODUCT_KEYS)
    expected_keys = tuple(selected_product_keys or PRODUCT_KEYS)
    if module_keys != expected_keys:
        raise ProviderInitializationError(
            "Product SAM adapter keys do not match selected_product_keys"
        )


def _validate_contexts(
    contexts: Sequence[RealtimeFrameContext],
    *,
    window_no: int,
    terminal: bool = False,
) -> tuple[RealtimeFrameContext, ...]:
    values = tuple(contexts)
    if (not terminal and len(values) != WINDOW_FRAMES) or (
        terminal and not 1 <= len(values) <= WINDOW_FRAMES
    ):
        raise ProviderContractError(
            f"SAM3.1 requires exactly {WINDOW_FRAMES} contexts per window"
        )
    if any(item.frame is None for item in values):
        raise ProviderContractError("SAM3.1 contexts must carry decoded frames")
    if any(item.camera_id != values[0].camera_id for item in values):
        raise ProviderContractError("SAM3.1 window mixes camera_id values")
    if any(
        (item.frame_width, item.frame_height)
        != (values[0].frame_width, values[0].frame_height)
        for item in values
    ):
        raise ProviderContractError("SAM3.1 window mixes frame dimensions")
    if any(right.source_seq <= left.source_seq for left, right in zip(values, values[1:])):
        raise ProviderContractError("SAM3.1 window source_seq must be strictly increasing")
    if window_no > 0 and not terminal and len(values[OVERLAP_FRAMES:]) != FRESH_FRAMES:
        raise ProviderContractError("Subsequent SAM3.1 windows require eight fresh frames")
    if window_no > 0 and terminal and len(values) <= OVERLAP_FRAMES:
        raise ProviderContractError("Terminal SAM3.1 window requires at least one fresh frame")
    return values


def _sampled_frames(
    base: Any,
    contexts: Sequence[RealtimeFrameContext],
) -> tuple[object, ...]:
    result = []
    for context in contexts:
        assert context.frame is not None
        captured_at = context.captured_at
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        result.append(base.SampledFrame(
            sample_id=context.source_seq,
            source_seq=context.source_seq,
            wall_time=captured_at.timestamp(),
            frame=context.frame,
        ))
    return tuple(result)


def _product_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    context: RealtimeFrameContext,
    product_keys: Sequence[str],
) -> tuple[Stage10ProductObservation, ...]:
    by_key: dict[str, Stage10ProductObservation] = {}
    for row in rows:
        source_seq = int(row["source_seq"])
        product_key = str(row["product_key"])
        if source_seq != context.source_seq:
            raise ProviderContractError(
                f"Product row source_seq {source_seq} does not match {context.source_seq}"
            )
        if product_key in by_key:
            raise ProviderContractError(
                f"Product runtime duplicated {product_key} at source_seq {source_seq}"
            )
        row_timestamp = _parse_timestamp(row.get("timestamp"))
        if row_timestamp is not None and _utc_naive(row_timestamp) != _utc_naive(context.timestamp):
            raise ProviderContractError(
                f"Product timestamp does not match source_seq {source_seq}"
            )
        try:
            status = ProductTrackStatus(str(row["track_status"]))
            display_state = ProductDisplayState(str(row["display_state"]))
        except (KeyError, ValueError) as error:
            raise ProviderContractError(
                f"Product status contract failed at source_seq {source_seq}"
            ) from error
        mask_present = bool(row.get("mask_present", False))
        bbox = row.get("bbox")
        normalized_bbox = None
        centroid = row.get("centroid")
        normalized_centroid = None
        if mask_present:
            if not isinstance(bbox, Sequence) or len(bbox) != 4:
                raise ProviderContractError("Observed Product row requires a four-value bbox")
            normalized_bbox = (
                float(bbox[0]) / context.frame_width,
                float(bbox[1]) / context.frame_height,
                float(bbox[2]) / context.frame_width,
                float(bbox[3]) / context.frame_height,
            )
            if not isinstance(centroid, Sequence) or len(centroid) != 2:
                raise ProviderContractError("Observed Product row requires a centroid")
            normalized_centroid = (float(centroid[0]), float(centroid[1]))
        track_id = row.get("product_track_id")
        track_token = row.get("product_track_token")
        if track_id is None or not track_token:
            raise ProviderContractError("Product row requires initialized track identity")
        observation = ProductAssociationObservation(
            observed_at=context.timestamp,
            source_seq=context.source_seq,
            camera_id=context.camera_id,
            product_key=product_key,
            product_type=str(row["product_type"]),
            product_track_id=int(track_id),
            product_track_token=str(track_token),
            bbox=normalized_bbox,
            centroid=normalized_centroid,
            track_status=status,
            display_state=display_state,
            mask_present=mask_present,
        )
        diagnostic_keys = (
            "local_obj_id",
            "grounding_candidate_count",
            "candidate_count",
            "display_evidence_mode",
            "current_support_contact_point",
            "approved_support_contact_point",
            "support_contact_distance",
            "support_contact_tolerance",
            "display_state_reason",
            "association_method",
            "association_success",
            "association_ambiguous",
            "consecutive_missed_windows",
            "tracking_lost",
            "recovered_from_missing_pending",
            "recovered_after_missed_windows",
            "recovery_attempted",
            "recovery_candidate_count",
            "recovery_success",
            "recovery_rejected_reason",
            "recovery_latency_windows",
            "association_mask_iou",
            "association_centroid_distance_ratio",
        )
        diagnostics = {
            key: row.get(key)
            for key in diagnostic_keys
            if key in row
        }
        diagnostics["local_obj_id"] = row.get(
            "_diagnostic_local_obj_id", diagnostics.get("local_obj_id")
        )
        overlap = row.get("surface_overlap_ratio")
        by_key[product_key] = Stage10ProductObservation(
            observation=observation,
            surface_overlap_ratio=None if overlap is None else float(overlap),
            recovered_from_missing_pending=bool(
                row.get("recovered_from_missing_pending", False)
            ),
            diagnostics=diagnostics,
            mask=row.get("_diagnostic_mask") if isinstance(row.get("_diagnostic_mask"), np.ndarray) else None,
        )
    if set(by_key) != set(product_keys):
        raise ProviderContractError(
            "Product runtime must return one row for both Toy A and Toy B"
        )
    return tuple(by_key[key] for key in product_keys)


def _parse_timestamp(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderContractError(f"Invalid Product timestamp: {value}") from error


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
