import tempfile
import textwrap
import unittest
from pathlib import Path

from app.config import load_config


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


def write_config(folder, body):
    path = Path(folder) / "config.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


class ConfigTest(unittest.TestCase):
    def test_example_loads_one_camera(self):
        config = load_config(EXAMPLE)
        self.assertEqual(len(config.cameras), 1)
        self.assertEqual(config.cameras[0].id, "dvr_ch1")
        self.assertEqual(config.motion.diff_threshold, 30)
        self.assertAlmostEqual(config.motion.min_area_ratio, 0.01)
        self.assertEqual(config.motion.confirm_frames, 3)
        self.assertEqual(config.clip.duration_s, 10)
        self.assertEqual(config.clip.preroll_s, 3)
        self.assertTrue(str(config.database).endswith("inventory.db"))
        self.assertIsNone(config.telegram)

    def test_rejects_more_than_ten_cameras(self):
        cameras = "\n".join(
            f"  - id: cam{index:02d}\n    url: rtsp://127.0.0.1/{index}"
            for index in range(1, 12)
        )
        with tempfile.TemporaryDirectory() as folder:
            path = write_config(
                folder,
                f"""
                model_url: https://example.com/yolov8n.onnx
                cameras:
                {cameras}
                """,
            )
            with self.assertRaises(ValueError):
                load_config(path)

    def test_rejects_non_rtsp_and_bad_preroll(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_config(
                folder,
                """
                model_url: https://example.com/yolov8n.onnx
                cameras:
                  - id: puerta
                    url: http://127.0.0.1/live
                """,
            )
            with self.assertRaises(ValueError):
                load_config(path)
            path.write_text(
                textwrap.dedent(
                    """
                    model_url: https://example.com/yolov8n.onnx
                    clip:
                      duration_s: 10
                      preroll_s: 10
                    cameras:
                      - id: puerta
                        url: rtsp://127.0.0.1/stream2
                    """
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
