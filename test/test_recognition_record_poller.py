from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime, timedelta

from agent.camera_face_api import CameraApiAuthenticationError, CameraApiTransportError
from agent.device_recognition_intake import IntakeReceipt
from agent.device_identity import (
    DeviceMemberMappingData,
    InMemoryDeviceIdentityRepository,
    RecognitionRecordPoller,
)


NOW = datetime(2026, 7, 14, 12, 0, 10)


def record(
    log_id,
    second=9,
    *,
    group_id=2,
    person_id=19,
    unique_id="vendor-person-19",
    pass_code=0,
    similarity=0.83,
    rule_id=1,
    reco_valid=True,
):
    return {
        "log_id": log_id,
        "group_id": group_id,
        "person_id": person_id,
        "unique_id": unique_id,
        "occurred_at": datetime(2026, 7, 14, 12, 0, second),
        "pass_code": pass_code,
        "similarity": similarity,
        "rule_id": rule_id,
        "reco_valid": reco_valid,
    }


class FakeClient:
    device_id = "front-door"

    def __init__(self, pages=None, error=None):
        self.pages = pages or {1: []}
        self.error = error
        self.calls = []

    def query_recognition_records(self, start_time, stop_time, **kwargs):
        self.calls.append((start_time, stop_time, kwargs))
        if self.error:
            raise self.error
        return list(self.pages.get(kwargs["page_num"], []))


class FakeIngestor:
    def __init__(self):
        self.candidates = []

    def receive(self, candidate):
        self.candidates.append(candidate)
        return IntakeReceipt(status="received", canonical_event_id="inbox-1")


class FailingIngestor(FakeIngestor):
    def receive(self, candidate):
        raise RuntimeError("database unavailable: INTAKE-SECRET")


class RecognitionRecordPollerTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryDeviceIdentityRepository()
        self.repository.save_mapping(
            DeviceMemberMappingData(
                device_id="front-door",
                member_id="member-19",
                group_id=2,
                group_uuid="group-uuid",
                person_id=19,
                unique_id="vendor-person-19",
                face_uuid="face-uuid",
                sync_status="synced",
                member_active=True,
            )
        )
        self.ingestor = FakeIngestor()

    def create_poller(self, client, **kwargs):
        ingestor = kwargs.pop("ingestor", self.ingestor)
        return RecognitionRecordPoller(
            client=client,
            repository=self.repository,
            intake=ingestor,
            clock=lambda: NOW,
            **kwargs,
        )

    def test_poller_requires_recognition_intake(self):
        with self.assertRaisesRegex(ValueError, "intake is required"):
            RecognitionRecordPoller(
                client=FakeClient(),
                repository=self.repository,
                intake=None,
                clock=lambda: NOW,
            )

    def test_first_poll_only_looks_back_five_seconds_and_pages_before_cursor(self):
        client = FakeClient(pages={1: [record(1)], 2: [record(2)], 3: []})
        poller = self.create_poller(client, page_size=1)

        results = poller.poll_once()

        self.assertEqual(["received", "received"], [result.status for result in results])
        self.assertEqual(
            ["1:2026-07-14T12:00:09", "2:2026-07-14T12:00:09"],
            [item.source_event_id for item in self.ingestor.candidates],
        )
        self.assertEqual(3, len(client.calls))
        self.assertEqual(NOW - timedelta(seconds=5), client.calls[0][0])
        self.assertEqual([1, 2, 3], [call[2]["page_num"] for call in client.calls])
        cursor = self.repository.get_cursor("front-door")
        self.assertEqual(NOW, cursor.last_record_time)
        self.assertEqual(2, cursor.last_log_id)

    def test_poller_submits_poll_candidate_without_legacy_processing(self):
        ingestor = FakeIngestor()
        poller = self.create_poller(FakeClient(pages={1: [record(7)]}), ingestor=ingestor)

        results = poller.poll_once()

        self.assertEqual(["received"], [result.status for result in results])
        self.assertEqual(1, len(ingestor.candidates))
        candidate = ingestor.candidates[0]
        self.assertEqual("front-door", candidate.device_id)
        self.assertEqual("poll", candidate.source)
        self.assertEqual("7:2026-07-14T12:00:09", candidate.source_event_id)
        self.assertEqual("vendor-person-19", candidate.unique_id)

    def test_poller_submits_ineligible_records_to_intake(self):
        ingestor = FakeIngestor()
        records = [
            record(1, reco_valid=False),
            record(2, pass_code=1),
            record(3, rule_id=2),
            record(4, similarity=0.74),
        ]

        self.create_poller(
            FakeClient(pages={1: records}), ingestor=ingestor
        ).poll_once()

        self.assertEqual(4, len(ingestor.candidates))
        self.assertFalse(ingestor.candidates[0].reco_valid)
        self.assertEqual(1, ingestor.candidates[1].pass_code)
        self.assertEqual(2, ingestor.candidates[2].rule_id)
        self.assertEqual(0.74, ingestor.candidates[3].similarity)

    def test_poller_default_interval_is_thirty_seconds(self):
        poller = self.create_poller(FakeClient())

        self.assertEqual(30.0, poller.poll_interval_seconds)

    def test_overlap_window_submits_log_id_reset_records_to_intake(self):
        first_client = FakeClient(pages={1: [record(7)]})
        self.create_poller(first_client).poll_once()
        reset_record = record(0, second=10, unique_id="vendor-person-19")
        second_client = FakeClient(pages={1: [record(7), reset_record]})

        results = self.create_poller(second_client).poll_once()

        self.assertEqual(2, len(results))
        self.assertEqual(
            [
                "7:2026-07-14T12:00:09",
                "7:2026-07-14T12:00:09",
                "0:2026-07-14T12:00:10",
            ],
            [item.source_event_id for item in self.ingestor.candidates],
        )
        self.assertEqual(NOW - timedelta(seconds=5), second_client.calls[0][0])
        self.assertEqual(0, self.repository.get_cursor("front-door").last_log_id)

    def test_same_member_records_are_both_submitted_to_intake(self):
        client = FakeClient(pages={1: [record(1, second=8), record(2, second=9)]})

        results = self.create_poller(client).poll_once()

        self.assertEqual(2, len(results))
        self.assertEqual(
            ["1:2026-07-14T12:00:08", "2:2026-07-14T12:00:09"],
            [item.source_event_id for item in self.ingestor.candidates],
        )

    def test_invalid_or_unmapped_records_are_submitted_to_intake(self):
        invalid_records = [
            record(1, pass_code=1),
            record(2, reco_valid=False),
            record(3, rule_id=2),
            record(4, similarity=0.74),
            record(5, person_id=999, unique_id="missing"),
        ]
        client = FakeClient(pages={1: invalid_records})

        results = self.create_poller(client).poll_once()

        self.assertEqual(5, len(results))
        self.assertEqual(5, len(self.ingestor.candidates))

    def test_inactive_member_or_mapping_is_not_emitted(self):
        self.repository.save_mapping(
            DeviceMemberMappingData(
                device_id="front-door",
                member_id="disabled-member",
                group_id=4,
                group_uuid="g2",
                person_id=44,
                unique_id="disabled",
                face_uuid="f2",
                sync_status="disabled",
                member_active=True,
            )
        )
        self.repository.save_mapping(
            DeviceMemberMappingData(
                device_id="front-door",
                member_id="inactive-member",
                group_id=5,
                group_uuid="g3",
                person_id=55,
                unique_id="inactive",
                face_uuid="f3",
                sync_status="synced",
                member_active=False,
            )
        )
        client = FakeClient(
            pages={
                1: [
                    record(6, group_id=4, person_id=44, unique_id="disabled"),
                    record(7, group_id=5, person_id=55, unique_id="inactive"),
                ]
            }
        )

        self.assertEqual(2, len(self.create_poller(client).poll_once()))
        self.assertEqual(2, len(self.ingestor.candidates))

    def test_cursor_only_advances_after_all_pages_succeed(self):
        class FailOnSecondPage(FakeClient):
            def query_recognition_records(self, start_time, stop_time, **kwargs):
                if kwargs["page_num"] == 2:
                    raise CameraApiTransportError("offline", retryable=True)
                return super().query_recognition_records(start_time, stop_time, **kwargs)

        poller = self.create_poller(FailOnSecondPage(pages={1: [record(1)]}), page_size=1)

        self.assertEqual([], poller.poll_once())
        self.assertIsNone(self.repository.get_cursor("front-door"))

    def test_new_path_does_not_advance_cursor_when_later_page_fails(self):
        class FailOnSecondPage(FakeClient):
            def query_recognition_records(self, start_time, stop_time, **kwargs):
                if kwargs["page_num"] == 2:
                    raise CameraApiTransportError("offline", retryable=True)
                return super().query_recognition_records(start_time, stop_time, **kwargs)

        ingestor = FakeIngestor()
        poller = self.create_poller(
            FailOnSecondPage(pages={1: [record(1)]}),
            ingestor=ingestor,
            page_size=1,
        )

        self.assertEqual([], poller.poll_once())
        self.assertEqual([], ingestor.candidates)
        self.assertIsNone(self.repository.get_cursor("front-door"))

    def test_intake_failure_does_not_advance_cursor_or_escape(self):
        poller = self.create_poller(
            FakeClient(pages={1: [record(1)]}),
            ingestor=FailingIngestor(),
        )

        with self.assertLogs("agent.device_identity", level="WARNING") as captured:
            results = poller.poll_once()

        self.assertEqual([], results)
        self.assertIsNone(self.repository.get_cursor("front-door"))
        self.assertEqual(1, poller._consecutive_failures)
        self.assertNotIn("INTAKE-SECRET", "\n".join(captured.output))

    def test_three_failures_mark_device_unhealthy_and_success_recovers(self):
        client = FakeClient(error=CameraApiTransportError("offline", retryable=True))
        health = []
        poller = RecognitionRecordPoller(
            client=client,
            repository=self.repository,
            intake=self.ingestor,
            on_health_change=lambda device_id, healthy, reason: health.append(
                (device_id, healthy, reason)
            ),
            clock=lambda: NOW,
        )

        poller.poll_once()
        poller.poll_once()
        poller.poll_once()
        self.assertFalse(poller.healthy)
        self.assertEqual(("front-door", False, "request_failed"), health[-1])

        client.error = None
        client.pages = {1: []}
        poller.poll_once()
        self.assertTrue(poller.healthy)
        self.assertEqual(("front-door", True, "recovered"), health[-1])

    def test_authentication_failure_suspends_fast_polling(self):
        client = FakeClient(
            error=CameraApiAuthenticationError("bad credentials", retryable=False)
        )
        poller = self.create_poller(client)

        poller.poll_once()
        poller.poll_once()

        self.assertEqual(1, len(client.calls))
        self.assertFalse(poller.healthy)
        self.assertTrue(poller.authentication_suspended)

    def test_authentication_failure_logs_once_without_unrelated_secret(self):
        client = FakeClient(
            error=CameraApiAuthenticationError(
                "camera front-door authentication failed", retryable=False
            )
        )
        client.password = "AUTH-LOG-SECRET"
        poller = self.create_poller(client)

        with self.assertLogs("agent.device_identity", level="WARNING") as captured:
            poller.poll_once()
            poller.poll_once()

        self.assertEqual(1, len(captured.output))
        log_output = captured.output[0]
        self.assertIn("front-door", log_output)
        self.assertIn("CameraApiAuthenticationError", log_output)
        self.assertIn("retryable=False", log_output)
        self.assertNotIn("AUTH-LOG-SECRET", log_output)

    def test_transport_failure_logs_only_first_and_third_failure(self):
        client = FakeClient(
            error=CameraApiTransportError(
                "camera front-door request failed: ConnectTimeout", retryable=True
            )
        )
        poller = self.create_poller(client)

        with self.assertLogs("agent.device_identity", level="WARNING") as captured:
            for _ in range(4):
                poller.poll_once()

        self.assertEqual(2, len(captured.output))
        self.assertIn("failure_count=1", captured.output[0])
        self.assertIn("failure_count=3", captured.output[1])
        self.assertTrue(all("retryable=True" in line for line in captured.output))

    def test_non_camera_failure_log_omits_exception_message(self):
        poller = self.create_poller(FakeClient())

        with self.assertLogs("agent.device_identity", level="WARNING") as captured:
            poller._record_failure(RuntimeError("DATABASE-LOG-SECRET"))

        self.assertIn("RuntimeError", captured.output[0])
        self.assertIn("failure_count=1", captured.output[0])
        self.assertNotIn("DATABASE-LOG-SECRET", captured.output[0])

    def test_clock_warning_callback_failure_preserves_records_and_recovery(self):
        client = FakeClient(error=CameraApiTransportError("offline", retryable=True))
        health = []
        warnings = []

        def fail_warning_callback(device_id, reason):
            warnings.append((device_id, reason))
            raise RuntimeError(
                "CLOCK-WARNING-SECRET <Ownner>raw-record</Ownner> "
                "credentials=device-password"
            )

        poller = RecognitionRecordPoller(
            client=client,
            repository=self.repository,
            intake=self.ingestor,
            on_health_change=lambda device_id, healthy, reason: health.append(
                (device_id, healthy, reason)
            ),
            on_warning=fail_warning_callback,
            clock=lambda: NOW,
        )
        poller.poll_once()
        poller.poll_once()
        poller.poll_once()
        self.assertFalse(poller.healthy)

        client.error = None
        client.pages = {
            1: [
                record(10, second=16, pass_code=1),
                record(11, second=17, person_id=999, unique_id="missing"),
            ]
        }
        with self.assertLogs("agent.device_identity", level="ERROR") as captured:
            first_emitted = poller.poll_once()
            second_emitted = poller.poll_once()

        self.assertEqual(2, len(first_emitted))
        self.assertEqual(2, len(second_emitted))
        self.assertEqual(4, len(self.ingestor.candidates))
        cursor = self.repository.get_cursor("front-door")
        self.assertEqual(11, cursor.last_log_id)
        self.assertEqual(NOW, cursor.last_record_time)
        self.assertEqual(NOW, cursor.last_success_at)
        self.assertTrue(poller.healthy)
        self.assertEqual(0, poller._consecutive_failures)
        self.assertIn(("front-door", True, "recovered"), health)
        self.assertEqual(
            [
                ("front-door", "device_clock_ahead"),
                ("front-door", "device_clock_ahead"),
            ],
            warnings,
        )
        log_output = "\n".join(captured.output)
        self.assertIn("front-door", log_output)
        self.assertIn("RuntimeError", log_output)
        self.assertNotIn("device_clock_ahead", log_output)
        self.assertNotIn("CLOCK-WARNING-SECRET", log_output)
        self.assertNotIn("Ownner", log_output)
        self.assertNotIn("credentials", log_output)
        self.assertNotIn("device-password", log_output)
        self.assertNotIn("missing", log_output)


if __name__ == "__main__":
    unittest.main()
