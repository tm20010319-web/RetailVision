from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import uvicorn

from demo.camera_profile.config import DemoConfig
from demo.camera_profile.profiling import OllamaDemoProfiler
from demo.camera_profile.runtime import DemoRuntime
from demo.camera_profile.store import VisitorStore
from demo.camera_profile.web import create_app
from demo.camera_profile.worker import ProfileWorker


def build_app(config: DemoConfig):
    package_dir = Path(__file__).resolve().parent
    data_dir = package_dir / "data"
    captures_dir = data_dir / "captures"
    store = VisitorStore(data_dir / "visitors.json", captures_dir, limit=5)
    profiler = OllamaDemoProfiler(
        ollama_url=config.ollama_url,
        model=config.ollama_model,
        timeout_seconds=config.ollama_timeout_seconds,
    )
    worker = ProfileWorker(store, profiler)
    runtime = DemoRuntime(
        store=store,
        worker=worker,
        source=config.video_source,
        camera_id=config.camera_id,
        captures_dir=captures_dir,
        model_path=config.yolo_model,
        yolo_device=config.yolo_device,
        conf_threshold=config.conf_threshold,
        capture_cooldown_seconds=config.capture_cooldown_seconds,
        ollama_model=config.ollama_model,
    )
    return create_app(runtime)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    config = DemoConfig.from_env_and_args(argv)
    app = build_app(config)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
