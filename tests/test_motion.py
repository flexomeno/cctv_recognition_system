import unittest

import numpy as np

from app.config import MotionConfig
from app.motion import MotionGate


def gate(**overrides):
    values = dict(
        diff_threshold=30,
        min_area_ratio=0.02,
        blur=0,
        confirm_frames=2,
        frame_gap=1,
        cooldown_s=12,
        analysis_width=80,
    )
    values.update(overrides)
    return MotionGate(MotionConfig(**values))


def blank():
    return np.zeros((60, 80, 3), dtype=np.uint8)


def with_block(x: int) -> np.ndarray:
    frame = blank()
    frame[10:40, x : x + 30] = 255
    return frame


class MotionTest(unittest.TestCase):
    def test_solid_block_fires_after_the_confirm_frames(self):
        detector = gate()
        self.assertEqual(detector.update(blank())[0], False)
        self.assertEqual(detector.update(blank())[0], False)
        fired, ratio = detector.update(with_block(10))
        self.assertFalse(fired)
        self.assertGreater(ratio, 0.02)
        fired, _ratio = detector.update(with_block(40))
        self.assertTrue(fired)

    def test_scattered_specks_do_not_fire(self):
        detector = gate(min_area_ratio=0.01, confirm_frames=1)
        quiet = blank()
        detector.update(quiet)
        detector.update(quiet)
        noisy = blank()
        noisy[5, 5] = 255
        noisy[20, 40] = 255
        noisy[50, 70] = 255
        noisy[30, 10] = 255
        fired, ratio = detector.update(noisy)
        self.assertFalse(fired)
        self.assertLess(ratio, 0.01)

    def test_a_block_that_stopped_does_not_keep_triggering(self):
        detector = gate(confirm_frames=1)
        still = with_block(20)
        detector.update(blank())
        self.assertTrue(detector.update(still)[0])
        fired, ratio = detector.update(still)
        self.assertFalse(fired)
        self.assertLess(ratio, 0.02)

    def test_a_slow_shift_shows_up_across_the_gap(self):
        detector = gate(frame_gap=3, confirm_frames=1, min_area_ratio=0.02)
        for _ in range(3):
            self.assertFalse(detector.update(with_block(5))[0])
        fired, ratio = detector.update(with_block(40))
        self.assertTrue(fired)
        self.assertGreater(ratio, 0.02)


if __name__ == "__main__":
    unittest.main()
