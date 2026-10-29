"""Start the member management API server."""

from __future__ import annotations

import logging

import uvicorn

from agent.member_api import app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
