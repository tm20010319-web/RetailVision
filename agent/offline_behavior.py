"""Offline Stage9 normalization and safe BehaviorEvent projection seams."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Iterable, Optional

from agent.member_priority import normalize_tier
from agent.offline_identity import OfflineTrackIdentityAssociation


_POINT_TYPES = {"PICK_UP": "merchandise_pick_up", "PUT_DOWN": "merchandise_put_down"}


@dataclass(frozen=True)
class OfflineNormalizationDiagnostic:
    interaction_session_id: str
    reason: str
    raw_event_refs: tuple[str, ...]


@dataclass(frozen=True)
class OfflineNormalizedBehaviorFact:
    analysis_job_id: str
    analysis_run_id: str
    source_video_id: str
    event_type: str
    camera_id: str
    customer_track_id: int
    customer_track_token: str
    interaction_session_id: str
    product_key: str
    product_name: Optional[str]
    product_track_token: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    relative_started_at_seconds: Optional[float]
    relative_ended_at_seconds: Optional[float]
    confidence: Optional[float]
    normalized_fact_ref: str
    raw_event_refs: tuple[str, ...]
    holding_duration_seconds: Optional[float]
    keyframe_ref: Optional[str]


@dataclass(frozen=True)
class OfflineNormalizationResult:
    facts: tuple[OfflineNormalizedBehaviorFact, ...]
    diagnostics: tuple[OfflineNormalizationDiagnostic, ...]


class Stage9BehaviorFactAdapter:
    """Convert Stage9 transition evidence into auditable behavior facts."""

    def normalize(
        self,
        *,
        analysis_job_id: str,
        analysis_run_id: str,
        source_video_id: str,
        raw_events: Iterable[Any],
        absolute_event_time_status: str = "available",
        behavior_time_quality: str = "authoritative",
    ) -> OfflineNormalizationResult:
        _required_strings(analysis_job_id, analysis_run_id, source_video_id)
        degraded_time = (
            absolute_event_time_status != "available"
            or behavior_time_quality == "degraded"
        )
        sessions: dict[str, list[Any]] = {}
        diagnostics: list[OfflineNormalizationDiagnostic] = []
        for event in raw_events:
            session = _field(event, "interaction_session_id")
            if not isinstance(session, str) or not session:
                diagnostics.append(OfflineNormalizationDiagnostic("", "interaction_session_missing", (_raw_ref(event),)))
                continue
            sessions.setdefault(session, []).append(event)

        facts: list[OfflineNormalizedBehaviorFact] = []
        for session_id in sorted(sessions):
            session_facts, session_diagnostics = self._normalize_session(
                analysis_job_id=analysis_job_id,
                analysis_run_id=analysis_run_id,
                source_video_id=source_video_id,
                session_id=session_id,
                events=tuple(sessions[session_id]),
                degraded_time=degraded_time,
            )
            facts.extend(session_facts)
            diagnostics.extend(session_diagnostics)
        return OfflineNormalizationResult(tuple(facts), tuple(diagnostics))

    def _normalize_session(
        self,
        *,
        analysis_job_id: str,
        analysis_run_id: str,
        source_video_id: str,
        session_id: str,
        events: tuple[Any, ...],
        degraded_time: bool,
    ) -> tuple[list[OfflineNormalizedBehaviorFact], list[OfflineNormalizationDiagnostic]]:
        diagnostics: list[OfflineNormalizationDiagnostic] = []
        pickups = [event for event in events if _event_type(event) == "PICK_UP"]
        putdowns = [event for event in events if _event_type(event) == "PUT_DOWN"]
        refs = tuple(sorted(_raw_ref(event) for event in events))
        if len(pickups) > 1:
            return [], [OfflineNormalizationDiagnostic(session_id, "duplicate_pickup", refs)]
        if len(putdowns) > 1:
            diagnostics.append(OfflineNormalizationDiagnostic(session_id, "conflicting_putdown", refs))
            putdowns = []

        facts: list[OfflineNormalizedBehaviorFact] = []
        pickup = pickups[0] if pickups else None
        if degraded_time:
            try:
                for event in (*pickups, *putdowns):
                    _relative_observed_at(event)
            except ValueError:
                return [], [
                    OfflineNormalizationDiagnostic(
                        session_id, "relative_media_time_unavailable", refs
                    )
                ]
        if pickup is not None:
            facts.append(self._point_fact(
                analysis_job_id, analysis_run_id, source_video_id, pickup,
                "merchandise_pick_up", degraded_time=degraded_time,
            ))
        if pickup is None or not putdowns:
            return facts, diagnostics

        putdown = putdowns[0]
        compatibility = _session_compatibility(pickup, putdown)
        if compatibility is not None:
            diagnostics.append(OfflineNormalizationDiagnostic(session_id, compatibility, refs))
            return facts, diagnostics
        pickup_at = _event_clock(pickup, degraded_time=degraded_time)
        putdown_at = _event_clock(putdown, degraded_time=degraded_time)
        if putdown_at < pickup_at:
            diagnostics.append(OfflineNormalizationDiagnostic(session_id, "putdown_before_pickup", refs))
            return facts, diagnostics
        facts.append(self._holding_fact(
            analysis_job_id, analysis_run_id, source_video_id, pickup, putdown,
            degraded_time=degraded_time,
        ))
        facts.append(self._point_fact(
            analysis_job_id, analysis_run_id, source_video_id, putdown,
            "merchandise_put_down", degraded_time=degraded_time,
        ))
        return facts, diagnostics

    @staticmethod
    def _point_fact(
        analysis_job_id: str,
        analysis_run_id: str,
        source_video_id: str,
        event: Any,
        event_type: str,
        *,
        degraded_time: bool,
    ) -> OfflineNormalizedBehaviorFact:
        occurred_at = None if degraded_time else _observed_at(event)
        relative_at = _relative_observed_at(event) if degraded_time else None
        keyframe_ref = _keyframe_ref(_field(event, "interaction_session_id"), event_type)
        return _fact(
            analysis_job_id=analysis_job_id,
            analysis_run_id=analysis_run_id,
            source_video_id=source_video_id,
            event_type=event_type,
            event=event,
            started_at=occurred_at,
            ended_at=occurred_at,
            relative_started_at_seconds=relative_at,
            relative_ended_at_seconds=relative_at,
            raw_event_refs=(_raw_ref(event),),
            holding_duration_seconds=None,
            keyframe_ref=keyframe_ref,
        )

    @staticmethod
    def _holding_fact(
        analysis_job_id: str,
        analysis_run_id: str,
        source_video_id: str,
        pickup: Any,
        putdown: Any,
        *,
        degraded_time: bool,
    ) -> OfflineNormalizedBehaviorFact:
        pickup_at = None if degraded_time else _observed_at(pickup)
        putdown_at = None if degraded_time else _observed_at(putdown)
        relative_pickup = _relative_observed_at(pickup) if degraded_time else None
        relative_putdown = _relative_observed_at(putdown) if degraded_time else None
        duration = (
            float(Decimal(str(relative_putdown)) - Decimal(str(relative_pickup)))
            if degraded_time
            else (putdown_at - pickup_at).total_seconds()
        )
        return _fact(
            analysis_job_id=analysis_job_id,
            analysis_run_id=analysis_run_id,
            source_video_id=source_video_id,
            event_type="merchandise_holding",
            event=pickup,
            started_at=pickup_at,
            ended_at=putdown_at,
            relative_started_at_seconds=relative_pickup,
            relative_ended_at_seconds=relative_putdown,
            raw_event_refs=tuple(sorted((_raw_ref(pickup), _raw_ref(putdown)))),
            holding_duration_seconds=duration,
            keyframe_ref=None,
        )


@dataclass(frozen=True)
class MemberProfileEnrichment:
    status: str
    member_name: Optional[str]
    member_tier: Optional[str]
    reason: Optional[str]

    def __post_init__(self) -> None:
        if self.status not in {"available", "partial", "unavailable", "member_not_found"}:
            raise ValueError("member_enrichment_status_invalid")


class OfflineMemberProfileEnricher:
    """Read presentation fields without participating in identity decisions."""

    def enrich(
        self,
        *,
        identity_status: str,
        member_id: Optional[str],
        member_principal_proven: bool,
        member_reader: Any,
    ) -> MemberProfileEnrichment:
        if identity_status != "confirmed" or not member_id:
            return MemberProfileEnrichment("unavailable", None, None, "identity_not_confirmed")
        if not member_principal_proven:
            raise ValueError("confirmed_member_principal_unproven")
        try:
            profile = member_reader(member_id)
        except Exception:
            return MemberProfileEnrichment("unavailable", None, None, "member_service_unavailable")
        if profile is None:
            return MemberProfileEnrichment("member_not_found", None, None, "member_service_not_found")
        if not isinstance(profile, dict):
            return MemberProfileEnrichment("unavailable", None, None, "member_service_response_invalid")
        name = profile.get("name")
        tier = profile.get("tier")
        if name is not None and not isinstance(name, str):
            name = None
        if tier is not None and not isinstance(tier, str):
            tier = None
        return MemberProfileEnrichment(
            "available" if name is not None and tier is not None else "partial",
            name, tier, None,
        )


@dataclass(frozen=True)
class OfflineBehaviorEventCandidate:
    event_key: str
    event_type: str
    assessment: str
    store_id: Optional[str]
    camera_id: str
    customer_track_id: int
    customer_track_token: str
    started_at: datetime
    ended_at: datetime
    confidence: Optional[float]
    member_id: Optional[str]
    member_tier: Optional[str]
    identity_status: str
    identity_method: Optional[str]
    identity_confidence: Optional[float]
    details: dict[str, Any]
    visit_id: None = None
    entry_event_id: None = None
    evidence_clip_id: None = None


@dataclass(frozen=True)
class PreparedOfflineBehaviorRow:
    """Complete deterministic DB payload used by both manifest and publisher."""

    event_key: str
    event_type: str
    assessment: str
    store_id: str
    camera_id: str
    customer_track_id: int
    customer_track_token: str
    track_token: str
    entry_event_id: None
    visit_id: None
    is_demo: bool
    staff_track_id: None
    staff_track_token: None
    staff_name: None
    staff_attribution_source: None
    member_id: Optional[str]
    member_tier: Optional[str]
    identity_status: str
    identity_source: str
    identity_confidence: Optional[float]
    identity_binding_level: Optional[str]
    binding_id: None
    backfilled_at: None
    occurred_at: datetime
    evaluation_id: None
    policy_version_id: None
    algorithm_version: None
    parameter_version: None
    area_id: None
    started_at: datetime
    ended_at: datetime
    duration_seconds: Optional[float]
    confidence: Optional[float]
    details: Mapping[str, Any]
    evidence_clip_id: None

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _freeze_json(self.details))

    @classmethod
    def from_candidate(
        cls, candidate: "OfflineBehaviorEventCandidate"
    ) -> "PreparedOfflineBehaviorRow":
        _required_strings(candidate.store_id, candidate.camera_id)
        confirmed_member = (
            candidate.member_id
            if candidate.identity_status == "confirmed" and candidate.member_id
            else None
        )
        if candidate.member_id is not None and confirmed_member is None:
            raise ValueError("offline_member_projection_requires_confirmed_identity")
        member_tier = (
            normalize_tier(candidate.member_tier)
            if confirmed_member and candidate.member_tier
            else None
        )
        if candidate.event_type in {"merchandise_pick_up", "merchandise_put_down"}:
            duration_seconds = None
        elif candidate.event_type == "merchandise_holding":
            duration_seconds = (candidate.ended_at - candidate.started_at).total_seconds()
        else:
            raise ValueError("offline_behavior_event_type_invalid")
        return cls(
            event_key=candidate.event_key,
            event_type=candidate.event_type,
            assessment=candidate.assessment,
            store_id=candidate.store_id,
            camera_id=candidate.camera_id,
            customer_track_id=candidate.customer_track_id,
            customer_track_token=candidate.customer_track_token,
            track_token=candidate.customer_track_token,
            entry_event_id=None,
            visit_id=None,
            is_demo=False,
            staff_track_id=None,
            staff_track_token=None,
            staff_name=None,
            staff_attribution_source=None,
            member_id=confirmed_member,
            member_tier=member_tier,
            identity_status=candidate.identity_status,
            identity_source="offline_track_association",
            identity_confidence=candidate.identity_confidence,
            identity_binding_level="confirmed" if confirmed_member else None,
            binding_id=None,
            backfilled_at=None,
            occurred_at=candidate.started_at,
            evaluation_id=None,
            policy_version_id=None,
            algorithm_version=None,
            parameter_version=None,
            area_id=None,
            started_at=candidate.started_at,
            ended_at=candidate.ended_at,
            duration_seconds=duration_seconds,
            confidence=candidate.confidence,
            details=candidate.details,
            evidence_clip_id=None,
        )

    def details_for_db(self) -> dict[str, Any]:
        return _thaw_json(self.details)


class OfflineBehaviorIdentityProjector:
    """The only route by which Offline identity reaches formal business facts."""

    def __init__(self, canonicalizer: Optional["OfflineBehaviorCanonicalizerV1"] = None) -> None:
        self.canonicalizer = canonicalizer or OfflineBehaviorCanonicalizerV1()

    def project(
        self,
        *,
        fact: OfflineNormalizedBehaviorFact,
        association: OfflineTrackIdentityAssociation,
        enrichment: Optional[MemberProfileEnrichment],
        artifact_manifest_hash: str,
        association_record_ref: str,
        store_id: Optional[str] = None,
    ) -> OfflineBehaviorEventCandidate:
        if association.analysis_run_id != fact.analysis_run_id:
            raise ValueError("association_run_scope_mismatch")
        if association.track_token != fact.customer_track_token:
            raise ValueError("association_track_scope_mismatch")
        if not artifact_manifest_hash.startswith("v1:sha256:"):
            raise ValueError("artifact_manifest_hash_invalid")
        if not association_record_ref:
            raise ValueError("association_artifact_ref_required")
        if fact.started_at is None or fact.ended_at is None:
            raise ValueError("authoritative_fact_time_required_for_projection")

        confirmed = association.status == "confirmed" and bool(association.member_id)
        member_id = association.member_id if confirmed else None
        member_tier = enrichment.member_tier if confirmed and enrichment else None
        enrichment_status = enrichment.status if enrichment else "unavailable"
        enrichment_reason = enrichment.reason if enrichment else "enrichment_not_requested"
        details: dict[str, Any] = {
            "analysis_job_id": fact.analysis_job_id,
            "analysis_run_id": fact.analysis_run_id,
            "source_video_id": fact.source_video_id,
            "interaction_session_id": fact.interaction_session_id,
            "product_key": fact.product_key,
            "product_name": fact.product_name,
            "product_track_token": fact.product_track_token,
            "normalized_fact_ref": fact.normalized_fact_ref,
            "member_enrichment_status": enrichment_status,
            "member_enrichment_reason": enrichment_reason,
            "offline_artifact": {
                "analysis_job_id": fact.analysis_job_id,
                "analysis_run_id": fact.analysis_run_id,
                "source_video_id": fact.source_video_id,
                "artifact_manifest_hash": artifact_manifest_hash,
                "normalized_fact_ref": fact.normalized_fact_ref,
                "association_record_ref": association_record_ref,
            },
            "association_artifact_ref": {
                "manifest_hash": artifact_manifest_hash,
                "artifact_name": "track_identity_associations.jsonl",
                "record_ref": association_record_ref,
            },
        }
        if fact.keyframe_ref is not None:
            details["keyframe_ref"] = fact.keyframe_ref
        if fact.holding_duration_seconds is not None:
            details["holding_duration_seconds"] = fact.holding_duration_seconds
        # Candidate and conflict identities stay in the sealed Artifact only.
        return OfflineBehaviorEventCandidate(
            event_key=self.canonicalizer.event_key(fact),
            event_type=fact.event_type,
            assessment="confirmed",
            store_id=store_id,
            camera_id=fact.camera_id,
            customer_track_id=fact.customer_track_id,
            customer_track_token=fact.customer_track_token,
            started_at=fact.started_at,
            ended_at=fact.ended_at,
            confidence=fact.confidence,
            member_id=member_id,
            member_tier=member_tier,
            identity_status=association.status,
            identity_method=association.method,
            identity_confidence=association.confidence,
            details=details,
        )


class OfflineBehaviorCanonicalizerV1:
    """Canonical bytes shared by deterministic event keys and manifests."""

    version = "offline_behavior_event_v1"

    def event_key(self, fact: OfflineNormalizedBehaviorFact) -> str:
        if fact.started_at is None or fact.ended_at is None:
            raise ValueError("authoritative_fact_time_required_for_event_key")
        identity = {
            "analysis_job_id": fact.analysis_job_id,
            "analysis_run_id": fact.analysis_run_id,
            "normalized_event_type": fact.event_type,
            "customer_track_token": fact.customer_track_token,
            "interaction_session_id": fact.interaction_session_id,
            "product_key": fact.product_key,
            "product_track_token": fact.product_track_token,
            "started_at": _timestamp(fact.started_at),
            "ended_at": _timestamp(fact.ended_at),
            "normalized_fact_ref": fact.normalized_fact_ref,
        }
        return "offline:v1:" + hashlib.sha256(self.canonical_bytes(identity)).hexdigest()

    def event_payload(self, event: PreparedOfflineBehaviorRow) -> dict[str, Any]:
        return {
            "event_key": event.event_key,
            "event_type": event.event_type,
            "assessment": event.assessment,
            "store_id": event.store_id,
            "camera_id": event.camera_id,
            "customer_track_id": event.customer_track_id,
            "customer_track_token": event.customer_track_token,
            "track_token": event.track_token,
            "entry_event_id": event.entry_event_id,
            "visit_id": event.visit_id,
            "is_demo": event.is_demo,
            "staff_track_id": event.staff_track_id,
            "staff_track_token": event.staff_track_token,
            "staff_name": event.staff_name,
            "staff_attribution_source": event.staff_attribution_source,
            "member_id": event.member_id,
            "member_tier": event.member_tier,
            "identity_status": event.identity_status,
            "identity_source": event.identity_source,
            "identity_confidence": event.identity_confidence,
            "identity_binding_level": event.identity_binding_level,
            "binding_id": event.binding_id,
            "backfilled_at": event.backfilled_at,
            "occurred_at": _timestamp(event.occurred_at),
            "evaluation_id": event.evaluation_id,
            "policy_version_id": event.policy_version_id,
            "algorithm_version": event.algorithm_version,
            "parameter_version": event.parameter_version,
            "area_id": event.area_id,
            "started_at": _timestamp(event.started_at),
            "ended_at": _timestamp(event.ended_at),
            "duration_seconds": event.duration_seconds,
            "confidence": event.confidence,
            "details": event.details,
            "evidence_clip_id": event.evidence_clip_id,
        }

    def canonical_bytes(self, value: Any) -> bytes:
        return json.dumps(
            _canonical_value(value), ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")


@dataclass(frozen=True)
class PreparedOfflineBehaviorPublication:
    analysis_job_id: str
    analysis_run_id: str
    source_video_id: str
    events: tuple[PreparedOfflineBehaviorRow, ...]
    event_count: int
    event_manifest_hash: str
    canonical_manifest_bytes: bytes

    @classmethod
    def create(
        cls,
        *,
        analysis_job_id: str,
        analysis_run_id: str,
        source_video_id: str,
        events: Iterable[OfflineBehaviorEventCandidate | PreparedOfflineBehaviorRow],
        canonicalizer: Optional[OfflineBehaviorCanonicalizerV1] = None,
    ) -> "PreparedOfflineBehaviorPublication":
        _required_strings(analysis_job_id, analysis_run_id, source_video_id)
        canonicalizer = canonicalizer or OfflineBehaviorCanonicalizerV1()
        prepared_rows = tuple(
            item
            if isinstance(item, PreparedOfflineBehaviorRow)
            else PreparedOfflineBehaviorRow.from_candidate(item)
            for item in events
        )
        sorted_events = tuple(sorted(prepared_rows, key=lambda item: item.event_key))
        keys = [item.event_key for item in sorted_events]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_event_key")
        body = {
            "manifest_version": "offline_behavior_event_manifest_v1",
            "canonicalization_version": canonicalizer.version,
            "analysis_job_id": analysis_job_id,
            "analysis_run_id": analysis_run_id,
            "source_video_id": source_video_id,
            "event_count": len(sorted_events),
            "events": [canonicalizer.event_payload(event) for event in sorted_events],
        }
        payload = canonicalizer.canonical_bytes(body)
        return cls(
            analysis_job_id=analysis_job_id,
            analysis_run_id=analysis_run_id,
            source_video_id=source_video_id,
            events=sorted_events,
            event_count=len(sorted_events),
            event_manifest_hash="v1:sha256:" + hashlib.sha256(payload).hexdigest(),
            canonical_manifest_bytes=payload,
        )


@dataclass(frozen=True)
class CustomerOrdinalObservation:
    analysis_run_id: str
    track_token: str
    first_valid_observed_at: Optional[datetime]
    first_valid_sample_seq: Optional[int]
    first_valid_source_seq: Optional[int]


@dataclass(frozen=True)
class OfflineRunCustomerOrdinalRegistry:
    analysis_run_id: str
    ordinals: dict[str, int]

    @classmethod
    def build(
        cls,
        *,
        analysis_run_id: str,
        observations: Iterable[CustomerOrdinalObservation],
        authoritative_time: bool,
    ) -> "OfflineRunCustomerOrdinalRegistry":
        values = tuple(item for item in observations if item.analysis_run_id == analysis_run_id)
        if len({item.track_token for item in values}) != len(values):
            raise ValueError("duplicate_track_ordinal_observation")
        if authoritative_time:
            if any(item.first_valid_observed_at is None for item in values):
                raise ValueError("display_observed_at_unavailable")
            ordered = sorted(values, key=lambda item: (item.first_valid_observed_at, item.track_token))
        else:
            if any(item.first_valid_sample_seq is None or item.first_valid_source_seq is None for item in values):
                raise ValueError("display_sequence_unavailable")
            ordered = sorted(values, key=lambda item: (
                item.first_valid_sample_seq, item.first_valid_source_seq, item.track_token
            ))
        return cls(analysis_run_id, {item.track_token: index for index, item in enumerate(ordered, 1)})

    def ordinal_for(self, track_token: str) -> int:
        return self.ordinals[track_token]


@dataclass(frozen=True)
class OfflineDisplayIdentity:
    analysis_run_id: str
    track_token: str
    customer_ordinal: int
    identity_status: str
    member_id: Optional[str]
    member_name: Optional[str]
    artifact_only: bool


class OfflinePersonDisplayIdentityResolver:
    def resolve_label(self, value: OfflineDisplayIdentity) -> str:
        if value.artifact_only:
            return f"顾客{value.customer_ordinal} · ARTIFACT ONLY"
        if value.identity_status == "confirmed":
            if value.member_name:
                return f"会员：{value.member_name}"
            if value.member_id:
                return f"会员 {value.member_id}"
        if value.identity_status == "probable":
            return f"顾客{value.customer_ordinal} · 疑似会员"
        return f"顾客{value.customer_ordinal}"


def _fact(
    *,
    analysis_job_id: str,
    analysis_run_id: str,
    source_video_id: str,
    event_type: str,
    event: Any,
    started_at: Optional[datetime],
    ended_at: Optional[datetime],
    relative_started_at_seconds: Optional[float],
    relative_ended_at_seconds: Optional[float],
    raw_event_refs: tuple[str, ...],
    holding_duration_seconds: Optional[float],
    keyframe_ref: Optional[str],
) -> OfflineNormalizedBehaviorFact:
    authoritative = started_at is not None or ended_at is not None
    if authoritative:
        _require_utc_naive(started_at)
        _require_utc_naive(ended_at)
        if relative_started_at_seconds is not None or relative_ended_at_seconds is not None:
            raise ValueError("normalized_fact_time_modes_conflict")
        if ended_at < started_at:
            raise ValueError("normalized_fact_time_reversal")
    else:
        _relative_seconds(relative_started_at_seconds)
        _relative_seconds(relative_ended_at_seconds)
        if relative_ended_at_seconds < relative_started_at_seconds:
            raise ValueError("normalized_fact_relative_time_reversal")
    values = {
        "analysis_job_id": analysis_job_id,
        "analysis_run_id": analysis_run_id,
        "source_video_id": source_video_id,
        "event_type": event_type,
        "camera_id": _field(event, "camera_id"),
        "customer_track_id": _field(event, "person_track_id"),
        "customer_track_token": _field(event, "person_track_token"),
        "interaction_session_id": _field(event, "interaction_session_id"),
        "product_key": _field(event, "product_key"),
        "product_track_token": _field(event, "product_track_token"),
        "started_at": None if started_at is None else _timestamp(started_at),
        "ended_at": None if ended_at is None else _timestamp(ended_at),
        "relative_started_at_seconds": relative_started_at_seconds,
        "relative_ended_at_seconds": relative_ended_at_seconds,
        "raw_event_refs": list(raw_event_refs),
    }
    _required_strings(
        values["camera_id"], values["customer_track_token"], values["interaction_session_id"],
        values["product_key"], values["product_track_token"],
    )
    if isinstance(values["customer_track_id"], bool) or not isinstance(values["customer_track_id"], int) or values["customer_track_id"] < 0:
        raise ValueError("customer_track_id_required")
    fact_ref = "fact:v1:sha256:" + hashlib.sha256(
        OfflineBehaviorCanonicalizerV1().canonical_bytes(values)
    ).hexdigest()
    confidence = _field(event, "confidence")
    return OfflineNormalizedBehaviorFact(
        analysis_job_id=analysis_job_id,
        analysis_run_id=analysis_run_id,
        source_video_id=source_video_id,
        event_type=event_type,
        camera_id=values["camera_id"],
        customer_track_id=values["customer_track_id"],
        customer_track_token=values["customer_track_token"],
        interaction_session_id=values["interaction_session_id"],
        product_key=values["product_key"],
        product_name=_field(event, "product_name"),
        product_track_token=values["product_track_token"],
        started_at=started_at,
        ended_at=ended_at,
        relative_started_at_seconds=relative_started_at_seconds,
        relative_ended_at_seconds=relative_ended_at_seconds,
        confidence=float(confidence) if confidence is not None else None,
        normalized_fact_ref=fact_ref,
        raw_event_refs=raw_event_refs,
        holding_duration_seconds=holding_duration_seconds,
        keyframe_ref=keyframe_ref,
    )


def _session_compatibility(pickup: Any, putdown: Any) -> Optional[str]:
    fields = (
        ("person_track_token", "session_actor_mismatch"),
        ("product_key", "session_product_mismatch"),
        ("product_track_token", "session_product_track_mismatch"),
        ("camera_id", "session_camera_mismatch"),
    )
    for field, reason in fields:
        if _field(pickup, field) != _field(putdown, field):
            return reason
    return None


def _event_type(event: Any) -> str:
    value = _field(event, "event_type")
    value = getattr(value, "value", value)
    return str(value).upper()


def _observed_at(event: Any) -> datetime:
    value = _field(event, "observed_at")
    _require_utc_naive(value)
    return value


def _relative_observed_at(event: Any) -> float:
    value = _field(event, "source_pts_seconds")
    _relative_seconds(value)
    return float(value)


def _event_clock(event: Any, *, degraded_time: bool) -> datetime | float:
    return _relative_observed_at(event) if degraded_time else _observed_at(event)


def _field(event: Any, name: str) -> Any:
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


def _raw_ref(event: Any) -> str:
    value = _field(event, "event_id")
    if not value:
        raise ValueError("raw_stage9_event_id_required")
    return f"raw:{value}"


def _keyframe_ref(session_id: str, event_type: str) -> str:
    label = "pick_up" if event_type == "merchandise_pick_up" else "put_down"
    token = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return f"keyframes/{token}/{label}.jpg"


def _timestamp(value: datetime) -> str:
    _require_utc_naive(value)
    return value.isoformat(timespec="microseconds")


def _require_utc_naive(value: Any) -> None:
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError("offline_event_time_must_be_utc_naive")


def _relative_seconds(value: Any) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError("offline_relative_media_time_invalid")


def _required_strings(*values: Any) -> None:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("offline_fact_scope_required")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _timestamp(value)
    if value is None or isinstance(value, str) or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical_number_invalid")
        return _decimal_string(value)
    if isinstance(value, Decimal):
        return _decimal_string(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    raise ValueError("canonical_value_unsupported")


def _decimal_string(value: Any) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("canonical_number_invalid") from exc
    if not decimal.is_finite():
        raise ValueError("canonical_number_invalid")
    rendered = format(decimal.normalize(), "f")
    return "0" if rendered in {"", "-0"} else rendered


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("prepared_behavior_details_invalid")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
