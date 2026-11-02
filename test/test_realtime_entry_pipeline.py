from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from agent.legacy.detector import Detection
from pipelines.legacy.realtime_entry_pipeline import RealtimeEntryPipeline, create_realtime_pipeline


class FakeDetector:
    def __init__(self) -> None:
        self.reset_called = False

    def reset(self) -> None:
        self.reset_called = True


class RealtimeEntryPipelineTests(unittest.TestCase):
    def test_confirmed_track_emits_new_customer_once(self) -> None:
        detector = FakeDetector()
        pipeline = RealtimeEntryPipeline(detector=detector, stability_threshold=3)
        frame = object()

        detections = [Detection([0, 0, 10, 10], 0.70, 0, "person", track_id=7)]

        self.assertEqual(pipeline._update_tracks(detections, 1), [])
        self.assertEqual(pipeline._update_tracks(detections, 2), [])

        new_entries = pipeline._update_tracks(detections, 3)

        self.assertEqual(len(new_entries), 1)
        self.assertEqual(new_entries[0][0], 7)
        self.assertEqual(new_entries[0][1]["track_id"], 7)
        self.assertEqual(new_entries[0][1]["confidence"], 0.70)

        self.assertEqual(pipeline._update_tracks(detections, 4), [])

    def test_best_detection_uses_highest_confidence_before_confirmation(self) -> None:
        detector = FakeDetector()
        pipeline = RealtimeEntryPipeline(detector=detector, stability_threshold=3)
        frame = object()

        pipeline._update_tracks(
            [Detection([0, 0, 10, 10], 0.50, 0, "person", track_id=3)], 1,
        )
        pipeline._update_tracks(
            [Detection([0, 0, 10, 10], 0.92, 0, "person", track_id=3)], 2,
        )
        new_entries = pipeline._update_tracks(
            [Detection([0, 0, 10, 10], 0.70, 0, "person", track_id=3)], 3,
        )

        self.assertEqual(new_entries[0][1]["confidence"], 0.92)

    def test_missing_track_resets_consecutive_count_and_expires(self) -> None:
        detector = FakeDetector()
        pipeline = RealtimeEntryPipeline(
            detector=detector,
            stability_threshold=3,
            track_lost_timeout=10.0,
        )
        frame = object()

        with patch(
            "pipelines.legacy.realtime_entry_pipeline.time.time",
            side_effect=[100.0, 100.0, 101.0, 112.0],
        ):
            pipeline._update_tracks(
                [Detection([0, 0, 10, 10], 0.80, 0, "person", track_id=5)], 1,
            )
            pipeline._update_tracks([], 2)
            pipeline._update_tracks([], 3)

        self.assertNotIn(5, pipeline._track_states)

    def test_reset_clears_pipeline_and_detector_state(self) -> None:
        detector = FakeDetector()
        pipeline = RealtimeEntryPipeline(detector=detector)
        pipeline._track_states[1] = object()

        pipeline.reset()

        self.assertEqual(pipeline._track_states, {})
        self.assertTrue(detector.reset_called)

    def test_factory_initializes_detector_and_pipeline(self) -> None:
        initialized = []

        class FakeVisionDetector:
            def __init__(self, model_path, device, conf_threshold):
                self.model_path = model_path
                self.device = device
                self.conf_threshold = conf_threshold

            def initialize(self):
                initialized.append(self.model_path)

        callback = lambda track_id, detection, frame: None

        with patch("pipelines.legacy.realtime_entry_pipeline.VisionDetector", FakeVisionDetector):
            pipeline = create_realtime_pipeline(
                model_path="model.pt",
                device="cpu",
                conf_threshold=0.6,
                stability_threshold=4,
                process_every_seconds=0.25,
                on_new_customer=callback,
            )

        self.assertEqual(initialized, ["model.pt"])
        self.assertEqual(pipeline.detector.model_path, "model.pt")
        self.assertEqual(pipeline.detector.conf_threshold, 0.6)
        self.assertEqual(pipeline.stability_threshold, 4)
        self.assertEqual(pipeline.process_every_seconds, 0.25)
        self.assertIs(pipeline.on_new_customer, callback)


# -----------------------------------------------------------------------
# Mock helpers for process_frame tests
# -----------------------------------------------------------------------

class MockFrameDetector:
    """Mock detector that returns configurable detections from process_frame()."""

    def __init__(self, detections=None):
        self._detections = detections or []
        self.reset_called = False

    def process_frame(self, frame):
        return list(self._detections)

    def reset(self):
        self.reset_called = True

    def initialize(self):
        pass


class MockFaceRecognizer:
    """Mock face recognizer for testing."""

    def __init__(self, return_value=None):
        self.return_value = return_value
        self.call_count = 0

    def recognize_face(self, image, allow_fallback=False):
        self.call_count += 1
        return self.return_value


def _make_detection(track_id=1, confidence=0.9, bbox=None):
    if bbox is None:
        bbox = [100, 50, 300, 400]
    return Detection(bbox, confidence, 0, "person", track_id=track_id)


