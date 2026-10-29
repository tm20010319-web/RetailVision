"""MCP-facing adapter for read-only Offline member behavior queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from agent.member_behavior import MemberBehaviorError, MemberBehaviorQueryService


class MemberBehaviorMCPTools:
    def __init__(self, query_service: MemberBehaviorQueryService) -> None:
        if not all(
            callable(getattr(query_service, name, None))
            for name in (
                "get_member_behavior_summary",
                "list_member_behavior_timeline",
            )
        ):
            raise TypeError("member_behavior_query_service_required")
        self.query_service = query_service

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "get_member_behavior_summary",
                "description": (
                    "Summarize formally published Offline pickup, holding, and "
                    "put-down facts for one confirmed active member within the "
                    "trusted store, camera, and time scope. Returns safe member, "
                    "scope, behavior-count, holding-quality, and product-summary facts; "
                    "may return member_not_found, member_ambiguous, invalid_time, "
                    "invalid_time_range, scope_out_of_bounds, or invalid_arguments."
                ),
                "inputSchema": MEMBER_SUMMARY_SCHEMA,
            },
            {
                "name": "list_member_behavior_timeline",
                "description": (
                    "List the earliest formally published Offline pickup, holding, "
                    "and put-down facts for one confirmed active member within the "
                    "trusted scope. The limit is 1 to 50; has_more only reports "
                    "truncation and V1 has no cursor or continuation token."
                ),
                "inputSchema": MEMBER_TIMELINE_SCHEMA,
            },
        ]

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise MemberBehaviorError("invalid_arguments")
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "get_member_behavior_summary": self.get_member_behavior_summary,
            "list_member_behavior_timeline": self.list_member_behavior_timeline,
        }
        try:
            handler = handlers[name]
        except (KeyError, TypeError) as exc:
            raise MemberBehaviorError("unknown_member_behavior_tool") from exc
        return handler(arguments)

    def get_member_behavior_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(arguments, MEMBER_SUMMARY_SCHEMA["properties"])
        return self.query_service.get_member_behavior_summary(
            **self._common_arguments(arguments)
        )

    def list_member_behavior_timeline(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(arguments, MEMBER_TIMELINE_SCHEMA["properties"])
        common = self._common_arguments(arguments)
        if "limit" in arguments:
            limit = arguments["limit"]
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
                raise MemberBehaviorError("invalid_arguments")
        else:
            limit = 20
        return self.query_service.list_member_behavior_timeline(
            **common, limit=limit
        )

    @staticmethod
    def _validate_keys(arguments: dict[str, Any], properties: dict[str, Any]) -> None:
        if any(key not in properties for key in arguments):
            raise MemberBehaviorError("invalid_arguments")

    @staticmethod
    def _common_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        member_ref = arguments.get("member_ref")
        if not isinstance(member_ref, str) or not member_ref.strip():
            raise MemberBehaviorError("invalid_arguments")

        start_present = "start_at" in arguments
        end_present = "end_at" in arguments
        if start_present != end_present:
            raise MemberBehaviorError("invalid_time_range")
        start_at = _local_datetime(arguments.get("start_at")) if start_present else None
        end_at = _local_datetime(arguments.get("end_at")) if end_present else None

        store_id = _optional_nullable_string(arguments, "store_id")
        camera_id = _optional_nullable_string(
            arguments, "camera_id", nullable=False
        )
        product_name = _optional_product_name(arguments)
        return {
            "member_ref": member_ref,
            "store_id": store_id,
            "camera_id": camera_id,
            "start_at": start_at,
            "end_at": end_at,
            "product_name": product_name,
            "store_provided": "store_id" in arguments,
            "camera_provided": "camera_id" in arguments,
            "start_provided": start_present,
            "end_provided": end_present,
            "product_provided": "product_name" in arguments,
        }


def _local_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise MemberBehaviorError("invalid_time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MemberBehaviorError("invalid_time") from exc
    if parsed.tzinfo is not None:
        raise MemberBehaviorError("invalid_time")
    return parsed


def _optional_nullable_string(
    arguments: dict[str, Any], name: str, *, nullable: bool = True
) -> Optional[str]:
    if name not in arguments:
        return None
    value = arguments[name]
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise MemberBehaviorError("invalid_arguments")
    return value


def _optional_product_name(arguments: dict[str, Any]) -> Optional[str]:
    if "product_name" not in arguments or arguments["product_name"] is None:
        return None
    value = arguments["product_name"]
    if not isinstance(value, str) or not value.strip():
        raise MemberBehaviorError("invalid_arguments")
    return value.strip()


_NULLABLE_STRING = {"type": ["string", "null"]}
_COMMON_PROPERTIES = {
    "member_ref": {"type": "string", "minLength": 1},
    "store_id": _NULLABLE_STRING,
    "camera_id": {"type": "string", "minLength": 1},
    "start_at": {
        "type": "string",
        "description": "Naive ISO-8601 time in the trusted store timezone.",
    },
    "end_at": {
        "type": "string",
        "description": "Naive ISO-8601 time in the trusted store timezone.",
    },
    "product_name": {"type": ["string", "null"]},
}

MEMBER_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": dict(_COMMON_PROPERTIES),
    "required": ["member_ref"],
    "additionalProperties": False,
}
MEMBER_TIMELINE_SCHEMA = {
    "type": "object",
    "properties": {
        **_COMMON_PROPERTIES,
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
    },
    "required": ["member_ref"],
    "additionalProperties": False,
}
