"""Standalone LLM tool-calling loop for Product Analytics queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from tools.harness.product_analytics.descriptions import PRODUCT_ANALYTICS_AGENT_TOOLS
from agent.analytics.product_interaction_mcp import (
    ProductAnalyticsMCPTools,
    build_default_product_analytics_mcp_tools,
)
from .tool_calling import (
    OllamaRetailToolCallingModel,
    RetailToolCallingModel,
    RetailToolCallingResponse,
)


class ProductAnalyticsAgentError(RuntimeError):
    pass


RETAIL_PRODUCT_ANALYTICS_SYSTEM_PROMPT = """You are the Retail Product Analytics Agent.
Use Product Analytics tools to obtain all product heat, interaction, holding, and trend statistics.
Never invent product data from general knowledge or from the user message.
When the user asks about product popularity, product interactions, performance, or comparisons,
choose the appropriate Product Analytics tool before answering. Use the runtime context to fill
store and time scope when it is not supplied. If no Product Analytics tool applies, answer without
calling one and state that this agent only has product interaction analytics."""


@dataclass(frozen=True)
class ProductAnalyticsAgentContext:
    """Runtime-owned query defaults; this context is never embedded in MCP."""

    store_id: str
    default_start_at: datetime
    default_end_at: datetime
    camera_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.store_id, str) or not self.store_id:
            raise ValueError("product_analytics_context_store_id_required")
        if self.camera_id is not None and (not isinstance(self.camera_id, str) or not self.camera_id):
            raise ValueError("product_analytics_context_camera_id_invalid")
        if not isinstance(self.default_start_at, datetime) or not isinstance(self.default_end_at, datetime):
            raise ValueError("product_analytics_context_time_required")
        if self.default_start_at.tzinfo is not None or self.default_end_at.tzinfo is not None:
            raise ValueError("product_analytics_context_time_must_be_naive")
        if self.default_end_at <= self.default_start_at:
            raise ValueError("product_analytics_context_time_range_invalid")

    def as_dict(self) -> dict[str, str]:
        result = {
            "store_id": self.store_id,
            "default_start_at": self.default_start_at.isoformat(),
            "default_end_at": self.default_end_at.isoformat(),
        }
        if self.camera_id is not None:
            result["camera_id"] = self.camera_id
        return result

    def defaults_for(self, tool_name: str) -> dict[str, Any]:
        common = {"store_id": self.store_id}
        if self.camera_id is not None:
            common["camera_id"] = self.camera_id
        if tool_name in {"rank_products", "get_product_behavior_summary"}:
            return {
                **common,
                "start_at": self.default_start_at.isoformat(),
                "end_at": self.default_end_at.isoformat(),
            }
        if tool_name == "compare_product_behavior":
            duration = self.default_end_at - self.default_start_at
            return {
                **common,
                "first_start_at": (self.default_start_at - duration).isoformat(),
                "first_end_at": self.default_start_at.isoformat(),
                "second_start_at": self.default_start_at.isoformat(),
                "second_end_at": self.default_end_at.isoformat(),
            }
        return common


@dataclass(frozen=True)
class ProductAnalyticsAgentResult:
    final_answer: str
    trace: dict[str, Any]


class ProductAnalyticsAgentRuntime:
    """Run an LLM-selected MCP tool loop without database access or routing rules."""

    def __init__(
        self,
        *,
        model: RetailToolCallingModel,
        mcp_tools: ProductAnalyticsMCPTools,
        context: ProductAnalyticsAgentContext,
        tools: tuple[dict[str, Any], ...] = PRODUCT_ANALYTICS_AGENT_TOOLS,
        max_tool_rounds: int = 3,
    ) -> None:
        if not callable(getattr(model, "complete", None)):
            raise TypeError("retail_tool_calling_model_required")
        if not callable(getattr(mcp_tools, "call", None)):
            raise TypeError("product_analytics_mcp_tools_required")
        if not isinstance(context, ProductAnalyticsAgentContext):
            raise TypeError("product_analytics_agent_context_required")
        if not isinstance(max_tool_rounds, int) or max_tool_rounds < 1:
            raise ValueError("max_tool_rounds_invalid")
        self.model = model
        self.mcp_tools = mcp_tools
        self.context = context
        self.tools = tuple(dict(tool) for tool in tools)
        self.allowed_tools = {str(tool["mcp_tool"]) for tool in self.tools}
        self.max_tool_rounds = max_tool_rounds

    def answer(self, user_message: str) -> ProductAnalyticsAgentResult:
        if not isinstance(user_message, str) or not user_message:
            raise ValueError("user_message_required")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_message(self.context)},
            {"role": "user", "content": user_message},
        ]
        trace: dict[str, Any] = {
            "user_message": user_message,
            "runtime_context": self.context.as_dict(),
            "available_tools": [dict(tool) for tool in self.tools],
            "model_tool_calls": [],
            "mcp_results": [],
            "final_answer": None,
            "events": [
                {"type": "system_prompt", "content": RETAIL_PRODUCT_ANALYTICS_SYSTEM_PROMPT},
                {"type": "user_message", "content": user_message},
                {"type": "available_tools", "tools": [dict(tool) for tool in self.tools]},
            ],
        }
        for tool_round in range(1, self.max_tool_rounds + 2):
            response = self._complete(messages)
            messages.append(_assistant_message(response))
            if not response.tool_calls:
                trace["final_answer"] = response.content
                trace["events"].append({"type": "final_answer", "content": response.content})
                return ProductAnalyticsAgentResult(response.content, trace)
            if tool_round > self.max_tool_rounds:
                raise ProductAnalyticsAgentError("product_analytics_tool_call_round_limit")
            self._execute_tool_calls(response.tool_calls, messages, trace, tool_round)
        raise AssertionError("unreachable")

    def run(self, user_message: str) -> ProductAnalyticsAgentResult:
        """Alias for composition roots that model the Runtime as a command."""
        return self.answer(user_message)

    def _complete(self, messages: list[dict[str, Any]]) -> RetailToolCallingResponse:
        response = self.model.complete(messages=messages, tools=list(self.tools))
        if not isinstance(response, RetailToolCallingResponse):
            raise ProductAnalyticsAgentError("retail_tool_calling_response_required")
        return response

    def _execute_tool_calls(
        self,
        tool_calls: tuple[Any, ...],
        messages: list[dict[str, Any]],
        trace: dict[str, Any],
        tool_round: int,
    ) -> None:
        for call in tool_calls:
            if call.name not in self.allowed_tools:
                raise ProductAnalyticsAgentError("product_analytics_tool_not_allowed")
            resolved_arguments = {**self.context.defaults_for(call.name), **call.arguments}
            trace["model_tool_calls"].append({
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments),
                "resolved_arguments": resolved_arguments,
                "tool_round": tool_round,
                "round": tool_round,
            })
            trace["events"].append({
                "type": "model_tool_call",
                "tool_round": tool_round,
                "round": tool_round,
                "action": "tool_call",
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments),
                "resolved_arguments": resolved_arguments,
            })
            result = self.mcp_tools.call(call.name, resolved_arguments)
            trace_result = {
                "tool_round": tool_round,
                "round": tool_round,
                "tool_call_id": call.id,
                "name": call.name,
                "result": result,
            }
            trace["mcp_results"].append(trace_result)
            trace["events"].append({"type": "mcp_result", **trace_result})
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
            })


def _assistant_message(response: RetailToolCallingResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False, sort_keys=True),
                },
            }
            for call in response.tool_calls
        ],
    }


def build_default_product_analytics_agent_runtime() -> ProductAnalyticsAgentRuntime:
    """Compose the standalone analytics Agent without touching legacy LangGraph."""
    return ProductAnalyticsAgentRuntime(
        model=OllamaRetailToolCallingModel(),
        mcp_tools=build_default_product_analytics_mcp_tools(),
        context=ProductAnalyticsAgentContext(
            store_id="store-1",
            camera_id="cam02",
            default_start_at=datetime(2026, 8, 20, 21, 7),
            default_end_at=datetime(2026, 8, 20, 21, 9),
        ),
    )


def _system_message(context: ProductAnalyticsAgentContext) -> str:
    return (
        RETAIL_PRODUCT_ANALYTICS_SYSTEM_PROMPT
        + "\n\nRuntime context (use as defaults, do not claim it is user-provided):\n"
        + json.dumps(context.as_dict(), ensure_ascii=False, sort_keys=True)
    )
