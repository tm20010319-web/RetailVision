from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import threading
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.schema_evolution import evolve_schema
from agent.device_recognition_intake import DeviceRecognitionIntake
from agent.device_health import DeviceHealthStore
from agent.member_entry_service import MemberEntryService
from agent.models import (
    DeviceMemberMapping,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
    MemberArrival,
    Visit,
    BusinessJob,
)


try:
    DeviceRecognitionInboxWorker = importlib.import_module(
        "agent.device_event_worker"
    ).DeviceRecognitionInboxWorker
except (AttributeError, ModuleNotFoundError):
    DeviceRecognitionInboxWorker = None


NOW = datetime(2026, 7, 20, 12, 0, 0)
TEST_TEMP_ROOT = Path(__file__).resolve().parents[1] / ".task5-tests"
TEST_TEMP_ROOT.mkdir(exist_ok=True)


class RecordingCoordinator:
    def __init__(self, failures=0):
        self.failures = failures
        self.events = []
        self._lock = threading.Lock()

    def submit_recognition(self, event):
        with self._lock:
            if self.failures:
                self.failures -= 1
                raise RuntimeError("coordinator unavailable: " + "x" * 700)
            self.events.append(event)


class DeviceRecognitionInboxWorkerTests(unittest.TestCase):
    def setUp(self):
        self.database_path = TEST_TEMP_ROOT / f"worker-{uuid.uuid4().hex}.sqlite3"
        self.engine = create_engine(
            f"sqlite:///{self.database_path}",
            connect_args={"check_same_thread": False},
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as session:
            session.add(Member(id="member-1", name="Member One"))
            session.commit()
        self.released = []

        class CaptureStore:
            def remove_path(inner_self, path):
                self.released.append(path)

        self.intake = DeviceRecognitionIntake(
            self.sessions,
            capture_store=CaptureStore(),
        )

    def tearDown(self):
        self.engine.dispose()
        self.database_path.unlink(missing_ok=True)

    def insert_inbox(
        self,
        event_id,
        *,
        status="received",
        locked_at=None,
        claim_token=None,
    ):
        with self.sessions() as session:
            session.add(
                DeviceRecognitionInbox(
                    canonical_event_id=event_id,
                    device_id="entrance-1",
                    source="push",
                    source_event_id=f"source-{event_id}",
                    occurred_at=NOW,
                    member_id="member-1",
                    group_id=3,
                    person_id=7,
                    unique_id="member-one",
                    similarity=0.94,
                    rule_id=1,
                    capture_path=f"captures/{event_id}.jpg",
                    status=status,
                    locked_at=locked_at,
                    claim_token=claim_token,
                    received_at=NOW,
                )
            )
            session.commit()

    def get_inbox(self, event_id):
        with self.sessions() as session:
            row = session.get(DeviceRecognitionInbox, event_id)
            return {
                "status": row.status,
                "locked_at": row.locked_at,
                "processed_at": row.processed_at,
                "last_error": row.last_error,
                "claim_token": row.claim_token,
            }

    def make_worker(self, coordinator=None, **kwargs):
        self.assertIsNotNone(
            DeviceRecognitionInboxWorker,
            "DeviceRecognitionInboxWorker has not been implemented",
        )
        clock = kwargs.pop("clock", lambda: NOW)
        return DeviceRecognitionInboxWorker(
            session_factory=self.sessions,
            coordinator=coordinator or RecordingCoordinator(),
            intake=self.intake,
            member_arrival=MemberEntryService(self.sessions, clock=lambda: NOW),
            reconcile_waiting=self.intake._reconcile_waiting,
            clock=clock,
            **kwargs,
        )

    def test_worker_delivers_persisted_event_once(self):
        self.insert_inbox("evt-1")
        coordinator = RecordingCoordinator()
        worker = self.make_worker(coordinator)

        self.assertEqual(1, worker.run_once())
        self.assertEqual(0, worker.run_once())

        self.assertEqual(["evt-1"], [event.event_id for event in coordinator.events])
        event = coordinator.events[0]
        self.assertEqual("push", event.source)
        self.assertEqual("source-evt-1", event.source_event_id)
        self.assertEqual("member-1", event.member_id)
        self.assertEqual("captures/evt-1.jpg", event.capture_path)
        row = self.get_inbox("evt-1")
        self.assertEqual("processed", row["status"])
        self.assertEqual(NOW, row["processed_at"])
        self.assertIsNone(row["locked_at"])

    def test_new_mode_only_records_compatibility_event(self):
        self.insert_inbox("evt-new-mode")
        worker = DeviceRecognitionInboxWorker(
            session_factory=self.sessions,
            coordinator=None,
            intake=self.intake,
            member_arrival=MemberEntryService(self.sessions, clock=lambda: NOW),
            reconcile_waiting=self.intake._reconcile_waiting,
            clock=lambda: NOW,
            identity_association_mode="new",
            new_mode_store_id="store-1",
        )

        self.assertEqual(1, worker.run_once())

        with self.sessions() as session:
            self.assertEqual(1, session.query(MemberEntryEvent).count())
            self.assertEqual(0, session.query(Visit).count())
            self.assertEqual(0, session.query(MemberArrival).count())
            self.assertEqual(0, session.query(BusinessJob).count())
            inbox = session.get(DeviceRecognitionInbox, "evt-new-mode")
            self.assertEqual("processed", inbox.status)
            self.assertEqual("accepted", inbox.recognition_status)
            self.assertEqual("resolved", inbox.store_resolution_status)
            self.assertEqual("pending", inbox.association_status)

    def test_worker_completes_debounced_member_arrival_without_leaving_a_lease(self):
        with self.sessions() as session:
            session.add(MemberEntryEvent(
                event_id="evt-existing",
                device_id="entrance-1",
                member_id="member-1",
                occurred_at=NOW - timedelta(seconds=5),
                similarity=0.9,
                identity_time=NOW - timedelta(seconds=5),
                status="recognized",
            ))
            session.commit()
        self.insert_inbox("evt-debounced")
        coordinator = RecordingCoordinator()

        self.assertEqual(1, self.make_worker(coordinator).run_once())

        self.assertEqual([], coordinator.events)
        row = self.get_inbox("evt-debounced")
        self.assertEqual("ignored", row["status"])
        self.assertEqual("member_debounce", row["last_error"])
        self.assertEqual(NOW, row["processed_at"])
        self.assertIsNone(row["locked_at"])
        self.assertIsNone(row["claim_token"])

    def test_worker_recovers_waiting_mapping_before_delivery(self):
        with self.sessions() as session:
            session.add(
                DeviceRecognitionInbox(
                    canonical_event_id="evt-waiting",
                    device_id="entrance-1",
                    source="push",
                    source_event_id="source-waiting",
                    occurred_at=NOW,
                    member_id=None,
                    group_id=3,
                    person_id=7,
                    unique_id="member-one",
                    similarity=0.94,
                    rule_id=1,
                    status="waiting_mapping",
                    received_at=NOW,
                )
            )
            session.add(
                DeviceMemberMapping(
                    id="entrance-1:member-1",
                    device_id="entrance-1",
                    member_id="member-1",
                    group_id=3,
                    group_uuid="group",
                    person_id=7,
                    unique_id="member-one",
                    face_uuid="face",
                    sync_status="synced",
                    is_enabled=True,
                )
            )
            session.commit()

        coordinator = RecordingCoordinator()
        worker = DeviceRecognitionInboxWorker(
            session_factory=self.sessions,
            coordinator=coordinator,
            intake=self.intake,
            member_arrival=MemberEntryService(self.sessions, clock=lambda: NOW),
            reconcile_waiting=self.intake._reconcile_waiting,
            clock=lambda: NOW,
        )

        self.assertEqual(1, worker.run_once())
        self.assertEqual(["member-1"], [event.member_id for event in coordinator.events])
        with self.sessions() as session:
            inbox = session.get(DeviceRecognitionInbox, "evt-waiting")
            self.assertEqual("processed", inbox.status)
            self.assertEqual("member-1", inbox.member_id)
            self.assertEqual(1, session.query(MemberEntryEvent).count())

    def test_worker_expires_unmapped_event_and_releases_capture_after_24_hours(self):
        capture_path = "captures/unmapped.jpg"
        with self.sessions() as session:
            session.add(
                DeviceRecognitionInbox(
                    canonical_event_id="evt-unmapped",
                    device_id="entrance-1",
                    source="push",
                    source_event_id="source-unmapped",
                    occurred_at=NOW - timedelta(hours=24),
                    member_id=None,
                    group_id=99,
                    person_id=99,
                    unique_id="missing",
                    similarity=0.94,
                    rule_id=1,
                    capture_path=capture_path,
                    status="waiting_mapping",
                    received_at=NOW - timedelta(hours=24),
                )
            )
            session.commit()
        worker = DeviceRecognitionInboxWorker(
            session_factory=self.sessions,
            coordinator=RecordingCoordinator(),
            intake=self.intake,
            reconcile_waiting=self.intake._reconcile_waiting,
            mapping_retention_seconds=24 * 60 * 60,
            clock=lambda: NOW,
        )

        self.assertEqual(0, worker.run_once())

        with self.sessions() as session:
            inbox = session.get(DeviceRecognitionInbox, "evt-unmapped")
            self.assertEqual("unmapped", inbox.status)
            self.assertEqual("mapping_timeout", inbox.last_error)
            self.assertEqual(NOW, inbox.processed_at)
            self.assertIsNone(inbox.capture_path)
        self.assertEqual([capture_path], self.released)

    def test_worker_submits_before_marking_row_processed(self):
        self.insert_inbox("evt-ordering")
        observed_states = []

        class StatusCheckingCoordinator:
            def submit_recognition(_self, event):
                observed_states.append(self.get_inbox(event.event_id))

        worker = self.make_worker(StatusCheckingCoordinator())

        self.assertEqual(1, worker.run_once())
        self.assertEqual("processing", observed_states[0]["status"])
        self.assertIsNone(observed_states[0]["processed_at"])
        self.assertEqual("processed", self.get_inbox("evt-ordering")["status"])

    def test_submission_failure_returns_row_to_received_for_retry(self):
        self.insert_inbox("evt-retry")
        coordinator = RecordingCoordinator(failures=1)
        worker = self.make_worker(coordinator)

        self.assertEqual(0, worker.run_once())
        failed = self.get_inbox("evt-retry")
        self.assertEqual("received", failed["status"])
        self.assertIsNone(failed["locked_at"])
        self.assertEqual("submission_failed:RuntimeError", failed["last_error"])
        self.assertNotIn("coordinator unavailable", failed["last_error"])

        self.assertEqual(1, worker.run_once())
        self.assertEqual(
            ["evt-retry"], [event.event_id for event in coordinator.events]
        )

    def test_worker_recovers_stale_processing_row(self):
        self.insert_inbox(
            "evt-stale",
            status="processing",
            locked_at=NOW - timedelta(seconds=31),
            claim_token="stale-owner-token",
        )
        worker = self.make_worker(processing_timeout_seconds=30)

        self.assertEqual(1, worker.recover_stale())

        row = self.get_inbox("evt-stale")
        self.assertEqual("received", row["status"])
        self.assertIsNone(row["locked_at"])
        self.assertIsNone(row["claim_token"])

    def test_worker_only_claims_received_rows(self):
        for status in ("ignored", "waiting_mapping", "failed", "processed"):
            self.insert_inbox(f"evt-{status}", status=status)
        coordinator = RecordingCoordinator()

        self.assertEqual(0, self.make_worker(coordinator).run_once())
        self.assertEqual([], coordinator.events)

    def test_two_sqlite_workers_do_not_claim_the_same_row(self):
        self.insert_inbox("evt-concurrent")
        coordinator = RecordingCoordinator()
        workers = [self.make_worker(coordinator), self.make_worker(coordinator)]
        ready = threading.Barrier(2)

        def run(worker):
            ready.wait(timeout=2)
            return worker.run_once()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, workers))

        self.assertEqual([0, 1], sorted(results))
        self.assertEqual(
            ["evt-concurrent"], [event.event_id for event in coordinator.events]
        )
        self.assertEqual("processed", self.get_inbox("evt-concurrent")["status"])

    def test_expired_old_owner_cannot_complete_or_release_new_lease(self):
        class MutableClock:
            def __init__(self):
                self.now = NOW

            def __call__(self):
                return self.now

        clock = MutableClock()
        self.insert_inbox("evt-lease")
        old_worker = self.make_worker(clock=clock, processing_timeout_seconds=30)
        new_worker = self.make_worker(clock=clock, processing_timeout_seconds=30)

        old_claim = old_worker._claim_batch()[0]
        old_token = self.get_inbox("evt-lease")["claim_token"]
        clock.now = NOW + timedelta(seconds=31)
        self.assertEqual(1, new_worker.recover_stale())
        clock.now = NOW
        new_claim = new_worker._claim_batch()[0]

        self.assertNotEqual(old_token, self.get_inbox("evt-lease")["claim_token"])
        self.assertFalse(old_worker._complete(old_claim))
        old_worker._release(old_claim, RuntimeError("old owner secret"))
        leased = self.get_inbox("evt-lease")
        self.assertEqual("processing", leased["status"])
        self.assertEqual(new_claim.claim_token, leased["claim_token"])
        self.assertTrue(new_worker._complete(new_claim))

    def test_polling_recovers_claims_stranded_by_completion_or_release_failure(self):
        class MutableClock:
            def __init__(self):
                self.now = NOW

            def __call__(self):
                return self.now

            def advance(self, seconds):
                self.now += timedelta(seconds=seconds)

        class TwoIterationStop:
            def __init__(self, clock):
                self.clock = clock
                self.waits = 0

            def is_set(self):
                return self.waits >= 2

            def wait(self, _timeout):
                self.waits += 1
                self.clock.advance(31)
                return self.is_set()

        for failure_stage in ("complete", "release"):
            with self.subTest(failure_stage=failure_stage):
                with self.sessions() as session:
                    session.query(MemberEntryEvent).delete()
                    session.commit()
                event_id = f"evt-stranded-{failure_stage}"
                self.insert_inbox(event_id)
                clock = MutableClock()
                coordinator = RecordingCoordinator(
                    failures=1 if failure_stage == "release" else 0
                )
                worker = self.make_worker(
                    coordinator,
                    clock=clock,
                    processing_timeout_seconds=30,
                )
                worker._stop = TwoIterationStop(clock)
                if failure_stage == "complete":
                    real_complete = worker._complete
                    attempts = 0

                    def fail_complete_once(claim):
                        nonlocal attempts
                        attempts += 1
                        if attempts == 1:
                            raise RuntimeError("complete database unavailable")
                        return real_complete(claim)

                    worker._complete = fail_complete_once
                else:
                    real_release = worker._release
                    attempts = 0

                    def fail_release_once(claim, error):
                        nonlocal attempts
                        attempts += 1
                        if attempts == 1:
                            raise RuntimeError("release database unavailable")
                        return real_release(claim, error)

                    worker._release = fail_release_once

                worker.run_forever()

                self.assertEqual("processed", self.get_inbox(event_id)["status"])

    def test_thread_start_failure_clears_state_and_allows_restart(self):
        created_threads = []

        class StartThread:
            def __init__(self, *, target, name, daemon):
                self.alive = False
                self.fail = not created_threads
                created_threads.append(self)

            def start(self):
                if self.fail:
                    raise RuntimeError("thread start failed")
                self.alive = True

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                self.alive = False

        worker = self.make_worker()
        with patch("agent.device_event_worker.threading.Thread", StartThread):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                worker.start()
            self.assertIsNone(worker._thread)
            worker.stop()
            worker.start()
            worker.stop()

        self.assertEqual(2, len(created_threads))

    def test_worker_start_recovers_stale_and_owns_one_daemon_thread(self):
        self.insert_inbox(
            "evt-startup-stale",
            status="processing",
            locked_at=NOW - timedelta(seconds=31),
        )
        created_threads = []

        class RecordingThread:
            def __init__(self, *, target, name, daemon):
                self.target = target
                self.name = name
                self.daemon = daemon
                self.alive = False
                self.join_calls = []
                created_threads.append(self)

            def start(self):
                self.alive = True

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                self.join_calls.append(timeout)
                self.alive = False

        worker = self.make_worker(processing_timeout_seconds=30)
        with patch("agent.device_event_worker.threading.Thread", RecordingThread):
            worker.start()
            worker.start()
            worker.stop()
            worker.stop()

        self.assertEqual("received", self.get_inbox("evt-startup-stale")["status"])
        self.assertEqual(1, len(created_threads))
        self.assertEqual("device-recognition-inbox", created_threads[0].name)
        self.assertTrue(created_threads[0].daemon)
        self.assertEqual([3.0], created_threads[0].join_calls)


class DeviceHealthStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.store = DeviceHealthStore(self.sessions)

    def tearDown(self):
        self.engine.dispose()

    def test_fresh_push_health_keeps_identity_available_when_poll_fails(self):
        self.assertTrue(hasattr(self.store, "mark_poll_failure"))
        self.assertTrue(hasattr(self.store, "identity_available"))
        self.store.mark_heartbeat("entrance-1", NOW)
        self.store.mark_poll_failure("entrance-1", "request_failed")

        self.assertTrue(self.store.identity_available("entrance-1", NOW))

    def test_fresh_poll_health_keeps_identity_available_after_push_expires(self):
        self.assertTrue(hasattr(self.store, "mark_poll_success"))
        self.store.mark_heartbeat("entrance-1", NOW - timedelta(seconds=91))
        self.store.mark_poll_success("entrance-1", NOW)

        self.assertTrue(self.store.identity_available("entrance-1", NOW))

    def test_default_health_freshness_is_exactly_ninety_seconds(self):
        self.assertTrue(hasattr(self.store, "identity_available"))
        self.store.mark_heartbeat("entrance-1", NOW)

        self.assertTrue(
            self.store.identity_available(
                "entrance-1", NOW + timedelta(seconds=90)
            )
        )
        self.assertFalse(
            self.store.identity_available(
                "entrance-1", NOW + timedelta(seconds=90, microseconds=1)
            )
        )
        self.assertFalse(self.store.identity_available("unknown", NOW))

    def test_health_observations_are_stored_as_utc_naive(self):
        utc8 = timezone(timedelta(hours=8))

        self.store.mark_heartbeat(
            "entrance-1", datetime(2026, 7, 20, 12, 0, tzinfo=utc8)
        )
        self.store.mark_poll_success(
            "entrance-1", datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc)
        )

        state = self.store.get("entrance-1")
        self.assertEqual(datetime(2026, 7, 20, 4, 0), state.last_heartbeat_at)
        self.assertEqual(datetime(2026, 7, 20, 4, 0), state.last_poll_success_at)
        self.assertIsNone(state.last_heartbeat_at.tzinfo)
        self.assertIsNone(state.last_poll_success_at.tzinfo)
        self.assertTrue(
            self.store.identity_available(
                "entrance-1",
                datetime(2026, 7, 20, 4, 1, tzinfo=timezone.utc),
            )
        )

    def test_future_health_observation_is_not_available(self):
        self.store.mark_heartbeat("entrance-1", NOW + timedelta(microseconds=1))

        self.assertFalse(self.store.identity_available("entrance-1", NOW))


if __name__ == "__main__":
    unittest.main()
