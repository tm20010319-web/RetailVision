"""Offline adapters around the frozen same-camera association policy."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable, Optional

from agent.identity.camera_track_identity import SameCameraRecognitionBinder, TrackSnapshot
from agent.identity.face_spatial_evidence import FaceSpatialEvidence
from agent.offline.offline_media import (
    GuardedInteriorEventTimeAssessment,
    assess_guarded_interior_event_time,
)
from agent.offline.offline_recognition_provider import (
    OfflineRecognitionEvidence,
    RecognitionRecordQueryResult,
)


@dataclass(frozen=True)
class OfflineSpatialGeometry:
    decoded_source_frame_size: Optional[tuple[int, int]]
    analysis_frame_size: Optional[tuple[int, int]]
    full_frame_resize_verified: bool
    cropped: bool
    mirrored: bool
    rotated: bool
    padded: bool
    transform_type: Optional[str] = None
    scale_x: Optional[float] = None
    scale_y: Optional[float] = None
    offset_x: float = 0.0
    offset_y: float = 0.0
    policy_id: Optional[str] = None
    policy_version: Optional[str] = None
    validation_report_ref: Optional[str] = None
    device_id: Optional[str] = None


def map_face_bbox_to_recording_frame(
    face: FaceSpatialEvidence, geometry: OfflineSpatialGeometry
) -> Optional[FaceSpatialEvidence]:
    """Map normalized FaceReco coordinates through a verified frame transform."""
    if (
        geometry.transform_type != "full_frame_resize"
        or not geometry.full_frame_resize_verified
        or face.source_frame_size != geometry.decoded_source_frame_size
        or geometry.analysis_frame_size is None
        or geometry.scale_x is None
        or geometry.scale_y is None
        or geometry.scale_x <= 0
        or geometry.scale_y <= 0
    ):
        return None
    sw, sh = face.source_frame_size
    tw, th = geometry.analysis_frame_size
    x1, y1, x2, y2 = face.bbox
    px = (x1 * sw) * geometry.scale_x + geometry.offset_x
    py = (y1 * sh) * geometry.scale_y + geometry.offset_y
    qx = (x2 * sw) * geometry.scale_x + geometry.offset_x
    qy = (y2 * sh) * geometry.scale_y + geometry.offset_y
    mapped = (px / tw, py / th, qx / tw, qy / th)
    if not (0 <= mapped[0] < mapped[2] <= 1 and 0 <= mapped[1] < mapped[3] <= 1):
        return None
    return replace(face, bbox=mapped, source_frame_size=(tw, th), quality_status="verified")


@dataclass(frozen=True)
class SpatialGeometryCompatibility:
    status: str
    reason: Optional[str]
    transform_type: Optional[str]


class OfflineSpatialGeometryValidator:
    """Admit only verified normalized full-frame resize geometry in V1."""

    def validate(
        self,
        *,
        face: FaceSpatialEvidence,
        geometry: OfflineSpatialGeometry,
    ) -> SpatialGeometryCompatibility:
        if face.coordinate_space != "normalized_full_frame" or not face.normalized:
            return SpatialGeometryCompatibility(
                "invalid", "unsupported_coordinate_space", None
            )
        if face.source_frame_size is None or geometry.decoded_source_frame_size is None:
            return SpatialGeometryCompatibility(
                "unavailable", "source_frame_metadata_missing", None
            )
        if geometry.analysis_frame_size is None:
            return SpatialGeometryCompatibility(
                "unavailable", "analysis_frame_metadata_missing", None
            )
        if any(value <= 0 for value in (*geometry.decoded_source_frame_size, *geometry.analysis_frame_size)):
            return SpatialGeometryCompatibility(
                "invalid", "frame_size_invalid", None
            )
        if face.source_frame_size != geometry.decoded_source_frame_size:
            return SpatialGeometryCompatibility(
                "unavailable", "face_recording_transform_unverified", None
            )
        if geometry.cropped:
            return SpatialGeometryCompatibility("invalid", "crop_detected", None)
        if geometry.mirrored:
            return SpatialGeometryCompatibility("invalid", "mirror_detected", None)
        if geometry.rotated:
            return SpatialGeometryCompatibility("invalid", "rotation_unsupported", None)
        if geometry.padded:
            return SpatialGeometryCompatibility("invalid", "padding_unsupported", None)
        if not geometry.full_frame_resize_verified or geometry.transform_type not in {None, "full_frame_resize"}:
            return SpatialGeometryCompatibility(
                "unavailable", "full_frame_transform_unverified", None
            )
        if geometry.transform_type == "full_frame_resize":
            source_width, source_height = geometry.decoded_source_frame_size
            target_width, target_height = geometry.analysis_frame_size
            if (
                geometry.scale_x is None
                or geometry.scale_y is None
                or not math.isclose(geometry.scale_x, target_width / source_width, rel_tol=0, abs_tol=1e-9)
                or not math.isclose(geometry.scale_y, target_height / source_height, rel_tol=0, abs_tol=1e-9)
                or geometry.offset_x != 0.0
                or geometry.offset_y != 0.0
            ):
                return SpatialGeometryCompatibility(
                    "unavailable", "transform_metadata_mismatch", None
                )
        return SpatialGeometryCompatibility("compatible", None, "full_frame_resize")


@dataclass(frozen=True)
class InboxSpatialEvidenceRecord:
    canonical_event_id: str
    device_id: str
    occurred_at: datetime
    group_id: int
    person_id: int
    unique_id: str
    face_spatial_evidence: Optional[dict]


@dataclass(frozen=True)
class SpatialEnrichmentResult:
    status: str
    face: Optional[FaceSpatialEvidence]
    reason: Optional[str]


class ExactEventSpatialEnricher:
    """Enrich only the exact QueryRecord event; never scan the Inbox by time."""

    def enrich(
        self,
        *,
        evidence: OfflineRecognitionEvidence,
        records: Iterable[InboxSpatialEvidenceRecord],
        expected_camera_id: str,
    ) -> SpatialEnrichmentResult:
        exact = tuple(
            item
            for item in records
            if item.canonical_event_id == evidence.canonical_event_id
        )
        if not exact:
            return SpatialEnrichmentResult("absent", None, "exact_inbox_event_missing")
        if any(not _metadata_matches(item, evidence) for item in exact):
            return SpatialEnrichmentResult("conflict", None, "exact_event_metadata_mismatch")

        faces: list[FaceSpatialEvidence] = []
        canonical_faces: set[str] = set()
        for item in exact:
            if item.face_spatial_evidence is None:
                continue
            face = FaceSpatialEvidence.from_safe_dict(item.face_spatial_evidence)
            if face is None:
                return SpatialEnrichmentResult("invalid", None, "face_spatial_evidence_invalid")
            if face.occurred_at is not None and face.occurred_at != evidence.occurred_at:
                return SpatialEnrichmentResult("conflict", None, "face_event_time_mismatch")
            if face.camera_id is not None and face.camera_id != expected_camera_id:
                return SpatialEnrichmentResult("conflict", None, "face_camera_mismatch")
            if face.camera_id is None:
                face = replace(face, camera_id=expected_camera_id)
            canonical = json.dumps(
                face.to_safe_dict(), sort_keys=True, separators=(",", ":")
            )
            canonical_faces.add(canonical)
            faces.append(face)
        if not faces:
            return SpatialEnrichmentResult("absent", None, "face_spatial_evidence_absent")
        if len(canonical_faces) != 1:
            return SpatialEnrichmentResult("conflict", None, "conflicting_face_spatial_evidence")
        return SpatialEnrichmentResult("matched", faces[0], None)


@dataclass(frozen=True)
class OfflineEventAssociationResult:
    status: str
    member_id: Optional[str]
    track_token: Optional[str]
    method: Optional[str]
    reason: Optional[str]
    confidence: Optional[float]
    candidate_count: int
    spatial_diagnostics: tuple[dict, ...]
    candidate_member_id: Optional[str] = None

    def as_track_evidence(self, *, evidence_id: Optional[str] = None):
        """Convert to the frozen track aggregator's event contract."""
        from agent.offline.offline_identity import OfflineEventTrackAssociation

        return OfflineEventTrackAssociation(
            member_id=self.member_id,
            candidate_member_id=self.candidate_member_id,
            status=self.status,
            evidence_id=evidence_id,
            method=self.method,
            confidence=self.confidence,
        )


