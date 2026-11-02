"""
CustomerProfilePipeline 单元测试。

测试覆盖：
1. 裁剪图生成
2. Schema 校验通过
3. 畸变 LLM 输出拒绝
4. 隐私边界（privacy_flags）
5. 空输入边界
6. 工厂函数验证
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

from agent.profile.llm_service import (
    BaseVisionLLMService,
    MockVisionLLMService,
)
from agent.profile.schemas import CustomerProfileSchema
from pipelines.legacy.customer_profile_pipeline import (
    CustomerProfilePipeline,
    create_customer_profile_pipeline,
)


# ---------------------------------------------------------------------------
# Mock LLM Services
# ---------------------------------------------------------------------------

class ValidMockVisionLLM(BaseVisionLLMService):
    """始终返回合法画像的 Mock。"""

    def analyze_image(self, image_base64: str, context: dict) -> dict:
        return {
            "style_tags": ["商务", "简约"],
            "visible_clothing": {"upper": "衬衫", "lower": "西裤"},
            "interest_categories": ["商务服饰", "配饰"],
            "purchase_intent_score": 0.7,
            "profile_summary": "顾客穿着商务风格，在货架前停留。",
            "sales_suggestion": "推荐商务系列新品和搭配配饰。",
            "confidence": 0.85,
            "privacy_flags": {
                "face_processed": False,
                "sensitive_attributes_inferred": False,
            },
        }


class MalformedMockVisionLLM(BaseVisionLLMService):
    """返回格式错误数据的 Mock。"""

    def analyze_image(self, image_base64: str, context: dict) -> dict:
        # purchase_intent_score 超出 [0, 1] 范围
        return {
            "style_tags": ["休闲"],
            "visible_clothing": {},
            "interest_categories": [],
            "purchase_intent_score": 1.5,
            "profile_summary": "测试",
            "sales_suggestion": "测试",
            "confidence": 0.5,
            "privacy_flags": {},
        }


class EmptyFieldsMockVisionLLM(BaseVisionLLMService):
    """返回空必填字段的 Mock。"""

    def analyze_image(self, image_base64: str, context: dict) -> dict:
        return {
            "style_tags": [],
            "visible_clothing": {},
            "interest_categories": [],
            "purchase_intent_score": 0.5,
            "profile_summary": "",   # 空 → 应被拒绝
            "sales_suggestion": "",  # 空 → 应被拒绝
            "confidence": 0.5,
            "privacy_flags": {},
        }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def make_test_frame(width=640, height=480):
    """创建一个测试用的 BGR 帧。"""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    # 在中心区域填充灰色
    frame[100:380, 200:440] = 128
    return frame


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class CustomerProfilePipelineTests(unittest.TestCase):

    def setUp(self):
        """每个测试使用临时输出目录。"""
        self._tmpdir = tempfile.mkdtemp()
        self.output_dir = self._tmpdir

    def tearDown(self):
        """清理临时文件。"""
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. 裁剪图生成
    # ------------------------------------------------------------------

    def test_crop_image_generated(self):
        """验证裁剪图文件被正确保存。"""
        frame = make_test_frame()
        bbox = [200, 100, 440, 380]

        pipeline = CustomerProfilePipeline(
            llm_service=ValidMockVisionLLM(),
            output_dir=self.output_dir,
        )

        result = pipeline.run(track_id=1, frame=frame, bbox=bbox,
                              camera_id="cam_01", zone_name="entrance")

        crop_path = result["crop_path"]
        self.assertTrue(os.path.exists(crop_path))
        self.assertIn("customer_1_", os.path.basename(crop_path))

    # ------------------------------------------------------------------
    # 2. Schema 校验通过
    # ------------------------------------------------------------------

    def test_profile_passes_schema_validation(self):
        frame = make_test_frame()
        bbox = [200, 100, 440, 380]

        pipeline = CustomerProfilePipeline(
            llm_service=ValidMockVisionLLM(),
            output_dir=self.output_dir,
        )

        result = pipeline.run(track_id=2, frame=frame, bbox=bbox,
                              camera_id="cam_01", zone_name="fitting_room")

        # 不会抛异常即通过
        validated = CustomerProfileSchema(**result["profile"])
        self.assertIn("商务", validated.style_tags)
        self.assertEqual(validated.purchase_intent_score, 0.7)
        self.assertEqual(result["track_id"], 2)
        self.assertEqual(result["zone_name"], "fitting_room")

    def test_device_capture_without_track_logs_successfully(self):
        image_path = Path(self.output_dir) / "device-capture.jpg"
        image_path.write_bytes(b"device-capture")
        pipeline = CustomerProfilePipeline(
            llm_service=ValidMockVisionLLM(),
            output_dir=self.output_dir,
        )

        with self.assertLogs("pipelines.legacy.customer_profile_pipeline", level="INFO") as logs:
            result = pipeline.run_from_image_path(
                str(image_path),
                track_id=None,
                camera_id="entrance-1",
                zone_name="entrance",
            )

        self.assertIsNone(result["track_id"])
        self.assertIn("track_id=None", "\n".join(logs.output))

    # ------------------------------------------------------------------
    # 3. 畸变 LLM 输出拒绝 — 分值超范围
    # ------------------------------------------------------------------

    def test_malformed_llm_output_rejected_score_out_of_range(self):
        frame = make_test_frame()
        bbox = [200, 100, 440, 380]

        pipeline = CustomerProfilePipeline(
            llm_service=MalformedMockVisionLLM(),
            output_dir=self.output_dir,
        )

        with self.assertRaises(Exception):
            pipeline.run(track_id=3, frame=frame, bbox=bbox)

    # ------------------------------------------------------------------
    # 3b. 畸变 LLM 输出拒绝 — 空必填字段
    # ------------------------------------------------------------------

    def test_malformed_llm_output_rejected_empty_fields(self):
        frame = make_test_frame()
        bbox = [200, 100, 440, 380]

        pipeline = CustomerProfilePipeline(
            llm_service=EmptyFieldsMockVisionLLM(),
            output_dir=self.output_dir,
        )

        with self.assertRaises(Exception):
            pipeline.run(track_id=4, frame=frame, bbox=bbox)

    # ------------------------------------------------------------------
    # 4. 隐私边界
    # ------------------------------------------------------------------

    def test_privacy_flags_set_correctly(self):
        """验证 privacy_flags 中 face_processed 和 sensitive_attributes_inferred 为 False。"""
        frame = make_test_frame()
        bbox = [200, 100, 440, 380]

        pipeline = CustomerProfilePipeline(
            llm_service=ValidMockVisionLLM(),
            output_dir=self.output_dir,
        )

        result = pipeline.run(track_id=5, frame=frame, bbox=bbox)

        privacy = result["profile"]["privacy_flags"]
        self.assertFalse(privacy.get("face_processed", True))
        self.assertFalse(privacy.get("sensitive_attributes_inferred", True))

    def test_no_face_data_in_profile(self):
        """验证 profile 中不包含人脸相关数据。"""
        frame = make_test_frame()
        bbox = [200, 100, 440, 380]

        pipeline = CustomerProfilePipeline(
            llm_service=ValidMockVisionLLM(),
            output_dir=self.output_dir,
        )

        result = pipeline.run(track_id=6, frame=frame, bbox=bbox)

        profile = result["profile"]
        self.assertNotIn("face_embedding", profile)
        self.assertNotIn("face_id", profile)
        self.assertNotIn("age", profile)
        self.assertNotIn("gender", profile)

    # ------------------------------------------------------------------
    # 5. 空输入边界
    # ------------------------------------------------------------------

    def test_invalid_bbox_returns_graceful(self):
        """无效 bbox 时应仍能处理（_crop_person 返回原帧）。"""
        frame = make_test_frame()
        bbox = []  # 无效 bbox

        pipeline = CustomerProfilePipeline(
            llm_service=ValidMockVisionLLM(),
            output_dir=self.output_dir,
        )

        result = pipeline.run(track_id=7, frame=frame, bbox=bbox)

        # 仍应返回有效 profile
        self.assertIn("profile", result)
        validated = CustomerProfileSchema(**result["profile"])
        self.assertIsNotNone(validated.style_tags)

    # ------------------------------------------------------------------
    # 6. 工厂函数验证
    # ------------------------------------------------------------------

    def test_factory_creates_pipeline_correctly(self):
        pipeline = create_customer_profile_pipeline(
            use_mock_llm=True,
            output_dir=self.output_dir,
        )

        self.assertIsInstance(pipeline, CustomerProfilePipeline)
        self.assertIsInstance(pipeline.llm_service, MockVisionLLMService)
        self.assertEqual(pipeline.output_dir, self.output_dir)

    def test_factory_default_output_dir(self):
        pipeline = create_customer_profile_pipeline(use_mock_llm=True)

        self.assertIsInstance(pipeline, CustomerProfilePipeline)
        self.assertIn("customer_profile", pipeline.output_dir)

if __name__ == "__main__":
    unittest.main()
