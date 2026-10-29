from __future__ import annotations

import unittest
from dataclasses import dataclass
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pipelines.behavior_runtime import BehaviorCameraRuntime, BehaviorRuntime
from pipelines.store_runtime import StoreRuntime
from run_behavior_runtime import validate_runtime_settings as validate_behavior_settings
from run_behavior_runtime import build_legacy_store_runtime
from run_device_identity import validate_runtime_settings as validate_device_settings
from run_store_runtime import build_runtime as build_store_runtime


@dataclass
class _Lease:
    store_id: str
    mode: str
    owner_id: str


class _LeaseService:
    def __init__(self, *, fail_acquire: bool = False, fail_release: bool = False):
        self.fail_acquire = fail_acquire
        self.fail_release = fail_release
        self.acquired = []
        self.heartbeats = []
        self.heartbeat_observed = Event()
        self.released = []
        self.release_calls = 0

    def acquire(self, *, store_id, mode, owner_id):
        if self.fail_acquire:
            raise RuntimeError("lease unavailable")
        lease = _Lease(store_id, mode, owner_id)
        self.acquired.append(lease)
        return lease

    def release(self, lease):
        self.release_calls += 1
        if self.fail_release:
            raise OSError("lease database unavailable")
        self.released.append(lease)

    def heartbeat(self, lease):
        self.heartbeats.append(lease)
        self.heartbeat_observed.set()
        return lease


class _Component:
    def __init__(
        self,
        *,
        fail_start=False,
        fail_stop=False,
        start_result=None,
        on_start=None,
        name=None,
        events=None,
    ):
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.start_result = start_result
        self.on_start = on_start
        self.name = name
        self.events = events
        self.started = False
        self.stopped = False

    def start(self):
        if self.events is not None:
            self.events.append(f"{self.name}:start")
        if self.fail_start:
            raise RuntimeError("component failed")
        if self.on_start is not None:
            self.on_start()
        self.started = True
        return self.start_result

    def stop(self):
        if self.events is not None:
            self.events.append(f"{self.name}:stop")
        self.stopped = True
        if self.fail_stop:
            raise RuntimeError("component stop failed")