@dataclass(frozen=True)
class GuardedOfflineEventAssociation:
    """Restricted-audit result for one Evidence-to-Track assessment.

    This record intentionally retains the guard assessment next to the event
    decision.  It is not a public member projection and a failed guard never
    reaches the same-camera binder.
    """

    evidence_id: str
    canonical_event_id: str
    source_event_id: Optional[str]
    status: str
    member_id: Optional[str]
    track_token: Optional[str]
    reason: Optional[str]
    guard: GuardedInteriorEventTimeAssessment
    association: Optional[OfflineEventAssociationResult]


class GuardedOfflineIdentityAssociator:
    """Apply the B2.6 evidence gates before the frozen same-camera binder.

    The durable provider owns coverage, recognition acceptance, and historical
    resolution.  This adapter consumes only its complete accepted result, then
    applies the guarded-interior event-time policy before attempting temporal
    Primary Snapshot selection or spatial association.
    """

    def __init__(
        self,
        *,
        associator: Optional["OfflineSameCameraIdentityAssociator"] = None,
        geometry_validator: Optional[OfflineSpatialGeometryValidator] = None,
    ) -> None:
        self.associator = associator or OfflineSameCameraIdentityAssociator()
        self.geometry_validator = geometry_validator or OfflineSpatialGeometryValidator()

    def associate(
        self,
        *,
        recognition_result: RecognitionRecordQueryResult,
        recording_start: datetime,
        recording_end: datetime,
        presentation_timeline_status: str,
        store_id: str,
        camera_id: str,
        analysis_run_id: str,
        tracks: Iterable[TrackSnapshot],
        spatial_geometry: Optional[OfflineSpatialGeometry],
    ) -> tuple[GuardedOfflineEventAssociation, ...]:
        """Return restricted diagnostics; unavailable inputs never invoke binder."""
        evidence = tuple(recognition_result.accepted_evidence)
        if (
            recognition_result.status != "complete"
            or not recognition_result.coverage_complete
        ):
            return tuple(
                self._unavailable(
                    item,
                    recording_start=recording_start,
                    recording_end=recording_end,
                    presentation_timeline_status=presentation_timeline_status,
                    reason=recognition_result.failure_reason
                    or "recognition_provider_unavailable",
                )
                for item in evidence
            )

        snapshots = tuple(tracks)
        results: list[GuardedOfflineEventAssociation] = []
        for item in evidence:
            guard = assess_guarded_interior_event_time(
                event_time=item.occurred_at,
                recording_start=recording_start,
                recording_end=recording_end,
                presentation_timeline_status=presentation_timeline_status,
            )
            if item.historical_mapping_status != "resolved" or not item.member_id:
                results.append(GuardedOfflineEventAssociation(
                    evidence_id=item.evidence_id,
                    canonical_event_id=item.canonical_event_id,
                    source_event_id=item.source_event_id,
                    status="unavailable",
                    member_id=None,
                    track_token=None,
                    reason="historical_mapping_unavailable",
                    guard=guard,
                    association=None,
                ))
                continue
            if guard.status != "verified":
                results.append(GuardedOfflineEventAssociation(
                    evidence_id=item.evidence_id,
                    canonical_event_id=item.canonical_event_id,
                    source_event_id=item.source_event_id,
                    status="unavailable",
                    member_id=None,
                    track_token=None,
                    reason=guard.reason,
                    guard=guard,
                    association=None,
                ))
                continue

            face = FaceSpatialEvidence.from_safe_dict(item.face_spatial_evidence)
            compatibility = (
                self.geometry_validator.validate(face=face, geometry=spatial_geometry)
                if face is not None and spatial_geometry is not None
                else None
            )
            if (
                face is not None
                and spatial_geometry is not None
                and spatial_geometry.transform_type is not None
                and compatibility is not None
            ):
                mapped_face = map_face_bbox_to_recording_frame(face, spatial_geometry)
                if compatibility.status == "compatible" and mapped_face is None:
                    compatibility = SpatialGeometryCompatibility(
                        "unavailable", "face_recording_transform_mapping_invalid", None
                    )
                elif mapped_face is not None:
                    face = mapped_face
            association = self.associator.associate(
                evidence=item,
                store_id=store_id,
                camera_id=camera_id,
                analysis_run_id=analysis_run_id,
                tracks=snapshots,
                face=face,
                spatial_geometry=compatibility,
            )
            results.append(GuardedOfflineEventAssociation(
                evidence_id=item.evidence_id,
                canonical_event_id=item.canonical_event_id,
                source_event_id=item.source_event_id,
                status=association.status,
                member_id=association.member_id,
                track_token=association.track_token,
                reason=association.reason,
                guard=guard,
                association=association,
            ))
        return tuple(results)

    @staticmethod
    def _unavailable(
        evidence: OfflineRecognitionEvidence,
        *,
        recording_start: datetime,
        recording_end: datetime,
        presentation_timeline_status: str,
        reason: str,
    ) -> GuardedOfflineEventAssociation:
        guard = assess_guarded_interior_event_time(
            event_time=evidence.occurred_at,
            recording_start=recording_start,
            recording_end=recording_end,
            presentation_timeline_status=presentation_timeline_status,
        )
        return GuardedOfflineEventAssociation(
            evidence_id=evidence.evidence_id,
            canonical_event_id=evidence.canonical_event_id,
            source_event_id=evidence.source_event_id,
            status="unavailable",
            member_id=None,
            track_token=None,
            reason=reason,
            guard=guard,
            association=None,
        )


