from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.runtime.stage10_demo import (  # noqa: E402
    AsyncRtspWindowSource,
    RealtimeFrameContext,
    JsonlMetricsWriter,
    ProviderFrameObservations,
    RealtimeWindow,
    Stage10PerformanceRecorder,
    RuntimeCounters,
    SustainedMetricsWriter,
    aggregate_acceptance_summary,
    run_windowed_rtsp_demo,
)


class Stage102PerformanceTests(unittest.TestCase):
    def test_window_selects_newest_legal_frames_when_queue_has_backlog(self):
        started = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)

        def context(seq: int) -> RealtimeFrameContext:
            return RealtimeFrameContext(
                camera_id="cam02",
                source_seq=seq,
                captured_at=started,
                frame_width=12,
                frame_height=8,
            )

        reader = type("Reader", (), {"counters": RuntimeCounters()})()
        source = AsyncRtspWindowSource(reader, max_queue_size=32)
        with source._condition:
            source._contexts.extend(context(seq) for seq in range(24))
        window = source.wait_window(timeout_seconds=0.01)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(list(range(12, 24)), [item.source_seq for item in window.fresh_contexts])
        self.assertEqual(12, window.dropped_during_window)

    def test_negative_frame_latency_is_clamped_before_aggregation(self):
        recorder = Stage10PerformanceRecorder()
        recorder.record_frame(
            {
                "source_seq": 1,
                "capture_to_provider_start_ms": -5.0,
                "capture_to_overlay_ms": -1.0,
            }
        )
        summary = recorder.close()
        self.assertEqual(0.0, summary["capture_to_provider_start_ms"]["p50"])
        self.assertEqual(0.0, summary["capture_to_overlay_ms"]["max"])

    def test_provider_summary_aggregates_downstream_timings(self):
        recorder = Stage10PerformanceRecorder()
        recorder.record_provider(
            {
                "window_no": 0,
                "person_inference_ms": 100.0,
                "product_inference_ms": 80.0,
                "association_ms": 5.0,
                "interaction_ms": 3.0,
                "overlay_ms": 2.0,
                "event_emit_ms": 1.0,
            }
        )
        summary = recorder.close()
        self.assertEqual(5.0, summary["provider"]["association_ms"]["p50"])
        self.assertEqual(1.0, summary["provider"]["event_emit_ms"]["max"])

    def test_empty_acceptance_summary_is_not_pass(self):
        summary = aggregate_acceptance_summary(())
        self.assertEqual(0, summary["scenario_count"])
        self.assertFalse(summary["all_pass"])

    def test_sustained_metrics_writer_has_explicit_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sustained_metrics.jsonl"
            writer = SustainedMetricsWriter(path)
            writer.record({"elapsed_seconds": 10, "queue_depth": 2, "frame_age_ms": 40})
            writer.close()
            writer.close()
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([{"elapsed_seconds": 10, "queue_depth": 2, "frame_age_ms": 40}], rows)

    def test_acceptance_summary_requires_all_hard_gates_to_be_zero(self):
        summary = aggregate_acceptance_summary(
            (
                {"scenario": "A", "status": "PASS"},
                {"scenario": "B", "status": "PASS", "false_pick_up": 0},
                {"scenario": "C", "status": "PASS", "false_put_down": 0},
                {"scenario": "D", "status": "PASS"},
                {"scenario": "E", "status": "PASS", "product_token_change": 1},
                {"scenario": "F", "status": "PASS"},
            )
        )
        self.assertFalse(summary["all_pass"])
        self.assertEqual(1, summary["hard_gate_totals"]["product_token_change"])
    def test_windowed_runner_records_provider_and_frame_latency(self):
        started = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)
        context = RealtimeFrameContext(
            camera_id="cam02",
            source_seq=1,
            captured_at=started,
            frame_width=12,
            frame_height=8,
        )

        class Source:
            reader = SimpleNamespace(counters=RuntimeCounters())
            error = None

            def start(self):
                return self

            def wait_window(self, *, previous_overlap=(), timeout_seconds=30.0):
                del previous_overlap, timeout_seconds
                if hasattr(self, "used"):
                    return None
                self.used = True
                return RealtimeWindow(
                    window_no=0,
                    contexts=(context,),
                    fresh_contexts=(context,),
                    dropped_frames=0,
                    window_wait_ms=25.0,
                    queue_depth_before=3,
                    queue_depth_after=1,
                    oldest_frame_age_ms=90.0,
                    newest_frame_age_ms=40.0,
                    fresh_frame_count=1,
                )

            def stop(self):
                return None

        class Bundle:
            last_window_metrics = {
                "person_inference_ms": 110.0,
                "product_inference_ms": 70.0,
                "person_session_count": 1,
                "product_session_count": 1,
                "person_track_count": 2,
                "grounding_count": 1,
            }

            def start(self):
                return None

            def process_window(self, contexts):
                return (
                    ProviderFrameObservations(
                        context=contexts[0],
                        person_tracks=(),
                        products=(),
                        person_ready_at=started,
                        product_ready_at=started,
                        person_inference_ms=110.0,
                        product_inference_ms=70.0,
                        provider_started_at=started,
                    ),
                )

            def gpu_metrics(self):
                return {"peak_vram_mb": 123.0}

            def close(self):
                return None

        class Runtime:
            counters = RuntimeCounters()

            def process_observations(self, **kwargs):
                self.counters.frames_processed += 1
                return SimpleNamespace(
                    context=kwargs["context"],
                    events=(),
                    timings_ms={
                        "person_ms": 110.0,
                        "product_ms": 70.0,
                        "association_ms": 3.0,
                        "interaction_ms": 2.0,
                        "event_emit_ms": 0.0,
                        "overlay_ms": 0.0,
                        "end_to_end_ms": 185.0,
                    },
                    stage_timestamps={
                        "capture_at": started,
                        "provider_start_at": started,
                        "person_ready_at": started,
                        "product_ready_at": started,
                        "association_ready_at": started,
                        "interaction_ready_at": started,
                        "event_emitted_at": None,
                        "overlay_ready_at": None,
                    },
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage10_2_performance"
            recorder = Stage10PerformanceRecorder(root)
            summary = run_windowed_rtsp_demo(
                source=Source(),
                runtime=Runtime(),
                provider_bundle=Bundle(),
                max_windows=1,
                performance_recorder=recorder,
            )

            self.assertEqual(1, summary.frames_processed)
            performance = json.loads(
                (root / "performance_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, performance["windows"])
            self.assertEqual(110.0, performance["provider"]["person_inference_ms"]["p50"])
            self.assertEqual(185.0, performance["capture_to_overlay_ms"]["p50"])

    def test_window_reports_queue_age_and_freshness_metrics(self):
        started = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)

        def context(seq: int) -> RealtimeFrameContext:
            return RealtimeFrameContext(
                camera_id="cam02",
                source_seq=seq,
                captured_at=started.replace(microsecond=seq * 1000),
                frame_width=12,
                frame_height=8,
            )

        reader = type("Reader", (), {"counters": RuntimeCounters()})()
        source = AsyncRtspWindowSource(reader, max_queue_size=12)
        with source._condition:
            source._contexts.extend(context(seq) for seq in range(13))
        window = source.wait_window(timeout_seconds=0.01)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(13, window.queue_depth_before)
        self.assertEqual(0, window.queue_depth_after)
        self.assertEqual(1, window.dropped_during_window)
        self.assertEqual(12, window.fresh_frame_count)
        self.assertGreaterEqual(window.oldest_frame_age_ms, 0.0)
        self.assertGreaterEqual(window.newest_frame_age_ms, 0.0)
        self.assertGreaterEqual(window.window_wait_ms, 0.0)

    def test_metrics_writer_flushes_and_recorder_writes_required_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "stage10_2_performance"
            recorder = Stage10PerformanceRecorder(root)
            recorder.record_window(
                {
                    "window_no": 0,
                    "window_wait_ms": 12.0,
                    "oldest_frame_age_ms": 80.0,
                    "newest_frame_age_ms": 20.0,
                }
            )
            recorder.record_frame(
                {
                    "source_seq": 7,
                    "capture_to_overlay_ms": 140.0,
                }
            )
            summary = recorder.close()
            recorder.close()

            self.assertEqual(1, summary["windows"])
            self.assertEqual(1, summary["frames"])
            self.assertEqual(12.0, summary["window_wait_ms"]["p50"])
            self.assertEqual(140.0, summary["capture_to_overlay_ms"]["max"])
            for name in (
                "window_timing.jsonl",
                "frame_latency.jsonl",
                "queue_metrics.jsonl",
                "provider_metrics.jsonl",
                "performance_summary.json",
            ):
                self.assertTrue((root / name).exists(), name)
            self.assertEqual(
                1,
                len((root / "window_timing.jsonl").read_text(encoding="utf-8").splitlines()),
            )
            self.assertEqual(
                summary,
                json.loads((root / "performance_summary.json").read_text(encoding="utf-8")),
            )

    def test_metrics_writer_rejects_writes_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = JsonlMetricsWriter(Path(directory) / "metrics.jsonl")
            writer.write({"recorded_at": datetime.now(timezone.utc).isoformat()})
            writer.close()
            with self.assertRaises(RuntimeError):
                writer.write({"late": True})


if __name__ == "__main__":
    unittest.main()
