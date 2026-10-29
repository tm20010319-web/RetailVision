"""Atomic Job-level publication fence for normalized Offline behavior facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Protocol

from agent.offline_artifacts import (
    OfflineRunArtifactStorePort,
    SealedOfflineRunArtifacts,
)
from agent.offline_behavior import PreparedOfflineBehaviorPublication


class OfflinePublicationBlockedError(ValueError):
    pass


class OfflinePublicationRepositoryPort(Protocol):
    def publish_once(
        self,
        *,
        prepared: PreparedOfflineBehaviorPublication,
        published_at: datetime,
    ) -> "OfflinePublicationResult": ...


class OfflineBehaviorPublisherPort(Protocol):
    def publish(
        self,
        *,
        prepared: PreparedOfflineBehaviorPublication,
        artifacts: SealedOfflineRunArtifacts,
        artifact_store: OfflineRunArtifactStorePort,
        time_authority: "OfflineBehaviorTimeAuthority | None" = None,
    ) -> OfflinePublicationResult: ...


@dataclass(frozen=True)
class OfflinePublicationResult:
    status: str
    analysis_job_id: str
    published_analysis_run_id: str
    event_count: int
    event_manifest_hash: str


@dataclass(frozen=True)
class OfflineBehaviorTimeAuthority:
    media_timeline_status: str
    absolute_event_time_status: str
    behavior_time_quality: str
    identity_status: str
    publication_eligible: bool
    publication_block_reason: str | None


@dataclass(frozen=True)
class TrustedOfflinePublicationQualification:
    """Result of the optional B2.8 real-device publication gate.

    This is deliberately a qualification decision, not a second publication
    model.  The existing artifact seal, canonical event preparation, and
    job-level fence remain the only formal publication path.
    """

    status: str
    reason: str | None


class TrustedOfflinePublicationGate:
    """Require the B2.8 dynamic evidence chain before formal publication.

    The generic Offline V1 pipeline may publish an anonymous behavior fact
    after an identity failure.  A real-device B2.8 qualification is narrower:
    it must prove the same run has trusted source/time evidence and a confirmed
    identity for every behavior-bearing Person Track.
    """

    def assess(
        self,
        *,
        profile_resolution: Any,
        device_id: str | None,
        source_status: str,
        registration_status: str,
        segment_duration_status: str,
        presentation_timeline_status: str,
        time_authority: OfflineBehaviorTimeAuthority,
        recognition_coverage_status: str | None,
        historical_identity_status: str | None,
        guarded_event_time_status: str | None,
        runtime_face_geometry_status: str | None,
        behavior_track_tokens: Iterable[str],
        associations: Any,
    ) -> TrustedOfflinePublicationQualification:
        if profile_resolution is None or not device_id:
            return TrustedOfflinePublicationQualification(
                "unavailable", "capability_profile_unavailable"
            )
        profile_reason = profile_resolution.production_eligibility_reason(
            device_id=device_id
        )
        if profile_reason is not None:
            return TrustedOfflinePublicationQualification("unavailable", profile_reason)
        if source_status != "verified":
            return TrustedOfflinePublicationQualification(
                "unavailable", "source_integrity_unavailable"
            )
        if registration_status != "available":
            return TrustedOfflinePublicationQualification(
                "unavailable", "registration_scope_unavailable"
            )
        if segment_duration_status != "compatible":
            return TrustedOfflinePublicationQualification(
                "unavailable", "presentation_timeline_unavailable"
            )
        if presentation_timeline_status != "verified":
            return TrustedOfflinePublicationQualification(
                "unavailable", "presentation_timeline_unavailable"
            )
        if not time_authority.publication_eligible:
            return TrustedOfflinePublicationQualification(
                "unavailable",
                time_authority.publication_block_reason or "behavior_time_unavailable",
            )
        if recognition_coverage_status != "complete":
            return TrustedOfflinePublicationQualification(
                "unavailable", "push_coverage_unavailable"
            )
        if historical_identity_status != "resolved":
            return TrustedOfflinePublicationQualification(
                "unavailable", "historical_identity_unavailable"
            )
        if guarded_event_time_status != "verified":
            return TrustedOfflinePublicationQualification(
                "unavailable", "guarded_event_time_unavailable"
            )
        if runtime_face_geometry_status != "compatible":
            return TrustedOfflinePublicationQualification(
                "unavailable", "runtime_face_geometry_unavailable"
            )

        for token in sorted(set(behavior_track_tokens)):
            association = associations.get(token) if hasattr(associations, "get") else None
            if getattr(association, "status", None) != "confirmed":
                return TrustedOfflinePublicationQualification(
                    "unavailable", "track_identity_not_confirmed"
                )
        return TrustedOfflinePublicationQualification("verified", None)


class OfflineBehaviorTimePublicationGate:
    """Keep degraded media clocks artifact-only without blocking anonymous facts."""

    def assess(
        self,
        *,
        media_timeline_status: str,
        absolute_event_time_status: str,
        identity_status: str,
    ) -> OfflineBehaviorTimeAuthority:
        if media_timeline_status != "available":
            return OfflineBehaviorTimeAuthority(
                media_timeline_status, absolute_event_time_status, "degraded",
                "unavailable", False, "media_timeline_unavailable",
            )
        if absolute_event_time_status != "available":
            return OfflineBehaviorTimeAuthority(
                media_timeline_status, absolute_event_time_status, "degraded",
                "unavailable" if identity_status == "confirmed" else identity_status,
                False, "absolute_event_time_unavailable",
            )
        return OfflineBehaviorTimeAuthority(
            media_timeline_status, absolute_event_time_status, "authoritative",
            identity_status, True, None,
        )


class OfflineBehaviorPublisher:
    """Publish one Prepared batch once, without Live behavior side effects."""

    def __init__(
        self,
        *,
        publication_repository: OfflinePublicationRepositoryPort,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    ) -> None:
        self.publication_repository = publication_repository
        self.clock = clock

    def publish(
        self,
        *,
        prepared: PreparedOfflineBehaviorPublication,
        artifacts: SealedOfflineRunArtifacts,
        artifact_store: OfflineRunArtifactStorePort,
        time_authority: OfflineBehaviorTimeAuthority | None = None,
    ) -> OfflinePublicationResult:
        if time_authority is not None and not time_authority.publication_eligible:
            raise OfflinePublicationBlockedError(
                time_authority.publication_block_reason or "behavior_time_unavailable"
            )
        verification = artifact_store.verify(artifacts)
        if not verification.publication_eligible:
            raise OfflinePublicationBlockedError(
                "artifact_integrity_" + (verification.reason or "unavailable")
            )
        _validate_prepared_against_artifacts(prepared, artifacts)
        published_at = self.clock()
        if not isinstance(published_at, datetime) or published_at.tzinfo is not None:
            raise ValueError("published_at_must_be_utc_naive")

        return self.publication_repository.publish_once(
            prepared=prepared, published_at=published_at
        )


def _validate_prepared_against_artifacts(
    prepared: PreparedOfflineBehaviorPublication,
    artifacts: SealedOfflineRunArtifacts,
) -> None:
    if (
        prepared.analysis_job_id != artifacts.analysis_job_id
        or prepared.analysis_run_id != artifacts.analysis_run_id
        or prepared.source_video_id != artifacts.source_video_id
    ):
        raise OfflinePublicationBlockedError("publication_artifact_scope_mismatch")
    for event in prepared.events:
        details = event.details
        if (
            details.get("analysis_job_id") != prepared.analysis_job_id
            or details.get("analysis_run_id") != prepared.analysis_run_id
            or details.get("source_video_id") != prepared.source_video_id
        ):
            raise OfflinePublicationBlockedError("event_provenance_scope_mismatch")
        artifact = details.get("offline_artifact")
        if not isinstance(artifact, Mapping) or artifact.get(
            "artifact_manifest_hash"
        ) != artifacts.artifact_manifest_hash:
            raise OfflinePublicationBlockedError("event_artifact_provenance_mismatch")
        if event.visit_id is not None or event.entry_event_id is not None or event.evidence_clip_id is not None:
            raise OfflinePublicationBlockedError("offline_relation_must_be_null")
        if event.member_id is not None and event.identity_status != "confirmed":
            raise OfflinePublicationBlockedError("member_projection_requires_confirmed")


def existing_publication_result(
    existing: Any,
    prepared: PreparedOfflineBehaviorPublication,
) -> OfflinePublicationResult:
    same = all((
        existing.published_analysis_run_id == prepared.analysis_run_id,
        existing.source_video_id == prepared.source_video_id,
        existing.event_count == prepared.event_count,
        existing.event_manifest_hash == prepared.event_manifest_hash,
    ))
    return OfflinePublicationResult(
        "already_published_same_manifest" if same else "already_published_conflict",
        existing.analysis_job_id,
        existing.published_analysis_run_id,
        existing.event_count,
        existing.event_manifest_hash,
    )
