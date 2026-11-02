from datetime import datetime
from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.models import Base  # noqa: E402
from agent.offline.offline_behavior import Stage9BehaviorFactAdapter  # noqa: E402
from agent.analytics.product_interaction_analytics import ProductInteractionFactWriter, ProductAnalyticsQueryService  # noqa: E402
from tools.harness.product_analytics.descriptions import select_product_analytics_tool  # noqa: E402
from agent.analytics.product_interaction_mcp import (  # noqa: E402
    ProductAnalyticsMCPTools,
    create_product_analytics_mcp_tools,
)
from agent.store_context import StoreContext  # noqa: E402
from agent.analytics.product_catalog import ProductCatalog  # noqa: E402


class ProductInteractionMCPTests(unittest.TestCase):
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
        self.tools = ProductAnalyticsMCPTools(
            ProductAnalyticsQueryService(sessions),
            StoreContext(
                store_id="store-1", default_camera_id="cam-1",
                default_start_at=datetime(2026, 8, 20, 3),
                default_end_at=datetime(2026, 8, 20, 5),
                business_date=datetime(2026, 8, 20).date(),
            ),
            product_catalog=ProductCatalog.from_mapping({
                "toy-a": {"Toy A", "智能熊"}, "toy-b": {"Toy B", "智能象"},
            }),
        )

    @staticmethod
    def _raw(kind, session, at):
        return SimpleNamespace(
            event_id=f"{session}:{kind}", event_type=kind, observed_at=at,
            camera_id="cam-1", product_key="toy-a", product_name="Toy A",
            product_track_token="product-1", person_track_id=1,
            person_track_token="person-1", interaction_session_id=session,
            confidence=1.0,
        )

    def test_rank_tool_delegates_and_returns_analytics(self):
        result = self.tools.call("rank_products", {
            "store_id": "store-1", "start_at": "2026-08-20T03:00:00",
            "end_at": "2026-08-20T05:00:00", "limit": 5,
        })
        self.assertEqual(1, result[0]["pickup_count"])
        self.assertEqual("toy-a", result[0]["product_key"])

    def test_rank_tool_projects_answer_fields_only_when_requested(self):
        arguments = {
            "store_id": "store-1",
            "start_at": "2026-08-20T03:00:00",
            "end_at": "2026-08-20T05:00:00",
        }

        detailed = self.tools.call("rank_products", arguments)
        normal = self.tools.call(
            "rank_products", {**arguments, "response_detail": "normal"},
        )
        concise = self.tools.call(
            "rank_products", {**arguments, "response_detail": "concise"},
        )

        self.assertIn("max_holding_seconds", detailed[0])
        self.assertEqual(
            {
                "rank", "product_key", "product_name", "pickup_count",
                "completed_interaction_count", "total_holding_seconds",
                "average_holding_seconds",
            },
            set(normal[0]),
        )
        self.assertEqual(
            {"rank", "product_key", "product_name", "pickup_count"},
            set(concise[0]),
        )

    def test_rank_tool_rejects_unknown_response_detail(self):
        with self.assertRaisesRegex(ValueError, "^response_detail_invalid$"):
            self.tools.call("rank_products", {
                "store_id": "store-1",
                "start_at": "2026-08-20T03:00:00",
                "end_at": "2026-08-20T05:00:00",
                "response_detail": "verbose",
            })

    def test_tools_reject_timezone_and_unknown_names(self):
        with self.assertRaisesRegex(ValueError, "must_be_naive"):
            self.tools.call("rank_products", {
                "store_id": "store-1", "start_at": "2026-08-20T03:00:00Z",
                "end_at": "2026-08-20T05:00:00", 
            })
        with self.assertRaisesRegex(ValueError, "unknown_product_analytics_tool"):
            self.tools.call("nope", {})

    def test_agent_intent_selection(self):
        self.assertEqual("rank_products", select_product_analytics_tool("最近哪些商品最火？"))
        self.assertEqual("compare_product_behavior", select_product_analytics_tool("比较今天和昨天"))

    def test_tool_catalog_exposes_product_analytics_tools(self):
        self.assertEqual(
            {"rank_products", "get_product_behavior_summary", "compare_product_behavior", "recommend_hot_products", "analyze_product_trend", "detect_product_anomaly", "suggest_product_display"},
            {item["name"] for item in self.tools.list_tools()},
        )

    def test_summary_and_compare_tools_delegate(self):
        summary = self.tools.call("get_product_behavior_summary", {
            "store_id": "store-1", "product_key": "toy-a",
            "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00",
        })
        self.assertEqual(1, summary["interaction_count"])
        comparison = self.tools.call("compare_product_behavior", {
            "store_id": "store-1", "product_key": "toy-a",
            "first_start_at": "2026-08-20T03:00:00", "first_end_at": "2026-08-20T04:00:00",
            "second_start_at": "2026-08-20T04:00:00", "second_end_at": "2026-08-20T05:00:00",
        })
        self.assertEqual(1, comparison["interaction_change"])

    def test_product_catalog_resolves_alias_and_canonical_key(self):
        canonical = self.tools.call("get_product_behavior_summary", {
            "store_id": "store-1", "product_key": "toy-a",
            "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00",
        })
        alias = self.tools.call("get_product_behavior_summary", {
            "store_id": "store-1", "product_key": "智能熊",
            "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00",
        })
        self.assertEqual("toy-a", canonical["product_key"])
        self.assertEqual(canonical, alias)

        comparison = self.tools.call("compare_product_behavior", {
            "store_id": "store-1", "product_key": "智能熊",
            "second_product_key": "智能象",
            "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00",
        })
        self.assertEqual("toy-a", comparison["comparison"]["first"]["product_key"])
        self.assertEqual("toy-b", comparison["comparison"]["second"]["product_key"])

    def test_unknown_product_key_fails_closed_instead_of_returning_zero_summary(self):
        with self.assertRaisesRegex(ValueError, "^product_key_unknown$"):
            self.tools.call("get_product_behavior_summary", {
                "store_id": "store-1", "product_key": "未知商品",
                "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00",
            })

    def test_hot_product_recommendation_returns_structured_reason(self):
        result = self.tools.call("recommend_hot_products", {
            "store_id": "store-1", "start_at": "2026-08-20T03:00:00",
            "end_at": "2026-08-20T05:00:00",
        })
        self.assertEqual("toy-a", result[0]["product_key"])
        self.assertTrue(result[0]["recommended"])
        self.assertIn("pickup_interest", result[0]["recommendation_reasons"])

    def test_compare_tool_supports_two_products(self):
        result = self.tools.call("compare_product_behavior", {
            "store_id": "store-1", "product_key": "toy-a", "second_product_key": "toy-b",
            "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00",
        })
        self.assertIn("comparison", result)
        self.assertIn("reason", result)
        self.assertIn("recommendation", result)

    def test_store_context_resolves_omitted_scope_for_business_tools(self):
        result = self.tools.call("recommend_hot_products", {})
        self.assertEqual("toy-a", result[0]["product_key"])

    def test_store_context_rejects_cross_store_and_cross_camera_queries(self):
        for arguments, error in (
            ({"store_id": "store-other"}, "store_context_store_scope_mismatch"),
            ({"camera_id": "cam-other"}, "store_context_camera_scope_mismatch"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.tools.call("recommend_hot_products", arguments)

    def test_store_context_rejects_default_window_over_31_days(self):
        with self.assertRaisesRegex(
            ValueError, "query_time_range_exceeds_maximum"
        ):
            StoreContext(
                store_id="store-1",
                default_camera_id="cam-1",
                default_start_at=datetime(2026, 1, 1),
                default_end_at=datetime(2026, 2, 2),
                business_date=datetime(2026, 2, 2).date(),
            )

    def test_tool_results_do_not_expose_identity_or_raw_track_fields(self):
        result = self.tools.call("get_product_behavior_summary", {
            "store_id": "store-1", "product_key": "toy-a",
            "start_at": "2026-08-20T03:00:00", "end_at": "2026-08-20T05:00:00",
        })
        serialized = str(result)
        for forbidden in (
            "member_id", "member_name", "identity", "face_bbox", "person_track_token"
        ):
            self.assertNotIn(forbidden, serialized.lower())

    def test_tool_rejects_time_ranges_over_maximum(self):
        with self.assertRaisesRegex(
            ValueError, "query_time_range_exceeds_maximum"
        ):
            self.tools.call("rank_products", {
                "store_id": "store-1", "camera_id": "cam-1",
                "start_at": "2026-01-01T00:00:00",
                "end_at": "2026-02-02T00:00:00",
            })

    def test_trend_anomaly_and_display_tools_return_structured_facts(self):
        trend = self.tools.call("analyze_product_trend", {"product_key": "toy-a"})
        self.assertEqual("up", trend["interaction_trend"])
        self.assertIn("pickup_change", trend)
        anomalies = self.tools.call("detect_product_anomaly", {})
        self.assertIsInstance(anomalies, list)
        display = self.tools.call("suggest_product_display", {})
        self.assertEqual("toy-a", display[0]["product_key"])
        self.assertIn("display_priority", display[0])


if __name__ == "__main__":
    unittest.main()
