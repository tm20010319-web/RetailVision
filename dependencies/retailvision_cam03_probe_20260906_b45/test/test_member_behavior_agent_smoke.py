from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from scripts.run_member_behavior_agent_smoke import (  # noqa: E402
    _answer_fact_consistent,
    _answer_complete,
    _explicit_product_mentions,
    _run,
)


def _summary_payload() -> dict:
    counts = {
        "merchandise_pick_up": 1,
        "merchandise_holding": 1,
        "merchandise_put_down": 1,
    }
    return {
        "status": "ok",
        "member": {
            "member_id": "fixture-member-gao",
            "display_name": "高俊杰",
            "member_tier": "gold",
        },
        "scope": {
            "store_id": "store-1",
            "camera_id": "cam02",
            "camera_scope": "single",
            "start_at": "2026-08-27T09:00:00+08:00",
            "end_at": "2026-08-27T12:00:00+08:00",
            "product_name": None,
        },
        "behavior_summary": {
            "total_behavior_count": 3,
            "behavior_counts": counts,
            "holding_count": 1,
            "total_holding_seconds": 4.0,
            "last_behavior_at": "2026-08-27T10:02:00+08:00",
        },
        "interacted_products": [{
            "product_ref": "product_1",
            "product_name": "智能熊",
            "product_name_status": "available",
            "behavior_count": 3,
            "behavior_counts": counts,
            "total_holding_seconds": 4.0,
            "holding_duration_status": "available",
            "last_behavior_at": "2026-08-27T10:02:00+08:00",
        }],
        "data_quality": {"holding_duration": "available"},
    }


def _timeline_payload() -> dict:
    common = _summary_payload()
    return {
        "status": "ok",
        "member": common["member"],
        "scope": common["scope"],
        "timeline": [
            {
                "occurred_at": "2026-08-27T10:00:00+08:00",
                "behavior_type": "merchandise_pick_up",
                "behavior_label": "拿起商品",
                "product_ref": "product_1",
                "product_name": "智能熊",
                "product_name_status": "available",
                "duration_seconds": None,
                "duration_status": "not_applicable",
            },
            {
                "occurred_at": "2026-08-27T10:01:00+08:00",
                "behavior_type": "merchandise_holding",
                "behavior_label": "持有商品",
                "product_ref": "product_1",
                "product_name": "智能熊",
                "product_name_status": "available",
                "duration_seconds": 4.0,
                "duration_status": "available",
            },
            {
                "occurred_at": "2026-08-27T10:02:00+08:00",
                "behavior_type": "merchandise_put_down",
                "behavior_label": "放下商品",
                "product_ref": "product_1",
                "product_name": "智能熊",
                "product_name_status": "available",
                "duration_seconds": None,
                "duration_status": "not_applicable",
            },
        ],
        "has_more": False,
    }


def _stream(
    payload: dict,
    *,
    extra_tool: str | None = None,
    related_payload: dict | None = None,
    related_args: dict | None = None,
    related_error: bool = False,
    answer: str = "高俊杰共3次商品行为，持有4秒。",
) -> bytes:
    events = [
        {"type": "tool_use", "tool_name": "ToolSearch", "args": {}},
        {
            "type": "tool_result", "tool_name": "ToolSearch",
            "output": "mcp__retailvision__get_member_behavior_summary "
            "mcp__retailvision__list_member_behavior_timeline",
        },
    ]
    if extra_tool:
        events.append({"type": "tool_use", "tool_name": extra_tool, "args": {}})
    events.extend((
        {
            "type": "tool_use",
            "tool_name": "mcp__retailvision__get_member_behavior_summary",
            "args": {"member_ref": "高俊杰"},
        },
        {
            "type": "tool_result",
            "tool_name": "mcp__retailvision__get_member_behavior_summary",
            "output": json.dumps(payload, ensure_ascii=False),
            "is_error": False,
        },
    ))
    if related_payload is not None:
        events.extend((
            {
                "type": "tool_use",
                "tool_name": "mcp__retailvision__list_member_behavior_timeline",
                "args": related_args or {"member_ref": "高俊杰"},
            },
            {
                "type": "tool_result",
                "tool_name": "mcp__retailvision__list_member_behavior_timeline",
                "output": json.dumps(related_payload, ensure_ascii=False),
                "is_error": related_error,
            },
        ))
    events.append({"type": "result", "result": answer})
    return ("\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n").encode()


