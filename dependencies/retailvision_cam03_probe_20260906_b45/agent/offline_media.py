"""Offline media-time and verified P6S capability contracts.

All datetimes exposed by this module are naive values whose explicit semantic
meaning is UTC.  The module deliberately does not provide a frame-index or
nominal-FPS fallback for authoritative identity or publication clocks.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Iterable, Optional


_PROFILE_HASH_PREFIX = "v1:sha256:"


@dataclass(frozen=True)
class PresentationFrame:
    """One decoded source frame in verified presentation order."""

    source_seq: int
    raw_pts: Optional[int]
    duration_ticks: Optional[int]


@dataclass(frozen=True)
class OfflineSample:
    sample_seq: int
    source_seq: int
    target_k: int
    target_pts_seconds: float
    raw_pts: int
    source_pts_seconds: float
    sampling_delta_seconds: float


@dataclass(frozen=True)
class SamplingGap:
    target_k: int
    target_pts_seconds: float
    reason: str


@dataclass(frozen=True)
class SamplingResult:
    samples: tuple[OfflineSample, ...]
    gaps: tuple[SamplingGap, ...]
    media_duration_seconds: float


@dataclass(frozen=True)
class OfflineObservationEventTime:
    source_seq: int
    source_pts_seconds: float
    observed_at_utc: datetime
    event_time_standard: str = "utc_naive"


@dataclass(frozen=True)
class ClockSkewSample:
    """Transport/clock diagnostic; it never changes the event-time authority."""

    source_event_id: str
    device_event_at: datetime
    receiver_arrived_at: datetime

    @property
    def delta_seconds(self) -> float:
        return (self.device_event_at - self.receiver_arrived_at).total_seconds()


@dataclass(frozen=True)
class ClockSkewDiagnostics:
    """Deterministic statistics for a set of Push clock-delta samples."""

    sample_count: int
    minimum_seconds: float
    maximum_seconds: float
    mean_seconds: float
    median_seconds: float
    population_stddev_seconds: float

    @classmethod
    def calculate(cls, samples: Iterable[ClockSkewSample]) -> "ClockSkewDiagnostics":
        deltas: list[float] = []
        for sample in samples:
            if not isinstance(sample, ClockSkewSample):
                raise ValueError("clock_skew_sample_invalid")
            for value in (sample.device_event_at, sample.receiver_arrived_at):
                if not isinstance(value, datetime) or value.tzinfo is not None:
                    raise ValueError("clock_skew_time_must_be_utc_naive")
            deltas.append(sample.delta_seconds)
        if not deltas:
            raise ValueError("clock_skew_samples_missing")
        return cls(
            sample_count=len(deltas),
            minimum_seconds=min(deltas),
            maximum_seconds=max(deltas),
            mean_seconds=statistics.fmean(deltas),
            median_seconds=statistics.median(deltas),
            population_stddev_seconds=statistics.pstdev(deltas),
        )


class OfflineEventTimeMapper:
    """Map authoritative media Presentation Time onto absolute UTC Event Time."""

    def map_source_pts(
        self, *, normalized_video_started_at_utc: datetime, source_pts_seconds: float,
        source_seq: int,
    ) -> OfflineObservationEventTime:
        if (
            not isinstance(normalized_video_started_at_utc, datetime)
            or normalized_video_started_at_utc.tzinfo is not None
        ):
            raise ValueError("video_started_at_must_be_utc_naive")
        if isinstance(source_seq, bool) or not isinstance(source_seq, int) or source_seq < 0:
            raise ValueError("source_seq_invalid")
        if source_pts_seconds < 0 or not math.isfinite(float(source_pts_seconds)):
            raise ValueError("source_pts_invalid")
        return OfflineObservationEventTime(
            source_seq=source_seq,
            source_pts_seconds=float(source_pts_seconds),
            observed_at_utc=normalized_video_started_at_utc + timedelta(
                seconds=float(source_pts_seconds)
            ),
        )


class AuthoritativePresentationTimeSampler:
    """Select at most one source frame for each presentation-time target."""

    algorithm_version = "authoritative_presentation_sampler_v1"

    def __init__(self, *, analysis_fps: float) -> None:
        if isinstance(analysis_fps, bool) or analysis_fps <= 0:
            raise ValueError("analysis_fps must be positive")
        self.analysis_fps = _decimal(analysis_fps, "analysis_fps")

    def sample(
        self,
        frames: Iterable[PresentationFrame],
        *,
        presentation_origin_pts: Optional[int],
        time_base: Fraction,
        presentation_continuity_verified: bool = False,
    ) -> SamplingResult:
        if presentation_origin_pts is None:
            raise ValueError("presentation_origin_missing")
        if not isinstance(time_base, Fraction) or time_base <= 0:
            raise ValueError("time_base_invalid")

        tick_seconds = Decimal(time_base.numerator) / Decimal(time_base.denominator)
        samples: list[OfflineSample] = []
        gaps: list[SamplingGap] = []
        next_k = 0
        previous_raw_pts: Optional[int] = None
        previous_source_pts: Optional[Decimal] = None
        last_end: Optional[Decimal] = None

        for frame in frames:
            if frame.raw_pts is None:
                raise ValueError("pts_missing")
            if frame.duration_ticks is None or frame.duration_ticks <= 0:
                raise ValueError("frame_duration_unavailable")
            if previous_raw_pts is not None and frame.raw_pts < previous_raw_pts:
                raise ValueError("pts_regression")

            source_pts = Decimal(frame.raw_pts - presentation_origin_pts) * tick_seconds
            if source_pts < 0:
                raise ValueError("source_pts_negative")
            if previous_source_pts is not None and source_pts < previous_source_pts:
                raise ValueError("normalized_pts_regression")

            frame_end = source_pts + Decimal(frame.duration_ticks) * tick_seconds
            if last_end is not None and source_pts > last_end:
                if not presentation_continuity_verified:
                    raise ValueError("timeline_discontinuity")
            last_end = frame_end
            previous_raw_pts = frame.raw_pts
            previous_source_pts = source_pts

            target = Decimal(next_k) / self.analysis_fps
            if source_pts < target:
                continue

            samples.append(
                OfflineSample(
                    sample_seq=len(samples),
                    source_seq=frame.source_seq,
                    target_k=next_k,
                    target_pts_seconds=float(target),
                    raw_pts=frame.raw_pts,
                    source_pts_seconds=float(source_pts),
                    sampling_delta_seconds=float(source_pts - target),
                )
            )
            next_k += 1
            while Decimal(next_k) / self.analysis_fps <= source_pts:
                gap_target = Decimal(next_k) / self.analysis_fps
                gaps.append(
                    SamplingGap(
                        target_k=next_k,
                        target_pts_seconds=float(gap_target),
                        reason="crossed_by_same_source_frame",
                    )
                )
                next_k += 1

        if last_end is None:
            raise ValueError("presentation_frames_missing")
        return SamplingResult(
            samples=tuple(samples),
            gaps=tuple(gaps),
            media_duration_seconds=float(last_end),
        )


@dataclass(frozen=True)
class NormalizedRecordingInterval:
    raw_started_at: datetime
    raw_ended_at: datetime
    timezone_offset_minutes: int
    timezone_source: str
    timezone_verified: bool
    started_at_utc: datetime
    ended_at_utc: datetime
    event_time_standard: str = "utc_naive"


class OfflineRecordingTimeNormalizer:
    """Convert a verified device-local RecordingFile interval into UTC-naive."""

    def normalize(
        self,
        *,
        raw_started_at: datetime,
        raw_ended_at: datetime,
        timezone_offset_minutes: Optional[int],
        timezone_source: str,
        timezone_verified: bool,
    ) -> NormalizedRecordingInterval:
        if not isinstance(raw_started_at, datetime) or not isinstance(raw_ended_at, datetime):
            raise ValueError("recording_time_invalid")
        if raw_started_at.tzinfo is not None or raw_ended_at.tzinfo is not None:
            raise ValueError("recording_time_must_be_device_local_naive")
        if raw_ended_at <= raw_started_at:
            raise ValueError("recording_interval_invalid")
        if (
            isinstance(timezone_offset_minutes, bool)
            or not isinstance(timezone_offset_minutes, int)
            or not timezone_verified
            or not timezone_source
        ):
            raise ValueError("recording_timezone_unavailable")
        local_zone = timezone(timedelta(minutes=timezone_offset_minutes))
        started = raw_started_at.replace(tzinfo=local_zone).astimezone(timezone.utc).replace(tzinfo=None)
        ended = raw_ended_at.replace(tzinfo=local_zone).astimezone(timezone.utc).replace(tzinfo=None)
        return NormalizedRecordingInterval(
            raw_started_at=raw_started_at,
            raw_ended_at=raw_ended_at,
            timezone_offset_minutes=timezone_offset_minutes,
            timezone_source=timezone_source,
            timezone_verified=True,
            started_at_utc=started,
            ended_at_utc=ended,
        )


@dataclass(frozen=True)
class P6SCapabilityProfile:
    profile_id: str
    profile_version: str
    trust_class: str
    status: str
    device_model: str
    firmware_versions: tuple[str, ...]
    api_versions: tuple[str, ...]
    pagination_completion_mode: str
    pagination_policy_version: str
    pagination_validation_report_ref: str
    segment_duration_policy_id: str
    segment_duration_policy_version: str
    segment_duration_tolerance_seconds: str
    segment_duration_validation_report_ref: str
    historical_mapping_capability_status: str
    historical_mapping_validation_report_ref: str
    timezone_interpretation_mode: str
    timezone_validation_report_ref: str
    profile_content_hash: str
    recognition_evidence_mode: str = "historical_query"
    historical_query_status: str = "supported"
    push_coverage_policy_id: str = ""
    push_coverage_policy_version: str = ""
    push_coverage_validation_report_ref: str = ""
    push_coverage_status: str = "unavailable"
    full_segment_boundary_status: str = "unverified"
    event_time_mode: str = "full_segment"
    event_time_policy_id: str = ""
    event_time_policy_version: str = ""
    boundary_guard_seconds: str = ""
    event_time_validation_report_ref: str = ""
    timezone_offset_minutes: Optional[int] = None
    face_recording_geometry_policy_id: str = ""
    face_recording_geometry_policy_version: str = ""
    face_recording_geometry_status: str = "unavailable"
    face_recording_geometry_validation_report_ref: str = ""
    face_recording_source_frame_size: Optional[tuple[int, int]] = None
    face_recording_target_frame_size: Optional[tuple[int, int]] = None
    face_recording_scale_x: Optional[str] = None
    face_recording_scale_y: Optional[str] = None
    face_recording_offset_x: Optional[str] = None
    face_recording_offset_y: Optional[str] = None
    face_recording_crop: Optional[str] = None
    face_recording_padding: Optional[str] = None
    face_recording_mirror: Optional[bool] = None
    face_recording_rotation: Optional[int] = None
    recognition_acceptance_policy_id: str = ""
    recognition_acceptance_policy_version: str = ""
    recognition_acceptance_policy_hash: str = ""
    recognition_similarity_threshold: str = ""
    # Empty api_versions means device-reported API version is not exposed by
    # this scoped device contract; runtime_protocol_contract remains required.
    device_serial: str = ""
    runtime_protocol_contract: str = ""

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        profile_version: str,
        trust_class: str,
        status: str,
        device_model: str,
        firmware_versions: tuple[str, ...],
        api_versions: tuple[str, ...],
        pagination_completion_mode: str,
        pagination_policy_version: str,
        pagination_validation_report_ref: str,
        segment_duration_policy_id: str,
        segment_duration_policy_version: str,
        segment_duration_tolerance_seconds: str,
        segment_duration_validation_report_ref: str,
        historical_mapping_capability_status: str,
        historical_mapping_validation_report_ref: str,
        timezone_interpretation_mode: str,
        timezone_validation_report_ref: str,
        recognition_evidence_mode: str = "historical_query",
        historical_query_status: str = "supported",
        push_coverage_policy_id: str = "",
        push_coverage_policy_version: str = "",
        push_coverage_validation_report_ref: str = "",
        push_coverage_status: str = "unavailable",
        full_segment_boundary_status: str = "unverified",
        event_time_mode: str = "full_segment",
        event_time_policy_id: str = "",
        event_time_policy_version: str = "",
        boundary_guard_seconds: str = "",
        event_time_validation_report_ref: str = "",
        timezone_offset_minutes: Optional[int] = None,
        face_recording_geometry_policy_id: str = "",
        face_recording_geometry_policy_version: str = "",
        face_recording_geometry_status: str = "unavailable",
        face_recording_geometry_validation_report_ref: str = "",
        face_recording_source_frame_size: Optional[tuple[int, int]] = None,
        face_recording_target_frame_size: Optional[tuple[int, int]] = None,
        face_recording_scale_x: Optional[str] = None,
        face_recording_scale_y: Optional[str] = None,
        face_recording_offset_x: Optional[str] = None,
        face_recording_offset_y: Optional[str] = None,
        face_recording_crop: Optional[str] = None,
        face_recording_padding: Optional[str] = None,
        face_recording_mirror: Optional[bool] = None,
        face_recording_rotation: Optional[int] = None,
        recognition_acceptance_policy_id: str = "",
        recognition_acceptance_policy_version: str = "",
        recognition_acceptance_policy_hash: str = "",
        recognition_similarity_threshold: str = "",
        device_serial: str = "",
        runtime_protocol_contract: str = "",
    ) -> "P6SCapabilityProfile":
        values = {
            "profile_id": profile_id,
            "profile_version": profile_version,
            "trust_class": trust_class,
            "status": status,
            "device_model": device_model,
            "firmware_versions": sorted(firmware_versions),
            "api_versions": sorted(api_versions),
            "pagination_completion_mode": pagination_completion_mode,
            "pagination_policy_version": pagination_policy_version,
            "pagination_validation_report_ref": pagination_validation_report_ref,
            "segment_duration_policy_id": segment_duration_policy_id,
            "segment_duration_policy_version": segment_duration_policy_version,
            "segment_duration_tolerance_seconds": (
                _canonical_decimal(segment_duration_tolerance_seconds)
                if segment_duration_tolerance_seconds else ""
            ),
            "segment_duration_validation_report_ref": segment_duration_validation_report_ref,
            "historical_mapping_capability_status": historical_mapping_capability_status,
            "historical_mapping_validation_report_ref": historical_mapping_validation_report_ref,
            "timezone_interpretation_mode": timezone_interpretation_mode,
            "timezone_validation_report_ref": timezone_validation_report_ref,
            "recognition_evidence_mode": recognition_evidence_mode,
            "historical_query_status": historical_query_status,
            "push_coverage_policy_id": push_coverage_policy_id,
            "push_coverage_policy_version": push_coverage_policy_version,
            "push_coverage_validation_report_ref": push_coverage_validation_report_ref,
            "push_coverage_status": push_coverage_status,
            "full_segment_boundary_status": full_segment_boundary_status,
            "event_time_mode": event_time_mode,
            "event_time_policy_id": event_time_policy_id,
            "event_time_policy_version": event_time_policy_version,
            "boundary_guard_seconds": (
                _canonical_decimal(boundary_guard_seconds)
                if boundary_guard_seconds else ""
            ),
            "event_time_validation_report_ref": event_time_validation_report_ref,
            "timezone_offset_minutes": timezone_offset_minutes,
            "face_recording_geometry_policy_id": face_recording_geometry_policy_id,
            "face_recording_geometry_policy_version": face_recording_geometry_policy_version,
            "face_recording_geometry_status": face_recording_geometry_status,
            "face_recording_geometry_validation_report_ref": face_recording_geometry_validation_report_ref,
            "face_recording_source_frame_size": face_recording_source_frame_size,
            "face_recording_target_frame_size": face_recording_target_frame_size,
            "face_recording_scale_x": (
                _canonical_decimal(face_recording_scale_x)
                if face_recording_scale_x is not None else None
            ),
            "face_recording_scale_y": (
                _canonical_decimal(face_recording_scale_y)
                if face_recording_scale_y is not None else None
            ),
            "face_recording_offset_x": (
                _canonical_decimal(face_recording_offset_x)
                if face_recording_offset_x is not None else None
            ),
            "face_recording_offset_y": (
                _canonical_decimal(face_recording_offset_y)
                if face_recording_offset_y is not None else None
            ),
            "face_recording_crop": face_recording_crop,
            "face_recording_padding": face_recording_padding,
            "face_recording_mirror": face_recording_mirror,
            "face_recording_rotation": face_recording_rotation,
            "recognition_acceptance_policy_id": recognition_acceptance_policy_id,
            "recognition_acceptance_policy_version": recognition_acceptance_policy_version,
            "recognition_acceptance_policy_hash": recognition_acceptance_policy_hash,
            "recognition_similarity_threshold": (
                _canonical_decimal(recognition_similarity_threshold)
                if recognition_similarity_threshold else ""
            ),
            "device_serial": device_serial,
            "runtime_protocol_contract": runtime_protocol_contract,
        }
        _nonempty_values({
            key: values[key]
            for key in (
                "profile_id", "profile_version", "trust_class", "status",
                "device_model", "firmware_versions",
                "historical_mapping_capability_status",
                "historical_mapping_validation_report_ref",
                "timezone_interpretation_mode", "timezone_validation_report_ref",
            )
        })
        if any(not isinstance(value, str) or not value for value in api_versions):
            raise ValueError("api_versions_invalid")
        if status not in {"verified", "unverified"}:
            raise ValueError("profile_status_invalid")
        if trust_class not in {"production_verified", "test_fixture"}:
            raise ValueError("profile_trust_class_invalid")
        if recognition_evidence_mode not in {"historical_query", "durable_push_inbox"}:
            raise ValueError("recognition_evidence_mode_invalid")
        if historical_query_status not in {"supported", "unsupported", "unverified"}:
            raise ValueError("historical_query_status_invalid")
        if recognition_evidence_mode == "historical_query":
            _nonempty_values({
                key: values[key]
                for key in (
                    "pagination_completion_mode", "pagination_policy_version",
                    "pagination_validation_report_ref", "segment_duration_policy_id",
                    "segment_duration_policy_version", "segment_duration_tolerance_seconds",
                    "segment_duration_validation_report_ref",
                )
            })
            if historical_query_status != "supported":
                raise ValueError("historical_query_profile_unsupported")
            if pagination_completion_mode not in {"explicit_metadata", "verified_short_page"}:
                raise ValueError("pagination_completion_mode_invalid")
            if _decimal(values["segment_duration_tolerance_seconds"], "segment_duration_tolerance_seconds") < 0:
                raise ValueError("segment_duration_tolerance_invalid")
        else:
            _validate_durable_push_profile(values)
        if historical_mapping_capability_status not in {"available", "unavailable"}:
            raise ValueError("historical_mapping_capability_status_invalid")
        content_hash = _PROFILE_HASH_PREFIX + _sha256(_canonical_json(values))
        return cls(
            profile_id=profile_id,
            profile_version=profile_version,
            trust_class=trust_class,
            status=status,
            device_model=device_model,
            firmware_versions=tuple(sorted(firmware_versions)),
            api_versions=tuple(sorted(api_versions)),
            pagination_completion_mode=pagination_completion_mode,
            pagination_policy_version=pagination_policy_version,
            pagination_validation_report_ref=pagination_validation_report_ref,
            segment_duration_policy_id=segment_duration_policy_id,
            segment_duration_policy_version=segment_duration_policy_version,
            segment_duration_tolerance_seconds=values["segment_duration_tolerance_seconds"],
            segment_duration_validation_report_ref=segment_duration_validation_report_ref,
            historical_mapping_capability_status=historical_mapping_capability_status,
            historical_mapping_validation_report_ref=historical_mapping_validation_report_ref,
            timezone_interpretation_mode=timezone_interpretation_mode,
            timezone_validation_report_ref=timezone_validation_report_ref,
            profile_content_hash=content_hash,
            recognition_evidence_mode=recognition_evidence_mode,
            historical_query_status=historical_query_status,
            push_coverage_policy_id=push_coverage_policy_id,
            push_coverage_policy_version=push_coverage_policy_version,
            push_coverage_validation_report_ref=push_coverage_validation_report_ref,
            push_coverage_status=push_coverage_status,
            full_segment_boundary_status=full_segment_boundary_status,
            event_time_mode=event_time_mode,
            event_time_policy_id=event_time_policy_id,
            event_time_policy_version=event_time_policy_version,
            boundary_guard_seconds=values["boundary_guard_seconds"],
            event_time_validation_report_ref=event_time_validation_report_ref,
            timezone_offset_minutes=timezone_offset_minutes,
            face_recording_geometry_policy_id=face_recording_geometry_policy_id,
            face_recording_geometry_policy_version=face_recording_geometry_policy_version,
            face_recording_geometry_status=face_recording_geometry_status,
            face_recording_geometry_validation_report_ref=face_recording_geometry_validation_report_ref,
            face_recording_source_frame_size=face_recording_source_frame_size,
            face_recording_target_frame_size=face_recording_target_frame_size,
            face_recording_scale_x=values["face_recording_scale_x"],
            face_recording_scale_y=values["face_recording_scale_y"],
            face_recording_offset_x=values["face_recording_offset_x"],
            face_recording_offset_y=values["face_recording_offset_y"],
            face_recording_crop=face_recording_crop,
            face_recording_padding=face_recording_padding,
            face_recording_mirror=face_recording_mirror,
            face_recording_rotation=face_recording_rotation,
            recognition_acceptance_policy_id=recognition_acceptance_policy_id,
            recognition_acceptance_policy_version=recognition_acceptance_policy_version,
            recognition_acceptance_policy_hash=recognition_acceptance_policy_hash,
            recognition_similarity_threshold=values["recognition_similarity_threshold"],
            device_serial=device_serial,
            runtime_protocol_contract=runtime_protocol_contract,
        )

    def verifies_own_hash(self) -> bool:
        rebuilt = self.create(
            profile_id=self.profile_id,
            profile_version=self.profile_version,
            trust_class=self.trust_class,
            status=self.status,
            device_model=self.device_model,
            firmware_versions=self.firmware_versions,
            api_versions=self.api_versions,
            pagination_completion_mode=self.pagination_completion_mode,
            pagination_policy_version=self.pagination_policy_version,
            pagination_validation_report_ref=self.pagination_validation_report_ref,
            segment_duration_policy_id=self.segment_duration_policy_id,
            segment_duration_policy_version=self.segment_duration_policy_version,
            segment_duration_tolerance_seconds=self.segment_duration_tolerance_seconds,
            segment_duration_validation_report_ref=self.segment_duration_validation_report_ref,
            historical_mapping_capability_status=self.historical_mapping_capability_status,
            historical_mapping_validation_report_ref=self.historical_mapping_validation_report_ref,
            timezone_interpretation_mode=self.timezone_interpretation_mode,
            timezone_validation_report_ref=self.timezone_validation_report_ref,
            recognition_evidence_mode=self.recognition_evidence_mode,
            historical_query_status=self.historical_query_status,
            push_coverage_policy_id=self.push_coverage_policy_id,
            push_coverage_policy_version=self.push_coverage_policy_version,
            push_coverage_validation_report_ref=self.push_coverage_validation_report_ref,
            push_coverage_status=self.push_coverage_status,
            full_segment_boundary_status=self.full_segment_boundary_status,
            event_time_mode=self.event_time_mode,
            event_time_policy_id=self.event_time_policy_id,
            event_time_policy_version=self.event_time_policy_version,
            boundary_guard_seconds=self.boundary_guard_seconds,
            event_time_validation_report_ref=self.event_time_validation_report_ref,
            timezone_offset_minutes=self.timezone_offset_minutes,
            face_recording_geometry_policy_id=self.face_recording_geometry_policy_id,
            face_recording_geometry_policy_version=self.face_recording_geometry_policy_version,
            face_recording_geometry_status=self.face_recording_geometry_status,
            face_recording_geometry_validation_report_ref=self.face_recording_geometry_validation_report_ref,
            face_recording_source_frame_size=self.face_recording_source_frame_size,
            face_recording_target_frame_size=self.face_recording_target_frame_size,
            face_recording_scale_x=self.face_recording_scale_x,
            face_recording_scale_y=self.face_recording_scale_y,
            face_recording_offset_x=self.face_recording_offset_x,
            face_recording_offset_y=self.face_recording_offset_y,
            face_recording_crop=self.face_recording_crop,
            face_recording_padding=self.face_recording_padding,
            face_recording_mirror=self.face_recording_mirror,
            face_recording_rotation=self.face_recording_rotation,
            recognition_acceptance_policy_id=self.recognition_acceptance_policy_id,
            recognition_acceptance_policy_version=self.recognition_acceptance_policy_version,
            recognition_acceptance_policy_hash=self.recognition_acceptance_policy_hash,
            recognition_similarity_threshold=self.recognition_similarity_threshold,
            device_serial=self.device_serial,
            runtime_protocol_contract=self.runtime_protocol_contract,
        )
        return rebuilt.profile_content_hash == self.profile_content_hash


@dataclass(frozen=True)
class P6SDeviceFingerprint:
    device_id: str
    device_model: str
    firmware_version: str
    # Device-reported API/protocol value. It is allowed to be absent for a
    # device scope that validates a separately-versioned runtime contract.
    api_version: Optional[str]
    metadata_source: str
    verified: bool
    device_serial: str = ""
    retrieved_at: str = ""
    metadata_endpoint: str = ""
    runtime_protocol_contract: str = ""

    @property
    def device_reported_api_version(self) -> Optional[str]:
        return self.api_version


def _validate_durable_push_profile(values: dict[str, object]) -> None:
    required = (
        "push_coverage_policy_id", "push_coverage_policy_version",
        "push_coverage_validation_report_ref", "event_time_policy_id",
        "event_time_policy_version", "boundary_guard_seconds",
        "event_time_validation_report_ref", "face_recording_geometry_policy_id",
        "face_recording_geometry_policy_version", "face_recording_geometry_validation_report_ref",
        "recognition_acceptance_policy_id", "recognition_acceptance_policy_version",
        "recognition_acceptance_policy_hash", "recognition_similarity_threshold",
    )
    _nonempty_values({key: values[key] for key in required})
    if values["historical_query_status"] != "unsupported":
        raise ValueError("durable_push_historical_query_status_invalid")
    if values["push_coverage_status"] != "verified":
        raise ValueError("push_coverage_status_invalid")
    if values["full_segment_boundary_status"] != "unverified":
        raise ValueError("full_segment_boundary_status_invalid")
    if values["event_time_mode"] != "guarded_interior":
        raise ValueError("event_time_mode_invalid")
    if _decimal(values["boundary_guard_seconds"], "boundary_guard_seconds") <= 0:
        raise ValueError("boundary_guard_seconds_invalid")
    if values["face_recording_geometry_status"] != "verified":
        raise ValueError("face_recording_geometry_status_invalid")
    source_size = values["face_recording_source_frame_size"]
    target_size = values["face_recording_target_frame_size"]
    if not _valid_frame_size(source_size) or not _valid_frame_size(target_size):
        raise ValueError("face_recording_frame_size_invalid")
    for field in (
        "face_recording_scale_x", "face_recording_scale_y",
        "face_recording_offset_x", "face_recording_offset_y",
    ):
        if values[field] is None:
            raise ValueError("face_recording_transform_incomplete")
    if (
        values["face_recording_crop"] != "none"
        or values["face_recording_padding"] != "none"
        or values["face_recording_mirror"] is not False
        or values["face_recording_rotation"] != 0
    ):
        raise ValueError("face_recording_transform_unsupported")
    if not isinstance(values["timezone_offset_minutes"], int):
        raise ValueError("timezone_offset_minutes_invalid")


def _valid_frame_size(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in value)
    )


@dataclass(frozen=True)
class CapabilityProfileResolution:
    status: str
    profile: Optional[P6SCapabilityProfile]
    reason: Optional[str]
    fingerprint: Optional[P6SDeviceFingerprint] = None
    resolution_attestation: Optional[str] = None

    def production_eligibility_reason(self, *, device_id: str) -> Optional[str]:
        if self.status != "resolved" or self.profile is None or self.fingerprint is None:
            return self.reason or "provider_capability_profile_unavailable"
        if self.fingerprint.device_id != device_id:
            return "profile_device_scope_mismatch"
        if not self.fingerprint.verified or not self.fingerprint.metadata_source:
            return "device_fingerprint_unverified"
        profile = self.profile
        if profile.trust_class != "production_verified" or profile.status != "verified":
            return "profile_not_production_verified"
        if not profile.verifies_own_hash():
            return "profile_hash_invalid"
        if (
            profile.device_model != self.fingerprint.device_model
            or self.fingerprint.firmware_version not in profile.firmware_versions
        ):
            return "profile_fingerprint_scope_mismatch"
        if profile.device_serial and profile.device_serial != self.fingerprint.device_serial:
            return "profile_serial_scope_mismatch"
        if (
            profile.runtime_protocol_contract
            and profile.runtime_protocol_contract != self.fingerprint.runtime_protocol_contract
        ):
            return "runtime_protocol_scope_mismatch"
        if profile.api_versions and self.fingerprint.api_version not in profile.api_versions:
            return "device_reported_api_scope_mismatch"
        if profile.historical_mapping_capability_status != "available":
            return "historical_mapping_capability_unavailable"
        if profile.recognition_evidence_mode == "durable_push_inbox":
            if profile.push_coverage_status != "verified":
                return "push_coverage_capability_unavailable"
            if profile.event_time_mode != "guarded_interior":
                return "event_time_policy_unavailable"
            if profile.full_segment_boundary_status != "unverified":
                return "full_segment_boundary_scope_invalid"
            if profile.face_recording_geometry_status != "verified":
                return "face_recording_geometry_unavailable"
            if not profile.recognition_acceptance_policy_hash:
                return "recognition_acceptance_policy_unavailable"
        expected = _profile_resolution_attestation(self.fingerprint, profile)
        if self.resolution_attestation != expected:
            return "profile_resolution_attestation_invalid"
        return None

    def profile_snapshot(self) -> Optional[dict[str, object]]:
        if self.profile is None or self.fingerprint is None:
            return None
        return {
            "profile_id": self.profile.profile_id,
            "profile_version": self.profile.profile_version,
            "profile_content_hash": self.profile.profile_content_hash,
            "device_id": self.fingerprint.device_id,
            "device_model": self.fingerprint.device_model,
            "firmware_version": self.fingerprint.firmware_version,
            "device_reported_api_version": self.fingerprint.api_version,
            "runtime_protocol_contract": self.fingerprint.runtime_protocol_contract,
            "fingerprint_source": self.fingerprint.metadata_source,
            "device_serial": self.fingerprint.device_serial,
            "fingerprint_retrieved_at": self.fingerprint.retrieved_at,
            "fingerprint_metadata_endpoint": self.fingerprint.metadata_endpoint,
            "fingerprint_verified": self.fingerprint.verified,
            "resolution_attestation": self.resolution_attestation,
            "recognition_evidence_mode": self.profile.recognition_evidence_mode,
            "historical_query_status": self.profile.historical_query_status,
            "push_coverage_policy_id": self.profile.push_coverage_policy_id,
            "push_coverage_policy_version": self.profile.push_coverage_policy_version,
            "event_time_mode": self.profile.event_time_mode,
            "event_time_policy_id": self.profile.event_time_policy_id,
            "event_time_policy_version": self.profile.event_time_policy_version,
            "boundary_guard_seconds": self.profile.boundary_guard_seconds,
            "face_recording_geometry_policy_id": self.profile.face_recording_geometry_policy_id,
            "face_recording_geometry_policy_version": self.profile.face_recording_geometry_policy_version,
            "recognition_acceptance_policy_id": self.profile.recognition_acceptance_policy_id,
            "recognition_acceptance_policy_version": self.profile.recognition_acceptance_policy_version,
            "recognition_acceptance_policy_hash": self.profile.recognition_acceptance_policy_hash,
            "profile_device_serial": self.profile.device_serial,
            "profile_runtime_protocol_contract": self.profile.runtime_protocol_contract,
        }

    def runtime_geometry_reason(
        self,
        *,
        source_frame_size: tuple[int, int],
        target_frame_size: tuple[int, int],
        policy_id: Optional[str] = None,
        policy_version: Optional[str] = None,
    ) -> Optional[str]:
        if self.status != "resolved" or self.profile is None:
            return self.reason or "provider_capability_profile_unavailable"
        if self.profile.face_recording_geometry_status != "verified":
            return "face_recording_geometry_unavailable"
        if (
            policy_id is not None
            and policy_id != self.profile.face_recording_geometry_policy_id
        ) or (
            policy_version is not None
            and policy_version != self.profile.face_recording_geometry_policy_version
        ):
            return "face_recording_geometry_policy_mismatch"
        if (
            source_frame_size != self.profile.face_recording_source_frame_size
            or target_frame_size != self.profile.face_recording_target_frame_size
        ):
            return "face_recording_geometry_runtime_mismatch"
        return None

    def event_time_policy_reason(
        self,
        *,
        mode: str,
        guard_seconds: float,
        policy_id: Optional[str] = None,
        policy_version: Optional[str] = None,
    ) -> Optional[str]:
        if self.status != "resolved" or self.profile is None:
            return self.reason or "provider_capability_profile_unavailable"
        if self.profile.event_time_mode != mode:
            return "event_time_mode_mismatch"
        if (
            policy_id is not None and policy_id != self.profile.event_time_policy_id
        ) or (
            policy_version is not None
            and policy_version != self.profile.event_time_policy_version
        ):
            return "event_time_policy_mismatch"
        if not math.isclose(
            float(self.profile.boundary_guard_seconds), guard_seconds, rel_tol=0, abs_tol=1e-9
        ):
            return "guard_seconds_mismatch"
        return None


class P6SCapabilityProfileResolver:
    def __init__(self, profiles: Iterable[P6SCapabilityProfile]) -> None:
        self.profiles = tuple(profiles)

    def resolve(
        self,
        *,
        device_id: Optional[str],
        device_model: Optional[str],
        firmware_version: Optional[str],
        api_version: Optional[str],
        fingerprint_source: Optional[str],
        fingerprint_verified: bool,
        device_serial: Optional[str] = None,
        fingerprint_retrieved_at: Optional[str] = None,
        fingerprint_metadata_endpoint: Optional[str] = None,
        runtime_protocol_contract: Optional[str] = None,
    ) -> CapabilityProfileResolution:
        if not device_id:
            return CapabilityProfileResolution("unavailable", None, "device_id_unknown")
        if not fingerprint_source or not fingerprint_verified:
            return CapabilityProfileResolution("unavailable", None, "device_fingerprint_unverified")
        if fingerprint_source == "device_push_metadata" and (
            not fingerprint_retrieved_at
            or fingerprint_metadata_endpoint != "POST /api/v1/event/pull"
        ):
            return CapabilityProfileResolution(
                "unavailable", None, "device_push_metadata_provenance_unavailable"
            )
        if not device_model:
            return CapabilityProfileResolution("unavailable", None, "device_model_unknown")
        if not firmware_version:
            return CapabilityProfileResolution("unavailable", None, "firmware_unknown")
        model_matches = [item for item in self.profiles if item.device_model == device_model]
        if not model_matches:
            return CapabilityProfileResolution("unavailable", None, "profile_not_found")
        serial_matches = [
            item for item in model_matches
            if not item.device_serial or item.device_serial == device_serial
        ]
        if not serial_matches:
            return CapabilityProfileResolution(
                "unavailable", None,
                "device_serial_unknown" if not device_serial else "serial_scope_mismatch",
            )
        firmware_matches = [item for item in serial_matches if firmware_version in item.firmware_versions]
        if not firmware_matches:
            return CapabilityProfileResolution("unavailable", None, "firmware_scope_mismatch")
        runtime_matches = [
            item for item in firmware_matches
            if not item.runtime_protocol_contract
            or item.runtime_protocol_contract == runtime_protocol_contract
        ]
        if not runtime_matches:
            return CapabilityProfileResolution(
                "unavailable", None,
                "runtime_protocol_contract_unknown"
                if not runtime_protocol_contract else "runtime_protocol_scope_mismatch",
            )
        api_matches = [
            item for item in runtime_matches
            if not item.api_versions or api_version in item.api_versions
        ]
        if not api_matches:
            return CapabilityProfileResolution(
                "unavailable", None,
                "device_reported_api_version_unknown"
                if not api_version else "api_scope_mismatch",
            )
        if len(api_matches) != 1:
            return CapabilityProfileResolution("unavailable", None, "profile_resolution_conflict")
        profile = api_matches[0]
        if profile.trust_class != "production_verified":
            return CapabilityProfileResolution("unavailable", None, "profile_source_untrusted")
        if profile.status != "verified":
            return CapabilityProfileResolution("unavailable", None, "profile_not_verified")
        if not profile.verifies_own_hash():
            return CapabilityProfileResolution("unavailable", None, "profile_hash_invalid")
        if profile.historical_mapping_capability_status != "available":
            return CapabilityProfileResolution(
                "unavailable", None, "historical_mapping_capability_unavailable"
            )
        if profile.recognition_evidence_mode not in {"historical_query", "durable_push_inbox"}:
            return CapabilityProfileResolution("unavailable", None, "recognition_evidence_mode_invalid")
        if profile.recognition_evidence_mode == "durable_push_inbox" and profile.push_coverage_status != "verified":
            return CapabilityProfileResolution("unavailable", None, "push_coverage_capability_unavailable")
        fingerprint = P6SDeviceFingerprint(
            device_id=device_id,
            device_model=device_model,
            firmware_version=firmware_version,
            api_version=api_version,
            metadata_source=fingerprint_source,
            verified=True,
            device_serial=device_serial or "",
            retrieved_at=fingerprint_retrieved_at or "",
            metadata_endpoint=fingerprint_metadata_endpoint or "",
            runtime_protocol_contract=runtime_protocol_contract or "",
        )
        return CapabilityProfileResolution(
            "resolved",
            profile,
            None,
            fingerprint,
            _profile_resolution_attestation(fingerprint, profile),
        )


@dataclass(frozen=True)
class SegmentDurationPolicy:
    policy_id: str
    policy_version: str
    status: str
    tolerance_seconds: Optional[str]
    validation_ref: Optional[str]


@dataclass(frozen=True)
class SegmentDurationAssessment:
    status: str
    index_duration_seconds: Optional[float]
    media_duration_seconds: Optional[float]
    duration_delta_seconds: Optional[float]
    tolerance_seconds: Optional[float]
    reason: Optional[str]
    policy_id: Optional[str] = None
    policy_version: Optional[str] = None
    profile_id: Optional[str] = None
    profile_version: Optional[str] = None
    profile_content_hash: Optional[str] = None
    index_duration_source: Optional[str] = None
    media_duration_source: Optional[str] = None
    presentation_timeline_status: Optional[str] = None

    def trusted_for(self, resolution: CapabilityProfileResolution) -> bool:
        profile = resolution.profile
        return bool(
            self.status in {"compatible", "conflict", "unavailable"}
            and profile is not None
            and self.policy_id == profile.segment_duration_policy_id
            and self.policy_version == profile.segment_duration_policy_version
            and self.profile_id == profile.profile_id
            and self.profile_version == profile.profile_version
            and self.profile_content_hash == profile.profile_content_hash
            and self.index_duration_source == "device_recording_segment"
            and self.media_duration_source == "last_presentation_frame_end"
            and self.presentation_timeline_status == "verified"
        )


SegmentDurationValidation = SegmentDurationAssessment


@dataclass(frozen=True)
class SegmentBoundaryAssessment:
    """Separate RecordingFile boundary evidence from duration consistency."""

    status: str
    index_start_seconds: Optional[float]
    index_end_seconds: Optional[float]
    first_presentation_pts_seconds: Optional[float]
    last_presentation_start_pts_seconds: Optional[float]
    last_presentation_end_pts_seconds: Optional[float]
    leading_delta_seconds: Optional[float]
    trailing_start_delta_seconds: Optional[float]
    trailing_end_delta_seconds: Optional[float]
    duration_delta_seconds: Optional[float]
    policy_version: str
    reason: Optional[str] = None
    ownership: str = "[start,end)"


@dataclass(frozen=True)
class AdjacentSegmentBoundaryAssessment:
    status: str
    boundary_delta_seconds: float
    ownership: str = "[start,end)"


@dataclass(frozen=True)
class GuardedInteriorEventTimeAssessment:
    """Fail-closed eligibility for the observed, non-boundary segment interior."""

    status: str
    policy_id: str
    policy_version: str
    guard_seconds: float
    event_offset_from_start_seconds: float
    event_distance_to_end_seconds: float
    reason: Optional[str]


def assess_guarded_interior_event_time(
    *,
    event_time: datetime,
    recording_start: datetime,
    recording_end: datetime,
    presentation_timeline_status: str,
    guard_seconds: float = 5.0,
    policy_id: str = "p6s_guarded_interior_event_time",
    policy_version: str = "p6s_guarded_interior_event_time_v1",
) -> GuardedInteriorEventTimeAssessment:
    """Assess a narrow event-time contract without changing segment boundaries.

    This policy is intentionally an event gate. It neither repairs a media
    timeline nor makes a claim about RecordingFile end-boundary semantics.
    """
    if not all(isinstance(value, datetime) for value in (
        event_time, recording_start, recording_end
    )):
        raise ValueError("guarded_interior_time_invalid")
    if not policy_id or not policy_version:
        raise ValueError("guarded_interior_policy_required")
    guard = _decimal(guard_seconds, "guarded_interior_guard_seconds")
    if guard < 0:
        raise ValueError("guarded_interior_guard_seconds_invalid")
    if recording_end <= recording_start:
        raise ValueError("guarded_interior_recording_interval_invalid")

    offset = (event_time - recording_start).total_seconds()
    distance_to_end = (recording_end - event_time).total_seconds()
    duration = (recording_end - recording_start).total_seconds()
    result = lambda status, reason: GuardedInteriorEventTimeAssessment(
        status=status,
        policy_id=policy_id,
        policy_version=policy_version,
        guard_seconds=float(guard),
        event_offset_from_start_seconds=offset,
        event_distance_to_end_seconds=distance_to_end,
        reason=reason,
    )

    if duration <= float(guard * 2):
        return result("unavailable", "guarded_interior_empty")
    if presentation_timeline_status != "verified":
        return result("unavailable", "presentation_timeline_unverified")
    if event_time < recording_start or event_time >= recording_end:
        return result("unavailable", "event_outside_recording_interval")
    if offset < float(guard):
        return result("unavailable", "recording_start_boundary_guard")
    if distance_to_end <= float(guard):
        return result("unavailable", "recording_end_boundary_guard")
    return result("verified", None)


def assess_adjacent_segment_boundary(
    *,
    previous_index_start_seconds: str,
    previous_last_presentation_end_seconds: str,
    next_index_start_seconds: str,
    next_first_presentation_pts_seconds: str,
) -> AdjacentSegmentBoundaryAssessment:
    previous_end = _decimal(
        previous_index_start_seconds, "previous_index_start_seconds"
    ) + _decimal(
        previous_last_presentation_end_seconds,
        "previous_last_presentation_end_seconds",
    )
    next_start = _decimal(
        next_index_start_seconds, "next_index_start_seconds"
    ) + _decimal(
        next_first_presentation_pts_seconds,
        "next_first_presentation_pts_seconds",
    )
    delta = next_start - previous_end
    if delta == 0:
        status = "exact"
    elif delta > 0:
        status = "gap"
    else:
        status = "overlap"
    return AdjacentSegmentBoundaryAssessment(status, float(delta))


def segment_owns_event_time(
    *, event_seconds: str, index_start_seconds: str, index_end_seconds: str
) -> bool:
    event = _decimal(event_seconds, "event_seconds")
    start = _decimal(index_start_seconds, "index_start_seconds")
    end = _decimal(index_end_seconds, "index_end_seconds")
    if end <= start:
        raise ValueError("segment_boundary_index_invalid")
    return start <= event < end


def assess_segment_boundary(
    *,
    index_start_seconds: str,
    index_end_seconds: str,
    first_presentation_pts_seconds: str,
    last_presentation_start_pts_seconds: str,
    last_presentation_end_pts_seconds: str,
    policy_version: str = "p6s_recording_segment_boundary_v1",
    leading_tolerance_seconds: str = "0",
    trailing_tolerance_seconds: Optional[str] = None,
    presentation_timeline_status: str = "verified",
) -> SegmentBoundaryAssessment:
    """Assess independently observable segment boundaries.

    ``index_end`` is a device cut boundary, not automatically the last frame
    start. A trailing frame may extend across that boundary; it is reported as
    evidence and is only accepted when the caller supplies an evidence-backed
    trailing tolerance. The event ownership contract remains half-open.
    """
    values = {
        "index_start": _decimal(index_start_seconds, "index_start_seconds"),
        "index_end": _decimal(index_end_seconds, "index_end_seconds"),
        "first": _decimal(first_presentation_pts_seconds, "first_presentation_pts_seconds"),
        "last_start": _decimal(
            last_presentation_start_pts_seconds,
            "last_presentation_start_pts_seconds",
        ),
        "last_end": _decimal(
            last_presentation_end_pts_seconds,
            "last_presentation_end_pts_seconds",
        ),
        "leading_tolerance": _decimal(
            leading_tolerance_seconds, "leading_tolerance_seconds"
        ),
    }
    if trailing_tolerance_seconds is None:
        trailing_tolerance = None
    else:
        trailing_tolerance = _decimal(
            trailing_tolerance_seconds, "trailing_tolerance_seconds"
        )
    if values["index_end"] <= values["index_start"]:
        raise ValueError("segment_boundary_index_invalid")
    if values["leading_tolerance"] < 0 or (
        trailing_tolerance is not None and trailing_tolerance < 0
    ):
        raise ValueError("segment_boundary_tolerance_invalid")
    if presentation_timeline_status != "verified":
        return _boundary_result(values, policy_version, "presentation_timeline_unverified")
    if any(value < 0 for key, value in values.items() if key not in {"index_start", "index_end"}):
        raise ValueError("segment_boundary_pts_invalid")
    if values["first"] < values["index_start"]:
        return _boundary_result(values, policy_version, "first_presentation_before_index_start")
    if values["last_start"] < values["first"] or values["last_end"] < values["last_start"]:
        return _boundary_result(values, policy_version, "presentation_pts_non_monotonic")
    if values["last_start"] >= values["index_end"]:
        return _boundary_result(values, policy_version, "last_presentation_start_at_or_after_index_end")
    leading = values["first"] - values["index_start"]
    trailing_start = values["last_start"] - values["index_end"]
    trailing_end = values["last_end"] - values["index_end"]
    duration_delta = values["last_end"] - values["index_end"]
    if leading > values["leading_tolerance"]:
        reason = "leading_boundary_outside_policy"
        status = "rejected"
    elif trailing_tolerance is None:
        reason = "trailing_boundary_policy_unverified"
        status = "unavailable"
    elif abs(trailing_end) > trailing_tolerance:
        reason = "trailing_boundary_outside_policy"
        status = "rejected"
    else:
        reason = None
        status = "verified"
    return SegmentBoundaryAssessment(
        status=status,
        index_start_seconds=float(values["index_start"]),
        index_end_seconds=float(values["index_end"]),
        first_presentation_pts_seconds=float(values["first"]),
        last_presentation_start_pts_seconds=float(values["last_start"]),
        last_presentation_end_pts_seconds=float(values["last_end"]),
        leading_delta_seconds=float(leading),
        trailing_start_delta_seconds=float(trailing_start),
        trailing_end_delta_seconds=float(trailing_end),
        duration_delta_seconds=float(duration_delta),
        policy_version=policy_version,
        reason=reason,
    )


def _boundary_result(
    values: dict[str, Decimal], policy_version: str, reason: str
) -> SegmentBoundaryAssessment:
    return SegmentBoundaryAssessment(
        status="rejected",
        index_start_seconds=float(values["index_start"]),
        index_end_seconds=float(values["index_end"]),
        first_presentation_pts_seconds=float(values["first"]),
        last_presentation_start_pts_seconds=float(values["last_start"]),
        last_presentation_end_pts_seconds=float(values["last_end"]),
        leading_delta_seconds=None,
        trailing_start_delta_seconds=None,
        trailing_end_delta_seconds=None,
        duration_delta_seconds=None,
        policy_version=policy_version,
        reason=reason,
    )


def validate_segment_duration(
    *,
    index_duration_seconds: str,
    media_duration_seconds: str,
    policy: SegmentDurationPolicy,
    source_frame_starts: Iterable[str],
) -> SegmentDurationAssessment:
    # ``source_frame_starts`` remains accepted for call-site compatibility.
    # Frame-vs-index boundary semantics are evaluated by
    # ``assess_segment_boundary``; they are not duration consistency.
    del source_frame_starts
    index_duration = _decimal(index_duration_seconds, "index_duration_seconds")
    media_duration = _decimal(media_duration_seconds, "media_duration_seconds")
    if index_duration <= 0 or media_duration <= 0:
        raise ValueError("segment_duration_invalid")
    if policy.status != "verified" or policy.tolerance_seconds is None or not policy.validation_ref:
        return SegmentDurationAssessment(
            "unavailable", float(index_duration), float(media_duration),
            None, None, "duration_policy_unverified"
        )
    tolerance = _decimal(policy.tolerance_seconds, "duration_tolerance_seconds")
    if tolerance < 0:
        raise ValueError("duration_tolerance_invalid")
    delta = media_duration - index_duration
    status = "compatible" if abs(delta) <= tolerance else "conflict"
    return SegmentDurationAssessment(
        status, float(index_duration), float(media_duration), float(delta),
        float(tolerance), None if status == "compatible" else "duration_outside_verified_tolerance",
        policy.policy_id, policy.policy_version,
    )


def assess_segment_duration(
    *,
    device_id: str,
    index_duration_seconds: str,
    media_duration_seconds: str,
    source_frame_starts: Iterable[str],
    profile_resolution: CapabilityProfileResolution,
    presentation_timeline_status: str,
) -> SegmentDurationAssessment:
    if presentation_timeline_status != "verified":
        return SegmentDurationAssessment(
            "unavailable",
            float(_decimal(index_duration_seconds, "index_duration_seconds")),
            None,
            None,
            None,
            "presentation_timeline_unverified",
            index_duration_source="device_recording_segment",
        )
    reason = profile_resolution.production_eligibility_reason(device_id=device_id)
    if reason is not None or profile_resolution.profile is None:
        return SegmentDurationAssessment(
            "unavailable",
            float(_decimal(index_duration_seconds, "index_duration_seconds")),
            float(_decimal(media_duration_seconds, "media_duration_seconds")),
            None,
            None,
            reason or "provider_capability_profile_unavailable",
            index_duration_source="device_recording_segment",
            media_duration_source="last_presentation_frame_end",
            presentation_timeline_status="verified",
        )
    profile = profile_resolution.profile
    result = validate_segment_duration(
        index_duration_seconds=index_duration_seconds,
        media_duration_seconds=media_duration_seconds,
        policy=SegmentDurationPolicy(
            policy_id=profile.segment_duration_policy_id,
            policy_version=profile.segment_duration_policy_version,
            status="verified",
            tolerance_seconds=profile.segment_duration_tolerance_seconds,
            validation_ref=profile.segment_duration_validation_report_ref,
        ),
        source_frame_starts=source_frame_starts,
    )
    return SegmentDurationAssessment(
        status=result.status,
        index_duration_seconds=result.index_duration_seconds,
        media_duration_seconds=result.media_duration_seconds,
        duration_delta_seconds=result.duration_delta_seconds,
        tolerance_seconds=result.tolerance_seconds,
        reason=result.reason,
        policy_id=profile.segment_duration_policy_id,
        policy_version=profile.segment_duration_policy_version,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_content_hash=profile.profile_content_hash,
        index_duration_source="device_recording_segment",
        media_duration_source="last_presentation_frame_end",
        presentation_timeline_status="verified",
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _profile_resolution_attestation(
    fingerprint: P6SDeviceFingerprint,
    profile: P6SCapabilityProfile,
) -> str:
    body = {
        "device_id": fingerprint.device_id,
        "device_model": fingerprint.device_model,
        "firmware_version": fingerprint.firmware_version,
        "api_version": fingerprint.api_version,
        "runtime_protocol_contract": fingerprint.runtime_protocol_contract,
        "metadata_source": fingerprint.metadata_source,
        "fingerprint_verified": fingerprint.verified,
        "device_serial": fingerprint.device_serial,
        "retrieved_at": fingerprint.retrieved_at,
        "metadata_endpoint": fingerprint.metadata_endpoint,
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_content_hash": profile.profile_content_hash,
    }
    return _PROFILE_HASH_PREFIX + _sha256(_canonical_json(body))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_decimal(value: object) -> str:
    number = _decimal(value, "decimal")
    rendered = format(number.normalize(), "f")
    return "0" if rendered in {"-0", ""} else rendered


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field}_invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc
    if not result.is_finite():
        raise ValueError(f"{field}_invalid")
    return result


def _nonempty_values(values: dict[str, object]) -> None:
    for key, value in values.items():
        if isinstance(value, (tuple, list)):
            if not value or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"{key}_required")
        elif not isinstance(value, str) or not value:
            raise ValueError(f"{key}_required")
