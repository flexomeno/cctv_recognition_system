from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from app.capture import CameraStream
from app.config import AppConfig, load_config
from app.inventory import Inventory, format_counts, start_of_local_day
from app.motion import MotionGate
from app.recorder import CameraRecorder, ClipWriter, prepare_codec
from app.telegram import TelegramNotifier

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
    gates: dict[str, MotionGate] = {}
    clips_dir = config.output_dir / "clips"
    for camera in config.cameras:
        recorder = CameraRecorder(camera.id, config.clip, clips_dir, writer)
        recorders[camera.id] = recorder
        stream = CameraStream(camera.id, camera.url, on_frame=recorder.observe)
        streams[camera.id] = stream
        gates[camera.id] = MotionGate(config.motion)
        stream.start()

    log.info(
        "Monitoreando %s cámara(s) por movimiento. Umbral %s, bloque mínimo %.1f%%, %s frames.",
        len(config.cameras),
        config.motion.diff_threshold,
        config.motion.min_area_ratio * 100,
        config.motion.confirm_frames,
    )
    last_frame_ts = {camera.id: -1.0 for camera in config.cameras}
    next_clip = {camera.id: 0.0 for camera in config.cameras}
    visit_ids = {camera.id: 0 for camera in config.cameras}
    last_summary = 0.0
    last_check_ms: float | None = None
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
            checked = False
            for camera in config.cameras:
                recorders[camera.id].flush_if_stale(now_mono)
                latest = streams[camera.id].latest()
                if latest is None or latest[0] <= last_frame_ts[camera.id]:
                    continue
                frame_ts, frame = latest
                last_frame_ts[camera.id] = frame_ts
                try:
                    started = time.perf_counter()
                    fired, ratio = gates[camera.id].update(frame)
                    last_check_ms = (time.perf_counter() - started) * 1000
                except Exception:
                    log.exception("El análisis de movimiento falló en %s", camera.id)
                    continue
                checked = True
                recorder = recorders[camera.id]
                if not fired or recorder.active_path() is not None or now_mono < next_clip[camera.id]:
                    continue
                visit_ids[camera.id] += 1
                clip_path = recorder.start(frame_ts)
                recorder.note("movimiento")
                inventory.add_visit(
                    camera_id=camera.id,
                    class_name="movimiento",
                    track_id=visit_ids[camera.id],
                    started_at=now_wall,
                    ended_at=now_wall + config.clip.duration_s,
                    confidence=ratio,
                    clip_path=clip_path,
                )
                next_clip[camera.id] = now_mono + config.motion.cooldown_s
                log.info("%s movimiento, bloque %.1f%% del cuadro", camera.id, ratio * 100)

            if now_wall - last_summary >= config.summary_interval_s:
                _log_summary(streams, inventory, last_check_ms)
                last_summary = now_wall
            if not checked:
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


def _log_summary(
    streams: dict[str, CameraStream],
    inventory: Inventory,
    last_check_ms: float | None,
) -> None:
    active = sum(1 for stream in streams.values() if stream.connected())
    lines = [f"Cámaras con imagen: {active}/{len(streams)}"]
    if last_check_ms is not None:
        lines.append(f"Último análisis: {last_check_ms:.0f} ms")
    lines.append("Videos de hoy:")
    lines.append(format_counts(inventory.counts(since=start_of_local_day())))
    log.info("\n%s", "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor RTSP por movimiento")
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
