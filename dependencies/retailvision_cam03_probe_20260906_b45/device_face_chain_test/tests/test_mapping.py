import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agent.models import Base, DeviceMemberMapping, Member
from device_face_chain_test.mapping import MappingResult, SqliteMappingResolver


class TrackingSession(Session):
    closed_sessions = []

    def close(self):
        self.closed_sessions.append(self)
        super().close()


class MappingResolverTests(unittest.TestCase):
    def setUp(self):
        self.file_database_paths = []
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        for path in self.file_database_paths:
            path.unlink(missing_ok=True)

    def file_database_url(self):
        directory = Path("outputs/device_face_chain_test_tests")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"mapping-{uuid4().hex}.sqlite"
        self.file_database_paths.append(path)
        return path, f"sqlite:///{path.as_posix()}"

    def create_file_backed_mapping(self, database_url):
        engine = create_engine(database_url)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        session = factory()
        try:
            session.add(Member(id="file-member", name="File Member", is_active=True))
            session.add(
                DeviceMemberMapping(
                    id="file-mapping",
                    device_id="file-camera",
                    member_id="file-member",
                    group_uuid="file-group",
                    unique_id="file-uid",
                    face_uuid="file-face",
                    sync_status="synced",
                    is_enabled=True,
                )
            )
            session.commit()
        finally:
            session.close()
            engine.dispose()

    def add_mapping(
        self,
        *,
        device_id="camera-1",
        unique_id="platform-uid-1",
        member_active=True,
        mapping_enabled=True,
        sync_status="synced",
    ):
        session = self.session_factory()
        try:
            session.add(
                Member(
                    id="member-1",
                    name="Test Member",
                    is_active=member_active,
                )
            )
            session.add(
                DeviceMemberMapping(
                    id="mapping-1",
                    device_id=device_id,
                    member_id="member-1",
                    group_uuid="group-1",
                    unique_id=unique_id,
                    face_uuid="face-1",
                    sync_status=sync_status,
                    is_enabled=mapping_enabled,
                )
            )
            session.commit()
        finally:
            session.close()

    def resolver(self):
        return SqliteMappingResolver(
            "sqlite:///:memory:",
            "camera-1",
            session_factory=self.session_factory,
        )

    def test_resolves_one_active_enabled_synced_mapping(self):
        self.add_mapping()

        result = self.resolver().resolve("platform-uid-1")

        self.assertEqual(
            result,
            MappingResult(
                member_id="member-1",
                logical_device_id="camera-1",
                unique_id="platform-uid-1",
            ),
        )

    def test_missing_unique_id_returns_none(self):
        self.add_mapping()

        self.assertIsNone(self.resolver().resolve("unknown-uid"))

    def test_blank_unique_id_returns_none_without_opening_a_session(self):
        calls = []

        resolver = SqliteMappingResolver(
            "sqlite:///:memory:",
            "camera-1",
            session_factory=lambda: calls.append("opened"),
        )

        self.assertIsNone(resolver.resolve("   "))
        self.assertEqual(calls, [])

    def test_different_logical_device_id_does_not_resolve(self):
        self.add_mapping(device_id="camera-2")

        self.assertIsNone(self.resolver().resolve("platform-uid-1"))

    def test_inactive_member_does_not_resolve(self):
        self.add_mapping(member_active=False)

        self.assertIsNone(self.resolver().resolve("platform-uid-1"))

    def test_disabled_mapping_does_not_resolve(self):
        self.add_mapping(mapping_enabled=False)

        self.assertIsNone(self.resolver().resolve("platform-uid-1"))

    def test_pending_mapping_does_not_resolve(self):
        self.add_mapping(sync_status="pending")

        self.assertIsNone(self.resolver().resolve("platform-uid-1"))

    def test_session_is_closed_after_successful_and_failed_lookups(self):
        self.add_mapping()
        TrackingSession.closed_sessions = []
        tracking_factory = sessionmaker(bind=self.engine, class_=TrackingSession)
        resolver = SqliteMappingResolver(
            "sqlite:///:memory:",
            "camera-1",
            session_factory=tracking_factory,
        )

        resolver.resolve("platform-uid-1")
        resolver.resolve("missing-uid")

        self.assertEqual(len(TrackingSession.closed_sessions), 2)

    def test_non_sqlite_database_url_is_rejected_when_building_an_engine(self):
        with self.assertRaisesRegex(ValueError, "SQLite"):
            SqliteMappingResolver("postgresql://localhost/test", "camera-1")

    def test_file_backed_sqlite_uses_read_only_mode_without_creating_missing_databases(self):
        existing_path, existing_database_url = self.file_database_url()
        self.create_file_backed_mapping(existing_database_url)

        resolver = SqliteMappingResolver(existing_database_url, "file-camera")
        try:
            self.assertEqual(
                resolver.resolve("file-uid"),
                MappingResult(
                    member_id="file-member",
                    logical_device_id="file-camera",
                    unique_id="file-uid",
                ),
            )
            self.assertEqual(resolver._engine.url.query["mode"], "ro")
            self.assertEqual(resolver._engine.url.query["uri"], "true")
            self.assertTrue(existing_path.exists())
        finally:
            resolver._engine.dispose()

        missing_path, missing_database_url = self.file_database_url()
        self.assertFalse(missing_path.exists())
        resolver = SqliteMappingResolver(missing_database_url, "file-camera")
        try:
            with self.assertRaises(OperationalError):
                resolver.resolve("file-uid")

            self.assertFalse(missing_path.exists())
        finally:
            resolver._engine.dispose()

    def test_blank_logical_device_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "logical device ID"):
            SqliteMappingResolver("sqlite:///:memory:", "   ")


if __name__ == "__main__":
    unittest.main()
