import unittest

from app.capture import extract_jpegs


class ExtractJpegTest(unittest.TestCase):
    def test_joins_a_frame_split_across_chunks(self):
        payload = b"\xff\xd8" + b"hola" + b"\xff\xd9"
        pending, frames = extract_jpegs(b"", payload[:4])
        self.assertEqual(frames, [])
        pending, frames = extract_jpegs(pending, payload[4:])
        self.assertEqual(frames, [payload])
        self.assertEqual(pending, b"")

    def test_reads_two_frames_and_keeps_the_tail(self):
        one = b"\xff\xd8A\xff\xd9"
        two = b"\xff\xd8B\xff\xd9"
        pending, frames = extract_jpegs(b"xx", one + two + b"\xff")
        self.assertEqual(frames, [one, two])
        self.assertEqual(pending, b"\xff")


if __name__ == "__main__":
    unittest.main()