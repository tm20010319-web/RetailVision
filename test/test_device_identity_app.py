from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import importlib
import os
import signal
from datetime import datetime
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch
import uuid

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import agent.config as config_module
import agent.db as db_module
from agent.schema_evolution import evolve_schema
from agent.identity.device_identity import (
    DeviceMemberMappingData,
    InMemoryDeviceIdentityRepository,
    SqlAlchemyDeviceIdentityRepository,
    StableTrackEvent,
)
from agent.legacy.member_entry_service import MemberEntryResult
from agent.profile.llm_service import LLMServiceFactory, OllamaVisionLLMService
from agent.models import (
    BusinessJob,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
    Visit,
)
from pipelines.legacy.member_business_runtime import (
    MemberBusinessRuntime,
    create_member_business_runtime,
)
from pipelines.legacy.customer_profile_pipeline import create_customer_profile_pipeline
from pipelines.device_identity_runtime import (
    DeviceIdentityRuntime,
    create_device_identity_runtime,
)
from pipelines.legacy.realtime_entry_pipeline import RealtimeEntryPipeline
from run_device_identity import (
    build_runtime,
    main,
    parse_video_source,
    validate_runtime_settings,
)


TEST_TEMP_ROOT = Path(__file__).resolve().parents[1] / ".task5-tests"
TEST_TEMP_ROOT.mkdir(exist_ok=True)


