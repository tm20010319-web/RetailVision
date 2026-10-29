"""Small application contracts for an Offline V1 analysis attempt."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from agent.offline_source import P6SRecordingSource


@dataclass(frozen=True)
class OfflineVideoAnalysisRequest:
    analysis_job_id: str
    analysis_run_id: str
    video_path: Path
    store_id: str
    camera_id: str
    device_id: Optional[str]
    source_origin: str
    recording: Optional[P6SRecordingSource]
    controlled_download: bool
    timezone_offset_minutes: Optional[int]
    timezone_source: str
    timezone_verified: bool
    analysis_fps: float = 5.0
    segment_duration_status: str = "unavailable"
    provider_a_status: str = "unavailable"

    def __post_init__(self) -> None:
        for value in (self.analysis_job_id, self.analysis_run_id, self.store_id, self.camera_id):
            if not isinstance(value, str) or not value:
                raise ValueError("offline_request_scope_required")
        if ":" in self.analysis_run_id:
            raise ValueError("analysis_run_id_must_not_contain_colon")
        if self.source_origin not in {"p6s_recording_file", "untrusted_import"}:
            raise ValueError("source_origin_invalid")
        if self.source_origin == "p6s_recording_file" and (not self.device_id or self.recording is None):
            raise ValueError("p6s_recording_request_incomplete")
        if self.analysis_fps <= 0:
            raise ValueError("analysis_fps_invalid")


@dataclass(frozen=True)
class OfflineBehaviorPass:
    media_timeline_status: str
    raw_stage9_events: tuple[Any, ...]
    person_tracks: tuple[Any, ...]
    product_tracks: tuple[Any, ...]
    identity_evidence: tuple[Any, ...]
    track_identity_associations: Mapping[str, Any]
    member_enrichments: Mapping[str, Any]
    behavior_summary: Mapping[str, Any]
    analyzed_video_bytes: bytes
    keyframes: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class OfflineIdentityPassResult:
    recognition_coverage_status: str
    associations: Mapping[str, Any]
    failure_reason: Optional[str] = None
    accepted_evidence: tuple[Any, ...] = ()
    record_audits: tuple[Any, ...] = ()
    page_audits: tuple[Any, ...] = ()
    recognition_acceptance_policy_snapshot: Mapping[str, Any] = field(
        default_factory=dict
    )
    # These are evidence-derived runtime assessments used only by the optional
    # B2.8 qualification gate.  They keep the generic anonymous behavior path
    # independent from identity availability.
    historical_identity_status: str = "unavailable"
    guarded_event_time_status: str = "unavailable"
    runtime_face_geometry_status: str = "unavailable"
    presentation_timeline_status: str = "unavailable"

    def __post_init__(self) -> None:
        if self.recognition_coverage_status not in {"complete", "unavailable"}:
            raise ValueError("recognition_coverage_status_invalid")
        for value in (
            self.historical_identity_status,
            self.guarded_event_time_status,
            self.runtime_face_geometry_status,
            self.presentation_timeline_status,
        ):
            if value not in {"resolved", "verified", "compatible", "unavailable"}:
                raise ValueError("identity_runtime_assessment_invalid")
