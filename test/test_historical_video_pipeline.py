"""
HistoricalVideoAnalysisPipeline 集成测试。

测试覆盖：
1. 三步调用顺序（detector → aggregator → llm）
2. LLM 输出通过 InsightReportSchema 校验
3. 格式错误的 LLM 输出被拒绝
4. YOLO 检测结果不直接发给 LLM（AST 检查）
5. 空视频边界处理
6. 工厂函数验证
"""

import ast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from agent.profile.llm_service import BaseTextLLMService, MockTextLLMService
from agent.profile.schemas import InsightReportSchema, VideoSummarySchema
from agent.profile.video_aggregator import VideoAggregator
from pipelines.legacy.historical_video_pipeline import (
    HistoricalVideoAnalysisPipeline,
    create_historical_pipeline,
)


# ---------------------------------------------------------------------------
# Mock Detector — 模拟 VisionDetector
# ---------------------------------------------------------------------------

class MockDetector:
    """模拟 VisionDetector，不加载真实 YOLO 模型。"""

    def __init__(self, analyze_result=None):
        self.reset_called = False
        self.analyze_called = False
        self._result = analyze_result or {}

    def reset(self):
        self.reset_called = True

    def analyze_video(self, video_source, zone_definitions=None,
                      process_interval=3, process_every_seconds=None,
                      max_frames=None):
        self.analyze_called = True
        self._last_video_source = video_source
        self._last_zone_definitions = zone_definitions
        return self._result


# ---------------------------------------------------------------------------
# Mock LLM Service
# ---------------------------------------------------------------------------

class ValidMockLLMService(BaseTextLLMService):
    """始终返回合法报告的 Mock LLM。"""

    def generate_report(self, summary: dict) -> dict:
        return {
            "report_title": "测试报告",
            "traffic_summary": "测试客流概述",
            "area_insights": ["区域洞察1"],
            "operation_suggestions": ["运营建议1"],
            "sales_suggestions": ["导购建议1"],
        }


class MalformedMockLLMService(BaseTextLLMService):
    """返回格式错误数据的 Mock LLM。"""

    def generate_report(self, summary: dict) -> dict:
        # 缺少必填字段 report_title 和 traffic_summary
        return {
            "area_insights": ["only insights"],
        }


