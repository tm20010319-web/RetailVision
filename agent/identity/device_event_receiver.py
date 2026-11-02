"""Formal FastAPI receiver for sanitized camera push events."""

from __future__ import annotations

import logging
import math
import base64
import binascii
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from agent.identity.device_recognition_intake import (
    DeviceRecognitionEvent,
    FaceCapture,
    DeviceRecognitionIntake,
    source_event_key,
)
from agent.identity.device_event_protocol import (
    ProtocolError,
    face_reco_ack,
    fallback_face_reco_ack,
    heartbeat_ack,
    unsupported_operator_ack,
)
from agent.identity.face_spatial_evidence import FaceSpatialEvidenceAdapter
from agent.identity.push_coverage import PushCoverageStore


LOGGER = logging.getLogger(__name__)
_LOG_VALUE_LIMIT = 128
_DEVICE_TIMEZONE = timezone(timedelta(hours=8))
_FACE_SPATIAL_ADAPTER = FaceSpatialEvidenceAdapter()


def create_device_event_router(
    intake: DeviceRecognitionIntake,
    health_store,
    serial_to_device_id,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    coverage_store: Optional[PushCoverageStore] = None,
    receiver_instance_id: Optional[str] = None,
    boot_id: Optional[str] = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/event/pull")
    async def receive_event(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "invalid JSON event"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"detail": "invalid JSON event"})

        resolved_intake = intake or getattr(
            request.app.state, "device_recognition_intake", None
        )
        resolved_health_store = health_store or getattr(
            request.app.state, "device_health_store", None
        )
        resolved_coverage_store = coverage_store or getattr(
            request.app.state, "push_coverage_store", None
        )
        resolved_receiver_instance_id = receiver_instance_id or getattr(
            request.app.state, "push_coverage_receiver_instance_id", None
        )
        resolved_boot_id = boot_id or getattr(
            request.app.state, "push_coverage_boot_id", None
        )
        return await run_in_threadpool(
            _dispatch_event,
            payload,
            resolved_intake,
            resolved_health_store,
            serial_to_device_id,
            clock,
            resolved_coverage_store,
            resolved_receiver_instance_id,
            resolved_boot_id,
        )

    return router


def _dispatch_event(
    payload,
    intake,
    health_store,
    serial_to_device_id,
    clock,
    coverage_store=None,
    receiver_instance_id=None,
    boot_id=None,
):
    operator = payload.get("operator")
    if operator == "heartbeat":
        return _receive_heartbeat(
            payload,
            health_store,
            serial_to_device_id,
            clock,
            coverage_store,
            receiver_instance_id,
            boot_id,
        )
    if operator == "FaceReco":
        return _receive_face_reco(
            payload,
            intake,
            serial_to_device_id,
            coverage_store,
            receiver_instance_id,
            boot_id,
            clock,
        )
    _log_event(payload, status="ignored", reason="unsupported_operator")
    return unsupported_operator_ack()


def _receive_heartbeat(
    payload,
    health_store,
    serial_to_device_id,
    clock,
    coverage_store=None,
    receiver_instance_id=None,
    boot_id=None,
):
    now_utc, now_utc8 = _clock_times(clock)
    try:
        acknowledgement = heartbeat_ack(payload, now_utc8)
        serial_number = _serial_number(payload)
    except ProtocolError:
        return JSONResponse(status_code=400, content={"detail": "invalid heartbeat event"})

    device_id = _resolve_device_id(serial_to_device_id, serial_number)
    if device_id is None:
        _log_event(payload, status="ignored", reason="unknown_device")
        return acknowledgement
    if health_store is None:
        return JSONResponse(status_code=503, content={"detail": "heartbeat persistence failed"})
    try:
        health_store.mark_heartbeat(device_id, now_utc)
    except Exception as exception:
        _record_coverage_failure(
            coverage_store,
            device_id=device_id,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            source_event_id=_heartbeat_source_event_id(payload),
            observed_at=now_utc,
            failure_class="heartbeat_persistence",
        )
        _log_event(payload, status="failed", reason="health_persistence", exception=exception)
        return JSONResponse(status_code=503, content={"detail": "heartbeat persistence failed"})
    if coverage_store is not None and receiver_instance_id and boot_id:
        try:
            coverage_store.record_heartbeat(
                device_id=device_id,
                receiver_instance_id=receiver_instance_id,
                boot_id=boot_id,
                source_event_id=_heartbeat_source_event_id(payload),
                observed_at=now_utc,
                device_event_at=_heartbeat_event_time(payload),
                device_metadata=_device_metadata(payload.get("deviceInfo", {}), retrieved_at=now_utc),
            )
        except Exception as exception:
            LOGGER.warning(
                "Push coverage heartbeat observation failed: error_type=%s",
                type(exception).__name__,
            )
    _log_event(payload, status="acknowledged", reason="heartbeat")
    return acknowledgement


