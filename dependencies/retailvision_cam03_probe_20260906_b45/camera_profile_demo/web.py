from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


PACKAGE_DIR = Path(__file__).resolve().parent


def _public_visitor(record: Any) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude={"capture_path"})


def create_app(runtime: Any) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    app = FastAPI(title="真实摄像头顾客画像 Demo", lifespan=lifespan)

    @app.get("/api/status")
    def get_status():
        return runtime.status().model_dump(mode="json")

    @app.get("/api/visitors")
    def list_visitors():
        return {
            "visitors": [
                _public_visitor(record) for record in runtime.store.list_recent()
            ]
        }

    @app.get("/api/visitors/{visitor_id}")
    def get_visitor(visitor_id: str):
        record = runtime.store.get(visitor_id)
        if record is None:
            raise HTTPException(status_code=404, detail="visitor not found")
        return _public_visitor(record)

    @app.get("/", include_in_schema=False)
    def dashboard():
        html = (PACKAGE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    Path(runtime.captures_dir).mkdir(parents=True, exist_ok=True)
    app.mount(
        "/captures",
        StaticFiles(directory=runtime.captures_dir),
        name="captures",
    )
    app.mount(
        "/static",
        StaticFiles(directory=PACKAGE_DIR / "static"),
        name="static",
    )
    return app
