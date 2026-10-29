"""Unit tests for the member management API and service layer.

These tests use an in-memory SQLite database and mock the device sync
service, so no camera device or YOLO model is required.
"""

from __future__ import annotations

import io
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Bootstrap must come before agent imports.
from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from fastapi.testclient import TestClient

from agent.db import init_db
from agent.member_api import (
    app,
    get_device_event_services,
    get_operations_query_service,
    reset_device_event_services,
    reset_service,
)
from agent.device_identity import InMemoryDeviceIdentityRepository
from agent.member_device_sync import MemberDeviceSyncService
from agent.member_management_service import MemberManagementError, MemberManagementService
from agent.models import Base, BusinessJob, Member
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _make_session_factory():
    """Create an in-memory SQLite session factory with all tables.

    Uses StaticPool so the single in-memory connection is shared across
    threads (FastAPI async handlers run in a thread pool).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def factory():
        return session_factory()

    return factory, engine


class TestMemberManagementService(unittest.TestCase):
    """Test the orchestration layer directly (no HTTP, no device)."""

    def setUp(self):
        self.session_factory, self.engine = _make_session_factory()
        self.service = MemberManagementService(
            session_factory=self.session_factory,
            device_sync_service=None,
            group_uuid=None,
            group_id=-1,
            device_id=None,
        )

    def test_create_member(self):
        result = self.service.create_member(
            member_id="M001",
            name="Alice",
            phone="13800000001",
            tier="gold",
            department="研发部",
            position="研发组长",
            points=100,
        )
        self.assertEqual(result["id"], "M001")
        self.assertEqual(result["name"], "Alice")
        self.assertEqual(result["tier"], "gold")
        self.assertEqual(result["department"], "研发部")
        self.assertEqual(result["position"], "研发组长")
        self.assertEqual(result["points"], 100)
        self.assertTrue(result["is_active"])
        self.assertEqual(result["face_sync"]["sync_status"], "not_uploaded")

    def test_create_member_duplicate(self):
        self.service.create_member(member_id="M001", name="Alice")
        with self.assertRaises(MemberManagementError) as ctx:
            self.service.create_member(member_id="M001", name="Bob")
        self.assertIn("already exists", str(ctx.exception))

    def test_create_member_invalid_tier(self):
        with self.assertRaises(MemberManagementError):
            self.service.create_member(member_id="M002", name="Bob", tier="invalid")

    def test_gender_and_display_name_suffix_rules(self):
        female = self.service.create_member(
            member_id="M-FEMALE", name="李", gender="female"
        )
        male = self.service.create_member(
            member_id="M-MALE", name="王先生", gender="male"
        )
        unknown = self.service.create_member(
            member_id="M-UNKNOWN", name="赵", gender="unknown"
        )
        self.assertEqual(female["gender"], "female")
        self.assertEqual(female["display_name"], "李女士")
        self.assertEqual(male["display_name"], "王先生")
        self.assertEqual(unknown["display_name"], "赵")

    def test_invalid_gender_is_rejected(self):
        with self.assertRaises(MemberManagementError):
            self.service.create_member(member_id="M-BAD", name="Bad", gender="女")

    def test_create_member_supports_diamond_and_platinum(self):
        diamond = self.service.create_member(
            member_id="M-DIAMOND", name="Diamond", tier="diamond"
        )
        platinum = self.service.create_member(
            member_id="M-PLATINUM", name="Platinum", tier="platinum"
        )
        self.assertEqual(diamond["tier"], "diamond")
        self.assertEqual(platinum["tier"], "platinum")

    def test_create_member_missing_name(self):
        with self.assertRaises(MemberManagementError):
            self.service.create_member(member_id="M003", name="")

    def test_get_member(self):
        self.service.create_member(
            member_id="M001",
            name="Alice",
            tier="silver",
        )
        result = self.service.get_member("M001")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Alice")
        self.assertEqual(result["tier"], "silver")

    def test_get_member_not_found(self):
        result = self.service.get_member("nonexistent")
        self.assertIsNone(result)

    def test_get_member_by_phone(self):
        self.service.create_member(
            member_id="M001",
            name="Alice",
            phone="13800000001",
        )
        result = self.service.get_member_by_phone("13800000001")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "M001")

    def test_list_members(self):
        for i in range(5):
            self.service.create_member(
                member_id=f"M00{i}",
                name=f"Member{i}",
                tier="gold" if i < 2 else "bronze",
            )
        result = self.service.list_members(tier="gold", page=1, size=10)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["members"]), 2)

    def test_list_members_pagination(self):
        for i in range(5):
            self.service.create_member(member_id=f"M00{i}", name=f"Member{i}")
        result = self.service.list_members(page=1, size=2)
        self.assertEqual(result["total"], 5)
        self.assertEqual(len(result["members"]), 2)

    def test_update_member(self):
        self.service.create_member(member_id="M001", name="Alice", tier="bronze")
        result = self.service.update_member(
            "M001",
            name="Alice2",
            tier="gold",
            department="招商部",
            position="招商主管",
            points=500,
        )
        self.assertEqual(result["name"], "Alice2")
        self.assertEqual(result["tier"], "gold")
        self.assertEqual(result["department"], "招商部")
        self.assertEqual(result["position"], "招商主管")
        self.assertEqual(result["points"], 500)

    def test_update_member_not_found(self):
        with self.assertRaises(MemberManagementError):
            self.service.update_member("nonexistent", name="X")

    def test_get_face_status_not_uploaded(self):
        result = self.service.get_face_status("M001")
        self.assertEqual(result["sync_status"], "not_uploaded")


class TestMemberManagementServiceWithDevice(unittest.TestCase):
    """Test device face registration with a mock MemberDeviceSyncService."""

    def setUp(self):
        self.session_factory, self.engine = _make_session_factory()
        from agent.device_identity import DeviceMemberMappingData

        self.mock_sync = MagicMock()
        self.mock_sync.authorize_member.return_value = DeviceMemberMappingData(
            device_id="cam01",
            member_id="M001",
            group_id=1,
            group_uuid="group-uuid-123",
            person_id=5,
            unique_id="uid-001",
            face_uuid="face-uuid-001",
            sync_status="synced",
            last_sync_at=datetime(2026, 7, 14, 12, 0, 0),
            last_error=None,
            member_active=True,
        )
        self.service = MemberManagementService(
            session_factory=self.session_factory,
            device_sync_service=self.mock_sync,
            group_uuid="group-uuid-123",
            group_id=1,
            device_id="cam01",
        )
        self.service.create_member(member_id="M001", name="Alice")

    def test_upload_face_success(self):
        result = self.service.upload_face("M001", b"fake_photo_bytes", "male")
        self.assertEqual(result["sync_status"], "synced")
        self.assertEqual(result["person_id"], 5)
        self.mock_sync.authorize_member.assert_called_once()

    def test_upload_face_preserves_group_id_zero(self):
        self.service.group_id = 0
        self.mock_sync.authorize_member.return_value.group_id = 0

        self.service.upload_face("M001", b"fake_photo_bytes", "male")

        self.assertEqual(
            self.mock_sync.authorize_member.call_args.kwargs["group_id"],
            0,
        )

    def test_upload_face_preserves_positive_group_id(self):
        self.service.group_id = 7

        self.service.upload_face("M001", b"fake_photo_bytes", "male")

        self.assertEqual(
            self.mock_sync.authorize_member.call_args.kwargs["group_id"],
            7,
        )

    def test_upload_face_rejects_missing_group_id_before_device_call(self):
        self.service.group_id = None

        with self.assertRaisesRegex(
            MemberManagementError,
            "CAMERA_FACE_GROUP_ID must be configured",
        ):
            self.service.upload_face("M001", b"fake_photo_bytes", "male")

        self.mock_sync.authorize_member.assert_not_called()

    def test_upload_face_rejects_negative_group_id_before_device_call(self):
        self.service.group_id = -1

        with self.assertRaisesRegex(
            MemberManagementError,
            "CAMERA_FACE_GROUP_ID must be configured",
        ):
            self.service.upload_face("M001", b"fake_photo_bytes", "male")

        self.mock_sync.authorize_member.assert_not_called()

    def test_group_id_zero_mapping_can_resolve_recognition(self):
        repository = InMemoryDeviceIdentityRepository()
        client = MagicMock()
        client.device_id = "cam01"
        client.query_person_info.return_value = [
            {
                "alias": "m-test",
                "model_state": "success",
                "person_id": 5,
                "unique_id": "uid-001",
            }
        ]
        sync = MemberDeviceSyncService(
            client=client,
            repository=repository,
            clock=lambda: datetime(2026, 7, 14, 12, 0, 0),
        )
        service = MemberManagementService(
            session_factory=self.session_factory,
            device_sync_service=sync,
            group_uuid="group-uuid-123",
            group_id=0,
            device_id="cam01",
        )
        alias = "m-test"
        with patch("agent.member_device_sync._opaque_alias", return_value=alias):
            service.upload_face("M001", b"fake_photo_bytes", "male")

        mapping = repository.resolve_mapping(
            "cam01",
            0,
            5,
            "uid-001",
        )
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.group_id, 0)

    def test_upload_face_member_not_found(self):
        with self.assertRaises(MemberManagementError):
            self.service.upload_face("nonexistent", b"photo", "male")

    def test_upload_face_invalid_sex(self):
        with self.assertRaises(MemberManagementError):
            self.service.upload_face("M001", b"photo", "other")

    def test_revoke_face(self):
        from agent.camera_face_api import CameraOperationResult

        self.mock_sync.revoke_member.return_value = CameraOperationResult(
            success=True, status_code=0, message=None,
        )
        result = self.service.revoke_face("M001")
        self.assertEqual(result["sync_status"], "revoked")

    def test_upload_face_no_device_configured(self):
        service = MemberManagementService(
            session_factory=self.session_factory,
            device_sync_service=None,
        )
        with self.assertRaises(MemberManagementError):
            service.upload_face("M001", b"photo", "male")


class TestMemberAPI(unittest.TestCase):
    """Test the FastAPI endpoints via TestClient."""

    def setUp(self):
        self.session_factory, self.engine = _make_session_factory()
        self.service = MemberManagementService(
            session_factory=self.session_factory,
            device_sync_service=None,
        )
        reset_device_event_services()
        reset_service()
        app.dependency_overrides = {}
        # Patch get_service to return our test service.
        self._patch = patch("agent.member_api.get_service", return_value=self.service)
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self._patch.stop()
        reset_device_event_services()
        reset_service()

    def _create_profile_job(
        self,
        *,
        event_id: str,
        status: str,
        result=None,
        attempts: int = 0,
        max_attempts: int = 5,
        last_error=None,
        created_at=None,
    ):
        db = self.session_factory()
        try:
            db.add(BusinessJob(
                event_id=event_id,
                member_id="M001",
                task_type="customer_profile",
                status=status,
                result=result,
                payload={},
                attempts=attempts,
                max_attempts=max_attempts,
                last_error=last_error,
                created_at=created_at or datetime.now(),
            ))
            db.commit()
        finally:
            db.close()

    def test_create_member_api(self):
        response = self.client.post("/api/members", json={
            "member_id": "M001",
            "name": "Alice",
            "tier": "gold",
        })
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["id"], "M001")
        self.assertEqual(data["name"], "Alice")

    def test_create_member_duplicate_api(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        response = self.client.post("/api/members", json={"member_id": "M001", "name": "Bob"})
        self.assertEqual(response.status_code, 400)

    def test_get_member_api(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        response = self.client.get("/api/members/M001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Alice")

    def test_get_member_not_found_api(self):
        response = self.client.get("/api/members/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_list_members_api(self):
        for i in range(3):
            self.client.post("/api/members", json={
                "member_id": f"M00{i}",
                "name": f"Member{i}",
            })
        response = self.client.get("/api/members?page=1&size=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["members"]), 2)

    def test_get_member_by_phone_api(self):
        self.client.post("/api/members", json={
            "member_id": "M001",
            "name": "Alice",
            "phone": "13800000001",
        })
        response = self.client.get("/api/members?phone=13800000001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "M001")

    def test_update_member_api(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        response = self.client.put("/api/members/M001", json={"tier": "gold"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tier"], "gold")

    def test_get_face_status_api(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        response = self.client.get("/api/members/M001/face/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sync_status"], "not_uploaded")

    def test_get_profile_member_not_found_api(self):
        response = self.client.get("/api/members/nonexistent/profile")

        self.assertEqual(response.status_code, 404)

    def test_get_profile_not_started_api(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})

        response = self.client.get("/api/members/M001/profile")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "member_id": "M001",
            "status": "not_started",
            "event_id": None,
            "attempts": 0,
            "max_attempts": 5,
            "last_error": None,
            "reason": None,
            "profile_event_id": None,
            "profile": None,
            "updated_at": None,
        })

    def test_get_profile_reports_active_and_failed_states(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        cases = (
            ("pending", 0, None),
            ("running", 1, None),
            ("failed", 3, "ollama unavailable"),
        )
        for index, (status, attempts, last_error) in enumerate(cases):
            with self.subTest(status=status):
                self._create_profile_job(
                    event_id=f"evt-{index}",
                    status=status,
                    attempts=attempts,
                    last_error=last_error,
                    created_at=datetime(2026, 7, 14, 12, index, 0),
                )
                response = self.client.get("/api/members/M001/profile")
                data = response.json()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(data["status"], status)
                self.assertEqual(data["event_id"], f"evt-{index}")
                self.assertEqual(data["attempts"], attempts)
                self.assertEqual(data["last_error"], last_error)
                self.assertIsNotNone(data["updated_at"])

    def test_get_profile_succeeded_returns_latest_member_profile(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        profile = {"style_tags": ["casual"], "confidence": 0.9}
        db = self.session_factory()
        try:
            member = db.query(Member).filter(Member.id == "M001").one()
            member.profile = {
                "latest_customer_profile_event_id": "evt-success",
                "latest_customer_profile": profile,
            }
            db.commit()
        finally:
            db.close()
        self._create_profile_job(
            event_id="evt-success",
            status="succeeded",
            result={"profile": profile},
            attempts=1,
        )

        data = self.client.get("/api/members/M001/profile").json()

        self.assertEqual(data["status"], "succeeded")
        self.assertEqual(data["profile_event_id"], "evt-success")
        self.assertEqual(data["profile"], profile)

    def test_get_profile_skipped_returns_reason(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        for index, reason in enumerate(("skipped_no_track", "skipped_no_snapshot")):
            with self.subTest(reason=reason):
                self._create_profile_job(
                    event_id=f"evt-skip-{index}",
                    status="skipped",
                    result={"reason": reason},
                    created_at=datetime(2026, 7, 14, 13, index, 0),
                )
                data = self.client.get("/api/members/M001/profile").json()
                self.assertEqual(data["status"], "skipped")
                self.assertEqual(data["reason"], reason)

    def test_get_profile_exposes_stale_profile_event_for_frontend_guard(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        db = self.session_factory()
        try:
            member = db.query(Member).filter(Member.id == "M001").one()
            member.profile = {
                "latest_customer_profile_event_id": "evt-old",
                "latest_customer_profile": {"style_tags": ["old"]},
            }
            db.commit()
        finally:
            db.close()
        self._create_profile_job(event_id="evt-new", status="pending")

        data = self.client.get("/api/members/M001/profile").json()

        self.assertEqual(data["event_id"], "evt-new")
        self.assertEqual(data["profile_event_id"], "evt-old")
        self.assertEqual(data["profile"], {"style_tags": ["old"]})

    def test_get_operations_status_api(self):
        query_service = MagicMock()
        query_service.status.return_value = {"api": {"state": "online"}}
        with patch(
            "agent.member_api.get_operations_query_service",
            create=True,
            return_value=query_service,
        ):
            response = self.client.get(
                "/api/operations/status",
                headers={"Origin": "http://localhost:5173"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api"]["state"], "online")
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )
        query_service.status.assert_called_once_with()

    def test_recent_recognitions_api_passes_limit_and_allows_cors(self):
        query_service = MagicMock()
        query_service.recent_recognitions.return_value = {"items": []}
        with patch(
            "agent.member_api.get_operations_query_service",
            create=True,
            return_value=query_service,
        ):
            response = self.client.get(
                "/api/recognitions/recent?limit=25",
                headers={"Origin": "http://localhost:5173"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": []})
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )
        query_service.recent_recognitions.assert_called_once_with(limit=25)

    def test_recent_recognitions_validates_limit(self):
        self.assertEqual(
            self.client.get("/api/recognitions/recent?limit=0").status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/api/recognitions/recent?limit=101").status_code,
            422,
        )

    def test_operations_routes_are_get_only(self):
        self.assertEqual(self.client.post("/api/operations/status").status_code, 405)
        self.assertEqual(self.client.post("/api/recognitions/recent").status_code, 405)

    def test_operations_routes_use_member_service_session_factory(self):
        status_response = self.client.get("/api/operations/status")
        recent_response = self.client.get("/api/recognitions/recent")

        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["api"]["state"], "online")
        self.assertEqual(recent_response.status_code, 200)
        self.assertIsInstance(recent_response.json()["items"], list)
        self.assertIs(
            get_operations_query_service().session_factory,
            self.service.session_factory,
        )

    def test_member_api_cors_allows_local_frontend_get_and_options(self):
        get_response = self.client.get(
            "/api/members/nonexistent",
            headers={"Origin": "http://localhost:5173"},
        )
        options_response = self.client.options(
            "/api/members/M001/profile",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

        self.assertEqual(
            get_response.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )
        self.assertEqual(options_response.status_code, 200)
        self.assertIn("GET", options_response.headers["access-control-allow-methods"])
        self.assertNotIn(
            "access-control-allow-credentials",
            options_response.headers,
        )

    def test_member_api_cors_rejects_unconfigured_origin(self):
        response = self.client.get(
            "/api/members/nonexistent",
            headers={"Origin": "http://example.com"},
        )

        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_member_api_cors_rejects_browser_write_requests(self):
        response = self.client.post(
            "/api/members",
            headers={"Origin": "http://localhost:5173"},
            json={"member_id": "M001", "name": "Alice"},
        )

        self.assertEqual(response.status_code, 405)
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertIsNone(self.service.get_member("M001"))

    def test_device_receiver_is_mounted_and_allows_non_browser_camera_post(self):
        response = self.client.post(
            "/api/v1/event/pull",
            json={
                "operator": "heartbeat",
                "deviceInfo": {"serialNumber": "unconfigured-camera"},
                "info": {"eventId": 71},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["operator"], "heartbeat-Ack")

    def test_device_receiver_rejects_browser_origin_post(self):
        response = self.client.post(
            "/api/v1/event/pull",
            headers={"Origin": "http://localhost:5173"},
            json={
                "operator": "heartbeat",
                "deviceInfo": {"serialNumber": "P6S-001"},
                "info": {"eventId": 72},
            },
        )

        self.assertEqual(response.status_code, 405)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_device_event_services_are_owned_by_app_state(self):
        sentinel = object()
        with patch(
            "agent.member_api._build_device_event_services",
            return_value=sentinel,
        ) as builder:
            first = get_device_event_services()
            second = get_device_event_services()

        self.assertIs(first, sentinel)
        self.assertIs(second, sentinel)
        self.assertIs(app.state.device_event_services, sentinel)
        builder.assert_called_once_with()

    def test_get_visits_api(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        response = self.client.get("/api/members/M001/visits")
        self.assertEqual(response.status_code, 200)
        self.assertIn("visits", response.json())

    def test_get_coupons_api(self):
        self.client.post("/api/members", json={"member_id": "M001", "name": "Alice"})
        response = self.client.get("/api/members/M001/coupons")
        self.assertEqual(response.status_code, 200)
        self.assertIn("coupons", response.json())


if __name__ == "__main__":
    unittest.main()
