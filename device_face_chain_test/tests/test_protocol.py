from datetime import datetime
import unittest

from device_face_chain_test.protocol import (
    ProtocolError,
    event_key,
    face_reco_ack,
    heartbeat_ack,
)


HEARTBEAT = {
    "operator": "heartbeat",
    "deviceInfo": {"serialNumber": "P6S-001"},
    "info": {"eventId": 7},
}

FACE_RECO = {
    "operator": "FaceReco",
    "deviceInfo": {"serialNumber": "P6S-001"},
    "info": {
        "eventId": 8,
        "personInfo": {"uniqueId": "uid-001", "personId": "42"},
        "CaptureImage": {"pictureMd5": "abc123", "picture": "data:image/jpeg;base64,ignored"},
    },
}

EVENT_FLAGS = {
    "isEnableElectronicDefence",
    "isCrossBorderDetectEnable",
    "isOffDutyDetectEnable",
    "isPassengerFlowStaticsEnable",
    "isCryScreamDetectEnable",
    "isPetDetectEnable",
    "isFallDetectEnable",
    "isSnapshotEnable",
    "isPersonInfoEnable",
    "isPersonDetectEnable",
    "isCarLicenseSnapshotEnable",
    "isCarDetectEnable",
    "isTimedSnapshotEnable",
    "isGroundLockStatusChangedEnable",
    "isStartupReportEnable",
    "isAbnormalEventEnable",
    "isNonWhitelistCarEnable",
    "isMotionDetectEnable",
    "isTrafficStatisticsEnable",
    "isTrafficStatisticsCarShapeEnable",
    "isKey2CallEnable",
    "isLicensePlateSurveillanceEnable",
    "isFireDetectEventEnable",
    "isVideoCoverEventEnable",
    "isElectricBikeEventEnable",
    "isGasContainerEventEnable",
    "isElectronicDefenceV2EventEnable",
    "isPeopleNumberStatisticsEventEnable",
    "isPeopleNumberOverLimitEventEnable",
}


class ProtocolTests(unittest.TestCase):
    def test_heartbeat_ack_echoes_event_and_only_enables_face_recognition(self):
        ack = heartbeat_ack(HEARTBEAT, datetime(2026, 7, 20, 13, 25, 0))

        self.assertEqual(ack["operator"], "heartbeat-Ack")
        self.assertEqual(ack["info"]["eventId"], 7)
        self.assertEqual(ack["info"]["time"], "20260720T132500+08")
        self.assertEqual(ack["info"]["eventSendMode"], "realTime")
        strategy = ack["info"]["strategy"]
        event_flags = {key: value for key, value in strategy.items() if key.startswith("is")}
        self.assertEqual(set(event_flags), EVENT_FLAGS)
        self.assertEqual([key for key, value in event_flags.items() if value], ["isPersonInfoEnable"])
        self.assertEqual(ack["info"]["strategy"]["passengerStaticsInterval"], 2)
        self.assertEqual(ack["info"]["strategy"]["heartBeatInterval"], 30)
        self.assertEqual(ack["result"], {"errorNo": 0, "description": "ok"})

    def test_face_reco_ack_preserves_unique_id_numeric_person_id_and_picture_md5(self):
        ack = face_reco_ack(FACE_RECO)

        self.assertEqual(ack["operator"], "FaceReco-Ack")
        self.assertEqual(ack["info"], {
            "personId": 42,
            "uniqueId": "uid-001",
            "pictureMd5": "abc123",
        })
        self.assertEqual(ack["result"], {"errorNo": 0, "description": "ok"})
        self.assertNotIn("picture", ack)

    def test_non_numeric_person_id_becomes_zero(self):
        payload = {**FACE_RECO, "info": {**FACE_RECO["info"], "personInfo": {"uniqueId": "uid-002", "personId": "unknown"}}}

        self.assertEqual(face_reco_ack(payload)["info"]["personId"], 0)

    def test_missing_picture_md5_becomes_empty_string(self):
        payload = {**FACE_RECO, "info": {**FACE_RECO["info"], "CaptureImage": {}}}

        self.assertEqual(face_reco_ack(payload)["info"]["pictureMd5"], "")

    def test_malformed_person_info_becomes_empty_person_fields(self):
        payload = {**FACE_RECO, "info": {**FACE_RECO["info"], "personInfo": "malformed"}}

        self.assertEqual(face_reco_ack(payload)["info"]["personId"], 0)
        self.assertEqual(face_reco_ack(payload)["info"]["uniqueId"], "")

    def test_malformed_capture_image_becomes_empty_picture_md5(self):
        payload = {**FACE_RECO, "info": {**FACE_RECO["info"], "CaptureImage": "malformed"}}

        self.assertEqual(face_reco_ack(payload)["info"]["pictureMd5"], "")

    def test_event_key_is_serial_plus_event_id(self):
        self.assertEqual(event_key(FACE_RECO), "P6S-001:8")

    def test_missing_envelope_fields_raise_protocol_error(self):
        with self.assertRaisesRegex(ProtocolError, "serialNumber"):
            event_key({"deviceInfo": {}, "info": {"eventId": 1}})
        with self.assertRaisesRegex(ProtocolError, "eventId"):
            event_key({"deviceInfo": {"serialNumber": "P6S-001"}, "info": {}})

    def test_wrong_operator_raises_protocol_error(self):
        with self.assertRaisesRegex(ProtocolError, "operator"):
            heartbeat_ack({**HEARTBEAT, "operator": "FaceReco"}, datetime(2026, 7, 20, 13, 25, 0))
        with self.assertRaisesRegex(ProtocolError, "operator"):
            face_reco_ack({**FACE_RECO, "operator": "heartbeat"})


if __name__ == "__main__":
    unittest.main()
