from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.mcp_server.server import (  # noqa: E402
    ProductAnalyticsMCPServer,
    build_member_agent_instructions,
    build_default_server,
)
from agent.analytics.member_behavior import MemberBehaviorError  # noqa: E402
from agent.models import Base, BehaviorEvent, Member  # noqa: E402
from agent.store_context import StoreContext  # noqa: E402


class FakeDispatcher:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    def list_tools(self):
        return [{"name": name, "description": name, "inputSchema": {}}
                for name in self.tools]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": json.dumps({"ok": True})}], "isError": False}


class MemberBehaviorMCPServerTests(unittest.TestCase):
    def _server(self, product: bool, member: bool) -> ProductAnalyticsMCPServer:
        return ProductAnalyticsMCPServer(
            FakeDispatcher(["rank_products"]),
            member_dispatcher=FakeDispatcher(["get_member_behavior_summary"]),
            product_enabled=product,
            member_enabled=member,
        )

    def test_tools_list_is_protocol_success_for_all_four_feature_combinations(self):
        for product, member, expected in (
            (False, False, 0), (True, False, 7), (False, True, 3), (True, True, 10)
        ):
            with self.subTest(product=product, member=member):
                server = self._server(product, member)
                server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                self.assertEqual(expected, len(listed["result"]["tools"]))
                self.assertNotIn("error", listed)

    def test_known_disabled_tools_return_same_tool_result_error_layer(self):
        server = self._server(False, False)
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        for name, expected in (
            ("rank_products", "product_analytics_mcp_disabled"),
            ("get_member_behavior_summary", "member_behavior_mcp_disabled"),
        ):
            response = server.handle({
                "jsonrpc": "2.0", "id": name, "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            })
            self.assertIn("result", response)
            self.assertTrue(response["result"]["isError"])
            self.assertEqual(expected, json.loads(response["result"]["content"][0]["text"])["error"])

    def test_unknown_tool_is_not_classified_as_disabled_feature(self):
        server = self._server(False, False)
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        response = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "not_a_tool", "arguments": {}},
        })
        self.assertTrue(response["result"]["isError"])
        self.assertEqual("unknown_tool", json.loads(response["result"]["content"][0]["text"])["error"])

    def test_enabled_dispatchers_are_called_and_disabled_dispatcher_is_not(self):
        server = self._server(True, True)
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "rank_products", "arguments": {}}})
        server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_member_behavior_summary", "arguments": {}}})
        self.assertEqual(1, len(server.product_dispatcher.calls))
        self.assertEqual(1, len(server.member_dispatcher.calls))


