from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from ._common import add_missing_columns


class ProductAnalyticsSchemaNotReadyError(RuntimeError):
    """Raised when the read-only analytics runtime cannot use its projection."""


_REQUIRED_INDEXES = {
    "idx_product_interaction_fact_fact_ref": (
        "analysis_run_id", "normalized_fact_ref",
    ),
    "idx_product_interaction_fact_scope_time": (
        "store_id", "camera_id", "started_at",
    ),
    "idx_product_interaction_fact_product_time": (
        "product_key", "started_at",
    ),
}
_UNIQUE_FACT_KEY = (
    "analysis_run_id",
    "interaction_session_id",
    "event_type",
)


def require_compatible_schema(engine: Engine) -> None:
    """Inspect the Product Analytics projection without executing DDL."""
    from agent.models import ProductInteractionFact

    try:
        inspector = inspect(engine)
        table_name = ProductInteractionFact.__tablename__
        if not inspector.has_table(table_name):
            raise ProductAnalyticsSchemaNotReadyError(
                "product_analytics_schema_incompatible"
            )
        actual_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        required_columns = {
            column.name for column in ProductInteractionFact.__table__.columns
        }
        if not required_columns.issubset(actual_columns):
            raise ProductAnalyticsSchemaNotReadyError(
                "product_analytics_schema_incompatible"
            )
        indexes = inspector.get_indexes(table_name)
        actual_indexes = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in indexes
        }
        if any(
            actual_indexes.get(name) != columns
            for name, columns in _REQUIRED_INDEXES.items()
        ):
            raise ProductAnalyticsSchemaNotReadyError(
                "product_analytics_schema_incompatible"
            )
        unique_keys = {
            tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table_name)
        }
        unique_keys.update(
            tuple(item.get("column_names") or ())
            for item in indexes
            if item.get("unique")
        )
        if _UNIQUE_FACT_KEY not in unique_keys:
            raise ProductAnalyticsSchemaNotReadyError(
                "product_analytics_schema_incompatible"
            )
    except ProductAnalyticsSchemaNotReadyError:
        raise
    except Exception as exc:
        raise ProductAnalyticsSchemaNotReadyError(
            "product_analytics_schema_incompatible"
        ) from exc


def evolve(connection, columns: set[str] | None) -> None:
    """Apply additive evolution for the anonymous analytics projection.

    The projection is intentionally independent of formal behavior publication;
    this migration only creates or extends its own table and indexes.
    """
    if columns is None:
        return
    add_missing_columns(connection, "product_interaction_facts", columns, {
        "source_video_id": "VARCHAR(192)",
        "normalized_fact_ref": "VARCHAR(96)",
        "artifact_manifest_hash": "VARCHAR(96)",
        "projection_version": "VARCHAR(64)",
    })
    connection.exec_driver_sql(
        "UPDATE product_interaction_facts "
        "SET projection_version = 'product_interaction_projection_v1' "
        "WHERE projection_version IS NULL"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_product_interaction_fact_fact_ref "
        "ON product_interaction_facts(analysis_run_id, normalized_fact_ref)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_product_interaction_fact_scope_time "
        "ON product_interaction_facts(store_id, camera_id, started_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_product_interaction_fact_product_time "
        "ON product_interaction_facts(product_key, started_at)"
    )
    if not _has_unique_fact_key(connection):
        # SQLite cannot ALTER TABLE to add a table constraint. Creating the
        # equivalent unique index preserves the same idempotency contract for
        # legacy tables and fails atomically when legacy rows conflict.
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_product_interaction_fact_run_session_type "
            "ON product_interaction_facts(analysis_run_id, interaction_session_id, event_type)"
        )


def _has_unique_fact_key(connection) -> bool:
    rows = connection.exec_driver_sql(
        "PRAGMA index_list('product_interaction_facts')"
    ).all()
    expected = (
        "analysis_run_id",
        "interaction_session_id",
        "event_type",
    )
    for row in rows:
        # SQLite returns (seq, name, unique, origin, partial).
        if not row[2]:
            continue
        columns = tuple(
            item[2]
            for item in connection.exec_driver_sql(
                f"PRAGMA index_info('{row[1]}')"
            ).all()
        )
        if columns == expected:
            return True
    return False
