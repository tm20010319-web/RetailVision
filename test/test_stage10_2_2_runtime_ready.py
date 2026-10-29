from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.stage10_demo import Stage10PerformanceRecorder  # noqa: E402
from agent.stage10_runtime_readiness import (  # noqa: E402
    PersonLatencyProfileRecorder,
    RuntimeReadinessController,
    RuntimeState,
    Stage1022AcceptanceRecorder,
    classify_ready_root_cause,
    finalize_person_window_profile,
    monotonic_growth,
)


def _performance(*, ready_p50: float = 3500.0, ready_p95: float = 4500.0):
    return {
        "all_run_metrics": {
            "pipeline_lag_ms": {"p50": 3700.0, "p95": 6000.0, "max": 7000.0},
        },
        "ready_state_metrics": {
            "pipeline_lag_ms": {"p50": ready_p50, "p95": ready_p95, "max": ready_p95 + 200.0},
            "oldest_frame_age_ms": {"p50": 1600.0, "p95": 1800.0, "max": 1900.0},
            "newest_frame_age_ms": {"p50": 30.0, "p95": 180.0, "max": 200.0},
        },
    }


def _readiness(state: str = "STOPPED"):
    return {
        "runtime_state": state,
        "ready_at": datetime.now(timezone.utc).isoformat(),
        "startup_to_ready_seconds": 12.0,
        "stage11_entered": False,
    }


def _person(track_id: int = 7, token: str = "store:cam02:run:7"):
    return {"track_id": track_id, "track_token": token}


def _products(a_id: int = 1, a_token: str = "toy-a", b_id: int = 2, b_token: str = "toy-b"):
    return [
        {"product_key": "smart_toy_a", "track_id": a_id, "track_token": a_token, "track_status": "ACTIVE"},
        {"product_key": "smart_toy_b", "track_id": b_id, "track_token": b_token, "track_status": "ACTIVE"},
    ]


