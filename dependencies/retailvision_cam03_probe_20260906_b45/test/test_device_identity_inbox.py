from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.camera_face_api import CameraApiTransportError
from agent.schema_evolution import evolve_schema
from agent.device_recognition_intake import DeviceRecognitionIntake
from agent.device_identity import (
    DeviceMemberMappingData,
    InMemoryDeviceIdentityRepository,
    RecognitionRecordPoller,
    SqlAlchemyDeviceIdentityRepository,
)
from agent.models import (
    DeviceMemberMapping,
    DeviceRecognitionInbox,
    DeviceRecognitionCursor,
    Member,
    MemberEntryEvent,
)


NOW = datetime(2026, 7, 14, 12, 0, 10)


def recognition_record(
    log_id=1,
    second=10,
    *,
    group_id=2,
    person_id=19,
    unique_id="vendor-person-19",
    pass_code=0,
    similarity=0.83,
    rule_id=1,
    reco_valid=True,
):
    return {
        "log_id": log_id,
        "group_id": group_id,
        "person_id": person_id,
        "unique_id": unique_id,
        "occurred_at": datetime(2026, 7, 14, 12, 0, second),
        "pass_code": pass_code,
        "similarity": similarity,
        "rule_id": rule_id,
        "reco_valid": reco_valid,
    }


class FakeClient:
    device_id = "front-door"

    def __init__(self, records=None, error=None):
        self.records = records
        self.error = error

    def query_recognition_records(self, start_time, stop_time, **kwargs):
        if self.error:
            raise self.error
        if kwargs["page_num"] > 1:
            return []
        if self.records is not None:
            return list(self.records)
        return [recognition_record()]


class TransientCursorRepository(InMemoryDeviceIdentityRepository):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures
        self.get_cursor_calls = 0
        self.failure_seen = threading.Event()
        self.success_seen = threading.Event()

    def get_cursor(self, device_id):
        self.get_cursor_calls += 1
        if self.get_cursor_calls <= self.failures:
            self.failure_seen.set()
            raise RuntimeError("database unavailable: TOP-SECRET")
        self.success_seen.set()
        return super().get_cursor(device_id)


class DeviceIdentityInboxTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(engine)
        self.addCleanup(engine.dispose)
        self.session_factory = sessionmaker(bind=engine)
        session = self.session_factory()
        session.add(Member(id="member-19", name="Member", is_active=True))
        session.commit()
        session.close()
        self.repository = SqlAlchemyDeviceIdentityRepository(self.session_factory)
        self.repository.save_mapping(
            DeviceMemberMappingData(
                device_id="front-door",
                member_id="member-19",
                group_id=2,
                group_uuid="group-uuid",
                person_id=19,
                unique_id="vendor-person-19",
                face_uuid="face-uuid",
                sync_status="synced",
            )
        )
        self.ingestor = DeviceRecognitionIntake(self.session_factory)

    def tearDown(self):
        pass

    def test_poller_persists_recognized_event_in_durable_inbox(self):
        poller = RecognitionRecordPoller(
            client=FakeClient(),
            repository=self.repository,
            intake=self.ingestor,
            clock=lambda: NOW,
        )

        results = poller.poll_once()

        self.assertEqual(["received"], [result.status for result in results])
        session = self.session_factory()
        self.addCleanup(session.close)
        self.assertEqual(1, session.query(DeviceRecognitionInbox).count())
        self.assertEqual(0, session.query(MemberEntryEvent).count())

    def test_clock_warning_callback_failure_preserves_records_and_recovery(self):
        client = FakeClient(
            error=CameraApiTransportError("offline", retryable=True)
        )
        health = []
        warnings = []
        def fail_warning_callback(device_id, reason):
            warnings.append((device_id, reason))
            raise RuntimeError(
                "CLOCK-WARNING-SECRET <Ownner>raw-record</Ownner> "
                "credentials=device-password"
            )

        poller = RecognitionRecordPoller(
            client=client,
            repository=self.repository,
            intake=self.ingestor,
            on_health_change=lambda device_id, healthy, reason: health.append(
                (device_id, healthy, reason)
            ),
            on_warning=fail_warning_callback,
            clock=lambda: NOW,
        )
        poller.poll_once()
        poller.poll_once()
        poller.poll_once()
        self.assertFalse(poller.healthy)

        client.error = None
        client.records = [
            recognition_record(log_id=10, second=16, pass_code=1),
            recognition_record(
                log_id=11,
                second=17,
                person_id=999,
                unique_id="missing",
            ),
        ]
        with self.assertLogs("agent.device_identity", level="ERROR") as captured:
            first_emitted = poller.poll_once()
            second_emitted = poller.poll_once()

        self.assertEqual(["ignored", "waiting_mapping"], [item.status for item in first_emitted])
        self.assertEqual(["duplicate", "duplicate"], [item.status for item in second_emitted])
        session = self.session_factory()
        self.addCleanup(session.close)
        self.assertEqual(2, session.query(DeviceRecognitionInbox).count())
        self.assertEqual(0, session.query(MemberEntryEvent).count())
        cursor = session.query(DeviceRecognitionCursor).one()
        self.assertEqual(11, cursor.last_log_id)
        self.assertEqual(NOW, cursor.last_record_time)
        self.assertEqual(NOW, cursor.last_success_at)
        self.assertTrue(poller.healthy)
        self.assertEqual(0, poller._consecutive_failures)
        self.assertIn(("front-door", True, "recovered"), health)
        self.assertEqual(
            [
                ("front-door", "device_clock_ahead"),
                ("front-door", "device_clock_ahead"),
            ],
            warnings,
        )
        log_output = "\n".join(captured.output)
        self.assertIn("front-door", log_output)
        self.assertIn("RuntimeError", log_output)
        self.assertNotIn("device_clock_ahead", log_output)
        self.assertNotIn("CLOCK-WARNING-SECRET", log_output)
        self.assertNotIn("Ownner", log_output)
        self.assertNotIn("credentials", log_output)
        self.assertNotIn("device-password", log_output)
        self.assertNotIn("missing", log_output)

    def test_run_forever_survives_one_transient_repository_failure(self):
        repository = TransientCursorRepository(failures=1)
        poller = RecognitionRecordPoller(
            client=FakeClient(),
            repository=repository,
            intake=self.ingestor,
            poll_interval_seconds=0.01,
            clock=lambda: NOW,
        )

        poller.start()
        try:
            self.assertTrue(repository.failure_seen.wait(timeout=1))
            self.assertTrue(repository.success_seen.wait(timeout=1))
            self.assertTrue(poller._thread.is_alive())
        finally:
            poller.stop(timeout=1)

    def test_repository_failures_use_health_threshold_and_sanitized_logging(self):
        repository = TransientCursorRepository(failures=3)
        health = []
        recovered = threading.Event()

        def record_health(device_id, healthy, reason):
            health.append((device_id, healthy, reason))
            if healthy and reason == "recovered":
                recovered.set()

        poller = RecognitionRecordPoller(
            client=FakeClient(),
            repository=repository,
            intake=self.ingestor,
            on_health_change=record_health,
            poll_interval_seconds=0.01,
            clock=lambda: NOW,
        )

        with self.assertLogs("agent.device_identity", level="ERROR") as captured:
            poller.start()
            try:
                self.assertTrue(recovered.wait(timeout=1))
            finally:
                poller.stop(timeout=1)

        self.assertIn(("front-door", False, "request_failed"), health)
        self.assertIn(("front-door", True, "recovered"), health)
        self.assertGreaterEqual(repository.get_cursor_calls, 4)
        log_output = "\n".join(captured.output)
        self.assertIn("RuntimeError", log_output)
        self.assertNotIn("TOP-SECRET", log_output)

    def test_health_callback_failure_is_sanitized_and_does_not_stop_poller(self):
        repository = TransientCursorRepository(failures=3)
        callback_called = threading.Event()

        def fail_health_callback(device_id, healthy, reason):
            callback_called.set()
            raise RuntimeError("HEALTH-CALLBACK-SECRET")

        poller = RecognitionRecordPoller(
            client=FakeClient(),
            repository=repository,
            intake=self.ingestor,
            on_health_change=fail_health_callback,
            poll_interval_seconds=0.01,
            clock=lambda: NOW,
        )

        with self.assertLogs("agent.device_identity", level="ERROR") as captured:
            poller.start()
            try:
                self.assertTrue(callback_called.wait(timeout=1))
                self.assertTrue(repository.success_seen.wait(timeout=1))
                self.assertTrue(poller._thread.is_alive())
            finally:
                poller.stop(timeout=1)

        self.assertEqual(0, poller._consecutive_failures)
        log_output = "\n".join(captured.output)
        self.assertIn("RuntimeError", log_output)
        self.assertNotIn("HEALTH-CALLBACK-SECRET", log_output)

    def test_stop_surfaces_join_timeout_and_rejects_restart_until_exit(self):
        entered = threading.Event()
        release = threading.Event()
        restarted = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        class BlockingPoller(RecognitionRecordPoller):
            def poll_once(self):
                nonlocal call_count
                with call_lock:
                    call_count += 1
                    current_call = call_count
                if current_call == 1:
                    entered.set()
                    release.wait(timeout=5)
                else:
                    restarted.set()
                return []

        poller = BlockingPoller(
            client=FakeClient(),
            repository=InMemoryDeviceIdentityRepository(),
            intake=self.ingestor,
            poll_interval_seconds=0.01,
            clock=lambda: NOW,
        )
        old_thread = None

        try:
            poller.start()
            self.assertTrue(entered.wait(timeout=1))
            old_thread = poller._thread

            with self.assertRaisesRegex(RuntimeError, "still running"):
                poller.stop(timeout=0.01)

            self.assertTrue(old_thread.is_alive())
            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                poller.start()

            release.set()
            old_thread.join(timeout=1)
            self.assertFalse(old_thread.is_alive())

            poller.start()
            self.assertIsNot(old_thread, poller._thread)
            self.assertTrue(restarted.wait(timeout=1))
            poller.stop(timeout=1)
            self.assertFalse(poller._thread.is_alive())
        finally:
            release.set()
            poller._stop.set()
            if old_thread is not None:
                old_thread.join(timeout=1)
            if poller._thread is not None and poller._thread is not old_thread:
                poller._thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
