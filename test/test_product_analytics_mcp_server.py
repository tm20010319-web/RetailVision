from datetime import datetime
from io import StringIO
from types import SimpleNamespace
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.models import Base  # noqa: E402
from agent.offline_behavior import Stage9BehaviorFactAdapter  # noqa: E402
from agent.product_interaction_analytics import ProductAnalyticsQueryService, ProductInteractionFactWriter  # noqa: E402
from agent.product_interaction_mcp import ProductAnalyticsMCPTools  # noqa: E402
from agent.mcp_server.server import (  # noqa: E402
    ProductAnalyticsMCPServer,
    build_agent_instructions,
    build_default_server,
    configured_product_aliases,
)
from agent.mcp_server.grounding_contract import (  # noqa: E402
    HARD_GROUNDING_BOUNDARY,
    SAFE_DERIVED_INTERPRETATIONS,
)
from agent.mcp_server.tools import ProductAnalyticsMCPToolDispatcher  # noqa: E402
from agent.mcp_server.transport import StdioJsonRpcTransport  # noqa: E402
from agent.store_context import StoreContext  # noqa: E402
from agent.product_catalog import ProductCatalog  # noqa: E402


class ProductAnalyticsMCPServerTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        writer = ProductInteractionFactWriter(sessions, track_hash_secret=b"test")
        raw = (
            self._raw("PICK_UP", datetime(2026, 8, 20, 21, 7)),
            self._raw("HOLDING", datetime(2026, 8, 20, 21, 7)),
            self._raw("PUT_DOWN", datetime(2026, 8, 20, 21, 8)),
        )
        facts = Stage9BehaviorFactAdapter().normalize(
            analysis_job_id="job-mcp", analysis_run_id="run-mcp", source_video_id="video-mcp", raw_events=raw,
        ).facts
        writer.write(facts, store_id="store-1")
        adapter = ProductAnalyticsMCPTools(
            ProductAnalyticsQueryService(sessions),
            StoreContext(
                store_id="store-1",
                default_camera_id="cam02",
                default_start_at=datetime(2026, 8, 20, 21, 7),
                default_end_at=datetime(2026, 8, 20, 21, 9),
                business_date=datetime(2026, 8, 20).date(),
            ),
            product_catalog=ProductCatalog.from_mapping({
                "smart_toy_a": {"智能熊"},
                "smart_toy_b": {"智能象", "智能小象"},
            }),
        )
        self.dispatcher = ProductAnalyticsMCPToolDispatcher(adapter)
        self.server = ProductAnalyticsMCPServer(self.dispatcher, enabled=True)

    @staticmethod
    def _raw(kind, at):
        return SimpleNamespace(
            event_id=f"session-1:{kind}", event_type=kind, observed_at=at,
            camera_id="cam02", product_key="smart_toy_a", product_name="智能熊",
            product_track_token="product-1", person_track_id=1,
            person_track_token="person-1", interaction_session_id="session-1", confidence=1.0,
        )

    def test_initialize_and_list_tools(self):
        initialized = self.server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertEqual("2025-06-18", initialized["result"]["protocolVersion"])
        self.assertEqual("retailvision-product-analytics", initialized["result"]["serverInfo"]["name"])
        listed = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(
            {"rank_products", "get_product_behavior_summary", "compare_product_behavior", "recommend_hot_products", "analyze_product_trend", "detect_product_anomaly", "suggest_product_display"},
            {tool["name"] for tool in listed["result"]["tools"]},
        )

    def test_compare_schema_publishes_both_runtime_required_argument_modes(self):
        self.server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        listed = self.server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        compare = next(
            tool for tool in listed["result"]["tools"]
            if tool["name"] == "compare_product_behavior"
        )
        schema = compare["inputSchema"]

        self.assertEqual(["store_id", "product_key"], schema["required"])
        self.assertEqual(
            {
                frozenset({"second_product_key", "start_at", "end_at"}),
                frozenset({
                    "first_start_at", "first_end_at",
                    "second_start_at", "second_end_at",
                }),
            },
            {frozenset(branch["required"]) for branch in schema["anyOf"]},
        )

    def test_initialize_publishes_trusted_scope_and_grounding_policy(self):
        context = self.dispatcher.adapter.store_context
        server = ProductAnalyticsMCPServer(
            self.dispatcher,
            enabled=True,
            instructions=build_agent_instructions(
                context,
                product_aliases="智能熊=smart_toy_a,智能象=smart_toy_b",
            ),
        )
        initialized = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        instructions = initialized["result"]["instructions"]
        self.assertIn("store_id=store-1", instructions)
        self.assertIn("camera_id=cam02", instructions)
        self.assertIn("智能熊=smart_toy_a", instructions)
        self.assertIn("rank_products", instructions)
        self.assertIn("推荐重点陈列哪些商品", instructions)
        self.assertIn("商品应该怎样调整陈列", instructions)
        self.assertIn("Do not substitute one display tool", instructions)
        self.assertIn("Do not inspect files", instructions)
        self.assertIn("unique_person_track_count=匿名轨迹数", instructions)
        self.assertIn("interaction_trend=up/down/flat", instructions)
        self.assertIn("display priority", instructions)
        self.assertNotIn("recommendation code", instructions)
        self.assertIn("Never infer or restate purchase", instructions)
        self.assertIn("User wording such as 热度", instructions)
        self.assertIn("Do not mention physical placement words", instructions)
        self.assertNotIn("Prefer one product per line", instructions)
        self.assertNotIn("Even Do not mention", instructions)
        self.assertNotIn("Even when the user asks for full raw metrics", instructions)
        self.assertNotIn("say that subjective preference cannot be determined", instructions)
        self.assertNotIn("In the final answer", instructions)
        self.assertNotIn("person-1", instructions)
        tools = ProductAnalyticsMCPTools.list_tools()
        self.assertEqual(7, len(tools))
        descriptions = {tool["name"]: tool["description"] for tool in tools}
        self.assertIn("deterministic anonymous interaction metrics", descriptions["rank_products"])
        self.assertIn("which product has more interactions", descriptions["compare_product_behavior"])
        self.assertIn("ranked for display priority", descriptions["recommend_hot_products"])
        self.assertNotIn("recommendation score", descriptions["recommend_hot_products"])
        self.assertNotIn(HARD_GROUNDING_BOUNDARY, descriptions["rank_products"])
        self.assertNotIn(SAFE_DERIVED_INTERPRETATIONS, descriptions["rank_products"])

    def test_product_catalog_profile_resolves_server_side(self):
        from unittest.mock import patch

        with patch.dict(
            "os.environ",
            {"RETAILVISION_PRODUCT_CATALOG_PROFILE": "stage10_smart_toys_v1"},
            clear=False,
        ):
            aliases = configured_product_aliases()
        self.assertEqual(
            "智能熊=smart_toy_a,智能象/智能小象=smart_toy_b", aliases
        )

    def test_explicit_product_aliases_can_supply_shared_catalog(self):
        from unittest.mock import patch

        with patch.dict(
            "os.environ",
            {
                "RETAILVISION_PRODUCT_CATALOG_PROFILE": "",
                "RETAILVISION_PRODUCT_ALIASES": "商品甲=product-a,商品乙=product-b",
            },
            clear=False,
        ):
            aliases = configured_product_aliases(require_profile=True)

        self.assertEqual("商品甲=product-a,商品乙=product-b", aliases)

    def test_enabled_server_requires_product_catalog_profile_before_database(self):
        from unittest.mock import patch

        with patch.dict(
            "os.environ",
            {
                "RETAILVISION_PRODUCT_CATALOG_PROFILE": "",
                "RETAILVISION_PRODUCT_ALIASES": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "product_catalog_required"):
                build_default_server(
                    database_url="sqlite:///definitely-missing-r5-04-2.db",
                    enabled=True,
                )

    def test_no_catalog_instructions_do_not_claim_canonical_keys_are_usable(self):
        instructions = build_agent_instructions(
            self.dispatcher.adapter.store_context,
            product_aliases=None,
        )
        self.assertNotIn("use canonical product_key values only", instructions)
        self.assertIn("catalog profile", instructions)

    def test_tools_are_rejected_before_initialize(self):
        for method in ("tools/list", "tools/call"):
            response = self.server.handle({
                "jsonrpc": "2.0", "id": method, "method": method, "params": {},
            })
            self.assertEqual(-32002, response["error"]["code"])
            self.assertEqual("server_not_initialized", response["error"]["message"])

    def test_disabled_server_lists_no_tools_and_returns_feature_disabled_call_errors(self):
        server = ProductAnalyticsMCPServer(self.dispatcher)
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        listed = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        self.assertEqual([], listed["result"]["tools"])
        response = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "rank_products", "arguments": {}},
        })
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(
            "product_analytics_mcp_disabled",
            json.loads(response["result"]["content"][0]["text"])["error"],
        )

    def test_initialize_negotiates_mewcode_sdk_protocol(self):
        initialized = self.server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mewcode", "version": "1.1.3"}},
        })
        self.assertEqual("2024-11-05", initialized["result"]["protocolVersion"])

    def test_call_tool_reaches_query_service(self):
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        result = self.server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "rank_products",
                "arguments": {
                    "store_id": "store-1", "camera_id": "cam02",
                    "start_at": "2026-08-20T21:07:00", "end_at": "2026-08-20T21:09:00",
                },
            },
        })
        self.assertFalse(result["result"]["isError"])
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual("smart_toy_a", payload[0]["product_key"])

    def test_chinese_alias_resolves_and_unknown_product_is_stable_error(self):
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        alias_result = self.server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_product_behavior_summary", "arguments": {
                "store_id": "store-1", "camera_id": "cam02",
                "product_key": "智能熊", "start_at": "2026-08-20T21:07:00",
                "end_at": "2026-08-20T21:09:00",
            }},
        })
        self.assertFalse(alias_result["result"]["isError"])
        alias_payload = json.loads(alias_result["result"]["content"][0]["text"])
        self.assertEqual("smart_toy_a", alias_payload["product_key"])
        self.assertEqual(1, alias_payload["interaction_count"])

        unknown_result = self.server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_product_behavior_summary", "arguments": {
                "store_id": "store-1", "camera_id": "cam02",
                "product_key": "未知商品", "start_at": "2026-08-20T21:07:00",
                "end_at": "2026-08-20T21:09:00",
            }},
        })
        self.assertTrue(unknown_result["result"]["isError"])
        self.assertEqual(
            {"error": "product_key_unknown"},
            json.loads(unknown_result["result"]["content"][0]["text"]),
        )

    def test_call_recommend_hot_products_returns_structured_result(self):
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        result = self.server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "recommend_hot_products",
                "arguments": {
                    "store_id": "store-1", "camera_id": "cam02",
                    "start_at": "2026-08-20T21:07:00", "end_at": "2026-08-20T21:09:00",
                },
            },
        })
        self.assertFalse(result["result"]["isError"])
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertTrue(payload[0]["recommended"])
        self.assertIn("recommendation_score", payload[0])

    def test_call_compare_two_products_returns_business_fields(self):
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        result = self.server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "compare_product_behavior", "arguments": {
                "store_id": "store-1", "camera_id": "cam02", "product_key": "smart_toy_a",
                "second_product_key": "smart_toy_b", "start_at": "2026-08-20T21:07:00",
                "end_at": "2026-08-20T21:09:00",
            }},
        })
        self.assertFalse(result["result"]["isError"])
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertIn("comparison", payload)
        self.assertIn("recommendation", payload)

    def test_store_intelligence_tools_are_listed(self):
        listed = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertFalse("error" in listed)
        names = {tool["name"] for tool in self.server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })["result"]["tools"]}
        self.assertTrue({"analyze_product_trend", "detect_product_anomaly", "suggest_product_display"} <= names)

    def test_invalid_arguments_return_mcp_error_without_exit(self):
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        result = self.server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "rank_products", "arguments": {"start_at": "2026-08-20T21:07:00", "end_at": "2026-08-20T21:09:00"}},
        })
        self.assertTrue(result["result"]["isError"])
        self.assertIn("store_id_required", result["result"]["content"][0]["text"])

    def test_all_seven_tools_complete_through_protocol(self):
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        common = {
            "store_id": "store-1", "camera_id": "cam02",
            "start_at": "2026-08-20T21:07:00", "end_at": "2026-08-20T21:09:00",
        }
        calls = {
            "rank_products": common,
            "get_product_behavior_summary": {**common, "product_key": "smart_toy_a"},
            "compare_product_behavior": {
                **common, "product_key": "smart_toy_a", "second_product_key": "smart_toy_b",
            },
            "recommend_hot_products": common,
            "analyze_product_trend": {**common, "product_key": "smart_toy_a"},
            "detect_product_anomaly": common,
            "suggest_product_display": common,
        }
        for index, (name, arguments) in enumerate(calls.items(), start=2):
            with self.subTest(tool=name):
                response = self.server.handle({
                    "jsonrpc": "2.0", "id": index, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                })
                self.assertFalse(response["result"]["isError"])
                payload = response["result"]["content"][0]["text"].lower()
                for forbidden in (
                    "member_id", "member_name", "person_track_token", "face_bbox"
                ):
                    self.assertNotIn(forbidden, payload)

    def test_stdio_transport_emits_one_response_per_request(self):
        incoming = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        ]) + "\n"
        output = StringIO()
        StdioJsonRpcTransport(self.server.handle, stdin=StringIO(incoming), stdout=output, stderr=StringIO()).serve()
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([1, 2], [item["id"] for item in responses])


if __name__ == "__main__":
    unittest.main()
