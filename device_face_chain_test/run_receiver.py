"""Command-line runner for the standalone device face-event receiver."""

import os
from dataclasses import dataclass
from typing import Mapping

import uvicorn

from device_face_chain_test.app import build_processor_from_env, create_app


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int


def server_settings_from_env(environ: Mapping[str, str] = os.environ) -> ServerSettings:
    """Read and validate the standalone receiver listener settings."""
    host = environ.get("P6S_EVENT_HOST", "0.0.0.0")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("P6S_EVENT_HOST must be a nonblank string")

    port_value = environ.get("P6S_EVENT_PORT", "8081")
    if isinstance(port_value, bool) or not isinstance(port_value, str):
        raise ValueError("P6S_EVENT_PORT must be an integer from 1 to 65535")
    try:
        port = int(port_value)
    except ValueError as exception:
        raise ValueError("P6S_EVENT_PORT must be an integer from 1 to 65535") from exception
    if port < 1 or port > 65535:
        raise ValueError("P6S_EVENT_PORT must be an integer from 1 to 65535")

    return ServerSettings(host=host.strip(), port=port)


def main(environ: Mapping[str, str] = os.environ) -> None:
    """Compose and serve the standalone device face-event receiver."""
    server = server_settings_from_env(environ)
    processor = build_processor_from_env(environ)
    app = create_app(processor)
    uvicorn.run(app, host=server.host, port=server.port)


if __name__ == "__main__":
    main()
