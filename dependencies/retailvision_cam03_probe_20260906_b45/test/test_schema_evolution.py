from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agent.models import (
    Base,
    BehaviorEvent,
    DeviceMemberIdentityRevision,
    Member,
    MemberEntryEvent,
    TrackIdentityBinding,
    Visit,
)
from agent.schema_evolution import (
    EntryDomainSchemaNotReadyError,
    evolve_schema,
    require_entry_schema,
)


class SchemaEvolutionTests(unittest.TestCase):
    def test_fresh_schema_is_complete_and_entry_ready(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)

        evolve_schema(engine)
        require_entry_schema(engine)

        inspector = inspect(engine)
        self.assertTrue(inspector.has_table("device_recognition_inbox"))
        self.assertTrue(inspector.has_table("device_health_states"))
        self.assertIn(
            "snapshot_path",
            {
                column["name"]
                for column in inspector.get_columns("member_entry_events")
            },
        )
        inbox_indexes = {
            tuple(index["column_names"]): bool(index.get("unique"))
            for index in inspector.get_indexes("device_recognition_inbox")
        }
        self.assertTrue(
            inbox_indexes[("device_id", "source", "source_event_id")]
        )

        binding_columns = {
            column["name"]
            for column in inspect(engine).get_columns("track_identity_bindings")
        }
        job_columns = {
            column["name"]
            for column in inspect(engine).get_columns(
                "track_association_finalization_jobs"
            )
        }
        behavior_columns = {
            column["name"]
            for column in inspect(engine).get_columns("behavior_events")
        }
        self.assertIn("expires_at", binding_columns)
        self.assertTrue({"application_status", "application_reason"} <= job_columns)
        self.assertIn("binding_id", behavior_columns)

        analytics_columns = {
            column["name"]
            for column in inspect(engine).get_columns("product_interaction_facts")
        }
        self.assertTrue({
            "source_video_id",
            "normalized_fact_ref",
            "artifact_manifest_hash",
            "projection_version",
        } <= analytics_columns)
        analytics_indexes = {
            index["name"]
            for index in inspect(engine).get_indexes("product_interaction_facts")
        }
        self.assertIn("idx_product_interaction_fact_fact_ref", analytics_indexes)

        binding_indexes = {
            index["name"] for index in inspect(engine).get_indexes(
                "track_identity_bindings"
            )
        }
        job_indexes = {
            index["name"] for index in inspect(engine).get_indexes(
                "track_association_finalization_jobs"
            )
        }
        self.assertIn("uq_track_identity_binding_active_token", binding_indexes)
        with engine.connect() as connection:
            index_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='uq_track_identity_binding_active_token'"
            ).scalar_one()
        self.assertIn("WHERE status = 'confirmed'", index_sql)
        self.assertIn("valid_to IS NULL", index_sql)
        self.assertIn(
            "idx_track_finalization_job_recognition_status", job_indexes
        )

    def test_product_analytics_legacy_table_gets_indexes_and_unique_key(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE product_interaction_facts ("
                "id VARCHAR(64) PRIMARY KEY, "
                "analysis_job_id VARCHAR(128) NOT NULL, "
                "analysis_run_id VARCHAR(128) NOT NULL, "
                "store_id VARCHAR(64) NOT NULL, camera_id VARCHAR(64) NOT NULL, "
                "product_key VARCHAR(128) NOT NULL, product_name VARCHAR(256), "
                "interaction_session_id VARCHAR(192) NOT NULL, event_type VARCHAR(32) NOT NULL, "
                "started_at DATETIME NOT NULL, ended_at DATETIME NOT NULL, "
                "holding_duration_seconds FLOAT, track_key_hash VARCHAR(64) NOT NULL, "
                "created_at DATETIME NOT NULL)"
            )

        evolve_schema(engine)
        evolve_schema(engine)

        inspector = inspect(engine)
        index_names = {
            index["name"] for index in inspector.get_indexes(
                "product_interaction_facts"
            )
        }
        self.assertTrue({
            "idx_product_interaction_fact_fact_ref",
            "idx_product_interaction_fact_scope_time",
            "idx_product_interaction_fact_product_time",
            "uq_product_interaction_fact_run_session_type",
        } <= index_names)

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO product_interaction_facts "
                "(id, analysis_job_id, analysis_run_id, store_id, camera_id, product_key, "
                "interaction_session_id, event_type, started_at, ended_at, track_key_hash, created_at) "
                "VALUES ('one', 'job', 'run', 'store', 'cam', 'sku', 'session', "
                "'merchandise_pick_up', '2026-08-20 04:00:00', '2026-08-20 04:00:00', 'hash', "
                "'2026-08-20 04:00:00')"
            )
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO product_interaction_facts "
                    "(id, analysis_job_id, analysis_run_id, store_id, camera_id, product_key, "
                    "interaction_session_id, event_type, started_at, ended_at, track_key_hash, created_at) "
                    "VALUES ('two', 'job', 'run', 'store', 'cam', 'sku', 'session', "
                    "'merchandise_pick_up', '2026-08-20 04:00:00', '2026-08-20 04:00:00', 'hash', "
                    "'2026-08-20 04:00:00')"
                )

    def test_product_analytics_legacy_duplicate_rows_fail_migration(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE product_interaction_facts ("
                "id VARCHAR(64) PRIMARY KEY, analysis_job_id VARCHAR(128), "
                "analysis_run_id VARCHAR(128), store_id VARCHAR(64), camera_id VARCHAR(64), "
                "product_key VARCHAR(128), product_name VARCHAR(256), "
                "interaction_session_id VARCHAR(192), event_type VARCHAR(32), "
                "started_at DATETIME, ended_at DATETIME, holding_duration_seconds FLOAT, "
                "track_key_hash VARCHAR(64), created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO product_interaction_facts VALUES "
                "('one','job','run','store','cam','sku',NULL,'session','merchandise_pick_up',"
                "'2026-08-20 04:00:00','2026-08-20 04:00:00',NULL,'hash','2026-08-20 04:00:00'),"
                "('two','job','run','store','cam','sku',NULL,'session','merchandise_pick_up',"
                "'2026-08-20 04:00:00','2026-08-20 04:00:00',NULL,'hash','2026-08-20 04:00:00')"
            )

        with self.assertRaises(IntegrityError):
            evolve_schema(engine)

        with engine.connect() as connection:
            self.assertEqual(
                2,
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM product_interaction_facts"
                ).scalar_one(),
            )

    def test_product_analytics_migration_preserves_formal_domain_tables_and_rows(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine)
        now = datetime(2026, 8, 20, 4)
        with sessions() as db:
            db.add(Member(id="member-sentinel", name="Sentinel", gender="male"))
            db.add(Visit(
                id="visit-sentinel", member_id="member-sentinel", store_id="store-1",
                entry_time=now, is_member=True, visit_source="identity",
                verification_status="verified", visitor_type="member",
                identity_status="identified_member", member_visit_counted_at=now,
            ))
            db.add(BehaviorEvent(
                id="behavior-sentinel", event_key="behavior-sentinel",
                event_type="merchandise_pick_up", assessment="confirmed",
                camera_id="cam02", customer_track_id=1,
                customer_track_token="formal-track-sentinel",
                track_token="formal-track-sentinel", store_id="store-1",
                occurred_at=now, started_at=now, ended_at=now,
                details={"sentinel": True},
            ))
            db.add(DeviceMemberIdentityRevision(
                id="identity-sentinel", device_id="device-1", unique_id="person-1",
                member_id="member-sentinel", effective_from=now,
                mapping_enabled=True, mapping_sync_status="synced",
                member_principal_exists=True, member_principal_active=True,
            ))
            db.commit()

        formal_tables = (
            "behavior_events", "device_member_identity_revisions", "visits"
        )
        before_columns = {
            table: tuple(
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspect(engine).get_columns(table)
            )
            for table in formal_tables
        }
        with engine.connect() as connection:
            before_rows = {
                table: tuple(connection.exec_driver_sql(
                    f"SELECT * FROM {table} ORDER BY 1"
                ).all())
                for table in formal_tables
            }
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE product_interaction_facts")
            connection.exec_driver_sql(
                "CREATE TABLE product_interaction_facts ("
                "id VARCHAR(64) PRIMARY KEY, analysis_job_id VARCHAR(128), "
                "analysis_run_id VARCHAR(128), store_id VARCHAR(64), camera_id VARCHAR(64), "
                "product_key VARCHAR(128), product_name VARCHAR(256), "
                "interaction_session_id VARCHAR(192), event_type VARCHAR(32), "
                "started_at DATETIME, ended_at DATETIME, holding_duration_seconds FLOAT, "
                "track_key_hash VARCHAR(64), created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO product_interaction_facts VALUES "
                "('legacy','job','run','store-1','cam02','sku',NULL,'session',"
                "'merchandise_pick_up','2026-08-20 04:00:00','2026-08-20 04:00:00',"
                "NULL,'hash','2026-08-20 04:00:00')"
            )

        evolve_schema(engine)
        evolve_schema(engine)

        after_columns = {
            table: tuple(
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspect(engine).get_columns(table)
            )
            for table in formal_tables
        }
        with engine.connect() as connection:
            after_rows = {
                table: tuple(connection.exec_driver_sql(
                    f"SELECT * FROM {table} ORDER BY 1"
                ).all())
                for table in formal_tables
            }
            self.assertEqual(
                1,
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM product_interaction_facts"
                ).scalar_one(),
            )
        self.assertEqual(before_columns, after_columns)
        self.assertEqual(before_rows, after_rows)

    def test_phase2b_schema_evolution_is_idempotent_and_additive(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)

        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE track_identity_bindings DROP COLUMN expires_at"
            )
            connection.exec_driver_sql(
                "ALTER TABLE track_association_finalization_jobs "
                "DROP COLUMN application_status"
            )
            connection.exec_driver_sql(
                "ALTER TABLE track_association_finalization_jobs "
                "DROP COLUMN application_reason"
            )
            connection.exec_driver_sql("DROP TABLE behavior_events")
            connection.exec_driver_sql(
                "CREATE TABLE behavior_events ("
                "id VARCHAR(64) PRIMARY KEY, "
                "event_key VARCHAR(192), "
                "customer_track_token VARCHAR(128))"
            )

        evolve_schema(engine)
        evolve_schema(engine)

        self.assertIn(
            "expires_at",
            {column["name"] for column in inspect(engine).get_columns(
                "track_identity_bindings"
            )},
        )
        self.assertIn(
            "application_status",
            {column["name"] for column in inspect(engine).get_columns(
                "track_association_finalization_jobs"
            )},
        )
        self.assertIn(
            "binding_id",
            {column["name"] for column in inspect(engine).get_columns(
                "behavior_events"
            )},
        )

    def test_active_binding_unique_constraint_uses_full_track_token(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine)

        with sessions() as db:
            first = TrackIdentityBinding(
                recognition_event_id="event-a",
                evaluation_id="evaluation-a",
                store_id="store-1",
                camera_id="entrance-1",
                runtime_owner_id="owner-1",
                runtime_instance_id="runtime-1",
                track_id=7,
                track_token="store-1:entrance-1:runtime-1:7",
                member_id="member-a",
                visit_id="visit-a",
                binding_level="confirmed",
                status="confirmed",
                identity_source="device_api",
                valid_from=datetime(2026, 8, 9, 10),
            )
            second_track = TrackIdentityBinding(
                recognition_event_id="event-b",
                evaluation_id="evaluation-b",
                store_id="store-1",
                camera_id="entrance-1",
                runtime_owner_id="owner-1",
                runtime_instance_id="runtime-1",
                track_id=7,
                track_token="store-1:entrance-1:runtime-1:8",
                member_id="member-a",
                visit_id="visit-a",
                binding_level="confirmed",
                status="confirmed",
                identity_source="device_api",
                valid_from=datetime(2026, 8, 9, 10),
            )
            db.add_all((first, second_track))
            db.commit()

            duplicate = TrackIdentityBinding(
                recognition_event_id="event-c",
                evaluation_id="evaluation-c",
                store_id="store-1",
                camera_id="entrance-1",
                runtime_owner_id="owner-1",
                runtime_instance_id="runtime-1",
                track_id=99,
                track_token="store-1:entrance-1:runtime-1:7",
                member_id="member-b",
                visit_id="visit-b",
                binding_level="confirmed",
                status="confirmed",
                identity_source="device_api",
                valid_from=datetime(2026, 8, 9, 10),
            )
            db.add(duplicate)
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_duplicate_unterminated_confirmed_bindings_fail_fast(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)

        with sessions() as db:
            for event_id, evaluation_id in (("event-a", "evaluation-a"), ("event-b", "evaluation-b")):
                db.add(TrackIdentityBinding(
                    recognition_event_id=event_id,
                    evaluation_id=evaluation_id,
                    store_id="store-1",
                    camera_id="entrance-1",
                    runtime_owner_id="owner-1",
                    runtime_instance_id="runtime-1",
                    track_id=7,
                    track_token="store-1:entrance-1:runtime-1:7",
                    member_id="member-a",
                    visit_id="visit-a",
                    binding_level="confirmed",
                    status="confirmed",
                    identity_source="device_api",
                    valid_from=datetime(2026, 8, 9, 10),
                ))
            db.commit()

        with self.assertLogs("agent.schema_evolution", level="ERROR") as logs:
            with self.assertRaisesRegex(
                RuntimeError, "track_identity_binding_active_duplicate"
            ):
                evolve_schema(engine)

        self.assertIn(
            "duplicate unterminated confirmed TrackIdentityBinding",
            "\n".join(logs.output),
        )
        self.assertNotIn("store-1:entrance-1:runtime-1:7", "\n".join(logs.output))
        index_names = {
            index["name"] for index in inspect(engine).get_indexes(
                "track_identity_bindings"
            )
        }
        self.assertNotIn("uq_track_identity_binding_active_token", index_names)

    def test_member_evolution_is_idempotent_and_preserves_manual_gender(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE members ("
                "id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO members (id, name) VALUES "
                "('member-huangmengyu', '黄梦瑜'), "
                "('member-maoyijie', '毛逸杰'), "
                "('member-renyu', '任瑜'), ('other', '张三')"
            )

        evolve_schema(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE members SET gender = 'unknown' WHERE id = 'other'"
            )
        evolve_schema(engine)

        columns = {
            column["name"] for column in inspect(engine).get_columns("members")
        }
        self.assertTrue({"department", "position", "gender"} <= columns)
        with engine.connect() as connection:
            rows = dict(connection.exec_driver_sql(
                "SELECT id, gender FROM members"
            ).all())
        self.assertEqual("female", rows["member-huangmengyu"])
        self.assertEqual("female", rows["member-maoyijie"])
        self.assertEqual("female", rows["member-renyu"])
        self.assertEqual("unknown", rows["other"])

    def test_recognition_and_member_arrival_evolution_preserves_rows(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE device_recognition_inbox ("
                "canonical_event_id VARCHAR(64) PRIMARY KEY, "
                "device_id VARCHAR(64) NOT NULL, source VARCHAR(16) NOT NULL, "
                "source_event_id VARCHAR(128) NOT NULL, "
                "occurred_at DATETIME NOT NULL, received_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO device_recognition_inbox "
                "(canonical_event_id, device_id, source, source_event_id, "
                "occurred_at, received_at) VALUES "
                "('canonical-a', 'entrance-1', 'push', 'camera-7', "
                "'2026-07-20 12:00:00', '2026-07-20 12:00:01'), "
                "('canonical-b', 'entrance-1', 'push', 'camera-7', "
                "'2026-07-20 12:01:00', '2026-07-20 12:01:01')"
            )
            connection.exec_driver_sql(
                "CREATE TABLE member_entry_events ("
                "event_id VARCHAR(64) PRIMARY KEY, device_id VARCHAR(64), "
                "member_id VARCHAR(64), occurred_at DATETIME, similarity FLOAT, "
                "identity_time DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO member_entry_events VALUES "
                "('evt-legacy', 'entrance-1', 'member-1', "
                "'2026-07-20 12:00:00', 0.9, '2026-07-20 12:00:00')"
            )

        evolve_schema(engine)

        inspector = inspect(engine)
        self.assertIn(
            "claim_token",
            {
                column["name"]
                for column in inspector.get_columns("device_recognition_inbox")
            },
        )
        self.assertIn(
            "snapshot_path",
            {
                column["name"]
                for column in inspector.get_columns("member_entry_events")
            },
        )
        with engine.connect() as connection:
            inbox_rows = connection.exec_driver_sql(
                "SELECT canonical_event_id, source_event_id "
                "FROM device_recognition_inbox ORDER BY canonical_event_id"
            ).all()
            arrival_id = connection.exec_driver_sql(
                "SELECT event_id FROM member_entry_events"
            ).scalar_one()
        self.assertEqual([
            ("canonical-a", "camera-7"),
            ("canonical-b", "legacy-duplicate:canonical-b"),
        ], inbox_rows)
        self.assertEqual("evt-legacy", arrival_id)

    def test_all_legacy_domain_rows_are_extended_without_relinking(self):
        path = Path.cwd() / f"schema-evolution-{uuid4().hex}.sqlite3"
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE members (id VARCHAR(64) PRIMARY KEY, "
                    "name VARCHAR(128), visit_count INTEGER, last_visit DATETIME)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE visits (id VARCHAR(64) PRIMARY KEY, "
                    "member_id VARCHAR(64), entry_time DATETIME, "
                    "is_member BOOLEAN, created_at DATETIME)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE customer_entry_events ("
                    "id VARCHAR(64) PRIMARY KEY, entry_key VARCHAR(192), "
                    "camera_id VARCHAR(64), track_id INTEGER, "
                    "track_token VARCHAR(128), occurred_at DATETIME, "
                    "member_id VARCHAR(64), member_tier VARCHAR(20), "
                    "confidence FLOAT, created_at DATETIME)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE behavior_events (id VARCHAR(64) PRIMARY KEY, "
                    "event_key VARCHAR(192), customer_track_token VARCHAR(128))"
                )
                connection.exec_driver_sql(
                    "INSERT INTO members VALUES ('member-old', 'Old', 1, NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO visits VALUES ('visit-old', 'member-old', "
                    "'2026-07-20 10:00:00', 1, '2026-07-20 10:00:00')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO customer_entry_events VALUES "
                    "('entry-old', 'dedup-old', 'entrance-1', 7, 'runtime:7', "
                    "'2026-07-20 10:00:00', NULL, NULL, 0.9, "
                    "'2026-07-20 10:00:00')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO behavior_events VALUES "
                    "('behavior-old', 'behavior-key', 'runtime:7')"
                )

            evolve_schema(engine)
            evolve_schema(engine)

            with engine.connect() as connection:
                visit = connection.exec_driver_sql(
                    "SELECT visit_source, verification_status, entry_event_id, "
                    "member_visit_counted_at FROM visits WHERE id = 'visit-old'"
                ).one()
                entry = connection.exec_driver_sql(
                    "SELECT dedup_key, tracker_track_id, confidence "
                    "FROM customer_entry_events WHERE id = 'entry-old'"
                ).one()
                behavior = connection.exec_driver_sql(
                    "SELECT track_token, is_demo FROM behavior_events "
                    "WHERE id = 'behavior-old'"
                ).one()
            self.assertEqual(("legacy", "legacy", None), visit[:3])
            self.assertIsNotNone(visit.member_visit_counted_at)
            self.assertEqual(("dedup-old", 7, 0.9), entry)
            self.assertEqual(("runtime:7", 0), behavior)
        finally:
            engine.dispose()
            path.unlink(missing_ok=True)

    def test_visit_duplicate_repair_preserves_finalized_arrival_reference(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Member.__table__.create(engine)
        MemberEntryEvent.__table__.create(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE visits (id VARCHAR(64) PRIMARY KEY, "
                "member_id VARCHAR(64), device_event_id VARCHAR(64), "
                "entry_time DATETIME, created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO visits VALUES "
                "('visit-referenced', 'M001', 'event-1', "
                "'2026-07-14 12:00:00', '2026-07-14 12:00:00'), "
                "('visit-earlier', 'M001', 'event-1', "
                "'2026-07-14 11:00:00', '2026-07-14 11:00:00')"
            )
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
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

        evolve_schema(engine)
        evolve_schema(engine)

        with engine.connect() as connection:
            rows = dict(connection.exec_driver_sql(
                "SELECT id, device_event_id FROM visits ORDER BY id"
            ).all())
        self.assertEqual("event-1", rows["visit-referenced"])
        self.assertIsNone(rows["visit-earlier"])

    def test_entry_conflict_preserves_rows_and_fails_readiness(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE customer_entry_events ("
                "id VARCHAR(64) PRIMARY KEY, entry_key VARCHAR(192), "
                "camera_id VARCHAR(64), track_id INTEGER, "
                "track_token VARCHAR(128), occurred_at DATETIME, "
                "member_id VARCHAR(64), member_tier VARCHAR(20), "
                "confidence FLOAT, created_at DATETIME)"
            )
            for event_id in ("entry-1", "entry-2"):
                connection.exec_driver_sql(
                    "INSERT INTO customer_entry_events VALUES "
                    "(?, 'duplicate-key', 'entrance-1', 7, 'runtime:7', "
                    "'2026-07-20 10:00:00', NULL, NULL, 0.9, "
                    "'2026-07-20 10:00:00')",
                    (event_id,),
                )

        with self.assertLogs("agent.schema_evolution", level="ERROR") as logs:
            evolve_schema(engine)

        with engine.connect() as connection:
            self.assertEqual(
                2,
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM customer_entry_events"
                ).scalar_one(),
            )
        self.assertIn("duplicates", "\n".join(logs.output))
        with self.assertRaisesRegex(
            EntryDomainSchemaNotReadyError,
            "uq_customer_entry_dedup_key",
        ):
            require_entry_schema(engine)


if __name__ == "__main__":
    unittest.main()
