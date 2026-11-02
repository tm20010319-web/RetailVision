from __future__ import annotations

import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from demo.camera_profile.models import VisitorRecord, build_mock_basics
from demo.camera_profile.store import VisitorStore


ROOT = Path(__file__).resolve().parent / ".test_data_store"


def make_visitor(index: int) -> VisitorRecord:
    visitor_id = f"visitor-{index}"
    return VisitorRecord(
        visitor_id=visitor_id,
        track_id=index,
        camera_id="entrance-1",
        captured_at=datetime(2026, 7, 16, 12, 0, 0) + timedelta(seconds=index),
        capture_url=f"/captures/{visitor_id}.jpg",
        capture_path=str(ROOT / "captures" / f"{visitor_id}.jpg"),
        detection_confidence=0.8,
        basics=build_mock_basics(visitor_id),
    )


class VisitorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)
        self.captures_dir = ROOT / "captures"
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.store = VisitorStore(
            ROOT / "visitors.json", self.captures_dir, limit=5
        )

    def tearDown(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)

    def test_mock_basics_are_deterministic_and_not_image_dependent(self):
        visitor_id = "visitor-20260716-120000-001"

        first = build_mock_basics(visitor_id)
        second = build_mock_basics(visitor_id)

        self.assertEqual(first, second)
        self.assertEqual(
            {"display_name", "tier", "points", "visit_count", "customer_status"},
            set(first),
        )
        self.assertNotIn("image", str(first).lower())
        self.assertEqual("pending", make_visitor(0).profile_status)

    def test_store_keeps_only_newest_five(self):
        for index in range(6):
            self.store.add(make_visitor(index))

        recent = self.store.list_recent()

        self.assertEqual(5, len(recent))
        self.assertEqual("visitor-5", recent[0].visitor_id)
        self.assertEqual("visitor-1", recent[-1].visitor_id)
        self.assertIsNone(self.store.get("visitor-0"))

    def test_store_round_trips_json_and_updates_records(self):
        record = make_visitor(1)
        self.store.add(record)
        self.store.update(
            record.visitor_id,
            profile_status="fallback",
            profile_source="mock_fallback",
            last_error="ollama_timeout",
        )

        reloaded = VisitorStore(
            ROOT / "visitors.json", self.captures_dir, limit=5
        )
        reloaded.load()

        loaded = reloaded.get(record.visitor_id)
        self.assertIsNotNone(loaded)
        self.assertEqual("fallback", loaded.profile_status)
        self.assertEqual("mock_fallback", loaded.profile_source)
        self.assertEqual("ollama_timeout", loaded.last_error)
        self.assertFalse((ROOT / "visitors.json.tmp").exists())

    def test_cleanup_removes_only_unreferenced_jpegs(self):
        kept = make_visitor(1)
        kept_path = Path(kept.capture_path)
        kept_path.write_bytes(b"kept")
        orphan_path = self.captures_dir / "orphan.jpg"
        orphan_path.write_bytes(b"orphan")
        self.store.add(kept)

        self.store.cleanup_unreferenced_captures()

        self.assertTrue(kept_path.exists())
        self.assertFalse(orphan_path.exists())


if __name__ == "__main__":
    unittest.main()

