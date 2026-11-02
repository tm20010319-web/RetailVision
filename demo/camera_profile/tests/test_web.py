from __future__ import annotations

import shutil
import unittest
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from demo.camera_profile.config import DemoConfig
from demo.camera_profile.models import DemoStatus, VisitorRecord, build_mock_basics
from demo.camera_profile.store import VisitorStore
from demo.camera_profile.web import create_app


ROOT = Path(__file__).resolve().parent / ".test_data_web"


class FakeRuntime:
    def __init__(self, store, captures_dir):
        self.store = store
        self.captures_dir = captures_dir
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def status(self):
        return DemoStatus(
            camera_connected=True,
            capture_running=True,
            worker_running=True,
            worker_busy=False,
            queue_depth=0,
            last_camera_error=None,
            ollama_model="qwen2.5vl:3b",
        )


class DemoConfigTests(unittest.TestCase):
    def test_cli_source_overrides_environment(self):
        config = DemoConfig.from_env_and_args(
            ["--source", "rtsp://cli/stream"],
            environ={"DEMO_VIDEO_SOURCE": "rtsp://env/stream"},
        )

        self.assertEqual("rtsp://cli/stream", config.video_source)
        self.assertEqual(8010, config.port)
        self.assertEqual(45, config.ollama_timeout_seconds)
        self.assertEqual(3.0, config.capture_cooldown_seconds)

    def test_missing_source_and_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            DemoConfig.from_env_and_args([], environ={})
        with self.assertRaises(ValueError):
            DemoConfig.from_env_and_args(
                [],
                environ={
                    "DEMO_VIDEO_SOURCE": "0",
                    "DEMO_OLLAMA_TIMEOUT_SECONDS": "0",
                },
            )
        with self.assertRaises(ValueError):
            DemoConfig.from_env_and_args(
                [], environ={"DEMO_VIDEO_SOURCE": "0", "DEMO_PORT": "70000"}
            )
        with self.assertRaises(ValueError):
            DemoConfig.from_env_and_args(
                [],
                environ={
                    "DEMO_VIDEO_SOURCE": "0",
                    "DEMO_CAPTURE_COOLDOWN_SECONDS": "-1",
                },
            )


class DemoWebTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(ROOT, ignore_errors=True)
        captures = ROOT / "captures"
        captures.mkdir(parents=True, exist_ok=True)
        self.store = VisitorStore(ROOT / "visitors.json", captures)
        capture_path = captures / "visitor.jpg"
        capture_path.write_bytes(b"jpeg")
        visitor_id = "visitor-20260716-120000-001"
        self.store.add(
            VisitorRecord(
                visitor_id=visitor_id,
                track_id=4,
                camera_id="entrance-1",
                captured_at=datetime(2026, 7, 16, 12, 0, 0),
                capture_url="/captures/visitor.jpg",
                capture_path=str(capture_path),
                detection_confidence=0.91,
                basics=build_mock_basics(visitor_id),
            )
        )
        self.runtime = FakeRuntime(self.store, captures)
        self.app = create_app(self.runtime)

    def tearDown(self):
        shutil.rmtree(ROOT, ignore_errors=True)

    def test_lifespan_starts_and_stops_runtime(self):
        with TestClient(self.app) as client:
            self.assertEqual(1, self.runtime.started)
            self.assertEqual(200, client.get("/api/status").status_code)

        self.assertEqual(1, self.runtime.stopped)

    def test_visitors_are_public_and_unknown_id_is_404(self):
        with TestClient(self.app) as client:
            response = client.get("/api/visitors")
            detail = client.get("/api/visitors/visitor-20260716-120000-001")
            missing = client.get("/api/visitors/missing")

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.json()["visitors"]))
        self.assertNotIn("capture_path", response.text)
        self.assertEqual(4, detail.json()["track_id"])
        self.assertEqual(404, missing.status_code)

    def test_dashboard_and_capture_are_served(self):
        with TestClient(self.app) as client:
            page = client.get("/")
            capture = client.get("/captures/visitor.jpg")

        self.assertEqual(200, page.status_code)
        self.assertIn("最近访客", page.text)
        self.assertIn("大模型画像", page.text)
        self.assertEqual(b"jpeg", capture.content)


if __name__ == "__main__":
    unittest.main()
