from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.visits.member_arrival_effects import MemberArrivalEffectService
from agent.models import Member, MemberArrival, MemberArrivalEffect, Visit
from agent.schema_evolution import evolve_schema


class MemberArrivalEffectTests(unittest.TestCase):
    def setUp(self):
        self.engine = configure_sqlite_foreign_keys(create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False,
            expire_on_commit=False,
        )
        self.now = datetime(2026, 8, 5, 6, 0, 0)
        db = self.sessions()
        db.add(Member(id="member-1", name="Member One"))
        db.commit()
        visit = Visit(member_id="member-1", store_id="store-1", entry_time=self.now)
        db.add(visit)
        db.flush()
        arrival = MemberArrival(
            visit_id=visit.id,
            member_id="member-1",
            occurred_at=self.now,
            arrival_source="device_recognition",
        )
        db.add(arrival)
        db.flush()
        db.add(MemberArrivalEffect(
            member_arrival_id=arrival.id,
            effect_type="welcome_display",
            status="pending",
            deadline_at=self.now + timedelta(seconds=30),
        ))
        db.commit()
        db.close()
        self.service = MemberArrivalEffectService(
            self.sessions, clock=lambda: self.now
        )

    def tearDown(self):
        self.engine.dispose()

    def test_claim_and_ack_are_token_guarded_and_idempotent(self):
        claim = self.service.claim(
            effect_type="welcome_display", consumer_id="display-1"
        )

        self.assertIsNotNone(claim)
        self.assertTrue(self.service.ack(
            effect_id=claim.effect_id,
            consumer_id="display-1",
            claim_token=claim.claim_token,
            executed_at=self.now + timedelta(seconds=1),
        ))
        self.assertTrue(self.service.ack(
            effect_id=claim.effect_id,
            consumer_id="display-1",
            claim_token=claim.claim_token,
            executed_at=self.now + timedelta(seconds=1),
        ))
        self.assertIsNone(self.service.claim(
            effect_type="welcome_display", consumer_id="display-2"
        ))

    def test_deadline_is_rechecked_before_execution(self):
        db = self.sessions()
        db.query(MemberArrivalEffect).update({
            MemberArrivalEffect.deadline_at: self.now + timedelta(seconds=5)
        })
        db.commit()
        db.close()
        claim = self.service.claim(
            effect_type="welcome_display", consumer_id="display-1"
        )
        self.now += timedelta(seconds=6)

        self.assertFalse(self.service.authorize_execution(
            effect_id=claim.effect_id,
            consumer_id="display-1",
            claim_token=claim.claim_token,
        ))

        db = self.sessions()
        effect = db.get(MemberArrivalEffect, claim.effect_id)
        self.assertEqual("suppressed", effect.status)
        self.assertEqual("arrival_effect_delay_exceeded", effect.reason)
        db.close()

    def test_expired_processing_lease_can_be_reclaimed(self):
        db = self.sessions()
        db.query(MemberArrivalEffect).update({MemberArrivalEffect.deadline_at: None})
        db.commit()
        db.close()
        first = self.service.claim(
            effect_type="welcome_display", consumer_id="display-1"
        )
        self.now += timedelta(seconds=31)

        second = self.service.claim(
            effect_type="welcome_display", consumer_id="display-2"
        )

        self.assertIsNotNone(second)
        self.assertNotEqual(first.claim_token, second.claim_token)
        self.assertFalse(self.service.ack(
            effect_id=first.effect_id,
            consumer_id="display-1",
            claim_token=first.claim_token,
            executed_at=self.now,
        ))

    def test_failed_effect_respects_retry_time(self):
        claim = self.service.claim(
            effect_type="welcome_display", consumer_id="display-1"
        )
        self.assertTrue(self.service.fail(
            effect_id=claim.effect_id,
            consumer_id="display-1",
            claim_token=claim.claim_token,
            reason="display_offline",
            retry_after_seconds=5,
        ))
        self.assertIsNone(self.service.claim(
            effect_type="welcome_display", consumer_id="display-2"
        ))

        self.now += timedelta(seconds=5)
        retry = self.service.claim(
            effect_type="welcome_display", consumer_id="display-2"
        )
        self.assertIsNotNone(retry)
        self.assertEqual(2, self._effect(retry.effect_id).attempts)

    def _effect(self, effect_id):
        db = self.sessions()
        try:
            return db.get(MemberArrivalEffect, effect_id)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
