"""Observe three independent, one-attempt autonomous MCP Agent batches."""

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

from scripts.run_store_intelligence_agent_acceptance import (  # noqa: E402
    SCENARIOS,
    build_config,
    database_url,
    prepare_output_directory,
    provider_identity,
    run_scenario,
    write_json,
)


def _record_passed(item: dict) -> bool:
    return (
        item.get("tool_outcome") == "success"
        and item.get("protocol_verdict") == "PASS"
        and item.get("security_verdict") == "PASS"
        and item.get("grounding_verdict") == "PASS"
        and item.get("presentation_verdict") == "PASS"
        and item.get("final_classification") == "SUCCESS"
        and int(item.get("retry_count", 0)) == 0
    )


def _run_batch(*, args, source, provider, output_dir: Path, batch_number: int) -> dict:
    batch_dir = output_dir / f"batch-{batch_number}"
    batch_dir.mkdir()
    records = []
    raw_outputs = []
    with tempfile.TemporaryDirectory(prefix=f"retailvision-business-batch-{batch_number}-") as temp:
        temporary = Path(temp)
        database = temporary / "fixture.db"
        fixture = subprocess.run(
            [
                args.python,
                str(PROJECT_ROOT / "scripts" / "create_product_analytics_acceptance_fixture.py"),
                "--database-url",
                database_url(database),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if fixture.returncode != 0 or not database.is_file():
            raise SystemExit(f"fixture_creation_failed_batch_{batch_number}")
        config = temporary / "mewcode-business-stability.yaml"
        config.write_text(
            yaml.safe_dump(
                build_config(source, database, args.python),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        for scenario_id, question, expected_tool in SCENARIOS:
            record, _ = run_scenario(
                java=args.java,
                jar=args.java_repo.resolve() / "target" / "mewcode.jar",
                config=config,
                java_repo=args.java_repo.resolve(),
                scenario_id=scenario_id,
                question=question,
                expected_tool=expected_tool,
                provider=provider,
                timeout_seconds=args.timeout_seconds,
                capture_raw=True,
            )
            raw_outputs.append({
                "scenario_id": scenario_id,
                "raw_stdout": record.pop("_raw_stdout", ""),
            })
            record["batch"] = batch_number
            record["attempt_count"] = 1
            records.append(record)

    evidence_name = "agent_acceptance.jsonl"
    (batch_dir / evidence_name).write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    # Preserve the exact stream-json stdout for audit/replay. This file is
    # intentionally separate from sanitized acceptance evidence.
    (batch_dir / "raw_agent_output.jsonl").write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in raw_outputs
        ),
        encoding="utf-8",
    )
    passed = sum(_record_passed(item) for item in records)
    manifest = {
        "schema_version": "r5-04-2-minimal-autonomous-answer-gate-batch-v1",
        "batch": batch_number,
        "status": "PASS" if passed == len(SCENARIOS) else "BLOCKED",
        "scenario_count": len(records),
        "passed_count": passed,
        "protocol_passed_count": sum(item.get("protocol_verdict") == "PASS" for item in records),
        "protocol_failures": sum(item.get("protocol_verdict") != "PASS" for item in records),
        "security_passed_count": sum(item.get("security_verdict") == "PASS" for item in records),
        "security_failures": sum(item.get("security_verdict") != "PASS" for item in records),
        "grounding_passed_count": sum(item.get("grounding_verdict") == "PASS" for item in records),
        "grounding_failures": sum(item.get("grounding_verdict") != "PASS" for item in records),
        "presentation_passed_count": sum(item.get("presentation_verdict") == "PASS" for item in records),
        "presentation_failures": sum(item.get("presentation_verdict") != "PASS" for item in records),
        "presentation_warning_count": sum(
            len(item.get("presentation_warnings", [])) for item in records
        ),
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
        "total_attempts": len(records),
        "agent_invocations": len(records),
        "mcp_tool_calls": sum(int(item.get("tool_call_count", 0)) for item in records),
        "retries": sum(int(item.get("retry_count", 0)) for item in records),
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
        "evidence_file": evidence_name,
        "raw_output_file": "raw_agent_output.jsonl",
    }
    write_json(batch_dir / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-repo", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--java", default="java")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--batches", type=int, default=3)
    args = parser.parse_args(argv)
    if args.batches != 3:
        raise SystemExit("stability_requires_exactly_three_batches")
    jar = args.java_repo.resolve() / "target" / "mewcode.jar"
    if not jar.is_file():
        raise SystemExit("java_artifact_missing")
    source = yaml.safe_load(args.provider_config.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("providers"), list):
        raise SystemExit("provider_config_missing")
    provider = provider_identity(source["providers"][0])
    prepare_output_directory(args.output_dir)
    batches = [_run_batch(args=args, source=source, provider=provider, output_dir=args.output_dir, batch_number=i)
               for i in range(1, 4)]
    passed = sum(item["passed_count"] for item in batches)
    aggregate = {
        "schema_version": "r5-04-2-minimal-autonomous-answer-gate-stability-v1",
        "status": "PASS" if all(item["status"] == "PASS" for item in batches) else "BLOCKED",
        "batch_count": 3,
        "scenario_count": 21,
        "passed_count": passed,
        "required_pass_count": 21,
        "protocol_passed_count": sum(item["protocol_passed_count"] for item in batches),
        "protocol_failures": sum(item["protocol_failures"] for item in batches),
        "security_passed_count": sum(item["security_passed_count"] for item in batches),
        "security_failures": sum(item["security_failures"] for item in batches),
        "grounding_passed_count": sum(item["grounding_passed_count"] for item in batches),
        "grounding_failures": sum(item["grounding_failures"] for item in batches),
        "presentation_passed_count": sum(item["presentation_passed_count"] for item in batches),
        "presentation_failures": sum(item["presentation_failures"] for item in batches),
        "presentation_warning_count": sum(item["presentation_warning_count"] for item in batches),
        "stream_parse_failures": sum(item["stream_parse_failures"] for item in batches),
        "argument_assembly_failures": sum(item["argument_assembly_failures"] for item in batches),
        "unknown_product_key_failures": sum(item["unknown_product_key_failures"] for item in batches),
        "batch_manifests": [f"batch-{i}/manifest.json" for i in range(1, 4)],
        "total_attempts": sum(item["total_attempts"] for item in batches),
        "agent_invocations": sum(item["agent_invocations"] for item in batches),
        "mcp_tool_calls": sum(item["mcp_tool_calls"] for item in batches),
        "retries": sum(item["retries"] for item in batches),
        "failure_scenarios": [
            {"batch": batch["batch"], **failure}
            for batch in batches for failure in batch["failure_scenarios"]
        ],
        "final_classifications": {
            classification: sum(
                int(batch.get("final_classifications", {}).get(classification, 0))
                for batch in batches
            )
            for classification in sorted({
                classification
                for batch in batches
                for classification in batch.get("final_classifications", {})
            })
        },
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.output_dir / "manifest.json", aggregate)
    print(json.dumps(aggregate, ensure_ascii=False))
    return 0 if aggregate["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
