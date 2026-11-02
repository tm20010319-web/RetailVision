"""Control API for the isolated RTSP Demo greeting worker."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .service import DemoGreetingConfig, DemoGreetingRuntime


logger = logging.getLogger(__name__)


def create_app(runtime: Optional[DemoGreetingRuntime] = None) -> FastAPI:
    worker = runtime or DemoGreetingRuntime(DemoGreetingConfig.from_environment())
    app = FastAPI(
        title="RetailVision Demo Greeting Control",
        version="1.0.0",
        description="Explicitly labelled RTSP camera Demo greeting trigger.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
    )

    @app.get("/healthz")
    def healthz():
        status = worker.status()
        return {
            "ok": status["status"] not in {"error"},
            "service": "demo-greeting",
            "status": status["status"],
        }

    @app.get("/api/demo-greeting/status")
    def status():
        return worker.status()

    @app.post("/api/demo-greeting/start")
    def start():
        try:
            return worker.start()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Demo greeting worker could not start")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/demo-greeting/reset")
    def reset():
        try:
            return worker.reset()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/demo-greeting/stop")
    def stop():
        return worker.stop()

    @app.on_event("shutdown")
    def shutdown_worker() -> None:
        worker.stop()

    return app


app = create_app()
