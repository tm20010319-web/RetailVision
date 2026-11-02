"""Read-only smoke tests for the replacement P6S device HTTP API.

The web console URL proves only that the management service is reachable.
Set P6S_READONLY_API_URL to a documented GET endpoint before treating this as
device API verification. No POST, PUT, PATCH, or DELETE request is issued.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from urllib.parse import urldefrag, urlsplit, urlunsplit

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth


DEFAULT_WEB_URL = "http://p6scgi-api.p6sai.com:4999/web/#/663863654"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_url(url: str) -> str:
    """Remove credentials, query parameters, and fragments from diagnostics."""
    clean_url, _fragment = urldefrag(url)
    parts = urlsplit(clean_url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass(frozen=True)
class ProbeResult:
    url: str
    status_code: int
    content_type: str
    response_bytes: int


def _build_auth():
    mode = os.getenv("P6S_AUTH_MODE", "none").strip().lower()
    if mode == "none":
        return None
    if mode not in {"basic", "digest"}:
        raise ValueError("P6S_AUTH_MODE must be none, basic, or digest")
    username = os.getenv("P6S_USERNAME", "")
    password = os.getenv("P6S_PASSWORD", "")
    if not username or not password:
        raise ValueError("P6S_USERNAME and P6S_PASSWORD are required for auth")
    auth_type = HTTPBasicAuth if mode == "basic" else HTTPDigestAuth
    return auth_type(username, password)


def probe_readonly(url: str, *, expected_text: str = "") -> ProbeResult:
    """Issue one GET request and return sanitized response metadata."""
    clean_url, _fragment = urldefrag(url.strip())
    if not clean_url:
        raise ValueError("probe URL is required")
    connect_timeout = float(os.getenv("P6S_CONNECT_TIMEOUT_SECONDS", "3"))
    read_timeout = float(os.getenv("P6S_READ_TIMEOUT_SECONDS", "8"))
    if connect_timeout <= 0 or read_timeout <= 0:
        raise ValueError("P6S timeouts must be positive")
    response = requests.get(
        clean_url,
        auth=_build_auth(),
        timeout=(connect_timeout, read_timeout),
        allow_redirects=True,
        verify=_env_bool("P6S_VERIFY_TLS", True),
        headers={"Accept": "application/json, text/plain, text/html"},
    )
    if response.status_code in {401, 403}:
        raise AssertionError(
            f"authentication rejected by {_safe_url(clean_url)}: "
            f"HTTP {response.status_code}"
        )
    if response.status_code < 200 or response.status_code >= 400:
        raise AssertionError(
            f"unexpected response from {_safe_url(clean_url)}: "
            f"HTTP {response.status_code}"
        )
    if expected_text and expected_text not in response.text:
        raise AssertionError(
            f"expected marker was not found in response from {_safe_url(clean_url)}"
        )
    return ProbeResult(
        url=_safe_url(response.url),
        status_code=response.status_code,
        content_type=response.headers.get("Content-Type", ""),
        response_bytes=len(response.content),
    )


class ReplacementDeviceHttpSmokeTest(unittest.TestCase):
    def test_01_management_web_endpoint_is_reachable(self):
        result = probe_readonly(os.getenv("P6S_WEB_URL", DEFAULT_WEB_URL))

        self.assertGreater(result.response_bytes, 0)
        print(
            f"WEB_OK url={result.url} status={result.status_code} "
            f"content_type={result.content_type!r} bytes={result.response_bytes}"
        )

    def test_02_documented_readonly_api_is_reachable(self):
        api_url = os.getenv("P6S_READONLY_API_URL", "").strip()
        if not api_url:
            self.skipTest(
                "Set P6S_READONLY_API_URL to a documented GET endpoint; "
                "the /web/ page alone is not device API verification"
            )

        expected_text = os.getenv("P6S_EXPECT_TEXT", "")
        result = probe_readonly(api_url, expected_text=expected_text)
        print(
            f"API_OK url={result.url} status={result.status_code} "
            f"content_type={result.content_type!r} bytes={result.response_bytes}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
