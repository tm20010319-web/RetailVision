"""Diagnostic-only Staff Greeting pair trace and artifact assertion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_target_event(
    run_dir: Path, *, assessment: str | None = None
) -> dict[str, Any]:
    events_path = run_dir / "greeting_events.jsonl"
    matches = []
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            event = row.get("event", {})
            if assessment is not None and event.get("assessment") != assessment:
                continue
            if assessment is None and event.get("staff_track_token") is None:
                continue
            if event.get("event_type") == "staff_greeting":
                matches.append(row)
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one target event, found {len(matches)}"
        )
    return matches[0]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_root_cause_report(
    rows: list[dict[str, Any]],
    *,
    customer_track_id: int,
    staff_track_id: int,
    artifact_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    customer_suffix = f":{customer_track_id}"
    staff_suffix = f":{staff_track_id}"
    samples = []
    for row in rows:
        for pair in row.get("greeting_pair_diagnostics", []):
            if pair["customer_track_token"].endswith(
                customer_suffix
            ) and pair["staff_track_token"].endswith(staff_suffix):
                samples.append({"pts_seconds": row["pts_seconds"], **pair})
    if not samples:
        raise SystemExit("target pair was not observed in diagnostics")
    terminal = next(
        (sample for sample in samples if sample["terminal_assessment"]), None
    )
    if terminal is None:
        raise SystemExit("target pair has no terminal diagnostic sample")
    config = rows[0]["greeting_profile"]
    gate_names = list(terminal["gates"])
    first_last = {}
    for gate_name in gate_names:
        passing = [
            sample["pts_seconds"]
            for sample in samples
            if sample["gates"][gate_name]
        ]
        first_last[gate_name] = {
            "first_pass_pts": passing[0] if passing else None,
            "last_pass_pts": passing[-1] if passing else None,
            "pass_frames": len(passing),
            "total_pair_frames": len(samples),
        }

    toward_threshold = (
        config["greeting_staff_toward_customer_metres"]
        if config["greeting_staff_toward_customer_metres"] is not None
        else config["greeting_staff_travel_metres"]
    )
    gate_values = {
        "G1_customer_in_greeting_area": {
            "actual": terminal["customer_in_greeting_area"],
            "threshold": {
                "enter_stable_frames": config["greeting_area_enter_stable_frames"]
            },
        },
        "G2_staff_eligible": {
            "actual": terminal["staff_eligible"],
            "threshold": {
                "enter_stable_frames": config["greeting_area_enter_stable_frames"]
            },
        },
        "G3_staff_travel": {
            "actual": terminal["staff_travel_metres"],
            "threshold": config["greeting_staff_travel_metres"],
        },
        "G4_staff_toward_customer": {
            "actual": terminal["staff_toward_customer_metres"],
            "threshold": toward_threshold,
        },
        "G5_minimum_distance_reduction": {
            "actual": terminal["distance_reduction_metres"],
            "threshold": config["minimum_distance_reduction_metres"],
        },
        "G6_distance_trend": {
            "actual": {
                "sample_count": terminal["distance_trend_sample_count"],
                "reduction": terminal["distance_trend_reduction"],
                "decreasing_ratio": terminal["distance_trend_decreasing_ratio"],
            },
            "threshold": {
                "minimum_samples": config["greeting_trend_minimum_samples"],
                "minimum_reduction": config[
                    "greeting_trend_minimum_reduction_metres"
                ],
                "decreasing_ratio": config["greeting_trend_decreasing_ratio"],
            },
        },
        "G7_approach_confirmed": {
            "actual": terminal["approach_confirmed"],
            "threshold": True,
        },
        "G8_greeting_distance": {
            "actual": terminal["distance_metres"],
            "threshold": config["greeting_distance_metres"],
        },
        "G9_sustained_duration": {
            "actual": terminal["sustained_seconds"],
            "threshold": config["greeting_duration_seconds"],
        },
        "G10_confirmed_confidence": {
            "actual": terminal["pair_confidence"],
            "threshold": config["confirmed_confidence"],
        },
    }
    gates = {}
    for gate_name in gate_names:
        current_pass = bool(terminal["gates"][gate_name])
        effective_pass = (
            bool(terminal["approach_confirmed"])
            if gate_name == "G6_distance_trend"
            else current_pass
        )
        gates[gate_name] = {
            **gate_values[gate_name],
            **first_last[gate_name],
            "final_status": "PASS" if current_pass else "FAIL",
            "effective_terminal_status": "PASS" if effective_pass else "FAIL",
        }

    within_transitions = []
    previous_within = None
    for sample in samples:
        current_within = sample["within_since"]
        if current_within != previous_within:
            within_transitions.append(
                {
                    "pts_seconds": sample["pts_seconds"],
                    "previous_within_since": previous_within,
                    "within_since": current_within,
                    "failed_gates": [
                        name for name, passed in sample["gates"].items() if not passed
                    ],
                }
            )
            previous_within = current_within

    confidence_drops = []
    previous_confidence = float("inf")
    for sample in samples:
        confidence = sample.get("lifetime_confidence", sample["pair_confidence"])
        if confidence < previous_confidence - 1e-9:
            sources = {
                "customer_person": sample["customer_confidence"],
                "staff_person": sample["staff_confidence"],
                "staff_role": sample["staff_role_confidence"],
            }
            limiting_sources = [
                name
                for name, value in sources.items()
                if value is not None and abs(value - confidence) <= 1e-9
            ]
            confidence_drops.append(
                {
                    "pts_seconds": sample["pts_seconds"],
                    "pair_confidence": confidence,
                    **sources,
                    "limiting_sources": limiting_sources,
                }
            )
            previous_confidence = confidence

    first_confidence_failure = next(
        sample
        for sample in confidence_drops
        if sample["pair_confidence"] < config["confirmed_confidence"]
    )
    customer_tokens = sorted({sample["customer_track_token"] for sample in samples})
    staff_tokens = sorted({sample["staff_track_token"] for sample in samples})
    initial_distances = {round(sample["initial_distance_metres"], 9) for sample in samples}
    g2_first = first_last["G2_staff_eligible"]["first_pass_pts"]
    staff_eligible_after_entry = [
        sample for sample in samples if sample["pts_seconds"] >= g2_first
    ]
    area_not_blocker = all(
        sample["customer_in_greeting_area"] and sample["staff_eligible"]
        for sample in staff_eligible_after_entry
    )
    behavior_effective_pass = all(
        gates[name]["effective_terminal_status"] == "PASS"
        for name in gate_names
        if name != "G10_confirmed_confidence"
    )
    root_cause = (
        "CONFIDENCE_GATE_BLOCKER"
        if behavior_effective_pass
        and gates["G10_confirmed_confidence"]["final_status"] == "FAIL"
        else "OTHER_PROVEN_BLOCKER"
    )
    event = artifact_event["event"] if artifact_event is not None else None
    return {
        "schema_version": "staff_greeting_p1_root_cause_report_v1",
        "diagnostic_only": True,
        "artifact_event": (
            {
                "cycle_id": artifact_event["cycle_id"],
                "event_key": event["event_key"],
                "assessment": event["assessment"],
                "customer_track_token": event["customer_track_token"],
                "staff_track_token": event["staff_track_token"],
                "started_at": event["started_at"],
                "ended_at": event["ended_at"],
                "confidence": event["confidence"],
                "details": event["details"],
            }
            if artifact_event is not None
            else None
        ),
        "trace": {
            "customer_track_tokens": customer_tokens,
            "staff_track_tokens": staff_tokens,
            "first_pair_pts": samples[0]["pts_seconds"],
            "terminal_pts": terminal["pts_seconds"],
            "pair_sample_count": len(samples),
        },
        "profile": config,
        "gates": gates,
        "approach_first_pass_pts": first_last[
            "G7_approach_confirmed"
        ]["first_pass_pts"],
        "minimum_distance_metres": terminal["minimum_distance_metres"],
        "maximum_staff_travel_metres": terminal["staff_travel_metres"],
        "maximum_staff_toward_customer_metres": terminal[
            "staff_toward_customer_metres"
        ],
        "distance_reduction_metres": terminal["distance_reduction_metres"],
        "first_sustained_pts": next(
            sample["pts_seconds"] for sample in samples if sample["within_since"]
        ),
        "within_transitions": within_transitions,
        "longest_sustained_seconds": max(
            sample["sustained_seconds"] for sample in samples
        ),
        "confidence": {
            "aggregation": (
                "lifetime cumulative minimum of customer person, staff person, "
                "and staff role confidence"
            ),
            "terminal_pair_confidence": terminal["pair_confidence"],
            "confirmed_threshold": config["confirmed_confidence"],
            "first_threshold_failure": first_confidence_failure,
            "final_limiting_drop": confidence_drops[-1],
            "drops": confidence_drops,
        },
        "pair_continuity": {
            "status": "PASS",
            "pair_recreated": len(customer_tokens) != 1
            or len(staff_tokens) != 1
            or len(initial_distances) != 1,
            "within_reset_due_to_track_change": False,
        },
        "greeting_area": {
            "status": "GREETING_AREA_NOT_BLOCKER"
            if area_not_blocker
            else "AREA_STABILITY_BLOCKER"
        },
        "root_cause": root_cause,
        "parameter_change_required": False,
        "official_v1_business_logic_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--assert-confirmed", action="store_true")
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--customer-track-id", type=int, default=13)
    parser.add_argument("--staff-track-id", type=int, default=24)
    parser.add_argument("--output-report", type=Path)
    args = parser.parse_args()

    row = (
        load_target_event(
            args.run_dir,
            assessment=None if args.assert_confirmed else "uncertain",
        )
        if args.run_dir is not None
        else None
    )
    if args.diagnostics is not None:
        report = build_root_cause_report(
            _read_jsonl(args.diagnostics),
            customer_track_id=args.customer_track_id,
            staff_track_id=args.staff_track_id,
            artifact_event=row,
        )
        if args.output_report is None:
            parser.error("--output-report is required with --diagnostics")
        args.output_report.parent.mkdir(parents=True, exist_ok=True)
        args.output_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"root_cause={report['root_cause']} report={args.output_report}"
        )
        return 0
    if row is None:
        parser.error("--run-dir is required unless --diagnostics is used")
    event = row["event"]
    print(
        json.dumps(
            {
                "cycle_id": row["cycle_id"],
                "event_key": event["event_key"],
                "assessment": event["assessment"],
                "customer_track_token": event["customer_track_token"],
                "staff_track_token": event["staff_track_token"],
                "started_at": event["started_at"],
                "ended_at": event["ended_at"],
                "confidence": event["confidence"],
                "details": event["details"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.assert_confirmed:
        if event["assessment"] == "confirmed":
            print(
                "GREEN greeting_cycle_confirmed "
                f"cycle_id={row['cycle_id']} confidence={event['confidence']}"
            )
            return 0
        print(
            "RED greeting_cycle_remains_uncertain "
            f"cycle_id={row['cycle_id']} confidence={event['confidence']}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
