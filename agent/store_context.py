"""Business scope defaults for Store Intelligence Agent queries."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional


@dataclass(frozen=True)
class StoreContext:
    """Resolved store scope used when an Agent omits routine query arguments."""

    store_id: str
    default_camera_id: Optional[str]
    default_start_at: datetime
    default_end_at: datetime
    business_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.store_id, str) or not self.store_id:
            raise ValueError("store_context_store_id_required")
        if self.default_camera_id is not None and (
            not isinstance(self.default_camera_id, str) or not self.default_camera_id
        ):
            raise ValueError("store_context_camera_id_invalid")
        if any(
            not isinstance(value, datetime) or value.tzinfo is not None
            for value in (self.default_start_at, self.default_end_at)
        ):
            raise ValueError("store_context_time_invalid")
        if self.default_end_at <= self.default_start_at:
            raise ValueError("store_context_time_range_invalid")
        if self.default_end_at - self.default_start_at > timedelta(days=31):
            raise ValueError("query_time_range_exceeds_maximum")
        if not isinstance(self.business_date, date):
            raise ValueError("store_context_business_date_invalid")

    @classmethod
    def from_environment(
        cls,
        *,
        require_explicit_scope: bool = False,
        environment_prefix: str = "RETAILVISION_",
        fallback_prefix: str | None = None,
    ) -> "StoreContext":
        """Build deployment scope, optionally requiring the Member contract."""
        prefix = environment_prefix
        scope_names = ("STORE_ID", "CAMERA_ID", "ANALYSIS_START_AT", "ANALYSIS_END_AT")
        if fallback_prefix is not None and not any(
            os.getenv(f"{environment_prefix}{name}") is not None
            for name in scope_names
        ):
            prefix = fallback_prefix
        store_value = os.getenv(f"{prefix}STORE_ID")
        if require_explicit_scope and (not isinstance(store_value, str) or not store_value.strip()):
            raise ValueError("store_context_store_id_required")
        store_id = store_value.strip() if isinstance(store_value, str) and store_value.strip() else "default-store"

        start_name = f"{prefix}ANALYSIS_START_AT"
        end_name = f"{prefix}ANALYSIS_END_AT"
        start_raw = os.getenv(start_name)
        end_raw = os.getenv(end_name)
        if require_explicit_scope and (
            not isinstance(start_raw, str) or not start_raw.strip()
            or not isinstance(end_raw, str) or not end_raw.strip()
        ):
            raise ValueError("store_context_analysis_window_required")
        try:
            end_at = _env_datetime(end_name)
            start_at = _env_datetime(start_name)
        except ValueError:
            if require_explicit_scope:
                raise ValueError("store_context_time_invalid")
            raise
        if end_at is None:
            end_at = datetime.now().replace(microsecond=0)
        if start_at is None:
            start_at = end_at - timedelta(days=1)
        business_date = _env_date(f"{prefix}BUSINESS_DATE") or end_at.date()
        camera_value = os.getenv(f"{prefix}CAMERA_ID")
        camera_id = camera_value.strip() if isinstance(camera_value, str) and camera_value.strip() else None
        return cls(
            store_id=store_id,
            default_camera_id=camera_id,
            default_start_at=start_at,
            default_end_at=end_at,
            business_date=business_date,
        )

    def resolve_scope(
        self,
        *,
        store_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
    ) -> dict[str, Any]:
        resolved_store_id = store_id or self.store_id
        if resolved_store_id != self.store_id:
            raise ValueError("store_context_store_scope_mismatch")
        resolved_camera_id = (
            camera_id if camera_id is not None else self.default_camera_id
        )
        if (
            self.default_camera_id is not None
            and resolved_camera_id != self.default_camera_id
        ):
            raise ValueError("store_context_camera_scope_mismatch")
        return {
            "store_id": resolved_store_id,
            "camera_id": resolved_camera_id,
            "start_at": start_at or self.default_start_at,
            "end_at": end_at or self.default_end_at,
        }


def _env_datetime(name: str) -> Optional[datetime]:
    value = os.getenv(name)
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        raise ValueError(f"{name}_must_be_naive_local_time")
    return parsed


def _env_date(name: str) -> Optional[date]:
    value = os.getenv(name)
    return date.fromisoformat(value) if value else None
