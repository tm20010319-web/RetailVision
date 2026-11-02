"""Read-only Offline member-product behavior query boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.member.member_priority import normalize_tier
from agent.store_context import StoreContext


MEMBER_BEHAVIOR_EVENT_TYPES = (
    "merchandise_pick_up",
    "merchandise_holding",
    "merchandise_put_down",
)
MEMBER_BEHAVIOR_EVENT_TYPE_SET = frozenset(MEMBER_BEHAVIOR_EVENT_TYPES)
BEHAVIOR_LABELS = {
    "merchandise_pick_up": "拿起商品",
    "merchandise_holding": "持有商品",
    "merchandise_put_down": "放下商品",
}
MAX_QUERY_TIME_RANGE = timedelta(days=31)


class MemberBehaviorError(ValueError):
    """Stable public error code for the Member Behavior boundary."""


@dataclass(frozen=True)
class MemberBehaviorMember:
    member_id: str
    display_name: str
    tier: Optional[str]


@dataclass(frozen=True)
class MemberBehaviorEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    duration_seconds: Optional[float]
    details: dict[str, Any]
    member: Optional[MemberBehaviorMember] = None


class MemberBehaviorReadPort(Protocol):
    """Narrow read-only persistence boundary required by MB-01A."""

    def find_active_member_by_id(
        self, member_id: str
    ) -> Optional[MemberBehaviorMember]: ...

    def find_active_members_by_name(
        self, name: str, *, limit: int
    ) -> list[MemberBehaviorMember]: ...

    def list_eligible_events(
        self,
        *,
        member_id: str,
        scope: "ResolvedMemberBehaviorScope",
        limit: Optional[int],
    ) -> list[MemberBehaviorEvent]: ...

    def list_recent_eligible_events(
        self,
        *,
        scope: "ResolvedMemberBehaviorScope",
        limit: int,
    ) -> list[MemberBehaviorEvent]: ...


@dataclass(frozen=True)
class ResolvedMemberBehaviorScope:
    store_id: str
    camera_id: Optional[str]
    local_start_at: datetime
    local_end_at: datetime
    utc_start_at: datetime
    utc_end_at: datetime
    timezone: ZoneInfo
    product_name: Optional[str]

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "camera_scope": "single" if self.camera_id is not None else "store_all",
            "start_at": _aware_local(self.local_start_at, self.timezone).isoformat(),
            "end_at": _aware_local(self.local_end_at, self.timezone).isoformat(),
            "product_name": self.product_name,
        }


class MemberBehaviorScopePolicy:
    """Resolve local deployment scope and UTC-naive Offline query bounds."""

    def __init__(self, store_context: StoreContext, timezone_name: str) -> None:
        if not isinstance(timezone_name, str) or not timezone_name.strip():
            raise MemberBehaviorError("member_behavior_timezone_required")
        try:
            self.timezone = ZoneInfo(timezone_name.strip())
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise MemberBehaviorError("member_behavior_timezone_invalid") from exc
        self.store_context = store_context
        # Fail at readiness, including DST gaps or folds in deployment defaults.
        default_start = _aware_local(store_context.default_start_at, self.timezone)
        default_end = _aware_local(store_context.default_end_at, self.timezone)
        self._utc_bounds(default_start, default_end)

    def _utc_bounds(self, aware_start: datetime, aware_end: datetime) -> tuple[datetime, datetime]:
        utc_start = aware_start.astimezone(timezone.utc).replace(tzinfo=None)
        utc_end = aware_end.astimezone(timezone.utc).replace(tzinfo=None)
        if utc_end - utc_start > MAX_QUERY_TIME_RANGE:
            raise MemberBehaviorError("invalid_time_range")
        return utc_start, utc_end

    def resolve(
        self,
        *,
        store_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        product_name: Optional[str] = None,
        store_provided: bool = False,
        camera_provided: bool = False,
        start_provided: bool = False,
        end_provided: bool = False,
        product_provided: bool = False,
    ) -> ResolvedMemberBehaviorScope:
        context = self.store_context
        if store_provided:
            if store_id is None:
                store_id = context.store_id
            elif not isinstance(store_id, str) or not store_id:
                raise MemberBehaviorError("invalid_arguments")
            elif store_id != context.store_id:
                raise MemberBehaviorError("scope_out_of_bounds")
        else:
            store_id = context.store_id

        if camera_provided:
            if camera_id is None:
                raise MemberBehaviorError("invalid_arguments")
            elif not isinstance(camera_id, str) or not camera_id:
                raise MemberBehaviorError("invalid_arguments")
            elif context.default_camera_id is None or camera_id != context.default_camera_id:
                raise MemberBehaviorError("scope_out_of_bounds")
        else:
            camera_id = context.default_camera_id

        if start_provided != end_provided:
            raise MemberBehaviorError("invalid_time_range")
        if start_provided:
            if not _is_naive_datetime(start_at) or not _is_naive_datetime(end_at):
                raise MemberBehaviorError("invalid_time")
            local_start = start_at
            local_end = end_at
        else:
            local_start = context.default_start_at
            local_end = context.default_end_at
        assert isinstance(local_start, datetime) and isinstance(local_end, datetime)
        if local_start >= local_end or local_end - local_start > MAX_QUERY_TIME_RANGE:
            raise MemberBehaviorError("invalid_time_range")
        if (
            local_start < context.default_start_at
            or local_end > context.default_end_at
        ):
            raise MemberBehaviorError("scope_out_of_bounds")

        if product_provided:
            if product_name is None:
                product_name = None
            elif not isinstance(product_name, str) or not product_name.strip():
                raise MemberBehaviorError("invalid_arguments")
            else:
                product_name = product_name.strip()
        else:
            product_name = None

        aware_start = _aware_local(local_start, self.timezone)
        aware_end = _aware_local(local_end, self.timezone)
        utc_start, utc_end = self._utc_bounds(aware_start, aware_end)
        return ResolvedMemberBehaviorScope(
            store_id=store_id,
            camera_id=camera_id,
            local_start_at=local_start,
            local_end_at=local_end,
            utc_start_at=utc_start,
            utc_end_at=utc_end,
            timezone=self.timezone,
            product_name=product_name,
        )


class MemberBehaviorQueryService:
    """Resolve a member, select eligible Offline rows, and project safe facts."""

    def __init__(self, read_port: MemberBehaviorReadPort, scope_policy: MemberBehaviorScopePolicy) -> None:
        self.read_port = read_port
        self.scope_policy = scope_policy

    def get_member_behavior_summary(
        self,
        *,
        member_ref: str,
        store_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        product_name: Optional[str] = None,
        store_provided: bool = False,
        camera_provided: bool = False,
        start_provided: bool = False,
        end_provided: bool = False,
        product_provided: bool = False,
    ) -> dict[str, Any]:
        store_provided = store_provided or store_id is not None
        camera_provided = camera_provided or camera_id is not None
        start_provided = start_provided or start_at is not None
        end_provided = end_provided or end_at is not None
        product_provided = product_provided or product_name is not None
        scope = self.scope_policy.resolve(
            store_id=store_id, camera_id=camera_id,
            start_at=start_at, end_at=end_at, product_name=product_name,
            store_provided=store_provided, camera_provided=camera_provided,
            start_provided=start_provided, end_provided=end_provided,
            product_provided=product_provided,
        )
        member = self._resolve_member(member_ref)
        rows = self._eligible_rows(member.member_id, scope)
        counts = _behavior_counts(rows)
        total_holding, holding_status = _holding_summary(rows)
        last = max((row.occurred_at for row in rows), default=None)
        return {
            "status": "ok",
            "member": _member_projection(member),
            "scope": scope.as_public_dict(),
            "behavior_summary": {
                "total_behavior_count": sum(counts.values()),
                "behavior_counts": counts,
                "holding_count": counts["merchandise_holding"],
                "total_holding_seconds": total_holding,
                "last_behavior_at": _event_time(last, scope.timezone),
            },
            "interacted_products": _interacted_products(rows, scope.timezone),
            "data_quality": {"holding_duration": holding_status},
        }

    def list_member_behavior_timeline(
        self,
        *,
        member_ref: str,
        limit: int = 20,
        store_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        product_name: Optional[str] = None,
        store_provided: bool = False,
        camera_provided: bool = False,
        start_provided: bool = False,
        end_provided: bool = False,
        product_provided: bool = False,
    ) -> dict[str, Any]:
        store_provided = store_provided or store_id is not None
        camera_provided = camera_provided or camera_id is not None
        start_provided = start_provided or start_at is not None
        end_provided = end_provided or end_at is not None
        product_provided = product_provided or product_name is not None
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise MemberBehaviorError("invalid_arguments")
        scope = self.scope_policy.resolve(
            store_id=store_id, camera_id=camera_id,
            start_at=start_at, end_at=end_at, product_name=product_name,
            store_provided=store_provided, camera_provided=camera_provided,
            start_provided=start_provided, end_provided=end_provided,
            product_provided=product_provided,
        )
        member = self._resolve_member(member_ref)
        rows = self._eligible_rows(member.member_id, scope, limit=limit + 1)
        visible = rows[:limit]
        refs = _product_refs(visible)
        timeline = [
            {
                "occurred_at": _event_time(row.occurred_at, scope.timezone),
                "behavior_type": row.event_type,
                "behavior_label": BEHAVIOR_LABELS[row.event_type],
                "product_ref": refs[_row_product_group(row)],
                "product_name": _row_product_name(row),
                "product_name_status": (
                    "available" if _row_product_name(row) is not None else "unavailable"
                ),
                "duration_seconds": _duration(row)[0],
                "duration_status": _duration(row)[1],
            }
            for row in visible
        ]
        return {
            "status": "ok",
            "member": _member_projection(member),
            "scope": scope.as_public_dict(),
            "timeline": timeline,
            "has_more": len(rows) > limit,
        }

    def list_recent_member_behaviors(
        self,
        *,
        limit: int = 100,
        store_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        product_name: Optional[str] = None,
        store_provided: bool = False,
        camera_provided: bool = False,
        start_provided: bool = False,
        end_provided: bool = False,
        product_provided: bool = False,
    ) -> dict[str, Any]:
        """Return recent confirmed member interactions without a member filter."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise MemberBehaviorError("invalid_arguments")
        store_provided = store_provided or store_id is not None
        camera_provided = camera_provided or camera_id is not None
        start_provided = start_provided or start_at is not None
        end_provided = end_provided or end_at is not None
        product_provided = product_provided or product_name is not None
        scope = self.scope_policy.resolve(
            store_id=store_id, camera_id=camera_id,
            start_at=start_at, end_at=end_at, product_name=product_name,
            store_provided=store_provided, camera_provided=camera_provided,
            start_provided=start_provided, end_provided=end_provided,
            product_provided=product_provided,
        )
        rows = self.read_port.list_recent_eligible_events(
            scope=scope,
            limit=limit + 1,
        )
        if scope.product_name is not None:
            rows = [row for row in rows if _row_product_name(row) == scope.product_name]
        # The read port returns the newest bounded page in chronological order.
        # When the extra row is present, discard the oldest probe row rather
        # than the newest real event.
        visible = rows[-limit:]
        member_refs = _member_refs(visible)
        product_refs = _product_refs(visible)
        counts = _behavior_counts(visible)
        holding_total, holding_status = _holding_summary(visible)
        last = max((row.occurred_at for row in visible), default=None)
        return {
            "status": "ok",
            "scope": scope.as_public_dict(),
            "behavior_summary": {
                "total_behavior_count": len(visible),
                "behavior_counts": counts,
                "member_count": len(member_refs),
                "product_count": len(product_refs),
                "total_holding_seconds": holding_total,
                "last_behavior_at": _event_time(last, scope.timezone),
            },
            "member_summaries": _recent_member_summaries(
                visible, scope.timezone, member_refs
            ),
            "product_summaries": _recent_product_summaries(
                visible, scope.timezone, product_refs
            ),
            "timeline": _recent_timeline(
                visible, scope.timezone, member_refs, product_refs
            ),
            "has_more": len(rows) > limit,
            "data_quality": {
                "holding_duration": holding_status,
                "aggregation_scope": "returned_timeline",
            },
        }

    def _resolve_member(self, member_ref: str) -> MemberBehaviorMember:
        if not isinstance(member_ref, str) or not member_ref:
            raise MemberBehaviorError("invalid_arguments")
        member = self.read_port.find_active_member_by_id(member_ref)
        if member is not None:
            return member
        matches = self.read_port.find_active_members_by_name(member_ref, limit=2)
        if not matches:
            raise MemberBehaviorError("member_not_found")
        if len(matches) > 1:
            raise MemberBehaviorError("member_ambiguous")
        return matches[0]

    def _eligible_rows(
        self,
        member_id: str,
        scope: ResolvedMemberBehaviorScope,
        *,
        limit: Optional[int] = None,
    ) -> list[MemberBehaviorEvent]:
        # Revalidate the immutable scope at the service boundary.
        checked = self.scope_policy.resolve(
            store_id=scope.store_id, camera_id=scope.camera_id,
            start_at=scope.local_start_at, end_at=scope.local_end_at,
            product_name=scope.product_name,
            store_provided=True, camera_provided=scope.camera_id is not None,
            start_provided=True, end_provided=True,
            product_provided=scope.product_name is not None,
        )
        rows = self.read_port.list_eligible_events(
            member_id=member_id,
            scope=checked,
            limit=None if checked.product_name is not None else limit,
        )
        if checked.product_name is not None:
            rows = [row for row in rows if _row_product_name(row) == checked.product_name]
            if limit is not None:
                rows = rows[:limit]
        return rows


