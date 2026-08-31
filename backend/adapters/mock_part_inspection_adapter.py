from __future__ import annotations

from typing import Any

from .part_inspection_adapter import PartInspectionAdapter


class MockPartInspectionAdapter(PartInspectionAdapter):
    """Visual inspection simulator used before the fixed camera is available."""

    def get_status(self) -> dict[str, Any]:
        return {
            "connected": True,
            "mock": True,
            "mode": "mock",
            "calibrated": False,
            "message": (
                "Mock Part Vision입니다. 실제 카메라 수령 후 Tray ROI와 "
                "부품 검수 알고리즘을 연결해야 합니다."
            ),
        }

    def inspect(
        self,
        *,
        part_no: str,
        part_config: dict[str, Any],
    ) -> dict[str, Any]:
        vision_cfg = part_config.get("vision", {})
        return {
            "success": True,
            "mock": True,
            "calibrated": False,
            "present": True,
            "visual_ok": True,
            "detected_part_no": part_no,
            "detected_class": (
                vision_cfg.get("class_name")
                or part_no
            ),
            "confidence": 0.99,
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
