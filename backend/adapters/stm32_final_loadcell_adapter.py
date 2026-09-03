from __future__ import annotations

from typing import Any


class STM32FinalLoadCellAdapter:
    """
    최종 검수 박스용 HX711 #2 Adapter.

    별도 Serial 포트를 열지 않고 STM32StageAdapter의
    기존 Serial 연결과 I/O lock을 공유한다.
    """

    def __init__(self, stage) -> None:
        self.stage = stage

    def get_status(self) -> dict[str, Any]:
        result = self.stage.get_final_loadcell_status()

        if not result.get("success"):
            return {
                "connected": False,
                "mock": False,
                "mode": "stm32",
                "tared": False,
                "calibrated": False,
                "message": result.get(
                    "message",
                    "Final Load Cell 상태 확인 실패",
                ),
            }

        return {
            **result,
            "mock": False,
            "mode": "stm32",
        }

    def read_raw(self) -> dict[str, Any]:
        result = self.stage.read_final_loadcell_raw()

        if not result.get("success"):
            return result

        return {
            **result,
            "mock": False,
            "mode": "stm32",
        }

    def tare(self) -> dict[str, Any]:
        result = self.stage.tare_final_loadcell()

        if not result.get("success"):
            return result

        return {
            **result,
            "mock": False,
            "mode": "stm32",
        }

    def calibrate(
        self,
        known_weight_g: float,
    ) -> dict[str, Any]:
        result = self.stage.calibrate_final_loadcell(
            known_weight_g
        )

        if not result.get("success"):
            return result

        return {
            **result,
            "mock": False,
            "mode": "stm32",
        }

    def read_weight(self) -> dict[str, Any]:
        result = self.stage.read_final_loadcell_weight()

        if not result.get("success"):
            return result

        return {
            **result,
            "mock": False,
            "mode": "stm32",
            "stable": True,
        }