class Stage1022RuntimeReadyTests(unittest.TestCase):
    def test_runtime_state_starting_to_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = RuntimeReadinessController(directory, ready_after_completed_windows=2)
            controller.start(predictor_ready=True, warmup_enabled=False)
            controller.mark_rtsp_connected()
            controller.complete_provider_window(product_init_ready=True, product_tokens_valid=True, person_provider_primed=True)
            self.assertEqual(RuntimeState.PERSON_PRIMING, controller.state)
            controller.complete_provider_window(product_init_ready=True, product_tokens_valid=True, person_provider_primed=True)
            self.assertEqual(RuntimeState.READY, controller.state)
            self.assertIsNotNone(controller.startup_to_ready_seconds)
            controller.close()

    def test_product_init_incomplete_cannot_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = RuntimeReadinessController(directory, ready_after_completed_windows=1)
            controller.start(predictor_ready=True, warmup_enabled=False)
            controller.mark_rtsp_connected()
            controller.complete_provider_window(product_init_ready=False, product_tokens_valid=False, person_provider_primed=True)
            self.assertEqual(RuntimeState.PRODUCT_INITIALIZING, controller.state)
            controller.close()

    def test_person_provider_unprimed_cannot_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = RuntimeReadinessController(directory, ready_after_completed_windows=1)
            controller.start(predictor_ready=True, warmup_enabled=False)
            controller.mark_rtsp_connected()
            controller.complete_provider_window(product_init_ready=True, product_tokens_valid=True, person_provider_primed=False)
            self.assertEqual(RuntimeState.PERSON_PRIMING, controller.state)
            controller.close()

    def test_ready_state_metrics_are_aggregated_separately(self):
        recorder = Stage10PerformanceRecorder()
        recorder.record_window({"runtime_state": "PRODUCT_INITIALIZING", "oldest_frame_age_ms": 9000.0})
        recorder.record_window({"runtime_state": "READY", "oldest_frame_age_ms": 1800.0})
        recorder.record_frame({"runtime_state": "PRODUCT_INITIALIZING", "pipeline_lag_ms": 8000.0})
        recorder.record_frame({"runtime_state": "READY", "pipeline_lag_ms": 4000.0})
        recorder.record_provider({"runtime_state": "PRODUCT_INITIALIZING", "person_inference_ms": 2000.0})
        recorder.record_provider({"runtime_state": "READY", "person_inference_ms": 1000.0})
        summary = recorder.close()
        self.assertEqual(1, summary["ready_state_metrics"]["frames"])
        self.assertEqual(4000.0, summary["ready_state_metrics"]["pipeline_lag_ms"]["p95"])

    def test_startup_metrics_are_not_deleted(self):
        recorder = Stage10PerformanceRecorder()
        recorder.record_frame({"runtime_state": "STARTING", "pipeline_lag_ms": 9000.0})
        recorder.record_frame({"runtime_state": "READY", "pipeline_lag_ms": 3000.0})
        summary = recorder.close()
        self.assertEqual(2, summary["all_run_metrics"]["frames"])
        self.assertEqual(9000.0, summary["all_run_metrics"]["pipeline_lag_ms"]["max"])

    def test_person_latency_stage_aggregation_has_explicit_residual(self):
        row = finalize_person_window_profile(
            {
                "person_session_start_ms": 10.0,
                "person_grounding_ms": 20.0,
                "person_regrounding_ms": 5.0,
                "person_global_association_ms": 5.0,
                "person_propagation_ms": 40.0,
                "person_output_conversion_ms": 5.0,
                "person_session_close_ms": 5.0,
            },
            person_inference_ms=100.0,
            external_process_ms=90.0,
        )
        explained = sum(row[field] for field in (
            "person_session_start_ms", "person_grounding_ms", "person_global_association_ms",
            "person_propagation_ms", "person_output_conversion_ms", "person_session_close_ms",
            "person_other_ms", "person_unattributed_ms",
        ))
        self.assertAlmostEqual(100.0, explained)

    def test_empty_and_person_present_metadata_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = PersonLatencyProfileRecorder(directory)
            recorder.record({"person_scene_phase": "EMPTY_SCENE", "runtime_state": "PERSON_PRIMING", "person_inference_ms": 500.0})
            recorder.record({"person_scene_phase": "PERSON_PRESENT", "runtime_state": "READY", "person_inference_ms": 1500.0, "person_track_count": 1})
            summary = recorder.close()
        self.assertEqual(1, summary["empty_scene"]["window_count"])
        self.assertEqual(1, summary["person_present"]["window_count"])
        self.assertEqual(1500.0, summary["person_present"]["person_inference_ms"]["p50"])

    def _valid_recorder(self, directory: str, *, windows: int = 20) -> Stage1022AcceptanceRecorder:
        recorder = Stage1022AcceptanceRecorder(directory, minimum_person_present_ready_windows=20)
        for index in range(windows):
            recorder.record_queue({"runtime_state": "READY", "queue_depth_after": 0, "newest_frame_age_ms": 30 + index % 2})
            recorder.record_provider({
                "runtime_state": "READY", "window_no": index, "person_track_count": 1,
                "person_inference_ms": 1500.0, "product_inference_ms": 2100.0,
            })
            recorder.record_frame(
                {"runtime_state": "READY", "pipeline_lag_ms": 4000.0, "source_seq": index},
                {"runtime_state": "READY", "source_seq": index, "timestamp": f"2026-08-16T00:00:{index:02d}",
                 "persons": [_person()], "products": _products(), "associations": [], "interactions": [], "events": []},
            )
        return recorder

    def test_person_track_count_validation_requires_twenty_ready_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory, windows=19)
            summary = recorder.close(performance_summary=_performance(), runtime_summary={"runtime_errors": 0}, readiness=_readiness())
        self.assertEqual("NOT RUN", summary["person_present_test"])
        self.assertFalse(summary["allow_stage10_3_af"])

    def test_global_person_id_switch_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            recorder.record_frame(
                {"runtime_state": "READY", "pipeline_lag_ms": 4000.0, "source_seq": 99},
                {"source_seq": 99, "timestamp": "2026-08-16T00:01:39", "persons": [
                    _person(8, "store:cam02:run:8"),
                    _person(9, "store:cam02:run:9"),
                ],
                 "products": _products(), "associations": [], "interactions": [], "events": []},
            )
            summary = recorder.close(performance_summary=_performance(), runtime_summary={"runtime_errors": 0}, readiness=_readiness())
        self.assertEqual(1, summary["person_global_id_switch_count"])
        self.assertEqual([7, 8, 9], summary["person_global_ids"])
        self.assertFalse(summary["allow_stage10_3_af"])

    def test_ready_gate_rejects_missing_ready_and_identity_losses(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            not_ready = _readiness()
            not_ready["ready_at"] = None
            summary = recorder.close(
                performance_summary=_performance(),
                runtime_summary={"runtime_errors": 0},
                readiness=not_ready,
            )
        self.assertFalse(summary["runtime_ready"])
        self.assertFalse(summary["allow_stage10_3_af"])

        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            recorder.record_frame(
                {"runtime_state": "READY", "pipeline_lag_ms": 4000.0, "source_seq": 98},
                {"source_seq": 98, "timestamp": "2026-08-16T00:01:38", "persons": [],
                 "products": _products(), "associations": [], "interactions": [], "events": []},
            )
            recorder.record_frame(
                {"runtime_state": "READY", "pipeline_lag_ms": 4000.0, "source_seq": 99},
                {"source_seq": 99, "timestamp": "2026-08-16T00:01:39", "persons": [_person()],
                 "products": _products(), "associations": [], "interactions": [], "events": []},
            )
            summary = recorder.close(
                performance_summary=_performance(),
                runtime_summary={"runtime_errors": 0},
                readiness=_readiness(),
            )
        self.assertGreater(summary["unexpected_person_lost_count"], 0)
        self.assertFalse(summary["allow_stage10_3_af"])

        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            lost_products = _products()
            lost_products[0]["track_status"] = "LOST"
            recorder.record_frame(
                {"runtime_state": "READY", "pipeline_lag_ms": 4000.0, "source_seq": 99},
                {"source_seq": 99, "timestamp": "2026-08-16T00:01:39", "persons": [_person()],
                 "products": lost_products, "associations": [], "interactions": [], "events": []},
            )
            summary = recorder.close(
                performance_summary=_performance(),
                runtime_summary={"runtime_errors": 0},
                readiness=_readiness(),
            )
        self.assertGreater(summary["unexpected_product_lost_count"], 0)
        self.assertFalse(summary["allow_stage10_3_af"])

    def test_product_id_swap_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            recorder.record_frame(
                {"runtime_state": "READY", "pipeline_lag_ms": 4000.0, "source_seq": 99},
                {"source_seq": 99, "timestamp": "2026-08-16T00:01:39", "persons": [_person()],
                 "products": _products(a_id=2, b_id=1), "associations": [], "interactions": [], "events": []},
            )
            summary = recorder.close(performance_summary=_performance(), runtime_summary={"runtime_errors": 0}, readiness=_readiness())
        self.assertGreater(summary["product_id_swap_count"], 0)

    def test_false_interaction_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            recorder.record_frame(
                {"runtime_state": "READY", "pipeline_lag_ms": 4000.0, "source_seq": 99},
                {"source_seq": 99, "timestamp": "2026-08-16T00:01:39", "persons": [_person()], "products": _products(),
                 "associations": [], "interactions": [], "events": [{"event_type": "PICK_UP"}, {"event_type": "PUT_DOWN"}]},
            )
            summary = recorder.close(performance_summary=_performance(), runtime_summary={"runtime_errors": 0}, readiness=_readiness())
        self.assertEqual(1, summary["false_pick_up_count"])
        self.assertEqual(1, summary["false_put_down_count"])

    def test_queue_growth_detection_requires_monotonic_tail(self):
        self.assertTrue(monotonic_growth([0, 1, 2, 3, 4]))
        self.assertFalse(monotonic_growth([0, 1, 0, 1, 0]))

    def test_ready_e2e_gate_passes_only_with_all_hard_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            summary = recorder.close(performance_summary=_performance(), runtime_summary={"runtime_errors": 0}, readiness=_readiness())
        self.assertEqual("PASS", summary["stage10_2_performance_closure"])
        self.assertEqual("NO_LONGER_BLOCKED", summary["root_cause_classification"])

    def test_ready_e2e_over_five_seconds_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            summary = recorder.close(
                performance_summary=_performance(ready_p50=3900.0, ready_p95=5100.0),
                runtime_summary={"runtime_errors": 0}, readiness=_readiness(),
            )
        self.assertEqual("FAIL", summary["stage10_2_performance_closure"])
        self.assertFalse(summary["allow_stage10_3_af"])

    def test_serial_provider_root_cause_is_data_driven(self):
        result = classify_ready_root_cause(
            acceptance_passed=False, all_run_e2e_p95_ms=6000.0, ready_e2e_p95_ms=5500.0,
            person_p95_ms=2200.0, product_p95_ms=2300.0, oldest_frame_age_p95_ms=1200.0,
        )
        self.assertEqual("SERIAL_PROVIDER_DOMINANT", result)

    def test_stage11_remains_false_even_when_ready_gate_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._valid_recorder(directory)
            summary = recorder.close(performance_summary=_performance(), runtime_summary={"runtime_errors": 0}, readiness=_readiness())
        self.assertFalse(summary["stage11_entered"])
        self.assertTrue(summary["allow_stage10_3_af"])


if __name__ == "__main__":
    unittest.main()
