from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from app.config import MotionConfig


class MotionGate:
    """Compara el frame actual con uno anterior y dispara si un bloque cambió.

    A varios frames por segundo, un animal que camina casi no difiere del
    frame inmediato. Se compara con un frame más viejo para que ese desplazamiento
    se vea. El viento sigue siendo puntos sueltos; cuenta el bloque más grande.
    """

    def __init__(self, motion: MotionConfig):
        self._diff_threshold = motion.diff_threshold
        self._min_area_ratio = motion.min_area_ratio
        self._blur = motion.blur
        self._confirm_frames = motion.confirm_frames
        self._width = motion.analysis_width
        self._frames: deque[np.ndarray] = deque(maxlen=motion.frame_gap + 1)
        self._streak = 0

    def update(self, frame: np.ndarray) -> tuple[bool, float]:
        self._frames.append(_prepare(frame, self._width, self._blur))
        if len(self._frames) <= self._frames.maxlen - 1:
            return False, 0.0
        changed = cv2.absdiff(self._frames[-1], self._frames[0])
        mask = np.where(changed > self._diff_threshold, np.uint8(255), np.uint8(0))
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
