"""Offline V1 application orchestration; deliberately separate from Live flows."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Iterable, Optional, Protocol

from agent.offline.offline_artifacts import (
    OfflineArtifactError,
    OfflineRunArtifactStorePort,
)
from agent.offline.offline_behavior import (
    CustomerOrdinalObservation,
    MemberProfileEnrichment,
    OfflineDisplayIdentity,
    OfflineNormalizedBehaviorFact,
    OfflinePersonDisplayIdentityResolver,
    OfflineRunCustomerOrdinalRegistry,
    PreparedOfflineBehaviorPublication,
)
from agent.offline.offline_identity import OfflineTrackIdentityAggregator, OfflineTrackIdentityAssociation
from agent.offline.offline_media import CapabilityProfileResolution, SegmentDurationAssessment
from agent.offline.offline_publication import (
    OfflineBehaviorPublisherPort,
    OfflineBehaviorTimeAuthority,
    OfflinePublicationResult,
    TrustedOfflinePublicationGate,
)
from agent.offline.offline_scope import OfflineRunScopeValidator
from agent.offline.offline_source import (
    HistoricalRegistrationInterval,
    RegistrationScopeValidation,
    TrustedSourceVideoResult,
)
from agent.offline.offline_video_models import (
    OfflineBehaviorPass,
    OfflineIdentityPassResult,
    OfflineVideoAnalysisRequest,
)


class OfflineBehaviorAnalyzerPort(Protocol):
    """Behavior implementation port; Stage5/7C/8/9 remain behind this boundary."""

    def __call__(
        self,
        request: OfflineVideoAnalysisRequest,
        video_started_at: datetime | None,
    ) -> OfflineBehaviorPass: ...


class OfflineIdentityPassPort(Protocol):
    def __call__(
        self,
        request: OfflineVideoAnalysisRequest,
        video_started_at: datetime,
        video_ended_at: datetime,
    ) -> OfflineIdentityPassResult: ...


class OfflineBehaviorFactNormalizerPort(Protocol):
    def normalize(
        self,
        *,
        analysis_job_id: str,
        analysis_run_id: str,
        source_video_id: str,
        raw_events: Iterable[Any],
        absolute_event_time_status: str,
        behavior_time_quality: str,
    ) -> Any: ...


class ProductInteractionFactWriterPort(Protocol):
    def write(
        self,
        facts: Iterable[OfflineNormalizedBehaviorFact],
        *,
        store_id: str,
        artifact_manifest_hash: str | None = None,
    ) -> Any: ...


class OfflineBehaviorIdentityProjectionPort(Protocol):
    def project(
        self,
        *,
        fact: OfflineNormalizedBehaviorFact,
        association: OfflineTrackIdentityAssociation,
        enrichment: MemberProfileEnrichment | None,
        artifact_manifest_hash: str,
        association_record_ref: str,
    ) -> Any: ...


class OfflineBehaviorTimePublicationGatePort(Protocol):
    def assess(
        self,
        *,
        media_timeline_status: str,
        absolute_event_time_status: str,
        identity_status: str,
    ) -> OfflineBehaviorTimeAuthority: ...


class SegmentDurationAssessmentPort(Protocol):
    def __call__(
        self,
        request: OfflineVideoAnalysisRequest,
        behavior: OfflineBehaviorPass,
    ) -> SegmentDurationAssessment: ...


class RegistrationScopeValidationPort(Protocol):
    def validate(
        self,
        *,
        video_started_at: datetime,
        video_ended_at: datetime,
        requested_camera_id: str,
        requested_store_id: str,
        device_camera_intervals: Iterable[HistoricalRegistrationInterval],
        camera_store_intervals: Iterable[HistoricalRegistrationInterval],
    ) -> RegistrationScopeValidation: ...


class OfflineOverlayRendererPort(Protocol):
    def __call__(
        self,
        behavior: OfflineBehaviorPass,
        labels: dict[str, str],
        *,
        artifact_only: bool,
    ) -> Any: ...


class OfflineOverlayRenderResult:
    def __init__(self, video_bytes: bytes, *, artifact_only_marked: bool) -> None:
        self.video_bytes = video_bytes
        self.artifact_only_marked = artifact_only_marked


class OfflinePipelineResult:
    def __init__(
        self,
        *,
        source_video_integrity_status: str,
        registration_scope_status: str,
        segment_duration_status: str,
        media_timeline_status: str,
        absolute_event_time_status: str,
        identity_status: str,
        behavior_time_quality: str,
        publication_eligible: bool,
        publication_block_reason: str | None,
        artifact_directory: str,
        artifact_manifest_hash: str | None,
        publication: OfflinePublicationResult | None,
        publication_status: str,
        trusted_publication_status: str | None = None,
        trusted_publication_reason: str | None = None,
        product_analytics_projection_status: str = "disabled",
        product_analytics_projection_reason: str | None = None,
    ) -> None:
        self.source_video_integrity_status = source_video_integrity_status
        self.registration_scope_status = registration_scope_status
        self.segment_duration_status = segment_duration_status
        self.media_timeline_status = media_timeline_status
        self.absolute_event_time_status = absolute_event_time_status
        self.identity_status = identity_status
        self.behavior_time_quality = behavior_time_quality
        self.publication_eligible = publication_eligible
        self.publication_block_reason = publication_block_reason
        self.artifact_directory = artifact_directory
        self.artifact_manifest_hash = artifact_manifest_hash
        self.publication = publication
        self.publication_status = publication_status
        self.trusted_publication_status = trusted_publication_status
        self.trusted_publication_reason = trusted_publication_reason
        self.product_analytics_projection_status = product_analytics_projection_status
        self.product_analytics_projection_reason = product_analytics_projection_reason


class OfflineBehaviorPipeline:
    """Compose Offline-only gates without reusing Live Visit or Binding flows."""

    def __init__(
        self,
        *,
        artifact_store: OfflineRunArtifactStorePort,
        behavior_analyzer: OfflineBehaviorAnalyzerPort,
        trusted_source_provider: Callable[[OfflineVideoAnalysisRequest], TrustedSourceVideoResult],
        recording_time_provider: Callable[[OfflineVideoAnalysisRequest], Any | None],
        provider_a_profile: CapabilityProfileResolution,
        segment_duration_assessor: SegmentDurationAssessmentPort,
        fact_normalizer: OfflineBehaviorFactNormalizerPort,
        identity_projector: OfflineBehaviorIdentityProjectionPort,
        time_publication_gate: OfflineBehaviorTimePublicationGatePort,
        registration_validator: RegistrationScopeValidationPort,
        overlay_renderer: Optional[OfflineOverlayRendererPort] = None,
        publisher: Optional[OfflineBehaviorPublisherPort] = None,
        identity_pass: Optional[OfflineIdentityPassPort] = None,
        run_scope_validator: Optional[OfflineRunScopeValidator] = None,
        trusted_publication_gate: Optional[TrustedOfflinePublicationGate] = None,
        product_analytics_writer: Optional[ProductInteractionFactWriterPort] = None,
        product_analytics_enabled: bool = False,
    ) -> None:
        self.artifact_store = artifact_store
        self.behavior_analyzer = behavior_analyzer
        self.trusted_source_provider = trusted_source_provider
        self.recording_time_provider = recording_time_provider
        if not isinstance(provider_a_profile, CapabilityProfileResolution):
            raise ValueError("provider_capability_profile_resolution_required")
        self.provider_a_profile = provider_a_profile
        self.segment_duration_assessor = segment_duration_assessor
        self.normalizer_adapter = fact_normalizer
        self.identity_projector = identity_projector
        self.time_gate = time_publication_gate
        self.registration_validator = registration_validator
        self.overlay_renderer = overlay_renderer
        self.publisher = publisher
        self.identity_pass = identity_pass
        self.run_scope_validator = run_scope_validator or OfflineRunScopeValidator()
        self.trusted_publication_gate = trusted_publication_gate
        if not isinstance(product_analytics_enabled, bool):
            raise ValueError("product_analytics_enabled_must_be_boolean")
        if product_analytics_enabled and product_analytics_writer is None:
            raise ValueError("product_analytics_writer_required_when_enabled")
        self.product_analytics_writer = product_analytics_writer
        self.product_analytics_enabled = product_analytics_enabled

    def run(
        self,
        *,
        request: OfflineVideoAnalysisRequest,
        device_camera_intervals: Iterable[HistoricalRegistrationInterval] = (),
        camera_store_intervals: Iterable[HistoricalRegistrationInterval] = (),
    ) -> OfflinePipelineResult:
        analytics_status = (
            "not_written" if self.product_analytics_enabled else "disabled"
        )
        analytics_reason = (
            "analytics_artifact_not_sealed"
            if self.product_analytics_enabled
            else None
        )
        source, recording_interval, registration = self._foundation(
            request, device_camera_intervals, camera_store_intervals
        )
        started_at = (
            recording_interval.started_at_utc if recording_interval is not None else None
        )
        ended_at = (
            recording_interval.ended_at_utc if recording_interval is not None else None
        )
        registration_status = registration.status if registration is not None else "unavailable"
        registration_reason = registration.reason if registration is not None else "registration_coverage_unverifiable"
        behavior = self.behavior_analyzer(request, started_at)
        try:
            segment_duration = self.segment_duration_assessor(request, behavior)
        except Exception:
            segment_duration = SegmentDurationAssessment(
                "unavailable", None, None, None, None,
                "segment_duration_assessment_failed",
            )
        if not isinstance(segment_duration, SegmentDurationAssessment):
            segment_duration = SegmentDurationAssessment(
                "unavailable", None, None, None, None,
                "segment_duration_assessment_contract_invalid",
            )
        elif not segment_duration.trusted_for(self.provider_a_profile):
            index_known = (
                segment_duration.index_duration_seconds
                if segment_duration.index_duration_source == "device_recording_segment"
                else None
            )
            media_known = (
                segment_duration.media_duration_seconds
                if (
                    segment_duration.media_duration_source
                    in {"last_presentation_frame_end", "presentation_timeline"}
                    and segment_duration.presentation_timeline_status == "verified"
                )
                else None
            )
            segment_duration = SegmentDurationAssessment(
                "unavailable",
                index_known,
                media_known,
                None,
                None,
                "segment_duration_assessment_untrusted",
                index_duration_source=(
                    "device_recording_segment" if index_known is not None else None
                ),
                media_duration_source=(
                    segment_duration.media_duration_source
                    if media_known is not None else None
                ),
                presentation_timeline_status=(
                    "verified" if media_known is not None else None
                ),
            )
        behavior_scope = self.run_scope_validator.validate_behavior(
            store_id=request.store_id,
            camera_id=request.camera_id,
            analysis_run_id=request.analysis_run_id,
            raw_stage9_events=behavior.raw_stage9_events,
            person_tracks=behavior.person_tracks,
            product_tracks=behavior.product_tracks,
        )
        absolute_status = "available" if started_at is not None and ended_at is not None else "unavailable"
        time_authority = self.time_gate.assess(
            media_timeline_status=behavior.media_timeline_status,
            absolute_event_time_status=absolute_status,
            identity_status="unavailable",
        )
        facts = self.normalizer_adapter.normalize(
            analysis_job_id=request.analysis_job_id,
            analysis_run_id=request.analysis_run_id,
            source_video_id=source.source_video_id or "artifact-only-source",
            raw_events=behavior.raw_stage9_events,
            absolute_event_time_status=absolute_status,
            behavior_time_quality=time_authority.behavior_time_quality,
        ).facts
        fact_scope = self.run_scope_validator.validate_facts(
            store_id=request.store_id,
            camera_id=request.camera_id,
            analysis_run_id=request.analysis_run_id,
            facts=facts,
        )
        scope_contract_failure = behavior_scope.reason or fact_scope.reason
        identity_failure_reason = self._identity_gate_failure_reason(
            source=source,
            recording_interval=recording_interval,
            registration_status=registration_status,
            registration_reason=registration_reason,
            segment_duration_status=segment_duration.status,
            time_authority=time_authority,
            scope_contract_failure=scope_contract_failure,
        )
        identity_allowed = identity_failure_reason is None
        associations: dict[str, Any] = {}
        identity_result: OfflineIdentityPassResult | None = None
        if identity_allowed:
            try:
                identity_result = self.identity_pass(request, started_at, ended_at)
            except Exception:
                identity_allowed = False
                identity_failure_reason = "identity_pass_failed"
            if identity_allowed and not isinstance(identity_result, OfflineIdentityPassResult):
                identity_allowed = False
                identity_failure_reason = "identity_pass_contract_invalid"
                identity_result = None
            elif (
                identity_allowed
                and identity_result.recognition_coverage_status == "complete"
                and not _valid_acceptance_policy_snapshot(
                    identity_result.recognition_acceptance_policy_snapshot
                )
            ):
                identity_allowed = False
                identity_failure_reason = "recognition_acceptance_policy_snapshot_missing"
            elif identity_allowed and identity_result.recognition_coverage_status == "complete":
                associations = dict(identity_result.associations)
            elif identity_allowed:
                identity_allowed = False
                identity_failure_reason = (
                    identity_result.failure_reason or "recognition_coverage_unavailable"
                )
        if identity_allowed:
            association_scope = self.run_scope_validator.validate_associations(
                store_id=request.store_id,
                camera_id=request.camera_id,
                analysis_run_id=request.analysis_run_id,
                associations=associations,
            )
            if not association_scope.valid:
                identity_allowed = False
                identity_failure_reason = association_scope.reason
                scope_contract_failure = association_scope.reason
                associations = {}
        track_intervals = _track_intervals(behavior.person_tracks, facts)
        relative_track_intervals = _relative_track_intervals(
            behavior.person_tracks, facts
        )
        degraded_association_time = (
            absolute_status != "available"
            or time_authority.behavior_time_quality == "degraded"
        )
        track_tokens = (
            set(track_intervals) | set(relative_track_intervals)
            if degraded_association_time
            else set(track_intervals)
        )
        for token in sorted(track_tokens):
            track_interval = track_intervals.get(token)
            relative_track_interval = relative_track_intervals.get(token)
            if identity_allowed:
                if track_interval is None:
                    continue
                associations.setdefault(
                    token,
                    OfflineTrackIdentityAggregator().aggregate(
                        analysis_run_id=request.analysis_run_id,
                        track_token=token,
                        track_started_at=track_interval[0],
                        track_ended_at=track_interval[1],
                        associations=(),
                    ),
                )
            else:
                associations.setdefault(
                    token,
                    OfflineTrackIdentityAggregator.unavailable(
                        analysis_run_id=request.analysis_run_id,
                        track_token=token,
                        track_started_at=(
                            None if degraded_association_time else track_interval[0]
                        ),
                        track_ended_at=(
                            None if degraded_association_time else track_interval[1]
                        ),
                        relative_track_started_at=(
                            relative_track_interval[0]
                            if degraded_association_time
                            and relative_track_interval is not None
                            else None
                        ),
                        relative_track_ended_at=(
                            relative_track_interval[1]
                            if degraded_association_time
                            and relative_track_interval is not None
                            else None
                        ),
                        diagnostics=(
                            {"reason": identity_failure_reason},
                    ) if identity_failure_reason else (),
                    ),
                )
        associations = {
            token: _association_with_audit_scope(
                association,
                request=request,
                track_token=token,
            )
            for token, association in associations.items()
        }
        candidates = (
            self._project_facts(
                facts,
                associations,
                behavior.member_enrichments,
                store_id=request.store_id,
                camera_id=request.camera_id,
            )
            if time_authority.publication_eligible
            else ()
        )
        source_publication_eligible = source.status == "verified" and segment_duration.status == "compatible"
        can_formally_publish = (
            source_publication_eligible
            and time_authority.publication_eligible
            and scope_contract_failure is None
        )
        publication_block_reason = scope_contract_failure or time_authority.publication_block_reason
        trusted_qualification = None
        if can_formally_publish and self.trusted_publication_gate is not None:
            trusted_qualification = self.trusted_publication_gate.assess(
                profile_resolution=self.provider_a_profile,
                device_id=request.device_id,
                source_status=source.status,
                registration_status=registration_status,
                segment_duration_status=segment_duration.status,
                presentation_timeline_status=(
                    getattr(identity_result, "presentation_timeline_status", None)
                ),
                time_authority=time_authority,
                recognition_coverage_status=(
                    getattr(identity_result, "recognition_coverage_status", None)
                ),
                historical_identity_status=(
                    getattr(identity_result, "historical_identity_status", None)
                ),
                guarded_event_time_status=(
                    getattr(identity_result, "guarded_event_time_status", None)
                ),
                runtime_face_geometry_status=(
                    getattr(identity_result, "runtime_face_geometry_status", None)
                ),
                behavior_track_tokens=(fact.customer_track_token for fact in facts),
                associations=associations,
            )
            if trusted_qualification.status != "verified":
                can_formally_publish = False
                publication_block_reason = trusted_qualification.reason
        analyzed_video_bytes: bytes | None = None
        if self.overlay_renderer is None:
            if can_formally_publish:
                can_formally_publish = False
                publication_block_reason = "overlay_renderer_unavailable"
        else:
            try:
                display_labels = self._display_labels(
                    analysis_run_id=request.analysis_run_id,
                    person_tracks=behavior.person_tracks,
                    facts=facts,
                    associations=associations,
                    enrichments=behavior.member_enrichments,
                    artifact_only=not can_formally_publish,
                    authoritative_time=time_authority.publication_eligible,
                )
                try:
                    rendered = self.overlay_renderer(
                        behavior,
                        display_labels,
                        artifact_only=not can_formally_publish,
                    )
                except TypeError:
                    if not can_formally_publish:
                        raise
                    rendered = self.overlay_renderer(behavior, display_labels)
                if isinstance(rendered, OfflineOverlayRenderResult):
                    analyzed_video_bytes = rendered.video_bytes
                    if not can_formally_publish and not rendered.artifact_only_marked:
                        raise ValueError("artifact_only_overlay_marker_missing")
                else:
                    analyzed_video_bytes = rendered
                    if not can_formally_publish:
                        raise ValueError("artifact_only_overlay_attestation_required")
                if not isinstance(analyzed_video_bytes, bytes) or not analyzed_video_bytes:
                    raise ValueError("overlay_renderer_invalid_output")
            except Exception:
                analyzed_video_bytes = None
                if can_formally_publish:
                    can_formally_publish = False
                    publication_block_reason = "overlay_render_failed"
        writer = self.artifact_store.begin(
            analysis_job_id=request.analysis_job_id, analysis_run_id=request.analysis_run_id
        )
        self._write_artifacts(
            writer, request, source, recording_interval, behavior, facts, associations, candidates,
            identity_result=identity_result,
            publication_eligible=can_formally_publish,
            analyzed_video_bytes=analyzed_video_bytes,
            absolute_event_time_status=absolute_status,
            identity_status="available" if identity_allowed else "unavailable",
            identity_failure_reason=identity_failure_reason,
            behavior_time_quality=time_authority.behavior_time_quality,
            segment_duration=segment_duration,
            trusted_publication_qualification=trusted_qualification,
        )
        artifact_only_seal_allowed = _artifact_only_seal_allowed(
            source=source,
            analyzed_video_bytes=analyzed_video_bytes,
        )
        if not can_formally_publish and not artifact_only_seal_allowed:
            reason = _first_reason(
                source.reason if source.status != "verified" else None,
                segment_duration.reason if segment_duration.status != "compatible" else None,
                publication_block_reason,
                "overlay_renderer_unavailable" if self.overlay_renderer is None else None,
            )
            return OfflinePipelineResult(
                source_video_integrity_status=source.status,
                registration_scope_status=registration_status,
                segment_duration_status=segment_duration.status,
                media_timeline_status=behavior.media_timeline_status,
                absolute_event_time_status=absolute_status,
                identity_status="unavailable" if not identity_allowed else "available",
                behavior_time_quality=time_authority.behavior_time_quality,
                publication_eligible=False,
                publication_block_reason=reason,
                artifact_directory=str(writer.staging_directory),
                artifact_manifest_hash=None,
                publication=None,
                publication_status="not_published",
                trusted_publication_status=(
                    trusted_qualification.status if trusted_qualification else None
                ),
                trusted_publication_reason=(
                    trusted_qualification.reason if trusted_qualification else None
                ),
                product_analytics_projection_status=analytics_status,
                product_analytics_projection_reason=analytics_reason,
            )
        try:
            sealed = writer.seal(
                source_video_id=source.source_video_id,
                source_content_sha256=source.content_sha256,
                normalized_facts=facts,
            )
        except OfflineArtifactError as exc:
            return OfflinePipelineResult(
                source_video_integrity_status=source.status,
                registration_scope_status=registration_status,
                segment_duration_status=segment_duration.status,
                media_timeline_status=behavior.media_timeline_status,
                absolute_event_time_status=absolute_status,
                identity_status="unavailable" if not identity_allowed else "available",
                behavior_time_quality=time_authority.behavior_time_quality,
                publication_eligible=False,
                publication_block_reason=str(exc),
                artifact_directory=str(writer.staging_directory),
                artifact_manifest_hash=None,
                publication=None,
                publication_status="not_published",
                trusted_publication_status=(
                    trusted_qualification.status if trusted_qualification else None
                ),
                trusted_publication_reason=(
                    trusted_qualification.reason if trusted_qualification else None
                ),
                product_analytics_projection_status=analytics_status,
                product_analytics_projection_reason=analytics_reason,
            )
        if self.product_analytics_enabled:
            analytics_reason = _analytics_projection_ineligibility_reason(
                source=source,
                recording_interval=recording_interval,
                registration_status=registration_status,
                time_authority=time_authority,
                scope_contract_failure=scope_contract_failure,
                facts=facts,
            )
            if analytics_reason is None:
                try:
                    self.product_analytics_writer.write(
                        facts,
                        store_id=request.store_id,
                        artifact_manifest_hash=sealed.artifact_manifest_hash,
                    )
                    analytics_status = "completed"
                except Exception:
                    analytics_status = "failed"
                    analytics_reason = "product_analytics_projection_failed"
            else:
                analytics_status = "not_eligible"
        if not can_formally_publish:
            reason = _first_reason(
                source.reason if source.status != "verified" else None,
                segment_duration.reason if segment_duration.status != "compatible" else None,
                publication_block_reason,
            )
            return OfflinePipelineResult(
                source_video_integrity_status=source.status,
                registration_scope_status=registration_status,
                segment_duration_status=segment_duration.status,
                media_timeline_status=behavior.media_timeline_status,
                absolute_event_time_status=absolute_status,
                identity_status="unavailable" if not identity_allowed else "available",
                behavior_time_quality=time_authority.behavior_time_quality,
                publication_eligible=False,
                publication_block_reason=reason,
                artifact_directory=str(sealed.run_directory),
                artifact_manifest_hash=sealed.artifact_manifest_hash,
                publication=None,
                publication_status="not_published",
                trusted_publication_status=(
                    trusted_qualification.status if trusted_qualification else None
                ),
                trusted_publication_reason=(
                    trusted_qualification.reason if trusted_qualification else None
                ),
                product_analytics_projection_status=analytics_status,
                product_analytics_projection_reason=analytics_reason,
            )
        candidates = self._project_facts(
            facts,
            associations,
            behavior.member_enrichments,
            sealed.artifact_manifest_hash,
            store_id=request.store_id,
            camera_id=request.camera_id,
        )
        prepared = PreparedOfflineBehaviorPublication.create(
            analysis_job_id=request.analysis_job_id,
            analysis_run_id=request.analysis_run_id,
            source_video_id=source.source_video_id,
            events=candidates,
        )
        publication = self.publisher.publish(
            prepared=prepared, artifacts=sealed, artifact_store=self.artifact_store,
            time_authority=time_authority,
        ) if self.publisher is not None else None
        return OfflinePipelineResult(
            source_video_integrity_status=source.status,
            registration_scope_status=registration_status,
            segment_duration_status=segment_duration.status,
            media_timeline_status=behavior.media_timeline_status,
            absolute_event_time_status=absolute_status,
            identity_status="available" if identity_allowed else "unavailable",
            behavior_time_quality=time_authority.behavior_time_quality,
            publication_eligible=True,
            publication_block_reason=None,
            artifact_directory=str(sealed.run_directory),
            artifact_manifest_hash=sealed.artifact_manifest_hash,
            publication=publication,
            publication_status=(publication.status if publication is not None else "not_published"),
            trusted_publication_status=(
                trusted_qualification.status if trusted_qualification else None
            ),
            trusted_publication_reason=(
                trusted_qualification.reason if trusted_qualification else None
            ),
            product_analytics_projection_status=analytics_status,
            product_analytics_projection_reason=analytics_reason,
        )

    def _display_labels(
        self,
        *,
        analysis_run_id,
        person_tracks,
        facts,
        associations,
        enrichments,
        artifact_only: bool,
        authoritative_time: bool,
    ) -> dict[str, str]:
        observations = _ordinal_observations(
            analysis_run_id=analysis_run_id,
            person_tracks=person_tracks,
            facts=facts,
            authoritative_time=authoritative_time,
        )
        if not observations:
            return {}
        registry = OfflineRunCustomerOrdinalRegistry.build(
            analysis_run_id=analysis_run_id,
            observations=observations,
            authoritative_time=authoritative_time,
        )
        resolver = OfflinePersonDisplayIdentityResolver()
        labels = {}
        for observation in observations:
            token = observation.track_token
            association = associations.get(token)
            if association is None:
                continue
            enrichment = enrichments.get(association.member_id) if association.member_id else None
            if isinstance(enrichment, dict):
                enrichment = MemberProfileEnrichment(**enrichment)
            labels[token] = resolver.resolve_label(OfflineDisplayIdentity(
                analysis_run_id=analysis_run_id,
                track_token=token,
                customer_ordinal=registry.ordinal_for(token),
                identity_status=association.status,
                member_id=association.member_id if association.status == "confirmed" else None,
                member_name=enrichment.member_name if association.status == "confirmed" and enrichment else None,
                artifact_only=artifact_only,
            ))
        return labels

    def _foundation(self, request, device_intervals, store_intervals):
        try:
            source = self.trusted_source_provider(request)
        except Exception:
            return _unavailable_source(request, "trusted_source_provider_failed"), None, None
        if not isinstance(source, TrustedSourceVideoResult):
            return _unavailable_source(request, "trusted_source_contract_invalid"), None, None
        if source.status != "verified":
            return source, None, None
        try:
            interval = self.recording_time_provider(request)
            if not _valid_recording_interval(interval):
                return source, None, None
            started_at, ended_at = interval.started_at_utc, interval.ended_at_utc
        except Exception:
            return source, None, None
        try:
            registration = self.registration_validator.validate(
                video_started_at=started_at, video_ended_at=ended_at,
                requested_camera_id=request.camera_id, requested_store_id=request.store_id,
                device_camera_intervals=device_intervals,
                camera_store_intervals=store_intervals,
            )
        except Exception:
            registration = RegistrationScopeValidation(
                status="unavailable",
                reason="registration_coverage_unverifiable",
                device_camera_status="unverifiable",
                camera_store_status="unverifiable",
            )
        if not isinstance(registration, RegistrationScopeValidation):
            registration = RegistrationScopeValidation(
                status="unavailable",
                reason="registration_coverage_unverifiable",
                device_camera_status="unverifiable",
                camera_store_status="unverifiable",
            )
        return source, interval, registration

    def _identity_gate_failure_reason(
        self,
        *,
        source,
        recording_interval,
        registration_status,
        registration_reason,
        segment_duration_status,
        time_authority,
        scope_contract_failure,
    ) -> str | None:
        if scope_contract_failure is not None:
            return scope_contract_failure
        if source.status != "verified":
            return source.reason or "source_video_integrity_unavailable"
        if recording_interval is None:
            return "event_time_unavailable"
        if registration_status != "available":
            return registration_reason or "registration_coverage_unverifiable"
        if segment_duration_status != "compatible":
            return "segment_duration_unavailable"
        if not time_authority.publication_eligible:
            return time_authority.publication_block_reason or "behavior_time_unavailable"
        capability_reason = self.provider_a_profile.production_eligibility_reason(
            device_id=source.canonical_manifest.get("device_id", "")
            if isinstance(source.canonical_manifest, dict)
            else ""
        )
        if capability_reason is not None:
            return capability_reason
        if self.identity_pass is None:
            return "identity_pass_unavailable"
        return None

    def _project_facts(
        self,
        facts,
        associations,
        enrichments,
        artifact_manifest_hash="v1:sha256:pending",
        *,
        store_id,
        camera_id,
    ):
        results = []
        for fact in facts:
            association = associations.get(fact.customer_track_token)
            if association is None:
                association = OfflineTrackIdentityAggregator.unavailable(
                    analysis_run_id=fact.analysis_run_id,
                    track_token=fact.customer_track_token,
                    track_started_at=fact.started_at,
                    track_ended_at=fact.ended_at,
                )
            enrichment = enrichments.get(association.member_id) if association.member_id else None
            if enrichment is not None and not isinstance(enrichment, MemberProfileEnrichment):
                enrichment = MemberProfileEnrichment(**enrichment)
            results.append(self.identity_projector.project(
                fact=fact, association=association, enrichment=enrichment,
                artifact_manifest_hash=artifact_manifest_hash,
                association_record_ref=f"track:{fact.customer_track_token}",
                store_id=store_id,
            ))
        return tuple(results)

    def _write_artifacts(
        self, writer, request, source, recording_interval, behavior, facts, associations, candidates,
        identity_result: OfflineIdentityPassResult | None,
        *, publication_eligible: bool, analyzed_video_bytes: bytes | None,
        absolute_event_time_status: str, identity_status: str,
        identity_failure_reason: str | None, behavior_time_quality: str,
        segment_duration: SegmentDurationAssessment,
        trusted_publication_qualification: Any | None,
    ):
        writer.write_json("metadata.json", _json_value({
            "analysis_job_id": request.analysis_job_id,
            "analysis_run_id": request.analysis_run_id,
            "store_id": request.store_id,
            "camera_id": request.camera_id,
            "device_id": request.device_id,
            "event_time_standard": "utc_naive",
            "media_timeline_status": behavior.media_timeline_status,
            "absolute_event_time_status": absolute_event_time_status,
            "behavior_time_quality": behavior_time_quality,
            "publication_eligible": publication_eligible,
            "identity_status": identity_status,
            "identity_failure_reason": identity_failure_reason,
            "provider_a_capability_status": self.provider_a_profile.status,
            "provider_a_unavailable_reason": self.provider_a_profile.reason,
            "provider_a_capability_profile": self.provider_a_profile.profile_snapshot(),
            "segment_duration_request_claim_untrusted": request.segment_duration_status,
            "segment_duration_assessment": segment_duration,
            "trusted_publication_qualification": trusted_publication_qualification,
            "recognition_acceptance_policy_snapshot": (
                identity_result.recognition_acceptance_policy_snapshot
                if identity_result is not None else None
            ),
            "raw_started_at": getattr(recording_interval, "raw_started_at", None),
            "raw_ended_at": getattr(recording_interval, "raw_ended_at", None),
            "timezone_offset_minutes": getattr(recording_interval, "timezone_offset_minutes", None),
            "timezone_source": getattr(recording_interval, "timezone_source", None),
            "timezone_verified": getattr(recording_interval, "timezone_verified", False),
            "normalized_started_at_utc": getattr(recording_interval, "started_at_utc", None),
            "normalized_ended_at_utc": getattr(recording_interval, "ended_at_utc", None),
            "behavior_analysis": behavior.behavior_summary,
        }))
        writer.write_json("source_manifest.json", _json_value({
            "source_video_id": source.source_video_id or "artifact-only-source",
            "source_content_sha256": source.content_sha256 or "0" * 64,
            "download_content_sha256": source.content_sha256 or "0" * 64,
            "source_video_integrity_status": source.status,
            "source_provenance": source.canonical_manifest,
            "provider_a_capability_profile": self.provider_a_profile.profile_snapshot(),
            "segment_duration_assessment": segment_duration,
            "device_id": request.device_id,
            "raw_started_at": getattr(recording_interval, "raw_started_at", None),
            "raw_ended_at": getattr(recording_interval, "raw_ended_at", None),
            "timezone_offset_minutes": getattr(recording_interval, "timezone_offset_minutes", None),
            "timezone_source": getattr(recording_interval, "timezone_source", None),
            "timezone_verified": getattr(recording_interval, "timezone_verified", False),
            "normalized_started_at_utc": getattr(recording_interval, "started_at_utc", None),
            "normalized_ended_at_utc": getattr(recording_interval, "ended_at_utc", None),
        }))
        writer.write_jsonl("person_tracks.jsonl", (_json_value(item) for item in behavior.person_tracks))
        writer.write_jsonl("product_tracks.jsonl", (_json_value(item) for item in behavior.product_tracks))
        writer.write_jsonl(
            "identity_evidence.jsonl",
            _identity_artifact_records(behavior.identity_evidence, identity_result),
        )
        writer.write_jsonl(
            "track_identity_associations.jsonl",
            (
                _json_value(associations[token])
                for token in sorted(associations)
            ),
        )
        writer.write_jsonl("raw_stage9_events.jsonl", (_json_value(item) for item in behavior.raw_stage9_events))
        writer.write_jsonl("normalized_behavior_facts.jsonl", (_json_value(item) for item in facts))
        writer.write_json(
            "behavior_summary.json",
            _safe_behavior_summary(
                candidates,
                facts=facts,
                publication_eligible=publication_eligible,
                media_timeline_status=behavior.media_timeline_status,
                absolute_event_time_status=absolute_event_time_status,
                behavior_time_quality=behavior_time_quality,
                identity_status=identity_status,
                identity_failure_reason=identity_failure_reason,
            ),
        )
        if analyzed_video_bytes is not None:
            writer.write_bytes("analyzed_video.mp4", analyzed_video_bytes)
        for path, content in behavior.keyframes.items():
            writer.write_bytes(path, content)


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise ValueError("artifact_datetime_must_be_utc_naive")
        return value.isoformat(timespec="microseconds")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return {"value": str(value)}


def _first_reason(*values):
    return next((value for value in values if value), None)


def _association_with_audit_scope(
    association: OfflineTrackIdentityAssociation,
    *,
    request: OfflineVideoAnalysisRequest,
    track_token: str,
) -> OfflineTrackIdentityAssociation:
    global_track_id = association.global_track_id
    if global_track_id is None:
        token_id = track_token.rsplit(":", 1)[-1]
        global_track_id = int(token_id) if token_id.isdigit() else None
    return replace(
        association,
        analysis_job_id=association.analysis_job_id or request.analysis_job_id,
        store_id=association.store_id or request.store_id,
        camera_id=association.camera_id or request.camera_id,
        global_track_id=global_track_id,
    )


def _artifact_only_seal_allowed(
    *, source: TrustedSourceVideoResult, analyzed_video_bytes: bytes | None
) -> bool:
    """Allow a proven source to retain sealed artifact-only diagnostics.

    Sealing records immutable evidence; it does not grant publication
    authority.  Both hashed Golden fixtures and source-verified P6S recordings
    may therefore be retained after a later dynamic publication gate fails.
    """

    return bool(
        source.status in {"untrusted_test_fixture", "verified"}
        and isinstance(source.source_video_id, str)
        and source.source_video_id
        and isinstance(source.content_sha256, str)
        and len(source.content_sha256) == 64
        and isinstance(analyzed_video_bytes, bytes)
        and analyzed_video_bytes
    )


def _analytics_projection_ineligibility_reason(
    *,
    source: TrustedSourceVideoResult,
    recording_interval: Any | None,
    registration_status: str,
    time_authority: OfflineBehaviorTimeAuthority,
    scope_contract_failure: str | None,
    facts: Iterable[OfflineNormalizedBehaviorFact],
) -> str | None:
    if source.status != "verified":
        return "analytics_source_not_verified"
    if recording_interval is None:
        return "analytics_authoritative_time_unavailable"
    if registration_status != "available":
        return "analytics_registration_scope_not_verified"
    if scope_contract_failure is not None:
        return "analytics_scope_contract_invalid"
    if (
        not time_authority.publication_eligible
        or time_authority.behavior_time_quality != "authoritative"
    ):
        return "analytics_authoritative_time_unavailable"
    for fact in facts:
        if (
            not isinstance(fact.started_at, datetime)
            or not isinstance(fact.ended_at, datetime)
            or fact.started_at.tzinfo is not None
            or fact.ended_at.tzinfo is not None
            or fact.ended_at < fact.started_at
        ):
            return "analytics_authoritative_time_unavailable"
    return None


def _unavailable_source(
    request: OfflineVideoAnalysisRequest, reason: str
) -> TrustedSourceVideoResult:
    indexed_size = (
        request.recording.indexed_size_bytes if request.recording is not None else 0
    )
    return TrustedSourceVideoResult(
        status="unavailable",
        source_video_id=None,
        content_sha256=None,
        indexed_size_bytes=indexed_size,
        downloaded_size_bytes=None,
        reason=reason,
        canonical_manifest=None,
    )


def _valid_recording_interval(value: Any) -> bool:
    try:
        return all((
            value.timezone_verified is True,
            isinstance(value.timezone_offset_minutes, int),
            not isinstance(value.timezone_offset_minutes, bool),
            isinstance(value.timezone_source, str),
            bool(value.timezone_source),
            value.event_time_standard == "utc_naive",
            _utc_naive_datetime(value.started_at_utc),
            _utc_naive_datetime(value.ended_at_utc),
            value.ended_at_utc > value.started_at_utc,
            _utc_naive_datetime(value.raw_started_at),
            _utc_naive_datetime(value.raw_ended_at),
            value.raw_ended_at > value.raw_started_at,
        ))
    except (AttributeError, TypeError):
        return False


def _track_intervals(
    person_tracks: Iterable[Any],
    facts: Iterable[OfflineNormalizedBehaviorFact],
) -> dict[str, tuple[datetime, datetime]]:
    intervals: dict[str, tuple[datetime, datetime]] = {}

    def add(token: Any, started_at: Any, ended_at: Any) -> None:
        if not isinstance(token, str) or not token:
            return
        if not _utc_naive_datetime(started_at) or not _utc_naive_datetime(ended_at):
            return
        prior = intervals.get(token)
        intervals[token] = (
            started_at if prior is None else min(prior[0], started_at),
            ended_at if prior is None else max(prior[1], ended_at),
        )

    for track in person_tracks:
        observed_at = _field(track, "observed_at")
        add(
            _field(track, "track_token"),
            _field(track, "valid_from") or _field(track, "started_at") or observed_at,
            _field(track, "valid_to") or _field(track, "ended_at") or observed_at,
        )
    for fact in facts:
        add(fact.customer_track_token, fact.started_at, fact.ended_at)
    return intervals


def _relative_track_intervals(
    person_tracks: Iterable[Any],
    facts: Iterable[OfflineNormalizedBehaviorFact],
) -> dict[str, tuple[float, float]]:
    intervals: dict[str, tuple[float, float]] = {}

    def add(token: Any, started_at: Any, ended_at: Any) -> None:
        if not isinstance(token, str) or not token:
            return
        if not _nonnegative_seconds(started_at) or not _nonnegative_seconds(ended_at):
            return
        if ended_at < started_at:
            return
        started = float(started_at)
        ended = float(ended_at)
        prior = intervals.get(token)
        intervals[token] = (
            started if prior is None else min(prior[0], started),
            ended if prior is None else max(prior[1], ended),
        )

    for track in person_tracks:
        source_pts = _field(track, "source_pts_seconds")
        add(_field(track, "track_token"), source_pts, source_pts)
    for fact in facts:
        add(
            fact.customer_track_token,
            fact.relative_started_at_seconds,
            fact.relative_ended_at_seconds,
        )
    return intervals


def _ordinal_observations(
    *,
    analysis_run_id: str,
    person_tracks: Iterable[Any],
    facts: Iterable[OfflineNormalizedBehaviorFact],
    authoritative_time: bool,
) -> tuple[CustomerOrdinalObservation, ...]:
    values: dict[str, CustomerOrdinalObservation] = {}

    def add(
        token: Any,
        observed_at: Any,
        sample_seq: Any,
        source_seq: Any,
    ) -> None:
        if not isinstance(token, str) or not token:
            return
        if authoritative_time:
            if not _utc_naive_datetime(observed_at):
                return
            current = values.get(token)
            if current is None or observed_at < current.first_valid_observed_at:
                values[token] = CustomerOrdinalObservation(
                    analysis_run_id, token, observed_at, None, None
                )
            return
        if not _nonnegative_int(sample_seq) or not _nonnegative_int(source_seq):
            return
        current = values.get(token)
        candidate = (sample_seq, source_seq)
        if current is None or candidate < (
            current.first_valid_sample_seq, current.first_valid_source_seq
        ):
            values[token] = CustomerOrdinalObservation(
                analysis_run_id, token, None, sample_seq, source_seq
            )

    for track in person_tracks:
        if _field(track, "role") not in {None, "customer"}:
            continue
        add(
            _field(track, "track_token"),
            _field(track, "observed_at"),
            _field(track, "sample_seq"),
            _field(track, "source_seq"),
        )
    if authoritative_time:
        for fact in facts:
            add(fact.customer_track_token, fact.started_at, None, None)
    return tuple(values.values())


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _utc_naive_datetime(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is None


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _nonnegative_seconds(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
        and value != float("inf")
    )


def _valid_acceptance_policy_snapshot(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = (
        "policy_id",
        "policy_version",
        "similarity_threshold",
        "policy_hash",
    )
    return all(
        isinstance(value.get(field), str) and bool(value[field])
        for field in required
    )


def _identity_artifact_records(
    behavior_records: Iterable[Any],
    identity_result: OfflineIdentityPassResult | None,
) -> Iterable[dict[str, Any]]:
    for record in behavior_records:
        yield {
            "record_type": "behavior_identity_diagnostic",
            "payload": _json_value(record),
        }
    if identity_result is None:
        return
    yield {
        "record_type": "recognition_acceptance_policy_snapshot",
        "payload": _json_value(
            identity_result.recognition_acceptance_policy_snapshot
        ),
    }
    for record in identity_result.page_audits:
        yield {"record_type": "recognition_page_audit", "payload": _json_value(record)}
    for record in identity_result.record_audits:
        yield {"record_type": "recognition_record_audit", "payload": _json_value(record)}
    for record in identity_result.accepted_evidence:
        yield {
            "record_type": "accepted_recognition_evidence",
            "payload": _json_value(record),
        }


def _safe_behavior_summary(
    candidates,
    *,
    facts=None,
    publication_eligible: bool,
    media_timeline_status: str,
    absolute_event_time_status: str,
    behavior_time_quality: str,
    identity_status: str,
    identity_failure_reason: str | None,
) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_member: dict[str, int] = {}
    summary_facts = tuple(candidates) if facts is None else tuple(facts)
    for fact in summary_facts:
        by_type[fact.event_type] = by_type.get(fact.event_type, 0) + 1
    for event in candidates:
        if event.member_id is not None:
            by_member[event.member_id] = by_member.get(event.member_id, 0) + 1
    return {
        "summary_version": "offline_behavior_summary_v1",
        "summary_authority": "formal_candidate" if publication_eligible else "artifact_only",
        "publication_eligible": publication_eligible,
        "media_timeline_status": media_timeline_status,
        "absolute_event_time_status": absolute_event_time_status,
        "behavior_time_quality": behavior_time_quality,
        "identity_status": identity_status,
        "identity_failure_reason": identity_failure_reason,
        "event_count": len(summary_facts),
        "events_by_type": by_type,
        "events_by_confirmed_member_id": by_member,
    }
