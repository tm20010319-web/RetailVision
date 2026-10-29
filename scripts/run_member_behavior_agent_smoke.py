"""Run the two MB-01A autonomous scenarios once against a temporary fixture."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.models import Base, BehaviorEvent, Member  # noqa: E402
from scripts.run_store_intelligence_agent_acceptance import _parse_events  # noqa: E402


TITLE = "CONTROLLED FIXTURE — NOT REAL MEMBER EVIDENCE"
SCENARIOS = (
    (
        "mb01-summary",
        "分析高俊杰在当前可信范围内的商品行为汇总。",
        "get_member_behavior_summary",
    ),
    (
        "mb01-timeline",
        "列出高俊杰在当前可信范围内的商品行为时间线。",
        "list_member_behavior_timeline",
    ),
)
FORBIDDEN = (
    "product_key", "product_track_token", "normalized_fact_ref",
    "customer_track_token", "track_token", "binding_id", "evaluation_id",
    "identity_source", "identity_status", "details", "artifact",
    "phone", "手机号", "积分", "消费金额", "purchase_amount", "visit",
    "preference", "confidence", "置信度", "started_at", "ended_at",
    "event_id", "interaction_session_id", "area_id", "identity_binding_level",
    "database", "sql", "artifact", "track", "provenance",
)

SUMMARY_KEYS = {
    "status", "member", "scope", "behavior_summary", "interacted_products",
    "data_quality",
}
TIMELINE_KEYS = {"status", "member", "scope", "timeline", "has_more"}
MEMBER_KEYS = {"member_id", "display_name", "member_tier"}
SCOPE_KEYS = {"store_id", "camera_id", "camera_scope", "start_at", "end_at", "product_name"}
SUMMARY_FACT_KEYS = {
    "total_behavior_count", "behavior_counts", "holding_count",
    "total_holding_seconds", "last_behavior_at",
}
PRODUCT_KEYS = {
    "product_ref", "product_name", "product_name_status", "behavior_count",
    "behavior_counts", "total_holding_seconds", "holding_duration_status",
    "last_behavior_at",
}
TIMELINE_ITEM_KEYS = {
    "occurred_at", "behavior_type", "behavior_label", "product_ref",
    "product_name", "product_name_status", "duration_seconds", "duration_status",
}
BEHAVIOR_TYPES = {
    "merchandise_pick_up": "拿起商品",
    "merchandise_holding": "持有商品",
    "merchandise_put_down": "放下商品",
}
MEMBER_TOOL_NAMES = {
    "get_member_behavior_summary",
    "list_member_behavior_timeline",
}


def _exact_keys(value: Any, expected: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected


def _validate_public_payload(payload: Any, expected_tool: str) -> bool:
    """Validate the frozen public projection without accepting extra nesting."""
    if not isinstance(payload, dict):
        return False
    root_keys = SUMMARY_KEYS if expected_tool == "get_member_behavior_summary" else TIMELINE_KEYS
    if not _exact_keys(payload, root_keys):
        return False
    if not _exact_keys(payload["member"], MEMBER_KEYS) or not _exact_keys(payload["scope"], SCOPE_KEYS):
        return False
    member = payload["member"]
    scope = payload["scope"]
    if not isinstance(member["member_id"], str) or not isinstance(member["display_name"], str):
        return False
    if member["member_tier"] is not None and member["member_tier"] not in {"diamond", "platinum", "gold", "silver", "bronze"}:
        return False
    if not isinstance(scope["store_id"], str) or not isinstance(scope["start_at"], str) or not isinstance(scope["end_at"], str):
        return False
    if scope["camera_scope"] not in {"single", "store_all"}:
        return False
    if scope["camera_scope"] == "single" and not isinstance(scope["camera_id"], str):
        return False
    if scope["camera_scope"] == "store_all" and scope["camera_id"] is not None:
        return False
    if scope["product_name"] is not None and not isinstance(scope["product_name"], str):
        return False
    if expected_tool == "get_member_behavior_summary":
        summary = payload["behavior_summary"]
        if not _exact_keys(summary, SUMMARY_FACT_KEYS):
            return False
        if not _exact_keys(summary["behavior_counts"], set(BEHAVIOR_TYPES)):
            return False
        if not _exact_keys(payload["data_quality"], {"holding_duration"}):
            return False
        if payload["data_quality"]["holding_duration"] not in {"not_applicable", "available", "partial_unavailable", "unavailable"}:
            return False
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in summary["behavior_counts"].values()):
            return False
        products = payload["interacted_products"]
        if not isinstance(products, list):
            return False
        for item in products:
            if not _exact_keys(item, PRODUCT_KEYS):
                return False
            if not _exact_keys(item["behavior_counts"], set(BEHAVIOR_TYPES)):
                return False
            if not isinstance(item["product_ref"], str) or not isinstance(item["behavior_count"], int):
                return False
            if item["product_name"] is not None and not isinstance(item["product_name"], str):
                return False
            if item["product_name_status"] not in {"available", "unavailable"}:
                return False
            if item["holding_duration_status"] not in {"not_applicable", "available", "partial_unavailable", "unavailable"}:
                return False
    else:
        timeline = payload["timeline"]
        if not isinstance(timeline, list) or not isinstance(payload["has_more"], bool):
            return False
        for item in timeline:
            if not _exact_keys(item, TIMELINE_ITEM_KEYS):
                return False
            if item.get("behavior_type") not in BEHAVIOR_TYPES:
                return False
            if item.get("behavior_label") != BEHAVIOR_TYPES[item["behavior_type"]]:
                return False
            if not isinstance(item["occurred_at"], str) or not isinstance(item["product_ref"], str):
                return False
            if item["product_name"] is not None and not isinstance(item["product_name"], str):
                return False
            if item["product_name_status"] not in {"available", "unavailable"}:
                return False
            if item["duration_status"] not in {"not_applicable", "available", "unavailable"}:
                return False
    return True


def _answer_fact_consistent(
    answer: str,
    payload: Any,
    *,
    product_names: set[str] | None = None,
    time_values: list[str] | None = None,
) -> bool:
    """Bind only semantic count, duration, time, and product claims to facts."""
    if not isinstance(answer, str) or not isinstance(payload, dict):
        return False
    if any(marker in answer.lower() for marker in FORBIDDEN):
        return False
    display_name = payload.get("member", {}).get("display_name")
    for claimed_name in re.findall(
        r"(?:^|[。；;\n])\s*([\u4e00-\u9fff]{2,4})(?=拿起|拿取|拾取|持有|放下|放回|与)",
        answer,
    ):
        if claimed_name in {"大约", "约", "累计", "总共", "合计", "一共", "仅呈现"}:
            continue
        if claimed_name != display_name:
            return False
    if "behavior_summary" in payload:
        summary = payload["behavior_summary"]
        counts = summary.get("behavior_counts", {})
        total = summary.get("total_behavior_count")
    else:
        timeline = payload.get("timeline", [])
        counts = {key: sum(item.get("behavior_type") == key for item in timeline if isinstance(item, dict)) for key in BEHAVIOR_TYPES}
        total = len(timeline)
    count_claims = (
        (r"(?:拿起|拿取|拾取|pickup)[^\d]{0,8}(\d+(?:\.\d+)?)\s*次", "merchandise_pick_up"),
        (r"(?:持有|持握|握持|holding)[^\d]{0,8}(\d+(?:\.\d+)?)\s*次", "merchandise_holding"),
        (r"(?:放下|放回|put\s*down)[^\d]{0,8}(\d+(?:\.\d+)?)\s*次", "merchandise_put_down"),
        (r"(?:总共|合计|共计|总计|一共|共|total)[^\d]{0,8}(\d+(?:\.\d+)?)\s*次(?!\s*(?:记录|情况))", "__total__"),
    )
    for pattern, key in count_claims:
        for match in re.finditer(pattern, answer, re.IGNORECASE):
            expected = total if key == "__total__" else counts.get(key)
            if expected is None or float(match.group(1)) != float(expected):
                return False
    # Bind total claims expressed with the domain noun, while excluding generic
    # "N records" statements from the completeness gate below.
    for match in re.finditer(
        r"(?:总行为数|商品行为|商品互动|互动|行为)[^\d]{0,8}(\d+(?:\.\d+)?)\s*次",
        answer,
        re.IGNORECASE,
    ):
        if float(match.group(1)) != float(total):
            return False
    for match in re.finditer(
        r"(?:总行为次数|总行为数|行为总数)\s*(?:\*\*)?\s*[：:]\s*(\d+(?:\.\d+)?)",
        answer,
    ):
        if float(match.group(1)) != float(total):
            return False
    product_names = _payload_product_names(payload) if product_names is None else product_names
    for mention in _explicit_product_mentions(answer):
        if mention not in product_names and mention not in {"未命名商品", "一件未命名商品", "商品名称不可用", "名称不可用"}:
            return False
    time_values = _time_values(payload) if time_values is None else time_values
    for hour, minute in re.findall(r"\b(\d{1,2}):(\d{2})\b", answer):
        if not any(f"T{int(hour):02d}:{minute}" in value or f" {int(hour):02d}:{minute}" in value for value in time_values):
            return False
    for date_value in re.findall(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", answer):
        if not any(date_value.replace("/", "-") in value for value in time_values):
            return False
    return True


def _answer_complete(answer: str, payload: Any) -> bool:
    """Require the smallest useful answer for the selected public projection."""
    if not isinstance(answer, str) or not isinstance(payload, dict):
        return False
    display_name = payload.get("member", {}).get("display_name")
    if not isinstance(display_name, str) or not display_name.strip() or display_name not in answer:
        return False
    if "behavior_summary" in payload:
        summary = payload.get("behavior_summary", {})
        total = summary.get("total_behavior_count")
        total_claimed = any(
            float(match.group(1)) == float(total)
            for pattern in (
                r"(?:总共|合计|共计|总计|一共|共|total)[^\d]{0,8}(\d+(?:\.\d+)?)\s*次(?!\s*(?:记录|情况))",
                r"(?:总行为数|商品行为|商品互动|互动|行为)[^\d]{0,8}(\d+(?:\.\d+)?)\s*次",
                r"(?:总行为次数|总行为数|行为总数)\s*(?:\*\*)?\s*[：:]\s*(\d+(?:\.\d+)?)",
            )
            for match in re.finditer(pattern, answer, re.IGNORECASE)
        )
        has_product_label = bool(
            re.search(r"(?:商品|产品|物品)\s*[：:]", answer)
            and _explicit_product_mentions(answer)
        )
        has_behavior_context = "商品行为" in answer or has_product_label or any(
            re.search(pattern, answer, re.IGNORECASE)
            for pattern in (r"拿起|拿取|拾取", r"持有|持握|握持", r"放下|放回", r"互动")
        )
        return total_claimed and has_behavior_context
    timeline = payload.get("timeline", [])
    if not isinstance(timeline, list):
        return False
    required_verbs = {
        "merchandise_pick_up": r"拿起|拿取|拾取",
        "merchandise_holding": r"持有|持握|握持",
        "merchandise_put_down": r"放下|放回",
    }
    return all(
        not isinstance(item, dict)
        or item.get("behavior_type") not in required_verbs
        or re.search(required_verbs[item["behavior_type"]], answer)
        for item in timeline
    )


def _explicit_product_mentions(answer: str) -> set[str]:
    """Extract concrete product mentions without inventing a product vocabulary."""
    generic = {
        "商品", "产品", "物品", "未命名商品", "一件未命名商品", "商品名称不可用", "名称不可用",
        "发生了", "发生", "互动", "行为", "情况", "记录", "一次", "一件", "完整", "完整的", "的", "次数",
    }
    mentions: set[str] = set()
    def add(value: str) -> None:
        value = _clean_product_candidate(value, generic)
        if value:
            mentions.add(value)

    # A label is product context; stop before count/behavior suffixes.
    for match in re.finditer(r"(?:商品|产品|物品)\s*[：:]\s*([^，。；,.;：:!?！？()（）\n]+)", answer):
        add(match.group(1))

    # Action context supports natural prose and strips an intervening generic
    # noun ("拿起商品智能熊"). Duration phrases are rejected by the cleaner.
    for match in re.finditer(r"(?:拿起|拿取|拾取|持有|持握|握持|放下|放回|与)\s*(?:了|着)?\s*([^，。、；,.;：:!?！？()（）/|\\\n]+)", answer):
        candidate = match.group(1).strip()
        if candidate.startswith(("商品", "产品", "物品")):
            candidate = candidate[2:]
        add(candidate)

    # Quoted content is accepted only when the quote itself follows a product
    # label or an action; member-name quotes therefore remain non-products.
    for match in re.finditer(r"[\"“‘]([^\"”’]+)[\"”’]", answer):
        prefix = answer[max(0, match.start() - 16):match.start()]
        if re.search(r"(?:商品|产品|物品)\s*[：:]\s*$", prefix) or re.search(
            r"(?:拿起|拿取|拾取|持有|持握|握持|放下|放回|与)\s*(?:了|着)?\s*$", prefix
        ):
            add(match.group(1))
    return mentions


def _clean_product_candidate(value: str, generic: set[str]) -> str | None:
    delimiters = "，。；,.;:：!?！？()（）\"“”‘’|`*"
    value = value.strip().strip(delimiters)
    value = re.split(r"(?:总共|合计|共计|总计|一共|共|发生|行为|互动|记录|情况)", value, maxsplit=1)[0].strip()
    value = value.strip(delimiters)
    value = re.sub(r"^(?:商品|产品|物品)\s*", "", value)
    value = value.strip(delimiters)
    if not value or value in generic or value.isdigit() or value[0].isdigit():
        return None
    if value.startswith("时长"):
        return None
    if re.fullmatch(r"(?:大约|约)?\s*\d+(?:\.\d+)?\s*(?:秒|秒钟|分钟|小时|天)(?:左右|上下)?", value):
        return None
    return value


def _payload_product_names(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("interacted_products", "timeline"):
        values = payload.get(key, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and isinstance(item.get("product_name"), str):
                name = item["product_name"].strip()
                if name:
                    names.add(name)
    return names


def _time_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    scope = payload.get("scope")
    if isinstance(scope, dict):
        values.extend(str(scope[key]) for key in ("start_at", "end_at") if isinstance(scope.get(key), str))
    summary = payload.get("behavior_summary")
    if isinstance(summary, dict) and isinstance(summary.get("last_behavior_at"), str):
        values.append(summary["last_behavior_at"])
    for product in payload.get("interacted_products", []):
        if isinstance(product, dict) and isinstance(product.get("last_behavior_at"), str):
            values.append(product["last_behavior_at"])
    for item in payload.get("timeline", []):
        if isinstance(item, dict) and isinstance(item.get("occurred_at"), str):
            values.append(item["occurred_at"])
    return values


def _arguments_are_safe(arguments: Any) -> bool:
    if not isinstance(arguments, dict):
        return False
    allowed = {"member_ref", "store_id", "camera_id", "start_at", "end_at", "product_name", "limit"}
    return set(arguments).issubset(allowed) and not any(
        marker in json.dumps(arguments, ensure_ascii=False).lower()
        for marker in FORBIDDEN
    )


def _fixture_payload_matches(tool_name: str, payload: dict[str, Any]) -> bool:
    if tool_name == "get_member_behavior_summary":
        summary = payload.get("behavior_summary", {})
        return (
            summary.get("total_behavior_count") == 3
            and summary.get("behavior_counts") == {
                "merchandise_pick_up": 1,
                "merchandise_holding": 1,
                "merchandise_put_down": 1,
            }
            and summary.get("total_holding_seconds") == 4.0
        )
    timeline = payload.get("timeline", [])
    return (
        [item.get("behavior_label") for item in timeline]
        == ["拿起商品", "持有商品", "放下商品"]
        and [item.get("occurred_at") for item in timeline]
        == [
            "2026-08-27T10:00:00+08:00",
            "2026-08-27T10:01:00+08:00",
            "2026-08-27T10:02:00+08:00",
        ]
    )


def _parse_member_payloads(
    member_results: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], bool]:
    payloads: dict[str, dict[str, Any]] = {}
    valid = True
    for result in member_results:
        tool_name = str(result.get("tool_name", "")).removeprefix("mcp__retailvision__")
        output = result.get("output")
        if tool_name in payloads or tool_name not in MEMBER_TOOL_NAMES or not isinstance(output, str):
            valid = False
            continue
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            valid = False
            continue
        if not isinstance(payload, dict):
            valid = False
            continue
        payloads[tool_name] = payload
    return payloads, valid


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _create_fixture(path: Path) -> None:
    engine = create_engine(_database_url(path))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(Member(
            id="fixture-member-gao", name="高俊杰", tier="gold", is_active=True,
        ))
        rows = (
            ("pickup", "merchandise_pick_up", datetime(2026, 8, 27, 2), None),
            ("holding", "merchandise_holding", datetime(2026, 8, 27, 2, 1), 4.0),
            ("putdown", "merchandise_put_down", datetime(2026, 8, 27, 2, 2), None),
        )
        for event_id, event_type, occurred_at, duration in rows:
            db.add(BehaviorEvent(
                id=f"fixture-{event_id}", event_key=f"fixture:{event_id}",
                event_type=event_type, assessment="confirmed", camera_id="cam02",
                customer_track_id=1,
                customer_track_token=f"fixture-restricted-{event_id}",
                store_id="store-1", is_demo=False,
                member_id="fixture-member-gao", identity_status="confirmed",
                identity_source="offline_track_association",
                identity_binding_level="confirmed", occurred_at=occurred_at,
                started_at=occurred_at, ended_at=occurred_at,
                duration_seconds=duration,
                details={"product_key": "fixture-product", "product_name": "智能熊"},
            ))
        db.commit()
    engine.dispose()


def _config(source: dict[str, Any], database: Path, python: str) -> dict[str, Any]:
    return {
        "providers": source["providers"],
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
                "RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED": "false",
                "RETAILVISION_MEMBER_BEHAVIOR_MCP_ENABLED": "true",
                "RETAILVISION_STORE_ID": "store-1",
                "RETAILVISION_CAMERA_ID": "cam02",
                "RETAILVISION_ANALYSIS_START_AT": "2026-08-27T09:00:00",
                "RETAILVISION_ANALYSIS_END_AT": "2026-08-27T12:00:00",
                "RETAILVISION_TIMEZONE": "Asia/Shanghai",
            },
        }],
    }


def _run(
    *, java: str, jar: Path, config: Path, java_repo: Path,
    scenario_id: str, question: str, expected_tool: str, timeout: int,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            java, "-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8",
            "-Dstderr.encoding=UTF-8", "-jar", str(jar), str(config),
            "-p", question, "--output-format", "stream-json",
        ],
        cwd=java_repo,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    raw = completed.stdout.decode("utf-8", errors="replace")
    raw_stderr = completed.stderr.decode("utf-8", errors="replace")
    events = _parse_events(raw)
    discovery_results = [
        str(item.get("output") or "") for item in events
        if item.get("type") == "tool_result" and item.get("tool_name") == "ToolSearch"
    ]
    discovered = sorted(set(
        re.findall(r"mcp__retailvision__([a-z_]+)", "\n".join(discovery_results))
    ))
    uses = [item for item in events if item.get("type") == "tool_use"]
    results = [item for item in events if item.get("type") == "tool_result"]
    allowed_tools = {"ToolSearch"} | {
        f"mcp__retailvision__{tool_name}" for tool_name in MEMBER_TOOL_NAMES
    }
    blocked_tool_uses = [
        item for item in uses if str(item.get("tool_name", "")) not in allowed_tools
    ]
    blocked_tool_results = [
        item for item in results if str(item.get("tool_name", "")) not in allowed_tools
    ]
    member_uses = [
        item for item in uses
        if str(item.get("tool_name", "")).startswith("mcp__retailvision__")
    ]
    member_results = [
        item for item in results
        if str(item.get("tool_name", "")).startswith("mcp__retailvision__")
    ]
    answers = [item for item in events if item.get("type") == "result"]
    selected = [
        str(item.get("tool_name", "")).removeprefix("mcp__retailvision__")
        for item in member_uses
    ]
    selected_counts = Counter(selected)
    result_names = [
        str(item.get("tool_name", "")).removeprefix("mcp__retailvision__")
        for item in member_results
    ]
    tool_arguments = [item.get("args") for item in member_uses]
    raw_results = [item.get("output") for item in member_results]
    raw_answer = str(answers[-1].get("result") or "") if answers else ""
    exposed = json.dumps(
        {"tool_results": raw_results, "agent_output": raw_answer},
        ensure_ascii=False,
    ).lower()
    safe_projection = not any(marker in exposed for marker in FORBIDDEN)
    no_inference = True
    for marker in ("购买了", "购买意愿", "购买行为", "偏好", "消费能力"):
        cursor = 0
        while True:
            index = raw_answer.find(marker, cursor)
            if index < 0:
                break
            sentence_start = max(
                raw_answer.rfind("。", 0, index),
                raw_answer.rfind("\n", 0, index),
                raw_answer.rfind("；", 0, index),
            )
            prefix = raw_answer[sentence_start + 1:index]
            if not re.search(r"不|未|无|不能|不等|不推断|不代表|仅为", prefix):
                no_inference = False
                break
            cursor = index + len(marker)
        if not no_inference:
            break
    advertised = max(
        (
            int(item.get("advertised_tool_count", 0)) for item in events
            if item.get("type") == "provider_diagnostics"
        ),
        default=0,
    )
    payloads, payload_parse_valid = _parse_member_payloads(member_results)
    public_shape = payload_parse_valid and bool(payloads) and all(
        _validate_public_payload(payload, tool_name)
        for tool_name, payload in payloads.items()
    )
    member_views = [payload.get("member") for payload in payloads.values()]
    scope_views = [payload.get("scope") for payload in payloads.values()]
    same_member = bool(member_views) and all(view == member_views[0] for view in member_views)
    same_scope = bool(scope_views) and all(view == scope_views[0] for view in scope_views)
    product_names = set().union(*(
        _payload_product_names(payload) for payload in payloads.values()
    )) if payloads else set()
    time_values = [
        value
        for payload in payloads.values()
        for value in _time_values(payload)
    ]
    expected_payload = payloads.get(expected_tool)
    facts_match = (
        public_shape
        and same_member
        and same_scope
        and isinstance(expected_payload, dict)
        and all(
            _fixture_payload_matches(tool_name, payload)
            for tool_name, payload in payloads.items()
        )
        and all(
            _answer_fact_consistent(
                raw_answer,
                payload,
                product_names=product_names,
                time_values=time_values,
            )
            for payload in payloads.values()
        )
        and _answer_complete(raw_answer, expected_payload)
    )
    invocation_count = 1 if completed.returncode is not None else 0
    retry_count = sum(
        1 for item in events if item.get("type") in {"retry", "retrying"}
    )
    passed = (
        completed.returncode == 0
        and set(discovered) == {
            "get_member_behavior_summary", "list_member_behavior_timeline",
        }
        and expected_tool in selected_counts
        and set(selected_counts).issubset(MEMBER_TOOL_NAMES)
        and all(count == 1 for count in selected_counts.values())
        and not blocked_tool_uses
        and not blocked_tool_results
        and all(_arguments_are_safe(item.get("args")) for item in member_uses)
        and Counter(result_names) == selected_counts
        and all(not bool(item.get("is_error")) for item in member_results)
        and bool(raw_answer.strip())
        and safe_projection
        and no_inference
        and facts_match
        and retry_count == 0
        and invocation_count == 1
    )
    return {
        "report_title": TITLE,
        "scenario_id": scenario_id,
        "evidence_basis": "controlled_fixture",
        "database_path_kind": "temporary",
        "agent_invocation_count": invocation_count,
        "discovered_tools": discovered,
        "advertised_tool_count": advertised,
        "selected_tool": selected[0] if len(selected) == 1 else selected,
        "tool_call_count": len(selected),
        "tool_arguments": tool_arguments,
        "raw_tool_result": raw_results,
        "raw_agent_output": raw_answer,
        "fact_consistency": "PASS" if passed else "BLOCKED",
        "safe_projection_pass": safe_projection,
        "retry_count": retry_count,
        "production_database_written": False,
        "process_returncode": completed.returncode,
        "process_stderr": raw_stderr,
        "status": "PASS" if passed else "BLOCKED",
        "raw_stream_json": raw,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-repo", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--java", default="java")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)

    source = yaml.safe_load(args.provider_config.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("providers"), list):
        raise SystemExit("provider_config_missing")
    jar = args.java_repo.resolve() / "target" / "mewcode.jar"
    if not jar.is_file():
        raise SystemExit("java_artifact_missing")
    args.output_dir.mkdir(parents=True, exist_ok=False)

    with tempfile.TemporaryDirectory(prefix="mb01-agent-smoke-") as raw:
        temporary = Path(raw).resolve()
        database = temporary / "fixture.db"
        _create_fixture(database)
        if database.resolve().parent != temporary:
            raise SystemExit("fixture_path_not_temporary")
        config = temporary / "mewcode-member-behavior.yaml"
        config.write_text(
            yaml.safe_dump(
                _config(source, database, args.python),
                allow_unicode=True, sort_keys=False,
            ),
            encoding="utf-8",
        )
        records = [
            _run(
                java=args.java, jar=jar, config=config,
                java_repo=args.java_repo.resolve(), scenario_id=scenario_id,
                question=question, expected_tool=expected_tool,
                timeout=args.timeout_seconds,
            )
            for scenario_id, question, expected_tool in SCENARIOS
        ]

    (args.output_dir / "agent_smoke.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    passed = all(item["status"] == "PASS" for item in records)
    manifest = {
        "report_title": TITLE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_basis": "controlled_fixture",
        "database_kind": "temporary_sqlite",
        "production_database_written": False,
        "real_member_evidence": "pending",
        "scenario_count": len(records),
        "passed_count": sum(item["status"] == "PASS" for item in records),
        "retry_count": sum(int(item.get("retry_count", 0)) for item in records),
        "status": "PASS" if passed else "BLOCKED",
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
