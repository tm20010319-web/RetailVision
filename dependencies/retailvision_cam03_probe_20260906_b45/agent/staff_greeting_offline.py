"""Offline Staff Greeting replay, artifacts, and presentation-only overlay."""

from __future__ import annotations

import json
import math
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Protocol

import cv2

from agent.behavior_monitor import BehaviorMonitor
from agent.behavior_vision import (
    BehaviorCameraConfig,
    BehaviorVisionAdapter,
    load_behavior_camera_configs,
    localize_staff_greeting_overlay_line,
)
from agent.behavior_events import BehaviorEventInput, EntryEventInput
from agent.overlay_text import OverlayText, draw_overlay_texts_bgr
from agent.staff_greeting_evaluation import OfficialGreetingCore


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish one JSON file through a flushed, durable replacement."""
    temporary = Path(f"{path}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class MediaReader(Protocol):
    def validate(self) -> dict[str, Any]: ...
    def frames(self) -> Iterable[tuple[Any, float, int]]: ...


class VisionAdapter(Protocol):
    def process_frame(self, frame: Any) -> list[Any]: ...


class VideoWriter(Protocol):
    def write(self, frame: Any) -> None: ...
    def release(self) -> None: ...


class OfflineEligibilityError(ValueError):
    """Stable media/config gate failure; no formal analysis artifacts are eligible."""


@dataclass(frozen=True)
class OfflineRunResult:
    analysis_run_id: str
    status: str
    output_dir: Path
    manifest_path: Path


class OpenCVPTSReader:
    """Two-pass MP4 reader using OpenCV's FFmpeg PTS property."""

    def __init__(self, source: Path, expected_size: tuple[int, int]) -> None:
        self.source = Path(source)
        self.expected_size = expected_size
        self._metadata: Optional[dict[str, Any]] = None

    def validate(self) -> dict[str, Any]:
        if not self.source.is_file():
            raise ValueError("video_source_unavailable")
        capture = cv2.VideoCapture(str(self.source), cv2.CAP_FFMPEG)
        if not capture.isOpened():
            raise ValueError("video_source_unavailable")
        try:
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if (width, height) != self.expected_size:
                raise ValueError("calibration_image_size_mismatch")
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            if not math.isfinite(fps) or fps <= 0:
                raise ValueError("presentation_timeline_invalid")
            pts: list[int] = []
            while True:
                ok, _ = capture.read()
                if not ok:
                    break
                raw = float(capture.get(cv2.CAP_PROP_PTS))
                rounded = int(round(raw)) if math.isfinite(raw) else -1
                if not math.isfinite(raw) or abs(raw - rounded) > 1e-6:
                    raise ValueError("video_pts_unavailable")
                if pts and rounded < pts[-1]:
                    raise ValueError("video_pts_non_monotonic")
                pts.append(rounded)
            if not pts:
                raise ValueError("video_pts_unavailable")
            fps_fraction = Fraction(str(fps)).limit_denominator(1_000_000)
            time_base = Fraction(1, 1) / fps_fraction
            self._metadata = {
                "width": width,
                "height": height,
                "fps": fps,
                "frame_count": len(pts),
                "first_raw_pts": pts[0],
                "last_raw_pts": pts[-1],
                "last_pts_seconds": (pts[-1] - pts[0]) * float(time_base),
                "time_base_num": time_base.numerator,
                "time_base_den": time_base.denominator,
                "presentation_timeline_status": "verified",
            }
            return dict(self._metadata)
        finally:
            capture.release()

    def frames(self) -> Iterable[tuple[Any, float, int]]:
        metadata = self._metadata or self.validate()
        capture = cv2.VideoCapture(str(self.source), cv2.CAP_FFMPEG)
        if not capture.isOpened():
            raise ValueError("video_source_unavailable")
        try:
            origin = int(metadata["first_raw_pts"])
            time_base = float(metadata["time_base_num"]) / float(metadata["time_base_den"])
            previous: Optional[int] = None
            source_seq = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                raw_value = float(capture.get(cv2.CAP_PROP_PTS))
                raw = int(round(raw_value)) if math.isfinite(raw_value) else -1
                if raw < 0:
                    raise ValueError("video_pts_unavailable")
                if previous is not None and raw < previous:
                    raise ValueError("video_pts_non_monotonic")
                previous = raw
                yield frame, (raw - origin) * time_base, source_seq
                source_seq += 1
        finally:
            capture.release()


