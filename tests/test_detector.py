import unittest

import numpy as np

from app.detector import detections_from_output, letterbox


def _raw(anchors):
    """anchors: list of (cx, cy, w, h, class_id, score) in canvas pixels."""
    pred = np.zeros((1, 84, len(anchors)), dtype=np.float32)
    for index, (cx, cy, width, height, class_id, score) in enumerate(anchors):
        pred[0, 0, index] = cx
        pred[0, 1, index] = cy
        pred[0, 2, index] = width
        pred[0, 3, index] = height
        pred[0, 4 + class_id, index] = score
    return pred


class DetectorDecodeTest(unittest.TestCase):
    def test_letterbox_is_exact(self):
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        canvas, ratio, pad = letterbox(image, (320, 320))
        self.assertEqual(canvas.shape[:2], (320, 320))
        self.assertAlmostEqual(ratio, 0.5)
        self.assertEqual(pad, (0.0, 70.0))

    def test_reads_person_and_maps_box_back(self):
        found = detections_from_output(
            _raw([(160, 160, 40, 80, 0, 0.91)]),
            ratio=0.5,
            pad=(0.0, 70.0),
            orig_shape=(360, 640),
            input_size=(320, 320),
            confidence=0.4,
            iou_threshold=0.45,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].class_name, "persona")
        self.assertAlmostEqual(found[0].confidence, 0.91)
        self.assertEqual(tuple(round(value) for value in found[0].bbox), (280, 100, 360, 260))

    def test_keeps_person_and_car_and_suppresses_duplicate(self):
        found = detections_from_output(
            _raw(
                [
                    (160, 160, 40, 80, 0, 0.95),
                    (162, 162, 40, 80, 0, 0.55),
                    (40, 40, 30, 20, 2, 0.80),
                    (10, 10, 8, 8, 16, 0.99),
                ]
            ),
            ratio=1,
            pad=(0.0, 0.0),
            orig_shape=(320, 320),
            input_size=(320, 320),
            confidence=0.4,
            iou_threshold=0.45,
        )
        classes = sorted(item.class_name for item in found)
        self.assertEqual(classes, ["auto", "persona"])

    def test_drops_low_score_and_accepts_normalized_boxes(self):
        pred = np.zeros((8, 84), dtype=np.float32)
        pred[0, 0:4] = (0.5, 0.5, 0.25, 0.25)
        pred[0, 4] = 0.2
        pred[1, 0:4] = (0.5, 0.5, 0.25, 0.25)
        pred[1, 4] = 0.7
        found = detections_from_output(
            pred,
            ratio=1,
            pad=(0.0, 0.0),
            orig_shape=(320, 320),
            input_size=(320, 320),
            confidence=0.4,
            iou_threshold=0.45,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(tuple(round(value) for value in found[0].bbox), (120, 120, 200, 200))


if __name__ == "__main__":
    unittest.main()