def runtime_settings(**overrides):
    values = {
        "CAMERA_FACE_DEVICE_ID": "entrance-1",
        "CAMERA_FACE_BASE_URL": "http://camera.local",
        "CAMERA_FACE_USERNAME": "operator",
        "CAMERA_FACE_PASSWORD": "secret",
        "CAMERA_FACE_UNIQUE_CODE": "unique-code",
        "CAMERA_FACE_CONNECT_TIMEOUT": 2.0,
        "CAMERA_FACE_READ_TIMEOUT": 5.0,
        "CAMERA_FACE_POLL_INTERVAL": 1.0,
        "DEVICE_EVENT_WORKER_INTERVAL": 0.1,
        "DEVICE_EVENT_PROCESSING_TIMEOUT": 30.0,
        "DEVICE_HEALTH_FRESHNESS_SECONDS": 90.0,
        "PROFILE_MODE": "mock",
        "PROFILE_JOB_POLL_INTERVAL": 0.25,
        "PROFILE_JOB_MAX_ATTEMPTS": 7,
        "OLLAMA_VISION_TIMEOUT_SECONDS": 120.0,
        "OLLAMA_VISION_MAX_RETRIES": 3,
        "MEMBER_ENTRY_RECOVERY_INTERVAL": 0.5,
        "TRACK_SNAPSHOT_DIR": "snapshots",
        "TRACK_SNAPSHOT_TTL_SECONDS": 90.0,
        "P6S_CAPTURE_DIR": "snapshots/device_push",
        "P6S_MAX_CAPTURE_BYTES": 2_000_000,
        "P6S_MAX_CAPTURE_PIXELS": 12_000_000,
        "P6S_SIMILARITY_THRESHOLD": 0.81,
        "YOLO_MODEL_PATH": "model.pt",
        "VIDEO_SOURCE": "0",
        "IDENTITY_ASSOCIATION_MODE": "legacy",
        "IDENTITY_COMPONENT_DEBUG": False,
        "IDENTITY_ASSOCIATION_STORE_ID": "store-1",
        "IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID": "legacy-runtime-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DeviceIdentityApplicationTests(unittest.TestCase):
    def test_settings_read_exact_task_7_environment_values(self):
        environment = {
            "OUTPUT_DIR": "task-7-output",
            "VIDEO_SOURCE": "shop.mp4",
            "PROFILE_MODE": "OLLAMA",
            "PROFILE_JOB_POLL_INTERVAL": "0.2",
            "PROFILE_JOB_MAX_ATTEMPTS": "9",
            "OLLAMA_VISION_TIMEOUT_SECONDS": "45",
            "OLLAMA_VISION_MAX_RETRIES": "1",
            "MEMBER_ENTRY_RECOVERY_INTERVAL": "0.3",
            "TRACK_SNAPSHOT_TTL_SECONDS": "321",
        }
        try:
            with patch.dict(os.environ, environment, clear=True):
                configured = importlib.reload(config_module).settings
                self.assertEqual("shop.mp4", configured.VIDEO_SOURCE)
                self.assertEqual("ollama", configured.PROFILE_MODE)
                self.assertEqual(0.2, configured.PROFILE_JOB_POLL_INTERVAL)
                self.assertEqual(9, configured.PROFILE_JOB_MAX_ATTEMPTS)
                self.assertEqual(45.0, configured.OLLAMA_VISION_TIMEOUT_SECONDS)
                self.assertEqual(1, configured.OLLAMA_VISION_MAX_RETRIES)
                self.assertEqual(0.3, configured.MEMBER_ENTRY_RECOVERY_INTERVAL)
                self.assertEqual(
                    os.path.join("task-7-output", "member_track_snapshots"),
                    configured.TRACK_SNAPSHOT_DIR,
                )
                self.assertEqual(321.0, configured.TRACK_SNAPSHOT_TTL_SECONDS)
        finally:
            importlib.reload(config_module)

    def test_settings_keep_ollama_vision_defaults_compatible(self):
        try:
            with patch.dict(os.environ, {}, clear=True):
                configured = importlib.reload(config_module).settings
                self.assertEqual(120.0, configured.OLLAMA_VISION_TIMEOUT_SECONDS)
                self.assertEqual(3, configured.OLLAMA_VISION_MAX_RETRIES)
        finally:
            importlib.reload(config_module)

    def test_parse_video_source_converts_only_numeric_sources(self):
        self.assertEqual(0, parse_video_source("0"))
        self.assertEqual("shop.mp4", parse_video_source("shop.mp4"))

    def test_validation_names_each_missing_required_camera_setting(self):
        for name in (
            "CAMERA_FACE_DEVICE_ID",
            "CAMERA_FACE_BASE_URL",
            "CAMERA_FACE_USERNAME",
            "CAMERA_FACE_UNIQUE_CODE",
        ):
            with self.subTest(name=name):
                configured = runtime_settings(**{name: "  "})
                with self.assertRaisesRegex(ValueError, name):
                    validate_runtime_settings(configured)

    def test_validation_allows_device_with_empty_password(self):
        validate_runtime_settings(runtime_settings(CAMERA_FACE_PASSWORD=""))

    def test_validation_rejects_unknown_profile_mode(self):
        with self.assertRaisesRegex(ValueError, "PROFILE_MODE"):
            validate_runtime_settings(runtime_settings(PROFILE_MODE="remote"))

    def test_validation_rejects_each_non_positive_runtime_duration(self):
        duration_settings = (
            "CAMERA_FACE_CONNECT_TIMEOUT",
            "CAMERA_FACE_READ_TIMEOUT",
            "CAMERA_FACE_POLL_INTERVAL",
            "DEVICE_EVENT_WORKER_INTERVAL",
            "DEVICE_EVENT_PROCESSING_TIMEOUT",
            "DEVICE_HEALTH_FRESHNESS_SECONDS",
            "PROFILE_JOB_POLL_INTERVAL",
            "MEMBER_ENTRY_RECOVERY_INTERVAL",
            "TRACK_SNAPSHOT_TTL_SECONDS",
        )
        for name in duration_settings:
            for value in (0, -0.1):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        validate_runtime_settings(runtime_settings(**{name: value}))

    def test_validation_requires_positive_integer_profile_max_attempts(self):
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, "PROFILE_JOB_MAX_ATTEMPTS"
                ):
                    validate_runtime_settings(
                        runtime_settings(PROFILE_JOB_MAX_ATTEMPTS=value)
                    )

    def test_validation_rejects_invalid_ollama_vision_timeout(self):
        for value in (0, -1, float("inf"), True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, "OLLAMA_VISION_TIMEOUT_SECONDS"
                ):
                    validate_runtime_settings(
                        runtime_settings(OLLAMA_VISION_TIMEOUT_SECONDS=value)
                    )

    def test_validation_requires_positive_integer_ollama_vision_retries(self):
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, "OLLAMA_VISION_MAX_RETRIES"
                ):
                    validate_runtime_settings(
                        runtime_settings(OLLAMA_VISION_MAX_RETRIES=value)
                    )

    def test_vision_pipeline_passes_timeout_and_retries_to_factory(self):
        with patch(
            "pipelines.legacy.customer_profile_pipeline.LLMServiceFactory.create_vision_llm"
        ) as create_llm:
            pipeline = create_customer_profile_pipeline(
                use_mock_llm=False,
                output_dir="snapshots",
                vision_timeout_seconds=45.0,
                vision_max_retries=1,
            )

        create_llm.assert_called_once_with(
            use_mock=False,
            timeout_seconds=45.0,
            max_retries=1,
        )
        self.assertIs(pipeline.llm_service, create_llm.return_value)

    def test_vision_factory_configures_ollama_service(self):
        service = LLMServiceFactory.create_vision_llm(
            use_mock=False,
            timeout_seconds=45.0,
            max_retries=1,
        )

        self.assertIsInstance(service, OllamaVisionLLMService)
        self.assertEqual(45.0, service.timeout)
        self.assertEqual(1, service.max_retries)

    def test_database_engine_log_redacts_url_password(self):
        database_url = "postgresql://camera_user:database-secret@db.local/retail"
        with patch.object(db_module, "_engine", None), patch.object(
            db_module.settings, "DATABASE_URL", database_url
        ), patch.object(
            db_module, "create_engine", return_value=object()
        ), self.assertLogs(
            db_module.logger, level="INFO"
        ) as captured:
            db_module._get_engine()

        rendered_logs = "\n".join(captured.output)
        self.assertNotIn("database-secret", rendered_logs)
        self.assertIn("db.local/retail", rendered_logs)

    @patch("run_device_identity.create_device_identity_runtime")
    @patch("run_device_identity.create_member_business_runtime")
    @patch("run_device_identity.SqlAlchemyDeviceIdentityRepository")
    @patch("run_device_identity.DeviceCaptureStore")
    @patch("run_device_identity.CameraFaceApiClient")
    @patch("run_device_identity.get_session_factory")
    @patch("run_device_identity.init_db")
    def test_build_runtime_wires_factories_without_face_recognizer(
        self,
        init_db,
        get_session_factory,
        client_type,
        capture_store_type,
        repository_type,
        business_factory,
        device_factory,
    ):
        configured = runtime_settings(PROFILE_MODE="mock")
        sessions = object()
        get_session_factory.return_value = sessions

        runtime = build_runtime(configured)

        self.assertIs(device_factory.return_value, runtime)
        init_db.assert_called_once_with()
        get_session_factory.assert_called_once_with()
        client_type.assert_called_once_with(
            device_id="entrance-1",
            base_url="http://camera.local",
            username="operator",
            password="secret",
            unique_code="unique-code",
            connect_timeout=2.0,
            read_timeout=5.0,
        )
        repository_type.assert_called_once_with(sessions)
        capture_store_type.assert_called_once_with(
            configured.P6S_CAPTURE_DIR,
            max_bytes=configured.P6S_MAX_CAPTURE_BYTES,
            max_pixels=configured.P6S_MAX_CAPTURE_PIXELS,
        )
        business_factory.assert_called_once_with(
            session_factory=sessions,
            profile_mode="mock",
            output_dir="snapshots",
            profile_job_poll_interval=0.25,
            profile_job_max_attempts=7,
            ollama_vision_timeout_seconds=120.0,
            ollama_vision_max_retries=3,
            recovery_interval_seconds=0.5,
            snapshot_ttl_seconds=90.0,
        )
        device_factory.assert_called_once_with(
            client=client_type.return_value,
            repository=repository_type.return_value,
            session_factory=sessions,
            capture_store=capture_store_type.return_value,
            recognition_similarity_threshold=0.81,
            model_path="model.pt",
            poll_interval_seconds=1.0,
            inbox_poll_interval_seconds=0.1,
            inbox_processing_timeout_seconds=30.0,
            device_health_freshness_seconds=90.0,
            member_business_runtime=business_factory.return_value,
            identity_association_mode="legacy",
            association_store_id="store-1",
            association_runtime_owner_id="legacy-runtime-1",
            component_debug=False,
        )
        self.assertNotIn("face_recognizer", device_factory.call_args.kwargs)

    @patch("run_device_identity.signal.signal")
    @patch("run_device_identity.build_runtime")
    def test_main_runs_numeric_source_and_signal_handlers_stop_runtime(
        self, build_runtime_mock, signal_mock
    ):
        configured = runtime_settings(VIDEO_SOURCE="2")
        runtime = build_runtime_mock.return_value

        main(configured)

        runtime.run.assert_called_once_with(2)
        self.assertEqual(2, signal_mock.call_count)
        registered = {
            registered_call.args[0]: registered_call.args[1]
            for registered_call in signal_mock.call_args_list
        }
        self.assertEqual(
            {signal.SIGINT, signal.SIGTERM},
            set(registered),
        )
        for handler in registered.values():
            handler(None, None)
        self.assertEqual([call(), call()], runtime.request_stop.call_args_list)


