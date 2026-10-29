"""Trusted P6S source-video and interval-wide registration gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class P6SRecordingSource:
    device_id: str
    path_name: str
    file_name: str
    raw_started_at: datetime
    raw_ended_at: datetime
    indexed_size_bytes: int


@dataclass(frozen=True)
class TrustedSourceVideoResult:
    status: str
    source_video_id: Optional[str]
    content_sha256: Optional[str]
    indexed_size_bytes: int
    downloaded_size_bytes: Optional[int]
    reason: Optional[str]
    canonical_manifest: Optional[dict[str, object]]


class TrustedSourceVideoVerifier:
    """Seal bytes retrieved through the controlled P6S download path."""

    def verify(
        self,
        *,
        recording: P6SRecordingSource,
        downloaded_path: Path,
        controlled_download: bool,
    ) -> TrustedSourceVideoResult:
        _validate_recording(recording)
        if not controlled_download:
            return self._failure(recording, "untrusted", "source_retrieval_untrusted")
        path = Path(downloaded_path)
        if not path.is_file():
            return self._failure(recording, "unavailable", "downloaded_source_missing")
        downloaded_size = path.stat().st_size
        if downloaded_size != recording.indexed_size_bytes:
            return TrustedSourceVideoResult(
                status="conflict",
                source_video_id=None,
                content_sha256=None,
                indexed_size_bytes=recording.indexed_size_bytes,
                downloaded_size_bytes=downloaded_size,
                reason="recording_size_mismatch",
                canonical_manifest=None,
            )
        digest = _file_sha256(path)
        manifest: dict[str, object] = {
            "source_type": "p6s_recording_file",
            "device_id": recording.device_id,
            "path_name": recording.path_name,
            "file_name": recording.file_name,
            "raw_started_at": _timestamp(recording.raw_started_at),
            "raw_ended_at": _timestamp(recording.raw_ended_at),
            "indexed_size_bytes": recording.indexed_size_bytes,
            "downloaded_size_bytes": downloaded_size,
            "content_sha256": digest,
        }
        source_video_id = "p6s:v1:sha256:" + hashlib.sha256(
            _canonical_json(manifest)
        ).hexdigest()
        return TrustedSourceVideoResult(
            status="verified",
            source_video_id=source_video_id,
            content_sha256=digest,
            indexed_size_bytes=recording.indexed_size_bytes,
            downloaded_size_bytes=downloaded_size,
            reason=None,
            canonical_manifest=manifest,
        )

    def verify_analysis_input(
        self,
        *,
        trusted_source: TrustedSourceVideoResult,
        analysis_input_path: Path,
    ) -> TrustedSourceVideoResult:
        """Confirm the bytes passed to analysis still match the sealed source."""
        if trusted_source.status != "verified" or not trusted_source.content_sha256:
            return TrustedSourceVideoResult(
                status="unavailable", source_video_id=None, content_sha256=None,
                indexed_size_bytes=trusted_source.indexed_size_bytes,
                downloaded_size_bytes=None, reason="trusted_source_unavailable",
                canonical_manifest=None,
            )
        path = Path(analysis_input_path)
        if not path.is_file():
            return TrustedSourceVideoResult(
                status="unavailable", source_video_id=None, content_sha256=None,
                indexed_size_bytes=trusted_source.indexed_size_bytes,
                downloaded_size_bytes=None, reason="analysis_input_missing",
                canonical_manifest=None,
            )
        if _file_sha256(path) != trusted_source.content_sha256:
            return TrustedSourceVideoResult(
                status="modified", source_video_id=None, content_sha256=None,
                indexed_size_bytes=trusted_source.indexed_size_bytes,
                downloaded_size_bytes=path.stat().st_size,
                reason="analysis_input_hash_mismatch", canonical_manifest=None,
            )
        return trusted_source

    @staticmethod
    def _failure(
        recording: P6SRecordingSource, status: str, reason: str
    ) -> TrustedSourceVideoResult:
        return TrustedSourceVideoResult(
            status=status,
            source_video_id=None,
            content_sha256=None,
            indexed_size_bytes=recording.indexed_size_bytes,
            downloaded_size_bytes=None,
            reason=reason,
            canonical_manifest=None,
        )


@dataclass(frozen=True)
class RecordingFileSelection:
    status: str
    recording: Optional[P6SRecordingSource]
    reason: Optional[str]


class UniqueP6SRecordingFileSelector:
    """Refuse to turn an ambiguous Recording Index result into one source."""

    def select(
        self, candidates: Iterable[P6SRecordingSource]
    ) -> RecordingFileSelection:
        values = tuple(candidates)
        if not values:
            return RecordingFileSelection("unavailable", None, "recording_file_not_found")
        if len(values) != 1:
            return RecordingFileSelection("conflict", None, "recording_file_not_unique")
        _validate_recording(values[0])
        return RecordingFileSelection("verified", values[0], None)


@dataclass(frozen=True)
class HistoricalRegistrationInterval:
    effective_from: datetime
    effective_to: Optional[datetime]
    value: str


@dataclass(frozen=True)
class RegistrationScopeValidation:
    status: str
    reason: Optional[str]
    device_camera_status: str
    camera_store_status: str


class RegistrationScopeValidator:
    """Require one compatible device/camera/store scope for a whole recording."""

    def validate(
        self,
        *,
        video_started_at: datetime,
        video_ended_at: datetime,
        requested_camera_id: str,
        requested_store_id: str,
        device_camera_intervals: Iterable[HistoricalRegistrationInterval],
        camera_store_intervals: Iterable[HistoricalRegistrationInterval],
    ) -> RegistrationScopeValidation:
        if video_ended_at <= video_started_at:
            raise ValueError("video_interval_invalid")
        device = _validate_coverage(
            tuple(device_camera_intervals),
            start=video_started_at,
            end=video_ended_at,
            expected=requested_camera_id,
            changed_reason="device_camera_changed",
            mismatch_reason="device_camera_mismatch",
        )
        store = _validate_coverage(
            tuple(camera_store_intervals),
            start=video_started_at,
            end=video_ended_at,
            expected=requested_store_id,
            changed_reason="camera_store_changed",
            mismatch_reason="camera_store_mismatch",
        )
        reason = device[1] or store[1]
        return RegistrationScopeValidation(
            status="available" if reason is None else "unavailable",
            reason=reason,
            device_camera_status=device[0],
            camera_store_status=store[0],
        )


def _validate_coverage(
    intervals: tuple[HistoricalRegistrationInterval, ...],
    *,
    start: datetime,
    end: datetime,
    expected: str,
    changed_reason: str,
    mismatch_reason: str,
) -> tuple[str, Optional[str]]:
    relevant = sorted(
        (
            item
            for item in intervals
            if item.effective_from < end
            and (item.effective_to is None or item.effective_to > start)
        ),
        key=lambda item: (item.effective_from, item.effective_to or datetime.max, item.value),
    )
    if not relevant:
        return "missing", "registration_coverage_missing"
    for item in relevant:
        if item.effective_to is not None and item.effective_to <= item.effective_from:
            return "unverifiable", "registration_coverage_unverifiable"
    for index, left in enumerate(relevant):
        left_end = left.effective_to or datetime.max
        for right in relevant[index + 1:]:
            if right.effective_from >= left_end:
                break
            if right.value != left.value:
                return "conflict", "registration_conflict"
    if any(item.value != expected for item in relevant):
        values = {item.value for item in relevant}
        reason = mismatch_reason if len(values) == 1 else changed_reason
        return "mismatch", reason

    cursor = start
    for item in relevant:
        interval_start = max(start, item.effective_from)
        interval_end = min(end, item.effective_to or end)
        if interval_start > cursor:
            return "missing", "registration_coverage_missing"
        if interval_end > cursor:
            cursor = interval_end
        if cursor >= end:
            return "covered", None
    return "missing", "registration_coverage_missing"


def _validate_recording(recording: P6SRecordingSource) -> None:
    if not all((recording.device_id, recording.path_name, recording.file_name)):
        raise ValueError("recording_identity_invalid")
    if recording.raw_ended_at <= recording.raw_started_at:
        raise ValueError("recording_interval_invalid")
    if isinstance(recording.indexed_size_bytes, bool) or recording.indexed_size_bytes < 0:
        raise ValueError("recording_size_invalid")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is not None:
        raise ValueError("recording_time_must_be_naive")
    return value.isoformat(timespec="microseconds")


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
