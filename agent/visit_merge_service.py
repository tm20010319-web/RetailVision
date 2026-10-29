"""Audit-preserving convergence of duplicate Reusable Visits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from agent.models import (
    AssociationOutcomeRebuildJob,
    BehaviorEvent,
    DeviceRecognitionInbox,
    Member,
    MemberArrival,
    MemberArrivalEffect,
    MemberEntryEvent,
    PurchaseRecord,
    Visit,
    VisitEntryEvidence,
    ZoneStayEvent,
)
from agent.member_statistics import MemberStatisticsRecalculator


@dataclass(frozen=True)
class VisitMergeResult:
    canonical_visit_id: str
    superseded_visit_ids: tuple[str, ...]
    moved_counts: dict[str, int]


@dataclass(frozen=True)
class VisitMergePlan:
    """Read-only canonical selection for a planned merge."""

    canonical_visit_id: str
    duplicate_visit_ids: tuple[str, ...]


class VisitMergeService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.statistics = MemberStatisticsRecalculator(session_factory)

    def merge_reusable(
        self,
        *,
        member_id: str,
        store_id: str,
        now: Optional[datetime] = None,
        inactivity_timeout_seconds: float = 1800.0,
        max_visit_duration_seconds: float = 14400.0,
    ) -> VisitMergeResult:
        if not member_id or not store_id:
            raise ValueError("member_id and store_id are required")
        db = self.session_factory()
        try:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            resolved_now = (now or self.clock()).replace(tzinfo=None)
            inactivity_cutoff = resolved_now - timedelta(
                seconds=inactivity_timeout_seconds
            )
            duration_cutoff = resolved_now - timedelta(
                seconds=max_visit_duration_seconds
            )
            candidate_ids = [
                visit.id
                for visit in db.query(Visit).filter(
                    Visit.member_id == member_id,
                    Visit.store_id == store_id,
                    Visit.exit_time.is_(None),
                    Visit.superseded_by_visit_id.is_(None),
                )
                if visit.entry_time is not None
                and visit.entry_time >= duration_cutoff
                and self._activity_at(visit) >= inactivity_cutoff
            ]
            result = self.merge_in_session(
                db, member_id=member_id, store_id=store_id,
                now=resolved_now,
                candidate_visit_ids=candidate_ids,
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def preview_reusable(
        self, *, member_id: str, store_id: str, now: Optional[datetime] = None
    ) -> dict:
        resolved_now = (now or self.clock()).replace(tzinfo=None)
        db = self.session_factory()
        try:
            candidates = [
                visit for visit in db.query(Visit).filter(
                    Visit.member_id == member_id, Visit.store_id == store_id,
                    Visit.exit_time.is_(None), Visit.superseded_by_visit_id.is_(None),
                )
                if visit.entry_time is not None
                and resolved_now - visit.entry_time <= timedelta(seconds=14400)
                and resolved_now - self._activity_at(visit) <= timedelta(seconds=1800)
            ]
            if not candidates:
                return {"status": "no_change", "canonical": None, "duplicates": ()}
            canonical = min(candidates, key=self._canonical_key)
            return {
                "status": "would_merge" if len(candidates) > 1 else "no_change",
                "canonical": canonical.id,
                "duplicates": tuple(visit.id for visit in candidates if visit.id != canonical.id),
            }
        finally:
            db.close()

    def merge_in_session(
        self,
        db: Session,
        *,
        member_id: str,
        store_id: str,
        now: datetime,
        candidate_visit_ids: Optional[list[str]] = None,
    ) -> VisitMergeResult:
        query = db.query(Visit).filter(
                Visit.member_id == member_id,
                Visit.store_id == store_id,
                Visit.exit_time.is_(None),
                Visit.superseded_by_visit_id.is_(None),
        )
        if candidate_visit_ids is not None:
            query = query.filter(Visit.id.in_(candidate_visit_ids))
        visits = query.all()
        if not visits:
            raise ValueError("no reusable visits to merge")
        canonical = min(visits, key=self._canonical_key)
        duplicates = [visit for visit in visits if visit.id != canonical.id]
        if not duplicates:
            return VisitMergeResult(canonical.id, (), {})
        counts = {
            "behavior_events": 0,
            "zone_stay_events": 0,
            "member_entry_events": 0,
            "recognition_inbox": 0,
            "purchase_records": 0,
            "visit_entry_evidence": 0,
        }
        duplicate_ids = [visit.id for visit in duplicates]
        affected_recognitions = [
            row[0]
            for row in db.query(DeviceRecognitionInbox.canonical_event_id)
            .filter(DeviceRecognitionInbox.matched_visit_id.in_(duplicate_ids + [canonical.id]))
            .all()
        ]
        counts["behavior_events"] = self._move(
            db, BehaviorEvent, "visit_id", duplicate_ids, canonical.id
        )
        counts["zone_stay_events"] = self._move(
            db, ZoneStayEvent, "visit_id", duplicate_ids, canonical.id
        )
        counts["member_entry_events"] = self._move(
            db, MemberEntryEvent, "visit_id", duplicate_ids, canonical.id
        )
        counts["recognition_inbox"] = self._move(
            db, DeviceRecognitionInbox, "matched_visit_id", duplicate_ids, canonical.id
        )
        counts["purchase_records"] = self._move(
            db, PurchaseRecord, "visit_id", duplicate_ids, canonical.id
        )
        counts["visit_entry_evidence"] = self._move(
            db, VisitEntryEvidence, "visit_id", duplicate_ids, canonical.id
        )
        self._merge_arrivals(db, canonical, duplicates, now)
        for duplicate in duplicates:
            duplicate.exit_time = now
            duplicate.closed_reason = "superseded"
            duplicate.superseded_by_visit_id = canonical.id
            duplicate.superseded_at = now
            duplicate.superseded_reason = "duplicate_reusable_visit"
        db.query(Visit).filter(
            Visit.superseded_by_visit_id.in_(duplicate_ids)
        ).update({Visit.superseded_by_visit_id: canonical.id}, synchronize_session=False)
        self.statistics.recalculate(member_id, session=db)
        for recognition_event_id in affected_recognitions:
            inbox = db.get(DeviceRecognitionInbox, recognition_event_id)
            if inbox is not None:
                inbox.projection_pending = True
            existing_job = db.query(AssociationOutcomeRebuildJob).filter(
                AssociationOutcomeRebuildJob.recognition_event_id == recognition_event_id,
                AssociationOutcomeRebuildJob.status.in_(("pending", "failed")),
            ).first()
            if existing_job is None:
                db.add(AssociationOutcomeRebuildJob(
                    recognition_event_id=recognition_event_id,
                    visit_id=canonical.id,
                    reason="visit_merge",
                ))
            else:
                existing_job.visit_id = canonical.id
                existing_job.reason = "visit_merge"
                existing_job.status = "pending"
                existing_job.next_retry_at = None
        return VisitMergeResult(canonical.id, tuple(duplicate_ids), counts)

    def preview_in_session(
        self,
        db: Session,
        *,
        candidate_visit_ids: list[str],
        member_id: str,
        store_id: str,
        anonymous_visit_id: str,
    ) -> VisitMergePlan:
        """Plan canonical selection without changing any persisted facts."""
        if not candidate_visit_ids:
            raise ValueError("candidate visits are required")
        visits = db.query(Visit).filter(Visit.id.in_(candidate_visit_ids)).all()
        by_id = {visit.id: visit for visit in visits}
        if len(by_id) != len(set(candidate_visit_ids)):
            raise ValueError("candidate visit not found")
        if any(
            visit.exit_time is not None
            or visit.superseded_by_visit_id is not None
            or visit.store_id != store_id
            or (visit.id != anonymous_visit_id and visit.member_id != member_id)
            or (visit.id == anonymous_visit_id and visit.member_id is not None)
            for visit in visits
        ):
            raise ValueError("candidate visit is not reusable")
        canonical = min(visits, key=self._canonical_key)
        return VisitMergePlan(
            canonical_visit_id=canonical.id,
            duplicate_visit_ids=tuple(
                visit.id for visit in visits if visit.id != canonical.id
            ),
        )

    @staticmethod
    def _move(db: Session, model, column: str, duplicate_ids: list[str], canonical_id: str) -> int:
        if not duplicate_ids:
            return 0
        return int(
            db.query(model)
            .filter(getattr(model, column).in_(duplicate_ids))
            .update({getattr(model, column): canonical_id}, synchronize_session=False)
        )

    @staticmethod
    def _canonical_key(visit: Visit) -> tuple:
        return (
            0 if visit.visit_source == "entrance_crossing" and visit.verification_status == "confirmed" else 1,
            0 if visit.entry_event_id is not None else 1,
            visit.entry_time or datetime.max,
            visit.created_at or datetime.max,
            visit.id,
        )

    @staticmethod
    def _activity_at(visit: Visit) -> datetime:
        return (
            visit.last_seen_at
            or visit.identified_at
            or visit.identity_time
            or visit.entry_time
            or datetime.min
        )

    def _merge_arrivals(
        self, db: Session, canonical: Visit, duplicates: list[Visit], now: datetime
    ) -> None:
        visit_ids = [canonical.id, *(visit.id for visit in duplicates)]
        arrivals = (
            db.query(MemberArrival)
            .filter(
                MemberArrival.visit_id.in_(visit_ids),
                MemberArrival.superseded_by_arrival_id.is_(None),
            )
            .order_by(MemberArrival.occurred_at, MemberArrival.created_at, MemberArrival.id)
            .all()
        )
        if not arrivals:
            return
        winner = arrivals[0]
        losers = arrivals[1:]
        loser_ids = [arrival.id for arrival in losers]
        original_winner_visit_id = winner.visit_id
        for loser in losers:
            loser.superseded_by_arrival_id = winner.id
            loser.superseded_at = now
            loser.superseded_reason = "duplicate_visit_merge"
        if loser_ids:
            db.query(MemberArrival).filter(
                MemberArrival.superseded_by_arrival_id.in_(loser_ids)
            ).update({MemberArrival.superseded_by_arrival_id: winner.id}, synchronize_session=False)
        self._merge_effects(db, arrivals, winner, now)
        db.flush()
        if winner.visit_id != canonical.id:
            canonical_loser = next(
                (arrival for arrival in losers if arrival.visit_id == canonical.id),
                None,
            )
            if canonical_loser is not None:
                if db.get_bind().dialect.name == "sqlite":
                    db.connection().exec_driver_sql("PRAGMA defer_foreign_keys=ON")
                temporary_visit_id = f"merge-staging:{winner.id}"
                winner.visit_id = temporary_visit_id
                db.flush()
                canonical_loser.visit_id = original_winner_visit_id
                db.flush()
            winner.visit_id = canonical.id
            db.flush()

    @staticmethod
    def _effect_key(effect: MemberArrivalEffect, now: datetime) -> tuple:
        valid_processing = (
            effect.status == "processing"
            and effect.claim_expires_at is not None
            and effect.claim_expires_at > now
        )
        priority = {
            "succeeded": 0,
            "pending": 2,
            "failed": 3,
            "suppressed": 4,
        }.get(effect.status, 1 if valid_processing else 5)
        return (
            priority,
            effect.executed_at or datetime.max,
            effect.created_at or datetime.max,
            effect.id,
        )

    def _merge_effects(
        self,
        db: Session,
        arrivals: list[MemberArrival],
        winner: MemberArrival,
        now: datetime,
    ) -> None:
        effects = (
            db.query(MemberArrivalEffect)
            .filter(
                MemberArrivalEffect.member_arrival_id.in_(
                    [arrival.id for arrival in arrivals]
                ),
                MemberArrivalEffect.superseded_by_effect_id.is_(None),
            )
            .all()
        )
        by_type: dict[str, list[MemberArrivalEffect]] = {}
        for effect in effects:
            by_type.setdefault(effect.effect_type, []).append(effect)
        for typed_effects in by_type.values():
            final = min(typed_effects, key=lambda effect: self._effect_key(effect, now))
            for effect in typed_effects:
                if effect.id == final.id:
                    continue
                effect.superseded_by_effect_id = final.id
                effect.superseded_at = now
                effect.superseded_reason = "duplicate_arrival_merge"
            loser_effect_ids = [effect.id for effect in typed_effects if effect.id != final.id]
            if loser_effect_ids:
                db.query(MemberArrivalEffect).filter(
                    MemberArrivalEffect.superseded_by_effect_id.in_(loser_effect_ids)
                ).update({MemberArrivalEffect.superseded_by_effect_id: final.id}, synchronize_session=False)
            db.flush()
            final.member_arrival_id = winner.id
            db.flush()


def resolve_canonical_visit_id(db: Session, visit_id: str) -> str:
    """Resolve the final canonical Visit while rejecting corrupt cycles."""
    current_id = visit_id
    seen: set[str] = set()
    while True:
        if current_id in seen:
            raise ValueError("visit supersede cycle detected")
        seen.add(current_id)
        visit = db.get(Visit, current_id)
        if visit is None:
            raise ValueError("visit not found")
        if visit.superseded_by_visit_id is None:
            return visit.id
        current_id = visit.superseded_by_visit_id
