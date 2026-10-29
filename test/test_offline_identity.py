from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.camera_face_api import CameraApiTransportError  # noqa: E402
from agent.camera_track_identity import TrackSnapshot  # noqa: E402
from agent.face_spatial_evidence import FaceSpatialEvidence  # noqa: E402
from agent.offline_identity import (  # noqa: E402
    HistoricalIdentityIntegrityError,
    HistoricalIdentityResolver,
    HistoricalIdentityRevisionService,
    HistoricalIdentityRevisionData,
    OfflineEventTrackAssociation,
    OfflineTrackIdentityAggregator,
)
from agent.offline_identity_sql import SqlAlchemyHistoricalIdentityRepository  # noqa: E402
from agent.offline_recognition_provider import (  # noqa: E402
    DeviceRecognitionAcceptancePolicy,
    OfflineRecognitionEvidence,
    PaginationCompletionPolicy,
    RecordedCameraRecognitionProvider,
    RecognitionRecordQueryResult,
)
from agent.offline_identity_association import (  # noqa: E402
    GuardedOfflineIdentityAssociator,
    OfflineSpatialGeometry,
    OfflineSpatialGeometryValidator,
    _coerce_track_snapshot,
    map_face_bbox_to_recording_frame,
)
from agent.offline_media import (  # noqa: E402
    P6SCapabilityProfile,
    P6SCapabilityProfileResolver,
)


class FaceSpatialGeometryTransformTests(unittest.TestCase):
    def test_track_snapshot_coercion_preserves_snapshot_input(self):
        request = SimpleNamespace(
            store_id="store-1", camera_id="cam02", analysis_run_id="run-1"
        )
        observed_at = datetime(2026, 8, 19, 9, 55, 22)
        track = TrackSnapshot(
            store_id="store-1", camera_id="cam02", runtime_id="run-1", track_id=1,
            track_token="store-1:cam02:run-1:1", observed_at=observed_at,
            bbox=(0.1, 0.2, 0.3, 0.8), confidence=0.9, role="customer",
            stable_frames=5, is_frame_clipped=False, frame_size=(800, 448),
        )
        self.assertIs(track, _coerce_track_snapshot(track, request))

    def test_track_snapshot_coercion_accepts_valid_dict(self):
        request = SimpleNamespace(
            store_id="store-1", camera_id="cam02", analysis_run_id="run-1"
        )
        observed_at = datetime(2026, 8, 19, 9, 55, 22)
        coerced = _coerce_track_snapshot({
            "track_id": 1, "track_token": "store-1:cam02:run-1:1",
            "observed_at": observed_at, "bbox": [0.1, 0.2, 0.3, 0.8],
            "confidence": 0.9, "role": "customer", "stable_frames": 5,
            "is_frame_clipped": False, "frame_size": [800, 448],
        }, request)
        self.assertIsNotNone(coerced)
        self.assertEqual("store-1:cam02:run-1:1", coerced.track_token)
        self.assertEqual(observed_at, coerced.observed_at)
    def test_verified_resize_mapping_is_deterministic(self):
        face = FaceSpatialEvidence(
            bbox=(0.4104166667, 0.3529411765, 0.5104166667, 0.5294117647),
            normalized=True, quality_status="parsed", source_frame_size=(1920, 1088),
        )
        geometry = OfflineSpatialGeometry(
            decoded_source_frame_size=(1920, 1088), analysis_frame_size=(800, 448),
            full_frame_resize_verified=True, cropped=False, mirrored=False,
            rotated=False, padded=False, transform_type="full_frame_resize",
            scale_x=800 / 1920, scale_y=448 / 1088,
        )
        mapped = map_face_bbox_to_recording_frame(face, geometry)
        self.assertIsNotNone(mapped)
        self.assertEqual((800, 448), mapped.source_frame_size)
        for actual, expected in zip(
            mapped.bbox, (0.4104166667, 0.3529411765, 0.5104166667, 0.5294117647)
        ):
            self.assertAlmostEqual(expected, actual, places=12)
        self.assertEqual(mapped, map_face_bbox_to_recording_frame(face, geometry))

    def test_unverified_transform_remains_unavailable(self):
        face = FaceSpatialEvidence(
            bbox=(0.2, 0.2, 0.3, 0.3), normalized=True,
            quality_status="parsed", source_frame_size=(1920, 1088),
        )
        geometry = OfflineSpatialGeometry(
            decoded_source_frame_size=(800, 448), analysis_frame_size=(800, 448),
            full_frame_resize_verified=True, cropped=False, mirrored=False,
            rotated=False, padded=False,
        )
        result = OfflineSpatialGeometryValidator().validate(face=face, geometry=geometry)
        self.assertEqual("unavailable", result.status)
        self.assertIsNone(map_face_bbox_to_recording_frame(face, geometry))

    def test_transform_metadata_mismatch_is_unavailable(self):
        face = FaceSpatialEvidence(
            bbox=(0.2, 0.2, 0.3, 0.3), normalized=True,
            quality_status="parsed", source_frame_size=(1920, 1088),
        )
        geometry = OfflineSpatialGeometry(
            decoded_source_frame_size=(1920, 1088), analysis_frame_size=(800, 448),
            full_frame_resize_verified=True, cropped=False, mirrored=False,
            rotated=False, padded=False, transform_type="full_frame_resize",
            scale_x=0.5, scale_y=448 / 1088,
        )
        result = OfflineSpatialGeometryValidator().validate(face=face, geometry=geometry)
        self.assertEqual("unavailable", result.status)
        self.assertEqual("transform_metadata_mismatch", result.reason)

    def test_guarded_association_uses_verified_resize_before_binder(self):
        when = datetime(2026, 8, 19, 9, 55, 22)
        evidence = OfflineRecognitionEvidence(
            evidence_id="e701", canonical_event_id="c701", device_id="cam02",
            occurred_at=when, member_id="member-gaojunjie", similarity=0.889,
            source="push", log_id=701, group_id=0, person_id=0, unique_id="face-701",
            face_spatial_evidence={
                "coordinate_space": "normalized_full_frame",
                "normalized_bbox": [0.4104166667, 0.3529411765, 0.5104166667, 0.5294117647],
                "source_frame_size": [1920, 1088], "quality_status": "parsed",
                "camera_id": "cam02", "event_time": when.isoformat(),
            },
        )
        query = RecognitionRecordQueryResult(
            status="complete", requested_start=when - timedelta(seconds=10),
            requested_end=when + timedelta(seconds=10), accepted_evidence=(evidence,),
            record_audits=(), pages_fetched=1, raw_record_count=1,
            partial_record_count=0, coverage_complete=True, failure_reason=None,
        )
        track = TrackSnapshot(
            store_id="store-1", camera_id="cam02", runtime_id="run-701", track_id=1,
            track_token="store-1:cam02:run-701:1", observed_at=when,
            bbox=(0.3646, 0.3682, 0.5213, 0.9221), confidence=0.91,
            role="customer", stable_frames=5, is_frame_clipped=False,
            frame_size=(800, 448),
        )
        result = GuardedOfflineIdentityAssociator().associate(
            recognition_result=query, recording_start=when - timedelta(seconds=10),
            recording_end=when + timedelta(seconds=10), presentation_timeline_status="verified",
            store_id="store-1", camera_id="cam02", analysis_run_id="run-701",
            tracks=(track,), spatial_geometry=OfflineSpatialGeometry(
                decoded_source_frame_size=(1920, 1088), analysis_frame_size=(800, 448),
                full_frame_resize_verified=True, cropped=False, mirrored=False,
                rotated=False, padded=False, transform_type="full_frame_resize",
                scale_x=800 / 1920, scale_y=448 / 1088,
            ),
        )
        self.assertEqual(("verified", "confirmed", "member-gaojunjie"),
                         (result[0].guard.status, result[0].status, result[0].member_id))


class HistoricalIdentityRevisionPersistenceTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from agent.models import Base, Member

        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.start = datetime(2026, 8, 17, 2, 0, 0)
        db = self.sessions()
        try:
            db.add_all((
                Member(id="member-a", name="Member A", is_active=True),
                Member(id="member-b", name="Member B", is_active=True),
            ))
            db.commit()
        finally:
            db.close()
        self.history = SqlAlchemyHistoricalIdentityRepository(
            self.sessions, history_available_from=self.start
        )
        self.service = HistoricalIdentityRevisionService(self.history)

    def tearDown(self):
        self.engine.dispose()

    def test_transition_closes_prior_revision_and_updates_current_projection_atomically(self):
        first = self._mapping("member-a")
        second = self._mapping("member-b")
        self.service.apply_mapping_revision(first, effective_from=self.start)
        self.service.apply_mapping_revision(
            second, effective_from=self.start + timedelta(minutes=1)
        )

        resolver = self.history.resolver()
        before = resolver.resolve_at(
            device_id="device-1", group_id=7, person_id=42,
            unique_id="person-42", occurred_at=self.start + timedelta(seconds=30),
        )
        after = resolver.resolve_at(
            device_id="device-1", group_id=7, person_id=42,
            unique_id="person-42", occurred_at=self.start + timedelta(minutes=1),
        )
        current = self.history.current_mapping(
            device_id="device-1", group_id=7, person_id=42, unique_id="person-42"
        )

        self.assertEqual(("resolved", "member-a"), (before.status, before.member_id))
        self.assertEqual(("resolved", "member-b"), (after.status, after.member_id))
        self.assertEqual("member-b", current.member_id)
        revisions = self.history.load_revisions()
        self.assertEqual(2, len(revisions))
        self.assertEqual(self.start + timedelta(minutes=1), revisions[0].effective_to)

    def test_missing_member_principal_rolls_back_without_creating_history_or_projection(self):
        with self.assertRaises(HistoricalIdentityIntegrityError):
            self.service.apply_mapping_revision(
                self._mapping("missing-member"), effective_from=self.start
            )

        self.assertEqual((), self.history.load_revisions())
        self.assertIsNone(
            self.history.current_mapping(
                device_id="device-1", group_id=7, person_id=42, unique_id="person-42"
            )
        )

    def test_history_does_not_claim_to_resolve_before_explicit_bootstrap(self):
        self.service.apply_mapping_revision(self._mapping("member-a"), effective_from=self.start)

        result = self.history.resolver().resolve_at(
            device_id="device-1", group_id=7, person_id=42,
            unique_id="person-42", occurred_at=self.start - timedelta(microseconds=1),
        )

        self.assertEqual(("unverifiable", "event_precedes_history_bootstrap"), (result.status, result.reason))

    def test_mapping_enabled_is_independent_from_synced_status(self):
        self.service.apply_mapping_revision(
            self._mapping("member-a", is_enabled=False, sync_status="synced"),
            effective_from=self.start,
        )

        revision = self.history.load_revisions()[0]
        resolved = self.history.resolver().resolve_at(
            device_id="device-1", group_id=7, person_id=42,
            unique_id="person-42", occurred_at=self.start,
        )
        current = self.history.current_mapping(
            device_id="device-1", group_id=7, person_id=42,
            unique_id="person-42",
        )

        self.assertFalse(revision.mapping_enabled)
        self.assertEqual("synced", revision.mapping_sync_status)
        self.assertEqual("unmapped", resolved.status)
        self.assertFalse(current.is_enabled)

    def test_member_active_transition_is_resolved_at_event_time(self):
        self.service.apply_mapping_revision(
            self._mapping("member-a"), effective_from=self.start
        )
        inactive_at = self.start + timedelta(minutes=1)
        self.service.apply_member_principal_revision(
            member_id="member-a", active=False, effective_from=inactive_at
        )
        resolver = self.history.resolver()

        active = resolver.resolve_at(
            device_id="device-1", group_id=7, person_id=42,
            unique_id="person-42", occurred_at=self.start + timedelta(seconds=30),
        )
        inactive = resolver.resolve_at(
            device_id="device-1", group_id=7, person_id=42,
            unique_id="person-42", occurred_at=inactive_at,
        )

        self.assertEqual(("resolved", "member-a"), (active.status, active.member_id))
        self.assertEqual(("unmapped", None), (inactive.status, inactive.member_id))

    @staticmethod
    def _mapping(member_id, *, is_enabled=True, sync_status="synced"):
        from agent.device_identity import DeviceMemberMappingData

        return DeviceMemberMappingData(
            device_id="device-1", member_id=member_id, group_id=7,
            group_uuid="group-7", person_id=42, unique_id="person-42",
            face_uuid="face-42", sync_status=sync_status, is_enabled=is_enabled,
        )
