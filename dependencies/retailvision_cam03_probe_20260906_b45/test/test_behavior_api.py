from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from fastapi.testclient import TestClient

from agent.member_api import app


class FakeBehaviorQuery:
    def __init__(self, now):
        self.now = now
        self.calls = []

    def peak_entry_window(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "lookback_days": kwargs["days"],
            "window_minutes": 5,
            "count": 4,
            "start": self.now,
            "end": self.now + timedelta(minutes=5),
            "camera_id": kwargs.get("camera_id") or "entrance-1",
            "event_ids": ["entry-1", "entry-2", "entry-3", "entry-4"],
        }

    def recent_behaviors(self, *, limit):
        return [{"id": "behavior-1", "limit": limit}]

    def staff_greeting_summary(self, *, scope="official"):
        self.calls.append({"scope": scope})
        return [{
            "staff_name": "Alice",
            "greeting_count": 3,
            "last_greeted_at": "2026-07-27T10:00:00",
        }]

    def recent_entries(self, *, limit):
        return [{"id": "entry-1", "limit": limit}]


class FakeClipStore:
    def __init__(self, ready_path=None):
        self.ready_path_value = ready_path
        self.requests = []

    def request(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(id="clip-1", status="pending")

    def status(self, clip_id):
        if clip_id == "missing":
            return None
        return {
            "id": clip_id,
            "status": "ready" if self.ready_path_value else "pending",
            "available": bool(self.ready_path_value),
        }

    def ready_path(self, clip_id):
        return self.ready_path_value


class BehaviorApiTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 27, 10, 0, 0)
        self.query = FakeBehaviorQuery(self.now)
        self.store = FakeClipStore()
        self.patches = [
            patch("agent.member_api.get_service", return_value=MagicMock()),
            patch("agent.member_api.get_behavior_query_service", return_value=self.query),
            patch("agent.member_api.get_evidence_clip_store", return_value=self.store),
            patch("agent.member_api.get_evidence_clip_processor", return_value=None),
            patch("agent.member_api.settings.PEAK_ENTRY_CAMERA_ID", "entrance-1"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = TestClient(app)

    def test_peak_query_serializes_window(self):
        response = self.client.get("/api/traffic/peak?days=7")

        self.assertEqual(200, response.status_code)
        self.assertEqual(4, response.json()["count"])
        self.assertEqual("2026-07-27T10:00:00", response.json()["start"])
        self.assertEqual("2026-07-27T10:05:00", response.json()["end"])
        self.assertEqual(7, self.query.calls[0]["days"])

    def test_peak_query_rejects_other_lookbacks(self):
        response = self.client.get("/api/traffic/peak?days=5")
        self.assertEqual(422, response.status_code)

    def test_recent_behavior_contract(self):
        response = self.client.get("/api/behaviors/recent?limit=25")

        self.assertEqual(200, response.status_code)
        self.assertEqual([{"id": "behavior-1", "limit": 25}], response.json()["items"])

    def test_staff_greeting_summary_contract(self):
        response = self.client.get("/api/staff-greetings/summary")

        self.assertEqual(200, response.status_code)
        self.assertEqual("Alice", response.json()["items"][0]["staff_name"])
        self.assertEqual(3, response.json()["items"][0]["greeting_count"])

    def test_staff_greeting_demo_scope_is_forwarded(self):
        response = self.client.get("/api/staff-greetings/summary?scope=demo")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"scope": "demo"}, self.query.calls[-1])

    def test_demo_staff_greeting_summary_has_a_distinct_route(self):
        response = self.client.get("/api/staff-greetings/demo-summary")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"scope": "demo"}, self.query.calls[-1])

    def test_staff_greeting_summary_rejects_unknown_scope(self):
        response = self.client.get("/api/staff-greetings/summary?scope=invalid")

        self.assertEqual(422, response.status_code)

    def test_recent_customer_entry_contract(self):
        response = self.client.get("/api/traffic/recent?limit=25")

        self.assertEqual(200, response.status_code)
        self.assertEqual([{"id": "entry-1", "limit": 25}], response.json()["items"])

    def test_browser_can_request_peak_evidence(self):
        response = self.client.post(
            "/api/traffic/peak/evidence",
            headers={"origin": "http://localhost:5173"},
            json={"days": 3},
        )

        self.assertEqual(202, response.status_code)
        self.assertEqual("clip-1", response.json()["evidence"]["id"])
        self.assertEqual("peak_entry_window", self.store.requests[0]["owner_type"])
        self.assertEqual(self.now, self.store.requests[0]["requested_start"])

    def test_other_browser_writes_remain_blocked(self):
        response = self.client.post(
            "/api/members",
            headers={"origin": "http://localhost:5173"},
            json={"member_id": "M1", "name": "member"},
        )

        self.assertEqual(405, response.status_code)
        self.assertEqual(
            "cross-origin member API access is read-only",
            response.json()["detail"],
        )

    def test_missing_evidence_returns_not_found(self):
        response = self.client.get("/api/evidence/missing")
        self.assertEqual(404, response.status_code)

    def test_video_must_resolve_within_evidence_root(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "clip.mp4"
            outside.write_bytes(b"video")
            self.store.ready_path_value = str(outside)
            with patch("agent.member_api.settings.EVIDENCE_CLIP_DIR", root_dir):
                response = self.client.get("/api/evidence/clip-1/video")

        self.assertEqual(404, response.status_code)

    def test_ready_video_is_served_without_exposing_source_path(self):
        with tempfile.TemporaryDirectory() as root_dir:
            video = Path(root_dir) / "clip-1.mp4"
            video.write_bytes(b"video")
            self.store.ready_path_value = str(video)
            with patch("agent.member_api.settings.EVIDENCE_CLIP_DIR", root_dir):
                response = self.client.get("/api/evidence/clip-1/video")

        self.assertEqual(200, response.status_code)
        self.assertEqual(b"video", response.content)
        self.assertIn("clip-1.mp4", response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
