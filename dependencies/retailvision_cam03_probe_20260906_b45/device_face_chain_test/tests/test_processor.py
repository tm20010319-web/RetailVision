import base64
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from device_face_chain_test.processor import ChainProcessor, ProcessorSettings


OUTPUT_ROOT = Path("outputs/device_face_chain_test_tests")
FIXED_NOW = datetime(2026, 7, 20, 13, 25, 0)
CAPTURE_BYTES = b"\xff\xd8replacement-camera-capture\xff\xd9"
RESULT_FIELDS = {
    "status",
    "reason",
    "event_key",
    "serial_number",
    "event_id",
    "occurred_at",
    "match_number",
    "member_id",
    "unique_id",
    "person_id",
    "group_id",
    "similarity",
    "duplicate",
    "processed_at",
    "capture_path",
    "capture_error",
}


class FakeResolver:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def resolve(self, unique_id):
        self.calls.append(unique_id)
        if self.error is not None:
            raise self.error
        return self.result


class PrepareBarrierPayload(dict):
    """Pause each thread after the unguarded deduplication check."""

    def __init__(self, *args, barrier, **kwargs):
        super().__init__(*args, **kwargs)
        self._barrier = barrier
        self._get_counts = threading.local()

    def get(self, key, default=None):
        count = getattr(self._get_counts, "device_info_gets", 0)
        if key == "deviceInfo":
            count += 1
            self._get_counts.device_info_gets = count
            if count == 2:
                self._barrier.wait(timeout=5)
        return super().get(key, default)


def face_reco(event_id=101, **info_overrides):
    person_info = {
        "uniqueId": "uid-001",
        "personId": "person-42",
        "groupId": "group-7",
        "match": 88.083099,
        "faceFeature": "sensitive-feature-text",
        "name": "Sensitive Name",
    }
    info = {
        "eventId": event_id,
        "eventTime": "2026-07-20T13:24:59+08:00",
        "matchNumber": 1,
        "personInfo": person_info,
        "CaptureImage": {
            "picture": "data:image/jpeg;base64,"
            + base64.b64encode(CAPTURE_BYTES).decode("ascii"),
        },
    }
    info.update(info_overrides)
    return {
        "operator": "FaceReco",
        "deviceInfo": {
            "serialNumber": "P6S/001",
            "ip": "192.0.2.42",
            "mac": "00:11:22:33:44:55",
        },
        "info": info,
    }


