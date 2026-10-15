from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

MAX_CAMERAS = 10
_CAMERA_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass(frozen=True)
class CameraConfig:
    id: str
    url: str


@dataclass(frozen=True)
class InferenceConfig:
    imgsz: int
    confidence: float
    iou: float
    fps_per_camera: float


@dataclass(frozen=True)
class ClipConfig:
    duration_s: float
    preroll_s: float
    buffer_fps: float


@dataclass(frozen=True)
class TrackingConfig:
    iou_threshold: float
    max_lost_s: float
    min_hits: int


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class AppConfig:
    output_dir: Path
    database: Path
    model_path: Path
    model_url: str
    inference: InferenceConfig
    clip: ClipConfig
    tracking: TrackingConfig
    cameras: tuple[CameraConfig, ...]
    summary_interval_s: float
    telegram: TelegramConfig | None


def load_config(path: Path) -> AppConfig:
    path = path.resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"No se pudo leer {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("La configuración tiene que ser un mapa YAML.")

    base = path.parent
    inference = _section(raw, "inference")
    clip = _section(raw, "clip")
    tracking = _section(raw, "tracking")
    imgsz = _int(inference, "imgsz", 320)
    if imgsz < 160 or imgsz % 32 != 0:
        raise ValueError("inference.imgsz tiene que ser múltiplo de 32 y valer al menos 160.")

    duration_s = _float(clip, "duration_s", 10)
    preroll_s = _float(clip, "preroll_s", 3)
    if duration_s <= 0:
        raise ValueError("clip.duration_s tiene que ser mayor que 0.")
    if preroll_s < 0 or preroll_s >= duration_s:
        raise ValueError("clip.preroll_s tiene que ser >= 0 y menor que clip.duration_s.")

    confidence = _float(inference, "confidence", 0.4)
    iou = _float(inference, "iou", 0.45)
    track_iou = _float(tracking, "iou_threshold", 0.3)
    for name, value in (
        ("inference.confidence", confidence),
        ("inference.iou", iou),
        ("tracking.iou_threshold", track_iou),
    ):
        if not 0 < value < 1:
            raise ValueError(f"{name} tiene que estar entre 0 y 1.")

    fps = _float(inference, "fps_per_camera", 2)
    buffer_fps = _float(clip, "buffer_fps", 5)
    if fps <= 0 or buffer_fps <= 0:
        raise ValueError("Los FPS tienen que ser mayores que 0.")

    min_hits = _int(tracking, "min_hits", 1)
    max_lost_s = _float(tracking, "max_lost_s", 2)
    if min_hits < 1:
        raise ValueError("tracking.min_hits tiene que ser al menos 1.")
    if max_lost_s <= 0:
        raise ValueError("tracking.max_lost_s tiene que ser mayor que 0.")

    model_url = raw.get("model_url")
    if not isinstance(model_url, str) or not model_url.startswith("https://"):
        raise ValueError("model_url tiene que ser una URL https.")

    return AppConfig(
        output_dir=_resolve(base, _text(raw, "output_dir", "data")),
        database=_resolve(base, _text(raw, "database", "data/inventory.db")),
        model_path=_resolve(base, _text(raw, "model_path", "models/yolov8n.onnx")),
        model_url=model_url,
        inference=InferenceConfig(
            imgsz=imgsz,
            confidence=confidence,
            iou=iou,
            fps_per_camera=fps,
        ),
        clip=ClipConfig(
            duration_s=duration_s,
            preroll_s=preroll_s,
            buffer_fps=buffer_fps,
        ),
        tracking=TrackingConfig(
            iou_threshold=track_iou,
            max_lost_s=max_lost_s,
            min_hits=min_hits,
        ),
        cameras=_cameras(raw.get("cameras")),
        summary_interval_s=_summary_interval(raw),
        telegram=_telegram(raw.get("telegram")),
    )


def _cameras(value: object) -> tuple[CameraConfig, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("Agrega al menos una cámara en 'cameras'.")
    if len(value) > MAX_CAMERAS:
        raise ValueError(f"Este equipo admite como máximo {MAX_CAMERAS} cámaras.")
    cameras: list[CameraConfig] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Cada cámara tiene que tener id y url.")
        camera_id = str(item.get("id", "")).strip()
        url = str(item.get("url", "")).strip()
        if not _CAMERA_ID.match(camera_id):
            raise ValueError(
                f"Id de cámara inválido: {camera_id!r}. Usa letras, números, _ o -."
            )
        if camera_id in seen:
            raise ValueError(f"Id de cámara repetido: {camera_id}.")
        if not url.startswith("rtsp://"):
            raise ValueError(f"La URL de {camera_id} tiene que empezar por rtsp://.")
        seen.add(camera_id)
        cameras.append(CameraConfig(id=camera_id, url=url))
    return tuple(cameras)


def _telegram(value: object) -> TelegramConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("telegram tiene que ser un mapa.")
    token = str(value.get("bot_token") or "").strip()
    chat_id = str(value.get("chat_id") or "").strip()
    if not token and not chat_id:
        return None
    if not token or not chat_id:
        raise ValueError("telegram necesita bot_token y chat_id.")
    return TelegramConfig(bot_token=token, chat_id=chat_id)


def _summary_interval(raw: dict) -> float:
    value = _float(raw, "summary_interval_s", 60)
    if value <= 0:
        raise ValueError("summary_interval_s tiene que ser mayor que 0.")
    return value


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name) or {}
    if not isinstance(value, dict):
        raise ValueError(f"'{name}' tiene que ser un mapa.")
    return value


def _text(raw: dict, key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' tiene que ser un texto.")
    return value.strip()


def _float(raw: dict, key: str, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{key}' tiene que ser un número.")
    return float(value)


def _int(raw: dict, key: str, default: int) -> int:
    value = _float(raw, key, default)
    if not value.is_integer():
        raise ValueError(f"'{key}' tiene que ser un entero.")
    return int(value)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()
