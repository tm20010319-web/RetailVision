"""Event-time historical member resolution and offline Track aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional, Protocol


class HistoricalIdentityIntegrityError(RuntimeError):
    """The authoritative member principal cannot support a history revision."""


@dataclass(frozen=True)
class HistoricalIdentityRevisionData:
    revision_id: str
    device_id: str
    group_id: Optional[int]
    person_id: Optional[int]
    unique_id: str
    member_id: str
    effective_from: datetime
    effective_to: Optional[datetime]
    mapping_enabled: bool
    mapping_sync_status: str
    member_principal_exists: bool
    member_principal_active: bool


@dataclass(frozen=True)
class HistoricalIdentityResolution:
    status: str
    member_id: Optional[str]
    revision_ids: tuple[str, ...]
    reason: Optional[str]


class HistoricalIdentityResolver:
    """Resolve one device identity through immutable Event-Time revisions."""

    def __init__(
        self,
        *,
        history_available_from: datetime,
        revisions: Iterable[HistoricalIdentityRevisionData],
    ) -> None:
        if not isinstance(history_available_from, datetime):
            raise ValueError("history_available_from_required")
        self.history_available_from = history_available_from
        self.revisions = tuple(revisions)

    def resolve_at(
        self,
        *,
        device_id: str,
        group_id: Optional[int],
        person_id: Optional[int],
        unique_id: str,
        occurred_at: datetime,
    ) -> HistoricalIdentityResolution:
        if occurred_at < self.history_available_from:
            return HistoricalIdentityResolution(
                "unverifiable", None, (), "event_precedes_history_bootstrap"
            )
        matches = tuple(
            revision
            for revision in self.revisions
            if _same_device_identity(
                revision,
                device_id=device_id,
                group_id=group_id,
                person_id=person_id,
                unique_id=unique_id,
            )
            and revision.effective_from <= occurred_at
            and (
                revision.effective_to is None
                or occurred_at < revision.effective_to
            )
        )
        if not matches:
            return HistoricalIdentityResolution(
                "unmapped", None, (), "mapping_not_found_at_event_time"
            )
        eligible = tuple(item for item in matches if _eligible(item))
        revision_ids = tuple(sorted(item.revision_id for item in matches))
        if len(matches) > 1 or len(eligible) > 1:
            return HistoricalIdentityResolution(
                "conflict", None, revision_ids, "historical_revision_overlap"
            )
        if not eligible:
            return HistoricalIdentityResolution(
                "unmapped", None, revision_ids, "identity_not_eligible_at_event_time"
            )
        return HistoricalIdentityResolution(
            "resolved", eligible[0].member_id, revision_ids, None
        )


class HistoricalIdentityRevisionRepositoryPort(Protocol):
    def apply_mapping_revision(self, mapping: Any, *, effective_from: datetime) -> None: ...

    def apply_member_principal_revision(
        self, *, member_id: str, active: bool, effective_from: datetime
    ) -> None: ...


class HistoricalIdentityRevisionService:
    """Application seam for recording an Offline-auditable mapping change."""

    def __init__(self, repository: HistoricalIdentityRevisionRepositoryPort) -> None:
        self.repository = repository

    def apply_mapping_revision(self, mapping: Any, *, effective_from: datetime) -> None:
        self.repository.apply_mapping_revision(mapping, effective_from=effective_from)

    def apply_member_principal_revision(
        self, *, member_id: str, active: bool, effective_from: datetime
    ) -> None:
        self.repository.apply_member_principal_revision(
            member_id=member_id, active=active, effective_from=effective_from
        )


@dataclass(frozen=True)
class OfflineEventTrackAssociation:
    member_id: Optional[str]
    status: str
    evidence_id: Optional[str] = None
    method: Optional[str] = None
    confidence: Optional[float] = None
    candidate_member_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in {
            "confirmed",
            "probable",
            "ambiguous",
            "unmatched",
            "conflict",
            "unavailable",
        }:
            raise ValueError("event_track_association_status_invalid")
        candidate = self.candidate_member_id or self.member_id
        if self.status in {"confirmed", "probable"} and not candidate:
            raise ValueError("member_candidate_required")


@dataclass(frozen=True)
class OfflineTrackIdentityAssociation:
    analysis_run_id: str
    track_token: str
    status: str
    member_id: Optional[str]
    candidate_member_id: Optional[str]
    valid_from: Optional[datetime]
    valid_to: Optional[datetime]
    evidence_ids: tuple[str, ...]
    method: Optional[str]
    confidence: Optional[float]
    relative_valid_from_seconds: Optional[float] = None
    relative_valid_to_seconds: Optional[float] = None
    analysis_job_id: Optional[str] = None
    store_id: Optional[str] = None
    camera_id: Optional[str] = None
    global_track_id: Optional[int] = None
    canonical_event_ids: tuple[str, ...] = ()
    candidate_member_ids: tuple[str, ...] = ()
    diagnostics: tuple[dict, ...] = ()


class OfflineTrackIdentityAggregator:
    """Aggregate event-level evidence without voting probable into confirmed."""

    def aggregate(
        self,
        *,
        analysis_run_id: str,
        track_token: str,
        track_started_at: datetime,
        track_ended_at: datetime,
        associations: Iterable[OfflineEventTrackAssociation],
    ) -> OfflineTrackIdentityAssociation:
        if not analysis_run_id or not track_token:
            raise ValueError("track_scope_required")
        if track_ended_at < track_started_at:
            raise ValueError("track_interval_invalid")
        values = tuple(associations)
        evidence_ids = tuple(
            sorted(item.evidence_id for item in values if item.evidence_id)
        )
        methods = {item.method for item in values if item.method}
        confidences = [item.confidence for item in values if item.confidence is not None]
        method = next(iter(methods)) if len(methods) == 1 else None
        confidence = max(confidences) if confidences else None

        candidates = {
            item.candidate_member_id or item.member_id
            for item in values
            if item.status in {"confirmed", "probable"}
            and (item.candidate_member_id or item.member_id)
        }
        if any(item.status == "conflict" for item in values) or len(candidates) > 1:
            return self._result(
                analysis_run_id, track_token, "conflict", None, None,
                track_started_at, track_ended_at, evidence_ids, method, confidence,
                candidate_member_ids=tuple(sorted(candidates)),
            )
        if candidates:
            member_id = next(iter(candidates))
            if any(item.status == "confirmed" for item in values):
                return self._result(
                    analysis_run_id, track_token, "confirmed", member_id, None,
                    track_started_at, track_ended_at, evidence_ids, method, confidence,
                    candidate_member_ids=(member_id,),
                )
            return self._result(
                analysis_run_id, track_token, "probable", None, member_id,
                track_started_at, track_ended_at, evidence_ids, method, confidence,
                candidate_member_ids=(member_id,),
            )
        if any(item.status == "unavailable" for item in values):
            status = "unavailable"
        else:
            status = "ambiguous" if any(item.status == "ambiguous" for item in values) else "unmatched"
        return self._result(
            analysis_run_id, track_token, status, None, None,
            track_started_at, track_ended_at, evidence_ids, method, confidence,
        )

    @staticmethod
    def unavailable(
        *,
        analysis_run_id: str,
        track_token: str,
        track_started_at: Optional[datetime],
        track_ended_at: Optional[datetime],
        relative_track_started_at: Optional[float] = None,
        relative_track_ended_at: Optional[float] = None,
        diagnostics: tuple[dict, ...] = (),
    ) -> OfflineTrackIdentityAssociation:
        return OfflineTrackIdentityAssociation(
            analysis_run_id=analysis_run_id,
            track_token=track_token,
            status="unavailable",
            member_id=None,
            candidate_member_id=None,
            valid_from=track_started_at,
            valid_to=track_ended_at,
            evidence_ids=(),
            method=None,
            confidence=None,
            relative_valid_from_seconds=relative_track_started_at,
            relative_valid_to_seconds=relative_track_ended_at,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _result(
        analysis_run_id: str,
        track_token: str,
        status: str,
        member_id: Optional[str],
        candidate_member_id: Optional[str],
        valid_from: datetime,
        valid_to: datetime,
        evidence_ids: tuple[str, ...],
        method: Optional[str],
        confidence: Optional[float],
        candidate_member_ids: tuple[str, ...] = (),
    ) -> OfflineTrackIdentityAssociation:
        return OfflineTrackIdentityAssociation(
            analysis_run_id=analysis_run_id,
            track_token=track_token,
            status=status,
            member_id=member_id,
            candidate_member_id=candidate_member_id,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence_ids=evidence_ids,
            method=method,
            confidence=confidence,
            candidate_member_ids=candidate_member_ids,
        )


def _same_device_identity(
    revision: HistoricalIdentityRevisionData,
    *,
    device_id: str,
    group_id: Optional[int],
    person_id: Optional[int],
    unique_id: str,
) -> bool:
    if revision.device_id != device_id:
        return False
    if unique_id:
        return revision.unique_id == unique_id
    return revision.group_id == group_id and revision.person_id == person_id


def _eligible(revision: HistoricalIdentityRevisionData) -> bool:
    return all(
        (
            revision.mapping_enabled,
            revision.mapping_sync_status == "synced",
            revision.member_principal_exists,
            revision.member_principal_active,
        )
    )
