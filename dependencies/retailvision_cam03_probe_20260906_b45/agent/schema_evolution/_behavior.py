from __future__ import annotations

from ._common import add_missing_columns


def evolve(connection, columns: set[str] | None) -> None:
    if columns is None:
        return
    add_missing_columns(connection, "behavior_events", columns, {
        "track_token": "VARCHAR(256)",
        "entry_event_id": "VARCHAR(64)",
        "visit_id": "VARCHAR(64)",
        "store_id": "VARCHAR(64)",
        "is_demo": "BOOLEAN",
        "staff_name": "VARCHAR(128)",
        "staff_attribution_source": "VARCHAR(32)",
        "identity_status": "VARCHAR(24)",
        "identity_source": "VARCHAR(32)",
        "identity_confidence": "FLOAT",
        "identity_binding_level": "VARCHAR(24)",
        "binding_id": "VARCHAR(64)",
        "backfilled_at": "DATETIME",
        "occurred_at": "DATETIME",
        "evaluation_id": "VARCHAR(64)",
        "policy_version_id": "VARCHAR(64)",
        "algorithm_version": "VARCHAR(64)",
        "parameter_version": "VARCHAR(64)",
    })
    connection.exec_driver_sql(
        "UPDATE behavior_events SET track_token = customer_track_token "
        "WHERE track_token IS NULL"
    )
    connection.exec_driver_sql(
        "UPDATE behavior_events SET is_demo = 0 WHERE is_demo IS NULL"
    )
    occurred_source = (
        "started_at"
        if "started_at" in columns
        else "created_at"
        if "created_at" in columns
        else "CURRENT_TIMESTAMP"
    )
    connection.exec_driver_sql(
        "UPDATE behavior_events SET occurred_at = "
        f"{occurred_source} WHERE occurred_at IS NULL"
    )
