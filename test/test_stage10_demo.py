from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior.person_product_association import (
    PersonAssociationObservation,
    PersonProductAssociationConfig,
    ProductAssociationObservation,
)
from agent.behavior.product_interaction import ProductInteractionConfig
from agent.behavior.sam31_behavior_adapter import Sam31BehaviorAdapter
from agent.behavior.sam31_product_tracking import (
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplayState,
    ProductDisplaySurface,
    ProductTrackStatus,
)
from agent.behavior.sam31_live_behavior import Sam31GlobalTrack
from agent.runtime.stage10_demo import (
    EventStream,
    JsonlEventSink,
    LatestFrameBuffer,
    RealtimeFrameContext,
    RuntimeCounters,
    RtspFrameReader,
    Stage10DemoRuntime,
    Stage10DemoEvent,
    Stage10FrameResult,
    Stage10OverlayRenderer,
    SyncStatus,
    build_demo_overlay_view,
    synchronize_persons,
    render_unified_overlay,
    MultiProductTrackFrameSource,
    run_rtsp_demo,
)


class _ProductSource:
    def __init__(self, product_factory):
        self.product_factory = product_factory
        self.calls = []

    def process_frame(self, context):
        self.calls.append(context)
        return (self.product_factory(context),)


class _EmptyProductSource:
    def process_frame(self, context):
        return ()


class _ErrorProductSource:
    def process_frame(self, context):
        raise RuntimeError("synthetic provider error")


class _FakeCapture:
    instances = []

    def __init__(self, _source, _backend):
        self.opened = True
        self.frames = [(True, np.full((8, 12, 3), 7, dtype=np.uint8))]
        self.released = False
        self.__class__.instances.append(self)

    def isOpened(self):
        return self.opened

    def read(self):
        if self.frames:
            return self.frames.pop(0)
        return False, None

    def release(self):
        self.released = True


