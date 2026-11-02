"""One-time, audit-preserving historical member identity repair."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.db import configure_sqlite_foreign_keys
from agent.visits.member_arrival_repair import MemberArrivalRepairService
from agent.member.member_statistics import MemberStatisticsRecalculator
from agent.models import Visit
from agent.schema_evolution import evolve_schema
from agent.identity.store_resolution import StoreResolutionService
from agent.visits.visit_merge_service import VisitMergeService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--visit-id")
    parser.add_argument("--member-id")
    parser.add_argument("--store-id")
    parser.add_argument("--from-time")
    parser.add_argument("--to-time")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--checkpoint")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    database = Path(args.database).resolve()
    engine = configure_sqlite_foreign_keys(create_engine(f"sqlite:///{database.as_posix()}"))
    if args.apply:
        evolve_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store_resolution = StoreResolutionService(sessions)
    visit_merge = VisitMergeService(sessions)
    arrival_repair = MemberArrivalRepairService(sessions)
    statistics = MemberStatisticsRecalculator(sessions)
    _ = (store_resolution, visit_merge, statistics)
    checkpoint = {}
    if args.checkpoint and Path(args.checkpoint).exists():
        checkpoint = json.loads(Path(args.checkpoint).read_text(encoding="utf-8"))
    last_visit_id = checkpoint.get("last_visit_id")
    db = sessions()
    try:
        query = db.query(Visit.id)
        if args.visit_id:
            query = query.filter(Visit.id == args.visit_id)
        if args.member_id:
            query = query.filter(Visit.member_id == args.member_id)
        if args.store_id:
            query = query.filter(Visit.store_id == args.store_id)
        if args.from_time:
            query = query.filter(Visit.entry_time >= datetime.fromisoformat(args.from_time))
        if args.to_time:
            query = query.filter(Visit.entry_time <= datetime.fromisoformat(args.to_time))
        if last_visit_id:
            query = query.filter(Visit.id > last_visit_id)
        visit_ids = [row[0] for row in query.order_by(Visit.id).limit(args.batch_size).all()]
    finally:
        db.close()
    results = []
    for visit_id in visit_ids:
        store_result = store_resolution.resolve_visit(visit_id, apply=args.apply)
        repair_result = arrival_repair.repair_visit(visit_id, apply=args.apply)
        db = sessions()
        try:
            visit = db.get(Visit, visit_id)
            member_id = visit.member_id if visit else None
            store_id = visit.store_id if visit else None
        finally:
            db.close()
        merge_result = visit_merge.preview_reusable(member_id=member_id, store_id=store_id) if member_id and store_id else None
        if args.apply and member_id and store_id:
            try:
                merged = visit_merge.merge_reusable(member_id=member_id, store_id=store_id)
                merge_result = {
                    "canonical": merged.canonical_visit_id,
                    "duplicates": merged.superseded_visit_ids,
                    "moved_counts": merged.moved_counts,
                }
            except ValueError as error:
                merge_result = {"skipped": str(error)}
        statistics_result = (
            statistics.recalculate(member_id) if args.apply and member_id
            else statistics.preview(member_id) if member_id else None
        )
        results.append({
            "visit_id": visit_id,
            "store_resolution": {"status": store_result.status, "store_id": store_result.store_id, "reason": store_result.reason},
            "merge": merge_result,
            "arrival": repair_result,
            "statistics": statistics_result,
        })
    report = {
        "mode": "apply" if args.apply else "dry-run",
        "scope": {"visit_id": args.visit_id, "member_id": args.member_id, "store_id": args.store_id, "batch_size": args.batch_size},
        "results": results,
    }
    if args.checkpoint:
        Path(args.checkpoint).write_text(json.dumps({
            "last_visit_id": visit_ids[-1] if visit_ids else last_visit_id,
            "report": report,
        }, ensure_ascii=True, default=str, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, default=str))
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