class FakeCameraClient:
    device_id = "entrance-1"

    def __init__(self, records):
        self.records = list(records)

    def query_recognition_records(self, *args, **kwargs):
        records, self.records = self.records, []
        return records


class MockModeBusinessSmokeTests(unittest.TestCase):
    def test_persisted_recognition_track_and_worker_complete_without_http(self):
        now = datetime(2026, 7, 14, 12, 0, 0)
        record = {
            "log_id": 17,
            "occurred_at": now,
            "person_id": 23,
            "group_id": 11,
            "unique_id": "authorized-member",
            "similarity": 0.93,
            "reco_valid": True,
            "pass_code": 0,
            "rule_id": 1,
        }
        database_path = TEST_TEMP_ROOT / f"business-{uuid.uuid4().hex}.sqlite3"
        try:
            root = TEST_TEMP_ROOT
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            try:
                evolve_schema(engine)
                sessions = sessionmaker(bind=engine)
                db = sessions()
                try:
                    db.add(Member(id="member-1", name="Member", visit_count=0))
                    db.commit()
                finally:
                    db.close()

                repository = SqlAlchemyDeviceIdentityRepository(sessions)
                repository.save_mapping(
                    DeviceMemberMappingData(
                        device_id="entrance-1",
                        member_id="member-1",
                        group_id=11,
                        group_uuid="group-uuid",
                        person_id=23,
                        unique_id="authorized-member",
                        face_uuid="face-uuid",
                        sync_status="synced",
                    )
                )
                business = create_member_business_runtime(
                    session_factory=sessions,
                    profile_mode="mock",
                    output_dir=str(root / "snapshots"),
                    profile_job_poll_interval=0.01,
                    profile_job_max_attempts=5,
                    recovery_interval_seconds=1.0,
                    snapshot_ttl_seconds=600.0,
                )
                runtime = create_device_identity_runtime(
                    client=FakeCameraClient([record]),
                    repository=repository,
                    session_factory=sessions,
                    detector=Detector(),
                    member_business_runtime=business,
                    clock=lambda: now,
                    identity_association_mode="legacy",
                )
                track = StableTrackEvent(
                    camera_id="entrance-1",
                    track_id=7,
                    bbox=[1, 1, 9, 9],
                    detection_confidence=0.95,
                    confirmed_at=now,
                )

                with patch("requests.sessions.Session.request") as http_request:
                    business.capture_track(
                        track, np.full((10, 10, 3), 127, dtype=np.uint8)
                    )
                    runtime.coordinator.submit_track(track)
                    emitted = runtime.poll_once()
                    self.assertEqual(1, len(emitted))
                    self.assertTrue(business.profile_worker.run_once())
                    http_request.assert_not_called()

                db = sessions()
                try:
                    self.assertEqual(1, db.query(Visit).count())
                    inbox = db.query(DeviceRecognitionInbox).one()
                    self.assertEqual("processed", inbox.status)
                    self.assertEqual(1, db.query(MemberEntryEvent).count())
                    job = db.query(BusinessJob).one()
                    self.assertEqual("succeeded", job.status)
                    member = db.get(Member, "member-1")
                    self.assertEqual(1, member.visit_count)
                    self.assertIn("latest_customer_profile", member.profile)
                finally:
                    db.close()
            finally:
                engine.dispose()
        finally:
            database_path.unlink(missing_ok=True)


