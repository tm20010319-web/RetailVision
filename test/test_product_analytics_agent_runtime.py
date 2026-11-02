from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.models import Base  # noqa: E402
from agent.offline.offline_behavior import Stage9BehaviorFactAdapter  # noqa: E402
from tools.harness.product_analytics.runtime import (  # noqa: E402
    ProductAnalyticsAgentContext,
    ProductAnalyticsAgentRuntime,
)
from tools.harness.product_analytics.tool_calling import RetailToolCall, RetailToolCallingResponse  # noqa: E402
from tools.harness.product_analytics.tool_calling import OllamaRetailToolCallingModel  # noqa: E402
from agent.analytics.product_interaction_mcp import create_product_analytics_mcp_tools  # noqa: E402
from agent.analytics.product_interaction_analytics import ProductInteractionFactWriter  # noqa: E402
from agent.store_context import StoreContext  # noqa: E402
from agent.analytics.product_catalog import ProductCatalog  # noqa: E402


class ScriptedToolCallingModel:
    def __init__(self, tool_name, arguments, final_answer):
        self.tool_name = tool_name
        self.arguments = arguments
        self.final_answer = final_answer
        self.requests = []

    def complete(self, *, messages, tools):
        self.requests.append({
            "messages": [dict(message) for message in messages],
            "tools": [dict(tool) for tool in tools],
        })
        if len(self.requests) == 1:
            return RetailToolCallingResponse(
                "", (RetailToolCall("call-1", self.tool_name, self.arguments),)
            )
        return RetailToolCallingResponse(self.final_answer)


class ProductAnalyticsAgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        writer = ProductInteractionFactWriter(sessions, track_hash_secret=b"test")
        facts = Stage9BehaviorFactAdapter().normalize(
            analysis_job_id="job-1", analysis_run_id="run-1", source_video_id="video-1",
            raw_events=(
                self._raw("PICK_UP", "session-1", datetime(2026, 8, 20, 4, 0)),
                self._raw("HOLDING", "session-1", datetime(2026, 8, 20, 4, 0)),
                self._raw("PUT_DOWN", "session-1", datetime(2026, 8, 20, 4, 5)),
            ),
        ).facts
        writer.write(facts, store_id="store-1")
        self.mcp_tools = create_product_analytics_mcp_tools(
            sessions,
            StoreContext(
                store_id="store-1",
                default_camera_id="cam-1",
                default_start_at=datetime(2026, 8, 20, 3),
                default_end_at=datetime(2026, 8, 20, 5),
                business_date=datetime(2026, 8, 20).date(),
            ),
            product_catalog=ProductCatalog.from_mapping({
                "smart_toy_a": {"智能熊"},
            }),
        )
        self.context = ProductAnalyticsAgentContext(
            store_id="store-1",
            camera_id="cam-1",
            default_start_at=datetime(2026, 8, 20, 3),
            default_end_at=datetime(2026, 8, 20, 5),
        )

    @staticmethod
    def _raw(kind, session, at):
        return SimpleNamespace(
            event_id=f"{session}:{kind}", event_type=kind, observed_at=at,
            camera_id="cam-1", product_key="smart_toy_a", product_name="智能熊",
            product_track_token="product-1", person_track_id=1,
            person_track_token="person-1", interaction_session_id=session, confidence=1.0,
        )

    def test_rank_query_runs_full_llm_tool_call_trace(self):
        result, model = self._run(
            "最近哪些商品最火？", "rank_products",
            {"store_id": "store-1", "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00", "limit": 10},
        )
        self.assertEqual("rank_products", result.trace["model_tool_calls"][0]["name"])
        self.assertEqual("智能熊是最近最热门商品。", result.final_answer)
        self.assertEqual("tool", model.requests[1]["messages"][-1]["role"])
        self.assertEqual("smart_toy_a", result.trace["mcp_results"][0]["result"][0]["product_key"])
        self.assertEqual(
            ["system_prompt", "user_message", "available_tools", "model_tool_call", "mcp_result", "final_answer"],
            [event["type"] for event in result.trace["events"]],
        )
        self.assertEqual(1, result.trace["model_tool_calls"][0]["tool_round"])
        self.assertEqual("store-1", result.trace["model_tool_calls"][0]["resolved_arguments"]["store_id"])

    def test_summary_query_runs_full_llm_tool_call_trace(self):
        result, _model = self._run(
            "智能熊最近表现怎么样？", "get_product_behavior_summary",
            {"store_id": "store-1", "product_key": "smart_toy_a", "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00"},
        )
        self.assertEqual("get_product_behavior_summary", result.trace["model_tool_calls"][0]["name"])
        self.assertEqual(1, result.trace["mcp_results"][0]["result"]["interaction_count"])

    def test_compare_query_runs_full_llm_tool_call_trace(self):
        result, _model = self._run(
            "智能熊今天和昨天相比如何？", "compare_product_behavior",
            {"store_id": "store-1", "product_key": "smart_toy_a", "first_start_at": "2026-08-19T00:00:00", "first_end_at": "2026-08-20T00:00:00", "second_start_at": "2026-08-20T00:00:00", "second_end_at": "2026-08-21T00:00:00"},
        )
        self.assertEqual("compare_product_behavior", result.trace["model_tool_calls"][0]["name"])
        self.assertEqual(1, result.trace["mcp_results"][0]["result"]["interaction_change"])

    def test_runtime_never_uses_keyword_fallback(self):
        model = ScriptedToolCallingModel(
            "rank_products",
            {"store_id": "store-1", "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00"},
            "完成",
        )
        with patch(
            "tools.harness.product_analytics.descriptions.select_product_analytics_tool",
            side_effect=AssertionError("keyword fallback must not run"),
        ):
            ProductAnalyticsAgentRuntime(model=model, mcp_tools=self.mcp_tools, context=self.context).answer("最近哪些商品最火？")

    def test_non_product_question_does_not_force_an_analytics_tool(self):
        class NoToolModel:
            def complete(self, *, messages, tools):
                return RetailToolCallingResponse("请说明具体的商品分析问题。")

        result = ProductAnalyticsAgentRuntime(
            model=NoToolModel(), mcp_tools=self.mcp_tools, context=self.context
        ).answer("最近门店情况怎么样？")
        self.assertEqual([], result.trace["model_tool_calls"])
        self.assertEqual("请说明具体的商品分析问题。", result.final_answer)

    def test_ollama_adapter_converts_tool_call_and_json_arguments(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "ollama-1",
                        "function": {"name": "rank_products", "arguments": json.dumps({"limit": 3})},
                    }],
                }
            },
        )
        with patch("tools.harness.product_analytics.tool_calling.requests.post", return_value=response) as post:
            model = OllamaRetailToolCallingModel(ollama_url="http://ollama/api/chat", ollama_model="test")
            parsed = model.complete(messages=[{"role": "user", "content": "最近哪些商品最火？"}], tools=[{
                "name": "rank_products", "description": "rank", "input_schema": {"type": "object"},
            }])
        self.assertEqual("rank_products", parsed.tool_calls[0].name)
        self.assertEqual(3, parsed.tool_calls[0].arguments["limit"])
        sent_tool = post.call_args.kwargs["json"]["tools"][0]
        self.assertEqual("rank_products", sent_tool["function"]["name"])

    def _run(self, user_message, tool_name, arguments):
        model = ScriptedToolCallingModel(tool_name, arguments, "智能熊是最近最热门商品。")
        result = ProductAnalyticsAgentRuntime(model=model, mcp_tools=self.mcp_tools, context=self.context).answer(user_message)
        return result, model


if __name__ == "__main__":
    unittest.main()
