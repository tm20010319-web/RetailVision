from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.offline_media import (  # noqa: E402
    AuthoritativePresentationTimeSampler,
    ClockSkewDiagnostics,
    ClockSkewSample,
    SegmentBoundaryAssessment,
    CapabilityProfileResolution,
    OfflineRecordingTimeNormalizer,
    OfflineEventTimeMapper,
    P6SCapabilityProfile,
    P6SCapabilityProfileResolver,
    P6SDeviceFingerprint,
    PresentationFrame,
    SegmentDurationAssessment,
    SegmentDurationPolicy,
    assess_segment_duration,
    validate_segment_duration,
    assess_segment_boundary,
    assess_adjacent_segment_boundary,
    assess_guarded_interior_event_time,
    segment_owns_event_time,
)
from agent.offline_source import (  # noqa: E402
    HistoricalRegistrationInterval,
    P6SRecordingSource,
    RegistrationScopeValidator,
    TrustedSourceVideoVerifier,
    UniqueP6SRecordingFileSelector,
)


class OfflineMediaContractTests(unittest.TestCase):
    def test_segment_boundary_separates_trailing_frame_from_duration_policy(self):
        result = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0",
            last_presentation_start_pts_seconds="119.96",
            last_presentation_end_pts_seconds="120.125",
            trailing_tolerance_seconds="0.2",
        )
        self.assertIsInstance(result, SegmentBoundaryAssessment)
        self.assertEqual("verified", result.status)
        self.assertAlmostEqual(-0.04, result.trailing_start_delta_seconds)
        self.assertAlmostEqual(0.125, result.trailing_end_delta_seconds)
        self.assertEqual("[start,end)", result.ownership)
        rejected = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0",
            last_presentation_start_pts_seconds="119.96",
            last_presentation_end_pts_seconds="120.125",
            trailing_tolerance_seconds="0.1",
        )
        self.assertEqual("rejected", rejected.status)
        self.assertEqual("trailing_boundary_outside_policy", rejected.reason)

    def test_segment_boundary_rejects_missing_or_non_monotonic_pts(self):
        unavailable = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0",
            last_presentation_start_pts_seconds="119.9",
            last_presentation_end_pts_seconds="120.0",
        )
        self.assertEqual("unavailable", unavailable.status)
        rejected = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0.1",
            last_presentation_start_pts_seconds="119.9",
            last_presentation_end_pts_seconds="119.8",
            trailing_tolerance_seconds="0.2",
        )
        self.assertEqual("rejected", rejected.status)
        self.assertEqual("presentation_pts_non_monotonic", rejected.reason)

    def test_segment_boundary_rejects_first_frame_offset_outside_start_policy(self):
        result = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0.04",
            last_presentation_start_pts_seconds="119.96",
            last_presentation_end_pts_seconds="120",
            trailing_tolerance_seconds="0.2",
        )
        self.assertEqual("rejected", result.status)
        self.assertEqual("leading_boundary_outside_policy", result.reason)

    def test_segment_boundary_distinguishes_crossing_frame_from_frame_after_end(self):
        crossing = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0",
            last_presentation_start_pts_seconds="119.96",
            last_presentation_end_pts_seconds="120.04",
            trailing_tolerance_seconds="0.1",
        )
        after_end = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0",
            last_presentation_start_pts_seconds="120.04",
            last_presentation_end_pts_seconds="120.08",
            trailing_tolerance_seconds="0.1",
        )
        discontinuous = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0",
            last_presentation_start_pts_seconds="119.96",
            last_presentation_end_pts_seconds="120.04",
            trailing_tolerance_seconds="0.1",
            presentation_timeline_status="unverified",
        )
        self.assertEqual("verified", crossing.status)
        self.assertEqual("rejected", after_end.status)
        self.assertEqual(
            "last_presentation_start_at_or_after_index_end", after_end.reason
        )
        self.assertEqual("rejected", discontinuous.status)
        self.assertEqual("presentation_timeline_unverified", discontinuous.reason)

    def test_adjacent_segment_boundary_reports_exact_gap_and_overlap(self):
        exact = assess_adjacent_segment_boundary(
            previous_index_start_seconds="0",
            previous_last_presentation_end_seconds="120",
            next_index_start_seconds="120",
            next_first_presentation_pts_seconds="0",
        )
        gap = assess_adjacent_segment_boundary(
            previous_index_start_seconds="0",
            previous_last_presentation_end_seconds="119.9",
            next_index_start_seconds="120",
            next_first_presentation_pts_seconds="0",
        )
        overlap = assess_adjacent_segment_boundary(
            previous_index_start_seconds="0",
            previous_last_presentation_end_seconds="120.1",
            next_index_start_seconds="120",
            next_first_presentation_pts_seconds="0",
        )
        self.assertEqual(("exact", 0.0), (exact.status, exact.boundary_delta_seconds))
        self.assertEqual(("gap", 0.1), (gap.status, gap.boundary_delta_seconds))
        self.assertEqual(("overlap", -0.1), (overlap.status, overlap.boundary_delta_seconds))

    def test_event_at_adjacent_boundary_belongs_only_to_next_segment(self):
        self.assertFalse(segment_owns_event_time(
            event_seconds="120", index_start_seconds="0", index_end_seconds="120"
        ))
        self.assertTrue(segment_owns_event_time(
            event_seconds="120", index_start_seconds="120", index_end_seconds="240"
        ))

    def test_half_open_segment_owns_start_and_times_before_end(self):
        for event_seconds in ("100", "100.001", "219.999"):
            with self.subTest(event_seconds=event_seconds):
                self.assertTrue(
                    segment_owns_event_time(
                        event_seconds=event_seconds,
                        index_start_seconds="100",
                        index_end_seconds="220",
                    )
                )

    def test_adjacent_media_overlap_does_not_duplicate_index_ownership(self):
        boundary = assess_adjacent_segment_boundary(
            previous_index_start_seconds="0",
            previous_last_presentation_end_seconds="120.2",
            next_index_start_seconds="120",
            next_first_presentation_pts_seconds="0",
        )

        self.assertEqual("overlap", boundary.status)
        self.assertFalse(
            segment_owns_event_time(
                event_seconds="120.1",
                index_start_seconds="0",
                index_end_seconds="120",
            )
        )
        self.assertTrue(
            segment_owns_event_time(
                event_seconds="120.1",
                index_start_seconds="120",
                index_end_seconds="240",
            )
        )

    def test_adjacent_media_gap_does_not_prove_event_frame_coverage(self):
        boundary = assess_adjacent_segment_boundary(
            previous_index_start_seconds="0",
            previous_last_presentation_end_seconds="119.8",
            next_index_start_seconds="120",
            next_first_presentation_pts_seconds="0",
        )
        previous = assess_segment_boundary(
            index_start_seconds="0",
            index_end_seconds="120",
            first_presentation_pts_seconds="0",
            last_presentation_start_pts_seconds="119.76",
            last_presentation_end_pts_seconds="119.8",
        )

        self.assertEqual("gap", boundary.status)
        self.assertTrue(
            segment_owns_event_time(
                event_seconds="119.9",
                index_start_seconds="0",
                index_end_seconds="120",
            )
        )
        self.assertEqual("unavailable", previous.status)
        self.assertEqual("trailing_boundary_policy_unverified", previous.reason)

    def test_guarded_interior_event_time_uses_a_half_open_five_second_interval(self):
        start = datetime(2026, 8, 19, 6, 0, 0)
        end = datetime(2026, 8, 19, 6, 2, 0)
        cases = (
            (-0.001, "unavailable", "recording_start_boundary_guard"),
            (0, "verified", None),
            (109.999, "verified", None),
            (110, "unavailable", "recording_end_boundary_guard"),
        )

        for seconds_from_interior_start, status, reason in cases:
            with self.subTest(seconds_from_interior_start=seconds_from_interior_start):
                result = assess_guarded_interior_event_time(
                    event_time=start + timedelta(seconds=5 + seconds_from_interior_start),
                    recording_start=start,
                    recording_end=end,
                    presentation_timeline_status="verified",
                )
                self.assertEqual((status, reason), (result.status, result.reason))
                self.assertEqual("p6s_guarded_interior_event_time", result.policy_id)
                self.assertEqual("p6s_guarded_interior_event_time_v1", result.policy_version)
                self.assertEqual(5.0, result.guard_seconds)

    def test_guarded_interior_event_time_fails_closed_at_recording_edges_and_empty_interval(self):
        start = datetime(2026, 8, 19, 6, 0, 0)
        end = datetime(2026, 8, 19, 6, 2, 0)

        at_start = assess_guarded_interior_event_time(
            event_time=start,
            recording_start=start,
            recording_end=end,
            presentation_timeline_status="verified",
        )
        at_end = assess_guarded_interior_event_time(
            event_time=end,
            recording_start=start,
            recording_end=end,
            presentation_timeline_status="verified",
        )
        empty = assess_guarded_interior_event_time(
            event_time=start + timedelta(seconds=5),
            recording_start=start,
            recording_end=start + timedelta(seconds=10),
            presentation_timeline_status="verified",
        )

        self.assertEqual("recording_start_boundary_guard", at_start.reason)
        self.assertEqual("event_outside_recording_interval", at_end.reason)
        self.assertEqual("guarded_interior_empty", empty.reason)

    def test_guarded_interior_event_time_requires_verified_presentation_timeline(self):
        start = datetime(2026, 8, 19, 6, 0, 0)
        for timeline_status in ("unverified", "pts_discontinuity"):
            with self.subTest(timeline_status=timeline_status):
                result = assess_guarded_interior_event_time(
                    event_time=start + timedelta(seconds=60),
                    recording_start=start,
                    recording_end=start + timedelta(seconds=120),
                    presentation_timeline_status=timeline_status,
                )
                self.assertEqual(
                    ("unavailable", "presentation_timeline_unverified"),
                    (result.status, result.reason),
                )

    def test_guarded_interior_real_event_fixtures_are_eligible(self):
        fixtures = (
            ("624", datetime(2026, 8, 19, 6, 35, 19), 121, 31),
            ("628", datetime(2026, 8, 19, 6, 39, 24), 122, 105),
            ("630", datetime(2026, 8, 19, 6, 41, 26), 120, 87),
        )

        for source_event_id, start, duration, source_pts in fixtures:
            with self.subTest(source_event_id=source_event_id):
                result = assess_guarded_interior_event_time(
                    event_time=start + timedelta(seconds=source_pts),
                    recording_start=start,
                    recording_end=start + timedelta(seconds=duration),
                    presentation_timeline_status="verified",
                )
                self.assertEqual(("verified", None), (result.status, result.reason))

    def test_clock_skew_diagnostics_are_deterministic_and_do_not_compensate_event_time(self):
        samples = (
            ClockSkewSample(
                "event-1",
                datetime(2026, 8, 17, 2, 0, 18, 700000),
                datetime(2026, 8, 17, 2, 0, 0),
            ),
            ClockSkewSample(
                "event-2",
                datetime(2026, 8, 17, 2, 1, 18, 900000),
                datetime(2026, 8, 17, 2, 1, 0),
            ),
        )
        result = ClockSkewDiagnostics.calculate(samples)

        self.assertEqual(2, result.sample_count)
        self.assertEqual(18.7, result.minimum_seconds)
        self.assertEqual(18.9, result.maximum_seconds)
        self.assertAlmostEqual(18.8, result.mean_seconds)
        self.assertAlmostEqual(18.8, result.median_seconds)
        self.assertAlmostEqual(0.1, result.population_stddev_seconds)
        self.assertEqual(18.7, samples[0].delta_seconds)

    def test_sampler_uses_normalized_pts_and_never_reuses_a_source_frame(self):
        sampler = AuthoritativePresentationTimeSampler(analysis_fps=5.0)

        result = sampler.sample(
            (
                PresentationFrame(source_seq=10, raw_pts=100, duration_ticks=2),
                PresentationFrame(source_seq=11, raw_pts=102, duration_ticks=2),
                PresentationFrame(source_seq=12, raw_pts=113, duration_ticks=2),
            ),
            presentation_origin_pts=100,
            time_base=Fraction(1, 10),
            presentation_continuity_verified=True,
        )

        self.assertEqual([0, 1, 2], [item.sample_seq for item in result.samples])
        self.assertEqual([10, 11, 12], [item.source_seq for item in result.samples])
        self.assertEqual([0.0, 0.2, 1.3], [item.source_pts_seconds for item in result.samples])
        self.assertEqual([3, 4, 5, 6], [gap.target_k for gap in result.gaps])
        self.assertEqual(1.5, result.media_duration_seconds)

    def test_sampler_rejects_missing_or_regressing_presentation_timestamps(self):
        sampler = AuthoritativePresentationTimeSampler(analysis_fps=5.0)

        with self.assertRaisesRegex(ValueError, "pts_missing"):
            sampler.sample(
                (PresentationFrame(source_seq=1, raw_pts=None, duration_ticks=1),),
                presentation_origin_pts=0,
                time_base=Fraction(1, 25),
            )

        with self.assertRaisesRegex(ValueError, "timeline_discontinuity"):
            sampler.sample(
                (
                    PresentationFrame(source_seq=1, raw_pts=0, duration_ticks=1),
                    PresentationFrame(source_seq=2, raw_pts=5, duration_ticks=1),
                ),
                presentation_origin_pts=0,
                time_base=Fraction(1, 10),
            )

        with self.assertRaisesRegex(ValueError, "pts_regression"):
            sampler.sample(
                (
                    PresentationFrame(source_seq=1, raw_pts=10, duration_ticks=1),
                    PresentationFrame(source_seq=2, raw_pts=9, duration_ticks=1),
                ),
                presentation_origin_pts=10,
                time_base=Fraction(1, 25),
            )

    def test_recording_local_time_is_normalized_to_utc_naive(self):
        normalizer = OfflineRecordingTimeNormalizer()

        result = normalizer.normalize(
            raw_started_at=datetime(2026, 8, 17, 10, 0, 0),
            raw_ended_at=datetime(2026, 8, 17, 10, 1, 0),
            timezone_offset_minutes=480,
            timezone_source="device_registration",
            timezone_verified=True,
        )

        self.assertEqual(datetime(2026, 8, 17, 2, 0, 0), result.started_at_utc)
        self.assertEqual(datetime(2026, 8, 17, 2, 1, 0), result.ended_at_utc)
        self.assertEqual("utc_naive", result.event_time_standard)

    def test_absolute_event_time_is_recording_start_plus_authoritative_source_pts(self):
        mapped = OfflineEventTimeMapper().map_source_pts(
            normalized_video_started_at_utc=datetime(2026, 8, 17, 2),
            source_pts_seconds=35.4,
            source_seq=12,
        )

        self.assertEqual(datetime(2026, 8, 17, 2, 0, 35, 400000), mapped.observed_at_utc)

    def test_capability_profile_requires_production_trust_and_exact_scope(self):
        profile = P6SCapabilityProfile.create(
            profile_id="p6s-x",
            profile_version="1",
            trust_class="production_verified",
            status="verified",
            device_model="P6S-X",
            firmware_versions=("3.2.1",),
            api_versions=("v1",),
            pagination_completion_mode="verified_short_page",
            pagination_policy_version="p1",
            pagination_validation_report_ref="report:paging:sha256:abc",
            segment_duration_policy_id="duration-p6s-x",
            segment_duration_policy_version="d1",
            segment_duration_tolerance_seconds="0.250000",
            segment_duration_validation_report_ref="report:duration:sha256:def",
            historical_mapping_capability_status="available",
            historical_mapping_validation_report_ref="report:history:sha256:jkl",
            timezone_interpretation_mode="device_local_wall_clock",
            timezone_validation_report_ref="report:timezone:sha256:ghi",
        )
        resolver = P6SCapabilityProfileResolver((profile,))

        resolved = resolver.resolve(
            device_id="device-1", device_model="P6S-X",
            firmware_version="3.2.1", api_version="v1",
            fingerprint_source="verified_device_metadata", fingerprint_verified=True,
            device_serial="SN-1", fingerprint_retrieved_at="2026-08-19T08:00:00Z",
            fingerprint_metadata_endpoint="/System/DeviceInfo",
        )
        unavailable = resolver.resolve(
            device_id="device-1", device_model="P6S-X",
            firmware_version="3.2.2", api_version="v1",
            fingerprint_source="verified_device_metadata", fingerprint_verified=True,
        )
        fake = CapabilityProfileResolution(
            "resolved", profile, None,
            P6SDeviceFingerprint(
                "device-1", "P6S-X", "3.2.1", "v1",
                "verified_device_metadata", True,
            ),
            "v1:sha256:" + "a" * 64,
        )

        self.assertEqual("resolved", resolved.status)
        self.assertEqual(profile.profile_content_hash, resolved.profile.profile_content_hash)
        self.assertEqual("SN-1", resolved.profile_snapshot()["device_serial"])
        self.assertEqual(
            "/System/DeviceInfo",
            resolved.profile_snapshot()["fingerprint_metadata_endpoint"],
        )
        self.assertEqual("unavailable", unavailable.status)
        self.assertEqual("firmware_scope_mismatch", unavailable.reason)
        self.assertEqual(
            "profile_resolution_attestation_invalid",
            fake.production_eligibility_reason(device_id="device-1"),
        )
        self.assertEqual(
            "profile_device_scope_mismatch",
            resolved.production_eligibility_reason(device_id="device-2"),
        )

    def test_durable_push_profile_resolves_without_historical_pagination(self):
        profile = P6SCapabilityProfile.create(
            profile_id="p6s_cam02_offline_identity", profile_version="v1",
            trust_class="production_verified", status="verified",
            device_model="P6S-CAM02", firmware_versions=("fw-observed-20260819",),
            api_versions=("p6s-http-v1",),
            pagination_completion_mode="unsupported", pagination_policy_version="unsupported",
            pagination_validation_report_ref="report:p6s-pagination:unsupported",
            segment_duration_policy_id="duration-p6s-cam02",
            segment_duration_policy_version="duration-v1",
            segment_duration_tolerance_seconds="0.250",
            segment_duration_validation_report_ref="report:duration:not-verified",
            historical_mapping_capability_status="available",
            historical_mapping_validation_report_ref="P6S_DURABLE_PUSH_INBOX_PROVIDER_VALIDATION.md",
            timezone_interpretation_mode="verified_device_offset",
            timezone_validation_report_ref="P6S_RECORDING_TIME_REAL_DEVICE_VALIDATION.md",
            recognition_evidence_mode="durable_push_inbox", historical_query_status="unsupported",
            push_coverage_policy_id="p6s_push_coverage_v1", push_coverage_policy_version="p6s_push_coverage_v1",
            push_coverage_validation_report_ref="P6S_DURABLE_PUSH_COVERAGE_VALIDATION.md", push_coverage_status="verified",
            full_segment_boundary_status="unverified", event_time_mode="guarded_interior",
            event_time_policy_id="p6s_guarded_interior_event_time", event_time_policy_version="p6s_guarded_interior_event_time_v1",
            boundary_guard_seconds="5.0", event_time_validation_report_ref="P6S_GUARDED_INTERIOR_EVENT_TIME_VALIDATION.md",
            timezone_offset_minutes=480,
            face_recording_geometry_policy_id="p6s_face_recording_geometry", face_recording_geometry_policy_version="p6s_face_recording_geometry_v1",
            face_recording_geometry_status="verified", face_recording_geometry_validation_report_ref="P6S_FACE_RECORDING_SPATIAL_GEOMETRY_VALIDATION.md",
            face_recording_source_frame_size=(1920, 1088), face_recording_target_frame_size=(800, 448),
            face_recording_scale_x="0.4166666666666667", face_recording_scale_y="0.4117647058823529",
            face_recording_offset_x="0", face_recording_offset_y="0", face_recording_crop="none",
            face_recording_padding="none", face_recording_mirror=False, face_recording_rotation=0,
            recognition_acceptance_policy_id="device-recognition", recognition_acceptance_policy_version="v1",
            recognition_acceptance_policy_hash="v1:sha256:" + "a" * 64, recognition_similarity_threshold="0.75",
        )
        self.assertTrue(profile.verifies_own_hash())
        resolution = P6SCapabilityProfileResolver((profile,)).resolve(
            device_id="cam02", device_model="P6S-CAM02", firmware_version="fw-observed-20260819",
            api_version="p6s-http-v1", fingerprint_source="verified_device_metadata",
            fingerprint_verified=True,
        )
        self.assertEqual("resolved", resolution.status)
        self.assertIsNone(resolution.production_eligibility_reason(device_id="cam02"))
        self.assertEqual("guard_seconds_mismatch", resolution.event_time_policy_reason(mode="guarded_interior", guard_seconds=3.0))
        self.assertEqual("face_recording_geometry_runtime_mismatch", resolution.runtime_geometry_reason(source_frame_size=(1920, 1088), target_frame_size=(2560, 1440)))
        drift = P6SCapabilityProfileResolver((profile,)).resolve(
            device_id="cam02", device_model="P6S-CAM02", firmware_version="fw-other",
            api_version="p6s-http-v1", fingerprint_source="verified_device_metadata",
            fingerprint_verified=True,
        )
        self.assertEqual(("unavailable", "firmware_scope_mismatch"), (drift.status, drift.reason))

        wrong_model = P6SCapabilityProfileResolver((profile,)).resolve(
            device_id="cam02", device_model="other-model", firmware_version="fw-observed-20260819",
            api_version="p6s-http-v1", fingerprint_source="verified_device_metadata",
            fingerprint_verified=True,
        )
        wrong_api = P6SCapabilityProfileResolver((profile,)).resolve(
            device_id="cam02", device_model="P6S-CAM02", firmware_version="fw-observed-20260819",
            api_version="p6s-http-v2", fingerprint_source="verified_device_metadata",
            fingerprint_verified=True,
        )
        tampered = replace(profile, profile_content_hash="v1:sha256:" + "b" * 64)
        tampered_resolution = P6SCapabilityProfileResolver((tampered,)).resolve(
            device_id="cam02", device_model="P6S-CAM02", firmware_version="fw-observed-20260819",
            api_version="p6s-http-v1", fingerprint_source="verified_device_metadata",
            fingerprint_verified=True,
        )
        self.assertEqual(("unavailable", "profile_not_found"), (wrong_model.status, wrong_model.reason))
        self.assertEqual(("unavailable", "api_scope_mismatch"), (wrong_api.status, wrong_api.reason))
        self.assertEqual(("unavailable", "profile_hash_invalid"), (tampered_resolution.status, tampered_resolution.reason))

    def test_fingerprint_resolution_requires_every_authenticated_metadata_field(self):
        profile = P6SCapabilityProfile.create(
            profile_id="p6s-fingerprint", profile_version="v1",
            trust_class="production_verified", status="verified",
            device_model="P6S-X", firmware_versions=("3.2.1",), api_versions=("v1",),
            pagination_completion_mode="verified_short_page", pagination_policy_version="p1",
            pagination_validation_report_ref="report:paging", segment_duration_policy_id="duration",
            segment_duration_policy_version="d1", segment_duration_tolerance_seconds="0.25",
            segment_duration_validation_report_ref="report:duration",
            historical_mapping_capability_status="available",
            historical_mapping_validation_report_ref="report:history",
            timezone_interpretation_mode="device_local_wall_clock",
            timezone_validation_report_ref="report:timezone",
        )
        resolver = P6SCapabilityProfileResolver((profile,))
        common = {
            "device_id": "cam02", "device_model": "P6S-X",
            "firmware_version": "3.2.1", "api_version": "v1",
            "fingerprint_source": "authenticated_p6s_metadata",
            "fingerprint_verified": True,
        }

        cases = (
            ("device_model", None, "device_model_unknown"),
            ("firmware_version", None, "firmware_unknown"),
            ("api_version", None, "device_reported_api_version_unknown"),
            ("fingerprint_source", None, "device_fingerprint_unverified"),
            ("fingerprint_verified", False, "device_fingerprint_unverified"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                values = dict(common)
                values[field] = value
                resolution = resolver.resolve(**values)
                self.assertEqual(("unavailable", reason), (resolution.status, resolution.reason))

        first = resolver.resolve(
            **common, device_serial="SN-CAM02",
            fingerprint_retrieved_at="2026-08-19T11:55:32Z",
            fingerprint_metadata_endpoint="/System/DeviceInfo",
        )
        second = resolver.resolve(
            **common, device_serial="SN-CAM02",
            fingerprint_retrieved_at="2026-08-19T11:55:32Z",
            fingerprint_metadata_endpoint="/System/DeviceInfo",
        )
        self.assertEqual(first.resolution_attestation, second.resolution_attestation)
        self.assertEqual("SN-CAM02", first.profile_snapshot()["device_serial"])
        self.assertTrue(first.profile_snapshot()["fingerprint_verified"])

    def test_scoped_profile_resolves_without_device_reported_api_version(self):
        profile = P6SCapabilityProfile.create(
            profile_id="p6s_cam02_offline_identity", profile_version="v1",
            trust_class="production_verified", status="verified",
            device_model="T5AI-W/IPG-5950PCS-AI",
            device_serial="I20F11AB92E67AA9FE29D3D",
            firmware_versions=("3.4.0-20260430-EN",), api_versions=(),
            runtime_protocol_contract="p6s-http-v1",
            pagination_completion_mode="unsupported", pagination_policy_version="unsupported",
            pagination_validation_report_ref="report:paging:unsupported",
            segment_duration_policy_id="duration-p6s-cam02", segment_duration_policy_version="duration-v1",
            segment_duration_tolerance_seconds="0.250", segment_duration_validation_report_ref="report:duration",
            historical_mapping_capability_status="available", historical_mapping_validation_report_ref="report:history",
            timezone_interpretation_mode="verified_device_offset", timezone_validation_report_ref="report:timezone",
            recognition_evidence_mode="durable_push_inbox", historical_query_status="unsupported",
            push_coverage_policy_id="p6s_push_coverage", push_coverage_policy_version="p6s_push_coverage_v1",
            push_coverage_validation_report_ref="report:coverage", push_coverage_status="verified",
            full_segment_boundary_status="unverified", event_time_mode="guarded_interior",
            event_time_policy_id="p6s_guarded_interior_event_time", event_time_policy_version="p6s_guarded_interior_event_time_v1",
            boundary_guard_seconds="5.0", event_time_validation_report_ref="report:time", timezone_offset_minutes=480,
            face_recording_geometry_policy_id="p6s_face_recording_geometry", face_recording_geometry_policy_version="p6s_face_recording_geometry_v1",
            face_recording_geometry_status="verified", face_recording_geometry_validation_report_ref="report:geometry",
            face_recording_source_frame_size=(1920, 1088), face_recording_target_frame_size=(800, 448),
            face_recording_scale_x="0.4166666666666667", face_recording_scale_y="0.4117647058823529",
            face_recording_offset_x="0", face_recording_offset_y="0", face_recording_crop="none",
            face_recording_padding="none", face_recording_mirror=False, face_recording_rotation=0,
            recognition_acceptance_policy_id="device-recognition", recognition_acceptance_policy_version="v1",
            recognition_acceptance_policy_hash="v1:sha256:" + "a" * 64, recognition_similarity_threshold="0.75",
        )
        resolver = P6SCapabilityProfileResolver((profile,))
        resolved = resolver.resolve(
            device_id="cam02", device_serial="I20F11AB92E67AA9FE29D3D",
            device_model="T5AI-W/IPG-5950PCS-AI", firmware_version="3.4.0-20260430-EN",
            api_version=None, runtime_protocol_contract="p6s-http-v1",
            fingerprint_source="device_push_metadata", fingerprint_verified=True,
            fingerprint_retrieved_at="2026-08-19T12:30:25Z",
            fingerprint_metadata_endpoint="POST /api/v1/event/pull",
        )
        self.assertEqual("resolved", resolved.status)
        self.assertIsNone(resolved.production_eligibility_reason(device_id="cam02"))
        self.assertEqual("I20F11AB92E67AA9FE29D3D", resolved.profile_snapshot()["device_serial"])
        self.assertIsNone(resolved.profile_snapshot()["device_reported_api_version"])
        self.assertEqual("p6s-http-v1", resolved.profile_snapshot()["runtime_protocol_contract"])

        for kwargs, reason in (
            ({"device_serial": "OTHER"}, "serial_scope_mismatch"),
            ({"firmware_version": "3.4.1"}, "firmware_scope_mismatch"),
            ({"runtime_protocol_contract": "p6s-http-v2"}, "runtime_protocol_scope_mismatch"),
            ({"device_serial": None}, "device_serial_unknown"),
            ({"fingerprint_retrieved_at": None}, "device_push_metadata_provenance_unavailable"),
        ):
            values = {
                "device_id": "cam02", "device_serial": "I20F11AB92E67AA9FE29D3D",
                "device_model": "T5AI-W/IPG-5950PCS-AI", "firmware_version": "3.4.0-20260430-EN",
                "api_version": None, "runtime_protocol_contract": "p6s-http-v1",
                "fingerprint_source": "device_push_metadata", "fingerprint_verified": True,
                "fingerprint_retrieved_at": "2026-08-19T12:30:25Z",
                "fingerprint_metadata_endpoint": "POST /api/v1/event/pull",
            }
            values.update(kwargs)
            result = resolver.resolve(**values)
            self.assertEqual(("unavailable", reason), (result.status, result.reason))

    def test_segment_duration_requires_a_verified_policy(self):
        unverified = SegmentDurationPolicy(
            policy_id="duration-p6s-x",
            policy_version="d0",
            status="unverified",
            tolerance_seconds=None,
            validation_ref=None,
        )
        verified = SegmentDurationPolicy(
            policy_id="duration-p6s-x",
            policy_version="d1",
            status="verified",
            tolerance_seconds="0.250000",
            validation_ref="report:duration:sha256:def",
        )

        unavailable = validate_segment_duration(
                index_duration_seconds="60.0",
                media_duration_seconds="60.1",
                policy=unverified,
                source_frame_starts=("0.0", "59.9"),
            )
        compatible = validate_segment_duration(
                index_duration_seconds="60.0",
                media_duration_seconds="60.1",
                policy=verified,
                source_frame_starts=("0.0", "59.9"),
            )
        self.assertEqual("unavailable", unavailable.status)
        self.assertEqual((60.0, 60.1, None), (
            unavailable.index_duration_seconds,
            unavailable.media_duration_seconds,
            unavailable.duration_delta_seconds,
        ))
        self.assertEqual("compatible", compatible.status)
        self.assertAlmostEqual(0.1, compatible.duration_delta_seconds)
        self.assertEqual(
            "compatible",
            validate_segment_duration(
                index_duration_seconds="60.0",
                media_duration_seconds="60.1",
                policy=verified,
                source_frame_starts=("0.0", "60.0"),
            ).status,
        )

    def test_unavailable_profile_keeps_measured_durations_but_not_delta(self):
        result = assess_segment_duration(
            device_id="device-1",
            index_duration_seconds="60.0",
            media_duration_seconds="60.1",
            source_frame_starts=("0.0", "59.9"),
            profile_resolution=CapabilityProfileResolution(
                "unavailable", None, "real_device_not_verified"
            ),
            presentation_timeline_status="verified",
        )

        self.assertEqual("unavailable", result.status)
        self.assertEqual((60.0, 60.1, None), (
            result.index_duration_seconds,
            result.media_duration_seconds,
            result.duration_delta_seconds,
        ))
        self.assertEqual("device_recording_segment", result.index_duration_source)
        self.assertEqual("last_presentation_frame_end", result.media_duration_source)

    def test_trusted_source_requires_controlled_download_and_matching_size(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "record.mp4"
            source.write_bytes(b"p6s-recording-bytes")
            recording = P6SRecordingSource(
                device_id="device-1",
                path_name="/record/20260817",
                file_name="segment.mp4",
                raw_started_at=datetime(2026, 8, 17, 10, 0, 0),
                raw_ended_at=datetime(2026, 8, 17, 10, 1, 0),
                indexed_size_bytes=len(b"p6s-recording-bytes"),
            )

            result = TrustedSourceVideoVerifier().verify(
                recording=recording,
                downloaded_path=source,
                controlled_download=True,
            )

            self.assertEqual("verified", result.status)
            self.assertTrue(result.source_video_id.startswith("p6s:v1:sha256:"))
            self.assertEqual(len(b"p6s-recording-bytes"), result.downloaded_size_bytes)
            self.assertEqual(result, TrustedSourceVideoVerifier().verify(
                recording=recording,
                downloaded_path=source,
                controlled_download=True,
            ))

            source.write_bytes(b"modified")
            self.assertEqual("modified", TrustedSourceVideoVerifier().verify_analysis_input(
                trusted_source=result, analysis_input_path=source
            ).status)

    def test_recording_index_selection_never_guesses_between_multiple_files(self):
        selector = UniqueP6SRecordingFileSelector()
        recording = P6SRecordingSource(
            device_id="device-1", path_name="records", file_name="segment.mp4",
            raw_started_at=datetime(2026, 8, 17, 10),
            raw_ended_at=datetime(2026, 8, 17, 10, 1), indexed_size_bytes=1,
        )

        self.assertEqual("unavailable", selector.select(()).status)
        self.assertEqual("verified", selector.select((recording,)).status)
        self.assertEqual("conflict", selector.select((recording, recording)).status)

    def test_registration_scope_requires_continuous_unchanged_mapping(self):
        start = datetime(2026, 8, 17, 2, 0, 0)
        middle = datetime(2026, 8, 17, 2, 0, 30)
        end = datetime(2026, 8, 17, 2, 1, 0)
        validator = RegistrationScopeValidator()

        result = validator.validate(
            video_started_at=start,
            video_ended_at=end,
            requested_camera_id="cam-1",
            requested_store_id="store-1",
            device_camera_intervals=(
                HistoricalRegistrationInterval(start, middle, "cam-1"),
                HistoricalRegistrationInterval(middle, None, "cam-1"),
            ),
            camera_store_intervals=(
                HistoricalRegistrationInterval(start, None, "store-1"),
            ),
        )

        self.assertEqual("available", result.status)
        self.assertEqual(
            "unavailable",
            validator.validate(
                video_started_at=start,
                video_ended_at=end,
                requested_camera_id="cam-1",
                requested_store_id="store-1",
                device_camera_intervals=(
                    HistoricalRegistrationInterval(start, middle, "cam-1"),
                    HistoricalRegistrationInterval(
                        middle, None, "cam-2"
                    ),
                ),
                camera_store_intervals=(
                    HistoricalRegistrationInterval(start, None, "store-1"),
                ),
            ).status,
        )
        conflict = validator.validate(
            video_started_at=start,
            video_ended_at=end,
            requested_camera_id="cam-1",
            requested_store_id="store-1",
            device_camera_intervals=(
                HistoricalRegistrationInterval(start, end, "cam-1"),
                HistoricalRegistrationInterval(start, end, "cam-2"),
            ),
            camera_store_intervals=(
                HistoricalRegistrationInterval(start, end, "store-1"),
            ),
        )
        self.assertEqual(
            ("unavailable", "registration_conflict"),
            (conflict.status, conflict.reason),
        )
        conflict = validator.validate(
            video_started_at=start,
            video_ended_at=end,
            requested_camera_id="cam-1",
            requested_store_id="store-1",
            device_camera_intervals=(
                HistoricalRegistrationInterval(start, end, "cam-1"),
                HistoricalRegistrationInterval(start, end, "cam-2"),
            ),
            camera_store_intervals=(
                HistoricalRegistrationInterval(start, end, "store-1"),
            ),
        )
        self.assertEqual(
            ("unavailable", "registration_conflict"),
            (conflict.status, conflict.reason),
        )


if __name__ == "__main__":
    unittest.main()