def _is_naive_datetime(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is None


def _aware_local(value: datetime, zone: ZoneInfo) -> datetime:
    if not _is_naive_datetime(value):
        raise MemberBehaviorError("invalid_time")
    candidates: list[datetime] = []
    for fold in (0, 1):
        aware = value.replace(tzinfo=zone, fold=fold)
        round_trip = aware.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == value and round_trip.fold == fold:
            candidates.append(aware)
    distinct = {(item.utcoffset(), item.fold) for item in candidates}
    offsets = {item.utcoffset() for item in candidates}
    if not candidates or len(offsets) > 1:
        raise MemberBehaviorError("invalid_time")
    # Normal times can round-trip with either fold but have one real offset.
    return candidates[0]


def _event_time(value: Optional[datetime], zone: ZoneInfo) -> Optional[str]:
    if not _is_naive_datetime(value):
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(zone).isoformat()


def _member_projection(member: MemberBehaviorMember) -> dict[str, Any]:
    tier = normalize_tier(member.tier) if isinstance(member.tier, str) and member.tier.strip() else None
    if tier == "unknown":
        tier = None
    return {
        "member_id": member.member_id,
        "display_name": member.display_name,
        "member_tier": tier,
    }


def _behavior_counts(rows: Iterable[MemberBehaviorEvent]) -> dict[str, int]:
    counts = {event_type: 0 for event_type in MEMBER_BEHAVIOR_EVENT_TYPES}
    for row in rows:
        counts[row.event_type] += 1
    return counts


def _duration(row: MemberBehaviorEvent) -> tuple[Optional[float], str]:
    if row.event_type != "merchandise_holding":
        return None, "not_applicable"
    value = row.duration_seconds
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "unavailable"
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None, "unavailable"
    return number, "available"


def _holding_summary(rows: Iterable[MemberBehaviorEvent]) -> tuple[Optional[float], str]:
    holdings = [row for row in rows if row.event_type == "merchandise_holding"]
    if not holdings:
        return 0.0, "not_applicable"
    values = [_duration(row) for row in holdings]
    available = [value for value, status in values if status == "available"]
    if len(available) == len(values):
        return round(float(sum(available)), 6), "available"
    if available:
        return None, "partial_unavailable"
    return None, "unavailable"


def _details(row: MemberBehaviorEvent) -> dict[str, Any]:
    return row.details if isinstance(row.details, dict) else {}


def _row_product_name(row: MemberBehaviorEvent) -> Optional[str]:
    value = _details(row).get("product_name")
    return value if isinstance(value, str) and value.strip() else None


def _row_product_group(row: MemberBehaviorEvent) -> tuple[str, str]:
    value = _details(row).get("product_key")
    if isinstance(value, str) and value.strip():
        return "key", value
    return "row", row.event_id


def _product_refs(rows: list[MemberBehaviorEvent]) -> dict[tuple[str, str], str]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = _row_product_group(row)
        group = groups.setdefault(key, {"first": index, "last": row.occurred_at})
        if row.occurred_at > group["last"]:
            group["last"] = row.occurred_at
    # Groups are inserted while rows are in the stable occurred_at/id order;
    # Python's stable sort therefore preserves first appearance on ties.
    ordered = sorted(groups, key=lambda key: groups[key]["last"], reverse=True)
    return {key: f"product_{index}" for index, key in enumerate(ordered, start=1)}


def _member_refs(rows: list[MemberBehaviorEvent]) -> dict[str, str]:
    groups: dict[str, datetime] = {}
    for row in rows:
        if row.member is None:
            continue
        previous = groups.get(row.member.member_id)
        if previous is None or row.occurred_at > previous:
            groups[row.member.member_id] = row.occurred_at
    ordered = sorted(groups, key=lambda member_id: groups[member_id], reverse=True)
    return {
        member_id: f"member_{index}"
        for index, member_id in enumerate(ordered, start=1)
    }


def _recent_member_summaries(
    rows: list[MemberBehaviorEvent],
    zone: ZoneInfo,
    member_refs: dict[str, str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[MemberBehaviorEvent]] = {}
    for row in rows:
        if row.member is not None:
            grouped.setdefault(row.member.member_id, []).append(row)
    result = []
    for member_id, member_ref in member_refs.items():
        member_rows = grouped[member_id]
        member = member_rows[0].member
        assert member is not None
        counts = _behavior_counts(member_rows)
        holding_total, holding_status = _holding_summary(member_rows)
        result.append({
            "member_ref": member_ref,
            "member": _member_projection(member),
            "behavior_count": len(member_rows),
            "behavior_counts": counts,
            "total_holding_seconds": holding_total,
            "holding_duration_status": holding_status,
            "last_behavior_at": _event_time(
                max(row.occurred_at for row in member_rows), zone
            ),
        })
    return result


def _recent_product_summaries(
    rows: list[MemberBehaviorEvent],
    zone: ZoneInfo,
    product_refs: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[MemberBehaviorEvent]] = {}
    for row in rows:
        grouped.setdefault(_row_product_group(row), []).append(row)
    result = []
    for group_key, product_ref in product_refs.items():
        product_rows = grouped[group_key]
        names = {_row_product_name(row) for row in product_rows}
        names.discard(None)
        name = next(iter(names)) if len(names) == 1 else None
        counts = _behavior_counts(product_rows)
        holding_total, holding_status = _holding_summary(product_rows)
        result.append({
            "product_ref": product_ref,
            "product_name": name,
            "product_name_status": "available" if name is not None else "unavailable",
            "behavior_count": len(product_rows),
            "behavior_counts": counts,
            "unique_member_count": len({
                row.member.member_id for row in product_rows if row.member is not None
            }),
            "total_holding_seconds": holding_total,
            "holding_duration_status": holding_status,
            "last_behavior_at": _event_time(
                max(row.occurred_at for row in product_rows), zone
            ),
        })
    return result


def _recent_timeline(
    rows: list[MemberBehaviorEvent],
    zone: ZoneInfo,
    member_refs: dict[str, str],
    product_refs: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    timeline = []
    for row in rows:
        if row.member is None:
            continue
        product_name = _row_product_name(row)
        duration, duration_status = _duration(row)
        timeline.append({
            "occurred_at": _event_time(row.occurred_at, zone),
            "member_ref": member_refs[row.member.member_id],
            "behavior_type": row.event_type,
            "behavior_label": BEHAVIOR_LABELS[row.event_type],
            "product_ref": product_refs[_row_product_group(row)],
            "product_name": product_name,
            "product_name_status": (
                "available" if product_name is not None else "unavailable"
            ),
            "duration_seconds": duration,
            "duration_status": duration_status,
        })
    return timeline


def _interacted_products(rows: list[MemberBehaviorEvent], zone: ZoneInfo) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = _row_product_group(row)
        group = groups.setdefault(key, {"first": index, "rows": [], "last": row.occurred_at})
        group["rows"].append(row)
        if row.occurred_at > group["last"]:
            group["last"] = row.occurred_at
    ordered = sorted(groups.values(), key=lambda group: group["last"], reverse=True)
    result = []
    for index, group in enumerate(ordered, start=1):
        product_rows = group["rows"]
        names = {_row_product_name(row) for row in product_rows}
        names.discard(None)
        name = next(iter(names)) if len(names) == 1 else None
        counts = _behavior_counts(product_rows)
        holding_total, holding_status = _holding_summary(product_rows)
        result.append({
            "product_ref": f"product_{index}",
            "product_name": name,
            "product_name_status": "available" if name is not None else "unavailable",
            "behavior_count": sum(counts.values()),
            "behavior_counts": counts,
            "total_holding_seconds": holding_total,
            "holding_duration_status": holding_status,
            "last_behavior_at": _event_time(group["last"], zone),
        })
    return result
