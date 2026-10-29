from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Mapping, Sequence


def _video_source(value: str) -> str | int:
    return int(value) if value.isdigit() else value


@dataclass(frozen=True)
class DemoConfig:
    video_source: str | int
    camera_id: str
    yolo_model: str
    yolo_device: str
    conf_threshold: float
    ollama_url: str
    ollama_model: str
    ollama_timeout_seconds: float
    capture_cooldown_seconds: float
    host: str
    port: int

    @classmethod
    def from_env_and_args(
        cls,
        argv: Sequence[str] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "DemoConfig":
        env = os.environ if environ is None else environ
        parser = argparse.ArgumentParser(
            description="Real-camera customer profile demo without device identity API."
        )
        parser.add_argument("--source", default=None)
        parser.add_argument("--host", default=None)
        parser.add_argument("--port", type=int, default=None)
        args = parser.parse_args(argv)

        raw_source = args.source or env.get("DEMO_VIDEO_SOURCE", "").strip()
        if not raw_source:
            raise ValueError("--source or DEMO_VIDEO_SOURCE is required")
        timeout = float(env.get("DEMO_OLLAMA_TIMEOUT_SECONDS", "45"))
        port = args.port if args.port is not None else int(env.get("DEMO_PORT", "8010"))
        confidence = float(env.get("DEMO_CONF_THRESHOLD", "0.5"))
        cooldown = float(env.get("DEMO_CAPTURE_COOLDOWN_SECONDS", "3"))
        if timeout <= 0:
            raise ValueError("DEMO_OLLAMA_TIMEOUT_SECONDS must be positive")
        if port < 1 or port > 65535:
            raise ValueError("DEMO_PORT must be between 1 and 65535")
        if confidence <= 0 or confidence > 1:
            raise ValueError("DEMO_CONF_THRESHOLD must be in (0, 1]")
        if cooldown < 0:
            raise ValueError("DEMO_CAPTURE_COOLDOWN_SECONDS must be non-negative")
        return cls(
            video_source=_video_source(raw_source),
            camera_id=env.get("DEMO_CAMERA_ID", "entrance-1"),
            yolo_model=env.get("DEMO_YOLO_MODEL", "yolov8n.pt"),
            yolo_device=env.get("DEMO_YOLO_DEVICE", "cpu"),
            conf_threshold=confidence,
            ollama_url=env.get(
                "DEMO_OLLAMA_URL", "http://localhost:11434/api/generate"
            ),
            ollama_model=env.get("DEMO_OLLAMA_MODEL", "qwen2.5vl:3b"),
            ollama_timeout_seconds=timeout,
            capture_cooldown_seconds=cooldown,
            host=args.host or env.get("DEMO_HOST", "0.0.0.0"),
            port=port,
        )
