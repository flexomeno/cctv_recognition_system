import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.config import ClipConfig, TrackingConfig
from app.inventory import Inventory
from app.main import handle_events
from app.recorder import CameraRecorder, ClipWriter
from app.tracker import CameraTracker


class Det:
    def __init__(self, class_name, confidence, bbox):
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox


class PipelineTest(unittest.TestCase):
    def test_two_visits_share_one_clip_and_are_stored_once(self):
        writer = ClipWriter()
        try:
            with tempfile.TemporaryDirectory() as folder:
                folder = Path(folder)
                recorder = CameraRecorder(
                    "cam01",
                    ClipConfig(duration_s=10, preroll_s=3, buffer_fps=5),
                    folder / "clips",
                    writer,
                )
                image = np.zeros((48, 64, 3), dtype=np.uint8)
                recorder.observe(0, image)
                recorder.observe(0.2, image)
                inventory = Inventory(folder / "inventory.db")
                follow = CameraTracker(
                    TrackingConfig(iou_threshold=0.3, max_lost_s=2, min_hits=2)
                )
                person = Det("persona", 0.8, (0, 0, 20, 40))
                car = Det("auto", 0.7, (30, 10, 60, 40))
                handle_events(follow.update([person, car], 10), recorder, inventory, "cam01", 0.2)
                events = follow.update([person, car], 10.4)
                handle_events(events, recorder, inventory, "cam01", 0.4)
                self.assertEqual({event.track.clip_path for event in events}, {events[0].track.clip_path})
                self.assertTrue(events[0].track.clip_path.endswith((".mp4", ".avi")))
                closed = follow.update([], 13)
                handle_events(closed, recorder, inventory, "cam01", 3)
                rows = sqlite3.connect(folder / "inventory.db").execute(
                    "SELECT class_name, clip_path FROM visits ORDER BY class_name"
                ).fetchall()
                self.assertEqual([row[0] for row in rows], ["auto", "persona"])
                self.assertEqual(rows[0][1], rows[1][1])
                self.assertEqual(inventory.counts(), [("cam01", "auto", 1), ("cam01", "persona", 1)])
                recorder.finish_now(20)
                writer.wait()
                inventory.close()
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()