def _receive_face_reco(
    payload,
    intake,
    serial_to_device_id,
    coverage_store=None,
    receiver_instance_id=None,
    boot_id=None,
    clock=lambda: datetime.now(timezone.utc),
):
    receiver_arrived_at = _clock_times(clock)[0]
    try:
        acknowledgement = face_reco_ack(payload)
    except ProtocolError:
        _log_event(payload, status="ignored", reason="malformed_recognition")
        return fallback_face_reco_ack()

    parsed = _parse_face_reco(payload)
    if parsed is None:
        _log_event(payload, status="ignored", reason="malformed_recognition")
        return acknowledgement
    if parsed["match_number"] == 0:
        _log_event(payload, status="acknowledged", reason="unrecognized_face")
        return acknowledgement

    device_id = _resolve_device_id(serial_to_device_id, parsed["serial_number"])
    if device_id is None:
        _log_event(payload, status="ignored", reason="unknown_device")
        return acknowledgement
    if intake is None:
        _record_coverage_failure(
            coverage_store,
            device_id=device_id,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            source_event_id=parsed["source_event_id"],
            observed_at=_clock_times(clock)[0],
            failure_class="recognition_intake_unavailable",
        )
        return JSONResponse(status_code=503, content={"detail": "recognition persistence failed"})
    maximum_capture_bytes = getattr(
        getattr(intake, "capture_store", None),
        "max_bytes",
        None,
    )
    capture_bytes = _decode_capture_data_uri(
        parsed["capture_data_uri"],
        maximum_bytes=maximum_capture_bytes,
    )
    background_capture_bytes = _decode_capture_data_uri(
        parsed.get("background_capture_data_uri"),
        maximum_bytes=maximum_capture_bytes,
    )
    event = DeviceRecognitionEvent(
        device_id=device_id,
        source="push",
        source_event_id=source_event_key(
            parsed["source_event_id"], parsed["occurred_at"]
        ),
        occurred_at=parsed["occurred_at"],
        group_id=parsed["group_id"],
        person_id=parsed["person_id"],
        rule_id=1,
        unique_id=parsed["unique_id"],
        similarity=parsed["similarity"],
        device_occurred_at_raw=parsed["event_time_raw"],
        device_utc_offset_minutes=parsed["utc_offset_minutes"],
        receiver_arrived_at=receiver_arrived_at,
        face_spatial_evidence=(
            parsed["face_spatial_evidence"].to_safe_dict()
            if parsed["face_spatial_evidence"] is not None
            else None
        ),
        device_metadata=_device_metadata(
            payload.get("deviceInfo", {}), retrieved_at=receiver_arrived_at
        ) or None,
        background_capture=(
            FaceCapture(background_capture_bytes)
            if background_capture_bytes is not None
            else None
        ),
    )
    try:
        result = intake.receive(
            event,
            capture=FaceCapture(capture_bytes) if capture_bytes is not None else None,
        )
    except Exception as exception:
        _record_coverage_failure(
            coverage_store,
            device_id=device_id,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            source_event_id=event.source_event_id,
            observed_at=_clock_times(clock)[0],
            failure_class="recognition_persistence",
        )
        _log_event(
            payload,
            status="failed",
            reason="recognition_persistence",
            exception=exception,
            device_id=device_id,
            source="push",
        )
        return JSONResponse(status_code=503, content={"detail": "recognition persistence failed"})
    if coverage_store is not None and receiver_instance_id and boot_id:
        try:
            coverage_store.record(
                device_id=device_id,
                observation_type="persistence_success",
                observed_at=_clock_times(clock)[0],
                device_event_at=parsed["occurred_at"],
                receiver_instance_id=receiver_instance_id,
                boot_id=boot_id,
                source_event_id=event.source_event_id,
                canonical_event_id=result.canonical_event_id,
                outcome="deduplicated" if result.status == "duplicate" else "committed",
                observation_key=(
                    f"recognition-persistence:{device_id}:{receiver_instance_id}:"
                    f"{event.source_event_id}"
                ),
            )
        except Exception as exception:
            LOGGER.warning(
                "Push coverage recognition observation failed: error_type=%s",
                type(exception).__name__,
            )
    _log_event(
        payload,
        status="accepted" if result.status == "received" else result.status,
        reason="recognition",
        device_id=device_id,
        source="push",
        canonical_event_id=result.canonical_event_id,
    )
    return acknowledgement


