from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agent.device_identity import (
    DeviceMemberMappingData,
    DeviceRecognitionCursorData,
    SqlAlchemyDeviceIdentityRepository,
)
from agent.models import (
    Base,
    DeviceMemberMapping,
    DeviceRecognitionCursor,
    Member,
)


class TrackingSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.close_calls = 0
        self.created_thread_id = None

    def close(self):
        self.close_calls += 1
        super().close()


class TrackingSessionFactory:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self.sessions = []

    def __call__(self):
        session = self._session_factory()
        session.created_thread_id = threading.get_ident()
        self.sessions.append(session)
        return session


class SqlDeviceIdentityRepositoryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Member.__table__.create(engine)
        DeviceMemberMapping.__table__.create(engine)
        DeviceRecognitionCursor.__table__.create(engine)
        session_factory = sessionmaker(bind=engine)
        session = session_factory()
        session.add(Member(id="member-1", name="Member", is_active=True))
        session.commit()
        session.close()
        self.repository = SqlAlchemyDeviceIdentityRepository(session_factory)

    def test_mapping_and_cursor_work_on_sqlite(self):
        now = datetime(2026, 7, 14, 12, 0, 0)
        self.repository.save_mapping(
            DeviceMemberMappingData(
                device_id="front-door",
                member_id="member-1",
                group_id=2,
                group_uuid="group-uuid",
                person_id=19,
                unique_id="vendor-19",
                face_uuid="face-uuid",
                sync_status="synced",
            )
        )
        resolved = self.repository.resolve_mapping(
            "front-door", 2, 19, "vendor-19"
        )
        self.assertEqual("member-1", resolved.member_id)

        self.repository.save_cursor(
            DeviceRecognitionCursorData(
                device_id="front-door",
                last_log_id=8,
                last_record_time=now,
                last_success_at=now,
            )
        )
        self.assertEqual(8, self.repository.get_cursor("front-door").last_log_id)

    def test_new_schema_does_not_create_legacy_processed_ledger(self):
        self.assertNotIn("processed_device_recognitions", Base.metadata.tables)

    def test_new_tables_do_not_use_array_columns(self):
        for table in (
            DeviceMemberMapping.__table__,
            DeviceRecognitionCursor.__table__,
        ):
            for column in table.columns:
                self.assertNotEqual("ARRAY", column.type.__class__.__name__)

    def test_repository_operations_use_and_close_new_sessions_in_worker_thread(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        for model in (
            Member,
            DeviceMemberMapping,
            DeviceRecognitionCursor,
        ):
            model.__table__.create(engine)
        bootstrap_session = sessionmaker(bind=engine)()
        bootstrap_session.add(Member(id="member-thread", name="Member", is_active=True))
        bootstrap_session.commit()
        bootstrap_session.close()
        factory = TrackingSessionFactory(
            sessionmaker(bind=engine, class_=TrackingSession)
        )
        repository = SqlAlchemyDeviceIdentityRepository(factory)
        now = datetime(2026, 7, 14, 12, 0, 0)
        mapping = DeviceMemberMappingData(
            device_id="front-door",
            member_id="member-thread",
            group_id=2,
            group_uuid="group-uuid",
            person_id=19,
            unique_id="vendor-19",
            face_uuid="face-uuid",
            sync_status="synced",
        )
        errors = []
        worker_thread_id = []

        def run_operations():
            worker_thread_id.append(threading.get_ident())
            try:
                repository.save_mapping(mapping)
                repository.get_mapping("front-door", "member-thread")
                repository.resolve_mapping("front-door", 2, 19, "vendor-19")
                repository.save_cursor(
                    DeviceRecognitionCursorData(
                        device_id="front-door",
                        last_log_id=8,
                        last_record_time=now,
                        last_success_at=now,
                    )
                )
                repository.get_cursor("front-door")
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=run_operations)
        worker.start()
        worker.join()

        self.assertEqual([], errors)
        self.assertEqual(5, len(factory.sessions))
        self.assertEqual(
            [worker_thread_id[0]] * 5,
            [session.created_thread_id for session in factory.sessions],
        )
        self.assertEqual([1] * 5, [session.close_calls for session in factory.sessions])


if __name__ == "__main__":
    unittest.main()
