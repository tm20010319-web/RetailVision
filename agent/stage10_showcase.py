"""Video artifact helpers for the Stage 10.3 acceptance package."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class Stage10ShowcaseResult:
    output_path: str
    clip_count: int
    frame_count: int
    frame_width: int
    frame_height: int
    fps: float

    def to_dict(self) -> dict[str, object]:
        return {
            "output_path": self.output_path,
            "clip_count": self.clip_count,
            "frame_count": self.frame_count,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "fps": self.fps,
        }


def concatenate_stage10_videos(
    input_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    fps: float = 5.0,
    frame_size: Optional[tuple[int, int]] = None,
) -> Stage10ShowcaseResult:
    """Concatenate real input frames in order into one silent MP4 artifact."""

    paths = tuple(Path(path) for path in input_paths)
    if not paths:
        raise ValueError("at least one input video is required")
    if fps <= 0:
        raise ValueError("fps must be positive")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"input video does not exist: {missing[0]}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    written = 0
    width = height = 0
    try:
        for path in paths:
            capture = cv2.VideoCapture(str(path))
            if not capture.isOpened():
                raise RuntimeError(f"cannot open input video: {path}")
            try:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if writer is None:
                        width, height = frame_size or (int(frame.shape[1]), int(frame.shape[0]))
                        writer = cv2.VideoWriter(
                            str(output),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            float(fps),
                            (width, height),
                        )
                        if not writer.isOpened():
                            raise RuntimeError(f"cannot open showcase writer: {output}")
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                    writer.write(frame)
                    written += 1
            finally:
                capture.release()
    finally:
        if writer is not None:
            writer.release()
    if written == 0:
        raise ValueError("input videos contain no decodable frames")
    return Stage10ShowcaseResult(
        output_path=str(output),
        clip_count=len(paths),
        frame_count=written,
        frame_width=width,
        frame_height=height,
        fps=float(fps),
    )


def extract_video_keyframes(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    positions: Sequence[float] = (0.1, 0.5, 0.9),
) -> tuple[Path, ...]:
    """Extract deterministic review frames from an existing video."""

    if not positions or any(position < 0 or position > 1 for position in positions):
        raise ValueError("positions must contain values between 0 and 1")
    source = Path(video_path)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video for keyframes: {source}")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 1:
        capture.release()
        raise ValueError("video contains no decodable frames")
    saved = []
    try:
        for index, position in enumerate(positions, start=1):
            frame_index = min(total - 1, int(round((total - 1) * float(position))))
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(
                    f"cannot decode keyframe {frame_index} from video: {source}"
                )
            path = root / f"keyframe_{index:02d}_{frame_index:06d}.jpg"
            if not cv2.imwrite(str(path), frame):
                raise RuntimeError(f"cannot write keyframe: {path}")
            saved.append(path)
    finally:
        capture.release()
    return tuple(saved)


def apply_recorded_event_banners(
    input_path: str | Path,
    frame_results_path: str | Path,
    output_path: str | Path,
    *,
    event_hold_seconds: float = 3.0,
) -> Stage10ShowcaseResult:
    """Reapply banners from recorded events without creating event facts."""

    if event_hold_seconds < 0:
        raise ValueError("event_hold_seconds must be non-negative")
    rows = _read_frame_results(Path(frame_results_path))
    source = Path(input_path)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ValueError("input and output video paths must differ")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open input video: {source}")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 5.0
    writer = None
    written = 0
    active_event: Optional[Mapping[str, object]] = None
    expires_at: Optional[datetime] = None
    width = height = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if written >= len(rows):
                raise ValueError("overlay video has more frames than frame_results.jsonl")
            row = rows[written]
            observed_at = datetime.fromisoformat(str(row["timestamp"]))
            events = tuple(row.get("events", ()))
            if events:
                active_event = _preferred_recorded_event(events)
                expires_at = observed_at + timedelta(seconds=float(event_hold_seconds))
            elif expires_at is not None and observed_at > expires_at:
                active_event = None
                expires_at = None
            if active_event is not None:
                frame = _draw_recorded_event_banner(frame, active_event)
            if writer is None:
                height, width = frame.shape[:2]
                output.parent.mkdir(parents=True, exist_ok=True)
                writer = cv2.VideoWriter(
                    str(output),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    (width, height),
                )
                if not writer.isOpened():
                    raise RuntimeError(f"cannot open overlay writer: {output}")
            writer.write(frame)
            written += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()
    if written != len(rows):
        raise ValueError(
            f"frame count mismatch: video={written}, frame_results={len(rows)}"
        )
    return Stage10ShowcaseResult(
        output_path=str(output),
        clip_count=1,
        frame_count=written,
        frame_width=width,
        frame_height=height,
        fps=fps,
    )


def _read_frame_results(path: Path) -> tuple[dict[str, object], ...]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            rows.append(value)
    return tuple(rows)


def _preferred_recorded_event(
    events: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    return next(
        (
            event
            for event in reversed(events)
            if str(event.get("event_type")) in {"PICK_UP", "PUT_DOWN"}
        ),
        events[-1],
    )


@lru_cache(maxsize=16)
def _banner_font(size: int) -> ImageFont.FreeTypeFont:
    path = Path(r"C:\Windows\Fonts\msyhbd.ttc")
    if not path.is_file():
        raise RuntimeError(f"Chinese overlay font is unavailable: {path}")
    return ImageFont.truetype(str(path), size=size)


def _draw_recorded_event_banner(
    frame: np.ndarray,
    event: Mapping[str, object],
) -> np.ndarray:
    message = str(event.get("display", {}).get("message") or "")
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image, "RGBA")
    width, _height = image.size
    left, right = 378, min(width - 16, 916)
    font = _banner_font(26)
    for size in range(26, 16, -1):
        candidate = _banner_font(size)
        if draw.textbbox((0, 0), message, font=candidate)[2] <= right - left - 36:
            font = candidate
            break
    draw.rounded_rectangle(
        (left, 16, right, 62),
        radius=4,
        fill=(127, 29, 29, 255),
        outline=(255, 90, 95, 255),
        width=2,
    )
    draw.text((left + 18, 23), message, font=font, fill=(248, 250, 252, 255))
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
