from __future__ import annotations

from typing import Any

from .part_inspection_adapter import PartInspectionAdapter


class MockPartInspectionAdapter(PartInspectionAdapter):
    """Visual inspection simulator used before the fixed camera is available."""

    def get_status(self) -> dict[str, Any]:
        return {
            "connected": False,
            "mock": True,
            "simulated": True,
            "mode": "mock",
            "calibrated": False,
            "detector_backend": "mock",
            "single_object_only": False,
            "counting_validated": False,
            "classifier_ready": False,
            "sample_counts": {},
            "unregistered_classes": [],
            "validation": {
                "mock_results_included": False,
                "by_class": {},
                "confusion_matrix": {},
                "recent_trials": [],
            },
            "message": (
                "Mock Part Vision입니다. 실제 카메라 수령 후 Tray ROI와 "
                "부품 검수 알고리즘을 연결해야 합니다."
            ),
        }

    def inspect(
        self,
        *,
        class_key: str,
        part_config: dict[str, Any],
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        count = max(int(expected_count or 0), 0)
        display_name = part_config.get("display_name") or class_key
        return {
            "success": True,
            "mock": True,
            "calibrated": False,
            "present": True,
            "visual_ok": True,
            "status": "classified",
            "class_key": class_key,
            "display_name": display_name,
            "detected_part_no": part_config.get("part_no"),
            "detected_class": class_key,
            "detected_count": count,
            "count": count,
            "confidence": 0.99,
            "classification_score": 0.99,
            "score_type": "mock",
            "confidence_is_probability": False,
            "parts": [
                {
                    "class_key": class_key,
                    "display_name": display_name,
                    "count": count,
                    "confidence": 0.99,
                    "classification_score": 0.99,
                    "status": "classified",
                }
            ],
            "size_check": {
                "available": False,
                "ok": None,
                "reason": "CAMERA_NOT_CALIBRATED",
            },
            "foreign_object_detected": False,
            "message": (
                "Mock 영상 검수 결과입니다. 실제 운용에서는 부품 종류/형상/"
                "크기 검수 결과로 교체됩니다."
            ),
        }

    def debug_action(self, *_args, **_kwargs) -> dict[str, Any]:
        return {
            "success": False,
            "mock": True,
            "status": "error",
            "error": "REAL_CAMERA_REQUIRED",
            "message": "Mock 결과는 실제 reference 또는 분류 통계에 사용할 수 없습니다.",
        }
