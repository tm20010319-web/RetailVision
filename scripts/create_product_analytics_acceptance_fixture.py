"""Create the deterministic anonymous Product Analytics acceptance fixture."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.offline_behavior import Stage9BehaviorFactAdapter
from agent.product_interaction_analytics import ProductInteractionFactWriter
from agent.schema_evolution import evolve_schema


START_AT = datetime(2026, 8, 20, 13, 0, 0)


def _raw(
    kind: str,
    at: datetime,
    *,
    session_id: str,
    product_key: str,
    product_name: str,
    person_track_token: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=f"{session_id}:{kind}",
        event_type=kind,
        observed_at=at,
        camera_id="cam02",
        product_key=product_key,
        product_name=product_name,
        product_track_token=f"{session_id}:product",
        person_track_id=1,
        person_track_token=person_track_token,
        interaction_session_id=session_id,
        confidence=1.0,
    )


def _session(
    session_id: str,
    product_key: str,
    product_name: str,
    start_at: datetime,
    holding_seconds: float,
    person_track_token: str,
) -> tuple[SimpleNamespace, ...]:
    return (
        _raw(
            "PICK_UP", start_at, session_id=session_id,
            product_key=product_key, product_name=product_name,
            person_track_token=person_track_token,
        ),
        _raw(
            "HOLDING", start_at, session_id=session_id,
            product_key=product_key, product_name=product_name,
            person_track_token=person_track_token,
        ),
        _raw(
            "PUT_DOWN", start_at + timedelta(seconds=holding_seconds),
            session_id=session_id, product_key=product_key,
            product_name=product_name, person_track_token=person_track_token,
        ),
    )


def create_fixture(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        evolve_schema(engine)
        raw_events = (
            *_session(
                "fixture-a-1", "smart_toy_a", "智能熊",
                START_AT + timedelta(minutes=5), 5.0, "fixture-track-1",
            ),
            *_session(
                "fixture-a-2", "smart_toy_a", "智能熊",
                START_AT + timedelta(minutes=15), 7.0, "fixture-track-2",
            ),
            *_session(
                "fixture-b-1", "smart_toy_b", "智能象",
                START_AT + timedelta(minutes=25), 20.0, "fixture-track-3",
            ),
        )
        facts = Stage9BehaviorFactAdapter().normalize(
            analysis_job_id="job-product-analytics-acceptance-v1",
            analysis_run_id="run-product-analytics-acceptance-v1",
            source_video_id="fixture-product-analytics-v1",
            raw_events=raw_events,
        ).facts
        if len(facts) != 9:
            raise RuntimeError("product_analytics_acceptance_fixture_invalid")
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        ProductInteractionFactWriter(
            sessions, track_hash_secret=b"product-analytics-acceptance-v1"
        ).write(facts, store_id="store-1")
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args(argv)
    create_fixture(args.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
