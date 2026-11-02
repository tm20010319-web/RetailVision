from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.sam31_product_tracking import ProductCandidate, ProductDisplaySurface  # noqa: E402
from agent.behavior.sam31_realtime_providers import (  # noqa: E402
    FRESH_FRAMES,
    OVERLAP_FRAMES,
    WINDOW_FRAMES,
    Sam31RealtimeProductProvider,
    Sam31RealtimeProviderBundle,
    _validate_frozen_runtime,
)
from agent.runtime.stage10_demo import RealtimeFrameContext, Stage10PerformanceRecorder  # noqa: E402
from agent.runtime.stage10_product_profile import (  # noqa: E402
    PRODUCT_PROFILE_FIELDS,
    ProductLatencyProfileRecorder,
    classify_product_bottleneck,
    cuda_memory_snapshot,
    finalize_product_window_profile,
)
from scripts.run_stage10_demo import parse_args  # noqa: E402


def _profile(window_no: int, inference_ms: float, **stages: float) -> dict[str, object]:
    payload: dict[str, object] = {
        "window_no": window_no,
        "product_phase": "INITIALIZATION" if window_no == 0 else "STEADY_STATE",
        "product_materialize_ms": 2.0,
    }
    payload.update({field: 0.0 for field in PRODUCT_PROFILE_FIELDS})
    payload.update(stages)
    return finalize_product_window_profile(
        payload,
        product_inference_ms=inference_ms,
        external_process_ms=sum(float(payload[field]) for field in PRODUCT_PROFILE_FIELDS),
        external_artifact_conversion_ms=float(payload["product_artifact_conversion_ms"]),
    )


