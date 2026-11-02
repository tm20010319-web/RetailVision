from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.member.member_priority import alert_level, normalize_tier, sql_tier_rank, tier_rank
from agent.models import Base, BusinessJob, Member
from agent.profile.profile_jobs import ProfileJobWorker


class MemberPriorityTests(unittest.TestCase):
    def test_normalizes_tiers_and_assigns_rank_and_alert(self):
        cases = [
            ("Diamond", "diamond", 500, "vip"),
            (" platinum ", "platinum", 400, "vip"),
            (" GOLD ", "gold", 300, "vip"),
            ("Silver", "silver", 200, "priority"),
            ("bronze", "bronze", 100, "normal"),
            (None, "unknown", 0, "normal"),
            ("invalid", "unknown", 0, "normal"),
        ]
        for raw, normalized, rank, alert in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_tier(raw), normalized)
                self.assertEqual(tier_rank(raw), rank)
                self.assertEqual(alert_level(raw), alert)

    def test_sql_tier_rank_uses_canonical_values(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            values = db.query(sql_tier_rank(Member.tier)).select_from(Member).all()
            self.assertEqual(values, [])

            db.add_all([
                Member(id="diamond", name="diamond", tier="DIAMOND"),
                Member(id="platinum", name="platinum", tier=" platinum "),
                Member(id="gold", name="gold", tier=" GOLD "),
                Member(id="silver", name="silver", tier="Silver"),
                Member(id="bronze", name="bronze", tier="bronze"),
                Member(id="unknown", name="unknown", tier="invalid"),
            ])
            db.commit()
            ranked = dict(
                db.query(Member.id, sql_tier_rank(Member.tier))
                .order_by(Member.id)
                .all()
            )
            self.assertEqual(ranked, {
                "bronze": 100,
                "diamond": 500,
                "gold": 300,
                "platinum": 400,
                "silver": 200,
                "unknown": 0,
            })
        finally:
            db.close()


class ProfileClaimPriorityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        self.factory = Session
        self.now = datetime(2026, 7, 21, 12, 0, 0)

    def _add_job(self, member_id, tier, job_id, available_at, created_at=None):
        if created_at is None:
            created_at = available_at
        db = self.factory()
        db.add(Member(id=member_id, name=member_id, tier=tier))
        db.add(BusinessJob(
            id=job_id,
            event_id=f"event-{job_id}",
            member_id=member_id,
            task_type="customer_profile",
            status="pending",
            payload={"crop_path": "x.jpg", "track_id": None},
            available_at=available_at,
            created_at=created_at,
        ))
        db.commit()
        db.close()

    def test_higher_tier_is_claimed_before_older_lower_tier(self):
        self._add_job("bronze-member", "bronze", "job-bronze", self.now)
        self._add_job("gold-member", "gold", "job-gold", self.now)
        worker = ProfileJobWorker(
            self.factory,
            MagicMock(),
            clock=lambda: self.now,
        )
        claimed = worker._claim_due_job()
        self.assertEqual(claimed.lease.job_id, "job-gold")

    def test_diamond_is_claimed_before_platinum_and_gold(self):
        self._add_job("gold-member", "gold", "job-gold", self.now)
        self._add_job("platinum-member", "platinum", "job-platinum", self.now)
        self._add_job("diamond-member", "diamond", "job-diamond", self.now)
        worker = ProfileJobWorker(self.factory, MagicMock(), clock=lambda: self.now)
        self.assertEqual(worker._claim_due_job().lease.job_id, "job-diamond")

    def test_same_tier_uses_available_time_before_created_time(self):
        self._add_job(
            "gold-later",
            "gold",
            "job-1",
            self.now,
            created_at=self.now,
        )
        self._add_job(
            "gold-earlier",
            "gold",
            "job-2",
            self.now - timedelta(seconds=1),
            created_at=self.now,
        )
        worker = ProfileJobWorker(self.factory, MagicMock(), clock=lambda: self.now)
        self.assertEqual(worker._claim_due_job().lease.job_id, "job-2")

    def test_same_available_time_uses_created_time_before_id(self):
        self._add_job(
            "gold-later",
            "gold",
            "job-1",
            self.now,
            created_at=self.now + timedelta(seconds=1),
        )
        self._add_job(
            "gold-earlier",
            "gold",
            "job-2",
            self.now,
            created_at=self.now,
        )
        worker = ProfileJobWorker(self.factory, MagicMock(), clock=lambda: self.now)
        self.assertEqual(worker._claim_due_job().lease.job_id, "job-2")

    def test_same_available_and_created_time_uses_id(self):
        self._add_job("gold-2", "gold", "job-2", self.now)
        self._add_job("gold-1", "gold", "job-1", self.now)
        worker = ProfileJobWorker(self.factory, MagicMock(), clock=lambda: self.now)
        self.assertEqual(worker._claim_due_job().lease.job_id, "job-1")

    def test_future_and_exhausted_jobs_are_not_claimed(self):
        self._add_job("future", "gold", "job-future", self.now + timedelta(seconds=1))
        self._add_job("ready", "bronze", "job-ready", self.now)
        db = self.factory()
        exhausted = db.get(BusinessJob, "job-ready")
        exhausted.attempts = exhausted.max_attempts
        db.commit()
        db.close()
        worker = ProfileJobWorker(self.factory, MagicMock(), clock=lambda: self.now)
        self.assertIsNone(worker._claim_due_job())

    def test_running_job_is_not_preempted(self):
        self._add_job("running", "gold", "job-running", self.now)
        self._add_job("pending", "silver", "job-pending", self.now)
        db = self.factory()
        running = db.get(BusinessJob, "job-running")
        running.status = "running"
        running.locked_at = self.now
        db.commit()
        db.close()
        worker = ProfileJobWorker(self.factory, MagicMock(), clock=lambda: self.now)
        self.assertEqual(worker._claim_due_job().lease.job_id, "job-pending")
