from __future__ import annotations

from pathlib import Path
from typing import Any

from parts_db import (
    find_part_by_identifier,
    load_parts_catalog,
    serialize_part,
)


class InspectionService:
    """Normalize part-detector results and compare expected/detected counts."""

    def __init__(
        self,
        *,
        loadcell,
        part_vision,
        parts_config_path: Path,
    ) -> None:
        self.loadcell = loadcell
        self.part_vision = part_vision
        self.parts_config_path = Path(parts_config_path)
        self._parts = self._load_parts()

    def _load_parts(self) -> dict[str, dict[str, Any]]:
        return load_parts_catalog(self.parts_config_path)

    def reload_config(self) -> None:
        self._parts = self._load_parts()
        if hasattr(self.part_vision, "parts"):
            self.part_vision.parts = self._parts

    def get_parts_config(self) -> dict[str, Any]:
        status = self.part_vision.get_status()
        sample_counts = status.get("sample_counts", {})
        parts: dict[str, dict[str, Any]] = {}
        for class_key, config in self._parts.items():
            item = serialize_part(config)
            sample_count = int(sample_counts.get(class_key, 0))
            item["reference_sample_count"] = sample_count
            item["reference_registered"] = sample_count > 0
            parts[class_key] = item
        return {
            "parts": parts,
            "config_path": str(self.parts_config_path),
        }

    def get_status(self) -> dict[str, Any]:
        return {
            "loadcell": self.loadcell.get_status(),
            "part_vision": self.part_vision.get_status(),
            "configured_parts": len(self._parts),
        }

    def _get_part_config(self, identifier: str) -> dict[str, Any] | None:
        return find_part_by_identifier(identifier, catalog=self._parts)

    @staticmethod
    def _quantity_status(expected: int, detected: int) -> tuple[str, int, str]:
        difference = detected - expected
        if difference == 0:
            return "match", difference, "정상"
        if difference < 0:
            return "shortage", difference, f"{abs(difference)}개 부족"
        return "excess", difference, f"{difference}개 초과"

    def run(
        self,
        *,
        part_no: str | None = None,
        class_key: str | None = None,
        expected_quantity: int,
        vision_enabled: bool = True,
    ) -> dict[str, Any]:
        identifier = str(class_key or part_no or "").strip()
        if expected_quantity <= 0:
            return {
                "success": False,
                "passed": False,
                "matched": False,
                "status": "error",
                "error": "INVALID_EXPECTED_QUANTITY",
                "message": "요구 수량은 1개 이상이어야 합니다.",
            }
        part_config = self._get_part_config(identifier)
        if part_config is None:
            return {
                "success": False,
                "passed": False,
                "matched": False,
                "status": "error",
                "error": "PART_NOT_CONFIGURED",
                "message": f"parts.yaml에 {identifier} 설정이 없습니다.",
            }

        canonical_key = part_config["class_key"]
        canonical_part_no = part_config.get("part_no") or canonical_key
        loadcell_result = self.loadcell.estimate_count(
            part_config=part_config,
            expected_quantity=expected_quantity,
        )

        loadcell_passed = bool(
            loadcell_result.get("success", False)
            and loadcell_result.get("count_confident", False)
            and int(loadcell_result.get("estimated_quantity") or 0)
            == expected_quantity
        )

        if not vision_enabled:
            return {
                "success": bool(loadcell_result.get("success", False)),
                "passed": loadcell_passed,
                "matched": loadcell_passed,
                "mock": bool(loadcell_result.get("mock", False)),
                "status": "match" if loadcell_passed else "mismatch",
                "status_label": (
                    "정상"
                    if loadcell_passed
                    else "Load Cell 수량 불일치"
                ),
                "decision": "PASS" if loadcell_passed else "NG",
                "class_key": canonical_key,
                "display_name": part_config["display_name"],
                "part_no": canonical_part_no,
                "expected_count": expected_quantity,
                "detected_count": (
                    int(loadcell_result.get("estimated_quantity") or 0)
                ),
                "expected_quantity": expected_quantity,
                "detected_quantity": (
                    int(loadcell_result.get("estimated_quantity") or 0)
                ),
                "difference": (
                    int(loadcell_result.get("estimated_quantity") or 0)
                    - expected_quantity
                ),
                "reasons": (
                    []
                    if loadcell_passed
                    else ["LOADCELL_COUNT_MISMATCH"]
                ),
                "vision": {
                    "enabled": False,
                    "status": "SKIPPED",
                    "passed": None,
                    "message": "중간 Vision 검수가 비활성화되어 있습니다.",
                },
                "loadcell": loadcell_result,
                "message": (
                    "Load Cell 검수 PASS"
                    if loadcell_passed
                    else "Load Cell 검수 FAIL"
                ),
            }

        vision_result = self.part_vision.inspect(
            class_key=canonical_key,
            part_config=part_config,
            expected_count=expected_quantity,
        )
        detected_count = int(vision_result.get("detected_count") or 0)
        detected_key = (
            vision_result.get("class_key")
            or vision_result.get("detected_class")
        )
        vision_status = str(
            vision_result.get("status") or "error"
        )
        counting_pending = bool(
            not vision_result.get("mock")
            and expected_quantity > 1
            and not vision_result.get("counting_validated", False)
        )

        if not vision_result.get("success", False):
            status = "error"
            difference = None
            status_label = "분석 오류"
        elif vision_status == "unknown" or detected_key is None:
            status = "unknown"
            difference = None
            status_label = "판정 불가"
        elif counting_pending:
            status = "unknown"
            difference = None
            status_label = "복수 부품 counting 미검증"
        else:
            status, difference, status_label = self._quantity_status(
                expected_quantity,
                detected_count,
            )

        class_matched = bool(detected_key == canonical_key)
        vision_passed = bool(
            status == "match"
            and class_matched
        )
        passed = bool(
            loadcell_passed
            and vision_passed
        )
        reasons: list[str] = []
        if status == "unknown":
            reasons.append(
                "COUNTING_NOT_VALIDATED"
                if counting_pending and detected_key is not None
                else str(vision_result.get("unknown_reason") or "UNKNOWN_CLASS")
            )
        elif status == "error":
            reasons.append(str(vision_result.get("error") or "INSPECTION_ERROR"))
        else:
            if not class_matched:
                reasons.append("WRONG_PART")
            if difference != 0:
                reasons.append("COUNT_MISMATCH")

        catalog_item = serialize_part(part_config)
        mock = bool(vision_result.get("mock"))
        message = (
            "Mock 검수 결과입니다. 실제 분류 성능 통계에는 포함되지 않습니다."
            if mock
            else "단품 class는 판정했지만 복수 부품 counting은 아직 실물 검증 전입니다."
            if counting_pending and detected_key is not None
            else "부품 종류와 수량이 일치합니다."
            if passed
            else vision_result.get("message") or status_label
        )
        return {
            "success": bool(vision_result.get("success", False)),
            "passed": passed,
            "matched": passed,
            "mock": mock,
            "status": status,
            "status_label": status_label,
            "decision": "PASS" if passed else "NG",
            "class_key": canonical_key,
            "display_name": part_config["display_name"],
            "part_no": canonical_part_no,
            "expected_count": expected_quantity,
            "detected_count": detected_count,
            "expected_quantity": expected_quantity,
            "detected_quantity": detected_count,
            "difference": difference,
            "confidence": vision_result.get("confidence"),
            "classification_score": vision_result.get("classification_score"),
            "score_type": vision_result.get("score_type"),
            "reasons": reasons,
            "parts": list(vision_result.get("parts", [])),
            "vision": vision_result,
            "loadcell": loadcell_result,
            "part_config": {
                **catalog_item,
                "loadcell_calibrated": False,
                "vision_calibrated": bool(
                    part_config.get("vision", {}).get("calibrated", False)
                ),
            },
            "spec_display": catalog_item["spec_display"],
            "weight_g": part_config.get("weight_g"),
            "weight_display": catalog_item["weight_display"],
            "message": message,
        }

    def debug_action(
        self,
        *,
        action: str,
        class_key: str | None = None,
        condition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.part_vision.get_status().get("mock"):
            return {
                "success": False,
                "mock": True,
                "status": "error",
                "error": "REAL_CAMERA_REQUIRED",
                "message": "Mock 결과는 실제 reference 또는 검증 통계에 포함할 수 없습니다.",
            }
        normalized_action = str(action).strip().lower()
        if normalized_action == "capture_background":
            return self.part_vision.capture_background()
        if normalized_action == "inspect_current":
            return self.part_vision.classify_current()
        if normalized_action == "capture_reference":
            return self.part_vision.capture_reference(str(class_key or ""), condition)
        if normalized_action == "classify":
            return self.part_vision.run_classification_test(str(class_key or ""), condition)
        if normalized_action == "clear_class":
            return self.part_vision.clear_class(str(class_key or ""))
        return {
            "success": False,
            "status": "error",
            "error": "INVALID_DEBUG_ACTION",
            "message": f"지원하지 않는 debug action입니다: {action}",
        }

    def get_debug_status(self) -> dict[str, Any]:
        return {
            "success": True,
            "parts": self.get_parts_config()["parts"],
            "inspection": self.part_vision.get_status(),
        }

    def get_debug_jpeg(self) -> bytes | None:
        getter = getattr(self.part_vision, "get_debug_jpeg", None)
        return getter() if callable(getter) else None