class GuardedOfflineIdentityPass:
    """Adapter suitable for the existing ``OfflineIdentityPassPort`` seam.

    Callers provide Stage5 snapshots through ``track_supplier``; no second
    track or identity model is created.  The returned associations are the
    existing ``OfflineTrackIdentityAssociation`` records consumed by the
    pipeline and projector.
    """

    def __init__(
        self,
        *,
        provider: object,
        track_supplier: object,
        geometry: Optional[OfflineSpatialGeometry] = None,
        presentation_timeline_status: str = "verified",
        associator: Optional[GuardedOfflineIdentityAssociator] = None,
    ) -> None:
        self.provider = provider
        self.track_supplier = track_supplier
        self.geometry = geometry
        self.presentation_timeline_status = presentation_timeline_status
        self.associator = associator or GuardedOfflineIdentityAssociator()

    def __call__(self, request, video_started_at: datetime, video_ended_at: datetime):
        from agent.offline.offline_identity import OfflineTrackIdentityAggregator
        from agent.offline.offline_video_models import OfflineIdentityPassResult

        query = self.provider.query(video_started_at, video_ended_at)
        supplied = self.track_supplier(request)
        tracks = tuple(_coerce_track_snapshot(item, request) for item in supplied)
        tracks = tuple(item for item in tracks if item is not None)
        guarded = self.associator.associate(
            recognition_result=query,
            recording_start=video_started_at,
            recording_end=video_ended_at,
            presentation_timeline_status=self.presentation_timeline_status,
            store_id=request.store_id,
            camera_id=request.camera_id,
            analysis_run_id=request.analysis_run_id,
            tracks=tracks,
            spatial_geometry=self.geometry,
        )
        by_track: dict[str, list] = {}
        unavailable_reason: Optional[str] = None
        for item in guarded:
            if item.association is None or item.track_token is None:
                if item.status == "unavailable":
                    unavailable_reason = item.reason or "identity_association_unavailable"
                continue
            by_track.setdefault(item.track_token, []).append(
                item.association.as_track_evidence(evidence_id=item.evidence_id)
            )
        associations = {}
        for token in {item.track_token for item in tracks}:
            matching = [item for item in tracks if item.track_token == token]
            started = min(item.observed_at for item in matching)
            ended = max(item.observed_at for item in matching)
            associations[token] = OfflineTrackIdentityAggregator().aggregate(
                analysis_run_id=request.analysis_run_id,
                track_token=token,
                track_started_at=started,
                track_ended_at=ended,
                associations=tuple(by_track.get(token, ())),
            )
        if query.status != "complete" or not query.coverage_complete:
            associations = {
                token: OfflineTrackIdentityAggregator.unavailable(
                    analysis_run_id=request.analysis_run_id,
                    track_token=token,
                    track_started_at=None,
                    track_ended_at=None,
                    diagnostics=({"reason": query.failure_reason or "recognition_provider_unavailable"},),
                )
                for token in associations
            }
        elif unavailable_reason is not None:
            associations = {
                token: OfflineTrackIdentityAggregator.unavailable(
                    analysis_run_id=request.analysis_run_id,
                    track_token=token,
                    track_started_at=None,
                    track_ended_at=None,
                    diagnostics=({"reason": unavailable_reason},),
                )
                for token in associations
            }
        return OfflineIdentityPassResult(
            recognition_coverage_status=("complete" if query.status == "complete" and query.coverage_complete else "unavailable"),
            associations=associations,
            failure_reason=query.failure_reason,
            accepted_evidence=query.accepted_evidence,
            record_audits=query.record_audits,
            page_audits=query.page_audits,
            recognition_acceptance_policy_snapshot=_acceptance_policy_snapshot(self.provider),
            historical_identity_status=(
                "resolved"
                if query.status == "complete"
                and all(item.historical_mapping_status == "resolved" for item in query.accepted_evidence)
                else "unavailable"
            ),
            guarded_event_time_status=(
                "verified"
                if any(
                    item.status == "confirmed" and item.guard.status == "verified"
                    for item in guarded
                )
                else "unavailable"
            ),
            runtime_face_geometry_status=(
                "compatible"
                if any(
                    item.status == "confirmed"
                    and item.association is not None
                    and item.association.method == "spatial"
                    for item in guarded
                )
                else "unavailable"
            ),
            presentation_timeline_status=self.presentation_timeline_status,
        )


