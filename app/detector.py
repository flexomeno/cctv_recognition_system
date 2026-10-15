from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

log = logging.getLogger("cctv")

# Índices COCO de YOLOv8. El inventario guarda el nombre en español.
TARGET_CLASSES = {
    0: "persona",
    1: "bicicleta",
    2: "auto",
    3: "moto",
    5: "bus",
    7: "camión",
}
_MIN_MODEL_BYTES = 1_000_000


@dataclass(frozen=True)
class Detection:
    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]


def ensure_model(path: Path, url: str) -> None:
    if path.exists() and path.stat().st_size >= _MIN_MODEL_BYTES:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    log.info("Descargando YOLOv8n en %s", path)
    request = urllib.request.Request(url, headers={"User-Agent": "cctv-recognition/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"No se pudo descargar el modelo desde {url}: {exc}") from exc
    if tmp.stat().st_size < _MIN_MODEL_BYTES:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"La descarga del modelo quedó incompleta ({url}).")
    tmp.replace(path)


def letterbox(
    image: np.ndarray, new_shape: tuple[int, int]
) -> tuple[np.ndarray, float, tuple[float, float]]:
    height, width = image.shape[:2]
    new_h, new_w = new_shape
    ratio = min(new_w / width, new_h / height)
    resized_w = int(round(width * ratio))
    resized_h = int(round(height * ratio))
    resized = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    pad_w = new_w - resized_w
    pad_h = new_h - resized_h
    left = pad_w // 2
    right = pad_w - left
    top = pad_h // 2
    bottom = pad_h - top
    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
    )
    return padded, ratio, (float(left), float(top))


def detections_from_output(
    output: np.ndarray,
    *,
    ratio: float,
    pad: tuple[float, float],
    orig_shape: tuple[int, int],
    input_size: tuple[int, int],
    confidence: float,
    iou_threshold: float,
) -> list[Detection]:
    pred = np.asarray(output, dtype=np.float32)
    if pred.ndim == 3:
        pred = pred[0]
    if pred.ndim != 2:
        raise RuntimeError(f"Salida ONNX inesperada: {tuple(output.shape)}")
    if pred.shape[0] in (6, 84, 85) and pred.shape[1] not in (6, 84, 85):
        pred = pred.T
    if pred.shape[1] == 6:
        return _from_nms_rows(
            pred, ratio, pad, orig_shape, input_size, confidence, iou_threshold
        )
    if pred.shape[1] < 4 + max(TARGET_CLASSES) + 1:
        raise RuntimeError(f"Salida ONNX inesperada: {tuple(output.shape)}")
    return _from_raw_rows(
        pred, ratio, pad, orig_shape, input_size, confidence, iou_threshold
    )


class Detector:
    def __init__(self, path: Path, imgsz: int, confidence: float, iou: float):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(path), options, providers=["CPUExecutionProvider"]
        )
        model_input = self.session.get_inputs()[0]
        self.input_name = model_input.name
        self.input_hw = _input_hw(model_input.shape, imgsz)
        self.confidence = confidence
        self.iou = iou
        self.requested_imgsz = imgsz
        if self.input_hw != (imgsz, imgsz):
            log.warning(
                "El ONNX tiene entrada fija de %sx%s. Se usará ese tamaño, no imgsz=%s.",
                self.input_hw[1],
                self.input_hw[0],
                imgsz,
            )
        else:
            log.info("Modelo listo. Entrada %sx%s.", self.input_hw[1], self.input_hw[0])

    def detect(self, frame: np.ndarray) -> list[Detection]:
        canvas, ratio, pad = letterbox(frame, self.input_hw)
        blob = canvas[:, :, ::-1].astype(np.float32)
        blob /= 255.0
        blob = np.transpose(blob, (2, 0, 1))[None]
        output = self.session.run(None, {self.input_name: blob})[0]
        return detections_from_output(
            output,
            ratio=ratio,
            pad=pad,
            orig_shape=frame.shape[:2],
            input_size=self.input_hw,
            confidence=self.confidence,
            iou_threshold=self.iou,
        )


def _input_hw(shape: list, requested: int) -> tuple[int, int]:
    height = _fixed_dim(shape, 2)
    width = _fixed_dim(shape, 3)
    if height and width:
        return height, width
    return requested, requested


def _fixed_dim(shape: list, index: int) -> int | None:
    if len(shape) <= index:
        return None
    value = shape[index]
    if isinstance(value, (int, np.integer)) and int(value) > 0:
        return int(value)
    return None