class RecordingThread:
    def __init__(self, lifecycle, *, target, name, daemon):
        self.lifecycle = lifecycle
        self.target = target
        self.name = name
        self.daemon = daemon
        self._alive = False

    def start(self):
        self.lifecycle.append(f"{self.name}.start")
        self._alive = True

    def join(self, timeout=None):
        self.lifecycle.append(f"{self.name}.join:{timeout}")
        self._alive = False

    def is_alive(self):
        return self._alive


class FailingJoinThread(RecordingThread):
    def join(self, timeout=None):
        self.lifecycle.append(f"{self.name}.join:{timeout}")
        raise RuntimeError("join failed")


class StuckThread(RecordingThread):
    def join(self, timeout=None):
        self.lifecycle.append(f"{self.name}.join:{timeout}")


class ReleasableThread(StuckThread):
    def finish(self):
        self._alive = False


class RecordingService:
    def __init__(self, lifecycle, result=None, recover_error=None):
        self.lifecycle = lifecycle
        self.result = result
        self.recover_error = recover_error

    def finalize(self, session):
        self.lifecycle.append(("business.handle", session))
        return self.result

    def recover_due(self):
        self.lifecycle.append("business.recover")
        if self.recover_error is not None:
            raise self.recover_error
        return 0


class RecordingStore:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle

    def capture(self, event, frame):
        self.lifecycle.append(("snapshot.capture", event, frame))
        return "snapshot.jpg"

    def cleanup_expired(self, max_age_seconds):
        self.lifecycle.append(("snapshot.cleanup", max_age_seconds))


class RecordingWorker:
    def __init__(self, lifecycle, start_error=None):
        self.lifecycle = lifecycle
        self.start_error = start_error

    def start(self):
        self.lifecycle.append("worker.start")
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.lifecycle.append("worker.stop")


class OneIterationStop:
    def __init__(self):
        self.calls = 0

    def wait(self, timeout):
        self.calls += 1
        return self.calls > 1


