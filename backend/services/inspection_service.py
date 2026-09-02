from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class InspectionService:
    """Combine load-cell quantity and camera-based part inspection."""

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
        with self.parts_config_path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            raw = yaml.safe_load(handle) or {}

        items = raw.get("parts", {})
        if not isinstance(items, dict):
            raise ValueError("parts.yaml의 parts 항목이 올바르지 않습니다.")

        return {
            str(part_no).strip().upper(): dict(config or {})
            for part_no, config in items.items()
        }

    def reload_config(self) -> None:
        self._parts = self._load_parts()

    def get_parts_config(self) -> dict[str, Any]:
        return {
            "parts": self._parts,
            "config_path": str(self.parts_config_path),
        }

    def get_status(self) -> dict[str, Any]:
        return {
            "loadcell": self.loadcell.get_status(),
            "part_vision": self.part_vision.get_status(),
            "configured_parts": len(self._parts),
        }

    def _get_part_config(self, part_no: str) -> dict[str, Any] | None:
        return self._parts.get(str(part_no).strip().upper())

    def run(
        self,
        *,
        part_no: str,
        expected_quantity: int,
    ) -> dict[str, Any]:
        normalized_part_no = str(part_no).strip().upper()

        if expected_quantity <= 0:
            return {
                "success": False,
                "passed": False,
                "matched": False,
                "error": "INVALID_EXPECTED_QUANTITY",
                "message": "요구 수량은 1개 이상이어야 합니다.",
            }

        part_config = self._get_part_config(normalized_part_no)
        if part_config is None:
            return {
                "success": False,
                "passed": False,
                "matched": False,
                "error": "PART_NOT_CONFIGURED",
                "message": f"parts.yaml에 {normalized_part_no} 설정이 없습니다.",
            }

        loadcell_result = self.loadcell.estimate_count(
            part_config=part_config,
            expected_quantity=expected_quantity,
        )
        vision_result = self.part_vision.inspect(
            part_no=normalized_part_no,
            part_config=part_config,
        )

        estimated_quantity = loadcell_result.get("estimated_quantity")
        count_ok = bool(
            loadcell_result.get("success")
            and loadcell_result.get("stable")
            and loadcell_result.get("count_confident")
            and estimated_quantity == expected_quantity
        )

        detected_part_no = str(
            vision_result.get("detected_part_no") or ""
        ).strip().upper()
        part_ok = bool(
            vision_result.get("success")
            and vision_result.get("present")
            and vision_result.get("visual_ok")
            and detected_part_no == normalized_part_no
            and not vision_result.get("foreign_object_detected", False)
        )

        passed = bool(count_ok and part_ok)

        reasons: list[str] = []
        if not loadcell_result.get("stable", False):
            reasons.append("WEIGHT_UNSTABLE")
        if loadcell_result.get("count_confident") is False:
            reasons.append("WEIGHT_UNCERTAIN")
        if estimated_quantity != expected_quantity:
            reasons.append("COUNT_MISMATCH")
        if not vision_result.get("present", False):
            reasons.append("PART_NOT_VISIBLE")
        if detected_part_no and detected_part_no != normalized_part_no:
            reasons.append("WRONG_PART")
        if vision_result.get("foreign_object_detected", False):
            reasons.append("FOREIGN_OBJECT")
        if not vision_result.get("visual_ok", False):
            reasons.append("VISUAL_NG")

        both_mock = bool(
            loadcell_result.get("mock")
            and vision_result.get("mock")
        )

        return {
            "success": True,
            "passed": passed,
            # Legacy fields kept so the existing workflow/UI can migrate
            # without breaking in the same commit.
            "matched": passed,
            "detected_quantity": estimated_quantity,
            "expected_quantity": expected_quantity,
            "part_no": normalized_part_no,
            "mock": both_mock,
            "decision": "PASS" if passed else "NG",
            "reasons": reasons,
            "loadcell": loadcell_result,
            "vision": vision_result,
            "part_config": {
                "tray_id": part_config.get("tray_id"),
                "display_name": part_config.get("display_name"),
                "spec": part_config.get("spec"),
                "loadcell_calibrated": bool(
                    part_config.get("loadcell", {}).get("calibrated", False)
                ),
                "vision_calibrated": bool(
                    part_config.get("vision", {}).get("calibrated", False)
                ),
            },
            "message": (
                "Mock 센서 통합 검수 PASS"
                if passed and both_mock
                else "부품 종류/외형 및 중량 기반 수량 검수 PASS"
                if passed
                else "검수 NG: " + ", ".join(reasons or ["UNKNOWN"])
            ),
        }
