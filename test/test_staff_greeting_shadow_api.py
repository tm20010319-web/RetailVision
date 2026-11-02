from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from fastapi.testclient import TestClient

from agent.api.member_api import app


class FakeGreetingQuery:
    def recent_shadow(self, **kwargs):
        return [{"id": "shadow-1", "label": "SHADOW / 不计绩效", "query": kwargs}]

    def get_shadow(self, evaluation_id):
        return None if evaluation_id == "missing" else {"id": evaluation_id, "mode": "shadow"}


class StaffGreetingShadowApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.patch = patch(
            "agent.api.member_api.get_staff_greeting_shadow_query",
            return_value=FakeGreetingQuery(),
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_shadow_list_is_read_only_and_filterable(self):
        response = self.client.get(
            "/api/operations/greetings/evaluations?path=already_close&decision=uncertain&limit=10"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("SHADOW / 不计绩效", response.json()["items"][0]["label"])
        self.assertEqual("already_close", response.json()["items"][0]["query"]["path"])

    def test_shadow_detail_returns_not_found(self):
        response = self.client.get("/api/operations/greetings/evaluations/missing")
        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
