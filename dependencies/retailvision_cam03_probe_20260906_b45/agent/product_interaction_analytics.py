"""Offline product interaction facts and read-only analytics queries."""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from agent.models import ProductInteractionFact, new_uuid


logger = logging.getLogger(__name__)


PRODUCT_EVENT_TYPES = frozenset({
    "merchandise_pick_up",
    "merchandise_holding",
    "merchandise_put_down",
})
MAX_QUERY_TIME_RANGE = timedelta(days=31)
PROJECTION_VERSION = "product_interaction_projection_v1"


class ProductAnalyticsError(ValueError):
    pass


@dataclass(frozen=True)
class ProductAnalyticsProjectionAudit:
    analysis_job_id: Optional[str]
    analysis_run_id: Optional[str]
    source_video_id: Optional[str]
    artifact_manifest_hash: Optional[str]
    projection_version: str
    inserted_count: int
    replayed_count: int
    conflict_count: int
    failure_reason: Optional[str]


@dataclass(frozen=True)
class ProductRankItem:
    product_key: str
    product_name: Optional[str]
    pickup_count: int
    put_down_count: int
    completed_interaction_count: int
    total_holding_seconds: float
    average_holding_seconds: float
    max_holding_seconds: float
    unique_track_count: int
    last_interaction_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_key": self.product_key,
            "product_name": self.product_name,
            "pickup_count": self.pickup_count,
            "put_down_count": self.put_down_count,
            "completed_interaction_count": self.completed_interaction_count,
            "total_holding_seconds": self.total_holding_seconds,
            "average_holding_seconds": self.average_holding_seconds,
            "avg_holding_seconds": self.average_holding_seconds,
            "max_holding_seconds": self.max_holding_seconds,
            "unique_track_count": self.unique_track_count,
            "unique_person_track_count": self.unique_track_count,
            "last_interaction_at": self.last_interaction_at.isoformat(),
        }