class MemberBusinessRuntimeTests(unittest.TestCase):
    def test_delegates_snapshot_capture_and_durable_finalization(self):
        lifecycle = []
        result = MemberEntryResult("event-1", "visit-1", "pending")
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle, result=result),
            profile_worker=None,
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )
        event = object()
        frame = object()
        session = {"device_event_id": "event-1", "member_id": "member-1"}

        self.assertIsNone(runtime.capture_track(event, frame))
        self.assertIs(result, runtime.handle_member_entry(session))
        self.assertEqual(
            [
                ("snapshot.capture", event, frame),
                ("business.handle", session),
            ],
            lifecycle,
        )

    def test_start_and_stop_are_ordered_and_idempotent(self):
        lifecycle = []
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle),
            profile_worker=RecordingWorker(lifecycle),
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )

        with patch(
            "pipelines.legacy.member_business_runtime.threading.Thread",
            side_effect=lambda **kwargs: RecordingThread(lifecycle, **kwargs),
        ):
            runtime.start()
            runtime.start()
            runtime.stop()
            runtime.stop()

        self.assertEqual(
            [
                "worker.start",
                "business.recover",
                "member-business-recovery.start",
                "member-business-recovery.join:2.0",
                "worker.stop",
            ],
            lifecycle,
        )

    def test_recovery_iteration_runs_finalization_and_snapshot_cleanup(self):
        lifecycle = []
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle),
            profile_worker=None,
            recovery_interval_seconds=0.01,
            snapshot_ttl_seconds=37.0,
        )
        runtime._stop = OneIterationStop()

        runtime._recover_forever()

        self.assertEqual(
            ["business.recover", ("snapshot.cleanup", 37.0)], lifecycle
        )

    def test_start_failure_stops_an_already_started_profile_worker(self):
        lifecycle = []
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(
                lifecycle, recover_error=RuntimeError("database unavailable")
            ),
            profile_worker=RecordingWorker(lifecycle),
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            runtime.start()

        self.assertEqual(
            ["worker.start", "business.recover", "worker.stop"], lifecycle
        )

    def test_profile_worker_start_failure_still_requests_worker_stop(self):
        lifecycle = []
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle),
            profile_worker=RecordingWorker(
                lifecycle, start_error=RuntimeError("worker partial start")
            ),
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )

        with self.assertRaisesRegex(RuntimeError, "worker partial start"):
            runtime.start()

        self.assertEqual(["worker.start", "worker.stop"], lifecycle)

    def test_stop_still_stops_worker_when_recovery_join_raises(self):
        lifecycle = []
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle),
            profile_worker=RecordingWorker(lifecycle),
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )

        with patch(
            "pipelines.legacy.member_business_runtime.threading.Thread",
            side_effect=lambda **kwargs: FailingJoinThread(lifecycle, **kwargs),
        ):
            runtime.start()
            with self.assertRaisesRegex(RuntimeError, "join failed"):
                runtime.stop()

        self.assertEqual("worker.stop", lifecycle[-1])

    def test_stop_reports_recovery_thread_that_outlives_join_timeout(self):
        lifecycle = []
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle),
            profile_worker=RecordingWorker(lifecycle),
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )

        with patch(
            "pipelines.legacy.member_business_runtime.threading.Thread",
            side_effect=lambda **kwargs: StuckThread(lifecycle, **kwargs),
        ):
            runtime.start()
            with self.assertRaisesRegex(RuntimeError, "still running"):
                runtime.stop()

        self.assertTrue(runtime._started)
        self.assertEqual("worker.stop", lifecycle[-1])

    def test_start_rejects_stopping_child_then_performs_full_restart_after_exit(self):
        lifecycle = []
        created_threads = []
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle),
            profile_worker=RecordingWorker(lifecycle),
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )

        def build_thread(**kwargs):
            thread_type = ReleasableThread if not created_threads else RecordingThread
            thread = thread_type(lifecycle, **kwargs)
            created_threads.append(thread)
            return thread

        with patch(
            "pipelines.legacy.member_business_runtime.threading.Thread",
            side_effect=build_thread,
        ):
            runtime.start()
            with self.assertRaisesRegex(RuntimeError, "still running"):
                runtime.stop()

            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                runtime.start()

            created_threads[0].finish()
            runtime.start()
            runtime.stop()

        self.assertEqual(2, len(created_threads))
        self.assertEqual(2, lifecycle.count("worker.start"))
        self.assertEqual(2, lifecycle.count("business.recover"))
        self.assertFalse(runtime._started)

    def test_stop_retry_clears_state_only_after_recovery_child_exits(self):
        lifecycle = []
        recovery_thread = None
        runtime = MemberBusinessRuntime(
            snapshot_store=RecordingStore(lifecycle),
            service=RecordingService(lifecycle),
            profile_worker=RecordingWorker(lifecycle),
            recovery_interval_seconds=1.0,
            snapshot_ttl_seconds=60.0,
        )

        def build_thread(**kwargs):
            nonlocal recovery_thread
            recovery_thread = ReleasableThread(lifecycle, **kwargs)
            return recovery_thread

        with patch(
            "pipelines.legacy.member_business_runtime.threading.Thread",
            side_effect=build_thread,
        ):
            runtime.start()
            with self.assertRaisesRegex(RuntimeError, "still running"):
                runtime.stop()
            with self.assertRaisesRegex(RuntimeError, "still running"):
                runtime.stop()

            self.assertTrue(runtime._started)
            self.assertEqual(1, lifecycle.count("worker.stop"))

            recovery_thread.finish()
            runtime.stop()

        self.assertFalse(runtime._started)
        self.assertEqual(1, lifecycle.count("worker.stop"))

    def test_factory_builds_worker_only_for_mock_and_ollama_modes(self):
        for mode, expected_mock, profile_enabled in (
            ("mock", True, True),
            ("ollama", False, True),
            ("disabled", None, False),
        ):
            with self.subTest(mode=mode), patch(
                "pipelines.legacy.member_business_runtime.ProfileSnapshotStore"
            ) as store_type, patch(
                "pipelines.legacy.member_business_runtime.MemberEntryService"
            ) as service_type, patch(
                "pipelines.legacy.member_business_runtime.create_customer_profile_pipeline"
            ) as profile_factory, patch(
                "pipelines.legacy.member_business_runtime.ProfileJobWorker"
            ) as worker_type:
                store = store_type.return_value
                service = service_type.return_value

                runtime = create_member_business_runtime(
                    session_factory=object(),
                    profile_mode=mode,
                    output_dir="snapshots",
                    profile_job_poll_interval=0.25,
                    profile_job_max_attempts=7,
                    ollama_vision_timeout_seconds=45.0,
                    ollama_vision_max_retries=1,
                    recovery_interval_seconds=0.5,
                    snapshot_ttl_seconds=90.0,
                )

                store_type.assert_called_once_with("snapshots")
                self.assertIs(runtime.snapshot_store, store)
                self.assertIs(runtime.service, service)
                self.assertEqual(7, runtime.profile_job_max_attempts)
                self.assertEqual(
                    profile_enabled,
                    service_type.call_args.kwargs["profile_enabled"],
                )
                self.assertEqual(
                    7,
                    service_type.call_args.kwargs["profile_job_max_attempts"],
                )
                if expected_mock is None:
                    profile_factory.assert_not_called()
                    worker_type.assert_not_called()
                    self.assertIsNone(runtime.profile_worker)
                else:
                    profile_factory.assert_called_once_with(
                        use_mock_llm=expected_mock,
                        output_dir="snapshots",
                        vision_timeout_seconds=45.0,
                        vision_max_retries=1,
                    )
                    worker_type.assert_called_once_with(
                        unittest.mock.ANY,
                        profile_factory.return_value,
                        poll_interval_seconds=0.25,
                    )
                    self.assertIs(runtime.profile_worker, worker_type.return_value)

    def test_factory_rejects_unknown_profile_mode(self):
        with self.assertRaisesRegex(ValueError, "profile_mode"):
            create_member_business_runtime(
                session_factory=object(),
                profile_mode="remote",
                output_dir="snapshots",
                profile_job_poll_interval=1.0,
                profile_job_max_attempts=5,
                recovery_interval_seconds=1.0,
                snapshot_ttl_seconds=60.0,
            )


