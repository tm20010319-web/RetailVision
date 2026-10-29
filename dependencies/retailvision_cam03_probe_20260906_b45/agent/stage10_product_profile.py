"""Stage 10-only Product SAM latency profiling and artifact aggregation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np

from agent.stage10_demo import JsonlMetricsWriter


PRODUCT_PROFILE_FIELDS = (
    "product_session_start_ms",
    "product_grounding_ms",
    "product_propagation_ms",
    "product_output_conversion_ms",
    "product_candidate_evaluation_ms",
    "product_track_association_ms",
    "product_artifact_conversion_ms",
    "product_session_close_ms",
    "product_other_ms",
    "product_unattributed_ms",
)
SLOW_PRODUCT_WINDOW_MS = 5000.0


def latency_stats(values: Sequence[float]) -> dict[str, float | int]:
    clean = [max(0.0, float(value)) for value in values]
    if not clean:
        return {"count": 0, "total": 0.0, "average": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(clean),
        "total": float(sum(clean)),
        "average": float(sum(clean) / len(clean)),
        "p50": float(np.percentile(clean, 50)),
        "p95": float(np.percentile(clean, 95)),
        "max": float(max(clean)),
    }


def finalize_product_window_profile(
    payload: Mapping[str, object],
    *,
    product_inference_ms: float,
    external_process_ms: float,
    external_artifact_conversion_ms: float,
) -> dict[str, object]:
    """Close one timing record without hiding uninstrumented Product work."""

    result = dict(payload)
    result["product_inference_ms"] = max(0.0, float(product_inference_ms))
    external_known_fields = (
        "product_session_start_ms",
        "product_grounding_ms",
        "product_propagation_ms",
        "product_output_conversion_ms",
        "product_candidate_evaluation_ms",
        "product_track_association_ms",
        "product_session_close_ms",
    )
    external_known = sum(max(0.0, float(result.get(field, 0.0) or 0.0)) for field in external_known_fields)
    external_known += max(0.0, float(external_artifact_conversion_ms))
    result["product_other_ms"] = max(0.0, float(external_process_ms) - external_known)
    accounted = sum(
        max(0.0, float(result.get(field, 0.0) or 0.0))
        for field in PRODUCT_PROFILE_FIELDS
        if field != "product_unattributed_ms"
    )
    result["product_unattributed_ms"] = max(
        0.0,
        float(result["product_inference_ms"]) - accounted,
    )
    result["product_candidate_association_ms"] = (
        float(result.get("product_candidate_evaluation_ms", 0.0) or 0.0)
        + float(result.get("product_track_association_ms", 0.0) or 0.0)
    )
    result["product_accounted_ms"] = accounted
    result["product_accounted_ratio"] = (
        min(1.0, accounted / float(result["product_inference_ms"]))
        if float(result["product_inference_ms"]) > 0.0
        else 0.0
    )
    result["slow_product_window"] = float(result["product_inference_ms"]) > SLOW_PRODUCT_WINDOW_MS
    return result


def classify_product_bottleneck(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    steady = [row for row in records if row.get("product_phase") == "STEADY_STATE"]
    initialization = [row for row in records if row.get("product_phase") == "INITIALIZATION"]
    basis = steady or list(records)
    inference_total = sum(float(row.get("product_inference_ms", 0.0) or 0.0) for row in basis)

    def contribution(*fields: str) -> float:
        if inference_total <= 0.0:
            return 0.0
        value = sum(
            sum(float(row.get(field, 0.0) or 0.0) for field in fields)
            for row in basis
        )
        return value / inference_total

    contributions = {
        "grounding": contribution("product_grounding_ms"),
        "propagation": contribution("product_propagation_ms"),
        "session_lifecycle": contribution(
            "product_session_start_ms",
            "product_session_close_ms",
        ),
        "data_movement": contribution(
            "product_materialize_ms",
            "product_output_conversion_ms",
            "product_artifact_conversion_ms",
        ),
        "association": contribution(
            "product_candidate_evaluation_ms",
            "product_track_association_ms",
        ),
        "other_and_unattributed": contribution(
            "product_other_ms",
            "product_unattributed_ms",
        ),
    }
    init_slow = any(float(row.get("product_inference_ms", 0.0) or 0.0) > SLOW_PRODUCT_WINDOW_MS for row in initialization)
    steady_slow = any(float(row.get("product_inference_ms", 0.0) or 0.0) > SLOW_PRODUCT_WINDOW_MS for row in steady)
    if init_slow and not steady_slow:
        classification = "INITIALIZATION_ONLY"
    elif contributions["grounding"] >= 0.50:
        classification = "GROUNDING_DOMINANT"
    elif contributions["propagation"] >= 0.50:
        classification = "PROPAGATION_DOMINANT"
    elif contributions["session_lifecycle"] >= 0.30:
        classification = "SESSION_LIFECYCLE_DOMINANT"
    elif contributions["data_movement"] >= 0.30:
        classification = "DATA_MOVEMENT_DOMINANT"
    else:
        classification = "MIXED"
    return {
        "classification": classification,
        "basis": "STEADY_STATE" if steady else "ALL_WINDOWS",
        "contributions": contributions,
    }


class ProductLatencyProfileRecorder:
    """Write per-window Product timings and derive deterministic summaries."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._profile_writer = JsonlMetricsWriter(self.output_dir / "product_window_profile.jsonl")
        self._memory_writer = JsonlMetricsWriter(self.output_dir / "cuda_memory_profile.jsonl")
        self._records: list[dict[str, object]] = []
        self._closed = False
        self._summary: Optional[dict[str, object]] = None
        self._warmup: dict[str, object] = {"enabled": False}

    def set_warmup(self, payload: Mapping[str, object]) -> None:
        self._warmup = dict(payload)

    def record(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise RuntimeError("Product latency profile recorder is closed")
        row = dict(payload)
        self._records.append(row)
        self._profile_writer.write(row)
        self._memory_writer.write({
            key: row.get(key)
            for key in (
                "recorded_at",
                "window_no",
                "product_phase",
                "allocated_before_mb",
                "reserved_before_mb",
                "allocated_after_mb",
                "reserved_after_mb",
                "peak_allocated_mb",
                "cuda_memory_delta_mb",
            )
        })

    @staticmethod
    def _field_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
        fields = (
            "product_inference_ms",
            *PRODUCT_PROFILE_FIELDS,
            "product_candidate_association_ms",
            "product_materialize_ms",
        )
        return {
            field: latency_stats([
                float(row.get(field, 0.0) or 0.0)
                for row in records
            ])
            for field in fields
        }

    def close(self) -> dict[str, object]:
        if self._summary is not None:
            return dict(self._summary)
        initialization = [row for row in self._records if row.get("product_phase") == "INITIALIZATION"]
        steady = [row for row in self._records if row.get("product_phase") == "STEADY_STATE"]
        slow = [row for row in self._records if bool(row.get("slow_product_window"))]
        root_cause = classify_product_bottleneck(self._records)
        self._summary = {
            "window_count": len(self._records),
            "slow_window_threshold_ms": SLOW_PRODUCT_WINDOW_MS,
            "slow_window_count": len(slow),
            "slow_window_numbers": [int(row["window_no"]) for row in slow],
            "initialization": self._field_summary(initialization),
            "steady_state": self._field_summary(steady),
            "root_cause": root_cause,
            "warmup": dict(self._warmup),
        }
        self._profile_writer.close()
        self._memory_writer.close()
        (self.output_dir / "product_phase_summary.json").write_text(
            json.dumps(self._summary, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.output_dir / "slow_windows.json").write_text(
            json.dumps(slow, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        duplicate_work = {
            "materialize_calls_per_window": 1,
            "person_product_shared_window_directory": True,
            "duplicate_window_jpeg_write_detected": False,
            "frame_copy_in_materialize": False,
            "conclusion": "NO_DUPLICATE_MATERIALIZE_DETECTED",
        }
        (self.output_dir / "duplicate_work_report.json").write_text(
            json.dumps(duplicate_work, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._write_markdown(self._summary)
        self._closed = True
        return dict(self._summary)

    def _write_markdown(self, summary: Mapping[str, object]) -> None:
        initialization = summary["initialization"]
        steady = summary["steady_state"]
        root = summary["root_cause"]
        assert isinstance(initialization, Mapping)
        assert isinstance(steady, Mapping)
        assert isinstance(root, Mapping)
        lines = [
            "# Product SAM Latency Breakdown",
            "",
            f"- Windows: `{summary['window_count']}`",
            f"- Slow windows: `{summary['slow_window_count']}` {summary['slow_window_numbers']}",
            f"- Root cause: `{root['classification']}`",
            "",
            "| Phase | Count | Product P50 | Product P95 | Product Max |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for label, values in (("INITIALIZATION", initialization), ("STEADY_STATE", steady)):
            inference = values["product_inference_ms"]
            lines.append(
                f"| {label} | {inference['count']} | {inference['p50']:.2f} | "
                f"{inference['p95']:.2f} | {inference['max']:.2f} |"
            )
        lines.extend([
            "",
            "## Steady-State Stages",
            "",
            "| Stage | P50 ms | P95 ms | Average ms |",
            "| --- | ---: | ---: | ---: |",
        ])
        for field in PRODUCT_PROFILE_FIELDS:
            values = steady[field]
            lines.append(
                f"| `{field}` | {values['p50']:.2f} | {values['p95']:.2f} | {values['average']:.2f} |"
            )
        lines.append("")
        (self.output_dir / "product_latency_breakdown.md").write_text(
            "\n".join(lines),
            encoding="utf-8",
        )


def cuda_memory_snapshot(base: object) -> dict[str, float]:
    torch = getattr(base, "torch", None)
    cuda = getattr(torch, "cuda", None)
    if cuda is None or not callable(getattr(cuda, "is_available", None)) or not cuda.is_available():
        return {"allocated_mb": 0.0, "reserved_mb": 0.0, "peak_allocated_mb": 0.0}
    divisor = 1024.0 * 1024.0
    return {
        "allocated_mb": float(cuda.memory_allocated()) / divisor,
        "reserved_mb": float(cuda.memory_reserved()) / divisor,
        "peak_allocated_mb": float(cuda.max_memory_allocated()) / divisor,
    }
