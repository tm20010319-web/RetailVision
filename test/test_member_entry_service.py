from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime, timedelta
from pathlib import Path
import threading
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agent import db as db_module
from agent.db import configure_sqlite_foreign_keys
from agent.schema_evolution import evolve_schema
from agent.legacy.member_entry_service import MemberEntryService
from agent.member.member_service import MemberService
from agent.models import (
    BusinessJob,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
    Visit,
)


class SnapshotResolver:
    def __init__(self, crop_path=None):
        self.crop_path = crop_path

    def get(self, camera_id, track_id):
        return self.crop_path


class MemberEntryServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        for model in (Member, DeviceRecognitionInbox, MemberEntryEvent, Visit, BusinessJob):
            model.__table__.create(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.now = datetime(2026, 7, 14, 12, 0, 10)
        session = self.session_factory()
        try:
            session.add(Member(id="member-1", name="Member", visit_count=0))
            session.add(MemberEntryEvent(
                event_id="event-1",
                device_id="camera-1",
                member_id="member-1",
                occurred_at=self.now - timedelta(seconds=10),
                similarity=0.91,
                identity_time=self.now - timedelta(seconds=9),
            ))
            session.commit()
        finally:
            session.close()

    def _snapshot(self, name):
        if "track" in name:
            return str(Path(__file__).with_name("test_entry_event_coordinator.py"))
        return str(Path(__file__))

    def test_prepare_recognition_creates_member_arrival_candidate(self):
        occurred_at = self.now + timedelta(seconds=20)
        db = self.session_factory()
        db.add(DeviceRecognitionInbox(
            canonical_event_id="event-2",
            device_id="camera-1",
            source="push",
            source_event_id="source-2",
            occurred_at=occurred_at,
            member_id="member-1",
            status="processing",
        ))
        db.commit()
        db.close()

        result = MemberEntryService(
            self.session_factory,
            clock=lambda: self.now,
        ).prepare_recognition({
            "device_event_id": "event-2",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "occurred_at": occurred_at,
            "confidence": 0.92,
            "identity_time": occurred_at,
        })

        self.assertEqual("prepared", result)
        db = self.session_factory()
        self.addCleanup(db.close)
        event = db.get(MemberEntryEvent, "event-2")
        self.assertEqual("recognized", event.status)
        self.assertEqual(0.92, event.similarity)

    def test_prepare_recognition_applies_member_debounce(self):
        occurred_at = self.now - timedelta(seconds=5)
        db = self.session_factory()
        db.add(DeviceRecognitionInbox(
            canonical_event_id="event-debounce",
            device_id="camera-1",
            source="poll",
            source_event_id="source-debounce",
            occurred_at=occurred_at,
            member_id="member-1",
            status="processing",
        ))
        db.commit()
        db.close()

        result = MemberEntryService(self.session_factory).prepare_recognition({
            "device_event_id": "event-debounce",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "occurred_at": occurred_at,
        })

        self.assertEqual("ignored", result)
        db = self.session_factory()
        self.addCleanup(db.close)
        inbox = db.get(DeviceRecognitionInbox, "event-debounce")
        self.assertEqual("processing", inbox.status)
        self.assertIsNone(inbox.last_error)
        self.assertIsNone(db.get(MemberEntryEvent, "event-debounce"))

    def test_reconcile_captures_updates_skipped_profile_job(self):
        capture_path = self._snapshot("device-capture.jpg")
        db = self.session_factory()
        db.add(DeviceRecognitionInbox(
            canonical_event_id="event-late-capture",
            device_id="camera-1",
            source="poll",
            source_event_id="source-late",
            occurred_at=self.now,
            member_id="member-1",
            capture_path=capture_path,
            status="processed",
        ))
        db.add(MemberEntryEvent(
            event_id="event-late-capture",
            device_id="camera-1",
            member_id="member-1",
            occurred_at=self.now,
            similarity=0.91,
            identity_time=self.now,
            status="finalized",
        ))
        db.add(BusinessJob(
            event_id="event-late-capture",
            member_id="member-1",
            task_type="customer_profile",
            status="skipped",
            payload={},
            result={"reason": "skipped_no_snapshot"},
        ))
        db.commit()
        db.close()

        adopted = MemberEntryService(
            self.session_factory,
            clock=lambda: self.now,
        ).reconcile_captures()

        self.assertEqual(1, adopted)
        db = self.session_factory()
        self.addCleanup(db.close)
        event = db.get(MemberEntryEvent, "event-late-capture")
        job = db.query(BusinessJob).filter_by(event_id="event-late-capture").one()
        self.assertEqual(capture_path, event.snapshot_path)
        self.assertEqual("pending", job.status)
        self.assertEqual(capture_path, job.payload["crop_path"])

    def test_finalize_replay_creates_one_visit_job_and_member_count(self):
        crop_path = self._snapshot("track-crop.jpg")
        service = MemberEntryService(
            self.session_factory,
            snapshot_resolver=SnapshotResolver(crop_path),
            clock=lambda: self.now,
        )
        session = {
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": 7,
            "detection_bbox": [1, 2, 30, 40],
            "zone_name": "entrance",
            "confidence": 0.91,
            "entry_time": self.now - timedelta(seconds=10),
            "identity_time": self.now - timedelta(seconds=9),
        }

        first = service.finalize(session)
        second = service.finalize(session)

        self.assertEqual(first, second)
        db = self.session_factory()
        self.addCleanup(db.close)
        self.assertEqual(1, db.query(Visit).count())
        self.assertEqual(1, db.query(BusinessJob).count())
        self.assertEqual(1, db.get(Member, "member-1").visit_count)
        visit = db.get(Visit, first.visit_id)
        self.assertEqual("event-1", visit.device_event_id)
        self.assertEqual(7, visit.track_id)
        job = db.query(BusinessJob).one()
        self.assertEqual("pending", first.profile_job_status)
        self.assertEqual("pending", job.status)
        self.assertEqual({
            "crop_path": crop_path,
            "track_id": 7,
            "camera_id": "camera-1",
            "zone_name": "entrance",
            "member_id": "member-1",
            "event_id": "event-1",
        }, job.payload)

    def test_finalize_replay_persists_configured_profile_job_max_attempts(self):
        crop_path = self._snapshot("track-crop.jpg")
        service = MemberEntryService(
            self.session_factory,
            snapshot_resolver=SnapshotResolver(crop_path),
            profile_job_max_attempts=7,
            clock=lambda: self.now,
        )
        session = {
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": 7,
            "zone_name": "entrance",
        }

        first = service.finalize(session)
        second = service.finalize(session)

        self.assertEqual(first, second)
        db = self.session_factory()
        self.addCleanup(db.close)
        self.assertEqual(1, db.query(BusinessJob).count())
        self.assertEqual(7, db.query(BusinessJob).one().max_attempts)

    def test_no_track_member_uses_device_capture_for_pending_profile_job(self):
        capture_path = self._snapshot("device-capture.jpg")

        result = MemberEntryService(self.session_factory, clock=lambda: self.now).finalize({
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": None,
            "snapshot_path": capture_path,
            "identity_source": "device_api",
        })

        db = self.session_factory()
        self.addCleanup(db.close)
        job = db.query(BusinessJob).one()
        self.assertEqual("pending", result.profile_job_status)
        self.assertEqual(capture_path, job.payload["crop_path"])
        self.assertEqual(capture_path, db.get(MemberEntryEvent, "event-1").snapshot_path)

    def test_no_track_device_identity_without_image_skips_no_snapshot(self):
        result = MemberEntryService(self.session_factory, clock=lambda: self.now).finalize({
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": None,
            "snapshot_path": None,
            "identity_source": "device_api",
        })

        db = self.session_factory()
        self.addCleanup(db.close)
        job = db.query(BusinessJob).one()
        self.assertEqual("skipped", result.profile_job_status)
        self.assertEqual({"reason": "skipped_no_snapshot"}, job.result)

    def test_track_crop_remains_fallback_when_device_capture_is_absent(self):
        crop_path = self._snapshot("track-crop.jpg")
        MemberEntryService(
            self.session_factory,
            snapshot_resolver=SnapshotResolver(crop_path),
            clock=lambda: self.now,
        ).finalize({
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": 8,
            "snapshot_path": None,
            "identity_source": "device_api",
        })

        db = self.session_factory()
        self.addCleanup(db.close)
        self.assertEqual(crop_path, db.query(BusinessJob).one().payload["crop_path"])

    def test_device_capture_precedes_track_crop(self):
        capture_path = self._snapshot("device-capture.jpg")
        crop_path = self._snapshot("track-crop.jpg")
        MemberEntryService(
            self.session_factory,
            snapshot_resolver=SnapshotResolver(crop_path),
            clock=lambda: self.now,
        ).finalize({
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": 8,
            "snapshot_path": capture_path,
            "identity_source": "device_api",
        })

        db = self.session_factory()
        self.addCleanup(db.close)
        self.assertEqual(capture_path, db.query(BusinessJob).one().payload["crop_path"])

    def test_device_capture_must_be_a_regular_file(self):
        result = MemberEntryService(self.session_factory, clock=lambda: self.now).finalize({
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": None,
            "snapshot_path": str(Path(__file__).parent),
            "identity_source": "device_api",
        })

        db = self.session_factory()
        self.addCleanup(db.close)
        self.assertEqual("skipped", result.profile_job_status)
        self.assertEqual({"reason": "skipped_no_snapshot"}, db.query(BusinessJob).one().result)

    def test_recovery_uses_persisted_device_capture(self):
        capture_path = self._snapshot("recovered-device-capture.jpg")
        db = self.session_factory()
        try:
            db.get(MemberEntryEvent, "event-1").snapshot_path = capture_path
            db.commit()
        finally:
            db.close()

        self.assertEqual(
            1,
            MemberEntryService(self.session_factory, clock=lambda: self.now).recover_due(),
        )

        db = self.session_factory()
        self.addCleanup(db.close)
        self.assertEqual("pending", db.query(BusinessJob).one().status)
        self.assertEqual(capture_path, db.query(BusinessJob).one().payload["crop_path"])

    def test_profile_job_max_attempts_requires_positive_integer(self):
        for invalid_value in (0, -1, 1.5, True, "7", None):
            with self.subTest(value=invalid_value), self.assertRaisesRegex(
                ValueError, "profile_job_max_attempts must be a positive integer"
            ):
                MemberEntryService(
                    self.session_factory,
                    profile_job_max_attempts=invalid_value,
                )

    def test_recover_due_finalizes_device_event_without_image_and_skips_profile_job(self):
        service = MemberEntryService(
            self.session_factory,
            snapshot_resolver=SnapshotResolver("unused.jpg"),
            profile_job_max_attempts=7,
            clock=lambda: self.now,
        )

        recovered = service.recover_due()

        self.assertEqual(1, recovered)
        db = self.session_factory()
        self.addCleanup(db.close)
        event = db.get(MemberEntryEvent, "event-1")
        self.assertEqual("finalized", event.status)
        self.assertTrue(event.visit_id)
        self.assertEqual(1, db.query(Visit).count())
        job = db.query(BusinessJob).one()
        self.assertEqual("skipped", job.status)
        self.assertEqual({"reason": "skipped_no_snapshot"}, job.result)
        self.assertEqual(7, job.max_attempts)

    def test_finalize_without_profile_leaves_no_business_job(self):
        service = MemberEntryService(
            self.session_factory,
            profile_enabled=False,
            clock=lambda: self.now,
        )

        result = service.finalize({
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": 7,
            "zone_name": "entrance",
        })

        self.assertIsNone(result.profile_job_status)
        db = self.session_factory()
        self.addCleanup(db.close)
        self.assertEqual(0, db.query(BusinessJob).count())

    def test_record_visit_reuses_device_event_and_keeps_later_last_visit(self):
        db = self.session_factory()
        try:
            member = db.get(Member, "member-1")
            member.last_visit = self.now
            db.commit()

            service = MemberService(db)
            first_visit_id = service.record_visit(
                "member-1", "entrance", 0.91,
                device_event_id="device-event-1",
                entry_time=self.now - timedelta(seconds=5),
                camera_id="camera-1",
                track_id=7,
                detection_bbox=[1, 2, 30, 40],
                identity_time=self.now - timedelta(seconds=4),
            )
            duplicate_visit_id = service.record_visit(
                "member-1", "entrance", 0.91,
                device_event_id="device-event-1",
                entry_time=self.now,
            )

            self.assertEqual(first_visit_id, duplicate_visit_id)
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(1, db.get(Member, "member-1").visit_count)
            self.assertEqual(self.now, db.get(Member, "member-1").last_visit)
        finally:
            db.close()

    def test_recover_due_records_retry_with_bounded_backoff_after_failure(self):
        service = MemberEntryService(self.session_factory, clock=lambda: self.now)

        with patch.object(service, "finalize", side_effect=RuntimeError("database unavailable")):
            recovered = service.recover_due()

        self.assertEqual(0, recovered)
        db = self.session_factory()
        self.addCleanup(db.close)
        event = db.get(MemberEntryEvent, "event-1")
        self.assertEqual(1, event.finalize_attempts)
        self.assertEqual(
            self.now + timedelta(seconds=1),
            event.next_attempt_at,
        )
        self.assertEqual("RuntimeError: database unavailable", event.last_error)

    def test_recover_due_missing_member_rolls_back_and_records_retry(self):
        db = self.session_factory()
        try:
            db.delete(db.get(Member, "member-1"))
            db.commit()
        finally:
            db.close()

        recovered = MemberEntryService(
            self.session_factory,
            clock=lambda: self.now,
        ).recover_due()

        self.assertEqual(0, recovered)
        db = self.session_factory()
        self.addCleanup(db.close)
        event_row = db.get(MemberEntryEvent, "event-1")
        self.assertEqual("recognized", event_row.status)
        self.assertIsNone(event_row.visit_id)
        self.assertEqual(1, event_row.finalize_attempts)
        self.assertEqual(
            "ValueError: member not found: member-1",
            event_row.last_error,
        )
        self.assertEqual(self.now + timedelta(seconds=1), event_row.next_attempt_at)
        self.assertEqual(0, db.query(Visit).count())
        self.assertEqual(0, db.query(BusinessJob).count())

    def test_recover_due_caps_backoff_and_truncates_error(self):
        db = self.session_factory()
        try:
            db.get(MemberEntryEvent, "event-1").finalize_attempts = 9
            db.commit()
        finally:
            db.close()
        service = MemberEntryService(self.session_factory, clock=lambda: self.now)

        with patch.object(service, "finalize", side_effect=RuntimeError("x" * 600)):
            recovered = service.recover_due()

        self.assertEqual(0, recovered)
        db = self.session_factory()
        self.addCleanup(db.close)
        event_row = db.get(MemberEntryEvent, "event-1")
        self.assertEqual(10, event_row.finalize_attempts)
        self.assertEqual(self.now + timedelta(seconds=300), event_row.next_attempt_at)
        self.assertEqual(512, len(event_row.last_error))

    def test_crash_replay_finishes_event_and_job_without_second_member_count(self):
        db = self.session_factory()
        try:
            db.add(Visit(
                member_id="member-1",
                device_event_id="event-1",
                entry_time=self.now - timedelta(seconds=10),
                zones_visited=["entrance"],
                is_member=True,
            ))
            db.get(Member, "member-1").visit_count = 1
            db.commit()
        finally:
            db.close()

        result = MemberEntryService(
            self.session_factory,
            profile_enabled=True,
            clock=lambda: self.now,
        ).finalize({
            "device_event_id": "event-1",
            "member_id": "member-1",
            "camera_id": "camera-1",
            "track_id": None,
            "zone_name": "entrance",
        })

        db = self.session_factory()
        self.addCleanup(db.close)
        event_row = db.get(MemberEntryEvent, "event-1")
        self.assertEqual("finalized", event_row.status)
        self.assertEqual(result.visit_id, event_row.visit_id)
        self.assertEqual(1, db.query(Visit).count())
        self.assertEqual(1, db.get(Member, "member-1").visit_count)
        job = db.query(BusinessJob).one()
        self.assertEqual("skipped", job.status)
        self.assertEqual({"reason": "skipped_no_track"}, job.result)

    def test_concurrent_profile_disabled_finalizations_share_one_visit(self):
        database_path = Path.cwd() / "member_entry_service_concurrent.sqlite3"
        database_path.unlink(missing_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        try:
            evolve_schema(engine)
            session_factory = sessionmaker(bind=engine)
            db = session_factory()
            try:
                db.add(Member(id="member-1", name="Member", visit_count=0))
                db.add(MemberEntryEvent(
                    event_id="event-1",
                    device_id="camera-1",
                    member_id="member-1",
                    occurred_at=self.now - timedelta(seconds=10),
                    similarity=0.91,
                    identity_time=self.now - timedelta(seconds=9),
                ))
                db.commit()
            finally:
                db.close()

            insert_barrier = threading.Barrier(2)
            results = []
            errors = []
            results_lock = threading.Lock()

            def synchronize_visit_insert(mapper, connection, target):
                if target.device_event_id == "event-1":
                    insert_barrier.wait(timeout=10)

            event.listen(Visit, "before_insert", synchronize_visit_insert)
            try:
                def finalize_in_thread():
                    try:
                        result = MemberEntryService(
                            session_factory,
                            profile_enabled=False,
                            clock=lambda: self.now,
                        ).finalize({
                            "device_event_id": "event-1",
                            "member_id": "member-1",
                            "camera_id": "camera-1",
                            "track_id": None,
                            "zone_name": "entrance",
                        })
                        with results_lock:
                            results.append(result)
                    except Exception as error:
                        with results_lock:
                            errors.append(error)

                threads = [threading.Thread(target=finalize_in_thread) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=15)
            finally:
                event.remove(Visit, "before_insert", synchronize_visit_insert)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual([], errors)
            self.assertEqual(2, len(results))
            self.assertEqual(1, len({result.visit_id for result in results}))
            db = session_factory()
            try:
                self.assertEqual(1, db.query(Visit).count())
                self.assertEqual(1, db.get(Member, "member-1").visit_count)
            finally:
                db.close()
        finally:
            engine.dispose()
            database_path.unlink(missing_ok=True)

    def test_concurrent_member_delete_cannot_leave_finalized_orphans(self):
        database_path = Path.cwd() / f"member_entry_delete_race_{uuid4().hex}.sqlite3"
        engine = configure_sqlite_foreign_keys(create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        ))
        try:
            evolve_schema(engine)
            session_factory = sessionmaker(bind=engine)
            db = session_factory()
            try:
                db.add(Member(id="member-race", name="Member", visit_count=0))
                db.add(MemberEntryEvent(
                    event_id="event-race",
                    device_id="camera-1",
                    member_id="member-race",
                    occurred_at=self.now - timedelta(seconds=10),
                    similarity=0.91,
                    identity_time=self.now - timedelta(seconds=9),
                ))
                db.commit()
            finally:
                db.close()

            finalizer_reached_update = threading.Event()
            delete_started = threading.Event()
            thread_errors = []
            delete_was_rejected = []
            results_lock = threading.Lock()

            def coordinate_race(
                connection, cursor, statement, parameters, context, executemany
            ):
                normalized = " ".join(statement.upper().split())
                if normalized.startswith("UPDATE MEMBER_ENTRY_EVENTS"):
                    finalizer_reached_update.set()
                    if not delete_started.wait(timeout=10):
                        raise RuntimeError("member delete did not reach the database")
                elif normalized.startswith("DELETE FROM MEMBERS"):
                    delete_started.set()

            event.listen(engine, "before_cursor_execute", coordinate_race)
            try:
                def finalize_in_thread():
                    try:
                        MemberEntryService(
                            session_factory,
                            snapshot_resolver=SnapshotResolver("crop.jpg"),
                            clock=lambda: self.now,
                        ).finalize({
                            "device_event_id": "event-race",
                            "member_id": "member-race",
                            "camera_id": "camera-1",
                            "track_id": 7,
                            "zone_name": "entrance",
                        })
                    except Exception as error:
                        with results_lock:
                            thread_errors.append(error)

                def delete_in_thread():
                    if not finalizer_reached_update.wait(timeout=10):
                        with results_lock:
                            thread_errors.append(RuntimeError(
                                "finalizer did not acquire the write lock"
                            ))
                        return
                    delete_db = session_factory()
                    try:
                        delete_db.delete(delete_db.get(Member, "member-race"))
                        try:
                            delete_db.commit()
                        except IntegrityError:
                            delete_db.rollback()
                            with results_lock:
                                delete_was_rejected.append(True)
                    except Exception as error:
                        delete_db.rollback()
                        with results_lock:
                            thread_errors.append(error)
                    finally:
                        delete_db.close()

                finalize_thread = threading.Thread(target=finalize_in_thread)
                delete_thread = threading.Thread(target=delete_in_thread)
                finalize_thread.start()
                delete_thread.start()
                finalize_thread.join(timeout=15)
                delete_thread.join(timeout=15)
            finally:
                event.remove(engine, "before_cursor_execute", coordinate_race)

            self.assertFalse(finalize_thread.is_alive())
            self.assertFalse(delete_thread.is_alive())
            self.assertEqual([], thread_errors)
            self.assertEqual([True], delete_was_rejected)

            db = session_factory()
            try:
                member = db.get(Member, "member-race")
                event_row = db.get(MemberEntryEvent, "event-race")
                visits = db.query(Visit).filter_by(member_id="member-race").all()
                jobs = db.query(BusinessJob).filter_by(member_id="member-race").all()

                self.assertIsNotNone(member)
                self.assertEqual("finalized", event_row.status)
                self.assertEqual(1, len(visits))
                self.assertEqual(1, len(jobs))
                self.assertEqual(visits[0].id, event_row.visit_id)
            finally:
                db.close()
        finally:
            engine.dispose()
            database_path.unlink(missing_ok=True)


class DatabaseEngineConfigurationTests(unittest.TestCase):
    def test_production_sqlite_engine_enables_foreign_keys_before_first_connection(self):
        database_path = Path.cwd() / f"production_fk_{uuid4().hex}.sqlite3"
        database_url = f"sqlite:///{database_path}"
        with patch.object(db_module, "_engine", None), patch.object(
            db_module.settings, "DATABASE_URL", database_url
        ):
            engine = db_module._get_engine()
            try:
                with engine.connect() as connection:
                    self.assertEqual(
                        1,
                        connection.exec_driver_sql(
                            "PRAGMA foreign_keys"
                        ).scalar_one(),
                    )
            finally:
                engine.dispose()
                database_path.unlink(missing_ok=True)

    def test_configured_sqlite_engine_enables_foreign_keys_on_every_connection(self):
        database_path = Path.cwd() / f"foreign_keys_{uuid4().hex}.sqlite3"
        engine = create_engine(f"sqlite:///{database_path}")
        try:
            with patch("agent.db.event.listen", wraps=event.listen) as listen:
                self.assertIs(engine, configure_sqlite_foreign_keys(engine))
                self.assertIs(engine, configure_sqlite_foreign_keys(engine))
            self.assertEqual(1, listen.call_count)

            with engine.connect() as first_connection:
                with engine.connect() as second_connection:
                    self.assertEqual(
                        1,
                        first_connection.exec_driver_sql(
                            "PRAGMA foreign_keys"
                        ).scalar_one(),
                    )
                    self.assertEqual(
                        1,
                        second_connection.exec_driver_sql(
                            "PRAGMA foreign_keys"
                        ).scalar_one(),
                    )
        finally:
            engine.dispose()
            database_path.unlink(missing_ok=True)

    def test_configurator_does_not_install_listener_on_non_sqlite_engine(self):
        engine = Mock()
        engine.dialect.name = "postgresql"

        with patch("agent.db.event.listen") as listen:
            self.assertIs(engine, configure_sqlite_foreign_keys(engine))

        listen.assert_not_called()

    def test_configured_sqlite_engine_rejects_orphan_writes_and_member_delete(self):
        database_path = Path.cwd() / f"integrity_{uuid4().hex}.sqlite3"
        engine = configure_sqlite_foreign_keys(
            create_engine(f"sqlite:///{database_path}")
        )
        try:
            evolve_schema(engine)
            session_factory = sessionmaker(bind=engine)
            db = session_factory()
            try:
                for orphan in (
                    Visit(member_id="missing-member", is_member=True),
                    BusinessJob(
                        event_id="event-orphan",
                        member_id="missing-member",
                        task_type="customer_profile",
                        payload={},
                    ),
                ):
                    with self.subTest(model=type(orphan).__name__):
                        db.add(orphan)
                        with self.assertRaises(IntegrityError):
                            db.commit()
                        db.rollback()

                db.add(Member(id="member-1", name="Member"))
                db.commit()
                db.add_all([
                    Visit(member_id="member-1", is_member=True),
                    BusinessJob(
                        event_id="event-1",
                        member_id="member-1",
                        task_type="customer_profile",
                        payload={},
                    ),
                ])
                db.commit()

                db.delete(db.get(Member, "member-1"))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()

                self.assertIsNotNone(db.get(Member, "member-1"))
                self.assertEqual(1, db.query(Visit).count())
                self.assertEqual(1, db.query(BusinessJob).count())
            finally:
                db.close()
        finally:
            engine.dispose()
            database_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
