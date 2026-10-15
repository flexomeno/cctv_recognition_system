from __future__ import annotations

from dataclasses import dataclass, field

from app.config import TrackingConfig


@dataclass
class Track:
    track_id: int
    class_name: str
    bbox: tuple[float, float, float, float]
    hits: int
    first_seen: float
    last_seen: float
    confidence: float
    confirmed: bool = False
    clip_path: str | None = None


@dataclass
class TrackEvent:
    kind: str
    track: Track = field()


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


class CameraTracker:
    """Sigue objetos dentro de una cámara. Cada visita confirmada cuenta una vez."""

    def __init__(self, tracking: TrackingConfig):
        self._iou_threshold = tracking.iou_threshold
        self._max_lost_s = tracking.max_lost_s
        self._min_hits = tracking.min_hits
        self._tracks: list[Track] = []
        self._next_id = 1

    def has_open_tracks(self) -> bool:
        return bool(self._tracks)

    def update(self, detections: list, now: float) -> list[TrackEvent]:
        events: list[TrackEvent] = []
        pairs: list[tuple[float, int, int]] = []
        for ti, track in enumerate(self._tracks):
            for di, det in enumerate(detections):
                if det.class_name != track.class_name:
                    continue
                score = iou(track.bbox, det.bbox)
                if score >= self._iou_threshold:
                    pairs.append((score, ti, di))
        pairs.sort(reverse=True)

        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()
        for _score, ti, di in pairs:
            if ti in matched_tracks or di in matched_dets:
                continue
            matched_tracks.add(ti)
            matched_dets.add(di)
            det = detections[di]
            track = self._tracks[ti]
            track.bbox = det.bbox
            track.hits += 1
            track.last_seen = now
            track.confidence = max(track.confidence, float(det.confidence))
            if not track.confirmed and track.hits >= self._min_hits:
                track.confirmed = True
                events.append(TrackEvent("confirmed", track))

        still_open: list[Track] = []
        for ti, track in enumerate(self._tracks):
            if ti in matched_tracks:
                still_open.append(track)
                continue
            if now - track.last_seen > self._max_lost_s:
                if track.confirmed:
                    events.append(TrackEvent("closed", track))
                continue
            still_open.append(track)

        for di, det in enumerate(detections):
            if di in matched_dets:
                continue
            track = Track(
                track_id=self._next_id,
                class_name=det.class_name,
                bbox=det.bbox,
                hits=1,
                first_seen=now,
                last_seen=now,
                confidence=float(det.confidence),
            )
            self._next_id += 1
            if track.hits >= self._min_hits:
                track.confirmed = True
                events.append(TrackEvent("confirmed", track))
            still_open.append(track)

        self._tracks = still_open
        return events
