import unittest
from datetime import datetime

from app.telegram import caption_for


class CaptionTest(unittest.TestCase):
    def test_lists_each_detection(self):
        text = caption_for("cam01", ["persona", "persona", "auto"], datetime(2026, 10, 14, 20, 47, 23))
        self.assertEqual(text, "cam01\n2 persona, auto\n14/10/2026 20:47:23")


if __name__ == "__main__":
    unittest.main()