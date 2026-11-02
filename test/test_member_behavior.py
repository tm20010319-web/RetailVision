from __future__ import annotations

import math
import unittest
from datetime import date, datetime
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.analytics.member_behavior import (  # noqa: E402
    MemberBehaviorError,
    MemberBehaviorQueryService,
    MemberBehaviorScopePolicy,
)
from agent.analytics.member_behavior_repository import SqlAlchemyMemberBehaviorReadAdapter  # noqa: E402
from agent.models import Base, BehaviorEvent, DeviceMemberMapping, Member, Visit  # noqa: E402
from agent.store_context import StoreContext  # noqa: E402


class MemberBehaviorQueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.context = StoreContext(
            store_id="store-1",
            default_camera_id="cam02",
            default_start_at=datetime(2026, 8, 27, 9),
            default_end_at=datetime(2026, 8, 27, 12),
            business_date=date(2026, 8, 27),
        )
        self.policy = MemberBehaviorScopePolicy(self.context, "Asia/Shanghai")
        self.service = MemberBehaviorQueryService(
            SqlAlchemyMemberBehaviorReadAdapter(self.sessions), self.policy
        )
        with self.sessions() as db:
            db.add_all((
                Member(id="member-a", name="高俊杰", tier="gold", is_active=True),
                Member(id="member-disabled", name="停用会员", is_active=False),
            ))
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def _event(
        self,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        *,
        member_id: str | None = "member-a",
        store_id: str = "store-1",
        camera_id: str = "cam02",
        duration_seconds: float | None = None,
        product_key: object = "toy-a",
        product_name: object = "智能熊",
        assessment: str = "confirmed",
        is_demo: bool = False,
        identity_status: str = "confirmed",
        identity_source: str = "offline_track_association",
        identity_binding_level: str = "confirmed",
    ) -> None:
        details = {}
        if product_key is not ...:
            details["product_key"] = product_key
        if product_name is not ...:
            details["product_name"] = product_name
        with self.sessions() as db:
            db.add(BehaviorEvent(
                id=event_id,
                event_key=f"fixture:{event_id}",
                event_type=event_type,
                assessment=assessment,
                camera_id=camera_id,
                customer_track_id=1,
                customer_track_token=f"restricted:{event_id}",
                track_token=f"restricted:{event_id}",
                store_id=store_id,
                is_demo=is_demo,
                member_id=member_id,
                identity_status=identity_status,
                identity_source=identity_source,
                identity_binding_level=identity_binding_level,
                occurred_at=occurred_at,
                started_at=occurred_at,
                ended_at=occurred_at,
                duration_seconds=duration_seconds,
                details=details,
            ))
            db.commit()

    def test_summary_resolves_active_member_and_reads_only_offline_eligible_rows(self) -> None:
        self._event("pickup", "merchandise_pick_up", datetime(2026, 8, 27, 2))
        self._event(
            "holding", "merchandise_holding", datetime(2026, 8, 27, 2, 1),
            duration_seconds=4.0,
        )
        self._event("putdown", "merchandise_put_down", datetime(2026, 8, 27, 2, 2))
        self._event(
            "live", "merchandise_pick_up", datetime(2026, 8, 27, 2, 3),
            identity_status="identified_member", identity_source="verified_face",
        )
        self._event(
            "greeting", "staff_greeting", datetime(2026, 8, 27, 2, 4)
        )

        result = self.service.get_member_behavior_summary(member_ref="member-a")

        self.assertEqual("ok", result["status"])
        self.assertEqual(
            {"member_id": "member-a", "display_name": "高俊杰", "member_tier": "gold"},
            result["member"],
        )
        self.assertEqual(3, result["behavior_summary"]["total_behavior_count"])
        self.assertEqual(
            {
                "merchandise_pick_up": 1,
                "merchandise_holding": 1,
                "merchandise_put_down": 1,
            },
            result["behavior_summary"]["behavior_counts"],
        )
        self.assertEqual(4.0, result["behavior_summary"]["total_holding_seconds"])
        self.assertEqual("available", result["data_quality"]["holding_duration"])
        self.assertEqual("2026-08-27T10:02:00+08:00", result["behavior_summary"]["last_behavior_at"])
        self.assertNotIn("timeline", result)

    def test_recent_member_behaviors_returns_public_timeline_and_member_product_aggregates(self) -> None:
        with self.sessions() as db:
            db.add(Member(id="member-b", name="李晓明", tier="silver", is_active=True))
            db.commit()
        self._event(
            "a-pickup", "merchandise_pick_up", datetime(2026, 8, 27, 2),
            member_id="member-a", product_key="toy-a", product_name="智能熊",
        )
        self._event(
            "b-pickup", "merchandise_pick_up", datetime(2026, 8, 27, 2, 1),
            member_id="member-b", product_key="toy-b", product_name="智能小象",
        )
        self._event(
            "a-holding", "merchandise_holding", datetime(2026, 8, 27, 2, 2),
            member_id="member-a", product_key="toy-a", product_name="智能熊",
            duration_seconds=12.5,
        )

        result = self.service.list_recent_member_behaviors(limit=100)

        self.assertEqual("ok", result["status"])
        self.assertFalse(result["has_more"])
        self.assertEqual(3, result["behavior_summary"]["total_behavior_count"])
        self.assertEqual(2, len(result["member_summaries"]))
        self.assertEqual(2, len(result["product_summaries"]))
        self.assertEqual(
            ["拿起商品", "拿起商品", "持有商品"],
            [item["behavior_label"] for item in result["timeline"]],
        )
        self.assertEqual(
            {"高俊杰", "李晓明"},
            {item["member"]["display_name"] for item in result["member_summaries"]},
        )
        bear = next(
            item for item in result["product_summaries"]
            if item["product_name"] == "智能熊"
        )
        self.assertEqual(2, bear["behavior_count"])
        self.assertEqual(1, bear["unique_member_count"])
        self.assertEqual(12.5, bear["total_holding_seconds"])
        serialized = str(result)
        for forbidden in (
            "toy-a", "toy-b", "restricted:a-pickup", "track_token",
            "identity_status", "identity_source", "details", "phone",
            "face_encoding",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_unique_name_empty_result_is_not_member_not_found_and_scope_does_not_expand(self) -> None:
        self._event("other-store", "merchandise_pick_up", datetime(2026, 8, 27, 2), store_id="store-2")
        self._event("other-camera", "merchandise_pick_up", datetime(2026, 8, 27, 2), camera_id="cam03")
        self._event("outside-time", "merchandise_pick_up", datetime(2026, 8, 27, 4, 1))

        result = self.service.get_member_behavior_summary(member_ref="高俊杰")

        self.assertEqual("ok", result["status"])
        self.assertEqual(0, result["behavior_summary"]["total_behavior_count"])
        self.assertEqual([], result["interacted_products"])
        self.assertEqual("not_applicable", result["data_quality"]["holding_duration"])
        self.assertEqual(
            {
                "store_id": "store-1",
                "camera_id": "cam02",
                "camera_scope": "single",
                "start_at": "2026-08-27T09:00:00+08:00",
                "end_at": "2026-08-27T12:00:00+08:00",
                "product_name": None,
            },
            result["scope"],
        )

    def test_inactive_missing_and_duplicate_active_names_fail_closed(self) -> None:
        with self.sessions() as db:
            db.add(Member(id="member-b", name="高俊杰", is_active=True))
            db.commit()
        for reference, code in (
            ("member-disabled", "member_not_found"),
            ("不存在", "member_not_found"),
            ("高俊杰", "member_ambiguous"),
        ):
            with self.subTest(reference=reference), self.assertRaisesRegex(
                MemberBehaviorError, f"^{code}$"
            ):
                self.service.get_member_behavior_summary(member_ref=reference)

    def test_only_offline_confirmed_identity_rows_are_eligible(self) -> None:
        rejected = (
            {"event_id": "probable", "assessment": "confirmed", "identity_status": "probable"},
            {"event_id": "ambiguous", "assessment": "confirmed", "identity_status": "ambiguous"},
            {"event_id": "conflict", "assessment": "confirmed", "identity_status": "conflict"},
            {"event_id": "unavailable", "assessment": "confirmed", "identity_status": "unavailable"},
            {"event_id": "anonymous", "assessment": "confirmed", "member_id": None},
            {"event_id": "demo", "assessment": "confirmed", "is_demo": True},
            {"event_id": "uncertain", "assessment": "uncertain"},
            {"event_id": "future", "event_type": "merchandise_future"},
            {"event_id": "live", "identity_status": "identified_member", "identity_source": "verified_face"},
        )
        for item in rejected:
            self._event(
                item["event_id"], item.get("event_type", "merchandise_pick_up"),
                datetime(2026, 8, 27, 2), member_id=item.get("member_id", "member-a"),
                assessment=item.get("assessment", "confirmed"),
                is_demo=item.get("is_demo", False),
                identity_status=item.get("identity_status", "confirmed"),
                identity_source=item.get("identity_source", "offline_track_association"),
            )
        result = self.service.get_member_behavior_summary(member_ref="member-a")
        self.assertEqual(0, result["behavior_summary"]["total_behavior_count"])

    def test_visit_and_device_mapping_cannot_infer_member_behavior(self) -> None:
        self._event("anonymous", "merchandise_pick_up", datetime(2026, 8, 27, 2), member_id=None)
        with self.sessions() as db:
            db.add(Visit(
                id="visit-inference", member_id="member-a", store_id="store-1",
                camera_id="cam02", entry_time=datetime(2026, 8, 27, 2),
            ))
            db.add(DeviceMemberMapping(
                id="mapping-inference", device_id="device-1", member_id="member-a",
                group_uuid="group-1", unique_id="unique-1", face_uuid="face-1",
            ))
            db.commit()
        result = self.service.get_member_behavior_summary(member_ref="member-a")
        self.assertEqual(0, result["behavior_summary"]["total_behavior_count"])

    def test_holding_duration_completeness_does_not_estimate(self) -> None:
        cases = (
            ("none", [], 0.0, "not_applicable"),
            ("all", [2.5, 3.5], 6.0, "available"),
            ("partial", [2.5, None], None, "partial_unavailable"),
            ("missing", [None, -1, math.nan, math.inf], None, "unavailable"),
        )
        for prefix, durations, expected_total, expected_status in cases:
            with self.subTest(case=prefix):
                with self.sessions() as db:
                    db.query(BehaviorEvent).delete()
                    db.commit()
                for index, duration in enumerate(durations):
                    self._event(
                        f"{prefix}-{index}", "merchandise_holding",
                        datetime(2026, 8, 27, 2, index), duration_seconds=duration,
                    )
                result = self.service.get_member_behavior_summary(member_ref="member-a")
                self.assertEqual(expected_total, result["behavior_summary"]["total_holding_seconds"])
                self.assertEqual(expected_status, result["data_quality"]["holding_duration"])

    def test_public_holding_totals_do_not_expose_floating_point_noise(self) -> None:
        self._event(
            "holding-a", "merchandise_holding", datetime(2026, 8, 27, 2),
            duration_seconds=21.44,
        )
        self._event(
            "holding-b", "merchandise_holding", datetime(2026, 8, 27, 2, 1),
            duration_seconds=6.4,
        )

        result = self.service.list_recent_member_behaviors()

        self.assertEqual(27.84, result["behavior_summary"]["total_holding_seconds"])

    def test_timeline_is_stable_limited_and_projects_only_allowlisted_fields(self) -> None:
        self._event("b", "merchandise_holding", datetime(2026, 8, 27, 2), duration_seconds=-1)
        self._event("a", "merchandise_pick_up", datetime(2026, 8, 27, 2))
        self._event("c", "merchandise_put_down", datetime(2026, 8, 27, 2, 1), product_name=...)

        result = self.service.list_member_behavior_timeline(member_ref="member-a", limit=2)

        self.assertTrue(result["has_more"])
        self.assertEqual(["拿起商品", "持有商品"], [item["behavior_label"] for item in result["timeline"]])
        self.assertEqual(["not_applicable", "unavailable"], [item["duration_status"] for item in result["timeline"]])
        self.assertEqual("product_1", result["timeline"][0]["product_ref"])
        serialized = str(result)
        for forbidden in (
            "product_key", "track_token", "binding_id", "evaluation_id",
            "identity_status", "identity_source", "details", "area_id", "fixture:a",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("behavior_summary", result)
        self.assertNotIn("interacted_products", result)

    def test_product_grouping_keeps_distinct_keys_and_missing_keys_separate(self) -> None:
        self._event("a", "merchandise_pick_up", datetime(2026, 8, 27, 2), product_key="key-a", product_name="同名")
        self._event("b", "merchandise_holding", datetime(2026, 8, 27, 2, 1), product_key="key-a", product_name="冲突名", duration_seconds=2)
        self._event("c", "merchandise_put_down", datetime(2026, 8, 27, 2, 2), product_key="key-b", product_name="同名")
        self._event("d", "merchandise_pick_up", datetime(2026, 8, 27, 2, 3), product_key=..., product_name="同名")
        self._event("e", "merchandise_pick_up", datetime(2026, 8, 27, 2, 4), product_key=..., product_name="同名")

        products = self.service.get_member_behavior_summary(member_ref="member-a")["interacted_products"]

        self.assertEqual(4, len(products))
        self.assertEqual(["product_1", "product_2", "product_3", "product_4"], [item["product_ref"] for item in products])
        conflicted = next(item for item in products if item["behavior_count"] == 2)
        self.assertIsNone(conflicted["product_name"])
        self.assertEqual("unavailable", conflicted["product_name_status"])
        self.assertEqual(2, conflicted["behavior_count"])
        self.assertEqual(2, sum(conflicted["behavior_counts"].values()))

    def test_product_filter_applies_before_timeline_limit_and_is_exact(self) -> None:
        for index in range(3):
            self._event(
                f"other-{index}", "merchandise_pick_up",
                datetime(2026, 8, 27, 1, index), product_key=f"other-{index}", product_name="其他",
            )
        self._event("target", "merchandise_pick_up", datetime(2026, 8, 27, 2), product_name="智能熊")
        self._event("target-2", "merchandise_put_down", datetime(2026, 8, 27, 2, 5), product_name="智能熊")

        result = self.service.list_member_behavior_timeline(
            member_ref="member-a", product_name=" 智能熊 ", product_provided=True, limit=1,
        )

        self.assertEqual(1, len(result["timeline"]))
        self.assertEqual("智能熊", result["timeline"][0]["product_name"])
        self.assertTrue(result["has_more"])
        for value in ("智能", "SMART BEAR", "智能熊A"):
            empty = self.service.get_member_behavior_summary(
                member_ref="member-a", product_name=value, product_provided=True,
            )
            self.assertEqual(0, empty["behavior_summary"]["total_behavior_count"])

    def test_recent_member_product_filter_applies_before_limit(self) -> None:
        for index in range(3):
            self._event(
                f"recent-other-{index}", "merchandise_pick_up",
                datetime(2026, 8, 27, 1, index),
                product_key=f"other-{index}", product_name="其他商品",
            )
        self._event(
            "recent-target", "merchandise_pick_up", datetime(2026, 8, 27, 2),
            product_key="target", product_name="智能熊",
        )

        result = self.service.list_recent_member_behaviors(
            product_name="智能熊", product_provided=True, limit=1,
        )

        self.assertEqual(1, result["behavior_summary"]["total_behavior_count"])
        self.assertEqual("智能熊", result["timeline"][0]["product_name"])
        self.assertFalse(result["has_more"])

    def test_recent_member_limit_selects_newest_events_then_returns_chronologically(self) -> None:
        for index in range(4):
            self._event(
                f"recent-{index}", "merchandise_pick_up",
                datetime(2026, 8, 27, 1, index),
            )

        result = self.service.list_recent_member_behaviors(limit=2)

        self.assertTrue(result["has_more"])
        self.assertEqual(
            [
                "2026-08-27T09:02:00+08:00",
                "2026-08-27T09:03:00+08:00",
            ],
            [item["occurred_at"] for item in result["timeline"]],
        )

    def test_limit_rejects_non_json_integers(self) -> None:
        for value in (True, 20.0, "20", None, 0, -1, 51):
            with self.subTest(value=value), self.assertRaisesRegex(
                MemberBehaviorError, "^invalid_arguments$"
            ):
                self.service.list_member_behavior_timeline(member_ref="member-a", limit=value)

    def test_store_wide_scope_rejects_explicit_camera_but_queries_all_store_cameras(self) -> None:
        context = StoreContext(
            store_id="store-1", default_camera_id=None,
            default_start_at=datetime(2026, 8, 27, 9),
            default_end_at=datetime(2026, 8, 27, 12), business_date=date(2026, 8, 27),
        )
        service = MemberBehaviorQueryService(
            SqlAlchemyMemberBehaviorReadAdapter(self.sessions),
            MemberBehaviorScopePolicy(context, "Asia/Shanghai"),
        )
        self._event("cam-a", "merchandise_pick_up", datetime(2026, 8, 27, 2), camera_id="cam-a")
        self._event("cam-b", "merchandise_pick_up", datetime(2026, 8, 27, 2), camera_id="cam-b")
        result = service.get_member_behavior_summary(member_ref="member-a")
        self.assertEqual(2, result["behavior_summary"]["total_behavior_count"])
        self.assertEqual("store_all", result["scope"]["camera_scope"])
        self.assertIsNone(result["scope"]["camera_id"])
        with self.assertRaisesRegex(MemberBehaviorError, "^scope_out_of_bounds$"):
            service.get_member_behavior_summary(
                member_ref="member-a", camera_id="cam-a", camera_provided=True
            )

    def test_scope_rejects_partial_out_of_bounds_and_aware_times(self) -> None:
        cases = (
            ({"start_at": datetime(2026, 8, 27, 10), "start_provided": True}, "invalid_time_range"),
            ({"start_at": datetime(2026, 8, 27, 8), "end_at": datetime(2026, 8, 27, 10), "start_provided": True, "end_provided": True}, "scope_out_of_bounds"),
            ({"start_at": datetime.fromisoformat("2026-08-27T10:00:00+08:00"), "end_at": datetime(2026, 8, 27, 11), "start_provided": True, "end_provided": True}, "invalid_time"),
            ({"store_id": "store-2", "store_provided": True}, "scope_out_of_bounds"),
            ({"camera_id": "cam03", "camera_provided": True}, "scope_out_of_bounds"),
        )
        for arguments, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(MemberBehaviorError, f"^{code}$"):
                self.service.get_member_behavior_summary(member_ref="member-a", **arguments)

    def test_dst_gap_and_fold_are_invalid(self) -> None:
        for start, end in (
            (datetime(2026, 3, 8, 2, 15), datetime(2026, 3, 8, 2, 45)),
            (datetime(2026, 11, 1, 1, 15), datetime(2026, 11, 1, 1, 45)),
        ):
            context = StoreContext(
                store_id="store-1", default_camera_id=None,
                default_start_at=start.replace(hour=0, minute=0),
                default_end_at=start.replace(hour=4, minute=0),
                business_date=start.date(),
            )
            policy = MemberBehaviorScopePolicy(context, "America/New_York")
            with self.subTest(start=start), self.assertRaisesRegex(MemberBehaviorError, "^invalid_time$"):
                policy.resolve(
                    start_at=start, end_at=end,
                    start_provided=True, end_provided=True,
                )

    def test_scope_is_validated_before_member_resolution(self) -> None:
        port = Mock()
        port.find_active_member_by_id.return_value = None
        port.find_active_members_by_name.return_value = []
        service = MemberBehaviorQueryService(port, self.policy)
        with self.assertRaisesRegex(MemberBehaviorError, "^scope_out_of_bounds$"):
            service.get_member_behavior_summary(
                member_ref="does-not-exist", store_id="other-store",
                store_provided=True,
            )
        port.find_active_member_by_id.assert_not_called()
        port.find_active_members_by_name.assert_not_called()

    def test_unknown_member_tier_is_projected_as_null(self) -> None:
        with self.sessions() as db:
            member = db.get(Member, "member-a")
            member.tier = "emerald"
            db.commit()
        result = self.service.get_member_behavior_summary(member_ref="member-a")
        self.assertIsNone(result["member"]["member_tier"])

    def test_dst_fall_back_utc_window_over_31_days_is_rejected(self) -> None:
        context = StoreContext(
            store_id="store-1", default_camera_id=None,
            default_start_at=datetime(2026, 10, 25),
            default_end_at=datetime(2026, 11, 25),
            business_date=date(2026, 10, 25),
        )
        with self.assertRaisesRegex(MemberBehaviorError, "^invalid_time_range$"):
            MemberBehaviorScopePolicy(context, "America/New_York")


if __name__ == "__main__":
    unittest.main()