class RecordingPipeline:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle

    def stop(self):
        self.lifecycle.append("pipeline.stop")

    def run(self, video_source):
        self.lifecycle.append(("pipeline.run", video_source))
        return {"video_source": video_source}


class RecordingPoller:
    device_id = "front-door"

    def __init__(self, lifecycle, start_error=None):
        self.lifecycle = lifecycle
        self.start_error = start_error

    def start(self):
        self.lifecycle.append("poller.start")
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.lifecycle.append("poller.stop")


class RecordingInboxWorker:
    def __init__(self, lifecycle, start_error=None):
        self.lifecycle = lifecycle
        self.start_error = start_error

    def start(self):
        self.lifecycle.append("inbox.start")
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.lifecycle.append("inbox.stop")


class RecordingCoordinator:
    def flush(self, now):
        pass


class RecordingBusiness:
    def __init__(self, lifecycle, start_error=None, start_callback=None):
        self.lifecycle = lifecycle
        self.start_error = start_error
        self.start_callback = start_callback

    def start(self):
        self.lifecycle.append("business.start")
        if self.start_callback is not None:
            self.start_callback()
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.lifecycle.append("business.stop")

    def handle_member_entry(self, session):
        self.lifecycle.append(("business.handle", session))

    def capture_track(self, event, frame):
        self.lifecycle.append(("business.capture", event, frame))


class Detector:
    def process_frame(self, frame):
        return []

    def reset(self):
        pass


class Client:
    device_id = "front-door"


class Repository:
    pass


