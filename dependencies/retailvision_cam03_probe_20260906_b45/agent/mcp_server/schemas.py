"""MCP protocol and Product Analytics tool schemas."""

from __future__ import annotations

import json
from typing import Any

from agent.product_interaction_mcp import ProductAnalyticsMCPTools
from agent.member_behavior_mcp import MemberBehaviorMCPTools


SERVER_INFO = {
    "name": "retailvision-product-analytics",
    "version": "1.0.0",
}

# Keep the Java MCP SDK used by mewcode (1.1.3) in the negotiated set.  Newer
# clients may still select the newer protocol when they advertise it.
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-06-18")
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


def negotiate_protocol_version(client_version: Any) -> str:
    """Choose a protocol version understood by both sides.

    Older clients such as mewcode's SDK advertise ``2024-11-05``.  When a
    newer client advertises a version supported by this server, echo it;
    otherwise use the compatibility default.
    """
    if client_version in SUPPORTED_PROTOCOL_VERSIONS:
        return client_version
    return PROTOCOL_VERSION


def tool_definitions() -> list[dict[str, Any]]:
    """Return MCP ``tools/list`` entries from the existing adapter contract."""
    return [
        {
            "name": item["name"],
            "description": item["description"],
            "inputSchema": item["inputSchema"],
        }
        for item in ProductAnalyticsMCPTools.list_tools()
    ]


def member_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "description": item["description"],
            "inputSchema": item["inputSchema"],
        }
        for item in MemberBehaviorMCPTools.list_tools()
    ]


def text_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False, sort_keys=True,
            ),
        }],
        "isError": is_error,
    }


def jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
