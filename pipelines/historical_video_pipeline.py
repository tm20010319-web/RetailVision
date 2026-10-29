"""
HistoricalVideoAnalysisPipeline：历史视频经营分析管线（P0 核心）。

职责：
- 驱动 VisionDetector.analyze_video() 完成视频检测。
- 调用 VideoAggregator 聚合为业务摘要。
- 调用 LLMService 生成经营分析报告。
- 通过 Pydantic Schema 校验每一步输出。

约束：
- Pipeline 层不做视频读取或 YOLO 推理，全部委托给 detector。
- Pipeline 只做 编排 → 聚合 → LLM → 校验。
- YOLO 检测结果不直接发给 LLM（先聚合再发文本摘要）。
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

from agent.detector import VisionDetector
from agent.llm_service import BaseTextLLMService, LLMServiceFactory
from agent.schemas import InsightReportSchema, VideoSummarySchema
from agent.video_aggregator import VideoAggregator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认区域定义（左右半屏 + 中间通道）
# ---------------------------------------------------------------------------

DEFAULT_ZONE_DEFINITIONS: Dict[str, List[Tuple[int, int]]] = {
    "zone_left": [(0, 0), (426, 0), (426, 720), (0, 720)],
    "zone_center": [(426, 0), (852, 0), (852, 720), (426, 720)],
    "zone_right": [(852, 0), (1280, 0), (1280, 720), (852, 720)],
}


class HistoricalVideoAnalysisPipeline:
    """历史视频经营分析管线。

    编排流程：
        detector.reset()
        → detector.analyze_video()
        → aggregator.aggregate()
        → VideoSummarySchema 校验
        → llm_service.generate_report()
        → InsightReportSchema 校验
        → return result
    """

    def __init__(
        self,
        detector: VisionDetector,
        aggregator: VideoAggregator,
        llm_service: BaseTextLLMService,
        zone_definitions: Optional[Dict[str, List[Tuple[int, int]]]] = None,
    ):
        self.detector = detector
        self.aggregator = aggregator
        self.llm_service = llm_service
        self.zone_definitions = zone_definitions or DEFAULT_ZONE_DEFINITIONS

    def run(
        self,
        video_source: str,
        process_every_seconds: float = 3.0,
        on_progress: Optional[Callable[[int, int], None]] = None,
        time_range: Optional[str] = None,
    ) -> dict:
        """执行历史视频分析。

        Args:
            video_source: 视频文件路径。
            process_every_seconds: 采样间隔（秒），传给 analyze_video()。
            on_progress: 进度回调 (processed_frames, total_frames)。
            time_range: 可选的时段描述，写入 summary。

        Returns:
            {
                "summary": dict,       # 符合 VideoSummarySchema
                "report": dict,        # 符合 InsightReportSchema
                "raw_stats": dict,     # 原始检测统计
            }
        """
        # 1. 清除上次追踪状态
        self.detector.reset()

        # 2. 视频检测
        logger.info("Starting video analysis: %s", video_source)
        result = self.detector.analyze_video(
            video_source,
            zone_definitions=self.zone_definitions,
            process_every_seconds=process_every_seconds,
        )

        # 边界：视频无法打开
        if not result or "error" in result:
            error_msg = result.get("error", "unknown error") if result else "empty result"
            logger.error("Video analysis failed: %s", error_msg)
            empty_summary = self.aggregator.aggregate({}, time_range=time_range)
            empty_report = self.llm_service.generate_report(empty_summary)
            return {
                "summary": empty_summary,
                "report": empty_report,
                "raw_stats": {"error": error_msg},
            }

        # 进度回调
        if on_progress:
            processed = result.get("processed_frames", 0)
            total = result.get("total_frames", 0)
            on_progress(processed, total)

        # 3. 聚合
        summary = self.aggregator.aggregate(result, time_range=time_range)

        # 4. Schema 校验（aggregator 内部已做，这里二次确认）
        VideoSummarySchema(**summary)

        # 5. LLM 生成报告
        report = self.llm_service.generate_report(summary)

        # 6. Schema 校验 LLM 输出
        InsightReportSchema(**report)

        # 7. 组装返回
        raw_stats = {
            "total_frames": result.get("total_frames", 0),
            "processed_frames": result.get("processed_frames", 0),
            "fps": result.get("fps", 0.0),
            "unique_tracks": result.get("unique_tracks", 0),
            "total_events": len(result.get("events", [])),
            "zone_visits_raw": result.get("zone_visits", {}),
        }

        logger.info(
            "Historical video analysis complete: %d visitors, %d events",
            raw_stats["unique_tracks"],
            raw_stats["total_events"],
        )

        return {
            "summary": summary,
            "report": report,
            "raw_stats": raw_stats,
        }


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------

def create_historical_pipeline(
    model_path: Optional[str] = None,
    device: str = "cpu",
    conf_threshold: float = 0.5,
    use_mock_llm: bool = True,
    zone_definitions: Optional[Dict[str, List[Tuple[int, int]]]] = None,
) -> HistoricalVideoAnalysisPipeline:
    """创建 HistoricalVideoAnalysisPipeline 的便捷工厂。

    Args:
        model_path: YOLO 模型路径（None 则用 config 默认值）。
        device: 推理设备（"cpu" 或 "cuda"）。
        conf_threshold: 检测置信度阈值。
        use_mock_llm: True 使用 MockTextLLMService（不发起 HTTP 请求）。
        zone_definitions: 区域定义（None 则用默认左右三等分）。

    Returns:
        配置好的 HistoricalVideoAnalysisPipeline 实例。
    """
    detector = VisionDetector(
        model_path=model_path,
        device=device,
        conf_threshold=conf_threshold,
    )
    detector.initialize()

    aggregator = VideoAggregator()
    llm_service = LLMServiceFactory.create_text_llm(use_mock=use_mock_llm)

    return HistoricalVideoAnalysisPipeline(
        detector=detector,
        aggregator=aggregator,
        llm_service=llm_service,
        zone_definitions=zone_definitions,
    )
