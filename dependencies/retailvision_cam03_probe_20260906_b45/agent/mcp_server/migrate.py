"""Explicit operator command for RetailVision database schema evolution."""

from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine

from agent.schema_evolution import evolve_schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    database_url = (
        args.database_url
        or os.getenv("RETAILVISION_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )
    if not database_url:
        parser.error("--database-url or RETAILVISION_DATABASE_URL is required")
    engine = create_engine(database_url)
    try:
        evolve_schema(engine)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