class MemberBehaviorServerCompositionTests(unittest.TestCase):
    def _database(self, directory: Path) -> Path:
        path = directory / "controlled-fixture.db"
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        self.assertEqual(directory.resolve(), path.resolve().parent)
        return path

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "RETAILVISION_STORE_ID": "store-1",
            "RETAILVISION_CAMERA_ID": "cam02",
            "RETAILVISION_ANALYSIS_START_AT": "2026-08-27T09:00:00",
            "RETAILVISION_ANALYSIS_END_AT": "2026-08-27T12:00:00",
            "RETAILVISION_TIMEZONE": "Asia/Shanghai",
            "RETAILVISION_PRODUCT_CATALOG_PROFILE": "stage10_smart_toys_v1",
        }

    def test_off_off_does_not_open_or_create_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-off-") as raw:
            path = Path(raw) / "must-not-exist.db"
            with patch("agent.mcp_server.server.create_engine") as create:
                server = build_default_server(
                    database_url=f"sqlite:///{path.as_posix()}",
                    product_enabled=False,
                    member_enabled=False,
                )
            self.assertFalse(create.called)
            self.assertFalse(path.exists())
            initialized = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
            })
            self.assertNotIn("instructions", initialized["result"])

    def test_real_startup_matrix_registers_only_enabled_features(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-matrix-") as raw:
            path = self._database(Path(raw))
            with patch.dict("os.environ", self._environment(), clear=False):
                for product, member, count in (
                    (True, False, 7), (False, True, 3), (True, True, 10),
                ):
                    with self.subTest(product=product, member=member):
                        server = build_default_server(
                            database_url=f"sqlite:///{path.as_posix()}",
                            product_enabled=product,
                            member_enabled=member,
                        )
                        try:
                            server.handle({
                                "jsonrpc": "2.0", "id": 1,
                                "method": "initialize", "params": {},
                            })
                            listed = server.handle({
                                "jsonrpc": "2.0", "id": 2,
                                "method": "tools/list", "params": {},
                            })
                            names = [item["name"] for item in listed["result"]["tools"]]
                            self.assertEqual(count, len(names))
                            self.assertEqual(count, len(set(names)))
                        finally:
                            server.close()

    def test_product_and_member_features_use_independent_database_and_scope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-dual-source-") as raw:
            directory = Path(raw)
            (directory / "product").mkdir()
            (directory / "member").mkdir()
            product_path = self._database(directory / "product")
            member_path = self._database(directory / "member")
            writer_engine = create_engine(f"sqlite:///{member_path.as_posix()}")
            sessions = sessionmaker(bind=writer_engine)
            with sessions() as db:
                db.add(Member(
                    id="member-cam03", name="高俊杰", tier="gold", is_active=True,
                ))
                at = datetime(2026, 9, 7, 6, 41)
                db.add(BehaviorEvent(
                    id="cam03-event", event_key="cam03:event",
                    event_type="merchandise_pick_up", assessment="confirmed",
                    camera_id="cam03", customer_track_id=1,
                    customer_track_token="restricted-track", store_id="store-1",
                    is_demo=False, member_id="member-cam03",
                    identity_status="confirmed",
                    identity_source="offline_track_association",
                    identity_binding_level="confirmed", occurred_at=at,
                    started_at=at, ended_at=at,
                    details={"product_key": "restricted-key", "product_name": "AI 电吉他"},
                ))
                db.commit()
            writer_engine.dispose()
            environment = {
                **self._environment(),
                "RETAILVISION_MEMBER_DATABASE_URL": f"sqlite:///{member_path.as_posix()}",
                "RETAILVISION_MEMBER_STORE_ID": "store-1",
                "RETAILVISION_MEMBER_CAMERA_ID": "cam03",
                "RETAILVISION_MEMBER_ANALYSIS_START_AT": "2026-09-07T14:00:00",
                "RETAILVISION_MEMBER_ANALYSIS_END_AT": "2026-09-07T15:00:00",
                "RETAILVISION_MEMBER_TIMEZONE": "Asia/Shanghai",
            }

            with patch.dict("os.environ", environment, clear=True):
                server = build_default_server(
                    database_url=f"sqlite:///{product_path.as_posix()}",
                    product_enabled=True,
                    member_enabled=True,
                )
            try:
                server.handle({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
                })
                listed = server.handle({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
                })
                self.assertEqual(10, len(listed["result"]["tools"]))
                response = server.handle({
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {
                        "name": "list_recent_member_behaviors", "arguments": {},
                    },
                })
                payload = json.loads(response["result"]["content"][0]["text"])
                self.assertEqual(1, payload["behavior_summary"]["total_behavior_count"])
                self.assertEqual("cam03", payload["scope"]["camera_id"])
                self.assertEqual("2026-09-07T14:41:00+08:00", payload["timeline"][0]["occurred_at"])
            finally:
                server.close()

    def test_store_wide_member_instructions_publish_independent_defaults_without_camera_placeholder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-store-wide-") as raw:
            path = self._database(Path(raw))
            environment = {
                **self._environment(),
                "RETAILVISION_MEMBER_STORE_ID": "store-1",
                "RETAILVISION_MEMBER_CAMERA_ID": "",
                "RETAILVISION_MEMBER_ANALYSIS_START_AT": "2026-09-07T14:00:00",
                "RETAILVISION_MEMBER_ANALYSIS_END_AT": "2026-09-07T15:00:00",
                "RETAILVISION_MEMBER_TIMEZONE": "Asia/Shanghai",
            }
            with patch.dict("os.environ", environment, clear=True):
                server = build_default_server(
                    database_url=f"sqlite:///{path.as_posix()}",
                    product_enabled=True, member_enabled=True,
                )
            try:
                initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                member_instructions = initialized["result"]["instructions"].split(
                    "RetailVision confirmed Offline Member Behavior tools.", 1
                )[1]
                self.assertNotIn("camera_id=store-wide", member_instructions)
                self.assertIn("omit camera_id", member_instructions)
                self.assertIn("start_at=2026-09-07T14:00:00", member_instructions)
                self.assertIn("end_at=2026-09-07T15:00:00", member_instructions)
                # Follow the documented default call through the real protocol and scope policy.
                response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "list_recent_member_behaviors", "arguments": {}}})
                self.assertFalse(response["result"].get("isError", False))
                payload = json.loads(response["result"]["content"][0]["text"])
                self.assertIsNone(payload["scope"]["camera_id"])
                self.assertEqual("store_all", payload["scope"]["camera_scope"])
                self.assertTrue(payload["scope"]["start_at"].startswith("2026-09-07T14:00:00"))
                # Fix the published instructions; never reinterpret invalid IDs or widen a request.
                for arguments in (
                    {"camera_id": "store-wide"}, {"camera_id": "cam02"},
                    {"start_at": environment["RETAILVISION_ANALYSIS_START_AT"],
                     "end_at": environment["RETAILVISION_ANALYSIS_END_AT"]},
                ):
                    denied = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "list_recent_member_behaviors", "arguments": arguments}})
                    self.assertTrue(denied["result"]["isError"])
                    self.assertEqual("scope_out_of_bounds", json.loads(denied["result"]["content"][0]["text"])["error"])
            finally:
                server.close()

    def test_member_only_does_not_read_product_catalog_or_validate_product(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-member-only-") as raw:
            path = self._database(Path(raw))
            environment = self._environment()
            environment.pop("RETAILVISION_PRODUCT_CATALOG_PROFILE")
            with (
                patch.dict("os.environ", environment, clear=False),
                patch(
                    "agent.mcp_server.server.configured_product_aliases",
                    side_effect=AssertionError("product catalog read"),
                ),
                patch("agent.mcp_server.server.require_product_analytics_schema") as product_validator,
            ):
                server = build_default_server(
                    database_url=f"sqlite:///{path.as_posix()}",
                    product_enabled=False,
                    member_enabled=True,
                )
            try:
                product_validator.assert_not_called()
            finally:
                server.close()

    def test_enabled_validator_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-fail-closed-") as raw:
            path = self._database(Path(raw))
            with (
                patch.dict("os.environ", self._environment(), clear=False),
                patch(
                    "agent.mcp_server.server.require_member_behavior_schema",
                    side_effect=RuntimeError("member schema failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "member schema failed"):
                    build_default_server(
                        database_url=f"sqlite:///{path.as_posix()}",
                        product_enabled=True,
                        member_enabled=True,
                    )

    def test_member_timezone_readiness_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-timezone-") as raw:
            path = self._database(Path(raw))
            environment = self._environment()
            environment["RETAILVISION_TIMEZONE"] = ""
            with patch.dict("os.environ", environment, clear=False):
                with self.assertRaisesRegex(
                    MemberBehaviorError, "^member_behavior_timezone_required$"
                ):
                    build_default_server(
                        database_url=f"sqlite:///{path.as_posix()}",
                        product_enabled=False,
                        member_enabled=True,
                    )

    def test_development_mode_allows_grounded_interest_analysis_and_advice(self) -> None:
        context = StoreContext(
            store_id="store-1", default_camera_id=None,
            default_start_at=datetime(2026, 9, 1),
            default_end_at=datetime(2026, 9, 10),
            business_date=datetime(2026, 9, 9).date(),
        )

        with patch.dict(
            "os.environ", {"STORE_STRICT_INFERENCE_BOUNDARY": "false"}, clear=False
        ):
            instructions = build_member_agent_instructions(
                context, "Asia/Shanghai"
            )

        self.assertIn("互动兴趣或关注度", instructions)
        self.assertIn("陈列、导购、活动", instructions)
        self.assertIn("不能声称已经购买或成交", instructions)
        self.assertIn(
            "未指定会员的最近、全部或整体会员行为",
            instructions,
        )
        self.assertIn("list_recent_member_behaviors", instructions)
        self.assertIn("会员商品分析", instructions)
        self.assertIn("不能用 rank_products 替代", instructions)
        self.assertIn("完整、综合或全量视觉互动报告必须组合", instructions)
        self.assertIn("不得因为用户没有明确写出“会员”就省略", instructions)
        self.assertNotIn(
            "Do not infer purchases, purchase intent, preferences", instructions
        )

    def test_controlled_fixture_protocol_smoke_is_temporary_and_query_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-smoke-") as raw:
            directory = Path(raw).resolve()
            path = self._database(directory)
            writer_engine = create_engine(f"sqlite:///{path.as_posix()}")
            sessions = sessionmaker(bind=writer_engine)
            with sessions() as db:
                db.add(Member(
                    id="fixture-member", name="高俊杰", tier="gold", is_active=True,
                ))
                at = datetime(2026, 8, 27, 2)
                db.add(BehaviorEvent(
                    id="fixture-event", event_key="fixture:event",
                    event_type="merchandise_pick_up", assessment="confirmed",
                    camera_id="cam02", customer_track_id=1,
                    customer_track_token="restricted-track", store_id="store-1",
                    is_demo=False, member_id="fixture-member",
                    identity_status="confirmed",
                    identity_source="offline_track_association",
                    identity_binding_level="confirmed", occurred_at=at,
                    started_at=at, ended_at=at,
                    details={"product_key": "restricted-key", "product_name": "智能熊"},
                ))
                db.commit()
            writer_engine.dispose()

            with patch.dict("os.environ", self._environment(), clear=False):
                server = build_default_server(
                    database_url=f"sqlite:///{path.as_posix()}",
                    product_enabled=False,
                    member_enabled=True,
                )
            try:
                server.handle({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
                })
                response = server.handle({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {
                        "name": "get_member_behavior_summary",
                        "arguments": {"member_ref": "高俊杰"},
                    },
                })
                payload = json.loads(response["result"]["content"][0]["text"])
                self.assertEqual(1, payload["behavior_summary"]["total_behavior_count"])
                self.assertEqual("2026-08-27T10:00:00+08:00", payload["behavior_summary"]["last_behavior_at"])
                serialized = json.dumps(payload, ensure_ascii=False)
                for forbidden in ("restricted-key", "restricted-track", "product_key"):
                    self.assertNotIn(forbidden, serialized)

                read_port = server.member_dispatcher.adapter.query_service.read_port
                with read_port.session_factory() as db:
                    with self.assertRaises(OperationalError):
                        db.execute(text("INSERT INTO members (id, name, is_active) VALUES ('write', 'write', 1)"))
            finally:
                server.close()

            self.assertEqual(directory, path.resolve().parent)

    def test_member_enabled_requires_explicit_store_and_analysis_window(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-config-required-") as raw:
            path = self._database(Path(raw))
            for missing, code in (
                ("RETAILVISION_STORE_ID", "store_context_store_id_required"),
                ("RETAILVISION_ANALYSIS_START_AT", "store_context_analysis_window_required"),
                ("RETAILVISION_ANALYSIS_END_AT", "store_context_analysis_window_required"),
            ):
                environment = self._environment()
                environment.pop(missing)
                with self.subTest(missing=missing), patch.dict(
                    "os.environ", environment, clear=True
                ):
                    with self.assertRaisesRegex(ValueError, code):
                        build_default_server(
                            database_url=f"sqlite:///{path.as_posix()}",
                            product_enabled=False, member_enabled=True,
                        )

    def test_member_disabled_does_not_require_member_scope_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mb01-config-off-") as raw:
            missing = Path(raw) / "not-created.db"
            environment = self._environment()
            for key in (
                "RETAILVISION_STORE_ID",
                "RETAILVISION_ANALYSIS_START_AT",
                "RETAILVISION_ANALYSIS_END_AT",
            ):
                environment.pop(key, None)
            with patch.dict("os.environ", environment, clear=True):
                server = build_default_server(
                    database_url=f"sqlite:///{missing.as_posix()}",
                    product_enabled=False, member_enabled=False,
                )
            server.close()

    def test_camera_environment_is_blank_to_none_and_nonblank_is_stripped(self) -> None:
        environment = self._environment()
        environment["RETAILVISION_CAMERA_ID"] = "   "
        with patch.dict("os.environ", environment, clear=True):
            self.assertIsNone(StoreContext.from_environment(require_explicit_scope=True).default_camera_id)
        environment["RETAILVISION_CAMERA_ID"] = "  cam02  "
        with patch.dict("os.environ", environment, clear=True):
            self.assertEqual("cam02", StoreContext.from_environment(require_explicit_scope=True).default_camera_id)

if __name__ == "__main__":
    unittest.main()
