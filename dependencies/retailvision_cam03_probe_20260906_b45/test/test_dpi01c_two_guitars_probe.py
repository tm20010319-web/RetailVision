import unittest

from tools.probe_dpi01c_two_guitars import parse_args


class Dpi01C2GuitarProbeCliTests(unittest.TestCase):
    def _args(self, *extra):
        return parse_args([
            "--video", "video.mp4",
            "--camera-config", "camera.json",
            "--analysis-run-id", "run",
            "--checkpoint-path", "checkpoint.pt",
            "--output-dir", "output",
            *extra,
        ])

    def test_negative_point_defaults_are_applied_once_at_runtime(self):
        args = self._args()
        self.assertIsNone(args.electric_negative)
        self.assertIsNone(args.guitar_negative)

    def test_explicit_negative_points_do_not_include_defaults(self):
        args = self._args(
            "--electric-negative", "860,450",
            "--guitar-negative", "840,455",
        )
        self.assertEqual(["860,450"], args.electric_negative)
        self.assertEqual(["840,455"], args.guitar_negative)

    def test_additional_positive_points_are_opt_in_and_repeatable(self):
        defaults = self._args()
        self.assertIsNone(defaults.electric_additional_positive)
        self.assertIsNone(defaults.guitar_additional_positive)
        args = self._args(
            "--electric-additional-positive", "803,380",
            "--electric-additional-positive", "812,440",
            "--guitar-additional-positive", "899,405",
        )
        self.assertEqual("803,458", args.electric_positive)
        self.assertEqual(["803,380", "812,440"], args.electric_additional_positive)
        self.assertEqual(["899,405"], args.guitar_additional_positive)

    def test_first_product_order_keeps_existing_default(self):
        self.assertEqual("ai_electric_guitar", self._args().first_product)
        self.assertEqual("ai_guitar", self._args("--first-product", "ai_guitar").first_product)


if __name__ == "__main__":
    unittest.main()
