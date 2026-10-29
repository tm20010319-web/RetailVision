from __future__ import annotations

from ._common import add_missing_columns, migration_applied, record_migration


def evolve(connection, columns: set[str] | None) -> None:
    if columns is None:
        return
    add_missing_columns(connection, "members", columns, {
        "department": "VARCHAR(128)",
        "position": "VARCHAR(128)",
        "gender": "VARCHAR(16) NOT NULL DEFAULT 'unknown'",
    })
    if migration_applied(connection, "members_gender_v1"):
        return
    # Preserve the existing deterministic seed policy; this is not name inference.
    connection.exec_driver_sql(
        "UPDATE members SET gender = 'female' "
        "WHERE id IN ('member-huangmengyu', 'member-maoyijie', 'member-renyu') "
        "AND (gender IS NULL OR gender = 'unknown')"
    )
    connection.exec_driver_sql(
        "UPDATE members SET gender = 'male' "
        "WHERE (gender IS NULL OR gender = 'unknown') "
        "AND id NOT IN ('member-huangmengyu', 'member-maoyijie', 'member-renyu')"
    )
    record_migration(connection, "members_gender_v1")
