from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.mcp_server.migrate import main as migrate_main  # noqa: E402
from agent.mcp_server.server import (  # noqa: E402
    ProductAnalyticsMCPServer,
    build_default_server,
)
from agent.schema_evolution import MemberBehaviorSchemaNotReadyError  # noqa: E402
from agent.models import BehaviorEvent, Member, Visit  # noqa: E402
from agent.schema_evolution import (  # noqa: E402
    ProductAnalyticsSchemaNotReadyError,
    evolve_schema,
)


class ProductAnalyticsMCPSchemaReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.environment = patch.dict(
            "os.environ",
            {
                "RETAILVISION_STORE_ID": "store-1",
                "RETAILVISION_CAMERA_ID": "cam02",
                "RETAILVISION_ANALYSIS_START_AT": "2026-08-20T13:00:00",
                "RETAILVISION_ANALYSIS_END_AT": "2026-08-20T14:00:00",
                "RETAILVISION_PRODUCT_CATALOG_PROFILE": "stage10_smart_toys_v1",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _url(path: Path) -> str:
        return "sqlite:///" + path.as_posix()

    def test_disabled_server_does_not_create_or_inspect_database(self) -> None:
        database = self.root / "disabled.db"
        with patch(
            "agent.mcp_server.server.require_product_analytics_schema"
        ) as inspect_schema:
            server = build_default_server(
                database_url=self._url(database), enabled=False
            )
        self.assertFalse(database.exists())
        inspect_schema.assert_not_called()
        initialized = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertIn("result", initialized)

    def test_enabled_missing_database_fails_without_creating_file(self) -> None:
        database = self.root / "missing.db"
        with self.assertRaisesRegex(
            ProductAnalyticsSchemaNotReadyError,
            "product_analytics_schema_incompatible",
        ):
            build_default_server(database_url=self._url(database), enabled=True)
        self.assertFalse(database.exists())

    def test_enabled_missing_catalog_fails_before_database_access(self) -> None:
        database = self.root / "catalog-required.db"
        with patch.dict(
            "os.environ", {"RETAILVISION_PRODUCT_CATALOG_PROFILE": ""}, clear=False
        ):
            with self.assertRaisesRegex(ValueError, "product_catalog_required"):
                build_default_server(database_url=self._url(database), enabled=True)
        self.assertFalse(database.exists())

    def test_enabled_incompatible_schema_fails_closed(self) -> None:
        database = self.root / "incompatible.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE product_interaction_facts (id TEXT)")
            connection.commit()
        with self.assertRaisesRegex(
            ProductAnalyticsSchemaNotReadyError,
            "product_analytics_schema_incompatible",
        ):
            build_default_server(database_url=self._url(database), enabled=True)

    def test_same_name_index_on_wrong_columns_is_incompatible(self) -> None:
        database = self.root / "wrong-index.db"
        url = self._url(database)
        engine = create_engine(url)
        evolve_schema(engine)
        engine.dispose()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "DROP INDEX idx_product_interaction_fact_scope_time"
            )
            connection.execute(
                "CREATE INDEX idx_product_interaction_fact_scope_time "
                "ON product_interaction_facts(product_key, started_at)"
            )
            connection.commit()
        with self.assertRaisesRegex(
            ProductAnalyticsSchemaNotReadyError,
            "product_analytics_schema_incompatible",
        ):
            build_default_server(database_url=url, enabled=True)

    def test_enabled_compatible_schema_starts_without_ddl(self) -> None:
        database = self.root / "compatible.db"
        engine = create_engine(self._url(database))
        evolve_schema(engine)
        engine.dispose()
        before = self._schema(database)

        server = build_default_server(
            database_url=self._url(database), enabled=True
        )

        self.assertTrue(server.enabled)
        self.assertEqual(before, self._schema(database))
        server.close()

    def test_enabled_sqlite_connections_are_query_only(self) -> None:
        database = self.root / "query-only.db"
        engine = create_engine(self._url(database))
        evolve_schema(engine)
        engine.dispose()
        server = build_default_server(
            database_url=self._url(database), enabled=True
        )
        session = server.dispatcher.adapter.query_service.session_factory()
        try:
            self.assertEqual(1, session.execute(text("PRAGMA query_only")).scalar())
        finally:
            session.close()
            server.close()

    def test_member_only_server_does_not_require_product_catalog(self) -> None:
        database = self.root / "member-only.db"
        engine = create_engine(self._url(database))
        from agent.models import Base
        Base.metadata.create_all(engine)
        engine.dispose()
        with patch.dict(
            "os.environ",
            {
                "RETAILVISION_PRODUCT_CATALOG_PROFILE": "",
                "RETAILVISION_PRODUCT_ALIASES": "",
                "RETAILVISION_TIMEZONE": "Asia/Shanghai",
            },
            clear=False,
        ):
            server = build_default_server(
                database_url=self._url(database),
                product_enabled=False,
                member_enabled=True,
            )
        try:
            listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(2, len(server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})["result"]["tools"]))
            self.assertNotIn("Product Analytics", listed["result"].get("instructions", ""))
        finally:
            server.close()

    def test_member_only_server_requires_timezone_without_touching_product_catalog(self) -> None:
        database = self.root / "member-timezone-required.db"
        engine = create_engine(self._url(database))
        from agent.models import Base
        Base.metadata.create_all(engine)
        engine.dispose()
        with patch.dict(
            "os.environ",
            {
                "RETAILVISION_PRODUCT_CATALOG_PROFILE": "",
                "RETAILVISION_PRODUCT_ALIASES": "",
                "RETAILVISION_TIMEZONE": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "member_behavior_timezone_required"):
                build_default_server(
                    database_url=self._url(database),
                    product_enabled=False,
                    member_enabled=True,
                )

    def test_both_features_require_both_schemas(self) -> None:
        database = self.root / "both-incompatible.db"
        engine = create_engine(self._url(database))
        from agent.models import Base
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE behavior_events")
        engine.dispose()
        with patch.dict("os.environ", {"RETAILVISION_TIMEZONE": "Asia/Shanghai"}, clear=False):
            with self.assertRaisesRegex(MemberBehaviorSchemaNotReadyError, "member_behavior_schema_incompatible"):
                build_default_server(
                    database_url=self._url(database),
                    product_enabled=True,
                    member_enabled=True,
                )

    def test_close_is_idempotent(self) -> None:
        close_count = 0

        def close_callback() -> None:
            nonlocal close_count
            close_count += 1

        server = ProductAnalyticsMCPServer(
            None, enabled=False, close_callback=close_callback
        )
        server.close()
        server.close()
        self.assertEqual(1, close_count)

    def test_explicit_migration_makes_missing_schema_startable(self) -> None:
        database = self.root / "migrated.db"
        self.assertEqual(
            0, migrate_main(["--database-url", self._url(database)])
        )
        before = self._schema(database)
        server = build_default_server(
            database_url=self._url(database), enabled=True
        )
        self.assertTrue(server.enabled)
        self.assertEqual(before, self._schema(database))
        server.close()

    def test_readiness_failure_and_repeated_migration_preserve_formal_domains(self) -> None:
        database = self.root / "formal-domains.db"
        url = self._url(database)
        engine = create_engine(url)
        evolve_schema(engine)
        sessions = sessionmaker(bind=engine)
        db = sessions()
        try:
            db.add(Member(id="member-sentinel", name="Sentinel"))
            db.add(Visit(
                id="visit-sentinel", store_id="store-sentinel",
                visitor_type="anonymous", identity_status="anonymous",
            ))
            db.add(BehaviorEvent(
                id="behavior-sentinel", event_key="event-sentinel",
                event_type="sentinel", assessment="sentinel",
                camera_id="camera-sentinel", customer_track_id=1,
                customer_track_token="formal-sentinel",
                track_token="formal-sentinel",
                occurred_at=datetime(2026, 8, 20, 13),
                started_at=datetime(2026, 8, 20, 13),
                ended_at=datetime(2026, 8, 20, 13),
                details={},
            ))
            db.commit()
        finally:
            db.close()
            engine.dispose()
        before = self._formal_snapshot(database)

        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "DROP INDEX idx_product_interaction_fact_scope_time"
            )
            connection.commit()
        with self.assertRaisesRegex(
            ProductAnalyticsSchemaNotReadyError,
            "product_analytics_schema_incompatible",
        ):
            build_default_server(database_url=url, enabled=True)
        self.assertEqual(before, self._formal_snapshot(database))

        engine = create_engine(url)
        evolve_schema(engine)
        evolve_schema(engine)
        engine.dispose()
        self.assertEqual(before, self._formal_snapshot(database))

    @staticmethod
    def _schema(database: Path) -> list[tuple[str, str, str | None]]:
        with closing(sqlite3.connect(database)) as connection:
            return connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()

    @staticmethod
    def _formal_snapshot(database: Path) -> dict[str, object]:
        tables = ("behavior_events", "members", "visits")
        with closing(sqlite3.connect(database)) as connection:
            return {
                table: {
                    "schema": connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()[0],
                    "rows": connection.execute(
                        f'SELECT * FROM "{table}" ORDER BY 1'
                    ).fetchall(),
                }
                for table in tables
            }


if __name__ == "__main__":
    unittest.main()
