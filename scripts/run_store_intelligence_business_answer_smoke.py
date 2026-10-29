"""Run the R5-03.2 real-Agent business-answer smoke scenarios once each."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_store_intelligence_agent_acceptance import (
    _build_config,
    _database_url,
    _prepare_output_directory,
    _provider_identity,
    _run_scenario,
    _write_json,
)


SCENARIOS = (
    ("concise-heat-report", "最近哪个商品热度最高？给我一份简短报告。", "rank_products"),
    ("display-priority-summary", "哪个商品应该优先展示？", "recommend_hot_products"),
    ("interaction-comparison", "智能熊和智能象哪个互动更多？", "compare_product_behavior"),
    ("subjective-popularity", "哪个商品更受欢迎？", "rank_products"),
    ("subjective-interest", "用户可能对哪些商品更感兴趣？", "rank_products"),
    ("concise-product-summary", "智能熊表现怎么样？", "get_product_behavior_summary"),
    ("full-raw-metrics", "把所有原始指标都列出来。", "rank_products"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-repo", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--java", default="java")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)

    jar = args.java_repo.resolve() / "target" / "mewcode.jar"
    if not jar.is_file():
        raise SystemExit("java_artifact_missing")
    source = yaml.safe_load(args.provider_config.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("providers"), list):
        raise SystemExit("provider_config_missing")
    provider = _provider_identity(source["providers"][0])
    _prepare_output_directory(args.output_dir)

    records: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="retailvision-business-smoke-") as temp:
        temporary = Path(temp)
        database = temporary / "fixture.db"
        fixture = subprocess.run(
            [
                args.python,
                str(Path(__file__).with_name("create_product_analytics_acceptance_fixture.py")),
                "--database-url",
                _database_url(database),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if fixture.returncode != 0 or not database.is_file():
            raise SystemExit("fixture_creation_failed")
        config = temporary / "mewcode-business-smoke.yaml"
        config.write_text(
            yaml.safe_dump(
                _build_config(source, database, args.python),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        for scenario_id, question, expected_tool in SCENARIOS:
            record, _ = _run_scenario(
                java=args.java,
                jar=jar,
                config=config,
                java_repo=args.java_repo.resolve(),
                scenario_id=scenario_id,
                question=question,
                expected_tool=expected_tool,
                provider=provider,
                timeout_seconds=args.timeout_seconds,
            )
            records.append(record)

    (args.output_dir / "business_answer_smoke.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    passed = sum(
        item.get("tool_outcome") == "success"
        and item.get("grounding_verdict") == "PASS"
        and item.get("forbidden_field_verdict") == "PASS"
        for item in records
    )
    manifest = {
        "schema_version": "r5-03-2-business-answer-smoke-v1",
        "status": "PASS" if passed == len(SCENARIOS) else "BLOCKED",
        "scenario_count": len(records),
        "passed_count": passed,
        "grounding_failures": sum(item.get("grounding_verdict") != "PASS" for item in records),
        "forbidden_field_failures": sum(item.get("forbidden_field_verdict") != "PASS" for item in records),
        "attempt_count": len(records),
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_file": "business_answer_smoke.jsonl",
    }
    _write_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
