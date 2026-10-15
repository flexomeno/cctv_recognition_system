from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("cctv")
_JPEG_START = b"\xff\xd8"
_JPEG_END = b"\xff\xd9"


class CameraStream:
    """Lee un RTSP y se queda solo con el frame más reciente."""

    def __init__(self, camera_id: str, url: str, on_frame=None):
        self.camera_id = camera_id
        self.url = url
        self._on_frame = on_frame
        self._frame: np.ndarray | None = None
        self._ts = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._announced_offline = False
        self._seen_online = False
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(
            target=self._run, name=f"cap-{camera_id}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self._thread.join(timeout=3)
        if proc is not None and proc.poll() is None:
            proc.kill()

    def latest(self) -> tuple[float, np.ndarray] | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._ts, self._frame.copy()

    def frame_age(self) -> float | None:
        with self._lock:
            if self._frame is None:
                return None
            return time.monotonic() - self._ts

    def connected(self) -> bool:
        age = self.frame_age()
        return age is not None and age < 5

    def _run(self) -> None:
        # ffmpeg propio decodifica H.265 del DVR. OpenCV del contenedor a veces no trae ese códec.
        use_ffmpeg = _ffmpeg_exe() is not None
        if use_ffmpeg:
            log.info("%s: captura RTSP con ffmpeg", self.camera_id)
        elif not _opencv_has_ffmpeg():
            log.error("%s: no hay ffmpeg ni OpenCV con RTSP", self.camera_id)
        while not self._stop.is_set():
            detail = self._read_ffmpeg() if use_ffmpeg else self._read_opencv()
            if self._stop.is_set():
                return
            self._mark_offline(detail)
            if self._stop.wait(2):
                return

    def _read_opencv(self) -> str:
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
        if not cap.isOpened():
            cap.release()
            return "no se pudo abrir el RTSP"
        failures = 0
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None or frame.size == 0:
                    failures += 1
                    if failures >= 15:
                        return "se cortó la lectura"
                    continue
                failures = 0
                self._publish(frame)
        finally:
            cap.release()
        return ""

    def _read_ffmpeg(self) -> str:
        ffmpeg = _ffmpeg_exe()
        if not ffmpeg:
            return "no está instalado ffmpeg"
        proc = subprocess.Popen(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-i",
                self.url,
                "-an",
                "-vf",
                "fps=8",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                "5",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        self._proc = proc
        if self._stop.is_set():
            proc.terminate()
            return ""
        errors: list[str] = []
        threading.Thread(target=_drain_stderr, args=(proc, errors), daemon=True).start()
        pending = b""
        try:
            assert proc.stdout is not None
            while not self._stop.is_set():
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                pending, payloads = extract_jpegs(pending, chunk)
                for payload in payloads:
                    frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        self._publish(frame)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self._proc = None
        if errors:
            return errors[-1]
        return "ffmpeg cerró el stream"

    def _publish(self, frame: np.ndarray) -> None:
        ts = time.monotonic()
        stored = frame.copy()
        with self._lock:
            self._frame = stored
            self._ts = ts
        if not self._seen_online or self._announced_offline:
            log.info("%s conectada", self.camera_id)
        self._seen_online = True
        self._announced_offline = False
        if self._on_frame is not None:
            try:
                self._on_frame(ts, stored)
            except Exception:
                log.exception("No se pudo conservar un frame de %s", self.camera_id)

    def _mark_offline(self, detail: str = "") -> None:
        if not self._announced_offline:
            if detail:
                log.warning("%s sin imagen (%s), reintentando", self.camera_id, detail)
            else:
                log.warning("%s sin imagen, reintentando", self.camera_id)
            self._announced_offline = True


def _opencv_has_ffmpeg() -> bool:
    for line in cv2.getBuildInformation().splitlines():
        if line.strip().startswith("FFMPEG:"):
            return "YES" in line
    return False


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
    except ImportError:
        return shutil.which("ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_jpegs(pending: bytes, chunk: bytes) -> tuple[bytes, list[bytes]]:
    data = pending + chunk
    frames: list[bytes] = []
    while True:
        start = data.find(_JPEG_START)
        if start < 0:
            return data[-1:], frames
        end = data.find(_JPEG_END, start + 2)
        if end < 0:
            if len(data) - start > 8_000_000:
                return b"", frames
            return data[start:], frames
        frames.append(data[start : end + 2])
        data = data[end + 2 :]


def _drain_stderr(proc: subprocess.Popen, errors: list[str]) -> None:
    if proc.stderr is None:
        return
    for line in proc.stderr:
        text = line.decode("utf-8", "replace").strip()
        if text:
            errors.append(text)
            del errors[:-8]