class ProcessorTests(unittest.TestCase):
    def settings(self, **overrides):
        values = {
            "expected_serial": "P6S/001",
            "similarity_threshold": 0.75,
            "output_dir": OUTPUT_ROOT / self._testMethodName,
        }
        values.update(overrides)
        return ProcessorSettings(**values)

    def process(self, payload, resolver=None, settings=None):
        processor = ChainProcessor(
            resolver or FakeResolver(SimpleNamespace(member_id="member-17")),
            settings or self.settings(),
            clock=lambda: FIXED_NOW,
        )
        return processor, processor.process(processor.prepare(payload))

    def test_settings_validation_and_default_threshold(self):
        defaults = ProcessorSettings(
            expected_serial="P6S/001",
            output_dir=OUTPUT_ROOT / self._testMethodName,
        )

        self.assertEqual(defaults.similarity_threshold, 0.75)
        self.assertIsInstance(defaults.output_dir, Path)
        for value in ("", "   "):
            with self.subTest(expected_serial=value):
                with self.assertRaises(ValueError):
                    self.settings(expected_serial=value)
        for value in (0, -0.1, 1.01, "0.75"):
            with self.subTest(similarity_threshold=value):
                with self.assertRaises(ValueError):
                    self.settings(similarity_threshold=value)
        for value in (0, -1, True, False, 1.2):
            with self.subTest(max_capture_bytes=value):
                with self.assertRaises(ValueError):
                    self.settings(max_capture_bytes=value)

    def test_prepare_keeps_only_sanitized_event_values(self):
        processor = ChainProcessor(
            FakeResolver(SimpleNamespace(member_id="member-17")),
            self.settings(save_capture=True),
            clock=lambda: FIXED_NOW,
        )

        prepared = processor.prepare(face_reco())

        self.assertEqual(
            prepared.payload.get("serial_number"),
            "P6S/001",
        )
        self.assertEqual(
            prepared.payload.get("person_info", {}).get("unique_id"),
            "uid-001",
        )
        self.assertNotIn("CaptureImage", prepared.payload)
        self.assertNotIn("faceFeature", json.dumps(prepared.payload, ensure_ascii=False))
        self.assertNotIn("sensitive-feature-text", json.dumps(prepared.payload, ensure_ascii=False))

    def test_success_converts_similarity_maps_member_and_writes_sanitized_artifacts(self):
        _, result = self.process(face_reco())

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["reason"], "passed")
        self.assertEqual(result["member_id"], "member-17")
        self.assertEqual(result["similarity"], 0.88083099)
        self.assertEqual(result["occurred_at"], "2026-07-20T13:24:59+08:00")
        self.assertEqual(result["processed_at"], FIXED_NOW.isoformat())
        self.assertEqual(set(result), RESULT_FIELDS)

        artifact = self.settings().output_dir / "P6S_001-101.json"
        latest = self.settings().output_dir / "latest_result.json"
        self.assertTrue(artifact.is_file())
        self.assertTrue(latest.is_file())
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), result)
        artifact_text = artifact.read_text(encoding="utf-8")
        self.assertNotIn("picture", artifact_text)
        self.assertNotIn("faceFeature", artifact_text)
        self.assertNotIn("sensitive-feature-text", artifact_text)
        self.assertNotIn("raw payload", artifact_text)
        self.assertNotIn("192.0.2.42", artifact_text)

    def test_serial_mismatch_fails_without_resolver(self):
        resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
        payload = face_reco()
        payload["deviceInfo"]["serialNumber"] = "OTHER-001"

        _, result = self.process(payload, resolver)

        self.assertEqual((result["status"], result["reason"]), ("failed", "device_serial_mismatch"))
        self.assertEqual(resolver.calls, [])

    def test_unknown_face_waits_without_resolver(self):
        resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
        payload = face_reco(matchNumber=0)

        _, result = self.process(payload, resolver)

        self.assertEqual((result["status"], result["reason"]), ("waiting", "unrecognized_face"))
        self.assertEqual(resolver.calls, [])

    def test_malformed_recognition_fields_fail_without_resolver(self):
        cases = {
            "match_count": {"matchNumber": "one"},
            "negative_match_count": {"matchNumber": -1},
            "person_info": {"personInfo": "not an object"},
            "match": {"personInfo": {"uniqueId": "uid-001", "match": "unknown"}},
            "unique_id": {"personInfo": {"match": 88.083099}},
        }
        for index, (name, overrides) in enumerate(cases.items(), start=1):
            with self.subTest(name=name):
                resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
                payload = face_reco(event_id=110 + index, **overrides)
                _, result = self.process(payload, resolver)
                self.assertEqual((result["status"], result["reason"]), ("failed", "malformed_recognition"))
                self.assertEqual(resolver.calls, [])

    def test_below_threshold_fails_without_resolver(self):
        resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
        payload = face_reco(personInfo={"uniqueId": "uid-001", "match": 74.999})

        _, result = self.process(payload, resolver)

        self.assertEqual((result["status"], result["reason"]), ("failed", "similarity_below_threshold"))
        self.assertEqual(result["similarity"], 0.74999)
        self.assertEqual(resolver.calls, [])

    def test_missing_mapping_and_resolver_error_have_distinct_failures(self):
        _, missing = self.process(face_reco(event_id=120), FakeResolver())
        _, unavailable = self.process(
            face_reco(event_id=121),
            FakeResolver(error=RuntimeError("database offline")),
        )

        self.assertEqual((missing["status"], missing["reason"]), ("failed", "mapping_not_found"))
        self.assertEqual(
            (unavailable["status"], unavailable["reason"]),
            ("failed", "database_unavailable"),
        )

    def test_duplicate_skips_resolver_and_preserves_artifacts(self):
        resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
        settings = self.settings()
        processor = ChainProcessor(resolver, settings, clock=lambda: FIXED_NOW)
        payload = face_reco(event_id=130)

        first = processor.process(processor.prepare(payload))
        event_artifact = settings.output_dir / "P6S_001-130.json"
        latest_artifact = settings.output_dir / "latest_result.json"
        event_bytes = event_artifact.read_bytes()
        latest_bytes = latest_artifact.read_bytes()
        sentinel_mtime_ns = 1_234_567_890_000_000_000
        event_artifact.touch()
        os.utime(event_artifact, ns=(sentinel_mtime_ns, sentinel_mtime_ns))

        duplicate = processor.process(processor.prepare(payload))

        self.assertEqual(first["status"], "passed")
        self.assertEqual((duplicate["status"], duplicate["reason"]), ("ignored", "duplicate"))
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(resolver.calls, ["uid-001"])
        self.assertEqual(event_artifact.read_bytes(), event_bytes)
        self.assertEqual(event_artifact.stat().st_mtime_ns, sentinel_mtime_ns)
        self.assertEqual(latest_artifact.read_bytes(), latest_bytes)

    def test_concurrent_same_event_is_reserved_once_and_processed_once(self):
        resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
        settings = self.settings()
        processor = ChainProcessor(resolver, settings, clock=lambda: FIXED_NOW)
        payload = PrepareBarrierPayload(
            face_reco(event_id=201),
            barrier=threading.Barrier(2),
        )
        start = threading.Barrier(3)
        results = []
        errors = []

        def prepare_and_process():
            try:
                start.wait(timeout=5)
                prepared = processor.prepare(payload)
                results.append(processor.process(prepared))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=prepare_and_process) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual([result["status"] for result in results].count("passed"), 1)
        self.assertEqual([result["status"] for result in results].count("ignored"), 1)
        self.assertEqual(resolver.calls, ["uid-001"])
        self.assertEqual(
            json.loads((settings.output_dir / "P6S_001-201.json").read_text(encoding="utf-8"))["status"],
            "passed",
        )

    def test_artifact_failure_releases_pending_reservation_for_retry(self):
        resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
        settings = self.settings(save_capture=True)
        processor = ChainProcessor(resolver, settings, clock=lambda: FIXED_NOW)
        payload = face_reco(event_id=202)
        prepared = processor.prepare(payload)

        with patch("device_face_chain_test.processor.Path.mkdir", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                processor.process(prepared)

        retry = processor.prepare(payload)
        result = processor.process(retry)

        self.assertFalse(retry.duplicate)
        self.assertEqual((result["status"], result["reason"]), ("passed", "passed"))
        self.assertEqual(resolver.calls, ["uid-001", "uid-001"])
        self.assertEqual(
            (settings.output_dir / "P6S_001-202.jpg").read_bytes(),
            CAPTURE_BYTES,
        )

    def test_result_construction_failure_releases_pending_reservation_for_retry(self):
        class FailingOnceClock:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("clock unavailable")
                return FIXED_NOW

        resolver = FakeResolver(SimpleNamespace(member_id="member-17"))
        settings = self.settings(save_capture=True)
        clock = FailingOnceClock()
        processor = ChainProcessor(resolver, settings, clock=clock)
        payload = face_reco(event_id=204)

        with self.assertRaisesRegex(RuntimeError, "clock unavailable"):
            processor.process(processor.prepare(payload))

        retry = processor.prepare(payload)
        result = processor.process(retry)

        self.assertFalse(retry.duplicate)
        self.assertEqual((result["status"], result["reason"]), ("passed", "passed"))
        self.assertEqual(resolver.calls, ["uid-001"])
        self.assertEqual(
            (settings.output_dir / "P6S_001-204.jpg").read_bytes(),
            CAPTURE_BYTES,
        )

    def test_normal_validation_failure_completes_reservation(self):
        processor = ChainProcessor(
            FakeResolver(SimpleNamespace(member_id="member-17")),
            self.settings(),
            clock=lambda: FIXED_NOW,
        )
        payload = face_reco(event_id=203, matchNumber="malformed")

        first = processor.process(processor.prepare(payload))
        duplicate = processor.prepare(payload)

        self.assertEqual((first["status"], first["reason"]), ("failed", "malformed_recognition"))
        self.assertTrue(duplicate.duplicate)

    def test_concurrent_different_events_write_valid_artifacts_without_temp_collision(self):
        settings = self.settings()
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "P6S_001-211.json",
            "P6S_001-212.json",
            "latest_result.json",
            "latest_result.json.tmp",
        ):
            (settings.output_dir / name).unlink(missing_ok=True)

        processor = ChainProcessor(
            FakeResolver(SimpleNamespace(member_id="member-17")),
            settings,
            clock=lambda: FIXED_NOW,
        )
        before_write = threading.Barrier(2)
        after_write = threading.Barrier(2)
        original_write_text = Path.write_text
        results = []
        errors = []

        def wait_for_peer(barrier):
            try:
                barrier.wait(timeout=0.1)
            except threading.BrokenBarrierError:
                pass

        def coordinated_write(path, *args, **kwargs):
            if path.name.startswith("latest_result.json") and path.suffix == ".tmp":
                wait_for_peer(before_write)
                written = original_write_text(path, *args, **kwargs)
                wait_for_peer(after_write)
                return written
            return original_write_text(path, *args, **kwargs)

        def process_event(event_id):
            try:
                results.append(processor.process(processor.prepare(face_reco(event_id=event_id))))
            except Exception as error:
                errors.append(error)

        with patch.object(Path, "write_text", new=coordinated_write):
            threads = [threading.Thread(target=process_event, args=(event_id,)) for event_id in (211, 212)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual([result["status"] for result in results], ["passed", "passed"])
        for event_id in (211, 212):
            artifact = settings.output_dir / f"P6S_001-{event_id}.json"
            self.assertEqual(json.loads(artifact.read_text(encoding="utf-8"))["event_id"], event_id)
        latest = json.loads((settings.output_dir / "latest_result.json").read_text(encoding="utf-8"))
        self.assertIn(latest["event_id"], (211, 212))
        self.assertEqual(list(settings.output_dir.glob("*.tmp")), [])

    def test_valid_jpeg_capture_is_saved_when_enabled(self):
        settings = self.settings(save_capture=True)

        _, result = self.process(face_reco(event_id=140), settings=settings)

        capture = settings.output_dir / "P6S_001-140.jpg"
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["capture_error"], None)
        self.assertEqual(result["capture_path"], str(capture))
        self.assertEqual(capture.read_bytes(), CAPTURE_BYTES)

    def test_invalid_capture_does_not_change_successful_result(self):
        settings = self.settings(save_capture=True)
        payload = face_reco(
            event_id=150,
            CaptureImage={"picture": "data:image/jpeg;base64,not-valid-base64!"},
        )

        _, result = self.process(payload, settings=settings)

        self.assertEqual((result["status"], result["reason"]), ("passed", "passed"))
        self.assertEqual(result["capture_error"], "invalid_capture")
        self.assertIsNone(result["capture_path"])

    def test_oversized_capture_does_not_change_successful_result(self):
        settings = self.settings(save_capture=True, max_capture_bytes=len(CAPTURE_BYTES) - 1)

        _, result = self.process(face_reco(event_id=160), settings=settings)

        self.assertEqual((result["status"], result["reason"]), ("passed", "passed"))
        self.assertEqual(result["capture_error"], "capture_too_large")
        self.assertIsNone(result["capture_path"])

    def test_oversized_capture_is_rejected_before_base64_decode(self):
        settings = self.settings(save_capture=True, max_capture_bytes=1)

        with patch(
            "device_face_chain_test.processor.base64.b64decode",
            side_effect=AssertionError("oversized capture must not be decoded"),
        ) as decode:
            _, result = self.process(face_reco(event_id=161), settings=settings)

        decode.assert_not_called()
        self.assertEqual((result["status"], result["reason"]), ("passed", "passed"))
        self.assertEqual(result["capture_error"], "capture_too_large")
        self.assertIsNone(result["capture_path"])


if __name__ == "__main__":
    unittest.main()
