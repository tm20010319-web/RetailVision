"""Read-only Shadow evaluation queries for the operations UI."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_
from sqlalchemy.orm import aliased

from agent.models import EvidenceClip, GreetingCycle, GreetingEvaluation


SHADOW_MODE = "shadow"
OFFICIAL_MODE = "official"


class StaffGreetingShadowQuery:
    """Read-only Shadow list/detail queries with official comparison facts."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def recent_shadow(
        self,
        *,
        limit: int = 50,
        camera_id: Optional[str] = None,
        path: Optional[str] = None,
        decision: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        db = self.session_factory()
        try:
            official = aliased(GreetingEvaluation)
            query = (
                db.query(GreetingEvaluation, GreetingCycle, EvidenceClip, official)
                .join(GreetingCycle, GreetingCycle.id == GreetingEvaluation.greeting_cycle_id)
                .outerjoin(EvidenceClip, EvidenceClip.id == GreetingEvaluation.evidence_clip_id)
                .outerjoin(
                    official,
                    and_(
                        official.greeting_cycle_id == GreetingCycle.id,
                        official.mode == OFFICIAL_MODE,
                    ),
                )
                .filter(GreetingEvaluation.mode == SHADOW_MODE)
            )
            if camera_id:
                query = query.filter(GreetingCycle.camera_id == camera_id)
            if path:
                query = query.filter(GreetingEvaluation.candidate_path == path)
            if decision:
                query = query.filter(GreetingEvaluation.decision == decision)
            if since:
                query = query.filter(GreetingEvaluation.settled_at >= since)
            if until:
                query = query.filter(GreetingEvaluation.settled_at <= until)
            rows = (
                query.order_by(
                    GreetingEvaluation.settled_at.desc(),
                    GreetingEvaluation.id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [_serialize(*row) for row in rows]
        finally:
            db.close()

    def get_shadow(self, evaluation_id: str) -> Optional[dict[str, Any]]:
        db = self.session_factory()
        try:
            official = aliased(GreetingEvaluation)
            row = (
                db.query(GreetingEvaluation, GreetingCycle, EvidenceClip, official)
                .join(GreetingCycle, GreetingCycle.id == GreetingEvaluation.greeting_cycle_id)
                .outerjoin(EvidenceClip, EvidenceClip.id == GreetingEvaluation.evidence_clip_id)
                .outerjoin(
                    official,
                    and_(
                        official.greeting_cycle_id == GreetingCycle.id,
                        official.mode == OFFICIAL_MODE,
                    ),
                )
                .filter(
                    GreetingEvaluation.id == evaluation_id,
                    GreetingEvaluation.mode == SHADOW_MODE,
                )
                .one_or_none()
            )
            return None if row is None else _serialize(*row)
        finally:
            db.close()


def _serialize(evaluation, cycle, clip, official) -> dict[str, Any]:
    def facts(row):
        if row is None:
            return None
        return {
            "id": row.id,
            "evaluator_version": row.evaluator_version,
            "mode": row.mode,
            "candidate_path": row.candidate_path,
            "decision": row.decision,
            "interaction_score": row.interaction_score,
            "track_quality_score": row.track_quality_score,
            "raw_metrics": row.raw_metrics or {},
            "score_breakdown": row.score_breakdown or {},
            "calibration_mode": row.calibration_mode,
            "calibration_consistency": row.calibration_consistency,
            "waiting_started_at": _iso(row.waiting_started_at),
            "response_seconds": row.response_seconds,
            "settled_at": _iso(row.settled_at),
            "reason": row.reason,
            "candidate_threshold": row.candidate_threshold,
            "confirm_threshold": row.confirm_threshold,
            "staff_track_id": row.staff_track_id,
            "staff_track_token": _short(row.staff_track_token),
        }

    return {
        "id": evaluation.id,
        "mode": evaluation.mode,
        "label": "SHADOW / 不计绩效",
        "cycle": {
            "id": cycle.id,
            "entry_event_id": cycle.entry_event_id,
            "entry_key": cycle.entry_key,
            "camera_id": cycle.camera_id,
            "customer_track_id": cycle.customer_track_id,
            "customer_track_token": _short(cycle.customer_track_token),
            "outcome": cycle.outcome,
            "cancel_reason": cycle.cancel_reason,
            "cycle_created_at": _iso(cycle.cycle_created_at),
            "waiting_started_at": _iso(cycle.waiting_started_at),
            "settled_at": _iso(cycle.settled_at),
        },
        "evaluation": facts(evaluation),
        "v1": facts(official),
        "evidence": None
        if clip is None
        else {
            "id": clip.id,
            "status": clip.status,
            "available": clip.status == "ready" and bool(clip.output_path),
        },
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _short(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value if len(value) <= 24 else f"…{value[-23:]}"
