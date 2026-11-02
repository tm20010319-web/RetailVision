import unittest

from fastapi.testclient import TestClient

from demo.greeting.api import create_app


class _FakeRuntime:
    def __init__(self):
        self.calls = []
        self.value = {
            "status": "idle",
            "running": False,
            "camera_id": "entrance-1",
            "session_event_count": 0,
        }

    def status(self):
        return dict(self.value)

    def start(self):
        self.calls.append("start")
        self.value.update(status="starting", running=True)
        return self.status()

    def reset(self):
        self.calls.append("reset")
        self.value.update(status="running", running=True)
        return self.status()

    def stop(self):
        self.calls.append("stop")
        self.value.update(status="stopped", running=False)
        return self.status()


class DemoGreetingApiTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _FakeRuntime()
        self.client = TestClient(create_app(self.runtime))

    def test_exposes_explicit_control_endpoints_without_event_injection(self):
        status = self.client.get("/api/demo-greeting/status")
        self.assertEqual(200, status.status_code)
        self.assertEqual("idle", status.json()["status"])

        started = self.client.post("/api/demo-greeting/start")
        self.assertEqual(200, started.status_code)
        self.assertEqual(["start"], self.runtime.calls)
        self.assertEqual("starting", started.json()["status"])

        stopped = self.client.post("/api/demo-greeting/stop")
        self.assertEqual(200, stopped.status_code)
        self.assertEqual(["start", "stop"], self.runtime.calls)

    def test_health_endpoint_reports_worker_state(self):
        response = self.client.get("/healthz")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["ok"])
        self.assertEqual("demo-greeting", response.json()["service"])

