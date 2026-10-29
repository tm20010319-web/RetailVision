"""Agent-facing descriptions for Product Analytics MCP tools."""

from __future__ import annotations

from typing import Any

from agent.product_interaction_mcp import (
    PRODUCT_COMPARE_SCHEMA,
    PRODUCT_ANOMALY_SCHEMA,
    PRODUCT_DISPLAY_SCHEMA,
    PRODUCT_TREND_SCHEMA,
    PRODUCT_SUMMARY_SCHEMA,
    RANK_PRODUCTS_SCHEMA,
    HOT_PRODUCTS_SCHEMA,
)


PRODUCT_ANALYTICS_AGENT_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "rank_products",
        "mcp_tool": "rank_products",
        "description": "Find which products are most interacted with in a store and time window.",
        "input_schema": RANK_PRODUCTS_SCHEMA,
    },
    {
        "name": "get_product_behavior_summary",
        "mcp_tool": "get_product_behavior_summary",
        "description": "Explain one product's anonymous pickup and holding behavior.",
        "input_schema": PRODUCT_SUMMARY_SCHEMA,
    },
    {
        "name": "compare_product_behavior",
        "mcp_tool": "compare_product_behavior",
        "description": "Compare one product across time windows or two products in one window.",
        "input_schema": PRODUCT_COMPARE_SCHEMA,
    },
    {
        "name": "recommend_hot_products",
        "mcp_tool": "recommend_hot_products",
        "description": "Recommend products worth focused store attention using interaction facts.",
        "input_schema": HOT_PRODUCTS_SCHEMA,
    },
    {
        "name": "analyze_product_trend",
        "mcp_tool": "analyze_product_trend",
        "description": "Analyze a product's interaction trend and recommendation.",
        "input_schema": PRODUCT_TREND_SCHEMA,
    },
    {
        "name": "detect_product_anomaly",
        "mcp_tool": "detect_product_anomaly",
        "description": "Find products with high attention but abnormal engagement.",
        "input_schema": PRODUCT_ANOMALY_SCHEMA,
    },
    {
        "name": "suggest_product_display",
        "mcp_tool": "suggest_product_display",
        "description": "Suggest which products deserve priority display placement.",
        "input_schema": PRODUCT_DISPLAY_SCHEMA,
    },
)

# Stable names for composition roots that prefer an explicit MCP/Agent label.
MCP_TOOL_DEFINITIONS = PRODUCT_ANALYTICS_AGENT_TOOLS
AGENT_TOOL_DESCRIPTIONS = PRODUCT_ANALYTICS_AGENT_TOOLS


def describe_product_analytics_tools() -> list[dict[str, Any]]:
    """Return JSON-safe tool descriptions for an Agent composition root."""
    return [dict(tool) for tool in PRODUCT_ANALYTICS_AGENT_TOOLS]


def select_product_analytics_tool(user_text: str) -> str | None:
    """Test-only fallback; production Agent routing must use LLM tool calls."""
    if not isinstance(user_text, str):
        return None
    text = user_text.lower()
    if any(token in text for token in ("最火", "排行", "热门", "most popular", "popular")):
        return "rank_products"
    if any(token in text for token in ("比较", "对比", "compare", "vs")):
        return "compare_product_behavior"
    if any(token in text for token in ("表现", "持有", "holding", "summary")):
        return "get_product_behavior_summary"
    return None