from agent.offline_identity_association import (  # noqa: E402
    ExactEventSpatialEnricher,
    GuardedOfflineIdentityAssociator,
    InboxSpatialEvidenceRecord,
    OfflineSameCameraIdentityAssociator,
    OfflineSpatialGeometry,
    OfflineSpatialGeometryValidator,
)


class OfflineIdentityContractTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 17, 2, 0, 0)
        self.identity = {
            "device_id": "device-1",
            "group_id": 7,
            "person_id": 42,
            "unique_id": "person-42",
        }

    def test_historical_resolver_uses_event_time_revision_not_current_mapping(self):
        resolver = HistoricalIdentityResolver(
            history_available_from=self.start,
            revisions=(
                HistoricalIdentityRevisionData(
                    revision_id="rev-a",
                    **self.identity,
                    member_id="member-a",
                    effective_from=self.start,
                    effective_to=self.start + timedelta(minutes=1),
                    mapping_enabled=True,
                    mapping_sync_status="synced",
                    member_principal_exists=True,
                    member_principal_active=True,
                ),
                HistoricalIdentityRevisionData(
                    revision_id="rev-b",
                    **self.identity,
                    member_id="member-b",
                    effective_from=self.start + timedelta(minutes=1),
                    effective_to=None,
                    mapping_enabled=True,
                    mapping_sync_status="synced",
                    member_principal_exists=True,
                    member_principal_active=True,
                ),
            ),
        )

        result = resolver.resolve_at(
            occurred_at=self.start + timedelta(minutes=2), **self.identity
        )
        before_bootstrap = resolver.resolve_at(
            occurred_at=self.start - timedelta(seconds=1), **self.identity
        )

        self.assertEqual(("resolved", "member-b"), (result.status, result.member_id))
        self.assertEqual(("unverifiable", None), (before_bootstrap.status, before_bootstrap.member_id))

    def test_historical_resolver_fails_closed_on_overlapping_members(self):
        resolver = HistoricalIdentityResolver(
            history_available_from=self.start,
            revisions=(
                HistoricalIdentityRevisionData(
                    revision_id="rev-a", **self.identity, member_id="member-a",
                    effective_from=self.start, effective_to=None,
                    mapping_enabled=True, mapping_sync_status="synced",
                    member_principal_exists=True, member_principal_active=True,
                ),
                HistoricalIdentityRevisionData(
                    revision_id="rev-b", **self.identity, member_id="member-b",
                    effective_from=self.start, effective_to=None,
                    mapping_enabled=True, mapping_sync_status="synced",
                    member_principal_exists=True, member_principal_active=True,
                ),
            ),
        )

        result = resolver.resolve_at(occurred_at=self.start, **self.identity)

        self.assertEqual("conflict", result.status)
        self.assertIsNone(result.member_id)

    def test_track_aggregator_never_votes_probable_to_confirmed_and_closes_conflicts(self):
        aggregator = OfflineTrackIdentityAggregator()
        identity = dict(
            analysis_run_id="run-1",
            track_token="store-1:cam-1:run-1:23",
            track_started_at=self.start,
            track_ended_at=self.start + timedelta(seconds=10),
        )

        probable = aggregator.aggregate(
            associations=(
                OfflineEventTrackAssociation(member_id="member-a", status="probable"),
                OfflineEventTrackAssociation(member_id="member-a", status="probable"),
            ),
            **identity,
        )
        confirmed = aggregator.aggregate(
            associations=(
                OfflineEventTrackAssociation(member_id="member-a", status="confirmed"),
                OfflineEventTrackAssociation(member_id="member-a", status="probable"),
                OfflineEventTrackAssociation(member_id=None, status="ambiguous"),
            ),
            **identity,
        )
        conflict = aggregator.aggregate(
            associations=(
                OfflineEventTrackAssociation(member_id="member-a", status="confirmed"),
                OfflineEventTrackAssociation(member_id="member-b", status="probable"),
            ),
            **identity,
        )

        self.assertEqual(("probable", "member-a"), (probable.status, probable.candidate_member_id))
        self.assertEqual(("confirmed", "member-a"), (confirmed.status, confirmed.member_id))
        self.assertEqual(("conflict", None), (conflict.status, conflict.member_id))
        self.assertEqual(self.start, confirmed.valid_from)
        self.assertEqual(self.start + timedelta(seconds=10), confirmed.valid_to)

    def test_unavailable_event_does_not_become_unmatched(self):
        association = OfflineTrackIdentityAggregator().aggregate(
            analysis_run_id="run-1",
            track_token="store-1:cam-1:run-1:23",
            track_started_at=self.start,
            track_ended_at=self.start + timedelta(seconds=10),
            associations=(OfflineEventTrackAssociation(status="unavailable", member_id=None),),
        )
        self.assertEqual("unavailable", association.status)
        self.assertIsNone(association.member_id)

    def test_recorded_provider_fetches_through_verified_eof_and_resolves_history(self):
        client = Mock()
        client.query_recognition_records.side_effect = [
            [self._record(log_id=1), self._record(log_id=2)],
            [],
        ]
        resolver = HistoricalIdentityResolver(
            history_available_from=self.start - timedelta(days=1),
            revisions=(
                HistoricalIdentityRevisionData(
                    revision_id="rev-a", **self.identity, member_id="member-a",
                    effective_from=self.start - timedelta(days=1), effective_to=None,
                    mapping_enabled=True, mapping_sync_status="synced",
                    member_principal_exists=True, member_principal_active=True,
                ),
            ),
        )
        provider = RecordedCameraRecognitionProvider(
            client=client,
            device_id="device-1",
            pagination_policy=PaginationCompletionPolicy(
                policy_version="paging-v1",
                status="verified",
                trust_class="production_verified",
                completion_mode="verified_short_page",
            ),
            acceptance_policy=DeviceRecognitionAcceptancePolicy.create(
                policy_id="device-recognition",
                policy_version="v1",
                similarity_threshold="0.75",
                accepted_rule_ids=(1,),
                accepted_pass_codes=(0,),
                require_reco_valid=True,
            ),
            historical_identity_resolver=resolver,
            recognition_timezone_offset_minutes=480,
            profile_resolution=self._profile_resolution(),
            page_size=2,
        )

        result = provider.query(self.start, self.start + timedelta(minutes=1))

        self.assertEqual("complete", result.status)
        self.assertEqual(2, result.pages_fetched)
        self.assertEqual(2, len(result.accepted_evidence))
        self.assertEqual([(2, False), (0, True)], [
            (item.records_count, item.eof_proven) for item in result.page_audits
        ])
        self.assertEqual({"member-a"}, {item.member_id for item in result.accepted_evidence})
        self.assertEqual([1, 2], [call.kwargs["page_num"] for call in client.query_recognition_records.call_args_list])
        self.assertEqual(self.start + timedelta(hours=8), client.query_recognition_records.call_args_list[0].args[0])

    def test_provider_a_capability_requires_verified_exact_device_profile(self):
        available = self._profile_resolution()
        unavailable = self._profile_resolution(
            trust_class="test_fixture", fingerprint_verified=True
        )

        self.assertIsNone(
            available.production_eligibility_reason(device_id="device-1")
        )
        self.assertEqual("unavailable", unavailable.status)
        self.assertEqual("profile_source_untrusted", unavailable.reason)

    def test_recorded_provider_discards_partial_records_after_page_failure(self):
        client = Mock()
        client.query_recognition_records.side_effect = [
            [self._record(log_id=1), self._record(log_id=2)],
            CameraApiTransportError("timeout", retryable=True),
        ]
        provider = self._provider(client)

        result = provider.query(self.start, self.start + timedelta(minutes=1))

        self.assertEqual("unavailable", result.status)
        self.assertEqual(0, len(result.accepted_evidence))
        self.assertEqual(2, result.partial_record_count)
        self.assertEqual("pagination_failed", result.failure_reason)
        self.assertEqual(1, len(result.page_audits))

    def test_explicit_metadata_pagination_does_not_use_short_page_eof(self):
        client = Mock()
        client.query_recognition_records.side_effect = [
            {
                "records": [self._record(log_id=1)],
                "has_next": True,
                "next_page": 2,
                "total_count": 2,
            },
            {
                "records": [self._record(log_id=2)],
                "has_next": False,
                "next_page": None,
                "total_count": 2,
            },
        ]

        result = self._provider(
            client, completion_mode="explicit_metadata"
        ).query(self.start, self.start + timedelta(minutes=1))

        self.assertEqual("complete", result.status)
        self.assertEqual(2, result.pages_fetched)
        self.assertFalse(result.page_audits[0].eof_proven)
        self.assertTrue(result.page_audits[1].eof_proven)

    def test_explicit_metadata_mode_rejects_plain_list_even_when_short(self):
        client = Mock()
        client.query_recognition_records.return_value = []

        result = self._provider(
            client, completion_mode="explicit_metadata"
        ).query(self.start, self.start + timedelta(minutes=1))

        self.assertEqual("unavailable", result.status)
        self.assertEqual("pagination_metadata_unavailable", result.failure_reason)

    def test_recorded_provider_without_real_device_capability_never_queries_records(self):
        client = Mock()
        provider = RecordedCameraRecognitionProvider(
            client=client, device_id="device-1",
            pagination_policy=PaginationCompletionPolicy(
                policy_version="paging-v1", status="verified",
                trust_class="production_verified", completion_mode="verified_short_page",
            ),
            acceptance_policy=DeviceRecognitionAcceptancePolicy.create(
                policy_id="device-recognition", policy_version="v1", similarity_threshold="0.75",
                accepted_rule_ids=(1,), accepted_pass_codes=(0,), require_reco_valid=True,
            ),
            historical_identity_resolver=HistoricalIdentityResolver(
                history_available_from=self.start, revisions=()
            ),
            recognition_timezone_offset_minutes=480,
        )

        result = provider.query(self.start, self.start + timedelta(seconds=1))

        self.assertEqual(("unavailable", "provider_capability_unavailable"), (result.status, result.failure_reason))
        client.query_recognition_records.assert_not_called()

    def test_low_similarity_is_rejected_without_breaking_coverage(self):
        client = Mock()
        client.query_recognition_records.return_value = [
            self._record(log_id=1, similarity=0.60)
        ]

        result = self._provider(client).query(
            self.start, self.start + timedelta(minutes=1)
        )

        self.assertEqual("complete", result.status)
        self.assertEqual(0, len(result.accepted_evidence))
        self.assertEqual("similarity_below_threshold", result.record_audits[0].reason)

    def test_record_outside_requested_interval_fails_whole_query_closed(self):
        client = Mock()
        record = self._record(log_id=1)
        record["occurred_at"] = self.start - timedelta(microseconds=1)
        client.query_recognition_records.return_value = [record]

        result = self._provider(client).query(
            self.start, self.start + timedelta(minutes=1)
        )

        self.assertEqual("unavailable", result.status)
        self.assertEqual("record_outside_requested_interval", result.failure_reason)
        self.assertEqual(0, len(result.accepted_evidence))

    def test_malformed_page_fails_whole_query_closed(self):
        client = Mock()
        malformed = self._record(log_id=1)
        malformed.pop("person_id")
        client.query_recognition_records.return_value = [malformed]

        result = self._provider(client).query(
            self.start, self.start + timedelta(minutes=1)
        )

        self.assertEqual("unavailable", result.status)
        self.assertEqual("response_integrity_failure", result.failure_reason)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(0, len(result.accepted_evidence))

    def test_missing_face_time_uses_recognition_effective_time_for_spatial_clock(self):
        evidence = self._provider_evidence()
        face = FaceSpatialEvidence(
            bbox=(0.20, 0.15, 0.30, 0.30),
            normalized=True,
            quality_status="parsed",
            source_frame_size=(1280, 720),
            camera_id="cam-1",
            occurred_at=None,
        )
        geometry = OfflineSpatialGeometryValidator().validate(
            face=face,
            geometry=OfflineSpatialGeometry(
                decoded_source_frame_size=(1280, 720),
                analysis_frame_size=(1280, 720),
                full_frame_resize_verified=True,
                cropped=False,
                mirrored=False,
                rotated=False,
                padded=False,
            ),
        )

        result = OfflineSameCameraIdentityAssociator().associate(
            evidence=evidence,
            store_id="store-1",
            camera_id="cam-1",
            analysis_run_id="run-1",
            tracks=(self._track(1, (0.10, 0.10, 0.50, 0.90)),),
            face=face,
            spatial_geometry=geometry,
        )

        self.assertEqual("confirmed", result.status)
        self.assertEqual(
            "recognition_effective_time",
            result.spatial_diagnostics[-1]["spatial_association_clock_source"],
        )

    def test_compatible_face_geometry_uniquely_confirms_primary_track(self):
        evidence = self._provider_evidence()
        face = FaceSpatialEvidence(
            bbox=(0.20, 0.15, 0.30, 0.30),
            normalized=True,
            quality_status="verified",
            source_frame_size=(1280, 720),
            camera_id="cam-1",
            occurred_at=evidence.occurred_at,
        )
        geometry = OfflineSpatialGeometryValidator().validate(
            face=face,
            geometry=OfflineSpatialGeometry(
                decoded_source_frame_size=(1280, 720),
                analysis_frame_size=(1280, 720),
                full_frame_resize_verified=True,
                cropped=False,
                mirrored=False,
                rotated=False,
                padded=False,
            ),
        )
        tracks = (
            self._track(1, (0.10, 0.10, 0.50, 0.90)),
            self._track(2, (0.60, 0.10, 0.95, 0.90)),
        )

        result = OfflineSameCameraIdentityAssociator().associate(
            evidence=evidence,
            store_id="store-1",
            camera_id="cam-1",
            analysis_run_id="run-1",
            tracks=tracks,
            face=face,
            spatial_geometry=geometry,
        )

        self.assertEqual(("confirmed", "member-a"), (result.status, result.member_id))
        self.assertEqual(tracks[0].track_token, result.track_token)

    def test_temporal_only_association_never_confirms(self):
        evidence = self._provider_evidence()
        associator = OfflineSameCameraIdentityAssociator()

        probable = associator.associate(
            evidence=evidence,
            store_id="store-1",
            camera_id="cam-1",
            analysis_run_id="run-1",
            tracks=(self._track(1, (0.10, 0.10, 0.50, 0.90)),),
            face=None,
            spatial_geometry=None,
        )
        ambiguous = associator.associate(
            evidence=evidence,
            store_id="store-1",
            camera_id="cam-1",
            analysis_run_id="run-1",
            tracks=(
                self._track(1, (0.10, 0.10, 0.50, 0.90)),
                self._track(2, (0.60, 0.10, 0.95, 0.90)),
            ),
            face=None,
            spatial_geometry=None,
        )

        self.assertEqual("probable", probable.status)
        self.assertEqual("ambiguous", ambiguous.status)

    def test_guard_verified_allows_association_and_keeps_incompatible_geometry_probable(self):
        evidence = self._provider_evidence()
        result = RecognitionRecordQueryResult(
            status="complete", requested_start=self.start,
            requested_end=self.start + timedelta(minutes=2),
            accepted_evidence=(evidence,), record_audits=(), pages_fetched=0,
            raw_record_count=1, partial_record_count=0, coverage_complete=True,
            failure_reason=None,
        )
        tracks = (self._track(1, (0.10, 0.10, 0.50, 0.90)),)
        guarded = GuardedOfflineIdentityAssociator().associate(
            recognition_result=result,
            recording_start=self.start - timedelta(seconds=10),
            recording_end=self.start + timedelta(seconds=20),
            presentation_timeline_status="verified",
            store_id="store-1", camera_id="cam-1", analysis_run_id="run-1",
            tracks=tracks,
            spatial_geometry=OfflineSpatialGeometry(
                decoded_source_frame_size=(800, 448),
                analysis_frame_size=(800, 448),
                full_frame_resize_verified=True,
                cropped=False, mirrored=False, rotated=False, padded=False,
            ),
        )
        self.assertEqual(1, len(guarded))
        self.assertEqual("verified", guarded[0].guard.status)
        self.assertEqual("probable", guarded[0].status)
        self.assertIsNone(guarded[0].member_id)
        self.assertEqual("member-a", guarded[0].association.candidate_member_id)

    def test_guard_failure_short_circuits_binder_and_is_unavailable(self):
        evidence = self._provider_evidence()
        result = RecognitionRecordQueryResult(
            status="complete", requested_start=self.start,
            requested_end=self.start + timedelta(minutes=2),
            accepted_evidence=(evidence,), record_audits=(), pages_fetched=0,
            raw_record_count=1, partial_record_count=0, coverage_complete=True,
            failure_reason=None,
        )

        class FailingAssociator:
            def associate(self, **_kwargs):
                raise AssertionError("binder must not run after guard failure")

        guarded = GuardedOfflineIdentityAssociator(associator=FailingAssociator()).associate(
            recognition_result=result,
            recording_start=self.start,
            recording_end=self.start + timedelta(seconds=30),
            presentation_timeline_status="verified",
            store_id="store-1", camera_id="cam-1", analysis_run_id="run-1",
            tracks=(self._track(1, (0.10, 0.10, 0.50, 0.90)),),
            spatial_geometry=None,
        )
        self.assertEqual("unavailable", guarded[0].status)
        self.assertEqual("recording_start_boundary_guard", guarded[0].reason)
        self.assertIsNone(guarded[0].track_token)

    def test_primary_snapshot_is_global_minimum_delta_before_geometry(self):
        evidence = self._provider_evidence()
        near = self._track(1, (0.60, 0.10, 0.95, 0.90))
        far = replace(near, observed_at=evidence.occurred_at + timedelta(seconds=0.4),
                      track_id=2, track_token="store-1:cam-1:run-1:2",
                      bbox=(0.10, 0.10, 0.50, 0.90))
        face = FaceSpatialEvidence(
            bbox=(0.20, 0.15, 0.30, 0.30), normalized=True,
            quality_status="verified", source_frame_size=(1280, 720),
            camera_id="cam-1", occurred_at=evidence.occurred_at,
        )
        geometry = OfflineSpatialGeometryValidator().validate(
            face=face,
            geometry=OfflineSpatialGeometry(
                decoded_source_frame_size=(1280, 720), analysis_frame_size=(1280, 720),
                full_frame_resize_verified=True, cropped=False, mirrored=False,
                rotated=False, padded=False,
            ),
        )
        result = OfflineSameCameraIdentityAssociator().associate(
            evidence=evidence, store_id="store-1", camera_id="cam-1",
            analysis_run_id="run-1", tracks=(near, far), face=face,
            spatial_geometry=geometry,
        )
        self.assertEqual("unmatched", result.status)
        self.assertEqual(0.0, result.spatial_diagnostics[-1]["primary_time_delta_seconds"])
        self.assertIsNone(result.track_token)

    def test_incompatible_spatial_geometry_never_confirms(self):
        evidence = self._provider_evidence()
        face = FaceSpatialEvidence(
            bbox=(0.20, 0.15, 0.30, 0.30), normalized=True,
            quality_status="verified", source_frame_size=(1920, 1088),
            camera_id="cam-1", occurred_at=evidence.occurred_at,
        )
        geometry = OfflineSpatialGeometryValidator().validate(
            face=face,
            geometry=OfflineSpatialGeometry(
                decoded_source_frame_size=(800, 448), analysis_frame_size=(800, 448),
                full_frame_resize_verified=True, cropped=False, mirrored=False,
                rotated=False, padded=False,
            ),
        )
        self.assertEqual("unavailable", geometry.status)
        result = OfflineSameCameraIdentityAssociator().associate(
            evidence=evidence, store_id="store-1", camera_id="cam-1",
            analysis_run_id="run-1", tracks=(self._track(1, (0.10, 0.10, 0.50, 0.90)),),
            face=face, spatial_geometry=geometry,
        )
        self.assertEqual("probable", result.status)
        self.assertIsNone(result.member_id)

    def test_inbox_spatial_enrichment_requires_exact_event_and_metadata(self):
        evidence = self._provider_evidence()
        face = FaceSpatialEvidence(
            bbox=(0.20, 0.15, 0.30, 0.30), normalized=True,
            quality_status="verified", source_frame_size=(1280, 720),
            camera_id="cam-1", occurred_at=evidence.occurred_at,
        )
        exact = InboxSpatialEvidenceRecord(
            canonical_event_id=evidence.canonical_event_id,
            device_id=evidence.device_id,
            occurred_at=evidence.occurred_at,
            group_id=evidence.group_id,
            person_id=evidence.person_id,
            unique_id=evidence.unique_id,
            face_spatial_evidence=face.to_safe_dict(),
        )
        mismatch = InboxSpatialEvidenceRecord(
            canonical_event_id=evidence.canonical_event_id,
            device_id="other-device",
            occurred_at=evidence.occurred_at,
            group_id=evidence.group_id,
            person_id=evidence.person_id,
            unique_id=evidence.unique_id,
            face_spatial_evidence=face.to_safe_dict(),
        )

        matched = ExactEventSpatialEnricher().enrich(
            evidence=evidence, records=(exact,), expected_camera_id="cam-1"
        )
        conflicted = ExactEventSpatialEnricher().enrich(
            evidence=evidence, records=(mismatch,), expected_camera_id="cam-1"
        )

        self.assertEqual("matched", matched.status)
        self.assertEqual(face.bbox, matched.face.bbox)
        self.assertEqual("conflict", conflicted.status)
        self.assertIsNone(conflicted.face)

    def _provider_evidence(self):
        client = Mock()
        client.query_recognition_records.return_value = [self._record(log_id=1)]
        result = self._provider(client).query(
            self.start, self.start + timedelta(minutes=1)
        )
        return result.accepted_evidence[0]

    def _track(self, track_id, bbox):
        return TrackSnapshot(
            store_id="store-1",
            camera_id="cam-1",
            runtime_id="run-1",
            track_id=track_id,
            track_token=f"store-1:cam-1:run-1:{track_id}",
            observed_at=self.start + timedelta(seconds=1),
            bbox=bbox,
            confidence=0.9,
            role="customer",
            stable_frames=5,
            is_frame_clipped=False,
            frame_size=(1280, 720),
        )

    def _provider(self, client, *, completion_mode="verified_short_page"):
        resolver = HistoricalIdentityResolver(
            history_available_from=self.start - timedelta(days=1),
            revisions=(
                HistoricalIdentityRevisionData(
                    revision_id="rev-a", **self.identity, member_id="member-a",
                    effective_from=self.start - timedelta(days=1), effective_to=None,
                    mapping_enabled=True, mapping_sync_status="synced",
                    member_principal_exists=True, member_principal_active=True,
                ),
            ),
        )
        return RecordedCameraRecognitionProvider(
            client=client,
            device_id="device-1",
            pagination_policy=PaginationCompletionPolicy(
                policy_version="paging-v1", status="verified",
                trust_class="production_verified", completion_mode=completion_mode,
            ),
            acceptance_policy=DeviceRecognitionAcceptancePolicy.create(
                policy_id="device-recognition", policy_version="v1",
                similarity_threshold="0.75", accepted_rule_ids=(1,),
                accepted_pass_codes=(0,), require_reco_valid=True,
            ),
            historical_identity_resolver=resolver,
            recognition_timezone_offset_minutes=480,
            profile_resolution=self._profile_resolution(
                completion_mode=completion_mode
            ),
            page_size=2,
        )

    @staticmethod
    def _profile_resolution(
        *,
        completion_mode="verified_short_page",
        trust_class="production_verified",
        fingerprint_verified=True,
    ):
        profile = P6SCapabilityProfile.create(
            profile_id=f"p6s-test-{completion_mode}",
            profile_version="v1",
            trust_class=trust_class,
            status="verified",
            device_model="P6S-Test",
            firmware_versions=("1.0",),
            api_versions=("v1",),
            pagination_completion_mode=completion_mode,
            pagination_policy_version="paging-v1",
            pagination_validation_report_ref="test:paging-contract",
            segment_duration_policy_id="duration-test",
            segment_duration_policy_version="duration-v1",
            segment_duration_tolerance_seconds="0.25",
            segment_duration_validation_report_ref="test:duration-contract",
            historical_mapping_capability_status="available",
            historical_mapping_validation_report_ref="test:history-contract",
            timezone_interpretation_mode="verified_device_offset",
            timezone_validation_report_ref="test:timezone-contract",
        )
        return P6SCapabilityProfileResolver((profile,)).resolve(
            device_id="device-1",
            device_model="P6S-Test",
            firmware_version="1.0",
            api_version="v1",
            fingerprint_source="test_verified_device_metadata",
            fingerprint_verified=fingerprint_verified,
        )

    def _record(self, *, log_id: int, similarity: float = 0.90):
        return {
            "log_id": log_id,
            "group_id": self.identity["group_id"],
            "person_id": self.identity["person_id"],
            "occurred_at": self.start + timedelta(seconds=log_id),
            "pass_code": 0,
            "similarity": similarity,
            "rule_id": 1,
            "reco_valid": True,
            "unique_id": self.identity["unique_id"],
        }


if __name__ == "__main__":
    unittest.main()
