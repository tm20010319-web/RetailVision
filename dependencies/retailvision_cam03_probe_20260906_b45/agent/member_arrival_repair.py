"""Explicit historical Member Arrival repair from trusted evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from agent.member_statistics import MemberStatisticsRecalculator
from agent.models import CustomerEntryEvent, DeviceRecognitionInbox, MemberArrival, MemberArrivalEffect, Visit
from agent.visit_merge_service import resolve_canonical_visit_id


class MemberArrivalRepairService:
    def __init__(self, session_factory: Callable[[], Session], *, clock=datetime.utcnow, effect_types=("welcome_display", "staff_alert", "coupon_issuance", "customer_profile")) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.effect_types = tuple(effect_types)
        self.statistics = MemberStatisticsRecalculator(session_factory)

    def repair_visit(self, visit_id: str, *, apply: bool = False) -> dict:
        db = self.session_factory()
        try:
            if apply and db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            canonical_id = resolve_canonical_visit_id(db, visit_id)
            visit = db.get(Visit, canonical_id)
            if visit is None or not visit.member_id:
                return {"visit_id": canonical_id, "status": "skipped", "reason": "member_missing"}
            existing = db.query(MemberArrival).filter(
                MemberArrival.visit_id == canonical_id,
                MemberArrival.superseded_by_arrival_id.is_(None),
            ).one_or_none()
            if existing is not None:
                return {"visit_id": canonical_id, "status": "no_change", "arrival_id": existing.id}
            evidence = self._trusted_evidence(db, visit)
            if evidence is None:
                return {"visit_id": canonical_id, "status": "skipped", "reason": "trusted_arrival_evidence_missing"}
            if not apply:
                return {"visit_id": canonical_id, "status": "would_create", "evidence": evidence[0]}
            arrival = MemberArrival(
                visit_id=canonical_id, member_id=visit.member_id,
                recognition_event_id=evidence[1], occurred_at=evidence[2],
                arrival_source="historical_repair",
            )
            db.add(arrival)
            db.flush()
            for effect_type in self.effect_types:
                db.add(MemberArrivalEffect(
                    member_arrival_id=arrival.id, effect_type=effect_type,
                    status="suppressed", reason="repair_mode",
                ))
            statistics = self.statistics.recalculate(visit.member_id, session=db)
            db.commit()
            return {
                "visit_id": canonical_id, "status": "created", "arrival_id": arrival.id,
                "evidence": evidence[0], "statistics": statistics,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _trusted_evidence(db: Session, visit: Visit):
        if visit.device_event_id:
            inbox = db.get(DeviceRecognitionInbox, visit.device_event_id)
            if (
                inbox is not None
                and inbox.recognition_status == "accepted"
                and inbox.store_resolution_status == "resolved"
                and inbox.member_id == visit.member_id
                and inbox.store_id == visit.store_id
            ):
                return "accepted_recognition", inbox.canonical_event_id, inbox.occurred_at
        if visit.entry_event_id:
            entry = db.get(CustomerEntryEvent, visit.entry_event_id)
            if (
                entry is not None
                and entry.member_id == visit.member_id
                and entry.store_id == visit.store_id
            ):
                return "identified_entry", None, entry.occurred_at
        return None
