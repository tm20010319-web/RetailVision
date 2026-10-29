from __future__ import annotations

import logging

logger = logging.getLogger("agent.schema_evolution")


def add_missing_columns(connection, table: str, existing_columns: set[str] | None, required_columns: dict[str, str]) -> None:
    if existing_columns is None:
        return
    for name, column_type in required_columns.items():
        if name not in existing_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {name} {column_type}"
            )


def create_unique_index_if_clean(connection, *, table: str, column: str, index: str) -> bool:
    conflicts = connection.exec_driver_sql(
        f"SELECT {column}, COUNT(*) FROM {table} "
        f"WHERE {column} IS NOT NULL GROUP BY {column} HAVING COUNT(*) > 1"
    ).all()
    if conflicts:
        logger.error(
            "Schema migration conflict: table=%s column=%s duplicates=%s; "
            "unique index %s was not created",
            table,
            column,
            conflicts,
            index,
        )
        return False
    connection.exec_driver_sql(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table}({column}) "
        f"WHERE {column} IS NOT NULL"
    )
    return True


def migration_applied(connection, name: str) -> bool:
    return connection.exec_driver_sql(
        "SELECT 1 FROM schema_migrations WHERE name = ?",
        (name,),
    ).first() is not None


def record_migration(connection, name: str) -> None:
    connection.exec_driver_sql(
        "INSERT INTO schema_migrations (name) VALUES (?)",
        (name,),
    )