def _coerce_track_snapshot(value: object, request) -> Optional[TrackSnapshot]:
    if isinstance(value, TrackSnapshot):
        return value
    if not isinstance(value, dict):
        return None
    try:
        observed_at = value.get("observed_at")
        if not isinstance(observed_at, datetime):
            return None
        bbox = tuple(float(item) for item in value["bbox"])
        frame_size = value.get("frame_size")
        return TrackSnapshot(
            store_id=value.get("store_id", request.store_id),
            camera_id=value.get("camera_id", request.camera_id),
            runtime_id=value.get("runtime_id", request.analysis_run_id),
            track_id=int(value.get("track_id", value.get("global_track_id"))),
            track_token=value["track_token"],
            observed_at=observed_at,
            bbox=bbox,
            confidence=float(value.get("confidence", 0.0)),
            role=value.get("role", "customer"),
            stable_frames=int(value.get("stable_frames", 3)),
            is_frame_clipped=bool(value.get("is_frame_clipped", False)),
            coordinate_space=value.get("coordinate_space", "normalized_full_frame"),
            frame_size=tuple(frame_size) if frame_size else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _acceptance_policy_snapshot(provider: object) -> dict:
    policy = getattr(provider, "acceptance_policy", None)
    if policy is None:
        return {}
    return {
        "policy_id": getattr(policy, "policy_id", ""),
        "policy_version": getattr(policy, "policy_version", ""),
        "similarity_threshold": getattr(policy, "similarity_threshold", ""),
        "policy_hash": getattr(policy, "policy_hash", ""),
    }


class OfflineSameCameraIdentityAssociator:
    """Map one accepted Recognition Evidence to one run-scoped Person Track."""

    def __init__(
        self, binder: Optional[SameCameraRecognitionBinder] = None
    ) -> None:
        self.binder = binder or SameCameraRecognitionBinder(
            association_window_seconds=1.0
        )

    def associate(
        self,
        *,
        evidence: OfflineRecognitionEvidence,
        store_id: str,
        camera_id: str,
        analysis_run_id: str,
        tracks: Iterable[TrackSnapshot],
        face: Optional[FaceSpatialEvidence],
        spatial_geometry: Optional[SpatialGeometryCompatibility],
    ) -> OfflineEventAssociationResult:
        snapshots = tuple(tracks)
        primary_snapshots = _primary_snapshots(
            snapshots,
            reference_time=evidence.occurred_at,
            store_id=store_id,
            camera_id=camera_id,
            runtime_id=analysis_run_id,
            binder=self.binder,
        )
        usable_face = (
            face
            if face is not None
            and spatial_geometry is not None
            and spatial_geometry.status == "compatible"
            else None
        )
        spatial_clock_fallback = usable_face is not None and usable_face.occurred_at is None
        if spatial_clock_fallback:
            usable_face = replace(usable_face, occurred_at=evidence.occurred_at)
        decision = self.binder.evaluate(
            store_id=store_id,
            camera_id=camera_id,
            runtime_id=analysis_run_id,
            effective_occurred_at=evidence.occurred_at,
            reference_time=(
                usable_face.occurred_at
                if usable_face is not None and usable_face.occurred_at is not None
                else evidence.occurred_at
            ),
            # The offline Primary is selected globally by recognition time.
            # Geometry is deliberately evaluated only after this selection.
            tracks=primary_snapshots,
            face=usable_face,
            window_complete=True,
            watermark_at=None,
        )
        status = {
            "confirmed": "confirmed",
            "probable": "probable",
            "ambiguous": "ambiguous",
            "no_track_match": "unmatched",
        }.get(decision.status, "unmatched")
        has_member_candidate = status in {"confirmed", "probable"}
        diagnostics = decision.spatial_diagnostics + ({
            "primary_snapshot_selection": "minimum_absolute_temporal_delta",
            "primary_snapshot_observed_at": (
                primary_snapshots[0].observed_at.isoformat()
                if primary_snapshots else None
            ),
            "primary_time_delta_seconds": (
                abs((primary_snapshots[0].observed_at - evidence.occurred_at).total_seconds())
                if primary_snapshots else None
            ),
        },)
        if spatial_clock_fallback:
            diagnostics += ({
                "spatial_association_clock_source": "recognition_effective_time",
                "face_event_time_status": "missing",
            },)
        return OfflineEventAssociationResult(
            status=status,
            member_id=evidence.member_id if status == "confirmed" else None,
            candidate_member_id=evidence.member_id if has_member_candidate else None,
            track_token=decision.track_token if has_member_candidate else None,
            method=decision.reason,
            reason=decision.reason,
            confidence=evidence.similarity if has_member_candidate else None,
            candidate_count=decision.candidate_count,
            spatial_diagnostics=diagnostics,
        )


def _primary_snapshots(
    tracks: tuple[TrackSnapshot, ...], *, reference_time: datetime,
    store_id: str, camera_id: str, runtime_id: str,
    binder: SameCameraRecognitionBinder,
) -> tuple[TrackSnapshot, ...]:
    """Keep every legal Track observed at the globally closest snapshot time."""
    eligible = tuple(
        item for item in tracks
        if binder._eligible(item, store_id, camera_id, runtime_id, reference_time)
    )
    if not eligible:
        return ()
    closest_delta = min(
        abs((item.observed_at - reference_time).total_seconds()) for item in eligible
    )
    return tuple(
        item for item in eligible
        if abs((item.observed_at - reference_time).total_seconds()) == closest_delta
    )


def _metadata_matches(
    record: InboxSpatialEvidenceRecord,
    evidence: OfflineRecognitionEvidence,
) -> bool:
    return all(
        (
            record.device_id == evidence.device_id,
            record.occurred_at == evidence.occurred_at,
            record.group_id == evidence.group_id,
            record.person_id == evidence.person_id,
            record.unique_id == evidence.unique_id,
        )
    )