def _record_coverage_failure(
    coverage_store,
    *,
    device_id,
    receiver_instance_id,
    boot_id,
    source_event_id,
    observed_at,
    failure_class,
):
    if coverage_store is None or not receiver_instance_id or not boot_id:
        return
    try:
        coverage_store.record_persistence_failure(
            device_id=device_id,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
            source_event_id=source_event_id,
            observed_at=observed_at,
            failure_class=failure_class,
        )
    except Exception as exception:
        LOGGER.warning(
            "Push coverage failure observation unavailable: error_type=%s",
            type(exception).__name__,
        )


def _heartbeat_source_event_id(payload: dict) -> str:
    info = payload.get("info")
    return str(info.get("eventId")) if isinstance(info, dict) else "unknown"


def _heartbeat_event_time(payload: dict) -> Optional[datetime]:
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    return _event_time(info.get("time"))




def _decode_capture_data_uri(
    value: object,
    *,
    maximum_bytes: Optional[int] = None,
) -> Optional[bytes]:
    if not isinstance(value, str):
        return None
    prefixes = ("data:image/jpeg;base64,", "data:image/jpg;base64,")
    prefix = next((item for item in prefixes if value.startswith(item)), None)
    if prefix is None:
        return None
    encoded = value[len(prefix):]
    if maximum_bytes is not None:
        maximum_encoded_length = ((maximum_bytes + 2) // 3) * 4
        if len(encoded) > maximum_encoded_length:
            return None
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if maximum_bytes is not None and len(decoded) > maximum_bytes:
        return None
    return decoded


def _parse_face_reco(payload: dict) -> Optional[dict]:
    device_info = payload.get("deviceInfo")
    info = payload.get("info")
    if not isinstance(device_info, dict) or not isinstance(info, dict):
        return None
    serial_number = _text(device_info.get("serialNumber"), maximum=128)
    device_metadata = _device_metadata(device_info)
    source_event_id = _text(info.get("eventId"), maximum=128)
    event_time_raw = info.get("eventTime", info.get("time"))
    occurred_at = _event_time(event_time_raw)
    match_number = info.get("matchNumber")
    if (
        serial_number is None
        or source_event_id is None
        or occurred_at is None
        or not isinstance(match_number, int)
        or isinstance(match_number, bool)
        or match_number < 0
    ):
        return None
    if match_number == 0:
        return {
            "serial_number": serial_number,
            "source_event_id": source_event_id,
            "occurred_at": occurred_at,
            "match_number": 0,
            "device_metadata": device_metadata,
        }

    person_info = info.get("personInfo")
    if not isinstance(person_info, dict):
        return None
    similarity = _similarity(person_info.get("match"))
    unique_id = _text(person_info.get("uniqueId"), maximum=64, allow_empty=True)
    group_id = _integer_or_none(person_info.get("groupId"))
    person_id = _integer_or_none(person_info.get("personId"))
    if similarity is None or unique_id is None:
        return None
    if not unique_id and (group_id is None or person_id is None):
        return None

    capture_image = info.get("CaptureImage")
    capture_data_uri = None
    if isinstance(capture_image, dict) and isinstance(capture_image.get("picture"), str):
        capture_data_uri = capture_image["picture"]
    face_info = info.get("FaceInfo", info.get("faceInfo"))
    background_image = None
    for container in (face_info, capture_image, info):
        if not isinstance(container, dict):
            continue
        candidate = container.get(
            "BackgroundImage", container.get("backgroundImage")
        )
        if isinstance(candidate, dict):
            background_image = candidate
            break
    background_capture_data_uri = None
    if isinstance(background_image, dict) and isinstance(background_image.get("picture"), str):
        background_capture_data_uri = background_image["picture"]
    return {
        "serial_number": serial_number,
        "source_event_id": source_event_id,
        "occurred_at": occurred_at,
        "match_number": match_number,
        "unique_id": unique_id,
        "group_id": group_id,
        "person_id": person_id,
        "similarity": similarity,
        "capture_data_uri": capture_data_uri,
        "background_capture_data_uri": background_capture_data_uri,
        "event_time_raw": event_time_raw.strip(),
        "utc_offset_minutes": _utc_offset_minutes(event_time_raw),
        "face_spatial_evidence": _FACE_SPATIAL_ADAPTER.parse(
            info,
            occurred_at=occurred_at,
        ),
        "device_metadata": device_metadata,
    }


def _serial_number(payload: dict) -> str:
    device_info = payload.get("deviceInfo")
    serial_number = device_info.get("serialNumber") if isinstance(device_info, dict) else None
    value = _text(serial_number, maximum=128)
    if value is None:
        raise ProtocolError("missing deviceInfo.serialNumber")
    return value


def _device_metadata(device_info: dict, *, retrieved_at: Optional[datetime] = None) -> dict:
    """Keep only non-secret device fingerprint fields for restricted audit."""
    aliases = {
        "device_model": ("model", "deviceModel", "modelName", "deviceType"),
        "firmware_version": ("version", "firmwareVersion", "firmware", "softwareVersion", "deviceVersion"),
        "api_version": ("apiVersion", "apiVer"),
        "protocol_version": ("protocolVersion",),
        "device_serial": ("serialNumber", "serial", "SN", "uuid", "hardwareId", "mac"),
        "metadata_source": ("metadataSource",),
    }
    result = {}
    for normalized_name, keys in aliases.items():
        for key in keys:
            value = _text(device_info.get(key), maximum=256)
            if value is not None:
                result[normalized_name] = {
                    "field_name": normalized_name,
                    "raw_value": value,
                    "normalized_value": value,
                    "source": "device_push_metadata",
                    "device_info_key": key,
                }
                if retrieved_at is not None:
                    result[normalized_name]["retrieved_at"] = retrieved_at.isoformat() + "Z"
                break
    return result


def _resolve_device_id(resolver, serial_number: str) -> Optional[str]:
    value = resolver(serial_number) if callable(resolver) else resolver.get(serial_number)
    return _text(value, maximum=64) if value is not None else None


def _similarity(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        percentage = Decimal(str(value))
        similarity = float(percentage / Decimal("100"))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(similarity) or similarity < 0 or similarity > 1:
        return None
    return similarity


def _integer_or_none(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _event_time(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_DEVICE_TIMEZONE)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_offset_minutes(value: object) -> Optional[int]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return 480
    return int(parsed.utcoffset().total_seconds() // 60)


def _clock_times(clock: Callable[[], datetime]) -> tuple[datetime, datetime]:
    now = clock()
    if not isinstance(now, datetime):
        raise TypeError("clock must return datetime")
    aware_utc = (
        now.replace(tzinfo=timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None
        else now.astimezone(timezone.utc)
    )
    return (
        aware_utc.replace(tzinfo=None),
        aware_utc.astimezone(_DEVICE_TIMEZONE),
    )


def _text(value: object, *, maximum: int, allow_empty: bool = False) -> Optional[str]:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text and not allow_empty:
        return None
    if len(text) > maximum:
        return None
    return text


def _log_event(
    payload: dict,
    *,
    status,
    reason,
    exception: Exception | None = None,
    device_id: str | None = None,
    source: str | None = None,
    canonical_event_id: str | None = None,
) -> None:
    device_info = payload.get("deviceInfo")
    info = payload.get("info")
    serial_number = device_info.get("serialNumber") if isinstance(device_info, dict) else None
    event_id = info.get("eventId") if isinstance(info, dict) else None
    normalized = device_id is not None
    LOGGER.info(
        "device event operator=%s device_id=%s source=%s canonical_event_id=%s "
        "serial_number=%s event_id=%s status=%s reason=%s exception_type=%s",
        _log_scalar(payload.get("operator")),
        _log_scalar(device_id),
        _log_scalar(source),
        _log_scalar(canonical_event_id),
        None if normalized else _log_scalar(serial_number),
        None if normalized else _log_scalar(event_id),
        _log_scalar(status),
        _log_scalar(reason),
        _log_scalar(type(exception).__name__) if exception is not None else None,
    )


def _log_scalar(value: object) -> Optional[str]:
    if not isinstance(value, (str, int, float, bool)):
        return None
    return str(value)[:_LOG_VALUE_LIMIT]
