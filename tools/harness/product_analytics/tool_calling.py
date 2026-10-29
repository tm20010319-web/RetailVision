"""Tool-calling contracts and the Ollama adapter for Product Analytics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from agent.config import settings


class ProductAnalyticsToolCallingError(RuntimeError):
    pass


class ToolCallingNotSupportedError(ProductAnalyticsToolCallingError):
    """The configured provider answered but did not return native tool calls."""

    code = "tool_calling_not_supported"


@dataclass(frozen=True)
class RetailToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class RetailToolCallingResponse:
    content: str
    tool_calls: tuple[RetailToolCall, ...] = ()


class RetailToolCallingModel(Protocol):
    """Provider-neutral LLM contract for the Product Analytics Agent loop."""

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> RetailToolCallingResponse: ...


class OllamaRetailToolCallingModel:
    """Ollama `/api/chat` adapter using its native function-tool protocol."""

    def __init__(
        self,
        *,
        ollama_url: str | None = None,
        ollama_model: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.ollama_url = ollama_url or _chat_url(settings.OLLAMA_URL)
        self.ollama_model = ollama_model or settings.OLLAMA_MODEL
        self.timeout = timeout

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> RetailToolCallingResponse:
        response = requests.post(
            self.ollama_url,
            json={
                "model": self.ollama_model,
                "messages": messages,
                "tools": [_ollama_tool(tool) for tool in tools],
                "stream": False,
            },
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if response.status_code in {400, 422}:
                raise ToolCallingNotSupportedError(
                    ToolCallingNotSupportedError.code
                ) from exc
            raise
        payload = response.json()
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ProductAnalyticsToolCallingError("ollama_tool_calling_message_missing")
        content = message.get("content", "")
        if not isinstance(content, str):
            raise ProductAnalyticsToolCallingError("ollama_tool_calling_content_invalid")
        return RetailToolCallingResponse(
            content=content,
            tool_calls=tuple(
                _tool_call(item, index)
                for index, item in enumerate(message.get("tool_calls", ()))
            ),
        )

    def validate_tool_calling(self, *, tools: list[dict[str, Any]]) -> RetailToolCallingResponse:
        """Probe native function calling without falling back to text routing."""
        response = self.complete(
            messages=[{
                "role": "user",
                "content": "Call rank_products with an empty JSON object. Return a tool call only.",
            }],
            tools=tools,
        )
        if not response.tool_calls:
            raise ToolCallingNotSupportedError(ToolCallingNotSupportedError.code)
        return response


def _chat_url(value: str) -> str:
    if value.endswith("/api/generate"):
        return value[:-len("/generate")] + "/chat"
    return value


def _ollama_tool(tool: dict[str, Any]) -> dict[str, Any]:
    name = tool.get("mcp_tool", tool.get("name"))
    schema = tool.get("input_schema", tool.get("inputSchema"))
    if not isinstance(name, str) or not name or not isinstance(schema, dict):
        raise ProductAnalyticsToolCallingError("agent_tool_schema_invalid")
    description = tool.get("description", "")
    if not isinstance(description, str):
        raise ProductAnalyticsToolCallingError("agent_tool_description_invalid")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }


def _tool_call(value: Any, index: int) -> RetailToolCall:
    if not isinstance(value, dict):
        raise ProductAnalyticsToolCallingError("ollama_tool_call_invalid")
    function = value.get("function", value)
    if not isinstance(function, dict):
        raise ProductAnalyticsToolCallingError("ollama_tool_call_invalid")
    name = function.get("name")
    arguments = function.get("arguments", {})
    call_id = value.get("id") or f"ollama-call-{index + 1}"
    if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
        raise ProductAnalyticsToolCallingError("ollama_tool_call_invalid")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ProductAnalyticsToolCallingError("ollama_tool_arguments_invalid") from exc
    if not isinstance(arguments, dict):
        raise ProductAnalyticsToolCallingError("ollama_tool_arguments_invalid")
    return RetailToolCall(id=call_id, name=name, arguments=arguments)


# Product-domain spelling kept as a stable import for future Agent composition.
ProductAnalyticsToolCallingModel = RetailToolCallingModel
ProductAnalyticsOllamaToolCallingModel = OllamaRetailToolCallingModel
