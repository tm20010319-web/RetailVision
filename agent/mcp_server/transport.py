"""MCP stdio newline-delimited JSON-RPC transport."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO


class StdioJsonRpcTransport:
    """Read one JSON-RPC object per line and write one response per line."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.handler = handler
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

    def serve(self) -> None:
        for line in self.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("jsonrpc_request_object_required")
                response = self.handler(request)
                if response is not None:
                    self.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                    self.stdout.flush()
            except Exception as exc:
                self.stderr.write(f"mcp_stdio_request_failed: {exc}\n")
                self.stderr.flush()
