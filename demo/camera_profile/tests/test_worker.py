from __future__ import annotations

import shutil
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from demo.camera_profile.models import DemoProfile, VisitorRecord, build_mock_basics
from demo.camera_profile.profiling import ProfileResult
from demo.camera_profile.store import VisitorStore
from demo.camera_profile.worker import ProfileWorker


ROOT = Path(__file__).resolve().parent / ".test_data_worker"


def profile(label: str) -> DemoProfile:
    return DemoProfile(
        style_tags=[label],
        visible_clothing={"upper": "外套"},
        interest_categories=["服饰"],
        purchase_intent_score=0.5,
        profile_summary=f"{label}画像",
        sales_suggestion="推荐新品",
        confidence=0.7,
    )


class BlockingProfiler:
    def __init__(self):
        self.release = threading.Event()
        self.first_started = threading.Event()
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def analyze(self, image_path, context):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(context["visitor_id"])
            if len(self.calls) == 1:
                self.first_started.set()
        self.release.wait(timeout=2)
        with self.lock:
            self.active -= 1
        visitor_id = context["visitor_id"]
        if visitor_id.endswith("fallback"):
            return ProfileResult(
                profile=profile("兜底"),
                source="mock_fallback",
                status="fallback",
                error_code="ollama_timeout",
            )
        return ProfileResult(
            profile=profile("真实"),
            source="ollama",
            status="succeeded",
            error_code=None,
        )


class ProfileWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)
        captures = ROOT / "captures"
        captures.mkdir(parents=True, exist_ok=True)
        self.store = VisitorStore(ROOT / "visitors.json", captures)
        self.profiler = BlockingProfiler()
        self.worker = ProfileWorker(self.store, self.profiler)

    def tearDown(self) -> None:
        self.profiler.release.set()
        self.worker.stop(timeout=2)
        shutil.rmtree(ROOT, ignore_errors=True)

    def add_visitor(self, visitor_id: str) -> None:
        path = ROOT / "captures" / f"{visitor_id}.jpg"
        path.write_bytes(b"image")
        self.store.add(
            VisitorRecord(
                visitor_id=visitor_id,
                track_id=1,
                camera_id="entrance-1",
                captured_at=datetime.now(),
                capture_url=f"/captures/{path.name}",
                capture_path=str(path),
                detection_confidence=0.9,
                basics=build_mock_basics(visitor_id),
            )
        )

    def wait_for_status(self, visitor_id: str, expected: str) -> None:
        deadline = time.time() + 2
        while time.time() < deadline:
            record = self.store.get(visitor_id)
            if record and record.profile_status == expected:
                return
            time.sleep(0.01)
        self.fail(f"{visitor_id} did not reach {expected}")

    def test_worker_processes_fifo_without_overlapping_requests(self):
        self.add_visitor("visitor-first")
        self.add_visitor("visitor-second")
        self.worker.start()

        self.worker.enqueue("visitor-first")
        self.worker.enqueue("visitor-second")
        self.assertTrue(self.profiler.first_started.wait(timeout=1))

        self.assertEqual("running", self.store.get("visitor-first").profile_status)
        self.assertEqual("pending", self.store.get("visitor-second").profile_status)
        self.profiler.release.set()
        self.wait_for_status("visitor-second", "succeeded")

        self.assertEqual(["visitor-first", "visitor-second"], self.profiler.calls)
        self.assertEqual(1, self.profiler.max_active)

    def test_fallback_job_does_not_stop_next_job(self):
        self.add_visitor("visitor-fallback")
        self.add_visitor("visitor-success")
        self.profiler.release.set()
        self.worker.start()

        self.worker.enqueue("visitor-fallback")
        self.worker.enqueue("visitor-success")
        self.wait_for_status("visitor-fallback", "fallback")
        self.wait_for_status("visitor-success", "succeeded")

        fallback = self.store.get("visitor-fallback")
        self.assertEqual("mock_fallback", fallback.profile_source)
        self.assertEqual("ollama_timeout", fallback.last_error)
        self.assertTrue(self.worker.running)


if __name__ == "__main__":
    unittest.main()

