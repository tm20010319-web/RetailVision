"""Durable behavior events, evidence requests, and peak-entry queries."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from agent.member_priority import normalize_tier
from agent.models import (
    BehaviorEvent,
    CustomerEntryConfirmedEvent,
    CustomerEntryEvent,
    EvidenceClip,
    Member,
    Visit,
    VisitEntryEvidence,
    new_uuid,
)
from agent.visit_merge_service import resolve_canonical_visit_id


BEHAVIOR_TYPES = {
    "staff_greeting",
    "merchandise_dwell",
    "merchandise_reach",
    "merchandise_pick_up",
    "merchandise_holding",
    "merchandise_put_down",
}
ASSESSMENTS = {"confirmed", "not_observed", "uncertain"}
PEAK_LOOKBACK_DAYS = {3, 7}


class BehaviorEventError(ValueError):
    pass


class BehaviorEventConflictError(BehaviorEventError):
    pass


@dataclass(frozen=True)
class EntryEventInput:
    entry_key: str
    camera_id: str
    track_id: int
    track_token: str
    occurred_at: datetime
    confidence: float
    member_id: Optional[str] = None
    member_tier: Optional[str] = None


@dataclass(frozen=True)
class BehaviorEventInput:
    event_key: str
    event_type: str
    assessment: str
    camera_id: str
    customer_track_id: int
    customer_track_token: str
    started_at: datetime
    ended_at: datetime
    confidence: Optional[float] = None
    staff_track_id: Optional[int] = None
    staff_track_token: Optional[str] = None
    member_id: Optional[str] = None
    member_tier: Optional[str] = None
    area_id: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    evidence_start: Optional[datetime] = None
    evidence_end: Optional[datetime] = None
    entry_event_id: Optional[str] = None
    visit_id: Optional[str] = None
    is_demo: bool = False
    identity_status: Optional[str] = None
    identity_source: Optional[str] = None
    identity_confidence: Optional[float] = None
    identity_binding_level: Optional[str] = None
    binding_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    policy_version_id: Optional[str] = None
    algorithm_version: Optional[str] = None
    parameter_version: Optional[str] = None


class EvidenceClipStore:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def request(
        self,
        *,
        owner_type: str,
        owner_id: str,
        camera_id: str,
        requested_start: datetime,
        requested_end: datetime,
    ) -> EvidenceClip:
        _nonempty(owner_type, "owner_type")
        _nonempty(owner_id, "owner_id")
        _nonempty(camera_id, "camera_id")
        _time_range(requested_start, requested_end)
        db = self.session_factory()
        try:
            existing = (
                db.query(EvidenceClip)
                .filter_by(owner_type=owner_type, owner_id=owner_id)
                .first()
            )
            if existing is not None:
                return existing
            row = EvidenceClip(
                id=new_uuid(),
                owner_type=owner_type,
                owner_id=owner_id,
                camera_id=camera_id,
                requested_start=requested_start,
                requested_end=requested_end,
                status="pending",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row
        except IntegrityError:
            db.rollback()
            row = (
                db.query(EvidenceClip)
                .filter_by(owner_type=owner_type, owner_id=owner_id)
                .one()
            )
            return row
        finally:
            db.close()

    def status(self, clip_id: str) -> Optional[dict[str, Any]]:
        _nonempty(clip_id, "clip_id")
        db = self.session_factory()
        try:
            clip = db.get(EvidenceClip, clip_id)
            if clip is None:
                return None
            return {
                "id": clip.id,
                "owner_type": clip.owner_type,
                "owner_id": clip.owner_id,
                "camera_id": clip.camera_id,
                "requested_start": clip.requested_start.isoformat(),
                "requested_end": clip.requested_end.isoformat(),
                "status": clip.status,
                "attempts": clip.attempts,
                "error_code": clip.last_error,
                "available": clip.status == "ready" and bool(clip.output_path),
            }
        finally:
            db.close()

    def ready_path(self, clip_id: str) -> Optional[str]:
        _nonempty(clip_id, "clip_id")
        db = self.session_factory()
        try:
            clip = db.get(EvidenceClip, clip_id)
            if clip is None or clip.status != "ready" or not clip.output_path:
                return None
            return clip.output_path
        finally:
            db.close()


class BehaviorEventStore:
    def __init__(
        self,
        session_factory,
        *,
        greeting_mock_staff_name: Optional[str] = None,
        store_id: Optional[str] = None,
    ) -> None:
        self.session_factory = session_factory
        self.greeting_mock_staff_name = _optional_staff_name(greeting_mock_staff_name)
        self.store_id = store_id

    def record_behavior(self, value: BehaviorEventInput) -> BehaviorEvent:
        _validate_behavior(value)
        event_id = new_uuid()
        evidence_id = new_uuid()
        evidence_start = value.evidence_start or value.started_at
        evidence_end = value.evidence_end or value.ended_at
        _time_range(evidence_start, evidence_end)
        staff_name, staff_attribution_source = self._greeting_attribution(value)
        db = self.session_factory()
        try:
            existing = db.query(BehaviorEvent).filter_by(event_key=value.event_key).first()
            if existing is not None:
                return existing
            row = BehaviorEvent(
                id=event_id,
                event_key=value.event_key,
                event_type=value.event_type,
                assessment=value.assessment,
                camera_id=value.camera_id,
                customer_track_id=value.customer_track_id,
                customer_track_token=value.customer_track_token,
                track_token=value.customer_track_token,
                entry_event_id=value.entry_event_id,
                visit_id=value.visit_id,
                store_id=self.store_id,
                is_demo=value.is_demo,
                staff_track_id=value.staff_track_id,
                staff_track_token=value.staff_track_token,
                staff_name=staff_name,
                staff_attribution_source=staff_attribution_source,
                member_id=value.member_id,
                member_tier=(
                    normalize_tier(value.member_tier) if value.member_id else None
                ),
                identity_status=value.identity_status,
                identity_source=value.identity_source,
                identity_confidence=value.identity_confidence,
                identity_binding_level=value.identity_binding_level,
                binding_id=value.binding_id,
                occurred_at=value.started_at,
                evaluation_id=value.evaluation_id,
                policy_version_id=value.policy_version_id,
                algorithm_version=value.algorithm_version,
                parameter_version=value.parameter_version,
                area_id=value.area_id,
                started_at=value.started_at,
                ended_at=value.ended_at,
                duration_seconds=(value.ended_at - value.started_at).total_seconds(),
                confidence=value.confidence,
                details=dict(value.details),
                evidence_clip_id=evidence_id,
            )
            clip = EvidenceClip(
                id=evidence_id,
                owner_type="behavior_event",
                owner_id=event_id,
                camera_id=value.camera_id,
                requested_start=evidence_start,
                requested_end=evidence_end,
                status="pending",
            )
            db.add_all((row, clip))
            db.commit()
            db.refresh(row)
            return row
        except IntegrityError:
            db.rollback()
            return db.query(BehaviorEvent).filter_by(event_key=value.event_key).one()
        finally:
            db.close()

    def bulk_insert_behavior(self, *, session, events: Iterable[Any]) -> tuple[BehaviorEvent, ...]:
        """Insert already-prepared Offline rows without changing business fields."""
        rows = []
        for value in events:
            _validate_prepared_offline_behavior_row(value)
            row = BehaviorEvent(
                id=new_uuid(),
                event_key=value.event_key,
                event_type=value.event_type,
                assessment=value.assessment,
                camera_id=value.camera_id,
                customer_track_id=value.customer_track_id,
                customer_track_token=value.customer_track_token,
                track_token=value.track_token,
                entry_event_id=value.entry_event_id,
                visit_id=value.visit_id,
                store_id=value.store_id,
                is_demo=value.is_demo,
                staff_track_id=value.staff_track_id,
                staff_track_token=value.staff_track_token,
                staff_name=value.staff_name,
                staff_attribution_source=value.staff_attribution_source,
                member_id=value.member_id,
                member_tier=value.member_tier,
                identity_status=value.identity_status,
                identity_source=value.identity_source,
                identity_confidence=value.identity_confidence,
                identity_binding_level=value.identity_binding_level,
                binding_id=value.binding_id,
                backfilled_at=value.backfilled_at,
                occurred_at=value.occurred_at,
                evaluation_id=value.evaluation_id,
                policy_version_id=value.policy_version_id,
                algorithm_version=value.algorithm_version,
                parameter_version=value.parameter_version,
                area_id=value.area_id,
                started_at=value.started_at,
                ended_at=value.ended_at,
                duration_seconds=value.duration_seconds,
                confidence=value.confidence,
                details=value.details_for_db(),
                evidence_clip_id=value.evidence_clip_id,
            )
            session.add(row)
            rows.append(row)
        return tuple(rows)

    def _greeting_attribution(
        self, value: BehaviorEventInput
    ) -> tuple[Optional[str], Optional[str]]:
        if (
            value.event_type != "staff_greeting"
            or value.assessment != "confirmed"
            or self.greeting_mock_staff_name is None
        ):
            return None, None
        return self.greeting_mock_staff_name, "configured_mock"

    def attach_visit(
        self,
        *,
        track_token: str,
        visit_id: str,
        entry_event_id: Optional[str] = None,
    ) -> int:
        _nonempty(track_token, "track_token")
        _nonempty(visit_id, "visit_id")
        db = self.session_factory()
        try:
            visit = db.get(Visit, visit_id)
            if visit is None:
                raise BehaviorEventError(f"visit not found: {visit_id}")
            target_entry_id = entry_event_id or visit.entry_event_id
            if (
                entry_event_id is not None
                and visit.entry_event_id is not None
                and visit.entry_event_id != entry_event_id
            ):
                raise BehaviorEventError("entry_event_id does not belong to visit")
            conflict_filter = BehaviorEvent.entry_event_id.is_not(None)
            if target_entry_id is not None:
                conflict_filter = BehaviorEvent.entry_event_id != target_entry_id
            conflict = (
                db.query(BehaviorEvent.id, BehaviorEvent.entry_event_id)
                .filter(
                    BehaviorEvent.track_token == track_token,
                    BehaviorEvent.visit_id.is_(None),
                    conflict_filter,
                )
                .first()
            )
            if conflict is not None:
                raise BehaviorEventConflictError(
                    "behavior event is already associated with a different entry: "
                    f"{conflict.entry_event_id}"
                )
            updated = (
                db.query(BehaviorEvent)
                .filter(
                    BehaviorEvent.track_token == track_token,
                    BehaviorEvent.visit_id.is_(None),
                    or_(
                        BehaviorEvent.entry_event_id.is_(None),
                        BehaviorEvent.entry_event_id == target_entry_id,
                    ),
                )
                .update(
                    {
                        BehaviorEvent.visit_id: visit_id,
                        BehaviorEvent.entry_event_id: target_entry_id,
                    },
                    synchronize_session=False,
                )
            )
            db.commit()
            return updated
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def attach_identity_to_track(
        self,
        *,
        store_id: str,
        camera_id: str,
        runtime_instance_id: str,
        track_id: int,
        track_token: str,
        member_id: str,
        canonical_visit_id: str,
        member_tier: Optional[str],
        evaluation_id: str,
        binding_id: str,
        policy_version_id: Optional[str],
        algorithm_version: Optional[str],
        parameter_version: Optional[str],
        recognized_at: datetime,
        identity_source: str,
        identity_confidence: float,
    ) -> dict[str, Any]:
        _nonempty(store_id, "store_id")
        _nonempty(camera_id, "camera_id")
        _nonempty(runtime_instance_id, "runtime_instance_id")
        _nonempty(track_token, "track_token")
        _nonempty(member_id, "member_id")
        _nonempty(canonical_visit_id, "canonical_visit_id")
        _nonempty(evaluation_id, "evaluation_id")
        _nonempty(binding_id, "binding_id")
        try:
            token_store, token_camera, token_runtime, token_track = track_token.split(":")
            parsed_track_id = int(token_track)
        except (TypeError, ValueError):
            raise BehaviorEventError("track_token is malformed") from None
        if (
            token_store != store_id
            or token_camera != camera_id
            or token_runtime != runtime_instance_id
            or parsed_track_id != track_id
        ):
            raise BehaviorEventError("track_token scope mismatch")
        db = self.session_factory()
        stats = {
            "scanned_count": 0,
            "applied_count": 0,
            "already_applied_count": 0,
            "conflict_count": 0,
            "conflict_reasons": {},
        }
        try:
            resolved_target_id = resolve_canonical_visit_id(db, canonical_visit_id)
            target_visit = db.get(Visit, resolved_target_id)
            if target_visit is None or target_visit.member_id != member_id:
                raise BehaviorEventError("target Visit does not match member")
            if resolved_target_id != canonical_visit_id:
                raise BehaviorEventError("target Visit is not canonical")
            start = recognized_at - timedelta(seconds=30)
            rows = (
                db.query(BehaviorEvent)
                .filter(
                    BehaviorEvent.store_id == store_id,
                    BehaviorEvent.camera_id == camera_id,
                    BehaviorEvent.customer_track_id == track_id,
                    BehaviorEvent.track_token == track_token,
                    BehaviorEvent.occurred_at >= start,
                    BehaviorEvent.occurred_at <= recognized_at,
                )
                .order_by(BehaviorEvent.started_at, BehaviorEvent.id)
                .all()
            )
            stats["scanned_count"] = len(rows)

            def conflict(reason: str) -> None:
                stats["conflict_count"] += 1
                reasons = stats["conflict_reasons"]
                reasons[reason] = reasons.get(reason, 0) + 1

            for row in rows:
                if row.member_id is not None and row.member_id != member_id:
                    conflict("different_member")
                    continue
                if row.visit_id is not None:
                    try:
                        existing_canonical = resolve_canonical_visit_id(db, row.visit_id)
                    except ValueError:
                        conflict("invalid_visit_provenance")
                        continue
                    if existing_canonical != canonical_visit_id:
                        conflict("different_canonical_visit")
                        continue
                if row.entry_event_id is not None:
                    evidence = (
                        db.query(VisitEntryEvidence)
                        .filter(VisitEntryEvidence.entry_event_id == row.entry_event_id)
                        .one_or_none()
                    )
                    if evidence is not None:
                        try:
                            entry_visit_id = resolve_canonical_visit_id(
                                db, evidence.visit_id
                            )
                        except ValueError:
                            conflict("invalid_entry_visit_provenance")
                            continue
                        if entry_visit_id != canonical_visit_id:
                            conflict("different_entry_visit_provenance")
                            continue
                provenance = (
                    ("binding_id", row.binding_id, binding_id),
                    ("evaluation_id", row.evaluation_id, evaluation_id),
                    ("policy_version_id", row.policy_version_id, policy_version_id),
                    ("algorithm_version", row.algorithm_version, algorithm_version),
                    ("parameter_version", row.parameter_version, parameter_version),
                )
                incompatible = next(
                    (
                        name
                        for name, existing, incoming in provenance
                        if existing is not None and existing != incoming
                    ),
                    None,
                )
                if incompatible is not None:
                    conflict(f"different_{incompatible}")
                    continue
                already = all(
                    (
                        row.member_id == member_id,
                        row.visit_id is not None,
                        row.binding_id == binding_id,
                        row.evaluation_id == evaluation_id,
                        row.policy_version_id == policy_version_id,
                        row.algorithm_version == algorithm_version,
                        row.parameter_version == parameter_version,
                        row.identity_binding_level == "confirmed",
                    )
                )
                if already:
                    stats["already_applied_count"] += 1
                    continue
                if row.visit_id is None:
                    row.visit_id = canonical_visit_id
                row.member_id = member_id
                row.member_tier = normalize_tier(member_tier) if member_tier else None
                row.identity_status = "identified_member"
                row.identity_source = identity_source
                row.identity_confidence = identity_confidence
                row.identity_binding_level = "confirmed"
                row.binding_id = binding_id
                row.evaluation_id = evaluation_id
                row.policy_version_id = policy_version_id
                row.algorithm_version = algorithm_version
                row.parameter_version = parameter_version
                row.backfilled_at = datetime.utcnow()
                stats["applied_count"] += 1
            db.commit()
            return stats
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

class TrafficQueryService:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def peak_entry_window(
        self,
        *,
        days: int,
        now: datetime,
        camera_id: Optional[str] = None,
        window_minutes: int = 5,
    ) -> dict[str, Any]:
        if days not in PEAK_LOOKBACK_DAYS:
            raise BehaviorEventError("days must be 3 or 7")
        if isinstance(window_minutes, bool) or window_minutes != 5:
            raise BehaviorEventError("window_minutes must be 5")
        if camera_id is not None:
            _nonempty(camera_id, "camera_id")
        lookback_start = now - timedelta(days=days)
        db = self.session_factory()
        try:
            query = db.query(CustomerEntryEvent).filter(
                CustomerEntryEvent.occurred_at >= lookback_start,
                CustomerEntryEvent.occurred_at <= now,
            )
            if camera_id is not None:
                query = query.filter(CustomerEntryEvent.camera_id == camera_id)
            events = query.order_by(
                CustomerEntryEvent.occurred_at,
                CustomerEntryEvent.id,
            ).all()
            if not events:
                return {
                    "lookback_days": days,
                    "window_minutes": window_minutes,
                    "count": 0,
                    "start": None,
                    "end": None,
                    "camera_id": camera_id,
                    "event_ids": [],
                }

            width = timedelta(minutes=window_minutes)
            best_left = 0
            best_right = 1
            right = 0
            for left, event in enumerate(events):
                if right < left:
                    right = left
                window_end = event.occurred_at + width
                while right < len(events) and events[right].occurred_at <= window_end:
                    right += 1
                if (right - left, event.occurred_at) >= (
                    best_right - best_left,
                    events[best_left].occurred_at,
                ):
                    best_left = left
                    best_right = right

            selected = events[best_left:best_right]
            start = selected[0].occurred_at
            return {
                "lookback_days": days,
                "window_minutes": window_minutes,
                "count": len(selected),
                "start": start,
                "end": start + width,
                "camera_id": camera_id or selected[0].camera_id,
                "event_ids": [event.id for event in selected],
            }
        finally:
            db.close()

    def recent_behaviors(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise BehaviorEventError("limit must be an integer from 1 to 100")
        db = self.session_factory()
        try:
            rows = (
                db.query(BehaviorEvent, EvidenceClip)
                .outerjoin(EvidenceClip, EvidenceClip.id == BehaviorEvent.evidence_clip_id)
                .order_by(BehaviorEvent.started_at.desc(), BehaviorEvent.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "assessment": event.assessment,
                    "camera_id": event.camera_id,
                    "customer_track_id": event.customer_track_id,
                    "staff_track_id": event.staff_track_id,
                    "member_id": event.member_id,
                    "member_tier": event.member_tier,
                    "staff_name": event.staff_name,
                    "staff_attribution_source": event.staff_attribution_source,
                    "is_demo": event.is_demo,
                    "area_id": event.area_id,
                    "started_at": event.started_at.isoformat(),
                    "ended_at": event.ended_at.isoformat(),
                    "duration_seconds": event.duration_seconds,
                    "confidence": event.confidence,
                    "details": event.details or {},
                    "evidence": None
                    if clip is None
                    else {
                        "id": clip.id,
                        "status": clip.status,
                        "available": clip.status == "ready" and bool(clip.output_path),
                    },
                }
                for event, clip in rows
            ]
        finally:
            db.close()

    def staff_greeting_summary(
        self, *, scope: str = "official"
    ) -> list[dict[str, Any]]:
        if scope not in {"official", "demo", "all"}:
            raise BehaviorEventError("scope must be official, demo, or all")
        db = self.session_factory()
        try:
            greeting_count = func.count(BehaviorEvent.id)
            last_greeted_at = func.max(BehaviorEvent.ended_at)
            filters = [
                BehaviorEvent.event_type == "staff_greeting",
                BehaviorEvent.assessment == "confirmed",
                BehaviorEvent.staff_name.is_not(None),
            ]
            if scope == "official":
                filters.append(BehaviorEvent.is_demo.is_(False))
            elif scope == "demo":
                filters.append(BehaviorEvent.is_demo.is_(True))
            rows = (
                db.query(
                    BehaviorEvent.staff_name,
                    greeting_count.label("greeting_count"),
                    last_greeted_at.label("last_greeted_at"),
                )
                .filter(*filters)
                .group_by(BehaviorEvent.staff_name)
                .order_by(greeting_count.desc(), last_greeted_at.desc())
                .all()
            )
            return [
                {
                    "staff_name": staff_name,
                    "greeting_count": greeting_count_value,
                    "last_greeted_at": last_greeted.isoformat(),
                }
                for staff_name, greeting_count_value, last_greeted in rows
            ]
        finally:
            db.close()

    def recent_entries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise BehaviorEventError("limit must be an integer from 1 to 100")
        db = self.session_factory()
        try:
            rows = (
                db.query(CustomerEntryConfirmedEvent, Visit, Member)
                .outerjoin(
                    Visit,
                    Visit.entry_event_id == CustomerEntryConfirmedEvent.id,
                )
                .outerjoin(Member, Member.id == Visit.member_id)
                .order_by(
                    CustomerEntryConfirmedEvent.occurred_at.desc(),
                    CustomerEntryConfirmedEvent.id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": event.id,
                    "camera_id": event.camera_id,
                    "track_id": event.track_id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "member_id": visit.member_id if visit is not None else None,
                    "member_tier": member.tier if member is not None else None,
                    "confidence": event.confidence,
                    "identity_status": (
                        visit.identity_status
                        if visit is not None
                        else "anonymous"
                    ),
                    "visitor_type": (
                        visit.visitor_type
                        if visit is not None
                        else "anonymous"
                    ),
                }
                for event, visit, member in rows
            ]
        finally:
            db.close()


def _validate_behavior(value: BehaviorEventInput) -> None:
    _nonempty(value.event_key, "event_key")
    if value.event_type not in BEHAVIOR_TYPES:
        raise BehaviorEventError("unsupported event_type")
    if value.assessment not in ASSESSMENTS:
        raise BehaviorEventError("unsupported assessment")
    _nonempty(value.camera_id, "camera_id")
    _track(value.customer_track_id, "customer_track_id")
    _nonempty(value.customer_track_token, "customer_track_token")
    if value.staff_track_id is not None:
        _track(value.staff_track_id, "staff_track_id")
    if value.staff_track_token is not None:
        _nonempty(value.staff_track_token, "staff_track_token")
    _time_range(value.started_at, value.ended_at)
    if value.confidence is not None:
        _confidence(value.confidence)
    if not isinstance(value.is_demo, bool):
        raise BehaviorEventError("is_demo must be a boolean")
    if not isinstance(value.details, dict):
        raise BehaviorEventError("details must be an object")


def _validate_prepared_offline_behavior_row(value: Any) -> None:
    from agent.offline_behavior import PreparedOfflineBehaviorRow

    if not isinstance(value, PreparedOfflineBehaviorRow):
        raise BehaviorEventError("offline behavior row must be prepared before publication")
    for field in ("visit_id", "entry_event_id", "evidence_clip_id"):
        if getattr(value, field, None) is not None:
            raise BehaviorEventError(f"offline {field} must be null")
    if getattr(value, "member_id", None) is not None and getattr(
        value, "identity_status", None
    ) != "confirmed":
        raise BehaviorEventError("offline member projection requires confirmed identity")
    if getattr(value, "member_id", None) is None and getattr(
        value, "member_tier", None
    ) is not None:
        raise BehaviorEventError("offline anonymous behavior cannot have member_tier")
    _nonempty(value.store_id, "store_id")
    if value.track_token != value.customer_track_token:
        raise BehaviorEventError("offline track_token mismatch")
    expected_duration = (
        None
        if value.event_type in {"merchandise_pick_up", "merchandise_put_down"}
        else (value.ended_at - value.started_at).total_seconds()
    )
    if value.duration_seconds != expected_duration:
        raise BehaviorEventError("offline duration contract mismatch")
    if value.occurred_at != value.started_at:
        raise BehaviorEventError("offline occurred_at contract mismatch")
    if value.identity_source != "offline_track_association":
        raise BehaviorEventError("offline identity_source contract mismatch")
    if value.identity_binding_level != ("confirmed" if value.member_id else None):
        raise BehaviorEventError("offline identity_binding_level contract mismatch")
    if value.is_demo:
        raise BehaviorEventError("offline production event cannot be demo")
    _validate_behavior(
        BehaviorEventInput(
            event_key=value.event_key,
            event_type=value.event_type,
            assessment=value.assessment,
            camera_id=value.camera_id,
            customer_track_id=value.customer_track_id,
            customer_track_token=value.customer_track_token,
            started_at=value.started_at,
            ended_at=value.ended_at,
            confidence=value.confidence,
            member_id=value.member_id,
            member_tier=value.member_tier,
            details=value.details_for_db(),
            identity_status=value.identity_status,
            identity_source=value.identity_source,
            identity_confidence=value.identity_confidence,
        )
    )


def _optional_staff_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BehaviorEventError("greeting_mock_staff_name must be a string")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 128:
        raise BehaviorEventError("greeting_mock_staff_name is too long")
    return normalized


def _time_range(start: datetime, end: datetime) -> None:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise BehaviorEventError("start and end must be datetimes")
    if end < start:
        raise BehaviorEventError("end must be on or after start")


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BehaviorEventError(f"{name} is required")


def _track(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BehaviorEventError(f"{name} must be a non-negative integer")


def _confidence(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 1
    ):
        raise BehaviorEventError("confidence must be between 0 and 1")
