from __future__ import annotations

import unittest

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from tools.acceptance.stage10_live_acceptance import evaluate_stage10_3_scenario  # noqa: E402


def _product(key: str, token: str) -> dict[str, object]:
    return {
        "product_key": key,
        "track_id": 1,
        "track_token": token,
        "track_status": "ACTIVE",
    }


def _frame(seq: int, *, persons: int = 1) -> dict[str, object]:
    return {
        "source_seq": seq,
        "timestamp": f"2026-08-16T10:00:{seq:02d}",
        "persons": [
            {
                "track_id": index + 1,
                "track_token": f"store-1:cam02:run:{index + 1}",
                "display_name": f"顾客{index + 1}",
            }
            for index in range(persons)
        ],
        "products": [
            _product("smart_toy_a", "product:bear:1"),
            _product("smart_toy_b", "product:elephant:1"),
        ],
        "interactions": [],
        "associations": [],
        "events": [],
    }


def _event(event_type: str, product_key: str, *, session: str = "session-1", actor: str = "store-1:cam02:run:1") -> dict[str, object]:
    return {
        "event_id": f"{session}:{event_type}",
        "event_type": event_type,
        "timestamp": "2026-08-16T10:00:02",
        "person": {"track_id": 1, "track_token": actor, "display_name": "顾客1"},
        "product": {"product_key": product_key},
        "interaction": {"interaction_session_id": session},
    }


