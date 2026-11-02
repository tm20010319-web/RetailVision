"""Run the Demo greeting control service with ``python -m demo.greeting``."""

from __future__ import annotations

import uvicorn


if __name__ == "__main__":
    uvicorn.run("demo.greeting.api:app", host="0.0.0.0", port=8082, reload=False)
