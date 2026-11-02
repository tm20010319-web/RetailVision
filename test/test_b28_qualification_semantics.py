import unittest
import numpy as np

from agent.behavior.sam31_realtime_providers import _validate_contexts
from agent.runtime.stage10_demo import RealtimeFrameContext


def _contexts(count):
    return tuple(
        RealtimeFrameContext(
            camera_id="cam02",
            source_seq=index,
            captured_at=__import__("datetime").datetime(2026, 1, 1),
            frame_width=8,
            frame_height=8,
            frame=np.zeros((8, 8, 3), dtype=np.uint8),
        )
        for index in range(count)
    )


class B28QualificationSemanticsTests(unittest.TestCase):
    def test_normal_window_still_requires_twelve_frames(self):
        with self.assertRaises(Exception):
            _validate_contexts(_contexts(5), window_no=1)

    def test_terminal_window_accepts_real_pending_frames_after_overlap(self):
        values = _contexts(5)  # four overlap frames plus one fresh EOF sample
        result = _validate_contexts(values, window_no=1, terminal=True)
        self.assertEqual(5, len(result))

    def test_terminal_window_cannot_contain_only_overlap(self):
        with self.assertRaises(Exception):
            _validate_contexts(_contexts(4), window_no=1, terminal=True)


if __name__ == "__main__":
    unittest.main()
