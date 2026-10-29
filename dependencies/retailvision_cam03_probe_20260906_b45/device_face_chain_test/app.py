"""FastAPI receiver and environment composition for device face events."""

import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url

from device_face_chain_test.mapping import SqliteMappingResolver
from device_face_chain_test.processor import ChainProcessor, ProcessorSettings
from device_face_chain_test.protocol import ProtocolError, face_reco_ack, heartbeat_ack


LOGGER = logging.getLogger(__name__)
_FALLBACK_FACE_RECO_ACK = {
    "operator": "FaceReco-Ack",
    "info": {"personId": 0, "uniqueId": "", "pictureMd5": ""},
    "result": {"errorNo": 0, "description": "ok"},
}
_ERROR_ACK = {
    "operator": "error-Ack",
    "result": {"errorNo": 1, "description": "unsupported_operator"},
}
_LOG_VALUE_LIMIT = 128


def _log_scalar(value: object) -> str | None:
    if not isinstance(value, (str, int, float, bool)):
        return None
    return str(value)[:_LOG_VALUE_LIMIT]


def _event_log_values(payload: dict) -> tuple[str | None, str | None, str | None]:
    device_info = payload.get("deviceInfo")
    info = payload.get("info")
    serial_number = device_info.get("serialNumber") if isinstance(device_info, dict) else None
    event_id = info.get("eventId") if isinstance(info, dict) else None
    return (
        _log_scalar(payload.get("operator")),
        _log_scalar(serial_number),
        _log_scalar(event_id),
    )


def _log_event(
    payload: dict,
    *,
    status: object = None,
    reason: object = None,
    exception: Exception | None = None,
) -> None:
    operator, serial_number, event_id = _event_log_values(payload)
    LOGGER.info(
        "event operator=%s serial_number=%s event_id=%s status=%s reason=%s exception_type=%s",
        operator,
        serial_number,
        event_id,
        _log_scalar(status),
        _log_scalar(reason),
        _log_scalar(type(exception).__name__) if exception is not None else None,
    )


def _process_face_reco(processor, payload: dict) -> None:
    try:
        prepared = processor.prepare(payload)
        result = processor.process(prepared)
    except Exception as exception:
        _log_event(payload, exception=exception)
        return
    if isinstance(result, dict):
        _log_event(payload, status=result.get("status"), reason=result.get("reason"))
    else:
        _log_event(payload)


def create_app(processor, clock: Callable[[], datetime] = datetime.now) -> FastAPI:
    """Create a callback application using the supplied recognition processor."""
    app = FastAPI()

    @app.post("/api/v1/event/pull")
    async def receive_event(request: Request, background_tasks: BackgroundTasks):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "invalid JSON event"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"detail": "invalid JSON event"})

        operator = payload.get("operator")
        if operator == "heartbeat":
            try:
                acknowledgement = heartbeat_ack(payload, clock())
            except ProtocolError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "invalid heartbeat event"},
                )
            _log_event(payload, status="acknowledged", reason="heartbeat_ack")
            return acknowledgement

        if operator == "FaceReco":
            try:
                acknowledgement = face_reco_ack(payload)
            except ProtocolError:
                _log_event(payload, status="acknowledged", reason="fallback_face_reco")
                return _FALLBACK_FACE_RECO_ACK
            background_tasks.add_task(_process_face_reco, processor, payload)
            return JSONResponse(content=acknowledgement, background=background_tasks)

        _log_event(payload, status="failed", reason="unsupported_operator")
        return _ERROR_ACK

    return app


def _required_setting(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _boolean_setting(environ: Mapping[str, str], name: str, default: str) -> bool:
    value = environ.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a boolean")
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _threshold_setting(environ: Mapping[str, str]) -> float:
    value = environ.get("P6S_SIMILARITY_THRESHOLD", "0.75")
    if not isinstance(value, str):
        raise ValueError("P6S_SIMILARITY_THRESHOLD must be a number")
    try:
        threshold = float(value)
    except ValueError as exception:
        raise ValueError("P6S_SIMILARITY_THRESHOLD must be a number") from exception
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("P6S_SIMILARITY_THRESHOLD must be between 0 and 1")
    return threshold


def _max_capture_bytes_setting(environ: Mapping[str, str]) -> int:
    value = environ.get("P6S_MAX_CAPTURE_BYTES", "1048576")
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("P6S_MAX_CAPTURE_BYTES must be a positive integer")
    try:
        limit = int(value)
    except ValueError as exception:
        raise ValueError("P6S_MAX_CAPTURE_BYTES must be a positive integer") from exception
    if limit <= 0:
        raise ValueError("P6S_MAX_CAPTURE_BYTES must be a positive integer")
    return limit


def build_processor_from_env(environ: Mapping[str, str] = os.environ) -> ChainProcessor:
    """Build the standalone recognition processor from validated environment settings."""
    database_url = _required_setting(environ, "DATABASE_URL")
    try:
        database = make_url(database_url)
    except Exception as exception:
        raise ValueError("DATABASE_URL must be a valid SQLite URL") from exception
    if database.get_backend_name() != "sqlite":
        raise ValueError("DATABASE_URL must be a SQLite URL")

    expected_serial = _required_setting(environ, "P6S_DEVICE_SERIAL")
    logical_device_id = _required_setting(environ, "CAMERA_FACE_DEVICE_ID")
    output_dir = environ.get("P6S_CHAIN_OUTPUT_DIR", "outputs/device_face_chain_test")
    if not isinstance(output_dir, str):
        raise ValueError("P6S_CHAIN_OUTPUT_DIR must be a path")

    try:
        resolver = SqliteMappingResolver(database_url, logical_device_id)
    except Exception as exception:
        raise ValueError("DATABASE_URL could not create a SQLite resolver") from exception
    settings = ProcessorSettings(
        expected_serial=expected_serial,
        output_dir=Path(output_dir),
        save_capture=_boolean_setting(environ, "P6S_SAVE_CAPTURE", "false"),
        similarity_threshold=_threshold_setting(environ),
        max_capture_bytes=_max_capture_bytes_setting(environ),
    )
    return ChainProcessor(resolver, settings)
