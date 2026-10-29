"""Recognition validation and artifacts for the replacement-camera test."""

import base64
import binascii
import json
import math
import re
import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path

from device_face_chain_test.protocol import event_key


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")
_CAPTURE_PREFIX = "data:image/jpeg;base64,"


@dataclass(frozen=True)
class ProcessorSettings:
    expected_serial: str
    similarity_threshold: float = 0.75
    output_dir: Path = Path("outputs/device_face_chain_test")
    save_capture: bool = False
    max_capture_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if not isinstance(self.expected_serial, str) or not self.expected_serial.strip():
            raise ValueError("expected_serial must not be blank")
        if (
            isinstance(self.similarity_threshold, bool)
            or not isinstance(self.similarity_threshold, (int, float))
            or not 0 < self.similarity_threshold <= 1
        ):
            raise ValueError("similarity_threshold must be between 0 and 1")
        if (
            isinstance(self.max_capture_bytes, bool)
            or not isinstance(self.max_capture_bytes, int)
            or self.max_capture_bytes <= 0
        ):
            raise ValueError("max_capture_bytes must be a positive integer")
        object.__setattr__(self, "output_dir", Path(self.output_dir))


@dataclass(frozen=True)
class PreparedEvent:
    payload: dict
    event_key: str
    duplicate: bool


