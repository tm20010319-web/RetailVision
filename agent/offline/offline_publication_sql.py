"""SQLAlchemy adapter for the Offline publication fence port."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from agent.models import OfflineBehaviorPublication
from agent.offline.offline_behavior import PreparedOfflineBehaviorPublication
from agent.offline.offline_publication import (
    OfflinePublicationResult,
    existing_publication_result,
)


class SqlAlchemyOfflinePublicationRepository:
    def __init__(self, session_factory: Any, behavior_event_store: Any) -> None:
        self.session_factory = session_factory
        self.behavior_event_store = behavior_event_store

    def publish_once(
        self,
        *,
        prepared: PreparedOfflineBehaviorPublication,
        published_at: datetime,
    ) -> OfflinePublicationResult:
        session = self.session_factory()
        try:
            existing = session.get(OfflineBehaviorPublication, prepared.analysis_job_id)
            if existing is not None:
                return existing_publication_result(existing, prepared)
            session.rollback()
            try:
                session.add(OfflineBehaviorPublication(
                    analysis_job_id=prepared.analysis_job_id,
                    published_analysis_run_id=prepared.analysis_run_id,
                    source_video_id=prepared.source_video_id,
                    event_count=prepared.event_count,
                    event_manifest_hash=prepared.event_manifest_hash,
                    published_at=published_at,
                ))
                session.flush()
                self.behavior_event_store.bulk_insert_behavior(
                    session=session, events=prepared.events
                )
                session.flush()
                session.commit()
                return OfflinePublicationResult(
                    "published", prepared.analysis_job_id, prepared.analysis_run_id,
                    prepared.event_count, prepared.event_manifest_hash,
                )
            except IntegrityError:
                session.rollback()
                existing = session.get(OfflineBehaviorPublication, prepared.analysis_job_id)
                if existing is not None:
                    return existing_publication_result(existing, prepared)
                raise
            except Exception:
                session.rollback()
                raise
        finally:
            session.close()
