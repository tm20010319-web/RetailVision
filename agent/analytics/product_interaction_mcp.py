"""MCP-facing adapters for the read-only Product Analytics query service.

This module intentionally contains no persistence logic.  MCP callers receive
the same aggregates as :class:`ProductAnalyticsQueryService`; the adapter only
validates transport-shaped arguments and converts ISO timestamps to the
service's naive, device-local datetime contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from agent.analytics.product_interaction_analytics import (
    ProductAnalyticsError,
    ProductAnalyticsQueryService,
)
from agent.store_context import StoreContext
from agent.analytics.product_catalog import ProductCatalog


def _datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ProductAnalyticsError(f"{name}_required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductAnalyticsError(f"{name}_invalid") from exc
    if parsed.tzinfo is not None:
        raise ProductAnalyticsError(f"{name}_must_be_naive_local_time")
    return parsed


def _required_args(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ProductAnalyticsError("arguments_object_required")
    return arguments


class ProductAnalyticsMCPTools:
    """Thin, framework-neutral MCP tool surface.

    A concrete MCP server can register ``list_tools`` and forward tool calls
    to ``call``.  Keeping this object framework-neutral avoids adding a second
    database or a hard dependency on an MCP SDK to the offline domain.
    """

    def __init__(
        self,
        query_service: ProductAnalyticsQueryService,
        store_context: StoreContext | None = None,
        *,
        product_catalog: ProductCatalog | None = None,
    ) -> None:
        if not all(
            callable(getattr(query_service, name, None))
            for name in (
                "rank_products",
                "get_product_behavior_summary",
                "compare_product_behavior",
                "recommend_hot_products",
                "analyze_product_trend",
                "detect_product_anomaly",
                "suggest_product_display",
            )
        ):
            raise TypeError("query_service_required")
        self.query_service = query_service
        self.store_context = store_context or StoreContext.from_environment()
        self.product_catalog = product_catalog or ProductCatalog.empty()

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "rank_products",
                "description": "Rank products by the tool's deterministic anonymous interaction metrics. Use for product interaction-heat ranking. The Agent may summarize which product ranks higher and compare returned interaction or holding metrics.",
                "inputSchema": RANK_PRODUCTS_SCHEMA,
            },
            {
                "name": "get_product_behavior_summary",
                "description": "Summarize one product's pickup, put-down, completed-interaction, holding-duration, and anonymous-track facts. The Agent may provide a concise natural-language summary.",
                "inputSchema": PRODUCT_SUMMARY_SCHEMA,
            },
            {
                "name": "compare_product_behavior",
                "description": "Compare two products or two time windows using returned interaction and holding metrics. The Agent may state which product has more interactions or longer holding duration, identify the metric used, and must not derive ratios or customer/commerce conclusions.",
                "inputSchema": PRODUCT_COMPARE_SCHEMA,
            },
            {
                "name": "recommend_hot_products",
                "description": (
                    "Return products ranked for display priority using deterministic "
                    "anonymous interaction facts. The Agent may state the ranked "
                    "product and returned interaction or holding facts; do not expose "
                    "internal fields, derived ratios, customer/commerce conclusions, "
                    "or physical location advice."
                ),
                "inputSchema": HOT_PRODUCTS_SCHEMA,
            },
            {
                "name": "analyze_product_trend",
                "description": "Report the returned interaction trend between the current and previous window. The Agent may explain an up/down/flat result using the returned current and previous metrics.",
                "inputSchema": PRODUCT_TREND_SCHEMA,
            },
            {
                "name": "detect_product_anomaly",
                "description": "Report products that meet the tool's deterministic anomaly rule. The Agent may explain which returned metrics triggered the rule but must not infer customer intent or commerce outcomes.",
                "inputSchema": PRODUCT_ANOMALY_SCHEMA,
            },
            {
                "name": "suggest_product_display",
                "description": (
                    "Return increase, maintain, or decrease display-priority actions based "
                    "on returned anonymous interaction metrics (for example: 商品应该怎样调整陈列). "
                    "The Agent may explain the action using those metrics, but cannot expose "
                    "internal fields or recommend a physical location."
                ),
                "inputSchema": PRODUCT_DISPLAY_SCHEMA,
            },
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        args = _required_args(arguments)
        response_detail = _response_detail(args)
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "rank_products": self.rank_products,
            "get_product_behavior_summary": self.get_product_behavior_summary,
            "compare_product_behavior": self.compare_product_behavior,
            "recommend_hot_products": self.recommend_hot_products,
            "analyze_product_trend": self.analyze_product_trend,
            "detect_product_anomaly": self.detect_product_anomaly,
            "suggest_product_display": self.suggest_product_display,
        }
        try:
            handler = handlers[name]
        except KeyError as exc:
            raise ProductAnalyticsError("unknown_product_analytics_tool") from exc
        result = handler(args)
        if name in {"rank_products", "recommend_hot_products"}:
            return _project_ranked_products(result, response_detail)
        return result

    def rank_products(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        scope = self.store_context.resolve_scope(
            store_id=_string(arguments, "store_id"),
            camera_id=_optional_string(arguments, "camera_id"),
            start_at=_datetime(arguments.get("start_at"), "start_at"),
            end_at=_datetime(arguments.get("end_at"), "end_at"),
        )
        return self.query_service.rank_products(
            **scope,
            limit=arguments.get("limit", 10),
        )

    def get_product_behavior_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        scope = self.store_context.resolve_scope(
            store_id=_string(arguments, "store_id"),
            camera_id=_optional_string(arguments, "camera_id"),
            start_at=_datetime(arguments.get("start_at"), "start_at"),
            end_at=_datetime(arguments.get("end_at"), "end_at"),
        )
        return self.query_service.get_product_behavior_summary(
            product_key=self._product_key(arguments, "product_key"),
            **scope,
        )

    def compare_product_behavior(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("second_product_key") is not None:
            scope = self.store_context.resolve_scope(
                store_id=_string(arguments, "store_id"),
                camera_id=_optional_string(arguments, "camera_id"),
                start_at=_datetime(arguments.get("start_at"), "start_at"),
                end_at=_datetime(arguments.get("end_at"), "end_at"),
            )
            return self.query_service.compare_products(
                first_product_key=self._product_key(arguments, "product_key"),
                second_product_key=self._product_key(arguments, "second_product_key"),
                **scope,
            )
        store_id = _string(arguments, "store_id")
        camera_id = _optional_string(arguments, "camera_id")
        first_scope = self.store_context.resolve_scope(
            store_id=store_id,
            camera_id=camera_id,
            start_at=_datetime(arguments.get("first_start_at"), "first_start_at"),
            end_at=_datetime(arguments.get("first_end_at"), "first_end_at"),
        )
        second_scope = self.store_context.resolve_scope(
            store_id=store_id,
            camera_id=camera_id,
            start_at=_datetime(arguments.get("second_start_at"), "second_start_at"),
            end_at=_datetime(arguments.get("second_end_at"), "second_end_at"),
        )
        return self.query_service.compare_product_behavior(
            product_key=self._product_key(arguments, "product_key"),
            store_id=first_scope["store_id"],
            camera_id=first_scope["camera_id"],
            first_start_at=first_scope["start_at"],
            first_end_at=first_scope["end_at"],
            second_start_at=second_scope["start_at"],
            second_end_at=second_scope["end_at"],
        )

    def recommend_hot_products(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        scope = self._scope(arguments)
        return self.query_service.recommend_hot_products(
            **scope,
            limit=arguments.get("limit", 10),
        )

    def analyze_product_trend(self, arguments: dict[str, Any]) -> dict[str, Any]:
        scope = self._scope(arguments)
        return self.query_service.analyze_product_trend(
            **scope, product_key=self._product_key(arguments, "product_key"),
        )

    def detect_product_anomaly(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        scope = self._scope(arguments)
        return self.query_service.detect_product_anomaly(
            **scope, limit=arguments.get("limit", 10),
        )

    def suggest_product_display(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        scope = self._scope(arguments)
        return self.query_service.suggest_product_display(
            **scope, limit=arguments.get("limit", 10),
        )

    def _scope(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.store_context.resolve_scope(
            store_id=_optional_string(arguments, "store_id"),
            camera_id=_optional_string(arguments, "camera_id"),
            start_at=_optional_datetime(arguments, "start_at"),
            end_at=_optional_datetime(arguments, "end_at"),
        )

    def _product_key(self, arguments: dict[str, Any], name: str) -> str:
        return self.product_catalog.resolve(_string(arguments, name))


def _string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ProductAnalyticsError(f"{name}_required")
    return value


def _optional_string(arguments: dict[str, Any], name: str) -> Optional[str]:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ProductAnalyticsError(f"{name}_invalid")
    return value


def _optional_datetime(arguments: dict[str, Any], name: str) -> Optional[datetime]:
    value = arguments.get(name)
    return None if value is None else _datetime(value, name)


def _response_detail(arguments: dict[str, Any]) -> str:
    value = arguments.get("response_detail")
    if value is None:
        return "detailed"
    if value not in {"concise", "normal", "detailed"}:
        raise ProductAnalyticsError("response_detail_invalid")
    return value


def _project_ranked_products(value: Any, response_detail: str) -> Any:
    if response_detail == "detailed" or not isinstance(value, list):
        return value
    fields = ["product_key", "product_name", "pickup_count"]
    if response_detail == "normal":
        fields.extend((
            "completed_interaction_count",
            "total_holding_seconds",
            "average_holding_seconds",
        ))
    projected = []
    for rank, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        row = {"rank": rank}
        row.update({field: item[field] for field in fields if field in item})
        projected.append(row)
    return projected


RANK_PRODUCTS_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string"},
        "camera_id": {"type": "string"},
        "start_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "end_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        "response_detail": {
            "type": "string", "enum": ["concise", "normal", "detailed"],
            "description": "Fields needed for the final answer; omit for the full legacy result.",
        },
    },
    "required": ["store_id", "start_at", "end_at"],
    "additionalProperties": False,
}

PRODUCT_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string"},
        "camera_id": {"type": "string"},
        "product_key": {"type": "string"},
        "start_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "end_at": {"type": "string", "description": "Naive ISO-8601 local time."},
    },
    "required": ["store_id", "product_key", "start_at", "end_at"],
    "additionalProperties": False,
}

PRODUCT_COMPARE_SCHEMA = {
    "type": "object",
    "description": (
        "Compare one product across two time windows, or compare two products "
        "in one window. For two products provide product_key, second_product_key, "
        "start_at, and end_at. For time-window comparison provide first_start_at, "
        "first_end_at, second_start_at, and second_end_at."
    ),
    "properties": {
        "store_id": {"type": "string"},
        "camera_id": {"type": "string"},
        "product_key": {"type": "string"},
        "second_product_key": {"type": "string"},
        "start_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "end_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "first_start_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "first_end_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "second_start_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "second_end_at": {"type": "string", "description": "Naive ISO-8601 local time."},
    },
    "required": ["store_id", "product_key"],
    "anyOf": [
        {"required": ["second_product_key", "start_at", "end_at"]},
        {"required": [
            "first_start_at", "first_end_at",
            "second_start_at", "second_end_at",
        ]},
    ],
    "additionalProperties": False,
}

HOT_PRODUCTS_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string"},
        "camera_id": {"type": "string"},
        "start_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "end_at": {"type": "string", "description": "Naive ISO-8601 local time."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        "response_detail": {
            "type": "string", "enum": ["concise", "normal", "detailed"],
            "description": "Fields needed for the final answer; omit for the full legacy result.",
        },
    },
    "required": [],
    "additionalProperties": False,
}

PRODUCT_TREND_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string"}, "camera_id": {"type": "string"},
        "product_key": {"type": "string"}, "start_at": {"type": "string"},
        "end_at": {"type": "string"},
    },
    "required": ["product_key"], "additionalProperties": False,
}

PRODUCT_ANOMALY_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string"}, "camera_id": {"type": "string"},
        "start_at": {"type": "string"}, "end_at": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    },
    "required": [], "additionalProperties": False,
}

PRODUCT_DISPLAY_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string"}, "camera_id": {"type": "string"},
        "start_at": {"type": "string"}, "end_at": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    },
    "required": [], "additionalProperties": False,
}


def create_product_analytics_mcp_tools(
    session_factory: Any,
    store_context: StoreContext | None = None,
    *,
    product_catalog: ProductCatalog | None = None,
) -> ProductAnalyticsMCPTools:
    """Compose the MCP adapter from the application's existing DB seam."""
    return ProductAnalyticsMCPTools(
        ProductAnalyticsQueryService(session_factory), store_context,
        product_catalog=product_catalog,
    )


def build_default_product_analytics_mcp_tools() -> ProductAnalyticsMCPTools:
    """Compose against the application's configured session factory on demand."""
    from agent.db import get_session_factory

    return create_product_analytics_mcp_tools(get_session_factory())
