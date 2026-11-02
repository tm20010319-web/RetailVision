from datetime import datetime, timedelta
import unittest

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.models import (
    Base,
    BusinessJob,
    DeviceHealthState,
    DeviceRecognitionInbox,
    Member,
    MemberEntryEvent,
)
from agent.api.operations_query_service import OperationsQueryService


class OperationsQueryServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.now = datetime(2026, 7, 21, 12, 0, 0)
        self.service = self._service()

    def tearDown(self):
        self.engine.dispose()

    def _service(self, profile_mode="enabled", *, clock=None, health_clock=None):
        return OperationsQueryService(
            self.session_factory,
            device_id="entrance-1",
            health_freshness_seconds=90,
            profile_mode=profile_mode,
            clock=clock or (lambda: self.now),
            health_clock=health_clock or (lambda: self.now),
        )

    def _add_health(self, *, push_at, poll_at):
        db = self.session_factory()
        try:
            db.add(DeviceHealthState(
                device_id="entrance-1",
                push_healthy=push_at is not None,
                poll_healthy=poll_at is not None,
                last_heartbeat_at=push_at,
                last_poll_success_at=poll_at,
            ))
            db.commit()
        finally:
            db.close()

    def _add_member(self, member_id, tier="bronze"):
        db = self.session_factory()
        try:
            db.add(Member(id=member_id, name=member_id, tier=tier))
            db.commit()
        finally:
            db.close()

    def _add_inbox(
        self,
        event_id,
        status,
        occurred_at,
        *,
        member_id=None,
        last_error=None,
        capture_path=None,
        matched_visit_id=None,
    ):
        db = self.session_factory()
        try:
            db.add(DeviceRecognitionInbox(
                canonical_event_id=event_id,
                device_id="entrance-1",
                source="push",
                source_event_id=event_id,
                occurred_at=occurred_at,
                member_id=member_id,
                unique_id="",
                similarity=0.91,
                capture_path=capture_path,
                matched_visit_id=matched_visit_id,
                status=status,
                last_error=last_error,
                received_at=occurred_at,
            ))
            db.commit()
        finally:
            db.close()

    def test_recent_recognitions_projects_new_mode_matched_visit(self):
        self._add_member("new-mode-member")
        self._add_inbox(
            "new-mode-recognition",
            "processed",
            self.now,
            member_id="new-mode-member",
            matched_visit_id="visit-new-mode",
        )
        self._add_member_entry("new-mode-recognition", "new-mode-member")

        item = self.service.recent_recognitions(now=self.now)["items"][0]

        self.assertEqual("visit-new-mode", item["visit_id"])

    def _add_member_entry(self, event_id, member_id, *, visit_id=None, track_id=None):
        db = self.session_factory()
        try:
            db.add(MemberEntryEvent(
                event_id=event_id,
                device_id="entrance-1",
                member_id=member_id,
                occurred_at=self.now,
                similarity=0.91,
                identity_time=self.now,
                visit_id=visit_id,
                track_id=track_id,
            ))
            db.commit()
        finally:
            db.close()

    def _add_profile_job(self, status, *, locked_at=None):
        db = self.session_factory()
        try:
            db.add(Member(id="profile-member", name="Profile Member"))
            db.add(BusinessJob(
                id="profile-job",
                event_id="profile-event",
                member_id="profile-member",
                task_type="customer_profile",
                status=status,
                payload={},
                available_at=self.now,
                locked_at=locked_at,
            ))
            db.commit()
        finally:
            db.close()

    def _replace_profile_jobs_with_due_pending(self):
        db = self.session_factory()
        try:
            db.query(BusinessJob).delete()
            db.add(BusinessJob(
                id="profile-job-pending",
                event_id="profile-event-pending",
                member_id="profile-member",
                task_type="customer_profile",
                status="pending",
                payload={},
                available_at=self.now,
            ))
            db.commit()
        finally:
            db.close()

    def test_missing_health_is_unknown(self):
        data = self.service.status(self.now)
        self.assertEqual(data["push"]["state"], "unknown")
        self.assertEqual(data["poll"]["state"], "unknown")
        self.assertEqual(data["runtime"]["state"], "unknown")

    def test_fresh_push_makes_merged_runtime_running(self):
        self._add_health(push_at=self.now, poll_at=None)
        data = self.service.status(self.now)
        self.assertEqual(data["push"]["state"], "healthy")
        self.assertEqual(data["runtime"]["state"], "running")
        self.assertEqual(data["runtime"]["observed_at"], self.now.isoformat())
        self.assertEqual(data["runtime"]["basis"], "push_or_poll")

    def test_health_freshness_uses_utc_without_changing_generated_at(self):
        local_now = datetime(2026, 7, 23, 14, 42, 28)
        utc_now = datetime(2026, 7, 23, 6, 42, 28)
        heartbeat_at = utc_now - timedelta(seconds=30)
        self._add_health(push_at=heartbeat_at, poll_at=None)
        service = self._service(
            clock=lambda: local_now,
            health_clock=lambda: utc_now,
        )

        data = service.status()

        self.assertEqual(data["generated_at"], local_now.isoformat())
        self.assertEqual(data["push"]["state"], "healthy")
        self.assertEqual(data["runtime"]["state"], "running")

    def test_fresh_poll_makes_runtime_running_and_old_poll_is_stale(self):
        self._add_health(push_at=None, poll_at=self.now)
        self.assertEqual(self.service.status(self.now)["runtime"]["state"], "running")
        later = self.now + timedelta(seconds=91)
        self.assertEqual(self.service.status(later)["runtime"]["state"], "stale")

    def test_inbox_counts_and_oldest_pending_are_reported(self):
        self._add_inbox("received-1", "received", self.now - timedelta(seconds=2))
        self._add_inbox("processing-1", "processing", self.now - timedelta(seconds=1))
        self._add_inbox("mapping-1", "waiting_mapping", self.now)
        inbox = self.service.status(self.now)["inbox"]
        self.assertEqual(
            (inbox["received"], inbox["processing"], inbox["waiting_mapping"]),
            (1, 1, 1),
        )
        self.assertEqual(
            inbox["oldest_pending_at"],
            (self.now - timedelta(seconds=2)).isoformat(),
        )

    def test_profile_states_cover_idle_busy_backlogged_and_disabled(self):
        self.assertEqual(self.service.status(self.now)["profile_worker"]["state"], "idle")
        self._add_profile_job("running", locked_at=self.now)
        self.assertEqual(self.service.status(self.now)["profile_worker"]["state"], "busy")
        self._replace_profile_jobs_with_due_pending()
        self.assertEqual(
            self.service.status(self.now)["profile_worker"]["state"], "backlogged"
        )
        disabled = self._service(profile_mode="disabled")
        self.assertEqual(disabled.status(self.now)["profile_worker"]["state"], "disabled")

    def test_recent_recognition_enriches_member_and_redacts_capture_path(self):
        self._add_member("gold-member", tier=" GOLD ")
        self._add_inbox(
            "recognized-1",
            "processed",
            self.now,
            member_id="gold-member",
            capture_path="captures/gold-member.jpg",
        )
        self._add_member_entry(
            "recognized-1", "gold-member", visit_id="visit-1", track_id=18
        )
        data = self.service.recent_recognitions(limit=20, now=self.now)
        item = data["items"][0]
        self.assertEqual(item["member"]["priority_rank"], 300)
        self.assertEqual(item["member"]["alert_level"], "vip")
        self.assertTrue(item["capture_available"])
        self.assertNotIn("capture_path", item)

    def test_recent_order_limit_and_nullable_relationships(self):
        self._add_inbox(
            "older", "waiting_mapping", self.now - timedelta(seconds=1), member_id=None
        )
        self._add_inbox(
            "newer",
            "ignored",
            self.now,
            member_id=None,
            last_error="member_debounce",
        )
        self.assertEqual(
            [item["event_id"] for item in self.service.recent_recognitions(1, self.now)["items"]],
            ["newer"],
        )
        older = self.service.recent_recognitions(100, self.now)["items"][1]
        self.assertIsNone(older["member"])
        self.assertIsNone(older["visit_id"])
        self.assertIsNone(older["track_id"])
        self.assertEqual(older["reason"], "waiting_mapping")

    def test_recent_recognitions_rejects_invalid_limits(self):
        for limit in (0, -1, 101, True, "20"):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    self.service.recent_recognitions(limit=limit, now=self.now)

    def test_recent_recognitions_exposes_unmapped_terminal_reason(self):
        self._add_inbox(
            "unmapped-1",
            "unmapped",
            self.now,
            member_id=None,
            last_error="mapping_timeout",
        )

        item = self.service.recent_recognitions(20, self.now)["items"][0]

        self.assertEqual("unmapped", item["status"])
        self.assertEqual("mapping_timeout", item["reason"])

    def test_recent_recognitions_use_bulk_member_and_event_reads(self):
        for index in range(3):
            member_id = f"member-{index}"
            event_id = f"event-{index}"
            self._add_member(member_id, tier="gold")
            self._add_inbox(event_id, "processed", self.now, member_id=member_id)
            self._add_member_entry(event_id, member_id, track_id=index)

        select_statements = []

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                select_statements.append(statement)

        sqlalchemy_event.listen(self.engine, "before_cursor_execute", count_selects)
        try:
            self.service.recent_recognitions(limit=20, now=self.now)
        finally:
            sqlalchemy_event.remove(self.engine, "before_cursor_execute", count_selects)

        self.assertLessEqual(len(select_statements), 3)


if __name__ == "__main__":
    unittest.main()
