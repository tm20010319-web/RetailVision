"""Read-only member mapping lookup for the replacement-camera test."""

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from agent.models import DeviceMemberMapping, Member


@dataclass(frozen=True)
class MappingResult:
    member_id: str
    logical_device_id: str
    unique_id: str


class SqliteMappingResolver:
    def __init__(
        self,
        database_url: str,
        logical_device_id: str,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        if not isinstance(logical_device_id, str) or not logical_device_id.strip():
            raise ValueError("logical device ID must not be blank")

        self.logical_device_id = logical_device_id
        self._engine: Engine | None = None
        if session_factory is None:
            database = make_url(database_url)
            if database.get_backend_name() != "sqlite":
                raise ValueError("SQLite database URL is required")
            if database.database not in (None, "", ":memory:"):
                database_path = database.database
                if not database_path.startswith("file:"):
                    database_path = f"file:{database_path}"
                database = database.set(database=database_path).update_query_dict(
                    {"mode": "ro", "uri": "true"}
                )
            self._engine = create_engine(database)
            self._session_factory = sessionmaker(bind=self._engine)
        else:
            self._session_factory = session_factory

    def resolve(self, unique_id: str) -> MappingResult | None:
        if not isinstance(unique_id, str) or not unique_id.strip():
            return None

        session = self._session_factory()
        try:
            row = (
                session.query(
                    Member.id,
                    DeviceMemberMapping.device_id,
                    DeviceMemberMapping.unique_id,
                )
                .join(
                    DeviceMemberMapping,
                    DeviceMemberMapping.member_id == Member.id,
                )
                .filter(
                    DeviceMemberMapping.device_id == self.logical_device_id,
                    DeviceMemberMapping.unique_id == unique_id,
                    DeviceMemberMapping.sync_status == "synced",
                    DeviceMemberMapping.is_enabled.is_(True),
                    Member.is_active.is_(True),
                )
                .one_or_none()
            )
            if row is None:
                return None
            return MappingResult(
                member_id=row[0],
                logical_device_id=row[1],
                unique_id=row[2],
            )
        finally:
            session.close()
