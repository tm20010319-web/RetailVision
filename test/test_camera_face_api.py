from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime
from unittest.mock import Mock
from xml.etree import ElementTree as ET

from agent.camera_face_api import (
    CameraApiAuthenticationError,
    CameraApiProtocolError,
    CameraFaceApiClient,
    generate_ownner,
)


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code


class CameraFaceApiClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = CameraFaceApiClient(
            device_id="front-door",
            base_url="http://192.0.2.1",
            username="camera-user",
            password="camera-password",
            unique_code="1234567890ABCDEF",
            session=self.session,
            connect_timeout=1.5,
            read_timeout=4.0,
        )

    def test_ownner_uses_vendor_vector(self):
        value = generate_ownner(
            "1234567890ABCDEF", "2026-07-14T12:00:00"
        )
        self.assertEqual("b575e77e", value)

    def test_client_accepts_blank_password_for_default_device_credentials(self):
        session = Mock()
        session.request.return_value = _Response("<FaceGroups />")
        client = CameraFaceApiClient(
            device_id="front-door",
            base_url="http://192.0.2.1",
            username="admin",
            password="",
            unique_code="1234567890ABCDEF",
            session=session,
        )

        client.query_face_groups()

        self.assertEqual(("admin", ""), session.request.call_args.kwargs["auth"])

    def test_query_records_uses_basic_auth_and_expected_xml(self):
        self.session.request.return_value = _Response(
            """<RecognitionRecordList>
              <RecognitionRecord>
                <LogID>7</LogID><GroupID>2</GroupID><PersonID>19</PersonID>
                <Time>20260714T120001</Time><Pass>0</Pass><Similar>83</Similar>
                <RuleID>1</RuleID><RecoValid>true</RecoValid><UniqueID>member-7</UniqueID>
                <SnapshotPath>/sensitive/photo.jpg</SnapshotPath>
                <HealthCode>Green</HealthCode>
              </RecognitionRecord>
            </RecognitionRecordList>"""
        )

        records = self.client.query_recognition_records(
            start_time=datetime(2026, 7, 14, 12, 0, 0),
            stop_time=datetime(2026, 7, 14, 12, 0, 2),
            page_size=100,
            page_num=2,
        )

        self.assertEqual(1, len(records))
        self.assertEqual(
            {
                "log_id": 7,
                "group_id": 2,
                "person_id": 19,
                "occurred_at": datetime(2026, 7, 14, 4, 0, 1),
                "pass_code": 0,
                "similarity": 0.83,
                "rule_id": 1,
                "reco_valid": True,
                "unique_id": "member-7",
            },
            records[0],
        )
        request = self.session.request.call_args
        self.assertEqual("PUT", request.args[0])
        self.assertEqual(
            "http://192.0.2.1/FaceRecognition/QueryRecordList", request.args[1]
        )
        self.assertEqual((1.5, 4.0), request.kwargs["timeout"])
        self.assertEqual(("camera-user", "camera-password"), request.kwargs["auth"])
        root = ET.fromstring(request.kwargs["data"])
        self.assertEqual("FaceRecognitionFilter", root.tag)
        self.assertEqual("20260714T120000", root.findtext("StartTime"))
        self.assertEqual("20260714T120002", root.findtext("StopTime"))
        self.assertEqual("0", root.findtext("IsHasPath"))
        self.assertEqual("2", root.findtext("Pagenum"))

    def test_read_device_metadata_uses_authenticated_read_only_endpoint(self):
        self.session.request.return_value = _Response(
            """<DeviceInfo><serialNumber>SN-CAM02</serialNumber>
            <deviceModel>P6S-X</deviceModel><firmwareVersion>3.2.1</firmwareVersion>
            <protocolVersion>p6s-http-v1</protocolVersion></DeviceInfo>"""
        )

        metadata = self.client.read_device_metadata()

        request = self.session.request.call_args
        self.assertEqual("GET", request.args[0])
        self.assertEqual("http://192.0.2.1/System/DeviceInfo", request.args[1])
        self.assertEqual(("camera-user", "camera-password"), request.kwargs["auth"])
        self.assertEqual("SN-CAM02", metadata.device_serial)
        self.assertEqual("P6S-X", metadata.device_model)
        self.assertEqual("3.2.1", metadata.firmware_version)
        self.assertEqual("p6s-http-v1", metadata.api_version)
        self.assertEqual("authenticated_p6s_metadata", metadata.metadata_source)
        self.assertEqual(
            "/System/DeviceInfo",
            metadata.field_audit["device_model"]["endpoint"],
        )

    def test_metadata_only_client_needs_no_write_code_and_rejects_non_get(self):
        session = Mock()
        session.request.return_value = _Response(
            """<DeviceInfo><serialNumber>SN-CAM02</serialNumber>
            <deviceModel>P6S-X</deviceModel>
            <firmwareVersion>3.2.1</firmwareVersion>
            <apiVersion>v1</apiVersion></DeviceInfo>"""
        )
        client = CameraFaceApiClient(
            device_id="cam02", base_url="http://192.0.2.1",
            username="operator", password="secret", unique_code="",
            session=session, metadata_only=True,
        )

        self.assertEqual("P6S-X", client.read_device_metadata().device_model)
        with self.assertRaises(ValueError):
            client.query_face_groups()

    def test_read_device_metadata_rejects_incomplete_schema(self):
        self.session.request.return_value = _Response(
            "<DeviceInfo><deviceModel>P6S-X</deviceModel></DeviceInfo>"
        )

        with self.assertRaises(CameraApiProtocolError):
            self.client.read_device_metadata()

    def test_read_device_metadata_accepts_real_p6s_aliases_without_api_version(self):
        self.session.request.return_value = _Response(
            """<DeviceInfo><SerialNum>SN-CAM02</SerialNum>
            <DeviceTypeName>P6S-X</DeviceTypeName>
            <SoftWareVersion>3.4.0</SoftWareVersion></DeviceInfo>"""
        )

        metadata = self.client.read_device_metadata()

        self.assertEqual("SN-CAM02", metadata.device_serial)
        self.assertEqual("P6S-X", metadata.device_model)
        self.assertEqual("3.4.0", metadata.firmware_version)
        self.assertEqual("", metadata.api_version)

    def test_query_records_parses_single_root_record(self):
        self.session.request.return_value = _Response(
            """<RecognitionRecord><LogID>8</LogID><GroupID>1</GroupID>
            <PersonID>3</PersonID><Time>20260714T120003</Time><Pass>0</Pass>
            <Similar>75</Similar><RuleID>1</RuleID><RecoValid>1</RecoValid>
            <UniqueID>u-3</UniqueID></RecognitionRecord>"""
        )

        records = self.client.query_recognition_records(
            datetime(2026, 7, 14, 12, 0, 0),
            datetime(2026, 7, 14, 12, 0, 4),
        )

        self.assertEqual(8, records[0]["log_id"])
        self.assertTrue(records[0]["reco_valid"])

    def test_query_records_rejects_missing_required_record_fields(self):
        self.session.request.return_value = _Response(
            """<RecognitionRecordList><RecognitionRecord>
            <LogID>8</LogID><GroupID>1</GroupID><PersonID>3</PersonID>
            <Time>20260714T120003</Time><Pass>0</Pass><RuleID>1</RuleID>
            <RecoValid>1</RecoValid><UniqueID>u-3</UniqueID>
            </RecognitionRecord></RecognitionRecordList>"""
        )

        with self.assertRaises(CameraApiProtocolError):
            self.client.query_recognition_records(
                datetime(2026, 7, 14, 12), datetime(2026, 7, 14, 12, 0, 4)
            )

    def test_query_records_uses_explicit_device_timezone_offset(self):
        self.session.request.return_value = _Response(
            """<RecognitionRecord><LogID>8</LogID><GroupID>1</GroupID>
            <PersonID>3</PersonID><Time>20260714T120003</Time><Pass>0</Pass>
            <Similar>75</Similar><RuleID>1</RuleID><RecoValid>1</RecoValid>
            <UniqueID>u-3</UniqueID></RecognitionRecord>"""
        )

        records = self.client.query_recognition_records(
            datetime(2026, 7, 14, 12),
            datetime(2026, 7, 14, 12, 0, 4),
            timezone_offset_minutes=60,
        )

        self.assertEqual(datetime(2026, 7, 14, 11, 0, 3), records[0]["occurred_at"])

    def test_query_person_info_sends_complete_required_filter(self):
        self.session.request.return_value = _Response("<PersonInfoList />")

        self.client.query_person_info(
            group_id=0,
            alias="",
            page_size=100,
            page_num=1,
        )

        request = self.session.request.call_args
        root = ET.fromstring(request.kwargs["data"])
        self.assertEqual("PersonInfoFilter", root.tag)
        self.assertEqual("0", root.findtext("GroupID"))
        self.assertEqual("none", root.findtext("Sex"))
        self.assertEqual("none", root.findtext("CertificateType"))
        for field in (
            "Name",
            "CertificateID",
            "Country",
            "Address",
            "StartBirthday",
            "StopBirthday",
        ):
            self.assertIsNotNone(root.find(field), field)
            self.assertIsNone(root.find(field).text, field)
        self.assertEqual("100", root.findtext("Pagesize"))
        self.assertEqual("1", root.findtext("Pagenum"))

    def test_authentication_error_is_not_reported_as_retryable(self):
        self.session.request.return_value = _Response("unauthorized", status_code=401)

        with self.assertRaises(CameraApiAuthenticationError) as raised:
            self.client.query_recognition_records(
                datetime(2026, 7, 14, 12), datetime(2026, 7, 14, 12, 0, 1)
            )

        self.assertFalse(raised.exception.retryable)

    def test_malformed_xml_raises_protocol_error_without_response_body(self):
        self.session.request.return_value = _Response("<broken")

        with self.assertRaises(CameraApiProtocolError) as raised:
            self.client.query_recognition_records(
                datetime(2026, 7, 14, 12), datetime(2026, 7, 14, 12, 0, 1)
            )

        self.assertNotIn("<broken", str(raised.exception))

    def test_http_5xx_is_retried_with_an_upper_bound(self):
        self.session.request.side_effect = [
            _Response("temporary", status_code=503),
            _Response("<RecognitionRecordList />"),
        ]

        records = self.client.query_recognition_records(
            datetime(2026, 7, 14, 12), datetime(2026, 7, 14, 12, 0, 1)
        )

        self.assertEqual([], records)
        self.assertEqual(2, self.session.request.call_count)

    def test_vendor_error_status_on_query_is_not_treated_as_empty_page(self):
        self.session.request.return_value = _Response(
            "<ResponseStatus><statusCode>12</statusCode></ResponseStatus>"
        )

        with self.assertRaises(CameraApiProtocolError):
            self.client.query_recognition_records(
                datetime(2026, 7, 14, 12), datetime(2026, 7, 14, 12, 0, 1)
            )

    def test_upload_member_requires_declared_supported_sex(self):
        with self.assertRaises(ValueError):
            self.client.update_person_and_face_image(
                alias="member-42",
                sex="unknown",
                group_uuid="group-uuid",
                face_uuid="face-uuid",
                image_bytes=b"jpg",
                system_time=datetime(2026, 7, 14, 12),
            )

    def test_upload_member_requires_http_and_vendor_success(self):
        self.session.request.return_value = _Response(
            "<ResponseStatus><statusCode>0</statusCode><MD5>abc</MD5></ResponseStatus>"
        )

        result = self.client.update_person_and_face_image(
            alias="member-42",
            sex="female",
            group_uuid="group-uuid",
            face_uuid="face-uuid",
            image_bytes=b"jpg",
            system_time=datetime(2026, 7, 14, 12),
        )

        self.assertTrue(result.success)
        request = self.session.request.call_args
        self.assertNotIn("Ownner", str(result))
        self.assertEqual("member-42", request.kwargs["data"]["Name"])
        self.assertEqual(("face.jpg", b"jpg", "image/jpeg"), request.kwargs["files"]["file"])

        self.session.request.return_value = _Response(
            "<ResponseStatus><statusCode>9</statusCode><message>failed</message></ResponseStatus>"
        )
        with self.assertRaises(CameraApiProtocolError):
            self.client.update_person_and_face_image(
                alias="member-42",
                sex="female",
                group_uuid="group-uuid",
                face_uuid="face-uuid",
                image_bytes=b"jpg",
                system_time=datetime(2026, 7, 14, 12),
            )


if __name__ == "__main__":
    unittest.main()
