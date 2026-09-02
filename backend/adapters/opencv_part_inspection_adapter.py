from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable

import cv2
import numpy as np

from .part_inspection_adapter import PartInspectionAdapter


CANDIDATE_FEATURES = (
    "normalized_area",
    "aspect_ratio",
    "circularity",
    "solidity",
    "rectangularity",
    "hole_area_ratio",
    "mean_hsv_s",
    "mean_hsv_v",
)


class OpenCVPartInspectionAdapter(PartInspectionAdapter):
    """Single-object OpenCV baseline fed by the fixed camera's latest frame.

    No class thresholds are guessed. Reference features are collected from real
    frames and the most separating features are selected from those samples.
    Until every configured class has repeated references, classification
    returns ``unknown``.
    """

    def __init__(
        self,
        *,
        frame_source: Callable[..., Any],
        parts: dict[str, dict[str, Any]],
        state_path: str | Path,
        capture_root: str | Path,
        roi: dict[str, int] | None = None,
    ) -> None:
        self.frame_source = frame_source
        self.parts = parts
        self.state_path = Path(state_path)
        self.capture_root = Path(capture_root)
        self.roi = dict(roi) if isinstance(roi, dict) else None
        self.background_path = self.capture_root / "background.png"
        self._lock = threading.RLock()
        self._last_result: dict[str, Any] | None = None
        self._last_annotated_frame: Any | None = None
        self._state = self._load_state()
        self._background = self._load_background()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _empty_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "samples": {key: [] for key in self.parts},
            "trials": [],
        }

    def _load_state(self) -> dict[str, Any]:
        state = self._empty_state()
        if not self.state_path.is_file():
            return state
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            samples = loaded.get("samples", {})
            trials = loaded.get("trials", [])
            if isinstance(samples, dict):
                for class_key in self.parts:
                    values = samples.get(class_key, [])
                    state["samples"][class_key] = values if isinstance(values, list) else []
            if isinstance(trials, list):
                state["trials"] = trials[-1000:]
        except Exception:
            # A corrupt profile must never produce a confident classification.
            return state
        return state

    def _persist_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _load_background(self):
        if not self.background_path.is_file():
            return None
        frame = cv2.imread(str(self.background_path), cv2.IMREAD_COLOR)
        return frame if frame is not None and frame.size > 0 else None

    def _get_frame(self):
        try:
            frame = self.frame_source(copy=True)
        except TypeError:
            frame = self.frame_source()
        if (
            frame is None
            or not hasattr(frame, "shape")
            or len(frame.shape) < 2
            or frame.shape[0] <= 0
            or frame.shape[1] <= 0
        ):
            return None
        return frame

    def _crop_roi(self, frame):
        height, width = frame.shape[:2]
        if not self.roi:
            return frame, (0, 0, width, height)
        x = max(0, int(self.roi.get("x", 0)))
        y = max(0, int(self.roi.get("y", 0)))
        roi_width = max(1, int(self.roi.get("width", width - x)))
        roi_height = max(1, int(self.roi.get("height", height - y)))
        x2 = min(width, x + roi_width)
        y2 = min(height, y + roi_height)
        if x >= x2 or y >= y2:
            return frame, (0, 0, width, height)
        return frame[y:y2, x:x2], (x, y, x2 - x, y2 - y)

    def capture_background(self) -> dict[str, Any]:
        frame = self._get_frame()
        if frame is None:
            return self._camera_unavailable()
        self.capture_root.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(self.background_path), frame):
            return {
                "success": False,
                "status": "error",
                "error": "BACKGROUND_SAVE_FAILED",
                "message": "빈 배경 이미지를 저장하지 못했습니다.",
            }
        with self._lock:
            self._background = frame.copy()
            self._last_annotated_frame = frame.copy()
            self._last_result = {
                "success": True,
                "status": "background_registered",
                "captured_at": self._now(),
                "message": "빈 배경 reference를 등록했습니다.",
            }
        return dict(self._last_result)

    @staticmethod
    def _camera_unavailable() -> dict[str, Any]:
        return {
            "success": False,
            "status": "error",
            "class_key": None,
            "error": "CAMERA_FRAME_UNAVAILABLE",
            "message": "Work Order / Inspection Camera frame을 읽을 수 없습니다.",
        }

    def _unknown(
        self,
        reason: str,
        message: str,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "mock": False,
            "status": "unknown",
            "object_ready": False,
            "class_key": None,
            "display_name": None,
            "count": 0,
            "confidence": None,
            "classification_score": None,
            "score_type": "opencv_feature_distance",
            "confidence_is_probability": False,
            "unknown_reason": reason,
            "message": message,
            **extra,
        }

    def _extract_object(self, frame) -> dict[str, Any]:
        if self._background is None:
            return self._unknown(
                "BACKGROUND_NOT_REGISTERED",
                "부품이 없는 빈 배경을 먼저 등록하세요.",
            )
        if self._background.shape != frame.shape:
            return self._unknown(
                "BACKGROUND_SIZE_MISMATCH",
                "카메라 해상도가 변경되었습니다. 빈 배경을 다시 등록하세요.",
            )

        current_roi, roi_box = self._crop_roi(frame)
        background_roi, _ = self._crop_roi(self._background)
        difference = cv2.absdiff(current_roi, background_roi)
        gray_difference = cv2.cvtColor(difference, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray_difference, (5, 5), 0)
        otsu_value, mask = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        kernel = np.ones((3, 3), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        contours, hierarchy = cv2.findContours(
            mask,
            cv2.RETR_CCOMP,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours or hierarchy is None:
            return self._unknown(
                "OBJECT_NOT_DETECTED",
                "빈 배경과 구분되는 object를 찾지 못했습니다.",
                threshold=float(otsu_value),
            )

        hierarchy_rows = hierarchy[0]
        external_indexes = [
            index
            for index, values in enumerate(hierarchy_rows)
            if int(values[3]) == -1
        ]
        if not external_indexes:
            return self._unknown("OBJECT_NOT_DETECTED", "유효한 외곽 contour가 없습니다.")

        roi_height, roi_width = current_roi.shape[:2]
        roi_area = float(roi_height * roi_width)

        # Background subtraction에서는 조명/노출 변화가 화면 가장자리의
        # 거대한 contour로 잡히는 경우가 있으므로, 단품 후보를 먼저 필터링한다.
        border_margin = max(3, int(round(min(roi_width, roi_height) * 0.005)))
        min_area = max(80.0, roi_area * 0.00015)
        max_area = roi_area * 0.20

        valid_candidates: list[dict[str, Any]] = []
        rejected_candidates: list[dict[str, Any]] = []

        for index in external_indexes:
            candidate = contours[index]
            candidate_area = float(cv2.contourArea(candidate))
            x0, y0, w0, h0 = cv2.boundingRect(candidate)

            area_ratio = candidate_area / roi_area if roi_area > 0 else 0.0
            touches_border = bool(
                x0 <= border_margin
                or y0 <= border_margin
                or x0 + w0 >= roi_width - border_margin
                or y0 + h0 >= roi_height - border_margin
            )

            reject_reason = None
            if candidate_area < min_area:
                reject_reason = "AREA_TOO_SMALL"
            elif candidate_area > max_area:
                reject_reason = "AREA_TOO_LARGE"
            elif touches_border:
                reject_reason = "BORDER_TOUCHING"

            item = {
                "index": index,
                "area": candidate_area,
                "area_ratio": area_ratio,
                "bbox": {
                    "x": int(roi_box[0] + x0),
                    "y": int(roi_box[1] + y0),
                    "width": int(w0),
                    "height": int(h0),
                },
                "reason": reject_reason,
            }

            if reject_reason is None:
                valid_candidates.append(item)
            elif candidate_area >= min_area:
                rejected_candidates.append(item)

        valid_candidates.sort(key=lambda item: item["area"], reverse=True)
        rejected_candidates.sort(key=lambda item: item["area"], reverse=True)

        if not valid_candidates:
            return self._unknown(
                "NO_VALID_OBJECT_CANDIDATE",
                "배경 변화/경계 영역은 제외했지만 유효한 단품 object를 찾지 못했습니다.",
                threshold=float(otsu_value),
                rejected_candidates=rejected_candidates[:5],
                detector_limits={
                    "min_area": min_area,
                    "max_area": max_area,
                    "max_area_ratio": 0.20,
                    "border_margin_px": border_margin,
                },
            )

        contour_index = int(valid_candidates[0]["index"])
        contour = contours[contour_index]
        area = float(valid_candidates[0]["area"])

        if area <= 0:
            return self._unknown("INVALID_CONTOUR", "검출 contour 면적이 유효하지 않습니다.")

        significant_candidates = [
            item
            for item in valid_candidates
            if float(item["area"]) >= max(min_area, area * 0.15)
        ]

        object_count = len(significant_candidates)
        if object_count != 1:
            return self._unknown(
                "MULTIPLE_OBJECTS_IN_SINGLE_MODE",
                "단품 검증 영역에 여러 유효 object가 검출되었습니다.",
                object_count=object_count,
                threshold=float(otsu_value),
                candidate_bboxes=[item["bbox"] for item in significant_candidates[:5]],
                rejected_candidates=rejected_candidates[:5],
            )

        perimeter = float(cv2.arcLength(contour, True))
        x, y, width, height = cv2.boundingRect(contour)
        rotated = cv2.minAreaRect(contour)
        rect_width, rect_height = (float(rotated[1][0]), float(rotated[1][1]))
        long_side = max(rect_width, rect_height)
        short_side = min(rect_width, rect_height)
        hull = cv2.convexHull(contour)
        hull_area = float(cv2.contourArea(hull))
        rotated_area = rect_width * rect_height
        object_mask = np.zeros(mask.shape, dtype=np.uint8)
        cv2.drawContours(object_mask, [contour], -1, 255, thickness=cv2.FILLED)
        hsv = cv2.cvtColor(current_roi, cv2.COLOR_BGR2HSV)
        mean_hsv = cv2.mean(hsv, mask=object_mask)
        mean_gray = cv2.mean(cv2.cvtColor(current_roi, cv2.COLOR_BGR2GRAY), mask=object_mask)[0]

        hole_area = 0.0
        hole_count = 0
        child = int(hierarchy_rows[contour_index][2])
        while child != -1:
            hole_area += abs(float(cv2.contourArea(contours[child])))
            hole_count += 1
            child = int(hierarchy_rows[child][0])

        features = {
            "contour_area": area,
            "normalized_area": area / roi_area if roi_area > 0 else 0.0,
            "bbox_width": int(width),
            "bbox_height": int(height),
            "aspect_ratio": long_side / short_side if short_side > 0 else None,
            "perimeter": perimeter,
            "circularity": (4.0 * math.pi * area / (perimeter * perimeter)) if perimeter > 0 else None,
            "solidity": area / hull_area if hull_area > 0 else None,
            "rectangularity": area / rotated_area if rotated_area > 0 else None,
            "hole_count": hole_count,
            "hole_area_ratio": hole_area / area if area > 0 else 0.0,
            "mean_brightness": float(mean_gray),
            "mean_hsv_h": float(mean_hsv[0]),
            "mean_hsv_s": float(mean_hsv[1]) / 255.0,
            "mean_hsv_v": float(mean_hsv[2]) / 255.0,
        }
        absolute_bbox = {
            "x": int(roi_box[0] + x),
            "y": int(roi_box[1] + y),
            "width": int(width),
            "height": int(height),
        }
        return {
            "success": True,
            "status": "object_detected",
            "object_ready": True,
            "features": features,
            "bbox": absolute_bbox,
            "object_count": 1,
            "threshold": float(otsu_value),
            "mask": mask,
            "contour": contour,
            "roi_box": roi_box,
            "rejected_candidates": rejected_candidates[:5],
            "detector_limits": {
                "min_area": min_area,
                "max_area": max_area,
                "max_area_ratio": 0.20,
                "border_margin_px": border_margin,
            },
        }

    def _sample_counts(self) -> dict[str, int]:
        return {
            key: len(self._state["samples"].get(key, []))
            for key in self.parts
        }

    def _build_profile(self) -> dict[str, Any]:
        counts = self._sample_counts()
        missing = [key for key, count in counts.items() if count == 0]
        insufficient = [key for key, count in counts.items() if count < 2]
        if missing or insufficient:
            return {
                "ready": False,
                "missing_classes": missing,
                "insufficient_classes": insufficient,
                "selected_features": [],
                "reason": "REFERENCE_SAMPLES_INCOMPLETE",
            }

        samples_by_class: dict[str, list[dict[str, float]]] = {
            key: [sample["features"] for sample in self._state["samples"][key]]
            for key in self.parts
        }
        separation: dict[str, float] = {}
        for feature in CANDIDATE_FEATURES:
            groups = [
                np.asarray([float(sample[feature]) for sample in values], dtype=np.float64)
                for values in samples_by_class.values()
                if all(sample.get(feature) is not None for sample in values)
            ]
            if len(groups) != len(self.parts):
                continue
            class_means = np.asarray([float(group.mean()) for group in groups])
            within = float(np.mean([float(group.var()) for group in groups]))
            between = float(class_means.var())
            if between > 0:
                separation[feature] = between / max(within, np.finfo(float).eps)
        selected = [
            key
            for key, _score in sorted(separation.items(), key=lambda item: item[1], reverse=True)[:4]
        ]
        if not selected:
            return {
                "ready": False,
                "missing_classes": [],
                "insufficient_classes": [],
                "selected_features": [],
                "reason": "NO_SEPARATING_FEATURE",
            }

        all_vectors = np.asarray(
            [[float(sample[feature]) for feature in selected] for values in samples_by_class.values() for sample in values],
            dtype=np.float64,
        )
        scales = np.std(all_vectors, axis=0)
        scales[scales <= np.finfo(float).eps] = 1.0
        centers = {
            key: np.median(
                np.asarray([[float(sample[feature]) for feature in selected] for sample in values]),
                axis=0,
            )
            for key, values in samples_by_class.items()
        }
        distance_limits: dict[str, float] = {}
        reference_margins: list[float] = []
        separable = True
        for class_key, values in samples_by_class.items():
            own_distances: list[float] = []
            for sample in values:
                vector = np.asarray([float(sample[feature]) for feature in selected])
                distances = {
                    key: float(np.linalg.norm((vector - center) / scales))
                    for key, center in centers.items()
                }
                ordered = sorted(distances.items(), key=lambda item: item[1])
                own_distance = distances[class_key]
                own_distances.append(own_distance)
                other_distance = min(distance for key, distance in ordered if key != class_key)
                margin = other_distance - own_distance
                if margin <= 0:
                    separable = False
                else:
                    reference_margins.append(margin)
            distance_limits[class_key] = max(own_distances)

        return {
            "ready": bool(separable and reference_margins),
            "separable": separable,
            "reason": None if separable else "REFERENCE_CLASSES_OVERLAP",
            "missing_classes": [],
            "insufficient_classes": [],
            "selected_features": selected,
            "separation_scores": separation,
            "scales": scales,
            "centers": centers,
            "distance_limits": distance_limits,
            "required_margin": min(reference_margins) if reference_margins else None,
        }

    def _classify_features(self, features: dict[str, Any]) -> dict[str, Any]:
        profile = self._build_profile()
        if not profile.get("ready"):
            return self._unknown(
                str(profile.get("reason") or "REFERENCE_SAMPLES_INCOMPLETE"),
                "6종 class별 실제 reference를 2개 이상 등록해야 분류를 시작합니다.",
                classifier_profile={
                    key: value
                    for key, value in profile.items()
                    if key not in {"centers", "scales"}
                },
            )
        selected = profile["selected_features"]
        vector = np.asarray([float(features[feature]) for feature in selected])
        distances = {
            key: float(np.linalg.norm((vector - center) / profile["scales"]))
            for key, center in profile["centers"].items()
        }
        ordered = sorted(distances.items(), key=lambda item: item[1])
        best_key, best_distance = ordered[0]
        second_key, second_distance = ordered[1]
        margin = second_distance - best_distance
        candidates = [
            {
                "class_key": key,
                "display_name": self.parts[key]["display_name"],
                "distance": distance,
                "score": 1.0 / (1.0 + distance),
            }
            for key, distance in ordered[:3]
        ]
        if best_distance > profile["distance_limits"][best_key]:
            return self._unknown(
                "OUTSIDE_REFERENCE_RANGE",
                "현재 feature가 등록 reference 범위를 벗어났습니다.",
                candidates=candidates,
                selected_features=selected,
                score_margin=margin,
            )
        if margin < profile["required_margin"]:
            return self._unknown(
                "AMBIGUOUS_CLASS_MARGIN",
                "상위 class 간 score 차이가 충분하지 않습니다.",
                candidates=candidates,
                selected_features=selected,
                score_margin=margin,
            )
        return {
            "success": True,
            "mock": False,
            "status": "classified",
            "class_key": best_key,
            "display_name": self.parts[best_key]["display_name"],
            "count": 1,
            "confidence": None,
            "classification_score": 1.0 / (1.0 + best_distance),
            "score_type": "opencv_feature_distance",
            "confidence_is_probability": False,
            "selected_features": selected,
            "score_margin": margin,
            "candidates": candidates,
            "message": "실제 카메라 OpenCV 단품 분류 결과입니다.",
        }

    def _annotate(self, frame, detected: dict[str, Any], prediction: dict[str, Any]):
        annotated = frame.copy()

        # Reject된 큰 배경/경계 contour도 빨간색으로 보여줘서
        # 사용자가 왜 후보에서 제외됐는지 바로 확인할 수 있게 한다.
        for rejected in detected.get("rejected_candidates", []) or []:
            bbox = rejected.get("bbox") or {}
            x = int(bbox.get("x", 0))
            y = int(bbox.get("y", 0))
            width = int(bbox.get("width", 0))
            height = int(bbox.get("height", 0))
            if width <= 0 or height <= 0:
                continue

            color = (0, 0, 220)
            cv2.rectangle(
                annotated,
                (x, y),
                (x + width, y + height),
                color,
                2,
            )
            cv2.putText(
                annotated,
                str(rejected.get("reason") or "REJECTED"),
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                2,
                cv2.LINE_AA,
            )

        bbox = detected.get("bbox")
        if bbox:
            x, y = int(bbox["x"]), int(bbox["y"])
            width, height = int(bbox["width"]), int(bbox["height"])
            classified = prediction.get("status") == "classified"
            color = (40, 190, 40) if classified else (0, 180, 255)
            cv2.rectangle(annotated, (x, y), (x + width, y + height), color, 2)
            label = prediction.get("class_key") or "unknown"
            score = prediction.get("classification_score")
            if score is not None:
                label = f"{label} score={float(score):.3f}"
            cv2.putText(
                annotated,
                label,
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        return annotated

    def classify_current(self) -> dict[str, Any]:
        frame = self._get_frame()
        if frame is None:
            result = self._camera_unavailable()
            with self._lock:
                self._last_result = result
            return result
        detected = self._extract_object(frame)
        if detected.get("status") != "object_detected":
            prediction = detected
        else:
            prediction = self._classify_features(detected["features"])
            prediction.update(
                {
                    "features": detected["features"],
                    "bbox": detected["bbox"],
                    "object_count": detected["object_count"],
                    "threshold": detected["threshold"],
                    "object_ready": True,
                    "rejected_candidates": detected.get("rejected_candidates", []),
                    "detector_limits": detected.get("detector_limits"),
                }
            )
        annotated = self._annotate(frame, detected, prediction)
        prediction["captured_at"] = self._now()
        with self._lock:
            self._last_result = prediction
            self._last_annotated_frame = annotated
        return dict(prediction)

    def capture_reference(
        self,
        class_key: str,
        condition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if class_key not in self.parts:
            return {
                "success": False,
                "status": "error",
                "error": "UNKNOWN_CLASS_KEY",
                "message": f"등록되지 않은 class_key입니다: {class_key}",
            }
        frame = self._get_frame()
        if frame is None:
            return self._camera_unavailable()
        detected = self._extract_object(frame)
        if detected.get("status") != "object_detected":
            return detected

        sample_id = f"{time.time_ns()}"
        class_dir = self.capture_root / class_key
        class_dir.mkdir(parents=True, exist_ok=True)
        image_path = class_dir / f"{sample_id}.jpg"
        if not cv2.imwrite(str(image_path), frame):
            return {
                "success": False,
                "status": "error",
                "error": "REFERENCE_IMAGE_SAVE_FAILED",
                "message": "Reference frame을 저장하지 못했습니다.",
            }
        sample = {
            "sample_id": sample_id,
            "captured_at": self._now(),
            "class_key": class_key,
            "features": detected["features"],
            "bbox": detected["bbox"],
            "condition": dict(condition or {}),
            "image_path": str(image_path),
        }
        with self._lock:
            self._state["samples"][class_key].append(sample)
            self._persist_state()
            profile = self._build_profile()
            prediction = {
                "success": True,
                "mock": False,
                "status": "reference_registered",
                "class_key": class_key,
                "display_name": self.parts[class_key]["display_name"],
                "sample_count": len(self._state["samples"][class_key]),
                "features": detected["features"],
                "bbox": detected["bbox"],
                "classifier_ready": bool(profile.get("ready")),
                "message": "실제 카메라 frame에서 reference sample을 등록했습니다.",
            }
            self._last_result = prediction
            self._last_annotated_frame = self._annotate(frame, detected, prediction)
        return dict(prediction)

    def clear_class(self, class_key: str) -> dict[str, Any]:
        if class_key not in self.parts:
            return {
                "success": False,
                "status": "error",
                "error": "UNKNOWN_CLASS_KEY",
                "message": f"등록되지 않은 class_key입니다: {class_key}",
            }
        with self._lock:
            samples = list(self._state["samples"].get(class_key, []))
            for sample in samples:
                path = Path(str(sample.get("image_path") or ""))
                if path.is_file() and self.capture_root in path.parents:
                    path.unlink()
            self._state["samples"][class_key] = []
            self._state["trials"] = [
                trial
                for trial in self._state["trials"]
                if trial.get("ground_truth_class_key") != class_key
            ]
            self._persist_state()
        return {
            "success": True,
            "mock": False,
            "status": "class_reference_cleared",
            "class_key": class_key,
            "removed_samples": len(samples),
            "message": f"{class_key} reference와 관련 시험 기록을 초기화했습니다.",
        }

    def run_classification_test(
        self,
        ground_truth_class_key: str,
        condition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if ground_truth_class_key not in self.parts:
            return {
                "success": False,
                "status": "error",
                "error": "UNKNOWN_CLASS_KEY",
                "message": f"등록되지 않은 class_key입니다: {ground_truth_class_key}",
            }
        result = self.classify_current()
        if not result.get("success", False):
            return {
                **result,
                "ground_truth_class_key": ground_truth_class_key,
                "predicted_class_key": None,
                "outcome": None,
                "trial_recorded": False,
                "validation": self.get_validation_summary(),
            }
        if result.get("mock"):
            return {
                "success": False,
                "status": "error",
                "error": "MOCK_RESULT_NOT_ALLOWED",
                "message": "Mock 결과는 실물 분류 통계에 포함할 수 없습니다.",
            }
        predicted = result.get("class_key")
        if result.get("status") == "unknown" or predicted is None:
            outcome = "unknown"
        elif predicted == ground_truth_class_key:
            outcome = "correct"
        else:
            outcome = "misclassified"
        trial = {
            "tested_at": self._now(),
            "ground_truth_class_key": ground_truth_class_key,
            "predicted_class_key": predicted,
            "outcome": outcome,
            "classification_score": result.get("classification_score"),
            "score_margin": result.get("score_margin"),
            "condition": dict(condition or {}),
            "unknown_reason": result.get("unknown_reason"),
        }
        with self._lock:
            self._state["trials"].append(trial)
            self._state["trials"] = self._state["trials"][-1000:]
            self._persist_state()
        return {
            **result,
            "ground_truth_class_key": ground_truth_class_key,
            "predicted_class_key": predicted,
            "outcome": outcome,
            "trial_recorded": True,
            "validation": self.get_validation_summary(),
        }

    def get_validation_summary(self) -> dict[str, Any]:
        summary = {
            key: {"total": 0, "correct": 0, "misclassified": 0, "unknown": 0}
            for key in self.parts
        }
        matrix = {
            key: {**{predicted: 0 for predicted in self.parts}, "unknown": 0}
            for key in self.parts
        }
        for trial in self._state.get("trials", []):
            ground_truth = trial.get("ground_truth_class_key")
            if ground_truth not in summary:
                continue
            outcome = trial.get("outcome")
            summary[ground_truth]["total"] += 1
            if outcome in {"correct", "misclassified", "unknown"}:
                summary[ground_truth][outcome] += 1
            predicted = trial.get("predicted_class_key") or "unknown"
            if predicted in matrix[ground_truth]:
                matrix[ground_truth][predicted] += 1
        return {
            "mock_results_included": False,
            "by_class": summary,
            "confusion_matrix": matrix,
            "recent_trials": list(reversed(self._state.get("trials", [])[-20:])),
        }

    def get_status(self) -> dict[str, Any]:
        profile = self._build_profile()
        counts = self._sample_counts()
        return {
            "connected": self._get_frame() is not None,
            "mock": False,
            "mode": "opencv_baseline",
            "detector_backend": "opencv_baseline",
            "single_object_only": True,
            "counting_validated": False,
            "background_registered": self._background is not None,
            "sample_counts": counts,
            "unregistered_classes": [key for key, count in counts.items() if count == 0],
            "classifier_ready": bool(profile.get("ready")),
            "classifier_reason": profile.get("reason"),
            "selected_features": list(profile.get("selected_features", [])),
            "last_result": self._last_result,
            "validation": self.get_validation_summary(),
        }

    def inspect(
        self,
        *,
        class_key: str,
        part_config: dict[str, Any],
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        result = self.classify_current()
        detected_key = result.get("class_key")
        count = int(result.get("count") or 0)
        return {
            **result,
            "single_object_only": True,
            "counting_validated": False,
            "present": count > 0,
            "visual_ok": result.get("status") == "classified" and detected_key == class_key,
            "detected_part_no": part_config.get("part_no") if detected_key == class_key else None,
            "detected_class": detected_key,
            "detected_count": count,
            "parts": (
                [
                    {
                        "class_key": detected_key,
                        "display_name": result.get("display_name"),
                        "count": count,
                        "confidence": result.get("confidence"),
                        "classification_score": result.get("classification_score"),
                        "status": result.get("status"),
                    }
                ]
                if detected_key
                else []
            ),
            "foreign_object_detected": bool(detected_key and detected_key != class_key),
        }

    def get_debug_jpeg(self, jpeg_quality: int = 90) -> bytes | None:
        with self._lock:
            frame = None if self._last_annotated_frame is None else self._last_annotated_frame.copy()
        if frame is None:
            return None
        quality = max(50, min(int(jpeg_quality), 100))
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return encoded.tobytes() if ok else None