class StoreRuntimeTests(unittest.TestCase):
    def _build(self, *, mode="new", behavior=None, identity=None, **kwargs):
        lease_service = kwargs.pop("lease_service", _LeaseService())
        behavior_builder = kwargs.pop("behavior_builder", None)
        identity_builder = kwargs.pop("identity_builder", None)
        behavior = behavior or _Component()
        identity = identity or _Component()
        runtime = StoreRuntime.build(
            session_factory=lambda: None,
            store_id="store-1",
            mode=mode,
            owner_id="runtime-1",
            lease_service=lease_service,
            behavior_builder=behavior_builder or (lambda deps: behavior),
            identity_builder=identity_builder or (lambda deps, on_lease_lost: identity),
            **kwargs,
        )
        return runtime, lease_service, behavior, identity

    def test_new_builds_one_shared_dependency_context(self):
        captured = {}

        def behavior_builder(deps):
            captured["behavior"] = deps
            return _Component()

        def identity_builder(deps, on_lease_lost):
            captured["identity"] = deps
            return _Component()

        runtime, lease_service, _, _ = self._build(
            behavior=None,
            identity=None,
            behavior_builder=behavior_builder,
            identity_builder=identity_builder,
        )
        self.assertEqual("new", runtime.mode)
        self.assertIs(captured["behavior"].track_snapshot_buffer, captured["identity"].track_snapshot_buffer)
        self.assertIs(captured["behavior"].track_identity_cache, captured["identity"].track_identity_cache)
        self.assertIs(captured["behavior"].candidate_provider, captured["identity"].candidate_provider)
        self.assertIs(runtime.dependencies, captured["behavior"])
        self.assertNotEqual(runtime.owner_id, runtime.dependencies.runtime_instance_id)
        self.assertTrue(runtime.dependencies.runtime_instance_id)
        self.assertIs(
            captured["behavior"].camera_runtime_registry,
            captured["identity"].camera_runtime_registry,
        )
        self.assertEqual(1, len(lease_service.acquired))

    def test_phase2b_gate_constructs_one_shared_finalizer_and_heartbeat(self):
        finalizer = _Component()
        heartbeat = _Component()
        visit_activity = _Component()
        finalizer_factory = MagicMock(return_value=finalizer)
        heartbeat_factory = MagicMock(return_value=heartbeat)
        visit_activity_factory = MagicMock(return_value=visit_activity)

        runtime, _, _, _ = self._build(
            phase2b_finalization_enabled=True,
            finalization_worker_factory=finalizer_factory,
            binding_heartbeat_factory=heartbeat_factory,
            visit_activity_refresher_factory=visit_activity_factory,
        )

        self.assertIs(finalizer, runtime.dependencies.finalization_worker)
        self.assertIs(heartbeat, runtime.dependencies.binding_heartbeat)
        self.assertIs(visit_activity, runtime.dependencies.visit_activity_refresher)
        self.assertTrue(runtime.dependencies.resolver.phase2b_finalization_enabled)
        finalizer_kwargs = finalizer_factory.call_args.kwargs
        heartbeat_kwargs = heartbeat_factory.call_args.kwargs
        self.assertIs(
            runtime.dependencies.track_snapshot_buffer,
            finalizer_kwargs["track_snapshot_buffer"],
        )
        self.assertIs(
            runtime.dependencies.track_identity_cache,
            finalizer_kwargs["track_identity_cache"],
        )
        self.assertIs(
            runtime.dependencies.track_snapshot_buffer,
            heartbeat_kwargs["track_snapshot_buffer"],
        )
        self.assertIs(
            runtime.dependencies.track_identity_cache,
            heartbeat_kwargs["track_identity_cache"],
        )
        self.assertIs(
            runtime.dependencies.track_identity_cache,
            visit_activity_factory.call_args.kwargs["track_identity_cache"],
        )

    def test_phase2b_gate_disabled_constructs_no_consumer(self):
        finalizer_factory = MagicMock()
        heartbeat_factory = MagicMock()
        visit_activity_factory = MagicMock()

        runtime, _, _, _ = self._build(
            phase2b_finalization_enabled=False,
            finalization_worker_factory=finalizer_factory,
            binding_heartbeat_factory=heartbeat_factory,
            visit_activity_refresher_factory=visit_activity_factory,
        )

        finalizer_factory.assert_not_called()
        heartbeat_factory.assert_not_called()
        visit_activity_factory.assert_not_called()
        self.assertIsNone(runtime.dependencies.finalization_worker)
        self.assertIsNone(runtime.dependencies.binding_heartbeat)
        self.assertIsNone(runtime.dependencies.visit_activity_refresher)
        self.assertFalse(runtime.dependencies.resolver.phase2b_finalization_enabled)

    def test_legacy_mode_rejects_phase2b_gate(self):
        with self.assertRaisesRegex(ValueError, "requires new mode"):
            self._build(mode="legacy", phase2b_finalization_enabled=True)

    def test_visit_activity_writer_starts_before_runtime_and_stops_before_teardown(self):
        events = []
        behavior = _Component(name="behavior", events=events)
        identity = _Component(name="identity", events=events)
        visit_activity = _Component(name="visit_activity", events=events)
        runtime, _, _, _ = self._build(
            behavior=behavior,
            identity=identity,
            phase2b_finalization_enabled=True,
            finalization_worker_factory=lambda *_args, **_kwargs: _Component(),
            binding_heartbeat_factory=lambda *_args, **_kwargs: _Component(),
            visit_activity_refresher_factory=lambda *_args, **_kwargs: visit_activity,
        )

        runtime.start()
        runtime.stop()

        self.assertLess(events.index("visit_activity:start"), events.index("behavior:start"))
        self.assertLess(events.index("visit_activity:stop"), events.index("identity:stop"))
        self.assertLess(events.index("visit_activity:stop"), events.index("behavior:stop"))

    def test_identity_integrity_fatal_stops_visit_activity_before_runtime(self):
        events = []
        behavior = _Component(name="behavior", events=events)
        identity = _Component(name="identity", events=events)
        visit_activity = _Component(name="visit_activity", events=events)
        runtime, _, _, _ = self._build(
            behavior=behavior,
            identity=identity,
            phase2b_finalization_enabled=True,
            finalization_worker_factory=lambda *_args, **_kwargs: _Component(),
            binding_heartbeat_factory=lambda *_args, **_kwargs: _Component(),
            visit_activity_refresher_factory=lambda *_args, **_kwargs: visit_activity,
        )
        runtime.start()

        runtime.signal_fatal("identity_integrity_violation", "test_invariant")
        self.assertTrue(runtime._stopped.wait(1.0))

        self.assertTrue(visit_activity.stopped)
        self.assertLess(events.index("visit_activity:stop"), events.index("identity:stop"))
        self.assertLess(events.index("visit_activity:stop"), events.index("behavior:stop"))

    def test_composition_refreshes_lease_around_component_construction(self):
        runtime, lease_service, _, _ = self._build()
        self.assertGreaterEqual(len(lease_service.heartbeats), 2)
        runtime.stop()

    def test_each_store_runtime_build_gets_a_new_runtime_instance_id(self):
        first, _, _, _ = self._build()
        first_id = first.runtime_instance_id
        first.stop()
        second, _, _, _ = self._build()
        try:
            self.assertNotEqual(first_id, second.runtime_instance_id)
        finally:
            second.stop()

    def test_composition_heartbeats_while_component_is_being_built(self):
        lease_service = _LeaseService()

        def build_behavior(_dependencies):
            if not lease_service.heartbeat_observed.wait(0.2):
                raise RuntimeError("lease keeper did not heartbeat")
            return _Component()

        runtime, _, _, _ = self._build(
            lease_service=lease_service,
            behavior_builder=build_behavior,
            composition_heartbeat_interval_seconds=0.01,
        )
        runtime.stop()

    def test_multiple_behavior_cameras_share_store_buffer_and_cache(self):
        captured = {}

        def behavior_builder(deps):
            first = BehaviorCameraRuntime(
                object(), object(),
                track_snapshot_buffer=deps.track_snapshot_buffer,
                track_identity_cache=deps.track_identity_cache,
            )
            second = BehaviorCameraRuntime(
                object(), object(),
                track_snapshot_buffer=deps.track_snapshot_buffer,
                track_identity_cache=deps.track_identity_cache,
            )
            captured["runtime"] = BehaviorRuntime(
                {"camera-1": first, "camera-2": second},
                track_snapshot_buffer=deps.track_snapshot_buffer,
                track_identity_cache=deps.track_identity_cache,
            )
            return _Component()

        runtime, _, _, _ = self._build(behavior_builder=behavior_builder)
        behavior = captured["runtime"]
        self.assertIs(runtime.dependencies.track_snapshot_buffer, behavior.track_snapshot_buffer)
        self.assertIs(runtime.dependencies.track_identity_cache, behavior.track_identity_cache)
        self.assertTrue(all(
            camera.track_snapshot_buffer is runtime.dependencies.track_snapshot_buffer
            and camera.track_identity_cache is runtime.dependencies.track_identity_cache
            for camera in behavior.cameras.values()
        ))

    def test_legacy_has_no_new_association_dependencies(self):
        runtime, _, _, _ = self._build(mode="legacy")
        self.assertEqual("legacy", runtime.mode)
        self.assertIsNone(runtime.dependencies.resolver)
        self.assertIsNone(runtime.dependencies.projection_worker)
        self.assertIsNone(runtime.dependencies.candidate_provider)

    def test_start_failure_stops_started_component_and_releases_lease(self):
        behavior = _Component()
        identity = _Component(fail_start=True)
        runtime, lease_service, _, _ = self._build(
            behavior=behavior,
            identity=identity,
        )

        with self.assertRaisesRegex(RuntimeError, "component failed"):
            runtime.start()

        self.assertTrue(behavior.started)
        self.assertTrue(behavior.stopped)
        self.assertTrue(identity.stopped)
        self.assertEqual(1, len(lease_service.released))
        self.assertEqual("stopped", runtime.state)

    def test_start_failure_keeps_lease_when_component_cannot_stop(self):
        behavior = _Component(fail_stop=True)
        identity = _Component(fail_start=True)
        runtime, lease_service, _, _ = self._build(
            behavior=behavior,
            identity=identity,
        )

        with self.assertRaisesRegex(RuntimeError, "component failed"):
            runtime.start()

        self.assertEqual([], lease_service.released)
        self.assertEqual("failed", runtime.state)
        with self.assertRaisesRegex(RuntimeError, "StoreRuntime is failed"):
            runtime.start()

    def test_release_failure_keeps_failed_state_until_stop_retry_succeeds(self):
        lease_service = _LeaseService(fail_release=True)
        runtime, _, _, _ = self._build(lease_service=lease_service)
        runtime.start()

        with self.assertRaisesRegex(OSError, "lease database unavailable"):
            runtime.stop()

        self.assertEqual("failed", runtime.state)
        self.assertEqual([], lease_service.released)
        lease_service.fail_release = False
        runtime.stop()
        self.assertEqual("stopped", runtime.state)
        self.assertEqual(1, len(lease_service.released))

    def test_construction_failure_stops_created_component_and_releases_lease(self):
        behavior = _Component()
        lease_service = _LeaseService()

        def identity_builder(_deps, _on_lease_lost):
            raise RuntimeError("identity construction failed")

        with self.assertRaisesRegex(RuntimeError, "identity construction failed"):
            StoreRuntime.build(
                session_factory=lambda: None,
                store_id="store-1",
                mode="new",
                owner_id="runtime-1",
                lease_service=lease_service,
                behavior_builder=lambda deps: behavior,
                identity_builder=identity_builder,
            )
        self.assertTrue(behavior.stopped)
        self.assertEqual(1, len(lease_service.released))

    def test_construction_and_release_failure_is_reported_after_retries(self):
        lease_service = _LeaseService(fail_release=True)

        with self.assertRaisesRegex(
            RuntimeError, "construction failed and lease release failed"
        ):
            StoreRuntime.build(
                session_factory=lambda: None,
                store_id="store-1",
                mode="new",
                owner_id="runtime-1",
                lease_service=lease_service,
                behavior_builder=lambda _deps: _Component(),
                identity_builder=lambda _deps, _callback: (_ for _ in ()).throw(
                    RuntimeError("identity construction failed")
                ),
            )
        self.assertEqual(3, lease_service.release_calls)

    def test_lease_loss_stops_store_components(self):
        runtime, lease_service, behavior, identity = self._build()
        runtime.start()
        runtime.handle_lease_lost("runtime_lease_lost")

        self.assertTrue(behavior.stopped)
        self.assertTrue(identity.stopped)
        self.assertEqual("failed", runtime.state)
        self.assertEqual(0, len(lease_service.released))

    def test_lease_loss_completes_cleanup_when_component_stop_fails(self):
        runtime, lease_service, behavior, identity = self._build(
            identity=_Component(fail_stop=True),
        )
        runtime.start()

        runtime.handle_lease_lost("runtime_lease_lost")

        self.assertTrue(identity.stopped)
        self.assertTrue(behavior.stopped)
        self.assertEqual(0, len(lease_service.released))
        self.assertEqual("failed", runtime.state)

    def test_start_and_stop_use_required_component_order(self):
        events = []
        runtime, _, _, _ = self._build(
            behavior=_Component(name="behavior", events=events),
            identity=_Component(name="identity", events=events),
        )
        runtime.start()
        runtime.stop()
        self.assertEqual(
            ["behavior:start", "identity:start", "identity:stop", "behavior:stop"],
            events,
        )

    def test_stop_requested_during_startup_rolls_back_before_running(self):
        runtime_holder = {}
        behavior = _Component(on_start=lambda: runtime_holder["runtime"].stop())
        runtime, lease_service, _, identity = self._build(behavior=behavior)
        runtime_holder["runtime"] = runtime

        with self.assertRaisesRegex(RuntimeError, "startup.*stopped"):
            runtime.start()

        self.assertTrue(behavior.stopped)
        self.assertTrue(identity.stopped)
        self.assertEqual("stopped", runtime.state)
        self.assertEqual(1, len(lease_service.released))

    def test_false_start_result_rolls_back_store_runtime(self):
        behavior = _Component(start_result=False)
        runtime, lease_service, _, identity = self._build(behavior=behavior)
        with self.assertRaisesRegex(RuntimeError, "reported startup failure"):
            runtime.start()
        self.assertTrue(behavior.stopped)
        self.assertTrue(identity.stopped)
        self.assertEqual(1, len(lease_service.released))

    def test_released_store_runtime_cannot_restart(self):
        runtime, _, _, _ = self._build()
        runtime.start()
        runtime.stop()
        with self.assertRaisesRegex(RuntimeError, "released"):
            runtime.start()

    def test_invalid_mode_fails_before_acquiring_lease(self):
        lease_service = _LeaseService()
        with self.assertRaises(ValueError):
            StoreRuntime.build(
                session_factory=lambda: None,
                store_id="store-1",
                mode="",
                owner_id="runtime-1",
                lease_service=lease_service,
                behavior_builder=lambda deps: _Component(),
                identity_builder=lambda deps, on_lease_lost: _Component(),
            )
        self.assertEqual([], lease_service.acquired)

    def test_lease_failure_prevents_component_construction(self):
        constructed = []
        with self.assertRaisesRegex(RuntimeError, "lease unavailable"):
            StoreRuntime.build(
                session_factory=lambda: None,
                store_id="store-1",
                mode="new",
                owner_id="runtime-1",
                lease_service=_LeaseService(fail_acquire=True),
                behavior_builder=lambda deps: constructed.append("behavior"),
                identity_builder=lambda deps, on_lease_lost: constructed.append("identity"),
            )
        self.assertEqual([], constructed)

    def test_old_component_entries_reject_new_production_mode(self):
        configured = SimpleNamespace(
            IDENTITY_ASSOCIATION_MODE="new",
            IDENTITY_COMPONENT_DEBUG=False,
        )
        with self.assertRaisesRegex(RuntimeError, "StoreRuntime"):
            validate_device_settings(configured)
        with self.assertRaisesRegex(RuntimeError, "StoreRuntime"):
            validate_behavior_settings(configured)

    def test_component_entries_require_explicit_mode(self):
        with self.assertRaisesRegex(ValueError, "IDENTITY_ASSOCIATION_MODE"):
            validate_device_settings(SimpleNamespace())
        with self.assertRaisesRegex(ValueError, "IDENTITY_ASSOCIATION_MODE"):
            validate_behavior_settings(SimpleNamespace())

    def test_legacy_device_entry_requires_store_lease_context(self):
        configured = SimpleNamespace(
            IDENTITY_ASSOCIATION_MODE="legacy",
            IDENTITY_COMPONENT_DEBUG=False,
            IDENTITY_ASSOCIATION_STORE_ID="",
            IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID="",
        )
        with self.assertRaisesRegex(ValueError, "IDENTITY_ASSOCIATION_STORE_ID"):
            validate_device_settings(configured)

    @patch("run_behavior_runtime.BehaviorCaptureRunner")
    @patch("run_behavior_runtime.create_behavior_runtime")
    @patch("run_behavior_runtime.init_db")
    @patch("run_behavior_runtime.validate_runtime_settings")
    def test_legacy_behavior_entry_uses_store_runtime_lease(
        self,
        validate_settings,
        _init_db,
        _behavior_factory,
        runner_factory,
    ):
        validate_settings.return_value = SimpleNamespace(
            identity_association_mode="legacy",
            cameras=(SimpleNamespace(
                camera_id="camera-1", store_id="store-1", source="0"
            ),),
            person_model_path="person.pt",
            staff_vest_model_path="vest.pt",
            pose_model_path="pose.pt",
            device="cpu",
            tracker="bytetrack.yaml",
            confidence=0.5,
            staff_vest_enabled=True,
            staff_vest_confidence=0.68,
            role_window_frames=5,
            greeting_mock_staff_name="Staff",
            staff_hit_frames=3,
            maximum_missing_frames=8,
            greeting_v2_enabled=True,
        )
        runner_factory.return_value = _Component()
        lease_service = _LeaseService()
        runtime = build_legacy_store_runtime(
            SimpleNamespace(
                IDENTITY_ASSOCIATION_MODE="legacy",
                IDENTITY_COMPONENT_DEBUG=False,
                IDENTITY_ASSOCIATION_STORE_ID="store-1",
                IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID="legacy-runtime-1",
            ),
            session_factory=lambda: None,
            lease_service=lease_service,
        )

        self.assertEqual("legacy", lease_service.acquired[0].mode)
        self.assertIsNone(runtime.dependencies.resolver)
        runtime.start()
        runtime.stop()
        self.assertEqual(1, len(lease_service.released))

    @patch("run_store_runtime.create_device_identity_runtime")
    @patch("run_store_runtime.BehaviorCaptureRunner")
    @patch("run_store_runtime.create_behavior_runtime")
    @patch("run_store_runtime.DeviceCaptureStore")
    @patch("run_store_runtime.SqlAlchemyDeviceIdentityRepository")
    @patch("run_store_runtime.CameraFaceApiClient")
    @patch("run_store_runtime.init_db")
    @patch("run_store_runtime.validate_store_runtime_settings")
    def test_new_production_entry_injects_one_store_context(
        self,
        validate_settings,
        init_db,
        client_factory,
        repository_factory,
        capture_factory,
        behavior_factory,
        runner_factory,
        identity_factory,
    ):
        camera = SimpleNamespace(camera_id="camera-1", store_id="store-1", source="0")
        validate_settings.return_value = SimpleNamespace(
            cameras=(camera,),
            person_model_path="person.pt",
            staff_vest_model_path="vest.pt",
            pose_model_path="pose.pt",
            device="cpu",
            tracker="bytetrack.yaml",
            confidence=0.5,
            staff_vest_enabled=True,
            staff_vest_confidence=0.68,
            role_window_frames=5,
            staff_hit_frames=3,
            greeting_mock_staff_name="Staff",
            maximum_missing_frames=8,
            greeting_v2_enabled=True,
        )
        configured = SimpleNamespace(
            IDENTITY_ASSOCIATION_MODE="new",
            IDENTITY_ASSOCIATION_STORE_ID="store-1",
            IDENTITY_ASSOCIATION_RUNTIME_OWNER_ID="runtime-1",
            CAMERA_FACE_DEVICE_ID="device-1",
            CAMERA_FACE_BASE_URL="http://camera.local",
            CAMERA_FACE_USERNAME="operator",
            CAMERA_FACE_PASSWORD="secret",
            CAMERA_FACE_UNIQUE_CODE="code",
            CAMERA_FACE_CONNECT_TIMEOUT=2.0,
            CAMERA_FACE_READ_TIMEOUT=5.0,
            P6S_CAPTURE_DIR="captures",
            P6S_MAX_CAPTURE_BYTES=1_000_000,
            P6S_MAX_CAPTURE_PIXELS=1_000_000,
            P6S_SIMILARITY_THRESHOLD=0.8,
            YOLO_MODEL_PATH="model.pt",
            CAMERA_FACE_POLL_INTERVAL=30.0,
            DEVICE_EVENT_WORKER_INTERVAL=0.25,
            DEVICE_EVENT_PROCESSING_TIMEOUT=300.0,
            DEVICE_HEALTH_FRESHNESS_SECONDS=90.0,
            VIDEO_SOURCE="0",
        )
        behavior_component = _Component()
        identity_component = _Component()
        runner_factory.return_value = behavior_component
        identity_factory.return_value = identity_component
        lease_service = _LeaseService()

        runtime = build_store_runtime(
            configured,
            session_factory=lambda: None,
            lease_service=lease_service,
        )
        runtime.start()

        behavior_kwargs = behavior_factory.call_args.kwargs
        identity_kwargs = identity_factory.call_args.kwargs
        self.assertIs(
            runtime.dependencies.track_snapshot_buffer,
            behavior_kwargs["track_snapshot_buffer"],
        )
        self.assertIs(
            runtime.dependencies.track_identity_cache,
            behavior_kwargs["track_identity_cache"],
        )
        self.assertIs(
            runtime.dependencies.candidate_provider,
            identity_kwargs["track_candidate_provider"],
        )
        self.assertIs(runtime.dependencies.lease, identity_kwargs["association_lease"])
        self.assertIs(runtime.dependencies.resolver, identity_kwargs["resolver"])
        self.assertIs(
            runtime.dependencies.projection_worker,
            identity_kwargs["projection_worker"],
        )
        self.assertEqual(
            runtime.dependencies.runtime_instance_id,
            behavior_kwargs["runtime_instance_id"],
        )
        self.assertEqual(
            runtime.dependencies.runtime_instance_id,
            identity_kwargs["runtime_instance_id"],
        )
        self.assertIs(
            runtime.dependencies.association_audit,
            identity_kwargs["association_audit_service"],
        )
        self.assertIsNone(identity_kwargs["member_business_runtime"])
        self.assertTrue(behavior_component.started)
        self.assertTrue(identity_component.started)
        runtime.stop()


if __name__ == "__main__":
    unittest.main()
