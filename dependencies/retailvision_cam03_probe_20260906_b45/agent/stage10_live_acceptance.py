"""Stage 10.3 live-scenario acceptance over recorded Stage 10 facts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from agent.stage10_presentation import product_presentation


@dataclass(frozen=True)
class Stage103ScenarioResult:
    scenario: str
    scenario_name: str
    result: str
    started_at: Optional[str]
    ended_at: Optional[str]
    person_track_ids: tuple[int, ...]
    person_track_tokens: tuple[str, ...]
    display_names: tuple[str, ...]
    operation_product_key: Optional[str]
    actual_action: str
    system_events: tuple[Mapping[str, object], ...]
    false_positive_count: int
    false_pick_up_count: int
    false_put_down_count: int
    false_negative_count: int
    wrong_actor_count: int
    product_id_swap_count: int
    session_break_count: int
    overlay_path: str
    keyframe_paths: tuple[str, ...]
    diagnostics: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "scenario_name": self.scenario_name,
            "result": self.result,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "person_track_ids": list(self.person_track_ids),
            "person_track_tokens": list(self.person_track_tokens),
            "display_names": list(self.display_names),
            "operation_product_key": self.operation_product_key,
            "actual_action": self.actual_action,
            "system_events": [dict(event) for event in self.system_events],
            "false_positive_count": self.false_positive_count,
            "false_pick_up_count": self.false_pick_up_count,
            "false_put_down_count": self.false_put_down_count,
            "false_negative_count": self.false_negative_count,
            "wrong_actor_count": self.wrong_actor_count,
            "product_id_swap_count": self.product_id_swap_count,
            "session_break_count": self.session_break_count,
            "overlay_path": self.overlay_path,
            "keyframe_paths": list(self.keyframe_paths),
            "diagnostics": dict(self.diagnostics),
        }


def _product_id_swaps(frames: Sequence[Mapping[str, object]]) -> int:
    baseline: dict[str, tuple[object, object]] = {}
    swaps = 0
    for frame in frames:
        for product in frame.get("products", ()):
            key = str(product.get("product_key"))
            identity = (product.get("track_id"), product.get("track_token"))
            expected = baseline.setdefault(key, identity)
            swaps += identity != expected
    return swaps


def evaluate_stage10_3_scenario(
    scenario: str,
    *,
    frames: Sequence[Mapping[str, object]],
    events: Sequence[Mapping[str, object]],
    overlay_path: str,
    keyframe_paths: Sequence[str] = (),
    expected_product_key: Optional[str] = None,
    expected_actor_track_token: Optional[str] = None,
    runtime_errors: int = 0,
) -> Stage103ScenarioResult:
    code = str(scenario).strip().upper()
    if code not in {"A", "B", "C", "D", "E", "F"}:
        raise ValueError(f"unsupported Stage 10.3 scenario: {scenario}")
    values = tuple(dict(frame) for frame in frames)
    event_values = tuple(dict(event) for event in events)
    persons = [person for frame in values for person in frame.get("persons", ())]
    person_ids = tuple(sorted({int(person["track_id"]) for person in persons}))
    person_tokens = tuple(sorted({str(person["track_token"]) for person in persons}))
    display_names = tuple(sorted({
        str(person.get("display_name") or f"顾客{person['track_id']}")
        for person in persons
    }))
    swaps = _product_id_swaps(values)
    every_frame_has_person = bool(values) and all(frame.get("persons") for frame in values)
    single_person_track_continuity = (
        every_frame_has_person
        and len(person_ids) == 1
        and len(person_tokens) == 1
    )
    all_interactions = [
        interaction
        for frame in values
        for interaction in frame.get("interactions", ())
    ]
    products_stable = all(
        str(interaction.get("motion_state", "STABLE")) == "STABLE"
        for interaction in all_interactions
    )
    product_keys = {
        str(product.get("product_key"))
        for frame in values
        for product in frame.get("products", ())
    }
    association_observed = any(
        str(association.get("association_status")) == "ASSOCIATED"
        for frame in values
        for association in frame.get("associations", ())
    )
    target_key = expected_product_key or {
        "C": "smart_toy_a",
        "D": "smart_toy_b",
        "E": "smart_toy_a",
        "F": "smart_toy_a",
    }.get(code)
    target_events = tuple(
        event
        for event in event_values
        if str(event.get("product", {}).get("product_key")) == target_key
    )
    other_events = tuple(event for event in event_values if event not in target_events)
    pickup_count = sum(str(event.get("event_type")) == "PICK_UP" for event in target_events)
    putdown_count = sum(str(event.get("event_type")) == "PUT_DOWN" for event in target_events)
    target_sessions = {
        str(event.get("interaction", {}).get("interaction_session_id"))
        for event in target_events
        if event.get("interaction", {}).get("interaction_session_id")
    }
    event_actor_tokens = {
        str(event.get("person", {}).get("track_token"))
        for event in target_events
        if event.get("person", {}).get("track_token")
    }
    held_rows = [
        interaction
        for frame in values
        for interaction in frame.get("interactions", ())
        if str(interaction.get("product_key")) == target_key
        and str(interaction.get("interaction_state")) == "HELD"
    ]
    held_sessions = {
        str(interaction.get("interaction_session_id"))
        for interaction in held_rows
        if interaction.get("interaction_session_id")
    }
    held_observed = bool(held_rows)
    target_interactions = [
        interaction
        for frame in values
        for interaction in frame.get("interactions", ())
        if str(interaction.get("product_key")) == target_key
    ]
    occlusion_observed = any(
        str(interaction.get("product_track_status")) == "MISSING_PENDING"
        or bool(interaction.get("held_missing_grace"))
        for interaction in target_interactions
    )
    recovery_observed = any(
        bool(interaction.get("recovered_from_missing"))
        for interaction in target_interactions
    )
    interaction_actor_tokens = {
        str(interaction.get("interaction_actor_person_track_token"))
        for interaction in target_interactions
        if interaction.get("interaction_actor_person_track_token")
    }
    all_actor_tokens = event_actor_tokens | interaction_actor_tokens
    multi_person_observed = any(
        len(tuple(frame.get("persons", ()))) >= 2
        for frame in values
    )
    session_breaks = 0
    if code in {"C", "D", "E", "F"}:
        session_breaks += len(target_sessions) != 1
        session_breaks += bool(held_sessions and held_sessions != target_sessions)
    wrong_actor_count = 0
    if expected_actor_track_token is not None:
        wrong_actor_count = len({
            token
            for token in all_actor_tokens
            if token != expected_actor_track_token
        })
    elif len(all_actor_tokens) > 1:
        wrong_actor_count = len(all_actor_tokens) - 1
    false_positive_count = (
        len(other_events)
        + max(0, pickup_count - 1)
        + max(0, putdown_count - 1)
    )
    false_pick_up_count = sum(
        str(event.get("event_type")) == "PICK_UP" for event in other_events
    ) + max(0, pickup_count - 1)
    false_put_down_count = sum(
        str(event.get("event_type")) == "PUT_DOWN" for event in other_events
    ) + max(0, putdown_count - 1)
    false_negative_count = max(0, 1 - pickup_count) + max(0, 1 - putdown_count)

    common_ready = (
        bool(person_ids)
        and {"smart_toy_a", "smart_toy_b"}.issubset(product_keys)
        and swaps == 0
        and int(runtime_errors) == 0
    )
    if code in {"A", "B"}:
        passed = (
            common_ready
            and every_frame_has_person
            and not event_values
            and (code != "A" or single_person_track_continuity)
            and (code != "A" or products_stable)
            and (code != "B" or association_observed)
        )
        false_positive_count = len(event_values)
        false_pick_up_count = sum(
            str(event.get("event_type")) == "PICK_UP" for event in event_values
        )
        false_put_down_count = sum(
            str(event.get("event_type")) == "PUT_DOWN" for event in event_values
        )
        false_negative_count = 0
        session_breaks = 0
        wrong_actor_count = 0
    else:
        passed = (
            common_ready
            and pickup_count == 1
            and putdown_count == 1
            and held_observed
            and false_positive_count == 0
            and false_negative_count == 0
            and wrong_actor_count == 0
            and session_breaks == 0
            and (code != "E" or (occlusion_observed and recovery_observed))
            and (code != "F" or multi_person_observed)
        )
    scenario_names = {
        "A": "有人出现但不碰商品",
        "B": "靠近商品但不拿",
        "C": "拿起智能熊",
        "D": "拿起智能小象",
        "E": "遮挡恢复",
        "F": "多人干扰",
    }
    actual_actions = {
        "A": "有人出现，商品保持静止",
        "B": "靠近商品，未拿起",
        "C": "拿起、持有并放回智能熊",
        "D": "拿起、持有并放回智能小象",
        "E": "拿起智能熊，持有中短暂遮挡，恢复后放回",
        "F": "两人同场，指定操作员拿起并放回智能熊",
    }
    return Stage103ScenarioResult(
        scenario=code,
        scenario_name=scenario_names[code],
        result="PASS" if passed else "FAIL",
        started_at=None if not values else str(values[0].get("timestamp")),
        ended_at=None if not values else str(values[-1].get("timestamp")),
        person_track_ids=person_ids,
        person_track_tokens=person_tokens,
        display_names=display_names,
        operation_product_key=target_key,
        actual_action=actual_actions[code],
        system_events=event_values,
        false_positive_count=false_positive_count,
        false_pick_up_count=false_pick_up_count,
        false_put_down_count=false_put_down_count,
        false_negative_count=false_negative_count,
        wrong_actor_count=wrong_actor_count,
        product_id_swap_count=swaps,
        session_break_count=session_breaks,
        overlay_path=str(overlay_path),
        keyframe_paths=tuple(str(path) for path in keyframe_paths),
        diagnostics={
            "runtime_errors": int(runtime_errors),
            "frame_count": len(values),
            "every_frame_has_person": every_frame_has_person,
            "single_person_track_continuity": single_person_track_continuity,
            "products_stable": products_stable,
            "association_observed": association_observed,
            "target_product_display_name": (
                None if target_key is None else product_presentation(target_key).display_name
            ),
            "pickup_count": pickup_count,
            "putdown_count": putdown_count,
            "held_observed": held_observed,
            "occlusion_observed": occlusion_observed,
            "recovery_observed": recovery_observed,
            "multi_person_observed": multi_person_observed,
            "interaction_session_ids": sorted(target_sessions | held_sessions),
            "event_actor_track_tokens": sorted(event_actor_tokens),
            "interaction_actor_track_tokens": sorted(interaction_actor_tokens),
        },
    )


def evaluate_stage10_3_artifacts(
    scenario: str,
    *,
    artifact_dir: str | Path,
    expected_product_key: Optional[str] = None,
    expected_actor_track_token: Optional[str] = None,
    overlay_path: str | Path | None = None,
    keyframe_paths: Optional[Sequence[str | Path]] = None,
) -> Stage103ScenarioResult:
    """Evaluate one Stage 10.3 scenario from recorded runtime artifacts."""

    root = Path(artifact_dir)
    frames = _read_jsonl(root / "frame_results.jsonl")
    events = _read_jsonl(root / "event_stream.jsonl")
    runtime_summary = json.loads(
        (root / "runtime_summary.json").read_text(encoding="utf-8-sig")
    )
    resolved_overlay = Path(overlay_path) if overlay_path is not None else root / "overlay.mp4"
    resolved_keyframes = (
        tuple(Path(path) for path in keyframe_paths)
        if keyframe_paths is not None
        else tuple(sorted((root / "keyframes").glob("*.jpg")))
    )
    return evaluate_stage10_3_scenario(
        scenario,
        frames=frames,
        events=events,
        expected_product_key=expected_product_key,
        expected_actor_track_token=expected_actor_track_token,
        overlay_path=str(resolved_overlay),
        keyframe_paths=[str(path) for path in resolved_keyframes],
        runtime_errors=int(runtime_summary.get("runtime_errors", 0)),
    )


def summarize_stage10_3_acceptance(
    results: Mapping[str, Mapping[str, object] | Stage103ScenarioResult],
) -> dict[str, object]:
    """Build the A-F aggregate without changing individual scenario facts."""

    normalized = {
        str(code).upper(): (
            value.to_dict() if isinstance(value, Stage103ScenarioResult) else dict(value)
        )
        for code, value in results.items()
    }
    required = tuple("ABCDEF")
    missing = [code for code in required if code not in normalized]
    failed = [
        code
        for code in required
        if code in normalized and str(normalized[code].get("result")) != "PASS"
    ]
    return {
        "result": "PASS" if not missing and not failed else "FAIL",
        "scenario_results": {
            code: normalized[code].get("result", "FAIL")
            for code in required
            if code in normalized
        },
        "missing_scenarios": missing,
        "failed_scenarios": failed,
        "false_pick_up_count": sum(
            int(normalized[code].get("false_pick_up_count", 0))
            for code in required
            if code in normalized
        ),
        "false_put_down_count": sum(
            int(normalized[code].get("false_put_down_count", 0))
            for code in required
            if code in normalized
        ),
        "wrong_actor_count": sum(
            int(normalized[code].get("wrong_actor_count", 0))
            for code in required
            if code in normalized
        ),
        "product_id_swap_count": sum(
            int(normalized[code].get("product_id_swap_count", 0))
            for code in required
            if code in normalized
        ),
        "session_break_count": sum(
            int(normalized[code].get("session_break_count", 0))
            for code in required
            if code in normalized
        ),
        "stage11_entered": False,
    }


def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            value = line.strip()
            if not value:
                continue
            row = json.loads(value)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            rows.append(row)
    return tuple(rows)
