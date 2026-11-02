"""SQLAlchemy read adapter for the MB-01A query port."""

from __future__ import annotations

from typing import Any, Optional

from agent.analytics.member_behavior import (
    MEMBER_BEHAVIOR_EVENT_TYPES,
    MemberBehaviorEvent,
    MemberBehaviorMember,
    ResolvedMemberBehaviorScope,
)
from agent.models import BehaviorEvent, Member


class SqlAlchemyMemberBehaviorReadAdapter:
    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    def find_active_member_by_id(
        self, member_id: str
    ) -> Optional[MemberBehaviorMember]:
        db = self.session_factory()
        try:
            row = db.query(Member).filter(
                Member.id == member_id, Member.is_active.is_(True)
            ).one_or_none()
            return _member(row) if row is not None else None
        finally:
            db.close()

    def find_active_members_by_name(
        self, name: str, *, limit: int
    ) -> list[MemberBehaviorMember]:
        db = self.session_factory()
        try:
            rows = db.query(Member).filter(
                Member.name == name, Member.is_active.is_(True)
            ).order_by(Member.id).limit(limit).all()
            return [_member(row) for row in rows]
        finally:
            db.close()

    def list_eligible_events(
        self,
        *,
        member_id: str,
        scope: ResolvedMemberBehaviorScope,
        limit: Optional[int],
    ) -> list[MemberBehaviorEvent]:
        db = self.session_factory()
        try:
            query = db.query(BehaviorEvent).filter(
                BehaviorEvent.member_id == member_id,
                BehaviorEvent.store_id == scope.store_id,
                BehaviorEvent.event_type.in_(MEMBER_BEHAVIOR_EVENT_TYPES),
                BehaviorEvent.assessment == "confirmed",
                BehaviorEvent.is_demo.is_(False),
                BehaviorEvent.identity_status == "confirmed",
                BehaviorEvent.identity_binding_level == "confirmed",
                BehaviorEvent.identity_source == "offline_track_association",
                BehaviorEvent.occurred_at >= scope.utc_start_at,
                BehaviorEvent.occurred_at < scope.utc_end_at,
            )
            if scope.camera_id is not None:
                query = query.filter(BehaviorEvent.camera_id == scope.camera_id)
            query = query.order_by(BehaviorEvent.occurred_at, BehaviorEvent.id)
            if limit is not None:
                query = query.limit(limit)
            return [_event(row) for row in query.all()]
        finally:
            db.close()

    def list_recent_eligible_events(
        self,
        *,
        scope: ResolvedMemberBehaviorScope,
        limit: int,
    ) -> list[MemberBehaviorEvent]:
        db = self.session_factory()
        try:
            query = db.query(BehaviorEvent, Member).join(
                Member, BehaviorEvent.member_id == Member.id
            ).filter(
                Member.is_active.is_(True),
                BehaviorEvent.store_id == scope.store_id,
                BehaviorEvent.event_type.in_(MEMBER_BEHAVIOR_EVENT_TYPES),
                BehaviorEvent.assessment == "confirmed",
                BehaviorEvent.is_demo.is_(False),
                BehaviorEvent.identity_status == "confirmed",
                BehaviorEvent.identity_binding_level == "confirmed",
                BehaviorEvent.identity_source == "offline_track_association",
                BehaviorEvent.occurred_at >= scope.utc_start_at,
                BehaviorEvent.occurred_at < scope.utc_end_at,
            )
            if scope.camera_id is not None:
                query = query.filter(BehaviorEvent.camera_id == scope.camera_id)
            if scope.product_name is not None:
                query = query.filter(
                    BehaviorEvent.details["product_name"].as_string()
                    == scope.product_name
                )
            rows = query.order_by(
                BehaviorEvent.occurred_at.desc(), BehaviorEvent.id.desc()
            ).limit(limit).all()
            # The query selects the newest bounded page, while the public
            # timeline remains chronological for natural report generation.
            rows.reverse()
            return [_event(event, member=_member(member)) for event, member in rows]
        finally:
            db.close()


def _member(row: Member) -> MemberBehaviorMember:
    return MemberBehaviorMember(
        member_id=row.id,
        display_name=row.name,
        tier=row.tier,
    )


def _event(
    row: BehaviorEvent,
    *,
    member: MemberBehaviorMember | None = None,
) -> MemberBehaviorEvent:
    return MemberBehaviorEvent(
        event_id=row.id,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        duration_seconds=row.duration_seconds,
        details=dict(row.details) if isinstance(row.details, dict) else {},
        member=member,
    )
