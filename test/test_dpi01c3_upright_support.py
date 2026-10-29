import unittest
from datetime import datetime, timezone

import numpy as np

from agent.sam31_product_tracking import MultiProductTrackRuntime, ProductCandidate, ProductDisplaySurface, ProductDisplayState


class UprightSupportTests(unittest.TestCase):
    def setUp(self):
        self.surface = ProductDisplaySurface(((0.4, 0.4), (0.8, 0.4), (0.8, 0.8), (0.4, 0.8)))

    def candidate(self, bottom):
        mask = np.zeros((720, 1280), dtype=bool)
        top = min(300, bottom - 30)
        mask[top:bottom, 780:810] = True
        ys, xs = np.where(mask)
        return ProductCandidate(1, (float(xs.min()), float(ys.min()), float(xs.max()+1), float(ys.max()+1)), (float(xs.mean()), float(ys.mean())), 1.0, mask)

    def runtime(self):
        return MultiProductTrackRuntime(
            camera_id="cam03", runtime_instance_id="support-test", products={"guitar": "smart_guitar"},
            product_display_surface=self.surface, support_contact_points={"guitar": (795/1280, 520/720)},
            display_evidence_mode="fixed_support_contact",
        )

    def test_upright_support_keeps_standing_guitar_in_display(self):
        runtime = self.runtime()
        result = runtime.initialize_assignments(assignments={"guitar": self.candidate(521)}, observed_at=datetime.now(timezone.utc), source_seq=1)
        self.assertEqual(ProductDisplayState.IN_DISPLAY, result[0].display_state)

    def test_lifted_guitar_is_outside_even_when_expanded_surface_overlaps(self):
        runtime = self.runtime()
        runtime.initialize_assignments(assignments={"guitar": self.candidate(521)}, observed_at=datetime.now(timezone.utc), source_seq=1)
        result = runtime.process_window(candidates=[self.candidate(376)], observed_at=datetime.now(timezone.utc), source_seq=2, window_no=1)
        self.assertEqual(ProductDisplayState.OUTSIDE_DISPLAY, result[0].display_state)

    def test_support_point_is_required_for_initialization(self):
        runtime = self.runtime()
        with self.assertRaisesRegex(ValueError, "support contact point"):
            runtime.initialize_assignments(assignments={"guitar": self.candidate(450)}, observed_at=datetime.now(timezone.utc), source_seq=1)

    def test_diagnostic_reason_is_fixed_support_confirmation_or_outside(self):
        runtime = self.runtime()
        confirmed = runtime.initialize_assignments(assignments={"guitar": self.candidate(521)}, observed_at=datetime.now(timezone.utc), source_seq=1)[0]
        self.assertEqual("FIXED_SUPPORT_CONTACT_CONFIRMED", confirmed.display_state_reason)
        outside = runtime.process_window(candidates=[self.candidate(450)], observed_at=datetime.now(timezone.utc), source_seq=2, window_no=1)[0]
        self.assertEqual("SUPPORT_CONTACT_OUTSIDE_TOLERANCE", outside.display_state_reason)


if __name__ == "__main__":
    unittest.main()
