"""SQLAlchemy adapter for immutable Offline historical identity revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from agent.offline.offline_identity import (
    HistoricalIdentityIntegrityError,
    HistoricalIdentityResolver,
    HistoricalIdentityRevisionData,
)


class SqlAlchemyHistoricalIdentityRepository:
    """Persist Offline historical revisions and the Live current projection atomically."""

    def __init__(self, session_factory: Any, *, history_available_from: datetime) -> None:
        _require_utc_naive(history_available_from, "history_available_from")
        self.session_factory = session_factory
        self.history_available_from = history_available_from

    def resolver(self) -> HistoricalIdentityResolver:
        return HistoricalIdentityResolver(
            history_available_from=self.history_available_from,
            revisions=self.load_revisions(),
        )

    def load_revisions(self) -> tuple[HistoricalIdentityRevisionData, ...]:
        from agent.models import DeviceMemberIdentityRevision

        session = self.session_factory()
        try:
            rows = (
                session.query(DeviceMemberIdentityRevision)
                .order_by(
                    DeviceMemberIdentityRevision.effective_from.asc(),
                    DeviceMemberIdentityRevision.id.asc(),
                )
                .all()
            )
            return tuple(_revision_data(row) for row in rows)
        finally:
            session.close()

    def current_mapping(
        self,
        *,
        device_id: str,
        group_id: Optional[int],
        person_id: Optional[int],
        unique_id: str,
    ) -> Optional[Any]:
        from agent.identity.device_identity import DeviceMemberMappingData
        from agent.models import DeviceMemberMapping, Member

        _require_identity(device_id, group_id, person_id, unique_id)
        session = self.session_factory()
        try:
            row = (
                session.query(DeviceMemberMapping, Member)
                .join(Member, Member.id == DeviceMemberMapping.member_id)
                .filter(*_mapping_identity_predicates(
                    DeviceMemberMapping,
                    device_id=device_id,
                    group_id=group_id,
                    person_id=person_id,
                    unique_id=unique_id,
                ))
                .one_or_none()
            )
            if row is None:
                return None
            mapping, member = row
            return DeviceMemberMappingData(
                device_id=mapping.device_id,
                member_id=mapping.member_id,
                group_id=mapping.group_id,
                group_uuid=mapping.group_uuid,
                person_id=mapping.person_id,
                unique_id=mapping.unique_id,
                face_uuid=mapping.face_uuid,
                sync_status=mapping.sync_status,
                is_enabled=bool(mapping.is_enabled),
                last_sync_at=mapping.last_sync_at,
                last_error=mapping.last_error,
                member_active=bool(member.is_active),
            )
        finally:
            session.close()

    def apply_mapping_revision(self, mapping: Any, *, effective_from: datetime) -> None:
        from sqlalchemy import and_, or_
        from agent.models import (
            DeviceMemberIdentityRevision,
            DeviceMemberMapping,
            Member,
            new_uuid,
        )

        _require_utc_naive(effective_from, "effective_from")
        if effective_from < self.history_available_from:
            raise HistoricalIdentityIntegrityError("revision_precedes_history_bootstrap")
        _validate_mapping_data(mapping)
        session = self.session_factory()
        try:
            member = session.get(Member, mapping.member_id)
            if member is None:
                raise HistoricalIdentityIntegrityError("member_principal_missing")

            predicates = _revision_identity_predicates(
                DeviceMemberIdentityRevision,
                device_id=mapping.device_id,
                group_id=mapping.group_id,
                person_id=mapping.person_id,
                unique_id=mapping.unique_id,
            )
            open_revisions = (
                session.query(DeviceMemberIdentityRevision)
                .filter(
                    or_(
                        and_(*predicates),
                        # Resynchronizing the same member may replace the
                        # device identity. Do not leave the old one open.
                        and_(
                            DeviceMemberIdentityRevision.device_id == mapping.device_id,
                            DeviceMemberIdentityRevision.member_id == mapping.member_id,
                        ),
                    ),
                    DeviceMemberIdentityRevision.effective_to.is_(None),
                )
                .all()
            )
            for prior in open_revisions:
                if prior.effective_from >= effective_from:
                    raise HistoricalIdentityIntegrityError("non_monotonic_identity_revision")
                prior.effective_to = effective_from

            session.add(DeviceMemberIdentityRevision(
                id=new_uuid(),
                device_id=mapping.device_id,
                group_id=mapping.group_id,
                person_id=mapping.person_id,
                unique_id=mapping.unique_id,
                member_id=mapping.member_id,
                effective_from=effective_from,
                effective_to=None,
                mapping_enabled=bool(mapping.is_enabled),
                mapping_sync_status=mapping.sync_status,
                member_principal_exists=True,
                member_principal_active=bool(member.is_active),
            ))

            current_rows = (
                session.query(DeviceMemberMapping)
                .filter(*_mapping_identity_predicates(
                    DeviceMemberMapping,
                    device_id=mapping.device_id,
                    group_id=mapping.group_id,
                    person_id=mapping.person_id,
                    unique_id=mapping.unique_id,
                ))
                .all()
            )
            projection_id = f"{mapping.device_id}:{mapping.member_id}"
            for row in current_rows:
                if row.id != projection_id:
                    session.delete(row)
            projection = session.get(DeviceMemberMapping, projection_id)
            if projection is None:
                projection = DeviceMemberMapping(id=projection_id)
                session.add(projection)
            _copy_mapping_to_projection(projection, mapping)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def apply_member_principal_revision(
        self,
        *,
        member_id: str,
        active: bool,
        effective_from: datetime,
    ) -> None:
        """Record a Member active/inactive transition for every current mapping."""
        _require_utc_naive(effective_from, "effective_from")
        if not isinstance(member_id, str) or not member_id:
            raise ValueError("member_id_required")
        if not isinstance(active, bool):
            raise ValueError("member_active_invalid")
        from agent.models import DeviceMemberIdentityRevision, Member, new_uuid

        session = self.session_factory()
        try:
            member = session.get(Member, member_id)
            if member is None:
                raise HistoricalIdentityIntegrityError("member_principal_missing")
            open_revisions = (
                session.query(DeviceMemberIdentityRevision)
                .filter(
                    DeviceMemberIdentityRevision.member_id == member_id,
                    DeviceMemberIdentityRevision.effective_to.is_(None),
                )
                .all()
            )
            if not open_revisions:
                raise HistoricalIdentityIntegrityError("member_identity_revision_missing")
            for prior in open_revisions:
                if prior.effective_from >= effective_from:
                    raise HistoricalIdentityIntegrityError("non_monotonic_identity_revision")
                prior.effective_to = effective_from
                session.add(DeviceMemberIdentityRevision(
                    id=new_uuid(),
                    device_id=prior.device_id,
                    group_id=prior.group_id,
                    person_id=prior.person_id,
                    unique_id=prior.unique_id,
                    member_id=prior.member_id,
                    effective_from=effective_from,
                    effective_to=None,
                    mapping_enabled=bool(prior.mapping_enabled),
                    mapping_sync_status=prior.mapping_sync_status,
                    member_principal_exists=True,
                    member_principal_active=active,
                ))
            member.is_active = active
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _revision_data(row: Any) -> HistoricalIdentityRevisionData:
    return HistoricalIdentityRevisionData(
        revision_id=row.id,
        device_id=row.device_id,
        group_id=row.group_id,
        person_id=row.person_id,
        unique_id=row.unique_id,
        member_id=row.member_id,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        mapping_enabled=bool(row.mapping_enabled),
        mapping_sync_status=row.mapping_sync_status,
        member_principal_exists=bool(row.member_principal_exists),
        member_principal_active=bool(row.member_principal_active),
    )


def _require_utc_naive(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError(f"{field}_must_be_utc_naive")


def _require_identity(
    device_id: str,
    group_id: Optional[int],
    person_id: Optional[int],
    unique_id: str,
) -> None:
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("device_id_required")
    if unique_id:
        return
    if group_id is None or person_id is None:
        raise ValueError("device_identity_required")


def _validate_mapping_data(mapping: Any) -> None:
    for field in (
        "device_id", "member_id", "group_uuid", "unique_id", "face_uuid", "sync_status",
    ):
        value = getattr(mapping, field, None)
        if field == "unique_id":
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"mapping_{field}_required")
    _require_identity(
        mapping.device_id,
        getattr(mapping, "group_id", None),
        getattr(mapping, "person_id", None),
        mapping.unique_id,
    )
    if not isinstance(getattr(mapping, "is_enabled", None), bool):
        raise ValueError("mapping_enabled_required")


def _revision_identity_predicates(
    model: Any,
    *,
    device_id: str,
    group_id: Optional[int],
    person_id: Optional[int],
    unique_id: str,
) -> tuple[Any, ...]:
    if unique_id:
        return (model.device_id == device_id, model.unique_id == unique_id)
    return (
        model.device_id == device_id,
        model.group_id == group_id,
        model.person_id == person_id,
    )


def _mapping_identity_predicates(
    model: Any,
    *,
    device_id: str,
    group_id: Optional[int],
    person_id: Optional[int],
    unique_id: str,
) -> tuple[Any, ...]:
    return _revision_identity_predicates(
        model,
        device_id=device_id,
        group_id=group_id,
        person_id=person_id,
        unique_id=unique_id,
    )


def _copy_mapping_to_projection(row: Any, mapping: Any) -> None:
    row.device_id = mapping.device_id
    row.member_id = mapping.member_id
    row.group_id = mapping.group_id
    row.group_uuid = mapping.group_uuid
    row.person_id = mapping.person_id
    row.unique_id = mapping.unique_id
    row.face_uuid = mapping.face_uuid
    row.sync_status = mapping.sync_status
    row.is_enabled = bool(mapping.is_enabled)
    row.last_sync_at = getattr(mapping, "last_sync_at", None)
    row.last_error = getattr(mapping, "last_error", None)
