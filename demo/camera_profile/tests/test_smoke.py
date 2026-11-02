from __future__ import annotations

import shutil
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from demo.camera_profile.models import DemoProfile
from demo.camera_profile.profiling import ProfileResult
from demo.camera_profile.runtime import DemoRuntime
from demo.camera_profile.store import VisitorStore
from demo.camera_profile.web import create_app
from demo.camera_profile.worker import ProfileWorker


ROOT = Path(__file__).resolve().parent / ".test_data_smoke"


class ImmediateProfiler:
    def analyze(self, image_path, context):
        self.last_image_path = Path(image_path)
        return ProfileResult(
            profile=DemoProfile(
                style_tags=["真实测试"],
                visible_clothing={"upper": "测试外套"},
                interest_categories=["服饰"],
                purchase_intent_score=0.6,
                profile_summary="由真实裁剪触发的测试画像。",
                sales_suggestion="推荐同风格单品。",
                confidence=0.8,
            ),
            source="ollama",
            status="succeeded",
            error_code=None,
        )


class ClosedCapture:
    def isOpened(self):
        return False

    def release(self):
        pass


class DemoSmokeTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(ROOT, ignore_errors=True)
        (ROOT / "captures").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(ROOT, ignore_errors=True)

    def test_stable_real_crop_reaches_profile_api_without_identity_modules(self):
        store = VisitorStore(ROOT / "visitors.json", ROOT / "captures")
        profiler = ImmediateProfiler()
        worker = ProfileWorker(store, profiler)
        callbacks = {}

        class Pipeline:
            def process_frame(self, frame):
                return None

        def pipeline_factory(**kwargs):
            callbacks["stable"] = kwargs["on_stable_track_frame"]
            return Pipeline()

        runtime = DemoRuntime(
            store=store,
            worker=worker,
            source="rtsp://camera/stream1",
            camera_id="entrance-1",
            captures_dir=ROOT / "captures",
            model_path="data/models/yolov8n.pt",
            yolo_device="cpu",
            conf_threshold=0.5,
            pipeline_factory=pipeline_factory,
            capture_factory=lambda source: ClosedCapture(),
        )
        app = create_app(runtime)
        frame = np.full((20, 20, 3), 140, dtype=np.uint8)
        stable_event = SimpleNamespace(
            camera_id="entrance-1",
            track_id=22,
            bbox=[2, 1, 18, 19],
            detection_confidence=0.93,
            confirmed_at=datetime(2026, 7, 16, 12, 30, 0),
        )

        with TestClient(app) as client:
            callbacks["stable"](stable_event, frame)
            deadline = time.time() + 2
            visitor = None
            while time.time() < deadline:
                payload = client.get("/api/visitors").json()["visitors"]
                if payload and payload[0]["profile_status"] == "succeeded":
                    visitor = payload[0]
                    break
                time.sleep(0.01)
            self.assertIsNotNone(visitor)
            self.assertEqual("ollama", visitor["profile_source"])
            self.assertEqual(["真实测试"], visitor["profile"]["style_tags"])
            capture = client.get(visitor["capture_url"])
            self.assertEqual(200, capture.status_code)
            self.assertGreater(len(capture.content), 10)

        self.assertTrue(profiler.last_image_path.is_file())
        package_dir = Path(__file__).resolve().parents[1]
        production_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in package_dir.glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertNotIn("camera_face_api", production_source)
        self.assertNotIn("FaceRecognizer", production_source)


if __name__ == "__main__":
    unittest.main()