class Stage10DemoTests(unittest.TestCase):
    def setUp(self):
        self.started = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)
        self.adapter = Sam31BehaviorAdapter(
            store_id="store-1",
            camera_id="cam02",
            runtime_instance_id="stage10-test",
            frame_width=1280,
            frame_height=720,
        )
        self.association_config = PersonProductAssociationConfig(
            frame_width=1280,
            frame_height=720,
            association_enter_stable_frames=1,
            association_exit_stable_frames=3,
            association_grace_seconds=1.0,
            max_sync_delta_seconds=0.12,
        )
        self.interaction_config = ProductInteractionConfig(
            frame_width=1280,
            frame_height=720,
            surface_evidence_enabled=False,
        )

    def context(self, seq=1, seconds=0.0):
        return RealtimeFrameContext(
            camera_id="cam02",
            source_seq=seq,
            captured_at=self.started + timedelta(seconds=seconds),
            frame_width=1280,
            frame_height=720,
            frame=np.zeros((720, 1280, 3), dtype=np.uint8),
        )

    def person(self, *, source_seq=1, seconds=0.0):
        return PersonAssociationObservation(
            observed_at=self.started + timedelta(seconds=seconds),
            source_seq=source_seq,
            camera_id="cam02",
            track_id=23,
            track_token="store-1:cam02:stage10-test:23",
            bbox=(0.35, 0.18, 0.60, 0.92),
            centroid=(0.475, 0.55),
            foot_point=(0.475, 0.92),
            confidence=0.95,
            role="customer",
        )

    def product(self, *, source_seq=1, seconds=0.0, x=0.53, status=ProductTrackStatus.ACTIVE, mask=True):
        return ProductAssociationObservation(
            observed_at=self.started + timedelta(seconds=seconds),
            source_seq=source_seq,
            camera_id="cam02",
            product_key="smart_toy_a",
            product_type="smart_toy",
            product_track_id=1,
            product_track_token="product:cam02:stage10-test:smart_toy_a:1",
            bbox=(0.49, 0.44, 0.57, 0.58) if mask else None,
            centroid=(x, 0.51) if mask else None,
            track_status=status,
            display_state=ProductDisplayState.IN_DISPLAY if mask else ProductDisplayState.UNOBSERVED,
            mask_present=mask,
        )

    def make_runtime(self, product_source, *, person_identity_resolver=None):
        return Stage10DemoRuntime(
            camera_id="cam02",
            person_adapter=self.adapter,
            person_detector=lambda context: (Sam31GlobalTrack(23, (448, 130, 768, 662)),),
            product_source=product_source,
            association_config=self.association_config,
            interaction_config=self.interaction_config,
            person_identity_resolver=person_identity_resolver,
        )

    def test_runtime_uses_one_frame_identity_and_exact_sync(self):
        source = _ProductSource(lambda context: self.product(source_seq=context.source_seq))
        runtime = self.make_runtime(source)

        result = runtime.process_frame(self.context(seq=101))

        self.assertEqual(101, result.context.source_seq)
        self.assertEqual(101, result.persons[0].source_seq)
        self.assertEqual(101, result.products[0].source_seq)
        self.assertEqual(SyncStatus.EXACT, result.sync[0].status)
        self.assertEqual(0.0, result.sync[0].sync_delta_ms)
        self.assertEqual("store-1:cam02:stage10-test:23", result.associations[0].person_track_token)
        self.assertEqual(1, runtime.counters.frames_processed)
        self.assertEqual(101, source.calls[0].source_seq)

    def test_timestamp_fallback_is_bounded_and_diagnosed(self):
        product = self.product(source_seq=202, seconds=1.0)
        person = self.person(source_seq=201, seconds=1.05)

        matched = synchronize_persons(
            product=product,
            persons=(person,),
            max_delta_seconds=0.12,
        )

        self.assertEqual(SyncStatus.TEMPORAL_MATCH, matched.diagnostics.status)
        self.assertEqual(50.0, matched.diagnostics.sync_delta_ms)
        self.assertEqual((person,), matched.persons)

        rejected = synchronize_persons(
            product=product,
            persons=(self.person(source_seq=201, seconds=1.5),),
            max_delta_seconds=0.12,
        )
        self.assertEqual(SyncStatus.MISSING_PERSON, rejected.diagnostics.status)
        self.assertEqual((), rejected.persons)

    def test_missing_product_preserves_sync_contract_without_fake_person(self):
        source = _ProductSource(lambda context: self.product(
            source_seq=context.source_seq,
            status=ProductTrackStatus.MISSING_PENDING,
            mask=False,
        ))
        runtime = self.make_runtime(source)

        result = runtime.process_frame(self.context(seq=300))

        self.assertEqual(SyncStatus.EXACT, result.sync[0].status)
        self.assertFalse(result.products[0].mask_present)
        self.assertEqual(ProductTrackStatus.MISSING_PENDING, result.products[0].track_status)
        self.assertEqual("product:cam02:stage10-test:smart_toy_a:1", result.products[0].product_track_token)

    def test_empty_product_frame_has_explicit_missing_product_diagnostic(self):
        runtime = self.make_runtime(_EmptyProductSource())

        result = runtime.process_frame(self.context(seq=301))

        self.assertEqual(SyncStatus.MISSING_PRODUCT, result.sync[0].status)
        self.assertEqual((), result.products)

    def test_event_stream_serialization_and_overlay_are_consumable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            sink = JsonlEventSink(path)
            stream = EventStream((sink,))
            source = _ProductSource(lambda context: self.product(source_seq=context.source_seq))
            runtime = self.make_runtime(source)
            runtime.event_stream = stream
            result = runtime.process_frame(self.context(seq=400))
            image = render_unified_overlay(result.context.frame, result)
            sink.close()

            self.assertEqual((720, 1280, 3), image.shape)
            self.assertGreater(int(np.count_nonzero(image)), 0)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual([], lines)
            self.assertIn("camera_id", runtime.state())

    def test_display_identity_enriches_output_without_changing_track_or_product_identity(self):
        source = _ProductSource(lambda context: self.product(source_seq=context.source_seq))
        runtime = self.make_runtime(
            source,
            person_identity_resolver=lambda person: {
                "member_id": "member_001",
                "member_name": "张三",
            },
        )

        payload = runtime.process_frame(self.context(seq=401)).to_dict()

        self.assertEqual("store-1:cam02:stage10-test:23", payload["persons"][0]["track_token"])
        self.assertEqual("会员：张三", payload["persons"][0]["display_name"])
        self.assertEqual("member_001", payload["persons"][0]["member_id"])
        self.assertEqual("smart_toy_a", payload["products"][0]["product_key"])
        self.assertEqual("智能熊（smart bear）", payload["products"][0]["display_name"])
        self.assertEqual(
            "store-1:cam02:stage10-test:23",
            payload["associations"][0]["associated_person_track_token"],
        )

    def test_chinese_overlay_view_is_readable_and_does_not_mutate_stage8_or_stage9(self):
        source = _ProductSource(lambda context: self.product(source_seq=context.source_seq))
        runtime = self.make_runtime(source)
        result = runtime.process_frame(self.context(seq=402))
        before = result.to_dict()

        view = build_demo_overlay_view(result)
        image = Stage10OverlayRenderer().render(result.context.frame, result)

        self.assertEqual("顾客23", view["persons"][0]["display_name"])
        self.assertEqual("智能熊｜稳定｜空闲", view["products"][0]["summary"])
        self.assertEqual("顾客23 → 智能熊", view["current_interactions"][0]["relationship"])
        self.assertEqual((720, 1280, 3), image.shape)
        self.assertGreater(int(np.count_nonzero(image)), 0)
        self.assertEqual(before, result.to_dict())

    def test_chinese_overlay_handles_missing_product_displacement(self):
        source = _ProductSource(lambda context: self.product(
            source_seq=context.source_seq,
            status=ProductTrackStatus.MISSING_PENDING,
            mask=False,
        ))
        runtime = self.make_runtime(source)
        result = runtime.process_frame(self.context(seq=403))

        view = build_demo_overlay_view(result)
        image = Stage10OverlayRenderer().render(result.context.frame, result)

        self.assertEqual("稳定 0 帧｜位移 --", view["products"][0]["detail"])
        self.assertEqual("智能熊｜暂时遮挡｜空闲", view["products"][0]["summary"])
        self.assertEqual((720, 1280, 3), image.shape)

    def test_overlay_prioritizes_pickup_over_same_frame_holding_event(self):
        source = _ProductSource(lambda context: self.product(source_seq=context.source_seq))
        runtime = self.make_runtime(source)
        result = runtime.process_frame(self.context(seq=404))
        event_values = {
            "timestamp": result.context.timestamp,
            "source_seq": result.context.source_seq,
            "camera_id": "cam02",
            "person": {"track_id": 23, "display_name": "顾客23"},
            "product": {"product_key": "smart_toy_a"},
            "interaction": {"interaction_session_id": "session-1"},
            "evidence": {},
        }
        pickup = Stage10DemoEvent(
            event_id="pickup",
            event_type="PICK_UP",
            display={"event_name": "拿起", "message": "顾客23 拿起了 智能熊"},
            **event_values,
        )
        holding = Stage10DemoEvent(
            event_id="holding",
            event_type="HOLDING",
            display={"event_name": "持有中", "message": "顾客23 正在查看 智能熊"},
            **event_values,
        )

        view = build_demo_overlay_view(replace(result, events=(pickup, holding)))

        self.assertEqual("拿起", view["events"][0]["event_name"])
        self.assertEqual("顾客23 拿起了 智能熊", view["events"][0]["message"])

    def test_multi_product_source_preserves_tracker_token_and_surface_evidence(self):
        surface = ProductDisplaySurface(
            points=((0.40, 0.35), (0.70, 0.35), (0.75, 0.95), (0.35, 0.95))
        )
        candidate = ProductCandidate(
            local_obj_id=4,
            bbox=(690.0, 400.0, 760.0, 500.0),
            centroid=(725.0, 450.0),
            confidence=0.99,
            mask=np.pad(
                np.ones((100, 70), dtype=np.uint8),
                ((400, 220), (690, 520)),
            ).astype(bool),
        )
        tracker = MultiProductTrackRuntime(
            camera_id="cam02",
            runtime_instance_id="stage10-products",
            products={"smart_toy_a": "smart_toy"},
            product_display_surface=surface,
            product_track_lost_after_windows=3,
        )
        source = MultiProductTrackFrameSource(
            tracker=tracker,
            detector=lambda _context: (candidate,),
        )
        initialized = source.initialize(
            assignments={"smart_toy_a": candidate},
            context=self.context(seq=500),
        )
        observed = source.process_frame(self.context(seq=501, seconds=0.2))

        self.assertEqual(1, initialized[0].observation.product_track_id)
        self.assertEqual(
            initialized[0].observation.product_track_token,
            observed[0].observation.product_track_token,
        )
        self.assertEqual(ProductTrackStatus.ACTIVE, observed[0].observation.track_status)
        self.assertIsNotNone(observed[0].surface_overlap_ratio)

    def test_latest_frame_buffer_reports_replacement_drop(self):
        counters = RuntimeCounters()
        buffer = LatestFrameBuffer(maxsize=1, counters=counters)
        buffer.put(self.context(seq=1))
        buffer.put(self.context(seq=2))

        self.assertEqual(1, len(buffer))
        self.assertEqual(1, counters.frames_dropped)
        self.assertEqual(2, buffer.get().source_seq)

    def test_rtsp_reader_turns_decode_failure_into_diagnostic_and_reconnects(self):
        _FakeCapture.instances = []
        reader = RtspFrameReader(
            source="rtsp://cam02",
            camera_id="cam02",
            frame_width=12,
            frame_height=8,
            capture_factory=_FakeCapture,
            reconnect_attempts=2,
        )

        first = reader.read(captured_at=self.started)
        second = reader.read_with_reconnect(captured_at=self.started + timedelta(seconds=1))

        self.assertEqual(0, first.source_seq)
        self.assertIsNotNone(second)
        self.assertEqual(1, second.source_seq)
        self.assertGreaterEqual(reader.counters.read_failures, 1)
        self.assertGreaterEqual(reader.counters.reconnects, 2)
        reader.close()

    def test_rtsp_demo_runner_keeps_processing_and_reports_latency_stats(self):
        _FakeCapture.instances = []
        reader = RtspFrameReader(
            source="rtsp://cam02",
            camera_id="cam02",
            frame_width=1280,
            frame_height=720,
            capture_factory=_FakeCapture,
            reconnect_attempts=2,
        )
        source = _ProductSource(lambda context: self.product(source_seq=context.source_seq))
        runtime = self.make_runtime(source)
        with tempfile.TemporaryDirectory() as directory:
            summary = run_rtsp_demo(
                reader=reader,
                runtime=runtime,
                overlay_path=Path(directory) / "overlay.mp4",
                max_frames=2,
            )

        self.assertEqual(2, summary.frames_processed)
        self.assertEqual(2, summary.counters["frames_processed"])
        self.assertEqual(2, summary.latency_ms["end_to_end_ms"]["count"])

    def test_rtsp_demo_runner_bounds_provider_error_attempts(self):
        _FakeCapture.instances = []
        reader = RtspFrameReader(
            source="rtsp://cam02",
            camera_id="cam02",
            frame_width=1280,
            frame_height=720,
            capture_factory=_FakeCapture,
            reconnect_attempts=1,
        )
        runtime = self.make_runtime(_ErrorProductSource())

        summary = run_rtsp_demo(reader=reader, runtime=runtime, max_frames=1)

        self.assertEqual(0, summary.frames_processed)
        self.assertEqual(1, summary.runtime_errors)


if __name__ == "__main__":
    unittest.main()