class JsonlArtifactCollector:
    """Run-scoped staging writer with envelope and summary validation."""

    schema_version = "staff_greeting_offline_v1"

    def __init__(self, staging_dir: Path, *, analysis_run_id: str, camera: BehaviorCameraConfig) -> None:
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_run_id = analysis_run_id
        self.camera = camera
        self.events_path = self.staging_dir / "greeting_events.jsonl.tmp"
        self.summary_path = self.staging_dir / "greeting_summary.json.tmp"
        self._events: list[dict[str, Any]] = []
        self._cycles: set[str] = set()
        self._event_keys: set[str] = set()
        self._finalized_summary: Optional[dict[str, Any]] = None

    def add(self, event: BehaviorEventInput, *, cycle_id: str, pts_status: str = "verified") -> None:
        if pts_status != "verified":
            raise ValueError("not_eligible_event_forbidden")
        if cycle_id in self._cycles or event.event_key in self._event_keys:
            raise ValueError("duplicate_terminal_event")
        if event.event_type != "staff_greeting":
            raise ValueError("unsupported_event_type")
        if event.assessment not in {"confirmed", "uncertain", "not_observed", "cancelled"}:
            raise ValueError("non_terminal_event")
        payload = {
            "schema_version": self.schema_version,
            "source": "offline_mp4",
            "analysis_run_id": self.analysis_run_id,
            "cycle_id": cycle_id,
            "event_type": "staff_greeting",
            "presentation_timeline_status": "verified",
            "greeting_analysis_status": "eligible",
            "event": {
                "event_key": event.event_key,
                "event_type": event.event_type,
                "assessment": event.assessment,
                "camera_id": event.camera_id,
                "customer_track_id": event.customer_track_id,
                "customer_track_token": event.customer_track_token,
                "staff_track_id": event.staff_track_id,
                "staff_track_token": event.staff_track_token,
                "started_at": event.started_at.isoformat(),
                "ended_at": event.ended_at.isoformat(),
                "confidence": event.confidence,
                "details": dict(event.details or {}),
            },
        }
        self._cycles.add(cycle_id)
        self._event_keys.add(event.event_key)
        self._events.append(payload)

    def add_cancellation(self, cancellation: Any, *, run_epoch: datetime) -> None:
        entry_key = cancellation.greeting_cycle_id.removesuffix(":greeting-cycle")
        event = BehaviorEventInput(
            event_key=f"{entry_key}:greeting",
            event_type="staff_greeting",
            assessment="cancelled",
            camera_id=cancellation.camera_id,
            customer_track_id=0,
            customer_track_token=cancellation.customer_track_token,
            started_at=cancellation.started_at or cancellation.cancelled_at,
            ended_at=cancellation.cancelled_at,
            confidence=None,
            details={"reason": cancellation.cancel_reason},
        )
        self.add(event, cycle_id=cancellation.greeting_cycle_id)

    def finalize(self) -> dict[str, Any]:
        if self._finalized_summary is not None:
            return dict(self._finalized_summary)
        with self.events_path.open("w", encoding="utf-8", newline="\n") as handle:
            for item in self._events:
                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        summary = GreetingSummaryBuilder.build(
            self._events, analysis_run_id=self.analysis_run_id, camera=self.camera
        )
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._finalized_summary = dict(summary)
        return summary

    @property
    def event_count(self) -> int:
        return len(self._events)


