"""Tests for stable-track snapshot capture and image-path profile execution."""

from __future__ import annotations

import ast
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from agent.device_identity import StableTrackEvent
from agent.models import BusinessJob, Member, MemberEntryEvent, Visit
from agent.profile_jobs import ProfileJobWorker
from agent.profile_snapshot_store import ProfileSnapshotStore
from pipelines.customer_profile_pipeline import CustomerProfilePipeline
from pipelines.realtime_entry_pipeline import RealtimeEntryPipeline
from agent.detector import Detection


class SpyVisionService:
    """Returns a valid profile while recording actual profile requests."""

    def __init__(self) -> None:
        self.calls = []

    def analyze_image(self, image_base64: str, context: dict) -> dict:
        self.calls.append((image_base64, context))
        return {
            "style_tags": ["casual"],
            "visible_clothing": {"upper": "shirt"},
            "interest_categories": ["apparel"],
            "purchase_intent_score": 0.7,
            "profile_summary": "Customer wears a casual outfit.",
            "sales_suggestion": "Suggest complementary apparel.",
            "confidence": 0.8,
            "privacy_flags": {
                "face_processed": False,
                "sensitive_attributes_inferred": False,
            },
        }


class SinglePersonDetector:
    def __init__(self, detection: Detection) -> None:
        self.detection = detection

    def process_frame(self, frame: np.ndarray):
        return [self.detection]

    def reset(self) -> None:
        pass


class StubProfilePipeline:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def run_from_image_path(
        self, image_path: str, track_id: int, camera_id: str = "", zone_name: str = ""
    ) -> dict:
        self.calls.append((image_path, track_id, camera_id, zone_name))
        if self.error is not None:
            raise self.error
        return self.result


def make_event(bbox=None) -> StableTrackEvent:
    return StableTrackEvent(
        camera_id="camera-1",
        track_id=42,
        bbox=bbox if bbox is not None else [10, 20, 60, 90],
        detection_confidence=0.95,
        confirmed_at=datetime.now(),
    )


class ProfileSnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self._tmpdir.name) / "snapshots"
        self.store = ProfileSnapshotStore(self.output_dir)
        self.frame = np.full((100, 100, 3), 127, dtype=np.uint8)
        self.event = make_event()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_capture_clamps_bbox_stores_crop_and_gets_path(self) -> None:
        path = self.store.capture(self.event, self.frame)

        self.assertIsNotNone(path)
        self.assertEqual(path, self.store.get("camera-1", 42))
        saved_crop = cv2.imread(path)
        self.assertEqual(saved_crop.shape[:2], (70, 50))

    def test_capture_rejects_failed_write_without_storing_path(self) -> None:
        with patch("agent.profile_snapshot_store.cv2.imwrite", return_value=False):
            path = self.store.capture(self.event, self.frame)

        self.assertIsNone(path)
        self.assertIsNone(self.store.get("camera-1", 42))

    def test_successful_replacement_preserves_prior_durable_path(self) -> None:
        first_path = self.store.capture(self.event, self.frame)
        queued_payload = {"crop_path": first_path}
        second_path = self.store.capture(self.event, self.frame)

        self.assertIsNotNone(first_path)
        self.assertIsNotNone(second_path)
        self.assertNotEqual(first_path, second_path)
        self.assertTrue(Path(queued_payload["crop_path"]).is_file())
        self.assertIsNotNone(cv2.imread(queued_payload["crop_path"]))
        self.assertEqual(second_path, self.store.get("camera-1", 42))

    def test_failed_replacement_preserves_prior_snapshot(self) -> None:
        first_path = self.store.capture(self.event, self.frame)

        with patch("agent.profile_snapshot_store.cv2.imwrite", return_value=False):
            replacement_path = self.store.capture(self.event, self.frame)

        self.assertIsNotNone(first_path)
        self.assertIsNone(replacement_path)
        self.assertTrue(Path(first_path).is_file())
        self.assertEqual(first_path, self.store.get("camera-1", 42))

    def test_capture_rejects_non_numeric_bbox_without_writing_or_indexing(self) -> None:
        malformed_event = make_event([10, 20, "bad", 90])

        path = self.store.capture(malformed_event, self.frame)

        self.assertIsNone(path)
        self.assertIsNone(self.store.get("camera-1", 42))
        self.assertEqual([], list(self.output_dir.glob("*.jpg")))

    def test_cleanup_expired_removes_retired_snapshot_after_restart(self) -> None:
        first_path = self.store.capture(self.event, self.frame)
        second_path = self.store.capture(self.event, self.frame)
        outside_path = Path(self._tmpdir.name) / "outside.jpg"
        outside_path.write_bytes(b"outside snapshot directory")

        self.assertIsNotNone(first_path)
        self.assertIsNotNone(second_path)
        unexpired_timestamp = time.time() - 30
        os.utime(first_path, (unexpired_timestamp, unexpired_timestamp))
        self.store.cleanup_expired(max_age_seconds=60)

        self.assertTrue(Path(first_path).is_file())
        self.assertIsNotNone(cv2.imread(first_path))
        self.assertEqual(second_path, self.store.get("camera-1", 42))

        expired_timestamp = time.time() - 120
        os.utime(first_path, (expired_timestamp, expired_timestamp))
        os.utime(outside_path, (expired_timestamp, expired_timestamp))
        restarted_store = ProfileSnapshotStore(self.output_dir)
        restarted_store.cleanup_expired(max_age_seconds=60)

        self.assertFalse(Path(first_path).exists())
        self.assertTrue(Path(second_path).is_file())
        self.assertTrue(outside_path.is_file())

    def test_cleanup_expired_removes_snapshot_and_index_entry(self) -> None:
        path = self.store.capture(self.event, self.frame)
        self.assertIsNotNone(path)
        old_timestamp = datetime.now().timestamp() - 120
        os.utime(path, (old_timestamp, old_timestamp))

        self.store.cleanup_expired(max_age_seconds=60)

        self.assertFalse(Path(path).exists())
        self.assertIsNone(self.store.get("camera-1", 42))


class CustomerProfilePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.image_path = Path(self._tmpdir.name) / "customer.jpg"
        self.assertTrue(
            cv2.imwrite(str(self.image_path), np.full((10, 10, 3), 200, dtype=np.uint8))
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_run_from_image_path_calls_vision_service_once(self) -> None:
        service = SpyVisionService()
        pipeline = CustomerProfilePipeline(service, output_dir=self._tmpdir.name)

        result = pipeline.run_from_image_path(
            str(self.image_path), track_id=7, camera_id="camera-2", zone_name="entry"
        )

        self.assertEqual(len(service.calls), 1)
        self.assertEqual(
            service.calls[0][1],
            {"track_id": 7, "camera_id": "camera-2", "zone_name": "entry"},
        )
        self.assertEqual(result["crop_path"], str(self.image_path))

    def test_run_from_image_path_rejects_missing_crop(self) -> None:
        pipeline = CustomerProfilePipeline(SpyVisionService(), output_dir=self._tmpdir.name)

        with self.assertRaisesRegex(FileNotFoundError, "Customer crop not found"):
            pipeline.run_from_image_path(str(self.image_path.with_name("missing.jpg")), 7)


class ProfileJobWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Member.__table__.create(self.engine)
        Visit.__table__.create(self.engine)
        MemberEntryEvent.__table__.create(self.engine)
        BusinessJob.__table__.create(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.now = datetime(2026, 7, 14, 12, 0, 0)
        db = self.session_factory()
        try:
            db.add(Member(
                id="member-1",
                name="Member",
                profile={"existing": "preserved"},
            ))
            db.add(MemberEntryEvent(
                event_id="event-1",
                device_id="camera-1",
                member_id="member-1",
                occurred_at=self.now - timedelta(minutes=1),
                similarity=0.9,
                identity_time=self.now - timedelta(minutes=1),
            ))
            db.add(BusinessJob(
                id="job-1",
                event_id="event-1",
                member_id="member-1",
                task_type="customer_profile",
                status="pending",
                payload={
                    "crop_path": "crop.jpg",
                    "track_id": 7,
                    "camera_id": "camera-1",
                    "zone_name": "entrance",
                    "member_id": "member-1",
                    "event_id": "event-1",
                },
                available_at=self.now - timedelta(seconds=1),
            ))
            db.commit()
        finally:
            db.close()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_run_once_succeeds_and_updates_member_profile(self) -> None:
        result = {
            "profile": {"style_tags": ["casual"]},
            "track_id": 7,
            "camera_id": "camera-1",
            "zone_name": "entrance",
            "crop_path": "crop.jpg",
        }
        pipeline = StubProfilePipeline(result=result)
        worker = ProfileJobWorker(
            self.session_factory,
            pipeline,
            clock=lambda: self.now,
        )

        self.assertTrue(worker.run_once())

        db = self.session_factory()
        try:
            job = db.get(BusinessJob, "job-1")
            self.assertEqual("succeeded", job.status)
            self.assertEqual(1, job.attempts)
            self.assertEqual(result, job.result)
            self.assertIsNone(job.locked_at)
            self.assertIsNone(job.last_error)
            self.assertEqual(
                {
                    "existing": "preserved",
                    "latest_customer_profile": result["profile"],
                    "latest_customer_profile_event_id": "event-1",
                },
                db.get(Member, "member-1").profile,
            )
        finally:
            db.close()

    def test_older_failed_job_succeeds_without_overwriting_newer_profile(self) -> None:
        newer_result = {
            "profile": {"source": "newer"},
            "track_id": 8,
            "camera_id": "camera-1",
            "zone_name": "entrance",
            "crop_path": "newer.jpg",
        }
        older_result = {
            "profile": {"source": "older-retry"},
            "track_id": 7,
            "camera_id": "camera-1",
            "zone_name": "entrance",
            "crop_path": "crop.jpg",
        }
        db = self.session_factory()
        try:
            older_job = db.get(BusinessJob, "job-1")
            older_job.status = "failed"
            older_job.attempts = 1
            older_job.available_at = self.now + timedelta(seconds=1)
            db.add(MemberEntryEvent(
                event_id="event-2",
                device_id="camera-1",
                member_id="member-1",
                occurred_at=self.now,
                similarity=0.9,
                identity_time=self.now,
            ))
            db.add(BusinessJob(
                id="job-2",
                event_id="event-2",
                member_id="member-1",
                task_type="customer_profile",
                status="pending",
                payload={"crop_path": "newer.jpg", "track_id": 8},
                available_at=self.now - timedelta(seconds=1),
            ))
            db.commit()
        finally:
            db.close()

        current_time = [self.now]
        pipeline = StubProfilePipeline(result=newer_result)
        worker = ProfileJobWorker(
            self.session_factory,
            pipeline,
            clock=lambda: current_time[0],
        )

        self.assertTrue(worker.run_once())
        pipeline.result = older_result
        current_time[0] = self.now + timedelta(seconds=2)
        self.assertTrue(worker.run_once())

        db = self.session_factory()
        try:
            self.assertEqual("succeeded", db.get(BusinessJob, "job-1").status)
            self.assertEqual("succeeded", db.get(BusinessJob, "job-2").status)
            self.assertEqual(older_result, db.get(BusinessJob, "job-1").result)
            self.assertEqual(
                {
                    "existing": "preserved",
                    "latest_customer_profile": newer_result["profile"],
                    "latest_customer_profile_event_id": "event-2",
                },
                db.get(Member, "member-1").profile,
            )
        finally:
            db.close()

    def test_equal_visit_times_use_event_id_as_deterministic_tiebreaker(self) -> None:
        persisted_profile = {
            "existing": "preserved",
            "latest_customer_profile": {"source": "event-b"},
            "latest_customer_profile_event_id": "event-b",
        }
        db = self.session_factory()
        try:
            member = db.get(Member, "member-1")
            member.profile = persisted_profile
            job = db.get(BusinessJob, "job-1")
            job.event_id = "event-a"
            db.add_all([
                Visit(
                    id="visit-a",
                    member_id="member-1",
                    device_event_id="event-a",
                    entry_time=self.now,
                    is_member=True,
                ),
                Visit(
                    id="visit-b",
                    member_id="member-1",
                    device_event_id="event-b",
                    entry_time=self.now,
                    is_member=True,
                ),
            ])
            db.commit()
        finally:
            db.close()

        worker = ProfileJobWorker(
            self.session_factory,
            StubProfilePipeline(result={
                "profile": {"source": "event-a"},
                "track_id": 7,
                "camera_id": "camera-1",
                "zone_name": "entrance",
                "crop_path": "crop.jpg",
            }),
            clock=lambda: self.now,
        )

        self.assertTrue(worker.run_once())

        db = self.session_factory()
        try:
            self.assertEqual("succeeded", db.get(BusinessJob, "job-1").status)
            self.assertEqual(persisted_profile, db.get(Member, "member-1").profile)
        finally:
            db.close()

    def test_replayed_event_succeeds_without_replacing_its_recorded_profile(self) -> None:
        persisted_profile = {
            "existing": "preserved",
            "latest_customer_profile": {"source": "first-completion"},
            "latest_customer_profile_event_id": "event-1",
        }
        db = self.session_factory()
        try:
            db.get(Member, "member-1").profile = persisted_profile
            db.commit()
        finally:
            db.close()

        worker = ProfileJobWorker(
            self.session_factory,
            StubProfilePipeline(result={
                "profile": {"source": "replay"},
                "track_id": 7,
                "camera_id": "camera-1",
                "zone_name": "entrance",
                "crop_path": "crop.jpg",
            }),
            clock=lambda: self.now,
        )

        self.assertTrue(worker.run_once())

        db = self.session_factory()
        try:
            self.assertEqual("succeeded", db.get(BusinessJob, "job-1").status)
            self.assertEqual(persisted_profile, db.get(Member, "member-1").profile)
        finally:
            db.close()

    def test_run_once_records_failure_backoff_and_truncated_error(self) -> None:
        pipeline = StubProfilePipeline(error=RuntimeError("x" * 600))
        worker = ProfileJobWorker(
            self.session_factory,
            pipeline,
            clock=lambda: self.now,
        )

        self.assertTrue(worker.run_once())

        db = self.session_factory()
        try:
            job = db.get(BusinessJob, "job-1")
            self.assertEqual("failed", job.status)
            self.assertEqual(1, job.attempts)
            self.assertIsNone(job.locked_at)
            self.assertEqual(512, len(job.last_error))
            self.assertTrue(job.last_error.startswith("RuntimeError: "))
            self.assertEqual(self.now + timedelta(seconds=1), job.available_at)
            self.assertEqual(
                {"existing": "preserved"},
                db.get(Member, "member-1").profile,
            )
        finally:
            db.close()

    def test_recover_stale_returns_old_running_job_to_pending(self) -> None:
        db = self.session_factory()
        try:
            job = db.get(BusinessJob, "job-1")
            job.status = "running"
            job.attempts = 1
            job.locked_at = self.now - timedelta(seconds=301)
            db.commit()
        finally:
            db.close()
        worker = ProfileJobWorker(
            self.session_factory,
            StubProfilePipeline(),
            clock=lambda: self.now,
            lock_timeout_seconds=300,
        )

        self.assertEqual(1, worker.recover_stale())

        db = self.session_factory()
        try:
            job = db.get(BusinessJob, "job-1")
            self.assertEqual("pending", job.status)
            self.assertIsNone(job.locked_at)
        finally:
            db.close()

    def test_recover_stale_final_attempt_becomes_terminal_failure(self) -> None:
        db = self.session_factory()
        try:
            job = db.get(BusinessJob, "job-1")
            job.status = "running"
            job.attempts = 5
            job.max_attempts = 5
            job.locked_at = self.now - timedelta(seconds=301)
            db.commit()
        finally:
            db.close()
        worker = ProfileJobWorker(
            self.session_factory,
            StubProfilePipeline(),
            clock=lambda: self.now,
            lock_timeout_seconds=300,
        )

        self.assertEqual(1, worker.recover_stale())

        db = self.session_factory()
        try:
            job = db.get(BusinessJob, "job-1")
            self.assertEqual("failed", job.status)
            self.assertEqual(5, job.attempts)
            self.assertIsNone(job.locked_at)
            self.assertIn("stale", job.last_error.lower())
            self.assertLessEqual(len(job.last_error), 512)
        finally:
            db.close()
        self.assertFalse(worker.run_once())

    def test_concurrent_run_once_calls_execute_job_only_once(self) -> None:
        database_path = Path.cwd() / "profile_jobs_concurrent.sqlite3"
        database_path.unlink(missing_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        try:
            Member.__table__.create(engine)
            BusinessJob.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)
            db = session_factory()
            try:
                db.add(Member(id="member-thread", name="Member", profile={}))
                db.add(BusinessJob(
                    id="job-thread",
                    event_id="event-thread",
                    member_id="member-thread",
                    task_type="customer_profile",
                    status="pending",
                    payload={"crop_path": "crop.jpg", "track_id": 8},
                    available_at=self.now - timedelta(seconds=1),
                ))
                db.commit()
            finally:
                db.close()

            select_barrier = threading.Barrier(2)
            select_count = 0
            select_count_lock = threading.Lock()

            def synchronize_candidate_select(
                connection, cursor, statement, parameters, context, executemany
            ) -> None:
                nonlocal select_count
                normalized = statement.lower()
                if "select business_jobs.id" not in normalized or "status in" not in normalized:
                    return
                with select_count_lock:
                    select_count += 1
                    should_wait = select_count <= 2
                if should_wait:
                    select_barrier.wait(timeout=10)

            event.listen(engine, "before_cursor_execute", synchronize_candidate_select)
            pipeline = StubProfilePipeline(result={
                "profile": {"style_tags": ["casual"]},
                "track_id": 8,
                "camera_id": "",
                "zone_name": "",
                "crop_path": "crop.jpg",
            })
            workers = [
                ProfileJobWorker(session_factory, pipeline, clock=lambda: self.now)
                for _ in range(2)
            ]
            outcomes = []
            errors = []
            result_lock = threading.Lock()

            def run_worker(worker: ProfileJobWorker) -> None:
                try:
                    outcome = worker.run_once()
                    with result_lock:
                        outcomes.append(outcome)
                except Exception as error:
                    with result_lock:
                        errors.append(error)

            threads = [
                threading.Thread(target=run_worker, args=(worker,))
                for worker in workers
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            event.remove(engine, "before_cursor_execute", synchronize_candidate_select)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual([], errors)
            self.assertEqual([False, True], sorted(outcomes))
            self.assertEqual(1, len(pipeline.calls))
            db = session_factory()
            try:
                job = db.get(BusinessJob, "job-thread")
                self.assertEqual("succeeded", job.status)
                self.assertEqual(1, job.attempts)
            finally:
                db.close()
        finally:
            engine.dispose()
            database_path.unlink(missing_ok=True)

    def _assert_stale_attempt_cannot_finalize_active_lease(
        self, *, stale_error: bool
    ) -> None:
        suffix = "failure" if stale_error else "success"
        database_path = Path.cwd() / f"profile_jobs_lease_{suffix}.sqlite3"
        database_path.unlink(missing_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        threads = []
        try:
            Member.__table__.create(engine)
            BusinessJob.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)
            db = session_factory()
            try:
                db.add(Member(id="member-lease", name="Member", profile={}))
                db.add(BusinessJob(
                    id="job-lease",
                    event_id="event-lease",
                    member_id="member-lease",
                    task_type="customer_profile",
                    status="pending",
                    payload={"crop_path": "crop.jpg", "track_id": 9},
                    available_at=self.now - timedelta(seconds=1),
                ))
                db.commit()
            finally:
                db.close()

            started = [threading.Event(), threading.Event()]
            releases = [threading.Event(), threading.Event()]
            call_lock = threading.Lock()
            call_count = 0

            class LeaseRacePipeline:
                def run_from_image_path(self, *args, **kwargs) -> dict:
                    nonlocal call_count
                    with call_lock:
                        call_index = call_count
                        call_count += 1
                    started[call_index].set()
                    releases[call_index].wait(timeout=10)
                    if call_index == 0 and stale_error:
                        raise RuntimeError("stale attempt failed")
                    return {
                        "profile": {"owner": f"attempt-{call_index + 1}"},
                        "track_id": 9,
                        "camera_id": "",
                        "zone_name": "",
                        "crop_path": "crop.jpg",
                    }

            current_time = [self.now]
            pipeline = LeaseRacePipeline()
            workers = [
                ProfileJobWorker(
                    session_factory,
                    pipeline,
                    clock=lambda: current_time[0],
                    lock_timeout_seconds=300,
                )
                for _ in range(2)
            ]
            errors = []

            def run_worker(worker: ProfileJobWorker) -> None:
                try:
                    worker.run_once()
                except Exception as error:
                    errors.append(error)

            first = threading.Thread(target=run_worker, args=(workers[0],))
            threads.append(first)
            first.start()
            self.assertTrue(started[0].wait(timeout=2))

            second_lock_time = self.now + timedelta(seconds=301)
            current_time[0] = second_lock_time
            self.assertEqual(1, workers[1].recover_stale())

            second = threading.Thread(target=run_worker, args=(workers[1],))
            threads.append(second)
            second.start()
            self.assertTrue(started[1].wait(timeout=2))

            releases[0].set()
            first.join(timeout=3)
            self.assertFalse(first.is_alive())
            self.assertEqual([], errors)

            db = session_factory()
            try:
                job = db.get(BusinessJob, "job-lease")
                self.assertEqual("running", job.status)
                self.assertEqual(2, job.attempts)
                self.assertEqual(second_lock_time, job.locked_at)
                self.assertIsNone(job.result)
                self.assertIsNone(job.last_error)
                self.assertEqual({}, db.get(Member, "member-lease").profile)
            finally:
                db.close()

            releases[1].set()
            second.join(timeout=3)
            self.assertFalse(second.is_alive())
            self.assertEqual([], errors)
            db = session_factory()
            try:
                job = db.get(BusinessJob, "job-lease")
                self.assertEqual("succeeded", job.status)
                self.assertEqual({"owner": "attempt-2"}, job.result["profile"])
                self.assertEqual(
                    {"owner": "attempt-2"},
                    db.get(Member, "member-lease").profile[
                        "latest_customer_profile"
                    ],
                )
            finally:
                db.close()
        finally:
            for release in releases if "releases" in locals() else []:
                release.set()
            for thread in threads:
                thread.join(timeout=1)
            engine.dispose()
            database_path.unlink(missing_ok=True)

    def test_stale_success_cannot_finalize_newer_lease(self) -> None:
        self._assert_stale_attempt_cannot_finalize_active_lease(stale_error=False)

    def test_stale_failure_cannot_corrupt_newer_lease(self) -> None:
        self._assert_stale_attempt_cannot_finalize_active_lease(stale_error=True)

    def test_claim_carries_generation_created_by_its_own_cas(self) -> None:
        database_path = Path.cwd() / "profile_jobs_claim_generation.sqlite3"
        database_path.unlink(missing_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        try:
            Member.__table__.create(engine)
            BusinessJob.__table__.create(engine)
            base_factory = sessionmaker(bind=engine)
            db = base_factory()
            try:
                db.add(Member(id="member-generation", name="Member", profile={}))
                db.add(BusinessJob(
                    id="job-generation",
                    event_id="event-generation",
                    member_id="member-generation",
                    task_type="customer_profile",
                    status="pending",
                    payload={"crop_path": "crop.jpg", "track_id": 10},
                    available_at=self.now - timedelta(seconds=1),
                ))
                db.commit()
            finally:
                db.close()

            replacement_lock = self.now + timedelta(seconds=1)
            factory_lock = threading.Lock()
            session_count = 0

            def replace_committed_lease(committed_session) -> None:
                replacement = base_factory()
                try:
                    replaced = (
                        replacement.query(BusinessJob)
                        .filter(
                            BusinessJob.id == "job-generation",
                            BusinessJob.status == "running",
                            BusinessJob.attempts == 1,
                        )
                        .update(
                            {
                                BusinessJob.locked_at: replacement_lock,
                                BusinessJob.attempts: 2,
                            },
                            synchronize_session=False,
                        )
                    )
                    self.assertEqual(1, replaced)
                    replacement.commit()
                finally:
                    replacement.close()

            def worker_session_factory():
                nonlocal session_count
                session = base_factory()
                with factory_lock:
                    session_count += 1
                    is_claim_session = session_count == 1
                if is_claim_session:
                    event.listen(session, "after_commit", replace_committed_lease, once=True)
                return session

            result = {
                "profile": {"owner": "original-attempt"},
                "track_id": 10,
                "camera_id": "",
                "zone_name": "",
                "crop_path": "crop.jpg",
            }
            worker = ProfileJobWorker(
                worker_session_factory,
                StubProfilePipeline(result=result),
                clock=lambda: self.now,
            )

            self.assertTrue(worker.run_once())

            db = base_factory()
            try:
                job = db.get(BusinessJob, "job-generation")
                self.assertEqual("running", job.status)
                self.assertEqual(2, job.attempts)
                self.assertEqual(replacement_lock, job.locked_at)
                self.assertIsNone(job.result)
                self.assertEqual({}, db.get(Member, "member-generation").profile)
            finally:
                db.close()
        finally:
            engine.dispose()
            database_path.unlink(missing_ok=True)

    def test_start_recovers_stale_job_and_stop_joins_daemon_worker(self) -> None:
        database_path = Path.cwd() / "profile_jobs_lifecycle.sqlite3"
        database_path.unlink(missing_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        try:
            Member.__table__.create(engine)
            BusinessJob.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)
            db = session_factory()
            try:
                db.add(Member(id="member-thread", name="Member", profile={}))
                db.add(BusinessJob(
                    id="job-thread",
                    event_id="event-thread",
                    member_id="member-thread",
                    task_type="customer_profile",
                    status="running",
                    attempts=1,
                    locked_at=self.now - timedelta(seconds=301),
                    payload={"crop_path": "crop.jpg", "track_id": 8},
                    available_at=self.now - timedelta(seconds=1),
                ))
                db.commit()
            finally:
                db.close()

            called = threading.Event()

            class LifecyclePipeline(StubProfilePipeline):
                def run_from_image_path(self, *args, **kwargs) -> dict:
                    result = super().run_from_image_path(*args, **kwargs)
                    called.set()
                    return result

            pipeline = LifecyclePipeline(result={
                "profile": {"style_tags": ["casual"]},
                "track_id": 8,
                "camera_id": "",
                "zone_name": "",
                "crop_path": "crop.jpg",
            })
            worker = ProfileJobWorker(
                session_factory,
                pipeline,
                clock=lambda: self.now,
                poll_interval_seconds=0.01,
                lock_timeout_seconds=300,
            )

            worker.start()
            self.assertTrue(called.wait(timeout=2))
            worker.stop()

            self.assertIsNotNone(worker._thread)
            self.assertTrue(worker._thread.daemon)
            self.assertFalse(worker._thread.is_alive())
            db = session_factory()
            try:
                self.assertEqual("succeeded", db.get(BusinessJob, "job-thread").status)
            finally:
                db.close()
        finally:
            engine.dispose()
            database_path.unlink(missing_ok=True)

    def test_stop_during_start_does_not_lose_shutdown_request(self) -> None:
        recovery_started = threading.Event()
        release_recovery = threading.Event()
        stop_lock_attempted = threading.Event()

        class RecordingLock:
            def __init__(self) -> None:
                self._lock = threading.Lock()

            def __enter__(self):
                if threading.current_thread().name == "stop-during-start":
                    stop_lock_attempted.set()
                self._lock.acquire()
                return self

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                self._lock.release()

        class BlockingRecoveryWorker(ProfileJobWorker):
            def recover_stale(self) -> int:
                recovery_started.set()
                release_recovery.wait(timeout=5)
                return 0

            def run_once(self) -> bool:
                self._stop.wait(timeout=5)
                return False

        worker = BlockingRecoveryWorker(
            lambda: None,
            StubProfilePipeline(),
            poll_interval_seconds=0.01,
        )
        worker._lifecycle_lock = RecordingLock()
        start_thread = threading.Thread(target=worker.start)
        stop_thread = threading.Thread(target=worker.stop, name="stop-during-start")

        try:
            start_thread.start()
            self.assertTrue(recovery_started.wait(timeout=1))
            stop_thread.start()
            self.assertTrue(stop_lock_attempted.wait(timeout=1))
            release_recovery.set()
            start_thread.join(timeout=3)
            stop_thread.join(timeout=3)

            self.assertFalse(start_thread.is_alive())
            self.assertFalse(stop_thread.is_alive())
            self.assertIsNotNone(worker._thread)
            self.assertFalse(worker._thread.is_alive())
        finally:
            release_recovery.set()
            worker._stop.set()
            start_thread.join(timeout=1)
            stop_thread.join(timeout=1)
            if worker._thread is not None:
                worker._thread.join(timeout=1)

    def test_stop_reports_blocked_model_thread_and_preserves_lease(self) -> None:
        database_path = Path.cwd() / "profile_jobs_stop_timeout.sqlite3"
        database_path.unlink(missing_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        release_model = threading.Event()
        model_started = threading.Event()
        try:
            Member.__table__.create(engine)
            BusinessJob.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)
            db = session_factory()
            try:
                db.add(Member(id="member-stop", name="Member", profile={}))
                db.add(BusinessJob(
                    id="job-stop",
                    event_id="event-stop",
                    member_id="member-stop",
                    task_type="customer_profile",
                    status="pending",
                    payload={"crop_path": "crop.jpg", "track_id": 11},
                    available_at=self.now - timedelta(seconds=1),
                ))
                db.commit()
            finally:
                db.close()

            class BlockingPipeline(StubProfilePipeline):
                def run_from_image_path(self, *args, **kwargs) -> dict:
                    model_started.set()
                    release_model.wait(timeout=10)
                    return {
                        "profile": {"source": "blocked-model"},
                        "track_id": 11,
                        "camera_id": "",
                        "zone_name": "",
                        "crop_path": "crop.jpg",
                    }

            worker = ProfileJobWorker(
                session_factory,
                BlockingPipeline(),
                clock=lambda: self.now,
                poll_interval_seconds=0.01,
            )
            worker.start()
            self.assertTrue(model_started.wait(timeout=1))
            old_thread = worker._thread

            db = session_factory()
            try:
                active_job = db.get(BusinessJob, "job-stop")
                original_lock = active_job.locked_at
                self.assertEqual("running", active_job.status)
                self.assertEqual(1, active_job.attempts)
                self.assertIsNotNone(original_lock)
            finally:
                db.close()

            with self.assertRaisesRegex(TimeoutError, "did not stop within 2 seconds"):
                worker.stop()
            self.assertTrue(old_thread.is_alive())
            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                worker.start()

            db = session_factory()
            try:
                active_job = db.get(BusinessJob, "job-stop")
                self.assertEqual("running", active_job.status)
                self.assertEqual(1, active_job.attempts)
                self.assertEqual(original_lock, active_job.locked_at)
            finally:
                db.close()

            release_model.set()
            old_thread.join(timeout=3)
            self.assertFalse(old_thread.is_alive())

            worker.start()
            self.assertIsNot(old_thread, worker._thread)
            worker.stop()
            self.assertFalse(worker._thread.is_alive())
        finally:
            release_model.set()
            if "worker" in locals():
                try:
                    worker.stop()
                except TimeoutError:
                    pass
            engine.dispose()
            database_path.unlink(missing_ok=True)


class StableTrackFrameCallbackTests(unittest.TestCase):
    def test_device_api_captures_before_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileSnapshotStore(tmpdir)
            callback_order = []

            def capture_snapshot(event: StableTrackEvent, frame: np.ndarray) -> None:
                callback_order.append("snapshot")
                self.assertIsNotNone(store.capture(event, frame))

            def submit_event(event: StableTrackEvent) -> None:
                callback_order.append("event")

            detector = SinglePersonDetector(
                Detection([10, 20, 60, 90], 0.95, 0, "person", track_id=42)
            )
            pipeline = RealtimeEntryPipeline(
                detector=detector,
                stability_threshold=1,
                identity_mode="device_api",
                camera_id="camera-1",
                on_stable_track_frame=capture_snapshot,
                on_stable_track=submit_event,
            )

            result = pipeline.process_frame(np.full((100, 100, 3), 127, dtype=np.uint8))

            self.assertEqual(callback_order, ["snapshot", "event"])
            self.assertTrue(store.get("camera-1", 42))
            self.assertEqual(result["confirmed_entries"][0]["entry_type"], "identity_pending")

    def test_realtime_pipeline_has_no_profile_processing_imports_or_calls(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "pipelines" / "realtime_entry_pipeline.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        forbidden_names = {
            "analyze_image",
            "run_from_image_path",
            "encode_image_base64",
            "CustomerProfilePipeline",
        }

        imported_names = set()
        called_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.add(node.module or "")
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr)

        self.assertTrue(forbidden_names.isdisjoint(imported_names))
        self.assertTrue(forbidden_names.isdisjoint(called_names))


if __name__ == "__main__":
    unittest.main()
