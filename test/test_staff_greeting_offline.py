import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior_events import BehaviorEventInput, EntryEventInput
from agent.behavior_vision import TrackObservation
from agent.staff_greeting_evaluation import BehaviorMonitorConfig, GreetingShadowConfig, OfficialGreetingCore
from agent.behavior_monitor import PolygonArea
from agent.staff_greeting_offline import (
    GreetingSummaryBuilder,
    JsonlArtifactCollector,
    OfflineOverlayRenderer,
    OfflineStaffGreetingRunner,
    OpenCVPTSReader,
    _atomic_write_json,
    load_reception_camera_config,
)
from agent._staff_greeting_shadow import _GreetingShadowEvaluator


class FakeMedia:
    def __init__(self, *, valid=True):
        self.valid = valid
        self.frames_seen = []

    def validate(self):
        if not self.valid:
            raise ValueError("video_pts_non_monotonic")
        return {"width": 2560, "height": 1440, "fps": 25.0, "frame_count": 3,
                "first_raw_pts": 0, "last_raw_pts": 2, "time_base_num": 1,
                "time_base_den": 25, "presentation_timeline_status": "verified"}

    def frames(self):
        for index, pts in enumerate((0.0, 0.04, 0.08)):
            frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
            self.frames_seen.append(index)
            yield frame, pts, index


class FakeVision:
    def process_frame(self, frame):
        return []


class FakeBoxVision(FakeVision):
    def __init__(self):
        self.render_count = 0

    def render_debug_overlay(self, frame, track_state_lines):
        self.render_count += 1
        rendered = frame.copy()
        rendered[0, 0] = (0, 255, 0)
        return rendered


class FakeWriter:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(b"fake-mp4")
        self.write_count = 0
        self.release_called = False

    def write(self, frame):
        self.write_count += 1

    def release(self):
        self.release_called = True


class FakeCore:
    def __init__(self, event=None, pending_cancel=None):
        self.event = event
        self.pending_cancel = pending_cancel
        self.observations = 0

    def observe(self, observed_at, observations, entries):
        self.observations += 1
        if self.event is not None and self.observations == 2:
            return (self.event,), ()
        return (), ()

    def debug_lines(self, token):
        return ("WAITING_FOR_GREETING",)

    def cancel_all(self, observed_at, reason):
        if self.pending_cancel is None:
            return ()
        value = self.pending_cancel
        self.pending_cancel = None
        return (value,)


def _camera():
    return load_reception_camera_config(Path("behavior_cameras.example.json"), "entrance-1")


