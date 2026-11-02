from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DemoProfile(BaseModel):
    style_tags: list[str] = Field(default_factory=list)
    visible_clothing: dict[str, str] = Field(default_factory=dict)
    interest_categories: list[str] = Field(default_factory=list)
    purchase_intent_score: float = Field(ge=0.0, le=1.0)
    profile_summary: str
    sales_suggestion: str
    confidence: float = Field(ge=0.0, le=1.0)


class VisitorRecord(BaseModel):
    visitor_id: str
    track_id: int
    camera_id: str
    captured_at: datetime
    capture_url: str
    capture_path: str
    detection_confidence: float = Field(ge=0.0, le=1.0)
    basics: dict[str, str | int]
    profile_status: Literal["pending", "running", "succeeded", "fallback"] = (
        "pending"
    )
    profile_source: Literal["ollama", "mock_fallback"] | None = None
    profile: DemoProfile | None = None
    last_error: str | None = None


class DemoStatus(BaseModel):
    camera_connected: bool = False
    capture_running: bool = False
    worker_running: bool = False
    worker_busy: bool = False
    queue_depth: int = 0
    last_camera_error: str | None = None
    ollama_model: str


def build_mock_basics(visitor_id: str) -> dict[str, str | int]:
    digest = hashlib.sha256(visitor_id.encode("utf-8")).digest()
    display_names = ("顾客 A", "顾客 B", "顾客 C", "顾客 D", "顾客 E")
    tiers = ("普通顾客", "银卡会员", "金卡会员", "体验会员")
    statuses = ("活跃", "新客", "回访顾客")
    return {
        "display_name": display_names[digest[0] % len(display_names)],
        "tier": tiers[digest[1] % len(tiers)],
        "points": 100 + int.from_bytes(digest[2:4], "big") % 4901,
        "visit_count": 1 + digest[4] % 12,
        "customer_status": statuses[digest[5] % len(statuses)],
    }
