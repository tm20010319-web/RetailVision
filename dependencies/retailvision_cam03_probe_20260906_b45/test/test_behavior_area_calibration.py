from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from scripts.calibrate_behavior_areas import (
    save_single_area_config,
    save_single_area_overlay,
)


class SingleBehaviorAreaCalibrationTests(unittest.TestCase):
    def test_single_interaction_area_saves_pixels_normalized_points_and_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "display_island.json"
            overlay_path = root / "display_island_overlay.jpg"
            points = [(100, 200), (400, 200), (450, 600), (80, 600)]

            payload = save_single_area_config(
                output=config_path,
                image_path="cam02_reference_1280x720.jpg",
                image_width=1280,
                image_height=720,
                area_id="display_island",
                area_type="interaction",
                points=points,
            )
            saved_overlay = save_single_area_overlay(
                image=np.zeros((720, 1280, 3), dtype=np.uint8),
                output=overlay_path,
                area_id="display_island",
                area_type="interaction",
                normalized_points=payload["display_island"]["points"],
            )

            stored = json.loads(config_path.read_text(encoding="utf-8"))
            overlay = cv2.imread(str(saved_overlay))

        self.assertEqual([[100, 200], [400, 200], [450, 600], [80, 600]], stored["display_island"]["pixel_points"])
        self.assertEqual("interaction", stored["display_island"]["area_type"])
        self.assertEqual([0.078125, 0.2777777777777778], stored["display_island"]["points"][0])
        self.assertIsNotNone(overlay)
        self.assertGreater(np.count_nonzero(overlay), 0)

    def test_single_area_rejects_unknown_type(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "supports interaction or display"):
                save_single_area_config(
                    output=Path(directory) / "invalid.json",
                    image_path="image.jpg",
                    image_width=1280,
                    image_height=720,
                    area_id="display_island",
                    area_type="product",
                    points=[(1, 1), (2, 1), (2, 2)],
                )

    def test_single_display_area_uses_display_overlay_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "smart_toy_display_zone.json"
            overlay_path = root / "smart_toy_display_zone_overlay.jpg"
            points = [(700, 300), (820, 300), (840, 430), (690, 430)]

            payload = save_single_area_config(
                output=config_path,
                image_path="cam02_products_reference_1280x720.jpg",
                image_width=1280,
                image_height=720,
                area_id="smart_toy_display_zone",
                area_type="display",
                points=points,
            )
            saved_overlay = save_single_area_overlay(
                image=np.zeros((720, 1280, 3), dtype=np.uint8),
                output=overlay_path,
                area_id="smart_toy_display_zone",
                area_type="display",
                normalized_points=payload["smart_toy_display_zone"]["points"],
            )

            stored = json.loads(config_path.read_text(encoding="utf-8"))
            overlay = cv2.imread(str(saved_overlay))

        self.assertEqual("display", stored["smart_toy_display_zone"]["area_type"])
        self.assertEqual(
            [[700, 300], [820, 300], [840, 430], [690, 430]],
            stored["smart_toy_display_zone"]["pixel_points"],
        )
        self.assertIsNotNone(overlay)
        self.assertGreater(np.count_nonzero(overlay), 0)


if __name__ == "__main__":
    unittest.main()