class DeviceIdentityCompositionTests(unittest.TestCase):
    def test_sweeper_survives_health_and_flush_failures(self):
        health_times = iter(
            [
                datetime(2026, 7, 20, 4, 0),
                datetime(2026, 7, 20, 4, 0, 1),
            ]
        )
        health_calls = []
        health_results = iter(
            [RuntimeError("HEALTH_SECRET"), True]
        )

        class HealthStore:
            def identity_available(self, device_id, now):
                health_calls.append((device_id, now))
                result = next(health_results)
                if isinstance(result, Exception):
                    raise result
                return result

        class Coordinator:
            def __init__(self):
                self.health = []
                self.flush_calls = []

            def set_device_health(self, device_id, available):
                self.health.append((device_id, available))

            def flush(self, now):
                self.flush_calls.append(now)
                if len(self.flush_calls) == 1:
                    raise ValueError("FLUSH_SECRET")

        class TwoIterationStop:
            def __init__(self):
                self.waits = 0

            def wait(self, _timeout):
                self.waits += 1
                return self.waits > 2

        coordinator = Coordinator()
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline([]),
            coordinator=coordinator,
            device_health_store=HealthStore(),
            device_id="front-door",
            clock=lambda: datetime(2030, 1, 1, 12, 0),
            health_clock=lambda: next(health_times),
        )
        runtime._stop = TwoIterationStop()

        with self.assertLogs(
            "pipelines.device_identity_runtime", level="ERROR"
        ) as logs:
            runtime._sweep_forever()

        self.assertEqual(
            [
                ("front-door", datetime(2026, 7, 20, 4, 0)),
                ("front-door", datetime(2026, 7, 20, 4, 0, 1)),
            ],
            health_calls,
        )
        self.assertEqual(
            [("front-door", False), ("front-door", True)], coordinator.health
        )
        self.assertEqual(
            [datetime(2030, 1, 1, 12, 0)] * 2,
            coordinator.flush_calls,
        )
        rendered = "\n".join(logs.output)
        self.assertIn("RuntimeError", rendered)
        self.assertIn("ValueError", rendered)
        self.assertNotIn("HEALTH_SECRET", rendered)
        self.assertNotIn("FLUSH_SECRET", rendered)

    def test_poll_callback_publishes_merged_health_but_reports_raw_health(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine)
        repository = SqlAlchemyDeviceIdentityRepository(sessions)
        external = []
        observed_at = datetime(2026, 7, 20, 4, 0)
        runtime = create_device_identity_runtime(
            client=Client(),
            repository=repository,
            session_factory=sessions,
            detector=Detector(),
            clock=lambda: datetime(2030, 1, 1, 12, 0),
            health_clock=lambda: observed_at,
            on_device_health_change=lambda *args: external.append(args),
            identity_association_mode="legacy",
        )
        runtime.device_health_store.mark_heartbeat("front-door", observed_at)

        runtime.poller.on_health_change("front-door", False, "request_failed")

        self.assertTrue(runtime.coordinator._device_health["front-door"])
        self.assertEqual(
            [("front-door", False, "request_failed")], external
        )

    def test_unbound_realtime_pipeline_remains_reusable(self):
        class EmptyDetector:
            def initialize(self):
                pass

            def process_frame(self, frame):
                return []

            def reset(self):
                pass

        class OneFrameCapture:
            def __init__(self):
                self._reads = 0

            def isOpened(self):
                return True

            def get(self, property_id):
                return 30.0

            def read(self):
                self._reads += 1
                if self._reads == 1:
                    return True, np.zeros((4, 4, 3), dtype=np.uint8)
                return False, None

            def release(self):
                pass

        pipeline = RealtimeEntryPipeline(
            detector=EmptyDetector(),
            process_every_seconds=0,
        )

        with patch(
            "pipelines.legacy.realtime_entry_pipeline.open_video_capture",
            side_effect=[OneFrameCapture(), OneFrameCapture()],
        ):
            first = pipeline.run("first.mp4")
            second = pipeline.run("second.mp4")

        self.assertEqual(1, first["total_frames_processed"])
        self.assertEqual(1, second["total_frames_processed"])

    def test_stop_at_pipeline_run_entry_never_opens_camera_or_leaves_services(self):
        lifecycle = []

        class RunEntryDetector:
            def __init__(self):
                self.initialize_count = 0

            def initialize(self):
                self.initialize_count += 1

            def process_frame(self, frame):
                raise AssertionError("frame processing must not start")

            def reset(self):
                pass

        detector = RunEntryDetector()
        pipeline = RealtimeEntryPipeline(detector=detector)
        runtime = DeviceIdentityRuntime(
            pipeline=pipeline,
            poller=RecordingPoller(lifecycle),
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(lifecycle),
        )
        real_pipeline_run = pipeline.run

        def stop_at_run_entry(video_source):
            runtime.request_stop()
            return real_pipeline_run(video_source)

        pipeline.run = stop_at_run_entry

        with patch(
            "pipelines.device_identity_runtime.threading.Thread",
            side_effect=lambda **kwargs: RecordingThread(lifecycle, **kwargs),
        ), patch(
            "pipelines.legacy.realtime_entry_pipeline.open_video_capture"
        ) as open_capture:
            result = runtime.run("camera-0")

        open_capture.assert_not_called()
        self.assertEqual(0, detector.initialize_count)
        self.assertEqual(0, result["total_frames_read"])
        self.assertFalse(runtime._services_started)
        self.assertEqual(
            [
                "business.start",
                "poller.start",
                "identity-coordinator-front-door.start",
                "identity-coordinator-front-door.join:2.0",
                "poller.stop",
                "business.stop",
            ],
            lifecycle,
        )

    def test_signal_during_business_start_does_not_deadlock_or_start_poller(self):
        lifecycle = []
        handlers = {}
        business = RecordingBusiness(lifecycle)
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=RecordingPoller(lifecycle),
            coordinator=RecordingCoordinator(),
            member_business_runtime=business,
        )
        business.start_callback = lambda: handlers[signal.SIGINT](signal.SIGINT, None)

        def register_handler(signum, handler):
            handlers[signum] = handler

        with patch(
            "run_device_identity.build_runtime", return_value=runtime
        ), patch(
            "run_device_identity.signal.signal", side_effect=register_handler
        ):
            runner = threading.Thread(
                target=main,
                args=(runtime_settings(),),
                daemon=True,
            )
            runner.start()
            runner.join(timeout=0.5)

        self.assertFalse(runner.is_alive(), "signal handler deadlocked during startup")
        self.assertEqual(
            ["business.start", "pipeline.stop", "business.stop"], lifecycle
        )
        self.assertFalse(runtime._services_started)
        runtime.start_background_services()
        self.assertEqual(
            ["business.start", "pipeline.stop", "business.stop"], lifecycle
        )

    def test_signal_immediately_before_run_prevents_all_service_startup(self):
        lifecycle = []
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=RecordingPoller(lifecycle),
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(lifecycle),
        )

        def register_and_interrupt(signum, handler):
            if signum == signal.SIGTERM:
                handler(signum, None)

        with patch(
            "run_device_identity.build_runtime", return_value=runtime
        ), patch(
            "run_device_identity.signal.signal", side_effect=register_and_interrupt
        ), patch(
            "pipelines.device_identity_runtime.threading.Thread",
            side_effect=lambda **kwargs: RecordingThread(lifecycle, **kwargs),
        ):
            main(runtime_settings(VIDEO_SOURCE="3"))

        self.assertEqual(["pipeline.stop"], lifecycle)
        self.assertFalse(runtime._services_started)
        runtime.start_background_services()
        self.assertEqual(["pipeline.stop"], lifecycle)

    def test_device_lifecycle_wraps_poller_and_sweeper_with_business_runtime(self):
        lifecycle = []
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=RecordingPoller(lifecycle),
            inbox_worker=RecordingInboxWorker(lifecycle),
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(lifecycle),
        )

        with patch(
            "pipelines.device_identity_runtime.threading.Thread",
            side_effect=lambda **kwargs: RecordingThread(lifecycle, **kwargs),
        ):
            runtime.start_background_services()
            runtime.start_background_services()
            runtime.stop()
            runtime.stop()

        self.assertEqual(
            [
                "business.start",
                "inbox.start",
                "poller.start",
                "identity-coordinator-front-door.start",
                "pipeline.stop",
                "identity-coordinator-front-door.join:2.0",
                "poller.stop",
                "inbox.stop",
                "business.stop",
            ],
            lifecycle,
        )

    def test_device_lifecycle_supports_worker_without_compensation_poller(self):
        lifecycle = []
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=None,
            inbox_worker=RecordingInboxWorker(lifecycle),
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(lifecycle),
            device_id="front-door",
        )

        with patch(
            "pipelines.device_identity_runtime.threading.Thread",
            side_effect=lambda **kwargs: RecordingThread(lifecycle, **kwargs),
        ):
            runtime.start_background_services()
            runtime.stop()

        self.assertEqual(
            [
                "business.start",
                "inbox.start",
                "identity-coordinator-front-door.start",
                "pipeline.stop",
                "identity-coordinator-front-door.join:2.0",
                "inbox.stop",
                "business.stop",
            ],
            lifecycle,
        )

    def test_device_start_failure_rolls_back_business_runtime(self):
        lifecycle = []
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=RecordingPoller(
                lifecycle, start_error=RuntimeError("poller unavailable")
            ),
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(lifecycle),
        )

        with self.assertRaisesRegex(RuntimeError, "poller unavailable"):
            runtime.start_background_services()

        self.assertEqual(
            [
                "business.start",
                "poller.start",
                "poller.stop",
                "business.stop",
            ],
            lifecycle,
        )

    def test_partial_start_cleanup_timeout_remains_retryable_until_child_exits(self):
        lifecycle = []
        entered = threading.Event()
        release = threading.Event()
        real_thread_type = threading.Thread

        class BlockingPoller(RecordingPoller):
            def __init__(self):
                super().__init__(lifecycle)
                self._thread = None

            def start(self):
                lifecycle.append("poller.start")

                def block():
                    entered.set()
                    release.wait(timeout=5)

                self._thread = real_thread_type(target=block, daemon=True)
                self._thread.start()
                self.assert_started()

            def assert_started(self):
                if not entered.wait(timeout=1):
                    raise RuntimeError("poller child did not start")

            def stop(self):
                lifecycle.append("poller.stop")
                self._thread.join(timeout=0.01)
                if self._thread.is_alive():
                    raise RuntimeError("poller cleanup still running")

        class FailingStartThread(RecordingThread):
            def start(self):
                lifecycle.append(f"{self.name}.start")
                raise RuntimeError("sweeper failed to start")

        poller = BlockingPoller()
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=poller,
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(lifecycle),
        )

        try:
            with patch(
                "pipelines.device_identity_runtime.threading.Thread",
                side_effect=lambda **kwargs: FailingStartThread(
                    lifecycle, **kwargs
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "poller cleanup still running"
                ):
                    runtime.start_background_services()

            self.assertTrue(poller._thread.is_alive())
            self.assertTrue(runtime._services_started)
            self.assertEqual("business.stop", lifecycle[-1])

            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                runtime.start_background_services()

            with self.assertRaisesRegex(
                RuntimeError, "poller cleanup still running"
            ):
                runtime.stop()
            self.assertTrue(runtime._services_started)

            release.set()
            poller._thread.join(timeout=1)
            runtime.stop()

            self.assertFalse(poller._thread.is_alive())
            self.assertFalse(runtime._services_started)
        finally:
            release.set()
            if poller._thread is not None:
                poller._thread.join(timeout=1)

    def test_business_start_failure_still_requests_business_stop(self):
        lifecycle = []
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=RecordingPoller(lifecycle),
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(
                lifecycle, start_error=RuntimeError("business partial start")
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "business partial start"):
            runtime.start_background_services()

        self.assertEqual(["business.start", "business.stop"], lifecycle)

    def test_device_stop_reports_sweeper_that_outlives_join_timeout(self):
        lifecycle = []
        runtime = DeviceIdentityRuntime(
            pipeline=RecordingPipeline(lifecycle),
            poller=RecordingPoller(lifecycle),
            coordinator=RecordingCoordinator(),
            member_business_runtime=RecordingBusiness(lifecycle),
        )

        with patch(
            "pipelines.device_identity_runtime.threading.Thread",
            side_effect=lambda **kwargs: StuckThread(lifecycle, **kwargs),
        ):
            runtime.start_background_services()
            with self.assertRaisesRegex(RuntimeError, "still running"):
                runtime.stop()

        self.assertTrue(runtime._services_started)
        self.assertEqual("business.stop", lifecycle[-1])

    def test_factory_finalizes_durable_entry_before_external_callback(self):
        lifecycle = []
        business = RecordingBusiness(lifecycle)
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine)
        runtime = create_device_identity_runtime(
            client=Client(),
            repository=Repository(),
            session_factory=sessions,
            detector=Detector(),
            member_business_runtime=business,
            on_member_entry=lambda session: lifecycle.append(
                ("external.handle", session)
            ),
            identity_association_mode="legacy",
        )
        session = {"device_event_id": "event-1", "member_id": "member-1"}

        runtime.coordinator.on_member_entry(session)

        self.assertEqual(
            [
                ("business.handle", session),
                ("external.handle", session),
            ],
            lifecycle,
        )
        self.assertIsNone(runtime.pipeline.face_recognizer)
        self.assertEqual("device_api", runtime.pipeline.identity_mode)
        self.assertEqual(
            business.capture_track,
            runtime.pipeline.on_stable_track_frame,
        )

    def test_factory_uses_explicit_session_factory_with_in_memory_repository(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine)

        runtime = create_device_identity_runtime(
            client=Client(),
            repository=InMemoryDeviceIdentityRepository(),
            session_factory=sessions,
            detector=Detector(),
            identity_association_mode="legacy",
        )

        self.assertIs(runtime.poller.intake.session_factory, sessions)
        self.assertIs(runtime.inbox_worker.session_factory, sessions)


if __name__ == "__main__":
    unittest.main()