def _make_dummy_frame(h=480, w=640):
    import numpy as np
    return np.zeros((h, w, 3), dtype=np.uint8)


# -----------------------------------------------------------------------
# process_frame tests
# -----------------------------------------------------------------------

class ProcessFrameTests(unittest.TestCase):
    """测试 process_frame 公开接口。"""

    # -- 1. process_frame 单帧确认会员 --

    def test_process_frame_confirms_member(self):
        """有 FaceRecognizer 且匹配成功 → entry_type='member'。"""
        member_result = {"member_id": "M001", "confidence": 0.92}
        mock_fr = MockFaceRecognizer(return_value=member_result)
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=1)])

        member_entries = []
        pipeline = RealtimeEntryPipeline(
            detector=mock_det,
            face_recognizer=mock_fr,
            stability_threshold=1,
            on_member_entry=lambda s: member_entries.append(s),
        )

        result = pipeline.process_frame(_make_dummy_frame())

        self.assertEqual(len(result["confirmed_entries"]), 1)
        entry = result["confirmed_entries"][0]
        self.assertEqual(entry["entry_type"], "member")
        self.assertEqual(entry["track_id"], 1)
        self.assertIsNotNone(entry["session"])
        self.assertEqual(entry["session"]["member_id"], "M001")
        self.assertIsNone(entry["reason"])
        self.assertEqual(len(member_entries), 1)

    # -- 2. process_frame 单帧确认匿名访客 --

    def test_process_frame_confirms_anonymous(self):
        """有 FaceRecognizer 但匹配失败 → entry_type='anonymous'。"""
        mock_fr = MockFaceRecognizer(return_value=None)
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=2)])

        anonymous_visits = []
        pipeline = RealtimeEntryPipeline(
            detector=mock_det,
            face_recognizer=mock_fr,
            stability_threshold=1,
            on_anonymous_visit=lambda tid, d, r: anonymous_visits.append((tid, r)),
        )

        result = pipeline.process_frame(_make_dummy_frame())

        self.assertEqual(len(result["confirmed_entries"]), 1)
        entry = result["confirmed_entries"][0]
        self.assertEqual(entry["entry_type"], "anonymous")
        self.assertIsNone(entry["session"])
        self.assertEqual(entry["reason"], "face_not_matched")
        self.assertEqual(len(anonymous_visits), 1)

    # -- 3. process_frame 无 FaceRecognizer (Phase 1 兼容) --

    def test_process_frame_without_face_recognizer(self):
        """无 FaceRecognizer → 触发 on_new_customer，entry_type='anonymous'。"""
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=3)])
        new_customers = []

        pipeline = RealtimeEntryPipeline(
            detector=mock_det,
            face_recognizer=None,
            stability_threshold=1,
            on_new_customer=lambda tid, d, f: new_customers.append(tid),
        )

        result = pipeline.process_frame(_make_dummy_frame())

        self.assertEqual(len(result["confirmed_entries"]), 1)
        entry = result["confirmed_entries"][0]
        self.assertEqual(entry["entry_type"], "anonymous")
        self.assertIsNone(entry["session"])
        self.assertEqual(entry["reason"], "face_recognizer_unavailable")
        self.assertEqual(len(new_customers), 1)
        self.assertEqual(new_customers[0], 3)

    # -- 4. process_frame 返回结构验证 --

    def test_process_frame_returns_structured_dict(self):
        """验证返回 dict 包含所有必需字段。"""
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=1)])
        pipeline = RealtimeEntryPipeline(
            detector=mock_det, stability_threshold=1,
        )

        result = pipeline.process_frame(_make_dummy_frame())

        self.assertIn("confirmed_entries", result)
        self.assertIn("detections", result)
        self.assertIn("active_tracks", result)
        self.assertIsInstance(result["detections"], list)
        self.assertIsInstance(result["active_tracks"], int)

        # detections 应为 dict 列表（to_dict 后）
        if result["detections"]:
            self.assertIsInstance(result["detections"][0], dict)
            self.assertIn("bbox", result["detections"][0])
            self.assertIn("track_id", result["detections"][0])

    # -- 5. run() 复用 process_frame --

    def test_run_uses_process_frame(self):
        """验证 run() 内部调用 process_frame()。"""
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=1)])
        pipeline = RealtimeEntryPipeline(
            detector=mock_det, stability_threshold=1,
            process_every_seconds=0,
        )

        call_count = 0
        original_pf = pipeline.process_frame

        def counting_pf(frame):
            nonlocal call_count
            call_count += 1
            return original_pf(frame)

        pipeline.process_frame = counting_pf

        # 使用 Mock 视频源
        with patch("pipelines.legacy.realtime_entry_pipeline.open_video_capture") as mock_open:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.get.return_value = 30.0
            mock_cap.read.side_effect = [(True, _make_dummy_frame())] * 3 + [(False, None)]
            mock_open.return_value = mock_cap

            summary = pipeline.run("fake_video.mp4")

        self.assertGreater(call_count, 0)
        self.assertIn("total_frames_processed", summary)

    # -- 6. 非会员不触发 LLM (AST 检查) --

    def test_no_llm_imports_in_pipeline(self):
        """AST 检查：realtime_entry_pipeline.py 无 LLM 相关导入。"""
        source_path = Path(__file__).resolve().parents[1] / "pipelines" / "legacy" / "realtime_entry_pipeline.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)

        llm_patterns = ["openai", "anthropic", "langchain", "langgraph", "ollama", "gpt", "claude"]
        for imp in imports:
            for pattern in llm_patterns:
                self.assertNotIn(
                    pattern, imp.lower(),
                    f"LLM-related import found: {imp}",
                )

        # 不含 LLM API 端点
        self.assertNotIn("api/generate", source.lower())
        self.assertNotIn("chat/completions", source.lower())

    # -- 7. fallback 默认被阻断 --

    def test_fallback_blocked_in_process_frame(self):
        """is_fallback=True 默认被阻断 → entry_type='anonymous'。"""
        fallback_result = {"member_id": "test_001", "confidence": 0.85, "is_fallback": True}
        mock_fr = MockFaceRecognizer(return_value=fallback_result)
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=1)])

        pipeline = RealtimeEntryPipeline(
            detector=mock_det,
            face_recognizer=mock_fr,
            stability_threshold=1,
            allow_fallback_match=False,
        )

        result = pipeline.process_frame(_make_dummy_frame())

        entry = result["confirmed_entries"][0]
        self.assertEqual(entry["entry_type"], "anonymous")
        self.assertIsNone(entry["session"])
        self.assertEqual(entry["reason"], "face_not_matched")

    def test_fallback_allowed_when_enabled(self):
        """allow_fallback_match=True → fallback 被接受。"""
        fallback_result = {"member_id": "test_001", "confidence": 0.85, "is_fallback": True}
        mock_fr = MockFaceRecognizer(return_value=fallback_result)
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=1)])

        pipeline = RealtimeEntryPipeline(
            detector=mock_det,
            face_recognizer=mock_fr,
            stability_threshold=1,
            allow_fallback_match=True,
        )

        result = pipeline.process_frame(_make_dummy_frame())

        entry = result["confirmed_entries"][0]
        self.assertEqual(entry["entry_type"], "member")
        self.assertEqual(entry["session"]["member_id"], "test_001")

    # -- 8. 同一 track_id 不重复创建 session --

    def test_same_track_no_duplicate_session(self):
        """同一 track_id 多帧调用 process_frame 不重复创建 session。"""
        member_result = {"member_id": "M001", "confidence": 0.92}
        mock_fr = MockFaceRecognizer(return_value=member_result)
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=1)])

        pipeline = RealtimeEntryPipeline(
            detector=mock_det,
            face_recognizer=mock_fr,
            stability_threshold=1,
        )

        frame = _make_dummy_frame()
        # 第一帧：确认 + 匹配
        result1 = pipeline.process_frame(frame)
        self.assertEqual(len(result1["confirmed_entries"]), 1)
        self.assertEqual(result1["confirmed_entries"][0]["entry_type"], "member")

        # 后续帧：track 已确认，不再出现在 confirmed_entries
        result2 = pipeline.process_frame(frame)
        self.assertEqual(len(result2["confirmed_entries"]), 0)
        # FaceRecognizer 只被调用一次
        self.assertEqual(mock_fr.call_count, 1)

    # -- 9. 低置信度返回匿名 --

    def test_low_confidence_returns_anonymous(self):
        """confidence < threshold → entry_type='anonymous', reason='low_confidence'。"""
        low_conf_result = {"member_id": "M002", "confidence": 0.50}
        mock_fr = MockFaceRecognizer(return_value=low_conf_result)
        mock_det = MockFrameDetector(detections=[_make_detection(track_id=1)])

        pipeline = RealtimeEntryPipeline(
            detector=mock_det,
            face_recognizer=mock_fr,
            stability_threshold=1,
            member_match_threshold=0.75,
        )

        result = pipeline.process_frame(_make_dummy_frame())

        entry = result["confirmed_entries"][0]
        self.assertEqual(entry["entry_type"], "anonymous")
        self.assertEqual(entry["reason"], "low_confidence")

    # -- 10. active_tracks 统计正确 --

    def test_active_tracks_count(self):
        """验证 active_tracks 正确统计当前活跃 track 数。"""
        mock_det = MockFrameDetector(detections=[
            _make_detection(track_id=1),
            _make_detection(track_id=2),
        ])
        pipeline = RealtimeEntryPipeline(
            detector=mock_det, stability_threshold=5,
        )

        result = pipeline.process_frame(_make_dummy_frame())
        self.assertEqual(result["active_tracks"], 2)


if __name__ == "__main__":
    unittest.main()
