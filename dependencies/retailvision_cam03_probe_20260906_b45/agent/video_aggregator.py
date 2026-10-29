"""
检测结果聚合器：将 VisionDetector.analyze_video() 的原始输出
转换为业务摘要 dict（符合 VideoSummarySchema）。

核心设计：
- 从 events 列表按 (track_id, zone) 去重重建 zone_heatmap，
  不依赖 zone_visits（按帧计数，未去重）。
- 不直接消费 detections 列表中的 Detection 对象。
"""

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from agent.schemas import VideoSummarySchema

logger = logging.getLogger(__name__)


class VideoAggregator:
    """检测结果 → 业务摘要聚合器。"""

    # 区域名称中用于识别转化信号的关键字
    FITTING_ROOM_KEYWORDS = ("fitting", "试衣", "试穿")
    CHECKOUT_KEYWORDS = ("checkout", "收银", "结账", "买单")

    def aggregate(
        self,
        analyze_result: dict,
        time_range: Optional[str] = None,
        peak_hours_top_n: int = 3,
    ) -> dict:
        """聚合 analyze_video() 的返回值为业务摘要。

        Args:
            analyze_result: VisionDetector.analyze_video() 的返回 dict。
            time_range: 可选的时段描述（如 "2024-01-01 10:00-12:00"）。
            peak_hours_top_n: 高峰时段取前 N 个。

        Returns:
            符合 VideoSummarySchema 的 dict。
        """
        if not analyze_result or "error" in analyze_result:
            return self._empty_summary(time_range)

        events: List[Dict[str, Any]] = analyze_result.get("events", [])
        unique_tracks: int = analyze_result.get("unique_tracks", 0)
        processed_frames: int = analyze_result.get("processed_frames", 0)

        # 1. 按 (track_id, zone) 去重
        unique_zone_visits: set[tuple] = set()
        for ev in events:
            track_id = ev.get("track_id")
            zone = ev.get("zone")
            if track_id is not None and zone is not None:
                unique_zone_visits.add((track_id, zone))

        # 2. 构建去重后的 zone_heatmap（每个区域的独立 track 数）
        zone_heatmap: Dict[str, int] = {}
        for _track_id, zone in unique_zone_visits:
            zone_heatmap[zone] = zone_heatmap.get(zone, 0) + 1

        # 3. hot_zones — 按访问量降序排列
        hot_zones: List[Dict[str, Any]] = [
            {"zone": zone, "visit_count": count}
            for zone, count in sorted(
                zone_heatmap.items(), key=lambda x: x[1], reverse=True
            )
        ]

        # 4. conversion_signals — 根据 zone 名称匹配
        conversion_signals = self._extract_conversion_signals(
            zone_heatmap, unique_zone_visits
        )

        # 5. peak_hours — 按 timestamp 提取小时分布
        peak_hours = self._extract_peak_hours(events, peak_hours_top_n)

        # 6. 组装 summary
        summary: Dict[str, Any] = {
            "task_type": "historical_video_analysis",
            "time_range": time_range,
            "summary_metrics": {
                "estimated_unique_visitors": unique_tracks,
                "processed_frames": processed_frames,
                "total_zone_events": len(events),
            },
            "zone_heatmap": zone_heatmap,
            "hot_zones": hot_zones,
            "conversion_signals": conversion_signals,
            "peak_hours": peak_hours,
        }

        # Schema 校验
        validated = VideoSummarySchema(**summary)
        logger.info(
            "VideoAggregator: %d unique visitors, %d zone events, %d zones",
            unique_tracks,
            len(events),
            len(zone_heatmap),
        )
        return validated.model_dump()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _extract_conversion_signals(
        self,
        zone_heatmap: Dict[str, int],
        unique_zone_visits: set[tuple],
    ) -> dict:
        """根据区域名称关键字提取转化信号。"""
        fitting_room_visits = 0
        checkout_visits = 0

        for zone, count in zone_heatmap.items():
            zone_lower = zone.lower()
            if any(kw in zone_lower for kw in self.FITTING_ROOM_KEYWORDS):
                fitting_room_visits += count
            if any(kw in zone_lower for kw in self.CHECKOUT_KEYWORDS):
                checkout_visits += count

        high_interest_low_checkout = (
            fitting_room_visits > 0 and checkout_visits == 0
        )

        return {
            "fitting_room_visits": fitting_room_visits,
            "checkout_visits": checkout_visits,
            "high_interest_low_checkout": high_interest_low_checkout,
        }

    def _extract_peak_hours(
        self, events: List[Dict[str, Any]], top_n: int
    ) -> List[int]:
        """从 events 的 timestamp 提取小时分布，返回访问量最高的前 N 个小时。"""
        hour_counter: Counter = Counter()
        for ev in events:
            timestamp = ev.get("timestamp")
            if timestamp is not None and timestamp >= 0:
                hour = int(timestamp // 3600) % 24
                hour_counter[hour] += 1

        if not hour_counter:
            return []

        # 按访问量降序，取前 top_n
        return [
            hour
            for hour, _ in hour_counter.most_common(top_n)
        ]

    def _empty_summary(self, time_range: Optional[str] = None) -> dict:
        """空输入的默认摘要。"""
        summary = {
            "task_type": "historical_video_analysis",
            "time_range": time_range,
            "summary_metrics": {
                "estimated_unique_visitors": 0,
                "processed_frames": 0,
                "total_zone_events": 0,
            },
            "zone_heatmap": {},
            "hot_zones": [],
            "conversion_signals": {
                "fitting_room_visits": 0,
                "checkout_visits": 0,
                "high_interest_low_checkout": False,
            },
            "peak_hours": [],
        }
        validated = VideoSummarySchema(**summary)
        return validated.model_dump()