class ProductInteractionFactWriter:
    """Persist Stage9 normalized facts without consulting identity or BehaviorEvent."""

    def __init__(self, session_factory: Any, *, track_hash_secret: bytes = b"retailvision-product-analytics-v1") -> None:
        if not isinstance(track_hash_secret, bytes) or not track_hash_secret:
            raise ProductAnalyticsError("track_hash_secret_required")
        self.session_factory = session_factory
        self.track_hash_secret = track_hash_secret
        self.last_audit: Optional[ProductAnalyticsProjectionAudit] = None

    def write(
        self,
        facts: Iterable[Any],
        *,
        store_id: str,
        artifact_manifest_hash: Optional[str] = None,
    ) -> tuple[ProductInteractionFact, ...]:
        values = tuple(facts)
        rows: list[ProductInteractionFact] = []
        pending: dict[tuple[str, str, str], ProductInteractionFact] = {}
        replayed_count = 0
        db = None
        try:
            _nonempty(store_id, "store_id")
            if artifact_manifest_hash is not None and not artifact_manifest_hash.startswith("v1:sha256:"):
                raise ProductAnalyticsError("artifact_manifest_hash_invalid")
            db = self.session_factory()
            for fact in values:
                row = self._row(
                    fact,
                    store_id=store_id,
                    artifact_manifest_hash=artifact_manifest_hash,
                )
                key = (row.analysis_run_id, row.interaction_session_id, row.event_type)
                duplicate = pending.get(key)
                if duplicate is not None:
                    if not _same_fact(duplicate, row):
                        raise ProductAnalyticsError("product_interaction_fact_conflict")
                    replayed_count += 1
                    rows.append(duplicate)
                    continue
                existing = (
                    db.query(ProductInteractionFact)
                    .filter_by(
                        analysis_run_id=row.analysis_run_id,
                        interaction_session_id=row.interaction_session_id,
                        event_type=row.event_type,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    if not _same_fact(existing, row):
                        raise ProductAnalyticsError("product_interaction_fact_conflict")
                    replayed_count += 1
                    rows.append(existing)
                    continue
                db.add(row)
                pending[key] = row
                rows.append(row)
            db.commit()
            self._record_audit(
                values,
                artifact_manifest_hash=artifact_manifest_hash,
                inserted_count=len(pending),
                replayed_count=replayed_count,
                conflict_count=0,
                failure_reason=None,
            )
            return tuple(rows)
        except IntegrityError as exc:
            if db is not None:
                db.rollback()
            self._record_audit(
                values,
                artifact_manifest_hash=artifact_manifest_hash,
                inserted_count=0,
                replayed_count=replayed_count,
                conflict_count=1,
                failure_reason="product_interaction_fact_conflict",
            )
            raise ProductAnalyticsError("product_interaction_fact_conflict") from exc
        except ProductAnalyticsError as exc:
            if db is not None:
                db.rollback()
            reason = str(exc)
            self._record_audit(
                values,
                artifact_manifest_hash=artifact_manifest_hash,
                inserted_count=0,
                replayed_count=replayed_count,
                conflict_count=int(reason == "product_interaction_fact_conflict"),
                failure_reason=reason,
            )
            raise
        except Exception:
            if db is not None:
                db.rollback()
            self._record_audit(
                values,
                artifact_manifest_hash=artifact_manifest_hash,
                inserted_count=0,
                replayed_count=replayed_count,
                conflict_count=0,
                failure_reason="product_analytics_projection_failed",
            )
            raise
        finally:
            if db is not None:
                db.close()

    def _record_audit(
        self,
        facts: tuple[Any, ...],
        *,
        artifact_manifest_hash: Optional[str],
        inserted_count: int,
        replayed_count: int,
        conflict_count: int,
        failure_reason: Optional[str],
    ) -> None:
        audit = ProductAnalyticsProjectionAudit(
            analysis_job_id=_uniform_audit_attr(facts, "analysis_job_id"),
            analysis_run_id=_uniform_audit_attr(facts, "analysis_run_id"),
            source_video_id=_uniform_audit_attr(facts, "source_video_id"),
            artifact_manifest_hash=artifact_manifest_hash,
            projection_version=PROJECTION_VERSION,
            inserted_count=inserted_count,
            replayed_count=replayed_count,
            conflict_count=conflict_count,
            failure_reason=failure_reason,
        )
        self.last_audit = audit
        logger.info(
            "product_analytics_projection analysis_job_id=%s analysis_run_id=%s "
            "source_video_id=%s artifact_manifest_hash=%s projection_version=%s "
            "inserted_count=%d replayed_count=%d conflict_count=%d failure_reason=%s",
            audit.analysis_job_id,
            audit.analysis_run_id,
            audit.source_video_id,
            audit.artifact_manifest_hash,
            audit.projection_version,
            audit.inserted_count,
            audit.replayed_count,
            audit.conflict_count,
            audit.failure_reason,
        )

    def _row(
        self,
        fact: Any,
        *,
        store_id: str,
        artifact_manifest_hash: Optional[str],
    ) -> ProductInteractionFact:
        event_type = getattr(fact, "event_type", None)
        if event_type not in PRODUCT_EVENT_TYPES:
            raise ProductAnalyticsError("unsupported_product_event_type")
        analysis_job_id = _required_attr(fact, "analysis_job_id")
        analysis_run_id = _required_attr(fact, "analysis_run_id")
        camera_id = _required_attr(fact, "camera_id")
        product_key = _required_attr(fact, "product_key")
        session_id = _required_attr(fact, "interaction_session_id")
        started_at = getattr(fact, "started_at", None)
        ended_at = getattr(fact, "ended_at", None)
        if not isinstance(started_at, datetime) or not isinstance(ended_at, datetime):
            raise ProductAnalyticsError("authoritative_fact_time_required")
        if started_at.tzinfo is not None or ended_at.tzinfo is not None or ended_at < started_at:
            raise ProductAnalyticsError("fact_time_invalid")
        duration = getattr(fact, "holding_duration_seconds", None)
        if event_type == "merchandise_holding":
            if duration is None:
                duration = (ended_at - started_at).total_seconds()
            if duration < 0:
                raise ProductAnalyticsError("holding_duration_invalid")
        else:
            duration = None
        track_token = _required_attr(fact, "customer_track_token")
        track_key_hash = hmac.new(
            self.track_hash_secret, track_token.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return ProductInteractionFact(
            id=new_uuid(),
            analysis_job_id=analysis_job_id,
            analysis_run_id=analysis_run_id,
            source_video_id=_required_attr(fact, "source_video_id"),
            store_id=store_id,
            camera_id=camera_id,
            product_key=product_key,
            product_name=getattr(fact, "product_name", None),
            interaction_session_id=session_id,
            event_type=event_type,
            started_at=started_at,
            ended_at=ended_at,
            holding_duration_seconds=duration,
            track_key_hash=track_key_hash,
            normalized_fact_ref=_required_attr(fact, "normalized_fact_ref"),
            artifact_manifest_hash=artifact_manifest_hash,
            projection_version=PROJECTION_VERSION,
        )


class ProductAnalyticsQueryService:
    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    def rank_products(
        self,
        *,
        store_id: str,
        start_at: datetime,
        end_at: datetime,
        camera_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        _scope(store_id, camera_id, start_at, end_at)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ProductAnalyticsError("limit_invalid")
        rows = self._facts(store_id, camera_id, start_at, end_at)
        grouped: dict[str, list[ProductInteractionFact]] = {}
        for row in rows:
            grouped.setdefault(row.product_key, []).append(row)
        items = [self._summary(product_key, values) for product_key, values in grouped.items()]
        items.sort(key=lambda item: (-item.completed_interaction_count, -item.pickup_count, -item.total_holding_seconds, item.product_key))
        return [item.as_dict() for item in items[:limit]]

    def get_product_behavior_summary(
        self,
        *,
        store_id: str,
        product_key: str,
        start_at: datetime,
        end_at: datetime,
        camera_id: Optional[str] = None,
    ) -> dict[str, Any]:
        _nonempty(product_key, "product_key")
        _scope(store_id, camera_id, start_at, end_at)
        rows = [row for row in self._facts(store_id, camera_id, start_at, end_at) if row.product_key == product_key]
        if not rows:
            return {
                "product_key": product_key,
                "product_name": None,
                "interaction_count": 0,
                "pickup_count": 0,
                "put_down_count": 0,
                "holding_count": 0,
                "completed_interaction_count": 0,
                "total_holding_seconds": 0.0,
                "avg_holding_seconds": 0.0,
                "average_hold_time": 0.0,
                "max_hold_time": 0.0,
                "max_holding_seconds": 0.0,
                "unique_person_track_count": 0,
                "last_interaction_at": None,
                "last_interaction_time": None,
            }
        item = self._summary(product_key, rows)
        return {
            "product_key": item.product_key,
            "product_name": item.product_name,
            "interaction_count": item.completed_interaction_count,
            "pickup_count": item.pickup_count,
            "put_down_count": item.put_down_count,
            "holding_count": sum(row.event_type == "merchandise_holding" for row in rows),
            "completed_interaction_count": item.completed_interaction_count,
            "total_holding_seconds": item.total_holding_seconds,
            "avg_holding_seconds": item.average_holding_seconds,
            "average_hold_time": item.average_holding_seconds,
            "max_hold_time": item.max_holding_seconds,
            "max_holding_seconds": item.max_holding_seconds,
            "unique_person_track_count": item.unique_track_count,
            "last_interaction_at": item.last_interaction_at.isoformat(),
            "last_interaction_time": item.last_interaction_at.isoformat(),
        }

    def compare_product_behavior(
        self,
        *,
        store_id: str,
        product_key: str,
        first_start_at: datetime,
        first_end_at: datetime,
        second_start_at: datetime,
        second_end_at: datetime,
        camera_id: Optional[str] = None,
    ) -> dict[str, Any]:
        first = self.get_product_behavior_summary(
            store_id=store_id, product_key=product_key, camera_id=camera_id,
            start_at=first_start_at, end_at=first_end_at,
        )
        second = self.get_product_behavior_summary(
            store_id=store_id, product_key=product_key, camera_id=camera_id,
            start_at=second_start_at, end_at=second_end_at,
        )
        return {
            "product_key": product_key,
            "first": first,
            "second": second,
            "pickup_change": second["pickup_count"] - first["pickup_count"],
            "holding_change": second["holding_count"] - first["holding_count"],
            "interaction_change": second["interaction_count"] - first["interaction_count"],
        }

    def compare_products(
        self,
        *,
        store_id: str,
        first_product_key: str,
        second_product_key: str,
        start_at: datetime,
        end_at: datetime,
        camera_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compare two products in one scope without changing fact aggregation."""
        first = self.get_product_behavior_summary(
            store_id=store_id, product_key=first_product_key, camera_id=camera_id,
            start_at=start_at, end_at=end_at,
        )
        second = self.get_product_behavior_summary(
            store_id=store_id, product_key=second_product_key, camera_id=camera_id,
            start_at=start_at, end_at=end_at,
        )
        first_score = _recommendation_score(first)
        second_score = _recommendation_score(second)
        if first_score == second_score:
            winner = "tie"
            reason = "equal_interaction_and_holding_facts"
        elif first_score > second_score:
            winner = first_product_key
            reason = "higher_structured_engagement_score"
        else:
            winner = second_product_key
            reason = "higher_structured_engagement_score"
        return {
            "comparison": {"first": first, "second": second},
            "scores": {first_product_key: round(first_score, 6), second_product_key: round(second_score, 6)},
            "reason": reason,
            "recommendation": winner,
        }

    def recommend_hot_products(
        self,
        *,
        store_id: str,
        start_at: datetime,
        end_at: datetime,
        camera_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return ranked products with deterministic recommendation facts.

        This is intentionally a business-rule projection: the MCP layer returns
        facts and reasons, while an Agent remains responsible for prose.
        """
        ranked = self.rank_products(
            store_id=store_id,
            camera_id=camera_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
        recommendations: list[dict[str, Any]] = []
        for item in ranked:
            score = (
                item["completed_interaction_count"] * 2.0
                + item["pickup_count"]
                + item["total_holding_seconds"] / 60.0
            )
            reasons = []
            if item["completed_interaction_count"]:
                reasons.append("completed_interactions")
            if item["pickup_count"]:
                reasons.append("pickup_interest")
            if item["total_holding_seconds"]:
                reasons.append("holding_engagement")
            recommendations.append({
                **item,
                "recommendation_score": round(score, 6),
                "recommended": bool(reasons),
                "recommendation_reasons": reasons,
            })
        recommendations.sort(
            key=lambda item: (-item["recommendation_score"], item["product_key"])
        )
        return recommendations

    def analyze_product_trend(
        self,
        *,
        store_id: str,
        product_key: str,
        start_at: datetime,
        end_at: datetime,
        camera_id: Optional[str] = None,
    ) -> dict[str, Any]:
        _scope(store_id, camera_id, start_at, end_at)
        duration = end_at - start_at
        previous_start = start_at - duration
        previous = self.get_product_behavior_summary(
            store_id=store_id, camera_id=camera_id, product_key=product_key,
            start_at=previous_start, end_at=start_at,
        )
        current = self.get_product_behavior_summary(
            store_id=store_id, camera_id=camera_id, product_key=product_key,
            start_at=start_at, end_at=end_at,
        )
        interaction_change = current["interaction_count"] - previous["interaction_count"]
        pickup_change = current["pickup_count"] - previous["pickup_count"]
        holding_change = current["holding_count"] - previous["holding_count"]
        trend = "up" if interaction_change > 0 else "down" if interaction_change < 0 else "flat"
        return {
            "product_key": product_key,
            "previous": previous,
            "current": current,
            "interaction_trend": trend,
            "interaction_change": interaction_change,
            "pickup_change": pickup_change,
            "holding_change": holding_change,
            "recommendation": "increase_attention" if trend == "up" else "monitor" if trend == "flat" else "review_display",
        }

    def detect_product_anomaly(
        self,
        *,
        store_id: str,
        start_at: datetime,
        end_at: datetime,
        camera_id: Optional[str] = None,
        limit: int = 10,
        low_hold_seconds: float = 5.0,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ProductAnalyticsError("limit_invalid")
        ranked = self.rank_products(
            store_id=store_id, camera_id=camera_id,
            start_at=start_at, end_at=end_at, limit=100,
        )
        anomalies = []
        for item in ranked:
            high_attention = item["completed_interaction_count"] >= 2 or item["pickup_count"] >= 3
            low_engagement = item["average_holding_seconds"] < low_hold_seconds
            if high_attention and low_engagement:
                anomalies.append({
                    "product_key": item["product_key"],
                    "product_name": item["product_name"],
                    "anomaly_type": "high_attention_low_hold",
                    "severity": "medium",
                    "facts": item,
                    "recommendation": "review_product_positioning",
                })
        return anomalies[:limit]

    def suggest_product_display(
        self,
        *,
        store_id: str,
        start_at: datetime,
        end_at: datetime,
        camera_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        recommendations = self.recommend_hot_products(
            store_id=store_id, camera_id=camera_id,
            start_at=start_at, end_at=end_at, limit=limit,
        )
        return [
            {
                "product_key": item["product_key"],
                "product_name": item["product_name"],
                "display_priority": index + 1,
                "recommendation_score": item["recommendation_score"],
                "recommendation": "重点展示" if item["recommended"] else "observe",
                "reasons": item["recommendation_reasons"],
                "facts": item,
            }
            for index, item in enumerate(recommendations)
        ]

    def _facts(self, store_id: str, camera_id: Optional[str], start_at: datetime, end_at: datetime) -> list[ProductInteractionFact]:
        db = self.session_factory()
        try:
            query = db.query(ProductInteractionFact).filter(
                ProductInteractionFact.store_id == store_id,
                ProductInteractionFact.started_at >= start_at,
                ProductInteractionFact.started_at < end_at,
            )
            if camera_id is not None:
                query = query.filter(ProductInteractionFact.camera_id == camera_id)
            return query.order_by(ProductInteractionFact.started_at, ProductInteractionFact.id).all()
        finally:
            db.close()

    @staticmethod
    def _summary(product_key: str, rows: list[ProductInteractionFact]) -> ProductRankItem:
        holdings = [row.holding_duration_seconds or 0.0 for row in rows if row.event_type == "merchandise_holding"]
        pickups = sum(row.event_type == "merchandise_pick_up" for row in rows)
        put_downs = sum(row.event_type == "merchandise_put_down" for row in rows)
        sessions = {row.interaction_session_id for row in rows if row.event_type == "merchandise_holding"}
        names = [row.product_name for row in rows if row.product_name]
        return ProductRankItem(
            product_key=product_key,
            product_name=names[0] if names else None,
            pickup_count=pickups,
            put_down_count=put_downs,
            completed_interaction_count=len(sessions),
            total_holding_seconds=sum(holdings),
            average_holding_seconds=sum(holdings) / len(holdings) if holdings else 0.0,
            max_holding_seconds=max(holdings, default=0.0),
            unique_track_count=len({row.track_key_hash for row in rows}),
            last_interaction_at=max(row.ended_at for row in rows),
        )


def _same_fact(existing: ProductInteractionFact, incoming: ProductInteractionFact) -> bool:
    fields = (
        "analysis_job_id", "source_video_id", "store_id", "camera_id",
        "product_key", "product_name", "started_at", "ended_at",
        "holding_duration_seconds", "track_key_hash", "normalized_fact_ref",
        "artifact_manifest_hash", "projection_version",
    )
    return all(getattr(existing, field) == getattr(incoming, field) for field in fields)


def _uniform_audit_attr(facts: tuple[Any, ...], name: str) -> Optional[str]:
    values = {
        value
        for fact in facts
        if isinstance((value := getattr(fact, name, None)), str) and value
    }
    if not values:
        return None
    return next(iter(values)) if len(values) == 1 else "mixed"


def _recommendation_score(summary: dict[str, Any]) -> float:
    return (
        summary["interaction_count"] * 2.0
        + summary["pickup_count"]
        + summary["average_hold_time"] / 60.0
    )


def _required_attr(value: Any, name: str) -> str:
    result = getattr(value, name, None)
    if not isinstance(result, str) or not result:
        raise ProductAnalyticsError(f"{name}_required")
    return result


def _nonempty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ProductAnalyticsError(f"{name}_required")


def _scope(store_id: str, camera_id: Optional[str], start_at: datetime, end_at: datetime) -> None:
    _nonempty(store_id, "store_id")
    if camera_id is not None:
        _nonempty(camera_id, "camera_id")
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime) or start_at.tzinfo is not None or end_at.tzinfo is not None:
        raise ProductAnalyticsError("query_time_invalid")
    if end_at <= start_at:
        raise ProductAnalyticsError("query_time_range_invalid")
    if end_at - start_at > MAX_QUERY_TIME_RANGE:
        raise ProductAnalyticsError("query_time_range_exceeds_maximum")
