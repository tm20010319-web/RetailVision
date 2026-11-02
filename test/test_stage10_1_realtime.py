from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.sam31_live_behavior import Sam31GlobalTrack
from agent.behavior.sam31_product_tracking import (
    ProductCandidate,
    ProductDisplayState,
    ProductDisplaySurface,
    ProductTrackStatus,
)
from agent.behavior.sam31_realtime_providers import (
    Sam31RealtimePersonProvider,
    Sam31RealtimeProductProvider,
)
from agent.runtime.stage10_demo import (
    AsyncRtspWindowSource,
    FatalStage10Error,
    ProviderFrameObservations,
    RealtimeFrameContext,
    RealtimeWindow,
    RuntimeCounters,
    RuntimeErrorDiagnostic,
    RtspFrameReader,
    Stage10DemoRuntime,
    Stage10ProductObservation,
    run_windowed_rtsp_demo,
)
from agent.behavior.person_product_association import (
    PersonProductAssociationConfig,
    ProductAssociationObservation,
)
from agent.behavior.product_interaction import ProductInteractionConfig
from agent.behavior.sam31_behavior_adapter import Sam31BehaviorAdapter


class _ProductSource:
    def process_frame(self, _context):
        return ()


class _SequenceCapture:
    def __init__(self, _source, _backend):
        self.frames = [
            (True, np.zeros((8, 12, 3), dtype=np.uint8))
            for _ in range(3)
        ]

    def isOpened(self):
        return True

    def read(self):
        if self.frames:
            return self.frames.pop(0)
        return False, None

    def release(self):
        return None


class _Runtime:
    def __init__(self):
        self.calls = []
        self.counters = RuntimeCounters()

    def process_observations(self, *, context, person_tracks, product_inputs, **kwargs):
        self.calls.append((context.source_seq, tuple(person_tracks), tuple(product_inputs)))
        self.counters.frames_processed += 1
        return type("Result", (), {
            "context": context,
            "timings_ms": {"end_to_end_ms": 1.0},
            "events": (),
            "stage_timestamps": {},
        })()


class _WindowSource:
    def __init__(self, windows):
        self.windows = list(windows)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return self

    def wait_window(self, *, previous_overlap=(), timeout_seconds=30.0):
        if not self.windows:
            return None
        return self.windows.pop(0)

    @property
    def error(self):
        return None

    def stop(self):
        self.stopped = True


class _Bundle:
    def __init__(self, outputs=None, error=None):
        self.outputs = outputs or []
        self.error = error
        self.calls = []

    def process_window(self, contexts):
        self.calls.append(tuple(item.source_seq for item in contexts))
        if self.error is not None:
            raise self.error
        return self.outputs.pop(0)

    def gpu_metrics(self):
        return {"peak_vram_mb": 12.0}

    def close(self):
        return None


def _context(seq: int, started: datetime) -> RealtimeFrameContext:
    return RealtimeFrameContext(
        camera_id="cam02",
        source_seq=seq,
        captured_at=started + timedelta(seconds=seq / 5),
        frame_width=1280,
        frame_height=720,
        frame=np.zeros((720, 1280, 3), dtype=np.uint8),
    )


def _provider_output(context: RealtimeFrameContext) -> ProviderFrameObservations:
    return ProviderFrameObservations(
        context=context,
        person_tracks=(Sam31GlobalTrack(7, (10.0, 10.0, 100.0, 200.0)),),
        products=(),
        person_ready_at=context.captured_at + timedelta(milliseconds=20),
        product_ready_at=context.captured_at + timedelta(milliseconds=30),
        person_inference_ms=20.0,
        product_inference_ms=10.0,
    )


