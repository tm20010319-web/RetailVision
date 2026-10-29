"""数据库会话工厂和 SQLite 连接配置。"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from agent.config import settings

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def configure_sqlite_foreign_keys(engine: Engine) -> Engine:
    """Enable SQLite FK enforcement for every new connection on an engine."""
    dialect = getattr(engine, "dialect", None)
    if getattr(dialect, "name", None) != "sqlite":
        return engine
    if not event.contains(engine, "connect", _enable_sqlite_foreign_keys):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _get_engine():
    global _engine
    if _engine is None:
        engine = create_engine(
            settings.DATABASE_URL,
            echo=False,
            connect_args={"check_same_thread": False}
            if settings.DATABASE_URL.startswith("sqlite")
            else {},
        )
        _engine = configure_sqlite_foreign_keys(engine)
        safe_url = make_url(settings.DATABASE_URL).render_as_string(
            hide_password=True
        )
        logger.info("Database engine created: %s", safe_url)
    return _engine


def _get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=_get_engine(), autoflush=False, autocommit=False
        )
    return _SessionLocal


def get_session_factory() -> sessionmaker:
    return _get_session_factory()


def get_db_session() -> Session:
    """Return a session; callers own the close lifecycle."""
    return get_session_factory()()


def init_db(*, preserve_existing_recognition_rows: bool = False):
    """Create ORM tables and apply the domain schema evolution."""
    from agent.schema_evolution import evolve_schema

    evolve_schema(
        _get_engine(),
        preserve_existing_recognition_rows=preserve_existing_recognition_rows,
    )
    logger.info("Database tables created")
