from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from scripts.run_store_intelligence_agent_acceptance import (  # noqa: E402
    SCENARIOS,
    _answer_is_grounded,
    _load_test_summary,
    _manifest_from_records,
    _prepare_output_directory,
    _presentation_violations,
    _presentation_warnings,
    _validate_summary_against_manifest,
    _run_scenario,
)


class StoreIntelligenceAgentAcceptanceTests(unittest.TestCase):
    def test_presentation_rejects_internal_keys_scores_and_execution_text(self):
        decoded = [{"product_key": "smart_toy_a", "product_name": "智能熊"}]
        cases = {
            "smart_toy_a 拿起2次。": "internal_product_key",
            "智能熊推荐评分6.2。": "raw_internal_field",
            "I'll call rank_products before answering. 智能熊拿起2次。": "execution_process",
            "Let me summarize. 智能熊拿起2次。": "execution_process",
            "我来汇报一下。智能熊拿起2次。": "execution_process",
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                violations = _presentation_violations(answer, decoded)
                self.assertIn(expected, violations)
        self.assertEqual([], _presentation_violations("智能熊拿起2次。", decoded))

    def test_generic_empty_tool_result_is_non_blocking_presentation_warning(self):
        decoded: list[object] = []
        answer = "工具返回了空结果，当前窗口没有符合规则的异常商品行为。"
        self.assertEqual([], _presentation_violations(answer, decoded))
        self.assertEqual(
            ["generic_empty_tool_result"],
            _presentation_warnings(answer, decoded),
        )

    def test_named_tool_or_route_remains_blocking_presentation_failure(self):
        decoded: list[object] = []
        for answer in (
            "rank_products 返回了空结果。",
            "MCP 工具返回了空结果。",
            "我会先调用工具再回答。",
        ):
            with self.subTest(answer=answer):
                self.assertTrue(_presentation_violations(answer, decoded))
                self.assertEqual([], _presentation_warnings(answer, decoded))

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_presentation_warning_does_not_block_scenario_success(self, run):
        events = [
            {
                "type": "tool_use",
                "tool_name": "mcp__retailvision__detect_product_anomaly",
                "args": {},
            },
            {
                "type": "tool_result",
                "tool_name": "mcp__retailvision__detect_product_anomaly",
                "output": "[]",
                "is_error": False,
            },
            {
                "type": "result",
                "result": "工具返回了空结果，当前窗口没有符合规则的异常商品行为。",
            },
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item, ensure_ascii=False) for item in events)).encode(),
            stderr=b"",
        )

        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="behavior-anomaly",
            question="最近有没有异常商品行为？",
            expected_tool="detect_product_anomaly",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )

        self.assertTrue(passed)
        self.assertEqual("PASS", record["presentation_verdict"])
        self.assertEqual([], record["presentation_checks"])
        self.assertEqual(
            ["generic_empty_tool_result"], record["presentation_warnings"]
        )
        self.assertEqual("SUCCESS", record["final_classification"])

    def test_identified_people_wording_remains_grounding_failure(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a", "product_name": "智能熊",
            "completed_interaction_count": 2,
        }], ensure_ascii=False)
        for answer in (
            "智能熊有2次顾客互动。",
            "智能熊有2次消费者互动。",
            "智能熊涉及2人次互动。",
        ):
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "toy-a-summary", answer, [payload]
                )
                self.assertFalse(grounded)
                self.assertIn("rejected:identified_people", checks)

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_agent_stdout_uses_utf8_without_corrupting_chinese(self, run):
        events = [
            {
                "type": "tool_use",
                "tool_name": "mcp__retailvision__rank_products",
                "args": {"store_id": "store-1"},
            },
            {
                "type": "tool_result",
                "tool_name": "mcp__retailvision__rank_products",
                "output": json.dumps([{
                    "product_key": "smart_toy_a",
                    "product_name": "智能熊",
                    "pickup_count": 2,
                }], ensure_ascii=False),
                "is_error": False,
            },
            {"type": "result", "result": "智能熊拿起2次。"},
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(
                json.dumps(item, ensure_ascii=False) for item in events
            )).encode("utf-8"),
            stderr=b"",
        )

        record, _ = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="hot-products",
            question="最近哪些商品最火？", expected_tool="rank_products",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )

        self.assertEqual("智能熊拿起2次。", record["final_answer"])
        command = run.call_args.args[0]
        self.assertEqual([
            "-Dfile.encoding=UTF-8",
            "-Dstdout.encoding=UTF-8",
            "-Dstderr.encoding=UTF-8",
        ], command[1:4])

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_invalid_utf8_agent_stdout_is_output_invalid(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"\xff", stderr=b"",
        )

        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="hot-products",
            question="最近哪些商品最火？", expected_tool="rank_products",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )

        self.assertFalse(passed)
        self.assertEqual("AGENT_OUTPUT_INVALID", record["final_classification"])

    def test_provider_rounds_are_persisted_with_sanitized_fields(self):
        events = [{
            "type": "provider_diagnostics", "provider_round": 2,
            "advertised_tool_count": 13, "tool_call_delta_count": 2,
            "tool_call_complete_count": 1, "finish_reason": "tool_calls",
            "answer_non_empty": False, "tool_call_parse_failure_count": 0,
            "argument_assembly_failure_count": 0, "classification": "SUCCESS",
        }, {"type": "result", "result": ""}]
        with patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=("\n".join(json.dumps(item) for item in events)).encode(),
                stderr=b"",
            )
            record, _ = _run_scenario(
                java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
                java_repo=Path("."), scenario_id="hot-products",
                question="最近哪些商品最火？", expected_tool="rank_products",
                provider={"provider": "test", "model": "test"}, timeout_seconds=1,
            )
        self.assertEqual([{
            "provider_round": 2, "advertised_tool_count": 13,
            "tool_call_delta_count": 2, "tool_call_complete_count": 1,
            "finish_reason": "tool_calls", "answer_non_empty": False,
            "tool_call_parse_failure_count": 0,
            "argument_assembly_failure_count": 0, "classification": "SUCCESS",
        }], record["provider_rounds"])

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_agent_failure_classifications_are_not_provider_tool_missing(self, run):
        cases = [
            (subprocess.TimeoutExpired("java", 1), "AGENT_TIMEOUT"),
            (subprocess.CompletedProcess([], 7, b"{}\n", b""), "AGENT_PROCESS_FAILED"),
            (subprocess.CompletedProcess([], 0, b"not-json\n", b""), "AGENT_OUTPUT_INVALID"),
        ]
        for completed, expected in cases:
            with self.subTest(expected=expected):
                if isinstance(completed, BaseException):
                    run.side_effect = completed
                else:
                    run.side_effect = None
                    run.return_value = completed
                record, _ = _run_scenario(
                    java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
                    java_repo=Path("."), scenario_id="hot-products",
                    question="最近哪些商品最火？", expected_tool="rank_products",
                    provider={"provider": "test", "model": "test"}, timeout_seconds=1,
                )
                self.assertEqual(expected, record["final_classification"])

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_selected_wrong_tool_is_classified_as_model_wrong_tool_call(self, run):
        events = [{
            "type": "tool_use", "tool_name": "mcp__retailvision__get_product_behavior_summary",
            "args": {},
        }, {"type": "result", "result": ""}]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in events)).encode(), stderr=b"",
        )
        record, _ = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="hot-products",
            question="最近哪些商品最火？", expected_tool="rank_products",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )
        self.assertEqual("MODEL_WRONG_TOOL_CALL", record["final_classification"])

    def test_grounding_rejects_unsupported_people_commerce_and_placement_claims(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a",
            "pickup_count": 2,
            "total_holding_seconds": 12.0,
        }])
        for claim in (
            "smart_toy_a 有 2 次购买",
            "smart_toy_a 覆盖 2 位顾客",
            "smart_toy_a 应放在主通道黄金位置",
            "smart_toy_a 的购买意图较强",
            "smart_toy_a 覆盖 2 人均互动，关注度更高",
            "smart_toy_a 有 2 次来访互动",
            "smart_toy_a 的互动节奏正常、走势平稳，没有突出波动",
            "smart_toy_a 单次持有更久，因此互动更深",
        ):
            with self.subTest(claim=claim):
                grounded, checks = _answer_is_grounded("hot-products", claim, [payload])
                self.assertFalse(grounded)
                self.assertTrue(any(item.startswith("rejected:") for item in checks))

    def test_grounding_allows_explicitly_negated_boundary_statement(self):
        payload = json.dumps([{"product_key": "smart_toy_a", "pickup_count": 2}])
        grounded, checks = _answer_is_grounded(
            "hot-products",
            "smart_toy_a 拿起 2 次；这不是购买或成交数据。",
            [payload],
        )
        self.assertTrue(grounded, checks)

    def test_grounding_restarts_negation_scope_at_conjunctions(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a", "pickup_count": 2,
        }])
        for answer in (
            "这不是购买数据且智能熊关注度很高。",
            "不涉及物理摆放位置且建议把智能熊放到主通道。",
            "This is not purchase data and engagement level is high.",
        ):
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "hot-products", answer, [payload]
                )
                self.assertFalse(grounded, checks)

    def test_negation_scope_restarts_after_comma_and_contrast(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a",
            "completed_interaction_count": 2,
        }])
        cases = (
            "smart_toy_a 完成2次互动，这不是购买或成交数据，但智能熊关注度很高。",
            "smart_toy_a 完成2次互动，不涉及物理摆放位置，但建议把智能熊放到主通道。",
            "smart_toy_a 完成2次互动，没有顾客身份信息，但顾客对智能熊兴趣很高。",
            "smart_toy_a completed 2 interactions. This is not purchase data, but engagement level is high.",
        )
        for answer in cases:
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "hot-products", answer, [payload]
                )
                self.assertFalse(grounded)
                self.assertTrue(any(item.startswith("rejected:") for item in checks))

    def test_pure_negated_boundary_statements_remain_allowed(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a",
            "completed_interaction_count": 2,
        }])
        for answer in (
            "smart_toy_a 完成2次互动，这不是购买或成交数据。",
            "smart_toy_a 完成2次互动，该结果不涉及物理摆放位置。",
            "smart_toy_a 完成2次互动，系统没有顾客身份信息。",
            "smart_toy_a 完成2次互动，工具结果不能证明智能熊关注度更高。",
        ):
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "hot-products", answer, [payload]
                )
                self.assertTrue(grounded, checks)

    def test_grounding_rejects_unsupported_engagement_claims(self):
        payload = json.dumps([{"product_key": "smart_toy_a", "pickup_count": 2}])
        for claim in (
            "该商品存在一定关注",
            "持握参与度高",
            "互动规模较小",
            "该商品参与深度较高",
            "高关注但互动数据异常",
            "该商品互动质量较高",
            "该商品引发了兴趣",
            "该商品具有吸引力",
            "该商品提供深度体验",
        ):
            with self.subTest(claim=claim):
                grounded, checks = _answer_is_grounded(
                    "hot-products", claim, [payload]
                )
                self.assertFalse(grounded)
                self.assertIn("rejected:unsupported_engagement_claim", checks)

    def test_grounding_allows_negated_physical_boundary(self):
        payload = json.dumps([{"product_key": "smart_toy_a", "pickup_count": 2}])
        grounded, checks = _answer_is_grounded(
            "hot-products", "smart_toy_a 拿起 2 次；不涉及物理摆放位置。", [payload]
        )
        self.assertTrue(grounded, checks)

    def test_grounding_allows_subjective_question_quote_when_reframed(self):
        payload = json.dumps([
            {"product_key": "smart_toy_a", "completed_interaction_count": 2},
            {"product_key": "smart_toy_b", "completed_interaction_count": 1},
        ])
        grounded, checks = _answer_is_grounded(
            "hot-products",
            "用户问哪些商品更受关注；主观偏好无法判断，按互动次数智能熊更高。",
            [payload],
        )
        self.assertTrue(grounded, checks)

        grounded, checks = _answer_is_grounded(
            "hot-products",
            "没有筛选出高关注但互动行为异常的商品。",
            [json.dumps([])],
        )
        self.assertTrue(grounded, checks)

        grounded, checks = _answer_is_grounded(
            "hot-products",
            "smart_toy_a 完成交互 2 次，尚无形成购买或转化的事实。",
            [json.dumps({"product_key": "smart_toy_a", "completed_interaction_count": 2})],
        )
        self.assertTrue(grounded, checks)

        display_payload = json.dumps([{
            "product_key": "smart_toy_a", "display_priority": 1,
        }])
        grounded, checks = _answer_is_grounded(
            "display-adjustment",
            "smart_toy_a 优先级 1；工具不具备通道等物理摆放知识，"
            "互动不等同于购买。",
            [display_payload],
        )
        self.assertTrue(grounded, checks)

    def test_grounding_rejects_structured_recommendation_contradiction(self):
        payload = json.dumps({
            "first": {"product_key": "smart_toy_a", "product_name": "智能熊"},
            "second": {"product_key": "smart_toy_b", "product_name": "智能象"},
            "recommendation": "smart_toy_a",
            "scores": {"smart_toy_a": 6.1, "smart_toy_b": 3.33},
        })
        grounded, checks = _answer_is_grounded(
            "compare-toys",
            "智能象被推荐，评分 3.33；智能熊评分 6.1。",
            [payload],
        )
        self.assertFalse(grounded)
        self.assertIn("rejected:structured_recommendation_contradiction", checks)

    def test_grounding_requires_every_numeric_claim_to_exist_in_tool_result(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a",
            "pickup_count": 2,
            "total_holding_seconds": 12.0,
        }])
        grounded, _ = _answer_is_grounded(
            "hot-products",
            "smart_toy_a 拿起 2 次，持握 12 秒。",
            [payload],
        )
        self.assertTrue(grounded)
        grounded, checks = _answer_is_grounded(
            "hot-products",
            "smart_toy_a 拿起 2 次，持握 99 秒。",
            [payload],
        )
        self.assertFalse(grounded)
        self.assertIn("rejected:ungrounded_numeric_claim", checks)

    def test_grounding_allows_rounding_to_reported_precision(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a", "recommendation_score": 3.333333,
        }])
        grounded, checks = _answer_is_grounded(
            "display-priority", "smart_toy_a 推荐分 3.33。", [payload]
        )
        self.assertTrue(grounded, checks)

    def test_grounding_rejects_code_value_not_returned_by_tool(self):
        payload = json.dumps({
            "product_key": "smart_toy_a",
            "interaction_count": 2,
            "recommendation": "increase_attention",
        })
        grounded, checks = _answer_is_grounded(
            "rising-products",
            "smart_toy_a 上涨 2 次，建议 include_increase_attention。",
            [payload],
        )
        self.assertFalse(grounded)
        self.assertIn(
            "rejected:ungrounded_code:include_increase_attention", checks
        )

    def test_grounding_rejects_metrics_swapped_between_products(self):
        payload = json.dumps([
            {
                "product_key": "smart_toy_a", "product_name": "智能熊",
                "avg_holding_seconds": 6.0,
            },
            {
                "product_key": "smart_toy_b", "product_name": "智能象",
                "avg_holding_seconds": 20.0,
            },
        ])
        grounded, checks = _answer_is_grounded(
            "hot-products",
            "智能熊平均持握 20 秒。\n智能象平均持握 6 秒。",
            [payload],
        )
        self.assertFalse(grounded)
        self.assertIn("rejected:misattributed_metric:smart_toy_a:20", checks)
        self.assertIn("rejected:misattributed_metric:smart_toy_b:6", checks)

        grounded, checks = _answer_is_grounded(
            "hot-products",
            "1. 智能熊平均持握 6 秒。\n2. 智能象平均持握 20 秒。",
            [payload],
        )
        self.assertTrue(grounded, checks)

    def test_grounding_binds_multi_product_metrics_in_sentences(self):
        payload = json.dumps([
            {
                "product_key": "smart_toy_a", "product_name": "智能熊",
                "completed_interaction_count": 2,
                "total_holding_seconds": 12,
            },
            {
                "product_key": "smart_toy_b", "product_name": "智能象",
                "completed_interaction_count": 1,
                "total_holding_seconds": 20,
            },
        ], ensure_ascii=False)
        wrong = (
            "智能熊完成1次互动，智能象完成2次互动，因此智能熊互动更多。"
        )
        grounded, checks = _answer_is_grounded("compare-toys", wrong, [payload])
        self.assertFalse(grounded)
        self.assertTrue(any("misattributed_metric" in item for item in checks))

        correct = "智能熊完成2次互动，智能象完成1次互动，因此智能熊互动更多。"
        grounded, checks = _answer_is_grounded("compare-toys", correct, [payload])
        self.assertTrue(grounded, checks)

    def test_grounding_accepts_recovery_summary_single_dwell_wording(self):
        payload = json.dumps({
            "product_key": "smart_toy_a",
            "product_name": "智能熊",
            "completed_interaction_count": 2,
            "pickup_count": 2,
            "put_down_count": 2,
            "total_holding_seconds": 12.0,
            "avg_holding_seconds": 6.0,
            "max_holding_seconds": 7.0,
            "unique_person_track_count": 2,
        }, ensure_ascii=False)
        answer = (
            "智能熊在最近一小时内拿起2次、放下2次，完整互动2次，"
            "累计持有12秒，平均6秒，最长7秒，涉及2条匿名轨迹。"
            "整体来看，互动均完成，单次停留约6秒左右。"
        )

        grounded, checks = _answer_is_grounded(
            "toy-a-summary", answer, [payload]
        )

        self.assertTrue(grounded, checks)

    def test_recovery_comparison_recommendation_is_grounded_but_exposes_tool(self):
        decoded = {
            "comparison": {
                "first": {
                    "product_key": "smart_toy_a", "product_name": "智能熊",
                    "completed_interaction_count": 2, "pickup_count": 2,
                    "total_holding_seconds": 12.0, "avg_holding_seconds": 6.0,
                },
                "second": {
                    "product_key": "smart_toy_b", "product_name": "智能象",
                    "completed_interaction_count": 1, "pickup_count": 1,
                    "total_holding_seconds": 20.0, "avg_holding_seconds": 20.0,
                },
            },
            "recommendation": "smart_toy_a",
        }
        answer = (
            "从互动数据来看，智能熊的互动更多。\n\n"
            "- 智能熊：完整互动2次，拿起次数2次，累计持有时长12秒。\n"
            "- 智能象：完整互动1次，拿起次数1次，累计持有时长20秒。\n\n"
            "智能熊的互动次数更高，但智能象的单次平均持有时长"
            "（20秒）更长。综合比较工具推荐优先关注智能熊。"
        )

        grounded, checks = _answer_is_grounded(
            "compare-toys", answer, [json.dumps(decoded, ensure_ascii=False)]
        )

        self.assertTrue(grounded, checks)
        self.assertIn("execution_process", _presentation_violations(answer, [decoded]))

    def test_grounding_accepts_returned_attention_recommendations_only(self):
        trend_payload = json.dumps([
            {
                "product_key": "smart_toy_a",
                "current": {
                    "product_key": "smart_toy_a", "product_name": "智能熊",
                    "completed_interaction_count": 2,
                    "total_holding_seconds": 12.0,
                },
                "interaction_trend": "up",
                "recommendation": "increase_attention",
            },
            {
                "product_key": "smart_toy_b",
                "current": {
                    "product_key": "smart_toy_b", "product_name": "智能象",
                    "completed_interaction_count": 1,
                    "total_holding_seconds": 20.0,
                },
                "interaction_trend": "up",
                "recommendation": "increase_attention",
            },
        ], ensure_ascii=False)
        for answer in (
            "智能熊上涨且完整互动2次；智能象上涨且完整互动1次，两款商品适合持续关注。",
            "智能熊上涨且完整互动2次；智能象上涨且完整互动1次，两款商品都值得保持关注。",
        ):
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "rising-products", answer, [trend_payload]
                )
                self.assertTrue(grounded, checks)

        display_payload = json.dumps([
            {
                "product_key": "smart_toy_a", "product_name": "智能熊",
                "display_priority": 1, "recommended": True,
                "recommendation": "重点展示", "completed_interaction_count": 2,
            },
            {
                "product_key": "smart_toy_b", "product_name": "智能象",
                "display_priority": 2, "recommended": True,
                "recommendation": "重点展示", "completed_interaction_count": 1,
            },
        ], ensure_ascii=False)
        grounded, checks = _answer_is_grounded(
            "display-adjustment",
            "智能熊优先级第1且完整互动2次，智能象优先级第2且完整互动1次；"
            "重点关注互动更频繁的智能熊。",
            [display_payload],
        )
        self.assertTrue(grounded, checks)

        no_recommendation = json.dumps({
            "product_key": "smart_toy_a", "product_name": "智能熊",
            "completed_interaction_count": 2,
        }, ensure_ascii=False)
        for answer in (
            "智能熊完整互动2次，适合持续关注。",
            "智能熊完整互动2次，关注度不高。",
            "智能熊完整互动2次，属于高关注商品。",
        ):
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "toy-a-summary", answer, [no_recommendation]
                )
                self.assertFalse(grounded)
                self.assertIn("rejected:unsupported_engagement_claim", checks)

    def test_grounding_ignores_iso_scope_immediately_followed_by_chinese(self):
        payload = json.dumps({
            "product_key": "smart_toy_a", "product_name": "智能熊",
            "pickup_count": 2, "put_down_count": 2,
            "completed_interaction_count": 2,
            "total_holding_seconds": 12.0, "avg_holding_seconds": 6.0,
            "max_holding_seconds": 7.0,
            "last_interaction_at": "2026-08-20T13:15:07",
        }, ensure_ascii=False)
        answer = (
            "在2026-08-20 13:00–14:00这个查询时段内，智能熊被拿起2次、"
            "放下2次、完整互动2次；累计12秒，平均6秒，最长7秒，"
            "最近一次互动发生在13:15。"
        )

        grounded, checks = _answer_is_grounded(
            "toy-a-summary", answer, [payload]
        )

        self.assertTrue(grounded, checks)

    def test_grounding_allows_safe_natural_comparisons(self):
        payload = json.dumps([
            {
                "product_key": "smart_toy_a", "product_name": "智能熊",
                "completed_interaction_count": 2, "avg_holding_seconds": 6.0,
                "total_holding_seconds": 12.0,
            },
            {
                "product_key": "smart_toy_b", "product_name": "智能象",
                "completed_interaction_count": 1, "avg_holding_seconds": 20.0,
                "total_holding_seconds": 20.0,
            },
        ], ensure_ascii=False)
        cases = (
            "智能熊互动次数更多：2次，智能象1次。",
            "智能象平均持有更长：20秒，智能熊6秒。",
            "摄像头数据不能判断主观兴趣；按互动次数，智能熊更活跃。",
        )
        for answer in cases:
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "compare-toys", answer, [payload]
                )
                self.assertTrue(grounded, checks)

    def test_grounding_binds_chinese_cross_product_comparison_order(self):
        payload = json.dumps({
            "comparison": [
                {"product_key": "smart_toy_a", "product_name": "智能熊",
                 "completed_interaction_count": 2, "avg_holding_seconds": 6.0},
                {"product_key": "smart_toy_b", "product_name": "智能象",
                 "completed_interaction_count": 1, "avg_holding_seconds": 20.0},
            ]
        }, ensure_ascii=False)
        correct = "智能象平均持有20秒，高于智能熊6秒；智能熊完整互动2次，高于智能象1次。"
        grounded, checks = _answer_is_grounded("compare-toys", correct, [payload])
        self.assertTrue(grounded, checks)

        wrong = "智能象平均持有6秒，高于智能熊20秒；智能熊完整互动1次，高于智能象2次。"
        grounded, checks = _answer_is_grounded("compare-toys", wrong, [payload])
        self.assertFalse(grounded)
        self.assertTrue(any("misattributed_metric" in item for item in checks), checks)

    def test_grounding_cross_product_comparison_ignores_previous_window_metrics(self):
        payload = json.dumps([
            {
                "product_key": "smart_toy_a",
                "current": {
                    "product_key": "smart_toy_a", "product_name": "智能熊",
                    "interaction_count": 2, "avg_holding_seconds": 6.0,
                },
                "previous": {
                    "product_key": "smart_toy_a",
                    "interaction_count": 0, "avg_holding_seconds": 0.0,
                },
                "interaction_trend": "up",
            },
            {
                "product_key": "smart_toy_b",
                "current": {
                    "product_key": "smart_toy_b", "product_name": "智能象",
                    "interaction_count": 1, "avg_holding_seconds": 20.0,
                },
                "previous": {
                    "product_key": "smart_toy_b",
                    "interaction_count": 0, "avg_holding_seconds": 0.0,
                },
                "interaction_trend": "up",
            },
        ], ensure_ascii=False)

        correct = (
            "两款商品互动热度都在上涨。"
            "智能象虽互动次数较少，但单次平均持有时长明显更长。"
        )
        grounded, checks = _answer_is_grounded(
            "rising-products", correct, [payload]
        )
        self.assertTrue(grounded, checks)

        wrong = (
            "两款商品互动热度都在上涨。"
            "智能熊虽互动次数较少，但单次平均持有时长明显更长。"
        )
        grounded, checks = _answer_is_grounded(
            "rising-products", wrong, [payload]
        )
        self.assertFalse(grounded)
        self.assertTrue(
            any("derived_comparison_direction" in item for item in checks), checks
        )

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_acceptance_separates_security_grounding_and_presentation(self, run):
        events = [
            {"type": "tool_use", "tool_name": "mcp__retailvision__rank_products",
             "args": {"store_id": "store-1"}},
            {"type": "tool_result", "tool_name": "mcp__retailvision__rank_products",
             "output": json.dumps([{"product_key": "smart_toy_a",
                                    "product_name": "智能熊", "pickup_count": 2,
                                    "recommendation_score": 6.2}]),
             "is_error": False},
            {"type": "result", "result":
             "I'll call rank_products. smart_toy_a recommendation_score=6.2；智能熊拿起2次。"},
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in events)).encode(), stderr=b"",
        )
        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="hot-products",
            question="最近哪些商品最火？", expected_tool="rank_products",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )
        self.assertFalse(passed)
        self.assertEqual("PASS", record["security_verdict"])
        self.assertEqual("PASS", record["grounding_verdict"])
        self.assertEqual("FAIL", record["presentation_verdict"])
        self.assertNotIn("forbidden_field_verdict", record)

    def test_grounding_allows_rank_and_concise_recommendation_summary(self):
        ranked = json.dumps([
            {
                "product_key": "smart_toy_a", "product_name": "智能熊",
                "completed_interaction_count": 2, "recommendation_score": 6.2,
                "recommended": True,
            },
            {
                "product_key": "smart_toy_b", "product_name": "智能象",
                "completed_interaction_count": 1, "recommendation_score": 3.33,
                "recommended": True,
            },
        ], ensure_ascii=False)
        grounded, checks = _answer_is_grounded(
            "hot-products", "智能熊互动热度排名第一。", [ranked]
        )
        self.assertTrue(grounded, checks)
        grounded, checks = _answer_is_grounded(
            "display-priority", "建议优先展示智能熊，推荐评分6.20。", [ranked]
        )
        self.assertTrue(grounded, checks)

    def test_grounding_rejects_wrong_safe_comparison_or_recommendation_direction(self):
        payload = json.dumps([
            {
                "product_key": "smart_toy_a", "product_name": "智能熊",
                "completed_interaction_count": 2,
                "recommendation_score": 6.2,
                "recommended": True,
            },
            {
                "product_key": "smart_toy_b", "product_name": "智能象",
                "completed_interaction_count": 1,
                "recommendation_score": 3.33,
                "recommended": True,
            },
        ], ensure_ascii=False)
        grounded, checks = _answer_is_grounded(
            "compare-toys", "智能象互动次数更多：2次，智能熊1次。", [payload]
        )
        self.assertFalse(grounded)
        self.assertTrue(any("derived_comparison_direction" in item for item in checks))
        grounded, checks = _answer_is_grounded(
            "display-priority", "建议优先展示智能象，推荐评分3.33。", [payload]
        )
        self.assertFalse(grounded)
        self.assertIn("rejected:structured_recommendation_contradiction", checks)

    def test_grounding_metric_binding_prefers_specific_holding_and_interaction_metrics(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a", "product_name": "智能熊",
            "completed_interaction_count": 2, "pickup_count": 2,
            "avg_holding_seconds": 6.0, "total_holding_seconds": 12.0,
            "max_holding_seconds": 7.0,
        }], ensure_ascii=False)
        grounded, checks = _answer_is_grounded(
            "hot-products", "智能熊被拿起2次，平均持有时长6秒，最长持有时长7秒。",
            [payload],
        )
        self.assertTrue(grounded, checks)

    def test_grounding_binds_chinese_number_before_metric_rank_date_and_track_count(self):
        payload = json.dumps([{
            "product_key": "smart_toy_a", "product_name": "智能熊",
            "completed_interaction_count": 2, "pickup_count": 2,
            "avg_holding_seconds": 6.0, "total_holding_seconds": 12.0,
            "max_holding_seconds": 7.0, "unique_person_track_count": 2,
            "display_priority": 1,
        }], ensure_ascii=False)
        answer = (
            "8月20日，智能熊陈列优先级第1：有2次拿起、2次完整互动，"
            "累计持有12秒，平均6秒，最大7秒，覆盖2条匿名轨迹。"
        )
        grounded, checks = _answer_is_grounded(
            "display-adjustment", answer, [payload]
        )
        self.assertTrue(grounded, checks)

    def test_grounding_binds_markdown_lists_tables_and_english(self):
        payload = json.dumps([
            {
                "product_key": "smart_toy_a", "product_name": "智能熊",
                "completed_interaction_count": 2,
                "total_holding_seconds": 12,
            },
            {
                "product_key": "smart_toy_b", "product_name": "智能象",
                "completed_interaction_count": 1,
                "total_holding_seconds": 20,
            },
        ], ensure_ascii=False)
        answers = (
            "1. smart_toy_a\n- completed interactions: 2\n- total holding: 12 seconds\n"
            "2. smart_toy_b\n- completed interactions: 1\n- total holding: 20 seconds",
            "| Product | Completed interactions | Total holding |\n"
            "| --- | ---: | ---: |\n"
            "| smart_toy_a | 2 | 12 |\n"
            "| smart_toy_b | 1 | 20 |",
            "smart_toy_a completed 2 interactions and total holding was 12 seconds; "
            "smart_toy_b completed 1 interaction and total holding was 20 seconds.",
        )
        for answer in answers:
            with self.subTest(answer=answer):
                grounded, checks = _answer_is_grounded(
                    "compare-toys", answer, [payload]
                )
                self.assertTrue(grounded, checks)

    def test_output_directory_is_non_overwriting_and_empty_directory_is_writable(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            old = root_path / "old-evidence"
            old.mkdir()
            old_file = old / "manifest.json"
            old_file.write_text('{"status":"HISTORICAL"}', encoding="utf-8")
            before = old_file.read_bytes()
            new = root_path / "new-evidence"
            _prepare_output_directory(new)
            self.assertTrue(new.is_dir())
            with self.assertRaisesRegex(
                SystemExit, "acceptance_output_directory_not_empty"
            ):
                _prepare_output_directory(old)
            self.assertEqual(before, old_file.read_bytes())

    def test_manifest_jsonl_and_test_summary_statistics_are_consistent(self):
        records = []
        for index, (scenario_id, _, expected_tool) in enumerate(SCENARIOS):
            records.append({
                "scenario_id": scenario_id,
                "selected_tool_names": [expected_tool],
                "tool_outcome": "success",
                "protocol_verdict": "PASS",
                "security_verdict": "PASS",
                "grounding_verdict": "PASS",
                "presentation_verdict": "PASS",
                "presentation_warnings": (
                    ["generic_empty_tool_result"] if index == 0 else []
                ),
                "final_classification": "SUCCESS",
                "retry_count": 0,
                "provider_rounds": [],
                "attempt_count": 2,
                "first_attempt_passed": False,
            })
        manifest = _manifest_from_records(
            records, {"provider": "test", "model": "test"}, "test_summary.json"
        )
        self.assertEqual("PASS", manifest["status"])
        self.assertEqual(7, manifest["scenario_count"])
        self.assertEqual(7, manifest["passed_count"])
        self.assertEqual(14, manifest["total_attempts"])
        self.assertEqual(0, manifest["first_attempt_passed_count"])
        self.assertEqual(1, manifest["presentation_warning_count"])
        with tempfile.TemporaryDirectory() as root:
            summary_path = Path(root) / "summary.json"
            summary_path.write_text(json.dumps({
                "suites": [{
                    "total": 10, "passed": 8, "failures": 1,
                    "errors": 0, "skipped": 1,
                }],
            }), encoding="utf-8")
            summary = _load_test_summary(summary_path)
            self.assertEqual(
                summary["suites"][0]["total"],
                sum(summary["suites"][0][key] for key in (
                    "passed", "failures", "errors", "skipped"
                )),
            )
            summary_path.write_text(json.dumps({
                "suites": [{
                    "total": 10, "passed": 9, "failures": 1,
                    "errors": 0, "skipped": 1,
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "test_summary_inconsistent"):
                _load_test_summary(summary_path)
            summary_path.write_text(json.dumps({
                "suites": [{
                    "total": 1.9, "passed": 1.9, "failures": 0,
                    "errors": 0, "skipped": 0,
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "test_summary_invalid"):
                _load_test_summary(summary_path)
            summary_path.write_text(json.dumps({"suites": [1]}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "test_summary_invalid"):
                _load_test_summary(summary_path)

    def test_real_agent_summary_must_match_emitted_manifest(self):
        summary = {"suites": [{
            "id": "real_agent_acceptance", "total": 7, "passed": 7,
            "failures": 0, "errors": 0, "skipped": 0,
        }]}
        manifest = {"scenario_count": 7, "passed_count": 3}
        with self.assertRaisesRegex(
            SystemExit, "test_summary_agent_acceptance_inconsistent"
        ):
            _validate_summary_against_manifest(summary, manifest)
        with self.assertRaisesRegex(
            SystemExit, "test_summary_agent_acceptance_missing"
        ):
            _validate_summary_against_manifest(
                {"suites": [{"id": "other"}]}, manifest
            )
        with self.assertRaisesRegex(SystemExit, "test_summary_invalid"):
            _validate_summary_against_manifest(
                {"suites": [{
                    "id": "real_agent_acceptance", "total": 7.9,
                    "passed": 4.9, "failures": 3.9, "errors": 0,
                    "skipped": 0,
                }]},
                manifest,
            )

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_forbidden_agent_content_is_redacted_before_evidence_is_returned(self, run):
        events = [
            {
                "type": "tool_use",
                "tool_name": "mcp__retailvision__rank_products",
                "args": {"store_id": "store-1", "api_key": "top-secret"},
            },
            {
                "type": "tool_result",
                "tool_name": "mcp__retailvision__rank_products",
                "output": json.dumps([{"product_key": "smart_toy_a", "pickup_count": 2}]),
                "is_error": False,
            },
            {"type": "result", "result": "smart_toy_a 拿起 2 次。"},
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in events)).encode(),
            stderr=b"",
        )
        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="hot-products",
            question="最近哪些商品最火？", expected_tool="rank_products",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )
        self.assertFalse(passed)
        self.assertEqual("FAIL", record["security_verdict"])
        self.assertEqual([], record["safe_arguments"])
        self.assertEqual("[redacted:security_failure]", record["final_answer"])
        self.assertNotIn("top-secret", json.dumps(record))

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_expected_tool_must_have_its_own_successful_result(self, run):
        events = [
            {
                "type": "tool_use",
                "tool_name": "mcp__retailvision__rank_products",
                "args": {"store_id": "store-1"},
            },
            {
                "type": "tool_result",
                "tool_name": "mcp__retailvision__get_product_behavior_summary",
                "output": json.dumps({
                    "product_key": "smart_toy_a", "pickup_count": 2,
                }),
                "is_error": False,
            },
            {"type": "result", "result": "smart_toy_a 拿起 2 次。"},
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in events)).encode(),
            stderr=b"",
        )
        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="hot-products",
            question="最近哪些商品最火？", expected_tool="rank_products",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )
        self.assertFalse(passed)
        self.assertEqual("tool_call_failed", record["tool_outcome"])

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_provider_diagnostics_classify_explicit_empty_stop_as_model_no_call(self, run):
        events = [
            {
                "type": "provider_diagnostics",
                "provider_round": 1,
                "advertised_tool_count": 13,
                "tool_call_delta_count": 0,
                "tool_call_complete_count": 0,
                "finish_reason": "stop",
                "answer_non_empty": False,
                "classification": "MODEL_NO_TOOL_CALL",
            },
            {"type": "result", "result": "", "duration_ms": 10},
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in events)).encode(),
            stderr=b"",
        )
        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="rising-products",
            question="哪些商品热度正在上涨？", expected_tool="analyze_product_trend",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )
        self.assertFalse(passed)
        self.assertEqual("MODEL_NO_TOOL_CALL", record["final_classification"])
        self.assertEqual("stop", record["provider_finish_reason"])
        self.assertEqual(13, record["advertised_tool_count"])
        self.assertFalse(record["answer_non_empty"])
        self.assertEqual(1, record["agent_invocation_count"])

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_parser_failure_classification_is_preserved(self, run):
        events = [
            {
                "type": "provider_diagnostics",
                "provider_round": 1,
                "advertised_tool_count": 13,
                "tool_call_delta_count": 1,
                "tool_call_complete_count": 0,
                "finish_reason": "",
                "answer_non_empty": False,
                "tool_call_parse_failure_count": 1,
                "classification": "STREAM_TOOL_CALL_PARSE_FAILED",
            },
            {"type": "result", "result": ""},
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in events)).encode(),
            stderr=b"",
        )
        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="display-adjustment",
            question="商品应该怎样调整陈列？", expected_tool="suggest_product_display",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )
        self.assertFalse(passed)
        self.assertEqual("STREAM_TOOL_CALL_PARSE_FAILED", record["final_classification"])
        self.assertEqual(0, record["tool_call_count"])
        self.assertEqual("not_called", record["mcp_tool_outcome"])

    @patch("scripts.run_store_intelligence_agent_acceptance.subprocess.run")
    def test_retry_event_is_counted_and_blocks_success(self, run):
        events = [
            {"type": "retry", "wait_ms": 0},
            {
                "type": "tool_use",
                "tool_name": "mcp__retailvision__rank_products",
                "args": {"store_id": "store-1"},
            },
            {
                "type": "tool_result",
                "tool_name": "mcp__retailvision__rank_products",
                "output": json.dumps([{
                    "product_key": "smart_toy_a", "pickup_count": 2,
                }]),
                "is_error": False,
            },
            {
                "type": "provider_diagnostics",
                "advertised_tool_count": 13,
                "finish_reason": "stop",
                "classification": "SUCCESS",
            },
            {"type": "result", "result": "smart_toy_a 拿起 2 次。"},
        ]
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=("\n".join(json.dumps(item) for item in events)).encode(),
            stderr=b"",
        )
        record, passed = _run_scenario(
            java="java", jar=Path("mewcode.jar"), config=Path("config.yaml"),
            java_repo=Path("."), scenario_id="hot-products",
            question="最近哪些商品最火？", expected_tool="rank_products",
            provider={"provider": "test", "model": "test"}, timeout_seconds=1,
        )
        self.assertFalse(passed)
        self.assertEqual(1, record["retry_count"])


if __name__ == "__main__":
    unittest.main()
