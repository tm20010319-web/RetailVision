import asyncio
import json
from datetime import datetime
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from device_face_chain_test.protocol import face_reco_ack, heartbeat_ack


FIXED_NOW = datetime(2026, 7, 20, 13, 25, 0)
SENTINEL_PICTURE = "data:image/jpeg;base64,SENTINEL_BASE64_PICTURE"


def heartbeat(event_id=7):
    return {
        "operator": "heartbeat",
        "deviceInfo": {"serialNumber": "P6S-001"},
        "info": {"eventId": event_id},
    }


def face_reco(event_id=8):
    return {
        "operator": "FaceReco",
        "deviceInfo": {"serialNumber": "P6S-001"},
        "info": {
            "eventId": event_id,
            "personInfo": {"uniqueId": "uid-001", "personId": "42"},
            "CaptureImage": {
                "pictureMd5": "abc123",
                "picture": SENTINEL_PICTURE,
            },
        },
    }


class FakeProcessor:
    def __init__(self, prepare_error=None, process_error=None, process_result=None):
        self.prepare_error = prepare_error
        self.process_error = process_error
        self.process_result = process_result or {"status": "passed", "reason": "passed"}
        self.calls = []

    def prepare(self, payload):
        self.calls.append(("prepare", payload))
        if self.prepare_error is not None:
            raise self.prepare_error
        return {"prepared": True}

    def process(self, prepared):
        self.calls.append(("process", prepared))
        if self.process_error is not None:
            raise self.process_error
        return self.process_result


class BlockingProcessor:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()

    def prepare(self, payload):
        return {"prepared": True}

    def process(self, prepared):
        self.started.set()
        self.release.wait(timeout=2)
        self.completed.set()
        return {"status": "passed", "reason": "passed"}


