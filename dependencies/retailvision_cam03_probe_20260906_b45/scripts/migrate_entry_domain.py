"""Apply the additive entry-domain schema migration.

Usage:
    python scripts/migrate_entry_domain.py
    python scripts/migrate_entry_domain.py --database-url sqlite:///D:/path/store.db
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import settings
from agent.db import configure_sqlite_foreign_keys
from agent.schema_evolution import (
    EntryDomainSchemaNotReadyError,
    evolve_schema,
    require_entry_schema,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the additive Customer Entry and Visit migration"
    )
    parser.add_argument(
        "--database-url",
        default=settings.DATABASE_URL,
        help="SQLAlchemy database URL; defaults to DATABASE_URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = configure_sqlite_foreign_keys(create_engine(
        args.database_url,
        connect_args={"check_same_thread": False}
        if args.database_url.startswith("sqlite")
        else {},
    ))
    try:
        evolve_schema(engine)
        require_entry_schema(engine)
        safe_url = make_url(args.database_url).render_as_string(hide_password=True)
        logging.getLogger(__name__).info(
            "Entry-domain migration completed: %s", safe_url
        )
        return 0
    except EntryDomainSchemaNotReadyError as error:
        logging.getLogger(__name__).error(
            "Entry-domain migration failed readiness check: %s", error
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
