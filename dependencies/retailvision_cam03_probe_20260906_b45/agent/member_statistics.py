"""Recalculate member arrival statistics from effective business facts."""

from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from agent.models import Member, MemberArrival, Visit


class MemberStatisticsRecalculator:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def recalculate(self, member_id: str, *, session: Session | None = None) -> dict:
        owns = session is None
        db = session or self.session_factory()
        try:
            member = db.get(Member, member_id)
            if member is None:
                raise ValueError("member not found")
            before = {"visit_count": member.visit_count or 0, "last_visit": member.last_visit}
            rows = db.query(MemberArrival, Visit).join(
                Visit, Visit.id == MemberArrival.visit_id
            ).filter(
                MemberArrival.member_id == member_id,
                MemberArrival.superseded_by_arrival_id.is_(None),
                Visit.superseded_by_visit_id.is_(None),
                Visit.member_id == member_id,
            ).all()
            member.visit_count = len(rows)
            member.last_visit = max(
                (visit.entry_time for _arrival, visit in rows if visit.entry_time),
                default=None,
            )
            if owns:
                db.commit()
            return {
                "before": before,
                "after": {"visit_count": member.visit_count, "last_visit": member.last_visit},
            }
        except Exception:
            if owns:
                db.rollback()
            raise
        finally:
            if owns:
                db.close()

    def preview(self, member_id: str) -> dict:
        db = self.session_factory()
        try:
            member = db.get(Member, member_id)
            if member is None:
                raise ValueError("member not found")
            rows = db.query(MemberArrival, Visit).join(Visit, Visit.id == MemberArrival.visit_id).filter(
                MemberArrival.member_id == member_id,
                MemberArrival.superseded_by_arrival_id.is_(None),
                Visit.superseded_by_visit_id.is_(None),
                Visit.member_id == member_id,
            ).all()
            return {
                "before": {"visit_count": member.visit_count or 0, "last_visit": member.last_visit},
                "after": {
                    "visit_count": len(rows),
                    "last_visit": max((visit.entry_time for _arrival, visit in rows if visit.entry_time), default=None),
                },
            }
        finally:
            db.close()
