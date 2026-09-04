from __future__ import annotations

from typing import Any

from .loadcell_adapter import LoadCellAdapter


class STM32LoadCellAdapter(LoadCellAdapter):
    """
    HX711이 Stage STM32에 함께 연결되어 있는 실제 Load Cell Adapter.

    별도 Serial 포트를 열지 않고 STM32StageAdapter의 기존 Serial과
    I/O lock을 그대로 공유한다.
    """

    def __init__(self, stage) -> None:
        self.stage = stage
        self._tare_offset_g = 0.0
        self._last_weight_g = 0.0

        # 데모 운용에서는 Backend 시작 시 캐리지가 비어 있다는
        # 조건으로 HX711 #1의 현재 빈 캐리지 하중을 자동 영점으로 잡는다.
        tare_result = self.tare()

        if not tare_result.get("success"):
            raise RuntimeError(
                "HX711 #1 startup tare 실패: "
                + str(tare_result.get("message", "원인 불명"))
            )

        print(
            "[LOAD CELL] startup tare 완료 "
            f"samples={tare_result.get('samples')} "
            f"tare_offset_g={self._tare_offset_g:.1f}"
        )

    def get_status(self) -> dict[str, Any]:
        result = self.stage.read_loadcell()

        if not result.get("success"):
            return {
                "connected": False,
                "mock": False,
                "mode": "stm32",
                "stable": False,
                "message": result.get(
                    "message",
                    "Load Cell 읽기 실패",
                ),
            }

        self._last_weight_g = float(
            result["weight_g"]
        )

        return {
            "connected": True,
            "mock": False,
            "mode": "stm32",
            "stable": True,
            "raw": result["raw"],
            "weight_g": self._last_weight_g,
            "tare_offset_g": self._tare_offset_g,
            "net_weight_g": (
                self._last_weight_g
                - self._tare_offset_g
            ),
            "calibrated": True,
        }

    def tare(self) -> dict[str, Any]:
        samples = 5
        weights: list[float] = []

        for _ in range(samples):
            result = self.stage.read_loadcell()

            if not result.get("success"):
                return result

            weights.append(
                float(result["weight_g"])
            )

        self._tare_offset_g = (
            sum(weights) / len(weights)
        )

        self._last_weight_g = weights[-1]

        return {
            "success": True,
            "mock": False,
            "samples": samples,
            "tare_offset_g": self._tare_offset_g,
            "message": "Backend tare 완료",
        }

    def read_weight(self) -> dict[str, Any]:
        result = self.stage.read_loadcell()

        if not result.get("success"):
            return result

        weight_g = float(
            result["weight_g"]
        )

        self._last_weight_g = weight_g

        return {
            "success": True,
            "mock": False,
            "stable": True,
            "raw": result["raw"],
            "weight_g": weight_g,
            "tare_offset_g": self._tare_offset_g,
            "net_weight_g": (
                weight_g
                - self._tare_offset_g
            ),
        }

    def estimate_count(
        self,
        *,
        part_config: dict[str, Any],
        expected_quantity: int | None = None,
    ) -> dict[str, Any]:

        measurement = self.read_weight()

        if not measurement.get("success"):
            return measurement

        unit_weight_g = float(
            part_config.get(
                "unit_weight_g",
                0.0,
            )
            or 0.0
        )

        empty_tray_weight_g = float(
            part_config.get(
                "empty_tray_weight_g",
                0.0,
            )
            or 0.0
        )

        tolerance_g = float(
            part_config.get(
                "tolerance_g",
                0.0,
            )
            or 0.0
        )

        if unit_weight_g <= 0.0:
            return {
                "success": False,
                "message": (
                    "unit_weight_g 실측 보정값이 없습니다."
                ),
                **measurement,
            }

        total_weight_g = float(
            measurement["net_weight_g"]
        )

        part_weight_g = (
            total_weight_g
            - empty_tray_weight_g
        )

        estimated_quantity = max(
            round(
                part_weight_g
                / unit_weight_g
            ),
            0,
        )

        expected_weight_g = (
            estimated_quantity
            * unit_weight_g
        )

        residual_g = abs(
            part_weight_g
            - expected_weight_g
        )

        return {
            **measurement,
            "success": True,
            "calibrated": True,
            "empty_tray_weight_g": empty_tray_weight_g,
            "unit_weight_g": unit_weight_g,
            "estimated_quantity": estimated_quantity,
            "residual_g": residual_g,
            "count_confident": (
                tolerance_g <= 0.0
                or residual_g <= tolerance_g
            ),
        }
