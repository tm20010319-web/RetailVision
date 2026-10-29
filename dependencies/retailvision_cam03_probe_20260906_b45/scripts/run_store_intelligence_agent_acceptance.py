"""Run the seven real mewcode/RetailVision Agent acceptance scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER_VERSION = "r5-04-2-minimal-answer-gate-v1"
SCHEMA_VERSION = "r5-04-2-autonomous-answer-gate-v1"
SCENARIOS = (
    ("hot-products", "最近哪些商品最火？", "rank_products"),
    ("toy-a-summary", "智能熊最近表现如何？", "get_product_behavior_summary"),
    ("compare-toys", "智能熊和智能象哪个互动更多？", "compare_product_behavior"),
    ("display-priority", "推荐重点陈列哪些商品？", "recommend_hot_products"),
    ("rising-products", "哪些商品热度正在上涨？", "analyze_product_trend"),
    ("behavior-anomaly", "最近有没有异常商品行为？", "detect_product_anomaly"),
    ("display-adjustment", "商品应该怎样调整陈列？", "suggest_product_display"),
)
CATALOG_ALIASES = {
    "smart_toy_a": {"智能熊"},
    "smart_toy_b": {"智能象", "智能小象"},
}
SAFE_ARGUMENTS = {
    "store_id", "camera_id", "start_at", "end_at", "product_key",
    "second_product_key", "limit", "threshold", "previous_start_at",
    "previous_end_at",
}
TOOL_NAMES = {tool_name for _, _, tool_name in SCENARIOS} | {"rank_products"}
FORBIDDEN_MARKERS = (
    "api_key", "authorization", "password", "secret", "database_url",
    "person_track_token", "track_key_hash", "person_track_hash", "member_id",
    "identity_candidate", "identity candidate", "face_bbox", "face bbox",
    "raw_stage9_events", "restricted artifact",
)
UNSUPPORTED_CLAIMS = {
    "commerce_outcome": re.compile(
        r"购买|(?<!完)成交|销售|销量|转化|下单|purchase|sale|conversion|bought",
        re.IGNORECASE,
    ),
    "identified_people": re.compile(
        r"顾客|消费者|访客|客流|来访|到访|光顾|人员|人数|人次|每人|人均|独立客|"
        r"customer|consumer|visitor|footfall|\bpeople\b|\bpersons?\b",
        re.IGNORECASE,
    ),
    "repeat_visit": re.compile(
        r"重复光顾|多次光顾|回访|复访|repeat(?:ed)?\s+visits?",
        re.IGNORECASE,
    ),
    "physical_placement": re.compile(
        r"主通道|黄金(?:位置|视线|区域)?|相邻|邻近|最显眼|伸手区|"
        r"视线区|摆放|排布|physical\s+placement|main\s+aisle|golden\s+zone|"
        r"adjacent|eye[- ]level|most\s+visible",
        re.IGNORECASE,
    ),
    "inferred_intent": re.compile(
        r"购买意图|成交倾向|兴趣浓度|价值感知|吸引力|深度体验|"
        r"互动深度|拿取深度|互动质量|关注度|参与广度|"
        r"互动.{0,8}更深|更深.{0,8}互动|"
        r"节奏正常|走势平稳|表现稳定|没有突出波动|无明显波动|"
        r"purchase\s+intent|buying\s+intent|interest\s+level|"
        r"interaction\s+quality|engagement\s+depth",
        re.IGNORECASE,
    ),
    "unsupported_engagement_claim": re.compile(
        r"(?<!加大)关注(?!数据)|参与度|互动规模|参与深度|高关注|互动质量|兴趣|吸引力|深度体验|"
        r"engagement\s+(?:level|scale|depth|quality)|interest|attractiveness",
        re.IGNORECASE,
    ),
    "unsupported_derived_metric": re.compile(r"\d+(?:\.\d+)?\s*倍"),
}

_CLAUSE_BOUNDARY = re.compile(
    r"[，,；;。.!！？?\n]|但(?:是)?|然而|不过|且|并且|\b(?:and|but|however)\b",
    re.IGNORECASE,
)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _database_url(path: Path) -> str:
    return "sqlite:///" + path.resolve().as_posix()


def _provider_identity(provider: dict[str, Any]) -> dict[str, str]:
    return {
        "provider": str(provider.get("name") or provider.get("protocol") or "unknown"),
        "model": str(provider.get("model") or "unknown"),
    }


def _build_config(
    source: dict[str, Any], database: Path, python: str
) -> dict[str, Any]:
    providers = source.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("provider_config_missing")
    return {
        "providers": providers,
        "permission_mode": source.get("permission_mode", "default"),
        "enable_coordinator_mode": False,
        "mcp_servers": [{
            "name": "retailvision",
            "command": python,
            "args": ["-m", "agent.mcp_server.server"],
            "request_timeout_seconds": 15,
            "env": {
                "PYTHONPATH": str(PROJECT_ROOT),
                "PYTHONUTF8": "1",
                "RETAILVISION_DATABASE_URL": _database_url(database),
                "RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED": "true",
                "RETAILVISION_STORE_ID": "store-1",
                "RETAILVISION_CAMERA_ID": "cam02",
                "RETAILVISION_ANALYSIS_START_AT": "2026-08-20T13:00:00",
                "RETAILVISION_ANALYSIS_END_AT": "2026-08-20T14:00:00",
                "RETAILVISION_PRODUCT_CATALOG_PROFILE": "stage10_smart_toys_v1",
            },
        }],
    }


def _parse_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
    return events


def _safe_arguments(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in sorted(value) if key in SAFE_ARGUMENTS}


def _contains_forbidden(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    return any(marker in text for marker in FORBIDDEN_MARKERS)


def _presentation_violations(answer: str, decoded: list[Any]) -> list[str]:
    """Detect internal execution details that are not part of a user answer."""
    lowered = answer.lower()
    violations: list[str] = []
    product_keys: set[str] = set(CATALOG_ALIASES)

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            key = value.get("product_key")
            if isinstance(key, str):
                product_keys.add(key)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for value in decoded:
        collect(value)
    if any(re.search(rf"(?<![\w-]){re.escape(key.lower())}(?![\w-])", lowered)
           for key in product_keys):
        violations.append("internal_product_key")
    if any(re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", lowered)
           for name in TOOL_NAMES) or "mcp__" in lowered:
        violations.append("tool_name")
    if re.search(
        r"recommendation[_ ]score|recommendation[_ ]reasons?|"
        r"\brecommended\s*[:=]|\breason\s*[:=]|推荐评分|推荐分数|原始分数|原因代码|"
        r"\b(?:true|false)\b",
        lowered,
    ):
        violations.append("raw_internal_field")
    if re.search(
        r"\b(?:i(?:'ll| will)|let me)\s+(?:call|check|query|load|use|invoke|"
        r"report|summari[sz]e|present|explain)|"
        r"\b(?:toolsearch|routing|tool|mcp)\b|"
        r"工具(?!返回(?:了)?空结果)|"
        r"(?:让我|我来|我会|将要?)\s*(?:加载|调用|查询|检查|使用|汇报|总结|说明)|"
        r"路由规则|工具路由|调用.{0,20}工具|使用.{0,20}工具",
        answer,
        re.IGNORECASE,
    ):
        violations.append("execution_process")
    return sorted(set(violations))


def _presentation_warnings(answer: str, decoded: list[Any]) -> list[str]:
    """Report non-blocking generic tool-result wording without internal details."""
    if _presentation_violations(answer, decoded):
        return []
    if re.search(r"工具返回(?:了)?空结果", answer, re.IGNORECASE):
        return ["generic_empty_tool_result"]
    return []


def _standalone_number(answer: str, value: int | float) -> bool:
    forms = {str(value), f"{float(value):g}"}
    return any(
        re.search(rf"(?<![\d.]){re.escape(form)}(?![\d.])", answer)
        for form in forms
    )


def _unsupported_claims(answer: str) -> list[str]:
    found: list[str] = []
    for name, pattern in UNSUPPORTED_CLAIMS.items():
        for match in pattern.finditer(answer):
            boundaries = list(_CLAUSE_BOUNDARY.finditer(answer, 0, match.start()))
            clause_start = boundaries[-1].end() if boundaries else 0
            prefix = answer[clause_start:match.start()].lower()[-40:]
            if re.search(
                r"不是|并非|没有|尚无|未形成|不等于|不等同于|不构成|不具备|不据此|不涉及|"
                r"不包含|不提供|不含|不在|不属于|非|"
                r"不能|不代表|无法|无(?:法)?|"
                r"not\s+(?:a\s+)?|does\s+not|do\s+not|cannot|can't|no\s+",
                prefix,
            ):
                continue
            if name == "physical_placement":
                negated_context = answer[max(0, match.start() - 50):match.start()]
                if re.search(r"不涉及[^。！？\n]{0,30}$", negated_context) and not re.search(
                    r"且|但|但是|建议|放到|放在|摆到", negated_context
                ):
                    continue
                prior = answer[:match.start()]
                negative_boundary = re.search(
                    r"(?:不涉及|不具备|不包含|不提供)[^。！？\n]{0,120}$",
                    prior,
                    re.IGNORECASE,
                )
                if negative_boundary and not re.search(
                    r"放到|放在|摆到|主通道|黄金位置|货架位置|"
                    r"eye[- ]?level|main\s+aisle|physical\s+placement",
                    answer[negative_boundary.start():],
                    re.IGNORECASE,
                ):
                    continue
            if name == "unsupported_engagement_claim" and re.search(
                r"用户(?:问|询问|提出|对)|问题(?:是|为)|(?:用户的)?问题中|关于[\"“]?",
                answer[max(0, match.start() - 40):match.start()],
                re.IGNORECASE,
            ):
                # Quoting or paraphrasing the user's subjective wording is
                # allowed when the answer immediately reframes it using facts.
                continue
            if name == "unsupported_engagement_claim":
                quoted_prefix = answer[max(0, match.start() - 20):match.start()]
                quoted_suffix = answer[match.end():match.end() + 20]
                if re.search(r"[\"“]", quoted_prefix) and re.search(r"[\"”]", quoted_suffix):
                    continue
            if re.search(
                r"(?:不在[^。！？\n]{0,30}范围|不属于[^。！？\n]{0,30}|"
                r"未(?:提供|包含)|不存在)",
                answer[match.end():match.end() + 50],
                re.IGNORECASE,
            ):
                continue
            found.append(name)
            break
    return found


def _strip_scope_literals(value: str) -> str:
    value = re.sub(
        r"(?<![A-Za-z0-9_])\d{4}-\d{2}-\d{2}"
        r"(?:[ T]\d{2}:\d{2}(?::\d{2})?)?(?![A-Za-z0-9_])",
        " ",
        value,
    )
    value = re.sub(
        r"(?<![A-Za-z0-9_])\d{1,2}月\d{1,2}日?(?![A-Za-z0-9_])",
        " ",
        value,
    )
    return re.sub(
        r"(?<![A-Za-z0-9_])\d{1,2}:\d{2}(?::\d{2})?(?![A-Za-z0-9_])",
        " ",
        value,
    )


def _numeric_claims(answer: str) -> list[tuple[float, int]]:
    without_scope = _strip_scope_literals(answer)
    without_scope = re.sub(
        r"\b(?:store|cam(?:era)?|smart_toy)[-_]?[A-Za-z0-9_-]*\b",
        " ",
        without_scope,
        flags=re.IGNORECASE,
    )
    claims: list[tuple[float, int]] = []
    for match in re.finditer(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])", without_scope):
        literal = match.group(0)
        precision = len(literal.partition(".")[2]) if "." in literal else 0
        claims.append((float(literal), precision))
    return claims


def _metric_matches(claim: float, precision: int, metric: float) -> bool:
    if precision == 0:
        return abs(claim - metric) < 1e-6
    return abs(claim - round(metric, precision)) < 10 ** (-(precision + 1))


_METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("completed_interaction_count", re.compile(
        r"(?:完成|完整)(?:的)?(?:匿名)?(?:\s*\d+(?:\.\d+)?\s*次)?\s*(?:交互|互动)|completed?\s+interactions?",
        re.IGNORECASE,
    )),
    ("pickup_count", re.compile(r"拿起|拾取|拾起|拿取|pickup(?:\s+count)?", re.IGNORECASE)),
    ("put_down_count", re.compile(r"放回|放下|put[- ]?down(?:\s+count)?", re.IGNORECASE)),
    ("unique_person_track_count", re.compile(
        r"匿名(?:个体)?轨迹(?:数|数量)?|匿名轨道(?:数|数量)?|unique\s+(?:person\s+)?tracks?",
        re.IGNORECASE,
    )),
    ("total_holding_seconds", re.compile(
        r"(?:总|累计|总计)(?:持有|持握|握持|把持)?|共(?=\s*\d+(?:\.\d+)?\s*(?:秒|s|seconds))|total\s+holding(?:\s+time)?",
        re.IGNORECASE,
    )),
    ("avg_holding_seconds", re.compile(
        r"平均(?:每次)?(?:持有|持握|握持|把持)?|平均每次|"
        r"(?:单次|每次)(?:持有|持握|握持|把持|停留)|average\s+holding(?:\s+time)?",
        re.IGNORECASE,
    )),
    ("max_holding_seconds", re.compile(
        r"最长(?:(?:持有|持握|握持|把持)|一次)?|最大(?:持有|持握|握持|把持)?|max(?:imum)?\s+holding(?:\s+time)?",
        re.IGNORECASE,
    )),
    ("recommendation_score", re.compile(
        r"(?:推荐)?(?:分数|评分|得分|指数|分)|推荐确定性|recommendation\s+score|score",
        re.IGNORECASE,
    )),
    ("display_priority", re.compile(
        r"展示优先级|陈列优先级|优先级|优先|display\s+priority",
        re.IGNORECASE,
    )),
    ("interaction_change", re.compile(r"变动|增长|变化|change|increase|decrease", re.IGNORECASE)),
    ("interaction_count", re.compile(
        r"互动(?:次数|量|数)?|交互(?:次数|量|数)?|interaction(?:\s+count|s)?",
        re.IGNORECASE,
    )),
    ("holding_seconds", re.compile(
        r"持有|持握|握持|把持|拿持|持续|时长|holding(?:\s+time)?",
        re.IGNORECASE,
    )),
    ("rank", re.compile(r"排名|第(?:一|二|三|[0-9]+)位|rank(?:ed)?", re.IGNORECASE)),
)

_METRIC_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "completed_interaction_count": ("completed_interaction_count", "interaction_count"),
    "pickup_count": ("pickup_count",),
    "put_down_count": ("put_down_count",),
    "unique_person_track_count": ("unique_person_track_count",),
    "total_holding_seconds": ("total_holding_seconds",),
    "avg_holding_seconds": ("avg_holding_seconds", "average_holding_seconds"),
    "max_holding_seconds": ("max_holding_seconds", "max_hold_time"),
    "recommendation_score": ("recommendation_score",),
    "display_priority": ("display_priority",),
    "interaction_change": ("interaction_change",),
    "interaction_count": ("interaction_count", "completed_interaction_count"),
    "holding_seconds": (
        "total_holding_seconds", "avg_holding_seconds",
        "average_holding_seconds", "max_holding_seconds", "max_hold_time",
    ),
    "rank": ("rank",),
}


def _product_metric_violations(answer: str, decoded: list[Any]) -> list[str]:
    product_metrics: dict[str, dict[str, set[float]]] = {}
    product_tokens: dict[str, set[str]] = {}

    def add_metric(product: str, metric: str, value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            product_metrics.setdefault(product, {}).setdefault(metric, set()).add(float(value))

    def collect(value: Any, inherited_product: str | None = None) -> None:
        if isinstance(value, dict):
            product = value.get("product_key")
            if not isinstance(product, str):
                product = inherited_product
            if isinstance(value.get("product_key"), str):
                product_key = value["product_key"]
                tokens = product_tokens.setdefault(
                    product_key,
                    {product_key.lower(), *(alias.lower() for alias in CATALOG_ALIASES.get(product_key, set()))},
                )
                product_name = value.get("product_name")
                if isinstance(product_name, str) and product_name:
                    tokens.add(product_name.lower())
            if product:
                for key, item in value.items():
                    for canonical, aliases in _METRIC_KEY_ALIASES.items():
                        if key in aliases:
                            add_metric(product, canonical, item)
            for key, item in value.items():
                if isinstance(item, dict) and key in {"scores", "recommendation_scores"}:
                    for score_product, score in item.items():
                        if isinstance(score_product, str):
                            add_metric(score_product, "recommendation_score", score)
                collect(item, product)
        elif isinstance(value, list):
            for index, item in enumerate(value, start=1):
                if isinstance(item, dict) and isinstance(item.get("product_key"), str):
                    add_metric(item["product_key"], "rank", index)
                collect(item, inherited_product)

    for value in decoded:
        collect(value)
    if not product_metrics:
        return []

    def clean_line(value: str) -> str:
        value = _strip_scope_literals(value)
        return re.sub(
            r"\b(?:store|cam(?:era)?)[-_]?[A-Za-z0-9_-]*\b",
            " ",
            value,
            flags=re.IGNORECASE,
        )

    def product_occurrences(value: str) -> list[tuple[int, int, str]]:
        lowered = value.lower()
        found: list[tuple[int, int, str]] = []
        for product_key, tokens in product_tokens.items():
            for token in tokens:
                found.extend(
                    (match.start(), match.end(), product_key)
                    for match in re.finditer(re.escape(token), lowered)
                )
        return sorted(found)

    def metric_occurrences(value: str) -> list[tuple[int, int, str]]:
        lowered = value.lower()
        found: list[tuple[int, int, str]] = []
        for metric, pattern in _METRIC_PATTERNS:
            for match in pattern.finditer(lowered):
                if any(
                    start < match.end() and match.start() < end
                    for start, end, _ in found
                ):
                    continue
                found.append((match.start(), match.end(), metric))
        # Broad fallback patterns overlap specific metric phrases. Keep the
        # specific match so nearby numbers bind to the intended field.
        specific = {
            "avg_holding_seconds", "max_holding_seconds",
            "total_holding_seconds", "completed_interaction_count",
        }
        specific_spans = [
            (start, end, metric) for start, end, metric in found
            if metric in specific
        ]
        found = [
            item for item in found
            if not (
                item[2] == "holding_seconds"
                and any(
                    start <= item[1] + 2 and item[0] <= end + 2
                    for start, end, metric in specific_spans
                    if metric in {
                        "avg_holding_seconds", "max_holding_seconds",
                        "total_holding_seconds",
                    }
                )
            )
            and not (
                item[2] == "interaction_count"
                and any(
                    start <= item[1] + 2 and item[0] <= end + 2
                    for start, end, metric in specific_spans
                    if metric == "completed_interaction_count"
                )
            )
        ]
        return sorted(found, key=lambda item: (item[0], -(item[1] - item[0])))

    def validate_segment(
        segment: str,
        inherited_product: str | None,
        header_metric: str | set[str] | None = None,
    ) -> list[str]:
        violations: list[str] = []
        products = product_occurrences(segment)
        numbers = list(re.finditer(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])", segment))
        if not numbers:
            return violations
        if len(numbers) >= 2 and re.search(r"\bvs\.?\b|\d\s*(?:比|对)\s*\d", segment, re.IGNORECASE):
            return violations
        for number in numbers:
            context_after = segment[number.end():number.end() + 24]
            context_before = segment[max(0, number.start() - 12):number.start()]
            if re.search(r"(?:优先级|排名|第)\s*$", context_before):
                continue
            if re.search(r"(?:轨迹|匿名轨迹)\s*$", context_before):
                continue
            if re.search(
                r"(?:起跳|无(?:数据|互动)|上一窗口|前一窗口|zero|no\s+data)",
                context_before + context_after,
                re.IGNORECASE,
            ):
                continue
            if re.search(r"\s*(?:款|个)\s*商品|products?\b", context_after, re.IGNORECASE):
                continue
            if re.search(r"(?:store|cam(?:era)?)\s*[-_]?\s*$", context_before, re.IGNORECASE):
                continue
            nearby_products = sorted(
                ((min(abs(number.start() - start), abs(number.end() - end)), product)
                 for start, end, product in products),
                key=lambda item: item[0],
            )
            associated = None
            if nearby_products:
                nearest_distance = nearby_products[0][0]
                nearest = {product for distance, product in nearby_products if distance == nearest_distance}
                if len(nearest) == 1:
                    associated = next(iter(nearest))
            elif inherited_product:
                associated = inherited_product
            if associated is None:
                violations.append(f"unbound_product:{number.group(0)}")
                continue

            chosen_metrics: set[str] = (
                set(header_metric) if isinstance(header_metric, set)
                else ({header_metric} if header_metric else set())
            )
            if not chosen_metrics:
                local_before = segment[max(0, number.start() - 24):number.start()]
                local_after = segment[number.end():number.end() + 12]
                explicit_metric = None
                contextual_metrics = (
                    ("rank", r"(?:排名|优先级|列|第)\s*$"),
                    ("unique_person_track_count", r"(?:匿名)?轨迹(?:数|为)?\s*$"),
                    ("avg_holding_seconds", r"平均[^，,；;。]{0,16}最长[，,]?\s*(?:达|为|约)?\s*$"),
                    ("max_holding_seconds", r"(?:最长|最大)(?:单次)?(?:(?:持有|持握|持时)(?:时长)?|时长)?(?:约|为)?\s*$"),
                    ("avg_holding_seconds", r"(?:平均(?:每次)?|单次平均)(?:(?:持有|持握|持时)(?:时长)?|时长)?(?:约|为)?\s*$"),
                    ("avg_holding_seconds", r"(?:单次|每次)(?:停留|持有|持握)(?:时长)?(?:约|为)?\s*$"),
                    ("total_holding_seconds", r"(?:累计|总计|总)(?:(?:持有|持握|持时)(?:时长)?|时长)?(?:约|为)?\s*$"),
                    ("completed_interaction_count", r"(?:完整|完成)(?:的)?(?:交互|互动)(?:次数|为)?\s*$"),
                    ("pickup_count", r"(?:拿起|拾取|拿取)(?:次数|为)?\s*$"),
                    ("put_down_count", r"(?:放下|放回)(?:次数|为)?\s*$"),
                    ("interaction_count", r"(?:交互|互动)(?:次数|量|数|为)?\s*$"),
                )
                for metric, pattern in contextual_metrics:
                    if re.search(pattern, local_before, re.IGNORECASE):
                        explicit_metric = metric
                        break
                if explicit_metric is None:
                    following_metrics = (
                        ("completed_interaction_count", r"\s*次?\s*(?:完整|完成)(?:交互|互动)"),
                        ("pickup_count", r"\s*次?\s*(?:拿起|拾取|拿取)"),
                        ("put_down_count", r"\s*次?\s*(?:放下|放回)"),
                        ("unique_person_track_count", r"\s*(?:条|个)?\s*(?:匿名)?轨迹"),
                        ("interaction_count", r"\s*次?\s*(?:交互|互动)"),
                    )
                    explicit_metric = next(
                        (
                            metric for metric, pattern in following_metrics
                            if re.match(pattern, local_after, re.IGNORECASE)
                        ),
                        None,
                    )
                if explicit_metric is None and re.match(r"\s*条", local_after):
                    explicit_metric = "unique_person_track_count"
                if explicit_metric is not None:
                    chosen_metrics = {explicit_metric}
            if not chosen_metrics:
                distances = sorted(
                    (min(abs(number.start() - start), abs(number.end() - end)), metric)
                    for start, end, metric in metric_occurrences(segment)
                )
                if distances:
                    nearest_distance = distances[0][0]
                    chosen_metrics = {
                        metric for distance, metric in distances if distance == nearest_distance
                    }
            if len(chosen_metrics) > 1 and "各" in segment:
                if all(
                    any(
                        _metric_matches(
                            float(number.group(0)),
                            len(number.group(0).partition(".")[2]),
                            value,
                        )
                        for value in product_metrics.get(associated, {}).get(candidate, set())
                    )
                    for candidate in chosen_metrics
                ):
                    continue
            if len(chosen_metrics) != 1:
                violations.append(f"unbound_metric:{associated}:{number.group(0)}")
                continue
            metric = next(iter(chosen_metrics))
            values = product_metrics.get(associated, {}).get(metric, set())
            if not any(
                _metric_matches(
                    float(number.group(0)),
                    len(number.group(0).partition(".")[2]),
                    value,
                )
                for value in values
            ):
                violations.append(f"{associated}:{number.group(0)}")
        return violations

    violations: list[str] = []
    current_product: str | None = None
    table_headers: list[str | None] | None = None
    table_products: dict[int, str] | None = None
    for raw_line in answer.splitlines():
        line = clean_line(raw_line)
        if "|" in line:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            header_candidates = [
                next((metric for metric, pattern in _METRIC_PATTERNS if pattern.search(cell)), None)
                for cell in cells
            ]
            if any(header_candidates):
                table_headers = header_candidates
                table_products = None
                continue
            header_products = {
                index: occurrences[0][2]
                for index, cell in enumerate(cells)
                if (occurrences := product_occurrences(cell))
            }
            if len(header_products) >= 2:
                table_products = header_products
                table_headers = None
                continue
            if table_products:
                row_metric = next(
                    (metric for metric, pattern in _METRIC_PATTERNS
                     if pattern.search(cells[0])),
                    None,
                )
                for index, cell in enumerate(cells):
                    metric = row_metric
                    product = table_products.get(index)
                    if (
                        product and metric
                        and re.search(
                            r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])",
                            cell,
                        )
                    ):
                        violations.extend(validate_segment(cell, product, metric))
                continue
            if table_headers:
                row_products = product_occurrences(" ".join(cells))
                row_product = row_products[0][2] if row_products else current_product
                for index, cell in enumerate(cells):
                    metric = table_headers[index] if index < len(table_headers) else None
                    if metric and re.search(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])", cell):
                        violations.extend(validate_segment(cell, row_product, metric))
                if row_product:
                    current_product = row_product
                continue

        segment_line = re.sub(
            r"^\s*[*_~]*\s*(?:[-*+]\s+|\d+[.)]\s+)",
            "",
            line,
        )
        segments = [
            segment for segment in re.split(
                r"[，,；;。!！？?]|(?<!\d)\.(?!\d)|(?:但(?:是)?|然而|不过)|\b(?:but|however)\b",
                segment_line,
                flags=re.IGNORECASE,
            ) if segment.strip()
        ]
        named = product_occurrences(segment_line)
        named_products = {item[2] for item in named}
        if len(named_products) == 1:
            current_product = next(iter(named_products))
        elif len(named_products) > 1:
            current_product = None
        active_product = current_product
        active_metric: str | set[str] | None = None
        for segment in segments:
            if len({item[2] for item in product_occurrences(segment)}) > 1:
                continue
            segment_products = {item[2] for item in product_occurrences(segment)}
            if len(segment_products) == 1:
                active_product = next(iter(segment_products))
            segment_metrics = {item[2] for item in metric_occurrences(segment)}
            if len(segment_metrics) == 1:
                active_metric = next(iter(segment_metrics))
                inherited_metric = None
            elif not segment_metrics:
                inherited_metric = active_metric
            else:
                occurrences = metric_occurrences(segment)
                active_metric = (
                    set(segment_metrics) if "各" in segment
                    else occurrences[-1][2]
                )
                inherited_metric = None
            violations.extend(
                validate_segment(segment, active_product, inherited_metric)
            )
    return sorted(set(violations))


def _prepare_output_directory(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise SystemExit("acceptance_output_directory_not_empty")
        return
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.mkdir()
    except FileExistsError:
        raise SystemExit("acceptance_output_directory_not_empty") from None


def _load_test_summary(source: Path) -> dict[str, Any]:
    try:
        summary = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("test_summary_invalid") from error
    suites = summary.get("suites") if isinstance(summary, dict) else None
    if not isinstance(suites, list) or not suites or _contains_forbidden(summary):
        raise SystemExit("test_summary_invalid")
    for suite in suites:
        if not isinstance(suite, dict):
            raise SystemExit("test_summary_invalid")
        count_keys = ("total", "passed", "failures", "errors", "skipped")
        if any(
            isinstance(suite.get(key), bool)
            or not isinstance(suite.get(key), int)
            for key in count_keys
        ):
            raise SystemExit("test_summary_invalid")
        try:
            total = int(suite["total"])
            passed = int(suite["passed"])
            failures = int(suite["failures"])
            errors = int(suite["errors"])
            skipped = int(suite["skipped"])
        except (KeyError, TypeError, ValueError) as error:
            raise SystemExit("test_summary_invalid") from error
        if (
            min(total, passed, failures, errors, skipped) < 0
            or total != passed + failures + errors + skipped
        ):
            raise SystemExit("test_summary_inconsistent")
    return summary


def _record_passed(record: dict[str, Any]) -> bool:
    return (
        record.get("tool_outcome") == "success"
        and record.get("protocol_verdict") == "PASS"
        and record.get("security_verdict") == "PASS"
        and record.get("grounding_verdict") == "PASS"
        and record.get("presentation_verdict") == "PASS"
        and int(record.get("retry_count", 0)) == 0
        and record.get("final_classification") == "SUCCESS"
    )


def _manifest_from_records(
    records: list[dict[str, Any]],
    provider: dict[str, str],
    test_summary_file: str,
) -> dict[str, Any]:
    expected_ids = {scenario_id for scenario_id, _, _ in SCENARIOS}
    passed_count = sum(_record_passed(item) for item in records)
    protocol_failures = sum(item.get("protocol_verdict") != "PASS" for item in records)
    security_failures = sum(item.get("security_verdict") != "PASS" for item in records)
    grounding_failures = sum(item.get("grounding_verdict") != "PASS" for item in records)
    presentation_failures = sum(item.get("presentation_verdict") != "PASS" for item in records)
    presentation_warning_count = sum(
        len(item.get("presentation_warnings", [])) for item in records
    )
    expected_tools = dict(
        (scenario_id, expected_tool)
        for scenario_id, _, expected_tool in SCENARIOS
    )
    complete = (
        {item.get("scenario_id") for item in records} == expected_ids
        and len(records) == len(SCENARIOS)
        and passed_count == len(SCENARIOS)
        and protocol_failures == 0
        and security_failures == 0
        and grounding_failures == 0
        and presentation_failures == 0
        and all(
            expected_tools.get(item.get("scenario_id")) in item.get("selected_tool_names", [])
            for item in records
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "checker_version": CHECKER_VERSION,
        "status": "PASS" if complete else "BLOCKED",
        "scenario_count": len(records),
        "passed_count": passed_count,
        "protocol_passed_count": len(records) - protocol_failures,
        "protocol_failures": protocol_failures,
        "security_passed_count": len(records) - security_failures,
        "security_failures": security_failures,
        "grounding_passed_count": len(records) - grounding_failures,
        "grounding_failures": grounding_failures,
        "presentation_passed_count": len(records) - presentation_failures,
        "presentation_failures": presentation_failures,
        "presentation_warning_count": presentation_warning_count,
        "stream_parse_failures": sum(
            sum(int(round_["tool_call_parse_failure_count"])
                for round_ in item.get("provider_rounds", []))
            for item in records
        ),
        "argument_assembly_failures": sum(
            sum(int(round_["argument_assembly_failure_count"])
                for round_ in item.get("provider_rounds", []))
            for item in records
        ),
        "unknown_product_key_failures": sum(
            int(item.get("unknown_product_key_count", 0)) for item in records
        ),
        "total_attempts": sum(int(item["attempt_count"]) for item in records),
        "first_attempt_passed_count": sum(
            bool(item.get("first_attempt_passed")) for item in records
        ),
        "retried_scenarios": sum(
            int(item.get("retry_count", 0)) > 0 for item in records
        ),
        "retry_count": sum(int(item.get("retry_count", 0)) for item in records),
        "failure_scenarios": [
            {
                "scenario_id": str(item.get("scenario_id")),
                "final_classification": str(item.get("final_classification")),
                "protocol_verdict": str(item.get("protocol_verdict")),
                "security_verdict": str(item.get("security_verdict")),
                "grounding_verdict": str(item.get("grounding_verdict")),
                "presentation_verdict": str(item.get("presentation_verdict")),
            }
            for item in records if not _record_passed(item)
        ],
        "final_classifications": {
            classification: sum(
                item.get("final_classification") == classification for item in records
            )
            for classification in sorted({
                str(item.get("final_classification", "AGENT_OUTPUT_INVALID"))
                for item in records
            })
        },
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_file": "agent_acceptance.jsonl",
        "test_summary_file": test_summary_file,
    }


def _validate_summary_against_manifest(
    summary: dict[str, Any], manifest: dict[str, Any]
) -> None:
    """Keep the real-agent suite arithmetic tied to emitted evidence."""
    suites = summary.get("suites", [])
    real_agent = next(
        (suite for suite in suites if suite.get("id") == "real_agent_acceptance"),
        None,
    )
    if real_agent is None:
        raise SystemExit("test_summary_agent_acceptance_missing")
    expected = {
        "total": manifest["scenario_count"],
        "passed": manifest["passed_count"],
        "failures": manifest["scenario_count"] - manifest["passed_count"],
        "errors": 0,
        "skipped": 0,
    }
    if real_agent.get("passed_kind") == "derived_from_manifest":
        real_agent.update(expected)
        real_agent["passed_formula"] = (
            f"{expected['total']} - {expected['failures']} - "
            f"{expected['errors']} - {expected['skipped']} = {expected['passed']}"
        )
        return
    if any(
        not isinstance(real_agent.get(key), int)
        or isinstance(real_agent.get(key), bool)
        for key in expected
    ):
        raise SystemExit("test_summary_invalid")
    if any(int(real_agent.get(key, -1)) != value for key, value in expected.items()):
        raise SystemExit("test_summary_agent_acceptance_inconsistent")


def _structured_conclusion_violations(answer: str, decoded: list[Any]) -> list[str]:
    recommendation: str | None = None
    products: dict[str, set[str]] = {}
    recommendation_scores: dict[str, float] = {}
    display_priorities: dict[str, float] = {}
    recommended_flags: set[str] = set()

    def collect(value: Any) -> None:
        nonlocal recommendation
        if isinstance(value, dict):
            candidate = value.get("recommendation")
            if isinstance(candidate, str) and candidate.startswith("smart_toy_"):
                recommendation = candidate
            product_key = value.get("product_key")
            if isinstance(product_key, str):
                tokens = products.setdefault(product_key, {product_key.lower()})
                product_name = value.get("product_name")
                if isinstance(product_name, str) and product_name:
                    tokens.add(product_name.lower())
                score = value.get("recommendation_score")
                if isinstance(score, (int, float)) and not isinstance(score, bool):
                    recommendation_scores[product_key] = float(score)
                priority = value.get("display_priority")
                if isinstance(priority, (int, float)) and not isinstance(priority, bool):
                    display_priorities[product_key] = float(priority)
                if value.get("recommended") is True:
                    recommended_flags.add(product_key)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for value in decoded:
        collect(value)
    if recommendation is not None and recommendation in products:
        winner_tokens = products[recommendation]
        loser_tokens = {
            token
            for product_key, tokens in products.items()
            if product_key != recommendation
            for token in tokens
        }
        for clause in re.split(r"[。！？.!?；;，,\n]", answer.lower()):
            if "推荐" not in clause:
                continue
            names_loser = any(token in clause for token in loser_tokens)
            names_winner = any(token in clause for token in winner_tokens)
            if names_loser and not names_winner and re.search(r"被推荐|推荐(?:为|的是)?", clause):
                return ["structured_recommendation_contradiction"]

    if re.search(r"(?:建议|推荐).{0,12}(?:优先展示|提高展示优先级|展示)", answer, re.IGNORECASE):
        if recommendation_scores:
            expected: set[str] = set()
            best = max(recommendation_scores.values())
            expected.update(
                product for product, score in recommendation_scores.items()
                if score == best
            )
        elif display_priorities:
            expected = set()
            best = min(display_priorities.values())
            expected.update(
                product for product, priority in display_priorities.items()
                if priority == best
            )
        else:
            expected = set(recommended_flags)
        if expected:
            mentioned = {
                product
                for product, product_tokens in products.items()
                if any(token in answer.lower() for token in product_tokens)
            }
            if mentioned and not mentioned.intersection(expected):
                return ["structured_recommendation_contradiction"]
    return []


def _ungrounded_code_tokens(answer: str, decoded: list[Any]) -> list[str]:
    allowed = set(TOOL_NAMES)
    allowed.update(SAFE_ARGUMENTS)

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                allowed.add(str(key).lower())
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            allowed.add(value.lower())

    for value in decoded:
        collect(value)
    tokens = {
        match.group(0).lower()
        for match in re.finditer(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", answer)
    }
    return sorted(token for token in tokens if token not in allowed)


def _decoded_product_facts(
    decoded: list[Any],
) -> tuple[dict[str, dict[str, set[float]]], dict[str, set[str]], dict[str, int]]:
    """Collect product facts for validating concise derived conclusions."""
    facts: dict[str, dict[str, set[float]]] = {}
    tokens: dict[str, set[str]] = {}
    ranks: dict[str, int] = {}

    def add(product: str, metric: str, value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            facts.setdefault(product, {}).setdefault(metric, set()).add(float(value))

    def collect(value: Any, inherited_product: str | None = None) -> None:
        if isinstance(value, dict):
            product = value.get("product_key")
            if not isinstance(product, str):
                product = inherited_product
            if isinstance(value.get("product_key"), str):
                product = value["product_key"]
                aliases = CATALOG_ALIASES.get(product, set())
                tokens.setdefault(product, {product.lower(), *(item.lower() for item in aliases)})
                name = value.get("product_name")
                if isinstance(name, str) and name:
                    tokens[product].add(name.lower())
            if product:
                for key, item in value.items():
                    for canonical, aliases in _METRIC_KEY_ALIASES.items():
                        if key in aliases:
                            add(product, canonical, item)
            for key, item in value.items():
                if isinstance(item, dict) and key in {"scores", "recommendation_scores"}:
                    for score_product, score in item.items():
                        if isinstance(score_product, str):
                            add(score_product, "recommendation_score", score)
                if key == "previous":
                    continue
                collect(item, product)
        elif isinstance(value, list):
            for index, item in enumerate(value, start=1):
                if isinstance(item, dict) and isinstance(item.get("product_key"), str):
                    ranks.setdefault(item["product_key"], index)
                collect(item, inherited_product)

    for value in decoded:
        collect(value)
    return facts, tokens, ranks


def _safe_derived_interpretation(
    answer: str, decoded: list[Any]
) -> tuple[bool, list[str]]:
    """Validate natural-language comparisons against returned product facts."""
    facts, tokens, ranks = _decoded_product_facts(decoded)
    lowered = answer.lower()
    recognized = False
    violations: list[str] = []

    def product_in_suffix(product: str, suffix: str) -> bool:
        return any(token in suffix for token in tokens.get(product, set()))

    def target_for(pattern: str) -> list[tuple[str, re.Match[str]]]:
        matches: list[tuple[str, re.Match[str]]] = []
        for product, product_tokens in tokens.items():
            for token in product_tokens:
                for match in re.finditer(re.escape(token) + pattern, lowered, re.IGNORECASE):
                    if any(
                        other != product
                        and any(other_token in match.group(0) for other_token in other_tokens)
                        for other, other_tokens in tokens.items()
                    ):
                        continue
                    matches.append((product, match))
        return matches

    def compare_metric(product: str, metric: str, greater: bool) -> None:
        values = facts.get(product, {}).get(metric, set())
        mentioned = [item for item in facts if item != product and product_in_suffix(item, lowered)]
        if not mentioned:
            mentioned = [item for item in facts if item != product]
        others = [facts[item].get(metric, set()) for item in mentioned]
        other_values = {value for group in others for value in group}
        if not values or not other_values:
            violations.append(f"derived_comparison_missing:{product}:{metric}")
            return
        left = max(values)
        right = max(other_values) if greater else min(other_values)
        if (greater and left <= right) or (not greater and left >= right):
            violations.append(f"derived_comparison_direction:{product}:{metric}")

    explicit_metric_patterns = (
        (r"(?:互动(?:次数|量|数)|完整互动)", "interaction_count"),
        (r"平均(?:每次)?持有(?:时长)?", "avg_holding_seconds"),
        (r"(?:累计|总计)持有(?:时长)?", "total_holding_seconds"),
    )
    for product, product_tokens in tokens.items():
        for other, other_tokens in tokens.items():
            if other == product:
                continue
            for product_token in product_tokens:
                for other_token in other_tokens:
                    for metric_pattern, metric in explicit_metric_patterns:
                        greater_patterns = (
                            re.escape(product_token) + r".{0,16}" + metric_pattern
                            + r".{0,24}(?:高于|超过)\s*" + re.escape(other_token),
                            re.escape(product_token) + r".{0,16}" + metric_pattern
                            + r".{0,24}比\s*" + re.escape(other_token)
                            + r".{0,16}(?:更长|更多|更高)",
                        )
                        lower_patterns = (
                            re.escape(product_token) + r".{0,16}" + metric_pattern
                            + r".{0,24}(?:低于|少于)\s*" + re.escape(other_token),
                            re.escape(product_token) + r".{0,16}" + metric_pattern
                            + r".{0,24}比\s*" + re.escape(other_token)
                            + r".{0,16}(?:更短|更少|更低)",
                        )
                        if any(re.search(pattern, lowered) for pattern in greater_patterns):
                            recognized = True
                            compare_metric(product, metric, True)
                        if any(re.search(pattern, lowered) for pattern in lower_patterns):
                            recognized = True
                            compare_metric(product, metric, False)

    comparison_patterns = (
        (r".{0,28}(?:互动(?:次数|量|数)|完整互动).{0,10}(?:更多|较多|更高|最高|更活跃)", "interaction_count", True),
        (r".{0,28}(?:互动(?:次数|量|数)|完整互动).{0,10}(?:更少|较少|更低)", "interaction_count", False),
        (r".{0,28}平均(?:每次)?持有.{0,16}(?:更长|较长|更高|高于)", "avg_holding_seconds", True),
        (r".{0,28}平均(?:每次)?持有.{0,10}(?:更短|较短|更低)", "avg_holding_seconds", False),
        (r".{0,28}(?:累计|总计)持有.{0,10}(?:更长|较长|更高)", "total_holding_seconds", True),
        (r".{0,28}(?:累计|总计)持有.{0,10}(?:更短|较短|更低)", "total_holding_seconds", False),
    )
    for pattern, metric, greater in comparison_patterns:
        for product, _ in target_for(pattern):
            recognized = True
            compare_metric(product, metric, greater)

    metric_before_product = (
        (r"(?:互动(?:次数|量|数)|完整互动).{0,12}", "interaction_count", True, r"(?:更多|较多|更高|更活跃)"),
        (r"平均(?:每次)?持有.{0,12}", "avg_holding_seconds", True, r"(?:更长|较长|更高)"),
        (r"(?:累计|总计)持有.{0,12}", "total_holding_seconds", True, r"(?:更长|较长|更高)"),
    )
    for prefix, metric, greater, suffix in metric_before_product:
        for product, product_tokens in tokens.items():
            matches = [
                match
                for token in product_tokens
                for match in re.finditer(
                    prefix + re.escape(token) + r".{0,8}" + suffix, lowered
                )
                if not re.search(r"(?:比|高于|低于)[^。！？\n]{0,12}" + re.escape(token), match.group(0))
            ]
            if matches:
                recognized = True
                compare_metric(product, metric, greater)

    for product, _ in target_for(r".{0,18}(?:互动热度).{0,10}(?:排名|第)一"):
        recognized = True
        if ranks.get(product) != 1:
            violations.append(f"derived_rank_direction:{product}:1")

    trend_patterns = (
        (r".{0,18}(?:趋势|热度).{0,8}(?:上升|上涨|up)", "up"),
        (r".{0,18}(?:趋势|热度).{0,8}(?:下降|下跌|down)", "down"),
        (r".{0,18}(?:趋势|热度).{0,8}(?:持平|flat)", "flat"),
    )
    for pattern, expected in trend_patterns:
        for product, _ in target_for(pattern):
            recognized = True
            returned = {
                str(item.get("interaction_trend"))
                for item in decoded
                if isinstance(item, dict)
                and item.get("product_key") == product
                and "interaction_trend" in item
            }
            if returned and expected not in returned:
                violations.append(f"derived_trend_direction:{product}:{expected}")

    if re.search(r"优先展示|提高展示优先级|降低展示优先级|保持展示", lowered):
        recognized = True
        selected = [
            product for product, product_tokens in tokens.items()
            if any(token in lowered for token in product_tokens)
            and re.search(
                r"(?:优先展示|提高展示优先级|降低展示优先级|保持展示)[^。！？\n]{0,20}"
                + "|" + r"(?:优先展示|提高展示优先级|降低展示优先级|保持展示)",
                lowered,
            )
        ]
        if selected:
            priorities = {
                product: min(facts.get(product, {}).get("display_priority", {999}))
                for product in facts
            }
            if priorities and selected[0] in priorities and priorities[selected[0]] != min(priorities.values()):
                violations.append(f"derived_display_direction:{selected[0]}")

    if re.search(r"摄像头数据.{0,12}(?:不能|无法|不能判断).{0,12}(?:兴趣|偏好)", lowered):
        recognized = True
    if re.search(r"(?:不涉及|不是|不代表).{0,20}(?:购买|成交|销量|物理摆放)", lowered):
        recognized = True

    return recognized, sorted(set(violations))


def _only_grounded_recommendation_attention(
    answer: str, decoded: list[Any]
) -> bool:
    """Allow recommendation wording without treating attention as a metric."""
    product_tokens: dict[str, set[str]] = {}
    recommended_products: set[str] = set()

    def collect(value: Any, inherited_product: str | None = None) -> None:
        if isinstance(value, dict):
            product = value.get("product_key")
            if not isinstance(product, str):
                product = inherited_product
            if isinstance(value.get("product_key"), str):
                tokens = product_tokens.setdefault(
                    product,
                    {product.lower(), *(alias.lower() for alias in CATALOG_ALIASES.get(product, set()))},
                )
                name = value.get("product_name")
                if isinstance(name, str) and name:
                    tokens.add(name.lower())
            candidate = value.get("recommendation")
            if isinstance(candidate, str) and candidate in CATALOG_ALIASES:
                recommended_products.add(candidate)
            elif isinstance(candidate, str) and candidate and product:
                recommended_products.add(product)
            if value.get("recommended") is True and product:
                recommended_products.add(product)
            for item in value.values():
                collect(item, product)
        elif isinstance(value, list):
            for item in value:
                collect(item, inherited_product)

    for value in decoded:
        collect(value)
    if not recommended_products:
        return False

    matches = list(UNSUPPORTED_CLAIMS["unsupported_engagement_claim"].finditer(answer))
    if not matches:
        return False
    lowered = answer.lower()
    for match in matches:
        if (
            match.group(0) != "关注"
            or answer[match.end():match.end() + 1] == "度"
            or answer[max(0, match.start() - 1):match.start()] == "高"
        ):
            return False
        start = max(
            lowered.rfind("。", 0, match.start()),
            lowered.rfind("；", 0, match.start()),
            lowered.rfind("\n", 0, match.start()),
        ) + 1
        ends = [
            position for marker in ("。", "；", "\n")
            if (position := lowered.find(marker, match.end())) >= 0
        ]
        end = min(ends) if ends else len(lowered)
        clause = lowered[start:end]
        mentioned = {
            product for product, tokens in product_tokens.items()
            if any(token in clause for token in tokens)
        }
        recommendation_wording = bool(re.search(
            r"推荐.{0,12}(?:优先)?关注|"
            r"(?:适合|建议).{0,10}(?:持续|重点|优先)?关注|"
            r"(?:值得)?保持关注|"
            r"(?:重点|优先)关注",
            clause,
        ))
        if not recommendation_wording:
            return False
        if mentioned:
            if not mentioned.issubset(recommended_products):
                return False
        elif not set(product_tokens).issubset(recommended_products):
            return False
    return True


def _answer_is_grounded(
    scenario_id: str, answer: str, result_payloads: list[str]
) -> tuple[bool, list[str]]:
    decoded: list[Any] = []
    for payload in result_payloads:
        try:
            decoded.append(json.loads(payload))
        except json.JSONDecodeError:
            return False, []
    if not decoded:
        return False, []
    if all(value in (None, [], {}) for value in decoded):
        lowered = answer.lower()
        explicit_empty = any(
            marker in lowered for marker in ("没有", "未", "no ", "none", "empty")
        )
        unsupported = _unsupported_claims(answer)
        code_violations = _ungrounded_code_tokens(answer, decoded)
        numeric_claims = _numeric_claims(answer)
        grounded = (
            explicit_empty and not unsupported and not numeric_claims
            and not code_violations
        )
        checks = ["explicit_empty_result"] if explicit_empty else []
        checks.extend(f"rejected:{claim}" for claim in unsupported)
        checks.extend(f"rejected:ungrounded_code:{token}" for token in code_violations)
        if numeric_claims:
            checks.append("rejected:ungrounded_numeric_claim")
        return grounded, checks

    product_tokens: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "product_key" and isinstance(item, str):
                    product_tokens.add(item.lower())
                    product_tokens.update(alias.lower() for alias in CATALOG_ALIASES.get(item, set()))
                elif key == "product_name" and isinstance(item, str):
                    product_tokens.add(item.lower())
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for value in decoded:
        collect(value)
    lowered = answer.lower()
    product_grounded = bool(product_tokens) and any(
        token in lowered for token in product_tokens
    )
    metric_values: set[float] = set()

    def collect_metrics(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    metric_values.add(float(item))
                collect_metrics(item)
        elif isinstance(value, list):
            metric_values.update(float(index) for index in range(1, len(value) + 1))
            for item in value:
                collect_metrics(item)

    for value in decoded:
        collect_metrics(value)
    claims = _numeric_claims(answer)
    metrics_grounded = bool(claims) and all(
        any(_metric_matches(claim, precision, value) for value in metric_values)
        for claim, precision in claims
    )
    unsupported = _unsupported_claims(answer)
    if (
        "unsupported_engagement_claim" in unsupported
        and _only_grounded_recommendation_attention(answer, decoded)
    ):
        unsupported.remove("unsupported_engagement_claim")
    structured_violations = _structured_conclusion_violations(answer, decoded)
    code_violations = _ungrounded_code_tokens(answer, decoded)
    product_metric_violations = _product_metric_violations(answer, decoded)
    derived_recognized, derived_violations = _safe_derived_interpretation(
        answer, decoded
    )
    if not claims:
        metrics_grounded = derived_recognized and not derived_violations
    if scenario_id == "rising-products":
        trend_grounded = "上涨" in answer or bool(
            re.search(r"\bup\b", lowered)
        )
        grounded = (
            product_grounded and trend_grounded and metrics_grounded
            and not unsupported and not structured_violations
            and not code_violations and not product_metric_violations
            and not derived_violations
        )
        checks = ["product_reference", "trend_direction", "all_numeric_claims"]
    else:
        grounded = (
            product_grounded and metrics_grounded
            and not unsupported and not structured_violations
            and not code_violations and not product_metric_violations
            and not derived_violations
        )
        checks = ["product_reference", "all_numeric_claims"]
    checks.extend(f"rejected:{claim}" for claim in unsupported)
    checks.extend(f"rejected:{claim}" for claim in structured_violations)
    checks.extend(f"rejected:{claim}" for claim in derived_violations)
    checks.extend(f"rejected:ungrounded_code:{token}" for token in code_violations)
    checks.extend(
        f"rejected:misattributed_metric:{item}"
        for item in product_metric_violations
    )
    if not metrics_grounded:
        checks.append("rejected:ungrounded_numeric_claim")
    return grounded, checks


def _run_scenario(
    *,
    java: str,
    jar: Path,
    config: Path,
    java_repo: Path,
    scenario_id: str,
    question: str,
    expected_tool: str,
    provider: dict[str, str],
    timeout_seconds: int,
    capture_raw: bool = False,
) -> tuple[dict[str, Any], bool]:
    started = time.monotonic()
    timestamp = datetime.now(timezone.utc).isoformat()
    base = {
        "run_id": uuid.uuid4().hex,
        "scenario_id": scenario_id,
        "question": question,
        "trusted_scope": {
            "store_id": "store-1", "camera_id": "cam02",
            "start_at": "2026-08-20T13:00:00",
            "end_at": "2026-08-20T14:00:00",
        },
        "selected_tool_names": [],
        "safe_arguments": [],
        "tool_outcome": "not_run",
        "result_hash": None,
        "protocol_verdict": "BLOCKED",
        "security_verdict": "BLOCKED",
        "final_answer": "",
        "grounding_verdict": "BLOCKED",
        "grounding_checks": [],
        "presentation_verdict": "BLOCKED",
        "presentation_checks": [],
        "presentation_warnings": [],
        "unknown_product_key_count": 0,
        "timestamp": timestamp,
        "model_provider_identifier": provider,
        "provider": provider["provider"],
        "model": provider["model"],
        "agent_invocation_count": 1,
        "advertised_tool_count": 0,
        "tool_call_count": 0,
        "mcp_tool_outcome": "not_called",
        "provider_finish_reason": "",
        "answer_non_empty": False,
        "provider_rounds": [],
        "retry_count": 0,
        "duration_ms": 0,
        "final_classification": "AGENT_OUTPUT_INVALID",
    }

    def finish(classification: str) -> tuple[dict[str, Any], bool]:
        base["duration_ms"] = round((time.monotonic() - started) * 1000)
        base["final_classification"] = classification
        passed = (
            base["tool_outcome"] == "success"
            and base["protocol_verdict"] == "PASS"
            and base["security_verdict"] == "PASS"
            and base["grounding_verdict"] == "PASS"
            and base["presentation_verdict"] == "PASS"
            and base["retry_count"] == 0
            and classification == "SUCCESS"
        )
        return base, passed

    try:
        completed = subprocess.run(
            [
                java,
                "-Dfile.encoding=UTF-8",
                "-Dstdout.encoding=UTF-8",
                "-Dstderr.encoding=UTF-8",
                "-jar", str(jar), str(config), "-p", question,
             "--output-format", "stream-json"],
            cwd=java_repo,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        base["tool_outcome"] = "agent_timeout"
        return finish("AGENT_TIMEOUT")
    raw_stdout = completed.stdout.decode("utf-8", errors="replace")
    if capture_raw:
        base["_raw_stdout"] = raw_stdout
    try:
        stdout = completed.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        base["tool_outcome"] = "agent_output_invalid"
        return finish("AGENT_OUTPUT_INVALID")
    events = _parse_events(stdout)
    diagnostics = [
        item for item in events if item.get("type") == "provider_diagnostics"
    ]
    base["provider_rounds"] = [
        {
            "provider_round": int(item.get("provider_round", index + 1)),
            "advertised_tool_count": int(item.get("advertised_tool_count", 0)),
            "tool_call_delta_count": int(item.get("tool_call_delta_count", 0)),
            "tool_call_complete_count": int(item.get("tool_call_complete_count", 0)),
            "finish_reason": str(item.get("finish_reason") or ""),
            "answer_non_empty": bool(item.get("answer_non_empty", False)),
            "tool_call_parse_failure_count": int(item.get("tool_call_parse_failure_count", 0)),
            "argument_assembly_failure_count": int(item.get("argument_assembly_failure_count", 0)),
            "classification": str(item.get("classification") or ""),
        }
        for index, item in enumerate(diagnostics)
    ]
    if diagnostics:
        base["advertised_tool_count"] = max(
            int(item.get("advertised_tool_count", 0)) for item in diagnostics
        )
        base["provider_finish_reason"] = str(
            diagnostics[-1].get("finish_reason") or ""
        )
    base["retry_count"] = sum(item.get("type") == "retry" for item in events)
    if completed.returncode != 0:
        base["tool_outcome"] = "agent_process_failed"
        return finish("AGENT_PROCESS_FAILED")
    if not events:
        base["tool_outcome"] = "agent_output_invalid"
        return finish("AGENT_OUTPUT_INVALID")

    uses = [item for item in events if item.get("type") == "tool_use"]
    retail_uses = [
        item for item in uses
        if str(item.get("tool_name", "")).startswith("mcp__retailvision__")
    ]
    selected = [
        str(item["tool_name"]).removeprefix("mcp__retailvision__")
        for item in retail_uses
    ]
    raw_arguments = [item.get("args") for item in retail_uses]
    base["selected_tool_names"] = selected
    base["tool_call_count"] = len(selected)
    base["safe_arguments"] = [_safe_arguments(value) for value in raw_arguments]
    results = [
        item for item in events
        if item.get("type") == "tool_result"
        and str(item.get("tool_name", "")).startswith("mcp__retailvision__")
    ]
    final_events = [item for item in events if item.get("type") == "result"]
    final_answer = str(final_events[-1].get("result") or "") if final_events else ""
    base["final_answer"] = final_answer
    base["answer_non_empty"] = bool(final_answer.strip())

    expected_results = [
        item for item in results
        if str(item.get("tool_name", "")).removeprefix("mcp__retailvision__")
        == expected_tool
    ]
    result_payloads = [str(item.get("output") or "") for item in expected_results]
    retail_results = [
        item for item in events
        if item.get("type") == "tool_result"
        and str(item.get("tool_name", "")).startswith("mcp__retailvision__")
    ]
    if retail_results:
        base["mcp_tool_outcome"] = (
            "failed" if any(bool(item.get("is_error")) for item in retail_results)
            else "success"
        )
    if result_payloads:
        canonical = "\n".join(result_payloads).encode("utf-8")
        base["result_hash"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    base["unknown_product_key_count"] = sum(
        "product_key_unknown" in payload for payload in result_payloads
    )
    security_failed = _contains_forbidden({
        "args": raw_arguments, "results": result_payloads,
        "answer": final_answer,
    })
    base["security_verdict"] = "FAIL" if security_failed else "PASS"

    has_expected = expected_tool in selected
    tool_failed = not expected_results or any(
        bool(item.get("is_error")) for item in expected_results
    )
    has_answer = bool(final_answer.strip())
    grounded, grounding_checks = _answer_is_grounded(
        scenario_id, final_answer, result_payloads
    ) if has_answer else (False, [])
    base["grounding_checks"] = grounding_checks
    base["grounding_verdict"] = "PASS" if grounded else "FAIL"
    decoded_results: list[Any] = []
    for payload in result_payloads:
        try:
            decoded_results.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    presentation_checks = (
        _presentation_violations(final_answer, decoded_results)
        if has_answer else ["empty_final_answer"]
    )
    base["presentation_checks"] = presentation_checks
    base["presentation_warnings"] = (
        _presentation_warnings(final_answer, decoded_results)
        if has_answer else []
    )
    base["presentation_verdict"] = "FAIL" if presentation_checks else "PASS"
    diagnostic_failures = [
        str(item.get("classification")) for item in diagnostics
        if item.get("classification") in {
            "PROVIDER_TOOL_CALL_MISSING",
            "PROVIDER_STREAM_INCOMPLETE",
            "STREAM_TOOL_CALL_PARSE_FAILED",
            "TOOL_ARGUMENT_ASSEMBLY_FAILED",
        }
    ]
    protocol_passed = (
        has_expected and not tool_failed and not diagnostic_failures
        and base["retry_count"] == 0
        and base["unknown_product_key_count"] == 0
    )
    base["protocol_verdict"] = "PASS" if protocol_passed else "FAIL"
    if not has_expected:
        base["tool_outcome"] = "expected_tool_not_selected"
    elif tool_failed:
        base["tool_outcome"] = "tool_call_failed"
    else:
        base["tool_outcome"] = "success"
    if security_failed:
        base["safe_arguments"] = []
        base["final_answer"] = "[redacted:security_failure]"
    if diagnostic_failures and not selected:
        classification = diagnostic_failures[0]
    elif not has_expected:
        classification = "MODEL_NO_TOOL_CALL" if not selected else "MODEL_WRONG_TOOL_CALL"
    elif tool_failed:
        classification = "MCP_TOOL_CALL_FAILED"
    elif not has_answer:
        classification = "EMPTY_FINAL_ANSWER"
    elif security_failed:
        classification = "SECURITY_FAILED"
    elif not grounded:
        classification = "GROUNDING_FAILED"
    elif presentation_checks:
        classification = "PRESENTATION_FAILED"
    else:
        classification = "SUCCESS"
    return finish(classification)


# Public runner API used by the deterministic stability gate. Keeping these
# wrappers stable avoids coupling release evidence to private implementation
# names while preserving the existing acceptance runner behavior.
def build_config(source: dict[str, Any], database: Path, python: str) -> dict[str, Any]:
    return _build_config(source, database, python)


def database_url(path: Path) -> str:
    return _database_url(path)


def prepare_output_directory(output_dir: Path) -> None:
    _prepare_output_directory(output_dir)


def provider_identity(provider: dict[str, Any]) -> dict[str, str]:
    return _provider_identity(provider)


def run_scenario(**kwargs: Any) -> tuple[dict[str, Any], bool]:
    return _run_scenario(**kwargs)


def write_json(path: Path, value: Any) -> None:
    _write_json(path, value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-repo", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--test-summary-source", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--max-attempts", type=int, default=1,
                        help="Deprecated compatibility option; acceptance always invokes each scenario once.")
    args = parser.parse_args(argv)

    jar = args.java_repo.resolve() / "target" / "mewcode.jar"
    if not jar.is_file():
        raise SystemExit("java_artifact_missing")
    if not args.provider_config.is_file():
        raise SystemExit("provider_config_missing")
    source = yaml.safe_load(args.provider_config.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise SystemExit("provider_config_missing")
    providers = source.get("providers")
    if not isinstance(providers, list) or not providers:
        raise SystemExit("provider_config_missing")
    provider = _provider_identity(providers[0])
    test_summary = _load_test_summary(args.test_summary_source)

    _prepare_output_directory(args.output_dir)
    records: list[dict[str, Any]] = []
    raw_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="retailvision-agent-acceptance-") as temp:
        temporary = Path(temp)
        database = temporary / "fixture.db"
        fixture = subprocess.run(
            [args.python, str(PROJECT_ROOT / "scripts" / "create_product_analytics_acceptance_fixture.py"),
             "--database-url", _database_url(database)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if fixture.returncode != 0 or not database.is_file():
            raise SystemExit("agent_process_failed")
        config = temporary / "mewcode-acceptance.yaml"
        config.write_text(
            yaml.safe_dump(
                _build_config(source, database, args.python),
                allow_unicode=True, sort_keys=False,
            ),
            encoding="utf-8",
        )
        for scenario_id, question, expected_tool in SCENARIOS:
            attempt_outcomes: list[str] = []
            passed = False
            first_attempt_passed = False
            record: dict[str, Any] = {}
            # R5-04 evidence is one independent invocation per scenario. Never
            # hide a failed first call behind acceptance retries.
            for attempt in range(1, 2):
                record, passed = _run_scenario(
                    java=args.java, jar=jar, config=config,
                    java_repo=args.java_repo.resolve(), scenario_id=scenario_id,
                    question=question, expected_tool=expected_tool,
                    provider=provider, timeout_seconds=args.timeout_seconds,
                    capture_raw=True,
                )
                if attempt == 1:
                    first_attempt_passed = passed
                attempt_outcomes.append(str(record["tool_outcome"]))
                if passed:
                    break
            record["attempt_count"] = len(attempt_outcomes)
            record["first_attempt_passed"] = first_attempt_passed
            record["prior_attempt_outcomes"] = attempt_outcomes[:-1]
            raw_records.append({
                "scenario_id": scenario_id,
                "raw_stdout": str(record.pop("_raw_stdout", "")),
            })
            records.append(record)

    jsonl = args.output_dir / "agent_acceptance.jsonl"
    jsonl.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    (args.output_dir / "raw_agent_output.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in raw_records),
        encoding="utf-8",
    )
    manifest = _manifest_from_records(
        records, provider, "test_summary.json",
    )
    _validate_summary_against_manifest(test_summary, manifest)
    _write_json(args.output_dir / "test_summary.json", test_summary)
    _write_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
