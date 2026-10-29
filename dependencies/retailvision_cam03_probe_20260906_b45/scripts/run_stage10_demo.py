"""Run the Stage 10 RTSP wiring with externally supplied model adapters."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.person_product_association import PersonProductAssociationConfig
from agent.product_interaction import ProductInteractionConfig
from agent.sam31_behavior_adapter import Sam31BehaviorAdapter
from agent.stage10_demo import (
    AsyncRtspWindowSource,
    ConsoleEventSink,
    EventStream,
    FatalStage10Error,
    JsonlEventSink,
    RtspFrameReader,
    ProductWindowPreflightResult,
    Stage10PerformanceRecorder,
    Stage10DemoRuntime,
    TransportPreflightResult,
    capture_product_preflight_window,
    render_product_preflight_surface_overlay,
    run_transport_preflight,
    validate_product_coordinate_space,
    run_rtsp_demo,
    run_windowed_rtsp_demo,
)
from agent.sam31_realtime_providers import Sam31RealtimeProviderBundle
from agent.sam31_product_tracking import ProductDisplaySurface
from agent.stage10_runtime_readiness import Stage1022AcceptanceRecorder


class _EmptyProductSource:
    def process_frame(self, _context):
        return ()


def main() -> int:
    args = parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    _configure_logging(args.artifacts_dir / "runtime_log.txt")
    stage8 = json.loads(args.stage8_config.read_text(encoding="utf-8-sig"))
    stage9 = json.loads(args.stage9_config.read_text(encoding="utf-8-sig"))
    frame_width, frame_height = (int(value) for value in stage8["frame_size"])
    person_adapter = Sam31BehaviorAdapter(
        store_id=str(stage8["store_id"]),
        camera_id=str(stage8["camera_id"]),
        runtime_instance_id=args.runtime_instance_id,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    association_config = PersonProductAssociationConfig(
        frame_width=frame_width,
        frame_height=frame_height,
        **{
            key: stage8[key]
            for key in (
                "expanded_bbox_horizontal_ratio",
                "expanded_bbox_vertical_ratio",
                "max_distance_ratio",
                "max_bbox_gap_ratio",
                "minimum_candidate_score",
                "ambiguity_margin",
                "association_enter_stable_frames",
                "association_exit_stable_frames",
                "association_grace_seconds",
                "association_switch_margin",
                "max_sync_delta_seconds",
                "unknown_role_penalty",
            )
        },
    )
    interaction_config = ProductInteractionConfig(
        frame_width=frame_width,
        frame_height=frame_height,
        **{
            key: stage9[key]
            for key in (
                "sample_fps",
                "motion_window_frames",
                "motion_enter_displacement_pixels",
                "motion_exit_displacement_pixels",
                "motion_enter_stable_frames",
                "motion_exit_stable_frames",
                "pickup_confirm_frames",
                "putdown_confirm_frames",
                "stable_baseline_min_frames",
                "holding_actor_grace_seconds",
                "minimum_holding_seconds",
                "surface_evidence_enabled",
                "pickup_surface_drop_pixels_equivalent",
                "held_recovery_putdown_surface_overlap_min",
                "held_recovery_putdown_confirm_frames",
                "recovery_motion_guard_frames",
                "minimum_pickup_confidence",
                "minimum_putdown_confidence",
            )
        },
    )
    if args.sam31_bundle:
        person_detector = lambda _context: ()
        product_source = _EmptyProductSource()
    else:
        if not args.person_provider or not args.product_provider:
            raise ValueError(
                "synchronous mode requires --person-provider and --product-provider"
            )
        person_detector = _load_factory(args.person_provider)
        product_source = _load_factory(args.product_provider)
    event_path = args.event_jsonl
    if args.sam31_bundle and event_path is None:
        event_path = args.artifacts_dir / "event_stream.jsonl"
    rtsp_source = _rtsp_source(args)
    reader = _make_reader(
        rtsp_source,
        camera_id=str(stage8["camera_id"]),
        sample_fps=float(stage9["sample_fps"]),
        frame_width=frame_width,
        frame_height=frame_height,
        warmup_seconds=1.0 if args.sam31_bundle else 0.0,
    )
    if args.sam31_bundle:
        preflight_root = args.artifacts_dir / "stage10_2_product_preflight"
        if args.reuse_product_preflight is not None:
            transport, window = _reuse_product_preflight(
                source_root=args.reuse_product_preflight,
                output_root=preflight_root,
            )
        else:
            transport, window = _run_product_preflight(
                rtsp_source=rtsp_source,
                camera_id=str(stage8["camera_id"]),
                sample_fps=float(stage9["sample_fps"]),
                frame_width=frame_width,
                frame_height=frame_height,
                output_root=preflight_root,
                frame_count=args.transport_preflight_frames,
            )
        if transport.result != "PASS" or window.frame_count != 12:
            _write_product_init_report(
                root=preflight_root,
                transport=transport,
                window=window,
                summary=None,
            )
            reader.close()
            payload = {
                "rtsp_source": "REDACTED",
                "transport_preflight": transport.to_dict(),
                "product_window_preflight": window.to_dict(),
                "product_init_status": "PRECONDITION_FAIL",
                "runtime_errors": 1,
            }
            print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
            return 1
        reader.close()
        reader = _make_reader(
            rtsp_source,
            camera_id=str(stage8["camera_id"]),
            sample_fps=float(stage9["sample_fps"]),
            frame_width=frame_width,
            frame_height=frame_height,
            warmup_seconds=1.0,
        )
    sinks = [ConsoleEventSink()]
    event_sink = JsonlEventSink(event_path) if event_path else None
    if event_sink is not None:
        sinks.append(event_sink)
    runtime = Stage10DemoRuntime(
        camera_id=str(stage8["camera_id"]),
        person_adapter=person_adapter,
        person_detector=person_detector,
        product_source=product_source,
        association_config=association_config,
        interaction_config=interaction_config,
        event_stream=EventStream(sinks),
    )
    overlay_path = args.overlay
    if args.sam31_bundle and overlay_path is None:
        overlay_path = args.artifacts_dir / "overlay.mp4"
    bundle = None
    performance_recorder = (
        Stage10PerformanceRecorder(args.artifacts_dir / "stage10_2_performance")
        if args.sam31_bundle
        else None
    )
    acceptance_recorder = (
        Stage1022AcceptanceRecorder(
            args.artifacts_dir,
            minimum_person_present_ready_windows=args.minimum_person_present_ready_windows,
        )
        if args.sam31_bundle and args.person_present_acceptance
        else None
    )
    try:
        if args.sam31_bundle:
            assignments = _candidate_assignments(args)
            bundle = Sam31RealtimeProviderBundle(
                runtime_instance_id=args.runtime_instance_id,
                output_dir=args.artifacts_dir,
                sam_runtime_root=args.sam31_runtime_root,
                candidate_assignments=assignments,
                selected_product_keys=args.selected_product_key,
                product_init_max_windows=args.product_init_max_windows,
                sam_warmup_probe=args.sam_warmup_probe,
                gpu_timing_probe=args.gpu_timing_probe,
                dual_predictor_vram_probe=args.dual_predictor_vram_probe,
                ready_after_completed_windows=args.ready_after_completed_windows,
            )
            source = AsyncRtspWindowSource(
                reader,
                max_queue_size=args.window_queue_size,
            )
            summary = run_windowed_rtsp_demo(
                source=source,
                runtime=runtime,
                provider_bundle=bundle,
                overlay_path=overlay_path,
                keyframes_dir=args.artifacts_dir / "keyframes",
                frame_result_path=args.artifacts_dir / "frame_results.jsonl",
                max_windows=args.max_windows,
                max_frames=args.max_frames,
                max_duration_seconds=args.duration_seconds,
                window_timeout_seconds=args.window_timeout_seconds,
                performance_recorder=performance_recorder,
                acceptance_recorder=acceptance_recorder,
            )
        else:
            summary = run_rtsp_demo(
                reader=reader,
                runtime=runtime,
                overlay_path=overlay_path,
                max_frames=args.max_frames,
            )
    except FatalStage10Error as error:
        logging.getLogger(__name__).exception("Stage 10 fatal startup or provider error")
        payload = _fatal_summary(error)
        if bundle is not None:
            try:
                payload["provider_metrics"] = dict(bundle.gpu_metrics())
            except Exception:
                pass
        if args.sam31_bundle:
            _write_product_init_report(
                root=args.artifacts_dir / "stage10_2_product_preflight",
                transport=None,
                window=None,
                summary=payload,
            )
        _write_artifacts(
            args=args,
            summary=payload,
            camera_id=str(stage8["camera_id"]),
            frame_width=frame_width,
            frame_height=frame_height,
            event_path=event_path,
            overlay_path=overlay_path,
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
        return 1
    finally:
        if event_sink is not None:
            event_sink.close()
        if performance_recorder is not None:
            performance_recorder.close()
    if args.sam31_bundle:
        _write_product_init_report(
            root=args.artifacts_dir / "stage10_2_product_preflight",
            transport=None,
            window=None,
            summary=summary.to_dict(),
        )
    payload = summary.to_dict()
    _write_artifacts(
        args=args,
        summary=payload,
        camera_id=str(stage8["camera_id"]),
        frame_width=frame_width,
        frame_height=frame_height,
        event_path=event_path,
        overlay_path=overlay_path,
    )
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if summary.runtime_errors == 0 else 1


def _load_factory(spec: str) -> Any:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("provider must use module:attribute syntax")
    value = getattr(importlib.import_module(module_name), attribute)
    return value() if isinstance(value, type) else value


def _rtsp_source(args: argparse.Namespace) -> str:
    if args.rtsp:
        return str(args.rtsp)
    value = os.environ.get(str(args.rtsp_env), "")
    if not value:
        raise ValueError(f"RTSP environment variable is not set: {args.rtsp_env}")
    return value


def _make_reader(
    source: str,
    *,
    camera_id: str,
    sample_fps: float,
    frame_width: int,
    frame_height: int,
    warmup_seconds: float,
) -> RtspFrameReader:
    return RtspFrameReader(
        source=source,
        camera_id=camera_id,
        sample_fps=sample_fps,
        frame_width=frame_width,
        frame_height=frame_height,
        warmup_seconds=warmup_seconds,
    )


def _load_product_surface() -> ProductDisplaySurface:
    path = PROJECT_ROOT / "product_cameras.cam02.stage7c.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("frame_size") != [1280, 720]:
        raise ValueError("frozen Product surface config is not 1280x720")
    area = payload["product_display_surface"]
    if area.get("calibration_image_size") != [1280, 720]:
        raise ValueError("frozen Product surface calibration is not 1280x720")
    return ProductDisplaySurface(
        tuple((float(x), float(y)) for x, y in area["points"])
    )


def _run_product_preflight(
    *,
    rtsp_source: str,
    camera_id: str,
    sample_fps: float,
    frame_width: int,
    frame_height: int,
    output_root: Path,
    frame_count: int,
) -> tuple[TransportPreflightResult, ProductWindowPreflightResult]:
    output_root.mkdir(parents=True, exist_ok=True)
    transport_reader = _make_reader(
        rtsp_source,
        camera_id=camera_id,
        sample_fps=sample_fps,
        frame_width=frame_width,
        frame_height=frame_height,
        warmup_seconds=0.0,
    )
    transport = run_transport_preflight(
        transport_reader,
        output_root / "transport",
        frame_count=frame_count,
        sample_indices=(0, 10, 20),
    )
    _write_json(output_root / "transport_preflight.json", transport.to_dict())
    if transport.result != "PASS":
        empty_window = ProductWindowPreflightResult(
            window_no=0,
            frame_count=0,
            source_seq_start=None,
            source_seq_end=None,
            ai_frame_shape=(),
            contact_sheet_path=str(output_root / "window_000_contact_sheet.jpg"),
        )
        (output_root / "windows").mkdir(parents=True, exist_ok=True)
        _write_json(output_root / "windows" / "window_000.json", empty_window.to_dict())
        return transport, empty_window
    window_reader = _make_reader(
        rtsp_source,
        camera_id=camera_id,
        sample_fps=sample_fps,
        frame_width=frame_width,
        frame_height=frame_height,
        warmup_seconds=0.0,
    )
    window = capture_product_preflight_window(
        window_reader,
        output_root,
        frame_count=12,
    )
    _write_json(output_root / "windows" / "window_000.json", window.to_dict())
    surface = _load_product_surface()
    validate_product_coordinate_space(surface, frame_size=(frame_width, frame_height))
    first_frame = cv2.imread(
        str(output_root / "windows" / "window_000" / "frame_00.jpg")
    )
    if first_frame is not None:
        render_product_preflight_surface_overlay(
            first_frame,
            surface,
            output_root / "overlays" / "product_preflight_surface_overlay.jpg",
        )
    return transport, window


def _reuse_product_preflight(
    *,
    source_root: Path,
    output_root: Path,
) -> tuple[TransportPreflightResult, ProductWindowPreflightResult]:
    transport_payload = json.loads(
        (source_root / "transport_preflight.json").read_text(encoding="utf-8")
    )
    window_payload = json.loads(
        (source_root / "windows" / "window_000.json").read_text(encoding="utf-8")
    )
    if transport_payload.get("result") != "PASS":
        raise ValueError("reused Product preflight is not PASS")
    if int(window_payload.get("frame_count", 0)) != 12:
        raise ValueError("reused Product preflight does not contain a complete 12-frame window")
    output_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, output_root, dirs_exist_ok=True)
    transport = TransportPreflightResult(
        opened=bool(transport_payload["opened"]),
        decoded_frames=int(transport_payload["decoded_frames"]),
        source_resolution=tuple(transport_payload["source_resolution"]),
        ai_resolution=tuple(transport_payload["ai_resolution"]),
        frame_shape=tuple(transport_payload["frame_shape"]),
        black_frames=int(transport_payload["black_frames"]),
        duplicate_frames=int(transport_payload["duplicate_frames"]),
        average_pixel_mean=float(transport_payload["average_pixel_mean"]),
        average_pixel_std=float(transport_payload["average_pixel_std"]),
        first_frame_timestamp=transport_payload.get("first_frame_timestamp"),
        last_frame_timestamp=transport_payload.get("last_frame_timestamp"),
        result=str(transport_payload["result"]),
    )
    window = ProductWindowPreflightResult(
        window_no=int(window_payload["window_no"]),
        frame_count=int(window_payload["frame_count"]),
        source_seq_start=window_payload.get("source_seq_start"),
        source_seq_end=window_payload.get("source_seq_end"),
        ai_frame_shape=tuple(window_payload["ai_frame_shape"]),
        contact_sheet_path=str(window_payload["contact_sheet_path"]),
    )
    return transport, window


def _first_jsonl(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return json.loads(line)
    return None


def _write_product_init_report(
    *,
    root: Path,
    transport: TransportPreflightResult | None,
    window: ProductWindowPreflightResult | None,
    summary: dict[str, object] | None,
) -> None:
    transport_payload = None if transport is None else transport.to_dict()
    window_payload = None if window is None else window.to_dict()
    if transport_payload is None:
        transport_path = root / "transport_preflight.json"
        if transport_path.exists():
            transport_payload = json.loads(transport_path.read_text(encoding="utf-8"))
    if window_payload is None:
        window_path = root / "windows" / "window_000.json"
        if window_path.exists():
            window_payload = json.loads(window_path.read_text(encoding="utf-8"))
    diagnostic = _first_jsonl(root / "diagnostics" / "product_init_windows.jsonl")
    lines = [
        "# Stage 10.2 Product Initialization Diagnostic Report",
        "",
        "## Result",
        "",
        "RTSP_SOURCE=REDACTED",
        "",
        f"Transport preflight: {None if transport_payload is None else transport_payload.get('result')}",
        f"Decoded frames: {None if transport_payload is None else transport_payload.get('decoded_frames')}",
        f"Source resolution: {None if transport_payload is None else transport_payload.get('source_resolution')}",
        f"AI resolution: {None if transport_payload is None else transport_payload.get('ai_resolution')}",
        f"First 12-frame Window: {None if window_payload is None else window_payload.get('frame_count')} frames",
        "",
        "## Product Candidate Pipeline",
        "",
    ]
    if diagnostic is None:
        lines.extend([
            "Grounding call count: NOT RUN",
            "Raw grounding object count: NOT RUN",
            "Raw ProductCandidate count: NOT RUN",
            "Credible candidate count: NOT RUN",
            "Product Init: NOT RUN",
        ])
    else:
        lines.extend([
            f"Grounding call count: {diagnostic.get('grounding_call_count', 0)}",
            f"Grounding called: {diagnostic.get('grounding_called', False)}",
            f"Raw grounding object count: {diagnostic.get('raw_grounding_object_count', 0)}",
            f"Raw ProductCandidate count: {diagnostic.get('raw_candidate_count', 0)}",
            f"Credible candidate count: {diagnostic.get('credible_candidate_count', 0)}",
            f"Zero-candidate classification: {diagnostic.get('zero_candidate_case')}",
            f"Product Init candidate count: {diagnostic.get('final_initialization_candidate_count', 0)}",
        ])
        for candidate in diagnostic.get("candidates", ()):
            lines.append(
                f"Candidate {candidate.get('local_obj_id')}: "
                f"eligible={candidate.get('eligible')} "
                f"reject_reason={candidate.get('reject_reason')} "
                f"surface_overlap={candidate.get('surface_overlap_ratio')}"
            )
    if summary is not None:
        lines.extend([
            "",
            "## Runtime Status",
            "",
            f"Product init ready: {summary.get('product_init_ready', False)}",
            f"Pending windows: {summary.get('product_init_pending_windows', 0)}",
            f"Completed provider windows: {summary.get('window_count', 0)}",
            f"20-window baseline: {'PASS' if summary.get('window_count', 0) >= 20 and summary.get('product_init_ready') else 'NOT COMPLETE'}",
        ])
    lines.extend([
        "",
        "Coordinate space: surface=AI_FRAME, candidate=AI_FRAME, frozen AI=1280x720.",
        "Scene precondition (Toy A/Toy B visible in the saved Window): HUMAN REVIEW REQUIRED.",
        "",
        "## Artifacts",
        "",
        "- transport/",
        "- windows/window_000/",
        "- diagnostics/product_init_windows.jsonl",
        "- diagnostics/grounding_diagnostics.jsonl",
        "- diagnostics/candidate_diagnostics.jsonl",
        "- overlays/product_preflight_surface_overlay.jpg",
        "- product_init_success.json (only when initialization succeeds)",
    ])
    root.mkdir(parents=True, exist_ok=True)
    (root / "STAGE10_2_PRODUCT_INIT_DIAGNOSTIC_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _candidate_assignments(args: argparse.Namespace) -> dict[str, int] | None:
    configured = tuple(args.candidate_assignment or ())
    if configured:
        if args.product_a_candidate_id is not None or args.product_b_candidate_id is not None:
            raise ValueError(
                "--candidate-assignment cannot be combined with legacy A/B candidate options"
            )
        result: dict[str, int] = {}
        for value in configured:
            product_key, separator, candidate_id = str(value).partition("=")
            if not separator or not product_key or product_key in result:
                raise ValueError("--candidate-assignment must use unique product_key=candidate_id values")
            result[product_key] = int(candidate_id)
        return result
    values = (args.product_a_candidate_id, args.product_b_candidate_id)
    if values == (None, None):
        return None
    if any(value is None for value in values):
        raise ValueError(
            "--product-a-candidate-id and --product-b-candidate-id must be supplied together"
        )
    return {
        "smart_toy_a": int(args.product_a_candidate_id),
        "smart_toy_b": int(args.product_b_candidate_id),
    }


def _configure_logging(path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=(logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()),
        force=True,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_artifacts(
    *,
    args: argparse.Namespace,
    summary: dict[str, object],
    camera_id: str,
    frame_width: int,
    frame_height: int,
    event_path: Path | None,
    overlay_path: Path | None,
) -> None:
    root = args.artifacts_dir
    _write_json(root / "runtime_summary.json", summary)
    _write_json(root / "latency_metrics.json", summary.get("latency_ms", {}))
    _write_json(root / "frame_stats.json", summary.get("counters", {}))
    _write_json(root / "gpu_metrics.json", summary.get("provider_metrics", {}))
    _write_json(root / "configuration_snapshot.json", {
        "camera_id": camera_id,
        "frame_size": [frame_width, frame_height],
        "runtime_instance_id": args.runtime_instance_id,
        "provider_mode": "sam31_windowed_bundle" if args.sam31_bundle else "synchronous_factories",
        "sample_fps": 5.0,
        "window_frames": 12 if args.sam31_bundle else None,
        "overlap_frames": 4 if args.sam31_bundle else None,
        "sam_warmup_probe": bool(args.sam_warmup_probe),
        "gpu_timing_probe": bool(args.gpu_timing_probe),
        "dual_predictor_vram_probe": bool(args.dual_predictor_vram_probe),
        "person_present_acceptance": bool(args.person_present_acceptance),
        "ready_after_completed_windows": int(args.ready_after_completed_windows),
        "minimum_person_present_ready_windows": int(args.minimum_person_present_ready_windows),
        "event_stream": None if event_path is None else str(event_path),
        "overlay": None if overlay_path is None else str(overlay_path),
        "rtsp_source": "REDACTED",
    })
    if args.sam31_bundle:
        _write_json(root / "realtime_acceptance_result.json", {
            "stage10_1_result": "FAIL",
            "stage10_overall_acceptance": "FAIL",
            "reason": (
                "A bounded provider smoke does not satisfy the required "
                "live A-F behavior scenarios or sustained acceptance run."
            ),
            "frames_processed": summary.get("frames_processed", 0),
            "runtime_errors": summary.get("runtime_errors", 0),
            "product_init_ready": summary.get("product_init_ready", False),
            "product_init_pending_windows": summary.get("product_init_pending_windows", 0),
        })


def _fatal_summary(error: FatalStage10Error) -> dict[str, object]:
    return {
        "frames_processed": 0,
        "runtime_errors": 1,
        "dropped_frames": 0,
        "latency_ms": {},
        "counters": {
            "frames_received": 0,
            "frames_sampled": 0,
            "frames_processed": 0,
            "frames_dropped": 0,
            "read_failures": 0,
            "decode_failures": 0,
            "reconnects": 0,
        },
        "overlay_path": None,
        "runtime_diagnostics": [{
            "source_seq": None,
            "timestamp": None,
            "exception_type": type(error).__name__,
            "exception_message": str(error),
            "stage": "provider_initialization",
            "provider": "Sam31RealtimeProviderBundle",
            "traceback": "See runtime_log.txt",
        }],
        "window_count": 0,
        "provider_metrics": {},
        "run_duration_seconds": 0.0,
        "effective_processed_fps": 0.0,
        "frame_drop_rate": 0.0,
        "performance_summary": {},
        "product_init_pending_windows": 0,
        "product_init_ready": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rtsp")
    source.add_argument(
        "--rtsp-env",
        metavar="NAME",
        help="Read the RTSP endpoint from an environment variable",
    )
    parser.add_argument("--stage8-config", type=Path, default=Path("person_product_association.cam02.stage8.json"))
    parser.add_argument("--stage9-config", type=Path, default=Path("product_interaction.cam02.stage9.json"))
    parser.add_argument("--runtime-instance-id", default="stage10-live")
    parser.add_argument("--person-provider", help="module:callable returning Person detector")
    parser.add_argument("--product-provider", help="module:callable returning ProductFrameSource")
    parser.add_argument(
        "--sam31-bundle",
        action="store_true",
        help="Use the coordinated frozen SAM3.1 rolling-window providers",
    )
    parser.add_argument(
        "--sam31-runtime-root",
        type=Path,
        default=Path(r"D:\GJJ\work\sam3"),
    )
    parser.add_argument("--product-a-candidate-id", type=int)
    parser.add_argument("--product-b-candidate-id", type=int)
    parser.add_argument(
        "--selected-product-key",
        action="append",
        help="Approved Product key; repeat for a configuration-driven dense adapter",
    )
    parser.add_argument(
        "--candidate-assignment",
        action="append",
        metavar="PRODUCT_KEY=CANDIDATE_ID",
        help="Configuration-driven candidate assignment; repeat once per selected key",
    )
    parser.add_argument("--event-jsonl", type=Path)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("stage10_demo_integration"),
    )
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--transport-preflight-frames", type=int, default=30)
    parser.add_argument(
        "--reuse-product-preflight",
        type=Path,
        help="Reuse an existing PASS Product preflight artifact after an explicit review",
    )
    parser.add_argument("--product-init-max-windows", type=int, default=10)
    parser.add_argument("--window-queue-size", type=int, default=64)
    parser.add_argument("--window-timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--sam-warmup-probe",
        action="store_true",
        help="Run one state-isolated SAM warmup session before Product initialization",
    )
    parser.add_argument(
        "--gpu-timing-probe",
        action="store_true",
        help="Synchronize CUDA around Product substages for a dedicated timing probe",
    )
    parser.add_argument(
        "--dual-predictor-vram-probe",
        action="store_true",
        help="Measure sequential dual-predictor VRAM feasibility without concurrent inference",
    )
    parser.add_argument(
        "--person-present-acceptance",
        action="store_true",
        help="Write Stage 10.2.2 READY, identity, critical-path, and false-event gates",
    )
    parser.add_argument("--ready-after-completed-windows", type=int, default=2)
    parser.add_argument("--minimum-person-present-ready-windows", type=int, default=20)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
