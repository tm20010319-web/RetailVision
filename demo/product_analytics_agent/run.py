"""Product Analytics Tool Calling Harness (not the production Agent entrypoint).

This local harness validates system prompts, tool schemas, native LLM tool
calls, and the in-process MCP adapter. Production Agent calls should originate
in mewcode-java through the RetailVision MCP Server.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from tools.harness.product_analytics.runtime import (
    ProductAnalyticsAgentContext,
    ProductAnalyticsAgentRuntime,
)
from tools.harness.product_analytics.tool_calling import (
    OllamaRetailToolCallingModel,
    ToolCallingNotSupportedError,
)
from agent.product_interaction_mcp import build_default_product_analytics_mcp_tools
from agent.product_interaction_agent_tools import PRODUCT_ANALYTICS_AGENT_TOOLS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-config", type=Path, default=Path("behavior_cameras.cam02.stage6a.json"))
    parser.add_argument("--camera-id", default="cam02")
    parser.add_argument("--start-at", default="2026-08-20T21:07:00")
    parser.add_argument("--end-at", default="2026-08-20T21:09:00")
    parser.add_argument("--query", default=None)
    parser.add_argument("--validate-tool-calling", action="store_true")
    args = parser.parse_args()

    store_id = _store_id_from_camera_config(args.camera_config, args.camera_id)
    context = ProductAnalyticsAgentContext(
        store_id=store_id,
        camera_id=args.camera_id,
        default_start_at=datetime.fromisoformat(args.start_at),
        default_end_at=datetime.fromisoformat(args.end_at),
    )
    model = OllamaRetailToolCallingModel()
    if args.validate_tool_calling:
        try:
            model.validate_tool_calling(tools=[dict(tool) for tool in PRODUCT_ANALYTICS_AGENT_TOOLS])
        except ToolCallingNotSupportedError as exc:
            print(exc.code)
            return 2
        print("tool_calling_supported")
    runtime = ProductAnalyticsAgentRuntime(
        model=model,
        mcp_tools=build_default_product_analytics_mcp_tools(),
        context=context,
    )
    query = args.query or input("User: ").strip()
    result = runtime.run(query)
    print(_render_trace(result.trace, result.final_answer))
    return 0


def _store_id_from_camera_config(path: Path, camera_id: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    for camera in data.get("cameras", ()): 
        if camera.get("camera_id") == camera_id and isinstance(camera.get("store_id"), str):
            return camera["store_id"]
    raise ValueError("camera_store_configuration_missing")


def _render_trace(trace: dict, final_answer: str) -> str:
    lines = ["====================", "USER:", trace["user_message"]]
    for event in trace["events"]:
        if event["type"] == "model_tool_call":
            lines.extend([
                "", f"MODEL TOOL CALL (round {event['tool_round']}):", event["name"],
                "arguments:", json.dumps(event["resolved_arguments"], ensure_ascii=False, indent=2, sort_keys=True),
            ])
        elif event["type"] == "mcp_result":
            lines.extend([
                "", f"MCP RESULT (round {event['tool_round']}):", event["name"],
                json.dumps(event["result"], ensure_ascii=False, indent=2, sort_keys=True),
            ])
    lines.extend(["", "FINAL ANSWER:", final_answer, "===================="])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
