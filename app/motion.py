from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from app.config import MotionConfig


class MotionGate:
    """Compara tres frames y dispara solo si un bloque compacto cambió.

    El viento y la compresión mueven puntos sueltos. Un animal, una persona o
    un vehículo dejan una mancha continua, y esa mancha es la que se mide.
    """

    def __init__(self, motion: MotionConfig):
        self._diff_threshold = motion.diff_threshold
        self._min_area_ratio = motion.min_area_ratio
        self._blur = motion.blur
        self._confirm_frames = motion.confirm_frames
        self._width = motion.analysis_width
        self._frames: deque[np.ndarray] = deque(maxlen=3)
        self._streak = 0

    def update(self, frame: np.ndarray) -> tuple[bool, float]:
        self._frames.append(_prepare(frame, self._width, self._blur))
        if len(self._frames) < 3:
            return False, 0.0
        older, previous, current = self._frames
        changed = cv2.absdiff(current, older)
        changed_recent = cv2.absdiff(current, previous)
        mask = np.where(
            (changed > self._diff_threshold) & (changed_recent > self._diff_threshold),
            np.uint8(255),
            np.uint8(0),
        )
        ratio = _largest_blob_ratio(mask)
        if ratio >= self._min_area_ratio:
            self._streak += 1
        else:
            self._streak = 0
        fired = self._streak >= self._confirm_frames
        if fired:
            self._streak = 0
        return fired, ratio


def _prepare(frame: np.ndarray, width: int, blur: int) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, current_width = gray.shape[:2]
    if current_width != width:
        target_height = max(1, int(round(height * width / current_width)))
        gray = cv2.resize(gray, (width, target_height), interpolation=cv2.INTER_AREA)
    if blur >= 3:
        gray = cv2.GaussianBlur(gray, (blur, blur), 0)
    return gray


def _largest_blob_ratio(mask: np.ndarray) -> float:
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return 0.0
    largest = int(stats[1:, cv2.CC_STAT_AREA].max())
    return largest / mask.size
