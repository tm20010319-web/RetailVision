from __future__ import annotations

import unittest
from datetime import datetime

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.models import CameraIdentityBindingPolicy, EvidenceClip, TrackAssociationEvaluation
from agent.schema_evolution import evolve_schema
from agent.identity.track_association_policy import CameraIdentityBindingPolicyService, TrackAssociationAuditService


class TrackAssociationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 5, 6)
        self.engine = configure_sqlite_foreign_keys(create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.policies = CameraIdentityBindingPolicyService(self.sessions, clock=lambda: self.now)
        self.audit = TrackAssociationAuditService(self.sessions, clock=lambda: self.now)

    def tearDown(self):
        self.engine.dispose()

    def test_confirmed_shadow_creates_clip_and_unavailable_evidence_is_uncertain_only(self):
        evaluation = self.audit.record_evaluation(
            recognition_event_id="event-1", camera_id="camera-1", runtime_id="runtime-1",
            result="confirmed", method="spatial", reason=None, candidates=[], observed_at=self.now,
            algorithm_version="a1", parameter_version="p1", policy_version_id=None,
            applied_binding=False, mode="shadow",
        )
        self.assertIsNotNone(evaluation.evidence_clip_id)
        with self.assertRaises(ValueError):
            self.audit.review(evaluation_id=evaluation.id, verdict="correct", reviewer="reviewer")
        review = self.audit.review(evaluation_id=evaluation.id, verdict="uncertain", reviewer="reviewer", reason="evidence_unavailable")
        self.assertEqual("uncertain", review.verdict)

    def test_incorrect_confirmed_review_rolls_enabled_camera_back(self):
        db = self.sessions()
        policy = CameraIdentityBindingPolicy(
            camera_id="camera-1", mode="enabled", algorithm_version="a1",
            parameter_version="p1", effective_from=self.now,
            change_reason="test_fixture", qualification_snapshot={},
        )
        db.add(policy)
        db.commit()
        db.close()
        evaluation = self.audit.record_evaluation(
            recognition_event_id="event-2", camera_id="camera-1", runtime_id="runtime-1",
            result="confirmed", method="spatial", reason=None, candidates=[], observed_at=self.now,
            algorithm_version="a1", parameter_version="p1", policy_version_id=policy.id,
            applied_binding=True, mode="enabled",
        )
        db = self.sessions()
        db.get(EvidenceClip, evaluation.evidence_clip_id).status = "ready"
        db.get(TrackAssociationEvaluation, evaluation.id).evidence_status = "ready"
        db.commit()
        db.close()

        self.audit.review(evaluation_id=evaluation.id, verdict="incorrect", reviewer="reviewer", reason="wrong_person")

        db = self.sessions()
        try:
            current = db.query(CameraIdentityBindingPolicy).filter(CameraIdentityBindingPolicy.effective_to.is_(None)).one()
            self.assertEqual("rolled_back", current.mode)
            self.assertEqual(evaluation.id, current.rollback_evaluation_id)
        finally:
            db.close()

    def test_unreviewed_confirmed_sample_prevents_qualification(self):
        db = self.sessions()
        for index in range(100):
            db.add(TrackAssociationEvaluation(
                recognition_event_id=f"event-q-{index}", camera_id="camera-q", runtime_id="runtime",
                mode="shadow", result="confirmed" if index < 50 else "probable",
                candidates=[], observed_at=self.now.replace(day=3 + index % 3),
                algorithm_version="a1", parameter_version="p1", applied_binding=False,
                evidence_status="ready",
            ))
        db.commit()
        db.close()
        self.assertFalse(self.audit.qualification("camera-q")["qualified"])


if __name__ == "__main__":
    unittest.main()
