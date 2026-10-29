"""
LLM 服务抽象层：Mock + Ollama 适配。

设计原则：
- 优先使用 MockLLMService，不主动接入真实大模型。
- 所有输出通过 Pydantic Schema 校验。
- Ollama 适配含重试机制（指数退避）。
"""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import requests

from agent.config import settings
from agent.schemas import CustomerProfileSchema, InsightReportSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON 提取工具（复用 customer_profile_analyzer.py:259-268 的逻辑模式）
# ---------------------------------------------------------------------------

def extract_json_object(text: str) -> dict[str, Any]:
    """从文本中提取 JSON 对象，容错解析。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"LLM response is not valid JSON: {text[:200]}")
    return json.loads(match.group(0))


# ---------------------------------------------------------------------------
# 文本 LLM 服务
# ---------------------------------------------------------------------------

class BaseTextLLMService(ABC):
    """文本大模型服务抽象基类。"""

    @abstractmethod
    def generate_report(self, summary: dict) -> dict:
        """根据业务摘要生成经营分析报告。

        Args:
            summary: VideoAggregator 聚合后的业务摘要 dict。

        Returns:
            符合 InsightReportSchema 的 dict。
        """
        ...


class MockTextLLMService(BaseTextLLMService):
    """Mock 文本大模型 — 不发起任何 HTTP 请求。

    根据 summary 中的 zone_heatmap 和 conversion_signals
    半动态生成报告内容。
    """

    def generate_report(self, summary: dict) -> dict:
        metrics = summary.get("summary_metrics", {})
        zone_heatmap = summary.get("zone_heatmap", {})
        hot_zones = summary.get("hot_zones", [])
        conversion_signals = summary.get("conversion_signals", {})
        time_range = summary.get("time_range", "指定时段")

        visitors = metrics.get("estimated_unique_visitors", 0)
        processed = metrics.get("processed_frames", 0)
        zone_events = metrics.get("total_zone_events", 0)

        # 半动态拼接报告
        top_zone = hot_zones[0]["zone"] if hot_zones else "未知区域"
        top_count = hot_zones[0]["visit_count"] if hot_zones else 0

        fitting = conversion_signals.get("fitting_room_visits", 0)
        checkout = conversion_signals.get("checkout_visits", 0)
        high_interest_low_checkout = conversion_signals.get("high_interest_low_checkout", False)

        traffic_summary = (
            f"该时段估算进店顾客 {visitors} 人，"
            f"处理帧数 {processed}，区域事件 {zone_events} 次。"
            f"访问量最高的区域为「{top_zone}」（{top_count} 人次）。"
        )

        area_insights = []
        for hz in hot_zones[:3]:
            area_insights.append(f"「{hz['zone']}」区域访问 {hz['visit_count']} 人次。")
        if high_interest_low_checkout:
            area_insights.append(
                f"试衣间访问 {fitting} 次但收银台仅 {checkout} 次，"
                f"浏览到购买转化可能不足。"
            )

        operation_suggestions = []
        if high_interest_low_checkout:
            operation_suggestions.append("建议在高兴趣区域增加试穿引导和搭配展示。")
        if top_zone:
            operation_suggestions.append(f"建议在「{top_zone}」区域优化商品陈列和导购配置。")
        operation_suggestions.append("建议持续监控高峰时段客流并动态调整人员排班。")

        sales_suggestions = [
            "导购可重点关注在高热度区域停留较久但未进入收银区的顾客。",
            "可根据区域热度优化商品推荐策略和促销资源分配。",
        ]

        report = {
            "report_title": f"{time_range} 门店视频分析报告",
            "traffic_summary": traffic_summary,
            "area_insights": area_insights,
            "operation_suggestions": operation_suggestions,
            "sales_suggestions": sales_suggestions,
        }

        # Schema 校验
        validated = InsightReportSchema(**report)
        return validated.model_dump()


class OllamaTextLLMService(BaseTextLLMService):
    """Ollama 文本大模型 — 真实 HTTP 调用，含重试。"""

    REPORT_PROMPT_TEMPLATE = """
你是门店经营分析助手。请根据以下业务摘要生成经营分析报告。

要求：
1. 只输出 JSON，不要输出 Markdown 或解释文字。
2. 如果数据不足，请写"数据不足，无法判断"，不要编造。

JSON 字段：
{{
  "report_title": "报告标题",
  "traffic_summary": "客流概述",
  "area_insights": ["区域洞察1", "区域洞察2"],
  "operation_suggestions": ["运营建议1", "运营建议2"],
  "sales_suggestions": ["导购建议1", "导购建议2"]
}}

