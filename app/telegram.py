from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.capture import _ffmpeg_exe

log = logging.getLogger("cctv")


def caption_for(camera_id: str, labels: list[str], when: datetime) -> str:
    counts = Counter(labels)
    if not counts:
        detected = "detección"
    else:
        detected = ", ".join(
            f"{count} {name}" if count > 1 else name for name, count in counts.items()
        )
    return f"{camera_id}\n{detected}\n{when.strftime('%d/%m/%Y %H:%M:%S')}"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._token = bot_token
        self._chat_id = chat_id
        self._queue: queue.Queue[object | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="telegram", daemon=False)
        self._thread.start()

    def submit_clip(self, clip) -> None:
        self._queue.put(clip)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()

    def _loop(self) -> None:
        while True:
            clip = self._queue.get()
            try:
                if clip is None:
                    return
                send_clip(self._token, self._chat_id, clip.path, clip.camera_id, clip.labels, clip.detected_at)
            except Exception:
                log.exception("No se pudo enviar %s a Telegram", getattr(clip, "path", ""))
            finally:
                self._queue.task_done()


def send_clip(
    bot_token: str,
    chat_id: str,
    path: Path,
    camera_id: str,
    labels: list[str],
    when: datetime,
) -> None:
    video = _h264_copy(path)
    temporary = video != path
    caption = caption_for(camera_id, labels, when)
    try:
        _post_video(bot_token, chat_id, video, caption)
    finally:
        if temporary:
            video.unlink(missing_ok=True)
    log.info("Video enviado a Telegram: %s (%s)", camera_id, caption.split("\n")[1])


def _h264_copy(source: Path) -> Path:
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return source
    target = source.with_name(f"{source.stem}_tg.mp4")
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-movflags",
            "+faststart",
            str(target),
        ],
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        if detail:
            log.warning("No se pudo convertir el clip a MP4: %s", detail.splitlines()[-1])
        target.unlink(missing_ok=True)
        return source
    return target


def _post_video(bot_token: str, chat_id: str, path: Path, caption: str) -> None:
    method = "sendVideo" if path.suffix.lower() == ".mp4" else "sendDocument"
    field = "video" if method == "sendVideo" else "document"
    mime = "video/mp4" if field == "video" else "application/octet-stream"
    payload = path.read_bytes()
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            _field(boundary, "chat_id", chat_id),
            _field(boundary, "caption", caption),
            _field(boundary, "supports_streaming", "true"),
            _file(boundary, field, path.name, mime, payload),
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        _raise_api_error(raw)
        raise RuntimeError(f"Telegram respondió HTTP {exc.code}") from None
    _raise_api_error(raw)


def _raise_api_error(raw: bytes) -> None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return
    if not data.get("ok", True):
        raise RuntimeError(data.get("description") or "Telegram rechazó el video")


def _field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode()


def _file(boundary: str, name: str, filename: str, mime: str, payload: bytes) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    return header + payload + b"\r\n"
