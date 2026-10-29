from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine


class MemberBehaviorSchemaNotReadyError(RuntimeError):
    """Raised when the read-only Member Behavior tables are unavailable."""


_REQUIRED_COLUMNS = {
    "members": {"id", "name", "tier", "is_active"},
    "behavior_events": {
        "member_id", "store_id", "camera_id", "event_type", "assessment",
        "is_demo", "identity_status", "identity_source", "identity_binding_level",
        "occurred_at", "duration_seconds", "details", "id",
    },
}


def require_compatible_schema(engine: Engine) -> None:
    """Inspect only the tables and columns used by MB-01 read queries."""
    try:
        inspector = inspect(engine)
        for table, required in _REQUIRED_COLUMNS.items():
            if not inspector.has_table(table):
                raise MemberBehaviorSchemaNotReadyError(
                    "member_behavior_schema_incompatible"
                )
            actual = {column["name"] for column in inspector.get_columns(table)}
            if not required.issubset(actual):
                raise MemberBehaviorSchemaNotReadyError(
                    "member_behavior_schema_incompatible"
                )
    except MemberBehaviorSchemaNotReadyError:
        raise
    except Exception as exc:
        raise MemberBehaviorSchemaNotReadyError(
            "member_behavior_schema_incompatible"
        ) from exc
