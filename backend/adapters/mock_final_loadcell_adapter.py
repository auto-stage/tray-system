from __future__ import annotations

from typing import Any


class MockFinalLoadCellAdapter:
    """
    최종 검수 박스용 Mock Load Cell.

    실제 HX711 #2가 없어도 Final Verification
    Backend/UI 흐름을 테스트하기 위한 Adapter.
    """

    def __init__(self) -> None:
        self._weight_g = 0.0
        self._tared = True
        self._calibrated = True
        self._tare_raw = 0.0
        self._count_per_g = 1.0

    def get_status(self) -> dict[str, Any]:
        return {
            "success": True,
            "connected": True,
            "mock": True,
            "mode": "mock",
            "tared": self._tared,
            "calibrated": self._calibrated,
            "tare_raw": self._tare_raw,
            "count_per_g": self._count_per_g,
        }

    def read_raw(self) -> dict[str, Any]:
        return {
            "success": True,
            "connected": True,
            "mock": True,
            "mode": "mock",
            "raw": int(self._weight_g),
        }

    def tare(self) -> dict[str, Any]:
        self._weight_g = 0.0
        self._tared = True

        return {
            "success": True,
            "connected": True,
            "mock": True,
            "mode": "mock",
            "tare_raw": 0.0,
            "samples": 10,
            "message": "Mock Final Load Cell tare 완료",
        }

    def calibrate(
        self,
        known_weight_g: float,
    ) -> dict[str, Any]:

        if known_weight_g <= 0:
            return {
                "success": False,
                "message": "기준 무게는 0g보다 커야 합니다.",
            }

        self._calibrated = True
        self._count_per_g = 1.0

        return {
            "success": True,
            "connected": True,
            "mock": True,
            "mode": "mock",
            "known_weight_g": float(known_weight_g),
            "count_per_g": self._count_per_g,
        }

    def read_weight(self) -> dict[str, Any]:
        return {
            "success": True,
            "connected": True,
            "mock": True,
            "mode": "mock",
            "stable": True,
            "weight_g": self._weight_g,
            "raw_average": self._weight_g,
            "samples": 10,
        }

    def set_mock_weight(
        self,
        weight_g: float,
    ) -> None:
        self._weight_g = float(weight_g)
