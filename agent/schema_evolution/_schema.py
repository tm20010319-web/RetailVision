from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from . import _behavior, _members, _product_analytics, _recognition
from ._common import logger
from ._entry import evolve_customer_entries, evolve_visits


def evolve_schema(
    engine: Engine,
    *,
    preserve_existing_recognition_rows: bool = False,
) -> None:
    """Create ORM tables and apply all additive SQLite domain evolution."""
    from agent.models import Base

    Base.metadata.create_all(engine)
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    columns = {
        table: (
            {column["name"] for column in inspector.get_columns(table)}
            if inspector.has_table(table)
            else None
        )
        for table in (
            "members",
            "member_entry_events",
            "device_recognition_inbox",
            "customer_entry_events",
            "behavior_events",
            "visits",
            "track_association_evaluations",
            "track_identity_bindings",
            "track_association_finalization_jobs",
            "push_coverage_observations",
            "product_interaction_facts",
        )
    }
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name VARCHAR(128) PRIMARY KEY)"
        )
        _members.evolve(connection, columns["members"])
        _recognition.evolve_member_arrival(
            connection, columns["member_entry_events"]
        )
        _recognition.evolve_inbox(
            connection,
            columns["device_recognition_inbox"],
            preserve_existing_rows=preserve_existing_recognition_rows,
        )
        _recognition.evolve_track_associations(
            connection, columns["track_association_evaluations"]
        )
        _recognition.evolve_track_identity_bindings(
            connection,
            columns["track_identity_bindings"],
            columns["track_association_finalization_jobs"],
        )
        _recognition.evolve_push_coverage(
            connection, columns["push_coverage_observations"]
        )
        evolve_customer_entries(
            connection, columns["customer_entry_events"]
        )
        _behavior.evolve(connection, columns["behavior_events"])
        _product_analytics.evolve(
            connection, columns["product_interaction_facts"]
        )
        evolve_visits(
            connection,
            columns["visits"],
            columns["member_entry_events"],
        )
    logger.info("Domain schema evolution completed")
