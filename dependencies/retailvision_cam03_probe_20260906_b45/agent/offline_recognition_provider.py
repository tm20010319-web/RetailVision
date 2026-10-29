"""Fail-closed P6S Recognition Record provider for offline analysis."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from agent.camera_face_api import CameraApiError
from agent.device_recognition_intake import (
    DeviceRecognitionEvent,
    canonical_event_id,
)
from agent.offline_identity import HistoricalIdentityResolver
from agent.offline_media import CapabilityProfileResolution
from agent.face_spatial_evidence import FaceSpatialEvidence
from agent.models import DeviceRecognitionInbox


@dataclass(frozen=True)
class PaginationCompletionPolicy:
    policy_version: str
    status: str
    trust_class: str
    completion_mode: str

    @property
    def production_eligible(self) -> bool:
        return (
            self.status == "verified"
            and self.trust_class == "production_verified"
            and self.completion_mode in {"verified_short_page", "explicit_metadata"}
        )


@dataclass(frozen=True)
class RecognitionAcceptanceAssessment:
    accepted: bool
    reason: Optional[str]


@dataclass(frozen=True)
class DeviceRecognitionAcceptancePolicy:
    policy_id: str
    policy_version: str
    similarity_threshold: str
    accepted_rule_ids: tuple[int, ...]
    accepted_pass_codes: tuple[Optional[int], ...]
    require_reco_valid: bool
    policy_hash: str

    @classmethod
    def create(
        cls,
        *,
        policy_id: str,
        policy_version: str,
        similarity_threshold: str,
        accepted_rule_ids: tuple[int, ...],
        accepted_pass_codes: tuple[Optional[int], ...],
        require_reco_valid: bool,
    ) -> "DeviceRecognitionAcceptancePolicy":
        threshold = _decimal(similarity_threshold, "similarity_threshold")
        if not Decimal("0") <= threshold <= Decimal("1"):
            raise ValueError("similarity_threshold_invalid")
        if not policy_id or not policy_version:
            raise ValueError("acceptance_policy_identity_required")
        if not accepted_rule_ids or not accepted_pass_codes:
            raise ValueError("accepted_recognition_codes_required")
        body = {
            "policy_id": policy_id,
            "policy_version": policy_version,
            "similarity_threshold": _canonical_decimal(threshold),
            "accepted_rule_ids": sorted(accepted_rule_ids),
            "accepted_pass_codes": sorted(
                accepted_pass_codes,
                key=lambda value: -1 if value is None else value,
            ),
            "require_reco_valid": require_reco_valid,
        }
        policy_hash = "v1:sha256:" + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            policy_id=policy_id,
            policy_version=policy_version,
            similarity_threshold=body["similarity_threshold"],
            accepted_rule_ids=tuple(sorted(accepted_rule_ids)),
            accepted_pass_codes=tuple(sorted(
                accepted_pass_codes,
                key=lambda value: -1 if value is None else value,
            )),
            require_reco_valid=require_reco_valid,
            policy_hash=policy_hash,
        )

    def assess(self, record: dict[str, Any]) -> RecognitionAcceptanceAssessment:
        if self.require_reco_valid and record["reco_valid"] is not True:
            return RecognitionAcceptanceAssessment(False, "reco_invalid")
        if record["pass_code"] not in self.accepted_pass_codes:
            return RecognitionAcceptanceAssessment(False, "pass_code_rejected")
        if record["rule_id"] not in self.accepted_rule_ids:
            return RecognitionAcceptanceAssessment(False, "rule_not_accepted")
        if _decimal(record["similarity"], "similarity") < _decimal(
            self.similarity_threshold, "similarity_threshold"
        ):
            return RecognitionAcceptanceAssessment(False, "similarity_below_threshold")
        return RecognitionAcceptanceAssessment(True, None)


@dataclass(frozen=True)
class OfflineRecognitionEvidence:
    evidence_id: str
    canonical_event_id: str
    device_id: str
    occurred_at: datetime
    member_id: str
    similarity: float
    source: str
    log_id: Optional[int]
    group_id: int
    person_id: int
    unique_id: str
    source_event_id: Optional[str] = None
    device_occurred_at_raw: Optional[str] = None
    device_utc_offset_minutes: Optional[int] = None
    rule_id: Optional[int] = None
    reco_valid: Optional[bool] = None
    pass_code: Optional[int] = None
    face_spatial_evidence: Optional[dict[str, Any]] = None
    capture_path: Optional[str] = None
    background_capture_path: Optional[str] = None
    background_capture_sha256: Optional[str] = None
    background_capture_frame_size: Optional[tuple[int, int]] = None
    historical_mapping_status: str = "resolved"
    historical_revision_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecognitionRecordAudit:
    canonical_event_id: str
    log_id: Optional[int]
    status: str
    reason: Optional[str]
    similarity: float
    member_id: Optional[str]
    source_event_id: Optional[str] = None
    diagnostics: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class RecognitionPageAudit:
    page_num: int
    requested_page_size: int
    records_count: int
    eof_proven: bool
    status: str
    completion_mode: str
    metadata: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class RecognitionRecordQueryResult:
    status: str
    requested_start: datetime
    requested_end: datetime
    accepted_evidence: tuple[OfflineRecognitionEvidence, ...]
    record_audits: tuple[RecognitionRecordAudit, ...]
    pages_fetched: int
    raw_record_count: int
    partial_record_count: int
    coverage_complete: bool
    failure_reason: Optional[str]
    page_audits: tuple[RecognitionPageAudit, ...] = ()
    source_mode: str = "historical_query"
    coverage_policy_version: Optional[str] = None
    coverage_material_hash: Optional[str] = None
    coverage_start: Optional[datetime] = None
    coverage_end: Optional[datetime] = None
    diagnostics: tuple[dict[str, Any], ...] = ()


class RecordedCameraRecognitionProvider:
    """Query one complete interval before exposing any association evidence."""

    def __init__(
        self,
        *,
        client: Any,
        device_id: str,
        pagination_policy: PaginationCompletionPolicy,
        acceptance_policy: DeviceRecognitionAcceptancePolicy,
        historical_identity_resolver: HistoricalIdentityResolver,
        recognition_timezone_offset_minutes: Optional[int],
        profile_resolution: Optional[CapabilityProfileResolution] = None,
        page_size: int = 100,
        max_pages: int = 10000,
    ) -> None:
        if not device_id:
            raise ValueError("device_id_required")
        if isinstance(page_size, bool) or page_size <= 0:
            raise ValueError("page_size_invalid")
        if profile_resolution is not None and not isinstance(
            profile_resolution, CapabilityProfileResolution
        ):
            raise ValueError("provider_capability_profile_resolution_required")
        self.client = client
        self.device_id = device_id
        self.pagination_policy = pagination_policy
        self.acceptance_policy = acceptance_policy
        self.historical_identity_resolver = historical_identity_resolver
        self.recognition_timezone_offset_minutes = recognition_timezone_offset_minutes
        self.profile_resolution = profile_resolution
        self.page_size = page_size
        self.max_pages = max_pages

    def query(
        self, requested_start: datetime, requested_end: datetime
    ) -> RecognitionRecordQueryResult:
        if requested_end < requested_start:
            raise ValueError("recognition_time_range_invalid")
        if self.recognition_timezone_offset_minutes is None:
            return self._unavailable(
                requested_start, requested_end, "recognition_timezone_unavailable"
            )
        if self.profile_resolution is None:
            return self._unavailable(
                requested_start, requested_end, "provider_capability_unavailable"
            )
        capability_reason = self.profile_resolution.production_eligibility_reason(
            device_id=self.device_id
        )
        if capability_reason is not None:
            return self._unavailable(
                requested_start, requested_end, capability_reason
            )
        if (
            self.profile_resolution.profile is not None
            and self.profile_resolution.profile.recognition_evidence_mode != "historical_query"
        ):
            return self._unavailable(
                requested_start, requested_end, "recognition_source_mode_mismatch"
            )
        if not self.pagination_policy.production_eligible:
            return self._unavailable(
                requested_start, requested_end, "pagination_protocol_unverified"
            )
        profile = self.profile_resolution.profile
        if (
            profile is None
            or self.pagination_policy.completion_mode != profile.pagination_completion_mode
            or self.pagination_policy.policy_version != profile.pagination_policy_version
        ):
            return self._unavailable(
                requested_start, requested_end, "pagination_profile_policy_mismatch"
            )

        raw_records: list[dict[str, Any]] = []
        pages_fetched = 0
        page_audits: list[RecognitionPageAudit] = []
        device_start = requested_start + timedelta(
            minutes=self.recognition_timezone_offset_minutes
        )
        device_end = requested_end + timedelta(
            minutes=self.recognition_timezone_offset_minutes
        )
        page_num = 1
        for _ in range(self.max_pages):
            try:
                response = self.client.query_recognition_records(
                    device_start,
                    device_end,
                    page_size=self.page_size,
                    page_num=page_num,
                    timezone_offset_minutes=self.recognition_timezone_offset_minutes,
                )
            except CameraApiError:
                return self._unavailable(
                    requested_start,
                    requested_end,
                    "pagination_failed",
                    pages_fetched=pages_fetched,
                    partial_record_count=len(raw_records),
                    page_audits=tuple(page_audits),
                )
            parsed = _parse_page_response(
                response,
                completion_mode=self.pagination_policy.completion_mode,
                page_num=page_num,
                page_size=self.page_size,
                records_seen=len(raw_records),
            )
            if parsed is None:
                return self._unavailable(
                    requested_start,
                    requested_end,
                    "pagination_metadata_unavailable"
                    if self.pagination_policy.completion_mode == "explicit_metadata"
                    else "response_protocol_invalid",
                    pages_fetched=pages_fetched,
                    partial_record_count=len(raw_records),
                    page_audits=tuple(page_audits),
                )
            page, eof_proven, next_page, metadata = parsed
            pages_fetched += 1
            page_audits.append(RecognitionPageAudit(
                page_num=page_num,
                requested_page_size=self.page_size,
                records_count=len(page),
                eof_proven=eof_proven,
                status="received",
                completion_mode=self.pagination_policy.completion_mode,
                metadata=metadata,
            ))
            try:
                for record in page:
                    _validate_record_shape(record)
                    if not requested_start <= record["occurred_at"] <= requested_end:
                        raise ValueError("record_outside_requested_interval")
            except ValueError as exc:
                return self._unavailable(
                    requested_start,
                    requested_end,
                    str(exc) if str(exc) == "record_outside_requested_interval" else "response_integrity_failure",
                    pages_fetched=pages_fetched,
                    partial_record_count=len(raw_records) + len(page),
                    page_audits=tuple(page_audits),
                )
            raw_records.extend(page)
            if eof_proven:
                break
            if next_page is None or next_page <= page_num:
                return self._unavailable(
                    requested_start,
                    requested_end,
                    "pagination_metadata_invalid",
                    pages_fetched=pages_fetched,
                    partial_record_count=len(raw_records),
                    page_audits=tuple(page_audits),
                )
            page_num = next_page
        else:
            return self._unavailable(
                requested_start,
                requested_end,
                "pagination_incomplete",
                pages_fetched=pages_fetched,
                partial_record_count=len(raw_records),
                page_audits=tuple(page_audits),
            )

        evidence: list[OfflineRecognitionEvidence] = []
        audits: list[RecognitionRecordAudit] = []
        for record in raw_records:
            event_id = _event_id(self.device_id, record)
            acceptance = self.acceptance_policy.assess(record)
            if not acceptance.accepted:
                audits.append(_audit(record, event_id, "rejected", acceptance.reason, None))
                continue
            historical = self.historical_identity_resolver.resolve_at(
                device_id=self.device_id,
                group_id=record["group_id"],
                person_id=record["person_id"],
                unique_id=record["unique_id"],
                occurred_at=record["occurred_at"],
            )
            if historical.status != "resolved" or not historical.member_id:
                audits.append(
                    _audit(
                        record,
                        event_id,
                        "conflict" if historical.status == "conflict" else "rejected",
                        f"mapping_historical_{historical.status}",
                        None,
                    )
                )
                continue
            evidence_id = "offline-recognition:" + hashlib.sha256(
                f"{self.device_id}|{event_id}".encode("utf-8")
            ).hexdigest()[:24]
            evidence.append(
                OfflineRecognitionEvidence(
                    evidence_id=evidence_id,
                    canonical_event_id=event_id,
                    device_id=self.device_id,
                    occurred_at=record["occurred_at"],
                    member_id=historical.member_id,
                    similarity=float(record["similarity"]),
                    source="device_record",
                    log_id=record["log_id"],
                    group_id=record["group_id"],
                    person_id=record["person_id"],
                    unique_id=record["unique_id"],
                )
            )
            audits.append(_audit(record, event_id, "accepted", None, historical.member_id))
        return RecognitionRecordQueryResult(
            status="complete",
            requested_start=requested_start,
            requested_end=requested_end,
            accepted_evidence=tuple(evidence),
            record_audits=tuple(audits),
            pages_fetched=pages_fetched,
            raw_record_count=len(raw_records),
            partial_record_count=0,
            coverage_complete=True,
            failure_reason=None,
            page_audits=tuple(page_audits),
        )

    @staticmethod
    def _unavailable(
        start: datetime,
        end: datetime,
        reason: str,
        *,
        pages_fetched: int = 0,
        partial_record_count: int = 0,
        page_audits: tuple[RecognitionPageAudit, ...] = (),
    ) -> RecognitionRecordQueryResult:
        return RecognitionRecordQueryResult(
            status="unavailable",
            requested_start=start,
            requested_end=end,
            accepted_evidence=(),
            record_audits=(),
            pages_fetched=pages_fetched,
            raw_record_count=0,
            partial_record_count=partial_record_count,
            coverage_complete=False,
            failure_reason=reason,
            page_audits=page_audits,
        )


class DurablePushInboxRecognitionProvider:
    """Read durable Push Recognition Evidence only after historical Coverage passes."""

    def __init__(
        self,
        *,
        session_factory: Any,
        push_coverage_store: Any,
        historical_identity_resolver: HistoricalIdentityResolver,
        acceptance_policy: DeviceRecognitionAcceptancePolicy,
        device_id: str,
        profile_resolution: Optional[CapabilityProfileResolution] = None,
    ) -> None:
        if not device_id:
            raise ValueError("device_id_required")
        self.session_factory = session_factory
        self.push_coverage_store = push_coverage_store
        self.historical_identity_resolver = historical_identity_resolver
        self.acceptance_policy = acceptance_policy
        self.device_id = device_id
        self.profile_resolution = profile_resolution

    def query(
        self, requested_start: datetime, requested_end: datetime
    ) -> RecognitionRecordQueryResult:
        if not isinstance(requested_start, datetime) or not isinstance(requested_end, datetime):
            raise ValueError("recognition_time_range_invalid")
        requested_start = _utc_naive_time(requested_start)
        requested_end = _utc_naive_time(requested_end)
        if requested_end < requested_start:
            raise ValueError("recognition_time_range_invalid")

        if self.profile_resolution is not None:
            capability_reason = self.profile_resolution.production_eligibility_reason(
                device_id=self.device_id
            )
            profile = self.profile_resolution.profile
            if capability_reason is not None:
                return self._unavailable(requested_start, requested_end, capability_reason)
            if profile is None or profile.recognition_evidence_mode != "durable_push_inbox":
                return self._unavailable(
                    requested_start, requested_end, "recognition_source_mode_mismatch"
                )
            coverage_policy = getattr(self.push_coverage_store, "policy", None)
            if (
                coverage_policy is None
                or getattr(coverage_policy, "policy_id", None) != profile.push_coverage_policy_id
                or coverage_policy.policy_version != profile.push_coverage_policy_version
            ):
                return self._unavailable(
                    requested_start, requested_end, "push_coverage_policy_mismatch"
                )
            acceptance_policy_hash = getattr(self.acceptance_policy, "policy_hash", None)
            if acceptance_policy_hash != profile.recognition_acceptance_policy_hash:
                return self._unavailable(
                    requested_start, requested_end, "recognition_acceptance_policy_mismatch"
                )

        # Coverage is deliberately the first external operation. Inbox rows
        # must never be used to infer whether the Push channel was complete.
        try:
            coverage = self.push_coverage_store.coverage(
                device_id=self.device_id,
                start_at=requested_start,
                end_at=requested_end,
            )
        except Exception:
            return self._unavailable(
                requested_start,
                requested_end,
                "push_coverage_query_failed",
            )
        if getattr(coverage, "status", None) != "complete":
            return self._unavailable(
                requested_start,
                requested_end,
                getattr(coverage, "reason", None) or "push_coverage_unavailable",
                coverage=coverage,
            )

        try:
            session = self.session_factory()
            try:
                rows = (
                    session.query(DeviceRecognitionInbox)
                    .filter(
                        DeviceRecognitionInbox.device_id == self.device_id,
                        DeviceRecognitionInbox.source == "push",
                        DeviceRecognitionInbox.occurred_at >= requested_start,
                        DeviceRecognitionInbox.occurred_at <= requested_end,
                    )
                    .order_by(
                        DeviceRecognitionInbox.occurred_at.asc(),
                        DeviceRecognitionInbox.canonical_event_id.asc(),
                    )
                    .all()
                )
            finally:
                session.close()
        except Exception:
            return self._unavailable(
                requested_start,
                requested_end,
                "push_inbox_query_failed",
                coverage=coverage,
            )

        evidence: list[OfflineRecognitionEvidence] = []
        audits: list[RecognitionRecordAudit] = []
        for row in rows:
            try:
                self._validate_row(row)
                event_id = _push_event_id(row)
                if event_id != row.canonical_event_id:
                    raise ValueError("canonical_identity_invalid")
                spatial = _validated_spatial(row)
            except (TypeError, ValueError, KeyError):
                return self._unavailable(
                    requested_start,
                    requested_end,
                    "response_integrity_failure",
                    coverage=coverage,
                    partial_record_count=len(rows),
                )

            acceptance_record = {
                "reco_valid": getattr(row, "reco_valid", None),
                "pass_code": getattr(row, "pass_code", None),
                "rule_id": row.rule_id,
                "similarity": row.similarity,
            }
            try:
                acceptance = self.acceptance_policy.assess(acceptance_record)
            except (TypeError, ValueError, KeyError):
                return self._unavailable(
                    requested_start,
                    requested_end,
                    "recognition_acceptance_policy_failure",
                    coverage=coverage,
                    partial_record_count=len(rows),
                )
            if not acceptance.accepted:
                audits.append(_push_audit(row, "rejected", acceptance.reason))
                continue

            try:
                historical = self.historical_identity_resolver.resolve_at(
                    device_id=row.device_id,
                    group_id=row.group_id,
                    person_id=row.person_id,
                    unique_id=row.unique_id,
                    occurred_at=row.occurred_at,
                )
            except Exception:
                return self._unavailable(
                    requested_start,
                    requested_end,
                    "historical_mapping_query_failed",
                    coverage=coverage,
                    partial_record_count=len(rows),
                )
            if historical.status != "resolved" or not historical.member_id:
                audits.append(_push_audit(
                    row,
                    "conflict" if historical.status == "conflict" else "rejected",
                    f"mapping_historical_{historical.status}",
                ))
                # A complete Push interval does not make an unresolved
                # historical principal a trustworthy empty result.  Preserve
                # the audit and fail the Provider closed instead of allowing
                # the caller to classify this as ``unmatched``.
                return self._unavailable(
                    requested_start,
                    requested_end,
                    "historical_mapping_unavailable",
                    coverage=coverage,
                    partial_record_count=len(rows),
                    record_audits=tuple(audits),
                    raw_record_count=len(rows),
                    coverage_complete=True,
                )

            diagnostics: list[dict[str, Any]] = []
            if row.member_id and row.member_id != historical.member_id:
                diagnostics.append({
                    "reason": "inbox_member_mapping_mismatch",
                    "inbox_member_id": row.member_id,
                    "historical_member_id": historical.member_id,
                })
            audits.append(_push_audit(row, "accepted", None, tuple(diagnostics)))
            evidence.append(OfflineRecognitionEvidence(
                evidence_id=_push_evidence_id(row.canonical_event_id),
                canonical_event_id=row.canonical_event_id,
                device_id=row.device_id,
                occurred_at=row.occurred_at,
                member_id=historical.member_id,
                similarity=float(row.similarity),
                source="push",
                log_id=None,
                group_id=row.group_id,
                person_id=row.person_id,
                unique_id=row.unique_id,
                source_event_id=row.source_event_id,
                device_occurred_at_raw=row.device_occurred_at_raw,
                device_utc_offset_minutes=row.device_utc_offset_minutes,
                rule_id=row.rule_id,
                reco_valid=getattr(row, "reco_valid", None),
                pass_code=getattr(row, "pass_code", None),
                face_spatial_evidence=(
                    spatial.to_safe_dict() if spatial is not None else None
                ),
                capture_path=row.capture_path,
                background_capture_path=row.background_capture_path,
                background_capture_sha256=row.background_capture_sha256,
                background_capture_frame_size=(
                    (row.background_capture_width, row.background_capture_height)
                    if row.background_capture_width is not None
                    and row.background_capture_height is not None
                    else None
                ),
                historical_mapping_status=historical.status,
                historical_revision_ids=historical.revision_ids,
            ))

        return RecognitionRecordQueryResult(
            status="complete",
            requested_start=requested_start,
            requested_end=requested_end,
            accepted_evidence=tuple(evidence),
            record_audits=tuple(audits),
            pages_fetched=0,
            raw_record_count=len(rows),
            partial_record_count=0,
            coverage_complete=True,
            failure_reason=None,
            source_mode="durable_push_inbox",
            coverage_policy_version=coverage.policy_version,
            coverage_material_hash=coverage.material_hash,
            coverage_start=coverage.coverage_start,
            coverage_end=coverage.coverage_end,
        )

    @staticmethod
    def _validate_row(row: DeviceRecognitionInbox) -> None:
        if not isinstance(row.canonical_event_id, str) or not row.canonical_event_id:
            raise ValueError("canonical_identity_invalid")
        if row.source != "push" or not isinstance(row.device_id, str):
            raise ValueError("source_device_scope_invalid")
        if not isinstance(row.source_event_id, str) or not row.source_event_id:
            raise ValueError("source_event_invalid")
        if not isinstance(row.occurred_at, datetime) or row.occurred_at.tzinfo is not None:
            raise ValueError("occurred_at_invalid")
        if not isinstance(row.unique_id, str):
            raise ValueError("identity_fields_invalid")
        if not row.unique_id and (
            not isinstance(row.group_id, int) or not isinstance(row.person_id, int)
        ):
            raise ValueError("identity_fields_invalid")
        if row.group_id is not None and not isinstance(row.group_id, int):
            raise ValueError("identity_fields_invalid")
        if row.person_id is not None and not isinstance(row.person_id, int):
            raise ValueError("identity_fields_invalid")
        if not isinstance(row.similarity, (int, float)) or isinstance(row.similarity, bool):
            raise ValueError("similarity_invalid")
        if not math.isfinite(float(row.similarity)) or not 0 <= float(row.similarity) <= 1:
            raise ValueError("similarity_invalid")
        if row.rule_id is not None and (
            not isinstance(row.rule_id, int) or isinstance(row.rule_id, bool)
        ):
            raise ValueError("rule_invalid")
        if row.capture_path is not None and not isinstance(row.capture_path, str):
            raise ValueError("capture_path_invalid")
        if row.background_capture_path is not None and not isinstance(
            row.background_capture_path, str
        ):
            raise ValueError("background_capture_path_invalid")
        if row.background_capture_sha256 is not None and (
            not isinstance(row.background_capture_sha256, str)
            or len(row.background_capture_sha256) != 64
        ):
            raise ValueError("background_capture_hash_invalid")

    @staticmethod
    def _unavailable(
        start: datetime,
        end: datetime,
        reason: str,
        *,
        coverage: Any = None,
        partial_record_count: int = 0,
        record_audits: tuple[RecognitionRecordAudit, ...] = (),
        raw_record_count: int = 0,
        coverage_complete: bool = False,
    ) -> RecognitionRecordQueryResult:
        return RecognitionRecordQueryResult(
            status="unavailable",
            requested_start=start,
            requested_end=end,
            accepted_evidence=(),
            record_audits=record_audits,
            pages_fetched=0,
            raw_record_count=raw_record_count,
            partial_record_count=partial_record_count,
            coverage_complete=coverage_complete,
            failure_reason=reason,
            source_mode="durable_push_inbox",
            coverage_policy_version=getattr(coverage, "policy_version", None),
            coverage_material_hash=getattr(coverage, "material_hash", None),
            coverage_start=getattr(coverage, "coverage_start", None),
            coverage_end=getattr(coverage, "coverage_end", None),
        )


def _push_event_id(row: DeviceRecognitionInbox) -> str:
    return canonical_event_id(
        DeviceRecognitionEvent(
            device_id=row.device_id,
            source="push",
            source_event_id=row.source_event_id,
            occurred_at=row.occurred_at,
            group_id=row.group_id,
            person_id=row.person_id,
            unique_id=row.unique_id,
            similarity=float(row.similarity),
            rule_id=row.rule_id,
        )
    )


def _utc_naive_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validated_spatial(row: DeviceRecognitionInbox) -> Optional[FaceSpatialEvidence]:
    if row.face_spatial_evidence is None:
        return None
    spatial = FaceSpatialEvidence.from_safe_dict(row.face_spatial_evidence)
    if spatial is None:
        raise ValueError("face_spatial_evidence_invalid")
    return spatial


def _push_evidence_id(canonical_id: str) -> str:
    return "durable-push-recognition:" + hashlib.sha256(
        canonical_id.encode("utf-8")
    ).hexdigest()[:24]


def _push_audit(
    row: DeviceRecognitionInbox,
    status: str,
    reason: Optional[str],
    diagnostics: tuple[dict[str, Any], ...] = (),
) -> RecognitionRecordAudit:
    return RecognitionRecordAudit(
        canonical_event_id=row.canonical_event_id,
        log_id=None,
        status=status,
        reason=reason,
        similarity=float(row.similarity),
        member_id=None,
        source_event_id=row.source_event_id,
        diagnostics=diagnostics,
    )


def _parse_page_response(
    response: Any,
    *,
    completion_mode: str,
    page_num: int,
    page_size: int,
    records_seen: int,
) -> Optional[tuple[list[dict[str, Any]], bool, Optional[int], Optional[Mapping[str, Any]]]]:
    if completion_mode == "verified_short_page":
        if not isinstance(response, list):
            return None
        return response, len(response) < page_size, page_num + 1, None

    if completion_mode != "explicit_metadata" or not isinstance(response, Mapping):
        return None
    records = response.get("records")
    if not isinstance(records, list):
        return None
    has_next = response.get("has_next")
    total_count = response.get("total_count")
    next_page_value = response.get("next_page")
    if has_next is not None and not isinstance(has_next, bool):
        return None
    if total_count is not None and (
        isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or total_count < 0
    ):
        return None
    if next_page_value is not None and (
        isinstance(next_page_value, bool)
        or not isinstance(next_page_value, int)
        or next_page_value <= page_num
    ):
        return None
    count_after_page = records_seen + len(records)
    if total_count is not None and count_after_page > total_count:
        return None
    eof_by_total = total_count is not None and count_after_page == total_count
    if has_next is True and eof_by_total:
        return None
    if has_next is False and total_count is not None and not eof_by_total:
        return None
    if has_next is None and total_count is None:
        return None
    eof_proven = has_next is False or eof_by_total
    if not eof_proven and has_next is not True and total_count is None:
        return None
    next_page = None if eof_proven else (next_page_value or page_num + 1)
    metadata = {
        "has_next": has_next,
        "next_page": next_page_value,
        "total_count": total_count,
    }
    return records, eof_proven, next_page, metadata


_REQUIRED_FIELDS = {
    "log_id": int,
    "group_id": int,
    "person_id": int,
    "occurred_at": datetime,
    "pass_code": int,
    "similarity": (int, float),
    "rule_id": int,
    "reco_valid": bool,
    "unique_id": str,
}


def _validate_record_shape(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("invalid_record_shape")
    for field, expected in _REQUIRED_FIELDS.items():
        if field not in value or isinstance(value[field], bool) and field != "reco_valid":
            raise ValueError("required_field_missing")
        if not isinstance(value[field], expected):
            raise ValueError("required_field_type_invalid")
    similarity = float(value["similarity"])
    if not math.isfinite(similarity) or not 0 <= similarity <= 1:
        raise ValueError("similarity_invalid")
    if not value["unique_id"] and (
        value["group_id"] is None or value["person_id"] is None
    ):
        raise ValueError("identity_fields_invalid")
    if value["occurred_at"].tzinfo is not None:
        raise ValueError("occurred_at_must_be_utc_naive")


def _event_id(device_id: str, record: dict[str, Any]) -> str:
    return canonical_event_id(
        DeviceRecognitionEvent(
            device_id=device_id,
            source="poll",
            source_event_id=str(record["log_id"]),
            occurred_at=record["occurred_at"],
            group_id=record["group_id"],
            person_id=record["person_id"],
            unique_id=record["unique_id"],
            similarity=float(record["similarity"]),
            rule_id=record["rule_id"],
            reco_valid=record["reco_valid"],
            pass_code=record["pass_code"],
        )
    )


def _audit(
    record: dict[str, Any],
    event_id: str,
    status: str,
    reason: Optional[str],
    member_id: Optional[str],
) -> RecognitionRecordAudit:
    return RecognitionRecordAudit(
        canonical_event_id=event_id,
        log_id=record["log_id"],
        status=status,
        reason=reason,
        similarity=float(record["similarity"]),
        member_id=member_id,
    )


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc
    if not result.is_finite():
        raise ValueError(f"{field}_invalid")
    return result


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return "0" if rendered == "-0" else rendered