def _from_raw_rows(
    pred: np.ndarray,
    ratio: float,
    pad: tuple[float, float],
    orig_shape: tuple[int, int],
    input_size: tuple[int, int],
    confidence: float,
    iou_threshold: float,
) -> list[Detection]:
    boxes = pred[:, :4]
    if boxes.size and float(np.max(boxes)) <= 2.0:
        input_h, input_w = input_size
        boxes = boxes.copy()
        boxes[:, [0, 2]] *= input_w
        boxes[:, [1, 3]] *= input_h
    class_scores = pred[:, 4:]
    wanted = [index for index in TARGET_CLASSES if index < class_scores.shape[1]]
    if not wanted:
        return []
    subset = class_scores[:, wanted]
    local = np.argmax(subset, axis=1)
    scores = subset[np.arange(subset.shape[0]), local]
    class_ids = np.asarray(wanted, dtype=np.int32)[local]
    keep = scores >= confidence
    return _nms_detections(
        boxes[keep],
        scores[keep],
        class_ids[keep],
        ratio,
        pad,
        orig_shape,
        confidence,
        iou_threshold,
        xywh=True,
    )


def _from_nms_rows(
    pred: np.ndarray,
    ratio: float,
    pad: tuple[float, float],
    orig_shape: tuple[int, int],
    input_size: tuple[int, int],
    confidence: float,
    iou_threshold: float,
) -> list[Detection]:
    boxes = pred[:, :4]
    if boxes.size and float(np.max(np.abs(boxes))) <= 2.0:
        input_h, input_w = input_size
        boxes = boxes.copy()
        boxes[:, [0, 2]] *= input_w
        boxes[:, [1, 3]] *= input_h
    scores = pred[:, 4]
    class_ids = pred[:, 5].astype(np.int32)
    keep = scores >= confidence
    return _nms_detections(
        boxes[keep],
        scores[keep],
        class_ids[keep],
        ratio,
        pad,
        orig_shape,
        confidence,
        iou_threshold,
        xywh=False,
    )


def _nms_detections(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    ratio: float,
    pad: tuple[float, float],
    orig_shape: tuple[int, int],
    confidence: float,
    iou_threshold: float,
    *,
    xywh: bool,
) -> list[Detection]:
    candidates: list[tuple[int, float, tuple[float, float, float, float]]] = []
    for box, score, class_id in zip(boxes, scores, class_ids):
        class_id = int(class_id)
        if class_id not in TARGET_CLASSES:
            continue
        xyxy = _xywh_to_xyxy(box) if xywh else (
            float(box[0]),
            float(box[1]),
            float(box[2]),
            float(box[3]),
        )
        xyxy = _to_original(xyxy, ratio, pad, orig_shape)
        if xyxy[2] - xyxy[0] < 2 or xyxy[3] - xyxy[1] < 2:
            continue
        candidates.append((class_id, float(score), xyxy))

    grouped: dict[int, list[int]] = {}
    for index, (class_id, _score, _xyxy) in enumerate(candidates):
        grouped.setdefault(class_id, []).append(index)

    detections: list[Detection] = []
    for class_id, indexes in grouped.items():
        nms_boxes: list[list[float]] = []
        nms_scores: list[float] = []
        for index in indexes:
            x1, y1, x2, y2 = candidates[index][2]
            nms_boxes.append([x1, y1, x2 - x1, y2 - y1])
            nms_scores.append(candidates[index][1])
        for local_index in _nms_indices(nms_boxes, nms_scores, confidence, iou_threshold):
            _class_id, score, xyxy = candidates[indexes[local_index]]
            detections.append(
                Detection(
                    class_name=TARGET_CLASSES[class_id],
                    confidence=score,
                    bbox=xyxy,
                )
            )
    detections.sort(key=lambda item: item.confidence, reverse=True)
    return detections


def _xywh_to_xyxy(box: np.ndarray) -> tuple[float, float, float, float]:
    cx, cy, width, height = (float(value) for value in box)
    return cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2


def _to_original(
    box: tuple[float, float, float, float],
    ratio: float,
    pad: tuple[float, float],
    orig_shape: tuple[int, int],
) -> tuple[float, float, float, float]:
    pad_x, pad_y = pad
    height, width = orig_shape
    x1 = (box[0] - pad_x) / ratio
    y1 = (box[1] - pad_y) / ratio
    x2 = (box[2] - pad_x) / ratio
    y2 = (box[3] - pad_y) / ratio
    x1 = min(max(x1, 0.0), float(width))
    y1 = min(max(y1, 0.0), float(height))
    x2 = min(max(x2, 0.0), float(width))
    y2 = min(max(y2, 0.0), float(height))
    return x1, y1, x2, y2


def _nms_indices(
    boxes: list[list[float]], scores: list[float], confidence: float, iou_threshold: float
) -> list[int]:
    if not boxes:
        return []
    chosen = cv2.dnn.NMSBoxes(boxes, scores, confidence, iou_threshold)
    if chosen is None or len(chosen) == 0:
        return []
    return [int(index) for index in np.asarray(chosen).reshape(-1)]
