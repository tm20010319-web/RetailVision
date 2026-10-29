from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import requests
from pydantic import ValidationError

from camera_profile_demo.models import DemoProfile


PROFILE_PROMPT = """
你是零售门店的视觉导购助手。请只根据图片中真实可见的人物穿搭、动作和场景，
生成用于现场演示的顾客画像。不要推断年龄、性别、民族、收入、健康等敏感信息。
只输出一个 JSON 对象，不要输出 Markdown 或解释，字段必须是：
style_tags（字符串数组）、visible_clothing（对象）、interest_categories（字符串数组）、
purchase_intent_score（0 到 1）、profile_summary（字符串）、sales_suggestion（字符串）、
confidence（0 到 1）。信息不足时使用“无法判断”，不要编造不可见事实。
""".strip()


@dataclass(frozen=True)
class ProfileResult:
    profile: DemoProfile
    source: Literal["ollama", "mock_fallback"]
    status: Literal["succeeded", "fallback"]
    error_code: str | None


def build_fallback_profile(visitor_id: str) -> DemoProfile:
    digest = hashlib.sha256(visitor_id.encode("utf-8")).digest()
    variants = (
        (
            ["休闲", "简约"],
            {"upper": "休闲上装", "lower": "深色长裤"},
            ["休闲服饰", "日常配饰"],
            "顾客以轻松休闲风格到店，适合从基础款开始介绍。",
        ),
        (
            ["通勤", "利落"],
            {"upper": "简洁外套", "lower": "长裤"},
            ["通勤服饰", "鞋履"],
            "顾客呈现简洁通勤风格，可关注实穿性和搭配效率。",
        ),
        (
            ["运动", "舒适"],
            {"upper": "运动上装", "lower": "休闲长裤"},
            ["运动休闲", "功能配件"],
            "顾客偏向舒适运动风格，可优先展示轻便系列。",
        ),
    )
    tags, clothing, interests, summary = variants[digest[0] % len(variants)]
    return DemoProfile(
        style_tags=tags,
        visible_clothing=clothing,
        interest_categories=interests,
        purchase_intent_score=round(0.42 + (digest[1] % 25) / 100, 2),
        profile_summary=summary,
        sales_suggestion="建议先询问当前搭配需求，再推荐同风格新品和配饰。",
        confidence=0.58,
    )


def _extract_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


class OllamaDemoProfiler:
    def __init__(
        self,
        *,
        ollama_url: str,
        model: str,
        timeout_seconds: float = 45,
        session: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.ollama_url = ollama_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def analyze(self, image_path: str | Path, context: dict[str, Any]) -> ProfileResult:
        visitor_id = str(context.get("visitor_id") or "demo-visitor")
        try:
            image_bytes = Path(image_path).read_bytes()
            payload = {
                "model": self.model,
                "prompt": PROFILE_PROMPT,
                "images": [base64.b64encode(image_bytes).decode("ascii")],
                "stream": False,
                "format": "json",
            }
            response = self.session.post(
                self.ollama_url,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            raw = _extract_json(str(body.get("response", "")))
            return ProfileResult(
                profile=DemoProfile.model_validate(raw),
                source="ollama",
                status="succeeded",
                error_code=None,
            )
        except requests.Timeout:
            error_code = "ollama_timeout"
        except requests.ConnectionError:
            error_code = "ollama_unavailable"
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
            error_code = "invalid_model_output"
        except Exception:
            error_code = "profile_failed"
        return ProfileResult(
            profile=build_fallback_profile(visitor_id),
            source="mock_fallback",
            status="fallback",
            error_code=error_code,
        )