class GreetingSummaryBuilder:
    """Pure aggregation of one run's staged greeting event envelopes."""

    @classmethod
    def build(
        cls, events: Iterable[dict[str, Any]], *, analysis_run_id: str,
        camera: BehaviorCameraConfig,
    ) -> dict[str, Any]:
        counts = {name: 0 for name in ("confirmed", "uncertain", "not_observed", "cancelled")}
        materialized = list(events)
        for item in materialized:
            if item.get("schema_version") != JsonlArtifactCollector.schema_version:
                raise ValueError("summary_schema_version_mismatch")
            if item.get("source") != "offline_mp4":
                raise ValueError("summary_source_mismatch")
            if item.get("analysis_run_id") != analysis_run_id:
                raise ValueError("summary_run_mismatch")
            if item.get("presentation_timeline_status") != "verified":
                raise ValueError("summary_timeline_not_verified")
            if item.get("greeting_analysis_status") != "eligible":
                raise ValueError("summary_analysis_not_eligible")
            event = item.get("event") or {}
            assessment = event.get("assessment")
            if assessment not in counts:
                raise ValueError("summary_non_terminal_event")
            counts[assessment] += 1
        total = len(materialized)
        if sum(counts.values()) != total:
            raise ValueError("summary_terminal_count_mismatch")
        return {
            "schema_version": JsonlArtifactCollector.schema_version,
            "source": "offline_mp4",
            "analysis_run_id": analysis_run_id,
            "camera_id": camera.camera_id,
            "camera_role": camera.camera_role.value,
            "calibration_status": camera.calibration_status,
            "presentation_timeline_status": "verified",
            "greeting_analysis_status": "eligible",
            "total_events": total,
            "confirmed_count": counts["confirmed"],
            "uncertain_count": counts["uncertain"],
            "not_observed_count": counts["not_observed"],
            "cancelled_count": counts["cancelled"],
            "successful_greeting_count": counts["confirmed"],
        }


