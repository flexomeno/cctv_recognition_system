#!/usr/bin/env python3
"""Reenvía a Telegram los mp4 que termina el agente libre de Kerberos.

El agente no habla con Telegram. Este proceso mira las carpetas montadas en
/recordings/<cámara> y manda cada archivo cuando deja de crecer.
"""
import json
import os
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
ROOT = Path("/recordings")
STATE = Path("/sent/sent.json")
MAX_BYTES = 45 * 1024 * 1024


def load():
    if STATE.exists():
        try:
            return set(json.loads(STATE.read_text()))
        except json.JSONDecodeError:
            return set()
    return set()


def save(sent):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(sent)))
    tmp.replace(STATE)


def camera_of(path: Path) -> str:
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return "kerberos"
    return relative.parts[0] if relative.parts else "kerberos"


def stable(path: Path) -> bool:
    try:
        size = path.stat().st_size
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False
    if size < 20000 or time.time() - mtime < 3:
        return False
    time.sleep(2)
    try:
        return path.stat().st_size == size
    except FileNotFoundError:
        return False


def send(path: Path) -> None:
    data = path.read_bytes()
    boundary = uuid.uuid4().hex
    caption = camera_of(path) + "\nkerberos\n" + path.name
    chunks = []

    def field(name, value):
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )

    field("chat_id", CHAT)
    field("caption", caption)
    field("supports_streaming", "true")
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="video"; filename="{path.name}"\r\n'
            f"Content-Type: video/mp4\r\n\r\n"
        ).encode()
        + data
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    req = Request(
        "https://api.telegram.org/bot" + TOKEN + "/sendVideo",
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST",
    )
    with urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode())
    if not payload.get("ok"):
        raise RuntimeError("telegram rechazo el video")


def main():
    sent = load()
    print("sidecar listo", flush=True)
    while True:
        files = (
            [
                path
                for path in ROOT.rglob("*")
                if path.is_file() and path.suffix.lower() == ".mp4"
            ]
            if ROOT.exists()
            else []
        )
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            try:
                key = str(path.relative_to(ROOT)) + ":" + str(path.stat().st_size)
            except (FileNotFoundError, ValueError):
                continue
            if key in sent or not stable(path):
                continue
            if path.stat().st_size > MAX_BYTES:
                print("omitido grande " + path.name, flush=True)
                sent.add(key)
                save(sent)
                continue
            try:
                send(path)
            except (HTTPError, URLError, TimeoutError, RuntimeError, OSError) as exc:
                print("fallo " + path.name + " " + type(exc).__name__, flush=True)
                continue
            sent.add(key)
            save(sent)
            print("enviado " + camera_of(path) + " " + path.name, flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