class MemberBehaviorAgentSmokeCheckerTests(unittest.TestCase):
    def _run(
        self,
        stdout: bytes,
        *,
        returncode: int | None = 0,
        expected_tool: str = "get_member_behavior_summary",
    ) -> dict:
        completed = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=b"")
        with patch("scripts.run_member_behavior_agent_smoke.subprocess.run", return_value=completed):
            return _run(
                java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
                java_repo=Path("."), scenario_id="summary",
                question="分析高俊杰的商品行为。",
                expected_tool=expected_tool, timeout=1,
            )

    def test_extra_file_or_shell_tool_blocks_acceptance(self) -> None:
        for name in ("Read", "Shell"):
            with self.subTest(tool=name):
                self.assertEqual("BLOCKED", self._run(_stream(_summary_payload(), extra_tool=name))["status"])

    def test_phone_points_or_purchase_fields_block_acceptance(self) -> None:
        for field in ("phone", "points", "purchase_amount"):
            payload = _summary_payload()
            payload["member"][field] = "restricted"
            with self.subTest(field=field):
                self.assertEqual("BLOCKED", self._run(_stream(payload))["status"])

    def test_unbound_final_answer_number_blocks_acceptance(self) -> None:
        result = self._run(_stream(_summary_payload(), answer="高俊杰共99次行为。"))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(1, result["agent_invocation_count"])
        self.assertEqual(0, result["retry_count"])

    def test_correct_summary_semantic_claims_pass(self) -> None:
        result = self._run(_stream(
            _summary_payload(),
            answer="高俊杰拿起1次，持有1次，放下1次，总共3次，持有4秒。",
        ))
        self.assertEqual("PASS", result["status"])

    def test_product_label_is_valid_summary_context(self) -> None:
        payload = _summary_payload()
        answer = "高俊杰的商品：智能熊共3次行为。"
        self.assertTrue(
            _answer_fact_consistent(answer, payload)
            and _answer_complete(answer, payload)
        )
        for generic_answer in (
            "高俊杰有3次行为。",
            "高俊杰查询成功。",
            "有3次记录。",
        ):
            with self.subTest(answer=generic_answer):
                self.assertFalse(
                    _answer_fact_consistent(generic_answer, payload)
                    and _answer_complete(generic_answer, payload)
                )

    def test_related_member_tools_may_be_composed_once_each(self) -> None:
        answer = "高俊杰总共3次商品行为，拿起、持有、放下商品智能熊。"
        stream = _stream(
            _summary_payload(),
            related_payload=_timeline_payload(),
            answer=answer,
        )
        self.assertEqual("PASS", self._run(stream)["status"])
        self.assertEqual(
            "PASS",
            self._run(
                stream,
                expected_tool="list_member_behavior_timeline",
            )["status"],
        )

    def test_related_member_tools_require_success_and_same_scope(self) -> None:
        timeline = _timeline_payload()
        mismatched = _timeline_payload()
        mismatched["scope"] = dict(mismatched["scope"], camera_id="cam99")
        for stream in (
            _stream(_summary_payload(), related_payload=timeline, related_error=True),
            _stream(_summary_payload(), related_payload=mismatched),
        ):
            with self.subTest():
                self.assertEqual("BLOCKED", self._run(stream)["status"])

    def test_duplicate_member_tool_call_blocks_acceptance(self) -> None:
        stream = _stream(_summary_payload()).decode()
        duplicate = "\n".join((
            json.dumps({
                "type": "tool_use",
                "tool_name": "mcp__retailvision__get_member_behavior_summary",
                "args": {"member_ref": "高俊杰"},
            }, ensure_ascii=False),
            json.dumps({
                "type": "tool_result",
                "tool_name": "mcp__retailvision__get_member_behavior_summary",
                "output": json.dumps(_summary_payload(), ensure_ascii=False),
                "is_error": False,
            }, ensure_ascii=False),
        ))
        stream = stream.replace('{"type": "result"', duplicate + '\n{"type": "result"', 1)
        self.assertEqual("BLOCKED", self._run(stream.encode())["status"])

    def test_explicit_product_names_are_bound_only_to_current_payload(self) -> None:
        answer = "高俊杰总共3次商品行为，拿起智能熊，之后放下智能象。"
        one_product = _summary_payload()
        self.assertEqual("BLOCKED", self._run(_stream(one_product, answer=answer))["status"])

        two_products = _summary_payload()
        second_product = dict(two_products["interacted_products"][0])
        second_product["product_ref"] = "product_2"
        second_product["product_name"] = "智能象"
        two_products["interacted_products"].append(second_product)
        self.assertEqual("PASS", self._run(_stream(two_products, answer=answer))["status"])

    def test_product_extractor_accepts_normal_chinese_product_phrases(self) -> None:
        expected = {"智能熊"}
        payload = _summary_payload()
        for answer in (
            "高俊杰拿起了智能熊",
            "会员“高俊杰”拿起了“智能熊”",
            "高俊杰与“智能熊”互动",
            "商品：智能熊共3次行为",
            "高俊杰拿起商品智能熊",
            "高俊杰拿起智能熊，持有约5秒钟",
            "高俊杰拿起智能熊，持有5秒左右",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(expected, _explicit_product_mentions(answer))
                self.assertTrue(_answer_fact_consistent(answer, payload))

    def test_product_extractor_ignores_markdown_and_action_enumeration(self) -> None:
        answer = (
            "高俊杰总共3次商品行为。\n"
            "| 时间 | 行为 | 商品 |\n"
            "| 10:00 | 拿起商品 | 智能熊 |\n"
            "仅一件商品：**智能熊**。\n"
            "该会员完成了拿起、持有、放下完整行为链路。"
        )
        self.assertEqual({"智能熊"}, _explicit_product_mentions(answer))

    def test_product_checker_rejects_names_absent_from_current_payload(self) -> None:
        payload = _summary_payload()
        for answer in (
            "高俊杰拿起了智能象",
            "高俊杰拿起智能熊，之后放下智能象",
            "会员“高俊杰”拿起了“智能象”",
            "商品：智能象共3次行为",
        ):
            with self.subTest(answer=answer):
                self.assertFalse(_answer_fact_consistent(answer, payload))

    def test_summary_requires_dynamic_member_total_and_behavior_context(self) -> None:
        payload = _summary_payload()
        for answer in (
            "该会员有商品行为",
            "该会员查询成功",
            "有3次记录",
            "高俊杰共3次记录",
            "高俊杰共3次记录，拿起商品。",
        ):
            with self.subTest(answer=answer):
                self.assertFalse(self._run(_stream(payload, answer=answer))["status"] == "PASS")

        self.assertTrue(
            _answer_fact_consistent("高俊杰共3次商品行为。", payload)
        )
        self.assertTrue(
            _answer_fact_consistent("高俊杰拿起1次，总共3次。", payload)
        )

    def test_timeline_requires_member_and_each_present_behavior_semantic(self) -> None:
        payload = {
            "member": _summary_payload()["member"],
            "timeline": [
                {"behavior_type": "merchandise_pick_up"},
                {"behavior_type": "merchandise_holding"},
                {"behavior_type": "merchandise_put_down"},
            ],
        }
        self.assertTrue(_answer_complete("高俊杰拿起、持有后放下商品。", payload))
        self.assertFalse(_answer_complete("该会员拿起、持有后放下商品。", payload))
        self.assertFalse(_answer_complete("高俊杰拿起并持有商品。", payload))

    def test_unavailable_product_name_allows_generic_description_only(self) -> None:
        payload = _summary_payload()
        payload["interacted_products"][0]["product_name"] = None
        payload["interacted_products"][0]["product_name_status"] = "unavailable"
        for answer, expected in (
            ("高俊杰与一件未命名商品发生了互动。", "PASS"),
            ("高俊杰拿起了智能象。", "BLOCKED"),
        ):
            with self.subTest(answer=answer):
                actual = "PASS" if _answer_fact_consistent(answer, payload) else "BLOCKED"
                self.assertEqual(expected, actual)

    def test_holding_duration_approximation_is_non_blocking(self) -> None:
        self.assertEqual(
            "PASS",
            "PASS" if _answer_fact_consistent("大约持有了5秒钟。", _summary_payload()) else "BLOCKED",
        )

    def test_explicit_wrong_member_name_blocks_acceptance(self) -> None:
        self.assertEqual(
            "BLOCKED",
            self._run(_stream(_summary_payload(), answer="李明拿起了智能熊。"))["status"],
        )

    def test_wrong_pickup_count_blocks_even_when_date_offset_contains_number(self) -> None:
        result = self._run(_stream(
            _summary_payload(), answer="高俊杰拿起8次（2026-08-27T10:00:00+08:00）。"
        ))
        self.assertEqual("BLOCKED", result["status"])

    def test_duration_difference_is_non_blocking_even_with_product_ref(self) -> None:
        self.assertTrue(_answer_fact_consistent("product_1持有1秒。", _summary_payload()))

    def test_retry_event_blocks_acceptance(self) -> None:
        stream = _stream(_summary_payload()).decode()
        stream = stream.replace('{"type": "result"', '{"type": "retry"}\n{"type": "result"', 1)
        result = self._run(stream.encode())
        self.assertEqual("BLOCKED", result["status"])

    def test_agent_invocation_count_not_one_blocks_acceptance(self) -> None:
        result = self._run(_stream(_summary_payload()), returncode=None)
        self.assertEqual(0, result["agent_invocation_count"])
        self.assertEqual("BLOCKED", result["status"])

    def test_missing_successful_member_call_blocks_acceptance(self) -> None:
        stream = _stream(_summary_payload()).decode()
        stream = "\n".join(
            line for line in stream.splitlines()
            if "mcp__retailvision__get_member_behavior_summary" not in line
        ) + "\n"
        self.assertEqual("BLOCKED", self._run(stream.encode())["status"])

    def test_wrong_member_tool_blocks_acceptance(self) -> None:
        stream = _stream(_summary_payload()).decode().replace(
            "mcp__retailvision__get_member_behavior_summary",
            "mcp__retailvision__list_member_behavior_timeline",
        )
        self.assertEqual("BLOCKED", self._run(stream.encode())["status"])

    def test_forbidden_member_argument_blocks_acceptance(self) -> None:
        stream = _stream(_summary_payload()).decode()
        stream = stream.replace('"member_ref": "高俊杰"', '"member_ref": "高俊杰", "product_key": "secret"')
        result = self._run(stream.encode())
        self.assertEqual("BLOCKED", result["status"])


if __name__ == "__main__":
    unittest.main()