class TrackingMockLLMService(BaseTextLLMService):
    """追踪传入参数的 Mock LLM，用于验证只收到 summary 而非 detections。"""

    def __init__(self):
        self.received_summary = None

    def generate_report(self, summary: dict) -> dict:
        self.received_summary = summary
        return {
            "report_title": "测试报告",
            "traffic_summary": "测试客流概述",
            "area_insights": [],
            "operation_suggestions": [],
            "sales_suggestions": [],
        }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def make_analyze_result(events=None, unique_tracks=0, processed_frames=0):
    """构造 analyze_video() 的返回值。"""
    return {
        "total_frames": processed_frames * 3,
        "processed_frames": processed_frames,
        "fps": 30.0,
        "detections": [],
        "events": events or [],
        "zone_visits": {},
        "unique_tracks": unique_tracks,
    }


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class HistoricalVideoPipelineTests(unittest.TestCase):

    # ------------------------------------------------------------------
    # 1. 三步调用顺序
    # ------------------------------------------------------------------

    def test_pipeline_calls_analyze_video_then_aggregate_then_llm(self):
        events = [
            {"type": "zone_visit", "track_id": 1, "zone": "entrance",
             "frame": 10, "timestamp": 0.33},
            {"type": "zone_visit", "track_id": 1, "zone": "fitting_room",
             "frame": 20, "timestamp": 0.66},
            {"type": "zone_visit", "track_id": 2, "zone": "entrance",
             "frame": 30, "timestamp": 1.0},
        ]
        analyze_result = make_analyze_result(events, unique_tracks=2, processed_frames=3)

        detector = MockDetector(analyze_result)
        aggregator = VideoAggregator()
        llm_service = TrackingMockLLMService()

        pipeline = HistoricalVideoAnalysisPipeline(
            detector=detector,
            aggregator=aggregator,
            llm_service=llm_service,
        )

        result = pipeline.run("fake_video.mp4")

        # 验证三步都被调用
        self.assertTrue(detector.reset_called)
        self.assertTrue(detector.analyze_called)
        self.assertIsNotNone(llm_service.received_summary)

        # 验证返回结构
        self.assertIn("summary", result)
        self.assertIn("report", result)
        self.assertIn("raw_stats", result)

        # 验证 summary 包含聚合数据
        self.assertEqual(result["summary"]["summary_metrics"]["estimated_unique_visitors"], 2)
        self.assertEqual(result["summary"]["summary_metrics"]["total_zone_events"], 3)

    # ------------------------------------------------------------------
    # 2. LLM 输出通过 InsightReportSchema
    # ------------------------------------------------------------------

    def test_report_passes_insight_report_schema(self):
        analyze_result = make_analyze_result(
            [{"type": "zone_visit", "track_id": 1, "zone": "entrance",
              "frame": 10, "timestamp": 0.33}],
            unique_tracks=1, processed_frames=1,
        )

        detector = MockDetector(analyze_result)
        pipeline = HistoricalVideoAnalysisPipeline(
            detector=detector,
            aggregator=VideoAggregator(),
            llm_service=ValidMockLLMService(),
        )

        result = pipeline.run("fake_video.mp4")

        # 不会抛异常即通过
        validated = InsightReportSchema(**result["report"])
        self.assertEqual(validated.report_title, "测试报告")
        self.assertEqual(validated.traffic_summary, "测试客流概述")

    # ------------------------------------------------------------------
    # 3. 格式错误的 LLM 输出被拒绝
    # ------------------------------------------------------------------

    def test_malformed_llm_output_rejected(self):
        analyze_result = make_analyze_result(
            [{"type": "zone_visit", "track_id": 1, "zone": "entrance",
              "frame": 10, "timestamp": 0.33}],
            unique_tracks=1, processed_frames=1,
        )

        detector = MockDetector(analyze_result)
        pipeline = HistoricalVideoAnalysisPipeline(
            detector=detector,
            aggregator=VideoAggregator(),
            llm_service=MalformedMockLLMService(),
        )

        # 缺少必填字段应抛出 pydantic ValidationError
        with self.assertRaises(Exception):
            pipeline.run("fake_video.mp4")

    # ------------------------------------------------------------------
    # 4. YOLO 检测结果不直接发给 LLM（AST 检查）
    # ------------------------------------------------------------------

    def test_no_llm_called_for_detection(self):
        """AST 检查：Pipeline 源码中 llm_service.generate_report() 的参数
        只能是 summary 变量，不能是 result（detections 原始数据）。"""
        pipeline_source_path = Path(__file__).resolve().parents[1] / "pipelines" / "legacy" / "historical_video_pipeline.py"
        source = pipeline_source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        llm_calls_with_result = []

        for node in ast.walk(tree):
            # 查找 generate_report 调用
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "generate_report"):
                for arg in node.args:
                    # 如果参数是名为 "result" 的变量 → 违规
                    if isinstance(arg, ast.Name) and arg.id == "result":
                        llm_calls_with_result.append(arg.id)
                    # 如果参数是 result 的属性访问 → 也违规
                    if (isinstance(arg, ast.Attribute)
                            and isinstance(arg.value, ast.Name)
                            and arg.value.id == "result"):
                        llm_calls_with_result.append(f"result.{arg.attr}")

        self.assertEqual(
            llm_calls_with_result, [],
            "LLM generate_report() should not receive raw detection 'result' — "
            "only the aggregated 'summary'"
        )

    def test_llm_receives_summary_not_detections(self):
        """运行时验证：LLM 收到的是 summary dict，不包含 detections 列表。"""
        events = [
            {"type": "zone_visit", "track_id": 1, "zone": "entrance",
             "frame": 10, "timestamp": 0.33},
        ]
        analyze_result = make_analyze_result(events, unique_tracks=1, processed_frames=1)

        detector = MockDetector(analyze_result)
        llm_service = TrackingMockLLMService()

        pipeline = HistoricalVideoAnalysisPipeline(
            detector=detector,
            aggregator=VideoAggregator(),
            llm_service=llm_service,
        )

        pipeline.run("fake_video.mp4")

        received = llm_service.received_summary
        self.assertIsNotNone(received)
        # summary 不应包含 detections / events / zone_visits 等原始字段
        self.assertNotIn("detections", received)
        self.assertNotIn("events", received)
        # summary 应包含聚合后的字段
        self.assertIn("summary_metrics", received)
        self.assertIn("zone_heatmap", received)

    # ------------------------------------------------------------------
    # 5. 空视频边界处理
    # ------------------------------------------------------------------

    def test_empty_video_returns_graceful_result(self):
        """analyze_video 返回 error 时的边界处理。"""
        detector = MockDetector({"error": "Cannot open video source"})
        pipeline = HistoricalVideoAnalysisPipeline(
            detector=detector,
            aggregator=VideoAggregator(),
            llm_service=ValidMockLLMService(),
        )

        result = pipeline.run("nonexistent.mp4")

        # 应返回空摘要而非抛异常
        self.assertIn("summary", result)
        self.assertIn("report", result)
        self.assertIn("raw_stats", result)
        self.assertEqual(result["summary"]["summary_metrics"]["estimated_unique_visitors"], 0)
        self.assertIn("error", result["raw_stats"])

    def test_empty_events_video_returns_zero_metrics(self):
        """视频可打开但无检测到任何事件。"""
        analyze_result = make_analyze_result([], unique_tracks=0, processed_frames=10)
        detector = MockDetector(analyze_result)
        pipeline = HistoricalVideoAnalysisPipeline(
            detector=detector,
            aggregator=VideoAggregator(),
            llm_service=ValidMockLLMService(),
        )

        result = pipeline.run("empty_video.mp4")

        self.assertEqual(result["summary"]["summary_metrics"]["estimated_unique_visitors"], 0)
        self.assertEqual(result["summary"]["summary_metrics"]["total_zone_events"], 0)
        self.assertEqual(result["raw_stats"]["total_events"], 0)

    # ------------------------------------------------------------------
    # 6. 工厂函数验证
    # ------------------------------------------------------------------

    def test_factory_creates_pipeline_correctly(self):
        """工厂函数创建的 Pipeline 包含正确的组件类型。"""
        with patch("pipelines.legacy.historical_video_pipeline.VisionDetector") as MockVD:
            mock_detector_instance = MagicMock()
            MockVD.return_value = mock_detector_instance

            pipeline = create_historical_pipeline(
                model_path="fake_model.pt",
                device="cpu",
                conf_threshold=0.6,
                use_mock_llm=True,
            )

            # 验证 detector 被正确初始化
            MockVD.assert_called_once_with(
                model_path="fake_model.pt",
                device="cpu",
                conf_threshold=0.6,
            )
            mock_detector_instance.initialize.assert_called_once()

            # 验证返回的 pipeline 类型
            self.assertIsInstance(pipeline, HistoricalVideoAnalysisPipeline)
            self.assertIsInstance(pipeline.aggregator, VideoAggregator)
            self.assertIsInstance(pipeline.llm_service, MockTextLLMService)

            # 验证默认 zone_definitions 被设置
            self.assertIsNotNone(pipeline.zone_definitions)
            self.assertIn("zone_left", pipeline.zone_definitions)

    def test_factory_with_custom_zone_definitions(self):
        """工厂函数支持自定义 zone_definitions。"""
        custom_zones = {
            "entrance": [(0, 0), (640, 0), (640, 720), (0, 720)],
            "exit": [(640, 0), (1280, 0), (1280, 720), (640, 720)],
        }

        with patch("pipelines.legacy.historical_video_pipeline.VisionDetector"):
            pipeline = create_historical_pipeline(
                use_mock_llm=True,
                zone_definitions=custom_zones,
            )

            self.assertEqual(pipeline.zone_definitions, custom_zones)


if __name__ == "__main__":
    unittest.main()