class Stage1021ProductLatencyTests(unittest.TestCase):
    def test_product_substage_timing_aggregation_explains_inference(self):
        row = _profile(
            0,
            100.0,
            product_session_start_ms=5.0,
            product_grounding_ms=20.0,
            product_propagation_ms=40.0,
            product_output_conversion_ms=5.0,
            product_candidate_evaluation_ms=5.0,
            product_track_association_ms=5.0,
            product_artifact_conversion_ms=5.0,
            product_session_close_ms=5.0,
        )
        explained = sum(float(row[field]) for field in PRODUCT_PROFILE_FIELDS)
        self.assertAlmostEqual(100.0, explained)
        self.assertAlmostEqual(0.9, row["product_accounted_ratio"])

    def test_unattributed_latency_is_a_nonnegative_residual(self):
        row = _profile(1, 50.0, product_grounding_ms=12.0, product_propagation_ms=18.0)
        self.assertAlmostEqual(20.0, row["product_unattributed_ms"])
        self.assertFalse(row["slow_product_window"])

    def test_init_and_steady_state_are_aggregated_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = ProductLatencyProfileRecorder(directory)
            recorder.record(_profile(0, 7000.0, product_grounding_ms=3000.0))
            recorder.record(_profile(1, 2000.0, product_grounding_ms=800.0))
            recorder.record(_profile(2, 2400.0, product_grounding_ms=900.0))
            summary = recorder.close()
        self.assertEqual(1, summary["initialization"]["product_inference_ms"]["count"])
        self.assertEqual(2, summary["steady_state"]["product_inference_ms"]["count"])
        self.assertEqual(2200.0, summary["steady_state"]["product_inference_ms"]["p50"])

    def test_slow_window_detection_records_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = ProductLatencyProfileRecorder(directory)
            recorder.record(_profile(0, 6001.0))
            recorder.record(_profile(1, 4999.0))
            summary = recorder.close()
        self.assertEqual(1, summary["slow_window_count"])
        self.assertEqual([0], summary["slow_window_numbers"])

    def test_grounding_dominant_classification_uses_measured_ratio(self):
        result = classify_product_bottleneck((
            _profile(1, 100.0, product_grounding_ms=70.0),
            _profile(2, 100.0, product_grounding_ms=60.0),
        ))
        self.assertEqual("GROUNDING_DOMINANT", result["classification"])

    def test_propagation_dominant_classification_uses_measured_ratio(self):
        result = classify_product_bottleneck((
            _profile(1, 100.0, product_propagation_ms=70.0),
            _profile(2, 100.0, product_propagation_ms=60.0),
        ))
        self.assertEqual("PROPAGATION_DOMINANT", result["classification"])

    def test_initialization_only_classification_requires_no_steady_slow_window(self):
        result = classify_product_bottleneck((
            _profile(0, 8000.0),
            _profile(1, 2000.0),
        ))
        self.assertEqual("INITIALIZATION_ONLY", result["classification"])

    def test_profile_writer_creates_all_required_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = ProductLatencyProfileRecorder(root)
            recorder.record(_profile(0, 100.0))
            recorder.close()
            for name in (
                "product_window_profile.jsonl",
                "product_phase_summary.json",
                "slow_windows.json",
                "cuda_memory_profile.jsonl",
                "product_latency_breakdown.md",
                "duplicate_work_report.json",
            ):
                self.assertTrue((root / name).is_file(), name)

    def test_memory_collection_is_safe_without_cuda(self):
        cuda = SimpleNamespace(is_available=lambda: False)
        self.assertEqual(
            {"allocated_mb": 0.0, "reserved_mb": 0.0, "peak_allocated_mb": 0.0},
            cuda_memory_snapshot(SimpleNamespace(torch=SimpleNamespace(cuda=cuda))),
        )

    def test_product_provider_instruments_session_grounding_propagation_and_association(self):
        @dataclass
        class SampledFrame:
            sample_id: int
            source_seq: int
            wall_time: float
            frame: np.ndarray

        cuda = SimpleNamespace(is_available=lambda: False)
        calls = {"start": 0, "close": 0, "propagation": 0, "association": 0}
        base = SimpleNamespace(
            SampledFrame=SampledFrame,
            torch=SimpleNamespace(cuda=cuda),
            to_numpy=np.asarray,
        )

        def start_session(_predictor):
            calls["start"] += 1
            return "session"

        def close_session(_predictor, _session_id):
            calls["close"] += 1

        def propagate_forward(_predictor, _session_id, start_frame_index=0):
            for index in range(start_frame_index, WINDOW_FRAMES):
                calls["propagation"] += 1
                yield {"frame_index": index, "outputs": {"out_obj_ids": [0, 1]}}

        base.start_session = start_session
        base.close_session = close_session
        base.propagate_forward = propagate_forward
        candidates = (
            ProductCandidate(0, (640.0, 350.0, 700.0, 430.0), (670.0, 390.0), 0.9, np.ones((720, 1280), dtype=bool)),
            ProductCandidate(1, (720.0, 350.0, 790.0, 430.0), (755.0, 390.0), 0.9, np.ones((720, 1280), dtype=bool)),
        )
        stage7c = SimpleNamespace(
            candidates_from_outputs=lambda _outputs: candidates,
        )

        class Guard:
            grounding_count = 0

            def run(self, _window_no, operation):
                self.grounding_count += 1
                return operation()

        class Runtime:
            def initialize_assignments(self, **_kwargs):
                calls["association"] += 1
                return ()

        module = SimpleNamespace(
            PRODUCT_KEYS=("smart_toy_a", "smart_toy_b"),
            TEXT_PROMPT="plush toy",
            ProbeState=lambda: SimpleNamespace(),
            SingleTextGroundingPerWindow=Guard,
            stage7c=stage7c,
            select_initial_assignments=lambda _frame, _surface, _candidates: {},
        )

        def process_window(**kwargs):
            session_id = base.start_session(kwargs["predictor"])
            response = kwargs["grounding_guard"].run(
                kwargs["window_no"],
                lambda: {"outputs": {"out_obj_ids": [0, 1]}},
            )
            converted = stage7c.candidates_from_outputs(response["outputs"])
            assignments = module.select_initial_assignments(
                kwargs["window_items"][0].frame,
                kwargs["surface"],
                converted,
            )
            kwargs["runtime"].initialize_assignments(assignments=assignments)
            for response in base.propagate_forward(kwargs["predictor"], session_id):
                stage7c.candidates_from_outputs(response["outputs"])
            base.close_session(kwargs["predictor"], session_id)
            for item in kwargs["window_items"]:
                rows = [{
                    "timestamp": datetime.fromtimestamp(item.wall_time, tz=timezone.utc).isoformat(),
                    "source_seq": item.source_seq,
                    "product_key": key,
                    "product_type": "smart_toy",
                    "product_track_id": index + 1,
                    "product_track_token": f"token:{key}",
                    "bbox": list(candidate.bbox),
                    "centroid": [candidate.centroid[0] / 1280, candidate.centroid[1] / 720],
                    "track_status": "ACTIVE",
                    "display_state": "IN_DISPLAY",
                    "mask_present": True,
                    "surface_overlap_ratio": 1.0,
                } for index, (key, candidate) in enumerate(assignments.items())]
                kwargs["artifacts"].append_frame(rows=rows, frame=item.frame, candidates=assignments)

        module.process_window = process_window
        started = datetime(2026, 8, 16, tzinfo=timezone.utc)
        contexts = tuple(
            RealtimeFrameContext(
                camera_id="cam02",
                source_seq=index,
                captured_at=started + timedelta(seconds=index / 5),
                frame_width=1280,
                frame_height=720,
                frame=np.zeros((720, 1280, 3), dtype=np.uint8),
            )
            for index in range(WINDOW_FRAMES)
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = ProductLatencyProfileRecorder(directory)
            provider = Sam31RealtimeProductProvider(
                base_module=base,
                product_module=module,
                predictor=object(),
                config={},
                surface=ProductDisplaySurface(((0.4, 0.3), (0.8, 0.3), (0.8, 0.9), (0.4, 0.9))),
                runtime=Runtime(),
                candidate_assignments={"smart_toy_a": 0, "smart_toy_b": 1},
                profile_recorder=recorder,
            )
            provider.process_window(contexts)
            row = provider.finalize_window_profile(
                product_inference_ms=float(provider.last_window_metrics["product_inference_ms"]),
                product_materialize_ms=1.0,
                person_inference_ms=2.0,
            )
            provider.close()
        self.assertEqual({"start": 1, "close": 1, "propagation": 12, "association": 1}, calls)
        for field in (
            "product_session_start_ms",
            "product_grounding_ms",
            "product_propagation_ms",
            "product_output_conversion_ms",
            "product_candidate_evaluation_ms",
            "product_track_association_ms",
            "product_session_close_ms",
            "allocated_before_mb",
            "allocated_after_mb",
        ):
            self.assertIn(field, row)

    def test_warmup_probe_does_not_modify_product_person_or_event_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage10_2_1_product_profile").mkdir()
            captured = {}
            product_provider = SimpleNamespace(
                window_no=0,
                initialized=False,
                grounding_guard=SimpleNamespace(grounding_count=0),
                set_warmup_profile=lambda value: captured.update(value),
            )
            person_provider = SimpleNamespace(window_no=0, next_global_id=1, global_tracks={})
            base = SimpleNamespace(
                start_session=lambda _predictor: "warmup",
                close_session=lambda _predictor, _session: None,
                propagate_forward=lambda *_args, **_kwargs: iter(({"frame_index": 0},)),
            )
            stage7c = SimpleNamespace(
                add_text_prompt=lambda *_args: {"outputs": {}},
                candidates_from_outputs=lambda _outputs: (),
            )
            bundle = Sam31RealtimeProviderBundle(
                runtime_instance_id="test",
                output_dir=root,
                base_module=base,
                product_module=SimpleNamespace(stage7c=stage7c, TEXT_PROMPT="plush toy"),
                predictor=object(),
                person_provider=person_provider,
                product_provider=product_provider,
                sam_warmup_probe=True,
            )
            bundle._run_sam_warmup_probe()
        self.assertTrue(captured["product_state_unchanged"])
        self.assertTrue(captured["person_state_unchanged"])
        self.assertFalse(captured["stage8_stage9_invoked"])
        self.assertEqual(0, captured["event_count"])

    def test_optional_probes_are_disabled_by_default(self):
        bundle = Sam31RealtimeProviderBundle(runtime_instance_id="test", output_dir="unused")
        self.assertFalse(bundle.sam_warmup_probe)
        self.assertFalse(bundle.gpu_timing_probe)
        self.assertFalse(bundle.dual_predictor_vram_probe)

    def test_cli_probe_flags_default_to_false(self):
        with patch.object(sys, "argv", ["run_stage10_demo/legacy_cli.py", "--rtsp", "rtsp://redacted"]):
            args = parse_args()
        self.assertFalse(args.sam_warmup_probe)
        self.assertFalse(args.gpu_timing_probe)
        self.assertFalse(args.dual_predictor_vram_probe)

    def test_frozen_five_fps_window_overlap_batch_and_offload_remain_enforced(self):
        base = SimpleNamespace(
            AI_WIDTH=1280,
            AI_HEIGHT=720,
            TARGET_AI_FPS=5.0,
            WINDOW_FRAMES=12,
            OVERLAP_FRAMES=4,
            FIXED_BATCH=2,
            ENABLE_BF16=True,
            ENABLE_CPU_OUTPUT_OFFLOAD=True,
        )
        _validate_frozen_runtime(base, SimpleNamespace(PRODUCT_KEYS=("smart_toy_a", "smart_toy_b")))
        self.assertEqual((12, 4, 8), (WINDOW_FRAMES, OVERLAP_FRAMES, FRESH_FRAMES))

    def test_performance_summary_carries_product_substage_stats(self):
        recorder = Stage10PerformanceRecorder()
        recorder.record_provider({
            "product_inference_ms": 100.0,
            "product_session_start_ms": 5.0,
            "product_grounding_ms": 20.0,
            "product_propagation_ms": 50.0,
            "product_candidate_association_ms": 4.0,
            "product_session_close_ms": 1.0,
            "product_unattributed_ms": 2.0,
        })
        summary = recorder.close()["provider"]
        self.assertEqual(20.0, summary["product_grounding_ms"]["p50"])
        self.assertEqual(50.0, summary["product_propagation_ms"]["p95"])

    def test_candidate_and_track_association_are_reported_together_without_double_counting(self):
        row = _profile(
            1,
            50.0,
            product_candidate_evaluation_ms=3.0,
            product_track_association_ms=7.0,
        )
        self.assertEqual(10.0, row["product_candidate_association_ms"])
        self.assertEqual(40.0, row["product_unattributed_ms"])

    def test_duplicate_work_report_confirms_single_materialization(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = ProductLatencyProfileRecorder(directory)
            recorder.record(_profile(0, 100.0))
            recorder.close()
            report = json.loads(
                (Path(directory) / "duplicate_work_report.json").read_text(encoding="utf-8")
            )
        self.assertEqual(1, report["materialize_calls_per_window"])
        self.assertFalse(report["duplicate_window_jpeg_write_detected"])


if __name__ == "__main__":
    unittest.main()
