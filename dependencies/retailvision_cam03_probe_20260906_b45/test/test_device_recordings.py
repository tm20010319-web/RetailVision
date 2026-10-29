from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.behavior_events import EvidenceClipStore
from agent.camera_face_api import CameraApiProtocolError
from agent.schema_evolution import evolve_schema
from agent.device_recordings import (
    DeviceRecordingClient,
    EvidenceClipProcessor,
    RecordingCoverageError,
    RecordingFile,
)
from agent.models import EvidenceClip


class FakeResponse:
    def __init__(self, content=b"", status_code=200, chunks=()):
        self.content = content
        self.status_code = status_code
        self._chunks = tuple(chunks)

    def iter_content(self, chunk_size):
        return iter(self._chunks)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class FileListClient(DeviceRecordingClient):
    def __init__(self, files):
        super().__init__(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            session=FakeSession([]),
        )
        self.files = files
        self.queries = []

    def recording_files(self, *, path_type=0, start=None, end=None):
        self.queries.append((path_type, start, end))
        return list(self.files)


class FakeClipClient:
    def __init__(self, files=None, error=None):
        self.files = list(files or [])
        self.error = error
        self.downloads = []

    def files_covering(self, start, end):
        if self.error is not None:
            raise self.error
        return list(self.files)

    def download(self, recording, destination):
        self.downloads.append((recording, destination))
        destination.write_bytes(b"recording")


class FakeTrimmer:
    def __init__(self):
        self.calls = []

    def trim(self, sources, *, start, end, output):
        self.calls.append((list(sources), start, end, output))
        output.write_bytes(b"mp4")


