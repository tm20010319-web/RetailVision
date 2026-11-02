"""
Pydantic Schema 定义：LLM 输出结构校验。

所有大模型输出必须通过对应的 Schema 校验，
非法字段或类型不匹配时直接拒绝。
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class VideoSummarySchema(BaseModel):
    """VideoAggregator 输出校验 — 聚合后的业务摘要。"""

    task_type: str = Field(default="historical_video_analysis")
    time_range: Optional[str] = None
    summary_metrics: dict = Field(
        default_factory=lambda: {
            "estimated_unique_visitors": 0,
            "processed_frames": 0,
            "total_zone_events": 0,
        }
    )
    zone_heatmap: dict[str, int] = Field(default_factory=dict)
    hot_zones: list[dict] = Field(default_factory=list)
    conversion_signals: dict = Field(default_factory=dict)
    peak_hours: list[int] = Field(default_factory=list)

    @field_validator("zone_heatmap")
    @classmethod
    def heatmap_values_non_negative(cls, v: dict) -> dict:
        for zone, count in v.items():
            if count < 0:
                raise ValueError(f"zone_heatmap count must be non-negative, got {count} for zone '{zone}'")
        return v


class InsightReportSchema(BaseModel):
    """文本大模型输出校验 — 经营分析报告（对齐方案文档 5.4 节）。"""

    report_title: str
    traffic_summary: str
    area_insights: list[str] = Field(default_factory=list)
    operation_suggestions: list[str] = Field(default_factory=list)
    sales_suggestions: list[str] = Field(default_factory=list)

    @field_validator("report_title", "traffic_summary")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must not be empty")
        return v


class CustomerProfileSchema(BaseModel):
    """顾客穿搭画像 Schema — 视觉大模型输出校验。

    对齐 customer_profile_analyzer.py:13-21 和方案文档 4.5 节。
    """

    style_tags: list[str] = Field(default_factory=list)
    visible_clothing: dict[str, str] = Field(default_factory=dict)
    interest_categories: list[str] = Field(default_factory=list)
    purchase_intent_score: float = Field(default=0.0, ge=0.0, le=1.0)
    profile_summary: str = ""
    sales_suggestion: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    privacy_flags: dict[str, bool] = Field(default_factory=dict)

    @field_validator("profile_summary", "sales_suggestion")
    @classmethod
    def not_empty_str(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must not be empty")
        return v