class Stage101RealtimeTests(unittest.TestCase):
    def setUp(self):
        self.started = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)

    def test_provider_outputs_preserve_first_and_subsequent_window_identity(self):
        first = tuple(_context(seq, self.started) for seq in range(12))
        second_fresh = tuple(_context(seq, self.started) for seq in range(12, 20))
        windows = (
            RealtimeWindow(0, first, first, 0),
            RealtimeWindow(1, first[-4:] + second_fresh, second_fresh, 0),
        )
        outputs = [
            tuple(_provider_output(item) for item in first),
            tuple(_provider_output(item) for item in second_fresh),
        ]
        source = _WindowSource(windows)
        bundle = _Bundle(outputs=outputs)
        runtime = _Runtime()

        summary = run_windowed_rtsp_demo(
            source=source,
            runtime=runtime,
            provider_bundle=bundle,
            max_windows=2,
        )

        self.assertEqual(list(range(20)), [item[0] for item in runtime.calls])
        self.assertEqual(
            [tuple(range(12)), tuple(range(8, 20))],
            bundle.calls,
        )
        self.assertEqual(20, summary.frames_processed)
        self.assertEqual(2, summary.window_count)
        self.assertEqual(12.0, summary.provider_metrics["peak_vram_mb"])
        self.assertTrue(source.started)
        self.assertTrue(source.stopped)

    def test_windowed_runner_records_delayed_provider_exception(self):
        contexts = tuple(_context(seq, self.started) for seq in range(12))
        source = _WindowSource((RealtimeWindow(0, contexts, contexts, 0),))
        bundle = _Bundle(error=RuntimeError("provider exploded"))

        summary = run_windowed_rtsp_demo(
            source=source,
            runtime=_Runtime(),
            provider_bundle=bundle,
            max_windows=1,
        )

        self.assertEqual(1, summary.runtime_errors)
        self.assertEqual("RuntimeError", summary.runtime_diagnostics[0].exception_type)
        self.assertEqual("provider exploded", summary.runtime_diagnostics[0].exception_message)
        self.assertEqual("provider_bundle", summary.runtime_diagnostics[0].stage)

    def test_windowed_runner_fails_fast_on_fatal_provider_error(self):
        contexts = tuple(_context(seq, self.started) for seq in range(12))
        source = _WindowSource((RealtimeWindow(0, contexts, contexts, 0),))

        with self.assertRaises(FatalStage10Error):
            run_windowed_rtsp_demo(
                source=source,
                runtime=_Runtime(),
                provider_bundle=_Bundle(error=FatalStage10Error("bad model")),
                max_windows=1,
            )

    def test_runtime_error_diagnostic_is_serializable(self):
        diagnostic = RuntimeErrorDiagnostic(
            source_seq=4,
            timestamp=self.started,
            exception_type="RuntimeError",
            exception_message="boom",
            stage="provider_bundle",
            provider="fake",
            traceback_text="trace",
        )
        self.assertEqual("provider_bundle", diagnostic.to_dict()["stage"])
        self.assertEqual(self.started.replace(tzinfo=None).isoformat(), diagnostic.to_dict()["timestamp"])

    def test_bounded_async_window_drop_is_counted(self):
        reader = type("Reader", (), {"counters": RuntimeCounters()})()
        source = AsyncRtspWindowSource(reader, max_queue_size=12)
        contexts = [_context(seq, self.started) for seq in range(13)]
        with source._condition:
            source._contexts.extend(contexts)
        window = source.wait_window(timeout_seconds=0.01)
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(12, len(window.fresh_contexts))
        self.assertEqual(1, window.dropped_frames)
        self.assertEqual(1, reader.counters.frames_dropped)

    def test_async_source_counts_unconsumed_shutdown_frames_as_dropped(self):
        reader = type("Reader", (), {
            "counters": RuntimeCounters(),
            "close": lambda self: None,
        })()
        source = AsyncRtspWindowSource(reader, max_queue_size=12)
        with source._condition:
            source._contexts.extend(
                _context(seq, self.started) for seq in range(3)
            )
        source.stop()
        self.assertEqual(3, reader.counters.frames_dropped)
        self.assertEqual(0, len(source._contexts))

    def test_person_provider_returns_every_fresh_frame_and_closes_sessions(self):
        @dataclass
        class SampledFrame:
            sample_id: int
            source_seq: int
            wall_time: float
            frame: np.ndarray

        closed = []
        base = SimpleNamespace(SampledFrame=SampledFrame)

        def start_session(_predictor):
            return f"session-{len(closed)}"

        def close_session(_predictor, session_id):
            closed.append(session_id)

        def run_window(**kwargs):
            session_id = base.start_session(kwargs["predictor"])
            fresh = kwargs["window_items"] if kwargs["window_no"] == 0 else kwargs["window_items"][4:]
            for item in fresh:
                kwargs["behavior_publisher"].publish(item, ({
                    "global_track_id": 23,
                    "bbox": [100.0, 120.0, 300.0, 600.0],
                    "centroid": [200.0, 360.0],
                },))
            return {
                "session_id": session_id,
                "new_overlap_bank": {0: {23: np.ones((1, 1), dtype=bool)}},
                "next_global_id": kwargs["next_global_id"],
                "next_empty_scene_streak": 0,
                "next_empty_scene_last_poll_wall_time": None,
            }

        base.start_session = start_session
        base.close_session = close_session
        base.run_window = run_window
        provider = Sam31RealtimePersonProvider(base_module=base, predictor=object())
        first = tuple(_context(seq, self.started) for seq in range(12))
        second = first[-4:] + tuple(_context(seq, self.started) for seq in range(12, 20))

        first_result = provider.process_window(first)
        second_result = provider.process_window(second)

        self.assertEqual(list(range(12)), list(first_result))
        self.assertEqual(list(range(12, 20)), list(second_result))
        self.assertEqual(23, first_result[0][0].global_track_id)
        self.assertEqual(["session-0", "session-1"], closed)

    def test_product_provider_uses_configured_a_b_candidate_ids(self):
        @dataclass
        class SampledFrame:
            sample_id: int
            source_seq: int
            wall_time: float
            frame: np.ndarray

        base = SimpleNamespace(SampledFrame=SampledFrame)
        candidates = (
            ProductCandidate(4, (640.0, 350.0, 700.0, 430.0), (670.0, 390.0), 0.9, np.ones((720, 1280), dtype=bool)),
            ProductCandidate(9, (720.0, 350.0, 790.0, 430.0), (755.0, 390.0), 0.9, np.ones((720, 1280), dtype=bool)),
        )
        selected = []
        module = SimpleNamespace()
        module.PRODUCT_KEYS = ("smart_toy_a", "smart_toy_b")
        module.ProbeState = lambda: SimpleNamespace()
        module.SingleTextGroundingPerWindow = lambda: object()
        module.LiveProbeClock = lambda _started: object()
        module.select_initial_assignments = lambda _frame, _surface, _candidates: {}

        def process_window(**kwargs):
            assignments = module.select_initial_assignments(
                kwargs["window_items"][0].frame,
                kwargs["surface"],
                candidates,
            )
            selected.append({key: value.local_obj_id for key, value in assignments.items()})
            for item in kwargs["window_items"]:
                rows = []
                for key, candidate in assignments.items():
                    rows.append({
                        "timestamp": datetime.fromtimestamp(item.wall_time, tz=timezone.utc).isoformat(),
                        "source_seq": item.source_seq,
                        "product_key": key,
                        "product_type": "smart_toy",
                        "product_track_id": 1,
                        "product_track_token": f"product:cam02:test:{key}:1",
                        "bbox": list(candidate.bbox),
                        "centroid": [candidate.centroid[0] / 1280, candidate.centroid[1] / 720],
                        "track_status": "ACTIVE",
                        "display_state": "IN_DISPLAY",
                        "mask_present": True,
                        "surface_overlap_ratio": 0.8,
                        "recovered_from_missing_pending": False,
                    })
                kwargs["artifacts"].append_frame(rows=rows, frame=item.frame, candidates=assignments)

        module.process_window = process_window
        provider = Sam31RealtimeProductProvider(
            base_module=base,
            product_module=module,
            predictor=object(),
            config={},
            surface=ProductDisplaySurface(((0.4, 0.3), (0.8, 0.3), (0.8, 0.9), (0.4, 0.9))),
            runtime=object(),
            candidate_assignments={"smart_toy_a": 4, "smart_toy_b": 9},
        )
        contexts = tuple(_context(seq, self.started) for seq in range(12))

        result = provider.process_window(contexts)

        self.assertEqual({"smart_toy_a": 4, "smart_toy_b": 9}, selected[0])
        self.assertEqual(list(range(12)), list(result))
        self.assertEqual(2, len(result[0]))
        self.assertEqual("product:cam02:test:smart_toy_a:1", result[0][0].observation.product_track_token)

    def test_product_provider_rejects_duplicate_and_missing_assignments(self):
        module = SimpleNamespace(PRODUCT_KEYS=("smart_toy_a", "smart_toy_b"))
        common = {
            "base_module": SimpleNamespace(),
            "product_module": module,
            "predictor": object(),
            "config": {},
            "surface": ProductDisplaySurface(((0.4, 0.3), (0.8, 0.3), (0.8, 0.9), (0.4, 0.9))),
            "runtime": object(),
        }
        with self.assertRaisesRegex(FatalStage10Error, "same candidate"):
            Sam31RealtimeProductProvider(
                **common,
                candidate_assignments={"smart_toy_a": 4, "smart_toy_b": 4},
            )
        with self.assertRaisesRegex(FatalStage10Error, "both smart_toy_a and smart_toy_b"):
            Sam31RealtimeProductProvider(
                **common,
                candidate_assignments={"smart_toy_a": 4},
            )

    def test_product_provider_accepts_three_configuration_driven_keys(self):
        keys = ("dense_a", "dense_b", "dense_c")
        module = SimpleNamespace(
            PRODUCT_KEYS=keys,
            ProbeState=lambda: SimpleNamespace(),
            SingleTextGroundingPerWindow=lambda: object(),
        )
        provider = Sam31RealtimeProductProvider(
            base_module=SimpleNamespace(),
            product_module=module,
            predictor=object(),
            config={
                "selected_product_keys": list(keys),
                "products": {key: {"product_type": "smart_toy"} for key in keys},
            },
            surface=ProductDisplaySurface(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0))),
            runtime=object(),
            candidate_assignments={key: index for index, key in enumerate(keys, start=10)},
        )

        self.assertEqual(keys, provider.product_keys)
        self.assertEqual({"dense_a": 10, "dense_b": 11, "dense_c": 12}, provider.candidate_assignments)

    def test_rtsp_counter_semantics_distinguish_decode_sample_and_drop(self):
        reader = RtspFrameReader(
            source="rtsp://redacted",
            camera_id="cam02",
            sample_fps=5.0,
            frame_width=12,
            frame_height=8,
            capture_factory=_SequenceCapture,
            reconnect_attempts=1,
        )
        decoded = reader.read(captured_at=self.started)
        self.assertIsNotNone(decoded)
        self.assertEqual((1, 0, 0), (
            reader.counters.frames_received,
            reader.counters.frames_sampled,
            reader.counters.frames_dropped,
        ))

        sampled_reader = RtspFrameReader(
            source="rtsp://redacted",
            camera_id="cam02",
            sample_fps=5.0,
            frame_width=12,
            frame_height=8,
            capture_factory=_SequenceCapture,
            reconnect_attempts=1,
        )
        with patch("agent.runtime.stage10_demo.time.monotonic", side_effect=(0.0, 0.05, 0.30)):
            self.assertIsNotNone(sampled_reader.read_sampled(captured_at=self.started))
            self.assertIsNotNone(sampled_reader.read_sampled(captured_at=self.started))
        self.assertEqual((3, 2, 1), (
            sampled_reader.counters.frames_received,
            sampled_reader.counters.frames_sampled,
            sampled_reader.counters.frames_dropped,
        ))

    def test_rtsp_warmup_drops_startup_frames_before_sampling(self):
        reader = RtspFrameReader(
            source="rtsp://redacted",
            camera_id="cam02",
            sample_fps=5.0,
            frame_width=12,
            frame_height=8,
            capture_factory=_SequenceCapture,
            reconnect_attempts=1,
            warmup_seconds=1.0,
        )
        with patch(
            "agent.runtime.stage10_demo.time.monotonic",
            side_effect=(0.0, 0.1, 0.2, 1.1),
        ):
            context = reader.read_sampled(captured_at=self.started)
        self.assertIsNotNone(context)
        self.assertEqual(2, context.source_seq)
        self.assertEqual((3, 1, 2), (
            reader.counters.frames_received,
            reader.counters.frames_sampled,
            reader.counters.frames_dropped,
        ))

    def test_no_event_timestamp_is_null_and_event_latency_uses_capture_time(self):
        adapter = Sam31BehaviorAdapter(
            store_id="store-1",
            camera_id="cam02",
            runtime_instance_id="stage10-event-test",
            frame_width=1280,
            frame_height=720,
        )
        runtime = Stage10DemoRuntime(
            camera_id="cam02",
            person_adapter=adapter,
            person_detector=lambda _context: (),
            product_source=_ProductSource(),
            association_config=PersonProductAssociationConfig(
                frame_width=1280,
                frame_height=720,
                association_enter_stable_frames=1,
                association_exit_stable_frames=3,
                association_grace_seconds=1.0,
                max_sync_delta_seconds=0.12,
            ),
            interaction_config=ProductInteractionConfig(
                frame_width=1280,
                frame_height=720,
                motion_window_frames=5,
                motion_enter_displacement_pixels=12.0,
                motion_exit_displacement_pixels=6.0,
                motion_enter_stable_frames=2,
                motion_exit_stable_frames=3,
                pickup_confirm_frames=2,
                putdown_confirm_frames=3,
                stable_baseline_min_frames=5,
                minimum_holding_seconds=1.0,
                surface_evidence_enabled=False,
            ),
        )
        capture_start = datetime.now(timezone.utc) - timedelta(seconds=10)
        results = []
        positions = [700.0] * 8 + [700.0, 750.0, 780.0, 805.0, 825.0]
        for seq, x in enumerate(positions):
            context = RealtimeFrameContext(
                camera_id="cam02",
                source_seq=seq,
                captured_at=capture_start + timedelta(seconds=seq * 0.2),
                frame_width=1280,
                frame_height=720,
                frame=np.zeros((720, 1280, 3), dtype=np.uint8),
            )
            product = ProductAssociationObservation(
                observed_at=context.timestamp,
                source_seq=seq,
                camera_id="cam02",
                product_key="smart_toy_a",
                product_type="smart_toy",
                product_track_id=1,
                product_track_token="product:cam02:event-test:smart_toy_a:1",
                bbox=((x - 35) / 1280, 395 / 720, (x + 35) / 1280, 465 / 720),
                centroid=(x / 1280, 430 / 720),
                track_status=ProductTrackStatus.ACTIVE,
                display_state=ProductDisplayState.IN_DISPLAY,
                mask_present=True,
            )
            results.append(runtime.process_observations(
                context=context,
                person_tracks=(Sam31GlobalTrack(23, (448, 130, 768, 662)),),
                product_inputs=(product,),
            ))

        self.assertIsNone(results[0].stage_timestamps["event_emitted_at"])
        emitted = [item for item in results if item.events]
        self.assertTrue(emitted)
        self.assertIsNotNone(emitted[0].stage_timestamps["event_emitted_at"])
        self.assertGreater(emitted[0].timings_ms["event_latency_ms"], 5000.0)


if __name__ == "__main__":
    unittest.main()
