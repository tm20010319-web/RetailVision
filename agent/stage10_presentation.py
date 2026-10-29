"""Chinese presentation contracts for the Stage 10 demo surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class ProductPresentation:
    internal_key: str
    english_name: str
    chinese_name: str

    @property
    def display_name(self) -> str:
        return f"{self.chinese_name}（{self.english_name}）"


@dataclass(frozen=True)
class PersonDisplayIdentity:
    display_name: str
    display_role: str
    member_id: Optional[str]
    member_name: Optional[str]

    def to_dict(self) -> dict[str, Optional[str]]:
        return {
            "display_name": self.display_name,
            "display_role": self.display_role,
            "member_id": self.member_id,
            "member_name": self.member_name,
        }


_PRODUCT_PRESENTATIONS = {
    "yellow_bee_toy": ProductPresentation(
        internal_key="yellow_bee_toy",
        english_name="yellow bee plush toy",
        chinese_name="黄色小蜜蜂玩偶",
    ),
    "white_guitar": ProductPresentation(
        internal_key="white_guitar",
        english_name="white guitar",
        chinese_name="白色吉他",
    ),
    "smart_toy_a": ProductPresentation(
        internal_key="smart_toy_a",
        english_name="smart bear",
        chinese_name="智能熊",
    ),
    "smart_toy_b": ProductPresentation(
        internal_key="smart_toy_b",
        english_name="smart elephant",
        chinese_name="智能小象",
    ),
    # cam03 uses the approved guitar catalog while reusing the frozen
    # Stage5/7C/8/9 runtime.  These are presentation labels only; behavior
    # semantics remain owned by the existing product interaction runtime.
    "ai_electric_guitar": ProductPresentation(
        internal_key="ai_electric_guitar",
        english_name="AI electric guitar",
        chinese_name="AI电吉他",
    ),
    "ai_guitar": ProductPresentation(
        internal_key="ai_guitar",
        english_name="AI guitar",
        chinese_name="AI吉他",
    ),
}

_CHINESE_STATUS = {
    "PICK_UP": "拿起",
    "HELD": "持有中",
    "HOLDING": "持有中",
    "PUT_DOWN": "放下",
    "STABLE": "稳定",
    "MOVING": "移动中",
    "MISSING": "暂时遮挡",
    "IDLE": "空闲",
    "ACTIVE": "追踪中",
    "MISSING_PENDING": "暂时遮挡",
    "LOST": "跟踪丢失",
    "ASSOCIATED": "已关联",
    "AMBIGUOUS": "关联不明确",
}


def product_presentation(product_key: str) -> ProductPresentation:
    try:
        return _PRODUCT_PRESENTATIONS[str(product_key)]
    except KeyError as error:
        raise ValueError(f"unknown demo product: {product_key}") from error


def chinese_status(value: object) -> str:
    raw = getattr(value, "value", value)
    key = str(raw).upper()
    return _CHINESE_STATUS.get(key, str(raw))


def resolve_person_display_identity(
    track_id: int,
    enrichment: Optional[Mapping[str, object]] = None,
) -> PersonDisplayIdentity:
    values = enrichment or {}
    member_id = str(values.get("member_id") or "").strip() or None
    member_name = str(values.get("member_name") or "").strip() or None
    if member_name is not None:
        display_name = f"会员：{member_name}"
    elif member_id is not None:
        display_name = f"会员 {member_id}"
    else:
        display_name = f"顾客{int(track_id)}"
    return PersonDisplayIdentity(
        display_name=display_name,
        display_role="会员" if member_id is not None or member_name is not None else "顾客",
        member_id=member_id,
        member_name=member_name,
    )


def event_display_message(
    event_type: object,
    *,
    person_name: str,
    product_name: str,
    holding_duration_seconds: Optional[float] = None,
) -> str:
    raw = str(getattr(event_type, "value", event_type)).upper()
    if raw == "PICK_UP":
        return f"{person_name} 拿起了 {product_name}"
    if raw == "PUT_DOWN":
        return f"{person_name} 放下了 {product_name}"
    if raw in {"HELD", "HOLDING"}:
        message = f"{person_name} 正在查看 {product_name}"
        if holding_duration_seconds is not None:
            message += f"｜已持续 {holding_duration_seconds:.1f} 秒"
        return message
    return f"{person_name}｜{chinese_status(raw)}｜{product_name}"
