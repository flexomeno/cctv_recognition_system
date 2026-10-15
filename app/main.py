from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from app.capture import CameraStream
from app.config import AppConfig, load_config
from app.detector import Detector, ensure_model
from app.inventory import Inventory, format_counts, start_of_local_day
from app.recorder import CameraRecorder, ClipWriter, prepare_codec
from app.telegram import TelegramNotifier
from app.tracker import CameraTracker

log = logging.getLogger("cctv")


def handle_events(events, recorder: CameraRecorder, inventory: Inventory, camera_id: str, now_mono: float) -> None:
    for event in events:
        track = event.track
        if event.kind == "confirmed":
            track.clip_path = recorder.start(now_mono)
            recorder.note(track.class_name)
            log.info(
                "%s visita confirmada: %s #%s (%.2f)",
                camera_id,
                track.class_name,
                track.track_id,
                track.confidence,
            )
        elif event.kind == "closed" and track.confirmed:
            inventory.add_visit(
                camera_id=camera_id,
                class_name=track.class_name,
                track_id=track.track_id,
                started_at=track.first_seen,
                ended_at=track.last_seen,
                confidence=track.confidence,
                clip_path=track.clip_path,
            )
            log.info(
                "%s visita cerrada: %s #%s",
                camera_id,
                track.class_name,
                track.track_id,
            )


def run(config: AppConfig) -> None:
    prepare_codec()
    ensure_model(config.model_path, config.model_url)
    detector = Detector(
        config.model_path,
        config.inference.imgsz,
        config.inference.confidence,
        config.inference.iou,
    )
    inventory = Inventory(config.database)
    notifier = None
    if config.telegram is not None:
        notifier = TelegramNotifier(config.telegram.bot_token, config.telegram.chat_id)
        log.info("Telegram activo. Cada clip se envía al chat %s.", config.telegram.chat_id)
    else:
        log.info("Telegram apagado. Agrega bot_token y chat_id en config.yaml.")
    writer = ClipWriter(on_saved=notifier.submit_clip if notifier is not None else None)
    recorders: dict[str, CameraRecorder] = {}
    streams: dict[str, CameraStream] = {}
    trackers: dict[str, CameraTracker] = {}
    clips_dir = config.output_dir / "clips"
    for camera in config.cameras:
        recorder = CameraRecorder(camera.id, config.clip, clips_dir, writer)
        recorders[camera.id] = recorder
        stream = CameraStream(camera.id, camera.url, on_frame=recorder.observe)
        streams[camera.id] = stream
        trackers[camera.id] = CameraTracker(config.tracking)
        stream.start()

    log.info(
        "Monitoreando %s cámara(s). Una inferencia a la vez, tope %.1f fps por cámara.",
        len(config.cameras),
        config.inference.fps_per_camera,
    )
    interval = 1.0 / config.inference.fps_per_camera
    next_infer = {camera.id: 0.0 for camera in config.cameras}
    last_frame_ts = {camera.id: -1.0 for camera in config.cameras}
    last_track_update = {camera.id: 0.0 for camera in config.cameras}
    cursor = 0
    last_summary = 0.0
    last_infer_ms: float | None = None
    total = len(config.cameras)
    stop = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        while not stop:
            now_mono = time.monotonic()
            now_wall = time.time()
            for camera in config.cameras:
                recorders[camera.id].flush_if_stale(now_mono)
                _expire_if_offline(
                    camera.id,
                    streams[camera.id],
                    trackers[camera.id],
                    recorders[camera.id],
                    inventory,
                    last_track_update,
                    now_mono,
                    now_wall,
                    config.tracking.max_lost_s,
                )

            ran = False
            for offset in range(total):
                camera = config.cameras[(cursor + offset) % total]
                if now_mono < next_infer[camera.id]:
                    continue
                latest = streams[camera.id].latest()
                if latest is None or latest[0] <= last_frame_ts[camera.id]:
                    continue
                frame_ts, frame = latest
                try:
                    started = time.perf_counter()
                    detections = detector.detect(frame)
                    last_infer_ms = (time.perf_counter() - started) * 1000
                    events = trackers[camera.id].update(detections, time.time())
                except Exception:
                    log.exception("Inferencia falló en %s", camera.id)
                    next_infer[camera.id] = time.monotonic() + interval
                    cursor = (cursor + offset + 1) % total
                    ran = True
                    break
                last_frame_ts[camera.id] = frame_ts
                last_track_update[camera.id] = time.time()
                next_infer[camera.id] = time.monotonic() + interval
                handle_events(
                    events,
                    recorders[camera.id],
                    inventory,
                    camera.id,
                    frame_ts,
                )
                cursor = (cursor + offset + 1) % total
                ran = True
                break

            if now_wall - last_summary >= config.summary_interval_s:
                _log_summary(streams, inventory, last_infer_ms)
                last_summary = now_wall
            if not ran:
                time.sleep(0.02)
    finally:
        for stream in streams.values():
            stream.stop()
        now = time.monotonic()
        for recorder in recorders.values():
            recorder.finish_now(now)
        writer.close()
        if notifier is not None:
            notifier.close()
        inventory.close()
        log.info("Monitor detenido")


def _expire_if_offline(
    camera_id: str,
    stream: CameraStream,
    tracker: CameraTracker,
    recorder: CameraRecorder,
    inventory: Inventory,
    last_track_update: dict[str, float],
    now_mono: float,
    now_wall: float,
    max_lost_s: float,
) -> None:
    if not tracker.has_open_tracks():
        return
    age = stream.frame_age()
    offline = age is None or age > max_lost_s
    if not offline or now_wall - last_track_update[camera_id] < max_lost_s:
        return
    events = tracker.update([], now_wall)
    last_track_update[camera_id] = now_wall
    handle_events(events, recorder, inventory, camera_id, now_mono)


def _log_summary(
    streams: dict[str, CameraStream],
    inventory: Inventory,
    last_infer_ms: float | None,
) -> None:
    active = sum(1 for stream in streams.values() if stream.connected())
    lines = [f"Cámaras con imagen: {active}/{len(streams)}"]
    if last_infer_ms is not None:
        lines.append(f"Última inferencia: {last_infer_ms:.0f} ms")
    lines.append("Visitas de hoy:")
    lines.append(format_counts(inventory.counts(since=start_of_local_day())))
    log.info("\n%s", "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor RTSP de personas y vehículos")
    parser.add_argument("--config", default="config.yaml", help="Ruta del YAML de cámaras")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    config_path = Path(args.config)
    if not config_path.exists():
        print(
            f"No existe {config_path}. Copia config.example.yaml a config.yaml y pon las URLs RTSP.",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(config_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        run(config)
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
