from _test_bootstrap import install_runtime_stubs

install_runtime_stubs()

import unittest
from datetime import datetime

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from agent.schema_evolution import evolve_schema
from agent.legacy.member_entry_service import MemberEntryService
from agent.models import BusinessJob, Member, MemberEntryEvent, Visit


class MemberBusinessModelTests(unittest.TestCase):
    def test_fresh_sqlite_schema_supports_json_and_uuid_defaults(self):
        engine = create_engine("sqlite:///:memory:")
        evolve_schema(engine)
        db = sessionmaker(bind=engine)()
        try:
            db.add(Member(id="M001", name="member", profile={}))
            db.add(MemberEntryEvent(
                event_id="evt-1", device_id="cam-1", member_id="M001",
                occurred_at=datetime(2026, 7, 14, 12), similarity=0.91,
                identity_time=datetime(2026, 7, 14, 12),
            ))
            visit = Visit(member_id="M001", zones_visited=["entrance"], is_member=True)
            job = BusinessJob(
                event_id="evt-1", member_id="M001",
                task_type="customer_profile", payload={"crop_path": "crop.jpg"},
            )
            db.add_all([visit, job])
            db.commit()
            self.assertTrue(visit.id)
            self.assertTrue(job.id)
            self.assertEqual(["entrance"], db.get(Visit, visit.id).zones_visited)
        finally:
            db.close()

    def test_legacy_visits_table_gets_additive_columns(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE visits (id VARCHAR(64) PRIMARY KEY, member_id VARCHAR(64))"
            )
        evolve_schema(engine)
        names = {column["name"] for column in inspect(engine).get_columns("visits")}
        self.assertTrue({
            "device_event_id", "camera_id", "track_id",
            "detection_bbox", "identity_time",
        } <= names)

    def test_visits_declares_and_repeats_device_event_unique_index(self):
        declared_indexes = [
            index for index in Visit.__table__.indexes
            if index.unique
            and [column.name for column in index.columns] == ["device_event_id"]
        ]
        self.assertEqual(1, len(declared_indexes))

        engine = create_engine("sqlite:///:memory:")
        evolve_schema(engine)
        evolve_schema(engine)

        indexes = {index["name"]: index for index in inspect(engine).get_indexes("visits")}
        self.assertIn("uq_visits_device_event_id", indexes)
        self.assertEqual(1, indexes["uq_visits_device_event_id"]["unique"])

    def test_legacy_duplicate_device_event_ids_are_repaired_before_unique_index(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE visits ("
                "id VARCHAR(64) PRIMARY KEY, member_id VARCHAR(64), "
                "device_event_id VARCHAR(64), entry_time DATETIME, created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO visits (id, member_id, device_event_id, entry_time, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("visit-later", "M001", "event-1", "2026-07-14 12:00:00", "2026-07-14 12:01:00"),
            )
            connection.exec_driver_sql(
                "INSERT INTO visits (id, member_id, device_event_id, entry_time, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("visit-canonical", "M001", "event-1", "2026-07-14 11:00:00", "2026-07-14 12:02:00"),
            )
            connection.exec_driver_sql(
                "INSERT INTO visits (id, member_id, device_event_id, entry_time, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("visit-no-entry", "M001", "event-1", None, "2026-07-14 10:00:00"),
            )

        evolve_schema(engine)
        evolve_schema(engine)

        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT id, device_event_id FROM visits ORDER BY id"
            ).all()
        self.assertEqual(3, len(rows))
        self.assertEqual({
            "visit-canonical": "event-1",
            "visit-later": None,
            "visit-no-entry": None,
        }, dict(rows))
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("visits")}
        self.assertEqual(1, indexes["uq_visits_device_event_id"]["unique"])

    def test_legacy_duplicate_preserves_finalized_event_referenced_visit_for_replay(self):
        engine = create_engine("sqlite:///:memory:")
        Member.__table__.create(engine)
        MemberEntryEvent.__table__.create(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE visits ("
                "id VARCHAR(64) PRIMARY KEY, member_id VARCHAR(64), "
                "device_event_id VARCHAR(64), camera_id VARCHAR(64), track_id INTEGER, "
                "detection_bbox JSON, identity_time DATETIME, recognized_confidence DECIMAL(3, 2), "
                "entry_time DATETIME, exit_time DATETIME, total_duration_seconds INTEGER, "
                "path JSON, zones_visited JSON, is_member BOOLEAN, created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO visits (id, member_id, device_event_id, entry_time, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("visit-referenced", "M001", "event-1", "2026-07-14 12:00:00", "2026-07-14 12:00:00"),
            )
            connection.exec_driver_sql(
                "INSERT INTO visits (id, member_id, device_event_id, entry_time, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("visit-earlier", "M001", "event-1", "2026-07-14 11:00:00", "2026-07-14 11:00:00"),
            )

        session_factory = sessionmaker(bind=engine)
        db = session_factory()
        try:
            db.add(Member(id="M001", name="Member", visit_count=1))
            db.add(MemberEntryEvent(
                event_id="event-1",
                device_id="camera-1",
                member_id="M001",
                occurred_at=datetime(2026, 7, 14, 12),
                similarity=0.91,
                identity_time=datetime(2026, 7, 14, 12),
                status="finalized",
                visit_id="visit-referenced",
            ))
            db.commit()
        finally:
            db.close()

        evolve_schema(engine)
        evolve_schema(engine)

        with engine.connect() as connection:
            rows = dict(connection.exec_driver_sql(
                "SELECT id, device_event_id FROM visits ORDER BY id"
            ).all())
        self.assertEqual("event-1", rows["visit-referenced"])
        self.assertIsNone(rows["visit-earlier"])
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("visits")}
        self.assertEqual(1, indexes["uq_visits_device_event_id"]["unique"])

        result = MemberEntryService(session_factory, profile_enabled=False).finalize({
            "device_event_id": "event-1",
            "member_id": "M001",
        })
        self.assertEqual("visit-referenced", result.visit_id)
        db = session_factory()
        self.addCleanup(db.close)
        self.assertEqual("visit-referenced", db.get(MemberEntryEvent, "event-1").visit_id)