class ChainProcessor:
    def __init__(self, resolver, settings: ProcessorSettings, clock=datetime.now):
        self._resolver = resolver
        self._settings = settings
        self._clock = clock
        self._state_lock = threading.Lock()
        self._artifact_lock = threading.Lock()
        self._event_states: dict[str, str] = {}
        self._captures: dict[str, tuple[bytes | None, str | None]] = {}

    def prepare(self, payload: dict) -> PreparedEvent:
        key = event_key(payload)
        with self._state_lock:
            duplicate = key in self._event_states
            if not duplicate:
                self._event_states[key] = "pending"
        try:
            sanitized_payload, capture = self._sanitize_payload(
                payload,
                read_capture=not duplicate,
            )
        except Exception:
            if not duplicate:
                self._release_reservation(key)
            raise
        if not duplicate:
            with self._state_lock:
                self._captures[key] = capture
        return PreparedEvent(
            payload=sanitized_payload,
            event_key=key,
            duplicate=duplicate,
        )

    def process(self, prepared: PreparedEvent) -> dict:
        if prepared.duplicate:
            result = self._base_result(prepared)
            result["status"] = "ignored"
            result["reason"] = "duplicate"
            return result

        try:
            result = self._base_result(prepared)
            payload = prepared.payload
            person_info = payload["person_info"]

            if result["serial_number"] != self._settings.expected_serial:
                self._set_outcome(result, "failed", "device_serial_mismatch")
            elif payload["match_number"] is None:
                self._set_outcome(result, "failed", "malformed_recognition")
            else:
                result["match_number"] = payload["match_number"]
                if payload["match_number"] == 0:
                    self._set_outcome(result, "waiting", "unrecognized_face")
                elif payload["match_number"] < 0:
                    self._set_outcome(result, "failed", "malformed_recognition")
                elif not isinstance(person_info, dict):
                    self._set_outcome(result, "failed", "malformed_recognition")
                else:
                    self._populate_person_fields(result, person_info)
                    similarity = self._similarity(person_info.get("match"))
                    if similarity is None:
                        self._set_outcome(result, "failed", "malformed_recognition")
                    else:
                        result["similarity"] = similarity
                        if similarity < self._settings.similarity_threshold:
                            self._set_outcome(result, "failed", "similarity_below_threshold")
                        elif not result["unique_id"]:
                            self._set_outcome(result, "failed", "malformed_recognition")
                        else:
                            self._resolve_member(result)

            self._settings.output_dir.mkdir(parents=True, exist_ok=True)
            self._save_capture(result, prepared.event_key)
            self._write_artifacts(result)
        except Exception:
            self._release_reservation(prepared.event_key)
            raise
        self._complete_reservation(prepared.event_key)
        return result

    def _complete_reservation(self, event_key: str) -> None:
        with self._state_lock:
            if self._event_states.get(event_key) == "pending":
                self._event_states[event_key] = "completed"
            self._captures.pop(event_key, None)

    def _release_reservation(self, event_key: str) -> None:
        with self._state_lock:
            if self._event_states.get(event_key) == "pending":
                self._event_states.pop(event_key, None)
            self._captures.pop(event_key, None)

    def _base_result(self, prepared: PreparedEvent) -> dict:
        payload = prepared.payload
        return {
            "status": None,
            "reason": None,
            "event_key": prepared.event_key,
            "serial_number": payload["serial_number"],
            "event_id": payload["event_id"],
            "occurred_at": payload["occurred_at"],
            "match_number": None,
            "member_id": None,
            "unique_id": None,
            "person_id": None,
            "group_id": None,
            "similarity": None,
            "duplicate": prepared.duplicate,
            "processed_at": self._clock().isoformat(),
            "capture_path": None,
            "capture_error": None,
        }

    def _sanitize_payload(
        self,
        payload: dict,
        read_capture: bool,
    ) -> tuple[dict, tuple[bytes | None, str | None]]:
        device_info = payload.get("deviceInfo")
        info = payload.get("info")
        device_info = device_info if isinstance(device_info, dict) else {}
        info = info if isinstance(info, dict) else {}
        person_info = info.get("personInfo")
        sanitized_person_info = None
        if isinstance(person_info, dict):
            sanitized_person_info = {
                "unique_id": self._text(person_info.get("uniqueId")),
                "person_id": self._scalar(person_info.get("personId")),
                "group_id": self._scalar(person_info.get("groupId")),
                "match": self._scalar(person_info.get("match")),
            }
        match_number = info.get("matchNumber")
        sanitized_payload = {
            "serial_number": self._text(device_info.get("serialNumber")),
            "event_id": self._scalar(info.get("eventId")),
            "occurred_at": self._scalar(info.get("eventTime", info.get("time"))),
            "match_number": match_number if self._is_integer(match_number) else None,
            "person_info": sanitized_person_info,
        }
        capture = self._capture_from_info(info) if read_capture else (None, None)
        return sanitized_payload, capture

    @staticmethod
    def _is_integer(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    @staticmethod
    def _text(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value
        return None

    @staticmethod
    def _scalar(value: object) -> str | int | float | bool | None:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        return None

    @staticmethod
    def _similarity(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            similarity = float(Decimal(str(value)) / Decimal("100"))
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            return None
        return similarity if math.isfinite(similarity) else None

    def _populate_person_fields(self, result: dict, person_info: dict) -> None:
        result["unique_id"] = person_info["unique_id"]
        result["person_id"] = person_info["person_id"]
        result["group_id"] = person_info["group_id"]

    def _resolve_member(self, result: dict) -> None:
        try:
            member = self._resolver.resolve(result["unique_id"])
        except Exception:
            self._set_outcome(result, "failed", "database_unavailable")
            return
        if member is None:
            self._set_outcome(result, "failed", "mapping_not_found")
            return
        result["member_id"] = self._scalar(getattr(member, "member_id", None))
        self._set_outcome(result, "passed", "passed")

    @staticmethod
    def _set_outcome(result: dict, status: str, reason: str) -> None:
        result["status"] = status
        result["reason"] = reason

    def _capture_from_info(self, info: dict) -> tuple[bytes | None, str | None]:
        if not self._settings.save_capture:
            return None, None
        capture_image = info.get("CaptureImage")
        picture = capture_image.get("picture") if isinstance(capture_image, dict) else None
        if not isinstance(picture, str) or not picture.startswith(_CAPTURE_PREFIX):
            return None, "invalid_capture"
        encoded_picture = picture[len(_CAPTURE_PREFIX) :]
        if len(encoded_picture) % 4 != 0:
            return None, "invalid_capture"
        padding = 2 if encoded_picture.endswith("==") else int(encoded_picture.endswith("="))
        decoded_size = (len(encoded_picture) // 4) * 3 - padding
        if decoded_size > self._settings.max_capture_bytes:
            return None, "capture_too_large"
        try:
            capture_bytes = base64.b64decode(encoded_picture, validate=True)
        except (binascii.Error, ValueError):
            return None, "invalid_capture"
        if len(capture_bytes) > self._settings.max_capture_bytes:
            return None, "capture_too_large"
        return capture_bytes, None

    def _save_capture(self, result: dict, event_key: str) -> None:
        if not self._settings.save_capture:
            return
        with self._state_lock:
            capture_bytes, capture_error = self._captures.pop(event_key, (None, None))
        if capture_error is not None:
            result["capture_error"] = capture_error
            return
        if capture_bytes is None:
            return

        capture_path = self._artifact_path(result, ".jpg")
        try:
            capture_path.write_bytes(capture_bytes)
        except OSError:
            result["capture_error"] = "capture_save_failed"
            return
        result["capture_path"] = str(capture_path)

    def _write_artifacts(self, result: dict) -> None:
        with self._artifact_lock:
            self._write_json(self._artifact_path(result, ".json"), result)
            self._write_json(self._settings.output_dir / "latest_result.json", result)

    def _artifact_path(self, result: dict, suffix: str) -> Path:
        serial = self._safe_filename_component(result["serial_number"])
        event_id = self._safe_filename_component(result["event_id"])
        return self._settings.output_dir / f"{serial}-{event_id}{suffix}"

    @staticmethod
    def _safe_filename_component(value: object) -> str:
        text = str(value) if value is not None else "unknown"
        return _SAFE_FILENAME.sub("_", text)

    @staticmethod
    def _write_json(path: Path, result: dict) -> None:
        temporary_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
