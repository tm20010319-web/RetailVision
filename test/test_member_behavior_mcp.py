from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.analytics.member_behavior import MemberBehaviorError  # noqa: E402
from agent.analytics.member_behavior_mcp import MemberBehaviorMCPTools  # noqa: E402
from agent.store_context import StoreContext  # noqa: E402


class RecordingMemberBehaviorService:
    def __init__(self) -> None:
        self.calls = []

    def get_member_behavior_summary(self, **kwargs):
        self.calls.append(("summary", kwargs))
        return {"status": "ok", "kind": "summary"}

    def list_member_behavior_timeline(self, **kwargs):
        self.calls.append(("timeline", kwargs))
        return {"status": "ok", "kind": "timeline"}

    def list_recent_member_behaviors(self, **kwargs):
        self.calls.append(("recent", kwargs))
        return {"status": "ok", "kind": "recent"}


class MemberBehaviorMCPToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = RecordingMemberBehaviorService()
        self.tools = MemberBehaviorMCPTools(self.service)

    def test_tool_schemas_are_fixed_and_do_not_accept_internal_fields(self) -> None:
        tools = self.tools.list_tools()
        self.assertEqual(
            [
                "get_member_behavior_summary",
                "list_member_behavior_timeline",
                "list_recent_member_behaviors",
            ],
            [item["name"] for item in tools],
        )
        for tool in tools[:2]:
            schema = tool["inputSchema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(["member_ref"], schema["required"])
            for forbidden in (
                "product_key", "product_ref", "product_track_token",
                "normalized_fact_ref", "camera_scope", "cursor", "offset",
            ):
                self.assertNotIn(forbidden, schema["properties"])
        self.assertNotIn("limit", tools[0]["inputSchema"]["properties"])
        self.assertEqual(50, tools[1]["inputSchema"]["properties"]["limit"]["maximum"])
        recent_schema = tools[2]["inputSchema"]
        self.assertEqual([], recent_schema["required"])
        self.assertNotIn("member_ref", recent_schema["properties"])
        self.assertEqual(100, recent_schema["properties"]["limit"]["default"])
        self.assertEqual(200, recent_schema["properties"]["limit"]["maximum"])

    def test_summary_parses_local_naive_time_and_trims_product_name(self) -> None:
        result = self.tools.call("get_member_behavior_summary", {
            "member_ref": "member-a",
            "store_id": "store-1",
            "camera_id": "cam02",
            "start_at": "2026-08-27T09:00:00",
            "end_at": "2026-08-27T10:00:00",
            "product_name": " 智能熊 ",
        })
        self.assertEqual("summary", result["kind"])
        _, arguments = self.service.calls[-1]
        self.assertEqual(datetime(2026, 8, 27, 9), arguments["start_at"])
        self.assertEqual("智能熊", arguments["product_name"])
        self.assertTrue(arguments["store_provided"])
        self.assertTrue(arguments["camera_provided"])
        self.assertTrue(arguments["product_provided"])

    def test_unknown_fields_invalid_types_and_offset_time_are_rejected(self) -> None:
        cases = (
            ({"member_ref": "member-a", "product_key": "secret"}, "invalid_arguments"),
            ({"member_ref": "member-a", "product_name": " "}, "invalid_arguments"),
            ({"member_ref": "member-a", "start_at": "2026-08-27T09:00:00Z", "end_at": "2026-08-27T10:00:00"}, "invalid_time"),
            ({"member_ref": "member-a", "start_at": "2026-08-27T09:00:00"}, "invalid_time_range"),
            ({"member_ref": "member-a", "limit": None}, "invalid_arguments"),
            ({"member_ref": "member-a", "limit": True}, "invalid_arguments"),
        )
        for arguments, code in cases:
            name = "list_member_behavior_timeline" if "limit" in arguments else "get_member_behavior_summary"
            with self.subTest(arguments=arguments), self.assertRaisesRegex(MemberBehaviorError, f"^{code}$"):
                self.tools.call(name, arguments)

    def test_null_optional_scope_values_are_distinct_from_invalid_empty_values(self) -> None:
        self.tools.call("get_member_behavior_summary", {
            "member_ref": "member-a", "store_id": None,
            "product_name": None,
        })
        _, arguments = self.service.calls[-1]
        self.assertTrue(arguments["store_provided"])
        self.assertFalse(arguments["camera_provided"])
        self.assertTrue(arguments["product_provided"])
        for name in ("store_id", "camera_id"):
            with self.subTest(name=name), self.assertRaisesRegex(MemberBehaviorError, "^invalid_arguments$"):
                self.tools.call("get_member_behavior_summary", {"member_ref": "member-a", name: ""})
        with self.assertRaisesRegex(MemberBehaviorError, "^invalid_arguments$"):
            self.tools.call(
                "get_member_behavior_summary",
                {"member_ref": "member-a", "camera_id": None},
            )

    def test_unknown_tool_and_non_object_arguments_fail_stably(self) -> None:
        with self.assertRaisesRegex(MemberBehaviorError, "^unknown_member_behavior_tool$"):
            self.tools.call("unknown", {})
        with self.assertRaisesRegex(MemberBehaviorError, "^invalid_arguments$"):
            self.tools.call("get_member_behavior_summary", [])


if __name__ == "__main__":
    unittest.main()
