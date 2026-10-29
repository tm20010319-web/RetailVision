import unittest

from tools.analyze_dpi01c2_action_windows import (
    REQUIRED_STATES,
    build_action_evidence,
    classify_product_action_window,
)


def _row(source_seq, *, physical, current=0.70, bottom=False, contact=False, bbox=None):
    if bbox is None:
        bbox = [760, 350, 820, 520]
    return {
        "source_seq": source_seq,
        "source_pts": source_seq * 512,
        "presentation_seconds": source_seq / 25.0,
        "objects": [{
            "product_key": "moving",
            "current_expanded_region": {
                "bbox": bbox,
                "centroid_inside": True,
                "surface_overlap_ratio": current,
                "mask_area": 1000,
            },
            "physical_tabletop": {
                "bbox": bbox,
                "centroid_inside": physical >= 0.45,
                "surface_overlap_ratio": physical,
                "bbox_bottom_center_inside": bottom,
                "lowest_contact_point_inside": contact,
                "mask_area": 1000,
            },
        }],
    }


class Dpi01C2ActionWindowTests(unittest.TestCase):
    def test_complete_sequence_and_expanded_surface_conflict(self):
        rows = []
        rows.extend(_row(index, physical=0.52, bottom=True, contact=True) for index in range(5))
        rows.extend([
            _row(5, physical=0.30, bottom=True, contact=True),
            _row(6, physical=0.10, bottom=True, contact=True),
        ])
        rows.extend(_row(index, physical=0.0, current=0.70) for index in range(7, 13))
        rows.extend([
            _row(13, physical=0.10, bottom=True, contact=True),
            _row(14, physical=0.30, bottom=True, contact=True),
        ])
        rows.extend(_row(index, physical=0.52, bottom=True, contact=True) for index in range(15, 21))

        result = classify_product_action_window(rows, "moving")

        self.assertEqual(list(REQUIRED_STATES), result["observed_states"])
        self.assertTrue(result["transition_complete"])
        self.assertEqual("moving_target", result["role"])
        self.assertTrue(result["expanded_region_held_conflict"])
        self.assertEqual(
            ["stationary_on_stand", "just_leaving_stand", "held", "returned"],
            [item["state"] for item in result["state_intervals"]],
        )

    def test_static_control_does_not_depart_on_bbox_jitter(self):
        rows = [
            _row(index, physical=0.70, bottom=False, contact=False,
                 bbox=[760 + index, 350, 820 + index, 520])
            for index in range(8)
        ]
        result = classify_product_action_window(rows, "moving")

        self.assertEqual(["stationary_on_stand"], result["observed_states"])
        self.assertFalse(result["transition_complete"])
        self.assertEqual("stationary_control", result["role"])
        self.assertFalse(result["expanded_region_held_conflict"])

    def test_evidence_keeps_physical_reference_outside_runtime_gate(self):
        rows = [_row(index, physical=0.52, bottom=True, contact=True) for index in range(3)]
        evidence = build_action_evidence(
            rows,
            camera_id="cam03",
            source_video_sha256="video-sha",
            time_base={"num": 1, "den": 12800},
            selected_product_keys=["moving"],
            product_display_surface_pixels=[[515, 300], [980, 300], [980, 545], [515, 545]],
            physical_tabletop_pixels=[[520, 454], [952, 452], [968, 526], [521, 531]],
        )

        self.assertTrue(evidence["product_display_surface"]["runtime_gate"])
        self.assertFalse(evidence["physical_tabletop_reference"]["runtime_gate"])
        self.assertFalse(evidence["behavior_events_emitted"])
        self.assertTrue(evidence["runtime_state_machine_modified"] is False)


if __name__ == "__main__":
    unittest.main()