class AppTests(unittest.TestCase):
    def client(self, processor):
        from device_face_chain_test.app import create_app

        return TestClient(create_app(processor, clock=lambda: FIXED_NOW))

    def test_heartbeat_returns_exact_ack_with_echoed_event_id(self):
        processor = FakeProcessor()

        with self.assertLogs("device_face_chain_test.app", level="INFO") as logs:
            response = self.client(processor).post("/api/v1/event/pull", json=heartbeat(71))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), heartbeat_ack(heartbeat(71), FIXED_NOW))
        self.assertEqual(processor.calls, [])
        self.assertIn("operator=heartbeat", "\n".join(logs.output))
        self.assertIn("status=acknowledged", "\n".join(logs.output))

    def test_invalid_heartbeat_envelope_returns_400(self):
        payload = heartbeat()
        del payload["deviceInfo"]["serialNumber"]

        response = self.client(FakeProcessor()).post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "invalid heartbeat event"})

    def test_face_reco_returns_exact_ack_then_prepares_and_processes_once(self):
        processor = FakeProcessor()
        payload = face_reco()

        response = self.client(processor).post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), face_reco_ack(payload))
        self.assertEqual(
            processor.calls,
            [("prepare", payload), ("process", {"prepared": True})],
        )

    def test_face_reco_response_body_is_sent_while_background_processor_waits(self):
        from device_face_chain_test.app import create_app

        processor = BlockingProcessor()
        body_sent = threading.Event()
        app_done = threading.Event()
        sent_bodies = []
        errors = []
        request_body = json.dumps(face_reco()).encode("utf-8")

        async def receive():
            return {"type": "http.request", "body": request_body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                sent_bodies.append(message["body"])
                body_sent.set()

        def serve():
            try:
                asyncio.run(
                    create_app(processor, clock=lambda: FIXED_NOW)(
                        {
                            "type": "http",
                            "asgi": {"version": "3.0"},
                            "http_version": "1.1",
                            "method": "POST",
                            "scheme": "http",
                            "path": "/api/v1/event/pull",
                            "raw_path": b"/api/v1/event/pull",
                            "query_string": b"",
                            "headers": [(b"content-type", b"application/json")],
                            "client": ("127.0.0.1", 12345),
                            "server": ("testserver", 80),
                        },
                        receive,
                        send,
                    )
                )
            except Exception as exception:
                errors.append(exception)
            finally:
                app_done.set()

        server_thread = threading.Thread(target=serve)
        server_thread.start()
        try:
            self.assertTrue(processor.started.wait(timeout=1))
            self.assertTrue(body_sent.wait(timeout=0.2))
            self.assertFalse(processor.completed.is_set())
            self.assertEqual(json.loads(sent_bodies[0]), face_reco_ack(face_reco()))
        finally:
            processor.release.set()
            server_thread.join(timeout=2)

        self.assertFalse(server_thread.is_alive())
        self.assertTrue(app_done.is_set())
        self.assertEqual(errors, [])
        self.assertTrue(processor.completed.is_set())

    def test_background_processing_logs_each_returned_outcome(self):
        for status, reason in (
            ("passed", "passed"),
            ("failed", "mapping_not_found"),
            ("waiting", "unrecognized_face"),
            ("ignored", "duplicate"),
        ):
            with self.subTest(status=status):
                processor = FakeProcessor(process_result={"status": status, "reason": reason})
                with self.assertLogs("device_face_chain_test.app", level="INFO") as logs:
                    response = self.client(processor).post("/api/v1/event/pull", json=face_reco())

                self.assertEqual(response.status_code, 200)
                log_text = "\n".join(logs.output)
                self.assertIn(f"status={status}", log_text)
                self.assertIn(f"reason={reason}", log_text)
                self.assertNotIn("SENTINEL_BASE64_PICTURE", log_text)

    def test_prepare_exception_still_returns_face_reco_ack_without_sensitive_logs(self):
        processor = FakeProcessor(prepare_error=RuntimeError("SENTINEL_EXCEPTION_MESSAGE"))
        payload = face_reco()

        with self.assertLogs("device_face_chain_test.app", level="INFO") as logs:
            response = self.client(processor).post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), face_reco_ack(payload))
        self.assertEqual(processor.calls, [("prepare", payload)])
        log_text = "\n".join(logs.output)
        self.assertIn("exception_type=RuntimeError", log_text)
        self.assertNotIn("SENTINEL_EXCEPTION_MESSAGE", log_text)
        self.assertNotIn("SENTINEL_BASE64_PICTURE", log_text)

    def test_process_exception_still_returns_face_reco_ack(self):
        processor = FakeProcessor(process_error=RuntimeError("processor failed"))
        payload = face_reco()

        response = self.client(processor).post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), face_reco_ack(payload))
        self.assertEqual(
            processor.calls,
            [("prepare", payload), ("process", {"prepared": True})],
        )

    def test_processor_exception_does_not_log_nested_event_data(self):
        processor = FakeProcessor(process_error=RuntimeError("processor failed"))
        payload = face_reco()
        payload["info"]["eventId"] = {"credential": "SENTINEL_NESTED_EVENT_DATA"}

        with self.assertLogs("device_face_chain_test.app", level="INFO") as logs:
            response = self.client(processor).post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SENTINEL_NESTED_EVENT_DATA", "\n".join(logs.output))

    def test_processor_exception_type_is_bounded_in_logs(self):
        exception_type = type("x" * 128 + "SENTINEL_EXCEPTION_TYPE", (Exception,), {})
        processor = FakeProcessor(process_error=exception_type())

        with self.assertLogs("device_face_chain_test.app", level="INFO") as logs:
            response = self.client(processor).post("/api/v1/event/pull", json=face_reco())

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SENTINEL_EXCEPTION_TYPE", "\n".join(logs.output))

    def test_incomplete_face_reco_envelope_returns_fallback_ack_without_processing(self):
        processor = FakeProcessor()
        payload = face_reco()
        del payload["info"]["eventId"]

        with self.assertLogs("device_face_chain_test.app", level="INFO") as logs:
            response = self.client(processor).post("/api/v1/event/pull", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "operator": "FaceReco-Ack",
                "info": {"personId": 0, "uniqueId": "", "pictureMd5": ""},
                "result": {"errorNo": 0, "description": "ok"},
            },
        )
        self.assertEqual(processor.calls, [])
        self.assertIn("reason=fallback_face_reco", "\n".join(logs.output))

    def test_unknown_operator_returns_error_ack_without_processing(self):
        processor = FakeProcessor()

        with self.assertLogs("device_face_chain_test.app", level="INFO") as logs:
            response = self.client(processor).post(
                "/api/v1/event/pull",
                json={"operator": "snapshot", "info": {"eventId": 9}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "operator": "error-Ack",
                "result": {"errorNo": 1, "description": "unsupported_operator"},
            },
        )
        self.assertEqual(processor.calls, [])
        self.assertIn("operator=snapshot", "\n".join(logs.output))
        self.assertIn("reason=unsupported_operator", "\n".join(logs.output))

    def test_malformed_json_and_json_array_return_400(self):
        client = self.client(FakeProcessor())

        malformed = client.post(
            "/api/v1/event/pull",
            content=b'{"operator":',
            headers={"content-type": "application/json"},
        )
        array = client.post("/api/v1/event/pull", json=[face_reco()])

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.json(), {"detail": "invalid JSON event"})
        self.assertEqual(array.status_code, 400)
        self.assertEqual(array.json(), {"detail": "invalid JSON event"})

    def test_environment_defaults_construct_expected_resolver_and_settings(self):
        from device_face_chain_test.app import build_processor_from_env

        class FakeResolver:
            def __init__(self, database_url, logical_device_id):
                self.database_url = database_url
                self.logical_device_id = logical_device_id

        class FakeChainProcessor:
            def __init__(self, resolver, settings):
                self.resolver = resolver
                self.settings = settings

        environ = {
            "DATABASE_URL": "sqlite:///:memory:",
            "P6S_DEVICE_SERIAL": "P6S-001",
            "CAMERA_FACE_DEVICE_ID": "camera-face-001",
        }
        with patch("device_face_chain_test.app.SqliteMappingResolver", FakeResolver), patch(
            "device_face_chain_test.app.ChainProcessor", FakeChainProcessor
        ):
            processor = build_processor_from_env(environ)

        self.assertEqual(processor.resolver.database_url, "sqlite:///:memory:")
        self.assertEqual(processor.resolver.logical_device_id, "camera-face-001")
        self.assertEqual(processor.settings.expected_serial, "P6S-001")
        self.assertEqual(processor.settings.output_dir.as_posix(), "outputs/device_face_chain_test")
        self.assertFalse(processor.settings.save_capture)
        self.assertEqual(processor.settings.similarity_threshold, 0.75)
        self.assertEqual(processor.settings.max_capture_bytes, 1_048_576)

    def test_each_missing_or_invalid_environment_setting_is_rejected_by_name(self):
        from device_face_chain_test.app import build_processor_from_env

        base = {
            "DATABASE_URL": "sqlite:///:memory:",
            "P6S_DEVICE_SERIAL": "P6S-001",
            "CAMERA_FACE_DEVICE_ID": "camera-face-001",
        }
        cases = {
            "DATABASE_URL": ("postgresql://localhost/test", "not a URL"),
            "P6S_DEVICE_SERIAL": ("", "   "),
            "CAMERA_FACE_DEVICE_ID": ("", "   "),
            "P6S_SAVE_CAPTURE": ("maybe", ""),
            "P6S_SIMILARITY_THRESHOLD": ("not-a-number", "0", "1.01", "nan"),
            "P6S_MAX_CAPTURE_BYTES": ("not-a-number", "0", "-1", True),
        }
        for setting, invalid_values in cases.items():
            for invalid_value in invalid_values:
                with self.subTest(setting=setting, invalid_value=invalid_value):
                    environ = {**base, setting: invalid_value}
                    with self.assertRaisesRegex(ValueError, setting):
                        build_processor_from_env(environ)
        for setting in ("DATABASE_URL", "P6S_DEVICE_SERIAL", "CAMERA_FACE_DEVICE_ID"):
            with self.subTest(setting=f"missing {setting}"):
                environ = dict(base)
                del environ[setting]
                with self.assertRaisesRegex(ValueError, setting):
                    build_processor_from_env(environ)

    def test_environment_accepts_all_documented_boolean_values(self):
        from device_face_chain_test.app import build_processor_from_env

        class FakeResolver:
            def __init__(self, *args):
                pass

        class FakeChainProcessor:
            def __init__(self, resolver, settings):
                self.settings = settings

        base = {
            "DATABASE_URL": "sqlite:///:memory:",
            "P6S_DEVICE_SERIAL": "P6S-001",
            "CAMERA_FACE_DEVICE_ID": "camera-face-001",
        }
        expected_values = {
            "true": True,
            "1": True,
            "yes": True,
            "on": True,
            "false": False,
            "0": False,
            "no": False,
            "off": False,
        }
        with patch("device_face_chain_test.app.SqliteMappingResolver", FakeResolver), patch(
            "device_face_chain_test.app.ChainProcessor", FakeChainProcessor
        ):
            for raw_value, expected in expected_values.items():
                with self.subTest(raw_value=raw_value):
                    processor = build_processor_from_env(
                        {**base, "P6S_SAVE_CAPTURE": raw_value}
                    )
                    self.assertIs(processor.settings.save_capture, expected)

    def test_environment_sqlite_dialect_failure_is_sanitized(self):
        from device_face_chain_test.app import build_processor_from_env

        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            build_processor_from_env(
                {
                    "DATABASE_URL": "sqlite+missing:///:memory:",
                    "P6S_DEVICE_SERIAL": "P6S-001",
                    "CAMERA_FACE_DEVICE_ID": "camera-face-001",
                }
            )


if __name__ == "__main__":
    unittest.main()
