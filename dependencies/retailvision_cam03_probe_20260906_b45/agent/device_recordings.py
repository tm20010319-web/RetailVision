"""P6S recording index access and precise Evidence Clip processing."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

from agent.camera_face_api import (
    CameraApiAuthenticationError,
    CameraApiProtocolError,
    CameraApiTransportError,
)
from agent.models import EvidenceClip


RECORDING_TIME_FORMAT = "%Y%m%d%H%M%S"
_RECORDING_QUERY_LOCKS: dict[str, threading.Lock] = {}
_RECORDING_QUERY_LOCKS_GUARD = threading.Lock()


class RecordingCoverageError(RuntimeError):
    pass


class MediaToolUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecordingInterval:
    channel: int
    record_type: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RecordingFile:
    path_name: str
    file_name: str
    start: datetime
    end: datetime
    size_bytes: int


@dataclass(frozen=True)
class LocalRecordingFile:
    recording: RecordingFile
    path: Path


class DeviceRecordingClient:
    def __init__(
        self,
        *,
        device_id: str,
        base_url: str,
        username: str,
        password: str,
        connect_timeout: float = 2.0,
        read_timeout: float = 30.0,
        max_retries: int = 2,
        timezone_offset_minutes: int = 480,
        recording_page_size: int = 100,
        session: Optional[requests.Session] = None,
        metadata_reader: Optional[Callable[[], Any]] = None,
        expected_device_serial: Optional[str] = None,
        expected_device_model: Optional[str] = None,
        expected_firmware_version: Optional[str] = None,
    ) -> None:
        if not all((device_id, base_url, username)) or not isinstance(password, str):
            raise ValueError("device_id, base_url and credentials are required")
        if isinstance(timezone_offset_minutes, bool) or not isinstance(
            timezone_offset_minutes, int
        ):
            raise ValueError("timezone_offset_minutes must be an integer")
        if (
            isinstance(recording_page_size, bool)
            or not isinstance(recording_page_size, int)
            or recording_page_size <= 0
        ):
            raise ValueError("recording_page_size must be a positive integer")
        self.device_id = device_id
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._timeout = (connect_timeout, read_timeout)
        self._max_retries = max(0, max_retries)
        self._timezone_offset_minutes = timezone_offset_minutes
        self._recording_page_size = recording_page_size
        self._session = session or requests.Session()
        self._recording_query_lock = _shared_recording_query_lock(self.base_url)
        self._metadata_reader = metadata_reader
        self._expected_device_serial = expected_device_serial
        self._expected_device_model = expected_device_model
        self._expected_firmware_version = expected_firmware_version
        self._device_scope_verified = False
        if any((
            expected_device_serial,
            expected_device_model,
            expected_firmware_version,
        )) and metadata_reader is None:
            raise ValueError("device fingerprint reader required for scoped recording client")

    def recordings_for_day(
        self, day: date, *, channel: int = 0, record_type: int = 0
    ) -> list[RecordingInterval]:
        self._ensure_device_scope()
        if isinstance(day, datetime) or not isinstance(day, date):
            raise ValueError("day must be a date")
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 0:
            raise ValueError("channel must be a non-negative integer")
        root = self._request_xml(
            "GET",
            "/Playback/GetRecordInfoByDay",
            params={
                "Year": day.year,
                "Month": day.month,
                "Day": day.day,
                "Channel": channel,
                "RecordType": record_type,
            },
        )
        intervals = []
        for element in _descendants(root, "RecordInfo"):
            start = _recording_time(_child_text(element, "StartTime"))
            end = _recording_time(_child_text(element, "EndTime"))
            if end < start:
                raise CameraApiProtocolError(
                    f"camera {self.device_id} returned an invalid recording interval",
                    retryable=False,
                )
            intervals.append(
                RecordingInterval(
                    channel=_integer(_child_text(element, "Channel"), "Channel"),
                    record_type=_child_text(element, "RecordType"),
                    start=start,
                    end=end,
                )
            )
        return sorted(intervals, key=lambda item: (item.start, item.end, item.channel))

    def recording_files(
        self,
        *,
        path_type: int = 0,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[RecordingFile]:
        self._ensure_device_scope()
        if path_type not in (0, 1):
            raise ValueError("path_type must be 0 or 1")
        if (start is None) != (end is None):
            raise ValueError("start and end must be provided together")
        if start is not None and end is not None and end <= start:
            raise ValueError("end must be after start")

        # The P6S file-list query is device state: select the range, then read
        # attributes and pages without allowing another query to interleave.
        with self._recording_query_lock:
            if start is not None and end is not None:
                self._select_recording_range(start, end)
            attributes = self._request_xml("GET", "/RecordFileList/Attribute")
            total = _integer(
                _child_text(attributes, "RecordFileSumCount"), "total"
            )
            per_page = _integer(
                _child_text(attributes, "RecordFileCountPerPage"), "per_page"
            )
            if total <= 0:
                return []
            if per_page <= 0:
                raise CameraApiProtocolError(
                    f"camera {self.device_id} returned an invalid recording page size",
                    retryable=False,
                )
            files = []
            for page in range(1, math.ceil(total / per_page) + 1):
                root = self._request_xml(
                    "GET",
                    f"/RecordFileList/Pages/{page}",
                    params={"Pathtype": path_type},
                )
                for element in _descendants(root, "RecordFile"):
                    file_start = _recording_time(_child_text(element, "StartTime"))
                    file_end = _recording_time(_child_text(element, "StopTime"))
                    if file_end < file_start:
                        raise CameraApiProtocolError(
                            f"camera {self.device_id} returned an invalid recording file",
                            retryable=False,
                        )
                    files.append(
                        RecordingFile(
                            path_name=_child_text(element, "PathName"),
                            file_name=_child_text(element, "FileName"),
                            start=file_start,
                            end=file_end,
                            size_bytes=_integer(
                                _child_text(element, "FileSize"), "FileSize"
                            ),
                        )
                    )
        return sorted(files, key=lambda item: (item.start, item.end, item.file_name))

    def _select_recording_range(self, start: datetime, end: datetime) -> None:
        root = ET.Element("RecordFileListAttribute")
        ET.SubElement(root, "RecordFileSumCount").text = "0"
        ET.SubElement(root, "RecordFileCountPerPage").text = str(
            self._recording_page_size
        )
        ET.SubElement(root, "begin_time").text = start.strftime("%Y%m%d %H%M%S")
        ET.SubElement(root, "end_time").text = end.strftime("%Y%m%d %H%M%S")
        ET.SubElement(root, "timezone_min_between_utc").text = str(
            self._timezone_offset_minutes
        )
        self._request(
            "PUT",
            "/RecordFileList/Attribute",
            data=ET.tostring(root, encoding="utf-8", xml_declaration=True),
            headers={"Content-Type": "application/xml"},
        )

    def files_covering(
        self,
        start: datetime,
        end: datetime,
        *,
        gap_tolerance_seconds: float = 1.0,
    ) -> list[RecordingFile]:
        if end <= start:
            raise ValueError("end must be after start")
        last_requested_day = (end - timedelta(microseconds=1)).date()
        query_start = datetime.combine(start.date(), time.min)
        query_end = datetime.combine(last_requested_day, time(23, 59, 59))
        files = [
            item
            for item in self.recording_files(
                path_type=0,
                start=query_start,
                end=query_end,
            )
            if item.start < end and item.end > start
        ]
        _assert_coverage(files, start, end, gap_tolerance_seconds)
        return files

    def download(self, recording: RecordingFile, destination: Path) -> None:
        self._ensure_device_scope()
        relative = _safe_recording_path(recording.path_name, recording.file_name)
        response = self._request("GET", f"/{quote(relative, safe='/')}", stream=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)

    def _ensure_device_scope(self) -> None:
        if self._device_scope_verified or self._metadata_reader is None:
            return
        try:
            metadata = self._metadata_reader()
        except CameraApiProtocolError:
            raise
        except Exception as exc:
            raise CameraApiProtocolError(
                f"camera {self.device_id} fingerprint unavailable",
                retryable=False,
            ) from exc
        expected = (
            ("device_serial", self._expected_device_serial),
            ("device_model", self._expected_device_model),
            ("firmware_version", self._expected_firmware_version),
        )
        if any(
            required is not None and getattr(metadata, field, None) != required
            for field, required in expected
        ):
            raise CameraApiProtocolError(
                f"camera {self.device_id} fingerprint scope mismatch",
                retryable=False,
            )
        self._device_scope_verified = True

    def _request_xml(self, method: str, path: str, **kwargs) -> ET.Element:
        response = self._request(method, path, **kwargs)
        try:
            return ET.fromstring(response.content)
        except (ET.ParseError, TypeError, ValueError) as exc:
            raise CameraApiProtocolError(
                f"camera {self.device_id} returned malformed recording XML",
                retryable=False,
            ) from exc

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    auth=self._auth,
                    timeout=self._timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if attempt < self._max_retries:
                    continue
                raise CameraApiTransportError(
                    f"camera {self.device_id} recording request failed: {type(exc).__name__}",
                    retryable=True,
                ) from exc
            if response.status_code in (401, 403):
                raise CameraApiAuthenticationError(
                    f"camera {self.device_id} authentication failed",
                    retryable=False,
                )
            if 500 <= response.status_code < 600 and attempt < self._max_retries:
                continue
            if not 200 <= response.status_code < 300:
                raise CameraApiTransportError(
                    f"camera {self.device_id} returned HTTP {response.status_code}",
                    retryable=response.status_code >= 500 or response.status_code == 429,
                )
            return response
        raise AssertionError("unreachable")


class MediaTrimmer:
    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        runner: Callable = subprocess.run,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.runner = runner

    def trim(
        self,
        sources: Iterable[LocalRecordingFile],
        *,
        start: datetime,
        end: datetime,
        output: Path,
    ) -> None:
        source_files = list(sources)
        if not source_files:
            raise RecordingCoverageError("recording_coverage_missing")
        if end <= start:
            raise ValueError("end must be after start")
        self._ensure_available()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            temp_dir = Path(temporary)
            pieces = []
            for index, source in enumerate(source_files):
                piece_start = max(start, source.recording.start)
                piece_end = min(end, source.recording.end)
                if piece_end <= piece_start:
                    continue
                piece = temp_dir / f"piece-{index:04d}.mp4"
                self._run(
                    [
                        self.ffmpeg_path,
                        "-y",
                        "-ss",
                        f"{(piece_start - source.recording.start).total_seconds():.3f}",
                        "-i",
                        str(source.path),
                        "-t",
                        f"{(piece_end - piece_start).total_seconds():.3f}",
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a?",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "23",
                        "-c:a",
                        "aac",
                        str(piece),
                    ]
                )
                pieces.append(piece)
            if not pieces:
                raise RecordingCoverageError("recording_coverage_missing")
            if len(pieces) == 1:
                os.replace(pieces[0], output)
                return
            manifest = temp_dir / "concat.txt"
            manifest.write_text(
                "".join(f"file '{_concat_path(piece)}'\n" for piece in pieces),
                encoding="utf-8",
            )
            self._run(
                [
                    self.ffmpeg_path,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(manifest),
                    "-c",
                    "copy",
                    str(output),
                ]
            )

    def _ensure_available(self) -> None:
        candidate = Path(self.ffmpeg_path)
        if candidate.is_absolute():
            available = candidate.is_file()
        else:
            available = shutil.which(self.ffmpeg_path) is not None
        if not available:
            raise MediaToolUnavailableError("ffmpeg_unavailable")

    def _run(self, command: list[str]) -> None:
        self.runner(command, check=True, capture_output=True)


class EvidenceClipProcessor:
    def __init__(
        self,
        session_factory,
        *,
        clients: dict[str, DeviceRecordingClient],
        output_dir: Path,
        trimmer: Optional[MediaTrimmer] = None,
    ) -> None:
        self.session_factory = session_factory
        self.clients = dict(clients)
        self.output_dir = Path(output_dir)
        self.trimmer = trimmer or MediaTrimmer()

    def process(self, clip_id: str) -> str:
        db = self.session_factory()
        try:
            clip = db.get(EvidenceClip, clip_id)
            if clip is None:
                raise KeyError(f"evidence clip not found: {clip_id}")
            if clip.status == "ready":
                return clip.status
            clip.status = "running"
            clip.attempts += 1
            clip.last_error = None
            db.commit()
            camera_id = clip.camera_id
            requested_start = clip.requested_start
            requested_end = clip.requested_end
        finally:
            db.close()

        client = self.clients.get(camera_id)
        if client is None:
            return self._finish(clip_id, "failed", "recording_client_unconfigured")

        try:
            recordings = client.files_covering(requested_start, requested_end)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output = self.output_dir / f"{clip_id}.mp4"
            with tempfile.TemporaryDirectory(dir=self.output_dir) as temporary:
                temp_dir = Path(temporary)
                local_files = []
                for index, recording in enumerate(recordings):
                    suffix = Path(recording.file_name).suffix or ".recording"
                    local_path = temp_dir / f"source-{index:04d}{suffix}"
                    client.download(recording, local_path)
                    local_files.append(LocalRecordingFile(recording, local_path))
                self.trimmer.trim(
                    local_files,
                    start=requested_start,
                    end=requested_end,
                    output=output,
                )
            return self._finish(clip_id, "ready", None, output_path=str(output))
        except RecordingCoverageError:
            return self._finish(clip_id, "unavailable", "recording_coverage_missing")
        except MediaToolUnavailableError:
            return self._finish(clip_id, "failed", "ffmpeg_unavailable")
        except (CameraApiTransportError, CameraApiProtocolError):
            return self._finish(clip_id, "failed", "recording_device_error")
        except (OSError, subprocess.SubprocessError):
            return self._finish(clip_id, "failed", "clip_processing_error")

    def _finish(
        self,
        clip_id: str,
        status: str,
        error_code: Optional[str],
        *,
        output_path: Optional[str] = None,
    ) -> str:
        db = self.session_factory()
        try:
            clip = db.get(EvidenceClip, clip_id)
            if clip is None:
                raise KeyError(f"evidence clip not found: {clip_id}")
            clip.status = status
            clip.last_error = error_code
            clip.output_path = output_path
            db.commit()
            return status
        finally:
            db.close()


def _assert_coverage(
    files: list[RecordingFile],
    start: datetime,
    end: datetime,
    gap_tolerance_seconds: float,
) -> None:
    if not files:
        raise RecordingCoverageError("recording_coverage_missing")
    tolerance = timedelta(seconds=gap_tolerance_seconds)
    covered_until = start
    for item in sorted(files, key=lambda value: (value.start, value.end)):
        if item.end <= covered_until:
            continue
        if item.start > covered_until + tolerance:
            raise RecordingCoverageError("recording_coverage_missing")
        covered_until = max(covered_until, item.end)
        if covered_until + tolerance >= end:
            return
    raise RecordingCoverageError("recording_coverage_missing")


def _shared_recording_query_lock(base_url: str) -> threading.Lock:
    with _RECORDING_QUERY_LOCKS_GUARD:
        lock = _RECORDING_QUERY_LOCKS.get(base_url)
        if lock is None:
            lock = threading.Lock()
            _RECORDING_QUERY_LOCKS[base_url] = lock
        return lock


def _safe_recording_path(path_name: str, file_name: str) -> str:
    path = PurePosixPath(path_name.replace("\\", "/").lstrip("/"))
    file = PurePosixPath(file_name.replace("\\", "/"))
    if file.name != str(file) or not file.name:
        raise CameraApiProtocolError("invalid recording file name", retryable=False)
    combined = path / file.name
    if ".." in combined.parts or not combined.parts:
        raise CameraApiProtocolError("invalid recording file path", retryable=False)
    return combined.as_posix()


def _recording_time(value: str) -> datetime:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) != 14:
        raise CameraApiProtocolError("invalid recording timestamp", retryable=False)
    try:
        return datetime.strptime(digits, RECORDING_TIME_FORMAT)
    except ValueError as exc:
        raise CameraApiProtocolError("invalid recording timestamp", retryable=False) from exc


def _integer(value: str, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CameraApiProtocolError(
            f"invalid recording field: {field}", retryable=False
        ) from exc


def _child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or "").strip()
    raise CameraApiProtocolError(f"missing recording field: {name}", retryable=False)


def _descendants(root: ET.Element, name: str):
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            yield element


def _concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")
