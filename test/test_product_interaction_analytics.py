from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from _test_bootstrap import ensure_project_root_on_path

PROJECT_ROOT = ensure_project_root_on_path()

from agent.models import Base, BehaviorEvent, ProductInteractionFact  # noqa: E402
from agent.offline.offline_behavior import Stage9BehaviorFactAdapter  # noqa: E402
from agent.analytics.product_interaction_analytics import (  # noqa: E402
    ProductAnalyticsError,
    ProductAnalyticsQueryService,
    ProductInteractionFactWriter,
)


class ProductInteractionAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine)
        self.writer = ProductInteractionFactWriter(
            self.session_factory, track_hash_secret=b"test-secret"
        )
        self.query = ProductAnalyticsQueryService(self.session_factory)
        self.start = datetime(2026, 8, 20, 4, 0, 0)

    def tearDown(self) -> None:
        self.session_factory.kw["bind"].dispose()

    def test_event_804_regression_counts_two_complete_interactions_without_identity(self) -> None:
        report = json.loads((
            PROJECT_ROOT
            / "reports"
            / "p6s_trusted_publication_20260820"
            / "event_804"
            / "behavior_probe_flush.json"
        ).read_text(encoding="utf-8"))
        raw_events = tuple(
            SimpleNamespace(
                **{
                    **item,
                    "observed_at": datetime.fromisoformat(item["observed_at"]),
                }
            )
            for item in report["raw_stage9_events"]
        )
        facts = Stage9BehaviorFactAdapter().normalize(
            analysis_job_id=report["analysis_job_id"],
            analysis_run_id=report["analysis_run_id"],
            source_video_id="event-804",
            raw_events=raw_events,
        ).facts

        self.writer.write(facts, store_id="store-1")
        self.assertEqual(6, self.writer.last_audit.inserted_count)
        self.assertEqual(0, self.writer.last_audit.replayed_count)

        self.writer.write(facts, store_id="store-1")
        self.assertEqual(0, self.writer.last_audit.inserted_count)
        self.assertEqual(6, self.writer.last_audit.replayed_count)

        summary = self.query.get_product_behavior_summary(
            store_id="store-1",
            camera_id="cam02",
            product_key="smart_toy_a",
            start_at=datetime(2026, 8, 20, 4, 23, 0),
            end_at=datetime(2026, 8, 20, 4, 24, 0),
        )
        self.assertEqual(2, summary["pickup_count"])
        self.assertEqual(2, summary["put_down_count"])
        self.assertEqual(2, summary["interaction_count"])
        self.assertEqual(2, summary["completed_interaction_count"])
        self.assertEqual(2, summary["holding_count"])
        self.assertAlmostEqual(20.188451, summary["total_holding_seconds"])
        self.assertAlmostEqual(10.0942255, summary["avg_holding_seconds"])
        self.assertAlmostEqual(10.0942255, summary["average_hold_time"])
        self.assertAlmostEqual(13.378855, summary["max_holding_seconds"])
        self.assertAlmostEqual(13.378855, summary["max_hold_time"])
        self.assertEqual(1, summary["unique_person_track_count"])

        ranked = self.query.rank_products(
            store_id="store-1",
            camera_id="cam02",
            start_at=datetime(2026, 8, 20, 4, 23, 0),
            end_at=datetime(2026, 8, 20, 4, 24, 0),
        )
        self.assertEqual(1, len(ranked))
        self.assertEqual(2, ranked[0]["put_down_count"])
        self.assertAlmostEqual(20.188451, ranked[0]["total_holding_seconds"])
        self.assertEqual(1, ranked[0]["unique_track_count"])

        db = self.session_factory()
        try:
            rows = db.query(ProductInteractionFact).all()
            self.assertEqual(6, len(rows))
            self.assertEqual(0, db.query(BehaviorEvent).count())
            self.assertTrue(all(len(row.track_key_hash) == 64 for row in rows))
            columns = set(ProductInteractionFact.__table__.columns.keys())
            self.assertNotIn("member_id", columns)
            self.assertNotIn("person_track_token", columns)
            self.assertTrue(all("person_track_token" not in row.__dict__ for row in rows))
        finally:
            db.close()

    def test_writer_is_idempotent_for_run_session_and_event_type(self) -> None:
        facts = self._facts("run-a", "sku-a", "Toy A", "session-a", self.start, 5.0)

        self.writer.write(facts, store_id="store-a")
        self.writer.write(facts, store_id="store-a")

        db = self.session_factory()
        try:
            self.assertEqual(3, db.query(ProductInteractionFact).count())
        finally:
            db.close()

    def test_conflicting_replay_is_rejected_without_changing_committed_rows(self) -> None:
        facts = self._facts("run-conflict", "sku-a", "Toy A", "session-a", self.start, 5.0)
        self.writer.write(facts, store_id="store-a")
        conflicting = (replace(facts[0], product_key="sku-other"),) + facts[1:]

        with self.assertRaisesRegex(
            ProductAnalyticsError, "product_interaction_fact_conflict"
        ):
            self.writer.write(conflicting, store_id="store-a")

        self.assertEqual(1, self.writer.last_audit.conflict_count)
        self.assertEqual(
            "product_interaction_fact_conflict",
            self.writer.last_audit.failure_reason,
        )
        db = self.session_factory()
        try:
            self.assertEqual(3, db.query(ProductInteractionFact).count())
            self.assertEqual(
                {"sku-a"},
                {row.product_key for row in db.query(ProductInteractionFact).all()},
            )
        finally:
            db.close()

    def test_writer_is_idempotent_with_duplicates_in_one_batch_and_keeps_projection_audit(self) -> None:
        facts = self._facts("run-audit", "sku-a", "Toy A", "session-a", self.start, 5.0)
        written = self.writer.write(
            facts + facts,
            store_id="store-a",
            artifact_manifest_hash="v1:sha256:" + "a" * 64,
        )

        self.assertEqual(6, len(written))
        db = self.session_factory()
        try:
            rows = db.query(ProductInteractionFact).all()
            self.assertEqual(3, len(rows))
            self.assertTrue(all(row.source_video_id == "video-run-audit" for row in rows))
            self.assertTrue(all(row.normalized_fact_ref for row in rows))
            self.assertTrue(all(row.artifact_manifest_hash.startswith("v1:sha256:") for row in rows))
            self.assertTrue(all(row.projection_version == "product_interaction_projection_v1" for row in rows))
        finally:
            db.close()

    def test_rank_products_orders_by_completed_interactions_then_pickups(self) -> None:
        self.writer.write(
            self._facts("run-a", "toy-a", "Toy A", "session-a", self.start, 5.0)
            + self._facts("run-a", "toy-a", "Toy A", "session-b", self.start + timedelta(minutes=1), 5.0)
            + self._facts("run-b", "toy-b", "Toy B", "session-c", self.start, 20.0),
            store_id="store-a",
        )

        ranked = self.query.rank_products(
            store_id="store-a",
            start_at=self.start - timedelta(seconds=1),
            end_at=self.start + timedelta(hours=1),
        )

        self.assertEqual(["toy-a", "toy-b"], [item["product_key"] for item in ranked])
        self.assertEqual(2, ranked[0]["pickup_count"])
        self.assertEqual(2, ranked[0]["completed_interaction_count"])
        self.assertEqual(20.0, ranked[1]["total_holding_seconds"])
        self.assertEqual(2, ranked[0]["unique_person_track_count"])
        self.assertEqual(ranked[0]["average_holding_seconds"], ranked[0]["avg_holding_seconds"])

    def test_scope_time_product_and_comparison_queries(self) -> None:
        self.writer.write(
            self._facts("run-a", "toy-a", "Toy A", "session-a", self.start, 3.0, camera_id="cam-a")
            + self._facts("run-b", "toy-a", "Toy A", "session-b", self.start + timedelta(days=1), 7.0, camera_id="cam-b"),
            store_id="store-a",
        )

        empty = self.query.get_product_behavior_summary(
            store_id="store-a", camera_id="cam-a", product_key="toy-a",
            start_at=self.start + timedelta(seconds=1),
            end_at=self.start + timedelta(hours=1),
        )
        self.assertEqual(0, empty["interaction_count"])

        comparison = self.query.compare_product_behavior(
            store_id="store-a", product_key="toy-a",
            first_start_at=self.start, first_end_at=self.start + timedelta(hours=1),
            second_start_at=self.start + timedelta(days=1), second_end_at=self.start + timedelta(days=1, hours=1),
        )
        self.assertEqual(0, comparison["interaction_change"])
        self.assertEqual(0, comparison["pickup_change"])

        with self.assertRaisesRegex(ProductAnalyticsError, "query_time_range_invalid"):
            self.query.rank_products(
                store_id="store-a", start_at=self.start, end_at=self.start - timedelta(seconds=1)
            )
        with self.assertRaisesRegex(
            ProductAnalyticsError, "query_time_range_exceeds_maximum"
        ):
            self.query.rank_products(
                store_id="store-a",
                start_at=self.start,
                end_at=self.start + timedelta(days=31, seconds=1),
            )
        with self.assertRaisesRegex(ProductAnalyticsError, "limit_invalid"):
            self.query.rank_products(
                store_id="store-a",
                start_at=self.start,
                end_at=self.start + timedelta(hours=1),
                limit=101,
            )

    def test_different_runs_stores_and_cameras_remain_isolated(self) -> None:
        self.writer.write(
            self._facts(
                "run-store-a", "toy-a", "Toy A", "session-a", self.start, 3.0,
                camera_id="cam-a",
            ),
            store_id="store-a",
        )
        self.writer.write(
            self._facts(
                "run-store-b", "toy-b", "Toy B", "session-a", self.start, 4.0,
                camera_id="cam-b",
            ),
            store_id="store-b",
        )

        store_a = self.query.rank_products(
            store_id="store-a", camera_id="cam-a",
            start_at=self.start, end_at=self.start + timedelta(hours=1),
        )
        wrong_camera = self.query.rank_products(
            store_id="store-a", camera_id="cam-b",
            start_at=self.start, end_at=self.start + timedelta(hours=1),
        )
        self.assertEqual(["toy-a"], [item["product_key"] for item in store_a])
        self.assertEqual([], wrong_camera)

    def _facts(
        self,
        run_id: str,
        product_key: str,
        product_name: str,
        session_id: str,
        started_at: datetime,
        holding_seconds: float,
        *,
        camera_id: str = "cam-a",
    ):
        end = started_at + timedelta(seconds=holding_seconds)
        adapter = Stage9BehaviorFactAdapter()
        return adapter.normalize(
            analysis_job_id=f"job-{run_id}",
            analysis_run_id=run_id,
            source_video_id=f"video-{run_id}",
            raw_events=(
                self._raw("PICK_UP", started_at, product_key, product_name, session_id, camera_id),
                self._raw("HOLDING", started_at, product_key, product_name, session_id, camera_id),
                self._raw("PUT_DOWN", end, product_key, product_name, session_id, camera_id),
            ),
        ).facts

    @staticmethod
    def _raw(event_type, observed_at, product_key, product_name, session_id, camera_id):
        return SimpleNamespace(
            event_id=f"{session_id}:{event_type}",
            event_type=event_type,
            observed_at=observed_at,
            camera_id=camera_id,
            product_key=product_key,
            product_name=product_name,
            product_track_token=f"{session_id}:product",
            person_track_id=1,
            person_track_token=f"{session_id}:track",
            interaction_session_id=session_id,
            confidence=1.0,
        )


if __name__ == "__main__":
    unittest.main()
