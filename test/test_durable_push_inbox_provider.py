from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.models import Base, DeviceRecognitionInbox
from agent.identity.device_recognition_intake import DeviceRecognitionEvent, canonical_event_id
from agent.offline.offline_identity import (
    HistoricalIdentityResolver,
    HistoricalIdentityRevisionData,
)
from agent.offline.offline_recognition_provider import (
    DeviceRecognitionAcceptancePolicy,
    DurablePushInboxRecognitionProvider,
    RecognitionRecordQueryResult,
)
from agent.identity.push_coverage import PushCoverageResult


START = datetime(2026, 8, 18, 12, 41, 20)
END = START + timedelta(minutes=5)


class DurablePushInboxProviderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.coverage = Mock()
        self.coverage.coverage.return_value = self._coverage("complete")
        self.resolver = HistoricalIdentityResolver(
            history_available_from=START - timedelta(days=1),
            revisions=(
                HistoricalIdentityRevisionData(
                    revision_id="revision-historical",
                    device_id="cam02",
                    group_id=7,
                    person_id=42,
                    unique_id="person-42",
                    member_id="member-historical",
                    effective_from=START - timedelta(days=1),
                    effective_to=None,
                    mapping_enabled=True,
                    mapping_sync_status="synced",
                    member_principal_exists=True,
                    member_principal_active=True,
                ),
            ),
        )
        self.acceptance = DeviceRecognitionAcceptancePolicy.create(
            policy_id="push-acceptance",
            policy_version="push-v1",
            similarity_threshold="0.75",
            accepted_rule_ids=(1,),
            accepted_pass_codes=(None,),
            require_reco_valid=False,
        )
        self.provider = self._provider()
        self.addCleanup(self.engine.dispose)

    def _coverage(self, status: str) -> PushCoverageResult:
        return PushCoverageResult(
            status=status,
            device_id="cam02",
            coverage_start=START,
            coverage_end=END,
            policy_version="p6s_push_coverage_v1",
            reason=None if status == "complete" else "heartbeat_gap_exceeded",
            material_hash=f"hash-{status}",
            supporting_observation_ids=("obs-1",),
        )

    def _provider(self):
        return DurablePushInboxRecognitionProvider(
            session_factory=self.sessions,
            push_coverage_store=self.coverage,
            historical_identity_resolver=self.resolver,
            acceptance_policy=self.acceptance,
            device_id="cam02",
        )

    def test_profile_source_mode_mismatch_fails_before_coverage_query(self):
        profile = SimpleNamespace(
            recognition_evidence_mode="historical_query",
            push_coverage_policy_id="p6s_push_coverage",
            push_coverage_policy_version="p6s_push_coverage_v1",
            recognition_acceptance_policy_hash=self.acceptance.policy_hash,
        )
        resolution = Mock()
        resolution.profile = profile
        resolution.production_eligibility_reason.return_value = None
        provider = DurablePushInboxRecognitionProvider(
            session_factory=self.sessions,
            push_coverage_store=self.coverage,
            historical_identity_resolver=self.resolver,
            acceptance_policy=self.acceptance,
            device_id="cam02",
            profile_resolution=resolution,
        )
        result = provider.query(START, END)
        self.assertEqual(("unavailable", "recognition_source_mode_mismatch"), (result.status, result.failure_reason))
        self.coverage.coverage.assert_not_called()

    def _add_row(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        source: str = "push",
        device_id: str = "cam02",
        member_id: str | None = "member-current",
        similarity: float = 0.90,
        rule_id: int | None = 1,
        face_spatial_evidence: dict | None = None,
    ):
        source_event_id = f"source-{event_id}"
        canonical_id = canonical_event_id(DeviceRecognitionEvent(
            device_id=device_id,
            source="push",
            source_event_id=source_event_id,
            occurred_at=occurred_at,
            group_id=7,
            person_id=42,
            unique_id="person-42",
            similarity=similarity,
            rule_id=rule_id,
        ))
        session = self.sessions()
        try:
            session.add(DeviceRecognitionInbox(
                canonical_event_id=canonical_id,
                device_id=device_id,
                source=source,
                source_event_id=source_event_id,
                occurred_at=occurred_at,
                member_id=member_id,
                group_id=7,
                person_id=42,
                unique_id="person-42",
                similarity=similarity,
                rule_id=rule_id,
                face_spatial_evidence=face_spatial_evidence,
            ))
            session.commit()
        finally:
            session.close()
        return canonical_id

    def test_coverage_unavailable_returns_before_inbox_query(self):
        self.coverage.coverage.return_value = self._coverage("unavailable")
        session_factory = Mock(side_effect=AssertionError("Inbox query must not run"))
        provider = DurablePushInboxRecognitionProvider(
            session_factory=session_factory,
            push_coverage_store=self.coverage,
            historical_identity_resolver=self.resolver,
            acceptance_policy=self.acceptance,
            device_id="cam02",
        )
        result = provider.query(START, END)
        self.assertEqual("unavailable", result.status)
        self.assertFalse(result.coverage_complete)
        self.assertEqual("durable_push_inbox", result.source_mode)
        self.assertEqual("hash-unavailable", result.coverage_material_hash)
        self.assertEqual((), result.accepted_evidence)
        session_factory.assert_not_called()

    def test_coverage_unavailable_with_records_still_returns_unavailable(self):
        self._add_row(event_id="event-1", occurred_at=START)
        self.coverage.coverage.return_value = self._coverage("unavailable")
        result = self.provider.query(START, END)
        self.assertEqual("unavailable", result.status)
        self.assertEqual(0, result.raw_record_count)

    def test_complete_zero_records_is_complete_empty_result(self):
        result = self.provider.query(START, END)
        self.assertEqual("complete", result.status)
        self.assertTrue(result.coverage_complete)
        self.assertEqual((), result.accepted_evidence)
        self.assertEqual(0, result.raw_record_count)

    def test_valid_push_event_uses_historical_mapping_and_provenance(self):
        event_id = self._add_row(event_id="event-1", occurred_at=START)
        result = self.provider.query(START, END)
        self.assertEqual("complete", result.status)
        self.assertEqual(1, len(result.accepted_evidence))
        evidence = result.accepted_evidence[0]
        self.assertEqual("member-historical", evidence.member_id)
        self.assertEqual(event_id, evidence.canonical_event_id)
        self.assertEqual("source-event-1", evidence.source_event_id)
        self.assertEqual("durable_push_inbox", result.source_mode)

    def test_current_member_id_never_overrides_historical_mapping(self):
        self._add_row(event_id="event-2", occurred_at=START, member_id="member-current")
        result = self.provider.query(START, END)
        self.assertEqual("member-historical", result.accepted_evidence[0].member_id)
        self.assertTrue(any(
            diagnostic.get("reason") == "inbox_member_mapping_mismatch"
            for item in result.record_audits
            for diagnostic in item.diagnostics
        ))

    def test_missing_historical_mapping_is_rejected_without_current_fallback(self):
        self._add_row(event_id="event-3", occurred_at=START, member_id="member-current")
        self.provider = DurablePushInboxRecognitionProvider(
            session_factory=self.sessions,
            push_coverage_store=self.coverage,
            historical_identity_resolver=HistoricalIdentityResolver(
                history_available_from=START - timedelta(days=1), revisions=()
            ),
            acceptance_policy=self.acceptance,
            device_id="cam02",
        )
        result = self.provider.query(START, END)
        self.assertEqual("complete", result.status)
        self.assertEqual((), result.accepted_evidence)
        self.assertEqual("mapping_historical_unmapped", result.record_audits[0].reason)

    def test_source_and_device_scope_are_isolated(self):
        self._add_row(event_id="event-4", occurred_at=START, source="poll")
        self._add_row(event_id="event-5", occurred_at=START, device_id="cam01")
        result = self.provider.query(START, END)
        self.assertEqual(0, result.raw_record_count)

    def test_records_are_ordered_by_time_then_canonical_id(self):
        b = self._add_row(event_id="event-b", occurred_at=START + timedelta(seconds=2))
        a = self._add_row(event_id="event-a", occurred_at=START + timedelta(seconds=1))
        result = self.provider.query(START, END)
        self.assertEqual([a, b], [
            item.canonical_event_id for item in result.accepted_evidence
        ])

    def test_acceptance_reject_keeps_complete_coverage(self):
        self._add_row(event_id="event-6", occurred_at=START, similarity=0.50)
        result = self.provider.query(START, END)
        self.assertEqual("complete", result.status)
        self.assertEqual((), result.accepted_evidence)
        self.assertEqual("similarity_below_threshold", result.record_audits[0].reason)

    def test_structural_corruption_fails_closed(self):
        event_id = self._add_row(event_id="event-7", occurred_at=START)
        session = self.sessions()
        try:
            row = session.get(DeviceRecognitionInbox, event_id)
            row.occurred_at = START + timedelta(seconds=1)
            session.commit()
        finally:
            session.close()
        result = self.provider.query(START, END)
        self.assertEqual("unavailable", result.status)
        self.assertEqual("response_integrity_failure", result.failure_reason)

    def test_face_spatial_evidence_is_preserved(self):
        spatial = {
            "coordinate_space": "normalized_full_frame",
            "normalized_bbox": [0.1, 0.2, 0.3, 0.4],
            "source_frame_size": [1920, 1088],
            "quality_status": "parsed",
        }
        self._add_row(
            event_id="event-8",
            occurred_at=START,
            face_spatial_evidence=spatial,
        )
        evidence = self.provider.query(START, END).accepted_evidence[0]
        self.assertEqual(spatial, evidence.face_spatial_evidence)

    def test_coverage_material_and_policy_are_propagated(self):
        result = self.provider.query(START, END)
        self.assertEqual("hash-complete", result.coverage_material_hash)
        self.assertEqual("p6s_push_coverage_v1", result.coverage_policy_version)
        self.assertEqual(START, result.coverage_start)
        self.assertEqual(END, result.coverage_end)

    def test_start_and_end_boundaries_are_inclusive(self):
        self._add_row(event_id="event-9", occurred_at=START)
        self._add_row(event_id="event-a", occurred_at=END)
        result = self.provider.query(START, END)
        self.assertEqual(2, len(result.accepted_evidence))

    def test_inbox_query_failure_is_fail_closed(self):
        broken = Mock(side_effect=RuntimeError("db unavailable"))
        provider = DurablePushInboxRecognitionProvider(
            session_factory=broken,
            push_coverage_store=self.coverage,
            historical_identity_resolver=self.resolver,
            acceptance_policy=self.acceptance,
            device_id="cam02",
        )
        result = provider.query(START, END)
        self.assertEqual("unavailable", result.status)
        self.assertEqual("push_inbox_query_failed", result.failure_reason)


if __name__ == "__main__":
    unittest.main()
