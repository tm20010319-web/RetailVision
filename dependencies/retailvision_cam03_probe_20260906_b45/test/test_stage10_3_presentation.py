from __future__ import annotations

import unittest
from datetime import datetime
import tempfile
from pathlib import Path

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.stage10_presentation import (  # noqa: E402
    chinese_status,
    product_presentation,
    resolve_person_display_identity,
)
from agent.product_interaction import (  # noqa: E402
    ProductInteractionEvent,
    ProductInteractionEventType,
    ProductMotionState,
)
from agent.sam31_product_tracking import ProductDisplayState  # noqa: E402
from agent.stage10_demo import (  # noqa: E402
    ConsoleEventSink,
    JsonlEventSink,
    Stage10DemoEvent,
)


class Stage103PresentationTests(unittest.TestCase):
    def test_product_names_are_chinese_first_without_changing_internal_keys(self):
        bear = product_presentation("smart_toy_a")
        elephant = product_presentation("smart_toy_b")

        self.assertEqual("smart_toy_a", bear.internal_key)
        self.assertEqual("smart bear", bear.english_name)
        self.assertEqual("智能熊", bear.chinese_name)
        self.assertEqual("智能熊（smart bear）", bear.display_name)
        self.assertEqual("smart_toy_b", elephant.internal_key)
        self.assertEqual("smart elephant", elephant.english_name)
        self.assertEqual("智能小象", elephant.chinese_name)
        self.assertEqual("智能小象（smart elephant）", elephant.display_name)

    def test_cam03_guitar_keys_have_presentation_labels(self):
        electric = product_presentation("ai_electric_guitar")
        guitar = product_presentation("ai_guitar")

        self.assertEqual("AI电吉他（AI electric guitar）", electric.display_name)
        self.assertEqual("AI吉他（AI guitar）", guitar.display_name)

    def test_status_labels_follow_the_chinese_demo_vocabulary(self):
        expected = {
            "PICK_UP": "拿起",
            "HELD": "持有中",
            "HOLDING": "持有中",
            "PUT_DOWN": "放下",
            "STABLE": "稳定",
            "MOVING": "移动中",
            "MISSING": "暂时遮挡",
            "IDLE": "空闲",
            "ACTIVE": "追踪中",
            "MISSING_PENDING": "暂时遮挡",
            "LOST": "跟踪丢失",
            "ASSOCIATED": "已关联",
            "AMBIGUOUS": "关联不明确",
        }
        self.assertEqual(expected, {key: chinese_status(key) for key in expected})

    def test_person_display_name_falls_back_to_global_track_id(self):
        first = resolve_person_display_identity(1)
        second = resolve_person_display_identity(2)

        self.assertEqual("顾客1", first.display_name)
        self.assertEqual("顾客2", second.display_name)
        self.assertEqual("顾客", first.display_role)
        self.assertIsNone(first.member_id)
        self.assertIsNone(first.member_name)

    def test_member_name_has_priority_and_member_id_is_preserved(self):
        identity = resolve_person_display_identity(
            7,
            {"member_id": "member_001", "member_name": "张三"},
        )

        self.assertEqual("会员：张三", identity.display_name)
        self.assertEqual("会员", identity.display_role)
        self.assertEqual("member_001", identity.member_id)
        self.assertEqual("张三", identity.member_name)

    def test_member_id_is_used_when_name_is_missing_and_late_name_can_replace_it(self):
        id_only = resolve_person_display_identity(7, {"member_id": "member_001"})
        enriched = resolve_person_display_identity(
            7,
            {"member_id": "member_001", "member_name": "张三"},
        )

        self.assertEqual("会员 member_001", id_only.display_name)
        self.assertEqual("会员：张三", enriched.display_name)

    def test_unified_event_keeps_domain_identity_and_adds_chinese_display_fields(self):
        event = ProductInteractionEvent(
            event_id="event-1",
            event_type=ProductInteractionEventType.PICK_UP,
            event_sequence=1,
            observed_at=datetime(2026, 8, 16, 10, 0, 0),
            source_seq=42,
            camera_id="cam02",
            product_key="smart_toy_a",
            product_track_id=1,
            product_track_token="product:cam02:run:smart_toy_a:1",
            person_track_id=7,
            person_track_token="store-1:cam02:run:7",
            interaction_session_id="session-1",
            confidence=0.95,
            motion_state=ProductMotionState.MOVING,
            display_state=ProductDisplayState.OUTSIDE_DISPLAY,
            holding_duration_seconds=None,
            evidence={},
        )
        identity = resolve_person_display_identity(
            7,
            {"member_id": "member_001", "member_name": "张三"},
        )

        payload = Stage10DemoEvent.from_event(
            event,
            person_identity=identity,
        ).to_dict()

        self.assertEqual("store-1:cam02:run:7", payload["person"]["track_token"])
        self.assertEqual("会员：张三", payload["person"]["display_name"])
        self.assertEqual("smart_toy_a", payload["product"]["product_key"])
        self.assertEqual("智能熊（smart bear）", payload["product"]["display_name"])
        self.assertEqual("拿起", payload["display"]["event_name"])
        self.assertEqual("会员：张三 拿起了 智能熊", payload["display"]["message"])

    def test_event_jsonl_keeps_chinese_display_text_human_readable(self):
        event = ProductInteractionEvent(
            event_id="event-utf8",
            event_type=ProductInteractionEventType.PICK_UP,
            event_sequence=1,
            observed_at=datetime(2026, 8, 16, 10, 0, 0),
            source_seq=42,
            camera_id="cam02",
            product_key="smart_toy_a",
            product_track_id=1,
            product_track_token="product:cam02:run:smart_toy_a:1",
            person_track_id=7,
            person_track_token="store-1:cam02:run:7",
            interaction_session_id="session-1",
            confidence=0.95,
            motion_state=ProductMotionState.MOVING,
            display_state=ProductDisplayState.OUTSIDE_DISPLAY,
            holding_duration_seconds=None,
            evidence={},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            sink = JsonlEventSink(path)
            display_event = Stage10DemoEvent.from_event(event)
            sink.emit(display_event)
            sink.close()
            raw = path.read_text(encoding="utf-8")
        console_lines = []
        ConsoleEventSink(console_lines.append).emit(display_event)

        self.assertIn("顾客7 拿起了 智能熊", raw)
        self.assertNotIn("\\u987e", raw)
        self.assertIn("顾客7 拿起了 智能熊", console_lines[0])


if __name__ == "__main__":
    unittest.main()
