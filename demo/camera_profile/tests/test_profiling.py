from __future__ import annotations

import base64
import json
import shutil
import unittest
from pathlib import Path

import requests

from demo.camera_profile.profiling import OllamaDemoProfiler


ROOT = Path(__file__).resolve().parent / ".test_data_profiling"


def valid_profile() -> dict:
    return {
        "style_tags": ["休闲", "简约"],
        "visible_clothing": {"upper": "浅色外套", "lower": "深色长裤"},
        "interest_categories": ["休闲服饰", "配饰"],
        "purchase_intent_score": 0.62,
        "profile_summary": "顾客穿着简约，正在浏览商品。",
        "sales_suggestion": "可推荐同风格新品。",
        "confidence": 0.81,
    }


class FakeResponse:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class FakeSession:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class OllamaDemoProfilerTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)
        ROOT.mkdir(parents=True, exist_ok=True)
        self.image_path = ROOT / "real-crop.jpg"
        self.image_bytes = b"real-camera-crop-bytes"
        self.image_path.write_bytes(self.image_bytes)

    def tearDown(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)

    def make_profiler(self, session) -> OllamaDemoProfiler:
        return OllamaDemoProfiler(
            ollama_url="http://localhost:11434/api/generate",
            model="qwen2.5vl:3b",
            timeout_seconds=45,
            session=session,
        )

    def test_success_sends_exact_saved_crop_and_validates_profile(self):
        session = FakeSession(
            FakeResponse({"response": json.dumps(valid_profile(), ensure_ascii=False)})
        )
        profiler = self.make_profiler(session)

        result = profiler.analyze(
            self.image_path, {"visitor_id": "visitor-1", "camera_id": "entrance-1"}
        )

        self.assertEqual("succeeded", result.status)
        self.assertEqual("ollama", result.source)
        self.assertEqual(["休闲", "简约"], result.profile.style_tags)
        _, kwargs = session.calls[0]
        self.assertEqual(45, kwargs["timeout"])
        self.assertEqual("qwen2.5vl:3b", kwargs["json"]["model"])
        self.assertFalse(kwargs["json"]["stream"])
        self.assertEqual("json", kwargs["json"]["format"])
        self.assertEqual(
            base64.b64encode(self.image_bytes).decode("ascii"),
            kwargs["json"]["images"][0],
        )

    def test_timeout_returns_complete_fallback(self):
        result = self.make_profiler(
            FakeSession(error=requests.Timeout("slow"))
        ).analyze(self.image_path, {"visitor_id": "visitor-2"})

        self.assertEqual("fallback", result.status)
        self.assertEqual("mock_fallback", result.source)
        self.assertEqual("ollama_timeout", result.error_code)
        self.assertTrue(result.profile.profile_summary)

    def test_connection_failure_returns_unavailable_fallback(self):
        result = self.make_profiler(
            FakeSession(error=requests.ConnectionError("offline"))
        ).analyze(self.image_path, {"visitor_id": "visitor-3"})

        self.assertEqual("ollama_unavailable", result.error_code)
        self.assertEqual("mock_fallback", result.source)

    def test_invalid_output_returns_invalid_model_fallback(self):
        result = self.make_profiler(
            FakeSession(FakeResponse({"response": "not-json"}))
        ).analyze(self.image_path, {"visitor_id": "visitor-4"})

        self.assertEqual("invalid_model_output", result.error_code)
        self.assertEqual("fallback", result.status)

    def test_other_http_failure_returns_generic_fallback(self):
        result = self.make_profiler(
            FakeSession(FakeResponse({"error": "failed"}, status_code=500))
        ).analyze(self.image_path, {"visitor_id": "visitor-5"})

        self.assertEqual("profile_failed", result.error_code)
        self.assertEqual("fallback", result.status)


if __name__ == "__main__":
    unittest.main()

