"""Internal identity attachment rules for entry-backed customer Visits."""

from __future__ import annotations

from datetime import datetime
from math import isclose
from typing import Callable, Optional

from sqlalchemy.orm import Session

from agent.models import CustomerEntryConfirmedEvent, IdentityEvidence, Member, Visit


class CustomerVisitIdentityError(RuntimeError):
    def __init__(self, message: str, *, persist_evidence: bool = False) -> None:
        super().__init__(message)
        self.persist_evidence = persist_evidence


class CustomerVisitIdentityConflict(CustomerVisitIdentityError):
    pass


def attach_identity_from_evidence(
    db: Session,
    visit: Visit,
    identity_evidence_id: str,
    *,
    clock: Callable[[], datetime],
    expected_member_id: Optional[str] = None,
    expected_confidence: Optional[float] = None,
) -> None:
    """Bind one eligible recognition Evidence to a Visit in the current transaction."""
    if visit.entry_event_id is None:
        raise CustomerVisitIdentityError(
            "automatic identity attachment requires an entry-backed visit"
        )
    if visit.visit_source != "entrance_crossing":
        raise CustomerVisitIdentityError(
            "automatic identity attachment requires visit_source=entrance_crossing"
        )
    if visit.verification_status != "confirmed":
        raise CustomerVisitIdentityError(
            "automatic identity attachment requires verification_status=confirmed"
        )

    evidence_query = db.query(IdentityEvidence).filter(
        IdentityEvidence.canonical_event_id == identity_evidence_id
    )
    if db.get_bind().dialect.name != "sqlite":
        evidence_query = evidence_query.with_for_update()
    evidence = evidence_query.one_or_none()
    if evidence is None:
        raise CustomerVisitIdentityError(
            f"identity evidence not found: {identity_evidence_id}"
        )
    if evidence.status in {"ignored", "unmapped", "failed"}:
        evidence.association_status = "expired"
        evidence.match_reason = f"ineligible_evidence_status:{evidence.status}"
        evidence.associated_at = clock()
        raise CustomerVisitIdentityError(
            f"identity evidence status is not eligible: {evidence.status}",
            persist_evidence=True,
        )
    if evidence.association_status in {"expired", "ambiguous"}:
        raise CustomerVisitIdentityError(
            "identity evidence association status is not eligible: "
            f"{evidence.association_status}"
        )
    if not isinstance(evidence.member_id, str) or not evidence.member_id.strip():
        raise CustomerVisitIdentityError("identity evidence has no member_id")
    if expected_member_id is not None and expected_member_id != evidence.member_id:
        raise CustomerVisitIdentityConflict(
            "member_id does not match identity evidence"
        )
    confidence = evidence.similarity
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise CustomerVisitIdentityError(
            "identity evidence similarity must be between 0 and 1"
        )
    if expected_confidence is not None and not isclose(
        expected_confidence, confidence, rel_tol=1e-9, abs_tol=1e-9
    ):
        raise CustomerVisitIdentityConflict(
            "confidence does not match identity evidence similarity"
        )
    if not isinstance(evidence.occurred_at, datetime):
        raise CustomerVisitIdentityError(
            "identity evidence occurred_at must be a datetime"
        )
    if evidence.association_status == "matched" and (
        evidence.matched_visit_id != visit.id
        or evidence.matched_entry_event_id != visit.entry_event_id
    ):
        raise CustomerVisitIdentityConflict(
            "matched identity evidence does not match the target visit and entry"
        )
    if (
        evidence.matched_entry_event_id is not None
        and evidence.matched_entry_event_id != visit.entry_event_id
    ):
        raise CustomerVisitIdentityConflict(
            "identity evidence is already associated with another entry"
        )
    if (
        evidence.matched_visit_id is not None
        and evidence.matched_visit_id != visit.id
    ):
        raise CustomerVisitIdentityConflict(
            "identity evidence is already bound to another visit"
        )

    member_query = db.query(Member).filter(Member.id == evidence.member_id)
    if db.get_bind().dialect.name != "sqlite":
        member_query = member_query.with_for_update()
    member = member_query.one_or_none()
    if member is None:
        raise CustomerVisitIdentityError(
            f"member not found: {evidence.member_id}"
        )
    if visit.member_id is not None and visit.member_id != evidence.member_id:
        evidence.association_status = "ambiguous"
        evidence.match_reason = "visit_already_bound_to_another_member"
        evidence.match_confidence = confidence
        evidence.associated_at = clock()
        raise CustomerVisitIdentityConflict(
            "visit is already bound to another member",
            persist_evidence=True,
        )

    visit.member_id = evidence.member_id
    visit.is_member = True
    visit.visitor_type = "member"
    visit.identity_status = "identified_member"
    visit.identity_source = evidence.source
    visit.identity_confidence = confidence
    visit.recognized_confidence = confidence
    visit.identified_at = evidence.occurred_at
    visit.identity_time = evidence.occurred_at
    if visit.member_visit_counted_at is None:
        visit.member_visit_counted_at = evidence.occurred_at
        member.visit_count = (member.visit_count or 0) + 1
        visit_time = visit.entry_time or evidence.occurred_at
        if member.last_visit is None or member.last_visit < visit_time:
            member.last_visit = visit_time

    evidence.association_status = "matched"
    evidence.matched_entry_event_id = visit.entry_event_id
    evidence.matched_visit_id = visit.id
    evidence.matched_track_token = _entry_track_token(db, visit.entry_event_id)
    evidence.match_reason = "unique_visit_identity"
    evidence.match_confidence = confidence
    evidence.associated_at = clock()


def _entry_track_token(db: Session, entry_event_id: Optional[str]) -> Optional[str]:
    if entry_event_id is None:
        return None
    return db.query(CustomerEntryConfirmedEvent.track_token).filter(
        CustomerEntryConfirmedEvent.id == entry_event_id
    ).scalar()
