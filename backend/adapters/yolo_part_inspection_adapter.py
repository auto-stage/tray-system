from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import threading
import time
from typing import Any, Callable

import cv2

from .part_inspection_adapter import PartInspectionAdapter


class YoloModelError(ValueError):
    pass


class YoloPartInspectionAdapter(PartInspectionAdapter):
    """Ultralytics detector consuming only the shared C920 latest frame."""

    def __init__(
        self,
        *,
        frame_source: Callable[..., Any],
        parts: dict[str, dict[str, Any]],
        confidence_threshold: float = 0.25,
        max_inference_fps: float = 2.0,
        model_loader: Callable[[str], Any] | None = None,
        validation_state_path: str | Path | None = None,
    ) -> None:
        self.frame_source = frame_source
        self.parts = parts
        self.id_to_key = dict(sorted((int(value["yolo_class_id"]), key) for key, value in parts.items()))
        self.confidence_threshold = max(0.01, min(float(confidence_threshold), 0.99))
        self.max_inference_fps = max(0.1, min(float(max_inference_fps), 30.0))
        self._model_loader = model_loader
        self.validation_state_path = Path(validation_state_path).resolve() if validation_state_path else None
        self._model = None
        self._model_path: str | None = None
        self._model_error: str | None = None
        self._last_result: dict[str, Any] = self._not_ready()
        self._last_frame = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._trials = self._load_trials()

    def _load_trials(self) -> list[dict[str, Any]]:
        if self.validation_state_path is None or not self.validation_state_path.is_file():
            return []
        try:
            value = json.loads(self.validation_state_path.read_text(encoding="utf-8"))
            return list(value.get("trials", []))[-1000:] if isinstance(value, dict) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_trials(self) -> None:
        if self.validation_state_path is None:
            return
        self.validation_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.validation_state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"trials": self._trials[-1000:]}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.validation_state_path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _default_loader(path: str):
        from ultralytics import YOLO
        return YOLO(path)

    def _load(self, path: str):
        return (self._model_loader or self._default_loader)(path)

    def _model_names(self, model) -> dict[int, str]:
        names = getattr(model, "names", None)
        if names is None and getattr(model, "model", None) is not None:
            names = getattr(model.model, "names", None)
        if isinstance(names, list):
            names = dict(enumerate(names))
        if not isinstance(names, dict):
            raise YoloModelError("모델 class names를 읽을 수 없습니다.")
        try:
            return {int(key): str(value) for key, value in names.items()}
        except (TypeError, ValueError) as error:
            raise YoloModelError("모델 class names 형식이 잘못되었습니다.") from error

    def validate_model(self, path: str | Path) -> dict[str, Any]:
        model_path = Path(path).resolve()
        if not model_path.is_file() or model_path.suffix.lower() != ".pt":
            raise YoloModelError("실제 .pt 모델 파일이 필요합니다.")
        try:
            model = self._load(str(model_path))
            actual = self._model_names(model)
        except YoloModelError:
            raise
        except Exception as error:
            raise YoloModelError(f"Ultralytics detection model load 실패: {error}") from error
        if actual != self.id_to_key:
            raise YoloModelError(f"MODEL_CLASS_MAPPING_MISMATCH: expected={self.id_to_key}, actual={actual}")
        return {"path": str(model_path), "class_mapping": actual}

    def load_model(self, path: str | Path) -> dict[str, Any]:
        validated = self.validate_model(path)
        model = self._load(validated["path"])
        with self._lock:
            self._model = model
            self._model_path = validated["path"]
            self._model_error = None
        self.start()
        return {"status": "ready", "model_path": self._model_path, "class_mapping": self.id_to_key}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="yolo-inference-worker", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            with self._lock:
                ready = self._model is not None
            if ready:
                try:
                    frame = self.frame_source(copy=True)
                    if frame is not None:
                        self.infer_frame(frame)
                except Exception as error:
                    with self._lock:
                        self._model_error = str(error)
            interval = 1.0 / self.max_inference_fps
            self._stop.wait(max(0.01, interval - (time.monotonic() - started)))

    def _not_ready(self) -> dict[str, Any]:
        return {
            "success": False, "mock": False, "mode": "yolo", "status": "model_not_ready",
            "error": "YOLO_MODEL_NOT_READY", "message": "활성 YOLO 모델이 없습니다.",
            "detections": [], "counts": {}, "class_key": None, "confidence": None,
        }

    @staticmethod
    def _tolist(value) -> list:
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        if hasattr(value, "tolist"):
            return value.tolist()
        return list(value)

    def infer_frame(self, frame) -> dict[str, Any]:
        with self._lock:
            model = self._model
            threshold = self.confidence_threshold
        if model is None:
            return self._not_ready()
        started = time.monotonic()
        try:
            results = model.predict(source=frame, conf=threshold, device="cpu", verbose=False)
            result = results[0] if results else None
            boxes = getattr(result, "boxes", None) if result is not None else None
            xyxy = self._tolist(boxes.xyxy) if boxes is not None else []
            confidences = self._tolist(boxes.conf) if boxes is not None else []
            classes = self._tolist(boxes.cls) if boxes is not None else []
            detections = []
            counts = {key: 0 for key in self.parts}
            for coordinates, confidence, class_id_raw in zip(xyxy, confidences, classes):
                class_id = int(class_id_raw)
                class_key = self.id_to_key.get(class_id)
                if class_key is None or float(confidence) < threshold:
                    continue
                x1, y1, x2, y2 = map(float, coordinates)
                detection = {
                    "class_id": class_id, "class_key": class_key,
                    "display_name": self.parts[class_key]["display_name"],
                    "confidence": float(confidence),
                    "bbox": {"x": x1, "y": y1, "width": max(0.0, x2 - x1), "height": max(0.0, y2 - y1)},
                }
                detections.append(detection)
                counts[class_key] += 1
            annotated = frame.copy()
            for detection in detections:
                box = detection["bbox"]
                x1, y1 = int(round(box["x"])), int(round(box["y"]))
                x2, y2 = int(round(box["x"] + box["width"])), int(round(box["y"] + box["height"]))
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (30, 220, 80), 2)
                cv2.putText(annotated, f'{detection["class_key"]} {detection["confidence"]:.2f}', (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 220, 80), 2)
            top = max(detections, key=lambda item: item["confidence"]) if detections else None
            payload = {
                "success": True, "mock": False, "mode": "yolo",
                "status": "detected" if detections else "no_detection",
                "class_key": top["class_key"] if top else None,
                "confidence": top["confidence"] if top else None,
                "detections": detections, "counts": counts,
                "detected_count": len(detections), "count": len(detections),
                "confidence_threshold": threshold,
                "inference_ms": round((time.monotonic() - started) * 1000, 1),
                "captured_at": self._now(), "model_path": self._model_path,
            }
            with self._lock:
                self._last_result = payload
                self._last_frame = annotated
                self._model_error = None
            return payload
        except Exception as error:
            payload = {
                "success": False, "mock": False, "mode": "yolo", "status": "error",
                "error": "YOLO_INFERENCE_FAILED", "message": str(error), "detections": [], "counts": {},
            }
            with self._lock:
                self._last_result = payload
                self._model_error = str(error)
            return payload

    def auto_label(self, frame) -> dict[str, Any]:
        return self.infer_frame(frame)

    def set_confidence_threshold(self, value: float) -> float:
        with self._lock:
            self.confidence_threshold = max(0.01, min(float(value), 0.99))
            return self.confidence_threshold

    def latest_result(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_result)

    def record_classification_test(self, ground_truth_class_key: str) -> dict[str, Any]:
        if ground_truth_class_key not in self.parts:
            raise ValueError("지원하지 않는 Ground Truth class입니다.")
        result = self.latest_result()
        if not result.get("success"):
            return result
        detections = list(result.get("detections", []))
        predicted = result.get("class_key")
        outcome = "no_detection" if not detections else "correct" if predicted == ground_truth_class_key else "misclassified"
        trial = {
            "tested_at": self._now(), "ground_truth_class_key": ground_truth_class_key,
            "predicted_class_key": predicted, "outcome": outcome,
            "confidence": result.get("confidence"), "detection_count": len(detections),
        }
        with self._lock:
            self._trials.append(trial)
            self._trials = self._trials[-1000:]
            self._save_trials()
        return {"success": True, "mock": False, "trial": trial, "validation": self.validation_summary()}

    def validation_summary(self) -> dict[str, Any]:
        with self._lock:
            trials = list(self._trials)
        summary = {key: {"tests": 0, "correct": 0, "misclassified": 0, "no_detection": 0} for key in self.parts}
        for trial in trials:
            key = trial.get("ground_truth_class_key")
            outcome = trial.get("outcome")
            if key in summary:
                summary[key]["tests"] += 1
                if outcome in summary[key]: summary[key][outcome] += 1
        return {"mock_results_included": False, "by_class": summary, "recent_trials": list(reversed(trials[-20:]))}

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            ready = self._model is not None
            running = self._thread is not None and self._thread.is_alive()
            return {
                "connected": ready, "mock": False, "mode": "yolo", "detector_backend": "yolo",
                "model_ready": ready, "active_model": self._model_path,
                "error": self._model_error if ready else "YOLO_MODEL_NOT_READY",
                "confidence_threshold": self.confidence_threshold,
                "max_inference_fps": self.max_inference_fps,
                "inference_worker_running": running,
                "counting_validated": ready, "single_object_only": False,
                "last_result": self._last_result,
                "validation": self.validation_summary(),
            }

    def inspect(self, *, class_key: str, part_config: dict[str, Any], expected_count: int | None = None) -> dict[str, Any]:
        result = self.latest_result()
        if not result.get("success"):
            return {**result, "detected_count": 0, "counting_validated": False, "parts": []}
        counts = result.get("counts", {})
        detected_count = int(counts.get(class_key, 0))
        parts = [
            {"class_key": key, "display_name": self.parts[key]["display_name"], "count": count}
            for key, count in counts.items() if count
        ]
        return {
            **result, "detected_class": result.get("class_key"), "detected_count": detected_count,
            "counting_validated": True, "single_object_only": False, "parts": parts,
            "present": detected_count > 0, "visual_ok": detected_count == int(expected_count or detected_count),
            "foreign_object_detected": any(key != class_key and count for key, count in counts.items()),
        }

    def get_debug_jpeg(self, jpeg_quality: int = 90) -> bytes | None:
        with self._lock:
            frame = None if self._last_frame is None else self._last_frame.copy()
        if frame is None:
            return None
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, max(50, min(int(jpeg_quality), 100))])
        return encoded.tobytes() if ok else None
