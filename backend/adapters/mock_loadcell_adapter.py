from __future__ import annotations

from typing import Any

from .loadcell_adapter import LoadCellAdapter


class MockLoadCellAdapter(LoadCellAdapter):
    """Deterministic load-cell simulator for hardware-free integration tests."""

    def __init__(self) -> None:
        self._tare_offset_g = 0.0
        self._last_weight_g = 0.0
        self._stable = True

    def set_mock_tray_present(
        self,
        present: bool,
        *,
        tray_weight_g: float = 500.0,
    ) -> None:
        """
        Mock Gripper가 Tray 파지/해제를 흉내낼 때 사용한다.

        CLOSE -> net weight 약 500g
        OPEN  -> net weight 0g
        """
        if present:
            self._last_weight_g = (
                self._tare_offset_g
                + float(tray_weight_g)
            )
        else:
            self._last_weight_g = (
                self._tare_offset_g
            )

        self._stable = True

    def get_status(self) -> dict[str, Any]:
        return {
            "connected": True,
            "mock": True,
            "mode": "mock",
            "stable": self._stable,
            "weight_g": round(self._last_weight_g, 3),
            "tare_offset_g": round(self._tare_offset_g, 3),
            "calibrated": False,
            "message": (
                "Mock Load Cell입니다. 실제 센서 수령 후 ADC/증폭기와 "
                "실측 캘리브레이션 값을 연결해야 합니다."
            ),
        }

    def tare(self) -> dict[str, Any]:
        self._tare_offset_g = self._last_weight_g
        return {
            "success": True,
            "mock": True,
            "tare_offset_g": round(self._tare_offset_g, 3),
        }

    def read_weight(self) -> dict[str, Any]:
        return {
            "success": True,
            "mock": True,
            "stable": self._stable,
            "weight_g": round(self._last_weight_g, 3),
            "net_weight_g": round(
                self._last_weight_g - self._tare_offset_g,
                3,
            ),
        }

    def estimate_count(
        self,
        *,
        part_config: dict[str, Any],
        expected_quantity: int | None = None,
    ) -> dict[str, Any]:
        # Mock mode intentionally does NOT treat placeholder values in
        # parts.yaml as real calibration data. It only provides a predictable
        # software-integration result until the hardware is available.
        quantity = max(int(expected_quantity or 0), 0)
        mock_unit_weight_g = 10.0
        mock_empty_tray_weight_g = 500.0
        net_weight_g = quantity * mock_unit_weight_g
        total_weight_g = mock_empty_tray_weight_g + net_weight_g
        self._last_weight_g = total_weight_g

        return {
            "success": True,
            "mock": True,
            "stable": True,
            "calibrated": False,
            "weight_g": total_weight_g,
            "empty_tray_weight_g": mock_empty_tray_weight_g,
            "net_weight_g": net_weight_g,
            "unit_weight_g": mock_unit_weight_g,
            "estimated_quantity": quantity,
            "residual_g": 0.0,
            "count_confident": True,
            "calibration_bypassed_for_mock": True,
            "message": (
                "Mock 수량 결과입니다. 실제 운용에서는 parts.yaml의 "
                "unit_weight_g/empty_tray_weight_g/tolerance_g 실측값이 필요합니다."
            ),
        }
