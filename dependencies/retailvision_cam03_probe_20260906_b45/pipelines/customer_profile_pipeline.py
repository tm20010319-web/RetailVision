"""
CustomerProfilePipeline：顾客穿搭画像管线（P1）。

职责：
- 从视频帧中裁剪顾客区域。
- 保存裁剪图并编码为 base64。
- 调用视觉 LLM 生成穿搭画像。
- 通过 Pydantic Schema 校验输出。

约束：
- 不做人脸识别（非会员匿名）。
- 不推断敏感属性（年龄、性别、民族等）。
- 复用 customer_profile_analyzer.py 的工具函数，不修改原文件。
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from agent.config import settings
from agent.customer_profile_analyzer import (
    clamp_bbox,
    save_customer_crop,
    encode_image_base64,
)
from agent.llm_service import BaseVisionLLMService, LLMServiceFactory
from agent.schemas import CustomerProfileSchema
from pipelines.realtime_entry_pipeline import RealtimeEntryPipeline

logger = logging.getLogger(__name__)


class CustomerProfilePipeline:
    """顾客穿搭画像管线。

    编排流程：
        crop_person(frame, bbox)
        → save_customer_crop(crop, output_path)
        → encode_image_base64(saved_path)
        → llm_service.analyze_image(image_b64, context)
        → CustomerProfileSchema 校验
        → return profile
    """

    def __init__(
        self,
        llm_service: BaseVisionLLMService,
        output_dir: Optional[str] = None,
    ):
        self.llm_service = llm_service
        self.output_dir = output_dir or os.path.join(settings.OUTPUT_DIR, "customer_profile")

    def run(
        self,
        track_id: int,
        frame: np.ndarray,
        bbox: List[int],
        camera_id: str = "",
        zone_name: str = "",
    ) -> dict:
        """执行顾客穿搭画像分析。

        Args:
            track_id: 顾客追踪 ID。
            frame: 完整视频帧（BGR）。
            bbox: 顾客边界框 [x1, y1, x2, y2]。
            camera_id: 摄像头 ID。
            zone_name: 当前所在区域名称。

        Returns:
            {
                "profile": dict,          # 符合 CustomerProfileSchema
                "track_id": int,
                "camera_id": str,
                "zone_name": str,
                "crop_path": str,         # 裁剪图保存路径
            }
        """
        # 1. 裁剪顾客区域
        crop = RealtimeEntryPipeline._crop_person(frame, bbox)
        logger.info("Cropped person region for track_id=%d", track_id)

        # 2. 保存裁剪图
        crop_filename = f"customer_{track_id}_{uuid.uuid4().hex[:8]}.jpg"
        crop_path = Path(self.output_dir) / crop_filename
        if bbox and len(bbox) == 4:
            saved_path = save_customer_crop(frame, bbox, crop_path)
        else:
            # bbox 无效时直接保存裁剪后的图（_crop_person 返回原帧）
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(crop_path), crop)
            saved_path = str(crop_path)
        logger.info("Saved customer crop: %s", saved_path)

        return self.run_from_image_path(
            saved_path,
            track_id=track_id,
            camera_id=camera_id,
            zone_name=zone_name,
        )

    def run_from_image_path(
        self,
        image_path: str,
        track_id: Optional[int],
        camera_id: str = "",
        zone_name: str = "",
    ) -> dict:
        """Generate a validated profile from an already-saved customer crop."""
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Customer crop not found: {path}")

        # 3. 编码 base64
        image_b64 = encode_image_base64(str(path))

        # 4. LLM 分析
        context = {
            "track_id": track_id,
            "camera_id": camera_id,
            "zone_name": zone_name,
        }
        profile = self.llm_service.analyze_image(image_b64, context)

        # 5. Schema 校验
        validated = CustomerProfileSchema(**profile)

        # 6. 返回
        result = {
            "profile": validated.model_dump(),
            "track_id": track_id,
            "camera_id": camera_id,
            "zone_name": zone_name,
            "crop_path": str(path),
        }

        logger.info(
            "Customer profile generated for track_id=%s: style=%s, intent=%.2f",
            track_id,
            validated.style_tags,
            validated.purchase_intent_score,
        )

        return result


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------

def create_customer_profile_pipeline(
    use_mock_llm: bool = True,
    output_dir: Optional[str] = None,
    vision_timeout_seconds: Optional[float] = None,
    vision_max_retries: Optional[int] = None,
) -> CustomerProfilePipeline:
    """创建 CustomerProfilePipeline 的便捷工厂。

    Args:
        use_mock_llm: True 使用 MockVisionLLMService（不发起 HTTP 请求）。
        output_dir: 裁剪图输出目录（None 则用 config 默认值）。

    Returns:
        配置好的 CustomerProfilePipeline 实例。
    """
    llm_service = LLMServiceFactory.create_vision_llm(
        use_mock=use_mock_llm,
        timeout_seconds=vision_timeout_seconds,
        max_retries=vision_max_retries,
    )
    return CustomerProfilePipeline(
        llm_service=llm_service,
        output_dir=output_dir,
    )
