import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.config import ClipConfig
from app.recorder import CameraRecorder, ClipWriter


def frame(step):
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    image[:, :] = (step * 20 % 255, 40, 80)
    return image


def read_count(path):
    cap = cv2.VideoCapture(str(path))
    count = 0
    try:
        while True:
            ok, _image = cap.read()
            if not ok:
                return count
            count += 1
    finally:
        cap.release()


class RecorderTest(unittest.TestCase):
    def test_clip_covers_preroll_and_shares_the_recording(self):
        writer = ClipWriter()
        try:
            with tempfile.TemporaryDirectory() as folder:
                recorder = CameraRecorder(
                    "cam01",
                    ClipConfig(duration_s=4, preroll_s=1, buffer_fps=4),
                    Path(folder),
                    writer,
                )
                path = None
                for step in range(0, 25):
                    ts = step * 0.25
                    recorder.observe(ts, frame(step))
                    if path is None and ts >= 1:
                        path = recorder.start(ts)
                        self.assertEqual(recorder.start(ts), path)
                writer.wait()
                self.assertIsNotNone(path)
                self.assertEqual(read_count(path), 17)
                second = recorder.start(6)
                self.assertNotEqual(second, path)
                recorder.finish_now(6)
                writer.wait()
                self.assertGreaterEqual(read_count(second), 2)
        finally:
            writer.close()

    def test_saved_clip_keeps_the_detected_classes(self):
        saved = []
        writer = ClipWriter(on_saved=lambda clip: saved.append(list(clip.labels)))
        try:
            with tempfile.TemporaryDirectory() as folder:
                recorder = CameraRecorder(
                    "cam01",
                    ClipConfig(duration_s=4, preroll_s=1, buffer_fps=4),
                    Path(folder),
                    writer,
                )
                recorder.observe(0, frame(0))
                recorder.observe(0.25, frame(1))
                recorder.start(0.25)
                recorder.note("persona")
                recorder.note("auto")
                recorder.finish_now(0.25)
                writer.wait()
        finally:
            writer.close()
        self.assertEqual(saved, [["persona", "auto"]])


if __name__ == "__main__":
    unittest.main()
