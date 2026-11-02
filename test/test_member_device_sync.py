from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime

from agent.platform.camera_face_api import CameraApiProtocolError, CameraOperationResult
from agent.identity.device_identity import InMemoryDeviceIdentityRepository
from agent.member.member_device_sync import MemberDeviceSyncError, MemberDeviceSyncService


NOW = datetime(2026, 7, 14, 12, 0, 0)


class SyncClient:
    device_id = "front-door"

    def __init__(self):
        self.uploads = []
        self.queries = []
        self.deletes = []
        self.people = []
        self.on_delete = None

    def update_person_and_face_image(self, **kwargs):
        self.uploads.append(kwargs)
        return CameraOperationResult(True, 0, image_md5="abc")

    def query_person_info(self, **kwargs):
        self.queries.append(kwargs)
        return list(self.people)

    def delete_person(self, **kwargs):
        if self.on_delete:
            self.on_delete()
        self.deletes.append(kwargs)
        return CameraOperationResult(True, 0)


class MemberDeviceSyncTests(unittest.TestCase):
    def setUp(self):
        self.client = SyncClient()
        self.repository = InMemoryDeviceIdentityRepository()
        self.service = MemberDeviceSyncService(
            client=self.client,
            repository=self.repository,
            clock=lambda: NOW,
        )

    def test_authorization_uses_business_alias_and_confirms_model_success(self):
        self.client.people = [
            {
                "group_id": 2,
                "person_id": 19,
                "alias": "member-expected",
                "model_state": "success",
                "face_uuid": "face-fixed",
                "unique_id": "vendor-19",
            }
        ]

        mapping = self.service.authorize_member(
            member_id="member-19",
            photo_bytes=b"authorized-jpeg",
            declared_sex="female",
            group_id=2,
            group_uuid="group-uuid",
            alias="member-expected",
            face_uuid="face-fixed",
        )

        self.assertEqual("synced", mapping.sync_status)
        self.assertEqual(19, mapping.person_id)
        self.assertEqual("vendor-19", mapping.unique_id)
        upload = self.client.uploads[0]
        self.assertEqual("member-expected", upload["alias"])
        self.assertEqual("female", upload["sex"])
        self.assertEqual(b"authorized-jpeg", upload["image_bytes"])

    def test_default_alias_is_opaque_and_face_uuid_is_generated(self):
        self.client.people = []
        with self.assertRaises(MemberDeviceSyncError):
            self.service.authorize_member(
                member_id="real-name-is-not-sent",
                photo_bytes=b"jpg",
                declared_sex="male",
                group_id=2,
                group_uuid="group-uuid",
            )

        upload = self.client.uploads[0]
        self.assertTrue(upload["alias"].startswith("m-"))
        self.assertNotIn("real-name-is-not-sent", upload["alias"])
        self.assertTrue(upload["face_uuid"])

    def test_model_state_must_be_success(self):
        self.client.people = [
            {
                "group_id": 2,
                "person_id": 19,
                "alias": "member-expected",
                "model_state": "failed",
                "face_uuid": "face-fixed",
                "unique_id": "vendor-19",
            }
        ]

        with self.assertRaises(MemberDeviceSyncError):
            self.service.authorize_member(
                member_id="member-19",
                photo_bytes=b"jpg",
                declared_sex="female",
                group_id=2,
                group_uuid="group-uuid",
                alias="member-expected",
                face_uuid="face-fixed",
            )

        mapping = self.repository.get_mapping("front-door", "member-19")
        self.assertEqual("failed", mapping.sync_status)
        self.assertNotIn("failed", mapping.last_error)

    def test_upload_failure_logs_sanitized_camera_error(self):
        self.client.password = "UPLOAD-LOG-SECRET"

        def fail_upload(**kwargs):
            raise CameraApiProtocolError(
                "camera front-door operation failed with statusCode=9",
                retryable=False,
            )

        self.client.update_person_and_face_image = fail_upload
        with self.assertLogs("agent.member.member_device_sync", level="WARNING") as captured:
            with self.assertRaises(MemberDeviceSyncError):
                self.service.authorize_member(
                    member_id="member-19",
                    photo_bytes=b"jpg",
                    declared_sex="male",
                    group_id=2,
                    group_uuid="group-uuid",
                )

        log_output = "\n".join(captured.output)
        self.assertIn("front-door", log_output)
        self.assertIn("CameraApiProtocolError", log_output)
        self.assertIn("statusCode=9", log_output)
        self.assertIn("retryable=False", log_output)
        self.assertNotIn("UPLOAD-LOG-SECRET", log_output)

    def test_idempotent_update_reuses_existing_face_uuid(self):
        self.client.people = [
            {
                "group_id": 2,
                "person_id": 19,
                "alias": "member-expected",
                "model_state": "success",
                "face_uuid": "face-fixed",
                "unique_id": "vendor-19",
            }
        ]
        kwargs = dict(
            member_id="member-19",
            photo_bytes=b"jpg",
            declared_sex="female",
            group_id=2,
            group_uuid="group-uuid",
            alias="member-expected",
            face_uuid="face-fixed",
        )
        self.service.authorize_member(**kwargs)

        self.service.authorize_member(**{**kwargs, "face_uuid": None})

        self.assertEqual("face-fixed", self.client.uploads[1]["face_uuid"])

    def test_revoke_disables_mapping_before_device_delete(self):
        self.client.people = [
            {
                "group_id": 2,
                "person_id": 19,
                "alias": "member-expected",
                "model_state": "success",
                "face_uuid": "face-fixed",
                "unique_id": "vendor-19",
            }
        ]
        self.service.authorize_member(
            member_id="member-19",
            photo_bytes=b"jpg",
            declared_sex="female",
            group_id=2,
            group_uuid="group-uuid",
            alias="member-expected",
            face_uuid="face-fixed",
        )
        self.client.on_delete = lambda: self.assertEqual(
            "disabled",
            self.repository.get_mapping("front-door", "member-19").sync_status,
        )

        result = self.service.revoke_member("member-19")

        self.assertTrue(result.success)
        self.assertEqual(
            "revoked",
            self.repository.get_mapping("front-door", "member-19").sync_status,
        )
        self.assertTrue(self.client.deletes[0]["delete_picture"])

    def test_delete_failure_keeps_mapping_disabled(self):
        self.client.people = [
            {
                "group_id": 2,
                "person_id": 19,
                "alias": "member-expected",
                "model_state": "success",
                "face_uuid": "face-fixed",
                "unique_id": "vendor-19",
            }
        ]
        self.service.authorize_member(
            member_id="member-19",
            photo_bytes=b"jpg",
            declared_sex="female",
            group_id=2,
            group_uuid="group-uuid",
            alias="member-expected",
            face_uuid="face-fixed",
        )

        def fail_delete(**kwargs):
            raise CameraApiProtocolError("delete failed", retryable=False)

        self.client.delete_person = fail_delete
        with self.assertRaises(MemberDeviceSyncError):
            self.service.revoke_member("member-19")

        mapping = self.repository.get_mapping("front-door", "member-19")
        self.assertEqual("disabled", mapping.sync_status)
        self.assertEqual("device_delete_failed", mapping.last_error)

    def test_delete_failure_logs_sanitized_camera_error(self):
        self.client.people = [
            {
                "group_id": 2,
                "person_id": 19,
                "alias": "member-expected",
                "model_state": "success",
                "face_uuid": "face-fixed",
                "unique_id": "vendor-19",
            }
        ]
        self.service.authorize_member(
            member_id="member-19",
            photo_bytes=b"jpg",
            declared_sex="female",
            group_id=2,
            group_uuid="group-uuid",
            alias="member-expected",
            face_uuid="face-fixed",
        )
        self.client.password = "DELETE-LOG-SECRET"

        def fail_delete(**kwargs):
            raise CameraApiProtocolError(
                "camera front-door operation failed with statusCode=12",
                retryable=False,
            )

        self.client.delete_person = fail_delete
        with self.assertLogs("agent.member.member_device_sync", level="WARNING") as captured:
            with self.assertRaises(MemberDeviceSyncError):
                self.service.revoke_member("member-19")

        log_output = "\n".join(captured.output)
        self.assertIn("front-door", log_output)
        self.assertIn("CameraApiProtocolError", log_output)
        self.assertIn("statusCode=12", log_output)
        self.assertIn("retryable=False", log_output)
        self.assertNotIn("DELETE-LOG-SECRET", log_output)


if __name__ == "__main__":
    unittest.main()
