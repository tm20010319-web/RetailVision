"""Bounded, atomic storage for camera-provided JPEG captures."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


_CAPTURE_PREFIXES = (
    "data:image/jpeg;base64,",
    "data:image/jpg;base64,",
)
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}
_JPEG_STANDALONE_MARKERS = {0x01, *range(0xD0, 0xD8)}
_MAX_JPEG_HEADER_SEGMENTS = 256


@dataclass(frozen=True)
class CaptureSaveResult:
    path: str
    created: bool
    sha256: str
    width: int
    height: int


class DeviceCaptureStore:
    def __init__(
        self,
        output_dir: str | Path,
        *,
        max_bytes: int,
        max_pixels: int = 12_000_000,
    ) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if (
            isinstance(max_pixels, bool)
            or not isinstance(max_pixels, int)
            or max_pixels <= 0
        ):
            raise ValueError("max_pixels must be a positive integer")
        self.output_dir = Path(output_dir)
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels

    def save_data_uri(
        self,
        device_id: object,
        event_id: object,
        data_uri: object,
    ) -> Optional[CaptureSaveResult]:
        capture_bytes = self._decode(data_uri)
        if capture_bytes is None:
            return None

        return self.save_bytes(device_id, event_id, capture_bytes)

    def decode_data_uri(self, data_uri: object) -> Optional[bytes]:
        """Decode and validate a camera data URI for a transport adapter."""
        return self._decode(data_uri)

    def save_bytes(
        self,
        device_id: object,
        event_id: object,
        capture_bytes: bytes,
    ) -> Optional[CaptureSaveResult]:
        """Persist already-decoded JPEG bytes with the same safety checks."""
        dimensions = self._jpeg_dimensions(capture_bytes)
        if dimensions is None or not self._validate_bytes(capture_bytes):
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._capture_id(device_id, event_id)}-{uuid.uuid4().hex}.jpg"
        target = self.output_dir / filename
        temporary = self.output_dir / f"{filename}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(capture_bytes)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                return None
        except OSError:
            return None
        finally:
            temporary.unlink(missing_ok=True)
        width, height = dimensions
        return CaptureSaveResult(
            path=str(target),
            created=True,
            sha256=hashlib.sha256(capture_bytes).hexdigest(),
            width=width,
            height=height,
        )

    def remove_if_created(self, result: Optional[CaptureSaveResult]) -> None:
        if result is None or not result.created:
            return
        self.remove_path(result.path)

    def remove_path(self, value: str | Path) -> bool:
        path = Path(value)
        try:
            if path.resolve().parent != self.output_dir.resolve():
                return False
            path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _decode(self, data_uri: object) -> Optional[bytes]:
        if not isinstance(data_uri, str):
            return None
        prefix = next(
            (candidate for candidate in _CAPTURE_PREFIXES if data_uri.startswith(candidate)),
            None,
        )
        if prefix is None:
            return None
        encoded = data_uri[len(prefix) :]
        maximum_encoded_length = ((self.max_bytes + 2) // 3) * 4
        if not encoded or len(encoded) > maximum_encoded_length:
            return None
        try:
            capture_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
        if len(capture_bytes) > self.max_bytes:
            return None
        if not self._validate_bytes(capture_bytes):
            return None
        return capture_bytes

    def _validate_bytes(self, capture_bytes: object) -> bool:
        if not isinstance(capture_bytes, bytes):
            return False
        if len(capture_bytes) > self.max_bytes:
            return False
        dimensions = self._jpeg_dimensions(capture_bytes)
        if dimensions is None:
            return False
        width, height = dimensions
        if width * height > self.max_pixels:
            return False
        encoded_image = np.frombuffer(capture_bytes, dtype=np.uint8)
        image = cv2.imdecode(encoded_image, cv2.IMREAD_UNCHANGED)
        if image is None or image.ndim < 2:
            return False
        decoded_height, decoded_width = image.shape[:2]
        if decoded_height != height or decoded_width != width:
            return False
        return True

    @staticmethod
    def _jpeg_dimensions(capture_bytes: bytes) -> Optional[tuple[int, int]]:
        if (
            len(capture_bytes) < 4
            or capture_bytes[:2] != b"\xff\xd8"
            or capture_bytes[-2:] != b"\xff\xd9"
        ):
            return None

        position = 2
        segment_count = 0
        while position < len(capture_bytes):
            if capture_bytes[position] != 0xFF:
                return None
            while position < len(capture_bytes) and capture_bytes[position] == 0xFF:
                position += 1
            if position >= len(capture_bytes):
                return None

            marker = capture_bytes[position]
            position += 1
            if marker in (0x00, 0xD8, 0xD9):
                return None
            if marker in _JPEG_STANDALONE_MARKERS:
                continue
            if position + 2 > len(capture_bytes):
                return None

            segment_length = int.from_bytes(
                capture_bytes[position : position + 2], "big"
            )
            if segment_length < 2:
                return None
            segment_start = position + 2
            segment_end = position + segment_length
            if segment_end > len(capture_bytes):
                return None

            segment_count += 1
            if segment_count > _MAX_JPEG_HEADER_SEGMENTS:
                return None
            if marker in _JPEG_SOF_MARKERS:
                if segment_length < 8:
                    return None
                height = int.from_bytes(
                    capture_bytes[segment_start + 1 : segment_start + 3], "big"
                )
                width = int.from_bytes(
                    capture_bytes[segment_start + 3 : segment_start + 5], "big"
                )
                component_count = capture_bytes[segment_start + 5]
                if (
                    height <= 0
                    or width <= 0
                    or component_count <= 0
                    or segment_length < 8 + 3 * component_count
                ):
                    return None
                return width, height
            if marker == 0xDA:
                return None
            position = segment_end
        return None

    @staticmethod
    def _capture_id(device_id: object, event_id: object) -> str:
        material = f"{device_id}\0{event_id}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()
