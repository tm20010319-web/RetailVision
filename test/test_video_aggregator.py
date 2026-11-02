"""
VideoAggregator 单元测试。

测试覆盖：
1. (track_id, zone) 去重
2. 不同 track 同 zone 分别计数
3. zone_heatmap 构建正确性
4. hot_zones 降序排序
5. conversion_signals 提取
6. Schema 校验通过
7. 空输入边界
"""

import unittest

from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from agent.profile.video_aggregator import VideoAggregator
from agent.profile.schemas import VideoSummarySchema


class VideoAggregatorTests(unittest.TestCase):

    def _make_result(self, events, unique_tracks=0, processed_frames=0, fps=30.0):
        """构造 analyze_video() 的返回值。"""
        return {
            "total_frames": processed_frames * 3,
            "processed_frames": processed_frames,
            "fps": fps,
            "detections": [],
            "events": events,
            "zone_visits": {},
            "unique_tracks": unique_tracks,
        }

    def _make_event(self, track_id, zone, frame, timestamp=None):
        if timestamp is None:
            timestamp = frame / 30.0
        return {
            "type": "zone_visit",
            "track_id": track_id,
            "zone": zone,
            "frame": frame,
            "timestamp": timestamp,
        }

    # ------------------------------------------------------------------
    # 1. 去重：同一 track 多帧进入同一 zone 只计一次
    # ------------------------------------------------------------------

    def test_deduplicate_zone_visits_by_track_and_zone(self):
        events = [
            self._make_event(1, "entrance", 10),
            self._make_event(1, "entrance", 20),
            self._make_event(1, "entrance", 30),
            self._make_event(1, "entrance", 40),
        ]
        result = self._make_result(events, unique_tracks=1, processed_frames=4)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        # 同一 track 在同一 zone 只计 1
        self.assertEqual(summary["zone_heatmap"]["entrance"], 1)
        # hot_zones 也只 1 条
        self.assertEqual(len(summary["hot_zones"]), 1)
        self.assertEqual(summary["hot_zones"][0]["visit_count"], 1)

    # ------------------------------------------------------------------
    # 2. 不同 track 进入同一 zone 分别计数
    # ------------------------------------------------------------------

    def test_different_tracks_same_zone_counted_separately(self):
        events = [
            self._make_event(1, "entrance", 10),
            self._make_event(2, "entrance", 20),
            self._make_event(3, "entrance", 30),
        ]
        result = self._make_result(events, unique_tracks=3, processed_frames=3)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        self.assertEqual(summary["zone_heatmap"]["entrance"], 3)
        self.assertEqual(summary["hot_zones"][0]["visit_count"], 3)

    # ------------------------------------------------------------------
    # 3. zone_heatmap 构建正确性
    # ------------------------------------------------------------------

    def test_zone_heatmap_built_correctly(self):
        events = [
            self._make_event(1, "entrance", 10),
            self._make_event(1, "fitting_room", 20),
            self._make_event(2, "entrance", 30),
            self._make_event(3, "checkout", 40),
        ]
        result = self._make_result(events, unique_tracks=3, processed_frames=4)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        heatmap = summary["zone_heatmap"]
        self.assertEqual(heatmap["entrance"], 2)       # track 1 + track 2
        self.assertEqual(heatmap["fitting_room"], 1)   # track 1
        self.assertEqual(heatmap["checkout"], 1)       # track 3

    # ------------------------------------------------------------------
    # 4. hot_zones 按访问量降序排列
    # ------------------------------------------------------------------

    def test_hot_zones_sorted_by_visit_count(self):
        events = [
            self._make_event(1, "zone_c", 10),
            self._make_event(1, "zone_a", 20),
            self._make_event(2, "zone_a", 30),
            self._make_event(3, "zone_a", 40),
            self._make_event(2, "zone_b", 50),
            self._make_event(3, "zone_b", 60),
        ]
        result = self._make_result(events, unique_tracks=3, processed_frames=6)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        hot_zones = summary["hot_zones"]
        self.assertEqual(len(hot_zones), 3)
        # zone_a: 3 tracks, zone_b: 2 tracks, zone_c: 1 track
        self.assertEqual(hot_zones[0]["zone"], "zone_a")
        self.assertEqual(hot_zones[0]["visit_count"], 3)
        self.assertEqual(hot_zones[1]["zone"], "zone_b")
        self.assertEqual(hot_zones[1]["visit_count"], 2)
        self.assertEqual(hot_zones[2]["zone"], "zone_c")
        self.assertEqual(hot_zones[2]["visit_count"], 1)

    # ------------------------------------------------------------------
    # 5. conversion_signals 提取
    # ------------------------------------------------------------------

    def test_conversion_signals_extracted(self):
        events = [
            self._make_event(1, "fitting_room", 10),
            self._make_event(2, "fitting_room", 20),
            self._make_event(1, "checkout", 30),
        ]
        result = self._make_result(events, unique_tracks=2, processed_frames=3)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        signals = summary["conversion_signals"]
        self.assertEqual(signals["fitting_room_visits"], 2)
        self.assertEqual(signals["checkout_visits"], 1)
        self.assertFalse(signals["high_interest_low_checkout"])

    def test_conversion_signals_high_interest_low_checkout(self):
        """试衣间有访问但收银台无访问 → high_interest_low_checkout=True"""
        events = [
            self._make_event(1, "fitting_room", 10),
            self._make_event(2, "fitting_room", 20),
        ]
        result = self._make_result(events, unique_tracks=2, processed_frames=2)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        signals = summary["conversion_signals"]
        self.assertEqual(signals["fitting_room_visits"], 2)
        self.assertEqual(signals["checkout_visits"], 0)
        self.assertTrue(signals["high_interest_low_checkout"])

    def test_conversion_signals_chinese_keywords(self):
        """中文关键字也能匹配"""
        events = [
            self._make_event(1, "试衣间", 10),
            self._make_event(1, "收银台", 20),
        ]
        result = self._make_result(events, unique_tracks=1, processed_frames=2)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        signals = summary["conversion_signals"]
        self.assertEqual(signals["fitting_room_visits"], 1)
        self.assertEqual(signals["checkout_visits"], 1)

    # ------------------------------------------------------------------
    # 6. 输出通过 VideoSummarySchema 校验
    # ------------------------------------------------------------------

    def test_summary_passes_schema_validation(self):
        events = [
            self._make_event(1, "entrance", 10, timestamp=3600),  # 1h
            self._make_event(1, "fitting_room", 20, timestamp=3700),
            self._make_event(2, "entrance", 30, timestamp=7200),  # 2h
            self._make_event(3, "checkout", 40, timestamp=7200),
        ]
        result = self._make_result(events, unique_tracks=3, processed_frames=4)

        agg = VideoAggregator()
        summary = agg.aggregate(result, time_range="2024-01-01 10:00-12:00")

        # 不会抛异常即通过
        validated = VideoSummarySchema(**summary)
        self.assertEqual(validated.task_type, "historical_video_analysis")
        self.assertEqual(validated.time_range, "2024-01-01 10:00-12:00")
        self.assertEqual(validated.summary_metrics["estimated_unique_visitors"], 3)
        self.assertEqual(validated.summary_metrics["processed_frames"], 4)
        self.assertEqual(validated.summary_metrics["total_zone_events"], 4)

    # ------------------------------------------------------------------
    # 7. 空输入边界
    # ------------------------------------------------------------------

    def test_empty_events_returns_zero_metrics(self):
        result = self._make_result([], unique_tracks=0, processed_frames=0)

        agg = VideoAggregator()
        summary = agg.aggregate(result)

        self.assertEqual(summary["summary_metrics"]["estimated_unique_visitors"], 0)
        self.assertEqual(summary["summary_metrics"]["processed_frames"], 0)
        self.assertEqual(summary["summary_metrics"]["total_zone_events"], 0)
        self.assertEqual(summary["zone_heatmap"], {})
        self.assertEqual(summary["hot_zones"], [])
        self.assertEqual(summary["peak_hours"], [])

    def test_error_result_returns_empty_summary(self):
        """analyze_video 返回 error 时返回空摘要"""
        agg = VideoAggregator()
        summary = agg.aggregate({"error": "Cannot open video source"})

        self.assertEqual(summary["summary_metrics"]["estimated_unique_visitors"], 0)
        self.assertEqual(summary["summary_metrics"]["total_zone_events"], 0)

    # ------------------------------------------------------------------
    # 8. peak_hours 提取
    # ------------------------------------------------------------------

    def test_peak_hours_extracted_correctly(self):
        """timestamp 转小时后取 top N"""
        events = [
            self._make_event(1, "zone_a", 10, timestamp=3600 * 1),    # 1h
            self._make_event(2, "zone_a", 20, timestamp=3600 * 1),    # 1h
            self._make_event(3, "zone_a", 30, timestamp=3600 * 1),    # 1h
            self._make_event(1, "zone_b", 40, timestamp=3600 * 2),    # 2h
            self._make_event(2, "zone_b", 50, timestamp=3600 * 2),    # 2h
            self._make_event(1, "zone_c", 60, timestamp=3600 * 3),    # 3h
        ]
        result = self._make_result(events, unique_tracks=3, processed_frames=6)

        agg = VideoAggregator()
        summary = agg.aggregate(result, peak_hours_top_n=2)

        # 1h: 3 events, 2h: 2 events, 3h: 1 event → top 2 = [1, 2]
        self.assertEqual(summary["peak_hours"], [1, 2])


if __name__ == "__main__":
    unittest.main()
