import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.inventory import Inventory, epoch_to_iso, format_counts, start_of_local_day


class InventoryTest(unittest.TestCase):
    def test_counts_by_camera_and_class(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Inventory(Path(folder) / "inventory.db")
            morning = datetime(2026, 10, 14, 9, 0).timestamp()
            later = datetime(2026, 10, 14, 18, 0).timestamp()
            db.add_visit(
                camera_id="cam01",
                class_name="persona",
                track_id=1,
                started_at=morning,
                ended_at=morning + 4,
                confidence=0.8123,
                clip_path="/tmp/a.mp4",
            )
            db.add_visit(
                camera_id="cam01",
                class_name="auto",
                track_id=2,
                started_at=later,
                ended_at=later + 2,
                confidence=0.5,
                clip_path="/tmp/a.mp4",
            )
            db.add_visit(
                camera_id="cam01",
                class_name="persona",
                track_id=3,
                started_at=later,
                ended_at=later + 1,
                confidence=0.6,
                clip_path=None,
            )
            self.assertEqual(
                db.counts(),
                [("cam01", "auto", 1), ("cam01", "persona", 2)],
            )
            self.assertEqual(
                db.counts(since=epoch_to_iso(later)),
                [("cam01", "auto", 1), ("cam01", "persona", 1)],
            )
            text = format_counts(db.counts())
            self.assertIn("persona", text)
            self.assertIn("Total: 3", text)
            self.assertIn("Todavía no hay visitas.", format_counts([]))
            day = start_of_local_day(datetime(2026, 10, 14, 15, 30))
            self.assertEqual(day, "2026-10-14T00:00:00")
            db.close()


if __name__ == "__main__":
    unittest.main()