class Stage103AcceptanceTests(unittest.TestCase):
    def test_scenario_a_passes_only_as_person_present_without_interaction(self):
        result = evaluate_stage10_3_scenario(
            "A",
            frames=[_frame(1), _frame(2)],
            events=[],
            overlay_path="videos/demo_person_present_no_interaction.mp4",
            keyframe_paths=["keyframes/a.jpg"],
        ).to_dict()

        self.assertEqual("A", result["scenario"])
        self.assertEqual("PASS", result["result"])
        self.assertEqual([1], result["person_track_ids"])
        self.assertEqual(["顾客1"], result["display_names"])
        self.assertEqual([], result["system_events"])
        self.assertEqual(0, result["false_positive_count"])
        self.assertEqual(0, result["false_pick_up_count"])
        self.assertEqual(0, result["false_put_down_count"])
        self.assertEqual(0, result["false_negative_count"])
        self.assertEqual(0, result["wrong_actor_count"])
        self.assertEqual(0, result["product_id_swap_count"])
        self.assertEqual(0, result["session_break_count"])
        self.assertEqual("videos/demo_person_present_no_interaction.mp4", result["overlay_path"])
        self.assertEqual(["keyframes/a.jpg"], result["keyframe_paths"])

    def test_scenario_b_requires_association_without_pickup(self):
        frames = [_frame(1), _frame(2, persons=2)]
        frames[-1]["associations"] = [{
            "product_key": "smart_toy_a",
            "association_status": "ASSOCIATED",
            "associated_person_track_token": "store-1:cam02:run:1",
        }]

        result = evaluate_stage10_3_scenario(
            "B",
            frames=frames,
            events=[],
            overlay_path="videos/demo_approach_no_pickup.mp4",
        ).to_dict()

        self.assertEqual("PASS", result["result"])
        self.assertTrue(result["diagnostics"]["association_observed"])
        self.assertEqual(0, result["false_positive_count"])
        self.assertEqual(0, result["false_pick_up_count"])
        self.assertEqual(0, result["false_put_down_count"])

    def test_scenario_a_rejects_person_track_switch(self):
        frames = [_frame(1), _frame(2)]
        frames[1]["persons"][0]["track_id"] = 2
        frames[1]["persons"][0]["track_token"] = "store-1:cam02:run:2"

        result = evaluate_stage10_3_scenario(
            "A",
            frames=frames,
            events=[],
            overlay_path="videos/demo_person_present_no_interaction.mp4",
        ).to_dict()

        self.assertEqual("FAIL", result["result"])
        self.assertFalse(result["diagnostics"]["single_person_track_continuity"])

    def test_scenario_a_rejects_product_motion(self):
        frames = [_frame(1), _frame(2)]
        frames[1]["interactions"] = [{
            "product_key": "smart_toy_a",
            "motion_state": "MOVING",
            "interaction_state": "IDLE",
        }]

        result = evaluate_stage10_3_scenario(
            "A",
            frames=frames,
            events=[],
            overlay_path="videos/demo_person_present_no_interaction.mp4",
        ).to_dict()

        self.assertEqual("FAIL", result["result"])
        self.assertFalse(result["diagnostics"]["products_stable"])

    def test_scenarios_c_and_d_require_one_complete_target_session(self):
        cases = (("C", "smart_toy_a"), ("D", "smart_toy_b"))
        for scenario, product_key in cases:
            with self.subTest(scenario=scenario):
                frames = [_frame(1), _frame(2), _frame(3)]
                frames[1]["interactions"] = [{
                    "product_key": product_key,
                    "interaction_state": "HELD",
                    "interaction_session_id": "session-1",
                    "interaction_actor_person_track_token": "store-1:cam02:run:1",
                }]
                events = [
                    _event("PICK_UP", product_key),
                    _event("PUT_DOWN", product_key),
                ]

                result = evaluate_stage10_3_scenario(
                    scenario,
                    frames=frames,
                    events=events,
                    expected_product_key=product_key,
                    expected_actor_track_token="store-1:cam02:run:1",
                    overlay_path=f"videos/demo_{scenario.lower()}.mp4",
                ).to_dict()

                self.assertEqual("PASS", result["result"])
                self.assertEqual(0, result["false_positive_count"])
                self.assertEqual(0, result["false_negative_count"])
                self.assertEqual(0, result["wrong_actor_count"])
                self.assertEqual(0, result["session_break_count"])
                self.assertEqual(1, result["diagnostics"]["pickup_count"])
                self.assertEqual(1, result["diagnostics"]["putdown_count"])
                self.assertTrue(result["diagnostics"]["held_observed"])

    def test_scenario_e_requires_occlusion_and_recovery_in_one_session(self):
        frames = [_frame(1), _frame(2), _frame(3), _frame(4)]
        frames[1]["interactions"] = [{
            "product_key": "smart_toy_a",
            "interaction_state": "HELD",
            "interaction_session_id": "session-1",
            "product_track_status": "MISSING_PENDING",
            "held_missing_grace": True,
            "recovered_from_missing": False,
        }]
        frames[2]["interactions"] = [{
            "product_key": "smart_toy_a",
            "interaction_state": "HELD",
            "interaction_session_id": "session-1",
            "product_track_status": "ACTIVE",
            "held_missing_grace": False,
            "recovered_from_missing": True,
        }]
        events = [
            _event("PICK_UP", "smart_toy_a"),
            _event("PUT_DOWN", "smart_toy_a"),
        ]

        result = evaluate_stage10_3_scenario(
            "E",
            frames=frames,
            events=events,
            expected_product_key="smart_toy_a",
            expected_actor_track_token="store-1:cam02:run:1",
            overlay_path="videos/demo_occlusion_recovery.mp4",
        ).to_dict()

        self.assertEqual("PASS", result["result"])
        self.assertTrue(result["diagnostics"]["occlusion_observed"])
        self.assertTrue(result["diagnostics"]["recovery_observed"])
        self.assertEqual(["session-1"], result["diagnostics"]["interaction_session_ids"])

    def test_scenario_f_requires_two_people_and_keeps_the_operator_actor(self):
        frames = [_frame(1, persons=2), _frame(2, persons=2), _frame(3, persons=2)]
        frames[1]["interactions"] = [{
            "product_key": "smart_toy_a",
            "interaction_state": "HELD",
            "interaction_session_id": "session-1",
            "interaction_actor_person_track_token": "store-1:cam02:run:1",
        }]
        events = [
            _event("PICK_UP", "smart_toy_a"),
            _event("PUT_DOWN", "smart_toy_a"),
        ]

        passed = evaluate_stage10_3_scenario(
            "F",
            frames=frames,
            events=events,
            expected_product_key="smart_toy_a",
            expected_actor_track_token="store-1:cam02:run:1",
            overlay_path="videos/demo_multi_person.mp4",
        ).to_dict()
        wrong_events = [events[0], _event(
            "PUT_DOWN",
            "smart_toy_a",
            actor="store-1:cam02:run:2",
        )]
        failed = evaluate_stage10_3_scenario(
            "F",
            frames=frames,
            events=wrong_events,
            expected_product_key="smart_toy_a",
            expected_actor_track_token="store-1:cam02:run:1",
            overlay_path="videos/demo_multi_person_wrong_actor.mp4",
        ).to_dict()

        self.assertEqual("PASS", passed["result"])
        self.assertTrue(passed["diagnostics"]["multi_person_observed"])
        self.assertEqual(0, passed["wrong_actor_count"])
        self.assertEqual("FAIL", failed["result"])
        self.assertEqual(1, failed["wrong_actor_count"])


if __name__ == "__main__":
    unittest.main()