class OfflineGreetingTests(unittest.TestCase):
    def test_chinese_offline_overlay_localizes_run_state_and_banner(self):
        renderer = OfflineOverlayRenderer(
            run_id="run-zh",
            camera=_camera(),
            epoch=datetime(2000, 1, 1),
            language="zh-CN",
        )
        renderer._banners.append((0.0, 2.0, "迎宾已确认"))
        captured = []

        def capture(frame, items):
            captured.extend(items)
            return frame

        with patch(
            "agent.staff_greeting_offline.draw_overlay_texts_bgr",
            side_effect=capture,
        ):
            renderer.render(
                np.zeros((480, 800, 3), dtype=np.uint8),
                pts_seconds=1.0,
                observations=(),
                state_lines=("WAITING: NO",),
            )

        texts = [item.text for item in captured]
        self.assertIn("来源：离线 MP4", texts)
        self.assertIn("分析批次：run-zh", texts)
        self.assertIn("顾客等待迎宾：否", texts)
        self.assertIn("迎宾已确认", texts)

    def _valid_envelope(self, *, schema_version="staff_greeting_offline_v1", analysis_status="eligible"):
        return {
            "schema_version": schema_version,
            "source": "offline_mp4",
            "analysis_run_id": "run-summary",
            "presentation_timeline_status": "verified",
            "greeting_analysis_status": analysis_status,
            "event": {"assessment": "confirmed"},
        }

    def test_summary_rejects_wrong_schema_version(self):
        with self.assertRaisesRegex(ValueError, "summary_schema_version_mismatch"):
            GreetingSummaryBuilder.build(
                (self._valid_envelope(schema_version="staff_greeting_offline_v2"),),
                analysis_run_id="run-summary", camera=_camera(),
            )

    def test_summary_rejects_not_eligible_envelope(self):
        with self.assertRaisesRegex(ValueError, "summary_analysis_not_eligible"):
            GreetingSummaryBuilder.build(
                (self._valid_envelope(analysis_status="not_eligible"),),
                analysis_run_id="run-summary", camera=_camera(),
            )

    def test_summary_accepts_valid_envelope(self):
        summary = GreetingSummaryBuilder.build(
            (self._valid_envelope(),), analysis_run_id="run-summary", camera=_camera()
        )
        self.assertEqual(1, summary["total_events"])
        self.assertEqual(1, summary["confirmed_count"])

    def test_finalized_manifest_uses_atomic_commit_and_failure_is_not_finalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run_manifest.json"
            payload = {"status": "FINALIZED", "analysis_run_id": "run-atomic"}
            _atomic_write_json(path, payload)
            self.assertEqual("FINALIZED", json.loads(path.read_text())["status"])
            failed_path = Path(directory) / "failed_manifest.json"
            with patch("agent.staff_greeting_offline.os.replace", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    _atomic_write_json(failed_path, payload)
            self.assertEqual("FINALIZED", json.loads(path.read_text())["status"])
            self.assertFalse(failed_path.exists())

    def test_runner_duplicate_finalize_is_idempotent(self):
        camera = _camera()
        media = FakeMedia()
        writers = []
        with tempfile.TemporaryDirectory() as directory:
            runner = OfflineStaffGreetingRunner(
                media_reader=media, vision_adapter=FakeVision(), greeting_core=FakeCore(),
                camera=camera, output_dir=Path(directory), analysis_run_id="run-idempotent",
                writer_factory=lambda path, *_: (writers.append(FakeWriter(path)) or writers[-1]),
            )
            first = runner.run()
            events = (first.output_dir / "greeting_events.jsonl").read_text()
            summary = (first.output_dir / "greeting_summary.json").read_text()
            second = runner.run()
            self.assertEqual("FINALIZED", second.status)
            self.assertEqual(events, (second.output_dir / "greeting_events.jsonl").read_text())
            self.assertEqual(summary, (second.output_dir / "greeting_summary.json").read_text())
            self.assertEqual(3, len(media.frames_seen))

    def test_runner_uses_existing_vision_detection_overlay(self):
        camera = _camera()
        vision = FakeBoxVision()
        written = []

        class InspectingWriter(FakeWriter):
            def write(self, frame):
                super().write(frame)
                written.append(tuple(int(value) for value in frame[0, 0]))

        with tempfile.TemporaryDirectory() as directory:
            result = OfflineStaffGreetingRunner(
                media_reader=FakeMedia(), vision_adapter=vision, greeting_core=FakeCore(),
                camera=camera, output_dir=Path(directory), analysis_run_id="run-boxes",
                writer_factory=lambda path, *_: InspectingWriter(path),
            ).run()
            self.assertEqual("FINALIZED", result.status)
            self.assertEqual(3, vision.render_count)
            self.assertEqual([(0, 255, 0)] * 3, written)

    def test_verified_run_processes_every_frame_and_publishes_artifacts(self):
        camera = _camera()
        event = BehaviorEventInput(
            event_key="entry-1:greeting", event_type="staff_greeting", assessment="confirmed",
            camera_id="entrance-1", customer_track_id=2, customer_track_token="customer-2",
            staff_track_id=1, staff_track_token="staff-1", started_at=datetime(2000, 1, 1),
            ended_at=datetime(2000, 1, 1, 0, 0, 1), confidence=0.9,
        )
        media = FakeMedia()
        writers = []
        with tempfile.TemporaryDirectory() as directory:
            runner = OfflineStaffGreetingRunner(
                media_reader=media, vision_adapter=FakeVision(), greeting_core=FakeCore(event),
                camera=camera, output_dir=Path(directory), analysis_run_id="run-1",
                writer_factory=lambda path, *_: (writers.append(FakeWriter(path)) or writers[-1]),
            )
            result = runner.run()
            self.assertEqual("FINALIZED", result.status)
            self.assertEqual([0, 1, 2], media.frames_seen)
            self.assertEqual(3, writers[0].write_count)
            self.assertTrue((result.output_dir / "greeting_events.jsonl").is_file())
            self.assertTrue((result.output_dir / "greeting_summary.json").is_file())
            manifest = json.loads((result.output_dir / "run_manifest.json").read_text())
            self.assertEqual("FINALIZED", manifest["status"])
            summary = json.loads((result.output_dir / "greeting_summary.json").read_text())
            self.assertEqual(1, summary["confirmed_count"])
            self.assertEqual(1, summary["successful_greeting_count"])

    def test_pts_failure_is_diagnostics_only(self):
        camera = _camera()
        with tempfile.TemporaryDirectory() as directory:
            result = OfflineStaffGreetingRunner(
                media_reader=FakeMedia(valid=False), vision_adapter=FakeVision(), greeting_core=FakeCore(),
                camera=camera, output_dir=Path(directory), analysis_run_id="run-bad",
            ).run()
            self.assertEqual("NOT_ELIGIBLE", result.status)
            self.assertFalse((result.output_dir / "greeting_events.jsonl").exists())
            self.assertFalse((result.output_dir / "greeting_summary.json").exists())
            diagnostics = json.loads((result.output_dir / "run_diagnostics.json").read_text())
            self.assertEqual("not_eligible", diagnostics["greeting_analysis_status"])

    def test_collector_rejects_duplicate_cycle_and_identity_fields(self):
        camera = _camera()
        with tempfile.TemporaryDirectory() as directory:
            collector = JsonlArtifactCollector(Path(directory), analysis_run_id="run-1", camera=camera)
            event = BehaviorEventInput(
                event_key="entry:greeting", event_type="staff_greeting", assessment="uncertain",
                camera_id="entrance-1", customer_track_id=1, customer_track_token="customer",
                started_at=datetime(2000, 1, 1), ended_at=datetime(2000, 1, 1), details={"member_id": "must-not-be-inferred"},
            )
            collector.add(event, cycle_id="cycle-1")
            with self.assertRaisesRegex(ValueError, "duplicate_terminal_event"):
                collector.add(event, cycle_id="cycle-1")
            collector.finalize()
            payload = json.loads(collector.events_path.read_text().splitlines()[0])
            self.assertNotIn("member_id", payload)

    def test_shadow_resets_proximity_after_tracking_gap(self):
        area = _camera().greeting_area
        evaluator = _GreetingShadowEvaluator(
            camera_id="entrance-1", greeting_area=area,
            config=GreetingShadowConfig(tracking_grace_seconds=1.0, greeting_area_stable_frames=1),
        )
        entry = EntryEventInput("entry", "entrance-1", 2, "customer", datetime(2000, 1, 1), 1.0)
        customer = TrackObservation(2, "customer", "customer", (0.3, 0.8), 1.0, world_point=(0.0, 0.0), person_height=0.4)
        staff = TrackObservation(1, "staff", "staff", (0.3, 0.8), 1.0, world_point=(0.5, 0.0), person_height=0.4)
        evaluator.observe(datetime(2000, 1, 1), (customer, staff), (entry,))
        evaluator.observe(datetime(2000, 1, 1, 0, 0, 0, 600000), (customer, staff))
        pair = next(iter(evaluator._cycles["entry"].pairs.values()))
        self.assertGreater(pair.raw_proximity, 0.0)
        evaluator.observe(datetime(2000, 1, 1, 0, 0, 3), (customer, staff))
        self.assertEqual(0.0, pair.raw_proximity)

    def test_cancellation_preserves_cycle_start_pts_and_null_staff(self):
        greeting_area = PolygonArea("greeting", "greeting", ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)))
        core = OfficialGreetingCore(
            camera_id="entrance-1", entrance_area=None, greeting_area=greeting_area,
            config=BehaviorMonitorConfig(greeting_area_enter_stable_frames=1, greeting_area_exit_stable_frames=1),
            calibration_status="provisional", ground_calibration_available=True,
        )
        start = datetime(2000, 1, 1, 0, 0, 1)
        end = start + timedelta(seconds=2)
        entry = EntryEventInput("entry", "entrance-1", 2, "customer", start, 1.0)
        inside = TrackObservation(2, "customer", "customer", (0.5, 0.5), 1.0, world_point=(0.0, 0.0))
        outside = TrackObservation(2, "customer", "customer", (0.0, 0.0), 1.0, world_point=(0.0, 0.0))
        core.observe(start, (inside,), (entry,))
        events, cancellations = core.observe(end, (outside,), ())
        self.assertEqual((), events)
        self.assertEqual(1, len(cancellations))
        cancellation = cancellations[0]
        self.assertEqual(start, cancellation.started_at)
        self.assertEqual(end, cancellation.cancelled_at)
        self.assertLess(cancellation.started_at, cancellation.cancelled_at)

    def test_cancellation_artifact_maps_cycle_start_and_keeps_staff_null(self):
        camera = _camera()
        with tempfile.TemporaryDirectory() as directory:
            collector = JsonlArtifactCollector(Path(directory), analysis_run_id="run-cancel", camera=camera)
            cancellation = type("Cancellation", (), {
                "greeting_cycle_id": "cycle:greeting-cycle", "camera_id": "entrance-1",
                "customer_track_token": "customer", "started_at": datetime(2000, 1, 1),
                "cancelled_at": datetime(2000, 1, 1, 0, 0, 2),
                "cancel_reason": "customer_left_greeting_area",
            })()
            collector.add_cancellation(cancellation, run_epoch=datetime(2000, 1, 1))
            collector.finalize()
            payload = json.loads(collector.events_path.read_text().splitlines()[0])
            event = payload["event"]
            self.assertEqual("2000-01-01T00:00:00", event["started_at"])
            self.assertEqual("2000-01-01T00:00:02", event["ended_at"])
            self.assertIsNone(event["staff_track_token"])


if __name__ == "__main__":
    unittest.main()