class DeviceRecordingClientTests(unittest.TestCase):
    def test_parses_daily_recording_intervals(self):
        session = FakeSession(
            [
                FakeResponse(
                    b"""<?xml version='1.0'?>
                    <RecordInfoByDay><RecordInfoList><RecordInfo>
                    <Channel>0</Channel><RecordType>general</RecordType>
                    <StartTime>20260727100000</StartTime>
                    <EndTime>20260727100500</EndTime>
                    </RecordInfo></RecordInfoList></RecordInfoByDay>"""
                )
            ]
        )
        client = DeviceRecordingClient(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            session=session,
        )

        result = client.recordings_for_day(date(2026, 7, 27), channel=0)

        self.assertEqual(1, len(result))
        self.assertEqual(datetime(2026, 7, 27, 10, 0), result[0].start)
        self.assertEqual(datetime(2026, 7, 27, 10, 5), result[0].end)
        self.assertEqual(
            {
                "Year": 2026,
                "Month": 7,
                "Day": 27,
                "Channel": 0,
                "RecordType": 0,
            },
            session.calls[0][2]["params"],
        )

    def test_reads_all_recording_file_pages(self):
        attributes = b"""<RecordFileListAttribute>
            <RecordFileSumCount>2</RecordFileSumCount>
            <RecordFileCountPerPage>1</RecordFileCountPerPage>
            </RecordFileListAttribute>"""
        page_one = b"""<RecordFileList><RecordFile>
            <PathName>records/20260727</PathName><FileName>a.mp4</FileName>
            <StartTime>20260727 100000</StartTime><StopTime>20260727 100300</StopTime>
            <FileSize>100</FileSize></RecordFile></RecordFileList>"""
        page_two = b"""<RecordFileList><RecordFile>
            <PathName>records/20260727</PathName><FileName>b.mp4</FileName>
            <StartTime>20260727 100300</StartTime><StopTime>20260727 100600</StopTime>
            <FileSize>200</FileSize></RecordFile></RecordFileList>"""
        session = FakeSession(
            [FakeResponse(attributes), FakeResponse(page_one), FakeResponse(page_two)]
        )
        client = DeviceRecordingClient(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            session=session,
        )

        result = client.recording_files()

        self.assertEqual(["a.mp4", "b.mp4"], [item.file_name for item in result])
        self.assertTrue(session.calls[1][1].endswith("/RecordFileList/Pages/1"))
        self.assertTrue(session.calls[2][1].endswith("/RecordFileList/Pages/2"))
        self.assertEqual({"Pathtype": 0}, session.calls[1][2]["params"])

    def test_selects_requested_time_range_before_reading_pages(self):
        selected = b"""<ResponseStatus><statusCode>0</statusCode></ResponseStatus>"""
        attributes = b"""<RecordFileListAttribute>
            <RecordFileSumCount>1</RecordFileSumCount>
            <RecordFileCountPerPage>100</RecordFileCountPerPage>
            </RecordFileListAttribute>"""
        page = b"""<RecordFileList><RecordFile>
            <PathName>records/20260726</PathName><FileName>a.mp4</FileName>
            <StartTime>20260726 100000</StartTime><StopTime>20260726 100300</StopTime>
            <FileSize>100</FileSize></RecordFile></RecordFileList>"""
        session = FakeSession(
            [FakeResponse(selected), FakeResponse(attributes), FakeResponse(page)]
        )
        client = DeviceRecordingClient(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            timezone_offset_minutes=480,
            session=session,
        )
        start = datetime(2026, 7, 26, 10, 0)
        end = datetime(2026, 7, 26, 10, 5)

        result = client.recording_files(start=start, end=end)

        self.assertEqual(["a.mp4"], [item.file_name for item in result])
        self.assertEqual("PUT", session.calls[0][0])
        self.assertTrue(session.calls[0][1].endswith("/RecordFileList/Attribute"))
        payload = session.calls[0][2]["data"].decode("utf-8")
        self.assertIn("<begin_time>20260726 100000</begin_time>", payload)
        self.assertIn("<end_time>20260726 100500</end_time>", payload)
        self.assertIn("<timezone_min_between_utc>480</timezone_min_between_utc>", payload)
        self.assertEqual("GET", session.calls[1][0])
        self.assertTrue(session.calls[2][1].endswith("/RecordFileList/Pages/1"))

    def test_clients_for_same_device_share_recording_query_lock(self):
        first = DeviceRecordingClient(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            session=FakeSession([]),
        )
        second = DeviceRecordingClient(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            session=FakeSession([]),
        )

        self.assertIs(first._recording_query_lock, second._recording_query_lock)

    def test_selects_complete_overlapping_file_coverage(self):
        start = datetime(2026, 7, 27, 10, 1)
        files = [
            RecordingFile("records", "a.mp4", start - timedelta(minutes=1), start + timedelta(minutes=2), 1),
            RecordingFile("records", "b.mp4", start + timedelta(minutes=2), start + timedelta(minutes=6), 1),
        ]
        client = FileListClient(files)

        selected = client.files_covering(start, start + timedelta(minutes=5))

        self.assertEqual(files, selected)
        self.assertEqual(
            (
                0,
                datetime(2026, 7, 27, 0, 0),
                datetime(2026, 7, 27, 23, 59, 59),
            ),
            client.queries[0],
        )

    def test_rejects_missing_recording_coverage(self):
        start = datetime(2026, 7, 27, 10, 1)
        files = [
            RecordingFile("records", "a.mp4", start, start + timedelta(minutes=2), 1),
            RecordingFile("records", "b.mp4", start + timedelta(minutes=3), start + timedelta(minutes=6), 1),
        ]
        client = FileListClient(files)

        with self.assertRaisesRegex(RecordingCoverageError, "recording_coverage_missing"):
            client.files_covering(start, start + timedelta(minutes=5))

    def test_cross_midnight_coverage_queries_both_calendar_days(self):
        start = datetime(2026, 7, 26, 23, 59, 58)
        end = datetime(2026, 7, 27, 0, 0, 3)
        files = [
            RecordingFile("records", "a.mp4", start, end, 1),
        ]
        client = FileListClient(files)

        self.assertEqual(files, client.files_covering(start, end))
        self.assertEqual(
            (
                0,
                datetime(2026, 7, 26, 0, 0),
                datetime(2026, 7, 27, 23, 59, 59),
            ),
            client.queries[0],
        )

    def test_download_uses_safe_relative_device_path(self):
        session = FakeSession([FakeResponse(chunks=(b"one", b"two"))])
        client = DeviceRecordingClient(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            session=session,
        )
        recording = RecordingFile(
            "records/20260727",
            "clip 1.mp4",
            datetime(2026, 7, 27, 10),
            datetime(2026, 7, 27, 10, 5),
            6,
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "clip.mp4"
            client.download(recording, destination)

            self.assertEqual(b"onetwo", destination.read_bytes())
        self.assertEqual(
            "http://camera/records/20260727/clip%201.mp4",
            session.calls[0][1],
        )

    def test_download_rejects_path_traversal(self):
        client = DeviceRecordingClient(
            device_id="camera-1",
            base_url="http://camera",
            username="user",
            password="pass",
            session=FakeSession([]),
        )
        recording = RecordingFile(
            "../secret",
            "clip.mp4",
            datetime(2026, 7, 27, 10),
            datetime(2026, 7, 27, 10, 5),
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CameraApiProtocolError):
                client.download(recording, Path(temporary) / "clip.mp4")

    def test_scoped_client_verifies_device_fingerprint_before_recording_request(self):
        session = FakeSession([])
        client = DeviceRecordingClient(
            device_id="cam02",
            base_url="http://wrong-camera",
            username="user",
            password="pass",
            session=session,
            metadata_reader=lambda: SimpleNamespace(
                device_serial="ENTRANCE-SERIAL",
                device_model="Entrance",
                firmware_version="1.0",
            ),
            expected_device_serial="CAM02-SERIAL",
            expected_device_model="P6S-X",
            expected_firmware_version="3.4.0",
        )

        with self.assertRaisesRegex(CameraApiProtocolError, "fingerprint scope mismatch"):
            client.recordings_for_day(date(2026, 8, 19))

        self.assertEqual([], session.calls)

    def test_scoped_client_reuses_successful_fingerprint_verification(self):
        session = FakeSession([
            FakeResponse(b"<RecordInfoByDay />"),
            FakeResponse(b"<RecordInfoByDay />"),
        ])
        calls = []
        client = DeviceRecordingClient(
            device_id="cam02",
            base_url="http://cam02",
            username="user",
            password="pass",
            session=session,
            metadata_reader=lambda: calls.append("metadata") or SimpleNamespace(
                device_serial="CAM02-SERIAL",
                device_model="P6S-X",
                firmware_version="3.4.0",
            ),
            expected_device_serial="CAM02-SERIAL",
            expected_device_model="P6S-X",
            expected_firmware_version="3.4.0",
        )

        client.recordings_for_day(date(2026, 8, 19))
        client.recordings_for_day(date(2026, 8, 19))

        self.assertEqual(["metadata"], calls)


class EvidenceClipProcessorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        evolve_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.now = datetime(2026, 7, 27, 10, 0)
        self.clip = EvidenceClipStore(self.sessions).request(
            owner_type="peak_entry_window",
            owner_id="entrance-1:20260727100000",
            camera_id="entrance-1",
            requested_start=self.now,
            requested_end=self.now + timedelta(minutes=5),
        )

    def tearDown(self):
        self.engine.dispose()

    def test_process_downloads_trims_and_marks_ready(self):
        recording = RecordingFile(
            "records",
            "source.mp4",
            self.now - timedelta(minutes=1),
            self.now + timedelta(minutes=6),
            100,
        )
        client = FakeClipClient([recording])
        trimmer = FakeTrimmer()
        with tempfile.TemporaryDirectory() as temporary:
            processor = EvidenceClipProcessor(
                self.sessions,
                clients={"entrance-1": client},
                output_dir=Path(temporary),
                trimmer=trimmer,
            )

            status = processor.process(self.clip.id)

            db = self.sessions()
            try:
                row = db.get(EvidenceClip, self.clip.id)
                self.assertEqual("ready", status)
                self.assertEqual("ready", row.status)
                self.assertIsNone(row.last_error)
                self.assertTrue(Path(row.output_path).is_file())
                self.assertEqual(1, row.attempts)
            finally:
                db.close()
        self.assertEqual(1, len(client.downloads))
        self.assertEqual(1, len(trimmer.calls))

    def test_missing_coverage_is_a_stable_unavailable_state(self):
        client = FakeClipClient(error=RecordingCoverageError("detail"))
        with tempfile.TemporaryDirectory() as temporary:
            processor = EvidenceClipProcessor(
                self.sessions,
                clients={"entrance-1": client},
                output_dir=Path(temporary),
                trimmer=FakeTrimmer(),
            )

            status = processor.process(self.clip.id)

        db = self.sessions()
        try:
            row = db.get(EvidenceClip, self.clip.id)
            self.assertEqual("unavailable", status)
            self.assertEqual("recording_coverage_missing", row.last_error)
            self.assertIsNone(row.output_path)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
