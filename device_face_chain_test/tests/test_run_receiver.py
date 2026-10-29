import importlib
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from device_face_chain_test.run_receiver import ServerSettings, main, server_settings_from_env


class ServerSettingsFromEnvTests(unittest.TestCase):
    def test_uses_default_host_and_port(self):
        self.assertEqual(
            server_settings_from_env({}),
            ServerSettings(host="0.0.0.0", port=8081),
        )

    def test_uses_custom_valid_host_and_port(self):
        self.assertEqual(
            server_settings_from_env(
                {"P6S_EVENT_HOST": "127.0.0.1", "P6S_EVENT_PORT": "9090"}
            ),
            ServerSettings(host="127.0.0.1", port=9090),
        )

    def test_rejects_blank_host(self):
        with self.assertRaisesRegex(ValueError, "P6S_EVENT_HOST"):
            server_settings_from_env({"P6S_EVENT_HOST": "   "})

    def test_rejects_invalid_ports(self):
        invalid_ports = ("", "true", "false", "yes", "no", "on", "off", "abc", "8081.5", "0", "-1", "65536")
        for port in invalid_ports:
            with self.subTest(port=port):
                with self.assertRaisesRegex(ValueError, "P6S_EVENT_PORT"):
                    server_settings_from_env({"P6S_EVENT_PORT": port})


class ReceiverRunnerTests(unittest.TestCase):
    def test_main_builds_and_runs_the_app_once(self):
        environ = {"P6S_EVENT_HOST": "127.0.0.1", "P6S_EVENT_PORT": "9090"}
        processor = object()
        app = object()

        with (
            patch("device_face_chain_test.run_receiver.build_processor_from_env", return_value=processor) as build_processor,
            patch("device_face_chain_test.run_receiver.create_app", return_value=app) as create_app,
            patch("device_face_chain_test.run_receiver.uvicorn.run") as run_server,
        ):
            main(environ)

        build_processor.assert_called_once_with(environ)
        create_app.assert_called_once_with(processor)
        run_server.assert_called_once_with(app, host="127.0.0.1", port=9090)

    def test_import_does_not_start_server_or_read_settings(self):
        script = """
import os
import uvicorn
import device_face_chain_test.app
from unittest.mock import patch

class UnreadableEnvironment:
    def get(self, *args, **kwargs):
        raise AssertionError("environment settings were read during import")

with patch.object(os, "environ", UnreadableEnvironment()), patch("uvicorn.run") as run_server:
    module = importlib.import_module("device_face_chain_test.run_receiver")
    assert hasattr(module, "main")
    assert not run_server.called
"""
        completed = subprocess.run(
            [sys.executable, "-c", "import importlib\n" + script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class ReadmeTests(unittest.TestCase):
    @staticmethod
    def _readme() -> str:
        return (Path(__file__).resolve().parents[1] / "README.md").read_text(
            encoding="utf-8"
        )

    def test_member_creation_curl_uses_a_powershell_safe_json_literal(self):
        readme = self._readme()
        self.assertIn(
            "-d '{\"member_id\":\"M001\",\"name\":\"Test Member\"}'",
            readme,
        )
        self.assertNotIn(
            '-d "{\\\"member_id\\\":\\\"M001\\\",\\\"name\\\":\\\"Test Member\\\"}"',
            readme,
        )

    def test_duplicate_guidance_preserves_original_artifacts(self):
        readme = self._readme()
        self.assertIn("`ignored/duplicate`", readme)
        self.assertIn("不会创建或重写事件 JSON 和 `latest_result.json`", readme)
        self.assertIn("检查原始事件 JSON", readme)