class OfflineOverlayRenderer:
    """Read-only renderer: Core state and terminal events are supplied by caller."""

    def __init__(
        self,
        *,
        run_id: str,
        camera: BehaviorCameraConfig,
        epoch: datetime,
        language: Literal["en", "zh-CN"] = "en",
    ) -> None:
        if language not in {"en", "zh-CN"}:
            raise ValueError("language must be en or zh-CN")
        self.run_id = run_id
        self.camera = camera
        self.epoch = epoch
        self.language = language
        self._banners: list[tuple[float, float, str]] = []

    def register_event(self, event: BehaviorEventInput) -> None:
        start = (event.ended_at - self.epoch).total_seconds()
        if self.language == "zh-CN":
            label = {
                "confirmed": "迎宾已确认",
                "uncertain": "迎宾结果不确定",
                "not_observed": "未观察到迎宾",
                "cancelled": "视频结束 / 分析已取消"
                if (event.details or {}).get("reason") == "video_eof"
                else "迎宾已取消",
            }[event.assessment]
        else:
            label = {
                "confirmed": "GREETING CONFIRMED",
                "uncertain": "GREETING UNCERTAIN",
                "not_observed": "GREETING NOT OBSERVED",
                "cancelled": "VIDEO EOF / ANALYSIS CANCELLED"
                if (event.details or {}).get("reason") == "video_eof"
                else "GREETING CANCELLED",
            }[event.assessment]
        self._banners.append((start, start + 2.0, label))

    def render(self, frame: Any, *, pts_seconds: float, observations: Iterable[Any], state_lines: Iterable[str]) -> Any:
        rendered = frame.copy()
        height, _ = rendered.shape[:2]
        y = 28
        chinese = self.language == "zh-CN"
        unicode_items: list[OverlayText] = []
        if chinese:
            calibration = {
                "provisional": "临时标定",
                "verified": "已验证",
                "unknown": "未知",
            }.get(str(self.camera.calibration_status or "unknown").lower(), "未知")
            unicode_items.extend(
                (
                    OverlayText("来源：离线 MP4", (20, 10), 28, (0, 220, 255), bold=True),
                    OverlayText(f"分析批次：{self.run_id}", (20, 45), 23, (255, 255, 255)),
                    OverlayText(f"标定状态：{calibration}", (20, 75), 23, (0, 215, 255)),
                )
            )
        else:
            cv2.putText(rendered, "SOURCE: OFFLINE_MP4", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2, cv2.LINE_AA)
            cv2.putText(rendered, f"RUN: {self.run_id}", (20, y + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(rendered, f"CALIBRATION: {str(self.camera.calibration_status or 'unknown').upper()}", (20, y + 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 215, 255), 1, cv2.LINE_AA)
        for observation in observations:
            x = int(observation.foot_point[0] * rendered.shape[1])
            yy = int(observation.foot_point[1] * height)
            color = (50, 255, 50) if observation.role == "staff" else (255, 180, 40)
            if chinese:
                role = {
                    "staff": "店员",
                    "customer": "顾客",
                    "unknown": "身份待确认",
                    "uncertain": "身份不确定",
                }.get(observation.role, "人员")
                unicode_items.append(
                    OverlayText(
                        f"{role}轨迹 {observation.track_id}",
                        (max(8, x - 140), max(95, yy - 38)),
                        28,
                        color,
                        bold=True,
                        stroke_width=1,
                    )
                )
            else:
                label = f"{observation.role.upper()} TRACK {observation.track_id}"
                cv2.putText(rendered, label, (max(8, x - 140), max(95, yy - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA)
            cv2.circle(rendered, (x, yy), 5, color, -1)
        for index, line in enumerate(state_lines):
            if chinese:
                unicode_items.append(
                    OverlayText(
                        localize_staff_greeting_overlay_line(line[:100]),
                        (20, height - 220 + index * 31),
                        23,
                        (255, 255, 255),
                    )
                )
            else:
                cv2.putText(rendered, line[:100], (20, height - 90 + index * 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        for start, end, label in self._banners:
            if start <= pts_seconds < end:
                cv2.rectangle(rendered, (20, height // 2 - 38), (min(rendered.shape[1] - 20, 700), height // 2 + 18), (0, 120, 220), -1)
                if chinese:
                    unicode_items.append(
                        OverlayText(label, (35, height // 2 - 33), 38, (255, 255, 255), bold=True)
                    )
                else:
                    cv2.putText(rendered, label, (35, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        return draw_overlay_texts_bgr(rendered, unicode_items)


class OfflineStaffGreetingRunner:
    """Artifact-only offline orchestration around the shared V1 Core."""

    def __init__(self, *, media_reader: MediaReader, vision_adapter: VisionAdapter, greeting_core: OfficialGreetingCore,
                 camera: BehaviorCameraConfig, output_dir: Path, analysis_run_id: Optional[str] = None,
                 artifact_collector_factory: Optional[Callable[[Path, str, BehaviorCameraConfig], JsonlArtifactCollector]] = None,
                 overlay_renderer: Optional[OfflineOverlayRenderer] = None,
                 writer_factory: Optional[Callable[[Path, float, tuple[int, int]], VideoWriter]] = None,
                 overlay_language: Literal["en", "zh-CN"] = "en") -> None:
        if overlay_language not in {"en", "zh-CN"}:
            raise ValueError("overlay_language must be en or zh-CN")
        self.media_reader = media_reader
        self.vision_adapter = vision_adapter
        self.greeting_core = greeting_core
        self.camera = camera
        self.output_dir = Path(output_dir)
        self.analysis_run_id = analysis_run_id or uuid.uuid4().hex
        self.artifact_collector_factory = artifact_collector_factory
        self.overlay_renderer = overlay_renderer
        self.overlay_language = overlay_language
        self.writer_factory = writer_factory or self._opencv_writer

    @staticmethod
    def _opencv_writer(path: Path, fps: float, size: tuple[int, int]) -> VideoWriter:
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        if not writer.isOpened():
            raise ValueError("video_writer_unavailable")
        return writer

    def run(self) -> OfflineRunResult:
        run_dir = self.output_dir / self.analysis_run_id
        staging = run_dir / ".staging"
        run_dir.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "run_manifest.json"
        if manifest_path.is_file():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if existing.get("status") == "FINALIZED":
                    return OfflineRunResult(self.analysis_run_id, "FINALIZED", run_dir, manifest_path)
            except (OSError, ValueError, TypeError):
                pass
        writer: Optional[VideoWriter] = None
        writer_released = False
        eligibility_complete = False
        metadata: dict[str, Any] = {}
        try:
            if self.camera.camera_role.value != "reception_entry" or self.camera.greeting_area is None:
                raise OfflineEligibilityError("camera_config_unavailable")
            if self.camera.ground_projector is None or self.camera.calibration_image_size is None:
                raise OfflineEligibilityError("camera_config_unavailable")
            try:
                metadata = self.media_reader.validate()
            except OfflineEligibilityError:
                raise
            except ValueError as exc:
                raise OfflineEligibilityError(str(exc)) from exc
            if metadata.get("presentation_timeline_status") != "verified":
                raise OfflineEligibilityError("presentation_timeline_invalid")
            eligibility_complete = True
            collector = (
                self.artifact_collector_factory(staging, self.analysis_run_id, self.camera)
                if self.artifact_collector_factory is not None
                else JsonlArtifactCollector(staging, analysis_run_id=self.analysis_run_id, camera=self.camera)
            )
            monitor = BehaviorMonitor(camera_id=self.camera.camera_id, entrance_area=self.camera.entry_trigger,
                                      greeting_area=self.camera.greeting_area, config=self.camera.monitor_config)
            epoch = datetime(2000, 1, 1)
            renderer = self.overlay_renderer or OfflineOverlayRenderer(
                run_id=self.analysis_run_id,
                camera=self.camera,
                epoch=epoch,
                language=self.overlay_language,
            )
            natural_events: set[str] = set()
            for frame, pts_seconds, _source_seq in self.media_reader.frames():
                if writer is None:
                    writer = self.writer_factory(staging / "analyzed_staff_greeting.staging.mp4", float(metadata["fps"]), (int(metadata["width"]), int(metadata["height"])))
                observed_at = epoch + timedelta(seconds=pts_seconds)
                observations = self.vision_adapter.process_frame(frame)
                entries, _ = monitor.process(observed_at, observations)
                events, cancellations = self.greeting_core.observe(observed_at, observations, entries)
                for event in events:
                    cycle_id = f"{event.event_key.removesuffix(':greeting')}:greeting-cycle"
                    collector.add(event, cycle_id=cycle_id)
                    renderer.register_event(event)
                    natural_events.add(event.event_key)
                for cancellation in cancellations:
                    if f"{cancellation.greeting_cycle_id.removesuffix(':greeting-cycle')}:greeting" not in natural_events:
                        collector.add_cancellation(cancellation, run_epoch=epoch)
                state_lines: list[str] = []
                track_state_lines: dict[str, tuple[str, ...]] = {}
                for observation in observations:
                    if observation.role == "customer":
                        lines = self.greeting_core.debug_lines(observation.track_token)
                        track_state_lines[observation.track_token] = lines
                        if not state_lines:
                            state_lines.extend(lines)
                render_vision = getattr(self.vision_adapter, "render_debug_overlay", None)
                detection_frame = (
                    render_vision(frame, track_state_lines)
                    if callable(render_vision)
                    else frame
                )
                writer.write(renderer.render(detection_frame, pts_seconds=pts_seconds, observations=observations, state_lines=state_lines))
            if writer is None:
                raise ValueError("video_has_no_frames")
            eof_seconds = metadata.get("last_pts_seconds")
            if eof_seconds is None:
                if not {"last_raw_pts", "first_raw_pts", "time_base_num", "time_base_den"}.issubset(metadata):
                    raise OfflineEligibilityError("presentation_timeline_invalid")
                eof_seconds = (float(metadata["last_raw_pts"]) - float(metadata["first_raw_pts"])) * (
                    float(metadata["time_base_num"]) / float(metadata["time_base_den"])
                )
            eof_time = epoch + timedelta(seconds=float(eof_seconds))
            for cancellation in self.greeting_core.cancel_all(eof_time, "video_eof"):
                collector.add_cancellation(cancellation, run_epoch=epoch)
            writer.release()
            writer_released = True
            if not (staging / "analyzed_staff_greeting.staging.mp4").is_file():
                raise RuntimeError("video_writer_output_missing")
            collector.finalize()
            final_video = run_dir / "analyzed_staff_greeting.mp4"
            os.replace(staging / "analyzed_staff_greeting.staging.mp4", final_video)
            os.replace(collector.events_path, run_dir / "greeting_events.jsonl")
            os.replace(collector.summary_path, run_dir / "greeting_summary.json")
            manifest = {
                "analysis_run_id": self.analysis_run_id,
                "status": "FINALIZED",
                "source": "offline_mp4",
                "camera_id": self.camera.camera_id,
                "camera_role": self.camera.camera_role.value,
                "schema_version": collector.schema_version,
                "presentation_timeline_status": "verified",
                "greeting_analysis_status": "eligible",
                "outputs": {"greeting_events": str((run_dir / "greeting_events.jsonl").resolve()),
                            "greeting_summary": str((run_dir / "greeting_summary.json").resolve()),
                            "analyzed_video": str(final_video.resolve())},
            }
            _atomic_write_json(manifest_path, manifest)
            return OfflineRunResult(self.analysis_run_id, "FINALIZED", run_dir, manifest_path)
        except OfflineEligibilityError as exc:
            diagnostics = {"analysis_run_id": self.analysis_run_id, "status": "NOT_ELIGIBLE", "source": "offline_mp4",
                           "camera_id": self.camera.camera_id, "camera_role": self.camera.camera_role.value,
                           "media": metadata, "presentation_timeline_status": "unavailable", "greeting_analysis_status": "not_eligible", "reason": str(exc)}
            (run_dir / "run_diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            return OfflineRunResult(self.analysis_run_id, "NOT_ELIGIBLE", run_dir, manifest_path)
        except ValueError as exc:
            diagnostics = {"analysis_run_id": self.analysis_run_id, "status": "FAILED", "source": "offline_mp4",
                           "camera_id": self.camera.camera_id, "camera_role": self.camera.camera_role.value,
                           "media": metadata, "presentation_timeline_status": "verified" if eligibility_complete else "unavailable",
                           "greeting_analysis_status": "failed", "reason": str(exc)}
            (run_dir / "run_diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            return OfflineRunResult(self.analysis_run_id, "FAILED", run_dir, manifest_path)
        except Exception as exc:
            diagnostics = {"analysis_run_id": self.analysis_run_id, "status": "FAILED", "source": "offline_mp4",
                           "camera_id": self.camera.camera_id, "camera_role": self.camera.camera_role.value,
                           "media": metadata, "presentation_timeline_status": "verified" if eligibility_complete else "unavailable",
                           "greeting_analysis_status": "failed", "reason": type(exc).__name__}
            (run_dir / "run_diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
            return OfflineRunResult(self.analysis_run_id, "FAILED", run_dir, manifest_path)
        finally:
            if writer is not None and not writer_released:
                try:
                    writer.release()
                except Exception:
                    pass


def load_reception_camera_config(path: Path, camera_id: str) -> BehaviorCameraConfig:
    if not camera_id.strip():
        raise ValueError("camera_config_unavailable")
    matches = [item for item in load_behavior_camera_configs(path) if item.camera_id == camera_id]
    if len(matches) != 1:
        raise ValueError("camera_config_unavailable")
    camera = matches[0]
    if camera.camera_role.value != "reception_entry":
        raise ValueError("camera_role_invalid")
    if camera.greeting_area is None or camera.ground_projector is None or camera.calibration_image_size is None:
        raise ValueError("camera_config_unavailable")
    return camera
