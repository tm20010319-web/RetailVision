from __future__ import annotations

import unittest
from datetime import datetime, timedelta
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.behavior.behavior_events import BehaviorEventStore, TrafficQueryService
from agent.behavior.behavior_monitor import (
    BehaviorMonitor,
    BehaviorMonitorConfig,
    PolygonArea,
    TrackObservation,
)
from agent.config import Settings
from agent.identity.camera_track_identity import CameraTrackIdentityCache, TrackSnapshotBuffer
from agent.visits.customer_entry_visit import CustomerEntryVisit
from agent.schema_evolution import evolve_schema
from agent.models import BehaviorEvent, CustomerEntryEvent, GreetingCycle, Visit
from agent.behavior.staff_greeting_evaluation import StaffGreetingEvaluation
from pipelines.behavior_runtime import (
    BehaviorCameraRuntime,
    BehaviorCaptureRunner,
    BehaviorRuntime,
    create_behavior_runtime,
)
from run_behavior_runtime import build_runner, validate_runtime_settings


class FakeAdapter:
    def __init__(self, observations):
        self.observations = observations
        self.frames = []

    def process_frame(self, frame):
        self.frames.append(frame)
        return list(self.observations)


class FakeCapture:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        try:
            return True, next(self.frames)
        except StopIteration:
            return False, None

    def release(self):
        self.released = True


class SequencedAdapter:
    def __init__(self, observations):
        self.observations = iter(observations)

    def process_frame(self, _frame):
        return list(next(self.observations))


class BehaviorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_background_runner_rejects_camera_exit_during_startup(self):
        runtime = MagicMock()
        runtime.cameras = {"camera-1": object()}
        runner = BehaviorCaptureRunner(
            runtime,
            {"camera-1": "0"},
            capture_factory=lambda _source: FakeCapture([]),
        )

        with self.assertRaisesRegex(RuntimeError, "stopped during startup"):
            runner.start()

    def test_settings_keep_existing_runtime_fields_with_dual_model_defaults(self):
        for name in (
            "ENTRY_POLICY_ALLOW_RECOGNITION_FALLBACK",
            "BEHAVIOR_PERSON_MODEL_PATH",
            "BEHAVIOR_STAFF_VEST_MODEL_PATH",
            "BEHAVIOR_STAFF_VEST_CONFIDENCE",
            "FACE_SPATIAL_MIN_CONTAINMENT",
            "FACE_SPATIAL_UPPER_BODY_RATIO",
            "VISIT_ACTIVITY_REFRESH_INTERVAL_SECONDS",
        ):
            self.assertTrue(hasattr(Settings, name), name)
        self.assertTrue(Settings.BEHAVIOR_PERSON_MODEL_PATH.strip())
        self.assertTrue(Settings.BEHAVIOR_STAFF_VEST_MODEL_PATH.strip())
        self.assertGreaterEqual(Settings.BEHAVIOR_STAFF_VEST_CONFIDENCE, 0)
        self.assertLessEqual(Settings.BEHAVIOR_STAFF_VEST_CONFIDENCE, 1)
        self.assertEqual(0.8, Settings.FACE_SPATIAL_MIN_CONTAINMENT)
        self.assertEqual(0.45, Settings.FACE_SPATIAL_UPPER_BODY_RATIO)
        self.assertEqual(5.0, Settings.VISIT_ACTIVITY_REFRESH_INTERVAL_SECONDS)

    def test_runtime_composes_entry_lookup_from_shared_exact_identity_cache(self):
        cache = CameraTrackIdentityCache()
        camera = SimpleNamespace(
            camera_id="entrance-1",
            store_id="store-1",
            staff_vest_enabled=False,
        )
        with patch(
            "pipelines.behavior_runtime.create_behavior_camera_runtime",
            return_value=MagicMock(),
        ) as factory:
            create_behavior_runtime(
                cameras=(camera,),
                session_factory=self.sessions,
                person_model_path="person.pt",
                staff_vest_model_path=None,
                pose_model_path="pose.pt",
                identity_association_mode="new",
                track_identity_cache=cache,
                runtime_instance_id="runtime-1",
            )

        resolver = factory.call_args.kwargs["entry_track_identity_resolver"]
        with patch.object(cache, "resolve_exact", return_value=None) as resolve:
            resolver(SimpleNamespace(
                tracker_track_id=7,
                track_token="store-1:entrance-1:runtime-1:7",
            ))
        resolve.assert_called_once_with(
            store_id="store-1",
            camera_id="entrance-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
        )

    def test_camera_runtime_persists_adapter_entry_events(self):
        inside = TrackObservation(
            track_id=1,
            track_token="store-1:entrance-1:runtime-1:1",
            role="customer",
            foot_point=(0.5, 0.5),
            confidence=0.95,
        )
        outside = TrackObservation(
            track_id=1,
            track_token="store-1:entrance-1:runtime-1:1",
            role="customer",
            foot_point=(0.5, 0.1),
            confidence=0.95,
        )
        trigger = TrackObservation(
            track_id=1,
            track_token="store-1:entrance-1:runtime-1:1",
            role="customer",
            foot_point=(0.5, 0.3),
            confidence=0.95,
        )
        store = BehaviorEventStore(self.sessions)
        monitor = BehaviorMonitor(
            camera_id="entrance-1",
            entrance_area=PolygonArea(
                "entrance", "entrance", ((0, 0.2), (1, 0.2), (1, 0.4), (0, 0.4))
            ),
            config=BehaviorMonitorConfig(
                outside_stable_frames=1,
                inside_stable_frames=1,
            ),
            behavior_sink=store.record_behavior,
        )
        adapter = SequencedAdapter(([outside], [trigger], [inside], [inside]))
        runtime = BehaviorCameraRuntime(
            adapter,
            monitor,
            customer_entry_visit=CustomerEntryVisit(self.sessions),
            entry_store_id="store-1",
            entry_runtime_session_id="runtime-1",
        )

        runtime.process_frame("outside", datetime(2026, 7, 27, 9, 59, 59))
        runtime.process_frame("trigger", datetime(2026, 7, 27, 10))
        entries, behaviors = runtime.process_frame(
            "inside", datetime(2026, 7, 27, 10, 0, 1)
        )
        retry_entries, _ = runtime.process_frame(
            "inside-retry", datetime(2026, 7, 27, 10, 0, 2)
        )

        self.assertEqual(1, len(entries))
        self.assertEqual([], retry_entries)
        self.assertEqual([], behaviors)
        db = self.sessions()
        try:
            self.assertEqual(1, db.query(CustomerEntryEvent).count())
            self.assertEqual(1, db.query(Visit).count())
            entry = db.query(CustomerEntryEvent).one()
            self.assertEqual("store-1", entry.store_id)
            self.assertEqual("runtime-1", entry.runtime_session_id)
        finally:
            db.close()

    def test_camera_runtime_publishes_normalized_full_frame_track_bbox(self):
        observed_at = datetime(2026, 8, 8, 12, 0, 0)
        observation = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.6, 0.75),
            confidence=0.9,
        )
        adapter = FakeAdapter([observation])
        adapter.store_id = "store-1"
        adapter.camera_id = "entrance-1"
        adapter.runtime_id = "runtime-1"
        adapter.last_person_tracks = (
            SimpleNamespace(
                track_id=7,
                track_token=observation.track_token,
                bbox=(1280.0, 360.0, 1920.0, 1080.0),
                confidence=0.9,
                is_frame_clipped=False,
            ),
        )
        snapshots = TrackSnapshotBuffer(clock=lambda: observed_at)
        runtime = BehaviorCameraRuntime(
            adapter,
            SimpleNamespace(process=lambda *_args: ([], [])),
            identity_association_mode="new",
            track_snapshot_buffer=snapshots,
        )

        runtime.process_frame(np.zeros((1440, 2560, 3), dtype=np.uint8), observed_at)

        snapshot = snapshots.snapshots(
            store_id="store-1",
            camera_id="entrance-1",
            runtime_id="runtime-1",
            occurred_at=observed_at,
        )[0]
        self.assertEqual((0.5, 0.25, 0.75, 0.75), snapshot.bbox)
        self.assertEqual("normalized_full_frame", snapshot.coordinate_space)
        self.assertEqual((2560, 1440), snapshot.frame_size)

    def test_camera_runtime_observes_exact_track_activity_without_db_work(self):
        observed_at = datetime(2026, 8, 10, 9, 30)
        token = "store-1:entrance-1:runtime-1:7"
        adapter = FakeAdapter([])
        adapter.store_id = "store-1"
        adapter.camera_id = "entrance-1"
        adapter.runtime_id = "runtime-1"
        adapter.last_person_tracks = (
            SimpleNamespace(
                track_id=7,
                track_token=token,
                bbox=(10.0, 20.0, 30.0, 40.0),
                confidence=0.9,
                is_frame_clipped=False,
            ),
        )
        refresher = MagicMock()
        runtime = BehaviorCameraRuntime(
            adapter,
            SimpleNamespace(process=lambda *_args: ([], [])),
            identity_association_mode="new",
            visit_activity_refresher=refresher,
        )

        runtime.process_frame(np.zeros((100, 100, 3), dtype=np.uint8), observed_at)

        refresher.observe_track.assert_called_once_with(
            store_id="store-1",
            camera_id="entrance-1",
            runtime_instance_id="runtime-1",
            track_id=7,
            track_token=token,
            observed_at=observed_at,
        )

    def test_greeting_decision_context_is_persisted_with_evidence_clip(self):
        store = BehaviorEventStore(self.sessions)
        entrance_area = PolygonArea(
            "entrance", "entrance", ((0, 0.2), (0.8, 0.2), (0.8, 0.4), (0, 0.4))
        )
        greeting_area = PolygonArea(
            "greeting", "greeting", ((0.4, 0.4), (0.8, 0.4), (0.8, 1), (0.4, 1))
        )
        monitor = BehaviorMonitor(
            camera_id="entrance-1",
            entrance_area=entrance_area,
            greeting_area=greeting_area,
            config=BehaviorMonitorConfig(
                outside_stable_frames=1,
                inside_stable_frames=1,
            ),
            behavior_sink=store.record_behavior,
            calibration_status="provisional",
        )
        staff_greeting = StaffGreetingEvaluation(
            camera_id="entrance-1",
            entrance_area=entrance_area,
            greeting_area=greeting_area,
            config=BehaviorMonitorConfig(outside_stable_frames=1, inside_stable_frames=1),
            behavior_sink=store.record_behavior,
            calibration_status="provisional",
        )
        customer_outside = TrackObservation(
            track_id=1,
            track_token="store-1:entrance-1:runtime-1:1",
            role="customer",
            foot_point=(0.5, 0.1),
            world_point=(2.0, 0.0),
            confidence=0.95,
        )
        customer_inside = TrackObservation(
            track_id=1,
            track_token=customer_outside.track_token,
            role="customer",
            foot_point=(0.2, 0.6),
            world_point=(2.0, 0.0),
            confidence=0.95,
        )

        def greeting_customer():
            return TrackObservation(
                track_id=1,
                track_token=customer_outside.track_token,
                role="customer",
                foot_point=(0.6, 0.8),
                world_point=(2.0, 0.0),
                confidence=0.95,
            )

        def greeting_staff(world, foot=(0.9, 0.5)):
            return TrackObservation(
                track_id=8,
                track_token="store-1:entrance-1:runtime-1:8",
                role="staff",
                foot_point=foot,
                world_point=world,
                confidence=0.96,
                role_source="staff_vest_yolo",
                role_confidence=0.91,
            )

        now = datetime(2026, 7, 27, 10)
        adapter = SequencedAdapter((
            [customer_outside, greeting_staff((0.0, 0.0))],
            [TrackObservation(
                track_id=1,
                track_token=customer_outside.track_token,
                role="customer",
                foot_point=(0.5, 0.3),
                world_point=(2.0, 0.0),
                confidence=0.95,
            ), greeting_staff((0.0, 0.0))],
            [customer_inside, greeting_staff((0.0, 0.0))],
            [greeting_customer(), greeting_staff((0.6, 0.0))],
            [greeting_customer(), greeting_staff((0.8, 0.0), (0.6, 0.5))],
            [greeting_customer(), greeting_staff((0.9, 0.0), (0.6, 0.5))],
        ))
        runtime = BehaviorCameraRuntime(
            adapter,
            monitor,
            staff_greeting=staff_greeting,
            customer_entry_visit=CustomerEntryVisit(self.sessions),
            entry_store_id="store-1",
            entry_runtime_session_id="runtime-1",
        )
        for index, observed_at in enumerate((
            now - timedelta(milliseconds=100), now,
            now + timedelta(milliseconds=100), now + timedelta(seconds=1),
            now + timedelta(seconds=3), now + timedelta(seconds=5),
        )):
            runtime.process_frame(index, observed_at)

        db = self.sessions()
        try:
            event = db.query(BehaviorEvent).one()
            self.assertEqual("provisional", event.details["calibration_status"])
            self.assertEqual("ground_homography", event.details["distance_mode"])
            self.assertTrue(event.details["greeting_cycle_id"].endswith("greeting-cycle"))
            self.assertTrue(event.evidence_clip_id)
        finally:
            db.close()

    def test_multi_camera_runtime_ignores_unconfigured_frames(self):
        adapter = FakeAdapter([])
        monitor = BehaviorMonitor(camera_id="entrance-1")
        runtime = BehaviorRuntime({"entrance-1": BehaviorCameraRuntime(adapter, monitor)})

        result = runtime.process_frames(
            {"entrance-1": "frame-a", "unconfigured": "frame-b"},
            datetime(2026, 7, 27, 10),
        )

        self.assertEqual({"entrance-1"}, set(result))
        self.assertEqual(["frame-a"], adapter.frames)

    def test_capture_runner_makes_camera_entries_publicly_queryable(self):
        inside = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.5, 0.5),
            confidence=0.95,
        )
        outside = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.5, 0.1),
            confidence=0.95,
        )
        trigger = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.5, 0.3),
            confidence=0.95,
        )
        store = BehaviorEventStore(self.sessions)
        monitor = BehaviorMonitor(
            camera_id="entrance-1",
            entrance_area=PolygonArea(
                "entrance", "entrance", ((0, 0.2), (1, 0.2), (1, 0.4), (0, 0.4))
            ),
            config=BehaviorMonitorConfig(
                outside_stable_frames=1,
                inside_stable_frames=1,
            ),
            behavior_sink=store.record_behavior,
        )
        runtime = BehaviorRuntime(
            {
                "entrance-1": BehaviorCameraRuntime(
                    SequencedAdapter(([outside], [trigger], [inside])),
                    monitor,
                    customer_entry_visit=CustomerEntryVisit(self.sessions),
                    entry_store_id="store-1",
                    entry_runtime_session_id="runtime-1",
                )
            }
        )
        capture = FakeCapture(["outside", "trigger", "inside"])
        runner = BehaviorCaptureRunner(
            runtime,
            {"entrance-1": "0"},
            capture_factory=lambda _source: capture,
            clock=lambda: datetime(2026, 7, 27, 10),
        )

        summary = runner.run()
        entries = TrafficQueryService(self.sessions).recent_entries(limit=10)

        self.assertEqual(3, summary["frames_processed"])
        self.assertEqual(1, summary["entry_events"])
        self.assertTrue(capture.released)
        self.assertEqual(1, len(entries))
        self.assertEqual("entrance-1", entries[0]["camera_id"])
        self.assertEqual(7, entries[0]["track_id"])

    def test_capture_disconnect_cancels_active_greeting_without_timeout(self):
        outside = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.5, 0.1),
            world_point=(3.0, 0.0),
            confidence=0.95,
        )
        inside = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.2, 0.6),
            world_point=(2.0, 0.0),
            confidence=0.95,
        )
        trigger = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.5, 0.3),
            world_point=(2.5, 0.0),
            confidence=0.95,
        )
        greeting = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.6, 0.5),
            world_point=(1.8, 0.5),
            confidence=0.95,
        )
        store = BehaviorEventStore(self.sessions)
        monitor = BehaviorMonitor(
            camera_id="entrance-1",
            entrance_area=PolygonArea(
                "entrance", "entrance", ((0, 0.2), (0.8, 0.2), (0.8, 0.4), (0, 0.4))
            ),
            greeting_area=PolygonArea(
                "greeting", "greeting", ((0.4, 0.4), (0.8, 0.4), (0.8, 1), (0.4, 1))
            ),
            config=BehaviorMonitorConfig(
                outside_stable_frames=1,
                inside_stable_frames=1,
            ),
            behavior_sink=store.record_behavior,
            calibration_status="provisional",
        )
        entrance_area = monitor.entrance_area
        greeting_area = monitor.greeting_area
        staff_greeting = StaffGreetingEvaluation(
            camera_id="entrance-1",
            entrance_area=entrance_area,
            greeting_area=greeting_area,
            config=BehaviorMonitorConfig(outside_stable_frames=1, inside_stable_frames=1),
            behavior_sink=store.record_behavior,
            calibration_status="provisional",
            comparison_enabled=True,
            session_factory=self.sessions,
        )
        runtime = BehaviorRuntime({
            "entrance-1": BehaviorCameraRuntime(
                SequencedAdapter(([outside], [trigger], [inside], [greeting])),
                monitor,
                staff_greeting=staff_greeting,
                customer_entry_visit=CustomerEntryVisit(self.sessions),
                entry_store_id="store-1",
                entry_runtime_session_id="runtime-1",
            )
        })
        runner = BehaviorCaptureRunner(
            runtime,
            {"entrance-1": "0"},
            capture_factory=lambda _source: FakeCapture(["a", "b", "c", "d"]),
            clock=lambda: datetime(2026, 7, 27, 10),
        )

        summary = runner.run()

        self.assertEqual(0, summary["behavior_events"])
        db = self.sessions()
        try:
            self.assertEqual("camera_disconnect", db.query(GreetingCycle).one().cancel_reason)
        finally:
            db.close()

    def test_runtime_shutdown_cancels_active_greeting_without_timeout(self):
        outside = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.5, 0.1),
            world_point=(3.0, 0.0),
            confidence=0.95,
        )
        inside = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.2, 0.6),
            world_point=(2.0, 0.0),
            confidence=0.95,
        )
        trigger = TrackObservation(
            track_id=7,
            track_token="store-1:entrance-1:runtime-1:7",
            role="customer",
            foot_point=(0.5, 0.3),
            world_point=(2.5, 0.0),
            confidence=0.95,
        )
        store = BehaviorEventStore(self.sessions)
        monitor = BehaviorMonitor(
            camera_id="entrance-1",
            entrance_area=PolygonArea(
                "entrance", "entrance", ((0, 0.2), (0.8, 0.2), (0.8, 0.4), (0, 0.4))
            ),
            greeting_area=PolygonArea(
                "greeting", "greeting", ((0.4, 0.4), (0.8, 0.4), (0.8, 1), (0.4, 1))
            ),
            config=BehaviorMonitorConfig(
                outside_stable_frames=1,
                inside_stable_frames=1,
            ),
            behavior_sink=store.record_behavior,
        )
        staff_greeting = StaffGreetingEvaluation(
            camera_id="entrance-1",
            entrance_area=monitor.entrance_area,
            greeting_area=monitor.greeting_area,
            config=BehaviorMonitorConfig(outside_stable_frames=1, inside_stable_frames=1),
            behavior_sink=store.record_behavior,
            comparison_enabled=True,
            session_factory=self.sessions,
        )
        runtime = BehaviorRuntime({
            "entrance-1": BehaviorCameraRuntime(
                SequencedAdapter(([outside], [trigger], [inside])),
                monitor,
                staff_greeting=staff_greeting,
                customer_entry_visit=CustomerEntryVisit(self.sessions),
                entry_store_id="store-1",
                entry_runtime_session_id="runtime-1",
            )
        })
        runtime.process_frames(
            {"entrance-1": "outside"}, datetime(2026, 7, 27, 10)
        )
        runtime.process_frames(
            {"entrance-1": "trigger"}, datetime(2026, 7, 27, 10, 0, 1)
        )
        runtime.process_frames(
            {"entrance-1": "inside"}, datetime(2026, 7, 27, 10, 0, 2)
        )

        cancelled = runtime.shutdown(datetime(2026, 7, 27, 10, 0, 3))

        self.assertEqual(1, len(cancelled["entrance-1"]))
        self.assertEqual(
            "runtime_shutdown",
            cancelled["entrance-1"][0].cancel_reason,
        )

    def test_behavior_startup_loads_config_and_composes_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            project_root = Path(root)
            config_path = project_root / "cameras.json"
            person_path = project_root / "models" / "person.pt"
            vest_path = project_root / "models" / "vest.pt"
            pose_path = project_root / "models" / "pose.pt"
            person_path.parent.mkdir()
            for path in (person_path, vest_path, pose_path):
                path.touch()
            config_path.write_text(
                json.dumps({
                    "cameras": [{
                        "camera_id": "entrance-1",
                        "source": "0",
                        "entrance_area": {
                            "area_id": "store-entrance",
                            "points": [[0, 0], [0.3, 0], [0.3, 1], [0, 1]],
                        },
                        "greeting_area": {
                            "area_id": "reception-greeting",
                            "points": [[0.3, 0], [0.7, 0], [0.7, 1], [0.3, 1]],
                        },
                        "ground_homography": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                        "calibration_status": "provisional",
                    }],
                }),
                encoding="utf-8",
            )
            configured = SimpleNamespace(
                BEHAVIOR_PROJECT_ROOT=str(project_root),
                BEHAVIOR_CONFIG_PATH="cameras.json",
                BEHAVIOR_PERSON_MODEL_PATH="models/person.pt",
                BEHAVIOR_STAFF_VEST_MODEL_PATH="models/vest.pt",
                BEHAVIOR_POSE_MODEL_PATH="models/pose.pt",
                BEHAVIOR_TRACKER="bytetrack.yaml",
                BEHAVIOR_DEVICE="cpu",
                BEHAVIOR_CONFIDENCE=0.5,
                BEHAVIOR_STAFF_VEST_ENABLED=True,
                BEHAVIOR_STAFF_VEST_CONFIDENCE=0.68,
                BEHAVIOR_ROLE_WINDOW_FRAMES=5,
                BEHAVIOR_STAFF_HIT_FRAMES=3,
                BEHAVIOR_MAXIMUM_MISSING_FRAMES=8,
                BEHAVIOR_GREETING_MOCK_STAFF_NAME="Alice",
                IDENTITY_ASSOCIATION_MODE="legacy",
                IDENTITY_COMPONENT_DEBUG=False,
            )

            validate_runtime_settings(configured)
            loaded_paths = []

            def load_model(path):
                loaded_paths.append(path)
                return SimpleNamespace(path=path)

            with patch("run_behavior_runtime.init_db") as init_db:
                runner = build_runner(
                    configured,
                    session_factory=self.sessions,
                    model_loader=load_model,
                )

        init_db.assert_called_once_with()
        self.assertEqual({"entrance-1": "0"}, runner.sources)
        self.assertEqual({"entrance-1"}, set(runner.runtime.cameras))
        self.assertEqual(
            [str(person_path.resolve()), str(vest_path.resolve()), str(pose_path.resolve())],
            loaded_paths,
        )
        adapter = runner.runtime.cameras["entrance-1"].adapter
        self.assertEqual(str(person_path.resolve()), adapter.person_model.path)
        self.assertEqual(str(vest_path.resolve()), adapter.vest_detector.model.path)
        self.assertEqual(str(pose_path.resolve()), adapter.pose_model.path)
        monitor = runner.runtime.cameras["entrance-1"].monitor
        self.assertEqual("reception-greeting", monitor.greeting_area.area_id)
        self.assertEqual(
            "Alice",
            monitor.behavior_sink.__self__.greeting_mock_staff_name,
        )
        self.assertEqual((), monitor.interaction_areas)
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        overlay = runner.runtime.cameras["entrance-1"].render_overlay(frame)
        self.assertTrue(np.all(frame == 0))
        self.assertGreater(np.count_nonzero(overlay), 0)

    def test_behavior_camera_can_disable_staff_vest_model(self):
        with tempfile.TemporaryDirectory() as root:
            project_root = Path(root)
            for relative in ("models/person.pt", "models/pose.pt"):
                path = project_root / relative
                path.parent.mkdir(exist_ok=True)
                path.touch()
            (project_root / "cameras.json").write_text(
                json.dumps({
                    "cameras": [{
                        "camera_id": "display-1",
                        "source": "0",
                        "staff_vest_enabled": False,
                        "entrance_area": None,
                    }]
                }),
                encoding="utf-8",
            )
            configured = SimpleNamespace(
                BEHAVIOR_PROJECT_ROOT=str(project_root),
                BEHAVIOR_CONFIG_PATH="cameras.json",
                BEHAVIOR_PERSON_MODEL_PATH="models/person.pt",
                BEHAVIOR_STAFF_VEST_MODEL_PATH="models/missing-vest.pt",
                BEHAVIOR_POSE_MODEL_PATH="models/pose.pt",
                BEHAVIOR_TRACKER="bytetrack.yaml",
                BEHAVIOR_DEVICE="cpu",
                BEHAVIOR_CONFIDENCE=0.5,
                BEHAVIOR_STAFF_VEST_ENABLED=True,
                BEHAVIOR_STAFF_VEST_CONFIDENCE=0.68,
                BEHAVIOR_ROLE_WINDOW_FRAMES=5,
                BEHAVIOR_STAFF_HIT_FRAMES=3,
                BEHAVIOR_MAXIMUM_MISSING_FRAMES=8,
                IDENTITY_ASSOCIATION_MODE="legacy",
                IDENTITY_COMPONENT_DEBUG=False,
            )
            loaded_paths = []

            with patch("run_behavior_runtime.init_db"):
                runner = build_runner(
                    configured,
                    session_factory=self.sessions,
                    model_loader=lambda path: loaded_paths.append(path) or object(),
                )

        self.assertEqual(2, len(loaded_paths))
        self.assertFalse(
            runner.runtime.cameras["display-1"].adapter.staff_vest_enabled
        )

    def test_greeting_camera_rejects_missing_staff_vest_model(self):
        with tempfile.TemporaryDirectory() as root:
            project_root = Path(root)
            for relative in ("models/person.pt", "models/pose.pt"):
                path = project_root / relative
                path.parent.mkdir(exist_ok=True)
                path.touch()
            (project_root / "cameras.json").write_text(
                json.dumps({
                    "cameras": [{
                        "camera_id": "entrance-1",
                        "source": "0",
                        "greeting_area": {
                            "area_id": "greeting",
                            "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                        },
                        "ground_homography": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                        "calibration_status": "provisional",
                    }]
                }),
                encoding="utf-8",
            )
            configured = SimpleNamespace(
                BEHAVIOR_PROJECT_ROOT=str(project_root),
                BEHAVIOR_CONFIG_PATH="cameras.json",
                BEHAVIOR_PERSON_MODEL_PATH="models/person.pt",
                BEHAVIOR_STAFF_VEST_MODEL_PATH="models/missing.pt",
                BEHAVIOR_POSE_MODEL_PATH="models/pose.pt",
                BEHAVIOR_TRACKER="bytetrack.yaml",
                BEHAVIOR_DEVICE="cpu",
                BEHAVIOR_CONFIDENCE=0.5,
                BEHAVIOR_STAFF_VEST_ENABLED=True,
                BEHAVIOR_STAFF_VEST_CONFIDENCE=0.68,
                BEHAVIOR_ROLE_WINDOW_FRAMES=5,
                BEHAVIOR_STAFF_HIT_FRAMES=3,
                BEHAVIOR_MAXIMUM_MISSING_FRAMES=8,
                IDENTITY_ASSOCIATION_MODE="legacy",
                IDENTITY_COMPONENT_DEBUG=False,
            )

            with self.assertRaisesRegex(FileNotFoundError, "staff vest model"):
                build_runner(configured, session_factory=self.sessions)

    def test_entry_trigger_rejects_missing_greeting_area_capability(self):
        with tempfile.TemporaryDirectory() as root:
            project_root = Path(root)
            for relative in (
                "models/person.pt",
                "models/vest.pt",
                "models/pose.pt",
            ):
                path = project_root / relative
                path.parent.mkdir(exist_ok=True)
                path.touch()
            (project_root / "cameras.json").write_text(
                json.dumps({
                    "cameras": [{
                        "camera_id": "entrance-1",
                        "store_id": "store-1",
                        "source": "0",
                        "entry_trigger": {
                            "area_id": "front-entry-trigger",
                            "points": [
                                [0.06640625, 0.5625],
                                [0.546875, 0.5625],
                                [0.546875, 0.72916667],
                                [0.06640625, 0.72916667],
                            ],
                        },
                        "greeting_area": None,
                        "calibration_status": "provisional",
                        "calibration_image_size": [2560, 1440],
                        "ground_homography": [
                            7.7008130081, 0.0, -0.5113821138,
                            0.0, 6.0, -3.375,
                            0.0, 0.0, 1.0,
                        ],
                    }]
                }),
                encoding="utf-8",
            )
            configured = SimpleNamespace(
                BEHAVIOR_PROJECT_ROOT=str(project_root),
                BEHAVIOR_CONFIG_PATH="cameras.json",
                BEHAVIOR_PERSON_MODEL_PATH="models/person.pt",
                BEHAVIOR_STAFF_VEST_MODEL_PATH="models/vest.pt",
                BEHAVIOR_POSE_MODEL_PATH="models/pose.pt",
                BEHAVIOR_TRACKER="bytetrack.yaml",
                BEHAVIOR_DEVICE="cpu",
                BEHAVIOR_CONFIDENCE=0.5,
                BEHAVIOR_STAFF_VEST_ENABLED=True,
                BEHAVIOR_STAFF_VEST_CONFIDENCE=0.68,
                BEHAVIOR_ROLE_WINDOW_FRAMES=5,
                BEHAVIOR_STAFF_HIT_FRAMES=3,
                BEHAVIOR_MAXIMUM_MISSING_FRAMES=8,
                IDENTITY_ASSOCIATION_MODE="legacy",
                IDENTITY_COMPONENT_DEBUG=False,
            )

            with self.assertRaisesRegex(ValueError, "greeting_area"):
                build_runner(configured, session_factory=self.sessions)

    def test_greeting_camera_rejects_missing_ground_homography(self):
        with tempfile.TemporaryDirectory() as root:
            project_root = Path(root)
            for relative in (
                "models/person.pt",
                "models/vest.pt",
                "models/pose.pt",
            ):
                path = project_root / relative
                path.parent.mkdir(exist_ok=True)
                path.touch()
            (project_root / "cameras.json").write_text(
                json.dumps({
                    "cameras": [{
                        "camera_id": "entrance-1",
                        "source": "0",
                        "greeting_area": {
                            "area_id": "greeting",
                            "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                        },
                    }]
                }),
                encoding="utf-8",
            )
            configured = SimpleNamespace(
                BEHAVIOR_PROJECT_ROOT=str(project_root),
                BEHAVIOR_CONFIG_PATH="cameras.json",
                BEHAVIOR_PERSON_MODEL_PATH="models/person.pt",
                BEHAVIOR_STAFF_VEST_MODEL_PATH="models/vest.pt",
                BEHAVIOR_POSE_MODEL_PATH="models/pose.pt",
                BEHAVIOR_TRACKER="bytetrack.yaml",
                BEHAVIOR_DEVICE="cpu",
                BEHAVIOR_CONFIDENCE=0.5,
                BEHAVIOR_STAFF_VEST_ENABLED=True,
                BEHAVIOR_STAFF_VEST_CONFIDENCE=0.68,
                BEHAVIOR_ROLE_WINDOW_FRAMES=5,
                BEHAVIOR_STAFF_HIT_FRAMES=3,
                BEHAVIOR_MAXIMUM_MISSING_FRAMES=8,
                IDENTITY_ASSOCIATION_MODE="legacy",
                IDENTITY_COMPONENT_DEBUG=False,
            )

            with self.assertRaisesRegex(ValueError, "ground_homography"):
                build_runner(configured, session_factory=self.sessions)

    def test_invalid_staff_vest_confidence_is_rejected(self):
        configured = SimpleNamespace(
            BEHAVIOR_PROJECT_ROOT=".",
            BEHAVIOR_CONFIG_PATH="configs/examples/behavior_cameras.example.json",
            BEHAVIOR_PERSON_MODEL_PATH="data/models/yolo11n.pt",
            BEHAVIOR_STAFF_VEST_MODEL_PATH=(
                "data/models/staff_vest.pt"
            ),
            BEHAVIOR_POSE_MODEL_PATH="data/models/yolo11n-pose.pt",
            BEHAVIOR_TRACKER="bytetrack.yaml",
            BEHAVIOR_DEVICE="cpu",
            BEHAVIOR_CONFIDENCE=0.5,
            BEHAVIOR_STAFF_VEST_ENABLED=True,
            BEHAVIOR_STAFF_VEST_CONFIDENCE=float("nan"),
            BEHAVIOR_ROLE_WINDOW_FRAMES=5,
            BEHAVIOR_STAFF_HIT_FRAMES=3,
            BEHAVIOR_MAXIMUM_MISSING_FRAMES=8,
            IDENTITY_ASSOCIATION_MODE="legacy",
            IDENTITY_COMPONENT_DEBUG=False,
        )

        with self.assertRaisesRegex(ValueError, "STAFF_VEST_CONFIDENCE"):
            validate_runtime_settings(configured)

    def test_legacy_model_setting_is_loaded_with_deprecation_warning(self):
        with tempfile.TemporaryDirectory() as root:
            project_root = Path(root)
            for relative in ("legacy.pt", "pose.pt"):
                (project_root / relative).touch()
            (project_root / "cameras.json").write_text(
                json.dumps({
                    "cameras": [{
                        "camera_id": "entrance-1",
                        "source": "0",
                        "greeting_area": {
                            "area_id": "greeting",
                            "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                        },
                        "ground_homography": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                        "calibration_status": "provisional",
                    }]
                }),
                encoding="utf-8",
            )
            configured = SimpleNamespace(
                BEHAVIOR_PROJECT_ROOT=str(project_root),
                BEHAVIOR_CONFIG_PATH="cameras.json",
                BEHAVIOR_VEST_MODEL_PATH="legacy.pt",
                BEHAVIOR_POSE_MODEL_PATH="pose.pt",
                BEHAVIOR_TRACKER="bytetrack.yaml",
                BEHAVIOR_DEVICE="cpu",
                BEHAVIOR_CONFIDENCE=0.5,
                IDENTITY_ASSOCIATION_MODE="legacy",
                IDENTITY_COMPONENT_DEBUG=False,
            )
            loaded_paths = []

            with self.assertWarns(DeprecationWarning):
                with patch("run_behavior_runtime.init_db"):
                    build_runner(
                        configured,
                        session_factory=self.sessions,
                        model_loader=lambda path: loaded_paths.append(path) or object(),
                    )

        self.assertEqual(
            [
                str((project_root / "legacy.pt").resolve()),
                str((project_root / "legacy.pt").resolve()),
                str((project_root / "pose.pt").resolve()),
            ],
            loaded_paths,
        )


if __name__ == "__main__":
    unittest.main()
