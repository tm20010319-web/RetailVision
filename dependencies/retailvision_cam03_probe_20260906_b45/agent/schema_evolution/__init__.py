"""Public seam for additive domain schema evolution."""

from ._entry import EntryDomainSchemaNotReadyError, require_entry_schema
from ._product_analytics import (
    ProductAnalyticsSchemaNotReadyError,
    require_compatible_schema as require_product_analytics_schema,
)
from ._member_behavior import (
    MemberBehaviorSchemaNotReadyError,
    require_compatible_schema as require_member_behavior_schema,
)
from ._schema import evolve_schema

__all__ = [
    "EntryDomainSchemaNotReadyError",
    "ProductAnalyticsSchemaNotReadyError",
    "evolve_schema",
    "require_entry_schema",
    "require_product_analytics_schema",
    "MemberBehaviorSchemaNotReadyError",
    "require_member_behavior_schema",
]
