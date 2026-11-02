from __future__ import annotations

import shutil
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from demo.camera_profile.capture import CameraCaptureLoop, save_real_crop
from demo.camera_profile.runtime import DemoRuntime
from demo.camera_profile.store import VisitorStore


ROOT = Path(__file__).resolve().parent / ".test_data_capture"


def event(bbox=None, track_id=7, confirmed_at=None):
    return SimpleNamespace(
        camera_id="entrance-1",
        track_id=track_id,
        bbox=bbox if bbox is not None else [-2, 1, 8, 9],
        detection_confidence=0.88,
        confirmed_at=confirmed_at or datetime(2026, 7, 16, 12, 0, 0),
    )


class FakeWorker:
    def __init__(self):
        self.enqueued = []
        self.running = False
        self.busy = False
        self.queue_depth = 0

    def start(self, **kwargs):
        self.running = True

    def enqueue(self, visitor_id):
        self.enqueued.append(visitor_id)
        self.queue_depth = len(self.enqueued)

    def stop(self, timeout=5):
        self.running = False


class FakePipeline:
    def __init__(self):
        self.frames = []
        self.frame_seen = threading.Event()

    def process_frame(self, frame):
        self.frames.append(frame.copy())
        self.frame_seen.set()


class FakeCapture:
    def __init__(self, opened, reads=None):
        self.opened = opened
        self.reads = list(reads or [])
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.reads:
            return self.reads.pop(0)
        return False, None

    def release(self):
        self.released = True


class CameraCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)
        (ROOT / "captures").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)

    def test_save_real_crop_clamps_bbox_and_writes_actual_pixels(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        frame[1:9, 0:8] = (10, 80, 200)

        path = save_real_crop(event(), frame, ROOT / "captures")

        self.assertIsNotNone(path)
        saved = cv2.imread(str(path))
        self.assertIsNotNone(saved)
        self.assertEqual((8, 8), saved.shape[:2])
        self.assertGreater(float(saved.mean()), 0)

    def test_save_real_crop_rejects_invalid_or_empty_bbox(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        self.assertIsNone(save_real_crop(event([1, 2, 3]), frame, ROOT / "captures"))
        self.assertIsNone(save_real_crop(event([5, 5, 5, 8]), frame, ROOT / "captures"))

    def test_capture_loop_reconnects_after_open_and_read_failures(self):
        frame = np.ones((4, 4, 3), dtype=np.uint8)
        captures = [
            FakeCapture(False),
            FakeCapture(True, [(True, frame), (False, None)]),
            FakeCapture(True, [(True, frame)]),
        ]
        pipeline = FakePipeline()

        loop = CameraCaptureLoop(
            source="rtsp://camera/stream1",
            pipeline=pipeline,
            capture_factory=lambda source: captures.pop(0),
            reconnect_delay_seconds=0.01,
        )
        loop.start()
        self.assertTrue(pipeline.frame_seen.wait(timeout=1))
        deadline = time.time() + 1
        while time.time() < deadline and len(pipeline.frames) < 2:
            time.sleep(0.01)
        loop.stop(timeout=1)

        self.assertGreaterEqual(len(pipeline.frames), 2)
        self.assertTrue(loop.last_capture_released)
        self.assertFalse(loop.running)

    def test_runtime_stable_callback_creates_real_pending_visitor(self):
        store = VisitorStore(ROOT / "visitors.json", ROOT / "captures")
        worker = FakeWorker()
        callback_holder = {}

        def pipeline_factory(**kwargs):
            callback_holder["callback"] = kwargs["on_stable_track_frame"]
            return FakePipeline()

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
            capture_factory=lambda source: FakeCapture(False),
        )
        frame = np.full((10, 10, 3), 120, dtype=np.uint8)

        callback_holder["callback"](event([1, 1, 9, 9]), frame)

        visitors = store.list_recent()
        self.assertEqual(1, len(visitors))
        visitor = visitors[0]
        self.assertTrue(Path(visitor.capture_path).is_file())
        self.assertEqual("pending", visitor.profile_status)
        self.assertEqual("/captures/" + Path(visitor.capture_path).name, visitor.capture_url)
        self.assertEqual([visitor.visitor_id], worker.enqueued)

    def test_runtime_applies_global_capture_cooldown_to_track_churn(self):
        store = VisitorStore(ROOT / "visitors.json", ROOT / "captures")
        worker = FakeWorker()
        callback_holder = {}

        def pipeline_factory(**kwargs):
            callback_holder["callback"] = kwargs["on_stable_track_frame"]
            return FakePipeline()

        runtime = DemoRuntime(
            store=store,
            worker=worker,
            source="rtsp://camera/stream1",
            camera_id="entrance-1",
            captures_dir=ROOT / "captures",
            model_path="data/models/yolov8n.pt",
            yolo_device="cpu",
            conf_threshold=0.5,
            capture_cooldown_seconds=3.0,
            pipeline_factory=pipeline_factory,
            capture_factory=lambda source: FakeCapture(False),
        )
        frame = np.full((10, 10, 3), 120, dtype=np.uint8)
        start = datetime(2026, 7, 16, 12, 0, 0)

        callback_holder["callback"](event(track_id=1, confirmed_at=start), frame)
        callback_holder["callback"](
            event(track_id=2, confirmed_at=start.replace(second=1)), frame
        )
        callback_holder["callback"](
            event(track_id=3, confirmed_at=start.replace(second=4)), frame
        )

        self.assertEqual(2, len(store.list_recent()))
        self.assertEqual(2, len(worker.enqueued))


if __name__ == "__main__":
    unittest.main()