业务摘要：
{summary_json}
""".strip()

    def __init__(
        self,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        timeout: int = 120,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ):
        self.ollama_url = ollama_url or settings.OLLAMA_URL
        self.ollama_model = ollama_model or settings.OLLAMA_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    def generate_report(self, summary: dict) -> dict:
        prompt = self.REPORT_PROMPT_TEMPLATE.format(
            summary_json=json.dumps(summary, ensure_ascii=False, indent=2)
        )

        last_error = None
        for attempt in range(self.max_retries):
            try:
                payload = {
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                }
                response = requests.post(
                    self.ollama_url, json=payload, timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
                content = data.get("response", "")
                result = extract_json_object(content)
                validated = InsightReportSchema(**result)
                return validated.model_dump()
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Ollama text LLM attempt {attempt + 1}/{self.max_retries} failed: {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff ** attempt)

        raise RuntimeError(
            f"Ollama text LLM failed after {self.max_retries} retries: {last_error}"
        )


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 视觉 LLM 服务
# ---------------------------------------------------------------------------

# 复用 customer_profile_analyzer.py 的 prompt（不修改原文件）
try:
    from agent.customer_profile_analyzer import CUSTOMER_PROFILE_PROMPT
except Exception:
    CUSTOMER_PROFILE_PROMPT = "请根据图片中顾客的可见穿搭、动作和场景，生成顾客画像。只输出JSON。"


class BaseVisionLLMService(ABC):
    """视觉大模型服务抽象基类。"""

    @abstractmethod
    def analyze_image(self, image_base64: str, context: dict) -> dict:
        """根据顾客裁剪图和上下文生成穿搭画像。

        Args:
            image_base64: base64 编码的顾客裁剪图。
            context: 上下文信息（track_id, camera_id, zone_name 等）。

        Returns:
            符合 CustomerProfileSchema 的 dict。
        """
        ...


class MockVisionLLMService(BaseVisionLLMService):
    """Mock 视觉大模型 — 不发起任何 HTTP 请求。

    返回符合 CustomerProfileSchema 的半动态 mock 数据。
    """

    def analyze_image(self, image_base64: str, context: dict) -> dict:
        zone_name = context.get("zone_name", "未知区域")
        camera_id = context.get("camera_id", "")

        profile = {
            "style_tags": ["休闲", "简约"],
            "visible_clothing": {"upper": "T恤", "lower": "牛仔裤"},
            "interest_categories": ["休闲服饰", "配饰"],
            "purchase_intent_score": 0.5,
            "profile_summary": f"顾客在「{zone_name}」区域浏览，穿着休闲风格。",
            "sales_suggestion": f"可推荐与休闲风格搭配的新品，关注其在「{zone_name}」区域的停留。",
            "confidence": 0.7,
            "privacy_flags": {
                "face_processed": False,
                "sensitive_attributes_inferred": False,
            },
        }

        validated = CustomerProfileSchema(**profile)
        return validated.model_dump()


class OllamaVisionLLMService(BaseVisionLLMService):
    """Ollama 视觉大模型 — 真实 HTTP 调用，含重试。

    复用 customer_profile_analyzer.py:271-298 的 Ollama 调用逻辑。
    """

    def __init__(
        self,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        timeout: int = 120,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ):
        self.ollama_url = ollama_url or settings.OLLAMA_URL
        self.ollama_model = ollama_model or settings.OLLAMA_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    def analyze_image(self, image_base64: str, context: dict) -> dict:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                payload = {
                    "model": self.ollama_model,
                    "prompt": CUSTOMER_PROFILE_PROMPT,
                    "images": [image_base64],
                    "stream": False,
                    "format": "json",
                }
                response = requests.post(
                    self.ollama_url, json=payload, timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
                content = data.get("response", "")
                raw = extract_json_object(content)

                # 将 LLM 原始输出映射到 Schema 字段
                profile = self._map_to_schema(raw, context)
                validated = CustomerProfileSchema(**profile)
                return validated.model_dump()
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Ollama vision LLM attempt {attempt + 1}/{self.max_retries} failed: {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff ** attempt)

        raise RuntimeError(
            f"Ollama vision LLM failed after {self.max_retries} retries: {last_error}"
        )

    def _map_to_schema(self, raw: dict, context: dict) -> dict:
        """将 LLM 原始 JSON 映射到 CustomerProfileSchema 字段。"""
        zone_name = context.get("zone_name", "未知区域")
        return {
            "style_tags": [raw.get("穿搭风格", "无法判断")],
            "visible_clothing": {},
            "interest_categories": raw.get("商品兴趣", []),
            "purchase_intent_score": raw.get("购买意图分", 0) / 100.0,
            "profile_summary": f"{raw.get('穿搭风格', '无法判断')}，行为状态：{raw.get('行为状态', '未知')}，区域：{zone_name}",
            "sales_suggestion": "；".join(raw.get("导购建议", ["暂无建议"])),
            "confidence": 0.8,
            "privacy_flags": {
                "face_processed": False,
                "sensitive_attributes_inferred": False,
            },
        }


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

class LLMServiceFactory:
    """LLM 服务工厂。"""

    @staticmethod
    def create_text_llm(use_mock: bool = True) -> BaseTextLLMService:
        if use_mock:
            return MockTextLLMService()
        return OllamaTextLLMService()

    @staticmethod
    def create_vision_llm(
        use_mock: bool = True,
        *,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> BaseVisionLLMService:
        if use_mock:
            return MockVisionLLMService()
        return OllamaVisionLLMService(
            timeout=(
                settings.OLLAMA_VISION_TIMEOUT_SECONDS
                if timeout_seconds is None
                else timeout_seconds
            ),
            max_retries=(
                settings.OLLAMA_VISION_MAX_RETRIES
                if max_retries is None
                else max_retries
            ),
        )
