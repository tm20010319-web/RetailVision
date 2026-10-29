import unittest
from pathlib import Path

from agent.product_camera_config import load_dpi01c_product_camera_config, validate_formal_replay_readiness, ProductCameraConfigError


class ReplayReadinessTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("product_cameras.cam03.dpi01c.dpi01b_action.stage7c.json")
        self.config = load_dpi01c_product_camera_config(self.path)

    def test_confirmed_config_requires_complete_mask_confirmation(self):
        with self.assertRaisesRegex(ProductCameraConfigError, "mask confirmation"):
            validate_formal_replay_readiness(self.config, {key: "required" for key in self.config.selected_product_keys})

    def test_confirmed_config_accepts_complete_confirmation(self):
        confirmations = {key: {"confirmation_status": "confirmed"} for key in self.config.selected_product_keys}
        validate_formal_replay_readiness(self.config, confirmations)

    def test_unknown_mode_fails_closed(self):
        self.assertEqual("fixed_support_contact", self.config.display_evidence_mode)


if __name__ == "__main__":
    unittest.main()
