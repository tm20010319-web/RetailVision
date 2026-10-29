from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from ._common import add_missing_columns, create_unique_index_if_clean, logger


class EntryDomainSchemaNotReadyError(RuntimeError):
    pass


def evolve_customer_entries(connection, columns: set[str] | None) -> None:
    if columns is None:
        return
    add_missing_columns(connection, "customer_entry_events", columns, {
        "dedup_key": "VARCHAR(192)",
        "store_id": "VARCHAR(64)",
        "tracker_track_id": "INTEGER",
        "runtime_session_id": "VARCHAR(64)",
        "direction": "VARCHAR(32)",
        "confirmed_at": "DATETIME",
        "staff_status": "VARCHAR(24)",
        "staff_source": "VARCHAR(32)",
        "staff_confidence": "FLOAT",
        "schema_version": "INTEGER",
    })
    connection.exec_driver_sql(
        "UPDATE customer_entry_events SET "
        "dedup_key = COALESCE(dedup_key, entry_key), "
        "store_id = COALESCE(store_id, 'legacy'), "
        "tracker_track_id = COALESCE(tracker_track_id, track_id), "
        "runtime_session_id = COALESCE(runtime_session_id, 'legacy'), "
        "direction = COALESCE(direction, 'outside_to_inside'), "
        "confirmed_at = COALESCE(confirmed_at, occurred_at), "
        "staff_status = COALESCE(staff_status, 'customer'), "
        "staff_source = COALESCE(staff_source, 'legacy_behavior'), "
        "schema_version = COALESCE(schema_version, 1)"
    )
    create_unique_index_if_clean(
        connection,
        table="customer_entry_events",
        column="dedup_key",
        index="uq_customer_entry_dedup_key",
    )


def evolve_visits(connection, columns: set[str] | None, member_entry_columns: set[str] | None) -> None:
    if columns is None:
        return
    required_columns = {
        "device_event_id": "VARCHAR(64)",
        "store_id": "VARCHAR(64)",
        "camera_id": "VARCHAR(64)",
        "track_id": "INTEGER",
        "detection_bbox": "JSON",
        "identity_time": "DATETIME",
        "entry_event_id": "VARCHAR(64)",
        "visitor_type": "VARCHAR(24)",
        "identity_status": "VARCHAR(32)",
        "identity_source": "VARCHAR(32)",
        "identity_confidence": "FLOAT",
        "identified_at": "DATETIME",
        "visit_source": "VARCHAR(32)",
        "verification_status": "VARCHAR(24)",
        "member_visit_counted_at": "DATETIME",
        "last_seen_at": "DATETIME",
        "closed_reason": "VARCHAR(64)",
        "superseded_by_visit_id": "VARCHAR(64)",
        "superseded_at": "DATETIME",
        "superseded_reason": "VARCHAR(128)",
    }
    add_missing_columns(connection, "visits", columns, required_columns)
    member_predicate = "member_id IS NOT NULL"
    if "is_member" in columns:
        member_predicate += " OR is_member = 1"
    counted_time_columns = ["identity_time"]
    counted_time_columns.extend(
        name for name in ("entry_time", "created_at") if name in columns
    )
    counted_time_columns.append("CURRENT_TIMESTAMP")
    counted_time = ", ".join(counted_time_columns)
    connection.exec_driver_sql(
        "UPDATE visits SET "
        "visitor_type = COALESCE(visitor_type, CASE "
        f"WHEN {member_predicate} THEN 'member' ELSE 'anonymous' END), "
        "identity_status = COALESCE(identity_status, CASE "
        f"WHEN {member_predicate} THEN 'identified_member' ELSE 'anonymous' END), "
        "visit_source = COALESCE(visit_source, 'legacy'), "
        "verification_status = COALESCE(verification_status, 'legacy'), "
        "member_visit_counted_at = CASE "
        "WHEN member_visit_counted_at IS NULL "
        f"AND ({member_predicate}) THEN COALESCE({counted_time}) "
        "ELSE member_visit_counted_at END"
    )
    repair_duplicate_visit_device_event_ids(
        connection, columns, member_entry_columns
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_visits_device_event_id "
        "ON visits(device_event_id) WHERE device_event_id IS NOT NULL"
    )
    create_unique_index_if_clean(
        connection,
        table="visits",
        column="entry_event_id",
        index="uq_visits_entry_event_id",
    )


def repair_duplicate_visit_device_event_ids(connection, visit_columns: set[str], member_entry_columns: set[str] | None) -> None:
    duplicate_event_ids = connection.exec_driver_sql(
        "SELECT device_event_id FROM visits "
        "WHERE device_event_id IS NOT NULL "
        "GROUP BY device_event_id HAVING COUNT(*) > 1"
    ).all()
    order_by = []
    if "entry_time" in visit_columns:
        order_by.extend(["entry_time IS NULL", "entry_time"])
    if "created_at" in visit_columns:
        order_by.extend(["created_at IS NULL", "created_at"])
    order_by.append("id")

    repaired_rows = 0
    for (event_id,) in duplicate_event_ids:
        visit_ids = connection.exec_driver_sql(
            "SELECT id FROM visits WHERE device_event_id = ? ORDER BY "
            + ", ".join(order_by),
            (event_id,),
        ).scalars().all()
        canonical_visit_id = referenced_visit_id(
            connection, event_id, visit_ids, member_entry_columns
        ) or visit_ids[0]
        for visit_id in visit_ids:
            if visit_id == canonical_visit_id:
                continue
            connection.exec_driver_sql(
                "UPDATE visits SET device_event_id = NULL WHERE id = ?",
                (visit_id,),
            )
            repaired_rows += 1
        if member_entry_columns and {"event_id", "visit_id"} <= member_entry_columns:
            connection.exec_driver_sql(
                "UPDATE member_entry_events SET visit_id = ? WHERE event_id = ?",
                (canonical_visit_id, event_id),
            )

    if repaired_rows:
        logger.warning("Repaired %s duplicate Visit event rows", repaired_rows)


def referenced_visit_id(connection, event_id, visit_ids: list[str], member_entry_columns: set[str] | None):
    if not member_entry_columns or not {"event_id", "visit_id"} <= member_entry_columns:
        return None
    referenced = connection.exec_driver_sql(
        "SELECT visit_id FROM member_entry_events "
        "WHERE event_id = ? AND visit_id IS NOT NULL",
        (event_id,),
    ).scalar()
    return referenced if referenced in visit_ids else None


def require_entry_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    requirements = {
        "customer_entry_events": {
            "name": "uq_customer_entry_dedup_key",
            "column_names": ["dedup_key"],
        },
        "visits": {
            "name": "uq_visits_entry_event_id",
            "column_names": ["entry_event_id"],
        },
    }
    invalid = []
    for table, requirement in requirements.items():
        index_name = requirement["name"]
        if not inspector.has_table(table):
            invalid.append(f"{table}.{index_name} (table missing)")
            continue
        indexes = {item["name"]: item for item in inspector.get_indexes(table)}
        index = indexes.get(index_name)
        if index is None:
            invalid.append(f"{table}.{index_name} (missing)")
            continue
        if not bool(index.get("unique")):
            invalid.append(f"{table}.{index_name} (expected unique=True)")
        if index.get("column_names") != requirement["column_names"]:
            invalid.append(
                f"{table}.{index_name} "
                f"(expected columns={requirement['column_names']}, "
                f"actual={index.get('column_names')})"
            )
    if invalid:
        raise EntryDomainSchemaNotReadyError(
            "entry-domain schema is not ready; invalid required indexes: "
            + ", ".join(invalid)
        )
