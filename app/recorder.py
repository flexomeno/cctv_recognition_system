from __future__ import annotations

import logging
import queue
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from app.config import ClipConfig

log = logging.getLogger("cctv")
_MAX_CLIP_WIDTH = 960
_JPEG_QUALITY = 70
_CODEC: str | None = None
_CODEC_LOCK = threading.Lock()


def video_suffix() -> str:
    return ".avi" if prepare_codec() == "MJPG" else ".mp4"


def prepare_codec() -> str:
    """Elige el códec más liviano que esta instalación de OpenCV puede leer de vuelta."""
    global _CODEC
    if _CODEC is not None:
        return _CODEC
    with _CODEC_LOCK:
        if _CODEC is not None:
            return _CODEC
        return _probe_codec()


def _probe_codec() -> str:
    global _CODEC
    folder = Path(tempfile.mkdtemp())
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    previous = cv2.utils.logging.getLogLevel()
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    try:
        for name in ("mp4v", "MJPG", "avc1", "H264"):
            ext = ".avi" if name == "MJPG" else ".mp4"
            path = folder / f"probe{ext}"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*name), 5, (32, 32))
            if not writer.isOpened():
                writer.release()
                continue
            writer.write(image)
            writer.write(image)
            writer.release()
            capture = cv2.VideoCapture(str(path))
            readable = capture.read()[0]
            capture.release()
            if readable:
                _CODEC = name
                log.info("Clips en %s (%s)", ext, name)
                return name
    finally:
        cv2.utils.logging.setLogLevel(previous)
    _CODEC = ""
    log.error("OpenCV no tiene un códec para guardar los clips.")
    return _CODEC


@dataclass
class _ActiveClip:
    path: Path
    end_ts: float
    camera_id: str = ""
    detected_at: datetime = field(default_factory=datetime.now)
    labels: list[str] = field(default_factory=list)
    frames: list[tuple[float, bytes]] = field(default_factory=list)


class _Ring:
    def __init__(self, preroll_s: float):
        self.preroll_s = preroll_s
        self.frames: deque[tuple[float, bytes]] = deque()

    def push(self, ts: float, jpeg: bytes) -> None:
        self.frames.append((ts, jpeg))
        cutoff = ts - self.preroll_s
        while self.frames and self.frames[0][0] < cutoff:
            self.frames.popleft()

    def snapshot(self) -> list[tuple[float, bytes]]:
        return list(self.frames)


class ClipWriter:
    """Codifica los clips de a uno, para no saturar los dos núcleos."""

    def __init__(self, on_saved=None):
        self._on_saved = on_saved
        self._queue: queue.Queue[_ActiveClip | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="clip-writer", daemon=False)
        self._thread.start()

    def submit(self, clip: _ActiveClip) -> None:
        self._queue.put(clip)

    def wait(self) -> None:
        self._queue.join()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()

    def _loop(self) -> None:
        while True:
            clip = self._queue.get()
            try:
                if clip is None:
                    return
                if _write_mp4(clip) and self._on_saved is not None:
                    self._on_saved(clip)
            except Exception:
                log.exception("No se pudo escribir %s", clip.path if clip else "")
            finally:
                self._queue.task_done()


class CameraRecorder:
    def __init__(self, camera_id: str, clip: ClipConfig, clips_dir: Path, writer: ClipWriter):
        self.camera_id = camera_id
        self._duration_s = clip.duration_s
        self._interval = 1.0 / clip.buffer_fps
        self._clips_dir = clips_dir
        self._writer = writer
        self._buffer = _Ring(clip.preroll_s)
        self._active: _ActiveClip | None = None
        self._last_ts: float | None = None
        self._lock = threading.Lock()

    def observe(self, ts: float, frame: np.ndarray) -> None:
        with self._lock:
            if self._last_ts is not None and ts + 1e-6 < self._last_ts:
                return
            due = self._last_ts is None or ts - self._last_ts >= self._interval - 1e-6
            if due:
                jpeg = _encode_jpeg(frame)
                if jpeg is not None:
                    self._last_ts = ts
                    self._buffer.push(ts, jpeg)
                    if self._active is not None and ts <= self._active.end_ts:
                        self._active.frames.append((ts, jpeg))
            self._finish_if_due(ts)

    def start(self, now: float) -> str:
        with self._lock:
            if self._active is not None:
                return str(self._active.path.resolve())
            preroll = self._buffer.snapshot()
            clip_start = preroll[0][0] if preroll else now
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = (self._clips_dir / self.camera_id / f"{stamp}{video_suffix()}").resolve()
            self._active = _ActiveClip(
                path=path,
                end_ts=clip_start + self._duration_s,
                camera_id=self.camera_id,
                frames=preroll,
            )
            self._finish_if_due(now)
            return str(path)

    def note(self, class_name: str) -> None:
        with self._lock:
            if self._active is not None:
                self._active.labels.append(class_name)

    def active_path(self) -> str | None:
        with self._lock:
            if self._active is None:
                return None
            return str(self._active.path.resolve())

    def flush_if_stale(self, now: float) -> None:
        with self._lock:
            if self._active is not None and now >= self._active.end_ts + 1:
                self._submit_active()

    def finish_now(self, _now: float) -> None:
        with self._lock:
            if self._active is not None:
                self._submit_active()

    def _finish_if_due(self, ts: float) -> None:
        if self._active is not None and ts >= self._active.end_ts:
            self._submit_active()

    def _submit_active(self) -> None:
        clip = self._active
        self._active = None
        if clip is not None:
            self._writer.submit(clip)


def _encode_jpeg(frame: np.ndarray) -> bytes | None:
    height, width = frame.shape[:2]
    if width > _MAX_CLIP_WIDTH:
        scale = _MAX_CLIP_WIDTH / width
        frame = cv2.resize(
            frame,
            (_MAX_CLIP_WIDTH, max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(
        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY]
    )
    if not ok:
        return None
    return encoded.tobytes()


def _write_mp4(clip: _ActiveClip) -> bool:
    if len(clip.frames) < 2:
        log.warning("Clip vacío, no se escribió %s", clip.path)
        return False
    images = []
    for _ts, payload in clip.frames:
        image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            images.append(image)
    if len(images) < 2:
        log.warning("Clip vacío, no se escribió %s", clip.path)
        return False
    height, width = images[0].shape[:2]
    span = clip.frames[-1][0] - clip.frames[0][0]
    fps = (len(images) - 1) / span if span > 0.05 else 5.0
    fps = max(1.0, min(fps, 30.0))
    codec = prepare_codec()
    if not codec:
        return False
    clip.path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(clip.path), cv2.VideoWriter_fourcc(*codec), fps, (width, height)
    )
    if not writer.isOpened():
        log.error("OpenCV no pudo crear %s", clip.path)
        return False
    try:
        for image in images:
            if image.shape[1] != width or image.shape[0] != height:
                image = cv2.resize(image, (width, height))
            writer.write(image)
    finally:
        writer.release()
    log.info(
        "Clip guardado %s (%s frames, %.1f s) -> %s",
        clip.path.parent.name,
        len(images),
        (len(images) - 1) / fps,
        clip.path,
    )
    return clip.path.exists() and clip.path.stat().st_size > 0
