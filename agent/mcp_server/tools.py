"""MCP tool dispatch; business access remains behind ProductAnalyticsMCPTools."""

from __future__ import annotations

import json
from typing import Any

from agent.analytics.product_interaction_analytics import ProductAnalyticsError
from agent.analytics.product_interaction_mcp import ProductAnalyticsMCPTools
from agent.analytics.member_behavior import MemberBehaviorError
from agent.analytics.member_behavior_mcp import MemberBehaviorMCPTools

from .schemas import text_result


class ProductAnalyticsMCPToolDispatcher:
    def __init__(self, adapter: ProductAnalyticsMCPTools) -> None:
        if not callable(getattr(adapter, "call", None)):
            raise TypeError("product_analytics_mcp_tools_required")
        self.adapter = adapter

    def list_tools(self) -> list[dict[str, Any]]:
        return self.adapter.list_tools()

    def call(self, name: Any, arguments: Any) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            return text_result({"error": "tool_name_required"}, is_error=True)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return text_result({"error": "arguments_object_required"}, is_error=True)
        try:
            return text_result(self.adapter.call(name, arguments))
        except (ProductAnalyticsError, ValueError) as exc:
            return text_result({"error": str(exc)}, is_error=True)
        except Exception:
            return text_result({"error": "product_analytics_tool_failed"}, is_error=True)


class MemberBehaviorMCPToolDispatcher:
    def __init__(self, adapter: MemberBehaviorMCPTools) -> None:
        if not callable(getattr(adapter, "call", None)):
            raise TypeError("member_behavior_mcp_tools_required")
        self.adapter = adapter

    def list_tools(self) -> list[dict[str, Any]]:
        return self.adapter.list_tools()

    def call(self, name: Any, arguments: Any) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            return text_result({"error": "tool_name_required"}, is_error=True)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return text_result({"error": "arguments_object_required"}, is_error=True)
        try:
            return text_result(self.adapter.call(name, arguments))
        except (MemberBehaviorError, ValueError) as exc:
            return text_result({"error": str(exc)}, is_error=True)
        except Exception:
            return text_result({"error": "member_behavior_tool_failed"}, is_error=True)
