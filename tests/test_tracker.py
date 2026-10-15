import unittest

from app.config import TrackingConfig
from app.tracker import CameraTracker


class Det:
    def __init__(self, class_name, confidence, bbox):
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox


def tracker():
    return CameraTracker(TrackingConfig(iou_threshold=0.3, max_lost_s=2, min_hits=2))


class TrackerTest(unittest.TestCase):
    def test_confirms_on_second_hit_and_counts_once(self):
        follow = tracker()
        box = (0, 0, 10, 10)
        det = Det("auto", 0.8, box)
        self.assertEqual(follow.update([det], 0), [])
        events = follow.update([Det("auto", 0.9, box)], 0.5)
        self.assertEqual([event.kind for event in events], ["confirmed"])
        self.assertEqual(events[0].track.track_id, 1)
        self.assertAlmostEqual(events[0].track.confidence, 0.9)
        self.assertEqual(follow.update([det], 1.0), [])

    def test_same_visit_if_it_returns_within_two_seconds(self):
        follow = tracker()
        box = (0, 0, 10, 10)
        follow.update([Det("persona", 0.7, box)], 0)
        follow.update([Det("persona", 0.7, box)], 0.5)
        follow.update([], 1.0)
        events = follow.update([Det("persona", 0.7, box)], 2.4)
        self.assertEqual(events, [])
        self.assertTrue(follow.has_open_tracks())

    def test_new_visit_after_the_gap(self):
        follow = tracker()
        box = (10, 10, 30, 40)
        follow.update([Det("moto", 0.6, box)], 0)
        follow.update([Det("moto", 0.6, box)], 0.4)
        closed = follow.update([], 3.0)
        self.assertEqual([event.kind for event in closed], ["closed"])
        self.assertEqual(closed[0].track.track_id, 1)
        follow.update([Det("moto", 0.6, box)], 3.2)
        confirmed = follow.update([Det("moto", 0.6, box)], 3.6)
        self.assertEqual(confirmed[0].track.track_id, 2)

    def test_different_classes_are_not_the_same_track(self):
        follow = tracker()
        box = (0, 0, 20, 20)
        follow.update(
            [Det("persona", 0.9, box), Det("auto", 0.9, box)],
            0,
        )
        events = follow.update(
            [Det("persona", 0.9, box), Det("auto", 0.9, box)],
            0.4,
        )
        self.assertEqual(sorted(event.track.class_name for event in events), ["auto", "persona"])
        self.assertEqual(len({event.track.track_id for event in events}), 2)

    def test_distant_box_does_not_match(self):
        follow = tracker()
        follow.update([Det("bus", 0.8, (0, 0, 10, 10))], 0)
        follow.update([Det("bus", 0.8, (0, 0, 10, 10))], 0.2)
        events = follow.update([Det("bus", 0.8, (100, 100, 120, 120))], 0.4)
        self.assertEqual(events, [])
        closed = follow.update([], 3)
        self.assertEqual(closed[0].track.track_id, 1)


if __name__ == "__main__":
    unittest.main()
