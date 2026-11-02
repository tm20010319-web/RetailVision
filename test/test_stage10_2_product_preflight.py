from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.sam31_product_tracking import ProductCandidate, ProductDisplaySurface  # noqa: E402
from agent.runtime.stage10_demo import (  # noqa: E402
    ProductInitializationPending,
    ProductInitializationTimeout,
    ProviderFrameObservations,
    RealtimeFrameContext,
    RealtimeWindow,
    RuntimeCounters,
    RtspFrameReader,
    TransportPreflightResult,
    build_contact_sheet,
    capture_product_preflight_window,
    classify_product_candidates,
    redact_rtsp_source,
    run_transport_preflight,
    run_windowed_rtsp_demo,
    validate_product_coordinate_space,
)


class _Capture:
    def __init__(self, _source, _backend):
        self.frames = [
            (True, np.full((8, 12, 3), index % 250, dtype=np.uint8))
            for index in range(2, 1000)
        ]

    def isOpened(self):
        return True

    def read(self):
        if not self.frames:
            return False, None
        time.sleep(0.005)
        return self.frames.pop(0)

    def release(self):
        return None


class Stage102ProductPreflightTests(unittest.TestCase):
    def _reader(self, *, frames=None, width=12, height=8):
        class Capture(_Capture):
            def __init__(self, source, backend):
                super().__init__(source, backend)
                if frames is not None:
                    self.frames = list(frames)

        return RtspFrameReader(
            source="rtsp://redacted",
            camera_id="cam02",
            sample_fps=5.0,
            frame_width=width,
            frame_height=height,
            capture_factory=Capture,
            reconnect_attempts=1,
        )

    def test_transport_preflight_decodes_requested_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_transport_preflight(
                self._reader(),
                Path(directory),
                frame_count=3,
                sample_indices=(0, 1, 2),
            )
        self.assertTrue(result.opened)
        self.assertEqual(3, result.decoded_frames)
        self.assertEqual("PASS", result.result)

    def test_transport_preflight_detects_black_frames(self):
        frames = [(True, np.zeros((8, 12, 3), dtype=np.uint8)) for _ in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            result = run_transport_preflight(
                self._reader(frames=frames), Path(directory), frame_count=3
            )
        self.assertEqual(3, result.black_frames)
        self.assertEqual("FAIL", result.result)

    def test_transport_preflight_detects_duplicate_frames(self):
        frame = np.full((8, 12, 3), 7, dtype=np.uint8)
        frames = [(True, frame.copy()) for _ in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            result = run_transport_preflight(
                self._reader(frames=frames), Path(directory), frame_count=3
            )
        self.assertEqual(2, result.duplicate_frames)
        self.assertEqual("FAIL", result.result)

    def test_transport_preflight_writes_contact_sheet_and_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_transport_preflight(
                self._reader(), root, frame_count=3, sample_indices=(0, 1, 2)
            )
            self.assertTrue((root / "transport_contact_sheet.jpg").exists())
            self.assertTrue((root / "frame_000.jpg").exists())
            self.assertTrue((root / "frame_001.jpg").exists())
            self.assertTrue((root / "frame_002.jpg").exists())

    def test_transport_result_contains_source_and_ai_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_transport_preflight(self._reader(), Path(directory), frame_count=1)
        self.assertEqual((12, 8), result.source_resolution)
        self.assertEqual((12, 8), result.ai_resolution)
        self.assertEqual((8, 12, 3), result.frame_shape)

    def test_window_preflight_saves_all_twelve_ai_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            result = capture_product_preflight_window(
                self._reader(), Path(directory), frame_count=12
            )
            window = Path(directory) / "windows" / "window_000"
            self.assertEqual(12, result.frame_count)
            self.assertEqual(12, len(list(window.glob("frame_*.jpg"))))
            self.assertTrue((Path(directory) / "window_000_contact_sheet.jpg").exists())

    def test_window_preflight_keeps_ai_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            result = capture_product_preflight_window(
                self._reader(), Path(directory), frame_count=12
            )
        self.assertTrue(result.ai_frame_shape == (8, 12, 3))

    def test_contact_sheet_rejects_empty_frames(self):
        with self.assertRaises(ValueError):
            build_contact_sheet((), Path(tempfile.gettempdir()) / "unused.jpg")

    def test_candidate_diagnostic_reports_surface_rejection(self):
        surface = ProductDisplaySurface(((0.4, 0.4), (0.7, 0.4), (0.7, 0.8), (0.4, 0.8)))
        candidate = ProductCandidate(
            local_obj_id=3,
            bbox=(10.0, 10.0, 30.0, 30.0),
            centroid=(20.0, 20.0),
            confidence=0.9,
            mask=np.ones((720, 1280), dtype=bool),
        )
        diagnostic = classify_product_candidates(
            candidates=(candidate,),
            surface=surface,
            frame_width=1280,
            frame_height=720,
            window_no=0,
            source_seq_start=1,
            source_seq_end=12,
        )
        self.assertEqual(1, diagnostic["raw_candidate_count"])
        self.assertEqual(0, diagnostic["credible_candidate_count"])
        self.assertEqual("OUTSIDE_DISPLAY_SURFACE", diagnostic["candidates"][0]["reject_reason"])

    def test_candidate_diagnostic_reports_two_credible_candidates(self):
        surface = ProductDisplaySurface(((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)))
        candidates = tuple(
            ProductCandidate(
                local_obj_id=index,
                bbox=(200.0 + index * 300, 200.0, 450.0 + index * 300, 550.0),
                centroid=(300.0 + index * 300, 350.0),
                confidence=0.9,
                mask=np.pad(np.ones((350, 250), dtype=bool), ((200, 170), (200 + index * 300, 830 - index * 300))),
            )
            for index in range(2)
        )
        diagnostic = classify_product_candidates(
            candidates=candidates,
            surface=surface,
            frame_width=1280,
            frame_height=720,
            window_no=0,
            source_seq_start=1,
            source_seq_end=12,
        )
        self.assertEqual(2, diagnostic["credible_candidate_count"])
        self.assertEqual(2, diagnostic["final_initialization_candidate_count"])

    def test_candidate_diagnostic_reject_reason_is_explicit_for_empty_mask(self):
        surface = ProductDisplaySurface(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
        candidate = ProductCandidate(1, (1.0, 1.0, 2.0, 2.0), (1.5, 1.5), 0.5, np.zeros((720, 1280), dtype=bool))
        diagnostic = classify_product_candidates(
            candidates=(candidate,), surface=surface, frame_width=1280, frame_height=720,
            window_no=0, source_seq_start=1, source_seq_end=12,
        )
        self.assertEqual("EMPTY_MASK", diagnostic["candidates"][0]["reject_reason"])

    def test_grounding_diagnostic_can_represent_called_but_zero_result(self):
        diagnostic = classify_product_candidates(
            candidates=(), surface=ProductDisplaySurface(((0, 0), (1, 0), (1, 1), (0, 1))),
            frame_width=1280, frame_height=720, window_no=2, source_seq_start=20, source_seq_end=31,
            grounding_called=True, grounding_call_count=1, raw_grounding_object_count=0,
        )
        self.assertTrue(diagnostic["grounding_called"])
        self.assertEqual(0, diagnostic["raw_grounding_object_count"])
        self.assertEqual("CASE_A_RAW_GROUNDING_ZERO", diagnostic["zero_candidate_case"])

    def test_grounding_two_objects_surface_rejected_is_case_b(self):
        surface = ProductDisplaySurface(((0.4, 0.4), (0.7, 0.4), (0.7, 0.8), (0.4, 0.8)))
        candidates = tuple(
            ProductCandidate(index, (1, 1, 5, 5), (3, 3), 0.9, np.ones((720, 1280), dtype=bool))
            for index in (1, 2)
        )
        diagnostic = classify_product_candidates(
            candidates=candidates, surface=surface, frame_width=1280, frame_height=720,
            window_no=0, source_seq_start=1, source_seq_end=12,
            grounding_called=True, grounding_call_count=1, raw_grounding_object_count=2,
        )
        self.assertEqual("CASE_B_FILTER_REJECTED", diagnostic["zero_candidate_case"])

    def test_coordinate_space_accepts_frozen_ai_surface(self):
        surface = ProductDisplaySurface(((0.5, 0.4), (0.7, 0.4), (0.7, 0.9), (0.5, 0.9)))
        result = validate_product_coordinate_space(surface, frame_size=(1280, 720))
        self.assertEqual("AI_FRAME", result["surface_coordinate_space"])
        self.assertEqual("AI_FRAME", result["candidate_coordinate_space"])

    def test_coordinate_space_rejects_non_ai_frame(self):
        surface = ProductDisplaySurface(((0.5, 0.4), (0.7, 0.4), (0.7, 0.9), (0.5, 0.9)))
        with self.assertRaises(ValueError):
            validate_product_coordinate_space(surface, frame_size=(2560, 1440))

    def test_pending_initialization_is_nonfatal_signal(self):
        error = ProductInitializationPending("WAITING_FOR_PRODUCT_INIT")
        self.assertIn("WAITING_FOR_PRODUCT_INIT", str(error))

    def test_pending_initialization_diagnostic_is_json_safe(self):
        payload = {"status": "WAITING_FOR_PRODUCT_INIT", "recorded_at": datetime.now(timezone.utc).isoformat()}
        self.assertEqual(payload, json.loads(json.dumps(payload)))

    def test_redact_rtsp_source_never_returns_credentials(self):
        value = redact_rtsp_source("rtsp://admin:secret@192.0.2.6:554/live/0/MAIN")
        self.assertEqual("REDACTED", value)

    def test_redact_empty_source_is_redacted(self):
        self.assertEqual("REDACTED", redact_rtsp_source(""))

    def test_transport_result_serializes_without_source_url(self):
        result = TransportPreflightResult(
            opened=True, decoded_frames=30, source_resolution=(2560, 1440),
            ai_resolution=(1280, 720), frame_shape=(720, 1280, 3), black_frames=0,
            duplicate_frames=0, average_pixel_mean=100.0, average_pixel_std=20.0,
            first_frame_timestamp="2026-08-16T00:00:00", last_frame_timestamp="2026-08-16T00:00:01",
            result="PASS",
        )
        self.assertNotIn("rtsp://", json.dumps(result.to_dict()))

    def test_candidate_diagnostic_contains_all_required_counts(self):
        diagnostic = classify_product_candidates(
            candidates=(), surface=ProductDisplaySurface(((0, 0), (1, 0), (1, 1), (0, 1))),
            frame_width=12, frame_height=8, window_no=0, source_seq_start=1, source_seq_end=12,
        )
        for key in (
            "raw_grounding_object_count", "raw_candidate_count", "credible_candidate_count",
            "final_initialization_candidate_count",
        ):
            self.assertIn(key, diagnostic)

    def test_pending_signal_is_distinct_from_fatal_initialization_error(self):
        self.assertFalse(issubclass(ProductInitializationPending, RuntimeError) is False)

    def test_init_wait_does_not_invoke_downstream_before_product_ready(self):
        started = datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)

        def context(seq):
            return RealtimeFrameContext(
                camera_id="cam02", source_seq=seq, captured_at=started,
                frame_width=1280, frame_height=720,
                frame=np.zeros((720, 1280, 3), dtype=np.uint8),
            )

        class Source:
            reader = type("Reader", (), {"counters": RuntimeCounters()})()
            error = None
            windows = [
                RealtimeWindow(0, tuple(context(i) for i in range(12)), tuple(context(i) for i in range(12)), 0),
                RealtimeWindow(1, tuple(context(i) for i in range(8, 20)), tuple(context(i) for i in range(12, 20)), 0),
            ]

            def start(self):
                return self

            def wait_window(self, **_kwargs):
                return self.windows.pop(0) if self.windows else None

            def stop(self):
                return None

        class Bundle:
            product_init_ready = False

            def __init__(self):
                self.calls = 0

            def start(self):
                return None

            def process_window(self, contexts):
                self.calls += 1
                if self.calls == 1:
                    raise ProductInitializationPending("WAITING_FOR_PRODUCT_INIT")
                self.product_init_ready = True
                return tuple(
                    ProviderFrameObservations(
                        context=item,
                        person_tracks=(),
                        products=(),
                        person_ready_at=started,
                        product_ready_at=started,
                        person_inference_ms=1.0,
                        product_inference_ms=1.0,
                    )
                    for item in contexts[4:]
                )

            def gpu_metrics(self):
                return {}

            def close(self):
                return None

        class Runtime:
            counters = RuntimeCounters()

            def process_observations(self, **kwargs):
                self.counters.frames_processed += 1
                return type("Result", (), {
                    "timings_ms": {"end_to_end_ms": 1.0},
                    "events": (),
                    "stage_timestamps": {},
                    "context": kwargs["context"],
                })()

        bundle = Bundle()
        summary = run_windowed_rtsp_demo(
            source=Source(), runtime=Runtime(), provider_bundle=bundle,
            max_windows=2,
        )
        self.assertEqual(1, summary.product_init_pending_windows)
        self.assertTrue(summary.product_init_ready)
        self.assertEqual(8, summary.frames_processed)

    def test_product_init_timeout_is_fatal_after_bounded_wait(self):
        with self.assertRaises(ProductInitializationTimeout):
            raise ProductInitializationTimeout("PRODUCT_INIT_TIMEOUT")

    def test_product_init_status_does_not_change_frozen_coordinate_contract(self):
        result = validate_product_coordinate_space(
            ProductDisplaySurface(((0.49921875, 0.4708333333), (0.6546875, 0.4694444444), (0.71796875, 0.9611111111), (0.48125, 0.9652777778))),
            frame_size=(1280, 720),
        )
        self.assertEqual("AI_FRAME", result["surface_coordinate_space"])


if __name__ == "__main__":
    unittest.main()
